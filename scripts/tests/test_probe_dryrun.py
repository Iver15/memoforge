"""Tests for scripts/memoforge/probe.py — the end-to-end dry run (ТЗ §9, §0.2 G2, §11)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, namespace, temp_root  # noqa: E402
from memoforge import (  # noqa: E402
    events,
    finalize,
    machine,
    phases,
    preflight,
    probe,
    schema,
    state_io,
)

ENTRY_POINT = PLUGIN_ROOT / "scripts" / "memoforge" / "__main__.py"
"""The file `scripts/mf` and `scripts/mf.cmd` hand to the interpreter (§5.1, §5.6)."""


def _step_results(state: dict, key: str) -> list[dict]:
    """The stored `result` of every closed attempt of one script command key (§2.2)."""
    results = []
    for row in state.get("steps") or []:
        if row.get("kind") != "script" or machine.command_key(row.get("command")) != key:
            continue
        result = (row.get("result_ref") or {}).get("result")
        if isinstance(result, dict):
            results.append(result)
    return results


class DryRunTest(unittest.TestCase):
    """`mf probe dry-run` reaches `done` within the G2 ceilings (D-242: one mode)."""

    def _run(self) -> dict:
        root = temp_root(self)
        return probe.run_dry_run(namespace(workdir=str(root), seed=0))

    def test_the_dry_run_takes_no_mode_and_reports_full(self):
        """D-242: one mode — the report still names it, read from the state of the run."""
        result = probe.run_dry_run(namespace(workdir=str(temp_root(self)), seed=0))
        self.assertTrue(result["ok"], result)
        self.assertEqual("full", result["mode"])
        self.assertEqual("full", state_io.read_state(Path(result["work_dir"]))["mode"])

    def test_dry_run_full_reaches_done(self):
        result = self._run()
        self.assertEqual("done", result["final_phase"], result["invariants"])
        self.assertTrue(result["ok"], result)

    def test_the_dry_run_probe_document_carries_the_ru_servers(self):
        """D-184: the fixture `intake/mcp-probe.json` names `casus` and `fas` as connected."""
        result = self._run()
        document = state_io.read_json(Path(result["work_dir"]) / "intake" / "mcp-probe.json")
        self.assertEqual("mcp__plugin_memoforge_casus", document["namespaces"]["casus"])
        self.assertEqual("mcp__plugin_memoforge_fas-search", document["namespaces"]["fas"])
        self.assertEqual("ok", document["status"]["casus"])
        self.assertEqual("ok", document["status"]["fas"])

    def test_g2_ceilings_hold(self):
        result = self._run()
        self.assertEqual({}, result["g2_exceeded"], result["counts"])
        for name, cap in probe.G2_LIMITS.items():
            self.assertLessEqual(result["counts"][name], cap, name)

    def test_the_script_ceiling_leaves_room_for_one_deviation(self):
        """D-63/D-117: the clean route plus a lint-fix round (+1) and one more iteration (+4)."""
        counts = self._run()["counts"]
        self.assertLessEqual(counts["script"] + 7, probe.G2_LIMITS["script"], counts)

    def test_the_merged_steps_cost_three_script_calls_less(self):
        """D-117: `draft finish` replaces three steps and `docx render` validates itself: 13 -> 10.

        D-147 adds one back: `sources preflight` runs in `planning`, so the clean route is 11.
        """
        result = self._run()
        self.assertEqual(11, result["counts"]["script"], result["counts"])
        keys = [
            machine.command_key(row["command"])
            for row in state_io.read_state(Path(result["work_dir"]))["steps"]
            if row.get("kind") == "script"
        ]
        self.assertEqual(1, keys.count("draft.finish"), keys)
        self.assertEqual(1, keys.count("sources.preflight"), keys)
        for gone in ("draft.anchor", "draft.lint", "draft.audit-citations", "docx.validate"):
            self.assertNotIn(gone, keys)

    def test_the_dry_run_never_opens_a_socket_for_the_preflight(self):
        """D-147: the fixture route runs `sources preflight` offline and restores the environment."""
        with mock.patch.dict(os.environ):
            os.environ.pop(preflight.OFFLINE_ENV, None)
            result = self._run()
            self.assertIsNone(os.environ.get(preflight.OFFLINE_ENV), "the switch outlived the run")
        document = state_io.read_json(Path(result["work_dir"]) / preflight.PREFLIGHT_PATH)
        self.assertTrue(document["offline"])
        self.assertEqual([], [row for row in document["hosts"] if row["status"] != "ok"])

    def test_the_observable_metric_is_estimated_and_stays_under_its_cap(self):
        """D-63 / §0.2 G2: the dry run cannot emit `cli_call` (D-43), so it prints an estimate."""
        result = self._run()
        counts, estimate = result["counts"], result["g2_observed_estimate"]
        journal = events.read_events(Path(result["work_dir"]))
        self.assertEqual([], [row for row in journal if row["event"] == "cli_call"])
        self.assertEqual(
            len([row for row in journal if row["event"] == "step_issued"]),
            estimate["step_issued"],
        )
        self.assertEqual(
            counts["next"] + counts["report"] + counts["script"] + counts["gate"],
            estimate["cli_call_estimate"],
        )
        self.assertEqual(estimate["cli_call_estimate"] + estimate["step_issued"], estimate["total"])
        self.assertEqual(150, estimate["limit"])
        self.assertLessEqual(estimate["total"], estimate["limit"], estimate)
        self.assertEqual({}, result["g2_exceeded"])

    def test_invariants_hold_after_every_step(self):
        result = self._run()
        self.assertEqual([], result["invariants"])
        state = state_io.read_state(Path(result["work_dir"]))
        self.assertEqual([], schema.validate(state, "state"))
        for row in state["published"]:
            path = Path(result["work_dir"]) / row["canonical_path"]
            self.assertTrue(path.is_file(), row["canonical_path"])
            self.assertEqual(row["sha256"], state_io.sha256_file(path))

    def test_no_gate_step_outside_a_gate_phase(self):
        result = self._run()
        for row in result["trace"]:
            if row["kind"] in ("gate-text", "gate-auq"):
                self.assertTrue(phases.is_gate(str(row["phase"])), row)

    def test_deliverable_and_summary_exist_at_the_terminal_phase(self):
        result = self._run()
        work_dir = Path(result["work_dir"])
        self.assertTrue((work_dir / "summary.md").is_file())
        deliverables = list(work_dir.glob("deliverable.*"))
        self.assertTrue(deliverables, "M9: a terminal phase needs a deliverable")

    def test_the_deliverable_is_a_valid_docx(self):
        """§5.5: the fixture draft renders through `renderer.py` — the md fallback is a regression."""
        result = self._run()
        self.assertEqual("done", result["final_phase"], result.get("errors"))
        work_dir = Path(result["work_dir"])
        state = state_io.read_state(work_dir)

        render = _step_results(state, "docx.render")[-1]
        self.assertEqual("docx", render["renderer"], render.get("render_error"))
        self.assertIsNone(render["render_error"])
        # D-117: the render step validated its own output — there is no second step.
        self.assertTrue(render["valid"], render["validation"])
        self.assertIsNone(render["demoted_to"])
        self.assertEqual([], _step_results(state, "docx.validate"))

        finalized = _step_results(state, "finalize")[-1]
        self.assertEqual("deliverable.docx", finalized["deliverable"], finalized)
        kind = finalize.choose_deliverable(work_dir, state, None)["kind"]
        self.assertEqual("docx", kind, "`finalize` answers `deliverable_kind: docx`")
        self.assertTrue((work_dir / "deliverable.docx").is_file())

        banners = [row.get("banner_id") for row in state.get("fallback_banners") or []]
        self.assertNotIn("docx_export_failed", banners)


class ProbeLanguageTest(unittest.TestCase):
    """D-178a: `mf probe dry-run` runs in another memo language and stays green in English."""

    def _run(self, language: str, ui_language: str = "en") -> dict:
        root = temp_root(self)
        return probe.run_dry_run(
            namespace(workdir=str(root), seed=0, language=language, ui_language=ui_language)
        )

    def test_russian_reaches_approved_on_v1(self):
        result = self._run("ru")
        self.assertEqual("done", result["final_phase"], result.get("errors"))
        self.assertEqual("approved_on_v1", result["final_status"], result)
        state = state_io.read_state(Path(result["work_dir"]))
        self.assertEqual("ru", state["language"])

    def test_german_reaches_approved_on_v1(self):
        result = self._run("de")
        self.assertEqual("done", result["final_phase"], result.get("errors"))
        self.assertEqual("approved_on_v1", result["final_status"], result)
        state = state_io.read_state(Path(result["work_dir"]))
        self.assertEqual("de", state["language"])

    def test_the_russian_deliverable_carries_the_russian_risk_line_and_sources(self):
        result = self._run("ru")
        self.assertEqual("approved_on_v1", result["final_status"], result.get("errors"))
        work_dir = Path(result["work_dir"])
        state = state_io.read_state(work_dir)
        deliverable = work_dir / state["final_docx_path"] if state.get("final_docx_path") else None
        if deliverable is not None and deliverable.suffix == ".docx":
            from docx import Document as _DocxDocument

            text = "\n".join(paragraph.text for paragraph in _DocxDocument(deliverable).paragraphs)
        else:
            candidates = list(work_dir.glob("deliverable.*"))
            self.assertTrue(candidates, "M9: a terminal phase needs a deliverable")
            text = candidates[0].read_text(encoding="utf-8-sig")
        self.assertIn("Риск:", text)
        self.assertIn("Источники", text)
        self.assertNotIn("## Sources", text)

    def test_english_dry_runs_are_unchanged(self):
        root = temp_root(self)
        result = probe.run_dry_run(namespace(workdir=str(root), seed=0))
        self.assertEqual("done", result["final_phase"], result.get("errors"))
        self.assertTrue(result["ok"], result)


class CrossedLanguageDryRunTest(unittest.TestCase):
    """Plan 56 task 6 (D-178a): crossed memo/UI languages — the probe answers the plan gate
    with the emitted localized labels, and the deliverable follows the memo language."""

    def _run(self, language: str, ui_language: str) -> dict:
        root = temp_root(self)
        return probe.run_dry_run(
            namespace(workdir=str(root), seed=0, language=language, ui_language=ui_language)
        )

    def test_ru_memo_de_ui_reaches_approved_on_v1(self):
        result = self._run("ru", "de")
        self.assertEqual("done", result["final_phase"], result.get("errors"))
        self.assertEqual("approved_on_v1", result["final_status"], result)
        work_dir = Path(result["work_dir"])
        state = state_io.read_state(work_dir)
        self.assertEqual(("ru", "de"), (state["language"], state["ui_language"]))
        # The plan gate was answered with the emitted German labels, stored canonical.
        iterations = machine.plan_gate_iterations(state)
        self.assertTrue(iterations, "the plan gate left no iteration")
        self.assertEqual("approve", iterations[-1].get("action"), iterations[-1])
        answers = iterations[-1].get("answers") or {}
        self.assertEqual("Approve", answers.get("Plan"), answers)
        self.assertNotIn("Mode", answers, "D-242: the plan gate asks no Mode")
        self.assertNotIn("Genehmigen", json.dumps(answers), answers)
        # The deliverable follows the memo language, not the UI language.
        text = self._deliverable_text(work_dir, state)
        self.assertIn("Риск:", text)
        self.assertIn("Источники", text)
        self.assertNotIn("## Sources", text)
        self.assertNotIn("Risiko:", text)
        self.assertNotIn("## Quellen", text)

    def test_de_memo_ru_ui_reaches_approved_on_v1(self):
        result = self._run("de", "ru")
        self.assertEqual("done", result["final_phase"], result.get("errors"))
        self.assertEqual("approved_on_v1", result["final_status"], result)
        work_dir = Path(result["work_dir"])
        state = state_io.read_state(work_dir)
        self.assertEqual(("de", "ru"), (state["language"], state["ui_language"]))
        iterations = machine.plan_gate_iterations(state)
        self.assertTrue(iterations, "the plan gate left no iteration")
        self.assertEqual("approve", iterations[-1].get("action"), iterations[-1])
        answers = iterations[-1].get("answers") or {}
        self.assertEqual("Approve", answers.get("Plan"), answers)
        self.assertNotIn("Mode", answers, "D-242: the plan gate asks no Mode")
        self.assertNotIn("Утвердить", json.dumps(answers), answers)
        # The deliverable follows the memo language, not the UI language.
        text = self._deliverable_text(work_dir, state)
        self.assertIn("Risiko:", text)
        self.assertIn("Quellen", text)
        self.assertNotIn("## Sources", text)
        self.assertNotIn("Риск:", text)
        self.assertNotIn("Источники", text)

    @staticmethod
    def _deliverable_text(work_dir: Path, state: dict) -> str:
        """The finished deliverable as text (docx paragraphs, else the md fallback)."""
        deliverable = work_dir / state["final_docx_path"] if state.get("final_docx_path") else None
        if deliverable is not None and deliverable.suffix == ".docx":
            from docx import Document as _DocxDocument

            return "\n".join(paragraph.text for paragraph in _DocxDocument(deliverable).paragraphs)
        candidates = list(work_dir.glob("deliverable.*"))
        assert candidates, "M9: a terminal phase needs a deliverable"
        return candidates[0].read_text(encoding="utf-8-sig")

    def test_the_plan_gate_questions_follow_the_ui_language_not_the_memo_language(self):
        for language, ui_language, yes, no in (
            ("ru", "de", "Genehmigen", "Утвердить"),
            ("de", "ru", "Утвердить", "Genehmigen"),
        ):
            with self.subTest(language=language, ui_language=ui_language):
                driver = Driver(temp_root(self), slug=f"crossed-{language}-{ui_language}")
                state_io.write_state(
                    driver.work_dir,
                    lambda current: current.update(
                        {"language": language, "ui_language": ui_language}
                    ),
                )
                action = driver.run_until("plan_approval_pending")
                self.assertEqual("gate-auq", action["kind"], action)
                labels = [
                    option["label"]
                    for question in action["questions"]
                    for option in question["options"]
                ]
                self.assertIn(yes, labels, action["questions"])
                self.assertNotIn(no, json.dumps(action["questions"], ensure_ascii=False))
                fallback = action["text_fallback"]
                self.assertIn(yes, fallback)
                self.assertNotIn(no, fallback)
                self.assertNotIn("Approve this research plan?", fallback)


class SufficiencyFollowupProbeTest(unittest.TestCase):
    """Plan 56 task 6: a fixture sufficiency verdict that opens gate 7 under `ui_language=ru`.

    The emitted text is Russian and the fixture answer `proceed` closes the gate — the
    text channel is untranslated by design, so `GATE_REPLIES` keeps the canonical token.
    """

    SUFFICIENCY = {
        "reviewer": "research_sufficiency",
        "overall_verdict": "targeted_followup_needed",
        "blocking_gaps": [
            {
                "gap": "The retention period the client applies is unknown.",
                "target": "user",
                "status": "missing",
                "why_blocking": "The conclusion turns on it.",
                "followup_question": {
                    "question": "Какой срок хранения применяет клиент?",
                    "header": "Period",
                    "options": [
                        {"label": "Пять лет", "description": "Законодательный минимум."},
                        {"label": "Семь лет", "description": "Налоговое правило."},
                    ],
                    "default_assumption_if_skipped": "Семь лет.",
                },
            },
        ],
        "drafting_warnings": [],
    }

    def test_gate_7_under_ru_ui_prints_russian_and_proceed_closes_it(self):
        driver = Driver(temp_root(self), slug="gate-7-ru")
        state_io.write_state(
            driver.work_dir, lambda current: current.update({"ui_language": "ru"})
        )
        action = driver.run_until("research_sufficiency")
        agent = action["agents"][0]
        target = driver.work_dir / agent["expected_outputs"][0]["work_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(target, self.SUFFICIENCY)
        machine.run_agent_log(
            namespace(
                workdir=str(driver.work_dir),
                step=action["step_id"],
                attempt=action["attempt"],
                slot=agent["slot"],
                state="done",
                detail=None,
                mcp=None,
            )
        )
        driver.report(action["step_id"], action["attempt"], agent=agent["slot"])
        gate = driver.next()
        while gate["kind"] == "script":
            driver.act(gate)
            gate = driver.next()
        self.assertEqual("research_sufficiency_followup_pending", gate["phase"])
        self.assertEqual("gate-text", gate["kind"])
        self.assertIn("оставило пробелы", gate["text"])
        self.assertIn("`1A 2C 3: свободный текст`", gate["text"])
        self.assertNotIn("Research left gaps", gate["text"])
        # The agent-written fields are Russian (only the internal `header` stays English);
        # none of the English fixture prose leaks into the emitted gate.
        self.assertIn("Какой срок хранения применяет клиент?", gate["text"])
        self.assertIn("Пять лет", gate["text"])
        self.assertIn("Семь лет", gate["text"])
        self.assertIn("Законодательный минимум.", gate["text"])
        self.assertIn("Налоговое правило.", gate["text"])
        self.assertIn("Если пропустить, примем: Семь лет.", gate["text"])
        for english in (
            "Which retention period does the client apply today?",
            "Five years",
            "Seven years",
            "Statutory minimum.",
            "Tax rule.",
        ):
            self.assertNotIn(english, gate["text"])
        self.assertEqual("proceed", probe.GATE_REPLIES["research_sufficiency_followup_pending"])
        driver.act(gate)
        state = driver.state()
        self.assertEqual("answered", (state.get("sufficiency_followup") or {}).get("status"))


class EntryPointTest(unittest.TestCase):
    """§5.1/§5.5: the CLI runs `__main__.py` as a plain file — imports must behave as in-process.

    `scripts/mf` and `scripts/mf.cmd` execute the entry point by path, which puts its own folder on
    `sys.path`; the internal `memoforge/docx/` package then shadows python-docx and every render of
    the export phase degrades to the markdown fallback. A test that only calls `cli.main` in-process
    never sees it, so this one really runs the command in a subprocess.
    """

    def test_docx_render_through_the_entry_point_produces_a_docx(self):
        driver = Driver(temp_root(self), slug="entry-point")
        action = None
        for _ in range(60):
            action = driver.next()
            self.assertNotIn("errors", action, action)
            if action.get("kind") == "script" and machine.command_key(action["command"]) == "docx.render":
                break
            driver.act(action)
        else:  # pragma: no cover - the export phase is always reached
            self.fail("the dry run never reached `docx render`")

        command = [sys.executable, str(ENTRY_POINT)] + [str(token) for token in action["command"][1:]]
        process = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, process.returncode, process.stderr)
        payload = json.loads(process.stdout)
        self.assertIsNone(payload["render_error"], payload["render_error"])
        self.assertEqual("docx", payload["renderer"])
        self.assertTrue((driver.work_dir / payload["docx"]).is_file())


class DryRunErrorsTest(unittest.TestCase):
    """D-52: an unsuccessful dry run answers `errors[]`, which is what makes the CLI exit 1 (§9)."""

    def test_a_run_that_never_reaches_done_is_an_error(self):
        self.assertEqual(["final_phase: research"], probe.dry_run_errors("research", {}, []))

    def test_an_exceeded_g2_ceiling_is_an_error(self):
        errors = probe.dry_run_errors("done", {"next": 61}, [])
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith("g2_exceeded: next=61"), errors)

    def test_an_exceeded_observable_estimate_is_an_error(self):
        """D-63: the §0.2 cap of 150 fails the run the same way a modelled ceiling does."""
        errors = probe.dry_run_errors("done", {probe.G2_OBSERVED_KEY: 151}, [])
        self.assertEqual([f"g2_exceeded: {probe.G2_OBSERVED_KEY}=151 > 150"], errors)

    def test_a_run_over_the_observable_cap_is_not_ok(self):
        root = temp_root(self)
        with mock.patch.object(probe, "G2_OBSERVED_LIMIT", 1):
            result = probe.run_dry_run(namespace(workdir=str(root), seed=0))
        self.assertFalse(result["ok"], result["counts"])
        self.assertIn(probe.G2_OBSERVED_KEY, result["g2_exceeded"])
        self.assertTrue(
            any(row.startswith(f"g2_exceeded: {probe.G2_OBSERVED_KEY}=") for row in result["errors"]),
            result["errors"],
        )

    def test_every_broken_invariant_is_an_error(self):
        errors = probe.dry_run_errors("done", {}, ["state_invalid: x", "published_drift: y"])
        self.assertEqual(["invariant: state_invalid: x", "invariant: published_drift: y"], errors)

    def test_a_clean_finished_run_has_no_errors(self):
        self.assertEqual([], probe.dry_run_errors("done", {}, []))

    def test_a_dry_run_that_stops_before_done_is_not_ok_and_reports_it(self):
        """A truncated loop is the cheapest real failure: `ok: false` must carry `errors[]`."""
        root = temp_root(self)
        with mock.patch.object(probe, "MAX_LOOP", 1):
            result = probe.run_dry_run(namespace(workdir=str(root), seed=0))
        self.assertFalse(result["ok"], result)
        self.assertNotEqual("done", result["final_phase"])
        self.assertTrue(
            any(row.startswith("final_phase: ") for row in result["errors"]), result["errors"]
        )

    def test_a_successful_dry_run_carries_no_errors_key(self):
        result = probe.run_dry_run(namespace(workdir=str(temp_root(self)), seed=0))
        self.assertTrue(result["ok"], result["invariants"])
        self.assertNotIn("errors", result)


class ProbeStubTest(unittest.TestCase):
    """§11: `mf probe P<n>` prints the procedure and never executes it."""

    def test_every_probe_of_the_table_is_registered(self):
        self.assertEqual(
            {"P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P11"}, set(probe.PROBES)
        )

    def test_probe_prints_the_procedure_without_running_it(self):
        result = probe.run_probe(namespace(name="P4"))
        self.assertFalse(result["executed"])
        self.assertIn("AskUserQuestion", result["question"])
        self.assertIn("Method:", result["text"])
        self.assertIn("v2-probes.md", result["text"])

    def test_unknown_probe_is_an_error(self):
        result = probe.run_probe(namespace(name="P42"))
        self.assertIn("errors", result)

    def test_probe_document_lists_every_probe(self):
        document = (PLUGIN_ROOT / "docs" / "probes" / "v2-probes.md").read_text(encoding="utf-8-sig")
        for name in probe.PROBES:
            self.assertIn(f"| {name} ", document)


if __name__ == "__main__":
    unittest.main()
