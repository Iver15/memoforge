"""Tests for scripts/memoforge/sufficiency.py — phase 6 routing (ТЗ §2.1 стр.6–7, §9)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import limits, modes, schema, state_io, sufficiency, task  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sufficiency"
TASK_ID = "memo-20260908T120000Z-fixture"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8-sig"))


def new_task(
    root: Path,
    *,
    layers: list[str] | None = None,
    user_followup_used: int = 0,
    research_followup_used: int = 0,
) -> Path:
    """A task at `research_sufficiency`; `layers` narrows `config.researcher_layers` (D-112)."""
    work_dir = root / TASK_ID
    task.create_work_dir_tree(work_dir)
    state = task.build_initial_state(
        task_id=TASK_ID,
        user_query="Biometric data of minors under the GDPR",
        language="en",
        work_dir=work_dir,
        output_folder=root,
        config=modes.resolve_config("full"),
    )
    if layers is not None:
        state["config"]["researcher_layers"] = list(layers)
    state["current_phase"] = "research_sufficiency"
    # D-116: two budgets — asking the user and re-dispatching a researcher are charged apart.
    state["attempts"]["sufficiency_user_followup"] = user_followup_used
    state["attempts"]["sufficiency_research_followup"] = research_followup_used
    state_io.create_state(work_dir, state)
    return work_dir


def counters(work_dir: Path) -> tuple[int, int]:
    """`(sufficiency_user_followup, sufficiency_research_followup)` as state holds them (D-116)."""
    attempts = state_io.read_state(work_dir)["attempts"]
    return (
        int(attempts.get("sufficiency_user_followup", 0)),
        int(attempts.get("sufficiency_research_followup", 0)),
    )


def put(work_dir: Path, name: str) -> None:
    target = work_dir / sufficiency.SUFFICIENCY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / f"{name}.json", target)


def issue_step(work_dir: Path, step_id: str, attempt: int = 1) -> None:
    """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

    def mutator(state: dict) -> None:
        rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
        if any(row.get("step_id") == step_id and int(row.get("attempt") or 1) == attempt for row in rows):
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
    from memoforge import stepctx

    entry = {
        "canonical_path": canonical,
        "sha256": state_io.sha256_file(work_dir / canonical),
        "by": "step",
        "step_id": "s-050",
        "at": "2026-09-08T12:00:00.000Z",
    }
    state_io.write_state(work_dir, lambda state: stepctx.merge_published(state, [entry]))


def route_args(work_dir: Path, step: str = "s-060") -> argparse.Namespace:
    if step:
        issue_step(work_dir, step)
    return argparse.Namespace(workdir=str(work_dir), step=step, attempt=1, human=False)


