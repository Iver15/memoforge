"""Tone, size and section order of agents/*.md (ТЗ §4.2, §7.1; owner requirement: no shouting)."""

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
PROMPTS = PLUGIN_ROOT / "scripts" / "memoforge" / "prompts"
TEMPLATES = PLUGIN_ROOT / "templates"
PROBE = "probe-echo"

# §4.2: the upper-case intensifiers that v1 leaned on and v2 drops.
SHOUTING = re.compile(r"\b(HARD RULE|MANDATORY|STOP|MUST|NEVER|ALWAYS|DO NOT)\b")

# §7.1 and M12: the dead live-progress stack, and the interpreter that is a Store stub on Windows.
BANNED = (
    "update_artifact",
    "create_artifact",
    "visualize",
    "TodoWrite",
    "mark_chapter",
    "render_live_progress",
    "live_progress",
    "python3",
)

# §4.3, §4.4, §4.2: the prompt ceilings, in lines including front matter.
MAX_LINES = {"legal-researcher": 120, "memo-writer": 150}
DEFAULT_MAX_LINES = 100

# §4.2: one section order for every agent.
SECTIONS = ["Role", "Task", "Inputs", "Output contract", "Rules", "Failure modes", "Final response"]

# §4.2: the v1 sections that are gone.
REMOVED_SECTIONS = ("Pre-return checklist", "Live progress", "Tool-call telemetry", "Logging")

HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)

# D-78: `${…}` belongs to the dispatch template; an agent body names the value in prose.
PLACEHOLDER = re.compile(r"\$\{")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def agent_files() -> dict[str, Path]:
    return {path.stem: path for path in sorted(AGENTS.glob("*.md"))}


def roster_files() -> dict[str, Path]:
    return {stem: path for stem, path in agent_files().items() if stem != PROBE}


class ToneTest(unittest.TestCase):
    """The owner's requirement: ordinary prose, no upper-case enforcement."""

    def test_no_agent_shouts(self):
        for stem, path in agent_files().items():
            hits = sorted(set(SHOUTING.findall(read(path))))
            self.assertEqual([], hits, stem)

    def test_no_dispatch_prompt_shouts(self):
        for path in sorted(PROMPTS.glob("*.md")):
            hits = sorted(set(SHOUTING.findall(read(path))))
            self.assertEqual([], hits, path.name)

    def test_no_agent_carries_a_dead_channel_or_a_python3_call(self):
        for stem, path in agent_files().items():
            text = read(path)
            hits = [token for token in BANNED if token in text]
            self.assertEqual([], hits, stem)

    def test_no_dispatch_prompt_carries_a_dead_channel_or_a_python3_call(self):
        for path in sorted(PROMPTS.glob("*.md")):
            text = read(path)
            hits = [token for token in BANNED if token in text]
            self.assertEqual([], hits, path.name)

    def test_removed_v1_sections_are_gone(self):
        for stem, path in agent_files().items():
            headings = HEADING.findall(read(path))
            for removed in REMOVED_SECTIONS:
                self.assertNotIn(removed, headings, stem)


class SizeTest(unittest.TestCase):
    """§4.2: the ceilings that keep the agent layer readable."""

    def test_every_agent_is_within_its_line_budget(self):
        for stem, path in agent_files().items():
            limit = MAX_LINES.get(stem, DEFAULT_MAX_LINES)
            lines = len(read(path).splitlines())
            self.assertLessEqual(lines, limit, f"{stem}: {lines} > {limit}")

    def test_the_two_raised_ceilings_are_the_researcher_and_the_writer(self):
        self.assertEqual({"legal-researcher", "memo-writer"}, set(MAX_LINES))
        self.assertLessEqual(set(MAX_LINES), set(dispatch.AGENT_MODELS))


class SectionOrderTest(unittest.TestCase):
    """§4.2: Role -> Task -> Inputs -> Output contract -> Rules -> Failure modes -> Final response."""

    def test_every_roster_agent_has_the_sections_in_order(self):
        for stem, path in roster_files().items():
            headings = [h for h in HEADING.findall(read(path)) if h in SECTIONS]
            self.assertEqual(SECTIONS, headings, stem)

    def test_inputs_section_points_at_the_dispatch_prompt(self):
        for stem, path in roster_files().items():
            if stem == "style-extractor":
                continue  # §4.6: dispatched by the style skill, not by `mf next`.
            body = read(path).split("## Inputs", 1)[1].split("\n## ", 1)[0]
            self.assertIn("dispatch prompt", body, stem)
            for name in ("work_dir", "step_id", "attempt", "slot"):
                self.assertIn(f"`{name}`", body, f"{stem}: {name}")

    def test_final_response_is_capped_at_a_hundred_words(self):
        for stem, path in roster_files().items():
            body = read(path).split("## Final response", 1)[1]
            self.assertIn("100 words", body, stem)

    def test_shared_blocks_are_referenced_not_copied(self):
        for stem, path in roster_files().items():
            if stem == "style-extractor":
                continue
            self.assertIn("agent-core directory named in your prompt", read(path), stem)


