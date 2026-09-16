"""D-27: `memoforge.__version__` is read from `.claude-plugin/plugin.json` — its single source."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import memoforge  # noqa: E402

MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"


class VersionTest(unittest.TestCase):
    def test_version_matches_plugin_json(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
        self.assertEqual(memoforge.__version__, manifest["version"])

    def test_manifest_path_points_at_the_plugin_manifest(self):
        self.assertEqual(memoforge.PLUGIN_MANIFEST, MANIFEST)

    def test_missing_manifest_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = memoforge.PLUGIN_MANIFEST
            memoforge.PLUGIN_MANIFEST = Path(tmp) / "plugin.json"
            try:
                self.assertEqual(memoforge._read_version(), "0.0.0")
            finally:
                memoforge.PLUGIN_MANIFEST = original


if __name__ == "__main__":
    unittest.main()