class PureRoutingTest(unittest.TestCase):
    """§2.1 стр.6 — the outcome table, first match wins."""

    def test_sufficient_goes_to_currency_check(self):
        decision = sufficiency.route(fixture("sufficient"))
        self.assertEqual(sufficiency.NEXT_CURRENCY, decision["next"])
        self.assertFalse(decision["spend_budget"])

    def test_insufficient_goes_to_the_gate(self):
        decision = sufficiency.route(fixture("insufficient"))
        self.assertEqual(sufficiency.NEXT_INSUFFICIENT, decision["next"])
        self.assertFalse(decision["spend_budget"])

    def test_subset_u_goes_to_the_followup_gate(self):
        decision = sufficiency.route(fixture("followup-user"))
        self.assertEqual(sufficiency.NEXT_GATE, decision["next"])
        self.assertTrue(decision["spend_user_budget"])
        self.assertEqual(1, len(decision["subset_u"]))
        self.assertEqual(["case_law"], decision["layers"])

    def test_a_user_gap_does_not_spend_the_research_budget(self):
        """D-116: the gate is charged to the user counter; only the layers it carries cost research."""
        decision = sufficiency.route(fixture("followup-user"), layers=["statutes"])
        self.assertEqual(sufficiency.NEXT_GATE, decision["next"])
        self.assertTrue(decision["spend_user_budget"])
        self.assertFalse(decision["spend_research_budget"], "no layer is re-dispatched, no cost")

    def test_subset_u_without_either_budget_becomes_drafting_warnings(self):
        decision = sufficiency.route(
            fixture("followup-user"),
            user_followup_used=limits.MAX_SUFFICIENCY_USER_FOLLOWUP,
            research_followup_used=limits.MAX_SUFFICIENCY_RESEARCH_FOLLOWUP,
        )
        self.assertEqual(sufficiency.NEXT_CURRENCY, decision["next"])
        self.assertFalse(decision["spend_budget"])
        self.assertEqual(2, len(decision["warn_gaps"]))

    def test_a_spent_user_budget_still_leaves_the_research_one(self):
        """D-116: the question cannot be asked again, but the missing layer is still affordable."""
        decision = sufficiency.route(
            fixture("followup-user"),
            user_followup_used=limits.MAX_SUFFICIENCY_USER_FOLLOWUP,
        )
        self.assertEqual(sufficiency.NEXT_RESEARCH, decision["next"])
        self.assertEqual(["case_law"], decision["layers"])
        self.assertFalse(decision["spend_user_budget"])
        self.assertTrue(decision["spend_research_budget"])
        self.assertEqual(1, len(decision["warn_gaps"]), "the unanswered user gap is caveated")

    def test_the_user_budget_is_two(self):
        """D-116: a question to the user costs ~100 s of waiting — two of them per run."""
        self.assertEqual(2, limits.MAX_SUFFICIENCY_USER_FOLLOWUP)
        decision = sufficiency.route(fixture("followup-user"), user_followup_used=1)
        self.assertEqual(sufficiency.NEXT_GATE, decision["next"])

    def test_the_research_budget_is_two_with_no_mode_argument(self):
        """D-242: one mode, one research follow-up budget (D-116)."""
        self.assertEqual(2, limits.MAX_SUFFICIENCY_RESEARCH_FOLLOWUP)
        self.assertFalse(hasattr(limits, "research_followup_limit"))
        left = sufficiency.route(fixture("followup-layers"), research_followup_used=1)
        self.assertEqual(sufficiency.NEXT_RESEARCH, left["next"])
        spent = sufficiency.route(fixture("followup-layers"), research_followup_used=2)
        self.assertEqual(sufficiency.NEXT_CURRENCY, spent["next"])
        with self.assertRaises(TypeError):
            sufficiency.route(fixture("followup-layers"), mode="full")

    def test_subset_r_missing_re_runs_the_named_layers(self):
        decision = sufficiency.route(fixture("followup-layers"))
        self.assertEqual(sufficiency.NEXT_RESEARCH, decision["next"])
        self.assertEqual(["case_law"], decision["layers"])
        self.assertTrue(decision["spend_research_budget"])
        self.assertFalse(decision["spend_user_budget"])

    def test_subset_r_missing_without_budget_becomes_drafting_warnings(self):
        decision = sufficiency.route(
            fixture("followup-layers"),
            research_followup_used=limits.MAX_SUFFICIENCY_RESEARCH_FOLLOWUP,
        )
        self.assertEqual(sufficiency.NEXT_CURRENCY, decision["next"])
        self.assertEqual([], decision["layers"])

    def test_weak_only_never_spends_the_budget(self):
        decision = sufficiency.route(fixture("followup-weak"))
        self.assertEqual(sufficiency.NEXT_CURRENCY, decision["next"])
        self.assertFalse(decision["spend_budget"])
        self.assertEqual(["doctrine"], decision["weak_layers"])

    def test_empty_subsets_go_to_currency_check(self):
        document = dict(fixture("followup-weak"), blocking_gaps=[])
        self.assertEqual([], schema.validate(document, "research-sufficiency"))
        self.assertEqual(sufficiency.NEXT_CURRENCY, sufficiency.route(document)["next"])

    def test_a_layer_outside_the_mode_is_not_re_researched(self):
        """D-112: a run researching statutes only routes a `case_law` gap on instead of dispatching."""
        decision = sufficiency.route(fixture("followup-layers"), layers=["statutes"])
        self.assertEqual(sufficiency.NEXT_CURRENCY, decision["next"])
        self.assertEqual([], decision["layers"])
        self.assertEqual([], decision["subset_r"])
        self.assertEqual(["case_law"], [gap["target"] for gap in decision["out_of_scope"]])
        self.assertFalse(decision["spend_budget"])

    def test_the_same_gap_in_full_mode_still_re_runs_the_layer(self):
        decision = sufficiency.route(
            fixture("followup-layers"), layers=["statutes", "case_law", "doctrine"]
        )
        self.assertEqual(sufficiency.NEXT_RESEARCH, decision["next"])
        self.assertEqual(["case_law"], decision["layers"])
        self.assertEqual([], decision["out_of_scope"])

    def test_an_out_of_mode_layer_does_not_hold_the_followup_gate_back(self):
        """D-112: the user gap still opens the gate; the `case_law` gap is not in its subset_r."""
        decision = sufficiency.route(fixture("followup-user"), layers=["statutes"])
        self.assertEqual(sufficiency.NEXT_GATE, decision["next"])
        self.assertEqual([], decision["layers"])
        self.assertEqual([], decision["subset_r"])
        self.assertEqual(1, len(decision["out_of_scope"]))

    def test_a_sufficient_verdict_warns_its_gaps(self):
        """D-238: a gap the reviewer leaves on a `sufficient` verdict is a limitation of the memo."""
        self.assertEqual([], schema.validate(fixture("sufficient-weak"), "research-sufficiency"))
        decision = sufficiency.route(fixture("sufficient-weak"))
        self.assertEqual(sufficiency.NEXT_CURRENCY, decision["next"])
        self.assertFalse(decision["spend_budget"])
        self.assertEqual(3, len(decision["warn_gaps"]))

    def test_partition_splits_user_and_layer_targets(self):
        parts = sufficiency.partition(fixture("insufficient"))
        self.assertEqual(1, len(parts["subset_u"]))
        self.assertEqual(1, len(parts["subset_r"]))
        self.assertEqual(["case_law"], parts["missing_layers"])


