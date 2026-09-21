"""Tests for scripts/memoforge/machine.py — the `next`/`report` protocol (ТЗ §2.1, §2.4, §3.1, §9)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, namespace, temp_root  # noqa: E402
import _i18n  # noqa: E402
from memoforge import docx as docx_export  # noqa: E402
from memoforge import (  # noqa: E402
    events,
    gates,
    limits,
    machine,
    modes,
    phases,
    preflight,
    probe,
    review,
    sources,
    state_io,
    stepctx,
)
from memoforge.docx import fallback as md_fallback  # noqa: E402


def _published_shas(state: dict) -> dict:
    """`published[]` reduced to «path -> sha»: the part a replay must leave untouched (§2.2)."""
    return {row["canonical_path"]: row["sha256"] for row in state.get("published") or []}


def _agent_done(driver: Driver, action: dict, slot: str) -> None:
    """`mf agent log --state done` for one slot: the completion marker the checks of §3.1 read."""
    machine.run_agent_log(
        namespace(
            workdir=str(driver.work_dir),
            step=action["step_id"],
            attempt=action["attempt"],
            slot=slot,
            state="done",
            detail=None,
            mcp=None,
        )
    )


BREAK_RISK = ("Risk: medium. The exposure", "Risk: unclear. The exposure")
"""An L-07 blocker (D-12/D-22): the Risk line of the analytical subsection loses its literal form."""


def _act_writer(driver: Driver, action: dict, *, edit: tuple[str, str] | None = None) -> dict:
    """Run the fixture writer, optionally edit the produced draft, then report the slot."""
    agent = action["agents"][0]
    step = {"step_id": action["step_id"], "attempt": action["attempt"]}
    probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
    if edit is not None:
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        text = target.read_text(encoding="utf-8-sig").replace(edit[0], edit[1], 1)
        target.write_bytes(text.encode("utf-8"))
        _agent_done(driver, action, agent["slot"])
    return driver.report(action["step_id"], action["attempt"], agent=agent["slot"])


def _drive_to_script(driver: Driver, key: str, *, limit: int = 60, act: bool = True) -> dict:
    """Advance the run until `next` issues the script step of `key`; act on it when asked."""
    for _ in range(limit):
        action = driver.next()
        if action.get("errors"):
            raise AssertionError(f"next failed: {action['errors']}")
        if action["kind"] == "script" and machine.command_key(action["command"]) == key:
            if act:
                driver.act(action)
            return action
        driver.act(action)
    raise AssertionError(f"the script step {key} was never issued")


class PhaseTableTest(unittest.TestCase):
    """§2.1: for every phase x mode, the kind of step `next` issues first."""

    EXPECTED = {
        "intake_preliminary_research": "inline-llm",
        "intake_questions_pending": "gate-text",
        "planning": "inline-llm",
        "plan_approval_pending": "gate-auq",
        "research": "dispatch",
        "research_sufficiency": "dispatch",
        "currency_check": "script",
        "source_pack": "script",
        "source_review_pending": "gate-text",
        "drafting": "dispatch",
        "revision_loop": "dispatch",
        "client_readiness": "dispatch",
        "export": "script",
    }

    def test_every_phase_issues_the_kind_of_step_the_table_names(self):
        for mode in ("full", "brief"):
            driver = Driver(temp_root(self), mode=mode, slug=f"table-{mode}")
            seen: dict[str, str] = {}
            for _ in range(200):
                action = driver.next()
                self.assertNotIn("errors", action, action)
                if action["kind"] == "terminal":
                    break
                # D-57 puts `mf render <view>` in front of the consumer named by the table; the
                # table describes the working step of the phase, so the views are not counted.
                if not (
                    action["kind"] == "script"
                    and machine.command_key(action["command"]).startswith("render.")
                ):
                    seen.setdefault(str(action["phase"]), str(action["kind"]))
                driver.act(action)
            for phase, kind in seen.items():
                self.assertEqual(
                    self.EXPECTED[phase], kind, f"{mode}/{phase} issued {kind}"
                )
            self.assertIn("export", seen)
            if mode == "brief":
                self.assertNotIn("source_review_pending", seen)
            else:
                self.assertIn("source_review_pending", seen)

    def test_sufficiency_outcomes_map_onto_phases(self):
        # §2.1 row 6: the four `sufficiency route` outcomes and where each one goes.
        self.assertEqual(
            {
                "currency_check": "currency_check",
                "gate_followup": "research_sufficiency_followup_pending",
                "research_subset": "research",
                "insufficient_gate": "research_insufficient_pending",
            },
            machine.SUFFICIENCY_TARGETS,
        )

    def test_terminal_phase_returns_a_terminal_step(self):
        driver = Driver(temp_root(self), slug="terminal")
        action = driver.run_to_end()
        self.assertEqual("terminal", action["kind"])
        self.assertEqual("done", action["phase"])
        self.assertIn("Summary:", action["text"])
        again = driver.next()
        self.assertEqual(action["step_id"], again["step_id"])

    def test_the_terminal_text_names_the_published_folder_only_when_there_is_one(self):
        """D-109: the `Published:` line is the folder the user opens, and it is skipped otherwise."""
        driver = Driver(temp_root(self), slug="published")
        action = driver.run_to_end()
        state = driver.state()
        if not machine.published_to(state):
            self.assertNotIn("Published:", action["text"])

        def mutator(current: dict) -> None:
            current["progress"]["published_to"] = "/mnt/user-data/outputs/memoforge/published"

        state_io.write_state(driver.work_dir, mutator)
        text = machine.terminal_response(driver.work_dir, driver.state())["text"]
        self.assertIn("Published: /mnt/user-data/outputs/memoforge/published", text)
        self.assertLess(text.index("Summary:"), text.index("Published:"))

    def test_the_terminal_text_names_the_memo_copy_only_when_there_is_one(self):
        """D-167: the `Memo:` line is the root-level copy the router presents, skipped otherwise."""
        driver = Driver(temp_root(self), slug="memo-line")
        action = driver.run_to_end()
        self.assertNotIn("Memo:", action["text"])

        def mutator(current: dict) -> None:
            current["progress"]["published_memo"] = "/mnt/user-data/outputs/memo-memo-line.md"

        state_io.write_state(driver.work_dir, mutator)
        text = machine.terminal_response(driver.work_dir, driver.state())["text"]
        self.assertIn("Memo: /mnt/user-data/outputs/memo-memo-line.md", text)
        lines = text.splitlines()
        deliverable_idx = next(i for i, line in enumerate(lines) if line.startswith("Deliverable:"))
        memo_idx = next(i for i, line in enumerate(lines) if line.startswith("Memo:"))
        self.assertEqual(memo_idx, deliverable_idx + 1)


class IdempotenceTest(unittest.TestCase):
    """M3: `next` and `report` are idempotent (§3.1)."""

    def test_idempotent_next_without_report(self):
        driver = Driver(temp_root(self), slug="idem")
        first = driver.next()
        second = driver.next()
        # An unfinished inline-llm step comes back unchanged: same id, same attempt, same phase.
        self.assertEqual(first["step_id"], second["step_id"])
        self.assertEqual(first["attempt"], second["attempt"])
        self.assertEqual(first["phase"], second["phase"])
        self.assertEqual(1, len({row["step_id"] for row in driver.state()["steps"]}))

        gate = driver.run_until("intake_questions_pending")
        repeat = driver.next()
        self.assertEqual(gate["step_id"], repeat["step_id"])
        self.assertEqual(gate["text"], repeat["text"])
        self.assertEqual(gate["generation"], repeat["generation"])

    def test_next_on_an_unclosed_dispatch_recovers_without_spending_budget(self):
        # §3.1 rule 3: slots without a marker are re-issued with attempt+1 and `reason: recovery`.
        driver = Driver(temp_root(self), slug="recover")
        action = driver.run_until("research")
        again = driver.next()
        self.assertEqual(action["step_id"], again["step_id"])
        self.assertEqual(action["attempt"] + 1, again["attempt"])
        self.assertEqual("recovery", again["reason"])
        self.assertEqual(0, driver.state()["attempts"]["research_dispatch_retry"])
        self.assertEqual("research", driver.state()["current_phase"])

    def test_report_is_noop_when_closed(self):
        driver = Driver(temp_root(self), slug="noop")
        action = driver.run_until("intake_preliminary_research")
        driver.act(action)
        answer = driver.report(action["step_id"], action["attempt"])
        self.assertTrue(answer["accepted"])
        self.assertTrue(answer["already_reported"])

    def test_report_emits_agent_returned(self):
        driver = Driver(temp_root(self), slug="returned")
        action = driver.run_until("research")
        driver.act(action)
        records = [
            row
            for row in events.read_events(driver.work_dir)
            if row["event"] == "agent_returned" and row["data"]["step_id"] == action["step_id"]
        ]
        self.assertEqual(3, len(records))
        self.assertEqual({"statutes", "case_law", "doctrine"}, {r["data"]["slot"] for r in records})
        self.assertTrue(all(isinstance(r["data"]["duration_seconds"], (int, float)) for r in records))

    def test_report_clears_active_without_subagent_stop(self):
        driver = Driver(temp_root(self), slug="active")
        action = driver.run_until("research")
        self.assertEqual(3, len(driver.state()["progress"]["active"]))
        driver.report(action["step_id"], action["attempt"], agent="statutes", status="fail")
        active = {row["slot"] for row in driver.state()["progress"]["active"]}
        self.assertEqual({"case_law", "doctrine"}, active)


class ProtocolRulesTest(unittest.TestCase):
    """§3.1 rules 1, 2 and 6."""

    def test_next_writes_step_issued(self):
        driver = Driver(temp_root(self), slug="rules")
        action = driver.next()
        journal = events.read_events(driver.work_dir)
        issued = [row for row in journal if row["event"] == "step_issued"]
        self.assertEqual([action["step_id"]], [row["data"]["step_id"] for row in issued])
        # D-43: `cli_call` belongs to `cli.main`; the handlers themselves emit nothing.
        self.assertEqual([], [row for row in journal if row["event"] == "cli_call"])

    def test_parallel_dispatch_is_marked_for_one_message(self):
        driver = Driver(temp_root(self), slug="parallel")
        research = driver.run_until("research")
        self.assertTrue(research["parallel"])
        self.assertEqual(3, len(research["agents"]))
        driver.act(research)
        reviewers = driver.run_until("revision_loop")
        self.assertTrue(reviewers["parallel"])
        self.assertEqual(4, len(reviewers["agents"]))

    def test_single_slot_dispatch_is_not_parallel(self):
        driver = Driver(temp_root(self), slug="serial")
        action = driver.run_until("intake_preliminary_research")
        driver.act(action)
        analyst = driver.next()
        self.assertFalse(analyst["parallel"])

    def test_report_on_an_unknown_step_is_refused(self):
        driver = Driver(temp_root(self), slug="unknown")
        driver.next()
        answer = driver.report("s-999", 1)
        self.assertFalse(answer["accepted"])
        self.assertEqual(["unknown_step: s-999"], answer["errors"])


class CompletionTest(unittest.TestCase):
    """§3.1: marker + input_sha + output sha + schema, before publication."""

    def test_autoclose_rejects_previous_attempt_output(self):
        # §3.1 rule 3: a marker of the previous attempt never closes the current one.
        driver = Driver(temp_root(self), slug="autoclose")
        action = driver.run_until("research")
        step = {"step_id": action["step_id"], "attempt": 1}
        doctrine = next(agent for agent in action["agents"] if agent["slot"] == "doctrine")
        for agent in action["agents"]:
            probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
        driver.report(action["step_id"], 1, agent="statutes")
        driver.report(action["step_id"], 1, agent="case_law")
        driver.report(action["step_id"], 1, agent="doctrine", status="fail")

        retry = driver.next()
        self.assertEqual(2, retry["attempt"])
        self.assertEqual(["doctrine"], [agent["slot"] for agent in retry["agents"]])
        self.assertFalse((driver.work_dir / "research/doctrine.json").is_file())

        # The a1 output and marker are still on disk; they must not close a2.
        self.assertTrue(
            (driver.work_dir / doctrine["expected_outputs"][0]["work_path"]).is_file()
        )
        again = driver.next()
        self.assertEqual("research", driver.state()["current_phase"])
        self.assertEqual(3, again["attempt"])
        self.assertEqual("recovery", again["reason"])
        self.assertFalse((driver.work_dir / "research/doctrine.json").is_file())

    def test_autoclose_publishes_a_slot_that_finished_without_a_report(self):
        driver = Driver(temp_root(self), slug="autoclose-ok")
        action = driver.run_until("research")
        step = {"step_id": action["step_id"], "attempt": action["attempt"]}
        for agent in action["agents"]:
            probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
        driver.next()
        self.assertEqual("research_sufficiency", driver.state()["current_phase"])
        self.assertTrue((driver.work_dir / "research/doctrine.json").is_file())
        autoclosed = [
            row
            for row in events.read_events(driver.work_dir)
            if row["event"] == "step_autoclosed" and row["data"]["step_id"] == action["step_id"]
        ]
        self.assertEqual(3, len(autoclosed))

    def test_partial_dispatch_resume_keeps_accepted_slots(self):
        driver = Driver(temp_root(self), slug="partial")
        action = driver.run_until("research")
        driver.act(action, skip_slots=("doctrine",))
        resumed = driver.next()
        self.assertEqual(action["step_id"], resumed["step_id"])
        self.assertEqual(2, resumed["attempt"])
        self.assertEqual(["doctrine"], [agent["slot"] for agent in resumed["agents"]])
        row = machine.step_row(driver.state(), action["step_id"], 1)
        statuses = {agent["slot"]: agent["status"] for agent in row["agents"]}
        self.assertEqual("ok", statuses["statutes"])
        self.assertEqual("ok", statuses["case_law"])
        self.assertTrue((driver.work_dir / "research/statutes.json").is_file())

    def test_late_superseded_result_rejected(self):
        driver = Driver(temp_root(self), slug="late")
        action = driver.run_until("research")
        driver.act(action, skip_slots=("doctrine",))
        driver.next()  # attempt 2 is now current
        late = driver.report(action["step_id"], 1, agent="doctrine", status="ok")
        self.assertFalse(late["accepted"])
        self.assertEqual(["identity_mismatch"], late["errors"])
        self.assertEqual(2, late["current_attempt"])

    def test_preseeded_draft_is_not_completed_revision(self):
        # §3.1: for a vN revision `sha == seed` is a failure, not a completed step.
        driver = Driver(temp_root(self), slug="preseed")
        reviewers = driver.run_until("revision_loop")
        self._act_reviewers_with_blocker(driver, reviewers)
        writer = None
        for _ in range(6):
            action = driver.next()
            if action["kind"] == "dispatch" and action["agents"][0]["subagent_type"].endswith("memo-writer"):
                writer = action
                break
            driver.act(action)
        self.assertIsNotNone(writer, "the revision loop never dispatched the writer")

        agent = writer["agents"][0]
        canonical = agent["expected_outputs"][0]["canonical"]
        self.assertEqual("drafts/v2.md", canonical)
        work_path = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        self.assertTrue(work_path.is_file(), "vN is pre-seeded into the writer workspace")
        self.assertEqual(
            state_io.sha256_file(driver.work_dir / canonical), state_io.sha256_file(work_path)
        )

        # The writer returns the seed untouched: the completion check must refuse it.
        machine.run_agent_log(
            namespace(
                workdir=str(driver.work_dir),
                step=writer["step_id"],
                attempt=writer["attempt"],
                slot=agent["slot"],
                state="done",
                detail=None,
                mcp=None,
            )
        )
        answer = driver.report(writer["step_id"], writer["attempt"], agent=agent["slot"])
        self.assertFalse(answer["accepted"])
        self.assertIn("writer_output_identical_to_seed", answer["errors"])
        row = machine.step_row(driver.state(), writer["step_id"], writer["attempt"])
        self.assertEqual("fail", row["agents"][0]["status"])
        self.assertIsNone(row["status"])

    def test_a_writer_that_never_changes_the_draft_ends_the_loop_under_manual_review(self):
        # A43-1 / D-153: two identical returns spend the retry; the seed is not a new version.
        driver = Driver(temp_root(self), slug="writer-stuck")
        reviewers = driver.run_until("revision_loop")
        self._act_reviewers_with_blocker(driver, reviewers)
        writer = None
        for _ in range(6):
            action = driver.next()
            if action["kind"] == "dispatch" and action["agents"][0]["subagent_type"].endswith("memo-writer"):
                writer = action
                break
            driver.act(action)
        self.assertIsNotNone(writer)
        slot = writer["agents"][0]["slot"]

        def return_seed_untouched(step_id: str, attempt: int) -> dict:
            machine.run_agent_log(
                namespace(
                    workdir=str(driver.work_dir), step=step_id, attempt=attempt, slot=slot,
                    state="done", detail=None, mcp=None,
                )
            )
            return driver.report(step_id, attempt, agent=slot)

        first = return_seed_untouched(writer["step_id"], writer["attempt"])
        self.assertIn("writer_output_identical_to_seed", first["errors"])
        retry = driver.next()  # reason: failure, attempt 2 of the same step
        self.assertEqual(writer["step_id"], retry["step_id"])
        self.assertEqual(writer["attempt"] + 1, retry["attempt"])
        second = return_seed_untouched(retry["step_id"], retry["attempt"])
        self.assertIn("writer_output_identical_to_seed", second["errors"])

        following = driver.next()
        state = driver.state()
        self.assertEqual("export", state["current_phase"], following)
        self.assertEqual("manual_review_required_on_v1", state["final_status"])
        self.assertIn("writer_failed", state["final_status_reasons"])
        self.assertEqual("drafts/v1.md", state["current_draft_path"])
        self.assertEqual(1, state["current_iteration"])
        self.assertTrue(state["remaining_blocking_issues"], "the reviewers' open blockers travel to the deliverable")
        self.assertIn("revision_incomplete", [row["banner_id"] for row in state["fallback_banners"]])
        # No reviewer was dispatched on the unchanged bytes.
        reviewer_steps = [
            row for row in state["steps"]
            if row.get("kind") == "dispatch" and row.get("phase") == "revision_loop"
            # `steps[].agents[]` keeps the bare `agent_type`; `subagent_type` is only in the action.
            and any(a["agent_type"].endswith("-reviewer") for a in row.get("agents") or [])
        ]
        self.assertEqual(1, len({row["step_id"] for row in reviewer_steps}), "only the iteration-1 reviewers ran")

    def test_a_stuck_writer_on_an_unclean_v1_is_exported_and_delivered_as_v1(self):
        # D-158: with no checked version left, `export_draft_sha` falls back to the last one — so the
        # seed the revision rejected has to leave `draft_versions[]`, or the memo is delivered as v2.
        driver = Driver(temp_root(self), slug="writer-stuck-unclean")
        reviewers = driver.run_until("revision_loop")
        self._act_reviewers_with_blocker(driver, reviewers)
        self._spend_the_writer_on_the_seed(driver)
        # v1 still carries a mechanical blocker after its fix budget: both deterministic reports
        # drifted from `published[]`, so D-68 leaves `lint_clean`/`citations_clean` false everywhere.
        for name in ("lint.json", "citations.json"):
            report = state_io.read_json(driver.work_dir / name)
            report["tampered"] = True
            state_io.write_json_atomic(driver.work_dir / name, report)

        driver.next()  # the writer step closes `fail` and the loop exits to `export`
        state = driver.state()
        self.assertEqual("export", state["current_phase"])
        self.assertEqual(
            [1],
            [int(row["version"]) for row in state["draft_versions"]],
            "the rejected seed is not a version the export may select",
        )
        self.assertFalse(
            any(row["lint_clean"] and row["citations_clean"] for row in state["draft_versions"]),
            "the scenario is `no_checked_draft`: nothing vouches for v1 either",
        )

        driver.run_to_end()
        state = driver.state()
        self.assertEqual("manual_review_required_on_v1", state["final_status"])
        self.assertIn("writer_failed", state["final_status_reasons"])
        self.assertIn("no_checked_draft", state["final_status_reasons"])
        self.assertEqual(1, state["current_iteration"])
        delivered = [
            name for name in ("deliverable.docx", "deliverable.md") if (driver.work_dir / name).is_file()
        ]
        self.assertTrue(delivered, "M9: the run still delivers")

    def _spend_the_writer_on_the_seed(self, driver: Driver) -> None:
        """Both writer attempts return `drafts/v2.md` exactly as `revision next` seeded it (D-153)."""
        writer = None
        for _ in range(6):
            action = driver.next()
            if action["kind"] == "dispatch" and action["agents"][0]["subagent_type"].endswith("memo-writer"):
                writer = action
                break
            driver.act(action)
        self.assertIsNotNone(writer, "the revision loop never dispatched the writer")
        slot = writer["agents"][0]["slot"]
        step = {"step_id": writer["step_id"], "attempt": writer["attempt"]}
        for index in range(2):
            _agent_done(driver, step, slot)
            answer = driver.report(step["step_id"], step["attempt"], agent=slot)
            self.assertIn("writer_output_identical_to_seed", answer["errors"])
            if index == 0:
                retry = driver.next()  # reason: failure, attempt 2 of the same step
                step = {"step_id": retry["step_id"], "attempt": retry["attempt"]}

    @staticmethod
    def _act_reviewers_with_blocker(driver: Driver, action: dict) -> None:
        """Every reviewer approves except `logic`, which raises a grounded hard-fail blocker."""
        from memoforge import review as review_module

        hard_fail = next(item["id"] for item in review_module.load_checklist("logic") if item["hard_fail"])
        step = {"step_id": action["step_id"], "attempt": action["attempt"]}
        for agent in action["agents"]:
            slot = agent["slot"]
            probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
            if slot == "logic":
                target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
                document = state_io.read_json(target)
                for item in document["checklist"]:
                    if item["id"] == hard_fail:
                        item["pass"] = False
                document["issues"] = [
                    {
                        "severity": "blocker",
                        "category": "rule_unexplained",
                        "section_id": "s-3",
                        "issue": "The rule is applied before it is explained.",
                        "suggestion": "Explain the provision before applying it.",
                        "checklist_id": hard_fail,
                    }
                ]
                document["verdict"] = "needs_revision"
                state_io.write_json_atomic(target, document)
                machine.run_agent_log(
                    namespace(
                        workdir=str(driver.work_dir),
                        step=action["step_id"],
                        attempt=action["attempt"],
                        slot=slot,
                        state="done",
                        detail=None,
                        mcp=None,
                    )
                )
            driver.report(action["step_id"], action["attempt"], agent=slot)


MISSING_TOKEN_ISSUE = {
    "severity": "blocker",
    "category": "unsupported_claim",
    "section_id": "s-4-2",
    "issue": "The rule statement carries no [[src:]] token.",
    "suggestion": "Add the token of the provision the sentence states.",
    "issue_category": "unsupported_claim",
}
"""D-165: the single blocker the 2026-09-16 run could not close inside its iteration budget."""


def _next_reviewer_dispatch(driver: Driver, *, limit: int = 12) -> dict:
    """Advance until `next` dispatches the reviewers of an iteration; the action is not acted on."""
    for _ in range(limit):
        action = driver.next()
        if action.get("errors"):
            raise AssertionError(f"next failed: {action['errors']}")
        if action["kind"] == "dispatch" and all(
            agent["slot"] in review.REVIEWER_KINDS for agent in action["agents"]
        ):
            return action
        driver.act(action)
    raise AssertionError("the revision loop never dispatched reviewers again")


def _next_writer_dispatch(driver: Driver, *, limit: int = 8) -> dict:
    for _ in range(limit):
        action = driver.next()
        if action.get("errors"):
            raise AssertionError(f"next failed: {action['errors']}")
        if action["kind"] == "dispatch" and action["agents"][0]["subagent_type"].endswith("memo-writer"):
            return action
        driver.act(action)
    raise AssertionError("the revision loop never dispatched the writer")


class TargetedCitationFixTest(unittest.TestCase):
    """D-165: the budget ends on one missing `[[src:]]` token, so one writer pass + `citations` runs."""

    @staticmethod
    def _act_reviewers_with_a_missing_token(driver: Driver, action: dict) -> None:
        """Every reviewer approves except `citations`, which reports one `unsupported_claim`."""
        step = {"step_id": action["step_id"], "attempt": action["attempt"]}
        for agent in action["agents"]:
            slot = agent["slot"]
            probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
            if slot == "citations":
                target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
                document = state_io.read_json(target)
                document["issues"] = [dict(MISSING_TOKEN_ISSUE)]
                document["verdict"] = "needs_revision"
                state_io.write_json_atomic(target, document)
                _agent_done(driver, action, slot)
            driver.report(action["step_id"], action["attempt"], agent=slot)

    def _run_to_the_targeted_reviewer(self, driver: Driver) -> dict:
        """Drive iterations 1–2, then branch 9's writer, and stop on the citations-only dispatch."""
        first = driver.run_until("revision_loop")
        CompletionTest._act_reviewers_with_blocker(driver, first)
        second = _next_reviewer_dispatch(driver)
        self.assertEqual(2, driver.state()["current_iteration"])
        self._act_reviewers_with_a_missing_token(driver, second)

        writer = _next_writer_dispatch(driver)
        self.assertEqual("drafts/v3.md", writer["agents"][0]["expected_outputs"][0]["canonical"])
        state = driver.state()
        self.assertEqual(1, state["attempts"]["targeted_fix"])
        self.assertEqual({"iteration": 3, "reviewers": ["citations"]}, state["targeted_fix"])
        self.assertEqual(3, state["current_iteration"])
        driver.act(writer)

        third = _next_reviewer_dispatch(driver)
        self.assertEqual(["citations"], [agent["slot"] for agent in third["agents"]])
        return third

    def test_the_targeted_pass_closes_the_memo_when_the_token_is_added(self):
        driver = Driver(temp_root(self), slug="targeted-fix")
        third = self._run_to_the_targeted_reviewer(driver)
        driver.act(third)  # the citations reviewer approves v3
        driver.run_to_end()

        state = driver.state()
        self.assertEqual("approved_on_v3", state["final_status"])
        self.assertEqual(1, state["attempts"]["targeted_fix"])
        record = review.iteration_record(state, 3)
        self.assertEqual(["citations"], record["reviewers"])
        self.assertEqual(["citations"], record["coverage"])
        self.assertEqual([], record["failed_reviewers"])

    def test_a_token_still_missing_after_the_targeted_pass_forces_the_exit(self):
        driver = Driver(temp_root(self), slug="targeted-fix-open")
        third = self._run_to_the_targeted_reviewer(driver)
        self._act_reviewers_with_a_missing_token(driver, third)
        driver.run_to_end()

        state = driver.state()
        self.assertEqual("forced_exit_on_v3_with_remaining_issues", state["final_status"])
        self.assertEqual(1, state["attempts"]["targeted_fix"])
        self.assertTrue(state["remaining_blocking_issues"])


