from __future__ import annotations

import contextlib
import importlib.util
import multiprocessing
import sys
import traceback
from pathlib import Path

from .models import Target, Vec3


def _worker(connection, path):
    # A worker protects the controller from hangs, not from malicious Python.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            spec = importlib.util.spec_from_file_location("fleet_user_algorithm", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not callable(getattr(module, "step", None)):
                raise ValueError("Algorithm must define step(context)")
        connection.send((True, "ready"))
        while True:
            context = connection.recv()
            if context is None:
                break
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    result = module.step(context)
                connection.send((True, result))
            except Exception:
                connection.send((False, traceback.format_exc(limit=4)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try:
            connection.send((False, traceback.format_exc(limit=4)))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


class AlgorithmRunner:
    def __init__(self):
        self.path = None
        self.process = self.connection = None

    def load(self, path):
        resolved = Path(path).resolve(strict=True)
        if resolved.suffix != ".py":
            raise ValueError("Only .py algorithms supported in v0.1; .m conversion is not implemented")
        self.close()
        self.path = str(resolved)
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, self.path), daemon=True)
        self.process.start()
        child.close()
        if not self.connection.poll(5):
            self.close()
            raise TimeoutError("Algorithm import timed out")
        ok, message = self.connection.recv()
        if not ok:
            self.close()
            raise ValueError(message)

    def step(self, context, timeout):
        if self.path is None:
            leader = Vec3.parse(context["leader"]["position"])
            result = {str(f["id"]): {"position": list(leader + Vec3.parse(f["offset"]))} for f in context["followers"]}
        else:
            if self.process is None or not self.process.is_alive():
                raise RuntimeError("Algorithm worker stopped; reload the algorithm")
            self.connection.send(context)
            if not self.connection.poll(timeout):
                self.close()
                raise TimeoutError("Algorithm exceeded its execution deadline")
            ok, result = self.connection.recv()
            if not ok:
                raise ValueError(f"Algorithm failed: {result}")
        if not isinstance(result, dict):
            raise ValueError("Algorithm output must be {follower_id: target}")
        expected = {str(f["id"]) for f in context["followers"]}
        if len(result) != len(expected) or {str(k) for k in result} != expected:
            raise ValueError("Algorithm must return exactly the configured follower IDs")
        targets = {}
        for key, value in result.items():
            if not isinstance(value, dict) or set(value) - {"position", "velocity", "yaw"}:
                raise ValueError("Target fields: position OR velocity, optional yaw")
            position = Vec3.parse(value["position"]) if "position" in value else None
            velocity = Vec3.parse(value["velocity"]) if "velocity" in value else None
            yaw = value.get("yaw")
            if yaw is not None and (isinstance(yaw, bool) or not isinstance(yaw, (int, float))):
                raise ValueError("Yaw must be a number in radians")
            targets[int(key)] = Target(int(key), position, velocity, yaw)
        return targets

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=0.3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.3)
            self.process.close()
        if self.connection is not None:
            self.connection.close()
        self.process = self.connection = None

    def use_builtin(self):
        self.close()
        self.path = None
