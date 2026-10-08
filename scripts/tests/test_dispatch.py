"""Tests for scripts/memoforge/dispatch.py — prompts, models and `description` (ТЗ §3.3, §4.1, §9)."""

from __future__ import annotations

import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, temp_root  # noqa: E402
from memoforge import (  # noqa: E402
    brief_lint,
    cli,
    dispatch,
    gates,
    i18n,
    machine,
    modes,
    preflight,
    quotes,
    review,
    routing,
    schema,
    sources,
    state_io,
    task,
)

import _i18n  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "prompts"
UPDATE = os.environ.get("MF_UPDATE_GOLDEN") == "1"

TASK_ID = "memo-20260908T120000Z-prompt-golden"

PROBED_NAMESPACES = {
    "ldh": "mcp__ldh",
    "courtlistener": "mcp__courtlistener",
    "fedregs": "mcp__plugin_memoforge_federal-regulations",
    "lex": "mcp__plugin_memoforge_lex",
    "other": [],
}
"""D-110: what the fixture `intake/mcp-probe.json` of `mcp_namespaces` above would have found."""


def _state(work_dir: Path) -> dict:
    state = task.build_initial_state(
        task_id=TASK_ID,
        user_query="How long may the client keep customer records?",
        language="en",
        work_dir=work_dir,
        output_folder=work_dir.parent,
        config=modes.resolve_config("full"),
    )
    state["current_iteration"] = 1
    state["current_draft_path"] = "drafts/v1.md"
    state["current_draft_sha"] = "0" * 64
    return state


def _specs(work_dir: Path, state: dict) -> list[dict]:
    """One spec per pipeline agent, with the extras the planners of `machine.py` pass (§3.3)."""
    config = state["config"]
    layers = list(config["researcher_layers"])
    specs = [
        dispatch.spec(
            "analyst",
            "fact-assumption-analyst",
            "intake",
            [("intake/questions.json", "intake-questions"), ("intake/preliminary-sources.json", "research-findings")],
            max_questions=str(config["intake_max_questions"]),
            mcp_namespaces="ldh, courtlistener, fedregs, lex",
            routing_digest=routing.routing_digest(PROBED_NAMESPACES),
            retry_errors="none",
        )
    ]
    specs.extend(machine.researcher_specs(work_dir, state, layers))
    specs.append(
        dispatch.spec(
            "sufficiency",
            "research-sufficiency-reviewer",
            "sufficiency",
            [("research/research-sufficiency.json", "research-sufficiency")],
            research_files=", ".join(f"`research/{layer}.json`" for layer in layers),
            drafting_warnings="none",
            retry_errors="none",
        )
    )
    specs.append(
        dispatch.spec(
            "currency",
            "currency-checker",
            "currency",
            [("research/currency.json", "currency")],
            verify_report="`research/sources.json` carries `liveness` and `verification` per source",
            sources_list="src-1, src-2",
            mcp_namespaces="ldh, courtlistener",
            retry_errors="none",
        )
    )
    specs.append(
        machine.writer_spec(
            work_dir,
            state,
            task="draft",
            version=1,
            canonical="drafts/v1.md",
            instructions="none - this is the first version",
            seed=False,
        )
    )
    specs.extend(machine.reviewer_specs(work_dir, state, list(config["reviewer_list"]), 1))
    specs.append(
        dispatch.spec(
            "mediator",
            "revision-mediator",
            "mediator v1",
            [("reviews/v1-mediator.json", "mediator")],
            iteration=1,
            draft_path="drafts/v1.md",
            draft_version=1,
            review_files="`reviews/v1-logic.json`",
            issues_path="`state.iterations[]` of this iteration (see `mf state get`)",
            retry_errors="none",
        )
    )
    specs.append(
        dispatch.spec(
            "client_readiness",
            "client-readiness-reviewer",
            "client readiness",
            [("reviews/final-client-readiness.json", "client-readiness")],
            checklist="client-readiness",
            draft_path="drafts/v1.md",
            draft_version=1,
            draft_sha="0" * 64,
            polish_budget=str(config["max_client_polish"]),
            retry_errors="none",
        )
    )
    # D-223: the two agents of `/memoforge:brief`, with the extras the brief driver passes.
    specs.extend(_brief_specs())
    return specs


BRIEF_BLOCKS = (
    "s-header = the title and header lines; s-main = «Bottom line»; s-b1 = «Record retention»; "
    "s-actions = «What to do»"
)
"""`brief_lint.section_list` of a one-block brief — the `block_list` and `section_ids` of the fixture."""


def _brief_specs() -> list[dict]:
    """The writer and the fidelity reviewer of a decision brief, as the brief driver issues them (D-223)."""
    return [
        dispatch.spec(
            "writer",
            "brief-writer",
            "brief v1",
            [("brief/v1.md", None)],
            brief_task="write",
            memo_path="drafts/v1.md",
            memo_sha="0" * 64,
            open_issues_path="brief/open-issues.json",
            user_question="How long may the client keep customer records?",
            brief_template_path=dispatch.lib_path("templates", "decision-brief.md"),
            brief_labels=brief_lint.writer_labels("en"),
            seed_path="none",
            instructions_path="none",
            retry_errors="none",
        ),
        dispatch.spec(
            "fidelity",
            "brief-fidelity-reviewer",
            "brief fidelity r0",
            [("brief/reviews/r0-fidelity.json", "brief-review")],
            memo_path="drafts/v1.md",
            memo_sha="0" * 64,
            brief_path="brief/v1.md",
            brief_sha="1" * 64,
            open_issues_path="brief/open-issues.json",
            user_question="How long may the client keep customer records?",
            block_list=BRIEF_BLOCKS,
            checklist="brief-fidelity",
            retry_errors="none",
        ),
    ]


TOKENS = (
    ("{MF}", lambda work_dir: dispatch.mf_path()),
    ("{AGENT_CORE}", lambda work_dir: dispatch.lib_path("lib", "agent-core")),
    ("{CHECKLISTS}", lambda work_dir: dispatch.lib_path("lib", "checklists")),
    ("{SCHEMAS}", lambda work_dir: dispatch.lib_path("schemas")),
    ("{TEMPLATES}", lambda work_dir: dispatch.lib_path("templates")),
    ("{PROSE_STYLE}", lambda work_dir: dispatch.lib_path("lib", "prose-style.md")),
    ("{WORK_DIR}", lambda work_dir: str(work_dir)),
)


def normalize(text: str, work_dir: Path) -> str:
    """Replace the machine-specific absolute paths with stable tokens (goldens are portable)."""
    for token, resolve in TOKENS:
        text = text.replace(resolve(work_dir), token)
    # Golden files were recorded on Windows; normalize only tokenized path separators.
    for token, _ in TOKENS:
        text = text.replace(token + "\\", token + "/")
    return text


