"""Tests for scripts/memoforge/style_profile.py — `mf style …` (ТЗ §4.6, §5.2).

Port of `scripts/tests/test_resolve_style_profile.py`: the same behaviours, moved onto the v2
plugin-data chain (`CLAUDE_PLUGIN_DATA` instead of `MEMOFORGE_PROFILES_HOME`), the JSON/exit-code
contract of CONVENTIONS instead of bare text, and the `style-meta` schema on `meta.json`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, temp_root  # noqa: E402
from memoforge import cli, style_profile as sp  # noqa: E402


class _TempDataDirMixin:
    """Point `plugin_data_dir()` at a fresh temp dir so no test touches the real profiles."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self._previous = os.environ.get("CLAUDE_PLUGIN_DATA")
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.home)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._previous is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = self._previous


class NameValidationTest(unittest.TestCase):
    def test_valid_names(self):
        for name in ("a", "my-firm", "firm_v2", "abc123", "x" * 64):
            self.assertIsNone(sp.validate_name(name), msg=f"name={name!r}")

    def test_invalid_names(self):
        for name in ("", "My-Firm", "my firm", "-leading-dash", "_leading", "a/b", "a.b", "x" * 65):
            self.assertIsNotNone(sp.validate_name(name), msg=f"expected invalid: {name!r}")


class ProfileLifecycleTest(_TempDataDirMixin, unittest.TestCase):
    def test_init_creates_dir_and_meta(self):
        files = sp.init_profile("test-profile", "examples")
        self.assertTrue(files["dir"].is_dir())
        self.assertTrue(files["meta"].is_file())
        self.assertTrue(files["sources_dir"].is_dir())
        self.assertFalse(files["template"].is_file())
        self.assertFalse(files["prose_style"].is_file())

        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], "test-profile")
        self.assertEqual(meta["input_type"], "examples")
        self.assertFalse(meta["has_template"])
        self.assertFalse(meta["rules_provided"])

    def test_init_stub_satisfies_the_style_meta_schema(self):
        files = sp.init_profile("test-profile", "examples")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        self.assertEqual(sp.validate_meta(meta), [])

    def test_init_rejects_bad_name(self):
        with self.assertRaises(ValueError):
            sp.init_profile("Bad Name", "examples")

    def test_init_rejects_bad_input_type(self):
        with self.assertRaises(ValueError):
            sp.init_profile("ok", "bogus")

    def test_init_writes_no_mode_binding(self):
        """D-244: a profile no longer binds a run mode, so a new `meta.json` has no `mode_binding`."""
        files = sp.init_profile("test-profile", "examples")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        self.assertNotIn("mode_binding", meta)

    def test_init_takes_no_mode_binding(self):
        with self.assertRaises(TypeError):
            sp.init_profile("ok", "examples", "brief")

    def test_list_empty(self):
        self.assertEqual(sp.list_profiles(), [])

    def test_list_with_profiles(self):
        sp.init_profile("alpha", "examples")
        sp.init_profile("beta", "rules", rules_provided=True)
        records = sp.list_profiles()
        self.assertEqual(sorted(row["name"] for row in records), ["alpha", "beta"])
        for row in records:
            self.assertIn("meta", row)
            self.assertIn("valid", row)
            self.assertFalse(row["is_default"])


class DefaultTest(_TempDataDirMixin, unittest.TestCase):
    def test_default_roundtrip(self):
        sp.init_profile("alpha", "examples")
        self.assertIsNone(sp.get_default())
        sp.set_default("alpha")
        self.assertEqual(sp.get_default(), "alpha")
        sp.clear_default()
        self.assertIsNone(sp.get_default())

    def test_set_default_rejects_unknown(self):
        with self.assertRaises(FileNotFoundError):
            sp.set_default("does-not-exist")

    def test_delete_clears_default_when_was_default(self):
        sp.init_profile("alpha", "examples")
        sp.init_profile("beta", "rules")
        sp.set_default("alpha")
        sp.delete_profile("alpha")
        self.assertIsNone(sp.get_default())
        self.assertEqual([row["name"] for row in sp.list_profiles()], ["beta"])

    def test_delete_other_keeps_default(self):
        sp.init_profile("alpha", "examples")
        sp.init_profile("beta", "rules")
        sp.set_default("alpha")
        sp.delete_profile("beta")
        self.assertEqual(sp.get_default(), "alpha")


