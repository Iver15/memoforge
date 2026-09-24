"""Validation, fail-closed issues and the merge of the two brief reviews (plan 75A, D-225)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _brief import CLEAN_BRIEF, approving_fidelity, approving_form  # noqa: E402
from memoforge import brief_lint, brief_review, i18n  # noqa: E402

SHA = "a" * 64
PARSED = brief_lint.parse_brief(CLEAN_BRIEF, "en")
BLOCKS = brief_lint.block_ids(PARSED)
"""The one-block fixture brief: `s-header`, `s-main`, `s-b1`, `s-actions`."""


def form_issue(checklist_id: str | None = "BC-05", section_id: str = "s-actions", severity: str = "major") -> dict:
    row = {
        "severity": severity,
        "category": "clarity",
        "section_id": section_id,
        "issue": f"Fixture form issue on {checklist_id}.",
        "suggestion": "Fix it.",
        "lens": "clarity",
    }
    if checklist_id is not None:
        row["checklist_id"] = checklist_id
    return row


def fidelity_issue(checklist_id: str = "BF-02", block: str = "s-b1", severity: str = "blocker",
                   number: int = 1, quote: str = "Records may not be kept beyond their purpose") -> dict:
    return {
        "id": f"bf-{number}",
        "checklist_id": checklist_id,
        "severity": severity,
        "block": block,
        "issue": f"Fixture fidelity issue {number} on {checklist_id}.",
        "brief_quote": quote,
        "memo_quote": "The memo's words.",
        "suggestion": "Restore the condition.",
    }


def graded(document: dict, **passes) -> dict:
    """A copy of `document` with the named items graded (`BC_05=False` grades BC-05)."""
    document = copy.deepcopy(document)
    for key, value in passes.items():
        identifier = key.replace("_", "-")
        for item in document["checklist"]:
            if item["id"] == identifier:
                item["pass"] = value
    return document


def form(**passes) -> dict:
    return graded(approving_form(SHA), **passes)


def fidelity(**passes) -> dict:
    return graded(approving_fidelity(SHA), **passes)


def validate(kind: str, document: dict, draft_sha: str = SHA) -> dict:
    return brief_review.validate_review(kind, document, draft_sha=draft_sha, block_ids=BLOCKS)


class ChecklistTest(unittest.TestCase):
    def test_both_brief_checklists_load(self):
        self.assertEqual("BF-01", brief_review.load_checklist("brief-fidelity")[0]["id"])
        self.assertEqual(8, len(brief_review.load_checklist("brief-form")))
        with self.assertRaises(ValueError):
            brief_review.load_checklist("logic")

    def test_the_shorten_instruction_shortens_by_omission(self):
        """D-229: shortening moves leaves that meet no keep criterion to the omitted list; it never compresses.

        Fix 3 (replay 5): an omitted leaf's action due within 14 days of the memo date stays (the actions rule).
        """
        self.assertEqual(
            "shorten to about {soft_cap} words (about three pages) by omission: move further leaves that meet no "
            "keep criterion to the omitted list and drop their assumptions and those of their actions the actions "
            "rule does not keep — an action due within 14 days of the memo date stays; never compress a kept "
            "sentence or drop a qualifier, condition, verdict or open point",
            brief_review.SHORTEN_INSTRUCTION,
        )
        self.assertIn("within 14 days", brief_review.SHORTEN_INSTRUCTION)
        self.assertNotIn("drop their actions and assumptions", brief_review.SHORTEN_INSTRUCTION)

    def test_the_bf07_banner_label_names_both_directions(self):
        """D-229 fix 3 (replay 5): BF-07 also fails on a missing action, so its label is not one-sided."""
        self.assertEqual("the actions differ from the memorandum's recommendations",
                         i18n.t("en", "memo.brief.checks.BF-07"))
        expected = {
            "ru": "действия расходятся с рекомендациями меморандума",
            "de": "die Maßnahmen weichen von den Empfehlungen des Memorandums ab",
            "fr": "les actions s'écartent des recommandations du mémorandum",
            "es": "las actuaciones no coinciden con las recomendaciones del memorando",
        }
        retired = ("does not recommend", "не рекомендует", "nicht empfiehlt", "ne recommande pas", "no recomienda")
        for language in ("en", *expected):
            label = i18n.t(language, "memo.brief.checks.BF-07")
            with self.subTest(language=language):
                if language in expected:
                    self.assertEqual(expected[language], label)
                for old in retired:
                    self.assertNotIn(old, label)

    def test_the_length_rules_never_decide_the_verdict(self):
        """D-229 fix round 2: the word budgets (B-12) are length findings, like B-01."""
        self.assertEqual(("B-01", "B-12"), brief_review.LENGTH_RULES)

    def test_check_names_are_human_and_never_raise(self):
        self.assertEqual(i18n.t("en", "memo.brief.checks.writer_failed"),
                         brief_review.check_name("en", "writer_failed"))
        self.assertEqual("X-99", brief_review.check_name("en", "X-99"))


class ValidateTest(unittest.TestCase):
    def assert_error(self, result: dict, error: str) -> None:
        self.assertFalse(result["valid"], result)
        self.assertIn(error, result["errors"])

    def test_approving_reviews_are_valid(self):
        self.assertEqual({"valid": True, "errors": []}, validate("form", form()))
        self.assertEqual({"valid": True, "errors": []}, validate("fidelity", fidelity()))

    def test_a_stale_draft_sha(self):
        self.assert_error(validate("form", form(), draft_sha="b" * 64), "stale_draft_sha")
        self.assert_error(validate("fidelity", fidelity(), draft_sha="b" * 64), "stale_draft_sha")

    def test_a_missing_checklist_item(self):
        document = form()
        document["checklist"] = [item for item in document["checklist"] if item["id"] != "BC-03"]
        self.assert_error(validate("form", document), "missing_checklist_id: BC-03")

    def test_a_duplicate_checklist_item(self):
        document = fidelity()
        document["checklist"].append(dict(document["checklist"][0]))
        self.assert_error(validate("fidelity", document), "duplicate_checklist_id: BF-01")

    def test_an_unknown_checklist_item(self):
        document = form()
        document["checklist"].append({"id": "C-01", "pass": True, "evidence": "Fixture."})
        self.assert_error(validate("form", document), "unknown_checklist_id: C-01")

    def test_a_failed_major_item_without_an_issue(self):
        self.assert_error(validate("form", form(BC_05=False)), "missing_issue_for_failed_item: BC-05")

    def test_an_unknown_major_item_without_an_issue(self):
        self.assert_error(validate("form", form(BC_01="unknown")), "missing_issue_for_failed_item: BC-01")

    def test_a_failed_minor_item_needs_no_issue(self):
        self.assertTrue(validate("form", form(BC_07=False))["valid"])

    def test_a_form_issue_without_a_checklist_id(self):
        document = form(BC_05=False)
        document["issues"] = [form_issue("BC-05"), form_issue(None)]
        result = validate("form", document)
        self.assertFalse(result["valid"])
        self.assertTrue(any(error.startswith("issue_without_failed_item") for error in result["errors"]), result)

    def test_an_issue_on_an_item_that_passed(self):
        document = fidelity()
        document["issues"] = [fidelity_issue("BF-02")]
        result = validate("fidelity", document)
        self.assertTrue(any(error.startswith("issue_without_failed_item") for error in result["errors"]), result)

    def test_a_memo_section_id_is_an_unknown_block(self):
        document = form(BC_05=False)
        document["issues"] = [form_issue("BC-05", section_id="s-2")]
        self.assert_error(validate("form", document), "unknown_block: s-2")

    def test_a_block_this_brief_does_not_have(self):
        document = form(BC_05=False)
        document["issues"] = [form_issue("BC-05", section_id="s-b5")]
        self.assert_error(validate("form", document), "unknown_block: s-b5")
        document = fidelity(BF_02=False)
        document["issues"] = [fidelity_issue("BF-02", block="s-b5")]
        self.assert_error(validate("fidelity", document), "unknown_block: s-b5")

    def test_the_seventh_block_is_a_valid_block(self):
        """D-228: up to seven conclusion blocks, and both review schemas know them."""
        blocks = BLOCKS + ["s-b6", "s-b7"]
        document = fidelity(BF_02=False)
        document["issues"] = [fidelity_issue("BF-02", block="s-b7")]
        self.assertEqual({"valid": True, "errors": []},
                         brief_review.validate_review("fidelity", document, draft_sha=SHA, block_ids=blocks))
        document = form(BC_05=False)
        document["issues"] = [form_issue("BC-05", section_id="s-b6")]
        self.assertEqual({"valid": True, "errors": []},
                         brief_review.validate_review("form", document, draft_sha=SHA, block_ids=blocks))

    def test_document_is_a_valid_block(self):
        document = form(BC_05=False)
        document["issues"] = [form_issue("BC-05", section_id="document")]
        self.assertEqual({"valid": True, "errors": []}, validate("form", document))

    def test_a_review_of_another_reviewer_is_not_a_form_review(self):
        document = form()
        document["reviewer"] = "logic"
        self.assertFalse(validate("form", document)["valid"])

    def test_a_schema_failure_is_reported_as_such(self):
        document = fidelity()
        document["checklist"][0]["pass"] = "maybe"
        result = validate("fidelity", document)
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"])


class EffectiveIssuesTest(unittest.TestCase):
    def test_an_omitted_item_becomes_a_synthetic_issue_at_its_floor(self):
        document = form()
        document["checklist"] = [item for item in document["checklist"] if item["id"] != "BC-04"]
        issues = brief_review.effective_issues("form", document)
        self.assertEqual(
            [{"source": "form", "checklist_id": "BC-04", "severity": "major", "block": "document",
              "issue": i18n.t("en", "memo.brief.checks.BC-04"), "suggestion": "", "quote": "",
              "synthetic": True}],
            issues,
        )

    def test_an_unknown_item_without_an_issue_is_synthetic(self):
        [issue] = brief_review.effective_issues("form", form(BC_02="unknown"))
        self.assertEqual(("BC-02", "major", True), (issue["checklist_id"], issue["severity"], issue["synthetic"]))

    def test_a_failed_minor_item_is_a_synthetic_minor(self):
        [issue] = brief_review.effective_issues("form", form(BC_07=False))
        self.assertEqual(("BC-07", "minor"), (issue["checklist_id"], issue["severity"]))

    def test_a_real_issue_is_raised_to_the_floor(self):
        document = form(BC_05=False)
        document["issues"] = [form_issue("BC-05", severity="minor")]
        [issue] = brief_review.effective_issues("form", document)
        self.assertEqual("major", issue["severity"])
        self.assertFalse(issue["synthetic"])
        self.assertEqual("s-actions", issue["block"])

    def test_a_real_issue_is_capped_at_the_max(self):
        document = fidelity(BF_05=False, BF_06=False)
        document["issues"] = [fidelity_issue("BF-05", severity="blocker", number=1),
                              fidelity_issue("BF-06", severity="blocker", number=2)]
        issues = {row["checklist_id"]: row for row in brief_review.effective_issues("fidelity", document)}
        self.assertEqual("blocker", issues["BF-05"]["severity"])
        self.assertEqual("major", issues["BF-06"]["severity"])
        self.assertEqual("Records may not be kept beyond their purpose", issues["BF-05"]["quote"])

    def test_a_missing_review_counts_every_item_as_missing(self):
        issues = brief_review.effective_issues("form", None)
        rows = brief_review.load_checklist("brief-form")
        self.assertEqual([row["id"] for row in rows], [issue["checklist_id"] for issue in issues])
        self.assertTrue(all(issue["synthetic"] for issue in issues))
        self.assertEqual([row["severity_floor"] for row in rows], [issue["severity"] for issue in issues])

    def test_a_broken_document_is_read_defensively(self):
        issues = brief_review.effective_issues("fidelity", {"checklist": ["x", {"id": "BF-01", "pass": True}],
                                                            "issues": "none"})
        self.assertEqual(8, len(issues))
        self.assertNotIn("BF-01", [issue["checklist_id"] for issue in issues])

    def test_the_synthetic_text_follows_the_brief_language(self):
        [issue] = brief_review.effective_issues("form", form(BC_02="unknown"), language="ru")
        self.assertEqual(i18n.t("ru", "memo.brief.checks.BC-02"), issue["issue"])


class MergeTest(unittest.TestCase):
    def test_two_different_issues_of_one_item_and_block_are_kept(self):
        document = fidelity(BF_02=False)
        document["issues"] = [fidelity_issue("BF-02", number=1, quote="first"),
                              fidelity_issue("BF-02", number=2, quote="second")]
        issues = brief_review.effective_issues("fidelity", document)
        merged = brief_review.merge([], issues, [])
        self.assertEqual(2, len(merged))

    def test_exact_duplicates_are_dropped(self):
        issue = brief_review.effective_issues("form", form(BC_02="unknown"))
        self.assertEqual(1, len(brief_review.merge([], [], issue + copy.deepcopy(issue))))

    def test_order_is_severity_then_block(self):
        lint_findings = [
            {"rule": "B-09", "severity": "major", "line": 3, "section_id": "s-main", "excerpt": "memo", "hint": "H."},
            {"rule": "B-01", "severity": "major", "line": None, "section_id": "document", "excerpt": "",
             "hint": "Long."},
        ]
        document = fidelity(BF_02=False)
        document["issues"] = [fidelity_issue("BF-02", block="s-b1")]
        merged = brief_review.merge(
            lint_findings, brief_review.effective_issues("fidelity", document),
            brief_review.effective_issues("form", form(BC_07=False)),
        )
        self.assertEqual(
            [("BF-02", "blocker", "s-b1"), ("B-09", "major", "s-main"), ("B-01", "major", "document"),
             ("BC-07", "minor", "document")],
            [(row["checklist_id"], row["severity"], row["block"]) for row in merged],
        )
        self.assertEqual("lint", merged[1]["source"])

    def test_instructions_group_by_block_then_minor(self):
        document = fidelity(BF_02=False)
        document["issues"] = [fidelity_issue("BF-02", block="s-b1")]
        merged = brief_review.merge(
            [], brief_review.effective_issues("fidelity", document),
            brief_review.effective_issues("form", form(BC_07=False)),
        )
        text = brief_review.instructions_markdown(merged, brief_review.block_headings(PARSED))
        self.assertIn("## Retention period (s-b1)\n\n- [blocker] Fixture fidelity issue 1 on BF-02. — "
                      "Restore the condition.\n", text)
        self.assertIn("## Minor (if easy)\n\n- [minor] ", text)
        self.assertLess(text.index("## Retention period"), text.index("## Minor (if easy)"))


if __name__ == "__main__":
    unittest.main()