class PromptGoldenTest(unittest.TestCase):
    """§3.3: `substitute()` of every template against a golden file (D-242: one mode, `*.full.md`)."""

    def _render(self) -> dict:
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state_io.write_json_atomic(work_dir / "plan.json", {
            "classification": "regulatory_analysis",
            "jurisdictions": ["EU"],
            "doctrine_required": True,
            "estimated_complexity": "high",
            "issues": [
                {
                    "issue_id": "i1",
                    "title": "Retention of customer records",
                    "question": "How long may the client keep customer records?",
                    "jurisdictions": ["EU"],
                }
            ],
        })
        agents = dispatch.render_agents(
            work_dir,
            state,
            step_id="s-042",
            attempt=1,
            specs=_specs(work_dir, state),
            position=5,
            total=13,
        )
        return {"work_dir": work_dir, "agents": agents}

    def test_prompts_match_the_golden_files(self):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        rendered = self._render()
        for agent in rendered["agents"]:
            name = f"{agent['agent']}.{agent['slot']}.full.md"
            path = GOLDEN / name
            text = normalize(agent["prompt"], rendered["work_dir"])
            if UPDATE:
                path.write_bytes(text.encode("utf-8"))
                continue
            with self.subTest(prompt=name):
                self.assertTrue(path.is_file(), f"missing golden {name}")
                self.assertEqual(normalize(path.read_text(encoding="utf-8-sig"), rendered["work_dir"]), text)

    def test_the_writer_prompt_names_no_executive_brief(self):
        """D-243: a warning for the client goes into the Assumptions block; there is no second template."""
        for text in (
            (dispatch.prompts_dir() / "memo-writer.md").read_text(encoding="utf-8"),
            (GOLDEN / "memo-writer.writer.full.md").read_text(encoding="utf-8-sig"),
        ):
            self.assertNotIn("executive brief", text.lower())
            self.assertIn("goes into the Assumptions block of the facts section as one sentence", text)

    def test_the_analyst_prompt_carries_the_routing_digest(self):
        """D-110: the intake pass is told what to call first for each jurisdiction."""
        rendered = self._render()
        prompt = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "analyst")
        self.assertIn("- tool order by jurisdiction (statutes / case law):", prompt)
        for line in routing.routing_digest(PROBED_NAMESPACES).splitlines():
            self.assertIn(line, prompt)
        digest = prompt.split("(statutes / case law):", 1)[1].split("## Write", 1)[0]
        self.assertIn("ldh_resolve_reference", digest)
        self.assertNotIn("legalviz", digest, "an unconnected server is not offered to the analyst")
        for agent in rendered["agents"]:
            if agent["slot"] != "analyst":
                self.assertNotIn("tool order by jurisdiction", agent["prompt"], agent["slot"])

    def test_the_sufficiency_prompt_names_the_layers_of_the_mode(self):
        """D-112: the reviewer judges coverage against the layers the run researches."""
        rendered = self._render()
        prompt = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "sufficiency")
        self.assertIn("- mode: `full`", prompt)
        self.assertIn("- layers this mode researches: statutes, case_law, doctrine", prompt)
        self.assertIn("out_of_scope_gaps", prompt)
        named = prompt.split("- layers this mode researches: ", 1)[1].splitlines()[0]
        self.assertEqual(list(modes.MODES["full"]["researcher_layers"]), named.split(", "))

    def test_the_currency_prompt_forbids_rewriting_its_declared_input(self):
        """D-114: `mf sources verify` inside the step moved `research/sources.json` under the step."""
        rendered = self._render()
        prompt = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "currency")
        mf = dispatch.mf_path()
        self.assertIn("`research/sources.json` is the declared input of this step", prompt)
        self.assertIn("Do not run", prompt)
        for command in ("sources verify", "sources liveness", "sources register"):
            with self.subTest(command=command):
                self.assertIn(f"`{mf} {command}`", prompt)
        self.assertIn(f"`{mf} agent log`", prompt)
        self.assertIn(f"`{mf} events log`", prompt)
        self.assertNotIn("--set", prompt)

    def test_the_researcher_prompt_carries_the_source_access_of_the_preflight(self):
        """D-147: the researcher is told which portal failed today before it tries it."""
        rendered = self._render()
        for agent in rendered["agents"]:
            line = [row for row in agent["prompt"].splitlines() if "source access today" in row]
            if agent["agent"] != "legal-researcher":
                self.assertEqual([], line, agent["slot"])
                continue
            self.assertEqual(1, len(line), agent["slot"])
            self.assertIn("use the alternative, do not retry", line[0])
            # No `plan.json`/`intake/preflight.json` in this fixture: nothing was measured.
            self.assertTrue(line[0].endswith(preflight.UNKNOWN_LINE), line[0])

    def test_the_source_access_placeholder_always_has_a_value(self):
        """§3.3: `substitute` raises on an unresolved name, so every prompt name has a default."""
        self.assertEqual(preflight.UNKNOWN_LINE, dispatch._DEFAULT_EXTRAS["source_access"])  # noqa: SLF001

    def test_the_default_question_budget_agrees_with_the_limit(self):
        """D-108/D-110: the re-render fallback must not offer five questions where ten are asked."""
        self.assertEqual(dispatch._DEFAULT_EXTRAS["max_questions"], "10")  # noqa: SLF001

    def test_every_pipeline_agent_has_a_prompt(self):
        for agent in dispatch.PIPELINE_AGENTS:
            self.assertTrue(dispatch.prompt_path(agent).is_file(), agent)

    def test_the_brief_agents_are_rendered(self):
        """D-223: exactly two goldens, `brief-writer.writer.full.md` and the fidelity reviewer's."""
        names = {f"{agent['agent']}.{agent['slot']}" for agent in self._render()["agents"]}
        self.assertEqual(
            {"brief-writer.writer", "brief-fidelity-reviewer.fidelity"},
            {name for name in names if name.startswith("brief-")},
        )

    def test_paths_in_prompts_are_absolute(self):
        rendered = self._render()
        for agent in rendered["agents"]:
            for line in agent["prompt"].splitlines():
                for match in re.findall(r"`([^`]+)`", line):
                    if "agent-core" in match or "checklists" in match or "prose-style" in match:
                        candidate = match.split()[0]
                        self.assertTrue(
                            Path(candidate).is_absolute(), f"{agent['agent']}: {candidate}"
                        )
            self.assertIn(dispatch.mf_path(), agent["prompt"])
            self.assertTrue(Path(dispatch.mf_path()).is_absolute())

    def test_researcher_prompt_carries_only_its_own_layer_rule(self):
        rendered = self._render()
        for agent in rendered["agents"]:
            if agent["agent"] != "legal-researcher":
                continue
            layer = agent["slot"]
            line = next(row for row in agent["prompt"].splitlines() if "layer rule (yours only)" in row)
            self.assertIn(layer, line)
            for other in routing.LAYERS:
                if other != layer:
                    self.assertNotIn(other, line, f"{layer} prompt leaked the {other} rule")

    def test_prompt_starts_and_ends_with_agent_log(self):
        rendered = self._render()
        for agent in rendered["agents"]:
            self.assertIn("--state start", agent["prompt"])
            self.assertIn("--state done", agent["prompt"])
            self.assertLess(
                agent["prompt"].index("--state start"), agent["prompt"].index("--state done")
            )

    def test_unresolved_placeholder_raises(self):
        with self.assertRaises(KeyError):
            dispatch.render_prompt("legal-researcher", {"mf": "x"})

    def test_no_placeholder_survives_rendering(self):
        """D-78: `${…}` lives in the template only — a rendered prompt carries none."""
        rendered = self._render()
        for agent in rendered["agents"]:
            with self.subTest(agent=agent["agent"]):
                self.assertNotIn("${", agent["prompt"])

    def test_mediator_names_the_source_pack_and_writer_names_the_quote_limit(self):
        """D-183: the mediator checks a norm against the frozen pack; the writer sizes `--text`."""
        rendered = self._render()
        mediator = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "mediator")
        self.assertIn("research/source-pack.json", mediator)
        writer = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "writer")
        self.assertIn("60 words", writer)  # D-217

    def test_the_two_claim_reviewers_look_up_the_saved_text_within_their_budget(self):
        """D-208: citations spends 20 units, counterarguments 8; logic and form never look anything up."""
        rendered = self._render()
        prompts = {agent["slot"]: agent["prompt"] for agent in rendered["agents"]}
        command = (
            f"`{dispatch.mf_path()} quote locate --workdir {rendered['work_dir']} "
            '--source <id> --text "<phrase>" [--context N]`'
        )
        for slot, budget in (("citations", "20"), ("counterarguments", "8")):
            with self.subTest(slot=slot):
                self.assertIn(command, prompts[slot])
                self.assertIn(f"Lookup budget: {budget} units", prompts[slot])
                self.assertIn("text_checks", prompts[slot])
                self.assertIn("source_evidence", prompts[slot])
        for slot in ("logic", "form"):
            if slot in prompts:
                with self.subTest(slot=slot):
                    self.assertNotIn("quote locate", prompts[slot])
                    self.assertNotIn("Lookup budget", prompts[slot])
        for slot, prompt in prompts.items():
            with self.subTest(removed_phrase=slot):
                self.assertNotIn("must not go beyond", prompt)

    def test_the_two_claim_reviewers_confirm_a_court_s_act_only_by_its_own_sentence(self):
        """D-208, fix round 2: a recited clause never confirms what the court did; the passage is copied whole."""
        rules = (
            "What a court did is confirmed only by the court's own sentence.",
            "is never that evidence, even when the words match and even when the court quotes it approvingly",
            "its suggestion then attributes the words to the offer or the contract, never to the court.",
            "the suggestion may only ask to withdraw the attribution or to qualify it as unresolved.",
            "The passage is copied, not abbreviated.",
            "cut only at its two ends: no ellipses, no joined fragments",
        )
        rendered = self._render()
        prompts = {agent["slot"]: agent["prompt"] for agent in rendered["agents"]}
        for slot in ("citations", "counterarguments"):
            for rule in rules:
                with self.subTest(slot=slot, rule=rule):
                    self.assertIn(rule, " ".join(prompts[slot].split()))
        for slot in ("logic", "form"):
            if slot in prompts:
                with self.subTest(slot=slot):
                    self.assertNotIn("court's own sentence", prompts[slot])

    def test_the_citations_budget_checks_every_cit_01_candidate_first(self):
        """D-208, fix round 3: a statement whose finding lacks the rule is checked before items 2-4."""
        rules = (
            "and every CIT-01 candidate — a statement of law whose paired research finding does not record "
            "the rule the draft states: no finding at all, or a finding about something else.",
            "check each one before items 2–4: read the cited statute article whole (1 unit), or look up the "
            "court's own words.",
        )
        rendered = self._render()
        prompts = {agent["slot"]: " ".join(agent["prompt"].split()) for agent in rendered["agents"]}
        for rule in rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, prompts["citations"])
        with self.subTest(slot="counterarguments"):
            self.assertNotIn("CIT-01 candidate", prompts["counterarguments"])

    def test_the_lookup_budget_of_each_reviewer_and_the_default_of_every_other_agent(self):
        """D-208: `${lookup_budget}` is 20 / 8 / 0 / 0 by reviewer kind, and 0 for every other agent."""
        work_dir = temp_root(self) / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        specs = machine.reviewer_specs(work_dir, state, ["logic", "form", "citations", "counterarguments"], 1)
        self.assertEqual(
            {"logic": "0", "form": "0", "citations": "20", "counterarguments": "8"},
            {spec["slot"]: spec["extra"]["lookup_budget"] for spec in specs},
        )
        self.assertEqual("0", dispatch._DEFAULT_EXTRAS["lookup_budget"])  # noqa: SLF001

    def test_the_readiness_prompt_carries_the_open_findings_and_the_disposition_rules(self):
        """D-211: `${open_findings}` and the rules of the three dispositions; the re-check scope line."""
        prompts = {agent["slot"]: agent["prompt"] for agent in self._render()["agents"]}
        readiness = prompts["client_readiness"]
        self.assertIn("## Open reviewer findings\n\nnone\n", readiness)
        for phrase in (
            "`dispositions`",
            '"action": "polish" | "manual_review" | "leave"',
            "no new statement of law and no new authority",
            "CIT-04",
            "not allow, counts as `manual_review`.",  # D-237: one default, `manual_review`, for every class
            "for information",
        ):
            self.assertIn(phrase, readiness)
        self.assertIn(
            "- polish re-check (none: an ordinary review of the whole draft): none", prompts["citations"]
        )
        self.assertIn("`resolutions`", prompts["citations"])
        self.assertEqual("none", dispatch._DEFAULT_EXTRAS["open_findings"])  # noqa: SLF001
        self.assertEqual("none", dispatch._DEFAULT_EXTRAS["recheck_scope"])  # noqa: SLF001

    def test_the_claim_pairs_make_the_finding_the_key_and_the_text_the_ceiling(self):
        """D-208 rule 1: the removed wording is gone from the spec, and the saved text wins."""
        work_dir = temp_root(self) / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        pairs = machine.reviewer_specs(work_dir, state, ["citations"], 1)[0]["extra"]["claim_pairs"]
        self.assertNotIn("must not go beyond", pairs)
        self.assertIn("pairing key", pairs)
        self.assertIn("the text wins", pairs)
        self.assertIn("`critical`", pairs)

    def test_the_carry_over_follows_the_flagged_statements_and_the_adjacent_limb_rule_is_a_cit_02_blocker(self):
        """D-214: `${carry_over}` right after item 1 of the budget order; a left-out sibling limb is `source_drift`."""
        carry = (
            "The pairs the last review did not reach come next, before items 2–4: the cited statute and case-law "
            "pairs that no earlier citations review checked against the saved text, one per line as "
            "`source_id · section_id · reason` (`not_reached`: that review's budget ran out first; "
            "`never_checked`: no review checked it; `none`: nothing is carried over). none 2. Every statement"
        )
        limb = (
            "A statute rule on a time limit, a threshold or an exception is checked against the whole paragraph "
            "it sits in. A sibling limb of that paragraph that the facts engage and that changes the advice — a "
            "shorter limit, an exception — and that the draft leaves out means the paraphrase does not say what "
            "the source says: CIT-02 grades `false`, and the draft sentence gets a blocker with `checklist_id` "
            "`CIT-02` and `issue_category: source_drift`."
        )
        prompts = {agent["slot"]: " ".join(agent["prompt"].split()) for agent in self._render()["agents"]}
        self.assertIn(carry, prompts["citations"])
        self.assertIn(limb, prompts["citations"])
        self.assertNotIn("did not reach come next", prompts["counterarguments"])
        self.assertNotIn("sibling limb", prompts["counterarguments"])
        self.assertEqual("none", dispatch._DEFAULT_EXTRAS["carry_over"])  # noqa: SLF001

    def test_only_the_citations_spec_carries_the_unchecked_pairs(self):
        """D-214: iteration 2 hands `citations` the pairs v1 did not reach; the other reviewers get `none`."""
        work_dir = temp_root(self) / TASK_ID
        (work_dir / "drafts").mkdir(parents=True, exist_ok=True)
        (work_dir / "reviews").mkdir(parents=True, exist_ok=True)
        (work_dir / "research").mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            work_dir / "research" / "sources.json",
            {"schema_version": 2, "sources": {"art-14": {"layer": "statutes"}, "case-1": {"layer": "case_law"}}},
        )
        (work_dir / "drafts" / "v2.md").write_text(
            "# Memo\n\n## 1. Summary\n\nOne month [[src:art-14]].\n\n## 2. Analysis\n\nHeld [[src:case-1]].\n",
            encoding="utf-8",
        )
        checklist = [
            {"id": row["id"], "pass": True, "evidence": "Checked."} for row in review.load_checklist("citations")
        ]
        document = {
            "reasoning": "All pass.",
            "reviewer": "citations",
            "draft_sha": "1" * 64,
            "iteration": 1,
            "checklist": checklist,
            "issues": [],
            "verdict": "approved",
            "text_checks": [
                {"source_id": "case-1", "section_id": "s-2", "status": "not_reached", "finding_disagrees": False,
                 "note": "Budget spent."}
            ],
        }
        path = state_io.write_json_atomic(work_dir / "reviews" / "v1-citations.json", document)
        state = _state(work_dir)
        state.update(current_iteration=2, current_draft_path="drafts/v2.md")
        state["iterations"] = [{"iteration": 1, "draft_sha": "1" * 64}]
        state["published"] = [{"canonical_path": "reviews/v1-citations.json", "sha256": state_io.sha256_file(path)}]
        specs = machine.reviewer_specs(work_dir, state, ["logic", "form", "citations", "counterarguments"], 2)
        self.assertEqual(
            {
                "logic": "none",
                "form": "none",
                "citations": "case-1 · s-2 · not_reached\nart-14 · s-1 · never_checked",
                "counterarguments": "none",
            },
            {spec["slot"]: spec["extra"]["carry_over"] for spec in specs},
        )
        first = machine.reviewer_specs(work_dir, state, ["citations"], 1)[0]
        self.assertEqual("none", first["extra"]["carry_over"])

    def test_only_the_citations_spec_carries_the_sufficiency_checks(self):
        """D-238: a weak gap's `why_blocking` reaches the citations reviewer; the client-facing `gap` does not."""
        # Run 79, first weak gap: the `gap` is about the unread offer, `why_blocking` about the saved text.
        gap = "Текст оферты Ozon в редакции на август 2026 года получить не удалось."
        why = (
            "Находка по п. 1 ст. 902 ГК РФ опускает оговорку «если законом или договором хранения\n"
            "не предусмотрено иное», хотя сохранённый текст статьи её содержит."
        )
        work_dir = temp_root(self) / TASK_ID
        (work_dir / "research").mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        kinds = ["logic", "form", "citations", "counterarguments"]
        self.assertEqual("none", dispatch._DEFAULT_EXTRAS["sufficiency_checks"])  # noqa: SLF001
        before = machine.reviewer_specs(work_dir, state, ["citations"], 1)[0]
        self.assertEqual("none", before["extra"]["sufficiency_checks"], "no reviewer file, nothing to check")
        document = {
            "reviewer": "research_sufficiency",
            "overall_verdict": "sufficient",
            "blocking_gaps": [
                {"gap": gap, "target": "statutes", "status": "weak", "why_blocking": why, "followup_question": None},
                {"gap": "Практика округов не найдена.", "target": "case_law", "status": "weak",
                 "why_blocking": " ", "followup_question": None},
                {"gap": "Доктрина не найдена.", "target": "doctrine", "status": "missing",
                 "why_blocking": "Слой доктрины пуст.", "followup_question": None},
                # Fix round 1: a user gap says nothing about a saved text, so it spends no lookup.
                {"gap": "Не известно, кто указан продавцом в чеках.", "target": "user", "status": "weak",
                 "why_blocking": "Ответ пользователя не получен.",
                 "followup_question": {
                     "question": "Кто указан продавцом в чеках?",
                     "header": "Продавец",
                     "options": [
                         {"label": "Селлер", "description": "В чеках продавцом указан селлер."},
                         {"label": "Маркетплейс", "description": "В чеках продавцом указан маркетплейс."},
                     ],
                     "default_assumption_if_skipped": "Продавцом в чеках указан селлер.",
                 }},
            ],
            "drafting_warnings": [],
        }
        self.assertEqual([], schema.validate(document, "research-sufficiency"))
        state_io.write_json_atomic(work_dir / "research" / "research-sufficiency.json", document)
        specs = machine.reviewer_specs(work_dir, state, kinds, 1)
        checks = {spec["slot"]: spec["extra"]["sufficiency_checks"] for spec in specs}
        self.assertEqual(
            "statutes · Находка по п. 1 ст. 902 ГК РФ опускает оговорку «если законом или договором хранения "
            "не предусмотрено иное», хотя сохранённый текст статьи её содержит.",
            checks["citations"],
        )
        self.assertNotIn(gap, checks["citations"])
        self.assertNotIn("Ответ пользователя не получен.", checks["citations"])
        self.assertNotIn("user ·", checks["citations"])
        self.assertEqual({"logic": "none", "form": "none", "counterarguments": "none"},
                         {slot: text for slot, text in checks.items() if slot != "citations"})

    def test_every_output_names_its_schema_file(self):
        """D-79: `${outputs}` prints the schema name and the absolute schema path."""
        rendered = self._render()
        for agent in rendered["agents"]:
            for row in agent["expected_outputs"]:
                name = row["schema"]
                if not name:
                    continue
                path = Path(dispatch.lib_path("schemas", f"{name}.schema.json"))
                self.assertTrue(path.is_file(), str(path))
                if "${outputs}" not in dispatch.prompt_path(agent["agent"]).read_text("utf-8-sig"):
                    continue
                with self.subTest(agent=agent["agent"], schema=name):
                    self.assertIn(f"(schema `{name}` — `{path}`)", agent["prompt"])


