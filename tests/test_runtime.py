import tempfile
import time
import unittest
from unittest.mock import patch

from support import ROOT, Vec3, wait_until, Packet
from multi_drone.algorithm_runtime import AlgorithmRunner
from multi_drone.app import App
from multi_drone.config import Config
from multi_drone.logger import replay


class AlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.runner = AlgorithmRunner()
        self.addCleanup(self.runner.close)
        self.context = {"leader": {"position": [1, 2, -3]}, "followers": [{"id": 2, "offset": [-3, 0, 0]}]}

    def test_builtin_and_loaded_algorithm_agree(self):
        expected = self.runner.step(self.context, .2)
        self.runner.load(ROOT / "algorithms/python/offset_follow.py")
        self.assertEqual(self.runner.step(self.context, .2), expected)

    def test_wrong_member_ids_rejected(self):
        self.runner.load(ROOT / "tests/fixtures/bad_algorithm.py")
        with self.assertRaises(ValueError):
            self.runner.step(self.context, .2)

    def test_nan_output_rejected(self):
        self.runner.load(ROOT / "tests/fixtures/nan_algorithm.py")
        with self.assertRaises(ValueError):
            self.runner.step(self.context, .2)

    def test_hanging_algorithm_is_terminated(self):
        self.runner.load(ROOT / "tests/fixtures/hang_algorithm.py")
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.runner.step(self.context, .05)
        self.assertLess(time.monotonic()-start, 1)
        self.assertIsNone(self.runner.process)
        with self.assertRaises(RuntimeError):
            self.runner.step(self.context, .05)

    def test_m_file_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            self.runner.load(ROOT / "README.md")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = App(Config.default(), self.directory.name)
        self.addCleanup(self.app.close)
        self.app.connect("sim")
        self.app.wait_for_vehicles(3)
        wait_until(lambda: all(s.position is not None and s.landed is not None for s in self.app.registry.snapshot().values()))

    def airborne(self):
        with self.app.transport.lock:
            for drone in self.app.transport.drones.values():
                drone.armed, drone.landed = True, 2
                drone.position = Vec3(drone.position.x, drone.position.y, -3)
            self.app.transport.last_slow = -float("inf")
        wait_until(lambda: all(s.armed and s.landed == 2 and s.position.z == -3 for s in self.app.registry.snapshot().values()))

    def active(self):
        self.airborne()
        self.app.coordinator.start()
        self.assertEqual(self.app.coordinator.phase, "priming")
        wait_until(lambda: self.app.coordinator.phase == "active")

    def test_single_vehicle_commands_and_parameter_readback(self):
        self.assertEqual(self.app.parameter("get", 2, "MAV_SYS_ID")["value"], 2)
        self.assertEqual(self.app.parameter("set", 2, "MPC_XY_VEL_MAX", 4)["value"], 4.)
        self.assertEqual(self.app.command("arm", [2])["status"], "accepted")
        wait_until(lambda: self.app.registry.snapshot()[2].armed)
        with self.assertRaises(RuntimeError):
            self.app.parameter("set", 2, "MPC_XY_VEL_MAX", 5)
        self.assertEqual(self.app.command("takeoff", [2], 2)["status"], "accepted")
        wait_until(lambda: self.app.registry.snapshot()[2].landed == 2)
        self.assertEqual(self.app.command("disarm", [2])["status"], "failed")

    def test_position_mode_group_is_confirmed_without_arming(self):
        result = self.app.command("position", [2, 3])
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(all(r["state_confirmed"] for r in result["results"]))
        states = self.app.registry.snapshot()
        self.assertEqual(states[2].mode, "POSCTL")
        self.assertEqual(states[3].mode, "POSCTL")
        self.assertFalse(states[2].armed)
        self.assertEqual(states[1].mode, "HOLD")

    def test_position_denial_has_recent_px4_text_without_other_drone_or_old_errors(self):
        self.app.telemetry.ingest(Packet(2, 1, "STATUSTEXT", {"severity": 3, "text": "old error"}), time.monotonic()-10)
        self.app.telemetry.ingest(Packet(1, 1, "STATUSTEXT", {"severity": 3, "text": "other drone"}), time.monotonic())
        self.app.transport.control("reject", 2, ["on"])
        result = self.app.command("position", [2, 3])
        self.assertEqual(result["status"], "partial")
        failure = next(r for r in result["results"] if r["target"] == 2)
        self.assertEqual(failure["result_name"], "DENIED")
        self.assertEqual([m["text"] for m in failure["recent_messages"]], ["Simulator: command rejected"])
        self.assertEqual(self.app.messages([2])[-1]["source"], "APP_COMMAND")
        self.assertEqual(self.app.registry.snapshot()[2].last_status, "Simulator: command rejected")

    def test_position_ack_without_new_mode_heartbeat_is_not_confirmed(self):
        original = self.app.transport.command

        def ack_only(system_id, component_id, command, params):
            if command == 176:
                self.app.transport.incoming.put(Packet(system_id, 1, "COMMAND_ACK", {"command": 176, "result": 0}))
            else:
                original(system_id, component_id, command, params)

        with patch.object(self.app.transport, "command", side_effect=ack_only):
            result = self.app.command("position", [2])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["results"][0]["acknowledged"])
        self.assertFalse(result["results"][0]["state_confirmed"])

    def test_position_override_stops_formation_and_keeps_selected_mode(self):
        self.active()
        result = self.app.command("position", [2])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.app.coordinator.phase, "idle")
        self.assertEqual(self.app.registry.snapshot()[2].mode, "POSCTL")
        wait_until(lambda: self.app.registry.snapshot()[3].mode == "HOLD")

    def test_partial_group_command(self):
        self.app.transport.control("reject", 3, ["on"])
        result = self.app.command("arm", [2, 3])
        self.assertEqual(result["status"], "partial")
        self.assertEqual({r["target"]: r["acknowledged"] for r in result["results"]}, {2: True, 3: False})

    def test_formation_follow_then_fresh_heartbeat_stale_pose_stops_followers(self):
        self.active()
        self.app.transport.control("move", 1, [1., 0., -3.])
        wait_until(lambda: self.app.registry.snapshot()[2].position.x > -2.5)
        self.app.transport.control("freeze", 1, ["on"])
        wait_until(lambda: self.app.coordinator.phase == "idle", 2)
        self.assertIn("position", self.app.coordinator.reason)
        wait_until(lambda: all(self.app.registry.snapshot()[n].mode == "HOLD" for n in (2, 3)))
        self.assertEqual(self.app.registry.snapshot()[1].mode, "HOLD")
        self.assertLess(time.monotonic()-self.app.registry.snapshot()[1].heartbeat_at, 1.)

    def test_operator_takeover_not_overwritten(self):
        self.active()
        self.app.transport.control("reject", 2, ["off"])
        with self.app.transport.lock:
            self.app.transport.drones[2].mode = "LAND"
            self.app.transport.last_slow = -float("inf")
        wait_until(lambda: self.app.coordinator.phase == "idle")
        self.assertNotEqual(self.app.transport.drones[2].mode, "HOLD")

    def test_log_is_read_only_replay_and_contains_real_events(self):
        self.app.command("arm", [2])
        self.app.disconnect()
        events = list(replay(self.app.logger.path))
        self.assertTrue(any(e["kind"] == "command_ack" for e in events))
        self.assertTrue(any(e["kind"] == "telemetry" for e in events))
        self.assertTrue(all("utc" in e and "monotonic" in e for e in events))

    def test_leader_link_loss_stops_only_followers(self):
        self.active()
        self.app.transport.control("drop", 1, ["on"])
        wait_until(lambda: self.app.coordinator.phase == "idle", 2)
        wait_until(lambda: all(self.app.registry.snapshot()[n].mode == "HOLD" for n in (2, 3)))
        self.assertEqual(self.app.transport.drones[1].mode, "HOLD")

    def test_worker_hang_stops_formation_without_stopping_receive_loop(self):
        self.airborne()
        self.app.runner.load(ROOT / "tests/fixtures/hang_algorithm.py")
        self.app.coordinator.start()
        wait_until(lambda: self.app.coordinator.phase == "idle", 4)
        self.assertIn("deadline", self.app.coordinator.reason)
        wait_until(lambda: self.app.registry.snapshot()[2].mode == "HOLD")
        self.assertLess(time.monotonic()-self.app.registry.snapshot()[1].heartbeat_at, 1.)

    def test_disk_full_does_not_prevent_failsafe_or_kill_control_threads(self):
        self.active()
        with patch.object(self.app.logger, "_write_line", side_effect=OSError("disk full")):
            self.app.logger.write("force_failure")
            wait_until(lambda: self.app.logger.last_error is not None)
            self.app.transport.control("freeze", 1, ["on"])
            wait_until(lambda: self.app.coordinator.phase == "idle", 2)
            wait_until(lambda: all(self.app.registry.snapshot()[n].mode == "HOLD" for n in (2, 3)))
            self.assertTrue(all(thread.is_alive() for thread in self.app.threads))

    def test_disconnect_marks_last_state_unavailable(self):
        self.app.disconnect()
        self.assertIsNone(self.app.registry.snapshot()[1].heartbeat_at)
        self.assertIsNone(self.app.registry.snapshot()[1].position_at)
