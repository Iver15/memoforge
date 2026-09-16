"""Tests for scripts/memoforge/gates.py — the single gate parser and the CLI prompts (ТЗ §2.4, §9)."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, temp_root  # noqa: E402
from memoforge import events, gates, limits, machine, probe, state_io  # noqa: E402


def parse_args(driver: Driver, action: dict, text: str, **overrides) -> argparse.Namespace:
    """`mf gate parse` arguments for one issued gate step, with per-test overrides."""
    payload = {
        "workdir": str(driver.work_dir),
        "gate": None,
        "text": text,
        "step": action["step_id"],
        "attempt": action["attempt"],
        "generation": action.get("generation", 0),
        "human": False,
    }
    payload.update(overrides)
    return argparse.Namespace(**payload)


class ParserFormatTest(unittest.TestCase):
    """§2.4 rule (a): one parser, one keyword set for every gate."""

    def test_letter_and_free_text_answers(self):
        parsed = gates.parse_reply("intake", "1A 2C\n3: the client is a controller")
        self.assertEqual("proceed", parsed["action"])
        self.assertEqual({"1": "A", "2": "C", "3": "the client is a controller"}, parsed["answers"])
        self.assertEqual([], parsed["errors"])

    def test_proceed_and_continue_and_cancel(self):
        self.assertEqual("proceed", gates.parse_reply("intake", "proceed")["action"])
        self.assertEqual("continue", gates.parse_reply("source_review", "continue")["action"])
        self.assertEqual("cancel", gates.parse_reply("insufficient", "cancel")["action"])

    def test_cancel_wins_over_every_other_keyword(self):
        parsed = gates.parse_reply("plan", "approve full cancel")
        self.assertEqual("cancel", parsed["action"])
        self.assertEqual({"Plan": "Cancel"}, parsed["answers"])

    def test_approve_with_mode_style_and_sources(self):
        parsed = gates.parse_reply("plan", "approve brief style:my-firm sources:reduced")
        self.assertEqual("approve", parsed["action"])
        self.assertEqual("Brief", parsed["answers"]["Mode"])
        self.assertEqual("my-firm", parsed["answers"]["Style"])
        self.assertEqual("Reduced", parsed["answers"]["Sources"])
        self.assertEqual("Approve", parsed["answers"]["Plan"])

    def test_approve_standard_style(self):
        parsed = gates.parse_reply("plan", "approve full standard")
        self.assertEqual("standard", parsed["answers"]["Style"])

    def test_edit_carries_the_free_text(self):
        parsed = gates.parse_reply("plan", "edit: add the UK angle")
        self.assertEqual("edit", parsed["action"])
        self.assertEqual("add the UK angle", parsed["answers"]["edit_text"])

    def test_unrecognized_reply_is_not_accepted(self):
        parsed = gates.parse_reply("intake", "maybe later")
        self.assertIsNone(parsed["action"])
        self.assertFalse(parsed["recognized"])
        self.assertTrue(parsed["errors"])

    def test_approve_is_rejected_on_a_question_gate(self):
        parsed = gates.parse_reply("intake", "approve full")
        self.assertIsNone(parsed["action"])
        self.assertIn("action_not_allowed_for_gate: approve", parsed["errors"])

    def test_defaults_never_accept_the_assumptions(self):
        questions = [{"question": "q1"}, {"question": "q2"}]
        parsed = gates.apply_defaults("intake", questions)
        self.assertEqual("proceed", parsed["action"])
        self.assertEqual(["1", "2"], parsed["defaults_applied"])
        self.assertTrue(parsed["budget_exhausted"])
        self.assertEqual("cancel", gates.apply_defaults("plan", [])["action"])
        self.assertEqual("continue", gates.apply_defaults("source_review", [])["action"])
        self.assertEqual("cancel", gates.apply_defaults("insufficient", [])["action"])


class MixedReplyFormatTest(unittest.TestCase):
    """D-56 / находка 8: `1A 2C 3: free text` разбирается целиком, без потери свободного текста."""

    def test_letters_and_free_text_on_one_line(self):
        parsed = gates.parse_reply("intake", "1A 2C 3: the client is a controller")
        self.assertEqual([], parsed["errors"])
        self.assertEqual(
            {"1": "A", "2": "C", "3": "the client is a controller"}, parsed["answers"]
        )
        self.assertEqual("proceed", parsed["action"])

    def test_free_text_may_answer_any_question_number(self):
        parsed = gates.parse_reply("sufficiency_followup", "2: only the German entity")
        self.assertEqual({"2": "only the German entity"}, parsed["answers"])
        self.assertEqual([], parsed["errors"])

    def test_free_text_swallows_tokens_that_look_like_letter_answers(self):
        parsed = gates.parse_reply("intake", "1B 2: option 3C does not apply here")
        self.assertEqual({"1": "B", "2": "option 3C does not apply here"}, parsed["answers"])
        self.assertEqual([], parsed["errors"])


class PartialReplyTest(unittest.TestCase):
    """D-56 / находка 8: частично распознанный ответ с `errors` не коммитится."""

    PARTIAL = "1A maybe later"

    def test_parse_reply_reports_the_unrecognised_remainder(self):
        parsed = gates.parse_reply("intake", self.PARTIAL)
        self.assertTrue(parsed["errors"])

    def test_partial_reply_is_not_committed(self):
        driver = Driver(temp_root(self), slug="parse-partial")
        action = driver.run_until("intake_questions_pending")
        answer = driver.parse_gate(action, self.PARTIAL)
        self.assertIsNone(answer["action"])
        self.assertTrue(answer["errors"])
        self.assertIn("reprompt", answer)
        state = driver.state()
        row = machine.step_row(state, action["step_id"], action["attempt"])
        self.assertIsNone(row["status"], "a partial reply must not close the gate step")
        self.assertFalse((driver.work_dir / gates.USER_FACTS_PATH).is_file())
        self.assertFalse((state.get("intake") or {}).get("assumptions_accepted"))
        self.assertEqual(
            [], [r for r in events.read_events(driver.work_dir) if r["event"] == "gate_answered"]
        )

    def test_exhausted_budget_applies_defaults_without_accepting_assumptions(self):
        driver = Driver(temp_root(self), slug="parse-partial-budget")
        action = driver.run_until("intake_questions_pending")
        for expected in (1, 2):
            self.assertEqual(expected, driver.parse_gate(action, self.PARTIAL)["parse_errors"])
        final = driver.parse_gate(action, self.PARTIAL)
        self.assertEqual("proceed", final["action"])
        self.assertEqual(["1", "2"], final["defaults_applied"])
        self.assertFalse(driver.state()["intake"]["assumptions_accepted"])


class QuestionRangeTest(unittest.TestCase):
    """D-61 / N-03: ключ вне `1..N` — не ответ, а `unrecognized_token`; коммита нет (D-56)."""

    def test_a_key_above_the_printed_range_is_not_an_answer(self):
        parsed = gates.parse_reply("intake", "10A", 2)
        self.assertIsNone(parsed["action"])
        self.assertFalse(parsed["recognized"])
        self.assertEqual({}, parsed["answers"])
        self.assertEqual(["unrecognized_token: 10A"], parsed["errors"])

    def test_free_text_above_the_printed_range_is_not_an_answer(self):
        parsed = gates.parse_reply("intake", "1A 3: the client is a controller", 2)
        self.assertEqual({"1": "A"}, parsed["answers"])
        # The in-range half is still an answer, but the `errors` make it a partial reply (D-56).
        self.assertEqual(["unrecognized_token: 3:"], parsed["errors"])
        self.assertFalse(gates.parse_reply("intake", "3: the client is a controller", 2)["recognized"])

    def test_keys_inside_the_range_are_unchanged(self):
        parsed = gates.parse_reply("intake", "1A 2C 3: the client is a controller", 3)
        self.assertEqual([], parsed["errors"])
        self.assertEqual({"1": "A", "2": "C", "3": "the client is a controller"}, parsed["answers"])
        self.assertEqual("proceed", parsed["action"])

    def test_the_neighbouring_cases_still_behave_as_before(self):
        # A letter outside the options, an empty free-text marker and a decimal in prose were
        # already errors without the bound and stay errors with it.
        self.assertEqual(["unrecognized_token: 1E"], gates.parse_reply("intake", "1E", 2)["errors"])
        self.assertEqual(["unrecognized_token: 3:"], gates.parse_reply("intake", "3:", 2)["errors"])
        self.assertTrue(gates.parse_reply("intake", "We keep 3.2 million records", 2)["errors"])

    def test_an_unbounded_reply_keeps_the_old_behaviour(self):
        self.assertEqual({"10": "A"}, gates.parse_reply("intake", "10A")["answers"])

    def test_printed_questions_is_the_count_the_gate_prints(self):
        driver = Driver(temp_root(self), slug="range-count")
        action = driver.run_until("intake_questions_pending")
        questions = gates.printed_questions(driver.work_dir, driver.state(), "intake")
        self.assertEqual(2, len(questions))
        for index in range(1, len(questions) + 1):
            self.assertIn(f"{index}. ", action["text"])
        self.assertNotIn("3. ", action["text"])
        self.assertIsNone(gates.printed_questions(driver.work_dir, driver.state(), "plan"))

    def test_an_out_of_range_reply_is_reprompted_and_never_committed(self):
        driver = Driver(temp_root(self), slug="range-parse")
        action = driver.run_until("intake_questions_pending")
        answer = driver.parse_gate(action, "10A")
        self.assertIsNone(answer["action"])
        self.assertEqual(["unrecognized_token: 10A"], answer["errors"])
        self.assertIn("reprompt", answer)
        state = driver.state()
        row = machine.step_row(state, action["step_id"], action["attempt"])
        self.assertIsNone(row["status"], "an out-of-range key must not close the gate step")
        self.assertFalse((driver.work_dir / gates.USER_FACTS_PATH).is_file())
        self.assertFalse((state.get("intake") or {}).get("assumptions_accepted"))
        self.assertEqual(
            [], [r for r in events.read_events(driver.work_dir) if r["event"] == "gate_answered"]
        )


class NumberedKeyNormalisationTest(unittest.TestCase):
    """D-71 / R2-04: `01A` ≡ `1A` — номер канонизируется до проверки диапазона и записи."""

    def test_a_leading_zero_letter_answer_is_the_same_key(self):
        parsed = gates.parse_reply("intake", "01A 02C", 2)
        self.assertEqual({"1": "A", "2": "C"}, parsed["answers"])
        self.assertEqual([], parsed["errors"])
        self.assertEqual("proceed", parsed["action"])

    def test_a_leading_zero_free_text_answer_is_the_same_key(self):
        parsed = gates.parse_reply("intake", "001: the client is a controller", 2)
        self.assertEqual({"1": "the client is a controller"}, parsed["answers"])
        self.assertEqual([], parsed["errors"])

    def test_a_padded_key_outside_the_range_is_still_rejected(self):
        self.assertEqual(["unrecognized_token: 03A"], gates.parse_reply("intake", "03A", 2)["errors"])
        self.assertEqual(["unrecognized_token: 03:"], gates.parse_reply("intake", "03: text", 2)["errors"])
        self.assertEqual(["unrecognized_token: 0A"], gates.parse_reply("intake", "0A", 2)["errors"])

    def test_the_normalised_key_answers_the_printed_question(self):
        # R2-04: `01A` passed the bound check but was stored as `01`, so `user-facts.md` said
        # "not answered" for every question while `assumptions_accepted` stayed true.
        driver = Driver(temp_root(self), slug="key-normalise")
        action = driver.run_until("intake_questions_pending")
        answer = driver.parse_gate(action, "01A 02A")
        self.assertEqual("proceed", answer["action"])
        self.assertEqual({"1": "A", "2": "A"}, answer["answers"])
        facts = (driver.work_dir / gates.USER_FACTS_PATH).read_text(encoding="utf-8")
        self.assertNotIn("not answered", facts)
        self.assertTrue((driver.state().get("intake") or {}).get("assumptions_accepted"))


class PlanEditAnswersTest(unittest.TestCase):
    """D-66 / N-08: `edit:` на текстовом канале сохраняет распознанные Mode/Style/Sources."""

    def test_edit_keeps_mode_style_and_sources(self):
        parsed = gates.parse_reply(
            "plan", "edit: add the UK angle\nbrief style:my-firm sources:reduced"
        )
        self.assertEqual("edit", parsed["action"])
        self.assertEqual("add the UK angle", parsed["answers"]["edit_text"])
        self.assertEqual("Edit", parsed["answers"]["Plan"])
        self.assertEqual("Brief", parsed["answers"]["Mode"])
        self.assertEqual("my-firm", parsed["answers"]["Style"])
        self.assertEqual("Reduced", parsed["answers"]["Sources"])

    def test_the_forced_approval_of_an_edit_keeps_the_chosen_mode(self):
        # §2.2: the forced approve applies the answers of the last edit iteration, so `Mode`
        # must survive the `edit` branch instead of falling back to `full`.
        parsed = gates.parse_reply("plan", "brief\nedit: add the UK angle")
        applied = machine.apply_plan_answers({"config": {}}, parsed["answers"])
        self.assertEqual("brief", applied["mode"])

    def test_edit_without_a_mode_answer_carries_none(self):
        parsed = gates.parse_reply("plan", "edit: add the UK angle")
        self.assertNotIn("Mode", parsed["answers"])


class AuqAnswersTest(unittest.TestCase):
    """§2.4: `report --answers` runs through the same parser; Cancel first."""

    def test_cancel_in_any_component_cancels(self):
        parsed = gates.parse_auq({"Plan": "Approve", "Mode": "Full", "Sources": "Cancel"})
        self.assertEqual("cancel", parsed["action"])

    def test_approve_keeps_every_answer(self):
        parsed = gates.parse_auq({"Plan": "Approve", "Mode": "Brief", "Style": "my-firm"})
        self.assertEqual("approve", parsed["action"])
        self.assertEqual("Brief", parsed["answers"]["Mode"])

    def test_edit_is_recognised(self):
        self.assertEqual("edit", gates.parse_auq({"Plan": "Edit"})["action"])

    def test_unknown_plan_answer_is_rejected(self):
        parsed = gates.parse_auq({"Plan": "Later"})
        self.assertFalse(parsed["recognized"])

    def test_style_order_applies_mode_binding_before_mode(self):
        # §2.4: Style is applied before Mode, so a profile's `mode_binding` wins.
        state = {"config": {}}
        applied = machine.apply_plan_answers(state, {"Plan": "Approve", "Mode": "Brief"})
        self.assertEqual("brief", applied["mode"])
        self.assertEqual(["statutes"], applied["config"]["researcher_layers"])


class AuqPlanAnswerTest(unittest.TestCase):
    """D-56 / находка 9: `approve` только при явном `Plan: Approve`."""

    def test_empty_answers_are_not_an_approval(self):
        parsed = gates.parse_auq({})
        self.assertIsNone(parsed["action"])
        self.assertFalse(parsed["recognized"])
        self.assertEqual(["plan_answer_missing"], parsed["errors"])
        self.assertEqual({}, parsed["answers"])

    def test_an_answer_without_plan_is_not_an_approval(self):
        parsed = gates.parse_auq({"Mode": "Full", "Style": "my-firm"})
        self.assertFalse(parsed["recognized"])
        self.assertEqual(["plan_answer_missing"], parsed["errors"])

    def test_a_blank_plan_answer_is_not_an_approval(self):
        parsed = gates.parse_auq({"Plan": "   ", "Mode": "Full"})
        self.assertFalse(parsed["recognized"])
        self.assertEqual(["plan_answer_missing"], parsed["errors"])

    def test_report_answers_without_plan_leaves_the_gate_open(self):
        driver = Driver(temp_root(self), slug="auq-no-plan")
        action = driver.run_until("plan_approval_pending")
        answer = driver.report(
            action["step_id"],
            action["attempt"],
            answers=json.dumps({"Mode": "Full"}),
            generation=action.get("generation", 0),
        )
        self.assertFalse(answer["accepted"])
        self.assertEqual(["plan_answer_missing"], answer["errors"])
        state = driver.state()
        self.assertNotEqual("approved", (state.get("plan_approval") or {}).get("status"))
        row = machine.step_row(state, action["step_id"], action["attempt"])
        self.assertIsNone(row["status"])


class ParseCheckOrderTest(unittest.TestCase):
    """Находка 10 / §3.1 / D-34: identity → generation → closed → content в `gate parse`."""

    def test_identity_is_checked_before_the_generation(self):
        driver = Driver(temp_root(self), slug="order-identity")
        action = driver.run_until("intake_questions_pending")
        answer = gates.run_parse(
            parse_args(driver, action, "1A 2B", attempt=action["attempt"] + 1, generation=9)
        )
        self.assertEqual(["identity_mismatch"], answer["errors"])

    def test_generation_is_required_for_an_issued_gate(self):
        driver = Driver(temp_root(self), slug="order-generation")
        action = driver.run_until("intake_questions_pending")
        answer = gates.run_parse(parse_args(driver, action, "1A 2B", generation=None))
        self.assertEqual(["generation_required"], answer["errors"])
        row = machine.step_row(driver.state(), action["step_id"], action["attempt"])
        self.assertIsNone(row["status"], "a reply without --generation changes nothing")

    def test_generation_is_still_checked_before_the_closed_step(self):
        driver = Driver(temp_root(self), slug="order-closed")
        action = driver.run_until("intake_questions_pending")
        driver.parse_gate(action, "1A 2B")
        stale = gates.run_parse(parse_args(driver, action, "1A 2B", generation=4))
        self.assertEqual(["stale_generation"], stale["errors"])
        self.assertNotIn("already_parsed", stale)


class RenderTest(unittest.TestCase):
    """§2.4: the CLI generates every prompt; the plan gate has an equivalent text fallback."""

    def test_intake_prompt_lists_capped_must_answer_questions_and_defaults(self):
        driver = Driver(temp_root(self), slug="render-intake")
        action = driver.run_until("intake_questions_pending")
        text = action["text"]
        self.assertIn(f"/memoforge:continue {driver.work_dir.name} 1A 2C", text)
        self.assertIn("1. Fixture question 1", text)
        self.assertIn("2. Fixture question 2", text)
        self.assertIn("A) Option A", text)
        self.assertIn("If that is wrong:", text)
        self.assertIn("`proceed`", text)
        self.assertIn("`cancel`", text)
        must, defaulted = gates.intake_questions(driver.work_dir, driver.state())
        self.assertLessEqual(len(must), limits.INTAKE_MAX_QUESTIONS)
        self.assertTrue(defaulted)

    def test_plan_gate_has_auq_questions_and_a_full_text_fallback(self):
        driver = Driver(temp_root(self), slug="render-plan")
        action = driver.run_until("plan_approval_pending")
        headers = [question["header"] for question in action["questions"]]
        self.assertEqual(["Plan", "Mode"], headers[:2])
        recommended = [
            option["label"]
            for question in action["questions"]
            if question["header"] == "Mode"
            for option in question["options"]
            if option["description"].startswith("(Recommended)")
        ]
        self.assertEqual(["Full"], recommended)
        fallback = action["text_fallback"]
        self.assertIn("`approve [brief|full] [style:<name>|standard] [sources:reduced]`", fallback)
        self.assertIn("`edit: <what to change>`", fallback)
        self.assertIn("`cancel`", fallback)

    def test_plan_digest_is_the_plan_only_and_the_fallback_adds_the_answer_format(self):
        """D-86: `render_plan_digest` is what the user reads; the answer format lives in the fallback."""
        driver = Driver(temp_root(self), slug="render-plan-digest")
        driver.run_until("plan_approval_pending")
        digest = gates.render_plan_digest(driver.work_dir, driver.state())
        self.assertIn("`plan.json`", digest)
        self.assertIn("Retention of customer records", digest)
        self.assertIn("How long may the client keep customer records?", digest)
        self.assertIn("Research layers: statutes, case_law, doctrine", digest)
        self.assertIn("estimated complexity: high", digest)
        self.assertIn("Recommended mode: full", digest)
        self.assertLessEqual(len(digest.splitlines()), 40, "the digest must stay readable in chat")
        for instruction in ("Reply with one of:", "edit: <what to change>", "/memoforge:continue"):
            self.assertNotIn(instruction, digest, "answer instructions belong to the text fallback")
        fallback = gates.render(driver.work_dir, driver.state(), "plan")
        self.assertIn(digest.rstrip(), fallback)
        self.assertIn("Reply with one of:", fallback)

    def test_the_plan_gate_text_carries_the_source_access_block(self):
        """D-147: what did not answer today is part of what the user approves."""
        from unittest import mock

        from memoforge import preflight

        driver = Driver(temp_root(self), slug="render-plan-access")
        driver.run_until("plan_approval_pending")
        blocked = [
            {
                "host": "legifrance.gouv.fr",
                "status": "cloudflare",
                "alternative": preflight.PREFLIGHT_ALTERNATIVES["legifrance.gouv.fr"],
            }
        ]
        with mock.patch.object(preflight, "blocked_hosts", return_value=blocked):
            state = driver.state()
            text = machine.plan_gate_text(driver.work_dir, state)
            fallback = gates.render(driver.work_dir, state, "plan")
        for rendered in (text, fallback):
            self.assertIn("Source access today:", rendered)
            self.assertIn(
                "- legifrance.gouv.fr: Cloudflare block → "
                + preflight.PREFLIGHT_ALTERNATIVES["legifrance.gouv.fr"],
                rendered,
            )

    def test_source_review_prompt_comes_from_the_digest(self):
        driver = Driver(temp_root(self), slug="render-sources")
        action = driver.run_until("source_review_pending")
        self.assertIn("Source review —", action["text"])
        self.assertIn("Reply `continue`", action["text"])


class BriefMismatchHintTest(unittest.TestCase):
    """D34-01: where Brief is not the recommendation, gate 4 says in one sentence what it costs."""

    def plan(self, complexity: str, issues: int) -> dict:
        return dict(
            probe.fixture_plan(),
            estimated_complexity=complexity,
            issues=[
                {
                    "issue_id": f"i{index}",
                    "title": f"Issue {index}",
                    "question": f"Question {index}?",
                    "jurisdictions": ["EU"],
                }
                for index in range(1, issues + 1)
            ],
        )

    def test_the_hint_fires_on_high_complexity_and_on_more_than_three_issues(self):
        self.assertIn(
            "This plan has 1 issue at high complexity; Brief researches one layer",
            gates.brief_mismatch_hint(self.plan("high", 1)),
        )
        self.assertIn(
            "This plan has 4 issues at medium complexity",
            gates.brief_mismatch_hint(self.plan("medium", 4)),
        )
        self.assertIn(
            "case law and doctrine gaps become caveats",
            gates.brief_mismatch_hint(self.plan("high", 8)),
        )

    def test_a_plan_brief_can_carry_says_nothing(self):
        self.assertEqual("", gates.brief_mismatch_hint(self.plan("low", 8)), "Brief is recommended")
        self.assertEqual("", gates.brief_mismatch_hint(self.plan("medium", 3)))
        self.assertEqual("", gates.brief_mismatch_hint(None))

    def test_the_digest_and_the_brief_option_carry_it(self):
        driver = Driver(temp_root(self), slug="brief-hint")
        action = driver.run_until("plan_approval_pending")
        hint = gates.brief_mismatch_hint(probe.fixture_plan())
        self.assertTrue(hint)
        self.assertIn(hint, gates.render_plan_digest(driver.work_dir, driver.state()))
        self.assertIn(hint, action["text"])
        options = {
            option["label"]: option["description"]
            for question in action["questions"]
            if question["header"] == "Mode"
            for option in question["options"]
        }
        self.assertIn(hint, options["Brief"])
        self.assertNotIn(hint, options["Full"])

    def test_a_low_complexity_plan_leaves_both_options_plain(self):
        driver = Driver(temp_root(self), slug="brief-hint-low")
        driver.run_until("plan_approval_pending")
        state_io.write_json_atomic(
            driver.work_dir / gates.PLAN_PATH, dict(probe.fixture_plan(), estimated_complexity="low")
        )
        auq = gates.build_auq(driver.work_dir, driver.state())
        descriptions = [
            option["description"]
            for question in auq["questions"]
            if question["header"] == "Mode"
            for option in question["options"]
        ]
        self.assertEqual(
            ["(Recommended) " + gates.MODE_SUMMARY["brief"], gates.MODE_SUMMARY["full"]], descriptions
        )
        self.assertNotIn("Brief researches one layer", gates.render_plan_digest(driver.work_dir, driver.state()))

    def test_the_answer_records_the_recommendation_it_was_given_against(self):
        driver = Driver(temp_root(self), slug="brief-hint-record")
        action = driver.run_until("plan_approval_pending")
        driver.parse_gate(action, "approve brief")
        iteration = driver.state()["plan_approval"]["iterations"][-1]
        self.assertEqual("full", iteration["recommended_mode"])
        self.assertEqual("Brief", iteration["answers"]["Mode"])


class FollowupFactsTest(unittest.TestCase):
    """D34-06: the follow-up answer joins the canonical file of user facts and is republished."""

    QUESTION = "Where are the support agents whose performance the AI outputs will help score?"
    SECOND_QUESTION = "Which supervisory authority has already contacted the client?"
    FIRST_ASKED_AT = "2026-01-01T09:00:00Z"
    SECOND_ASKED_AT = "2026-01-02T09:00:00Z"
    NO_STEP = {"step_id": None, "attempt": 1, "generation": 0}
    """`mf gate parse` outside the machine: §5.2 leaves `--step` optional (R2-01)."""

    def _at_the_followup_gate(self, slug: str, *, asked_at: str | None = FIRST_ASKED_AT) -> tuple[Driver, dict]:
        driver = Driver(temp_root(self), slug=slug)
        intake = driver.run_until("intake_questions_pending")
        driver.parse_gate(intake, "1A 2B")
        state_io.write_state(
            driver.work_dir,
            lambda current: current.update(
                {
                    "current_phase": "research_sufficiency_followup_pending",
                    "sufficiency_followup": {
                        "status": "pending",
                        "asked_at": asked_at,
                        "questions": [
                            {
                                "question": self.QUESTION,
                                "header": "Locale",
                                "options": [
                                    {"label": "EEA-based staff", "description": "Inside the EEA."},
                                    {"label": "Outside the EEA", "description": "Third country."},
                                ],
                                "default_assumption_if_skipped": "Agents sit outside the EEA.",
                            }
                        ],
                    },
                }
            ),
        )
        return driver, driver.next()

    def _facts(self, driver: Driver) -> str:
        return (driver.work_dir / gates.USER_FACTS_PATH).read_text(encoding="utf-8")

    def _ask_again(self, driver: Driver, *, asked_at: str | None = SECOND_ASKED_AT) -> None:
        """The second follow-up gate of D-116, with the questions `mf sufficiency route` wrote."""
        state_io.write_state(
            driver.work_dir,
            lambda current: current.update(
                {
                    "current_phase": "research_sufficiency_followup_pending",
                    "sufficiency_followup": {
                        "status": "pending",
                        "asked_at": asked_at,
                        "questions": [
                            {
                                "question": self.SECOND_QUESTION,
                                "header": "Authority",
                                "options": [
                                    {"label": "The Irish DPC", "description": "Lead authority."},
                                    {"label": "No authority yet", "description": "No contact so far."},
                                ],
                            }
                        ],
                    },
                }
            ),
        )

    def _commit(self, driver: Driver, text: str, step_id: str | None) -> dict:
        parsed = gates.parse_reply("sufficiency_followup", text, 1)
        return gates.commit(
            driver.work_dir,
            driver.state(),
            "sufficiency_followup",
            parsed,
            generation=0,
            raw=text,
            step_id=step_id,
            attempt=1,
        )

    def test_the_answer_is_appended_under_its_own_heading(self):
        driver, action = self._at_the_followup_gate("followup-facts")
        driver.parse_gate(action, "1A")
        facts = self._facts(driver)
        self.assertIn("Option A", facts, "the intake answers are still there")
        self.assertIn(gates.FOLLOWUP_HEADING, facts)
        self.assertIn(f"- **{self.QUESTION}** — EEA-based staff", facts)

    def test_free_text_and_defaults_are_recorded_as_such(self):
        driver, action = self._at_the_followup_gate("followup-facts-text")
        driver.parse_gate(action, "1: only the German entity, 2019 onwards")
        self.assertIn("— only the German entity, 2019 onwards", self._facts(driver))

    def test_the_file_is_republished_with_its_new_sha(self):
        driver, action = self._at_the_followup_gate("followup-facts-publish")
        driver.parse_gate(action, "1A")
        rows = [
            row
            for row in driver.state()["published"]
            if row["canonical_path"] == gates.USER_FACTS_PATH
        ]
        self.assertEqual(1, len(rows), "one row per canonical path")
        self.assertEqual(
            state_io.sha256_file(driver.work_dir / gates.USER_FACTS_PATH), rows[0]["sha256"]
        )

    def test_re_closing_one_gate_rewrites_only_its_own_block(self):
        """D-143: the section is keyed by gate identity, and re-closing one replaces one block."""
        driver, action = self._at_the_followup_gate("followup-facts-idempotent")
        driver.parse_gate(action, "1A")
        first = self._facts(driver)
        self._commit(driver, "1B", action["step_id"])
        second = self._facts(driver)
        self.assertEqual(1, second.count(gates.FOLLOWUP_HEADING))
        self.assertEqual(1, second.count(gates.FOLLOWUP_ROUND_HEADING))
        self.assertIn("— Outside the EEA", second)
        self.assertNotIn("— EEA-based staff", second)
        self.assertEqual(first.split(gates.FOLLOWUP_HEADING)[0], second.split(gates.FOLLOWUP_HEADING)[0])

    def test_a_second_follow_up_keeps_the_answers_of_the_first(self):
        """D-143 (D-138 × D-116): two user follow-ups are allowed; the second erased the first."""
        driver, action = self._at_the_followup_gate("followup-facts-two-rounds")
        driver.parse_gate(action, "1A")
        self._ask_again(driver)
        self._commit(driver, "1A", "s-090")
        facts = self._facts(driver)
        self.assertIn("Option A", facts, "the intake answers are still there")
        self.assertEqual(1, facts.count(gates.FOLLOWUP_HEADING))
        self.assertEqual(2, facts.count(gates.FOLLOWUP_ROUND_HEADING))
        self.assertIn(f"- **{self.QUESTION}** — EEA-based staff", facts, "round one survives")
        self.assertIn(f"- **{self.SECOND_QUESTION}** — The Irish DPC", facts)

    def test_the_second_round_is_the_one_a_repeat_of_it_replaces(self):
        driver, action = self._at_the_followup_gate("followup-facts-two-rounds-repeat")
        driver.parse_gate(action, "1A")
        self._ask_again(driver)
        self._commit(driver, "1A", "s-090")
        self._commit(driver, "1B", "s-090")
        facts = self._facts(driver)
        self.assertEqual(2, facts.count(gates.FOLLOWUP_ROUND_HEADING))
        self.assertIn(f"- **{self.QUESTION}** — EEA-based staff", facts)
        self.assertIn("— No authority yet", facts)
        self.assertNotIn("— The Irish DPC", facts)

    def _parse_without_step(self, driver: Driver, text: str) -> dict:
        """`mf gate parse --gate ... --text ...` with no `--step`, the way the CLI allows it."""
        return gates.run_parse(parse_args(driver, self.NO_STEP, text))

    def test_two_rounds_answered_without_a_step_both_keep_their_answers(self):
        """R2-01: without `--step` both rounds were keyed alike, so the second erased the first."""
        driver, _ = self._at_the_followup_gate("followup-facts-no-step")
        self._parse_without_step(driver, "1A")
        self._ask_again(driver)
        self._parse_without_step(driver, "1A")
        facts = self._facts(driver)
        self.assertEqual(1, facts.count(gates.FOLLOWUP_HEADING))
        self.assertEqual(2, facts.count(gates.FOLLOWUP_ROUND_HEADING))
        self.assertIn(f"- **{self.QUESTION}** — EEA-based staff", facts, "round one survives")
        self.assertIn(f"- **{self.SECOND_QUESTION}** — The Irish DPC", facts)

    def test_re_answering_one_round_without_a_step_replaces_only_its_block(self):
        """The identity is the round's own opening, so a second close of it rewrites one block."""
        driver, _ = self._at_the_followup_gate("followup-facts-no-step-repeat")
        self._parse_without_step(driver, "1A")
        self._ask_again(driver)
        self._parse_without_step(driver, "1A")
        self._parse_without_step(driver, "1B")
        facts = self._facts(driver)
        self.assertEqual(1, facts.count(gates.FOLLOWUP_HEADING))
        self.assertEqual(2, facts.count(gates.FOLLOWUP_ROUND_HEADING))
        self.assertIn(f"- **{self.QUESTION}** — EEA-based staff", facts, "round one survives")
        self.assertIn("— No authority yet", facts)
        self.assertNotIn("— The Irish DPC", facts)

    def test_rounds_are_kept_apart_even_when_the_state_carries_no_asked_at(self):
        """Last resort of `followup_round_identity`: the questions the round printed."""
        driver, _ = self._at_the_followup_gate("followup-facts-no-asked-at", asked_at=None)
        self._parse_without_step(driver, "1A")
        self._ask_again(driver, asked_at=None)
        self._parse_without_step(driver, "1A")
        self._parse_without_step(driver, "1B")
        facts = self._facts(driver)
        self.assertEqual(2, facts.count(gates.FOLLOWUP_ROUND_HEADING))
        self.assertIn(f"- **{self.QUESTION}** — EEA-based staff", facts, "round one survives")
        self.assertIn("— No authority yet", facts, "the repeat replaced round two, not round one")

    def test_the_round_identity_prefers_the_step_then_the_opening_of_the_round(self):
        state = {"sufficiency_followup": {"status": "pending", "asked_at": self.FIRST_ASKED_AT}}
        self.assertEqual("s-090", gates.followup_round_identity(state, "s-090"))
        self.assertEqual(f"asked-{self.FIRST_ASKED_AT}", gates.followup_round_identity(state))
        self.assertEqual(
            gates.followup_round_identity(state), gates.followup_round_identity(state, None)
        )
        other = {"sufficiency_followup": {"status": "pending", "asked_at": self.SECOND_ASKED_AT}}
        self.assertNotEqual(
            gates.followup_round_identity(state), gates.followup_round_identity(other)
        )
        self.assertTrue(gates.followup_round_identity({}).startswith("questions-"))


