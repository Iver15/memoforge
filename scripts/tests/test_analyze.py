"""Tests for scripts/memoforge/analyze.py — `events analyze` and `probe metrics` (ТЗ §7.2 F, §0.2).

The six cases of the v1 `scripts/tests/test_analyze_run.py` are carried over onto the v2 journal
(`step_issued`/`agent_returned`/`cli_call`, no `phase_transition`) and joined by the checks the v2
spec adds: `silent_gap` (§7.3), `hooks_absent`/`agent_logs_absent` (§7.2) and G1/G2/G7 (§0.2).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, temp_root  # noqa: E402
from memoforge import analyze, cli, limits  # noqa: E402

BASE = dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=dt.timezone.utc)
REVIEWERS = ["logic", "form", "citations", "counterarguments"]


def at(minutes: float) -> dt.datetime:
    return BASE + dt.timedelta(minutes=minutes)


def iso(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def event(when: dt.datetime, name: str, phase: str | None = None, **data) -> dict:
    return {
        "ts": iso(when),
        "event": name,
        "actor": "cli",
        "severity": "info",
        "phase": phase,
        "step_id": data.get("step_id"),
        "event_key": None,
        "data": data,
    }


def write_file(path: Path, content: str, when: dt.datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))


def write_events(work_dir: Path, records: list[dict], when: dt.datetime) -> None:
    path = work_dir / "events.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))


def write_state(work_dir: Path, state: dict, when: dt.datetime) -> None:
    write_file(work_dir / "state.json", json.dumps(state), when)


def build_slow_run(work_dir: Path) -> None:
    """Journal dies in `revision_loop`; reviewers dispatched one at a time; the score regresses."""
    records = [
        event(at(0), "task_created", "intake_preliminary_research", task_id="memo-slow"),
        event(at(1), "step_issued", "intake_preliminary_research", step_id="s1", attempt=1,
              agents=[{"slot": "a1", "agent_type": "fact-assumption-analyst"}]),
        event(at(5), "step_issued", "research", step_id="s2", attempt=1,
              agents=[{"slot": "r1", "agent_type": "legal-researcher"}]),
        event(at(50), "step_issued", "drafting", step_id="s3", attempt=1,
              agents=[{"slot": "w", "agent_type": "memo-writer"}]),
        # one reviewer per dispatch, 6 minutes apart -> serial
        event(at(62), "step_issued", "revision_loop", step_id="s4", attempt=1,
              agents=[{"slot": "rev1", "agent_type": "logic-reviewer"}]),
        event(at(70), "step_issued", "revision_loop", step_id="s5", attempt=1,
              agents=[{"slot": "rev2", "agent_type": "form-reviewer"}]),
        event(at(78), "step_issued", "revision_loop", step_id="s6", attempt=1,
              agents=[{"slot": "rev3", "agent_type": "citation-auditor"}]),
        event(at(85), "step_issued", "revision_loop", step_id="s7", attempt=1,
              agents=[{"slot": "rev4", "agent_type": "counterargument-reviewer"}]),
    ]
    write_events(work_dir, records, at(85))
    write_state(
        work_dir,
        {
            "schema_version": 2,
            "task_id": "memo-slow",
            "mode": "full",
            "created_at": iso(at(0)),
            "config": {"reviewer_list": REVIEWERS},
            "final_status": "forced_exit_on_v3_with_remaining_issues",
            "final_status_reasons": ["regression_forced_exit"],
            "steps": [{"step_id": f"s{n}", "kind": "dispatch"} for n in range(1, 8)],
            # v2 `iterations[]` (D-45): blockers and coverage, no v1 score fields.
            "iterations": [
                {"iteration": 1, "substance_blockers": 5, "form_blockers": 7, "coverage": REVIEWERS},
                {"iteration": 2, "substance_blockers": 3, "form_blockers": 6, "coverage": REVIEWERS},
                {"iteration": 3, "substance_blockers": 4, "form_blockers": 2, "coverage": REVIEWERS},
            ],
        },
        at(200),
    )
    write_file(work_dir / "drafts" / "v1.md", "draft", at(62))
    for kind, minute in zip(REVIEWERS, (64, 72, 80, 88)):
        write_file(work_dir / "reviews" / f"v1-{kind}.json", '{"verdict":"needs_revision"}', at(minute))


def build_fast_run(work_dir: Path) -> None:
    """Complete journal through `done`; the reviewer round goes out in one dispatch."""
    records = [
        event(at(0), "task_created", "intake_preliminary_research", task_id="memo-fast"),
        event(at(1), "step_issued", "intake_preliminary_research", step_id="s1", attempt=1,
              agents=[{"slot": "a1", "agent_type": "fact-assumption-analyst"}]),
        event(at(3), "agent_returned", "intake_preliminary_research", step_id="s1", slot="a1",
              duration_seconds=110),
        event(at(4), "step_issued", "research", step_id="s2", attempt=1,
              agents=[{"slot": "r1", "agent_type": "legal-researcher"}]),
        event(at(6), "subagent_started", "research", agent_id="x", agent_type="legal-researcher"),
        event(at(8), "agent_log", "research", state="step", detail="statutes done"),
        event(at(9), "step_issued", "drafting", step_id="s3", attempt=1,
              agents=[{"slot": "w", "agent_type": "memo-writer"}]),
        event(at(12), "step_issued", "revision_loop", step_id="s4", attempt=1,
              agents=[{"slot": f"rev{n}", "agent_type": kind} for n, kind in enumerate(REVIEWERS, 1)]),
        # D-43 shape: one `cli_call` per invocation, `bytes_out` = length of the JSON answer.
        event(at(14), "cli_call", "revision_loop", group="next", cmd="", ok=True, exit_code=0,
              bytes_out=1200),
        event(at(15), "step_issued", "export", step_id="s5", attempt=1, agents=[]),
        event(at(16), "cli_call", "done", group="finalize", cmd="", ok=True, exit_code=0,
              bytes_out=300),
    ]
    write_events(work_dir, records, at(16))
    write_state(
        work_dir,
        {
            "schema_version": 2,
            "task_id": "memo-fast",
            "mode": "full",
            "created_at": iso(at(0)),
            "config": {"reviewer_list": REVIEWERS},
            "final_status": "approved_on_v1",
            "current_phase": "done",
            "final_status_reasons": [],
            "steps": [{"step_id": f"s{n}", "kind": "dispatch"} for n in range(1, 6)],
            "iterations": [
                {"iteration": 1, "substance_blockers": 0, "form_blockers": 0, "coverage": REVIEWERS}
            ],
        },
        at(16),
    )
    write_file(work_dir / "drafts" / "v1.md", "draft", at(12))
    for kind, second in zip(REVIEWERS, (0.0, 0.2, 0.4, 0.6)):
        write_file(work_dir / "reviews" / f"v1-{kind}.json", '{"verdict":"approved"}', at(13 + second))
    write_file(work_dir / "logs" / "legal-researcher-r1.log", "start\ndone\n", at(9))


class SlowRunTest(unittest.TestCase):
    def test_detects_truncation_serial_and_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            report = analyze.analyze(work_dir)
        self.assertTrue(report["events_truncated"], "the dark-events gap must be detected")
        self.assertEqual(report["events_died_at_phase"], "revision_loop")
        self.assertTrue(report["serial_dispatch"], "one reviewer per dispatch must flag as serial")
        self.assertGreaterEqual(report["serial_round_count"], 1)
        self.assertEqual(report["regressions_at_iter"], [3])
        self.assertTrue(report["regression_forced_exit"])
        self.assertGreater(report["total_s"], 0)

    def test_serial_round_estimates_recoverable_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            report = analyze.analyze(work_dir)
        row = next(item for item in report["revision_rounds"] if item["iteration"] == 1)
        self.assertTrue(row["serial"])
        self.assertEqual(row["order"], REVIEWERS)
        self.assertTrue(row["monotonic_in_list_order"])
        self.assertGreater(row["savings_est_s"], 0)

    def test_serial_dispatch_group_names_the_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            report = analyze.analyze(work_dir)
        group = next(item for item in report["dispatch_staircases"] if item["phase"] == "revision_loop")
        self.assertEqual(group["dispatches"], 4)
        self.assertEqual(["s4", "s5", "s6", "s7"], group["step_ids"])
        self.assertTrue(group["serial"])


class FastRunTest(unittest.TestCase):
    def test_complete_events_no_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            report = analyze.analyze(work_dir)
        self.assertFalse(report["events_truncated"])
        self.assertEqual(report["serial_round_count"], 0, "a clustered round is not serial")
        self.assertFalse(report["serial_dispatch"])
        self.assertEqual(report["regressions_at_iter"], [])
        self.assertFalse(report["regression_forced_exit"])

    def test_hook_and_agent_channels_are_reported_as_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            report = analyze.analyze(work_dir)
        self.assertFalse(report["hooks_absent"])
        self.assertFalse(report["agent_logs_absent"])

    def test_phase_timeline_covers_every_phase_seen_in_the_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            report = analyze.analyze(work_dir)
        seen = [row["phase"] for row in report["phase_durations"]]
        self.assertEqual(
            seen,
            ["intake_preliminary_research", "research", "drafting", "revision_loop", "export", "done"],
        )


class AbsentChannelsTest(unittest.TestCase):
    def test_hooks_and_agent_logs_absent_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            report = analyze.analyze(work_dir)
        self.assertTrue(report["hooks_absent"])
        self.assertTrue(report["agent_logs_absent"])
        joined = " ".join(report["warnings"])
        self.assertIn("hooks_absent", joined)
        self.assertIn("agent_logs_absent", joined)


class SilentGapTest(unittest.TestCase):
    def _run(self, records: list[dict], last: dt.datetime) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            write_events(work_dir, records, last)
            write_state(work_dir, {"schema_version": 2, "task_id": "memo-x", "steps": []}, last)
            return analyze.analyze(work_dir)

    def test_gap_over_five_minutes_without_step_issued_is_silent(self):
        records = [
            event(at(0), "step_issued", "research", step_id="s1", attempt=1, agents=[]),
            event(at(20), "step_issued", "drafting", step_id="s2", attempt=1, agents=[]),
        ]
        report = self._run(records, at(20))
        self.assertTrue(report["silent_gap"])
        gap = report["silent_gaps"][0]
        self.assertEqual(gap["phase"], "research")
        self.assertGreater(gap["seconds"], limits.SILENT_GAP_SECONDS)

    def test_a_gate_wait_is_not_a_silent_gap(self):
        records = [
            event(at(0), "step_issued", "intake_questions_pending", step_id="s1", attempt=1, agents=[]),
            event(at(40), "gate_answered", "intake_questions_pending", gate="intake"),
            event(at(41), "step_issued", "planning", step_id="s2", attempt=1, agents=[]),
        ]
        report = self._run(records, at(41))
        self.assertFalse(report["silent_gap"])
        self.assertEqual(report["gaps"][0]["reason"], "gate_waiting_for_user")
        self.assertAlmostEqual(report["user_gate_wait_s"], 2400.0)

    def test_a_dense_run_has_no_gaps(self):
        records = [
            event(at(0), "step_issued", "research", step_id="s1", attempt=1, agents=[]),
            event(at(2), "step_issued", "research", step_id="s2", attempt=1, agents=[]),
            event(at(4), "step_issued", "drafting", step_id="s3", attempt=1, agents=[]),
        ]
        report = self._run(records, at(4))
        self.assertEqual(report["gaps"], [])


def agent_log(when: dt.datetime, phase: str, step_id: str, attempt: int, slot: str, state: str) -> dict:
    """One `mf agent log` record, the shape the audited run wrote (§7.2 E)."""
    record = event(when, "agent_log", phase, step_id=step_id, attempt=attempt, slot=slot, state=state)
    record["step_id"] = step_id
    return record


class RunEndTest(unittest.TestCase):
    """D34-25: `events analyze` dates the run by the run, never by its own telemetry line."""

    def _run(self, records: list[dict], last: dt.datetime, state: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            write_events(work_dir, records, last)
            write_state(work_dir, state or {"schema_version": 2, "task_id": "memo-x", "steps": []}, last)
            return analyze.analyze(work_dir)

    def test_run_end_is_the_terminal_step_issued(self):
        records = [
            event(at(0), "task_created", "intake_preliminary_research", task_id="memo-x"),
            event(at(60), "step_issued", "export", step_id="s27", attempt=1, agents=[]),
            event(at(80), "step_issued", "done", step_id="s28", kind="terminal"),
            # Two days later a forensic pass appends its own line — it must not end the run.
            event(at(3000), "cli_call", None, group="events", cmd="analyze", ok=True, bytes_out=14299),
        ]
        report = self._run(records, at(3000))
        self.assertEqual("terminal_step_issued", report["run_end_source"])
        self.assertEqual(80 * 60.0, report["total_s"])

    def test_final_status_write_ends_a_run_without_a_terminal_step(self):
        records = [
            event(at(0), "task_created", "intake_preliminary_research", task_id="memo-x"),
            event(at(30), "state_written", "failed", sha256="x"),
            event(at(900), "cli_call", None, group="events", cmd="analyze", ok=True, bytes_out=10),
        ]
        report = self._run(records, at(900))
        self.assertEqual("final_status", report["run_end_source"])
        self.assertEqual(30 * 60.0, report["total_s"])

    def test_last_event_remains_the_fallback(self):
        records = [
            event(at(0), "task_created", "intake_preliminary_research", task_id="memo-x"),
            event(at(30), "step_issued", "drafting", step_id="s1", attempt=1, agents=[]),
        ]
        report = self._run(records, at(30))
        self.assertIn(report["run_end_source"], ("last_event", "state.json"))

    def test_no_silent_gap_is_accounted_inside_done(self):
        records = [
            event(at(0), "step_issued", "export", step_id="s27", attempt=1, agents=[]),
            event(at(5), "step_issued", "done", step_id="s28", kind="terminal"),
            event(at(3000), "cli_call", "done", group="events", cmd="analyze", ok=True, bytes_out=10),
        ]
        report = self._run(records, at(3000))
        self.assertEqual([], [gap for gap in report["gaps"] if gap["phase"] == "done"])
        self.assertFalse(report["silent_gap"])


class SerialityTest(unittest.TestCase):
    """D34-07: seriality is decided per `(step_id, attempt)` from request/done order."""

    def _run(self, records: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            write_events(work_dir, records, at(120))
            write_state(
                work_dir,
                {
                    "schema_version": 2,
                    "task_id": "memo-x",
                    "config": {"reviewer_list": REVIEWERS},
                    "steps": [],
                },
                at(120),
            )
            return analyze.analyze(work_dir)

    def _reviewer_round(self, offsets: list[float], done_at: list[float]) -> list[dict]:
        records = [
            event(at(0), "step_issued", "revision_loop", step_id="s21", attempt=1,
                  kind="dispatch",
                  agents=[{"slot": slot} for slot in ("logic", "citations", "counterarguments")]),
        ]
        for slot, minute in zip(("logic", "citations", "counterarguments"), offsets):
            records.append(event(at(minute), "subagent_requested", "revision_loop", agent_type=slot))
            records.append(agent_log(at(minute + 0.1), "revision_loop", "s21", 1, slot, "start"))
        for slot, minute in zip(("logic", "counterarguments", "citations"), done_at):
            records.append(agent_log(at(minute), "revision_loop", "s21", 1, slot, "done"))
        records.sort(key=lambda row: row["ts"])
        return records

    def test_one_message_with_three_agents_is_not_serial(self):
        """The audited `s-021`: 22 s between requests, 3 minutes before the first report."""
        report = self._run(self._reviewer_round([0.5, 0.7, 0.9], [3.2, 4.7, 5.5]))
        group = report["dispatch_groups"][0]
        self.assertEqual(("revision_loop", "s21", 1), (group["phase"], group["step_id"], group["attempt"]))
        self.assertEqual(3, group["requests"])
        self.assertEqual(3, group["completions"])
        self.assertFalse(group["serial"])
        self.assertFalse(report["serial_dispatch"])

    def test_each_agent_asked_for_after_the_previous_one_finished_is_serial(self):
        report = self._run(self._reviewer_round([0.5, 6.0, 12.0], [4.0, 10.0, 16.0]))
        self.assertTrue(report["dispatch_groups"][0]["serial"])
        self.assertTrue(report["serial_dispatch"])

    def test_two_passes_of_one_phase_are_two_groups(self):
        """The audited `research`: `s-006` and `s-010` are separate passes, not one serial group."""
        records = [
            event(at(0), "step_issued", "research", step_id="s6", attempt=1, kind="dispatch",
                  agents=[{"slot": "statutes"}]),
            event(at(1), "subagent_requested", "research", agent_type="legal-researcher"),
            agent_log(at(1.2), "research", "s6", 1, "statutes", "start"),
            agent_log(at(13), "research", "s6", 1, "statutes", "done"),
            event(at(14), "step_issued", "research_sufficiency", step_id="s7", attempt=1, kind="dispatch",
                  agents=[{"slot": "sufficiency"}]),
            event(at(19), "step_issued", "research_sufficiency_followup_pending", step_id="s9",
                  attempt=1, kind="gate-text", agents=[]),
            event(at(21), "step_issued", "research", step_id="s10", attempt=1, kind="dispatch",
                  agents=[{"slot": "case_law"}, {"slot": "doctrine"}, {"slot": "statutes"}]),
            event(at(27), "subagent_requested", "research", agent_type="legal-researcher"),
            event(at(27.4), "subagent_requested", "research", agent_type="legal-researcher"),
            event(at(27.8), "subagent_requested", "research", agent_type="legal-researcher"),
            agent_log(at(28), "research", "s10", 1, "case_law", "start"),
            agent_log(at(35), "research", "s10", 1, "case_law", "done"),
            agent_log(at(36), "research", "s10", 1, "doctrine", "done"),
            agent_log(at(43), "research", "s10", 1, "statutes", "done"),
        ]
        report = self._run(records)
        keys = [(group["phase"], group["step_id"], group["attempt"]) for group in report["dispatch_groups"]]
        self.assertEqual([("research", "s10", 1)], keys, "single-agent passes are not a group")
        self.assertFalse(report["dispatch_groups"][0]["serial"])
        self.assertEqual([], report["dispatch_staircases"])
        self.assertFalse(report["serial_dispatch"])

    def test_seriality_is_unknown_without_agent_logs(self):
        records = [
            event(at(0), "step_issued", "revision_loop", step_id="s21", attempt=1, kind="dispatch",
                  agents=[{"slot": "logic"}, {"slot": "citations"}]),
            event(at(1), "subagent_requested", "revision_loop", agent_type="logic-reviewer"),
            event(at(2), "subagent_requested", "revision_loop", agent_type="citation-auditor"),
        ]
        report = self._run(records)
        self.assertIsNone(report["dispatch_groups"][0]["serial"])
        self.assertFalse(report["serial_dispatch"], "unknown is never reported as serial")

    def test_mtime_rounds_carry_no_verdict_when_agent_logs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            write_file(work_dir / "logs" / "logic-reviewer.log", "start\n", at(64))
            report = analyze.analyze(work_dir)
        row = next(item for item in report["revision_rounds"] if item["iteration"] == 1)
        self.assertIsNone(row["serial"], "mtimes are a fallback, not the verdict")
        self.assertEqual("agent_log", row["serial_source"])


class PayloadRejectionTest(unittest.TestCase):
    """D34-18: a refused agent payload is not a broken CLI."""

    def _run(self, records: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            write_events(work_dir, records, at(10))
            write_state(work_dir, {"schema_version": 2, "task_id": "memo-x", "steps": []}, at(10))
            return analyze.analyze(work_dir)

    def test_cli_calls_carrying_a_rejection_are_counted_apart(self):
        records = [
            event(at(0), "cli_call", "research", group="report", cmd="", ok=False, exit_code=2,
                  bytes_out=203, rejection="schema_invalid[research-findings]"),
            event(at(1), "cli_call", "research", group="report", cmd="", ok=False, exit_code=2,
                  bytes_out=145, rejection="input_sha_mismatch: research/sources.json"),
            event(at(2), "cli_call", "research", group="next", cmd="", ok=True, exit_code=0, bytes_out=10),
        ]
        self.assertEqual(2, self._run(records)["agent_payload_rejected"])

    def test_a_journal_without_the_field_counts_zero(self):
        records = [
            event(at(0), "cli_call", "research", group="report", cmd="", ok=False, exit_code=1, bytes_out=203),
        ]
        self.assertEqual(0, self._run(records)["agent_payload_rejected"])


class EdgeCaseTest(unittest.TestCase):
    def test_missing_inputs_are_a_business_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = analyze.run_analyze(
                argparse.Namespace(workdir=tmp, compare=None, human=False)
            )
        self.assertEqual(result["errors"], ["no_events_and_no_state"])

    def test_malformed_lines_are_tolerated_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            (work_dir / "events.jsonl").write_text(
                json.dumps(event(at(0), "task_created", "planning")) + "\n{ not valid json\n",
                encoding="utf-8",
            )
            report = analyze.analyze(work_dir)
        self.assertEqual(report["malformed_event_lines"], 1)
        self.assertEqual(report["n_events"], 1)

    def test_compare_reports_both_runs(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            build_slow_run(Path(a))
            build_fast_run(Path(b))
            result = analyze.run_analyze(
                argparse.Namespace(workdir=None, compare=[a, b], human=True)
            )
        self.assertIn("compare", result["human"])
        self.assertIn("serial reviewer rounds", result["human"])
        self.assertEqual(result["a"]["task_id"], "memo-slow")
        self.assertEqual(result["b"]["task_id"], "memo-fast")

    def test_human_report_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            result = analyze.run_analyze(
                argparse.Namespace(workdir=str(work_dir), compare=None, human=True)
            )
        self.assertIn("memoforge run analysis", result["human"])
        self.assertIn("SERIAL", result["human"])


class MetricsTest(unittest.TestCase):
    def test_g1_sums_read_bytes_and_next_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            read_a = work_dir / "drafts" / "v1.md"
            reads = work_dir / "reads.txt"
            reads.write_text(f"{read_a}\n# a comment\n", encoding="utf-8")
            read_bytes = read_a.stat().st_size
            result = analyze.metrics(work_dir, reads=str(reads))

        self.assertEqual(result["g1"]["read_files"], 1)
        self.assertEqual(result["g1"]["read_bytes"], read_bytes)
        self.assertEqual(
            result["g1"]["total_bytes"], result["g1"]["read_bytes"] + result["g1"]["next_bytes"]
        )
        self.assertEqual(
            result["g1"]["tokens_estimate"], round(result["g1"]["total_bytes"] / 4, 1)
        )
        self.assertEqual(result["g1"]["next_bytes"], 1200)
        self.assertEqual(result["g1"]["next_calls"], 1)
        self.assertEqual(result["g1"]["target"], analyze.G1_TOKEN_TARGET)
        self.assertTrue(result["g1"]["pass"])
        self.assertFalse(result["lower_bound"])
        self.assertEqual(result["lower_bound_reasons"], [])

    def test_g1_accepts_a_json_read_list_with_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            reads = work_dir / "reads.json"
            reads.write_text(
                json.dumps([{"path": "prompts/writer.md", "bytes": 4000}, "missing.md"]),
                encoding="utf-8",
            )
            result = analyze.metrics(work_dir, reads=str(reads))
        self.assertEqual(result["g1"]["read_bytes"], 4000)
        self.assertEqual(result["g1"]["missing_reads"], ["missing.md"])

    def test_g2_counts_cli_calls_and_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            result = analyze.metrics(work_dir)
        self.assertEqual(result["g2"]["step_issued"], 5)
        self.assertEqual(result["g2"]["cli_call"], 2)
        self.assertEqual(result["g2"]["observable_total"], 7)
        self.assertEqual(result["g2"]["target"], analyze.G2_OBSERVABLE_TARGET)
        self.assertTrue(result["g2"]["pass"])

    def test_g7_reports_wall_clock_and_gate_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            result = analyze.metrics(work_dir)
        self.assertAlmostEqual(result["g7"]["wall_clock_s"], 16 * 60, delta=1)
        self.assertTrue(result["g7"]["pass"])

    def test_dry_run_is_marked_as_a_lower_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_fast_run(work_dir)
            result = analyze.metrics(work_dir, dry_run=True)
        self.assertTrue(result["lower_bound"])
        self.assertEqual(result["measurement"], "dry_run")

    def test_missing_next_events_make_g1_an_explicit_lower_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)  # no `cli_call` at all
            reads = work_dir / "reads.txt"
            reads.write_text(f"{work_dir / 'drafts' / 'v1.md'}\n", encoding="utf-8")
            result = analyze.metrics(work_dir, reads=str(reads))
        self.assertEqual(result["g1"]["next_bytes"], 0)
        self.assertEqual(result["g1"]["next_calls"], 0)
        self.assertTrue(result["lower_bound"])
        self.assertTrue(
            any("cli_call" in reason for reason in result["lower_bound_reasons"]),
            result["lower_bound_reasons"],
        )

    def test_an_unmeasured_g1_is_null_not_a_vacuous_pass(self):
        """17a N-01: neither channel observed -> `pass: null`, never `0 <= 25000` reading as met."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)  # no `cli_call` at all, and no --reads below
            result = analyze.metrics(work_dir)
        self.assertEqual(result["g1"]["total_bytes"], 0)
        self.assertTrue(result["lower_bound"])
        self.assertIsNone(result["g1"]["pass"])

    def test_a_measured_lower_bound_still_carries_a_verdict(self):
        """Only the empty measurement is `null`: one observed channel is enough for true/false."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            reads = work_dir / "reads.txt"
            reads.write_text(str(work_dir / "drafts" / "v1.md") + "\n", encoding="utf-8")
            result = analyze.metrics(work_dir, reads=str(reads))
        self.assertGreater(result["g1"]["total_bytes"], 0)
        self.assertTrue(result["lower_bound"])
        self.assertTrue(result["g1"]["pass"])

    def test_metrics_on_a_missing_work_dir_is_a_business_error(self):
        result = analyze.run_metrics(
            argparse.Namespace(
                workdir=str(Path(tempfile.gettempdir()) / "memoforge-nope"),
                reads=None,
                dry_run=False,
                human=False,
            )
        )
        self.assertTrue(result["errors"])


class G1AgainstRealNextAnswersTest(unittest.TestCase):
    """§0.2 G1 / finding 10: `next_bytes` are the bytes `mf next` actually printed."""

    def _next(self, work_dir: Path) -> int:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main(["next", "--workdir", str(work_dir)])
        return len(buffer.getvalue().strip().encode("utf-8"))

    def test_g1_equals_the_independently_counted_answers(self):
        driver = Driver(temp_root(self), slug="g1-real")
        expected = sum(self._next(driver.work_dir) for _ in range(3))
        result = analyze.metrics(driver.work_dir)
        self.assertEqual(3, result["g1"]["next_calls"], "every `next` answer counts, re-issues too")
        self.assertEqual(expected, result["g1"]["next_bytes"])
        self.assertEqual(expected, result["g1"]["total_bytes"])

    def test_the_state_serialization_is_not_the_measurement(self):
        driver = Driver(temp_root(self), slug="g1-not-state")
        for _ in range(3):
            self._next(driver.work_dir)
        steps = driver.state()["steps"]
        serialized = sum(
            len(json.dumps(step, ensure_ascii=False, default=str).encode("utf-8")) for step in steps
        )
        result = analyze.metrics(driver.work_dir)
        self.assertEqual(1, len(steps), "three idempotent `next` calls issue one step")
        self.assertNotEqual(serialized, result["g1"]["next_bytes"])

    def test_the_old_state_based_estimator_is_gone(self):
        self.assertFalse(hasattr(analyze, "next_bytes_total"))


class RegressionModelTest(unittest.TestCase):
    """D-45: regression comes from v2 `iterations[]` and `final_status_reasons`, not from a score."""

    def test_the_v1_score_model_is_gone(self):
        report = analyze.analyze(Path(tempfile.gettempdir()))
        self.assertNotIn("score_trend", report)
        self.assertNotIn("score_regressions_at_iter", report)
        source = (PLUGIN_ROOT / "scripts" / "memoforge" / "analyze.py").read_text(
            encoding="utf-8-sig"
        )
        for field in ("aggregate_score", "blocking_count"):
            self.assertNotIn(field, source)

    def test_more_substance_blockers_at_equal_coverage_is_a_regression(self):
        trend = analyze.blocker_trend(
            {
                "iterations": [
                    {"iteration": 1, "substance_blockers": 2, "form_blockers": 1,
                     "coverage": ["form", "logic"]},
                    {"iteration": 2, "substance_blockers": 4, "form_blockers": 0,
                     "coverage": ["logic", "form"]},
                ]
            }
        )
        self.assertEqual([2], analyze.regression_iterations(trend))

    def test_a_changed_coverage_is_not_a_regression(self):
        trend = analyze.blocker_trend(
            {
                "iterations": [
                    {"iteration": 1, "substance_blockers": 2, "coverage": ["logic"]},
                    {"iteration": 2, "substance_blockers": 4, "coverage": ["logic", "form"]},
                ]
            }
        )
        self.assertEqual([], analyze.regression_iterations(trend))

    def test_a_v2_record_without_the_v1_fields_still_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            report = analyze.analyze(work_dir)
        self.assertEqual(
            [
                {"iteration": 1, "substance_blockers": 5, "form_blockers": 7,
                 "coverage": sorted(REVIEWERS)},
                {"iteration": 2, "substance_blockers": 3, "form_blockers": 6,
                 "coverage": sorted(REVIEWERS)},
                {"iteration": 3, "substance_blockers": 4, "form_blockers": 2,
                 "coverage": sorted(REVIEWERS)},
            ],
            report["blocker_trend"],
        )
        self.assertEqual([3], report["regressions_at_iter"])
        self.assertTrue(report["regression_forced_exit"])
        self.assertIn("regression_forced_exit", " ".join(report["warnings"]))

    def test_the_human_report_renders_the_blocker_trend(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            build_slow_run(work_dir)
            text = analyze.build_report(analyze.analyze(work_dir))
        self.assertIn("revision blocker trend", text)
        self.assertIn("v3=4s/2f", text)


if __name__ == "__main__":
    unittest.main()
