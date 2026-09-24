"""Tests for scripts/memoforge/revision.py — branches 1..8 of the revision loop (ТЗ §4.5 п.4, §9)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import (  # noqa: E402
    events,
    limits,
    machine,
    modes,
    render,
    review,
    revision,
    schema,
    state_io,
    stepctx,
    task,
)

TASK_ID = "memo-20260908T120000Z-fixture"
DRAFT_SHA = "9b31b63e286f3517c59962ed8716a3bf7421ed25d719eb7b438f005b7ca0542e"


def issue(
    source: str,
    *,
    severity: str = "blocker",
    section_id: str = "s-4-1",
    category: str = "missing_application",
    checklist_id: str | None = None,
    issue_category: str | None = None,
    text: str = "The balancing test is stated but never applied.",
) -> dict:
    payload = {
        "severity": severity,
        "category": category,
        "section_id": section_id,
        "issue": text,
        "suggestion": "Apply the test to the facts.",
    }
    if checklist_id:
        payload["checklist_id"] = checklist_id
    if issue_category:
        payload["issue_category"] = issue_category
    return review._normalize_issue(payload, source)


def record(
    iteration: int = 1,
    *,
    reviewers: tuple[str, ...] = ("logic", "form", "citations", "counterarguments"),
    coverage: tuple[str, ...] | None = None,
    failed: tuple[str, ...] = (),
    issues: tuple[dict, ...] = (),
    conflict: bool = False,
) -> dict:
    coverage = tuple(sorted(coverage if coverage is not None else [r for r in reviewers if r not in failed]))
    blockers = [row for row in issues if row["severity"] == "blocker"]
    return {
        "iteration": iteration,
        "draft_sha": DRAFT_SHA,
        "reviewers": list(reviewers),
        "coverage": list(coverage),
        "failed_reviewers": sorted(failed),
        "substance_blockers": len([row for row in blockers if row["tier"] == "substance"]),
        "form_blockers": len([row for row in blockers if row["tier"] == "form"]),
        "deterministic_blockers": len(
            [row for row in blockers if review.DETERMINISTIC in row["provenance"]]
        ),
        "pass_ratio": 1.0,
        "conflict": conflict,
        "stale_reports": [],
        "issues": list(issues),
        "aggregated_at": "2026-09-08T12:00:00.000Z",
    }


GROUNDED = issue("deterministic")
GROUNDED_LOGIC = issue("logic", checklist_id="LOG-02")
UNGROUNDED = issue("counterarguments", checklist_id="CTR-05", category="overconfidence")
FORM_BLOCKER = issue("form", category="headings", section_id="s-2")
MAJOR_LOGIC = issue("logic", severity="major", category="ordering", section_id="s-3")
MAJOR_CITATIONS = issue("citations", severity="major", category="pinpoint", section_id="s-5")


def missing_token(section_id: str = "s-4-2") -> dict:
    """D-165: the blocker of the 2026-09-16 run — a rule statement without a `[[src:]]` token."""
    return issue(
        "citations",
        category="unsupported_claim",
        section_id=section_id,
        issue_category="unsupported_claim",
        text=f"The rule statement in {section_id} carries no [[src:]] token.",
    )


MISSING_TOKEN = missing_token()
CITATION_OTHER = issue(
    "citations",
    category="source_drift",
    section_id="s-4-3",
    issue_category="source_drift",
    text="The pinpoint of the cited article moved.",
)
PACK_MISMATCH = issue(
    "citations",
    category="source_pack_mismatch",
    section_id="s-4-4",
    issue_category="source_pack_mismatch",
    text="The cited guidance is used against its pack role.",
)


def new_task(root: Path, *, iteration: int = 1, versions: int = 1) -> Path:
    work_dir = root / TASK_ID
    task.create_work_dir_tree(work_dir)
    config = modes.resolve_config("full")
    state = task.build_initial_state(
        task_id=TASK_ID,
        user_query="Biometric data of minors under the GDPR",
        language="en",
        work_dir=work_dir,
        output_folder=root,
        config=config,
    )
    state["current_phase"] = "revision_loop"
    state["current_iteration"] = iteration
    state["current_draft_path"] = f"drafts/v{iteration}.md"
    state["current_draft_sha"] = DRAFT_SHA
    state_io.create_state(work_dir, state)
    for version in range(1, versions + 1):
        path = work_dir / f"drafts/v{version}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Memo v{version}\n\n## 1. Executive summary\n", encoding="utf-8")
    return work_dir


def set_iterations(work_dir: Path, *records: dict, **state_fields) -> None:
    def mutator(state: dict) -> None:
        state["iterations"] = list(records)
        for key, value in state_fields.items():
            state[key] = value
        for version in sorted({row["iteration"] for row in records}):
            path = work_dir / f"drafts/v{version}.md"
            if path.is_file():
                revision._record_draft_version(
                    state,
                    {
                        "version": version,
                        "path": f"drafts/v{version}.md",
                        "sha256": state_io.sha256_file(path),
                        "lint_clean": True,
                        "citations_clean": True,
                        "checked_at": "2026-09-08T12:00:00.000Z",
                    },
                )

    state_io.write_state(work_dir, mutator)


def issue_step(work_dir: Path, step_id: str, attempt: int = 1) -> None:
    """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

    def mutator(state: dict) -> None:
        rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
        if any(row.get("step_id") == step_id for row in rows):
            return
        rows.append(
            {
                "step_id": step_id,
                "kind": "script",
                "phase": state.get("current_phase"),
                "attempt": attempt,
                "reason": "initial",
                "issued_at": "2026-09-08T12:00:00.000Z",
                "status": None,
            }
        )
        state["steps"] = rows

    state_io.write_state(work_dir, mutator)


def next_args(work_dir: Path, iteration: int = 1, step: str = "s-200") -> argparse.Namespace:
    issue_step(work_dir, step)
    return argparse.Namespace(
        workdir=str(work_dir), iteration=iteration, step=step, attempt=1, next_step=None, human=False
    )


