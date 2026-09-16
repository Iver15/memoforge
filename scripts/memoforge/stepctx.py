"""Step identity, canonical publication and step closing for `--step` CLI commands (ТЗ §2.2, §3.1).

Minimal shared helper for the script-steps of slice S3 (`sources pack --freeze`, `draft anchor`,
`draft lint`, `draft audit-citations`). Slice S2 (`machine.py`) extends it; the contract kept here
is the one §3.1 states for every `--step`-command: identity `(step_id, attempt)`, a no-op repeat
returning `steps[].result_ref`, and one state write that both publishes and closes the step.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import events, state_io

CLI_SLOT = "cli"
"""Slot of every CLI-executed step (CONVENTIONS: agents use their own slot)."""

IDENTITY_MISMATCH = "identity_mismatch"
OUTPUT_MODIFIED = "output_modified_after_publish"

STATUS_OK = "ok"
STATUS_CLOSED = "closed"
STATUS_MISMATCH = "mismatch"


class IdentityMismatch(Exception):
    """The identity re-checked under `state.lock` is no longer the issued one (§3.1, D-40)."""

    def __init__(self, step_id: str, attempt: int, reason: str, current_attempt: int | None = None) -> None:
        super().__init__(f"{IDENTITY_MISMATCH}: {step_id}/a{attempt} ({reason})")
        self.step_id = step_id
        self.attempt = int(attempt)
        self.reason = reason
        self.current_attempt = current_attempt

    def as_result(self) -> dict:
        """The JSON answer a command returns when its identity was lost mid-flight."""
        result = {
            "errors": [IDENTITY_MISMATCH],
            "reason": self.reason,
            "step_id": self.step_id,
            "attempt": self.attempt,
        }
        if self.current_attempt is not None:
            result["current_attempt"] = self.current_attempt
        return result


class OutputModifiedAfterPublish(Exception):
    """A canonical file no longer matches the sha recorded in `published[]` (§2.2, D-41)."""

    def __init__(self, canonical_path: str) -> None:
        super().__init__(canonical_path)
        self.canonical_path = canonical_path


# --- paths ----------------------------------------------------------------


def rel_path(work_dir: str | os.PathLike, path: str | os.PathLike) -> str:
    """POSIX path relative to `work_dir` (CONVENTIONS: state paths are relative POSIX strings)."""
    work = Path(work_dir).absolute()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = work / candidate
    try:
        return candidate.absolute().relative_to(work).as_posix()
    except ValueError:
        return candidate.as_posix()


def abs_path(work_dir: str | os.PathLike, path: str | os.PathLike) -> Path:
    """Resolve a work-dir-relative path to an absolute one."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else Path(work_dir) / candidate


def step_dir(work_dir: str | os.PathLike, step_id: str, attempt: int, slot: str = CLI_SLOT) -> Path:
    """`steps/<step_id>/a<attempt>/<slot>` — the working space of one attempt (§2.2)."""
    return Path(work_dir) / "steps" / str(step_id) / f"a{int(attempt)}" / slot


def ensure_step_dir(work_dir: str | os.PathLike, step_id: str, attempt: int, slot: str = CLI_SLOT) -> Path:
    """Create and return the working directory of the attempt."""
    directory = step_dir(work_dir, step_id, attempt, slot)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def stage_input(
    work_dir: str | os.PathLike,
    step_id: str,
    attempt: int,
    source: str | os.PathLike,
    name: str | None = None,
) -> str | None:
    """Copy one input file into `steps/<step>/a<n>/cli/inputs/` (§2.2 «сначала копирует свои входы»).

    Written **once per identity** (D-42): a replay of the same `(step_id, attempt)` keeps the bytes
    the first run saw, so the recomputed result is the one the original input produces. Anything that
    happened to the canonical file after the crash (an external edit included) never becomes the
    input of the replay, and therefore never acquires an authoritative sha.
    """
    inputs = ensure_step_dir(work_dir, step_id, attempt) / "inputs"
    src = abs_path(work_dir, source)
    target = inputs / (name or src.name)
    if target.is_file():
        return rel_path(work_dir, target)
    if not src.is_file():
        return None
    inputs.mkdir(parents=True, exist_ok=True)
    state_io.write_bytes_atomic(target, src.read_bytes())
    return rel_path(work_dir, target)


def staged_input(
    work_dir: str | os.PathLike,
    step_id: str,
    attempt: int,
    name: str,
) -> Path | None:
    """The input this identity already staged, or None — the replay's own copy, never a fresh one.

    D-42: `stage_input` copies the canonical file when it is called for the first time, so a replay
    that wants to know what the interrupted run actually read has to ask **before** staging
    anything. Whatever happened to the canonical file after the crash never answers this.
    """
    candidate = step_dir(work_dir, step_id, attempt) / "inputs" / name
    return candidate if candidate.is_file() else None


