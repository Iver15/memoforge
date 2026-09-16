"""Tests for scripts/mf and scripts/mf.cmd — launcher discovery wrappers (ТЗ §5.6, §2.5, M12)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, pylauncher, schema  # noqa: E402

MF_SH = PLUGIN_ROOT / "scripts" / "mf"
MF_CMD = PLUGIN_ROOT / "scripts" / "mf.cmd"

BASH = shutil.which("bash")
CYGPATH = shutil.which("cygpath") if os.name == "nt" else None
WINDIR = os.environ.get("SystemRoot", r"C:\Windows")

# Variables of the §2.5 chain; every test drives them explicitly.
CHAIN_KEYS = (
    "CLAUDE_PLUGIN_DATA",
    "LOCALAPPDATA",
    "HOME",
    "USERPROFILE",
    "HOMEPATH",
    "HOMEDRIVE",
    "CLAUDE_PLUGIN_ROOT",
)

VERSION_PROBE = "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)"
STORE_ALIAS_TEXT = "Python was not found"

# D-100: the skip reasons `scripts/ci/check_no_skips.py` allows. None of them is a missing
# dependency — they are conditions of the host. The Ubuntu CI job cannot run `mf.cmd` no matter
# what is installed (WINDOWS_ONLY_REASON), and a `windows-latest` runner may or may not carry the
# Windows Store `python3` alias (usually not) or `py.exe`. The guard repeats these literals instead
# of importing them; keep the copies in step.
WINDOWS_ONLY_REASON = "mf.cmd runs on Windows only"
STORE_ALIAS_MISSING_REASON = (
    "no Windows Store python3 alias on PATH; a .cmd shim cannot stand in for it, because "
    "cmd.exe hands control to a batch file without returning to mf.cmd"
)
PY_LAUNCHER_MISSING_REASON = "py.exe (PEP 397 launcher) is not installed"


def posix_toolchain_dir() -> Path | None:
    """Directory holding the coreutils `scripts/mf` needs (`sed`, `tr`, `mkdir`, `mv`, `rm`)."""
    if BASH is None:
        return None
    parent = Path(BASH).parent
    for candidate in (parent, parent.parent / "usr" / "bin"):
        if (candidate / "sed").exists() or (candidate / "sed.exe").exists():
            return candidate
    return None


TOOLCHAIN = posix_toolchain_dir()


def store_alias_dir() -> Path | None:
    """Directory of a real Windows Store `python3` alias — the P0 of §0.1 п.4, if installed here."""
    if os.name != "nt":
        return None
    found = shutil.which("python3")
    if not found:
        return None
    try:
        done = subprocess.run(
            [found, "-c", VERSION_PROBE], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 and STORE_ALIAS_TEXT in (done.stdout + done.stderr):
        return Path(found).parent
    return None


STORE_ALIAS_DIR = store_alias_dir()
WINDOWS_PY = shutil.which("py", path=WINDIR) if os.name == "nt" else None


def base_env(**overrides: str) -> dict:
    """Environment without the §2.5 chain variables and without an inherited PYTHONPATH."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in CHAIN_KEYS
        and key not in ("PYTHONPATH", "MEMOFORGE_OUTPUT_FOLDER", "CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER")
    }
    env.update(overrides)
    return env


def wrapper_env(data_dir: str) -> dict:
    return base_env(CLAUDE_PLUGIN_DATA=data_dir)


