"""Tests for scripts/memoforge/task.py — new/resolve/list/cancel (ТЗ §2.5, §2.4 d, §9)."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, events, limits, phases, schema, state_io, task  # noqa: E402

ENV_KEYS = (
    "CLAUDE_PLUGIN_DATA",
    "CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER",
    "CLAUDE_PLUGIN_OPTION_WRITER_MODEL",
    "CLAUDE_PLUGIN_OPTION_SOURCE_REVIEW_GATE",
    "CLAUDE_PLUGIN_OPTION_DASHBOARD",
    "CLAUDE_PLUGIN_OPTION_STOP_GUARD",
    "CLAUDE_PLUGIN_OPTION_WEBSEARCH_AUTOALLOW",
    "MEMOFORGE_OUTPUT_FOLDER",
    "CLAUDE_PROJECT_DIR",
    "LOCALAPPDATA",
    "HOME",
    "USERPROFILE",
)


def clean_env(**overrides: str):
    environment = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
    environment.update(overrides)
    return mock.patch.dict(os.environ, environment, clear=True)


def new_args(**kwargs) -> argparse.Namespace:
    base = {
        "query": "Biometric data of minors under the GDPR",
        "slug": None,
        "output_folder": None,
        "writer_model": None,
        "source_review_gate": None,
        "human": False,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


def run_cli(argv: list) -> dict:
    """Drive a command through the real CLI parser and return its answer dict."""
    args = cli.build_parser().parse_args(argv)
    args.human = False
    return args.func(args)


def ns(**kwargs) -> argparse.Namespace:
    base = {"task_id": None, "workdir": None, "active": False, "limit": 0, "human": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def mark_plugin_root(path: Path) -> Path:
    """D-104: a directory carrying this marker is a plugin root, never a session working folder."""
    (path / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (path / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    return path


def write_state_file(work_dir: Path, document: dict) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "state.json").write_text(json.dumps(document), encoding="utf-8")


class SlugTest(unittest.TestCase):
    def test_spaces_and_case(self):
        self.assertEqual(task.sanitize_slug("Biometric Data"), "biometric-data")

    def test_path_traversal(self):
        self.assertEqual(task.sanitize_slug("../../etc/passwd"), "etc-passwd")
        self.assertEqual(task.sanitize_slug(".."), "task")
        self.assertEqual(task.sanitize_slug("/"), "task")
        self.assertEqual(task.sanitize_slug("C:\\Windows\\system32"), "c-windows-system32")

    def test_empty_and_none(self):
        self.assertEqual(task.sanitize_slug(""), "task")
        self.assertEqual(task.sanitize_slug(None), "task")
        self.assertEqual(task.sanitize_slug("   "), "task")

    def test_non_ascii_is_dropped(self):
        self.assertEqual(task.sanitize_slug("персональные данные"), "task")
        self.assertEqual(task.sanitize_slug("Ärzte data"), "arzte-data")

    def test_length_cap_and_charset(self):
        slug = task.sanitize_slug("word " * 40)
        self.assertLessEqual(len(slug), limits.SLUG_MAX_LENGTH)
        self.assertRegex(slug, r"^[a-z0-9-]+$")
        self.assertFalse(slug.startswith("-"))
        self.assertFalse(slug.endswith("-"))

    def test_collapses_separators(self):
        self.assertEqual(task.sanitize_slug("a---b   c"), "a-b-c")


class LanguageTest(unittest.TestCase):
    """ТЗ §0.3: the memo is English-only, so the query language never selects the output language."""

    def test_a_non_english_query_still_produces_an_english_memo(self):
        self.assertEqual(task.detect_language("Обработка данных"), "en")

    def test_defaults_to_english(self):
        self.assertEqual(task.detect_language("Data processing"), "en")

    def test_a_new_task_records_english(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                result = task.run_new(new_args(query="Обработка данных в поддержке"))
            self.assertEqual(result["language"], task.MEMO_LANGUAGE)


class WorkDirChainTest(unittest.TestCase):
    def test_user_config_wins(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=other):
                resolved = task.resolve_output_folder(tmp)
                self.assertEqual(resolved["source"], "user_config")
                self.assertEqual(resolved["output_folder"], Path(tmp).absolute())

    def test_plugin_option_env_is_user_config(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            with clean_env(CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER=tmp, MEMOFORGE_OUTPUT_FOLDER=other):
                resolved = task.resolve_output_folder()
                self.assertEqual(resolved["source"], "user_config")
                self.assertEqual(resolved["output_folder"], Path(tmp).absolute())

    def test_env_variable_is_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp):
                resolved = task.resolve_output_folder()
                self.assertEqual(resolved["source"], "env")

    def test_the_project_folder_is_third(self):
        """D-104: the folder attached to the session, as `<folder>/memoforge`."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "session"
            project.mkdir()
            with clean_env(CLAUDE_PROJECT_DIR=str(project)):
                resolved = task.resolve_output_folder()
            self.assertEqual(resolved["source"], "project_folder")
            self.assertEqual(resolved["output_folder"], (project / "memoforge").absolute())

    def test_without_claude_project_dir_the_cwd_is_the_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                with clean_env():
                    resolved = task.resolve_output_folder()
            finally:
                os.chdir(previous)
            self.assertEqual(resolved["source"], "project_folder")
            self.assertEqual(resolved["output_folder"], (Path(tmp) / "memoforge").absolute())

    def test_the_environment_variable_still_beats_the_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "session"
            project.mkdir()
            env_folder = Path(tmp) / "env"
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=str(env_folder), CLAUDE_PROJECT_DIR=str(project)):
                resolved = task.resolve_output_folder()
            self.assertEqual(resolved["source"], "env")
            self.assertFalse((project / "memoforge").exists(), "the project folder was created")

    def test_the_plugin_root_is_not_a_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = mark_plugin_root(Path(tmp) / "plugin")
            home = Path(tmp) / "home"
            with clean_env(CLAUDE_PROJECT_DIR=str(project)):
                with mock.patch("memoforge.hooks_common.home_dir", return_value=home):
                    resolved = task.resolve_output_folder()
            self.assertEqual(resolved["source"], "home_documents")
            self.assertFalse((project / "memoforge").exists(), "a task folder landed in the plugin")

    def test_the_home_directory_itself_is_not_a_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            with clean_env(CLAUDE_PROJECT_DIR=str(home)):
                with mock.patch("memoforge.hooks_common.home_dir", return_value=home):
                    resolved = task.resolve_output_folder()
            self.assertEqual(resolved["source"], "home_documents")
            self.assertEqual(resolved["output_folder"], (home / "Documents" / "memoforge").absolute())
            self.assertFalse((home / "memoforge").exists(), "a task folder landed loose in the home")

    def test_a_filesystem_root_is_not_a_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with clean_env(CLAUDE_PROJECT_DIR=Path(tmp).anchor):
                with mock.patch("memoforge.hooks_common.home_dir", return_value=home):
                    resolved = task.resolve_output_folder()
            self.assertEqual(resolved["source"], "home_documents")

    def test_home_documents_is_fourth(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env():
                with mock.patch("memoforge.hooks_common.is_project_folder", return_value=False):
                    with mock.patch("memoforge.hooks_common.home_dir", return_value=Path(tmp)):
                        resolved = task.resolve_output_folder()
                        self.assertEqual(resolved["source"], "home_documents")
                        self.assertEqual(
                            resolved["output_folder"],
                            (Path(tmp) / "Documents" / "memoforge").absolute(),
                        )

    def test_cwd_outputs_is_the_last_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                with clean_env():
                    with mock.patch("memoforge.hooks_common.is_project_folder", return_value=False):
                        with mock.patch("memoforge.hooks_common.home_dir", return_value=None):
                            resolved = task.resolve_output_folder()
                            self.assertEqual(resolved["source"], "cwd_outputs")
                            self.assertEqual(
                                resolved["output_folder"],
                                (Path(tmp) / "outputs" / "memoforge-work").absolute(),
                            )
            finally:
                os.chdir(previous)

    def test_first_writable_wins_and_rejections_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocked"
            blocker.write_text("not a directory", encoding="utf-8")
            good = Path(tmp) / "good"
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=str(good)):
                resolved = task.resolve_output_folder(str(blocker))
                self.assertEqual(resolved["source"], "env")
                self.assertEqual([row["source"] for row in resolved["rejected"]], ["user_config"])


