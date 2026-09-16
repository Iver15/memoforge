"""Tests for scripts/memoforge/hooks_common.py — finding the active task (ТЗ §8.1, §8.3, §9).

The hooks and the status line must answer «which task is this session working on?» from cwd or
`$CLAUDE_PROJECT_DIR` alone, stay silent when the answer is ambiguous, and never import `state_io`
(M2: hooks do not write state).
"""

from __future__ import annotations

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

from memoforge import hooks_common  # noqa: E402

ENV_KEYS = (
    "CLAUDE_PROJECT_DIR",
    "MEMOFORGE_OUTPUT_FOLDER",
    "CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER",
    "CLAUDE_PLUGIN_ROOT",
)


def make_task(root: Path, name: str, *, phase: str = "research", schema_version: int = 2) -> Path:
    """A minimal work dir the hooks can recognise (they read `state.json` and nothing else)."""
    work_dir = root / name
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "task_id": name,
                "current_phase": phase,
                "created_at": f"2026-09-08T12:00:{len(name) % 60:02d}Z",
            }
        ),
        encoding="utf-8",
    )
    return work_dir


class _EnvMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ProjectDirTest(_EnvMixin, unittest.TestCase):
    def test_claude_project_dir_wins_over_cwd(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        self.assertEqual(hooks_common.project_dir(cwd="/somewhere/else"), self.root)

    def test_cwd_is_used_when_the_env_var_is_empty(self):
        os.environ["CLAUDE_PROJECT_DIR"] = "   "
        self.assertEqual(hooks_common.project_dir(cwd=str(self.root)), self.root)


class FindActiveTaskTest(_EnvMixin, unittest.TestCase):
    def test_finds_the_task_whose_work_dir_is_the_cwd(self):
        work_dir = make_task(self.root, "memo-a")
        self.assertEqual(hooks_common.find_active_task(cwd=str(work_dir)), work_dir)

    def test_finds_the_task_from_a_subdirectory_of_its_work_dir(self):
        work_dir = make_task(self.root, "memo-a")
        nested = work_dir / "research" / "raw"
        nested.mkdir(parents=True)
        self.assertEqual(hooks_common.find_active_task(cwd=str(nested)), work_dir)

    def test_claude_project_dir_selects_the_task(self):
        work_dir = make_task(self.root, "memo-a")
        os.environ["CLAUDE_PROJECT_DIR"] = str(work_dir)
        self.assertEqual(hooks_common.find_active_task(cwd=str(self.root)), work_dir)

    def test_output_folder_chain_finds_the_only_active_task(self):
        work_dir = make_task(self.root, "memo-a")
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        elsewhere = self.root.parent
        self.assertEqual(hooks_common.find_active_task(cwd=str(elsewhere)), work_dir)

    def test_several_active_tasks_under_the_project_dir_pick_the_newest(self):
        older = make_task(self.root, "memo-old")
        newer = make_task(self.root, "memo-newer-one")
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        found = hooks_common.find_active_task(cwd=str(self.root))
        self.assertIn(found, (older, newer))
        self.assertEqual(found, max((older, newer), key=hooks_common._sort_key))

    def test_several_active_tasks_and_no_match_stays_silent(self):
        """§8.3: «Несколько активных задач → ... иначе {}» — the hook must not guess."""
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        make_task(self.root, "memo-a")
        make_task(self.root, "memo-b")
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        self.assertIsNone(hooks_common.find_active_task(cwd=str(outside)))

    def test_no_task_at_all(self):
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        self.assertIsNone(hooks_common.find_active_task(cwd=str(self.root)))

    def test_terminal_task_is_not_active(self):
        work_dir = make_task(self.root, "memo-done", phase="done")
        self.assertIsNone(hooks_common.find_active_task(cwd=str(work_dir)))

    def test_v1_task_is_not_active(self):
        work_dir = make_task(self.root, "memo-legacy", schema_version=1)
        self.assertIsNone(hooks_common.find_active_task(cwd=str(work_dir)))

    def test_unreadable_state_is_not_active(self):
        work_dir = self.root / "memo-broken"
        work_dir.mkdir()
        (work_dir / "state.json").write_text("{ not json", encoding="utf-8")
        self.assertIsNone(hooks_common.find_active_task(cwd=str(work_dir)))

    def test_one_terminal_and_one_active_task_resolve_to_the_active_one(self):
        make_task(self.root, "memo-finished", phase="done")
        active = make_task(self.root, "memo-running")
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        outside = Path(tempfile.mkdtemp())
        self.assertEqual(hooks_common.find_active_task(cwd=str(outside)), active)


class OutputFolderCandidatesTest(_EnvMixin, unittest.TestCase):
    """D-93: an unexpanded `${…}` is never a work-dir candidate, at either `user_config` level."""

    PLACEHOLDER = "${plugin.output_folder}"

    def rows(self, explicit=None) -> list:
        return hooks_common.output_folder_candidates(explicit, cwd=self.root)

    def test_a_placeholder_host_option_is_not_a_candidate(self):
        os.environ["CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER"] = self.PLACEHOLDER
        rows = self.rows()
        self.assertNotIn("user_config", [row["source"] for row in rows])
        self.assertNotIn(self.PLACEHOLDER, [str(row["path"]) for row in rows])

    def test_a_real_host_option_is_still_the_first_candidate(self):
        os.environ["CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER"] = str(self.root)
        self.assertEqual(self.rows()[0], {"source": "user_config", "path": self.root})

    def test_a_placeholder_explicit_value_falls_through_to_the_env_variable(self):
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root)
        self.assertEqual(self.rows(self.PLACEHOLDER)[0], {"source": "env", "path": self.root})

    def test_the_placeholder_directory_is_never_searched_for_tasks(self):
        """The bug: `resolve_options` skipped it, then this seam read it back out of the env."""
        ghost = make_task(self.root / self.PLACEHOLDER, "memo-ghost")
        os.environ["CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER"] = str(ghost.parent)
        self.assertNotIn(ghost, hooks_common.task_dirs(cwd=self.root))


