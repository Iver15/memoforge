"""`mf revision next` — branches 1..8 of the revision loop; the only owner of `current_iteration` (ТЗ §4.5 п.4)."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import limits, review, state_io, stepctx

REASON_INCOMPLETE_REVIEW = "incomplete_review"
REASON_ALL_REVIEWERS_FAILED = "all_reviewers_failed"
REASON_REGRESSION = "regression_forced_exit"
REASON_UNRESOLVED_BLOCKERS = "unresolved_blockers"
REASON_LENGTH_OVERFLOW = "length_overflow"
"""Accumulated in `final_status_reasons[]`; never removed by a later verdict (§2.1 стр.15, §2.2, D-21)."""

LENGTH_OVERFLOW_RULE = "L-10"
LENGTH_OVERFLOW_BRANCHES: tuple[int, ...] = (2, 4, 5, 7, 8)
"""D-44: every branch that leaves the loop for `client_readiness` carries the L-10 overlay of §2.1."""

NEXT_RERUN = "rerun_reviewers"
NEXT_CLIENT_READINESS = "client_readiness"
NEXT_MEDIATOR = "dispatch_mediator"
NEXT_WRITER = "dispatch_writer"


def draft_path(version: int) -> str:
    """Canonical path of one draft version (§6)."""
    return f"drafts/v{version}.md"


def _blockers(record: dict) -> list[dict]:
    return [issue for issue in record.get("issues", []) if issue.get("severity") == "blocker"]


def _grounded_blockers(record: dict) -> list[dict]:
    return [issue for issue in _blockers(record) if issue.get("grounded")]


def _substance_keys(record: dict) -> set[tuple[str, str]]:
    return {
        (issue.get("section_id", ""), issue.get("category", ""))
        for issue in _grounded_blockers(record)
        if issue.get("tier") == "substance"
    }


def _participants(issue: dict) -> set[str]:
    """Everyone who raised one issue: `provenance[]` keeps every participant of a merge (§4.5 п.3)."""
    sources = {source for source in (issue.get("provenance") or []) if source}
    if issue.get("source_reviewer"):
        sources.add(issue["source_reviewer"])
    return sources


def mediator_needed(record: dict) -> bool:
    """§4.5 п.4.6: >=2 blocker/major issues raised by >=2 reviewers, or a recorded `conflict`."""
    if record.get("conflict"):
        return True
    strong = [
        issue for issue in record.get("issues", []) if issue.get("severity") in ("blocker", "major")
    ]
    reviewers: set[str] = set()
    for issue in strong:
        reviewers |= _participants(issue)
    return len(strong) >= review.MEDIATOR_MIN_ISSUES and len(reviewers) >= review.MEDIATOR_MIN_REVIEWERS


def decide(
    *,
    iteration: int,
    record: dict,
    previous: dict | None,
    max_iterations: int,
    reviewer_rerun_used: int,
    mediator_exists: bool,
) -> dict:
    """Pure branch selection of §4.5 п.4 — first matching branch wins."""
    failed = list(record.get("failed_reviewers") or [])
    reviewers = list(record.get("reviewers") or [])
    coverage = sorted(record.get("coverage") or [])
    substance = int(record.get("substance_blockers") or 0)
    form = int(record.get("form_blockers") or 0)

    # 1. failed reviewers with rerun budget left.
    if failed and reviewer_rerun_used < limits.MAX_REVIEWER_RERUN:
        return {
            "branch": 1,
            "next": NEXT_RERUN,
            "rerun_reviewers": sorted(failed),
            "final_status": None,
            "reasons": [],
            "banner": None,
        }

    # 2. incomplete mandatory review: never `approved`.
    if failed:
        all_failed = bool(reviewers) and set(failed) >= set(reviewers)
        return {
            "branch": 2,
            "next": NEXT_CLIENT_READINESS,
            "final_status": f"manual_review_required_on_v{iteration}",
            "reasons": [REASON_ALL_REVIEWERS_FAILED if all_failed else REASON_INCOMPLETE_REVIEW],
            "banner": ("reviewer_json_invalid", {"iteration": iteration, "count": len(failed)}),
            "failed_reviewers": sorted(failed),
        }

    # 3. regression: equal coverage, more substance blockers and a new grounded one.
    if iteration >= 2 and previous is not None:
        previous_coverage = sorted(previous.get("coverage") or [])
        new_keys = _substance_keys(record) - _substance_keys(previous)
        if (
            coverage == previous_coverage
            and substance > int(previous.get("substance_blockers") or 0)
            and new_keys
        ):
            return {
                "branch": 3,
                "next": NEXT_CLIENT_READINESS,
                "final_status": f"forced_exit_on_v{iteration - 1}_with_remaining_issues",
                "reasons": [REASON_REGRESSION],
                "banner": ("max_iterations_with_blockers", {"count": substance}),
                "regression_to": iteration - 1,
                "new_blockers": sorted(f"{section}/{category}" for section, category in new_keys),
            }

    # 4. clean.
    if substance == 0 and form == 0:
        return {
            "branch": 4,
            "next": NEXT_CLIENT_READINESS,
            "final_status": f"approved_on_v{iteration}",
            "reasons": [],
            "banner": None,
        }

    # 5. form blockers only.
    if substance == 0:
        return {
            "branch": 5,
            "next": NEXT_CLIENT_READINESS,
            "final_status": f"accepted_early_on_v{iteration}",
            "reasons": [],
            "banner": ("max_iterations_with_blockers", {"count": form}),
        }

    grounded = _grounded_blockers(record)

    # 6. another iteration on grounded blockers.
    if iteration < max_iterations and grounded:
        needs_mediator = mediator_needed(record)
        if needs_mediator and not mediator_exists:
            return {
                "branch": 6,
                "next": NEXT_MEDIATOR,
                "final_status": None,
                "reasons": [],
                "banner": None,
                "mediator_needed": True,
            }
        return {
            "branch": 6,
            "next": NEXT_WRITER,
            "final_status": None,
            "reasons": [],
            "banner": None,
            "mediator_needed": needs_mediator,
            "preseed_version": iteration + 1,
        }

    # 7. iterations left but nothing grounded to fix.
    if iteration < max_iterations:
        return {
            "branch": 7,
            "next": NEXT_CLIENT_READINESS,
            "final_status": f"accepted_early_on_v{iteration}",
            "reasons": [],
            "banner": ("max_iterations_with_blockers", {"count": len(_blockers(record))}),
        }

    # 8. budget of iterations exhausted.
    return {
        "branch": 8,
        "next": NEXT_CLIENT_READINESS,
        "final_status": f"forced_exit_on_v{iteration}_with_remaining_issues",
        "reasons": [REASON_UNRESOLVED_BLOCKERS],
        "banner": ("max_iterations_with_blockers", {"count": len(_blockers(record))}),
    }


def length_overflow(work_dir: Path, draft_sha: str | None) -> bool:
    """True when the current `lint.json` still reports the L-10 word cap for this draft (§2.1)."""
    path = work_dir / "lint.json"
    if not path.is_file():
        return False
    try:
        report = state_io.read_json(path)
    except ValueError:
        return False
    if not isinstance(report, dict):
        return False
    if draft_sha and report.get("draft_sha") != draft_sha:
        return False
    return any(
        isinstance(finding, dict) and finding.get("rule") == LENGTH_OVERFLOW_RULE
        for finding in report.get("findings", [])
    )


def apply_length_overflow(decision: dict, iteration: int) -> dict:
    """Overlay §2.1 on a loop-exit branch: `manual_review_required_on_v<N>` + `length_overflow`."""
    overlaid = dict(decision)
    overlaid["final_status"] = f"manual_review_required_on_v{iteration}"
    overlaid["reasons"] = list(decision["reasons"]) + [REASON_LENGTH_OVERFLOW]
    overlaid["length_overflow"] = True
    return overlaid


def _draft_version_row(version: int, sha: str) -> dict:
    return {
        "version": version,
        "path": draft_path(version),
        "sha256": sha,
        "lint_clean": False,
        "citations_clean": False,
        "checked_at": None,
    }


def _record_draft_version(state: dict, row: dict) -> None:
    rows = state.setdefault("draft_versions", [])
    for index, existing in enumerate(rows):
        if existing.get("version") == row["version"]:
            rows[index] = row
            return
    rows.append(row)


def run_next(args: argparse.Namespace) -> dict:
    """`mf revision next` — choose the branch, apply it to state and pre-seed the next draft."""
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    iteration = args.iteration or int(state.get("current_iteration") or 1)
    args_key = f"revision next --iteration {iteration}"
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": list(identity["errors"]), "reason": identity.get("reason"), "step_id": args.step}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = identity.get("result")
        return stored if isinstance(stored, dict) else {"already_closed": True, "result": stored}

    record = review.iteration_record(state, iteration)
    if record is None:
        return {"errors": [f"no_aggregate_for_iteration: {iteration}"], "iteration": iteration}

    config = state.get("config") or {}
    max_iterations = int(config.get("max_iterations") or 1)
    attempts = state.get("attempts") or {}
    rerun_used = int((attempts.get("reviewer_rerun") or {}).get(str(iteration), 0))
    mediator_exists = (work_dir / review.mediator_path(iteration)).is_file()

    decision = decide(
        iteration=iteration,
        record=record,
        previous=review.iteration_record(state, iteration - 1),
        max_iterations=max_iterations,
        reviewer_rerun_used=rerun_used,
        mediator_exists=mediator_exists,
    )

    banners = [decision["banner"]] if decision.get("banner") else []
    if decision["branch"] in LENGTH_OVERFLOW_BRANCHES and length_overflow(
        work_dir, state.get("current_draft_sha")
    ):
        decision = apply_length_overflow(decision, iteration)
        banners.append(("length_overflow_recommendation", {}))

    published: dict | None = None
    mediator_published: dict | None = None
    draft_row: dict | None = None
    seed_relative: str | None = None
    next_step = getattr(args, "next_step", None) or f"{getattr(args, 'step', None) or 's'}-w{iteration + 1}"

    if decision["next"] == NEXT_WRITER and not mediator_exists:
        # §4.5 п.4.6: no mediator agent is needed, so the CLI assembles the instructions itself.
        document = review.build_mediator(record)
        from . import schema  # noqa: PLC0415 - local import keeps `jsonschema` optional at import time

        schema.validate_or_raise(document, "mediator")
        mediator_published = review.publish_result(
            work_dir,
            args,
            review.mediator_path(iteration),
            state_io.dumps(document).encode("utf-8"),
        )

    if decision["next"] == NEXT_WRITER:
        source = work_dir / (state.get("current_draft_path") or draft_path(iteration))
        if not source.is_file():
            return {"errors": [f"missing_draft: {source.name}"], "iteration": iteration}
        payload = source.read_bytes()
        version = decision["preseed_version"]
        # §3.1: the seed lives in the *writer's* attempt workspace and is published as drafts/vN+1.md.
        seed = stepctx.ensure_step_dir(work_dir, next_step, 1, "writer") / f"v{version}.md"
        state_io.write_bytes_atomic(seed, payload)
        seed_relative = stepctx.rel_path(work_dir, seed)
        published = stepctx.publish_file(
            work_dir, seed, draft_path(version), by="command", step_id=next_step
        )
        draft_row = _draft_version_row(version, published["sha256"])

    def mutator(current: dict) -> None:
        if decision["branch"] == 1:
            rerun = current.setdefault("attempts", {}).setdefault("reviewer_rerun", {})
            rerun[str(iteration)] = rerun_used + 1
            return
        if decision["final_status"]:
            current["final_status"] = decision["final_status"]
        for reason in decision["reasons"]:
            if reason not in current.setdefault("final_status_reasons", []):
                current["final_status_reasons"].append(reason)
        for condition_key, params in banners:
            review.record_banner(current, condition_key, **params)
        if decision.get("regression_to"):
            version = decision["regression_to"]
            current["current_draft_path"] = draft_path(version)
            for row in current.get("draft_versions", []):
                if row.get("version") == version:
                    current["current_draft_sha"] = row.get("sha256")
                    break
        if decision["next"] == NEXT_CLIENT_READINESS:
            current["remaining_blocking_issues"] = _blockers(record)
        if draft_row is not None and published is not None:
            _record_draft_version(current, draft_row)
            current["current_iteration"] = draft_row["version"]
            current["current_draft_path"] = draft_row["path"]
            current["current_draft_sha"] = draft_row["sha256"]

    result = {
        "next": decision["next"],
        "branch": decision["branch"],
        "iteration": iteration,
        "final_status": decision["final_status"],
        "reasons": decision["reasons"],
        "substance_blockers": record.get("substance_blockers"),
        "form_blockers": record.get("form_blockers"),
        "coverage": sorted(record.get("coverage") or []),
        "failed_reviewers": sorted(record.get("failed_reviewers") or []),
        "mediator_needed": decision.get("mediator_needed", False),
        "length_overflow": bool(decision.get("length_overflow")),
    }
    if decision.get("rerun_reviewers"):
        result["rerun_reviewers"] = decision["rerun_reviewers"]
    if decision.get("regression_to"):
        result["regression_to"] = decision["regression_to"]
        result["current_draft_path"] = draft_path(decision["regression_to"])
    if mediator_published is not None:
        result["mediator_path"] = mediator_published["canonical_path"]
        result["mediator_by"] = "cli"
    if draft_row is not None:
        result["current_iteration"] = draft_row["version"]
        result["draft_path"] = draft_row["path"]
        result["draft_sha"] = draft_row["sha256"]
        result["writer_work_path"] = seed_relative
        result["next_step"] = next_step
    entries = [entry for entry in (mediator_published, published) if entry is not None]
    stepctx.close_step(
        work_dir, args.step, args.attempt, result, args_key=args_key, published=entries, mutate=mutator
    )
    return result


def register(subparsers) -> None:
    """Register the `revision` command group."""
    from . import cli

    group = cli.group_subparsers(subparsers, "revision", "revision loop routing (§4.5 п.4)")

    parser = group.add_parser("next", help="pick branch 1..8 of the revision loop")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--iteration", type=int, default=None)
    parser.add_argument("--step", required=True)  # D-40: identity is checked, never skipped
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument(
        "--next-step",
        dest="next_step",
        default=None,
        help="step id of the writer dispatch that receives the pre-seeded draft (§3.1)",
    )
    parser.set_defaults(func=run_next)