class UnreadablePlanDegradesGateFourTest(unittest.TestCase):
    """D-99: gate 4 degrades on an unusable `plan.json` instead of raising.

    `build_auq` and `render_plan_digest` used to raise `TypeError` on a plan whose `issues` or
    `jurisdictions` is not a list, and `machine.resume_gate` rebuilds both on every `next` at the
    plan gate — a structurally wrong plan crashed the run instead of asking a question (D-97).
    """

    def at_plan_gate(self, slug: str) -> Driver:
        driver = Driver(temp_root(self), slug=slug)
        driver.run_until("plan_approval_pending")
        return driver

    def unusable_plans(self) -> dict:
        """Syntactically valid JSON that `schemas/plan.schema.json` rejects."""
        issue = {
            "issue_id": "i1",
            "title": "Retention of customer records",
            "question": "How long may the client keep customer records?",
            "jurisdictions": "EU",
        }
        return {
            "issues is not a list": {"issues": 42},
            "jurisdictions is not a list": {"jurisdictions": 42},
            "issue jurisdictions is a bare string": dict(probe.fixture_plan(), issues=[issue]),
        }

    def assertDegraded(self, driver: Driver) -> None:
        state = driver.state()
        auq = gates.build_auq(driver.work_dir, state)
        plan_question = auq["questions"][0]
        self.assertEqual("Plan", plan_question["header"])
        self.assertEqual(gates.PLAN_UNREADABLE, plan_question["question"])
        self.assertEqual(["Edit", "Cancel"], [row["label"] for row in plan_question["options"]])
        mode_question = auq["questions"][1]
        self.assertEqual("Mode", mode_question["header"])
        self.assertEqual(["Full", "Brief"], [row["label"] for row in mode_question["options"]])
        self.assertEqual(gates.PLAN_UNREADABLE + "\n", gates.render_plan_digest(driver.work_dir, state))
        fallback = gates.render(driver.work_dir, state, "plan")
        self.assertIn(gates.PLAN_UNREADABLE, fallback)
        self.assertIn("- `edit: <what to change>`", fallback)
        self.assertIn("- `cancel`", fallback)
        self.assertNotIn("`approve", fallback, "approval is not offered without a readable plan")
        action = driver.next()
        self.assertNotIn("errors", action)
        self.assertEqual(machine.KIND_GATE_AUQ, action["kind"])
        self.assertEqual(
            ["Edit", "Cancel"], [row["label"] for row in action["questions"][0]["options"]]
        )
        self.assertIn(gates.PLAN_UNREADABLE, action["text"])

    def test_a_structurally_wrong_plan_degrades_instead_of_raising(self):
        driver = self.at_plan_gate("plan-invalid")
        for name, plan in self.unusable_plans().items():
            with self.subTest(name):
                state_io.write_json_atomic(driver.work_dir / gates.PLAN_PATH, plan)
                self.assertDegraded(driver)

    def test_a_missing_plan_degrades_instead_of_raising(self):
        driver = self.at_plan_gate("plan-missing")
        (driver.work_dir / gates.PLAN_PATH).unlink()
        self.assertDegraded(driver)

    def test_a_plan_that_is_not_json_degrades_instead_of_raising(self):
        driver = self.at_plan_gate("plan-not-json")
        (driver.work_dir / gates.PLAN_PATH).write_text("{ not json", encoding="utf-8")
        self.assertDegraded(driver)