class TaskNewTest(unittest.TestCase):
    def test_creates_tree_state_and_events(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                result = task.run_new(new_args())
            self.assertNotIn("errors", result)
            work_dir = Path(result["work_dir"])
            self.assertTrue(work_dir.is_dir())
            for relative in task.WORK_DIR_SUBDIRS:
                self.assertTrue((work_dir / relative).is_dir(), relative)
            for lock in state_io.LOCK_FILENAMES:
                self.assertTrue((work_dir / lock).is_file(), lock)
            self.assertTrue((work_dir / "events.jsonl").is_file())

            state = state_io.read_state(work_dir)
            self.assertEqual(schema.validate(state, "state"), [])
            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(state["current_phase"], phases.INITIAL_PHASE)
            self.assertIsNone(state["mode"])
            self.assertFalse(state["cancel_requested"])
            self.assertFalse(state["sources_frozen"])
            self.assertEqual(state["config"]["intake_max_questions"], limits.INTAKE_MAX_QUESTIONS)
            self.assertEqual(state["config"]["source_review_gate"], "auto")
            self.assertEqual(state["config"]["plugin_data_dir"], str(Path(data)))
            self.assertEqual(state["attempts"]["plan_edit"], 0)
            self.assertEqual(state["attempts"]["lint_fix"], {})

            names = [event["event"] for event in events.read_events(work_dir)]
            self.assertIn("task_created", names)
            self.assertIn("work_dir_resolved", names)
            self.assertIn("state_written", names)
            resolved = [e for e in events.read_events(work_dir) if e["event"] == "work_dir_resolved"][0]
            self.assertEqual(resolved["data"]["source"], "env")

    def test_task_id_shape_and_slug(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                result = task.run_new(new_args(slug="Some Slug/../x"))
            self.assertEqual(result["slug"], "some-slug-x")
            self.assertRegex(result["task_id"], r"^memo-\d{8}T\d{6}Z-some-slug-x$")

    def test_two_tasks_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                first = task.run_new(new_args(slug="same"))
                second = task.run_new(new_args(slug="same"))
            self.assertNotEqual(first["work_dir"], second["work_dir"])
            self.assertNotEqual(first["task_id"], second["task_id"])

    def test_the_language_flag_no_longer_exists(self):
        """D-15: `task new --language` is removed; the memo is always English (§0.3)."""
        parser = cli.build_parser()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["task", "new", "--query", "x", "--language", "de"])
        self.assertEqual(raised.exception.code, 2)

    def test_a_new_task_is_always_english(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                result = task.run_new(new_args(query="Обработка данных"))
            self.assertEqual(result["language"], "en")
            self.assertEqual(state_io.read_state(Path(result["work_dir"]))["language"], "en")

    def test_an_unknown_writer_model_degrades_instead_of_failing(self):
        """ТЗ §4.1 / D-15: fallback to `opus` plus a `writer_model_fallback` event."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(
                MEMOFORGE_OUTPUT_FOLDER=tmp,
                CLAUDE_PLUGIN_DATA=data,
                CLAUDE_PLUGIN_OPTION_WRITER_MODEL="gpt",
            ):
                result = task.run_new(new_args())
            self.assertNotIn("errors", result)
            work_dir = Path(result["work_dir"])
            state = state_io.read_state(work_dir)
            self.assertEqual(schema.validate(state, "state"), [])
            self.assertEqual(state["config"]["writer_model"], "opus")
            self.assertNotIn("writer_model_fallback", state["config"])
            logged = [e for e in events.read_events(work_dir) if e["event"] == "writer_model_fallback"]
            self.assertEqual(len(logged), 1)
            self.assertEqual(logged[0]["data"], {"requested": "gpt", "applied": "opus"})
            self.assertEqual(logged[0]["severity"], "warn")

    def test_an_allowed_writer_model_logs_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(
                MEMOFORGE_OUTPUT_FOLDER=tmp,
                CLAUDE_PLUGIN_DATA=data,
                CLAUDE_PLUGIN_OPTION_WRITER_MODEL="fable",
            ):
                result = task.run_new(new_args())
            work_dir = Path(result["work_dir"])
            self.assertEqual(state_io.read_state(work_dir)["config"]["writer_model"], "fable")
            names = [e["event"] for e in events.read_events(work_dir)]
            self.assertNotIn("writer_model_fallback", names)

    def test_empty_query_is_a_business_error(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                result = task.run_new(new_args(query="   "))
            self.assertEqual(result["errors"], ["empty_query"])

    def test_user_config_from_env_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(
                MEMOFORGE_OUTPUT_FOLDER=tmp,
                CLAUDE_PLUGIN_DATA=data,
                CLAUDE_PLUGIN_OPTION_WRITER_MODEL="sonnet",
                CLAUDE_PLUGIN_OPTION_SOURCE_REVIEW_GATE="on",
                CLAUDE_PLUGIN_OPTION_STOP_GUARD="true",
            ):
                result = task.run_new(new_args())
            state = state_io.read_state(Path(result["work_dir"]))
            self.assertEqual(state["config"]["writer_model"], "sonnet")
            self.assertEqual(state["config"]["source_review_gate"], "on")
            # D-74: the hook options stay in the environment; `state.config` never carries them.
            self.assertNotIn("stop_guard", state["config"])


class OptionChainTest(unittest.TestCase):
    """D-91: flag > `CLAUDE_PLUGIN_OPTION_*` > `<plugin_data_dir>/options.json` > manifest default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.out = self.data / "out"
        self.out.mkdir()

    def env(self, **overrides: str):
        return clean_env(CLAUDE_PLUGIN_DATA=str(self.data), **overrides)

    def write_options(self, document: dict) -> None:
        (self.data / task.OPTIONS_FILENAME).write_text(json.dumps(document), encoding="utf-8")

    def test_the_defaults_are_the_manifest_defaults(self):
        with self.env():
            resolved = task.resolve_options()
        self.assertEqual(resolved["values"]["dashboard"], "true")
        self.assertEqual(resolved["values"]["writer_model"], "opus")
        self.assertEqual(resolved["values"]["source_review_gate"], "auto")
        self.assertEqual(resolved["sources"]["dashboard"], "default")
        self.assertNotIn("output_folder", resolved["values"])
        self.assertEqual(resolved["sources"]["output_folder"], "default")

    def test_the_options_file_beats_the_default(self):
        self.write_options({"dashboard": "false", "writer_model": "sonnet"})
        with self.env():
            resolved = task.resolve_options()
        self.assertEqual(resolved["values"]["dashboard"], "false")
        self.assertEqual(resolved["sources"]["dashboard"], "options.json")
        self.assertEqual(resolved["sources"]["writer_model"], "options.json")
        self.assertEqual(resolved["sources"]["source_review_gate"], "default")

    def test_the_environment_beats_the_options_file(self):
        self.write_options({"dashboard": "false"})
        with self.env(CLAUDE_PLUGIN_OPTION_DASHBOARD="true"):
            resolved = task.resolve_options()
        self.assertEqual(resolved["values"]["dashboard"], "true")
        self.assertEqual(resolved["sources"]["dashboard"], "env")

    def test_a_flag_beats_the_environment(self):
        self.write_options({"writer_model": "sonnet"})
        with self.env(CLAUDE_PLUGIN_OPTION_WRITER_MODEL="fable"):
            resolved = task.resolve_options({"writer_model": "opus"})
        self.assertEqual(resolved["values"]["writer_model"], "opus")
        self.assertEqual(resolved["sources"]["writer_model"], "flag")

    def test_an_unexpanded_placeholder_is_ignored_at_every_level(self):
        self.write_options({"output_folder": str(self.out)})
        with self.env(CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER="${plugin.output_folder}"):
            resolved = task.resolve_options({"output_folder": "${OUTPUT_FOLDER}"})
        self.assertEqual(resolved["values"]["output_folder"], str(self.out))
        self.assertEqual(resolved["sources"]["output_folder"], "options.json")

    def test_a_placeholder_with_nothing_below_it_falls_back_to_the_default(self):
        with self.env(CLAUDE_PLUGIN_OPTION_DASHBOARD="${dashboard}"):
            resolved = task.resolve_options()
        self.assertEqual(resolved["values"]["dashboard"], "true")
        self.assertEqual(resolved["sources"]["dashboard"], "default")

    def test_an_empty_value_falls_through(self):
        self.write_options({"writer_model": "sonnet"})
        with self.env(CLAUDE_PLUGIN_OPTION_WRITER_MODEL="   "):
            resolved = task.resolve_options()
        self.assertEqual(resolved["sources"]["writer_model"], "options.json")

    def test_a_boolean_written_as_json_true_is_read_back(self):
        self.write_options({"dashboard": True, "stop_guard": False})
        with self.env():
            values = task.user_config_from_env()
        self.assertIs(values["dashboard"], True)
        self.assertIs(values["stop_guard"], False)

    def test_an_unknown_key_in_the_file_is_ignored(self):
        self.write_options({"dashboard": "false", "secret_token": "nope"})
        with self.env():
            resolved = task.resolve_options()
        self.assertNotIn("secret_token", resolved["values"])
        self.assertNotIn("secret_token", resolved["sources"])

    def test_a_broken_options_file_is_not_an_error(self):
        (self.data / task.OPTIONS_FILENAME).write_text("{not json", encoding="utf-8")
        with self.env():
            resolved = task.resolve_options()
        self.assertEqual(resolved["sources"]["dashboard"], "default")

    def test_task_new_reports_the_source_of_every_option(self):
        self.write_options({"dashboard": "false"})
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out), CLAUDE_PLUGIN_OPTION_WRITER_MODEL="fable"):
            result = task.run_new(new_args())
        self.assertEqual(sorted(result["options_source"]), sorted(task.OPTION_KEYS))
        self.assertEqual(result["options_source"]["dashboard"], "options.json")
        self.assertEqual(result["options_source"]["writer_model"], "env")
        self.assertEqual(result["options_source"]["source_review_gate"], "default")
        state = state_io.read_state(Path(result["work_dir"]))
        self.assertIs(state["config"]["dashboard"], False)
        self.assertNotIn("options_source", state, "D-91: the source is an answer field, not state")

    def test_the_options_file_can_turn_the_dashboard_on_without_the_host(self):
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out)):
            default_on = task.run_new(new_args(slug="a"))
        self.assertIs(state_io.read_state(Path(default_on["work_dir"]))["config"]["dashboard"], True)
        self.assertEqual(default_on["options_source"]["dashboard"], "default")

    def test_an_option_flag_wins_and_is_validated(self):
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out), CLAUDE_PLUGIN_OPTION_DASHBOARD="true"):
            result = task.run_new(new_args(option=["dashboard=false"]))
        self.assertEqual(result["options_source"]["dashboard"], "flag")
        self.assertIs(state_io.read_state(Path(result["work_dir"]))["config"]["dashboard"], False)

    def test_a_broken_option_flag_is_a_business_error(self):
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out)):
            self.assertEqual(
                task.run_new(new_args(option=["dashboard"]))["errors"],
                ["invalid_option_flag: dashboard (expected key=value)"],
            )
            self.assertEqual(
                task.run_new(new_args(option=["dashboard=maybe"]))["errors"],
                ["invalid_option_value: dashboard=maybe (expected true or false)"],
            )
            self.assertEqual(
                task.run_new(new_args(option=["nonsense=1"]))["errors"], ["unknown_option: nonsense"]
            )

    def test_publish_folder_is_a_declared_option_with_an_empty_default(self):
        """D-109: no value means «the host's outputs area if it has one», not «unset key»."""
        self.assertIn("publish_folder", task.OPTION_KEYS)
        self.assertEqual(task.OPTION_DEFAULTS["publish_folder"], "")
        with self.env():
            resolved = task.resolve_options()
        self.assertNotIn("publish_folder", resolved["values"])
        self.assertEqual(resolved["sources"]["publish_folder"], "default")

    def test_publish_folder_is_validated_as_a_path_string(self):
        self.assertIsNone(task.validate_option("publish_folder", str(self.out)))
        self.assertIsNone(task.validate_option("publish_folder", "C:\\Users\\lawyer\\Desktop"))
        self.assertEqual(
            task.validate_option("publish_folder", ""), "empty_option_value: publish_folder"
        )
        self.assertEqual(
            task.validate_option("publish_folder", "${plugin.publish_folder}"),
            "placeholder_option_value: publish_folder=${plugin.publish_folder}",
        )
        self.assertIn(
            "expected a directory path", task.validate_option("publish_folder", "out\x00dir")
        )

    def test_publish_folder_reaches_state_config(self):
        """`finalize.publish_root` reads it from `state.config`, so the chain has to put it there."""
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out)):
            result = task.run_new(new_args(option=[f"publish_folder={self.out}"]))
        state = state_io.read_state(Path(result["work_dir"]))
        self.assertEqual(state["config"]["publish_folder"], str(self.out))
        self.assertEqual(result["options_source"]["publish_folder"], "flag")

    def test_a_named_flag_beats_the_same_key_given_as_an_option(self):
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out)):
            result = task.run_new(new_args(writer_model="fable", option=["writer_model=sonnet"]))
        self.assertEqual(state_io.read_state(Path(result["work_dir"]))["config"]["writer_model"], "fable")

    def test_an_output_folder_known_only_to_the_options_file_stays_findable(self):
        """`task new` used it, so `task resolve`/`list` have to look there too (D-91)."""
        self.write_options({"output_folder": str(self.out)})
        with self.env():
            created = task.run_new(new_args(slug="findable"))
            self.assertEqual(created["work_dir_source"], "user_config")
            self.assertEqual(Path(created["work_dir"]).parent, self.out)
            listed = task.run_list(ns())
            resolved = task.run_resolve(ns())
        self.assertIn(created["task_id"], [row["task_id"] for row in listed["tasks"]])
        self.assertEqual(resolved["task_id"], created["task_id"])


    def test_a_placeholder_output_folder_never_becomes_a_directory(self):
        """D-93: `resolve_options` skips the `${…}`, so the §2.5 chain must not read it back."""
        literal = "${plugin.output_folder}"
        env = self.env(CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER=literal, MEMOFORGE_OUTPUT_FOLDER=str(self.out))
        with env, mock.patch("memoforge.hooks_common.home_dir", return_value=None):
            created = task.run_new(new_args(slug="ghost"))
            listed = task.run_list(ns())
            resolved = task.run_resolve(ns())
        self.assertEqual(created["work_dir_source"], "env")
        self.assertEqual(Path(created["work_dir"]).parent, self.out)
        self.assertFalse((Path.cwd() / literal).exists(), "a literal ${…} directory was created")
        self.assertEqual(resolved["task_id"], created["task_id"])
        self.assertIn(created["task_id"], [row["task_id"] for row in listed["tasks"]])
        self.assertNotIn(literal, " ".join(row["work_dir"] for row in listed["tasks"]))

    def test_a_placeholder_option_flag_falls_through_instead_of_failing(self):
        """D-93: `task new --option` skips it (like `--output-folder`); `config set` still refuses."""
        new_task = ["task", "new", "--query", "q", "--slug"]
        with self.env(MEMOFORGE_OUTPUT_FOLDER=str(self.out)):
            created = run_cli(new_task + ["fall", "--option", "output_folder=${plugin.output_folder}"])
            named = run_cli(new_task + ["named", "--output-folder", "${plugin.output_folder}"])
            refused = run_cli(["config", "set", "output_folder", "${plugin.output_folder}"])
            written = task.read_options_file(task.options_path())
        self.assertNotIn("errors", created)
        self.assertEqual(created["options_source"]["output_folder"], "default")
        self.assertEqual(created["work_dir_source"], "env")
        self.assertEqual(Path(created["work_dir"]).parent, self.out)
        self.assertEqual(named["work_dir_source"], created["work_dir_source"])
        self.assertEqual(
            refused["errors"], ["placeholder_option_value: output_folder=${plugin.output_folder}"]
        )
        self.assertEqual(written, {}, "D-91: `config set` must not write the placeholder")


