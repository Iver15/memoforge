"""The decision-brief driver `mf brief next|report` (plan 75A, D-224)."""

from __future__ import annotations

import copy
import html
import json
import os
import re
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _brief import CLEAN_BRIEF, FinishedTask, approving_fidelity, approving_form  # noqa: E402
from _pipeline import temp_root  # noqa: E402
from memoforge import (  # noqa: E402
    brief,
    brief_lint,
    cli,
    dispatch,
    events,
    i18n,
    limits,
    schema,
    state_io,
    stepctx,
)
from memoforge.docx import fallback, renderer, validate  # noqa: E402
from test_brief_review import fidelity_issue, form_issue, graded  # noqa: E402

REFUSED = "ui.brief.refused."


def refused_text(code: str, language: str = "en", **fmt) -> str:
    return i18n.t(language, REFUSED + code, **fmt)


class BriefCase(unittest.TestCase):
    """Every case: the task files have the same bytes after the brief ran as before."""

    def setUp(self) -> None:
        self.tasks: list[FinishedTask] = []

    def task(self, **kwargs) -> FinishedTask:
        finished = FinishedTask(self, **kwargs)
        self.tasks.append(finished)
        return finished

    def tearDown(self) -> None:
        for finished in self.tasks:
            if finished.snapshot is not None:
                self.assertEqual(finished.snapshot, finished.task_files(), "the brief wrote a task file")

    # -- shared helpers -----------------------------------------------------
    def promoted_writer(self, finished: FinishedTask, text: str = CLEAN_BRIEF) -> tuple[dict, dict]:
        """Issue the writer, let it finish and report; returns `(writer action, next action)`."""
        action = finished.next()
        self.assertEqual("dispatch", action["kind"], action)
        finished.write_fixture_writer(action, text)
        self.assertTrue(finished.report(action, slot="writer", status="ok")["accepted"])
        return action, finished.next()

    def finish_run(self, finished: FinishedTask, outcome: str = "clean") -> dict:
        finished.next()

        def mutate(run: dict) -> None:
            run["outcome"] = outcome
            run["step"] = "finished"

        return brief.write_run(finished.W, mutate)


def set_status(finished: FinishedTask, final_status: str, **fields) -> None:
    def mutate(state: dict) -> None:
        state["final_status"] = final_status
        state.update(fields)

    finished.write_state(mutate)


def add_version(finished: FinishedTask, version: int, text: str, *, checked: bool) -> str:
    path = finished.W / "drafts" / f"v{version}.md"
    path.write_text(text, encoding="utf-8")
    sha = state_io.sha256_file(path)

    def mutate(state: dict) -> None:
        row = copy.deepcopy(state["draft_versions"][0])
        row.update({"version": version, "path": f"drafts/v{version}.md", "sha256": sha,
                    "lint_clean": checked, "citations_clean": checked})
        state["draft_versions"].append(row)

    finished.write_state(mutate)
    return sha


BLOCKER = {
    "severity": "blocker",
    "category": "unsupported_claim",
    "section_id": "s-3",
    "issue": "The tax carve-out is asserted without a source.",
    "suggestion": "Cite the tax provision.",
    "source_reviewer": "citations",
}


def open_major(row_id: str = "om-1", **fields) -> dict:
    row = {
        "id": row_id,
        "class": "logic",
        "reviewer": "logic",
        "section_id": "s-3",
        "category": "reasoning",
        "issue_category": None,
        "issue": "The application skips the purpose test.",
        "issue_client": "The purpose test is not applied.",
        "suggestion": "Apply the purpose test.",
        "from_iteration": 1,
        "origin": "loop",
        "status": "left",
    }
    row.update(fields)
    return row


class StartTest(BriefCase):
    def test_clean_task_dispatches_the_writer(self):
        finished = self.task()
        action = finished.next()
        self.assertEqual("dispatch", action["kind"])
        self.assertFalse(action["parallel"])
        [agent] = action["agents"]
        self.assertEqual("writer", agent["slot"])
        self.assertEqual("memoforge:brief-writer", agent["subagent_type"])
        self.assertEqual(finished.state()["config"]["writer_model"], agent["model"])
        prompt = agent["prompt"]
        self.assertIn("`drafts/v1.md`", prompt)
        self.assertIn("`brief/open-issues.json`", prompt)
        self.assertIn(brief_lint.writer_labels("en"), prompt)
        self.assertNotIn("${", prompt)

        run = finished.run()
        self.assertEqual([], schema.validate(run, "brief-state"))
        self.assertEqual(brief.step_id(run["run_id"], 1), action["step_id"])
        self.assertTrue(action["step_id"].startswith(f"b-{run['run_id'][:8]}-"))
        self.assertEqual(run["run_id"], action["run_id"])
        self.assertEqual(1, action["attempt"])
        self.assertEqual("write", run["step"])
        self.assertIsNone(run["confirmed_unclean"])
        self.assertEqual("drafts/v1.md", run["input"]["draft_path"])
        self.assertEqual(finished.state()["delivered_draft_sha"], run["input"]["draft_sha"])
        self.assertEqual(
            f"steps/{action['step_id']}/a1/writer/v1.md", agent["expected_outputs"][0]["work_path"]
        )
        self.assertEqual([], state_io.read_json(finished.W / "brief" / "open-issues.json"))

        issued = [row for row in events.read_events(finished.W) if row["event"] == "step_issued"]
        self.assertEqual("brief", issued[-1]["data"]["run"])
        self.assertEqual(run["run_id"], issued[-1]["data"]["run_id"])
        self.assertEqual("done", issued[-1]["phase"])
        self.assertEqual(action["step_id"], issued[-1]["step_id"])

    def test_the_cli_registers_next_and_report(self):
        parser = cli.build_parser()
        args = parser.parse_args(["brief", "next", "--workdir", "W"])
        self.assertIs(brief.run_next, args.func)
        args = parser.parse_args(
            ["brief", "report", "--workdir", "W", "--run", "r", "--step", "s", "--attempt", "1", "--text", "yes"]
        )
        self.assertIs(brief.run_report, args.func)

    def test_a_folder_that_is_no_task_is_refused_untouched(self):
        empty = temp_root(self) / "not-a-task"
        empty.mkdir()
        action = brief.run_next(cli.build_parser().parse_args(["brief", "next", "--workdir", str(empty)]))
        self.assertEqual("no_task", action["reason"])
        self.assertEqual([], list(empty.iterdir()))

    def test_report_takes_exactly_one_answer(self):
        finished = self.task()
        action = finished.next()
        result = finished.report(action, slot="writer", status="ok", text="yes")
        self.assertFalse(result["accepted"])
        self.assertTrue(result["errors"][0].startswith("report_mode"))
        result = finished.report(action)
        self.assertFalse(result["accepted"])


class PreflightTest(BriefCase):
    """Each case on its own fresh finished task: no earlier brief run in its work dir."""

    def assert_refused(self, finished: FinishedTask, code: str) -> dict:
        action = finished.next()
        self.assertEqual("done", action["kind"], action)
        self.assertEqual("refused", action["outcome"])
        self.assertEqual(code, action["reason"])
        self.assertEqual(refused_text(code), action["text"])
        self.assertFalse(action["present"])
        self.assertFalse((finished.W / "brief").exists(), "a refused preflight writes nothing")
        return action

    def test_preflight_without_delivered_sha_falls_back(self):
        finished = self.task()
        finished.write_state(lambda state: state.pop("delivered_draft_sha"))
        self.assertEqual("dispatch", finished.next()["kind"])

    def test_draft_changed_after_export_is_refused(self):
        finished = self.task()
        path = finished.W / "drafts" / "v1.md"
        path.write_bytes(path.read_bytes() + b"\nA line added after delivery.\n")
        self.assert_refused(finished, "draft_changed_after_export")

    def test_draft_changed_before_finalization_builds(self):
        """Task 1: finalize delivered the bytes on disk, and `delivered_draft_sha` is theirs."""
        finished = self.task()
        path = finished.W / "drafts" / "v1.md"
        path.write_bytes(path.read_bytes() + b"\nA line added before finalization.\n")
        sha = state_io.sha256_file(path)
        finished.write_state(lambda state: state.update({"delivered_draft_sha": sha}))
        action = finished.next()
        self.assertEqual("dispatch", action["kind"])
        self.assertEqual(sha, finished.run()["input"]["draft_sha"])

    def test_export_pin_builds_from_the_pinned_version(self):
        finished = self.task()
        v1_sha = finished.state()["draft_versions"][0]["sha256"]
        add_version(finished, 2, (finished.W / "drafts" / "v1.md").read_text(encoding="utf-8") + "\nMore.\n",
                    checked=True)

        def pin(state: dict) -> None:
            state.pop("delivered_draft_sha")
            state["export_pin"] = {"version": 1, "sha256": v1_sha}

        finished.write_state(pin)
        self.assertEqual("dispatch", finished.next()["kind"])
        run = finished.run()
        self.assertEqual("drafts/v1.md", run["input"]["draft_path"])
        self.assertEqual(1, run["input"]["draft_version"])
        self.assertEqual(v1_sha, run["input"]["draft_sha"])

    def test_forced_exit_on_an_earlier_version_builds_the_delivered_one(self):
        finished = self.task()
        v2 = add_version(finished, 2, (finished.W / "drafts" / "v1.md").read_text(encoding="utf-8") + "\nV2.\n",
                         checked=True)
        set_status(finished, "forced_exit_on_v1_with_remaining_issues", delivered_draft_sha=v2,
                   remaining_blocking_issues=[BLOCKER])
        action = finished.next()
        self.assertEqual("gate-auq", action["kind"])
        run = finished.run()
        self.assertEqual("drafts/v2.md", run["input"]["draft_path"])
        self.assertEqual("forced_exit_on_v1_with_remaining_issues", run["input"]["final_status"])

    def test_failed_polish_on_a_later_version_builds_the_delivered_one(self):
        finished = self.task()
        add_version(finished, 2, "# Polished\n\n## 1. Executive summary\n\nText.\n", checked=False)
        set_status(finished, "manual_review_required_on_v2")
        action = finished.next()
        self.assertEqual("gate-auq", action["kind"])
        self.assertEqual("drafts/v1.md", finished.run()["input"]["draft_path"])

    def test_no_draft_behind_the_deliverable_is_no_memo(self):
        finished = self.task()
        finished.write_state(lambda state: state.update({"delivered_draft_sha": None}))
        self.assert_refused(finished, "no_memo")

    def test_a_task_that_is_not_done_is_refused(self):
        finished = self.task()
        finished.write_state(lambda state: state.update({"current_phase": "export"}))
        self.assert_refused(finished, "task_not_done")

    def test_a_tampered_source_pack_is_refused(self):
        finished = self.task()
        pack = finished.W / "research" / "source-pack.json"
        pack.write_bytes(pack.read_bytes().replace(b"{", b"{ ", 1))
        self.assert_refused(finished, "pack_changed_after_export")

    def test_a_tampered_published_registry_is_refused(self):
        """`research/sources.json` counts as soon as `published[]` knows it (as in `renderer.load_index`)."""
        finished = self.task()
        registry = finished.W / "research" / "sources.json"
        sha = state_io.sha256_file(registry)

        def publish(state: dict) -> None:
            row = dict(state["published"][-1])
            row.update({"canonical_path": "research/sources.json", "sha256": sha})
            state["published"].append(row)

        finished.write_state(publish)
        registry.write_bytes(registry.read_bytes().replace(b"{", b"{ ", 1))
        self.assert_refused(finished, "pack_changed_after_export")


