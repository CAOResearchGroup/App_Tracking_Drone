"""Optional plot process. Queue saturation must never wait on a GUI reader."""
from __future__ import annotations

import importlib.util
import multiprocessing as mp
import queue
import threading
import time

from .plot_data import snapshot_frame


def _plot_process(frames, stop, ready):
    try:
        from .plot_window import run_window
        run_window(frames, stop, ready)
    except Exception as exc:
        ready.send({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        ready.close()


class PlotService:
    def __init__(self, app):
        self.app = app
        self.process = self.thread = self.frames = self.stop = None

    def status(self):
        return {"running": bool(self.process and self.process.is_alive()),
                "display_hz": 10, "history_seconds": 120,
                "pid": self.process.pid if self.process and self.process.is_alive() else None}

    def open(self):
        if self.status()["running"]:
            return self.status()
        self.close()
        if any(importlib.util.find_spec(name) is None for name in ("PySide6", "pyqtgraph")):
            raise RuntimeError("Install plots first: python -m pip install -e '.[plots]'")
        ctx = mp.get_context("spawn")
        self.frames, self.stop = ctx.Queue(maxsize=2), ctx.Event()
        reader, writer = ctx.Pipe(duplex=False)
        self.process = ctx.Process(target=_plot_process, args=(self.frames, self.stop, writer),
                                   name="telemetry-plots", daemon=True)
        try:
            self.process.start()
            writer.close()
            if not reader.poll(15):
                raise RuntimeError("Plot window did not start within 15 seconds")
            result = reader.recv()
            if "error" in result:
                raise RuntimeError(result["error"])
            self.thread = threading.Thread(target=self._publish, name="plot-snapshots", daemon=True)
            self.thread.start()
        except Exception:
            self.close()
            raise
        finally:
            reader.close()
            writer.close()
        return self.status()

    def _publish(self):
        while not self.stop.is_set() and self.process.is_alive():
            frame = snapshot_frame(self.app, time.monotonic())
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                # Drop display frames, never wait on GUI rendering or Qt's event loop.
                pass
            self.stop.wait(.1)

    def close(self):
        if self.stop:
            self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.process and self.process.pid is not None:
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=2)
        if self.process:
            self.process.close()
        if self.frames:
            # Publisher has stopped. Drain its bounded queue so a normal window
            # close also releases the pipe's feeder thread on repeated open/close.
            for _ in range(3):
                try:
                    self.frames.get(timeout=.1)
                except (queue.Empty, EOFError, OSError):
                    break
            # A force-killed consumer may leave a damaged pipe; never join its feeder.
            self.frames.cancel_join_thread()
            self.frames.close()
        self.process = self.thread = self.frames = self.stop = None
