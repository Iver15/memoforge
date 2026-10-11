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


class ReviewRegressionTest(unittest.TestCase):
    def test_inherited_claude_root_never_controls_native_config(self):
        import json

        source = Path(__file__).resolve().parents[2]
        for platform in ("codex", "zcode"):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                root = build_package(platform, base / "build")
                foreign = base / "foreign-claude-data"
                env = dict(
                    os.environ,
                    HOME=str(base / "home"),
                    USERPROFILE=str(base / "home"),
                    CLAUDE_PLUGIN_ROOT=str(source),
                    CLAUDE_PLUGIN_DATA=str(foreign),
                )
                for key in (
                    "LOCALAPPDATA",
                    "MEMOFORGE_PLATFORM",
                    "PLUGIN_ROOT",
                    "PLUGIN_DATA",
                    "ZCODE_PLUGIN_ROOT",
                    "ZCODE_PLUGIN_DATA",
                ):
                    env.pop(key, None)
                launcher = root / "scripts" / ("mf.cmd" if os.name == "nt" else "mf")
                run = subprocess.run(
                    [str(launcher), "config", "show"], env=env, capture_output=True, text=True, encoding="utf-8"
                )
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)
                self.assertNotIn(str(foreign), json.dumps(actual))

    def test_build_is_independent_of_ansi_default_encoding(self):
        from unittest.mock import patch

        read, write = Path.read_text, Path.write_text

        def ansi_read(path, *args, **kwargs):
            kwargs.setdefault("encoding", "cp1252")
            return read(path, *args, **kwargs)

        def ansi_write(path, data, *args, **kwargs):
            kwargs.setdefault("encoding", "cp1252")
            return write(path, data, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="Проба ") as tmp:
            with patch.object(Path, "read_text", ansi_read), patch.object(Path, "write_text", ansi_write):
                for platform in ("codex", "zcode"):
                    root = build_package(platform, Path(tmp))
                    self.assertEqual(validate_package(root), [])

    def test_cmd_profiles_are_local_to_each_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = {p: build_package(p, Path(tmp)) for p in ("codex", "zcode")}
            for p, root in roots.items():
                text = (root / "scripts/mf.cmd").read_text(encoding="utf-8")
                self.assertLess(text.index("setlocal"), text.index('set "MEMOFORGE_PLATFORM=' + p + '"'))
            if os.name == "nt":
                env = dict(
                    os.environ,
                    MF_TEST_CDEX=str(roots["codex"]),
                    MF_TEST_ZCODE=str(roots["zcode"]),
                    LOCALAPPDATA=str(Path(tmp) / "data"),
                )
                env.pop("MEMOFORGE_PLATFORM", None)
                script = Path(tmp) / "sequence.cmd"
                script.write_text(
                    '@echo off\ncall "%MF_TEST_CDEX%\\scripts\\mf.cmd" --version\n'
                    "if errorlevel 1 exit /b 1\nif defined MEMOFORGE_PLATFORM exit /b 1\n"
                    'call "%MF_TEST_ZCODE%\\scripts\\mf.cmd" --version\n'
                    "if errorlevel 1 exit /b 1\nif defined MEMOFORGE_PLATFORM exit /b 1\n",
                    encoding="ascii",
                )
                run = subprocess.run(["cmd", "/d", "/c", str(script)], env=env, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(run.stdout.count("2.1.0"), 2)
