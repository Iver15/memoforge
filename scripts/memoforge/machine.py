"""`mf next` / `mf report`: the deterministic protocol machine of ТЗ §2.1, §2.4 and §3.1.

The orchestrator knows three commands; every phase transition, budget, autoclose and publication
decision lives here (M1). `next` is idempotent, `report` is idempotent, and each step is closed by
exactly one state write.
"""

from __future__ import annotations

import argparse
import copy
import shutil
from pathlib import Path

from . import (
    citations,
    dispatch,
    events,
    fallbacks,
    finalize,
    gates,
    i18n,
    i18n_en,
    limits,
    lint,
    modes,
    phases,
    preflight,
    review,
    revision,
    routing,
    schema,
    sources,
    state_io,
    stepctx,
    sufficiency,
)
from .docx import fallback as md_fallback

KIND_DISPATCH = "dispatch"
KIND_SCRIPT = "script"
KIND_GATE_TEXT = gates.KIND_GATE_TEXT
KIND_GATE_AUQ = gates.KIND_GATE_AUQ
KIND_INLINE = "inline-llm"
KIND_TERMINAL = "terminal"

REASON_INITIAL = "initial"
REASON_FAILURE = "failure"
REASON_RECOVERY = "recovery"
REASON_RERUN = "rerun"

SCRIPT_DONE_STATUSES = ("ok", "no_change", "skipped")
"""Closed statuses that satisfy the prerequisite of a script step; only `fail` does not (§2.2)."""

STEP_LOOP = "step_loop"
"""`final_status_reasons[]` code of the step-loop guard (D-30, D-58)."""

ORCHESTRATOR_SLOT = "orchestrator"

HOOK_ACTOR = "hook"
"""`actor` of the journal events `hooks/progress_logger.py` writes (`ACTOR` there, D-217)."""

BASE_ROUTE: tuple[str, ...] = (
    "intake_preliminary_research",
    "intake_questions_pending",
    "planning",
    "plan_approval_pending",
    "research",
    "research_sufficiency",
    "currency_check",
    "source_pack",
    "drafting",
    "revision_loop",
    "client_readiness",
    "export",
)
"""Phases every run passes through; §2.1 rows 7, 8 and 11 join the route only when reachable."""

DRAFTS_DIR = "drafts"

SUFFICIENCY_PATH = "research/research-sufficiency.json"
"""Canonical verdict of `research-sufficiency-reviewer`, read by `mf sufficiency route` (§6)."""


# --- step records ---------------------------------------------------------


def next_step_id(state: dict) -> str:
    """`s-001`, `s-002`, … — one id per protocol step, re-used across its attempts (§3.1)."""
    seen = {row.get("step_id") for row in (state.get("steps") or []) if isinstance(row, dict)}
    return f"s-{len(seen) + 1:03d}"


def step_row(state: dict, step_id: str, attempt: int | None = None) -> dict | None:
    """One `steps[]` record: the given attempt, or the latest one."""
    rows = stepctx.steps_for(state, step_id)
    if attempt is None:
        return rows[-1] if rows else None
    for row in rows:
        if int(row.get("attempt") or 1) == int(attempt):
            return row
    return None


def open_steps(state: dict) -> list[dict]:
    """Latest attempt of every step that has not been closed, in issue order."""
    latest: dict[str, dict] = {}
    for row in state.get("steps") or []:
        if not isinstance(row, dict):
            continue
        step_id = row.get("step_id")
        current = latest.get(step_id)
        if current is None or int(row.get("attempt") or 1) >= int(current.get("attempt") or 1):
            latest[step_id] = row
    order = [row.get("step_id") for row in (state.get("steps") or []) if isinstance(row, dict)]
    seen: list[str] = []
    for step_id in order:
        if step_id not in seen:
            seen.append(step_id)
    return [latest[step_id] for step_id in seen if latest[step_id].get("status") in (None, "")]


def command_key(command: list[str] | None) -> str:
    """`['<mf>', 'draft', 'lint', '--workdir', …]` -> `draft.lint` (the step's purpose)."""
    parts: list[str] = []
    for token in list(command or [])[1:]:
        if token.startswith("-"):
            break
        parts.append(token)
    return ".".join(parts)


def agent_names(row: dict) -> list[str]:
    """Bare agent names of a dispatch step, sorted."""
    return sorted(
        str(agent.get("agent_type") or "").split(":", 1)[-1] for agent in (row.get("agents") or [])
    )


def purpose(row: dict) -> str:
    """Stable purpose token of one step record; the planners walk the episode by it."""
    kind = row.get("kind")
    if kind == KIND_SCRIPT:
        return "script:" + command_key(row.get("command"))
    if kind == KIND_DISPATCH:
        return "dispatch:" + ",".join(agent_names(row))
    if kind == KIND_INLINE:
        outputs = row.get("expected_outputs") or []
        name = str(outputs[0].get("canonical_path", "")).rsplit("/", 1)[-1] if outputs else ""
        return f"inline:{name}"
    return f"{kind}:{row.get('phase')}"


def episode(state: dict) -> list[dict]:
    """Closed steps issued since the run last entered the current phase (§2.1 re-entry)."""
    phase = state.get("current_phase")
    steps = [row for row in (state.get("steps") or []) if isinstance(row, dict)]
    start = 0
    for index in range(len(steps) - 1, -1, -1):
        if steps[index].get("phase") != phase:
            start = index + 1
            break
    # A retried step appears several times; the latest attempt keeps the position of its own row.
    latest: dict[str, tuple[int, dict]] = {}
    for index, row in enumerate(steps[start:]):
        latest[row.get("step_id")] = (index, row)
    ordered = sorted(latest.values(), key=lambda pair: pair[0])
    return [row for _, row in ordered if row.get("status") not in (None, "")]


def merged_slot_statuses(state: dict, step_id: str) -> dict:
    """Slot -> best status across every attempt of one dispatch step (§2.1 row 5: partial results)."""
    rank = {"ok": 3, "no_change": 2, "fail": 1, "superseded": 0}
    merged: dict[str, str] = {}
    for row in stepctx.steps_for(state, step_id):
        for agent in row.get("agents") or []:
            slot = str(agent.get("slot"))
            status = str(agent.get("status") or "")
            if rank.get(status, 0) >= rank.get(merged.get(slot, ""), 0):
                merged[slot] = status
    return merged


def result_of(row: dict | None) -> dict:
    """The stored `result_ref.result` of a closed step, as a dict."""
    if not row:
        return {}
    stored = row.get("result_ref")
    if isinstance(stored, dict):
        result = stored.get("result")
        return result if isinstance(result, dict) else {}
    return {}


# --- progress (§2.2) ------------------------------------------------------


def route_for(state: dict) -> list[str]:
    """Non-terminal phases reachable with the current `config` (§3.1 rule 4: the denominator N)."""
    config = state.get("config") or {}
    route = list(BASE_ROUTE)
    if str(config.get("source_review_gate") or "auto") != "off":
        route.insert(route.index("source_pack") + 1, "source_review_pending")
    phase = str(state.get("current_phase") or "")
    if phase and not phases.is_terminal(phase) and phase not in route:
        rank = phases.PHASE_INDEX.get(phase, len(phases.PHASES))
        position = len(route)
        for index, name in enumerate(route):
            if phases.PHASE_INDEX.get(name, 0) > rank:
                position = index
                break
        route.insert(position, phase)
    return route


def position_in(route: list[str], phase: str) -> int:
    """1-based position of the phase in the route (0 for a terminal phase)."""
    return route.index(phase) + 1 if phase in route else 0


def mcp_call_counts(work_dir: Path) -> dict:
    """`progress.mcp_calls` — successful and failed calls per server, from the journal (§4.3).

    D-217: a host that runs the `PostToolUse` hook also gets the agent's own `mf agent log --mcp`
    for the same call, so every hook event counts and a self-report counts only when it is older
    than the first hook event — the usage of a run that started on a host without the hook.
    """
    calls = [record for record in events.read_events(work_dir) if record.get("event") == "mcp_call"]
    hook_times = [str(record.get("ts") or "") for record in calls if record.get("actor") == HOOK_ACTOR]
    first_hook = min(hook_times) if hook_times else None
    counts: dict[str, int] = {}
    for record in calls:
        self_report = record.get("actor") != HOOK_ACTOR
        if first_hook is not None and self_report and str(record.get("ts") or "") >= first_hook:
            continue
        server = str((record.get("data") or {}).get("server") or "unknown")
        counts[server] = counts.get(server, 0) + 1
    return counts


def _stale(started_at: str | None, now: str) -> bool:
    if not started_at:
        return False
    try:
        start = events.datetime.datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        current = events.datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, AttributeError):
        return False
    return (current - start).total_seconds() > limits.AGENT_STALE_SECONDS


def build_progress(work_dir: Path, state: dict) -> dict:
    """Recompute the derived `progress` field from `steps[]` and the journal (§2.2)."""
    previous = state.get("progress") or {}
    phase = str(state.get("current_phase"))
    route = route_for(state)
    now = events.utc_now()
    active: list[dict] = []
    for row in open_steps(state):
        if row.get("kind") != KIND_DISPATCH:
            continue
        for agent in row.get("agents") or []:
            if agent.get("status") not in (None, ""):
                continue
            started_at = row.get("issued_at")
            if _stale(started_at, now):
                continue
            active.append(
                {
                    "slot": agent.get("slot"),
                    "agent_type": agent.get("agent_type"),
                    "label": row.get("phase"),
                    "started_at": started_at,
                }
            )
    started = previous.get("phase_started_at")
    if previous.get("phase") != phase or not started:
        started = now
    return {
        "phase": phase,
        "phase_started_at": started,
        "route": route,
        # D-121: a terminal phase is off the route, and «0 of 12» at the end of a finished run reads
        # as a broken counter — the run is complete, so the position is the denominator.
        "position": len(route) if phases.is_terminal(phase) else position_in(route, phase),
        "total": len(route),
        "active": active,
        "last_line": previous.get("last_line"),
        "artifact_url": previous.get("artifact_url"),
        # D-109: written once by `mf finalize`; the recompute must carry it, not drop it.
        "published_to": previous.get("published_to"),
        # D-167: the same carry-over for the root-level memo copy beside it.
        "published_memo": previous.get("published_memo"),
        "mcp_calls": mcp_call_counts(work_dir),
    }


# --- optional dashboard (§7.5, D-87) --------------------------------------

DASHBOARD_HTML: tuple[str, ...] = ("lib", "dashboard.html")
"""The shipped page; the orchestrator publishes it once through the built-in `Artifact` tool."""

DASHBOARD_COLLECTION = "run"
DASHBOARD_DOC_ID = "state"
"""The single document the page subscribes to (`run/state`); one `write_db` per step updates it,
pinned to the version the previous write returned (D-159)."""

DASHBOARD_PATCH_FILE: tuple[str, ...] = ("dashboard", "patch.json")
"""Where `next` writes the document for the Artifact tool's `file_path` (D-159): the router hands the
path over instead of retyping up to 180 KB of JSON inline, and the answer of `next` stays small."""

DASHBOARD_UNAVAILABLE = fallbacks.DASHBOARD_UNAVAILABLE
"""`fallbacks.py` condition key and banner id: the host has no working `Artifact` tool."""

DASHBOARD_TIMELINE = 12
"""How many of the latest steps the page shows."""

DASHBOARD_GATE_HINT = i18n_en.EN["ui"]["machine"]["gate_hint"]
"""The whole `gate.hint` (D-89): the page says that the run waits, never what it asks."""

DASHBOARD_GATE_QUESTIONS = 12
DASHBOARD_GATE_OPTIONS = 6
"""D-96 caps: how much of an open gate the page repeats read-only (the chat stays the channel)."""

_DASHBOARD_ANSWER_HINT_KEYS: dict[str, str] = {
    "intake": "answer_hint_intake",
    "sufficiency_followup": "answer_hint_sufficiency_followup",
    "insufficient": "answer_hint_insufficient",
    "source_review": "answer_hint_source_review",
    "plan": "answer_hint_plan",
}
"""Gate -> `ui.machine.answer_hint_<gate>` key (D-177)."""


def _dashboard_answer_hint(gate: str, ui: str) -> str:
    """`gate.answer_hint` in the interface language (D-177); same English bytes as before."""
    key = _DASHBOARD_ANSWER_HINT_KEYS.get(gate)
    if key is None:
        return i18n.t(ui, "ui.machine.gate_hint")
    return i18n.t(ui, f"ui.machine.{key}")


DASHBOARD_ANSWER_HINTS: dict[str, str] = {
    gate: i18n_en.EN["ui"]["machine"][key] for gate, key in _DASHBOARD_ANSWER_HINT_KEYS.items()
}
"""`gate.answer_hint` per gate — the same reply format the CLI-generated gate text prints (D-96)."""

DASHBOARD_GATE_SOURCES: dict[str, tuple[str, ...]] = {
    "intake": (gates.QUESTIONS_PATH,),
    "plan": (gates.MCP_PROBE_PATH,),
}
"""D-97: the files a gate reads through `gates`, checked before its questions reach the page.

`gates._read_json` degrades a missing or corrupt file to `{}`, and the builders above it then ask
their questions from an empty document — a plan gate whose `plan.json` cannot be read would still
publish `Plan`/`Mode`/`Sources`, and `mcp-probe.json` unreadable would announce that every legal
database is missing. `plan.json` itself is not listed here: `_dashboard_plan` already validates it
against the schema (D-90), and the plan branch below publishes nothing without that projection.
"""

# --- D-95: the one place that turns a step record into a sentence a lawyer can read --------
# `purpose()` above stays the machine's token (`script:draft.lint`); nothing here touches it, the
# `chat_line` or the event journal. Only `dashboard_patch.timeline[].text` is built from these.

_DASHBOARD_AGENT_KEYS: dict[str, str] = {
    "fact-assumption-analyst": "agent_fact_assumption_analyst",
    "legal-researcher": "agent_legal_researcher",
    "research-sufficiency-reviewer": "agent_research_sufficiency_reviewer",
    "currency-checker": "agent_currency_checker",
    "memo-writer": "agent_memo_writer",
    "logic-reviewer": "agent_logic_reviewer",
    "form-reviewer": "agent_form_reviewer",
    "citation-auditor": "agent_citation_auditor",
    "counterargument-reviewer": "agent_counterargument_reviewer",
    "revision-mediator": "agent_revision_mediator",
    "client-readiness-reviewer": "agent_client_readiness_reviewer",
    "style-extractor": "agent_style_extractor",
}
"""Agent name -> `ui.machine.agent_*` key (D-177)."""

DASHBOARD_AGENT_LABELS: dict[str, str] = {
    name: i18n_en.EN["ui"]["machine"][key] for name, key in _DASHBOARD_AGENT_KEYS.items()
}

_DASHBOARD_SLOT_KEYS: dict[str, str] = {
    "statutes": "slot_statutes",
    "case_law": "slot_case_law",
    "doctrine": "slot_doctrine",
}
"""Research layer -> `ui.machine.slot_*` key (D-177)."""

DASHBOARD_SLOT_LABELS: dict[str, str] = {
    slot: i18n_en.EN["ui"]["machine"][key] for slot, key in _DASHBOARD_SLOT_KEYS.items()
}
"""Only the research layers qualify an agent label — `Researcher (case law)`."""

_DASHBOARD_SCRIPT_KEYS: dict[str, tuple[str, str]] = {
    "render.research": ("script_render_research_running", "script_render_research_finished"),
    "render.mediator": ("script_render_mediator_running", "script_render_mediator_finished"),
    "sufficiency.route": ("script_sufficiency_route_running", "script_sufficiency_route_finished"),
    "sources.preflight": ("script_sources_preflight_running", "script_sources_preflight_finished"),
    "sources.liveness": ("script_sources_liveness_running", "script_sources_liveness_finished"),
    "sources.verify": ("script_sources_verify_running", "script_sources_verify_finished"),
    "sources.pack": ("script_sources_pack_running", "script_sources_pack_finished"),
    "draft.anchor": ("script_draft_anchor_running", "script_draft_anchor_finished"),
    "draft.lint": ("script_draft_lint_running", "script_draft_lint_finished"),
    "draft.audit-citations": (
        "script_draft_audit_citations_running",
        "script_draft_audit_citations_finished",
    ),
    "draft.finish": ("script_draft_finish_running", "script_draft_finish_finished"),
    "review.aggregate": ("script_review_aggregate_running", "script_review_aggregate_finished"),
    "revision.next": ("script_revision_next_running", "script_revision_next_finished"),
    "docx.render": ("script_docx_render_running", "script_docx_render_finished"),
    "docx.validate": ("script_docx_validate_running", "script_docx_validate_finished"),
    "finalize": ("script_finalize_running", "script_finalize_finished"),
}
"""`command_key(command)` -> (running key, finished key) of `ui.machine.script_*` (D-177)."""

DASHBOARD_SCRIPT_LABELS: dict[str, tuple[str, str]] = {
    command: (
        i18n_en.EN["ui"]["machine"][running],
        i18n_en.EN["ui"]["machine"][finished],
    )
    for command, (running, finished) in _DASHBOARD_SCRIPT_KEYS.items()
}
"""`command_key(command)` → (running, finished); `mf_command` builds every one of these keys."""

_DASHBOARD_INLINE_KEYS: dict[str, tuple[str, str]] = {
    "plan.json": ("inline_plan_json_running", "inline_plan_json_finished"),
    "mcp-probe.json": ("inline_mcp_probe_json_running", "inline_mcp_probe_json_finished"),
}
"""Inline-llm output file -> (running key, finished key) of `ui.machine.inline_*` (D-177)."""

DASHBOARD_INLINE_LABELS: dict[str, tuple[str, str]] = {
    name: (
        i18n_en.EN["ui"]["machine"][running],
        i18n_en.EN["ui"]["machine"][finished],
    )
    for name, (running, finished) in _DASHBOARD_INLINE_KEYS.items()
}
"""Inline-llm steps, keyed by the file the orchestrator writes."""

_DASHBOARD_SUFFIX_KEYS: dict[str, str] = {
    "": "suffix_started",
    "ok": "suffix_finished",
    "no_change": "suffix_finished",
    "fail": "suffix_failed_retrying",
    "skipped": "suffix_skipped",
}
"""Dispatch status -> `ui.machine.suffix_*` key (D-177)."""

DASHBOARD_STEP_SUFFIX: dict[str, str] = {
    status: i18n_en.EN["ui"]["machine"][key] for status, key in _DASHBOARD_SUFFIX_KEYS.items()
}
"""What a dispatch line says about its status; a script/inline step picks a form from its pair."""


def dashboard_enabled(state: dict) -> bool:
    """True when `userConfig.dashboard` reached `state.config` (§7.5; default on, D-92)."""
    return bool((state.get("config") or {}).get("dashboard"))


def dashboard_declined(state: dict) -> bool:
    """True once `task dashboard --unavailable` recorded the banner: never offer publishing again."""
    return any(
        isinstance(banner, dict) and banner.get("banner_id") == DASHBOARD_UNAVAILABLE
        for banner in state.get("fallback_banners") or []
    )


def dashboard_url(state: dict) -> str:
    """The URL of the published page while the dashboard is live, else `""` (§7.5, D-94).

    Live means all three: the flag is on, the page was published (`task dashboard --url`) and the
    tool was never reported unavailable. It is the single predicate for "the user can read this on
    the page instead of in the chat".
    """
    if not dashboard_enabled(state) or dashboard_declined(state):
        return ""
    return str((state.get("progress") or {}).get("artifact_url") or "").strip()


def _dashboard_status(state: dict) -> str:
    """One word for the header: what the run is doing right now."""
    phase = str(state.get("current_phase") or "")
    if phases.is_terminal(phase):
        return str(state.get("final_status") or phase)
    if state.get("cancel_requested"):
        return "cancelling"
    if phases.is_gate(phase):
        return "waiting for you"
    return "running"


def _dashboard_deliverable(state: dict) -> str:
    """Absolute path of the deliverable once `finalize` wrote one, else an empty string."""
    relative = state.get("final_docx_path")
    if not relative:
        return ""
    work_dir = state.get("work_dir")
    return str(Path(work_dir) / str(relative)) if work_dir else str(relative)


def published_to(state: dict) -> str:
    """The folder `mf finalize` copied the result into, or an empty string when it copied none (D-109)."""
    return str((state.get("progress") or {}).get("published_to") or "").strip()


def published_file_count(work_dir: Path, state: dict) -> int | None:
    """The count the router's copy of the `Published:` folder must reach, or None without a manifest (D-217).

    The manifest is `files` of the last `result_published` event — what `finalize.publish` put there,
    never a scan of the folder, where a stale file would count. The two root copies (D-167) are in
    the manifest but outside the folder the router copies, so they are taken out.
    """
    manifest = None
    for record in events.read_events(work_dir):
        if record.get("event") == "result_published":
            manifest = (record.get("data") or {}).get("files")
    if not isinstance(manifest, list):
        return None
    slug = finalize.slug_of(state, Path(work_dir))
    root_copies = {
        finalize.root_memo_name(slug, finalize.DELIVERABLE_DOCX),
        finalize.root_memo_name(slug, finalize.DELIVERABLE_MD),
        finalize.root_summary_name(slug),
        Path(published_memo(state)).name,
    }
    return sum(1 for name in manifest if str(name) not in root_copies)


def published_memo(state: dict) -> str:
    """The root-level memo copy (`<root>/memo-<slug>.<ext>`), or "" when there is none (D-167)."""
    return str((state.get("progress") or {}).get("published_memo") or "").strip()


def _dashboard_banners(state: dict) -> list[dict]:
    """Known banner ids of `state` with their static label (D-88).

    The stored `text` is never republished: `output_folder_unavailable` embeds the absolute
    `work_dir` and `dashboard_unavailable` an arbitrary error string, and the page has no need for
    either. Ids outside `fallbacks.BANNER_IDS` are dropped rather than passed through.

    D-176b: the label is read in the interface language — the notices are the page's own prose,
    and an English notice on a Russian page is the same defect as an English phase name.
    """
    ui = i18n.ui_language(state)
    published: list[dict] = []
    seen: set[str] = set()
    for banner in state.get("fallback_banners") or []:
        if not isinstance(banner, dict):
            continue
        banner_id = str(banner.get("banner_id") or "")
        if banner_id not in fallbacks.BANNER_IDS or banner_id in seen:
            continue
        seen.add(banner_id)
        published.append({"id": banner_id, "text": fallbacks.dashboard_label(banner_id, ui)})
    return published


def _latest_steps(state: dict) -> list[dict]:
    """Latest attempt of every step, in issue order (a retry keeps its step's position)."""
    latest: dict[str, dict] = {}
    for row in state.get("steps") or []:
        if isinstance(row, dict):
            latest[str(row.get("step_id"))] = row
    return list(latest.values())


def _agent_name(agent: dict, ui: str = "en") -> str:
    """`memoforge:legal-researcher` -> `Researcher` — the agent in the interface language (D-177).

    An agent id the `ui.machine.agent_*` table does not know degrades to its own spelling, as it
    always has; an entry without an `agent_type` has no name at all and answers `""`.
    """
    name = str(agent.get("agent_type") or "").split(":", 1)[-1]
    key = _DASHBOARD_AGENT_KEYS.get(name)
    if key is not None:
        return i18n.t(ui, f"ui.machine.{key}")
    return DASHBOARD_AGENT_LABELS.get(name) or name.replace("-", " ").capitalize()


def _agent_layer(agent: dict, ui: str = "en") -> str:
    """The research layer of an agent in the interface language, or `""` when it has none.

    Only the three layers of `_DASHBOARD_SLOT_KEYS` qualify an agent. Every other slot (`writer`,
    `logic`, `currency`, …) is an internal identifier with no pack entry, so it is dropped rather
    than printed raw: the reviewers and the writer name themselves completely already.
    """
    slot_key = _DASHBOARD_SLOT_KEYS.get(str(agent.get("slot") or ""))
    return i18n.t(ui, f"ui.machine.{slot_key}") if slot_key is not None else ""


def _agent_label(agent: dict, ui: str = "en") -> str:
    """`Researcher (case law)` — the agent in the interface language, qualified by its layer."""
    label = _agent_name(agent, ui) or "Agent"
    layer = _agent_layer(agent, ui)
    return f"{label} ({layer})" if layer else label


def _step_state(row: dict) -> str:
    """`running | done | waiting | failed` — what the timeline dot of one step means (D-95).

    A dispatch is failed as soon as one slot is: the step itself stays open until every slot has
    reported, and the page must show the failure while the surviving slots finish.
    """
    status = str(row.get("status") or "")
    if row.get("kind") == KIND_DISPATCH and any(
        isinstance(agent, dict) and str(agent.get("status") or "") == "fail"
        for agent in (row.get("agents") or [])
    ):
        return "failed"
    if not status:
        return "waiting" if row.get("kind") in (KIND_GATE_TEXT, KIND_GATE_AUQ) else "running"
    return "failed" if status == "fail" else "done"


def _inline_output_name(row: dict) -> str:
    outputs = [entry for entry in (row.get("expected_outputs") or []) if isinstance(entry, dict)]
    return str(outputs[0].get("canonical_path", "")).rsplit("/", 1)[-1] if outputs else ""


def _step_sentence(row: dict, ui: str = "en") -> str:
    """One timeline line in the user's words (D-95); the tables above are the whole vocabulary.

    Nothing here is a machine token: `purpose()` keeps `script:draft.lint` for the planners, and an
    unknown step degrades to the phase label plus `step` rather than leaking its command key.
    D-176: the phase name is read in the interface language; the sentence frames around it and the
    `DASHBOARD_*_LABELS` tables are the dashboard's own strings.
    """
    kind = row.get("kind")
    flow = _step_state(row)
    status = str(row.get("status") or "")
    phase_label = phases.label(row.get("phase"), ui)
    if kind == KIND_TERMINAL:
        return phase_label
    if kind in (KIND_GATE_TEXT, KIND_GATE_AUQ):
        if flow == "waiting":
            return i18n.t(ui, "ui.machine.waiting_for_you", label=phase_label)
        if status == "skipped":
            return i18n.t(ui, "ui.machine.skipped", label=phase_label)
        return i18n.t(ui, "ui.machine.you_answered", label=phase_label)
    if kind == KIND_DISPATCH:
        agents = [entry for entry in (row.get("agents") or []) if isinstance(entry, dict)]
        labels = [_agent_label(entry, ui) for entry in agents] or [
            i18n.t(ui, "ui.machine.phase_step", label=phase_label)
        ]
        head = ", ".join(labels[:3])
        if len(labels) > 3:
            head += " " + i18n.t(ui, "ui.machine.more", count=len(labels) - 3)
        if flow == "failed":
            return f"{head}: {i18n.t(ui, 'ui.machine.suffix_failed_retrying')}"
        suffix_key = _DASHBOARD_SUFFIX_KEYS.get(status)
        suffix = i18n.t(ui, f"ui.machine.{suffix_key}") if suffix_key is not None else None
        return f"{head}: {suffix if suffix is not None else (status or i18n.t(ui, 'ui.machine.suffix_started'))}"
    pair = None
    if kind == KIND_SCRIPT:
        keys = _DASHBOARD_SCRIPT_KEYS.get(command_key(row.get("command")))
        if keys is not None:
            pair = (i18n.t(ui, f"ui.machine.{keys[0]}"), i18n.t(ui, f"ui.machine.{keys[1]}"))
    elif kind == KIND_INLINE:
        keys = _DASHBOARD_INLINE_KEYS.get(_inline_output_name(row))
        if keys is not None:
            pair = (i18n.t(ui, f"ui.machine.{keys[0]}"), i18n.t(ui, f"ui.machine.{keys[1]}"))
    if pair is None:
        step = i18n.t(ui, "ui.machine.phase_step", label=phase_label)
        pair = (step, step)
    if flow == "failed":
        return f"{pair[0]}: {i18n.t(ui, 'ui.machine.suffix_failed_retrying')}"
    if status == "skipped":
        return f"{pair[0]}: {i18n.t(ui, 'ui.machine.suffix_skipped')}"
    return pair[1] if flow == "done" else pair[0]


def _dashboard_chat_line(state: dict, steps: list[dict], ui: str) -> str:
    """The "what is happening right now" line of the page, in the interface language (D-176b).

    `progress.last_line` stays English in `state` — it is the line the router relays into the chat
    in its own words (§10) — but the page prints what it is given, so the projection renders the
    step that line belongs to with the same machinery the timeline uses. A run with no step to
    name yet degrades to the localized status line, never to the raw English sentence; before the
    first line there is nothing to show and the field stays empty, as it always was.
    """
    if not str((state.get("progress") or {}).get("last_line") or "").strip():
        return ""
    return _step_sentence(steps[-1], ui) if steps else _status_label(state)