class DeclaredInputTest(unittest.TestCase):
    """D-114: the §3.1 rejection says why an input sha moved — the agent rewrote its own input."""

    def test_input_sha_mismatch_names_the_cause(self):
        work_dir = temp_root(self) / "memo-20260101T000000Z-input-sha"
        marker_dir = stepctx.step_dir(work_dir, "s-009", 1, "currency")
        marker_dir.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            marker_dir / "done.json",
            {
                "task_id": "memo-20260101T000000Z-input-sha",
                "step_id": "s-009",
                "attempt": 1,
                "slot": "currency",
                "input_sha": {"research/sources.json": "b" * 64},
                "output_sha": {},
            },
        )
        row = {
            "step_id": "s-009",
            "attempt": 1,
            "inputs": {"research/sources.json": "a" * 64},
            "expected_outputs": [
                {
                    "work_path": "steps/s-009/a1/currency/currency.json",
                    "canonical_path": "research/currency.json",
                    "schema": "currency",
                }
            ],
        }
        completion = machine.check_completion(
            work_dir, {}, row, {"slot": "currency", "agent_type": "memoforge:currency-checker"}
        )
        self.assertFalse(completion["ok"])
        self.assertIn(
            "input_sha_mismatch: research/sources.json (declared input changed during the step"
            " — agents must not run sources verify/liveness/register on their own input)",
            completion["errors"],
        )


class BudgetTest(unittest.TestCase):
    """§2.2: a `reason: failure` retry spends exactly one budget; recovery spends none."""

    def test_research_dispatch_retry_is_spent_once_then_the_run_continues(self):
        driver = Driver(temp_root(self), slug="budget")
        action = driver.run_until("research")
        driver.act(action, fail_slots=("doctrine",))
        retry = driver.next()
        self.assertEqual("failure", retry["reason"])
        self.assertEqual(["doctrine"], [agent["slot"] for agent in retry["agents"]])
        self.assertEqual(1, driver.state()["attempts"]["research_dispatch_retry"])

        driver.act(retry, fail_slots=("doctrine",))
        after = driver.next()
        self.assertEqual(1, driver.state()["attempts"]["research_dispatch_retry"])
        self.assertEqual("research_sufficiency", driver.state()["current_phase"])
        self.assertEqual(["case_law", "statutes"], driver.state()["dispatched_researchers"])
        self.assertTrue(
            any("doctrine" in str(row.get("message")) for row in driver.state()["drafting_warnings"])
        )
        # §2.2: the budget test asserts the number of real dispatches, not only the counter.
        researcher_rows = [
            row
            for row in driver.state()["steps"]
            if row["kind"] == "dispatch" and machine.agent_names(row) == ["legal-researcher"] * len(row["agents"])
        ]
        self.assertEqual(2, len(researcher_rows))
        self.assertEqual("dispatch", after["kind"])

    def test_zero_valid_layers_go_to_the_insufficient_gate(self):
        driver = Driver(temp_root(self), slug="nolayers")
        action = driver.run_until("research")
        slots = tuple(agent["slot"] for agent in action["agents"])
        driver.act(action, fail_slots=slots)
        retry = driver.next()
        driver.act(retry, fail_slots=slots)
        follow = driver.next()
        self.assertEqual("research_insufficient_pending", follow["phase"])
        self.assertEqual("gate-text", follow["kind"])


class InlineAndPlanBudgetTest(unittest.TestCase):
    """§2.2: `inline_llm_retry` and `plan_edit`."""

    def test_inline_llm_retry_budget_ends_in_failed(self):
        # §2.2/§3.1: a rejected inline result closes its attempt; the next one is a new identity
        # with `reason: failure` and the schema errors of the previous try (Codex 3).
        driver = Driver(temp_root(self), slug="inline")
        action = driver.run_until("planning")
        step_id = action["step_id"]
        for attempt in range(1, limits.MAX_INLINE_LLM_RETRY + 2):
            self.assertEqual(attempt, action["attempt"])
            target = driver.work_dir / action["write_to"]
            target.parent.mkdir(parents=True, exist_ok=True)
            state_io.write_json_atomic(target, {"not": "a plan"})
            answer = driver.report(step_id, attempt)
            self.assertFalse(answer["accepted"])
            self.assertEqual(attempt + 1, answer["retry"]["attempt"])
            self.assertEqual(attempt, driver.state()["attempts"]["inline_llm_retry"][step_id])
            self.assertEqual("fail", machine.step_row(driver.state(), step_id, attempt)["status"])
            # The same report a second time is the no-op of a closed step: no second charge.
            repeat = driver.report(step_id, attempt)
            self.assertTrue(repeat["already_reported"])
            self.assertEqual(attempt, driver.state()["attempts"]["inline_llm_retry"][step_id])
            action = driver.next()
            if action["kind"] != "inline-llm":
                break
            self.assertEqual(step_id, action["step_id"])
            self.assertEqual("failure", action["reason"])
            self.assertIn("rejected", action["instruction"])
        self.assertEqual("script", action["kind"])
        self.assertIn("plan_invalid", action["command"])
        machine.run_command(list(action["command"]))
        self.assertEqual("failed", driver.state()["current_phase"])

    def test_reported_fail_is_not_undone_by_the_file_left_behind(self):
        # Codex 3: after `report --status fail` the leftover output must not autoclose the step.
        driver = Driver(temp_root(self), slug="inline-fail")
        action = driver.run_until("planning")
        target = driver.work_dir / action["write_to"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, probe.fixture_plan())
        answer = driver.report(action["step_id"], action["attempt"], status="fail")
        self.assertFalse(answer["accepted"])
        self.assertIn("reported_fail", answer["errors"])

        retry = driver.next()
        self.assertEqual("inline-llm", retry["kind"])
        self.assertEqual(action["step_id"], retry["step_id"])
        self.assertEqual(2, retry["attempt"])
        self.assertEqual("failure", retry["reason"])
        self.assertNotEqual(action["write_to"], retry["write_to"])
        self.assertTrue(target.is_file())
        self.assertFalse((driver.work_dir / "plan.json").is_file())
        self.assertEqual("planning", driver.state()["current_phase"])

    def test_plan_edit_becomes_the_task_of_the_next_planning_step(self):
        # Codex 11 / §2.4 Edit: the planner gets the previous plan and the correction itself.
        driver = Driver(temp_root(self), slug="planedit-task")
        gate = driver.run_until("plan_approval_pending")
        driver.report(
            gate["step_id"],
            gate["attempt"],
            answers=json.dumps({"Plan": "Edit", "Change": "add the Irish rules"}),
            generation=gate.get("generation", 0),
        )
        action = driver.next()
        self.assertEqual("inline-llm", action["kind"])
        self.assertEqual("planning", action["phase"])
        self.assertIn("`plan.json`", action["instruction"])
        self.assertIn("add the Irish rules", action["instruction"])
        self.assertTrue((driver.work_dir / "plan.json").is_file())

    def test_plan_edit_budget_forces_approve_of_the_last_version(self):
        driver = Driver(temp_root(self), slug="planedit")
        edit = json.dumps({"Plan": "Edit"})
        for round_number in range(1, limits.MAX_PLAN_EDIT + 1):
            gate = driver.run_until("plan_approval_pending")
            driver.report(
                gate["step_id"], gate["attempt"], answers=edit, generation=gate.get("generation", 0)
            )
            driver.next()
            self.assertEqual("planning", driver.state()["current_phase"])
            self.assertEqual(round_number, driver.state()["attempts"]["plan_edit"])
        gate = driver.run_until("plan_approval_pending")
        driver.report(
            gate["step_id"], gate["attempt"], answers=edit, generation=gate.get("generation", 0)
        )
        # §2.2: the forced approval belongs to the version that carries the last correction, so the
        # edit is executed first and only the plan it produced is approved.
        planning = driver.next()
        self.assertEqual("inline-llm", planning["kind"])
        self.assertEqual("planning", planning["phase"])
        self.assertEqual(limits.MAX_PLAN_EDIT + 1, driver.state()["attempts"]["plan_edit"])
        driver.act(planning)
        after = driver.next()
        state = driver.state()
        self.assertEqual("research", state["current_phase"])
        self.assertEqual("dispatch", after["kind"])
        self.assertEqual(limits.MAX_PLAN_EDIT + 1, state["attempts"]["plan_edit"])
        self.assertTrue(
            any(row["banner_id"] == "plan_forced_approve" for row in state["fallback_banners"])
        )


