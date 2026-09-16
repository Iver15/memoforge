"""Tests for scripts/memoforge/modes.py — the mode matrix and config resolution (ТЗ §2.3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import events, limits, modes, routing  # noqa: E402

# D-148: six bundled servers; `ldh` in Full is 10, not 16 — the free plan's day ends at 14-15 calls.
# D-160: `fedregs` (US federal regulations) is the seventh.
# D-161: `lex` (UK legislation, explanatory notes and amendments by i.AI) is the eighth.
BRIEF_BUDGET = {
    "ldh": 8,
    "courtlistener": 10,
    "legalviz": 10,
    "uklegal": 10,
    "justicelibre": 10,
    "opencaselaw": 10,
    "fedregs": 10,
    "lex": 10,
}
FULL_BUDGET = {
    "ldh": 10,
    "courtlistener": 40,
    "legalviz": 40,
    "uklegal": 40,
    "justicelibre": 40,
    "opencaselaw": 40,
    "fedregs": 40,
    "lex": 40,
}

# Literal transcription of the ТЗ §2.3 table.
SPEC_MATRIX = {
    "brief": {
        "researcher_layers": ["statutes"],
        "reviewer_list": ["logic", "citations", "counterarguments"],
        "max_iterations": 2,
        "client_polish_enabled": False,
        "max_client_polish": 0,
        "template_id": "executive-brief",
        "source_review_gate": "off",
        "lint_fix_rounds": 1,
        "intake_max_questions": 10,
        "mcp_budget": BRIEF_BUDGET,
    },
    "full": {
        "researcher_layers": ["statutes", "case_law", "doctrine"],
        "reviewer_list": ["logic", "form", "citations", "counterarguments"],
        "max_iterations": 2,
        "client_polish_enabled": True,
        "max_client_polish": 1,
        "template_id": "classical-memo",
        "source_review_gate": "auto",
        "lint_fix_rounds": 2,
        "intake_max_questions": 10,
        "mcp_budget": FULL_BUDGET,
    },
}


class ModeMatrixTest(unittest.TestCase):
    def test_only_two_modes(self):
        self.assertEqual(sorted(modes.MODES), ["brief", "full"])

    def test_matrix_matches_spec(self):
        self.assertEqual(modes.MODES, SPEC_MATRIX)

    def test_intake_max_questions_comes_from_limits(self):
        for mode in modes.MODES.values():
            self.assertEqual(mode["intake_max_questions"], limits.INTAKE_MAX_QUESTIONS)

    def test_normalize_mode(self):
        self.assertEqual(modes.normalize_mode("Full"), "full")
        self.assertEqual(modes.normalize_mode(" brief "), "brief")
        self.assertIsNone(modes.normalize_mode(None))
        self.assertIsNone(modes.normalize_mode(""))
        with self.assertRaises(ValueError):
            modes.normalize_mode("turbo")


class ResolveConfigTest(unittest.TestCase):
    def test_full_config(self):
        config = modes.resolve_config("full", {})
        self.assertEqual(config["reviewer_list"], ["logic", "form", "citations", "counterarguments"])
        self.assertEqual(config["researcher_layers"], ["statutes", "case_law", "doctrine"])
        self.assertEqual(config["max_iterations"], 2)
        self.assertEqual(config["lint_fix_rounds"], 2)
        self.assertEqual(config["template_id"], "classical-memo")
        self.assertTrue(config["client_polish_enabled"])
        self.assertEqual(config["max_client_polish"], 1)
        self.assertEqual(config["mcp_budget"], FULL_BUDGET)

    def test_brief_config(self):
        config = modes.resolve_config("brief", {})
        self.assertEqual(config["reviewer_list"], ["logic", "citations", "counterarguments"])
        self.assertEqual(config["researcher_layers"], ["statutes"])
        self.assertEqual(config["max_iterations"], 2)
        self.assertEqual(config["template_id"], "executive-brief")
        self.assertFalse(config["client_polish_enabled"])
        self.assertEqual(config["max_client_polish"], 0)
        self.assertEqual(config["mcp_budget"], BRIEF_BUDGET)

    def test_every_bundled_server_has_a_budget_and_a_daily_ceiling(self):
        """D-148: a server without a budget line is invisible to the plan gate's estimate."""
        for mode, row in modes.MODES.items():
            with self.subTest(mode=mode):
                self.assertEqual(sorted(row["mcp_budget"]), sorted(routing.MCP_SERVERS))
                for name, value in row["mcp_budget"].items():
                    self.assertLessEqual(value, limits.MCP_PROVIDER_DAILY_LIMITS[name], name)

    def test_the_full_ldh_budget_stays_under_the_observed_daily_quota(self):
        """analysis/39 §9.3: the free plan answered 14 calls and refused the 15th; 16 was a lie."""
        self.assertEqual(10, modes.MODES["full"]["mcp_budget"]["ldh"])
        self.assertEqual(
            limits.MCP_PROVIDER_DAILY_LIMITS["ldh"], modes.MODES["full"]["mcp_budget"]["ldh"]
        )

    def test_config_is_a_copy_not_a_reference(self):
        config = modes.resolve_config("full", {})
        config["reviewer_list"].append("bogus")
        config["mcp_budget"]["ldh"] = 999
        self.assertEqual(modes.MODES["full"]["reviewer_list"], SPEC_MATRIX["full"]["reviewer_list"])
        self.assertEqual(modes.MODES["full"]["mcp_budget"], FULL_BUDGET)

    def test_no_mode_yet_has_no_mode_fields(self):
        config = modes.resolve_config(None, {})
        self.assertEqual(config["intake_max_questions"], limits.INTAKE_MAX_QUESTIONS)
        self.assertEqual(config["source_review_gate"], "auto")
        for key in ("reviewer_list", "researcher_layers", "max_iterations", "template_id"):
            self.assertNotIn(key, config)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            modes.resolve_config("turbo", {})


