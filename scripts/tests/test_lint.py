"""Tests for scripts/memoforge/lint.py — the 15 L-rules and `draft anchor` (ТЗ §5.4, M10, §9)."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402
from memoforge import i18n, limits, lint, quotes, schema, sources, state_io, stepctx, task  # noqa: E402

TASK_ID = "memo-20260101T000000Z-lint"
DRAFTS = Path(__file__).resolve().parent / "fixtures" / "drafts"

RAW_ART_6 = (
    "# Article 6 - Lawfulness of processing\n"
    "\n"
    "1. Processing shall be lawful only if and to the extent that at least one of the following applies.\n"
    "\n"
    "(a) the data subject has given consent to the processing of his or her personal data;\n"
    "\n"
    "Consent must be freely given, specific, informed and unambiguous.\n"
)


BRIEF_WITH_FRONT_MATTER = """# Agent scoring: what must change before launch

Question: what must the company fix before launching AI-drafted support replies.

## Key assumptions

- The support agents whose replies are scored are EEA-based staff.

## 1. Reach of the GDPR

Consent is available for this flow [[src:gdpr-art-6 Art. 6(1)(a)]].

Risk: medium. The basis holds while the opt-in stays unticked. Product must keep it unticked.

## 2. Monitoring of the support agents

Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].

Risk: high. The scores drive evaluation. Legal must document the basis before launch.

## 3. Duties under the AI Act

The deployer duties follow from the role of the company [[src:gdpr-art-7 Art. 7(3)]].

Risk: low. The role is settled on the facts. Legal must re-check it at each release.

## 4. Recommendations

- Ship a one-click withdrawal control, owned by Legal, before the flow ships.

<!-- sources: generated -->
"""
"""D34-09: the real Brief structure of run `memo-20260910T095310Z` — front matter, then `## 1. …`."""

RU_SUBSECTION = (
    "## 2. Правовое основание\n\n"
    "Обработка опирается на согласие [[src:gdpr art 6]].\n\n"
    "Риск: средний. Основание действует, пока отметка не проставлена заранее. Продукт оставляет её пустой.\n"
)
"""One analytical subsection in Russian; its Risk line is literal only under the Russian pack (D-174)."""


def fixture(name: str) -> str:
    return (DRAFTS / f"{name}.md").read_text(encoding="utf-8-sig")


class LintTestCase(unittest.TestCase):
    template = "classical-memo"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work_dir = self.root / TASK_ID
        task.create_work_dir_tree(self.work_dir)
        state = task.build_initial_state(
            task_id=TASK_ID,
            user_query="lint",
            language="en",
            work_dir=self.work_dir,
            output_folder=self.root,
            config={
                "writer_model": "opus",
                "source_review_gate": "auto",
                "intake_max_questions": 5,
                "template_id": self.template,
            },
            created_at="2026-01-01T00:00:00.000Z",
        )
        state_io.create_state(self.work_dir, state)
        self.addCleanup(self._tmp.cleanup)
        self._seed_sources()

    def _seed_sources(self) -> None:
        raw = self.root / "art6.md"
        raw.write_bytes(RAW_ART_6.encode("utf-8"))
        sources.register_source(
            self.work_dir,
            layer="statutes",
            title="Regulation (EU) 2016/679, Article 6",
            citation="GDPR, Art. 6",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art6",
            tool="mcp__ldh__get_document",
            tier="critical",
            raw_file=raw,
            source_id="gdpr-art-6",
        )
        sources.register_source(
            self.work_dir,
            layer="statutes",
            title="Regulation (EU) 2016/679, Article 7",
            citation="GDPR, Art. 7",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art7",
            tool="mcp__ldh__get_document",
            tier="supporting",
            source_id="gdpr-art-7",
        )
        quote = quotes.extract_quote(
            self.work_dir, "gdpr-art-6", "Consent must be freely given, specific, informed and unambiguous"
        )
        self.assertEqual("q-gdpr-art-6-1", quote["quote_id"])

    # -- helpers ----------------------------------------------------------

    def lint(self, text: str, *, template: str | None = None, state: dict | None = None) -> list[dict]:
        return lint.lint_text(
            text,
            work_dir=self.work_dir,
            state=state or state_io.read_state(self.work_dir),
            template=template or self.template,
        )[0]

    def issue_step(self, step_id: str, attempt: int = 1) -> None:
        """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

        def mutator(state: dict) -> None:
            rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
            for row in rows:
                if row.get("step_id") == step_id:
                    return
            rows.append(
                {
                    "step_id": step_id,
                    "kind": "script",
                    "phase": state.get("current_phase"),
                    "attempt": attempt,
                    "reason": "initial",
                    "issued_at": "2026-01-01T00:00:00.000Z",
                    "status": None,
                }
            )
            state["steps"] = rows

        state_io.write_state(self.work_dir, mutator)

    def rules(self, findings: list[dict]) -> set[str]:
        return {row["rule"] for row in findings}

    def only(self, findings: list[dict], rule: str) -> list[dict]:
        return [row for row in findings if row["rule"] == rule]


class CleanFixtureTest(LintTestCase):
    def test_the_clean_classical_fixture_has_no_findings(self):
        findings = self.lint(fixture("classical-clean"))
        self.assertEqual([], findings, [f"{row['rule']}: {row['hint']}" for row in findings])

    def test_two_subsections_one_raw_source_pass_l08_and_l09_with_a_skip(self):
        # D-164: a quotation is optional — the second subsection needs no skip.
        text = fixture("two-subsections-one-source")
        findings = self.lint(text)
        self.assertEqual([], self.only(findings, "L-08"), "a subsection without a quote is not a finding")
        self.assertEqual([], findings, [f"{row['rule']}: {row['hint']}" for row in findings])


