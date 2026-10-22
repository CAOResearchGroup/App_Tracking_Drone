from __future__ import annotations

from itertools import combinations
import time

from .models import Target, Vec3


class SafetyError(RuntimeError):
    pass


class Safety:
    def __init__(self, config):
        self.config = config

    def connected(self, state, now):
        if state is None or state.heartbeat_at is None or now-state.heartbeat_at > self.config.heartbeat_timeout:
            raise SafetyError("Drone missing or heartbeat expired")
        if state.autopilot != 12:
            raise SafetyError("Only PX4 autopilots supported by this adapter")

    def common_position(self, state):
        origin = self.config.drone(state.system_id).origin
        if origin is None or state.position is None:
            raise SafetyError(f"Drone {state.system_id}: local origin/position not configured")
        return origin + state.position

    def ready(self, state, now, airborne=True):
        self.connected(state, now)
        if state.position_at is None or now-state.position_at > self.config.state_timeout:
            raise SafetyError(f"Drone {state.system_id}: stale/missing position")
        if not state.armed:
            raise SafetyError(f"Drone {state.system_id}: not armed")
        if airborne and (state.landed != 2 or state.landed_at is None or now-state.landed_at > self.config.health_timeout):
            raise SafetyError(f"Drone {state.system_id}: not confirmed airborne")
        for key in ("goodAttitudeEsimate", "goodHorizPosRelEstimate"):
            sample = state.facts["estimatorStatus."+key]
            if sample.value is not True or sample.received_at is None or now-sample.received_at > self.config.health_timeout:
                raise SafetyError(f"Drone {state.system_id}: estimator {key} unavailable/unhealthy")
        accel = state.facts["estimatorStatus.accelError"]
        if accel.value is True and accel.received_at is not None and now-accel.received_at <= self.config.health_timeout:
            raise SafetyError(f"Drone {state.system_id}: estimator acceleration error")
        battery = state.facts["battery.percentRemaining"]
        if battery.value is None or battery.received_at is None or now-battery.received_at > self.config.health_timeout:
            raise SafetyError(f"Drone {state.system_id}: battery status unavailable/stale")
        if battery.value < self.config.min_battery or state.value("battery.chargeState") in (3, 4, 5, 6):
            raise SafetyError(f"Drone {state.system_id}: battery low/critical")
        self.common_position(state)

    def fleet(self, states, ids, now):
        if not self.config.frame_confirmed:
            raise SafetyError("Set frame_confirmed only after validating the common NED frame and origins")
        positions = {}
        for system_id in ids:
            state = states.get(system_id)
            self.ready(state, now)
            positions[system_id] = self.common_position(state)
        self.separation(positions)

    def bounds(self, position):
        if (position.x**2+position.y**2)**0.5 > self.config.max_radius:
            raise SafetyError("Target exceeds common-frame horizontal radius")
        if not self.config.min_altitude <= -position.z <= self.config.max_altitude:
            raise SafetyError("Target exceeds common-frame altitude limits")

    def separation(self, positions):
        for (a, pa), (b, pb) in combinations(positions.items(), 2):
            if (pa-pb).norm() < self.config.min_separation:
                raise SafetyError(f"Separation below limit: drones {a} and {b}")

    def targets(self, targets: dict[int, Target], states, leader_id, dt):
        positions = {leader_id: self.common_position(states[leader_id])}
        for system_id, target in targets.items():
            current = self.common_position(states[system_id])
            if target.position is not None:
                self.bounds(target.position)
                if (target.position-current).norm() > self.config.max_tracking_error:
                    raise SafetyError(f"Drone {system_id}: target too far from actual position")
                positions[system_id] = target.position
            else:
                for name in ("goodHorizVelEstimate", "goodVertVelEstimate"):
                    sample = states[system_id].facts["estimatorStatus."+name]
                    now = time.monotonic()
                    if sample.value is not True or sample.received_at is None or now-sample.received_at > self.config.health_timeout:
                        raise SafetyError(f"Drone {system_id}: velocity estimate unavailable/unhealthy")
                if target.velocity.norm() > self.config.max_speed:
                    raise SafetyError("Velocity target exceeds max_speed")
                positions[system_id] = current+target.velocity.scale(dt)
                self.bounds(positions[system_id])
        self.separation(positions)