class BranchTableTest(unittest.TestCase):
    """§4.5 п.4 — the eight branches, in order, «first matching branch wins»."""

    CASES = (
        # (label, iteration, record kwargs, rerun_used, mediator_exists, max_iterations, branch, next, status)
        (
            "1 failed reviewer with rerun budget",
            1,
            {"failed": ("form",)},
            0,
            False,
            2,
            1,
            revision.NEXT_RERUN,
            None,
        ),
        (
            "2 failed reviewer, budget spent",
            1,
            {"failed": ("form",)},
            1,
            False,
            2,
            2,
            revision.NEXT_CLIENT_READINESS,
            "manual_review_required_on_v1",
        ),
        (
            "2 beats 4: no blockers but the review is incomplete",
            1,
            {"failed": ("form",), "issues": ()},
            1,
            False,
            2,
            2,
            revision.NEXT_CLIENT_READINESS,
            "manual_review_required_on_v1",
        ),
        (
            "4 clean",
            1,
            {},
            0,
            False,
            2,
            4,
            revision.NEXT_CLIENT_READINESS,
            "approved_on_v1",
        ),
        (
            "5 form blockers only",
            1,
            {"issues": (FORM_BLOCKER,)},
            0,
            False,
            2,
            5,
            revision.NEXT_CLIENT_READINESS,
            "accepted_early_on_v1",
        ),
        (
            "6 grounded blocker, mediator not needed",
            1,
            {"issues": (GROUNDED,)},
            0,
            False,
            2,
            6,
            revision.NEXT_WRITER,
            None,
        ),
        (
            "6 grounded blockers from two reviewers -> mediator",
            1,
            {"issues": (GROUNDED, GROUNDED_LOGIC)},
            0,
            False,
            2,
            6,
            revision.NEXT_MEDIATOR,
            None,
        ),
        (
            "6 mediator already produced -> writer",
            1,
            {"issues": (GROUNDED, GROUNDED_LOGIC)},
            0,
            True,
            2,
            6,
            revision.NEXT_WRITER,
            None,
        ),
        (
            "7 blockers but none grounded",
            1,
            {"issues": (UNGROUNDED,)},
            0,
            False,
            2,
            7,
            revision.NEXT_CLIENT_READINESS,
            "accepted_early_on_v1",
        ),
        (
            "8 last iteration with blockers",
            2,
            {"iteration": 2, "issues": (GROUNDED,)},
            0,
            False,
            2,
            8,
            revision.NEXT_CLIENT_READINESS,
            "forced_exit_on_v2_with_remaining_issues",
        ),
        (
            "4 beats 8: last iteration but clean",
            2,
            {"iteration": 2},
            0,
            False,
            2,
            4,
            revision.NEXT_CLIENT_READINESS,
            "approved_on_v2",
        ),
        (
            "8 when the cap is one iteration",
            1,
            {"issues": (GROUNDED,)},
            0,
            False,
            1,
            8,
            revision.NEXT_CLIENT_READINESS,
            "forced_exit_on_v1_with_remaining_issues",
        ),
    )

    def test_branch_order(self):
        for label, iteration, kwargs, rerun, mediator, maximum, branch, expected, status in self.CASES:
            with self.subTest(case=label):
                decision = revision.decide(
                    iteration=iteration,
                    record=record(**kwargs),
                    previous=None,
                    max_iterations=maximum,
                    reviewer_rerun_used=rerun,
                    mediator_exists=mediator,
                )
                self.assertEqual(branch, decision["branch"])
                self.assertEqual(expected, decision["next"])
                self.assertEqual(status, decision["final_status"])

    def test_all_reviewers_failed_is_not_approved(self):
        reviewers = ("logic", "form", "citations", "counterarguments")
        decision = revision.decide(
            iteration=1,
            record=record(reviewers=reviewers, failed=reviewers, coverage=()),
            previous=None,
            max_iterations=2,
            reviewer_rerun_used=limits.MAX_REVIEWER_RERUN,
            mediator_exists=False,
        )
        self.assertEqual(2, decision["branch"])
        self.assertEqual("manual_review_required_on_v1", decision["final_status"])
        self.assertEqual([revision.REASON_ALL_REVIEWERS_FAILED], decision["reasons"])
        self.assertNotIn("approved", decision["final_status"])

    def test_one_failed_substance_reviewer_never_yields_approved(self):
        for iteration in (1, 2):
            with self.subTest(iteration=iteration):
                decision = revision.decide(
                    iteration=iteration,
                    record=record(iteration=iteration, failed=("logic",)),
                    previous=record(iteration=iteration - 1) if iteration == 2 else None,
                    max_iterations=2,
                    reviewer_rerun_used=limits.MAX_REVIEWER_RERUN,
                    mediator_exists=False,
                )
                self.assertEqual(2, decision["branch"])
                self.assertEqual(f"manual_review_required_on_v{iteration}", decision["final_status"])
                self.assertEqual([revision.REASON_INCOMPLETE_REVIEW], decision["reasons"])