class BriefPromptTest(unittest.TestCase):
    """D-223: the prompts of `/memoforge:brief` and the two brief variables of the form reviewer."""

    BRIEF_EXTRAS = (
        "brief_role",
        "section_ids",
        "memo_path",
        "memo_sha",
        "brief_path",
        "brief_sha",
        "open_issues_path",
        "user_question",
        "brief_template_path",
        "brief_labels",
        "block_list",
    )

    def _render(self, specs: list[dict]) -> list[dict]:
        work_dir = temp_root(self) / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        return dispatch.render_agents(
            work_dir, state, step_id="b-1a2b3c4d-001", attempt=1, specs=specs, position=0, total=0
        )

    def test_the_brief_extras_default_to_empty_and_the_task_to_write(self):
        for key in self.BRIEF_EXTRAS:
            with self.subTest(key=key):
                self.assertEqual("", dispatch._DEFAULT_EXTRAS[key])  # noqa: SLF001
        self.assertEqual("write", dispatch._DEFAULT_EXTRAS["brief_task"])  # noqa: SLF001

    def test_the_two_brief_agents_are_registered(self):
        for agent in ("brief-writer", "brief-fidelity-reviewer"):
            with self.subTest(agent=agent):
                self.assertEqual({"model": "opus", "effort": "high"}, dispatch.AGENT_MODELS[agent])
        self.assertEqual(("brief-writer", "brief-fidelity-reviewer"), dispatch.PIPELINE_AGENTS[-2:])

    def test_the_writer_prompt_carries_the_memo_the_open_issues_and_the_labels(self):
        writer = self._render(_brief_specs()[:1])[0]
        prompt = writer["prompt"]
        self.assertEqual("opus", writer["model"])
        self.assertIn("`drafts/v1.md`", prompt)
        self.assertIn("`brief/open-issues.json`", prompt)
        self.assertIn(dispatch.lib_path("templates", "decision-brief.md"), prompt)
        self.assertIn(brief_lint.writer_labels("en"), prompt)
        self.assertIn("steps/b-1a2b3c4d-001/a1/writer/v1.md", prompt)
        self.assertNotIn("${", prompt)

    def test_the_fidelity_prompt_carries_the_sha_to_copy_the_blocks_and_the_checklist(self):
        prompt = self._render(_brief_specs()[1:])[0]["prompt"]
        self.assertIn("1" * 64, prompt)
        self.assertIn(BRIEF_BLOCKS, prompt)
        self.assertIn(dispatch.lib_path("lib", "checklists", "brief-fidelity.json"), prompt)
        self.assertIn(dispatch.lib_path("schemas", "brief-review.schema.json"), prompt)
        self.assertNotIn("${", prompt)

    def test_the_form_prompt_takes_the_brief_role_and_its_block_ids(self):
        section_ids = (
            " Section ids for this draft (use exactly these as section_id): " + BRIEF_BLOCKS
            + "; use document only for the whole brief."
        )
        brief_role = (
            " This draft is a decision brief for a non-lawyer: grade it only against the checklist given, and "
            "every issue names the checklist_id of the item it fails."
        )
        prose_style = dispatch.lib_path("lib", "prose-style.md")
        spec = dispatch.spec(
            "form",
            "form-reviewer",
            "brief form r0",
            [("brief/reviews/r0-form.json", "review")],
            checklist="brief-form",
            draft_path="brief/v1.md",
            draft_sha="1" * 64,
            draft_version=1,
            iteration=1,
            lint_attachment="",
            prose_style_path=prose_style,
            brief_role=brief_role,
            section_ids=section_ids,
            retry_errors="none",
        )
        lines = self._render([spec])[0]["prompt"].splitlines()
        checklist = dispatch.lib_path("lib", "checklists", "brief-form.json")
        self.assertIn(f"- checklist (grade every id, no additions, no omissions): `{checklist}`{section_ids}", lines)
        self.assertIn(f"Every issue carries `lens`: `clarity` or `style`.{brief_role}", lines)
        self.assertIn(f"- style profile (authoritative when set): `{prose_style}`", lines)

    def test_the_memo_form_prompt_ends_both_lines_where_it_did(self):
        """With both brief variables empty the memo prompt is byte-identical (the golden proves the rest)."""
        work_dir = temp_root(self) / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        spec = machine.reviewer_specs(work_dir, state, ["form"], 1)[0]
        lines = self._render([spec])[0]["prompt"].splitlines()
        self.assertIn("Every issue carries `lens`: `clarity` or `style`.", lines)
        checklist = next(line for line in lines if line.startswith("- checklist (grade every id"))
        self.assertTrue(checklist.endswith("form.json`"), checklist)