class GateRoutingTest(unittest.TestCase):
    """§2.4: gates only on segment boundaries, generation and the text fallback."""

    def test_no_gate_inside_segment(self):
        driver = Driver(temp_root(self), slug="segment")
        for _ in range(200):
            action = driver.next()
            if action["kind"] == "terminal":
                break
            if action["kind"] in ("gate-text", "gate-auq"):
                self.assertTrue(
                    phases.is_gate(str(action["phase"])),
                    f"gate issued in non-gate phase {action['phase']}",
                )
            driver.act(action)

    def test_auq_gate_carries_the_plan_digest_on_issue_and_reissue(self):
        """D-86: the plan gate hands the router the plan to print before the AskUserQuestion."""
        driver = Driver(temp_root(self), slug="auq-digest")
        issued = driver.run_until("plan_approval_pending")
        reissued = driver.next()
        self.assertTrue(reissued.get("reissued"))
        self.assertEqual(issued["step_id"], reissued["step_id"])
        for action in (issued, reissued):
            self.assertEqual("gate-auq", action["kind"])
            text = str(action.get("text") or "")
            self.assertTrue(text.strip(), "gate-auq must carry the plan as `text`")
            self.assertIn("plan.json", text)
            self.assertIn("Retention of customer records", text)
            self.assertIn("Recommended mode: full", text)
            self.assertNotIn("Reply with one of:", text)
        self.assertEqual(issued["text"], reissued["text"])

    def test_auq_no_answer_switches_to_equivalent_text_gate(self):
        driver = Driver(temp_root(self), slug="auq")
        auq = driver.run_until("plan_approval_pending")
        self.assertEqual("gate-auq", auq["kind"])
        self.assertEqual(0, auq["generation"])
        self.assertIn("approve [brief|full]", auq["text_fallback"])

        answer = driver.report(auq["step_id"], auq["attempt"], status="no_answer")
        self.assertTrue(answer["accepted"])
        self.assertEqual(1, answer["generation"])
        switched = [r for r in events.read_events(driver.work_dir) if r["event"] == "gate_channel_switched"]
        self.assertEqual(1, len(switched))

        text_gate = driver.next()
        self.assertEqual("gate-text", text_gate["kind"])
        self.assertEqual(auq["step_id"], text_gate["step_id"])
        self.assertEqual(1, text_gate["generation"])
        self.assertIn("approve [brief|full]", text_gate["text"])

        stale = driver.parse_gate({**text_gate, "generation": 0}, "approve full")
        self.assertEqual(["stale_generation"], stale["errors"])

        driver.parse_gate(text_gate, "approve full")
        driver.next()
        self.assertEqual("research", driver.state()["current_phase"])
        self.assertEqual("full", driver.state()["mode"])

    def test_report_answers_without_generation_is_rejected(self):
        # §3.1 order / D-34: identity -> generation -> closed; an answer must name its channel.
        driver = Driver(temp_root(self), slug="generation")
        auq = driver.run_until("plan_approval_pending")
        before = driver.state()
        answer = driver.report(
            auq["step_id"], auq["attempt"], answers=json.dumps({"Plan": "Approve", "Mode": "Full"})
        )
        self.assertFalse(answer["accepted"])
        self.assertEqual(["generation_required"], answer["errors"])
        self.assertEqual(auq["generation"], answer["expected_generation"])
        self.assertEqual(before, driver.state())
        self.assertIsNone(
            machine.step_row(driver.state(), auq["step_id"], auq["attempt"])["status"]
        )

    def test_currency_regate_route_to_followup_gate(self):
        driver = Driver(temp_root(self), slug="regate")
        action = driver.run_until("currency_check")
        while action["kind"] != "dispatch":
            driver.act(action)
            action = driver.next()
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        document = probe.fixture_currency(driver.work_dir)
        critical = [
            row["source_id"]
            for row in document["sources"]
            if row["source_id"].endswith("source-1")
        ]
        for row in document["sources"]:
            if row["source_id"] in critical:
                row["status"] = "do_not_use"
        document["blocking"] = critical
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, document)
        machine.run_agent_log(
            namespace(
                workdir=str(driver.work_dir),
                step=action["step_id"],
                attempt=action["attempt"],
                slot=agent["slot"],
                state="done",
                detail=None,
                mcp=None,
            )
        )
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])
        second_pass = driver.next()
        self.assertEqual("research_sufficiency", driver.state()["current_phase"])
        self.assertEqual(1, driver.state()["attempts"]["currency_regate"])

        # The second sufficiency pass asks the user: the run re-enters the phase-7 gate (§2.1 row 6).
        sufficiency_agent = second_pass["agents"][0]
        target = driver.work_dir / sufficiency_agent["expected_outputs"][0]["work_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            target,
            {
                "reviewer": "research_sufficiency",
                "overall_verdict": "targeted_followup_needed",
                "blocking_gaps": [
                    {
                        "gap": "The retention period the client actually applies is unknown.",
                        "target": "user",
                        "status": "missing",
                        "why_blocking": "The conclusion turns on it.",
                        "followup_question": {
                            "question": "Which retention period does the client apply today?",
                            "header": "Period",
                            "options": [
                                {"label": "Five years", "description": "Statutory minimum."},
                                {"label": "Seven years", "description": "Tax rule."},
                            ],
                            "default_assumption_if_skipped": "Seven years.",
                        },
                    }
                ],
                "drafting_warnings": [],
            },
        )
        machine.run_agent_log(
            namespace(
                workdir=str(driver.work_dir),
                step=second_pass["step_id"],
                attempt=second_pass["attempt"],
                slot=sufficiency_agent["slot"],
                state="done",
                detail=None,
                mcp=None,
            )
        )
        driver.report(second_pass["step_id"], second_pass["attempt"], agent=sufficiency_agent["slot"])
        gate = None
        for _ in range(6):  # D-57 renders the sufficiency view before `sufficiency route`
            gate = driver.next()
            if gate["kind"] != "script":
                break
            machine.run_command(list(gate["command"]))
        self.assertEqual("gate-text", gate["kind"])
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        self.assertIn("Which retention period", gate["text"])

    def test_cancel_after_interruption_in_each_autonomous_phase(self):
        for phase in ("research", "research_sufficiency", "currency_check", "drafting"):
            with self.subTest(phase=phase):
                driver = Driver(temp_root(self), slug="cancel-" + phase.replace("_", "-"))
                driver.run_until(phase)
                before = len(driver.state()["steps"])
                state_io.write_state(
                    driver.work_dir, lambda current: current.update({"cancel_requested": True})
                )
                step = driver.next()
                self.assertEqual("script", step["kind"])
                self.assertIn("finalize", step["command"])
                machine.run_command(list(step["command"]))
                final = driver.next()
                self.assertEqual("terminal", final["kind"])
                self.assertEqual("cancelled_by_user", driver.state()["current_phase"])
                self.assertLessEqual(len(driver.state()["steps"]), before + 3)


class ReviewLoopTest(unittest.TestCase):
    """§4.5: the deterministic checks come before the reviewers; polish is re-checked."""

    def test_reviewers_not_dispatched_until_lint_clean_or_rounds_exhausted(self):
        driver = Driver(temp_root(self), slug="reviewers")
        order: list[str] = []
        for _ in range(200):
            action = driver.next()
            if action["kind"] == "terminal":
                break
            if action["phase"] in ("drafting", "revision_loop"):
                if action["kind"] == "script":
                    order.append(machine.command_key(action["command"]))
                elif action["kind"] == "dispatch":
                    order.append("dispatch:" + ",".join(a["slot"] for a in action["agents"]))
            driver.act(action)
        reviewer_index = next(
            index for index, name in enumerate(order) if name.startswith("dispatch:logic")
        )
        self.assertIn("draft.finish", order[:reviewer_index])

    def test_polish_changes_go_through_lint_and_citations(self):
        driver = Driver(temp_root(self), slug="polish")
        action = driver.run_until("client_readiness")
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        document = probe.fixture_client_readiness(str(driver.state()["current_draft_sha"]), 1)
        document["verdict"] = "needs_final_polish"
        document["issues"] = [
            {
                "section_id": "s-3",
                "severity": "minor",
                "issue": "The conclusion could name the owner earlier.",
                "suggestion": "Move the owner to the first sentence.",
            }
        ]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, document)
        machine.run_agent_log(
            namespace(
                workdir=str(driver.work_dir),
                step=action["step_id"],
                attempt=action["attempt"],
                slot=agent["slot"],
                state="done",
                detail=None,
                mcp=None,
            )
        )
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

        polish = driver.next()
        self.assertEqual("dispatch", polish["kind"])
        self.assertEqual("memo-writer", polish["agents"][0]["subagent_type"].split(":")[-1])
        self.assertEqual(1, driver.state()["attempts"]["client_polish"])
        driver.act(polish)

        order: list[str] = []
        for _ in range(6):
            step = driver.next()
            if step["kind"] == "script":
                order.append(machine.command_key(step["command"]))
            elif step["kind"] == "dispatch":
                order.append("dispatch:" + step["agents"][0]["subagent_type"].split(":")[-1])
                break
            else:
                break
            driver.act(step)
        self.assertEqual(["draft.finish", "dispatch:client-readiness-reviewer"], order)


class ProgressTest(unittest.TestCase):
    """§2.2 `progress`: the denominator is the reachable route of the current config."""

    def test_progress_denominator_matches_route(self):
        full = Driver(temp_root(self), mode="full", slug="progress-full")
        full.run_until("research")
        progress = full.state()["progress"]
        self.assertEqual(progress["total"], len(progress["route"]))
        self.assertEqual(13, progress["total"])
        self.assertIn("source_review_pending", progress["route"])
        self.assertEqual(5, progress["position"])

        brief = Driver(temp_root(self), mode="brief", slug="progress-brief")
        brief.run_until("research")
        progress = brief.state()["progress"]
        self.assertEqual(progress["total"], len(progress["route"]))
        self.assertEqual(12, progress["total"])
        self.assertNotIn("source_review_pending", progress["route"])

    def test_description_uses_the_route_denominator(self):
        driver = Driver(temp_root(self), slug="description")
        action = driver.run_until("research")
        for agent in action["agents"]:
            self.assertTrue(agent["description"].startswith("P5/13 · legal-researcher · "))


class CrashIdempotenceTest(unittest.TestCase):
    """§2.2: a crash between the file action and the state close is replayed, not repeated."""

    STEP_COMMANDS = (
        "sufficiency.route",
        "sources.pack",
        "draft.finish",
        "review.aggregate",
        "revision.next",
        "docx.render",
        "finalize",
    )

    def test_crash_between_action_and_step_close_is_idempotent(self):
        driver = Driver(temp_root(self), slug="crash")
        seen: set[str] = set()
        for _ in range(200):
            action = driver.next()
            if action["kind"] == "terminal":
                break
            if action["kind"] == "script":
                key = machine.command_key(action["command"])
                if key in self.STEP_COMMANDS and key not in seen:
                    seen.add(key)
                    with self.subTest(command=key):
                        first = machine.run_command(list(action["command"]))
                        published_before = _published_shas(driver.state())
                        # Crash after the command, before the orchestrator called `next`: the repeat
                        # is a no-op returning the stored result, and `next` does not re-run it.
                        second = machine.run_command(list(action["command"]))
                        self.assertFalse(second.get("errors"), second)
                        self.assertEqual(
                            published_before,
                            _published_shas(driver.state()),
                            f"{key} republished different content on a repeat",
                        )
                        row = machine.step_row(driver.state(), action["step_id"], action["attempt"])
                        self.assertEqual("ok", row["status"])
                        self.assertIsInstance(first, dict)
                    continue
            driver.act(action)
        self.assertEqual(set(self.STEP_COMMANDS), seen)

    def test_unclosed_script_step_is_replayed_by_next(self):
        driver = Driver(temp_root(self), slug="replay")
        action = driver.run_until("currency_check")
        self.assertEqual("script", action["kind"])
        self.assertEqual("sources.liveness", machine.command_key(action["command"]))
        # The orchestrator never ran the command: `next` replays it and closes the step.
        follow = driver.next()
        row = machine.step_row(driver.state(), action["step_id"], action["attempt"])
        self.assertEqual("ok", row["status"])
        self.assertNotEqual(action["step_id"], follow["step_id"])


class StepAwareScriptTest(unittest.TestCase):
    """D-28: `sources liveness|verify` and `docx validate` are ordinary `--step/--attempt` steps."""

    D28_COMMANDS = (("sources", "liveness"), ("sources", "verify"), ("docx", "validate"))

    def test_every_script_command_carries_its_identity(self):
        driver = Driver(temp_root(self), slug="d28")
        for tokens in self.D28_COMMANDS:
            with self.subTest(command=".".join(tokens)):
                action = machine.script_step(
                    driver.work_dir, driver.state(), *tokens, chat="probe"
                )
                command = list(action["command"])
                self.assertIn("--step", command)
                self.assertIn("--attempt", command)
                self.assertEqual(action["step_id"], command[command.index("--step") + 1])
                self.assertEqual(
                    str(action["attempt"]), command[command.index("--attempt") + 1]
                )
                self.assertIn(".".join(tokens), machine.SELF_CLOSING)

    def test_no_completion_predicate_second_guesses_the_orchestrator(self):
        self.assertFalse(hasattr(machine, "REPLAY_COMPLETE"))
        self.assertFalse(hasattr(machine, "_sources_checked"))


class LintAttachmentTest(unittest.TestCase):
    """D-39: `lint.json`/`citations.json` reach the reviewers only on `lint_not_converged`."""

    def _driver(self, slug: str, *, clean: bool, banner: bool) -> Driver:
        driver = Driver(temp_root(self), slug=slug)
        sha = "a" * 64
        for name in ("lint.json", "citations.json"):
            state_io.write_json_atomic(
                driver.work_dir / name, {"draft_sha": sha, "clean": clean}
            )

        def mutate(state: dict) -> None:
            state["current_draft_sha"] = sha
            state["current_draft_path"] = "drafts/v1.md"
            if banner:
                state["fallback_banners"] = [
                    {
                        "banner_id": "lint_not_converged",
                        "condition_key": "lint_not_converged",
                        "text": "…",
                    }
                ]

        state_io.write_state(driver.work_dir, mutate)
        return driver

    def _attachment(self, driver: Driver) -> str:
        state = driver.state()
        return machine.lint_attachment(
            driver.work_dir, state, str(state.get("current_draft_sha") or "")
        )

    def test_unconverged_and_dirty_draft_attaches_the_reports(self):
        driver = self._driver("d39-attach", clean=False, banner=True)
        self.assertIn("lint.json", self._attachment(driver))

    def test_dirty_draft_without_the_banner_attaches_nothing(self):
        driver = self._driver("d39-rounds-left", clean=False, banner=False)
        self.assertEqual("", self._attachment(driver))

    def test_clean_draft_attaches_nothing_even_after_the_banner(self):
        driver = self._driver("d39-clean", clean=True, banner=True)
        self.assertEqual("", self._attachment(driver))

    def test_the_reviewer_spec_carries_the_computed_attachment(self):
        driver = self._driver("d39-spec", clean=False, banner=False)
        specs = machine.reviewer_specs(driver.work_dir, driver.state(), ["logic"], 1)
        self.assertEqual("", specs[0]["extra"]["lint_attachment"])