class RegressionTest(unittest.TestCase):
    """§4.5 п.4.3 — regression needs equal coverage and a new grounded substance blocker."""

    def _previous(self) -> dict:
        return record(iteration=1, issues=(GROUNDED,))

    def _regressed(self, **kwargs) -> dict:
        new = issue("deterministic", section_id="s-6", category="C-02", text="Quote no longer matches raw.")
        return record(iteration=2, issues=(GROUNDED, new), **kwargs)

    def test_regression_on_equal_coverage_and_a_new_grounded_blocker(self):
        decision = revision.decide(
            iteration=2,
            record=self._regressed(),
            previous=self._previous(),
            max_iterations=2,
            reviewer_rerun_used=0,
            mediator_exists=False,
        )
        self.assertEqual(3, decision["branch"])
        self.assertEqual("forced_exit_on_v1_with_remaining_issues", decision["final_status"])
        self.assertEqual(1, decision["regression_to"])
        self.assertEqual([revision.REASON_REGRESSION], decision["reasons"])

    def test_no_regression_when_coverage_differs(self):
        decision = revision.decide(
            iteration=2,
            record=self._regressed(failed=("form",)),
            previous=self._previous(),
            max_iterations=2,
            reviewer_rerun_used=limits.MAX_REVIEWER_RERUN,
            mediator_exists=False,
        )
        self.assertNotEqual(3, decision["branch"])

    def test_no_regression_without_a_new_grounded_blocker(self):
        # More blockers, but they repeat the (section_id, category) pairs of iteration 1.
        repeated = record(iteration=2, issues=(GROUNDED, issue("citations", section_id="s-4-1")))
        decision = revision.decide(
            iteration=2,
            record=repeated,
            previous=self._previous(),
            max_iterations=2,
            reviewer_rerun_used=0,
            mediator_exists=False,
        )
        self.assertNotEqual(3, decision["branch"])
        self.assertEqual(8, decision["branch"])

    def test_the_run_revises_once_before_the_loop_can_exit(self):
        # D-115: a run has two iterations, so the first round of findings reaches the writer
        # (branch 6) instead of forcing the exit (branch 8), and branch 3 becomes reachable.
        self.assertEqual(2, modes.MODES["full"]["max_iterations"])
        decision = revision.decide(
            iteration=1,
            record=record(issues=(GROUNDED,)),
            previous=None,
            max_iterations=modes.MODES["full"]["max_iterations"],
            reviewer_rerun_used=0,
            mediator_exists=False,
        )
        self.assertEqual(6, decision["branch"])
        self.assertEqual(revision.NEXT_WRITER, decision["next"])
        self.assertEqual(2, decision["preseed_version"])
        self.assertIsNone(decision["final_status"])
        regressed = revision.decide(
            iteration=2,
            record=self._regressed(),
            previous=self._previous(),
            max_iterations=modes.MODES["full"]["max_iterations"],
            reviewer_rerun_used=0,
            mediator_exists=False,
        )
        self.assertEqual(3, regressed["branch"])

    def test_regression_moves_the_current_draft_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, self._previous(), self._regressed())
            result = revision.run_next(next_args(work_dir, iteration=2))
            self.assertEqual(3, result["branch"])
            state = state_io.read_state(work_dir)
            self.assertEqual("drafts/v1.md", state["current_draft_path"])
            self.assertEqual(
                state_io.sha256_file(work_dir / "drafts/v1.md"), state["current_draft_sha"]
            )
            self.assertIn(revision.REASON_REGRESSION, state["final_status_reasons"])


class RunNextTest(unittest.TestCase):
    def test_preseeded_draft_is_published_and_iteration_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            set_iterations(work_dir, record(issues=(GROUNDED,)))
            result = revision.run_next(next_args(work_dir))

            self.assertEqual(revision.NEXT_WRITER, result["next"])
            self.assertEqual(2, result["current_iteration"])
            self.assertEqual("drafts/v2.md", result["draft_path"])
            seed = work_dir / result["writer_work_path"]
            self.assertTrue(seed.is_file())
            self.assertTrue(seed.as_posix().endswith(f"steps/{result['next_step']}/a1/writer/v2.md"))
            canonical = work_dir / "drafts/v2.md"
            self.assertTrue(canonical.is_file())
            self.assertEqual(
                (work_dir / "drafts/v1.md").read_bytes(), canonical.read_bytes()
            )

            state = state_io.read_state(work_dir)
            self.assertEqual(2, state["current_iteration"])
            self.assertEqual("drafts/v2.md", state["current_draft_path"])
            self.assertEqual(state_io.sha256_file(canonical), state["current_draft_sha"])
            published = [row for row in state["published"] if row["canonical_path"] == "drafts/v2.md"]
            self.assertEqual(1, len(published))
            self.assertEqual(state_io.sha256_file(canonical), published[0]["sha256"])
            version = [row for row in state["draft_versions"] if row["version"] == 2]
            self.assertEqual(1, len(version))
            self.assertFalse(version[0]["lint_clean"])
            self.assertFalse(version[0]["citations_clean"])

    def test_cli_builds_the_mediator_file_when_no_mediator_is_dispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            set_iterations(work_dir, record(issues=(GROUNDED,)))
            result = revision.run_next(next_args(work_dir))
            self.assertEqual("reviews/v1-mediator.json", result["mediator_path"])
            document = state_io.read_json(work_dir / result["mediator_path"])
            self.assertEqual([], schema.validate(document, "mediator"))
            self.assertEqual(1, len(document["instructions"]))

    def test_mediator_is_requested_before_the_writer_when_two_reviewers_disagree(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            set_iterations(work_dir, record(issues=(GROUNDED, MAJOR_LOGIC, MAJOR_CITATIONS)))
            first = revision.run_next(next_args(work_dir, step="s-200"))
            self.assertEqual(revision.NEXT_MEDIATOR, first["next"])
            self.assertTrue(first["mediator_needed"])
            self.assertFalse((work_dir / "drafts/v2.md").exists())
            self.assertEqual(1, state_io.read_state(work_dir)["current_iteration"])

            # The mediator agent published its file; the same branch now pre-seeds and dispatches.
            state_io.write_json_atomic(
                work_dir / "reviews/v1-mediator.json", {"instructions": [], "dropped": []}
            )
            second = revision.run_next(next_args(work_dir, step="s-201"))
            self.assertEqual(revision.NEXT_WRITER, second["next"])
            self.assertEqual(2, state_io.read_state(work_dir)["current_iteration"])

    def test_mediator_counts_every_participant_of_a_merged_issue(self):
        # §4.5 п.3–4.6: a merged issue was raised by everyone in `provenance[]`, not only by the
        # reviewer whose copy happened to be first.
        merged = dict(
            issue("citations", category="source_drift", section_id="s-4-2"),
            provenance=["citations", "form"],
        )
        other = issue("citations", category="unsupported_claim", section_id="s-5")
        self.assertEqual({"citations", "form"}, revision._participants(merged))
        self.assertTrue(revision.mediator_needed(record(issues=(merged, other))))
        self.assertFalse(
            revision.mediator_needed(record(issues=(dict(merged, provenance=["citations"]), other))),
            "one reviewer raising two issues is still one reviewer",
        )

    def test_conflict_alone_requires_a_mediator(self):
        self.assertTrue(revision.mediator_needed(record(issues=(GROUNDED,), conflict=True)))
        self.assertFalse(revision.mediator_needed(record(issues=(GROUNDED,))))

    def test_final_status_reasons_accumulate(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=1, versions=2)

            def seed(state: dict) -> None:
                state["final_status_reasons"] = ["lint_not_converged"]
                state["attempts"]["reviewer_rerun"] = {"1": limits.MAX_REVIEWER_RERUN}

            state_io.write_state(work_dir, seed)
            set_iterations(work_dir, record(failed=("form",)))
            revision.run_next(next_args(work_dir, iteration=1, step="s-200"))
            state = state_io.read_state(work_dir)
            self.assertEqual(
                ["lint_not_converged", revision.REASON_INCOMPLETE_REVIEW], state["final_status_reasons"]
            )
            self.assertEqual("manual_review_required_on_v1", state["final_status"])

            set_iterations(
                work_dir,
                record(failed=("form",)),
                record(iteration=2, issues=(GROUNDED,)),
                current_iteration=2,
            )
            revision.run_next(next_args(work_dir, iteration=2, step="s-202"))
            state = state_io.read_state(work_dir)
            self.assertEqual(
                [
                    "lint_not_converged",
                    revision.REASON_INCOMPLETE_REVIEW,
                    revision.REASON_UNRESOLVED_BLOCKERS,
                ],
                state["final_status_reasons"],
            )
            self.assertEqual("forced_exit_on_v2_with_remaining_issues", state["final_status"])

    def test_rerun_branch_spends_the_reviewer_rerun_budget_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            set_iterations(work_dir, record(failed=("form",)))
            first = revision.run_next(next_args(work_dir, step="s-200"))
            self.assertEqual(revision.NEXT_RERUN, first["next"])
            self.assertEqual(["form"], first["rerun_reviewers"])
            state = state_io.read_state(work_dir)
            self.assertEqual(1, state["attempts"]["reviewer_rerun"]["1"])
            self.assertIsNone(state["final_status"])

            second = revision.run_next(next_args(work_dir, step="s-201"))
            self.assertEqual(2, second["branch"])
            self.assertEqual("manual_review_required_on_v1", second["final_status"])

    def test_remaining_blocking_issues_are_recorded_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, record(iteration=2, issues=(GROUNDED, MAJOR_LOGIC)))
            revision.run_next(next_args(work_dir, iteration=2))
            state = state_io.read_state(work_dir)
            self.assertEqual(1, len(state["remaining_blocking_issues"]))
            self.assertEqual("blocker", state["remaining_blocking_issues"][0]["severity"])

    def test_missing_aggregate_is_a_business_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            result = revision.run_next(next_args(work_dir))
            self.assertEqual(["no_aggregate_for_iteration: 1"], result["errors"])

    def test_repeat_of_a_closed_step_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            set_iterations(work_dir, record(issues=(GROUNDED,)))
            first = revision.run_next(next_args(work_dir))
            again = revision.run_next(next_args(work_dir))
            self.assertEqual(first, again)
            self.assertEqual(2, state_io.read_state(work_dir)["current_iteration"])


