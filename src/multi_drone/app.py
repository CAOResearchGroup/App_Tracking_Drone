from __future__ import annotations

import threading
import time
import math

from .algorithm_runtime import AlgorithmRunner
from .commands import CommandService, MODE_PARAMS
from .connection import MavlinkTransport
from .formation import Coordinator
from .logger import EventLog
from .models import Registry
from .parameters import ParameterService, decode_parameter, encode_parameter
from .plotting import PlotService
from .safety import Safety, SafetyError
from .simulator import SimTransport
from .telemetry import Telemetry


STREAMS = {0: 1, 1: 2, 24: 2, 30: 20, 32: 20, 33: 5, 74: 5, 85: 5, 141: 2, 147: 2, 230: 2, 242: 0.5, 245: 2}


class App:
    def __init__(self, config, log_dir="logs"):
        self.config = config
        self.logger = EventLog(log_dir)
        self.runner = AlgorithmRunner()
        self.safety = Safety(config)
        self.transport = self.coordinator = self.commands = self.parameters = None
        self.registry = Registry()
        self.stop_event = threading.Event()
        self.threads = []
        self.last_error = None
        self.endpoint = None
        self.plots = PlotService(self)
        self.logger.write("session", config=config.as_dict())

    def connect(self, endpoint="sim"):
        new_transport = SimTransport(self.config) if endpoint == "sim" else MavlinkTransport(endpoint, self.config.source_system)
        self.disconnect()
        self.endpoint, self.transport = endpoint, new_transport
        self.registry = Registry()
        self.telemetry = Telemetry(self.registry, self.config.battery_id)
        self.commands = CommandService(new_transport, self.logger, self.config.source_system)
        self.parameters = ParameterService(new_transport, self.logger)
        self.coordinator = Coordinator(self.config, self.registry, self.safety, self.commands, new_transport, self.runner, self.logger)
        self.stop_event = threading.Event()
        self.last_error = None
        self._stream_queue, self._stream_future, self._discovered = [], None, set()
        self.threads = [threading.Thread(target=self._receive_loop, name="mavlink-io", daemon=True),
                        threading.Thread(target=self._control_loop, name="formation-control", daemon=True)]
        for thread in self.threads:
            thread.start()
        self.logger.write("connected", endpoint=endpoint)

    def _receive_loop(self):
        last_heartbeat = 0
        while not self.stop_event.is_set():
            try:
                packet = self.transport.receive(0.01)
                now = time.monotonic()
                if packet:
                    accepted = self.telemetry.ingest(packet, now)
                    if accepted:
                        self.commands.handle(packet)
                        self.parameters.handle(packet)
                        self.logger.write("telemetry", system_id=packet.system_id, component_id=packet.component_id,
                                          message=packet.kind, sequence=packet.sequence, data=self._clean(packet.data))
                        if packet.kind == "HEARTBEAT" and packet.system_id not in self._discovered:
                            self._discovered.add(packet.system_id)
                            if self.endpoint != "sim":
                                self.request_streams(packet.system_id)
                if now-last_heartbeat >= 1:
                    self.transport.heartbeat()
                    last_heartbeat = now
                    for state in self.registry.snapshot().values():
                        self.logger.write("vehicle_state", **state.as_dict(now))
                self.commands.tick(now)
                self.parameters.tick(now)
                if (self._stream_future is None or self._stream_future.done()) and self._stream_queue:
                    system_id, message_id, rate = self._stream_queue.pop(0)
                    state = self.registry.snapshot().get(system_id)
                    if state:
                        self._stream_future = self.commands.send(system_id, state.component_id, 511,
                            [float(message_id), 1e6/rate], "request-stream", timeout=1)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                self.logger.write("packet_rejected", error=str(exc))
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self.last_error = str(exc)
                self.logger.write("connection_error", error=str(exc))
                # Coordinator is stopped on its own thread; RX thread must not wait on plugin lock.
                self.stop_event.wait(0.05)

    def _control_loop(self):
        period = 1/self.config.rate_hz
        while not self.stop_event.is_set():
            start = time.monotonic()
            if self.coordinator:
                self.coordinator.tick(start)
            self.stop_event.wait(max(0, period-(time.monotonic()-start)))

    @staticmethod
    def _clean(value):
        import math
        if isinstance(value, dict):
            return {str(k): App._clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [App._clean(v) for v in value]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    def wait_for_vehicles(self, count=1, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if len(self.registry.snapshot()) >= count:
                return
            time.sleep(0.02)
        raise TimeoutError(f"Discovered fewer than {count} PX4 vehicles")

    def request_streams(self, system_id):
        self._stream_queue.extend((system_id, message, rate) for message, rate in STREAMS.items())

    def targets(self, token):
        if self.coordinator is None:
            raise RuntimeError("Not connected")
        if token == "all":
            return [d.system_id for d in self.config.drones]
        if token == "followers":
            return list(self.coordinator.follower_ids)
        if token == "leader":
            return [self.coordinator.leader_id]
        ids = [int(n) for n in token.split(",")]
        if len(ids) != len(set(ids)) or not ids:
            raise ValueError("Targets must be distinct")
        return ids

    def command(self, action, ids, altitude=3):
        if self.transport is None:
            raise RuntimeError("Not connected")
        if self.coordinator.phase != "idle" and set(ids) & set(self.coordinator.follower_ids):
            self.coordinator.stop("Operator command overrides formation", skip_ids=ids)
        states, now = self.registry.snapshot(), time.monotonic()
        futures, results = {}, []
        for n in ids:
            try:
                state = states.get(n)
                self.safety.connected(state, now)
                if action == "arm":
                    cmd, params = 400, [1.]
                elif action == "disarm":
                    if state.landed != 1 or state.landed_at is None or now-state.landed_at > self.config.health_timeout:
                        raise SafetyError("Disarm requires fresh ON_GROUND state")
                    cmd, params = 400, [0.]
                elif action == "takeoff":
                    if not state.armed or state.landed != 1:
                        raise SafetyError("Takeoff requires armed, ON_GROUND drone")
                    if state.home_amsl is None:
                        raise SafetyError("Home AMSL is unavailable; cannot encode takeoff altitude")
                    if not self.config.min_altitude <= altitude <= self.config.max_altitude:
                        raise ValueError("Takeoff altitude outside limits")
                    cmd, params = 22, [0., 0., 0., float("nan"), float("nan"), float("nan"), state.home_amsl+altitude]
                elif action in ("hold", "land", "rtl"):
                    cmd, params = 176, MODE_PARAMS[action]
                else:
                    raise ValueError("Use arm, disarm, takeoff, hold, land, rtl")
                futures[n] = self.commands.send(n, state.component_id, cmd, params, action)
            except Exception as exc:
                results.append({"target": n, "error": str(exc), "acknowledged": False})
        for n, future in futures.items():
            try:
                result = future.result(timeout=35)
                # ACK may arrive before the next heartbeat. Do not let an immediate
                # takeoff observe the old disarmed state after a successful arm.
                if result["acknowledged"] and action in ("arm", "disarm"):
                    expected = action == "arm"
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline:
                        latest = self.registry.snapshot().get(n)
                        if latest and latest.armed is expected:
                            result["state_confirmed"] = True
                            break
                        time.sleep(.01)
                    else:
                        result["state_confirmed"] = False
                        result["error"] = "ACK received but armed state not confirmed by heartbeat"
                results.append(result)
            except Exception as exc:
                results.append({"target": n, "error": str(exc), "acknowledged": False})
        successful = sum(r["acknowledged"] and r.get("state_confirmed", True) for r in results)
        return {"status": "accepted" if successful == len(ids) else "partial" if successful else "failed", "results": results}

    def parameter(self, operation, system_id, name="", value=None):
        state = self.registry.snapshot().get(system_id)
        self.safety.connected(state, time.monotonic())
        now = time.monotonic()
        if operation == "set" and (state.armed is not False or state.landed != 1 or state.landed_at is None or now-state.landed_at > self.config.health_timeout):
            raise SafetyError("Parameter writes require disarmed, ON_GROUND drone")
        if operation == "set":
            old = self.parameters.request(system_id, state.component_id, "get", name).result(timeout=6)
            latest = self.registry.snapshot()[system_id]
            self.safety.connected(latest, time.monotonic())
            if latest.armed is not False or latest.landed != 1 or latest.landed_at is None or time.monotonic()-latest.landed_at > self.config.health_timeout:
                raise SafetyError("Drone state changed; parameter write cancelled")
            self.parameters.request(system_id, state.component_id, "set", name, value, old["type"]).result(timeout=6)
            verified = self.parameters.request(system_id, state.component_id, "get", name).result(timeout=6)
            expected = decode_parameter(encode_parameter(value, old["type"]), old["type"])
            matches = verified["value"] == expected if old["type"] != 9 else math.isclose(verified["value"], expected, rel_tol=1e-6, abs_tol=1e-7)
            if not matches:
                raise ValueError("Parameter changed during final readback verification")
            self.logger.write("parameter_verified", target=system_id, old=old, new=verified)
            return verified
        return self.parameters.request(system_id, state.component_id, operation, name).result(timeout=6)

    def disconnect(self):
        if self.coordinator:
            self.coordinator.stop("Disconnect/shutdown")
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("Background loop did not stop")
        self.threads = []
        if self.transport:
            self.commands.cancel_all()
            self.parameters.close()
            self.transport.close()
            self.logger.write("disconnected", endpoint=self.endpoint)
            with self.registry.lock:
                for state in self.registry.vehicles.values():
                    state.heartbeat_at = state.position_at = None
        self.transport = None
        self.logger.flush()

    def close(self):
        try:
            self.disconnect()
        finally:
            try:
                self.plots.close()
            finally:
                self.runner.close()
                self.logger.close()
