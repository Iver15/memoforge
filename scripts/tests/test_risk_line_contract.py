"""D-12: the literal Risk-line and Exec-summary-bullet forms live in the content, not only in lint."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

CLASSICAL = PLUGIN_ROOT / "templates" / "classical-memo.md"
PROSE_STYLE = PLUGIN_ROOT / "lib" / "prose-style.md"
CONTENT_FILES = (CLASSICAL, PROSE_STYLE)

VERDICTS = ("high", "medium", "low", "undetermined")

# D-12: the paragraph opens with `Risk: <level>.` and continues in the same paragraph.
RISK_LINE = re.compile(r"^Risk: (high|medium|low|undetermined)\. \S")
# D-12: a classical Executive summary bullet ends with `Risk: <level>.`
SUMMARY_BULLET = re.compile(r"^- \S.* Risk: (high|medium|low|undetermined)\.$")

# The `L-nn` ids and the numeric caps belong to lint.py/limits.py; content must not restate them.
LINT_ID = re.compile(r"\bL-\d\d\b")
WORD_CAP = re.compile(r"\b\d+\s+words\b")


def code_spans(path: Path) -> list[str]:
    """Every backticked span of a markdown file — where the templates show literal forms."""
    return re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8"))


class RiskLineFormatTest(unittest.TestCase):
    def test_every_content_file_shows_a_literal_risk_line(self):
        for path in CONTENT_FILES:
            with self.subTest(file=path.name):
                examples = [span for span in code_spans(path) if RISK_LINE.match(span)]
                self.assertTrue(
                    examples,
                    f"{path.name} must show one literal `Risk: <level>. …` example (D-12)",
                )

    def test_the_permitted_verdicts_are_named(self):
        for path in CONTENT_FILES:
            with self.subTest(file=path.name):
                spans = code_spans(path)
                for verdict in VERDICTS:
                    self.assertIn(verdict, spans, f"{path.name} must name `{verdict}`")

    def test_the_classical_template_shows_a_literal_summary_bullet(self):
        examples = [span for span in code_spans(CLASSICAL) if SUMMARY_BULLET.match(span)]
        self.assertTrue(
            examples,
            "classical-memo.md must show one literal Executive-summary bullet ending in "
            "`Risk: <level>.` (D-12)",
        )

    def test_the_examples_are_consistent_with_each_other(self):
        for path in CONTENT_FILES:
            with self.subTest(file=path.name):
                for span in code_spans(path):
                    if not span.startswith("Risk:"):
                        continue
                    self.assertRegex(span, RISK_LINE, f"{path.name}: malformed Risk-line example")


class NoLintDuplicationTest(unittest.TestCase):
    """Finding 10's fix must not undo the earlier one: no lint ids or caps back in the content."""

    def test_no_lint_rule_ids(self):
        for path in CONTENT_FILES:
            with self.subTest(file=path.name):
                self.assertIsNone(LINT_ID.search(path.read_text(encoding="utf-8")))

    def test_no_numeric_word_caps(self):
        self.assertIsNone(WORD_CAP.search(PROSE_STYLE.read_text(encoding="utf-8")))


class AnchorExampleTest(unittest.TestCase):
    """D-23: `draft anchor` writes `§s-N` on an H2 and `§s-N-M` on an H3."""

    def test_the_classical_template_shows_both_levels(self):
        spans = code_spans(CLASSICAL)
        self.assertIn("<!-- §s-4 -->", spans)
        self.assertIn("<!-- §s-4-1 -->", spans)


class LanguageNeutralityTest(unittest.TestCase):
    """D-173: static content files name the memo language as given in the task prompt."""

    NEUTRAL = (
        "the memo language, the section headings and the risk-line literal are given "
        "in your task prompt; the english forms below are the example"
    )

    def test_the_template_and_prose_style_carry_the_neutral_sentence(self):
        for path in CONTENT_FILES:
            with self.subTest(file=path.name):
                self.assertIn(self.NEUTRAL, path.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
