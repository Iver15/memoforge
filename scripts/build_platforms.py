#!/usr/bin/env python3
"""Build portable native plugins from the shared runtime, never from user caches."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("codex", "zcode")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".data", "outputs", "hooks.json")


def validate_package(root: Path) -> list[str]:
    errors = []
    root = root.resolve()
    try:
        metadata = json.loads((root / "memoforge-platform.json").read_text())
        platform = metadata["platform"]
        if platform not in PLATFORMS:
            raise ValueError("invalid_platform")
        manifest = json.loads((root / ("." + platform + "-plugin") / "plugin.json").read_text())
        if manifest["version"] != metadata["version"] or manifest["name"] != "memoforge-" + platform:
            errors.append("package_identity_mismatch")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ["invalid_package_metadata: " + str(exc)]
    for rel in (
        "schemas/state.schema.json",
        "scripts/memoforge/__main__.py",
        "scripts/mf",
        "scripts/mf.cmd",
        "requirements.txt",
        "LICENSE",
        "router.md",
        "lib/agent-core/tooling-core.md",
    ):
        if not (root / rel).is_file():
            errors.append("missing_resource: " + rel)
    for name in ("memo", "continue", "status", "style", "brief"):
        if not (root / "skills" / name / "SKILL.md").is_file():
            errors.append("missing_skill: " + name)
    mcp = root / ".mcp.json"
    try:
        if len(json.loads(mcp.read_text())["mcpServers"]) != 10:
            errors.append("missing_mcp_servers")
    except (OSError, ValueError, KeyError):
        errors.append("invalid_mcp_configuration")
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append("symlink_not_allowed: " + str(path.relative_to(root)))
        if any(part in (".git", ".data", "outputs", "__pycache__") for part in path.relative_to(root).parts):
            errors.append("private_resource: " + str(path.relative_to(root)))
        if path.suffix == ".md" and path.is_file() and not path.is_symlink():
            for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8-sig")):
                target = target.split("#", 1)[0].strip("<>")
                if not target or re.match(r"^[a-zA-Z]+:", target) or "${" in target:
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.is_relative_to(root) or not resolved.exists():
                    errors.append("invalid_reference: " + str(path.relative_to(root)) + " -> " + target)
    return errors


def build_package(platform: str, destination: Path) -> Path:
    if platform not in PLATFORMS:
        raise ValueError("invalid_platform: " + platform)
    destination = destination.resolve()
    root = destination / ("memoforge-" + platform)
    if root.exists():
        # Only replace a package previously created by this builder.
        if not (root / "memoforge-platform.json").is_file():
            raise ValueError("destination_not_owned: " + str(root))
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for folder in ("schemas", "lib", "templates", "agents", "hooks"):
        shutil.copytree(ROOT / folder, root / folder, ignore=IGNORED)
    shutil.copytree(ROOT / "scripts/memoforge", root / "scripts/memoforge", ignore=IGNORED)
    for rel in ("scripts/mf", "scripts/mf.cmd", "requirements.txt", "LICENSE"):
        shutil.copy2(ROOT / rel, root / rel)
    adapter = ROOT / "platforms" / platform
    shutil.copytree(adapter, root, dirs_exist_ok=True, ignore=IGNORED)
    manifest = json.loads((adapter / ("." + platform + "-plugin") / "plugin.json").read_text())
    version = manifest["version"]
    (root / "memoforge-platform.json").write_text(
        json.dumps({"platform": platform, "version": version}, indent=2) + "\n"
    )
    cmd = root / "scripts/mf.cmd"
    text = (
        cmd.read_bytes()
        .decode()
        .replace(
            "@echo off\r\n",
            '@echo off\r\nif defined MEMOFORGE_PLATFORM '
        f'if not "%MEMOFORGE_PLATFORM%"=="{platform}" exit /b 2\r\n'
        f'set "MEMOFORGE_PLATFORM={platform}"\r\n',
            1,
        )
    )
    cmd.write_bytes(text.encode())
    tooling = root / "lib/agent-core/tooling-core.md"
    tooling.write_text(tooling.read_text() + (adapter / "tooling-core.md").read_text())
    (root / "tooling-core.md").unlink()
    for path in (root / "agents").glob("*.md"):
        text = path.read_text()
        if text.startswith("---"):
            front, body = text.split("---", 2)[1:]
            front = "\n".join(
                line for line in front.splitlines() if not line.startswith(("model:", "effort:", "disallowedTools:"))
            )
            text = "---\n" + front.strip() + "\n---" + body
        path.write_text(text)
    if platform == "zcode":
        commands = root / "commands"
        commands.mkdir()
        for name in ("memo", "continue", "status", "style", "brief"):
            (commands / (name + ".md")).write_text(
                f"---\ndescription: Memoforge — {name}\n---\n\n"
                f"Прочитай `${{ZCODE_PLUGIN_ROOT}}/skills/{name}/SKILL.md` "
                "и выполни его с аргументами `$ARGUMENTS`.\n"
            )
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    if platform == "codex":
        for server in mcp["mcpServers"].values():
            server["type"] = "streamable-http"
    (root / ".mcp.json").write_text(json.dumps(mcp, indent=2) + "\n")
    errors = validate_package(root)
    if errors:
        raise ValueError("\n".join(errors))
    archive = destination / (root.name + "-" + version + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out.write(path, str(Path(root.name) / path.relative_to(root)))
    return root


def build_marketplace(platform: str, package: Path, destination: Path) -> Path:
    marketplace = destination / "marketplaces" / platform
    marketplace.mkdir(parents=True, exist_ok=True)
    target = marketplace / package.name
    if target.exists():
        if not (target / "memoforge-platform.json").exists():
            raise ValueError("destination_not_owned")
        shutil.rmtree(target)
    shutil.copytree(package, target)
    manifest = json.loads((package / ("." + platform + "-plugin") / "plugin.json").read_text())
    entry = {
        "name": package.name,
        "source": "./" + package.name,
        "version": manifest["version"],
        "description": manifest["description"],
    }
    if platform == "codex":
        entry["policy"] = {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
        entry["category"] = "Productivity"
        path = marketplace / ".agents/plugins/marketplace.json"
    else:
        entry["category"] = "productivity"
        path = marketplace / "marketplace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"name": package.name + "-local", "owner": {"name": "Memoforge"}, "plugins": [entry]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return marketplace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS + ("all",), default="all")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for platform in PLATFORMS if args.platform == "all" else (args.platform,):
        root = build_package(platform, args.out)
        build_marketplace(platform, root, args.out)
        print(root)


if __name__ == "__main__":
    main()