class ClassifyTest(BriefCase):
    def base(self) -> dict:
        if not hasattr(self, "_base"):
            self._base = self.task().state()
        return copy.deepcopy(self._base)

    def classify(self, final_status: str, **fields) -> dict:
        state = self.base()
        state["final_status"] = final_status
        state.update(fields)
        return brief.classify_status(state)

    def test_approved_and_client_ready_are_clean(self):
        for status in ("client_ready_on_v2", "approved_on_v1"):
            with self.subTest(status=status):
                result = self.classify(status)
                self.assertTrue(result["clean"])
                self.assertEqual(status, result["status"])
                self.assertEqual([], result["open_issues"])

    def test_every_other_status_is_unclean(self):
        for status in (
            "accepted_early_on_v1",
            "manual_review_required_on_v2",
            "forced_exit_on_v1_with_remaining_issues",
            "delivered",
        ):
            with self.subTest(status=status):
                self.assertFalse(self.classify(status)["clean"])

    def test_a_left_major_makes_it_unclean_and_is_listed(self):
        result = self.classify("client_ready_on_v1", open_substance_majors=[open_major("om-3")])
        self.assertFalse(result["clean"])
        self.assertEqual(
            [{"id": "om-3", "section_id": "s-3", "class": "logic", "severity": "major",
              "text": "The purpose test is not applied."}],
            result["open_issues"],
        )

    def test_a_citations_row_already_among_the_blockers_is_listed_once(self):
        row = open_major("om-1", **{"class": "citations", "reviewer": "citations", "status": "unresolved",
                                    "issue_client": None})
        result = self.classify(
            "manual_review_required_on_v1", remaining_blocking_issues=[BLOCKER], open_substance_majors=[row]
        )
        self.assertFalse(result["clean"])
        self.assertEqual(
            [{"id": "ob-1", "section_id": "s-3", "class": "citations", "severity": "blocker",
              "text": BLOCKER["issue"]}],
            result["open_issues"],
        )

    def test_open_issues_are_written_for_the_writer(self):
        finished = self.task()
        set_status(finished, "client_ready_on_v1", open_substance_majors=[open_major("om-2")])
        action = finished.next()
        self.assertEqual("gate-auq", action["kind"])
        written = state_io.read_json(finished.W / "brief" / "open-issues.json")
        self.assertEqual(["om-2"], [row["id"] for row in written])
        self.assertEqual(written, action["open_issues"])


class GateTest(BriefCase):
    def unclean(self, **kwargs) -> FinishedTask:
        finished = self.task(**kwargs)
        set_status(finished, "manual_review_required_on_v1", remaining_blocking_issues=[BLOCKER])
        return finished

    def test_an_unclean_memo_asks_first(self):
        finished = self.unclean()
        action = finished.next()
        self.assertEqual("gate-auq", action["kind"])
        self.assertEqual(0, action["generation"])
        [question] = action["questions"]
        status = fallback.status_name("manual_review_required_on_v1", "en")
        self.assertEqual(i18n.t("en", "ui.brief.gate_question", status=status, count=1), question["question"])
        self.assertEqual(i18n.t("en", "ui.brief.gate_header"), question["header"])
        self.assertEqual(
            [i18n.t("en", "ui.brief.option_yes"), i18n.t("en", "ui.brief.option_no")],
            [option["label"] for option in question["options"]],
        )
        self.assertEqual(i18n.t("en", "ui.brief.gate_text", status=status, count=1), action["text_fallback"])
        self.assertEqual("ob-1", action["open_issues"][0]["id"])
        self.assertEqual("status_gate", finished.run()["step"])

    def test_no_answer_switches_to_the_text_gate(self):
        finished = self.unclean()
        action = finished.next()
        self.assertTrue(finished.report(action, status="no_answer")["accepted"])
        text_gate = finished.next()
        self.assertEqual("gate-text", text_gate["kind"])
        self.assertTrue(text_gate["end_turn"])
        self.assertEqual(action["text_fallback"], text_gate["text"])
        self.assertTrue(text_gate["text"].endswith("Reply yes or no."))
        self.assertEqual(action["step_id"], text_gate["step_id"])

    def test_a_russian_yes_confirms_the_unclean_memo(self):
        finished = self.unclean(language="ru", ui_language="ru")
        gate = finished.next()
        self.assertEqual("gate-auq", gate["kind"])
        self.assertIn(i18n.t("ru", "ui.brief.gate_header"), gate["questions"][0]["header"])
        result = finished.report(gate, text="да")
        self.assertTrue(result["accepted"], result)
        action = finished.next()
        self.assertEqual("dispatch", action["kind"])
        self.assertEqual("writer", action["agents"][0]["slot"])
        self.assertIs(True, finished.run()["confirmed_unclean"])

    def test_an_unrecognised_reply_keeps_the_gate_open(self):
        finished = self.unclean()
        gate = finished.next()
        result = finished.report(gate, text="maybe")
        self.assertFalse(result["accepted"])
        self.assertEqual(["unrecognised_answer"], result["errors"])
        again = finished.next()
        self.assertEqual("gate-text", again["kind"])
        self.assertEqual("status_gate", finished.run()["step"])

    def test_no_declines(self):
        finished = self.unclean()
        gate = finished.next()
        answers = json.dumps({gate["questions"][0]["question"]: i18n.t("en", "ui.brief.option_no")})
        result = finished.report(gate, answers=answers)
        self.assertTrue(result["accepted"])
        self.assertEqual("done", result["kind"])
        self.assertEqual("declined", result["outcome"])
        self.assertEqual(i18n.t("en", "ui.brief.declined"), result["text"])
        run = finished.run()
        self.assertEqual("declined", run["outcome"])
        self.assertEqual("finished", run["step"])


