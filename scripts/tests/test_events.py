"""Tests for scripts/memoforge/events.py — the journal writer protocol (ТЗ §7.2 C/C′, §9)."""

from __future__ import annotations

import builtins
import io
import json
import multiprocessing
import os
import re
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, events, limits, state_io  # noqa: E402

APPENDERS = 8

TOOLING_CORE = PLUGIN_ROOT / "lib" / "agent-core" / "tooling-core.md"


def wait_for(path: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.02)
    return False


def worker_append(work_dir: str, index: int, ready: str, go: str) -> None:
    """Child process: one journal append under events.lock."""
    from memoforge import events as child_events

    Path(ready).write_text("ready", encoding="utf-8")
    wait_for(go)
    child_events.append_event(work_dir, "cli_call", f"worker-{index}", {"index": index})


def spawn(target, args):
    ctx = multiprocessing.get_context("spawn")
    return ctx.Process(target=target, args=args)


class EventTypesTest(unittest.TestCase):
    def test_event_types_is_a_set_of_names(self):
        self.assertIsInstance(events.EVENT_TYPES, set)
        for name in events.EVENT_TYPES:
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")

    def test_cli_and_hook_events_of_the_spec_are_declared(self):
        for name in (
            "step_issued",
            "agent_returned",
            "step_autoclosed",
            "cli_call",
            "state_written",
            "task_created",
            "work_dir_resolved",
            "gate_answered",
            "gate_channel_switched",
            "cancel_requested",
            "subagent_requested",
            "subagent_started",
            "subagent_stopped",
            "mcp_call",
            "context_compacted",
            "stop_guard_blocked",
            "stop_guard_gave_up",
            # D-10 (agent-side rate-limit degradation) and D-15 (writer_model fallback)
            "mcp_ratelimit_fallback",
            "writer_model_fallback",
        ):
            self.assertIn(name, events.EVENT_TYPES)

    def test_schema_enum_matches_events_py(self):
        path = PLUGIN_ROOT / "schemas" / "events.schema.json"
        schema_doc = json.loads(path.read_text(encoding="utf-8-sig"))
        enum = schema_doc["$defs"]["event_type"]["enum"]
        self.assertEqual(enum, sorted(enum), "enum must stay sorted")
        self.assertEqual(set(enum), events.EVENT_TYPES)


