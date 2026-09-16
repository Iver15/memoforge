#!/usr/bin/env python
"""Hook layer C of ТЗ §7.2: append progress events to `events.jsonl`, never touch state.

Four invocations, one script (§8.1 table):

    --pre        PreToolUse  `^(Agent|Task)$`            -> subagent_requested {description}
    (no flag)    SubagentStart / SubagentStop            -> subagent_started / subagent_stopped
    --mcp        PostToolUse `^mcp__`                    -> mcp_call {server, tool, ok}
    --compact    PreCompact                              -> context_compacted

The journal writer is `memoforge.events.append_event` (D-14): one line under `events.lock`, the
`event_key` marker of §7.2 C making the three interpreter variants of one hook idempotent. Hook
events are at-least-once, so a duplicate is possible and is removed on read; a *lost* event is not.
No active memoforge task, an unknown hook event or any exception at all -> exit 0 without writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

ACTOR = "hook"

MAX_TEXT = 500
"""Free-form payload strings are cut here so one journal line stays under `EVENT_LINE_MAX_BYTES`."""

SUBAGENT_EVENTS = {"SubagentStart": "subagent_started", "SubagentStop": "subagent_stopped"}

MCP_ALIAS_SPELLINGS: tuple[tuple[str, str], ...] = (
    ("legaldatahunter", "ldh"),
    ("ldh", "ldh"),
    ("courtlistener", "courtlistener"),
    ("legalviz", "legalviz"),
    ("eurlex", "legalviz"),
    ("uklegalmcp", "uklegal"),
    ("uklegal", "uklegal"),
    ("justicelibre", "justicelibre"),
    ("opencaselaw", "opencaselaw"),
    ("swisscaselaw", "opencaselaw"),
)
"""How a host spells one of the six bundled legal servers -> its `routing.MCP_SERVERS` alias.

The `PostToolUse` matcher is `^mcp__`, so every MCP server of the session reaches this hook and the
filter lives here: a call to anything else is not accounted for. The alias is the same value the
agent channel writes (`mf agent log --mcp <alias>`) and the same key `config.mcp_budget` and
`progress.mcp_calls` use, so the two channels are counted in the same units (the agent channel is
the fallback for a host that runs no `PostToolUse` hook, and a host that runs both over-counts —
the budget is an orientation for the researcher, not a cut-off). Matching
is on the namespace segment with punctuation dropped and is a suffix test, because a host prefixes
the server name: `plugin_memoforge_uk-legal` in Claude Code, `claude_ai_Legal_Data_Hunter` in
Cowork, `eurlex` where LegalViz announces itself by its `serverInfo.name` (D-105), and
`swiss-caselaw` where OpenCaseLaw does the same (D-148).
"""

NON_ALNUM = re.compile(r"[^a-z0-9]+")


def server_alias(server: object) -> str | None:
    """Routing alias of one MCP namespace segment; None when it is not a bundled legal server."""
    name = NON_ALNUM.sub("", str(server or "").lower())
    if not name:
        return None
    for spelling, alias in MCP_ALIAS_SPELLINGS:
        if name.endswith(spelling):
            return alias
    return None


def _text(value: object, limit: int = MAX_TEXT) -> str | None:
    """A short string, or None when the value is absent or not a string."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return value[:limit]


def event_key(payload: dict, event: str, identity_key: str) -> str | None:
    """`sha1(session_id + event + <tool_use_id|agent_id>)` (§7.2 C); None when either id is absent."""
    session_id = _text(payload.get("session_id"), 200)
    identity = _text(payload.get(identity_key), 200)
    if not session_id or not identity:
        return None
    return hashlib.sha1((session_id + event + identity).encode("utf-8")).hexdigest()


# --- payload -> event -----------------------------------------------------


def build_pre(payload: dict) -> dict | None:
    """PreToolUse on `Agent`/`Task`: the dispatch `description` is layer A of §7.2."""
    tool_input = payload.get("tool_input")
    description = _text(tool_input.get("description")) if isinstance(tool_input, dict) else None
    data = {"description": description}
    if isinstance(tool_input, dict):
        subagent_type = _text(tool_input.get("subagent_type"), 120)
        if subagent_type:
            data["agent_type"] = subagent_type
    return {
        "event": "subagent_requested",
        "data": data,
        "event_key": event_key(payload, "subagent_requested", "tool_use_id"),
    }


