from __future__ import annotations

import argparse
import json
import math
import shlex
import sys
import time
import threading
from pathlib import Path

from .app import App
from .config import Config
from .logger import json_safe, replay
from .models import Vec3
from .simulator import SimTransport


HELP = """Commands (NED: x=North, y=East, z=Down; distances m):
  list                              Danh sách drone, mode, vị trí, tuổi dữ liệu
  status [ID]                       Trạng thái và đủ 52 trường (thiếu = null)
  watch [seconds]                   In trạng thái mỗi giây; Ctrl-C để dừng xem
  plot open|close|status            Đồ thị realtime, kéo thả/gộp theo từng drone
  arm|disarm TARGET                 TARGET = ID, 1,2,3, all, leader, followers
  takeoff TARGET [height_m]         Cất cánh theo độ cao so với home
  hold|land|rtl TARGET              Lệnh một drone hoặc nhóm, kết quả từng drone
  params list ID [filter]           Đọc toàn bộ parameter, lọc theo tên
  params get ID NAME                Đọc một parameter
  params set ID NAME VALUE          Ghi và đọc lại; chỉ khi disarmed/on-ground
  formation setup LEADER F1 [F2]    Chọn vai trò từ các ID có trong config
  formation offset ID N E D         Đặt offset trong hệ NED chung, khi idle
  formation start|stop|status       Bắt đầu/dừng/xem luồng Offboard leader-follower
  formation wait [seconds]          Chờ xác nhận active; lỗi nếu vào Offboard thất bại
  algorithm load PATH.py            Nạp hàm step(context), chạy trong worker riêng
  algorithm builtin|status          Dùng thuật toán offset mặc định / xem plugin
  streams TARGET                    Yêu cầu các luồng MAVLink đã chọn
  connect sim|URI                   Kết nối lại; URI: udpin:0.0.0.0:14550
  disconnect                        Dừng điều phối, ngắt kết nối
  config                            Xem origin, offset và các giới hạn
  log                               Xem đường dẫn session JSONL
  sim move ID N E D                 Di chuyển drone mô phỏng (leader)
  sim drop|freeze|reject ID on|off   Thử mất link / stale state / command rejection
  wait SECONDS                      Chờ trong script trong khi vòng điều khiển chạy
  require-sim                       Chặn chạy script demo nhầm trên drone thật
  help                              Xem lệnh
  quit                              Dừng đội hình và thoát
"""


def print_json(value):
    print(json.dumps(json_safe(value), ensure_ascii=False, allow_nan=False), flush=True)


