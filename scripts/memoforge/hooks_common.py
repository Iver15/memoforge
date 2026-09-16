"""Stdlib-only helpers shared by hooks and the status line: find the active task (ТЗ §8.1, §8.3).

Hooks never write `state.json` (M2), so this module must not import `state_io`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import phases

STATE_FILENAME = "state.json"
EVENTS_FILENAME = "events.jsonl"
ANCESTOR_DEPTH = 4
OUTPUT_FOLDER_OPTION_ENV = "CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER"
PROJECT_FOLDER_SUBDIR = "memoforge"
"""`<session folder>/memoforge` — the third link of the §2.5 chain (D-104)."""

PLUGIN_MARKER = (".claude-plugin", "plugin.json")
"""A directory holding this file is a plugin root, never a session working folder (D-104)."""

PLACEHOLDER = re.compile(r"\$\{[^}]*\}")
"""A host that failed to expand `${…}` hands us the literal text; that is never a value (D-91)."""


def is_placeholder(value: object) -> bool:
    """True for an unexpanded `${…}` placeholder, ignored at every level of the chain (D-91).

    D-93: `task.py` imports this predicate instead of defining its own — the option chain and the
    work-dir chain must reject the same values, or one chain hands back what the other skipped.
    """
    return bool(PLACEHOLDER.search(str(value)))


def project_dir(cwd: str | os.PathLike | None = None) -> Path:
    """`$CLAUDE_PROJECT_DIR` when set, otherwise the given cwd (default: process cwd)."""
    env = (os.environ.get("CLAUDE_PROJECT_DIR") or "").strip()
    if env:
        return Path(env)
    return Path(cwd) if cwd is not None else Path.cwd()


def home_dir() -> Path | None:
    """User home directory, or None when it cannot be determined (empty `$HOME`)."""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return None
    if not str(home) or not home.is_absolute():
        return None
    return home


def _user_config_folder(explicit: str | os.PathLike | None) -> str:
    """First real `user_config` value: the caller's, else `$CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER`.

    D-93: a placeholder is skipped at both levels, so a `${…}` the option chain already refused
    cannot come back here and become a literal directory.
    """
    for raw in (explicit, os.environ.get(OUTPUT_FOLDER_OPTION_ENV)):
        text = "" if raw is None else str(raw).strip()
        if text and not is_placeholder(text):
            return text
    return ""


def _plugin_roots() -> list[Path]:
    """Plugin roots known without importing `pylauncher` (it pulls in `state_io`, banned here, M2).

    Same two answers `pylauncher.plugin_root()` gives: `$CLAUDE_PLUGIN_ROOT` and the package root
    (`<root>/scripts/memoforge/hooks_common.py` -> `<root>`).
    """
    roots = [Path(__file__).resolve().parents[2]]
    env = (os.environ.get("CLAUDE_PLUGIN_ROOT") or "").strip()
    if env and not is_placeholder(env):
        roots.append(Path(env))
    return roots


def _same_dir(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.absolute())) == os.path.normcase(str(right.absolute()))
    except OSError:
        return False


def is_project_folder(path: str | os.PathLike) -> bool:
    """True when `path` is a real session working folder, so `<path>/memoforge` may hold tasks.

    D-104: the plugin root, the home directory itself and a filesystem root are not — a task folder
    created there lands among the plugin's own files, loose in `$HOME`, or at a drive root.
    """
    folder = Path(path)
    if is_placeholder(str(folder)):
        return False
    try:
        folder = folder.absolute()
    except OSError:
        return False
    if folder.parent == folder:
        return False
    home = home_dir()
    if home is not None and _same_dir(folder, home):
        return False
    try:
        if folder.joinpath(*PLUGIN_MARKER).is_file():
            return False
    except OSError:
        return False
    return not any(_same_dir(folder, root) for root in _plugin_roots())


def output_folder_candidates(
    explicit: str | os.PathLike | None = None,
    cwd: str | os.PathLike | None = None,
) -> list[dict]:
    """Work-dir resolution chain of §2.5, in order, as `{source, path}` rows."""
    base = Path(cwd) if cwd is not None else Path.cwd()
    out: list[dict] = []
    option = _user_config_folder(explicit)
    if option:
        out.append({"source": "user_config", "path": Path(option)})
    env = (os.environ.get("MEMOFORGE_OUTPUT_FOLDER") or "").strip()
    if env:
        out.append({"source": "env", "path": Path(env)})
    # D-104: the folder the user attached to the session (`$CLAUDE_PROJECT_DIR`, else the cwd) comes
    # before `~/Documents`, or inside a host VM the deliverable lands where the user never looks.
    session = project_dir(cwd)
    if is_project_folder(session):
        out.append(
            {"source": "project_folder", "path": session.absolute() / PROJECT_FOLDER_SUBDIR}
        )
    home = home_dir()
    if home is not None:
        out.append({"source": "home_documents", "path": home / "Documents" / "memoforge"})
    out.append({"source": "cwd_outputs", "path": base / "outputs" / "memoforge-work"})
    return out


def read_json_quiet(path: str | os.PathLike) -> dict | None:
    """Read a JSON object as utf-8-sig, returning None on any error (hook safety, §8.1)."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        value = json.loads(text)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_state_quiet(work_dir: str | os.PathLike) -> dict | None:
    """Read `<work_dir>/state.json` without raising."""
    return read_json_quiet(Path(work_dir) / STATE_FILENAME)