class ValidateProfileTest(_TempDataDirMixin, unittest.TestCase):
    def test_init_then_missing_prose_style_is_invalid(self):
        sp.init_profile("p", "examples")
        valid, errors = sp.validate_profile("p")
        self.assertFalse(valid)
        self.assertIn("prose-style.md", "\n".join(errors))

    def test_complete_profile_is_valid(self):
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        valid, errors = sp.validate_profile("p")
        self.assertTrue(valid, msg=f"errors={errors}")

    def test_template_inconsistency_is_invalid(self):
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        files["template"].write_text("# template stub\n", encoding="utf-8")
        valid, errors = sp.validate_profile("p")
        self.assertFalse(valid)
        self.assertTrue(any("template.md exists but" in error for error in errors), msg=errors)

    def test_missing_meta_is_invalid(self):
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        files["meta"].unlink()
        valid, errors = sp.validate_profile("p")
        self.assertFalse(valid)
        self.assertTrue(any("meta.json" in error for error in errors), msg=errors)

    def test_nonexistent_profile_is_invalid(self):
        valid, errors = sp.validate_profile("ghost")
        self.assertFalse(valid)
        self.assertTrue(any("does not exist" in error for error in errors), msg=errors)

    def test_meta_violating_the_schema_is_invalid(self):
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        meta["examples_count"] = "three"  # the schema pins an integer, and only the schema does
        files["meta"].write_text(json.dumps(meta), encoding="utf-8")
        valid, errors = sp.validate_profile("p")
        self.assertFalse(valid)
        self.assertTrue(any("style-meta" in error for error in errors), msg=errors)

    def test_numeric_confidence_from_the_v1_extractor_is_valid(self):
        """D-16: the unchanged v1 style-extractor writes `confidence` as a number."""
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        meta["confidence"] = 0.85
        files["meta"].write_text(json.dumps(meta), encoding="utf-8")
        valid, errors = sp.validate_profile("p")
        self.assertTrue(valid, msg=errors)

    def test_meta_without_mode_binding_is_valid(self):
        """D-244: `mode_binding` is no longer a required key."""
        files = sp.init_profile("p", "examples")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        meta.pop("mode_binding", None)
        self.assertEqual(sp.validate_meta(meta), [])

    def test_an_old_profile_with_a_mode_binding_is_valid_and_listed(self):
        """R1: a profile made before plan 75B keeps its `mode_binding`; it still validates and lists."""
        for binding in ("brief", "full"):
            with self.subTest(binding=binding):
                files = sp.init_profile(f"old-{binding}", "examples")
                files["prose_style"].write_text("# stub\n", encoding="utf-8")
                meta = json.loads(files["meta"].read_text(encoding="utf-8"))
                meta["mode_binding"] = binding
                files["meta"].write_text(json.dumps(meta), encoding="utf-8")
                self.assertEqual(sp.validate_meta(meta), [])
                self.assertEqual(sp.validate_profile(f"old-{binding}"), (True, []))
        listed = {row["name"]: row["valid"] for row in sp.list_profiles()}
        self.assertEqual({"old-brief": True, "old-full": True}, listed)


class ResolvePathsTest(_TempDataDirMixin, unittest.TestCase):
    def test_resolves_with_template(self):
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        files["template"].write_text("# template\n", encoding="utf-8")
        paths = sp.resolve_paths("p")
        self.assertEqual(paths["style_profile"], "p")
        self.assertTrue(paths["style_profile_path"].endswith("/p"))
        self.assertTrue(paths["prose_style_path"].endswith("/p/prose-style.md"))
        self.assertTrue(paths["template_path"].endswith("/p/template.md"))

    def test_resolves_without_template(self):
        files = sp.init_profile("p", "rules", rules_provided=True)
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        paths = sp.resolve_paths("p")
        self.assertIsNone(paths["template_path"])
        self.assertTrue(paths["prose_style_path"].endswith("/p/prose-style.md"))

    def test_paths_use_posix_separator(self):
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        paths = sp.resolve_paths("p")
        for key in ("style_profile_path", "prose_style_path"):
            self.assertNotIn("\\", paths[key], msg=f"{key}={paths[key]}")

    def test_an_old_mode_binding_does_not_travel_with_the_paths(self):
        """D-244: `resolve_paths` no longer returns `style_profile_mode_binding`, even for an old profile."""
        files = sp.init_profile("p", "examples")
        files["prose_style"].write_text("# stub\n", encoding="utf-8")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        meta["mode_binding"] = "brief"
        files["meta"].write_text(json.dumps(meta), encoding="utf-8")
        self.assertNotIn("style_profile_mode_binding", sp.resolve_paths("p"))

    def test_resolve_paths_rejects_an_unknown_profile(self):
        with self.assertRaises(FileNotFoundError):
            sp.resolve_paths("ghost")


