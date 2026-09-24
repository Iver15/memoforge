"""Tests for scripts/memoforge/brief_lint.py — the decision-brief lint B-01…B-10 (plan 75A, D-222)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import brief_lint, i18n, limits, lint, schema  # noqa: E402

SHA = "0" * 64

# --- memos -----------------------------------------------------------------

MEMO_FLAT = """# Marketing emails: lawful basis and retention

**Date:** 2026-09-23

## 1. Executive summary
<!-- §s-1 -->

- Consent is available for the marketing emails. Risk: medium.
- The mailing list may be kept for two years. Risk: low.

## 2. Facts, assumptions and limitations
<!-- §s-2 -->

**Facts**

The company sends marketing emails to its customers.

## 4. Lawful basis for marketing emails
<!-- §s-4 -->

Consent is the lawful basis for the emails [[src:gdpr Art. 6(1)]].

Risk: medium. The basis holds while the opt-in box stays unticked.

## 5. Retention of the mailing list
<!-- §s-5 -->

The list may be kept for two years [[src:pecr reg 22]].

Risk: low. The period is short and documented.

## 6. Conclusion and recommendations
<!-- §s-6 -->

- Product keeps the opt-in box unticked before launch.

<!-- sources: generated -->
"""

MEMO_NESTED = """# Marketing emails: lawful basis

**Date:** 2026-09-23

## 1. Executive summary
<!-- §s-1 -->

- Consent is doubtful. Risk: high.
- Legitimate interest is available. Risk: low.
- Retention is acceptable. Risk: medium.

## 2. Facts, assumptions and limitations
<!-- §s-2 -->

**Facts**

The company sends marketing emails to its customers.

## 4. Lawful basis
<!-- §s-4 -->

### 4.1. Consent
<!-- §s-4-1 -->

The pre-ticked box does not give valid consent [[src:gdpr Art. 6(1)]].

Risk: high. The box is ticked by default today.

### 4.2. Legitimate interest
<!-- §s-4-2 -->

Existing customers may be contacted on a soft opt-in [[src:pecr reg 22]].

Risk: low. The customers bought similar products.

## 5. Retention of the mailing list
<!-- §s-5 -->

The list may be kept for two years [[src:pecr reg 22]].

Risk: medium. The period is not yet documented.

## 6. Conclusion and recommendations
<!-- §s-6 -->

- Product removes the pre-ticked box before launch.

<!-- sources: generated -->
"""

MEMO_RISKLESS = """# Marketing emails: lawful basis

## 1. Executive summary
<!-- §s-1 -->

- Consent is doubtful. Risk: high.

## 4. Lawful basis
<!-- §s-4 -->

### 4.1. Consent
<!-- §s-4-1 -->

The pre-ticked box does not give valid consent [[src:gdpr Art. 6(1)]].

Risk: high. The box is ticked by default today.

### 4.2. Legitimate interest
<!-- §s-4-2 -->

Existing customers may be contacted on a soft opt-in [[src:pecr reg 22]].

Risk: low. The customers bought similar products.

### 4.3. Profiling
<!-- §s-4-3 -->

Profiling for the emails was not examined in the documents provided.

## 6. Conclusion and recommendations
<!-- §s-6 -->

- Product removes the pre-ticked box before launch.

<!-- sources: generated -->
"""

MEMO_RU = """# Договор присоединения: риски для клиента

**Дата:** 2026-09-23

## 1. Резюме
<!-- §s-1 -->

- Условие может быть оспорено. Риск: высокий.
- Срок давности не истёк. Риск: низкий.

## 2. Факты, допущения и ограничения
<!-- §s-2 -->

Клиент подписал договор на стандартных условиях банка.

## 4. Условия договора присоединения
<!-- §s-4 -->

### 4.1. Обременительные условия
<!-- §s-4-1 -->

Присоединившаяся сторона вправе потребовать расторжения договора [[src:gk-rf п. 2 ст. 428]].

Риск: высокий. Условие явно обременительно для клиента.

### 4.2. Срок исковой давности
<!-- §s-4-2 -->

Общий срок исковой давности составляет три года [[src:gk-rf ст. 196]].

Риск: низкий. Срок ещё не истёк.

## 5. Выводы и рекомендации
<!-- §s-5 -->

- Юрист клиента направляет банку требование до конца месяца.

