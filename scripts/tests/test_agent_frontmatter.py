"""Tests for agents/*.md front matter against `lib/models.md` (ТЗ §4.1, M11)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import dispatch  # noqa: E402

AGENTS = PLUGIN_ROOT / "agents"
MODELS_MD = PLUGIN_ROOT / "lib" / "models.md"

# §4.1: the diagnostic agent of probe P9 is not part of the roster and has no models.md row.
PROBE = "probe-echo"

# §4.1: no agent spawns agents or asks the user a question; the Cowork artifact tools are gone (§7.1).
FORBIDDEN_TOOLS = ("Agent", "Task", "AskUserQuestion", "mcp__cowork__*")
DISALLOWED_LINE = "Agent, Task, AskUserQuestion, mcp__cowork__*"

ABSENT = "—"

# | `agent` | `model` | `effort` | `tools: …`; `disallowedTools: …` | why |
ROW = re.compile(
    r"^\|\s*`([a-z-]+)`\s*\|\s*`([a-z._]+)`\s*\|\s*`([a-z]+)`\s*\|\s*(.+?)\s*\|",
)
FIELD = re.compile(r"`(tools|disallowedTools):\s*([^`]*)`")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def frontmatter(text: str) -> dict:
    """Flat YAML front matter of an agent file (no agent uses nesting)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def models_table() -> dict[str, dict]:
    """`lib/models.md` as `{agent: {model, effort, tools, disallowedTools}}` (the single source)."""
    table: dict[str, dict] = {}
    for line in read(MODELS_MD).splitlines():
        match = ROW.match(line)
        if not match:
            continue
        agent, model, effort, policy = match.groups()
        fields = {name: value.strip() for name, value in FIELD.findall(policy)}
        table[agent] = {
            "model": model,
            "effort": effort,
            "tools": fields.get("tools", ABSENT),
            "disallowedTools": fields.get("disallowedTools", ABSENT),
        }
    return table


def agent_files() -> dict[str, Path]:
    return {path.stem: path for path in sorted(AGENTS.glob("*.md"))}


class RosterTest(unittest.TestCase):
    """§4.1: 16 - 6 + 2 = 12 agents, plus the two of the decision brief (D-223) and the P9 probe."""

    def test_models_md_lists_exactly_fourteen_agents(self):
        self.assertEqual(14, len(models_table()))

    def test_agents_directory_holds_the_fourteen_plus_probe_echo(self):
        files = agent_files()
        self.assertEqual(set(models_table()) | {PROBE}, set(files))
        self.assertEqual(15, len(files))

    def test_merged_and_scripted_v1_agents_are_gone(self):
        for name in (
            "statutory-researcher",
            "case-law-researcher",
            "doctrinal-researcher",
            "clarity-reviewer",
            "style-reviewer",
            "source-pack-builder",
        ):
            self.assertNotIn(name, agent_files(), name)

    def test_dispatch_knows_every_agent_of_the_roster(self):
        self.assertEqual(set(models_table()), set(dispatch.AGENT_MODELS))


class FrontmatterTest(unittest.TestCase):
    """§4.1: every field of an agent file is the row of `lib/models.md`."""

    def setUp(self):
        self.table = models_table()
        self.files = agent_files()

    def test_name_matches_the_file_name(self):
        for stem, path in self.files.items():
            self.assertEqual(stem, frontmatter(read(path)).get("name"), stem)

    def test_description_is_present_and_substantial(self):
        for stem, path in self.files.items():
            description = frontmatter(read(path)).get("description") or ""
            self.assertGreater(len(description), 40, stem)

    def test_model_and_effort_match_models_md(self):
        for agent, row in self.table.items():
            fields = frontmatter(read(self.files[agent]))
            self.assertEqual(row["model"], fields.get("model"), agent)
            self.assertEqual(row["effort"], fields.get("effort"), agent)

    def test_mcp_agents_have_no_tools_field_and_the_full_disallowed_list(self):
        inheriting = [agent for agent, row in self.table.items() if row["tools"] == ABSENT]
        self.assertEqual(
            {"fact-assumption-analyst", "legal-researcher", "currency-checker"}, set(inheriting)
        )
        for agent in inheriting:
            fields = frontmatter(read(self.files[agent]))
            self.assertNotIn("tools", fields, agent)
            self.assertEqual(DISALLOWED_LINE, fields.get("disallowedTools"), agent)

    def test_allowlisted_agents_declare_their_tools_and_no_denylist(self):
        for agent, row in self.table.items():
            if row["tools"] == ABSENT:
                continue
            fields = frontmatter(read(self.files[agent]))
            self.assertEqual(row["tools"], fields.get("tools"), agent)
            self.assertNotIn("disallowedTools", fields, agent)

    def test_no_agent_can_spawn_an_agent_or_ask_the_user(self):
        for agent, path in self.files.items():
            fields = frontmatter(read(path))
            tools = [item.strip() for item in (fields.get("tools") or "").split(",") if item.strip()]
            if tools:
                for forbidden in FORBIDDEN_TOOLS:
                    self.assertNotIn(forbidden, tools, agent)
            else:
                self.assertEqual(DISALLOWED_LINE, fields.get("disallowedTools"), agent)

    def test_every_agent_can_reach_bash_for_agent_log(self):
        for agent, path in self.files.items():
            tools = frontmatter(read(path)).get("tools")
            if tools is not None:
                self.assertIn("Bash", tools, agent)

    def test_writer_keeps_edit_for_targeted_revisions(self):
        self.assertIn("Edit", frontmatter(read(self.files["memo-writer"]))["tools"])


if __name__ == "__main__":
    unittest.main()