class WriteMetaTest(_TempDataDirMixin, unittest.TestCase):
    def full_meta(self, **overrides) -> dict:
        meta = {
            "name": "p",
            "created_at": "2026-05-25T12:00:00Z",
            "input_type": "examples",
            "examples_count": 3,
            "rules_provided": False,
            "has_template": True,
            "jurisdictions": ["EU"],
            "language": "en",
            "confidence": "high",
            "summary": "From 3 EU GDPR memos.",
        }
        meta.update(overrides)
        return meta

    def test_write_meta_roundtrip(self):
        files = sp.init_profile("p", "examples")
        sp.write_meta("p", json.dumps(self.full_meta()))
        on_disk = json.loads(files["meta"].read_text(encoding="utf-8"))
        self.assertEqual(on_disk["examples_count"], 3)
        self.assertTrue(on_disk["has_template"])

    def test_write_meta_rejects_missing_keys(self):
        sp.init_profile("p", "examples")
        with self.assertRaises(ValueError):
            sp.write_meta("p", json.dumps({"name": "p"}))

    def test_write_meta_rejects_invalid_json(self):
        sp.init_profile("p", "examples")
        with self.assertRaises(ValueError):
            sp.write_meta("p", "{not json}")

    def test_write_meta_rejects_a_schema_violation(self):
        sp.init_profile("p", "examples")
        with self.assertRaises(ValueError):
            sp.write_meta("p", json.dumps(self.full_meta(examples_count="three")))

    def test_write_meta_accepts_a_numeric_confidence(self):
        """D-16: `confidence` is anyOf number | high|medium|low | null."""
        files = sp.init_profile("p", "examples")
        sp.write_meta("p", json.dumps(self.full_meta(confidence=0.85)))
        on_disk = json.loads(files["meta"].read_text(encoding="utf-8"))
        self.assertEqual(on_disk["confidence"], 0.85)

    def test_write_meta_accepts_a_meta_without_and_with_an_old_mode_binding(self):
        """D-244 / R1: the key is optional and, when an old profile carries it, still schema-valid."""
        files = sp.init_profile("p", "examples")
        sp.write_meta("p", json.dumps(self.full_meta()))
        self.assertNotIn("mode_binding", json.loads(files["meta"].read_text(encoding="utf-8")))
        sp.write_meta("p", json.dumps(self.full_meta(mode_binding="brief")))
        self.assertEqual("brief", json.loads(files["meta"].read_text(encoding="utf-8"))["mode_binding"])

    def test_write_meta_rejects_an_unknown_profile(self):
        with self.assertRaises(FileNotFoundError):
            sp.write_meta("ghost", json.dumps(self.full_meta(name="ghost")))