def _phase_label(state: dict) -> str:
    """Phase name for the page — `Review round 2` while the run iterates (D-95, D-176)."""
    ui = i18n.ui_language(state)
    phase = str(state.get("current_phase") or "")
    iteration = int(state.get("current_iteration") or 0)
    if phase == "revision_loop" and iteration > 0:
        return f"{phases.label(phase, ui)} {iteration}"
    return phases.label(phase, ui)


def _status_label(state: dict) -> str:
    """`_dashboard_status` in the words the page prints (D-95, D-177)."""
    ui = i18n.ui_language(state)
    if phases.is_terminal(str(state.get("current_phase") or "")):
        return phases.label(state.get("current_phase"), ui)
    status = _dashboard_status(state)
    return {
        "running": i18n.t(ui, "ui.machine.status_working"),
        "waiting for you": i18n.t(ui, "ui.machine.status_your_turn"),
        "cancelling": i18n.t(ui, "ui.machine.status_cancelling"),
    }.get(status, status)


def _gate_source_intact(root: Path, relative: str) -> bool:
    """True when one `DASHBOARD_GATE_SOURCES` file holds a JSON object; False for any read error."""
    try:
        return isinstance(state_io.read_json(root / relative), dict)
    except (OSError, ValueError):
        return False


def _gate_questions(state: dict, gate: str) -> list[dict]:
    """The open gate's questions, read-only, in one shape for the page (D-96).

    Text gates reuse `gates.printed_questions` and `gates.OPTION_LETTERS`, so the numbers and the
    letters on the page are the ones the chat printed; the plan gate projects `gates.build_auq`,
    whose options are named by label instead of a letter. Both are capped for the page and both
    degrade to `[]` — an open gate never fails a `next` answer because a source file is unreadable.

    D-103: the chat text of a text gate is a pointer to this card while the page is live, so every
    string here is what the user actually reads and carries the long cap (`_long_text`).

    D-97: a read error is detected instead of being inherited as an empty document. The plan gate
    publishes questions only when `_dashboard_plan` projected a schema-valid `plan.json` (D-90), and
    every gate first checks the files of `DASHBOARD_GATE_SOURCES`; anything else is `[]`.
    """
    work_dir = state.get("work_dir")
    if not work_dir:
        return []
    rows: list[dict] = []
    try:
        root = Path(work_dir)
        for relative in DASHBOARD_GATE_SOURCES.get(gate, ()):
            if not _gate_source_intact(root, relative):
                return []
        if gate == "plan":
            if _dashboard_plan(state) is None:
                return []
            for index, question in enumerate(gates.build_auq(work_dir, state)["questions"], 1):
                rows.append(
                    {
                        "n": index,
                        "header": str(question.get("header") or ""),
                        "text": _long_text(question.get("question")),
                        "options": [
                            {
                                "key": "",
                                "label": _long_text(option.get("label")),
                                "detail": _long_text(option.get("description")),
                            }
                            for option in (question.get("options") or [])[:DASHBOARD_GATE_OPTIONS]
                        ],
                        "default": "",
                    }
                )
        else:
            printed = gates.printed_questions(work_dir, state, gate) or []
            for index, question in enumerate(printed[:DASHBOARD_GATE_QUESTIONS], 1):
                options = (question.get("options") or [])[
                    : min(DASHBOARD_GATE_OPTIONS, len(gates.OPTION_LETTERS))
                ]
                rows.append(
                    {
                        "n": index,
                        "header": "",
                        "text": _long_text(question.get("question")),
                        "options": [
                            {
                                "key": gates.OPTION_LETTERS[position],
                                "label": _long_text(option.get("label")),
                                "detail": _long_text(option.get("description")),
                            }
                            for position, option in enumerate(options)
                        ],
                        "default": _long_text(
                            question.get("default")
                            or question.get("default_assumption_if_skipped")
                            or ""
                        ),
                    }
                )
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return []
    return rows[:DASHBOARD_GATE_QUESTIONS]


def _dashboard_gate(state: dict) -> dict | None:
    """`{phase, phase_label, kind, hint, questions, answer_hint}` at a gate (§2.4), else None.

    D-96 reverses the "no gate text on the page" half of D-89 at the user's request: the questions
    are repeated read-only, with the same numbers and letters the chat printed, so the plan or the
    intake list can be read on the page. The channel does not move — the answer is still typed in
    the chat, and no answer of the user is ever published back.
    """
    phase = str(state.get("current_phase") or "")
    if not phases.is_gate(phase):
        return None
    gate = gates.gate_for_phase(phase) or ""
    kind = KIND_GATE_AUQ if gate == "plan" else KIND_GATE_TEXT
    for row in reversed(state.get("steps") or []):
        if not isinstance(row, dict) or row.get("phase") != phase:
            continue
        if row.get("kind") in (KIND_GATE_TEXT, KIND_GATE_AUQ):
            kind = str(row.get("kind"))
            break
    ui = i18n.ui_language(state)
    return {
        "phase": phase,
        "phase_label": phases.label(phase, ui),
        "kind": kind,
        "hint": i18n.t(ui, "ui.machine.gate_hint"),
        "questions": _gate_questions(state, gate),
        "answer_hint": _dashboard_answer_hint(gate, ui),
    }


def _dashboard_plan(state: dict, ui: str = "en") -> dict | None:
    """`plan.json` as structure for the page (D-89) — None until `planning` wrote the file.

    The same fields `gates.render_plan_digest` reads, in the same order and with the same issue cap
    (`gates.PLAN_DIGEST_MAX_ISSUES`); the digest text itself is not re-rendered here. `layers`
    are the layers of the one run mode (D-242). Nothing in the result carries a machine-generated
    path.

    D-90: only a `plan.json` that satisfies `schemas/plan.schema.json` is projected. A file that is
    unreadable, not JSON or structurally wrong (a half-written plan, a wrong type on any field) is
    reported as `None` — this never raises, so a bad plan cannot break the `next` answer.

    D-172: `memo_language` names the memo language exactly when `gates.memo_language_line`
    would print it — an `en`/`en` task carries no key at all.
    """
    work_dir = state.get("work_dir")
    if not work_dir:
        return None
    try:
        plan = state_io.read_json(Path(work_dir) / gates.PLAN_PATH)
        if not isinstance(plan, dict) or schema.validate(plan, "plan"):
            return None
    except (OSError, ValueError, schema.DependencyMissing):
        return None
    issues = [row for row in (plan.get("issues") or []) if isinstance(row, dict)]
    approval = state.get("plan_approval")
    approval = approval if isinstance(approval, dict) else {}
    # D-101: this section is projected outside the `_dashboard_history` guard, so the one part of
    # it that reads `state` instead of the schema-checked plan carries its own.
    try:
        decision = _plan_decision(state)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
        decision = None
    card: dict = {
        "classification": str(plan.get("classification") or ""),
        "jurisdictions": _dashboard_codes(plan.get("jurisdictions")),
        "complexity": str(plan.get("estimated_complexity") or ""),
        "layers": [
            layer
            for layer in modes.MODES["full"]["researcher_layers"]
            if layer != "doctrine" or plan.get("doctrine_required")
        ],
        "issues": [
            {
                "id": str(issue.get("issue_id") or ""),
                "title": _long_text(issue.get("title")),
                "question": _long_text(issue.get("question")),
                "jurisdictions": _dashboard_codes(issue.get("jurisdictions")),
            }
            for issue in issues[: gates.PLAN_DIGEST_MAX_ISSUES]
        ],
        "approved": str(approval.get("status")) == "approved",
        "decision": decision,
    }
    language_line = gates.memo_language_line(state)
    if language_line:
        memo = i18n.normalize(state.get("language")) or i18n.DEFAULT
        card["memo_language"] = str(i18n.t(ui, f"ui.language_names.{memo}"))
    return card


# --- D-98: the run's own history, one section per tab of the page ---------------------------
# Every section is `None` until the run wrote the data it projects, none of them re-reads a file
# the pipeline does not already own, and none of them raises: `_dashboard_history` is the single
# guard, so a half-written artifact costs the page one card, never the `next` answer (D-97).

DASHBOARD_SOURCE_ITEMS = 40
"""How many frozen sources the page lists; `research/source-pack.json` stays the full record."""

DASHBOARD_TEXT_CHARS = 120
"""Cap on one machine-written string of the page (a source title, `summary.md`)."""

DASHBOARD_LONG_TEXT_CHARS = 2000
"""Cap on one user-facing string (D-103): a question, an option, an answer, an issue title.

D-101 put every free string of the page under `DASHBOARD_TEXT_CHARS`; at 120 characters a legal
issue or an intake question reached the reader cut in half, which is not acceptable for content
written for them. Content the user reads keeps this cap instead, and the short one stays where the
string is machine-written and short by construction.
"""

DASHBOARD_TEXT_ELLIPSIS = "…"
"""What a cut string ends with, so the reader sees it was cut."""

DASHBOARD_CODE_CHARS = 16
"""Cap on one jurisdiction code of the plan section (D-102) — `"EU"`, not a pasted document."""

DASHBOARD_JURISDICTION_CODES = 12
"""How many codes one jurisdiction list of the plan section publishes (D-102)."""

DASHBOARD_HISTORY_ROWS = 12
"""Cap on the intake lists — the same number of rows an open gate publishes."""

_DASHBOARD_REVIEW_SUMMARY_KEYS: dict[str, str] = {
    "clean": "review_no_blockers",
    "blockers": "review_blockers_left",
    "incomplete": "review_incomplete",
}
"""`reviews[].status` -> `ui.machine.review_*` key (D-177)."""

DASHBOARD_REVIEW_SUMMARY: dict[str, str] = {
    status: i18n_en.EN["ui"]["machine"][key]
    for status, key in _DASHBOARD_REVIEW_SUMMARY_KEYS.items()
}
"""`reviews[].status` -> `reviews[].summary`: one static sentence, never a reviewer's own text."""


def _dashboard_text(value: object, limit: int = DASHBOARD_TEXT_CHARS) -> str:
    """One free-text string of a history section: whitespace collapsed, capped, cut marked (D-101).

    Every free string the page publishes goes through here — a question, an answer label, an
    assumption, a source title, an edit request — so no single field of `dashboard_patch` can grow
    without bound and push the document past the size the page subscribes to. A string that was cut
    ends in `DASHBOARD_TEXT_ELLIPSIS`, and the result is never longer than the cap counting it.
    `limit` picks the cap: `DASHBOARD_LONG_TEXT_CHARS` for what the user reads (D-103), the short
    default for machine-written strings, `DASHBOARD_CODE_CHARS` for a jurisdiction code (D-102).
    """
    text = " ".join(str("" if value is None else value).split())
    if len(text) <= limit:
        return text
    return text[: limit - len(DASHBOARD_TEXT_ELLIPSIS)] + DASHBOARD_TEXT_ELLIPSIS


def _long_text(value: object) -> str:
    """`_dashboard_text` at the user-facing cap (D-103): a question, an option, an answer, a title."""
    return _dashboard_text(value, DASHBOARD_LONG_TEXT_CHARS)


def _dashboard_codes(value: object) -> list[str]:
    """`plan.jurisdictions` / `plan.issues[].jurisdictions` as the page may publish them (D-102).

    `schemas/plan.schema.json` bounds neither the length of a code nor how many a list holds, so a
    plan carrying a pasted document instead of `"EU"` used to reach the page whole: 70 000
    characters per code times the length of the list. Non-string entries are dropped, at most
    `DASHBOARD_JURISDICTION_CODES` survive, and each one is cut to `DASHBOARD_CODE_CHARS`.
    """
    rows = value if isinstance(value, list) else []
    codes = [name for name in rows if isinstance(name, str)][:DASHBOARD_JURISDICTION_CODES]
    return [_dashboard_text(name, DASHBOARD_CODE_CHARS) for name in codes]


