import importlib.util
import socket
import unittest

from support import Registry, Telemetry


@unittest.skipUnless(importlib.util.find_spec("pymavlink"), "Install .[mavlink] to test real MAVLink wire")
class WireTests(unittest.TestCase):
    def test_udp_v2_multiple_sysids_and_explicit_setpoint_target(self):
        from pymavlink.dialects.v20 import common
        from multi_drone.connection import MavlinkTransport
        from multi_drone.models import Target, Vec3
        reserve = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
        reserve.close()
        transport = MavlinkTransport(f"udpin:127.0.0.1:{port}")
        self.addCleanup(transport.close)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(2)
        self.addCleanup(peer.close)

        class Writer:
            def write(self, data):
                peer.sendto(data, ("127.0.0.1", port))

        sender = common.MAVLink(Writer(), srcSystem=1, srcComponent=1)
        registry, telemetry = Registry(), None
        telemetry = Telemetry(registry)
        for n in (1, 2):
            sender.srcSystem = n
            sender.heartbeat_send(2, 12, 129, 4 << 16 | 3 << 24, 4)
            packet = transport.receive(1)
            self.assertIsNotNone(packet)
            telemetry.ingest(packet, 10.)
        self.assertEqual(set(registry.snapshot()), {1, 2})
        transport.setpoint(Target(2, Vec3(4, -2, -3)), 1)
        data, _ = peer.recvfrom(4096)
        self.assertEqual(data[0], 0xFD)
        parser = common.MAVLink(None)
        message = parser.parse_char(data)
        self.assertEqual(message.target_system, 2)
        self.assertEqual(message.target_component, 1)
        self.assertEqual(message.coordinate_frame, 1)
        self.assertEqual((message.x, message.y, message.z), (4., -2., -3.))
        self.assertEqual(message.type_mask & 7, 0)
        self.assertEqual(message.type_mask & 56, 56)
