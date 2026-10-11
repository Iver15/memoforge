"""Packages must work after extraction outside the repository."""

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_platforms import build_package, validate_package  # noqa: E402


class PlatformPackagesTest(unittest.TestCase):
    def test_package_runs_outside_checkout(self):
        for platform in ("codex", "zcode"):
            with tempfile.TemporaryDirectory(prefix="Проба с пробелами ") as tmp:
                root = build_package(platform, Path(tmp))
                self.assertEqual(validate_package(root), [])
                env = dict(os.environ, HOME=tmp)
                for key in (
                    "MEMOFORGE_PLATFORM",
                    "CLAUDE_PLUGIN_ROOT",
                    "CLAUDE_PLUGIN_DATA",
                    "PLUGIN_DATA",
                    "ZCODE_PLUGIN_DATA",
                ):
                    env.pop(key, None)
                result = subprocess.run(
                    [sys.executable, str(root / "scripts/memoforge/__main__.py"), "--version"],
                    env=env,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("2.1.0", result.stdout)
                for name in (".git", ".data", "outputs", ".claude-plugin", "__pycache__"):
                    self.assertFalse((root / name).exists(), name)
                archive = next(Path(tmp).glob("*.zip"))
                with zipfile.ZipFile(archive) as z:
                    mf = z.getinfo(root.name + "/scripts/mf")
                    self.assertTrue((mf.external_attr >> 16) & 0o111)

    def test_missing_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_package("codex", Path(tmp))
            (root / "schemas/state.schema.json").unlink()
            self.assertTrue(validate_package(root))

    def test_symlink_outside_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_package("zcode", Path(tmp))
            (root / "escape").symlink_to(Path(tmp).parent)
            self.assertTrue(validate_package(root))

    def test_native_package_does_not_autoload_claude_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_package("zcode", Path(tmp))
            self.assertFalse((root / "hooks/hooks.json").exists())
