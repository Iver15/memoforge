#!/usr/bin/env python3
"""Register and install only the requested native plugin, then read back its identity."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ZCODE_ENTRY = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")


def host_command(platform: str) -> list[str]:
    if platform == "codex":
        return [shutil.which("codex") or "codex"]
    if platform == "zcode":
        if ZCODE_ENTRY.is_file():
            return [shutil.which("node") or "node", str(ZCODE_ENTRY)]
        return [shutil.which("zcode") or "zcode"]
    raise ValueError("invalid_platform: " + platform)


def run_json(argv: list[str], timeout: int = 120) -> object:
    completed = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if completed.returncode:
        # Diagnostic messages are native CLI errors, never user configuration content.
        raise RuntimeError("cli_failed: " + str(completed.returncode) + " " + completed.stderr.strip()[:1000])
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError("invalid_cli_json") from exc
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError("cli_reported_failure: " + json.dumps(result.get("diagnostics", [])))
    return result


def install_platform(platform: str, marketplace_root: Path) -> dict:
    command = host_command(platform)
    plugin = "memoforge-" + platform
    marketplace = plugin + "-local"
    result = {"ok": False, "plugin": plugin, "errors": []}
    try:
        if platform == "codex":
            run_json(command + ["plugin", "marketplace", "add", str(marketplace_root.resolve()), "--json"])
            installed = run_json(command + ["plugin", "add", plugin + "@" + marketplace, "--json"])
            readback = run_json(command + ["plugin", "list", "--marketplace", marketplace, "--json"])
            rows = readback.get("installed", []) if isinstance(readback, dict) else []
        else:
            run_json(command + ["plugins", "validate", str(marketplace_root / plugin), "--json"])
            run_json(command + ["plugins", "marketplace", "add", str(marketplace_root.resolve()), "--json"])
            installed = run_json(command + ["plugins", "install", plugin + "@" + marketplace, "--json"])
            # Install may reuse the same-version cache; update refreshes generated content.
            run_json(command + ["plugins", "update", plugin + "@" + marketplace, "--json"])
            readback = run_json(command + ["plugins", "list", "--json"])
            rows = readback if isinstance(readback, list) else []
        expected_version = (
            json.loads(
                (marketplace_root / plugin / ("." + platform + "-plugin/plugin.json")).read_text(encoding="utf-8")
            )["version"]
            if (marketplace_root / plugin).is_dir()
            else "2.1.0"
        )
        matches = [
            r
            for r in rows
            if r.get("name") == plugin and r.get("version") == expected_version and r.get("enabled") is True
        ]
        if not matches:
            result["errors"].append("plugin_readback_mismatch")
        else:
            row = matches[0]
            result.update(
                ok=True,
                version=row["version"],
                installed_plugin=row.get("pluginId") or row.get("id"),
                installed_path=row.get("installPath")
                or row.get("rootPath")
                or (installed.get("installedPath") if isinstance(installed, dict) else None),
            )
            if not result["installed_path"]:
                result["ok"] = False
                result["errors"].append("missing_installed_path")
    except subprocess.TimeoutExpired:
        result["errors"].append("timeout: native installation exceeded 120 seconds")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        result["errors"].append(str(exc))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("codex", "zcode"))
    parser.add_argument("marketplace_root", type=Path)
    args = parser.parse_args()
    result = install_platform(args.platform, args.marketplace_root)
    if result["ok"] and result.get("installed_path"):
        launcher = Path(result["installed_path"]) / "scripts" / ("mf.cmd" if os.name == "nt" else "mf")
        # Native cache installation and runtime dependencies have independent outcomes.
        try:
            result["dependencies"] = run_json([str(launcher), "deps", "install"])
            result["dependency_check"] = run_json([str(launcher), "deps", "check"])
            if not result["dependency_check"].get("ok"):
                result["errors"].append("dependency_readback_failed")
                result["ok"] = False
            if result["dependencies"].get("errors"):
                result["errors"].extend(result["dependencies"]["errors"])
                result["ok"] = False
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result["errors"].append("dependency_install_failed: " + str(exc))
            result["ok"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
