"""Tests for scripts/memoforge/pylauncher.py — plugin_data_dir chain and discovery (ТЗ §2.5, §5.6)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import limits, pylauncher, schema  # noqa: E402

ENV_KEYS = (
    "CLAUDE_PLUGIN_DATA",
    "LOCALAPPDATA",
    "HOME",
    "USERPROFILE",
    "HOMEPATH",
    "HOMEDRIVE",
    "CLAUDE_PLUGIN_ROOT",
)


def clean_env(**overrides: str):
    """patch.dict that first removes every variable of the chain."""
    environment = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
    environment.update(overrides)
    return mock.patch.dict(os.environ, environment, clear=True)


class PluginRootTest(unittest.TestCase):
    def test_plugin_root_holds_the_package(self):
        root = pylauncher.plugin_root()
        self.assertTrue((root / "scripts" / "memoforge" / "pylauncher.py").is_file())
        self.assertTrue((root / "schemas" / "state.schema.json").is_file())

    def test_env_override_is_ignored_when_it_is_not_a_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(CLAUDE_PLUGIN_ROOT=tmp):
                self.assertEqual(pylauncher.plugin_root(), PLUGIN_ROOT)

    def test_env_override_is_used_when_it_holds_the_package(self):
        with clean_env(CLAUDE_PLUGIN_ROOT=str(PLUGIN_ROOT)):
            self.assertEqual(pylauncher.plugin_root(), PLUGIN_ROOT.absolute())


class PluginDataDirChainTest(unittest.TestCase):
    def test_claude_plugin_data_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(CLAUDE_PLUGIN_DATA=tmp, LOCALAPPDATA=tmp + "-local"):
                chain = pylauncher.plugin_data_dir_candidates()
                self.assertEqual(chain[0]["source"], "claude_plugin_data")
                self.assertEqual(chain[0]["path"], Path(tmp))
                self.assertEqual(pylauncher.plugin_data_dir(), Path(tmp))

    def test_localappdata_is_the_windows_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(LOCALAPPDATA=tmp):
                chain = pylauncher.plugin_data_dir_candidates()
                self.assertEqual([row["source"] for row in chain], ["localappdata", "plugin_root"])
                self.assertEqual(
                    chain[0]["path"], Path(tmp) / "claude" / "plugin-data" / "memoforge"
                )

    def test_home_is_used_without_localappdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env():
                with mock.patch.object(pylauncher, "home_dir", return_value=Path(tmp)):
                    chain = pylauncher.plugin_data_dir_candidates()
                    self.assertEqual([row["source"] for row in chain], ["home", "plugin_root"])
                    self.assertEqual(
                        chain[0]["path"], Path(tmp) / ".claude" / "plugin-data" / "memoforge"
                    )

    def test_plugin_root_data_is_the_last_link(self):
        with clean_env():
            chain = pylauncher.plugin_data_dir_candidates()
            self.assertEqual(chain[-1]["source"], "plugin_root")
            self.assertEqual(chain[-1]["path"], pylauncher.plugin_root() / ".data")

    def test_empty_home_falls_back_to_plugin_root(self):
        with clean_env(HOME="", USERPROFILE=""):
            with mock.patch.object(pylauncher, "home_dir", return_value=None):
                chain = pylauncher.plugin_data_dir_candidates()
                self.assertEqual([row["source"] for row in chain], ["plugin_root"])

    def test_empty_home_still_resolves_to_a_writable_absolute_dir(self):
        with clean_env(HOME="", USERPROFILE=""):
            resolved = pylauncher.plugin_data_dir()
            self.assertTrue(resolved.is_absolute())
            self.assertTrue(resolved.is_dir())

    def test_unwritable_first_candidate_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "not-a-dir"
            blocker.write_text("file", encoding="utf-8")
            with clean_env(CLAUDE_PLUGIN_DATA=str(blocker), LOCALAPPDATA=str(Path(tmp) / "local")):
                resolved = pylauncher.plugin_data_dir()
                self.assertEqual(
                    resolved, Path(tmp) / "local" / "claude" / "plugin-data" / "memoforge"
                )

    def test_site_packages_is_under_the_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(CLAUDE_PLUGIN_DATA=tmp):
                self.assertEqual(pylauncher.site_packages_dir(), Path(tmp) / "site-packages")
                self.assertEqual(pylauncher.launcher_cache_path(), Path(tmp) / "launcher.json")


class HomeDirTest(unittest.TestCase):
    def test_none_when_home_cannot_be_determined(self):
        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("no home")):
            self.assertIsNone(pylauncher.home_dir())

    def test_none_for_a_relative_home(self):
        with mock.patch("pathlib.Path.home", return_value=Path(".")):
            self.assertIsNone(pylauncher.home_dir())


class PythonCmdTest(unittest.TestCase):
    def test_version_check_uses_the_limit(self):
        self.assertIn(
            f"({limits.PYTHON_MIN_VERSION[0]},{limits.PYTHON_MIN_VERSION[1]})",
            pylauncher.VERSION_CHECK,
        )
        self.assertEqual(pylauncher.PYTHON_CANDIDATES, (("python",), ("python3",), ("py", "-3")))

    def test_probe_rejects_a_missing_interpreter(self):
        self.assertFalse(pylauncher.probe_interpreter(["definitely-not-a-python-xyz"]))

    def test_probe_accepts_the_running_interpreter(self):
        self.assertTrue(pylauncher.probe_interpreter([sys.executable]))

    def test_discovery_writes_a_schema_valid_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(CLAUDE_PLUGIN_DATA=tmp):
                command = pylauncher.python_cmd(refresh=True)
                self.assertTrue(command)
                cache = Path(tmp) / "launcher.json"
                if command != [sys.executable]:
                    self.assertTrue(cache.is_file())
                    document = pylauncher.state_io.read_json(cache)
                    self.assertEqual(schema.validate(document, "internal"), [])
                    self.assertEqual(document["python_cmd"], command)

    def test_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(CLAUDE_PLUGIN_DATA=tmp):
                pylauncher.state_io.write_json_atomic(
                    Path(tmp) / "launcher.json",
                    {"schema_version": 1, "kind": "launcher", "python_cmd": [sys.executable]},
                )
                with mock.patch.object(pylauncher, "probe_interpreter") as probe:
                    self.assertEqual(pylauncher.python_cmd(), [sys.executable])
                    probe.assert_not_called()

    def test_corrupt_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(CLAUDE_PLUGIN_DATA=tmp):
                (Path(tmp) / "launcher.json").write_text("{not json", encoding="utf-8")
                self.assertTrue(pylauncher.python_cmd())


if __name__ == "__main__":
    unittest.main()
