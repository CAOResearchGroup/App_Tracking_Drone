from __future__ import annotations

import threading
import time
from dataclasses import asdict

from .commands import MODE_PARAMS
from .models import Target, Vec3
from .safety import SafetyError


class Coordinator:
    def __init__(self, config, registry, safety, commands, transport, runner, logger):
        self.config, self.registry, self.safety = config, registry, safety
        self.commands, self.transport, self.runner, self.logger = commands, transport, runner, logger
        self.lock = threading.RLock()
        self.leader_id = config.leader_id
        self.follower_ids = [d.system_id for d in config.followers]
        self.phase = "idle"
        self.reason = None
        self.started = self.last_tick = 0.
        self.last_targets: dict[int, Target] = {}
        self.mode_futures = {}
        self.switch_at = None
        self.iterations = 0

    def configure(self, leader_id, follower_ids):
        with self.lock:
            self.require_idle()
            ids = [leader_id, *follower_ids]
            if not follower_ids or len(ids) != len(set(ids)):
                raise ValueError("Choose one leader and distinct followers")
            if any(n not in {d.system_id for d in self.config.drones} for n in ids):
                raise ValueError("All selected IDs must be present in config")
            self.leader_id, self.follower_ids = leader_id, list(follower_ids)

    def require_idle(self):
        if self.phase != "idle":
            raise RuntimeError("Stop formation before changing configuration/algorithm")

    def start(self):
        with self.lock:
            self.require_idle()
            if not self.follower_ids:
                raise ValueError("At least one follower is required")
            now, states = time.monotonic(), self.registry.snapshot()
            self.safety.fleet(states, [self.leader_id, *self.follower_ids], now)
            self.last_targets = {n: Target(n, self.safety.common_position(states[n])) for n in self.follower_ids}
            self.phase, self.reason = "priming", None
            self.started = self.last_tick = now
            self.switch_at, self.mode_futures = None, {}
            self.iterations = 0
            self.logger.write("formation_start", leader=self.leader_id, followers=self.follower_ids)

    def tick(self, now=None):
        with self.lock:
            if self.phase == "idle":
                return
            now = time.monotonic() if now is None else now
            dt = min(0.1, max(0.001, now-self.last_tick))
            # A large scheduling gap means Offboard continuity cannot be assumed.
            if now-self.last_tick > 0.4:
                self.stop("Control loop missed its deadline")
                return
            self.last_tick = now
            try:
                states = self.registry.snapshot()
                self.safety.fleet(states, [self.leader_id, *self.follower_ids], now)
                if self.phase == "active":
                    if any(states[n].mode != "OFFBOARD" for n in self.follower_ids):
                        raise SafetyError("Follower left OFFBOARD; operator/autopilot takeover")
                    context = self._context(states, dt, now)
                    desired = self.runner.step(context, self.config.algorithm_timeout)
                    self.safety.targets(desired, states, self.leader_id, dt)
                    targets = self._slew(desired, states, dt)
                    self.safety.targets(targets, states, self.leader_id, dt)
                else:
                    targets = self.last_targets
                for system_id, target in targets.items():
                    self._send(target, states[system_id])
                self.last_targets = targets
                self.iterations += 1
                if self.phase == "priming" and now-self.started >= 1.2:
                    self.mode_futures = {n: self.commands.send(n, states[n].component_id, 176, MODE_PARAMS["offboard"], "offboard") for n in self.follower_ids}
                    self.phase, self.switch_at = "switching", now
                elif self.phase == "switching":
                    for future in self.mode_futures.values():
                        if future.done() and not future.result()["acknowledged"]:
                            raise SafetyError("PX4 refused OFFBOARD")
                    if all(f.done() for f in self.mode_futures.values()) and all(states[n].mode == "OFFBOARD" for n in self.follower_ids):
                        self.phase = "active"
                        self.logger.write("formation_active", leader=self.leader_id, followers=self.follower_ids)
                    elif now-self.switch_at > 4:
                        raise SafetyError("OFFBOARD not confirmed by ACK and heartbeat")
                if self.iterations % 5 == 0:
                    self.logger.write("formation_state", **self.status())
            except Exception as exc:
                self.stop(str(exc))

    def _context(self, states, dt, now):
        def describe(n):
            state = states[n]
            return {"id": n, "position": list(self.safety.common_position(state)),
                    "velocity": list(state.velocity) if state.velocity is not None else None,
                    "yaw": state.yaw, "state_age": now-state.position_at}
        return {"dt": dt, "elapsed": now-self.started, "leader": describe(self.leader_id),
                "followers": [{**describe(n), "offset": list(self.config.drone(n).offset)} for n in self.follower_ids]}

    def _slew(self, desired, states, dt):
        result = {}
        for n, target in desired.items():
            if target.position is None:
                result[n] = target
                continue
            previous = self.last_targets.get(n)
            start = previous.position if previous and previous.position is not None else self.safety.common_position(states[n])
            delta = target.position-start
            distance = delta.norm()
            position = start+delta.scale(min(1., self.config.max_speed*dt/distance)) if distance else start
            result[n] = Target(n, position, yaw=target.yaw)
        return result

    def _send(self, target, state):
        origin = self.config.drone(target.system_id).origin
        local = Target(target.system_id, target.position-origin if target.position is not None else None, target.velocity, target.yaw)
        self.transport.setpoint(local, state.component_id)
        self.logger.write("setpoint", target=target.system_id, common=asdict(target), local=asdict(local))

    def stop(self, reason="Operator stopped formation", skip_ids=()):
        with self.lock:
            was_running = self.phase != "idle"
            self.phase, self.reason = "idle", reason
            if was_running:
                self.logger.write("formation_stopped", reason=reason)
                states, now = self.registry.snapshot(), time.monotonic()
                for n in self.follower_ids:
                    if n in skip_ids:
                        continue
                    state = states.get(n)
                    # Never overwrite a pilot's newly selected mode, and never change leader mode.
                    if state and state.mode == "OFFBOARD" and state.heartbeat_at is not None and now-state.heartbeat_at < self.config.heartbeat_timeout:
                        try:
                            self.commands.fallback(n, state.component_id, self.config.failsafe)
                        except Exception as exc:
                            self.logger.write("failsafe_send_failed", target=n, error=str(exc))

    def status(self):
        with self.lock:
            states, now = self.registry.snapshot(), time.monotonic()
            errors = {}
            for n, target in self.last_targets.items():
                state = states.get(n)
                if state is not None and state.position is not None and target.position is not None:
                    error = target.position-self.safety.common_position(state)
                    errors[str(n)] = {"xyz": list(error), "norm": error.norm(), "state_age": state.age(state.position_at, now)}
            return {"phase": self.phase, "reason": self.reason, "leader": self.leader_id, "followers": self.follower_ids,
                    "algorithm": self.runner.path or "builtin-offset", "iterations": self.iterations, "tracking_error": errors}
