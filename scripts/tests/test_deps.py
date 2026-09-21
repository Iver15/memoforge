"""Tests for scripts/memoforge/deps.py — `mf deps check|install` (ТЗ §5.6, D-33)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, deps, pylauncher, schema  # noqa: E402


def check_args(out: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(out=out, human=False)


def install_args(**kwargs) -> argparse.Namespace:
    base = {"requirements": None, "out": None, "dry_run": False, "human": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


class RequirementsTest(unittest.TestCase):
    def test_requirements_txt_is_the_source_of_the_version_floors(self):
        required = deps.read_requirements()
        self.assertEqual(
            {"jsonschema", "python-docx", "mistune"}, set(required), required
        )
        self.assertEqual(">=4.18", required["jsonschema"])

    def test_the_optional_line_of_pypdf_stays_commented_out(self):
        """D-201: `pypdf` is never required, so `mf deps install` never brings it in."""
        text = deps.requirements_path().read_text(encoding="utf-8-sig")
        self.assertIn("# pypdf>=4.0", text)
        self.assertNotIn("pypdf", deps.read_requirements())

    def test_comments_and_blank_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            path.write_text(
                "# a comment\n\nmistune>=3.0  # trailing\n# rapidfuzz>=3.0\n", encoding="utf-8"
            )
            self.assertEqual({"mistune": ">=3.0"}, deps.read_requirements(path))

    def test_version_comparison_covers_the_operators_of_requirements_txt(self):
        self.assertTrue(deps.satisfies("4.23.0", ">=4.18"))
        self.assertFalse(deps.satisfies("4.17", ">=4.18"))
        self.assertTrue(deps.satisfies("1.1.2", ">=1.1"))
        self.assertFalse(deps.satisfies(None, ">=1.0"))
        self.assertTrue(deps.satisfies("3.0", ""))
        self.assertIsNone(deps.satisfies("3.0", "~=3.0"))


class CheckTest(unittest.TestCase):
    def test_check_reports_every_dependency_with_its_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deps.json"
            result = deps.run_check(check_args(str(target)))
            report = json.loads(target.read_text(encoding="utf-8-sig"))

        self.assertEqual(sorted(result["deps"]), ["docx", "jsonschema", "mistune", "pypdf"])
        for name, row in result["deps"].items():
            with self.subTest(dependency=name):
                self.assertIsInstance(row["installed"], bool)
                self.assertIn("version", row)
                self.assertIn("required", row)
        self.assertEqual("deps", report["kind"])
        self.assertEqual(result["deps"], report["deps"])
        self.assertEqual(str(target), result["deps_json"])
        self.assertEqual(sorted(result["missing"]), result["missing"])

    def test_the_report_matches_the_internal_schema(self):
        if not schema.available():
            self.skipTest("jsonschema is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deps.json"
            deps.run_check(check_args(str(target)))
            report = json.loads(target.read_text(encoding="utf-8-sig"))
        self.assertEqual([], schema.validate(report, "internal"))

    def test_a_missing_dependency_is_reported_and_not_ok(self):
        with mock.patch.object(deps.importlib.util, "find_spec", return_value=None):
            with tempfile.TemporaryDirectory() as tmp:
                result = deps.run_check(check_args(str(Path(tmp) / "deps.json")))
        self.assertFalse(result["ok"])
        self.assertEqual(["docx", "jsonschema", "mistune"], result["missing"])
        self.assertNotIn("pypdf", result["missing"], "D-201: an optional module is never missing")
        self.assertIn("mf deps install", result["human"])

    def test_an_outdated_dependency_is_flagged(self):
        with mock.patch.object(deps, "installed_version", return_value="0.1"):
            with tempfile.TemporaryDirectory() as tmp:
                result = deps.run_check(check_args(str(Path(tmp) / "deps.json")))
        self.assertFalse(result["ok"])
        self.assertEqual(["docx", "jsonschema", "mistune"], result["outdated"])
        self.assertNotIn("pypdf", result["outdated"])

    def test_check_never_runs_a_subprocess(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("no subprocess")):
            with tempfile.TemporaryDirectory() as tmp:
                deps.run_check(check_args(str(Path(tmp) / "deps.json")))

    def test_check_works_without_jsonschema(self):
        """§5.6: `deps check` is part of the bootstrap and must not need the dependency it reports."""
        with mock.patch.object(schema, "available", return_value=False):
            with tempfile.TemporaryDirectory() as tmp:
                result = deps.run_check(check_args(str(Path(tmp) / "deps.json")))
        self.assertIn("jsonschema", result["deps"])


class OptionalModuleTest(unittest.TestCase):
    """D-201: `pypdf` is reported, never required and never installed by `mf deps install`."""

    def test_the_optional_tuple_names_pypdf_and_nothing_required(self):
        self.assertEqual((("pypdf", "pypdf"),), deps.OPTIONAL_MODULES)
        self.assertEqual(set(), {name for name, _ in deps.OPTIONAL_MODULES} & {name for name, _ in deps.MODULES})

    def test_the_row_is_marked_optional_and_never_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = deps.run_check(check_args(str(Path(tmp) / "deps.json")))
        row = result["deps"]["pypdf"]
        self.assertTrue(row["optional"])
        self.assertNotIn("pypdf", result["missing"])
        self.assertNotIn("pypdf", result["outdated"])

    def test_an_absent_optional_module_prints_its_state_without_a_mark(self):
        with mock.patch.object(deps.importlib.util, "find_spec", return_value=None):
            with tempfile.TemporaryDirectory() as tmp:
                result = deps.run_check(check_args(str(Path(tmp) / "deps.json")))
        line = [row for row in result["human"].splitlines() if "pypdf" in row]
        self.assertEqual(1, len(line), result["human"])
        self.assertIn("missing (optional)", line[0])
        self.assertTrue(line[0].startswith("   "), line[0])
        self.assertEqual(["docx", "jsonschema", "mistune"], result["missing"])

    def test_the_report_with_the_optional_row_still_matches_the_open_internal_schema(self):
        """The `deps` map of `internal.schema.json` is open, so no schema change was needed."""
        if not schema.available():
            self.skipTest("jsonschema is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deps.json"
            deps.run_check(check_args(str(target)))
            report = json.loads(target.read_text(encoding="utf-8-sig"))
        self.assertIn("pypdf", report["deps"])
        self.assertEqual([], schema.validate(report, "internal"))

    def test_install_never_reaches_for_an_optional_module(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed) as runner:
            with tempfile.TemporaryDirectory() as tmp:
                deps.run_install(install_args(out=str(Path(tmp) / "deps.json")))
        self.assertNotIn("pypdf", " ".join(runner.call_args[0][0]))


class InstallTest(unittest.TestCase):
    def test_install_calls_pip_with_target_and_requirements(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed) as runner:
            with tempfile.TemporaryDirectory() as tmp:
                result = deps.run_install(install_args(out=str(Path(tmp) / "deps.json")))

        self.assertTrue(result["installed"])
        command = runner.call_args[0][0]
        self.assertEqual([sys.executable, "-m", "pip", "install"], command[:4])
        self.assertEqual("--target", command[4])
        self.assertEqual(str(pylauncher.site_packages_dir()), command[5])
        self.assertEqual("-r", command[6])
        self.assertEqual(str(deps.requirements_path()), command[7])
        self.assertIn("check", result)

    def test_a_failing_pip_is_a_business_error(self):
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with mock.patch.object(subprocess, "run", return_value=completed):
            result = deps.run_install(install_args())
        self.assertEqual(["pip_failed: exit 1"], result["errors"])
        self.assertEqual("boom", result["stderr_tail"])

    def test_dry_run_prints_the_command_without_installing(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("no subprocess")):
            result = deps.run_install(install_args(dry_run=True))
        self.assertFalse(result["installed"])
        self.assertIn("pip", result["command"])
        self.assertIn("install", result["command"])

    def test_a_missing_requirements_file_is_a_business_error(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("no subprocess")):
            with tempfile.TemporaryDirectory() as tmp:
                result = deps.run_install(install_args(requirements=str(Path(tmp) / "nope.txt")))
        self.assertTrue(result["errors"][0].startswith("requirements_not_found:"))

    def test_nothing_installs_without_the_explicit_command(self):
        """D-33: `install` is the only place that runs pip, and only on the explicit command."""
        source = (PLUGIN_ROOT / "scripts" / "memoforge" / "deps.py").read_text(encoding="utf-8-sig")
        self.assertEqual(1, source.count("subprocess.run("))
        self.assertNotIn("os.system", source)


class RegistrationTest(unittest.TestCase):
    def test_deps_check_and_install_are_reachable_from_the_cli(self):
        parser = cli.build_parser()
        self.assertIs(deps.run_check, parser.parse_args(["deps", "check"]).func)
        self.assertIs(deps.run_install, parser.parse_args(["deps", "install"]).func)
        self.assertEqual(("deps", "check"), cli.command_labels(parser.parse_args(["deps", "check"])))


if __name__ == "__main__":
    unittest.main()
