#!/usr/bin/env python3
"""Run native host acceptance in a disposable work folder and retain explicit evidence."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from install_platform import host_command, run_json


def run_session(platform: str, work_dir: Path, prompt: str, label: str, timeout_seconds: int) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    log = work_dir / (label + ".log")
    command = host_command(platform)
    if platform == "codex":
        argv = command + [
            "exec",
            "--sandbox",
            "danger-full-access",
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(work_dir),
            prompt,
        ]
    else:
        argv = command + [
            "--cwd",
            str(work_dir),
            "--surface",
            "terminal",
            "--mode",
            "yolo",
            "--prompt",
            prompt,
            "--json",
        ]
    env = dict(os.environ)
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
    if platform == "zcode":
        bundled = Path("/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json")
        if bundled.is_file():
            env.setdefault("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", str(bundled))
    with log.open("w") as output:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                start_new_session=(os.name == "posix"),
            )
            code = process.wait(timeout=timeout_seconds)
            return {"exit_code": code, "log": str(log)}
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
                process.kill()
            process.wait()
            return {"exit_code": None, "log": str(log), "error": "timeout"}
        except OSError as exc:
            return {"exit_code": None, "log": str(log), "error": str(exc)}


def collect_evidence(platform: str, work_dir: Path) -> dict:
    """Read actual CLI state/output; never turn an agent's assertion into acceptance."""
    result = {"errors": [], "loaded_skills": [], "task_path": None, "dispatch_result": None, "gate_result": None}
    evidence = work_dir / "intake-evidence.json"
    if not evidence.is_file():
        result["errors"].append("missing_native_intake_evidence")
        return result
    try:
        data = json.loads(evidence.read_text(encoding="utf-8"))
        result["errors"].extend(data.get("errors", []))
        result["loaded_skills"] = [name.rsplit(":", 1)[-1] for name in data.get("loaded_skills", [])]
        if set(result["loaded_skills"]) != {"memo", "continue", "status", "style", "brief"}:
            result["errors"].append("missing_native_skills")
        task_path = Path(data.get("task_path", "")).resolve()
        if not task_path.is_relative_to((work_dir / "tasks").resolve()):
            result["errors"].append("task_outside_validation_folder")
            return result
        state = json.loads((task_path / "state.json").read_text(encoding="utf-8"))
        result["task_path"] = str(task_path)
        if (
            state.get("runtime_platform") != platform
            or state.get("language") != "ru"
            or state.get("ui_language") != "ru"
        ):
            result["errors"].append("native_task_identity_mismatch")
        gate = state["steps"][-1]
        if (
            gate.get("kind") != "gate-text"
            or gate.get("status")
            or gate.get("step_id") != data.get("step_id")
            or gate.get("generation") != data.get("generation")
        ):
            result["errors"].append("native_gate_mismatch")
        result["gate_result"] = {
            "kind": gate.get("kind"),
            "step_id": gate.get("step_id"),
            "generation": gate.get("generation"),
            "phase": state["current_phase"],
        }
        outputs = []
        for step in state["steps"]:
            for agent in step.get("agents") or []:
                if agent.get("slot") == "analyst" and agent.get("status") == "ok":
                    outputs.extend(agent.get("outputs") or [])
        actual = bool(outputs)
        for row in outputs:
            path = task_path / row["canonical_path"]
            if not path.resolve().is_relative_to(task_path) or not path.is_file():
                actual = False
                continue
            json.loads(path.read_text(encoding="utf-8"))
        result["dispatch_result"] = {
            "analyst_output_exists": actual,
            "canonical_outputs": [r["canonical_path"] for r in outputs],
        }
        if not actual:
            result["errors"].append("missing_analyst_output")
    except (OSError, ValueError, KeyError, IndexError) as exc:
        result["errors"].append(str(exc))
    return result


def verify_runtime(platform: str, work_dir: Path, timeout_seconds: int = 300, run_native: bool = True) -> dict:
    command = host_command(platform)  # Reject unknown platform before any subprocess.
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "platform": platform,
        "host_version": None,
        "installed_plugin": None,
        "loaded_skills": [],
        "task_path": None,
        "dispatch_result": None,
        "gate_result": None,
        "export_path": None,
        "errors": [],
    }
    try:
        version = subprocess.run(command + ["--version"], capture_output=True, text=True, timeout=20)
        result["host_version"] = version.stdout.strip()
        rows = run_json(
            command
            + (
                ["plugin", "list", "--marketplace", "memoforge-codex-local", "--json"]
                if platform == "codex"
                else ["plugins", "list", "--json"]
            )
        )
        installed = rows.get("installed", []) if isinstance(rows, dict) else rows
        match = next((r for r in installed if r.get("name") == "memoforge-" + platform and r.get("enabled")), None)
        if not match:
            raise ValueError("plugin_not_installed")
        result["installed_plugin"] = match.get("pluginId") or match.get("id")
        if platform == "zcode":
            inventory = run_json(command + ["skills", "list", "--json"])
            result["loaded_skills"] = [
                s.get("qualifiedName") for s in inventory["skills"] if s.get("pluginName") == "memoforge-zcode"
            ]
            root = Path(match["rootPath"])
        else:
            root = Path.home() / ".codex/plugins/cache/memoforge-codex-local/memoforge-codex" / match["version"]
        result["plugin_root"] = str(root)
        prompt = f"""Проверь установленный плагин memoforge-{platform}. Это разрешённая тестовая задача, работай в
{work_dir}. Не меняй другие проекты/настройки. Используй доступный плагин и его навыки. Прочитай все
пять SKILL.md из {root}/skills/{{memo,continue,status,style,brief}}, затем выполни memo с запросом:
«Нужна внутренняя юридическая записка на русском: участники учебного клуба хотят публиковать
фотографии; юрисдикция и возраст участников пока не известны. Факты учебные, внешнее исследование
сейчас не нужно». Выполняй реальный next→act→report до ПЕРВОГО текстового gate и остановись.
Подагенты для выданных слотов явно разрешены; используй нативный инструмент и полную системную роль.
Не используй fixture/dry-run и не отвечай за пользователя. Рабочие каталоги задач создавай внутри
{work_dir}/tasks (передай --output-folder). В конце запиши в {work_dir}/intake-evidence.json JSON с
loaded_skills (пять имён), task_path (абсолютный путь) и gate_kind/step_id/generation. Если native
инструмент недоступен или CLI отказал, честно запиши errors. Нельзя имитировать завершение.
Обязательно сохрани результат настоящего аналитика по пути из dispatch. Выведи короткий итог
по-русски."""
        if run_native:
            result["session"] = run_session(platform, work_dir, prompt, "intake", timeout_seconds)
            if result["session"].get("exit_code") != 0:
                result["errors"].append("native_session_failed: " + str(result["session"].get("error", "exit")))
        collected = collect_evidence(platform, work_dir)
        result["errors"].extend(collected.pop("errors"))
        result.update(collected)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result["errors"].append(str(exc))
    (work_dir / "runtime-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("codex", "zcode"))
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--evidence-only", action="store_true", help="Validate a completed Desktop session")
    args = parser.parse_args()
    result = verify_runtime(args.platform, args.work_dir, args.timeout, not args.evidence_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