def run(command: list[str] | str, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd or PLUGIN_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def run_cmd(args: list[str], env: dict, wrapper: Path = MF_CMD, cwd: Path | None = None):
    """Run mf.cmd through `cmd /s /c "<full command line>"` (paths and args may hold spaces)."""
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    inner = subprocess.list2cmdline([str(wrapper)] + list(args))
    return run(f'"{comspec}" /s /c "{inner}"', env, cwd=cwd)


def run_sh(args: list[str], env: dict, wrapper: Path = MF_SH, cwd: Path | None = None):
    return run([BASH, str(wrapper).replace("\\", "/")] + list(args), env, cwd=cwd)


def native_path(text: str) -> Path:
    """Host path of a wrapper's output (Git Bash prints MSYS paths such as `/tmp/...`)."""
    text = text.strip()
    if os.name == "nt" and CYGPATH is not None and not re.match(r"^[A-Za-z]:", text):
        done = subprocess.run([CYGPATH, "-w", text], capture_output=True, text=True, timeout=60)
        if done.returncode == 0 and done.stdout.strip():
            text = done.stdout.strip()
    return Path(text)


def cached_python_cmd(data_dir: str) -> list:
    document = json.loads((Path(data_dir) / "launcher.json").read_text(encoding="utf-8-sig"))
    return document["python_cmd"]


def write_sh_shim(path: Path, body: str) -> None:
    path.write_bytes(("#!/bin/sh\n" + body).encode("utf-8"))
    os.chmod(path, 0o755)


def store_alias_shim(path: Path) -> None:
    """A `python3` that behaves like the Windows Store alias: exit 9009, «Python was not found»."""
    write_sh_shim(path, f'echo "{STORE_ALIAS_TEXT}; install it from the Microsoft Store" >&2\nexit 9009\n')


def interpreter_shim(path: Path, *, drop_dash_three: bool = False) -> None:
    executable = sys.executable.replace("\\", "/")
    prefix = 'if [ "$1" = "-3" ]; then shift; fi\n' if drop_dash_three else ""
    write_sh_shim(path, f'{prefix}exec "{executable}" "$@"\n')


class WrapperFilesTest(unittest.TestCase):
    def test_both_wrappers_exist(self):
        self.assertTrue(MF_SH.is_file())
        self.assertTrue(MF_CMD.is_file())

    def test_posix_wrapper_is_lf_only(self):
        self.assertNotIn(b"\r", MF_SH.read_bytes())

    def test_cmd_wrapper_is_crlf(self):
        raw = MF_CMD.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))

    def test_wrappers_declare_the_discovery_order(self):
        for path in (MF_SH, MF_CMD):
            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index("python3"), text.index("py -3"), path.name)
            self.assertIn("site-packages", text)
            self.assertIn("__main__.py", text)
            self.assertNotIn("python3 \"", text)

    def test_wrappers_read_the_discovery_cache(self):
        """§5.6: the cache exists to be read, not only written (finding 7)."""
        for path in (MF_SH, MF_CMD):
            text = path.read_text(encoding="utf-8")
            self.assertIn("launcher.json", text)
            self.assertIn("MF_FROM_CACHE", text, path.name)


class PluginDataDirIdentityTest(unittest.TestCase):
    """§2.5/§5.6: the wrappers and `pylauncher.plugin_data_dir()` must resolve the same directory."""

    def chain_cases(self, stack) -> list[tuple[str, dict]]:
        first = stack.enter_context(tempfile.TemporaryDirectory())
        second = stack.enter_context(tempfile.TemporaryDirectory())
        third = stack.enter_context(tempfile.TemporaryDirectory())
        return [
            ("claude_plugin_data", base_env(CLAUDE_PLUGIN_DATA=first)),
            ("localappdata", base_env(LOCALAPPDATA=second)),
            ("home", base_env(HOME=third, USERPROFILE=third)),
        ]

    def expected(self, env: dict) -> Path:
        with mock.patch.dict(os.environ, env, clear=True):
            return pylauncher.plugin_data_dir()

    def _check(self, runner):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for name, env in self.chain_cases(stack):
                with self.subTest(link=name):
                    result = runner(["--print-plugin-data-dir"], env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(native_path(result.stdout), self.expected(env))

    def _check_unwritable_first_link(self, runner):
        """Finding 4: a candidate that cannot be written to is skipped, not turned into `.data`."""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "not-a-dir"
            blocker.write_text("file", encoding="utf-8")
            env = base_env(CLAUDE_PLUGIN_DATA=str(blocker), LOCALAPPDATA=str(Path(tmp) / "local"))
            result = runner(["--print-plugin-data-dir"], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = Path(tmp) / "local" / "claude" / "plugin-data" / "memoforge"
            self.assertEqual(self.expected(env), expected)
            self.assertEqual(native_path(result.stdout), expected)

    @unittest.skipUnless(os.name == "nt", WINDOWS_ONLY_REASON)
    def test_cmd_wrapper_matches_pylauncher(self):
        self._check(run_cmd)

    @unittest.skipUnless(os.name == "nt", WINDOWS_ONLY_REASON)
    def test_cmd_wrapper_skips_an_unwritable_candidate(self):
        self._check_unwritable_first_link(run_cmd)

    @unittest.skipUnless(BASH is not None, "bash is not available")
    @unittest.skipUnless(
        os.name != "nt" or CYGPATH is not None,
        "cygpath is needed to compare the MSYS path printed by Git Bash with the host path",
    )
    def test_bash_wrapper_skips_an_unwritable_candidate(self):
        self._check_unwritable_first_link(run_sh)

    @unittest.skipUnless(BASH is not None, "bash is not available")
    @unittest.skipUnless(
        os.name != "nt" or CYGPATH is not None,
        "cygpath is needed to compare the MSYS path printed by Git Bash with the host path",
    )
    def test_bash_wrapper_matches_pylauncher(self):
        self._check(run_sh)


@unittest.skipUnless(os.name == "nt", WINDOWS_ONLY_REASON)
class CmdWrapperTest(unittest.TestCase):
    def test_task_list_returns_json_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            result = run_cmd(["task", "list", "--workdir", tmp], wrapper_env(data))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.strip())
            self.assertEqual(payload, {"tasks": [], "count": 0})

    def test_task_new_through_the_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            env = wrapper_env(data)
            env["MEMOFORGE_OUTPUT_FOLDER"] = tmp
            created = run_cmd(["task", "new", "--query", "GDPR biometrics"], env)
            self.assertEqual(created.returncode, 0, created.stderr)
            payload = json.loads(created.stdout.strip())
            self.assertEqual(payload["schema_version"], 2)
            self.assertTrue(Path(payload["work_dir"]).is_dir())

            listed = run_cmd(["task", "list", "--workdir", tmp], env)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout.strip())["count"], 1)

    def test_wrapper_writes_the_discovery_cache_where_pylauncher_reads_it(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            result = run_cmd(["task", "list", "--workdir", tmp], wrapper_env(data))
            self.assertEqual(result.returncode, 0, result.stderr)
            cache = Path(data) / "launcher.json"
            self.assertTrue(cache.is_file(), "mf.cmd must cache discovery in launcher.json")
            document = json.loads(cache.read_text(encoding="utf-8-sig"))
            self.assertEqual(schema.validate(document, "internal"), [])
            self.assertEqual(document["source"], "mf.cmd")
            self.assertTrue(document["python_cmd"])
            os.environ["CLAUDE_PLUGIN_DATA"] = data
            try:
                self.assertEqual(pylauncher.launcher_cache_path(), cache)
            finally:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)


