from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from .models import Packet, Target


class Transport(ABC):
    @abstractmethod
    def receive(self, timeout: float) -> Packet | None: ...

    @abstractmethod
    def command(self, system_id: int, component_id: int, command: int, params: list[float]): ...

    @abstractmethod
    def setpoint(self, target: Target, component_id: int): ...

    @abstractmethod
    def parameter(self, system_id: int, component_id: int, operation: str, name: str = "", value: float = 0, param_type: int = 9): ...

    def heartbeat(self):
        pass

    @abstractmethod
    def close(self): ...


class MavlinkTransport(Transport):
    """One bidirectional link carrying multiple sysids; all sends explicitly targeted."""

    def __init__(self, endpoint: str, source_system: int = 250):
        try:
            from pymavlink import mavutil
            from pymavlink.dialects.v20 import common
        except ImportError as exc:
            raise RuntimeError("Install MAVLink support: .venv/bin/python -m pip install -e '.[mavlink]'") from exc
        if not endpoint.startswith(("udpin:", "udpout:", "tcp:", "tcpin:")):
            raise ValueError("Use udpin:host:port, udpout:host:port, tcp:host:port or tcpin:host:port")
        self.link = mavutil.mavlink_connection(endpoint, source_system=source_system, source_component=190, autoreconnect=True)
        # Explicit v2 dialect; do not rely on process-global MAVLINK20 state.
        self.link.mav = common.MAVLink(self.link, srcSystem=source_system, srcComponent=190)
        self.link.mav.robust_parsing = True
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.endpoint = endpoint

    def receive(self, timeout: float) -> Packet | None:
        message = self.link.recv_match(blocking=True, timeout=timeout)
        if message is None or message.get_type() == "BAD_DATA":
            return None
        data = message.to_dict()
        if message.get_type() == "STATUSTEXT":
            # Decode UTF-8 only after joining chunks (a character can cross chunks).
            data["text"] = message._text_raw
        return Packet(message.get_srcSystem(), message.get_srcComponent(), message.get_type(), data, message.get_seq())

    def heartbeat(self):
        with self.lock:
            self.link.mav.heartbeat_send(6, 8, 0, 0, 4)  # GCS, INVALID autopilot, ACTIVE.

    def command(self, system_id, component_id, command, params):
        with self.lock:
            self.link.mav.command_long_send(system_id, component_id, command, 0, *(params + [0.] * (7-len(params))))

    def setpoint(self, target, component_id):
        position, velocity = target.position, target.velocity
        # Ignore acceleration, yaw-rate; enable only the requested control fields.
        mask = (7 << 6) | (1 << 11) | ((7 << 3) if position else 7)
        if target.yaw is None:
            mask |= 1 << 10
        with self.lock:
            self.link.mav.set_position_target_local_ned_send(
                int((time.monotonic()-self.started)*1000) & 0xFFFFFFFF,
                target.system_id, component_id, 1, mask,
                *(list(position) if position else [0., 0., 0.]),
                *(list(velocity) if velocity else [0., 0., 0.]),
                0., 0., 0., target.yaw or 0., 0.)

    def parameter(self, system_id, component_id, operation, name="", value=0, param_type=9):
        encoded = name.encode("ascii")
        if len(encoded) > 16:
            raise ValueError("MAVLink parameter ID is at most 16 ASCII characters")
        with self.lock:
            if operation == "list":
                self.link.mav.param_request_list_send(system_id, component_id)
            elif operation == "get":
                self.link.mav.param_request_read_send(system_id, component_id, encoded, -1)
            elif operation == "set":
                self.link.mav.param_set_send(system_id, component_id, encoded, value, param_type)
            else:
                raise ValueError("Unknown parameter operation")

    def close(self):
        self.link.close()