class DraftCheckRerunTest(unittest.TestCase):
    """D-54: `draft finish` belongs to the draft sha it ran on (Codex 1, 7; D-117 merged the three)."""

    def _writer_dispatch(self, driver: Driver) -> dict:
        action = driver.run_until("drafting")
        while action["kind"] != "dispatch":  # D-57 renders the writer's md views first
            driver.act(action)
            action = driver.next()
        return action

    def test_writer_fix_re_issues_both_checks_for_the_new_bytes(self):
        driver = Driver(temp_root(self), slug="d54")
        writer = self._writer_dispatch(driver)
        _act_writer(driver, writer, edit=BREAK_RISK)
        # D-117: one step does the anchors, the lint and the citation audit.
        finish = driver.next()
        self.assertEqual("draft.finish", machine.command_key(finish["command"]), finish)
        result = machine.run_command(list(finish["command"]))
        self.assertGreaterEqual(result["anchor"]["anchors_inserted"], 1)
        self.assertIn("clean", result["lint"])
        self.assertIn("clean", result["citations"])

        fix = driver.next()
        self.assertEqual("dispatch", fix["kind"])
        self.assertEqual("memo-writer", fix["agents"][0]["subagent_type"].split(":")[-1])
        self.assertEqual(1, driver.state()["attempts"]["lint_fix"]["1"])
        before = driver.state()["current_draft_sha"]
        _act_writer(driver, fix)
        self.assertNotEqual(before, driver.state()["current_draft_sha"])

        rerun = driver.next()
        self.assertEqual("script", rerun["kind"])
        self.assertEqual("draft.finish", machine.command_key(rerun["command"]))
        self.assertEqual(finish["step_id"], rerun["step_id"])
        self.assertEqual(2, rerun["attempt"])
        self.assertEqual("rerun", rerun["reason"])
        # The rerun is free and the fix budget is untouched: no second fix, no `lint_not_converged`.
        self.assertEqual(1, driver.state()["attempts"]["lint_fix"]["1"])
        driver.act(rerun)
        state = driver.state()
        sha = machine.current_draft_sha(state)
        for name in ("lint.json", "citations.json"):
            self.assertIsNotNone(machine.report_for_draft(driver.work_dir, state, name, sha), name)

    def test_report_that_drifted_from_published_is_not_a_verdict(self):
        # D-41: `_draft_checks` reads through `published[]`; drift re-issues the check.
        driver = Driver(temp_root(self), slug="d41-lint")
        _drive_to_script(driver, "draft.finish")
        self.assertEqual((True, True), machine._draft_checks(driver.work_dir, driver.state()))
        report = state_io.read_json(driver.work_dir / "lint.json")
        report["findings"] = list(report.get("findings") or [])
        report["_tampered"] = True
        state_io.write_json_atomic(driver.work_dir / "lint.json", report)
        self.assertEqual((False, True), machine._draft_checks(driver.work_dir, driver.state()))

        step = driver.next()
        self.assertEqual("script", step["kind"])
        self.assertEqual("draft.finish", machine.command_key(step["command"]))
        self.assertEqual("drafting", driver.state()["current_phase"])


def _ask_for_polish(driver: Driver, action: dict) -> None:
    """Answer the client-readiness dispatch with `needs_final_polish` (§2.1 row 14)."""
    agent = action["agents"][0]
    target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
    document = probe.fixture_client_readiness(str(driver.state()["current_draft_sha"]), 1)
    document["verdict"] = "needs_final_polish"
    document["issues"] = [
        {
            "section_id": "s-3",
            "severity": "minor",
            "issue": "The conclusion could name the owner earlier.",
            "suggestion": "Move the owner to the first sentence.",
        }
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    state_io.write_json_atomic(target, document)
    _agent_done(driver, action, agent["slot"])
    driver.report(action["step_id"], action["attempt"], agent=agent["slot"])


class PinpointNotInRawTest(unittest.TestCase):
    """D-204: a C-09 starts the ordinary lint-fix round of v1 — and changes nothing anywhere else."""

    PHANTOM = ("art 5(1)(e)]]", "art 9(2)]]")
    """The fixture draft's pinpoint moved to an article its saved text never prints."""

    def _code_saved(self, driver: Driver) -> None:
        """Every saved text becomes code-saved before the freeze pins the kinds (D-200)."""
        registry = sources.read_registry(driver.work_dir)
        for record in registry["sources"].values():
            if record.get("raw_path"):
                record["raw_kind"] = "full_text"
        with sources.sources_lock(driver.work_dir):
            sources.write_registry(driver.work_dir, registry)

    def _v1_with_phantom(self, slug: str) -> Driver:
        """A full-mode run up to `draft finish` of a v1 whose only defect is a C-09."""
        driver = Driver(temp_root(self), slug=slug)
        driver.run_until("source_pack")
        self._code_saved(driver)
        action = driver.run_until("drafting")
        while action["kind"] != "dispatch":  # D-57 renders the writer's md views first
            driver.act(action)
            action = driver.next()
        _act_writer(driver, action, edit=self.PHANTOM)
        _drive_to_script(driver, "draft.finish")
        state = driver.state()
        self.assertEqual((True, True), machine._draft_checks(driver.work_dir, state))
        self.assertTrue(self._c09(driver), "the phantom pinpoint must draw a C-09")
        return driver

    @staticmethod
    def _c09(driver: Driver) -> list[dict]:
        report = state_io.read_json(driver.work_dir / "citations.json")
        return [row for row in report["findings"] if row["rule"] == "C-09"]

    def test_c09_alone_runs_one_lint_fix_round_then_the_loop_without_the_banner(self):
        driver = self._v1_with_phantom("c09-round")
        self.assertTrue(machine._has_c09(driver.work_dir, driver.state()))

        fix = driver.next()
        self.assertEqual("dispatch", fix["kind"])
        self.assertTrue(fix["agents"][0]["subagent_type"].endswith("memo-writer"), fix)
        self.assertIn("fix the pinpoint or remove it", fix["agents"][0]["prompt"])
        self.assertEqual(1, driver.state()["attempts"]["lint_fix"]["1"])
        _act_writer(driver, fix)  # the fixture writer edits another sentence: the phantom stays
        _drive_to_script(driver, "draft.finish")
        self.assertTrue(machine._has_c09(driver.work_dir, driver.state()))

        action = driver.next()
        state = driver.state()
        self.assertEqual("revision_loop", state["current_phase"])
        self.assertEqual("dispatch", action["kind"])
        self.assertFalse(
            any(agent["subagent_type"].endswith("memo-writer") for agent in action["agents"]),
            "the reviewers, not a second fix",
        )
        # Full mode allows two rounds for blockers; a C-09 alone buys exactly one.
        self.assertEqual(2, state["config"]["lint_fix_rounds"])
        self.assertEqual(1, state["attempts"]["lint_fix"]["1"])
        self.assertFalse(machine._lint_not_converged(state), "C-09 alone never raises the banner")

    def test_a_leftover_c09_ends_in_the_ordinary_export_with_its_appendix_line(self):
        driver = self._v1_with_phantom("c09-export")
        driver.run_to_end()
        state = driver.state()
        self.assertEqual("done", state["current_phase"])
        self.assertEqual("approved_on_v1", state["final_status"])
        self.assertFalse(machine._lint_not_converged(state))
        self.assertEqual(1, state["attempts"]["lint_fix"]["1"])
        self.assertTrue(all(row["citations_clean"] for row in state["draft_versions"]))

        leftover = self._c09(driver)
        self.assertEqual(1, len(leftover), leftover)
        note = md_fallback.label(
            "pinpoint_not_in_raw_note", pinpoint="art 9(2)", source_id=leftover[0]["source_id"]
        )
        views = list(driver.work_dir.glob("memo-*.md"))
        self.assertTrue(views, "the markdown view always ships")
        self.assertIn(note, views[0].read_text(encoding="utf-8"))
        with zipfile.ZipFile(driver.work_dir / "deliverable.docx") as archive:
            self.assertIn(note, archive.read("word/document.xml").decode("utf-8"))

    def test_the_polish_path_is_untouched_by_c09(self):
        driver = self._v1_with_phantom("c09-polish")
        _ask_for_polish(driver, driver.run_until("client_readiness"))
        polish = driver.next()
        self.assertTrue(polish["agents"][0]["subagent_type"].endswith("memo-writer"))
        driver.act(polish)

        order: list[str] = []
        for _ in range(6):
            step = driver.next()
            if step["kind"] == "script":
                order.append(machine.command_key(step["command"]))
            elif step["kind"] == "dispatch":
                order.append("dispatch:" + step["agents"][0]["subagent_type"].split(":")[-1])
                break
            else:
                break
            driver.act(step)
        self.assertEqual(["draft.finish", "dispatch:client-readiness-reviewer"], order)
        self.assertTrue(machine._has_c09(driver.work_dir, driver.state()), "the phantom survived the polish")
        self.assertNotIn("polish", driver.state()["attempts"].get("lint_fix") or {})

    def test_has_c09_reads_the_report_of_the_current_draft_only(self):
        driver = Driver(temp_root(self), slug="c09-predicate")
        sha = "a" * 64

        def mutate(state: dict) -> None:
            state["current_draft_sha"] = sha
            state["current_draft_path"] = "drafts/v1.md"

        state_io.write_state(driver.work_dir, mutate)
        c09 = {
            "rule": "C-09",
            "severity": "major",
            "line": 3,
            "section_id": "s-3",
            "excerpt": "Body [[src:x ст. 9]].",
            "hint": "Pinpoint `ст. 9` …",
            "source_id": "x",
            "pinpoint": "ст. 9",
        }
        for findings, draft_sha, expected in (
            ([c09], sha, True),
            ([dict(c09, rule="C-08")], sha, False),
            ([], sha, False),
            ([c09], "b" * 64, False),
        ):
            with self.subTest(findings=[row["rule"] for row in findings], draft_sha=draft_sha[:1]):
                state_io.write_json_atomic(
                    driver.work_dir / "citations.json",
                    {"draft_sha": draft_sha, "clean": True, "findings": findings},
                )
                self.assertEqual(expected, machine._has_c09(driver.work_dir, driver.state()))


class DraftFinishContractTest(unittest.TestCase):
    """D-117: the merged command keeps what the three commands it replaced guaranteed."""

    def _to_finish(
        self,
        slug: str,
        *,
        mode: str = "full",
        edit: tuple[str, str] | None = None,
        rewrite=None,
    ) -> tuple[Driver, dict]:
        """Write v1 (optionally edited) and stop on the `draft finish` step without running it."""
        driver = Driver(temp_root(self), mode=mode, slug=slug)
        action = driver.run_until("drafting")
        while action["kind"] != "dispatch":  # D-57 renders the writer's md views first
            driver.act(action)
            action = driver.next()
        if rewrite is None:
            _act_writer(driver, action, edit=edit)
        else:
            agent = action["agents"][0]
            step = {"step_id": action["step_id"], "attempt": action["attempt"]}
            probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
            target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
            target.write_bytes(rewrite(target.read_text(encoding="utf-8-sig")).encode("utf-8"))
            _agent_done(driver, action, agent["slot"])
            driver.report(action["step_id"], action["attempt"], agent=agent["slot"])
        finish = driver.next()
        self.assertEqual("draft.finish", machine.command_key(finish["command"]), finish)
        return driver, finish

    @staticmethod
    def _draft(driver: Driver) -> Path:
        return driver.work_dir / str(driver.state()["current_draft_path"])

    def _interrupt(self, driver: Driver, finish: dict) -> None:
        """Run `draft finish` up to the anchor publication and die before `close_step` (§2.2)."""
        with mock.patch.object(stepctx, "close_step", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                machine.run_command(list(finish["command"]))
        state = driver.state()
        row = machine.step_row(state, finish["step_id"], finish["attempt"])
        self.assertIn(row.get("status"), (None, ""), "the step must still be open")
        # The canonical draft is the anchored text; `published[]` still holds the pre-anchor sha.
        self.assertEqual(
            stepctx.OUTPUT_MODIFIED,
            stepctx.verify_published(driver.work_dir, state, str(state["current_draft_path"])),
        )

    def test_a_replay_after_an_interrupted_anchor_recovers_from_the_staged_input(self):
        """D-42 × D-117: the replay must not read its own anchored bytes as a foreign edit."""
        driver, finish = self._to_finish("d117-replay")
        self._interrupt(driver, finish)

        result = machine.run_command(list(finish["command"]))
        self.assertNotIn("errors", result)
        self.assertTrue(result["replay_recovered"])
        self.assertEqual(state_io.sha256_file(self._draft(driver)), result["draft_sha"])

        state = driver.state()
        path = str(state["current_draft_path"])
        self.assertIsNone(stepctx.verify_published(driver.work_dir, state, path))
        self.assertEqual("ok", machine.step_row(state, finish["step_id"], finish["attempt"])["status"])
        self.assertEqual(result["draft_sha"], machine.current_draft_sha(state))
        for name in ("lint.json", "citations.json"):
            self.assertIsNotNone(
                machine.report_for_draft(driver.work_dir, state, name, result["draft_sha"]), name
            )
        after = driver.next()
        self.assertNotIn("errors", after)
        self.assertNotEqual("draft.finish", machine.command_key(after.get("command")))

    def test_a_hand_edit_after_the_interrupted_anchor_is_still_refused(self):
        """D-41: the recovery is sha-exact — anything the anchor did not produce stays a drift."""
        driver, finish = self._to_finish("d117-foreign")
        self._interrupt(driver, finish)
        path = self._draft(driver)
        path.write_bytes(path.read_bytes() + b"\nA later hand edit.\n")

        result = machine.run_command(list(finish["command"]))
        self.assertEqual([stepctx.OUTPUT_MODIFIED], result["errors"])

    def test_a_duplicate_section_anchor_is_refused_before_the_draft_is_touched(self):
        """D-130 × D-117: `draft finish` refuses the collision `draft anchor` refuses."""
        driver, finish = self._to_finish(
            "d130-finish", edit=("## 4. Conclusion", "## 3. Conclusion")
        )
        path = self._draft(driver)
        before = state_io.sha256_file(path)

        result = machine.run_command(list(finish["command"]))
        self.assertTrue(result["errors"], result)
        for message in result["errors"]:
            self.assertTrue(message.startswith("duplicate_section_anchor: s-3 on lines "), message)
        self.assertEqual(before, state_io.sha256_file(path), "the canonical draft is untouched")
        self.assertIsNone(stepctx.verify_published(driver.work_dir, driver.state(), result["draft"]))
        for name in ("lint.json", "citations.json"):
            self.assertFalse((driver.work_dir / name).exists(), name)

    def test_the_citation_majors_reach_the_orchestrator(self):
        """D-131 × D-117: Full, one major and no blocker — the merged command still says «major»."""
        driver, finish = self._to_finish("d131-majors", edit=(" art 5(1)(e)]]", "]]"))
        result = machine.run_command(list(finish["command"]))

        findings = state_io.read_json(driver.work_dir / "citations.json")["findings"]
        majors = sum(1 for row in findings if row["severity"] == "major")
        self.assertGreaterEqual(majors, 1, findings)
        self.assertEqual(0, result["citations"]["blockers"])
        self.assertTrue(result["citations"]["clean"], "a major is not a blocker (§5.4)")
        self.assertEqual(majors, result["citations"]["majors"])
        self.assertGreaterEqual(result["majors"], majors)

        stored = machine.step_row(
            driver.state(), finish["step_id"], finish["attempt"]
        )["result_ref"]["result"]
        self.assertEqual(majors, stored["citations"]["majors"])
        self.assertEqual(result["majors"], stored["majors"])
        self.assertEqual(result["blockers"], stored["blockers"])

    @staticmethod
    def _uncite(text: str) -> str:
        """Drop every `[[src:]]` token and the quoted line: the packed rule source is then unused."""
        kept = [row for row in text.splitlines() if "[[q:" not in row]
        return re.sub(r"\s*\[\[src:[^\]]+\]\]", "", "\n".join(kept))

    def test_an_uncited_rule_source_is_info_in_brief_and_major_in_full(self):
        """D-131: C-07 carries the run's severity, so the merged command audits with the run's mode."""
        brief, finish = self._to_finish("d131-brief", mode="brief", rewrite=self._uncite)
        result = machine.run_command(list(finish["command"]))
        rows = state_io.read_json(brief.work_dir / "citations.json")["findings"]
        c07 = [row for row in rows if row["rule"] == "C-07"]
        self.assertTrue(c07, rows)
        self.assertEqual({"info"}, {row["severity"] for row in c07})
        self.assertEqual(0, result["citations"]["majors"], rows)
        self.assertEqual(0, result["citations"]["blockers"], rows)

        full, finish = self._to_finish("d131-full", mode="full", rewrite=self._uncite)
        result = machine.run_command(list(finish["command"]))
        rows = state_io.read_json(full.work_dir / "citations.json")["findings"]
        c07 = [row for row in rows if row["rule"] == "C-07"]
        self.assertTrue(c07, rows)
        self.assertEqual({"major"}, {row["severity"] for row in c07})
        self.assertEqual(len(c07), result["citations"]["majors"], rows)


class ReviewerRetryBudgetTest(unittest.TestCase):
    """D-55: `reviewer_json_retry{iteration,kind}` — one counter per kind, owned by `review aggregate`."""

    @staticmethod
    def _reviewer_dispatch_slots(state: dict) -> int:
        return sum(
            len(row.get("agents") or [])
            for row in state.get("steps") or []
            if machine._is_reviewer_dispatch(row)
        )

    def _act(self, driver: Driver, action: dict, failing: tuple) -> None:
        step = {"step_id": action["step_id"], "attempt": action["attempt"]}
        for agent in action["agents"]:
            if agent["slot"] in failing:
                driver.report(action["step_id"], action["attempt"], agent=agent["slot"], status="fail")
                continue
            probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
            driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

    def test_group_failure_then_subset_failure_counts_one_key_per_kind(self):
        driver = Driver(temp_root(self), slug="d55")
        first = driver.run_until("revision_loop")
        self.assertEqual(4, len(first["agents"]))
        self._act(driver, first, tuple(agent["slot"] for agent in first["agents"]))

        retry = driver.next()
        self.assertEqual(first["step_id"], retry["step_id"])
        self.assertEqual("failure", retry["reason"])
        self.assertEqual(4, len(retry["agents"]))
        # The machine only reads the counter: `review aggregate` is its only writer.
        self.assertEqual({}, driver.state()["attempts"]["reviewer_json_retry"])

        self._act(driver, retry, ("form", "logic"))
        aggregate = driver.next()
        self.assertEqual("review.aggregate", machine.command_key(aggregate["command"]))
        driver.act(aggregate)

        # D-69: the failure retry above already spent `1:form` and `1:logic`, so the aggregate
        # stubs both kinds here instead of asking for a third dispatch of the same pair.
        result = machine.result_of(machine.step_row(driver.state(), aggregate["step_id"]))
        self.assertEqual("revision_next", result["next"])
        self.assertEqual(["form", "logic"], result["failed_reviewers"])
        self.assertEqual(
            ["reviews/v1-form.json", "reviews/v1-logic.json"], sorted(result["stubs_written"])
        )
        # §2.2: the budget test counts the real dispatches — 4 + 4, never an unbounded retry.
        self.assertEqual(8, self._reviewer_dispatch_slots(driver.state()))
        self.assertEqual({}, driver.state()["attempts"]["reviewer_json_retry"])

    def test_counters_survive_the_reviewer_rerun_of_revision_next(self):
        driver = Driver(temp_root(self), slug="d55-rerun")
        first = driver.run_until("revision_loop")
        self._act(driver, first, ("form",))
        retry = driver.next()
        self.assertEqual(["form"], [agent["slot"] for agent in retry["agents"]])
        self._act(driver, retry, ("form",))
        driver.act(driver.next())  # review aggregate: the JSON budget is gone, `form` is a stub
        revision = driver.next()
        self.assertEqual("revision.next", machine.command_key(revision["command"]))
        driver.act(revision)

        state = driver.state()
        # D-69: `reviewer_rerun` is a budget of its own — the coverage rerun of `revision next` is
        # not a JSON retry and does not read the counter the machine and the aggregate share.
        self.assertEqual({"1": 1}, state["attempts"]["reviewer_rerun"])
        self.assertEqual({}, state["attempts"]["reviewer_json_retry"])


class ScriptErrorPolicyTest(unittest.TestCase):
    """§2.2: a CLI error is one recovery, then `finalize --reason cli_error` (Codex 5)."""

    def test_failed_sources_pack_never_satisfies_the_next_phase(self):
        driver = Driver(temp_root(self), slug="script-error")
        action = driver.run_until("source_pack")
        self.assertEqual("sources.pack", machine.command_key(action["command"]))
        findings = driver.work_dir / "research" / "statutes.json"
        findings.write_bytes(findings.read_bytes() + b"\n")  # drift from `published[]` (D-41)
        self.assertTrue(machine.run_command(list(action["command"])).get("errors"))

        recovery = driver.next()
        self.assertEqual("script", recovery["kind"])
        self.assertEqual("sources.pack", machine.command_key(recovery["command"]))
        self.assertEqual(action["step_id"], recovery["step_id"])
        self.assertEqual("recovery", recovery["reason"])
        self.assertEqual("fail", machine.step_row(driver.state(), action["step_id"], 1)["status"])
        machine.run_command(list(recovery["command"]))

        exit_step = driver.next()
        self.assertEqual("script", exit_step["kind"])
        self.assertEqual("finalize", machine.command_key(exit_step["command"]))
        self.assertIn("cli_error", exit_step["command"])
        self.assertEqual("source_pack", driver.state()["current_phase"])
        machine.run_command(list(exit_step["command"]))
        self.assertEqual("failed", driver.state()["current_phase"])


class NoChangeScopeTest(unittest.TestCase):
    """D-53: the seed comparison belongs to `memo-writer`; acceptance comes before publication."""

    def test_currency_output_identical_to_the_published_file_is_ok(self):
        driver = Driver(temp_root(self), slug="d53-currency")
        action = driver.run_until("currency_check")
        while action["kind"] != "dispatch":
            driver.act(action)
            action = driver.next()
        agent = action["agents"][0]
        probe.run_fixture_agent(
            driver.work_dir,
            driver.state(),
            {"step_id": action["step_id"], "attempt": action["attempt"]},
            agent,
        )
        # The canonical file already holds exactly these bytes — for a non-writer that is `ok`.
        stepctx.publish(
            driver.work_dir,
            action["step_id"],
            action["attempt"],
            agent["expected_outputs"][0]["work_path"],
            "research/currency.json",
        )
        answer = driver.report(action["step_id"], action["attempt"], agent=agent["slot"])
        self.assertTrue(answer["accepted"], answer)
        row = machine.step_row(driver.state(), action["step_id"], action["attempt"])
        self.assertEqual("ok", row["agents"][0]["status"])

    def test_autoclose_publishes_nothing_before_the_acceptance_checks(self):
        driver = Driver(temp_root(self), slug="d53-autoclose")
        reviewers = driver.run_until("revision_loop")
        CompletionTest._act_reviewers_with_blocker(driver, reviewers)
        writer = None
        for _ in range(8):
            action = driver.next()
            if action["kind"] == "dispatch" and action["agents"][0]["subagent_type"].endswith(
                "memo-writer"
            ):
                writer = action
                break
            driver.act(action)
        self.assertIsNotNone(writer, "the revision loop never dispatched the writer")

        # The writer returns the pre-seeded v2 untouched and only writes its marker.
        _agent_done(driver, writer, writer["agents"][0]["slot"])
        before = _published_shas(driver.state())
        follow = driver.next()
        state = driver.state()
        self.assertEqual("command", stepctx.published_entry(state, "drafts/v2.md")["by"])
        self.assertEqual(before, _published_shas(state))
        row = machine.step_row(state, writer["step_id"], writer["attempt"])
        self.assertEqual("fail", row["agents"][0]["status"])
        self.assertEqual("failure", follow["reason"])


class ReadinessVerdictTest(unittest.TestCase):
    """D-41 / §2.1 row 14: a readiness verdict is bound to the draft sha it reviewed (Codex 7)."""

    def test_verdict_for_another_draft_is_rejected(self):
        driver = Driver(temp_root(self), slug="d41-readiness")
        action = driver.run_until("client_readiness")
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        document = probe.fixture_client_readiness("b" * 64, 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, document)
        _agent_done(driver, action, agent["slot"])
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

        again = driver.next()
        self.assertEqual("dispatch", again["kind"])
        self.assertEqual(
            "client-readiness-reviewer", again["agents"][0]["subagent_type"].split(":")[-1]
        )
        self.assertNotEqual(action["step_id"], again["step_id"])
        state = driver.state()
        self.assertEqual("client_readiness", state["current_phase"])
        self.assertFalse(state.get("client_readiness"), "a stale verdict was recorded")


class FollowupSubsetTest(unittest.TestCase):
    """Codex 12: the missing subset survives the gate, and sufficiency sees the dispatched layers."""

    SUFFICIENCY = {
        "reviewer": "research_sufficiency",
        "overall_verdict": "targeted_followup_needed",
        "blocking_gaps": [
            {
                "gap": "The retention period the client applies is unknown.",
                "target": "user",
                "status": "missing",
                "why_blocking": "The conclusion turns on it.",
                "followup_question": {
                    "question": "Which retention period does the client apply today?",
                    "header": "Period",
                    "options": [
                        {"label": "Five years", "description": "Statutory minimum."},
                        {"label": "Seven years", "description": "Tax rule."},
                    ],
                    "default_assumption_if_skipped": "Seven years.",
                },
            },
            {
                "gap": "No commentary on the retention carve-out was found.",
                "target": "doctrine",
                "status": "missing",
                "why_blocking": "The counterargument needs a secondary source.",
            },
        ],
        "drafting_warnings": [],
    }

    def test_the_gate_answer_re_dispatches_exactly_the_missing_layers(self):
        driver = Driver(temp_root(self), slug="followup-subset")
        action = driver.run_until("research_sufficiency")
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, self.SUFFICIENCY)
        _agent_done(driver, action, agent["slot"])
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

        gate = driver.next()
        while gate["kind"] == "script":
            driver.act(gate)
            gate = driver.next()
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        driver.act(gate)

        research = driver.next()
        self.assertEqual("dispatch", research["kind"])
        self.assertEqual("research", research["phase"])
        self.assertEqual(["doctrine"], [item["slot"] for item in research["agents"]])
        followup = driver.state()["sufficiency_followup"]
        self.assertEqual("research_subset", followup["status"])
        self.assertEqual(["doctrine"], machine.missing_layers(driver.state()))

    def test_sufficiency_sees_the_layers_that_were_dispatched(self):
        driver = Driver(temp_root(self), slug="dispatched-layers")
        action = driver.run_until("research")
        driver.act(action, fail_slots=("doctrine",))
        retry = driver.next()
        driver.act(retry, fail_slots=("doctrine",))
        sufficiency = driver.next()
        self.assertEqual("research_sufficiency", sufficiency["phase"])
        self.assertEqual(["case_law", "statutes"], driver.state()["dispatched_researchers"])
        prompt = sufficiency["agents"][0]["prompt"]
        self.assertIn("`research/statutes.json`", prompt)
        self.assertNotIn("research/doctrine.json", prompt)
        self.assertEqual(
            ["case_law", "statutes"], machine.research_layers(driver.work_dir, driver.state())
        )


class ModeScopedFollowupTest(unittest.TestCase):
    """D-112: a Brief run whose reviewer reports a `case_law` gap does not become a Full run."""

    SUFFICIENCY = {
        "reviewer": "research_sufficiency",
        "overall_verdict": "targeted_followup_needed",
        "blocking_gaps": [
            {
                "gap": "The retention period the client applies is unknown.",
                "target": "user",
                "status": "missing",
                "why_blocking": "The conclusion turns on it.",
                "followup_question": {
                    "question": "Which retention period does the client apply today?",
                    "header": "Period",
                    "options": [
                        {"label": "Five years", "description": "Statutory minimum."},
                        {"label": "Seven years", "description": "Tax rule."},
                    ],
                    "default_assumption_if_skipped": "Seven years.",
                },
            },
            {
                "gap": "No CJEU authority on the retention carve-out was located.",
                "target": "case_law",
                "status": "missing",
                "why_blocking": "The conclusion rests on the statute alone.",
            },
            {
                "gap": "No commentary on the retention carve-out was found.",
                "target": "doctrine",
                "status": "missing",
                "why_blocking": "The counterargument needs a secondary source.",
            },
        ],
        "drafting_warnings": [],
    }

    def _judge(self, driver: Driver) -> None:
        """Run the sufficiency reviewer of a Brief run with the verdict above."""
        action = driver.run_until("research_sufficiency")
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, self.SUFFICIENCY)
        _agent_done(driver, action, agent["slot"])
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

    def test_the_gate_answer_dispatches_no_layer_outside_the_mode(self):
        driver = Driver(temp_root(self), mode="brief", slug="brief-scope")
        self._judge(driver)

        gate = driver.next()
        while gate["kind"] == "script":
            driver.act(gate)
            gate = driver.next()
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        state = driver.state()
        self.assertEqual([], state["sufficiency_followup"]["subset_r"])
        warnings = [row["message"] for row in state["drafting_warnings"]]
        self.assertEqual(2, len(warnings), warnings)
        for message in warnings:
            self.assertTrue(message.startswith("Out of scope for brief mode: "), message)

        driver.act(gate)
        after = driver.next()
        # §2.1 row 7 with an empty in-scope subset: the run continues as a `continue` would.
        self.assertEqual("research_sufficiency", after["phase"])
        self.assertEqual("dispatch", after["kind"])
        self.assertEqual(
            ["research-sufficiency-reviewer"],
            [item["subagent_type"].split(":")[-1] for item in after["agents"]],
        )
        self.assertEqual(["statutes"], driver.state()["dispatched_researchers"])
        self.assertEqual([], machine.missing_layers(driver.state()))

    def test_the_same_verdict_in_full_mode_re_dispatches_both_layers(self):
        driver = Driver(temp_root(self), mode="full", slug="full-scope")
        self._judge(driver)

        gate = driver.next()
        while gate["kind"] == "script":
            driver.act(gate)
            gate = driver.next()
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        self.assertEqual([], driver.state()["drafting_warnings"])
        driver.act(gate)

        research = driver.next()
        self.assertEqual("research", research["phase"])
        self.assertEqual(["case_law", "doctrine"], [item["slot"] for item in research["agents"]])

    def test_target_layers_intersects_the_subset_with_the_configured_layers(self):
        followup = {
            "status": machine.RESEARCH_SUBSET_STATUS,
            "subset_r": [
                {"target": "case_law", "status": "missing"},
                {"target": "statutes", "status": "missing"},
            ],
        }
        work_dir = temp_root(self)
        brief = {"config": {"researcher_layers": ["statutes"]}, "sufficiency_followup": followup}
        self.assertEqual(["statutes"], machine.missing_layers(brief))
        self.assertEqual(["statutes"], machine.target_layers(work_dir, brief))
        full = {
            "config": {"researcher_layers": ["statutes", "case_law", "doctrine"]},
            "sufficiency_followup": followup,
        }
        self.assertEqual(["case_law", "statutes"], machine.missing_layers(full))
        self.assertEqual(["case_law", "statutes"], machine.target_layers(work_dir, full))


