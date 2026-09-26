"""`mf brief next|report` — the driver of `/memoforge:brief`, a decision brief of a finished memo (plan 75A).

D-224. The brief keeps its own state in `<work_dir>/brief/state.json` (schema `brief-state`) and never
writes the task's `state.json`, its memo, `summary.md` or `deliverable.*`. Every read-mutate-write of
the brief state happens under `brief.lock`, the first lock of `state_io.LOCK_ORDER`; a whole `next` or
`report` is one critical section, and the events it produces are appended after the lock is released.

A run: preflight (read only) → archive of the previous finished run → `status_gate` (only for an
unclean memo) → `write` → `lint` → [`lint_fix` → `lint`] → `review` → `review_merge` → [`revise` |
`shorten` → `lint` → …] → `render` → `publish`. D-225: `review_merge` is `brief_review.after_reviews`
(validation, fail-closed merge, revise/shorten, the verdict); `render` builds `brief/brief.docx` (or the
markdown twin, or keeps the last version) and `publish` copies it next to the published memo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from . import (
    brief_lint,
    brief_review,
    dispatch,
    docx,
    events,
    finalize,
    i18n,
    limits,
    machine,
    schema,
    state_io,
    stepctx,
)
from .docx import fallback, validate

BRIEF_DIR = "brief"
RUN_FILE = "state.json"
RUN_SCHEMA = "brief-state"
ARCHIVE_ASIDE = "brief.archiving"
"""The finished run's folder while it is moved into `brief/previous/` (beside `brief/`, never inside)."""
PREVIOUS_DIR = "previous"
OPEN_ISSUES_PATH = "brief/open-issues.json"
PHASE = "done"
"""The task phase every brief event is journalled under (the real phase of the task)."""

PACK_FILES: tuple[str, ...] = ("research/source-pack.json", "research/sources.json", "research/quotes.json")
"""The frozen pack the brief's citations resolve against; each one `published[]` knows must be intact."""

WRITER_STEPS: tuple[str, ...] = ("write", "lint_fix", "revise", "shorten")
DISPATCH_STEPS: tuple[str, ...] = WRITER_STEPS + ("review",)
WRITER_ATTEMPTS = 1 + limits.MAX_SINGLE_DISPATCH_RETRY
REVIEW_ATTEMPTS = 1 + limits.MAX_REVIEWER_JSON_RETRY
BLOCKING_SEVERITIES: tuple[str, ...] = ("blocker", "major")

SLOT_SCHEMAS: dict[str, str | None] = {"writer": None, "fidelity": "brief-review", "form": "review"}
"""The schema the output of each slot is checked against before it is promoted."""

RUN_PATCH_FIELDS: frozenset = frozenset(
    {"round", "instructions_path", "carried_form_findings", "shorten_done", "too_long", "verdict", "outcome_reasons"}
)
"""What `brief_review.after_reviews` may change in the run (Task 6 adds no field)."""

BRIEF_ROLE = (
    " This draft is a decision brief for a non-lawyer: grade it only against the checklist given, and "
    "every issue names the checklist_id of the item it fails."
)
"""`${brief_role}` of the form reviewer; it ends an existing prompt line, hence the leading space (D-223)."""

FULL_REVIEW = ("none", "all")
"""`(previous_review_path, changed_blocks)` of a fidelity review that traces the whole brief (D-228)."""

SECTION_IDS_LEAD = " Section ids for this draft (use exactly these as section_id): "
SECTION_IDS_TAIL = "; use document only for the whole brief."

CITATION_STYLE = "inline"
"""The brief always cites inline, whatever the memo's style (the template's front matter, D-222)."""
DOCX_DELIVERABLE = f"{BRIEF_DIR}/brief.docx"
MD_DELIVERABLE = f"{BRIEF_DIR}/brief.md"
DELIVERABLE_SUFFIXES: tuple[str, ...] = (".docx", ".md")
BINDING_LINE = re.compile(r"[ \t]*<!--\s*(?:from\s|omitted\b).*?-->[ \t]*")
"""A binding comment line of a conclusion block or the header's omitted-leaves comment, without its line end;
the reader never sees either (D-225, D-229)."""

MAX_CODE_STEPS = 64
"""Guard of the step loop inside one `next`; a real run crosses a handful of code steps."""


class BriefChangedOnDisk(Exception):
    """A promoted brief file no longer hashes to the sha recorded when it was promoted."""


# --- paths and the run file -------------------------------------------------