def _plan_decision(state: dict) -> dict | None:
    """`plan.decision` — the last answer of gate 4, as `gates.commit` recorded it (D-98).

    D-101: the record is checked before it is read — `plan_approval` and its `iterations[]` rows
    have to be objects and `answers` a mapping. Anything else is a history the run did not write in
    the shape `gates.commit` writes it, so the page loses this one line (`decision: null`) and the
    rest of the plan section is published unchanged.

    D-102: the decision is the **last** record, and only the last one. A malformed last record is
    `decision: null`, never a fall back to the record before it — an older answer republished as
    the current decision would show the reader an approval the run has already superseded.
    """
    approval = state.get("plan_approval")
    rows = approval.get("iterations") if isinstance(approval, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    last = rows[-1]
    if not isinstance(last, dict):
        return None
    given = last.get("answers")
    if given is None:
        given = {}
    if not isinstance(given, dict):
        return None
    answers = {str(key): str(value) for key, value in given.items()}
    decision = {
        "action": str(last.get("action") or ""),
        "mode": str(state.get("mode") or "").lower(),
        "at": str(last.get("at") or ""),
    }
    edit = _long_text(answers.get("edit_text"))
    if edit:
        decision["edit_text"] = edit
    return decision


def _answered_gate_result(state: dict, phase: str) -> dict | None:
    """`result_ref.result` of the closed gate step of one phase — what `gates.commit` stored."""
    rows = [row for row in (state.get("steps") or []) if isinstance(row, dict)]
    for row in reversed(rows):
        if row.get("phase") != phase or row.get("kind") not in (KIND_GATE_TEXT, KIND_GATE_AUQ):
            continue
        if row.get("status") in (None, ""):
            continue
        return result_of(row)
    return None


def _intake_answer(question: dict, given: str | None) -> dict:
    """One `intake.questions[]` row: the letter and label chosen, or the assumption applied.

    D-98 reverses the last half of D-96 for this one section at the user's request: the answer the
    user gave is repeated on the page, exactly as `intake/user-facts.md` already records it. A free
    text answer is the user's own words, so it is collapsed and capped like every other free string.
    """
    options = [entry for entry in (question.get("options") or []) if isinstance(entry, dict)]
    text = _long_text(question.get("question"))
    if given is None:
        default = question.get("default") or question.get("default_assumption_if_skipped") or ""
        return {
            "text": text,
            "answer_key": "",
            "answer_label": _long_text(default),
            "answered": False,
        }
    key = given if len(given) == 1 and given in gates.OPTION_LETTERS else ""
    label = given
    if key:
        position = gates.OPTION_LETTERS.index(key)
        label = options[position].get("label") if position < len(options) else given
    return {
        "text": text,
        "answer_key": key,
        "answer_label": _long_text(label),
        "answered": True,
    }


def _dashboard_intake(state: dict) -> dict | None:
    """`intake` — what gate 2 asked and what the run took from the answer (D-98), else None.

    The two halves are the ones `gates.user_facts_markdown` wrote into `intake/user-facts.md`: the
    numbered questions of `gates.intake_questions` and the answers the closed gate step stored, so
    the numbering on the page is the numbering of that file. `assumptions[]` is the "everything
    else is assumed as follows" block of the same gate, with the confidence it printed.
    """
    work_dir = state.get("work_dir")
    answer = _answered_gate_result(state, gates.PHASE_BY_GATE["intake"])
    if not work_dir or answer is None:
        return None
    ui = i18n.ui_language(state)
    root = Path(work_dir)
    if not _gate_source_intact(root, gates.QUESTIONS_PATH):
        return None
    asked, defaulted = gates.intake_questions(root, state)
    answers = {str(key): str(value) for key, value in (answer.get("answers") or {}).items()}
    questions = [
        dict(_intake_answer(question, answers.get(str(index))), n=index)
        for index, question in enumerate(asked[:DASHBOARD_HISTORY_ROWS], 1)
        if isinstance(question, dict)
    ]
    assumptions = []
    for question in defaulted:
        if not isinstance(question, dict):
            continue
        default = question.get("default") or question.get("default_assumption_if_skipped") or ""
        if not default:
            continue
        assumptions.append(
            {
                "text": _long_text(
                    i18n.t(
                        ui,
                        "ui.machine.intake_row",
                        question=question.get("question") or "",
                        default=default,
                    )
                ),
                "confidence": str(question.get("confidence") or ""),
            }
        )
    return {"questions": questions, "assumptions": assumptions[:DASHBOARD_HISTORY_ROWS]}


def _dashboard_sources(state: dict) -> dict | None:
    """`sources` — the frozen pack in three counts and a capped list (D-98), None before the freeze.

    Read from `research/source-pack.json`, the file `sources pack --freeze` publishes, and reduced
    to what identifies a source on a page: its id, its title, its tier and its currency verdict.
    Neither the URL nor any local path of a source is published.
    """
    work_dir = state.get("work_dir")
    if not work_dir:
        return None
    pack = sources.read_pack(Path(work_dir))
    if not isinstance(pack, dict):
        return None
    entries = [row for row in (pack.get("entries") or []) if isinstance(row, dict)]
    counts = {tier: 0 for tier in sources.TIERS}
    for entry in entries:
        tier = str(entry.get("tier") or "")
        if tier in counts:
            counts[tier] += 1
    return {
        "frozen_at": str(pack.get("frozen_at") or ""),
        "counts": counts,
        "items": [
            {
                "id": str(entry.get("source_id") or ""),
                "title": _dashboard_text(entry.get("title")),
                "tier": str(entry.get("tier") or ""),
                "status": str(entry.get("currency_status") or ""),
            }
            for entry in entries[:DASHBOARD_SOURCE_ITEMS]
        ],
    }


def _reviewer_label(kind: str, ui: str = "en") -> str:
    """`citations` -> `Citation auditor` — the reviewer kind in the words of D-95 (D-177)."""
    name = dispatch.REVIEWER_AGENTS.get(kind, kind)
    key = _DASHBOARD_AGENT_KEYS.get(name)
    if key is not None:
        return i18n.t(ui, f"ui.machine.{key}")
    return DASHBOARD_AGENT_LABELS.get(name) or str(name).replace("-", " ").capitalize()


def _dashboard_reviews(state: dict) -> list[dict] | None:
    """`reviews` — one row per `state.iterations[]` record (D-98), None before the first round.

    `blockers` is `substance_blockers + form_blockers`, the two disjoint halves `mf review
    aggregate` counts (`deterministic_blockers` is a provenance of the same issues, not a third
    group), and `status` is read off that count and `failed_reviewers[]` alone — the branch
    `revision.py` picks depends on budgets the page does not know and is not repeated here.
    """
    rows = [row for row in (state.get("iterations") or []) if isinstance(row, dict)]
    if not rows:
        return None
    ui = i18n.ui_language(state)
    versions = {
        str(row.get("sha256")): int(row.get("version") or 0)
        for row in (state.get("draft_versions") or [])
        if isinstance(row, dict)
    }
    reviews: list[dict] = []
    for row in rows:
        failed = sorted(str(kind) for kind in (row.get("failed_reviewers") or []))
        blockers = int(row.get("substance_blockers") or 0) + int(row.get("form_blockers") or 0)
        status = "incomplete" if failed else ("blockers" if blockers else "clean")
        reviews.append(
            {
                "iteration": int(row.get("iteration") or 0),
                "draft_version": versions.get(str(row.get("draft_sha"))),
                "status": status,
                "blockers": blockers,
                "failed_reviewers": [_reviewer_label(kind, ui) for kind in failed],
                "summary": _long_text(
                    i18n.t(ui, f"ui.machine.{_DASHBOARD_REVIEW_SUMMARY_KEYS[status]}")
                ),
            }
        )
    return reviews


def _dashboard_memo(state: dict) -> dict | None:
    """`memo` — the deliverable, the outcome and `summary.md` (D-98), None until `finalize` ran.

    The section waits for the deliverable itself, not for `final_status`: the verdict of the review
    (`approved_on_v1`) is written while the run is still drafting, and a Memo tab without a memo
    behind it is a promise the run has not kept yet. `deliverable` is the one absolute path of the
    patch (D-88); `summary_path` is the name `finalize` writes in the same folder, published only
    once that file exists.
    """
    deliverable = _dashboard_deliverable(state)
    if not deliverable:
        return None
    final_status = str(state.get("final_status") or "")
    work_dir = state.get("work_dir")
    summary = ""
    if work_dir and (Path(work_dir) / finalize.SUMMARY_MD).is_file():
        summary = finalize.SUMMARY_MD
    return {
        "deliverable": deliverable,
        "final_status": final_status,
        "summary_path": _dashboard_text(summary),
        # D-109: the second (and last) absolute path of the patch — the folder the user opens.
        "published_to": _dashboard_text(published_to(state)),
    }


def _dashboard_history(state: dict) -> dict:
    """The four history sections; a section that cannot be built is `None` and never raises (D-97)."""
    built: dict[str, object] = {}
    for name, builder in (
        ("intake", _dashboard_intake),
        ("sources", _dashboard_sources),
        ("reviews", _dashboard_reviews),
        ("memo", _dashboard_memo),
    ):
        try:
            built[name] = builder(state)
        except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
            built[name] = None
    return built


def dashboard_patch(state: dict) -> dict:
    """The whole document the page renders — recomputed from `state` on every `next` (§7.5)."""
    progress = state.get("progress") or {}
    ui = i18n.ui_language(state)
    history = _dashboard_history(state)
    steps = _latest_steps(state)
    agents = []
    for entry in progress.get("active") or []:
        # D-176b: "Running now" is displayed prose, so it names the agent and its layer from the
        # same `ui.machine.agent_*`/`slot_*` tables the timeline reads — never the internal
        # `agent_type` and the raw phase of the step. An agent whose slot is not a research layer
        # has no second part: `_agent_layer` answers `""` and the join drops it.
        parts = (_agent_name(entry, ui), _agent_layer(entry, ui))
        agents.append(
            {
                "slot": str(entry.get("slot") or ""),
                "description": " · ".join(part for part in parts if part),
                "since": str(entry.get("started_at") or ""),
            }
        )
    return {
        "task_id": str(state.get("task_id") or ""),
        "query": " ".join(str(state.get("user_query") or "").split()),
        "mode": state.get("mode") or "",
        "phase": str(state.get("current_phase") or ""),
        "phase_label": _phase_label(state),
        "phase_no": int(progress.get("position") or 0),
        "phase_total": int(progress.get("total") or 0),
        "chat_line": _dashboard_chat_line(state, steps, ui),
        "status": _dashboard_status(state),
        "status_label": _status_label(state),
        "steps_done": len([row for row in steps if row.get("status") not in (None, "")]),
        "steps_total": len(steps),
        "agents_running": agents,
        "timeline": [
            {
                "ts": str(row.get("issued_at") or ""),
                "text": _step_sentence(row, ui),
                "state": _step_state(row),
            }
            for row in steps[-DASHBOARD_TIMELINE:]
        ],
        "banners": _dashboard_banners(state),
        "gate": _dashboard_gate(state),
        "plan": _dashboard_plan(state, ui),
        "intake": history["intake"],
        "sources": history["sources"],
        "reviews": history["reviews"],
        "memo": history["memo"],
        "deliverable": _dashboard_deliverable(state),
        "labels": i18n.node(ui, "ui.dashboard"),
        "updated_at": events.utc_now(),
    }


def dashboard_block(work_dir: Path, state: dict) -> dict | None:
    """The `dashboard` key of a `next` answer: publish once, then one `write_db` per step (§7.5).

    D-87: this is the only extra orchestrator action of `dashboard: on` (G8 = 2). Nothing here can
    fail the pipeline — the CLI never touches `Artifact`, and a host without it answers
    `task dashboard --unavailable`, which raises the banner and silences this block for good.
    D-88: that decline is final, so it is read before the URL branch — a run that published a page
    and then reported the tool unavailable stops carrying the block, `write_db` included.
    D-159: the document travels as `<work_dir>/dashboard/patch.json` (the router passes its
    `file_path` to `Artifact` instead of retyping it inline), pinned to the version the previous
    write returned — the first write after `publish` carries no `if_version` (the router holds it).
    """
    if not dashboard_enabled(state) or dashboard_declined(state):
        return None
    url = dashboard_url(state)
    if url:
        target = Path(work_dir) / Path(*DASHBOARD_PATCH_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, dashboard_patch(state))
        return {
            "write_db": {
                "url": url,
                "collection": DASHBOARD_COLLECTION,
                "doc_id": DASHBOARD_DOC_ID,
                "file_path": str(target.absolute()),
            }
        }
    task_id = str(state.get("task_id") or Path(work_dir).name)
    ui = i18n.ui_language(state)
    return {
        "publish": {
            "file": dispatch.lib_path(*DASHBOARD_HTML),
            "title": i18n.t(ui, "ui.machine.title", task_id=task_id),
            "description": i18n.t(ui, "ui.machine.description"),
            "capabilities": {"db": {}},
            "favicon": "⚖️",
        },
        "then": (
            f'"{dispatch.mf_path()}" task dashboard '
            f'--workdir "{Path(work_dir).absolute()}" --url <URL>'
        ),
    }


def with_dashboard(work_dir: Path, response: dict) -> dict:
    """Attach the §7.5 block to a `next` answer; with `dashboard: off` the answer is untouched."""
    if not isinstance(response, dict) or response.get("errors"):
        return response
    state = state_io.read_state_or_none(work_dir)
    block = dashboard_block(work_dir, state) if state else None
    if block:
        response["dashboard"] = block
    return response


def plan_gate_text(work_dir: Path, state: dict) -> str:
    """`text` of gate 4 — the full digest in chat, three lines when the page is live (D-86, D-94).

    A published dashboard already renders the plan issue by issue, so repeating the digest in chat
    is noise: the gate then names the page, the file and the shape of the plan and stops there.
    `text_fallback` (the text channel, where there is no page in front of the user) always carries
    the full digest. A malformed or missing `plan.json` degrades to zero issues and `unknown`
    complexity — this never raises, exactly like `_dashboard_plan` (D-90). D-172 adds the
    `Memo language` line to the pointer while the memo language can still be changed.
    """
    ui = i18n.ui_language(state)
    url = dashboard_url(state)
    if not url:
        return gates.render_plan_digest(work_dir, state)
    try:
        plan = state_io.read_json(Path(work_dir) / gates.PLAN_PATH)
    except (OSError, ValueError):
        plan = None
    if not isinstance(plan, dict):
        plan = {}
    rows = plan.get("issues")
    issues = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    complexity = str(plan.get("estimated_complexity") or i18n.t(ui, "ui.gates.plan_digest_unknown"))
    # D-147: the page renders the plan, not the preflight, so the blocked portals stay in chat.
    access = preflight.source_access_block(work_dir, state, ui=i18n.ui_language(state))
    lines = [
        i18n.t(ui, "ui.machine.plan_gate_dashboard", url=url),
        i18n.t(
            ui,
            "ui.machine.plan_gate_file",
            path=gates.PLAN_PATH,
            work_dir=Path(work_dir).absolute(),
        ),
    ]
    # D-172: the memo language is still changeable here, so the pointer names it too.
    language_line = gates.memo_language_line(state)
    if language_line:
        lines.append(language_line)
    shape = "plan_gate_shape_one" if len(issues) == 1 else "plan_gate_shape_many"
    lines.append(
        i18n.t(
            ui,
            f"ui.machine.{shape}",
            count=len(issues),
            complexity=complexity,
        )
    )
    if access:
        lines.append(access)
    return "\n".join(lines) + "\n"


def _gate_reply_lines(text: str, numbered: bool) -> list[str]:
    """The reply-format lines of a rendered gate text, taken from it verbatim (D-103).

    A pointer has to accept exactly the replies the full text accepts, so the lines are lifted out
    of `gates.render` instead of being re-worded here — `mf gate parse` is unchanged and never
    reads either. Two lines make the format: for a gate that printed numbered questions the
    instruction line that ends in a colon (``Please answer, in the form `1A 2C 3: free text`:``),
    and for every gate the closing block of consecutive non-empty lines — the
    `proceed`/`continue`/`cancel` prompt, which is the whole thing a gate without numbered
    questions asks for.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    form = [line for line in lines[1:] if line.strip().endswith(":")][:1] if numbered else []
    tail: list[str] = []
    for line in reversed(lines):
        if not line.strip():
            break
        tail.insert(0, line)
    return form + tail


def gate_text(work_dir: Path, state: dict, gate: str, full: str) -> str:
    """`text` of a text gate: the full prompt, or a pointer to the live page (D-103).

    The rule D-94 gave gate 4, for the other four gates: with a page in front of the user the chat
    says where the questions are, how many there are and how to answer them, and the questions
    themselves are read on the dashboard card (`_gate_questions` publishes them in full). `full` —
    what `gates.render` produced — stays the `text_fallback` of the answer and is the `text` itself
    whenever there is no page. Gate 4 keeps the full digest here: `plan_gate_text` is the pointer
    of its AUQ channel and D-94 binds its text channel to the digest.
    """
    if gate == "plan" or not dashboard_url(state):
        return full
    ui = i18n.ui_language(state)
    try:
        printed = gates.printed_questions(work_dir, state, gate)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        printed = None
    count = len(printed) if printed else 0
    label = phases.label(gates.PHASE_BY_GATE.get(gate, ""), ui) or gate
    head = i18n.t(ui, "ui.machine.gate_pointer", label=label, url=dashboard_url(state))
    if count:
        pointer = "gate_pointer_one" if count == 1 else "gate_pointer_many"
        head = i18n.t(
            ui, f"ui.machine.{pointer}", label=label, count=count, url=dashboard_url(state)
        )
    return "\n".join([head] + _gate_reply_lines(full, bool(count))) + "\n"


# --- completion checks (§3.1) ---------------------------------------------


def marker_path(work_dir: Path, step_id: str, attempt: int, slot: str) -> Path:
    """`steps/<step_id>/a<attempt>/<slot>/done.json` (§2.2)."""
    return stepctx.step_dir(work_dir, step_id, attempt, slot) / "done.json"


def is_writer(agent: dict) -> bool:
    """True for a `memo-writer` slot — the only agent whose output is compared with a seed (D-53)."""
    return str(agent.get("agent_type") or "").split(":", 1)[-1] == "memo-writer"


def check_completion(work_dir: Path, state: dict, row: dict, agent: dict) -> dict:
    """Marker, input sha, output existence, schema and output sha for one slot (§3.1)."""
    step_id = str(row["step_id"])
    attempt = int(row["attempt"])
    slot = str(agent["slot"])
    writer = is_writer(agent)
    errors: list[str] = []
    outputs = list(agent.get("outputs") or row.get("expected_outputs") or [])

    marker_file = marker_path(work_dir, step_id, attempt, slot)
    if not marker_file.is_file():
        return {"ok": False, "errors": ["missing_done_marker"], "outputs": [], "no_change": False}
    try:
        marker = state_io.read_json(marker_file)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"unreadable_done_marker: {exc}"], "outputs": [], "no_change": False}
    errors.extend(schema.validate(marker, "done-marker"))
    if not errors:
        if str(marker.get("step_id")) != step_id or int(marker.get("attempt") or 0) != attempt:
            errors.append("marker_identity_mismatch")
        if str(marker.get("slot")) != slot:
            errors.append("marker_slot_mismatch")

    declared = dict(row.get("inputs") or {})
    marker_inputs = dict(marker.get("input_sha") or {})
    for relative, sha in declared.items():
        if marker_inputs.get(relative) != sha:
            errors.append(
                f"input_sha_mismatch: {relative} (declared input changed during the step"
                " — agents must not run sources verify/liveness/register on their own input)"
            )

    marker_outputs = dict(marker.get("output_sha") or {})
    if not outputs:
        # §3.1 rule 3: an empty `expected_outputs` never autocloses.
        errors.append("no_expected_outputs")

    resolved: list[dict] = []
    no_change = False
    for entry in outputs:
        work_path = str(entry.get("work_path"))
        target = work_dir / work_path
        if not target.is_file():
            errors.append(f"missing_output: {work_path}")
            continue
        actual = state_io.sha256_file(target)
        if marker_outputs.get(work_path) != actual:
            errors.append(f"output_sha_mismatch: {work_path}")
        schema_name = entry.get("schema")
        if schema_name:
            try:
                document = state_io.read_json(target)
            except (OSError, ValueError) as exc:
                errors.append(f"invalid_json[{work_path}]: {exc}")
                continue
            errors.extend(f"{work_path}: {message}" for message in schema.validate(document, schema_name))
        elif target.suffix == ".md":
            text = target.read_text(encoding="utf-8-sig")
            if not text.strip() or "\n## " not in "\n" + text:
                errors.append(f"draft_without_sections: {work_path}")
        # §3.1 seed comparison: the canonical file still holds the seed until `report` publishes.
        # D-53: only `memo-writer` edits a seed in place; for every other agent an output that
        # happens to repeat the previous canonical bytes is an ordinary `ok`.
        if writer:
            seed = stepctx.published_sha(state, str(entry.get("canonical_path") or entry.get("canonical")))
            if seed is not None and seed == actual:
                no_change = True
        resolved.append({**entry, "agent_sha256": actual})

    return {"ok": not errors, "errors": errors, "outputs": resolved, "no_change": no_change}


NO_CHANGE_PHASES = ("drafting", "client_readiness")
"""Phases whose writer dispatch edits the current version in place, so `no_change` is legitimate (§3.1)."""


def slot_status(row: dict, completion: dict) -> str:
    """`ok`, `no_change` or `fail` for one completed slot (§3.1: v1 and vN never accept `no_change`)."""
    if not completion["no_change"]:
        return "ok"
    return "no_change" if row.get("phase") in NO_CHANGE_PHASES else "fail"


def publish_outputs(work_dir: Path, step_id: str, outputs: list[dict]) -> list[dict]:
    """Publish every attempt output onto its canonical path (§2.2: only publication creates them)."""
    entries = []
    for entry in outputs:
        entries.append(
            stepctx.publish_file(
                work_dir,
                entry["work_path"],
                entry["canonical_path"],
                by="step",
                step_id=step_id,
            )
        )
    return entries


def accept_slot(work_dir: Path, row: dict, completion: dict) -> tuple[str, list[dict]]:
    """D-53: acceptance first, publication second — the one order `report` and autoclose share.

    A slot that ends `fail` (a writer that returned the seed unchanged) publishes nothing, so the
    canonical file keeps the bytes the previous accepted step put there.
    """
    status = slot_status(row, completion)
    if status == "fail":
        return status, []
    return status, publish_outputs(work_dir, str(row["step_id"]), completion["outputs"])


# --- script replay (§2.2 «сначала закрыть/переиграть незакрытые script-шаги») ---


SELF_CLOSING: tuple[str, ...] = (
    "sufficiency.route",
    "sources.pack",
    "sources.liveness",
    "sources.verify",
    "draft.anchor",
    "draft.lint",
    "draft.audit-citations",
    "draft.finish",
    "render.research",
    "render.source-pack",
    "render.mediator",
    "render.sufficiency",
    "review.aggregate",
    "review.mediator-from-issues",
    "revision.next",
    "docx.render",
    "docx.validate",
    "finalize",
)
"""Script commands that take `--step/--attempt` and close the step themselves (§3.1, D-28).

Since D-28 there is no other kind: `sources liveness|verify` and `docx validate` used to be closed
by a completion predicate of this module, which second-guessed whether the orchestrator had already
run them; they now carry the identity like every other script step and a closed identity replays
from the stored result (D-40).
"""


def run_command(tokens: list[str]) -> dict:
    """Execute one `mf` command in-process (used by replay and by `probe dry-run`)."""
    from . import cli

    parser = cli.build_parser()
    args = parser.parse_args([str(token) for token in tokens[1:]])
    args.human = False
    return args.func(args)


def replay_script(work_dir: Path, state: dict, row: dict) -> dict:
    """Close an unclosed `script` step by re-running its command (§2.2; identity replay is D-40)."""
    step_id = str(row["step_id"])
    attempt = int(row["attempt"])
    result = run_command(list(row["command"]))
    refreshed = state_io.read_state(work_dir)
    if (step_row(refreshed, step_id, attempt) or {}).get("status") in (None, ""):
        # A business error left the step open: the machine closes it.
        stepctx.close_step(
            work_dir,
            step_id,
            attempt,
            result if isinstance(result, dict) else {"result": result},
            kind=KIND_SCRIPT,
            phase=row.get("phase"),
            status="fail" if isinstance(result, dict) and result.get("errors") else "ok",
            reason=str(row.get("reason") or REASON_INITIAL),
            command=list(row.get("command") or ()),
        )
    return result


# --- budgets (§2.2) -------------------------------------------------------


def budget_for(state: dict, row: dict, slots: list[str] | None = None) -> dict:
    """Which `attempts` counter a `reason: failure` retry of this step consumes (§2.2)."""
    step_id = str(row["step_id"])
    if row.get("kind") == KIND_INLINE:
        return {"name": "inline_llm_retry", "key": step_id, "limit": limits.MAX_INLINE_LLM_RETRY}
    names = set(agent_names(row))
    if names == {"legal-researcher"}:
        return {"name": "research_dispatch_retry", "key": None, "limit": limits.MAX_RESEARCH_DISPATCH_RETRY}
    if names & set(dispatch.REVIEWER_AGENTS.values()):
        # §2.2 `reviewer_json_retry{iteration,kind}`: one counter per failed kind, never one per
        # group. D-55 makes `mf review aggregate` its only writer, so the machine reads the counter
        # and adds the failure retries it issued for that `(iteration, kind)` itself (D-65).
        iteration = int(state.get("current_iteration") or 1)
        kinds = sorted(slots or [str(agent.get("slot")) for agent in (row.get("agents") or [])])
        return {
            "name": "reviewer_json_retry",
            "key": None,
            "iteration": iteration,
            "kinds": kinds,
            "spend": False,
            "limit": limits.MAX_REVIEWER_JSON_RETRY,
        }
    return {"name": "single_dispatch_retry", "key": step_id, "limit": limits.MAX_SINGLE_DISPATCH_RETRY}


def reviewer_failure_attempts(state: dict, iteration: int, kind: str) -> int:
    """`reason: failure` attempts the machine issued for one reviewer kind of one iteration (D-65).

    D-69: the count itself lives in `review.py`, so that `mf review aggregate` and the machine read
    one and the same `reviewer_json_retry{iteration,kind}` budget instead of two halves of it.
    """
    return review.failure_retry_attempts(state, iteration, kind)


def budget_used(state: dict, budget: dict, slot: str | None = None) -> int:
    """Current value of one budget counter; a read-only budget also counts its own retries (D-55).

    D-65: `reviewer_json_retry` is read for exactly one `<iteration>:<kind>` key — never as a
    maximum across the kinds of one dispatch, which would spend one kind's budget on another.
    D-69: that one key is counted by `review.json_retry_used`, the same function `aggregate` uses.
    """
    attempts = state.get("attempts") or {}
    value = attempts.get(budget["name"])
    kinds = budget.get("kinds")
    if kinds is not None:
        iteration = int(budget.get("iteration") or 1)
        kind = str(slot if slot is not None else (kinds[0] if kinds else ""))
        return review.json_retry_used(state, iteration, kind)
    if budget["key"] is None:
        return int(value or 0)
    return int((value or {}).get(budget["key"], 0))


def budget_slots_left(state: dict, budget: dict, slots: list[str]) -> list[str]:
    """The slots of a failed dispatch that may still be re-issued (§2.2, D-65).

    A per-kind budget is decided slot by slot; every other budget belongs to the dispatch as a
    whole, so it allows either all of the pending slots or none of them.
    """
    limit = int(budget["limit"])
    if budget.get("kinds") is None:
        return list(slots) if budget_used(state, budget) < limit else []
    return [slot for slot in slots if budget_used(state, budget, slot) < limit]


def spend_budget(state: dict, budget: dict) -> None:
    """Increment one budget counter in place (called inside a `write_state` mutator)."""
    if not budget.get("spend", True):
        return  # D-55: this counter has another owner; the machine only reads it.
    attempts = state.setdefault("attempts", {})
    if budget["key"] is None:
        attempts[budget["name"]] = int(attempts.get(budget["name"]) or 0) + 1
        return
    counters = attempts.setdefault(budget["name"], {})
    counters[budget["key"]] = int(counters.get(budget["key"], 0)) + 1


# --- issuing --------------------------------------------------------------


def _state_outputs(outputs: list[dict]) -> list[dict]:
    return [
        {
            "canonical_path": entry["canonical"],
            "work_path": entry["work_path"],
            "schema": entry.get("schema"),
        }
        for entry in outputs
    ]


def _response_outputs(outputs: list[dict]) -> list[dict]:
    return [
        {
            "canonical": entry.get("canonical") or entry.get("canonical_path"),
            "work_path": entry["work_path"],
            "schema": entry.get("schema"),
        }
        for entry in outputs
    ]


def mf_command(work_dir: Path, step_id: str, attempt: int, *tokens: str) -> list[str]:
    """`command[]` of a script step: the absolute `mf` wrapper and the identity (§3.1, D-28)."""
    return [
        dispatch.mf_path(),
        *tokens,
        "--workdir",
        str(Path(work_dir).absolute()),
        "--step",
        step_id,
        "--attempt",
        str(int(attempt)),
    ]


def step_loop_exit(work_dir: Path, state: dict, step_id: str, spec: dict) -> dict | None:
    """D-58: one `step_id` is issued at most `limits.MAX_STEP_ATTEMPTS` times, then the run exits.

    The general guard behind the input-sha rule of §2.2: whatever makes a step repeat — a `rerun`
    whose inputs really do change on every pass, a planner that keeps choosing the same branch — the
    run leaves through the always-deliver exit (`finalize --reason step_loop`, M9) instead of
    spinning until the orchestrator's own loop limit stops it. The exit itself is a script step, so
    `finalize` is the one command the guard never blocks.
    """
    if command_key(spec.get("command")) == "finalize":
        return None
    if len(stepctx.steps_for(state, step_id)) < limits.MAX_STEP_ATTEMPTS:
        return None
    return finalize_step(work_dir, state, STEP_LOOP) or transition(work_dir, state, "failed")


def issue(
    work_dir: Path,
    state: dict,
    spec: dict,
    *,
    step_id: str | None = None,
    attempt: int = 1,
    reason: str = REASON_INITIAL,
    generation: int = 0,
) -> dict:
    """Append a `steps[]` record, write `progress`, emit `step_issued` and return the `next` payload."""
    step_id = step_id or next_step_id(state)
    looping = step_loop_exit(work_dir, state, step_id, spec)
    if looping is not None:
        return looping
    phase = str(spec.get("phase") or state.get("current_phase"))
    kind = spec["kind"]
    row: dict = {
        "step_id": step_id,
        "kind": kind,
        "phase": phase,
        "issued_at": events.utc_now(),
        "attempt": int(attempt),
        "reason": reason,
        "status": None,
    }
    response: dict = {"step_id": step_id, "kind": kind, "phase": phase, "attempt": int(attempt), "reason": reason}

    if kind == KIND_DISPATCH:
        agents = spec["agents"]
        row["agents"] = [
            {
                "slot": agent["slot"],
                "agent_type": agent["subagent_type"],
                "attempt": int(attempt),
                "status": None,
                "reported_at": None,
                "payload_ref": None,
                "outputs": _state_outputs(agent["expected_outputs"]),
            }
            for agent in agents
        ]
        row["expected_outputs"] = [
            entry for agent in row["agents"] for entry in agent["outputs"]
        ]
        row["inputs"] = dict(spec.get("inputs") or {})
        response["parallel"] = len(agents) > 1
        response["agents"] = [
            {
                "slot": agent["slot"],
                "subagent_type": agent["subagent_type"],
                "model": agent["model"],
                "description": agent["description"],
                "prompt": agent["prompt"],
                "expected_outputs": _response_outputs(agent["expected_outputs"]),
            }
            for agent in agents
        ]
    elif kind == KIND_SCRIPT:
        row["command"] = list(spec["command"])
        response["command"] = list(spec["command"])
        if spec.get("inputs_sha") is not None:
            row["inputs_sha"] = dict(spec["inputs_sha"])
    elif kind == KIND_INLINE:
        row["expected_outputs"] = _state_outputs(spec["expected_outputs"])
        response["instruction"] = spec["instruction"]
        response["write_to"] = spec["expected_outputs"][0]["work_path"]
        response["schema"] = f"schemas/{spec['expected_outputs'][0]['schema']}.schema.json"
        response["expected_outputs"] = _response_outputs(spec["expected_outputs"])
    elif kind == KIND_GATE_TEXT:
        row["generation"] = int(generation)
        response["generation"] = int(generation)
        response["text"] = spec["text"]
        if spec.get("text_fallback"):
            # D-103: with a live page `text` is a pointer, so the full prompt travels here.
            response["text_fallback"] = spec["text_fallback"]
        response["end_turn"] = True
    elif kind == KIND_GATE_AUQ:
        row["generation"] = int(generation)
        response["generation"] = int(generation)
        response["questions"] = spec["questions"]
        response["text"] = spec["text"]
        response["text_fallback"] = spec["text_fallback"]

    if spec.get("chat_line"):
        response["chat_line"] = spec["chat_line"]

    def mutator(current: dict) -> None:
        steps = [item for item in (current.get("steps") or []) if isinstance(item, dict)]
        steps = [
            item
            for item in steps
            if not (item.get("step_id") == step_id and int(item.get("attempt") or 1) == int(attempt))
        ]
        for item in steps:
            if item.get("step_id") == step_id and int(item.get("attempt") or 1) < int(attempt):
                item["superseded"] = True
        steps.append(row)
        current["steps"] = steps
        if spec.get("mutate"):
            spec["mutate"](current)
        current["progress"] = build_progress(work_dir, current)
        if spec.get("chat_line"):
            current["progress"]["last_line"] = spec["chat_line"]

    state_io.write_state(work_dir, mutator)
    events.append_event(
        work_dir,
        "step_issued",
        "cli",
        {
            "step_id": step_id,
            "attempt": int(attempt),
            "kind": kind,
            "reason": reason,
            "agents": [agent["slot"] for agent in spec.get("agents", [])],
        },
        phase=phase,
        step_id=step_id,
    )
    return response


def record_publications(
    current: dict, published: list[dict] | tuple[dict, ...], *, merge: bool = False
) -> None:
    """The single bookkeeping path for everything a dispatch published (D-59).

    `report` and the autoclose branch of `next` are two call sites of one protocol, so the draft
    accounting of §2.2 (`current_draft_path`, `current_draft_sha`, `draft_versions[]`,
    `current_iteration`) is written here for both. `merge` additionally folds the entries into
    `published[]`: the branches that keep the step open write the state themselves, while
    `close_step` leaves that half to `stepctx`.
    """
    entries = list(published)
    if merge:
        stepctx.merge_published(current, entries)
    _record_draft_publication(current, entries)


def close_step(
    work_dir: Path,
    row: dict,
    result: dict,
    *,
    status: str = "ok",
    mutate=None,
    published: list[dict] | tuple[dict, ...] = (),
    agents: list[dict] | None = None,
) -> None:
    """Close a machine-owned step (dispatch, inline-llm, gate) through `stepctx` (D-40, §0.3a).

    There is one implementation of the step protocol and it lives in `stepctx.close_step`: it
    re-checks the identity under `state.lock` and publishes, closes and mutates in a single state
    write. This wrapper only adds what a machine-owned step carries on top — the `agents[]`
    snapshot, the draft bookkeeping of every published output (D-59) and the recomputed
    `progress` — and hands them all to that one writer as its `mutate`.
    """
    step_id = str(row["step_id"])
    attempt = int(row["attempt"])

    def mutator(current: dict) -> None:
        if agents is not None:
            for item in current.get("steps") or []:
                if item.get("step_id") == step_id and int(item.get("attempt") or 1) == attempt:
                    item["agents"] = agents
        record_publications(current, published)
        if mutate is not None:
            mutate(current)
        current["progress"] = build_progress(work_dir, current)

    stepctx.close_step(
        work_dir,
        step_id,
        attempt,
        result,
        kind=str(row.get("kind") or KIND_DISPATCH),
        phase=row.get("phase"),
        status=status,
        reason=str(row.get("reason") or REASON_INITIAL),
        published=list(published),
        mutate=mutator,
    )


# --- open-step handling in `next` -----------------------------------------


def _autoclose_slots(work_dir: Path, state: dict, row: dict) -> tuple[list[dict], list[dict]]:
    """Autoclose every slot with a valid marker and outputs; returns `(agents, published)` (§3.1 rule 3)."""
    agents = [dict(agent) for agent in (row.get("agents") or [])]
    published: list[dict] = []
    for agent in agents:
        if agent.get("status") not in (None, ""):
            continue
        completion = check_completion(work_dir, state, row, agent)
        if not completion["ok"]:
            continue
        # D-53: the same acceptance -> publication order `report` uses; a `fail` never publishes.
        status, entries = accept_slot(work_dir, row, completion)
        published.extend(entries)
        agent["outputs"] = completion["outputs"]
        agent["status"] = status
        agent["reported_at"] = events.utc_now()
        events.append_event(
            work_dir,
            "step_autoclosed",
            "cli",
            {"step_id": row["step_id"], "attempt": row["attempt"], "slot": agent["slot"]},
            phase=row.get("phase"),
            step_id=str(row["step_id"]),
        )
    return agents, published


def resume_dispatch(work_dir: Path, state: dict, row: dict) -> dict | None:
    """Autoclose, close or reissue an open dispatch step; None means «keep planning» (§3.1 rule 3)."""
    agents, published = _autoclose_slots(work_dir, state, row)
    pending = [agent for agent in agents if agent.get("status") in (None, "", "fail")]
    if not pending:
        status = "no_change" if all(a.get("status") == "no_change" for a in agents) else "ok"
        close_step(
            work_dir,
            row,
            {"slots": {a["slot"]: a["status"] for a in agents}},
            status=status,
            published=published,
            agents=agents,
        )
        return None

    failed = [agent for agent in pending if agent.get("status") == "fail"]
    reason = REASON_FAILURE if failed else REASON_RECOVERY
    budget = budget_for(state, row, [str(agent["slot"]) for agent in pending])
    if reason == REASON_FAILURE:
        # D-65: every reviewer kind spends its own `<iteration>:<kind>` budget, so a kind that has
        # one left is re-issued even when another has none; `review aggregate` stubs the rest.
        allowed = set(budget_slots_left(state, budget, [str(agent["slot"]) for agent in pending]))
        pending = [agent for agent in pending if str(agent["slot"]) in allowed]
        if not pending:
            close_step(
                work_dir,
                row,
                {
                    "slots": {a["slot"]: a.get("status") for a in agents},
                    "budget_exhausted": budget["name"],
                },
                status="fail",
                published=published,
                agents=agents,
            )
            return None

    attempt = int(row["attempt"]) + 1
    specs = [
        dispatch._spec_from_step(work_dir, state, row, agent)  # noqa: SLF001 - one rebuild path
        for agent in pending
    ]
    for spec_row, agent in zip(specs, pending):
        spec_row["extra"]["retry_errors"] = str(agent.get("payload_ref") or "none")
    progress = state.get("progress") or {}
    rendered = dispatch.render_agents(
        work_dir,
        state,
        step_id=str(row["step_id"]),
        attempt=attempt,
        specs=specs,
        position=int(progress.get("position") or 0),
        total=int(progress.get("total") or 0),
    )
    inputs = {key: value for key, value in (row.get("inputs") or {}).items()}
    for agent in rendered:
        for entry in agent["expected_outputs"]:
            _seed_for(work_dir, state, row, agent["slot"], entry)

    def mutate(current: dict) -> None:
        record_publications(current, published, merge=True)
        for item in current.get("steps") or []:
            if item.get("step_id") == row["step_id"] and int(item.get("attempt") or 1) == int(row["attempt"]):
                item["agents"] = agents
                item["superseded"] = True
        if reason == REASON_FAILURE:
            spend_budget(current, budget)

    spec = {
        "kind": KIND_DISPATCH,
        "phase": row.get("phase"),
        "agents": rendered,
        "inputs": inputs,
        "mutate": mutate,
        "chat_line": _chat(state, f"re-issued {len(pending)} slot(s) ({reason})"),
    }
    return issue(
        work_dir,
        state,
        spec,
        step_id=str(row["step_id"]),
        attempt=attempt,
        reason=reason,
    )


def _seed_for(work_dir: Path, state: dict, row: dict, slot: str, entry: dict) -> str | None:
    """Re-create the writer's working copy for a retried attempt and return its sha (§3.1 seed)."""
    canonical = entry.get("canonical") or entry.get("canonical_path")
    if slot != "writer" or not canonical:
        return None
    source = work_dir / str(canonical)
    if not source.is_file():
        return None
    target = work_dir / entry["work_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = source.read_bytes()
    state_io.write_bytes_atomic(target, payload)
    return state_io.sha256_bytes(payload)


def resume_inline(work_dir: Path, state: dict, row: dict) -> dict | None:
    """Reissue an inline-llm step whose file is missing; publish and close it when it is there."""
    outputs = row.get("expected_outputs") or []
    ready = []
    for entry in outputs:
        target = work_dir / str(entry["work_path"])
        if not target.is_file():
            return _reissue_inline(work_dir, state, row)
        try:
            document = state_io.read_json(target)
        except (OSError, ValueError):
            return _reissue_inline(work_dir, state, row)
        if entry.get("schema") and schema.validate(document, entry["schema"]):
            return _reissue_inline(work_dir, state, row)
        ready.append(entry)
    published = publish_outputs(work_dir, str(row["step_id"]), ready)
    close_step(
        work_dir, row, {"published": [entry["canonical_path"] for entry in published]}, published=published
    )
    return None


def inline_errors(state: dict, step_id: str, attempt: int) -> list[str]:
    """Schema errors the previous attempt of an inline-llm step was rejected with (§2.2, §3.1)."""
    previous = step_row(state, step_id, int(attempt) - 1)
    if previous is None or previous.get("status") != "fail":
        return []
    return [str(message) for message in (result_of(previous).get("errors") or [])]


def _inline_spec_for(work_dir: Path, state: dict, row: dict, attempt: int) -> dict | None:
    """The inline-llm spec of one attempt, carrying the errors the previous one was rejected with."""
    step_id = str(row["step_id"])
    spec = inline_spec(work_dir, state, str(row.get("phase")), step_id, attempt)
    if spec is None:
        return None
    errors = inline_errors(state, step_id, attempt)
    if not errors:
        return spec
    detail = "; ".join(errors)[:400]
    return {**spec, "instruction": f"{spec['instruction']} The previous attempt was rejected: {detail}."}


def _reissue_inline(work_dir: Path, state: dict, row: dict) -> dict | None:
    """`next` on an unfinished inline-llm step: return the same instruction again (idempotent §3.1)."""
    spec = _inline_spec_for(work_dir, state, row, int(row["attempt"]))
    if spec is None:
        return None
    response = {
        "step_id": row["step_id"],
        "kind": KIND_INLINE,
        "phase": row.get("phase"),
        "attempt": int(row["attempt"]),
        "reason": row.get("reason"),
        "instruction": spec["instruction"],
        "write_to": spec["expected_outputs"][0]["work_path"],
        "schema": f"schemas/{spec['expected_outputs'][0]['schema']}.schema.json",
        "expected_outputs": _response_outputs(spec["expected_outputs"]),
        "reissued": True,
    }
    if spec.get("chat_line"):
        response["chat_line"] = spec["chat_line"]
    return response


def retry_inline(work_dir: Path, state: dict, row: dict) -> dict | None:
    """Re-issue a rejected inline-llm step as `attempt+1`, `reason: failure` (§2.2 `inline_llm_retry`).

    The budget was spent when `report` rejected the result, so the retry only reads it; `None` means
    the budget is gone and the caller degrades through `mf finalize` (M9). The new attempt has its
    own work path, so the file the rejected attempt left behind can never close it.
    """
    budget = budget_for(state, row)
    if budget_used(state, budget) > budget["limit"]:
        return None
    attempt = int(row["attempt"]) + 1
    spec = _inline_spec_for(work_dir, state, row, attempt)
    if spec is None:
        return None
    return issue(
        work_dir, state, spec, step_id=str(row["step_id"]), attempt=attempt, reason=REASON_FAILURE
    )


def resume_gate(work_dir: Path, state: dict, row: dict) -> dict:
    """`next` on an unanswered gate step: the identical prompt, same attempt, same generation (§3.1)."""
    gate = gates.gate_for_phase(row.get("phase")) or "intake"
    generation = int(row.get("generation") or 0)
    if row.get("kind") == KIND_GATE_AUQ and generation == 0:
        auq = gates.build_auq(work_dir, state)
        return {
            "step_id": row["step_id"],
            "kind": KIND_GATE_AUQ,
            "phase": row.get("phase"),
            "attempt": int(row["attempt"]),
            "generation": generation,
            "questions": auq["questions"],
            "text": plan_gate_text(work_dir, state),
            "text_fallback": gates.render(work_dir, state, "plan"),
            "reissued": True,
            "chat_line": _chat(state, "waiting for the plan gate"),
        }
    full = gates.render(work_dir, state, gate)
    return {
        "step_id": row["step_id"],
        "kind": KIND_GATE_TEXT,
        "phase": row.get("phase"),
        "attempt": int(row["attempt"]),
        "generation": generation,
        "text": gate_text(work_dir, state, gate, full),
        "text_fallback": full,
        "end_turn": True,
        "reissued": True,
        "chat_line": _chat(state, f"waiting for the {gate} gate"),
    }


# --- chat line ------------------------------------------------------------


def _chat(state: dict, detail: str) -> str:
    route = route_for(state)
    phase = str(state.get("current_phase"))
    return f"Phase {position_in(route, phase)}/{len(route)} — {phase}: {detail}"


# --- transitions ----------------------------------------------------------


def transition(work_dir: Path, state: dict, phase: str, *, mutate=None, warnings=(), banners=()) -> dict:
    """Write the new phase, its warnings and banners in one state write, then plan again."""
    def mutator(current: dict) -> None:
        current["current_phase"] = phase
        for warning in warnings:
            if warning not in current.setdefault("drafting_warnings", []):
                current["drafting_warnings"].append(warning)
        for condition_key, params in banners:
            review.record_banner(current, condition_key, **params)
        if mutate is not None:
            mutate(current)
        current["progress"] = build_progress(work_dir, current)

    state_io.write_state(work_dir, mutator)
    return {"transition": phase}


def warning(code: str, message: str, phase: str) -> dict:
    """One `drafting_warnings[]` entry (§2.2; the CLI is the only writer)."""
    return {"code": code, "message": message, "phase": phase, "at": events.utc_now()}


def memo_warning(key: str, language: object, **fmt) -> str:
    """One `memo.warnings` string of the memo language, rendered with `**fmt` (D-175).

    The language is locked by the time the code writes a warning, so the message is created
    in the language the deliverable will print it in.
    """
    return i18n.t(i18n.normalize(language) or i18n.DEFAULT, f"memo.warnings.{key}", **fmt)


# --- inline-llm specs ------------------------------------------------------


def inline_spec(work_dir: Path, state: dict, phase: str, step_id: str, attempt: int) -> dict | None:
    """The two inline-llm steps of the pipeline: the MCP probe and `plan.json` (§2.1 rows 1, 3)."""
    if phase == "intake_preliminary_research":
        return {
            "kind": KIND_INLINE,
            "phase": phase,
            "instruction": (
                "List the legal MCP namespaces available in this session (Legal Data Hunter, "
                "CourtListener, LegalViz — the EU-law server, which names itself `eurlex` — "
                "UK Legal (`uk-legal-mcp`), JusticeLibre (French law), OpenCaseLaw (Swiss law), "
                "Federal Regulations (US CFR via eCFR and the Federal Register), "
                "Lex (UK legislation, explanatory notes and amendments, by i.AI), "
                "CasusLegal (Russian case law and commentary), "
                "FAS advertising practice (Russian FAS decisions on advertising law), "
                "any other legal server). Then make exactly ONE cheap call per connected server and "
                'record what came back: LegalViz `resolve("Regulation (EU) 2016/679")`, UK Legal '
                '`legislation_search("Data Protection Act 2018", limit 1)`, CourtListener `search` '
                'for a case you know exists, LDH `discover_sources("EU")`, JusticeLibre '
                '`get_law_article(code="CT", num="L1121-1")`, OpenCaseLaw '
                '`get_law(sr_number="220", article="328b")`, CasusLegal '
                '`casuslegal_find_term` with a two-word phrase, FAS `get_filter_options`. Write both as '
                '`mcp-probe` JSON: {"namespaces": {"ldh": "<namespace or null>", '
                '"courtlistener": "<namespace or null>", "legalviz": "<namespace or null>", '
                '"uklegal": "<namespace or null>", "justicelibre": "<namespace or null>", '
                '"opencaselaw": "<namespace or null>", "fedregs": "<namespace or null>", '
                '"lex": "<namespace or null>", "casus": "<namespace or null>", '
                '"fas": "<namespace or null>", "other": ["<namespace>", …]}, '
                '"status": {"ldh": "ok|quota|auth|error|absent", "courtlistener": …, '
                '"legalviz": …, "uklegal": …, "justicelibre": …, "opencaselaw": …, "fedregs": …, '
                '"lex": …, "casus": …, "fas": …}}. `ok` only '
                "when the call returned a real answer: a quota or rate-limit refusal is `quota`, a "
                "401/OAuth refusal is `auth`, any other failure is `error`, a server that is not "
                "connected is `absent`. One call each, no retries — a server that is not `ok` is "
                "routed around for the rest of the run."
            ),
            "expected_outputs": [
                dispatch.output("intake/mcp-probe.json", "mcp-probe", step_id, attempt, ORCHESTRATOR_SLOT)
            ],
            "chat_line": _chat(state, "probing the legal MCP servers"),
        }
    if phase == "planning":
        edit = plan_edit_request(state)
        names = dispatch.language_context(state)
        ui_name = names["ui_language_name"]
        shape = (
            "classification, jurisdictions, doctrine_required, estimated_complexity and one entry "
            "per legal issue (issue_id, title, question, jurisdictions)"
        )
        # D-176b: only the three prose fields are translated. `classification` is an enum of six
        # English values in `schemas/plan.schema.json`, so a translated one is a rejected plan.
        ui_paragraph = (
            f"The plan digest prints your prose verbatim: write `issues[].title`, `issues[].question` "
            f"and `notes` in {ui_name}. The machine fields stay English — `issue_id`, `layer`, "
            f"jurisdiction codes, `classification` and `estimated_complexity`."
        )
        if edit is None:
            instruction = (
                f"Classify the question and write the research plan as `plan` JSON: {shape}. "
                "Use the user question, `intake/questions.json` and `intake/user-facts.md`. "
                f"{ui_paragraph}"
            )
        else:
            # §2.4 Edit: the next plan is the previous one plus exactly the correction the user asked
            # for — the planner never starts over from the question alone.
            instruction = (
                "Revise the research plan the user asked to change. Read the previous version "
                "`plan.json`, apply exactly this correction and change nothing else: "
                f"{edit or '(the user asked for a change without naming it; re-read the question)'}. "
                f"Write the complete `plan` JSON again: {shape}. {ui_paragraph}"
            )
        return {
            "kind": KIND_INLINE,
            "phase": phase,
            "instruction": instruction,
            "expected_outputs": [
                dispatch.output("plan.json", "plan", step_id, attempt, ORCHESTRATOR_SLOT)
            ],
            "chat_line": _chat(
                state, "revising the research plan" if edit is not None else "building the research plan"
            ),
        }
    return None


def plan_gate_iterations(state: dict) -> list[dict]:
    """`plan_approval.iterations[]` — every answer the plan gate has collected (§2.4 b)."""
    rows = (state.get("plan_approval") or {}).get("iterations") or []
    return [row for row in rows if isinstance(row, dict)]


def plan_edit_request(state: dict) -> str | None:
    """The text of the last `edit` answer on the plan gate, or None when the last answer was not one."""
    rows = plan_gate_iterations(state)
    if not rows or rows[-1].get("action") != "edit":
        return None
    answers = dict(rows[-1].get("answers") or {})
    text = str(answers.get("edit_text") or "").strip()
    if text:
        return text
    extra = [
        f"{key}: {value}"
        for key, value in sorted(answers.items())
        if key not in ("Plan", "edit_text") and str(value).strip()
    ]
    plan_answer = str(answers.get("Plan") or "").strip()
    if plan_answer.lower().startswith("edit") and len(plan_answer) > len("edit"):
        extra.insert(0, plan_answer[len("edit") :].strip(" :;-"))
    return "; ".join(part for part in extra if part)


# --- dispatch specs --------------------------------------------------------


def _plan_document(work_dir: Path) -> dict:
    """The approved plan, read against `published[]` — drift is no plan at all (D-41, D-60)."""
    try:
        document = stepctx.read_published(work_dir, gates.PLAN_PATH)
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _mcp_probe(work_dir: Path) -> dict | None:
    """`intake/mcp-probe.json`, read against `published[]`; None when it cannot be read (D-41, D-60)."""
    try:
        probe = stepctx.read_published(work_dir, gates.MCP_PROBE_PATH)
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        return None
    return probe if isinstance(probe, dict) else None


def _mcp_namespaces(work_dir: Path) -> str:
    """The probed MCP namespaces, read against `published[]` (D-41, D-60).

    D-187a: every bundled server of `routing.MCP_SERVERS`, in table order, then whatever the probe
    filed under `other`. The list used to name two aliases, so a session with the other bundled
    servers connected — and a purely RU session, with casus and fas alone — was told `none`.
    """
    probe = _mcp_probe(work_dir)
    if probe is None:
        return "unknown"
    namespaces = probe.get("namespaces") or {}
    names = [str(namespaces.get(alias)) for alias in routing.MCP_SERVERS if namespaces.get(alias)]
    names += [str(name) for name in (namespaces.get("other") or [])]
    return ", ".join(names) if names else "none"


def _mcp_probe_namespaces(work_dir: Path) -> dict:
    """`namespaces` of the probe as a mapping — what `routing.routing_digest` filters by (D-110).

    D-147: a server whose smoke call answered `quota`, `auth` or `error` is connected and useless,
    so it leaves the mapping here and every line of the digest, exactly like an exhausted quota
    (D-122). A probe written before D-147 carries no `status` and is read as it always was.
    """
    return preflight.usable_namespaces(_mcp_probe(work_dir))


def _alnum(value: object) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _exhaustion_alias(server: object) -> str | None:
    """The routing alias an `mcp_ratelimit_fallback` names (D-122).

    Agents write `server` in whatever form the host showed them — `ldh`, `Legal_Data_Hunter`,
    `legal-data-hunter` — so the alias, the bundled server name and the human label of
    `routing.MCP_SERVERS` / `MCP_SERVER_LABELS` are all compared without punctuation or case.
    """
    key = _alnum(server)
    if not key:
        return None
    for alias, name in routing.MCP_SERVERS.items():
        labels = (alias, name, routing.MCP_SERVER_LABELS.get(alias, ""))
        if key in {_alnum(label) for label in labels if label}:
            return alias
    return None


def _quota_exhausted(data: dict) -> bool:
    """True when the fallback says the provider's quota is gone, not that it is merely throttled."""
    text = " ".join(str(value) for key, value in data.items() if key != "server").lower()
    if "quota" not in text:
        return False
    return "exhaust" in text or data.get("retry_after") is None


def record_mcp_exhaustion(work_dir: Path, state: dict) -> dict:
    """D-122: `state.mcp_exhausted[alias] = <date>` for every server that reported its quota gone.

    The quota is the provider's own daily one, not the run's, so a second research pass on the same
    day must not walk back into it: `mf next` strikes the alias off the routing digest until the UTC
    date changes. Returns the merged map.
    """
    found: dict[str, str] = {}
    for record in events.read_events(work_dir):
        if record.get("event") != "mcp_ratelimit_fallback":
            continue
        data = record.get("data") or {}
        if not isinstance(data, dict) or not _quota_exhausted(data):
            continue
        alias = _exhaustion_alias(data.get("server"))
        if alias:
            found[alias] = (str(record.get("ts") or "") or events.utc_now())[:10]
    current = {
        str(key): str(value)
        for key, value in (state.get("mcp_exhausted") or {}).items()
        if isinstance(key, str)
    }
    merged = {**current, **found}
    if merged != current:
        def mutator(inner: dict) -> None:
            inner["mcp_exhausted"] = merged

        state_io.write_state(work_dir, mutator)
    return merged


def mcp_exhausted_today(state: dict) -> tuple[str, ...]:
    """Aliases whose provider quota ran out **today** — what `routing_digest` drops (D-122)."""
    today = events.utc_now()[:10]
    rows = state.get("mcp_exhausted") or {}
    if not isinstance(rows, dict):
        return ()
    return tuple(sorted(alias for alias, day in rows.items() if str(day) == today))


def _warnings_text(state: dict) -> str:
    rows = [
        row.get("message") if isinstance(row, dict) else str(row)
        for row in (state.get("drafting_warnings") or [])
    ]
    return "; ".join(text for text in rows if text) or "none"


RESEARCH_SUBSET_STATUS = "research_subset"
"""`sufficiency_followup.status` that carries the subset of layers phase 5 must repeat (§2.1 rows 6–7)."""


def in_scope_layers(state: dict) -> list[str]:
    """`config.researcher_layers` — the layers this run researches (§2.3, D-112)."""
    config = state.get("config") or {}
    layers = [layer for layer in (config.get("researcher_layers") or []) if layer in routing.LAYERS]
    return layers or list(routing.LAYERS)


def missing_layers(state: dict) -> list[str]:
    """The layers `mf sufficiency route` approved for a second research pass (§2.1 rows 6–7).

    D-116: the router is the one place the research budget is charged, so what it approved —
    `sufficiency_followup.approved_layers` — is what phase 5 may dispatch, and nothing else. The
    set used to be re-derived here from `subset_r`, and with the user budget left but the research
    budget spent the router answered `layers: []` while `subset_r` still named the gap: the gate
    answer then bought a research pass nobody had paid for.

    D-112: a gap in a layer the run does not research is never dispatched — `mf sufficiency route`
    turned it into a `drafting_warnings[]` entry. A state written before `approved_layers` existed
    still derives the set from `subset_r`.
    """
    followup = state.get("sufficiency_followup") or {}
    scope = set(in_scope_layers(state))
    approved = followup.get(sufficiency.APPROVED_LAYERS)
    if isinstance(approved, list):
        return sorted({str(layer) for layer in approved if layer in scope})
    return sorted(
        {
            str(gap.get("target"))
            for gap in (followup.get("subset_r") or [])
            if isinstance(gap, dict) and gap.get("status") == "missing" and gap.get("target") in scope
        }
    )


def target_layers(work_dir: Path, state: dict) -> list[str]:
    """Layers this entry of phase 5 must dispatch: the full list, or the subset asked for by §2.1 row 6.

    The subset is the one computed when the run left phase 6 or 7 and kept in
    `sufficiency_followup` (`status: research_subset` + `approved_layers`), so the follow-up answer
    re-dispatches exactly the layers the router paid for instead of every configured one. D-112: it
    is `missing_layers` ∩ `config.researcher_layers` — the follow-up narrows the mode, never widens
    it, and D-116 keeps it within what the research budget bought.
    """
    followup = state.get("sufficiency_followup") or {}
    if str(followup.get("status")) == RESEARCH_SUBSET_STATUS:
        subset = missing_layers(state)  # already ∩ `config.researcher_layers` (D-112)
        if subset:
            return subset
    config = state.get("config") or {}
    layers = [layer for layer in (config.get("researcher_layers") or ["statutes"])]
    if not bool(_plan_document(work_dir).get("doctrine_required", True)):
        layers = [layer for layer in layers if layer != "doctrine"]
    return layers


def _routing_table(rows: list[dict]) -> str:
    """`${routing}` — one indented block per routed row: tools, LDH corpora, then the row's note.

    D-187a: the tool line used to be the whole parameter, so everything else the routing table
    carries — which LDH corpus answers for that jurisdiction, which portal is captcha-gated or
    behind a WAF, how a pinpoint is written there — never reached the researcher, whatever the
    jurisdiction. The head keeps the shape it always had; the corpora and the note follow it.
    """
    lines: list[str] = []
    for row in rows:
        head = f"{row['jurisdiction'] or 'any'}: {' > '.join(row['tools'])}"
        if row["domains"]:
            head += f" (domains: {', '.join(row['domains'])})"
        lines.append(f"  - {head}")
        if row["ldh_sources"]:
            lines.append(f"    LDH sources: {', '.join(row['ldh_sources'])}")
        if row["note"]:
            lines.append(f"    {row['note']}")
    return ("\n" + "\n".join(lines)) if lines else "none"


def research_layers(work_dir: Path, state: dict) -> list[str]:
    """Layers whose findings the downstream consumers may name: what was actually dispatched (§2.1)."""
    dispatched = [
        layer for layer in (state.get("dispatched_researchers") or []) if layer in routing.LAYERS
    ]
    return dispatched or target_layers(work_dir, state)


def researcher_specs(work_dir: Path, state: dict, layers: list[str]) -> list[dict]:
    """One `legal-researcher` spec per layer (§4.3)."""
    plan = _plan_document(work_dir)
    issues = ", ".join(
        f"{issue.get('issue_id')}: {issue.get('question')}" for issue in (plan.get("issues") or [])
    ) or "see plan.json"
    jurisdictions = ", ".join(plan.get("jurisdictions") or []) or "unspecified"
    followup = state.get("sufficiency_followup") or {}
    user_response = followup.get("user_response") or "none"
    # A43-2 / D-154: the gaps the sufficiency reviewer named for THIS layer, and the earlier pass to
    # extend — without them the re-dispatch is a blind repeat that replaces the first pass's file.
    gaps_by_layer: dict[str, list[str]] = {}
    for gap in followup.get("subset_r") or []:
        if not isinstance(gap, dict) or gap.get("status") != "missing" or not gap.get("gap"):
            continue
        line = f"- {gap['gap']}"
        if gap.get("why_blocking"):
            line += f" — why it blocks: {gap['why_blocking']}"
        gaps_by_layer.setdefault(str(gap.get("target")), []).append(line)
    # D-147: the researcher is told which routed portal did not answer today, before it tries.
    source_access = preflight.source_access_line(work_dir, state)
    specs = []
    for layer in layers:
        table = _routing_table(routing.routing_for([layer], plan.get("jurisdictions") or []))
        gaps = gaps_by_layer.get(layer) or []
        previous = f"research/{layer}.json"
        previous_findings = (
            f"`{previous}` — your earlier pass. Read it first; keep every finding and registered source that "
            "still stands, add what the gaps below ask for, and write the complete document (it replaces the file)."
            if (work_dir / previous).is_file()
            else "none - first pass of this layer"
        )
        specs.append(
            dispatch.spec(
                layer,
                "legal-researcher",
                layer,
                [(f"research/{layer}.json", "research-findings")],
                inputs=[gates.PLAN_PATH, gates.MCP_PROBE_PATH, preflight.PREFLIGHT_PATH],
                # D-209: `case_law` on `opus`; None keeps the agent's own model for the other layers.
                model=dispatch.RESEARCH_LAYER_MODELS.get(layer),
                layer=layer,
                issues=issues,
                jurisdictions=jurisdictions,
                routing=table,
                mcp_namespaces=_mcp_namespaces(work_dir),
                source_access=source_access,
                followup_prompts=user_response,
                followup_gaps=("\n" + "\n".join(gaps)) if gaps else "none",
                previous_findings=previous_findings,
                retry_errors="none",
            )
        )
    return specs


def reviewer_specs(work_dir: Path, state: dict, kinds: list[str], iteration: int) -> list[dict]:
    """One spec per reviewer of `config.reviewer_list` for iteration N (§4.5 п.2)."""
    draft_path = str(state.get("current_draft_path") or f"{DRAFTS_DIR}/v{iteration}.md")
    draft_sha = str(state.get("current_draft_sha") or "")
    attachment = lint_attachment(work_dir, state, draft_sha)
    research_files = ", ".join(
        f"`research/{layer}.json`" for layer in research_layers(work_dir, state)
    ) or "`research/`"
    specs = []
    for kind in kinds:
        agent = dispatch.REVIEWER_AGENTS[kind]
        # D-214: the next citations reviewer starts with the pairs no earlier text check reached.
        carry_over = _carry_over(work_dir, state, iteration, draft_path) if kind == "citations" else "none"
        specs.append(
            dispatch.spec(
                kind,
                agent,
                f"{kind} v{iteration}",
                [(review.review_path(iteration, kind), "review")],
                inputs=[draft_path],
                checklist=kind,
                iteration=iteration,
                draft_path=draft_path,
                draft_version=iteration,
                draft_sha=draft_sha,
                lint_attachment=attachment,
                claim_pairs=(
                    "pair every `[[src:<id>]]` claim of the draft with the finding in the research files "
                    "whose `source_id` matches. The finding is the pairing key: its `proposition` and "
                    "`pinpoint` say what the researcher recorded. For a `critical` source the saved text "
                    "is the ceiling, and where the finding and the text disagree, the text wins"
                ),
                # D-208: the units a reviewer may spend reading saved texts; 0 for logic and form.
                lookup_budget=str(limits.REVIEWER_LOOKUP_BUDGET.get(kind, 0)),
                carry_over=carry_over,
                research_files=research_files,
                retry_errors="none",
            )
        )
    return specs


def _carry_over(work_dir: Path, state: dict, iteration: int, draft_path: str) -> str:
    """D-214 `${carry_over}`: one `source_id · section_id · reason` line per unchecked pair, or `none`."""
    path = work_dir / draft_path
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    pairs = review.unchecked_pairs(work_dir, state, iteration, text)
    return "\n".join(f"{row['source_id']} · {row['section_id']} · {row['reason']}" for row in pairs) or "none"


def writer_spec(
    work_dir: Path,
    state: dict,
    *,
    task: str,
    version: int,
    canonical: str,
    instructions: str,
    seed: bool,
) -> dict:
    """The `memo-writer` spec of v1, a lint fix, a revision or the client polish (§4.4)."""
    return dispatch.spec(
        "writer",
        "memo-writer",
        f"{task} v{version}",
        [(canonical, None)],
        inputs=[gates.PLAN_PATH],
        writer_task=task,
        draft_version=version,
        draft_path=canonical,
        seed_path=canonical if seed else "none - this is the first version",
        instructions_path=instructions,
        inputs_list=(
            "`plan.json`, `intake/`, `research/*.md`, `research/source-pack.json`, "
            "`research/currency.json`"
        ),
        drafting_warnings=_warnings_text(state),
        retry_errors="none",
    )


def _lint_not_converged(state: dict) -> bool:
    """True once the bounded lint-fix loop gave up on a draft (`lint_not_converged` banner, §4.4)."""
    return any(
        str(row.get("condition_key") or row.get("banner_id") or "") == "lint_not_converged"
        for row in (state.get("fallback_banners") or [])
        if isinstance(row, dict)
    )


DETERMINISTIC_REPORTS: dict[str, str] = {
    "draft.lint": "lint.json",
    "draft.audit-citations": "citations.json",
}
"""The two deterministic checks of §4.5 п.1, by the script command key that produces each report."""


def report_for_draft(work_dir: Path, state: dict, name: str, draft_sha: str | None) -> dict | None:
    """The published `lint.json`/`citations.json` of one draft sha, or None (D-41, D-54).

    D-41: the bytes are checked against `published[]` first — a report edited after publication is
    drift, not a verdict. D-54: a report written for another draft sha does not describe this draft,
    so it is treated as absent and the check is re-issued.
    """
    try:
        report = stepctx.read_published(work_dir, name, state=state)
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    if draft_sha and report.get("draft_sha") != draft_sha:
        return None
    return report


def lint_attachment(work_dir: Path, state: dict, draft_sha: str | None) -> str:
    """D-39: reviewers see `lint.json`/`citations.json` only when the fix rounds are exhausted
    and the draft is still not clean; in every other case the placeholder stays empty."""
    if not _lint_not_converged(state) or all(_draft_checks(work_dir, state, draft_sha)):
        return ""
    return "`lint.json` and `citations.json` in the work dir; the deterministic findings stay blockers"


# --- script-step helpers ---------------------------------------------------

_STRIPPED_OPTIONS = ("--step", "--attempt", "--next-step")


def command_signature(command: list[str] | None) -> tuple:
    """The command without its identity options — the key that recognises a repeat (§3.1 rerun)."""
    tokens = list(command or [])
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _STRIPPED_OPTIONS:
            index += 2
            continue
        out.append(token)
        index += 1
    return tuple(out)


def find_script_step(state: dict, command: list[str]) -> dict | None:
    """The latest record of a script step with the same command signature, or None."""
    signature = command_signature(command)
    found = None
    for row in state.get("steps") or []:
        if not isinstance(row, dict) or row.get("kind") != KIND_SCRIPT:
            continue
        if command_signature(row.get("command")) == signature:
            found = row
    return found


def with_identity(command: list[str], step_id: str, attempt: int) -> list[str]:
    """The same command retargeted at another `(step_id, attempt)` (§3.1 identity options)."""
    tokens = list(command)
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--step":
            out += ["--step", str(step_id)]
            index += 2
            continue
        if token == "--attempt":
            out += ["--attempt", str(int(attempt))]
            index += 2
            continue
        out.append(token)
        index += 1
    return out


def retry_failed_script(work_dir: Path, state: dict, row: dict) -> dict:
    """§2.2 script-error policy: one repeat (`reason: recovery`), then `mf finalize --reason cli_error`.

    A CLI error is never a business outcome, so a `fail`-closed script step neither satisfies the
    prerequisite of its phase nor lets the run walk on: it is re-issued once and, if it fails again,
    the run leaves through the always-deliver exit (M9).
    """
    step_id = str(row["step_id"])
    attempts = stepctx.steps_for(state, step_id)
    if any(str(item.get("reason")) == REASON_RECOVERY for item in attempts):
        if command_key(row.get("command")) == "finalize":
            # The always-deliver exit itself is the failing command: end the run (M9 best effort).
            return transition(work_dir, state, "failed")
        return finalize_step(work_dir, state, "cli_error") or transition(work_dir, state, "failed")
    attempt = int(row["attempt"]) + 1
    command = with_identity(list(row.get("command") or ()), step_id, attempt)
    return issue(
        work_dir,
        state,
        {
            "kind": KIND_SCRIPT,
            "phase": row.get("phase"),
            "command": command,
            "chat_line": _chat(state, f"retrying {command_key(command)} after a CLI error"),
        },
        step_id=step_id,
        attempt=attempt,
        reason=REASON_RECOVERY,
    )


def inputs_sha(work_dir: Path, inputs) -> dict:
    """`steps[].inputs_sha` — sha of every file a script step depends on, `""` when it is absent.

    A missing file is recorded, not dropped: the difference between «no docx yet» and «this docx»
    is exactly what tells a `reason: rerun` re-issue apart from a repetition of the same work.
    """
    result: dict[str, str] = {}
    for relative in inputs or ():
        name = str(relative)
        if not name:
            continue
        path = Path(work_dir) / name
        result[name] = state_io.sha256_file(path) if path.is_file() else ""
    return result


def script_step(
    work_dir: Path,
    state: dict,
    *tokens: str,
    chat: str,
    extra: tuple = (),
    mutate=None,
    inputs=None,
) -> dict | None:
    """Issue (or re-issue with `reason: rerun`) one script step; None when there is nothing to do.

    §2.2: `reason: rerun` is «the same action on changed inputs», so a step that declares `inputs`
    is only re-issued once the sha of one of them differs from the `inputs_sha` recorded when the
    closed attempt was issued. With the same inputs the closed step (`ok|skipped|no_change`) is the
    answer and `None` tells the planner to walk on.
    """
    probe = mf_command(work_dir, "?", 1, *tokens) + list(extra)
    previous = find_script_step(state, probe)
    if previous is not None and previous.get("status") in (None, ""):
        return None  # replayed earlier in this `next` call
    if previous is not None and previous.get("status") == "fail":
        return retry_failed_script(work_dir, state, previous)
    current = inputs_sha(work_dir, inputs) if inputs is not None else None
    if (
        current is not None
        and previous is not None
        and previous.get("status") in SCRIPT_DONE_STATUSES
        and dict(previous.get("inputs_sha") or {}) == current
    ):
        return None  # the same command over the same inputs — it has already run
    step_id = str(previous["step_id"]) if previous is not None else next_step_id(state)
    attempt = int(previous["attempt"]) + 1 if previous is not None else 1
    reason = REASON_RERUN if previous is not None else REASON_INITIAL
    command = mf_command(work_dir, step_id, attempt, *tokens) + list(extra)
    return issue(
        work_dir,
        state,
        {
            "kind": KIND_SCRIPT,
            "command": command,
            "chat_line": chat,
            "mutate": mutate,
            "inputs_sha": current,
        },
        step_id=step_id,
        attempt=attempt,
        reason=reason,
    )


def script_done(state: dict, key: str) -> dict | None:
    """The finished step of one script command key inside the current episode.

    §2.2: a script step that ended in a CLI error never satisfies the prerequisite of the next
    phase, so `fail` is the one status that does not count. Every other closed status does —
    `skipped` is a legitimate outcome of a script command whose work was not needed (`docx validate`
    without a docx, D-51/M9), and re-issuing the step would only produce the same answer again.
    """
    for row in episode(state):
        if purpose(row) == f"script:{key}" and row.get("status") in SCRIPT_DONE_STATUSES:
            return row
    return None


def dispatch_done(state: dict, agent: str) -> dict | None:
    """The closed dispatch step of one agent inside the current episode."""
    for row in episode(state):
        if row.get("kind") == KIND_DISPATCH and agent in agent_names(row):
            return row
    return None


def issue_dispatch(
    work_dir: Path,
    state: dict,
    specs: list[dict],
    *,
    chat: str,
    step_id: str | None = None,
    seeds: dict | None = None,
    mutate=None,
) -> dict:
    """Render the prompts, record `inputs[]` (including writer seeds) and issue the dispatch step."""
    step_id = step_id or next_step_id(state)
    progress = state.get("progress") or {}
    agents = dispatch.render_agents(
        work_dir,
        state,
        step_id=step_id,
        attempt=1,
        specs=specs,
        position=int(progress.get("position") or 0),
        total=int(progress.get("total") or 0),
    )
    inputs = dispatch.inputs_map(work_dir, specs)
    for agent in agents:
        source = (seeds or {}).get(agent["slot"])
        if source is None:
            continue
        for entry in agent["expected_outputs"]:
            target = work_dir / entry["work_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            origin = Path(source) if Path(source).is_absolute() else work_dir / source
            state_io.write_bytes_atomic(target, origin.read_bytes())
    return issue(
        work_dir,
        state,
        {
            "kind": KIND_DISPATCH,
            "agents": agents,
            "inputs": inputs,
            "chat_line": chat,
            "mutate": mutate,
        },
        step_id=step_id,
    )


def view_path(canonical: str) -> str:
    """The `.md` sibling `mf render` writes for one canonical JSON (D-62)."""
    return canonical[: -len(".json")] + ".md" if canonical.endswith(".json") else canonical


def views_fresh(state: dict, sources: list[str]) -> bool:
    """True when every `<name>.md` view was published after the `<name>.json` it renders (D-57)."""
    for source in sources:
        view = stepctx.published_entry(state, view_path(source))
        if view is None:
            return False
        origin = stepctx.published_entry(state, source) or {}
        if str(view.get("at") or "") < str(origin.get("at") or ""):
            return False
    return True


def render_step(
    work_dir: Path,
    state: dict,
    view: str,
    sources: list[str],
    *,
    chat: str,
    extra: tuple = (),
) -> dict | None:
    """D-57: `mf render <view>` is a script step in front of every consumer of the md view.

    `None` means the views are already current — the JSON-only agents never write them themselves,
    so the consumer may only be dispatched once this step has run for the published artifacts.
    """
    present = [source for source in sources if (work_dir / source).is_file()]
    if not present or views_fresh(state, present):
        return None
    return script_step(
        work_dir,
        state,
        "render",
        view,
        chat=_chat(state, chat),
        extra=extra,
        inputs=tuple(present),
    )


def finalize_step(work_dir: Path, state: dict, reason: str | None) -> dict | None:
    """Always-deliver exit: `mf finalize` writes the deliverable, then the terminal phase (M9)."""
    extra = ("--reason", reason) if reason else ()
    return script_step(
        work_dir,
        state,
        "finalize",
        chat=_chat(state, f"finalizing ({reason or 'complete'})"),
        extra=extra,
    )


# --- per-phase planners ----------------------------------------------------


def _inline_row(state: dict, basename: str) -> dict | None:
    """The closed inline-llm step of one output, or None when it has not run yet."""
    for row in episode(state):
        if purpose(row) == f"inline:{basename}":
            return row
    return None


def _inline_stage(
    work_dir: Path, state: dict, phase: str, basename: str, reason: str
) -> dict | None:
    """The inline-llm step of one phase: issue it, retry it, or `None` once it produced its file.

    §2.2/§3.1: a rejected result closes its attempt as `fail`, so the next attempt is a new identity
    (`attempt+1`, `reason: failure`) carrying the schema errors; `inline_llm_retry` is spent by
    `report` once per rejected attempt, and its exhaustion degrades through `mf finalize` (M9).
    """
    row = _inline_row(state, basename)
    if row is None:
        return issue(work_dir, state, inline_spec(work_dir, state, phase, next_step_id(state), 1))
    if row.get("status") == "fail":
        retry = retry_inline(work_dir, state, row)
        if retry is not None:
            return retry
        return finalize_step(work_dir, state, reason) or transition(work_dir, state, "failed")
    return None


def plan_intake_preliminary_research(work_dir: Path, state: dict) -> dict:
    """§2.1 row 1: inline mcp-probe, then the analyst dispatch."""
    step = _inline_stage(
        work_dir, state, "intake_preliminary_research", "mcp-probe.json", "mcp_probe_invalid"
    )
    if step is not None:
        return step
    analyst = dispatch_done(state, "fact-assumption-analyst")
    if analyst is None:
        config = state.get("config") or {}
        spec = dispatch.spec(
            "analyst",
            "fact-assumption-analyst",
            "intake",
            [
                ("intake/questions.json", "intake-questions"),
                ("intake/preliminary-sources.json", "research-findings"),
            ],
            inputs=[gates.MCP_PROBE_PATH],
            max_questions=str(config.get("intake_max_questions") or limits.INTAKE_MAX_QUESTIONS),
            mcp_namespaces=_mcp_namespaces(work_dir),
            routing_digest=routing.routing_digest(
                _mcp_probe_namespaces(work_dir), exhausted=mcp_exhausted_today(state)
            ),
            retry_errors="none",
        )
        return issue_dispatch(work_dir, state, [spec], chat=_chat(state, "analysing facts and assumptions"))
    if analyst.get("status") == "fail":
        return finalize_step(work_dir, state, "intake_questions_invalid") or {"transition": "failed"}
    return transition(work_dir, state, "intake_questions_pending")


def plan_planning(work_dir: Path, state: dict) -> dict:
    """§2.1 row 3: inline-llm `plan.json` -> script `mf sources preflight`; invalid plan -> `failed`.

    D-147: the preflight needs the approved jurisdictions, and the plan gate needs the preflight —
    which portal answers today is part of what the user approves. So it sits between them, keyed on
    `plan.json`: an `edit` that renames a jurisdiction re-runs it (`reason: rerun`, §2.2).
    """
    step = _inline_stage(work_dir, state, "planning", "plan.json", "plan_invalid")
    if step is not None:
        return step
    checked = script_step(
        work_dir,
        state,
        "sources",
        "preflight",
        chat=_chat(state, "checking which source portals answer today"),
        inputs=[gates.PLAN_PATH],
    )
    if checked is not None:
        return checked
    return transition(work_dir, state, "plan_approval_pending")


def _gate_row(state: dict) -> dict | None:
    for row in episode(state):
        if row.get("kind") in (KIND_GATE_TEXT, KIND_GATE_AUQ):
            return row
    return None


def _issue_gate(work_dir: Path, state: dict, gate: str) -> dict:
    if gate == "plan":
        auq = gates.build_auq(work_dir, state)
        return issue(
            work_dir,
            state,
            {
                "kind": KIND_GATE_AUQ,
                "questions": auq["questions"],
                "text": plan_gate_text(work_dir, state),
                "text_fallback": gates.render(work_dir, state, "plan"),
                "chat_line": _chat(state, "plan and coverage need your decision"),
            },
            generation=0,
        )
    full = gates.render(work_dir, state, gate)
    return issue(
        work_dir,
        state,
        {
            "kind": KIND_GATE_TEXT,
            "text": gate_text(work_dir, state, gate, full),
            "text_fallback": full,
            "chat_line": _chat(state, f"waiting for your {gate} answer"),
        },
        generation=0,
    )


def plan_intake_questions_pending(work_dir: Path, state: dict) -> dict:
    """§2.1 row 2: the intake gate; any accepted answer moves on to planning."""
    row = _gate_row(state)
    if row is None:
        return _issue_gate(work_dir, state, "intake")
    return transition(work_dir, state, "planning")


def _style_overrides(state: dict, answers: dict) -> dict:
    """Style answer of gate 4: the profile paths (§4.6)."""
    from . import style_profile

    name = str(answers.get("Style") or answers.get("style") or "").strip()
    if not name or name.lower() == "standard":
        return {}
    try:
        directory = style_profile.profiles_dir() / name
    except (OSError, ValueError):
        return {}
    if not directory.is_dir():
        return {}
    overrides: dict = {"style_profile": name}
    prose = directory / "prose-style.md"
    template = directory / "template.md"
    if prose.is_file():
        overrides["prose_style_path"] = str(prose)
    if template.is_file():
        overrides["template_path"] = str(template)
    return overrides


def apply_plan_answers(state: dict, answers: dict) -> dict:
    """Style > Sources > Plan (§2.4): the config the run continues with.

    D-242: every run is Full — a `Mode` answer an older gate may still send is ignored, and the
    Full config is re-resolved from `state.config` plus the style overrides.
    """
    overrides = _style_overrides(state, answers)
    user_config = dict(state.get("config") or {})
    user_config.update(overrides)
    config = modes.resolve_config("full", user_config)
    config.pop(modes.WRITER_MODEL_FALLBACK_KEY, None)
    for key in ("python_cmd", "plugin_data_dir", "prose_style_path", "template_path", "style_profile"):
        if user_config.get(key) is not None:
            config[key] = user_config[key]
    return {"mode": "full", "config": config, "sources": str(answers.get("Sources") or "").lower()}


def plan_plan_approval_pending(work_dir: Path, state: dict) -> dict:
    """§2.1 row 4: approve / edit (budget `plan_edit`) / cancel.

    §2.2: when `plan_edit` is exhausted the forced approval applies to **the version that carries
    the last correction** — the edit is executed in `planning` first and this entry approves its
    result without asking again.
    """
    used = int((state.get("attempts") or {}).get("plan_edit") or 0)
    forced = used > limits.MAX_PLAN_EDIT
    row = _gate_row(state)
    if row is None and not forced:
        return _issue_gate(work_dir, state, "plan")
    result = result_of(row) if row is not None else {}
    answers = dict(result.get("answers") or {})
    if not answers and forced:
        answers = dict((plan_gate_iterations(state)[-1:] or [{}])[0].get("answers") or {})
    if result.get("action") == "edit":
        def bump(current: dict) -> None:
            current.setdefault("attempts", {})["plan_edit"] = used + 1

        return transition(work_dir, state, "planning", mutate=bump)
    applied = apply_plan_answers(state, answers)
    banners = [("plan_forced_approve", {})] if forced else []

    def mutate(current: dict) -> None:
        current["mode"] = applied["mode"]
        current["config"] = applied["config"]

    events.append_event(
        work_dir,
        "mode_selected",
        "cli",
        {"mode": applied["mode"], "forced": forced, "answers": answers},
        phase="plan_approval_pending",
    )
    return transition(work_dir, state, "research", mutate=mutate, banners=banners)


def plan_research(work_dir: Path, state: dict) -> dict:
    """§2.1 row 5: parallel researchers; partial results are carried forward as warnings."""
    row = dispatch_done(state, "legal-researcher")
    if row is None:
        layers = target_layers(work_dir, state)
        specs = researcher_specs(work_dir, state, layers)
        return issue_dispatch(
            work_dir,
            state,
            specs,
            chat=_chat(state, f"{len(layers)} researcher(s) dispatched ({', '.join(layers)})"),
        )
    statuses = merged_slot_statuses(state, str(row["step_id"]))
    valid = sorted(slot for slot, status in statuses.items() if status in ("ok", "no_change"))

    def mutate(current: dict) -> None:
        # A subset re-entry (§2.1 rows 6–7) dispatches only the named layers; the layers that
        # already produced findings stay dispatched.
        current["dispatched_researchers"] = sorted(
            set(current.get("dispatched_researchers") or []) | set(valid)
        )

    if len(valid) == len(statuses):
        return transition(work_dir, state, "research_sufficiency", mutate=mutate)
    if valid:
        missing = sorted(slot for slot in statuses if slot not in valid)
        return transition(
            work_dir,
            state,
            "research_sufficiency",
            mutate=mutate,
            warnings=[
                warning(
                    "research_layer_missing",
                    memo_warning(
                        "no_findings_for_layers",
                        state.get("language"),
                        layers=", ".join(missing),
                    ),
                    "research",
                )
            ],
            banners=[("research_layers_partial", {"layers": ", ".join(missing)})],
        )
    return transition(
        work_dir,
        state,
        "research_insufficient_pending",
        mutate=mutate,
        banners=[("research_insufficient_budget_consumed", {})],
    )


SUFFICIENCY_TARGETS = {
    "currency_check": "currency_check",
    "gate_followup": "research_sufficiency_followup_pending",
    "research_subset": "research",
    "insufficient_gate": "research_insufficient_pending",
}


def _reset_research_budget(current: dict) -> None:
    """`research_dispatch_retry` is counted per entry of phase 5 (§2.2)."""
    current.setdefault("attempts", {})["research_dispatch_retry"] = 0


def plan_research_sufficiency(work_dir: Path, state: dict) -> dict:
    """§2.1 row 6: the reviewer dispatch, then `mf sufficiency route`."""
    row = dispatch_done(state, "research-sufficiency-reviewer")
    if row is None:
        # §2.1 rows 5–6: the reviewer judges the layers that were actually dispatched — never the
        # configured list, which still names `doctrine` when the plan does not require it.
        layers = research_layers(work_dir, state)
        spec = dispatch.spec(
            "sufficiency",
            "research-sufficiency-reviewer",
            "sufficiency",
            [(SUFFICIENCY_PATH, "research-sufficiency")],
            inputs=[gates.PLAN_PATH] + [f"research/{layer}.json" for layer in layers],
            research_files=", ".join(f"`research/{layer}.json`" for layer in layers) or "`research/`",
            drafting_warnings=_warnings_text(state),
            retry_errors="none",
        )
        return issue_dispatch(work_dir, state, [spec], chat=_chat(state, "judging research sufficiency"))
    # D-62: `research/research-sufficiency.md` has no consumer — `mf sufficiency route` reads the
    # JSON and the gate-7 text is built from `state.sufficiency_followup`, so no view is rendered.
    routed = script_done(state, "sufficiency.route")
    if routed is None:
        step = script_step(
            work_dir,
            state,
            "sufficiency",
            "route",
            chat=_chat(state, "routing the sufficiency verdict"),
        )
        if step is not None:
            return step
        return {"transition": str(state["current_phase"])}
    target = SUFFICIENCY_TARGETS.get(str(result_of(routed).get("next")), "currency_check")
    mutate = _reset_research_budget if target == "research" else None
    return transition(work_dir, state, target, mutate=mutate)


def plan_research_sufficiency_followup_pending(work_dir: Path, state: dict) -> dict:
    """§2.1 row 7: re-research the missing layers if any, then always re-run sufficiency."""
    row = _gate_row(state)
    if row is None:
        return _issue_gate(work_dir, state, "sufficiency_followup")
    if missing_layers(state):
        # §2.1 row 7: the layers the router approved — and charged the research budget for — are the
        # ones the next research dispatch must use, so they are carried in `sufficiency_followup`
        # (status + `approved_layers`) instead of being recomputed from the gaps or the config.
        # D-116: with the research budget spent the approved set is empty, and the answered gate
        # walks on to the reviewer instead of buying an unpaid pass.
        def mutate(current: dict) -> None:
            _reset_research_budget(current)
            followup = dict(current.get("sufficiency_followup") or {"status": "answered"})
            followup["status"] = RESEARCH_SUBSET_STATUS
            current["sufficiency_followup"] = followup

        return transition(work_dir, state, "research", mutate=mutate)
    # D-112: nothing in scope to re-research — the answer continues the run the way `continue`
    # does, straight to the second reviewer pass, and the out-of-scope gaps stay as warnings.
    return transition(work_dir, state, "research_sufficiency")


def plan_research_insufficient_pending(work_dir: Path, state: dict) -> dict:
    """§2.1 row 8: continue with caveats, or cancel."""
    row = _gate_row(state)
    if row is None:
        return _issue_gate(work_dir, state, "insufficient")
    return transition(
        work_dir,
        state,
        "currency_check",
        warnings=[
            warning(
                "insufficient_research_accepted",
                memo_warning("continue_with_incomplete_research", state.get("language")),
                "research_insufficient_pending",
            )
        ],
        banners=[("research_insufficient_user_continue", {})],
    )


def _unchecked_currency(work_dir: Path) -> dict:
    from . import sources

    registry = sources.read_registry(work_dir)
    rows = [
        {"source_id": source_id, "status": "unchecked", "note": "currency-checker unavailable"}
        for source_id in sorted(registry.get("sources") or {})
    ]
    return {
        "checked_at": events.utc_now()[:10],
        "sources": rows,
        "blocking": [],
        "warnings": [row["source_id"] for row in rows],
    }


def plan_currency_check(work_dir: Path, state: dict) -> dict:
    """§2.1 row 9: deterministic verifications, the currency agent and the optional re-gate."""
    from . import sources

    if script_done(state, "sources.liveness") is None:
        step = script_step(
            work_dir,
            state,
            "sources",
            "liveness",
            chat=_chat(state, "checking that every source URL still resolves"),
        )
        if step is not None:
            return step
    if script_done(state, "sources.verify") is None:
        step = script_step(
            work_dir,
            state,
            "sources",
            "verify",
            chat=_chat(state, "checking identifier syntax and duplicates"),
        )
        if step is not None:
            return step
    row = dispatch_done(state, "currency-checker")
    if row is None:
        registry = sources.read_registry(work_dir)
        listing = ", ".join(sorted(registry.get("sources") or {})) or "none registered"
        spec = dispatch.spec(
            "currency",
            "currency-checker",
            "currency",
            [("research/currency.json", "currency")],
            inputs=["research/sources.json"],
            verify_report="`research/sources.json` carries `liveness` and `verification` per source",
            sources_list=listing,
            mcp_namespaces=_mcp_namespaces(work_dir),
            retry_errors="none",
        )
        return issue_dispatch(work_dir, state, [spec], chat=_chat(state, "checking source currency"))

    banners: list[tuple] = []
    warnings: list[dict] = []
    path = work_dir / sources.CURRENCY_PATH
    if row.get("status") == "fail" or not path.is_file():
        state_io.write_json_atomic(path, _unchecked_currency(work_dir))
        banners.append(("currency_checker_failed", {}))
        warnings.append(
            warning(
                "currency_unchecked",
                memo_warning("currency_unchecked", state.get("language")),
                "currency_check",
            )
        )
    try:
        document = stepctx.read_published(work_dir, sources.CURRENCY_PATH, state=state)
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        document = {"blocking": []}
    if not isinstance(document, dict):
        document = {"blocking": []}
    registry = sources.read_registry(work_dir)
    critical = {
        source_id
        for source_id, record in (registry.get("sources") or {}).items()
        if record.get("tier") == "critical"
    }
    blocking = [source_id for source_id in (document.get("blocking") or []) if source_id in critical]
    used = int((state.get("attempts") or {}).get("currency_regate") or 0)
    if blocking and used < limits.MAX_CURRENCY_REGATE:
        def mutate(current: dict) -> None:
            current.setdefault("attempts", {})["currency_regate"] = used + 1

        return transition(
            work_dir,
            state,
            "research_sufficiency",
            mutate=mutate,
            banners=[("currency_blocking_issues", {"count": len(blocking)})],
        )
    if blocking:
        banners.append(("currency_blocking_issues", {"count": len(blocking)}))
    return transition(work_dir, state, "source_pack", warnings=warnings, banners=banners)


def plan_source_pack(work_dir: Path, state: dict) -> dict:
    """§2.1 row 10: `sources pack --freeze`, then the conditional source-review gate."""
    from . import sources

    if script_done(state, "sources.pack") is None:
        step = script_step(
            work_dir,
            state,
            "sources",
            "pack",
            chat=_chat(state, "freezing the source pack"),
            extra=("--freeze", "--phase", "source_pack"),
        )
        if step is not None:
            return step
    gate = str((state.get("config") or {}).get("source_review_gate") or "auto")
    if gate == "off":
        return transition(work_dir, state, "drafting")
    if gate == "on":
        target = "source_review_pending"
    else:
        target = "source_review_pending" if sources.collect_exceptions(work_dir, state) else "drafting"
    # D-62: the gate-11 text is built by `sources.render_digest` from the registry and the frozen
    # pack JSON, so `research/source-pack.md` is not rendered for it.
    return transition(work_dir, state, target)


def plan_source_review_pending(work_dir: Path, state: dict) -> dict:
    """§2.1 row 11: continue or cancel."""
    row = _gate_row(state)
    if row is None:
        return _issue_gate(work_dir, state, "source_review")
    return transition(work_dir, state, "drafting")


# --- drafting, revision loop, client readiness, export ---------------------


def current_draft_sha(state: dict) -> str | None:
    """The authoritative sha of the current draft: its `published[]` row (§2.2, D-54)."""
    path = str(state.get("current_draft_path") or "")
    published = stepctx.published_sha(state, path) if path else None
    return published or state.get("current_draft_sha")


def _draft_checks(work_dir: Path, state: dict, draft_sha: str | None = None) -> tuple[bool, bool]:
    """`(lint_clean, citations_clean)` for the current draft sha (§4.5 п.1, D-41)."""
    sha = current_draft_sha(state) if draft_sha is None else draft_sha
    reports = [
        report_for_draft(work_dir, state, name, sha) for name in DETERMINISTIC_REPORTS.values()
    ]
    return bool(reports[0] and reports[0].get("clean")), bool(reports[1] and reports[1].get("clean"))


def _has_c09(work_dir: Path, state: dict) -> bool:
    """True when the citations report of the current draft holds a C-09 finding (D-204).

    Read by `plan_drafting` for v1 and nowhere else: a C-09 is a `major`, so `clean`, `_draft_checks`,
    the version flags and the polish path never see it. It buys the ordinary lint-fix round of v1.
    """
    report = report_for_draft(
        work_dir, state, DETERMINISTIC_REPORTS["draft.audit-citations"], current_draft_sha(state)
    )
    return any(
        isinstance(row, dict) and row.get("rule") == citations.PINPOINT_NOT_IN_RAW
        for row in ((report or {}).get("findings") or [])
    )


C09_FIX_ROUNDS = 1
"""D-204: a draft whose only defect is a C-09 gets one lint-fix round, whatever `lint_fix_rounds`
allows for blockers; a pinpoint that survives it becomes an appendix line, not another round."""

C09_INSTRUCTION = (
    "; each C-09 names a pinpoint whose number the saved text of its source does not print - "
    "fix the pinpoint or remove it"
)
"""D-204: what the writer of the v1 lint-fix round is told when the round was bought by a C-09."""


def _lint_steps(work_dir: Path, state: dict, draft: str, phase: str) -> dict | None:
    """One `draft finish` (anchor + lint + citations) for the current draft; None when it is current.

    D-117: the three used to be three script steps, and each cost an `mf next` round trip the
    orchestrator spent minutes getting round to. They are one step now, with the same contract.

    D-54: a finished check belongs to the `draft_sha` it ran on. Once a writer fix, a revision or
    the client polish changed the bytes, the reports of the previous version prove nothing, so the
    step is re-issued as the same `step_id` with `attempt+1` and `reason: rerun` — free, because it
    is the same action on a changed input (§2.2), not a retry of a failure.

    `inputs_sha` therefore covers the draft **and** the two reports the step would replace: an
    unchanged draft whose reports still stand is no reason to run again, while a report that drifted
    from `published[]` (D-41) is no answer at all and the checks have to run.
    """
    sha = current_draft_sha(state)
    reports = tuple(DETERMINISTIC_REPORTS[key] for key in ("draft.lint", "draft.audit-citations"))
    if all(report_for_draft(work_dir, state, name, sha) is not None for name in reports):
        return None
    return script_step(
        work_dir,
        state,
        "draft",
        "finish",
        chat=_chat(state, "checking the draft"),
        extra=("--draft", draft, "--phase", phase),
        inputs=(draft,) + reports,
    )


def lint_fix_dispatch(
    work_dir: Path,
    state: dict,
    *,
    draft: str,
    version: int,
    counter: str,
    rounds: int,
    note: str = "",
) -> dict | None:
    """One `memo-writer` fix round on the deterministic findings, or None when `lint_fix` is spent.

    §2.2 `lint_fix{draft_version}`: the counter is keyed by what is being fixed — `"1"` for the v1
    fix loop of §2.1 row 12, `"polish"` for the single round §2.1 row 14 allows after the polish.
    `note` extends the writer's instruction (D-204: what to do with a C-09); empty, it is unchanged.
    """
    used = int(((state.get("attempts") or {}).get("lint_fix") or {}).get(counter, 0))
    if used >= max(int(rounds), 0):
        return None
    spec = writer_spec(
        work_dir,
        state,
        task="lint-fix",
        version=version,
        canonical=draft,
        instructions="`lint.json` and `citations.json` - fix exactly the listed positions" + note,
        seed=True,
    )

    def mutate(current: dict) -> None:
        counters = current.setdefault("attempts", {}).setdefault("lint_fix", {})
        counters[counter] = used + 1

    return issue_dispatch(
        work_dir,
        state,
        [spec],
        chat=_chat(state, f"lint fix round {used + 1}/{rounds} ({counter})"),
        seeds={"writer": draft},
        mutate=mutate,
    )


def _writer_views(work_dir: Path, state: dict) -> dict | None:
    """The md views `memo-writer` reads: one per research layer (D-62: `render research` only)."""
    return render_step(
        work_dir,
        state,
        "research",
        [f"research/{layer}.json" for layer in research_layers(work_dir, state)],
        chat="rendering the research views",
    )


def plan_drafting(work_dir: Path, state: dict) -> dict:
    """§2.1 row 12: writer v1, anchors, lint, citations and the bounded fix loop.

    D-204: a C-09 in the citations report of v1 starts the same fix round a blocker would, and is
    read here and nowhere else. On its own it buys `C09_FIX_ROUNDS` and never raises
    `lint_not_converged`: a pinpoint that survives the round goes into the appendix, and the run
    continues to the ordinary export.
    """
    config = state.get("config") or {}
    draft = f"{DRAFTS_DIR}/v1.md"
    rows = episode(state)
    writers = [row for row in rows if purpose(row) == "dispatch:memo-writer"]
    if not writers:
        # D-57: `memo-writer` is told to read `research/*.md` and the source pack; the JSON-only
        # agents never write those views, so the render steps run before the writer is dispatched.
        step = _writer_views(work_dir, state)
        if step is not None:
            return step
        spec = writer_spec(
            work_dir,
            state,
            task="draft",
            version=1,
            canonical=draft,
            instructions="none - this is the first version",
            seed=False,
        )
        return issue_dispatch(work_dir, state, [spec], chat=_chat(state, "writing draft v1"))
    if writers[-1].get("status") == "fail" and len(writers) == 1:
        return finalize_step(work_dir, state, "writer_failed") or {"transition": "failed"}
    # D-117: `draft finish` inserts the anchors and runs both deterministic checks in one step.
    step = _lint_steps(work_dir, state, draft, "drafting")
    if step is not None:
        return step
    lint_clean, citations_clean = _draft_checks(work_dir, state)
    clean = lint_clean and citations_clean
    pinpoints = _has_c09(work_dir, state)
    if clean and not pinpoints:
        return transition(work_dir, state, "revision_loop", mutate=_enter_revision_loop)
    rounds = C09_FIX_ROUNDS if clean else int(config.get("lint_fix_rounds") or 1)
    step = lint_fix_dispatch(
        work_dir,
        state,
        draft=draft,
        version=1,
        counter="1",
        rounds=rounds,
        note=C09_INSTRUCTION if pinpoints else "",
    )
    if step is not None:
        return step
    return transition(
        work_dir,
        state,
        "revision_loop",
        mutate=_enter_revision_loop,
        banners=[] if clean else [("lint_not_converged", {})],
    )


def _enter_revision_loop(current: dict) -> None:
    if int(current.get("current_iteration") or 0) < 1:
        current["current_iteration"] = 1


def _reviewer_kinds(state: dict) -> list[str]:
    # D-165: the iteration a targeted citation pass produced is re-checked by its own reviewer set,
    # not by `config.reviewer_list` — `review aggregate` expects exactly the same set.
    targeted = review.targeted_reviewers(state, int(state.get("current_iteration") or 0))
    configured = (state.get("config") or {}).get("reviewer_list") or []
    return [kind for kind in (targeted or configured) if kind in dispatch.REVIEWER_AGENTS]


def _last_revision_next(state: dict) -> dict | None:
    """The most recent closed `revision next` of this revision-loop episode.

    `script_done` answers with the *first* closed step of a command key, which is the right answer
    everywhere a phase runs a script once. D-165 puts a second `revision next` -> mediator -> writer
    round inside one episode, and the writer must be routed by the branch that was just chosen, not
    by the one that opened the loop.
    """
    rows = [
        row
        for row in episode(state)
        if purpose(row) == "script:revision.next" and row.get("status") in SCRIPT_DONE_STATUSES
    ]
    return rows[-1] if rows else None


def _is_reviewer_dispatch(row: dict) -> bool:
    return row.get("kind") == KIND_DISPATCH and bool(
        set(agent_names(row)) & set(dispatch.REVIEWER_AGENTS.values())
    )


def plan_revision_loop(work_dir: Path, state: dict) -> dict:
    """§2.1 row 13 and §4.5: reviewers, aggregate, `revision next`, mediator, writer vN+1."""
    iteration = max(int(state.get("current_iteration") or 1), 1)
    rows = episode(state)
    last = rows[-1] if rows else None
    draft = str(state.get("current_draft_path") or f"{DRAFTS_DIR}/v{iteration}.md")

    if last is None:
        return _dispatch_reviewers(work_dir, state, _reviewer_kinds(state), iteration)
    if last.get("kind") == KIND_SCRIPT and last.get("status") == "fail":
        # §2.2: the loop is driven by the results of its script steps, and a CLI error is not one.
        return retry_failed_script(work_dir, state, last)
    if purpose(last) == "dispatch:memo-writer" and last.get("status") == "fail":
        # A43-1 / D-153: the retry budget is spent and the draft never changed — the seed v<N+1>
        # is a byte copy of v<N>, so reviewing it again would only re-grade the version they saw.
        return _writer_failed_exit(work_dir, state, iteration)
    key = purpose(last)
    if _is_reviewer_dispatch(last):
        step = script_step(
            work_dir,
            state,
            "review",
            "aggregate",
            chat=_chat(state, f"aggregating iteration {iteration}"),
            extra=("--iteration", str(iteration)),
        )
        if step is not None:
            return step
        return {"transition": str(state["current_phase"])}
    if key == "script:review.aggregate":
        aggregate = result_of(last)
        if aggregate.get("next") == "rerun_reviewers":
            # §4.5 п.3: an invalid reviewer output is retried once before it becomes a stub.
            kinds = [
                kind
                for kind in (aggregate.get("retry_reviewers") or [])
                if kind in dispatch.REVIEWER_AGENTS
            ]
            return _dispatch_reviewers(work_dir, state, kinds or _reviewer_kinds(state), iteration)
        return _issue_revision_next(work_dir, state, iteration)
    if key == "dispatch:revision-mediator":
        return _issue_revision_next(work_dir, state, iteration)
    if key == "script:revision.next":
        return _after_revision_next(work_dir, state, last, iteration)
    if key == "script:render.mediator":
        # The mediator view was rendered for the branch `revision next` already chose (D-57).
        routed = _last_revision_next(state)
        if routed is not None:
            return _after_revision_next(work_dir, state, routed, iteration)
    if key in ("dispatch:memo-writer", "script:draft.finish"):
        # §2.1 row 13: a new version is checked before the reviewers see it; D-54 re-issues both
        # checks when the writer changed the bytes again.
        step = _lint_steps(work_dir, state, draft, "revision_loop")
        if step is not None:
            return step
    return _dispatch_reviewers(work_dir, state, _reviewer_kinds(state), iteration)


def _dispatch_reviewers(work_dir: Path, state: dict, kinds: list[str], iteration: int) -> dict:
    """§4.5 п.2: every reviewer of the iteration in one message (`parallel: true`)."""
    specs = reviewer_specs(work_dir, state, kinds, iteration)
    return issue_dispatch(
        work_dir,
        state,
        specs,
        chat=_chat(state, f"iteration {iteration}: {len(specs)} reviewer(s) dispatched"),
    )


def _issue_revision_next(work_dir: Path, state: dict, iteration: int) -> dict:
    probe = mf_command(work_dir, "?", 1, "revision", "next") + ["--iteration", str(iteration)]
    previous = find_script_step(state, probe)
    step_id = str(previous["step_id"]) if previous is not None else next_step_id(state)
    attempt = int(previous["attempt"]) + 1 if previous is not None else 1
    step = script_step(
        work_dir,
        state,
        "revision",
        "next",
        chat=_chat(state, f"choosing the revision branch for iteration {iteration}"),
        extra=("--iteration", str(iteration), "--next-step", f"{step_id}-w{attempt}"),
    )
    if step is not None:
        return step
    return {"transition": str(state["current_phase"])}


def _after_revision_next(work_dir: Path, state: dict, row: dict, iteration: int) -> dict:
    result = result_of(row)
    branch = str(result.get("next"))
    # `mf revision next` owns `current_iteration` and has already bumped it for the writer branch;
    # the mediator and the reviews the branch refers to belong to the iteration it aggregated.
    iteration = int(result.get("iteration") or iteration)
    if branch == "rerun_reviewers":
        kinds = [kind for kind in (result.get("rerun_reviewers") or []) if kind in dispatch.REVIEWER_AGENTS]
        return _dispatch_reviewers(work_dir, state, kinds or _reviewer_kinds(state), iteration)
    if branch == "dispatch_mediator":
        spec = dispatch.spec(
            "mediator",
            "revision-mediator",
            f"mediator v{iteration}",
            [(review.mediator_path(iteration), "mediator")],
            inputs=[str(state.get("current_draft_path") or "")],
            iteration=iteration,
            draft_path=str(state.get("current_draft_path") or ""),
            draft_version=iteration,
            review_files=", ".join(
                f"`{review.review_path(iteration, kind)}`" for kind in _reviewer_kinds(state)
            ),
            issues_path="`state.iterations[]` of this iteration (see `mf state get`)",
            retry_errors="none",
        )
        return issue_dispatch(work_dir, state, [spec], chat=_chat(state, "mediating the review issues"))
    if branch == "dispatch_writer":
        version = int(result.get("current_iteration") or iteration + 1)
        canonical = str(result.get("draft_path") or f"{DRAFTS_DIR}/v{version}.md")
        # D-62: the writer edits from `reviews/v<N>-mediator.md`; the file exists whether the
        # mediator agent or `revision next` assembled the JSON, and only `mf render` writes the
        # view. The `.json` stays the state source of truth, the writer is given the `.md`.
        step = render_step(
            work_dir,
            state,
            "mediator",
            [review.mediator_path(iteration)],
            chat=f"rendering the mediator view of iteration {iteration}",
            extra=("--iteration", str(iteration)),
        )
        if step is not None:
            return step
        instructions = f"`{view_path(review.mediator_path(iteration))}` - edit only the named sections"
        if result.get("targeted"):
            # D-165, D-212: branch 9 bought one pass for its targeted `citations` blockers (`unsupported_claim`,
            # `source_drift`), nothing wider. D-208: a token only where a pack source holds the rule —
            # otherwise the statement goes or is qualified.
            instructions += (
                ". Targeted pass: for each named item, add the source token where a pack source contains "
                "the rule; otherwise withdraw the statement or qualify it as unresolved. Change nothing else."
            )
        spec = writer_spec(
            work_dir,
            state,
            task="revision",
            version=version,
            canonical=canonical,
            instructions=instructions,
            seed=True,
        )
        return issue_dispatch(
            work_dir,
            state,
            [spec],
            chat=_chat(state, f"writing draft v{version}"),
            step_id=str(result.get("next_step") or f"{row['step_id']}-w{row['attempt']}"),
            seeds={"writer": canonical},
        )
    return transition(work_dir, state, "client_readiness")


CLIENT_READINESS_PATH = "reviews/final-client-readiness.json"

POLISH_FIX_ROUNDS = 1
"""§2.2 `lint_fix{polish}`: exactly one writer fix is allowed after the client polish (D-57)."""

POLISH_RECHECK = review.POLISH_RECHECK
"""D-211: slot, path and budget key of the citations re-check of the polish — never a reviewer kind."""

WHOLE_DOCUMENT = review.UNVERIFIED_SECTION_ID
"""`section_id` of a finding or an issue that belongs to no one section (`document`)."""

PREAMBLE_SECTION = "preamble"
"""D-211: the owner of the lines of a draft above its first heading."""

CROSS_REFERENCE_KINDS: tuple[str, ...] = ("executive_summary", "conclusion", "recommendations")
"""D-211: the sections a polish keeps in step with the polished one (`agents/memo-writer.md`)."""

DISPOSITION_ACTIONS: dict[str, tuple[str, ...]] = {
    "citations": ("polish", "manual_review"),
    "logic": ("polish", "leave"),
    "counterarguments": ("polish", "leave"),
}
"""D-211: what the readiness reviewer may decide for an open major, by the class of its row."""

DISPOSITION_DEFAULTS: dict[str, str] = {"citations": "manual_review", "logic": "leave", "counterarguments": "leave"}
"""D-211: a missing disposition, or one the class does not allow, counts as this."""

DISPOSITION_STATUS: dict[str, str] = {"manual_review": "manual_review", "leave": "left"}
"""D-211: the status pass 1 settles at once; a `polish` row stays `open` until the polish is re-checked."""

OPEN_MAJORS_REASON = "open_substance_majors"
POLISH_OUT_OF_SCOPE_REASON = "polish_out_of_scope"
RECHECK_BLOCKER_REASON = "polish_recheck_blocker"
"""D-211: the `final_status_reasons[]` codes of the readiness step's settlement."""

APPROVED_FAMILIES: tuple[str, ...] = ("approved", "accepted_early", "client_ready")
"""D-211: the `final_status` families a settled finding turns into `manual_review_required_on_v<N>`."""

OPEN_FINDINGS_NONE = "none"
OPEN_FINDINGS_RECHECK = "raised by the polish re-check (for information, no disposition)"
"""D-211: the `${open_findings}` of a run without open majors, and the mark of a re-check row."""


def _client_verdict(work_dir: Path, state: dict) -> dict:
    """The readiness verdict, read against `published[]` — drift is no verdict at all (D-41)."""
    try:
        document = stepctx.read_published(work_dir, CLIENT_READINESS_PATH, state=state)
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _readiness_degraded(work_dir: Path, state: dict, version: int) -> dict:
    """§2.1 row 14: no usable readiness verdict — leave for `export` under manual review (N-11)."""
    return _to_export(
        work_dir,
        state,
        version,
        mutate=lambda current: _manual_review(current, "incomplete_review", version),
        banners=[("client_readiness_reviewer_failed", {})],
    )


# --- D-211: the open majors at the last reader -----------------------------


def prepolish_path(version: int) -> str:
    """`reviews/v<N>-prepolish.md`: the draft as the polish writer found it, the baseline of D-211."""
    return f"reviews/v{int(version)}-prepolish.md"


def _open_majors(state: dict) -> list[dict]:
    """The rows of `state.open_substance_majors` (D-210), the very dicts — a mutator edits them in place."""
    return [row for row in (state.get("open_substance_majors") or []) if isinstance(row, dict)]


BLOCKER_MARK = "blocker"
"""D-213: the `severity` of a blocker row, and its mark in `${open_findings}` and `${recheck_scope}`."""

BLOCKER_SCOPE_NOTE = (
    "The resolution of a blocker finding covers every statement in these sections that rests on it: "
    "record it `resolved` only if none of them still carries the withdrawn statement."
)
"""D-213, gate R1-2: the `${recheck_scope}` sentence of a polish that touched a blocker row."""

BLOCKER_BANNER = "max_iterations_with_blockers"
"""D-213: the fallback row whose banner counts the blockers left — recounted when the settlement lifts one."""


def _is_blocker_row(row: dict) -> bool:
    """D-213: a row that stands for a `remaining_blocking_issues[]` entry (`blocker_of`), not a major."""
    return isinstance(row.get("blocker_of"), dict)


def _polish_issued(state: dict) -> bool:
    """True once the polish writer was dispatched: `attempts.client_polish` is spent with it (§2.2)."""
    return int((state.get("attempts") or {}).get("client_polish") or 0) > 0


def open_findings_text(state: dict) -> str:
    """`${open_findings}` of the readiness prompt: one `id · class · section · issue · suggestion` line per row.

    D-211: not gated on the draft sha, unlike `known_blockers_text` — the rows belong to the version the
    loop left on, and the second pass after the polish needs them all the more. Once the polish was
    issued, each loop row carries its status; a re-check row is marked as information only.
    """
    rows = _open_majors(state)
    if not rows:
        return OPEN_FINDINGS_NONE
    polished = _polish_issued(state)
    lines = []
    for row in rows:
        parts = [
            " ".join(str(row.get(field) or "").split())
            for field in ("id", "class", "section_id", "issue", "suggestion")
        ]
        if _is_blocker_row(row):
            parts.insert(2, BLOCKER_MARK)  # D-213: `· blocker` right after the class
        line = "- " + " · ".join(part for part in parts if part)
        if row.get("origin") == "recheck":
            line += f" · {OPEN_FINDINGS_RECHECK}"
        elif polished:
            line += f" · status: {row.get('status')}"
        lines.append(line)
    return "\n".join(lines)


def _unique_by_id(rows: object, field: str) -> dict[str, str]:
    """`id -> row[field]` for the rows whose `id` occurs exactly once (D-211).

    Two rows for one id contradict each other or say nothing new; either way neither is read, whatever
    their order — the id falls back to what a missing row means. The other ids stay usable.
    """
    items = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]
    counts: dict[str, int] = {}
    for row in items:
        counts[str(row.get("id"))] = counts.get(str(row.get("id")), 0) + 1
    return {str(row.get("id")): str(row.get(field)) for row in items if counts[str(row.get("id"))] == 1}


def _apply_dispositions(current: dict, document: dict) -> None:
    """D-211 pass 1: `manual_review` and `leave` settle a loop row at once; `polish` keeps it `open`.

    A missing disposition, a duplicated id, or an action outside the row's class (`leave` on
    `citations`, `manual_review` on `logic`) counts as the class default.

    D-216: the `note` of a disposition that is applied as given is kept on the row
    (`disposition_note`), so `summary.md` can say why the row stays; a default carries no note.
    """
    given = _unique_by_id(document.get("dispositions"), "action")
    notes = _unique_by_id(document.get("dispositions"), "note")
    for row in _open_majors(current):
        if row.get("origin") != "loop" or row.get("status") != "open":
            continue
        klass = str(row.get("class") or "")
        action = given.get(str(row.get("id")))
        if action not in DISPOSITION_ACTIONS.get(klass, ()):
            action = DISPOSITION_DEFAULTS.get(klass, "leave")
        elif notes.get(str(row.get("id")), "").strip():
            row["disposition_note"] = notes[str(row.get("id"))].strip()
        if action in DISPOSITION_STATUS:
            row["status"] = DISPOSITION_STATUS[action]


def _owned_lines(document: dict) -> dict[str, list[str]]:
    """Every line of a parsed draft under the innermost section holding it; a heading owns itself."""
    owned: dict[str, list[str]] = {}
    for number, line in enumerate(document["lines"], start=1):
        owned.setdefault(lint.section_of(document, number) or PREAMBLE_SECTION, []).append(line)
    return owned


def _changed_sections(before: dict, after: dict) -> list[str]:
    """The sections whose own lines differ between two parsed drafts, in the order of `after`."""
    old, new = _owned_lines(before), _owned_lines(after)
    order = list(new) + [section_id for section_id in old if section_id not in new]
    return [section_id for section_id in order if old.get(section_id) != new.get(section_id)]


def _subtree(documents: tuple, roots: set[str]) -> set[str]:
    """`roots` plus every section below one of them, in any of the parsed drafts."""
    found = set(roots)
    for document in documents:
        parents = {section["section_id"]: section.get("parent") for section in document["sections"]}
        for section_id in parents:
            seen: set[str] = set()
            node = parents.get(section_id)
            while node and node not in seen:
                if node in roots:
                    found.add(section_id)
                    break
                seen.add(node)
                node = parents.get(node)
    return found


def _source_ids(document: dict, section_id: str) -> set[str]:
    """The `[[src:]]` ids on the own lines of one section."""
    return {
        str(token["id"])
        for token in document["src_tokens"]
        if (token.get("section_id") or PREAMBLE_SECTION) == section_id
    }


def _parse_pair(before: str, after: str, language: str) -> tuple[dict, dict]:
    """Both drafts under the memo language's grammar: English would give «Резюме» no `kind` at all."""
    grammar = lint.grammar(language)
    return lint.parse_draft(before, grammar), lint.parse_draft(after, grammar)


def polish_scope_errors(
    before: str, after: str, allowed: set[str] | None, *, language: str = i18n.DEFAULT
) -> list[str]:
    """D-211: what a polish changed outside its instructions, on non-overlapping section content.

    `allowed` — the section ids of the readiness issues — widens to their descendants and to the
    `executive_summary`/`conclusion`/`recommendations` sections the writer keeps in step; `None` (a
    `document` issue) lifts the section restriction. A changed section whose `[[src:]]` id set grew is
    an error in every case: a polish withdraws or softens, it never brings a new authority.
    """
    old, new = _parse_pair(before, after, language)
    permitted: set[str] | None = None
    if allowed is not None:
        permitted = _subtree((old, new), {str(section_id) for section_id in allowed})
        permitted |= {
            section["section_id"]
            for document in (old, new)
            for section in document["sections"]
            if section.get("kind") in CROSS_REFERENCE_KINDS
        }
    errors: list[str] = []
    for section_id in _changed_sections(old, new):
        if permitted is not None and section_id not in permitted:
            errors.append(f"section_out_of_scope: {section_id}")
        added = sorted(_source_ids(new, section_id) - _source_ids(old, section_id))
        if added:
            errors.append(f"new_source_token: {section_id}: {', '.join(added)}")
    return errors


def _held_rows(rows: list[dict], documents: tuple, changed: list[str]) -> list[dict]:
    """The `polish` rows (loop, still `open`) that a changed section holds.

    A row is held by its own section and every descendant; a `document` row by any changed section.
    """
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("origin") == "loop"
        and row.get("status") == "open"
        and _holds(row, documents, changed)
    ]


def _holds(row: dict, documents: tuple, changed: list[str]) -> bool:
    """Did the polish change the row's section or a descendant — any section, for a `document` row?"""
    if not changed:
        return False
    section_id = str(row.get("section_id") or WHOLE_DOCUMENT)
    return section_id == WHOLE_DOCUMENT or bool(_subtree(documents, {section_id}) & set(changed))


def _changed_cross_references(documents: tuple, changed: list[str]) -> list[str]:
    """The changed sections of kind `executive_summary`, `conclusion` or `recommendations`, in `changed` order."""
    kinds = {
        section["section_id"]
        for document in documents
        for section in document["sections"]
        if section.get("kind") in CROSS_REFERENCE_KINDS
    }
    return [section_id for section_id in changed if section_id in kinds]


def _row_scope(row: dict, documents: tuple, changed: list[str]) -> set[str]:
    """The sections a row's resolution covers: its own and every descendant.

    D-213 (gate R1-2): a blocker row also covers every changed cross-reference section — the summary
    bullet or the conclusion item that rested on the withdrawn statement.
    """
    scope = _subtree(documents, {str(row.get("section_id") or WHOLE_DOCUMENT)})
    if _is_blocker_row(row):
        scope |= set(_changed_cross_references(documents, changed))
    return scope


def _blocked(row: dict, scope: set[str], blockers: list[dict]) -> bool:
    """Does a re-check blocker sit in the row's scope — anywhere, for a `document` row?"""
    whole = str(row.get("section_id") or WHOLE_DOCUMENT) == WHOLE_DOCUMENT
    return any(whole or str(blocker.get("section_id")) in scope for blocker in blockers)


def polished_findings(rows: list[dict], before: str, after: str, *, language: str = i18n.DEFAULT) -> list[dict]:
    """D-211: the `polish` rows the polish touched — the findings its citations re-check is about."""
    documents = _parse_pair(before, after, language)
    return _held_rows(rows, documents, _changed_sections(*documents))


BASELINE_UNAVAILABLE = "baseline_unavailable"
DRAFT_UNAVAILABLE = "draft_unavailable"
"""D-211: the `polish_check` errors of a scope check that could not read its inputs — a failed check."""


def _published_text(work_dir: Path, state: dict, path: str) -> str | None:
    """The text of a canonical file `published[]` vouches for, or None: unpublished, missing or drifted."""
    if stepctx.published_entry(state, path) is None:
        return None
    try:
        return str(stepctx.read_published(work_dir, path, state=state))
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        return None


def _polish_inputs(work_dir: Path, state: dict, draft: str, version: int) -> tuple[tuple[str, str] | None, str | None]:
    """`((baseline, current draft), None)`, or `(None, <the error>)` when one cannot be read as published."""
    before = _published_text(work_dir, state, prepolish_path(version))
    if before is None:
        return None, BASELINE_UNAVAILABLE
    after = _published_text(work_dir, state, draft)
    if after is None:
        return None, DRAFT_UNAVAILABLE
    return (before, after), None


def _polish_texts(work_dir: Path, state: dict, draft: str, version: int) -> tuple[str, str] | None:
    """The published baseline and the current draft, or None when either cannot be read as published."""
    return _polish_inputs(work_dir, state, draft, version)[0]


def _publish_baseline(work_dir: Path, state: dict, draft: str, version: int, step_id: str) -> dict:
    """D-211 step 2: the `published[]` entry of the baseline, written with the polish dispatch.

    Idempotent: bytes the file already holds are not rewritten. It is never replaced once the polish
    step exists — the polish is issued once — and every later reader takes the published copy.
    """
    canonical = prepolish_path(version)
    payload = (work_dir / draft).read_bytes()
    sha = state_io.sha256_bytes(payload)
    target = work_dir / canonical
    if target.is_file() and state_io.sha256_file(target) == sha:
        return {"canonical_path": canonical, "sha256": sha, "by": "command", "step_id": step_id, "at": events.utc_now()}
    return stepctx.publish_file(work_dir, draft, canonical, by="command", step_id=step_id)


def _polish_step(state: dict) -> dict | None:
    """The polish writer: the first `memo-writer` dispatch of the `client_readiness` episode."""
    return next((row for row in episode(state) if purpose(row) == "dispatch:memo-writer"), None)


def _polish_allowed(work_dir: Path, state: dict) -> set[str] | None:
    """The sections of the pass-1 readiness issues the polish executed; None once one is `document`."""
    issues = [issue for issue in (_client_verdict(work_dir, state).get("issues") or []) if isinstance(issue, dict)]
    sections = {str(issue.get("section_id") or "") for issue in issues}
    return None if WHOLE_DOCUMENT in sections else sections - {""}


def _check_polish_scope(work_dir: Path, state: dict, draft: str, version: int) -> dict | None:
    """D-211 step 3: the scope check of the polish output, once, before its lint round.

    The first entry after the polish writer stores `polish_check` and plans again, so a re-entry —
    the lint-fix writer's included — never recomputes it; the lint fix edits lint positions only and
    stays outside the check by construction. A stored error leads to `_polish_out_of_scope`. A
    baseline or a draft that cannot be read as published is a failed check, never a skipped one.
    """
    if not _open_majors(state) or _polish_step(state) is None:
        return None
    check = state.get("polish_check")
    if not isinstance(check, dict):
        texts, missing = _polish_inputs(work_dir, state, draft, version)
        errors = (
            [str(missing)]
            if texts is None
            else polish_scope_errors(
                texts[0], texts[1], _polish_allowed(work_dir, state), language=md_fallback.memo_language(state)
            )
        )
        return _store_polish_check(work_dir, state, errors)
    if check.get("errors"):
        return _polish_out_of_scope(work_dir, state, draft, version)
    return None


def _store_polish_check(work_dir: Path, state: dict, errors: list[str]) -> dict:
    """Write `state.polish_check` for the current draft sha and plan again on the fresh state (D-211)."""
    record = {"draft_sha": str(current_draft_sha(state) or ""), "errors": list(errors)}

    def mutator(current: dict) -> None:
        current["polish_check"] = record

    state_io.write_state(work_dir, mutator)
    return {"transition": str(state.get("current_phase"))}


def _require_manual_review(current: dict, reasons: list[str], version: int) -> None:
    """D-211: an approved, accepted or `client_ready` status — or none — becomes `manual_review_required_on_v<N>`.

    Any other label (a forced exit, a manual review) is kept; either way the reasons are added once.
    """
    status = str(current.get("final_status") or "")
    family, _ = md_fallback.status_family(status)
    if not status or family in APPROVED_FAMILIES:
        current["final_status"] = f"manual_review_required_on_v{max(int(version), 1)}"
    for reason in reasons:
        if reason not in current.setdefault("final_status_reasons", []):
            current["final_status_reasons"].append(reason)


def _reviewed_version(work_dir: Path, state: dict) -> tuple[int, str] | None:
    """The last `draft_versions[]` row whose bytes on disk the review loop reviewed (`iterations[].draft_sha`)."""
    reviewed = {
        str(row.get("draft_sha"))
        for row in (state.get("iterations") or [])
        if isinstance(row, dict) and row.get("draft_sha")
    }
    rows = [row for row in (state.get("draft_versions") or []) if isinstance(row, dict) and row.get("path")]
    for row in sorted(rows, key=lambda item: int(item.get("version") or 0), reverse=True):
        path = work_dir / str(row["path"])
        if path.is_file():
            sha = state_io.sha256_file(path)
            if sha in reviewed:
                return int(row.get("version") or 1), sha
    return None


def _scope_inputs_lost(work_dir: Path, state: dict, draft: str, version: int) -> dict:
    """D-211: a failed scope check with no baseline to put back — the same exit, reason `polish_out_of_scope`.

    The pin names the last version whose bytes the review loop reviewed and that are still on disk.
    With none, there is no pin: `export` makes its ordinary choice (the last version whose checks
    passed on its current bytes, else the last version — the polished draft itself), under manual review.
    """
    fallback = _reviewed_version(work_dir, state)
    row = next((item for item in (state.get("draft_versions") or []) if item.get("path") == draft), None)
    label = fallback[0] if fallback is not None else (int(row["version"]) if row is not None else int(version))

    def fail(current: dict) -> None:
        if fallback is not None:
            current["export_pin"] = {"version": fallback[0], "sha256": fallback[1]}
        _require_manual_review(current, [POLISH_OUT_OF_SCOPE_REASON], label)

    return _to_export(work_dir, state, version, mutate=fail)


def _polish_out_of_scope(work_dir: Path, state: dict, draft: str, version: int) -> dict:
    """D-211: the baseline goes back onto the polished canonical, and the run leaves for `export` pinned to it.

    One state write: the republication, the re-aligned `draft_versions[]` row of that path (the
    computation `sync_draft_versions` makes, which only runs at the top of `mf next`), the pin, the
    manual review of that version (an existing non-approved label is kept) and the settlement. The
    pin makes `export_draft_sha` name exactly these bytes even when an earlier version is the last one
    checked clean. Without a readable baseline, `_scope_inputs_lost` takes the same exit.
    """
    if _published_text(work_dir, state, prepolish_path(version)) is None:
        return _scope_inputs_lost(work_dir, state, draft, version)
    polish = _polish_step(state)
    entry = stepctx.publish_file(
        work_dir,
        prepolish_path(version),
        draft,
        by="command",
        step_id=str(polish["step_id"]) if polish is not None else None,
    )
    sha = str(entry["sha256"])
    flags = {
        field: bool((report_for_draft(work_dir, state, DETERMINISTIC_REPORTS[key], sha) or {}).get("clean"))
        for field, key in (("lint_clean", "draft.lint"), ("citations_clean", "draft.audit-citations"))
    }
    row = next((item for item in (state.get("draft_versions") or []) if item.get("path") == draft), None)
    pinned = int(row["version"]) if row is not None else int(version)

    def revert(current: dict) -> None:
        stepctx.merge_published(current, [entry])
        current["current_draft_sha"] = sha
        for item in current.get("draft_versions") or []:
            if item.get("path") == draft:
                item["sha256"] = sha
                item.update(flags)
                item["checked_at"] = events.utc_now() if any(flags.values()) else None
        current["export_pin"] = {"version": pinned, "sha256": sha}
        _require_manual_review(current, [POLISH_OUT_OF_SCOPE_REASON], pinned)

    return _to_export(work_dir, state, version, mutate=revert)


def _polish_recheck_steps(state: dict, version: int) -> list[str]:
    """The step ids that dispatched the polish re-check of `version`, counted by its canonical path."""
    target = review.review_path(int(version), POLISH_RECHECK)
    found: list[str] = []
    for row in state.get("steps") or []:
        if not isinstance(row, dict) or str(row.get("step_id")) in found:
            continue
        if any(
            str(entry.get("canonical_path") or entry.get("canonical") or "") == target
            for entry in (row.get("expected_outputs") or [])
        ):
            found.append(str(row.get("step_id")))
    return found


def _recheck_scope(rows: list[dict], changed: list[str], documents: tuple = ()) -> str:
    """`${recheck_scope}`: the sections to grade and the findings that were polished there.

    D-213 (gate R1-2): a blocker row brings in every changed cross-reference section, and the line
    says that its resolution covers the statements there that rest on it.
    """
    sections: list[str] = []
    for row in rows:
        section_id = str(row.get("section_id") or WHOLE_DOCUMENT)
        candidates = list(changed) if section_id == WHOLE_DOCUMENT else [section_id]
        if _is_blocker_row(row):
            candidates += _changed_cross_references(documents, changed)
        for candidate in candidates:
            if candidate not in sections:
                sections.append(candidate)
    sections.sort(key=review._section_order)  # noqa: SLF001 - the document order D-210 sorts rows by
    findings = "; ".join(
        " · ".join(
            [str(row.get("id")), str(row.get("class"))]
            + ([BLOCKER_MARK] if _is_blocker_row(row) else [])
            + [" ".join(str(row.get("issue") or "").split())]
        )
        for row in rows
    )
    line = (
        f"Scope: sections {', '.join(sections)}; the open findings {findings} were polished. "
        "Grade CIT-01/02/04 on these sections. Record one `resolutions` row per listed id."
    )
    return f"{line} {BLOCKER_SCOPE_NOTE}" if any(_is_blocker_row(row) for row in rows) else line


def _dispatch_polish_recheck(work_dir: Path, state: dict, draft: str, version: int) -> dict | None:
    """D-211 step 4: the one citations re-check of a polish that changed a section holding a `polish` row.

    Class-independent: a softened `logic` or `counterarguments` sentence can still state law. The slot
    and path are `citations_polish`, so `failure_retry_attempts(…, "citations_polish")` and the
    `<N>:citations_polish` key of `reviewer_json_retry` carry its retries and leave `<N>:citations` alone.
    """
    rows = _open_majors(state)
    if not rows or not _polish_issued(state):
        return None
    if len(_polish_recheck_steps(state, version)) >= limits.MAX_POLISH_RECHECK:
        return None
    if not any(row.get("origin") == "loop" and row.get("status") == "open" for row in rows):
        return None  # no `polish` row: nothing for a re-check to judge
    texts, missing = _polish_inputs(work_dir, state, draft, version)
    if texts is None:
        # A scope input lost after the scope check fails that check: the stored error takes the exit.
        return _store_polish_check(work_dir, state, [str(missing)])
    documents = _parse_pair(texts[0], texts[1], md_fallback.memo_language(state))
    changed = _changed_sections(*documents)
    polished = _held_rows(rows, documents, changed)
    if not polished:
        return None
    base = reviewer_specs(work_dir, state, ["citations"], version)[0]
    # D-214 (gate R1-7): the re-check grades the polished sections only and inherits no carry-over.
    extra = dict(base["extra"], recheck_scope=_recheck_scope(polished, changed, documents), carry_over="none")
    spec = dispatch.spec(
        POLISH_RECHECK,
        base["agent"],
        f"citations re-check v{version}",
        [(review.review_path(version, POLISH_RECHECK), "review")],
        inputs=list(base["inputs"]),
        **extra,
    )
    return issue_dispatch(work_dir, state, [spec], chat=_chat(state, "re-checking the polished findings"))


def _polish_recheck(work_dir: Path, state: dict, version: int) -> dict:
    """D-211 step 5: what the closed re-check says, read against the post-polish draft sha.

    `blockers` are kept from every re-check that is published, not a stub, schema-valid and for this
    sha — `downgraded` or not, the synthetic `unverified_hard_fail` blocker included. `usable` gates
    only `resolutions`: a valid, not downgraded re-check with no `unverified_hard_fail` issue and no
    `document` blocker. `majors` become the `origin: recheck` rows.
    """
    outcome: dict = {"ran": False, "usable": False, "resolutions": {}, "blockers": [], "majors": []}
    if not _polish_recheck_steps(state, version):
        return outcome
    outcome["ran"] = True
    sha = str(current_draft_sha(state) or "")
    result = review.read_polish_recheck(work_dir, state, version, sha)
    document = result.get("document")
    if (
        not isinstance(document, dict)
        or result.get("stub")
        or str(document.get("draft_sha") or "") != sha
        or schema.validate(document, "review")
    ):
        return outcome
    issues = [issue for issue in (document.get("issues") or []) if isinstance(issue, dict)]
    outcome["blockers"] = [dict(issue) for issue in issues if issue.get("severity") == "blocker"]
    outcome["majors"] = [dict(issue) for issue in issues if issue.get("severity") == "major"]
    outcome["usable"] = (
        bool(result.get("valid"))
        and not result.get("downgraded")
        and not any(issue.get("category") == review.UNVERIFIED_HARD_FAIL for issue in issues)
        and not any(str(issue.get("section_id")) == WHOLE_DOCUMENT for issue in outcome["blockers"])
    )
    if outcome["usable"]:
        # A duplicated id resolves nothing, whatever the order of its rows; the other ids stand.
        outcome["resolutions"] = _unique_by_id(document.get("resolutions"), "status")
    return outcome


def _recheck_row(issue: dict, number: int, version: int) -> dict:
    """A new major of the re-check as an `origin: recheck` row: `summary.md` only, never a disposition."""
    return {
        "id": f"om-{number}",
        "class": "citations",
        "reviewer": "citations",
        "section_id": str(issue.get("section_id") or WHOLE_DOCUMENT),
        "category": str(issue.get("category") or ""),
        "issue_category": issue.get("issue_category"),
        "issue": str(issue.get("issue") or ""),
        "issue_client": issue.get("issue_client"),
        "suggestion": str(issue.get("suggestion") or ""),
        "from_iteration": max(int(version), 1),
        "origin": "recheck",
        "status": "open",
    }


def _row_number(row: dict) -> int:
    text = str(row.get("id") or "")
    return int(text[3:]) if text.startswith("om-") and text[3:].isdigit() else 0


def _rows_after_polish(work_dir: Path, state: dict, draft: str, version: int) -> list[dict]:
    """D-211 steps 5-6: the rows readiness pass 2 sees — every `polish` row settled, re-check majors added.

    A `polish` row is `resolved` only when the polish touched it, the re-check is usable, its
    `resolutions` marks the row `resolved`, and no re-check blocker sits in the row's scope — its
    section or below it, anywhere for a `document` row, and for a blocker row also the changed
    cross-reference sections (D-213); otherwise it is `unresolved`.
    """
    rows = copy.deepcopy(_open_majors(state))
    outcome = _polish_recheck(work_dir, state, version)
    texts = _polish_texts(work_dir, state, draft, version) if outcome["ran"] else None
    documents: tuple = ()
    changed: list[str] = []
    listed: set[str] = set()
    if texts is not None:
        documents = _parse_pair(texts[0], texts[1], md_fallback.memo_language(state))
        changed = _changed_sections(*documents)
        listed = {str(row.get("id")) for row in _held_rows(rows, documents, changed)}
    for row in rows:
        if row.get("origin") != "loop" or row.get("status") != "open":
            continue
        resolved = (
            str(row.get("id")) in listed
            and outcome["usable"]
            and outcome["resolutions"].get(str(row.get("id"))) == "resolved"
            and not _blocked(row, _row_scope(row, documents, changed), outcome["blockers"])
        )
        row["status"] = "resolved" if resolved else "unresolved"
    if outcome["majors"] and not any(row.get("origin") == "recheck" for row in rows):
        number = max((_row_number(row) for row in rows), default=0)
        for issue in outcome["majors"]:
            number += 1
            rows.append(_recheck_row(issue, number, version))
    return rows


def _moved_finding(row: dict) -> dict:
    """A settled `citations` row as a `remaining_blocking_issues[]` entry: `severity: major`, its client sentence."""
    entry = {
        "severity": "major",
        "category": str(row.get("category") or ""),
        "section_id": str(row.get("section_id") or WHOLE_DOCUMENT),
        "issue": str(row.get("issue") or ""),
        "suggestion": str(row.get("suggestion") or ""),
        "source_reviewer": str(row.get("reviewer") or "citations"),
    }
    for field in ("issue_category", "issue_client"):
        if row.get(field):
            entry[field] = row[field]
    return entry


def _settle_open_majors(
    current: dict, blockers: list[dict], version: int, *, lifted: set[str] | frozenset = frozenset()
) -> None:
    """D-211 step 7: the open majors at the transition to `export`, inside that very state write.

    A loop row still `open` had no completed polish with a usable re-check, so it is `unresolved`.
    Every loop `citations` row left `manual_review` or `unresolved` joins `remaining_blocking_issues`
    (reason `open_substance_majors`), and so does every re-check blocker as the reviewer wrote it
    (reason `polish_recheck_blocker`). If anything joined, an approved, accepted or `client_ready`
    status becomes `manual_review_required_on_v<its version>`; a forced exit or a manual review keeps
    its label and only gains the reasons. `logic`/`counterarguments` rows and `origin: recheck` rows
    stay for `summary.md` and never change the status. An empty list changes nothing.

    D-213: a blocker row (`blocker_of`) is never moved. Its blocker leaves `remaining_blocking_issues`
    only when its id is in `lifted` — the evidence `_lifted_blockers` read at this very transition;
    otherwise the blocker stays as it is and the row is `unresolved`. A lifted blocker recounts the
    blocker banner, and a forced exit whose only reason was that blocker becomes `approved_on_v<N>`.
    """
    rows = _open_majors(current)
    if not rows and not blockers:
        return
    remaining = current.setdefault("remaining_blocking_issues", [])
    removed = _settle_blocker_rows(rows, remaining, lifted)
    for row in rows:
        if row.get("origin") == "loop" and row.get("status") == "open":
            row["status"] = "unresolved"
    reasons: list[str] = []
    moved = [
        _moved_finding(row)
        for row in rows
        if row.get("origin") == "loop"
        and row.get("class") == "citations"
        and row.get("status") in ("manual_review", "unresolved")
        and not _is_blocker_row(row)
    ]
    for group, reason in ((moved, OPEN_MAJORS_REASON), ([dict(row) for row in blockers], RECHECK_BLOCKER_REASON)):
        for entry in group:
            if entry not in remaining:
                remaining.append(entry)
        if group:
            reasons.append(reason)
    if removed:
        _recount_blocker_banner(current)  # after the moves: the count is what the Status section lists
    family, number = md_fallback.status_family(str(current.get("final_status") or ""))
    left = list(current.get("final_status_reasons") or [])
    if removed and not remaining:
        if number and family == "forced_exit_with_remaining_issues" and left == [revision.REASON_UNRESOLVED_BLOCKERS]:
            # D-213: the same label a clean branch-9 re-check gets from branch 4.
            current["final_status"] = f"approved_on_v{number}"
            current["final_status_reasons"] = []
        elif revision.REASON_UNRESOLVED_BLOCKERS in left:
            # D-213 fix round 1: no blocker is left, so the reason that says one is goes; the label and
            # every other reason stay as the rules above decide.
            current["final_status_reasons"] = [
                reason for reason in left if reason != revision.REASON_UNRESOLVED_BLOCKERS
            ]
    if not reasons:
        return
    _require_manual_review(current, reasons, int(number) if number else version)


def _settle_blocker_rows(rows: list[dict], remaining: list, lifted: set[str] | frozenset) -> bool:
    """D-213: lift the blocker of every row in `lifted`, keep every other one; True once one was removed.

    A lifted row is `resolved` and its linked entry leaves `remaining_blocking_issues`; any other blocker
    row that was `open` or `resolved` is `unresolved` — a saved `resolved` alone never lifts a blocker.
    """
    removed = False
    for row in rows:
        if not _is_blocker_row(row):
            continue
        if str(row.get("id")) in lifted:
            link = review.blocker_link(row["blocker_of"])
            entry = next(
                (item for item in remaining if isinstance(item, dict) and review.blocker_link(item) == link), None
            )
            if entry is not None:
                remaining.remove(entry)
                removed = True
            row["status"] = "resolved"
        elif row.get("status") in ("open", "resolved"):
            row["status"] = "unresolved"
    return removed


def _recount_blocker_banner(current: dict) -> None:
    """D-213 (gate R1-4): one blocker banner counting what is left, or none once nothing is left."""
    banners = [row for row in current.get("fallback_banners") or [] if isinstance(row, dict)]
    if not any(row.get("condition_key") == BLOCKER_BANNER for row in banners):
        return
    current["fallback_banners"] = [row for row in banners if row.get("condition_key") != BLOCKER_BANNER]
    if current.get("remaining_blocking_issues"):
        review.record_banner(current, BLOCKER_BANNER, count=len(current["remaining_blocking_issues"]))


def _lifted_blockers(work_dir: Path, state: dict, version: int, outcome: dict) -> set[str]:
    """D-213 (gate R1-1): the blocker rows whose evidence holds now, read from the files, never from a status.

    A row is lifted when the polish touched it, the re-check is usable for the current draft sha, its
    `resolutions` row says `resolved`, and no re-check blocker sits in its scope (its section, the
    descendants and the changed cross-reference sections). A row the reader sent to `manual_review` is
    never lifted. The export pin is checked where the settlement writes (`_to_export`).
    """
    rows = [
        row
        for row in _open_majors(state)
        if _is_blocker_row(row) and row.get("origin") == "loop" and row.get("status") != "manual_review"
    ]
    if not rows or not outcome["usable"]:
        return set()
    draft = str(state.get("current_draft_path") or f"{DRAFTS_DIR}/v{version}.md")
    texts = _polish_texts(work_dir, state, draft, version)
    if texts is None:
        return set()
    documents = _parse_pair(texts[0], texts[1], md_fallback.memo_language(state))
    changed = _changed_sections(*documents)
    return {
        str(row.get("id"))
        for row in rows
        if _holds(row, documents, changed)
        and outcome["resolutions"].get(str(row.get("id"))) == "resolved"
        and not _blocked(row, _row_scope(row, documents, changed), outcome["blockers"])
    }


def _settle_on_cancel(work_dir: Path, state: dict) -> None:
    """D-211: a run cancelled inside `client_readiness` settles its open majors before `finalize` renders.

    The same rows the second pass and the transition to `export` would settle — `polish` rows judged
    against the re-check, re-check blockers kept — so a cancelled run never delivers an approval the
    settlement would have withdrawn. Idempotent: an unchanged state is not written again.

    A published polish is scope-checked first, by the planner's own `_check_polish_scope`: an unchecked
    one is checked and stored, and a stored error takes the scope-error exit — baseline restored, export
    pinned, `polish_out_of_scope`, settlement — in that exit's one write. No agent is dispatched, so the
    re-check never runs on a cancel.
    """
    if str(state.get("current_phase")) != "client_readiness" or not _open_majors(state):
        return
    version = max(int(state.get("current_iteration") or 1), 1)
    draft = str(state.get("current_draft_path") or f"{DRAFTS_DIR}/v{version}.md")
    for _ in range(2):  # the first call may only store the check; the second acts on a stored error
        if _check_polish_scope(work_dir, state, draft, version) is None:
            break
        state = state_io.read_state(work_dir)
        if str(state.get("current_phase")) != "client_readiness":
            return  # the scope-error exit restored the baseline, pinned the export and settled the majors
    rows = _rows_after_polish(work_dir, state, draft, version) if _polish_issued(state) else None
    blockers = _polish_recheck(work_dir, state, version)["blockers"]

    def settle(current: dict) -> None:
        if rows is not None:
            current["open_substance_majors"] = copy.deepcopy(rows)
        # D-213 (gate R1-3, owner decision 1): a cancelled run keeps its blocker and its label.
        _settle_open_majors(current, blockers, version, lifted=set())

    probe = copy.deepcopy(state)
    settle(probe)
    if probe != state:
        state_io.write_state(work_dir, settle)


def _to_export(work_dir: Path, state: dict, version: int, *, mutate=None, banners=()) -> dict:
    """Every exit of `client_readiness` to `export`: the phase's own mutation, then the settlement (D-211).

    With no open majors the settlement is a no-op and the write is what it always was.

    D-213: the blocker rows whose evidence holds are read here, against the draft sha the re-check was
    read for; they are lifted only if that is still the current sha after `mutate` and `export_pin` is
    absent or pins it — so a scope error that put the baseline back lifts nothing.
    """
    outcome = _polish_recheck(work_dir, state, version) if _open_majors(state) else None
    blockers = outcome["blockers"] if outcome is not None else []
    lifted = _lifted_blockers(work_dir, state, version, outcome) if outcome is not None else set()
    sha = str(current_draft_sha(state) or "")

    def settled(current: dict) -> None:
        if mutate is not None:
            mutate(current)
        pin = current.get("export_pin")
        pinned = str(pin.get("sha256") or "") if isinstance(pin, dict) else sha
        held = lifted if str(current_draft_sha(current) or "") == sha and pinned == sha else set()
        _settle_open_majors(current, blockers, version, lifted=held)

    return transition(work_dir, state, "export", mutate=settled, banners=banners)


def _writer_failed_exit(work_dir: Path, state: dict, iteration: int) -> dict:
    """A43-1 / D-153: leave the loop on the last reviewed version when the writer could not change it.

    `revision next` published `drafts/v<N+1>.md` as a copy of v<N> and moved `current_*` to it; both
    writer attempts returned that copy untouched. The copy is not a version anyone wrote, so the run
    goes to `export` on v<N> — the version the reviewers actually judged — under manual review, with
    their open blockers recorded for the deliverable's Status section.
    """
    previous = max(int(iteration) - 1, 1)
    row = next(
        (r for r in (state.get("draft_versions") or []) if int(r.get("version") or 0) == previous),
        None,
    )
    record = review.iteration_record(state, previous)

    def mutate(current: dict) -> None:
        current["current_iteration"] = previous
        if row is not None:
            current["current_draft_path"] = str(row["path"])
            current["current_draft_sha"] = str(row["sha256"])
        if int(iteration) > previous:
            # D-158: the seed is not a version anyone wrote, so it must not stay eligible for the
            # export — `export_draft_sha` falls back to the *last* row when none is checked, and
            # would deliver the rejected copy under `manual_review_required_on_v<N+1>`. Its
            # `published[]` entry stays: that is history, not eligibility.
            current["draft_versions"] = [
                item
                for item in (current.get("draft_versions") or [])
                if int(item.get("version") or 0) != int(iteration)
            ]
        if record is not None:
            current["remaining_blocking_issues"] = revision._blockers(record)  # noqa: SLF001 - same rule as the loop exit
        # D-210: the export delivers v<N>, so the majors its reviewers left open travel with it.
        current["open_substance_majors"] = review.open_substance_majors(current, previous)
        _manual_review(current, "writer_failed", previous)

    return transition(work_dir, state, "export", mutate=mutate, banners=[("revision_writer_failed", {})])


def _manual_review(current: dict, reason: str, version: int) -> None:
    current["final_status"] = f"manual_review_required_on_v{version}"
    if reason not in current.setdefault("final_status_reasons", []):
        current["final_status_reasons"].append(reason)


def _after_client_write(work_dir: Path, state: dict, draft: str, version: int) -> dict:
    """§2.1 row 14 / D-57: lint + citations on the new bytes, one fix round, then the reviewer again.

    D-211: with open majors the polish output is first checked for scope, and a clean draft goes
    through the one citations re-check before the second readiness pass.
    """
    step = _check_polish_scope(work_dir, state, draft, version)
    if step is not None:
        return step
    step = _lint_steps(work_dir, state, draft, "client_readiness")
    if step is not None:
        return step
    lint_clean, citations_clean = _draft_checks(work_dir, state)
    if lint_clean and citations_clean:
        step = _dispatch_polish_recheck(work_dir, state, draft, version)
        if step is not None:
            return step
        return _dispatch_readiness(work_dir, state, draft, version)
    step = lint_fix_dispatch(
        work_dir, state, draft=draft, version=version, counter="polish", rounds=POLISH_FIX_ROUNDS
    )
    if step is not None:
        return step
    return _to_export(
        work_dir,
        state,
        version,
        mutate=lambda current: _manual_review(current, "unresolved_blockers", version),
        banners=[("client_polish_budget_consumed", {})],
    )


def plan_client_readiness(work_dir: Path, state: dict) -> dict:
    """§2.1 row 14: the readiness reviewer, the optional polish and its deterministic re-checks."""
    config = state.get("config") or {}
    version = max(int(state.get("current_iteration") or 1), 1)
    draft = str(state.get("current_draft_path") or f"{DRAFTS_DIR}/v{version}.md")
    rows = episode(state)
    last = rows[-1] if rows else None

    if last is None:
        return _dispatch_readiness(work_dir, state, draft, version)
    key = purpose(last)
    if key in ("dispatch:memo-writer", "script:draft.finish"):
        # §2.1 row 14: the polish (and the one fix round it may need) is re-checked on the new
        # bytes; only a draft that passes both checks goes back to the readiness reviewer.
        return _after_client_write(work_dir, state, draft, version)
    if key == "dispatch:citation-auditor":
        # D-211: the polish re-check, `ok` or `fail`, is read by the second readiness pass — never
        # by the generic branch below, which would degrade the run for a failed re-check.
        return _dispatch_readiness(work_dir, state, draft, version)

    if last.get("status") == "fail":
        return _readiness_degraded(work_dir, state, version)
    document = _client_verdict(work_dir, state)
    reviewed = str(current_draft_sha(state) or "")
    if reviewed and str(document.get("draft_sha") or "") != reviewed:
        # D-41 / §2.1 row 14: a verdict about another version of the draft never steers the
        # transition — the reviewer is re-issued once, then the run degrades to manual review.
        readiness_rows = [row for row in rows if purpose(row) == "dispatch:client-readiness-reviewer"]
        if len(readiness_rows) < 2:
            return _dispatch_readiness(work_dir, state, draft, version)
        return _readiness_degraded(work_dir, state, version)
    verdict = str(document.get("verdict") or "manual_review_required")
    used = int((state.get("attempts") or {}).get("client_polish") or 0)

    def store(current: dict) -> None:
        current["client_readiness"] = {
            "verdict": verdict,
            "draft_sha": document.get("draft_sha"),
            "version_reviewed": version,
            "at": events.utc_now(),
        }
        if used == 0:
            # D-211: the dispositions of pass 1 are read; the second pass is "as today".
            _apply_dispositions(current, document)

    if verdict == "client_ready":
        def ready(current: dict) -> None:
            store(current)
            if not current.get("final_status"):
                current["final_status"] = f"client_ready_on_v{version}"

        return _to_export(work_dir, state, version, mutate=ready)
    allowed = int(config.get("max_client_polish") or 0)
    if verdict == "needs_final_polish" and used < allowed:
        spec = writer_spec(
            work_dir,
            state,
            task="polish",
            version=version,
            canonical=draft,
            instructions=f"`{CLIENT_READINESS_PATH}` - the `issues[]` list, section by section",
            seed=True,
        )
        step_id = next_step_id(state)
        # D-211: the baseline of the scope check and of the re-check, published with the polish step.
        baseline = _publish_baseline(work_dir, state, draft, version, step_id) if _open_majors(state) else None

        def mutate(current: dict) -> None:
            store(current)
            current.setdefault("attempts", {})["client_polish"] = used + 1
            if baseline is not None:
                stepctx.merge_published(current, [baseline])

        return issue_dispatch(
            work_dir,
            state,
            [spec],
            chat=_chat(state, "final polish"),
            step_id=step_id,
            seeds={"writer": draft},
            mutate=mutate,
        )

    def manual(current: dict) -> None:
        store(current)
        _manual_review(current, "unresolved_blockers", version)

    return _to_export(
        work_dir,
        state,
        version,
        mutate=manual,
        banners=[("client_readiness_manual_review", {})],
    )


KNOWN_BLOCKERS_NONE = "none"
KNOWN_BLOCKERS_MAX = 12
KNOWN_BLOCKERS_CHARS = 200
"""D-119 caps on the «already known» list handed to the client-readiness reviewer."""

KNOWN_BLOCKERS_LEAD = (
    "{count} blocking issue(s) were already aggregated by the review loop on this same draft and are "
    "in the deliverable; do not re-report them. Report only classes of problem that are NEW next to "
    "this list (disclosure of the run's status, order and completeness of the recommendations, "
    "readability for the client)."
)


def known_blockers_text(state: dict, draft_sha: str) -> str:
    """The blockers the review loop already recorded for exactly this draft (D-119).

    After a forced exit `client_readiness` runs on an unchanged `draft_sha`, so two thirds of the
    reviewer's findings repeated blockers that were already written down. The list is handed over
    with the instruction to look for what is new; when the bytes have moved on since the aggregate
    (a polish round), the list no longer describes this draft and nothing is passed.

    D-213: a blocker an open-finding row links (`blocker_of`) is left out — the reviewer disposes of
    that row, so it must not also be told to ignore the finding.
    """
    linked = [review.blocker_link(row["blocker_of"]) for row in _open_majors(state) if _is_blocker_row(row)]
    rows = [
        row
        for row in (state.get("remaining_blocking_issues") or [])
        if isinstance(row, dict) and review.blocker_link(row) not in linked
    ]
    iterations = [row for row in (state.get("iterations") or []) if isinstance(row, dict)]
    if not rows or not draft_sha or not iterations:
        return KNOWN_BLOCKERS_NONE
    if str(iterations[-1].get("draft_sha") or "") != str(draft_sha):
        return KNOWN_BLOCKERS_NONE
    lines = [KNOWN_BLOCKERS_LEAD.format(count=len(rows))]
    for row in rows[:KNOWN_BLOCKERS_MAX]:
        section = str(row.get("section_id") or "document")
        category = str(row.get("category") or "").strip()
        text = " ".join(str(row.get("issue") or "").split())[:KNOWN_BLOCKERS_CHARS]
        parts = [part for part in (section, category, text) if part]
        lines.append("- " + " · ".join(parts))
    if len(rows) > KNOWN_BLOCKERS_MAX:
        lines.append(f"- … and {len(rows) - KNOWN_BLOCKERS_MAX} more in `state.remaining_blocking_issues`")
    return "\n".join(lines)


def _dispatch_readiness(work_dir: Path, state: dict, draft: str, version: int) -> dict:
    """The readiness reviewer; D-211: with the open majors, and after the polish with their statuses.

    Once the polish was issued, the rows are settled for the second pass in the dispatch's own state
    write: every `polish` row `resolved` or `unresolved`, the re-check's new majors appended.
    """
    config = state.get("config") or {}
    used = int((state.get("attempts") or {}).get("client_polish") or 0)
    draft_sha = str(current_draft_sha(state) or "")
    extra: dict = {}
    rows: list[dict] | None = None
    if _open_majors(state):
        if used > 0:
            rows = _rows_after_polish(work_dir, state, draft, version)
        extra["open_findings"] = open_findings_text(state if rows is None else dict(state, open_substance_majors=rows))

    def settle_rows(current: dict) -> None:
        current["open_substance_majors"] = rows

    spec = dispatch.spec(
        "client_readiness",
        "client-readiness-reviewer",
        "client readiness",
        [(CLIENT_READINESS_PATH, "client-readiness")],
        inputs=[draft],
        checklist="client-readiness",
        draft_path=draft,
        draft_version=version,
        draft_sha=draft_sha,
        polish_budget=str(max(int(config.get("max_client_polish") or 0) - used, 0)),
        known_blockers=known_blockers_text(state, draft_sha),
        retry_errors="none",
        **extra,
    )
    return issue_dispatch(
        work_dir,
        state,
        [spec],
        chat=_chat(state, "final delivery review"),
        mutate=settle_rows if rows is not None else None,
    )


def export_draft_sha(state: dict) -> tuple[str | None, int]:
    """Last version that passed lint and citations; `no_checked_draft` when there is none (§2.1 row 15).

    D-211: `export_pin` comes first, whatever the check flags say — an out-of-scope polish put its
    baseline back, and the exported bytes and the version the status names must be those.
    """
    pin = state.get("export_pin")
    if isinstance(pin, dict) and pin.get("sha256"):
        return str(pin["sha256"]), max(int(pin.get("version") or 1), 1)
    checked = [
        row
        for row in (state.get("draft_versions") or [])
        if row.get("lint_clean") and row.get("citations_clean")
    ]
    if checked:
        last = max(checked, key=lambda row: int(row.get("version") or 0))
        return str(last["sha256"]), int(last["version"])
    versions = state.get("draft_versions") or []
    if versions:
        last = max(versions, key=lambda row: int(row.get("version") or 0))
        return str(last.get("sha256") or ""), int(last.get("version") or 1)
    return state.get("current_draft_sha"), max(int(state.get("current_iteration") or 1), 1)


def export_inputs(work_dir: Path, state: dict, sha: str | None) -> tuple:
    """The file `docx render` depends on — its `inputs_sha` (§2.2, §5.5).

    Render reads the selected draft and validates its own output in the same step (D-117), so the
    draft is the whole input. It is recorded when the step is issued, so an unchanged export is
    never re-issued as a `rerun` and the phase walks on to `finalize` (M9).

    D-211: when `sha` is the pinned one, the pinned version's row wins over an older one with the same bytes.
    """
    rows = [
        row
        for row in (state.get("draft_versions") or [])
        if isinstance(row, dict) and row.get("path") and str(row.get("sha256")) == str(sha)
    ]
    pin = state.get("export_pin")
    if isinstance(pin, dict) and str(pin.get("sha256")) == str(sha):
        rows = [row for row in rows if int(row.get("version") or 0) == int(pin.get("version") or 0)] + rows
    return (str(rows[0]["path"]) if rows else str(state.get("current_draft_path") or ""),)


def plan_export(work_dir: Path, state: dict) -> dict:
    """§2.1 row 15: render (validating its own output, D-117) then finalize — export never blocks (M9)."""
    sha, version = export_draft_sha(state)
    render_inputs = export_inputs(work_dir, state, sha)
    if script_done(state, "docx.render") is None:
        checked = any(
            row.get("lint_clean") and row.get("citations_clean")
            for row in (state.get("draft_versions") or [])
        )
        mutate = None
        if not checked:
            def mutate(current: dict) -> None:  # noqa: F811 - narrow, deliberate shadow
                _manual_review(current, "no_checked_draft", version)
                review.record_banner(current, "no_checked_draft")

        step = script_step(
            work_dir,
            state,
            "docx",
            "render",
            chat=_chat(state, f"rendering the deliverable from v{version}"),
            extra=("--draft-sha", str(sha or "")),
            mutate=mutate,
            inputs=render_inputs,
        )
        if step is not None:
            return step
    # D-117: `docx render` validated what it wrote, so there is no second step to issue here.
    step = finalize_step(work_dir, state, None)
    if step is not None:
        return step
    return {"transition": str(state.get("current_phase"))}


PLANNERS = {
    "intake_preliminary_research": plan_intake_preliminary_research,
    "intake_questions_pending": plan_intake_questions_pending,
    "planning": plan_planning,
    "plan_approval_pending": plan_plan_approval_pending,
    "research": plan_research,
    "research_sufficiency": plan_research_sufficiency,
    "research_sufficiency_followup_pending": plan_research_sufficiency_followup_pending,
    "research_insufficient_pending": plan_research_insufficient_pending,
    "currency_check": plan_currency_check,
    "source_pack": plan_source_pack,
    "source_review_pending": plan_source_review_pending,
    "drafting": plan_drafting,
    "revision_loop": plan_revision_loop,
    "client_readiness": plan_client_readiness,
    "export": plan_export,
}


# --- terminal --------------------------------------------------------------


def terminal_response(work_dir: Path, state: dict) -> dict:
    """The `terminal` form of §3.1: printed once the run has a deliverable and a summary (M9)."""
    phase = str(state.get("current_phase"))
    existing = None
    for row in state.get("steps") or []:
        if isinstance(row, dict) and row.get("kind") == KIND_TERMINAL:
            existing = row
    if existing is None:
        step_id = next_step_id(state)
        row = {
            "step_id": step_id,
            "kind": KIND_TERMINAL,
            "phase": phase,
            "issued_at": events.utc_now(),
            "attempt": 1,
            "reason": REASON_INITIAL,
            "status": "ok",
            "closed_at": events.utc_now(),
        }

        def mutator(current: dict) -> None:
            current.setdefault("steps", []).append(row)
            current["progress"] = build_progress(work_dir, current)

        state_io.write_state(work_dir, mutator)
        events.append_event(
            work_dir, "step_issued", "cli", {"step_id": step_id, "kind": KIND_TERMINAL}, phase=phase
        )
    else:
        step_id = str(existing["step_id"])

    deliverable = state.get("final_docx_path") or ""
    lines = [f"memoforge: task {state.get('task_id')} finished in phase `{phase}`."]
    if deliverable:
        lines.append(f"Deliverable: {Path(work_dir) / deliverable}")
    for name in ("deliverable.docx", "deliverable.md"):
        candidate = Path(work_dir) / name
        if not deliverable and candidate.is_file():
            lines.append(f"Deliverable: {candidate}")
            break
    summary = Path(work_dir) / "summary.md"
    memo = published_memo(state)
    if memo:
        lines.append(f"Memo: {memo}")
    if summary.is_file():
        lines.append(f"Summary: {summary}")
    published = published_to(state)
    if published:
        lines.append(f"Published: {published}")
        count = published_file_count(work_dir, state)
        if count is not None:
            lines.append(f"Files: {count}")
    if state.get("final_status"):
        lines.append(f"Status: {state['final_status']}")
    # Final review of plan 70: through `banner_text_for`, like docx, md and `summary.md`, so a stored
    # D-216 rewording ("listed in the appendix") prints as the Status section wording. The terminal
    # text is English by design (the router translates it), so the language is the default.
    for banner in state.get("fallback_banners") or []:
        if isinstance(banner, dict):
            text = fallbacks.banner_text_for(banner, i18n.DEFAULT) if banner.get("text") else ""
        else:
            text = str(banner)
        if text:
            lines.append(f"- {text}")
    text = "\n".join(lines)
    return {
        "step_id": step_id,
        "kind": KIND_TERMINAL,
        "phase": phase,
        "text": text,
        "chat_line": text.splitlines()[0],
        "final_status": state.get("final_status"),
    }


# --- draft bookkeeping -----------------------------------------------------


def sync_draft_versions(work_dir: Path) -> None:
    """Re-align the draft bookkeeping with `published[]`, then apply the two deterministic reports.

    D-60/D-41: both reports are read against `published[]`. D-68: a report that drifted after
    publication — or one that was never published — is no report at all, so its mark is **revoked**
    on every version instead of leaving an earlier `true` standing on a version nothing vouches for;
    the check is re-issued (D-54). A readable report is a verdict for exactly one version: the one
    whose `sha256` is its `draft_sha`; the other versions keep the marks their own reports gave them.
    """
    _sync_current_draft(work_dir)
    state = state_io.read_state(work_dir)
    verdicts: dict[str, dict[str, bool]] = {}
    revoked: list[str] = []
    for name, field in (("lint.json", "lint_clean"), ("citations.json", "citations_clean")):
        try:
            document = stepctx.read_published(work_dir, name, state=state)
        except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
            document = None
        sha = document.get("draft_sha") if isinstance(document, dict) else None
        if not isinstance(sha, str) or not sha:
            revoked.append(field)
            continue
        verdicts.setdefault(sha, {})[field] = bool(document.get("clean"))

    def patch_for(row: dict) -> dict:
        patch: dict[str, bool] = {field: False for field in revoked}
        patch.update(verdicts.get(str(row.get("sha256") or "")) or {})
        return {field: value for field, value in patch.items() if bool(row.get(field)) != value}

    if not any(patch_for(row) for row in state.get("draft_versions") or []):
        return

    def mutator(current: dict) -> None:
        for row in current.get("draft_versions") or []:
            patch = patch_for(row)
            if not patch:
                continue
            row.update(patch)
            # A version nothing vouches for any more carries no check timestamp either (§2.2).
            marked = row.get("lint_clean") or row.get("citations_clean")
            row["checked_at"] = events.utc_now() if marked else None

    state_io.write_state(work_dir, mutator)


def _sync_current_draft(work_dir: Path) -> None:
    """`draft anchor` republishes the draft: `current_draft_sha` and `draft_versions[]` follow it (§3.1)."""
    state = state_io.read_state(work_dir)
    path = str(state.get("current_draft_path") or "")
    if not path:
        return
    sha = stepctx.published_sha(state, path)
    if not sha or sha == state.get("current_draft_sha"):
        return

    def mutator(current: dict) -> None:
        current["current_draft_sha"] = sha
        for row in current.get("draft_versions") or []:
            if row.get("path") == path:
                row["sha256"] = sha
                row["lint_clean"] = False
                row["citations_clean"] = False
                row["checked_at"] = None

    state_io.write_state(work_dir, mutator)


def _record_draft_publication(current: dict, published: list[dict]) -> None:
    """A published `drafts/v<N>.md` becomes the current draft and a `draft_versions[]` row (§2.2)."""
    for entry in published:
        path = str(entry.get("canonical_path") or "")
        if not path.startswith(f"{DRAFTS_DIR}/v") or not path.endswith(".md"):
            continue
        try:
            version = int(path[len(DRAFTS_DIR) + 2 : -3])
        except ValueError:
            continue
        current["current_draft_path"] = path
        current["current_draft_sha"] = entry["sha256"]
        rows = [row for row in (current.get("draft_versions") or []) if row.get("version") != version]
        rows.append(
            {
                "version": version,
                "path": path,
                "sha256": entry["sha256"],
                "lint_clean": False,
                "citations_clean": False,
                "checked_at": None,
            }
        )
        current["draft_versions"] = sorted(rows, key=lambda row: int(row["version"]))
        if int(current.get("current_iteration") or 0) < version:
            current["current_iteration"] = version


# --- mf next ---------------------------------------------------------------


def run_next(args: argparse.Namespace) -> dict:
    """`mf next --workdir W` — the only thing the orchestrator has to know (§3.1).

    `cli_call` is written by `cli.main` for every invocation, this one included (D-43). The
    `dashboard` block of §7.5 is attached to whatever step the machine chose — including the
    `terminal` one, so the page ends on the deliverable — and only when `config.dashboard` is on.
    """
    work_dir = Path(args.workdir)
    return with_dashboard(work_dir, _next_action(work_dir))


def _next_action(work_dir: Path) -> dict:
    """The step `next` returns: replay, resume, then plan forward (§3.1)."""
    state = state_io.read_state(work_dir)

    # (1) close or replay every unclosed script step before anything else (§2.2).
    for row in open_steps(state):
        if row.get("kind") == KIND_SCRIPT:
            replay_script(work_dir, state, row)
            state = state_io.read_state(work_dir)
    sync_draft_versions(work_dir)
    state = state_io.read_state(work_dir)
    # D-122: a provider that reported its daily quota gone is struck off the routing for the day.
    record_mcp_exhaustion(work_dir, state)
    state = state_io.read_state(work_dir)

    # (2) resume the remaining open step: autoclose, close or re-issue (§3.1 rule 3).
    for row in open_steps(state):
        kind = row.get("kind")
        if state.get("cancel_requested"):
            # §2.4 (d): no new dispatch, script or gate step is issued once cancel was requested;
            # a late `report` of an already issued step is still accepted by `mf report`.
            close_step(work_dir, row, {"cancelled": True}, status="skipped")
            state = state_io.read_state(work_dir)
            continue
        if kind == KIND_DISPATCH:
            response = resume_dispatch(work_dir, state, row)
            state = state_io.read_state(work_dir)
            if response is not None:
                return response
        elif kind == KIND_INLINE:
            response = resume_inline(work_dir, state, row)
            state = state_io.read_state(work_dir)
            if response is not None:
                return response
        elif kind in (KIND_GATE_TEXT, KIND_GATE_AUQ):
            return resume_gate(work_dir, state, row)

    # (3) plan forward.
    for _ in range(24):
        state = state_io.read_state(work_dir)
        if phases.is_terminal(str(state.get("current_phase"))):
            return terminal_response(work_dir, state)
        if state.get("cancel_requested"):
            # §2.4 (d): no new dispatch or script steps except the always-deliver finalize. D-211: a
            # cancelled readiness step settles its open majors first, as its exit to `export` would.
            _settle_on_cancel(work_dir, state)
            state = state_io.read_state(work_dir)
            step = finalize_step(work_dir, state, None)
            if step is not None:
                return step
            state = state_io.read_state(work_dir)
            return terminal_response(work_dir, state)
        planner = PLANNERS.get(str(state.get("current_phase")))
        if planner is None:
            return {"errors": [f"unknown_phase: {state.get('current_phase')}"]}
        outcome = planner(work_dir, state)
        if "transition" not in outcome:
            return outcome
    return {"errors": ["planning_did_not_converge"], "phase": state.get("current_phase")}


# --- mf report -------------------------------------------------------------


def _stamp(value: object):
    """One journal/step timestamp as a `datetime`, or None when it is missing or malformed."""
    try:
        return events.datetime.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, AttributeError, TypeError):
        return None


