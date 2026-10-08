#!/usr/bin/env python
"""CI guard: `python -m unittest discover -s scripts/tests -v` must report no unexpected skip.

G9 of the spec asks for "0 skipped when the dependencies are installed": a skip is how a missing
`jsonschema` or `python-docx` turns a broken build into a green one. Feed this script the verbose
output (file argument, or stdin) and it fails the job on any skip whose reason is not in
ALLOWED_SKIP_REASONS, on a suite that ran no tests at all, and on a run whose trailing status line
is missing.

    python -m unittest discover -s scripts/tests -v 2>&1 | tee test-output.txt
    python scripts/ci/check_no_skips.py test-output.txt

The allowlist holds skip reasons that encode a *platform or host environment* condition — a test
that cannot run on this runner and would not run any better with every dependency installed. A
missing dependency never qualifies: `bash is not available` stays a hard failure, because both CI
runners have bash and its absence means a broken environment, not a foreign one.
"""

from __future__ import annotations

import re
import sys

# Duplicated on purpose: this guard must not import from scripts/tests, and the test module must
# not import from scripts/ci. All three literals are named constants in
# scripts/tests/test_launcher_wrappers.py — change them there and here together.
#
# Platform: the ubuntu job cannot run `mf.cmd` however complete its dependencies are.
WINDOWS_ONLY_REASON = "mf.cmd runs on Windows only"
# The msvcrt lock primitive is also unavailable on POSIX hosts.
WINDOWS_LOCK_REASON = "Windows-specific msvcrt.locking behaviour"
# Environment-conditional on Windows: a `windows-latest` runner may or may not carry the Windows
# Store `python3` alias (usually not) or `py.exe`. Neither is a dependency the job installs.
STORE_ALIAS_MISSING_REASON = (
    "no Windows Store python3 alias on PATH; a .cmd shim cannot stand in for it, because "
    "cmd.exe hands control to a batch file without returning to mf.cmd"
)
PY_LAUNCHER_MISSING_REASON = "py.exe (PEP 397 launcher) is not installed"

ALLOWED_SKIP_REASONS = frozenset(
    {WINDOWS_ONLY_REASON, WINDOWS_LOCK_REASON, STORE_ALIAS_MISSING_REASON, PY_LAUNCHER_MISSING_REASON}
)

RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
STATUS = re.compile(r"^(OK|FAILED)(?:\s*\((?P<detail>[^)]*)\))?\s*$", re.M)
SKIPPED_COUNT = re.compile(r"\bskipped=(\d+)")
SKIPPED_LINE = re.compile(r"^(?P<test>.*?)\s\.\.\.\sskipped(?P<reason>.*)$", re.M)


def read(argv: list[str]) -> str:
    if len(argv) > 1:
        with open(argv[1], encoding="utf-8", errors="replace") as handle:
            return handle.read()
    return sys.stdin.read()


def reason_text(raw: str) -> str:
    """The reason as unittest wrote it: `... skipped 'mf.cmd runs on Windows only'`."""
    return raw.strip().strip("'\"")


def main(argv: list[str]) -> int:
    text = read(argv)
    problems: list[str] = []

    ran = RAN.search(text)
    if ran is None:
        problems.append("no 'Ran N tests' line — the suite did not finish")
    elif int(ran.group(1)) == 0:
        problems.append("the suite ran 0 tests")

    status = STATUS.search(text)
    if status is None:
        problems.append("no OK/FAILED status line")

    reported = sum(int(count) for count in SKIPPED_COUNT.findall(text))
    skips = [(test.strip(), reason_text(reason)) for test, reason in SKIPPED_LINE.findall(text)]
    allowed = [entry for entry in skips if entry[1] in ALLOWED_SKIP_REASONS]
    unexpected = [entry for entry in skips if entry[1] not in ALLOWED_SKIP_REASONS]

    for test, reason in allowed:
        print(f"check_no_skips: allowed skip — {test} — {reason}")

    if unexpected:
        problems.append(f"{len(unexpected)} skipped test(s) — every skip outside the allowlist is a failure")
        for test, reason in unexpected:
            problems.append(f"  {test} — skipped {reason!r}")
    if reported > len(skips):
        problems.append(
            f"the status line reports skipped={reported} but only {len(skips)} '... skipped' "
            "line(s) were found — run the suite with -v"
        )

    if problems:
        print("check_no_skips: FAIL", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"check_no_skips: OK — {ran.group(1)} tests, {len(allowed)} allowed skip(s), 0 unexpected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
