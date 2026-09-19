"""Tests for scripts/memoforge/docs_render.py — `mf docs render [--check]` (M11, ТЗ §2.3, §8.2, §9)."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import docs_render, events, fallbacks, limits, modes, phases  # noqa: E402


def _committed_permissions() -> str:
    return (PLUGIN_ROOT / "docs" / "permissions.md").read_text(encoding="utf-8-sig")


def render_args(root: Path, **overrides) -> argparse.Namespace:
    payload = {"root": str(root), "target": "all", "check": False, "human": False}
    payload.update(overrides)
    return argparse.Namespace(**payload)


class GeneratorTest(unittest.TestCase):
    def test_phases_doc_lists_every_phase_exactly_once(self):
        text = docs_render.render_phases()
        for phase in phases.PHASES:
            self.assertEqual(text.count(f"| `{phase}` |"), 1, phase)
        self.assertIn("terminal", text)
        self.assertIn("gate", text)

    def test_phases_doc_carries_the_plain_english_label_of_every_phase(self):
        """D-95: the label table is generated from `phases.PHASE_LABELS`, never hand-written."""
        text = docs_render.render_phases()
        self.assertIn("| # | phase | kind | label (D-95) |", text)
        for phase in phases.PHASES:
            with self.subTest(phase=phase):
                self.assertIn(f"| `{phase}` | ", text)
                self.assertIn(f"| {phases.label(phase)} |", text)

    def test_modes_doc_carries_the_matrix_numbers(self):
        text = docs_render.render_modes()
        self.assertIn("| `max_iterations` | `2` | `2` |", text)
        self.assertIn("executive-brief", text)
        self.assertIn("classical-memo", text)
        for name in limits.ALLOWED_WRITER_MODELS:
            self.assertIn(f"`{name}`", text)
        for mode in modes.MODES:
            self.assertIn(mode, text)

    def test_events_doc_lists_every_event_type(self):
        text = docs_render.render_events()
        for name in events.EVENT_TYPES:
            self.assertIn(f"| `{name}` |", text)
        self.assertIn(str(limits.EVENT_LINE_MAX_BYTES), text)

    def test_always_deliver_doc_carries_every_row_and_banner_text(self):
        text = docs_render.render_always_deliver()
        for row in fallbacks.FALLBACKS:
            self.assertIn(row["condition_key"], text)
            if row["banner_id"]:
                self.assertIn(row["banner_id"], text)
                self.assertIn(row["banner_text"].split("{")[0].strip(), text)

    def test_every_generated_file_is_marked_as_generated(self):
        for name, content in docs_render.targets(PLUGIN_ROOT).items():
            if content is None:
                continue
            self.assertIn(docs_render.GENERATED_HEADER, content, name)


class PermissionsTest(unittest.TestCase):
    def test_permissions_is_skipped_when_the_allowlist_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(docs_render.render_permissions(Path(tmp)))
            result = docs_render.run_render(render_args(Path(tmp)))
        self.assertEqual([row["file"] for row in result["skipped"]], ["permissions.md"])
        self.assertIn("allowlist.txt", result["skipped"][0]["reason"])

    def test_permissions_block_is_built_from_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hooks").mkdir()
            (root / "hooks" / "allowlist.txt").write_text(
                "# comment\neur-lex.europa.eu\ncuria.europa.eu\n\n", encoding="utf-8"
            )
            text = docs_render.render_permissions(root)
        self.assertIn('"WebFetch(domain:eur-lex.europa.eu)"', text)
        self.assertIn('"WebFetch(domain:*.eur-lex.europa.eu)"', text)
        self.assertIn('"WebFetch(domain:curia.europa.eu)"', text)
        self.assertIn("mcp__plugin_memoforge_legal-data-hunter__*", text)
        self.assertIn("mcp__plugin_memoforge_courtlistener__*", text)
        self.assertIn("mcp__plugin_memoforge_federal-regulations__*", text)
        self.assertIn("mcp__plugin_memoforge_lex__*", text)
        self.assertIn("mcp__plugin_memoforge_casus__*", text)
        self.assertIn("mcp__plugin_memoforge_fas-search__*", text)
        self.assertIn("scripts/mf *", text)
        allow_block = text.split("```json", 1)[1].split("```", 1)[0]
        self.assertNotIn("Agent(", allow_block, "globs for Agent are not confirmed (§8.2)")
        self.assertIn("mcp__workspace__*", text, "the Cowork warning of §8.2 is mandatory")

    def test_bash_rule_keeps_the_plugin_root_placeholder(self):
        """D-26: no machine-specific path leaks into the block; the README line explains the swap."""
        for text in (docs_render.render_permissions(PLUGIN_ROOT), _committed_permissions()):
            self.assertIn('"Bash(${CLAUDE_PLUGIN_ROOT}/scripts/mf *)"', text)
            self.assertIn("замените `${CLAUDE_PLUGIN_ROOT}` на путь установки плагина", text)
            self.assertIsNone(
                re.search(r"[A-Za-z]:[\\/]", text), "a drive letter is machine-specific"
            )
            self.assertNotIn('"Bash(/', text, "an absolute path is machine-specific")
            self.assertNotIn(PLUGIN_ROOT.as_posix(), text)
            self.assertNotIn(str(PLUGIN_ROOT), text)

    def test_comments_and_blanks_are_stripped_from_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hooks").mkdir()
            (root / "hooks" / "allowlist.txt").write_text(
                "justia.com  # optional group\n\njustia.com\n", encoding="utf-8"
            )
            hosts = docs_render.read_allowlist(root)
        self.assertEqual(hosts, ["justia.com"])


class RenderCommandTest(unittest.TestCase):
    def make_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        return root

    def test_render_writes_every_document(self):
        root = self.make_root()
        result = docs_render.run_render(render_args(root))
        self.assertEqual(
            sorted(result["written"]),
            ["always-deliver.md", "events.md", "modes.md", "phases.md"],
        )
        for name in result["written"]:
            self.assertTrue((root / "docs" / name).is_file())

    def test_check_passes_right_after_a_render(self):
        root = self.make_root()
        docs_render.run_render(render_args(root))
        result = docs_render.run_render(render_args(root, check=True))
        self.assertEqual(result["mismatched"], [])
        self.assertNotIn("errors", result)
        self.assertEqual(result["written"], [])

    def test_check_fails_when_a_document_drifted(self):
        root = self.make_root()
        docs_render.run_render(render_args(root))
        (root / "docs" / "modes.md").write_text("hand-edited\n", encoding="utf-8")
        result = docs_render.run_render(render_args(root, check=True))
        self.assertEqual([row["file"] for row in result["mismatched"]], ["modes.md"])
        self.assertTrue(result["errors"])
        self.assertEqual(
            (root / "docs" / "modes.md").read_text(encoding="utf-8"),
            "hand-edited\n",
            "--check must not write",
        )

    def test_check_fails_when_a_document_is_missing(self):
        root = self.make_root()
        result = docs_render.run_render(render_args(root, check=True))
        self.assertEqual(len(result["mismatched"]), 4)
        self.assertTrue(result["errors"])

    def test_a_single_target_can_be_rendered(self):
        root = self.make_root()
        result = docs_render.run_render(render_args(root, target="modes"))
        self.assertEqual(result["written"], ["modes.md"])
        self.assertFalse((root / "docs" / "phases.md").exists())

    def test_unknown_target_is_a_business_error(self):
        root = self.make_root()
        result = docs_render.run_render(render_args(root, target="nope"))
        self.assertTrue(result["errors"])

    def test_render_is_idempotent(self):
        root = self.make_root()
        docs_render.run_render(render_args(root))
        result = docs_render.run_render(render_args(root))
        self.assertEqual(result["written"], [])
        self.assertEqual(len(result["unchanged"]), 4)


class RepositoryDocsTest(unittest.TestCase):
    """The committed `docs/` must match the code; CI runs `mf docs render --check` (§9)."""

    def test_committed_docs_are_up_to_date(self):
        result = docs_render.run_render(render_args(PLUGIN_ROOT, check=True))
        self.assertEqual(
            result["mismatched"],
            [],
            "run `mf docs render` — a source of truth in phases/modes/events/fallbacks changed",
        )


if __name__ == "__main__":
    unittest.main()
