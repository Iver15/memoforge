"""`mf draft finish` — anchor, lint and audit-citations of one draft in a single step (D-117)."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import citations, lint, sources, state_io, stepctx


def _replays_own_output(
    work_dir: Path,
    state: dict,
    step_id: str,
    attempt: int,
    draft_path: Path,
    draft_rel: str,
) -> bool:
    """True when the canonical draft is this identity's own anchored output (D-42, D-117).

    The anchored draft is published before `close_step` writes its sha into `published[]`, so an
    interruption between the two leaves the canonical file one step ahead of state: the replay of
    the same `(step_id, attempt)` reads its own bytes as `output_modified_after_publish` and the
    step can never be closed again. The staged input of that identity still holds the pre-anchor
    bytes, and anchoring is deterministic — when it reproduces the canonical file exactly, or when
    the file carries a sha this very step published, the replay recomputes the same result and goes
    on to the lint and the audit. Any other drift is a foreign edit and is still refused.
    """
    current = state_io.sha256_file(draft_path)
    staged = stepctx.staged_input(work_dir, step_id, attempt, draft_path.name)
    if staged is not None:
        anchored, _, errors = lint.checked_anchor(staged.read_text(encoding="utf-8-sig"))
        if not errors and state_io.sha256_bytes(anchored.encode("utf-8")) == current:
            return True
    return any(
        isinstance(row, dict)
        and row.get("canonical_path") == draft_rel
        and row.get("step_id") == step_id
        and row.get("sha256") == current
        for row in (state.get("published") or [])
    )


def run_finish(args: argparse.Namespace) -> dict:
    """`mf draft finish --step --attempt --draft <path>` — the three deterministic checks at once.

    `draft anchor`, `draft lint` and `draft audit-citations` were three script steps, and each one
    cost the orchestrator a full `mf next` ↔ command ↔ `mf next` round trip: on a real run two of
    those idle windows alone came to 12 minutes for work that takes milliseconds. The three run
    here in process, in that order — the lint and the audit judge the **anchored** bytes, exactly as
    they did when they ran after `draft anchor` — under one identity, one `close_step` and one
    publication set (`drafts/vN.md`, `lint.json`, `citations.json`).

    D-54 is unchanged: the two reports belong to the draft sha they ran on, so a writer fix makes
    the machine re-issue this same `step_id` with `attempt+1` and `reason: rerun`.

    The anchor half keeps both guarantees of the standalone `draft anchor`: it refuses a heading
    collision before the canonical draft is touched (D-130), and a replay whose canonical draft is
    already this identity's own anchored output recovers from the staged input instead of reading
    its own bytes as a foreign edit (D-42).
    """
    work_dir = Path(args.workdir)
    draft_rel = stepctx.rel_path(work_dir, args.draft)
    args_key = f"draft finish --draft {draft_rel}"
    state = state_io.read_state(work_dir)
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": identity["errors"], "reason": identity.get("reason")}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = dict(identity.get("result") or {})
        stored["already_done"] = True
        return stored

    draft_path = stepctx.abs_path(work_dir, draft_rel)
    if not draft_path.is_file():
        return {"errors": [f"draft_not_found: {draft_rel}"]}
    drift = stepctx.verify_published(work_dir, state, draft_rel)
    recovered = False
    if drift:
        if not _replays_own_output(
            work_dir, state, args.step, args.attempt, draft_path, draft_rel
        ):
            return {"errors": [drift], "draft": draft_rel}
        recovered = True

    # --- anchor ----------------------------------------------------------
    staged = stepctx.stage_input(work_dir, args.step, args.attempt, draft_path)
    source_text = (stepctx.abs_path(work_dir, staged) if staged else draft_path).read_text(encoding="utf-8-sig")
    anchored, inserted, errors = lint.checked_anchor(source_text)
    if errors:
        # D-130 × D-117: a heading collision makes every finding on `s-N` ambiguous, so the merged
        # command refuses it on exactly the terms `draft anchor` does — the shared `checked_anchor`
        # — before the canonical draft is touched, not as an L-15 finding after the duplicate
        # anchors are already published.
        return {"errors": errors, "draft": draft_rel}
    anchored_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, draft_path.name, anchored.encode("utf-8")
    )
    draft_entry = stepctx.publish_file(work_dir, anchored_file, draft_rel, step_id=args.step)
    draft_sha = draft_entry["sha256"]
    document = lint.parse_draft(anchored)
    anchor_result = {
        "anchors_inserted": inserted,
        "sections": [
            section["section_id"] for section in document["sections"] if section["level"] in (2, 3)
        ],
    }

    # --- lint ------------------------------------------------------------
    template = lint.resolve_template(state, getattr(args, "template", None))
    findings, warnings = lint.lint_text(anchored, work_dir=work_dir, state=state, template=template)
    lint_report = lint.build_report(draft_sha, findings)
    lint_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, "lint.json", state_io.dumps(lint_report).encode("utf-8")
    )
    lint_entry = stepctx.publish_file(work_dir, lint_file, lint.LINT_PATH, step_id=args.step)
    lint_result = {
        "report_path": lint.LINT_PATH,
        "template": template,
        "clean": lint_report["clean"],
        "findings_count": len(findings),
        "blockers": sum(1 for row in findings if row["severity"] == "blocker"),
        "majors": sum(1 for row in findings if row["severity"] == "major"),
    }

    # --- citations -------------------------------------------------------
    stepctx.stage_input(work_dir, args.step, args.attempt, work_dir / sources.PACK_PATH)
    # D-131: the C-07 severity is the run's — `info` in Brief, `major` in Full — and the mode is
    # read from the state this command already loaded, exactly as `draft audit-citations` does.
    citation_findings = citations.audit(anchored, work_dir=work_dir, mode=str(state.get("mode") or ""))
    citation_report = lint.build_report(draft_sha, citation_findings)
    citation_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, "citations.json", state_io.dumps(citation_report).encode("utf-8")
    )
    citation_entry = stepctx.publish_file(
        work_dir, citation_file, citations.CITATIONS_PATH, step_id=args.step
    )
    # D-131 (D34-10): `clean` alone reads as «citations are fine» with 25 majors behind it. The
    # merged command is the one the machine runs now, so it reports both severities the standalone
    # audit reports — `mf next` has nothing else to print them from.
    citation_result = {
        "report_path": citations.CITATIONS_PATH,
        "clean": citation_report["clean"],
        "findings_count": len(citation_findings),
        "blockers": sum(1 for row in citation_findings if row["severity"] == "blocker"),
        "majors": sum(1 for row in citation_findings if row["severity"] == "major"),
    }

    result = {
        "draft": draft_rel,
        "draft_sha": draft_sha,
        "anchor": anchor_result,
        "lint": lint_result,
        "citations": citation_result,
        "clean": bool(lint_report["clean"] and citation_report["clean"]),
        # D-131: the totals of both reports, so the orchestrator reads «2 majors, 0 blockers» off
        # the answer without opening either one.
        "blockers": lint_result["blockers"] + citation_result["blockers"],
        "majors": lint_result["majors"] + citation_result["majors"],
    }
    if recovered:
        # D-42: the canonical draft was this identity's own anchored output from an interrupted
        # run; the replay recomputed it and says so, so the recovery is visible in `result_ref`.
        result["replay_recovered"] = True

    def mutate(state_doc: dict) -> None:
        existing = list(state_doc.get("drafting_warnings") or [])
        for warning in warnings:
            if warning not in existing:
                existing.append(warning)
        state_doc["drafting_warnings"] = existing

    stepctx.close_step(
        work_dir,
        args.step,
        args.attempt,
        result,
        phase=args.phase,
        args_key=args_key,
        published=[draft_entry, lint_entry, citation_entry],
        mutate=mutate,
    )
    return result


def register(subparsers) -> None:
    """Register `mf draft finish` (the `draft` group is shared with lint.py and citations.py)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "draft", "draft anchors and deterministic checks")
    parser = group.add_parser("finish", help="anchor, lint and audit the citations of a draft in one step")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--template", default=None, choices=[lint.TEMPLATE_CLASSICAL, lint.TEMPLATE_BRIEF])
    parser.add_argument("--phase", default=None)
    parser.set_defaults(func=run_finish)