class CitationStyleOptionTest(unittest.TestCase):
    """D-152: `citation_style` travels the public options chain into `state.config` (§8.4, §5.5)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.out = self.data / "out"
        self.out.mkdir()

    def create(self, option: list | None = None) -> dict:
        with clean_env(CLAUDE_PLUGIN_DATA=str(self.data)):
            result = task.run_new(new_args(output_folder=str(self.out), option=option))
        self.assertNotIn("errors", result, result)
        return state_io.read_state(Path(result["work_dir"]))

    def test_the_option_lands_in_state_and_reaches_the_renderer(self):
        from memoforge.docx import oscola

        state = self.create(["citation_style=footnotes"])
        self.assertEqual("footnotes", state["config"]["citation_style"])
        self.assertEqual(oscola.STYLE_FOOTNOTES, oscola.resolve_style(state))

    def test_the_default_is_inline(self):
        from memoforge.docx import oscola

        state = self.create()
        self.assertEqual("inline", state["config"]["citation_style"])
        self.assertEqual(oscola.STYLE_INLINE, oscola.resolve_style(state))

    def test_an_unknown_style_is_refused_with_the_expected_values(self):
        with clean_env(CLAUDE_PLUGIN_DATA=str(self.data)):
            result = task.run_new(
                new_args(output_folder=str(self.out), option=["citation_style=endnotes"])
            )
        self.assertEqual(
            ["invalid_option_value: citation_style=endnotes (expected footnotes|inline)"],
            result["errors"],
        )

    def test_the_option_is_known_to_the_chain_and_to_the_session_hook(self):
        manifest = json.loads(
            (Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8-sig"
            )
        )
        hook = (Path(__file__).resolve().parents[2] / "hooks" / "ensure_deps.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("citation_style", task.OPTION_KEYS)
        self.assertIn("citation_style", manifest["userConfig"])
        self.assertIn('"citation_style"', hook, "SessionStart mirrors every declared option")
        with clean_env(CLAUDE_PLUGIN_DATA=str(self.data)):
            resolved = task.resolve_options()
        self.assertEqual("inline", resolved["values"]["citation_style"])
        self.assertEqual("default", resolved["sources"]["citation_style"])


class ProjectFolderTaskTest(unittest.TestCase):
    """D-104: a task created in the session folder is the one `resolve`/`list` find again."""

    def test_task_new_lands_in_the_project_folder_and_stays_findable(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            project = Path(tmp) / "session"
            project.mkdir()
            home = Path(tmp) / "home"
            with clean_env(CLAUDE_PROJECT_DIR=str(project), CLAUDE_PLUGIN_DATA=data):
                with mock.patch("memoforge.hooks_common.home_dir", return_value=home):
                    created = task.run_new(new_args(slug="in-project"))
                    listed = task.run_list(ns())
                    resolved = task.run_resolve(ns(task_id=created["task_id"]))
            work_dir = Path(created["work_dir"])
            self.assertEqual(created["work_dir_source"], "project_folder")
            self.assertTrue(work_dir.is_absolute(), "the skill prints this path verbatim")
            self.assertEqual(work_dir.parent, (project / "memoforge").absolute())
            self.assertIn(created["task_id"], [row["task_id"] for row in listed["tasks"]])
            self.assertEqual(resolved["work_dir"], str(work_dir.absolute()))


class ResolveTest(unittest.TestCase):
    def _make(self, root: Path, data: str, **kwargs):
        with clean_env(MEMOFORGE_OUTPUT_FOLDER=str(root), CLAUDE_PLUGIN_DATA=data):
            return task.run_new(new_args(**kwargs))

    def test_resolve_by_id(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            created = self._make(Path(tmp), data, slug="alpha")
            result = task.run_resolve(ns(task_id=created["task_id"], workdir=tmp))
            self.assertEqual(result["task_id"], created["task_id"])
            self.assertFalse(result["unsupported"])
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["current_phase"], phases.INITIAL_PHASE)

    def test_resolve_without_id_takes_the_last_unfinished(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            first = self._make(Path(tmp), data, slug="first")
            second = self._make(Path(tmp), data, slug="second")
            # close the newer one: the older unfinished task must win
            state_io.write_state(
                Path(second["work_dir"]), lambda state: state.__setitem__("current_phase", "done")
            )
            result = task.run_resolve(ns(workdir=tmp))
            self.assertEqual(result["task_id"], first["task_id"])

    def test_resolve_returns_unsupported_for_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "memo-20250101T000000Z-legacy"
            write_state_file(
                work_dir,
                {"task_id": "memo-20250101T000000Z-legacy", "current_phase": "drafting", "schema_version": 1},
            )
            with clean_env():
                result = task.run_resolve(ns(workdir=tmp))
            self.assertTrue(result["unsupported"])
            self.assertEqual(result["hint"], task.LEGACY_HINT)
            self.assertNotIn("errors", result)

    def test_resolve_treats_a_state_without_schema_version_as_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "memo-20250101T000000Z-old"
            write_state_file(work_dir, {"task_id": "old", "current_phase": "research"})
            with clean_env():
                result = task.run_resolve(ns(workdir=tmp))
            self.assertTrue(result["unsupported"])

    def test_resolve_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env():
                result = task.run_resolve(ns(task_id="memo-nope", workdir=tmp))
            self.assertEqual(result["errors"], ["task_not_found"])

    def test_resolve_without_any_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env():
                result = task.run_resolve(ns(workdir=tmp))
            self.assertEqual(result["errors"], ["no_unfinished_task"])

    def test_resolve_accepts_the_work_dir_itself(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            created = self._make(Path(tmp), data, slug="direct")
            with clean_env():
                result = task.run_resolve(ns(workdir=created["work_dir"]))
            self.assertEqual(result["task_id"], created["task_id"])


class ListTest(unittest.TestCase):
    def test_lists_tasks_and_flags_terminal(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                first = task.run_new(new_args(slug="one"))
                second = task.run_new(new_args(slug="two"))
            state_io.write_state(
                Path(second["work_dir"]), lambda state: state.__setitem__("current_phase", "done")
            )
            with clean_env():
                result = task.run_list(ns(workdir=tmp))
            self.assertEqual(result["count"], 2)
            by_id = {row["task_id"]: row for row in result["tasks"]}
            self.assertFalse(by_id[first["task_id"]]["terminal"])
            self.assertTrue(by_id[second["task_id"]]["terminal"])

            with clean_env():
                active = task.run_list(ns(workdir=tmp, active=True))
            self.assertEqual(active["count"], 1)

    def test_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env():
                self.assertEqual(task.run_list(ns(workdir=tmp)), {"tasks": [], "count": 0})

    def test_human_prints_a_table(self):
        """D-36: `mf task list --human` — task_id, phase, mode, updated, work_dir."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                created = task.run_new(new_args(slug="human"))
            with clean_env():
                result = task.run_list(ns(workdir=tmp, human=True))

        text = result["human"]
        header, rule, row = text.splitlines()[:3]
        self.assertEqual(
            ["task_id", "phase", "mode", "updated", "work_dir"], header.split()
        )
        self.assertTrue(set(rule) <= {"-", " "}, rule)
        self.assertIn(created["task_id"], row)
        self.assertIn(str(Path(created["work_dir"])), row)
        self.assertIn("intake_preliminary_research", row)
        updated = result["tasks"][0]["updated"]
        self.assertRegex(updated, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIn(updated, row)

    def test_json_output_carries_no_human_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env():
                self.assertNotIn("human", task.run_list(ns(workdir=tmp)))


class CancelTest(unittest.TestCase):
    def test_cancel_sets_the_flag_and_emits_an_event(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                created = task.run_new(new_args(slug="cancel-me"))
            work_dir = Path(created["work_dir"])
            with clean_env():
                result = task.run_cancel(ns(workdir=str(work_dir)))
            self.assertTrue(result["cancelled"])
            self.assertTrue(result["cancel_requested"])
            self.assertTrue(state_io.read_state(work_dir)["cancel_requested"])
            self.assertEqual(state_io.read_state(work_dir)["current_phase"], phases.INITIAL_PHASE)
            names = [event["event"] for event in events.read_events(work_dir)]
            self.assertIn("cancel_requested", names)

    def test_cancel_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                created = task.run_new(new_args(slug="cancel-twice"))
            work_dir = Path(created["work_dir"])
            with clean_env():
                task.run_cancel(ns(workdir=str(work_dir)))
                again = task.run_cancel(ns(workdir=str(work_dir)))
            self.assertTrue(again["already_requested"])
            self.assertTrue(state_io.read_state(work_dir)["cancel_requested"])

    def test_cancel_of_a_terminal_task_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            with clean_env(MEMOFORGE_OUTPUT_FOLDER=tmp, CLAUDE_PLUGIN_DATA=data):
                created = task.run_new(new_args(slug="finished"))
            work_dir = Path(created["work_dir"])
            state_io.write_state(work_dir, lambda state: state.__setitem__("current_phase", "done"))
            with clean_env():
                result = task.run_cancel(ns(workdir=str(work_dir)))
            self.assertTrue(result["already_terminal"])
            self.assertFalse(result["cancelled"])
            self.assertFalse(state_io.read_state(work_dir)["cancel_requested"])

    def test_cancel_of_a_v1_task_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "memo-20250101T000000Z-legacy"
            write_state_file(work_dir, {"task_id": "legacy", "current_phase": "drafting", "schema_version": 1})
            with clean_env():
                result = task.run_cancel(ns(workdir=str(work_dir)))
            self.assertTrue(result["unsupported"])


if __name__ == "__main__":
    unittest.main()
