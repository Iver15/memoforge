"""Tone, size and section order of agents/*.md (ТЗ §4.2, §7.1; owner requirement: no shouting)."""

from __future__ import annotations

import re
import shlex
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, dispatch  # noqa: E402

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

    def test_the_template_takes_the_question_line_from_the_user_verbatim(self):
        header = read(TEMPLATES / "classical-memo.md").split("**Header block**", 1)[1]
        header = header.split("\n", 1)[0]
        self.assertIn("user_query", header)
        self.assertIn("word for word", header)
        self.assertNotIn("restatement of the question", header)

    def test_the_template_does_not_ask_for_the_template_name(self):
        # D-216: runs 71 and 74 printed `Template: classical-memo` in the client's header.
        header = read(TEMPLATES / "classical-memo.md").split("**Header block**", 1)[1]
        header = header.split("\n", 1)[0]
        self.assertIn("date (YYYY-MM-DD), jurisdictions, the question.", header)
        self.assertNotIn("template name", header)

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

    def test_the_template_drops_an_instruction_about_the_form_of_the_work(self):
        header = read(TEMPLATES / "classical-memo.md").split("**Header block**", 1)[1]
        header = header.split("\n", 1)[0]
        self.assertIn("instruction", header)
        self.assertIn("the header already states", header)

    def test_the_writer_prompt_states_the_same_exception(self):
        text = read(PROMPTS / "memo-writer.md")
        self.assertIn("`Question:`", text)
        self.assertIn("the header already states", text)