class DispatchTest(BriefCase):
    def test_a_promoted_brief_goes_through_lint_to_review(self):
        finished = self.task()
        writer, review = self.promoted_writer(finished)
        run = finished.run()
        self.assertEqual(1, len(run["versions"]))
        promoted = finished.W / "brief" / "v1.md"
        self.assertEqual(CLEAN_BRIEF.encode("utf-8"), promoted.read_bytes())
        self.assertEqual(state_io.sha256_file(promoted), run["versions"][0]["sha256"])
        self.assertEqual(0, run["versions"][0]["lint_findings"])
        self.assertTrue((finished.W / "brief" / "lint-v1.json").is_file())

        self.assertEqual("dispatch", review["kind"])
        self.assertTrue(review["parallel"])
        slots = {agent["slot"]: agent for agent in review["agents"]}
        self.assertEqual({"fidelity", "form"}, set(slots))
        sha = run["versions"][0]["sha256"]
        blocks = brief_lint.section_list(brief_lint.parse_brief(CLEAN_BRIEF, "en"))
        self.assertEqual("memoforge:brief-fidelity-reviewer", slots["fidelity"]["subagent_type"])
        self.assertIn(sha, slots["fidelity"]["prompt"])
        self.assertIn(blocks, slots["fidelity"]["prompt"])
        self.assertIn(dispatch.lib_path("lib", "checklists", "brief-fidelity.json"), slots["fidelity"]["prompt"])
        self.assertEqual("memoforge:form-reviewer", slots["form"]["subagent_type"])
        # D-228: every brief reviewer runs on Opus; the memo pipeline's form reviewer stays on Sonnet.
        self.assertEqual(("opus", "opus"), (slots["fidelity"]["model"], slots["form"]["model"]))
        self.assertEqual("sonnet", dispatch.AGENT_MODELS["form-reviewer"]["model"])
        self.assertIn(brief.BRIEF_ROLE, slots["form"]["prompt"])
        self.assertIn(blocks + "; use document only for the whole brief.", slots["form"]["prompt"])
        self.assertIn(dispatch.lib_path("lib", "checklists", "brief-form.json"), slots["form"]["prompt"])
        self.assertIn(dispatch.lib_path("lib", "prose-style.md"), slots["form"]["prompt"])
        self.assertEqual("brief/reviews/r0-fidelity.json", slots["fidelity"]["expected_outputs"][0]["canonical"])
        self.assertNotIn("${", slots["form"]["prompt"])

        finished.write_fixture_reviews(review)
        for slot in ("fidelity", "form"):
            self.assertTrue(finished.report(review, slot=slot, status="ok")["accepted"])
        run = finished.run()
        self.assertEqual(["fidelity", "form"], [row["slot"] for row in run["reviews"]])
        self.assertTrue(all(row["valid"] and row["draft_sha"] == sha for row in run["reviews"]))
        self.assertEqual("review_merge", run["step"])
        done = finished.next()
        self.assertEqual("done", done["kind"], done)
        self.assertEqual("clean", finished.run()["outcome"])

    def test_a_lint_finding_sends_the_seeded_writer_back(self):
        finished = self.task()
        flawed = CLEAN_BRIEF.replace("before the next audit.\n", "before the next audit, as the memorandum says.\n")
        _, action = self.promoted_writer(finished, flawed)
        self.assertEqual("dispatch", action["kind"])
        run = finished.run()
        self.assertEqual("lint_fix", run["step"])
        self.assertEqual(0, run["lint_fix_done_round"])
        self.assertEqual("brief/lint-v1.json", run["instructions_path"])
        self.assertGreater(run["versions"][0]["lint_findings"], 0)
        agent = action["agents"][0]
        seed = finished.W / agent["expected_outputs"][0]["work_path"]
        self.assertEqual("brief/v2.md", agent["expected_outputs"][0]["canonical"])
        self.assertEqual(flawed.encode("utf-8"), seed.read_bytes(), "the writer edits a copy of v1")
        self.assertIn("brief/lint-v1.json", agent["prompt"])
        self.assertIn(agent["expected_outputs"][0]["work_path"], agent["prompt"])

        # One lint fix per round: a v2 that still fails goes on to review.
        finished.write_fixture_writer(action, flawed)
        finished.report(action, slot="writer", status="ok")
        following = finished.next()
        self.assertEqual("review", finished.run()["step"])
        self.assertEqual({"fidelity", "form"}, {agent["slot"] for agent in following["agents"]})

    def test_length_alone_goes_to_review_without_a_lint_fix(self):
        """D-227 (F8-3): B-01 alone spends no writer pass; the merge still revises on it."""
        finished = self.task()
        _, action = self.promoted_writer(finished, padded_brief(limits.DECISION_BRIEF_SOFT_CAP + 100))
        run = finished.run()
        report = state_io.read_json(finished.W / "brief" / "lint-v1.json")
        self.assertEqual(["B-01"], [row["rule"] for row in report["findings"]])
        self.assertEqual("review", run["step"])
        self.assertIsNone(run["lint_fix_done_round"])
        self.assertIsNone(run["instructions_path"])
        self.assertEqual({"fidelity", "form"}, {agent["slot"] for agent in action["agents"]})

    def test_length_with_another_blocking_finding_still_sends_a_lint_fix(self):
        finished = self.task()
        text = padded_brief(limits.DECISION_BRIEF_SOFT_CAP + 100).replace("Risk: medium.", "Risk: high.")
        _, action = self.promoted_writer(finished, text)
        report = state_io.read_json(finished.W / "brief" / "lint-v1.json")
        self.assertEqual({"B-01", "B-05"}, {row["rule"] for row in report["findings"]})
        run = finished.run()
        self.assertEqual("lint_fix", run["step"])
        self.assertEqual("brief/lint-v1.json", run["instructions_path"])
        self.assertEqual(["writer"], [agent["slot"] for agent in action["agents"]])


class SlotCheckTest(BriefCase):
    """Fix round 1: a finished attempt with a bad marker or a bad output fails its slot and says why."""

    def test_a_marker_with_a_malformed_output_map_fails_the_slot(self):
        finished = self.task()
        action = finished.next()
        finished.write_output(action, "writer", CLEAN_BRIEF)
        marker = finished.W / "steps" / action["step_id"] / "a1" / "writer" / "done.json"
        state_io.write_json_atomic(marker, {
            "task_id": finished.state()["task_id"], "step_id": action["step_id"], "attempt": 1,
            "slot": "writer", "input_sha": {}, "output_sha": 1,
        })
        again = finished.next()  # the autoclose path: no TypeError
        self.assertEqual(2, again["attempt"])
        prompt = again["agents"][0]["prompt"]
        self.assertIn("output_sha", prompt.split("previous attempt errors to fix:", 1)[1])
        self.assertEqual([], finished.run()["versions"])

    def test_output_that_is_not_utf8_is_never_promoted(self):
        finished = self.task()
        action = finished.next()
        target = finished.write_output(action, "writer", CLEAN_BRIEF)
        target.write_bytes(CLEAN_BRIEF.encode("utf-8") + b"\n\xff\xfe broken bytes\n")
        finished.agent_done(action, "writer")
        result = finished.report(action, slot="writer", status="ok")
        self.assertTrue(result["accepted"])
        self.assertEqual("fail", result["status"])
        self.assertTrue(any(error.startswith("invalid_utf8[") for error in result["slot_errors"]), result)
        self.assertEqual([], finished.run()["versions"])
        again = finished.next()
        self.assertEqual(2, again["attempt"])
        self.assertIn("invalid_utf8[", again["agents"][0]["prompt"])

    def test_a_finished_unreported_output_without_sections_tells_the_next_attempt(self):
        finished = self.task()
        action = finished.next()
        finished.write_fixture_writer(action, "# A title only\n\nNo sections at all.\n")
        again = finished.next()
        self.assertEqual(2, again["attempt"])
        errors = again["agents"][0]["prompt"].split("previous attempt errors to fix:", 1)[1].splitlines()[0]
        self.assertIn("draft_without_sections", errors)

    def test_the_dispatch_line_follows_the_interface_language(self):
        finished = self.task(language="ru", ui_language="ru")
        action = finished.next()
        expected = i18n.t("ru", "ui.brief.dispatch_line", step=i18n.t("ru", "ui.brief.steps.write"), attempt=1)
        self.assertEqual(expected, action["chat_line"])
        self.assertIn("Справка", action["chat_line"])
        self.assertNotIn("writer", action["chat_line"])


class ExhaustionTest(BriefCase):
    def test_exhausted_reviewers_go_to_the_merge_with_what_parsed(self):
        """A review that parsed but failed its schema is kept; one never produced is recorded with no sha."""
        finished = self.task()
        _, review = self.promoted_writer(finished)
        sha = finished.run()["versions"][-1]["sha256"]
        for attempt in (1, 2):
            self.assertEqual(attempt, review["attempt"])
            broken = {"reviewer": "brief_fidelity", "draft_sha": sha, "checklist": [], "issues": []}
            finished.write_output(review, "fidelity", json.dumps(broken))
            finished.agent_done(review, "fidelity")
            result = finished.report(review, slot="fidelity", status="ok")
            self.assertEqual("fail", result["status"])
            self.assertTrue(result["slot_errors"])
            finished.report(review, slot="form", status="fail")
            review = finished.next()
            if attempt == 1:
                self.assertEqual({"fidelity", "form"}, {agent["slot"] for agent in review["agents"]})
                form_prompt = next(agent["prompt"] for agent in review["agents"] if agent["slot"] == "form")
                self.assertIn("previous attempt errors to fix: agent_failed", form_prompt)
        # D-225: what parsed is merged fail-closed; the missing checks send the brief back in round 0.
        self.assertEqual("dispatch", review["kind"])
        self.assertEqual("revise", finished.run()["step"])
        rows = {row["slot"]: row for row in finished.run()["reviews"]}
        self.assertIsNone(rows["form"]["sha256"])
        self.assertFalse(rows["form"]["valid"])
        self.assertEqual(["agent_failed"], rows["form"]["errors"])
        self.assertFalse(rows["fidelity"]["valid"])
        promoted = finished.W / rows["fidelity"]["path"]
        self.assertEqual(state_io.sha256_file(promoted), rows["fidelity"]["sha256"])
        self.assertEqual(broken, state_io.read_json(promoted))

    def test_an_exhausted_lint_fix_keeps_the_last_good_version(self):
        finished = self.task()
        flawed = CLEAN_BRIEF.replace("before the next audit.\n", "before the next audit, as the memorandum says.\n")
        _, action = self.promoted_writer(finished, flawed)
        for _ in (1, 2):
            finished.report(action, slot="writer", status="fail")
            action = finished.next()
        run = finished.run()
        self.assertEqual("unverified", run["verdict"])
        self.assertEqual(["writer_failed"], run["outcome_reasons"])
        self.assertEqual(1, len(run["versions"]))
        self.assertEqual("done", action["kind"])
        self.assertEqual("unverified", action["outcome"])


