"""Tests for scripts/memoforge/stepctx.py — step identity, publication and closing (ТЗ §2.2, §3.1)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import state_io, stepctx, task  # noqa: E402

TASK_ID = "memo-20260101T000000Z-stepctx"


def issue_step(work_dir: Path, step_id: str, attempt: int = 1) -> None:
    """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

    def mutator(state: dict) -> None:
        rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
        if any(row.get("step_id") == step_id and int(row.get("attempt") or 1) == attempt for row in rows):
            return
        rows.append(
            {
                "step_id": step_id,
                "kind": "script",
                "phase": state.get("current_phase"),
                "attempt": attempt,
                "reason": "initial",
                "issued_at": "2026-09-08T12:00:00.000Z",
                "status": None,
            }
        )
        state["steps"] = rows

    state_io.write_state(work_dir, mutator)


def make_task(root: Path) -> Path:
    """Work dir with a schema-valid v2 state.json."""
    work_dir = Path(root) / TASK_ID
    task.create_work_dir_tree(work_dir)
    state = task.build_initial_state(
        task_id=TASK_ID,
        user_query="step context",
        language="en",
        work_dir=work_dir,
        output_folder=Path(root),
        config={"writer_model": "opus", "source_review_gate": "auto", "intake_max_questions": 5},
        created_at="2026-01-01T00:00:00.000Z",
    )
    state_io.create_state(work_dir, state)
    return work_dir


class StepContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work_dir = make_task(self.root)
        self.addCleanup(self._tmp.cleanup)

    # --- paths ------------------------------------------------------------

    def test_step_dir_is_the_attempt_workspace(self):
        directory = stepctx.step_dir(self.work_dir, "s-010", 2)
        self.assertEqual(("steps", "s-010", "a2", "cli"), directory.parts[-4:])

    def test_rel_path_is_posix_and_relative_to_work_dir(self):
        path = self.work_dir / "research" / "sources.json"
        self.assertEqual("research/sources.json", stepctx.rel_path(self.work_dir, path))
        self.assertEqual("research/sources.json", stepctx.rel_path(self.work_dir, "research/sources.json"))

    def test_stage_input_copies_the_input_into_the_attempt_workspace(self):
        source = self.work_dir / "research" / "sources.json"
        source.write_text("{}", encoding="utf-8")
        staged = stepctx.stage_input(self.work_dir, "s-010", 1, source)
        self.assertEqual("steps/s-010/a1/cli/inputs/sources.json", staged)
        self.assertEqual("{}", (self.work_dir / staged).read_text(encoding="utf-8"))

    def test_stage_input_of_a_missing_file_is_none(self):
        self.assertIsNone(stepctx.stage_input(self.work_dir, "s-010", 1, self.work_dir / "nope.json"))

    def test_stage_input_saves_the_input_once_per_identity(self):
        """D-42: a replay must recompute from the bytes the first run saw, not from today's file."""
        source = self.work_dir / "drafts" / "v1.md"
        source.write_text("original", encoding="utf-8")
        staged = stepctx.stage_input(self.work_dir, "s-010", 1, source)
        source.write_text("edited between the crash and the replay", encoding="utf-8")
        again = stepctx.stage_input(self.work_dir, "s-010", 1, source)
        self.assertEqual(staged, again)
        self.assertEqual("original", (self.work_dir / staged).read_text(encoding="utf-8"))

    def test_a_new_attempt_stages_its_own_input(self):
        source = self.work_dir / "drafts" / "v1.md"
        source.write_text("v1", encoding="utf-8")
        stepctx.stage_input(self.work_dir, "s-010", 1, source)
        source.write_text("v1 after the fix round", encoding="utf-8")
        staged = stepctx.stage_input(self.work_dir, "s-010", 2, source)
        self.assertEqual("v1 after the fix round", (self.work_dir / staged).read_text(encoding="utf-8"))

    # --- identity ---------------------------------------------------------

    def test_unknown_step_is_identity_mismatch(self):
        """D-40: an identity that `next` never issued is a mismatch, not a licence to run."""
        state = state_io.read_state(self.work_dir)
        identity = stepctx.check_identity(state, "s-010", 1)
        self.assertEqual(stepctx.STATUS_MISMATCH, identity["status"])
        self.assertEqual(["identity_mismatch"], identity["errors"])
        self.assertEqual("unknown_step", identity["reason"])

    def test_closed_step_repeats_as_a_no_op_with_the_stored_result(self):
        stepctx.close_step(self.work_dir, "s-010", 1, {"value": 42}, args_key="k")
        state = state_io.read_state(self.work_dir)
        identity = stepctx.check_identity(state, "s-010", 1, args_key="k")
        self.assertEqual(stepctx.STATUS_CLOSED, identity["status"])
        self.assertEqual({"value": 42}, identity["result"])

    def test_same_identity_with_other_arguments_is_identity_mismatch(self):
        stepctx.close_step(self.work_dir, "s-010", 1, {"value": 42}, args_key="draft=v1.md")
        state = state_io.read_state(self.work_dir)
        identity = stepctx.check_identity(state, "s-010", 1, args_key="draft=v2.md")
        self.assertEqual(stepctx.STATUS_MISMATCH, identity["status"])
        self.assertEqual(["identity_mismatch"], identity["errors"])
        self.assertEqual("arguments_changed", identity["reason"])

    def test_stale_attempt_is_identity_mismatch(self):
        stepctx.close_step(self.work_dir, "s-010", 2, {"value": 1})
        state = state_io.read_state(self.work_dir)
        identity = stepctx.check_identity(state, "s-010", 1)
        self.assertEqual(stepctx.STATUS_MISMATCH, identity["status"])
        self.assertEqual("stale_attempt", identity["reason"])

    def test_an_attempt_above_the_issued_one_is_identity_mismatch(self):
        """D-40: `a2` runs only once `next` issued it; `a99` against an open `a1` never does."""
        stepctx.close_step(self.work_dir, "s-010", 1, {"value": 1})
        state = state_io.read_state(self.work_dir)
        identity = stepctx.check_identity(state, "s-010", 2)
        self.assertEqual(stepctx.STATUS_MISMATCH, identity["status"])
        self.assertEqual("unissued_attempt", identity["reason"])
        self.assertEqual(1, identity["current_attempt"])

    def test_an_issued_higher_attempt_may_run(self):
        stepctx.close_step(self.work_dir, "s-010", 1, {"value": 1})
        issue_step(self.work_dir, "s-010", 2)
        state = state_io.read_state(self.work_dir)
        self.assertEqual(stepctx.STATUS_OK, stepctx.check_identity(state, "s-010", 2)["status"])

    def test_open_step_of_the_same_attempt_may_run(self):
        def mutator(state: dict) -> None:
            state["steps"] = [
                {
                    "step_id": "s-010",
                    "kind": "script",
                    "phase": state["current_phase"],
                    "attempt": 1,
                    "reason": "initial",
                    "status": None,
                }
            ]

        state_io.write_state(self.work_dir, mutator)
        state = state_io.read_state(self.work_dir)
        self.assertEqual(stepctx.STATUS_OK, stepctx.check_identity(state, "s-010", 1)["status"])

    # --- publication ------------------------------------------------------

    def test_publish_writes_the_canonical_file_and_the_published_row(self):
        work_file = stepctx.stage_result(self.work_dir, "s-010", 1, "source-pack.json", b'{"a":1}')
        entry = stepctx.publish(self.work_dir, "s-010", 1, work_file, "research/source-pack.json")
        canonical = self.work_dir / "research" / "source-pack.json"
        self.assertEqual(b'{"a":1}', canonical.read_bytes())
        self.assertEqual(state_io.sha256_file(canonical), entry["sha256"])
        state = state_io.read_state(self.work_dir)
        self.assertEqual(entry["sha256"], stepctx.published_sha(state, "research/source-pack.json"))

    def test_publish_replaces_the_previous_row_for_the_same_path(self):
        first = stepctx.stage_result(self.work_dir, "s-010", 1, "v1.md", b"one")
        stepctx.publish(self.work_dir, "s-010", 1, first, "drafts/v1.md")
        second = stepctx.stage_result(self.work_dir, "s-011", 1, "v1.md", b"two")
        entry = stepctx.publish(self.work_dir, "s-011", 1, second, "drafts/v1.md")
        state = state_io.read_state(self.work_dir)
        rows = [row for row in state["published"] if row["canonical_path"] == "drafts/v1.md"]
        self.assertEqual(1, len(rows))
        self.assertEqual(entry["sha256"], rows[0]["sha256"])

    def test_verify_published_detects_a_file_changed_after_publication(self):
        work_file = stepctx.stage_result(self.work_dir, "s-010", 1, "v1.md", b"one")
        stepctx.publish(self.work_dir, "s-010", 1, work_file, "drafts/v1.md")
        state = state_io.read_state(self.work_dir)
        self.assertIsNone(stepctx.verify_published(self.work_dir, state, "drafts/v1.md"))
        (self.work_dir / "drafts" / "v1.md").write_text("tampered", encoding="utf-8")
        self.assertEqual(
            stepctx.OUTPUT_MODIFIED, stepctx.verify_published(self.work_dir, state, "drafts/v1.md")
        )

    def test_verify_published_ignores_a_never_published_file(self):
        state = state_io.read_state(self.work_dir)
        self.assertIsNone(stepctx.verify_published(self.work_dir, state, "drafts/v9.md"))

    # --- closing ----------------------------------------------------------

    def test_close_step_publishes_and_closes_in_one_state_write(self):
        work_file = stepctx.stage_result(self.work_dir, "s-010", 1, "lint.json", b"{}")
        entry = stepctx.publish_file(self.work_dir, work_file, "lint.json", step_id="s-010")
        before = len(state_io.read_state(self.work_dir).get("published") or [])
        stepctx.close_step(
            self.work_dir,
            "s-010",
            1,
            {"clean": True},
            published=[entry],
            mutate=lambda state: state.update({"sources_frozen": True}),
        )
        state = state_io.read_state(self.work_dir)
        self.assertEqual(before + 1, len(state["published"]))
        self.assertTrue(state["sources_frozen"])
        step = stepctx.current_step(state, "s-010")
        self.assertEqual("ok", step["status"])
        self.assertEqual({"clean": True}, step["result_ref"]["result"])
        self.assertIsNotNone(step["closed_at"])

    def test_close_step_keeps_the_state_schema_valid(self):
        stepctx.close_step(self.work_dir, "s-010", 1, {"ok": True}, phase="source_pack")
        state = state_io.read_state(self.work_dir)
        step = stepctx.current_step(state, "s-010")
        self.assertEqual("source_pack", step["phase"])
        self.assertEqual("script", step["kind"])
        self.assertEqual("initial", step["reason"])

    def test_close_step_reuses_an_open_record_of_the_same_attempt(self):
        def mutator(state: dict) -> None:
            state["steps"] = [
                {
                    "step_id": "s-010",
                    "kind": "script",
                    "phase": "source_pack",
                    "attempt": 1,
                    "reason": "recovery",
                    "status": None,
                }
            ]

        state_io.write_state(self.work_dir, mutator)
        stepctx.close_step(self.work_dir, "s-010", 1, {"ok": True})
        state = state_io.read_state(self.work_dir)
        self.assertEqual(1, len(stepctx.steps_for(state, "s-010")))
        self.assertEqual("recovery", stepctx.current_step(state, "s-010")["reason"])


