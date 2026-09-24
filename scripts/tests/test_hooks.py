"""Tests for `hooks/` — permission gate, progress logger, stop guard, generator (ТЗ §8.1–§8.4, §9).

The bypasses in `FetchGateTest` are the table of `analysis/03-scripts-audit.md` §6 verbatim: every
row that the v1 inline hook got wrong (S-03) is a test here. The bash gate is checked with both
lexers (POSIX quoting and the Windows `posix=False` variant) because §8.1 requires the executable to
resolve under both. Nothing in this file touches the network or the user's real task folders: `HOME`
/ `USERPROFILE` and the output-folder chain are redirected into a temporary directory.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import events, limits, routing, schema, task  # noqa: E402


def load_hook(name: str, directory: str = "hooks"):
    """Import a hook script by path — `hooks/` is deliberately not a package."""
    path = PLUGIN_ROOT / directory / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"memoforge_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


permission_gate = load_hook("permission_gate")
progress_logger = load_hook("progress_logger")
stop_guard = load_hook("stop_guard")
ensure_deps = load_hook("ensure_deps")
build_hooks = load_hook("build_hooks")
probe_echo = load_hook("probe_echo")
statusline = load_hook("subagent_statusline", directory="scripts")

ENV_KEYS = (
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_PLUGIN_ROOT",
    "MEMOFORGE_OUTPUT_FOLDER",
    "CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER",
    "CLAUDE_PLUGIN_OPTION_WEBSEARCH_AUTOALLOW",
    "CLAUDE_PLUGIN_OPTION_STOP_GUARD",
    "HOME",
    "USERPROFILE",
)


def make_task(root: Path, name: str = "memo-a", *, phase: str = "research", schema_version: int = 2) -> Path:
    """A work dir the hooks recognise: `state.json` with a v2 schema and a non-terminal phase."""
    work_dir = root / name
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "task_id": name,
                "current_phase": phase,
                "created_at": "2026-09-08T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return work_dir


def hook_payload(**fields) -> str:
    """One hook payload as the JSON text the script reads from stdin."""
    return json.dumps(fields)


def read_lines(work_dir: Path) -> list:
    """Raw journal lines (not deduplicated) — the at-least-once contract is about the file."""
    path = work_dir / events.EVENTS_FILENAME
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8-sig").split("\n") if line.strip()]


class _EnvMixin:
    """Temporary root plus a hermetic environment: no real task folder can be discovered."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["HOME"] = str(self.home)
        os.environ["USERPROFILE"] = str(self.home)
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def isolate(self, directory: Path) -> Path:
        """Point both the project dir and the output-folder chain at one directory."""
        directory.mkdir(parents=True, exist_ok=True)
        os.environ["CLAUDE_PROJECT_DIR"] = str(directory)
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(directory)
        return directory


# --- permission_gate --mode fetch -----------------------------------------


class FetchGateTest(unittest.TestCase):
    """`analysis/03-scripts-audit.md` §6, one test per row, plus the S-03 fix."""

    def decide(self, tool_input, tool_name: str = "WebFetch") -> dict:
        text = hook_payload(hook_event_name="PreToolUse", tool_name=tool_name, tool_input=tool_input)
        return json.loads(permission_gate.main(["--mode", "fetch"], stdin_text=text))

    def assertAllowed(self, tool_input):
        result = self.decide(tool_input)
        self.assertEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"), "allow", result)

    def assertPassthrough(self, tool_input):
        self.assertEqual(self.decide(tool_input), {}, tool_input)

    def test_allowlisted_host_is_allowed(self):
        self.assertAllowed({"url": "https://edpb.europa.eu/news/x"})

    def test_the_decision_names_the_host(self):
        result = self.decide({"url": "https://ico.org.uk/a"})
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("ico.org.uk", reason)
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_evil_europa_eu_is_not_a_suffix_match(self):
        self.assertPassthrough({"url": "https://evil-europa.eu/x"})

    def test_allowlisted_host_as_a_prefix_of_another_domain(self):
        self.assertPassthrough({"url": "https://europa.eu.evil.com/x"})

    def test_userinfo_in_the_authority_does_not_fool_the_parser(self):
        self.assertPassthrough({"url": "https://europa.eu@evil.com/"})

    def test_allowlisted_host_in_a_fragment_is_ignored(self):
        self.assertPassthrough({"url": "https://evil.com/#https://europa.eu"})

    def test_trailing_dot_is_a_safe_false_negative(self):
        self.assertPassthrough({"url": "https://edpb.europa.eu./"})

    def test_url_in_a_prompt_is_never_read(self):
        """S-03: the v1 hook scanned every string value and allowed this."""
        self.assertPassthrough({"prompt": "see https://edpb.europa.eu first", "target_url": "https://evil.com"})

    def test_null_url_with_an_allowlisted_prompt_is_passthrough(self):
        """S-03: `{"url": null, "prompt": "…europa.eu"}` used to be allowed."""
        self.assertPassthrough({"url": None, "prompt": "https://edpb.europa.eu"})

    def test_the_url_key_is_not_overridden_by_another_key(self):
        self.assertPassthrough({"url": "https://evil.com", "prompt": "https://europa.eu"})

    def test_every_url_key_present_must_be_allowlisted(self):
        self.assertPassthrough({"url": "https://europa.eu/a", "href": "https://evil.com/b"})

    def test_uri_link_and_href_are_read(self):
        for key in ("uri", "link", "href"):
            self.assertAllowed({key: "https://cnil.fr/a"})

    def test_empty_tool_input(self):
        self.assertPassthrough({})

    def test_non_dict_tool_input_does_not_crash(self):
        """v1 raised AttributeError and exited 1 on this."""
        self.assertPassthrough("x")

    def test_missing_tool_input(self):
        text = hook_payload(hook_event_name="PreToolUse", tool_name="WebFetch")
        self.assertEqual(json.loads(permission_gate.main(["--mode", "fetch"], stdin_text=text)), {})

    def test_non_json_stdin_does_not_crash(self):
        """v1 raised JSONDecodeError and exited 1 on this."""
        self.assertEqual(permission_gate.main(["--mode", "fetch"], stdin_text="not json at all"), "{}")

    def test_empty_stdin(self):
        self.assertEqual(permission_gate.main(["--mode", "fetch"], stdin_text=""), "{}")

    def test_mcp_fetch_tools_use_the_same_gate(self):
        self.assertAllowed({"url": "https://publications.europa.eu/resource/celex/32016R0679"})
        self.assertEqual(self.decide({"url": "https://evil.com"}, tool_name="mcp__workspace__web_fetch"), {})

    def test_the_optional_group_is_off_by_default(self):
        """§8.1: justia.com / findlaw.com / iapp.org ship commented out."""
        for host in ("justia.com", "findlaw.com", "iapp.org"):
            self.assertPassthrough({"url": f"https://{host}/x"})

    def test_the_crafted_fragment_host_is_never_allowed(self):
        """D-151: `mf sources fetch` used to read `govinfo.gov` out of this fragment."""
        self.assertPassthrough({"url": "https://outside.example#@govinfo.gov"})
        self.assertEqual("outside.example", permission_gate.request_host("https://outside.example#@govinfo.gov"))

    def test_a_url_with_userinfo_is_refused_whichever_half_is_allow_listed(self):
        self.assertPassthrough({"url": "https://evil.example@govinfo.gov/link/uscode/15/45"})
        self.assertPassthrough({"url": "https://govinfo.gov@evil.example/x"})
        self.assertEqual("", permission_gate.request_host("https://evil.example@govinfo.gov/x"))

    def test_only_http_and_https_are_read_as_urls(self):
        for url in ("ftp://govinfo.gov/x", "file:///etc/passwd", "javascript:fetch('//govinfo.gov')"):
            self.assertPassthrough({"url": url})
            self.assertEqual("", permission_gate.request_host(url))

    def test_the_gate_and_the_command_parse_a_url_the_same_way(self):
        """D-151: one predicate — the bypass was the two of them disagreeing about `hostname`."""
        from memoforge import sources

        for url in (
            "https://outside.example#@govinfo.gov",
            "https://evil.example@govinfo.gov/x",
            "https://govinfo.gov@evil.example/x",
            "https://GovInfo.GOV/link/uscode/15/45",
            "https://europa.eu.evil.com/x",
            "https://edpb.europa.eu./",
            "ftp://govinfo.gov/x",
            "file:///etc/passwd",
            "https:///path",
            "not a url at all",
            "",
        ):
            with self.subTest(url=url):
                self.assertEqual(sources.request_host(url), permission_gate.request_host(url))

    def test_a_missing_allowlist_file_allows_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = hook_payload(tool_input={"url": "https://europa.eu/x"})
            self.assertEqual(permission_gate.main(["--mode", "fetch"], stdin_text=text, root=tmp), "{}")

    def test_case_and_subdomains(self):
        self.assertAllowed({"url": "https://EUR-LEX.EUROPA.EU/legal-content"})

    def test_scheme_without_a_host(self):
        self.assertPassthrough({"url": "file:///etc/passwd"})


