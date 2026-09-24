"""§9 `test_no_legacy`: the v1 identifiers must not survive anywhere in the shipped tree.

The four channels of §7.1 (live-progress renderer, widget MCP, artifact updates, forced
`TodoWrite`), the v1 mode names, the `PHASE-MACHINE` cheat-sheet and the `python3` hard-code
of M12 are removed, not deprecated. History keeps them: `docs/attic/`, `docs/postmortems/`,
`CHANGELOG.md` and `docs/dev/` are the documented exceptions and are never scanned.
"""

from __future__ import annotations

import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

# §9: the scanned scope.
SCOPE_DIRS = ("skills", "agents", "lib", "scripts", "hooks", "templates", "schemas")
SCOPE_FILES = ("README.md",)

# §9: the documented exceptions — history and developer notes may name what the code may not.
EXCEPTIONS = ("docs/attic/", "docs/postmortems/", "docs/dev/", "CHANGELOG.md")

# §9: the identifiers. `Quick/Standard/Deep` are the v1 mode names (v2 has one mode, Full, since
# D-242); `executive-brief` is the template removed with the Brief mode (D-243, D-244);
# `python3 "` is the Windows Store stub of M12 — bare `python3` inside prose is not a defect.
IDENTIFIERS = (
    "heartbeat",
    "visualize",
    "update_artifact",
    "TodoWrite",
    "mark_chapter",
    "research-summary-only",
    "Quick mode",
    "Standard mode",
    "Deep mode",
    "${CLAUDE_PLUGIN_DATA}/work",
    'python3 "',
    "PHASE-MACHINE",
    "live_progress",
    "executive-brief",
)

# The test tree may name the identifiers it forbids; so may this file. Fixtures replay v1 data.
SKIPPED_PARTS = ("__pycache__", ".git", "fixtures")
SKIPPED_SUFFIXES = (".pyc", ".pyo", ".webp", ".png", ".docx", ".zip")
TESTS_DIR = Path("scripts") / "tests"

# D-48: no exception is needed any more — the schema describes the removed `config.*` keys without
# naming them, so the scan below covers every shipped file without a carve-out.
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def scanned_files() -> list[Path]:
    """Every text file of the §9 scope, minus guard tests, fixtures and build artefacts."""
    paths: list[Path] = []
    for name in SCOPE_DIRS:
        paths.extend(p for p in (PLUGIN_ROOT / name).rglob("*") if p.is_file())
    paths.extend(PLUGIN_ROOT / name for name in SCOPE_FILES)
    keep = []
    for path in paths:
        rel = path.relative_to(PLUGIN_ROOT)
        if any(part in SKIPPED_PARTS for part in rel.parts):
            continue
        if path.suffix in SKIPPED_SUFFIXES:
            continue
        if rel.parent == TESTS_DIR and path.suffix == ".py":
            continue
        keep.append(path)
    return sorted(keep)


def hits() -> list[str]:
    """`<posix path>:<line>: <identifier>` for every un-allowed occurrence."""
    found: list[str] = []
    for path in scanned_files():
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, ValueError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for identifier in IDENTIFIERS:
                if identifier in line and (rel, identifier) not in ALLOWED:
                    found.append(f"{rel}:{number}: {identifier}")
    return found


class NoLegacyTest(unittest.TestCase):
    def test_no_legacy_identifiers_in_shipped_tree(self):
        found = hits()
        self.assertEqual(found, [], "legacy v1 identifiers survive:\n  " + "\n  ".join(found))

    def test_scope_directories_exist(self):
        for name in SCOPE_DIRS:
            self.assertTrue((PLUGIN_ROOT / name).is_dir(), f"{name}/ is missing")
        for name in SCOPE_FILES:
            self.assertTrue((PLUGIN_ROOT / name).is_file(), f"{name} is missing")

    def test_exceptions_are_outside_the_scope(self):
        """The archive is excluded by construction, not by a filter someone can widen."""
        scanned = {path.relative_to(PLUGIN_ROOT).as_posix() for path in scanned_files()}
        for exception in EXCEPTIONS:
            self.assertFalse(
                any(rel == exception or rel.startswith(exception) for rel in scanned),
                f"{exception} must not be scanned",
            )

    def test_archive_still_holds_the_identifiers(self):
        """Non-tautology: the words really do exist in the repo, only not in shipped code."""
        attic = (PLUGIN_ROOT / "docs" / "attic").rglob("*.md")
        archived = "\n".join(
            path.read_text(encoding="utf-8-sig", errors="replace") for path in attic
        )
        for identifier in ("live_progress", "TodoWrite", "update_artifact", "research-summary-only"):
            self.assertIn(identifier, archived, f"{identifier} vanished from docs/attic/")

    def test_v1_scripts_are_deleted(self):
        """§0.4: the v1 scripts and their tests are gone, replaced by `scripts/memoforge/`."""
        removed = [
            "scripts/validate_state.py",
            "scripts/validate_review_json.py",
            "scripts/log_event.py",
            "scripts/analyze_run.py",
            "scripts/tidy_workdir.py",
            "scripts/resolve_style_profile.py",
            "scripts/render_live_progress.py",
            "scripts/resolve_work_dir.sh",
            "scripts/tests/test_validate_state.py",
            "scripts/tests/test_validate_review_json.py",
            "scripts/tests/test_log_event.py",
            "scripts/tests/test_analyze_run.py",
            "scripts/tests/test_tidy_workdir.py",
            "scripts/tests/test_resolve_style_profile.py",
            "scripts/tests/test_md_to_docx_banner.py",
            "scripts/tests/test_render_live_progress.py",
            "scripts/tests/test_phase_machine_coverage.py",
            "lib/docx-render",
            "skills/memo/PHASE-MACHINE.md",
        ]
        alive = [name for name in removed if (PLUGIN_ROOT / name).exists()]
        self.assertEqual(alive, [], f"v1 files still present: {alive}")

    def test_allowed_entries_are_still_needed(self):
        """An allowance that no longer matches anything must be deleted, not left to rot."""
        for rel, identifier in sorted(ALLOWED):
            path = PLUGIN_ROOT / rel
            self.assertTrue(path.is_file(), f"{rel} is gone — drop its ALLOWED entry")
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn(identifier, text, f"{rel} no longer contains {identifier!r}")


if __name__ == "__main__":
    unittest.main()
