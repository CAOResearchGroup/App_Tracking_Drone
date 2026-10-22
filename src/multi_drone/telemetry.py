from __future__ import annotations

import math

from .models import FACT_NAMES, Packet, Registry, Sample, Vec3, VehicleState


PX4_MODES = {1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 5: "ACRO", 6: "OFFBOARD", 7: "STABILIZED", 8: "RATTITUDE"}
PX4_AUTO = {2: "TAKEOFF", 3: "HOLD", 4: "MISSION", 5: "RTL", 6: "LAND", 8: "FOLLOW"}


def px4_mode(custom_mode: int) -> str:
    main, sub = (custom_mode >> 16) & 255, (custom_mode >> 24) & 255
    return PX4_AUTO.get(sub, f"AUTO:{sub}") if main == 4 else PX4_MODES.get(main, f"PX4:{main}:{sub}")


def quaternion_euler(q) -> tuple[float, float, float]:
    norm = math.sqrt(sum(v * v for v in q))
    if not math.isfinite(norm) or norm < 1e-8:
        raise ValueError("Invalid quaternion")
    w, x, y, z = (v / norm for v in q)
    return (math.atan2(2 * (w*x + y*z), 1 - 2 * (x*x + y*y)),
            math.asin(max(-1, min(1, 2 * (w*y - z*x)))),
            math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z)))


def valid_number(value, sentinel=None, scale=1):
    if value is None or value == sentinel or not math.isfinite(value):
        return None
    return value * scale


