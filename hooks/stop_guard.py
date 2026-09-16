#!/usr/bin/env python
"""Stop hook (M9, ТЗ §8.3): refuse to end the turn while a memoforge task is mid-flight.

Read-only with respect to `state.json`. When the active task sits in a non-terminal, non-gate phase
the hook answers `{"decision": "block", "reason": ...}` — including when the last step is already
closed, because an open phase means the work is not finished. The block counter lives in the journal
(`stop_guard_blocked`, at most `limits.STOP_GUARD_MAX_BLOCKS` per phase); after that the guard emits
`stop_guard_gave_up` once and stays silent, so a genuinely stuck run can still end.

This is defence in depth, not the mechanism M9 relies on (that is `mf finalize`), so it is off until
`userConfig.stop_guard` — `CLAUDE_PLUGIN_OPTION_STOP_GUARD` — is switched on, which also keeps P7
(re-entry after a `decision: block` on Windows and in Cowork) an opt-in experiment.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

ACTOR = "hook"

STOP_GUARD_OPTION = "CLAUDE_PLUGIN_OPTION_STOP_GUARD"

TRUE_VALUES: tuple[str, ...] = ("1", "true", "yes", "on")
"""Mirror of `task.coerce_bool`; the guard is off unless the option says otherwise (§8.4)."""

REASON = (
    "memoforge: task {task_id} is in phase {phase}; run `mf next` to continue "
    "or `mf finalize --reason interrupted`"
)

DEDUP_SECONDS = 5.0
"""One `Stop` fires this hook once per interpreter variant of §8.1, and — unlike the PreToolUse and
Subagent hooks — a `Stop` payload carries no id to build an `event_key` from (§7.2 C defines one only
for `tool_use_id` / `agent_id`). Without a guard the two or three variants would spend the whole
`STOP_GUARD_MAX_BLOCKS` budget on a single `Stop`. So a block recorded less than this many seconds
ago counts as the same `Stop`: the extra variants still answer `block` (the decision is identical),
they just do not increment the counter. A real second `Stop` needs a whole assistant turn, which is
far longer than this window."""


def enabled() -> bool:
    """`userConfig.stop_guard`, default false (§8.3, §8.4)."""
    return (os.environ.get(STOP_GUARD_OPTION) or "").strip().lower() in TRUE_VALUES


def _phase_records(work_dir, event: str, phase: str) -> list:
    """Journal records of one event name in one phase, oldest first."""
    from memoforge import events

    found = []
    for record in events.read_events(work_dir):
        if record.get("event") != event:
            continue
        data = record.get("data")
        recorded = record.get("phase")
        if not isinstance(recorded, str) and isinstance(data, dict):
            recorded = data.get("phase")
        if recorded == phase:
            found.append(record)
    return found


def _age_seconds(record: dict) -> float | None:
    """Seconds since the record was written, or None when its timestamp cannot be read."""
    stamp = record.get("ts")
    if not isinstance(stamp, str) or not stamp:
        return None
    text = stamp[:-1] + "+00:00" if stamp.endswith(("Z", "z")) else stamp
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - moment).total_seconds()


def decide(payload: object, cwd: str | os.PathLike | None = None) -> dict:
    """`{}` or the block decision of §8.3; the journal counter decides which."""
    if not enabled():
        return {}
    if not isinstance(payload, dict):
        return {}

    from memoforge import events, hooks_common, limits, phases, state_io

    if cwd is None:
        payload_cwd = payload.get("cwd")
        cwd = payload_cwd if isinstance(payload_cwd, str) and payload_cwd else None
    work_dir = hooks_common.find_active_task(cwd)
    if work_dir is None:
        return {}
    state = hooks_common.read_state_quiet(work_dir)
    if not hooks_common.is_active(state):
        return {}
    phase = state.get("current_phase")
    if not isinstance(phase, str) or phases.is_gate(phase):
        return {}

    task_id = state.get("task_id")
    task_id = task_id if isinstance(task_id, str) and task_id else work_dir.name

    # Counting and appending happen under `events.lock` so the interpreter variants of one `Stop`
    # cannot each read the same count and each append a block (`append_event` re-enters the lock).
    with state_io.FileLock(work_dir / events.EVENTS_LOCK_FILENAME):
        blocks = _phase_records(work_dir, "stop_guard_blocked", phase)
        if len(blocks) >= limits.STOP_GUARD_MAX_BLOCKS:
            if not _phase_records(work_dir, "stop_guard_gave_up", phase):
                events.append_event(
                    work_dir,
                    "stop_guard_gave_up",
                    ACTOR,
                    {"phase": phase, "task_id": task_id, "blocks": len(blocks)},
                    phase=phase,
                    severity="warn",
                )
            return {}
        age = _age_seconds(blocks[-1]) if blocks else None
        if age is None or age >= DEDUP_SECONDS:
            events.append_event(
                work_dir,
                "stop_guard_blocked",
                ACTOR,
                {"phase": phase, "task_id": task_id, "block": len(blocks) + 1},
                phase=phase,
            )
    return {"decision": "block", "reason": REASON.format(task_id=task_id, phase=phase)}


# --- entry point ----------------------------------------------------------


def read_stdin() -> str:
    """Hook payload as text; `utf-8-sig` because PowerShell writes a BOM (§8.1)."""
    data = sys.stdin.buffer.read()
    return data.decode("utf-8-sig", errors="replace")


def main(argv: list | None = None, stdin_text: str | None = None, cwd: str | os.PathLike | None = None) -> str:
    """Return the JSON the hook prints; never raises, so the caller always exits 0."""
    try:
        parser = argparse.ArgumentParser(description="memoforge Stop guard (ТЗ §8.3)")
        parser.parse_args(argv)
        text = read_stdin() if stdin_text is None else stdin_text
        payload = json.loads(text) if text.strip() else None
        return json.dumps(decide(payload, cwd), ensure_ascii=False)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 — a hook must never fail loudly (§8.1)
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