@unittest.skipUnless(BASH is not None, "bash is not available")
class BashWrapperTest(unittest.TestCase):
    def test_task_list_returns_json_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            result = run_sh(["task", "list", "--workdir", tmp], wrapper_env(data))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.strip())
            self.assertEqual(payload, {"tasks": [], "count": 0})

    def test_unknown_command_exits_two(self):
        with tempfile.TemporaryDirectory() as data:
            result = run_sh(["task", "nope"], wrapper_env(data))
            self.assertEqual(result.returncode, 2)


class LauncherCacheReuseTest(unittest.TestCase):
    """§5.6: a usable cached interpreter is reused; a stale one triggers discovery and a rewrite."""

    def _seed(self, data: str, python_cmd: list[str]) -> Path:
        cache = Path(data) / "launcher.json"
        payload = {
            "schema_version": 1,
            "kind": "launcher",
            "python_cmd": python_cmd,
            "source": "pylauncher",
        }
        cache.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return cache

    def _check(self, runner, own_source: str):
        with tempfile.TemporaryDirectory() as data:
            cache = self._seed(data, [Path(sys.executable).stem])
            result = runner(["task", "list", "--workdir", data], wrapper_env(data))
            self.assertEqual(result.returncode, 0, result.stderr)
            kept = json.loads(cache.read_text(encoding="utf-8-sig"))
            self.assertEqual(kept["source"], "pylauncher", "a usable cache must not be rewritten")

        with tempfile.TemporaryDirectory() as data:
            cache = self._seed(data, ["mf-no-such-interpreter"])
            result = runner(["task", "list", "--workdir", data], wrapper_env(data))
            self.assertEqual(result.returncode, 0, result.stderr)
            rewritten = json.loads(cache.read_text(encoding="utf-8-sig"))
            self.assertEqual(rewritten["source"], own_source, "a stale cache must be rewritten")

    @unittest.skipUnless(os.name == "nt", WINDOWS_ONLY_REASON)
    def test_cmd_wrapper_reuses_the_cache(self):
        self._check(run_cmd, "mf.cmd")

    @unittest.skipUnless(BASH is not None, "bash is not available")
    def test_bash_wrapper_reuses_the_cache(self):
        self._check(run_sh, "mf")


