"""Native acceptance rejects missing output and stale/self-reported evidence."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_platform_runtime import collect_evidence, run_session  # noqa: E402


class RuntimeEvidenceTest(unittest.TestCase):
    def setup_evidence(self, root):
        task = root / "tasks/run"
        task.mkdir(parents=True)
        state = {
            "runtime_platform": "codex",
            "language": "ru",
            "ui_language": "ru",
            "current_phase": "intake_questions_pending",
            "steps": [
                {
                    "step_id": "s-2",
                    "kind": "dispatch",
                    "status": "ok",
                    "agents": [
                        {"slot": "analyst", "status": "ok", "outputs": [{"canonical_path": "intake/questions.json"}]}
                    ],
                },
                {"step_id": "s-3", "kind": "gate-text", "generation": 0},
            ],
            "published": [],
        }
        (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
        evidence = {
            "task_path": str(task),
            "loaded_skills": ["memo", "continue", "status", "style", "brief"],
            "gate_kind": "gate-text",
            "step_id": "s-3",
            "generation": 0,
            "errors": [],
        }
        (root / "intake-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        return task, evidence

    def test_missing_analyst_output_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.setup_evidence(root)
            result = collect_evidence("codex", root)
            self.assertIn("missing_analyst_output", result["errors"])

    def test_existing_real_output_and_gate_are_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, _ = self.setup_evidence(root)
            (task / "intake").mkdir()
            (task / "intake/questions.json").write_text('{"must_answer": []}', encoding="utf-8")
            result = collect_evidence("codex", root)
            self.assertEqual(result["errors"], [])
            self.assertTrue(result["dispatch_result"]["analyst_output_exists"])

    def test_qualified_native_skill_names_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, evidence = self.setup_evidence(root)
            (task / "intake").mkdir()
            (task / "intake/questions.json").write_text("{}", encoding="utf-8")
            evidence["loaded_skills"] = ["memoforge-codex:" + name for name in evidence["loaded_skills"]]
            (root / "intake-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            self.assertEqual(collect_evidence("codex", root)["errors"], [])

    def test_agent_errors_survive_missing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, evidence = self.setup_evidence(root)
            (task / "state.json").unlink()
            evidence["errors"] = ["jsonschema_missing"]
            (root / "intake-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            self.assertIn("jsonschema_missing", collect_evidence("codex", root)["errors"])

    def test_unrelated_task_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, evidence = self.setup_evidence(root)
            evidence["task_path"] = "/unrelated/project"
            (root / "intake-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            self.assertIn("task_outside_validation_folder", collect_evidence("codex", root)["errors"])


class SessionTimeoutTest(unittest.TestCase):
    def test_timeout_terminates_native_wrapper_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = Mock(pid=12345)
            process.wait.side_effect = [subprocess.TimeoutExpired(["codex"], 1), 0]
            with (
                patch("verify_platform_runtime.subprocess.Popen", return_value=process),
                patch("verify_platform_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0)),
                patch("verify_platform_runtime.os.killpg", create=True) as killer,
            ):
                result = run_session("codex", Path(tmp), "test", "timeout", 1)
                self.assertEqual(result.get("error"), "timeout")
                if os.name == "posix":
                    killer.assert_called_once()
                else:
                    process.kill.assert_called_once()
