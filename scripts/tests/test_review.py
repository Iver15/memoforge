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
from memoforge import events, i18n, limits, modes, review, revision, schema, state_io, stepctx, task  # noqa: E402

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


RUN_20260922 = Path(__file__).resolve().parent / "fixtures" / "run-20260922"
"""D-212: `iterations[]` of the 2026-09-22 run and the `reviews/v2-mediator.json` it published."""


def run_74_json(name: str):
    return json.loads((RUN_20260922 / name).read_text(encoding="utf-8-sig"))


class TargetedMediatorTest(unittest.TestCase):
    """D-212: with `only`, the mediator names those issues and drops every other one."""

    def setUp(self):
        self.v2 = run_74_json("state-iterations.json")[1]
        self.blocker = next(row for row in self.v2["issues"] if row["severity"] == "blocker")

    def test_without_only_the_output_is_what_the_run_published(self):
        published = run_74_json("v2-mediator.json")
        document = review.build_mediator(self.v2)
        self.assertEqual(published, document)
        self.assertEqual(state_io.dumps(published), state_io.dumps(document))
        self.assertEqual(review.build_mediator(self.v2), review.build_mediator(self.v2, only=None))

    def test_only_names_the_blockers_and_drops_the_majors_and_minors(self):
        document = review.build_mediator(self.v2, only=[self.blocker])
        self.assertEqual([], schema.validate(document, "mediator"))
        self.assertEqual(["s-5-4"], [row["section_id"] for row in document["instructions"]])
        self.assertEqual(run_74_json("v2-mediator.json")["instructions"][:1], document["instructions"])
        self.assertEqual(13, len(document["dropped"]))
        self.assertEqual(
            {"targeted pass: only the named blockers are fixed"}, {row["reason"] for row in document["dropped"]}
        )
        self.assertEqual(
            {"major": 4, "minor": 9},
            {
                severity: len([row for row in document["dropped"] if row["severity"] == severity])
                for severity in ("major", "minor")
            },
        )
        self.assertEqual(2, document["iteration"])
        self.assertEqual(self.v2["draft_sha"], document["draft_sha"])


class SourceEvidenceMediatorTest(unittest.TestCase):
    """D-239: the saved passage a reviewer's finding rests on reaches the writer through the CLI mediator."""

    RUN_79 = "v2-citations-source-evidence"
    """`reviews/v2-citations.json` of the 2026-09-23 run (run 79): the s-6 blocker carries `source_evidence`."""

    def aggregated(self, tmp: str) -> dict:
        document = fixture(self.RUN_79)
        work_dir = new_task(Path(tmp), reviewers=["citations"], iteration=2)
        state_io.write_state(
            work_dir, lambda state: state.update({"language": "ru", "current_draft_sha": document["draft_sha"]})
        )
        put_review(work_dir, 2, "citations", self.RUN_79)
        result = review.run_aggregate(aggregate_args(work_dir, iteration=2))
        self.assertTrue(result["aggregated"], result)
        return review.iteration_record(state_io.read_state(work_dir), 2)

    def test_the_run_79_targeted_instruction_ends_with_the_saved_passage(self):
        evidence = next(row for row in fixture(self.RUN_79)["issues"] if row["section_id"] == "s-6")
        evidence = evidence["source_evidence"]
        with tempfile.TemporaryDirectory() as tmp:
            record = self.aggregated(tmp)
        targeted = revision.targeted_blockers(record)
        self.assertEqual(["s-6"], [row["section_id"] for row in targeted])
        self.assertEqual(evidence, targeted[0]["source_evidence"])

        document = review.build_mediator(record, only=targeted)
        self.assertEqual([], schema.validate(document, "mediator"))
        self.assertEqual(["s-6"], [row["section_id"] for row in document["instructions"]])
        instruction = document["instructions"][0]["instruction"]
        suffix = f" Source text ({evidence['source_id']}): «{evidence['passage']}»"
        self.assertTrue(instruction.endswith(suffix), instruction)
        self.assertEqual(f"{targeted[0]['issue']} {targeted[0]['suggestion']}{suffix}", instruction)

    def test_an_issue_without_evidence_keeps_today_s_instruction(self):
        issue = {
            "severity": "blocker",
            "category": "untokened_rule",
            "section_id": "s-2",
            "issue": "A rule with no token.",
            "suggestion": "Add the token.",
            "issue_category": "unsupported_claim",
        }
        row = review._normalize_issue(issue, "citations")
        self.assertNotIn("source_evidence", row)
        document = review.build_mediator({"iteration": 2, "issues": [row]})
        self.assertEqual("A rule with no token. Add the token.", document["instructions"][0]["instruction"])

    def test_a_legacy_aggregate_gives_the_instruction_the_old_build_published(self):
        # A run resumed from a build before D-239 has `iterations[].issues[]` without `source_evidence`.
        v2 = run_74_json("state-iterations.json")[1]
        self.assertFalse(any("source_evidence" in row for row in v2["issues"]))
        self.assertEqual(run_74_json("v2-mediator.json"), review.build_mediator(v2))
        blocker = next(row for row in v2["issues"] if row["severity"] == "blocker")
        self.assertEqual(
            run_74_json("v2-mediator.json")["instructions"][:1],
            review.build_mediator(v2, only=[blocker])["instructions"],
        )

    def test_a_merged_duplicate_keeps_the_survivor_s_evidence_or_takes_the_other_s(self):
        def row(evidence: dict | None) -> dict:
            issue = {
                "severity": "blocker",
                "category": "exception_limb_omitted",
                "section_id": "s-6",
                "issue": "Section 6 drops the limb of the rule on the nature of the relationship.",
                "suggestion": "Quote the limb.",
            }
            if evidence:
                issue["source_evidence"] = evidence
            return review._normalize_issue(issue, "citations")

        first = {"source_id": "zozpp-st-12", "status": "contradicted", "passage": "first passage"}
        second = {"source_id": "zozpp-st-12", "status": "contradicted", "passage": "second passage"}
        taken = review.deduplicate([row(None), row(second)])
        self.assertEqual(1, len(taken))
        self.assertEqual(second, taken[0]["source_evidence"])
        kept = review.deduplicate([row(first), row(second)])
        self.assertEqual(1, len(kept))
        self.assertEqual(first, kept[0]["source_evidence"])


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