class SourceReviewGateChainTest(unittest.TestCase):
    """`userConfig` explicit on|off > mode > auto (ТЗ §2.3)."""

    def test_mode_default_when_user_config_is_auto(self):
        self.assertEqual(modes.resolve_config("brief", {"source_review_gate": "auto"})["source_review_gate"], "off")
        self.assertEqual(modes.resolve_config("full", {"source_review_gate": "auto"})["source_review_gate"], "auto")

    def test_mode_default_when_user_config_absent(self):
        self.assertEqual(modes.resolve_config("brief", {})["source_review_gate"], "off")
        self.assertEqual(modes.resolve_config("full", {})["source_review_gate"], "auto")

    def test_user_config_on_overrides_mode(self):
        for mode in ("brief", "full"):
            config = modes.resolve_config(mode, {"source_review_gate": "on"})
            self.assertEqual(config["source_review_gate"], "on")

    def test_user_config_off_overrides_mode(self):
        for mode in ("brief", "full"):
            config = modes.resolve_config(mode, {"source_review_gate": "off"})
            self.assertEqual(config["source_review_gate"], "off")

    def test_without_mode_the_gate_is_auto(self):
        self.assertEqual(modes.resolve_source_review_gate(None, {}), "auto")
        self.assertEqual(modes.resolve_source_review_gate(None, {"source_review_gate": "on"}), "on")

    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            modes.resolve_config("full", {"source_review_gate": "sometimes"})


class WriterModelTest(unittest.TestCase):
    def test_default_is_opus(self):
        self.assertEqual(modes.resolve_config("full", {})["writer_model"], modes.DEFAULT_WRITER_MODEL)
        self.assertEqual(modes.DEFAULT_WRITER_MODEL, "opus")

    def test_allowed_value_passes(self):
        config = modes.resolve_config("full", {"writer_model": "sonnet"})
        self.assertEqual(config["writer_model"], "sonnet")

    def test_unknown_model_falls_back_to_opus(self):
        """ТЗ §4.1 / D-15: an unknown value degrades to `opus` and is reported, never raised."""
        config = modes.resolve_config("full", {"writer_model": "gpt"})
        self.assertEqual(config["writer_model"], modes.DEFAULT_WRITER_MODEL)
        self.assertEqual(config[modes.WRITER_MODEL_FALLBACK_KEY], "gpt")

    def test_an_allowed_model_reports_no_fallback(self):
        config = modes.resolve_config("full", {"writer_model": "sonnet"})
        self.assertNotIn(modes.WRITER_MODEL_FALLBACK_KEY, config)
        self.assertNotIn(modes.WRITER_MODEL_FALLBACK_KEY, modes.resolve_config("full", {}))

    def test_the_fallback_key_is_named_after_the_event(self):
        self.assertEqual(modes.WRITER_MODEL_FALLBACK_KEY, "writer_model_fallback")
        self.assertIn("writer_model_fallback", events.EVENT_TYPES)

    def test_allowlist_lives_in_limits(self):
        for model in limits.ALLOWED_WRITER_MODELS:
            self.assertEqual(modes.resolve_config(None, {"writer_model": model})["writer_model"], model)


class PassthroughTest(unittest.TestCase):
    def test_the_hook_options_never_reach_state_config(self):
        """D-74: only the hooks read `stop_guard`/`websearch_autoallow`, and they read the env."""
        given = modes.resolve_config(
            "full", {"dashboard": True, "stop_guard": True, "websearch_autoallow": False}
        )
        default = modes.resolve_config("full", {})
        for name in ("stop_guard", "websearch_autoallow"):
            self.assertNotIn(name, given, name)
            self.assertNotIn(name, default, name)

    def test_dashboard_is_resolved_into_state_config(self):
        """D-87: `mf next` reads `config.dashboard` to decide whether its answer carries §7.5."""
        self.assertIs(modes.resolve_config("full", {"dashboard": True})["dashboard"], True)
        self.assertIs(modes.resolve_config(None, {"dashboard": False})["dashboard"], False)

    def test_the_dashboard_default_is_on(self):
        """D-92: the manifest default is `true`; an unset option must resolve the same way."""
        self.assertIs(modes.DEFAULT_DASHBOARD, True)
        self.assertIs(modes.resolve_config("full", {})["dashboard"], True)
        self.assertIs(modes.resolve_config(None, {})["dashboard"], True)

    def test_the_style_profile_keys_are_copied(self):
        config = modes.resolve_config(
            "full",
            {
                "style_profile": "house",
                "style_profile_path": "styles/house",
                "style_profile_mode_binding": "full",
            },
        )
        self.assertEqual("house", config["style_profile"])
        self.assertEqual("styles/house", config["style_profile_path"])
        self.assertEqual("full", config["style_profile_mode_binding"])
        self.assertIsNone(modes.resolve_config("full", {})["style_profile"])

    def test_launcher_fields_are_copied_when_present(self):
        config = modes.resolve_config(None, {"python_cmd": ["py", "-3"], "plugin_data_dir": "X"})
        self.assertEqual(config["python_cmd"], ["py", "-3"])
        self.assertEqual(config["plugin_data_dir"], "X")


if __name__ == "__main__":
    unittest.main()
