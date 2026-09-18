"""`mf config show|set|unset` — read and write `<plugin_data_dir>/options.json` (ТЗ §8.4, D-91)."""

from __future__ import annotations

import argparse

from . import state_io, task

UNSET = "(unset)"
"""Printed for an option with no value anywhere — `output_folder` resolves through the §2.5 chain."""


def _human(resolved: dict, path) -> str:
    """One line per option: value and the level of the chain it came from."""
    width = max(len(key) for key in task.OPTION_KEYS)
    lines = [
        f"{key.ljust(width)}  {resolved['values'].get(key, UNSET)}  [{resolved['sources'][key]}]"
        for key in task.OPTION_KEYS
    ]
    lines.append("")
    lines.append(f"file: {path}")
    lines.append("set a value with: mf config set <key> <value>")
    return "\n".join(lines)


def _answer(path, extra: dict | None = None) -> dict:
    resolved = task.resolve_options()
    result = {
        "options": resolved["values"],
        "options_source": resolved["sources"],
        "options_path": str(path),
        "options_file": task.read_options_file(path),
    }
    result.update(extra or {})
    result["human"] = _human(resolved, path)
    return result


def run_show(args: argparse.Namespace) -> dict:
    """`mf config show` — effective value and source of each of the ten options (§8.4)."""
    return _answer(task.options_path())


def run_set(args: argparse.Namespace) -> dict:
    """`mf config set <key> <value>` — write one option into `options.json` (validated, §8.4)."""
    key = str(args.key).strip().lower()
    value = str(args.value).strip()
    error = task.validate_option(key, value)
    if error:
        return {"errors": [error], "known_options": list(task.OPTION_KEYS)}
    path = task.options_path()
    document = task.read_options_file(path)
    document[key] = value
    state_io.write_json_atomic(path, document)
    return _answer(path, {"set": {key: value}})


def run_unset(args: argparse.Namespace) -> dict:
    """`mf config unset <key>` — drop one option; the chain then falls back to env or default."""
    key = str(args.key).strip().lower()
    if key not in task.OPTION_KEYS:
        return {"errors": [f"unknown_option: {key}"], "known_options": list(task.OPTION_KEYS)}
    path = task.options_path()
    document = task.read_options_file(path)
    removed = document.pop(key, None) is not None
    if removed:
        state_io.write_json_atomic(path, document)
    return _answer(path, {"unset": key, "removed": removed})


def register(subparsers) -> None:
    """Register the `config` command group."""
    from . import cli

    group = cli.group_subparsers(subparsers, "config", "plugin options (userConfig mirror)")

    show = group.add_parser("show", help="effective options and where each value came from")
    show.set_defaults(func=run_show)

    known = ", ".join(task.OPTION_KEYS)
    setter = group.add_parser("set", help="write one option into options.json")
    setter.add_argument("key", help=known)
    setter.add_argument("value")
    setter.set_defaults(func=run_set)

    unset = group.add_parser("unset", help="remove one option from options.json")
    unset.add_argument("key", help=known)
    unset.set_defaults(func=run_unset)