class RunRouteTest(unittest.TestCase):
    def test_sufficient_writes_no_followup_and_no_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "sufficient")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("currency_check", result["next"])
            state = state_io.read_state(work_dir)
            self.assertEqual("resolved", state["sufficiency_followup"]["status"])
            self.assertEqual([], state["drafting_warnings"])
            self.assertEqual((0, 0), counters(work_dir))

    def test_a_sufficient_verdict_carries_its_user_gap_as_a_caveat(self):
        """D-238: the gaps become warnings; no question is stored and no follow-up banner is raised."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "sufficient-weak")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("currency_check", result["next"])
            state = state_io.read_state(work_dir)
            self.assertEqual("resolved", state["sufficiency_followup"]["status"])
            messages = [row["message"] for row in state["drafting_warnings"]]
            self.assertEqual(3, len(messages))
            self.assertTrue(any("14-П" in message for message in messages))
            self.assertEqual([], state["sufficiency_followup"].get("questions") or [])
            self.assertEqual([], state.get("fallback_banners") or [])
            self.assertEqual((0, 0), counters(work_dir))

    def test_gate_followup_records_questions_and_charges_both_budgets_apart(self):
        """D-116: the gate costs one user follow-up; the layer it carries costs one research one."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "followup-user")
            first = sufficiency.run_route(route_args(work_dir, step="s-060"))
            self.assertEqual("gate_followup", first["next"])
            self.assertEqual(1, first["questions"])
            self.assertTrue(first["user_budget_spent"])
            self.assertTrue(first["research_budget_spent"], "the gate carried `case_law`")

            state = state_io.read_state(work_dir)
            self.assertEqual((1, 1), counters(work_dir))
            followup = state["sufficiency_followup"]
            self.assertEqual("pending", followup["status"])
            self.assertEqual(1, len(followup["subset_u"]))
            self.assertEqual(1, len(followup["questions"]))
            self.assertIsNotNone(followup["asked_at"])

            # Second pass of the same reviewer: the user budget is 2, so a second question is asked
            # — and the research budget of Full is 2, so the layer may ride along once more.
            second = sufficiency.run_route(route_args(work_dir, step="s-061"))
            self.assertEqual("gate_followup", second["next"])
            self.assertEqual((2, 2), counters(work_dir))

            # Third pass: both budgets are spent, so the gaps become warnings.
            third = sufficiency.run_route(route_args(work_dir, step="s-062"))
            self.assertEqual("currency_check", third["next"])
            self.assertFalse(third["budget_spent"])
            state = state_io.read_state(work_dir)
            self.assertEqual((2, 2), counters(work_dir))
            self.assertTrue(state["drafting_warnings"])
            self.assertIn(
                "assumptions_after_followup",
                [banner["banner_id"] for banner in state["fallback_banners"]],
            )

    def test_a_user_question_leaves_the_research_budget_alone(self):
        """D-116: with `case_law` out of scope the gap is not bought, so the gate is charged to the user."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), layers=["statutes"])
            put(work_dir, "followup-user")
            result = sufficiency.run_route(route_args(work_dir, step="s-060"))
            self.assertEqual("gate_followup", result["next"])
            self.assertTrue(result["user_budget_spent"])
            self.assertFalse(result["research_budget_spent"])
            self.assertEqual((1, 0), counters(work_dir))

    def test_research_subset_returns_the_named_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "followup-layers")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("research_subset", result["next"])
            self.assertEqual(["case_law"], result["layers"])
            state = state_io.read_state(work_dir)
            self.assertEqual((0, 1), counters(work_dir), "a layer costs research, not the user")
            self.assertEqual("research_subset", state["sufficiency_followup"]["status"])
            self.assertEqual(["case_law"], state["sufficiency_followup"][sufficiency.APPROVED_LAYERS])

    def test_the_route_writes_down_the_layers_it_paid_for(self):
        """D-116: the gate carries the layer only while the research budget can pay for it."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "followup-user")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("gate_followup", result["next"])
            followup = state_io.read_state(work_dir)["sufficiency_followup"]
            self.assertEqual(result["layers"], followup[sufficiency.APPROVED_LAYERS])
            self.assertEqual(["case_law"], followup[sufficiency.APPROVED_LAYERS])

    def test_a_gate_the_research_budget_cannot_pay_for_approves_no_layer(self):
        """N-01: `subset_r` still names the gap, but nothing was bought, so nothing is approved."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(
                Path(tmp), research_followup_used=limits.MAX_SUFFICIENCY_RESEARCH_FOLLOWUP
            )
            put(work_dir, "followup-user")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("gate_followup", result["next"])
            self.assertEqual([], result["layers"])
            self.assertFalse(result["research_budget_spent"])
            followup = state_io.read_state(work_dir)["sufficiency_followup"]
            self.assertEqual([], followup[sufficiency.APPROVED_LAYERS])
            self.assertTrue(followup["subset_r"], "the gap is recorded, it is just not affordable")

    def test_weak_gaps_become_drafting_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "followup-weak")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("currency_check", result["next"])
            self.assertEqual(2, result["drafting_warnings_added"])
            state = state_io.read_state(work_dir)
            codes = [row["code"] for row in state["drafting_warnings"]]
            self.assertIn("unresolved_research_gap", codes)
            self.assertIn("sufficiency_warning", codes)
            self.assertEqual((0, 0), counters(work_dir))

    def test_insufficient_routes_to_the_insufficient_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "insufficient")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("insufficient_gate", result["next"])
            self.assertFalse(result["degraded"])
            self.assertEqual((0, 0), counters(work_dir))

    def test_missing_reviewer_output_degrades_to_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("insufficient_gate", result["next"])
            self.assertTrue(result["degraded"])
            state = state_io.read_state(work_dir)
            self.assertIn(
                "sufficiency_unavailable",
                [banner["banner_id"] for banner in state["fallback_banners"]],
            )
            self.assertTrue(state["drafting_warnings"])

    def test_schema_invalid_reviewer_output_degrades_to_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "sufficient")
            broken = fixture("sufficient")
            broken["overall_verdict"] = "insufficient_for_client_ready_memo"  # not a D-01 value
            state_io.write_json_atomic(work_dir / sufficiency.SUFFICIENCY_PATH, broken)
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("insufficient_gate", result["next"])
            self.assertTrue(result["degraded"])

    def test_repeat_of_a_closed_step_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "followup-user")
            first = sufficiency.run_route(route_args(work_dir))
            again = sufficiency.run_route(route_args(work_dir))
            self.assertEqual(first, again)
            self.assertEqual((1, 1), counters(work_dir))


class OutOfScopeGapTest(unittest.TestCase):
    """D-112: a gap outside `config.researcher_layers` is a warning, not a second pass.

    D-242: every run is Full, so these tests narrow `config.researcher_layers` to `statutes`
    to put the `case_law` gap out of scope; the reviewer's own `out_of_scope_gaps` need no narrowing.
    """

    def _messages(self, work_dir: Path) -> list[str]:
        return [row["message"] for row in state_io.read_state(work_dir)["drafting_warnings"]]

    def test_a_case_law_gap_out_of_scope_warns_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), layers=["statutes"])
            put(work_dir, "followup-layers")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("currency_check", result["next"])
            self.assertEqual([], result["layers"])
            self.assertEqual(1, result["out_of_scope_gaps"])
            state = state_io.read_state(work_dir)
            self.assertEqual([], state["sufficiency_followup"]["subset_r"])
            self.assertEqual((0, 0), counters(work_dir), "no budget is spent")
            self.assertEqual(
                [sufficiency.OUT_OF_SCOPE_CODE],
                [row["code"] for row in state["drafting_warnings"]],
            )
            self.assertTrue(
                self._messages(work_dir)[0].startswith("Out of scope for full mode: "),
                self._messages(work_dir),
            )

    def test_the_followup_gate_carries_the_out_of_scope_warning_and_no_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), layers=["statutes"])
            put(work_dir, "followup-user")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("gate_followup", result["next"])
            self.assertEqual([], result["layers"], "an out-of-scope layer is never re-dispatched")
            state = state_io.read_state(work_dir)
            self.assertEqual([], state["sufficiency_followup"]["subset_r"])
            self.assertEqual(1, len(state["drafting_warnings"]))
            self.assertIn("Out of scope for full mode: ", self._messages(work_dir)[0])

    def test_the_second_reviewer_pass_does_not_repeat_the_warning(self):
        """D-112: one warning per gap — the two passes differ only in the `at` of the row."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp), layers=["statutes"])
            put(work_dir, "followup-user")
            # D-116: the user budget is 2, so the second pass asks again; the warning is still one.
            self.assertEqual("gate_followup", sufficiency.run_route(route_args(work_dir, "s-060"))["next"])
            self.assertEqual("gate_followup", sufficiency.run_route(route_args(work_dir, "s-061"))["next"])
            self.assertEqual("currency_check", sufficiency.run_route(route_args(work_dir, "s-062"))["next"])
            out_of_scope = [
                message
                for message in self._messages(work_dir)
                if message.startswith("Out of scope for full mode: ")
            ]
            self.assertEqual(1, len(out_of_scope), self._messages(work_dir))

    def test_an_in_scope_layer_is_still_re_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "followup-layers")
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("research_subset", result["next"])
            self.assertEqual(["case_law"], result["layers"])
            self.assertEqual(0, result["out_of_scope_gaps"])
            self.assertEqual([], state_io.read_state(work_dir)["drafting_warnings"])

    def test_out_of_scope_gaps_of_the_reviewer_become_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            document = dict(
                fixture("sufficient"),
                out_of_scope_gaps=["No CJEU authority was searched for the oversight duty."],
            )
            self.assertEqual([], schema.validate(document, "research-sufficiency"))
            state_io.write_json_atomic(work_dir / sufficiency.SUFFICIENCY_PATH, document)
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("currency_check", result["next"])
            self.assertEqual(1, result["out_of_scope_gaps"])
            self.assertEqual(
                ["Out of scope for full mode: No CJEU authority was searched for the oversight duty."],
                self._messages(work_dir),
            )

    def test_gap_warnings_of_a_german_task_carry_the_german_prefix_and_collapse_with_the_reviewer_line(
        self,
    ):
        """D-175/D-173b: the `memo.warnings` prefix in the memo language; the D-113 pair collapses."""
        from unittest import mock

        from memoforge import i18n

        with tempfile.TemporaryDirectory() as tmp:
            packs = Path(tmp) / "packs"
            packs.mkdir()
            with mock.patch.object(i18n, "PACK_DIR", packs):
                _i18n.fake_pack(
                    packs,
                    "de",
                    {"memo.warnings.out_of_scope_prefix": "Außerhalb des {mode}-Modus: "},
                )
                work_dir = new_task(Path(tmp) / "work", layers=["statutes"])
                state_io.write_state(work_dir, lambda state: state.update(language="de"))
                gap = "Keine EuGH-Rechtsprechung zur Aufsichtspflicht wurde geprüft."
                document = dict(
                    fixture("sufficient"),
                    blocking_gaps=[
                        {
                            "gap": gap,
                            "target": "case_law",
                            "status": "missing",
                            "why_blocking": "Der Anwendungsteil ruhte sonst auf nationalen Entscheidungen.",
                            "followup_question": None,
                        }
                    ],
                    drafting_warnings=[f"{gap} Behandeln Sie den Punkt als offen."],
                )
                self.assertEqual([], schema.validate(document, "research-sufficiency"))
                state_io.write_json_atomic(work_dir / sufficiency.SUFFICIENCY_PATH, document)
                result = sufficiency.run_route(route_args(work_dir))
                self.assertEqual("currency_check", result["next"])
                rows = state_io.read_state(work_dir)["drafting_warnings"]
                codes = [row["code"] for row in rows]
                self.assertEqual(1, codes.count(sufficiency.OUT_OF_SCOPE_CODE), codes)
                collapsed = [row for row in rows if row["code"] == sufficiency.OUT_OF_SCOPE_CODE]
                self.assertEqual(1, len(collapsed))
                self.assertTrue(
                    collapsed[0]["message"].startswith("Außerhalb des full-Modus: "), collapsed
                )


