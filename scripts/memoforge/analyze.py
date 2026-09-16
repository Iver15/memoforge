"""`mf events analyze` and `mf probe metrics` — read-only run forensics (ТЗ §7.2 F, §0.2 G1/G2/G7).

Ported from the v1 `scripts/analyze_run.py` onto the v2 journal: there is no `phase_transition`
event any more, so the timeline comes from the `phase` field every record carries, the serial-vs-
parallel dispatch question is answered from `step_issued {step_id, attempt, agents}` (with the v1
mtime staircase kept as the channel that survives a dead journal), and the guaranteed CLI trail
(`step_issued`/`agent_returned`) lets the report state honestly whether the best-effort hook and
agent-log channels were present at all (§7.2 «`events analyze` помечает прогон `hooks_absent`/
`agent_logs_absent`»).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from . import events, limits, phases, state_io

SERIAL_SPAN_THRESHOLD_S = 360.0
"""A dispatch group spanning more than this, one agent at a time, is treated as serial (v1 value)."""

EVENTS_DARK_GAP_S = 600.0
"""Journal silence before the last filesystem activity that counts as «events went dark» (v1 value)."""

HOOK_EVENTS: tuple[str, ...] = (
    "subagent_requested",
    "subagent_started",
    "subagent_stopped",
    "mcp_call",
    "context_compacted",
)
"""Layer C of §7.2 — best effort; their absence marks the run `hooks_absent`, it does not fail it."""

COUNTED_EVENTS: tuple[str, ...] = (
    "cli_call",
    "step_issued",
    "agent_returned",
    "step_autoclosed",
    "gate_answered",
    "mcp_call",
    "agent_log",
    "subagent_requested",
    "subagent_started",
    "subagent_stopped",
    "fallback_invoked",
    "context_compacted",
)

G1_TOKEN_TARGET = 25000
"""§0.2 G1: instructions the orchestrator receives over all channels, `bytes/4`."""

G2_OBSERVABLE_TARGET = 150
"""§0.2 G2: observable `cli_call` + `step_issued` on a real run."""

G7_WALL_CLOCK_TARGET_S = 3600
"""§0.2 G7: Full-run wall clock guideline (not an acceptance criterion)."""

BYTES_PER_TOKEN = 4
"""G1 is measured as `bytes/4` in both the v1 baseline and v2 (§0.2)."""

REGRESSION_REASON = "regression_forced_exit"
"""§4.5 п.4 branch 3 / D-21: the only regression verdict `analyze` reports (D-45)."""

NEXT_GROUP = "next"
"""`cli_call {group}` of `mf next` — the answers G1 counts (D-43)."""


# --- parsing helpers ------------------------------------------------------


def parse_ts(value: object) -> dt.datetime | None:
    """Parse an ISO-8601 event timestamp (trailing `Z` allowed) into aware UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        out = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc)


def mtime_utc(path: Path) -> dt.datetime | None:
    """File mtime as aware UTC, or None when the file is unreachable."""
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    except OSError:
        return None