class ResumeTest(BriefCase):
    def test_second_invocation_resumes_and_autocloses(self):
        finished = self.task()
        action = finished.next()
        finished.write_fixture_writer(action)
        following = finished.next()
        run = finished.run()
        self.assertEqual(1, len(run["versions"]))
        self.assertFalse((finished.W / "steps" / action["step_id"] / "a2").exists())
        self.assertIn(run["step"], ("lint_fix", "review"))
        self.assertEqual("dispatch", following["kind"])
        self.assertNotEqual(action["step_id"], following["step_id"])
        autoclosed = [row for row in events.read_events(finished.W) if row["event"] == "step_autoclosed"]
        self.assertEqual(action["step_id"], autoclosed[-1]["step_id"])
        self.assertEqual("brief", autoclosed[-1]["data"]["run"])

    def test_two_live_routers_promote_once(self):
        finished = self.task()
        action = finished.next()
        finished.write_fixture_writer(action)
        finished.next()
        finished.next()
        run = finished.run()
        self.assertEqual(1, len(run["versions"]))
        self.assertFalse((finished.W / "steps" / action["step_id"] / "a2").exists())
        before = (finished.W / "brief" / "state.json").read_bytes()
        late = finished.report(action, slot="writer", status="ok")
        self.assertTrue(late.get("already_reported") or late.get("errors") == ["stale_report"], late)
        self.assertEqual(before, (finished.W / "brief" / "state.json").read_bytes())

    def test_concurrent_start_after_a_finished_run_archives_once(self):
        finished = self.task()
        first = self.finish_run(finished)
        one = finished.next()
        two = finished.next()
        self.assertEqual(one["run_id"], two["run_id"])
        self.assertNotEqual(first["run_id"], one["run_id"])
        previous = state_io.read_json(finished.W / "brief" / "previous" / "state.json")
        self.assertEqual(first["run_id"], previous["run_id"])
        self.assertFalse((finished.W / "brief" / "previous" / "previous").exists())
        self.assertFalse((finished.W / "brief.archiving").exists())

    def test_a_brief_edited_after_promotion_is_refused(self):
        finished = self.task()
        action = finished.next()
        finished.write_fixture_writer(action)
        finished.report(action, slot="writer", status="ok")
        promoted = finished.W / "brief" / "v1.md"
        promoted.write_text(CLEAN_BRIEF + "\nAdded by hand.\n", encoding="utf-8")
        done = finished.next()
        self.assertEqual("done", done["kind"])
        self.assertEqual("refused", done["outcome"])
        self.assertEqual("brief_changed_on_disk", done["reason"])
        self.assertEqual(refused_text("brief_changed_on_disk"), done["text"])
        run = finished.run()
        self.assertEqual("refused", run["outcome"])
        self.assertEqual(["brief_changed_on_disk"], run["outcome_reasons"])
        self.assertIsNone(run["deliverable_path"])
        self.assertEqual([], run["published"])
        self.assertFalse((finished.W / "brief" / "lint-v1.json").exists())

    def test_a_lost_attempt_is_reissued_and_its_late_output_ignored(self):
        finished = self.task()
        first = finished.next()
        second = finished.next()
        self.assertEqual(first["step_id"], second["step_id"])
        self.assertEqual(2, second["attempt"])
        old_path = first["agents"][0]["expected_outputs"][0]["work_path"]
        new_path = second["agents"][0]["expected_outputs"][0]["work_path"]
        self.assertIn("/a2/", new_path)
        self.assertNotEqual(old_path, new_path)
        self.assertIn("previous attempt errors to fix: none", second["agents"][0]["prompt"])

        stale = finished.report(first, slot="writer", status="ok")
        self.assertEqual({"accepted": False, "errors": ["stale_report"],
                          "message": i18n.t("en", "ui.brief.stale_report")}, stale)
        finished.write_fixture_writer(first, CLEAN_BRIEF + "\nThe late first attempt.\n")
        finished.write_fixture_writer(second)
        self.assertTrue(finished.report(second, slot="writer", status="ok")["accepted"])
        self.assertEqual(CLEAN_BRIEF.encode("utf-8"), (finished.W / "brief" / "v1.md").read_bytes())
        self.assertEqual(1, len(finished.run()["versions"]))

    def test_a_report_of_another_run_is_stale(self):
        finished = self.task()
        action = finished.next()
        before = (finished.W / "brief" / "state.json").read_bytes()
        result = finished.report(action, run="0" * 32, slot="writer", status="ok")
        self.assertEqual(["stale_report"], result["errors"])
        self.assertEqual(before, (finished.W / "brief" / "state.json").read_bytes())

    def test_two_runs_archive_the_first(self):
        finished = self.task()
        first = self.finish_run(finished)
        action = finished.next()
        previous = state_io.read_json(finished.W / "brief" / "previous" / "state.json")
        self.assertEqual(first["run_id"], previous["run_id"])
        second = finished.run()
        self.assertNotEqual(first["run_id"][:8], second["run_id"][:8])
        self.assertTrue(action["step_id"].startswith(f"b-{second['run_id'][:8]}-"))
        self.assertFalse(action["step_id"].startswith(f"b-{first['run_id'][:8]}-"))

    def finished_run_with_a_version(self, finished: FinishedTask) -> tuple[dict, dict[str, bytes]]:
        """Run 1 with a promoted `v1.md`, finished through `write_run`; returns it and its files."""
        self.promoted_writer(finished)

        def mutate(run: dict) -> None:
            run["outcome"] = "clean"
            run["step"] = "finished"

        first = brief.write_run(finished.W, mutate)
        files = {path.name: path.read_bytes() for path in (finished.W / "brief").iterdir() if path.is_file()}
        self.assertIn("v1.md", files)
        return first, files

    def assert_recovered(self, finished: FinishedTask, first: dict, files: dict[str, bytes]) -> None:
        previous = finished.W / "brief" / "previous"
        self.assertEqual(files, {name: (previous / name).read_bytes() for name in files})
        self.assertEqual(first["run_id"], state_io.read_json(previous / "state.json")["run_id"])
        action = finished.next()
        self.assertEqual("dispatch", action["kind"], action)
        second = finished.run()
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertIsNone(second["outcome"])
        self.assertEqual(files, {name: (previous / name).read_bytes() for name in files})
        self.assertFalse((previous / "previous").exists())
        self.assertEqual([], state_io.read_json(finished.W / "brief" / "open-issues.json"))
        self.assertEqual({"open-issues.json", "previous", "state.json"},
                         {path.name for path in (finished.W / "brief").iterdir()})

    def test_a_start_cut_short_after_the_archive_keeps_the_finished_run(self):
        """(a) The process dies after the last archive rename, before the new run's first state write."""
        finished = self.task()
        first, files = self.finished_run_with_a_version(finished)
        with mock.patch.object(brief, "_new_run", side_effect=RuntimeError("killed")):
            with self.assertRaises(RuntimeError):
                finished.next()
        self.assertIsNone(finished.run())
        self.assertEqual({"previous"}, {path.name for path in (finished.W / "brief").iterdir()})
        self.assert_recovered(finished, first, files)

    def test_a_start_cut_short_after_the_open_issues_keeps_the_finished_run(self):
        """(b) The same, with `brief/open-issues.json` already written by the interrupted start."""
        finished = self.task()
        first, files = self.finished_run_with_a_version(finished)
        with mock.patch.object(brief, "_save", side_effect=RuntimeError("killed")):
            with self.assertRaises(RuntimeError):
                finished.next()
        self.assertIsNone(finished.run())
        self.assertEqual({"open-issues.json", "previous"}, {path.name for path in (finished.W / "brief").iterdir()})
        self.assert_recovered(finished, first, files)

    def test_a_refusal_after_a_finished_run_leaves_it_alone(self):
        finished = self.task()
        self.finish_run(finished)
        before = (finished.W / "brief" / "state.json").read_bytes()
        pack = finished.W / "research" / "source-pack.json"
        pack.write_bytes(pack.read_bytes().replace(b"{", b"{ ", 1))
        action = finished.next()
        self.assertEqual("pack_changed_after_export", action["reason"])
        self.assertEqual(before, (finished.W / "brief" / "state.json").read_bytes())
        self.assertFalse((finished.W / "brief" / "previous").exists())

    def test_a_writer_that_keeps_failing_refuses_the_brief(self):
        finished = self.task()
        action = finished.next()
        for attempt in (1, 2):
            self.assertEqual(attempt, action["attempt"])
            self.assertTrue(finished.report(action, slot="writer", status="fail")["accepted"])
            action = finished.next()
        self.assertEqual("done", action["kind"])
        self.assertEqual("refused", action["outcome"])
        self.assertEqual("writer_failed", action["reason"])
        self.assertEqual(refused_text("writer_failed"), action["text"])
        self.assertEqual(["writer_failed"], finished.run()["outcome_reasons"])

    def test_a_locked_previous_run_is_refused(self):
        finished = self.task()
        first = self.finish_run(finished)
        before = (finished.W / "brief" / "state.json").read_bytes()
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if Path(src).name == "brief":
                raise PermissionError(13, "The process cannot access the file", str(src))
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch("os.replace", side_effect=replace):
            action = finished.next()
        self.assertEqual("done", action["kind"])
        self.assertEqual("previous_run_locked", action["reason"])
        self.assertIn("cannot access the file", action["text"])
        self.assertEqual(before, (finished.W / "brief" / "state.json").read_bytes())
        self.assertEqual(first["run_id"], finished.run()["run_id"])
        self.assertFalse((finished.W / "brief.archiving").exists())


class LockOrderTest(BriefCase):
    def test_no_event_is_appended_while_the_brief_lock_is_held(self):
        finished = self.task()
        depth = {"brief": 0}
        calls: list[int] = []
        real_enter, real_exit = state_io.FileLock.__enter__, state_io.FileLock.__exit__
        real_append = events.append_event

        def enter(lock):
            result = real_enter(lock)
            if lock.name == "brief":
                depth["brief"] += 1
            return result

        def exit_(lock, *exc):
            if lock.name == "brief":
                depth["brief"] -= 1
            return real_exit(lock, *exc)

        def append(*args, **kwargs):
            calls.append(depth["brief"])
            return real_append(*args, **kwargs)

        with mock.patch.object(state_io.FileLock, "__enter__", enter), \
                mock.patch.object(state_io.FileLock, "__exit__", exit_), \
                mock.patch.object(events, "append_event", append):
            action = finished.next()
            finished.write_fixture_writer(action)
            finished.report(action, slot="writer", status="ok")
            finished.next()
        self.assertTrue(calls, "the driver journals its steps")
        self.assertEqual([0] * len(calls), calls)


# --- Task 6 (D-225): review merge, revise and shorten, verdict, render, publish -------------------


def padded_brief(words: int) -> str:
    """CLEAN_BRIEF with filler actions, counting exactly `words` (B-01 count).

    D-229 fix round 2: every part has a word budget (B-12); only the number of actions is open, so the
    filler is actions of at most `DECISION_BRIEF_ACTION_WORDS` words each and B-01 is the only finding.
    """
    missing = words - brief_lint.brief_words(brief_lint.parse_brief(CLEAN_BRIEF, "en"))
    actions = []
    while missing > 0:
        take = min(missing, limits.DECISION_BRIEF_ACTION_WORDS)
        actions.append(f"{len(actions) + 2}. " + " ".join(["records"] * take))
        missing -= take
    text = CLEAN_BRIEF + "\n".join(actions) + "\n"
    assert brief_lint.brief_words(brief_lint.parse_brief(text, "en")) == words
    return text


def docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def current_sha(run: dict) -> str:
    return run["versions"][-1]["sha256"]