class TargetedFixTest(unittest.TestCase):
    """D-165: branch 9 — one targeted citation pass when only missing `[[src:]]` tokens are left."""

    @staticmethod
    def decide(iteration: int, *, targeted_fix_used: int = 0, max_iterations: int = 2, **kwargs) -> dict:
        return revision.decide(
            iteration=iteration,
            record=record(iteration=iteration, **kwargs),
            previous=None,
            max_iterations=max_iterations,
            reviewer_rerun_used=0,
            mediator_exists=False,
            targeted_fix_used=targeted_fix_used,
        )

    def test_branch_9_targets_a_single_unsupported_claim_at_the_end_of_the_budget(self):
        decision = self.decide(2, issues=(MISSING_TOKEN,))
        self.assertEqual(9, decision["branch"])
        self.assertEqual(revision.NEXT_WRITER, decision["next"])
        self.assertIsNone(decision["final_status"])
        self.assertTrue(decision["targeted"])
        self.assertEqual(["citations"], decision["reviewers"])
        self.assertEqual(3, decision["preseed_version"])
        self.assertFalse(decision["mediator_needed"])

    def test_branch_9_never_fires_twice(self):
        decision = self.decide(2, issues=(MISSING_TOKEN,), targeted_fix_used=1)
        self.assertEqual(8, decision["branch"])
        self.assertEqual("forced_exit_on_v2_with_remaining_issues", decision["final_status"])

    def test_branch_9_needs_only_citation_token_blockers(self):
        cases = (
            ("a logic blocker joins them", (MISSING_TOKEN, GROUNDED_LOGIC)),
            ("a pack-mismatch citations blocker", (MISSING_TOKEN, PACK_MISMATCH)),
            ("a deterministic blocker joins them", (MISSING_TOKEN, GROUNDED)),
            ("three missing tokens", (missing_token("s-1"), missing_token("s-2"), missing_token("s-3"))),
            ("a form blocker is still open", (MISSING_TOKEN, FORM_BLOCKER)),
        )
        for label, issues in cases:
            with self.subTest(case=label):
                decision = self.decide(2, issues=issues)
                self.assertEqual(8, decision["branch"])
                self.assertEqual("forced_exit_on_v2_with_remaining_issues", decision["final_status"])

    def test_branch_9_takes_an_unsupported_claim_and_a_source_drift_together(self):
        # D-212, run of 2026-09-19: one `source_drift` blocker used to shut branch 9 for the other one.
        decision = self.decide(2, issues=(MISSING_TOKEN, CITATION_OTHER))
        self.assertEqual(9, decision["branch"])
        self.assertTrue(decision["targeted"])
        self.assertIsNone(decision["final_status"])

    def test_a_source_pack_mismatch_blocker_still_closes_the_loop(self):
        decision = self.decide(2, issues=(PACK_MISMATCH,))
        self.assertEqual(8, decision["branch"])
        self.assertEqual("forced_exit_on_v2_with_remaining_issues", decision["final_status"])

    def test_targeted_blockers_are_the_citations_substance_blockers_of_the_targeted_categories(self):
        rebuilt = record(
            iteration=2,
            issues=(MISSING_TOKEN, CITATION_OTHER, PACK_MISMATCH, GROUNDED_LOGIC, MAJOR_CITATIONS, FORM_BLOCKER),
        )
        self.assertEqual(("unsupported_claim", "source_drift"), revision.TARGETED_FIX_CATEGORIES)
        self.assertEqual([MISSING_TOKEN, CITATION_OTHER], revision.targeted_blockers(rebuilt))
        merged = dict(
            issue("logic", checklist_id="LOG-02", issue_category="source_drift"), provenance=["logic", "citations"]
        )
        self.assertEqual([merged], revision.targeted_blockers(record(iteration=2, issues=(merged,))))

    def test_branch_9_does_not_pre_empt_a_normal_iteration(self):
        decision = self.decide(1, issues=(MISSING_TOKEN,))
        self.assertEqual(6, decision["branch"])
        self.assertEqual(revision.NEXT_WRITER, decision["next"])
        self.assertFalse(decision.get("targeted"))

    def test_the_targeted_pass_is_recorded_once_in_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, record(iteration=2, issues=(MISSING_TOKEN,)))
            result = revision.run_next(next_args(work_dir, iteration=2))

            self.assertEqual(9, result["branch"])
            self.assertTrue(result["targeted"])
            self.assertEqual(3, result["current_iteration"])
            self.assertEqual("drafts/v3.md", result["draft_path"])
            self.assertEqual("reviews/v2-mediator.json", result["mediator_path"])

            state = state_io.read_state(work_dir)
            self.assertEqual(1, state["attempts"]["targeted_fix"])
            self.assertEqual({"iteration": 3, "reviewers": ["citations"]}, state["targeted_fix"])
            self.assertEqual(3, state["current_iteration"])
            self.assertEqual("drafts/v3.md", state["current_draft_path"])
            self.assertIsNone(state["final_status"])

            # The budget is spent: the same blocker at v3 leaves the loop through branch 8.
            set_iterations(
                work_dir,
                record(iteration=2, issues=(MISSING_TOKEN,)),
                record(iteration=3, reviewers=("citations",), issues=(MISSING_TOKEN,)),
            )
            again = revision.run_next(next_args(work_dir, iteration=3, step="s-201"))
            self.assertEqual(8, again["branch"])
            self.assertEqual("forced_exit_on_v3_with_remaining_issues", again["final_status"])


