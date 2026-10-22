import json
import subprocess
import sys
import tempfile
import unittest

from support import ROOT


class CliTests(unittest.TestCase):
    def test_group_arm_immediately_followed_by_takeoff(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "run_cli.py"), "--sim", "--log-dir", directory,
                "--execute", "arm all", "--execute", "takeoff all 3"], cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            outputs = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(outputs[-1]["status"], "accepted")
            self.assertEqual(len(outputs[-1]["results"]), 3)

    def test_script_error_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "run_cli.py"), "--sim", "--log-dir", directory,
                "--execute", "formation start"], cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not armed", result.stdout)
