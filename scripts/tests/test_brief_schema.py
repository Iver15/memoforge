"""The fidelity review of the decision brief and the two brief checklists (plan 75A, D-223)."""

from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import brief_lint, review, schema  # noqa: E402

AGENTS = PLUGIN_ROOT / "agents"
CHECKLISTS = PLUGIN_ROOT / "lib" / "checklists"
SCHEMA_FILE = PLUGIN_ROOT / "schemas" / "brief-review.schema.json"

JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)

ROW_KEYS = {"id", "text", "tier", "hard_fail", "severity_floor", "severity_max"}

# Spec DB-05: the severity of every row, as `(severity_floor, severity_max)`.
FIDELITY = {
    "BF-01": ("blocker", "blocker"),
    "BF-02": ("blocker", "blocker"),
    "BF-03": ("blocker", "blocker"),
    "BF-04": ("blocker", "blocker"),
    "BF-05": ("major", "blocker"),
    "BF-06": ("major", "major"),
    "BF-07": ("major", "major"),
    "BF-08": ("major", "major"),
    "BF-09": ("major", "major"),
}
FORM = {
    **{f"BC-0{n}": ("major", "major") for n in range(1, 6)},
    **{f"BC-0{n}": ("minor", "minor") for n in range(6, 9)},
}


def checklist(name: str) -> list:
    return json.loads((CHECKLISTS / f"{name}.json").read_text(encoding="utf-8-sig"))


def example() -> dict:
    text = (AGENTS / "brief-fidelity-reviewer.md").read_text(encoding="utf-8-sig")
    return json.loads(JSON_BLOCK.findall(text)[0])


