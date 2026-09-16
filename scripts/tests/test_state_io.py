"""Tests for scripts/memoforge/state_io.py — locks and the single state writer (ТЗ §2.2, M2, §9)."""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import events, schema, state_io, task  # noqa: E402

WORKERS = 12
READ_PROBES = 8


# --- helpers shared with the spawned children ------------------------------


def make_task(root: Path, task_id: str = "memo-20260101T000000Z-lock-test") -> Path:
    """Create a work dir with a schema-valid v2 state.json."""
    work_dir = Path(root) / task_id
    task.create_work_dir_tree(work_dir)
    state = task.build_initial_state(
        task_id=task_id,
        user_query="lock test",
        language="en",
        work_dir=work_dir,
        output_folder=Path(root),
        config={"writer_model": "opus", "source_review_gate": "auto", "intake_max_questions": 5},
        created_at="2026-01-01T00:00:00.000Z",
    )
    state_io.create_state(work_dir, state)
    return work_dir


def increment_iteration(state: dict) -> None:
    """Mutator used by the concurrency tests."""
    state["current_iteration"] = int(state.get("current_iteration", 0)) + 1


def read_probe(work_dir: str) -> int:
    """Return 1 when state.json was readable but not parseable (torn write)."""
    path = Path(work_dir) / "state.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    try:
        json.loads(text)
    except ValueError:
        return 1
    return 0


def wait_for(path: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.02)
    return False


def worker_write_state(work_dir: str, index: int, ready: str, go: str, result: str) -> None:
    """Child process: one `write_state` increment plus torn-JSON probes around it."""
    from memoforge import state_io as child_state_io

    Path(ready).write_text("ready", encoding="utf-8")
    wait_for(go)
    torn = 0
    outcome = {"index": index, "error": None, "torn": 0}
    for _ in range(READ_PROBES):
        torn += read_probe(work_dir)
        time.sleep(0.005)
    try:
        child_state_io.write_state(work_dir, increment_iteration, actor=f"worker-{index}")
    except BaseException as exc:  # noqa: BLE001 - reported back to the parent
        outcome["error"] = repr(exc)
    for _ in range(READ_PROBES):
        torn += read_probe(work_dir)
        time.sleep(0.005)
    outcome["torn"] = torn
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def worker_counter(lock_file: str, counter_file: str, rounds: int, ready: str, go: str) -> None:
    """Child process: non-atomic read/modify/write of a counter under FileLock."""
    from memoforge import state_io as child_state_io

    Path(ready).write_text("ready", encoding="utf-8")
    wait_for(go)
    for _ in range(rounds):
        with child_state_io.FileLock(lock_file, timeout=60):
            value = int(Path(counter_file).read_text(encoding="utf-8").strip() or "0")
            time.sleep(0.001)
            Path(counter_file).write_text(str(value + 1), encoding="utf-8")


def worker_hold_lock(lock_file: str, ready: str, seconds: float) -> None:
    """Child process: take the lock, announce it and sleep until killed."""
    from memoforge import state_io as child_state_io

    with child_state_io.FileLock(lock_file, timeout=30):
        Path(ready).write_text("held", encoding="utf-8")
        time.sleep(seconds)


def spawn(target, args):
    ctx = multiprocessing.get_context("spawn")
    return ctx.Process(target=target, args=args)


# --- tests -----------------------------------------------------------------


class JsonIoTest(unittest.TestCase):
    def test_read_json_accepts_utf8_sig(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text('{"a": "тест"}', encoding="utf-8-sig")
            self.assertEqual(state_io.read_json(path), {"a": "тест"})

    def test_write_json_atomic_leaves_no_tmp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            state_io.write_json_atomic(path, {"a": 1, "b": "ю"})
            self.assertEqual(state_io.read_json(path), {"a": 1, "b": "ю"})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["x.json"])

    def test_write_json_atomic_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            state_io.write_json_atomic(path, {"v": 1})
            state_io.write_json_atomic(path, {"v": 2})
            self.assertEqual(state_io.read_json(path), {"v": 2})

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                state_io.sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )
            self.assertEqual(state_io.sha256_bytes(b"abc"), state_io.sha256_file(path))