class CliTest(_TempDataDirMixin, unittest.TestCase):
    """In-process CLI: `mf style <cmd>` prints one JSON object, exit 0 ok / 1 business error."""

    def run_cli(self, *args: str) -> tuple[int, dict]:
        from io import StringIO
        from unittest import mock

        buffer = StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            code = cli.main(["style", *args])
        printed = buffer.getvalue().strip()
        return code, json.loads(printed) if printed else {}

    def test_list_empty(self):
        code, payload = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(payload["profiles"], [])

    def test_validate_name_good(self):
        code, payload = self.run_cli("validate-name", "my-firm")
        self.assertEqual(code, 0)
        self.assertTrue(payload["valid"])

    def test_validate_name_bad(self):
        code, payload = self.run_cli("validate-name", "Bad Name")
        self.assertEqual(code, 1)
        self.assertIn("must match", payload["errors"][0])

    def test_init_then_list(self):
        code, _ = self.run_cli("init-profile", "alpha", "examples")
        self.assertEqual(code, 0)
        code, payload = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["profiles"][0]["name"], "alpha")
        self.assertEqual(payload["profiles"][0]["meta"]["input_type"], "examples")
        self.assertNotIn("mode_binding", payload["profiles"][0]["meta"])

    def test_init_profile_takes_no_mode_binding_positional(self):
        """D-244: `mf style init-profile <name> <input_type> [--rules-provided]` — no binding."""
        with mock.patch.object(sys, "stderr", StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_cli("init-profile", "alpha", "examples", "brief")
        self.assertEqual(2, raised.exception.code)
        self.assertFalse((self.home / "profiles" / "alpha").exists())

    def test_set_default_unknown_fails(self):
        code, payload = self.run_cli("set-default", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("profile not found", payload["errors"][0])

    def test_resolve_paths_cli(self):
        self.run_cli("init-profile", "alpha", "examples")
        (self.home / "profiles" / "alpha" / "prose-style.md").write_text("# stub\n", encoding="utf-8")
        code, payload = self.run_cli("resolve-paths", "alpha")
        self.assertEqual(code, 0)
        self.assertEqual(payload["style_profile"], "alpha")
        self.assertIsNone(payload["template_path"])

    def test_every_name_taking_subcommand_validates_the_name(self):
        """03 S-23: an invalid name is rejected before any filesystem work."""
        for command in (
            ["set-default", "Bad Name"],
            ["validate-name", "Bad Name"],
            ["validate-profile", "Bad Name"],
            ["delete", "Bad Name"],
            ["read-meta", "Bad Name"],
            ["resolve-paths", "Bad Name"],
            ["init-profile", "Bad Name", "examples"],
            ["write-meta", "Bad Name", "{}"],
        ):
            with self.subTest(command=command[0]):
                code, payload = self.run_cli(*command)
                self.assertEqual(code, 1)
                self.assertTrue(payload["errors"])

    def test_all_twelve_subcommands_are_registered(self):
        parser = cli.build_parser()
        style = parser._subparsers._group_actions[0].choices["style"]
        registered = set(style._subparsers._group_actions[0].choices)
        self.assertEqual(
            registered,
            {
                "list",
                "get-default",
                "set-default",
                "clear-default",
                "validate-name",
                "validate-profile",
                "delete",
                "read-meta",
                "resolve-paths",
                "ensure-dirs",
                "init-profile",
                "write-meta",
            },
        )

    def test_get_default_and_clear_default(self):
        self.run_cli("init-profile", "alpha", "examples")
        self.run_cli("set-default", "alpha")
        _, payload = self.run_cli("get-default")
        self.assertEqual(payload["default"], "alpha")
        self.run_cli("clear-default")
        _, payload = self.run_cli("get-default")
        self.assertIsNone(payload["default"])

    def test_ensure_dirs_and_read_meta(self):
        code, payload = self.run_cli("ensure-dirs")
        self.assertEqual(code, 0)
        self.assertTrue(Path(payload["profiles_dir"]).is_dir())
        self.run_cli("init-profile", "alpha", "examples")
        code, payload = self.run_cli("read-meta", "alpha")
        self.assertEqual(code, 0)
        self.assertEqual(payload["meta"]["name"], "alpha")

    def test_delete_via_cli(self):
        self.run_cli("init-profile", "alpha", "examples")
        code, payload = self.run_cli("delete", "alpha")
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted"], "alpha")
        _, payload = self.run_cli("list")
        self.assertEqual(payload["count"], 0)


class OldProfilePlanGateTest(_TempDataDirMixin, unittest.TestCase):
    """Review Focus 4: a profile made before plan 75B still applies at the plan gate; the run stays Full."""

    def test_old_profile_with_brief_binding_still_applies(self):
        files = sp.init_profile("old-brief", "examples")
        files["prose_style"].write_text("# House style\n", encoding="utf-8")
        files["template"].write_text("# House template\n", encoding="utf-8")
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
        meta.update(mode_binding="brief", has_template=True)
        files["meta"].write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual([("old-brief", True)], [(row["name"], row["valid"]) for row in sp.list_profiles()])

        driver = Driver(temp_root(self), slug="old-brief-profile")
        action = driver.run_until("plan_approval_pending")
        answer = driver.report(
            action["step_id"],
            action["attempt"],
            answers=json.dumps({"Plan": "Approve", "Style": "old-brief"}),
            generation=action.get("generation", 0),
        )
        self.assertTrue(answer["accepted"], msg=answer)
        driver.next()

        state = driver.state()
        self.assertEqual("full", state["mode"])
        config = state["config"]
        self.assertEqual("old-brief", config["style_profile"])
        self.assertEqual(Path(files["prose_style"]), Path(config["prose_style_path"]))
        self.assertEqual(Path(files["template"]), Path(config["template_path"]))
        self.assertNotIn("style_profile_mode_binding", config)
        self.assertEqual(["statutes", "case_law", "doctrine"], config["researcher_layers"])
        self.assertEqual(["logic", "form", "citations", "counterarguments"], config["reviewer_list"])


class ProfilesLocationTest(_TempDataDirMixin, unittest.TestCase):
    def test_profiles_live_under_plugin_data_dir(self):
        from memoforge import pylauncher

        self.assertEqual(sp.profiles_dir(), pylauncher.plugin_data_dir() / "profiles")
        self.assertEqual(sp.profiles_dir().parent, self.home)


if __name__ == "__main__":
    unittest.main()
