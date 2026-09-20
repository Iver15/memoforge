"""Tests for scripts/memoforge/cli.py — one `cli_call` per invocation (ТЗ §0.2 G2, §7.2 C′, D-43)."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, namespace, temp_root  # noqa: E402
from memoforge import cli, events, machine, probe, sources, state_io  # noqa: E402

PACKAGE = PLUGIN_ROOT / "scripts" / "memoforge"

DRAFT = (
    "# Memo\n\n"
    "## 1. Executive summary\n\n"
    "Risk: low.\n\n"
    "## 2. Conclusion and recommendations\n\n"
    "Nothing to add.\n\n"
    "<!-- sources: generated -->\n"
)


def run_cli(*argv: str) -> tuple[int, str]:
    """Run `cli.main` with stdout captured; returns `(exit_code, stdout)`."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(list(argv))
    return code, buffer.getvalue()


def calls(work_dir: Path) -> list[dict]:
    return [row for row in events.read_events(work_dir) if row["event"] == "cli_call"]


def issue_step(work_dir: Path, step_id: str, attempt: int = 1) -> None:
    """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

    def mutator(state: dict) -> None:
        rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
        rows.append(
            {
                "step_id": step_id,
                "kind": "script",
                "phase": state.get("current_phase"),
                "attempt": attempt,
                "reason": "initial",
                "issued_at": "2026-01-01T00:00:00.000Z",
                "status": None,
            }
        )
        state["steps"] = rows

    state_io.write_state(work_dir, mutator)


def modules_emitting_cli_call() -> list[str]:
    """Package modules that pass `"cli_call"` to `append_event` — the grep of D-43, done on the AST."""
    found: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "append_event":
                continue
            literals = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
            if "cli_call" in literals:
                found.add(path.relative_to(PACKAGE).as_posix())
    return sorted(found)


class CliCallEventTest(unittest.TestCase):
    """D-43: `cli.main` writes exactly one `cli_call` per invocation with a known `--workdir`."""

    def setUp(self):
        self.driver = Driver(temp_root(self), slug="cli-call")
        self.work_dir = self.driver.work_dir

    def test_next_logs_one_call_with_the_size_of_its_answer(self):
        code, out = run_cli("next", "--workdir", str(self.work_dir))
        self.assertEqual(cli.EXIT_OK, code)
        rows = calls(self.work_dir)
        self.assertEqual(1, len(rows), rows)
        data = rows[0]["data"]
        self.assertEqual("next", data["group"])
        self.assertEqual("", data["cmd"])
        self.assertTrue(data["ok"])
        self.assertEqual(0, data["exit_code"])
        self.assertEqual(len(out.strip().encode("utf-8")), data["bytes_out"])

    def test_a_grouped_command_logs_its_group_and_cmd(self):
        run_cli("state", "get", "--workdir", str(self.work_dir))
        rows = calls(self.work_dir)
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("state", rows[0]["data"]["group"])
        self.assertEqual("get", rows[0]["data"]["cmd"])

    def test_sources_fetch_is_reachable_and_journals_one_call(self):
        """D-149: `mf sources fetch` is registered, and a refused host is a content rejection."""
        code, out = run_cli(
            "sources", "fetch", "--workdir", str(self.work_dir), "--url", "https://example.org/x"
        )
        self.assertEqual(cli.EXIT_ERROR, code)
        self.assertEqual(["host_not_allowed: example.org"], json.loads(out.strip())["errors"])
        rows = calls(self.work_dir)
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("sources", rows[0]["data"]["group"])
        self.assertEqual("fetch", rows[0]["data"]["cmd"])
        self.assertEqual("host_not_allowed: example.org", rows[0]["data"]["rejection"])

    def test_a_refused_fetch_journals_no_credential(self):
        """A6/D-192: `rejection` is exported in `_run/events.jsonl`, so it carries no address."""
        code, out = run_cli(
            "sources",
            "fetch",
            "--workdir",
            str(self.work_dir),
            "--url",
            "https://user:TESTTOKEN@mcp.casus.legal/case/1?t=TESTTOKEN",
        )
        self.assertEqual(cli.EXIT_ERROR, code)
        self.assertEqual(
            ["userinfo_not_allowed: https://mcp.casus.legal/case/1"], json.loads(out.strip())["errors"]
        )
        rejection = calls(self.work_dir)[0]["data"]["rejection"]
        self.assertEqual("userinfo_not_allowed: https://mcp.casus.legal/case/1", rejection)
        self.assertNotIn("TESTTOKEN", rejection)
        self.assertNotIn("?", rejection)
        self.assertNotIn("@", rejection)

    def test_a_failing_command_is_logged_with_its_exit_code(self):
        code, _ = run_cli(
            "report", "--workdir", str(self.work_dir), "--step", "s-999", "--attempt", "1"
        )
        self.assertEqual(cli.EXIT_ERROR, code)
        rows = calls(self.work_dir)
        self.assertEqual(1, len(rows), rows)
        self.assertFalse(rows[0]["data"]["ok"])
        self.assertEqual(1, rows[0]["data"]["exit_code"])

    def test_a_content_rejection_carries_the_first_error_line(self):
        """D-118: exit 1 alone cannot tell a refused payload from a broken CLI — `rejection` can."""
        code, _ = run_cli(
            "report", "--workdir", str(self.work_dir), "--step", "s-999", "--attempt", "1"
        )
        self.assertEqual(cli.EXIT_ERROR, code, "the exit code does not change")
        data = calls(self.work_dir)[0]["data"]
        self.assertEqual("unknown_step: s-999", data["rejection"])

    def test_a_successful_call_carries_no_rejection(self):
        run_cli("next", "--workdir", str(self.work_dir))
        self.assertNotIn("rejection", calls(self.work_dir)[0]["data"])

    def test_the_rejection_is_the_first_error_flattened_and_capped(self):
        long_error = "input_sha_mismatch: research/sources.json\n" + "x" * 500
        self.assertEqual(
            cli.REJECTION_MAX_CHARS,
            len(cli.rejection_of({"errors": [long_error, "second"]})),
        )
        self.assertTrue(cli.rejection_of({"errors": [long_error]}).startswith("input_sha_mismatch:"))
        self.assertNotIn("\n", cli.rejection_of({"errors": [long_error]}))
        self.assertIsNone(cli.rejection_of({"ok": True}))
        self.assertIsNone(cli.rejection_of({"errors": []}))

    def test_a_repeated_no_op_call_is_counted_again(self):
        run_cli("next", "--workdir", str(self.work_dir))
        run_cli("next", "--workdir", str(self.work_dir))
        rows = calls(self.work_dir)
        self.assertEqual(2, len(rows), rows)
        self.assertEqual(["next", "next"], [row["data"]["group"] for row in rows])

    def test_human_output_is_measured_as_the_json_answer(self):
        _, human = run_cli("--human", "next", "--workdir", str(self.work_dir))
        rows = calls(self.work_dir)
        self.assertEqual(1, len(rows))
        self.assertNotEqual(len(human.strip().encode("utf-8")), rows[0]["data"]["bytes_out"])
        self.assertGreater(rows[0]["data"]["bytes_out"], 0)

    def test_a_work_dir_without_state_is_not_journalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("events", "analyze", "--workdir", tmp)
            self.assertFalse((Path(tmp) / "events.jsonl").exists())

    def test_handlers_do_not_emit_their_own_cli_call(self):
        """Finding 11: the emission lives in `cli.main` alone, not in the command handlers."""
        machine.run_next(namespace(workdir=str(self.work_dir)))
        self.assertEqual([], calls(self.work_dir))
        action = machine.run_next(namespace(workdir=str(self.work_dir)))
        machine.run_report(
            namespace(
                workdir=str(self.work_dir),
                step=action["step_id"],
                attempt=action["attempt"],
                agent=None,
                status="fail",
                answers=None,
                generation=None,
                stdout=None,
            )
        )
        self.assertEqual([], calls(self.work_dir))


class OneCallPerInvocationTest(unittest.TestCase):
    """D-43 guard: a real `cli.main` run journals its own `cli_call` and nothing adds a second."""

    def prepare(self, slug: str) -> Path:
        driver = Driver(temp_root(self), slug=slug)
        return driver.work_dir

    def argv(self, group: str, cmd: str, work_dir: Path) -> list[str]:
        """The invocation of one command, with whatever state it needs already in place."""
        if (group, cmd) == ("draft", "anchor"):
            draft = work_dir / "drafts" / "v1.md"
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text(DRAFT, encoding="utf-8")
            issue_step(work_dir, "s-anchor")
            return [
                "draft", "anchor",
                "--workdir", str(work_dir),
                "--step", "s-anchor",
                "--attempt", "1",
                "--draft", "drafts/v1.md",
            ]
        return [group] + ([cmd] if cmd else []) + ["--workdir", str(work_dir)]

    def test_each_command_journals_exactly_one_cli_call(self):
        for group, cmd in (("next", ""), ("draft", "anchor"), ("finalize", ""), ("task", "list")):
            label = f"{group} {cmd}".strip()
            with self.subTest(command=label):
                work_dir = self.prepare("one-" + label.replace(" ", "-"))
                argv = self.argv(group, cmd, work_dir)
                code, _ = run_cli(*argv)
                rows = calls(work_dir)
                self.assertEqual(cli.EXIT_OK, code, rows)
                self.assertEqual(1, len(rows), f"{label} journalled {len(rows)} cli_call events: {rows}")
                self.assertEqual(group, rows[0]["data"]["group"])
                self.assertEqual(cmd, rows[0]["data"]["cmd"])
                self.assertEqual("cli", rows[0]["actor"])

    def test_the_only_emitter_in_the_package_is_cli_py(self):
        """The «grep» of D-43: `append_event(..., "cli_call", ...)` lives in `cli.py` alone."""
        self.assertEqual(["cli.py"], modules_emitting_cli_call())


class DryRunExitCodeTest(unittest.TestCase):
    """D-52 / finding 16: CI reads the exit code, so an `ok: false` dry run must not exit 0."""

    def test_a_successful_dry_run_exits_0(self):
        code, out = run_cli("probe", "dry-run", "--mode", "brief", "--workdir", str(temp_root(self)))
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"], payload["invariants"])
        self.assertEqual(cli.EXIT_OK, code)

    def test_a_dry_run_that_fails_its_invariants_exits_1(self):
        with mock.patch.object(probe, "MAX_LOOP", 1):
            code, out = run_cli(
                "probe", "dry-run", "--mode", "brief", "--workdir", str(temp_root(self))
            )
        payload = json.loads(out.strip())
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"], payload)
        self.assertEqual(cli.EXIT_ERROR, code)


class CommandLabelTest(unittest.TestCase):
    def test_labels_come_from_the_parsed_namespace(self):
        parser = cli.build_parser()
        args = parser.parse_args(["task", "list"])
        self.assertEqual(("task", "list"), cli.command_labels(args))
        args = parser.parse_args(["next", "--workdir", "."])
        self.assertEqual(("next", ""), cli.command_labels(args))

    def test_sources_fetch_parses_the_options_of_d_149(self):
        args = cli.build_parser().parse_args(
            [
                "sources", "fetch",
                "--workdir", ".",
                "--url", "https://publications.europa.eu/resource/celex/32016R0679",
                "--accept", "application/xhtml+xml",
                "--lang", "eng",
                "--out", "statutes/gdpr.xhtml",
                "--layer", "statutes",
            ]
        )
        self.assertEqual(("sources", "fetch"), cli.command_labels(args))
        self.assertIs(sources.run_fetch, args.func)
        self.assertEqual("application/xhtml+xml", args.accept)
        self.assertEqual("eng", args.lang)
        self.assertEqual("statutes/gdpr.xhtml", args.out)
        self.assertEqual("statutes", args.layer)

    def test_sources_save_parses_the_options_of_d_199(self):
        args = cli.build_parser().parse_args(
            [
                "sources", "save",
                "--workdir", ".",
                "--layer", "case_law",
                "--title", "ВС РФ, определение № 5-КГ25-14-К2",
                "--citation", "Определение ВС РФ от 04.03.2025 № 5-КГ25-14-К2",
                "--tier", "critical",
                "--url", "https://vsrf.ru/stor_pdf.php?id=1",
                "--id", "vs-act",
                "--meta", "{}",
                "--identifiers", "{}",
                "--expect-number", "5-КГ25-14-К2",
                "--expect-date", "2025-03-04",
                "--expect-article", "152",
                "--method", "POST",
                "--json", '{"urn": "x"}',
                "--accept", "text/html",
                "--lang", "ru",
                "--timeout", "5",
            ]
        )
        self.assertEqual(("sources", "save"), cli.command_labels(args))
        self.assertIs(sources.run_save, args.func)
        self.assertEqual("case_law", args.layer)
        self.assertEqual("critical", args.tier)
        self.assertEqual("vs-act", args.id)
        self.assertEqual("5-КГ25-14-К2", args.expect_number)
        self.assertEqual("2025-03-04", args.expect_date)
        self.assertEqual("152", args.expect_article)
        self.assertEqual('{"urn": "x"}', args.json_body)
        self.assertEqual("ru", args.lang)
        self.assertIsNone(args.resolve)

    def test_save_takes_either_a_url_or_a_resolver_but_not_both(self):
        """D-199: `--resolve` is declared here and is mutually exclusive with `--url`."""
        parser = cli.build_parser()
        base = ["sources", "save", "--workdir", ".", "--layer", "case_law", "--title", "t", "--citation", "c"]
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(base + ["--url", "https://sudact.ru/x", "--resolve", "vsrf"])
            with self.assertRaises(SystemExit):
                parser.parse_args(base)
        args = parser.parse_args(base + ["--resolve", "sudact"])
        self.assertEqual("sudact", args.resolve)
        self.assertEqual("", args.url)

    def test_bytes_out_matches_the_emitted_payload(self):
        result = {"a": "ю", "b": [1, 2]}
        payload = json.dumps(result, ensure_ascii=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.emit(result, human=False, payload=payload)
        self.assertEqual(payload + "\n", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
