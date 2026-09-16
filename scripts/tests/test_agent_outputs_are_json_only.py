"""M4 over the agent layer: JSON outputs only, no markdown view written beside them (ТЗ §6, §4.2)."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import dispatch, schema  # noqa: E402

AGENTS = PLUGIN_ROOT / "agents"
PROMPTS = PLUGIN_ROOT / "scripts" / "memoforge" / "prompts"
CHECKLISTS = PLUGIN_ROOT / "lib" / "checklists"

# §6: the two authored markdown artefacts of the pipeline — primary texts, not views of JSON.
MD_AUTHORS = {"memo-writer", "style-extractor"}
# §11: the P9 diagnostic writes no file at all.
NO_OUTPUT = {"probe-echo"}

# §6 table: the schema each agent's worked example is written against.
EXAMPLE_SCHEMA = {
    "fact-assumption-analyst": "intake-questions",
    "legal-researcher": "research-findings",
    "research-sufficiency-reviewer": "research-sufficiency",
    "currency-checker": "currency",
    "logic-reviewer": "review",
    "form-reviewer": "review",
    "citation-auditor": "review",
    "counterargument-reviewer": "review",
    "revision-mediator": "mediator",
    "client-readiness-reviewer": "client-readiness",
}

# §4.5: the checklist file each grader works from.
EXAMPLE_CHECKLIST = {
    "logic-reviewer": "logic",
    "form-reviewer": "form",
    "citation-auditor": "citations",
    "counterargument-reviewer": "counterarguments",
    "client-readiness-reviewer": "client-readiness",
}

# "write research/statutes.md", "produce a human-readable currency-report.md", …
MD_WRITE = re.compile(
    r"(?i)\b(writes?|writing|produces?|emits?|creates?|append(?:s|ing)?(?: to)?)\b[^\n]{0,100}?\.md\b"
)

# v1 phrasings that asked for a second, markdown-shaped copy of the same content.
MD_VIEW_PHRASES = (
    "markdown view",
    "md view",
    "human-readable view",
    "machine-readable view",
    "two parallel files",
    "same content in different shapes",
    "in sync with the markdown",
)

JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def agent_files() -> dict[str, Path]:
    return {path.stem: path for path in sorted(AGENTS.glob("*.md"))}


class NoMarkdownViewTest(unittest.TestCase):
    """M4: no prompt asks an agent to write a markdown view of its JSON."""

    def test_no_agent_is_asked_to_write_a_markdown_file(self):
        for stem, path in agent_files().items():
            if stem in MD_AUTHORS:
                continue
            found = MD_WRITE.findall(read(path))
            self.assertEqual([], found, f"{stem}: {found}")

    def test_no_dispatch_prompt_is_asked_to_write_a_markdown_file(self):
        for path in sorted(PROMPTS.glob("*.md")):
            if path.stem in MD_AUTHORS:
                continue
            found = MD_WRITE.findall(read(path))
            self.assertEqual([], found, f"{path.name}: {found}")

    def test_no_agent_carries_a_v1_parallel_view_phrase(self):
        for stem, path in agent_files().items():
            text = read(path).lower()
            hits = [phrase for phrase in MD_VIEW_PHRASES if phrase in text]
            self.assertEqual([], hits, stem)

    def test_no_dispatch_prompt_carries_a_v1_parallel_view_phrase(self):
        for path in sorted(PROMPTS.glob("*.md")):
            text = read(path).lower()
            hits = [phrase for phrase in MD_VIEW_PHRASES if phrase in text]
            self.assertEqual([], hits, path.name)

    def test_the_two_markdown_authors_are_the_only_exception(self):
        roster = set(dispatch.AGENT_MODELS)
        self.assertEqual(MD_AUTHORS, roster & MD_AUTHORS)
        self.assertEqual(set(), MD_AUTHORS & set(EXAMPLE_SCHEMA))
        self.assertEqual(roster, MD_AUTHORS | set(EXAMPLE_SCHEMA))
        self.assertEqual(set(), roster & NO_OUTPUT)
        self.assertEqual(NO_OUTPUT, set(agent_files()) - roster)


class WorkedExampleTest(unittest.TestCase):
    """§4.2: one filled example per agent, valid against the schema of §6."""

    def test_every_json_agent_carries_exactly_one_example(self):
        for agent in EXAMPLE_SCHEMA:
            blocks = JSON_BLOCK.findall(read(AGENTS / f"{agent}.md"))
            self.assertEqual(1, len(blocks), agent)

    def test_every_example_validates_against_its_schema(self):
        for agent, name in EXAMPLE_SCHEMA.items():
            block = JSON_BLOCK.findall(read(AGENTS / f"{agent}.md"))[0]
            example = json.loads(block)
            self.assertEqual([], schema.validate(example, name), agent)

    def test_every_example_names_its_schema_in_the_text(self):
        for agent, name in EXAMPLE_SCHEMA.items():
            self.assertIn(f"schema `{name}`", read(AGENTS / f"{agent}.md"), agent)

    def test_reviewer_examples_use_real_checklist_ids(self):
        for agent, kind in EXAMPLE_CHECKLIST.items():
            known = {item["id"] for item in json.loads(read(CHECKLISTS / f"{kind}.json"))}
            example = json.loads(JSON_BLOCK.findall(read(AGENTS / f"{agent}.md"))[0])
            used = {row["id"] for row in example.get("checklist") or []}
            used |= {
                row["checklist_id"]
                for row in example.get("issues") or []
                if row.get("checklist_id")
            }
            self.assertTrue(used, agent)
            self.assertEqual(set(), used - known, agent)

    def test_reviewer_examples_put_reasoning_first(self):
        for agent, name in EXAMPLE_SCHEMA.items():
            block = JSON_BLOCK.findall(read(AGENTS / f"{agent}.md"))[0]
            if '"reasoning"' not in block:
                continue
            keys = list(json.loads(block))
            self.assertEqual("reasoning", keys[0], agent)

    def test_the_writer_shows_a_markdown_draft_and_no_json_output(self):
        text = read(AGENTS / "memo-writer.md")
        self.assertIn("```markdown", text)
        self.assertEqual([], JSON_BLOCK.findall(text))


if __name__ == "__main__":
    unittest.main()
