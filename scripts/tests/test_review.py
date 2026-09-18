"""Tests for scripts/memoforge/review.py — validator and aggregation (ТЗ §4.5 п.2–3, §9)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402
from memoforge import i18n, modes, review, schema, state_io, stepctx, task  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reviews"
DRAFT_SHA = "9b31b63e286f3517c59962ed8716a3bf7421ed25d719eb7b438f005b7ca0542e"
TASK_ID = "memo-20260908T120000Z-fixture"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8-sig"))


def new_task(
    root: Path,
    *,
    mode: str = "full",
    reviewers: list[str] | None = None,
    iteration: int = 1,
    max_iterations: int = 2,
) -> Path:
    """A schema-valid v2 work dir parked in `revision_loop` (state via task/state_io, §9)."""
    work_dir = root / TASK_ID
    task.create_work_dir_tree(work_dir)
    config = modes.resolve_config(mode)
    if reviewers is not None:
        config["reviewer_list"] = list(reviewers)
    config["max_iterations"] = max_iterations
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
    (work_dir / "drafts").mkdir(parents=True, exist_ok=True)
    (work_dir / f"drafts/v{iteration}.md").write_text("# Memo\n\n## 1. Executive summary\n", encoding="utf-8")
    return work_dir


def put_review(work_dir: Path, iteration: int, kind: str, name: str) -> None:
    shutil.copyfile(FIXTURES / f"{name}.json", work_dir / review.review_path(iteration, kind))


def put_report(work_dir: Path, target: str, name: str) -> None:
    shutil.copyfile(FIXTURES / f"{name}.json", work_dir / target)


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


def publish(work_dir: Path, canonical: str) -> None:
    """Record the file at `canonical` in `published[]` with its current sha (§2.2)."""
    entry = {
        "canonical_path": canonical,
        "sha256": state_io.sha256_file(work_dir / canonical),
        "by": "step",
        "step_id": "s-100",
        "at": "2026-09-08T12:00:00.000Z",
    }
    state_io.write_state(work_dir, lambda state: stepctx.merge_published(state, [entry]))


def aggregate_args(work_dir: Path, iteration: int = 1, step: str = "s-101") -> argparse.Namespace:
    issue_step(work_dir, step)
    return argparse.Namespace(workdir=str(work_dir), iteration=iteration, step=step, attempt=1, human=False)


def mediator_args(work_dir: Path, iteration: int = 1, step: str = "s-103") -> argparse.Namespace:
    issue_step(work_dir, step)
    return argparse.Namespace(workdir=str(work_dir), iteration=iteration, step=step, attempt=1, human=False)


class ValidatorTest(unittest.TestCase):
    """§4.5 п.2 — checklist id set, hard_fail rules, downgrade, `approved <=> 0 blockers`."""

    def test_valid_review_is_accepted_unchanged(self):
        result = review.validate_document("logic", fixture("v1-logic-blocker"), current_draft_sha=DRAFT_SHA)
        self.assertEqual([], result["errors"])
        self.assertTrue(result["valid"])
        self.assertFalse(result["downgraded"])
        self.assertEqual(1, result["blockers"])

    def test_missing_blocker_for_hard_fail_is_rejected(self):
        document = fixture("v1-logic-blocker")
        document["issues"] = [issue for issue in document["issues"] if issue["severity"] != "blocker"]
        result = review.validate_document("logic", document)
        self.assertFalse(result["valid"])
        self.assertIn("missing_blocker_for_hard_fail: LOG-02", result["errors"])

    def test_duplicate_checklist_id_is_rejected(self):
        document = fixture("v1-logic")
        document["checklist"].append(dict(document["checklist"][0]))
        result = review.validate_document("logic", document)
        self.assertFalse(result["valid"])
        self.assertIn("duplicate_checklist_id: LOG-01", result["errors"])

    def test_unknown_checklist_id_is_rejected(self):
        document = fixture("v1-logic")
        document["checklist"][0]["id"] = "LOG-99"
        result = review.validate_document("logic", document)
        self.assertFalse(result["valid"])
        self.assertIn("unknown_checklist_id: LOG-99", result["errors"])
        self.assertIn("missing_checklist_id: LOG-01", result["errors"])

    def test_missing_checklist_id_is_rejected(self):
        document = fixture("v1-logic")
        document["checklist"] = document["checklist"][:-1]
        result = review.validate_document("logic", document)
        self.assertFalse(result["valid"])
        self.assertIn("missing_checklist_id: LOG-11", result["errors"])

    def test_all_unknown_downgrades_the_verdict_and_adds_blockers(self):
        result = review.validate_document("logic", fixture("v1-logic-all-unknown"))
        self.assertEqual([], result["errors"])
        self.assertTrue(result["downgraded"])
        self.assertEqual("needs_revision", result["document"]["verdict"])
        hard_fail = sorted(row["id"] for row in review.load_checklist("logic") if row["hard_fail"])
        added = sorted(
            issue["checklist_id"]
            for issue in result["document"]["issues"]
            if issue["category"] == review.UNVERIFIED_HARD_FAIL
        )
        self.assertEqual(hard_fail, added)
        self.assertTrue(all(issue["severity"] == "blocker" for issue in result["document"]["issues"]))
        # The corrected document is itself a valid `review` document and revalidating is idempotent.
        self.assertEqual([], schema.validate(result["document"], "review"))
        again = review.validate_document("logic", result["document"])
        self.assertEqual([], again["errors"])
        self.assertEqual(len(result["document"]["issues"]), len(again["document"]["issues"]))

    def test_unknown_hard_fail_blocks_approval_even_without_other_issues(self):
        document = fixture("v1-logic")
        document["checklist"][1]["pass"] = "unknown"  # LOG-02 is hard_fail
        result = review.validate_document("logic", document)
        self.assertEqual([], result["errors"])
        self.assertEqual("needs_revision", result["document"]["verdict"])
        self.assertEqual(1, result["blockers"])

    def test_valid_stub_is_accepted(self):
        result = review.validate_document("form", fixture("v1-form-stub"), current_draft_sha=DRAFT_SHA)
        self.assertEqual([], result["errors"])
        self.assertTrue(result["stub"])
        self.assertEqual(0, result["blockers"])

    def test_seven_issues_are_valid(self):
        document = fixture("v1-logic-blocker")
        template = document["issues"][0]
        document["issues"] = [
            {**template, "severity": "major", "section_id": f"s-4-{n}", "issue": f"Unapplied rule {n}."}
            for n in range(1, 8)
        ]
        document["checklist"] = [
            {"id": row["id"], "pass": True, "evidence": "graded"} for row in review.load_checklist("logic")
        ]
        result = review.validate_document("logic", document)
        self.assertEqual([], result["errors"])
        self.assertEqual(7, len(result["document"]["issues"]))

    def test_approved_with_blockers_is_rejected(self):
        document = fixture("v1-logic-blocker")
        document["verdict"] = "approved"
        result = review.validate_document("logic", document)
        self.assertFalse(result["valid"])
        self.assertIn("approved_with_blockers: 1", result["errors"])

    def test_stale_draft_sha_is_rejected(self):
        result = review.validate_document("logic", fixture("v1-logic"), current_draft_sha="a" * 64)
        self.assertFalse(result["valid"])
        self.assertTrue(any(error.startswith("stale_draft_sha") for error in result["errors"]))

    def test_reviewer_kind_mismatch_is_rejected(self):
        result = review.validate_document("form", fixture("v1-logic"))
        self.assertFalse(result["valid"])
        self.assertTrue(any(error.startswith("reviewer_kind_mismatch") for error in result["errors"]))

    def test_client_readiness_uses_the_same_id_set_check(self):
        # D-07: the delivery review is checked against lib/checklists/client-readiness.json.
        document = json.loads(
            (
                Path(__file__).resolve().parent / "fixtures" / "schemas" / "client-readiness" / "valid-1.json"
            ).read_text(encoding="utf-8-sig")
        )
        result = review.validate_document("client-readiness", document)
        self.assertFalse(result["valid"])
        self.assertIn("missing_checklist_id: CRD-04", result["errors"])

        document["checklist"] = [
            {"id": row["id"], "pass": True, "evidence": "graded"}
            for row in review.load_checklist("client-readiness")
        ]
        self.assertEqual([], review.validate_document("client-readiness", document)["errors"])

        # The checklist itself stays optional (D-07).
        document.pop("checklist")
        self.assertEqual([], review.validate_document("client-readiness", document)["errors"])

    def test_run_validate_returns_the_corrected_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put_review(work_dir, 1, "logic", "v1-logic-all-unknown")
            args = argparse.Namespace(
                workdir=str(work_dir),
                kind="logic",
                path="reviews/v1-logic.json",
                iteration=1,
                draft_sha=None,
                human=False,
            )
            result = review.run_validate(args)
            self.assertEqual([], result["errors"])
            self.assertTrue(result["downgraded"])
            self.assertEqual("needs_revision", result["verdict"])
            self.assertEqual([], schema.validate(result["document"], "review"))


class IssueClientTest(unittest.TestCase):
    """D-173a: the client-facing sentence of a blocker, only outside English."""

    def test_normalize_issue_keeps_issue_client(self):
        row = review._normalize_issue({"severity": "blocker", "issue": "x", "issue_client": "Satz."}, "logic")
        self.assertEqual("Satz.", row["issue_client"])

    def test_an_english_review_loses_a_reviewer_written_client_sentence(self):
        # An English task's reviewer may emit the schema-valid optional field; validation removes
        # it silently, so it can never reach the Status section, the summary or the signature.
        document = fixture("v1-logic-blocker")
        document["issues"][0]["issue_client"] = "Der Test wird nicht angewendet."
        result = review.validate_document("logic", document, language="en")
        self.assertEqual([], result["errors"])
        rows = [review._normalize_issue(issue, "logic") for issue in result["document"]["issues"]]
        self.assertFalse(any("issue_client" in row for row in review.deduplicate(rows)))

    def test_a_merged_duplicate_keeps_the_client_sentence(self):
        payload = {
            "severity": "blocker",
            "category": "missing_application",
            "section_id": "s-4-1",
            "issue": "The balancing test is stated but never applied to the described facts.",
            "suggestion": "Apply it.",
        }
        first = review._normalize_issue({**payload, "issue_client": "Der Test wird nicht angewendet."}, "logic")
        second = review._normalize_issue(payload, "counterarguments")
        merged = review.deduplicate([first, second])
        self.assertEqual(1, len(merged))
        self.assertEqual("Der Test wird nicht angewendet.", merged[0]["issue_client"])

    def test_a_merged_duplicate_takes_the_other_sentence_when_its_own_is_missing(self):
        payload = {
            "severity": "blocker",
            "category": "missing_application",
            "section_id": "s-4-1",
            "issue": "The balancing test is stated but never applied to the described facts.",
            "suggestion": "Apply it.",
        }
        first = review._normalize_issue(payload, "logic")
        second = review._normalize_issue({**payload, "issue_client": "Der Test wird nicht angewendet."}, "logic")
        merged = review.deduplicate([first, second])
        self.assertEqual(1, len(merged))
        self.assertEqual("Der Test wird nicht angewendet.", merged[0]["issue_client"])

    def packs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        packs = Path(tmp.name)
        _i18n.fake_pack(packs, "de", {"memo.rules.L-07": "Format der Risikozeile"})
        return packs

    def test_deterministic_blockers_get_a_client_sentence_only_outside_english(self):
        report = {
            "draft_sha": DRAFT_SHA,
            "clean": False,
            "findings": [
                {
                    "rule": "L-07",
                    "severity": "blocker",
                    "line": 3,
                    "section_id": "s-4-1",
                    "excerpt": "Risk line",
                    "hint": "Risk line format: the last paragraph of the subsection must start with `Risk: <…>.`",
                }
            ],
        }
        for language, expected in (("de", "Format der Risikozeile"), ("en", None)):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp))
                state_io.write_state(work_dir, lambda state: state.update({"language": language}))
                (work_dir / "lint.json").write_text(json.dumps(report), encoding="utf-8")
                state = state_io.read_state(work_dir)
                with mock.patch.object(i18n, "PACK_DIR", self.packs()):
                    issues, stale = review._deterministic_issues(work_dir, state, DRAFT_SHA)
                self.assertEqual([], stale)
                self.assertEqual(1, len(issues))
                self.assertEqual(report["findings"][0]["hint"], issues[0]["issue"])
                if expected is None:
                    self.assertNotIn("issue_client", issues[0])
                else:
                    self.assertEqual(expected, issues[0]["issue_client"])

    def test_synthesized_blockers_get_a_client_sentence_only_outside_english(self):
        for language, expected in (("de", "Checklistenpunkt LOG-02 konnte nicht geprüft werden."), ("en", None)):
            with self.subTest(language=language):
                with mock.patch.object(i18n, "PACK_DIR", self.packs()):
                    _i18n.fake_pack(
                        i18n.PACK_DIR,
                        "de",
                        {
                            "memo.rules.L-07": "Format der Risikozeile",
                            "memo.blockers.hard_fail_unknown": (
                                "Checklistenpunkt {checklist_id} konnte nicht geprüft werden."
                            ),
                        },
                    )
                    row = review._unverified_issue("LOG-02", language=language)
                self.assertIn("Hard-fail checklist item LOG-02", row["issue"])
                if expected is None:
                    self.assertNotIn("issue_client", row)
                else:
                    self.assertEqual(expected, row["issue_client"])


class DeduplicationTest(unittest.TestCase):
    """§4.5 п.3 — merge inside one category, keep provenance, flag opposite conclusions."""

    def test_jaccard_merges_near_identical_texts(self):
        left = review._tokens("The balancing test is stated but never applied to the described facts.")
        right = review._tokens("The balancing test is stated but never applied to the facts described.")
        self.assertGreaterEqual(review.jaccard(left, right), review.DEDUP_JACCARD)

    def test_a_merge_never_demotes_a_substance_issue_to_a_form_one(self):
        """§4.5 п.3: the merged issue belongs to every participant, whichever order they arrive in."""
        payload = {
            "severity": "blocker",
            "category": "source_drift",
            "section_id": "s-4-2",
            "issue": "The cited paragraph no longer supports the proposition it is attached to.",
            "suggestion": "Repoint the citation.",
        }
        for order in (("form", "citations"), ("citations", "form")):
            with self.subTest(order=order):
                merged = review.deduplicate([review._normalize_issue(payload, source) for source in order])
                self.assertEqual(1, len(merged))
                self.assertEqual("substance", merged[0]["tier"])
                self.assertEqual(["citations", "form"], merged[0]["provenance"])
                self.assertEqual("citations", merged[0]["source_reviewer"])
                self.assertTrue(merged[0]["grounded"])

    def test_different_category_is_never_merged(self):
        issues = [
            review._normalize_issue(
                {"severity": "major", "category": "a", "section_id": "s-1", "issue": "same text", "suggestion": ""},
                "logic",
            ),
            review._normalize_issue(
                {"severity": "major", "category": "b", "section_id": "s-1", "issue": "same text", "suggestion": ""},
                "form",
            ),
        ]
        self.assertEqual(2, len(review.deduplicate(issues)))


class AggregateTest(unittest.TestCase):
    def test_unresolved_citation_blocker_prevents_approved_even_if_all_reviewers_approve(self):
        from memoforge import revision

        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            for kind in ("logic", "form", "citations", "counterarguments"):
                put_review(work_dir, 1, kind, f"v1-{kind}")
            put_report(work_dir, "lint.json", "lint-clean")
            put_report(work_dir, "citations.json", "citations-blocker")

            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertEqual([], result.get("errors", []))
            self.assertEqual(["citations", "counterarguments", "form", "logic"], result["coverage"])
            self.assertEqual([], result["failed_reviewers"])
            self.assertEqual(1, result["substance_blockers"])
            self.assertEqual(1, result["deterministic_blockers"])

            state = state_io.read_state(work_dir)
            record = review.iteration_record(state, 1)
            self.assertEqual(1, record["substance_blockers"])
            self.assertEqual(review.DETERMINISTIC, record["issues"][0]["source_reviewer"])

            issue_step(work_dir, "s-102")
            decision = revision.run_next(
                argparse.Namespace(
                    workdir=str(work_dir), iteration=1, step="s-102", attempt=1, next_step=None, human=False
                )
            )
            self.assertNotIn("approved", str(decision["final_status"]))
            self.assertNotEqual("approved_on_v1", decision["final_status"])

    def test_stale_lint_report_is_not_folded_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            for kind in ("logic", "form", "citations", "counterarguments"):
                put_review(work_dir, 1, kind, f"v1-{kind}")
            stale = fixture("citations-blocker")
            stale["draft_sha"] = "b" * 64
            (work_dir / "citations.json").write_text(json.dumps(stale), encoding="utf-8")

            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertEqual(0, result["substance_blockers"])
            self.assertEqual(["citations.json"], result["stale_reports"])

    def test_duplicate_issue_is_merged_keeping_provenance_and_max_severity(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic", "counterarguments"])
            put_review(work_dir, 1, "logic", "v1-logic-blocker")
            put_review(work_dir, 1, "counterarguments", "v1-counterarguments-duplicate")

            review.run_aggregate(aggregate_args(work_dir))
            record = review.iteration_record(state_io.read_state(work_dir), 1)
            merged = [issue for issue in record["issues"] if issue["category"] == "missing_application"]
            self.assertEqual(1, len(merged))
            self.assertEqual("blocker", merged[0]["severity"])
            self.assertEqual(["counterarguments", "logic"], merged[0]["provenance"])
            self.assertFalse(merged[0]["conflict"])

    def test_a_form_and_a_citations_blocker_merge_into_one_substance_blocker(self):
        # §4.5 п.3 / п.4: the standard Full order puts `form` before `citations`; the merged issue
        # must stay a substance blocker either way, or branch 5 would replace a substance fix.
        for order in (["form", "citations"], ["citations", "form"]):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp), reviewers=list(order))
                put_review(work_dir, 1, "form", "v1-form-duplicate")
                put_review(work_dir, 1, "citations", "v1-citations-blocker")

                result = review.run_aggregate(aggregate_args(work_dir))
                self.assertEqual(1, result["substance_blockers"])
                self.assertEqual(0, result["form_blockers"])
                record = review.iteration_record(state_io.read_state(work_dir), 1)
                merged = [row for row in record["issues"] if row["section_id"] == "s-4-2"]
                self.assertEqual(1, len(merged))
                self.assertEqual(["citations", "form"], merged[0]["provenance"])
                self.assertEqual("substance", merged[0]["tier"])

    def test_opposite_conclusions_are_flagged_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic", "counterarguments"])
            put_review(work_dir, 1, "logic", "v1-logic-conflict")
            put_review(work_dir, 1, "counterarguments", "v1-counterarguments-conflict")

            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertTrue(result["conflict"])
            record = review.iteration_record(state_io.read_state(work_dir), 1)
            same_section = [issue for issue in record["issues"] if issue["section_id"] == "s-4-1"]
            self.assertEqual(2, len(same_section))
            self.assertTrue(all(issue["conflict"] for issue in same_section))

    def test_invalid_review_is_retried_once_and_then_stubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic", "form"])
            put_review(work_dir, 1, "logic", "v1-logic")
            (work_dir / "reviews/v1-form.json").write_text("not json at all", encoding="utf-8")

            first = review.run_aggregate(aggregate_args(work_dir, step="s-101"))
            self.assertEqual("rerun_reviewers", first["next"])
            self.assertEqual(["form"], first["retry_reviewers"])
            self.assertFalse(first["aggregated"])
            state = state_io.read_state(work_dir)
            self.assertEqual(1, state["attempts"]["reviewer_json_retry"]["1:form"])
            self.assertIsNone(review.iteration_record(state, 1))

            second = review.run_aggregate(aggregate_args(work_dir, step="s-101b"))
            self.assertTrue(second["aggregated"])
            self.assertEqual(["form"], second["failed_reviewers"])
            self.assertEqual(["reviews/v1-form.json"], second["stubs_written"])
            stub = state_io.read_json(work_dir / "reviews/v1-form.json")
            self.assertEqual("failed", stub["status"])
            self.assertEqual([], schema.validate(stub, "review"))
            state = state_io.read_state(work_dir)
            published = [
                row for row in state["published"] if row["canonical_path"] == "reviews/v1-form.json"
            ]
            self.assertEqual(1, len(published))
            self.assertEqual(
                state_io.sha256_file(work_dir / "reviews/v1-form.json"), published[0]["sha256"]
            )
            self.assertIn(
                "reviewer_output_malformed",
                [banner["banner_id"] for banner in state["fallback_banners"]],
            )

    def test_a_retry_the_machine_already_issued_is_not_offered_again(self):
        # D-69: `mf next` and `mf review aggregate` spend one and the same
        # `reviewer_json_retry{iteration,kind}`; a `reason: failure` dispatch of `reviews/v1-form.json`
        # is that budget, so the aggregate stubs the kind instead of asking for a third dispatch.
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic", "form"])
            put_review(work_dir, 1, "logic", "v1-logic")
            (work_dir / "reviews/v1-form.json").write_text("not json at all", encoding="utf-8")

            def machine_retry(state: dict) -> None:
                state.setdefault("steps", []).append(
                    {
                        "step_id": "s-090",
                        "kind": "dispatch",
                        "phase": "revision_loop",
                        "attempt": 2,
                        "reason": "failure",
                        "status": "fail",
                        "issued_at": "2026-09-08T12:00:00.000Z",
                        "expected_outputs": [
                            {
                                "canonical_path": "reviews/v1-form.json",
                                "work_path": "w",
                                "schema": "review",
                            }
                        ],
                    }
                )

            state_io.write_state(work_dir, machine_retry)
            state = state_io.read_state(work_dir)
            self.assertEqual(1, review.failure_retry_attempts(state, 1, "form"))
            self.assertEqual(1, review.json_retry_used(state, 1, "form"))
            self.assertEqual(0, review.json_retry_used(state, 1, "logic"))

            result = review.run_aggregate(aggregate_args(work_dir, step="s-101"))
            self.assertTrue(result["aggregated"])
            self.assertEqual(["form"], result["failed_reviewers"])
            self.assertEqual(["reviews/v1-form.json"], result["stubs_written"])
            self.assertEqual({}, state_io.read_state(work_dir)["attempts"]["reviewer_json_retry"])

    def test_missing_review_file_counts_as_a_failed_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic", "form"])
            put_review(work_dir, 1, "logic", "v1-logic")
            review.run_aggregate(aggregate_args(work_dir, step="s-1"))
            result = review.run_aggregate(aggregate_args(work_dir, step="s-2"))
            self.assertEqual(["form"], result["failed_reviewers"])
            self.assertEqual(["logic"], result["coverage"])

    def test_a_targeted_iteration_expects_only_the_reviewers_it_dispatched(self):
        # D-165: the targeted citation pass dispatches `citations` alone, so the three reviewers it
        # deliberately skipped are not missing files — no stub, no `failed_reviewers`.
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), iteration=3)
            state_io.write_state(
                work_dir,
                lambda state: state.update(
                    {"targeted_fix": {"iteration": 3, "reviewers": ["citations"]}}
                ),
            )
            put_review(work_dir, 3, "citations", "v1-citations")

            result = review.run_aggregate(aggregate_args(work_dir, iteration=3))
            self.assertEqual([], result.get("errors", []))
            self.assertEqual(["citations"], result["coverage"])
            self.assertEqual([], result["failed_reviewers"])
            self.assertEqual([], result["stubs_written"])
            record = review.iteration_record(state_io.read_state(work_dir), 3)
            self.assertEqual(["citations"], record["reviewers"])

    def test_the_aggregated_issue_keeps_the_reviewer_s_issue_category(self):
        # D-165: branch 9 matches on `issue_category`, so the aggregate must carry it.
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["citations"])
            document = fixture("v1-citations")
            document["issues"] = [
                {
                    "severity": "blocker",
                    "category": "unsupported_claim",
                    "section_id": "s-4-2",
                    "issue": "The rule statement carries no [[src:]] token.",
                    "suggestion": "Add the token of the provision the sentence states.",
                    "issue_category": "unsupported_claim",
                }
            ]
            document["verdict"] = "needs_revision"
            state_io.write_json_atomic(work_dir / review.review_path(1, "citations"), document)

            review.run_aggregate(aggregate_args(work_dir))
            record = review.iteration_record(state_io.read_state(work_dir), 1)
            self.assertEqual(1, len(record["issues"]))
            self.assertEqual("unsupported_claim", record["issues"][0]["issue_category"])
            self.assertEqual("citations", record["issues"][0]["source_reviewer"])

    def test_repeat_of_a_closed_step_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic"])
            put_review(work_dir, 1, "logic", "v1-logic")
            first = review.run_aggregate(aggregate_args(work_dir, step="s-101"))
            again = review.run_aggregate(aggregate_args(work_dir, step="s-101"))
            self.assertEqual(first, again)
            state = state_io.read_state(work_dir)
            self.assertEqual(1, len([row for row in state["iterations"] if row["iteration"] == 1]))

    def test_pass_ratio_and_coverage_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic"])
            put_review(work_dir, 1, "logic", "v1-logic-blocker")
            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertEqual(["logic"], result["coverage"])
            self.assertEqual(round(10 / 11, 4), result["pass_ratio"])


class PublishedInputsTest(unittest.TestCase):
    """D-41: a canonical input is read through `published[]`, not trusted because it parses."""

    def test_a_review_changed_after_publication_stops_the_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic"])
            put_review(work_dir, 1, "logic", "v1-logic")
            publish(work_dir, "reviews/v1-logic.json")

            document = state_io.read_json(work_dir / "reviews/v1-logic.json")
            document["issues"].append(
                {
                    "severity": "minor",
                    "category": "ordering",
                    "section_id": "s-3-2",
                    "issue": "An issue nobody reviewed, added after the file was published.",
                    "suggestion": "Reorder the paragraph.",
                }
            )
            state_io.write_json_atomic(work_dir / "reviews/v1-logic.json", document)

            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertEqual(["output_modified_after_publish"], result["errors"])
            self.assertEqual("reviews/v1-logic.json", result["path"])
            state = state_io.read_state(work_dir)
            self.assertIsNone(review.iteration_record(state, 1), "no iteration is written on drift")
            self.assertIsNone(
                next(row for row in state["steps"] if row["step_id"] == "s-101")["status"],
                "the step stays open for recovery",
            )

    def test_an_unpublished_review_is_still_read(self):
        # The guard is about `published[]`, not about refusing files the CLI never published.
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic"])
            put_review(work_dir, 1, "logic", "v1-logic")
            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertEqual(["logic"], result["coverage"])

    def test_a_lint_report_changed_after_publication_stops_the_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic"])
            put_review(work_dir, 1, "logic", "v1-logic")
            put_report(work_dir, "lint.json", "lint-clean")
            publish(work_dir, "lint.json")

            report = state_io.read_json(work_dir / "lint.json")
            report["findings"].append(
                {
                    "rule": "L-05",
                    "severity": "blocker",
                    "line": 3,
                    "section_id": "s-1",
                    "excerpt": "### 1.1",
                    "hint": "A finding added to the published report by hand.",
                }
            )
            state_io.write_json_atomic(work_dir / "lint.json", report)

            result = review.run_aggregate(aggregate_args(work_dir))
            self.assertEqual(["output_modified_after_publish"], result["errors"])
            self.assertEqual("lint.json", result["path"])


class StepIdentityTest(unittest.TestCase):
    """D-40: `review aggregate` goes through `stepctx.check_identity` before any side effect."""

    def args(self, work_dir: Path, *, step: str, attempt: int, iteration: int = 1) -> argparse.Namespace:
        return argparse.Namespace(
            workdir=str(work_dir), iteration=iteration, step=step, attempt=attempt, human=False
        )

    def prepared(self, tmp: str) -> Path:
        work_dir = new_task(Path(tmp), reviewers=["logic"])
        put_review(work_dir, 1, "logic", "v1-logic")
        return work_dir

    def test_a_step_that_was_never_issued_is_an_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = self.prepared(tmp)
            result = review.run_aggregate(self.args(work_dir, step="s-404", attempt=1))
            self.assertEqual(["identity_mismatch"], result["errors"])
            self.assertIsNone(review.iteration_record(state_io.read_state(work_dir), 1))

    def test_an_attempt_the_machine_never_issued_is_an_identity_mismatch(self):
        for attempt in (1, 3):
            with self.subTest(attempt=attempt), tempfile.TemporaryDirectory() as tmp:
                work_dir = self.prepared(tmp)
                issue_step(work_dir, "s-101", attempt=2)
                result = review.run_aggregate(self.args(work_dir, step="s-101", attempt=attempt))
                self.assertEqual(["identity_mismatch"], result["errors"])
                self.assertIsNone(review.iteration_record(state_io.read_state(work_dir), 1))

    def test_the_step_is_closed_through_stepctx_with_its_args_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = self.prepared(tmp)
            result = review.run_aggregate(aggregate_args(work_dir))
            row = next(row for row in state_io.read_state(work_dir)["steps"] if row["step_id"] == "s-101")
            self.assertEqual("ok", row["status"])
            self.assertEqual({"args": "review aggregate --iteration 1", "result": result}, row["result_ref"])

    def test_the_same_step_replayed_with_other_arguments_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = self.prepared(tmp)
            review.run_aggregate(aggregate_args(work_dir))
            again = review.run_aggregate(self.args(work_dir, step="s-101", attempt=1, iteration=2))
            self.assertEqual(["identity_mismatch"], again["errors"])
            self.assertEqual("arguments_changed", again["reason"])


class MediatorTest(unittest.TestCase):
    def test_mediator_from_issues_writes_a_schema_valid_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), reviewers=["logic", "citations"])
            put_review(work_dir, 1, "logic", "v1-logic-blocker")
            put_review(work_dir, 1, "citations", "v1-citations-blocker")
            review.run_aggregate(aggregate_args(work_dir, step="s-101"))

            result = review.run_mediator_from_issues(mediator_args(work_dir))
            document = state_io.read_json(work_dir / result["path"])
            self.assertEqual([], schema.validate(document, "mediator"))
            self.assertEqual(2, len(document["instructions"]))
            self.assertEqual(1, len(document["dropped"]))
            self.assertEqual("blocker", document["instructions"][0]["severity"])
            self.assertEqual("minor", document["dropped"][0]["severity"])
            state = state_io.read_state(work_dir)
            published = [row for row in state["published"] if row["canonical_path"] == result["path"]]
            self.assertEqual(1, len(published))
            self.assertEqual(result["sha256"], published[0]["sha256"])

    def test_substance_instructions_precede_form_ones(self):
        record = {
            "iteration": 1,
            "issues": [
                review._normalize_issue(
                    {
                        "severity": "major",
                        "category": "style",
                        "section_id": "s-2",
                        "issue": "Sentence too long.",
                        "suggestion": "Split it.",
                    },
                    "form",
                ),
                review._normalize_issue(
                    {
                        "severity": "major",
                        "category": "logic",
                        "section_id": "s-4-1",
                        "issue": "Rule unapplied.",
                        "suggestion": "Apply it.",
                    },
                    "logic",
                ),
            ],
        }
        document = review.build_mediator(record)
        self.assertEqual(["logic", "form"], [row["source_reviewer"] for row in document["instructions"]])
        self.assertEqual([], schema.validate(document, "mediator"))


class FixtureShapeTest(unittest.TestCase):
    def test_every_review_fixture_is_schema_valid(self):
        for path in sorted(FIXTURES.glob("*.json")):
            # `lint-*.json` / `citations-*.json` are deterministic reports (`lint` schema, §5.4).
            name = "lint" if path.name.startswith(("lint-", "citations-")) else "review"
            with self.subTest(fixture=path.name):
                self.assertEqual([], schema.validate(json.loads(path.read_text(encoding="utf-8-sig")), name))

    def test_checklists_cover_every_reviewer_kind(self):
        for kind in review.KINDS:
            with self.subTest(kind=kind):
                items = review.load_checklist(kind)
                self.assertTrue(items)
                self.assertEqual(len(items), len({row["id"] for row in items}))
                self.assertTrue(all(row["tier"] in ("substance", "form") for row in items))


class NoPreD40WrappersTest(unittest.TestCase):
    """D-40: the pre-D-40 `review` wrappers are gone; every caller uses `stepctx` directly."""

    WRAPPERS = ("begin_step", "finish_step", "stage_and_publish")
    PACKAGE = PLUGIN_ROOT / "scripts" / "memoforge"

    def test_review_no_longer_exposes_them(self):
        for name in self.WRAPPERS:
            self.assertFalse(hasattr(review, name), f"review.{name} is a pre-D-40 wrapper")

    def test_no_module_calls_them_on_review(self):
        """The grep: `review.<wrapper>` — and the review-only name `stage_and_publish` — are gone."""
        hits: list[str] = []
        needles = [f"review.{name}" for name in self.WRAPPERS] + ["stage_and_publish"]
        for path in sorted(self.PACKAGE.rglob("*.py")):
            rel = path.relative_to(PLUGIN_ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
                for needle in needles:
                    if needle in line:
                        hits.append(f"{rel}:{number}: {needle}")
        self.assertEqual([], hits, "pre-D-40 wrappers survive:\n  " + "\n  ".join(hits))

    def test_the_replacement_is_the_one_that_exists(self):
        """Non-tautology: `stepctx` really carries the three primitives the wrappers hid."""
        for name in ("check_identity", "close_step", "publish_file"):
            self.assertTrue(callable(getattr(stepctx, name, None)), f"stepctx.{name} is missing")


if __name__ == "__main__":
    unittest.main()