class MoneyAndRequiredStepsTest(unittest.TestCase):
    """D-240: money and required steps are conclusions; LOG-06/LOG-10 grow, the ids stay, so resume stays valid."""

    def items(self) -> dict:
        return {row["id"]: row["text"] for row in review.load_checklist("logic")}

    def test_the_logic_ids_are_unchanged(self):
        self.assertEqual([f"LOG-{n:02d}" for n in range(1, 12)], list(self.items()))

    def test_log_06_covers_overlapping_heads_and_a_figure_called_reliable(self):
        text = self.items()["LOG-06"]
        self.assertTrue(text.startswith("Conclusions on different issues are consistent with each other"), text)
        for needle in ("one computation", "rates high-risk"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_log_10_asks_for_a_cited_rule_behind_a_required_step(self):
        text = self.items()["LOG-10"]
        self.assertTrue(text.startswith("Every recommendation traces back to a finding"), text)
        # The plan's needle "presented as required" belongs to the writer rule; the LOG-10 wording is verbatim.
        for needle in ("is told is required before another", "rest on a cited rule", "prudent practice"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


RUN_20260921 =Path(__file__).resolve().parent / "fixtures" / "run-20260921"
"""D-208: the saved reviews of the 2026-09-21 run (pre-plan-72 shape), kept out of `FIXTURES`."""


def run_review(name: str) -> dict:
    return json.loads((RUN_20260921 / f"run-20260921-{name}.json").read_text(encoding="utf-8-sig"))


def text_checks() -> list[dict]:
    """One `text_checks` row per status of D-208."""
    return [
        {
            "source_id": "ru-a40-630-2025",
            "section_id": "s-5-2",
            "status": "contradicted",
            "finding_disagrees": True,
            "note": "The formula is the respondent's position the decision recites, not the court's holding.",
        },
        {
            "source_id": "ru-gk-428",
            "section_id": "s-4",
            "status": "confirmed",
            "finding_disagrees": False,
            "note": "Article 428(2) located; the draft follows it.",
        },
        {
            "source_id": "ru-vs-309-es14-4692",
            "section_id": "s-9",
            "status": "inconclusive",
            "finding_disagrees": False,
            "note": "Three lookups for the recourse rule found nothing; the act was not read whole.",
        },
        {
            "source_id": "ru-a40-630-2025",
            "section_id": "s-10-3",
            "status": "not_reached",
            "finding_disagrees": False,
            "note": "The budget ran out before the practice statement of 10.3 was checked.",
        },
    ]


PASSAGE = (
    "Ответчик предлагает Продавцам компенсировать причиненные им убытки по размеру действительной стоимости "
    "товара, определяемой по условиям самой торговой Площадки, за вычетом стоимости услуг Ozon."
)
"""A sentence of the saved A40-630/2025 decision (`fixtures/source_text/`): a party's position the act recites."""


class TextChecksTest(unittest.TestCase):
    """D-208: `source_evidence` on an issue and `text_checks` rows on citations/counterarguments reviews."""

    RUN_REVIEWS = ("v1-citations", "v2-citations", "v3-citations", "v1-counterarguments")

    def test_a_citations_review_with_text_checks_passes_the_validator(self):
        document = run_review("v2-citations")
        # Rule 3: a `contradicted` check is a blocker on the draft sentence, and CIT-02 grades false.
        document["issues"].insert(
            1,
            {
                "severity": "blocker",
                "category": "holding_misstated",
                "section_id": "s-5-2",
                "issue": "5.2 writes the offer's formula as the measure the court applied; the decision recites it.",
                "suggestion": "Withdraw the attribution to the court, or qualify the formula as the respondent's.",
                "checklist_id": "CIT-02",
                "issue_category": "unsupported_claim",
                "source_evidence": {"source_id": "ru-a40-630-2025", "status": "contradicted", "passage": PASSAGE},
            },
        )
        cit02 = next(row for row in document["checklist"] if row["id"] == "CIT-02")
        cit02.update({"pass": False, "evidence": "5.2 attributes the offer's formula to the court."})
        document["text_checks"] = text_checks()
        result = review.validate_document("citations", document, language="ru")
        self.assertEqual([], result["errors"])
        self.assertTrue(result["valid"])
        self.assertEqual(2, result["blockers"])
        self.assertEqual(document, result["document"])

    def test_a_counterarguments_review_with_text_checks_passes_the_validator(self):
        document = run_review("v1-counterarguments")
        # Rule 6: a suggestion that states what the source holds carries the confirmed passage.
        formula = next(row for row in document["issues"] if row["category"] == "record_contradicts_assumption")
        formula["source_evidence"] = {"source_id": "ru-a40-630-2025", "status": "confirmed", "passage": PASSAGE}
        document["text_checks"] = text_checks()
        result = review.validate_document("counterarguments", document, language="ru")
        self.assertEqual([], result["errors"])
        self.assertTrue(result["valid"])
        self.assertEqual(document, result["document"])

    def test_a_status_outside_the_contract_is_refused(self):
        cases = (
            ("text_checks", "unchecked"),
            ("text_checks", "found"),
            ("source_evidence", "inconclusive"),
            ("source_evidence", "not_reached"),
        )
        for field, status in cases:
            with self.subTest(field=field, status=status):
                document = run_review("v2-citations")
                document["text_checks"] = text_checks()
                if field == "text_checks":
                    document["text_checks"][0]["status"] = status
                else:
                    document["issues"][0]["source_evidence"] = {
                        "source_id": "ru-a40-630-2025",
                        "status": status,
                        "passage": PASSAGE,
                    }
                result = review.validate_document("citations", document, language="ru")
                self.assertFalse(result["valid"])
                self.assertTrue(result["errors"])

    def test_the_saved_reviews_of_the_run_still_pass_unchanged(self):
        for name in self.RUN_REVIEWS:
            kind = name.split("-", 1)[1]
            with self.subTest(review=name):
                document = run_review(name)
                self.assertNotIn("text_checks", document)
                result = review.validate_document(kind, document, language="ru")
                self.assertEqual([], result["errors"])
                self.assertFalse(result["downgraded"])
                self.assertEqual(document, result["document"])


def stored(
    source: str,
    *,
    severity: str = "major",
    section_id: str = "s-5-1",
    category: str = "narrow_trigger",
    text: str = "The trigger is drawn too narrowly for the platform's own terms.",
    checklist_id: str | None = None,
    also: tuple[str, ...] = (),
) -> dict:
    """One `iterations[].issues[]` row as `aggregate` stores it; `also` merges more participants in."""
    payload = {
        "severity": severity,
        "category": category,
        "section_id": section_id,
        "issue": text,
        "suggestion": "Qualify the statement or support it.",
    }
    if checklist_id:
        payload["checklist_id"] = checklist_id
    row = review._normalize_issue(payload, source)
    if also:
        # §4.5 п.3: a merge keeps every participant and takes the tier and the reviewer of the union.
        row["provenance"] = sorted({source, *also})
        row["tier"] = review.tier_of(row["provenance"])
        row["source_reviewer"] = review.primary_reviewer(row["provenance"])
    return row


def stored_record(
    iteration: int,
    *issues: dict,
    reviewers: tuple[str, ...] = review.REVIEWER_KINDS,
    failed: tuple[str, ...] = (),
) -> dict:
    """The fields of one `iterations[]` record that D-210 reads."""
    return {
        "iteration": iteration,
        "reviewers": list(reviewers),
        "coverage": sorted(kind for kind in reviewers if kind not in failed),
        "failed_reviewers": sorted(failed),
        "issues": list(issues),
    }


def run_stored_record(number: int) -> dict:
    """Iteration `number` of the 2026-09-21 run rebuilt as a stored record.

    The saved rows keep a subset of the stored fields and no `coverage`: on this run every dispatched
    reviewer answered, so `coverage` is `reviewers` and each issue has its one reviewer as provenance.
    """
    saved = next(row for row in run_review("state-iterations")["iterations"] if row["iteration"] == number)
    issues = [
        stored(
            row["source_reviewer"],
            severity=row["severity"],
            section_id=row["section_id"],
            category=row["category"],
            text=f"{row['category']} in {row['section_id']}",
            checklist_id=row["checklist_id"],
        )
        for row in saved["open_issues"]
    ]
    return stored_record(number, *issues, reviewers=tuple(saved["reviewers"]))


class OpenSubstanceMajorsTest(unittest.TestCase):
    """D-210: the substantive majors still open on the delivered version, one row per stored issue."""

    KEYS = {
        "id",
        "class",
        "reviewer",
        "section_id",
        "category",
        "issue_category",
        "issue",
        "issue_client",
        "suggestion",
        "from_iteration",
        "origin",
        "status",
    }

    RUN_V2 = [
        (2, "counterarguments", "s-5-1", "narrow_trigger"),
        (2, "counterarguments", "s-5-1", "hidden_assumption"),
        (2, "counterarguments", "s-7-1", "unaddressed_statutory_limitation"),
        (2, "counterarguments", "s-9", "overstated_recourse"),
        (2, "logic", "s-10-3", "unsupported_conclusion"),
    ]
    """The five v2 majors of the run, by section in document order, then by position in the record."""

    @staticmethod
    def rows(*records: dict, version: int) -> list[dict]:
        return review.open_substance_majors({"iterations": list(records)}, version)

    @staticmethod
    def brief(rows: list[dict]) -> list[tuple]:
        return [(row["from_iteration"], row["class"], row["section_id"], row["category"]) for row in rows]

    def run_rows(self, version: int) -> list[dict]:
        return self.rows(*(run_stored_record(number) for number in (1, 2, 3)), version=version)

    def test_the_rebuilt_run_records_keep_the_saved_tiers(self):
        for number in (1, 2, 3):
            saved = next(row for row in run_review("state-iterations")["iterations"] if row["iteration"] == number)
            with self.subTest(iteration=number):
                self.assertEqual(
                    [row["tier"] for row in saved["open_issues"]],
                    [row["tier"] for row in run_stored_record(number)["issues"]],
                )

    def test_version_3_of_the_run_keeps_the_v2_majors_its_citations_pass_did_not_review(self):
        rows = self.run_rows(3)
        self.assertEqual(self.RUN_V2 + [(3, "citations", "s-9", "pinpoint_mismatch")], self.brief(rows))
        self.assertEqual([f"om-{number}" for number in range(1, 7)], [row["id"] for row in rows])
        for row in rows:
            self.assertEqual(self.KEYS, set(row))
            self.assertEqual(("loop", "open"), (row["origin"], row["status"]))
            self.assertEqual(row["class"], row["reviewer"])

    def test_version_2_of_the_run_gives_the_five_v2_majors(self):
        self.assertEqual(self.RUN_V2, self.brief(self.run_rows(2)))

    def test_a_row_carries_the_stored_issue(self):
        issue = stored("citations", section_id="s-4", category="unsupported_law", checklist_id="CIT-01")
        issue["issue_category"] = "unsupported_claim"
        issue["issue_client"] = "Правило не подтверждено источником."
        self.assertEqual(
            [
                {
                    "id": "om-1",
                    "class": "citations",
                    "reviewer": "citations",
                    "section_id": "s-4",
                    "category": "unsupported_law",
                    "issue_category": "unsupported_claim",
                    "issue": issue["issue"],
                    "issue_client": "Правило не подтверждено источником.",
                    "suggestion": issue["suggestion"],
                    "from_iteration": 1,
                    "origin": "loop",
                    "status": "open",
                }
            ],
            self.rows(stored_record(1, issue), version=1),
        )
        bare = self.rows(stored_record(1, stored("logic", section_id="s-3")), version=1)
        self.assertEqual((None, None), (bare[0]["issue_category"], bare[0]["issue_client"]))

    def test_a_merged_issue_is_one_row_of_the_class_its_record_still_speaks_for(self):
        merged = stored("counterarguments", also=("citations",))
        self.assertEqual("citations", merged["source_reviewer"])
        latest = self.rows(stored_record(1, merged), version=1)
        self.assertEqual([(1, "citations", "s-5-1", "narrow_trigger")], self.brief(latest))
        # v3 is a citations-only pass that did not re-raise it: citations no longer speaks for it.
        targeted = self.rows(stored_record(2, merged), stored_record(3, reviewers=("citations",)), version=3)
        self.assertEqual([(2, "counterarguments", "s-5-1", "narrow_trigger")], self.brief(targeted))
        self.assertEqual("citations", targeted[0]["reviewer"])

    def test_two_majors_of_one_section_and_category_stay_two_rows(self):
        first = stored("citations", section_id="s-9", category="pinpoint_mismatch", text="Art. 15 is cited to (1).")
        second = stored("citations", section_id="s-9", category="pinpoint_mismatch", text="Point 3 holds no rule.")
        rows = self.rows(stored_record(1, first, second), version=1)
        self.assertEqual(["om-1", "om-2"], [row["id"] for row in rows])
        self.assertEqual([first["issue"], second["issue"]], [row["issue"] for row in rows])

    def test_a_reviewer_that_failed_later_keeps_the_majors_it_raised_last(self):
        kept = stored("counterarguments", section_id="s-7-1", category="hidden_assumption")
        closed = stored("logic", section_id="s-3", category="unmapped_assumption", text="An assumption is unmapped.")
        rows = self.rows(stored_record(1, kept, closed), stored_record(2, failed=("counterarguments",)), version=2)
        self.assertEqual([(1, "counterarguments", "s-7-1", "hidden_assumption")], self.brief(rows))

    def test_a_regression_pair_gives_the_rows_of_the_selected_version(self):
        first = stored("logic", section_id="s-3", category="unmapped_assumption", text="An assumption is unmapped.")
        second = stored("counterarguments", section_id="s-6-1", category="risk_grade_overstated")
        records = (stored_record(1, first), stored_record(2, second))
        self.assertEqual([(1, "logic", "s-3", "unmapped_assumption")], self.brief(self.rows(*records, version=1)))
        self.assertEqual(
            [(2, "counterarguments", "s-6-1", "risk_grade_overstated")], self.brief(self.rows(*records, version=2))
        )

    def test_a_minor_a_form_major_and_a_blocker_are_never_rows(self):
        issues = (
            stored("logic", severity="minor", section_id="s-6-2", category="weak_application"),
            stored("form", section_id="s-5-1", category="undefined_term"),
            stored("citations", severity="blocker", section_id="s-5-2", category="unsupported_law"),
            stored("counterarguments", severity="blocker", section_id="s-5-1", category="omitted_contrary_authority"),
            stored("deterministic", severity="blocker", section_id="s-4", category="C-02"),
        )
        self.assertEqual([], self.rows(stored_record(1, *issues), version=1))
        self.assertEqual([], review.open_substance_majors({}, 1))

    @staticmethod
    def targeted_blocker(section_id: str) -> dict:
        blocker = stored("citations", severity="blocker", section_id=section_id, category="exception_limb_omitted",
                         text="A limb of the rule is left out.")
        blocker["issue_category"] = "source_drift"
        return blocker

    def targeted(self, *records: dict, version: int, iteration: int) -> list[dict]:
        state = {"iterations": list(records), "targeted_fix": {"iteration": iteration, "reviewers": ["citations"]}}
        return review.open_substance_majors(state, version)

    def run79_v2(self) -> dict:
        return stored_record(
            2,
            self.targeted_blocker("s-6"),
            stored("citations", section_id="s-4-4", category="authority_overstated",
                   text="Purchase cost credited to the court."),
            stored("counterarguments", section_id="s-4-4", category="overstated_recovery",
                   text="390 600 called reliable."),
            stored("logic", section_id="s-4-1", category="skipped_step",
                   text="The obligation breached is never named."),
        )

    def test_a_targeted_pass_keeps_the_citations_major_of_a_section_it_did_not_rewrite(self):
        clean = stored_record(3, stored("citations", severity="minor", section_id="s-6", category="qualifier_omitted"),
                              reviewers=("citations",))
        rows = self.targeted(self.run79_v2(), clean, version=3, iteration=3)
        self.assertEqual(
            [(2, "logic", "s-4-1", "skipped_step"), (2, "citations", "s-4-4", "authority_overstated"),
             (2, "counterarguments", "s-4-4", "overstated_recovery")],
            self.brief(rows),
        )

    def test_a_citations_major_in_the_rewritten_section_is_re_graded_by_the_targeted_pass(self):
        v2 = self.run79_v2()
        v2["issues"].append(stored("citations", section_id="s-6", category="pinpoint_mismatch",
                                   text="Point 2.1 is cited to 2.2."))
        rows = self.targeted(v2, stored_record(3, reviewers=("citations",)), version=3, iteration=3)
        self.assertNotIn(("s-6", "pinpoint_mismatch"), [(row["section_id"], row["category"]) for row in rows])
        self.assertIn((2, "citations", "s-4-4", "authority_overstated"), self.brief(rows))

    def test_a_major_the_targeted_pass_raises_is_a_row(self):
        new = stored("citations", section_id="s-5-1", category="weight_overstated",
                     text="One appellate act is called practice.")
        rows = self.targeted(self.run79_v2(), stored_record(3, new, reviewers=("citations",)), version=3, iteration=3)
        self.assertIn((3, "citations", "s-5-1", "weight_overstated"), self.brief(rows))
        self.assertEqual(4, len(rows))

    def test_a_merged_issue_keeps_its_citations_class_across_a_targeted_pass(self):
        merged = stored("counterarguments", section_id="s-4-4", also=("citations",))
        rows = self.targeted(stored_record(2, self.targeted_blocker("s-6"), merged),
                             stored_record(3, reviewers=("citations",)), version=3, iteration=3)
        self.assertEqual([(2, "citations", "s-4-4", "narrow_trigger")], self.brief(rows))

    def test_without_a_targeted_pass_a_narrow_record_still_supersedes(self):
        rows = self.rows(self.run79_v2(), stored_record(3, reviewers=("citations",)), version=3)
        self.assertNotIn("authority_overstated", [row["category"] for row in rows])


class PolishRecheckReadTest(unittest.TestCase):
    """D-211: the citations re-check of the final polish — a slot and a path, read as a `citations` review."""

    CANONICAL = "reviews/v2-citations_polish.json"
    RESOLUTIONS = [{"id": "om-1", "status": "resolved", "note": "The pinpoint was removed; the id stays."}]

    def work_dir(self) -> Path:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return new_task(Path(holder.name), iteration=2)

    def put(self, work_dir: Path, document: dict, *, published: bool = True) -> dict:
        state_io.write_json_atomic(work_dir / self.CANONICAL, document)
        if published:
            publish(work_dir, self.CANONICAL)
        return state_io.read_state(work_dir)

    def test_the_saved_readiness_document_of_the_run_still_validates(self):
        # No checklist gains an id: the run's own delivery review keeps passing semantic validation.
        document = run_review("final-client-readiness")
        result = review.validate_document(
            "client-readiness", document, current_draft_sha=document["draft_sha"], language="ru"
        )
        self.assertTrue(result["valid"], result["errors"])
        dispositions = [{"id": "om-1", "action": "manual_review", "note": "A lawyer checks the pinpoint."}]
        with_dispositions = dict(document, dispositions=dispositions)
        self.assertEqual([], schema.validate(with_dispositions, "client-readiness"))
        self.assertTrue(review.validate_document("client-readiness", with_dispositions, language="ru")["valid"])

    def test_the_recheck_is_a_slot_and_a_path_not_a_review_kind(self):
        self.assertEqual("citations_polish", review.POLISH_RECHECK)
        self.assertNotIn(review.POLISH_RECHECK, review.KINDS)
        with self.assertRaises(ValueError):
            review.checklist_path(review.POLISH_RECHECK)
        self.assertEqual(self.CANONICAL, review.review_path(2, review.POLISH_RECHECK))

    def test_a_published_recheck_is_validated_as_citations_for_the_post_polish_sha(self):
        work_dir = self.work_dir()
        state = self.put(work_dir, dict(fixture("v1-citations"), iteration=2, resolutions=self.RESOLUTIONS))
        result = review.read_polish_recheck(work_dir, state, 2, DRAFT_SHA)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual((False, False), (result["stub"], result["downgraded"]))
        self.assertEqual(self.RESOLUTIONS, result["document"]["resolutions"])
        stale = review.read_polish_recheck(work_dir, state, 2, "d" * 64)
        self.assertFalse(stale["valid"])
        self.assertTrue(any(error.startswith("stale_draft_sha") for error in stale["errors"]), stale["errors"])

    def test_an_unpublished_missing_or_drifted_recheck_is_no_review(self):
        work_dir = self.work_dir()
        state = self.put(work_dir, fixture("v1-citations"), published=False)
        self.assertEqual(
            ["unpublished_review_file"], review.read_polish_recheck(work_dir, state, 2, DRAFT_SHA)["errors"]
        )
        state = self.put(work_dir, fixture("v1-citations"))
        (work_dir / self.CANONICAL).write_text("{}", encoding="utf-8")
        drifted = review.read_polish_recheck(work_dir, state, 2, DRAFT_SHA)
        self.assertEqual((False, ["output_modified_after_publish"]), (drifted["valid"], drifted["errors"]))
        (work_dir / self.CANONICAL).unlink()
        self.assertEqual(["missing_review_file"], review.read_polish_recheck(work_dir, state, 2, DRAFT_SHA)["errors"])


SHA_V1 = "1" * 64
SHA_V2 = "2" * 64
SHA_OTHER = "f" * 64

CARRY_DRAFT = """# Memo

## 1. Executive summary

The notice is due within one month [[src:uk-gdpr-art-14]].

## 2. Analysis

### 2.1 Group claims

A representative body may bring the claim [[src:dpa-2018-s168]].

### 2.2 Information duties

The controller must also inform the data subject [[src:uk-gdpr-art-13]], as the regulator explains
[[src:ico-right-to-be-informed]].
"""
"""D-214: the draft under review at v2 — two pairs the v1 review saw, one new statute pair, one doctrine pair."""

CARRY_REGISTRY = {
    "uk-gdpr-art-14": "statutes",
    "uk-gdpr-art-13": "statutes",
    "dpa-2018-s168": "statutes",
    "lloyd-v-google": "case_law",
    "ico-right-to-be-informed": "doctrine",
}


def carry_row(source_id: str, section_id: str, status: str) -> dict:
    return {"source_id": source_id, "section_id": section_id, "status": status, "finding_disagrees": False,
            "note": "D-214 fixture row."}


def citations_review(iteration: int, draft_sha: str, rows: list[dict]) -> dict:
    """A valid `citations` review of `iteration` with the given `text_checks` rows."""
    document = fixture("v1-citations")
    document.update(iteration=iteration, draft_sha=draft_sha, text_checks=rows)
    return document


class UncheckedPairsTest(unittest.TestCase):
    """D-214: the cited statute and case-law pairs no earlier text check reached, for the next citations reviewer."""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp(prefix="mf-carry-"))
        self.addCleanup(shutil.rmtree, self.work_dir, ignore_errors=True)
        (self.work_dir / "reviews").mkdir()
        (self.work_dir / "research").mkdir()
        registry = {
            "schema_version": 2,
            "sources": {source_id: {"layer": layer} for source_id, layer in CARRY_REGISTRY.items()},
        }
        (self.work_dir / "research" / "sources.json").write_text(json.dumps(registry), encoding="utf-8")
        self.state = {
            "language": "en",
            "current_iteration": 2,
            "current_draft_sha": SHA_V2,
            "iterations": [{"iteration": 1, "draft_sha": SHA_V1}],
            "published": [],
        }

    def put(self, iteration: int, document: dict, *, publish_it: bool = True) -> Path:
        canonical = review.review_path(iteration, "citations")
        path = self.work_dir / canonical
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        if publish_it:
            self.state["published"].append({"canonical_path": canonical, "sha256": state_io.sha256_file(path)})
        return path

    def v1(self, draft_sha: str = SHA_V1) -> dict:
        return citations_review(
            1,
            draft_sha,
            [carry_row("uk-gdpr-art-14", "s-1", "confirmed"), carry_row("dpa-2018-s168", "s-2-1", "not_reached")],
        )

    def pairs(self, iteration: int = 2, draft: str = CARRY_DRAFT) -> list[tuple]:
        rows = review.unchecked_pairs(self.work_dir, self.state, iteration, draft)
        return [(row["source_id"], row["section_id"], row["reason"]) for row in rows]

    ALL_NEVER = [
        ("uk-gdpr-art-14", "s-1", "never_checked"),
        ("dpa-2018-s168", "s-2-1", "never_checked"),
        ("uk-gdpr-art-13", "s-2-2", "never_checked"),
    ]

    def test_the_unreached_pair_comes_first_then_the_new_statute_pair_and_no_doctrine(self):
        self.put(1, self.v1())
        self.assertEqual(
            [("dpa-2018-s168", "s-2-1", "not_reached"), ("uk-gdpr-art-13", "s-2-2", "never_checked")],
            self.pairs(),
        )

    def test_the_rows_carry_exactly_the_three_fields(self):
        self.put(1, self.v1())
        rows = review.unchecked_pairs(self.work_dir, self.state, 2, CARRY_DRAFT)
        self.assertEqual(
            {"source_id": "dpa-2018-s168", "section_id": "s-2-1", "reason": "not_reached"}, rows[0]
        )

    def test_iteration_one_carries_nothing(self):
        self.put(1, self.v1())
        self.assertEqual([], self.pairs(iteration=1))

    def test_the_list_is_capped_in_section_order(self):
        self.assertEqual(10, limits.CARRY_OVER_MAX)
        sections = "\n".join(f"## {n}. Part {n}\n\nRule [[src:uk-gdpr-art-14]].\n" for n in range(1, 13))
        pairs = self.pairs(draft=f"# Memo\n\n{sections}")
        self.assertEqual([("uk-gdpr-art-14", f"s-{n}", "never_checked") for n in range(1, 11)], pairs)

    def test_a_case_law_pair_is_carried_and_orders_by_section_then_source(self):
        draft = CARRY_DRAFT + "\nThe court awarded nothing [[src:lloyd-v-google]] [[src:dpa-2018-s168]].\n"
        self.put(1, self.v1())
        self.assertEqual(
            [
                ("dpa-2018-s168", "s-2-1", "not_reached"),
                ("dpa-2018-s168", "s-2-2", "never_checked"),
                ("lloyd-v-google", "s-2-2", "never_checked"),
                ("uk-gdpr-art-13", "s-2-2", "never_checked"),
            ],
            self.pairs(draft=draft),
        )

    def test_an_unpublished_review_contributes_nothing(self):
        self.put(1, self.v1(), publish_it=False)
        self.assertEqual(self.ALL_NEVER, self.pairs())

    def test_a_drifted_review_contributes_nothing(self):
        path = self.put(1, self.v1())
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(self.ALL_NEVER, self.pairs())

    def test_a_stub_contributes_nothing(self):
        stub = {"reviewer": "citations", "status": "failed", "reason": "gave up", "iteration": 1,
                "draft_sha": SHA_V1}
        self.put(1, stub)
        self.assertEqual(self.ALL_NEVER, self.pairs())

    def test_a_review_valid_only_for_another_sha_contributes_nothing(self):
        for sha in (SHA_OTHER, SHA_V2):  # SHA_V2: the current draft's sha is never the yardstick
            with self.subTest(sha=sha):
                self.state["published"] = []
                self.put(1, self.v1(draft_sha=sha))
                self.assertEqual(self.ALL_NEVER, self.pairs())

    def test_an_invalid_review_contributes_nothing(self):
        document = self.v1()
        document["checklist"] = document["checklist"][:-1]
        self.put(1, document)
        self.assertEqual(self.ALL_NEVER, self.pairs())

    def test_a_review_valid_for_its_own_sha_counts_while_the_current_sha_differs(self):
        self.put(1, self.v1())
        self.assertNotEqual(self.state["current_draft_sha"], self.state["iterations"][0]["draft_sha"])
        self.assertEqual(("dpa-2018-s168", "s-2-1", "not_reached"), self.pairs()[0])

    def test_the_targeted_iteration_s_review_counts_and_its_row_is_the_latest(self):
        self.put(1, self.v1())
        self.state["iterations"].append({"iteration": 2, "draft_sha": SHA_V2})
        self.state["targeted_fix"] = {"iteration": 2, "reviewers": ["citations"]}
        self.put(2, citations_review(2, SHA_V2, [carry_row("dpa-2018-s168", "s-2-1", "confirmed")]))
        self.assertEqual([("uk-gdpr-art-13", "s-2-2", "never_checked")], self.pairs(iteration=3))

    def test_a_later_not_reached_row_overrides_an_earlier_check(self):
        self.put(1, self.v1())
        self.state["iterations"].append({"iteration": 2, "draft_sha": SHA_V2})
        self.put(2, citations_review(2, SHA_V2, [carry_row("uk-gdpr-art-14", "s-1", "not_reached")]))
        self.assertEqual(
            [
                ("uk-gdpr-art-14", "s-1", "not_reached"),
                ("dpa-2018-s168", "s-2-1", "not_reached"),
                ("uk-gdpr-art-13", "s-2-2", "never_checked"),
            ],
            self.pairs(iteration=3),
        )

    def test_the_polish_recheck_is_never_read(self):
        self.put(1, self.v1())
        canonical = review.review_path(1, review.POLISH_RECHECK)
        path = self.work_dir / canonical
        path.write_text(
            json.dumps(citations_review(1, SHA_V1, [carry_row("uk-gdpr-art-13", "s-2-2", "confirmed")])),
            encoding="utf-8",
        )
        self.state["published"].append({"canonical_path": canonical, "sha256": state_io.sha256_file(path)})
        self.assertIn(("uk-gdpr-art-13", "s-2-2", "never_checked"), self.pairs())

    def test_a_critical_pair_comes_before_earlier_sections_of_supporting_ones(self):
        """D-253, run 84: ten slots in section order ran out at 4.2; art 14 (critical, 5.2) was never checked."""
        registry = {"schema_version": 2, "sources": {
            "ico-guide": {"layer": "statutes", "tier": "supporting"},
            "uk-gdpr-art-14": {"layer": "statutes", "tier": "critical"},
        }}
        (self.work_dir / "research" / "sources.json").write_text(json.dumps(registry), encoding="utf-8")
        sections = "\n".join(f"## {n}. Part {n}\n\nRule [[src:ico-guide]].\n" for n in range(1, 12))
        draft = f"# Memo\n\n{sections}\n## 12. Last\n\nRule [[src:uk-gdpr-art-14]].\n"
        pairs = self.pairs(draft=draft)
        self.assertEqual(("uk-gdpr-art-14", "s-12", "never_checked"), pairs[0])
        self.assertEqual(10, len(pairs))

    def test_an_unreached_supporting_pair_still_precedes_a_new_critical_one(self):
        # The `not_reached` rows of the last review stay first (D-214); the tier orders inside each reason.
        tiers = {"dpa-2018-s168": "supporting", "uk-gdpr-art-13": "critical", "uk-gdpr-art-14": "supporting"}
        registry = {"schema_version": 2, "sources": {
            source_id: {"layer": layer, "tier": tiers.get(source_id, "supporting")}
            for source_id, layer in CARRY_REGISTRY.items()
        }}
        (self.work_dir / "research" / "sources.json").write_text(json.dumps(registry), encoding="utf-8")
        self.put(1, self.v1())
        self.assertEqual(
            [("dpa-2018-s168", "s-2-1", "not_reached"), ("uk-gdpr-art-13", "s-2-2", "never_checked")],
            self.pairs(),
        )


class AdjacentLimbBlockerTest(unittest.TestCase):
    """D-214 (gate R1-5): a sibling limb left out is a CIT-02 fail, so its issue must be a `source_drift` blocker."""

    ISSUE = {
        "category": "sibling_limb_omitted",
        "section_id": "s-4-2",
        "issue": "4.2 relies on Art 14(3)(b) and leaves out the one-month outer limit of Art 14(3)(a) beside it.",
        "suggestion": "State the one-month limit of Art 14(3)(a) the facts engage, or qualify the timing as open.",
        "checklist_id": "CIT-02",
        "issue_category": "source_drift",
    }

    def document(self, severity: str) -> dict:
        document = fixture("v1-citations")
        cit02 = next(row for row in document["checklist"] if row["id"] == "CIT-02")
        cit02.update({"pass": False, "evidence": "4.2 omits the Art 14(3)(a) limit in the same paragraph."})
        document["issues"] = [dict(self.ISSUE, severity=severity)]
        document["verdict"] = "needs_revision"
        return document

    def test_a_source_drift_blocker_passes(self):
        result = review.validate_document("citations", self.document("blocker"))
        self.assertEqual([], result["errors"])
        self.assertEqual(1, result["blockers"])

    def test_the_same_issue_as_a_major_fails_the_hard_fail_rule(self):
        result = review.validate_document("citations", self.document("major"))
        self.assertFalse(result["valid"])
        self.assertIn("missing_blocker_for_hard_fail: CIT-02", result["errors"])


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


class RecordBannerOnceTest(unittest.TestCase):
    """D-248, run 84: the regate and the exit both recorded `currency_blocking`, so the header printed it twice."""

    # Two records of one run are minutes apart (run 84: 08:14:59 and 08:30:45); pinning the clock keeps
    # two calls inside one millisecond from hiding the duplicate.
    TIMES = ["2026-09-25T08:14:59.511Z", "2026-09-25T08:30:45.764Z"]

    def test_the_same_banner_recorded_twice_is_one_row(self):
        state = {}
        with mock.patch.object(events, "utc_now", side_effect=self.TIMES):
            review.record_banner(state, "currency_blocking_issues", count=1)
            review.record_banner(state, "currency_blocking_issues", count=1)
        self.assertEqual(1, len(state["fallback_banners"]))

    def test_the_same_condition_with_other_params_is_kept(self):
        state = {}
        with mock.patch.object(events, "utc_now", side_effect=self.TIMES):
            review.record_banner(state, "currency_blocking_issues", count=1)
            review.record_banner(state, "currency_blocking_issues", count=2)
        self.assertEqual(2, len(state["fallback_banners"]))


if __name__ == "__main__":
    unittest.main()
