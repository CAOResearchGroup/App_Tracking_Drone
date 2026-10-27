import time
import unittest

from support import FakeTransport, MemoryLog, Packet, Vec3, ready_state
from multi_drone.commands import CommandService
from multi_drone.config import Config
from multi_drone.models import Target
from multi_drone.parameters import ParameterService, encode_parameter, decode_parameter
from multi_drone.safety import Safety, SafetyError


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.service = CommandService(self.transport, MemoryLog())

    def ack(self, **updates):
        self.service.handle(Packet(2, 1, "COMMAND_ACK", {"command": 400, "result": 0, **updates}))

    def test_ack_is_targeted_and_in_progress_is_not_complete(self):
        future = self.service.send(2, 1, 400, [1.], "arm")
        self.ack(target_system=99)
        self.assertFalse(future.done())
        self.service.handle(Packet(3, 1, "COMMAND_ACK", {"command": 400, "result": 0}))
        self.assertFalse(future.done())
        self.ack(result=5, progress=30)
        self.assertFalse(future.done())
        self.ack()
        self.assertTrue(future.result()["acknowledged"])

    def test_duplicate_command_not_sent_twice(self):
        self.service.send(2, 1, 400, [1.])
        with self.assertRaises(RuntimeError):
            self.service.send(2, 1, 400, [1.])
        self.assertEqual(len(self.transport.sent), 1)

    def test_timeout_is_unknown_and_no_blind_retry(self):
        future = self.service.send(2, 1, 400, [1.])
        self.service.tick(time.monotonic()+10)
        with self.assertRaises(TimeoutError):
            future.result()
        self.assertEqual(len(self.transport.sent), 1)

    def test_denial_does_not_become_success(self):
        future = self.service.send(2, 1, 400, [1.])
        self.ack(result=2)
        self.assertFalse(future.result()["acknowledged"])

    def test_temporary_rejection_has_readable_reason(self):
        future = self.service.send(2, 1, 400, [1.], "arm")
        self.ack(result=1)
        self.assertEqual(future.result()["result_name"], "TEMPORARILY_REJECTED")
        self.assertIn("tạm thời", future.result()["error"])


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.service = ParameterService(FakeTransport(), MemoryLog())

    def packet(self, value=7, param_type=6, **updates):
        return Packet(2, 1, "PARAM_VALUE", {"param_id": "COUNT", "param_value": encode_parameter(value, param_type),
            "param_type": param_type, "param_index": 0, "param_count": 1, **updates})

    def test_bytewise_int32_including_negative(self):
        for value in (0, 7, -1, -100, 2147483647):
            self.assertEqual(decode_parameter(encode_parameter(value, 6), 6), value)

    def test_invalid_fractional_or_nonfinite(self):
        with self.assertRaises(ValueError):
            encode_parameter(1.5, 6)
        with self.assertRaises(ValueError):
            encode_parameter(float("nan"), 9)

    def test_write_mismatch_is_failure(self):
        future = self.service.request(2, 1, "set", "COUNT", 1000000000, 6)
        self.service.handle(self.packet(1000000001))
        with self.assertRaises(ValueError):
            future.result()

    def test_parameter_isolation_and_id_nul(self):
        future = self.service.request(2, 1, "get", "COUNT")
        self.service.handle(Packet(3, 1, "PARAM_VALUE", self.packet().data))
        self.assertFalse(future.done())
        self.service.handle(self.packet(param_id=b"COUNT\0"))
        self.assertEqual(future.result()["value"], 7)

    def test_missing_list_item_does_not_report_complete(self):
        future = self.service.request(2, 1, "list")
        self.service.handle(self.packet(param_count=2))
        self.assertFalse(future.done())
        self.service.tick(time.monotonic()+10)
        with self.assertRaises(TimeoutError):
            future.result()

    def test_list_completes_after_all_indices(self):
        future = self.service.request(2, 1, "list")
        self.service.handle(self.packet(param_count=2))
        self.service.handle(self.packet(param_id="OTHER", param_count=2, param_index=1))
        self.assertEqual(set(future.result()), {"COUNT", "OTHER"})

    def test_parameter_name_cannot_collide_with_list_request_key(self):
        future = self.service.request(2, 1, "list")
        self.service.handle(self.packet(param_id="*", param_count=2))
        self.assertFalse(future.done())
        self.service.handle(self.packet(param_id="OTHER", param_count=2, param_index=1))
        self.assertEqual(set(future.result()), {"*", "OTHER"})


    def test_unsolicited_parameter_flood_does_not_allocate_cache(self):
        for i in range(1000):
            self.service.handle(self.packet(param_id=f"P{i}"))
        self.assertEqual(self.service.cache, {})

    def test_requested_cache_is_bounded_across_names_and_vehicles(self):
        self.service = ParameterService(FakeTransport(), MemoryLog(), max_parameters=2, max_vehicles=2)
        for vehicle in range(1, 5):
            for i in range(4):
                name = f"P{i}"
                future = self.service.request(vehicle, 1, "get", name)
                self.service.handle(Packet(vehicle, 1, "PARAM_VALUE", self.packet(param_id=name).data))
                self.assertEqual(future.result()["value"], 7)
        self.assertEqual(len(self.service.cache), 2)
        self.assertTrue(all(len(values) == 2 for values in self.service.cache.values()))

    def test_inconsistent_list_metadata_fails_and_releases_state(self):
        for update in ({"param_count": 65535}, {"param_index": 2},
                       {"param_id": "OTHER"}, {"param_index": 1}, {"param_count": 1}):
            with self.subTest(update=update):
                future = self.service.request(2, 1, "list")
                self.service.handle(self.packet(param_count=2))
                self.service.handle(self.packet(**{"param_count": 2, **update}))
                with self.assertRaises(ValueError):
                    future.result(timeout=.1)
                self.assertEqual(self.service.lists, {})

    def test_new_list_excludes_stale_cache_and_allows_duplicates(self):
        self.service.request(2, 1, "get", "STALE")
        self.service.handle(self.packet(param_id="STALE"))
        future = self.service.request(2, 1, "list")
        self.service.handle(self.packet(param_count=2))
        self.service.handle(self.packet(param_count=2))
        self.assertFalse(future.done())
        self.service.handle(self.packet(param_id="OTHER", param_count=2, param_index=1))
        self.assertEqual(set(future.result()), {"COUNT", "OTHER"})
        self.assertEqual(self.service.lists, {})

    def test_list_timeout_cleans_up_and_late_responses_are_ignored(self):
        future = self.service.request(2, 1, "list")
        self.service.tick(time.monotonic()+10)
        self.assertIsInstance(future.exception(), TimeoutError)
        self.service.handle(self.packet())
        self.assertEqual(self.service.lists, {})
        self.assertEqual(self.service.cache, {})


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.config = Config.default()
        self.safety = Safety(self.config)
        self.now = time.monotonic()
        self.states = {1: ready_state(1, Vec3(0, 0, -3), self.now),
                       2: ready_state(2, Vec3(-3, -2, -3), self.now), 3: ready_state(3, Vec3(-3, 2, -3), self.now)}

    def test_reject_duplicate_identity(self):
        self.config.drones[1].system_id = 1
        with self.assertRaises(ValueError):
            self.config.__post_init__()

    def test_reject_unknown_common_frame(self):
        self.config.frame_confirmed = False
        with self.assertRaises(SafetyError):
            self.safety.fleet(self.states, [1, 2, 3], self.now)

    def test_origin_translation(self):
        self.config.drones[1].origin = Vec3(10, 0, 0)
        self.assertEqual(self.safety.common_position(self.states[2]), Vec3(7, -2, -3))

    def test_stale_position_even_when_heartbeat_alive(self):
        self.states[1].position_at -= 2
        with self.assertRaisesRegex(SafetyError, "position"):
            self.safety.fleet(self.states, [1, 2, 3], self.now)

    def test_unknown_or_low_battery(self):
        for value in (None, 5):
            self.states[1].set_fact("battery.percentRemaining", value, self.now)
            with self.assertRaises(SafetyError):
                self.safety.fleet(self.states, [1, 2, 3], self.now)

    def test_airborne_required(self):
        self.states[2].landed = 1
        with self.assertRaises(SafetyError):
            self.safety.fleet(self.states, [1, 2, 3], self.now)

    def test_colliding_targets_rejected(self):
        targets = {2: Target(2, Vec3(-3, 0, -3)), 3: Target(3, Vec3(-3, 0, -3))}
        with self.assertRaisesRegex(SafetyError, "Separation"):
            self.safety.targets(targets, self.states, 1, .05)

    def test_velocity_and_altitude_limits(self):
        with self.assertRaises(SafetyError):
            self.safety.targets({2: Target(2, velocity=Vec3(100, 0, 0))}, self.states, 1, .05)
        with self.assertRaises(SafetyError):
            self.safety.targets({2: Target(2, Vec3(-3, -2, 1))}, self.states, 1, .05)

    def test_nonfinite_vector(self):
        with self.assertRaises(ValueError):
            Vec3(float("nan"), 0, 0)
