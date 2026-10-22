import math
import unittest

from support import Packet, Registry, Telemetry, heartbeat, local
from multi_drone.models import FACT_NAMES


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.decoder = Telemetry(self.registry)
        self.decoder.ingest(heartbeat(), 10.)

    @property
    def state(self):
        return self.registry.snapshot()[1]

    def test_all_52_fields_initially_unavailable_except_derived_flight_time(self):
        self.assertEqual(len(FACT_NAMES), 52)
        self.assertIsNone(self.state.value("localPosition.x"))
        self.assertIsNone(self.state.value("battery.percentRemaining"))

    def test_multivehicle_component_isolation(self):
        self.decoder.ingest(heartbeat(2), 10.)
        self.decoder.ingest(local(2, x=20), 11.)
        self.assertIsNone(self.state.position)
        self.assertFalse(self.decoder.ingest(Packet(1, 100, "LOCAL_POSITION_NED", local().data), 12.))
        self.assertEqual(self.registry.snapshot()[2].position.x, 20)

    def test_ignore_gcs_and_non_px4(self):
        self.assertFalse(self.decoder.ingest(heartbeat(2, type=6), 11.))
        self.assertFalse(self.decoder.ingest(heartbeat(3, autopilot=3), 11.))
        self.assertEqual(len(self.registry.snapshot()), 1)

    def test_old_or_duplicate_position_does_not_refresh_freshness(self):
        self.decoder.ingest(local(stamp=1000), 11.)
        self.assertFalse(self.decoder.ingest(local(stamp=900, x=99), 12.))
        self.assertFalse(self.decoder.ingest(local(stamp=1000, x=99), 13.))
        self.assertEqual(self.state.position_at, 11.)
        self.assertEqual(self.state.position.x, 0)

    def test_boot_reboot_invalidates_old_health_and_arming(self):
        self.decoder.ingest(local(stamp=10000), 11.)
        for stamp in (100, 200, 300):
            self.decoder.ingest(local(stamp=stamp), 12.)
        self.assertIsNone(self.state.heartbeat_at)
        self.assertIsNone(self.state.armed)
        self.assertIsNone(self.state.value("battery.percentRemaining"))

    def test_boot_counter_wrap(self):
        self.decoder.ingest(local(stamp=0xFFFFFFF0), 11.)
        self.assertTrue(self.decoder.ingest(local(stamp=20, x=2), 12.))
        self.assertEqual(self.state.position.x, 2)

    def test_degrees_and_full_precision_yaw(self):
        self.decoder.ingest(Packet(1, 1, "ATTITUDE", dict(time_boot_ms=5, roll=math.pi/4, pitch=0., yaw=0.123,
            rollspeed=0., pitchspeed=0., yawspeed=math.pi)), 11.)
        self.assertAlmostEqual(self.state.yaw, .123)
        self.assertAlmostEqual(self.state.value("vehicle.yaw"), math.degrees(.123))
        self.assertNotIn("vehicle.heading", self.state.facts)
        self.assertAlmostEqual(self.state.value("vehicle.roll"), 45.)
        self.assertAlmostEqual(self.state.value("vehicle.yawRate"), 180.)

    def test_negative_yaw_keeps_normalized_display_and_signed_control_angle(self):
        self.decoder.ingest(Packet(1, 1, "ATTITUDE", dict(time_boot_ms=5, roll=0., pitch=0., yaw=-math.pi/2,
            rollspeed=0., pitchspeed=0., yawspeed=0.)), 11.)
        self.assertAlmostEqual(self.state.yaw, -math.pi/2)
        self.assertAlmostEqual(self.state.value("vehicle.yaw"), 270.)

    def test_quaternion_and_invalid_quaternion(self):
        data = dict(time_boot_ms=1, q1=1., q2=0., q3=0., q4=0., rollspeed=0., pitchspeed=0., yawspeed=0.)
        self.decoder.ingest(Packet(1, 1, "ATTITUDE_QUATERNION", data), 11.)
        self.assertEqual(self.state.yaw, 0.)
        with self.assertRaises(ValueError):
            self.decoder.ingest(Packet(1, 1, "ATTITUDE_QUATERNION", {**data, "time_boot_ms": 2, "q1": 0.}), 12.)

    def test_gps_sentinels_are_not_zero(self):
        self.decoder.ingest(Packet(1, 1, "GPS_RAW_INT", dict(lat=107765000, lon=1067000000, eph=65535, epv=120,
            satellites_visible=255, fix_type=6)), 11.)
        self.assertIsNone(self.state.value("gps.hdop"))
        self.assertIsNone(self.state.value("gps.count"))
        self.assertAlmostEqual(self.state.value("gps.vdop"), 1.2)
        self.assertAlmostEqual(self.state.value("gps.lat"), 10.7765)

    def test_battery_identity_and_unit_conversion(self):
        data = dict(id=0, voltages=[4000, 4000, 4000]+[65535]*7, current_battery=250, battery_remaining=80,
                    current_consumed=300, temperature=2650, time_remaining=0, charge_state=1)
        self.decoder.ingest(Packet(1, 1, "BATTERY_STATUS", data), 11.)
        self.decoder.ingest(Packet(1, 1, "BATTERY_STATUS", {**data, "id": 1, "battery_remaining": 5}), 12.)
        self.assertEqual(self.state.value("battery.voltage"), 12.)
        self.assertEqual(self.state.value("battery.current"), 2.5)
        self.assertEqual(self.state.value("battery.percentRemaining"), 80)
        self.assertEqual(self.state.value("battery.temperature"), 26.5)
        self.assertIsNone(self.state.value("battery.timeRemaining"))
        self.assertEqual(self.state.batteries[1]["percentRemaining"], 5)

    def test_target_mask_does_not_create_false_position_feedback(self):
        self.decoder.ingest(Packet(1, 1, "POSITION_TARGET_LOCAL_NED", dict(coordinate_frame=1, type_mask=7,
            x=0., y=0., z=0., vx=1., vy=2., vz=3.)), 11.)
        self.assertIsNone(self.state.value("localPositionSetpoint.x"))
        self.assertEqual(self.state.value("localPositionSetpoint.vx"), 1.)

    def test_nonfinite_local_position_rejected(self):
        with self.assertRaises(ValueError):
            self.decoder.ingest(local(x=float("nan")), 11.)

    def test_estimator_flags(self):
        self.decoder.ingest(Packet(1, 1, "ESTIMATOR_STATUS", {"flags": 1 | 8 | 1024}), 11.)
        self.assertTrue(self.state.value("estimatorStatus.goodAttitudeEsimate"))
        self.assertTrue(self.state.value("estimatorStatus.goodHorizPosRelEstimate"))
        self.assertTrue(self.state.value("estimatorStatus.gpsGlitch"))
        self.assertFalse(self.state.value("estimatorStatus.goodHorizPosAbsEstimate"))
