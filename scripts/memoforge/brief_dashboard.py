"""The dashboard page during `/memoforge:brief` (D-256).

A brief run writes no task file (D-224), so it cannot move the memo's page through `machine`. It
rebuilds the memo's last page from `state.json` with `machine.dashboard_patch` (read only), lays the
brief's progress over the overview, writes the document to `brief/dashboard.json` and hands the router
the memo's own `write_db` block. The page is decoration (D-87): nothing here may change or fail a step.
"""

from __future__ import annotations

from pathlib import Path

from . import events, i18n, machine, state_io

PATCH_FILE: tuple[str, ...] = ("brief", "dashboard.json")


def _status(run: dict) -> str:
    """`running | waiting | clean | unverified | declined | refused` — the brief's state for the page."""
    outcome = run.get("outcome")
    if outcome:
        return str(outcome)
    return "waiting" if run.get("step") == "status_gate" else "running"


def _label(ui: str, key: str, fallback: str) -> str:
    try:
        return i18n.t(ui, key)
    except (KeyError, i18n.PackUnavailable):
        return fallback


def _step_text(row: dict, ui: str) -> str:
    step = _label(ui, f"ui.brief.steps.{row['step']}", str(row["step"]))
    attempt = int(row.get("attempt") or 1)
    return step if attempt == 1 else i18n.t(ui, "ui.brief.dashboard.attempt", step=step, attempt=attempt)


def _step_state(row: dict) -> str:
    if row.get("ended_at"):
        return "done"
    return "waiting" if row.get("step") == "status_gate" else "running"


def _check(ui: str, key: str, refused: bool) -> str:
    """The check name of an outcome reason, or "" for a reason that is not a check (fix F5).

    A refused run's reasons that are refusal codes (`ui.brief.refused.*`, e.g. `writer_failed`, which also
    names a check) are the refusal, not a check.
    """
    if refused and _label(ui, f"ui.brief.refused.{key}", ""):
        return ""
    return _label(ui, f"memo.brief.checks.{key}", "")


def _file(run: dict, answer: dict) -> str:
    if answer.get("kind") == "done" and answer.get("path"):
        return str(answer["path"])
    published = [row for row in run.get("published") or [] if isinstance(row, dict)]
    return str(published[-1]["path"]) if published else ""


def overlay(run: dict, answer: dict, ui: str) -> dict:
    """The keys the brief sets on the memo's page; everything else stays the memo's."""
    steps = [row for row in run.get("steps") or [] if isinstance(row, dict)]
    status = _status(run)
    status_label = _label(ui, f"ui.brief.dashboard.status.{status}", status)
    slots = ((run.get("dispatch") or {}).get("slots") or {}) if run.get("outcome") is None else {}
    since = str(steps[-1]["started_at"]) if steps else ""
    text = str(answer.get("chat_line") or "") or (str(answer.get("text") or "").splitlines() or [""])[0]
    # Only check names (fix F5): an operational refusal code stays in the status label.
    refused = run.get("outcome") == "refused"
    checks = [
        name for name in (_check(ui, key, refused) for key in run.get("outcome_reasons") or []) if name
    ]
    done = len([row for row in steps if row.get("ended_at")])
    timeline = [
        {"ts": str(row.get("started_at") or ""), "text": _step_text(row, ui), "state": _step_state(row)}
        for row in steps[-machine.DASHBOARD_TIMELINE:]
    ]
    if refused and timeline:
        timeline[-1]["state"] = "failed"  # the refused step (fix F4); `_finish` stamped its `ended_at`
    return {
        "phase": "brief",
        "phase_label": i18n.t(ui, "ui.brief.dashboard.phase"),
        # The header bar follows the brief's steps (fix F9); the memo's phases are over.
        "phase_no": done,
        "phase_total": len(steps),
        "status": status,
        "status_label": status_label,
        "chat_line": text or status_label,
        "steps_done": done,
        "steps_total": len(steps),
        "agents_running": [
            {"slot": slot, "description": _label(ui, f"ui.brief.dashboard.agents.{slot}", slot), "since": since}
            for slot, entry in slots.items()
            if isinstance(entry, dict) and entry.get("status") is None
        ],
        "timeline": timeline,
        "gate": None,
        "brief": {
            "status_label": status_label,
            "rounds": int(run.get("round") or 0),
            "file": _file(run, answer),
            "checks": checks,
        },
        "updated_at": events.utc_now(),
    }


def attach(work_dir: Path, answer: dict) -> dict:
    """Add the `write_db` block to a `brief next` / ending `brief report` answer when the page is live.

    Only an answer of the CURRENT run qualifies: a refusal before a run exists (the memo may still be
    running and own the page) carries no block. The caller holds `brief.lock`.
    """
    try:
        # An answer with `errors` qualifies only when it ends the run (`kind: done`, e.g. the report that
        # finds `brief_changed_on_disk`, `brief._report_locked`; fix F6); a plain rejected report does not.
        if not isinstance(answer, dict) or (answer.get("errors") and answer.get("kind") != "done"):
            return answer
        from .brief import read_run  # the driver imports this module; import late

        run = read_run(work_dir)
        if run is None or answer.get("run_id") != run.get("run_id"):
            return answer
        state = state_io.read_state_or_none(work_dir)
        url = machine.dashboard_url(state) if state else ""
        if not url:
            return answer
        ui = run["input"]["ui_language"]
        document = machine.dashboard_patch(state)
        document.update(overlay(run, answer, ui))
        target = Path(work_dir) / Path(*PATCH_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, document)
    except Exception:  # noqa: BLE001 - decoration never fails the brief (D-87)
        return answer
    answer["dashboard"] = {
        "write_db": {
            "url": url,
            "collection": machine.DASHBOARD_COLLECTION,
            "doc_id": machine.DASHBOARD_DOC_ID,
            "file_path": str(target.absolute()),
        }
    }
    return answer
