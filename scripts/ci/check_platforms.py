#!/usr/bin/env python3
"""Extract both native ZIPs outside the checkout, run fixtures and inspect Russian DOCX."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_platforms import build_package, validate_package  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="Memoforge Проба с пробелами ") as tmp:
        base = Path(tmp)
        for platform in ("codex", "zcode"):
            built = build_package(platform, base / "build")
            archive = next((base / "build").glob(built.name + "-*.zip"))
            target = base / "extracted" / platform
            with zipfile.ZipFile(archive) as z:
                z.extractall(target)
            root = target / built.name
            errors = validate_package(root)
            if errors:
                raise RuntimeError(errors)
            env = dict(os.environ, HOME=str(base / "home"), LOCALAPPDATA=str(base / "local"))
            for key in (
                "MEMOFORGE_PLATFORM",
                "CLAUDE_PLUGIN_ROOT",
                "CLAUDE_PLUGIN_DATA",
                "PLUGIN_ROOT",
                "PLUGIN_DATA",
                "ZCODE_PLUGIN_ROOT",
                "ZCODE_PLUGIN_DATA",
            ):
                env.pop(key, None)
            # CI dependencies are installed in its interpreter, never copied from another host.
            cli = [sys.executable, str(root / "scripts/memoforge/__main__.py")]
            subprocess.run(cli + ["--version"], env=env, cwd=base, check=True)
            task_root = (base / "tasks" / platform).resolve()
            run = subprocess.run(
                cli + ["probe", "dry-run", "--workdir", str(task_root), "--language", "ru", "--ui-language", "ru"],
                env=env,
                cwd=base,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=True,
            )
            result = json.loads(run.stdout)
            if not result.get("ok"):
                raise RuntimeError(result)
            work = Path(result["work_dir"])
            state = json.loads((work / "state.json").read_text(encoding="utf-8"))
            docx = work / state["final_docx_path"]
            with zipfile.ZipFile(docx) as z:
                if z.testzip():
                    raise RuntimeError("broken_docx_archive")
                xml = z.read("word/document.xml").decode("utf-8")
                if not any("А" <= c <= "я" for c in xml):
                    raise RuntimeError("missing_russian_docx_text")
            print(
                json.dumps(
                    {
                        "platform": platform,
                        "ok": True,
                        "phase": result["final_phase"],
                        "docx": str(docx),
                        "evidence": "offline_fixture",
                    },
                    ensure_ascii=False,
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
