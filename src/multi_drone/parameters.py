from __future__ import annotations

import math
import struct
import threading
import time
from concurrent.futures import Future
from collections import OrderedDict


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
    def __init__(self, transport, logger, *, max_parameters=4096, max_vehicles=32):
        if min(max_parameters, max_vehicles) <= 0:
            raise ValueError("Parameter cache limits must be positive")
        self.max_parameters, self.max_vehicles = max_parameters, max_vehicles
        self.transport, self.logger = transport, logger
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.pending: dict[tuple[int, int, str | None], tuple[Future, float, object]] = {}
        self.lists = {}

    def request(self, system_id, component_id, operation, name="", value=None, param_type=9, timeout=5) -> Future:
        if operation not in ("get", "set", "list"):
            raise ValueError("Unknown parameter operation")
        if operation != "list" and (not name or len(name.encode("ascii")) > 16):
            raise ValueError("Parameter ID must contain 1..16 ASCII characters")
        wire = encode_parameter(value, param_type) if operation == "set" else 0
        expected = decode_parameter(wire, param_type) if operation == "set" else None
        key = (system_id, component_id, None if operation == "list" else name)
        with self.lock:
            if key in self.pending:
                raise RuntimeError("Parameter request already pending")
            future = Future()
            self.pending[key] = (future, time.monotonic()+timeout, expected)
            if operation == "list":
                self.lists[(system_id, component_id)] = {"count": None, "indices": {}, "entries": {}}
            self.logger.write("parameter_request", target=system_id, operation=operation, name=name, value=value)
            try:
                self.transport.parameter(system_id, component_id, operation, name, wire, param_type)
            except Exception as exc:
                self.pending.pop(key)
                if operation == "list":
                    self.lists.pop((system_id, component_id), None)
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
        if not name or "\0" in name or len(name.encode("ascii")) > 16:
            raise ValueError("Invalid parameter ID")
        vehicle_key = (packet.system_id, packet.component_id)
        key = (*vehicle_key, name)
        all_key = (*vehicle_key, None)
        with self.lock:
            if key not in self.pending and all_key not in self.pending:
                return  # Unsolicited network data must not allocate cache entries.
            value = decode_parameter(d["param_value"], d["param_type"])
            entry = {"name": name, "value": value, "type": d["param_type"], "received_at": time.monotonic()}
            listing = self.lists.get(vehicle_key)
            if listing is not None:
                count, index = d["param_count"], d["param_index"]
                indices, entries = listing["indices"], listing["entries"]
                if (type(count) is not int or type(index) is not int
                        or not 0 < count <= self.max_parameters or not 0 <= index < count
                        or listing["count"] not in (None, count)
                        or (index in indices and indices[index] != name)
                        or (name in entries and indices.get(index) != name)):
                    self.pending.pop(all_key)[0].set_exception(ValueError("Invalid/inconsistent parameter list metadata"))
                    self.lists.pop(vehicle_key)
                    return
                listing["count"] = count
                indices[index], entries[name] = name, entry
            cache = self.cache.setdefault(vehicle_key, OrderedDict())
            cache[name] = entry
            cache.move_to_end(name)
            while len(cache) > self.max_parameters:
                cache.popitem(last=False)
            self.cache.move_to_end(vehicle_key)
            while len(self.cache) > self.max_vehicles:
                self.cache.popitem(last=False)
            item = self.pending.pop(key, None)
            if item:
                future, _, expected = item
                matches = value == expected if d["param_type"] != 9 else math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-7) if expected is not None else True
                if expected is not None and not matches:
                    future.set_exception(ValueError(f"Parameter readback mismatch: requested {expected}, received {value}"))
                else:
                    future.set_result(entry)
                self.logger.write("parameter_value", target=packet.system_id, **entry)
            if listing is not None and len(listing["indices"]) == listing["count"]:
                self.pending.pop(all_key)[0].set_result(dict(listing["entries"]))
                self.lists.pop(vehicle_key)

    def tick(self, now):
        with self.lock:
            for key, item in list(self.pending.items()):
                if now >= item[1]:
                    self.pending.pop(key)
                    if key[2] is None:
                        self.lists.pop(key[:2], None)
                    item[0].set_exception(TimeoutError(f"Parameter response incomplete/missing for drone {key[0]}: {key[2]}"))

    def close(self):
        with self.lock:
            for future, _, _ in self.pending.values():
                future.set_exception(RuntimeError("Connection closed"))
            self.pending.clear()
            self.lists.clear()
            self.cache.clear()