class SentenceAndParagraphTest(LintTestCase):
    def test_l01_flags_a_sentence_above_the_cap(self):
        long_sentence = " ".join(["word"] * (limits.MAX_SENTENCE_WORDS + 5)) + "."
        text = fixture("classical-clean").replace(
            "The flow has no withdrawal control today.", long_sentence
        )
        findings = self.only(self.lint(text), "L-01")
        self.assertEqual(1, len(findings))
        self.assertIn(str(limits.MAX_SENTENCE_WORDS + 5), findings[0]["hint"])

    def test_l01_ignores_a_long_verbatim_blockquote(self):
        long_quote = "> [[q:q-gdpr-art-6-1]] " + " ".join(["word"] * 60) + "."
        text = fixture("classical-clean").replace(
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.",
            long_quote,
        )
        self.assertNotIn("L-01", self.rules(self.lint(text)))

    def test_l02_flags_a_paragraph_above_the_sentence_cap(self):
        text = fixture("classical-clean").replace(
            "The company collects contact data from users located in the EU.",
            "One. Two. Three. Four.",
        )
        findings = self.only(self.lint(text), "L-02")
        self.assertEqual(1, len(findings))

    def test_l02_flags_a_paragraph_above_the_word_cap(self):
        paragraph = " ".join(["word"] * (limits.MAX_PARAGRAPH_WORDS + 10)) + "."
        text = fixture("classical-clean").replace(
            "The company collects contact data from users located in the EU.", paragraph
        )
        self.assertIn("L-02", self.rules(self.lint(text)))

    def test_l02_does_not_count_a_bullet_list_as_one_paragraph(self):
        self.assertNotIn("L-02", self.rules(self.lint(fixture("classical-clean"))))

    def test_a_long_sentence_and_a_long_paragraph_are_minor_and_leave_the_draft_clean(self):
        # D-216 (owner decision b): readability belongs to the form reviewer; 27 of the 32 majors of
        # run 74 were L-01/L-02 that passed as `clean` all the same.
        sentence = " ".join(["word"] * 45) + "."
        text = fixture("classical-clean").replace("The flow has no withdrawal control today.", sentence)
        text = text.replace(
            "The company collects contact data from users located in the EU.", "One. Two. Three. Four."
        )
        findings = self.lint(text)
        self.assertEqual(["minor"], [row["severity"] for row in self.only(findings, "L-01")])
        self.assertEqual(["minor"], [row["severity"] for row in self.only(findings, "L-02")])
        self.assertTrue(lint.build_report("0" * 64, findings)["clean"])


class StyleRuleTest(LintTestCase):
    def test_l03_flags_an_em_dash_outside_a_definition(self):
        text = fixture("classical-clean").replace(
            "The flow has no withdrawal control today.",
            "The flow has no withdrawal control today — and that matters for the analysis below.",
        )
        self.assertIn("L-03", self.rules(self.lint(text)))

    def test_l03_accepts_the_term_definition_form(self):
        text = fixture("classical-clean").replace(
            "## 2. Facts, assumptions and limitations\n\nThe company collects",
            "## 2. Facts, assumptions and limitations\n\n"
            "Consent — an unambiguous indication of the data subject's wishes.\n\n"
            "The company collects",
        )
        self.assertNotIn("L-03", self.rules(self.lint(text)))

    def test_l04_flags_an_ai_tell(self):
        text = fixture("classical-clean").replace(
            "The flow has no withdrawal control today.",
            "It is worth noting that the flow has no withdrawal control today.",
        )
        findings = self.only(self.lint(text), "L-04")
        self.assertEqual(1, len(findings))
        self.assertIn("it is worth noting", findings[0]["hint"])

    def test_l04_is_quiet_on_the_clean_fixture(self):
        self.assertNotIn("L-04", self.rules(self.lint(fixture("classical-clean"))))


class HeadingTest(LintTestCase):
    def test_l05_rejects_a_fourth_level_heading(self):
        text = fixture("classical-clean").replace(
            "### 3.2. Withdrawal of consent", "#### 3.2. Withdrawal of consent"
        )
        self.assertIn("L-05", self.rules(self.lint(text)))

    def test_l05_rejects_a_question_heading(self):
        text = fixture("classical-clean").replace(
            "### 3.1. Consent as the basis of the flow", "### 3.1. Is consent available?"
        )
        findings = self.only(self.lint(text), "L-05")
        self.assertTrue(any("noun phrase" in row["hint"] for row in findings))

    def test_l05_rejects_a_missing_h1(self):
        text = fixture("classical-clean").replace(
            "# Consent under the GDPR: lawfulness of a marketing flow",
            "## Consent under the GDPR: lawfulness of a marketing flow",
        )
        self.assertIn("L-05", self.rules(self.lint(text)))

    def test_l05_accepts_the_clean_hierarchy(self):
        self.assertNotIn("L-05", self.rules(self.lint(fixture("classical-clean"))))