class SourcesCoverageTest(unittest.TestCase):
    """D-106: the `Sources` question fires on a research row that lost every database, not on the

    mere absence of one of the four bundled servers.
    """

    def work_dir(self, namespaces: dict) -> Path:
        root = Path(temp_root(self)) / "coverage"
        (root / "intake").mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            root / "intake" / "mcp-probe.json",
            {"namespaces": {**namespaces, "other": []}},
        )
        return root

    def plan(self, *codes: str) -> dict:
        return {
            "jurisdictions": list(codes),
            "doctrine_required": False,
            "issues": [{"issue_id": "i1", "jurisdictions": list(codes)}],
        }

    def budget(self, namespaces: dict, plan: dict, mode: str = "full") -> dict:
        return gates.sources_question_needed(self.work_dir(namespaces), {}, plan, mode)

    def test_one_connected_server_covers_the_eu_rows(self):
        budget = self.budget({"ldh": "mcp__x"}, self.plan("EU"))
        self.assertEqual([], budget["missing"])
        self.assertFalse(budget["needed"])

    def test_nothing_connected_names_the_eu_rows_and_their_servers(self):
        budget = self.budget({}, self.plan("EU"))
        self.assertTrue(budget["needed"])
        self.assertEqual(
            [("statutes", "EU"), ("case_law", "EU")],
            [(row["layer"], row["jurisdiction"]) for row in budget["missing"]],
        )
        self.assertEqual(
            ["LegalViz", "Legal Data Hunter"], budget["missing"][0]["servers"]
        )

    def test_the_uk_rows_are_covered_by_the_uk_server_alone(self):
        budget = self.budget({"uklegal": "mcp__x"}, self.plan("UK"))
        self.assertEqual([], budget["missing"])

    def test_a_us_plan_does_not_care_about_the_eu_and_uk_servers(self):
        budget = self.budget(
            {"ldh": "mcp__x", "courtlistener": "mcp__y"}, self.plan("US")
        )
        self.assertEqual([], budget["missing"])
        self.assertFalse(budget["needed"])

    def test_a_row_without_any_server_is_never_a_gap(self):
        # US doctrine is WebSearch plus WebFetch: no probe result can take a database away from it.
        self.assertEqual([], gates.coverage_gaps({}, ["doctrine"], ["US"]))

    def test_a_dead_portal_on_a_row_without_a_database_is_a_real_gap(self):
        """D-147: once the preflight has spoken, «no database» plus «no portal» is a gap."""
        portals = {"ftc.gov": "dead", "govinfo.gov": "interstitial"}
        gaps = gates.coverage_gaps({}, ["doctrine"], ["US"], portals)
        self.assertEqual([("doctrine", "US")], [(row["layer"], row["jurisdiction"]) for row in gaps])
        self.assertEqual([], gaps[0]["servers"])
        self.assertEqual(["ftc.gov", "govinfo.gov"], gaps[0]["portals"])

    def test_one_answering_portal_covers_the_row(self):
        portals = {"ftc.gov": "dead", "govinfo.gov": "ok"}
        self.assertEqual([], gates.coverage_gaps({}, ["doctrine"], ["US"], portals))

    def test_an_answering_portal_rescues_a_row_whose_servers_are_all_gone(self):
        """analysis/38 §1.5: Cellar served EU statutes in the window where everything else did not."""
        portals = {"publications.europa.eu": "ok", "eur-lex.europa.eu": "waf_challenge"}
        self.assertEqual([], gates.coverage_gaps({}, ["statutes"], ["EU"], portals))

    def test_a_blocked_portal_and_no_server_names_both_in_the_question(self):
        """US doctrine routes to no database at all: only the preflight can call it a gap."""
        root = self.work_dir({})
        plan = dict(
            probe.fixture_plan(),
            jurisdictions=["US"],
            issues=[dict(probe.fixture_plan()["issues"][0], jurisdictions=["US"])],
        )
        state_io.write_json_atomic(root / "plan.json", plan)
        state_io.write_json_atomic(
            root / "intake" / "preflight.json",
            {
                "schema_version": 1,
                "checked_at": "2026-09-13T06:05:00Z",
                "offline": False,
                "hosts": [
                    {"host": host, "url": "https://example.org/", "status": "interstitial"}
                    for host in ("ftc.gov", "govinfo.gov", "courtlistener.com", "supremecourt.gov")
                ],
            },
        )
        questions = gates.build_auq(root, {})["questions"]
        text = [q for q in questions if q["header"] == "Sources"][0]["question"]
        self.assertIn("No legal database is connected for statutes in US", text)
        self.assertIn("No source answered today for doctrine in US (ftc.gov, govinfo.gov).", text)

    def test_a_connected_server_that_failed_its_smoke_call_covers_nothing(self):
        """D-147: `status` is what counts, not the namespace the host printed."""
        root = self.work_dir({"ldh": "mcp__x"})
        state_io.write_json_atomic(
            root / "intake" / "mcp-probe.json",
            {"namespaces": {"ldh": "mcp__x", "other": []}, "status": {"ldh": "quota"}},
        )
        budget = gates.sources_question_needed(root, {}, self.plan("EU"), "full")
        self.assertTrue(budget["needed"])
        self.assertEqual(
            [("statutes", "EU"), ("case_law", "EU")],
            [(row["layer"], row["jurisdiction"]) for row in budget["missing"]],
        )

    def test_a_plan_that_cannot_be_read_does_not_fire_on_coverage(self):
        budget = gates.sources_question_needed(self.work_dir({}), {}, None, "full")
        self.assertEqual([], budget["missing"])
        self.assertFalse(budget["needed"])

    def test_the_question_names_the_layer_the_jurisdiction_and_the_servers(self):
        root = self.work_dir({})
        state_io.write_json_atomic(root / "plan.json", probe.fixture_plan())
        questions = gates.build_auq(root, {})["questions"]
        text = [q for q in questions if q["header"] == "Sources"][0]["question"]
        self.assertIn(
            "No legal database is connected for statutes in EU (LegalViz, Legal Data Hunter).", text
        )
        self.assertIn("case_law in EU", text)