def approve(run: dict, slot: str) -> dict:
    sha = current_sha(run)
    return approving_fidelity(sha) if slot == "fidelity" else approving_form(sha)


def bf02(run: dict) -> dict:
    document = graded(approving_fidelity(current_sha(run)), BF_02=False)
    document["verdict"] = "needs_revision"
    document["issues"] = [fidelity_issue("BF-02")]
    return document


def bc03(run: dict) -> dict:
    document = graded(approving_form(current_sha(run)), BC_03=False)
    document["verdict"] = "needs_revision"
    document["issues"] = [form_issue("BC-03", section_id="s-b1")]
    return document


def two_form_majors(run: dict) -> dict:
    """A form review that fails BC-03 and BC-05 with an issue each (the Ozon end state, D-228)."""
    document = graded(approving_form(current_sha(run)), BC_03=False, BC_05=False)
    document["verdict"] = "needs_revision"
    document["issues"] = [form_issue("BC-03", section_id="s-b1"), form_issue("BC-05", section_id="s-actions")]
    return document


def banner(key: str, language: str = "en", **fmt) -> str:
    return i18n.t(language, f"memo.brief.banners.{key}", **fmt)


def fidelity_extras(finished: FinishedTask, row: dict) -> tuple[str, str]:
    """`(previous_review_path, changed_blocks)` the fidelity slot of one trail row was issued with."""
    extra = dispatch.load_spec(finished.W, row["step_id"], row["attempt"], "fidelity")["extra"]
    return extra["previous_review_path"], extra["changed_blocks"]


class FlowCase(BriefCase):
    def drive(self, finished: FinishedTask, *, text=CLEAN_BRIEF, texts=None, reviews=None,
              failing_writers: tuple = ()) -> tuple[dict, list[dict]]:
        """Answer every dispatch until `done`; returns the done action and one trail row per dispatch.

        `texts(step)` / `text`: what the writer writes; `reviews(run, slot)`: the review document, or
        None for a reviewer that fails; `failing_writers`: writer steps whose every attempt fails.
        """
        reviews = reviews or approve
        action = finished.next()
        trail: list[dict] = []
        for _ in range(80):
            if action["kind"] == "done":
                return action, trail
            self.assertEqual("dispatch", action["kind"], action)
            run = finished.run()
            trail.append({
                "step": run["step"], "round": run["round"], "attempt": action["attempt"], "step_id": action["step_id"],
                "slots": [agent["slot"] for agent in action["agents"]],
                "canonical": [agent["expected_outputs"][0]["canonical"] for agent in action["agents"]],
                "prompts": [agent["prompt"] for agent in action["agents"]],
            })
            for agent in action["agents"]:
                slot = agent["slot"]
                if slot == "writer":
                    if run["step"] in failing_writers:
                        finished.report(action, slot=slot, status="fail")
                        continue
                    finished.write_fixture_writer(action, texts(run["step"]) if texts else text)
                else:
                    document = reviews(run, slot)
                    if document is None:
                        finished.report(action, slot=slot, status="fail")
                        continue
                    finished.write_output(action, slot, json.dumps(document, ensure_ascii=False))
                    finished.agent_done(action, slot)
                finished.report(action, slot=slot, status="ok")
            action = finished.next()
        self.fail("the brief run did not finish")

    def ready_at(self, finished: FinishedTask, step: str, **fields) -> dict:
        """A run with a promoted v1 moved straight to `step` (the review steps are covered above)."""
        self.promoted_writer(finished)

        def mutate(run: dict) -> None:
            run.update({"step": step, "dispatch": None, **fields})

        return brief.write_run(finished.W, mutate)

    def with_root(self, finished: FinishedTask) -> Path:
        root = temp_root(self) / "outputs"
        root.mkdir()
        finished.write_state(lambda state: state["progress"].update({"published_memo": str(root / "memo-test.docx")}))
        return root


class ReviewFlowTest(FlowCase):
    def test_clean_reviews_render_a_clean_docx(self):
        finished = self.task()
        checks: list[dict] = []
        real_validate = validate.validate_path

        def spy(*args, **kwargs):
            checks.append(real_validate(*args, **kwargs))
            return checks[-1]

        with mock.patch.object(validate, "validate_path", side_effect=spy):
            done, trail = self.drive(finished)
        self.assertEqual([("write", 0), ("review", 0)], [(row["step"], row["round"]) for row in trail])
        self.assertEqual("clean", done["outcome"])
        run = finished.run()
        self.assertEqual(("clean", "clean", False), (run["verdict"], run["outcome"], run["too_long"]))
        self.assertEqual([], run["outcome_reasons"])
        self.assertEqual("brief/brief.docx", run["deliverable_path"])
        self.assertEqual([True], [check["valid"] for check in checks])
        text = docx_text(finished.W / "brief" / "brief.docx")
        self.assertIn("Records may not be kept beyond their purpose", text)
        self.assertNotIn("from §s-3", text)
        self.assertNotIn(fallback.label("sources_heading"), text)
        self.assertNotIn(fallback.label("appendix_heading"), text)
        self.assertNotIn(banner("title"), text, "a clean brief of a clean memo has no banner")
        self.assertEqual("finished", run["step"])

    def test_a_blocker_in_every_round_revises_twice_then_renders_unverified(self):
        finished = self.task()
        done, trail = self.drive(finished, reviews=lambda run, slot: bf02(run) if slot == "fidelity"
                                 else approve(run, slot))
        self.assertEqual(
            [("write", 0), ("review", 0), ("revise", 1), ("review", 1), ("revise", 2), ("review", 2)],
            [(row["step"], row["round"]) for row in trail],
        )
        self.assertIn("brief/instructions-r1.md", trail[2]["prompts"][0])
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("- [blocker] Fixture fidelity issue 1 on BF-02. — Restore the condition.", instructions)
        self.assertTrue((finished.W / "brief" / "instructions-r2.md").is_file())
        self.assertEqual("unverified", done["outcome"])
        self.assertEqual(i18n.t("en", "ui.brief.done_unverified", path=done["path"]), done["text"])
        run = finished.run()
        self.assertEqual(("unverified", ["BF-02"]), (run["verdict"], run["outcome_reasons"]))
        self.assertEqual("brief/brief.docx", run["deliverable_path"])
        text = docx_text(finished.W / "brief" / "brief.docx")
        self.assertIn(banner("title"), text)
        self.assertIn(banner("unverified", checks=i18n.t("en", "memo.brief.checks.BF-02")), text)
        self.assertNotIn(banner("too_long"), text)

    def test_an_all_pass_review_of_another_version_is_never_clean(self):
        """Final fix (Astra): an approving fidelity review with a stale draft_sha on every attempt."""
        finished = self.task()

        def reviews(run: dict, slot: str) -> dict:
            return approving_fidelity("0" * 64) if slot == "fidelity" else approve(run, slot)

        done, trail = self.drive(finished, reviews=reviews)
        self.assertEqual([1, 2], [row["attempt"] for row in trail if row["step"] == "review"][:2])
        self.assertEqual("unverified", done["outcome"])
        run = finished.run()
        self.assertEqual("unverified", run["verdict"])
        self.assertIn("fidelity_review_missing", run["outcome_reasons"])
        self.assertNotIn("form_review_missing", run["outcome_reasons"])

    def test_a_review_of_another_version_grades_nothing_after_exhaustion(self):
        """Final fix: a retained review bound to a stale draft_sha keeps its findings, never its passes."""
        finished = self.task()
        stale = "0" * 64

        def reviews(run: dict, slot: str) -> dict:
            if slot != "fidelity":
                return approve(run, slot)
            document = bf02(run)
            document["draft_sha"] = stale
            return document

        done, trail = self.drive(finished, reviews=reviews)
        run = finished.run()
        fidelity_rows = [row for row in run["reviews"] if row["slot"] == "fidelity"]
        self.assertTrue(fidelity_rows)
        self.assertTrue(all(not row["valid"] and row["sha256"] for row in fidelity_rows), fidelity_rows)
        self.assertTrue(all("stale_draft_sha" in " ".join(row["errors"]) for row in fidelity_rows))
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("Fixture fidelity issue 1 on BF-02.", instructions, "the stale review's finding is kept")
        self.assertIn(i18n.t("en", "memo.brief.checks.BF-01"), instructions, "its passing grades count as missing")
        self.assertEqual("unverified", done["outcome"])
        self.assertIn("fidelity_review_missing", run["outcome_reasons"])
        self.assertIn("BF-01", run["outcome_reasons"])
        self.assertIn("BF-02", run["outcome_reasons"])
        self.assertNotIn("form_review_missing", run["outcome_reasons"])

    def assert_clean_after_form_revisions(self, finished: FinishedTask, done: dict, trail: list[dict]) -> None:
        """D-228: form findings send the brief back while rounds remain, but never decide the verdict."""
        self.assertEqual([("revise", 1), ("revise", 2)],
                         [(row["step"], row["round"]) for row in trail if row["step"] == "revise"])
        self.assertEqual("clean", done["outcome"])
        run = finished.run()
        self.assertEqual(("clean", []), (run["verdict"], run["outcome_reasons"]))
        self.assertNotIn(banner("title"), docx_text(finished.W / "brief" / "brief.docx"))

    def test_the_ozon_end_state_is_clean(self):
        """D-228: fidelity passes every item, the form reviewer still raises two majors after round 2."""
        finished = self.task()
        done, trail = self.drive(finished, reviews=lambda run, slot: two_form_majors(run) if slot == "form"
                                 else approve(run, slot))
        self.assert_clean_after_form_revisions(finished, done, trail)
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("- [major] Fixture form issue on BC-03.", instructions, "form findings still reach the writer")
        self.assertIn("- [major] Fixture form issue on BC-05.", instructions)
        rows = [row for row in finished.run()["reviews"] if row["slot"] == "form"]
        self.assertTrue(rows[-1]["valid"] and rows[-1]["round"] == 2)

    def test_a_form_review_that_omits_an_item_revises_but_never_decides_the_verdict(self):
        finished = self.task()

        def reviews(run: dict, slot: str) -> dict:
            document = approve(run, slot)
            if slot == "form":
                document["checklist"] = [item for item in document["checklist"] if item["id"] != "BC-04"]
            return document

        done, trail = self.drive(finished, reviews=reviews)
        self.assertEqual([1, 2], [row["attempt"] for row in trail if row["step"] == "review"][:2])
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn(i18n.t("en", "memo.brief.checks.BC-04"), instructions, "the omission is still fail-closed")
        self.assert_clean_after_form_revisions(finished, done, trail)

    def test_an_unknown_form_item_without_an_issue_revises_but_never_decides_the_verdict(self):
        finished = self.task()

        def reviews(run: dict, slot: str) -> dict:
            return graded(approve(run, slot), BC_02="unknown") if slot == "form" else approve(run, slot)

        done, trail = self.drive(finished, reviews=reviews)
        self.assert_clean_after_form_revisions(finished, done, trail)

    def assert_clean_without_revision(self, finished: FinishedTask, done: dict, trail: list[dict]) -> None:
        """Fix round 1: a missing or stale form review neither decides the verdict nor sends the brief back."""
        self.assertEqual([("write", 0, 1), ("review", 0, 1), ("review", 0, 2)],
                         [(row["step"], row["round"], row["attempt"]) for row in trail])
        self.assertFalse((finished.W / "brief" / "instructions-r1.md").exists())
        self.assertEqual("clean", done["outcome"])
        run = finished.run()
        self.assertEqual(("clean", []), (run["verdict"], run["outcome_reasons"]))
        self.assertNotIn(banner("title"), docx_text(finished.W / "brief" / "brief.docx"))

    def test_a_form_reviewer_that_never_answers_adds_no_reason_and_no_revision(self):
        finished = self.task()
        done, trail = self.drive(finished, reviews=lambda run, slot: None if slot == "form" else approve(run, slot))
        self.assert_clean_without_revision(finished, done, trail)
        self.assertIsNone([row for row in finished.run()["reviews"] if row["slot"] == "form"][-1]["sha256"])

    def test_a_stale_form_review_adds_no_reason_and_no_revision(self):
        finished = self.task()

        def reviews(run: dict, slot: str) -> dict:
            return approving_form("0" * 64) if slot == "form" else approve(run, slot)

        done, trail = self.drive(finished, reviews=reviews)
        rows = [row for row in finished.run()["reviews"] if row["slot"] == "form"]
        self.assertTrue(all(not row["valid"] and "stale_draft_sha" in " ".join(row["errors"]) for row in rows))
        self.assert_clean_without_revision(finished, done, trail)

    def test_the_real_issues_of_a_stale_form_review_still_reach_the_writer(self):
        finished = self.task()

        def reviews(run: dict, slot: str) -> dict:
            if slot != "form":
                return approve(run, slot)
            document = bc03(run)
            document["draft_sha"] = "0" * 64
            return document

        done, trail = self.drive(finished, reviews=reviews)
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("Fixture form issue on BC-03.", instructions)
        self.assertNotIn(i18n.t("en", "memo.brief.checks.BC-01"), instructions, "no synthetic of the stale review")
        self.assert_clean_after_form_revisions(finished, done, trail)

    def test_a_fidelity_reviewer_that_never_answers_is_fidelity_review_missing(self):
        finished = self.task()
        done, _ = self.drive(finished, reviews=lambda run, slot: None if slot == "fidelity" else approve(run, slot))
        run = finished.run()
        self.assertEqual("unverified", done["outcome"])
        self.assertIn("fidelity_review_missing", run["outcome_reasons"])
        self.assertNotIn("form_review_missing", run["outcome_reasons"])

    def test_a_writer_exhausted_on_revise_is_unverified_and_named(self):
        finished = self.task()
        done, trail = self.drive(finished, reviews=lambda run, slot: bf02(run) if slot == "fidelity"
                                 else approve(run, slot), failing_writers=("revise",))
        self.assertEqual(["write", "review", "revise", "revise"], [row["step"] for row in trail])
        self.assertEqual("unverified", done["outcome"])
        run = finished.run()
        self.assertEqual(["writer_failed"], run["outcome_reasons"])
        self.assertEqual(1, len(run["versions"]))
        text = docx_text(finished.W / "brief" / "brief.docx")
        self.assertIn(i18n.t("en", "memo.brief.checks.writer_failed"), text)