class SufficiencyBudgetCombinationsTest(unittest.TestCase):
    """D-116 / §2.1 rows 6–7: two budgets, four combinations — each buys exactly what it paid for."""

    SPENT_USER = limits.MAX_SUFFICIENCY_USER_FOLLOWUP
    SPENT_RESEARCH = limits.research_followup_limit("full")

    def _route(self, slug: str, *, user: int, research: int) -> tuple[Driver, dict]:
        """Judge the research with the two counters already at `user`/`research`; return what follows."""
        driver = Driver(temp_root(self), mode="full", slug=slug)
        action = driver.run_until("research_sufficiency")
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, ModeScopedFollowupTest.SUFFICIENCY)
        _agent_done(driver, action, agent["slot"])
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

        def spend(current: dict) -> None:
            counters = current.setdefault("attempts", {})
            counters["sufficiency_user_followup"] = user
            counters["sufficiency_research_followup"] = research

        state_io.write_state(driver.work_dir, spend)
        _drive_to_script(driver, "sufficiency.route")
        return driver, driver.next()

    @staticmethod
    def _counters(driver: Driver) -> tuple[int, int]:
        attempts = driver.state()["attempts"]
        return (
            int(attempts.get("sufficiency_user_followup", 0)),
            int(attempts.get("sufficiency_research_followup", 0)),
        )

    @staticmethod
    def _approved(driver: Driver) -> list[str]:
        return driver.state()["sufficiency_followup"]["approved_layers"]

    def test_both_budgets_left_asks_and_re_dispatches_the_named_layers(self):
        driver, gate = self._route("budget-both", user=0, research=0)
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        self.assertEqual((1, 1), self._counters(driver), "one question and one research pass")
        self.assertEqual(["case_law", "doctrine"], self._approved(driver))

        driver.act(gate)
        research = driver.next()
        self.assertEqual("research", research["phase"])
        self.assertEqual(["case_law", "doctrine"], [item["slot"] for item in research["agents"]])

    def test_the_user_budget_alone_asks_without_buying_a_research_pass(self):
        """N-01: `layers: []` was charged to nobody, so the answered gate may not dispatch one."""
        driver, gate = self._route("budget-user", user=0, research=self.SPENT_RESEARCH)
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        self.assertEqual((1, self.SPENT_RESEARCH), self._counters(driver))
        self.assertEqual([], self._approved(driver), "the research budget is gone")
        self.assertEqual([], machine.missing_layers(driver.state()))
        self.assertTrue(
            [gap for gap in driver.state()["sufficiency_followup"]["subset_r"] if gap["status"] == "missing"],
            "the gaps are still recorded — only re-deriving the layers from them is forbidden",
        )

        driver.act(gate)
        after = driver.next()
        self.assertEqual("research_sufficiency", after["phase"], "no unpaid research pass")
        self.assertEqual(
            ["research-sufficiency-reviewer"],
            [item["subagent_type"].split(":")[-1] for item in after["agents"]],
        )
        self.assertEqual((1, self.SPENT_RESEARCH), self._counters(driver))

    def test_the_research_budget_alone_re_dispatches_without_asking(self):
        driver, research = self._route("budget-research", user=self.SPENT_USER, research=0)
        self.assertEqual("research", research["phase"])
        self.assertEqual(["case_law", "doctrine"], [item["slot"] for item in research["agents"]])
        self.assertEqual((self.SPENT_USER, 1), self._counters(driver))
        self.assertEqual(["case_law", "doctrine"], self._approved(driver))

    def test_neither_budget_left_carries_the_gaps_as_warnings(self):
        driver, action = self._route(
            "budget-none", user=self.SPENT_USER, research=self.SPENT_RESEARCH
        )
        self.assertEqual("currency_check", action["phase"])
        self.assertEqual((self.SPENT_USER, self.SPENT_RESEARCH), self._counters(driver))
        self.assertEqual([], self._approved(driver))
        self.assertTrue(driver.state()["drafting_warnings"])

    def test_the_approved_set_beats_the_gaps_the_router_did_not_pay_for(self):
        """D-116: `subset_r` is the reviewer's list of gaps, not a licence to re-dispatch them."""
        followup = {
            "status": machine.RESEARCH_SUBSET_STATUS,
            "subset_r": [{"target": "case_law", "status": "missing"}],
            "approved_layers": [],
        }
        state = {
            "config": {"researcher_layers": ["statutes", "case_law"]},
            "sufficiency_followup": followup,
        }
        self.assertEqual([], machine.missing_layers(state))
        # A state written before the key existed still derives the set from the gaps.
        legacy = {
            "config": state["config"],
            "sufficiency_followup": {"status": "answered", "subset_r": followup["subset_r"]},
        }
        self.assertEqual(["case_law"], machine.missing_layers(legacy))