class CloseUnderLockTest(unittest.TestCase):
    """D-40: the identity is re-checked under `state.lock`, immediately before the write."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = make_task(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)

    def test_a_stale_attempt_cannot_close_the_current_one(self):
        issue_step(self.work_dir, "s-010", 1)
        issue_step(self.work_dir, "s-010", 2)
        with self.assertRaises(stepctx.IdentityMismatch) as caught:
            stepctx.close_step(self.work_dir, "s-010", 1, {"stale": True})
        self.assertEqual("stale_attempt", caught.exception.reason)
        self.assertEqual(["identity_mismatch"], caught.exception.as_result()["errors"])
        state = state_io.read_state(self.work_dir)
        self.assertTrue(all(row["status"] is None for row in stepctx.steps_for(state, "s-010")))

    def test_an_attempt_issued_after_the_command_started_wins(self):
        """The classic race: the command checked `a1`, `next` reissued as `a2`, the commit loses."""
        issue_step(self.work_dir, "s-010", 1)
        state = state_io.read_state(self.work_dir)
        self.assertEqual(stepctx.STATUS_OK, stepctx.check_identity(state, "s-010", 1)["status"])
        issue_step(self.work_dir, "s-010", 2)
        with self.assertRaises(stepctx.IdentityMismatch):
            stepctx.close_step(self.work_dir, "s-010", 1, {"late": True})

    def test_close_with_other_arguments_is_refused(self):
        stepctx.close_step(self.work_dir, "s-010", 1, {"value": 1}, args_key="draft=v1.md")
        with self.assertRaises(stepctx.IdentityMismatch):
            stepctx.close_step(self.work_dir, "s-010", 1, {"value": 2}, args_key="draft=v2.md")


class ReadPublishedTest(unittest.TestCase):
    """D-41: consumers read canonical files through `published[]`, not straight off disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = make_task(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)
        work_file = stepctx.stage_result(self.work_dir, "s-010", 1, "currency.json", b'{"sources": []}')
        stepctx.publish(self.work_dir, "s-010", 1, work_file, "research/currency.json")

    def test_a_published_file_is_returned_parsed(self):
        self.assertEqual({"sources": []}, stepctx.read_published(self.work_dir, "research/currency.json"))

    def test_a_file_changed_after_publication_is_refused(self):
        (self.work_dir / "research" / "currency.json").write_text('{"sources": [1]}', encoding="utf-8")
        with self.assertRaises(stepctx.OutputModifiedAfterPublish) as caught:
            stepctx.read_published(self.work_dir, "research/currency.json")
        self.assertEqual("research/currency.json", caught.exception.canonical_path)
        self.assertEqual(
            "output_modified_after_publish", stepctx.drift_result(caught.exception)["errors"][0]
        )

    def test_a_never_published_file_is_read_as_is(self):
        (self.work_dir / "research" / "sources.json").write_text('{"sources": {}}', encoding="utf-8")
        self.assertEqual({"sources": {}}, stepctx.read_published(self.work_dir, "research/sources.json"))


