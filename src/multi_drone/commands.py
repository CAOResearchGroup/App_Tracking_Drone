from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass


MODE_PARAMS = {"hold": [1., 4., 3.], "offboard": [1., 6., 0.], "land": [1., 4., 6.], "rtl": [1., 4., 5.]}


@dataclass
class Pending:
    correlation_id: str
    future: Future
    deadline: float
    progress_deadline: float
    action: str


class CommandService:
    def __init__(self, transport, logger, source_system=250):
        self.transport, self.logger, self.source_system = transport, logger, source_system
        self.lock = threading.RLock()
        self.pending: dict[tuple[int, int, int], Pending] = {}

    def send(self, system_id, component_id, command, params, action="command", timeout=3) -> Future:
        key = (system_id, component_id, command)
        with self.lock:
            if key in self.pending:
                raise RuntimeError(f"Command {command} already pending for drone {system_id}")
            now = time.monotonic()
            future = Future()
            pending = Pending(uuid.uuid4().hex, future, now+timeout, now+30, action)
            self.pending[key] = pending
            self.logger.write("command_sent", target=system_id, component=component_id, command=command,
                              action=action, params=params, correlation_id=pending.correlation_id)
            try:
                self.transport.command(system_id, component_id, command, params)
            except Exception as exc:
                self.pending.pop(key)
                future.set_exception(exc)
            return future

    def handle(self, packet):
        if packet.kind != "COMMAND_ACK":
            return
        d = packet.data
        # Ignore ACKs explicitly addressed to another GCS.
        if d.get("target_system", 0) not in (0, self.source_system):
            return
        if d.get("target_component", 0) not in (0, 190):
            return
        key = (packet.system_id, packet.component_id, d["command"])
        with self.lock:
            item = self.pending.get(key)
            if item is None:
                return
            self.logger.write("command_ack", target=packet.system_id, command=d["command"], result=d["result"],
                              progress=d.get("progress"), correlation_id=item.correlation_id)
            if d["result"] == 5:  # MAV_RESULT_IN_PROGRESS is not completion.
                item.deadline = min(item.progress_deadline, time.monotonic()+3)
                return
            self.pending.pop(key)
            item.future.set_result({"target": packet.system_id, "action": item.action, "result": d["result"],
                                    "acknowledged": d["result"] == 0, "correlation_id": item.correlation_id})

    def tick(self, now):
        with self.lock:
            for key, item in list(self.pending.items()):
                if now >= item.deadline:
                    self.pending.pop(key)
                    self.logger.write("command_timeout", target=key[0], command=key[2], correlation_id=item.correlation_id)
                    item.future.set_exception(TimeoutError(f"No final ACK for {item.action} from drone {key[0]}; result unknown"))

    def cancel_all(self, reason="Connection closed"):
        with self.lock:
            for item in self.pending.values():
                if not item.future.done():
                    item.future.set_exception(RuntimeError(reason))
            self.pending.clear()

    def fallback(self, system_id, component_id, action):
        # Called by watchdog: never wait for an ACK on the receiving/control thread.
        self.logger.write("failsafe_request", target=system_id, action=action)
        self.transport.command(system_id, component_id, 176, MODE_PARAMS[action])