def _seconds(start, end) -> float | None:
    return None if start is None or end is None else round((end - start).total_seconds(), 3)


def _duration(row: dict) -> float | None:
    return _seconds(_stamp(row.get("issued_at")), _stamp(events.utc_now()))


def _slot_timings(work_dir: Path, row: dict, slot: str) -> dict:
    """D-120: the slot's own `agent_log start → done` window, plus the queue and barrier around it.

    `duration_seconds` used to run from `step_issued` to `report`, so it carried the dispatch lag and
    the wait on the slowest slot of the barrier — three slots of one step reported 1 338 s each for
    7 to 15 minutes of work. The three fields now decompose that same span: `queue_s` (issue → this
    slot's first `start`), `duration_seconds` (`start` → `done`) and `barrier_s` (`done` → now, i.e.
    the report). Without the events the old value stands and the two new fields are `null`.
    """
    issued = _stamp(row.get("issued_at"))
    step_id = str(row.get("step_id") or "")
    attempt = int(row.get("attempt") or 1)
    started = finished = None
    for record in events.read_events(work_dir):
        if record.get("event") != "agent_log":
            continue
        data = record.get("data") or {}
        if str(data.get("step_id") or "") != step_id or int(data.get("attempt") or 1) != attempt:
            continue
        if str(data.get("slot") or "") != slot:
            continue
        stamp = _stamp(record.get("ts"))
        if stamp is None:
            continue
        if data.get("state") == "start" and started is None:
            started = stamp
        elif data.get("state") == "done":
            finished = stamp
    now = _stamp(events.utc_now())
    measured = _seconds(started, finished)
    return {
        "duration_seconds": _duration(row) if measured is None else measured,
        "queue_s": _seconds(issued, started),
        "barrier_s": _seconds(finished, now),
    }


