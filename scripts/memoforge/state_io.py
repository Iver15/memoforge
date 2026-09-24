"""Locking, atomic JSON writes and the single writer of `state.json` (ТЗ §2.2, M2)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import limits, schema

STATE_FILENAME = "state.json"

LOCK_ORDER: tuple[str, ...] = ("brief", "state", "sources", "events")
"""Strict nesting order of §2.2; taking a lock «upwards» raises LockOrderViolation.

D-224: `brief` guards `brief/state.json` of `/memoforge:brief`; its lock file sits beside the others,
outside the `brief/` folder the driver archives. The driver never appends an event while holding it.
"""

LOCK_FILENAMES: tuple[str, ...] = tuple(f"{name}.lock" for name in LOCK_ORDER)
"""Lock files are created by `task new`, never deleted and never replaced (§2.2)."""

_UNRANKED = len(LOCK_ORDER) + 1

_local = threading.local()


class LockOrderViolation(RuntimeError):
    """Raised when a lock is taken out of the `brief -> state -> sources -> events` order (§2.2)."""

    code = "lock_order_violation"


class LockTimeout(TimeoutError):
    """Raised when `limits.LOCK_TIMEOUT` elapses while waiting for an advisory lock (§2.2)."""

    code = "lock_timeout"


def _lock_stack() -> list[dict]:
    stack = getattr(_local, "mf_lock_stack", None)
    if stack is None:
        stack = []
        _local.mf_lock_stack = stack
    return stack


def _lock_rank(name: str) -> int:
    try:
        return LOCK_ORDER.index(name)
    except ValueError:
        return _UNRANKED


class FileLock:
    """Advisory lock on a permanent lock file: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows.

    The lock is released by the OS when the process dies, so no TTL, pid check or reclamation
    exists (§2.2). Re-entering the same lock file in the same thread is a no-op.
    """

    def __init__(self, path: str | os.PathLike, timeout: float | None = None, name: str | None = None) -> None:
        self.path = Path(path)
        self.timeout = limits.LOCK_TIMEOUT if timeout is None else timeout
        if name is None:
            filename = self.path.name
            name = filename[: -len(".lock")] if filename.endswith(".lock") else filename
        self.name = name
        self.rank = _lock_rank(self.name)
        self._resolved = str(self.path.absolute())
        self._fd: int | None = None
        self._reentrant = False

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    # -- api ---------------------------------------------------------------
    def acquire(self) -> "FileLock":
        """Take the lock, waiting at most `timeout` seconds."""
        resolved = self._resolved
        stack = _lock_stack()
        for entry in stack:
            if entry["path"] == resolved:
                entry["depth"] += 1
                self._reentrant = True
                return self
        held_above = [entry["name"] for entry in stack if entry["rank"] > self.rank]
        if held_above:
            raise LockOrderViolation(
                f"{LockOrderViolation.code}: cannot take {self.name!r} while holding "
                f"{held_above!r}; order is {' -> '.join(LOCK_ORDER)}"
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _lock_fd(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(
                        f"{LockTimeout.code}: {self.path} is held by another process "
                        f"(waited {self.timeout}s)"
                    )
                time.sleep(limits.LOCK_POLL_INTERVAL)
        self._fd = fd
        stack.append({"path": resolved, "name": self.name, "rank": self.rank, "depth": 1})
        return self

    def release(self) -> None:
        """Release the lock (or one nesting level of it)."""
        stack = _lock_stack()
        if self._reentrant:
            self._reentrant = False
            for entry in reversed(stack):
                if entry["path"] == self._resolved:
                    entry["depth"] -= 1
                    break
            return
        if self._fd is None:
            return
        for index in range(len(stack) - 1, -1, -1):
            if stack[index]["path"] == self._resolved:
                stack.pop(index)
                break
        try:
            _unlock_fd(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None


if os.name == "nt":  # pragma: no cover - platform branch
    import msvcrt

    def _lock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - platform branch
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# --- paths ----------------------------------------------------------------


def state_path(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/state.json`."""
    return Path(work_dir) / STATE_FILENAME


def lock_path(work_dir: str | os.PathLike, name: str) -> Path:
    """`<work_dir>/<name>.lock` for one of brief/state/sources/events."""
    if name not in LOCK_ORDER:
        raise ValueError(f"unknown_lock: {name!r}")
    return Path(work_dir) / f"{name}.lock"


def ensure_lock_files(work_dir: str | os.PathLike) -> list[Path]:
    """Create the permanent lock files if missing (§2.2: created by `task new`)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for filename in LOCK_FILENAMES:
        path = work_dir / filename
        if not path.exists():
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
            os.close(fd)
        created.append(path)
    return created


# --- json io --------------------------------------------------------------


def read_json(path: str | os.PathLike) -> dict:
    """Read a JSON document as utf-8-sig (CONVENTIONS, M12)."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return json.loads(text)