class BijectionTest(LintTestCase):
    def test_l06_flags_a_missing_summary_bullet(self):
        text = fixture("classical-clean").replace(
            "- The withdrawal control is the operative gap and must ship before launch. Risk: high.\n", ""
        )
        findings = self.only(self.lint(text), "L-06")
        self.assertTrue(any("Executive summary" in row["hint"] for row in findings))

    def test_l06_flags_a_missing_conclusion_item(self):
        text = fixture("classical-clean").replace(
            "- Ship a one-click withdrawal control, owned by Legal, before the flow ships.\n", ""
        )
        findings = self.only(self.lint(text), "L-06")
        self.assertTrue(any("Conclusion" in row["hint"] for row in findings))

    def test_l06_for_a_brief_flags_a_subsection_without_a_recommendation(self):
        # D-11: the brief bijection is «subsection <-> Recommendations item».
        text = fixture("brief-clean").replace(
            "## 2. Recommendations",
            "## 2. Withdrawal of consent\n"
            "\n"
            "Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].\n"
            "\n"
            "Risk: high. The flow has no control today. Legal must ship one before launch.\n"
            "\n"
            "## 3. Recommendations",
        )
        findings = self.only(self.lint(text, template="executive-brief"), "L-06")
        self.assertEqual(1, len(findings))

    def test_l06_for_a_brief_allows_extra_cross_cutting_recommendations(self):
        text = fixture("classical-clean").replace(
            "- Keep the opt-in unticked at launch, owned by Product, before the flow ships.",
            "- Keep the opt-in unticked at launch, owned by Product, before the flow ships.\n"
            "- Review the consent copy every year, owned by Legal, at each annual review.",
        )
        self.assertNotIn("L-06", self.rules(self.lint(text, template="executive-brief")))

    def test_l06_flags_a_conclusion_item_carrying_the_risk_verdict(self):
        # D-189: the verdict belongs to the risk line and the summary bullet, not to the conclusion.
        text = fixture("classical-clean").replace(
            "- Keep the opt-in unticked at launch, owned by Product, before the flow ships.",
            "- Keep the opt-in unticked at launch, owned by Product, before the flow ships. Risk: medium.",
        )
        findings = self.only(self.lint(text), "L-06")
        self.assertEqual(1, len(findings))
        self.assertIn("repeats the risk verdict", findings[0]["hint"])

    def test_l06_ignores_a_verdict_carrying_conclusion_item_of_the_brief(self):
        # D-189: the brief branch is the subsection count only; a verdict there changes nothing.
        # D-189a: the string this used to replace was not in `brief-clean.md`, so the fixture came
        # back untouched and the test asserted nothing. It now edits the brief's real item.
        original = fixture("brief-clean")
        item = "- Keep the opt-in unticked at launch, owned by Product, before the flow ships."
        text = original.replace(item, f"{item} Risk: high.")
        self.assertNotEqual(original, text)
        self.assertNotIn("L-06", self.rules(self.lint(text, template="executive-brief")))

    def test_l06_flags_the_verdict_wherever_it_stands_in_the_conclusion_item(self):
        # D-189a: `exec_bullet_risk` is end-anchored, so a verdict that opens the item or sits
        # before a section reference escaped L-06 entirely.
        item = "- Keep the opt-in unticked at launch, owned by Product, before the flow ships."
        for replacement in (
            "- Risk: medium. Product must keep the opt-in unticked before the flow ships.",
            "- Keep the opt-in unticked, owned by Product. Risk: medium. See section 3.1.",
            f"{item} Risk: medium.",
        ):
            with self.subTest(item=replacement):
                text = fixture("classical-clean").replace(item, replacement)
                findings = self.only(self.lint(text), "L-06")
                self.assertEqual(1, len(findings), replacement)
                self.assertIn("repeats the risk verdict", findings[0]["hint"])

    def test_l06_ignores_a_conclusion_item_that_merely_names_the_risk(self):
        # D-189a: the label without one of the four levels is ordinary prose, not a verdict.
        text = fixture("classical-clean").replace(
            "- Keep the opt-in unticked at launch, owned by Product, before the flow ships.",
            "- Risk of losing the goods stays with Product, owned by Product, before launch.",
        )
        self.assertNotIn("L-06", self.rules(self.lint(text)))


class RiskLineTest(LintTestCase):
    def test_l07_flags_a_missing_risk_line(self):
        text = fixture("classical-clean").replace(
            "Risk: medium. The basis holds only while the affirmative action stays in the flow. "
            "Product must keep the opt-in unticked before launch.\n",
            "",
        )
        findings = self.only(self.lint(text), "L-07")
        self.assertEqual(1, len(findings))
        self.assertEqual("s-3-1", findings[0]["section_id"])

    def test_l07_flags_an_unknown_verdict(self):
        text = fixture("classical-clean").replace(
            "Risk: medium. The basis holds only while the affirmative action stays in the flow.",
            "Risk: severe. The basis holds only while the affirmative action stays in the flow.",
        )
        findings = self.only(self.lint(text), "L-07")
        self.assertTrue(findings)
        self.assertIn("Risk line format", findings[0]["hint"])

    def test_l07_flags_a_bare_verdict_without_justification(self):
        text = fixture("classical-clean").replace(
            "Risk: medium. The basis holds only while the affirmative action stays in the flow. "
            "Product must keep the opt-in unticked before launch.",
            "Risk: medium.",
        )
        self.assertIn("L-07", self.rules(self.lint(text)))

    def test_l07_rejects_a_lower_case_verdict_without_a_period(self):
        # D-12: the literal format is `Risk: high.` — exact case, closing period.
        text = fixture("classical-clean").replace(
            "Risk: medium. The basis holds only while the affirmative action stays in the flow.",
            "risk: Medium the basis holds only while the affirmative action stays in the flow.",
        )
        findings = self.only(self.lint(text), "L-07")
        self.assertEqual(1, len(findings))
        self.assertIn("exact case", findings[0]["hint"])

    def test_l07_rejects_a_bulleted_risk_line_in_the_middle_of_the_subsection(self):
        # D-12: the Risk line is the last paragraph and carries no bullet or bold prefix.
        text = fixture("classical-clean").replace(
            "The provision makes consent one of six bases and sets the quality bar for it.",
            "- Risk: high. The provision makes consent one of six bases and sets the quality bar for it.",
        )
        findings = self.only(self.lint(text), "L-07")
        self.assertEqual(1, len(findings))
        self.assertIn("last paragraph of the subsection", findings[0]["hint"])

    def test_l07_rejects_a_risk_line_that_is_not_the_last_paragraph(self):
        text = fixture("classical-clean").replace(
            "Risk: medium. The basis holds only while the affirmative action stays in the flow. "
            "Product must keep the opt-in unticked before launch.\n",
            "Risk: medium. The basis holds only while the affirmative action stays in the flow. "
            "Product must keep the opt-in unticked before launch.\n"
            "\n"
            "One trailing paragraph after the verdict.\n",
        )
        findings = self.only(self.lint(text), "L-07")
        self.assertTrue(findings)
        self.assertIn("last paragraph", findings[0]["hint"])


