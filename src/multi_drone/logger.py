from __future__ import annotations

import json
import math
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class EventLog:
    """Best-effort, bounded logging; control threads never perform disk I/O."""

    def __init__(self, directory: str | Path = "logs", *, max_bytes=8*1024*1024,
                 max_files=4, queue_size=1024):
        if min(max_bytes, max_files, queue_size) <= 0:
            raise ValueError("Log limits must be positive")
        directory = Path(directory).absolute()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
        self.max_bytes, self.max_files = max_bytes, max_files
        self.file = None
        self.size = 0
        self.last_error = None
        self.dropped = 0
        self.records = queue.Queue(maxsize=queue_size)
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.closed = False
        self.thread = threading.Thread(target=self._run, name="event-log", daemon=True)
        self.thread.start()

    def write(self, kind: str, **data):
        with self.lock:
            if self.closed or self.last_error:
                self.dropped += 1
                return
        try:
            record = {"utc": datetime.now(timezone.utc).isoformat(), "monotonic": time.monotonic(), "kind": kind, **data}
            line = (json.dumps(json_safe(record), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        except Exception as exc:
            with self.lock:
                self.dropped += 1
                self.last_error = str(exc)
            return
        with self.lock:
            if self.closed or self.last_error or len(line) > min(self.max_bytes, 64*1024):
                self.dropped += 1
                return
            try:
                self.records.put_nowait(line)
            except queue.Full:
                self.dropped += 1

    def status(self):
        with self.lock:
            return {"path": str(self.path), "error": self.last_error,
                    "dropped": self.dropped, "queued": self.records.qsize(),
                    "max_bytes_per_file": self.max_bytes, "max_files_per_session": self.max_files}

    def _write_line(self, line):
        if self.size + len(line) > self.max_bytes:
            self.file.close()
            if self.max_files == 1:
                self.path.unlink()
            else:
                for index in range(self.max_files-1, 0, -1):
                    source = self.path if index == 1 else self.path.with_suffix(f".{index-1}.jsonl")
                    if source.exists():
                        source.replace(self.path.with_suffix(f".{index}.jsonl"))
            self.file = self.path.open("xb", buffering=0)
            self.size = 0
        remaining = memoryview(line)
        while remaining:
            written = self.file.write(remaining)
            if not written:
                raise OSError("Log write made no progress")
            remaining = remaining[written:]
        self.size += len(line)

    def _failure(self, exc, dropped=0):
        with self.lock:
            self.last_error = str(exc)
            self.dropped += dropped

    def _run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.file = self.path.open("xb", buffering=0)
            while not self.stopping.is_set() or not self.records.empty():
                try:
                    item = self.records.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    if isinstance(item, threading.Event):
                        item.set()
                    elif self.last_error is None:
                        self._write_line(item)
                    else:
                        with self.lock:
                            self.dropped += 1
                except Exception as exc:
                    self._failure(exc, dropped=1)
                finally:
                    self.records.task_done()
        except Exception as exc:
            self._failure(exc)
        finally:
            if self.file is not None:
                try:
                    self.file.close()
                except Exception as exc:
                    self._failure(exc)

    def flush(self, timeout=2):
        """Wait only at explicit read/shutdown boundaries, never in control loops."""
        deadline = time.monotonic() + timeout
        barrier = threading.Event()
        if not self.thread.is_alive():
            return False
        try:
            self.records.put(barrier, timeout=max(0, timeout))
        except queue.Full:
            return False
        return barrier.wait(max(0, deadline-time.monotonic())) and self.last_error is None

    def close(self):
        with self.lock:
            self.closed = True
            self.stopping.set()
        self.thread.join(timeout=2)


def replay(path: str | Path, speed: float = 0):
    """Read events only. Never construct a transport or send a command."""
    if speed < 0:
        raise ValueError("Replay speed must be nonnegative")
    previous = None
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if speed and previous is not None:
                time.sleep(min(1.0, max(0, record["monotonic"] - previous) / speed))
            previous = record["monotonic"]
            yield record