RUN_20260921 = Path(__file__).resolve().parent / "fixtures" / "run-20260921"
"""D-208: the saved reviews and `evidence/state-iterations.json` of the 2026-09-21 run."""


def run_saved() -> dict:
    """`evidence/state-iterations.json` of the 2026-09-21 run: its `iterations[]` and `config_max_iterations`."""
    return json.loads((RUN_20260921 / "run-20260921-state-iterations.json").read_text(encoding="utf-8-sig"))


def run_iteration(number: int) -> dict:
    """One `iterations[]` row of the 2026-09-21 run as the gate workspace saved it."""
    return next(row for row in run_saved()["iterations"] if row["iteration"] == number)


def run_issues(number: int) -> tuple[dict, ...]:
    """The open issues of one run iteration, normalised the way `decide()` reads them.

    The saved rows keep severity, category, section, reviewer and checklist id; the `issue_category`
    of a `citations` row is the one the run's own citations review of that version gave it.
    """
    labels: dict[tuple[str, str], str] = {}
    review_path = RUN_20260921 / f"run-20260921-v{number}-citations.json"
    if review_path.is_file():
        for row in json.loads(review_path.read_text(encoding="utf-8-sig"))["issues"]:
            labels[(row["section_id"], row["category"])] = row["issue_category"]
    return tuple(
        issue(
            row["source_reviewer"],
            severity=row["severity"],
            section_id=row["section_id"],
            category=row["category"],
            checklist_id=row["checklist_id"],
            issue_category=labels.get((row["section_id"], row["category"])),
        )
        for row in run_iteration(number)["open_issues"]
    )


def run_record(number: int, *extra: dict) -> dict:
    """The run's iteration `number` rebuilt as an aggregate record, plus the `extra` issues."""
    saved = run_iteration(number)
    return record(iteration=number, reviewers=tuple(saved["reviewers"]), issues=run_issues(number) + extra)


def citations_blocker(section_id: str, issue_category: str, text: str) -> dict:
    return issue(
        "citations",
        category="unsupported_law",
        section_id=section_id,
        checklist_id="CIT-01",
        issue_category=issue_category,
        text=text,
    )


# D-208: the two errors of the 2026-09-21 run that no reviewer raised at v2.
S9_RULE = "The rule stated in s-9 is not in the source its token names."
S52_FORMULA = "The s-5-2 formula is written as the court's holding; the decision only recites it."


