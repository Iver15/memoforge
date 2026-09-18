"""Single writer of `events.jsonl` and the source of truth for event names (ТЗ §7.2 C/C′)."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
from pathlib import Path

from . import limits

EVENT_TYPES: set[str] = {
    # --- CLI lifecycle (§2.5, §2.2, §3.1) --------------------------------
    "task_created",
    "work_dir_resolved",
    "state_written",
    "cli_call",
    "cancel_requested",
    # --- protocol steps (§3.1, §7.2 C′) ----------------------------------
    "step_issued",
    "step_autoclosed",
    "agent_returned",
    # --- gates (§2.4) -----------------------------------------------------
    "gate_answered",
    "gate_channel_switched",
    "mode_selected",
    "plan_approved",
    # --- pipeline outcomes (§2.1, §5.3, fallbacks.py) ---------------------
    "sources_frozen",
    "result_published",
    "fallback_invoked",
    # --- optional dashboard (§7.5, D-87) ----------------------------------
    "dashboard_published",
    "dashboard_unavailable",
    # --- degradations reported by agents and by config resolution (D-10, D-15)
    "mcp_ratelimit_fallback",
    "writer_model_fallback",
    # --- language option degradations (D-169): an invalid chain value resolves to `en`
    "language_fallback",
    # --- hooks, at-least-once with dedup by `event_key` (§7.2 C, §8.1) ----
    "subagent_requested",
    "subagent_started",
    "subagent_stopped",
    "mcp_call",
    "context_compacted",
    "stop_guard_blocked",
    "stop_guard_gave_up",
    # --- agent best-effort channel (§7.2 E) -------------------------------
    "agent_log",
}

SEVERITIES: tuple[str, ...] = ("info", "warn", "error")

EVENTS_FILENAME = "events.jsonl"
EVENTS_LOCK_FILENAME = "events.lock"
SEEN_DIRNAME = "events"
_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]")

TELEMETRY_EVENT = "cli_call"
"""The one event `cli.log_cli_call` appends about the invocation itself (D-43)."""

UNJOURNALLED_GROUPS: frozenset = frozenset({"events"})
"""D34-25: command groups whose own `cli_call` never enters the journal.

`events analyze` reads the journal it would otherwise write to: its telemetry line becomes the last
event of the run, and the report then dates the end of the run by its own timestamp («TOTAL 56h37m»
for a run of 2h18m). The whole `events` group opts out — `events log` has already written the line
it was asked for, and a second line about that command is noise.
"""


def is_journalled(event: str, data: dict | None) -> bool:
    """False for a `cli_call` of a group that must not touch the journal it reads (D34-25)."""
    if event != TELEMETRY_EVENT:
        return True
    return str((data or {}).get("group") or "") not in UNJOURNALLED_GROUPS


def events_path(work_dir: str | os.PathLike) -> Path:
    """Journal path: always `<work_dir>/events.jsonl` (§2.2, `events_path` field removed)."""
    return Path(work_dir) / EVENTS_FILENAME


def seen_dir(work_dir: str | os.PathLike) -> Path:
    """Directory of at-least-once dedup markers, kept until `tidy` (§7.2 C)."""
    return Path(work_dir) / SEEN_DIRNAME / ".seen"


def seen_marker(work_dir: str | os.PathLike, event_key: str) -> Path:
    """Marker file for one `event_key` (§7.2 C)."""
    safe = _SAFE_KEY.sub("_", event_key)[:120]
    if not safe:
        safe = "_"
    return seen_dir(work_dir) / safe


def utc_now() -> str:
    """UTC timestamp with milliseconds, e.g. `2026-09-08T12:00:00.000Z`."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_event(
    event: str,
    actor: str,
    data: dict | None = None,
    *,
    event_key: str | None = None,
    phase: str | None = None,
    step_id: str | None = None,
    severity: str = "info",
) -> dict:
    """Build one journal record; raises ValueError for unknown event names (M11)."""
    if event not in EVENT_TYPES:
        raise ValueError(f"unknown_event: {event!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown_severity: {severity!r}")
    if data is not None and not isinstance(data, dict):
        raise ValueError("event data must be an object")
    return {
        "ts": utc_now(),
        "event": event,
        "actor": str(actor),
        "severity": severity,
        "phase": phase,
        "step_id": step_id,
        "event_key": event_key,
        "data": dict(data or {}),
    }