class LengthTest(FlowCase):
    def test_a_brief_up_to_the_hard_cap_is_never_revised_for_length(self):
        """D-228: B-01 is minor at 1150 words — no revision, no banner."""
        finished = self.task()
        done, trail = self.drive(finished, text=padded_brief(1150))
        report = state_io.read_json(finished.W / "brief" / "lint-v1.json")
        self.assertEqual([("B-01", "minor")], [(row["rule"], row["severity"]) for row in report["findings"]])
        self.assertEqual(["write", "review"], [row["step"] for row in trail])
        self.assertEqual("clean", done["outcome"])
        run = finished.run()
        self.assertEqual(("clean", False, []), (run["verdict"], run["too_long"], run["outcome_reasons"]))
        self.assertNotIn(banner("title"), docx_text(finished.W / "brief" / "brief.docx"))

    def test_a_part_over_its_budget_is_revised_but_never_decides_the_verdict(self):
        """D-229 fix round 2: B-12 sends the brief back while rounds remain; like B-01 it never makes it unverified."""
        finished = self.task()
        long_main = CLEAN_BRIEF.replace("before the next audit.\n\n## Conclusions",
                                        "before the next audit. " + " ".join(["records"] * 60) + "\n\n## Conclusions")
        done, trail = self.drive(finished, text=long_main)
        report = state_io.read_json(finished.W / "brief" / "lint-v1.json")
        self.assertEqual([("B-12", "major", "s-main")],
                         [(row["rule"], row["severity"], row["section_id"]) for row in report["findings"]])
        self.assertEqual([("write", 0), ("review", 0), ("revise", 1), ("review", 1), ("revise", 2), ("review", 2)],
                         [(row["step"], row["round"]) for row in trail], "no lint_fix for a word budget")
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("- [major] B-12: ", instructions)
        run = finished.run()
        self.assertEqual(("clean", [], False), (run["verdict"], run["outcome_reasons"], run["too_long"]))
        self.assertEqual("clean", done["outcome"])

    def test_a_brief_over_the_hard_cap_is_revised_while_rounds_remain(self):
        finished = self.task()
        done, trail = self.drive(finished, text=padded_brief(1250))
        self.assertEqual([("write", 0), ("review", 0), ("revise", 1)],
                         [(row["step"], row["round"]) for row in trail[:3]])
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("- [major] B-01: The brief is 1250 words", instructions)
        self.assertEqual("clean", done["outcome"])

    def test_a_brief_over_the_hard_cap_is_shortened_once_and_marked_too_long(self):
        finished = self.task()
        done, trail = self.drive(finished, text=padded_brief(limits.DECISION_BRIEF_HARD_CAP + 1))
        steps = [(row["step"], row["round"]) for row in trail]
        shorten = steps.index(("shorten", 2))
        self.assertEqual(("review", 2), steps[shorten - 1])
        self.assertEqual(1, steps.count(("shorten", 2)))
        [after] = [row for row in trail[shorten + 1:] if row["step"] == "review"]
        self.assertEqual(["fidelity"], after["slots"])
        self.assertEqual(["brief/reviews/r2-fidelity-shortened.json"], after["canonical"])
        self.assertIn("brief/instructions-r2-shorten.md", trail[shorten]["prompts"][0])
        shorten_text = (finished.W / "brief" / "instructions-r2-shorten.md").read_text(encoding="utf-8")
        self.assertIn(f"shorten to about {limits.DECISION_BRIEF_SOFT_CAP} words", shorten_text)
        self.assertIn("about three pages", shorten_text)  # D-227
        self.assertTrue((finished.W / "brief" / "reviews" / "r2-fidelity.json").is_file())
        run = finished.run()
        self.assertTrue(run["shorten_done"])
        self.assertEqual(("clean", True), (run["verdict"], run["too_long"]))
        self.assertEqual("clean", done["outcome"])
        text = docx_text(finished.W / "brief" / "brief.docx")
        self.assertIn(banner("too_long"), text)
        self.assertNotIn(banner("unverified", checks="").split(":")[0], text)

    def test_an_exhausted_lint_fix_after_the_shorten_drops_the_length_banner(self):
        """Final fix: `too_long` is recomputed from the retained (shortened) version before the early render."""
        finished = self.task()
        long_text = padded_brief(limits.DECISION_BRIEF_HARD_CAP + 1)
        flawed = CLEAN_BRIEF.replace("before the next audit.\n", "before the next audit, as the memorandum says.\n")
        done, trail = self.drive(finished, texts=lambda step: flawed if step == "shorten" else long_text,
                                 failing_writers=("lint_fix",))
        steps = [row["step"] for row in trail]
        self.assertIn("shorten", steps)
        self.assertEqual("lint_fix", steps[-1])
        run = finished.run()
        self.assertEqual(flawed.encode("utf-8"), (finished.W / run["versions"][-1]["path"]).read_bytes())
        self.assertEqual(("unverified", False), (run["verdict"], run["too_long"]))
        self.assertIn("writer_failed", run["outcome_reasons"])
        text = docx_text(finished.W / "brief" / "brief.docx")
        self.assertNotIn(banner("too_long"), text)
        self.assertEqual("unverified", done["outcome"])

    def test_an_open_form_major_is_carried_through_the_shorten(self):
        finished = self.task()
        long_text = padded_brief(limits.DECISION_BRIEF_HARD_CAP + 1)

        def reviews(run: dict, slot: str) -> dict:
            return bc03(run) if slot == "form" and run["round"] == 2 else approve(run, slot)

        done, trail = self.drive(finished, texts=lambda step: CLEAN_BRIEF if step == "shorten" else long_text,
                                 reviews=reviews)
        self.assertIn("shorten", [row["step"] for row in trail])
        run = finished.run()
        self.assertEqual(["BC-03"], [row["checklist_id"] for row in run["carried_form_findings"]])
        # D-228: a carried form finding no longer decides the verdict.
        self.assertEqual(("clean", [], False), (run["verdict"], run["outcome_reasons"], run["too_long"]))
        self.assertEqual("clean", done["outcome"])
        # The re-check after the shortening pass is scoped to what the pass changed.
        [after] = [row for row in trail if row["step"] == "review" and row["slots"] == ["fidelity"]]
        # D-229 fix round 2: `padded_brief` pads with actions (every other part has a word budget).
        self.assertEqual(("brief/reviews/r2-fidelity.json", "s-actions"), fidelity_extras(finished, after))


