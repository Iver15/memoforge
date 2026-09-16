"""D-27/D-73: `scripts/ci/check_versions.py` holds one version in four places.

The mismatch cases run a copy of the script inside a temporary plugin root, so the real
`.claude-plugin/`, `README.md` and `CHANGELOG.md` are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "ci" / "check_versions.py"

VERSION = "9.9.9-test"
PLUGIN_JSON = {"name": "memoforge", "version": VERSION}
MARKETPLACE_JSON = {
    "name": "memoforge",
    "plugins": [{"name": "memoforge", "source": "./", "version": VERSION}],
}
README = "# memoforge\n\n![version](https://img.shields.io/badge/version-9.9.9--test-blue)\n"
CHANGELOG = "# Changelog\n\n## 9.9.9-test — 2026-09-09 (test)\n\nNotes.\n"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run(root: Path) -> subprocess.CompletedProcess:
    """Run the guard with `root` as its plugin root (it derives that from its own path)."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ci" / "check_versions.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )


class CheckVersionsTest(unittest.TestCase):
    def make_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts" / "ci").mkdir(parents=True)
        shutil.copy2(SCRIPT, root / "scripts" / "ci" / "check_versions.py")
        write_json(root / ".claude-plugin" / "plugin.json", PLUGIN_JSON)
        write_json(root / ".claude-plugin" / "marketplace.json", MARKETPLACE_JSON)
        (root / "README.md").write_text(README, encoding="utf-8")
        (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        return root

    def test_the_repository_itself_passes(self):
        result = run(PLUGIN_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_versions: OK", result.stdout)

    def test_the_ok_line_names_the_marketplace_manifest(self):
        result = run(PLUGIN_ROOT)
        self.assertIn("marketplace.json", result.stdout)

    def test_a_copy_in_agreement_passes(self):
        result = run(self.make_root())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(VERSION, result.stdout)

    def test_marketplace_version_drift_fails(self):
        root = self.make_root()
        drifted = {"name": "memoforge", "plugins": [{"name": "memoforge", "source": "./", "version": "2.0.0"}]}
        write_json(root / ".claude-plugin" / "marketplace.json", drifted)
        result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("check_versions: FAIL", result.stderr)
        self.assertIn("marketplace.json plugins[0].version is '2.0.0'", result.stderr)

    def test_marketplace_without_a_plugin_entry_fails(self):
        root = self.make_root()
        write_json(root / ".claude-plugin" / "marketplace.json", {"name": "memoforge", "plugins": []})
        result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("marketplace.json has no plugins[0] entry", result.stderr)

    def test_readme_badge_drift_still_fails(self):
        root = self.make_root()
        (root / "README.md").write_text(
            README.replace("9.9.9--test", "8.8.8"), encoding="utf-8"
        )
        result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("README badge is '8.8.8'", result.stderr)

    def test_changelog_heading_drift_still_fails(self):
        root = self.make_root()
        (root / "CHANGELOG.md").write_text(
            CHANGELOG.replace("## 9.9.9-test", "## 8.8.8"), encoding="utf-8"
        )
        result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("CHANGELOG top section is '8.8.8'", result.stderr)


if __name__ == "__main__":
    unittest.main()
