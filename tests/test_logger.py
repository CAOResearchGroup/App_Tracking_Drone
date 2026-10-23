import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import wait_until
from multi_drone.logger import EventLog, replay


class LogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def log(self, **limits):
        log = EventLog(self.directory.name, **limits)
        self.addCleanup(log.close)
        return log

    def test_rotation_bounds_files_and_preserves_complete_json(self):
        log = self.log(max_bytes=512, max_files=3)
        for i in range(50):
            log.write("sample", index=i, text="Việt Nam")
        log.close()
        paths = list(Path(self.directory.name).glob("*.jsonl"))
        self.assertEqual(len(paths), 3)
        for path in paths:
            self.assertLessEqual(path.stat().st_size, 512)
            self.assertTrue(list(replay(path)))
        self.assertEqual(list(replay(log.path))[-1]["index"], 49)

    def test_disk_full_does_not_escape_to_producer(self):
        with patch.object(EventLog, "_write_line", side_effect=OSError("disk full")):
            log = self.log()
            log.write("sample")
            self.assertFalse(log.flush())
            self.assertIn("disk full", log.status()["error"])
            log.write("failsafe_request")
            self.assertGreaterEqual(log.status()["dropped"], 2)

    def test_blocked_disk_and_full_queue_do_not_block_producer(self):
        entered, release = threading.Event(), threading.Event()
        original = EventLog._write_line

        def blocked(log, line):
            entered.set()
            release.wait(5)
            original(log, line)

        with patch.object(EventLog, "_write_line", blocked):
            log = self.log(queue_size=2)
            try:
                log.write("first")
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                for i in range(100):
                    log.write("sample", index=i)
                self.assertLess(time.monotonic()-start, .5)
                self.assertEqual(log.status()["queued"], 2)
                self.assertEqual(log.status()["dropped"], 98)
            finally:
                release.set()
                log.close()

    def test_oversize_record_dropped_and_next_record_written(self):
        log = self.log(max_bytes=512)
        log.write("huge", value="x"*1024)
        log.write("small")
        self.assertTrue(log.flush())
        self.assertEqual(log.status()["dropped"], 1)
        self.assertEqual([e["kind"] for e in replay(log.path)], ["small"])

    def test_unwritable_directory_disables_logging_without_crashing(self):
        path = Path(self.directory.name) / "not-a-directory"
        path.write_text("occupied")
        log = EventLog(path)
        self.addCleanup(log.close)
        wait_until(lambda: log.last_error is not None)
        log.write("failsafe_request")
        self.assertFalse(log.flush())