class ScopedReviewTest(FlowCase):
    """D-228: from round 1 on, the fidelity reviewer gets its previous review and the blocks that changed."""

    def test_the_extras_default_to_a_full_review(self):
        self.assertEqual("none", dispatch._DEFAULT_EXTRAS["previous_review_path"])  # noqa: SLF001
        self.assertEqual("all", dispatch._DEFAULT_EXTRAS["changed_blocks"])  # noqa: SLF001

    def test_each_round_names_the_previous_review_and_the_changed_blocks(self):
        finished = self.task()
        v2 = CLEAN_BRIEF.replace("only if it covers these records.", "only if a statute covers these records.")
        v3 = v2.replace("counsel should confirm", "counsel must first confirm")
        self.assertNotEqual(v2, v3)
        texts = iter([CLEAN_BRIEF, v2, v3])
        done, trail = self.drive(finished, texts=lambda step: next(texts),
                                 reviews=lambda run, slot: bf02(run) if slot == "fidelity" else approve(run, slot))
        reviews = [row for row in trail if row["step"] == "review"]
        self.assertEqual([0, 1, 2], [row["round"] for row in reviews])
        self.assertEqual(
            [("none", "all"), ("brief/reviews/r0-fidelity.json", "s-b1"), ("brief/reviews/r1-fidelity.json", "s-main")],
            [fidelity_extras(finished, row) for row in reviews],
        )
        self.assertEqual("unverified", done["outcome"])

    def test_a_changed_conclusions_preamble_asks_for_a_full_review(self):
        """Fix round 1 (Sol): a claim added above the first block is covered by no block id."""
        finished = self.task()
        added = CLEAN_BRIEF.replace("## Conclusions\n\n", "## Conclusions\n\nThe tax carve-out covers invoices.\n\n")
        texts = iter([CLEAN_BRIEF, added, added])
        _, trail = self.drive(finished, texts=lambda step: next(texts),
                              reviews=lambda run, slot: bf02(run) if slot == "fidelity" else approve(run, slot))
        reviews = [row for row in trail if row["step"] == "review"]
        self.assertEqual(
            [("none", "all"), ("brief/reviews/r0-fidelity.json", "all"), ("brief/reviews/r1-fidelity.json", "none")],
            [fidelity_extras(finished, row) for row in reviews],
        )

    def test_a_changed_omitted_list_asks_for_a_full_review(self):
        """D-229: the omitted list decides what the brief leaves out; its change is covered by no block id."""
        finished = self.task()
        omitted = CLEAN_BRIEF.replace("\n## Bottom line", "\n<!-- omitted §s-9 -->\n\n## Bottom line")
        self.assertIsNone(brief_lint.changed_blocks(CLEAN_BRIEF, omitted, "en"))
        _, trail = self.drive(finished, texts=lambda step: CLEAN_BRIEF if step == "write" else omitted,
                              reviews=lambda run, slot: bf02(run) if slot == "fidelity" else approve(run, slot))
        reviews = [row for row in trail if row["step"] == "review"]
        self.assertEqual([("none", "all"), ("brief/reviews/r0-fidelity.json", "all")],
                         [fidelity_extras(finished, row) for row in reviews[:2]])

    def test_a_wrongly_omitted_leaf_is_restored_by_a_partition_fix(self):
        """Fix round 1 (D-229): BF-05 on `s-header` — the leaf comes back in its block, its action with it."""
        finished = self.task()
        question = "**Question:** How long may the client keep customer records?\n"
        block = CLEAN_BRIEF[CLEAN_BRIEF.index("### Retention period"):CLEAN_BRIEF.index("## What to do")]
        action = "1. Counsel confirms the tax basis for the seven-year period before the next audit."
        omitted = (CLEAN_BRIEF.replace(question, question + "\n<!-- omitted §s-3 -->\n")
                   .replace(block, "Other matters: retention period (risk medium).\n\n")
                   .replace(action, "1. Counsel files the retention schedule."))
        restored = CLEAN_BRIEF.replace(question, question + "\n<!-- omitted -->\n")
        self.assertEqual(["s-3"], brief_lint.parse_brief(omitted, "en")["omitted"])

        def bf05(run: dict) -> dict:
            document = graded(approving_fidelity(current_sha(run)), BF_05=False)
            document["verdict"] = "needs_revision"
            document["issues"] = [dict(
                fidelity_issue("BF-05", block="s-header", severity="major", quote="<!-- omitted §s-3 -->"),
                issue="Leaf s-3 is omitted, but its risk line names the seven-year period, a keep criterion.",
                suggestion="Keep s-3: take it off the omitted list, bind it to a block and restore its action.",
            )]
            return document

        def reviews(run: dict, slot: str) -> dict:
            return bf05(run) if slot == "fidelity" and run["round"] == 0 else approve(run, slot)

        done, trail = self.drive(finished, texts=lambda step: restored if step == "revise" else omitted,
                                 reviews=reviews)
        self.assertEqual(["write", "lint_fix", "review", "revise", "review"], [row["step"] for row in trail])
        instructions = (finished.W / "brief" / "instructions-r1.md").read_text(encoding="utf-8")
        self.assertIn("## Title and header lines (s-header)\n\n- [major] Leaf s-3 is omitted", instructions)
        [revise] = [row for row in trail if row["step"] == "revise"]
        self.assertIn("Partition fix: when an issue concerns the partition", " ".join(revise["prompts"][0].split()))
        _, text, _ = brief.current_version(finished.W, finished.run())
        parsed = brief_lint.parse_brief(text, "en")
        self.assertEqual(([["s-3"]], []), ([row["bindings"] for row in parsed["blocks"]], parsed["omitted"]))
        self.assertIn(action, text)
        reviews_rows = [row for row in trail if row["step"] == "review"]
        self.assertEqual(("brief/reviews/r0-fidelity.json", "all"), fidelity_extras(finished, reviews_rows[1]))
        self.assertEqual("clean", done["outcome"])

    def test_an_unchanged_revision_changes_no_block(self):
        finished = self.task()
        _, trail = self.drive(finished, reviews=lambda run, slot: bf02(run) if slot == "fidelity"
                              else approve(run, slot))
        reviews = [row for row in trail if row["step"] == "review"]
        self.assertEqual(("brief/reviews/r0-fidelity.json", "none"), fidelity_extras(finished, reviews[1]))

    def test_a_round_whose_fidelity_review_never_came_is_followed_by_a_full_review(self):
        finished = self.task()

        def reviews(run: dict, slot: str) -> dict | None:
            if slot == "fidelity":
                return None if run["round"] == 0 else bf02(run)
            return approve(run, slot)

        _, trail = self.drive(finished, reviews=reviews)
        rows = [row for row in trail if row["step"] == "review"]
        self.assertEqual([(0, 1), (0, 2), (1, 1), (2, 1)], [(row["round"], row["attempt"]) for row in rows])
        self.assertEqual(
            [("none", "all"), ("none", "all"), ("none", "all"), ("brief/reviews/r1-fidelity.json", "none")],
            [fidelity_extras(finished, row) for row in rows],
        )