class DraftingWarningsBlockTest(unittest.TestCase):
    """D34-17: the writer reads warnings as a list ordered by severity, not as one joined string."""

    WARNINGS = [
        {"code": "currency_unchecked", "message": "source currency could not be verified"},
        {"code": "sufficiency_warning", "message": "Doctrine coverage is thin for the balancing test."},
        {
            "code": "unresolved_research_gap",
            "issue_id": "i1",
            "message": "No national Article 88 provision was reviewed.",
        },
        "L-08 s-2: no quote for gdpr-art6",
    ]

    def _writer_prompt(self, warnings: list) -> str:
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state["drafting_warnings"] = list(warnings)
        agents = dispatch.render_agents(
            work_dir,
            state,
            step_id="s-017",
            attempt=1,
            specs=[
                machine.writer_spec(
                    work_dir,
                    state,
                    task="draft",
                    version=1,
                    canonical="drafts/v1.md",
                    instructions="none",
                    seed=False,
                )
            ],
            position=9,
            total=12,
        )
        return agents[0]["prompt"]

    def _block(self, prompt: str) -> list[str]:
        return prompt.split("research gaps first:\n\n", 1)[1].split("\n\nA warning", 1)[0].splitlines()

    def test_one_line_per_warning_with_its_code_and_issue(self):
        self.assertEqual(
            [
                "- [unresolved_research_gap] (i1) No national Article 88 provision was reviewed.",
                "- [sufficiency_warning] (general) Doctrine coverage is thin for the balancing test.",
                "- [currency_unchecked] (general) source currency could not be verified",
                "- [note] (general) L-08 s-2: no quote for gdpr-art6",
            ],
            self._block(self._writer_prompt(self.WARNINGS)),
        )

    def test_the_joined_string_never_reaches_the_writer(self):
        # The run of the audit gave the writer 9 000 characters on one line, joined with `; `.
        prompt = self._writer_prompt(self.WARNINGS)
        self.assertNotIn("verified; Doctrine", prompt)

    def test_a_run_without_warnings_still_prints_a_list(self):
        self.assertEqual(dispatch.NO_WARNINGS, dispatch.warning_lines([]))
        self.assertEqual([dispatch.NO_WARNINGS], self._block(self._writer_prompt([])))

    def test_the_writer_is_told_which_warnings_it_executes(self):
        prompt = self._writer_prompt(self.WARNINGS)
        self.assertIn("execute it, do not quote it", prompt)
        self.assertIn("the Assumptions block of the facts section", prompt)

    def test_an_unknown_code_sorts_after_every_known_one(self):
        lines = dispatch.warning_lines(
            [{"code": "brand_new", "message": "later"}, {"code": "currency_unchecked", "message": "first"}]
        ).splitlines()
        self.assertEqual(
            ["- [currency_unchecked] (general) first", "- [brand_new] (general) later"], lines
        )


class SpecStoreEncodingTest(unittest.TestCase):
    """D34-19: the spec store and the prompts are utf-8, whatever the host console codepage is."""

    DASH = "none — this is the first version"

    def test_the_spec_store_is_utf8_under_a_cp1251_locale(self):
        import locale
        from unittest import mock

        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        specs = [
            dispatch.spec(
                "writer",
                "memo-writer",
                "draft v1",
                [("drafts/v1.md", None)],
                writer_task="draft",
                draft_version=1,
                draft_path="drafts/v1.md",
                seed_path=self.DASH,
                instructions_path=self.DASH,
                inputs_list="`plan.json`",
                retry_errors="none",
            )
        ]
        env = dict(os.environ)
        os.environ["PYTHONIOENCODING"] = "cp1251"
        try:
            with mock.patch.object(locale, "getpreferredencoding", lambda *a, **k: "cp1251"):
                agents = dispatch.render_agents(
                    work_dir, state, step_id="s-017", attempt=1, specs=specs, position=9, total=12
                )
        finally:
            os.environ.clear()
            os.environ.update(env)

        path = dispatch.spec_store_path(work_dir, "s-017", 1)
        text = path.read_bytes().decode("utf-8")
        self.assertNotIn("�", text)
        self.assertNotIn("?", text)
        self.assertEqual(self.DASH, state_io.read_json(path)["writer"]["extra"]["seed_path"])
        self.assertNotIn("�", agents[0]["prompt"])
        self.assertIn(self.DASH, agents[0]["prompt"])
        self.assertIn("·", agents[0]["prompt"], "the template itself is read as utf-8")


class DescriptionTest(unittest.TestCase):
    """§7.2 A: `P<n>/<N> · <agent> · <label>`, with N from the route."""

    def test_description_format(self):
        self.assertEqual(
            "P5/13 · legal-researcher · statutes",
            dispatch.description(5, 13, "legal-researcher", "statutes"),
        )

    def test_denominator_follows_the_route(self):
        for user_config, total in (({}, 13), ({"source_review_gate": "off"}, 12)):
            driver = Driver(temp_root(self), slug=f"denom-{total}", user_config=user_config)
            action = driver.run_until("research")
            for agent in action["agents"]:
                self.assertTrue(agent["description"].startswith(f"P5/{total} · "))

    def test_mcp_spent_counts_calls_per_server(self):
        # D-166: the quota servers carry `of <limit>`, the free ones a bare total.
        state = {"progress": {"mcp_calls": {
            "ldh": 7, "courtlistener": 1, "legalviz": 54, "justicelibre": 19, "fedregs": 0,
        }}}
        self.assertEqual(
            "ldh 7 of 10 (daily quota), courtlistener 1 of 125 (daily quota); "
            "legalviz 54, justicelibre 19, fedregs 0 (no quota, soft cap 100 per run)",
            dispatch.mcp_spent(state),
        )

    def test_mcp_spent_is_none_yet_when_nothing_was_called(self):
        self.assertEqual("none yet", dispatch.mcp_spent({"progress": {"mcp_calls": {}}}))
        self.assertEqual("none yet", dispatch.mcp_spent({}))

    def test_build_context_carries_mcp_spent_and_no_budget_share(self):
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state["progress"]["mcp_calls"] = {"ldh": 7, "legalviz": 54}
        context = dispatch.build_context(
            work_dir,
            state,
            step_id="s-099",
            attempt=1,
            slot="statutes",
            agent="legal-researcher",
            outputs=[],
            extra={"layer": "statutes"},
        )
        self.assertEqual(
            "ldh 7 of 10 (daily quota); legalviz 54 (no quota, soft cap 100 per run)",
            context["mcp_spent"],
        )
        self.assertNotIn("mcp_budget_share", context)


