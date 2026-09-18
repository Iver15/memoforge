"""Tests for scripts/memoforge/i18n.py — language packs and the English floor (D-168)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memoforge import i18n, i18n_en  # noqa: E402
import _i18n  # noqa: E402


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packs = Path(self.tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_english_is_the_code_floor_and_needs_no_file(self):
        self.assertIs(i18n.load("en"), i18n_en.EN)
        self.assertEqual("Risk", i18n.t("en", "memo.risk.label"))

    def test_a_pack_overrides_and_a_missing_key_falls_back_to_english(self):
        _i18n.fake_pack(self.packs, "ru", {"memo.risk.label": "Риск"})
        pack = json.loads((self.packs / "ru.json").read_text(encoding="utf-8"))
        del pack["memo"]["risk"]["levels"]["high"]
        (self.packs / "ru.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        self.assertEqual("Риск", i18n.t("ru", "memo.risk.label"))
        self.assertEqual("high", i18n.t("ru", "memo.risk.levels.high"))

    def test_an_unreadable_memo_pack_is_an_explicit_error_never_english(self):
        (self.packs / "de.json").write_text("{not json", encoding="utf-8")
        self.assertFalse(i18n.available("de"))
        with self.assertRaises(i18n.PackUnavailable) as caught:
            i18n.t("de", "memo.risk.label")
        self.assertEqual("de", caught.exception.code)

    def test_an_unreadable_ui_pack_answers_in_english(self):
        i18n_en.EN["ui"]["_probe"] = "probe"
        self.addCleanup(i18n_en.EN["ui"].pop, "_probe")
        self.assertEqual("probe", i18n.t("fr", "ui._probe"))          # no fr.json at all

    def test_placeholders_are_formatted_and_lists_come_back_raw(self):
        i18n_en.EN["memo"]["_fmt"] = "{count} more"
        self.addCleanup(i18n_en.EN["memo"].pop, "_fmt")
        self.assertEqual("3 more", i18n.t("en", "memo._fmt", count=3))
        self.assertIsInstance(i18n.node("en", "memo.placeholders"), list)

    def test_normalize(self):
        self.assertEqual("ru", i18n.normalize(" RU "))
        self.assertIsNone(i18n.normalize("it"))
        self.assertIsNone(i18n.normalize(None))


if __name__ == "__main__":
    unittest.main()