class RenderTest(FlowCase):
    def test_a_render_error_falls_back_to_markdown(self):
        finished = self.task()
        self.ready_at(finished, "render", verdict="unverified", outcome_reasons=["BF-02"])
        with mock.patch.object(renderer, "render", side_effect=renderer.RenderError("forced")):
            done = finished.next()
        run = finished.run()
        self.assertEqual("brief/brief.md", run["deliverable_path"])
        self.assertEqual(["BF-02"], run["outcome_reasons"], "a markdown fallback is no render failure")
        self.assertFalse((finished.W / "brief" / "brief.docx").exists())
        markdown = (finished.W / "brief" / "brief.md").read_text(encoding="utf-8")
        self.assertTrue(markdown.startswith(f"> **{banner('title')}**\n"), markdown[:200])
        self.assertIn(banner("unverified", checks=i18n.t("en", "memo.brief.checks.BF-02")), markdown)
        self.assertNotIn("<!-- from", markdown)
        self.assertNotIn("## Sources", markdown)
        self.assertEqual("md", done["format"])
        self.assertEqual(str(finished.W / "brief" / "brief.md"), done["path"])

    def test_crlf_binding_lines_never_reach_the_markdown(self):
        """Fix round 1: a writer that saves Windows line ends still has its binding comments removed."""
        finished = self.task()
        crlf = CLEAN_BRIEF.replace("\n", "\r\n")
        self.promoted_writer(finished, crlf)
        self.assertIn(b"<!-- from \xc2\xa7s-3 -->\r\n", (finished.W / "brief" / "v1.md").read_bytes())

        def mutate(run: dict) -> None:
            run.update({"step": "render", "dispatch": None, "verdict": "clean"})

        brief.write_run(finished.W, mutate)
        with mock.patch.object(renderer, "render", side_effect=renderer.RenderError("forced")):
            finished.next()
        self.assertEqual("brief/brief.md", finished.run()["deliverable_path"])
        markdown = (finished.W / "brief" / "brief.md").read_text(encoding="utf-8")
        self.assertNotIn("<!--", markdown)
        self.assertNotIn("from §s-3", markdown)
        self.assertIn("Records may not be kept beyond their purpose", markdown)

    def test_the_omitted_comment_never_reaches_the_reader(self):
        """D-229: the header's `<!-- omitted … -->` is stripped with the bindings, with ids or without."""
        for comment in ("<!-- omitted §s-8 §s-10-1 -->", "<!-- omitted -->", "  <!--omitted §s-2-->  "):
            with self.subTest(comment=comment):
                text = CLEAN_BRIEF.replace("\n## Bottom line", f"\n{comment}\n\n## Bottom line")
                for ending in ("\n", "\r\n"):
                    body = brief._deliverable_text(text.replace("\n", ending))  # noqa: SLF001
                    self.assertNotIn("<!--", body)
                    self.assertNotIn("omitted", body)
                    self.assertIn("**Question:** How long may the client keep customer records?", body)
                    self.assertNotIn(ending * 3, body)
        kept = "<!-- sources: generated -->"
        self.assertIn(kept, brief._deliverable_text(CLEAN_BRIEF + kept + "\n"))  # noqa: SLF001

    def test_a_missing_docx_dependency_falls_back_to_markdown(self):
        """Without `python-docx`/`mistune` the brief is markdown, as the memo is (§5.5); the CLI still loads."""
        from memoforge import docx as docx_package

        finished = self.task()
        self.ready_at(finished, "render", verdict="clean")
        module = sys.modules["memoforge.docx.renderer"]
        sys.modules["memoforge.docx.renderer"] = None
        delattr(docx_package, "renderer")
        try:
            done = finished.next()
        finally:
            sys.modules["memoforge.docx.renderer"] = module
            docx_package.renderer = module
        run = finished.run()
        self.assertEqual("brief/brief.md", run["deliverable_path"])
        self.assertEqual(("clean", []), (run["verdict"], run["outcome_reasons"]))
        self.assertEqual("clean", done["outcome"])
        fired = [row for row in events.read_events(finished.W) if row["event"] == "fallback_invoked"]
        self.assertEqual("brief_docx_unavailable", fired[-1]["data"]["condition_key"])

    def test_an_invalid_docx_falls_back_to_markdown(self):
        finished = self.task()
        self.ready_at(finished, "render", verdict="clean")
        with mock.patch.object(validate, "validate_path", return_value={"valid": False, "errors": ["x"]}):
            done = finished.next()
        self.assertEqual("brief/brief.md", finished.run()["deliverable_path"])
        self.assertFalse((finished.W / "brief" / "brief.docx").exists())
        self.assertEqual("clean", done["outcome"])

    def test_both_renders_failing_deliver_the_last_version(self):
        finished = self.task()
        self.ready_at(finished, "render", verdict="clean")
        with mock.patch.object(renderer, "render", side_effect=renderer.RenderError("forced")), \
                mock.patch.object(fallback, "render", side_effect=RuntimeError("forced too")):
            done = finished.next()
        run = finished.run()
        self.assertEqual("brief/v1.md", run["deliverable_path"])
        self.assertEqual(("unverified", ["render_failed"]), (run["verdict"], run["outcome_reasons"]))
        self.assertEqual("unverified", done["outcome"])
        self.assertEqual(str(finished.W / "brief" / "v1.md"), done["path"])
        self.assertFalse(done["present"])

    def test_both_renders_failing_publish_the_last_version(self):
        finished = self.task()
        root = self.with_root(finished)
        self.ready_at(finished, "render", verdict="clean")
        with mock.patch.object(renderer, "render", side_effect=renderer.RenderError("forced")), \
                mock.patch.object(fallback, "render", side_effect=RuntimeError("forced too")):
            done = finished.next()
        copy_path = root / "memo-test.brief.md"
        self.assertEqual(str(copy_path), done["path"])
        self.assertTrue(done["present"])
        self.assertEqual((finished.W / "brief" / "v1.md").read_bytes(), copy_path.read_bytes())

    def test_a_pack_edited_after_the_preflight_is_a_render_failure(self):
        finished = self.task()
        self.ready_at(finished, "render", verdict="clean")
        pack = finished.W / "research" / "source-pack.json"
        pack.write_bytes(pack.read_bytes().replace(b"{", b"{ ", 1))
        done = finished.next()
        run = finished.run()
        self.assertEqual(("unverified", ["render_failed"]), (run["verdict"], run["outcome_reasons"]))
        self.assertEqual("brief/brief.md", run["deliverable_path"])
        markdown = (finished.W / "brief" / "brief.md").read_text(encoding="utf-8")
        self.assertIn(i18n.t("en", "memo.brief.checks.render_failed"), markdown)
        self.assertEqual("unverified", done["outcome"])
        fired = [row for row in events.read_events(finished.W) if row["event"] == "fallback_invoked"]
        self.assertEqual(stepctx.OutputModifiedAfterPublish.__name__, fired[-1]["data"]["error_type"])
        self.assertEqual("brief", fired[-1]["data"]["run"])

    def test_the_unclean_memo_banner(self):
        finished = self.task()
        self.ready_at(finished, "render", verdict="clean", confirmed_unclean=True)
        finished.next()
        text = docx_text(finished.W / "brief" / "brief.docx")
        self.assertIn(banner("title"), text)
        self.assertIn(banner("subtitle"), text)
        self.assertIn(banner("unclean_memo"), text)


class PublishTest(FlowCase):
    def test_publish_without_root_keeps_brief_in_workdir(self):
        finished = self.task()
        self.assertIsNone(finished.state()["progress"]["published_memo"])
        done, _ = self.drive(finished)
        self.assertEqual("done", done["kind"])
        self.assertFalse(done["present"])
        self.assertEqual(str(finished.W / "brief" / "brief.docx"), done["path"])
        self.assertEqual(done["text"], done["chat_line"])
        self.assertEqual([], finished.run()["published"])

    def test_publish_never_creates_a_missing_outputs_root(self):
        """D-227 (F8-1): a Cowork path copied to another host (`/mnt/user-data/outputs/…`) is no root here."""
        finished = self.task()
        absent = temp_root(self) / "mnt"
        root = absent / "user-data" / "outputs"
        finished.write_state(lambda state: state["progress"].update({"published_memo": str(root / "memo-test.docx")}))
        done, _ = self.drive(finished)
        self.assertFalse(absent.exists(), "publish created the outputs root")
        self.assertFalse(done["present"])
        self.assertEqual(str(finished.W / "brief" / "brief.docx"), done["path"])
        self.assertEqual(i18n.t("en", "ui.brief.done", path=done["path"]), done["text"])
        self.assertEqual([], finished.run()["published"])

    def test_publish_with_a_root_copies_twice_and_replaces_the_other_format(self):
        finished = self.task()
        root = self.with_root(finished)
        done, _ = self.drive(finished)
        inner, outer = root / "memoforge" / "test" / "brief.docx", root / "memo-test.brief.docx"
        self.assertEqual(str(outer), done["path"])
        self.assertTrue(done["present"])
        payload = (finished.W / "brief" / "brief.docx").read_bytes()
        self.assertEqual(payload, inner.read_bytes())
        self.assertEqual(payload, outer.read_bytes())
        self.assertEqual(i18n.t("en", "ui.brief.done", path=str(outer)), done["text"])
        sha = state_io.sha256_bytes(payload)
        self.assertEqual([{"path": str(inner), "sha256": sha}, {"path": str(outer), "sha256": sha}],
                         finished.run()["published"])

        # Run 2 falls back to markdown: the docx copies of run 1 go.
        self.ready_at(finished, "render", verdict="clean")
        with mock.patch.object(renderer, "render", side_effect=renderer.RenderError("forced")):
            second = finished.next()
        self.assertEqual(str(root / "memo-test.brief.md"), second["path"])
        self.assertTrue((root / "memo-test.brief.md").is_file())
        self.assertTrue((root / "memoforge" / "test" / "brief.md").is_file())
        self.assertFalse(outer.exists())
        self.assertFalse(inner.exists())

    def test_a_failed_copy_is_reported_in_the_done_text(self):
        finished = self.task()
        root = self.with_root(finished)
        real_write = state_io.write_bytes_atomic

        def write(path, payload):
            if Path(root) in Path(path).parents:
                raise PermissionError(13, "Access is denied", str(path))
            return real_write(path, payload)

        self.ready_at(finished, "render", verdict="clean")
        with mock.patch.object(state_io, "write_bytes_atomic", side_effect=write):
            done = finished.next()
        local = str(finished.W / "brief" / "brief.docx")
        run = finished.run()
        self.assertEqual("clean", run["outcome"])
        self.assertEqual([], run["published"])
        self.assertFalse(done["present"])
        self.assertEqual(local, done["path"])
        first, second = done["text"].split("\n")
        self.assertEqual(i18n.t("en", "ui.brief.done", path=local), first)
        self.assertTrue(second.startswith(i18n.t("en", "ui.brief.copy_failed", path=local, error="")[:-1]))
        self.assertIn("Access is denied", second)
        self.assertEqual(first, done["chat_line"])

    def test_an_interrupted_delivery_publishes_once(self):
        finished = self.task()
        root = self.with_root(finished)
        first = self.ready_at(finished, "publish", verdict="clean", deliverable_path="brief/v1.md", outcome=None)
        done = finished.next()
        self.assertEqual("clean", done["outcome"])
        run = finished.run()
        self.assertEqual("clean", run["outcome"])
        self.assertEqual(2, len(run["published"]))
        self.assertEqual((finished.W / "brief" / "v1.md").read_bytes(), (root / "memo-test.brief.md").read_bytes())
        self.assertFalse((finished.W / "brief" / "previous").exists())

        # The normal lifecycle, not a second publish: the next call starts run 2 and archives run 1.
        self.assertEqual("dispatch", finished.next()["kind"])
        previous = state_io.read_json(finished.W / "brief" / "previous" / "state.json")
        self.assertEqual(first["run_id"], previous["run_id"])
        self.assertEqual("clean", previous["outcome"])


if __name__ == "__main__":
    unittest.main()