class RunOfSeptember21Test(unittest.TestCase):
    """D-208: `decide()` on the iterations of the 2026-09-21 run, with the blockers plan 72 adds."""

    @staticmethod
    def decide(rebuilt: dict, *, targeted_fix_used: int = 0) -> dict:
        return revision.decide(
            iteration=rebuilt["iteration"],
            record=rebuilt,
            previous=None,
            max_iterations=run_saved()["config_max_iterations"],
            reviewer_rerun_used=0,
            mediator_exists=False,
            targeted_fix_used=targeted_fix_used,
        )

    def test_the_rebuilt_records_keep_the_saved_blocker_counts(self):
        for number in (1, 2, 3):
            with self.subTest(iteration=number):
                saved = run_iteration(number)
                rebuilt = run_record(number)
                self.assertEqual(saved["substance_blockers"], rebuilt["substance_blockers"])
                self.assertEqual(saved["form_blockers"], rebuilt["form_blockers"])
        v2 = [row for row in run_issues(2) if row["severity"] == "blocker"]
        self.assertEqual([("citations", "s-5-2", "unsupported_claim")], [
            (row["source_reviewer"], row["section_id"], row.get("issue_category")) for row in v2
        ])

    def test_a_rule_on_a_source_that_lacks_it_joins_the_targeted_pass(self):
        # (a) rule 7 labels the s-9 error `unsupported_claim`, so v2 still takes branch 9.
        decision = self.decide(run_record(2, citations_blocker("s-9", "unsupported_claim", S9_RULE)))
        self.assertEqual(9, decision["branch"])
        self.assertEqual(revision.NEXT_WRITER, decision["next"])
        self.assertTrue(decision["targeted"])
        self.assertIsNone(decision["final_status"])

    def test_the_same_error_labelled_as_drift_joins_the_targeted_pass_too(self):
        # (b) D-212: `source_drift` is a targeted category now, so the label no longer closes the loop;
        # a `source_pack_mismatch` label still does.
        decision = self.decide(run_record(2, citations_blocker("s-9", "source_drift", S9_RULE)))
        self.assertEqual(9, decision["branch"])
        self.assertTrue(decision["targeted"])
        decision = self.decide(run_record(2, citations_blocker("s-9", "source_pack_mismatch", S9_RULE)))
        self.assertEqual(8, decision["branch"])
        self.assertEqual("forced_exit_on_v2_with_remaining_issues", decision["final_status"])

    def test_three_blockers_at_the_last_iteration_force_the_exit(self):
        # (c) the documented outcome: in the new system these errors are raised at v1 (Task 6).
        decision = self.decide(
            run_record(
                2,
                citations_blocker("s-5-2", "unsupported_claim", S52_FORMULA),
                citations_blocker("s-9", "unsupported_claim", S9_RULE),
            )
        )
        self.assertEqual(8, decision["branch"])
        self.assertEqual(revision.NEXT_CLIENT_READINESS, decision["next"])
        self.assertEqual("forced_exit_on_v2_with_remaining_issues", decision["final_status"])

    def test_the_targeted_record_with_one_major_is_approved(self):
        # (d) v3: citations only, one `major` and no blocker — branch 4 (Task 4 hands the major over).
        rebuilt = run_record(3)
        self.assertEqual(["citations"], rebuilt["reviewers"])
        self.assertEqual(["major"], [row["severity"] for row in rebuilt["issues"] if row["severity"] != "minor"])
        decision = self.decide(rebuilt, targeted_fix_used=1)
        self.assertEqual(4, decision["branch"])
        self.assertEqual("approved_on_v3", decision["final_status"])


RUN_20260922 = Path(__file__).resolve().parent / "fixtures" / "run-20260922"
"""D-212: `iterations[]` of the 2026-09-22 run and the `reviews/v2-mediator.json` it published."""
TARGETED_REASON = "targeted pass: only the named blockers are fixed"


def run_74_records() -> list[dict]:
    return json.loads((RUN_20260922 / "state-iterations.json").read_text(encoding="utf-8-sig"))


def render_args(work_dir: Path) -> argparse.Namespace:
    """`mf render mediator --iteration 2` without a step, the way a manual call renders the view."""
    return argparse.Namespace(
        workdir=str(work_dir),
        view="mediator",
        iteration=2,
        step=None,
        attempt=1,
        out=None,
        print_markdown=False,
        layer=None,
    )