def iter_refs(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


class BriefReviewSchemaTest(unittest.TestCase):
    """`schemas/brief-review.schema.json` — the document the fidelity reviewer writes."""

    def test_the_schema_is_draft_2020_12_with_local_refs_only(self):
        from jsonschema import Draft202012Validator

        document = json.loads(SCHEMA_FILE.read_text(encoding="utf-8-sig"))
        Draft202012Validator.check_schema(document)
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", document["$schema"])
        self.assertTrue(document["$id"].endswith("/brief-review.schema.json"))
        defs = document.get("$defs", {})
        used = set()
        for ref in iter_refs(document):
            self.assertTrue(ref.startswith("#/$defs/"), ref)
            used.add(ref[len("#/$defs/"):])
        self.assertEqual(set(defs), used, "every $def is used and every $ref resolves")
        self.assertIs(False, document["additionalProperties"])

    def test_the_block_enum_is_the_brief_vocabulary(self):
        document = json.loads(SCHEMA_FILE.read_text(encoding="utf-8-sig"))
        block = document["$defs"]["issue"]["properties"]["block"]
        self.assertEqual([*brief_lint.BLOCK_IDS, brief_lint.DOCUMENT_ID], block["enum"])

    def test_the_agent_example_validates(self):
        document = example()
        self.assertEqual([], schema.validate(document, "brief-review"))
        self.assertEqual("reasoning", next(iter(document)))
        self.assertEqual("brief_fidelity", document["reviewer"])
        known = [row["id"] for row in checklist("brief-fidelity")]
        graded = [row["id"] for row in document["checklist"]]
        # Task 6 rejects a review that does not grade every id exactly once; the example is what agents imitate.
        self.assertEqual(sorted(known), sorted(graded))
        self.assertEqual(len(graded), len(set(graded)))
        self.assertEqual(set(), {row["checklist_id"] for row in document["issues"]} - set(known))
        failed = {row["id"] for row in document["checklist"] if row["pass"] is not True}
        self.assertEqual(failed, {row["checklist_id"] for row in document["issues"]})

    def test_an_approved_review_without_issues_validates(self):
        document = example()
        document["verdict"] = "approved"
        document["issues"] = []
        document["checklist"] = [{"id": "BF-01", "pass": True, "evidence": "Every sentence is in the memo."}]
        self.assertEqual([], schema.validate(document, "brief-review"))

    def test_a_pass_outside_true_false_unknown_fails(self):
        document = example()
        document["checklist"][0]["pass"] = "maybe"
        self.assertNotEqual([], schema.validate(document, "brief-review"))

    def test_an_issue_without_brief_quote_fails(self):
        document = example()
        self.assertTrue(document["issues"], "the example raises at least one issue")
        del document["issues"][0]["brief_quote"]
        self.assertNotEqual([], schema.validate(document, "brief-review"))

    def test_the_closed_shapes_reject_what_they_do_not_name(self):
        base = example()
        cases = {
            "extra top-level key": lambda d: d.update({"iteration": 1}),
            "extra checklist key": lambda d: d["checklist"][0].update({"severity": "major"}),
            "extra issue key": lambda d: d["issues"][0].update({"lens": "clarity"}),
            "reviewer of the main loop": lambda d: d.update({"reviewer": "logic"}),
            "short draft_sha": lambda d: d.update({"draft_sha": "abc"}),
            "unknown verdict": lambda d: d.update({"verdict": "rejected"}),
            "empty checklist": lambda d: d.update({"checklist": []}),
            "block outside the vocabulary": lambda d: d["issues"][0].update({"block": "s-4-1"}),
            "eighth conclusion block": lambda d: d["issues"][0].update({"block": "s-b8"}),  # D-228: up to s-b7
            "issue id of another shape": lambda d: d["issues"][0].update({"id": "om-1"}),
            "unknown severity": lambda d: d["issues"][0].update({"severity": "critical"}),
            "issue without checklist_id": lambda d: d["issues"][0].pop("checklist_id"),
            "issue without memo_quote": lambda d: d["issues"][0].pop("memo_quote"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                document = copy.deepcopy(base)
                mutate(document)
                self.assertNotEqual([], schema.validate(document, "brief-review"))


class BriefChecklistTest(unittest.TestCase):
    """`lib/checklists/brief-fidelity.json` and `brief-form.json` — rows Task 6 reads the bounds of."""

    def test_both_checklists_are_lists_of_six_key_rows(self):
        for name, tier in (("brief-fidelity", "substance"), ("brief-form", "form")):
            rows = checklist(name)
            self.assertIsInstance(rows, list, name)
            for row in rows:
                with self.subTest(checklist=name, row=row.get("id")):
                    self.assertEqual(ROW_KEYS, set(row))
                    self.assertEqual(tier, row["tier"])
                    self.assertIs(False, row["hard_fail"])
                    self.assertTrue(row["text"].strip())
                    self.assertIn("Evidence:", row["text"])
                    self.assertNotIn("\n", row["text"])
                    self.assertIn(row["severity_floor"], review.SEVERITY_RANK)
                    self.assertIn(row["severity_max"], review.SEVERITY_RANK)
                    # Rank 0 is blocker, the most severe: the floor is never above the ceiling.
                    self.assertGreaterEqual(
                        review.SEVERITY_RANK[row["severity_floor"]], review.SEVERITY_RANK[row["severity_max"]]
                    )

    def test_the_ids_and_severities_are_those_of_the_spec(self):
        for name, expected in (("brief-fidelity", FIDELITY), ("brief-form", FORM)):
            rows = checklist(name)
            with self.subTest(checklist=name):
                self.assertEqual(list(expected), [row["id"] for row in rows])
                self.assertEqual(
                    expected, {row["id"]: (row["severity_floor"], row["severity_max"]) for row in rows}
                )

    def test_bf_05_ranges_from_major_to_blocker(self):
        row = next(row for row in checklist("brief-fidelity") if row["id"] == "BF-05")
        self.assertEqual(("major", "blocker"), (row["severity_floor"], row["severity_max"]))
        self.assertEqual(1, review.SEVERITY_RANK[row["severity_floor"]])
        self.assertEqual(0, review.SEVERITY_RANK[row["severity_max"]])
        self.assertGreaterEqual(review.SEVERITY_RANK[row["severity_floor"]], review.SEVERITY_RANK[row["severity_max"]])

    def test_bc_07_accepts_the_mandated_not_confirmed_sentence(self):
        """D-227 (F8-5): the fixed `memo.brief.unconfirmed` sentence repeated in two blocks is no repetition."""
        row = next(row for row in checklist("brief-form") if row["id"] == "BC-07")
        self.assertTrue(row["text"].endswith(" " + UNCONFIRMED_NOTE), row["text"])


TOKEN_RULE = (
    "A citation token follows the complete statement it supports, at the end of that clause or sentence; "
    "it never replaces a word or finishes a sentence for you."
)
UNCONFIRMED_NOTE = "The fixed 'not confirmed' sentence that marks an open point is required wording, not repetition."
WRITER_FILES = (
    AGENTS / "brief-writer.md",
    PLUGIN_ROOT / "scripts" / "memoforge" / "prompts" / "brief-writer.md",
    PLUGIN_ROOT / "templates" / "decision-brief.md",
    PLUGIN_ROOT / "skills" / "brief" / "SKILL.md",
)


def flat(path: Path) -> str:
    """The file's text with every run of whitespace as one space (the prompt template wraps its lines)."""
    return " ".join(path.read_text(encoding="utf-8-sig").split())


class BriefWriterWordingTest(unittest.TestCase):
    """D-227: what the writer reads after the acceptance replay (F8-2, F8-4)."""

    def test_a_citation_token_follows_its_statement(self):
        for path in WRITER_FILES[:2]:
            with self.subTest(file=path.name, folder=path.parent.name):
                self.assertIn(TOKEN_RULE, flat(path))

    def test_the_brief_is_about_three_pages(self):
        for path in WRITER_FILES:
            with self.subTest(file=path.name, folder=path.parent.name):
                text = flat(path).lower()
                self.assertNotIn("two pages", text)
                self.assertIn("about three pages", text)


FIDELITY_FILES = (
    AGENTS / "brief-fidelity-reviewer.md",
    PLUGIN_ROOT / "scripts" / "memoforge" / "prompts" / "brief-fidelity-reviewer.md",
)
QUALIFIERS = ('"probably"', '"should"', '"only if"', '"unless"', '"subject to"', '"may"', '"at the earliest"')


class BriefConvergenceWordingTest(unittest.TestCase):
    """D-228 (plan 75A.1, task 2): the writer traces, the fidelity review converges.

    In the 75A acceptance the writers upgraded "should" to "must", dropped conditions and put the wrong
    deadline on an action while compressing, and every fidelity round re-sampled the whole brief.
    """

    def test_the_writer_traces_every_sentence_and_keeps_its_qualifiers(self):
        for path in WRITER_FILES[:2]:
            text = flat(path)
            for needle in (
                *QUALIFIERS,
                "compresses one identified passage",
                "keep that passage in view",
                'never upgraded ("should" to "must") or dropped',
                "deadline for that exact step",
                "never a qualifier",
                "reread each block against the",
                "for these qualifiers",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, needle=needle):
                    self.assertIn(needle, text)

    def test_a_block_joins_only_leaves_of_one_verdict_and_keeps_their_conclusions_apart(self):
        """Fix round 2: the replay of a 12-leaf UK memo — "same conclusion and verdict" left 5 leaves unbound."""
        for path in WRITER_FILES[:3]:
            text = flat(path)
            for needle in (
                # D-229: blocks are counted over the kept leaves; the omitted ones are listed, not bound.
                "one block per kept leaf while the brief keeps at most seven leaves",
                "group the kept leaves into at most seven blocks",
                "only when they carry the same risk verdict",
                "Leaves with different conclusions may share a block",
                "each conclusion is then stated in its own sentence with its own condition",
                "never blended into one statement",
                "one or two sentences per leaf conclusion",
                "Every kept leaf is bound by a block",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, needle=needle):
                    self.assertIn(needle, text.replace("One block per kept leaf", "one block per kept leaf"))
            for retired in ("share both the conclusion", "states one conclusion", "five blocks", "highest verdict"):
                with self.subTest(file=path.name, folder=path.parent.name, retired=retired):
                    self.assertNotIn(retired, text)

    def test_the_fidelity_review_traces_round_0_in_full_and_converges_after(self):
        for path in FIDELITY_FILES:
            text = flat(path)
            for needle in (
                "`previous_review_path`",
                "`changed_blocks`",
                "full trace",
                "leaf by leaf",
                "whether it is fixed",
                "same severity",
                "Re-trace in full only the blocks named in `changed_blocks`",
                "`none` means no block is re-traced in full, while (a) and (c) still apply",
                "from the current brief where there is text to quote",
                "only for a `blocker` you can quote from both texts",
                "grade all nine items",
                "keeps its previous grade",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, needle=needle):
                    self.assertIn(needle, text)
            with self.subTest(file=path.name, folder=path.parent.name, retired="contradictions"):
                self.assertNotIn("only the previous issues are checked", text)
                self.assertNotIn("quoted from the current brief.", text)

    def test_the_fidelity_prompt_prints_the_scope_it_was_given(self):
        text = flat(FIDELITY_FILES[1])
        self.assertIn("${previous_review_path}", text)
        self.assertIn("${changed_blocks}", text)


BF_04_TEXT = (
    "Every open point of the memorandum that the brief touches is shown as not confirmed, and no conclusion of the "
    "brief rests on one. Evidence: name the open point by its id in the open issues and quote how the brief "
    "presents it."
)
KEEP_CRITERIA = (
    "A leaf is kept when any of these holds: its verdict is `high`; its executive-summary bullet names an amount or "
    "a sanction; it answers an explicit sub-question of the user's question (the main question's parts included); "
    "its verdict is `undetermined`, or an open issue touches it and a kept conclusion depends on it. Every other "
    "leaf is omitted. A date or a deadline alone does not keep a leaf: deadlines travel with the actions."
)
"""D-229 fix round 2: judged on the executive-summary bullet — in replay 4 every risk paragraph ended with a dated
recommendation, so "risk line names a date" kept almost every leaf."""


PARTITION_FIX = (
    "Partition fix: when an issue concerns the partition — BF-05, or B-03, B-04 or B-05 on an omitted leaf — you "
    "may change together"
)


class BriefOmissionWordingTest(unittest.TestCase):
    """D-229 (plan 75A.1, task 4): a decision brief by omission, not compression.

    Replay 3 converged on fidelity, but faithful briefs of dense memos ran 2,300–2,700 words because every
    leaf, action and assumption was kept.
    """

    def row(self, identifier: str) -> str:
        return next(row["text"] for row in checklist("brief-fidelity") if row["id"] == identifier)

    def test_the_fidelity_checklist_checks_the_partition_and_its_closure(self):
        needles = {
            "BF-03": ("clause by clause", "a ceiling written as a range", "written as a choice"),
            "BF-05": ("partition", "keep criterion", "every high-risk leaf is kept",
                      "the last part, «Other points assessed», names every omitted leaf in one item",
                      "verdict as the memo states it", "executive-summary bullet names an amount or a sanction",
                      "a date or a deadline alone keeps no leaf"),
            "BF-07": ("every kept leaf's recommendation", "owner, its step and its deadline or trigger",
                      "owner and deadline or trigger are identical", "within 14 days of the memo date",
                      "a 'must' stays an action", "other actions of omitted leaves may be absent"),
            "BF-08": ("Closure", "a kept conclusion depends", "beside that conclusion", "one short paragraph",
                      "may be absent"),
            "BF-09": ("every explicit sub-question", "answered in one sentence"),
        }
        for identifier, words in needles.items():
            for needle in words:
                with self.subTest(item=identifier, needle=needle):
                    self.assertIn(needle, self.row(identifier))

    def test_bf_07_does_not_judge_the_length_of_an_action(self):
        """Final review (D-229 addendum): action length is lint's B-12, never a fidelity finding that decides the
        verdict — BF-07 names no word count, and neither does the fidelity reviewer's text."""
        self.assertNotRegex(self.row("BF-07"), r"\d+\s+words|\bwords?\b|at most \d+")
        for path in FIDELITY_FILES:
            with self.subTest(file=path.name, folder=path.parent.name):
                self.assertNotRegex(flat(path), r"\b30 words\b")

    def test_bf_04_and_its_fixed_wording_are_unchanged(self):
        self.assertEqual(BF_04_TEXT, self.row("BF-04"))

    def test_the_writer_keeps_leaves_by_the_criteria_and_omits_the_rest(self):
        for path in WRITER_FILES[:3]:
            text = flat(path)
            for needle in (
                KEEP_CRITERIA,
                "<!-- omitted §s-8 §s-10-1 -->",
                "Other points assessed",
                "never translated into a likelihood",
                "never compress a kept sentence",
                "actions of omitted leaves",
                "owner and deadline or trigger are identical",
                "no kept conclusion depends on",
                "explicit sub-question",
                "presented as a choice only when",
                # D-229 fix round 2 (replay 4): deadlines travel with the actions, rewrite, a short paragraph.
                "within 14 days of the memo date",
                "at most 30 words",
                "Rewrite, don't copy",
                "copying memo sentences verbatim is not the method",
                "one short paragraph, no list, of at most 60 words",
                "beside that conclusion in its block",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, needle=needle):
                    self.assertIn(needle, text)
            for retired in (
                "Every leaf is bound by a block",
                "and action stays",
                "open point and action",
                "Only actions the memo recommends.",
                "names an amount, a sanction, a date or a deadline",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, retired=retired):
                    self.assertNotIn(retired, text)

    def test_a_revise_may_fix_the_partition_across_its_parts(self):
        """Fix round 1: a wrongly omitted leaf is fixed in several parts at once, not only in the named block."""
        for path in WRITER_FILES[:2]:
            text = flat(path)
            for needle in (
                PARTITION_FIX,
                "the omitted comment, the «Other points assessed» items, the conclusion block of that leaf (added or "
                "removed), its actions, its assumptions and the bottom line",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, needle=needle):
                    self.assertIn(needle, text)

    def test_the_writer_shortens_by_omission(self):
        for path in WRITER_FILES[:2]:
            text = flat(path)
            with self.subTest(file=path.name, folder=path.parent.name):
                self.assertIn("move further leaves that meet no keep criterion to the omitted list", text)

    def test_shortening_keeps_an_action_due_within_14_days(self):
        """Fix 3 (replay 5): an omitted leaf takes only the actions the actions rule does not keep."""
        for path in WRITER_FILES[:3]:
            text = flat(path)
            with self.subTest(file=path.name, folder=path.parent.name):
                self.assertIn("those of their actions the actions rule does not keep", text)
                self.assertIn("an action due within 14 days of the memo date stays", text)
                self.assertNotIn("drop their actions and assumptions", text)
                self.assertNotIn("its actions and assumptions go", text)

    def test_the_reviewer_example_keeps_a_leaf_by_its_summary_bullet(self):
        """Fix round 3: the example review keeps a leaf by its executive-summary bullet, never by its risk line."""
        document = example()
        row = next(item for item in document["checklist"] if item["id"] == "BF-05")
        self.assertIn("executive-summary bullet names a fine", row["evidence"])
        [issue] = [item for item in document["issues"] if item["checklist_id"] == "BF-05"]
        self.assertIn("executive-summary bullet", issue["issue"])
        self.assertNotIn("Risk:", issue["memo_quote"].split(".")[0])
        self.assertIn("executive-summary bullet", document["reasoning"])

    def test_the_fidelity_review_sweeps_the_summary_the_recommendations_and_the_omitted_list(self):
        for path in FIDELITY_FILES:
            text = flat(path)
            for needle in (
                "executive-summary bullets",
                "recommendations",
                "the omitted list",
                "whole bound leaf",
                "clause by clause",
                "keep criterion",
                "never translated into a likelihood",
                "executive-summary bullet names an amount or a sanction",
                "A date or a deadline alone does not keep a leaf",
                "within 14 days of the memo date",
            ):
                with self.subTest(file=path.name, folder=path.parent.name, needle=needle):
                    self.assertIn(needle, text)
            for retired in ("names an amount, a sanction, a date or a deadline", "risk line names",
                            "risk line name"):
                with self.subTest(file=path.name, folder=path.parent.name, retired=retired):
                    self.assertNotIn(retired, text)


if __name__ == "__main__":
    unittest.main()
