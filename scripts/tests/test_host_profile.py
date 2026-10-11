"""Platform identity and isolation, independent of host credentials."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memoforge.host_profile import get_profile, data_candidates  # noqa: E402


class HostProfileTest(unittest.TestCase):
    def test_default_is_claude(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_profile(Path(tmp)).name, "claude")

    def test_unknown_platform_is_rejected(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"MEMOFORGE_PLATFORM": "missing"}, clear=True),
        ):
            with self.assertRaisesRegex(ValueError, "invalid_platform"):
                get_profile(Path(tmp))

    def test_package_and_environment_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memoforge-platform.json").write_text(json.dumps({"platform": "codex"}))
            with patch.dict(os.environ, {"MEMOFORGE_PLATFORM": "zcode"}, clear=True):
                with self.assertRaisesRegex(ValueError, "platform_mismatch"):
                    get_profile(root)

    def test_native_fallback_never_uses_claude_home(self):
        for name in ("codex", "zcode"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                env = {
                    "MEMOFORGE_PLATFORM": name,
                    "HOME": tmp,
                    "CLAUDE_PLUGIN_DATA": tmp + "/.claude/plugin-data/memoforge",
                }
                with patch.dict(os.environ, env, clear=True):
                    chain = data_candidates(get_profile(Path(tmp)), Path(tmp))
                    self.assertEqual(chain[0]["path"], Path(tmp) / ("." + name) / "plugin-data" / ("memoforge-" + name))
                    self.assertFalse(any(".claude" in str(c["path"]) for c in chain))
                with patch.dict(os.environ, {"MEMOFORGE_PLATFORM": name, "LOCALAPPDATA": tmp}, clear=True):
                    self.assertEqual(
                        data_candidates(get_profile(Path(tmp)), Path(tmp))[0]["path"],
                        Path(tmp) / name / "plugin-data" / ("memoforge-" + name),
                    )

    def test_native_host_data_and_compatible_data_are_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "MEMOFORGE_PLATFORM": "codex",
                "PLUGIN_DATA": tmp + "/native",
                "CLAUDE_PLUGIN_DATA": tmp + "/other",
                "HOME": tmp,
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(data_candidates(get_profile(root), root)[0]["path"], root / "native")
            env.pop("PLUGIN_DATA")
            env["CLAUDE_PLUGIN_ROOT"] = str(root)
            (root / "memoforge-platform.json").write_text('{"platform":"codex"}')
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(data_candidates(get_profile(root), root)[0]["path"], root / "other")


class NativeWrapperTest(unittest.TestCase):
    def test_wrapper_and_python_data_chain_agree(self):
        import subprocess
        from memoforge import pylauncher

        launcher = Path(__file__).resolve().parents[1] / "mf"
        for platform in ("codex", "zcode"):
            with tempfile.TemporaryDirectory(prefix="Проба с пробелами ") as tmp:
                env = dict(
                    os.environ, MEMOFORGE_PLATFORM=platform, HOME=tmp, CLAUDE_PLUGIN_DATA=tmp + "/.claude/foreign"
                )
                for key in ("LOCALAPPDATA", "PLUGIN_DATA", "ZCODE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT"):
                    env.pop(key, None)
                with patch.dict(os.environ, env, clear=True):
                    expected = pylauncher.plugin_data_dir()
                    result = subprocess.run(
                        ["bash", str(launcher), "--print-plugin-data-dir"], env=env, capture_output=True, text=True
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(Path(result.stdout.strip()), expected)


class NativeInterpreterTest(unittest.TestCase):
    def test_native_discovery_cache_uses_absolute_interpreter(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MEMOFORGE_PLATFORM="codex", PLUGIN_DATA=tmp)
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            launcher = Path(__file__).resolve().parents[1] / "mf"
            result = subprocess.run(["bash", str(launcher), "--version"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            command = json.loads((Path(tmp) / "launcher.json").read_text())["python_cmd"]
            self.assertTrue(Path(command[0]).is_absolute(), command)

    def test_native_dependencies_require_successful_import(self):
        from memoforge import deps

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MEMOFORGE_PLATFORM": "codex"}):
            root = Path(tmp) / "jsonschema"
            root.mkdir()
            (root / "__init__.py").write_text("raise ImportError('incompatible binary dependency')\n")
            row = deps.check_modules(tmp)["jsonschema"]
            self.assertFalse(row["installed"])