class RenderViewsTest(unittest.TestCase):
    """D-57: the md views are script steps in front of the agents that read them (Codex 13)."""

    def test_writer_v1_is_dispatched_only_after_its_views_exist(self):
        driver = Driver(temp_root(self), slug="d57-writer")
        action = driver.run_until("drafting")
        rendered: list[str] = []
        while action["kind"] != "dispatch":
            rendered.append(machine.command_key(action["command"]))
            driver.act(action)
            action = driver.next()
        self.assertIn("render.research", rendered)
        for layer in machine.research_layers(driver.work_dir, driver.state()):
            self.assertTrue((driver.work_dir / f"research/{layer}.md").is_file(), layer)
        named = self._paths_in(action["agents"][0]["prompt"], driver.work_dir)
        self.assertTrue(named, "the writer prompt names no work-dir input at all")
        for path in named:
            self.assertTrue(path.is_file(), f"the writer prompt names a missing file: {path}")

    def test_a_view_without_a_consumer_is_never_issued(self):
        # D-62: `sufficiency route` and the writer read the JSON, and the gate-11 text is built by
        # `sources.render_digest`, so these three views buy nothing and cost a script step each.
        driver = Driver(temp_root(self), slug="d62-gates")
        driver.run_until("currency_check")
        driver.run_until("source_review_pending")
        issued = [
            machine.command_key(row["command"])
            for row in driver.state()["steps"]
            if row.get("kind") == "script"
        ]
        for view in ("render.sufficiency", "render.source-pack", "render.currency"):
            self.assertNotIn(view, issued)
        for name in (
            "research/research-sufficiency.md",
            "research/source-pack.md",
            "research/currency.md",
        ):
            self.assertFalse((driver.work_dir / name).exists(), name)

    def test_the_mediator_view_precedes_the_revision_writer(self):
        driver = Driver(temp_root(self), slug="d57-mediator")
        reviewers = driver.run_until("revision_loop")
        CompletionTest._act_reviewers_with_blocker(driver, reviewers)
        for _ in range(8):
            action = driver.next()
            if action["kind"] == "dispatch" and action["agents"][0]["subagent_type"].endswith(
                "memo-writer"
            ):
                self.assertTrue((driver.work_dir / "reviews/v1-mediator.md").is_file())
                # D-62: the writer is pointed at the rendered view, never at the state JSON.
                prompt = action["agents"][0]["prompt"]
                self.assertIn("`reviews/v1-mediator.md`", prompt)
                self.assertNotIn("`reviews/v1-mediator.json`", prompt)
                return
            driver.act(action)
        self.fail("the revision loop never dispatched the writer")

    @staticmethod
    def _paths_in(prompt: str, work_dir: Path) -> list[Path]:
        """Work-dir files the prompt names in backticks; a glob must match at least one file."""
        found: list[Path] = []
        for token in re.findall(r"`([^`]+)`", prompt):
            name = token.strip()
            if not name.startswith("research/") or not name.endswith((".md", ".json")):
                continue
            if "*" not in name:
                found.append(work_dir / name)
                continue
            matches = sorted(work_dir.glob(name))
            found.extend(matches or [work_dir / name])
        return found


class AutocloseBookkeepingTest(unittest.TestCase):
    """D-59: the autoclose branch of `next` keeps the same draft accounting as `report`."""

    @staticmethod
    def _writer_without_a_report(driver: Driver) -> dict:
        """Run the drafting writer to completion and leave `mf report` uncalled (§2.1 rule 3)."""
        action = driver.run_until("drafting")
        for _ in range(20):
            agents = action.get("agents") or [{}]
            if action["kind"] == "dispatch" and str(
                agents[0].get("subagent_type") or ""
            ).endswith("memo-writer"):
                break
            driver.act(action)
            action = driver.next()
        else:
            raise AssertionError("the drafting phase never dispatched the writer")
        step = {"step_id": action["step_id"], "attempt": action["attempt"]}
        probe.run_fixture_agent(driver.work_dir, driver.state(), step, action["agents"][0])
        return action

    def test_autoclose_records_the_draft_it_published(self):
        driver = Driver(temp_root(self), slug="d59")
        action = self._writer_without_a_report(driver)
        before = driver.state()
        self.assertIsNone(before.get("current_draft_path"))
        driver.next()  # the autoclose branch closes the writer step

        state = driver.state()
        row = machine.step_row(state, action["step_id"], action["attempt"])
        self.assertEqual("ok", row["status"])
        self.assertEqual("drafts/v1.md", state["current_draft_path"])
        self.assertEqual(
            stepctx.published_sha(state, "drafts/v1.md"), state["current_draft_sha"]
        )
        self.assertEqual([1], [version["version"] for version in state["draft_versions"]])
        self.assertEqual(1, state["current_iteration"])

    def test_a_run_recovered_by_autoclose_still_ends_approved(self):
        driver = Driver(temp_root(self), slug="d59-run")
        self._writer_without_a_report(driver)
        driver.run_to_end()
        state = driver.state()
        self.assertEqual("approved_on_v1", state["final_status"])
        self.assertEqual([], state.get("final_status_reasons") or [])
        self.assertEqual([1], [version["version"] for version in state["draft_versions"]])


class PublishedReportsTest(unittest.TestCase):
    """D-60/D-68: `lint.json`/`citations.json` reach the bookkeeping only through `published[]` (D-41)."""

    def _tamper(self, driver: Driver, name: str) -> None:
        report = state_io.read_json(driver.work_dir / name)
        report["tampered"] = True  # any edit after publication changes the bytes
        state_io.write_json_atomic(driver.work_dir / name, report)

    def test_a_tampered_lint_report_revokes_the_mark_it_gave(self):
        # D-68: the version is already marked clean — the drift has to take the mark away, so the
        # state is left exactly as the run built it (no pre-reset of the flags).
        driver = Driver(temp_root(self), slug="d68")
        driver.run_until("revision_loop")
        version = driver.state()["draft_versions"][0]
        self.assertTrue(version["lint_clean"])
        self.assertTrue(version["citations_clean"])

        self._tamper(driver, "lint.json")
        machine.sync_draft_versions(driver.work_dir)

        state = driver.state()
        row = state["draft_versions"][0]
        self.assertFalse(row["lint_clean"], "a report edited after publication is no verdict")
        self.assertTrue(row["citations_clean"], "the untouched report is still applied")
        self.assertEqual(
            [],
            [
                item
                for item in state["draft_versions"]
                if item["lint_clean"] and item["citations_clean"]
            ],
            "§2.1 row 15: the export must not pick the version as checked",
        )

    def test_both_reports_gone_leave_no_checked_version(self):
        driver = Driver(temp_root(self), slug="d68-gone")
        driver.run_until("revision_loop")
        for name in ("lint.json", "citations.json"):
            (driver.work_dir / name).unlink()

        machine.sync_draft_versions(driver.work_dir)

        row = driver.state()["draft_versions"][0]
        self.assertFalse(row["lint_clean"])
        self.assertFalse(row["citations_clean"])
        self.assertIsNone(row["checked_at"])

    def test_a_report_of_another_draft_leaves_the_version_it_checked(self):
        # D-68: a readable report is a verdict for its own `draft_sha` only; the marks of the other
        # versions come from their own reports and are not touched.
        driver = Driver(temp_root(self), slug="d68-other")
        driver.run_until("revision_loop")
        checked = dict(driver.state()["draft_versions"][0])

        def add_version(current: dict) -> None:
            current["draft_versions"].append(
                {
                    "version": 2,
                    "path": "drafts/v2.md",
                    "sha256": "f" * 64,
                    "lint_clean": False,
                    "citations_clean": False,
                    "checked_at": None,
                }
            )

        state_io.write_state(driver.work_dir, add_version)
        machine.sync_draft_versions(driver.work_dir)

        rows = {row["version"]: row for row in driver.state()["draft_versions"]}
        self.assertTrue(rows[1]["lint_clean"])
        self.assertTrue(rows[1]["citations_clean"])
        self.assertEqual(checked["checked_at"], rows[1]["checked_at"])
        self.assertFalse(rows[2]["lint_clean"])
        self.assertFalse(rows[2]["citations_clean"])


class ReviewerBudgetKeyTest(unittest.TestCase):
    """D-65: `reviewer_json_retry` is read per `<iteration>:<kind>` and outlives one `step_id`."""

    def test_a_kind_with_budget_left_is_re_issued_when_another_kind_has_none(self):
        driver = Driver(temp_root(self), slug="d65")
        first = driver.run_until("revision_loop")
        kinds = sorted(agent["slot"] for agent in first["agents"])
        self.assertIn("logic", kinds)

        def spend_logic(current: dict) -> None:
            current.setdefault("attempts", {})["reviewer_json_retry"] = {"1:logic": 1}

        state_io.write_state(driver.work_dir, spend_logic)
        for agent in first["agents"]:
            driver.report(
                first["step_id"], first["attempt"], agent=agent["slot"], status="fail"
            )

        retry = driver.next()
        self.assertEqual("dispatch", retry["kind"])
        self.assertEqual("failure", retry["reason"])
        self.assertEqual(
            [kind for kind in kinds if kind != "logic"],
            sorted(agent["slot"] for agent in retry["agents"]),
        )

    def test_a_rerun_under_a_new_step_id_keeps_the_failure_count(self):
        # D-32: `review aggregate` reruns a kind under a new `step_id`; the machine's own failure
        # retries are counted by `(iteration, kind)`, so the rerun does not reset them.
        state = {
            "current_iteration": 1,
            "attempts": {},
            "steps": [
                {
                    "step_id": "s-010",
                    "kind": "dispatch",
                    "phase": "revision_loop",
                    "attempt": 2,
                    "reason": "failure",
                    "status": "fail",
                    "expected_outputs": [
                        {"canonical_path": "reviews/v1-form.json", "work_path": "w", "schema": "review"}
                    ],
                }
            ],
        }
        row = {
            "step_id": "s-020",
            "kind": "dispatch",
            "phase": "revision_loop",
            "attempt": 1,
            "reason": "initial",
            "agents": [
                {"slot": "form", "agent_type": "form-reviewer"},
                {"slot": "logic", "agent_type": "logic-reviewer"},
            ],
        }
        budget = machine.budget_for(state, row, ["form", "logic"])
        self.assertEqual(1, machine.reviewer_failure_attempts(state, 1, "form"))
        self.assertEqual(0, machine.reviewer_failure_attempts(state, 1, "logic"))
        self.assertEqual(1, machine.budget_used(state, budget, "form"))
        self.assertEqual(0, machine.budget_used(state, budget, "logic"))
        # No `max()` across the kinds: `form` is out of budget, `logic` still has its own.
        self.assertEqual(["logic"], machine.budget_slots_left(state, budget, ["form", "logic"]))


class ReviewerJsonRetryTotalTest(unittest.TestCase):
    """D-69: `MAX_REVIEWER_JSON_RETRY` is the total of both owners, counted in real dispatches."""

    def test_a_failing_reviewer_is_dispatched_twice_before_the_stub(self):
        driver = Driver(temp_root(self), slug="d69")
        action = driver.run_until("revision_loop")
        dispatches = []
        while action.get("kind") == "dispatch":
            dispatches.append(action)
            for agent in action["agents"]:
                driver.report(
                    action["step_id"], action["attempt"], agent=agent["slot"], status="fail"
                )
            action = driver.next()

        self.assertEqual(
            1 + limits.MAX_REVIEWER_JSON_RETRY,
            len(dispatches),
            "the initial dispatch plus one JSON retry, never one per owner",
        )
        self.assertEqual(["initial", "failure"], [row["reason"] for row in dispatches])

        # The aggregate that follows must stub the kinds instead of asking for a third dispatch.
        self.assertEqual("script", action["kind"])
        self.assertEqual("review.aggregate", machine.command_key(action["command"]))
        driver.act(action)
        result = machine.result_of(machine.step_row(driver.state(), action["step_id"], action["attempt"]))
        self.assertEqual("revision_next", result["next"])
        kinds = sorted(agent["slot"] for agent in dispatches[0]["agents"])
        self.assertEqual(kinds, result["failed_reviewers"])
        self.assertEqual(
            [review.review_path(1, kind) for kind in kinds], sorted(result["stubs_written"])
        )
        attempts = driver.state()["attempts"]
        self.assertEqual({}, attempts.get("reviewer_json_retry") or {})
        self.assertEqual({}, attempts.get("reviewer_rerun") or {}, "a separate budget of §2.2")


class ForcedPlanApprovalModeTest(unittest.TestCase):
    """D-70: the forced approval of an edited plan never resets the mode to `full` silently."""

    def test_the_last_recognised_mode_of_the_history_wins(self):
        state = {
            "config": {},
            "plan_approval": {
                "iterations": [
                    {"action": "approve", "answers": {"Plan": "Edit", "Mode": "Brief"}},
                    {"action": "edit", "answers": {"Plan": "Edit: add the UK angle"}},
                ]
            },
        }
        applied = machine.apply_plan_answers(state, {"Plan": "Edit: add the UK angle"})
        self.assertEqual("brief", applied["mode"])

    def test_a_forced_approval_without_any_mode_answer_keeps_the_state_mode(self):
        driver = Driver(temp_root(self), slug="d70")
        gate = driver.run_until("plan_approval_pending")
        driver.report(
            gate["step_id"],
            gate["attempt"],
            answers=json.dumps({"Plan": "Edit: add the UK angle"}),
            generation=gate.get("generation", 0),
        )
        answers = driver.state()["plan_approval"]["iterations"][-1]["answers"]
        self.assertNotIn("Mode", answers, "the reply carries no Mode of its own")

        def exhaust(current: dict) -> None:
            current["mode"] = "brief"
            current.setdefault("attempts", {})["plan_edit"] = limits.MAX_PLAN_EDIT + 1

        state_io.write_state(driver.work_dir, exhaust)
        driver.run_until("research")  # the edit is planned, then the plan is approved by force

        state = driver.state()
        self.assertEqual("brief", state["mode"])
        self.assertEqual(modes.MODES["brief"]["reviewer_list"], state["config"]["reviewer_list"])
        self.assertIn(
            "plan_forced_approve", [banner["banner_id"] for banner in state["fallback_banners"]]
        )


class PolishFixRoundTest(unittest.TestCase):
    """§2.1 row 14 / D-57: polish -> findings -> one writer fix -> checks -> readiness again."""

    def _readiness_verdict(self, driver: Driver, action: dict, verdict: str) -> None:
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        document = probe.fixture_client_readiness(str(driver.state()["current_draft_sha"]), 1)
        document["verdict"] = verdict
        document["issues"] = [
            {
                "section_id": "s-3",
                "severity": "minor",
                "issue": "The conclusion could name the owner earlier.",
                "suggestion": "Move the owner to the first sentence.",
            }
        ]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, document)
        _agent_done(driver, action, agent["slot"])
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

    def test_findings_after_the_polish_earn_one_fix_before_any_degradation(self):
        driver = Driver(temp_root(self), slug="polish-fix")
        readiness = driver.run_until("client_readiness")
        self._readiness_verdict(driver, readiness, "needs_final_polish")

        polish = driver.next()
        self.assertEqual("dispatch", polish["kind"])
        self.assertEqual(1, driver.state()["attempts"]["client_polish"])
        _act_writer(driver, polish, edit=BREAK_RISK)  # the polish introduces an L-07 blocker

        step = driver.next()
        self.assertEqual("draft.finish", machine.command_key(step["command"]), step)
        driver.act(step)
        fix = driver.next()
        self.assertEqual("dispatch", fix["kind"])
        self.assertEqual("memo-writer", fix["agents"][0]["subagent_type"].split(":")[-1])
        self.assertEqual(1, driver.state()["attempts"]["lint_fix"]["polish"])
        self.assertEqual("client_readiness", driver.state()["current_phase"])
        _act_writer(driver, fix, edit=(BREAK_RISK[1], BREAK_RISK[0]))

        step = driver.next()
        self.assertEqual("draft.finish", machine.command_key(step["command"]), step)
        self.assertEqual("rerun", step["reason"])
        driver.act(step)
        again = driver.next()
        self.assertEqual("dispatch", again["kind"])
        self.assertEqual(
            "client-readiness-reviewer", again["agents"][0]["subagent_type"].split(":")[-1]
        )
        self.assertNotIn("manual_review_required", str(driver.state().get("final_status")))

        # Only now may the run degrade: `client_polish` and `lint_fix{polish}` are both spent.
        self._readiness_verdict(driver, again, "needs_final_polish")
        driver.next()
        state = driver.state()
        self.assertEqual("export", state["current_phase"])
        self.assertEqual("manual_review_required_on_v1", state["final_status"])
        self.assertEqual(1, state["attempts"]["client_polish"])
        self.assertEqual(1, state["attempts"]["lint_fix"]["polish"])