class ParseCommandTest(unittest.TestCase):
    """§2.4 (b): one state write records the answer, the files, the event and the closed step."""

    def test_intake_answer_writes_user_facts_and_closes_the_step(self):
        driver = Driver(temp_root(self), slug="parse-intake")
        action = driver.run_until("intake_questions_pending")
        result = driver.parse_gate(action, "1A 2B")
        self.assertEqual("proceed", result["action"])
        facts = driver.work_dir / gates.USER_FACTS_PATH
        self.assertTrue(facts.is_file())
        self.assertIn("Option A", facts.read_text(encoding="utf-8"))
        state = driver.state()
        self.assertTrue(state["intake"]["assumptions_accepted"])
        row = machine.step_row(state, action["step_id"], action["attempt"])
        self.assertEqual("ok", row["status"])
        answered = [r for r in events.read_events(driver.work_dir) if r["event"] == "gate_answered"]
        self.assertEqual(1, len(answered))
        repeat = driver.parse_gate(action, "1A 2B")
        self.assertTrue(repeat["already_parsed"])

    def test_three_unrecognized_replies_apply_defaults_without_accepting_assumptions(self):
        driver = Driver(temp_root(self), slug="parse-budget")
        action = driver.run_until("intake_questions_pending")
        for expected in (1, 2):
            answer = driver.parse_gate(action, "no idea")
            self.assertEqual(expected, answer["parse_errors"])
            self.assertIn("reprompt", answer)
            self.assertIsNone(answer["action"])
        final = driver.parse_gate(action, "still no idea")
        self.assertEqual("proceed", final["action"])
        self.assertEqual(["1", "2"], final["defaults_applied"])
        state = driver.state()
        self.assertFalse(state["intake"]["assumptions_accepted"])
        self.assertEqual(
            limits.MAX_GATE_PARSE_ERRORS, state["attempts"]["gate_parse_errors"]["intake"]
        )
        self.assertTrue(
            any(row["banner_id"] == "gate_defaults" for row in state["fallback_banners"])
        )

    def test_a_technical_error_does_not_spend_the_parse_budget(self):
        # D-72: `gate_parse_errors` counts misunderstood user replies only; a stale channel is a
        # technical error, answers without `reprompt` and leaves the budget untouched.
        driver = Driver(temp_root(self), slug="parse-technical")
        action = driver.run_until("intake_questions_pending")
        answer = gates.run_parse(parse_args(driver, action, "1A 2B", generation=7))
        self.assertEqual(["stale_generation"], answer["errors"])
        self.assertNotIn("reprompt", answer)
        counters = (driver.state().get("attempts") or {}).get("gate_parse_errors") or {}
        self.assertEqual(0, int(counters.get("intake", 0)))

    def test_cancel_sets_cancel_requested(self):
        driver = Driver(temp_root(self), slug="parse-cancel")
        action = driver.run_until("intake_questions_pending")
        driver.parse_gate(action, "cancel")
        self.assertTrue(driver.state()["cancel_requested"])

    def test_followup_answer_is_written_as_gate_answers_json(self):
        driver = Driver(temp_root(self), slug="parse-followup")
        driver.run_until("intake_questions_pending")
        state_io.write_state(
            driver.work_dir,
            lambda current: current.update(
                {
                    "current_phase": "research_sufficiency_followup_pending",
                    "sufficiency_followup": {
                        "status": "pending",
                        "questions": [
                            {
                                "question": "Which retention period applies?",
                                "header": "Period",
                                "options": [
                                    {"label": "Five years", "description": "Statutory minimum."},
                                    {"label": "Seven years", "description": "Tax rule."},
                                ],
                                "default_assumption_if_skipped": "Five years.",
                            }
                        ],
                    },
                }
            ),
        )
        action = driver.next()
        self.assertEqual("gate-text", action["kind"])
        driver.parse_gate(action, "1B")
        document = state_io.read_json(driver.work_dir / gates.FOLLOWUP_RESPONSE_PATH)
        self.assertEqual("sufficiency_followup", document["gate"])
        self.assertEqual({"1": "B"}, document["answers"])
        self.assertEqual("answered", driver.state()["sufficiency_followup"]["status"])

    def test_stale_generation_is_rejected_before_the_closed_check(self):
        driver = Driver(temp_root(self), slug="parse-generation")
        action = driver.run_until("intake_questions_pending")
        answer = driver.parse_gate({**action, "generation": 3}, "1A")
        self.assertEqual(["stale_generation"], answer["errors"])
        self.assertEqual(0, answer["expected_generation"])

    def test_render_and_parse_resolve_the_gate_from_the_phase(self):
        driver = Driver(temp_root(self), slug="parse-resolve")
        driver.run_until("intake_questions_pending")
        import argparse

        rendered = gates.run_render(
            argparse.Namespace(workdir=str(driver.work_dir), gate=None, human=False)
        )
        self.assertEqual("intake", rendered["gate"])
        self.assertEqual("intake_questions_pending", rendered["phase"])
        self.assertEqual(rendered["text"], gates.render(driver.work_dir, driver.state(), "intake"))