def build_subagent(payload: dict) -> dict | None:
    """SubagentStart / SubagentStop; the hook event name decides which of the two it is."""
    hook_event = _text(payload.get("hook_event_name"), 60)
    event = SUBAGENT_EVENTS.get(hook_event or "")
    if event is None:
        return None
    data = {
        "agent_id": _text(payload.get("agent_id"), 200),
        "agent_type": _text(payload.get("agent_type"), 120),
    }
    if event == "subagent_stopped":
        data["result"] = _text(payload.get("result"))
    return {"event": event, "data": data, "event_key": event_key(payload, event, "agent_id")}


def build_mcp(payload: dict) -> dict | None:
    """PostToolUse on any MCP tool: account for the four legal servers under their routing alias.

    `server` carries the alias, not the host namespace — anything else and `progress.mcp_calls`
    could never be compared with `config.mcp_budget`. A call to any other MCP server writes nothing.
    """
    tool_name = _text(payload.get("tool_name"), 200) or ""
    parts = tool_name.split("__", 2)
    if len(parts) < 3 or parts[0] != "mcp":
        return None
    alias = server_alias(parts[1])
    if alias is None:
        return None
    data = {"server": alias, "tool": parts[2] or None, "ok": mcp_ok(payload)}
    return {
        "event": "mcp_call",
        "data": data,
        "event_key": event_key(payload, "mcp_call", "tool_use_id"),
    }


def mcp_ok(payload: dict) -> bool:
    """Best-effort success flag of a PostToolUse response (absent evidence of failure = ok)."""
    response = payload.get("tool_response")
    if isinstance(response, dict):
        for key in ("is_error", "isError", "error"):
            if response.get(key):
                return False
        if "success" in response:
            return bool(response.get("success"))
    return True


def build_compact(payload: dict) -> dict | None:
    """PreCompact: best-effort, without a stable host identifier there is no `event_key` (§7.2 C)."""
    data = {"trigger": _text(payload.get("trigger"), 60)}
    return {"event": "context_compacted", "data": data, "event_key": None}


BUILDERS = {"pre": build_pre, "subagent": build_subagent, "mcp": build_mcp, "compact": build_compact}


# --- writing --------------------------------------------------------------


def log(payload: object, mode: str, cwd: str | os.PathLike | None = None) -> dict | None:
    """Append one event for this payload; None when there is nothing to write (§8.1)."""
    if not isinstance(payload, dict):
        return None
    builder = BUILDERS.get(mode)
    if builder is None:
        return None
    built = builder(payload)
    if built is None:
        return None

    from memoforge import events, hooks_common

    if cwd is None:
        payload_cwd = payload.get("cwd")
        cwd = payload_cwd if isinstance(payload_cwd, str) and payload_cwd else None
    work_dir = hooks_common.find_active_task(cwd)
    if work_dir is None:
        return None
    state = hooks_common.read_state_quiet(work_dir) or {}
    phase = state.get("current_phase")
    return events.append_event(
        work_dir,
        built["event"],
        ACTOR,
        built["data"],
        event_key=built.get("event_key"),
        phase=phase if isinstance(phase, str) else None,
    )


# --- entry point ----------------------------------------------------------


def read_stdin() -> str:
    """Hook payload as text; `utf-8-sig` because PowerShell writes a BOM (§8.1)."""
    data = sys.stdin.buffer.read()
    return data.decode("utf-8-sig", errors="replace")


def main(argv: list | None = None, stdin_text: str | None = None, cwd: str | os.PathLike | None = None) -> str:
    """Return the JSON the hook prints (always `{}`); never raises, so the caller exits 0."""
    try:
        parser = argparse.ArgumentParser(description="memoforge progress logger (ТЗ §7.2 C)")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--pre", dest="mode", action="store_const", const="pre")
        group.add_argument("--mcp", dest="mode", action="store_const", const="mcp")
        group.add_argument("--compact", dest="mode", action="store_const", const="compact")
        parser.set_defaults(mode="subagent")
        args = parser.parse_args(argv)
        text = read_stdin() if stdin_text is None else stdin_text
        payload = json.loads(text) if text.strip() else None
        log(payload, args.mode, cwd)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 — a hook must never fail loudly (§8.1)
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
    try:
        sys.stdout.write(main())
    except SystemExit:
        sys.stdout.write("{}")
    sys.exit(0)
