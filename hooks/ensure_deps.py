#!/usr/bin/env python
"""SessionStart hook (ТЗ §8.1): record the optional dependencies and mirror the plugin options.

It **checks and records, it never installs.** `pip` inside a hook would block the session start,
need the network and fail on a read-only or externally managed environment; installation stays an
explicit `mf deps install`. The result is a cache the CLI and the README can read:

    <plugin_data_dir>/deps.json      {schema_version, kind: "deps", checked_at, python_version, deps}
    <plugin_data_dir>/options.json   {"dashboard": "true", …} — the ten `userConfig` options (§8.4)

The second file exists because a host exports `CLAUDE_PLUGIN_OPTION_*` to **hook** processes only
(§8.4): a `Bash` command the orchestrator runs (`mf task new`) may see none of them, so the hook
mirrors whatever it was given and the CLI reads it back (D-91).

Shape of `deps.json` follows `schemas/internal.schema.json`. Missing dependencies are not an error:
`mf` degrades (no `jsonschema` -> `finalize --salvage`; no `python-docx`/`mistune` -> markdown
deliverable), so the hook always exits 0 and prints `{}`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

DEPS_FILENAME = "deps.json"
OPTIONS_FILENAME = "options.json"

OPTION_KEYS: tuple[str, ...] = (
    "output_folder",
    "publish_folder",
    "writer_model",
    "source_review_gate",
    "citation_style",
    "memo_language",
    "ui_language",
    "dashboard",
    "stop_guard",
    "websearch_autoallow",
)
"""The ten `userConfig` options declared in `.claude-plugin/plugin.json` (ТЗ §8.4, D-109, D-152, D-169)."""

MODULES: tuple[tuple[str, str], ...] = (
    ("jsonschema", "jsonschema"),
    ("docx", "python-docx"),
    ("mistune", "mistune"),
)
"""`(import name, distribution name)` of the three dependencies of §5.6."""


def _version(distribution: str) -> str | None:
    """Installed version without importing the package; None when it cannot be determined."""
    try:
        from importlib import metadata
    except ImportError:
        return None
    try:
        return metadata.version(distribution)
    except Exception:  # noqa: BLE001 — metadata is best effort
        return None


def check_modules(extra_path: str | None = None) -> dict:
    """`{module: {installed, version}}` for the three optional dependencies (import check only)."""
    if extra_path and extra_path not in sys.path:
        sys.path.insert(0, extra_path)
    report = {}
    for module, distribution in MODULES:
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            installed = False
        report[module] = {
            "installed": installed,
            "version": _version(distribution) if installed else None,
        }
    return report


def build_report(extra_path: str | None = None) -> dict:
    """The `deps.json` document (`schemas/internal.schema.json`, kind `deps`)."""
    from memoforge import events

    deps = check_modules(extra_path)
    return {
        "schema_version": 1,
        "kind": "deps",
        "checked_at": events.utc_now(),
        "source": "ensure_deps",
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "deps": deps,
        "missing": sorted(name for name, row in deps.items() if not row["installed"]),
    }


# --- userConfig mirror (§8.4, D-91) ---------------------------------------


def option_env_name(key: str) -> str:
    """`CLAUDE_PLUGIN_OPTION_<KEY>` — the variable a host exports for a `userConfig` option."""
    return "CLAUDE_PLUGIN_OPTION_" + key.upper()


def collect_options(environ: dict | None = None) -> dict:
    """Present, non-empty option variables as raw strings — nothing is parsed or defaulted here."""
    environ = os.environ if environ is None else environ
    options = {}
    for key in OPTION_KEYS:
        value = (environ.get(option_env_name(key)) or "").strip()
        if value:
            options[key] = value
    return options


def _home_dir() -> Path | None:
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return None
    if not str(home) or not home.is_absolute():
        return None
    return home


def _default_chain() -> list:
    """The `plugin_data_dir` chain of §2.5 without `$CLAUDE_PLUGIN_DATA` — its default half."""
    chain = []
    local_appdata = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        chain.append(Path(local_appdata) / "claude" / "plugin-data" / "memoforge")
    else:
        home = _home_dir()
        if home is not None:
            chain.append(home / ".claude" / "plugin-data" / "memoforge")
    chain.append(PLUGIN_ROOT / ".data")
    return chain


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


def _first_writable(chain: list) -> Path | None:
    for path in chain:
        if _is_writable(path):
            return path
    return None


def plugin_data_dirs() -> list:
    """Where the mirror goes: the same chain as `scripts/memoforge/pylauncher.py:plugin_data_dir`.

    A hook is stdlib-only and must not import the package (CONVENTIONS), so the short chain is
    duplicated here — keep it in step with `pylauncher.plugin_data_dir_candidates`. Two targets,
    not one: the dir the host handed us through `$CLAUDE_PLUGIN_DATA` **and** the one the CLI
    resolves in a shell that never saw that variable, so `mf` finds the mirror either way.
    """
    default_chain = _default_chain()
    env = (os.environ.get("CLAUDE_PLUGIN_DATA") or "").strip()
    chains = [[Path(env)] + default_chain, default_chain] if env else [default_chain]
    targets: list = []
    for chain in chains:
        directory = _first_writable(chain)
        if directory is not None and directory not in targets:
            targets.append(directory)
    return targets


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    """tmp + `os.replace` in the target directory (stdlib twin of `state_io.write_bytes_atomic`)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def mirror_options(environ: dict | None = None) -> list:
    """Write `options.json` into every `plugin_data_dir`; `[]` when the host exported no option.

    No option present is not «unset everything»: a file written by `mf config set` must survive a
    session start in a host with no options UI at all (D-91), so this never deletes or empties it.
    """
    options = collect_options(environ)
    if not options:
        return []
    payload = (json.dumps(options, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    written = []
    for directory in plugin_data_dirs():
        path = directory / OPTIONS_FILENAME
        try:
            _write_bytes_atomic(path, payload)
        except OSError:
            continue
        written.append(str(path))
    return written


def run(target: str | None = None) -> dict | None:
    """Write the report next to the launcher cache; None when nothing could be written."""
    from memoforge import pylauncher, state_io

    site_packages = None
    try:
        site_packages = str(pylauncher.site_packages_dir())
    except Exception:  # noqa: BLE001 — the chain is best effort inside a hook
        site_packages = None
    report = build_report(site_packages)
    path = Path(target) if target else pylauncher.plugin_data_dir() / DEPS_FILENAME
    state_io.write_bytes_atomic(path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return report


# --- entry point ----------------------------------------------------------


def main(argv: list | None = None) -> str:
    """Return the JSON the hook prints (always `{}`); never raises, so the caller exits 0."""
    try:
        parser = argparse.ArgumentParser(description="memoforge SessionStart dependency check")
        parser.add_argument("--out", default=None, help="write the report here instead of plugin_data_dir")
        args = parser.parse_args(argv)
        try:
            mirror_options()
        except BaseException:  # noqa: BLE001 — the mirror never costs us the deps report (§8.1)
            pass
        run(args.out)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 — a hook must never fail loudly (§8.1)
        pass
    return "{}"


def _utf8_stdout() -> None:
    """CONVENTIONS: a hook may print a path or a host that the console codepage cannot encode."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


if __name__ == "__main__":
    _utf8_stdout()
    try:
        sys.stdout.write(main())
    except SystemExit:
        sys.stdout.write("{}")
    sys.exit(0)
