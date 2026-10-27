"""Read-only plotting data, independent of Qt and flight-control code."""
from __future__ import annotations

import math
from collections import deque

from .models import FACT_NAMES


UNITS = {
    **{f"vehicle.{n}": "deg" for n in ("roll", "pitch", "yaw")},
    **{f"vehicle.{n}": "deg/s" for n in ("rollRate", "pitchRate", "yawRate")},
    "vehicle.groundSpeed": "m/s", "vehicle.climbRate": "m/s",
    "vehicle.altitudeRelative": "m", "vehicle.altitudeAMSL": "m",
    "vehicle.throttlePct": "%", "vehicle.flightTime": "s",
    "gps.lat": "deg", "gps.lon": "deg", "gps.hdop": "ratio", "gps.vdop": "ratio",
    "gps.lock": "enum", "gps.count": "count",
    **{f"{g}.{n}": "m" for g in ("localPosition", "localPositionSetpoint") for n in ("x", "y", "z")},
    **{f"{g}.{n}": "m/s" for g in ("localPosition", "localPositionSetpoint") for n in ("vx", "vy", "vz")},
    **{f"estimatorStatus.{n}": "0/1" for n in (
        "goodAttitudeEsimate", "goodHorizVelEstimate", "goodVertVelEstimate", "goodHorizPosRelEstimate",
        "goodHorizPosAbsEstimate", "goodVertPosAbsEstimate", "gpsGlitch", "accelError")},
    **{f"estimatorStatus.{n}": "ratio" for n in ("velRatio", "horizPosRatio", "vertPosRatio", "magRatio")},
    "estimatorStatus.horizPosAccuracy": "m", "estimatorStatus.vertPosAccuracy": "m",
    "battery.id": "ID", "battery.voltage": "V", "battery.current": "A", "battery.percentRemaining": "%",
    "battery.mahConsumed": "mAh", "battery.temperature": "°C", "battery.timeRemaining": "s",
    "battery.chargeState": "enum",
}


def validate_fields(system_id, fields):
    if isinstance(system_id, bool) or not isinstance(system_id, int) or not 1 <= system_id <= 255:
        raise ValueError("Invalid drone ID")
    if not isinstance(fields, (list, tuple)) or not fields or any(f not in FACT_NAMES for f in fields):
        raise ValueError("Choose one or more telemetry fields")
    return list(dict.fromkeys(fields))


def group_by_unit(fields):
    groups = {}
    for name in fields:
        groups.setdefault(UNITS[name], []).append(name)
    return list(groups.values())


def snapshot_frame(app, now):
    """Copy scalar telemetry under the registry lock, without touching transport."""
    registry = app.registry
    with registry.lock:
        vehicles = {
            n: {"mode": s.mode, "armed": s.armed, "heartbeat_at": s.heartbeat_at,
                "status_messages": [dict(message) for message in s.status_messages],
                "facts": {name: (sample.value, sample.received_at) for name, sample in s.facts.items()}}
            for n, s in registry.vehicles.items()
        }
    return {"epoch": id(registry), "now": now, "connected": app.transport is not None,
            "endpoint": app.endpoint, "vehicles": vehicles,
            "timeouts": {"heartbeat": app.config.heartbeat_timeout, "state": app.config.state_timeout,
                         "health": app.config.health_timeout}}


class PlotHistory:
    """Bounded, decimated display history. Never synthesize samples for stale data."""
    def __init__(self, seconds=120, max_points=2400):
        self.seconds, self.max_points = seconds, max_points
        self.epoch = self.started = None
        self.frame = {}
        self.points = {}
        self.last_stamp = {}
        self.live = {}

    def timeout(self, name):
        limits = self.frame.get("timeouts", {})
        return limits.get("state", .6) if name.startswith(("localPosition.", "localPositionSetpoint.")) else limits.get("health", 3.)

    def value(self, system_id, name, now):
        state = self.frame.get("vehicles", {}).get(system_id, {})
        value, at = state.get("facts", {}).get(name, (None, None))
        hb = state.get("heartbeat_at")
        available = (self.frame.get("connected", False) and hb is not None and
                     now-hb <= self.frame.get("timeouts", {}).get("heartbeat", 3.) and
                     at is not None and now-at <= self.timeout(name) and
                     isinstance(value, (int, float)) and math.isfinite(value))
        return (float(value) if available else None), at

    def update(self, frame):
        if frame["epoch"] != self.epoch:
            self.points.clear()
            self.last_stamp.clear()
            self.live.clear()
            self.epoch, self.started = frame["epoch"], frame["now"]
        self.frame = frame
        self.advance(frame["now"])

    def advance(self, now):
        if self.started is None:
            return
        for n in self.frame.get("vehicles", {}):
            for name in FACT_NAMES:
                key = (n, name)
                points = self.points.setdefault(key, deque(maxlen=self.max_points))
                value, stamp = self.value(n, name, now)
                if value is None:
                    if self.live.get(key):
                        points.append((now-self.started, math.nan))
                    self.live[key] = False
                elif stamp != self.last_stamp.get(key):
                    # Break lines across gaps even if the GUI missed intermediate frames.
                    last = self.last_stamp.get(key)
                    if last is not None and stamp-last > self.timeout(name) and self.live.get(key):
                        points.append(((stamp+last)/2-self.started, math.nan))
                    points.append((stamp-self.started, value))
                    self.last_stamp[key], self.live[key] = stamp, True
                while points and points[0][0] < now-self.started-self.seconds:
                    points.popleft()

    def series(self, system_id, name):
        points = self.points.get((system_id, name), ())
        return [p[0] for p in points], [p[1] for p in points]
