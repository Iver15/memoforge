"""ТЗ §9 / T-01: `docs/state.md` and `schemas/state.schema.json` name exactly the same fields."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "state.schema.json"
DOC_PATH = PLUGIN_ROOT / "docs" / "state.md"

BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\[\])?`")
"""One field name in the doc: `steps[]` and `steps` are the same property."""

CELL_SPLIT = re.compile(r"(?<!\\)\|")
"""Markdown cell separator; `\\|` inside a cell (the `kind` enum of `steps[]`) is not one."""

# D-74: the hooks read these two from `CLAUDE_PLUGIN_OPTION_*`; they are not part of `state.config`.
# `dashboard` left this list with D-87: `mf next` reads it from `state.config` (ТЗ §7.5).
HOOK_ONLY_OPTIONS = ("stop_guard", "websearch_autoallow")


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))


def table_rows() -> list[tuple[str, str]]:
    """Every two-column row of the field tables of `docs/state.md`, header and ruler dropped."""
    rows: list[tuple[str, str]] = []
    for line in DOC_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in CELL_SPLIT.split(line)[1:-1]]
        if len(cells) != 2:
            continue
        if cells[0] == "Field" or set(cells[0]) <= set("-: "):
            continue
        rows.append((cells[0], cells[1]))
    return rows


def documented_fields() -> set[str]:
    """Field names in the first column of the tables — the documented top-level properties."""
    names: set[str] = set()
    for first, _ in table_rows():
        names.update(BACKTICKED.findall(first))
    return names


def documented_config_keys() -> set[str]:
    """Field names listed in the `config` row — the documented `config` keys."""
    for first, second in table_rows():
        if BACKTICKED.findall(first) == ["config"]:
            return set(BACKTICKED.findall(second))
    raise AssertionError("docs/state.md has no `config` row")


class StateFieldsAreDocumentedTest(unittest.TestCase):
    def test_the_doc_and_the_schema_exist(self):
        self.assertTrue(SCHEMA_PATH.is_file(), SCHEMA_PATH)
        self.assertTrue(DOC_PATH.is_file(), DOC_PATH)

    def test_every_top_level_property_is_documented(self):
        missing = sorted(set(load_schema()["properties"]) - documented_fields())
        self.assertEqual([], missing, "properties of state.schema.json missing from docs/state.md")

    def test_no_documented_field_is_missing_from_the_schema(self):
        extra = sorted(documented_fields() - set(load_schema()["properties"]))
        self.assertEqual([], extra, "fields documented in docs/state.md that the schema does not define")

    def test_every_config_key_is_documented(self):
        keys = set(load_schema()["$defs"]["config"]["properties"])
        missing = sorted(keys - documented_config_keys())
        self.assertEqual([], missing, "config keys of state.schema.json missing from docs/state.md")

    def test_no_documented_config_key_is_missing_from_the_schema(self):
        keys = set(load_schema()["$defs"]["config"]["properties"])
        extra = sorted(documented_config_keys() - keys)
        self.assertEqual([], extra, "config keys documented in docs/state.md that the schema does not define")

    def test_the_hook_only_options_are_not_part_of_config(self):
        """D-74: `stop_guard` / `websearch_autoallow` live in the environment, not in state."""
        keys = set(load_schema()["$defs"]["config"]["properties"])
        documented = documented_config_keys()
        for name in HOOK_ONLY_OPTIONS:
            self.assertNotIn(name, keys, name)
            self.assertNotIn(name, documented, name)

    def test_the_dashboard_flag_is_part_of_config(self):
        """D-87: §7.5 is decided by `state.config.dashboard`, so schema and doc must both carry it."""
        self.assertIn("dashboard", load_schema()["$defs"]["config"]["properties"])
        self.assertIn("dashboard", documented_config_keys())


if __name__ == "__main__":
    unittest.main()
