#!/usr/bin/env python
"""CI guard: one version, four places — `plugin.json`, `marketplace.json`, the README badge, the changelog.

D-27 makes `.claude-plugin/plugin.json` the single source of the plugin version. This script proves
the copies did not drift: `plugins[0].version` of the `.claude-plugin/marketplace.json` manifest
D-73 adds for `/plugin marketplace add`, the shields.io badge in `README.md` (where `-` is written
`--`) and the first `## <version> — <date>` heading of `CHANGELOG.md`.

    python scripts/ci/check_versions.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = PLUGIN_ROOT / ".claude-plugin" / "marketplace.json"
README = PLUGIN_ROOT / "README.md"
CHANGELOG = PLUGIN_ROOT / "CHANGELOG.md"

BADGE = re.compile(r"img\.shields\.io/badge/version-(?P<version>.+?)-[a-z]+\)")
HEADING = re.compile(r"^##\s+(?P<version>[0-9][^\s]*)\s+—", re.M)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def unescape_badge(label: str) -> str:
    """shields.io writes a literal `-` as `--`; versions here carry no `_` or spaces."""
    return label.replace("--", "-")


def main() -> int:
    manifest = json.loads(read(MANIFEST))
    expected = manifest["version"]

    entries = json.loads(read(MARKETPLACE)).get("plugins") or []
    badge = BADGE.search(read(README))
    heading = HEADING.search(read(CHANGELOG))

    problems: list[str] = []
    if not entries:
        problems.append("marketplace.json has no plugins[0] entry")
    elif entries[0].get("version") != expected:
        problems.append(
            f"marketplace.json plugins[0].version is {entries[0].get('version')!r}, manifest is {expected!r}"
        )

    if badge is None:
        problems.append("README.md has no shields.io version badge")
    elif unescape_badge(badge.group("version")) != expected:
        problems.append(
            f"README badge is {unescape_badge(badge.group('version'))!r}, manifest is {expected!r}"
        )

    if heading is None:
        problems.append("CHANGELOG.md has no '## <version> — <date>' heading")
    elif heading.group("version") != expected:
        problems.append(
            f"CHANGELOG top section is {heading.group('version')!r}, manifest is {expected!r}"
        )

    if problems:
        print("check_versions: FAIL", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"check_versions: OK — {expected} in plugin.json, marketplace.json, README badge and CHANGELOG")
    return 0


if __name__ == "__main__":
    sys.exit(main())
