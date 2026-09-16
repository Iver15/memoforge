"""Tests for `mf config show|set|unset` — the `options.json` mirror of userConfig (ТЗ §8.4, D-91)."""

from __future__ import annotations

import argparse
import contextlib
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

from memoforge import cli, config_cmd, task  # noqa: E402

ENV_KEYS = ("CLAUDE_PLUGIN_DATA", "LOCALAPPDATA") + tuple(
    task.option_env_name(key) for key in task.OPTION_KEYS
)


def ns(**kwargs) -> argparse.Namespace:
    base = {"human": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def run_cli(*argv: str) -> tuple:
    """Run `cli.main` with stdout captured; returns `(exit_code, parsed answer)`."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(list(argv))
    return code, json.loads(buffer.getvalue())


class _DataDirMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        environment = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
        environment["CLAUDE_PLUGIN_DATA"] = str(self.data)
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = self.data / task.OPTIONS_FILENAME

    def document(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8-sig"))


class ShowTest(_DataDirMixin, unittest.TestCase):
    def test_defaults_when_nothing_is_configured(self):
        result = config_cmd.run_show(ns())
        self.assertEqual(sorted(result["options_source"]), sorted(task.OPTION_KEYS))
        self.assertEqual(result["options"]["dashboard"], "true")
        self.assertEqual(result["options_source"]["dashboard"], "default")
        self.assertEqual(result["options_file"], {})
        self.assertEqual(result["options_path"], str(self.path))

    def test_the_source_column_names_the_level_each_value_came_from(self):
        config_cmd.run_set(ns(key="dashboard", value="false"))
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_WRITER_MODEL": "sonnet"}):
            result = config_cmd.run_show(ns())
        self.assertEqual(result["options_source"]["dashboard"], "options.json")
        self.assertEqual(result["options_source"]["writer_model"], "env")
        self.assertEqual(result["options_source"]["stop_guard"], "default")

    def test_human_output_lists_every_option_with_its_source(self):
        text = config_cmd.run_show(ns())["human"]
        for key in task.OPTION_KEYS:
            self.assertIn(key, text)
        self.assertIn("[default]", text)
        self.assertIn(config_cmd.UNSET, text, "output_folder has no manifest default")
        self.assertIn(str(self.path), text)

    def test_show_never_writes_the_file(self):
        config_cmd.run_show(ns())
        self.assertFalse(self.path.exists())


class SetTest(_DataDirMixin, unittest.TestCase):
    def test_set_writes_the_option_and_show_reads_it_back(self):
        result = config_cmd.run_set(ns(key="dashboard", value="true"))
        self.assertNotIn("errors", result)
        self.assertEqual(result["set"], {"dashboard": "true"})
        self.assertEqual(self.document(), {"dashboard": "true"})
        self.assertEqual(config_cmd.run_show(ns())["options_source"]["dashboard"], "options.json")

    def test_set_keeps_the_other_options(self):
        config_cmd.run_set(ns(key="dashboard", value="false"))
        config_cmd.run_set(ns(key="writer_model", value="sonnet"))
        self.assertEqual(self.document(), {"dashboard": "false", "writer_model": "sonnet"})

    def test_set_overwrites_the_same_key(self):
        config_cmd.run_set(ns(key="source_review_gate", value="on"))
        config_cmd.run_set(ns(key="source_review_gate", value="off"))
        self.assertEqual(self.document(), {"source_review_gate": "off"})

    def test_an_unknown_key_is_a_business_error(self):
        result = config_cmd.run_set(ns(key="secret_token", value="x"))
        self.assertEqual(result["errors"], ["unknown_option: secret_token"])
        self.assertEqual(result["known_options"], list(task.OPTION_KEYS))
        self.assertFalse(self.path.exists())

    def test_an_invalid_boolean_is_rejected(self):
        result = config_cmd.run_set(ns(key="dashboard", value="maybe"))
        self.assertEqual(
            result["errors"], ["invalid_option_value: dashboard=maybe (expected true or false)"]
        )
        self.assertFalse(self.path.exists())

    def test_an_invalid_enum_is_rejected(self):
        result = config_cmd.run_set(ns(key="source_review_gate", value="sometimes"))
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("auto|on|off", result["errors"][0])

    def test_an_unknown_writer_model_is_rejected(self):
        result = config_cmd.run_set(ns(key="writer_model", value="gpt"))
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("invalid_option_value: writer_model=gpt", result["errors"][0])

    def test_an_empty_value_is_rejected(self):
        self.assertEqual(
            config_cmd.run_set(ns(key="output_folder", value="   "))["errors"],
            ["empty_option_value: output_folder"],
        )

    def test_an_unexpanded_placeholder_is_rejected(self):
        result = config_cmd.run_set(ns(key="output_folder", value="${plugin.output_folder}"))
        self.assertEqual(
            result["errors"], ["placeholder_option_value: output_folder=${plugin.output_folder}"]
        )

    def test_a_key_is_case_insensitive_and_trimmed(self):
        config_cmd.run_set(ns(key="  DASHBOARD ", value=" false "))
        self.assertEqual(self.document(), {"dashboard": "false"})


class UnsetTest(_DataDirMixin, unittest.TestCase):
    def test_unset_removes_the_key_and_the_chain_falls_back(self):
        config_cmd.run_set(ns(key="dashboard", value="false"))
        result = config_cmd.run_unset(ns(key="dashboard"))
        self.assertTrue(result["removed"])
        self.assertEqual(self.document(), {})
        self.assertEqual(result["options_source"]["dashboard"], "default")
        self.assertEqual(result["options"]["dashboard"], "true")

    def test_unset_of_a_missing_key_is_a_no_op(self):
        result = config_cmd.run_unset(ns(key="writer_model"))
        self.assertFalse(result["removed"])
        self.assertFalse(self.path.exists())

    def test_unset_of_an_unknown_key_is_a_business_error(self):
        self.assertEqual(
            config_cmd.run_unset(ns(key="nonsense"))["errors"], ["unknown_option: nonsense"]
        )

    def test_unset_keeps_the_other_options(self):
        config_cmd.run_set(ns(key="dashboard", value="false"))
        config_cmd.run_set(ns(key="stop_guard", value="true"))
        config_cmd.run_unset(ns(key="dashboard"))
        self.assertEqual(self.document(), {"stop_guard": "true"})


class CliTest(_DataDirMixin, unittest.TestCase):
    def test_the_group_is_registered_with_its_three_commands(self):
        parser = cli.build_parser()
        for argv in (["config", "show"], ["config", "set", "dashboard", "true"], ["config", "unset", "dashboard"]):
            with self.subTest(argv=" ".join(argv)):
                self.assertTrue(callable(getattr(parser.parse_args(argv), "func", None)))

    def test_a_round_trip_through_the_cli(self):
        code, answer = run_cli("config", "set", "dashboard", "false")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(answer["options"]["dashboard"], "false")
        code, answer = run_cli("config", "show")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(answer["options_source"]["dashboard"], "options.json")
        code, answer = run_cli("config", "unset", "dashboard")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(answer["options_source"]["dashboard"], "default")

    def test_an_invalid_value_exits_one(self):
        code, answer = run_cli("config", "set", "dashboard", "maybe")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertTrue(answer["errors"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
