"""Guard for D-13: `state_io.write_state_unvalidated` exists only for `finalize --salvage` (M2 vs M9)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PLUGIN_ROOT / "scripts" / "memoforge"

FUNCTION = "write_state_unvalidated"
DEFINITION_FILE = "state_io.py"  # defines it
ALLOWED_CALLER = "finalize.py"  # the single caller D-13 allows


def package_sources() -> list[Path]:
    return sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)


def references(path: Path) -> list[int]:
    """Line numbers where the module references the name in code (docstrings/comments excluded)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == FUNCTION:
            continue  # the definition itself
        if isinstance(node, ast.Attribute) and node.attr == FUNCTION:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == FUNCTION:
            lines.append(node.lineno)
    return sorted(set(lines))


class NoUnvalidatedStateWritesTest(unittest.TestCase):
    def test_the_package_has_sources_to_scan(self):
        self.assertGreater(len(package_sources()), 10)

    def test_only_finalize_references_the_unvalidated_writer(self):
        offenders = []
        callers = set()
        for path in package_sources():
            if path.name == DEFINITION_FILE:
                continue
            source_lines = path.read_text(encoding="utf-8").splitlines()
            for number in references(path):
                callers.add(path.name)
                if path.name != ALLOWED_CALLER:
                    relative = path.relative_to(PLUGIN_ROOT).as_posix()
                    offenders.append(f"{relative}:{number}: {source_lines[number - 1].strip()}")
        self.assertEqual(
            offenders,
            [],
            "D-13: only finalize.py may use write_state_unvalidated; every other write goes "
            "through write_state, which validates before os.replace (M2)",
        )
        self.assertEqual(callers, {ALLOWED_CALLER}, "finalize.py must remain the one caller")

    def test_the_definition_is_where_it_is_expected(self):
        source = (PACKAGE / DEFINITION_FILE).read_text(encoding="utf-8")
        self.assertIn(f"def {FUNCTION}(", source)
        self.assertIn("salvage", source, "the exception must stay documented at the definition")

    def test_the_salvage_reference_is_guarded_by_the_flag(self):
        path = PACKAGE / ALLOWED_CALLER
        source_lines = path.read_text(encoding="utf-8").splitlines()
        found = references(path)
        self.assertTrue(found, "finalize.py must still reach the salvage writer")
        for number in found:
            self.assertIn("salvage", source_lines[number - 1], f"{ALLOWED_CALLER}:{number}")


if __name__ == "__main__":
    unittest.main()
