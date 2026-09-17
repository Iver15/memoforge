"""Tests for scripts/memoforge/revision.py — branches 1..8 of the revision loop (ТЗ §4.5 п.4, §9)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import limits, modes, review, revision, schema, state_io, task  # noqa: E402

LINT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reviews"
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


def new_task(root: Path, *, mode: str = "full", iteration: int = 1, versions: int = 1) -> Path:
    work_dir = root / TASK_ID
    task.create_work_dir_tree(work_dir)
    config = modes.resolve_config(mode)
    state = task.build_initial_state(
        task_id=TASK_ID,
        user_query="Biometric data of minors under the GDPR",
        language="en",
        work_dir=work_dir,
        output_folder=root,
        config=config,
    )
    state["mode"] = mode
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
            "8 in Brief: one iteration only",
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

    def test_brief_revises_once_before_the_loop_can_exit(self):
        # D-115: Brief runs two iterations, so the first round of findings reaches the writer
        # (branch 6) instead of forcing the exit (branch 8), and branch 3 becomes reachable.
        self.assertEqual(2, modes.MODES["brief"]["max_iterations"])
        decision = revision.decide(
            iteration=1,
            record=record(issues=(GROUNDED,)),
            previous=None,
            max_iterations=modes.MODES["brief"]["max_iterations"],
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
            max_iterations=modes.MODES["brief"]["max_iterations"],
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
            ("another citations category", (MISSING_TOKEN, CITATION_OTHER)),
            ("a deterministic blocker joins them", (MISSING_TOKEN, GROUNDED)),
            ("three missing tokens", (missing_token("s-1"), missing_token("s-2"), missing_token("s-3"))),
            ("a form blocker is still open", (MISSING_TOKEN, FORM_BLOCKER)),
        )
        for label, issues in cases:
            with self.subTest(case=label):
                decision = self.decide(2, issues=issues)
                self.assertEqual(8, decision["branch"])
                self.assertEqual("forced_exit_on_v2_with_remaining_issues", decision["final_status"])

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


class LengthOverflowTest(unittest.TestCase):
    """§2.1: an L-10 word cap surviving phase 13 forces manual review (D-21)."""

    def put_lint(self, work_dir: Path, name: str, draft_sha: str | None = None) -> None:
        report = json.loads((LINT_FIXTURES / f"{name}.json").read_text(encoding="utf-8-sig"))
        if draft_sha:
            report["draft_sha"] = draft_sha
        state_io.write_json_atomic(work_dir / "lint.json", report)

    def test_length_overflow_overrides_approved_in_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), mode="brief")
            set_iterations(work_dir, record(reviewers=("logic", "citations", "counterarguments")))
            self.put_lint(work_dir, "lint-length-overflow")

            result = revision.run_next(next_args(work_dir))
            self.assertEqual(4, result["branch"])  # the reviewers themselves were clean
            self.assertTrue(result["length_overflow"])
            self.assertEqual("manual_review_required_on_v1", result["final_status"])
            self.assertIn(revision.REASON_LENGTH_OVERFLOW, result["reasons"])

            state = state_io.read_state(work_dir)
            self.assertEqual("manual_review_required_on_v1", state["final_status"])
            self.assertEqual([revision.REASON_LENGTH_OVERFLOW], state["final_status_reasons"])
            self.assertIn(
                "length_overflow", [banner["banner_id"] for banner in state["fallback_banners"]]
            )

    def test_length_overflow_overrides_every_loop_exit_branch(self):
        # D-44: branches 2, 4, 5, 7 and 8 all leave the loop for `client_readiness`.
        # D-115: both modes now run two iterations, so branch 8 needs the last one.
        cases = (
            (2, 1, {"failed": ("form",)}, "brief", "manual_review_required_on_v1", limits.MAX_REVIEWER_RERUN),
            (4, 1, {}, "brief", "approved_on_v1", 0),
            (5, 1, {"issues": (FORM_BLOCKER,)}, "brief", "accepted_early_on_v1", 0),
            (7, 1, {"issues": (UNGROUNDED,)}, "full", "accepted_early_on_v1", 0),  # needs an iteration left
            (8, 2, {"issues": (GROUNDED,)}, "brief", "forced_exit_on_v2_with_remaining_issues", 0),
        )
        for branch, iteration, kwargs, mode, without_overflow, rerun_used in cases:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp), mode=mode, iteration=iteration, versions=iteration)
                if rerun_used:
                    state_io.write_state(
                        work_dir,
                        lambda state: state["attempts"].__setitem__("reviewer_rerun", {"1": rerun_used}),
                    )
                set_iterations(work_dir, record(iteration=iteration, **kwargs))
                self.assertEqual(
                    without_overflow,
                    revision.decide(
                        iteration=iteration,
                        record=record(iteration=iteration, **kwargs),
                        previous=None,
                        max_iterations=modes.MODES[mode]["max_iterations"],
                        reviewer_rerun_used=rerun_used,
                        mediator_exists=False,
                    )["final_status"],
                )
                self.put_lint(work_dir, "lint-length-overflow")
                result = revision.run_next(next_args(work_dir, iteration=iteration))
                self.assertEqual(branch, result["branch"])
                self.assertTrue(result["length_overflow"])
                self.assertEqual(f"manual_review_required_on_v{iteration}", result["final_status"])
                self.assertIn(revision.REASON_LENGTH_OVERFLOW, result["reasons"])
                reasons = state_io.read_state(work_dir)["final_status_reasons"]
                self.assertIn(revision.REASON_LENGTH_OVERFLOW, reasons)
                if branch == 2:
                    self.assertIn(revision.REASON_INCOMPLETE_REVIEW, reasons, "the branch reason survives")

    def test_clean_or_stale_lint_leaves_the_verdict_alone(self):
        for name, sha in (("lint-clean", None), ("lint-length-overflow", "b" * 64)):
            with self.subTest(report=name), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp), mode="brief")
                set_iterations(work_dir, record(reviewers=("logic",)))
                self.put_lint(work_dir, name, draft_sha=sha)
                result = revision.run_next(next_args(work_dir))
                self.assertFalse(result["length_overflow"])
                self.assertEqual("approved_on_v1", result["final_status"])

    def test_overflow_does_not_touch_a_continuing_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))  # Full: iteration 1 of 2 keeps going
            set_iterations(work_dir, record(issues=(GROUNDED,)))
            self.put_lint(work_dir, "lint-length-overflow")
            result = revision.run_next(next_args(work_dir))
            self.assertEqual(revision.NEXT_WRITER, result["next"])
            self.assertFalse(result["length_overflow"])
            self.assertIsNone(result["final_status"])
            self.assertEqual([], state_io.read_state(work_dir)["final_status_reasons"])


if __name__ == "__main__":
    unittest.main()