def _copy_stdout(work_dir: Path, row: dict, slot: str, source: str | None) -> str | None:
    if not source:
        return None
    origin = Path(source)
    if not origin.is_absolute():
        origin = work_dir / source
    if not origin.is_file():
        return None
    target = stepctx.ensure_step_dir(work_dir, str(row["step_id"]), int(row["attempt"]), slot) / "stdout.txt"
    shutil.copyfile(origin, target)
    return stepctx.rel_path(work_dir, target)


def _report_dispatch(work_dir: Path, state: dict, row: dict, args: argparse.Namespace) -> dict:
    agents = [dict(agent) for agent in (row.get("agents") or [])]
    slots = [args.agent] if args.agent else [str(agent["slot"]) for agent in agents]
    unknown = [slot for slot in slots if slot not in {str(a["slot"]) for a in agents}]
    if unknown:
        return {"accepted": False, "errors": [f"unknown_slot: {', '.join(unknown)}"]}

    already = [slot for slot in slots if _slot(agents, slot).get("status") not in (None, "")]
    if already and len(already) == len(slots):
        return {"accepted": True, "already_reported": True, "step_id": row["step_id"], "slots": already}

    published: list[dict] = []
    errors: dict[str, list[str]] = {}
    for slot in slots:
        agent = _slot(agents, slot)
        if agent.get("status") not in (None, ""):
            continue
        agent["payload_ref"] = _copy_stdout(work_dir, row, slot, args.stdout)
        if args.status == "fail":
            agent["status"] = "fail"
            agent["reported_at"] = events.utc_now()
            errors[slot] = ["reported_fail"]
            continue
        completion = check_completion(work_dir, state, row, agent)
        if not completion["ok"]:
            agent["status"] = "fail"
            agent["reported_at"] = events.utc_now()
            agent["payload_ref"] = "; ".join(completion["errors"])[:400]
            errors[slot] = completion["errors"]
            continue
        status, entries = accept_slot(work_dir, row, completion)
        published.extend(entries)
        if status == "fail":
            errors[slot] = ["writer_output_identical_to_seed"]
            agent["payload_ref"] = "writer_output_identical_to_seed"
        agent["outputs"] = completion["outputs"]
        agent["status"] = status
        agent["reported_at"] = events.utc_now()

    for slot in slots:
        events.append_event(
            work_dir,
            "agent_returned",
            "cli",
            {
                "step_id": row["step_id"],
                "attempt": row["attempt"],
                "slot": slot,
                "status": _slot(agents, slot).get("status"),
                **_slot_timings(work_dir, row, slot),
            },
            phase=row.get("phase"),
            step_id=str(row["step_id"]),
        )

    pending = [a for a in agents if a.get("status") in (None, "", "fail")]
    if not pending:
        status = "no_change" if all(a.get("status") == "no_change" for a in agents) else "ok"
        close_step(
            work_dir,
            row,
            {"slots": {a["slot"]: a["status"] for a in agents}},
            status=status,
            published=published,
            agents=agents,
        )
        return {
            "accepted": True,
            "step_id": row["step_id"],
            "attempt": row["attempt"],
            "closed": True,
            "next_hint": ["mf", "next", "--workdir", str(work_dir)],
        }

    def mutate(current: dict) -> None:
        record_publications(current, published, merge=True)
        for item in current.get("steps") or []:
            if item.get("step_id") == row["step_id"] and int(item.get("attempt") or 1) == int(row["attempt"]):
                item["agents"] = agents
        current["progress"] = build_progress(work_dir, current)

    state_io.write_state(work_dir, mutate)
    if errors:
        return {
            "accepted": False,
            "errors": sorted({message for messages in errors.values() for message in messages}),
            "retry": {
                "step_id": row["step_id"],
                "attempt": int(row["attempt"]) + 1,
                "agents": sorted(errors),
            },
        }
    return {
        "accepted": True,
        "step_id": row["step_id"],
        "attempt": row["attempt"],
        "closed": False,
        "pending": [a["slot"] for a in pending],
        "next_hint": ["mf", "next", "--workdir", str(work_dir)],
    }