class BlockquoteTest(LintTestCase):
    def test_l08_rejects_two_blockquotes_in_one_subsection(self):
        text = fixture("classical-clean").replace(
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.",
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.\n"
            "\n"
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.",
        )
        findings = self.only(self.lint(text), "L-08")
        self.assertTrue(any("blockquotes in one subsection" in row["hint"] for row in findings))
        self.assertEqual("blocker", findings[0]["severity"])

    def test_l08_rejects_a_blockquote_without_a_quote_marker(self):
        text = fixture("classical-clean").replace(
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given",
            "> Consent must be freely given",
        )
        findings = self.only(self.lint(text), "L-08")
        self.assertTrue(any("no `[[q:]]` marker" in row["hint"] for row in findings))
        self.assertTrue(all(row["severity"] == "blocker" for row in findings))
        self.assertNotIn("quote skip", findings[0]["hint"])
        self.assertIn("quote extract", findings[0]["hint"])

    def test_l08_rejects_an_unmarked_blockquote_in_every_section_of_the_document(self):
        # §5.4 L-08 / G5: «каждая с `[[q:]]`» is a rule about every blockquote, not only the ones
        # inside an analytical subsection; an unmarked quote elsewhere carries no token for the
        # C-rules to check either.
        unmarked = "> A quotation the writer pasted without running `mf quote extract`."
        anchors = {
            lint.TITLE_SECTION_ID: "The company is considering a marketing flow for EU users. "
            "This memo evaluates "
            "the lawful basis. Vendor selection is out of scope.",
            "s-1": "- The withdrawal control is the operative gap and must ship before launch. Risk: high.",
            "s-2": "The company collects contact data from users located in the EU. The flow has no "
            "withdrawal control today. The analysis assumes that the users are consumers.",
            "s-4": "- Ship a one-click withdrawal control, owned by Legal, before the flow ships.",
        }
        for section_id, anchor in anchors.items():
            with self.subTest(section=section_id):
                text = fixture("classical-clean")
                self.assertIn(anchor, text)
                findings = self.only(self.lint(text.replace(anchor, f"{anchor}\n\n{unmarked}", 1)), "L-08")
                self.assertEqual(1, len(findings), [row["hint"] for row in findings])
                self.assertIn("no `[[q:]]` marker", findings[0]["hint"])
                self.assertEqual("blocker", findings[0]["severity"])
                self.assertEqual(section_id, findings[0]["section_id"])

    def test_l08_counts_quotes_only_inside_the_analytical_subsections(self):
        # The «<=1 per subsection» cap stays scoped to the analytical subsections (§5.4, D-164).
        quote = "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous."
        facts = "The company collects contact data from users located in the EU."
        text = fixture("classical-clean").replace(facts, f"{facts}\n\n{quote}\n\n{quote}\n\n", 1)
        self.assertEqual([], self.only(self.lint(text), "L-08"), "the cap does not reach a named section")

        text = fixture("classical-clean").replace(
            facts, f"{facts} The basis is stated in [[src:gdpr-art-6 Art. 6(1)(a)]].", 1
        )
        self.assertEqual([], self.only(self.lint(text), "L-08"), "no quote is required in a named section")

    def test_a_subsection_without_a_quote_is_not_a_finding_and_raises_no_warning(self):
        text = fixture("classical-clean").replace(
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.\n\n",
            "",
        )
        findings, warnings = lint.lint_text(
            text,
            work_dir=self.work_dir,
            state=state_io.read_state(self.work_dir),
            template=self.template,
        )
        self.assertEqual([], self.only(findings, "L-08"))
        self.assertEqual([], warnings)

    def test_l08_needs_no_quote_for_a_source_without_raw(self):
        # s-3-2 cites gdpr-art-7, which was registered without a raw file.
        self.assertNotIn("L-08", self.rules(self.lint(fixture("classical-clean"))))

    def test_l08_is_satisfied_by_a_recorded_skip(self):
        # D-164: kept for the «record_skip stays a valid command» half — a skip no longer changes L-08.
        text = fixture("classical-clean").replace(
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.\n\n",
            "",
        )
        self.assertNotIn("L-08", self.rules(self.lint(text)))
        quotes.record_skip(self.work_dir, "s-3-1", "gdpr-art-6", "too_long")
        self.assertNotIn("L-08", self.rules(self.lint(text)))


class DuplicateFragmentTest(LintTestCase):
    def test_l09_flags_the_same_fragment_quoted_twice(self):
        text = fixture("classical-clean").replace(
            "Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].",
            "Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].\n"
            "\n"
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.",
        )
        findings = self.only(self.lint(text), "L-09")
        self.assertEqual(1, len(findings))
        self.assertEqual("blocker", findings[0]["severity"])

    def test_l09_ignores_a_new_quote_id_for_a_different_range(self):
        other = quotes.extract_quote(self.work_dir, "gdpr-art-6", "the data subject has given consent")
        text = fixture("classical-clean").replace(
            "Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].",
            "Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].\n"
            "\n"
            f"> [[q:{other['quote_id']}]] {other['text']}",
        )
        self.assertNotIn("L-09", self.rules(self.lint(text)))


class BriefCapTest(LintTestCase):
    template = "executive-brief"

    def test_the_clean_brief_has_no_findings(self):
        findings = self.lint(fixture("brief-clean"), template="executive-brief")
        self.assertEqual([], findings, [f"{row['rule']}: {row['hint']}" for row in findings])

    def test_l10_formula_counts_body_words_plus_twelve_per_unique_source(self):
        padding = " ".join(["word"] * 30) + "."
        body = "\n\n".join(padding for _ in range(45))
        text = fixture("brief-clean").replace(
            "The provision sets the quality bar, and a pre-ticked box would not meet it.",
            body + "\n\nThe provision sets the quality bar, and a pre-ticked box would not meet it.",
        )
        findings = self.only(self.lint(text, template="executive-brief"), "L-10")
        self.assertEqual(1, len(findings))
        self.assertEqual("major", findings[0]["severity"])
        document = lint.parse_draft(text)
        words = lint.body_words(document)
        unique = len({token["id"] for token in document["src_tokens"]})
        total = words + unique * limits.BRIEF_SOURCE_WORD_WEIGHT
        self.assertGreater(total, limits.BRIEF_WORD_CAP)
        self.assertIn(str(total), findings[0]["hint"])
        self.assertIn(str(limits.BRIEF_WORD_CAP), findings[0]["hint"])

    def test_l10_never_applies_to_the_classical_memo(self):
        padding = "\n\n".join(" ".join(["word"] * 30) + "." for _ in range(60))
        text = fixture("classical-clean").replace(
            "The company collects contact data from users located in the EU.", padding
        )
        self.assertNotIn("L-10", self.rules(self.lint(text, template="classical-memo")))


class PlaceholderTest(LintTestCase):
    def test_l11_flags_a_todo(self):
        text = fixture("classical-clean").replace(
            "The flow has no withdrawal control today.", "TODO: confirm the withdrawal control."
        )
        self.assertIn("L-11", self.rules(self.lint(text)))

    def test_l11_flags_an_unfilled_template_slot(self):
        text = fixture("classical-clean").replace(
            "The flow has no withdrawal control today.", "The flow has <insert control name> today."
        )
        self.assertIn("L-11", self.rules(self.lint(text)))

    def test_l11_ignores_anchors_and_the_sources_marker(self):
        text, _ = lint.anchor_text(fixture("classical-clean"))
        self.assertNotIn("L-11", self.rules(self.lint(text)))