class LockOrderTest(unittest.TestCase):
    def test_lock_files_are_created_by_task_new_and_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            for name in state_io.LOCK_FILENAMES:
                self.assertTrue((work_dir / name).is_file(), name)

    def test_declared_order(self):
        self.assertEqual(state_io.LOCK_ORDER, ("state", "sources", "events"))

    def test_downward_nesting_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            with state_io.FileLock(state_io.lock_path(work_dir, "state")):
                with state_io.FileLock(state_io.lock_path(work_dir, "sources")):
                    with state_io.FileLock(state_io.lock_path(work_dir, "events")):
                        pass

    def test_upward_nesting_raises_lock_order_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            with state_io.FileLock(state_io.lock_path(work_dir, "events")):
                with self.assertRaises(state_io.LockOrderViolation) as ctx:
                    with state_io.FileLock(state_io.lock_path(work_dir, "state")):
                        pass
            self.assertIn("lock_order_violation", str(ctx.exception))

    def test_sources_under_events_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            with state_io.FileLock(state_io.lock_path(work_dir, "events")):
                with self.assertRaises(state_io.LockOrderViolation):
                    with state_io.FileLock(state_io.lock_path(work_dir, "sources")):
                        pass

    def test_stack_is_clean_after_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            with state_io.FileLock(state_io.lock_path(work_dir, "events")):
                with self.assertRaises(state_io.LockOrderViolation):
                    with state_io.FileLock(state_io.lock_path(work_dir, "state")):
                        pass
            # after leaving the events lock the state lock is free again
            with state_io.FileLock(state_io.lock_path(work_dir, "state"), timeout=5):
                pass

    def test_reentrant_same_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            path = state_io.lock_path(work_dir, "state")
            with state_io.FileLock(path):
                with state_io.FileLock(path, timeout=2):
                    pass

    def test_unknown_lock_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                state_io.lock_path(tmp, "draft")