def stage_result(
    work_dir: str | os.PathLike,
    step_id: str,
    attempt: int,
    name: str,
    payload: bytes,
) -> Path:
    """Write the recomputed result into the working directory of the attempt (§2.2)."""
    target = ensure_step_dir(work_dir, step_id, attempt) / name
    state_io.write_bytes_atomic(target, payload)
    return target


# --- identity -------------------------------------------------------------


def steps_for(state: dict, step_id: str) -> list[dict]:
    """Every `steps[]` record of one step id, oldest attempt first."""
    rows = [row for row in (state.get("steps") or []) if isinstance(row, dict) and row.get("step_id") == step_id]
    rows.sort(key=lambda row: int(row.get("attempt") or 1))
    return rows


def current_step(state: dict, step_id: str) -> dict | None:
    """The record of the highest attempt of `step_id`, or None when the step is unknown."""
    rows = steps_for(state, step_id)
    return rows[-1] if rows else None


def check_identity(state: dict, step_id: str, attempt: int, *, args_key: str | None = None) -> dict:
    """Execution identity of §3.1: `ok` (run it), `closed` (no-op) or `mismatch` (error).

    Strict (D-40): the step must be **issued** — present in `steps[]` — and `attempt` must be the
    attempt currently issued for it. An unknown step id, an attempt above the issued one (a call
    that was never handed out) and an attempt below it (a stale call of a superseded try) are all
    `identity_mismatch`; a closed identity answers with the stored `result_ref` instead of running
    the command again.
    """
    attempt = int(attempt)
    if attempt < 1:
        return {"status": STATUS_MISMATCH, "errors": [IDENTITY_MISMATCH], "reason": "attempt_below_one"}
    row = current_step(state, step_id)
    if row is None:
        return {"status": STATUS_MISMATCH, "errors": [IDENTITY_MISMATCH], "reason": "unknown_step"}
    row_attempt = int(row.get("attempt") or 1)
    if attempt > row_attempt:
        return {
            "status": STATUS_MISMATCH,
            "errors": [IDENTITY_MISMATCH],
            "reason": "unissued_attempt",
            "current_attempt": row_attempt,
        }
    if attempt < row_attempt:
        return {
            "status": STATUS_MISMATCH,
            "errors": [IDENTITY_MISMATCH],
            "reason": "stale_attempt",
            "current_attempt": row_attempt,
        }
    if row.get("status") in (None, ""):
        return {"status": STATUS_OK, "step": row}
    stored = row.get("result_ref")
    stored_args = stored.get("args") if isinstance(stored, dict) else None
    if args_key is not None and stored_args is not None and stored_args != args_key:
        return {
            "status": STATUS_MISMATCH,
            "errors": [IDENTITY_MISMATCH],
            "reason": "arguments_changed",
            "expected_args": stored_args,
        }
    result = stored.get("result") if isinstance(stored, dict) else stored
    return {"status": STATUS_CLOSED, "step": row, "result": result, "result_ref": stored}


# --- publication ----------------------------------------------------------


def published_entry(state: dict, canonical_path: str) -> dict | None:
    """The authoritative `published[]` record of one canonical path (§2.2)."""
    for row in reversed(state.get("published") or []):
        if isinstance(row, dict) and row.get("canonical_path") == canonical_path:
            return row
    return None


def published_sha(state: dict, canonical_path: str) -> str | None:
    """sha256 recorded in `published[]`, or None when the file was never published."""
    row = published_entry(state, canonical_path)
    return row.get("sha256") if row else None


def verify_published(work_dir: str | os.PathLike, state: dict, canonical_path: str) -> str | None:
    """Return `output_modified_after_publish` when a canonical file drifted from `published[]`."""
    expected = published_sha(state, canonical_path)
    if not expected:
        return None
    path = abs_path(work_dir, canonical_path)
    if not path.is_file():
        return OUTPUT_MODIFIED
    return None if state_io.sha256_file(path) == expected else OUTPUT_MODIFIED


def read_published(
    work_dir: str | os.PathLike,
    rel_path_: str,
    *,
    state: dict | None = None,
) -> dict | str:
    """Read one canonical file after checking its bytes against `published[]` (D-41, §2.2).

    `*.json` comes back parsed, anything else as text. A file that drifted from its published sha
    raises `OutputModifiedAfterPublish`, which the command turns into
    `{"errors": ["output_modified_after_publish"]}` so the step is reissued (`reason: recovery`).
    """
    if state is None:
        state = state_io.read_state_or_none(work_dir) or {}
    if verify_published(work_dir, state, rel_path_):
        raise OutputModifiedAfterPublish(rel_path_)
    path = abs_path(work_dir, rel_path_)
    if path.suffix.lower() == ".json":
        return state_io.read_json(path)
    return path.read_text(encoding="utf-8-sig")