class TemplateSectionTest(LintTestCase):
    def test_l12_flags_a_missing_sources_marker(self):
        text = fixture("classical-clean").replace("<!-- sources: generated -->\n", "")
        findings = self.only(self.lint(text), "L-12")
        self.assertTrue(any("sources: generated" in row["hint"] for row in findings))

    def test_l12_flags_a_missing_facts_section(self):
        text = fixture("classical-clean").replace(
            "## 2. Facts, assumptions and limitations", "## 2. Situation overview"
        )
        findings = self.only(self.lint(text), "L-12")
        self.assertTrue(any("facts" in row["hint"] for row in findings))

    def test_l12_flags_a_wrong_closing_section(self):
        text = fixture("classical-clean").replace(
            "## 4. Conclusion and recommendations", "## 4. Closing thoughts"
        )
        findings = self.only(self.lint(text), "L-12")
        self.assertTrue(any("last section" in row["hint"] for row in findings))

    def test_l12_flags_facts_before_the_executive_summary(self):
        text = fixture("classical-clean")
        text = text.replace("## 1. Executive summary", "@@SUMMARY@@")
        text = text.replace("## 2. Facts, assumptions and limitations", "## 1. Executive summary")
        text = text.replace("@@SUMMARY@@", "## 2. Facts, assumptions and limitations")
        findings = self.only(self.lint(text), "L-12")
        self.assertTrue(any("out of order" in row["hint"] for row in findings))

    def test_l12_accepts_the_brief_closing_with_recommendations(self):
        self.assertNotIn("L-12", self.rules(self.lint(fixture("brief-clean"), template="executive-brief")))

    def test_l12_for_a_brief_requires_only_recommendations_and_the_marker(self):
        # D-11: no executive summary and no facts section are required for the brief.
        text = fixture("brief-clean")
        self.assertNotIn("L-12", self.rules(self.lint(text, template="executive-brief")))
        without_marker = text.replace("<!-- sources: generated -->\n", "")
        self.assertIn("L-12", self.rules(self.lint(without_marker, template="executive-brief")))
        without_recommendations = text.replace("## 2. Recommendations", "## 2. Next steps")
        self.assertIn("L-12", self.rules(self.lint(without_recommendations, template="executive-brief")))

    def test_l12_for_a_classical_memo_requires_the_summary_and_facts_sections(self):
        # D-11: the canonical CONVENTIONS list applies to classical-memo only.
        text = fixture("classical-clean").replace("## 1. Executive summary", "## 1. Overview")
        findings = self.only(self.lint(text), "L-12")
        self.assertTrue(any("executive_summary" in row["hint"] for row in findings))

    def test_l12_rejects_a_classical_memo_closing_with_recommendations_only(self):
        text = fixture("classical-clean").replace(
            "## 4. Conclusion and recommendations", "## 4. Recommendations"
        )
        self.assertIn("L-12", self.rules(self.lint(text)))


class ExecutiveSummaryBulletTest(LintTestCase):
    def test_l13_flags_a_bullet_above_the_word_cap(self):
        long_bullet = "- " + " ".join(["word"] * (limits.EXEC_SUMMARY_BULLET_MAX_WORDS + 3)) + ". Risk: medium."
        text = fixture("classical-clean").replace(
            "- Consent is available as a lawful basis for the marketing flow. Risk: medium.", long_bullet
        )
        findings = self.only(self.lint(text), "L-13")
        self.assertTrue(any("words" in row["hint"] for row in findings))

    def test_l13_flags_a_bullet_without_a_risk_verdict(self):
        text = fixture("classical-clean").replace(
            "- Consent is available as a lawful basis for the marketing flow. Risk: medium.",
            "- Consent is available as a lawful basis for the marketing flow.",
        )
        findings = self.only(self.lint(text), "L-13")
        self.assertTrue(any("Risk:" in row["hint"] for row in findings))

    def test_l13_requires_the_literal_verdict_at_the_end_of_the_bullet(self):
        # D-12: the bullet ends with `Risk: <level>.`, exact case and period.
        text = fixture("classical-clean").replace(
            "- Consent is available as a lawful basis for the marketing flow. Risk: medium.",
            "- Consent is available as a lawful basis. Risk: medium, subject to the control below.",
        )
        self.assertIn("L-13", self.rules(self.lint(text)))

    def test_l13_is_quiet_on_the_clean_fixture(self):
        self.assertNotIn("L-13", self.rules(self.lint(fixture("classical-clean"))))

    def test_l13_does_not_apply_to_the_brief(self):
        # D-11: L-13 is a classical-memo rule; the brief has no executive summary.
        text = fixture("brief-clean").replace(
            "## 1. Consent as the basis of the flow",
            "## 1. Executive summary\n\n- A bullet with no verdict at all.\n\n"
            "## 2. Consent as the basis of the flow",
        ).replace("## 2. Recommendations", "## 3. Recommendations")
        self.assertNotIn("L-13", self.rules(self.lint(text, template="executive-brief")))


class SectionIdTest(LintTestCase):
    template = "executive-brief"

    def line_of(self, document: dict, needle: str) -> int:
        for number, line in enumerate(document["lines"], start=1):
            if needle in line:
                return number
        raise AssertionError(f"line not found: {needle!r}")

    def anchorable(self, document: dict) -> list[str]:
        return [row["section_id"] for row in document["sections"] if row["level"] in (2, 3)]

    def test_the_front_matter_h2_takes_s0_and_the_numbered_h2_keep_their_number(self):
        # D34-09: «## Key assumptions» used to increment the implicit counter to s-1, and the next
        # «## 1. …» reset it to s-1 again — the run shipped sections ["s-1","s-1","s-2","s-3","s-4"].
        document = lint.parse_draft(BRIEF_WITH_FRONT_MATTER)
        self.assertEqual(["s-0", "s-1", "s-2", "s-3", "s-4"], self.anchorable(document))
        self.assertEqual([], document["duplicate_sections"])

    def test_a_second_un_numbered_front_section_continues_the_s0_series(self):
        text = BRIEF_WITH_FRONT_MATTER.replace(
            "## Key assumptions", "## Executive summary\n\n- One bottom line.\n\n## Key assumptions", 1
        )
        document = lint.parse_draft(text)
        self.assertEqual(["s-0", "s-0-1", "s-1", "s-2", "s-3", "s-4"], self.anchorable(document))
        self.assertEqual([], document["duplicate_sections"])

    def test_a_front_matter_subsection_does_not_collide_with_the_next_front_section(self):
        text = BRIEF_WITH_FRONT_MATTER.replace(
            "## 1. Reach of the GDPR",
            "### Scope of the assumptions\n\nThe scope is the EEA.\n\n"
            "## Key facts\n\nThe staff are EEA-based.\n\n## 1. Reach of the GDPR",
            1,
        )
        document = lint.parse_draft(text)
        self.assertEqual(
            ["s-0", "s-0-1", "s-0-2", "s-1", "s-2", "s-3", "s-4"], self.anchorable(document)
        )
        self.assertEqual([], document["duplicate_sections"])

    def test_the_h1_no_longer_competes_with_the_front_matter_for_s0(self):
        document = lint.parse_draft(BRIEF_WITH_FRONT_MATTER)
        header = self.line_of(document, "Question: what must the company fix")
        assumption = self.line_of(document, "EEA-based staff")
        self.assertEqual(lint.TITLE_SECTION_ID, lint.section_of(document, header))
        self.assertEqual("s-0", lint.section_of(document, assumption))

    def test_the_numbered_hierarchy_of_the_classical_memo_is_unchanged(self):
        document = lint.parse_draft(fixture("classical-clean"))
        self.assertEqual(["s-1", "s-2", "s-3", "s-3-1", "s-3-2", "s-4"], self.anchorable(document))


