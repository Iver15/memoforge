"""`mf deps check|install` — the dependency contract of ТЗ §5.6 (D-33).

`check` never installs: it reports which of `requirements.txt` is importable, at which version, and
writes the same `<plugin_data_dir>/deps.json` the `ensure_deps` SessionStart hook writes. `install`
is the only place that runs pip, and only on the explicit command:

    pip install --target <plugin_data_dir>/site-packages -r requirements.txt

Both commands work without `jsonschema` — they are the bootstrap of §5.6 and must run before any
dependency exists.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

from . import events, pylauncher, state_io

DEPS_FILENAME = "deps.json"
REQUIREMENTS_FILENAME = "requirements.txt"

MODULES: tuple[tuple[str, str], ...] = (
    ("jsonschema", "jsonschema"),
    ("docx", "python-docx"),
    ("mistune", "mistune"),
)
"""`(import name, distribution name)` of the three dependencies of §5.6, in `requirements.txt` order."""

DISTRIBUTION_BY_MODULE: dict[str, str] = {module: dist for module, dist in MODULES}
MODULE_BY_DISTRIBUTION: dict[str, str] = {dist: module for module, dist in MODULES}

_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(.*)$")
_VERSION_PART = re.compile(r"\d+")


# --- requirements.txt ------------------------------------------------------


def requirements_path() -> Path:
    """`<plugin_root>/requirements.txt` — the single source of the version floors (§5.6)."""
    return pylauncher.plugin_root() / REQUIREMENTS_FILENAME


def read_requirements(path: str | Path | None = None) -> dict:
    """`{distribution: specifier}` of the uncommented lines of `requirements.txt`."""
    target = Path(path) if path else requirements_path()
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQUIREMENT.match(line)
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def _version_tuple(value: str) -> tuple:
    return tuple(int(part) for part in _VERSION_PART.findall(value)[:4])


def satisfies(version: str | None, specifier: str) -> bool | None:
    """Whether `version` meets `specifier`; None when the pair cannot be compared (§5.6 uses `>=`)."""
    if not version:
        return False
    specifier = (specifier or "").strip()
    if not specifier:
        return True
    match = re.match(r"^(>=|==|>|<=|<)\s*([0-9][0-9A-Za-z.*+-]*)$", specifier)
    if not match:
        return None
    operator, wanted = match.group(1), match.group(2)
    left, right = _version_tuple(version), _version_tuple(wanted)
    if not left or not right:
        return None
    size = max(len(left), len(right))
    left = left + (0,) * (size - len(left))
    right = right + (0,) * (size - len(right))
    return {
        ">=": left >= right,
        "==": left == right,
        ">": left > right,
        "<=": left <= right,
        "<": left < right,
    }[operator]


# --- import check ----------------------------------------------------------


def installed_version(distribution: str) -> str | None:
    """Installed version without importing the package; None when metadata does not know it."""
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return None
    try:
        return metadata.version(distribution)
    except Exception:  # noqa: BLE001 - metadata is best effort (§5.6)
        return None


def check_modules(extra_path: str | None = None) -> dict:
    """`{module: {installed, version, required, satisfied, distribution}}` — import check only."""
    if extra_path and extra_path not in sys.path:
        sys.path.insert(0, extra_path)
    required = read_requirements()
    report: dict[str, dict] = {}
    for module, distribution in MODULES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        version = installed_version(distribution) if found else None
        specifier = required.get(distribution, "")
        report[module] = {
            "installed": found,
            "version": version,
            "distribution": distribution,
            "required": specifier,
            "satisfied": satisfies(version, specifier) if found else False,
        }
    return report


def build_report(extra_path: str | None = None) -> dict:
    """The `deps.json` document (`schemas/internal.schema.json`, kind `deps`)."""
    deps = check_modules(extra_path)
    return {
        "schema_version": 1,
        "kind": "deps",
        "checked_at": events.utc_now(),
        "source": "deps check",
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "deps": deps,
        "missing": sorted(name for name, row in deps.items() if not row["installed"]),
        "outdated": sorted(
            name for name, row in deps.items() if row["installed"] and row["satisfied"] is False
        ),
    }


def deps_path() -> Path:
    """`<plugin_data_dir>/deps.json` — the cache shared with the `ensure_deps` hook (§8.1)."""
    return pylauncher.plugin_data_dir() / DEPS_FILENAME


def write_report(report: dict, target: str | Path | None = None) -> Path:
    """Write `deps.json` atomically and return its path."""
    path = Path(target) if target else deps_path()
    state_io.write_bytes_atomic(
        path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return path


# --- install ---------------------------------------------------------------


def install_command(site_packages: Path, requirements: Path) -> list[str]:
    """`pip install --target <plugin_data_dir>/site-packages -r requirements.txt` (D-33)."""
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(site_packages),
        "-r",
        str(requirements),
    ]


# --- commands --------------------------------------------------------------


def _human(report: dict, path: Path) -> str:
    lines = [f"python {report['python_version']}  ({report['python_executable']})"]
    for module, row in report["deps"].items():
        state = "missing" if not row["installed"] else (row["version"] or "installed")
        mark = "!" if not row["installed"] or row["satisfied"] is False else " "
        lines.append(f" {mark} {module:<12} {state:<12} required {row['required'] or 'any'}")
    lines.append(f"report: {path}")
    if report["missing"]:
        lines.append("run `mf deps install` to install the missing packages")
    return "\n".join(lines)


def run_check(args: argparse.Namespace) -> dict:
    """`mf deps check` — import + version report, also written to `deps.json` (§5.6, D-33)."""
    site_packages = None
    try:
        site_packages = str(pylauncher.site_packages_dir())
    except OSError:  # pragma: no cover - the chain always yields a path
        site_packages = None
    report = build_report(site_packages)
    written: str | None = None
    try:
        written = str(write_report(report, getattr(args, "out", None)))
    except OSError as exc:
        report["write_error"] = f"{type(exc).__name__}: {exc}"
    result = {
        "ok": not report["missing"] and not report["outdated"],
        "python_version": report["python_version"],
        "deps": report["deps"],
        "missing": report["missing"],
        "outdated": report["outdated"],
        "deps_json": written,
        "site_packages": site_packages,
    }
    result["human"] = _human(report, Path(written or "-"))
    return result


def run_install(args: argparse.Namespace) -> dict:
    """`mf deps install` — the only pip call of the package; never implicit (§5.6, D-33)."""
    requirements = Path(args.requirements) if args.requirements else requirements_path()
    if not requirements.is_file():
        return {"errors": [f"requirements_not_found: {requirements}"]}
    site_packages = pylauncher.site_packages_dir()
    site_packages.mkdir(parents=True, exist_ok=True)
    command = install_command(site_packages, requirements)
    if args.dry_run:
        return {"installed": False, "dry_run": True, "command": command, "target": str(site_packages)}
    try:
        completed = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        return {"errors": [f"pip_not_runnable: {type(exc).__name__}: {exc}"], "command": command}
    result = {
        "installed": completed.returncode == 0,
        "command": command,
        "target": str(site_packages),
        "returncode": completed.returncode,
        "stdout_tail": (completed.stdout or "").strip()[-2000:],
        "stderr_tail": (completed.stderr or "").strip()[-2000:],
    }
    if completed.returncode != 0:
        result["errors"] = [f"pip_failed: exit {completed.returncode}"]
        return result
    result["check"] = run_check(argparse.Namespace(out=getattr(args, "out", None)))
    return result


def register(subparsers) -> None:
    """Register `mf deps check|install` (§5.2, D-33)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "deps", "runtime dependencies (§5.6)")

    checker = group.add_parser("check", help="report importability and versions; writes deps.json")
    checker.add_argument("--out", default=None, help="write the report here instead of plugin_data_dir")
    checker.set_defaults(func=run_check)

    installer = group.add_parser("install", help="pip install --target <plugin_data_dir>/site-packages")
    installer.add_argument("--requirements", default=None, help="requirements file to install")
    installer.add_argument("--out", default=None, help="write the follow-up report here")
    installer.add_argument("--dry-run", dest="dry_run", action="store_true", help="print the command only")
    installer.set_defaults(func=run_install)