class ModelsTest(unittest.TestCase):
    """§4.1: `AGENT_MODELS` and `lib/models.md` are one table (M11)."""

    def test_agent_models_match_lib_models_md(self):
        text = (PLUGIN_ROOT / "lib" / "models.md").read_text(encoding="utf-8-sig")
        rows: dict[str, dict] = {}
        for line in text.splitlines():
            match = re.match(r"^\|\s*`([a-z-]+)`\s*\|\s*`([a-z._]+)`\s*\|\s*`([a-z]+)`\s*\|", line)
            if match:
                rows[match.group(1)] = {"model": match.group(2), "effort": match.group(3)}
        self.assertTrue(rows, "no agent rows parsed from lib/models.md")
        self.assertEqual(set(dispatch.AGENT_MODELS), set(rows))
        for agent, expected in sorted(rows.items()):
            self.assertEqual(expected, dispatch.AGENT_MODELS[agent], agent)

    def test_every_research_layer_runs_on_the_agent_row(self):
        """D-245: the researcher's row is `opus`, so the D-209 per-layer override is gone with its table."""
        self.assertEqual({"model": "opus", "effort": "high"}, dispatch.AGENT_MODELS["legal-researcher"])
        self.assertFalse(hasattr(dispatch, "RESEARCH_LAYER_MODELS"))
        text = (PLUGIN_ROOT / "lib" / "models.md").read_text(encoding="utf-8-sig")
        row = next(line for line in text.splitlines() if line.startswith("| `legal-researcher` |"))
        self.assertNotIn("RESEARCH_LAYER_MODELS", row)

    def test_no_pipeline_agent_runs_on_sonnet(self):
        """D-245: every agent that judges substance runs on `opus`; `config.writer_model` may still pick `sonnet`."""
        for agent in dispatch.PIPELINE_AGENTS:
            with self.subTest(agent=agent):
                self.assertNotEqual("sonnet", dispatch.AGENT_MODELS[agent]["model"])

    def test_writer_model_comes_from_config(self):
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state["config"]["writer_model"] = "fable"
        agents = dispatch.render_agents(
            work_dir,
            state,
            step_id="s-001",
            attempt=1,
            specs=[
                machine.writer_spec(
                    work_dir,
                    state,
                    task="draft",
                    version=1,
                    canonical="drafts/v1.md",
                    instructions="none",
                    seed=False,
                )
            ],
            position=9,
            total=13,
        )
        self.assertEqual("fable", agents[0]["model"])

    def test_the_dispatch_payload_carries_no_effort(self):
        """D-75: `Agent` does not take `effort`; the agent's frontmatter is the only channel."""
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        agents = dispatch.render_agents(
            work_dir,
            state,
            step_id="s-002",
            attempt=1,
            specs=[
                machine.writer_spec(
                    work_dir,
                    state,
                    task="revision",
                    version=2,
                    canonical="drafts/v2.md",
                    instructions="none",
                    seed=True,
                )
            ],
            position=9,
            total=13,
        )
        self.assertNotIn("effort", agents[0])


class DispatchRenderCommandTest(unittest.TestCase):
    """§5.2 `mf dispatch render`: the prompt of an issued step can be rebuilt from `steps[]`."""

    def test_render_rebuilds_the_issued_prompt(self):
        driver = Driver(temp_root(self), slug="render")
        action = driver.run_until("research")
        import argparse

        result = dispatch.run_render(
            argparse.Namespace(
                workdir=str(driver.work_dir),
                step=action["step_id"],
                attempt=action["attempt"],
                slot="statutes",
                human=False,
            )
        )
        self.assertEqual(1, len(result["agents"]))
        self.assertEqual("statutes", result["agents"][0]["slot"])
        self.assertIn("layer rule (yours only)", result["agents"][0]["prompt"])

    def test_render_reproduces_the_issued_prompt_byte_for_byte(self):
        """D-55: re-rendering an issued slot returns exactly the prompt the agent was given."""
        driver = Driver(temp_root(self), slug="render-exact")
        action = driver.run_until("research")
        import argparse

        for issued in action["agents"]:
            result = dispatch.run_render(
                argparse.Namespace(
                    workdir=str(driver.work_dir),
                    step=action["step_id"],
                    attempt=action["attempt"],
                    slot=issued["slot"],
                    human=False,
                )
            )
            with self.subTest(slot=issued["slot"]):
                self.assertEqual(issued["prompt"], result["agents"][0]["prompt"])


class RetrySpecTest(unittest.TestCase):
    """D-55 / находка 2: a retry restores the **full** original spec, not a guessed one."""

    def _step_row(self, step_id: str, attempt: int, agents: list[dict]) -> dict:
        return {
            "step_id": step_id,
            "attempt": attempt,
            "kind": "dispatch",
            "agents": [
                {
                    "slot": agent["slot"],
                    "agent_type": agent["subagent_type"],
                    "attempt": attempt,
                    "status": "fail",
                    "outputs": [
                        {
                            "canonical_path": entry["canonical"],
                            "work_path": entry["work_path"],
                            "schema": entry["schema"],
                        }
                        for entry in agent["expected_outputs"]
                    ],
                }
                for agent in agents
            ],
        }

    def _round_trip(self, state: dict, work_dir: Path, step_id: str, specs: list[dict]) -> tuple:
        issued = dispatch.render_agents(
            work_dir, state, step_id=step_id, attempt=1, specs=specs, position=9, total=13
        )
        row = self._step_row(step_id, 1, issued)
        restored = [
            dispatch._spec_from_step(work_dir, state, row, agent_row)  # noqa: SLF001
            for agent_row in row["agents"]
        ]
        again = dispatch.render_agents(
            work_dir, state, step_id=step_id, attempt=1, specs=restored, position=9, total=13
        )
        return issued, restored, again

    def _work_dir(self) -> tuple[Path, dict]:
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state_io.write_json_atomic(work_dir / "plan.json", {
            "classification": "regulatory_analysis",
            "jurisdictions": ["EU"],
            "doctrine_required": True,
            "estimated_complexity": "high",
            "issues": [
                {
                    "issue_id": "i1",
                    "title": "Retention of customer records",
                    "question": "How long may the client keep customer records?",
                    "jurisdictions": ["EU"],
                }
            ],
        })
        return work_dir, state

    def test_writer_fix_retry_keeps_the_task_and_the_instructions(self):
        work_dir, state = self._work_dir()
        state["current_iteration"] = 2
        original = machine.writer_spec(
            work_dir,
            state,
            task="lint-fix",
            version=2,
            canonical="drafts/v2.md",
            instructions="lint.json — fix L-01 and L-03",
            seed=True,
        )
        issued, restored, again = self._round_trip(state, work_dir, "s-070", [original])
        self.assertEqual("lint-fix", restored[0]["extra"]["writer_task"])
        self.assertEqual(original["extra"], restored[0]["extra"])
        self.assertEqual(original["inputs"], restored[0]["inputs"])
        self.assertEqual(original["effort"], restored[0]["effort"])
        self.assertEqual(issued[0]["prompt"], again[0]["prompt"])

    def test_researcher_retry_keeps_the_followup_parameters(self):
        work_dir, state = self._work_dir()
        state["sufficiency_followup"] = {
            "status": "answered",
            "user_response": "only the German entity, 2019 onwards",
        }
        originals = machine.researcher_specs(work_dir, state, ["statutes", "case_law"])
        originals[1]["model"] = "fable"
        issued, restored, again = self._round_trip(state, work_dir, "s-071", originals)
        for index, original in enumerate(originals):
            with self.subTest(slot=original["slot"]):
                self.assertEqual(
                    "only the German entity, 2019 onwards",
                    restored[index]["extra"]["followup_prompts"],
                )
                self.assertEqual(original["extra"], restored[index]["extra"])
                self.assertEqual(original["inputs"], restored[index]["inputs"])
                self.assertEqual(issued[index]["prompt"], again[index]["prompt"])
        # D-55: the stored spec carries a slot's own model, so a retry keeps it; the other slot takes the
        # agent's row, `opus` for every layer (D-245).
        self.assertEqual(["opus", "fable"], [agent["model"] for agent in again])

    def test_only_identity_paths_and_errors_change_on_the_next_attempt(self):
        work_dir, state = self._work_dir()
        originals = machine.researcher_specs(work_dir, state, ["statutes"])
        issued = dispatch.render_agents(
            work_dir, state, step_id="s-072", attempt=1, specs=originals, position=9, total=13
        )
        row = self._step_row("s-072", 1, issued)
        restored = dispatch._spec_from_step(work_dir, state, row, row["agents"][0])  # noqa: SLF001
        restored["extra"]["retry_errors"] = "schema_invalid: missing `_meta`"
        retried = dispatch.render_agents(
            work_dir, state, step_id="s-072", attempt=2, specs=[restored], position=9, total=13
        )
        self.assertEqual(
            "steps/s-072/a2/statutes/statutes.json", retried[0]["expected_outputs"][0]["work_path"]
        )
        self.assertIn("schema_invalid: missing `_meta`", retried[0]["prompt"])
        expected = (
            issued[0]["prompt"]
            .replace("/a1/", "/a2/")
            .replace("--attempt 1", "--attempt 2")
            .replace('"attempt": 1', '"attempt": 2')
            .replace(
                "previous attempt errors to fix: none",
                "previous attempt errors to fix: schema_invalid: missing `_meta`",
            )
        )
        self.assertEqual(expected, retried[0]["prompt"])

    def test_followup_researcher_is_told_the_gap_and_its_earlier_findings(self):
        # A43-2 / D-154
        work_dir, state = self._work_dir()
        (work_dir / "research").mkdir(exist_ok=True)
        (work_dir / "research" / "case_law.json").write_text('{"layer": "case_law", "issues": []}', encoding="utf-8")
        state["sufficiency_followup"] = {
            "status": "research_subset",
            "approved_layers": ["case_law"],
            "user_response": None,
            "subset_r": [
                {"gap": "No CJEU authority on joint controllership for the chat feature", "target": "case_law",
                 "status": "missing", "why_blocking": "Issue 2 rests on it."},
                {"gap": "German DPA guidance thin", "target": "doctrine", "status": "weak"},
            ],
        }
        specs = machine.researcher_specs(work_dir, state, ["case_law"])
        extra = specs[0]["extra"]
        self.assertIn("No CJEU authority on joint controllership", extra["followup_gaps"])
        self.assertIn("Issue 2 rests on it.", extra["followup_gaps"])
        self.assertNotIn("German DPA", extra["followup_gaps"], "weak gaps and other layers stay out")
        self.assertIn("research/case_law.json", extra["previous_findings"])
        self.assertEqual("none", extra["followup_prompts"])
        rendered = dispatch.render_agents(
            work_dir, state, step_id="s-090", attempt=1, specs=specs, position=5, total=13
        )
        self.assertIn("No CJEU authority on joint controllership", rendered[0]["prompt"])
        self.assertIn("research/case_law.json", rendered[0]["prompt"])

    def test_first_pass_researcher_has_no_gaps_and_no_earlier_findings(self):
        work_dir, state = self._work_dir()
        specs = machine.researcher_specs(work_dir, state, ["statutes"])
        self.assertEqual("none", specs[0]["extra"]["followup_gaps"])
        self.assertEqual("none - first pass of this layer", specs[0]["extra"]["previous_findings"])

    def test_citation_auditor_is_given_the_research_files(self):
        # A43-3 / D-155
        work_dir, state = self._work_dir()
        state["dispatched_researchers"] = ["statutes", "case_law"]
        state["current_draft_path"] = "drafts/v1.md"
        specs = machine.reviewer_specs(work_dir, state, ["citations"], 1)
        rendered = dispatch.render_agents(
            work_dir, state, step_id="s-091", attempt=1, specs=specs, position=8, total=13
        )
        prompt = rendered[0]["prompt"]
        self.assertIn("`research/statutes.json`, `research/case_law.json`", prompt)
        self.assertNotIn("lists every token and its source", prompt)
        self.assertIn("proposition", prompt)


