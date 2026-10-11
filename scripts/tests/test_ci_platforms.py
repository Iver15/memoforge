"""The standalone package CI report must survive a Windows ANSI console."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class PlatformCiOutputTest(unittest.TestCase):
    def test_russian_docx_paths_are_reported_on_cp1252_console(self):
        root = Path(__file__).resolve().parents[2]
        run = subprocess.run(
            [sys.executable, str(root / "scripts/ci/check_platforms.py")],
            cwd=root,
            env=dict(os.environ, PYTHONIOENCODING="cp1252:strict"),
            capture_output=True,
            text=True,
            encoding="ascii",
            timeout=180,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        reports = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
        self.assertEqual([report["platform"] for report in reports], ["codex", "zcode"])
        for report in reports:
            self.assertTrue(report["ok"])
            self.assertEqual(report["phase"], "done")
            self.assertEqual(report["evidence"], "offline_fixture")
            self.assertIn("Проба с пробелами", report["docx"])