class DuplicateAnchorTest(LintTestCase):
    template = "executive-brief"

    def test_l15_flags_two_headings_that_resolve_to_the_same_anchor(self):
        text = BRIEF_WITH_FRONT_MATTER.replace("## 2. Monitoring of the support agents", "## 1. Monitoring")
        findings = self.only(self.lint(text), "L-15")
        self.assertEqual(1, len(findings))
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertEqual("s-1", findings[0]["section_id"])
        self.assertIn("already taken by the heading on line", findings[0]["hint"])

    def test_l15_flags_a_hand_written_anchor_that_repeats_a_derived_one(self):
        text = BRIEF_WITH_FRONT_MATTER.replace(
            "## 3. Duties under the AI Act", "## 3. Duties under the AI Act\n<!-- §s-1 -->", 1
        )
        findings = self.only(self.lint(text), "L-15")
        self.assertEqual(1, len(findings))
        self.assertEqual("s-1", findings[0]["section_id"])

    def test_l15_is_quiet_on_the_clean_fixtures(self):
        for name, template in (("classical-clean", "classical-memo"), ("brief-clean", "executive-brief")):
            with self.subTest(fixture=name):
                self.assertNotIn("L-15", self.rules(self.lint(fixture(name), template=template)))
        self.assertNotIn("L-15", self.rules(self.lint(BRIEF_WITH_FRONT_MATTER)))


class DisclaimerTest(LintTestCase):
    def _state(self, accepted) -> dict:
        state = state_io.read_state(self.work_dir)
        state["intake"]["assumptions_accepted"] = accepted
        return state

    def test_l14_requires_a_disclaimer_when_assumptions_were_not_accepted(self):
        findings = self.lint(fixture("classical-clean"), state=self._state(False))
        self.assertIn("L-14", self.rules(findings))

    def test_l14_is_satisfied_by_a_disclaimer_paragraph(self):
        text = fixture("classical-clean").replace(
            "## 4. Conclusion and recommendations",
            "Disclaimer: the assumptions above were not confirmed by the user.\n\n"
            "## 4. Conclusion and recommendations",
        )
        self.assertNotIn("L-14", self.rules(self.lint(text, state=self._state(False))))

    def test_l14_does_not_apply_when_the_assumptions_were_accepted(self):
        self.assertNotIn("L-14", self.rules(self.lint(fixture("classical-clean"), state=self._state(True))))