class WarningDeduplicationTest(unittest.TestCase):
    """D-113 addendum: a gap and the reviewer's own warning about it are one line, not two."""

    GAP = "The Article 88 implementing provision of the member state was not reviewed."

    def _document(self, warning: str) -> dict:
        return {
            "reviewer": "research_sufficiency",
            "overall_verdict": "targeted_followup_needed",
            "blocking_gaps": [
                {
                    "gap": self.GAP,
                    "target": "statutes",
                    "status": "weak",
                    "why_blocking": "`research/statutes.json` carries no national text for issue i2.",
                    "followup_question": None,
                }
            ],
            "drafting_warnings": [warning],
        }

    def _route(self, document: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            self.assertEqual([], schema.validate(document, "research-sufficiency"))
            state_io.write_json_atomic(work_dir / sufficiency.SUFFICIENCY_PATH, document)
            sufficiency.run_route(route_args(work_dir))
            return state_io.read_state(work_dir)["drafting_warnings"]

    def test_the_gap_is_dropped_when_the_reviewer_already_says_it_to_the_client(self):
        # The gap sentence repeated as a drafting warning: the client-facing line is the one kept,
        # and the technical `why_blocking` half never reaches the memo.
        # D-238 fix 1: the kept line is still a research gap, so it keeps the gap's code.
        rows = self._route(self._document(f"{self.GAP} Treat the point as open."))
        self.assertEqual(["unresolved_research_gap"], [row["code"] for row in rows])
        self.assertNotIn("statutes.json", rows[0]["message"])

    def test_the_comparison_ignores_case_spacing_and_the_final_stop(self):
        rows = self._route(
            self._document("  the article 88 IMPLEMENTING   provision of the\nmember state was not reviewed")
        )
        self.assertEqual(["unresolved_research_gap"], [row["code"] for row in rows])

    def test_a_sufficient_verdict_keeps_the_reviewer_s_line_under_the_research_gap_code(self):
        """D-238 fix 1: the collapsed pair of a sufficient verdict is one limitation, not an assumption."""
        warning = f"{self.GAP} Treat the point as open."
        rows = self._route(dict(self._document(warning), overall_verdict="sufficient"))
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("unresolved_research_gap", rows[0]["code"])
        self.assertEqual(warning, rows[0]["message"])

    def test_a_warning_about_something_else_keeps_both_lines(self):
        rows = self._route(self._document("Doctrine coverage is thin for the balancing test."))
        self.assertEqual(
            ["unresolved_research_gap", "sufficiency_warning"], [row["code"] for row in rows]
        )


class WarningTextTest(unittest.TestCase):
    """D-215: a drafting warning carries the gap only; `why_blocking` is addressed to the researcher."""

    # Run 74, s-014: the gap sentence, and a `why_blocking` that instructs the researcher.
    GAP = (
        "Issue I4 has no finding on Article 6(11) UK GDPR, which the Data (Use and Access) Act 2025 "
        "inserted. ..."
    )
    WHY = (
        "... The text is already saved in full in uk-gdpr-article-6-lawfulness-of-processing "
        "(paragraphs 11-12, inserted from 5 February 2026), so no new retrieval is needed. "
        "Record a contrary finding under I4 from Article 6(11)(a). ..."
    )

    def _gap(self) -> dict:
        return {
            "gap": self.GAP,
            "target": "statutes",
            "status": "missing",
            "why_blocking": self.WHY,
            "followup_question": None,
        }

    def test_the_warning_message_is_the_gap_text_only(self):
        row = sufficiency._warning(self._gap())
        self.assertEqual(self.GAP, row["message"])
        self.assertNotIn("Record a contrary finding", row["message"])
        self.assertEqual("unresolved_research_gap", row["code"])

    def test_an_out_of_scope_warning_carries_the_gap_only(self):
        # D-216 (carried from D-215): the D-112 warning reaches `summary.md` the same way.
        rows = sufficiency._out_of_scope_warnings({}, [self._gap()], "full")  # noqa: SLF001
        self.assertEqual(["Out of scope for full mode: " + self.GAP], [row["message"] for row in rows])
        self.assertNotIn("Record a contrary finding", rows[0]["message"])

    def test_the_routed_warning_keeps_why_blocking_out_of_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            document = {
                "reviewer": "research_sufficiency",
                "overall_verdict": "targeted_followup_needed",
                "blocking_gaps": [dict(self._gap(), status="weak")],
                "drafting_warnings": [],
            }
            self.assertEqual([], schema.validate(document, "research-sufficiency"))
            state_io.write_json_atomic(work_dir / sufficiency.SUFFICIENCY_PATH, document)
            sufficiency.run_route(route_args(work_dir))
            rows = state_io.read_state(work_dir)["drafting_warnings"]
            self.assertEqual([self.GAP], [row["message"] for row in rows])
            saved = json.loads((work_dir / sufficiency.SUFFICIENCY_PATH).read_text(encoding="utf-8"))
            self.assertEqual(self.WHY, saved["blocking_gaps"][0]["why_blocking"])


class PublishedInputTest(unittest.TestCase):
    """D-41: the verdict is read from the published bytes, not from an edited file."""

    def test_a_sufficiency_file_modified_after_publication_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "insufficient")
            publish(work_dir, sufficiency.SUFFICIENCY_PATH)
            document = fixture("sufficient")
            (work_dir / sufficiency.SUFFICIENCY_PATH).write_text(json.dumps(document), encoding="utf-8")

            result = sufficiency.run_route(route_args(work_dir))

            self.assertEqual(["output_modified_after_publish"], result["errors"])
            self.assertEqual(sufficiency.SUFFICIENCY_PATH, result["path"])
            state = state_io.read_state(work_dir)
            self.assertIsNone(state.get("sufficiency_followup"))
            self.assertIsNone(next(iter(state["steps"]), {}).get("status"), "the step stays open")

    def test_an_untouched_published_file_routes_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            put(work_dir, "sufficient")
            publish(work_dir, sufficiency.SUFFICIENCY_PATH)
            result = sufficiency.run_route(route_args(work_dir))
            self.assertEqual("currency_check", result["next"])


class FixtureShapeTest(unittest.TestCase):
    def test_every_sufficiency_fixture_is_schema_valid(self):
        for path in sorted(FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                document = json.loads(path.read_text(encoding="utf-8-sig"))
                self.assertEqual([], schema.validate(document, "research-sufficiency"))
                # D-01: the baseline value `insufficient_for_client_ready_memo` is gone.
                self.assertIn(
                    document["overall_verdict"],
                    ("sufficient", "targeted_followup_needed", "insufficient"),
                )


if __name__ == "__main__":
    unittest.main()
