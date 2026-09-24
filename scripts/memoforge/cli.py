"""argparse front end: one JSON object per command on stdout, diagnostics on stderr (CONVENTIONS)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

from . import __version__

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

PROG = "mf"


def reconfigure_streams() -> None:
    """utf-8 stdout/stderr on every platform (M12)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - already-wrapped test streams
            pass


def group_subparsers(subparsers, name: str, help_text: str):
    """Return (creating once) the sub-subparsers of a command group, so several modules can share it."""
    registry = getattr(subparsers, "_mf_groups", None)
    if registry is None:
        registry = {}
        setattr(subparsers, "_mf_groups", registry)
    if name in registry:
        return registry[name]
    parser = subparsers.add_parser(name, help=help_text)
    group = parser.add_subparsers(dest=f"{name}_command", metavar="<command>", required=True)
    registry[name] = group
    return group


def build_parser() -> argparse.ArgumentParser:
    """Build the full CLI; every module exposes `register(subparsers)` (CONVENTIONS)."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="memoforge v2 pipeline CLI (deterministic state machine).",
    )
    parser.add_argument("--human", action="store_true", help="print text instead of JSON")
    parser.add_argument("--version", action="version", version=f"memoforge {__version__}")
    subparsers = parser.add_subparsers(dest="group", metavar="<group>", required=True)

    # One line per module; a slice adds its module to the tuple and nothing else (CONVENTIONS).
    from . import (
        analyze,
        brief,      # D-224
        citations,
        config_cmd,
        deps,
        dispatch,   # S2
        docs_render,
        docx,
        draft,
        events,
        finalize,
        gates,      # S2
        lint,
        machine,    # S2
        preflight,
        probe,      # S2
        quotes,
        render,
        review,
        revision,
        sources,
        state_cmd,
        style_profile,
        sufficiency,
        task,
    )

    for module in (
        task,
        config_cmd,
        events,
        deps,
        state_cmd,
        finalize,
        docx,
        analyze,
        style_profile,
        docs_render,
        review,
        revision,
        sufficiency,
        render,
        sources,   # S3
        preflight, # D-147: `sources preflight`, the same command group
        quotes,    # S3
        lint,      # S3
        citations, # S3
        draft,     # D-117: `draft finish` over the three of them
        machine,   # S2
        dispatch,  # S2
        gates,     # S2
        probe,     # S2
        brief,     # D-224: `mf brief next|report`, the driver of `/memoforge:brief`
    ):
        module.register(subparsers)
    return parser


def render_human(result: dict) -> str:
    """Flat text rendering of a result object for `--human`."""
    if isinstance(result.get("human"), str):
        return result["human"]
    lines = []
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def emit(result: dict, human: bool = False, payload: str | None = None) -> None:
    """Print exactly one JSON object (or the human rendering) to stdout."""
    if human:
        sys.stdout.write(render_human(result) + "\n")
    else:
        sys.stdout.write((json.dumps(result, ensure_ascii=False) if payload is None else payload) + "\n")
    sys.stdout.flush()


def command_labels(args: argparse.Namespace) -> tuple[str, str]:
    """`(group, cmd)` of the parsed invocation; `cmd` is empty for a group-less command (`next`)."""
    group = str(getattr(args, "group", "") or "")
    cmd = getattr(args, f"{group}_command", None) if group else None
    return group, str(cmd or "")


def call_work_dir(args: argparse.Namespace) -> str | None:
    """The `--workdir` of the invocation when it names a real task work dir, else None (D-43)."""
    from . import state_io

    value = getattr(args, "workdir", None)
    if not value:
        return None
    work_dir = str(value)
    if not os.path.isfile(os.path.join(work_dir, state_io.STATE_FILENAME)):
        return None
    return work_dir


REJECTION_MAX_CHARS = 200
"""How much of `errors[0]` reaches `cli_call.data.rejection` (§7.2 C: one journal line ≤4 KB)."""


def rejection_of(result: dict) -> str | None:
    """`errors[0]` of a refused answer — the reason the call returned 1 (D-118).

    Exit 1 alone cannot tell «the CLI broke» from «the command refused the content it was handed»
    (`identity_mismatch`, a schema-invalid agent payload, `input_sha_mismatch`, a rejected
    `sources register`). The first error line goes into the journal so `events analyze` can count
    those refusals instead of reading them as CLI failures; the exit code does not change.
    """
    errors = result.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    return " ".join(str(errors[0]).split())[:REJECTION_MAX_CHARS] or None


def log_cli_call(
    args: argparse.Namespace, exit_code: int, bytes_out: int, rejection: str | None = None
) -> None:
    """D-43 / §0.2 G2: exactly one `cli_call` per invocation with a known `--workdir`.

    No-ops and errors are logged too; `bytes_out` is the length of the JSON answer, which is what
    §0.2 G1 counts for `mf next`. Telemetry never fails the command it describes.
    """
    from . import events

    try:
        work_dir = call_work_dir(args)
        if work_dir is None:
            return
        group, cmd = command_labels(args)
        data = {
            "group": group,
            "cmd": cmd,
            "ok": exit_code == EXIT_OK,
            "exit_code": int(exit_code),
            "bytes_out": int(bytes_out),
        }
        if rejection:
            data["rejection"] = rejection
        events.append_event(
            work_dir,
            "cli_call",
            "cli",
            data,
            step_id=str(getattr(args, "step", "") or "") or None,
        )
    except BaseException as exc:  # noqa: BLE001 - the journal is best effort (§7.2 C′)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"cli_call not logged: {type(exc).__name__}: {exc}", file=sys.stderr)


def error_result(exc: BaseException) -> dict:
    """Map an exception onto the `{"errors": [...]}` contract (exit code 1)."""
    from . import schema, state_io

    if isinstance(exc, schema.SchemaValidationError):
        return {"errors": [f"schema_invalid[{exc.schema_name}]"] + exc.errors}
    if isinstance(exc, schema.DependencyMissing):
        return {"errors": [str(exc)], "hint": schema.DependencyMissing.hint}
    if isinstance(exc, state_io.LockOrderViolation):
        return {"errors": [str(exc)]}
    if isinstance(exc, state_io.LockTimeout):
        return {"errors": [str(exc)]}
    if isinstance(exc, (ValueError, OSError)):
        return {"errors": [f"{type(exc).__name__}: {exc}"]}
    return {"errors": [f"internal_error: {type(exc).__name__}: {exc}"]}


def main(argv: list[str] | None = None) -> int:
    """Entry point used by `__main__.py`; returns the process exit code."""
    reconfigure_streams()
    raw = list(sys.argv[1:] if argv is None else argv)
    human = "--human" in raw
    raw = [arg for arg in raw if arg != "--human"]

    parser = build_parser()
    args = parser.parse_args(raw)
    args.human = human

    func = getattr(args, "func", None)
    if func is None:  # pragma: no cover - argparse enforces `required=True`
        parser.error("no command given")
        return EXIT_USAGE

    try:
        result = func(args)
    except BaseException as exc:  # noqa: BLE001 - the CLI must never leak a traceback to stdout
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        traceback.print_exc(file=sys.stderr)
        result = error_result(exc)

    if not isinstance(result, dict):
        result = {"errors": [f"internal_error: command returned {type(result).__name__}"]}
    payload = json.dumps(result, ensure_ascii=False)
    exit_code = EXIT_ERROR if result.get("errors") else EXIT_OK
    log_cli_call(args, exit_code, len(payload.encode("utf-8")), rejection_of(result))
    emit(result, human=human, payload=payload)
    return exit_code