def brief_dir(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/brief` — everything a brief run writes outside `steps/`."""
    return Path(work_dir) / BRIEF_DIR


def run_path(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/brief/state.json`."""
    return brief_dir(work_dir) / RUN_FILE


def read_run(work_dir: str | os.PathLike) -> dict | None:
    """The brief state, or None when no run was ever started (or the last start was cut short)."""
    try:
        run = state_io.read_json(run_path(work_dir))
    except FileNotFoundError:
        return None
    if not isinstance(run, dict):
        raise ValueError("brief_state_not_an_object")
    return run


def _save(work_dir: Path, run: dict) -> None:
    """Validate and replace `brief/state.json`; the caller holds `brief.lock`."""
    schema.validate_or_raise(run, RUN_SCHEMA)
    state_io.write_json_atomic(run_path(work_dir), run)


def _lock(work_dir: Path) -> state_io.FileLock:
    return state_io.FileLock(state_io.lock_path(work_dir, "brief"))


def write_run(work_dir: str | os.PathLike, mutator) -> dict:
    """read → mutate → validate → replace of `brief/state.json` under `brief.lock`."""
    work_dir = Path(work_dir)
    with _lock(work_dir):
        run = read_run(work_dir)
        if run is None:
            raise FileNotFoundError(f"no_brief_run: {run_path(work_dir)}")
        result = mutator(run)
        if isinstance(result, dict):
            run = result
        _save(work_dir, run)
    return run


def new_run_id() -> str:
    """uuid4 hex; its first eight characters keep the step ids of two runs apart."""
    return uuid.uuid4().hex


def step_id(run_id: str, n: int) -> str:
    """`b-<run8>-NNN`."""
    return f"b-{run_id[:8]}-{int(n):03d}"


# --- the task: status and preflight (read only) -----------------------------


def _text_of(row: dict) -> str:
    """The open point for the brief writer: the client sentence, the lawyer's question, else the issue (D-252)."""
    manual = row.get("status") == "manual_review" or row.get("disposition") == "manual_review"
    if not row.get("issue_client") and manual and row.get("disposition_note"):
        return str(row["disposition_note"])
    return str(row.get("issue_client") or row.get("issue") or "")


def classify_status(state: dict) -> dict:
    """Is the delivered memo clean, and which open points does the brief have to mark as not confirmed?

    Clean = an approved status family, no remaining blocking issue and no `open_substance_majors`
    row still open. The open points are the blockers (`ob-N`) and the majors `summary.md` lists (their
    own `om-N` ids) — a row the readiness step moved among the blockers is listed once (D-237).
    """
    final_status = state.get("final_status")
    blocking = list(state.get("remaining_blocking_issues") or [])
    majors = [row for row in (state.get("open_substance_majors") or []) if isinstance(row, dict)]
    clean = (
        fallback.is_approved(final_status)
        and not blocking
        and not any(row.get("status") in finalize.OPEN_FINDING_STATUSES for row in majors)
    )
    open_issues: list[dict] = []
    for index, row in enumerate(blocking):
        row = row if isinstance(row, dict) else {"issue": str(row)}
        open_issues.append(
            {
                "id": f"ob-{index + 1}",
                "section_id": str(row.get("section_id") or brief_lint.DOCUMENT_ID),
                "class": str(row.get("source_reviewer") or "blocker"),
                "severity": str(row.get("severity") or "blocker"),
                "text": _text_of(row),
            }
        )
    for row in finalize.open_findings(majors, blocking):
        open_issues.append(
            {
                "id": str(row["id"]),
                "section_id": str(row.get("section_id") or brief_lint.DOCUMENT_ID),
                "class": str(row.get("class") or ""),
                "severity": str(row.get("severity") or "major"),
                "text": _text_of(row),
            }
        )
    return {"clean": bool(clean), "status": final_status, "open_issues": open_issues}


def _refusal(code: str) -> dict:
    return {"ok": False, "reason": code, "input": {}}


def preflight(work_dir: str | os.PathLike, state: dict) -> dict:
    """Is there a delivered memo the brief can be built from, unchanged since delivery? Reads only."""
    work_dir = Path(work_dir)
    if state.get("current_phase") != PHASE:
        return _refusal("task_not_done")
    selection = docx.select_draft(state, work_dir)
    if selection["path"] is None:
        return _refusal("no_memo")
    if "delivered_draft_sha" in state:
        anchor = state["delivered_draft_sha"]
        if anchor is None:  # D-220: the deliverable was not rendered from a draft
            return _refusal("no_memo")
    else:  # a task finalized before plan 75A: the export's own selection, from state alone
        anchor = machine.export_draft_sha(state)[0]
    if selection["sha256"] != anchor:
        return _refusal("draft_changed_after_export")
    if any(stepctx.verify_published(work_dir, state, relative) for relative in PACK_FILES):
        return _refusal("pack_changed_after_export")
    return {
        "ok": True,
        "reason": None,
        "input": {
            "draft_path": str(selection["relative"]),
            "draft_sha": str(selection["sha256"]),
            "draft_version": selection["version"],
            "final_status": state.get("final_status"),
            "language": i18n.normalize(state.get("language")) or i18n.DEFAULT,
            "ui_language": i18n.ui_language(state),
            "slug": docx.slug_of(state, work_dir),
        },
    }


# --- actions ----------------------------------------------------------------


def _refused_action(code: str, ui: str, *, run_id: str | None = None, **fmt: str) -> dict:
    action = {
        "kind": "done",
        "outcome": "refused",
        "reason": code,
        "text": i18n.t(ui, f"ui.brief.refused.{code}", **fmt),
        "present": False,
    }
    if run_id:
        action["run_id"] = run_id
    return action


def _stale(ui: str) -> dict:
    return {"accepted": False, "errors": ["stale_report"], "message": i18n.t(ui, "ui.brief.stale_report")}


def _journal(journal: list, event: str, run: dict, sid: str, **data: object) -> None:
    journal.append((event, sid, {**data, "run": "brief", "run_id": run["run_id"]}))


def _emit(work_dir: Path, journal: list) -> None:
    """Append the collected events — only ever after `brief.lock` is released (§2.2 lock order)."""
    for event, sid, data in journal:
        try:
            events.append_event(work_dir, event, "cli", data, phase=PHASE, step_id=sid)
        except OSError:
            pass  # the journal is best effort (§7.2)


# --- steps ------------------------------------------------------------------


def _close_step_row(run: dict) -> None:
    now = events.utc_now()
    for row in run["steps"]:
        if row["ended_at"] is None:
            row["ended_at"] = now


def _enter(run: dict, step: str) -> None:
    """Move the run to `step` under a new step id (`step_n + 1`, attempt 1)."""
    _close_step_row(run)
    run["step"] = step
    run["dispatch"] = None
    if step == "finished":
        return
    run["step_n"] += 1
    run["step_id"] = step_id(run["run_id"], run["step_n"])
    run["attempt"] = 1
    run["steps"].append(
        {"step_id": run["step_id"], "step": step, "attempt": 1, "started_at": events.utc_now(), "ended_at": None}
    )


def _finish(run: dict, outcome: str, reasons: list[str]) -> None:
    run["outcome"] = outcome
    run["outcome_reasons"] = list(dict.fromkeys(list(run["outcome_reasons"]) + list(reasons)))
    _enter(run, "finished")


def _refuse_run(work_dir: Path, run: dict, code: str) -> dict:
    _finish(run, "refused", [code])
    _save(work_dir, run)
    return _refused_action(code, run["input"]["ui_language"], run_id=run["run_id"])


# --- integrity of promoted files -------------------------------------------


def _verified_bytes(work_dir: Path, relative: str, sha: str) -> bytes:
    try:
        payload = (work_dir / relative).read_bytes()
    except OSError:
        raise BriefChangedOnDisk(relative)
    if state_io.sha256_bytes(payload) != sha:
        raise BriefChangedOnDisk(relative)
    return payload


def current_version(work_dir: str | os.PathLike, run: dict) -> tuple[str, str, str]:
    """`(path, text, sha)` of the last promoted version, re-hashed; a changed file raises BriefChangedOnDisk."""
    if not run["versions"]:
        raise ValueError("no_brief_version")
    row = run["versions"][-1]
    payload = _verified_bytes(Path(work_dir), row["path"], row["sha256"])
    return row["path"], payload.decode("utf-8-sig"), row["sha256"]


def load_review(work_dir: str | os.PathLike, run: dict, slot: str, round_: int) -> dict | None:
    """The promoted review of `slot` in `round_`, re-hashed; None when the reviewer never produced one."""
    rows = [row for row in run["reviews"] if row["slot"] == slot and int(row["round"]) == int(round_)]
    if not rows or rows[-1]["sha256"] is None:
        return None
    payload = _verified_bytes(Path(work_dir), rows[-1]["path"], rows[-1]["sha256"])
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


# --- dispatch: specs, issue, completion, promotion ---------------------------


def _writer_spec(work_dir: Path, run: dict, state: dict, attempt: int, retry: str) -> dict:
    inp = run["input"]
    task = run["step"]
    number = len(run["versions"]) + 1
    canonical = f"{BRIEF_DIR}/v{number}.md"
    seed = instructions = "none"
    if task != "write":
        # The writer edits a copy of the current version in its own attempt folder (the main machine's seed).
        current = run["versions"][-1]
        payload = _verified_bytes(work_dir, current["path"], current["sha256"])
        seed = dispatch.output(canonical, None, run["step_id"], attempt, "writer")["work_path"]
        state_io.write_bytes_atomic(work_dir / seed, payload)
        instructions = run["instructions_path"] or "none"
    model = (state.get("config") or {}).get("writer_model") or dispatch.AGENT_MODELS["brief-writer"]["model"]
    label = f"brief v{number}" + ("" if task == "write" else f" {task}")
    return dispatch.spec(
        "writer",
        "brief-writer",
        label,
        [(canonical, None)],
        model=model,
        brief_task=task,
        memo_path=inp["draft_path"],
        memo_sha=inp["draft_sha"],
        open_issues_path=OPEN_ISSUES_PATH,
        user_question=str(state.get("user_query") or ""),
        brief_template_path=dispatch.lib_path("templates", "decision-brief.md"),
        brief_labels=brief_lint.writer_labels(inp["language"]),
        seed_path=seed,
        instructions_path=instructions,
        retry_errors=retry,
    )


def _review_specs(work_dir: Path, run: dict, state: dict, slots: list[str], retries: dict) -> list[dict]:
    inp = run["input"]
    path, text, sha = current_version(work_dir, run)
    blocks = brief_lint.section_list(brief_lint.parse_brief(text, inp["language"]))
    round_ = run["round"]
    specs = []
    if "fidelity" in slots:
        previous_review, changed = _review_scope(work_dir, run, text)
        specs.append(
            dispatch.spec(
                "fidelity",
                "brief-fidelity-reviewer",
                f"brief fidelity r{round_}",
                [(_fidelity_canonical(run), SLOT_SCHEMAS["fidelity"])],
                memo_path=inp["draft_path"],
                memo_sha=inp["draft_sha"],
                brief_path=path,
                brief_sha=sha,
                open_issues_path=OPEN_ISSUES_PATH,
                user_question=str(state.get("user_query") or ""),
                block_list=blocks,
                checklist="brief-fidelity",
                previous_review_path=previous_review,
                changed_blocks=changed,
                retry_errors=retries.get("fidelity", "none"),
            )
        )
    if "form" in slots:
        specs.append(
            dispatch.spec(
                "form",
                "form-reviewer",
                f"brief form r{round_}",
                [(f"{BRIEF_DIR}/reviews/r{round_}-form.json", SLOT_SCHEMAS["form"])],
                checklist="brief-form",
                draft_path=path,
                draft_sha=sha,
                draft_version=len(run["versions"]),
                iteration=round_ + 1,
                lint_attachment="",
                prose_style_path=dispatch.lib_path("lib", "prose-style.md"),
                brief_role=BRIEF_ROLE,
                section_ids=SECTION_IDS_LEAD + blocks + SECTION_IDS_TAIL,
                retry_errors=retries.get("form", "none"),
            )
        )
    return specs


def _review_scope(work_dir: Path, run: dict, text: str) -> tuple[str, str]:
    """`(previous_review_path, changed_blocks)` of the fidelity slot (D-228, read by its prompt from Task 2 on).

    The previous review is the last fidelity review recorded — the previous round's, or the round's
    own first review for the re-check after `shorten`. Round 0, or a previous review that never came
    or failed its checks, gives a full review (`FULL_REVIEW`). `changed_blocks` compares the version
    that review graded with the current one: the block ids, comma-separated, or `none`; `all` when the
    text before the first conclusion block changed, which no block id covers.
    """
    rows = [row for row in run["reviews"] if row["slot"] == "fidelity"]
    if not rows or not rows[-1]["valid"] or rows[-1]["sha256"] is None:
        return FULL_REVIEW
    review = rows[-1]
    graded = next((row for row in run["versions"] if row["sha256"] == review["draft_sha"]), None)
    if graded is None:
        return FULL_REVIEW
    _verified_bytes(work_dir, review["path"], review["sha256"])
    before = _verified_bytes(work_dir, graded["path"], graded["sha256"]).decode("utf-8-sig")
    changed = brief_lint.changed_blocks(before, text, run["input"]["language"])
    if changed is None:
        return review["path"], FULL_REVIEW[1]
    return review["path"], ", ".join(changed) or "none"


def _fidelity_canonical(run: dict) -> str:
    """`r<round>-fidelity.json`; the re-check after `shorten` has its own name, so the round's first
    review keeps its file and its recorded sha (D-225)."""
    suffix = "-shortened" if run["shorten_done"] else ""
    return f"{BRIEF_DIR}/reviews/r{run['round']}-fidelity{suffix}.json"


def _step_slots(run: dict) -> list[str]:
    if run["step"] in WRITER_STEPS:
        return ["writer"]
    # After the shortening pass only fidelity is re-checked; the form findings are carried (Task 6).
    return ["fidelity"] if run["shorten_done"] else ["fidelity", "form"]


def _issue(work_dir: Path, run: dict, state: dict, attempt: int, slots: list[str], retries: dict,
           journal: list) -> dict:
    """Render the prompts of `slots` for `attempt` and record them as the open dispatch."""
    if attempt != run["attempt"]:
        _close_step_row(run)
        run["attempt"] = attempt
        run["steps"].append(
            {"step_id": run["step_id"], "step": run["step"], "attempt": attempt,
             "started_at": events.utc_now(), "ended_at": None}
        )
    if run["step"] in WRITER_STEPS:
        specs = [_writer_spec(work_dir, run, state, attempt, retries.get("writer", "none"))]
    else:
        specs = _review_specs(work_dir, run, state, slots, retries)
    agents = dispatch.render_agents(
        work_dir, state, step_id=run["step_id"], attempt=attempt, specs=specs, position=0, total=0
    )
    open_slots = dict((run["dispatch"] or {}).get("slots") or {})
    for agent in agents:
        outputs = agent["expected_outputs"]
        open_slots[agent["slot"]] = {
            "status": None,
            "expected": [entry["work_path"] for entry in outputs],
            "canonical": outputs[0]["canonical"],
            "errors": [],
        }
    run["dispatch"] = {"slots": open_slots}
    _save(work_dir, run)
    _journal(journal, "step_issued", run, run["step_id"], step_id=run["step_id"], attempt=attempt,
             kind="dispatch", step=run["step"], agents=[agent["slot"] for agent in agents])
    return {
        "kind": "dispatch",
        "run_id": run["run_id"],
        "step_id": run["step_id"],
        "attempt": attempt,
        "parallel": len(agents) > 1,
        "agents": [
            {
                "slot": agent["slot"],
                "subagent_type": agent["subagent_type"],
                "model": agent["model"],
                "description": agent["description"],
                "prompt": agent["prompt"],
                "expected_outputs": agent["expected_outputs"],
            }
            for agent in agents
        ],
        "chat_line": _chat_line(run, attempt),
    }


def _chat_line(run: dict, attempt: int) -> str:
    """The progress line of a dispatch, in the interface language (`ui.brief.dispatch_line`)."""
    ui = run["input"]["ui_language"]
    step = i18n.t(ui, f"ui.brief.steps.{run['step']}")
    return i18n.t(ui, "ui.brief.dispatch_line", step=step, attempt=attempt)


def _check_slot(work_dir: Path, run: dict, slot: str) -> tuple[bool, list[str], str | None, bytes | None]:
    """`_slot_complete` plus the verified bytes of the output, which are the bytes promotion copies."""
    entry = run["dispatch"]["slots"][slot]
    sid, attempt = run["step_id"], int(run["attempt"])
    marker_file = machine.marker_path(work_dir, sid, attempt, slot)
    if not marker_file.is_file():
        return False, ["missing_done_marker"], None, None
    try:
        marker = state_io.read_json(marker_file)
    except (OSError, ValueError) as exc:
        return False, [f"unreadable_done_marker: {exc}"], None, None
    errors = list(schema.validate(marker, "done-marker"))
    if not errors:
        if str(marker.get("step_id")) != sid or int(marker.get("attempt") or 0) != attempt:
            errors.append("marker_identity_mismatch")
        if str(marker.get("slot")) != slot:
            errors.append("marker_slot_mismatch")
    # A marker of the wrong shape already failed the schema above; its errors stand, it never raises.
    raw_outputs = marker.get("output_sha") if isinstance(marker, dict) else None
    marker_outputs = raw_outputs if isinstance(raw_outputs, dict) else {}
    work_path = entry["expected"][0]
    payload: bytes | None = None
    for relative in entry["expected"]:
        target = work_dir / relative
        try:
            data = target.read_bytes()
        except OSError:
            errors.append(f"missing_output: {relative}")
            continue
        if marker_outputs.get(relative) != state_io.sha256_bytes(data):
            errors.append(f"output_sha_mismatch: {relative}")
        if relative == work_path:
            payload = data
        name = SLOT_SCHEMAS.get(slot)
        if relative.endswith(".json"):
            try:
                document = json.loads(data.decode("utf-8-sig"))
            except ValueError as exc:
                errors.append(f"invalid_json[{relative}]: {exc}")
                continue
            if slot in brief_review.KINDS:
                errors.extend(f"{relative}: {message}" for message in _review_errors(work_dir, run, slot, document))
            elif name:
                errors.extend(f"{relative}: {message}" for message in schema.validate(document, name))
        elif relative.endswith(".md"):
            try:
                # Strict, as `current_version` reads it later: bytes that are not UTF-8 are never promoted.
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                errors.append(f"invalid_utf8[{relative}]: {exc}")
                continue
            if not text.strip() or "\n## " not in "\n" + text:
                errors.append(f"draft_without_sections: {relative}")
    return not errors, errors, work_path, payload


def _review_errors(work_dir: Path, run: dict, slot: str, document: object) -> list[str]:
    """Schema, draft sha, checklist coverage and blocks of a review, against the current version (D-225)."""
    _, text, sha = current_version(work_dir, run)
    blocks = brief_lint.block_ids(brief_lint.parse_brief(text, run["input"]["language"]))
    return brief_review.validate_review(slot, document, draft_sha=sha, block_ids=blocks)["errors"]


def _slot_complete(work_dir: Path, run: dict, slot: str) -> tuple[bool, list[str], str | None]:
    """Marker, identity, output sha and shape of one slot of the open dispatch (§3.1, D-224)."""
    ok, errors, work_path, _ = _check_slot(work_dir, run, slot)
    return ok, errors, work_path


def _record_review(run: dict, slot: str, path: str, sha: str | None, *, valid: bool, errors: list[str]) -> None:
    run["reviews"].append(
        {
            "slot": slot,
            "round": run["round"],
            "path": path,
            "sha256": sha,
            "draft_sha": run["versions"][-1]["sha256"],
            "valid": valid,
            "errors": list(errors),
        }
    )


def _promote(work_dir: Path, run: dict, slot: str, payload: bytes) -> None:
    """Copy the verified bytes onto the canonical path and record their sha."""
    entry = run["dispatch"]["slots"][slot]
    canonical = entry["canonical"]
    state_io.write_bytes_atomic(work_dir / canonical, payload)
    sha = state_io.sha256_bytes(payload)
    entry["status"] = "ok"
    entry["errors"] = []
    if slot == "writer":
        run["versions"].append({"n": len(run["versions"]) + 1, "path": canonical, "sha256": sha, "lint_findings": 0})
    else:
        _record_review(run, slot, canonical, sha, valid=True, errors=[])


def _all_ok(run: dict) -> bool:
    return all(entry["status"] == "ok" for entry in run["dispatch"]["slots"].values())


def _after_dispatch(run: dict) -> None:
    _enter(run, "lint" if run["step"] in WRITER_STEPS else "review_merge")


def _last_parseable_review(work_dir: Path, run: dict, slot: str) -> bytes | None:
    """The newest attempt's review of `slot` that parses as JSON with a checklist list (Task 6 fail-closed)."""
    basename = run["dispatch"]["slots"][slot]["canonical"].rsplit("/", 1)[-1]
    for attempt in range(int(run["attempt"]), 0, -1):
        target = stepctx.step_dir(work_dir, run["step_id"], attempt, slot) / basename
        try:
            payload = target.read_bytes()
            document = json.loads(payload.decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and isinstance(document.get("checklist"), list):
            return payload
    return None


def _exhausted(work_dir: Path, run: dict, pending: list[str]) -> dict | None:
    """No attempt left: refuse (`write`), keep the last good version (other writers) or merge what parsed."""
    step = run["step"]
    if step == "write":
        return _refuse_run(work_dir, run, "writer_failed")
    if step in WRITER_STEPS:
        run["verdict"] = "unverified"
        run["outcome_reasons"] = list(dict.fromkeys(list(run["outcome_reasons"]) + ["writer_failed"]))
        # The length banner describes the version that is rendered, not the one before a shortening pass.
        _, text, _ = current_version(work_dir, run)
        words = brief_lint.brief_words(brief_lint.parse_brief(text, run["input"]["language"]))
        run["too_long"] = words > limits.DECISION_BRIEF_HARD_CAP
        _enter(run, "render")
        _save(work_dir, run)
        return None
    for slot in pending:
        entry = run["dispatch"]["slots"][slot]
        errors = list(entry["errors"]) or ["no_valid_review"]
        payload = _last_parseable_review(work_dir, run, slot)
        if payload is None:
            _record_review(run, slot, entry["canonical"], None, valid=False, errors=errors)
        else:
            state_io.write_bytes_atomic(work_dir / entry["canonical"], payload)
            _record_review(run, slot, entry["canonical"], state_io.sha256_bytes(payload), valid=False, errors=errors)
        entry["status"] = "fail"
    _enter(run, "review_merge")
    _save(work_dir, run)
    return None


def _resume_dispatch(work_dir: Path, run: dict, state: dict, journal: list) -> dict | None:
    """Issue, autoclose, advance or re-issue the dispatch of the current step; None = keep going."""
    if run["dispatch"] is None:
        return _issue(work_dir, run, state, run["attempt"], _step_slots(run), {}, journal)
    slots = run["dispatch"]["slots"]
    for slot, entry in slots.items():
        if entry["status"] is not None:
            continue
        ok, errors, _, payload = _check_slot(work_dir, run, slot)
        if ok:
            # The rule of `machine._autoclose_slots`: a finished, unreported attempt is accepted, so two
            # routers converge on it instead of raising the attempt past each other.
            _promote(work_dir, run, slot, payload)
            _journal(journal, "step_autoclosed", run, run["step_id"], step_id=run["step_id"],
                     attempt=run["attempt"], slot=slot)
        elif errors != ["missing_done_marker"]:
            # The agent finished (its marker is there) but the output fails the checks: the slot fails
            # exactly as its report would have, and the next attempt is told why. A slot with no marker
            # is merely unfinished and keeps `retry_errors: none`.
            entry["status"] = "fail"
            entry["errors"] = errors
    if _all_ok(run):
        _after_dispatch(run)
        _save(work_dir, run)
        return None
    pending = [slot for slot, entry in slots.items() if entry["status"] != "ok"]
    limit = WRITER_ATTEMPTS if run["step"] in WRITER_STEPS else REVIEW_ATTEMPTS
    if int(run["attempt"]) >= limit:
        return _exhausted(work_dir, run, pending)
    retries = {slot: "; ".join(slots[slot]["errors"]) or "none" for slot in pending}
    return _issue(work_dir, run, state, int(run["attempt"]) + 1, pending, retries, journal)


def _lint(work_dir: Path, run: dict) -> None:
    """B-01…B-12 over the current version; one lint fix per round, then review.

    D-227: length alone (B-01, and the word budgets of B-12, D-229) spends no writer pass here — it reaches
    the writer through the merged instructions of `review_merge`, together with what the reviewers found.
    """
    inp = run["input"]
    _, text, sha = current_version(work_dir, run)
    memo = (work_dir / inp["draft_path"]).read_text(encoding="utf-8-sig")
    findings = brief_lint.lint_brief(text, memo, language=inp["language"])
    number = run["versions"][-1]["n"]
    report_path = f"{BRIEF_DIR}/lint-v{number}.json"
    state_io.write_json_atomic(work_dir / report_path, brief_lint.build_report(sha, findings))
    run["versions"][-1]["lint_findings"] = len(findings)
    blocking = any(
        row.get("severity") in BLOCKING_SEVERITIES and row.get("rule") not in brief_review.LENGTH_RULES
        for row in findings
    )
    if blocking and run["lint_fix_done_round"] != run["round"]:
        run["lint_fix_done_round"] = run["round"]
        run["instructions_path"] = report_path
        _enter(run, "lint_fix")
    else:
        _enter(run, "review")
    _save(work_dir, run)


def _review_merge(work_dir: Path, run: dict, state: dict) -> None:
    next_step, patch = brief_review.after_reviews(work_dir, run, state)
    unknown = sorted(set(patch) - RUN_PATCH_FIELDS)
    if unknown:
        raise ValueError(f"brief_run_patch_fields: {unknown}")
    run.update(patch)
    _enter(run, next_step)
    _save(work_dir, run)


def _deliverable_text(text: str) -> str:
    """The brief as the reader gets it: every `<!-- from … -->` binding line and the `<!-- omitted … -->`
    comment removed (D-225, D-229).

    Lines are split with their own ends, so a writer that saved `\\r\\n` loses its bindings too.
    """
    kept = [line for line in text.splitlines(keepends=True) if not BINDING_LINE.fullmatch(line.rstrip("\r\n"))]
    return re.sub(r"(\r?\n)(?:[ \t]*\r?\n){2,}", r"\1\1", "".join(kept))


def _banners(run: dict) -> list[dict]:
    """The notes the brief carries, localized in the memo language (`memo.brief.banners.*`)."""
    language = run["input"]["language"]
    rows = []
    if run["confirmed_unclean"]:
        rows.append({"banner_id": "brief_unclean_memo", "text": i18n.t(language, "memo.brief.banners.unclean_memo")})
    if run["verdict"] == "unverified":
        checks = "; ".join(brief_review.check_name(language, code) for code in run["outcome_reasons"])
        rows.append({"banner_id": "brief_unverified",
                     "text": i18n.t(language, "memo.brief.banners.unverified", checks=checks)})
    if run["too_long"]:
        rows.append({"banner_id": "brief_too_long", "text": i18n.t(language, "memo.brief.banners.too_long")})
    return rows


def _banner_args(run: dict) -> dict:
    language = run["input"]["language"]
    return {
        "banners": _banners(run),
        "banner_title": i18n.t(language, "memo.brief.banners.title"),
        "banner_subtitle": i18n.t(language, "memo.brief.banners.subtitle"),
    }


def _render_failed(run: dict, journal: list, exc: BaseException) -> None:
    """The formatted brief could not be produced: the brief is unverified; the type goes to the journal."""
    run["verdict"] = "unverified"
    run["outcome_reasons"] = list(dict.fromkeys(list(run["outcome_reasons"]) + ["render_failed"]))
    _journal(journal, "fallback_invoked", run, run["step_id"], condition_key="brief_render_failed",
             error_type=type(exc).__name__)


def _render_docx(work_dir: Path, run: dict, state: dict, body: str, journal: list) -> str | None:
    """`brief/brief.docx` when it renders and validates; None sends the brief to the markdown twin.

    The renderer is imported here, as `mf docx render` does: without `python-docx`/`mistune` the
    CLI still loads and the brief is markdown (§5.5, §5.6). Other exceptions reach the caller.
    """
    try:
        from .docx import renderer
    except ImportError as exc:
        _journal(journal, "fallback_invoked", run, run["step_id"], condition_key="brief_docx_unavailable",
                 error_type=type(exc).__name__)
        return None
    language = run["input"]["language"]
    docx_path = work_dir / DOCX_DELIVERABLE
    try:
        index = renderer.load_index(work_dir, state=state)
        result = renderer.render(body, index, docx_path, citation_style=CITATION_STYLE, language=language,
                                 sources=False, appendix=False, **_banner_args(run))
    except renderer.RenderError as exc:
        _journal(journal, "fallback_invoked", run, run["step_id"], condition_key="brief_docx_render_error",
                 error_type=type(exc).__name__)
        return None
    check = validate.validate_path(docx_path, footnotes_map=renderer.footnotes_map(result), language=language)
    if check.get("valid"):
        return DOCX_DELIVERABLE
    _journal(journal, "fallback_invoked", run, run["step_id"], condition_key="brief_docx_invalid",
             errors=[str(error) for error in list(check.get("errors") or [])[:5]])
    return None


def _render(work_dir: Path, run: dict, state: dict, journal: list) -> None:
    """`brief/brief.docx`, else its markdown twin `brief/brief.md`, else the last version (D-225).

    A missing renderer, a renderer failure or an invalid docx degrades to the markdown; anything
    else (the pack edited after the preflight, an unreadable pack) is the reason `render_failed`
    with whatever artifact exists. Only a promoted file changed on disk stops the run.
    """
    language = run["input"]["language"]
    version, text, _ = current_version(work_dir, run)
    body = _deliverable_text(text)
    deliverable = None
    try:
        for relative in PACK_FILES:  # the check `renderer.load_index` makes, with or without the renderer
            if stepctx.verify_published(work_dir, state, relative):
                raise stepctx.OutputModifiedAfterPublish(relative)
        deliverable = _render_docx(work_dir, run, state, body, journal)
    except Exception as exc:  # noqa: BLE001 - `next` never raises on a render (pack edited, pack unreadable, …)
        _render_failed(run, journal, exc)
    if deliverable is None:
        try:
            (work_dir / DOCX_DELIVERABLE).unlink()
        except OSError:
            pass
        try:
            markdown = fallback.render(
                body,
                fallback.SourceIndex.load(work_dir, state=state),
                state={"language": language},
                citation_style=CITATION_STYLE,
                sources=False,
                appendix=False,
                **_banner_args(run),
            )["markdown"]
            state_io.write_bytes_atomic(work_dir / MD_DELIVERABLE, markdown.encode("utf-8"))
            deliverable = MD_DELIVERABLE
        except Exception as exc:  # noqa: BLE001 - the last promoted version is still a brief
            _render_failed(run, journal, exc)
            deliverable = version
    run["deliverable_path"] = deliverable
    _enter(run, "publish")
    _save(work_dir, run)


def _outputs_root(state: dict) -> Path | None:
    """The folder of the published memo, only when it already exists on this host (D-227).

    `finalize` created it when it published the memo; a path recorded on another host (a Cowork
    `/mnt/user-data/outputs/…` in a copied work dir) is no root here, and the brief never creates one.
    """
    published_memo = str(((state or {}).get("progress") or {}).get("published_memo") or "").strip()
    if not published_memo:
        return None
    root = Path(published_memo).parent
    return root if root.is_dir() else None


def _publish(work_dir: Path, run: dict, state: dict) -> dict:
    """Copy the deliverable next to the published memo, when there is one, and finish the run (D-225)."""
    inp = run["input"]
    ui = inp["ui_language"]
    source = work_dir / (run["deliverable_path"] or run["versions"][-1]["path"])
    ext = source.suffix
    path, present, warning = str(source), False, None
    root = _outputs_root(state)
    if root is not None:
        targets = (root / "memoforge" / inp["slug"] / f"brief{ext}", root / f"memo-{inp['slug']}.brief{ext}")
        try:
            payload = source.read_bytes()
            sha = state_io.sha256_bytes(payload)
            for target in targets:
                state_io.write_bytes_atomic(target, payload)
                run["published"].append({"path": str(target), "sha256": sha})
                for other in DELIVERABLE_SUFFIXES:
                    stale = target.with_suffix(other)
                    if other != ext and stale.exists():
                        stale.unlink()  # a previous run's copy in the other format
            path, present = str(targets[-1]), True
        except OSError as exc:
            warning = i18n.t(ui, "ui.brief.copy_failed", path=str(source), error=str(exc))
    outcome = run["verdict"] or "unverified"
    _finish(run, outcome, [])
    _save(work_dir, run)
    key = "ui.brief.done" if outcome == "clean" else "ui.brief.done_unverified"
    text = i18n.t(ui, key, path=path) + (f"\n{warning}" if warning else "")
    return {"kind": "done", "run_id": run["run_id"], "outcome": outcome, "path": path, "present": present,
            "text": text, "chat_line": text.splitlines()[0], "format": ext.lstrip(".")}


# --- the status gate --------------------------------------------------------


def _open_issues(work_dir: Path) -> list:
    try:
        rows = state_io.read_json(work_dir / OPEN_ISSUES_PATH)
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _gate_action(work_dir: Path, run: dict, journal: list) -> dict:
    inp = run["input"]
    ui = inp["ui_language"]
    if run["gate"] is None:
        run["gate"] = {"generation": 0, "channel": "auq"}
        _save(work_dir, run)
    issues = _open_issues(work_dir)
    fmt = {"status": fallback.status_name(inp["final_status"], ui), "count": len(issues)}
    gate = run["gate"]
    base = {"run_id": run["run_id"], "step_id": run["step_id"], "attempt": run["attempt"],
            "generation": gate["generation"], "open_issues": issues}
    text = i18n.t(ui, "ui.brief.gate_text", **fmt)
    _journal(journal, "step_issued", run, run["step_id"], step_id=run["step_id"], attempt=run["attempt"],
             kind=f"gate-{gate['channel']}", step=run["step"], agents=[])
    if gate["channel"] == "text":
        return {"kind": "gate-text", **base, "text": text, "end_turn": True}
    yes, no = i18n.t(ui, "ui.brief.option_yes"), i18n.t(ui, "ui.brief.option_no")
    question = {
        "question": i18n.t(ui, "ui.brief.gate_question", **fmt),
        "header": i18n.t(ui, "ui.brief.gate_header"),
        "multiSelect": False,
        "options": [{"label": yes, "description": yes}, {"label": no, "description": no}],
    }
    return {"kind": "gate-auq", **base, "questions": [question], "text_fallback": text}


def _normalise_reply(text: str) -> str:
    return " ".join(str(text).casefold().split()).strip(" .!?,;:«»\"'")


def _answer_of(reply: str, ui: str) -> str | None:
    """`yes`, `no` or None for a reply in the interface language (the words or the option labels)."""
    reply = _normalise_reply(reply)
    for answer in ("yes", "no"):
        words = [str(word) for word in i18n.node(ui, f"ui.brief.{answer}_words")]
        words.append(i18n.t(ui, f"ui.brief.option_{answer}"))
        if reply in {_normalise_reply(word) for word in words}:
            return answer
    return None


def _gate_report(work_dir: Path, run: dict, args: argparse.Namespace) -> dict:
    ui = run["input"]["ui_language"]
    if args.status == "no_answer":
        run["gate"] = {"generation": int((run["gate"] or {}).get("generation") or 0), "channel": "text"}
        _save(work_dir, run)
        return {"accepted": True, "channel": "text"}
    if args.answers is not None:
        try:
            answers = json.loads(args.answers)
        except ValueError:
            return {"accepted": False, "errors": ["invalid_answers"]}
        values = list(answers.values()) if isinstance(answers, dict) else []
        reply = str(values[0]) if values else ""
    else:
        reply = str(args.text)
    answer = _answer_of(reply, ui)
    if answer is None:
        # The gate stays open; the next `next` asks again, as text.
        run["gate"] = {"generation": int((run["gate"] or {}).get("generation") or 0), "channel": "text"}
        _save(work_dir, run)
        return {"accepted": False, "errors": ["unrecognised_answer"]}
    if answer == "yes":
        run["confirmed_unclean"] = True
        _enter(run, "write")
        _save(work_dir, run)
        return {"accepted": True, "answer": "yes"}
    _finish(run, "declined", [])
    _save(work_dir, run)
    return {"accepted": True, "answer": "no", "kind": "done", "run_id": run["run_id"], "outcome": "declined",
            "text": i18n.t(ui, "ui.brief.declined"), "present": False}


# --- start of a run ---------------------------------------------------------


def _restore(brief: Path, aside: Path) -> None:
    """Put the finished run back where it was after a failed archive, when that is still possible."""
    try:
        if aside.exists() and not (brief / PREVIOUS_DIR).exists():
            if brief.exists():
                brief.rmdir()
            os.replace(aside, brief)
    except OSError:
        pass


def _clear_interrupted_start(brief: Path) -> None:
    """Remove what a start cut short left in `brief/` — everything except the retained `previous/`."""
    for child in brief.iterdir():
        if child.name == PREVIOUS_DIR:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _archive(work_dir: Path) -> None:
    """Move the finished run into `brief/previous/` (one copy; the older one is dropped). Raises OSError.

    Only a `brief/` that holds a run state is a run to archive (the caller reaches here only when that
    run is finished). A `brief/` without `state.json` is a start cut short after its archive — its
    `previous/` is the run just archived and stays; only the other leftovers of that start go.
    """
    brief = brief_dir(work_dir)
    aside = work_dir / ARCHIVE_ASIDE
    if aside.exists():
        # An archive cut short before its last rename: the aside holds the finished run and
        # `brief/` is empty or missing.
        if brief.exists():
            brief.rmdir()
    elif brief.exists() and not run_path(work_dir).exists():
        _clear_interrupted_start(brief)
        return
    elif brief.exists():
        os.replace(brief, aside)
    else:
        return
    try:
        brief.mkdir()
        if (aside / PREVIOUS_DIR).exists():
            shutil.rmtree(aside / PREVIOUS_DIR)
        os.replace(aside, brief / PREVIOUS_DIR)
    except OSError:
        _restore(brief, aside)
        raise


def _new_run(work_dir: Path, state: dict, checked: dict) -> dict:
    status = classify_status(state)
    run_id = new_run_id()
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": str(state.get("task_id") or work_dir.name),
        "started_at": events.utc_now(),
        "step": "status_gate",
        "step_n": 0,
        "step_id": step_id(run_id, 0),
        "attempt": 1,
        "round": 0,
        "shorten_done": False,
        "lint_fix_done_round": None,
        "input": checked["input"],
        "confirmed_unclean": None,
        "gate": None,
        "dispatch": None,
        "versions": [],
        "reviews": [],
        "instructions_path": None,
        "carried_form_findings": [],
        "too_long": False,
        "deliverable_path": None,
        "steps": [],
        "verdict": None,
        "outcome": None,
        "outcome_reasons": [],
        "published": [],
    }
    state_io.write_json_atomic(work_dir / OPEN_ISSUES_PATH, status["open_issues"])
    _enter(run, "write" if status["clean"] else "status_gate")
    _save(work_dir, run)
    return run


def _start(work_dir: Path) -> tuple[dict | None, dict | None, dict | None]:
    """`(run, state, refusal)`: preflight, then archive and write a new run — or refuse and write nothing."""
    try:
        state = state_io.read_state(work_dir)
    except (OSError, ValueError):
        return None, None, _refused_action("no_task", i18n.DEFAULT)
    ui = i18n.ui_language(state)
    checked = preflight(work_dir, state)
    if not checked["ok"]:
        return None, None, _refused_action(checked["reason"], ui)
    try:
        _archive(work_dir)
    except OSError as exc:
        return None, None, _refused_action("previous_run_locked", ui, error=str(exc))
    return _new_run(work_dir, state, checked), state, None


# --- next / report ----------------------------------------------------------


def _advance(work_dir: Path, run: dict, state: dict, journal: list) -> dict:
    """Walk the code steps until an action has to go to the router."""
    try:
        for _ in range(MAX_CODE_STEPS):
            step = run["step"]
            if step == "status_gate":
                return _gate_action(work_dir, run, journal)
            if step in DISPATCH_STEPS:
                action = _resume_dispatch(work_dir, run, state, journal)
                if action is not None:
                    return action
            elif step == "lint":
                _lint(work_dir, run)
            elif step == "review_merge":
                _review_merge(work_dir, run, state)
            elif step == "render":
                _render(work_dir, run, state, journal)
            elif step == "publish":
                return _publish(work_dir, run, state)
            else:
                raise ValueError(f"brief_step_without_action: {step}")
    except BriefChangedOnDisk:
        return _refuse_run(work_dir, run, "brief_changed_on_disk")
    raise RuntimeError("brief_step_loop")


def _next_locked(work_dir: Path, journal: list) -> dict:
    run = read_run(work_dir)
    if run is not None and run.get("outcome") is None:
        return _advance(work_dir, run, state_io.read_state(work_dir), journal)
    run, state, refusal = _start(work_dir)
    if refusal is not None:
        return refusal
    return _advance(work_dir, run, state, journal)


def run_next(args: argparse.Namespace) -> dict:
    """`mf brief next --workdir W` — continue the active run, or start one; returns one action."""
    work_dir = Path(args.workdir)
    if not state_io.state_path(work_dir).is_file():
        return _refused_action("no_task", i18n.DEFAULT)  # and no `brief.lock` in a folder that is no task
    journal: list = []
    with _lock(work_dir):
        action = _next_locked(work_dir, journal)
    _emit(work_dir, journal)
    return action


def _report_mode(args: argparse.Namespace) -> str | None:
    modes = []
    if args.slot is not None:
        modes.append("slot")
    if args.answers is not None:
        modes.append("answers")
    if args.text is not None:
        modes.append("text")
    if args.status == "no_answer":
        modes.append("no_answer")
    if len(modes) != 1:
        return None
    mode = modes[0]
    if mode == "slot" and args.status not in ("ok", "fail"):
        return None
    if mode in ("answers", "text") and args.status is not None:
        return None
    return mode


def _slot_report(work_dir: Path, run: dict, args: argparse.Namespace, journal: list) -> dict:
    slot = str(args.slot)
    if run["step"] not in DISPATCH_STEPS or run["dispatch"] is None or slot not in run["dispatch"]["slots"]:
        return {"accepted": False, "errors": [f"unknown_slot: {slot}"]}
    entry = run["dispatch"]["slots"][slot]
    if entry["status"] == "ok":
        return {"accepted": True, "already_reported": True, "slot": slot, "status": "ok"}
    if args.status == "fail":
        detail = ""
        if args.stdout:
            try:
                detail = " ".join(Path(args.stdout).read_text(encoding="utf-8-sig").split())[:300]
            except OSError:
                detail = ""
        entry["status"] = "fail"
        entry["errors"] = [f"agent_failed: {detail}" if detail else "agent_failed"]
        _save(work_dir, run)
        return {"accepted": True, "slot": slot, "status": "fail"}
    ok, errors, _, payload = _check_slot(work_dir, run, slot)
    if not ok:
        entry["status"] = "fail"
        entry["errors"] = errors
        _save(work_dir, run)
        return {"accepted": True, "slot": slot, "status": "fail", "slot_errors": errors}
    _promote(work_dir, run, slot, payload)
    _journal(journal, "agent_returned", run, run["step_id"], step_id=run["step_id"], attempt=run["attempt"],
             slot=slot)
    if _all_ok(run):
        _after_dispatch(run)
    _save(work_dir, run)
    return {"accepted": True, "slot": slot, "status": "ok"}


def _report_locked(work_dir: Path, args: argparse.Namespace, journal: list) -> dict:
    mode = _report_mode(args)
    if mode is None:
        return {
            "accepted": False,
            "errors": [
                "report_mode: exactly one of --slot X --status ok|fail, --answers, --text, --status no_answer"
            ],
        }
    run = read_run(work_dir)
    if run is None:
        return _stale(i18n.ui_language(state_io.read_state_or_none(work_dir)))
    ui = run["input"]["ui_language"]
    if (
        run.get("outcome") is not None
        or run["run_id"] != args.run
        or run["step_id"] != args.step
        or int(run["attempt"]) != int(args.attempt)
    ):
        return _stale(ui)
    if mode == "slot":
        try:
            return _slot_report(work_dir, run, args, journal)
        except BriefChangedOnDisk:
            return {"accepted": False, "errors": ["brief_changed_on_disk"],
                    **_refuse_run(work_dir, run, "brief_changed_on_disk")}
    if run["step"] != "status_gate":
        return _stale(ui)
    return _gate_report(work_dir, run, args)


def run_report(args: argparse.Namespace) -> dict:
    """`mf brief report --workdir W --run R --step S --attempt N` + one answer; a stale identity changes nothing."""
    work_dir = Path(args.workdir)
    if not state_io.state_path(work_dir).is_file():
        return {"accepted": False, "errors": ["no_task"]}
    journal: list = []
    with _lock(work_dir):
        result = _report_locked(work_dir, args, journal)
    _emit(work_dir, journal)
    return result


def register(subparsers) -> None:
    """Register `mf brief next` and `mf brief report` (D-224)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "brief", "decision brief of a finished task (/memoforge:brief)")
    nxt = group.add_parser("next", help="continue or start the decision brief; returns one action")
    nxt.add_argument("--workdir", required=True)
    nxt.set_defaults(func=run_next)

    report = group.add_parser("report", help="close a brief dispatch slot or answer the status gate")
    report.add_argument("--workdir", required=True)
    report.add_argument("--run", required=True)
    report.add_argument("--step", required=True)
    report.add_argument("--attempt", type=int, required=True)
    report.add_argument("--slot", default=None)
    report.add_argument("--status", default=None, choices=["ok", "fail", "no_answer"])
    report.add_argument("--stdout", default=None, help="file with the agent's final message")
    report.add_argument("--answers", default=None, help="JSON object of AskUserQuestion answers")
    report.add_argument("--text", default=None, help="the user's reply to the text gate")
    report.set_defaults(func=run_report)