def _slot(agents: list[dict], slot: str) -> dict:
    for agent in agents:
        if str(agent.get("slot")) == str(slot):
            return agent
    return {}


def _report_inline(work_dir: Path, state: dict, row: dict, args: argparse.Namespace) -> dict:
    outputs = list(row.get("expected_outputs") or [])
    errors: list[str] = []
    ready: list[dict] = []
    if args.status == "fail":
        errors.append("reported_fail")
    for entry in outputs:
        target = work_dir / str(entry["work_path"])
        if not target.is_file():
            errors.append(f"missing_output: {entry['work_path']}")
            continue
        try:
            document = state_io.read_json(target)
        except (OSError, ValueError) as exc:
            errors.append(f"invalid_json: {exc}")
            continue
        if entry.get("schema"):
            errors.extend(schema.validate(document, entry["schema"]))
        ready.append(entry)
    if errors:
        # §2.2, §3.1: the rejected attempt is a finished execution identity. Closing it as `fail`
        # spends `inline_llm_retry` exactly once (a repeat of the same `report` is the no-op of a
        # closed step), keeps the errors for the next instruction and makes `next` hand out
        # `attempt+1`; the file this attempt left behind can never autoclose the next one.
        budget = budget_for(state, row)
        close_step(
            work_dir,
            row,
            {"errors": errors},
            status="fail",
            mutate=lambda current: spend_budget(current, budget),
        )
        return {
            "accepted": False,
            "errors": errors,
            "retry": {
                "step_id": row["step_id"],
                "attempt": int(row["attempt"]) + 1,
                "agents": [],
            },
        }
    published = publish_outputs(work_dir, str(row["step_id"]), ready)
    close_step(
        work_dir,
        row,
        {"published": [entry["canonical_path"] for entry in published]},
        published=published,
    )
    return {
        "accepted": True,
        "step_id": row["step_id"],
        "closed": True,
        "next_hint": ["mf", "next", "--workdir", str(work_dir)],
    }