class ProjectFolderCandidateTest(_EnvMixin, unittest.TestCase):
    """D-104: the folder attached to the session comes before `~/Documents`, when it is a real one."""

    def setUp(self):
        super().setUp()
        self.session = self.root / "session"
        self.session.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        patcher = mock.patch.object(hooks_common, "home_dir", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sources(self, cwd=None) -> list:
        return [row["source"] for row in hooks_common.output_folder_candidates(cwd=cwd)]

    def rows(self, cwd=None) -> list:
        return hooks_common.output_folder_candidates(cwd=cwd)

    def test_the_whole_chain_in_order(self):
        os.environ["CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER"] = str(self.root / "option")
        os.environ["MEMOFORGE_OUTPUT_FOLDER"] = str(self.root / "env")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.session)
        rows = self.rows(cwd=self.root)
        self.assertEqual(
            [row["source"] for row in rows],
            ["user_config", "env", "project_folder", "home_documents", "cwd_outputs"],
        )
        self.assertEqual(rows[2]["path"], self.session / "memoforge")
        self.assertEqual(rows[3]["path"], self.home / "Documents" / "memoforge")

    def test_without_claude_project_dir_the_cwd_is_the_session_folder(self):
        rows = self.rows(cwd=self.session)
        self.assertEqual([row["source"] for row in rows], ["project_folder", "home_documents", "cwd_outputs"])
        self.assertEqual(rows[0]["path"], self.session / "memoforge")

    def test_the_plugin_root_is_not_a_session_folder(self):
        (self.session / ".claude-plugin").mkdir()
        (self.session / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.session)
        self.assertNotIn("project_folder", self.sources(cwd=self.root))

    def test_the_plugin_root_from_the_environment_is_not_a_session_folder(self):
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(self.session)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.session)
        self.assertNotIn("project_folder", self.sources(cwd=self.root))

    def test_the_home_directory_itself_is_not_a_session_folder(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.home)
        self.assertNotIn("project_folder", self.sources(cwd=self.root))
        self.assertIn("home_documents", self.sources(cwd=self.root))

    def test_a_filesystem_root_is_not_a_session_folder(self):
        os.environ["CLAUDE_PROJECT_DIR"] = self.root.anchor
        self.assertNotIn("project_folder", self.sources(cwd=self.root))

    def test_a_placeholder_project_dir_is_not_a_session_folder(self):
        os.environ["CLAUDE_PROJECT_DIR"] = "${CLAUDE_PROJECT_DIR}"
        self.assertNotIn("project_folder", self.sources(cwd=self.root))

    def test_a_task_in_the_project_folder_is_found(self):
        work_dir = make_task(self.session / "memoforge", "memo-in-project")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.session)
        self.assertIn(work_dir, hooks_common.task_dirs(cwd=self.root))
        self.assertEqual(hooks_common.find_active_task(cwd=self.root), work_dir)


class PredicateTest(unittest.TestCase):
    def test_is_v2_and_is_active(self):
        self.assertFalse(hooks_common.is_v2(None))
        self.assertFalse(hooks_common.is_v2({"schema_version": 1}))
        self.assertTrue(hooks_common.is_v2({"schema_version": 2}))
        self.assertTrue(hooks_common.is_active({"schema_version": 2, "current_phase": "research"}))
        self.assertFalse(hooks_common.is_active({"schema_version": 2, "current_phase": "done"}))
        self.assertFalse(hooks_common.is_active({"schema_version": 2, "current_phase": "nope"}))

    def test_read_json_quiet_never_raises(self):
        self.assertIsNone(hooks_common.read_json_quiet("/does/not/exist.json"))

    def test_events_path_is_fixed(self):
        self.assertEqual(hooks_common.events_path("/w").name, "events.jsonl")


class StdlibOnlyTest(unittest.TestCase):
    def test_hooks_common_imports_neither_state_io_nor_jsonschema(self):
        """M2: hooks only append to events.jsonl; importing the state writer would invite writes."""
        import ast

        source = (PLUGIN_ROOT / "scripts" / "memoforge" / "hooks_common.py").read_text(
            encoding="utf-8"
        )
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertNotIn("state_io", imported)
        self.assertNotIn("jsonschema", imported)
        self.assertNotIn("schema", imported)


if __name__ == "__main__":
    unittest.main()