class RoutingParameterTest(unittest.TestCase):
    """D-187a: `${routing}` hands over every routed row whole — tools, LDH corpora and the note."""

    def _work_dir(self, *codes: str) -> tuple[Path, dict]:
        root = temp_root(self)
        work_dir = root / TASK_ID
        work_dir.mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state_io.write_json_atomic(work_dir / "plan.json", {
            "classification": "regulatory_analysis",
            "jurisdictions": list(codes),
            "doctrine_required": True,
            "estimated_complexity": "high",
            "issues": [
                {
                    "issue_id": "i1",
                    "title": "Protection of business reputation",
                    "question": "What must the client take down?",
                    "jurisdictions": list(codes),
                }
            ],
        })
        return work_dir, state

    def _routing(self, layer: str, *codes: str) -> str:
        work_dir, state = self._work_dir(*codes)
        return machine.researcher_specs(work_dir, state, [layer])[0]["extra"]["routing"]

    def test_the_russian_statute_row_reaches_the_researcher_whole(self):
        text = self._routing("statutes", "RU")
        self.assertIn("RU: ldh_search > WebFetch", text)
        self.assertIn("(domains: www.consultant.ru, base.garant.ru)", text)
        self.assertIn("LDH sources: RU/PravoGovRu", text)
        self.assertIn("п. 2 ст. 152", text)

    def test_the_russian_case_law_row_names_the_corpus_and_the_fas_restriction(self):
        text = self._routing("case_law", "RU")
        self.assertIn("LDH sources: RU/Sudact", text)
        self.assertIn("fas_search_fas_cases", text)
        self.assertIn("search_fas_cases (semantic, filters year/region/article)", text)
        self.assertIn("kad.arbitr.ru is captcha-gated", text)

    def test_the_row_note_survives_into_the_rendered_prompt(self):
        work_dir, state = self._work_dir("RU")
        specs = machine.researcher_specs(work_dir, state, ["case_law"])
        prompt = dispatch.render_agents(
            work_dir, state, step_id="s-092", attempt=1, specs=specs, position=5, total=13
        )[0]["prompt"]
        self.assertIn("RU/Sudact", prompt)
        self.assertIn("fas_search_fas_cases", prompt)
        self.assertNotIn("${routing}", prompt)

    def test_every_jurisdiction_keeps_its_tool_line_and_gains_its_own_note(self):
        text = self._routing("statutes", "EU", "UK", "US")
        for code in ("EU", "UK", "US"):
            row = routing.route("statutes", code)
            head = f"{code}: {' > '.join(row['tools'])} (domains: {', '.join(row['domains'])})"
            with self.subTest(jurisdiction=code):
                self.assertIn(head, text)
                self.assertIn(row["note"], text)
        self.assertIn("LDH sources: EU/EUR-Lex, EU/ConsolidatedLegislation", text)
        self.assertIn("LDH sources: UK/Legislation", text)

    def test_the_uk_statute_row_shows_the_act_unit_citation_and_the_short_name(self):
        """D-218: the UK example of `save` reaches the researcher inside the UK statutes line."""
        text = self._routing("statutes", "EU", "UK", "US")
        block = text.split("  - UK: ", 1)[1].split("\n  - ", 1)[0]
        self.assertIn('--citation "UK GDPR, art 82"', block)
        self.assertIn("""--meta '{"short_name": "UK GDPR"}'""", block)

    def test_a_row_without_corpora_prints_no_ldh_sources_line(self):
        text = self._routing("case_law", "US")
        self.assertIn("US: courtlistener_search", text)
        self.assertNotIn("LDH sources:", text)


class CurrencyUnresolvedChangeTest(unittest.TestCase):
    """D-218, fix round 1: a listed change whose effect the checker could not resolve is `manual_check`."""

    def test_the_rendered_currency_prompt_names_the_status_of_an_unresolved_change(self):
        rendered = PromptGoldenTest._render(self)
        prompt = next(a["prompt"] for a in rendered["agents"] if a["agent"] == "currency-checker")
        text = " ".join(prompt.split())
        self.assertIn("If that lookup fails, the status is `manual_check`", text)
        self.assertIn("names the amending instrument and says its effect is unresolved", text)
        self.assertNotIn("the source is not `current` on that text", text)


