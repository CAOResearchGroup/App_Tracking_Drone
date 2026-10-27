import unittest

from support import Packet, Registry, Telemetry, heartbeat
from multi_drone.notifications import HISTORY_LIMIT


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.telemetry = Telemetry(self.registry)
        for n in (1, 2):
            self.telemetry.ingest(heartbeat(n), 10.)

    def send(self, text, *, n=1, severity=3, message_id=0, chunk=0, now=11.):
        self.telemetry.ingest(Packet(n, 1, "STATUSTEXT", {
            "severity": severity, "text": text, "id": message_id, "chunk_seq": chunk}), now)

    def messages(self, n=1):
        return self.registry.snapshot()[n].status_messages

    def test_severity_history_and_empty_message_do_not_erase_reason(self):
        self.send(b"Arming denied: position invalid\0")
        self.send(b"\0")
        message = self.messages()[0]
        self.assertEqual(message["level"], "ERROR")
        self.assertEqual(message["text"], "Arming denied: position invalid")
        self.assertEqual(message["system_id"], 1)
        self.assertEqual(message["received_at"], 11.)
        self.assertTrue(message["complete"])
        self.assertEqual(self.registry.snapshot()[1].last_status, message["text"])

    def test_chunked_utf8_reassembled_before_decoding_and_isolated_by_drone(self):
        raw = ("A"*49+"ế — calibration required").encode()
        self.send(raw[:50], message_id=8)
        self.send(b"B"*50, n=2, message_id=8)
        self.assertEqual(self.messages(), [])
        self.send(raw[:50], message_id=8)  # duplicate must not duplicate content
        self.send(raw[50:], message_id=8, chunk=1)
        self.send(b" end", n=2, message_id=8, chunk=1)
        self.assertEqual(self.messages()[0]["text"], raw.decode())
        self.assertEqual(self.messages(2)[0]["text"], "B"*50+" end")

    def test_gap_timeout_and_orphan_are_not_reported_as_complete(self):
        self.send(b"orphan", message_id=3, chunk=1)
        self.assertEqual(self.messages(), [])
        self.send(b"A"*50, message_id=3)
        self.send(b"tail", message_id=3, chunk=2)
        self.assertFalse(self.messages()[-1]["complete"])
        self.send(b"B"*50, message_id=4)
        self.telemetry.tick(14.)
        self.assertFalse(self.messages()[-1]["complete"])
        self.assertIn("thiếu đoạn", self.messages()[-1]["text"])

    def test_history_and_pending_assemblies_are_bounded(self):
        for i in range(200):
            self.send(f"Warning {i}", severity=4)
            self.send(b"A"*50, message_id=i+1)
        self.assertEqual(len(self.messages()), HISTORY_LIMIT)
        self.assertEqual(self.messages()[-1]["sequence"], 200)
        self.assertLessEqual(len(self.telemetry.status_text.pending), 64)

    def test_control_characters_and_invalid_severity(self):
        self.send("Warning\n\x1b[31mtest\t")
        self.assertNotIn("\x1b", self.messages()[0]["text"])
        self.assertNotIn("\n", self.messages()[0]["text"])
        with self.assertRaises(ValueError):
            self.send("invalid", severity=99)

    def test_full_length_legacy_and_empty_final_chunk(self):
        self.send("A"*50)
        self.assertEqual(len(self.messages()), 1)
        self.send(b"B"*50, message_id=1)
        self.send(b"\0", message_id=1, chunk=1)
        self.assertTrue(self.messages()[-1]["complete"])
        self.assertEqual(self.messages()[-1]["text"], "B"*50)