class AuqChannelSwitchTest(unittest.TestCase):
    """D-34: a text answer to the generation-0 `gate-auq` step switches the channel in `gate parse`."""

    def _plan_gate(self, slug: str) -> tuple[Driver, dict]:
        driver = Driver(temp_root(self), slug=slug)
        action = driver.run_until("plan_approval_pending")
        self.assertEqual("gate-auq", action["kind"])
        self.assertEqual(0, action.get("generation", 0))
        return driver, action

    def test_text_reply_bumps_the_generation_logs_the_switch_and_parses(self):
        driver, action = self._plan_gate("auq-switch")
        result = driver.parse_gate(action, "approve full")

        self.assertEqual("approve", result["action"])
        self.assertEqual(1, result["generation"])
        self.assertTrue(result["channel_switched"])

        state = driver.state()
        row = machine.step_row(state, action["step_id"], action["attempt"])
        self.assertEqual(1, row["generation"])
        self.assertEqual("gate-text", row["kind"])
        self.assertEqual("ok", row["status"])
        self.assertEqual("approved", state["plan_approval"]["status"])

        switched = [
            r for r in events.read_events(driver.work_dir) if r["event"] == "gate_channel_switched"
        ]
        self.assertEqual(1, len(switched), "exactly one channel switch, written by `gate parse`")
        self.assertEqual(0, switched[0]["data"]["from"])
        self.assertEqual(1, switched[0]["data"]["to"])
        self.assertEqual(action["step_id"], switched[0]["data"]["step_id"])

    def test_no_report_no_answer_is_needed_before_the_text_reply(self):
        driver, action = self._plan_gate("auq-no-report")
        driver.parse_gate(action, "approve full")
        self.assertEqual(
            [],
            [
                row
                for row in driver.state()["steps"]
                if row["step_id"] == action["step_id"] and row.get("kind") == "gate-auq"
            ],
        )

    def test_a_text_gate_is_not_switched(self):
        driver = Driver(temp_root(self), slug="auq-text-gate")
        action = driver.run_until("intake_questions_pending")
        result = driver.parse_gate(action, "1A 2B")
        self.assertNotIn("channel_switched", result)
        self.assertEqual(0, result["generation"])
        self.assertEqual(
            [],
            [
                r
                for r in events.read_events(driver.work_dir)
                if r["event"] == "gate_channel_switched"
            ],
        )

    def test_a_stale_generation_is_still_rejected_before_the_switch(self):
        driver, action = self._plan_gate("auq-stale")
        answer = driver.parse_gate({**action, "generation": 2}, "approve full")
        self.assertEqual(["stale_generation"], answer["errors"])
        row = machine.step_row(driver.state(), action["step_id"], action["attempt"])
        self.assertEqual(0, row["generation"])


if __name__ == "__main__":
    unittest.main()