def _report_gate(work_dir: Path, state: dict, row: dict, args: argparse.Namespace) -> dict:
    generation = int(row.get("generation") or 0)
    if args.status == "no_answer":
        # §2.4 text-fallback: the same gate, one generation further, over the text channel.
        def mutate(current: dict) -> None:
            for item in current.get("steps") or []:
                if item.get("step_id") == row["step_id"] and int(item.get("attempt") or 1) == int(row["attempt"]):
                    item["generation"] = generation + 1
                    item["kind"] = KIND_GATE_TEXT

        state_io.write_state(work_dir, mutate)
        events.append_event(
            work_dir,
            "gate_channel_switched",
            "cli",
            {"step_id": row["step_id"], "from": generation, "to": generation + 1},
            phase=row.get("phase"),
            step_id=str(row["step_id"]),
        )
        return {
            "accepted": True,
            "step_id": row["step_id"],
            "generation": generation + 1,
            "channel": "gate-text",
            "next_hint": ["mf", "next", "--workdir", str(work_dir)],
        }
    if not args.answers:
        return {"accepted": False, "errors": ["answers_required"]}
    import json

    try:
        answers = json.loads(args.answers)
    except ValueError as exc:
        return {"accepted": False, "errors": [f"invalid_answers_json: {exc}"]}
    if not isinstance(answers, dict):
        return {"accepted": False, "errors": ["invalid_answers_json: object expected"]}
    # D-176a: the AUQ answered in the interface language of the task comes back canonical.
    parsed = gates.parse_auq(answers, i18n.ui_language(state))
    if not parsed["recognized"]:
        return {"accepted": False, "errors": parsed["errors"]}
    result = gates.commit(
        work_dir,
        state,
        "plan",
        parsed,
        generation=generation,
        raw=str(args.answers),
        step_id=str(row["step_id"]),
        attempt=int(row["attempt"]),
        phase=str(row.get("phase")),
        kind=KIND_GATE_AUQ,
    )
    return {
        "accepted": True,
        "step_id": row["step_id"],
        "closed": True,
        "gate": result,
        "next_hint": ["mf", "next", "--workdir", str(work_dir)],
    }