<!-- sources: generated -->
"""

# --- briefs ------------------------------------------------------------------


def labels(language: str) -> dict:
    return {
        "main": i18n.t(language, "memo.brief.sections.main"),
        "conclusions": i18n.t(language, "memo.brief.sections.conclusions"),
        "actions": i18n.t(language, "memo.brief.sections.actions"),
        "assumptions": i18n.t(language, "memo.brief.sections.assumptions"),
        "date": i18n.t(language, "memo.brief.date_label"),
        "jurisdictions": i18n.t(language, "memo.brief.jurisdictions_label"),
        "question": i18n.t(language, "memo.brief.question_label"),
    }


def block(title: str, ids, body: str, risk: str) -> dict:
    return {"title": title, "ids": list(ids), "body": body, "risk": risk}


EN_TEXT = {
    "title": "# Marketing emails: decision brief",
    "jurisdictions": "United Kingdom",
    "question": "May the company rely on consent for its marketing emails?",
    "main": "The company may rely on consent for its emails. The overall risk is medium. "
    "Product keeps the opt-in box unticked before launch.",
    "actions": "1. Product keeps the opt-in box unticked before launch.",
    "assumptions": "The answer holds while the emails go only to existing customers.",
}

RU_TEXT = {
    "title": "# Договор присоединения: справка",
    "jurisdictions": "Россия",
    "question": "Может ли клиент оспорить условия договора присоединения?",
    "main": "Клиент может потребовать расторжения договора. Общий риск высокий. "
    "Юрист клиента направляет банку требование.",
    "actions": "1. Юрист клиента направляет банку требование до конца месяца.",
    "assumptions": "Вывод верен, пока клиент не участвовал в согласовании условий.",
}


def make_brief(
    blocks,
    *,
    language: str = "en",
    parts=("main", "conclusions", "actions"),
    header=("date", "jurisdictions", "question"),
    main_extra: str = "",
    header_gap: bool = False,
    omitted=None,
    other: str = "",
    actions: str | None = None,
    assumptions: str | None = None,
) -> str:
    names = labels(language)
    text = EN_TEXT if language == "en" else RU_TEXT
    header_values = {"date": "2026-09-23", "jurisdictions": text["jurisdictions"], "question": text["question"]}
    lines = [text["title"], ""]
    for index, key in enumerate(header):
        if header_gap and index:
            lines.append("")  # D-226: the template's form, one paragraph per header line
        lines.append(f"**{names[key]}:** {header_values[key]}")
    lines.append("")
    if omitted is not None:  # D-229: the leaves the brief leaves out, one comment above the first part
        lines += ["<!-- omitted" + "".join(f" §{sid}" for sid in omitted) + " -->", ""]
    for part in parts:
        lines += [f"## {names[part]}", ""]
        if part == "main":
            lines += [text["main"], ""]
            if main_extra:
                lines += [main_extra, ""]
        elif part == "conclusions":
            if other:
                lines += [other, ""]
            for row in blocks:
                lines.append(f"### {row['title']}")
                if row["ids"]:
                    lines.append("<!-- from " + " ".join(f"§{sid}" for sid in row["ids"]) + " -->")
                lines += ["", row["body"], ""]
                if row["risk"]:
                    lines += [row["risk"], ""]
        elif part == "actions":
            lines += [text["actions"] if actions is None else actions, ""]
        elif part == "assumptions":
            lines += [text["assumptions"] if assumptions is None else assumptions, ""]
    return "\n".join(lines)


FLAT_BLOCKS = [
    block(
        "Lawful basis for marketing emails",
        ["s-4"],
        "Consent is the lawful basis for the emails [[src:gdpr Art. 6(1)]].",
        "Risk: medium. The basis holds while the opt-in box stays unticked.",
    ),
    block(
        "Retention of the mailing list",
        ["s-5"],
        "The list may be kept for two years [[src:pecr reg 22]].",
        "Risk: low. The period is short and documented.",
    ),
]


def nested_blocks(**override) -> list[dict]:
    rows = {
        "s-4-1": block(
            "Consent",
            ["s-4-1"],
            "The pre-ticked box does not give valid consent [[src:gdpr Art. 6(1)]].",
            "Risk: high. The box is ticked by default today.",
        ),
        "s-4-2": block(
            "Legitimate interest",
            ["s-4-2"],
            "Existing customers may be contacted on a soft opt-in [[src:pecr reg 22]].",
            "Risk: low. The customers bought similar products.",
        ),
        "s-5": block(
            "Retention of the mailing list",
            ["s-5"],
            "The list may be kept for two years [[src:pecr reg 22]].",
            "Risk: medium. The period is not yet documented.",
        ),
    }
    rows.update(override)
    return [row for row in rows.values() if row is not None]


def rules(findings) -> list[str]:
    return sorted({row["rule"] for row in findings})


def of_rule(findings, rule: str) -> list[dict]:
    return [row for row in findings if row["rule"] == rule]


class CleanBriefTest(unittest.TestCase):
    def test_a_clean_brief_over_two_flat_leaves_has_no_findings(self):
        findings = brief_lint.lint_brief(make_brief(FLAT_BLOCKS), MEMO_FLAT, language="en")
        self.assertEqual([], findings)

    def test_assumptions_part_is_optional_and_may_close_the_brief(self):
        text = make_brief(FLAT_BLOCKS, parts=("main", "conclusions", "actions", "assumptions"))
        self.assertEqual([], brief_lint.lint_brief(text, MEMO_FLAT, language="en"))

    def test_finding_shape_uses_block_ids(self):
        text = make_brief(
            [dict(FLAT_BLOCKS[0], risk="Risk: high. The basis is weak."), FLAT_BLOCKS[1]]
        )
        findings = of_rule(brief_lint.lint_brief(text, MEMO_FLAT, language="en"), "B-05")
        self.assertEqual(1, len(findings), findings)
        row = findings[0]
        self.assertEqual({"rule", "severity", "line", "section_id", "excerpt", "hint"}, set(row))
        self.assertEqual("s-b1", row["section_id"])
        self.assertEqual("blocker", row["severity"])


class MemoFactsTest(unittest.TestCase):
    def test_leaves_pairs_ids_and_headings(self):
        facts = brief_lint.memo_facts(MEMO_NESTED, "en")
        self.assertEqual({"s-4-1": "high", "s-4-2": "low", "s-5": "medium"}, facts["leaves"])
        self.assertEqual({"gdpr", "pecr"}, facts["ids"])
        self.assertIn(("gdpr", "art 6(1)"), facts["pairs"])
        self.assertEqual("4.1. Consent", facts["headings"]["s-4-1"])

    def test_a_leaf_without_a_risk_line_is_undetermined(self):
        facts = brief_lint.memo_facts(MEMO_RISKLESS, "en")
        self.assertEqual("undetermined", facts["leaves"]["s-4-3"])


class BindingTest(unittest.TestCase):
    def test_b03_b04_nested_and_leaf_sections(self):
        clean = brief_lint.lint_brief(make_brief(nested_blocks()), MEMO_NESTED, language="en")
        self.assertEqual([], clean)

        parent = nested_blocks(**{"s-4-1": block(
            "Consent",
            ["s-4"],
            "The pre-ticked box does not give valid consent [[src:gdpr Art. 6(1)]].",
            "Risk: high. The box is ticked by default today.",
        )})
        findings = brief_lint.lint_brief(make_brief(parent), MEMO_NESTED, language="en")
        self.assertTrue(of_rule(findings, "B-03"), findings)
        self.assertEqual("s-b1", of_rule(findings, "B-03")[0]["section_id"])

        dropped = brief_lint.lint_brief(make_brief(nested_blocks(**{"s-5": None})), MEMO_NESTED, language="en")
        b04 = of_rule(dropped, "B-04")
        self.assertEqual(1, len(b04), dropped)
        self.assertIn("s-5", b04[0]["hint"])
        self.assertEqual("major", b04[0]["severity"])

    def test_a_block_without_binding_and_an_unknown_id_are_b03(self):
        rows = nested_blocks()
        rows[0] = dict(rows[0], ids=[])
        findings = brief_lint.lint_brief(make_brief(rows), MEMO_NESTED, language="en")
        self.assertTrue(of_rule(findings, "B-03"), findings)
        rows = nested_blocks()
        rows[0] = dict(rows[0], ids=["s-4-1", "s-9"])
        findings = brief_lint.lint_brief(make_brief(rows), MEMO_NESTED, language="en")
        self.assertIn("s-9", " ".join(row["hint"] for row in of_rule(findings, "B-03")))


class PartitionTest(unittest.TestCase):
    """D-229: every leaf of the memo is kept (bound to a block) or omitted (listed in the header comment)."""

    OTHER = "Other matters: retention of the mailing list (risk medium)."

    def lint(self, blocks, omitted=None, memo=MEMO_NESTED, **options) -> list[dict]:
        return brief_lint.lint_brief(make_brief(blocks, omitted=omitted, **options), memo, language="en")

    def test_every_leaf_bound_and_an_empty_omitted_list_are_clean(self):
        self.assertEqual([], self.lint(nested_blocks()))
        self.assertEqual([], self.lint(nested_blocks(), omitted=[]))

    def test_a_medium_leaf_omitted_is_clean(self):
        self.assertEqual([], self.lint(nested_blocks(**{"s-5": None}), omitted=["s-5"], other=self.OTHER))
        parsed = brief_lint.parse_brief(make_brief(nested_blocks(**{"s-5": None}), omitted=["s-5"]), "en")
        self.assertEqual(["s-5"], parsed["omitted"])
        self.assertEqual([], brief_lint.parse_brief(make_brief(nested_blocks()), "en")["omitted"])

    def test_several_ids_in_one_comment(self):
        memo = MEMO_NESTED.replace("Risk: high. The box is ticked by default today.",
                                   "Risk: medium. The box is ticked by default today.")
        text = make_brief([nested_blocks()[0]], omitted=["s-4-2", "s-5"])
        self.assertEqual(["s-4-2", "s-5"], brief_lint.parse_brief(text, "en")["omitted"])
        self.assertEqual([], brief_lint.lint_brief(text.replace("Risk: high.", "Risk: medium."), memo, language="en"))

    def test_the_omitted_comment_counts_only_above_the_first_part(self):
        text = make_brief(nested_blocks(**{"s-5": None}), main_extra="<!-- omitted §s-5 -->")
        self.assertEqual([], brief_lint.parse_brief(text, "en")["omitted"])
        self.assertEqual(["B-04"], rules(brief_lint.lint_brief(text, MEMO_NESTED, language="en")))

    def test_a_leaf_neither_bound_nor_omitted_is_b04(self):
        findings = self.lint(nested_blocks(**{"s-4-2": None, "s-5": None}), omitted=["s-4-2"])
        self.assertEqual(["B-04"], rules(findings))
        b04 = of_rule(findings, "B-04")
        self.assertEqual(1, len(b04), findings)
        self.assertIn("s-5", b04[0]["hint"])
        self.assertIn("omitted", b04[0]["hint"])
        self.assertEqual("major", b04[0]["severity"])

    def test_an_omitted_high_leaf_is_a_b05_blocker(self):
        findings = self.lint(nested_blocks(**{"s-4-1": None}), omitted=["s-4-1"])
        self.assertEqual(["B-05"], rules(findings))
        self.assertEqual(("blocker", "s-header"), (findings[0]["severity"], findings[0]["section_id"]))
        self.assertIn("s-4-1", findings[0]["hint"])
        self.assertIn("high", findings[0]["hint"])

    def test_an_omitted_id_that_is_not_a_leaf_is_b03(self):
        for sid in ("s-4", "s-9", "s-2"):
            with self.subTest(sid=sid):
                findings = self.lint(nested_blocks(), omitted=[sid])
                self.assertEqual(["B-03"], rules(findings))
                self.assertEqual(("major", "s-header"), (findings[0]["severity"], findings[0]["section_id"]))
                self.assertIn(sid, findings[0]["hint"])

    def test_a_leaf_both_bound_and_omitted_is_b03(self):
        findings = self.lint(nested_blocks(), omitted=["s-5"])
        self.assertEqual(["B-03"], rules(findings))
        self.assertIn("s-5", findings[0]["hint"])
        self.assertEqual("major", findings[0]["severity"])

    def test_the_other_matters_line_is_no_block(self):
        text = make_brief(nested_blocks(**{"s-5": None}), omitted=["s-5"], other=self.OTHER)
        parsed = brief_lint.parse_brief(text, "en")
        self.assertEqual(["s-b1", "s-b2"], [row["id"] for row in parsed["blocks"]])

    def test_russian_partition(self):
        rows = RussianTest().ru_blocks(**{"s-4-2": None})
        other = f"{i18n.t('ru', 'memo.brief.other_label')}: срок исковой давности (риск низкий)."
        text = make_brief(rows, language="ru", omitted=["s-4-2"], other=other)
        self.assertEqual([], brief_lint.lint_brief(text, MEMO_RU, language="ru"))
        high = make_brief(RussianTest().ru_blocks(**{"s-4-1": None}), language="ru", omitted=["s-4-1"])
        self.assertEqual(["B-05"], rules(brief_lint.lint_brief(high, MEMO_RU, language="ru")))


NBSP, NARROW_NBSP = chr(0x00A0), chr(0x202F)
"""The spaces a memo or a brief may write inside an amount (B-11 removes them)."""

MEMO_AMOUNTS = MEMO_FLAT.replace(
    "Consent is the lawful basis for the emails [[src:gdpr Art. 6(1)]].",
    "Consent is the lawful basis for the emails [[src:gdpr Art. 6(1)]]. The fine is up to "
    + "297" + NBSP + "600" + NBSP + "₽ or 4 % of turnover, and the regulator may add £1,500 at most.",
)

MEMO_RU_AMOUNTS = MEMO_RU.replace(
    "Присоединившаяся сторона вправе потребовать расторжения договора [[src:gk-rf п. 2 ст. 428]].",
    "Присоединившаяся сторона вправе потребовать расторжения договора [[src:gk-rf п. 2 ст. 428]]. "
    "Неустойка составляет до 300 000 руб.",
)


class AmountTest(unittest.TestCase):
    """D-229, B-11: every amount of the brief occurs in the memo, spaces aside; dates are not checked."""

    def lint(self, sentence: str) -> list[dict]:
        return brief_lint.lint_brief(make_brief(FLAT_BLOCKS, main_extra=sentence), MEMO_AMOUNTS, language="en")

    def test_an_amount_the_memo_gives_is_clean(self):
        for sentence in (
            "The fine is up to 297 600 ₽.",
            "The fine is up to 297" + NARROW_NBSP + "600 ₽.",
            "The fine may reach 4% of turnover.",
            "The regulator may add £1,500 at most.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual([], self.lint(sentence))

    def test_an_amount_the_memo_does_not_give_is_b11(self):
        for sentence, shown in (
            ("The fine is up to 300 000 ₽.", "300 000 ₽"),
            ("The fine may reach 14% of turnover.", "14%"),
            ("The regulator may add £2,000.", "£2,000"),
            ("The fine is about 600 ₽.", "600 ₽"),
        ):
            with self.subTest(sentence=sentence):
                findings = self.lint(sentence)
                self.assertEqual(["B-11"], rules(findings))
                self.assertEqual(("major", "s-main"), (findings[0]["severity"], findings[0]["section_id"]))
                self.assertIn(shown, findings[0]["hint"])

    def test_an_amount_soft_wrapped_in_the_memo_is_one_amount(self):
        """Final review (D-229 addendum): a memo paragraph wrapped inside an amount renders it whole."""
        memo = MEMO_AMOUNTS.replace("297" + NBSP + "600" + NBSP + "₽", "297\n600 ₽")
        self.assertIn("up to 297\n600 ₽ or", memo)

        def lint(sentence: str) -> list[dict]:
            return brief_lint.lint_brief(make_brief(FLAT_BLOCKS, main_extra=sentence), memo, language="en")

        self.assertEqual([], lint("The fine is up to 297 600 ₽."))
        self.assertEqual(["B-11"], rules(lint("The fine is up to 300 000 ₽.")))
        self.assertEqual(["B-11"], rules(lint("The fine is about 600 ₽.")))  # the wrap's tail is no amount

    def test_an_amount_soft_wrapped_in_the_brief_is_one_amount(self):
        self.assertEqual([], self.lint("The fine is up to 297\n600 ₽."))
        brief = make_brief(FLAT_BLOCKS, main_extra="The fine is up to 300\n000 ₽.")
        findings = brief_lint.lint_brief(brief, MEMO_AMOUNTS, language="en")
        self.assertEqual(["B-11"], rules(findings))
        self.assertIn("300 000 ₽", findings[0]["hint"])
        self.assertEqual(brief.split("\n").index("The fine is up to 300") + 1, findings[0]["line"])

    def test_a_hard_break_or_a_new_quote_level_ends_the_run(self):
        """Fix round 2 (final re-review): a Markdown hard break (two trailing spaces or a backslash), a change of
        blockquote depth and a list item inside a blockquote keep `297` and `600 RUB` apart, as Markdown renders
        them; a brief that states `600 RUB` is clean."""
        brief = make_brief(FLAT_BLOCKS, main_extra="The filing fee is 600 RUB.")
        for snippet in (
            "The filing details are:\nCase 297  \n600 RUB is the filing fee.",
            "The filing details are:\nCase 297\\\n600 RUB is the filing fee.",
            "> Case 297\n>> 600 RUB is the filing fee.",
            "> Case 297\n> - 600 RUB is the filing fee.",
        ):
            memo = MEMO_AMOUNTS.replace(
                "The company sends marketing emails to its customers.\n",
                "The company sends marketing emails to its customers.\n\n" + snippet + "\n",
            )
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, memo)
                self.assertEqual([], brief_lint.lint_brief(brief, memo, language="en"))

    def test_blank_lines_and_list_items_still_split_amounts(self):
        """Only a soft wrap inside one paragraph joins: a new paragraph or a new list item does not."""
        self.assertEqual(["B-11"], rules(self.lint("The fine is up to 297\n\n600 ₽ in all.")))
        self.assertEqual(["B-11"], rules(self.lint("- The fine is up to 297\n- 600 ₽ in all.")))

    def test_dates_and_day_counts_are_not_checked(self):
        self.assertEqual([], self.lint("Product acts by 2026-10-01, within 7 days of the notice of 1 October."))

    def test_russian_amounts(self):
        def lint_ru(sentence: str) -> list[dict]:
            text = make_brief(RussianTest().ru_blocks(), language="ru", main_extra=sentence)
            return brief_lint.lint_brief(text, MEMO_RU_AMOUNTS, language="ru")

        self.assertEqual([], lint_ru("Неустойка — до 300 000 рублей."))
        self.assertEqual(["B-11"], rules(lint_ru("Неустойка — до 500 000 руб.")))

    def test_the_check_is_named_in_every_pack(self):
        self.assertEqual("an amount that is not in the memorandum", i18n.t("en", "memo.brief.checks.B-11"))
        for code in ("de", "fr", "es", "ru"):
            with self.subTest(code=code):
                self.assertTrue(i18n.t(code, "memo.brief.checks.B-11"))


def words(count: int) -> str:
    """`count` filler words (no token, no bullet) for a part at or over its budget."""
    return " ".join(["records"] * count)


class BudgetTest(unittest.TestCase):
    """D-229 fix round 2, B-12: each part of the brief has a word budget; one finding per part over it."""

    ALL_PARTS = ("main", "conclusions", "actions", "assumptions")

    def lint(self, **options) -> list[dict]:
        options.setdefault("parts", self.ALL_PARTS)
        return of_rule(brief_lint.lint_brief(make_brief(FLAT_BLOCKS, **options), MEMO_FLAT, language="en"), "B-12")

    def test_the_budgets(self):
        self.assertEqual(
            (80, 100, 60, 30, 60),
            (limits.DECISION_BRIEF_MAIN_WORDS, limits.DECISION_BRIEF_BLOCK_WORDS, limits.DECISION_BRIEF_OTHER_WORDS,
             limits.DECISION_BRIEF_ACTION_WORDS, limits.DECISION_BRIEF_ASSUMPTIONS_WORDS),
        )
        self.assertEqual("major", brief_lint.SEVERITY["B-12"])
        self.assertEqual("a part is longer than its word budget", i18n.t("en", "memo.brief.checks.B-12"))
        for code in ("de", "fr", "es", "ru"):
            with self.subTest(code=code):
                self.assertTrue(i18n.t(code, "memo.brief.checks.B-12"))

    def test_a_brief_within_every_budget_is_clean(self):
        self.assertEqual([], self.lint())

    def test_the_bottom_line(self):
        base = len(EN_TEXT["main"].split())
        self.assertEqual([], self.lint(main_extra=words(80 - base)))
        [row] = self.lint(main_extra=words(81 - base))
        self.assertEqual(("major", "s-main"), (row["severity"], row["section_id"]))
        self.assertIn("81", row["hint"])
        self.assertIn("80", row["hint"])
        self.assertIn("Bottom line", row["hint"])

    def test_a_blockquote_counts_as_b01_counts_it(self):
        """Fix round 3: a blockquote (itself B-07) never lets a part undercount its budget."""
        base = len(EN_TEXT["main"].split())
        self.assertEqual([], self.lint(main_extra="> " + words(80 - base)))
        [row] = self.lint(main_extra="> " + words(81 - base))
        self.assertEqual("s-main", row["section_id"])
        self.assertIn("81", row["hint"])

    def test_a_conclusion_block(self):
        body = FLAT_BLOCKS[1]["body"]  # the token is not counted
        base = len(lint.SRC_TOKEN.sub("", body).split()) + len(FLAT_BLOCKS[1]["risk"].split())

        def with_body(count: int) -> list[dict]:
            row = dict(FLAT_BLOCKS[1], body=body + " " + words(count - base))
            return of_rule(brief_lint.lint_brief(make_brief([FLAT_BLOCKS[0], row]), MEMO_FLAT, language="en"), "B-12")

        self.assertEqual([], with_body(100))
        [row] = with_body(101)
        self.assertEqual("s-b2", row["section_id"])
        self.assertIn("100", row["hint"])

    def test_the_other_matters_sentence(self):
        label = "Other matters:"
        self.assertEqual([], self.lint(other=label + " " + words(58) + "."))
        [row] = self.lint(other=label + " " + words(60) + ".")
        self.assertEqual("document", row["section_id"])
        self.assertIn("60", row["hint"])
        self.assertIn("Other matters", row["hint"])

    def test_each_action(self):
        ok = "1. Product " + words(29)
        self.assertEqual([], self.lint(actions=ok + "\n2. Legal " + words(29)))
        rows = self.lint(actions=ok + "\n2. Legal " + words(30) + "\n3. Sales " + words(30))
        self.assertEqual(2, len(rows), rows)
        self.assertEqual({"s-actions"}, {row["section_id"] for row in rows})
        self.assertIn("30", rows[0]["hint"])

    def test_the_assumptions_are_one_short_paragraph(self):
        self.assertEqual([], self.lint(assumptions=words(60)))
        [row] = self.lint(assumptions=words(61))
        self.assertEqual("s-assumptions", row["section_id"])
        self.assertIn("60", row["hint"])
        for text in ("- The emails go only to existing customers.",
                     "The emails go to customers.\n\nThe list is kept two years."):
            with self.subTest(text=text):
                [row] = self.lint(assumptions=text)
                self.assertIn("one paragraph", row["hint"])

    def test_the_writer_sees_the_budgets(self):
        text = brief_lint.writer_labels("en")
        self.assertIn("Word budgets", text)
        for needle in ("«Bottom line» 80", "each conclusion block 100", "the Other matters sentence 60",
                       "each action 30", "«What the answer depends on» one paragraph, no list, 60"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class VerdictTest(unittest.TestCase):
    def test_single_binding_with_another_level_is_a_blocker(self):
        rows = nested_blocks(**{"s-4-1": dict(nested_blocks()[0], risk="Risk: medium. The box is ticked.")})
        findings = of_rule(brief_lint.lint_brief(make_brief(rows), MEMO_NESTED, language="en"), "B-05")
        self.assertEqual(1, len(findings))
        self.assertEqual("blocker", findings[0]["severity"])

    def merged(self, risk: str, ids=("s-4-1", "s-4-2"), memo=MEMO_NESTED, body_extra: str = "") -> list[dict]:
        merged = block(
            "Lawful basis",
            list(ids),
            "The pre-ticked box fails, but a soft opt-in works [[src:gdpr Art. 6(1)]]." + body_extra,
            risk,
        )
        rest = [row for row in nested_blocks() if row["ids"][0] not in ids]
        return brief_lint.lint_brief(make_brief([merged, *rest]), memo, language="en")

    def test_merged_leaves_with_different_verdicts_are_a_blocker(self):
        """D-228: a block merges only leaves of one verdict; the highest one no longer covers a mix."""
        for risk in ("Risk: high. The box is ticked by default today.", "Risk: low. A soft opt-in works."):
            with self.subTest(risk=risk):
                findings = self.merged(risk)
                self.assertEqual(["B-05"], rules(findings))
                self.assertEqual(1, len(findings), findings)
                self.assertEqual(("blocker", "s-b1"), (findings[0]["severity"], findings[0]["section_id"]))
                self.assertIn("s-4-1 «high», s-4-2 «low»", findings[0]["hint"])

    def test_merged_leaves_with_the_same_verdict_are_clean(self):
        memo = MEMO_NESTED.replace("Risk: low. The customers bought similar products.",
                                   "Risk: medium. The customers bought similar products.")
        self.assertEqual({"s-4-1": "high", "s-4-2": "medium", "s-5": "medium"},
                         brief_lint.memo_facts(memo, "en")["leaves"])
        self.assertEqual([], self.merged("Risk: medium. Both turn on the documentation.", ids=("s-4-2", "s-5"),
                                         memo=memo))
        findings = self.merged("Risk: high. Both turn on the documentation.", ids=("s-4-2", "s-5"), memo=memo)
        self.assertEqual(["B-05"], rules(findings))

    def test_undetermined_mixed_with_a_determinate_leaf_is_a_finding(self):
        findings = brief_lint.lint_brief(
            make_brief([
                block(
                    "Consent and profiling",
                    ["s-4-1", "s-4-3"],
                    "The pre-ticked box fails [[src:gdpr Art. 6(1)]]. Profiling is "
                    + i18n.t("en", "memo.brief.unconfirmed") + ".",
                    "Risk: high. The box is ticked by default today.",
                ),
                block(
                    "Legitimate interest",
                    ["s-4-2"],
                    "Existing customers may be contacted on a soft opt-in [[src:pecr reg 22]].",
                    "Risk: low. The customers bought similar products.",
                ),
            ]),
            MEMO_RISKLESS,
            language="en",
        )
        self.assertEqual(["B-05"], rules(findings))

    def riskless(self, body: str) -> list[dict]:
        rows = [
            block(
                "Consent",
                ["s-4-1"],
                "The pre-ticked box does not give valid consent [[src:gdpr Art. 6(1)]].",
                "Risk: high. The box is ticked by default today.",
            ),
            block(
                "Legitimate interest",
                ["s-4-2"],
                "Existing customers may be contacted on a soft opt-in [[src:pecr reg 22]].",
                "Risk: low. The customers bought similar products.",
            ),
            block("Profiling", ["s-4-3"], body, "Risk: undetermined. Profiling was not examined."),
        ]
        return brief_lint.lint_brief(make_brief(rows), MEMO_RISKLESS, language="en")

    def test_a_riskless_leaf_is_undetermined_and_carries_the_unconfirmed_wording(self):
        unconfirmed = i18n.t("en", "memo.brief.unconfirmed")
        self.assertEqual([], self.riskless(f"Whether profiling is lawful is {unconfirmed}."))
        # the wording is matched case-insensitively
        self.assertEqual([], self.riskless(f"Profiling: {unconfirmed.capitalize()}."))
        findings = self.riskless("Whether profiling is lawful was not examined.")
        self.assertEqual(["B-05"], rules(findings))
        self.assertIn("s-4-3", findings[0]["hint"])

    def test_a_block_without_a_risk_line_is_b05(self):
        rows = nested_blocks(**{"s-5": dict(nested_blocks()[2], risk="")})
        findings = brief_lint.lint_brief(make_brief(rows), MEMO_NESTED, language="en")
        self.assertEqual(["B-05"], rules(findings))
        self.assertEqual("s-b3", findings[0]["section_id"])


class RussianTest(unittest.TestCase):
    def ru_blocks(self, **override) -> list[dict]:
        rows = {
            "s-4-1": block(
                "Обременительные условия",
                ["s-4-1"],
                "Клиент вправе потребовать расторжения договора [[src:gk-rf п. 2 ст. 428]].",
                "Риск: высокий. Условие явно обременительно для клиента.",
            ),
            "s-4-2": block(
                "Срок исковой давности",
                ["s-4-2"],
                "Срок исковой давности составляет три года [[src:gk-rf ст. 196]].",
                "Риск: низкий. Срок ещё не истёк.",
            ),
        }
        rows.update(override)
        return [row for row in rows.values() if row is not None]

    def lint(self, rows) -> list[dict]:
        return brief_lint.lint_brief(make_brief(rows, language="ru"), MEMO_RU, language="ru")

    def test_facts_section_is_not_a_leaf(self):
        facts = brief_lint.memo_facts(MEMO_RU, "ru")
        self.assertEqual({"s-4-1": "high", "s-4-2": "low"}, facts["leaves"])
        self.assertEqual([], self.lint(self.ru_blocks()))

    def test_parent_binding_unbound_leaf_and_merged_verdict(self):
        parent = self.ru_blocks(**{"s-4-1": dict(self.ru_blocks()[0], ids=["s-4"])})
        self.assertIn("B-03", rules(self.lint(parent)))

        unbound = self.lint(self.ru_blocks(**{"s-4-2": None}))
        self.assertEqual(["B-04"], rules(unbound))
        self.assertIn("s-4-2", unbound[0]["hint"])

        merged = block(
            "Условия и срок",
            ["s-4-1", "s-4-2"],
            "Клиент вправе потребовать расторжения договора [[src:gk-rf п. 2 ст. 428]].",
            "Риск: низкий. Срок ещё не истёк.",
        )
        self.assertEqual(["B-05"], rules(self.lint([merged])))
        merged_high = dict(merged, risk="Риск: высокий. Условие явно обременительно для клиента.")
        self.assertEqual(["B-05"], rules(self.lint([merged_high])), "D-228: leaves of different verdicts")

    def test_b06_cyrillic_pinpoint_pairs(self):
        self.assertEqual([], self.lint(self.ru_blocks()))
        wrong = self.ru_blocks(**{"s-4-1": dict(
            self.ru_blocks()[0],
            body="Клиент вправе потребовать расторжения договора [[src:gk-rf п. 2 ст. 429]].",
        )})
        findings = self.lint(wrong)
        self.assertEqual(["B-06"], rules(findings))
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertIn("ст. 429", findings[0]["hint"])

    def test_b06_english_pinpoints_are_normalised(self):
        def with_token(token: str) -> list[dict]:
            rows = [dict(FLAT_BLOCKS[0], body=f"Consent is the lawful basis for the emails {token}."), FLAT_BLOCKS[1]]
            return brief_lint.lint_brief(make_brief(rows), MEMO_FLAT, language="en")

        self.assertEqual([], with_token("[[src:gdpr art 6(1)]]"))
        self.assertEqual(["B-06"], rules(with_token("[[src:gdpr art 22]]")))
        self.assertEqual(["B-06"], rules(with_token("[[src:eprivacy art 13]]")))


class BlockListTest(unittest.TestCase):
    def one_block(self) -> dict:
        return brief_lint.parse_brief(make_brief([FLAT_BLOCKS[0]]), "en")

    def test_block_ids_of_a_one_block_brief(self):
        self.assertEqual(["s-header", "s-main", "s-b1", "s-actions"], brief_lint.block_ids(self.one_block()))

    def test_block_ids_with_assumptions(self):
        parsed = brief_lint.parse_brief(
            make_brief(FLAT_BLOCKS, parts=("main", "conclusions", "actions", "assumptions")), "en"
        )
        self.assertEqual(
            ["s-header", "s-main", "s-b1", "s-b2", "s-actions", "s-assumptions"], brief_lint.block_ids(parsed)
        )

    def test_section_list_of_a_one_block_brief(self):
        self.assertEqual(
            "s-header = the title and header lines; s-main = «Bottom line»; "
            "s-b1 = «Lawful basis for marketing emails»; s-actions = «What to do»",
            brief_lint.section_list(self.one_block()),
        )

    def test_parse_brief_shape(self):
        parsed = self.one_block()
        self.assertEqual({"document", "parts", "blocks", "block_ids", "omitted"}, set(parsed))
        self.assertEqual({"main", "conclusions", "actions", "assumptions"}, set(parsed["parts"]))
        self.assertIsNone(parsed["parts"]["assumptions"])
        row = parsed["blocks"][0]
        self.assertEqual("s-b1", row["id"])
        self.assertEqual(["s-4"], row["bindings"])
        self.assertEqual("medium", row["verdict_key"])
        self.assertEqual(["gdpr"], [token["id"] for token in row["tokens"]])
        self.assertIn("s-b1", parsed["block_ids"].values())

    def test_block_ids_constant(self):
        self.assertEqual(
            ("s-header", "s-main", "s-b1", "s-b2", "s-b3", "s-b4", "s-b5", "s-b6", "s-b7", "s-actions",
             "s-assumptions"),
            brief_lint.BLOCK_IDS,
        )
        self.assertEqual("document", brief_lint.DOCUMENT_ID)
        self.assertEqual(limits.DECISION_BRIEF_MAX_BLOCKS, sum(1 for b in brief_lint.BLOCK_IDS if b.startswith("s-b")))


class ChangedBlocksTest(unittest.TestCase):
    """D-228: the blocks a revision changed, for the scoped fidelity re-review."""

    def changed(self, before: str, after: str, language: str = "en") -> list[str]:
        return brief_lint.changed_blocks(before, after, language)

    def test_an_edit_in_one_block_names_that_block_only(self):
        before = make_brief(FLAT_BLOCKS)
        edited = dict(FLAT_BLOCKS[1], body="The list may be kept for two years [[src:pecr reg 22]] if documented.")
        self.assertEqual(["s-b2"], self.changed(before, make_brief([FLAT_BLOCKS[0], edited])))

    def test_an_identical_version_changes_nothing(self):
        text = make_brief(FLAT_BLOCKS)
        self.assertEqual([], self.changed(text, text))
        self.assertEqual([], self.changed(text, text.replace("\n\n", "\n\n\n")), "blank lines are no change")

    def test_the_header_and_the_parts_are_blocks_too(self):
        before = make_brief(FLAT_BLOCKS, parts=("main", "conclusions", "actions", "assumptions"))
        after = before.replace("2026-09-23", "2026-09-24")
        after = after.replace(EN_TEXT["main"], "The company may rely on consent. The overall risk is medium.")
        after = after.replace(EN_TEXT["actions"], "1. Product ships the unticked box.")
        after = after.replace(EN_TEXT["assumptions"], "The answer holds while the emails go to every customer.")
        self.assertEqual(["s-header", "s-main", "s-actions", "s-assumptions"], self.changed(before, after))

    def test_a_block_in_only_one_version_is_changed(self):
        one, two = make_brief([FLAT_BLOCKS[0]]), make_brief(FLAT_BLOCKS)
        self.assertEqual(["s-b2"], self.changed(one, two))
        self.assertEqual(["s-b2"], self.changed(two, one))
        with_assumptions = make_brief(FLAT_BLOCKS, parts=("main", "conclusions", "actions", "assumptions"))
        self.assertEqual(["s-assumptions"], self.changed(two, with_assumptions))

    def test_a_changed_conclusions_preamble_scopes_nothing(self):
        """Fix round 1: text above the first `###` belongs to no block, so its change means a full review."""
        before = make_brief(FLAT_BLOCKS)
        heading = "## " + labels("en")["conclusions"] + "\n\n"
        added = before.replace(heading, heading + "Consent also covers the partner emails.\n\n")
        self.assertIsNone(self.changed(before, added))
        edited = added.replace("the partner emails", "the partner and reseller emails")
        self.assertIsNone(self.changed(added, edited))
        self.assertEqual([], self.changed(added, added.replace("\n\n", "\n\n\n")))

    def test_a_changed_omitted_list_scopes_nothing(self):
        """D-229: the omitted list decides what the brief leaves out, so its change means a full review."""
        before = make_brief(nested_blocks(**{"s-5": None}), omitted=["s-5"])
        after = make_brief(nested_blocks(**{"s-4-2": None, "s-5": None}), omitted=["s-4-2", "s-5"])
        self.assertIsNone(self.changed(before, after))
        self.assertIsNone(self.changed(make_brief(nested_blocks()), before))
        self.assertEqual([], self.changed(before, before.replace("<!-- omitted §s-5 -->", "<!--  omitted  §s-5 -->")))
        # An empty comment omits nothing: the list is unchanged, only the header text is.
        empty = make_brief(nested_blocks(), omitted=[])
        self.assertEqual(["s-header"], self.changed(make_brief(nested_blocks()), empty))

    def test_a_changed_heading_or_binding_is_a_change(self):
        before = make_brief(FLAT_BLOCKS)
        retitled = before.replace("### Lawful basis for marketing emails", "### Lawful basis")
        self.assertEqual(["s-b1"], self.changed(before, retitled))
        rebound = before.replace("<!-- from §s-5 -->", "<!-- from §s-4 §s-5 -->")
        self.assertEqual(["s-b2"], self.changed(before, rebound))


class WriterLabelsTest(unittest.TestCase):
    def test_labels_carry_headings_header_risk_literal_and_unconfirmed_wording(self):
        for language in ("en", "ru"):
            with self.subTest(language=language):
                text = brief_lint.writer_labels(language)
                for value in labels(language).values():
                    self.assertIn(value, text)
                self.assertIn(lint.grammar(language).risk_literal, text)
                self.assertIn(i18n.t(language, "memo.brief.unconfirmed"), text)

    def test_labels_carry_the_omitted_comment_and_the_other_matters_label(self):
        """D-229: the writer copies the omitted-comment syntax and the localized label of the omitted leaves."""
        self.assertEqual("Other matters", i18n.t("en", "memo.brief.other_label"))
        self.assertEqual("Прочие вопросы", i18n.t("ru", "memo.brief.other_label"))
        for language in ("en", "ru", "de", "fr", "es"):
            with self.subTest(language=language):
                text = brief_lint.writer_labels(language)
                self.assertIn("`<!-- omitted §s-8 §s-10-1 -->`", text)
                self.assertIn(f"`{i18n.t(language, 'memo.brief.other_label')}: ", text)


class LengthTest(unittest.TestCase):
    def test_b01_counts_body_headings_and_weighted_sources(self):
        base = make_brief(FLAT_BLOCKS)
        parsed = brief_lint.parse_brief(base, "en")
        words = brief_lint.brief_words(parsed)
        body = lint.body_words(parsed["document"])
        self.assertEqual(2 * limits.DECISION_BRIEF_SOURCE_WORD_WEIGHT, words - body - self.heading_words(parsed))

    def heading_words(self, parsed) -> int:
        return sum(len(heading["title"].split()) for heading in parsed["document"]["headings"])

    def padded(self, total: int) -> str:
        base = make_brief(FLAT_BLOCKS)
        missing = total - brief_lint.brief_words(brief_lint.parse_brief(base, "en"))
        return make_brief(FLAT_BLOCKS, main_extra=" ".join(["word"] * missing))

    def test_b01_at_and_over_the_soft_cap(self):
        cap = limits.DECISION_BRIEF_SOFT_CAP
        at_cap = self.padded(cap)
        self.assertEqual(cap, brief_lint.brief_words(brief_lint.parse_brief(at_cap, "en")))
        self.assertEqual([], of_rule(brief_lint.lint_brief(at_cap, MEMO_FLAT, language="en"), "B-01"))
        over = self.padded(cap + 1)
        findings = of_rule(brief_lint.lint_brief(over, MEMO_FLAT, language="en"), "B-01")
        self.assertEqual(1, len(findings))
        self.assertEqual("minor", findings[0]["severity"], "D-228: up to the hard cap B-01 is minor")
        self.assertIn(str(cap + 1), findings[0]["hint"])
        self.assertIn(str(cap), findings[0]["hint"])
        self.assertIn("about three pages", findings[0]["hint"])  # D-227
        self.assertNotIn("two pages", findings[0]["hint"])

    def test_b01_is_minor_up_to_the_hard_cap_and_major_above(self):
        """D-228: a brief at or under the hard cap never sends the writer back for length."""
        hard = limits.DECISION_BRIEF_HARD_CAP
        for words, severity in ((1150, "minor"), (hard, "minor"), (hard + 1, "major"), (1250, "major")):
            with self.subTest(words=words):
                findings = of_rule(brief_lint.lint_brief(self.padded(words), MEMO_FLAT, language="en"), "B-01")
                self.assertEqual([severity], [row["severity"] for row in findings])
                self.assertIn(str(words), findings[0]["hint"])

    def test_the_b01_hint_keeps_an_action_due_within_14_days(self):
        """D-229 fix 3 (replay 5): shortening drops only the actions the actions rule does not keep."""
        findings = of_rule(brief_lint.lint_brief(self.padded(1250), MEMO_FLAT, language="en"), "B-01")
        hint = findings[0]["hint"]
        self.assertIn("drop their assumptions and those of their actions the actions rule does not keep", hint)
        self.assertIn("an action due within 14 days of the memo date stays", hint)
        self.assertNotIn("drop their actions and assumptions", hint)

    def test_the_length_check_is_named_about_three_pages(self):
        """D-227: the Word measurement puts the soft cap at about three pages."""
        self.assertEqual("longer than about three pages", i18n.t("en", "memo.brief.checks.B-01"))
        self.assertEqual("длиннее примерно трёх страниц", i18n.t("ru", "memo.brief.checks.B-01"))

    def test_limits(self):
        self.assertEqual(1100, limits.DECISION_BRIEF_SOFT_CAP)  # D-228
        self.assertEqual(1200, limits.DECISION_BRIEF_HARD_CAP)  # D-228
        self.assertEqual(12, limits.DECISION_BRIEF_SOURCE_WORD_WEIGHT)
        self.assertEqual(7, limits.DECISION_BRIEF_MAX_BLOCKS)  # D-228


class StructureTest(unittest.TestCase):
    def lint(self, text: str) -> list[dict]:
        return brief_lint.lint_brief(text, MEMO_FLAT, language="en")

    def test_b02_missing_actions(self):
        self.assertEqual(["B-02"], rules(self.lint(make_brief(FLAT_BLOCKS, parts=("main", "conclusions")))))

    def test_b02_parts_out_of_order(self):
        text = make_brief(FLAT_BLOCKS, parts=("main", "actions", "conclusions"))
        self.assertEqual(["B-02"], rules(self.lint(text)))

    def test_b02_assumptions_before_actions(self):
        text = make_brief(FLAT_BLOCKS, parts=("main", "conclusions", "assumptions", "actions"))
        self.assertEqual(["B-02"], rules(self.lint(text)))

    def test_b02_seven_blocks_are_allowed(self):
        text = make_brief([FLAT_BLOCKS[0], FLAT_BLOCKS[1]] * 3 + [FLAT_BLOCKS[0]])
        self.assertEqual([], self.lint(text))
        self.assertEqual(["s-b1", "s-b2", "s-b3", "s-b4", "s-b5", "s-b6", "s-b7"],
                         [block_id for block_id in brief_lint.block_ids(brief_lint.parse_brief(text, "en"))
                          if block_id.startswith("s-b")])

    def test_b02_eight_blocks(self):
        findings = self.lint(make_brief([FLAT_BLOCKS[0], FLAT_BLOCKS[1]] * 4))
        self.assertEqual(["B-02"], rules(findings))
        self.assertIn("8 blocks", findings[0]["hint"])
        self.assertIn(str(limits.DECISION_BRIEF_MAX_BLOCKS), findings[0]["hint"])

    def test_b02_missing_question_line(self):
        findings = self.lint(make_brief(FLAT_BLOCKS, header=("date", "jurisdictions")))
        self.assertEqual(["B-02"], rules(findings))
        self.assertEqual("s-header", findings[0]["section_id"])

    def test_b02_accepts_header_lines_as_separate_paragraphs(self):
        """D-226: the template separates the header lines by blank lines; B-02 still finds each one."""
        self.assertEqual([], self.lint(make_brief(FLAT_BLOCKS, header_gap=True)))
        findings = self.lint(make_brief(FLAT_BLOCKS, header=("date", "question"), header_gap=True))
        self.assertEqual(["B-02"], rules(findings))
        self.assertEqual("s-header", findings[0]["section_id"])
        ru = make_brief(RussianTest().ru_blocks(), language="ru", header_gap=True)
        self.assertEqual([], brief_lint.lint_brief(ru, MEMO_RU, language="ru"))


class FormTest(unittest.TestCase):
    def lint(self, text: str) -> list[dict]:
        return brief_lint.lint_brief(text, MEMO_FLAT, language="en")

    def test_b07_blockquote_quote_token_and_sources_marker(self):
        quote = make_brief(FLAT_BLOCKS, main_extra="> Consent must be freely given.")
        self.assertEqual(["B-07"], rules(self.lint(quote)))
        token = make_brief(FLAT_BLOCKS, main_extra="The rule is clear [[q:q-1]].")
        self.assertEqual(["B-07"], rules(self.lint(token)))
        marker = make_brief(FLAT_BLOCKS) + "\n" + lint.SOURCES_MARKER + "\n"
        self.assertEqual(["B-07"], rules(self.lint(marker)))

    def test_b08_risk_line_in_another_form(self):
        rows = [dict(FLAT_BLOCKS[0], risk="Risk - medium. The basis holds."), FLAT_BLOCKS[1]]
        findings = self.lint(make_brief(rows))
        self.assertIn("B-08", rules(findings))
        self.assertEqual("major", of_rule(findings, "B-08")[0]["severity"])

    def test_b08_risk_line_must_be_the_last_paragraph(self):
        # Fix round 1 (Sol): a correct risk line followed by an ordinary paragraph is not the
        # block's risk line — the verdict is read from the last paragraph only, as L-07 does.
        rows = [
            dict(
                FLAT_BLOCKS[0],
                risk="Risk: medium. The basis holds while the opt-in box stays unticked.\n\n"
                "Product checks the box before launch.",
            ),
            FLAT_BLOCKS[1],
        ]
        findings = self.lint(make_brief(rows))
        self.assertEqual(["B-05", "B-08"], rules(findings))
        self.assertEqual("s-b1", of_rule(findings, "B-08")[0]["section_id"])
        self.assertIn("last paragraph", of_rule(findings, "B-08")[0]["hint"])
        parsed = brief_lint.parse_brief(make_brief(rows), "en")
        self.assertIsNone(parsed["blocks"][0]["verdict_key"])

    def test_b09_reference_to_the_memorandum(self):
        text = make_brief(FLAT_BLOCKS, main_extra="The detail is set out as the memorandum explains.")
        findings = self.lint(text)
        self.assertEqual(["B-09"], rules(findings))
        self.assertEqual("s-main", findings[0]["section_id"])

    def test_b09_matches_whole_words_only(self):
        # Fix round 1 (controller ruling): the edge rule of `lint.placeholder_pattern`, case-blind.
        for sentence in ("The memory of the board is short.", "A memorial plaque is not a notice."):
            with self.subTest(sentence=sentence):
                self.assertEqual([], self.lint(make_brief(FLAT_BLOCKS, main_extra=sentence)))
        for sentence in ("The detail is set out as the Memo explains.", "For the deadline, see section 4."):
            with self.subTest(sentence=sentence):
                self.assertEqual(["B-09"], rules(self.lint(make_brief(FLAT_BLOCKS, main_extra=sentence))))

    def test_b09_russian_whole_words(self):
        # «в меморандуме» is an entry of the ru pack and is flagged; «меморандумный» is a
        # different word and, with word edges on both sides, is not.
        def lint_ru(sentence: str) -> list[dict]:
            return brief_lint.lint_brief(
                make_brief(RussianTest().ru_blocks(), language="ru", main_extra=sentence), MEMO_RU, language="ru"
            )

        self.assertEqual(["B-09"], rules(lint_ru("Подробности изложены в меморандуме.")))
        self.assertEqual([], lint_ru("Меморандумный порядок согласования здесь не нужен."))

    def test_b09_russian_inflected_forms(self):
        # Fix round 1 (addition): with whole words the ru pack lists every case form of «меморандум».
        def lint_ru(sentence: str) -> list[dict]:
            return brief_lint.lint_brief(
                make_brief(RussianTest().ru_blocks(), language="ru", main_extra=sentence), MEMO_RU, language="ru"
            )

        for sentence in ("Это следует из меморандума.", "Вывод подтверждён меморандумом."):
            with self.subTest(sentence=sentence):
                self.assertEqual(["B-09"], rules(lint_ru(sentence)))

    def test_b09_plural_forms_in_every_pack(self):
        expected = {
            "en": ("memos", "memoranda"),
            "de": ("memorandums", "memos"),
            "fr": ("mémos", "mémorandums"),
            "es": ("memorandos",),
            "ru": ("меморандумы", "меморандумов"),
        }
        for code, forms in expected.items():
            with self.subTest(code=code):
                listed = i18n.node(code, "memo.brief.self_reference")
                for form in forms:
                    self.assertIn(form, listed)

    def test_b10_placeholder_is_a_blocker(self):
        findings = self.lint(make_brief(FLAT_BLOCKS, main_extra="TODO add the deadline."))
        self.assertEqual(["B-10"], rules(findings))
        self.assertEqual("blocker", findings[0]["severity"])

    def test_b10_ai_tell_is_major(self):
        tell = lint.ai_tells()[0]
        findings = self.lint(make_brief(FLAT_BLOCKS, main_extra=f"This is {tell} careful planning."))
        self.assertEqual(["B-10"], rules(findings))
        self.assertEqual("major", findings[0]["severity"])


class ReportTest(unittest.TestCase):
    def test_build_report_of_a_b_finding_validates(self):
        findings = brief_lint.lint_brief(
            make_brief(FLAT_BLOCKS, main_extra="TODO add the deadline."), MEMO_FLAT, language="en"
        )
        report = brief_lint.build_report(SHA, findings)
        self.assertFalse(report["clean"])
        self.assertEqual([], schema.validate(report, "lint"))

    def test_an_unknown_rule_family_still_fails(self):
        row = {"rule": "X-01", "severity": "major", "line": None, "section_id": "document", "excerpt": "", "hint": "x"}
        self.assertTrue(schema.validate({"draft_sha": SHA, "clean": True, "findings": [row]}, "lint"))
        with self.assertRaises(schema.SchemaValidationError):
            brief_lint.build_report(SHA, [row])

    def test_severity_table(self):
        self.assertEqual(
            {
                "B-01": "major", "B-02": "major", "B-03": "major", "B-04": "major", "B-05": "blocker",
                "B-06": "blocker", "B-07": "major", "B-08": "major", "B-09": "major", "B-10": "major",
                "B-11": "major", "B-12": "major",  # D-229
            },
            brief_lint.SEVERITY,
        )

    def test_the_b04_name_says_covered_or_omitted(self):
        self.assertEqual("a section is neither covered nor listed as omitted", i18n.t("en", "memo.brief.checks.B-04"))


if __name__ == "__main__":
    unittest.main()