def dumps(obj: object) -> str:
    """Canonical serialization used for every JSON artifact of the pipeline."""
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_json_atomic(path: str | os.PathLike, obj: object) -> Path:
    """tmp + `os.replace` with ×5 retries on Windows sharing violations (§2.2)."""
    path = Path(path)
    return write_bytes_atomic(path, dumps(obj).encode("utf-8"))


def write_bytes_atomic(path: str | os.PathLike, payload: bytes) -> Path:
    """Write bytes through a temporary file in the same directory and `os.replace`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return path


def _replace_with_retry(src: str, dst: Path) -> None:
    last: OSError | None = None
    for attempt in range(limits.ATOMIC_REPLACE_RETRIES):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError as exc:  # WinError 32: file used by another process
            last = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) != 32:
                raise
            last = exc
        time.sleep(limits.ATOMIC_REPLACE_BACKOFF * (2**attempt))
    raise last if last is not None else OSError(f"replace failed: {dst}")


def sha256_file(path: str | os.PathLike) -> str:
    """sha256 hex digest of a file (published[] / done-marker provenance, §2.2)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """sha256 hex digest of a byte string."""
    return hashlib.sha256(payload).hexdigest()


# --- state ----------------------------------------------------------------


def read_state(work_dir: str | os.PathLike) -> dict:
    """Read `state.json` (no lock needed: writes are atomic replaces)."""
    return read_json(state_path(work_dir))


def read_state_or_none(work_dir: str | os.PathLike) -> dict | None:
    """Read `state.json`, returning None when it is missing or unreadable."""
    try:
        state = read_state(work_dir)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def create_state(work_dir: str | os.PathLike, state: dict, *, actor: str = "cli") -> dict:
    """Validate and write the initial `state.json` under `state.lock` (used by `task new`)."""
    work_dir = Path(work_dir)
    ensure_lock_files(work_dir)
    with FileLock(lock_path(work_dir, "state")):
        path = state_path(work_dir)
        if path.exists():
            raise FileExistsError(f"state_exists: {path}")
        schema.validate_or_raise(state, "state")
        payload = dumps(state).encode("utf-8")
        write_bytes_atomic(path, payload)
        _emit_state_written(work_dir, state, actor, sha256_bytes(payload))
    return state


def write_state(
    work_dir: str | os.PathLike,
    mutator: Callable[[dict], None],
    *,
    actor: str = "cli",
    emit_event: bool = True,
) -> dict:
    """read -> mutate -> validate -> `os.replace`, all under `state.lock` (M2, §2.2).

    The mutator changes the state in place (a returned dict replaces it). An invalid result raises
    `schema.SchemaValidationError` and leaves `state.json` untouched.
    """
    work_dir = Path(work_dir)
    with FileLock(lock_path(work_dir, "state")):
        path = state_path(work_dir)
        state = read_json(path)
        if not isinstance(state, dict):
            raise ValueError("state_not_an_object")
        result = mutator(state)
        if isinstance(result, dict):
            state = result
        schema.validate_or_raise(state, "state")
        payload = dumps(state).encode("utf-8")
        write_bytes_atomic(path, payload)
        if emit_event:
            _emit_state_written(work_dir, state, actor, sha256_bytes(payload))
    return state


def write_state_unvalidated(
    work_dir: str | os.PathLike,
    mutator: Callable[[dict], None],
    *,
    actor: str = "cli",
    emit_event: bool = True,
) -> dict:
    """read -> mutate -> `os.replace` under `state.lock`, WITHOUT the schema check.

    # S5: salvage only. `mf finalize --salvage` must reach a terminal phase on a host where
    # `jsonschema` is not installed (M9, §5.6), and `write_state` cannot: it validates before the
    # replace and would raise `DependencyMissing`. No other command may use this function — the
    # normal writer stays `write_state`, so M2 keeps its «valid state.json or no write» guarantee.
    """
    work_dir = Path(work_dir)
    with FileLock(lock_path(work_dir, "state")):
        path = state_path(work_dir)
        state = read_json(path)
        if not isinstance(state, dict):
            raise ValueError("state_not_an_object")
        result = mutator(state)
        if isinstance(result, dict):
            state = result
        payload = dumps(state).encode("utf-8")
        write_bytes_atomic(path, payload)
        if emit_event:
            _emit_state_written(work_dir, state, actor, sha256_bytes(payload))
    return state


def _emit_state_written(work_dir: Path, state: dict, actor: str, sha256: str) -> None:
    from . import events  # local import: events.append_event uses FileLock from this module

    try:
        events.append_event(
            work_dir,
            "state_written",
            actor,
            {
                "schema_version": state.get("schema_version"),
                "task_id": state.get("task_id"),
                # G3 (§0.2) is measured by comparing this digest with sha256(state.json).
                "sha256": sha256,
            },
            phase=state.get("current_phase"),
        )
    except OSError:
        # The journal is best effort; a failed append never fails a state write (§7.2).
        pass
