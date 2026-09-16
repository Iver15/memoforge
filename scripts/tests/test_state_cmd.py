"""Tests for scripts/memoforge/state_cmd.py — `mf state get|validate` (ТЗ §5.2, M2)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import state_cmd, state_io, task  # noqa: E402


def state_args(work_dir: Path, **overrides) -> argparse.Namespace:
    payload = {"workdir": str(work_dir), "path": None, "human": False}
    payload.update(overrides)
    return argparse.Namespace(**payload)


class PathWalkTest(unittest.TestCase):
    DOC = {
        "a": {"b": [10, {"c": "deep"}]},
        "top": 1,
        "null": None,
    }

    def test_no_path_returns_the_whole_document(self):
        self.assertEqual(state_cmd.get_path(self.DOC, None), self.DOC)

    def test_object_key(self):
        self.assertEqual(state_cmd.get_path(self.DOC, "top"), 1)

    def test_array_index(self):
        self.assertEqual(state_cmd.get_path(self.DOC, "a.b.0"), 10)

    def test_negative_array_index(self):
        self.assertEqual(state_cmd.get_path(self.DOC, "a.b.-1"), {"c": "deep"})

    def test_nested_mix(self):
        self.assertEqual(state_cmd.get_path(self.DOC, "a.b.1.c"), "deep")

    def test_a_null_value_is_returned_not_reported_missing(self):
        self.assertIsNone(state_cmd.get_path(self.DOC, "null"))

    def test_missing_key_is_missing(self):
        self.assertIs(state_cmd.get_path(self.DOC, "nope"), state_cmd.MISSING)

    def test_out_of_range_index_is_missing(self):
        self.assertIs(state_cmd.get_path(self.DOC, "a.b.9"), state_cmd.MISSING)

    def test_index_on_an_object_is_missing(self):
        self.assertIs(state_cmd.get_path(self.DOC, "top.0"), state_cmd.MISSING)


class _TaskMixin:
    def make_task(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work_dir = Path(tmp.name) / "memo-20260908T120000Z-state"
        task.create_work_dir_tree(work_dir)
        state = task.build_initial_state(
            task_id=work_dir.name,
            user_query="Is it lawful?",
            language="en",
            work_dir=work_dir,
            output_folder=work_dir.parent,
            config={"reviewer_list": ["logic", "citations"]},
        )
        state_io.create_state(work_dir, state)
        return work_dir


class GetTest(_TaskMixin, unittest.TestCase):
    def test_get_whole_state(self):
        work_dir = self.make_task()
        result = state_cmd.run_get(state_args(work_dir))
        self.assertEqual(result["value"]["schema_version"], 2)

    def test_get_dotted_path(self):
        work_dir = self.make_task()
        result = state_cmd.run_get(state_args(work_dir, path="config.reviewer_list.0"))
        self.assertEqual(result["value"], "logic")

    def test_get_missing_path_is_a_business_error(self):
        work_dir = self.make_task()
        result = state_cmd.run_get(state_args(work_dir, path="config.nope"))
        self.assertEqual(result["errors"], ["path_not_found: config.nope"])

    def test_get_without_a_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = state_cmd.run_get(state_args(Path(tmp)))
        self.assertTrue(result["errors"][0].startswith("state_not_found:"))

    def test_get_on_a_corrupt_state(self):
        work_dir = self.make_task()
        state_io.state_path(work_dir).write_text("{ broken", encoding="utf-8")
        result = state_cmd.run_get(state_args(work_dir))
        self.assertTrue(result["errors"][0].startswith("state_unreadable:"))


class ValidateTest(_TaskMixin, unittest.TestCase):
    def test_a_fresh_task_validates(self):
        work_dir = self.make_task()
        result = state_cmd.run_validate(state_args(work_dir))
        self.assertTrue(result["valid"])
        self.assertEqual(result["current_phase"], "intake_preliminary_research")

    def test_a_schema_violation_is_reported(self):
        work_dir = self.make_task()
        broken = state_io.read_state(work_dir)
        broken["current_phase"] = "not_a_phase"
        state_io.write_json_atomic(state_io.state_path(work_dir), broken)
        result = state_cmd.run_validate(state_args(work_dir))
        self.assertFalse(result["valid"])
        self.assertTrue(any("current_phase" in error for error in result["errors"]))

    def test_a_missing_state_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = state_cmd.run_validate(state_args(Path(tmp)))
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"][0].startswith("state_not_found:"))

    def test_a_corrupt_state_is_reported(self):
        work_dir = self.make_task()
        state_io.state_path(work_dir).write_text("{ broken", encoding="utf-8")
        result = state_cmd.run_validate(state_args(work_dir))
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"][0].startswith("state_unparseable:"))


class CliSurfaceTest(_TaskMixin, unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, dict]:
        from io import StringIO
        from unittest import mock

        from memoforge import cli

        buffer = StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            code = cli.main(list(args))
        printed = buffer.getvalue().strip()
        return code, json.loads(printed) if printed else {}

    def test_state_get_exits_zero(self):
        work_dir = self.make_task()
        code, payload = self.run_cli("state", "get", "--workdir", str(work_dir), "--path", "task_id")
        self.assertEqual(code, 0)
        self.assertEqual(payload["value"], work_dir.name)

    def test_state_validate_of_an_invalid_document_exits_one(self):
        work_dir = self.make_task()
        broken = state_io.read_state(work_dir)
        del broken["attempts"]
        state_io.write_json_atomic(state_io.state_path(work_dir), broken)
        code, payload = self.run_cli("state", "validate", "--workdir", str(work_dir))
        self.assertEqual(code, 1)
        self.assertFalse(payload["valid"])

    def test_state_commands_never_write(self):
        work_dir = self.make_task()
        before = state_io.sha256_file(state_io.state_path(work_dir))
        self.run_cli("state", "get", "--workdir", str(work_dir))
        self.run_cli("state", "validate", "--workdir", str(work_dir))
        self.assertEqual(before, state_io.sha256_file(state_io.state_path(work_dir)))


if __name__ == "__main__":
    unittest.main()
