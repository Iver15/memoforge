"""Tests for skills/** — the router skills of ТЗ §3.4, §2.5 and the §9 no-legacy guard."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, i18n  # noqa: E402

SKILLS = PLUGIN_ROOT / "skills"
ROUTER = SKILLS / "memo" / "references" / "router.md"

# ТЗ §3.4 / §2.5: the line ceilings that keep the router readable after summarisation.
MAX_LINES = {"memo": 150, "continue": 60, "status": 50, "brief": 100}
ROUTER_MAX_LINES = 80

# ТЗ §3.4: allowed-tools of the memo router, verbatim.
MEMO_TOOLS = ["Read", "Write", "Bash", "Agent", "AskUserQuestion", "WebFetch", "WebSearch", "mcp__*"]

# ТЗ §9 `test_no_legacy`, restricted to the skills tree (v1 channels removed in §7.1).
LEGACY_IDENTIFIERS = ("PHASE-MACHINE", "update_artifact", "visualize", "TodoWrite", "mark_chapter", 'python3 "')

# `<mf> task new …`, `mf next …`, `"…/scripts/mf" style list`, `scripts/mf.cmd report …`.
MF_CALL = re.compile(r'(?:<mf>|(?:[^\s`"\']*/)?mf(?:\.cmd)?)"?[ \t]+([^`\n]*)')
PLACEHOLDER = re.compile(r"^[<\[]")


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def markdown_files() -> list[Path]:
    return sorted(SKILLS.rglob("*.md"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def frontmatter(text: str) -> dict:
    """Flat YAML front matter of a skill file (no nesting is used by any skill)."""
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


def cli_commands() -> tuple[set[str], dict[str, set[str]]]:
    """`({top-level commands}, {group: {subcommands}})` of the real parser."""
    top: set[str] = set()
    groups: dict[str, set[str]] = {}

    def walk(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, sub in action.choices.items():
                children = [a for a in sub._actions if isinstance(a, argparse._SubParsersAction)]
                if children:
                    groups.setdefault(name, set()).update(
                        key for child in children for key in child.choices
                    )
                else:
                    top.add(name)

    walk(cli.build_parser())
    return top, groups


class LayoutTest(unittest.TestCase):
    def test_router_reference_exists_and_is_short(self):
        self.assertTrue(ROUTER.is_file(), f"missing {ROUTER}")
        lines = read(ROUTER).splitlines()
        self.assertLessEqual(len(lines), ROUTER_MAX_LINES, "router.md is ~60 lines (ТЗ §2.5)")
        text = read(ROUTER)
        for kind in ("dispatch", "script", "gate-auq", "gate-text", "inline-llm", "terminal"):
            self.assertIn(f"`kind: {kind}`", text, f"router.md must say what to do on `{kind}`")
        for fragment in ("next --workdir", "report --workdir", "task new", "task resolve", "mf.cmd"):
            self.assertIn(fragment, text, fragment)

    def test_router_prints_the_plan_before_the_plan_gate_question(self):
        """D-86: the gate-auq section orders the plan first, the AskUserQuestion second."""
        section = read(ROUTER).split("### `kind: gate-auq`", 1)[1].split("\n### ", 1)[0]
        body = section.split("\n", 1)[1]
        self.assertIn("Print `text`", body, "router.md must tell the orchestrator to print the plan")
        self.assertLess(
            body.index("Print `text`"),
            body.index("AskUserQuestion"),
            "router.md must print `text` before calling AskUserQuestion",
        )

    def test_skill_line_budgets(self):
        for name, limit in MAX_LINES.items():
            path = SKILLS / name / "SKILL.md"
            self.assertTrue(path.is_file(), f"missing {path}")
            self.assertLessEqual(len(read(path).splitlines()), limit, f"{name}/SKILL.md > {limit} lines")

    def test_memo_and_continue_read_the_router(self):
        for name in ("memo", "continue"):
            self.assertIn(
                "skills/memo/references/router.md",
                read(SKILLS / name / "SKILL.md"),
                f"{name}/SKILL.md must point at the shared router protocol",
            )

    def test_no_v1_reference_files_are_left(self):
        left = sorted(p.name for p in markdown_files() if p.name not in {"SKILL.md", "router.md"})
        self.assertEqual(left, [], f"v1 reference files still in skills/: {left}")


class FrontmatterTest(unittest.TestCase):
    def test_every_skill_has_the_required_fields(self):
        for path in skill_files():
            fields = frontmatter(read(path))
            with self.subTest(skill=path.parent.name):
                self.assertEqual(fields.get("name"), path.parent.name)
                self.assertTrue(fields.get("description"), "description is required")
                self.assertEqual(fields.get("disable-model-invocation"), "true")
                self.assertTrue(fields.get("allowed-tools"), "allowed-tools is required")

    def test_memo_frontmatter_matches_the_spec(self):
        fields = frontmatter(read(SKILLS / "memo" / "SKILL.md"))
        self.assertTrue(fields.get("argument-hint"), "argument-hint is required (ТЗ §3.4)")
        tools = [item.strip() for item in fields["allowed-tools"].split(",")]
        self.assertEqual(tools, MEMO_TOOLS)

    def test_continue_carries_an_argument_hint(self):
        self.assertTrue(frontmatter(read(SKILLS / "continue" / "SKILL.md")).get("argument-hint"))

    def test_status_is_read_only(self):
        fields = frontmatter(read(SKILLS / "status" / "SKILL.md"))
        tools = [item.strip() for item in fields["allowed-tools"].split(",")]
        self.assertEqual(tools, ["Read", "Bash"])


class GateRouteTest(unittest.TestCase):
    """D-34 / находка 10: одна дорога для текстового ответа на AUQ-гейт — `mf gate parse`."""

    CONTINUE = SKILLS / "continue" / "SKILL.md"

    def test_continue_routes_a_text_gate_answer_to_gate_parse(self):
        text = read(self.CONTINUE)
        self.assertIn("gate parse", text)
        self.assertIn("D-34", text, "continue/SKILL.md must name the decision it follows")

    def test_continue_offers_no_second_route_for_a_text_answer(self):
        text = read(self.CONTINUE)
        self.assertNotIn("--answers", text, "the manual AUQ-answers route is removed (D-34)")
        self.assertNotIn("--status no_answer", text, "the channel switch belongs to `gate parse`")

    def test_every_gate_parse_call_carries_the_generation(self):
        for path in (ROUTER, self.CONTINUE):
            for line in read(path).splitlines():
                if "gate parse" not in line or "--text" not in line:
                    continue
                with self.subTest(file=path.name):
                    self.assertIn("--generation", line, f"{path.name}: gate parse needs --generation")


class DashboardIsStepZeroTest(unittest.TestCase):
    """D-94: the `dashboard` write is the first action on a `next` answer, before any `kind` work."""

    KINDS = ("dispatch", "script", "gate-auq", "gate-text", "inline-llm", "terminal")
    MEMO = SKILLS / "memo" / "SKILL.md"

    def rule(self, path: Path) -> str:
        """The paragraph that states the ordering rule."""
        text = read(path)
        start = text.index("Step 0")
        end = text.find("\n\n", start)
        return text[start:] if end < 0 else text[start:end]

    def test_the_router_states_the_rule_before_every_kind_section(self):
        text = read(ROUTER)
        step_zero = text.index("Step 0")
        self.assertLess(text.index("## 2. The loop"), step_zero, "the rule lives in the loop")
        self.assertLess(step_zero, text.index("## 2a."), "not only in the dashboard side section")
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                self.assertLess(step_zero, text.index(f"### `kind: {kind}`"))

    def test_the_memo_skill_states_it_before_it_describes_the_kinds(self):
        text = read(self.MEMO)
        self.assertLess(text.index("Step 0"), text.index('"kind":"dispatch"'))
        self.assertLess(text.index("## The loop"), text.index("Step 0"))

    def test_the_rule_names_everything_the_write_precedes(self):
        for path in (ROUTER, self.MEMO):
            rule = self.rule(path)
            with self.subTest(file=path.name):
                for token in ("dashboard.publish", "dashboard.write_db", "`Agent`", "command[]",
                              "AskUserQuestion", "terminal message", "`Artifact`"):
                    self.assertIn(token, rule, token)

    def test_the_dashboard_sections_state_the_version_pin(self):
        for path in (ROUTER, self.MEMO, SKILLS / "continue" / "SKILL.md"):
            text = read(path)
            with self.subTest(file=path.name):
                for token in ("if_version", "file_path"):
                    self.assertIn(token, text, token)
        self.assertIn("any **other** `write_db` error", read(self.MEMO))

    def test_continue_repeats_the_ordering_for_a_resumed_run(self):
        rule = self.rule(SKILLS / "continue" / "SKILL.md")
        for token in ("`Agent`", "command[]", "AskUserQuestion", "`Artifact`", "never after"):
            self.assertIn(token, rule, token)

    def test_the_write_is_never_folded_into_the_bash_call_of_next(self):
        for path in (ROUTER, self.MEMO, SKILLS / "continue" / "SKILL.md"):
            with self.subTest(file=path.name):
                self.assertIn("not a Bash command", self.rule(path))

    def test_the_failure_rule_survives_next_to_it(self):
        text = read(ROUTER)
        self.assertIn("task dashboard --workdir W --unavailable", text)
        self.assertIn("never let it change what you do with `kind`", text)


class ScriptErrorPolicyTest(unittest.TestCase):
    """Находка 5 / §2.2 / §3.4: одна политика script-ошибки в router.md и memo/SKILL.md."""

    POLICY = "non-zero exit **or** a JSON answer that carries `errors`"
    FILES = (ROUTER, SKILLS / "memo" / "SKILL.md")

    def test_both_files_state_the_same_policy(self):
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn(self.POLICY, text)

    def test_the_policy_is_one_repeat_then_finalize_and_end(self):
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("finalize --workdir W --reason cli_error", text)
                self.assertIn("once", text)
                self.assertIn("END", text)


class RepromptPolicyTest(unittest.TestCase):
    """D-72 / R2-05: `reprompt` — бизнес-исход гейта, а не ошибка CLI, ни в одном из роутеров."""

    ROUTERS = (ROUTER, SKILLS / "memo" / "SKILL.md", SKILLS / "continue" / "SKILL.md")

    def test_every_router_document_prints_reprompt_and_ends_the_turn(self):
        for path in self.ROUTERS:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("Print `reprompt` verbatim and END the turn", text)
                self.assertIn("never repeat the command and never finalize", text)

    def test_the_cli_error_rule_excludes_a_reprompt_answer(self):
        for path in (ROUTER, SKILLS / "memo" / "SKILL.md"):
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn(
                    ScriptErrorPolicyTest.POLICY + " and no `reprompt`",
                    text,
                    "the repeat-once/cli_error rule must not cover a `reprompt` answer",
                )


class WorkDirLineTest(unittest.TestCase):
    """D-91: the absolute `work_dir` is printed once — in a hosted VM it is the only way to it."""

    FILES = (ROUTER, SKILLS / "memo" / "SKILL.md")

    def test_both_routers_print_the_absolute_work_dir_after_task_new(self):
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("Working folder:", text)
                self.assertIn("absolute", text)

    def test_the_memo_skill_points_at_mf_config_for_options(self):
        text = read(SKILLS / "memo" / "SKILL.md")
        self.assertIn("mf config show", text)
        self.assertIn("mf config set", text)
        self.assertIn("options_source", text)


class MemoLanguageRouteTest(unittest.TestCase):
    """Final review, finding 3 / D-178: per-task memo language is reachable from the chat.

    Without these flags on `task new` the `memo_language=auto` option and "memo in German"
    can never resolve to anything but English, which is what README.md promises they do.
    """

    MEMO = SKILLS / "memo" / "SKILL.md"

    def test_both_routers_pass_the_two_language_flags_to_task_new(self):
        for path in (ROUTER, self.MEMO):
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("--detected-language", text)
                self.assertIn("--language", text)

    def test_the_router_reference_names_the_language_change_at_the_gate(self):
        self.assertIn("task language", read(ROUTER))


class UiLanguageRuleTest(unittest.TestCase):
    """D-178 (plan 56, task 5): the skills answer in the task's UI language."""

    RULE = "Every reply to the user is in the task's `ui_language`"

    def test_memo_continue_and_status_state_the_ui_language_rule(self):
        for name in ("memo", "continue", "status"):
            with self.subTest(skill=name):
                self.assertIn(self.RULE, read(SKILLS / name / "SKILL.md"))

    def test_the_rule_names_what_is_never_translated(self):
        for name in ("memo", "continue", "status"):
            with self.subTest(skill=name):
                self.assertIn("`text_fallback`", read(SKILLS / name / "SKILL.md"))

    def test_memo_and_router_pass_ui_language_only_on_an_explicit_request(self):
        for path in (ROUTER, SKILLS / "memo" / "SKILL.md"):
            with self.subTest(file=path.name):
                self.assertIn("--ui-language", read(path))

    def test_continue_routes_a_memo_language_edit_to_task_language(self):
        text = read(SKILLS / "continue" / "SKILL.md")
        self.assertIn("task language", text)
        self.assertIn("language_locked", text)

    def test_continue_checks_the_memo_language_route_before_gate_parse(self):
        text = read(SKILLS / "continue" / "SKILL.md")
        route = text.index("asks for another memo language")
        parser = text.index("mf gate parse --workdir W --step")
        self.assertLess(route, parser, "the memo-language case must precede the parser step")
        self.assertIn("never goes to `gate parse`", text)

    def test_continue_repeats_the_memo_skill_route_word_for_word(self):
        def route_lines(path: Path) -> list[str]:
            return [
                line for line in read(path).splitlines() if "asks for another memo language" in line
            ]

        memo_lines = route_lines(SKILLS / "memo" / "SKILL.md")
        cont_lines = route_lines(SKILLS / "continue" / "SKILL.md")
        self.assertTrue(memo_lines, "memo/SKILL.md must carry the language route")
        self.assertEqual(memo_lines, cont_lines, "the parked item must agree word for word")

    LABEL_SENTENCE = (
        "`Working folder:` and `Memo language:` are printed in the UI language"
        " — label and language name translated (the endonym for the name),"
        " the path and the code verbatim."
    )

    def test_memo_and_router_render_the_folder_and_language_labels_in_the_ui_language(self):
        self.assertEqual(self.LABEL_SENTENCE.count("`Working folder:`"), 1)
        for path in (ROUTER, SKILLS / "memo" / "SKILL.md"):
            with self.subTest(file=path.name):
                self.assertIn(self.LABEL_SENTENCE, read(path))

    def test_style_no_longer_claims_english_only(self):
        text = read(SKILLS / "style" / "SKILL.md")
        self.assertNotIn("are **English**", text)
        self.assertIn("user's language", text)

    def test_continue_reads_the_saved_ui_language_after_it_resolves_the_task(self):
        """Final review, finding 3: `task resolve` does not answer `ui_language`, so a fresh
        resume reads it from the state the way `status/SKILL.md` does, before it replies."""
        text = read(SKILLS / "continue" / "SKILL.md")
        line = next(
            (row for row in text.splitlines() if "state get" in row and "ui_language" in row), ""
        )
        self.assertIn("mf state get --workdir W --path ui_language", line)
        self.assertIn("English", line, "the absent-field default must be named")
        self.assertLess(
            text.index("task resolve"), text.index("--path ui_language"), "resolve comes first"
        )

    def test_the_legacy_answer_of_the_language_read_is_not_a_cli_failure(self):
        """Commit review P1: a task from before the option answers `{"errors":
        ["path_not_found: ui_language"]}`, and `router.md` §3 would finalize the run on any
        `errors` answer — so both skills that read the field say what that one answer means."""
        for name in ("continue", "status"):
            with self.subTest(skill=name):
                text = read(SKILLS / name / "SKILL.md")
                line = next(
                    row
                    for row in text.splitlines()
                    if "state get" in row and "--path ui_language" in row
                )
                self.assertIn("path_not_found: ui_language", line)
                self.assertIn("English", line)
                self.assertNotIn("CLI failure", line.replace("never a CLI failure", ""))

    def test_the_router_translates_terminal_prose_and_keeps_only_gate_fields_verbatim(self):
        """Final review, finding 4: `machine.terminal_response` is English on purpose, so the
        verbatim rule covers the localized gate fields and the terminal step is translated."""
        text = read(ROUTER)
        rule = next(row for row in text.splitlines() if "Every reply to the user is in" in row)
        self.assertIn("gate", rule, "the verbatim rule must name the gate fields it covers")
        self.assertIn("terminal", rule)
        self.assertIn("`text_fallback`", rule)
        terminal = text.split("### `kind: terminal`", 1)[1]
        self.assertNotIn("Print `text` verbatim", terminal)
        self.assertIn("UI language", terminal)
        self.assertIn("verbatim", terminal, "paths and machine tokens stay verbatim")