class AllowlistFileTest(unittest.TestCase):
    def test_the_shipped_allowlist_is_the_v1_set_minus_the_optional_group(self):
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        # D-105: the v1 78 plus the two LegalViz hosts; D34-02 adds the 11-host `legislature`
        # group and the Austrian supervisory authority; D-145 nets 92 - 3 + 1 = 90 —
        # `data.bka.gv.at` in, `api.legalviz.eu` out, `ecfr.gov`/`federalregister.gov` to
        # `optional`; D-148 adds the nine-host `legislature-api` group, 90 + 9 = 99;
        # D-162 returns the two US regulation API hosts to the active set, 99 + 2 = 101;
        # D-185 adds the seven-host `ru` group, 101 + 7 = 108;
        # D-205 adds `garant.ru` to the `ru` group, 108 + 1 = 109.
        self.assertEqual(len(hosts), 109)
        self.assertIn("europa.eu", hosts)
        self.assertNotIn("justia.com", hosts)

    def test_the_hosts_the_sweep_retired_are_no_longer_auto_allowed(self):
        """D-145: two «Request Access» stubs and one MCP endpoint that is not a fetch target.

        D-162 re-allowlisted the two stubs' hosts for their open APIs; only the MCP
        endpoint row remains retired.
        """
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        self.assertIn("ecfr.gov", hosts)
        self.assertIn("federalregister.gov", hosts)
        self.assertNotIn("api.legalviz.eu", hosts)

    def test_the_national_statute_portals_of_the_routing_table_are_allowlisted(self):
        """D34-02: the 20260910 run could not fetch a single member-state statute book."""
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        for host in (
            "gesetze-im-internet.de",
            "legifrance.gouv.fr",
            "irishstatutebook.ie",
            "wetten.overheid.nl",
            "boe.es",
            "normattiva.it",
            "ris.bka.gv.at",
            "data.bka.gv.at",
            "dejure.org",
            "buzer.de",
        ):
            with self.subTest(host=host):
                self.assertIn(host, hosts)

    def test_the_official_api_hosts_of_the_legislatures_are_allowlisted(self):
        """D-148 / analysis/39 §8.5: the machine channels behind the blocked HTML portals."""
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        for host in (
            "api.normattiva.it",
            "dati.normattiva.it",
            "repository.officiele-overheidspublicaties.nl",
            "zoekservice.overheid.nl",
            "code.travail.gouv.fr",
            "rechtsinformationen.bund.de",
            "courdecassation.fr",
            "fedlex.admin.ch",
            "bger.ch",
        ):
            with self.subTest(host=host):
                self.assertIn(host, hosts)

    def test_the_mcp_endpoints_are_not_fetch_targets(self):
        """D-148 keeps the D-145 rule: an MCP endpoint is called as a server, never fetched."""
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        for host in ("justicelibre.org", "mcp.opencaselaw.ch", "federal-regulations.caseyjhand.com"):
            with self.subTest(host=host):
                self.assertNotIn(host, hosts)

    def test_the_lex_endpoint_is_not_a_fetch_target(self):
        # D-161: the i.AI Lex endpoint is called as a server, never fetched.
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        self.assertNotIn("lex.lab.i.ai.gov.uk", hosts)

    def test_the_russian_portals_the_routing_table_points_at_are_allowlisted(self):
        # D-185: the RU rows fetch consultant.ru, base.garant.ru, sudact.ru, zakon.ru,
        # cyberleninka.ru and cite the Supreme Court's own portal. D-205: Plenum rulings are
        # indexed on the parent host, so `mf sources save --url` needs garant.ru itself.
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        for host in (
            "consultant.ru",
            "www.consultant.ru",
            "base.garant.ru",
            "garant.ru",
            "sudact.ru",
            "zakon.ru",
            "cyberleninka.ru",
            "vsrf.ru",
        ):
            with self.subTest(host=host):
                self.assertIn(host, hosts)

    def test_the_uk_portals_the_routing_table_points_at_are_allowlisted(self):
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        self.assertIn("legislation.gov.uk", hosts)
        self.assertIn("caselaw.nationalarchives.gov.uk", hosts)

    def test_the_us_regulation_api_hosts_are_allowlisted(self):
        # D-162: `mf sources fetch` refuses hosts off the allowlist, and the eCFR / Federal Register
        # APIs are the US fallback route.
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        self.assertIn("ecfr.gov", hosts)
        self.assertIn("federalregister.gov", hosts)

    def test_the_legalviz_reader_is_allowlisted_but_not_its_mcp_endpoint(self):
        """D-145 corrects D-105: an MCP endpoint is not called through WebFetch, so it is not here."""
        hosts = permission_gate.load_allowlist(PLUGIN_ROOT)
        self.assertIn("legalviz.eu", hosts)
        self.assertNotIn("api.legalviz.eu", hosts)

    def test_every_group_of_the_file_is_labelled(self):
        raw = (PLUGIN_ROOT / "hooks" / "allowlist.txt").read_text(encoding="utf-8-sig")
        for group in ("eu", "legislature", "legislature-api", "ru", "dpa", "us", "intl", "ngo", "optional"):
            self.assertIn(f"# group: {group}", raw)

    def test_comments_are_stripped_and_hosts_are_lowercase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hooks").mkdir()
            (root / "hooks" / "allowlist.txt").write_text(
                "# group: eu\nEUROPA.EU  # inline\n# justia.com\n\n", encoding="utf-8"
            )
            self.assertEqual(permission_gate.load_allowlist(root), frozenset({"europa.eu"}))


