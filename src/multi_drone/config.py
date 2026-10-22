from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Vec3


@dataclass
class DroneConfig:
    system_id: int
    role: str
    offset: Vec3 = field(default_factory=lambda: Vec3(0, 0, 0))
    origin: Vec3 | None = None


@dataclass
class Config:
    drones: list[DroneConfig]
    rate_hz: float = 20
    heartbeat_timeout: float = 3
    state_timeout: float = 0.6
    health_timeout: float = 3
    max_speed: float = 2
    max_radius: float = 100
    min_altitude: float = 0.5
    max_altitude: float = 30
    min_separation: float = 1
    min_battery: float = 20
    max_tracking_error: float = 10
    algorithm_timeout: float = 0.03
    frame_confirmed: bool = False
    source_system: int = 250
    failsafe: str = "hold"
    battery_id: int = 0

    def __post_init__(self):
        ids = [d.system_id for d in self.drones]
        if not ids or len(ids) != len(set(ids)) or any(type(n) is not int or not 1 <= n <= 255 for n in ids):
            raise ValueError("Drone IDs must be unique integers in 1..255")
        if sum(d.role == "leader" for d in self.drones) != 1:
            raise ValueError("Exactly one leader is required")
        if any(d.role not in ("leader", "follower") for d in self.drones):
            raise ValueError("Role must be leader or follower")
        if type(self.source_system) is not int or not 1 <= self.source_system <= 255 or self.source_system in ids:
            raise ValueError("GCS source_system must be unique and in 1..255")
        if type(self.battery_id) is not int or not 0 <= self.battery_id <= 255:
            raise ValueError("battery_id must be in 0..255")
        for name in ("rate_hz", "heartbeat_timeout", "state_timeout", "health_timeout", "max_speed", "max_radius", "min_altitude", "max_altitude", "min_separation", "min_battery", "max_tracking_error", "algorithm_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if not 5 <= self.rate_hz <= 50 or self.algorithm_timeout >= 1 / self.rate_hz:
            raise ValueError("rate_hz must be 5..50 and algorithm_timeout < one period")
        if self.min_altitude >= self.max_altitude or self.min_battery > 100:
            raise ValueError("Invalid altitude or battery limits")
        if self.failsafe not in ("hold", "land", "rtl") or type(self.frame_confirmed) is not bool:
            raise ValueError("Invalid failsafe/frame_confirmed")

    @property
    def leader_id(self) -> int:
        return next(d.system_id for d in self.drones if d.role == "leader")

    @property
    def followers(self) -> list[DroneConfig]:
        return [d for d in self.drones if d.role == "follower"]

    def drone(self, system_id: int) -> DroneConfig:
        for drone in self.drones:
            if drone.system_id == system_id:
                return drone
        raise ValueError(f"Drone {system_id} is not declared in config")

    @classmethod
    def default(cls, simulation: bool = True) -> Config:
        return cls([
            DroneConfig(1, "leader", origin=Vec3(0, 0, 0) if simulation else None),
            DroneConfig(2, "follower", Vec3(-3, -2, 0), Vec3(0, 0, 0) if simulation else None),
            DroneConfig(3, "follower", Vec3(-3, 2, 0), Vec3(0, 0, 0) if simulation else None),
        ], frame_confirmed=simulation)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        drones = [DroneConfig(
            system_id=d["system_id"], role=d["role"],
            offset=Vec3.parse(d.get("offset", [0, 0, 0])),
            origin=Vec3.parse(d["origin"]) if d.get("origin") is not None else None,
        ) for d in raw.pop("drones")]
        return cls(drones=drones, **raw)

    def as_dict(self) -> dict:
        raw = asdict(self)
        for output, drone in zip(raw["drones"], self.drones):
            output["offset"] = list(drone.offset)
            output["origin"] = list(drone.origin) if drone.origin is not None else None
        return raw
