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

_CANONICAL_BACKTICK_TOKENS: frozenset[str] = frozenset(
    {
        # D-176a: the text-channel tokens are never translated and stand unchanged
        # inside their backticks in every pack.
        "approve",
        "edit:",
        "cancel",
        "proceed",
        "continue",
        "brief",
        "full",
        "standard",
        "style:",
        "sources:",
        "1A",
        "3:",
    }
)
"""Tokens of the D-176a text channel whose backticked occurrence must survive verbatim."""


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
        """The full tree (`memo` + `ui`) is complete in every pack and no pack carries a key
        `EN` does not have (D-168 packs, plan 56 task 6)."""
        english = _leaves(i18n_en.EN)
        for code in PACK_CODES:
            with self.subTest(code=code):
                leaves = _leaves(self._pack(code))
                self.assertEqual(set(english), set(leaves), code)

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

    def test_facts_labels_are_present_and_non_empty(self):
        # D-190: the three bold labels of the facts section — `memo.facts.*_label`.
        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                for key in ("facts_label", "assumptions_label", "limitations_label"):
                    self.assertTrue(str(i18n.t(code, f"memo.facts.{key}")).strip(), key)

    def test_full_text_integrity_note_is_the_brief_sentence_in_every_pack(self):
        # D-200: the gate-11 digest prints the demotion through this label; a wrong or empty
        # sentence in any pack would misreport it, and the generic parity checks cannot see that.
        expected = {
            "en": (
                "saved text of {source_id} changed after it was saved by code; "
                "it now counts as an agent copy"
            ),
            "de": (
                "der von Code gespeicherte Text {source_id} hat sich geändert; "
                "er gilt jetzt als Agentenkopie"
            ),
            "fr": (
                "le texte de {source_id} enregistré par le code a changé ; "
                "il compte désormais comme une copie de l'agent"
            ),
            "es": (
                "el texto de {source_id} guardado por el código cambió; "
                "ahora cuenta como copia del agente"
            ),
            "ru": (
                "сохранённый код­ом текст {source_id} изменился после сохранения; "
                "теперь он считается копией агента"
            ),
        }
        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                note = str(i18n.t(code, "memo.labels.full_text_integrity_note"))
                self.assertTrue(note.strip(), code)
                self.assertIn("{source_id}", note, code)
                self.assertEqual(expected[code], note, code)

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

    # --- plan 67 task 2: no raw code reaches the reader (D-197) ---------------

    STATUS_FAMILIES: tuple[str, ...] = (
        "approved",
        "client_ready",
        "accepted_early",
        "manual_review_required",
        "forced_exit_with_remaining_issues",
        "delivered",
        "failed",
        "cancelled_by_user",
        "fallback_summary_delivered",
    )
    """Every `final_status` family `finalize.py`, `revision.py` and `machine.py` can write."""

    def test_every_status_family_has_a_name_in_every_pack(self):
        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                for family in self.STATUS_FAMILIES:
                    value = str(i18n.t(code, f"memo.status_names.{family}", version="1"))
                    self.assertTrue(value.strip(), family)

    def test_no_client_facing_name_keeps_a_snake_case_code(self):
        nodes = (
            "memo.status_names",
            "memo.status_reasons",
            "memo.severity",
            "memo.currency_names",
            "memo.link_names",
        )
        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                for node in nodes:
                    for key, value in dict(i18n.node(code, node)).items():
                        self.assertNotIn("_", str(value).replace("{version}", ""), f"{node}.{key}")

    def test_the_russian_wording_the_owner_fixed(self):
        expected = {
            "memo.status_names.approved": "утверждён на версии {version}",
            "memo.status_names.manual_review_required": "требуется ручная проверка (версия {version})",
            "memo.status_names.forced_exit_with_remaining_issues": (
                "выпущен с незакрытыми замечаниями (версия {version})"
            ),
            "memo.severity.blocker": "блокирующее замечание",
            "memo.severity.major": "существенное замечание",
            "memo.currency_names.current": "действует",
            "memo.currency_names.unchecked": "не проверялась",
            "memo.currency_names.manual_check": "нужна ручная проверка",
            "memo.currency_names.outdated_but_usable": "устарел, но применим",
        }
        for dotted, value in expected.items():
            with self.subTest(key=dotted):
                self.assertEqual(value, i18n.node("ru", dotted))

    def test_ru_section_label_is_statya(self):
        """Post-run fix: a UK Act section is «статья» in Russian legal usage."""
        self.assertEqual("ст.", i18n.node("ru", "memo.citation.s"))
        self.assertEqual("ст.", i18n.node("ru", "memo.citation.ss"))

    # --- plan 56 task 6: the `ui` parity rules (D-168 packs) ------------------

    _AUQ_PAIRS: tuple[tuple[str, str], ...] = (
        ("ui.gates.header_plan", "Plan"),
        ("ui.gates.header_mode", "Mode"),
        ("ui.gates.header_style", "Style"),
        ("ui.gates.header_sources", "Sources"),
        ("ui.gates.option_approve", "Approve"),
        ("ui.gates.option_edit", "Edit"),
        ("ui.gates.option_cancel", "Cancel"),
        ("ui.gates.option_brief", "Brief"),
        ("ui.gates.option_full", "Full"),
        ("ui.gates.option_continue", "Continue"),
    )
    """The ten keys `gates.canonical_map` builds its reverse map from (D-176a)."""

    def test_auq_headers_fit_their_control_and_option_labels_are_short(self):
        """Every AUQ `header` is ≤ 12 characters and every option label ≤ 20 (plan 56 contract)."""
        headers = (
            "ui.gates.header_plan",
            "ui.gates.header_mode",
            "ui.gates.header_style",
            "ui.gates.header_sources",
        )
        options = (
            "ui.gates.option_approve",
            "ui.gates.option_edit",
            "ui.gates.option_cancel",
            "ui.gates.option_brief",
            "ui.gates.option_full",
            "ui.gates.option_continue",
        )
        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                for dotted in headers:
                    self.assertLessEqual(len(str(i18n.t(code, dotted))), 12, dotted)
                for dotted in options:
                    self.assertLessEqual(len(str(i18n.t(code, dotted))), 20, dotted)

    def test_canonical_map_is_injective_per_language(self):
        """One localized label/header maps to exactly one canonical value (D-176a).

        `Cancel` legitimately repeats across the Plan and Sources questions, but every
        occurrence maps to the same value — so the check runs on the (localized, canonical)
        pairs, not on the localized strings alone.
        """
        from memoforge import gates

        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                seen: dict[str, str] = {}
                for key, canonical in self._AUQ_PAIRS:
                    localized = str(i18n.t(code, key))
                    self.assertTrue(localized.strip(), key)
                    if localized in seen:
                        self.assertEqual(seen[localized], canonical, key)
                    else:
                        seen[localized] = canonical
                mapping = gates.canonical_map(code)
                for localized, canonical in seen.items():
                    self.assertEqual(canonical, mapping[localized], localized)

    def test_backticked_tokens_of_english_strings_survive_unchanged(self):
        """Every canonical token inside a backticked span of an English string is present verbatim.

        Two gate strings put a *form example* in backticks (`` `1A 2C 3: free text` ``):
        the canonical tokens inside the span (`1A`, `2C`, `3:`) must not move, while the
        prose around them (`free text`) is translated — so the rule compares the tokens
        of the D-176a text channel (plus anything carrying digits, punctuation, slashes or
        brackets, like `{placeholders}` and `/memoforge:continue`), not whole spans and
        not pure prose words (plan 56 task 1 concern 3).
        """
        english = _leaves(i18n_en.EN)
        for code in PACK_CODES:
            with self.subTest(code=code):
                leaves = _leaves(self._pack(code))
                for dotted, value in english.items():
                    if not isinstance(value, str) or "`" not in value:
                        continue
                    localized = leaves[dotted]
                    self.assertIsInstance(localized, str, dotted)
                    for span in re.findall(r"`([^`]*)`", value):
                        for token in span.split():
                            if token in _CANONICAL_BACKTICK_TOKENS or re.search(
                                r"[^A-Za-z]", token
                            ):
                                self.assertIn(token, str(localized), f"{dotted}: `{token}`")

    def test_numbered_gate_instruction_ends_with_a_colon_and_reply_block_has_no_blank_line(self):
        """The instruction line of a numbered gate ends with `:` and the closing reply block
        (`machine._gate_reply_lines` shape) holds no blank line inside."""
        from memoforge import machine

        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                for dotted in (
                    "ui.gates.intake_form",
                    "ui.gates.followup_form",
                ):
                    self.assertTrue(str(i18n.t(code, dotted)).rstrip().endswith(":"), dotted)
                for gate, numbered in (
                    ("intake", True),
                    ("sufficiency_followup", True),
                    ("insufficient", False),
                    ("source_review", False),
                    ("plan", False),
                ):
                    text = self._render_gate(code, gate)
                    lines = machine._gate_reply_lines(text, numbered)
                    self.assertTrue(lines, gate)
                    self.assertTrue(all(line.strip() for line in lines), gate)
                    if numbered:
                        self.assertTrue(
                            any(line.rstrip().endswith(":") for line in lines), gate
                        )
                    for line in lines:
                        self.assertNotIn("\n", line, gate)

    def _render_gate(self, code: str, gate: str) -> str:
        """Render one gate text with the real pack at `lib/i18n` (no synthetic overlay)."""
        import tempfile

        from memoforge import gates as _gates
        from memoforge import probe as _probe
        from memoforge import state_io as _state_io

        with tempfile.TemporaryDirectory(prefix="mf-pack-gate-") as tmp:
            work = Path(tmp) / "memo-20260908T120000Z-pack"
            (work / "intake").mkdir(parents=True)
            _state_io.write_json_atomic(work / "intake" / "questions.json", _probe.fixture_questions())
            _state_io.write_json_atomic(
                work / "intake" / "mcp-probe.json", {"namespaces": {"other": []}}
            )
            _state_io.write_json_atomic(work / _gates.PLAN_PATH, _probe.fixture_plan())
            full_state = {
                "task_id": work.name,
                "language": "en",
                "ui_language": code,
                "config": {},
                "drafting_warnings": [],
                "sufficiency_followup": None,
            }
            return _gates.render(work, full_state, gate)

    def test_language_names_hold_the_five_endonyms(self):
        for code in ("en", *PACK_CODES):
            with self.subTest(code=code):
                self.assertEqual(
                    {
                        "en": "English",
                        "de": "Deutsch",
                        "fr": "Français",
                        "es": "Español",
                        "ru": "Русский",
                    },
                    {
                        name: str(i18n.t(code, f"ui.language_names.{name}"))
                        for name in ("en", "de", "fr", "es", "ru")
                    },
                    code,
                )

    def test_inline_plural_pairs_share_one_number_neutral_sentence(self):
        """The three `_one`/`_many` pairs exist only to keep English bytes; in every other
        pack both keys carry the same number-neutral sentence (plan 56 contract)."""
        pairs = (
            ("ui.gates.brief_mismatch_hint_one", "ui.gates.brief_mismatch_hint_many"),
            ("ui.machine.plan_gate_shape_one", "ui.machine.plan_gate_shape_many"),
            ("ui.machine.gate_pointer_one", "ui.machine.gate_pointer_many"),
        )
        for code in PACK_CODES:
            with self.subTest(code=code):
                for one, many in pairs:
                    self.assertEqual(str(i18n.t(code, one)), str(i18n.t(code, many)), one)


if __name__ == "__main__":
    unittest.main()