@unittest.skipUnless(BASH is not None, "bash is not available")
@unittest.skipUnless(TOOLCHAIN is not None, "no POSIX toolchain (sed/tr/mkdir) next to bash")
class BashDiscoveryTest(unittest.TestCase):
    """§5.6 discovery matrix driven through a PATH that holds only the shims plus coreutils."""

    def shim_env(self, shims: Path, data: str) -> dict:
        env = wrapper_env(data)
        env["PATH"] = f"{shims}{os.pathsep}{TOOLCHAIN}"
        return env

    def test_a_store_alias_python3_is_skipped(self):
        with tempfile.TemporaryDirectory() as shims, tempfile.TemporaryDirectory() as data:
            shim_dir = Path(shims)
            store_alias_shim(shim_dir / "python3")
            interpreter_shim(shim_dir / "py", drop_dash_three=True)

            direct = run(
                [BASH, str(shim_dir / "python3").replace("\\", "/"), "-c", VERSION_PROBE],
                base_env(),
            )
            # A POSIX shell truncates the exit status to one byte, so 9009 arrives as 9009 % 256.
            self.assertIn(direct.returncode, (9009, 9009 % 256), "the shim must imitate the alias")
            self.assertIn(STORE_ALIAS_TEXT, direct.stderr)

            result = run_sh(["task", "list", "--workdir", data], self.shim_env(shim_dir, data))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), {"tasks": [], "count": 0})
            self.assertEqual(cached_python_cmd(data), ["py", "-3"])

    def test_only_py_is_available(self):
        with tempfile.TemporaryDirectory() as shims, tempfile.TemporaryDirectory() as data:
            shim_dir = Path(shims)
            interpreter_shim(shim_dir / "py", drop_dash_three=True)
            result = run_sh(["task", "list", "--workdir", data], self.shim_env(shim_dir, data))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cached_python_cmd(data), ["py", "-3"])

    def test_only_python3_is_available(self):
        with tempfile.TemporaryDirectory() as shims, tempfile.TemporaryDirectory() as data:
            shim_dir = Path(shims)
            interpreter_shim(shim_dir / "python3")
            result = run_sh(["task", "list", "--workdir", data], self.shim_env(shim_dir, data))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cached_python_cmd(data), ["python3"])

    def test_no_interpreter_at_all_exits_two(self):
        with tempfile.TemporaryDirectory() as shims, tempfile.TemporaryDirectory() as data:
            shim_dir = Path(shims)
            store_alias_shim(shim_dir / "python3")
            result = run_sh(["task", "list", "--workdir", data], self.shim_env(shim_dir, data))
            self.assertEqual(result.returncode, 2)
            self.assertIn("no Python", result.stderr)


@unittest.skipUnless(os.name == "nt", WINDOWS_ONLY_REASON)
class CmdDiscoveryTest(unittest.TestCase):
    """The Store-alias case for mf.cmd uses the real alias: cmd.exe never returns from a .cmd shim."""

    @unittest.skipUnless(STORE_ALIAS_DIR is not None, STORE_ALIAS_MISSING_REASON)
    @unittest.skipUnless(WINDOWS_PY is not None, PY_LAUNCHER_MISSING_REASON)
    def test_a_store_alias_python3_is_skipped(self):
        with tempfile.TemporaryDirectory() as data:
            env = wrapper_env(data)
            env["PATH"] = os.pathsep.join(
                [str(STORE_ALIAS_DIR), str(Path(WINDIR) / "System32"), WINDIR]
            )
            result = run_cmd(["task", "list", "--workdir", data], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), {"tasks": [], "count": 0})
            self.assertEqual(cached_python_cmd(data), ["py", "-3"])

    @unittest.skipUnless(WINDOWS_PY is not None, PY_LAUNCHER_MISSING_REASON)
    def test_only_py_is_available(self):
        with tempfile.TemporaryDirectory() as data:
            env = wrapper_env(data)
            env["PATH"] = os.pathsep.join([str(Path(WINDIR) / "System32"), WINDIR])
            result = run_cmd(["task", "list", "--workdir", data], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cached_python_cmd(data), ["py", "-3"])


class SpacedPluginPathTest(unittest.TestCase):
    """§5.6: the wrappers run from a plugin path containing a space."""

    def _spaced_copy(self, tmp: str) -> Path:
        root = Path(tmp) / "plugin root with space"
        shutil.copytree(
            PLUGIN_ROOT / "scripts",
            root / "scripts",
            ignore=shutil.ignore_patterns("__pycache__", "tests"),
        )
        return root

    def _check(self, runner, wrapper_name: str):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            root = self._spaced_copy(tmp)
            result = runner(
                ["task", "list", "--workdir", data],
                wrapper_env(data),
                wrapper=root / "scripts" / wrapper_name,
                cwd=Path(tmp),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), {"tasks": [], "count": 0})

    @unittest.skipUnless(os.name == "nt", WINDOWS_ONLY_REASON)
    def test_cmd_wrapper_in_a_spaced_path(self):
        self._check(run_cmd, "mf.cmd")

    @unittest.skipUnless(BASH is not None, "bash is not available")
    def test_bash_wrapper_in_a_spaced_path(self):
        self._check(run_sh, "mf")


class WithoutJsonschemaTest(unittest.TestCase):
    """§5.6: `task list` is one of the commands that must work without `jsonschema` installed."""

    def run_cli(self, argv: list[str]) -> tuple[int, dict]:
        from io import StringIO

        buffer = StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            code = cli.main(argv)
        return code, json.loads(buffer.getvalue().strip())

    def test_task_list_runs_without_jsonschema(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(sys.modules, {"jsonschema": None}):
                self.assertFalse(schema.available(), "the dependency mask did not take effect")
                code, payload = self.run_cli(["task", "list", "--workdir", tmp])
            self.assertEqual(code, 0)
            self.assertEqual(payload, {"tasks": [], "count": 0})


if __name__ == "__main__":
    unittest.main()
