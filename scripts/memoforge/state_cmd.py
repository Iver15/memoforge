"""`mf state get|validate` — read-only access to `state.json` (ТЗ §5.2, M2).

Nothing here writes: `state_io.write_state` stays the single writer (M2). `state get --path a.b.0`
walks objects by key and arrays by index so the router and the hooks can ask one question instead of
Reading the whole document (G1).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import schema, state_io

MISSING = object()


def get_path(state: object, path: str | None) -> object:
    """Walk a dotted path (`config.reviewer_list.0`); returns `MISSING` when a segment is absent."""
    if not path:
        return state
    current = state
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return MISSING
            current = current[segment]
        elif isinstance(current, list):
            try:
                index = int(segment)
            except ValueError:
                return MISSING
            if not -len(current) <= index < len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def run_get(args: argparse.Namespace) -> dict:
    """`mf state get [--path a.b]`."""
    work_dir = Path(args.workdir)
    try:
        state = state_io.read_state(work_dir)
    except FileNotFoundError:
        return {"errors": [f"state_not_found: {state_io.state_path(work_dir)}"]}
    except (OSError, ValueError) as exc:
        return {"errors": [f"state_unreadable: {exc}"]}

    value = get_path(state, args.path)
    if value is MISSING:
        return {"errors": [f"path_not_found: {args.path}"], "path": args.path}
    return {"path": args.path, "value": value}


def run_validate(args: argparse.Namespace) -> dict:
    """`mf state validate` — check `state.json` against `schemas/state.schema.json` (M2)."""
    work_dir = Path(args.workdir)
    path = state_io.state_path(work_dir)
    try:
        state = state_io.read_state(work_dir)
    except FileNotFoundError:
        return {"errors": [f"state_not_found: {path}"], "valid": False}
    except (OSError, ValueError) as exc:
        return {"errors": [f"state_unparseable: {exc}"], "valid": False, "state_path": str(path)}

    try:
        errors = schema.validate(state, "state")
    except schema.DependencyMissing as exc:
        return {"errors": [str(exc)], "hint": schema.DependencyMissing.hint, "valid": None}
    if errors:
        return {"valid": False, "errors": errors, "state_path": str(path)}
    return {
        "valid": True,
        "state_path": str(path),
        "schema_version": state.get("schema_version"),
        "task_id": state.get("task_id"),
        "current_phase": state.get("current_phase"),
    }


def register(subparsers) -> None:
    """Register `mf state get` and `mf state validate`."""
    from . import cli

    group = cli.group_subparsers(subparsers, "state", "read-only access to state.json")

    get = group.add_parser("get", help="print state.json or one dotted path of it")
    get.add_argument("--workdir", required=True)
    get.add_argument("--path", default=None, help="dotted path, e.g. `config.reviewer_list.0`")
    get.set_defaults(func=run_get)

    validate = group.add_parser("validate", help="validate state.json against its schema")
    validate.add_argument("--workdir", required=True)
    validate.set_defaults(func=run_validate)