class SkippedScriptStepTest(unittest.TestCase):
    """§2.2/D-52: `skipped` closes a script step for good — only a CLI error (`fail`) does not."""

    @staticmethod
    def _docx(driver: Driver) -> Path:
        state = driver.state()
        return driver.work_dir / f"memo-{docx_export.slug_of(state, driver.work_dir)}.docx"

    def test_the_export_walks_on_without_a_second_validation_step(self):
        # D-117: `docx render` validated what it wrote, so the export goes straight to `finalize`
        # and a docx removed afterwards no longer re-opens a validation step.
        driver = Driver(temp_root(self), mode="brief", slug="skip-validate")
        render = _drive_to_script(driver, "docx.render")
        row = machine.step_row(driver.state(), render["step_id"], 1)
        self.assertEqual("ok", row["status"])
        self.assertTrue(row["result_ref"]["result"]["valid"])
        self._docx(driver).unlink()

        after = driver.next()
        self.assertEqual("finalize", machine.command_key(after["command"]), after)
        driver.act(after)
        self.assertTrue(phases.is_terminal(driver.state()["current_phase"]))

    def test_a_skipped_script_step_satisfies_its_phase(self):
        """§2.2: every closed status but `fail` counts; `skipped` is a legitimate outcome."""
        self.assertIn("skipped", machine.SCRIPT_DONE_STATUSES)
        self.assertNotIn("fail", machine.SCRIPT_DONE_STATUSES)


class RerunInputsTest(unittest.TestCase):
    """§2.2: `reason: rerun` is the same action on **changed** inputs — `inputs_sha` decides (D-58)."""

    def test_a_closed_step_with_unchanged_inputs_is_not_re_issued(self):
        driver = Driver(temp_root(self), mode="brief", slug="rerun-inputs")
        render = _drive_to_script(driver, "docx.render")
        state = driver.state()
        sha = machine.export_draft_sha(state)[0]
        inputs = machine.export_inputs(driver.work_dir, state, sha)
        row = machine.step_row(state, render["step_id"], 1)
        self.assertEqual("ok", row["status"])
        self.assertEqual(machine.inputs_sha(driver.work_dir, inputs), row["inputs_sha"])

        again = machine.script_step(
            driver.work_dir,
            state,
            "docx",
            "render",
            chat="rendering again",
            extra=("--draft-sha", str(sha or "")),
            inputs=inputs,
        )
        self.assertIsNone(again, "unchanged inputs are no reason to re-issue a closed step")
        self.assertEqual(1, len(stepctx.steps_for(driver.state(), render["step_id"])))

    def test_a_changed_input_sha_earns_the_rerun(self):
        driver = Driver(temp_root(self), mode="brief", slug="rerun-changed")
        render = _drive_to_script(driver, "docx.render")
        state = driver.state()
        sha = machine.export_draft_sha(state)[0]
        inputs = machine.export_inputs(driver.work_dir, state, sha)
        target = driver.work_dir / inputs[0]
        target.write_bytes(target.read_bytes() + b"\n")

        rerun = machine.script_step(
            driver.work_dir,
            driver.state(),
            "docx",
            "render",
            chat="rendering again",
            extra=("--draft-sha", str(sha or "")),
            inputs=inputs,
        )
        self.assertIsNotNone(rerun)
        self.assertEqual(render["step_id"], rerun["step_id"])
        self.assertEqual(2, rerun["attempt"])
        self.assertEqual("rerun", rerun["reason"])

    def test_a_step_issued_too_often_leaves_through_finalize(self):
        # D-58: whatever keeps a step repeating, the run exits instead of spinning.
        driver = Driver(temp_root(self), mode="brief", slug="step-loop")
        driver.run_until("research")
        work_dir = driver.work_dir
        step_id = machine.next_step_id(driver.state())
        spec = {"kind": machine.KIND_SCRIPT, "command": ["mf", "draft", "lint"], "chat_line": "loop"}
        for attempt in range(1, limits.MAX_STEP_ATTEMPTS + 1):
            issued = machine.issue(
                work_dir, driver.state(), dict(spec), step_id=step_id, attempt=attempt
            )
            self.assertEqual(step_id, issued["step_id"], attempt)

        exit_step = machine.issue(
            work_dir, driver.state(), dict(spec), step_id=step_id, attempt=limits.MAX_STEP_ATTEMPTS + 1
        )
        self.assertEqual("script", exit_step["kind"])
        self.assertEqual("finalize", machine.command_key(exit_step["command"]))
        self.assertIn(machine.STEP_LOOP, exit_step["command"])
        self.assertEqual(
            limits.MAX_STEP_ATTEMPTS, len(stepctx.steps_for(driver.state(), step_id))
        )

        machine.run_command(list(exit_step["command"]))
        state = driver.state()
        self.assertTrue(phases.is_terminal(state["current_phase"]))
        self.assertIn(machine.STEP_LOOP, state["final_status_reasons"])


class StepClosingTest(unittest.TestCase):
    """D-40: `stepctx.close_step` is the only implementation of the step protocol."""

    def test_machine_has_no_close_of_its_own(self):
        self.assertFalse(hasattr(machine, "close"))

    def test_closing_a_superseded_attempt_is_refused_under_the_lock(self):
        driver = Driver(temp_root(self), slug="d40")
        action = driver.run_until("research")
        stale = machine.step_row(driver.state(), action["step_id"], action["attempt"])
        driver.next()  # attempt 2 is the issued identity now
        with self.assertRaises(stepctx.IdentityMismatch):
            machine.close_step(driver.work_dir, stale, {"slots": {}}, status="ok")


class AgentLogTest(unittest.TestCase):
    """§3.1: `mf agent log --state done` writes the completion marker the checks read."""

    def test_done_marker_matches_the_declared_inputs(self):
        driver = Driver(temp_root(self), slug="marker")
        action = driver.run_until("research")
        agent = action["agents"][0]
        step = {"step_id": action["step_id"], "attempt": action["attempt"]}
        probe.run_fixture_agent(driver.work_dir, driver.state(), step, agent)
        marker = state_io.read_json(
            machine.marker_path(driver.work_dir, action["step_id"], action["attempt"], agent["slot"])
        )
        declared = machine.step_row(driver.state(), action["step_id"], action["attempt"])["inputs"]
        self.assertEqual(declared, marker["input_sha"])
        self.assertIn(agent["expected_outputs"][0]["work_path"], marker["output_sha"])

    def test_missing_marker_is_a_failed_completion(self):
        driver = Driver(temp_root(self), slug="nomarker")
        action = driver.run_until("research")
        answer = driver.report(action["step_id"], action["attempt"], agent="statutes")
        self.assertFalse(answer["accepted"])
        self.assertIn("missing_done_marker", answer["errors"])
        self.assertEqual(action["step_id"], answer["retry"]["step_id"])


class McpProbePromptTest(unittest.TestCase):
    """D-105: the inline probe names every bundled server and every key of `mcp-probe`."""

    def instruction(self) -> str:
        driver = Driver(temp_root(self), slug="probeprompt")
        action = driver.run_until("intake_preliminary_research")
        self.assertEqual("inline-llm", action["kind"])
        return action["instruction"]

    def test_the_probe_names_all_four_bundled_servers(self):
        text = self.instruction()
        for name in ("Legal Data Hunter", "CourtListener", "LegalViz", "eurlex", "UK Legal", "uk-legal-mcp"):
            self.assertIn(name, text, name)
        self.assertIn("any other legal server", text)

    def test_the_probe_names_every_namespace_key_of_the_schema(self):
        text = self.instruction()
        properties = json.loads(
            (PLUGIN_ROOT / "schemas" / "mcp-probe.schema.json").read_text(encoding="utf-8-sig")
        )["properties"]["namespaces"]["properties"]
        for key in properties:
            self.assertIn(f'"{key}"', text, key)

    def test_the_probe_asks_for_one_live_call_per_connected_server(self):
        """D-147: listing a namespace is not evidence; the LDH quota died on call 2 in the real run."""
        text = self.instruction()
        self.assertIn('resolve("Regulation (EU) 2016/679")', text)
        self.assertIn('legislation_search("Data Protection Act 2018", limit 1)', text)
        self.assertIn('discover_sources("EU")', text)
        self.assertIn("`search`", text)
        self.assertIn('"status"', text)
        for verdict in ("ok", "quota", "auth", "error", "absent"):
            self.assertIn(verdict, text, verdict)
        self.assertNotIn("Do not call the servers", text)


class PreflightStepTest(unittest.TestCase):
    """D-147: `planning` publishes `plan.json`, then probes the portals, then opens gate 4."""

    def steps(self, driver: Driver) -> list[str]:
        return [machine.purpose(row) for row in driver.state()["steps"] if row.get("phase") == "planning"]

    def test_the_preflight_runs_after_the_plan_and_before_the_gate(self):
        driver = Driver(temp_root(self), slug="preflight-order")
        action = driver.run_until("planning")
        self.assertEqual("inline-llm", action["kind"])
        driver.act(action)

        step = driver.next()
        self.assertEqual("script", step["kind"])
        self.assertEqual("sources.preflight", machine.command_key(step["command"]))
        self.assertEqual("planning", step["phase"])
        self.assertTrue((driver.work_dir / "plan.json").is_file(), "the plan is published first")
        driver.act(step)

        gate = driver.next()
        self.assertEqual("gate-auq", gate["kind"])
        self.assertEqual("plan_approval_pending", gate["phase"])
        self.assertEqual(["inline:plan.json", "script:sources.preflight"], self.steps(driver))
        self.assertTrue((driver.work_dir / preflight.PREFLIGHT_PATH).is_file())

    def test_a_plan_edit_re_runs_the_preflight_on_the_new_plan(self):
        """§2.2 `reason: rerun`: the corrected plan may name another jurisdiction."""
        driver = Driver(temp_root(self), slug="preflight-edit")
        gate = driver.run_until("plan_approval_pending")
        driver.report(gate["step_id"], gate["attempt"], answers=json.dumps({"Plan": "Edit"}),
                      generation=gate.get("generation", 0))
        driver.act(driver.run_until("planning"))
        # The planner rewrote `plan.json`; the sha of the declared input changed with it.
        state_io.write_json_atomic(
            driver.work_dir / "plan.json", dict(probe.fixture_plan(), jurisdictions=["UK"])
        )
        step = driver.next()
        self.assertEqual("sources.preflight", machine.command_key(step["command"]))
        self.assertEqual("rerun", machine.step_row(driver.state(), step["step_id"], step["attempt"])["reason"])

    def test_the_researcher_is_told_what_did_not_answer(self):
        driver = Driver(temp_root(self), slug="preflight-prompt")
        driver.run_until("plan_approval_pending")
        specs = machine.researcher_specs(driver.work_dir, driver.state(), ["statutes"])
        self.assertEqual(
            preflight.CLEAN_LINE, specs[0]["extra"]["source_access"], "the offline run answered ok"
        )
        self.assertIn(preflight.PREFLIGHT_PATH, specs[0]["inputs"])


class McpSmokeStatusTest(unittest.TestCase):
    """D-147: a connected server whose smoke call failed is dropped like an exhausted quota (D-122)."""

    def test_a_failed_server_leaves_the_routing_digest_of_the_analyst(self):
        driver = Driver(temp_root(self), slug="smoke-status")
        action = driver.run_until("intake_preliminary_research")
        target = driver.work_dir / action["write_to"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            target,
            dict(
                probe.fixture_mcp_probe(),
                status={"ldh": "quota", "courtlistener": "ok", "legalviz": "error", "uklegal": "ok"},
            ),
        )
        driver.report(action["step_id"], action["attempt"])
        analyst = driver.next()
        self.assertEqual("dispatch", analyst["kind"])
        prompt = analyst["agents"][0]["prompt"]
        digest = prompt.split("(statutes / case law):", 1)[1].split("## Write", 1)[0]
        self.assertNotIn("ldh_", digest, "a spent quota is not offered")
        self.assertNotIn("legalviz_", digest, "a server that failed its smoke call is not offered")
        self.assertIn("uklegal_legislation_search", digest)
        self.assertIn("courtlistener_search", digest)

    def test_a_probe_without_status_still_offers_every_connected_server(self):
        usable = machine.preflight.usable_namespaces(probe.fixture_mcp_probe() | {"status": {}})
        self.assertIn("ldh", usable)


class SlotTimingTest(unittest.TestCase):
    """D-120: `agent_returned` measures the slot, not the round trip around it."""

    @staticmethod
    def _log(driver: Driver, action: dict, slot: str, state: str) -> None:
        machine.run_agent_log(
            namespace(
                workdir=str(driver.work_dir),
                step=action["step_id"],
                attempt=action["attempt"],
                slot=slot,
                state=state,
                detail=None,
                mcp=None,
            )
        )

    @staticmethod
    def _returned(driver: Driver) -> list[dict]:
        return [
            row["data"]
            for row in events.read_events(driver.work_dir)
            if row["event"] == "agent_returned"
        ]

    def test_the_duration_is_the_slot_window_and_the_lags_are_their_own_fields(self):
        driver = Driver(temp_root(self), mode="brief", slug="timing")
        action = driver.run_until("research")
        slot = action["agents"][0]["slot"]
        self._log(driver, action, slot, "start")
        probe.run_fixture_agent(driver.work_dir, driver.state(), action, action["agents"][0])
        driver.report(action["step_id"], action["attempt"], agent=slot)

        data = self._returned(driver)[-1]
        self.assertEqual(slot, data["slot"])
        for field in ("duration_seconds", "queue_s", "barrier_s"):
            self.assertIsNotNone(data[field], field)
            self.assertGreaterEqual(data[field], 0.0)
        issued = machine.step_row(driver.state(), action["step_id"], action["attempt"])["issued_at"]
        total = (
            machine._stamp(events.utc_now()) - machine._stamp(issued)
        ).total_seconds()
        parts = data["queue_s"] + data["duration_seconds"] + data["barrier_s"]
        self.assertLessEqual(parts, total + 0.5, data)

    def test_without_a_start_event_the_old_value_stands_and_the_queue_is_null(self):
        driver = Driver(temp_root(self), mode="brief", slug="timing-fallback")
        action = driver.run_until("research")
        agent = action["agents"][0]
        # The fixture agent logs only `done`, the way an agent that forgot its first Bash call would.
        probe.run_fixture_agent(driver.work_dir, driver.state(), action, agent)
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])

        data = self._returned(driver)[-1]
        self.assertIsNotNone(data["duration_seconds"], "the issue -> report value is the fallback")
        self.assertIsNone(data["queue_s"], "no `start` event, no measurable queue")
        self.assertIsNotNone(data["barrier_s"])


class TerminalProgressTest(unittest.TestCase):
    """D-121: «0 of 12» at the end of a finished run reads as a broken counter."""

    def test_a_terminal_phase_puts_the_position_at_the_denominator(self):
        driver = Driver(temp_root(self), mode="brief", slug="terminal-progress")
        driver.run_to_end()
        progress = driver.state()["progress"]
        self.assertTrue(phases.is_terminal(driver.state()["current_phase"]))
        self.assertEqual(progress["total"], progress["position"])
        self.assertEqual(len(progress["route"]), progress["position"])

    def test_a_live_phase_still_counts_from_one(self):
        driver = Driver(temp_root(self), mode="brief", slug="live-progress")
        driver.run_until("research")
        progress = driver.state()["progress"]
        self.assertLess(progress["position"], progress["total"])
        self.assertGreater(progress["position"], 0)


