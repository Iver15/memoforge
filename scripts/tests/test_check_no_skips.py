"""D-100: `scripts/ci/check_no_skips.py` fails on every skip except the platform allowlist.

G9 wants "0 skipped when the dependencies are installed", so a `jsonschema is not installed`
skip must keep failing the job. A skip that encodes a *platform or host environment* condition is
different: the ubuntu job cannot run `mf.cmd` however complete its dependencies are, and a
`windows-latest` runner may carry neither the Windows Store `python3` alias nor `py.exe`. The
guard therefore accepts the three reasons named in `test_launcher_wrappers.py`
(`WINDOWS_ONLY_REASON`, `STORE_ALIAS_MISSING_REASON`, `PY_LAUNCHER_MISSING_REASON`) and prints the
allowed skips it saw.

The transcripts below are built by hand in the format `unittest -v` prints, so the tests never
depend on which platform runs them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "ci" / "check_no_skips.py"

WINDOWS_ONLY = "mf.cmd runs on Windows only"
NO_STORE_ALIAS = (
    "no Windows Store python3 alias on PATH; a .cmd shim cannot stand in for it, because "
    "cmd.exe hands control to a batch file without returning to mf.cmd"
)
NO_PY_LAUNCHER = "py.exe (PEP 397 launcher) is not installed"
ALLOWED = (WINDOWS_ONLY, NO_STORE_ALIAS, NO_PY_LAUNCHER)

MISSING_DEPENDENCY = "jsonschema is not installed"
NO_BASH = "bash is not available"

CMD_TEST = "test_launcher_wrappers.CmdWrapperTest.test_task_list_returns_json_and_exit_zero"
BASH_TEST = "test_launcher_wrappers.BashWrapperTest.test_task_list_returns_json_and_exit_zero"
DISCOVERY_TEST = "test_launcher_wrappers.CmdDiscoveryTest.test_a_store_alias_python3_is_skipped"
SCHEMA_TEST = "test_schema.SchemaTest.test_validate"
PLAIN_TEST = "test_task.TaskTest.test_new"


def line(test_id: str, outcome: str) -> str:
    return f"{test_id.rsplit('.', 1)[-1]} ({test_id}) ... {outcome}"


def report(*outcomes: tuple, ran: int | None = None) -> str:
    """A verbose transcript; each outcome is `(test_id, None)` for a pass or `(test_id, reason)`."""
    lines = []
    skipped = 0
    for test_id, reason in outcomes:
        if reason is None:
            lines.append(line(test_id, "ok"))
        else:
            skipped += 1
            lines.append(line(test_id, f"skipped {reason!r}"))
    total = len(outcomes) if ran is None else ran
    lines += ["", "-" * 70, f"Ran {total} tests in 0.123s", ""]
    lines.append("OK" + (f" (skipped={skipped})" if skipped else ""))
    return "\n".join(lines) + "\n"


def run(text: str) -> subprocess.CompletedProcess:
    """Run the guard over `text` written to a temporary transcript file."""
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "test-output.txt"
        transcript.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(transcript)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )


class CheckNoSkipsTest(unittest.TestCase):
    def test_a_run_without_skips_passes(self):
        result = run(report((PLAIN_TEST, None), (SCHEMA_TEST, None)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_no_skips: OK", result.stdout)
        self.assertIn("0 allowed skip(s)", result.stdout)

    def test_windows_locking_skip_passes(self):
        """The msvcrt locking test cannot run on macOS or the Ubuntu CI runner."""
        reason = "Windows-specific msvcrt.locking behaviour"
        test_id = "test_state_io.WindowsLockTest.test_second_handle_in_the_same_process_is_excluded"
        result = run(report((test_id, reason), (PLAIN_TEST, None)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 allowed skip(s), 0 unexpected", result.stdout)
        self.assertIn(test_id, result.stdout)

    def test_each_allowed_reason_alone_passes(self):
        for reason in ALLOWED:
            with self.subTest(reason=reason):
                result = run(report((DISCOVERY_TEST, reason), (PLAIN_TEST, None)))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("check_no_skips: OK", result.stdout)
                self.assertIn("1 allowed skip(s)", result.stdout)
                self.assertIn(reason, result.stdout)

    def test_every_allowed_reason_together_passes(self):
        result = run(
            report(
                (CMD_TEST, WINDOWS_ONLY),
                (DISCOVERY_TEST, NO_STORE_ALIAS),
                ("test_launcher_wrappers.CmdDiscoveryTest.test_only_py_is_available", NO_PY_LAUNCHER),
                (PLAIN_TEST, None),
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3 allowed skip(s), 0 unexpected", result.stdout)

    def test_the_allowed_skips_are_printed(self):
        result = run(report((CMD_TEST, WINDOWS_ONLY), (PLAIN_TEST, None)))
        self.assertIn("allowed skip", result.stdout)
        self.assertIn(WINDOWS_ONLY, result.stdout)
        self.assertIn(CMD_TEST, result.stdout)

    def test_a_missing_dependency_skip_fails(self):
        result = run(report((SCHEMA_TEST, MISSING_DEPENDENCY), (PLAIN_TEST, None)))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("check_no_skips: FAIL", result.stderr)
        self.assertIn(MISSING_DEPENDENCY, result.stderr)
        self.assertIn(SCHEMA_TEST, result.stderr)

    def test_a_missing_bash_still_fails(self):
        """Both runners have bash: `bash is not available` means a broken environment.

        Checked next to an allowed skip, because widening the allowlist must not widen it to this.
        """
        result = run(report((DISCOVERY_TEST, NO_PY_LAUNCHER), (BASH_TEST, NO_BASH), (PLAIN_TEST, None)))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(NO_BASH, result.stderr)
        self.assertIn(BASH_TEST, result.stderr)
        self.assertNotIn(NO_PY_LAUNCHER, result.stderr)

    def test_a_mixed_run_fails(self):
        result = run(
            report((CMD_TEST, WINDOWS_ONLY), (SCHEMA_TEST, MISSING_DEPENDENCY), (PLAIN_TEST, None))
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("check_no_skips: FAIL", result.stderr)
        self.assertIn(MISSING_DEPENDENCY, result.stderr)
        self.assertIn("1 skipped test(s)", result.stderr)
        self.assertNotIn(WINDOWS_ONLY, result.stderr)
        self.assertIn(WINDOWS_ONLY, result.stdout)

    def test_zero_tests_fails(self):
        result = run(report(ran=0))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("the suite ran 0 tests", result.stderr)

    def test_a_missing_status_line_fails(self):
        result = run("Ran 12 tests in 0.123s\n")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no OK/FAILED status line", result.stderr)

    def test_a_counted_skip_without_a_verbose_line_fails(self):
        """A non-verbose run must not hide skips behind an unattributable `skipped=N`."""
        result = run("Ran 12 tests in 0.123s\n\nOK (skipped=3)\n")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("skipped=3", result.stderr)
        self.assertIn("-v", result.stderr)


if __name__ == "__main__":
    unittest.main()
