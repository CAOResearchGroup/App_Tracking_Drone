from __future__ import annotations

import json
import math
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
    def __init__(self, directory: str | Path = "logs"):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
        self.file = self.path.open("x", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()
        self.closed = False

    def write(self, kind: str, **data):
        record = {"utc": datetime.now(timezone.utc).isoformat(), "monotonic": time.monotonic(), "kind": kind, **data}
        line = json.dumps(json_safe(record), ensure_ascii=False, allow_nan=False)
        with self.lock:
            if not self.closed:
                self.file.write(line + "\n")

    def close(self):
        with self.lock:
            self.closed = True
            self.file.close()


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
