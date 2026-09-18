"""Tests for scripts/memoforge/fallbacks.py — the degradation table (M9, M11, ТЗ §9 «единственность»)."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import fallbacks, i18n, phases  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402

PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
ROW_KEYS = {"condition_key", "phase", "action", "banner_id", "banner_text", "banner_params"}


class ShapeTest(unittest.TestCase):
    def test_every_row_has_exactly_the_declared_keys(self):
        for row in fallbacks.FALLBACKS:
            with self.subTest(condition=row.get("condition_key")):
                self.assertEqual(set(row), ROW_KEYS)

    def test_condition_keys_are_unique(self):
        keys = [row["condition_key"] for row in fallbacks.FALLBACKS]
        self.assertEqual(len(keys), len(set(keys)), "a condition key must identify one row")

    def test_banner_ids_are_unique(self):
        ids = [row["banner_id"] for row in fallbacks.FALLBACKS if row["banner_id"] is not None]
        self.assertEqual(len(ids), len(set(ids)), "a banner id must identify one row")
        self.assertEqual(sorted(ids), sorted(fallbacks.BANNER_IDS))

    def test_identifiers_are_snake_case(self):
        for row in fallbacks.FALLBACKS:
            self.assertRegex(row["condition_key"], r"^[a-z][a-z0-9_]*$")
            if row["banner_id"] is not None:
                self.assertRegex(row["banner_id"], r"^[a-z][a-z0-9_]*$")

    def test_by_condition_index_matches_the_table(self):
        self.assertEqual(len(fallbacks.BY_CONDITION), len(fallbacks.FALLBACKS))
        for row in fallbacks.FALLBACKS:
            self.assertIs(fallbacks.get(row["condition_key"]), row)

    def test_phases_are_real_phases_or_none(self):
        for row in fallbacks.FALLBACKS:
            if row["phase"] is not None:
                self.assertTrue(phases.is_phase(row["phase"]), row["phase"])

    def test_every_row_states_an_action(self):
        for row in fallbacks.FALLBACKS:
            self.assertTrue(row["action"].strip(), row["condition_key"])


class BannerTextTest(unittest.TestCase):
    def test_every_banner_id_has_text(self):
        for row in fallbacks.FALLBACKS:
            with self.subTest(condition=row["condition_key"]):
                if row["banner_id"] is None:
                    self.assertIsNone(row["banner_text"])
                else:
                    self.assertTrue(row["banner_text"], "a banner id must carry a user-facing text")

    def test_declared_params_match_the_placeholders_in_the_text(self):
        for row in fallbacks.FALLBACKS:
            with self.subTest(condition=row["condition_key"]):
                placeholders = set(PLACEHOLDER.findall(row["banner_text"] or ""))
                self.assertEqual(placeholders, set(row["banner_params"]))

    def test_banner_renders_for_every_row(self):
        for row in fallbacks.FALLBACKS:
            with self.subTest(condition=row["condition_key"]):
                params = {name: f"<{name}>" for name in row["banner_params"]}
                banner = fallbacks.banner(row["condition_key"], **params)
                if row["banner_id"] is None:
                    self.assertIsNone(banner)
                    continue
                self.assertEqual(banner["banner_id"], row["banner_id"])
                self.assertEqual(banner["condition_key"], row["condition_key"])
                self.assertNotRegex(banner["text"], PLACEHOLDER)
                for name in row["banner_params"]:
                    self.assertIn(f"<{name}>", banner["text"])

    def test_missing_params_are_rejected(self):
        rows = [row for row in fallbacks.FALLBACKS if row["banner_params"]]
        self.assertTrue(rows, "the table must exercise the parameter path at least once")
        with self.assertRaises(ValueError):
            fallbacks.banner(rows[0]["condition_key"])

    def test_unknown_condition_key_raises(self):
        with self.assertRaises(KeyError):
            fallbacks.get("no_such_condition")


class BannerLanguageTest(unittest.TestCase):
    """D-175: the deliverable prints `memo.banners.<id>` of the memo language at render time."""

    def setUp(self) -> None:
        from unittest import mock

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", {})

    def test_a_banner_is_rendered_in_the_memo_language_from_id_and_params(self):
        _i18n.fake_pack(
            self.packs,
            "ru",
            {"memo.banners.mcp_partial": "Частичное покрытие MCP — доступен только {available}."},
        )
        row = fallbacks.banner("mcp_partial", available="legalviz")
        self.assertEqual(
            "Частичное покрытие MCP — доступен только legalviz.",
            fallbacks.banner_text_for(row, "ru"),
        )
        self.assertEqual(row["text"], fallbacks.banner_text_for(row, "en"))

    def test_a_legacy_row_without_params_an_unknown_id_and_a_string_row_keep_their_text(self):
        legacy = {
            "banner_id": "mcp_partial",
            "text": "Partial MCP coverage — only legalviz was reachable.",
        }
        self.assertEqual(legacy["text"], fallbacks.banner_text_for(legacy, "ru"))
        self.assertEqual("x", fallbacks.banner_text_for({"banner_id": "nope", "text": "x"}, "ru"))
        self.assertEqual("plain", fallbacks.banner_text_for("plain", "ru"))

    def test_every_banner_id_has_an_english_pack_entry_with_the_same_text(self):
        for row in fallbacks.FALLBACKS:
            if row["banner_id"] is None:
                continue
            with self.subTest(banner=row["banner_id"]):
                self.assertEqual(
                    row["banner_text"], i18n.node("en", f"memo.banners.{row['banner_id']}")
                )


class SoftCapRowTest(unittest.TestCase):
    """D-166: the soft cap of the free servers has a row with the server and the count."""

    def test_the_soft_cap_row_carries_the_server_and_the_count(self):
        row = fallbacks.get("mcp_soft_cap_exceeded")
        self.assertEqual("mcp_soft_cap_exceeded", row["banner_id"])
        self.assertEqual(["server", "count"], row["banner_params"])
        banner = fallbacks.banner("mcp_soft_cap_exceeded", server="legalviz", count=120)
        self.assertIn("legalviz", banner["text"])
        self.assertIn("120", banner["text"])


class CoverageTest(unittest.TestCase):
    def test_the_always_deliver_rows_of_the_spec_are_present(self):
        """M9 anchors: the export branches and the two salvage rows must exist."""
        for key in (
            "docx_render_failed",
            "docx_invalid",
            "unresolved_reference_in_fallback",
            "no_checked_draft",
            "output_folder_write_failed",
            "salvage_state_corrupt",
            "universal_fallback",
        ):
            self.assertIn(key, fallbacks.BY_CONDITION)

    def test_no_removed_v1_subject_survives(self):
        """§0.4: the mid-run heartbeat gate, the research-summary mode and pandoc are gone."""
        blob = " ".join(
            str(value) for row in fallbacks.FALLBACKS for value in row.values() if value
        ).lower()
        for identifier in ("heartbeat", "pandoc", "research-summary", "research_summary"):
            self.assertNotIn(identifier, blob)


if __name__ == "__main__":
    unittest.main()
