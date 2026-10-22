from __future__ import annotations

import copy
import math
import threading
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float

    def __post_init__(self):
        if any(isinstance(v, bool) or not math.isfinite(v) for v in self):
            raise ValueError("Vector must contain three finite numbers")

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __add__(self, other: Vec3) -> Vec3:
        return Vec3(*(a + b for a, b in zip(self, other)))

    def __sub__(self, other: Vec3) -> Vec3:
        return Vec3(*(a - b for a, b in zip(self, other)))

    def scale(self, factor: float) -> Vec3:
        return Vec3(*(v * factor for v in self))

    def norm(self) -> float:
        return math.sqrt(sum(v * v for v in self))

    @classmethod
    def parse(cls, value) -> Vec3:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError("Expected [north, east, down]")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
            raise ValueError("Vector components must be numbers")
        return cls(*map(float, value))


FACT_GROUPS = {
    "vehicle": "roll pitch yaw rollRate pitchRate yawRate groundSpeed climbRate altitudeRelative altitudeAMSL throttlePct flightTime".split(),
    "gps": "lat lon hdop vdop lock count".split(),
    "localPosition": "x y z vx vy vz".split(),
    "localPositionSetpoint": "x y z vx vy vz".split(),
    "estimatorStatus": "goodAttitudeEsimate goodHorizVelEstimate goodVertVelEstimate goodHorizPosRelEstimate goodHorizPosAbsEstimate goodVertPosAbsEstimate gpsGlitch accelError velRatio horizPosRatio vertPosRatio magRatio horizPosAccuracy vertPosAccuracy".split(),
    "battery": "id voltage current percentRemaining mahConsumed temperature timeRemaining chargeState".split(),
}
FACT_NAMES = tuple(f"{group}.{name}" for group, names in FACT_GROUPS.items() for name in names)


@dataclass
class Sample:
    value: Any = None
    received_at: float | None = None


@dataclass
class VehicleState:
    system_id: int
    component_id: int = 1
    autopilot: int | None = None
    heartbeat_at: float | None = None
    position_at: float | None = None
    attitude_at: float | None = None
    landed_at: float | None = None
    home_at: float | None = None
    armed: bool | None = None
    mode: str | None = None
    landed: int | None = None
    system_status: int | None = None
    position: Vec3 | None = None  # Vehicle-local NED, never silently assumed common.
    velocity: Vec3 | None = None
    yaw: float | None = None  # Radians, full precision.
    home_amsl: float | None = None
    global_position: dict = field(default_factory=dict)
    sensors: dict = field(default_factory=dict)
    facts: dict[str, Sample] = field(default_factory=lambda: {n: Sample() for n in FACT_NAMES})
    batteries: dict[int, dict] = field(default_factory=dict)
    messages: int = 0
    last_status: str | None = None
    source_boot_ms: int | None = None
    source_times: dict[str, int] = field(default_factory=dict)
    reboot_candidates: dict[str, tuple[int, int]] = field(default_factory=dict)
    armed_since: float | None = None

    def set_fact(self, name: str, value: Any, now: float):
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        self.facts[name] = Sample(value, now)

    def value(self, name: str):
        return self.facts[name].value

    def age(self, timestamp: float | None, now: float) -> float | None:
        return None if timestamp is None else max(0.0, now - timestamp)

    def as_dict(self, now: float) -> dict:
        data = asdict(self)
        data["heartbeat_age"] = self.age(self.heartbeat_at, now)
        data["position_age"] = self.age(self.position_at, now)
        data["facts"] = {n: {"value": s.value, "age": self.age(s.received_at, now)} for n, s in self.facts.items()}
        return data


class Registry:
    def __init__(self):
        self.lock = threading.RLock()
        self.vehicles: dict[int, VehicleState] = {}

    def snapshot(self) -> dict[int, VehicleState]:
        with self.lock:
            return copy.deepcopy(self.vehicles)


@dataclass(frozen=True)
class Packet:
    system_id: int
    component_id: int
    kind: str
    data: dict
    sequence: int = 0


@dataclass(frozen=True)
class Target:
    system_id: int
    position: Vec3 | None = None  # Common NED in coordinator; converted on send.
    velocity: Vec3 | None = None
    yaw: float | None = None

    def __post_init__(self):
        if (self.position is None) == (self.velocity is None):
            raise ValueError("Target must specify exactly one of position or velocity")
        if self.yaw is not None and not math.isfinite(self.yaw):
            raise ValueError("Yaw must be finite")