class AppendTest(unittest.TestCase):
    def test_append_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = events.append_event(tmp, "cli_call", "cli", {"cmd": "next"}, phase="research", step_id="s-1")
            self.assertIsNotNone(record)
            lines = events.read_events(tmp)
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["event"], "cli_call")
            self.assertEqual(lines[0]["actor"], "cli")
            self.assertEqual(lines[0]["phase"], "research")
            self.assertEqual(lines[0]["step_id"], "s-1")
            self.assertEqual(lines[0]["data"], {"cmd": "next"})
            self.assertRegex(lines[0]["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

    def test_journal_path_is_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            events.append_event(tmp, "cli_call", "cli", {})
            self.assertTrue((Path(tmp) / "events.jsonl").is_file())

    def test_the_events_group_never_journals_its_own_cli_call(self):
        """D34-25: `events analyze` read the journal and its telemetry line ended the run."""
        with tempfile.TemporaryDirectory() as tmp:
            skipped = events.append_event(tmp, "cli_call", "cli", {"group": "events", "cmd": "analyze"})
            self.assertIsNone(skipped)
            self.assertFalse((Path(tmp) / "events.jsonl").exists())
            events.append_event(tmp, "cli_call", "cli", {"group": "events", "cmd": "log"})
            self.assertEqual([], events.read_events(tmp))

    def test_every_other_group_is_still_journalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            for group in ("next", "report", "sources", "draft"):
                self.assertIsNotNone(events.append_event(tmp, "cli_call", "cli", {"group": group}))
            self.assertEqual(4, len(events.read_events(tmp)))

    def test_only_cli_call_is_filtered_by_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = events.append_event(tmp, "agent_log", "cli", {"group": "events", "state": "done"})
            self.assertIsNotNone(record)
            self.assertEqual(1, len(events.read_events(tmp)))

    def test_unknown_event_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                events.append_event(tmp, "made_up_event", "cli", {})
            self.assertEqual(events.read_events(tmp), [])

    def test_unknown_severity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                events.append_event(tmp, "cli_call", "cli", {}, severity="fatal")

    def test_one_line_per_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                events.append_event(tmp, "cli_call", "cli", {"i": i})
            raw = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8")
            self.assertEqual(len([line for line in raw.split("\n") if line.strip()]), 3)
            self.assertTrue(raw.endswith("\n"))

    def test_line_is_capped_at_4kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            events.append_event(tmp, "cli_call", "cli", {"blob": "x" * 20000, "other": 1})
            raw = (Path(tmp) / "events.jsonl").read_bytes()
            self.assertLessEqual(len(raw), limits.EVENT_LINE_MAX_BYTES + 1)
            record = events.read_events(tmp)[0]
            self.assertTrue(record["data"]["truncated"])
            self.assertEqual(record["data"]["dropped_keys"], ["blob", "other"])


class DedupTest(unittest.TestCase):
    def test_same_event_key_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = events.append_event(tmp, "subagent_started", "hook", {"n": 1}, event_key="abc123")
            second = events.append_event(tmp, "subagent_started", "hook", {"n": 2}, event_key="abc123")
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(len(events.read_events(tmp)), 1)
            self.assertTrue(events.seen_marker(tmp, "abc123").is_file())

    def test_marker_is_created_after_the_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            events.append_event(tmp, "subagent_stopped", "hook", {}, event_key="k/1")
            marker = events.seen_marker(tmp, "k/1")
            self.assertTrue(marker.is_file())
            self.assertNotIn("/", marker.name)

    def test_a_failing_marker_still_leaves_the_line_in_the_journal(self):
        """§7.2 C step (2) precedes step (3): losing an event is impossible, a duplicate is not."""
        with tempfile.TemporaryDirectory() as tmp:
            real_os_open = os.open
            marker = events.seen_marker(tmp, "boom")

            def failing_open(path, flags, *args, **kwargs):
                if str(path) == str(marker):
                    raise OSError("marker cannot be created")
                return real_os_open(path, flags, *args, **kwargs)

            with mock.patch.object(os, "open", failing_open):
                with self.assertRaises(OSError):
                    events.append_event(tmp, "subagent_started", "hook", {"n": 1}, event_key="boom")

            self.assertFalse(marker.exists(), "the marker must not exist after the crash")
            raw = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8")
            lines = [line for line in raw.split("\n") if line.strip()]
            self.assertEqual(len(lines), 1, "the append happened before the marker")
            self.assertEqual(json.loads(lines[0])["data"], {"n": 1})

            # Without a marker the retry appends a second line — and the reader collapses the pair.
            events.append_event(tmp, "subagent_started", "hook", {"n": 1}, event_key="boom")
            raw = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8")
            self.assertEqual(len([line for line in raw.split("\n") if line.strip()]), 2)
            self.assertEqual(len(events.read_events(tmp)), 1)

    def test_a_failing_append_leaves_no_marker(self):
        """The reverse order would drop the event silently, so the marker must come second."""
        with tempfile.TemporaryDirectory() as tmp:
            real_open = builtins.open
            journal = events.events_path(tmp)

            def failing_open(file, *args, **kwargs):
                if str(file) == str(journal):
                    raise OSError("journal is unwritable")
                return real_open(file, *args, **kwargs)

            with mock.patch.object(builtins, "open", failing_open):
                with self.assertRaises(OSError):
                    events.append_event(tmp, "subagent_started", "hook", {"n": 2}, event_key="nope")

            self.assertFalse(events.seen_marker(tmp, "nope").exists())
            self.assertEqual(events.read_events(tmp), [])

    def test_reader_deduplicates_a_duplicated_line(self):
        """Crash between append and marker leaves a duplicate; the reader removes it (§7.2 C)."""
        with tempfile.TemporaryDirectory() as tmp:
            record = events.build_event("subagent_started", "hook", {"n": 1}, event_key="dup")
            line = json.dumps(record, ensure_ascii=False)
            (Path(tmp) / "events.jsonl").write_text(line + "\n" + line + "\n", encoding="utf-8")
            self.assertEqual(len(events.read_events(tmp)), 1)

    def test_different_keys_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            events.append_event(tmp, "subagent_started", "hook", {}, event_key="a")
            events.append_event(tmp, "subagent_started", "hook", {}, event_key="b")
            self.assertEqual(len(events.read_events(tmp)), 2)


class TornTailTest(unittest.TestCase):
    def test_broken_tail_is_repaired_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            good = json.dumps(events.build_event("cli_call", "cli", {"n": 0}), ensure_ascii=False)
            path.write_text(good + "\n" + '{"ts": "2026-01-01T00:00:00.0', encoding="utf-8")

            events.append_event(tmp, "cli_call", "cli", {"n": 1})

            raw = path.read_text(encoding="utf-8")
            lines = [line for line in raw.split("\n") if line.strip()]
            self.assertEqual(len(lines), 3, "the torn tail stays as its own invalid line")
            self.assertTrue(raw.endswith("\n"))
            records = events.read_events(tmp)
            self.assertEqual([r["data"]["n"] for r in records], [0, 1])

    def test_reader_skips_garbage_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            good = json.dumps(events.build_event("cli_call", "cli", {"n": 7}), ensure_ascii=False)
            path.write_text("not json\n[1,2,3]\n" + good + "\n{\n", encoding="utf-8")
            records = events.read_events(tmp)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["data"]["n"], 7)

    def test_missing_journal_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(events.read_events(tmp), [])


class DocumentedCliCommandTest(unittest.TestCase):
    """D-10: the line `lib/agent-core/tooling-core.md` gives agents must actually run."""

    def documented_command(self) -> str:
        text = TOOLING_CORE.read_text(encoding="utf-8")
        match = re.search(r"`\$\{mf\} (events log [^`]+)`", text)
        self.assertIsNotNone(match, "tooling-core.md no longer documents `mf events log`")
        return match.group(1)

    def run_cli(self, argv: list[str]) -> tuple[int, dict]:
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            code = cli.main(argv)
        return code, json.loads(buffer.getvalue().strip())

    def test_the_documented_line_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = self.documented_command()
            self.assertIn("--actor", command, "D-10 signature: --actor <agent_type> --data '<json>'")
            self.assertIn("--data", command)
            self.assertNotIn("--detail", command)
            filled = (
                command.replace("${work_dir}", Path(tmp).as_posix())
                .replace("<agent_type>", "statutes-researcher")
                .replace('"…"', '"ldh"')
                .replace("…", "30")
            )
            code, payload = self.run_cli(shlex.split(filled))
            self.assertEqual(code, 0, payload)
            self.assertTrue(payload["appended"])
            record = payload["event"]
            self.assertEqual(record["event"], "mcp_ratelimit_fallback")
            self.assertEqual(record["actor"], "statutes-researcher")
            self.assertEqual(record["data"], {"server": "ldh", "retry_after": 30})
            self.assertEqual([e["event"] for e in events.read_events(tmp)], ["mcp_ratelimit_fallback"])


class ConcurrentAppendTest(unittest.TestCase):
    def test_eight_processes_produce_eight_valid_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_io.ensure_lock_files(root)
            ready = [root / f"ready-{i}" for i in range(APPENDERS)]
            go = root / "go"
            procs = [
                spawn(worker_append, (str(root), i, str(ready[i]), str(go)))
                for i in range(APPENDERS)
            ]
            for proc in procs:
                proc.start()
            try:
                for path in ready:
                    self.assertTrue(wait_for(str(path), 120), f"child never started: {path}")
                go.write_text("go", encoding="utf-8")
                for proc in procs:
                    proc.join(180)
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.kill()
                        proc.join(10)

            raw = (root / "events.jsonl").read_text(encoding="utf-8")
            lines = [line for line in raw.split("\n") if line.strip()]
            self.assertEqual(len(lines), APPENDERS)
            for line in lines:
                json.loads(line)
            records = events.read_events(root)
            self.assertEqual(len(records), APPENDERS)
            self.assertEqual(sorted(r["data"]["index"] for r in records), list(range(APPENDERS)))


if __name__ == "__main__":
    unittest.main()