class ResearcherSaveRuleTest(unittest.TestCase):
    """D-205: the researcher stops being the glue between fetching a page and registering it.

    A web source is saved by `mf sources save`; `register --raw-file` keeps exactly three cases; and
    every answer `save` can give is mapped to one next move — the answers Tasks 6 and 7 kept apart
    are worth nothing unless the agent that reads them acts differently on each.
    """

    def _researchers(self) -> list[str]:
        """Every researcher prompt of the run, normalised exactly as the goldens are."""
        prompts = []
        rendered = PromptGoldenTest._render(self)
        for agent in rendered["agents"]:
            if agent["agent"] == "legal-researcher":
                prompts.append(normalize(agent["prompt"], rendered["work_dir"]))
        self.assertEqual(3, len(prompts), "statutes, case_law, doctrine")
        return prompts

    @staticmethod
    def _examples(prompt: str, command: str) -> list:
        """Every `{MF} sources <command> …` line of a prompt, split as a shell would and parsed."""
        parsed = []
        for printed in re.findall(r"`(\{MF\} sources " + command + r" [^`]+)`", prompt):
            tokens = shlex.split(printed.replace("{MF}", "mf", 1))
            try:
                parsed.append(cli.build_parser().parse_args(tokens[1:]))
            except SystemExit:  # argparse reports a bad command line by exiting
                raise AssertionError(f"the prompt shows a command the CLI refuses: {printed}") from None
        return parsed

    def test_a_web_source_is_saved_and_register_keeps_exactly_three_cases(self):
        for prompt in self._researchers():
            self.assertIn("A web source is saved by code", prompt)
            self.assertIn("exactly three cases", prompt)
            for kind in ("excerpt", "client_file", "agent_summary"):
                self.assertIn(f"`--raw-kind {kind}`", prompt)
            self.assertIn("`host_not_allowed`", prompt)
            self.assertIn("raised to the full text by running `save` over the same record", prompt)
            # The older, longer rule is gone, not softened: an agent that sees both follows it.
            self.assertNotIn("--tier <critical|supporting> --raw-file", prompt)
            self.assertNotIn('register that file with `--tool "mf-fetch <host>"`', prompt)
            self.assertNotIn("register it with `--tool WebFetch <domain>`", prompt)

    def test_a_post_save_names_the_public_page_of_its_document(self):
        """Final review A: a POST endpoint answers many documents at one address and names none, so
        the prompt says that such a save takes `--public-url` — for Normattiva, the resolver page of
        the URN the body carries — and that the save refuses without it."""
        for prompt in self._researchers():
            self.assertIn("`--public-url", prompt)
            self.assertIn("`https://www.normattiva.it/uri-res/N2Ls?`", prompt)
            self.assertIn("refused without it", prompt)

    def test_every_save_and_register_example_is_a_command_the_cli_accepts(self):
        """Addendum §6: an example that does not parse is worse than none."""
        for prompt in self._researchers():
            saves = self._examples(prompt, "save")
            registers = self._examples(prompt, "register")
            self.assertTrue(saves)
            self.assertTrue(registers)
            for args in saves:
                self.assertIs(sources.run_save, args.func)
                self.assertEqual("critical", args.tier)
            for args in registers:
                self.assertIs(sources.run_register, args.func)
                self.assertIn(args.raw_kind, sources.AGENT_RAW_KINDS)
                self.assertTrue(args.raw_file)
            printed = re.findall(r"`\{MF\} sources register [^`]+`", prompt)
            self.assertTrue(all("--raw-kind " in line for line in printed), "the default kind is never implied")

    def test_every_answer_of_save_is_mapped_to_one_next_move(self):
        # The spellings are the code's own, not the prompt's paraphrase of them.
        for code in ("requisites_mismatch", "requisites_ambiguous", "channel_unavailable", "channel_budget_spent"):
            self.assertIn(code, sources.RESOLVE_HINTS)
        self.assertIn("host_not_allowed", sources.SAVE_HINTS)
        answers = {
            "`full_text`": "cite it",
            "`excerpt:<reason>`": "cite it as an excerpt",
            "`requisites_mismatch`": "look at the number and the date again",
            "`requisites_ambiguous`": "choose one yourself and save it with `--url`",
            "`channel_unavailable: <reason>`": "go to the fallbacks",
            "`channel_budget_spent`": "go to the fallbacks",
            f"`channel_unavailable: {sources.CHANNEL_CAPTCHA}`": "has no exceptions",
            "`host_not_allowed`": "`--raw-kind agent_summary`",
        }
        for prompt in self._researchers():
            block = prompt.split("act on its answer", 1)[1].split("\n\n", 1)[0]
            for answer, move in answers.items():
                with self.subTest(answer=answer):
                    line = next((row for row in block.splitlines() if answer in row), "")
                    self.assertIn(move, line)
            self.assertIn("do not switch channels", block)
            self.assertIn("do not retry the same address", block)

    def test_a_captcha_is_never_retried_and_closes_sudact_for_the_run(self):
        """Addendum §1 and §9: LDH `RU/Sudact` answers with sudact.ru addresses — the obvious way back in."""
        for prompt in self._researchers():
            line = next(row for row in prompt.splitlines() if "`channel_unavailable: captcha`" in row)
            for words in (
                "never try the channel again in this run",
                "never try to get round the page",
                "never ask the user to solve it",
                "no exceptions",
                "do not pass an address on sudact.ru",
                "`--raw-kind excerpt`",
                "another allowed host",
            ):
                with self.subTest(words=words):
                    self.assertIn(words, line)

    def test_a_spent_budget_closes_sudact_for_every_command(self):
        """Final review C: the budget is the host's, so `save --url` on sudact.ru counts against it too —
        after `channel_budget_spent` an address there is refused like one after a captcha."""
        for prompt in self._researchers():
            line = next(row for row in prompt.splitlines() if row.startswith("- `channel_budget_spent`"))
            for words in ("do not pass an address on sudact.ru", "`--raw-kind excerpt`", "another allowed host"):
                with self.subTest(words=words):
                    self.assertIn(words, line)

    def test_the_number_goes_in_whole_and_the_date_is_the_act_s_own(self):
        """Addendum §2: a researcher who strips the suffix would certify the wrong twin."""
        for prompt in self._researchers():
            self.assertIn("suffix included (`305-ЭС24-8702 (1,3)`)", prompt)
            self.assertIn("never strip it", prompt)
            self.assertIn("never the date a portal's listing shows", prompt)

    def test_the_sufficiency_reviewer_reads_raw_kind_and_the_save_outcome(self):
        """D-205: a critical source not saved whole goes back for `save` — unless a save was tried."""
        rendered = PromptGoldenTest._render(self)
        prompt = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "sufficiency")
        for words in (
            "`raw_kind`",
            "neither `full_text` nor `client_file`",
            "`mf sources save`",
            "no `meta.save_outcome`",
            "`refused:",
            "`excerpt:",
            "is not sent back",
            "`raw_original_path`",
            "not checked by code",
        ):
            with self.subTest(words=words):
                self.assertIn(words, prompt)
        self.assertNotIn("A `critical` source with no saved raw text is a `missing` gap", prompt)


class CourtWordsPromptTest(unittest.TestCase):
    """D-209: a holding rests on the court's own words, and the quotes are checked against the saved text.

    The run of 2026-09-21 gave three passages one act recites as the court's holdings, and only 5 of 8
    `case_law` quotes could be located in the saved text; the researcher now looks each `critical` quote
    up before it finishes, and the sufficiency reviewer may spot-check five holdings.
    """

    LOCATE = '`{MF} quote locate --workdir {WORK_DIR} --source <source_id> --text "<quote_short>"`'

    def test_every_researcher_prompt_checks_its_critical_quotes_before_done(self):
        for prompt in ResearcherSaveRuleTest._researchers(self):
            self.assertIn(self.LOCATE, prompt)
            self.assertLess(prompt.index(self.LOCATE), prompt.rindex("--state done"))
            self.assertIn("the `quote_short` of every `critical` finding", prompt)
            self.assertIn("One lookup per `critical` finding, plus one retry", prompt)

    def test_a_holding_rests_on_the_court_s_own_statement(self):
        for prompt in ResearcherSaveRuleTest._researchers(self):
            for words in (
                "A finding that says what a court held or applied rests on the court's own statement.",
                'is described as such ("суд воспроизвёл условие оферты …", "истец полагал …")',
                "`quote_short` for a holding comes from the court's own words",
            ):
                with self.subTest(words=words):
                    self.assertIn(words, prompt)

    def test_the_sufficiency_prompt_spot_checks_at_most_five_case_law_holdings(self):
        rendered = PromptGoldenTest._render(self)
        prompt = next(a["prompt"] for a in rendered["agents"] if a["slot"] == "sufficiency")
        prompt = normalize(prompt, rendered["work_dir"])
        self.assertIn(self.LOCATE, prompt)
        self.assertIn("up to 5 `critical` case-law findings", prompt)
        self.assertIn("is a gap for `case_law`", prompt)
        self.assertIn("is a `missing` gap for `statutes`", prompt)

    def test_the_locate_line_is_a_command_the_cli_accepts(self):
        tokens = shlex.split(self.LOCATE.strip("`").replace("{MF}", "mf", 1))
        args = cli.build_parser().parse_args(tokens[1:])
        self.assertIs(quotes.run_locate, args.func)
        self.assertEqual("<quote_short>", args.text)


class McpNamespaceFieldTest(unittest.TestCase):
    """D-187a: `${mcp_namespaces}` names every bundled server the probe found, in table order."""

    def _namespaces(self, namespaces: dict) -> str:
        root = temp_root(self)
        work_dir = root / TASK_ID
        (work_dir / "intake").mkdir(parents=True, exist_ok=True)
        state = _state(work_dir)
        state_io.write_json_atomic(work_dir / gates.PLAN_PATH, {
            "classification": "regulatory_analysis",
            "jurisdictions": ["RU"],
            "doctrine_required": False,
            "estimated_complexity": "medium",
            "issues": [{"issue_id": "i1", "title": "t", "question": "q", "jurisdictions": ["RU"]}],
        })
        state_io.write_json_atomic(work_dir / gates.MCP_PROBE_PATH, {"namespaces": namespaces})
        return machine.researcher_specs(work_dir, state, ["statutes"])[0]["extra"]["mcp_namespaces"]

    def test_a_probe_with_only_the_russian_servers_names_both_namespaces(self):
        field = self._namespaces(
            {"casus": "mcp__plugin_memoforge_casus", "fas": "mcp__plugin_memoforge_fas-search",
             "other": []}
        )
        self.assertEqual("mcp__plugin_memoforge_casus, mcp__plugin_memoforge_fas-search", field)

    def test_every_connected_server_is_listed_in_the_table_order(self):
        probed = {alias: f"mcp__plugin_memoforge_{name}" for alias, name in routing.MCP_SERVERS.items()}
        field = self._namespaces({**probed, "other": ["mcp__house_server"]})
        self.assertEqual(
            list(probed.values()) + ["mcp__house_server"], field.split(", ")
        )

    def test_a_probe_with_only_ldh_and_courtlistener_is_unchanged(self):
        field = self._namespaces(
            {"ldh": "mcp__ldh", "courtlistener": "mcp__courtlistener", "other": []}
        )
        self.assertEqual("mcp__ldh, mcp__courtlistener", field)

    def test_a_probe_that_found_nothing_still_says_none(self):
        self.assertEqual("none", self._namespaces({"other": []}))