class ReplayTest(unittest.TestCase):
    """§2.2 «anchor replace -> crash -> resume»: the replay recomputes from the saved input (D-42)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = make_task(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def transform(text: str) -> bytes:
        """Stand-in for `draft anchor`: a deterministic function of the draft bytes."""
        return "\n".join(f"<!-- §s-{n} -->{line}" for n, line in enumerate(text.splitlines(), 1)).encode()

    def run_command(self, attempt: int, *, crash: bool) -> bytes:
        draft = self.work_dir / "drafts" / "v1.md"
        staged = stepctx.stage_input(self.work_dir, "s-anchor", attempt, draft)
        source = (self.work_dir / staged).read_text(encoding="utf-8")
        payload = self.transform(source)
        work_file = stepctx.stage_result(self.work_dir, "s-anchor", attempt, "v1.md", payload)
        entry = stepctx.publish_file(self.work_dir, work_file, "drafts/v1.md", step_id="s-anchor")
        if crash:  # the canonical file is replaced, the state write never happens (§2.2)
            return payload
        stepctx.close_step(self.work_dir, "s-anchor", attempt, {"anchored": True}, published=[entry])
        return payload

    def test_an_external_edit_between_crash_and_resume_is_discarded(self):
        (self.work_dir / "drafts" / "v1.md").write_text("# Memo\nBody\n", encoding="utf-8")
        issue_step(self.work_dir, "s-anchor", 1)
        expected = self.run_command(1, crash=True)

        (self.work_dir / "drafts" / "v1.md").write_text("tampered by hand\n", encoding="utf-8")
        replayed = self.run_command(1, crash=False)

        self.assertEqual(expected, replayed, "the replay recomputes from the saved input")
        canonical = self.work_dir / "drafts" / "v1.md"
        self.assertEqual(expected, canonical.read_bytes(), "the recomputed result is republished")
        state = state_io.read_state(self.work_dir)
        self.assertEqual(state_io.sha256_file(canonical), stepctx.published_sha(state, "drafts/v1.md"))
        self.assertIsNone(stepctx.verify_published(self.work_dir, state, "drafts/v1.md"))

    def test_a_replay_after_a_clean_crash_closes_the_step_with_the_same_bytes(self):
        (self.work_dir / "drafts" / "v1.md").write_text("# Memo\nBody\n", encoding="utf-8")
        issue_step(self.work_dir, "s-anchor", 1)
        expected = self.run_command(1, crash=True)
        replayed = self.run_command(1, crash=False)
        self.assertEqual(expected, replayed)
        state = state_io.read_state(self.work_dir)
        self.assertEqual("ok", stepctx.current_step(state, "s-anchor")["status"])


if __name__ == "__main__":
    unittest.main()