def run_report(args: argparse.Namespace) -> dict:
    """`mf report` for dispatch, inline-llm and the AUQ gate; checks run in the order of §3.1."""
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)

    row = step_row(state, args.step)
    if row is None:
        return {"accepted": False, "errors": [f"unknown_step: {args.step}"]}
    # (0) identity, then the gate generation — both before the «already closed» check (§3.1).
    if int(args.attempt) != int(row.get("attempt") or 1):
        return {
            "accepted": False,
            "errors": ["identity_mismatch"],
            "current_attempt": int(row.get("attempt") or 1),
        }
    kind = str(row.get("kind"))
    if kind in (KIND_GATE_AUQ, KIND_GATE_TEXT):
        current_generation = int(row.get("generation") or 0)
        if args.generation is None and args.answers:
            # §2.4 / D-34: an answer to an issued gate must name the channel it answers, otherwise
            # a reply from the old channel would slip past the `stale_generation` guard below.
            return {
                "accepted": False,
                "errors": ["generation_required"],
                "expected_generation": current_generation,
            }
        if args.generation is not None and int(args.generation) != current_generation:
            return {
                "accepted": False,
                "errors": ["stale_generation"],
                "expected_generation": current_generation,
            }
    # (1) already closed -> no-op.
    if row.get("status") not in (None, ""):
        return {
            "accepted": True,
            "already_reported": True,
            "step_id": row["step_id"],
            "attempt": row["attempt"],
        }
    # (2) content checks.
    if kind == KIND_DISPATCH:
        return _report_dispatch(work_dir, state, row, args)
    if kind == KIND_INLINE:
        return _report_inline(work_dir, state, row, args)
    if kind in (KIND_GATE_AUQ, KIND_GATE_TEXT):
        return _report_gate(work_dir, state, row, args)
    return {"accepted": False, "errors": [f"report_not_supported_for_kind: {kind}"]}


# --- mf agent log ----------------------------------------------------------


def workspace_outputs(work_dir: Path, step_id: str, attempt: int, slot: str) -> dict:
    """sha of every file the agent produced in its attempt workspace (`agent log --state done`)."""
    directory = stepctx.step_dir(work_dir, step_id, attempt, slot)
    result: dict[str, str] = {}
    if not directory.is_dir():
        return result
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("done.json", "stdout.txt") or "inputs" in path.relative_to(directory).parts:
            continue
        result[stepctx.rel_path(work_dir, path)] = state_io.sha256_file(path)
    return result


def run_agent_log(args: argparse.Namespace) -> dict:
    """`mf agent log --state start|step|done` — telemetry, and the completion marker on `done` (§3.1)."""
    work_dir = Path(args.workdir)
    state = state_io.read_state_or_none(work_dir) or {}
    payload = {
        "step_id": args.step,
        "attempt": int(args.attempt),
        "slot": args.slot,
        "state": args.state,
        "detail": args.detail,
    }
    if args.mcp:
        events.append_event(
            work_dir,
            "mcp_call",
            args.slot,
            {"server": args.mcp, "tool": args.detail or "unknown"},
            phase=state.get("current_phase"),
            step_id=args.step,
        )
    events.append_event(
        work_dir, "agent_log", args.slot, payload, phase=state.get("current_phase"), step_id=args.step
    )
    if args.state != "done":
        return {"logged": True, **payload}

    row = step_row(state, args.step, int(args.attempt)) or {}
    declared = dict(row.get("inputs") or {})
    input_sha = {}
    for relative in declared:
        path = work_dir / relative
        if path.is_file():
            input_sha[relative] = state_io.sha256_file(path)
    marker = {
        "task_id": str(state.get("task_id") or ""),
        "step_id": str(args.step),
        "attempt": int(args.attempt),
        "slot": str(args.slot),
        "input_sha": input_sha,
        "output_sha": workspace_outputs(work_dir, str(args.step), int(args.attempt), str(args.slot)),
        "at": events.utc_now(),
    }
    errors = schema.validate(marker, "done-marker")
    if errors:
        return {"errors": errors}
    target = marker_path(work_dir, str(args.step), int(args.attempt), str(args.slot))
    target.parent.mkdir(parents=True, exist_ok=True)
    state_io.write_json_atomic(target, marker)
    return {"logged": True, "marker": stepctx.rel_path(work_dir, target), "outputs": len(marker["output_sha"])}


# --- registration ----------------------------------------------------------


def register(subparsers) -> None:
    """Register `mf next`, `mf report` and `mf agent log` (§5.2)."""
    from . import cli

    nxt = subparsers.add_parser("next", help="what to do next (the only planner, §3.1)")
    nxt.add_argument("--workdir", required=True)
    nxt.set_defaults(func=run_next)

    report = subparsers.add_parser("report", help="report the result of a dispatch, inline-llm or AUQ gate")
    report.add_argument("--workdir", required=True)
    report.add_argument("--step", required=True)
    report.add_argument("--attempt", type=int, required=True)
    report.add_argument("--agent", default=None, help="slot; omitted means every slot of the step")
    report.add_argument("--status", default="ok", choices=["ok", "fail", "no_answer"])
    report.add_argument("--answers", default=None, help="JSON object of AskUserQuestion answers")
    report.add_argument("--generation", type=int, default=None)
    report.add_argument("--stdout", default=None, help="file with the agent's final message")
    report.set_defaults(func=run_report)

    group = cli.group_subparsers(subparsers, "agent", "agent-side telemetry and completion marker")
    log = group.add_parser("log", help="log one agent step; `--state done` writes the completion marker")
    log.add_argument("--workdir", required=True)
    log.add_argument("--step", required=True)
    log.add_argument("--attempt", type=int, default=1)
    log.add_argument("--slot", required=True)
    log.add_argument("--state", required=True, choices=["start", "step", "done"])
    log.add_argument("--detail", default=None)
    log.add_argument("--mcp", default=None, help="server name of an MCP call to count")
    log.set_defaults(func=run_agent_log)