class RunOfSeptember22Test(unittest.TestCase):
    """D-212: branch 9 of the 2026-09-22 run handed the writer 1 blocker + 4 majors; it gets the blocker only."""

    def test_the_targeted_mediator_names_the_blocker_and_drops_everything_else(self):
        records = run_74_records()
        v2 = records[1]
        self.assertEqual(2, v2["iteration"])
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, records[0], v2)
            result = revision.run_next(next_args(work_dir, iteration=2))

            self.assertEqual(9, result["branch"])
            self.assertTrue(result["targeted"])
            self.assertEqual("reviews/v2-mediator.json", result["mediator_path"])
            document = state_io.read_json(work_dir / result["mediator_path"])
            self.assertEqual([], schema.validate(document, "mediator"))

            published = json.loads((RUN_20260922 / "v2-mediator.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(5, len(published["instructions"]), "what the run published before D-212")
            self.assertEqual(
                [("s-5-4", "citations", "blocker")],
                [(row["section_id"], row["source_reviewer"], row["severity"]) for row in document["instructions"]],
            )
            self.assertEqual(published["instructions"][:1], document["instructions"])

            dropped = document["dropped"]
            self.assertEqual(13, len(dropped))
            self.assertEqual({TARGETED_REASON}, {row["reason"] for row in dropped})
            self.assertEqual(4, len([row for row in dropped if row["severity"] == "major"]))
            self.assertEqual(9, len([row for row in dropped if row["severity"] == "minor"]))
            others = sorted(
                (row["section_id"], row["category"], row["issue"])
                for row in v2["issues"]
                if row["severity"] != "blocker"
            )
            self.assertEqual(others, sorted((row["section_id"], row["category"], row["issue"]) for row in dropped))

    @staticmethod
    def _publish_full_mediator(work_dir: Path) -> None:
        """The supported way a full v2 mediator gets there first: `mf review mediator-from-issues` + `mf render`."""
        issue_step(work_dir, "s-150")
        review.run_mediator_from_issues(
            argparse.Namespace(workdir=str(work_dir), iteration=2, step="s-150", attempt=1, human=False)
        )
        render.run_render(render_args(work_dir))

    def test_targeted_pass_replaces_preexisting_full_mediator(self):
        records = run_74_records()
        v2 = records[1]
        canonical = review.mediator_path(2)
        instructions = machine.view_path(canonical)
        self.assertEqual("reviews/v2-mediator.md", instructions, "the view the writer edits from (D-62)")
        targeted = review.build_mediator(v2, only=revision.targeted_blockers(v2))
        self.assertEqual(["s-5-4"], [row["section_id"] for row in targeted["instructions"]])
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, records[0], v2)
            # One millisecond for everything: the full view and the replacement JSON tie on `at`, so
            # freshness can not be what makes the planner re-render.
            with mock.patch.object(events, "utc_now", return_value="2026-09-22T18:00:00.000Z"):
                self._publish_full_mediator(work_dir)
                self.assertEqual(review.build_mediator(v2), state_io.read_json(work_dir / canonical))
                self.assertTrue((work_dir / instructions).is_file())
                self.assertTrue(machine.views_fresh(state_io.read_state(work_dir), [canonical]))

                result = revision.run_next(next_args(work_dir, iteration=2))

                self.assertEqual(9, result["branch"])
                self.assertEqual(canonical, result["mediator_path"])
                state = state_io.read_state(work_dir)
                self.assertEqual(targeted, stepctx.read_published(work_dir, canonical, state=state))
                self.assertEqual(
                    state_io.sha256_bytes(state_io.dumps(targeted).encode("utf-8")),
                    stepctx.published_sha(state, canonical),
                )
                # The full view is gone, file and `published[]` row: the planner's `render_step` sees a
                # missing view and renders before the writer, whatever the timestamps say.
                self.assertFalse((work_dir / instructions).exists())
                self.assertIsNone(stepctx.published_entry(state, instructions))
                self.assertFalse(machine.views_fresh(state, [canonical]))
                render.run_render(render_args(work_dir))
            self.assertEqual(
                render.render_mediator(targeted), (work_dir / instructions).read_text(encoding="utf-8")
            )

    def test_missing_draft_is_refused_before_the_mediator_is_replaced(self):
        records = run_74_records()
        v2 = records[1]
        canonical = review.mediator_path(2)
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, records[0], v2)
            self._publish_full_mediator(work_dir)
            before = state_io.read_state(work_dir)
            full_bytes = (work_dir / canonical).read_bytes()
            view_bytes = (work_dir / "reviews/v2-mediator.md").read_bytes()
            (work_dir / "drafts/v2.md").unlink()

            result = revision.run_next(next_args(work_dir, iteration=2))

            self.assertEqual(["missing_draft: v2.md"], result["errors"])
            self.assertEqual(full_bytes, (work_dir / canonical).read_bytes())
            self.assertEqual(view_bytes, (work_dir / "reviews/v2-mediator.md").read_bytes())
            state = state_io.read_state(work_dir)
            self.assertEqual(before["published"], state["published"])
            self.assertEqual(review.build_mediator(v2), stepctx.read_published(work_dir, canonical, state=state))
            self.assertFalse((work_dir / "drafts/v3.md").exists())

    def test_targeted_pass_keeps_an_identical_published_mediator(self):
        records = run_74_records()
        v2 = records[1]
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=2, versions=2)
            set_iterations(work_dir, records[0], v2)
            targeted = review.build_mediator(v2, only=revision.targeted_blockers(v2))
            issue_step(work_dir, "s-150")
            entry = review.publish_result(
                work_dir,
                argparse.Namespace(step="s-150", attempt=1),
                review.mediator_path(2),
                state_io.dumps(targeted).encode("utf-8"),
            )
            state_io.write_state(work_dir, lambda state: stepctx.merge_published(state, [entry]))

            result = revision.run_next(next_args(work_dir, iteration=2))
            self.assertEqual(9, result["branch"])
            self.assertNotIn("mediator_path", result)
            state = state_io.read_state(work_dir)
            self.assertEqual(entry, stepctx.published_entry(state, review.mediator_path(2)))
            self.assertEqual(targeted, stepctx.read_published(work_dir, review.mediator_path(2), state=state))

    def test_branch_6_keeps_the_full_mediator(self):
        # A branch-6 iteration on a blocker and a major of one reviewer: the major is still an instruction.
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            major = issue("deterministic", severity="major", section_id="s-5", category="C-03")
            set_iterations(work_dir, record(issues=(GROUNDED, major)))
            result = revision.run_next(next_args(work_dir))
            self.assertEqual(6, result["branch"])
            document = state_io.read_json(work_dir / result["mediator_path"])
            self.assertEqual(2, len(document["instructions"]))
            self.assertEqual([], document["dropped"])


def run_74_exit() -> dict:
    """The state fields of the 2026-09-22 run at the end: the forced exit on v3 and its readiness step."""
    return json.loads((RUN_20260922 / "state-exit.json").read_text(encoding="utf-8-sig"))


class LastBlockerExitTest(unittest.TestCase):
    """D-213: a branch-8 exit whose blockers are branch-9-shaped, with the pass spent, lists them as rows."""

    def exit(self, records: list[dict], *, targeted_used: int = 1) -> tuple[dict, dict]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work_dir = new_task(Path(tmp.name), iteration=3, versions=3)
        set_iterations(work_dir, *records)
        state_io.write_state(
            work_dir, lambda state: state.setdefault("attempts", {}).update({"targeted_fix": targeted_used})
        )
        result = revision.run_next(next_args(work_dir, iteration=3))
        return result, state_io.read_state(work_dir)

    def test_the_run_74_exit_lists_the_last_blocker_after_the_three_majors(self):
        records = run_74_records()
        blocker = run_74_exit()["remaining_blocking_issues"][0]
        result, state = self.exit(records)

        self.assertEqual(8, result["branch"])
        self.assertEqual("forced_exit_on_v3_with_remaining_issues", state["final_status"])
        self.assertEqual(["unresolved_blockers"], state["final_status_reasons"])
        # A run that never reaches the settlement still delivers the blocker, exactly as today.
        self.assertEqual([blocker], state["remaining_blocking_issues"])
        rows = state["open_substance_majors"]
        self.assertEqual(
            [
                ("om-1", "logic", "s-4-2"),
                ("om-2", "counterarguments", "s-5-3"),
                ("om-3", "counterarguments", "s-6-1"),
                ("om-4", "citations", "s-4-2"),
            ],
            [(row["id"], row["class"], row["section_id"]) for row in rows],
        )
        for row in rows[:3]:
            self.assertNotIn("severity", row)
            self.assertNotIn("blocker_of", row)
        self.assertEqual(
            {
                "id": "om-4",
                "class": "citations",
                "reviewer": "citations",
                "section_id": "s-4-2",
                "category": "rule_not_in_source",
                "issue_category": "unsupported_claim",
                "issue": blocker["issue"],
                "issue_client": None,
                "suggestion": blocker["suggestion"],
                "from_iteration": 3,
                "origin": "loop",
                "status": "open",
                "severity": "blocker",
                "blocker_of": {"section_id": "s-4-2", "category": "rule_not_in_source", "issue": blocker["issue"]},
            },
            rows[3],
        )
        self.assertEqual([], schema.validate(state, "state"))

    def test_blocker_rows_number_from_start_in_record_order(self):
        records = run_74_records()
        second = dict(records[2]["issues"][0], section_id="s-2", issue="A second unsupported rule.")
        v3 = dict(records[2], issues=[*records[2]["issues"], second])
        rows = review.blocker_rows(v3, 7)
        self.assertEqual([("om-7", "s-4-2"), ("om-8", "s-2")], [(row["id"], row["section_id"]) for row in rows])
        self.assertEqual([], review.blocker_rows({"iteration": 2, "issues": []}, 1))
        logic = issue("logic", checklist_id="LOG-02")
        self.assertEqual([], review.blocker_rows({"iteration": 2, "issues": [logic]}, 1))

    def test_an_exit_the_targeted_pass_could_not_take_lists_no_blocker_row(self):
        records = run_74_records()
        v3 = records[2]
        pack = dict(v3["issues"][0], issue_category="source_pack_mismatch")
        logic = issue("logic", checklist_id="LOG-02", section_id="s-6-1", text="The conclusion does not follow.")
        cases = (
            ("a source_pack_mismatch blocker", [pack], 1),
            ("a logic blocker joins it", [v3["issues"][0], logic], 1),
        )
        for label, blockers, used in cases:
            with self.subTest(case=label):
                changed = dict(
                    v3,
                    issues=[*blockers, *(row for row in v3["issues"] if row["severity"] != "blocker")],
                    substance_blockers=len(blockers),
                )
                result, state = self.exit([records[0], records[1], changed], targeted_used=used)
                self.assertEqual(8, result["branch"])
                self.assertEqual(blockers, state["remaining_blocking_issues"])
                self.assertEqual(3, len(state["open_substance_majors"]))
                self.assertFalse(any("blocker_of" in row for row in state["open_substance_majors"]))