def _serialize(record: dict) -> str:
    """Serialize a record to one line ≤ EVENT_LINE_MAX_BYTES, degrading `data` if needed (§7.2 C)."""
    line = json.dumps(record, ensure_ascii=False)
    if len(line.encode("utf-8")) <= limits.EVENT_LINE_MAX_BYTES:
        return line
    trimmed = dict(record)
    trimmed["data"] = {"truncated": True, "dropped_keys": sorted(record.get("data") or {})}
    line = json.dumps(trimmed, ensure_ascii=False)
    if len(line.encode("utf-8")) <= limits.EVENT_LINE_MAX_BYTES:
        return line
    trimmed["data"] = {"truncated": True}
    return json.dumps(trimmed, ensure_ascii=False)


def append_event(
    work_dir: str | os.PathLike,
    event: str,
    actor: str,
    data: dict | None = None,
    *,
    event_key: str | None = None,
    phase: str | None = None,
    step_id: str | None = None,
    severity: str = "info",
) -> dict | None:
    """Append one event under `events.lock`; returns None when `event_key` was already seen (§7.2 C)."""
    from . import state_io  # local import: state_io emits `state_written` through this module

    if not is_journalled(event, data):
        return None  # D34-25: a reader of the journal does not write into it
    work_dir = Path(work_dir)
    record = build_event(
        event,
        actor,
        data,
        event_key=event_key,
        phase=phase,
        step_id=step_id,
        severity=severity,
    )
    line = _serialize(record).encode("utf-8")
    path = events_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = seen_marker(work_dir, event_key) if event_key else None

    with state_io.FileLock(work_dir / EVENTS_LOCK_FILENAME):
        # (1) marker present -> the line is already in the journal
        if marker is not None and marker.exists():
            return None
        # (2) repair a torn tail, then append exactly one line
        with open(path, "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(size - 1)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(line + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        # (3) create the marker only after a successful append
        if marker is not None:
            marker.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                pass
            else:
                os.close(fd)
    return record


def read_events(work_dir: str | os.PathLike) -> list[dict]:
    """Read the journal, skipping invalid lines (incl. a torn tail) and deduplicating by `event_key`."""
    path = events_path(work_dir)
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("event"), str):
            continue
        key = record.get("event_key")
        if isinstance(key, str) and key:
            if key in seen:
                continue
            seen.add(key)
        out.append(record)
    return out


# --- CLI ------------------------------------------------------------------


def run_log(args: argparse.Namespace) -> dict:
    """`mf events log` — append one event; works without jsonschema (§5.6)."""
    data = {}
    if args.data:
        try:
            data = json.loads(args.data)
        except ValueError as exc:
            return {"errors": [f"invalid_data_json: {exc}"]}
        if not isinstance(data, dict):
            return {"errors": ["invalid_data_json: object expected"]}
    try:
        record = append_event(
            args.workdir,
            args.event,
            args.actor,
            data,
            event_key=args.event_key,
            phase=args.phase,
            step_id=args.step,
            severity=args.severity,
        )
    except ValueError as exc:
        return {"errors": [str(exc)]}
    return {"appended": record is not None, "event": record}


def register(subparsers) -> None:
    """Register `mf events log` (the `events` group is shared with `analyze.py`)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "events", "journal (events.jsonl)")
    parser = group.add_parser("log", help="append one event to events.jsonl")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--event", required=True, choices=sorted(EVENT_TYPES))
    parser.add_argument("--actor", default="cli")
    parser.add_argument("--data", default=None, help="JSON object with the payload")
    parser.add_argument("--phase", default=None)
    parser.add_argument("--step", default=None)
    parser.add_argument("--event-key", dest="event_key", default=None)
    parser.add_argument("--severity", default="info", choices=list(SEVERITIES))
    parser.set_defaults(func=run_log)