class TerminalCopyTest(unittest.TestCase):
    """D-109: the terminal step hands the published folder to the user's own connected folder."""

    FILES = (ROUTER, SKILLS / "memo" / "SKILL.md")

    def test_both_routers_describe_the_copy_to_the_connected_folder(self):
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("Published:", text)
                self.assertIn("connected folder", text)
                self.assertIn("memoforge/<slug>/", text)

    def test_both_routers_copy_the_folder_whole_and_check_the_count(self):
        """D-217: one recursive copy of the whole folder, then its file count against `Files:`."""
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("`Files:`", text)
                self.assertIn("recursively", text)
                self.assertIn("shortfall", text)

    def test_a_host_without_the_tools_does_nothing_extra(self):
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("device file tools", text)
                self.assertRegex(text, r"(?i)(without such tools|with no such tools)")

    def test_both_routers_present_the_memo_copy_first(self):
        """D-167: the terminal step shows the `Memo:` file via `present_files` (Cowork)."""
        for path in self.FILES:
            text = read(path)
            with self.subTest(file=path.name):
                self.assertIn("present_files", text)
                self.assertIn("`Memo:`", text)


class BriefSkillTest(unittest.TestCase):
    """D-226 (plan 75A, DB-01): `/memoforge:brief` is a thin router over `mf brief next|report`."""

    BRIEF = SKILLS / "brief" / "SKILL.md"
    DESCRIPTION = (
        "Build a short decision brief (about three pages) for a decision maker from a finished memoforge memo. "
        "Use only when explicitly invoked via /memoforge:brief."
    )

    def text(self) -> str:
        self.assertTrue(self.BRIEF.is_file(), f"missing {self.BRIEF}")
        return read(self.BRIEF)

    def test_frontmatter_matches_the_plan(self):
        fields = frontmatter(self.text())
        self.assertEqual("brief", fields.get("name"))
        self.assertEqual(self.DESCRIPTION, fields.get("description"))
        self.assertEqual("[<task_id>]", fields.get("argument-hint"))
        self.assertEqual("true", fields.get("disable-model-invocation"))
        tools = [item.strip() for item in fields.get("allowed-tools", "").split(",")]
        self.assertEqual(["Read", "Bash", "Agent", "AskUserQuestion", "mcp__*"], tools)  # D-256: `Artifact`

    def test_it_acts_on_every_kind_the_driver_emits(self):
        text = self.text()
        for kind in ("dispatch", "gate-auq", "gate-text", "done"):
            with self.subTest(kind=kind):
                self.assertIn(f"`kind: {kind}`", text)

    def test_it_speaks_only_the_brief_protocol(self):
        """Only `task list`, `brief next` and `brief report`: the finished memo is never touched."""
        calls = set()
        for rest in MF_CALL.findall(self.text()):
            tokens = [token.strip('.,;:`"\'') for token in rest.split()][:2]
            calls.add(" ".join(tokens))
        self.assertEqual({"task list", "brief next", "brief report"}, calls)
        self.assertNotIn("finalize", self.text())

    def test_no_task_prints_the_english_refusal(self):
        """Before a task is resolved there is no interface language yet: the English pack line."""
        self.assertIn(i18n.t("en", "ui.brief.refused.no_task"), self.text())

    def test_the_report_carries_the_identity_and_one_answer(self):
        text = self.text()
        for fragment in ("--run", "--step", "--attempt", "--slot", "--status ok", "--status fail",
                         "--answers", "--status no_answer", "--text", "mf.cmd"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_a_dispatch_goes_out_in_one_message_unchanged(self):
        section = self.text().split("### `kind: dispatch`", 1)[1].split("\n### ", 1)[0]
        for fragment in ("ONE message", "unchanged", "`subagent_type`", "`model`", "`description`",
                         "`prompt`", "Add nothing"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, section)
        # DB-02: `next` re-issues a slot that is still running, so it waits for every report.
        self.assertIn("only after every slot", section)

    def test_a_reply_to_the_open_gate_goes_to_report_text(self):
        text = self.text()
        self.assertIn("D-34", text)
        self.assertIn("never a second `AskUserQuestion`", text)

    def test_the_reply_and_the_answers_reach_the_cli_verbatim(self):
        """Fix round 1: a reply with `"`, `$(…)` or `'` is one single-quoted argument, never double-quoted."""
        text = self.text()
        rule = text[text.index("**Shell quoting.**"):].split("\n\n", 1)[0]
        for fragment in ("`--text`", "`--answers`", "single-quoted", "`'\\''`", "Never put them inside double quotes",
                         "`$(…)`", """`--text 'it'\\''s "yes" $(date)'`"""):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, rule)
        example = re.search(r"`(--text '[^`]*')`", rule).group(1)
        self.assertEqual(["--text", 'it\'s "yes" $(date)'], shlex.split(example), "the example round-trips")
        self.assertNotIn('--text "', text, "no double-quoted reply anywhere in the skill")
        for line in text.splitlines():
            if "--answers" in line and "brief report" in line:
                self.assertIn("--answers '", line, "the answers JSON is single-quoted")

    def test_done_prints_the_whole_text_and_presents_the_file(self):
        section = self.text().split("### `kind: done`", 1)[1].split("\n### ", 1)[0]
        for fragment in ("in full", "present_files", "`present`", "`path`", "END"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, section)

    def test_failures_stale_report_and_one_retry(self):
        text = self.text()
        for fragment in ("stale_report", "unrecognised_answer", "once", "`kind: \"done\"`"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)


class NoLegacyTest(unittest.TestCase):
    def test_no_legacy_identifier_in_skills(self):
        for path in markdown_files():
            text = read(path)
            for identifier in LEGACY_IDENTIFIERS:
                with self.subTest(file=path.name, identifier=identifier):
                    self.assertNotIn(identifier, text)


class CliCoverageTest(unittest.TestCase):
    def test_every_mf_command_named_in_skills_exists(self):
        top, groups = cli_commands()
        self.assertIn("next", top)
        self.assertIn("task", groups)
        for path in markdown_files():
            for rest in MF_CALL.findall(read(path)):
                tokens = [token.strip('.,;:`"\'') for token in rest.split()]
                tokens = [token for token in tokens if token]
                if not tokens:
                    continue
                head = tokens[0]
                with self.subTest(file=path.name, call=" ".join(tokens[:2])):
                    self.assertTrue(
                        head in top or head in groups,
                        f"unknown `mf {head}` (known: {sorted(top | set(groups))})",
                    )
                    if head in groups and len(tokens) > 1:
                        sub = tokens[1]
                        if sub.startswith("-") or PLACEHOLDER.match(sub):
                            continue
                        self.assertIn(sub, groups[head], f"unknown `mf {head} {sub}`")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
