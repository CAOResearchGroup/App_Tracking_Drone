from __future__ import annotations

import math
import struct
import threading
import time
from concurrent.futures import Future


PARAM_FORMATS = {1: "B", 2: "b", 3: "H", 4: "h", 5: "I", 6: "i"}


def decode_parameter(wire: float, param_type: int):
    """PX4 uses PARAM_ENCODE_BYTEWISE, including when param_value looks tiny."""
    if param_type == 9:
        if not math.isfinite(wire):
            raise ValueError("Non-finite parameter value")
        return wire
    if param_type not in PARAM_FORMATS:
        raise ValueError(f"Unsupported parameter type {param_type}")
    fmt = "<" + PARAM_FORMATS[param_type]
    return struct.unpack(fmt, struct.pack("<f", wire)[:struct.calcsize(fmt)])[0]


def encode_parameter(value, param_type: int) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("Parameter must be finite")
    if param_type == 9:
        wire = struct.unpack("<f", struct.pack("<f", value))[0]
        if not math.isfinite(wire):
            raise ValueError("Parameter exceeds float32 range")
        return wire
    if param_type not in PARAM_FORMATS or int(value) != value:
        raise ValueError("Parameter type requires an integer")
    packed = struct.pack("<" + PARAM_FORMATS[param_type], int(value))
    return struct.unpack("<f", packed.ljust(4, b"\0"))[0]


class ParameterService:
    def __init__(self, transport, logger):
        self.transport, self.logger = transport, logger
        self.lock = threading.RLock()
        self.cache: dict[tuple[int, int], dict] = {}
        self.pending: dict[tuple[int, int, str], tuple[Future, float, object]] = {}
        self.indices: dict[tuple[int, int], set] = {}

    def request(self, system_id, component_id, operation, name="", value=None, param_type=9, timeout=5) -> Future:
        if operation not in ("get", "set", "list"):
            raise ValueError("Unknown parameter operation")
        if operation != "list" and (not name or len(name.encode("ascii")) > 16):
            raise ValueError("Parameter ID must contain 1..16 ASCII characters")
        wire = encode_parameter(value, param_type) if operation == "set" else 0
        expected = decode_parameter(wire, param_type) if operation == "set" else None
        key = (system_id, component_id, "*" if operation == "list" else name)
        with self.lock:
            if key in self.pending:
                raise RuntimeError("Parameter request already pending")
            future = Future()
            self.pending[key] = (future, time.monotonic()+timeout, expected)
            if operation == "list":
                self.indices[(system_id, component_id)] = set()
            self.logger.write("parameter_request", target=system_id, operation=operation, name=name, value=value)
            try:
                self.transport.parameter(system_id, component_id, operation, name, wire, param_type)
            except Exception as exc:
                self.pending.pop(key)
                future.set_exception(exc)
            return future

    def handle(self, packet):
        if packet.kind != "PARAM_VALUE":
            return
        d = packet.data
        name = d["param_id"]
        if isinstance(name, bytes):
            name = name.decode("ascii")
        name = name.rstrip("\0")
        value = decode_parameter(d["param_value"], d["param_type"])
        entry = {"name": name, "value": value, "type": d["param_type"], "received_at": time.monotonic()}
        vehicle_key = (packet.system_id, packet.component_id)
        key = (*vehicle_key, name)
        with self.lock:
            self.cache.setdefault(vehicle_key, {})[name] = entry
            item = self.pending.pop(key, None)
            if item:
                future, _, expected = item
                matches = value == expected if d["param_type"] != 9 else math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-7) if expected is not None else True
                if expected is not None and not matches:
                    future.set_exception(ValueError(f"Parameter readback mismatch: requested {expected}, received {value}"))
                else:
                    future.set_result(entry)
                self.logger.write("parameter_value", target=packet.system_id, **entry)
            all_key = (*vehicle_key, "*")
            if all_key in self.pending:
                indices = self.indices.setdefault(vehicle_key, set())
                if 0 <= d["param_index"] < d["param_count"]:
                    indices.add(d["param_index"])
                if d["param_count"] > 0 and len(indices) == d["param_count"]:
                    self.pending.pop(all_key)[0].set_result(dict(self.cache[vehicle_key]))

    def tick(self, now):
        with self.lock:
            for key, item in list(self.pending.items()):
                if now >= item[1]:
                    self.pending.pop(key)
                    item[0].set_exception(TimeoutError(f"Parameter response incomplete/missing for drone {key[0]}: {key[2]}"))

    def close(self):
        with self.lock:
            for future, _, _ in self.pending.values():
                future.set_exception(RuntimeError("Connection closed"))
            self.pending.clear()
