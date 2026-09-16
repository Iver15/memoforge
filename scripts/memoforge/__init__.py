"""memoforge v2 CLI package: deterministic state machine for the memo pipeline."""

from __future__ import annotations

import json
from pathlib import Path

FALLBACK_VERSION = "0.0.0"
PLUGIN_MANIFEST = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


def _read_version() -> str:
    """D-27: the single source of the plugin version is `.claude-plugin/plugin.json`."""
    try:
        with open(PLUGIN_MANIFEST, encoding="utf-8-sig") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return FALLBACK_VERSION
    version = manifest.get("version")
    return version if isinstance(version, str) and version else FALLBACK_VERSION


__version__ = _read_version()
