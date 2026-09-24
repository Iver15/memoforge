"""T-02 / D-79: which `lib/agent-core/` blocks each dispatch prompt actually delivers (ТЗ §4.2)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import dispatch  # noqa: E402

AGENT_CORE = PLUGIN_ROOT / "lib" / "agent-core"
PROMPTS = PLUGIN_ROOT / "scripts" / "memoforge" / "prompts"

# §4.2: «читают `memo-writer`, `form-reviewer`, `client-readiness-reviewer`, `revision-mediator`».
STYLE_PROFILE_READERS = {
    "memo-writer",
    "form-reviewer",
    "client-readiness-reviewer",
    "revision-mediator",
}

# §4.1: the three agents that inherit the session tool pool because they call the legal MCP servers.
MCP_AGENTS = {"fact-assumption-analyst", "legal-researcher", "currency-checker"}

# §4.2: the blocks every dispatched agent gets.
UNIVERSAL = ("untrusted-content.md", "output-json.md", "logging.md")

INCLUDE = re.compile(r"\$\{paths_agent_core\}/([A-Za-z0-9._-]+\.md)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def included(agent: str) -> set[str]:
    """The `lib/agent-core/` file names one dispatch prompt substitutes a path for."""
    return set(INCLUDE.findall(read(dispatch.prompt_path(agent))))


def readers_of(block: str) -> set[str]:
    """Every pipeline agent whose prompt pulls in one agent-core block."""
    return {agent for agent in dispatch.PIPELINE_AGENTS if block in included(agent)}


class AgentCoreInclusionTest(unittest.TestCase):
    """The composition B-01/B-02 broke: §4.2 is normative in both directions."""

    def test_every_pipeline_agent_has_a_prompt_on_disk(self):
        self.assertEqual(13, len(dispatch.PIPELINE_AGENTS))
        for agent in dispatch.PIPELINE_AGENTS:
            self.assertTrue(dispatch.prompt_path(agent).is_file(), agent)

    def test_style_profile_goes_to_exactly_the_four_readers(self):
        self.assertEqual(STYLE_PROFILE_READERS, readers_of("style-profile.md"))

    def test_tooling_core_goes_to_exactly_the_three_mcp_agents(self):
        self.assertEqual(MCP_AGENTS, readers_of("tooling-core.md"))

    def test_the_universal_blocks_go_to_every_pipeline_prompt(self):
        for block in UNIVERSAL:
            with self.subTest(block=block):
                self.assertEqual(set(dispatch.PIPELINE_AGENTS), readers_of(block))

    def test_every_agent_core_file_is_referenced_by_at_least_one_prompt(self):
        shipped = {path.name for path in sorted(AGENT_CORE.glob("*.md"))}
        self.assertTrue(shipped, "lib/agent-core is empty")
        referenced = {name for agent in dispatch.PIPELINE_AGENTS for name in included(agent)}
        self.assertEqual(set(), shipped - referenced, "shipped but never delivered")
        self.assertEqual(set(), referenced - shipped, "delivered but not shipped")

    def test_every_style_profile_reader_also_gets_the_path(self):
        """B-02: the block's first rule reads `${prose_style_path}`, so the prompt must print it."""
        for agent in sorted(STYLE_PROFILE_READERS):
            with self.subTest(agent=agent):
                self.assertIn("${prose_style_path}", read(dispatch.prompt_path(agent)), agent)

    def test_the_brief_agents_get_the_universal_blocks_and_no_style_profile(self):
        """D-223: the decision brief takes no style profile (spec DB-04), so neither brief prompt names one."""
        for agent in ("brief-writer", "brief-fidelity-reviewer"):
            with self.subTest(agent=agent):
                self.assertIn(agent, dispatch.PIPELINE_AGENTS)
                self.assertEqual(set(UNIVERSAL), included(agent))
                text = read(dispatch.prompt_path(agent))
                self.assertNotIn("${prose_style_path}", text)
                self.assertNotIn("style-profile.md", text)

    def test_no_other_prompt_prints_the_style_profile_path(self):
        """§4.2: «Other agents ignore it even when the path is present» — so it is not present."""
        for agent in dispatch.PIPELINE_AGENTS:
            if agent in STYLE_PROFILE_READERS:
                continue
            with self.subTest(agent=agent):
                self.assertNotIn("${prose_style_path}", read(dispatch.prompt_path(agent)), agent)


if __name__ == "__main__":
    unittest.main()