class McpExhaustionTest(unittest.TestCase):
    """D-122: a provider that says «quota exhausted» is off the routing table for the day."""

    def _driver(self, slug: str) -> Driver:
        return Driver(temp_root(self), mode="brief", slug=slug)

    @staticmethod
    def _fallback(driver: Driver, **data) -> None:
        events.append_event(driver.work_dir, "mcp_ratelimit_fallback", "statutes", data)

    def test_a_quota_note_records_the_alias_with_today_s_date(self):
        driver = self._driver("exhausted")
        self._fallback(
            driver,
            server="Legal_Data_Hunter",
            retry_after=None,
            note="daily free-plan quota exhausted after 2 resolve_reference calls",
        )
        merged = machine.record_mcp_exhaustion(driver.work_dir, driver.state())
        self.assertEqual({"ldh": events.utc_now()[:10]}, merged)
        self.assertEqual(merged, driver.state()["mcp_exhausted"])
        self.assertEqual(("ldh",), machine.mcp_exhausted_today(driver.state()))

    def test_a_plain_throttle_is_not_an_exhaustion(self):
        driver = self._driver("throttled")
        self._fallback(driver, server="ldh", retry_after=30, note="rate limited, retry shortly")
        self.assertEqual({}, machine.record_mcp_exhaustion(driver.work_dir, driver.state()))
        self.assertEqual((), machine.mcp_exhausted_today(driver.state()))

    def test_the_alias_is_matched_however_the_agent_spelled_the_server(self):
        for name in ("ldh", "Legal Data Hunter", "legal-data-hunter", "LEGAL_DATA_HUNTER"):
            with self.subTest(server=name):
                self.assertEqual("ldh", machine._exhaustion_alias(name))
        self.assertEqual("uklegal", machine._exhaustion_alias("uk-legal"))
        self.assertEqual("legalviz", machine._exhaustion_alias("LegalViz"))
        self.assertIsNone(machine._exhaustion_alias("some other server"))

    def test_yesterday_s_exhaustion_no_longer_filters_the_digest(self):
        driver = self._driver("exhausted-yesterday")

        def mutate(state: dict) -> None:
            state["mcp_exhausted"] = {"ldh": "2000-01-01"}

        state_io.write_state(driver.work_dir, mutate)
        self.assertEqual((), machine.mcp_exhausted_today(driver.state()))

    def test_the_exhausted_server_leaves_the_routing_digest(self):
        from memoforge import routing

        connected = {"ldh": "mcp__x__", "courtlistener": "mcp__y__"}
        before = routing.routing_digest(connected)
        after = routing.routing_digest(connected, exhausted=("ldh",))
        self.assertIn("ldh_", before)
        self.assertNotIn("ldh_", after)
        self.assertIn("courtlistener", after)
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))

    def test_the_ldh_daily_ceiling_matches_what_the_provider_really_allows(self):
        self.assertEqual(10, limits.MCP_PROVIDER_DAILY_LIMITS["ldh"])


class KnownBlockersTest(unittest.TestCase):
    """D-119: the readiness reviewer is told what the review loop already wrote down."""

    SHA = "b" * 64
    ISSUES = [
        {"section_id": "s-2", "category": "unsupported_claim", "severity": "blocker",
         "issue": "Article 17(1) is quoted without the rule it is said to carry."},
        {"section_id": "s-3", "category": "missing_counterargument", "severity": "blocker",
         "issue": "The one-stop-shop mechanism of Article 56 is never addressed."},
    ]

    def _state(self, *, aggregated_sha: str, draft_sha: str) -> dict:
        return {
            "remaining_blocking_issues": list(self.ISSUES),
            "iterations": [{"iteration": 1, "draft_sha": aggregated_sha}],
            "current_draft_sha": draft_sha,
        }

    def test_the_blockers_of_this_very_draft_are_handed_over(self):
        text = machine.known_blockers_text(self._state(aggregated_sha=self.SHA, draft_sha=self.SHA), self.SHA)
        self.assertIn("2 blocking issue(s)", text)
        self.assertIn("NEW", text)
        self.assertIn("Article 17(1)", text)
        self.assertIn("s-3 · missing_counterargument", text)

    def test_a_draft_the_aggregate_never_saw_hands_over_nothing(self):
        state = self._state(aggregated_sha="c" * 64, draft_sha=self.SHA)
        self.assertEqual(machine.KNOWN_BLOCKERS_NONE, machine.known_blockers_text(state, self.SHA))

    def test_a_run_without_blockers_hands_over_nothing(self):
        self.assertEqual(machine.KNOWN_BLOCKERS_NONE, machine.known_blockers_text({}, self.SHA))

    def test_the_readiness_dispatch_carries_the_extra(self):
        driver = Driver(temp_root(self), mode="brief", slug="known-blockers")
        action = driver.run_until("client_readiness")
        self.assertIn("already known blockers: none", action["agents"][0]["prompt"])

    def test_the_prompt_repeats_the_aggregated_blockers_when_there_are_some(self):
        driver = Driver(temp_root(self), mode="brief", slug="known-blockers-live")
        action = driver.run_until("client_readiness")

        def mutate(state: dict) -> None:
            state["remaining_blocking_issues"] = list(self.ISSUES)
            state["iterations"] = [
                dict(row, draft_sha=state.get("current_draft_sha"))
                for row in (state.get("iterations") or [{"iteration": 1}])
            ]

        state_io.write_state(driver.work_dir, mutate)
        state = driver.state()
        text = machine.known_blockers_text(state, str(machine.current_draft_sha(state) or ""))
        self.assertIn("Article 17(1)", text)
        self.assertIn("NEW", text)
        self.assertNotEqual(machine.KNOWN_BLOCKERS_NONE, text)
        self.assertIsNotNone(action)


class MachineWarningLanguageTest(unittest.TestCase):
    """D-175: the three code-written `machine.py` warnings are created in the memo language."""

    RU_WARNINGS = {
        "memo.warnings.no_findings_for_layers": "нет находок по слоям: {layers}",
        "memo.warnings.continue_with_incomplete_research": (
            "пользователь решил продолжить при неполном исследовании"
        ),
        "memo.warnings.currency_unchecked": "актуальность источника не удалось проверить",
    }

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.packs = self.root / "packs"
        self.packs.mkdir()
        patcher = mock.patch.object(machine.i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", self.RU_WARNINGS)

    def _state(self, phase: str) -> tuple:
        from memoforge import modes, state_io, task as _task

        work_dir = self.root / f"memo-20260908T120000Z-w-{phase.replace('_', '-')}"
        _task.create_work_dir_tree(work_dir)
        state = _task.build_initial_state(
            task_id=work_dir.name,
            user_query="How long may the client keep customer records?",
            language="ru",
            work_dir=work_dir,
            output_folder=self.root,
            config=modes.resolve_config("full"),
        )
        state["current_phase"] = phase
        state_io.create_state(work_dir, state)
        return work_dir, state

    def test_a_partially_failed_research_dispatch_warns_in_russian(self):
        work_dir, state = self._state("research")
        slot = {
            "slot": "doctrine",
            "status": "fail",
            "attempt": 1,
            "agent_type": "memoforge:legal-researcher",
        }
        other = {
            "slot": "statutes",
            "status": "ok",
            "attempt": 1,
            "agent_type": "memoforge:legal-researcher",
        }
        step_id = "s-001"

        def seed(current: dict) -> None:
            current["current_phase"] = "research"
            current["steps"] = [
                {
                    "step_id": step_id,
                    "kind": "dispatch",
                    "phase": "research",
                    "attempt": 1,
                    "reason": "initial",
                    "status": "ok",
                    "agents": [slot, other],
                }
            ]

        machine.state_io.write_state(work_dir, seed)
        machine.plan_research(work_dir, machine.state_io.read_state(work_dir))
        warnings = machine.state_io.read_state(work_dir)["drafting_warnings"]
        found = [row for row in warnings if row["code"] == "research_layer_missing"]
        self.assertEqual(1, len(found), warnings)
        self.assertEqual("нет находок по слоям: doctrine", found[0]["message"])
        self.assertEqual("research_sufficiency", machine.state_io.read_state(work_dir)["current_phase"])

    def test_accepting_insufficient_research_warns_in_russian(self):
        work_dir, state = self._state("research_insufficient_pending")
        gate = machine.plan_research_insufficient_pending(
            work_dir, machine.state_io.read_state(work_dir)
        )
        self.assertEqual("gate-text", gate["kind"])
        machine.gates.run_parse(
            namespace(
                workdir=str(work_dir), gate=None, text="continue",
                step=gate["step_id"], attempt=gate["attempt"], generation=0,
            )
        )
        machine.run_next(namespace(workdir=str(work_dir)))
        warnings = machine.state_io.read_state(work_dir)["drafting_warnings"]
        found = [row for row in warnings if row["code"] == "insufficient_research_accepted"]
        self.assertEqual(1, len(found), warnings)
        self.assertEqual(
            "пользователь решил продолжить при неполном исследовании", found[0]["message"]
        )

    def test_an_unavailable_currency_checker_warns_in_russian(self):
        work_dir, state = self._state("currency_check")

        def seed(current: dict) -> None:
            current["current_phase"] = "currency_check"
            agent = {
                "slot": "currency",
                "status": "fail",
                "attempt": 1,
                "agent_type": "memoforge:currency-checker",
            }
            current["steps"] = [
                {
                    "step_id": "s-010",
                    "kind": "dispatch",
                    "phase": "currency_check",
                    "attempt": 1,
                    "reason": "initial",
                    "status": "fail",
                    "agents": [agent],
                }
            ]

        machine.state_io.write_state(work_dir, seed)
        machine.plan_currency_check(work_dir, machine.state_io.read_state(work_dir))
        # The failure writes the unchecked report, the warning and the currency gate answer.
        gate = machine.run_next(namespace(workdir=str(work_dir)))
        while gate["kind"] == "script":
            machine.run_command(list(gate["command"]))
            gate = machine.run_next(namespace(workdir=str(work_dir)))
        warnings = machine.state_io.read_state(work_dir)["drafting_warnings"]
        found = [row for row in warnings if row["code"] == "currency_unchecked"]
        self.assertEqual(1, len(found), warnings)
        self.assertEqual("актуальность источника не удалось проверить", found[0]["message"])


class UiLanguageGateTest(unittest.TestCase):
    """D-176 / D-176a: the gates `next` issues speak the UI language; the answers stay canonical."""

    def setUp(self):
        self.packs = Path(temp_root(self)) / "packs"
        self.packs.mkdir()
        patcher = mock.patch.object(machine.i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", _i18n.RU_UI)

    def _russian_task(self, slug: str) -> Driver:
        driver = Driver(temp_root(self), slug=slug)
        state_io.write_state(
            driver.work_dir, lambda current: current.update({"ui_language": "ru"})
        )
        return driver

    def _with_dashboard(self, driver: Driver) -> dict:
        """The state of a run whose page is live, so the gates print the pointer form (D-94)."""
        state = dict(driver.state())
        state["config"] = dict(state.get("config") or {}, dashboard=True)
        state["progress"] = dict(state.get("progress") or {}, artifact_url="https://example.test/x")
        return state

    def test_a_localized_auq_answer_is_recorded_canonically_and_the_run_proceeds(self):
        driver = self._russian_task("ui-auq")
        action = driver.run_until("plan_approval_pending")
        self.assertEqual("gate-auq", action["kind"])
        answer = driver.report(
            action["step_id"],
            action["attempt"],
            answers=json.dumps({"План": "Утвердить", "Режим": "Кратко"}),
            generation=action.get("generation", 0),
        )
        self.assertTrue(answer["accepted"])
        iteration = driver.state()["plan_approval"]["iterations"][-1]
        self.assertEqual({"Plan": "Approve", "Mode": "Brief"}, iteration["answers"])
        self.assertEqual("approved", driver.state()["plan_approval"]["status"])
        driver.next()
        self.assertEqual("research", driver.state()["current_phase"])
        self.assertEqual("brief", driver.state()["mode"])

    def test_resume_gate_reissues_the_plan_gate_in_russian(self):
        driver = self._russian_task("ui-resume")
        issued = driver.run_until("plan_approval_pending")
        reissued = driver.next()
        self.assertTrue(reissued.get("reissued"))
        self.assertEqual(
            ["План", "Режим"], [question["header"] for question in reissued["questions"][:2]]
        )
        self.assertIn("Утвердить этот план исследования?", reissued["questions"][0]["question"])
        self.assertIn("Правовых вопросов для исследования:", reissued["text"])
        self.assertIn("Ответьте одним из:", reissued["text_fallback"])
        self.assertEqual(issued["text"], reissued["text"])

    def test_a_russian_intake_gate_keeps_the_english_pointer_shape(self):
        """D-103: `_gate_reply_lines` lifts the reply format by shape, in every language."""
        driver = self._russian_task("ui-pointer")
        action = driver.run_until("intake_questions_pending")
        russian = action["text"]
        english = gates.render(
            driver.work_dir, dict(driver.state(), ui_language="en"), "intake"
        )
        lines_ru = machine._gate_reply_lines(russian, True)
        lines_en = machine._gate_reply_lines(english, True)
        self.assertEqual(len(lines_en), len(lines_ru))
        self.assertTrue(lines_ru[0].endswith(":"), lines_ru)
        self.assertEqual(
            ["Ответьте в формате `1A 2C 3: свободный текст`:",
             "`proceed` принимает все допущения как есть. `cancel` останавливает задачу."],
            lines_ru,
        )

    def test_the_dashboard_pointer_of_a_text_gate_is_localized(self):
        driver = self._russian_task("ui-pointer-dashboard")
        action = driver.run_until("intake_questions_pending")
        state = self._with_dashboard(driver)
        pointer = machine.gate_text(
            driver.work_dir, state, "intake", gates.render(driver.work_dir, state, "intake")
        )
        self.assertIn("Ваши вводные ответы (вопросов: 2) — на панели:", pointer)
        self.assertIn("Ответьте в формате", pointer)
        self.assertNotIn("are on the dashboard", pointer)
        self.assertEqual(action["phase"], "intake_questions_pending")

    def test_the_plan_gate_pointer_names_the_memo_language_outside_en_en(self):
        driver = self._russian_task("ui-plan-pointer")
        driver.run_until("plan_approval_pending")
        state = self._with_dashboard(driver)
        text = machine.plan_gate_text(driver.work_dir, state)
        self.assertIn("План исследования — на вашей панели: https://example.test/x", text)
        self.assertIn("Язык мемо: English", text)
        self.assertIn("Правовых вопросов: 1 · рекомендуемый режим: full", text)
        english = dict(state, ui_language="en")
        self.assertNotIn("Memo language", machine.plan_gate_text(driver.work_dir, english))

    def test_the_dashboard_patch_carries_labels_and_the_memo_language_off_en_en(self):
        """D-177 (patch only): `labels` is the whole `ui.dashboard` dict; the plan card names
        the memo language exactly when `gates.memo_language_line` prints it."""
        from memoforge import i18n_en  # noqa: E402 - local import keeps the module header stable

        from memoforge import i18n  # noqa: E402 - local import keeps the module header stable

        driver = self._russian_task("ui-patch")
        state = dict(driver.state())
        patch = machine.dashboard_patch(state)
        self.assertEqual(patch["labels"], i18n.node("ru", "ui.dashboard"))
        self.assertEqual(patch["labels"]["card_your_turn"], "Ваш ход")
        self.assertEqual(patch["status_label"], "Работаем")
        self.assertNotIn("memo_language", patch["plan"] or {})
        probe_plan = probe.fixture_plan()
        state_io.write_json_atomic(driver.work_dir / gates.PLAN_PATH, probe_plan)
        patch = machine.dashboard_patch(dict(driver.state()))
        self.assertEqual(patch["plan"]["memo_language"], "English")
        english = dict(driver.state(), ui_language="en")
        before = {key: value for key, value in machine.dashboard_patch(english).items()}
        self.assertEqual(before["labels"], i18n_en.EN["ui"]["dashboard"])
        self.assertNotIn("memo_language", before["plan"] or {})


class PlannerUiLanguageTest(unittest.TestCase):
    """D-173b (UI half): the planner writes the gate-visible plan fields in the UI language."""

    def _instruction(self, *, ui_language: str, language: str = "en") -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        packs = Path(tmp.name)
        _i18n.fake_pack(packs, "ru", _i18n.RU)
        with mock.patch.object(machine.i18n, "PACK_DIR", packs):
            work_dir = temp_root(self) / "planner-ui"
            work_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "task_id": "memo-20260908T120000Z-planner-ui",
                "user_query": "How long may the client keep customer records?",
                "language": language,
                "ui_language": ui_language,
                "config": {},
                "current_iteration": 1,
            }
            spec = machine.inline_spec(work_dir, state, "planning", "s-003", 1)
            assert spec is not None
            return spec["instruction"]

    def test_the_russian_planner_prompt_names_russian_for_the_plan_fields(self):
        instruction = self._instruction(ui_language="ru")
        self.assertIn("Russian", instruction)
        self.assertIn("`issues[].title`", instruction)
        self.assertIn("`issues[].question`", instruction)
        self.assertIn("`issue_id`", instruction)

    def test_the_english_planner_prompt_names_english(self):
        self.assertIn("English", self._instruction(ui_language="en"))

    def test_the_classification_is_named_among_the_machine_fields(self):
        """Final review, finding 1: `schemas/plan.schema.json` accepts six English values for
        `classification`, so a planner told to write it in Russian produces a rejected plan.

        The paragraph names it with `estimated_complexity` on the unchanged side, and the list
        of fields to translate — everything between the lead and that sentence — holds only the
        three prose fields.
        """
        instruction = self._instruction(ui_language="ru")
        prose, marker, machine_fields = instruction.partition("The machine fields stay English")
        self.assertTrue(marker, instruction)
        self.assertIn("`classification`", machine_fields)
        self.assertIn("`estimated_complexity`", machine_fields)
        translated = prose.split("prints your prose verbatim", 1)[1]
        self.assertIn("`notes` in Russian", translated)
        for field in ("`classification`", "`estimated_complexity`", "`issue_id`", "`layer`"):
            self.assertNotIn(field, translated, field)


if __name__ == "__main__":
    unittest.main()
