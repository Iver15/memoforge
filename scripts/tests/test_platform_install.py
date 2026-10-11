"""Installation only succeeds after native readback; no shell expansion."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install_platform import install_platform  # noqa: E402


class PlatformInstallTest(unittest.TestCase):
    def test_unknown_platform_cannot_install(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as runner:
            with self.assertRaisesRegex(ValueError, "invalid_platform"):
                install_platform("missing", Path(tmp))
            runner.assert_not_called()

    def test_cli_failure_is_not_success(self):
        failure = subprocess.CompletedProcess([], 1, "", "failed")
        with patch("subprocess.run", return_value=failure):
            self.assertFalse(install_platform("codex", Path("/local"))["ok"])

    def test_readback_must_match_plugin(self):
        ok = subprocess.CompletedProcess([], 0, "{}", "")
        with patch("subprocess.run", return_value=ok):
            result = install_platform("codex", Path("/local"))
            self.assertFalse(result["ok"])
            self.assertIn("plugin_readback_mismatch", result["errors"])

    def test_reinstall_preserves_other_plugins(self):
        items = {
            "installed": [
                {"name": "unrelated", "enabled": True},
                {"name": "memoforge-codex", "version": "2.1.0", "enabled": True, "installPath": "/own/codex"},
            ]
        }
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(items) if "list" in argv else "{}", "")

        with patch("subprocess.run", side_effect=runner):
            self.assertTrue(install_platform("codex", Path("/local"))["ok"])
            self.assertTrue(install_platform("codex", Path("/local"))["ok"])
        self.assertFalse(any("remove" in a or "uninstall" in a for a in calls))

    def test_zcode_reinstall_refreshes_same_version_cache(self):
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            rows = [{"name": "memoforge-zcode", "version": "2.1.0", "enabled": True, "rootPath": "/own/zcode"}]
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows) if "list" in argv else "{}", "")

        with patch("subprocess.run", side_effect=runner):
            self.assertTrue(install_platform("zcode", Path("/local"))["ok"])
        self.assertTrue(any("update" in a for a in calls))

    def test_timeout_keeps_diagnostics(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["codex"], 10)):
            result = install_platform("codex", Path("/local"))
            self.assertFalse(result["ok"])
            self.assertIn("timeout", result["errors"][0])


class RuntimePathRegressionTest(unittest.TestCase):
    def test_missing_installed_path_is_not_success(self):
        rows = {
            "installed": [
                {
                    "name": "memoforge-codex",
                    "version": "2.1.0",
                    "enabled": True,
                    "pluginId": "memoforge-codex@memoforge-codex-local",
                }
            ]
        }

        def runner(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows) if "list" in argv else "{}", "")

        with patch("subprocess.run", side_effect=runner):
            result = install_platform("codex", Path("/local"))
        self.assertFalse(result["ok"])
        self.assertIn("missing_installed_path", result["errors"])
