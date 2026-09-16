"""Launcher discovery: plugin root, `plugin_data_dir` chain and `python_cmd` (ТЗ §2.5, §5.6)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import limits, schema, state_io

LAUNCHER_CACHE_FILENAME = "launcher.json"
SITE_PACKAGES_DIRNAME = "site-packages"

PYTHON_CANDIDATES: tuple[tuple[str, ...], ...] = (("python",), ("python3",), ("py", "-3"))
"""Discovery order of §5.6; a Windows Store alias is rejected by its exit code."""

VERSION_CHECK = (
    "import sys; sys.exit(0 if sys.version_info>="
    f"({limits.PYTHON_MIN_VERSION[0]},{limits.PYTHON_MIN_VERSION[1]}) else 1)"
)


def plugin_root() -> Path:
    """Repository root of the plugin (`<root>/scripts/memoforge/pylauncher.py` -> `<root>`)."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        candidate = Path(env)
        if (candidate / "scripts" / "memoforge").is_dir():
            return candidate.absolute()
    return Path(__file__).resolve().parents[2]


def home_dir() -> Path | None:
    """User home directory, or None when it cannot be determined (empty `$HOME`)."""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return None
    if not str(home) or not home.is_absolute():
        return None
    return home


def plugin_data_dir_candidates() -> list[dict]:
    """Chain of §2.5, in order; the same chain is implemented in `scripts/mf` and `scripts/mf.cmd`.

    `%LOCALAPPDATA%` is the Windows marker (it is never set on POSIX), which keeps the shell
    wrappers and this module byte-for-byte equivalent without a platform switch.
    """
    out: list[dict] = []
    env = (os.environ.get("CLAUDE_PLUGIN_DATA") or "").strip()
    if env:
        out.append({"source": "claude_plugin_data", "path": Path(env)})
    local_appdata = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        out.append(
            {
                "source": "localappdata",
                "path": Path(local_appdata) / "claude" / "plugin-data" / "memoforge",
            }
        )
    else:
        home = home_dir()
        if home is not None:
            out.append({"source": "home", "path": home / ".claude" / "plugin-data" / "memoforge"})
    out.append({"source": "plugin_root", "path": plugin_root() / ".data"})
    return out


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = path / ".mf-write-probe"
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        probe.unlink()
    except OSError:
        return False
    return True


def plugin_data_dir(create: bool = True) -> Path:
    """First writable candidate of the §2.5 chain; the last candidate is the unconditional fallback."""
    candidates = plugin_data_dir_candidates()
    for candidate in candidates:
        path = candidate["path"]
        if not create:
            return path
        if _is_writable(path):
            return path
    fallback = candidates[-1]["path"]
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback


def site_packages_dir() -> Path:
    """`<plugin_data_dir>/site-packages`, prepended to PYTHONPATH by the wrappers (§5.6)."""
    return plugin_data_dir() / SITE_PACKAGES_DIRNAME


def launcher_cache_path() -> Path:
    """`<plugin_data_dir>/launcher.json` — discovery cache shared with the wrappers (§5.6)."""
    return plugin_data_dir() / LAUNCHER_CACHE_FILENAME


def probe_interpreter(cmd: list[str] | tuple[str, ...]) -> bool:
    """True when `cmd` runs and reports Python >= limits.PYTHON_MIN_VERSION (§5.6)."""
    if shutil.which(cmd[0]) is None:
        return False
    try:
        completed = subprocess.run(
            list(cmd) + ["-c", VERSION_CHECK],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=limits.LAUNCHER_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _read_cache() -> dict | None:
    try:
        cached = state_io.read_json(launcher_cache_path())
    except (OSError, ValueError):
        return None
    return cached if isinstance(cached, dict) else None


def _write_cache(python_cmd_value: list[str]) -> None:
    payload = {
        "schema_version": 1,
        "kind": "launcher",
        "python_cmd": list(python_cmd_value),
        "checked_at": _utc_now(),
        "source": "pylauncher",
    }
    if schema.available():
        schema.validate_or_raise(payload, "internal")
    try:
        # One line, like the shell wrappers write it: they parse this file with sed/findstr (§5.6).
        state_io.write_bytes_atomic(
            launcher_cache_path(), (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
    except OSError:
        pass


def _utc_now() -> str:
    from . import events

    return events.utc_now()


def python_cmd(refresh: bool = False) -> list[str]:
    """Return the interpreter command (`['python']`, `['py','-3']`, …), caching it in launcher.json."""
    if not refresh:
        cached = _read_cache()
        if cached:
            value = cached.get("python_cmd")
            if (
                isinstance(value, list)
                and value
                and all(isinstance(part, str) and part for part in value)
                and shutil.which(value[0]) is not None
            ):
                return list(value)
    for candidate in PYTHON_CANDIDATES:
        if probe_interpreter(candidate):
            resolved = list(candidate)
            _write_cache(resolved)
            return resolved
    return [sys.executable]