class SourceSavingTest(unittest.TestCase):
    """D-205: the agent bodies and the shared tooling block say what the dispatch prompt says."""

    def test_the_researcher_saves_a_web_source_and_registers_three_cases_only(self):
        rules = read(AGENTS / "legal-researcher.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`<mf> sources save`", rules)
        self.assertIn("three cases", rules)
        for kind in ("excerpt", "client_file", "agent_summary"):
            self.assertIn(f"`--raw-kind {kind}`", rules)
        self.assertIn("no exceptions", rules)
        self.assertIn("never ask the user to solve it", rules)
        # The older, longer rule is gone, not softened.
        self.assertNotIn("goes through `<mf> sources register …` with `--raw-file`", rules)

    def test_the_researcher_body_prints_no_command_the_cli_would_refuse(self):
        """Addendum §6: a `<mf> sources save|register` line with arguments is a whole command.

        The bare name of the command is prose; a line with arguments is an example, and an example
        with `…` in place of a required flag is one the CLI would refuse.
        """
        body = read(AGENTS / "legal-researcher.md")
        for printed in re.findall(r"`(<mf> sources (?:save|register) [^`]+)`", body):
            with self.subTest(printed=printed):
                tokens = shlex.split(printed.replace("<mf>", "mf", 1))
                try:
                    cli.build_parser().parse_args(tokens[1:])
                except SystemExit:
                    self.fail(f"the body shows a command the CLI refuses: {printed}")

    def test_the_sufficiency_reviewer_reads_raw_kind_and_the_save_outcome(self):
        rules = read(AGENTS / "research-sufficiency-reviewer.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        for words in ("`raw_kind`", "`meta.save_outcome`", "`mf sources save`", "`raw_original_path`"):
            with self.subTest(words=words):
                self.assertIn(words, rules)
        self.assertNotIn("A `critical` source registered without saved raw text is a `missing` gap", rules)

    def test_no_agent_text_hands_a_challenge_to_the_user(self):
        """D-205 fix round 1: a CAPTCHA met on any path is never outsourced to a person.

        The researcher's rule has no exceptions — the channel is closed for the run and the work moves
        to the fallbacks — and an agent reads its body, its dispatch prompt and the shared blocks
        alike, so none of them may keep the old «ask the user to open it and paste the passage back».
        """
        handoff = re.compile(
            # The prohibition itself — «never ask the user to solve it» — is the rule, not a handoff.
            r"paste (?:the|it|that)\b|own browser|(?<!never )ask(?:s|ing)? the user to (?:open|solve|paste|get past)",
            re.IGNORECASE,
        )
        texts = sorted(AGENTS.glob("*.md")) + sorted(PROMPTS.glob("*.md"))
        texts += sorted((PLUGIN_ROOT / "lib" / "agent-core").glob("*.md"))
        for path in texts:
            with self.subTest(path=path.name):
                self.assertEqual([], handoff.findall(read(path)))
        tooling = read(PLUGIN_ROOT / "lib" / "agent-core" / "tooling-core.md")
        self.assertIn("nobody is asked to solve anything", tooling)

    def test_the_shared_tooling_block_no_longer_registers_a_fetched_file(self):
        text = read(PLUGIN_ROOT / "lib" / "agent-core" / "tooling-core.md")
        self.assertNotIn('register the file with `--tool "mf-fetch <host>"`', text)
        self.assertIn("`mf sources save`", text)
        # A captcha that `save` reports is never handed to the user (addendum §1).
        self.assertIn("`channel_unavailable: captcha`", text)
        # `save` writes the registry too, so a step whose declared input it is leaves it alone.
        self.assertIn("`mf sources save`, `mf sources verify`", text)


class TextVerifiedReviewTest(unittest.TestCase):
    """D-208: the finding pairs a claim with its source; for a `critical` source the saved text wins."""

    def test_the_citation_auditor_role_makes_the_text_the_ceiling(self):
        text = read(AGENTS / "citation-auditor.md")
        role = text.split("## Role", 1)[1].split("\n## ", 1)[0]
        self.assertIn("pairing key", role)
        self.assertIn("the text wins", role)
        self.assertNotIn("names the record entry it departs from", text)
        rules = text.split("## Rules", 1)[1].split("\n## ", 1)[0]
        for needle in ("CIT-04", "`finding_disagrees: true`", "`source_evidence`", "`text_checks`", "`major`"):
            self.assertIn(needle, rules, needle)

    def test_the_counterargument_reviewer_checks_before_it_asserts_a_holding(self):
        rules = read(AGENTS / "counterargument-reviewer.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        for needle in ("the text wins", "`mf quote locate`", "`source_evidence`", "`finding_disagrees: true`"):
            self.assertIn(needle, rules, needle)

    def test_the_mediator_restates_a_source_only_with_its_passage(self):
        rules = read(AGENTS / "revision-mediator.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`source_evidence`", rules)
        self.assertIn("withdraw-or-qualify, never as restate", rules)

    def test_both_claim_reviewers_confirm_a_court_s_act_only_by_its_own_sentence(self):
        """D-208, fix round 2: one line in each agent body; the dispatch prompts spell it out."""
        for stem in ("citation-auditor", "counterargument-reviewer"):
            rules = read(AGENTS / f"{stem}.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
            for needle in ("the court's own sentence", "attributed to the offer", "no ellipses"):
                with self.subTest(agent=stem, needle=needle):
                    self.assertIn(needle, rules)

    def test_the_citation_auditor_checks_every_cit_01_candidate_first(self):
        """D-208, fix round 3: one line in the agent body; the dispatch prompt spells out the budget order."""
        rules = read(AGENTS / "citation-auditor.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        for needle in ("CIT-01 candidate", "does not record the rule the draft states", "checked first"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)
        other = read(AGENTS / "counterargument-reviewer.md")
        self.assertNotIn("CIT-01 candidate", other)

    def test_the_old_ceiling_wording_is_gone(self):
        for path in [*agent_files().values(), *sorted(PROMPTS.glob("*.md"))]:
            with self.subTest(file=path.name):
                self.assertNotIn("must not go beyond", read(path))


class CourtWordsTest(unittest.TestCase):
    """D-209: the agent bodies carry in a line or two what the dispatch prompts spell out."""

    def test_the_researcher_rules_keep_the_court_s_words_apart_and_check_the_quotes(self):
        rules = read(AGENTS / "legal-researcher.md").split("## Rules", 1)[1].split("\n## ", 1)[0]
        for needle in ("the court's own statement", "`<mf> quote locate`", "read together", "higher-court act"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)

    def test_the_sufficiency_reviewer_asks_for_the_provision_and_may_spot_check_holdings(self):
        text = read(AGENTS / "research-sufficiency-reviewer.md")
        task = text.split("## Task", 1)[1].split("\n## ", 1)[0]
        self.assertIn("the provision that establishes it", task)
        rules = text.split("## Rules", 1)[1].split("\n## ", 1)[0]
        for needle in ("`mf quote locate`", "up to five", "gap for `case_law`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)


def rules_of(stem: str) -> str:
    return read(AGENTS / f"{stem}.md").split("## Rules", 1)[1].split("\n## ", 1)[0]


class SourcedDirectionTest(unittest.TestCase):
    """D-215: a reviewer needs a pack source to turn a conclusion; the writer marks what rests on an assumption."""

    DIRECTION = ("logic-reviewer", "counterargument-reviewer", "revision-mediator", "client-readiness-reviewer")

    def test_the_reviewers_and_the_mediator_turn_a_conclusion_only_with_a_pack_source(self):
        for stem in self.DIRECTION:
            with self.subTest(agent=stem):
                rules = rules_of(stem)
                self.assertIn("direction of a conclusion", rules)
                self.assertIn("pack source", rules)
                self.assertIn("the writer's call", rules)
                text = " ".join(read(PROMPTS / f"{stem}.md").split())
                for needle in (
                    "changes the direction of a conclusion",
                    "a source of the frozen source pack",
                    "resolve a stated contradiction or to add the opposing argument",
                    "the direction stays the writer's call",
                    "the draft's own conditions",
                ):
                    self.assertIn(needle, text)

    def test_no_limitation_becomes_an_instruction_to_delay_a_statutory_step(self):
        for stem in ("client-readiness-reviewer", "memo-writer"):
            with self.subTest(agent=stem):
                self.assertIn("delay a statutory step", rules_of(stem))
                self.assertIn("delay a statutory step", " ".join(read(PROMPTS / f"{stem}.md").split()))
        readiness = " ".join(read(PROMPTS / "client-readiness-reviewer.md").split())
        self.assertIn("already discloses and that changes no conclusion", readiness)
        self.assertIn("already discloses", rules_of("client-readiness-reviewer"))

    def test_the_writer_marks_a_conclusion_that_rests_on_an_assumption(self):
        self.assertIn("on the assumed facts", rules_of("memo-writer"))
        text = " ".join(read(PROMPTS / "memo-writer.md").split())
        self.assertIn("depends on an assumption rather than on a stated fact", text)
        self.assertIn('"on the assumed facts"', text)

    def test_a_polish_reuses_a_cited_authority_only_in_the_finding_s_own_section(self):
        # D-236: the scope check allows the reuse only where the citations re-check grades it.
        writer = (
            "On `polish`, a source the memo already cites may be cited again only in the section of the "
            "finding you are polishing; the summary bullet, the conclusion item and the risk line you keep "
            "in step get no new `[[src:]]` token."
        )
        readiness = (
            "a polish issue may ask for an authority the memo already cites only in the finding's own section."
        )
        for stem, needle in (("memo-writer", writer), ("client-readiness-reviewer", readiness)):
            with self.subTest(agent=stem):
                self.assertIn(needle, " ".join(rules_of(stem).split()))
                self.assertIn(needle, " ".join(read(PROMPTS / f"{stem}.md").split()))

    def test_a_gap_closed_from_a_saved_text_is_weak_and_why_blocking_stays_with_the_researcher(self):
        rules = rules_of("research-sufficiency-reviewer")
        text = " ".join(read(PROMPTS / "research-sufficiency-reviewer.md").split())
        for body in (rules, text):
            self.assertIn("already saved in `research/raw/`", body)
            self.assertIn("a new text has to be fetched", body)
            self.assertIn("addressed to the researcher", body)
            self.assertNotIn("the run summary prints it after the gap", body)


def flat(path: Path) -> str:
    """A dispatch prompt with its line breaks folded, so a needle may span a wrapped line."""
    return " ".join(read(path).split())


class ReasoningGapTest(unittest.TestCase):
    """D-237: a gap in the reasoning goes to a lawyer, and the client sees it in the memo language."""

    READINESS = "client-readiness-reviewer"

    def both(self, stem: str) -> tuple[str, str]:
        return flat(PROMPTS / f"{stem}.md"), " ".join(rules_of(stem).split())

    def test_readiness_decides_by_the_repair_and_asks_the_lawyer_s_question(self):
        for needle in (
            "the repair you choose",
            "the question the lawyer must answer",
            "A `logic` or `counterarguments` finding allows `polish`, `manual_review` or `leave`.",
        ):
            for where, text in zip(("prompt", "body"), self.both(self.READINESS)):
                with self.subTest(needle=needle, where=where):
                    self.assertIn(needle, text)

    def test_a_manual_review_row_never_switches_the_polish_off(self):
        # C1: `manual_review_required` exports without a polish, so one lawyer's row would strand the rest.
        needle = (
            "A `manual_review` disposition does not by itself make the verdict `manual_review_required`: when any "
            "open finding is `polish`, or any issue of yours can be fixed by a polish, the verdict is "
            "`needs_final_polish`, and the `manual_review` findings reach the Status section anyway."
        )
        for where, text in zip(("prompt", "body"), self.both(self.READINESS)):
            with self.subTest(where=where):
                self.assertIn(needle, text)

    def test_readiness_writes_a_blocker_only_for_a_blocker_row(self):
        # C4: a `blocker` issue reads to the writer as "withdraw or qualify", which cannot add a disclosure.
        needle = "`severity: blocker` on an issue of yours is used only for a finding the list marks `blocker`."
        for where, text in zip(("prompt", "body"), self.both(self.READINESS)):
            with self.subTest(where=where):
                self.assertIn(needle, text)

    def test_the_logic_and_counterarguments_reviewers_write_issue_client_on_majors(self):
        for stem in ("logic-reviewer", "counterargument-reviewer"):
            prompt, body = self.both(stem)
            with self.subTest(agent=stem):
                self.assertIn("`severity: major` carries `issue_client` too", prompt)
                self.assertIn("every `major` issue carries `issue_client`", body)


class SufficientGapsTest(unittest.TestCase):
    """D-238: the gaps of a `sufficient` verdict are disclosed, checked at delivery and handed to citations."""

    def test_readiness_receives_the_warnings_and_fails_crd_03_on_an_undisclosed_one(self):
        prompt = read(PROMPTS / "client-readiness-reviewer.md")
        self.assertIn("- warnings the memo must disclose: ${drafting_warnings}", prompt)
        needle = (
            "CRD-03 fails for a warning that touches a conclusion and that the memo does not disclose; "
            "the issue names the warning."
        )
        self.assertIn(needle, flat(PROMPTS / "client-readiness-reviewer.md"))
        self.assertIn(needle, " ".join(rules_of("client-readiness-reviewer").split()))

    def test_the_writer_states_a_research_gap_as_a_limitation(self):
        needle = (
            "A research-gap warning (`unresolved_research_gap`) is stated as a limitation of the memo, in the "
            "facts section's limitations block, and next to the conclusion it touches."
        )
        self.assertIn(needle, flat(PROMPTS / "memo-writer.md"))
        self.assertIn(needle, " ".join(rules_of("memo-writer").split()))

    def test_the_citations_reviewer_checks_the_sufficiency_findings_first(self):
        self.assertIn(
            "- what the sufficiency reviewer found on saved texts (check the draft's statements on these first, "
            "as item 1 of your budget): ${sufficiency_checks}",
            read(PROMPTS / "citation-auditor.md"),
        )
        self.assertIn("and the sufficiency checks your prompt lists", " ".join(rules_of("citation-auditor").split()))


class CourtAttributionTest(unittest.TestCase):
    """D-239: run 79 credited a court with a negative, an adopted measure and an outcome its text never states."""

    def both(self, stem: str) -> tuple[str, str]:
        return flat(PROMPTS / f"{stem}.md"), " ".join(rules_of(stem).split())

    def test_the_auditor_confirms_only_the_whole_statement(self):
        # The prompt quotes «агентский, а не как хранение», the body «а не как хранение»: the needle fits both.
        for needle in ("covers the whole statement", "а не как хранение»", "reported truthfully"):
            for where, text in zip(("prompt", "body"), self.both("citation-auditor")):
                with self.subTest(needle=needle, where=where):
                    self.assertIn(needle, text)

    def test_the_researcher_says_where_the_text_stops_and_what_arithmetic_proves(self):
        for needle in ("breaks off before the operative part", "arithmetically correct"):
            for where, text in zip(("prompt", "body"), self.both("legal-researcher")):
                with self.subTest(needle=needle, where=where):
                    self.assertIn(needle, text)

    def test_the_counterarguments_reviewer_checks_the_other_sources_before_one_source(self):
        for where, text in zip(("prompt", "body"), self.both("counterargument-reviewer")):
            with self.subTest(where=where):
                self.assertIn("rests on one source", text)


class CourtPositionTest(unittest.TestCase):
    """D-246: run 79's court misattributions were born at the writer, and the citations reviewer graded one
    as CIT-07, which the mediator then dropped, instead of CIT-02."""

    def test_the_writer_credits_a_court_only_with_its_own_reasoning_and_conclusion(self):
        prompt = flat(PROMPTS / "memo-writer.md")
        for needle in (
            "A court is credited only with its own reasoning and its own conclusion",
            "«суд рассуждал об агентировании»",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, prompt)
        self.assertIn(
            "A court is credited only with its own reasoning and conclusion", " ".join(rules_of("memo-writer").split())
        )

    def test_the_auditor_grades_a_misstated_court_position_as_cit_02(self):
        self.assertIn("is CIT-02, never CIT-07", flat(PROMPTS / "citation-auditor.md"))
        body = " ".join(read(AGENTS / "citation-auditor.md").split())
        self.assertIn("A misstated court position is CIT-02, never CIT-07.", body)


class FindingUnitAndAuthorshipTest(unittest.TestCase):
    """D-247: run 74 verified a compound finding by the quote of one half, and run 79 credited the court with
    words whose speaker the saved text never established."""

    def test_the_researcher_prompt_keeps_one_thought_per_finding_and_withholds_unestablished_authorship(self):
        prompt = flat(PROMPTS / "legal-researcher.md")
        for needle in (
            "One finding is one self-contained, verifiable thought",
            "a condition is never split from its consequence",
            "its authorship is not established",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, prompt)

    def test_the_researcher_body_carries_both_rules(self):
        rules = " ".join(rules_of("legal-researcher").split())
        for needle in ("One finding is one self-contained, verifiable thought", "authorship is not established"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)

    def test_the_writer_never_upgrades_an_unestablished_statement_to_the_court_s(self):
        prompt = flat(PROMPTS / "memo-writer.md")
        self.assertTrue("«суд установил»" in prompt or "«суд исходил»" in prompt)
        self.assertIn("whose position it is does not appear from the text", prompt)
        body = " ".join(read(AGENTS / "memo-writer.md").split())
        self.assertIn("whose authorship the research marks as not established", body)


class MoneyAndRequiredStepsTest(unittest.TestCase):
    """D-240: run 79 counted a loss and its replacement twice and called a prudent step required."""

    def both(self, stem: str) -> tuple[str, str]:
        return flat(PROMPTS / f"{stem}.md"), " ".join(rules_of(stem).split())

    def test_the_writer_treats_money_and_required_steps_as_conclusions(self):
        needle = (
            "Money and required steps are conclusions. A loss and the cost of replacing the same item are one "
            "computation. A figure called reliable excludes the parts the memo rates high-risk, or names them. "
            "A step presented as required before another needs a cited rule; otherwise write it as a prudent step."
        )
        for where, text in zip(("prompt", "body"), self.both("memo-writer")):
            with self.subTest(where=where):
                self.assertIn(needle, text)

    def test_an_understated_exposure_that_changes_what_the_client_pays_is_major(self):
        needle = (
            "An understated exposure that changes what the client pays or must do — a fine, a penalty, a sum "
            "called safe, a step called optional — is `major`, not `minor`."
        )
        for where, text in zip(("prompt", "body"), self.both("counterargument-reviewer")):
            with self.subTest(where=where):
                self.assertIn(needle, text)

    def test_the_mediator_never_drops_a_substantive_major_because_its_reviewer_approved(self):
        # F6: a counterarguments reviewer approves with majors as a matter of course (run 79 v2: four).
        rules = " ".join(rules_of("revision-mediator").split())
        self.assertIn(
            "Minor issues on sections nothing else touches are the usual candidates for `dropped[]`; a substantive "
            "major is never dropped because its reviewer approved.",
            rules,
        )
        self.assertNotIn("Issues from a reviewer that approved", rules)


class SavedTextFirstTest(unittest.TestCase):
    """D-218: run 74 called 12 UK GDPR articles `current` over a saved text that listed changes not yet applied,
    left a judgment `unchecked` although its first paragraph names the statute, and passed pages flagged
    "under review" with no note. One line in the agent body; the dispatch prompt spells it out."""

    def test_the_checker_body_reads_the_saved_text_before_any_lookup(self):
        rules = rules_of("currency-checker")
        for needle in (
            "The saved text first",
            "before any lookup",
            "`raw_path`",
            "not yet applied",
            "opening paragraphs",
            "under review",
            'never as a bare "not reviewed"',
            "`manual_check`, its note naming the amending instrument",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)

    def test_the_checker_body_leaves_unchecked_for_after_the_sources_conclusions_rest_on(self):
        task = read(AGENTS / "currency-checker.md").split("## Task", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`critical`, then `supporting`", task)
        self.assertIn("`unchecked` only after those", task)

    def test_the_checker_prompt_spells_out_each_saved_text_signal(self):
        text = flat(PROMPTS / "currency-checker.md")
        for needle in (
            "## The saved text first",
            "Before any lookup",
            "`raw_path`",
            "changes not yet applied",
            "name the amending instrument",
            "in one lookup what it changes and from when",
            'never a bare "not reviewed"',
            "the statute it was decided under",
            "opening paragraphs",
            '"under review"',
            '"being updated"',
            "`critical`, then `supporting`",
            "`unchecked` only after those",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class LawTimingTest(unittest.TestCase):
    """D-241: run 79 called ст. 13 ЗоЗПП `current` with no note over a saved text announcing 290-ФЗ changes
    from 01.10.2026, and saved a portal's news review as the full text of a Constitutional Court ruling."""

    def both(self, stem: str) -> tuple[str, str]:
        return flat(PROMPTS / f"{stem}.md"), " ".join(rules_of(stem).split())

    def test_the_checker_resolves_an_announced_change_against_the_part_relied_on(self):
        for needle in ("«вступает в силу с", "the part relied on", "in force from"):
            for where, text in zip(("prompt", "body"), self.both("currency-checker")):
                with self.subTest(needle=needle, where=where):
                    self.assertIn(needle, text)
        prompt = flat(PROMPTS / "currency-checker.md")
        self.assertIn(
            "when it does not, the status stays `current` and the `note` names the amending act, the date and "
            "the change; `manual_check` only when that lookup fails or the change governs the facts.",
            prompt,
        )
        self.assertIn("it is never marked as governing earlier facts.", prompt)

    def test_the_checker_receives_the_facts_it_judges_against(self):
        self.assertIn(
            '- the questions and the facts with their dates: `plan.json`, `intake/user-facts.md`; judge "the part '
            'relied on" and "governs the facts" against them',
            read(PROMPTS / "currency-checker.md"),
        )

    def test_the_writer_states_a_later_change_in_the_section_that_relies_on_it(self):
        needle = (
            "A currency note naming a later change to the provision relied on is stated in that section in one "
            "sentence: what changes and from when."
        )
        for where, text in zip(("prompt", "body"), self.both("memo-writer")):
            with self.subTest(where=where):
                self.assertIn(needle, text)

    def test_the_researcher_never_saves_a_review_as_the_act(self):
        needle = (
            "A review of an act is not the act. A portal's news item or «Обзор документа» about a judgment or a "
            "law is saved under its own title as background (`--tier supporting`), never under the act's "
            "citation, and never as the act's full text."
        )
        for where, text in zip(("prompt", "body"), self.both("legal-researcher")):
            with self.subTest(where=where):
                self.assertIn(needle, text)

    def test_readiness_receives_the_currency_notes_and_fails_crd_03_on_an_undisclosed_change(self):
        self.assertIn(
            "- currency notes of the sources the memo cites: ${currency_notes}",
            read(PROMPTS / "client-readiness-reviewer.md"),
        )
        needle = (
            "A note that names a later change to a provision the memo relies on is disclosed in the section that "
            "relies on it; CRD-03 fails otherwise, and the issue names the source."
        )
        for where, text in zip(("prompt", "body"), self.both("client-readiness-reviewer")):
            with self.subTest(where=where):
                self.assertIn(needle, text)


class OneFormPerActTest(unittest.TestCase):
    """D-218: run 74 excluded a case after a guessed citation came back with confidence 0; runs 71 and 74
    left 23 records without `meta.short_name` and wrote `citation_form` in two shapes."""

    CITATIONS = (
        "`UK GDPR, art 82`",
        "`Data Protection Act 2018, s 168`",
        "`42 U.S.C., § 1983`",
        "`BGB, § 823`",
        "`Code du travail, art. L1234-5`",
        "`Федеральный закон от 31.07.2025 № 289-ФЗ, ст. 23`",
    )
    SHORT_NAMES = (
        "`UK GDPR`",
        "`DPA 2018`",
        "`42 U.S.C.`",
        "`BGB`",
        "`ГК РФ`",
        "`289-ФЗ`",
        "`Определение № 66-КГ18-9`",
    )

    def test_the_researcher_body_finds_a_case_by_name_and_names_an_act_one_way(self):
        rules = rules_of("legal-researcher")
        for needle in (
            "parties' names",
            "guessed citation",
            "does not mean the case is absent",
            '"act, unit"',
            "`meta.short_name` is always set",
            "the same for every record of that act",
            "no database or retrieval tag in brackets",
            "content page",
            "table of contents",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)
        # The key the researcher sets itself is no longer among those it drops when a tool omits them.
        self.assertNotIn("`date` and `short_name` for the keys the tool's answer actually gives you", rules)

    def test_the_researcher_prompt_carries_the_forms_of_several_legal_systems(self):
        text = flat(PROMPTS / "legal-researcher.md")
        for needle in (
            "## Find a case by name",
            "parties' names",
            "guessed citation",
            "does not mean the case is absent",
            "## One form per act",
            '"act, unit"',
            "`meta.short_name` is always set",
            "the same for every record of that act",
            "no database or retrieval tag in brackets",
            "content page",
            "table of contents",
            *self.CITATIONS,
            *self.SHORT_NAMES,
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertNotIn('"short_name": …}\'` to either command with the keys the tool', text)

    def test_a_unit_other_than_an_article_is_expected_with_its_label(self):
        """D-219: a bare `--expect-article 22` finds only an article or a section heading, never `Regulation 22`."""
        text = flat(PROMPTS / "legal-researcher.md")
        for needle in ('`--expect-article "reg 22"`', '`"Sch 1"`', '`"Rule 23"`'):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        rules = rules_of("legal-researcher")
        self.assertIn('a regulation, schedule or rule with its label: `"reg 22"`, `"Sch 1"`', rules)


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
