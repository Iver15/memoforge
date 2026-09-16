#!/usr/bin/env python
"""Probe P2 (ТЗ §11): record that an exec-form hook ran, and with which substitutions.

Deliberately dependency-free — it does not import `memoforge`, because the question P2 asks is
whether the hook process starts at all and whether `${CLAUDE_PLUGIN_ROOT}` reaches it as a real
path. Pass `--variant python|python3|py-3` so the log says which exec form fired. Every call appends
one JSON line to `<plugin_data_dir>/probe-echo.log`:

    {ts, argv, executable, cwd, env: {...}, hook_event_name, tool_name, stdin_bytes}

`env.CLAUDE_PLUGIN_ROOT` still holding the literal `${CLAUDE_PLUGIN_ROOT}` (or the argv path not
existing on disk) is the failure signature the probe is looking for. Wire it up by hand — the
`hooks.PreToolUse` snippet with the three exec forms is in the P2 procedure of
`docs/probes/v2-probes.md` — never through `build_hooks.py`; it is not part of the §8.1 table.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

LOG_FILENAME = "probe-echo.log"

ENV_KEYS: tuple[str, ...] = (
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PLUGIN_DATA",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_SESSION_ID",
)


def plugin_data_dir() -> Path:
    """The §2.5 chain, inlined: the probe must not depend on the package it is probing for."""
    env = (os.environ.get("CLAUDE_PLUGIN_DATA") or "").strip()
    if env:
        return Path(env)
    local_appdata = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        return Path(local_appdata) / "claude" / "plugin-data" / "memoforge"
    home = (os.environ.get("HOME") or os.environ.get("USERPROFILE") or "").strip()
    if home:
        return Path(home) / ".claude" / "plugin-data" / "memoforge"
    return Path(__file__).resolve().parents[1] / ".data"


def build_record(argv: list, stdin_text: str) -> dict:
    """One log line: the call itself plus whatever of the hook payload can be parsed."""
    payload = {}
    try:
        parsed = json.loads(stdin_text) if stdin_text.strip() else {}
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        payload = {}
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "argv": list(argv),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "env": {key: os.environ.get(key) for key in ENV_KEYS},
        "hook_event_name": payload.get("hook_event_name"),
        "tool_name": payload.get("tool_name"),
        "agent_type": payload.get("agent_type"),
        "stdin_bytes": len(stdin_text.encode("utf-8", errors="replace")),
    }


def log_path(target: str | os.PathLike | None = None) -> Path:
    """`<plugin_data_dir>/probe-echo.log`, or an explicit `--out` path."""
    return Path(target) if target else plugin_data_dir() / LOG_FILENAME


def append(record: dict, target: str | os.PathLike | None = None) -> Path:
    """Append one line; the file is a plain append-only log, no locking (probe, not pipeline)."""
    path = log_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def main(argv: list | None = None, stdin_text: str | None = None) -> str:
    """Return the JSON the hook prints (always `{}`); never raises, so the caller exits 0."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        target = None
        if "--out" in argv:
            index = argv.index("--out")
            target = argv[index + 1] if index + 1 < len(argv) else None
        if stdin_text is None:
            stdin_text = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        append(build_record(argv, stdin_text), target)
    except BaseException:  # noqa: BLE001 — a probe must never break the session (§8.1)
        pass
    return "{}"


def _utf8_stdout() -> None:
    """CONVENTIONS: a hook may print a path or a host that the console codepage cannot encode."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


if __name__ == "__main__":
    _utf8_stdout()
    sys.stdout.write(main())
    sys.exit(0)
