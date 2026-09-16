"""Tests for scripts/memoforge/phases.py — the single source of truth for phases (ТЗ §2.1, M11)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import phases  # noqa: E402

# Literal transcription of the ТЗ §2.1 table (rows 1..16).
SPEC_PHASES = [
    "intake_preliminary_research",
    "intake_questions_pending",
    "planning",
    "plan_approval_pending",
    "research",
    "research_sufficiency",
    "research_sufficiency_followup_pending",
    "research_insufficient_pending",
    "currency_check",
    "source_pack",
    "source_review_pending",
    "drafting",
    "revision_loop",
    "client_readiness",
    "export",
    "done",
    "failed",
    "cancelled_by_user",
]

REMOVED_IN_V2 = ("mode_pick_pending",)  # the full removed set is covered by SPEC_PHASES equality


class PhaseListTest(unittest.TestCase):
    def test_phase_order_matches_spec(self):
        self.assertEqual(phases.PHASES, SPEC_PHASES)

    def test_no_duplicates(self):
        self.assertEqual(len(phases.PHASES), len(set(phases.PHASES)))

    def test_removed_v1_phases_are_absent(self):
        for phase in REMOVED_IN_V2:
            self.assertNotIn(phase, phases.PHASES)

    def test_terminal_phases(self):
        self.assertEqual(phases.TERMINAL, ("done", "failed", "cancelled_by_user"))
        for phase in phases.TERMINAL:
            self.assertIn(phase, phases.PHASES)
            self.assertTrue(phases.is_terminal(phase))
        self.assertFalse(phases.is_terminal("research"))

    def test_gate_phases(self):
        self.assertEqual(
            phases.GATES,
            (
                "intake_questions_pending",
                "plan_approval_pending",
                "research_sufficiency_followup_pending",
                "research_insufficient_pending",
                "source_review_pending",
            ),
        )
        for phase in phases.GATES:
            self.assertIn(phase, phases.PHASES)
            self.assertTrue(phases.is_gate(phase))
        self.assertFalse(phases.is_gate("drafting"))

    def test_non_terminal_excludes_terminal(self):
        self.assertEqual(len(phases.NON_TERMINAL), len(phases.PHASES) - 3)
        for phase in phases.NON_TERMINAL:
            self.assertFalse(phases.is_terminal(phase))

    def test_phase_index_is_the_declaration_order(self):
        for index, phase in enumerate(phases.PHASES):
            self.assertEqual(phases.PHASE_INDEX[phase], index)

    def test_initial_phase_is_the_first_row(self):
        self.assertEqual(phases.INITIAL_PHASE, phases.PHASES[0])

    def test_is_phase(self):
        self.assertTrue(phases.is_phase("export"))
        self.assertFalse(phases.is_phase("nope"))
        self.assertFalse(phases.is_phase(None))


class PhaseLabelTest(unittest.TestCase):
    """D-95: every phase has one plain-English name, and nothing user-facing invents another."""

    def test_every_phase_has_a_label(self):
        self.assertEqual(sorted(phases.PHASE_LABELS), sorted(phases.PHASES))
        for phase in phases.PHASES:
            with self.subTest(phase=phase):
                label = phases.label(phase)
                self.assertTrue(label.strip(), phase)
                self.assertNotIn("_", label, "a label is prose, not the phase name")
                self.assertEqual(label[0], label[0].upper())

    def test_the_labels_of_the_phases_the_user_meets_most(self):
        self.assertEqual(phases.label("research"), "Legal research")
        self.assertEqual(phases.label("plan_approval_pending"), "Plan approval")
        self.assertEqual(phases.label("export"), "Exporting the memo (DOCX)")
        self.assertEqual(phases.label("cancelled_by_user"), "Cancelled")

    def test_an_unknown_phase_degrades_to_its_own_name(self):
        self.assertEqual(phases.label("made_up"), "made_up")
        self.assertEqual(phases.label(None), "")


class SchemaEnumIdentityTest(unittest.TestCase):
    """`schemas/state.schema.json` duplicates the enum literally; it must stay identical (M11)."""

    def setUp(self):
        path = PLUGIN_ROOT / "schemas" / "state.schema.json"
        self.schema = json.loads(path.read_text(encoding="utf-8-sig"))

    def test_state_schema_phase_enum_matches_phases_py(self):
        enum = self.schema["$defs"]["phase"]["enum"]
        self.assertEqual(enum, phases.PHASES)

    def test_progress_route_uses_the_same_phase_definition(self):
        route = self.schema["$defs"]["progress"]["properties"]["route"]
        self.assertEqual(route["items"], {"$ref": "#/$defs/phase"})

    def test_step_phase_uses_the_same_phase_definition(self):
        step = self.schema["$defs"]["step"]["properties"]["phase"]
        self.assertEqual(step, {"$ref": "#/$defs/phase"})


if __name__ == "__main__":
    unittest.main()