class OpenSubstanceMajorsExitTest(unittest.TestCase):
    """D-210: every exit to client readiness writes the open majors of the version it leaves on."""

    REGRESSED = issue("deterministic", section_id="s-6", category="C-02", text="Quote no longer matches raw.")
    V1_LOGIC = [(1, "logic", "s-3", "ordering")]

    CASES = (
        # (branch, iteration, records, reviewer_rerun used, rows written or None)
        (1, 1, (record(failed=("form",), issues=(MAJOR_LOGIC,)),), 0, None),
        (2, 1, (record(failed=("form",), issues=(MAJOR_LOGIC,)),), limits.MAX_REVIEWER_RERUN, V1_LOGIC),
        (
            3,
            2,
            (
                record(1, issues=(GROUNDED, MAJOR_LOGIC)),
                record(2, issues=(GROUNDED, REGRESSED, MAJOR_CITATIONS)),
            ),
            0,
            V1_LOGIC,
        ),
        (4, 1, (record(issues=(MAJOR_LOGIC,)),), 0, V1_LOGIC),
        (5, 1, (record(issues=(FORM_BLOCKER, MAJOR_LOGIC)),), 0, V1_LOGIC),
        (6, 1, (record(issues=(GROUNDED, MAJOR_LOGIC)),), 0, None),
        (7, 1, (record(issues=(UNGROUNDED, MAJOR_LOGIC)),), 0, V1_LOGIC),
        (8, 2, (record(2, issues=(GROUNDED, MAJOR_LOGIC)),), 0, [(2, "logic", "s-3", "ordering")]),
        (9, 2, (record(2, issues=(MISSING_TOKEN, MAJOR_LOGIC)),), 0, None),
    )

    def test_written_on_branches_2_3_4_5_7_8_and_not_on_1_6_9(self):
        for branch, iteration, records, rerun_used, expected in self.CASES:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp), iteration=iteration, versions=iteration)
                if rerun_used:
                    state_io.write_state(
                        work_dir,
                        lambda state: state["attempts"].__setitem__("reviewer_rerun", {"1": rerun_used}),
                    )
                set_iterations(work_dir, *records)
                result = revision.run_next(next_args(work_dir, iteration=iteration))
                self.assertEqual(branch, result["branch"])
                state = state_io.read_state(work_dir)
                if expected is None:
                    self.assertNotIn("open_substance_majors", state)
                    continue
                rows = state["open_substance_majors"]
                brief = [(row["from_iteration"], row["class"], row["section_id"], row["category"]) for row in rows]
                self.assertEqual(expected, brief)
                version = result.get("regression_to") or iteration
                self.assertEqual(review.open_substance_majors(state, version), rows)
                self.assertEqual([], schema.validate(state, "state"))


class NoLengthOverlayTest(unittest.TestCase):
    """D-243: the L-10 overlay went with the executive brief; a `lint.json` naming L-10 changes nothing."""

    def test_an_old_l10_finding_leaves_the_verdict_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            set_iterations(work_dir, record(reviewers=("logic", "citations", "counterarguments")))
            finding = {
                "rule": "L-10",
                "severity": "major",
                "line": 3,
                "section_id": "s-1",
                "excerpt": "## 1. Executive summary",
                "hint": "Brief is 1340 words; the cap is 1200.",
            }
            state_io.write_json_atomic(
                work_dir / "lint.json", {"draft_sha": DRAFT_SHA, "clean": True, "findings": [finding]}
            )
            result = revision.run_next(next_args(work_dir))
            self.assertEqual(4, result["branch"])
            self.assertEqual("approved_on_v1", result["final_status"])
            self.assertEqual([], result["reasons"])
            self.assertNotIn("length_overflow", result)
            state = state_io.read_state(work_dir)
            self.assertEqual("approved_on_v1", state["final_status"])
            self.assertEqual([], state["final_status_reasons"])
            self.assertEqual([], [row["banner_id"] for row in state.get("fallback_banners") or []])

    def test_the_overlay_names_are_gone(self):
        for name in (
            "REASON_LENGTH_OVERFLOW",
            "LENGTH_OVERFLOW_RULE",
            "LENGTH_OVERFLOW_BRANCHES",
            "length_overflow",
            "apply_length_overflow",
        ):
            self.assertFalse(hasattr(revision, name), name)


if __name__ == "__main__":
    unittest.main()