class LocalizedGrammarTest(LintTestCase):
    """D-174: every language-bound recognizer of lint comes from the pack, never from a constant."""

    def setUp(self) -> None:
        super().setUp()
        self.packs = self.root / "i18n"
        self.packs.mkdir()
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", _i18n.RU)
        _i18n.fake_pack(self.packs, "es", {"memo.placeholders_ignore_case": False})
        _i18n.fake_pack(self.packs, "de", {"memo.abbreviations": ["abs", "vgl", "gem", "art"]})

    def rule(self, rule_id: str, text: str, *, language: str = "en") -> list[dict]:
        """Findings of one rule for a draft linted in one memo language."""
        state = state_io.read_state(self.work_dir)
        state["language"] = language
        return self.only(self.lint(text, state=state), rule_id)

    def test_the_english_grammar_is_todays_constants(self):
        english = lint.grammar("en")
        self.assertEqual(r"^Risk: (high|medium|low|undetermined)\.", english.risk_line.pattern)
        self.assertEqual("executive_summary", english.sections["executive summary"])
        self.assertEqual("Risk: <high|medium|low|undetermined>.", english.risk_literal)

    def test_a_russian_risk_line_satisfies_l07_and_an_english_one_does_not(self):
        self.assertEqual([], self.rule("L-07", RU_SUBSECTION, language="ru"))
        english = RU_SUBSECTION.replace("Риск: средний.", "Risk: medium.")
        hints = [row["hint"] for row in self.rule("L-07", english, language="ru")]
        literal = "Риск: <высокий|средний|низкий|не определён>."
        self.assertTrue(hints and all(literal in hint for hint in hints), hints)

    def test_a_misplaced_or_bold_localized_risk_line_is_malformed_not_missing(self):
        bold = RU_SUBSECTION.replace("Риск: средний.", "**Риск:** средний.")
        self.assertTrue(any("format" in row["hint"].lower() for row in self.rule("L-07", bold, language="ru")))

    def test_localized_section_titles_are_canonical(self):
        document = lint.parse_draft("# T\n\n## 1. Резюме\n\ntext\n", lint.grammar("ru"))
        # sections[0] is the H1 title section; the first H2 follows it.
        self.assertEqual("executive_summary", document["sections"][1]["kind"])

    def test_the_em_dash_rule_is_off_for_russian_and_on_for_english(self):
        text = (
            "# T\n\n## 1. A\n\n"
            "Эта оговорка занимает больше шести слов до тире — и остаётся обычной прозой.\n"
        )
        self.assertEqual([], self.rule("L-03", text, language="ru"))
        self.assertNotEqual([], self.rule("L-03", text, language="en"))

    def test_spanish_todo_is_prose_and_the_upper_case_placeholder_is_not(self):
        self.assertEqual(
            [], self.rule("L-11", "# T\n\n## 1. A\n\nTodo tratamiento requiere una base jurídica.\n", language="es")
        )
        self.assertNotEqual([], self.rule("L-11", "# T\n\n## 1. A\n\nTODO completar.\n", language="es"))
        self.assertNotEqual(
            [], self.rule("L-11", "# T\n\n## 1. A\n\nVéase [insert referencia].\n\n[insert otra]\n", language="es")
        )
        # English is unchanged: the same prose still matches «TODO» case-blind.
        self.assertNotEqual([], self.rule("L-11", "# T\n\n## 1. A\n\nTodo tratamiento.\n", language="en"))

    def test_german_abbreviations_do_not_split_a_paragraph(self):
        para = "Die Verarbeitung ist gem. Art. 6 Abs. 1 DSGVO zulässig, vgl. Erwägungsgrund 47."
        self.assertEqual(1, len(quotes.sentence_spans(para, lint.grammar("de").abbreviations)))
        self.assertGreater(len(quotes.sentence_spans(para)), 1)

    def test_a_russian_conclusion_item_carrying_the_verdict_is_an_l06(self):
        # D-189: the verdict check runs through the memo-language grammar (Риск: средний.).
        text = (
            "# T\n\n"
            "## 1. Резюме\n\n"
            "- Основание доступно для потока. Риск: средний.\n\n"
            "## 2. Факты\n\n"
            "Факты потока.\n\n"
            "## 3. Основание\n\n"
            "Основание доступно [[src:gdpr-art-6 Art. 6(1)(a)]].\n\n"
            "Риск: средний. Основание действует. Продукт оставляет отметку пустой.\n\n"
            "## 4. Выводы\n\n"
            "- Сохранить отметку пустой, владелец Продукт, до запуска. Риск: средний.\n\n"
            "<!-- sources: generated -->\n"
        )
        findings = self.rule("L-06", text, language="ru")
        self.assertEqual(1, len(findings))
        self.assertIn("repeats the risk verdict", findings[0]["hint"])

    def test_a_russian_verdict_before_a_section_reference_is_an_l06(self):
        # D-189a: `Риск: средний (раздел 4.1).` escaped the end-anchored recognizer entirely.
        text = (
            "# T\n\n"
            "## 1. Резюме\n\n"
            "- Основание доступно для потока. Риск: средний.\n\n"
            "## 2. Факты\n\n"
            "Факты потока.\n\n"
            "## 3. Основание\n\n"
            "Основание доступно [[src:gdpr-art-6 Art. 6(1)(a)]].\n\n"
            "Риск: средний. Основание действует. Продукт оставляет отметку пустой.\n\n"
            "## 4. Выводы\n\n"
            "- Сохранить отметку пустой, владелец Продукт. Риск: средний (раздел 3).\n\n"
            "<!-- sources: generated -->\n"
        )
        findings = self.rule("L-06", text, language="ru")
        self.assertEqual(1, len(findings))
        self.assertIn("repeats the risk verdict", findings[0]["hint"])

    def test_a_russian_conclusion_item_naming_the_risk_without_a_level_is_not_an_l06(self):
        text = (
            "# T\n\n"
            "## 1. Резюме\n\n"
            "- Основание доступно для потока. Риск: средний.\n\n"
            "## 2. Факты\n\n"
            "Факты потока.\n\n"
            "## 3. Основание\n\n"
            "Основание доступно [[src:gdpr-art-6 Art. 6(1)(a)]].\n\n"
            "Риск: средний. Основание действует. Продукт оставляет отметку пустой.\n\n"
            "## 4. Выводы\n\n"
            "- Риск утраты товара лежит на продавце, владелец Продукт, до запуска.\n\n"
            "<!-- sources: generated -->\n"
        )
        self.assertEqual([], self.rule("L-06", text, language="ru"))

    def test_the_l13_summary_bullet_recognizer_stays_end_anchored(self):
        # D-189a: only the L-06 conclusion check uses the loose recognizer; L-13 is unchanged.
        english = lint.grammar("en")
        self.assertTrue(english.exec_bullet_risk.pattern.endswith(r"\.\s*$"))
        self.assertIsNone(english.exec_bullet_risk.search("Risk: medium. Product must act."))
        self.assertIsNotNone(english.risk_verdict.search("Risk: medium. Product must act."))
        self.assertIsNotNone(english.risk_verdict.search("Owned by Legal. Risk: low (section 4.1)."))
        self.assertIsNone(english.risk_verdict.search("Risk of losing the goods stays with Product."))


# --- commands ---------------------------------------------------------------


