"""Tests for the optional live dashboard — ТЗ §7.5, D-87 (page, patch, `next` block, CLI, docs)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, namespace, temp_root  # noqa: E402
from memoforge import (  # noqa: E402
    events,
    fallbacks,
    gates,
    machine,
    probe,
    sources,
    state_io,
    task,
)

PAGE = PLUGIN_ROOT / "lib" / "dashboard.html"
ROUTER = PLUGIN_ROOT / "skills" / "memo" / "references" / "router.md"

URL = "https://claude.ai/public/artifacts/dry-run-page"


def fixture_state() -> dict:
    """A run mid-research: one open dispatch, one closed script step, one banner."""
    return {
        "task_id": "memo-20260908T120000Z-fixture",
        "user_query": "How long may the client keep customer records?",
        "work_dir": "/tmp/memo-20260908T120000Z-fixture",
        "mode": "full",
        "current_phase": "research",
        "final_docx_path": None,
        "config": {"dashboard": True},
        "fallback_banners": [
            {"banner_id": "mcp_partial", "text": "Partial MCP coverage — only ldh was reachable."}
        ],
        "steps": [
            {
                "step_id": "s-001",
                "kind": "script",
                "phase": "intake_preliminary_research",
                "issued_at": "2026-09-08T12:00:00.000Z",
                "attempt": 1,
                "status": "ok",
                "command": ["/plugin/scripts/mf", "sources", "register", "--workdir", "W"],
            },
            {
                "step_id": "s-002",
                "kind": "dispatch",
                "phase": "research",
                "issued_at": "2026-09-08T12:05:00.000Z",
                "attempt": 1,
                "status": None,
                "agents": [
                    {"slot": "statutes", "agent_type": "memoforge:legal-researcher", "status": None}
                ],
            },
        ],
        "progress": {
            "phase": "research",
            "route": ["intake_preliminary_research", "research", "drafting"],
            "position": 2,
            "total": 3,
            "active": [
                {
                    "slot": "statutes",
                    "agent_type": "memoforge:legal-researcher",
                    "label": "research",
                    "started_at": "2026-09-08T12:05:00.000Z",
                }
            ],
            "last_line": "Phase 2/3 — research: 1 researcher dispatched (statutes)",
            "artifact_url": None,
            "mcp_calls": {},
        },
    }


class DashboardPatchTest(unittest.TestCase):
    """§7.5: the whole document the page renders is derived from `state` alone."""

    def setUp(self) -> None:
        self.patch = machine.dashboard_patch(fixture_state())

    def test_the_patch_carries_every_field_the_page_reads(self):
        self.assertEqual(
            sorted(self.patch),
            sorted(
                [
                    "task_id",
                    "query",
                    "mode",
                    "phase",
                    "phase_label",
                    "phase_no",
                    "phase_total",
                    "chat_line",
                    "status",
                    "status_label",
                    "steps_done",
                    "steps_total",
                    "agents_running",
                    "timeline",
                    "banners",
                    "gate",
                    "plan",
                    "intake",
                    "sources",
                    "reviews",
                    "memo",
                    "deliverable",
                    "updated_at",
                ]
            ),
        )

    def test_the_header_values_come_from_progress(self):
        self.assertEqual(self.patch["task_id"], "memo-20260908T120000Z-fixture")
        self.assertEqual(self.patch["mode"], "full")
        self.assertEqual(self.patch["phase"], "research")
        self.assertEqual((self.patch["phase_no"], self.patch["phase_total"]), (2, 3))
        self.assertEqual(
            self.patch["chat_line"], "Phase 2/3 — research: 1 researcher dispatched (statutes)"
        )
        self.assertEqual(self.patch["status"], "running")
        self.assertEqual(self.patch["query"], "How long may the client keep customer records?")
        self.assertEqual(self.patch["phase_label"], "Legal research")
        self.assertEqual(self.patch["status_label"], "Working")

    def test_steps_are_counted_by_their_latest_attempt(self):
        self.assertEqual((self.patch["steps_done"], self.patch["steps_total"]), (1, 2))

    def test_running_agents_carry_slot_description_and_start(self):
        self.assertEqual(
            self.patch["agents_running"],
            [
                {
                    "slot": "statutes",
                    "description": "legal-researcher · research",
                    "since": "2026-09-08T12:05:00.000Z",
                }
            ],
        )

    def test_the_timeline_is_the_last_twelve_steps_in_plain_english(self):
        """D-95: a sentence per step — no phase name, no command key, no `purpose()` token."""
        self.assertEqual(
            self.patch["timeline"],
            [
                {
                    "ts": "2026-09-08T12:00:00.000Z",
                    "text": "Checking which legal databases are available step",
                    "state": "done",
                },
                {
                    "ts": "2026-09-08T12:05:00.000Z",
                    "text": "Researcher (statutes): started",
                    "state": "running",
                },
            ],
        )
        state = fixture_state()
        row = dict(state["steps"][0])
        state["steps"] = [dict(row, step_id=f"s-{index:03d}") for index in range(30)]
        self.assertEqual(len(machine.dashboard_patch(state)["timeline"]), machine.DASHBOARD_TIMELINE)
        self.assertEqual(machine.DASHBOARD_TIMELINE, 12)

    def test_banners_are_reduced_to_id_and_static_label(self):
        """D-88: the label comes from `fallbacks.py`, never from the rendered banner in `state`."""
        label = fallbacks.dashboard_label("mcp_partial")
        self.assertEqual(self.patch["banners"], [{"id": "mcp_partial", "text": label}])
        self.assertNotIn("ldh", label)

    def test_the_deliverable_is_empty_until_finalize_wrote_one(self):
        self.assertEqual(self.patch["deliverable"], "")
        state = fixture_state()
        state["current_phase"] = "done"
        state["final_status"] = "delivered"
        state["final_docx_path"] = "deliverable.docx"
        final = machine.dashboard_patch(state)
        self.assertEqual(final["status"], "delivered")
        self.assertTrue(final["deliverable"].endswith("deliverable.docx"))
        self.assertNotEqual(final["deliverable"], "deliverable.docx")

    def test_a_gate_phase_says_the_run_is_waiting_for_the_user(self):
        state = fixture_state()
        state["current_phase"] = "plan_approval_pending"
        patch = machine.dashboard_patch(state)
        self.assertEqual(patch["status"], "waiting for you")
        self.assertEqual(patch["status_label"], "Your turn")
        self.assertEqual(patch["phase_label"], "Plan approval")

    def test_the_labels_of_a_review_round_and_of_the_end(self):
        """D-95: `Review round 2` while the run iterates; a terminal phase names itself."""
        state = fixture_state()
        state["current_phase"] = "revision_loop"
        state["current_iteration"] = 2
        self.assertEqual(machine.dashboard_patch(state)["phase_label"], "Review round 2")
        for phase, label in (
            ("done", "Done"),
            ("failed", "Stopped with a fallback deliverable"),
            ("cancelled_by_user", "Cancelled"),
        ):
            with self.subTest(phase=phase):
                state["current_phase"] = phase
                patch = machine.dashboard_patch(state)
                self.assertEqual((patch["phase_label"], patch["status_label"]), (label, label))

    def test_updated_at_is_a_utc_stamp(self):
        self.assertRegex(self.patch["updated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


class DashboardPlanAndGateTest(unittest.TestCase):
    """D-89: the page shows the plan as structure and says when the run waits at a gate."""

    def setUp(self) -> None:
        self.driver = Driver(temp_root(self), "full", slug="plan", user_config={"dashboard": True})

    def test_there_is_no_plan_before_planning_wrote_one(self):
        patch = machine.dashboard_patch(self.driver.state())
        self.assertIsNone(patch["plan"])
        self.assertFalse((self.driver.work_dir / gates.PLAN_PATH).exists())

    def test_the_plan_gate_carries_the_plan_and_the_waiting_notice(self):
        action = self.driver.run_until("plan_approval_pending")
        patch = machine.dashboard_patch(self.driver.state())
        gate = patch["gate"]
        self.assertEqual(gate["phase"], "plan_approval_pending")
        self.assertEqual(gate["phase_label"], "Plan approval")
        self.assertEqual(gate["kind"], machine.KIND_GATE_AUQ)
        self.assertEqual(gate["hint"], machine.DASHBOARD_GATE_HINT)
        plan = patch["plan"]
        fixture = probe.fixture_plan()
        self.assertEqual(plan["classification"], fixture["classification"])
        self.assertEqual(plan["jurisdictions"], fixture["jurisdictions"])
        self.assertEqual(plan["complexity"], fixture["estimated_complexity"])
        self.assertEqual(plan["recommended_mode"], gates.recommended_mode(fixture))
        self.assertIn("doctrine", plan["layers"])
        self.assertEqual(
            plan["issues"],
            [
                {
                    "id": "i1",
                    "title": "Retention of customer records",
                    "question": "How long may the client keep customer records?",
                    "jurisdictions": ["EU"],
                }
            ],
        )
        self.assertFalse(plan["approved"])
        self.assertEqual(action["kind"], machine.KIND_GATE_AUQ)

    def test_the_approval_clears_the_gate_and_marks_the_plan(self):
        self.driver.act(self.driver.run_until("plan_approval_pending"))
        self.driver.next()
        patch = machine.dashboard_patch(self.driver.state())
        self.assertIsNone(patch["gate"])
        self.assertTrue(patch["plan"]["approved"])

    def test_the_issue_list_is_capped_like_the_gate_digest(self):
        plan = probe.fixture_plan()
        issue = dict(plan["issues"][0])
        plan["issues"] = [dict(issue, issue_id=f"i{index}") for index in range(30)]
        state_io.write_json_atomic(self.driver.work_dir / gates.PLAN_PATH, plan)
        patch = machine.dashboard_patch(self.driver.state())
        self.assertEqual(len(patch["plan"]["issues"]), gates.PLAN_DIGEST_MAX_ISSUES)

    def test_a_text_gate_is_reported_as_the_text_channel_and_carries_no_plan(self):
        action = self.driver.run_until("intake_questions_pending")
        self.assertEqual(action["kind"], machine.KIND_GATE_TEXT)
        patch = machine.dashboard_patch(self.driver.state())
        gate = patch["gate"]
        self.assertEqual(gate["phase"], "intake_questions_pending")
        self.assertEqual(gate["phase_label"], "Your intake answers")
        self.assertEqual(gate["kind"], machine.KIND_GATE_TEXT)
        self.assertEqual(gate["hint"], machine.DASHBOARD_GATE_HINT)
        self.assertIsNone(patch["plan"])


def malformed_plans() -> dict[str, dict]:
    """Syntactically valid JSON that `schemas/plan.schema.json` rejects (D-90)."""

    def issue(**overrides) -> dict:
        row = {
            "issue_id": "i1",
            "title": "Retention of customer records",
            "question": "How long may the client keep customer records?",
            "jurisdictions": ["EU"],
        }
        row.update(overrides)
        return dict(probe.fixture_plan(), issues=[row])

    return {
        "issues is not a list": {"issues": 42},
        "jurisdictions is not a list": {"jurisdictions": 42},
        "issue jurisdictions is a bare string": issue(jurisdictions="EU"),
        "issue title is not a string": issue(title={"text": "Retention"}),
        "half-written plan": {"classification": "regulatory_analysis"},
    }


class MalformedPlanIsNotProjectedTest(unittest.TestCase):
    """D-90: a structurally wrong `plan.json` is reported as `plan: null` and never breaks `next`."""

    def setUp(self) -> None:
        self.driver = Driver(
            temp_root(self), "brief", slug="bad-plan", user_config={"dashboard": True}
        )
        task.run_dashboard(namespace(workdir=str(self.driver.work_dir), url=URL, unavailable=None))

    def test_next_still_answers_and_the_plan_is_none(self):
        for name, plan in malformed_plans().items():
            with self.subTest(name):
                state_io.write_json_atomic(self.driver.work_dir / gates.PLAN_PATH, plan)
                action = self.driver.next()
                self.assertNotIn("errors", action)
                self.assertTrue(action.get("kind"))
                patch = json.loads(
                    Path(action["dashboard"]["write_db"]["file_path"]).read_text(encoding="utf-8")
                )
                self.assertIsNone(patch["plan"])

    def test_the_patch_is_still_serialisable_for_the_page(self):
        state_io.write_json_atomic(self.driver.work_dir / gates.PLAN_PATH, {"issues": 42})
        patch = machine.dashboard_patch(self.driver.state())
        self.assertIsNone(patch["plan"])
        self.assertEqual(json.loads(json.dumps(patch))["plan"], None)

    def test_a_valid_plan_written_by_planning_is_still_projected(self):
        state_io.write_json_atomic(self.driver.work_dir / gates.PLAN_PATH, probe.fixture_plan())
        patch = machine.dashboard_patch(self.driver.state())
        self.assertEqual(patch["plan"]["classification"], "regulatory_analysis")
        self.assertEqual([issue["id"] for issue in patch["plan"]["issues"]], ["i1"])


class NextCarriesTheDashboardTest(unittest.TestCase):
    """§7.5: publish once, then one `write_db` per step — and nothing at all when the flag is off."""

    def test_no_dashboard_key_when_the_flag_is_off(self):
        driver = Driver(temp_root(self), "brief", slug="dash-off", user_config={"dashboard": False})
        for _ in range(3):
            action = driver.next()
            self.assertNotIn("dashboard", action)
            driver.act(action)

    def test_the_first_answer_asks_for_one_publish(self):
        driver = Driver(temp_root(self), "brief", slug="dash-pub", user_config={"dashboard": True})
        block = driver.next()["dashboard"]
        self.assertEqual(sorted(block), ["publish", "then"])
        publish = block["publish"]
        self.assertEqual(publish["file"], str(PAGE))
        self.assertTrue(Path(publish["file"]).is_file())
        self.assertEqual(publish["title"], f"memoforge · {driver.work_dir.name}")
        self.assertEqual(publish["description"], "Live progress of a memoforge run")
        self.assertEqual(publish["capabilities"], {"db": {}})
        self.assertEqual(publish["favicon"], "⚖️")
        self.assertIn("task dashboard", block["then"])
        self.assertIn(str(driver.work_dir.absolute()), block["then"])
        self.assertIn("<URL>", block["then"])

    def test_once_the_url_is_known_every_answer_carries_one_write_db(self):
        driver = Driver(temp_root(self), "brief", slug="dash-db", user_config={"dashboard": True})
        driver.act(driver.next())
        task.run_dashboard(namespace(workdir=str(driver.work_dir), url=URL, unavailable=None))
        for _ in range(3):
            action = driver.next()
            block = action["dashboard"]
            # D-159: the document travels as a file the Artifact tool reads itself — the router never
            # retypes it inline, and the answer carries no `patch` any more.
            self.assertEqual(sorted(block), ["write_db"])
            write = block["write_db"]
            self.assertEqual(sorted(write), ["collection", "doc_id", "file_path", "url"])
            self.assertEqual((write["url"], write["collection"], write["doc_id"]), (URL, "run", "state"))
            patch_file = Path(write["file_path"])
            self.assertTrue(patch_file.is_absolute())
            self.assertEqual(patch_file, (driver.work_dir / "dashboard" / "patch.json").absolute())
            written = json.loads(patch_file.read_text(encoding="utf-8"))
            expected = machine.dashboard_patch(driver.state())
            # `updated_at` is stamped per call; everything else is a pure function of the state.
            self.assertEqual(
                {key: value for key, value in written.items() if key != "updated_at"},
                {key: value for key, value in expected.items() if key != "updated_at"},
            )
            self.assertTrue(written["updated_at"])
            self.assertNotIn("publish", block)
            driver.act(action)

    def test_the_terminal_answer_carries_the_final_patch(self):
        driver = Driver(temp_root(self), "brief", slug="dash-end", user_config={"dashboard": True})
        task.run_dashboard(namespace(workdir=str(driver.work_dir), url=URL, unavailable=None))
        action = driver.run_to_end()
        self.assertEqual(action["kind"], "terminal")
        patch = json.loads(Path(action["dashboard"]["write_db"]["file_path"]).read_text(encoding="utf-8"))
        self.assertTrue(patch["deliverable"], "the page must end on the deliverable path")
        self.assertEqual(patch["phase"], "done")

    def test_the_banner_silences_the_block_for_good(self):
        driver = Driver(temp_root(self), "brief", slug="dash-off2", user_config={"dashboard": True})
        self.assertIn("dashboard", driver.next())
        task.run_dashboard(
            namespace(workdir=str(driver.work_dir), url=None, unavailable="no Artifact tool")
        )
        for _ in range(3):
            action = driver.next()
            self.assertNotIn("dashboard", action)
            driver.act(action)

    def test_a_decline_after_a_publish_stops_the_write_db_too(self):
        """D-88: the decline is final — it is read before the URL branch, not after it."""
        driver = Driver(temp_root(self), "brief", slug="dash-late", user_config={"dashboard": True})
        task.run_dashboard(namespace(workdir=str(driver.work_dir), url=URL, unavailable=None))
        self.assertEqual(sorted(driver.next()["dashboard"]), ["write_db"])
        task.run_dashboard(
            namespace(workdir=str(driver.work_dir), url=None, unavailable="Artifact stopped working")
        )
        self.assertEqual(driver.state()["progress"]["artifact_url"], URL)
        for _ in range(3):
            action = driver.next()
            self.assertNotIn("dashboard", action)
            driver.act(action)


class PlanGateTextFollowsTheDashboardTest(unittest.TestCase):
    """D-94: with a live page the plan gate names it; without one the chat keeps the full digest."""

    def setUp(self) -> None:
        self.driver = Driver(
            temp_root(self), "full", slug="gate-text", user_config={"dashboard": True}
        )

    def publish(self) -> None:
        task.run_dashboard(namespace(workdir=str(self.driver.work_dir), url=URL, unavailable=None))

    def test_without_a_page_the_gate_prints_the_whole_digest(self):
        action = self.driver.run_until("plan_approval_pending")
        digest = gates.render_plan_digest(self.driver.work_dir, self.driver.state())
        self.assertEqual(action["text"], digest)
        self.assertIn("Retention of customer records", action["text"])

    def test_with_a_page_the_gate_is_three_lines_that_point_at_it(self):
        self.publish()
        action = self.driver.run_until("plan_approval_pending")
        text = action["text"]
        lines = text.rstrip("\n").splitlines()
        self.assertEqual(len(lines), 3, text)
        self.assertEqual(lines[0], f"The research plan is on your dashboard: {URL}")
        self.assertEqual(
            lines[1], f"File: {gates.PLAN_PATH} in the working folder {self.driver.work_dir}"
        )
        self.assertEqual(lines[2], "1 legal issue · recommended mode: full · estimated complexity: high")
        self.assertNotIn("Retention of customer records", text)

    def test_the_text_channel_keeps_the_full_digest_either_way(self):
        self.publish()
        action = self.driver.run_until("plan_approval_pending")
        digest = gates.render_plan_digest(self.driver.work_dir, self.driver.state())
        self.assertIn("Retention of customer records", digest)
        self.assertIn(digest.rstrip(), action["text_fallback"])
        self.assertIn("Reply with one of:", action["text_fallback"])

    def test_the_reissued_gate_is_identical(self):
        self.publish()
        issued = self.driver.run_until("plan_approval_pending")
        reissued = self.driver.next()
        self.assertTrue(reissued.get("reissued"))
        self.assertEqual(issued["step_id"], reissued["step_id"])
        self.assertEqual(issued["text"], reissued["text"])
        self.assertEqual(issued["text_fallback"], reissued["text_fallback"])

    def test_a_declined_dashboard_falls_back_to_the_digest(self):
        """D-88: the decline kills the pointer too — there is no page to point at any more."""
        self.publish()
        task.run_dashboard(
            namespace(workdir=str(self.driver.work_dir), url=None, unavailable="Artifact errored")
        )
        action = self.driver.run_until("plan_approval_pending")
        self.assertIn("Retention of customer records", action["text"])
        self.assertNotIn(URL, action["text"])


class TextGatesPointAtThePageTest(unittest.TestCase):
    """D-103: a text gate points at the live page instead of reprinting its questions in chat.

    The rule D-94 gave gate 4, for the four `gate-text` gates: `text` names the page, counts the
    questions and repeats the reply format `gates.render` prints, `text_fallback` keeps the whole
    prompt for the text channel, and `mf gate parse` accepts exactly the same replies.
    """

    INTAKE_FORM = "Please answer, in the form `1A 2C 3: free text`:"
    INTAKE_PROMPT = "`proceed` accepts every assumption as written. `cancel` stops the task."

    def at(self, phase: str, slug: str, *, publish: bool) -> tuple:
        driver = Driver(temp_root(self), "full", slug=slug, user_config={"dashboard": True})
        if publish:
            task.run_dashboard(namespace(workdir=str(driver.work_dir), url=URL, unavailable=None))
        return driver, driver.run_until(phase)

    def test_without_a_page_the_intake_gate_prints_every_question(self):
        driver, action = self.at("intake_questions_pending", "intake-plain", publish=False)
        printed = gates.printed_questions(driver.work_dir, driver.state(), "intake")
        self.assertEqual(action["text"], gates.render(driver.work_dir, driver.state(), "intake"))
        for index, question in enumerate(printed, start=1):
            self.assertIn(f"{index}. {question['question']}", action["text"])

    def test_with_a_page_the_intake_gate_is_a_pointer(self):
        driver, action = self.at("intake_questions_pending", "intake-page", publish=True)
        printed = gates.printed_questions(driver.work_dir, driver.state(), "intake")
        lines = action["text"].rstrip("\n").splitlines()
        self.assertEqual(
            lines[0],
            f"Your intake answers ({len(printed)} questions) are on the dashboard: {URL}",
        )
        self.assertEqual(lines[1:], [self.INTAKE_FORM, self.INTAKE_PROMPT])
        for question in printed:
            self.assertNotIn(question["question"], action["text"])
        self.assertEqual(
            action["text_fallback"], gates.render(driver.work_dir, driver.state(), "intake")
        )

    def test_the_pointer_is_reissued_identical_and_takes_the_same_reply(self):
        driver, issued = self.at("intake_questions_pending", "intake-parse", publish=True)
        reissued = driver.next()
        self.assertTrue(reissued.get("reissued"))
        self.assertEqual(issued["text"], reissued["text"])
        self.assertEqual(issued["text_fallback"], reissued["text_fallback"])
        parsed = driver.parse_gate(reissued, probe.GATE_REPLIES["intake_questions_pending"])
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(parsed["answers"], {"1": "A", "2": "B"})

    def test_a_gate_without_numbered_questions_points_with_its_decision_prompt(self):
        driver, action = self.at("source_review_pending", "review-page", publish=True)
        lines = action["text"].rstrip("\n").splitlines()
        self.assertEqual(lines[0], f"Your source review is on the dashboard: {URL}")
        self.assertEqual(lines[1:], ["Reply `continue` to draft on these sources, or `cancel` to stop."])
        self.assertIn("Source review —", action["text_fallback"])

    def test_a_declined_dashboard_brings_the_full_questions_back(self):
        driver, _ = self.at("intake_questions_pending", "intake-declined", publish=True)
        task.run_dashboard(
            namespace(workdir=str(driver.work_dir), url=None, unavailable="Artifact errored")
        )
        action = driver.next()
        self.assertEqual(action["text"], gates.render(driver.work_dir, driver.state(), "intake"))
        self.assertNotIn(URL, action["text"])

    def test_the_gate_card_carries_a_long_question_whole(self):
        """The chat points at the card, so the card may not cut what the chat no longer prints."""
        driver, _ = self.at("intake_questions_pending", "intake-long", publish=True)
        question = "Which retention period applies? " + "x" * 468
        self.assertEqual(len(question), 500)
        document = state_io.read_json(driver.work_dir / gates.QUESTIONS_PATH)
        document["must_answer"][0]["question"] = question
        document["must_answer"][0]["options"][0]["description"] = question
        state_io.write_json_atomic(driver.work_dir / gates.QUESTIONS_PATH, document)
        row = machine.dashboard_patch(driver.state())["gate"]["questions"][0]
        self.assertEqual(row["text"], question)
        self.assertEqual(row["options"][0]["detail"], question)
        self.assertNotIn(machine.DASHBOARD_TEXT_ELLIPSIS, row["text"])


class EveryAnswerKindCarriesTheWriteDbTest(unittest.TestCase):
    """D-94: the block rides on every `next` answer of a whole run, not only on some kinds."""

    KINDS = {"dispatch", "script", "gate-auq", "gate-text", "inline-llm", "terminal"}

    def test_every_kind_carries_the_patch_and_the_approval_is_written_at_once(self):
        driver = Driver(temp_root(self), "full", slug="dash-all", user_config={"dashboard": True})
        task.run_dashboard(namespace(workdir=str(driver.work_dir), url=URL, unavailable=None))
        seen: set = set()
        after_approval = None
        approved_now = False
        for _ in range(200):
            action = driver.next()
            self.assertNotIn("errors", action)
            kind = str(action.get("kind"))
            block = action.get("dashboard")
            self.assertIsNotNone(block, f"the {kind} answer lost the dashboard block")
            self.assertEqual(sorted(block), ["write_db"])
            self.assertEqual(sorted(block["write_db"]), ["collection", "doc_id", "file_path", "url"])
            block_patch = json.loads(Path(block["write_db"]["file_path"]).read_text(encoding="utf-8"))
            self.assertEqual(json.loads(json.dumps(block_patch)), block_patch)
            seen.add(kind)
            if approved_now:
                after_approval = block_patch
                approved_now = False
            if kind == "terminal":
                break
            approved_now = (
                kind == "gate-auq" and action.get("phase") == "plan_approval_pending"
            )
            driver.act(action)
        self.assertEqual(seen, self.KINDS)
        self.assertIsNotNone(after_approval, "the run never passed the plan gate")
        self.assertTrue(after_approval["plan"]["approved"], "the page still showed an open plan")
        self.assertIsNone(after_approval["gate"], "the page still said `your turn` during research")


class TimelineReadsLikeEnglishTest(unittest.TestCase):
    """D-95: every step of a real run becomes a sentence; `purpose()` tokens never reach the page."""

    def setUp(self) -> None:
        self.driver = Driver(temp_root(self), "full", slug="timeline", user_config={"dashboard": True})

    def texts(self) -> list[str]:
        return [row["text"] for row in machine.dashboard_patch(self.driver.state())["timeline"]]

    def test_the_early_steps_name_what_happened(self):
        self.driver.run_until("plan_approval_pending")
        self.assertEqual(
            self.texts(),
            [
                "Legal databases checked",
                "Facts & assumptions analyst: finished",
                "You answered: Your intake answers",
                "Plan drafted",
                "Source portals checked",  # D-147: `mf sources preflight` runs inside `planning`
                "Waiting for you: Plan approval",
            ],
        )
        self.assertEqual(
            [row["state"] for row in machine.dashboard_patch(self.driver.state())["timeline"]][-1],
            "waiting",
        )

    def test_research_and_review_name_the_agents(self):
        self.driver.run_until("drafting")
        texts = self.texts()
        self.assertIn(
            "Researcher (statutes), Researcher (case law), Researcher (doctrine): finished", texts
        )
        self.assertIn("Coverage reviewer: finished", texts)
        self.assertIn("Currency checker: finished", texts)
        self.assertIn("Source pack frozen", texts)

    def test_the_end_of_the_run_reads_as_prose(self):
        self.driver.run_to_end()
        texts = self.texts()
        # D-117: `docx render` validates its own output, so there is no separate «DOCX validated».
        for sentence in ("Draft checked", "Memo exported", "Deliverable ready", "Done"):
            self.assertIn(sentence, texts)
        self.assertIn("Logic reviewer, Form reviewer, Citation auditor +1 more: finished", texts)

    def test_no_machine_token_survives_into_the_timeline(self):
        self.driver.run_to_end()
        for text in self.texts():
            with self.subTest(text=text):
                for token in ("script:", "dispatch:", "inline:", "gate-", "_pending", "--workdir"):
                    self.assertNotIn(token, text)

    def test_a_failed_slot_says_it_is_being_retried(self):
        action = self.driver.run_until("research")
        self.driver.act(action, fail_slots=("statutes",))
        self.assertIn(
            "Researcher (statutes), Researcher (case law), Researcher (doctrine): failed, retrying",
            self.texts(),
        )

    def test_an_unknown_step_degrades_to_the_phase_label(self):
        state = fixture_state()
        state["steps"] = [
            {
                "step_id": "s-900",
                "kind": "script",
                "phase": "drafting",
                "issued_at": "2026-09-08T12:09:00.000Z",
                "attempt": 1,
                "status": "ok",
                "command": ["/plugin/scripts/mf", "brand", "new", "--workdir", "W"],
            }
        ]
        self.assertEqual(
            machine.dashboard_patch(state)["timeline"][0]["text"], "Writing the memo step"
        )

    def test_every_script_and_agent_the_machine_can_issue_has_a_label(self):
        """The tables are the whole vocabulary: a new command or agent must be added to them."""
        for key, pair in machine.DASHBOARD_SCRIPT_LABELS.items():
            with self.subTest(key=key):
                self.assertEqual(len(pair), 2)
                self.assertTrue(all(text and text[0].isupper() for text in pair))
        for name, label in machine.DASHBOARD_AGENT_LABELS.items():
            with self.subTest(agent=name):
                self.assertTrue((PLUGIN_ROOT / "agents" / f"{name}.md").is_file(), name)
                self.assertTrue(label[0].isupper())


class GateQuestionsReachThePageTest(unittest.TestCase):
    """D-96: the open gate is repeated read-only on the page; the answer still comes from chat."""

    def setUp(self) -> None:
        self.driver = Driver(temp_root(self), "full", slug="gate-q", user_config={"dashboard": True})

    def gate(self) -> dict:
        return machine.dashboard_patch(self.driver.state())["gate"]

    def test_the_intake_gate_carries_the_numbered_questions_of_the_chat(self):
        action = self.driver.run_until("intake_questions_pending")
        gate = self.gate()
        printed = gates.printed_questions(self.driver.work_dir, self.driver.state(), "intake")
        self.assertEqual(len(gate["questions"]), len(printed))
        for index, (row, source) in enumerate(zip(gate["questions"], printed), start=1):
            with self.subTest(n=index):
                self.assertEqual(row["n"], index)
                self.assertEqual(row["text"], source["question"])
                self.assertIn(f"{index}. {source['question']}", action["text"])
                self.assertEqual(
                    [option["key"] for option in row["options"]],
                    list(gates.OPTION_LETTERS[: len(source["options"])]),
                )
                self.assertEqual(
                    [option["label"] for option in row["options"]],
                    [option["label"] for option in source["options"]],
                )
        self.assertEqual(
            gate["answer_hint"], "Reply in chat: 1A 2C 3: your text · proceed · cancel"
        )

    def test_the_plan_gate_carries_the_auq_questions(self):
        action = self.driver.run_until("plan_approval_pending")
        gate = self.gate()
        self.assertEqual(
            [row["header"] for row in gate["questions"]],
            [question["header"] for question in action["questions"]],
        )
        first = gate["questions"][0]
        self.assertEqual(first["text"], "Approve this research plan?")
        self.assertEqual([option["label"] for option in first["options"]], ["Approve", "Edit", "Cancel"])
        self.assertEqual([option["key"] for option in first["options"]], ["", "", ""])
        self.assertTrue(first["options"][0]["detail"])
        self.assertIn("approve [brief|full]", gate["answer_hint"])

    def test_a_gate_without_numbered_questions_still_says_how_to_answer(self):
        self.driver.run_until("source_review_pending")
        gate = self.gate()
        self.assertEqual(gate["questions"], [])
        self.assertEqual(gate["answer_hint"], "Reply in chat: continue · cancel")

    def test_the_gate_disappears_once_it_is_answered(self):
        self.driver.act(self.driver.run_until("intake_questions_pending"))
        self.driver.next()
        self.assertIsNone(self.gate())

    def test_the_questions_are_capped_and_serialisable(self):
        self.driver.run_until("intake_questions_pending")
        gate = self.gate()
        self.assertLessEqual(len(gate["questions"]), machine.DASHBOARD_GATE_QUESTIONS)
        for row in gate["questions"]:
            self.assertLessEqual(len(row["options"]), machine.DASHBOARD_GATE_OPTIONS)
        self.assertEqual(json.loads(json.dumps(gate)), gate)

    def test_an_unreadable_questions_file_leaves_the_gate_answerable(self):
        self.driver.run_until("intake_questions_pending")
        (self.driver.work_dir / gates.QUESTIONS_PATH).write_text("{ not json", encoding="utf-8")
        gate = self.gate()
        self.assertEqual(gate["questions"], [])
        self.assertTrue(gate["answer_hint"])


class UnreadableGateSourceIsNotProjectedTest(unittest.TestCase):
    """D-97: a gate whose source file cannot be read publishes `questions: []`, never a guess.

    `gates._read_json` turns a missing or corrupt file into `{}`, so before D-97 a plan gate with an
    unreadable `plan.json` still built `Plan`/`Mode` from the empty document and an unreadable
    `mcp-probe.json` announced every legal database as missing. Every case here must also leave
    `next` answering the gate — a broken source file never fails the run.
    """

    def at(self, phase: str, slug: str) -> Driver:
        driver = Driver(temp_root(self), "full", slug=slug, user_config={"dashboard": True})
        task.run_dashboard(namespace(workdir=str(driver.work_dir), url=URL, unavailable=None))
        driver.run_until(phase)
        return driver

    def gate_of_next(self, driver: Driver) -> dict:
        """The gate the page gets on the answer `next` still gives for the open gate."""
        action = driver.next()
        self.assertNotIn("errors", action)
        self.assertTrue(action.get("kind"))
        return json.loads(Path(action["dashboard"]["write_db"]["file_path"]).read_text(encoding="utf-8"))[
            "gate"
        ]

    def assertNoQuestions(self, driver: Driver) -> None:
        gate = self.gate_of_next(driver)
        self.assertEqual(gate["questions"], [])
        self.assertTrue(gate["answer_hint"])
        self.assertEqual(gate["hint"], machine.DASHBOARD_GATE_HINT)

    # -- gate 4: the plan ---------------------------------------------------
    def test_the_plan_gate_asks_nothing_when_the_plan_is_missing(self):
        driver = self.at("plan_approval_pending", "no-plan")
        (driver.work_dir / gates.PLAN_PATH).unlink()
        self.assertNoQuestions(driver)

    def test_the_plan_gate_asks_nothing_when_the_plan_is_not_json(self):
        driver = self.at("plan_approval_pending", "bad-json-plan")
        (driver.work_dir / gates.PLAN_PATH).write_text("{ not json", encoding="utf-8")
        self.assertNoQuestions(driver)

    def test_the_plan_gate_asks_nothing_when_the_plan_is_structurally_invalid(self):
        # The projection is asserted on the patch itself: `gates.build_auq` and
        # `gates.render_plan_digest` still raise `TypeError` on a plan whose `issues` or
        # `jurisdictions` is not a list, which `next` rebuilds for the gate text — a `gates.py`
        # concern of its own, outside the dashboard projection D-97 fixes.
        driver = self.at("plan_approval_pending", "invalid-plan")
        for name, plan in malformed_plans().items():
            with self.subTest(name):
                state_io.write_json_atomic(driver.work_dir / gates.PLAN_PATH, plan)
                gate = machine.dashboard_patch(driver.state())["gate"]
                self.assertEqual(gate["questions"], [])
                self.assertTrue(gate["answer_hint"])

    def test_next_still_answers_the_gate_of_a_half_written_plan(self):
        driver = self.at("plan_approval_pending", "half-plan")
        state_io.write_json_atomic(
            driver.work_dir / gates.PLAN_PATH, malformed_plans()["half-written plan"]
        )
        self.assertNoQuestions(driver)

    def test_the_plan_gate_asks_nothing_when_the_probe_cannot_be_read(self):
        driver = self.at("plan_approval_pending", "bad-probe")
        probe_file = driver.work_dir / gates.MCP_PROBE_PATH
        probe_file.write_text("{ not json", encoding="utf-8")
        self.assertNoQuestions(driver)
        probe_file.unlink()
        self.assertNoQuestions(driver)

    def test_an_intact_plan_gate_still_carries_its_questions(self):
        driver = self.at("plan_approval_pending", "good-plan")
        gate = self.gate_of_next(driver)
        self.assertEqual([row["header"] for row in gate["questions"]][:2], ["Plan", "Mode"])

    # -- gate 2: intake -----------------------------------------------------
    def test_the_intake_gate_asks_nothing_when_its_questions_file_is_gone(self):
        driver = self.at("intake_questions_pending", "no-questions")
        (driver.work_dir / gates.QUESTIONS_PATH).unlink()
        self.assertNoQuestions(driver)

    def test_the_intake_gate_asks_nothing_when_its_questions_file_is_corrupt(self):
        driver = self.at("intake_questions_pending", "bad-questions")
        (driver.work_dir / gates.QUESTIONS_PATH).write_text("[]", encoding="utf-8")
        self.assertNoQuestions(driver)
        (driver.work_dir / gates.QUESTIONS_PATH).write_text("{ not json", encoding="utf-8")
        self.assertNoQuestions(driver)

    def test_the_intake_gate_does_not_read_the_probe(self):
        """The probe belongs to gate 4; a corrupt one must not hide the intake questions."""
        driver = self.at("intake_questions_pending", "intake-probe")
        (driver.work_dir / gates.MCP_PROBE_PATH).write_text("{ not json", encoding="utf-8")
        self.assertTrue(self.gate_of_next(driver)["questions"])


def frozen_pack(count: int) -> dict:
    """A frozen pack of `count` entries, in the shape `mf sources pack --freeze` publishes."""
    return {
        "schema_version": 2,
        "frozen_at": "2026-09-08T12:30:00.000Z",
        "snapshot": [],
        "entries": [
            {
                "source_id": f"source-{index:03d}",
                "layer": "statutes",
                "title": f"Source {index} " + "long title " * 20,
                "citation_form": f"Cite {index}",
                "url": "https://example.test/" + "x" * 200,
                "tier": sources.TIERS[index % len(sources.TIERS)],
                "currency_status": "current",
                "verification_us": "n/a",
                "pack": {},
            }
            for index in range(count)
        ],
    }


class RunHistoryReachesThePageTest(unittest.TestCase):
    """D-98: the page keeps the run's history — one patch section per tab, none of them a guess.

    One dry-run `full` run is driven to the end and every `next` answer is kept, so each section can
    be asserted twice: `null` while the run has not written the artifact behind it, and populated
    from that artifact once it has.
    """

    @classmethod
    def setUpClass(cls) -> None:
        holder = tempfile.TemporaryDirectory(prefix="mf-test-")
        cls.addClassCleanup(holder.cleanup)
        cls.driver = Driver(
            Path(holder.name), "full", slug="history", user_config={"dashboard": True}
        )
        task.run_dashboard(namespace(workdir=str(cls.driver.work_dir), url=URL, unavailable=None))
        cls.patches = []
        for _ in range(200):
            action = cls.driver.next()
            if action.get("errors"):
                raise AssertionError(f"next failed: {action['errors']}")
            cls.patches.append(
                (
                    str(action.get("phase") or ""),
                    json.loads(
                        Path(action["dashboard"]["write_db"]["file_path"]).read_text(encoding="utf-8")
                    ),
                )
            )
            if action.get("kind") == "terminal":
                break
            cls.driver.act(action)
        cls.final = cls.patches[-1][1]

    def at(self, phase: str) -> dict:
        """The first patch the run published while it was in `phase`."""
        for name, patch in self.patches:
            if name == phase:
                return patch
        raise AssertionError(f"phase {phase} never reached")

    def test_the_run_ends_with_every_section_filled(self):
        for key in ("intake", "plan", "sources", "reviews", "memo"):
            with self.subTest(key):
                self.assertTrue(self.final[key], key)

    def test_the_intake_section_waits_for_the_answer_of_gate_2(self):
        self.assertIsNone(self.at("intake_questions_pending")["intake"])
        self.assertTrue(self.at("planning")["intake"]["questions"])

    def test_the_intake_answers_are_the_ones_in_user_facts_md(self):
        """The numbering on the page is the numbering `gates.user_facts_markdown` wrote."""
        facts = (self.driver.work_dir / gates.USER_FACTS_PATH).read_text(encoding="utf-8-sig")
        printed = gates.printed_questions(self.driver.work_dir, self.driver.state(), "intake")
        section = self.final["intake"]
        self.assertEqual(
            [row["n"] for row in section["questions"]], list(range(1, len(printed) + 1))
        )
        for row, source in zip(section["questions"], printed):
            with self.subTest(n=row["n"]):
                self.assertEqual(row["text"], source["question"])
                self.assertTrue(row["answered"])
                self.assertIn(row["answer_key"], gates.OPTION_LETTERS)
                self.assertIn("**" + row["text"] + "** — " + row["answer_label"], facts)
        self.assertTrue(section["assumptions"])
        for row in section["assumptions"]:
            self.assertIn(" — assuming: ", row["text"])

    def test_the_plan_decision_is_the_answer_of_gate_4(self):
        self.assertIsNone(self.at("plan_approval_pending")["plan"]["decision"])
        decision = self.at("research")["plan"]["decision"]
        self.assertEqual(decision["action"], "approve")
        self.assertEqual(decision["mode"], "full")
        self.assertTrue(decision["at"])
        self.assertNotIn("edit_text", decision)

    def test_the_sources_section_arrives_with_the_freeze(self):
        self.assertIsNone(self.at("source_pack")["sources"])
        section = self.at("source_review_pending")["sources"]
        self.assertTrue(section["frozen_at"])
        self.assertEqual(sorted(section["counts"]), sorted(sources.TIERS))
        self.assertEqual(sum(section["counts"].values()), len(section["items"]))
        for row in section["items"]:
            with self.subTest(row["id"]):
                self.assertTrue(row["id"] and row["title"] and row["tier"])
                self.assertNotIn("http", json.dumps(row))

    def test_the_reviews_section_arrives_with_the_first_aggregate(self):
        self.assertIsNone(self.at("drafting")["reviews"])
        records = self.driver.state()["iterations"]
        rows = self.final["reviews"]
        self.assertEqual(
            [row["iteration"] for row in rows], [record["iteration"] for record in records]
        )
        for row, record in zip(rows, records):
            with self.subTest(iteration=row["iteration"]):
                self.assertEqual(
                    row["blockers"], record["substance_blockers"] + record["form_blockers"]
                )
                self.assertEqual(row["failed_reviewers"], [])
                self.assertEqual(row["draft_version"], 1)
                self.assertEqual(row["summary"], machine.DASHBOARD_REVIEW_SUMMARY[row["status"]])

    def test_the_memo_section_waits_for_the_deliverable_itself(self):
        self.assertIsNone(self.at("client_readiness")["memo"])
        memo = self.final["memo"]
        self.assertEqual(memo["deliverable"], self.final["deliverable"])
        self.assertTrue(memo["deliverable"])
        self.assertEqual(memo["summary_path"], "summary.md")
        self.assertEqual(memo["final_status"], self.driver.state()["final_status"])

    def test_the_memo_section_names_the_published_folder(self):
        """D-109: `published_to` republishes `progress.published_to` — empty when nothing was copied."""
        self.assertEqual(
            self.final["memo"]["published_to"], machine.published_to(self.driver.state())
        )
        state = dict(self.driver.state())
        state["progress"] = dict(state["progress"], published_to="/mnt/user-data/outputs/memoforge/x")
        self.assertEqual(
            machine.dashboard_patch(state)["memo"]["published_to"],
            "/mnt/user-data/outputs/memoforge/x",
        )

    def test_the_document_the_page_subscribes_to_stays_small(self):
        for phase, patch in self.patches:
            with self.subTest(phase):
                self.assertLess(len(json.dumps(patch, ensure_ascii=False).encode("utf-8")), 60000)


class HistorySectionsAreCappedAndDerivedTest(unittest.TestCase):
    """D-98: the caps and the derivations of the history sections, away from the dry-run fixture."""

    def state_with_pack(self, document: object) -> dict:
        root = temp_root(self)
        (root / "research").mkdir(parents=True, exist_ok=True)
        if isinstance(document, str):
            (root / sources.PACK_PATH).write_text(document, encoding="utf-8")
        else:
            state_io.write_json_atomic(root / sources.PACK_PATH, document)
        state = fixture_state()
        state["work_dir"] = str(root)
        return state

    def test_the_source_list_is_capped_and_the_counts_are_not(self):
        section = machine.dashboard_patch(self.state_with_pack(frozen_pack(60)))["sources"]
        self.assertEqual(len(section["items"]), machine.DASHBOARD_SOURCE_ITEMS)
        self.assertEqual(sum(section["counts"].values()), 60)
        for row in section["items"]:
            self.assertLessEqual(len(row["title"]), machine.DASHBOARD_TEXT_CHARS)

    def test_a_pack_that_cannot_be_read_costs_the_section_and_nothing_else(self):
        patch = machine.dashboard_patch(self.state_with_pack("{ not json"))
        self.assertIsNone(patch["sources"])
        self.assertEqual(patch["phase"], "research")

    def test_the_three_review_outcomes_come_from_the_stored_record(self):
        state = fixture_state()
        state["draft_versions"] = [{"version": 2, "sha256": "a" * 64}]
        state["iterations"] = [
            {"iteration": 1, "draft_sha": "a" * 64, "failed_reviewers": []},
            {"iteration": 2, "substance_blockers": 2, "form_blockers": 1, "failed_reviewers": []},
            {"iteration": 3, "failed_reviewers": ["citations", "logic"]},
        ]
        rows = machine.dashboard_patch(state)["reviews"]
        self.assertEqual([row["status"] for row in rows], ["clean", "blockers", "incomplete"])
        self.assertEqual([row["blockers"] for row in rows], [0, 3, 0])
        self.assertEqual([row["draft_version"] for row in rows], [2, None, None])
        self.assertEqual(rows[2]["failed_reviewers"], ["Citation auditor", "Logic reviewer"])
        self.assertEqual(
            [row["summary"] for row in rows],
            [machine.DASHBOARD_REVIEW_SUMMARY[row["status"]] for row in rows],
        )

    def test_an_edit_request_of_gate_4_is_published_capped(self):
        state = fixture_state()
        state["plan_approval"] = {
            "iterations": [
                {"action": "approve", "answers": {"Plan": "Approve", "Mode": "Brief"}, "at": "t1"},
                {
                    "action": "edit",
                    "answers": {"Plan": "Edit", "edit_text": "add " + "detail " * 60},
                    "at": "t2",
                },
            ]
        }
        decision = machine._plan_decision(state)  # noqa: SLF001 - the projection under test
        self.assertEqual((decision["action"], decision["at"]), ("edit", "t2"))
        self.assertLessEqual(len(decision["edit_text"]), machine.DASHBOARD_LONG_TEXT_CHARS)


class CorruptPlanApprovalCostsOnlyTheDecisionTest(unittest.TestCase):
    """D-101: `plan.decision` is built outside `_dashboard_history`, so it guards itself.

    `plan_approval.iterations = [{"answers": 42}]` used to raise `AttributeError` in
    `_plan_decision`; the exception left `_dashboard_plan` for `dashboard_patch` and cost the whole
    `next` answer. A record the run did not write in the shape `gates.commit` writes it now costs
    exactly one line of the page.
    """

    CORRUPT: dict[str, object] = {
        "plan_approval is not an object": 42,
        "iterations is not a list": {"iterations": 42},
        "the record is not an object": {"iterations": [42]},
        "answers is a number": {"iterations": [{"answers": 42}]},
        "answers is a string": {"iterations": [{"action": "approve", "answers": "Mode=full"}]},
        "answers is a list": {"iterations": [{"answers": [["Mode", "full"]]}]},
    }

    def setUp(self) -> None:
        self.root = temp_root(self)
        state_io.write_json_atomic(self.root / gates.PLAN_PATH, probe.fixture_plan())

    def patch_for(self, approval: object) -> dict:
        state = fixture_state()
        state["work_dir"] = str(self.root)
        state["plan_approval"] = approval
        return machine.dashboard_patch(state)

    def test_a_record_of_the_wrong_shape_costs_the_decision_and_nothing_else(self):
        for name, approval in self.CORRUPT.items():
            with self.subTest(name):
                patch = self.patch_for(approval)
                self.assertIsNone(patch["plan"]["decision"])
                self.assertFalse(patch["plan"]["approved"])
                self.assertEqual([issue["id"] for issue in patch["plan"]["issues"]], ["i1"])
                self.assertEqual(patch["phase"], "research")
                self.assertEqual(json.loads(json.dumps(patch)), patch)

    def test_the_same_record_read_on_its_own_is_none(self):
        for name, approval in self.CORRUPT.items():
            with self.subTest(name):
                state = dict(fixture_state(), plan_approval=approval)
                self.assertIsNone(machine._plan_decision(state))  # noqa: SLF001 - under test

    def test_a_record_missing_its_fields_still_publishes_what_it_has(self):
        patch = self.patch_for({"status": "approved", "iterations": [{"action": "approve"}]})
        self.assertEqual(patch["plan"]["decision"], {"action": "approve", "mode": "full", "at": ""})
        self.assertTrue(patch["plan"]["approved"])

    def test_an_intact_record_is_still_published(self):
        patch = self.patch_for(
            {
                "status": "approved",
                "iterations": [{"action": "approve", "answers": {"Mode": "Brief"}, "at": "t1"}],
            }
        )
        self.assertEqual(
            patch["plan"]["decision"], {"action": "approve", "mode": "brief", "at": "t1"}
        )

    def test_the_last_record_decides_and_a_corrupt_last_one_is_null(self):
        """D-102: a malformed last record is `decision: null`, never the record before it."""
        valid = {"action": "approve", "answers": {"Mode": "Brief"}, "at": "t1"}
        decided = {"action": "approve", "mode": "brief", "at": "t1"}
        histories: tuple[tuple[str, list, dict | None], ...] = (
            ("the corrupt record is the last one", [valid, 42], None),
            ("the corrupt record is the earlier one", [42, valid], decided),
            ("the last record is an object of the wrong shape", [valid, {"answers": 42}], None),
        )
        for name, iterations, expected in histories:
            with self.subTest(name):
                patch = self.patch_for({"status": "approved", "iterations": iterations})
                self.assertEqual(patch["plan"]["decision"], expected)
                self.assertEqual([issue["id"] for issue in patch["plan"]["issues"]], ["i1"])


class EveryFreeStringOfTheHistoryIsCappedTest(unittest.TestCase):
    """D-101: 70 000 characters anywhere in the history reach the page cut to a cap.

    No schema behind the sections bounds a question, an answer, an assumption or an issue title, so
    the ceiling is the page's own. D-103 raised that ceiling for what the user reads
    (`DASHBOARD_LONG_TEXT_CHARS`) and kept the short one where the string is machine-written, which
    is the arithmetic behind `CEILING`: 25 plan issues × 2 long strings + 12 intake rows × 2 +
    12 assumptions, ≈ 180 KB in the worst case these tests build. The fixture run publishes ~4 KB.
    """

    LONG = "word " * 14000
    """70 000 characters of free text — a pasted document in an answer box, not a question."""

    CEILING = 200000
    """Worst case of the sections below, with every free string blown past every cap."""

    def assert_capped(self, value: object, limit: int = machine.DASHBOARD_LONG_TEXT_CHARS) -> None:
        text = str(value)
        self.assertLessEqual(len(text), limit, text[:60])

    def assert_small(self, patch: dict) -> None:
        self.assertLess(len(json.dumps(patch, ensure_ascii=False).encode("utf-8")), self.CEILING)

    def long_question(self, impact: str) -> dict:
        return {
            "question": self.LONG,
            "header": "Scope",
            "options": [{"label": self.LONG, "description": self.LONG}],
            "impact": impact,
            "default": self.LONG,
            "confidence": "medium",
        }

    def intake_state(self) -> dict:
        """A closed intake gate whose questions, options, answers and defaults are all 70 000 long."""
        root = temp_root(self)
        (root / "intake").mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            root / gates.QUESTIONS_PATH,
            {
                "must_answer": [self.long_question("high") for _ in range(3)],
                "optional": [self.long_question("low")],
                "default_assumptions_if_skipped": [],
            },
        )
        state = fixture_state()
        state["work_dir"] = str(root)
        state["steps"].append(
            {
                "step_id": "s-003",
                "kind": machine.KIND_GATE_TEXT,
                "phase": gates.PHASE_BY_GATE["intake"],
                "issued_at": "2026-09-08T12:10:00.000Z",
                "attempt": 1,
                "status": "ok",
                "result_ref": {"result": {"answers": {"1": "A", "2": self.LONG}}},
            }
        )
        return state

    def test_the_intake_section_caps_questions_answers_and_assumptions(self):
        patch = machine.dashboard_patch(self.intake_state())
        section = patch["intake"]
        self.assertEqual(len(section["questions"]), 3)
        self.assertEqual([row["answered"] for row in section["questions"]], [True, True, False])
        self.assertEqual([row["answer_key"] for row in section["questions"]], ["A", "", ""])
        for row in section["questions"]:
            self.assert_capped(row["text"])
            self.assert_capped(row["answer_label"])
            self.assertTrue(row["answer_label"].endswith(machine.DASHBOARD_TEXT_ELLIPSIS))
        self.assertTrue(section["assumptions"])
        for row in section["assumptions"]:
            self.assert_capped(row["text"])
        self.assert_small(patch)

    def test_the_plan_section_caps_the_issue_title_and_question(self):
        root = temp_root(self)
        plan = probe.fixture_plan()
        plan["issues"] = [
            dict(plan["issues"][0], issue_id=f"i{index}", title=self.LONG, question=self.LONG)
            for index in range(gates.PLAN_DIGEST_MAX_ISSUES)
        ]
        state_io.write_json_atomic(root / gates.PLAN_PATH, plan)
        state = fixture_state()
        state["work_dir"] = str(root)
        patch = machine.dashboard_patch(state)
        self.assertEqual(len(patch["plan"]["issues"]), gates.PLAN_DIGEST_MAX_ISSUES)
        for issue in patch["plan"]["issues"]:
            self.assert_capped(issue["title"])
            self.assert_capped(issue["question"])
        self.assert_small(patch)

    def test_the_plan_section_caps_the_jurisdiction_codes(self):
        root = temp_root(self)
        plan = probe.fixture_plan()
        plan["jurisdictions"] = [self.LONG] * 100
        plan["issues"] = [
            dict(
                plan["issues"][0],
                title=self.LONG,
                question=self.LONG,
                jurisdictions=[self.LONG] * 100,
            )
        ]
        state_io.write_json_atomic(root / gates.PLAN_PATH, plan)
        state = fixture_state()
        state["work_dir"] = str(root)
        patch = machine.dashboard_patch(state)
        section = patch["plan"]
        lists = [section["jurisdictions"]] + [issue["jurisdictions"] for issue in section["issues"]]
        for codes in lists:
            self.assertEqual(len(codes), machine.DASHBOARD_JURISDICTION_CODES)
            for code in codes:
                self.assertLessEqual(len(code), machine.DASHBOARD_CODE_CHARS)
                self.assertTrue(code.endswith(machine.DASHBOARD_TEXT_ELLIPSIS), code)
        self.assertLess(len(json.dumps(section, ensure_ascii=False).encode("utf-8")), 8000)
        self.assert_small(patch)

    def test_a_jurisdiction_list_drops_what_is_not_a_string(self):
        codes = machine._dashboard_codes(["EU", 42, None, {"code": "FR"}, "US"])  # noqa: SLF001
        self.assertEqual(codes, ["EU", "US"])
        self.assertEqual(machine._dashboard_codes(42), [])  # noqa: SLF001 - not a list at all
        self.assertEqual(machine._dashboard_text("EU", machine.DASHBOARD_CODE_CHARS), "EU")  # noqa: SLF001

    def test_the_plan_decision_caps_the_edit_request(self):
        state = fixture_state()
        state["plan_approval"] = {
            "iterations": [{"action": "edit", "answers": {"edit_text": self.LONG}, "at": "t1"}]
        }
        decision = machine._plan_decision(state)  # noqa: SLF001 - the projection under test
        self.assert_capped(decision["edit_text"])
        self.assert_small(machine.dashboard_patch(state))

    def test_the_sources_section_caps_the_title(self):
        root = temp_root(self)
        (root / "research").mkdir(parents=True, exist_ok=True)
        pack = frozen_pack(machine.DASHBOARD_SOURCE_ITEMS + 5)
        for entry in pack["entries"]:
            entry["title"] = self.LONG
        state_io.write_json_atomic(root / sources.PACK_PATH, pack)
        state = fixture_state()
        state["work_dir"] = str(root)
        patch = machine.dashboard_patch(state)
        self.assertEqual(len(patch["sources"]["items"]), machine.DASHBOARD_SOURCE_ITEMS)
        for row in patch["sources"]["items"]:
            self.assert_capped(row["title"], machine.DASHBOARD_TEXT_CHARS)
        self.assert_small(patch)

    def test_the_reviews_and_memo_sections_publish_capped_strings_only(self):
        state = fixture_state()
        state["final_docx_path"] = "deliverable.docx"
        state["iterations"] = [{"iteration": index, "failed_reviewers": []} for index in range(1, 4)]
        patch = machine.dashboard_patch(state)
        for row in patch["reviews"]:
            self.assert_capped(row["summary"])
        for value in machine.DASHBOARD_REVIEW_SUMMARY.values():
            self.assert_capped(value)
        self.assert_capped(patch["memo"]["summary_path"], machine.DASHBOARD_TEXT_CHARS)
        self.assert_small(patch)

    def test_a_string_that_fits_is_published_whole_and_a_longer_one_is_marked(self):
        fits = "a" * machine.DASHBOARD_TEXT_CHARS
        cut = machine._dashboard_text("b " * machine.DASHBOARD_TEXT_CHARS)  # noqa: SLF001
        self.assertEqual(machine._dashboard_text(" ".join(["one", "two"])), "one two")  # noqa: SLF001
        self.assertEqual(machine._dashboard_text(fits), fits)  # noqa: SLF001
        self.assertEqual(len(cut), machine.DASHBOARD_TEXT_CHARS)
        self.assertTrue(cut.endswith(machine.DASHBOARD_TEXT_ELLIPSIS))
        self.assertEqual(machine._dashboard_text(None), "")  # noqa: SLF001


class DashboardPatchCarriesNoDiagnosticsTest(unittest.TestCase):
    """D-88: banner content on the page is a banner id plus a static label — nothing else."""

    WORK_DIRS = (
        "C:\\Users\\lawyer\\Documents\\memoforge\\memo-fixture",
        "/home/lawyer/memoforge/memo-fixture",
    )
    REASON = "Artifact tool failed at ~/.claude/plugins/memoforge/lib/dashboard.html"
    LEAKS = (r"[A-Za-z]:[\\/]", r"~[\\/]", r"(?<!\w)/[\w.-]+/")

    def patch_for(self, work_dir: str) -> dict:
        """The patch of a finalized run whose two worst banners embed a path and an error string."""
        state = fixture_state()
        state["work_dir"] = work_dir
        state["final_docx_path"] = "deliverable.docx"
        state["fallback_banners"] = [
            fallbacks.banner("output_folder_write_failed", work_dir=work_dir),
            fallbacks.banner("dashboard_unavailable", reason=self.REASON),
        ]
        return machine.dashboard_patch(state)

    def test_the_two_diagnostic_banners_are_published_as_id_and_label(self):
        patch = self.patch_for(self.WORK_DIRS[0])
        self.assertEqual(
            patch["banners"],
            [
                {
                    "id": "output_folder_unavailable",
                    "text": fallbacks.dashboard_label("output_folder_unavailable"),
                },
                {
                    "id": "dashboard_unavailable",
                    "text": fallbacks.dashboard_label("dashboard_unavailable"),
                },
            ],
        )

    def test_the_deliverable_is_the_only_path_in_the_patch(self):
        """D-98: `memo.deliverable` repeats that one path, and the Memo section adds no other."""
        for work_dir in self.WORK_DIRS:
            with self.subTest(work_dir=work_dir):
                patch = self.patch_for(work_dir)
                self.assertIn("memo-fixture", patch["deliverable"])
                self.assertEqual(patch["memo"]["deliverable"], patch["deliverable"])
                patch["memo"] = {key: value for key, value in patch["memo"].items()
                                 if key != "deliverable"}
                rest = json.dumps(
                    {key: value for key, value in patch.items() if key != "deliverable"},
                    ensure_ascii=False,
                )
                self.assertNotIn(work_dir, rest)
                self.assertNotIn("lawyer", rest)
                self.assertNotIn(self.REASON, rest)
                for pattern in self.LEAKS:
                    self.assertNotRegex(rest, pattern)

    def test_every_banner_id_has_a_parameter_free_label(self):
        for row in fallbacks.FALLBACKS:
            if row["banner_id"] is None:
                continue
            with self.subTest(banner=row["banner_id"]):
                label = fallbacks.dashboard_label(row["banner_id"])
                self.assertTrue(label)
                self.assertNotRegex(label, r"[{}]")
                if row["banner_params"]:
                    self.assertIn(row["banner_id"], fallbacks.DASHBOARD_LABELS)
                    self.assertNotEqual(label, row["banner_text"])

    def test_an_unknown_banner_id_never_reaches_the_page(self):
        state = fixture_state()
        state["fallback_banners"] = [{"banner_id": "made_up", "text": "C:\\secret\\note.txt"}]
        self.assertEqual(machine.dashboard_patch(state)["banners"], [])


class TaskDashboardCommandTest(unittest.TestCase):
    """`mf task dashboard` is the only writer of `progress.artifact_url` (§7.5)."""

    def setUp(self) -> None:
        self.driver = Driver(temp_root(self), "brief", slug="cli", user_config={"dashboard": True})

    def _events(self, name: str) -> list[dict]:
        return [row for row in events.read_events(self.driver.work_dir) if row["event"] == name]

    def test_url_is_stored_in_progress_and_logged(self):
        result = task.run_dashboard(
            namespace(workdir=str(self.driver.work_dir), url=URL, unavailable=None)
        )
        self.assertEqual(result["artifact_url"], URL)
        self.assertEqual(self.driver.state()["progress"]["artifact_url"], URL)
        logged = self._events("dashboard_published")
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["data"]["url"], URL)

    def test_the_url_survives_the_progress_recomputation_of_next(self):
        task.run_dashboard(namespace(workdir=str(self.driver.work_dir), url=URL, unavailable=None))
        self.driver.act(self.driver.next())
        self.assertEqual(self.driver.state()["progress"]["artifact_url"], URL)

    def test_unavailable_records_the_banner_and_the_event(self):
        result = task.run_dashboard(
            namespace(workdir=str(self.driver.work_dir), url=None, unavailable="Artifact errored")
        )
        self.assertIsNone(result["artifact_url"])
        banners = self.driver.state()["fallback_banners"]
        self.assertEqual([row["banner_id"] for row in banners], [fallbacks.DASHBOARD_UNAVAILABLE])
        self.assertIn("Artifact errored", banners[0]["text"])
        logged = self._events("dashboard_unavailable")
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["data"]["reason"], "Artifact errored")

    def test_exactly_one_of_url_and_unavailable_is_required(self):
        for url, reason in ((None, None), (URL, "also a reason")):
            with self.subTest(url=url, unavailable=reason):
                result = task.run_dashboard(
                    namespace(workdir=str(self.driver.work_dir), url=url, unavailable=reason)
                )
                self.assertTrue(result["errors"])
        self.assertIsNone(self.driver.state()["progress"]["artifact_url"])

    def test_the_state_stays_valid_after_both_branches(self):
        task.run_dashboard(namespace(workdir=str(self.driver.work_dir), url=URL, unavailable=None))
        task.run_dashboard(
            namespace(workdir=str(self.driver.work_dir), url=None, unavailable="late failure")
        )
        # `write_state` validates against `schemas/state.schema.json` before replacing the file.
        self.assertEqual(state_io.read_state(self.driver.work_dir)["progress"]["artifact_url"], URL)


class DashboardPageTest(unittest.TestCase):
    """The shipped page: no build step, no network, one subscription (§7.5)."""

    def setUp(self) -> None:
        self.text = PAGE.read_text(encoding="utf-8-sig")

    def test_the_page_exists_and_is_titled(self):
        self.assertTrue(PAGE.is_file(), PAGE)
        self.assertIn("<title>", self.text)

    def test_the_page_ships_no_skeleton_tags(self):
        for tag in ("<!doctype", "<html", "<head", "<body"):
            self.assertNotIn(tag, self.text.lower(), f"the Artifact tool adds {tag} itself")

    def test_the_page_loads_nothing_from_the_network(self):
        """D-97: the page opens inside Cowork, where no external host is guaranteed to load."""
        self.assertEqual(re.findall(r"https?://[^\s\"']+", self.text), [])
        for attribute in ("<script src", "<link rel", "@import", "url("):
            self.assertNotIn(attribute, self.text.lower())

    def test_the_typography_is_a_system_stack(self):
        for stack, expected in (
            ("--sans:", '"Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif'),
            ("--mono:", 'ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace'),
        ):
            line = next(row for row in self.text.splitlines() if stack in row)
            self.assertEqual(line.strip(), f"{stack} {expected};")

    def test_the_page_subscribes_to_the_one_document_the_cli_writes(self):
        self.assertIn('claude.use("db")', self.text)
        self.assertIn(
            f'"{machine.DASHBOARD_COLLECTION}/{machine.DASHBOARD_DOC_ID}"', self.text
        )
        self.assertIn("onSnapshot", self.text)

    def test_the_page_renders_without_the_capability(self):
        self.assertIn("Waiting for data", self.text)
        self.assertIn("live updates unavailable", self.text)

    def test_the_page_renders_the_plan_and_the_waiting_notice(self):
        for token in (
            "data.plan",
            "data.gate",
            "plan-card",
            "plan-issues",
            "recommended_mode",
            "gate-card",
            "gate-questions",
            "answer_hint",
        ):
            self.assertIn(token, self.text, token)
        self.assertLess(len(self.text.splitlines()), 480, "the page stays small enough to read")

    def test_the_gate_card_opens_the_overview_and_the_plan_has_its_own_tab(self):
        """D-98: the plan card left the Overview column for the Plan tab; the gate still leads."""
        self.assertLess(self.text.index('id="gate-card"'), self.text.index('id="timeline-card"'))
        self.assertLess(self.text.index('id="panel-overview"'), self.text.index('id="panel-plan"'))
        self.assertLess(self.text.index('id="panel-plan"'), self.text.index('id="plan-card"'))

    def test_the_approval_is_marked_and_the_notice_is_bound_to_the_open_gate(self):
        self.assertIn('plan.approved ? "approved"', self.text)
        self.assertIn('plan.approved ? "tag ok"', self.text)
        self.assertIn('show("gate-card", !!gate)', self.text, "no gate, no notice")

    def test_the_timeline_is_a_rail_with_one_dot_state_per_step(self):
        for token in ("class=\"rail\"", ".ev.is-running", ".ev.is-waiting", ".ev.is-failed"):
            self.assertIn(token, self.text, token)
        self.assertIn('"ev is-" + ((row && row.state)', self.text)

    def test_the_page_uses_the_agreed_tokens_and_two_columns(self):
        for token in ("--ground:", "--surface:", "--ink:", "--muted:", "--hairline:",
                      "--accent:", "--good:", "--warn:", "--critical:"):
            self.assertIn(token, self.text, token)
        self.assertIn("max-width: 960px", self.text)
        self.assertIn("@media (min-width: 820px)", self.text)
        self.assertIn("prefers-reduced-motion", self.text)
        self.assertIn("background: var(--ground)", self.text)

    def test_the_page_is_theme_aware(self):
        self.assertIn("prefers-color-scheme: dark", self.text)
        self.assertIn(':root:not([data-theme="light"])', self.text)
        self.assertIn(':root[data-theme="dark"]', self.text)

    def test_the_page_has_one_tab_and_one_panel_per_section(self):
        """D-98: Overview, Intake, Plan, Sources, Reviews, Memo — buttons, not links."""
        for name in ("overview", "intake", "plan", "sources", "reviews", "memo"):
            with self.subTest(name):
                self.assertIn(f'id="tab-{name}"', self.text)
                self.assertIn(f'id="panel-{name}"', self.text)
                self.assertIn(f'aria-controls="panel-{name}"', self.text)
                self.assertIn(f'aria-labelledby="tab-{name}"', self.text)
        self.assertIn('role="tablist"', self.text)
        self.assertIn('role="tabpanel"', self.text)
        self.assertIn("aria-selected", self.text)
        self.assertIn(":focus-visible", self.text)

    def test_the_tablist_is_operable_from_the_keyboard(self):
        """D-101: roving tabindex — one Tab stop, arrows and Home/End walk the visible tabs."""
        self.assertIn(
            'id="tab-overview" aria-controls="panel-overview" aria-selected="true" tabindex="0"',
            self.text,
        )
        for name in ("intake", "plan", "sources", "reviews", "memo"):
            with self.subTest(name):
                self.assertIn(
                    f'id="tab-{name}" aria-controls="panel-{name}" '
                    f'aria-selected="false" tabindex="-1"',
                    self.text,
                )
        for token in (
            'el("tabs").addEventListener("keydown"',
            "ArrowLeft",
            "ArrowRight",
            "Home",
            "End",
            "preventDefault",
            'el("tab-" + TAB_NAMES[i]).tabIndex = chosen ? 0 : -1',
            'el("tab-" + name).focus()',
            'return !el("tab-" + name).hidden;',
        ):
            self.assertIn(token, self.text, token)
        self.assertEqual(re.findall(r"https?://[^\s\"']+", self.text), [], "still no network")

    def test_a_tab_appears_only_when_its_section_carries_data(self):
        for token in (
            "intakePanel(data.intake)",
            "sourcesPanel(data.sources)",
            "reviewsPanel(data.reviews)",
            "memoPanel(data.memo)",
            'if (data.plan) { available.push("plan"); }',
            "tabs(available, !!data.gate)",
        ):
            self.assertIn(token, self.text, token)

    def test_an_open_gate_takes_the_page_back_to_the_overview(self):
        self.assertIn(
            'if (gateOpen || available.indexOf(openTab) < 0) { openTab = "overview"; }', self.text
        )
        for token in ("localStorage", "sessionStorage", "indexedDB"):
            self.assertNotIn(token, self.text, "the open tab is remembered in memory only")

    def test_the_page_never_builds_markup_out_of_the_data(self):
        for token in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(token, self.text, token)


class DashboardIsDocumentedTest(unittest.TestCase):
    """§3.4: the router documents the one extra action `dashboard: on` costs."""

    def test_the_router_documents_both_branches(self):
        text = ROUTER.read_text(encoding="utf-8-sig")
        for token in (
            "dashboard.publish",
            "dashboard.write_db",
            "task dashboard",
            "--unavailable",
            "if_version",
            "version_mismatch",
            "file_path",
        ):
            self.assertIn(token, text, token)

    def test_the_skills_name_the_dashboard_section(self):
        for name in ("memo", "continue"):
            path = PLUGIN_ROOT / "skills" / name / "SKILL.md"
            self.assertIn("dashboard", path.read_text(encoding="utf-8-sig"), name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