class WriteStateTest(unittest.TestCase):
    def test_write_state_mutates_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            state = state_io.write_state(work_dir, increment_iteration)
            self.assertEqual(state["current_iteration"], 1)
            self.assertEqual(state_io.read_state(work_dir)["current_iteration"], 1)

    def test_write_state_emits_state_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            before = len([e for e in events.read_events(work_dir) if e["event"] == "state_written"])
            state_io.write_state(work_dir, increment_iteration)
            after = [e for e in events.read_events(work_dir) if e["event"] == "state_written"]
            self.assertEqual(len(after), before + 1)
            self.assertEqual(after[-1]["phase"], "intake_preliminary_research")
            # G3 (§0.2) is measured by comparing this digest with the file on disk (D-15).
            self.assertEqual(
                after[-1]["data"]["sha256"],
                state_io.sha256_file(state_io.state_path(work_dir)),
            )

    def test_every_state_written_carries_the_sha_of_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            created = [e for e in events.read_events(work_dir) if e["event"] == "state_written"]
            self.assertEqual(
                created[0]["data"]["sha256"],
                state_io.sha256_file(state_io.state_path(work_dir)),
                "create_state must report the sha of what it wrote",
            )
            digests = []
            for _ in range(3):
                state_io.write_state(work_dir, increment_iteration)
                digests.append(state_io.sha256_file(state_io.state_path(work_dir)))
            logged = [
                e["data"]["sha256"]
                for e in events.read_events(work_dir)
                if e["event"] == "state_written"
            ]
            self.assertEqual(logged[1:], digests)
            self.assertEqual(len(set(digests)), 3, "each write changes the file")

    def test_schema_reject_leaves_the_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            path = state_io.state_path(work_dir)
            before = path.read_bytes()
            events_before = len(events.read_events(work_dir))

            def bad(state: dict) -> None:
                state["current_phase"] = "not_a_phase"

            with self.assertRaises(schema.SchemaValidationError):
                state_io.write_state(work_dir, bad)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(events.read_events(work_dir)), events_before)

    def test_unknown_top_level_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            path = state_io.state_path(work_dir)
            before = path.read_bytes()

            def bad(state: dict) -> None:
                state["live_progress"] = {"removed": "in v2"}

            with self.assertRaises(schema.SchemaValidationError):
                state_io.write_state(work_dir, bad)
            self.assertEqual(path.read_bytes(), before)

    def test_a_field_removed_from_config_in_v2_is_rejected(self):
        """§2.2 «Удалены»: `$defs.config` is closed too, not only the root (finding 11)."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            path = state_io.state_path(work_dir)
            before = path.read_bytes()

            for field, value in (
                ("live_progress_enabled", True),
                ("visualize_mode", "dashboard"),
            ):
                with self.subTest(field=field):

                    def bad(state: dict, field=field, value=value) -> None:
                        state["config"][field] = value

                    with self.assertRaises(schema.SchemaValidationError):
                        state_io.write_state(work_dir, bad)
                    self.assertEqual(path.read_bytes(), before)

    def test_mutator_exception_leaves_the_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            path = state_io.state_path(work_dir)
            before = path.read_bytes()

            def boom(state: dict) -> None:
                raise RuntimeError("nope")

            with self.assertRaises(RuntimeError):
                state_io.write_state(work_dir, boom)
            self.assertEqual(path.read_bytes(), before)

    def test_create_state_refuses_to_clobber(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = make_task(Path(tmp))
            state = state_io.read_state(work_dir)
            with self.assertRaises(FileExistsError):
                state_io.create_state(work_dir, state)


class ConcurrencyTest(unittest.TestCase):
    def test_twelve_processes_increment_the_counter_without_torn_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = make_task(root)
            procs = []
            ready = [root / f"ready-{i}" for i in range(WORKERS)]
            results = [root / f"result-{i}.json" for i in range(WORKERS)]
            go = root / "go"
            for i in range(WORKERS):
                proc = spawn(
                    worker_write_state,
                    (str(work_dir), i, str(ready[i]), str(go), str(results[i])),
                )
                proc.start()
                procs.append(proc)
            try:
                for path in ready:
                    self.assertTrue(wait_for(str(path), 120), f"child never started: {path}")
                go.write_text("go", encoding="utf-8")
                for proc in procs:
                    proc.join(180)
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.kill()
                        proc.join(10)

            outcomes = []
            for path in results:
                self.assertTrue(path.exists(), f"missing result: {path}")
                outcomes.append(json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual([o["error"] for o in outcomes], [None] * WORKERS)
            self.assertEqual(sum(o["torn"] for o in outcomes), 0)
            self.assertEqual(state_io.read_state(work_dir)["current_iteration"], WORKERS)
            self.assertEqual(
                len([e for e in events.read_events(work_dir) if e["event"] == "state_written"]),
                WORKERS + 1,  # + the one written by create_state
            )

    def test_mutual_exclusion_of_two_processes(self):
        rounds = 40
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_file = root / "state.lock"
            counter = root / "counter.txt"
            counter.write_text("0", encoding="utf-8")
            ready = [root / "ready-a", root / "ready-b"]
            go = root / "go"
            procs = [
                spawn(worker_counter, (str(lock_file), str(counter), rounds, str(path), str(go)))
                for path in ready
            ]
            for proc in procs:
                proc.start()
            try:
                for path in ready:
                    self.assertTrue(wait_for(str(path), 120))
                go.write_text("go", encoding="utf-8")
                for proc in procs:
                    proc.join(180)
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.kill()
                        proc.join(10)
            self.assertEqual(int(counter.read_text(encoding="utf-8")), rounds * 2)

    def test_process_killed_under_the_lock_does_not_block_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = make_task(root)
            lock_file = state_io.lock_path(work_dir, "state")
            ready = root / "held"
            proc = spawn(worker_hold_lock, (str(lock_file), str(ready), 120.0))
            proc.start()
            try:
                self.assertTrue(wait_for(str(ready), 120), "child never took the lock")
                with self.assertRaises(state_io.LockTimeout):
                    with state_io.FileLock(lock_file, timeout=0.5):
                        pass
                proc.kill()
                proc.join(30)
            finally:
                if proc.is_alive():  # pragma: no cover - defensive
                    proc.kill()
                    proc.join(10)
            with state_io.FileLock(lock_file, timeout=20):
                pass
            state = state_io.write_state(work_dir, increment_iteration)
            self.assertEqual(state["current_iteration"], 1)


@unittest.skipUnless(os.name == "nt", "Windows-specific msvcrt.locking behaviour")
class WindowsLockTest(unittest.TestCase):
    def test_second_handle_in_the_same_process_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = make_task(root)
            lock_file = state_io.lock_path(work_dir, "state")
            first = state_io.FileLock(lock_file)
            first.acquire()
            try:
                other = state_io.FileLock(lock_file, timeout=0.4)
                other.name = "state-clone"  # bypass the thread-local re-entrancy shortcut
                other._resolved = str(lock_file.absolute()) + "#clone"
                with self.assertRaises(state_io.LockTimeout):
                    other.acquire()
            finally:
                first.release()


if __name__ == "__main__":
    unittest.main()
