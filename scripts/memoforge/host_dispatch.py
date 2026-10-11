"""Convert a shared dispatch to a native host call without changing its slot contract."""

from __future__ import annotations

from pathlib import Path
from .host_profile import HostProfile


def adapt_agent(payload: dict, profile: HostProfile, root: Path) -> dict:
    if profile.name == "claude":
        return payload
    result = dict(payload)
    agent = str(payload["agent"])
    if Path(agent).name != agent:
        raise ValueError("invalid_agent_name")
    text = (root / "agents" / (agent + ".md")).read_text(encoding="utf-8-sig")
    role = text.split("---", 2)[2].strip() if text.startswith("---") else text
    tooling = root / "lib" / "agent-core" / "tooling-core.md"
    result["prompt"] = (
        f"# Системная роль: {agent}\n\n{role}\n\n# Правила инструментов\nПрочитай {tooling}. "
        "Не создавай вложенных агентов и не задавай вопросы пользователю.\n\n"
        f"# Текущее задание\n{payload['prompt']}"
    )
    result.pop("model", None)
    result["subagent_type"] = profile.plugin_name + ":" + agent if profile.name == "zcode" else "default"
    result["runtime_platform"] = profile.name
    return result