def load_events(work_dir: Path) -> tuple[list[dict], int]:
    """Read the journal in timestamp order; returns (events, malformed_line_count).

    Deduplication by `event_key` and the tolerance for a torn tail live in `events.read_events`
    (§7.2 C); this function only counts the lines that were dropped.
    """
    path = events.events_path(work_dir)
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return [], 0
    total = sum(1 for line in raw.split("\n") if line.strip())
    records = events.read_events(work_dir)
    records.sort(key=lambda record: (parse_ts(record.get("ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)))
    return records, max(0, total - len(records))


def fmt_dur(seconds: float | None) -> str:
    """`1h05m` / `5m03s` / `12s` / `—`."""
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = int(seconds % 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{rest:02d}s"
    return f"{rest}s"


def fmt_clock(value: dt.datetime | None) -> str:
    """`HH:MM:SSZ` or `—`."""
    return value.strftime("%H:%M:%SZ") if value else "—"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


# --- core analysis --------------------------------------------------------


def latest_fs_activity(work_dir: Path) -> tuple[dt.datetime | None, str]:
    """Newest mtime across the artifacts a run produces; bounds the run end when events died."""
    best: dt.datetime | None = None
    best_source = ""
    candidates: list[Path] = [work_dir / state_io.STATE_FILENAME]
    for name in ("drafts", "reviews", "research", "intake"):
        directory = work_dir / name
        if directory.is_dir():
            candidates.extend(path for path in directory.iterdir() if path.is_file())
    candidates.extend(work_dir.glob("memo-*.docx"))
    candidates.extend(work_dir.glob("memo-*.md"))
    candidates.extend(work_dir.glob("deliverable.*"))
    candidates.append(work_dir / "summary.md")
    for path in candidates:
        stamp = mtime_utc(path)
        if stamp and (best is None or stamp > best):
            best, best_source = stamp, path.name
    return best, best_source


def phase_timeline(records: list[dict]) -> list[dict]:
    """`[{phase, enter, exit, dur_s}]` from the `phase` field every v2 record carries (§7.2 C′)."""
    segments: list[dict] = []
    for record in records:
        phase = record.get("phase")
        stamp = parse_ts(record.get("ts"))
        if not isinstance(phase, str) or not phases.is_phase(phase) or stamp is None:
            continue
        if segments and segments[-1]["phase"] == phase:
            segments[-1]["_last"] = stamp
            continue
        if segments:
            segments[-1]["_exit"] = stamp
        segments.append({"phase": phase, "_enter": stamp, "_last": stamp, "_exit": None})
    out = []
    for segment in segments:
        end = segment["_exit"] or segment["_last"]
        out.append(
            {
                "phase": segment["phase"],
                "enter": _iso(segment["_enter"]),
                "exit": _iso(end),
                "dur_s": (end - segment["_enter"]).total_seconds(),
            }
        )
    return out


def dispatch_rows(records: list[dict]) -> list[dict]:
    """One row per `step_issued`, with the number of agents the step dispatched (§7.2 C′)."""
    rows: list[dict] = []
    for record in records:
        if record.get("event") != "step_issued":
            continue
        data = record.get("data") or {}
        agents = data.get("agents")
        agents = agents if isinstance(agents, list) else []
        rows.append(
            {
                "step_id": data.get("step_id") or record.get("step_id"),
                "attempt": data.get("attempt"),
                "phase": record.get("phase"),
                "ts": record.get("ts"),
                "n_agents": len(agents),
                "agents": [_agent_name(agent) for agent in agents],
            }
        )
    return rows


def _agent_name(agent: object) -> str:
    if isinstance(agent, dict):
        return str(agent.get("agent_type") or agent.get("slot") or agent)
    return str(agent)


def requests_by_dispatch(records: list[dict]) -> dict:
    """`subagent_requested` timestamps attributed to the `(phase, step_id, attempt)` that asked for them.

    The hook does not know the step (`step_id` is null on `subagent_requested`), so a request belongs
    to the last `step_issued` that dispatched agents; any other `step_issued` closes the window.
    """
    out: dict = {}
    current = None
    for record in records:
        event = record.get("event")
        if event == "step_issued":
            data = record.get("data") or {}
            agents = data.get("agents")
            current = (
                (record.get("phase"), data.get("step_id") or record.get("step_id"), data.get("attempt"))
                if isinstance(agents, list) and agents
                else None
            )
            continue
        if event == "subagent_requested" and current is not None:
            stamp = parse_ts(record.get("ts"))
            if stamp:
                out.setdefault(current, []).append(stamp)
    return out


def agent_log_marks(records: list[dict]) -> dict:
    """`{(phase, step_id, attempt): {"start": [...], "done": [...]}}` from `mf agent log` (§7.2 E)."""
    out: dict = {}
    for record in records:
        if record.get("event") != "agent_log":
            continue
        data = record.get("data") or {}
        state = data.get("state")
        stamp = parse_ts(record.get("ts"))
        if state not in ("start", "done") or stamp is None:
            continue
        key = (record.get("phase"), data.get("step_id") or record.get("step_id"), data.get("attempt"))
        out.setdefault(key, {"start": [], "done": []})[state].append(stamp)
    return out


def serial_verdict(requests: list, completions: list):
    """Serial ⇔ the k-th agent was asked for only after the (k−1)-th had reported done (D34-07).

    Returns None when the agent-log channel did not record enough completions to decide; one agent
    is never serial.
    """
    asked = sorted(requests)
    if len(asked) < 2:
        return False
    finished = sorted(completions)
    pairs = list(zip(asked[1:], finished))
    if len(pairs) < len(asked) - 1:
        return None
    return all(start > done for start, done in pairs)


def dispatch_groups(records: list[dict], rows: list[dict]) -> list[dict]:
    """One group per `(phase, step_id, attempt)`, with the seriality verdict of D34-07.

    The v1 check grouped by phase alone, so the two research passes of one run (`s-006`, `s-010`)
    became a single 42-minute «serial» group; and it read seriality off the span, so a reviewer round
    issued in one message was flagged because its reports landed in list order.
    """
    requests = requests_by_dispatch(records)
    marks = agent_log_marks(records)
    grouped: dict = {}
    for row in rows:
        if not row["n_agents"]:
            continue
        key = (row["phase"], row["step_id"], row["attempt"])
        group = grouped.setdefault(
            key,
            {
                "phase": row["phase"],
                "step_id": row["step_id"],
                "attempt": row["attempt"],
                "dispatches": 0,
                "agents": [],
                "_stamps": [],
            },
        )
        group["dispatches"] += 1
        group["agents"].extend(row["agents"])
        stamp = parse_ts(row["ts"])
        if stamp:
            group["_stamps"].append(stamp)

    out: list[dict] = []
    for key, group in grouped.items():
        stamps = group.pop("_stamps")
        group["span_s"] = (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else 0.0
        asked = requests.get(key) or (marks.get(key) or {}).get("start") or []
        done = (marks.get(key) or {}).get("done") or []
        serial = serial_verdict(asked, done)
        if serial is None and group["dispatches"] > 1:
            # No agent logs: one `step_issued` per agent over a long span is the v1 staircase.
            serial = group["span_s"] > SERIAL_SPAN_THRESHOLD_S
        group["requests"] = len(asked)
        group["completions"] = len(done)
        group["serial"] = serial
        group["_first"] = min(stamps) if stamps else None
        if len(group["agents"]) >= 2 or group["dispatches"] >= 2 or serial:
            out.append(group)
    out.sort(key=lambda group: (group["_first"] is None, group["_first"] or 0, str(group["step_id"])))
    for group in out:
        group.pop("_first", None)
    return out


def dispatch_staircases(rows: list[dict]) -> list[dict]:
    """Back-to-back single-agent dispatches of one phase — the «one `step_issued` per reviewer» shape.

    D34-07: the v1 check grouped by phase alone, so two research passes separated by a gate and three
    other steps counted as one serial group. A staircase has to be *consecutive*: any other
    `step_issued` in between ends it.
    """
    runs: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        extends = (
            row["n_agents"] == 1
            and isinstance(row["phase"], str)
            and bool(current)
            and row["phase"] == current[-1]["phase"]
            and row["step_id"] != current[-1]["step_id"]
        )
        if extends:
            current.append(row)
            continue
        if len(current) > 1:
            runs.append(current)
        current = [row] if row["n_agents"] == 1 and isinstance(row["phase"], str) else []
    if len(current) > 1:
        runs.append(current)

    groups = []
    for run in runs:
        stamps = [stamp for stamp in (parse_ts(row["ts"]) for row in run) if stamp]
        span = (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else 0.0
        groups.append(
            {
                "phase": run[0]["phase"],
                "step_ids": [row["step_id"] for row in run],
                "dispatches": len(run),
                "span_s": span,
                "agents": [row["agents"][0] if row["agents"] else "?" for row in run],
                "serial": span > SERIAL_SPAN_THRESHOLD_S,
            }
        )
    return groups


def revision_rounds(work_dir: Path, reviewer_list: list[str], *, agent_logs_absent: bool = True) -> list[dict]:
    """Reconstruct each revision iteration from `reviews/v<N>-<kind>.json` mtimes (journal-independent).

    D34-07: mtimes only say in which order the reports landed, which is not the same question as
    «was the round dispatched one agent at a time». The `serial` verdict is therefore left to
    `dispatch_groups` whenever the agent-log channel worked, and reconstructed here only when it
    did not; the timings stay either way.
    """
    reviews = work_dir / "reviews"
    drafts = work_dir / "drafts"
    rounds: list[dict] = []
    if not reviews.is_dir():
        return rounds
    for iteration in range(1, 9):
        draft_mtime = mtime_utc(drafts / f"v{iteration}.md")
        completions: list[tuple[str, dt.datetime]] = []
        for kind in reviewer_list:
            stamp = mtime_utc(reviews / f"v{iteration}-{kind}.json")
            if stamp:
                completions.append((kind, stamp))
        mediator = mtime_utc(reviews / f"v{iteration}-mediator.json")
        if not completions and mediator is None and draft_mtime is None:
            break
        if not completions:
            rounds.append({"iteration": iteration, "reviewers": [], "serial": None})
            continue
        completions.sort(key=lambda item: item[1])
        order = [kind for kind, _ in completions]
        stamps = [stamp for _, stamp in completions]
        span = (stamps[-1] - stamps[0]).total_seconds()
        anchor = draft_mtime if draft_mtime and draft_mtime <= stamps[0] else None
        serial_wall = (stamps[-1] - anchor).total_seconds() if anchor else span
        per_reviewer = []
        previous = anchor
        for kind, stamp in completions:
            if previous is not None:
                per_reviewer.append((stamp - previous).total_seconds())
            previous = stamp
        parallel_estimate = max(per_reviewer) if per_reviewer else None
        in_list_order = order == [kind for kind in reviewer_list if kind in order]
        rounds.append(
            {
                "iteration": iteration,
                "reviewers": order,
                "order": order,
                "monotonic_in_list_order": in_list_order,
                "span_s": span,
                "serial_wall_s": serial_wall,
                "parallel_est_s": parallel_estimate,
                "savings_est_s": (serial_wall - parallel_estimate) if parallel_estimate else None,
                "serial": (
                    len(completions) >= 3 and serial_wall > SERIAL_SPAN_THRESHOLD_S and in_list_order
                    if agent_logs_absent
                    else None
                ),
                "serial_source": "mtime" if agent_logs_absent else "agent_log",
            }
        )
    return rounds


def silent_gaps(records: list[dict], run_end: dt.datetime | None) -> list[dict]:
    """Autonomous stretches longer than `limits.SILENT_GAP_SECONDS` without a new `step_issued` (§7.3).

    A gate phase waits for a human, so its silence is not a granularity problem and is reported with
    `silent: false`; a terminal phase has no next step to issue at all, so it is not accounted
    (D34-25 — «no silent gap inside `done`»).
    """
    issued = [
        (parse_ts(record.get("ts")), record.get("phase"))
        for record in records
        if record.get("event") == "step_issued"
    ]
    issued = [(stamp, phase) for stamp, phase in issued if stamp]
    if not issued:
        return []
    boundaries = list(issued)
    if run_end is not None and run_end > issued[-1][0]:
        boundaries.append((run_end, issued[-1][1]))
    gaps: list[dict] = []
    for (start, phase), (end, _) in zip(boundaries, boundaries[1:]):
        seconds = (end - start).total_seconds()
        if seconds <= limits.SILENT_GAP_SECONDS:
            continue
        if isinstance(phase, str) and phases.is_terminal(phase):
            continue
        is_gate = isinstance(phase, str) and phases.is_gate(phase)
        gaps.append(
            {
                "from": _iso(start),
                "to": _iso(end),
                "seconds": seconds,
                "phase": phase,
                "silent": not is_gate,
                "reason": "gate_waiting_for_user" if is_gate else "no_step_issued",
            }
        )
    return gaps


def gate_wait_seconds(records: list[dict]) -> float | None:
    """Human time spent on gates: from the `step_issued` of a gate phase to its `gate_answered`."""
    total = 0.0
    pending: dict[str, dt.datetime] = {}
    for record in records:
        stamp = parse_ts(record.get("ts"))
        phase = record.get("phase")
        if stamp is None or not isinstance(phase, str):
            continue
        if record.get("event") == "step_issued" and phases.is_gate(phase):
            pending[phase] = stamp
        elif record.get("event") == "gate_answered" and phase in pending:
            total += (stamp - pending.pop(phase)).total_seconds()
    return total or None


def terminal_end(records: list[dict]):
    """`(timestamp, source)` of the moment the run really ended, or `(None, "")` (D34-25).

    The terminal `step_issued {kind: "terminal"}` is the CLI's own full stop; failing that, the first
    record written in a terminal phase is the `final_status` write. `last_event` is a last resort,
    and it is what made `events analyze` date the run by its own telemetry line.
    """
    for record in reversed(records):
        data = record.get("data") or {}
        if record.get("event") == "step_issued" and data.get("kind") == "terminal":
            stamp = parse_ts(record.get("ts"))
            if stamp:
                return stamp, "terminal_step_issued"
    for record in records:
        phase = record.get("phase")
        if isinstance(phase, str) and phases.is_terminal(phase):
            stamp = parse_ts(record.get("ts"))
            if stamp:
                return stamp, "final_status"
    return None, ""


def payload_rejections(records: list[dict]) -> int:
    """`cli_call` entries carrying a `rejection` — an agent payload refused, not a broken CLI (D34-18).

    The field is written by `cli.log_cli_call`; a journal from a build without it counts zero.
    """
    total = 0
    for record in records:
        if record.get("event") != "cli_call":
            continue
        data = record.get("data") or {}
        if data.get("rejection"):
            total += 1
    return total


def blocker_trend(state: dict | None) -> list[dict]:
    """The v2 revision trend: `iterations[]` blockers and coverage — never the v1 score (D-45)."""
    return [
        {
            "iteration": row.get("iteration"),
            "substance_blockers": row.get("substance_blockers"),
            "form_blockers": row.get("form_blockers"),
            "coverage": sorted(row.get("coverage") or []),
        }
        for row in ((state or {}).get("iterations") or [])
        if isinstance(row, dict)
    ]


def regression_iterations(trend: list[dict]) -> list:
    """Iterations that regressed by the §4.5 п.4 criterion: same coverage, more substance blockers."""
    out = []
    for index in range(1, len(trend)):
        current, previous = trend[index], trend[index - 1]
        if current["coverage"] != previous["coverage"]:
            continue
        if not isinstance(current["substance_blockers"], int):
            continue
        if not isinstance(previous["substance_blockers"], int):
            continue
        if current["substance_blockers"] > previous["substance_blockers"]:
            out.append(current["iteration"])
    return out


def analyze(work_dir: str | os.PathLike) -> dict:
    """Full read-only report over one work dir (`mf events analyze`)."""
    work_dir = Path(work_dir)
    records, malformed = load_events(work_dir)
    state = state_io.read_state_or_none(work_dir)

    metrics: dict = {
        "workdir": str(work_dir),
        "task_id": (state or {}).get("task_id"),
        "mode": (state or {}).get("mode"),
        "final_status": (state or {}).get("final_status"),
        "current_phase": (state or {}).get("current_phase"),
        "n_events": len(records),
        "malformed_event_lines": malformed,
        "warnings": [],
    }

    stamps = [parse_ts(record.get("ts")) for record in records]
    stamps = [stamp for stamp in stamps if stamp]
    first_ts = min(stamps) if stamps else None
    last_event_ts = max(stamps) if stamps else None
    fs_last, fs_source = latest_fs_activity(work_dir)

    run_start = first_ts or parse_ts((state or {}).get("created_at"))
    terminal_ts, terminal_source = terminal_end(records)
    if terminal_ts is not None:
        run_end, run_end_source = terminal_ts, terminal_source
    else:
        run_end = max([stamp for stamp in (last_event_ts, fs_last) if stamp], default=None)
        run_end_source = (
            fs_source if (fs_last and (not last_event_ts or fs_last > last_event_ts)) else "last_event"
        )
    metrics["run_start"] = _iso(run_start)
    metrics["run_end"] = _iso(run_end)
    metrics["run_end_source"] = run_end_source
    metrics["total_s"] = (run_end - run_start).total_seconds() if (run_start and run_end) else None
    metrics["last_event_ts"] = _iso(last_event_ts)

    # «events went dark»: the journal stops while the filesystem keeps moving (v1 incident). A run
    # that journalled its own end did not go dark — later mtimes are a copy, a tidy or an export.
    if (
        terminal_ts is None
        and last_event_ts
        and fs_last
        and (fs_last - last_event_ts).total_seconds() > EVENTS_DARK_GAP_S
    ):
        gap = (fs_last - last_event_ts).total_seconds()
        last_phase = next(
            (
                record.get("phase")
                for record in reversed(records)
                if isinstance(record.get("phase"), str)
            ),
            None,
        )
        metrics["events_truncated"] = True
        metrics["events_dark_s"] = gap
        metrics["events_died_at_phase"] = last_phase
        metrics["warnings"].append(
            f"events.jsonl went dark at {fmt_clock(last_event_ts)} (phase '{last_phase}') — "
            f"the run continued {fmt_dur(gap)} more; the rest is reconstructed from file mtimes."
        )
    else:
        metrics["events_truncated"] = False

    metrics["phase_durations"] = phase_timeline(records)

    counters = {name: 0 for name in COUNTED_EVENTS}
    for record in records:
        name = record.get("event")
        if name in counters:
            counters[name] += 1
    metrics["counters"] = counters
    log_files = sorted((work_dir / "logs").glob("*.log")) if (work_dir / "logs").is_dir() else []
    metrics["agent_logs_absent"] = not counters["agent_log"] and not log_files
    metrics["hooks_absent"] = not any(counters[name] for name in HOOK_EVENTS)
    metrics["agent_payload_rejected"] = payload_rejections(records)

    dispatches = dispatch_rows(records)
    metrics["dispatches"] = dispatches
    groups = dispatch_groups(records, dispatches)
    staircases = dispatch_staircases(dispatches)
    metrics["dispatch_groups"] = groups
    metrics["dispatch_staircases"] = staircases
    metrics["serial_dispatch"] = any(group["serial"] is True for group in groups) or any(
        group["serial"] for group in staircases
    )

    reviewer_list = ((state or {}).get("config") or {}).get("reviewer_list") or [
        "logic",
        "form",
        "citations",
        "counterarguments",
    ]
    metrics["reviewer_list"] = reviewer_list
    metrics["revision_rounds"] = revision_rounds(
        work_dir, reviewer_list, agent_logs_absent=metrics["agent_logs_absent"]
    )
    metrics["serial_round_count"] = sum(1 for row in metrics["revision_rounds"] if row.get("serial"))
    if metrics["serial_dispatch"] or metrics["serial_round_count"]:
        metrics["warnings"].append(
            "reviewers were dispatched one at a time — the round must go out in a single message."
        )

    gaps = silent_gaps(records, run_end)
    metrics["gaps"] = gaps
    metrics["silent_gaps"] = [gap for gap in gaps if gap["silent"]]
    metrics["silent_gap"] = bool(metrics["silent_gaps"])
    if metrics["silent_gap"]:
        worst = max(metrics["silent_gaps"], key=lambda gap: gap["seconds"])
        metrics["warnings"].append(
            f"silent_gap: {fmt_dur(worst['seconds'])} without a new step_issued in "
            f"'{worst['phase']}' (target <{limits.SILENT_GAP_SECONDS}s, §7.3)."
        )

    if metrics["hooks_absent"]:
        metrics["warnings"].append(
            "hooks_absent: no hook events in the journal — layer C is off; the CLI trail (C′) is authoritative."
        )
    if metrics["agent_logs_absent"]:
        metrics["warnings"].append("agent_logs_absent: no `mf agent log` records and no logs/*.log files.")

    metrics["blocker_trend"] = blocker_trend(state)
    metrics["regressions_at_iter"] = regression_iterations(metrics["blocker_trend"])
    metrics["regression_forced_exit"] = REGRESSION_REASON in list(
        (state or {}).get("final_status_reasons") or []
    )
    if metrics["regressions_at_iter"]:
        metrics["warnings"].append(
            f"substance blockers grew at iteration(s) {metrics['regressions_at_iter']} "
            "with unchanged reviewer coverage (§4.5 п.4 branch 3)."
        )
    if metrics["regression_forced_exit"]:
        metrics["warnings"].append(
            f"the revision loop exited on `{REGRESSION_REASON}` — the last iteration was wasted."
        )

    metrics["user_gate_wait_s"] = gate_wait_seconds(records)

    if not records and state is None:
        metrics["warnings"].append("no readable events.jsonl and no state.json — nothing to analyze.")
        metrics["empty"] = True
    else:
        metrics["empty"] = False
    return metrics


# --- metrics (G1 / G2 / G7) ----------------------------------------------


def read_list(path: str | os.PathLike) -> list[dict]:
    """Parse `--reads <file>`: a JSON array (strings or `{path, bytes}`) or one path per line."""
    text = Path(path).read_text(encoding="utf-8-sig")
    rows: list[dict] = []
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str):
                rows.append({"path": item, "bytes": None})
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                size = item.get("bytes")
                rows.append({"path": item["path"], "bytes": size if isinstance(size, int) else None})
        return rows
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append({"path": line, "bytes": None})
    return rows


def read_bytes_total(rows: list[dict]) -> dict:
    """Sum the byte size of every `Read` the orchestrator made; missing files count as 0."""
    total = 0
    missing: list[str] = []
    for row in rows:
        if isinstance(row.get("bytes"), int):
            total += max(0, row["bytes"])
            continue
        try:
            total += Path(row["path"]).stat().st_size
        except OSError:
            missing.append(row["path"])
    return {"bytes": total, "files": len(rows), "missing": missing}


def next_answer_bytes(records: list[dict]) -> dict:
    """Σ `bytes_out` of the `cli_call` events of `mf next` — the answers the orchestrator got.

    §0.2 G1 counts the texts `next` actually handed out (dispatch prompts, gate texts, `chat_line`),
    including every re-issue; the serialization of `steps[]` is neither of those (D-43, D-45).
    """
    total = 0
    calls = 0
    for record in records:
        if record.get("event") != "cli_call":
            continue
        data = record.get("data") or {}
        if data.get("group") != NEXT_GROUP:
            continue
        size = data.get("bytes_out")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            continue
        total += size
        calls += 1
    return {"bytes": total, "calls": calls}


def metrics(work_dir: str | os.PathLike, reads: str | None = None, dry_run: bool = False) -> dict:
    """G1 (bytes/4 over Reads + `next` answers), G2 (`cli_call`+`step_issued`), G7 (wall clock)."""
    work_dir = Path(work_dir)
    report = analyze(work_dir)
    records, _ = load_events(work_dir)

    read_rows = read_list(reads) if reads else []
    reads_summary = read_bytes_total(read_rows)
    next_answers = next_answer_bytes(records)
    g1_bytes = reads_summary["bytes"] + next_answers["bytes"]
    g1_tokens = g1_bytes / BYTES_PER_TOKEN

    # A measurement is a lower bound whenever one of the two G1 channels was not observed.
    lower_bound_reasons = []
    if dry_run:
        lower_bound_reasons.append("dry_run: no real orchestrator turn was measured")
    if not reads:
        lower_bound_reasons.append("no --reads: the orchestrator's Read calls are not counted")
    if not next_answers["calls"]:
        lower_bound_reasons.append(
            "no `cli_call` events for `next`: the answers of `next` are not counted"
        )

    counters = report["counters"]
    observable = counters["cli_call"] + counters["step_issued"]
    wall_clock = report.get("total_s")

    # A lower bound that observed nothing at all is not a pass: `0 <= 25000` would read as G1 met
    # when neither channel was measured, so the verdict is `null` — unmeasured (17a N-01).
    g1_pass = None if (lower_bound_reasons and g1_bytes == 0) else g1_tokens <= G1_TOKEN_TARGET

    return {
        "workdir": str(work_dir),
        "task_id": report.get("task_id"),
        "mode": report.get("mode"),
        "lower_bound": bool(lower_bound_reasons),
        "lower_bound_reasons": lower_bound_reasons,
        "measurement": "dry_run" if dry_run else "real_run",
        "g1": {
            "metric": "orchestrator instructions, bytes/4",
            "read_bytes": reads_summary["bytes"],
            "read_files": reads_summary["files"],
            "missing_reads": reads_summary["missing"],
            "next_bytes": next_answers["bytes"],
            "next_calls": next_answers["calls"],
            "total_bytes": g1_bytes,
            "tokens_estimate": round(g1_tokens, 1),
            "target": G1_TOKEN_TARGET,
            "pass": g1_pass,
        },
        "g2": {
            "metric": "observable orchestrator tool calls (cli_call + step_issued)",
            "cli_call": counters["cli_call"],
            "step_issued": counters["step_issued"],
            "observable_total": observable,
            "by_event": counters,
            "target": G2_OBSERVABLE_TARGET,
            "pass": observable <= G2_OBSERVABLE_TARGET,
        },
        "g7": {
            "metric": "wall clock of the run",
            "wall_clock_s": wall_clock,
            "wall_clock_human": fmt_dur(wall_clock),
            "target_s": G7_WALL_CLOCK_TARGET_S,
            "pass": (wall_clock is not None and wall_clock <= G7_WALL_CLOCK_TARGET_S),
            "user_gate_wait_s": report.get("user_gate_wait_s"),
        },
    }


# --- reporting ------------------------------------------------------------


def build_report(report: dict) -> str:
    """Human rendering of `events analyze` (`--human`)."""
    lines = [f"=== memoforge run analysis: {report.get('task_id') or report['workdir']} ==="]
    lines.append(
        f"mode={report.get('mode')}  final_status={report.get('final_status')}  events={report['n_events']}"
        + (f" (+{report['malformed_event_lines']} malformed)" if report["malformed_event_lines"] else "")
    )
    lines.append(
        f"TOTAL wall-clock: {fmt_dur(report.get('total_s'))}   "
        f"({fmt_clock(parse_ts(report.get('run_start')))} -> {fmt_clock(parse_ts(report.get('run_end')))}, "
        f"end via {report.get('run_end_source')})"
    )
    if report.get("events_truncated"):
        lines.append("")
        lines.append(
            f"! EVENTS TRUNCATED: dark at {fmt_clock(parse_ts(report.get('last_event_ts')))} "
            f"(phase '{report.get('events_died_at_phase')}'); +{fmt_dur(report.get('events_dark_s'))} from mtimes."
        )

    lines.append("")
    lines.append("-- phase durations --")
    for row in report.get("phase_durations", []):
        lines.append(f"  {row['phase']:<40} {fmt_dur(row.get('dur_s')):>8}")

    if report.get("dispatch_groups"):
        lines.append("")
        lines.append("-- dispatch groups (phase / step / attempt) --")
        for group in report["dispatch_groups"]:
            tag = "SERIAL" if group["serial"] else ("unknown" if group["serial"] is None else "parallel")
            label = f"{group['phase']} {group['step_id']}/a{group['attempt']}"
            lines.append(
                f"  {label:<40} {len(group['agents'])} agents in {group['dispatches']} dispatch(es), "
                f"{group['requests']} requested / {group['completions']} done [{tag}]"
            )

    if report.get("dispatch_staircases"):
        lines.append("")
        lines.append("-- consecutive single-agent dispatches of one phase --")
        for group in report["dispatch_staircases"]:
            tag = "SERIAL" if group["serial"] else "ok"
            lines.append(
                f"  {group['phase']:<40} {group['dispatches']} dispatches "
                f"({', '.join(group['step_ids'])}), span {fmt_dur(group['span_s'])} [{tag}]"
            )

    if report.get("revision_rounds"):
        lines.append("")
        lines.append("-- revision loop (mtime-reconstructed) --")
        for row in report["revision_rounds"]:
            if not row.get("reviewers"):
                lines.append(f"  iter {row['iteration']}: (no reviewer JSONs found)")
                continue
            tag = "SERIAL" if row.get("serial") else ("timings only" if row.get("serial") is None else "ok")
            lines.append(
                f"  iter {row['iteration']}: {len(row['reviewers'])} reviewers, "
                f"span {fmt_dur(row.get('span_s'))} [{tag}]"
            )
            lines.append(f"      order: {' -> '.join(row.get('order', []))}")

    if report.get("gaps"):
        lines.append("")
        lines.append("-- gaps between step_issued --")
        for gap in report["gaps"]:
            tag = "SILENT" if gap["silent"] else gap["reason"]
            lines.append(f"  {gap['phase']:<40} {fmt_dur(gap['seconds']):>8}  [{tag}]")

    lines.append("")
    lines.append("-- channels --")
    lines.append(
        f"  hooks_absent={report.get('hooks_absent')}  agent_logs_absent={report.get('agent_logs_absent')}"
        f"  agent_payload_rejected={report.get('agent_payload_rejected')}"
    )
    counters = report.get("counters", {})
    lines.append("  " + "  ".join(f"{name}={value}" for name, value in counters.items() if value))

    if report.get("blocker_trend"):
        lines.append("")
        lines.append("-- revision blocker trend (substance/form per iteration) --")
        lines.append(
            "  "
            + "  ".join(
                f"v{row['iteration']}={row['substance_blockers']}s/{row['form_blockers']}f"
                f"({len(row['coverage'])} reviewers)"
                for row in report["blocker_trend"]
            )
        )
    if report.get("user_gate_wait_s"):
        lines.append(f"  user-gate wait (human, not compute): {fmt_dur(report['user_gate_wait_s'])}")

    if report.get("warnings"):
        lines.append("")
        lines.append("-- notes --")
        lines.extend(f"  * {warning}" for warning in report["warnings"])
    return "\n".join(lines)


def build_compare(a: dict, b: dict) -> str:
    """Side-by-side rendering of two runs (`--compare A B --human`)."""
    lines = ["=== compare ==="]

    def row(label: str, left: object, right: object) -> str:
        return f"  {label:<26} {str(left):<24} {str(right)}"

    lines.append(row("", a.get("task_id") or "A", b.get("task_id") or "B"))
    lines.append(row("total wall-clock", fmt_dur(a.get("total_s")), fmt_dur(b.get("total_s"))))
    lines.append(row("mode", a.get("mode"), b.get("mode")))
    lines.append(row("final_status", a.get("final_status"), b.get("final_status")))
    lines.append(row("revision iterations", len(a.get("blocker_trend", [])), len(b.get("blocker_trend", []))))
    lines.append(row("serial reviewer rounds", a.get("serial_round_count"), b.get("serial_round_count")))
    lines.append(row("serial dispatch", a.get("serial_dispatch"), b.get("serial_dispatch")))
    lines.append(row("silent gaps", len(a.get("silent_gaps", [])), len(b.get("silent_gaps", []))))
    lines.append(row("events truncated", a.get("events_truncated"), b.get("events_truncated")))
    lines.append(row("hooks absent", a.get("hooks_absent"), b.get("hooks_absent")))
    lines.append(row("blocker regressions", a.get("regressions_at_iter"), b.get("regressions_at_iter")))
    lines.append(row("regression forced exit", a.get("regression_forced_exit"), b.get("regression_forced_exit")))
    lines.append(row("user-gate wait", fmt_dur(a.get("user_gate_wait_s")), fmt_dur(b.get("user_gate_wait_s"))))
    return "\n".join(lines)


# --- commands -------------------------------------------------------------


def run_analyze(args: argparse.Namespace) -> dict:
    """`mf events analyze` — one JSON object (or `--human` text) per §7.2 F.

    D34-25: the `cli_call` of this invocation never reaches the journal — `events.UNJOURNALLED_GROUPS`
    keeps the forensic command out of the run it measures.
    """
    if args.compare:
        left = analyze(args.compare[0])
        right = analyze(args.compare[1])
        return {"a": left, "b": right, "human": build_compare(left, right)}

    work_dir = Path(args.workdir)
    if not work_dir.is_dir():
        return {"errors": [f"work_dir_not_found: {work_dir}"]}
    report = analyze(work_dir)
    if report["empty"]:
        return {"errors": ["no_events_and_no_state"], "workdir": str(work_dir)}
    report["human"] = build_report(report)
    return report


def run_metrics(args: argparse.Namespace) -> dict:
    """`mf probe metrics` — G1/G2/G7 (§0.2)."""
    work_dir = Path(args.workdir)
    if not work_dir.is_dir():
        return {"errors": [f"work_dir_not_found: {work_dir}"]}
    try:
        return metrics(work_dir, reads=args.reads, dry_run=args.dry_run)
    except OSError as exc:
        return {"errors": [f"reads_unreadable: {exc}"]}


def register(subparsers) -> None:
    """Register `mf events analyze` and `mf probe metrics`."""
    from . import cli

    group = cli.group_subparsers(subparsers, "events", "journal (events.jsonl)")
    analyze_parser = group.add_parser("analyze", help="timeline, serial dispatch, silent gaps (§7.2 F)")
    analyze_parser.add_argument("--workdir", default=None)
    analyze_parser.add_argument("--compare", nargs=2, metavar=("A", "B"), default=None)
    analyze_parser.set_defaults(func=run_analyze)

    probe = cli.group_subparsers(subparsers, "probe", "platform probes and metrics (§11, §0.2)")
    metrics_parser = probe.add_parser("metrics", help="G1/G2/G7 for one run")
    metrics_parser.add_argument("--workdir", required=True)
    metrics_parser.add_argument("--reads", default=None, help="file listing the orchestrator's Read calls")
    metrics_parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    metrics_parser.set_defaults(func=run_metrics)