class MemoLanguageTest(unittest.TestCase):
    """D-173: every dispatch prompt carries the memo language; findings stay English."""

    PARAGRAPH = (
        "The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,\n"
        "`suggestion` and `reasoning` are always English, whatever the memo language. When the memo\n"
        "language above is not English, a finding with `severity: blocker` also carries `issue_client` —\n"
        "one sentence in ${memo_language_name} saying what the client must check before relying on the memo."
    )

    def test_the_paragraph_is_identical_across_the_eight_language_prompts(self):
        """The reviewer paragraph is one text, pasted word for word (D-173)."""
        names = [
            "memo-writer",
            "logic-reviewer",
            "form-reviewer",
            "citation-auditor",
            "counterargument-reviewer",
            "client-readiness-reviewer",
            "revision-mediator",
            "research-sufficiency-reviewer",
        ]
        for name in names:
            with self.subTest(prompt=name):
                text = dispatch.prompt_path(name).read_text(encoding="utf-8-sig")
                self.assertIn(self.PARAGRAPH, text.replace("\r\n", "\n"))

    def test_the_writer_prompt_adds_headings_risk_line_pinpoints_and_quotes(self):
        text = dispatch.prompt_path("memo-writer").read_text(encoding="utf-8-sig")
        for token in ("${section_titles}", "${risk_line_example}", "${risk_levels}"):
            self.assertIn(token, text)
        self.assertIn("art 6", text)
        self.assertIn("language of the source", text)

    def test_the_sufficiency_prompt_puts_the_printed_fields_in_the_memo_language(self):
        text = dispatch.prompt_path("research-sufficiency-reviewer").read_text(encoding="utf-8-sig")
        for field in ("`drafting_warnings[]`", "`blocking_gaps[].gap`", "`why_blocking`",
                       "`out_of_scope_gaps[]`", "${memo_language_name}"):
            self.assertIn(field, text)

    def _render(self, language: str, make_spec) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        packs = Path(tmp.name)
        _i18n.fake_pack(packs, "ru", _i18n.RU)
        with mock.patch.object(i18n, "PACK_DIR", packs):
            work_dir = temp_root(self) / TASK_ID
            work_dir.mkdir(parents=True, exist_ok=True)
            state = _state(work_dir)
            state["language"] = language
            agents = dispatch.render_agents(
                work_dir, state, step_id="s-017", attempt=1, specs=[make_spec(work_dir, state)],
                position=9, total=12,
            )
            return agents[0]["prompt"]

    def _writer_prompt(self, language: str) -> str:
        return self._render(
            language,
            lambda work_dir, state: machine.writer_spec(
                work_dir, state, task="draft", version=1, canonical="drafts/v1.md",
                instructions="none", seed=False,
            ),
        )

    def test_the_russian_writer_prompt_names_russian_sections_and_risk_words(self):
        prompt = self._writer_prompt("ru")
        self.assertIn("Russian", prompt)
        for title in ("Резюме", "Контекст", "Факты", "Допущения", "Выводы", "Рекомендации"):
            self.assertIn(title, prompt)
        self.assertIn("Риск: средний.", prompt)
        for level in ("высокий", "средний", "низкий", "не определён"):
            self.assertIn(level, prompt)

    def test_the_english_writer_prompt_renders_the_english_forms(self):
        prompt = self._writer_prompt("en")
        self.assertIn("English", prompt)
        self.assertIn("Executive summary", prompt)
        self.assertIn("Risk: medium.", prompt)
        for level in ("high", "medium", "low", "undetermined"):
            self.assertIn(level, prompt)

    def test_the_writer_prompt_carries_the_facts_labels(self):
        # D-190: `${facts_labels}` reaches the writer next to `${section_titles}`.
        for language, labels in (
            ("en", ("Facts", "Assumptions", "Limitations")),
            ("ru", ("Факты", "Допущения", "Ограничения")),
        ):
            with self.subTest(language=language):
                prompt = self._writer_prompt(language)
                for label in labels:
                    self.assertIn(label, prompt)
                self.assertNotIn("${", prompt)

    def test_no_prompt_contains_an_unsubstituted_variable(self):
        for language in ("en", "ru"):
            with self.subTest(language=language):
                self.assertNotIn("${", self._writer_prompt(language))


class SourceAccessLanguageTest(unittest.TestCase):
    """D-176 (sources/preflight): `${source_access}` keeps the English text (agent-facing).

    The researcher prompt carries the English line whatever the interface language is —
    only the gate-visible block is localized.
    """

    def test_the_researcher_prompt_keeps_the_english_source_access(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        packs = Path(tmp.name)
        _i18n.fake_pack(packs, "ru", _i18n.RU_UI)
        with mock.patch.object(i18n, "PACK_DIR", packs):
            root = temp_root(self) / (TASK_ID + "-ui")
            root.mkdir(parents=True, exist_ok=True)
            state = _state(root)
            state["ui_language"] = "ru"
            state_io.write_json_atomic(
                root / preflight.PREFLIGHT_PATH,
                {
                    "schema_version": 1,
                    "checked_at": "2026-09-13T06:05:00Z",
                    "offline": False,
                    "hosts": [
                        {
                            "host": "eur-lex.europa.eu",
                            "url": preflight.PREFLIGHT_URLS["eur-lex.europa.eu"],
                            "status": "waf_challenge",
                            "code": None,
                            "error": None,
                            "alternative": preflight.PREFLIGHT_ALTERNATIVES["eur-lex.europa.eu"],
                        }
                    ],
                },
            )
            specs = machine.researcher_specs(root, state, ["statutes"])
            access = specs[0]["extra"]["source_access"]
            self.assertEqual(1, len(access.splitlines()))
            self.assertIn("eur-lex.europa.eu: WAF challenge", access)
            self.assertNotIn("WAF-проверка", access)


class UiLanguageAgentFieldsTest(unittest.TestCase):
    """D-173b (UI half): gate-visible agent fields are written in the UI language.

    Analyst — `must_answer[].question`, `options[].label/description`, `default`,
    `default_if_wrong` in `${ui_language_name}` (`header` stays English, <= 12 chars,
    internal); sufficiency reviewer — `blocking_gaps[].followup_question` (`question`,
    `options[].label/description`, `default_assumption_if_skipped`) in
    `${ui_language_name}` (the memo-language fields stay as plan 54 set them).
    """

    def _analyst_prompt(self, *, ui_language: str, language: str = "en") -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        packs = Path(tmp.name)
        _i18n.fake_pack(packs, "ru", _i18n.RU)
        with mock.patch.object(i18n, "PACK_DIR", packs):
            work_dir = temp_root(self) / TASK_ID
            work_dir.mkdir(parents=True, exist_ok=True)
            state = _state(work_dir)
            state["language"] = language
            state["ui_language"] = ui_language
            agents = dispatch.render_agents(
                work_dir,
                state,
                step_id="s-017",
                attempt=1,
                specs=[dispatch.spec(
                    "analyst",
                    "fact-assumption-analyst",
                    "intake",
                    [("intake/questions.json", "intake-questions"),
                     ("intake/preliminary-sources.json", "research-findings")],
                    max_questions="10",
                    mcp_namespaces="ldh, courtlistener, fedregs, lex",
                    routing_digest=routing.routing_digest(PROBED_NAMESPACES),
                    retry_errors="none",
                )],
                position=9,
                total=12,
            )
            return agents[0]["prompt"]

    def _sufficiency_prompt(self, *, ui_language: str, language: str = "de") -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        packs = Path(tmp.name)
        _i18n.fake_pack(packs, "ru", _i18n.RU)
        _i18n.fake_pack(packs, "de", _i18n.RU)
        with mock.patch.object(i18n, "PACK_DIR", packs):
            work_dir = temp_root(self) / TASK_ID
            work_dir.mkdir(parents=True, exist_ok=True)
            state = _state(work_dir)
            state["language"] = language
            state["ui_language"] = ui_language
            agents = dispatch.render_agents(
                work_dir,
                state,
                step_id="s-017",
                attempt=1,
                specs=[dispatch.spec(
                    "sufficiency",
                    "research-sufficiency-reviewer",
                    "sufficiency",
                    [("research/research-sufficiency.json", "research-sufficiency")],
                    research_files="`research/statutes.json`",
                    drafting_warnings="none",
                    retry_errors="none",
                )],
                position=9,
                total=12,
            )
            return agents[0]["prompt"]

    def test_the_russian_analyst_prompt_names_russian_for_the_question_fields(self):
        prompt = self._analyst_prompt(ui_language="ru")
        self.assertIn("Russian", prompt)
        self.assertIn("`must_answer[].question`", prompt)
        self.assertIn("`options[].label`", prompt)
        self.assertIn("`default_if_wrong`", prompt)
        self.assertIn("`header`", prompt)
        self.assertNotIn("${", prompt)

    def test_the_english_analyst_prompt_names_english(self):
        prompt = self._analyst_prompt(ui_language="en")
        self.assertIn("English", prompt)
        self.assertNotIn("${", prompt)

    def test_the_russian_sufficiency_prompt_names_russian_for_the_followup(self):
        prompt = self._sufficiency_prompt(ui_language="ru")
        self.assertIn("Russian", prompt)
        self.assertIn("`blocking_gaps[].followup_question`", prompt)
        self.assertIn("`default_assumption_if_skipped`", prompt)
        self.assertIn("German", prompt)
        self.assertNotIn("${", prompt)

    def test_the_english_sufficiency_prompt_names_english(self):
        prompt = self._sufficiency_prompt(ui_language="en")
        self.assertIn("English", prompt)
        self.assertNotIn("${", prompt)


if __name__ == "__main__":
    unittest.main()
