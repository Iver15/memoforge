"""Parity of the shipped `lib/i18n/*.json` packs against the English floor (D-168, Task 7).

Unlike the synthetic packs of `_i18n.py` (used by every other task's tests), this module
runs against the real files under `lib/i18n/`: it pins that every pack carries the full
`memo` tree of `i18n_en.EN` with the same placeholders, and that the recognizers built
from each pack actually work.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import i18n, i18n_en, lint  # noqa: E402
from memoforge.docx import oscola  # noqa: E402

PACK_CODES: tuple[str, ...] = ("de", "fr", "es", "ru")


def _leaves(node: object, prefix: str = "") -> dict[str, object]:
    """Dotted key -> leaf value for every leaf of a nested pack dict."""
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            out.update(_leaves(value, dotted))
    else:
        out[prefix] = node
    return out


def _placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", text)))


class PackParityTest(unittest.TestCase):
    """Every shipped pack mirrors `EN`: same keys, same placeholders, usable recognizers."""

    def _pack(self, code: str) -> dict:
        path = PLUGIN_ROOT / "lib" / "i18n" / f"{code}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_every_pack_exists_and_loads(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                pack = self._pack(code)
                self.assertEqual(code, pack["code"])
                self.assertIsInstance(pack["memo"], dict)
                self.assertIsInstance(pack["ui"], dict)
                self.assertEqual(pack, i18n.load(code))

    def test_key_set_equals_en_recursively(self):
        english = _leaves(i18n_en.EN)
        for code in PACK_CODES:
            with self.subTest(code=code):
                self.assertEqual(set(english), set(_leaves(self._pack(code))), code)

    def test_placeholders_of_every_string_leaf_equal_the_english_ones(self):
        english = _leaves(i18n_en.EN)
        for code in PACK_CODES:
            with self.subTest(code=code):
                leaves = _leaves(self._pack(code))
                for dotted, value in english.items():
                    if isinstance(value, str):
                        self.assertIsInstance(leaves[dotted], str, dotted)
                        self.assertEqual(
                            _placeholders(value), _placeholders(leaves[dotted]), dotted
                        )

    def test_every_string_leaf_is_non_empty(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                for dotted, value in _leaves(self._pack(code)).items():
                    if isinstance(value, str):
                        self.assertTrue(value.strip(), dotted)

    def test_section_titles_are_pairwise_distinct_after_normalize_title(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                titles = list(dict(i18n.node(code, "memo.sections")).values())
                normalized = [lint.normalize_title(str(title)) for title in titles]
                self.assertEqual(len(normalized), len(set(normalized)), titles)

    def test_a_section_pinpoint_never_prints_as_a_page_one(self):
        """Final review, finding 2: `s`/`ss` and `p`/`pp` are two different pinpoints, so a
        pack whose printed labels collide (German `S.` for both) silently turns a statutory
        section into a page. Section and page are rendered separately and compared."""
        for code in PACK_CODES:
            with self.subTest(code=code):
                for section, page in (("s", "p"), ("ss", "pp")):
                    self.assertNotEqual(
                        oscola.display_pinpoint(f"{section} 2(1)", code),
                        oscola.display_pinpoint(f"{page} 2(1)", code),
                        f"{code}: `{section}` and `{page}` print the same label",
                    )

    def test_risk_levels_are_pairwise_distinct(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                levels = list(dict(i18n.node(code, "memo.risk.levels")).values())
                self.assertEqual(len(levels), len(set(levels)), levels)

    def test_disclaimer_pattern_compiles_and_matches_probe_disclaimer(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                sentence = i18n.t(code, "memo.probe_disclaimer")
                pattern = re.compile(i18n.t(code, "memo.disclaimer_pattern"), re.IGNORECASE)
                self.assertIsNotNone(pattern.search(sentence), sentence)

    def test_disclaimer_pattern_matches_a_plain_negated_sentence(self):
        """D-178a fix: L-14 must also see an ordinary negated disclaimer, not only the
        "without confirmation" construction a writer produces after the L-14 hint."""
        must_match = {
            "de": [
                "Die Annahmen dieses Memos wurden ohne Bestätigung übernommen.",
                "Die zugrunde gelegten Annahmen sind unbestätigt.",
            ],
            "fr": [
                "Les hypothèses de cette note ont été retenues sans confirmation.",
                "Les hypothèses n'ont pas été confirmées par le client.",
                "Ces hypothèses ne sont pas confirmées.",
            ],
            "es": [
                "Los supuestos de este memorando se han adoptado sin confirmación.",
                "Las hipótesis no han sido confirmadas por el cliente.",
                "Los supuestos no fueron confirmados.",
            ],
            "ru": [
                "Допущения настоящего мемо приняты без подтверждения.",
                "Допущения не были подтверждены клиентом, поэтому выводы предварительные.",
                "Принятые допущения клиент не подтвердил.",
            ],
        }
        must_not_match = {
            "de": ["Die Annahmen wurden vom Mandanten bestätigt."],
            "fr": ["Les hypothèses ont été confirmées par le client."],
            "es": ["Los supuestos fueron confirmados por el cliente."],
            "ru": ["Допущения подтверждены клиентом."],
        }
        for code in PACK_CODES:
            with self.subTest(code=code):
                pattern = re.compile(i18n.t(code, "memo.disclaimer_pattern"), re.IGNORECASE)
                self.assertIsNotNone(
                    pattern.search(i18n.t(code, "memo.probe_disclaimer")),
                    i18n.t(code, "memo.probe_disclaimer"),
                )
                for sentence in must_match[code]:
                    self.assertIsNotNone(pattern.search(sentence), sentence)
                for sentence in must_not_match[code]:
                    self.assertIsNone(pattern.search(sentence), sentence)

    def test_grammar_builds_for_every_pack(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                grammar = lint.grammar(code)
                self.assertEqual(code, grammar.language)

    def test_status_lead_keeps_a_non_empty_fixed_prefix(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                lead = i18n.t(code, "memo.labels.status_lead")
                self.assertTrue(lead.split("{", 1)[0].strip(), lead)

    def test_months_have_twelve_entries(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                months = i18n.node(code, "memo.months")
                self.assertIsInstance(months, list)
                self.assertEqual(12, len(months))

    def test_every_english_banner_id_is_present(self):
        english_ids = set(i18n_en.EN["memo"]["banners"])
        for code in PACK_CODES:
            with self.subTest(code=code):
                self.assertEqual(english_ids, set(dict(i18n.node(code, "memo.banners")).keys()))

    def test_lists_are_lists_and_the_boolean_is_a_boolean(self):
        for code in PACK_CODES:
            with self.subTest(code=code):
                for key in ("memo.placeholders", "memo.abbreviations", "memo.lint_off",
                            "memo.ai_tells", "memo.months",
                            "memo.labels.source_pack_columns"):
                    self.assertIsInstance(i18n.node(code, key), list, key)
                self.assertIsInstance(i18n.node(code, "memo.placeholders_ignore_case"), bool)

    def test_list_elements_are_non_empty_strings_of_the_english_length(self):
        """Finding 1/5: a pack must not swap a header list for a string or ship a blank
        header/month. Only the structurally fixed lists (`months`, `source_pack_columns`)
        keep the English length — `lint_off`, `placeholders` and `abbreviations` are
        language-specific by design."""
        english = _leaves(i18n_en.EN)
        for code in PACK_CODES:
            with self.subTest(code=code):
                leaves = _leaves(self._pack(code))
                for dotted, value in english.items():
                    if not isinstance(value, list):
                        continue
                    node = leaves[dotted]
                    self.assertIsInstance(node, list, dotted)
                    if dotted in ("memo.months", "memo.labels.source_pack_columns"):
                        self.assertEqual(len(value), len(node), dotted)
                    for element in node:
                        self.assertIsInstance(element, str, dotted)
                        self.assertTrue(element.strip(), dotted)


    def test_banners_carry_no_backticks(self):
        """Post-run fix: a backtick renders literally in the client docx, so no banner
        string of a shipped pack may contain one (English stays frozen and keeps them)."""
        for code in PACK_CODES:
            with self.subTest(code=code):
                for banner_id, text in dict(i18n.node(code, "memo.banners")).items():
                    self.assertNotIn("`", text, f"{code}: `{banner_id}`")

    def test_citation_label_stems_are_abbreviations(self):
        """Post-run fix: the sentence splitter must not cut after a label the pack itself
        prints, so every dotted citation label stem must be in `memo.abbreviations`.
        Labels without a trailing dot (full words like `Abschnitt`) are skipped."""
        for code in PACK_CODES:
            with self.subTest(code=code):
                abbreviations = set(i18n.node(code, "memo.abbreviations"))
                citation = dict(i18n.node(code, "memo.citation"))
                for key in ("art", "arts", "para", "paras", "reg", "regs", "s", "ss", "p", "pp"):
                    label = citation[key]
                    if not label.endswith("."):
                        continue
                    stem = label[:-1].lower().replace(" ", "")
                    self.assertIn(stem, abbreviations, f"{code}: `{key}` -> `{label}`")

    def test_ru_section_label_is_statya(self):
        """Post-run fix: a UK Act section is «статья» in Russian legal usage."""
        self.assertEqual("ст.", i18n.node("ru", "memo.citation.s"))
        self.assertEqual("ст.", i18n.node("ru", "memo.citation.ss"))


if __name__ == "__main__":
    unittest.main()