class Shell:
    def __init__(self, app):
        self.app = app

    def execute(self, line):
        words = shlex.split(line, comments=True)
        if not words:
            return True
        command, *args = words
        app = self.app
        if command in ("quit", "exit"):
            return False
        if command == "help":
            print(HELP)
        elif command == "list":
            self.list_vehicles()
        elif command == "status":
            states = app.registry.snapshot()
            if args:
                print_json(states[int(args[0])].as_dict(time.monotonic()))
            else:
                print_json({"endpoint": app.endpoint, "connected": app.transport is not None,
                            "last_error": app.last_error, "formation": app.coordinator.status() if app.coordinator else None,
                            "vehicles": {str(n): s.as_dict(time.monotonic()) for n, s in states.items()}})
        elif command == "plot":
            operation = args[0] if len(args) == 1 else ""
            if operation == "open":
                print_json(app.plots.open())
            elif operation == "close":
                app.plots.close()
                print_json(app.plots.status())
            elif operation == "status":
                print_json(app.plots.status())
            else:
                raise ValueError("Usage: plot open|close|status")
        elif command == "watch":
            duration = float(args[0]) if args else 10.
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("watch duration must be finite and nonnegative")
            deadline = time.monotonic()+duration
            try:
                while time.monotonic() < deadline:
                    self.list_vehicles()
                    time.sleep(min(1, max(0, deadline-time.monotonic())))
            except KeyboardInterrupt:
                pass
        elif command in ("arm", "disarm", "takeoff", "hold", "land", "rtl"):
            if not args:
                raise ValueError(f"Usage: {command} TARGET" + (" [height_m]" if command == "takeoff" else ""))
            result = app.command(command, app.targets(args[0]), float(args[1]) if len(args) > 1 else 3.)
            print_json(result)
            if result["status"] != "accepted":
                raise RuntimeError(f"{command}: {result['status']}; inspect per-drone results")
        elif command == "params":
            operation, raw_id, *rest = args
            n = int(raw_id)
            if operation == "list":
                entries = app.parameter("list", n)
                if rest:
                    entries = {k: v for k, v in entries.items() if rest[0].upper() in k.upper()}
                print_json(entries)
            elif operation == "get" and len(rest) == 1:
                print_json(app.parameter("get", n, rest[0]))
            elif operation == "set" and len(rest) == 2:
                print_json(app.parameter("set", n, rest[0], float(rest[1])))
            else:
                raise ValueError("Usage: params list|get|set ID [NAME] [VALUE]")
        elif command == "formation":
            if app.transport is None:
                raise RuntimeError("Not connected")
            operation, *rest = args
            coordinator = app.coordinator
            if operation == "setup":
                coordinator.configure(int(rest[0]), [int(n) for n in rest[1:]])
            elif operation == "offset":
                with coordinator.lock:
                    coordinator.require_idle()
                    app.config.drone(int(rest[0])).offset = Vec3.parse([float(v) for v in rest[1:]])
            elif operation == "start":
                coordinator.start()
            elif operation == "stop":
                coordinator.stop()
            elif operation == "wait":
                timeout = float(rest[0]) if rest else 5.
                if not math.isfinite(timeout) or not 0 < timeout <= 60:
                    raise ValueError("Formation wait timeout must be 0..60 seconds")
                deadline = time.monotonic()+timeout
                while coordinator.phase not in ("active", "idle") and time.monotonic() < deadline:
                    time.sleep(.02)
                if coordinator.phase != "active":
                    raise RuntimeError(coordinator.reason or "Formation did not become active")
            elif operation != "status":
                raise ValueError("formation setup|offset|start|stop|status")
            print_json(coordinator.status())
        elif command == "algorithm":
            operation, *rest = args
            lock = app.coordinator.lock if app.coordinator else threading.RLock()
            with lock:
                if app.coordinator and operation != "status":
                    app.coordinator.require_idle()
                if operation == "load":
                    app.runner.load(rest[0])
                elif operation == "builtin":
                    app.runner.use_builtin()
                elif operation != "status":
                    raise ValueError("algorithm load PATH.py | builtin | status")
            print_json({"algorithm": app.runner.path or "builtin-offset"})
        elif command == "streams":
            if app.transport is None:
                raise RuntimeError("Not connected")
            for n in app.targets(args[0]):
                app.request_streams(n)
            print_json({"stream_requests": "queued"})
        elif command == "connect":
            if args[0] != "sim" and app.config.frame_confirmed and app.endpoint == "sim":
                raise ValueError("Restart with --config config/px4.json --connect URI for real PX4; do not reuse simulator origins")
            app.connect(args[0])
            print_json({"endpoint": app.endpoint})
        elif command == "disconnect":
            app.disconnect()
            print_json({"connected": False})
        elif command == "config":
            print_json(app.config.as_dict())
        elif command == "log":
            print_json({"log": str(app.logger.path.resolve())})
        elif command == "sim":
            if not isinstance(app.transport, SimTransport):
                raise ValueError("sim commands require simulation transport")
            operation, raw_id, *values = args
            app.transport.control(operation, int(raw_id), [float(v) for v in values] if operation == "move" else values)
            print_json({"sim": operation, "target": int(raw_id)})
        elif command == "wait":
            duration = float(args[0])
            if not math.isfinite(duration) or not 0 <= duration <= 3600:
                raise ValueError("wait duration must be 0..3600 seconds")
            time.sleep(duration)
        elif command == "require-sim":
            if not isinstance(app.transport, SimTransport):
                raise ValueError("This script is simulation-only")
        else:
            raise ValueError(f"Unknown command {command}; type help")
        return True

    def list_vehicles(self):
        states, now = self.app.registry.snapshot(), time.monotonic()
        vehicles = []
        for n, state in sorted(states.items()):
            heartbeat_age, position_age = state.age(state.heartbeat_at, now), state.age(state.position_at, now)
            vehicles.append({"id": n, "mode": state.mode, "armed": state.armed, "landed_state": state.landed,
                             "position_local_ned": list(state.position) if state.position is not None else None,
                             "battery_pct": state.value("battery.percentRemaining"), "heartbeat_age": heartbeat_age,
                             "position_age": position_age, "stale": heartbeat_age is None or position_age is None or
                             heartbeat_age > self.app.config.heartbeat_timeout or position_age > self.app.config.state_timeout})
        print_json({"vehicles": vehicles})


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "replay":
        parser = argparse.ArgumentParser(description="Read JSONL logs without connecting to vehicles")
        parser.add_argument("path")
        parser.add_argument("--speed", type=float, default=0)
        parser.add_argument("--kind", action="append", help="Filter events by kind, repeatable")
        options = parser.parse_args(argv[1:])
        try:
            for event in replay(options.path, options.speed):
                if not options.kind or event["kind"] in options.kind:
                    print_json(event)
            return 0
        except (OSError, ValueError) as exc:
            print_json({"error": str(exc)})
            return 1
    parser = argparse.ArgumentParser(description="PX4 centralized leader-follower CLI (default: simulation)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sim", action="store_true", help="Use built-in kinematic simulator")
    mode.add_argument("--connect", metavar="URI", help="MAVLink UDP/TCP endpoint")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--script", type=Path, help="Run commands from file; stop on first error")
    parser.add_argument("--execute", action="append", default=[], help="Execute command, repeatable")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--plots", action="store_true", help="Open the optional live telemetry plot window")
    args = parser.parse_args(argv)
    app = None
    try:
        config = Config.load(args.config) if args.config else Config.default(simulation=not bool(args.connect))
        app = App(config, args.log_dir)
        app.connect(args.connect or "sim")
        if not args.connect:
            app.wait_for_vehicles(len(config.drones))
            time.sleep(0.1)
        print_json({"endpoint": app.endpoint, "log": str(app.logger.path.resolve())})
        shell = Shell(app)
        if args.plots:
            print_json({"plots": app.plots.open()})
        if args.script:
            commands = args.script.read_text(encoding="utf-8").splitlines()
        else:
            commands = args.execute
        if args.script or args.execute:
            for line in commands:
                if not shell.execute(line):
                    break
            return 0
        print("Python Multi-Drone CLI — help để xem lệnh, quit để thoát.")
        while True:
            try:
                line = input("fleet> ")
            except (EOFError, KeyboardInterrupt):
                break
            try:
                if not shell.execute(line):
                    break
            except KeyboardInterrupt:
                print_json({"interrupted": True})
                if app.coordinator:
                    app.coordinator.stop("Operator interrupt")
            except Exception as exc:
                print_json({"error": str(exc)})
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print_json({"error": str(exc)})
        return 1
    finally:
        if app:
            app.close()