class Telemetry:
    def __init__(self, registry: Registry, battery_id: int = 0):
        self.registry, self.battery_id = registry, battery_id

    def ingest(self, packet: Packet, now: float) -> bool:
        """Accept only a discovered autopilot component, not a camera/GCS heartbeat."""
        with self.registry.lock:
            d, kind = packet.data, packet.kind
            state = self.registry.vehicles.get(packet.system_id)
            if kind == "HEARTBEAT":
                if d.get("autopilot") != 12 or d.get("type") == 6:
                    return False
                if state is None:
                    state = VehicleState(packet.system_id, packet.component_id, 12)
                    self.registry.vehicles[packet.system_id] = state
            if state is None or state.component_id != packet.component_id:
                return False
            if not self._accept_timestamp(state, kind, d.get("time_boot_ms")):
                return False
            state.messages += 1
            set_fact = lambda name, value: state.set_fact(name, value, now)
            if kind == "HEARTBEAT":
                was_armed = state.armed
                state.heartbeat_at = now
                state.armed = bool(d["base_mode"] & 128)
                state.mode = px4_mode(d["custom_mode"])
                state.system_status = d.get("system_status")
                if state.armed and not was_armed:
                    state.armed_since = now
                if state.armed_since is not None:
                    set_fact("vehicle.flightTime", max(0, now - state.armed_since))
                if not state.armed:
                    state.armed_since = None
            elif kind in ("ATTITUDE", "ATTITUDE_QUATERNION"):
                if kind == "ATTITUDE_QUATERNION":
                    roll, pitch, yaw = quaternion_euler([d[f"q{i}"] for i in range(1, 5)])
                else:
                    roll, pitch, yaw = d["roll"], d["pitch"], d["yaw"]
                if not all(math.isfinite(v) for v in (roll, pitch, yaw)):
                    return False
                state.yaw, state.attitude_at = yaw, now
                for name, val in (("roll", roll), ("pitch", pitch), ("yaw", yaw % (2*math.pi))):
                    set_fact("vehicle." + name, math.degrees(val))
                for name, source in (("rollRate", "rollspeed"), ("pitchRate", "pitchspeed"), ("yawRate", "yawspeed")):
                    set_fact("vehicle." + name, valid_number(d.get(source), scale=180/math.pi))
            elif kind == "LOCAL_POSITION_NED":
                position = Vec3(d["x"], d["y"], d["z"])
                velocity = Vec3(d["vx"], d["vy"], d["vz"])
                state.position, state.velocity, state.position_at = position, velocity, now
                for name in ("x", "y", "z", "vx", "vy", "vz"):
                    set_fact("localPosition." + name, d[name])
            elif kind == "POSITION_TARGET_LOCAL_NED":
                # Only LOCAL_NED targets have the same interpretation as our schema.
                if d.get("coordinate_frame") != 1:
                    return False
                mask = d.get("type_mask", 0)
                for bit, name in enumerate(("x", "y", "z", "vx", "vy", "vz")):
                    set_fact("localPositionSetpoint." + name, None if mask & (1 << bit) else d.get(name))
            elif kind == "GLOBAL_POSITION_INT":
                state.global_position = {"lat": d["lat"] / 1e7, "lon": d["lon"] / 1e7, "alt": d["alt"] / 1000,
                                         "velocity": [d[k] / 100 for k in ("vx", "vy", "vz")], "received_at": now}
                set_fact("vehicle.altitudeAMSL", d["alt"] / 1000)
                set_fact("vehicle.altitudeRelative", d["relative_alt"] / 1000)
            elif kind == "ALTITUDE":
                set_fact("vehicle.altitudeAMSL", valid_number(d.get("altitude_amsl")))
                set_fact("vehicle.altitudeRelative", valid_number(d.get("altitude_relative")))
            elif kind == "VFR_HUD":
                for name, source in (("groundSpeed", "groundspeed"), ("climbRate", "climb"), ("throttlePct", "throttle")):
                    set_fact("vehicle." + name, valid_number(d.get(source)))
            elif kind == "GPS_RAW_INT":
                for name, source, sentinel, scale in (("lat", "lat", None, 1e-7), ("lon", "lon", None, 1e-7),
                        ("hdop", "eph", 65535, 0.01), ("vdop", "epv", 65535, 0.01),
                        ("count", "satellites_visible", 255, 1), ("lock", "fix_type", None, 1)):
                    set_fact("gps." + name, valid_number(d.get(source), sentinel, scale))
            elif kind == "ESTIMATOR_STATUS":
                flags = d["flags"]
                for name, bit in (("goodAttitudeEsimate", 1), ("goodHorizVelEstimate", 2), ("goodVertVelEstimate", 4),
                                  ("goodHorizPosRelEstimate", 8), ("goodHorizPosAbsEstimate", 16), ("goodVertPosAbsEstimate", 32),
                                  ("gpsGlitch", 1024), ("accelError", 2048)):
                    set_fact("estimatorStatus." + name, bool(flags & bit))
                for name, key in (("velRatio", "vel_ratio"), ("horizPosRatio", "pos_horiz_ratio"), ("vertPosRatio", "pos_vert_ratio"),
                                  ("magRatio", "mag_ratio"), ("horizPosAccuracy", "pos_horiz_accuracy"), ("vertPosAccuracy", "pos_vert_accuracy")):
                    set_fact("estimatorStatus." + name, valid_number(d.get(key)))
            elif kind == "BATTERY_STATUS":
                values = {
                    "id": d["id"], "voltage": self._battery_voltage(d),
                    "current": valid_number(d.get("current_battery"), -1, 0.01),
                    "percentRemaining": valid_number(d.get("battery_remaining"), -1),
                    "mahConsumed": valid_number(d.get("current_consumed"), -1),
                    "temperature": valid_number(d.get("temperature"), 32767, 0.01),
                    "timeRemaining": valid_number(d.get("time_remaining"), 0), "chargeState": d.get("charge_state"),
                }
                state.batteries[d["id"]] = {**values, "received_at": now}
                if d["id"] == self.battery_id:
                    for name, value in values.items():
                        set_fact("battery." + name, value)
            elif kind == "EXTENDED_SYS_STATE":
                state.landed, state.landed_at = d["landed_state"], now
            elif kind == "HOME_POSITION":
                state.home_amsl, state.home_at = d["altitude"] / 1000, now
            elif kind == "SYS_STATUS":
                state.sensors = {"present": d["onboard_control_sensors_present"], "enabled": d["onboard_control_sensors_enabled"],
                                 "health": d["onboard_control_sensors_health"], "received_at": now}
            elif kind == "STATUSTEXT":
                state.last_status = str(d.get("text", ""))
            return True

    @staticmethod
    def _battery_voltage(data):
        base = [v for v in data.get("voltages", []) if v != 65535]
        if not base:
            return None
        return (sum(base) + sum(v for v in data.get("voltages_ext", []) if v not in (0, 65535))) / 1000

    @staticmethod
    def _accept_timestamp(state, kind, stamp):
        if not isinstance(stamp, int):
            return True
        previous = state.source_times.get(kind)
        if previous is not None and stamp < previous and not (previous > 0xF0000000 and stamp < 0x0FFFFFFF):
            last, count = state.reboot_candidates.get(kind, (-1, 0))
            count = count + 1 if stamp > last else 1
            state.reboot_candidates[kind] = (stamp, count)
            if count < 3:
                return False  # Reordering does not refresh freshness.
            state.position = state.velocity = None
            state.position_at = state.heartbeat_at = state.attitude_at = state.landed_at = None
            state.armed = state.mode = state.landed = state.yaw = None
            state.home_amsl = state.home_at = None
            state.sensors.clear()
            state.facts = {n: Sample() for n in FACT_NAMES}
            state.source_times.clear()
        elif previous == stamp:
            return False
        state.source_times[kind] = stamp
        state.reboot_candidates.pop(kind, None)
        state.source_boot_ms = stamp
        return True