class DraftCommandTest(LintTestCase):
    def write_draft(self, name: str = "classical-clean") -> str:
        path = self.work_dir / "drafts" / "v1.md"
        path.write_text(fixture(name), encoding="utf-8")
        return "drafts/v1.md"

    def anchor(self, step: str = "s-012a", attempt: int = 1, draft: str = "drafts/v1.md") -> dict:
        self.issue_step(step, attempt)
        args = argparse.Namespace(
            workdir=str(self.work_dir), step=step, attempt=attempt, draft=draft, phase="drafting"
        )
        return lint.run_anchor(args)

    def run_lint(self, step: str = "s-012b", attempt: int = 1, draft: str = "drafts/v1.md") -> dict:
        self.issue_step(step, attempt)
        args = argparse.Namespace(
            workdir=str(self.work_dir),
            step=step,
            attempt=attempt,
            draft=draft,
            template=None,
            phase="drafting",
        )
        return lint.run_lint(args)

    def test_anchor_inserts_one_anchor_per_h2_and_h3(self):
        self.write_draft()
        result = self.anchor()
        self.assertEqual(6, result["anchors_inserted"])
        text = (self.work_dir / "drafts" / "v1.md").read_text(encoding="utf-8")
        self.assertIn("<!-- §s-3-1 -->", text)
        self.assertIn("<!-- §s-1 -->", text)
        self.assertEqual(["s-1", "s-2", "s-3", "s-3-1", "s-3-2", "s-4"], result["sections"])

    def test_anchor_of_a_brief_with_front_matter_numbers_the_sections_from_s0(self):
        # D34-09: the run printed `{"anchors_inserted": 5, "sections": ["s-1","s-1",...]}` and exited ok.
        (self.work_dir / "drafts" / "v1.md").write_text(BRIEF_WITH_FRONT_MATTER, encoding="utf-8")
        result = self.anchor()
        self.assertEqual(5, result["anchors_inserted"])
        self.assertEqual(["s-0", "s-1", "s-2", "s-3", "s-4"], result["sections"])
        text = (self.work_dir / "drafts" / "v1.md").read_text(encoding="utf-8")
        self.assertIn("<!-- §s-0 -->", text)

    def test_anchor_refuses_to_write_colliding_anchors(self):
        draft = self.work_dir / "drafts" / "v1.md"
        draft.write_text(
            BRIEF_WITH_FRONT_MATTER.replace("## 2. Monitoring of the support agents", "## 1. Monitoring"),
            encoding="utf-8",
        )
        result = self.anchor()
        self.assertEqual(1, len(result["errors"]))
        self.assertIn("duplicate_section_anchor: s-1", result["errors"][0])
        self.assertNotIn("<!-- §", draft.read_text(encoding="utf-8"))
        state = state_io.read_state(self.work_dir)
        self.assertIsNone(stepctx.current_step(state, "s-012a")["status"])

    def test_checked_anchor_is_the_path_both_commands_take(self):
        """D-130 × D-117: `draft anchor` and `draft finish` share one refusal (N-08)."""
        colliding = BRIEF_WITH_FRONT_MATTER.replace(
            "## 2. Monitoring of the support agents", "## 1. Monitoring"
        )
        text, inserted, errors = lint.checked_anchor(colliding)
        self.assertEqual(colliding, text, "a refused draft comes back untouched")
        self.assertEqual(0, inserted)
        self.assertEqual(1, len(errors))
        self.assertIn("duplicate_section_anchor: s-1", errors[0])

        clean, count, none = lint.checked_anchor(BRIEF_WITH_FRONT_MATTER)
        self.assertEqual([], none)
        self.assertEqual(lint.anchor_text(BRIEF_WITH_FRONT_MATTER), (clean, count))

    def test_anchor_is_idempotent(self):
        self.write_draft()
        self.anchor()
        second = self.anchor(step="s-012a2")
        self.assertEqual(0, second["anchors_inserted"])

    def test_anchor_writes_input_and_result_into_the_step_workspace(self):
        self.write_draft()
        self.anchor()
        workspace = self.work_dir / "steps" / "s-012a" / "a1" / "cli"
        self.assertTrue((workspace / "inputs" / "v1.md").is_file())
        self.assertTrue((workspace / "v1.md").is_file())

    def test_anchor_updates_published_so_lint_sees_no_false_recovery(self):
        self.write_draft()
        anchored = self.anchor()
        state = state_io.read_state(self.work_dir)
        self.assertEqual(anchored["draft_sha"], stepctx.published_sha(state, "drafts/v1.md"))
        result = self.run_lint()
        self.assertNotIn("errors", result)
        self.assertEqual(anchored["draft_sha"], result["draft_sha"])

    def test_lint_refuses_a_draft_modified_after_publication(self):
        self.write_draft()
        self.anchor()
        (self.work_dir / "drafts" / "v1.md").write_text("tampered", encoding="utf-8")
        result = self.run_lint()
        self.assertEqual([stepctx.OUTPUT_MODIFIED], result["errors"])

    def test_lint_publishes_lint_json_and_closes_the_step(self):
        draft = self.write_draft()
        result = self.run_lint(draft=draft)
        self.assertTrue(result["clean"])
        report = state_io.read_json(self.work_dir / lint.LINT_PATH)
        self.assertEqual([], schema.validate(report, "lint"))
        self.assertEqual(state_io.sha256_file(self.work_dir / draft), report["draft_sha"])
        state = state_io.read_state(self.work_dir)
        self.assertEqual(report["draft_sha"], result["draft_sha"])
        self.assertEqual(
            state_io.sha256_file(self.work_dir / lint.LINT_PATH),
            stepctx.published_sha(state, lint.LINT_PATH),
        )
        self.assertEqual("ok", stepctx.current_step(state, "s-012b")["status"])

    def test_lint_repeat_of_a_closed_step_is_a_no_op(self):
        self.write_draft()
        first = self.run_lint()
        second = self.run_lint()
        self.assertTrue(second["already_done"])
        self.assertEqual(first["draft_sha"], second["draft_sha"])
        self.assertEqual(first["clean"], second["clean"])
        self.assertIn("report", second)

    def test_lint_with_other_arguments_on_the_same_identity_is_identity_mismatch(self):
        self.write_draft()
        self.run_lint()
        (self.work_dir / "drafts" / "v2.md").write_text(fixture("classical-clean"), encoding="utf-8")
        result = self.run_lint(draft="drafts/v2.md")
        self.assertEqual(["identity_mismatch"], result["errors"])

    def test_lint_records_no_l08_drafting_warning_for_a_missing_quote(self):
        # D-164: a quotation is optional — a missing quote leaves drafting_warnings alone.
        path = self.work_dir / "drafts" / "v1.md"
        text = fixture("classical-clean").replace(
            "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.\n\n",
            "",
        )
        path.write_text(text, encoding="utf-8")
        result = self.run_lint()
        self.assertTrue(result["clean"], "a missing quote is not a finding at all")
        warnings = state_io.read_state(self.work_dir)["drafting_warnings"]
        self.assertEqual([], warnings)

    def test_lint_clean_is_false_only_for_a_blocker(self):
        path = self.work_dir / "drafts" / "v1.md"
        path.write_text(
            fixture("classical-clean").replace(
                "The flow has no withdrawal control today.",
                "It is worth noting that the flow has no withdrawal control today.",
            ),
            encoding="utf-8",
        )
        result = self.run_lint()
        self.assertTrue(result["clean"])
        self.assertEqual(0, result["blockers"])
        self.assertGreater(result["findings_count"], 0)

        path.write_text(
            fixture("classical-clean").replace("<!-- sources: generated -->\n", ""), encoding="utf-8"
        )
        result = self.run_lint(step="s-012c")
        self.assertFalse(result["clean"])
        self.assertGreater(result["blockers"], 0)

    def test_lint_of_a_missing_draft_errors(self):
        result = self.run_lint(draft="drafts/nope.md")
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
