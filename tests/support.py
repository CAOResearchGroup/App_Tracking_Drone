import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_drone.models import Packet, Registry, Vec3, VehicleState
from multi_drone.telemetry import Telemetry


class MemoryLog:
    def __init__(self):
        self.events = []

    def write(self, kind, **data):
        self.events.append((kind, data))


class FakeTransport:
    def __init__(self):
        self.sent = []

    def command(self, *args):
        self.sent.append(("command", args))

    def setpoint(self, *args):
        self.sent.append(("setpoint", args))

    def parameter(self, *args):
        self.sent.append(("parameter", args))


def heartbeat(n=1, **updates):
    data = dict(autopilot=12, type=2, base_mode=129, custom_mode=4 << 16 | 3 << 24, system_status=4)
    return Packet(n, 1, "HEARTBEAT", {**data, **updates})


def local(n=1, stamp=100, **updates):
    data = dict(time_boot_ms=stamp, x=0., y=0., z=-3., vx=0., vy=0., vz=0.)
    return Packet(n, 1, "LOCAL_POSITION_NED", {**data, **updates})


def ready_state(n=1, position=Vec3(0, 0, -3), now=None):
    now = time.monotonic() if now is None else now
    state = VehicleState(n, autopilot=12, heartbeat_at=now, position_at=now, landed_at=now,
                         position=position, velocity=Vec3(0, 0, 0), armed=True, mode="HOLD", landed=2, yaw=0.)
    for name, value in (("estimatorStatus.goodAttitudeEsimate", True), ("estimatorStatus.goodHorizPosRelEstimate", True),
                        ("battery.percentRemaining", 90), ("battery.chargeState", 1)):
        state.set_fact(name, value, now)
    return state


def wait_until(predicate, timeout=4):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Condition timed out")