def events_path(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/events.jsonl` (§2.2: the journal has no configurable path)."""
    return Path(work_dir) / EVENTS_FILENAME


def is_v2(state: dict | None) -> bool:
    """True when the state is a v2 document (`schema_version == 2`)."""
    return bool(state) and state.get("schema_version") == 2


def is_active(state: dict | None) -> bool:
    """True for a v2 task in a non-terminal phase (§8.1 «нет активной задачи -> {}»)."""
    if not is_v2(state):
        return False
    phase = state.get("current_phase")
    return isinstance(phase, str) and phase in phases.PHASE_INDEX and not phases.is_terminal(phase)


def task_dirs(roots: list[Path] | None = None, cwd: str | os.PathLike | None = None) -> list[Path]:
    """Work directories found in the resolution chain (a root itself or its immediate children)."""
    if roots is None:
        roots = [row["path"] for row in output_folder_candidates(cwd=cwd)]
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            candidates = [root] if (root / STATE_FILENAME).is_file() else sorted(root.iterdir())
            for candidate in candidates:
                if not candidate.is_dir() and candidate != root:
                    continue
                if not (candidate / STATE_FILENAME).is_file():
                    continue
                key = str(candidate.absolute()).lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(candidate)
        except OSError:
            continue
    return found


def _sort_key(work_dir: Path) -> tuple:
    state = read_state_quiet(work_dir) or {}
    created = state.get("created_at")
    if not isinstance(created, str):
        created = ""
    try:
        mtime = (work_dir / STATE_FILENAME).stat().st_mtime
    except OSError:
        mtime = 0.0
    return (created, mtime)


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child_abs = child.absolute()
        parent_abs = parent.absolute()
    except OSError:
        return False
    return child_abs == parent_abs or parent_abs in child_abs.parents


def find_active_task(cwd: str | os.PathLike | None = None) -> Path | None:
    """Work dir of the active v2 task for this session, or None (§8.3).

    The current directory (or `$CLAUDE_PROJECT_DIR`) wins; with several active tasks and no match
    the hook must stay silent, so None is returned.
    """
    base = project_dir(cwd)
    probe = base
    for _ in range(ANCESTOR_DEPTH + 1):
        if is_active(read_state_quiet(probe)):
            return probe
        if probe.parent == probe:
            break
        probe = probe.parent

    active = [work_dir for work_dir in task_dirs(cwd=cwd) if is_active(read_state_quiet(work_dir))]
    if not active:
        return None
    under_base = [work_dir for work_dir in active if _is_under(work_dir, base)]
    if under_base:
        return max(under_base, key=_sort_key)
    if len(active) == 1:
        return active[0]
    return None