# --- permission_gate --mode websearch -------------------------------------


class WebSearchGateTest(_EnvMixin, unittest.TestCase):
    def decide(self, work_dir: Path | None) -> dict:
        text = hook_payload(
            hook_event_name="PreToolUse",
            tool_name="WebSearch",
            tool_input={"query": "gdpr"},
            cwd=str(work_dir or self.root),
        )
        return json.loads(permission_gate.main(["--mode", "websearch"], stdin_text=text))

    def test_allowed_while_a_task_is_active(self):
        work_dir = make_task(self.root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(work_dir)
        self.assertEqual(
            self.decide(work_dir)["hookSpecificOutput"]["permissionDecision"], "allow"
        )

    def test_silent_without_an_active_task(self):
        self.assertEqual(self.decide(None), {})

    def test_silent_for_a_terminal_task(self):
        work_dir = make_task(self.root, phase="done")
        os.environ["CLAUDE_PROJECT_DIR"] = str(work_dir)
        self.assertEqual(self.decide(work_dir), {})

    def test_silent_for_a_v1_task(self):
        work_dir = make_task(self.root, schema_version=1)
        os.environ["CLAUDE_PROJECT_DIR"] = str(work_dir)
        self.assertEqual(self.decide(work_dir), {})

    def test_the_option_can_switch_it_off(self):
        work_dir = make_task(self.root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(work_dir)
        os.environ["CLAUDE_PLUGIN_OPTION_WEBSEARCH_AUTOALLOW"] = "false"
        self.assertEqual(self.decide(work_dir), {})

    def test_an_unset_option_means_on(self):
        work_dir = make_task(self.root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(work_dir)
        self.assertNotEqual(self.decide(work_dir), {})


# --- permission_gate --mode bash ------------------------------------------


class BashGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "plug in root"
        (self.root / "scripts").mkdir(parents=True)
        self.mf = str(self.root / "scripts" / "mf")
        self.mf_cmd = str(self.root / "scripts" / "mf.cmd")

    def decide(self, command, tool_name: str = "Bash") -> dict:
        text = hook_payload(
            hook_event_name="PreToolUse", tool_name=tool_name, tool_input={"command": command}
        )
        return json.loads(permission_gate.main(["--mode", "bash"], stdin_text=text, root=str(self.root)))

    def assertAllowed(self, command):
        result = self.decide(command)
        self.assertEqual(
            result.get("hookSpecificOutput", {}).get("permissionDecision"), "allow", command
        )

    def assertPassthrough(self, command):
        self.assertEqual(self.decide(command), {}, command)

    def test_double_quoted_path_with_spaces(self):
        self.assertAllowed(f'"{self.mf}" state show --workdir "{self.root}"')

    def test_single_quoted_path_with_spaces_posix_style(self):
        self.assertAllowed(f"'{self.mf}' state show")

    def test_cmd_wrapper_with_an_absolute_path(self):
        self.assertAllowed(f'"{self.mf_cmd}" next --workdir "{self.root}"')

    def test_forward_slashes_are_normalised(self):
        self.assertAllowed('"' + self.mf.replace(os.sep, "/") + '" state show')

    def test_json_argument_survives(self):
        self.assertAllowed(f"""'{self.mf}' events log --event agent_log --data '{{"a": 1}}'""")

    def test_bare_mf_cmd_is_not_accepted(self):
        self.assertPassthrough("mf.cmd state show")

    def test_bare_mf_is_not_accepted(self):
        self.assertPassthrough("mf state show")

    def test_relative_path_is_not_accepted(self):
        self.assertPassthrough("scripts/mf state show")

    def test_another_executable_is_not_accepted(self):
        self.assertPassthrough(f'python "{self.mf}" state show')

    def test_a_sibling_of_mf_is_not_accepted(self):
        self.assertPassthrough(f'"{self.mf}x" state show')

    def test_and_operator(self):
        self.assertPassthrough(f'"{self.mf}" next && rm -rf /')

    def test_semicolon(self):
        self.assertPassthrough(f'"{self.mf}" next; whoami')

    def test_pipe(self):
        self.assertPassthrough(f'"{self.mf}" next | tee out.txt')

    def test_or_operator(self):
        self.assertPassthrough(f'"{self.mf}" next || whoami')

    def test_redirect(self):
        self.assertPassthrough(f'"{self.mf}" next > out.txt')
        self.assertPassthrough(f'"{self.mf}" next < in.txt')

    def test_command_substitution(self):
        self.assertPassthrough(f'"{self.mf}" next --note $(whoami)')

    def test_backticks(self):
        self.assertPassthrough(f'"{self.mf}" next --note `whoami`')

    def test_newline(self):
        self.assertPassthrough(f'"{self.mf}" next\nrm -rf /')

    def test_background_ampersand(self):
        self.assertPassthrough(f'"{self.mf}" next &')

    def test_unbalanced_quotes_are_passthrough(self):
        self.assertPassthrough(f'"{self.mf}" next --note "unclosed')

    def test_missing_command_key(self):
        text = hook_payload(tool_name="Bash", tool_input={"cmd": "ls"})
        self.assertEqual(json.loads(permission_gate.main(["--mode", "bash"], stdin_text=text)), {})

    def test_non_dict_tool_input(self):
        text = hook_payload(tool_name="Bash", tool_input="ls")
        self.assertEqual(json.loads(permission_gate.main(["--mode", "bash"], stdin_text=text)), {})

    def test_cowork_bash_uses_the_same_gate(self):
        result = self.decide(f'"{self.mf}" state show', tool_name="mcp__workspace__bash")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_both_lexers_are_tried(self):
        """§8.1: «после `shlex`-разбора (posix и Windows-варианты)»."""
        variants = permission_gate.parse_variants(f'"{self.mf}" state show')
        self.assertEqual(len(variants), 2)
        for tokens in variants:
            self.assertEqual(tokens[0], self.mf)


class GateSafetyTest(unittest.TestCase):
    def test_an_unknown_mode_is_passthrough(self):
        self.assertEqual(permission_gate.decide("nope", {"tool_input": {"url": "https://europa.eu"}}), {})

    def test_a_non_dict_payload_is_passthrough(self):
        self.assertEqual(permission_gate.decide("fetch", ["url"]), {})

    def test_the_gate_never_denies(self):
        """The only outputs are `{}` and allow — a deny would override user permission rules."""
        source = (PLUGIN_ROOT / "hooks" / "permission_gate.py").read_text(encoding="utf-8-sig")
        self.assertNotIn('"deny"', source)
        self.assertNotIn("'deny'", source)


# --- progress_logger ------------------------------------------------------


class ProgressLoggerTest(_EnvMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.work_dir = make_task(self.root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.work_dir)

    def run_hook(self, argv, **fields) -> str:
        return progress_logger.main(argv, stdin_text=hook_payload(**fields), cwd=str(self.work_dir))

    def test_pre_tool_use_writes_subagent_requested(self):
        self.run_hook(
            ["--pre"],
            hook_event_name="PreToolUse",
            tool_name="Agent",
            session_id="s1",
            tool_use_id="t1",
            tool_input={
                "description": "P3/12 · legal-researcher · statutory",
                "subagent_type": "memoforge:legal-researcher",
            },
        )
        records = events.read_events(self.work_dir)
        self.assertEqual([row["event"] for row in records], ["subagent_requested"])
        self.assertEqual(records[0]["data"]["description"], "P3/12 · legal-researcher · statutory")
        self.assertEqual(records[0]["data"]["agent_type"], "memoforge:legal-researcher")
        self.assertEqual(records[0]["actor"], "hook")
        self.assertEqual(records[0]["phase"], "research")

    def test_the_event_is_written_under_the_events_lock(self):
        """§7.2 C: hooks use the same writer protocol as the CLI — lock file plus `.seen` marker."""
        self.run_hook(["--pre"], session_id="s1", tool_use_id="t1", tool_input={"description": "d"})
        self.assertTrue((self.work_dir / events.EVENTS_LOCK_FILENAME).is_file())
        key = events.read_events(self.work_dir)[0]["event_key"]
        self.assertTrue(events.seen_marker(self.work_dir, key).is_file())

    def test_three_runs_of_one_event_leave_one_line(self):
        """The three interpreter variants of §8.1 fire the same hook three times."""
        for _ in range(3):
            self.run_hook(["--pre"], session_id="s1", tool_use_id="t1", tool_input={"description": "d"})
        self.assertEqual(len(read_lines(self.work_dir)), 1)

    def test_three_concurrent_processes_leave_one_line(self):
        payload = hook_payload(
            hook_event_name="PreToolUse",
            session_id="s1",
            tool_use_id="t-concurrent",
            tool_input={"description": "d"},
        )
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.work_dir)
        script = str(PLUGIN_ROOT / "hooks" / "progress_logger.py")

        def run_one(_):
            return subprocess.run(
                [sys.executable, script, "--pre"],
                input=payload.encode("utf-8"),
                capture_output=True,
                env=env,
                cwd=str(self.work_dir),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(run_one, range(3)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        lines = read_lines(self.work_dir)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(json.loads(lines[0])["event"], "subagent_requested")

    def test_distinct_tool_use_ids_are_distinct_events(self):
        for index in range(3):
            self.run_hook(["--pre"], session_id="s1", tool_use_id=f"t{index}", tool_input={"description": "d"})
        self.assertEqual(len(read_lines(self.work_dir)), 3)

    def test_subagent_start_and_stop(self):
        self.run_hook(
            [],
            hook_event_name="SubagentStart",
            session_id="s1",
            agent_id="a1",
            agent_type="memoforge:memo-writer",
        )
        self.run_hook(
            [],
            hook_event_name="SubagentStop",
            session_id="s1",
            agent_id="a1",
            agent_type="memoforge:memo-writer",
            result="ok",
        )
        records = events.read_events(self.work_dir)
        self.assertEqual([row["event"] for row in records], ["subagent_started", "subagent_stopped"])
        self.assertEqual(records[1]["data"], {"agent_id": "a1", "agent_type": "memoforge:memo-writer", "result": "ok"})

    def test_an_unknown_hook_event_writes_nothing(self):
        self.run_hook([], hook_event_name="TeammateIdle", session_id="s1", agent_id="a1")
        self.assertEqual(read_lines(self.work_dir), [])

    def test_mcp_call_is_split_into_the_server_alias_and_the_tool(self):
        # D34-03: `server` is the routing alias, so `progress.mcp_calls` and `config.mcp_budget`
        # are keyed the same way whatever namespace the host mounted the server under.
        self.run_hook(
            ["--mcp"],
            hook_event_name="PostToolUse",
            session_id="s1",
            tool_use_id="t9",
            tool_name="mcp__plugin_memoforge_courtlistener__search",
            tool_response={"ok": True},
        )
        data = events.read_events(self.work_dir)[0]["data"]
        self.assertEqual(data["server"], "courtlistener")
        self.assertEqual(data["tool"], "search")
        self.assertTrue(data["ok"])

    def test_a_host_namespace_of_the_same_server_lands_on_the_same_alias(self):
        """D34-03: the 20260910 run logged 0 of 57 LegalViz calls because of the namespace."""
        for index, (tool_name, alias) in enumerate(
            (
                ("mcp__Legal_Data_Hunter__resolve_reference", "ldh"),
                ("mcp__claude_ai_Legal_Data_Hunter__search", "ldh"),
                ("mcp__Legalviz__get_law_part", "legalviz"),
                ("mcp__eurlex__get_case_law", "legalviz"),
                ("mcp__uk-legal__legislation_get_section", "uklegal"),
                ("mcp__uk_legal_mcp__case_law_search", "uklegal"),
                # D-182: Cowork spells the UK server `claude_ai_UK_MCP`.
                ("mcp__claude_ai_UK_MCP__legislation_search", "uklegal"),
                # D-148: justicelibre announces itself by its own name, OpenCaseLaw as
                # `swiss-caselaw` in `serverInfo.name`.
                ("mcp__plugin_memoforge_justicelibre__get_law_article", "justicelibre"),
                ("mcp__justicelibre__search_judiciaire", "justicelibre"),
                ("mcp__plugin_memoforge_opencaselaw__get_law", "opencaselaw"),
                ("mcp__swiss-caselaw__search_decisions", "opencaselaw"),
                ("mcp__plugin_memoforge_federal-regulations__regulations_get_cfr_section", "fedregs"),
                ("mcp__federal-regulations-mcp-server__regulations_search_rules", "fedregs"),
                # D-161: Lex (i.AI) announces itself as `Lex API`; the bare spelling
                # must never capture `eurlex` (which stays LegalViz).
                ("mcp__plugin_memoforge_lex__lookup_legislation", "lex"),
                ("mcp__Lex_API__search_for_legislation_sections", "lex"),
                ("mcp__eurlex__get_case_law", "legalviz"),
                # D-184: CasusLegal (RU) announces itself as `CasusLegal`/`Casus`,
                # FAS advertising practice (RU) as `fas-search`.
                ("mcp__plugin_memoforge_casus__casuslegal_search_practice", "casus"),
                ("mcp__claude_ai_CasusLegal__casuslegal_find_term", "casus"),
                ("mcp__plugin_memoforge_fas-search__search_fas_cases", "fas"),
                ("mcp__claude_ai_fas_search__get_case_details", "fas"),
            )
        ):
            with self.subTest(tool_name=tool_name):
                self.run_hook(
                    ["--mcp"],
                    hook_event_name="PostToolUse",
                    session_id="s1",
                    tool_use_id=f"t{index}",
                    tool_name=tool_name,
                    tool_response={"ok": True},
                )
        servers = [row["data"]["server"] for row in events.read_events(self.work_dir)]
        self.assertEqual(
            [
                "ldh",
                "ldh",
                "legalviz",
                "legalviz",
                "uklegal",
                "uklegal",
                "uklegal",
                "justicelibre",
                "justicelibre",
                "opencaselaw",
                "opencaselaw",
                "fedregs",
                "fedregs",
                "lex",
                "lex",
                "legalviz",
                "casus",
                "casus",
                "fas",
                "fas",
            ],
            servers,
        )

    def test_an_mcp_server_that_is_not_a_legal_database_is_not_accounted_for(self):
        self.run_hook(
            ["--mcp"],
            hook_event_name="PostToolUse",
            session_id="s1",
            tool_use_id="t9",
            tool_name="mcp__claude-in-chrome__navigate",
            tool_response={"ok": True},
        )
        self.assertEqual(read_lines(self.work_dir), [])

    def test_every_alias_of_the_hook_is_a_routing_alias(self):
        self.assertEqual(
            sorted(routing.MCP_SERVERS),
            sorted({alias for _, alias in progress_logger.MCP_ALIAS_SPELLINGS}),
        )

    def test_mcp_call_records_a_failure(self):
        self.run_hook(
            ["--mcp"],
            session_id="s1",
            tool_use_id="t9",
            tool_name="mcp__plugin_memoforge_ldh__get_document",
            tool_response={"is_error": True},
        )
        self.assertFalse(events.read_events(self.work_dir)[0]["data"]["ok"])

    def test_pre_compact_has_no_event_key(self):
        self.run_hook(["--compact"], hook_event_name="PreCompact", session_id="s1", trigger="auto")
        record = events.read_events(self.work_dir)[0]
        self.assertEqual(record["event"], "context_compacted")
        self.assertIsNone(record["event_key"])
        self.assertEqual(record["data"]["trigger"], "auto")

    def test_a_long_result_is_truncated_to_keep_the_line_small(self):
        self.run_hook([], hook_event_name="SubagentStop", session_id="s1", agent_id="a1", result="x" * 9000)
        line = read_lines(self.work_dir)[0]
        self.assertLessEqual(len(line.encode("utf-8")), limits.EVENT_LINE_MAX_BYTES)
        self.assertEqual(len(events.read_events(self.work_dir)[0]["data"]["result"]), progress_logger.MAX_TEXT)

    def test_no_active_task_writes_nothing(self):
        empty = self.isolate(self.root / "elsewhere")
        self.assertEqual(
            progress_logger.main(["--pre"], stdin_text=hook_payload(session_id="s", tool_use_id="t"), cwd=str(empty)),
            "{}",
        )
        self.assertEqual(read_lines(self.work_dir), [])

    def test_a_terminal_task_writes_nothing(self):
        done = make_task(self.isolate(self.root / "closed"), "memo-done", phase="done")
        os.environ["CLAUDE_PROJECT_DIR"] = str(done)
        progress_logger.main(["--pre"], stdin_text=hook_payload(session_id="s", tool_use_id="t"), cwd=str(done))
        self.assertEqual(read_lines(done), [])

    def test_non_json_stdin_is_silent(self):
        self.assertEqual(progress_logger.main(["--pre"], stdin_text="<html>", cwd=str(self.work_dir)), "{}")
        self.assertEqual(read_lines(self.work_dir), [])

    def test_a_non_dict_payload_is_silent(self):
        self.assertEqual(progress_logger.main(["--pre"], stdin_text="[1, 2]", cwd=str(self.work_dir)), "{}")
        self.assertEqual(read_lines(self.work_dir), [])

    def test_the_hook_never_writes_state(self):
        before = (self.work_dir / "state.json").read_bytes()
        self.run_hook(["--pre"], session_id="s1", tool_use_id="t1", tool_input={"description": "d"})
        self.assertEqual((self.work_dir / "state.json").read_bytes(), before)


# --- stop_guard -----------------------------------------------------------


class StopGuardTest(_EnvMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.work_dir = make_task(self.root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.work_dir)

    def decide(self, work_dir: Path | None = None) -> dict:
        target = work_dir or self.work_dir
        text = hook_payload(hook_event_name="Stop", session_id="s1", cwd=str(target), stop_hook_active=False)
        return json.loads(stop_guard.main([], stdin_text=text, cwd=str(target)))

    def no_dedup(self) -> None:
        """Treat each call as a separate `Stop` — the tests below are faster than the real window."""
        saved = stop_guard.DEDUP_SECONDS
        stop_guard.DEDUP_SECONDS = 0.0
        self.addCleanup(setattr, stop_guard, "DEDUP_SECONDS", saved)

    def test_off_by_default(self):
        self.assertEqual(self.decide(), {})
        self.assertEqual(read_lines(self.work_dir), [])

    def test_blocks_in_a_working_phase(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        result = self.decide()
        self.assertEqual(result["decision"], "block")
        self.assertIn("memo-a", result["reason"])
        self.assertIn("research", result["reason"])
        self.assertIn("mf next", result["reason"])
        self.assertIn("mf finalize --reason interrupted", result["reason"])
        self.assertEqual([row["event"] for row in events.read_events(self.work_dir)], ["stop_guard_blocked"])

    def test_gives_up_after_two_blocks_in_one_phase(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        self.no_dedup()
        self.assertEqual(self.decide()["decision"], "block")
        self.assertEqual(self.decide()["decision"], "block")
        self.assertEqual(self.decide(), {})
        self.assertEqual(self.decide(), {})
        names = [row["event"] for row in events.read_events(self.work_dir)]
        self.assertEqual(names.count("stop_guard_blocked"), limits.STOP_GUARD_MAX_BLOCKS)
        self.assertEqual(names.count("stop_guard_gave_up"), 1)

    def test_the_counter_is_per_phase(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        self.no_dedup()
        self.decide()
        self.decide()
        self.assertEqual(self.decide(), {})
        state = json.loads((self.work_dir / "state.json").read_text(encoding="utf-8"))
        state["current_phase"] = "drafting"
        (self.work_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.decide()["decision"], "block")

    def test_three_interpreter_variants_of_one_stop_count_once(self):
        """§8.1 ships three exec-form variants; one `Stop` must not spend the whole budget."""
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        for _ in range(3):
            self.assertEqual(self.decide()["decision"], "block")
        names = [row["event"] for row in events.read_events(self.work_dir)]
        self.assertEqual(names, ["stop_guard_blocked"])

    def test_silent_on_a_gate_phase(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        gate = make_task(self.isolate(self.root / "gated"), "memo-gate", phase="plan_approval_pending")
        os.environ["CLAUDE_PROJECT_DIR"] = str(gate)
        self.assertEqual(self.decide(gate), {})
        self.assertEqual(read_lines(gate), [])

    def test_silent_on_a_terminal_phase(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        done = make_task(self.isolate(self.root / "closed"), "memo-done", phase="done")
        os.environ["CLAUDE_PROJECT_DIR"] = str(done)
        self.assertEqual(self.decide(done), {})

    def test_silent_without_an_active_task(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        empty = self.isolate(self.root / "empty")
        self.assertEqual(self.decide(empty), {})

    def test_silent_on_a_v1_task(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        old = make_task(self.isolate(self.root / "legacy"), "memo-v1", schema_version=1)
        os.environ["CLAUDE_PROJECT_DIR"] = str(old)
        self.assertEqual(self.decide(old), {})

    def test_non_json_stdin_is_silent(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        self.assertEqual(stop_guard.main([], stdin_text="nope", cwd=str(self.work_dir)), "{}")

    def test_state_is_not_modified(self):
        os.environ["CLAUDE_PLUGIN_OPTION_STOP_GUARD"] = "true"
        before = (self.work_dir / "state.json").read_bytes()
        self.decide()
        self.assertEqual((self.work_dir / "state.json").read_bytes(), before)


# --- ensure_deps ----------------------------------------------------------


class EnsureDepsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "deps.json"

    def test_writes_the_report(self):
        self.assertEqual(ensure_deps.main(["--out", str(self.target)]), "{}")
        report = json.loads(self.target.read_text(encoding="utf-8-sig"))
        self.assertEqual(report["kind"], "deps")
        self.assertEqual(sorted(report["deps"]), ["docx", "jsonschema", "mistune"])
        for row in report["deps"].values():
            self.assertIsInstance(row["installed"], bool)
        self.assertEqual(report["missing"], sorted(report["missing"]))

    def test_the_report_matches_the_internal_schema(self):
        if not schema.available():
            self.skipTest("jsonschema is not installed")
        ensure_deps.main(["--out", str(self.target)])
        report = json.loads(self.target.read_text(encoding="utf-8-sig"))
        self.assertEqual(schema.validate(report, "internal"), [])

    def test_nothing_is_installed(self):
        source = (PLUGIN_ROOT / "hooks" / "ensure_deps.py").read_text(encoding="utf-8-sig")
        for forbidden in ("import subprocess", "pip install", "os.system", "check_call", "ensurepip"):
            self.assertNotIn(forbidden, source)


class EnsureDepsOptionsMirrorTest(unittest.TestCase):
    """D-91: the hook is the only process that sees `CLAUDE_PLUGIN_OPTION_*`, so it mirrors them."""

    EXTRA = "CLAUDE_PLUGIN_OPTION_SECRET_TOKEN"
    MIRROR_ENV = ("CLAUDE_PLUGIN_DATA", "LOCALAPPDATA", EXTRA) + tuple(
        ensure_deps.option_env_name(key) for key in ensure_deps.OPTION_KEYS
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.env_dir = self.root / "env-data"
        self.default_dir = self.root / "localappdata" / "claude" / "plugin-data" / "memoforge"
        self._saved = {key: os.environ.get(key) for key in self.MIRROR_ENV}
        for key in self.MIRROR_ENV:
            os.environ.pop(key, None)
        os.environ["LOCALAPPDATA"] = str(self.root / "localappdata")
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def option(self, key: str, value: str) -> None:
        os.environ[ensure_deps.option_env_name(key)] = value

    def mirror(self, directory: Path) -> dict:
        return json.loads((directory / "options.json").read_text(encoding="utf-8-sig"))

    def test_only_the_options_the_host_exported_are_written(self):
        self.option("dashboard", "true")
        self.option("output_folder", "  /root/Documents/memoforge  ")
        written = ensure_deps.mirror_options()
        self.assertEqual(len(written), 1, written)
        self.assertEqual(
            self.mirror(self.default_dir),
            {"dashboard": "true", "output_folder": "/root/Documents/memoforge"},
        )

    def test_an_option_variable_outside_the_six_is_not_mirrored(self):
        os.environ[self.EXTRA] = "nope"
        self.option("dashboard", "true")
        ensure_deps.mirror_options()
        self.assertEqual(list(self.mirror(self.default_dir)), ["dashboard"])

    def test_both_plugin_data_dirs_get_the_mirror(self):
        """The hook gets `$CLAUDE_PLUGIN_DATA`; a fresh `mf` shell resolves the default branch."""
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.env_dir)
        self.option("dashboard", "false")
        written = ensure_deps.mirror_options()
        self.assertEqual(len(written), 2, written)
        for directory in (self.env_dir, self.default_dir):
            with self.subTest(directory=str(directory)):
                self.assertEqual(self.mirror(directory), {"dashboard": "false"})

    def test_one_dir_when_the_env_points_at_the_default(self):
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.default_dir)
        self.option("stop_guard", "true")
        self.assertEqual(len(ensure_deps.mirror_options()), 1)

    def test_no_option_writes_nothing_and_never_deletes(self):
        """A file written by `mf config set` must survive a session start with no options at all."""
        self.default_dir.mkdir(parents=True)
        (self.default_dir / "options.json").write_text('{"dashboard": "true"}', encoding="utf-8")
        self.assertEqual(ensure_deps.mirror_options(), [])
        self.assertEqual(self.mirror(self.default_dir), {"dashboard": "true"})

    def test_the_hook_mirrors_and_still_answers_an_empty_object(self):
        self.option("websearch_autoallow", "false")
        self.assertEqual(ensure_deps.main(["--out", str(self.root / "deps.json")]), "{}")
        self.assertEqual(self.mirror(self.default_dir), {"websearch_autoallow": "false"})

    def test_an_empty_option_value_is_not_a_value(self):
        self.option("dashboard", "   ")
        self.assertEqual(ensure_deps.mirror_options(), [])


# --- build_hooks ----------------------------------------------------------


class BuildHooksTest(unittest.TestCase):
    def setUp(self):
        self.document = build_hooks.build(PLUGIN_ROOT)
        self.blocks = [
            (event, block)
            for event, blocks in self.document["hooks"].items()
            for block in blocks
        ]

    def test_the_document_is_valid_json(self):
        text = build_hooks.render(PLUGIN_ROOT)
        self.assertEqual(json.loads(text)["hooks"].keys(), self.document["hooks"].keys())
        self.assertTrue(text.endswith("\n"))

    def test_every_row_of_the_table_is_present(self):
        expected = {
            ("PreToolUse", "^WebFetch$|^mcp__.*fetch$", "permission_gate.py", ("--mode", "fetch")),
            ("PreToolUse", "^WebSearch$", "permission_gate.py", ("--mode", "websearch")),
            ("PreToolUse", "^Bash$|^mcp__workspace__bash$", "permission_gate.py", ("--mode", "bash")),
            ("PreToolUse", "^(Agent|Task)$", "progress_logger.py", ("--pre",)),
            ("PostToolUse", "^mcp__", "progress_logger.py", ("--mcp",)),
            ("SubagentStart", "^memoforge:", "progress_logger.py", ()),
            ("SubagentStop", "^memoforge:", "progress_logger.py", ()),
            ("Stop", None, "stop_guard.py", ()),
            ("SessionStart", "startup|resume", "ensure_deps.py", ()),
            ("PreCompact", None, "progress_logger.py", ("--compact",)),
        }
        found = set()
        for event, block in self.blocks:
            entry = block["hooks"][0]
            args = [part for part in entry["args"] if part != "-3"]
            found.add((event, block.get("matcher"), Path(args[0]).name, tuple(args[1:])))
        self.assertEqual(found, expected)

    def test_every_hook_ships_in_three_interpreter_variants(self):
        for event, block in self.blocks:
            commands = [entry["command"] for entry in block["hooks"]]
            self.assertEqual(commands, ["python", "python3", "py"], (event, block.get("matcher")))
            self.assertEqual(block["hooks"][2]["args"][0], "-3")
            for entry in block["hooks"]:
                self.assertEqual(entry["type"], "command")
                self.assertNotIn("command", set(entry) - {"command", "type", "args", "async"})

    def test_every_script_path_uses_the_plugin_root_placeholder(self):
        for _, block in self.blocks:
            for entry in block["hooks"]:
                script = [part for part in entry["args"] if part.endswith(".py")]
                self.assertEqual(len(script), 1, entry)
                self.assertTrue(script[0].startswith("${CLAUDE_PLUGIN_ROOT}/hooks/"), script)
                self.assertTrue((PLUGIN_ROOT / "hooks" / Path(script[0]).name).is_file(), script)

    def test_async_is_set_exactly_where_the_table_says(self):
        expected = {
            ("PreToolUse", "^(Agent|Task)$"),
            ("PostToolUse", "^mcp__"),
            ("SubagentStart", "^memoforge:"),
            ("SubagentStop", "^memoforge:"),
            ("SessionStart", "startup|resume"),
        }
        found = {
            (event, block.get("matcher"))
            for event, block in self.blocks
            if any(entry.get("async") for entry in block["hooks"])
        }
        self.assertEqual(found, expected)

    def test_stop_and_precompact_have_no_matcher(self):
        for event, block in self.blocks:
            if event in ("Stop", "PreCompact"):
                self.assertNotIn("matcher", block)

    def test_no_inline_fallback_by_default(self):
        document = build_hooks.build(PLUGIN_ROOT)
        for blocks in document["hooks"].values():
            for block in blocks:
                self.assertNotIn("_comment", block)
                for entry in block["hooks"]:
                    self.assertIn("args", entry, "every default hook is exec form")

    def test_inline_fallback_block_is_generated_from_the_allowlist(self):
        document = build_hooks.build(PLUGIN_ROOT, inline_fallback=True)
        blocks = [b for b in document["hooks"]["PreToolUse"] if b.get("_comment")]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["_comment"], build_hooks.INLINE_FALLBACK_COMMENT)
        self.assertEqual(blocks[0]["matcher"], build_hooks.FETCH_MATCHER)
        command = blocks[0]["hooks"][0]["command"]
        self.assertIn("europa.eu", command)
        self.assertNotIn("justia.com", command)
        self.assertNotIn('"', command[len('python -c "') : -1])

    def test_the_inline_fallback_decides_like_the_script(self):
        command = build_hooks.inline_fallback_command(build_hooks.read_allowlist(PLUGIN_ROOT))
        code = command[len('python -c "') : -1]
        cases = {
            '{"tool_input": {"url": "https://edpb.europa.eu/x"}}': True,
            '{"tool_input": {"url": "https://evil-europa.eu/x"}}': False,
            '{"tool_input": {"uri": "https://ico.org.uk/a"}}': True,
            '{"tool_input": {"url": null, "prompt": "https://europa.eu"}}': False,
            '{"tool_input": "x"}': False,
            "not json": False,
        }
        for payload, allowed in cases.items():
            result = subprocess.run(
                [sys.executable, "-c", code], input=payload.encode("utf-8"), capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            inline = json.loads(result.stdout.decode("utf-8") or "{}")
            script = json.loads(permission_gate.main(["--mode", "fetch"], stdin_text=payload))
            self.assertEqual(bool(inline), allowed, payload)
            self.assertEqual(bool(inline), bool(script), payload)

    def test_agent_fallback_matcher_enumerates_the_agents_of_models_md(self):
        names = build_hooks.agent_names(PLUGIN_ROOT)
        self.assertIn("legal-researcher", names)
        self.assertIn("memo-writer", names)
        self.assertEqual(len(names), 14)  # D-223: twelve + the two brief agents
        document = build_hooks.build(PLUGIN_ROOT, agent_fallback=True)
        matcher = document["hooks"]["SubagentStart"][0]["matcher"]
        self.assertTrue(matcher.startswith("^memoforge:("))
        self.assertTrue(matcher.endswith(")$"))
        for name in names:
            self.assertIn(name, matcher)

    def test_agent_fallback_degrades_to_the_prefix_without_models_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(build_hooks.agent_fallback_matcher(Path(tmp)), build_hooks.AGENT_PREFIX_MATCHER)

    def test_check_mode_reports_a_drift(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            target = Path(tmp) / "hooks.json"
            self.assertEqual(build_hooks.main(["--out", str(target), "--root", str(PLUGIN_ROOT)]), 0)
            self.assertEqual(build_hooks.main(["--out", str(target), "--root", str(PLUGIN_ROOT), "--check"]), 0)
            target.write_text("{}\n", encoding="utf-8")
            self.assertEqual(build_hooks.main(["--out", str(target), "--root", str(PLUGIN_ROOT), "--check"]), 1)

    def test_hooks_json_is_generated(self):
        """The committed file must be exactly what the generator produces (§8.1)."""
        current = (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8-sig")
        self.assertEqual(current, build_hooks.render(PLUGIN_ROOT))

    def test_the_generated_file_has_no_v1_leftovers(self):
        text = (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8-sig")
        for legacy in ("mcp__cowork__", "visualize", "python3 -c"):
            self.assertNotIn(legacy, text)


# --- subagent_statusline --------------------------------------------------


class StatusLineTest(unittest.TestCase):
    def payload(self, **task) -> dict:
        base = {"id": "t1", "description": "P3/12 · legal-researcher · statutory", "startTime": 1000.0}
        base.update(task)
        return {"tasks": [base]}

    def test_a_memoforge_task_gets_a_line(self):
        lines = statusline.build_lines(self.payload(), now=1125.0)
        self.assertEqual(lines, [{"id": "t1", "content": "P3/12 · legal-researcher · statutory · 2m05s"}])

    def test_output_is_one_json_object_per_line(self):
        text = statusline.render(self.payload(), now=1010.0)
        self.assertEqual(text.count("\n"), 1)
        self.assertEqual(json.loads(text)["content"].endswith("10s"), True)

    def test_a_foreign_task_is_skipped(self):
        self.assertEqual(statusline.build_lines({"tasks": [{"id": "x", "description": "Explore the repo"}]}), [])

    def test_the_label_is_used_when_the_description_is_not_ours(self):
        lines = statusline.build_lines(
            {"tasks": [{"id": "t1", "description": "generic", "label": "P1/9 · memo-writer · v1", "startTime": 0}]},
            now=61.0,
        )
        self.assertEqual(lines[0]["content"], "P1/9 · memo-writer · v1 · 1m01s")

    def test_epoch_milliseconds_are_understood(self):
        lines = statusline.build_lines(self.payload(startTime=1_700_000_000_000), now=1_700_000_045.0)
        self.assertTrue(lines[0]["content"].endswith("45s"))

    def test_iso_8601_with_z_is_understood(self):
        lines = statusline.build_lines(self.payload(startTime="2026-09-08T12:00:00Z"), now=None)
        self.assertTrue(lines[0]["content"].startswith("P3/12 · "))

    def test_a_missing_start_time_leaves_the_description_alone(self):
        lines = statusline.build_lines(self.payload(startTime=None))
        self.assertEqual(lines[0]["content"], "P3/12 · legal-researcher · statutory")

    def test_a_task_without_an_id_is_skipped(self):
        self.assertEqual(statusline.build_lines({"tasks": [{"description": "P1/2 · x · y"}]}), [])

    def test_elapsed_formats(self):
        self.assertEqual(statusline.format_elapsed(0), "0s")
        self.assertEqual(statusline.format_elapsed(59.9), "59s")
        self.assertEqual(statusline.format_elapsed(60), "1m00s")
        self.assertEqual(statusline.format_elapsed(3599), "59m59s")
        self.assertEqual(statusline.format_elapsed(4020), "1h07m")

    def test_malformed_input_prints_nothing(self):
        self.assertEqual(statusline.main(stdin_text="not json"), "")
        self.assertEqual(statusline.main(stdin_text=""), "")
        self.assertEqual(statusline.main(stdin_text='{"tasks": "x"}'), "")

    def test_the_script_reads_no_files(self):
        source = (PLUGIN_ROOT / "scripts" / "subagent_statusline.py").read_text(encoding="utf-8-sig")
        for forbidden in ("open(", "read_text", "pathlib", "os.environ"):
            self.assertNotIn(forbidden, source)


# --- probe_echo -----------------------------------------------------------


class ProbeEchoTest(unittest.TestCase):
    def test_the_call_is_logged_with_argv_and_the_plugin_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "probe-echo.log"
            payload = hook_payload(hook_event_name="PreToolUse", tool_name="Agent")
            self.assertEqual(probe_echo.main(["--variant", "python", "--out", str(target)], stdin_text=payload), "{}")
            record = json.loads(target.read_text(encoding="utf-8-sig").strip())
        self.assertIn("--variant", record["argv"])
        self.assertIn("CLAUDE_PLUGIN_ROOT", record["env"])
        self.assertEqual(record["hook_event_name"], "PreToolUse")
        self.assertEqual(record["tool_name"], "Agent")

    def test_a_broken_payload_still_logs_the_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "probe-echo.log"
            probe_echo.main(["--out", str(target)], stdin_text="<not json>")
            record = json.loads(target.read_text(encoding="utf-8-sig").strip())
        self.assertIsNone(record["hook_event_name"])
        self.assertEqual(record["stdin_bytes"], len("<not json>"))


# --- plugin manifest and settings -----------------------------------------


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig")
        )

    def test_required_fields_of_section_8_4(self):
        fields = ("name", "displayName", "version", "description", "author", "repository",
                  "homepage", "license", "keywords")
        for field in fields:
            self.assertIn(field, self.manifest)
        self.assertEqual(self.manifest["license"], "MIT")
        self.assertTrue(self.manifest["version"].startswith("2.0.0"))

    def test_user_config_matches_section_8_4(self):
        user_config = self.manifest["userConfig"]
        self.assertEqual(user_config["output_folder"]["type"], "directory")
        self.assertEqual(user_config["writer_model"]["default"], "opus")
        self.assertEqual(user_config["source_review_gate"]["default"], "auto")
        self.assertTrue(user_config["dashboard"]["default"], "D-92: the dashboard is on by default")
        self.assertFalse(user_config["stop_guard"]["default"])
        self.assertTrue(user_config["websearch_autoallow"]["default"])
        # D-152: `citation_style` is the eighth option; `inline` is the default of both templates.
        self.assertEqual(user_config["citation_style"]["default"], "inline")
        # D-169: the two language options; `memo_language` defaults to `en`, `ui_language` to `auto`.
        self.assertEqual(user_config["memo_language"]["default"], "en")
        self.assertEqual(user_config["ui_language"]["default"], "auto")
        self.assertEqual(
            sorted(user_config),
            ["citation_style", "dashboard", "memo_language", "output_folder", "publish_folder",
             "source_review_gate", "stop_guard", "ui_language", "websearch_autoallow", "writer_model"],
        )

    def test_the_manifest_the_chain_and_the_session_hook_list_the_same_options(self):
        manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(list(manifest["userConfig"]), list(task.OPTION_KEYS))
        self.assertEqual(tuple(ensure_deps.OPTION_KEYS), task.OPTION_KEYS)

    def test_the_writer_model_default_is_allowed(self):
        self.assertIn(self.manifest["userConfig"]["writer_model"]["default"], limits.ALLOWED_WRITER_MODELS)

    def test_every_user_config_option_is_declared_in_full(self):
        """`claude plugin validate` rejects an option without `title` (D-85)."""
        for key, option in self.manifest["userConfig"].items():
            for field in ("title", "type", "description"):
                with self.subTest(option=key, field=field):
                    self.assertIn(field, option)
                    self.assertIsInstance(option[field], str)
                    self.assertTrue(option[field].strip())


class PluginSettingsTest(unittest.TestCase):
    def test_settings_json_carries_only_the_subagent_status_line(self):
        settings = json.loads((PLUGIN_ROOT / "settings.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(list(settings), ["subagentStatusLine"])
        self.assertEqual(settings["subagentStatusLine"]["type"], "command")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/subagent_statusline.py", settings["subagentStatusLine"]["command"])


if __name__ == "__main__":
    unittest.main()
