"""Tests for scripts/memoforge/schema.py and the four schemas of slice S1 (ТЗ §6, §9)."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import schema, task  # noqa: E402

S1_SCHEMAS = ("state", "events", "done-marker", "internal")


def valid_state() -> dict:
    return task.build_initial_state(
        task_id="memo-20260101T000000Z-schema-test",
        user_query="q",
        language="en",
        work_dir=Path("/tmp/memo-20260101T000000Z-schema-test"),
        output_folder=Path("/tmp"),
        config={"writer_model": "opus", "source_review_gate": "auto", "intake_max_questions": 5},
        created_at="2026-01-01T00:00:00.000Z",
    )


def valid_event() -> dict:
    return {
        "ts": "2026-01-01T00:00:00.000Z",
        "event": "step_issued",
        "actor": "cli",
        "severity": "info",
        "phase": "research",
        "step_id": "s-017",
        "event_key": None,
        "data": {"attempt": 1},
    }


def valid_done_marker() -> dict:
    return {
        "task_id": "memo-20260101T000000Z-schema-test",
        "step_id": "s-017",
        "attempt": 1,
        "slot": "statutes",
        "input_sha": {"research/plan.json": "a" * 64},
        "output_sha": {"statutes.json": "b" * 64},
    }


def valid_internal() -> dict:
    return {
        "schema_version": 1,
        "kind": "launcher",
        "python_cmd": ["py", "-3"],
        "checked_at": "2026-01-01T00:00:00.000Z",
        "source": "mf.cmd",
    }


class SchemaLoadingTest(unittest.TestCase):
    def test_jsonschema_is_available(self):
        self.assertTrue(schema.available())

    def test_the_four_s1_schemas_exist(self):
        for name in S1_SCHEMAS:
            self.assertTrue(schema.schema_path(name).is_file(), name)
            self.assertIn(name, schema.known_schemas())

    def test_every_schema_is_valid_draft_2020_12(self):
        from jsonschema import Draft202012Validator

        for name in schema.known_schemas():
            document = schema.load_schema(name)
            self.assertEqual(document["$schema"], "https://json-schema.org/draft/2020-12/schema", name)
            Draft202012Validator.check_schema(document)

    def test_refs_are_local_only(self):
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref":
                        self.assertTrue(str(value).startswith("#/$defs/"), value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for name in schema.known_schemas():
            walk(schema.load_schema(name))

    def test_unknown_schema_name(self):
        with self.assertRaises(ValueError):
            schema.load_schema("does-not-exist")
        with self.assertRaises(ValueError):
            schema.schema_path("../evil")

    def test_validate_or_raise_reports_errors(self):
        with self.assertRaises(schema.SchemaValidationError) as ctx:
            schema.validate_or_raise({"schema_version": 1}, "state")
        self.assertTrue(ctx.exception.errors)
        self.assertEqual(ctx.exception.schema_name, "state")


class StateSchemaTest(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(schema.validate(valid_state(), "state"), [])

    def test_positive_with_steps_published_and_progress(self):
        state = valid_state()
        state["steps"] = [
            {
                "step_id": "s-017",
                "kind": "dispatch",
                "phase": "research",
                "issued_at": "2026-01-01T00:00:01.000Z",
                "attempt": 1,
                "reason": "initial",
                "status": None,
                "closed_at": None,
                "result_ref": None,
                "inputs": {"research/plan.json": "a" * 64},
                "agents": [
                    {
                        "slot": "statutes",
                        "agent_type": "memoforge:legal-researcher",
                        "attempt": 1,
                        "status": None,
                        "reported_at": None,
                        "payload_ref": None,
                        "outputs": [
                            {
                                "canonical_path": "research/statutes.json",
                                "work_path": "steps/s-017/a1/statutes/statutes.json",
                                "schema": "research-findings",
                                "agent_sha256": "c" * 64,
                            }
                        ],
                    }
                ],
            }
        ]
        state["published"] = [
            {
                "canonical_path": "research/statutes.json",
                "sha256": "c" * 64,
                "by": "step",
                "step_id": "s-017",
                "at": "2026-01-01T00:00:02.000Z",
            }
        ]
        state["draft_versions"] = [
            {
                "version": 1,
                "path": "drafts/v1.md",
                "sha256": "d" * 64,
                "lint_clean": True,
                "citations_clean": False,
                "checked_at": "2026-01-01T00:00:03.000Z",
            }
        ]
        state["progress"]["route"] = ["research", "research_sufficiency"]
        state["progress"]["total"] = 13
        state["progress"]["active"] = [
            {"slot": "statutes", "agent_type": "memoforge:legal-researcher", "label": "statutes", "started_at": None}
        ]
        self.assertEqual(schema.validate(state, "state"), [])

    def test_negative_missing_required_field(self):
        state = valid_state()
        del state["attempts"]
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_unknown_top_level_field(self):
        state = valid_state()
        state["live_progress"] = {}
        errors = schema.validate(state, "state")
        self.assertTrue(any("live_progress" in error for error in errors))

    def test_negative_unknown_phase(self):
        state = valid_state()
        state["current_phase"] = "mode_pick_pending"
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_schema_version_one(self):
        state = valid_state()
        state["schema_version"] = 1
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_task_id_pattern(self):
        state = valid_state()
        state["task_id"] = "memo-2026-bad slug"
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_unknown_attempts_key(self):
        state = valid_state()
        state["attempts"]["step_retry"] = 1
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_bad_step_kind(self):
        state = valid_state()
        state["steps"] = [
            {"step_id": "s-1", "kind": "magic", "phase": "research", "attempt": 1, "reason": "initial", "status": None}
        ]
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_bad_published_sha(self):
        state = valid_state()
        state["published"] = [
            {"canonical_path": "drafts/v1.md", "sha256": "nope", "by": "command", "at": "2026-01-01T00:00:00.000Z"}
        ]
        self.assertTrue(schema.validate(state, "state"))

    def test_negative_mode_value(self):
        state = valid_state()
        state["mode"] = "turbo"
        self.assertTrue(schema.validate(state, "state"))


class EventsSchemaTest(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(schema.validate(valid_event(), "events"), [])

    def test_positive_hook_event_with_key(self):
        record = valid_event()
        record["event"] = "subagent_stopped"
        record["event_key"] = "f" * 40
        record["step_id"] = None
        record["phase"] = None
        self.assertEqual(schema.validate(record, "events"), [])

    def test_negative_unknown_event_name(self):
        record = valid_event()
        record["event"] = "not_an_event"
        self.assertTrue(schema.validate(record, "events"))

    def test_negative_missing_ts(self):
        record = valid_event()
        del record["ts"]
        self.assertTrue(schema.validate(record, "events"))

    def test_negative_bad_timestamp(self):
        record = valid_event()
        record["ts"] = "yesterday"
        self.assertTrue(schema.validate(record, "events"))

    def test_negative_additional_property(self):
        record = valid_event()
        record["iteration"] = 1
        self.assertTrue(schema.validate(record, "events"))

    def test_negative_bad_severity(self):
        record = valid_event()
        record["severity"] = "critical"
        self.assertTrue(schema.validate(record, "events"))


class DoneMarkerSchemaTest(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(schema.validate(valid_done_marker(), "done-marker"), [])

    def test_positive_empty_sha_maps(self):
        marker = valid_done_marker()
        marker["input_sha"] = {}
        marker["output_sha"] = {}
        self.assertEqual(schema.validate(marker, "done-marker"), [])

    def test_negative_missing_output_sha(self):
        marker = valid_done_marker()
        del marker["output_sha"]
        self.assertTrue(schema.validate(marker, "done-marker"))

    def test_negative_bad_sha_value(self):
        marker = valid_done_marker()
        marker["output_sha"]["statutes.json"] = "XYZ"
        self.assertTrue(schema.validate(marker, "done-marker"))

    def test_negative_attempt_zero(self):
        marker = valid_done_marker()
        marker["attempt"] = 0
        self.assertTrue(schema.validate(marker, "done-marker"))

    def test_negative_additional_property(self):
        marker = valid_done_marker()
        marker["extra"] = True
        self.assertTrue(schema.validate(marker, "done-marker"))


class InternalSchemaTest(unittest.TestCase):
    def test_positive_launcher(self):
        self.assertEqual(schema.validate(valid_internal(), "internal"), [])

    def test_positive_deps(self):
        document = {
            "schema_version": 1,
            "kind": "deps",
            "deps": {"jsonschema": {"installed": True, "version": "4.23.0"}},
        }
        self.assertEqual(schema.validate(document, "internal"), [])

    def test_positive_wrapper_written_cache(self):
        document = json.loads('{"schema_version": 1, "kind": "launcher", "python_cmd": ["python"], "source": "mf.cmd"}')
        self.assertEqual(schema.validate(document, "internal"), [])

    def test_negative_missing_schema_version(self):
        document = copy.deepcopy(valid_internal())
        del document["schema_version"]
        self.assertTrue(schema.validate(document, "internal"))

    def test_negative_empty_python_cmd(self):
        document = valid_internal()
        document["python_cmd"] = []
        self.assertTrue(schema.validate(document, "internal"))

    def test_negative_unknown_kind(self):
        document = valid_internal()
        document["kind"] = "widgets"
        self.assertTrue(schema.validate(document, "internal"))


if __name__ == "__main__":
    unittest.main()
