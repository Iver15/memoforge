"""Tests for scripts/memoforge/render.py — deterministic markdown views (ТЗ §5.1, M4, §9)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import modes, render, state_io, task  # noqa: E402

TESTS = Path(__file__).resolve().parent
SCHEMA_FIXTURES = TESTS / "fixtures" / "schemas"
SUFFICIENCY_FIXTURES = TESTS / "fixtures" / "sufficiency"
TASK_ID = "memo-20260908T120000Z-fixture"

# view -> (fixture file, canonical input path, expected output path)
INPUTS = {
    "research": (SCHEMA_FIXTURES / "research-findings/valid-1.json", "research/statutes.json", "research/statutes.md"),
    "currency": (SCHEMA_FIXTURES / "currency/valid-1.json", "research/currency.json", "research/currency.md"),
    "source-pack": (
        SCHEMA_FIXTURES / "source-pack/valid-1.json",
        "research/source-pack.json",
        "research/source-pack.md",
    ),
    "mediator": (SCHEMA_FIXTURES / "mediator/valid-1.json", "reviews/v1-mediator.json", "reviews/v1-mediator.md"),
    "sufficiency": (
        SUFFICIENCY_FIXTURES / "followup-user.json",
        "research/research-sufficiency.json",
        "research/research-sufficiency.md",
    ),
}
"""D34-23: no `summary` row — `summary.md` has one writer, `finalize.write_summary` (§2.1 row 16)."""


def new_task(root: Path) -> Path:
    work_dir = root / TASK_ID
    task.create_work_dir_tree(work_dir)
    state = task.build_initial_state(
        task_id=TASK_ID,
        user_query="Biometric data of minors under the GDPR",
        language="en",
        work_dir=work_dir,
        output_folder=root,
        config=modes.resolve_config("full"),
    )
    state["mode"] = "full"
    state["current_phase"] = "export"
    state["current_iteration"] = 1
    state["current_draft_path"] = "drafts/v1.md"
    state_io.create_state(work_dir, state)
    return work_dir


def stage(work_dir: Path, view: str) -> None:
    source, target, _ = INPUTS[view]
    if source is None:
        return
    destination = work_dir / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


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


def publish(work_dir: Path, canonical: str) -> None:
    """Record the file at `canonical` in `published[]` with its current sha (§2.2)."""
    from memoforge import stepctx

    entry = {
        "canonical_path": canonical,
        "sha256": state_io.sha256_file(work_dir / canonical),
        "by": "step",
        "step_id": "s-100",
        "at": "2026-09-08T12:00:00.000Z",
    }
    state_io.write_state(work_dir, lambda state: stepctx.merge_published(state, [entry]))


def render_args(work_dir: Path, view: str, **overrides) -> argparse.Namespace:
    base = {
        "workdir": str(work_dir),
        "view": view,
        "layer": None,
        "iteration": None,
        "out": None,
        "print_markdown": True,
        "step": None,
        "attempt": 1,
        "human": False,
    }
    base.update(overrides)
    args = argparse.Namespace(**base)
    if args.step:
        issue_step(work_dir, args.step, int(args.attempt or 1))
    return args


class ViewTest(unittest.TestCase):
    def test_every_view_renders_from_its_fixture(self):
        for view, (_, _, expected) in INPUTS.items():
            with self.subTest(view=view), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp))
                stage(work_dir, view)
                result = render.run_render(render_args(work_dir, view))
                self.assertEqual([], result.get("errors", []))
                self.assertEqual(1, result["count"])
                self.assertEqual(expected, result["outputs"][0]["path"])
                text = (work_dir / expected).read_text(encoding="utf-8")
                self.assertTrue(text.startswith("# "), text[:40])
                self.assertTrue(text.endswith("\n"))
                self.assertNotIn("\r", text)
                self.assertEqual(
                    state_io.sha256_file(work_dir / expected), result["outputs"][0]["sha256"]
                )

    def test_every_view_is_covered(self):
        self.assertEqual(sorted(render.VIEWS), sorted(INPUTS))

    def test_research_view_renders_every_present_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "research")
            case_law = json.loads((work_dir / "research/statutes.json").read_text(encoding="utf-8"))
            case_law["layer"] = "case_law"
            state_io.write_json_atomic(work_dir / "research/case_law.json", case_law)

            result = render.run_render(render_args(work_dir, "research"))
            self.assertEqual(2, result["count"])
            self.assertEqual(
                ["research/case_law.md", "research/statutes.md"],
                sorted(row["path"] for row in result["outputs"]),
            )

            single = render.run_render(render_args(work_dir, "research", layer="statutes"))
            self.assertEqual(1, single["count"])

    def test_research_view_carries_findings_and_contrary_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "research")
            result = render.run_render(render_args(work_dir, "research"))
            text = result["markdown"]["research/statutes.md"]
            self.assertIn("# Research — statutes", text)
            self.assertIn("gdpr-art-6", text)
            self.assertIn("Art. 6(1)(f)", text)
            self.assertIn("### Contrary points", text)
            self.assertIn("Considered and excluded", text)

    def test_currency_view_lists_blocking_and_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            text = render.run_render(render_args(work_dir, "currency"))["markdown"]["research/currency.md"]
            self.assertIn("## Blocking", text)
            self.assertIn("smith-v-acme", text)
            self.assertIn("outdated_but_usable", text)

    def test_source_pack_view_shows_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "source-pack")
            text = render.run_render(render_args(work_dir, "source-pack"))["markdown"][
                "research/source-pack.md"
            ]
            self.assertIn("## Snapshot", text)
            self.assertIn("raw_sha256 none", text)  # a source without a stored raw body
            self.assertIn("do_not_use", text)

    def test_mediator_view_shows_instructions_and_dropped_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "mediator")
            text = render.run_render(render_args(work_dir, "mediator"))["markdown"]["reviews/v1-mediator.md"]
            self.assertIn("# Revision instructions — iteration 1", text)
            self.assertIn("deterministic", text)
            self.assertIn("## Dropped", text)
            self.assertIn("Form-only and minor", text)

    def test_sufficiency_view_shows_gaps_and_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "sufficiency")
            text = render.run_render(render_args(work_dir, "sufficiency"))["markdown"][
                "research/research-sufficiency.md"
            ]
            self.assertIn("Verdict: targeted_followup_needed", text)
            self.assertIn("special-category data", text)
            self.assertIn("## Follow-up questions", text)

    def test_there_is_no_summary_view(self):
        """D34-23: `summary.md` had two renderers; `finalize.write_summary` is the surviving one."""
        self.assertNotIn("summary", render.VIEWS)
        self.assertFalse(hasattr(render, "render_summary"))

    def test_missing_input_is_a_business_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            result = render.run_render(render_args(work_dir, "currency"))
            self.assertEqual(["missing_input: research/currency.json"], result["errors"])

    def test_out_overrides_the_target_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            result = render.run_render(render_args(work_dir, "currency", out="research/other.md"))
            self.assertEqual("research/other.md", result["outputs"][0]["path"])
            self.assertTrue((work_dir / "research/other.md").is_file())


class DeterminismTest(unittest.TestCase):
    def test_rendering_twice_gives_byte_identical_output(self):
        for view in render.VIEWS:
            with self.subTest(view=view), tempfile.TemporaryDirectory() as tmp:
                work_dir = new_task(Path(tmp))
                stage(work_dir, view)
                first = render.run_render(render_args(work_dir, view))
                payload = (work_dir / first["outputs"][0]["path"]).read_bytes()
                second = render.run_render(render_args(work_dir, view))
                self.assertEqual(first["outputs"][0]["sha256"], second["outputs"][0]["sha256"])
                self.assertEqual(payload, (work_dir / second["outputs"][0]["path"]).read_bytes())

    def test_two_work_dirs_render_the_same_input_identically(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            shas = []
            for root in (Path(one), Path(two)):
                work_dir = new_task(root)
                stage(work_dir, "source-pack")
                shas.append(render.run_render(render_args(work_dir, "source-pack"))["outputs"][0]["sha256"])
            self.assertEqual(shas[0], shas[1])


class StepTest(unittest.TestCase):
    def test_step_publishes_through_the_attempt_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            result = render.run_render(render_args(work_dir, "currency", step="s-300", attempt=1))
            staged = work_dir / "steps/s-300/a1/cli/currency.md"
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), (work_dir / "research/currency.md").read_bytes())

            state = state_io.read_state(work_dir)
            published = [row for row in state["published"] if row["canonical_path"] == "research/currency.md"]
            self.assertEqual(1, len(published))
            self.assertEqual(result["outputs"][0]["sha256"], published[0]["sha256"])
            closed = [row for row in state["steps"] if row["step_id"] == "s-300"]
            self.assertEqual(1, len(closed))
            self.assertEqual("ok", closed[0]["status"])

    def test_repeat_of_a_closed_step_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            first = render.run_render(render_args(work_dir, "currency", step="s-300"))
            again = render.run_render(render_args(work_dir, "currency", step="s-300"))
            self.assertEqual(first, again)

    def test_a_step_that_was_never_issued_is_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            result = render.run_render(
                argparse.Namespace(
                    workdir=str(work_dir),
                    view="currency",
                    layer=None,
                    iteration=None,
                    out=None,
                    print_markdown=False,
                    step="s-never-issued",
                    attempt=1,
                    human=False,
                )
            )
            self.assertEqual(["identity_mismatch"], result["errors"])
            self.assertFalse((work_dir / "research/currency.md").exists())


class PublishedInputTest(unittest.TestCase):
    """D-41: a view is rendered from the published bytes, never from a file edited afterwards."""

    def test_a_modified_published_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            publish(work_dir, "research/currency.json")
            document = json.loads((work_dir / "research/currency.json").read_text(encoding="utf-8"))
            document["sources"] = []
            (work_dir / "research/currency.json").write_text(json.dumps(document), encoding="utf-8")

            result = render.run_render(render_args(work_dir, "currency", step="s-300"))

            self.assertEqual(["output_modified_after_publish"], result["errors"])
            self.assertEqual("research/currency.json", result["path"])
            self.assertFalse((work_dir / "research/currency.md").exists())

    def test_an_untouched_published_input_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = new_task(Path(tmp))
            stage(work_dir, "currency")
            publish(work_dir, "research/currency.json")
            result = render.run_render(render_args(work_dir, "currency", step="s-300"))
            self.assertEqual(1, result["count"])


if __name__ == "__main__":
    unittest.main()