class DeclaredInputTest(unittest.TestCase):
    """D-114: the currency step declares `research/sources.json`, so its agent may not rewrite it."""

    def test_currency_checker_body_forbids_the_registry_writing_commands(self):
        body = read(agent_files()["currency-checker"])
        self.assertIn("declared input", body)
        for command in ("sources verify", "sources liveness", "sources register"):
            with self.subTest(command=command):
                self.assertIn(f"`<mf> {command}`", body)
        self.assertNotIn("--set", body)
        self.assertIn("read the verification fields", body)


class AddresseeTest(unittest.TestCase):
    """D34-21: `default_if_wrong` named an Article 27 FRIA duty a private employer does not owe."""

    def test_the_analyst_attributes_a_duty_only_to_its_addressee(self):
        body = read(agent_files()["fact-assumption-analyst"])
        rules = body.split("## Rules", 1)[1].split("\n## ", 1)[0]
        self.assertIn("default_if_wrong", rules)
        self.assertIn("addressee", rules)
        self.assertIn("unattributed", rules)


class QuestionCoverageTest(unittest.TestCase):
    """D34-05: the run of 20260910 answered a question the user had not asked."""

    def test_both_templates_take_the_question_line_from_the_user_verbatim(self):
        for name in ("executive-brief", "classical-memo"):
            with self.subTest(template=name):
                header = read(TEMPLATES / f"{name}.md").split("**Header block**", 1)[1]
                header = header.split("\n", 1)[0]
                self.assertIn("user_query", header)
                self.assertIn("word for word", header)
                self.assertNotIn("restatement of the question", header)

    def test_the_writer_keeps_every_sub_question_addressable(self):
        rules = read(agent_files()["memo-writer"]).split("## Rules", 1)[1].split("\n## ", 1)[0]
        self.assertIn("sub-question", rules)
        self.assertIn("recommendations", rules)
        self.assertIn("`Question:`", rules)


class PinpointScriptTest(unittest.TestCase):
    """D-196: a source that numbers itself in another script is pinpointed the way it does."""

    def test_the_writer_prompt_limits_the_machine_form_to_latin_script_sources(self):
        text = read(PROMPTS / "memo-writer.md")
        self.assertIn("Latin script", text)
        self.assertIn("п. 1 ст. 887", text)
        self.assertIn("п. 3 разд. «Возмещение»", text)

    def test_the_writer_body_carries_the_same_rule(self):
        rules = read(AGENTS / "memo-writer.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Latin script", rules)
        self.assertIn("п. 1 ст. 887", rules)


class QuestionInstructionTest(unittest.TestCase):
    """D-198: the `Question:` line carries the question, not the instruction to the pipeline."""

    def test_both_templates_drop_an_instruction_about_the_form_of_the_work(self):
        for name in ("executive-brief", "classical-memo"):
            with self.subTest(template=name):
                header = read(TEMPLATES / f"{name}.md").split("**Header block**", 1)[1]
                header = header.split("\n", 1)[0]
                self.assertIn("instruction", header)
                self.assertIn("the header already states", header)

    def test_the_writer_prompt_states_the_same_exception(self):
        text = read(PROMPTS / "memo-writer.md")
        self.assertIn("`Question:`", text)
        self.assertIn("the header already states", text)


class PlaceholderTest(unittest.TestCase):
    """D-78 / §4.2: the dispatch prompt substitutes the paths; the agent body names them in prose."""

    def test_no_agent_body_carries_a_literal_placeholder(self):
        for stem, path in agent_files().items():
            hits = [line for line in read(path).splitlines() if PLACEHOLDER.search(line)]
            self.assertEqual([], hits, f"{stem}: `${{…}}` is not substituted in an agent body")

    def test_every_dispatch_prompt_substitutes_the_shared_paths(self):
        for path in sorted(PROMPTS.glob("*.md")):
            text = read(path)
            with self.subTest(prompt=path.name):
                for name in ("${paths_agent_core}", "${mf}", "${work_dir}"):
                    self.assertIn(name, text, path.name)


if __name__ == "__main__":
    unittest.main()