def drift_result(exc: OutputModifiedAfterPublish, **extra: object) -> dict:
    """The recovery answer of a command whose canonical input drifted (§2.2, D-41)."""
    return {"errors": [OUTPUT_MODIFIED], "path": exc.canonical_path, **extra}


def publish_file(
    work_dir: str | os.PathLike,
    work_path: str | os.PathLike,
    canonical_path: str | os.PathLike,
    *,
    by: str = "command",
    step_id: str | None = None,
) -> dict:
    """tmp + `os.replace` the attempt result onto its canonical path; return the `published[]` entry."""
    source = abs_path(work_dir, work_path)
    target = abs_path(work_dir, canonical_path)
    payload = source.read_bytes()
    state_io.write_bytes_atomic(target, payload)
    return {
        "canonical_path": rel_path(work_dir, target),
        "sha256": state_io.sha256_bytes(payload),
        "by": by,
        "step_id": step_id,
        "at": events.utc_now(),
    }


def publish(
    work_dir: str | os.PathLike,
    step_id: str,
    attempt: int,
    work_path: str | os.PathLike,
    canonical_path: str | os.PathLike,
    by: str = "command",
) -> dict:
    """Publish a file and record it in `state.published[]` (§2.2; the step itself stays open)."""
    entry = publish_file(work_dir, work_path, canonical_path, by=by, step_id=step_id)

    def mutator(state: dict) -> None:
        merge_published(state, [entry])

    state_io.write_state(work_dir, mutator)
    return entry


def merge_published(state: dict, entries: Iterable[dict]) -> None:
    """Replace, in place, the `published[]` rows of the canonical paths in `entries`."""
    rows = [row for row in (state.get("published") or []) if isinstance(row, dict)]
    for entry in entries:
        rows = [row for row in rows if row.get("canonical_path") != entry["canonical_path"]]
        rows.append(dict(entry))
    state["published"] = rows


# --- closing --------------------------------------------------------------


def close_step(
    work_dir: str | os.PathLike,
    step_id: str,
    attempt: int,
    result: dict | str | None,
    *,
    kind: str = "script",
    phase: str | None = None,
    status: str = "ok",
    reason: str = "initial",
    args_key: str | None = None,
    published: Sequence[dict] = (),
    mutate: Callable[[dict], None] | None = None,
    command: Sequence[str] | None = None,
    actor: str = "cli",
    writer: Callable[..., dict] | None = None,
) -> dict:
    """One state write that publishes, closes the step and applies the command's own mutation (§2.2).

    The identity is re-checked here, i.e. under `state.lock` and immediately before the write
    (D-40): between the command's own check and this commit another `next` may have issued a new
    attempt, and a stale run must never close it. An unknown step is still created — the strict
    gate is `check_identity`, which the command runs before any side effect.

    `writer` selects the state writer: `finalize --salvage` passes
    `state_io.write_state_unvalidated` (D-13). That exception is the only reason the writer is a
    parameter; the step protocol itself stays this one implementation (D-40).
    """
    attempt = int(attempt)

    def mutator(state: dict) -> None:
        identity = check_identity(state, step_id, attempt, args_key=args_key)
        if identity["status"] == STATUS_MISMATCH and identity.get("reason") != "unknown_step":
            raise IdentityMismatch(
                step_id, attempt, str(identity.get("reason")), identity.get("current_attempt")
            )
        merge_published(state, published)
        steps = [row for row in (state.get("steps") or []) if isinstance(row, dict)]
        row = None
        for candidate in steps:
            if candidate.get("step_id") == step_id and int(candidate.get("attempt") or 1) == attempt:
                row = candidate
                break
        if row is None:
            row = {
                "step_id": step_id,
                "kind": kind,
                "phase": phase or state.get("current_phase"),
                "attempt": attempt,
                "reason": reason,
                "issued_at": events.utc_now(),
                "status": None,
            }
            steps.append(row)
        if command:
            row["command"] = list(command)
        row["status"] = status
        row["closed_at"] = events.utc_now()
        row["result_ref"] = {"args": args_key, "result": result}
        state["steps"] = steps
        if mutate is not None:
            mutate(state)

    return (writer or state_io.write_state)(work_dir, mutator, actor=actor)
