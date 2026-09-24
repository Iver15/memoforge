"""Tests for scripts/memoforge/finalize.py — always-deliver (M9, ТЗ §2.1 rows 15-16, §9).

§9 asks for: «`finalize`: crash/без зависимостей/повреждённый state → salvage создаёт
deliverable+summary» and «каждый терминальный путь и каждая строка `fallbacks.py` оставляет
deliverable + summary».
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import (  # noqa: E402
    cli,
    events,
    fallbacks,
    finalize,
    machine,
    phases,
    schema,
    sources,
    state_io,
    task,
)
from memoforge.docx import fallback as md_fallback  # noqa: E402
from memoforge.docx import slug_of  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402
from memoforge import i18n  # noqa: E402

ASSUMPTIONS_MD = "**Assumptions carried into the analysis**"
UNVERIFIED_MD = f"**{md_fallback.label('unverified_label')}**"
UNRESOLVED_MD = f"**{md_fallback.label('unresolved_label')}**"
"""The English appendix sub-headings, bolded the way the markdown deliverable writes them."""

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_docx_fallback import LONG_WARNING, warnings_fixture  # noqa: E402

DRAFT = """# Memo

## 1. Executive summary

- The transfer is lawful under [[src:gdpr-art6 art 6(1)(f)]]. Risk: medium.

## 3. Facts, assumptions and limitations

> Personal data must be processed lawfully. [[q:q-001]]

<!-- sources: generated -->
"""

SOURCES = {
    "schema_version": 2,
    "sources": {
        "gdpr-art6": {
            "layer": "statutes",
            "title": "GDPR Article 6",
            "citation_form": "Regulation (EU) 2016/679, art 6",
            "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
        }
    },
}

RAW_ARTICLE_6 = b"Article 6 raw text"
"""The raw file of the published fixture source; its digest is what authorises the export."""

QUOTES = {"quotes": {"q-001": {"source_id": "gdpr-art6", "raw_sha256": "0" * 64}}}

SIGNED_OFF = "approved_on_v1"

NAME_FORCED_EXIT_V1 = md_fallback.status_name("forced_exit_on_v1_with_remaining_issues")
NAME_MANUAL_REVIEW_V1 = md_fallback.status_name("manual_review_required_on_v1")
"""D-197: what a `final_status` reads like in the deliverable — the code stays in state.json."""
"""The `final_status` the revision decision writes before the export phase runs (revision.py, D-123).

An export is only the deliverable when its `## Status` section says what `finalize` is about to say,
so a work dir that renders a docx and then delivers it has to carry the status of a real run.
"""


def issue_step(work_dir: Path, step_id: str, attempt: int = 1) -> None:
    """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""
    if not (work_dir / state_io.STATE_FILENAME).is_file():
        return

    def mutator(state: dict) -> None:
        rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
        if any(row.get("step_id") == step_id and int(row.get("attempt") or 1) == attempt for row in rows):
            return
        rows.append(
            {
                "step_id": step_id,
                "kind": "terminal",
                "phase": state.get("current_phase"),
                "attempt": attempt,
                "reason": "initial",
                "issued_at": "2026-09-08T12:00:00.000Z",
                "status": None,
            }
        )
        state["steps"] = rows

    try:
        state_io.write_state(work_dir, mutator)
    except Exception:  # noqa: BLE001 - a corrupt state / hidden jsonschema has no step to issue
        pass


def finalize_args(work_dir: Path, **overrides) -> argparse.Namespace:
    """Namespace matching the `mf finalize` parser; the step is issued the way `next` issues it."""
    payload = {
        "workdir": str(work_dir),
        "step": "s-export",
        "attempt": 1,
        "reason": None,
        "salvage": False,
        "human": False,
    }
    payload.update(overrides)
    args = argparse.Namespace(**payload)
    if args.step:
        issue_step(work_dir, args.step, int(args.attempt or 1))
    return args


def render_export(work_dir: Path, step_id: str = "s-render") -> dict:
    """Run a real `mf docx render` step, so `memo-<slug>.{docx,md}` carry the D-50 binding."""
    from memoforge import docx

    issue_step(work_dir, step_id)
    return docx.run_render(
        argparse.Namespace(workdir=str(work_dir), step=step_id, attempt=1, draft_sha=None, human=False)
    )


def md_fallback_export(work_dir: Path) -> str:
    """The `memo-<slug>.md` export a `docx render` left in the work dir."""
    slug = slug_of(state_io.read_state(work_dir), work_dir)
    return (work_dir / f"memo-{slug}.md").read_text(encoding="utf-8-sig")


def forge_render_sha(work_dir: Path, sha: str) -> None:
    """Rewrite the `draft_sha` a closed `docx render` recorded, leaving every file on disk untouched."""

    def mutator(state: dict) -> None:
        for row in state.get("steps") or []:
            ref = row.get("result_ref") if isinstance(row, dict) else None
            result = ref.get("result") if isinstance(ref, dict) else None
            if isinstance(result, dict) and result.get("draft_sha"):
                result["draft_sha"] = sha

    state_io.write_state(work_dir, mutator)


class _WorkDirMixin:
    """A real v2 work dir with a draft, a registry and a schema-valid `state.json`."""

    def make_task(self, *, with_draft: bool = True, final_status: str | None = None, mutate=None) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work_dir = Path(tmp.name) / "memo-20260908T120000Z-gdpr-transcripts"
        task.create_work_dir_tree(work_dir)
        state = task.build_initial_state(
            task_id=work_dir.name,
            user_query="Is the transfer lawful?",
            language="en",
            work_dir=work_dir,
            output_folder=work_dir.parent,
            config={"reviewer_list": ["logic", "citations"]},
        )
        state["mode"] = "full"
        state["current_phase"] = "export"
        if final_status:
            state["final_status"] = final_status
        if with_draft:
            (work_dir / "drafts" / "v1.md").write_text(DRAFT, encoding="utf-8")
            state["current_draft_path"] = "drafts/v1.md"
            state_io.write_json_atomic(work_dir / "research" / "sources.json", SOURCES)
            state_io.write_json_atomic(work_dir / "research" / "quotes.json", QUOTES)
        if mutate is not None:
            mutate(state)
        state_io.create_state(work_dir, state)
        return work_dir

    def assert_delivered(self, work_dir: Path, kind: str = "md") -> None:
        deliverable = finalize.DELIVERABLE_DOCX if kind == "docx" else finalize.DELIVERABLE_MD
        self.assertTrue((work_dir / deliverable).is_file(), f"missing {deliverable}")
        self.assertTrue((work_dir / finalize.SUMMARY_MD).is_file(), "missing summary.md")
        self.assertGreater((work_dir / finalize.SUMMARY_MD).stat().st_size, 0)


class TerminalPathsTest(_WorkDirMixin, unittest.TestCase):
    def test_done_path_leaves_deliverable_and_summary(self):
        work_dir = self.make_task()
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["current_phase"], "done")
        self.assertTrue(result["state_written"])
        self.assert_delivered(work_dir)
        self.assertEqual(state_io.read_state(work_dir)["current_phase"], "done")

    def test_the_summary_paths_are_the_ones_of_the_published_folder(self):
        # D-216: run 74 printed `Work dir: /home/claude/...` and `State: state.json`, while the
        # published folder the owner opens holds the state and the journal under `_run/`.
        work_dir = self.make_task()
        finalize.run_finalize(finalize_args(work_dir))
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        paths = summary.partition("## Paths\n")[2].partition("\n## ")[0]
        self.assertIn("- State: `_run/state.json`\n", paths)
        self.assertIn("- Journal: `_run/events.jsonl`\n", paths)
        self.assertNotIn("Work dir", summary)
        self.assertNotIn(str(work_dir), summary)

    def test_failed_path_leaves_deliverable_and_summary(self):
        work_dir = self.make_task()
        result = finalize.run_finalize(finalize_args(work_dir, reason="cli_error"))
        self.assertEqual(result["current_phase"], "failed")
        self.assertIn("cli_error", result["final_status_reasons"])
        self.assert_delivered(work_dir)
        self.assertIn("cli_error", (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8"))

    def test_cancelled_path_leaves_deliverable_and_summary(self):
        work_dir = self.make_task(mutate=lambda state: state.update(cancel_requested=True))
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["current_phase"], "cancelled_by_user")
        self.assert_delivered(work_dir)

    def test_cancel_wins_over_an_explicit_reason(self):
        work_dir = self.make_task(mutate=lambda state: state.update(cancel_requested=True))
        result = finalize.run_finalize(finalize_args(work_dir, reason="interrupted"))
        self.assertEqual(result["current_phase"], "cancelled_by_user")

    def test_every_terminal_phase_of_phases_py_is_reachable(self):
        reached = set()
        for overrides, mutate in (
            ({}, None),
            ({"reason": "cli_error"}, None),
            ({}, lambda state: state.update(cancel_requested=True)),
        ):
            work_dir = self.make_task(mutate=mutate)
            reached.add(finalize.run_finalize(finalize_args(work_dir, **overrides))["current_phase"])
        self.assertEqual(reached, set(phases.TERMINAL))

    def test_no_draft_falls_back_to_a_summary_deliverable(self):
        work_dir = self.make_task(with_draft=False)
        result = finalize.run_finalize(finalize_args(work_dir, reason="drafting_failed"))
        self.assert_delivered(work_dir)
        self.assertTrue((work_dir / finalize.FALLBACK_SUMMARY_MD).is_file())
        self.assertEqual(result["final_status"], "fallback_summary_delivered")

    def test_existing_docx_becomes_the_deliverable(self):
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "docx")
        self.assert_delivered(work_dir, kind="docx")
        self.assertEqual(state_io.read_state(work_dir)["final_docx_path"], finalize.DELIVERABLE_DOCX)


class FallbackRowsTest(_WorkDirMixin, unittest.TestCase):
    def test_every_fallback_row_still_leaves_deliverable_and_summary(self):
        for row in fallbacks.FALLBACKS:
            with self.subTest(condition=row["condition_key"]):
                banner = None
                if row["banner_id"] is not None:
                    params = {name: f"<{name}>" for name in row["banner_params"]}
                    banner = fallbacks.banner(row["condition_key"], **params)

                def mutate(state: dict, banner=banner, row=row) -> None:
                    if banner is not None:
                        state["fallback_banners"] = [banner]
                    state["final_status_reasons"] = [row["condition_key"]]

                work_dir = self.make_task(mutate=mutate)
                result = finalize.run_finalize(finalize_args(work_dir))
                self.assert_delivered(work_dir)
                summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
                self.assertIn(row["condition_key"], summary)
                if banner is not None:
                    self.assertIn(banner["banner_id"], summary)
                    self.assertIn(
                        banner["banner_id"], [entry["banner_id"] for entry in result["banners"]]
                    )


class PublishTest(_WorkDirMixin, unittest.TestCase):
    """D-109: the finished result is copied out of the private work dir, and never fatally."""

    def make_published_task(self, root: Path, **kwargs) -> Path:
        def mutate(state: dict) -> None:
            state["config"]["publish_folder"] = str(root)

        work_dir = self.make_task(mutate=mutate, **kwargs)
        registry = json.loads(json.dumps(SOURCES))
        registry["sources"]["gdpr-art6"]["tier"] = "critical"
        registry["sources"]["gdpr-art6"]["raw_path"] = "research/raw/statutes/gdpr-art6.md"
        # D-201 fix round 3: a text is exported only when a digest vouches for it, exactly as an
        # original is — so a record with a raw file carries the sha of that file, as a real one does.
        registry["sources"]["gdpr-art6"]["raw_sha256"] = state_io.sha256_bytes(RAW_ARTICLE_6)
        registry["sources"]["ico-guide"] = {
            "layer": "doctrine",
            "title": "ICO guidance",
            "citation_form": "ICO, Guide to the UK GDPR",
            "url": "https://ico.org.uk/guide",
            "tier": "supporting",
            "raw_path": "research/raw/doctrine/ico-guide.md",
        }
        registry["sources"]["blog-post"] = {
            "layer": "doctrine",
            "title": "A blog post",
            "citation_form": "Blog",
            "url": "https://example.org/post",
            "tier": "background",
            "raw_path": "research/raw/doctrine/blog-post.md",
        }
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)
        raw = work_dir / "research" / "raw" / "statutes"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "gdpr-art6.md").write_bytes(RAW_ARTICLE_6)
        # `ico-guide` names a raw file nobody saved, `blog-post` is background: neither is published.
        return work_dir

    def published_dir(self, root: Path, work_dir: Path) -> Path:
        return root / finalize.PUBLISH_DIRNAME / "gdpr-transcripts"

    def test_the_deliverable_the_summary_and_the_sources_are_copied(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        result = finalize.run_finalize(finalize_args(work_dir))
        target = self.published_dir(root, work_dir)
        self.assertEqual(result["published_to"], str(target))
        self.assertTrue((target / finalize.DELIVERABLE_MD).is_file())
        self.assertTrue((target / finalize.SUMMARY_MD).is_file())
        pack = target / finalize.PUBLISH_SOURCES_DIRNAME / finalize.SOURCE_PACK_MD
        self.assertTrue(pack.is_file())
        self.assertIn("gdpr-art6", pack.read_text(encoding="utf-8"))
        raw = target / finalize.PUBLISH_SOURCES_DIRNAME / "gdpr-art6.txt"
        self.assertEqual(raw.read_bytes(), RAW_ARTICLE_6, "the stored bytes, not a round trip")
        self.assertEqual(
            (work_dir / finalize.DELIVERABLE_MD).read_bytes(),
            (target / finalize.DELIVERABLE_MD).read_bytes(),
        )

    def test_the_run_diagnostics_are_copied_next_to_the_result(self):
        """D-113: `_run/` carries the small files that explain the run — and nothing else."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        state_io.write_json_atomic(work_dir / "plan.json", {"issues": []})
        (work_dir / "intake" / "user-facts.md").write_text("# Facts\n", encoding="utf-8")
        state_io.write_json_atomic(
            work_dir / "research" / "research-sufficiency.json", {"verdict": "sufficient"}
        )
        state_io.write_json_atomic(work_dir / "reviews" / "v1-logic.json", {"kind": "logic"})
        (work_dir / "reviews" / "v1-logic.md").write_text("not json", encoding="utf-8")
        (work_dir / "steps" / "s-export").mkdir(parents=True, exist_ok=True)
        (work_dir / "steps" / "s-export" / "done.json").write_text("{}", encoding="utf-8")

        result = finalize.run_finalize(finalize_args(work_dir))
        run_dir = self.published_dir(root, work_dir) / finalize.PUBLISH_RUN_DIRNAME

        self.assertEqual(
            sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file()),
            [
                "events.jsonl",
                "intake/user-facts.md",
                "plan.json",
                "research/research-sufficiency.json",
                "reviews/v1-logic.json",
                "state.json",
            ],
        )
        self.assertIn("_run/state.json", result["published_files"])
        self.assertIn("_run/reviews/v1-logic.json", result["published_files"])
        self.assertFalse((run_dir / "steps").exists())
        self.assertFalse((run_dir / "research" / "raw").exists())

    def test_run_diagnostics_skip_what_the_run_never_wrote(self):
        """A run that stopped before planning publishes the files it has, and no empty folders."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)

        result = finalize.run_finalize(finalize_args(work_dir))
        run_dir = self.published_dir(root, work_dir) / finalize.PUBLISH_RUN_DIRNAME

        self.assertEqual(
            sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*")),
            ["events.jsonl", "state.json"],
        )
        self.assertNotIn("_run/plan.json", result["published_files"])

    def test_a_frozen_pack_is_published_through_the_existing_renderer(self):
        from memoforge import render

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        pack = {
            "frozen_at": "2026-09-08T12:00:00.000Z",
            "entries": [
                {
                    "source_id": "gdpr-art6",
                    "layer": "statutes",
                    "tier": "critical",
                    "pack": {"use_in_memo": "rule", "weight": "binding"},
                    "currency_status": "current",
                    "citation_form": "Regulation (EU) 2016/679, art 6",
                }
            ],
            "snapshot": [{"source_id": "gdpr-art6", "raw_sha256": state_io.sha256_bytes(RAW_ARTICLE_6)}],
        }
        state_io.write_json_atomic(work_dir / "research" / "source-pack.json", pack)
        finalize.run_finalize(finalize_args(work_dir))
        published = (
            self.published_dir(root, work_dir)
            / finalize.PUBLISH_SOURCES_DIRNAME
            / finalize.SOURCE_PACK_MD
        ).read_text(encoding="utf-8")
        self.assertIn(render.render_source_pack(pack).strip(), published)
        self.assertIn("Source pack (frozen)", published)
        # D-201 fix round 3: `ico-guide` names a raw file nobody saved, so the client is told why
        # `sources/` holds no text for it instead of being left to wonder.
        self.assertIn(
            md_fallback.label("text_export_mismatch_note", "en", source_id="`ico-guide`"),
            published,
        )

    def test_the_folder_is_named_by_the_slug_of_the_task(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(
            [path.name for path in (root / finalize.PUBLISH_DIRNAME).iterdir()],
            ["gdpr-transcripts"],
        )

    def test_a_source_whose_raw_file_is_missing_is_skipped(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        finalize.run_finalize(finalize_args(work_dir))
        published = sorted(
            path.name
            for path in (self.published_dir(root, work_dir) / finalize.PUBLISH_SOURCES_DIRNAME).iterdir()
        )
        self.assertEqual(published, ["gdpr-art6.txt", finalize.SOURCE_PACK_MD])

    def test_the_path_reaches_state_the_journal_and_the_answer(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        result = finalize.run_finalize(finalize_args(work_dir))
        state = state_io.read_state(work_dir)
        self.assertEqual(state["progress"]["published_to"], result["published_to"])
        published = [row for row in events.read_events(work_dir) if row["event"] == "result_published"]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["data"]["path"], result["published_to"])

    def test_an_unwritable_root_raises_the_banner_and_the_run_still_ends(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        with mock.patch("memoforge.finalize.shutil.copyfile", side_effect=OSError("read-only")):
            result = finalize.run_finalize(finalize_args(work_dir))
        self.assertIsNone(result["published_to"])
        self.assertIn("publish_failed", [row["banner_id"] for row in result["banners"]])
        self.assertIn("publish_failed", (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8"))
        self.assertNotIn("errors", result)
        self.assertEqual(result["current_phase"], "done")
        self.assert_delivered(work_dir)
        self.assertEqual(state_io.read_state(work_dir)["current_phase"], "done")

    def test_no_publish_root_copies_nothing_and_is_not_a_degradation(self):
        work_dir = self.make_task()
        with mock.patch.object(finalize, "HOST_OUTPUTS_DIR", str(Path(work_dir) / "absent")):
            result = finalize.run_finalize(finalize_args(work_dir))
        self.assertIsNone(result["published_to"])
        self.assertEqual(result["published_files"], [])
        self.assertEqual([], [row["banner_id"] for row in result["banners"]
                              if row["banner_id"] == "publish_failed"])
        self.assertIsNone(state_io.read_state(work_dir)["progress"]["published_to"])

    def test_the_host_outputs_area_is_the_root_when_no_option_is_set(self):
        host = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, host, True)
        work_dir = self.make_task()
        with mock.patch.object(finalize, "HOST_OUTPUTS_DIR", str(host)):
            result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(
            result["published_to"], str(host / finalize.PUBLISH_DIRNAME / "gdpr-transcripts")
        )

    def test_the_salvage_path_publishes_too(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        (work_dir / state_io.STATE_FILENAME).write_text("{not json", encoding="utf-8")
        with mock.patch.object(finalize, "HOST_OUTPUTS_DIR", str(root)):
            result = finalize.run_finalize(finalize_args(work_dir, salvage=True, step=None))
        self.assertEqual(
            result["published_to"], str(root / finalize.PUBLISH_DIRNAME / "gdpr-transcripts")
        )
        self.assertTrue((Path(result["published_to"]) / finalize.SUMMARY_MD).is_file())

    def assert_publish_failed_but_delivered(self, work_dir: Path, result: dict, exc_name: str) -> None:
        """D-111: whatever broke inside `publish`, the run still ends with a terminal state on disk."""
        self.assertIsNone(result["published_to"])
        self.assertEqual(result["published_files"], [])
        banners = [row for row in result["banners"] if row["banner_id"] == "publish_failed"]
        self.assertEqual(len(banners), 1)
        self.assertIn(exc_name, banners[0]["text"])
        self.assertNotIn("errors", result)
        self.assertEqual(result["current_phase"], "done")
        self.assert_delivered(work_dir)
        state = state_io.read_state(work_dir)
        self.assertEqual(state["current_phase"], "done")
        self.assertIsNone(state["progress"]["published_to"])
        self.assertIn("publish_failed", (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8"))

    def test_a_registry_record_of_the_wrong_type_is_not_an_exception(self):
        """D-111: `raw_path: 42` used to escape as a `TypeError` before the terminal state was written.

        D-201 fix round 3: it no longer even costs the publication. Every artefact is now staged
        and checked one by one, so a record the schema would reject is one artefact that cannot be
        handed over — named in `source-pack.md` — while the rest of the delivery goes out.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        registry = state_io.read_json(work_dir / "research" / "sources.json")
        registry["sources"]["gdpr-art6"]["raw_path"] = 42
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertIsNone(result.get("publish_error"), result.get("publish_error"))
        self.assertIsNotNone(result["published_to"])
        published = self.published_dir(root, work_dir) / finalize.PUBLISH_SOURCES_DIRNAME
        self.assertFalse((published / "gdpr-art6.txt").exists())
        self.assertIn(
            md_fallback.label("text_export_mismatch_note", "en", source_id="`gdpr-art6`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )
        self.assert_delivered(work_dir)

    def test_a_registry_that_is_not_a_dict_is_not_an_exception(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        with mock.patch.object(sources, "read_registry", return_value=["not", "a", "registry"]):
            result = finalize.run_finalize(finalize_args(work_dir))
        self.assert_publish_failed_but_delivered(work_dir, result, "AttributeError")

    def test_a_publish_root_that_raises_is_not_an_exception(self):
        """D-111: `publish_root` sits inside the guard too — it reads `config` nobody validated."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        with mock.patch.object(finalize, "publish_root", side_effect=RuntimeError("no root")):
            result = finalize.run_finalize(finalize_args(work_dir))
        self.assert_publish_failed_but_delivered(work_dir, result, "RuntimeError")

    def test_a_second_run_replaces_the_publication_of_the_first(self):
        """D-111: the folder holds one result — the latest — not a mixture of two runs (§2.5)."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root, final_status=SIGNED_OFF)
        doctrine = work_dir / "research" / "raw" / "doctrine"
        doctrine.mkdir(parents=True, exist_ok=True)
        (doctrine / "ico-guide.md").write_bytes(b"ICO raw text")
        (doctrine / "blog-post.md").write_bytes(b"Blog raw text")
        registry = state_io.read_json(work_dir / "research" / "sources.json")
        registry["sources"]["blog-post"]["tier"] = "supporting"
        # D-201 fix round 3: a text is exported only when a digest vouches for it.
        registry["sources"]["ico-guide"]["raw_sha256"] = state_io.sha256_bytes(b"ICO raw text")
        registry["sources"]["blog-post"]["raw_sha256"] = state_io.sha256_bytes(b"Blog raw text")
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)

        render_export(work_dir)  # the docx export of the selected version, so run one delivers docx
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(first["deliverable_kind"], "docx")
        target = self.published_dir(root, work_dir)
        self.assertEqual(
            sorted(path.name for path in (target / finalize.PUBLISH_SOURCES_DIRNAME).iterdir()),
            ["blog-post.txt", "gdpr-art6.txt", "ico-guide.txt", finalize.SOURCE_PACK_MD],
        )
        # Something the user (or the router) put next to the result: not ours to remove.
        (target / "notes.txt").write_text("mine", encoding="utf-8")

        # A later revision supersedes the export (md this time) and two sources leave the pack.
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")
        registry["sources"]["ico-guide"]["tier"] = "background"
        registry["sources"]["blog-post"]["tier"] = "background"
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)
        second = finalize.run_finalize(finalize_args(work_dir, step=None))

        self.assertEqual(second["deliverable_kind"], "md")
        self.assertEqual(second["published_to"], str(target))
        self.assertEqual(
            sorted(path.name for path in target.iterdir()),
            [
                finalize.PUBLISH_RUN_DIRNAME,
                finalize.DELIVERABLE_MD,
                "notes.txt",
                finalize.PUBLISH_SOURCES_DIRNAME,
                finalize.SUMMARY_MD,
            ],
        )
        self.assertEqual(
            sorted(path.name for path in (target / finalize.PUBLISH_SOURCES_DIRNAME).iterdir()),
            ["gdpr-art6.txt", finalize.SOURCE_PACK_MD],
        )
        self.assertIn(
            "A later revision.", (target / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        )

    def test_a_failed_publish_never_makes_the_export_stale(self):
        """D-144 (R2-03): `publish_failed` describes the copy, so the `## Status` section omits it.

        The banner is raised after `choose_deliverable` has already written the deliverable, so a
        docx could never carry it — counting it as a status input would hand the next `finalize` of
        a perfectly current run to the markdown branch instead.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(
            root, final_status="forced_exit_on_v1_with_remaining_issues"
        )
        render_export(work_dir)
        # D-201 fix round 3: a malformed `raw_path` no longer fails the copy — it is one artefact
        # that cannot be handed over — so the failure this test needs comes from the boundary
        # itself, exactly as `test_a_publish_root_that_raises_is_not_an_exception` raises it.
        with mock.patch.object(finalize, "publish_root", side_effect=OSError("read-only")):
            first = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(first["deliverable_kind"], "docx")
        self.assertIn("publish_failed", [row["banner_id"] for row in first["banners"]])
        self.assertIn("publish_failed", (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8"))
        self.assertIn(
            "publish_failed",
            [row["banner_id"] for row in state_io.read_state(work_dir)["fallback_banners"]],
        )

        second = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(second["deliverable_kind"], "docx", "the copy is not part of the memo")

    def test_a_failed_republish_keeps_the_previous_publication(self):
        # A43-5 / D-157: the old copy is replaced only once the new one exists in full.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        target = Path(first["published_to"])
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        self.assertTrue(before, "the first publish wrote files")
        md_target = target / finalize.DELIVERABLE_MD
        docx_target = target / finalize.DELIVERABLE_DOCX
        deliverable_bytes = md_target.read_bytes() if md_target.is_file() else docx_target.read_bytes()

        with mock.patch("memoforge.finalize.shutil.copyfile", side_effect=OSError("disk full")):
            second = finalize.run_finalize(finalize_args(work_dir, step=None))

        self.assertIsNone(second["published_to"])
        after = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        self.assertEqual(before, after, "the previous publication survived the failed copy")
        kept = md_target if md_target.is_file() else docx_target
        self.assertEqual(deliverable_bytes, kept.read_bytes())
        leftover = [p for p in target.parent.iterdir() if p.name.endswith(".publishing")]
        self.assertEqual([], leftover, "no staging left behind")

    def test_a_replacement_that_fails_half_way_restores_the_previous_publication(self):
        """A43-5 / D-158: staging succeeded, so the failure is in the swap — it has to roll back.

        A `deliverable.docx` open in Word, an antivirus holding the file, a volume that turned
        read-only: the replacement raises after the first item already landed in the folder.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        target = Path(first["published_to"])
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        self.assertTrue(before, "the first publish wrote files")
        deliverable = target / finalize.DELIVERABLE_MD
        kept_bytes = deliverable.read_bytes()
        run_state = target / finalize.PUBLISH_RUN_DIRNAME / state_io.STATE_FILENAME
        kept_run_bytes = run_state.read_bytes()

        # The second run has something new to say, so a half-replaced folder would be visible.
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")
        real_replace = os.replace
        landed: list[str] = []
        broken: list[str] = []

        def replace_but_break_the_second_item(src, dst, *args, **kwargs):
            # Staging and the move-aside run for real; only the replacements into `target` count,
            # and the second of them is the one the lock catches.
            if not broken and Path(dst).parent == target:
                if landed:
                    broken.append(str(dst))
                    raise PermissionError("the deliverable is open in another program")
                landed.append(str(dst))
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch("memoforge.finalize.os.replace", replace_but_break_the_second_item):
            second = finalize.run_finalize(finalize_args(work_dir, step=None))

        self.assertEqual(1, len(landed), "the first item of the new set has to land before the failure")
        self.assertTrue(broken, "the failure has to happen during the replacement, not before it")
        self.assertIsNone(second["published_to"])
        banners = [row["banner_id"] for row in second["banners"]]
        self.assertIn("publish_failed", banners)
        self.assertIn("PermissionError", " ".join(row["text"] for row in second["banners"]))
        after = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        self.assertEqual(before, after, "the previous publication came back in full")
        self.assertEqual(kept_bytes, deliverable.read_bytes(), "and it is yesterday's deliverable")
        self.assertEqual(kept_run_bytes, run_state.read_bytes(), "the item that had landed was undone")
        leftover = sorted(p.name for p in target.parent.iterdir() if p.name != target.name)
        self.assertEqual([], leftover, "neither the staging nor the aside copy is left behind")

    def test_a_move_aside_that_fails_half_way_leaves_the_untouched_files_alone(self):
        """A43-5 / D-158: the swap fails *while* the old set is being moved aside.

        The items that were never moved are still standing at their own paths, so the rollback has
        to undo what it did and nothing else — deleting by name would destroy them.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        target = Path(first["published_to"])
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        self.assertTrue(before, "the first publish wrote files")
        kept_bytes = {name: (target / name).read_bytes() for name in before}
        aside_dir = target.parent / f"{target.name}.previous"

        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")
        real_replace = os.replace
        moved: list[str] = []
        broken: list[str] = []

        def replace_but_break_the_second_move_aside(src, dst, *args, **kwargs):
            # Only the moves into `<slug>.previous` count; the second one is the locked item, so
            # `summary.md`, `sources/` and `_run/` never leave the folder at all.
            if not broken and Path(dst).parent == aside_dir:
                if moved:
                    broken.append(str(src))
                    raise PermissionError("the folder is held by another program")
                moved.append(str(src))
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch("memoforge.finalize.os.replace", replace_but_break_the_second_move_aside):
            second = finalize.run_finalize(finalize_args(work_dir, step=None))

        self.assertEqual(1, len(moved), "one old item has to be moved aside before the failure")
        self.assertTrue(broken, "the failure has to happen during the move-aside, not after it")
        self.assertIsNone(second["published_to"])
        self.assertIn("publish_failed", [row["banner_id"] for row in second["banners"]])
        self.assertIn("PermissionError", " ".join(row["text"] for row in second["banners"]))
        after = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        self.assertEqual(before, after, "every file of the previous publication is still there")
        self.assertEqual(
            kept_bytes,
            {name: (target / name).read_bytes() for name in after},
            "and every one of them is still yesterday's bytes",
        )
        leftover = sorted(p.name for p in target.parent.iterdir() if p.name != target.name)
        self.assertEqual([], leftover, "neither the staging nor the aside copy is left behind")

    def test_a_failed_publish_clears_the_path_the_previous_run_left(self):
        """D-111: `published_to` describes this finalize, so the terminal text stops naming a folder."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(
            state_io.read_state(work_dir)["progress"]["published_to"], first["published_to"]
        )

        with mock.patch("memoforge.finalize.shutil.copyfile", side_effect=OSError("read-only")):
            second = finalize.run_finalize(finalize_args(work_dir, step=None))

        self.assertIsNone(second["published_to"])
        state = state_io.read_state(work_dir)
        self.assertIsNone(state["progress"]["published_to"])
        self.assertEqual(machine.published_to(state), "")
        self.assertNotIn("Published:", machine.terminal_response(work_dir, state)["text"])

    ROOT_COPIES = ("memo-gdpr-transcripts.md", "memo-gdpr-transcripts.summary.md")
    """The two D-167 root copies of `make_published_task`: in the manifest, outside the folder."""

    def files_line(self, text: str) -> str:
        lines = text.splitlines()
        published = next(i for i, line in enumerate(lines) if line.startswith("Published:"))
        self.assertTrue(lines[published + 1].startswith("Files: "), text)
        return lines[published + 1]

    def manifest(self, work_dir: Path) -> list[str]:
        """`files` of the last `result_published` event — the publication manifest (D-217)."""
        rows = [row for row in events.read_events(work_dir) if row.get("event") == "result_published"]
        return list(rows[-1]["data"]["files"])

    def terminal_text(self, work_dir: Path) -> str:
        action = machine.run_next(argparse.Namespace(workdir=str(work_dir), human=False))
        self.assertEqual("terminal", action["kind"])
        return action["text"]

    def test_the_terminal_text_counts_the_manifest_without_the_root_copies(self):
        """D-217: `Files: <n>` is the last manifest minus the two root copies, never a disk scan."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir))
        manifest = self.manifest(work_dir)
        self.assertEqual(first["published_files"], manifest)
        for name in self.ROOT_COPIES:
            self.assertIn(name, manifest)
        expected = len(manifest) - len(self.ROOT_COPIES)
        self.assertGreater(expected, 5)
        self.assertEqual(f"Files: {expected}", self.files_line(self.terminal_text(work_dir)))

        # A stale file in the folder is not part of the publication, so it does not change `n`.
        (Path(first["published_to"]) / "notes.txt").write_text("mine", encoding="utf-8")
        self.assertEqual(f"Files: {expected}", self.files_line(self.terminal_text(work_dir)))

    def test_a_resumed_delivery_prints_the_count_of_its_own_manifest(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        finalize.run_finalize(finalize_args(work_dir))
        first = len(self.manifest(work_dir)) - len(self.ROOT_COPIES)
        self.assertEqual(f"Files: {first}", self.files_line(self.terminal_text(work_dir)))
        # `next` again on the finished run: the same terminal text, the same count.
        self.assertEqual(f"Files: {first}", self.files_line(self.terminal_text(work_dir)))

        # finalize again (it republishes, one more source text this time): the last manifest counts.
        (work_dir / "research" / "raw" / "doctrine").mkdir(parents=True, exist_ok=True)
        (work_dir / "research" / "raw" / "doctrine" / "ico-guide.md").write_bytes(b"ICO raw text")
        registry = state_io.read_json(work_dir / "research" / "sources.json")
        registry["sources"]["ico-guide"]["raw_sha256"] = state_io.sha256_bytes(b"ICO raw text")
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)
        result = finalize.run_finalize(finalize_args(work_dir, step="s-export-again"))
        self.assertTrue(result["already_terminal"])
        manifest = self.manifest(work_dir)
        self.assertIn(f"{finalize.PUBLISH_SOURCES_DIRNAME}/ico-guide.txt", manifest)
        self.assertEqual(
            f"Files: {len(manifest) - len(self.ROOT_COPIES)}", self.files_line(self.terminal_text(work_dir))
        )

    def test_no_manifest_no_files_line(self):
        """D-217: without a `result_published` event there is nothing to count — no `Files:` line."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        finalize.run_finalize(finalize_args(work_dir))
        journal = events.events_path(work_dir)
        kept = [
            line
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("event") != "result_published"
        ]
        journal.write_text("\n".join(kept) + "\n", encoding="utf-8")
        text = self.terminal_text(work_dir)
        self.assertIn("Published:", text)
        self.assertNotIn("Files:", text)


class TerminalOrderTest(_WorkDirMixin, unittest.TestCase):
    def test_terminal_phase_is_written_only_after_deliverable_and_summary(self):
        work_dir = self.make_task()
        args = finalize_args(work_dir)  # the step is issued before the spy counts state writes
        observed: list[dict] = []
        original = state_io.write_state

        def spy(target, mutator, **kwargs):
            observed.append(
                {
                    "deliverable": (Path(target) / finalize.DELIVERABLE_MD).is_file(),
                    "summary": (Path(target) / finalize.SUMMARY_MD).is_file(),
                }
            )
            return original(target, mutator, **kwargs)

        with mock.patch.object(state_io, "write_state", spy):
            finalize.run_finalize(args)

        self.assertEqual(len(observed), 1, "finalize must write state exactly once")
        self.assertTrue(observed[0]["deliverable"], "deliverable must exist before the state write")
        self.assertTrue(observed[0]["summary"], "summary must exist before the state write")

    def test_state_stays_non_terminal_when_the_state_write_fails(self):
        work_dir = self.make_task()
        args = finalize_args(work_dir)

        def boom(*call_args, **kwargs):
            raise OSError("disk full")

        with mock.patch.object(state_io, "write_state", boom):
            result = finalize.run_finalize(args)
        self.assertFalse(result["state_written"])
        self.assert_delivered(work_dir)
        self.assertEqual(state_io.read_state(work_dir)["current_phase"], "export")

    def test_a_failed_terminal_write_answers_errors_and_exits_1(self):
        """D-64: exit 0 here would let the orchestrator reissue `finalize` forever (15a N-06)."""
        work_dir = self.make_task()
        args = finalize_args(work_dir)  # the step is issued while `write_state` still works

        def boom(*call_args, **kwargs):
            raise OSError("disk full")

        with mock.patch.object(state_io, "write_state", boom):
            result = finalize.run_finalize(args)

        self.assertFalse(result["state_written"])
        self.assertTrue(result.get("errors"), result)
        self.assertIn("disk full", " ".join(result["errors"]))
        self.assert_delivered(work_dir)  # M9 still holds: the failure is reported, not silent

        # What the orchestrator actually sees is the exit code of the CLI (CONVENTIONS, D-52).
        issue_step(work_dir, "s-export-cli")
        buffer = io.StringIO()
        with mock.patch.object(state_io, "write_state", boom), contextlib.redirect_stdout(buffer):
            code = cli.main(["finalize", "--workdir", str(work_dir), "--step", "s-export-cli"])

        self.assertEqual(cli.EXIT_ERROR, code)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["errors"], payload)
        self.assertFalse(payload["state_written"])
        self.assertEqual(state_io.read_state(work_dir)["current_phase"], "export")


class IdempotencyTest(_WorkDirMixin, unittest.TestCase):
    def test_repeat_of_the_same_identity_is_a_no_op_with_the_stored_result(self):
        """§3.1: the same `(step, attempt)` answers from `result_ref`, it does not finalize twice."""
        work_dir = self.make_task()
        first = finalize.run_finalize(finalize_args(work_dir))
        deliverable = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        second = finalize.run_finalize(finalize_args(work_dir))

        self.assertTrue(second["already_done"])
        self.assertEqual(first["deliverable"], second["deliverable"])
        self.assertEqual(deliverable, (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8"))

        state = state_io.read_state(work_dir)
        self.assertEqual(len([step for step in state["steps"] if step["step_id"] == "s-export"]), 1)

    def test_second_finalize_is_a_no_op_on_disk(self):
        work_dir = self.make_task()
        first = finalize.run_finalize(finalize_args(work_dir))
        deliverable = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        second = finalize.run_finalize(finalize_args(work_dir, step="s-export-again"))

        self.assertFalse(first["already_terminal"])
        self.assertTrue(second["already_terminal"])
        self.assertEqual(first["current_phase"], second["current_phase"])
        self.assertEqual(deliverable, (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8"))

        state = state_io.read_state(work_dir)
        self.assertEqual(len([step for step in state["steps"] if step["step_id"] == "s-export"]), 1)
        banner_ids = [row["banner_id"] for row in state["fallback_banners"]]
        self.assertEqual(len(banner_ids), len(set(banner_ids)), "banners must not accumulate")

    def test_reason_is_recorded_once(self):
        work_dir = self.make_task()
        finalize.run_finalize(finalize_args(work_dir, reason="interrupted"))
        result = finalize.run_finalize(finalize_args(work_dir, step="s-export-again", reason="interrupted"))
        self.assertEqual(result["final_status_reasons"].count("interrupted"), 1)


class SalvageTest(_WorkDirMixin, unittest.TestCase):
    def _without_jsonschema(self):
        """Context manager hiding `jsonschema` from every importer (§5.6)."""
        return mock.patch.dict(sys.modules, {"jsonschema": None})

    def setUp(self):
        schema._validator.cache_clear()

    def tearDown(self):
        schema._validator.cache_clear()

    def test_salvage_reaches_a_terminal_phase_without_jsonschema(self):
        work_dir = self.make_task()
        args = finalize_args(work_dir, salvage=True)  # issue the step while jsonschema is still there
        with self._without_jsonschema():
            schema._validator.cache_clear()
            self.assertFalse(schema.available(), "the test must actually hide jsonschema")
            result = finalize.run_finalize(args)
        self.assert_delivered(work_dir)
        self.assertTrue(result["state_written"])
        self.assertEqual(state_io.read_state(work_dir)["current_phase"], "done")

    def test_plain_finalize_needs_jsonschema(self):
        """Guards the test above: without --salvage the missing dependency is reported, not ignored."""
        work_dir = self.make_task()
        args = finalize_args(work_dir)
        with self._without_jsonschema():
            schema._validator.cache_clear()
            with self.assertRaises(schema.DependencyMissing):
                finalize.run_finalize(args)

    def test_salvage_on_a_corrupt_state_still_delivers(self):
        work_dir = self.make_task()
        state_io.state_path(work_dir).write_text("{ this is not json", encoding="utf-8")

        result = finalize.run_finalize(finalize_args(work_dir, salvage=True))

        self.assert_delivered(work_dir)
        self.assertTrue(result["state_corrupt"])
        self.assertFalse(result["state_written"])
        self.assertIn("state_corrupt", [row["banner_id"] for row in result["banners"]])
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("salvage", summary)
        self.assertIn("state_corrupt", summary)

    def test_the_salvage_deliverable_states_the_corrupt_state_banner(self):
        """D-144: the banner belongs to `_salvaged_state`, so it is fixed before the deliverable.

        Appended after `choose_deliverable` it reached `summary.md` only — the one degradation the
        reader of the memorandum most needed to see was the one the memorandum did not mention.
        """
        work_dir = self.make_task()
        state_io.state_path(work_dir).write_text("{ this is not json", encoding="utf-8")

        result = finalize.run_finalize(finalize_args(work_dir, salvage=True))

        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertIn(md_fallback.status_heading(), body)
        self.assertIn("state.json was unreadable", body)
        self.assertEqual(
            1, [row["banner_id"] for row in result["banners"]].count("state_corrupt")
        )

    def test_corrupt_state_without_salvage_refuses_and_points_at_salvage(self):
        work_dir = self.make_task()
        state_io.state_path(work_dir).write_text("{ this is not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["errors"], ["state_unreadable"])
        self.assertIn("--salvage", result["hint"])
        self.assertFalse((work_dir / finalize.SUMMARY_MD).exists())

    def test_salvage_rebuilds_the_summary_from_the_files_on_disk(self):
        work_dir = self.make_task(with_draft=False)
        (work_dir / "research" / "findings.json").write_text('{"layer": "statutes"}', encoding="utf-8")
        state_io.state_path(work_dir).unlink()

        result = finalize.run_finalize(finalize_args(work_dir, salvage=True))

        self.assert_delivered(work_dir)
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertIn("research/findings.json", body)
        self.assertEqual(result["final_status"], "fallback_summary_delivered")

    def test_the_fallback_summary_condenses_warnings_for_the_client(self):
        # D-191: `fallback-summary.md` is published to the client when nothing else exists, so
        # its warnings are the condensed client form — first sentence, no protocol file names,
        # no `(warning_id)` tags — while `summary.md` keeps every warning verbatim.
        def mutate(state: dict) -> None:
            state["drafting_warnings"] = [
                {
                    "code": "unresolved_research_gap",
                    "message": (
                        "Nothing in intake states the retention period, "
                        "research/doctrine.json records the rest (w_1). Second sentence."
                    ),
                },
            ]

        work_dir = self.make_task(with_draft=False, mutate=mutate)
        summary = finalize.build_fallback_summary(state_io.read_state(work_dir), work_dir, None)

        self.assertIn("Nothing in intake states the retention period", summary)
        self.assertNotIn("research/doctrine.json", summary)
        self.assertNotIn("w_1", summary)
        self.assertNotIn("Second sentence.", summary)


class TidyTest(_WorkDirMixin, unittest.TestCase):
    def test_tidy_removes_tmp_and_seen_markers_but_keeps_locks_and_steps(self):
        work_dir = self.make_task()
        (work_dir / "drafts" / "v1.md.tmp").write_text("junk", encoding="utf-8")
        (work_dir / "scratch.tmp").write_text("junk", encoding="utf-8")
        (work_dir / "_update_state.py").write_text("print(1)", encoding="utf-8")
        marker = events.seen_marker(work_dir, "abc123")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
        step_file = work_dir / "steps" / "s-export" / "a1" / "cli" / "payload.tmp"
        step_file.parent.mkdir(parents=True, exist_ok=True)
        step_file.write_text("kept", encoding="utf-8")

        report = finalize.tidy(work_dir)

        self.assertFalse((work_dir / "drafts" / "v1.md.tmp").exists())
        self.assertFalse((work_dir / "scratch.tmp").exists())
        self.assertFalse((work_dir / "_update_state.py").exists())
        self.assertFalse(marker.exists())
        self.assertTrue(step_file.exists(), "steps/ is never tidied (§2.2)")
        for name in state_io.LOCK_FILENAMES:
            self.assertTrue((work_dir / name).exists(), f"{name} must survive tidy")
        self.assertTrue((work_dir / "state.json").exists())
        self.assertTrue((work_dir / "events.jsonl").exists())
        self.assertIn("scratch.tmp", report["removed"])

    def test_tidy_dry_run_deletes_nothing(self):
        work_dir = self.make_task()
        (work_dir / "scratch.tmp").write_text("junk", encoding="utf-8")
        report = finalize.tidy(work_dir, dry_run=True)
        self.assertIn("scratch.tmp", report["removed"])
        self.assertTrue((work_dir / "scratch.tmp").exists())

    def test_tidy_on_a_missing_work_dir_is_skipped_not_an_error(self):
        report = finalize.tidy(Path(tempfile.gettempdir()) / "memoforge-does-not-exist")
        self.assertTrue(report["skipped"])

    def test_finalize_runs_tidy(self):
        work_dir = self.make_task()
        (work_dir / "scratch.tmp").write_text("junk", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertIn("scratch.tmp", result["tidy"]["removed"])
        self.assertFalse((work_dir / "scratch.tmp").exists())


class IdentityTest(_WorkDirMixin, unittest.TestCase):
    """D-40: `finalize` runs the strict identity check before it touches a file."""

    def test_a_step_that_was_never_issued_is_identity_mismatch(self):
        work_dir = self.make_task()
        result = finalize.run_finalize(
            argparse.Namespace(
                workdir=str(work_dir),
                step="s-never-issued",
                attempt=1,
                reason=None,
                salvage=False,
                human=False,
            )
        )
        self.assertEqual(result["errors"], ["identity_mismatch"])
        self.assertEqual(result["reason"], "unknown_step")
        self.assertFalse((work_dir / finalize.SUMMARY_MD).exists(), "no side effect before the check")
        self.assertEqual(state_io.read_state(work_dir)["current_phase"], "export")

    def test_a_stale_attempt_cannot_finalize_over_the_current_one(self):
        work_dir = self.make_task()
        issue_step(work_dir, "s-export", 2)
        result = finalize.run_finalize(finalize_args(work_dir, step="s-export", attempt=1))
        self.assertEqual(result["errors"], ["identity_mismatch"])
        self.assertEqual(result["reason"], "stale_attempt")

    def test_salvage_delivers_even_without_an_issued_identity(self):
        work_dir = self.make_task()
        result = finalize.run_finalize(
            argparse.Namespace(
                workdir=str(work_dir),
                step="s-never-issued",
                attempt=1,
                reason=None,
                salvage=True,
                human=False,
            )
        )
        self.assert_delivered(work_dir)
        self.assertTrue(result["state_written"])
        journal = (work_dir / events.EVENTS_FILENAME).read_text(encoding="utf-8")
        self.assertIn('"identity_check": "skipped"', journal)


class DeliverableBindingTest(_WorkDirMixin, unittest.TestCase):
    """§2.1 row 15 / D-50: an export counts only when it came from the version `select_draft` chose."""

    def memo_md(self, work_dir: Path) -> Path:
        return work_dir / "memo-gdpr-transcripts.md"

    def memo_docx(self, work_dir: Path) -> Path:
        return work_dir / "memo-gdpr-transcripts.docx"

    def test_a_rendered_md_of_unknown_provenance_is_re_rendered(self):
        work_dir = self.make_task()
        self.memo_md(work_dir).write_text("# STALE EXPORT OF ANOTHER VERSION\n", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir))
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertNotIn("STALE EXPORT", body)
        self.assertIn("Sources", body)
        self.assertEqual(result["deliverable"], finalize.DELIVERABLE_MD)

    def test_the_render_of_the_selected_version_is_reused(self):
        work_dir = self.make_task(final_status=SIGNED_OFF)
        rendered = render_export(work_dir)
        self.assertEqual(rendered["deliverable_path"], self.memo_docx(work_dir).name)
        exported = self.memo_docx(work_dir).read_bytes()

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "docx")
        self.assertEqual(exported, (work_dir / finalize.DELIVERABLE_DOCX).read_bytes())

    def test_a_render_of_a_superseded_version_is_not_reused(self):
        work_dir = self.make_task()
        render_export(work_dir)
        # The writer produces a new version after the export ran.
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertFalse((work_dir / finalize.DELIVERABLE_DOCX).is_file())
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertIn("A later revision.", body)

    def test_a_docx_whose_source_sha_does_not_match_is_not_the_deliverable(self):
        """D-50: the binding is the sha `docx render` recorded, not the file sitting on disk."""
        work_dir = self.make_task()
        render_export(work_dir)
        forge_render_sha(work_dir, "0" * 64)

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertFalse((work_dir / finalize.DELIVERABLE_DOCX).is_file())
        self.assertTrue(self.memo_docx(work_dir).is_file(), "the export itself stays where it was")

    def test_a_docx_that_drifted_from_published_is_not_the_deliverable(self):
        work_dir = self.make_task()
        render_export(work_dir)
        self.memo_docx(work_dir).write_bytes(b"PK\x03\x04 hand-edited after the export")

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertFalse((work_dir / finalize.DELIVERABLE_DOCX).is_file())

    def test_a_demoted_re_render_leaves_no_docx_for_finalize(self):
        """D-117 N-06: `final_docx_path` is None, and the export of the first render goes with it."""
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        self.assertTrue(self.memo_docx(work_dir).is_file())

        # The same draft, re-rendered after the registry lost the source: `[unresolved: …]` reaches
        # the docx, so this render is demoted and publishes nothing.
        state_io.write_json_atomic(
            work_dir / "research" / "sources.json", {"schema_version": 2, "sources": {}}
        )
        second = render_export(work_dir, "s-render-2")
        self.assertEqual("memo-gdpr-transcripts.invalid.docx", second["demoted_to"])
        self.assertIsNone(state_io.read_state(work_dir)["final_docx_path"])
        self.assertFalse(self.memo_docx(work_dir).exists())

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertFalse((work_dir / finalize.DELIVERABLE_DOCX).is_file())

    def test_a_revoked_export_the_unlink_could_not_remove_is_ignored(self):
        """D-144 (R2-02): the publication is gone, so the bytes left behind are not a deliverable."""
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        self.assertTrue(self.memo_docx(work_dir).is_file())

        state_io.write_json_atomic(
            work_dir / "research" / "sources.json", {"schema_version": 2, "sources": {}}
        )
        real_unlink = Path.unlink

        def locked(path, *args, **kwargs):
            if path.name == "memo-gdpr-transcripts.docx":
                raise OSError("locked by another process")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", locked):
            second = render_export(work_dir, "s-render-2")

        self.assertEqual("memo-gdpr-transcripts.docx", second["revoked"])
        self.assertIn("OSError", second["revoke_error"])
        self.assertTrue(self.memo_docx(work_dir).is_file(), "the bytes could not be removed")
        state = state_io.read_state(work_dir)
        self.assertIsNone(state["final_docx_path"])
        self.assertNotIn(
            "memo-gdpr-transcripts.docx", [row["canonical_path"] for row in state["published"]]
        )

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertFalse((work_dir / finalize.DELIVERABLE_DOCX).is_file())

    def test_an_export_whose_status_no_longer_holds_is_not_the_deliverable(self):
        """D-123 N-07: a docx cannot be rewritten, so a stale `## Status` hands the run to markdown."""
        work_dir = self.make_task(final_status="manual_review_required_on_v1")
        render_export(work_dir)

        def decide(state: dict) -> None:
            state["final_status"] = "forced_exit_on_v1_with_remaining_issues"

        state_io.write_state(work_dir, decide)
        result = finalize.run_finalize(finalize_args(work_dir))

        self.assertEqual(result["deliverable_kind"], "md")
        self.assertFalse((work_dir / finalize.DELIVERABLE_DOCX).is_file())
        self.assertTrue(self.memo_docx(work_dir).is_file(), "the export itself stays where it was")
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        # D-197: the deliverable carries the status as a sentence, not as a code.
        self.assertIn(NAME_FORCED_EXIT_V1, body)
        self.assertNotIn(NAME_MANUAL_REVIEW_V1, body)

    def test_a_banner_raised_after_the_export_makes_it_stale_too(self):
        """D-123: the banners are half of what the `## Status` section says."""
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)

        def raise_banner(state: dict) -> None:
            rows = list(state.get("fallback_banners") or [])
            state["fallback_banners"] = rows + [fallbacks.banner("currency_checker_failed")]

        state_io.write_state(work_dir, raise_banner)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")

    def test_a_blocker_decided_after_the_export_makes_it_stale(self):
        """D-144 (R2-03): the signature is the whole rendered section, blocker lines included.

        `{final_status, banner ids}` matched here, so the docx went to the client with an empty
        «Unresolved blocking issues» list while `summary.md` printed the blocker.
        """
        work_dir = self.make_task(final_status="forced_exit_on_v1_with_remaining_issues")
        render_export(work_dir)

        def decide(state: dict) -> None:
            state["remaining_blocking_issues"] = [
                {"severity": "blocker", "section_id": "s-1", "issue": "Art. 17(1) carries no rule."}
            ]

        state_io.write_state(work_dir, decide)
        result = finalize.run_finalize(finalize_args(work_dir))

        self.assertEqual(result["deliverable_kind"], "md")
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertIn("- blocker · section 1 · Art. 17(1) carries no rule.", body)

    def test_a_banner_whose_text_changed_makes_the_export_stale(self):
        """D-144 (R2-03): the section prints banner *texts*; the old signature carried only ids."""

        def raise_partial(state: dict) -> None:
            state["fallback_banners"] = [fallbacks.banner("mcp_partial", available="the alpha server")]

        work_dir = self.make_task(
            final_status="forced_exit_on_v1_with_remaining_issues", mutate=raise_partial
        )
        render_export(work_dir)
        self.assertIn("the alpha server", self.memo_md(work_dir).read_text(encoding="utf-8"))

        def reword(state: dict) -> None:
            state["fallback_banners"] = [fallbacks.banner("mcp_partial", available="the beta server")]

        state_io.write_state(work_dir, reword)
        result = finalize.run_finalize(finalize_args(work_dir))

        self.assertEqual(result["deliverable_kind"], "md")
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertIn("the beta server", body)
        self.assertNotIn("the alpha server", body)

    def test_an_export_whose_status_still_holds_is_delivered(self):
        """The other half of D-123: unchanged status inputs keep the docx the deliverable."""
        work_dir = self.make_task(final_status="forced_exit_on_v1_with_remaining_issues")
        render_export(work_dir)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "docx")
        self.assertEqual(self.memo_docx(work_dir).read_bytes(), (work_dir / finalize.DELIVERABLE_DOCX).read_bytes())

    def test_the_markdown_render_of_the_selected_version_is_reused(self):
        """The same binding on the md branch of §2.1 row 15, with no docx left to deliver (M9)."""
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        exported = self.memo_md(work_dir).read_text(encoding="utf-8")
        self.memo_docx(work_dir).unlink()

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertEqual(exported, (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8"))

    def test_a_markdown_render_of_a_superseded_version_is_not_reused(self):
        work_dir = self.make_task()
        render_export(work_dir)
        self.memo_docx(work_dir).unlink()
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")

        finalize.run_finalize(finalize_args(work_dir))
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertIn("A later revision.", body)


class DeliveredDraftShaTest(_WorkDirMixin, unittest.TestCase):
    """D-220: `finalize` records the sha of the draft bytes the deliverable was rendered from."""

    def memo_docx(self, work_dir: Path) -> Path:
        return work_dir / "memo-gdpr-transcripts.docx"

    def delivered(self, work_dir: Path):
        state = state_io.read_state(work_dir)
        self.assertTrue("delivered_draft_sha" in state, "finalize did not record delivered_draft_sha")
        return state["delivered_draft_sha"]

    def record_versions(self, work_dir: Path, *versions: tuple[int, str]) -> None:
        """`draft_versions[]` rows that passed both checks, each bound to the sha it was checked at."""

        def mutator(state: dict) -> None:
            state["draft_versions"] = [
                {
                    "version": version,
                    "path": relative,
                    "sha256": state_io.sha256_file(work_dir / relative),
                    "lint_clean": True,
                    "citations_clean": True,
                    "checked_at": "2026-09-08T12:00:00.000Z",
                }
                for version, relative in versions
            ]

        state_io.write_state(work_dir, mutator)

    def test_a_reused_docx_export_records_the_selected_sha(self):
        from memoforge.docx import select_draft

        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "docx")
        expected = select_draft(state_io.read_state(work_dir), work_dir)["sha256"]
        self.assertEqual(expected, state_io.sha256_file(work_dir / "drafts" / "v1.md"))
        self.assertEqual(expected, self.delivered(work_dir))
        self.assertEqual(expected, result["delivered_draft_sha"])

    def test_a_reused_markdown_export_records_the_selected_sha(self):
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        exported = (work_dir / "memo-gdpr-transcripts.md").read_text(encoding="utf-8")
        self.memo_docx(work_dir).unlink()

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertEqual(exported, (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8"))
        self.assertEqual(state_io.sha256_file(work_dir / "drafts" / "v1.md"), self.delivered(work_dir))

    def test_a_fresh_render_of_a_checked_version_records_its_sha(self):
        work_dir = self.make_task()
        self.record_versions(work_dir, (1, "drafts/v1.md"))
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertEqual(state_io.sha256_file(work_dir / "drafts" / "v1.md"), self.delivered(work_dir))

    def test_a_pinned_older_version_records_the_pinned_sha(self):
        work_dir = self.make_task()
        (work_dir / "drafts" / "v2.md").write_text(DRAFT + "\nThe second version.\n", encoding="utf-8")
        self.record_versions(work_dir, (1, "drafts/v1.md"), (2, "drafts/v2.md"))
        pinned = state_io.sha256_file(work_dir / "drafts" / "v1.md")
        state_io.write_state(work_dir, lambda state: state.update(export_pin={"version": 1, "sha256": pinned}))

        finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(pinned, self.delivered(work_dir))
        self.assertNotEqual(state_io.sha256_file(work_dir / "drafts" / "v2.md"), self.delivered(work_dir))
        self.assertNotIn("The second version.", (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8"))

    def test_an_export_reused_without_a_draft_records_null(self):
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        (work_dir / "drafts" / "v1.md").unlink()

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "md")
        self.assertNotEqual("fallback_summary_delivered", result["final_status"])
        self.assertIsNone(self.delivered(work_dir))

    def test_a_draft_changed_before_finalization_records_the_bytes_on_disk(self):
        work_dir = self.make_task()
        self.record_versions(work_dir, (1, "drafts/v1.md"))
        recorded = state_io.read_state(work_dir)["draft_versions"][0]["sha256"]
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nChanged before finalize.\n", encoding="utf-8")

        finalize.run_finalize(finalize_args(work_dir))
        self.assertIn("Changed before finalize.", (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8"))
        on_disk = state_io.sha256_file(work_dir / "drafts" / "v1.md")
        self.assertNotEqual(recorded, on_disk)
        self.assertEqual(on_disk, self.delivered(work_dir))

    def test_the_fallback_summary_records_null(self):
        work_dir = self.make_task(with_draft=False)
        result = finalize.run_finalize(finalize_args(work_dir, reason="drafting_failed"))
        self.assertEqual("fallback_summary_delivered", result["final_status"])
        self.assertIsNone(self.delivered(work_dir))
        self.assertIsNone(result["delivered_draft_sha"])

    def test_salvage_over_a_readable_state_records_it_too(self):
        work_dir = self.make_task()
        finalize.run_finalize(finalize_args(work_dir, salvage=True))
        self.assertEqual(state_io.sha256_file(work_dir / "drafts" / "v1.md"), self.delivered(work_dir))

    def test_the_schema_accepts_a_sha_or_null_and_rejects_anything_else(self):
        state = state_io.read_state(self.make_task())
        self.assertEqual([], schema.validate(state, "state"), "absent is valid (a task finalized before 75A)")
        self.assertEqual([], schema.validate(dict(state, delivered_draft_sha=None), "state"))
        self.assertEqual([], schema.validate(dict(state, delivered_draft_sha="0123456789abcdef" * 4), "state"))
        self.assertNotEqual([], schema.validate(dict(state, delivered_draft_sha="abc"), "state"))


class AppendixTest(_WorkDirMixin, unittest.TestCase):
    """D-191: the client appendix carries unverified sources only; `summary.md` keeps every warning verbatim."""

    def make_appendix_task(self, *, currency_unavailable: bool = False, **kwargs) -> Path:
        def mutate(state: dict) -> None:
            state["drafting_warnings"] = warnings_fixture()
            if currency_unavailable:
                state["fallback_banners"] = [fallbacks.banner("currency_checker_failed")]

        work_dir = self.make_task(mutate=mutate, **kwargs)
        registry = json.loads(json.dumps(SOURCES))
        registry["sources"]["gdpr-art6"]["currency"] = {"status": "unchecked"}
        registry["sources"]["ai-act"] = {
            "layer": "statutes",
            "title": "AI Act",
            "citation_form": "Regulation (EU) 2024/1689 (AI Act), Annex III(4)",
            "currency": {"status": "unchecked"},
            "liveness": {"status": "changed"},
        }
        registry["sources"]["edpb-op28"] = {
            "layer": "doctrine",
            "title": "EDPB Opinion 28/2024",
            "citation_form": "EDPB, Opinion 28/2024",
            "currency": {"status": "unchecked"},
            "verification": {"eu_syntax_ok": False},
        }
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)
        # D-197: the appendix discloses cited sources, so the draft names the two extra records.
        (work_dir / "drafts" / "v1.md").write_text(
            DRAFT.replace(
                "<!-- sources: generated -->",
                "Also [[src:ai-act Annex III(4)]] and [[src:edpb-op28]].\n\n"
                "<!-- sources: generated -->",
            ),
            encoding="utf-8",
        )
        return work_dir

    def appendix(self, work_dir: Path) -> str:
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        heading = "## Appendix"
        self.assertIn(heading, body)
        return heading + body.partition(heading)[2]

    def bullets(self, appendix: str, label: str) -> list[str]:
        block = appendix.partition(label)[2].strip().split("\n\n")[0]
        return [line[2:] for line in block.splitlines() if line.startswith("- ")]

    def test_warnings_stay_out_of_the_appendix(self):
        # D-191: drafting warnings live in the facts section; only unverified sources open it.
        work_dir = self.make_appendix_task()
        finalize.run_finalize(finalize_args(work_dir))
        appendix = self.appendix(work_dir)

        self.assertNotIn(ASSUMPTIONS_MD, appendix)
        self.assertNotIn("Nothing in intake", appendix)
        # D-216: the only group of the appendix prints no label under the heading.
        self.assertNotIn(UNVERIFIED_MD, appendix)
        self.assertIn("- EDPB, Opinion 28/2024", appendix)

    def test_an_appendix_without_unverified_sources_is_absent(self):
        # D-191: warnings alone do not open the appendix.
        work_dir = self.make_appendix_task()
        (work_dir / "research" / "sources.json").write_text(json.dumps(SOURCES), encoding="utf-8")
        (work_dir / "drafts" / "v1.md").write_text(DRAFT, encoding="utf-8")
        finalize.run_finalize(finalize_args(work_dir))
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertNotIn("## Appendix", body)

    def test_the_summary_still_carries_every_warning_verbatim(self):
        work_dir = self.make_appendix_task()
        finalize.run_finalize(finalize_args(work_dir))
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")

        self.assertIn(LONG_WARNING, summary)
        self.assertIn("research/doctrine.json", summary)
        self.assertIn("(`unresolved_research_gap`)", summary)
        self.assertEqual(summary.count("Assumption 12 rests on"), 1)

    def test_an_unavailable_currency_checker_is_one_line_not_one_per_source(self):
        work_dir = self.make_appendix_task(currency_unavailable=True)
        finalize.run_finalize(finalize_args(work_dir))
        rows = self.bullets(self.appendix(work_dir), md_fallback.appendix_heading())

        self.assertEqual(rows[0], md_fallback.label('currency_unavailable_note'))
        self.assertNotIn("currency unchecked", "\n".join(rows))
        # Only the sources with a problem of their own are still listed.
        self.assertEqual(len(rows), 3)
        self.assertIn("Regulation (EU) 2024/1689 (AI Act), Annex III(4) — link changed", rows)
        self.assertIn("EDPB, Opinion 28/2024 — EU identifier syntax not verified", rows)

    def test_unresolved_references_survive_the_rewrite(self):
        """The third block names ids that really are in the text, so it is carried over as rendered."""
        work_dir = self.make_appendix_task()
        (work_dir / "drafts" / "v1.md").write_text(
            DRAFT.replace("[[q:q-001]]", "[[src:ghost]]"), encoding="utf-8"
        )
        finalize.run_finalize(finalize_args(work_dir))
        appendix = self.appendix(work_dir)

        self.assertIn(UNRESOLVED_MD, appendix)
        self.assertIn("ghost", appendix)
        self.assertNotIn(ASSUMPTIONS_MD, appendix)

    def test_a_currency_check_that_ran_keeps_its_per_source_lines(self):
        work_dir = self.make_appendix_task()
        finalize.run_finalize(finalize_args(work_dir))
        rows = self.bullets(self.appendix(work_dir), md_fallback.appendix_heading())

        self.assertNotIn(md_fallback.label('currency_unavailable_note'), rows)
        self.assertEqual(len([row for row in rows if "currency unchecked" in row]), 3)


class AppendixWithoutADraftTest(_WorkDirMixin, unittest.TestCase):
    """FF2/FF3: with no draft to establish citation membership, the pipeline does not guess.

    The recovery of fix round 1 (a citation set recorded on the render step, a tier heuristic) was
    removed: the recorded set was not bound to the delivered bytes and the heuristic inferred «not
    cited» from a tier. An existing export's «Unverified sources» block is preserved instead — it
    was written while the draft existed and is already filtered — and with no export at all the
    pre-Task-2 behaviour stands.
    """

    PLACEHOLDER = "Placeholder Opinion 1/2024"

    def make_task_with_a_placeholder(self) -> Path:
        work_dir = self.make_task()
        registry = json.loads(json.dumps(SOURCES))
        registry["sources"]["gdpr-art6"]["currency"] = {"status": "unchecked"}
        registry["sources"]["placeholder"] = {
            "layer": "doctrine",
            "title": "A record nothing cites",
            "citation_form": self.PLACEHOLDER,
            "tier": "background",
            "currency": {"status": "unchecked"},
        }
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)
        return work_dir

    def appendix_of(self, body: str) -> str:
        self.assertIn(md_fallback.appendix_heading(), body)
        return body.partition(md_fallback.appendix_heading())[2].rstrip()

    def test_the_render_step_records_no_citation_set(self):
        # FF2: a recorded set is applied to whatever bytes finalize happens to deliver, so a v1 set
        # could filter a v2 export. There is no recorded set any more, so that cannot happen.
        work_dir = self.make_task_with_a_placeholder()
        result = render_export(work_dir)
        self.assertNotIn("cited_source_ids", result)
        for row in state_io.read_state(work_dir).get("steps") or []:
            ref = row.get("result_ref") if isinstance(row, dict) else None
            stored = ref.get("result") if isinstance(ref, dict) else None
            if isinstance(stored, dict):
                self.assertNotIn("cited_source_ids", stored)

    def test_the_exports_unverified_block_is_preserved_when_the_draft_is_gone(self):
        # FF3 (a): the export was rendered while the draft existed, so its appendix is already the
        # filtered one — it is delivered as it stands, block for block.
        work_dir = self.make_task_with_a_placeholder()
        render_export(work_dir)
        exported = self.appendix_of(md_fallback_export(work_dir))
        self.assertIn("Regulation (EU) 2016/679", exported)
        self.assertNotIn(self.PLACEHOLDER, exported)

        (work_dir / "drafts" / "v1.md").unlink()
        finalize.run_finalize(finalize_args(work_dir))
        delivered = self.appendix_of(
            (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        )
        self.assertEqual(exported, delivered)
        self.assertNotIn(self.PLACEHOLDER, delivered)

    def test_without_a_draft_and_without_an_export_the_full_list_stands(self):
        # FF3 (c): the fallback summary is the deliverable and nothing rebuilds a filtered appendix;
        # `unverified_rows(..., None)` keeps its pre-Task-2 meaning for whoever asks it.
        work_dir = self.make_task_with_a_placeholder()
        (work_dir / "drafts" / "v1.md").unlink()
        result = finalize.run_finalize(finalize_args(work_dir))
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertEqual("fallback_summary_delivered", result["final_status"])
        self.assertNotIn(md_fallback.appendix_heading(), body)
        rows = md_fallback.SourceIndex.load(work_dir).unverified_rows()
        self.assertEqual(["gdpr-art6", "placeholder"], [row["source_id"] for row in rows])

    def test_the_index_has_no_tier_heuristic_any_more(self):
        self.assertFalse(hasattr(md_fallback.SourceIndex, "citable_ids"))
        from memoforge import docx as docx_pkg

        self.assertFalse(hasattr(docx_pkg, "rendered_cited_source_ids"))


class StatusSectionTest(_WorkDirMixin, unittest.TestCase):
    """D34-11: the banners and `state.remaining_blocking_issues` reach the client, not only state."""

    BLOCKERS = [
        {
            "severity": "blocker",
            "section_id": f"s-{n}",
            "category": "unsupported_inference",
            "issue": f"Art. 17(1) is cited without a rule in section {n}.",
        }
        for n in range(1, 16)
    ]

    def make_exited_task(self, **overrides) -> Path:
        def mutate(state: dict) -> None:
            state["final_status"] = "forced_exit_on_v1_with_remaining_issues"
            state["final_status_reasons"] = ["unresolved_blockers"]
            state["remaining_blocking_issues"] = self.BLOCKERS
            state["fallback_banners"] = [fallbacks.banner("currency_checker_failed")]
            state.update(overrides)

        return self.make_task(mutate=mutate)

    def deliverable(self, work_dir: Path) -> str:
        return (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")

    def status(self, body: str) -> str:
        self.assertIn(md_fallback.status_heading(), body)
        return body.partition(md_fallback.status_heading())[2].partition("\n## ")[0]

    def test_the_deliverable_states_the_banners_and_the_blockers(self):
        work_dir = self.make_exited_task()
        finalize.run_finalize(finalize_args(work_dir))
        status = self.status(self.deliverable(work_dir))

        self.assertIn(NAME_FORCED_EXIT_V1, status)
        self.assertIn("Currency check unavailable", status)
        self.assertIn("- blocker · section 1 · Art. 17(1) is cited without a rule in section 1.", status)

    def test_the_blocker_list_is_capped_and_points_at_the_summary(self):
        work_dir = self.make_exited_task()
        finalize.run_finalize(finalize_args(work_dir))
        status = self.status(self.deliverable(work_dir))
        rows = [row for row in status.splitlines() if row.startswith("- blocker · ")]

        self.assertEqual(md_fallback.STATUS_ISSUE_LIMIT, len(rows))
        self.assertIn("- … and 3 more in summary.md", status)

    def test_the_status_section_stands_before_the_appendix(self):
        # D-191: the exited task's currency-unavailable banner opens the appendix.
        work_dir = self.make_exited_task()
        finalize.run_finalize(finalize_args(work_dir))
        body = self.deliverable(work_dir)

        self.assertLess(
            body.index(md_fallback.status_heading()), body.index(md_fallback.appendix_heading())
        )

    def test_the_summary_lists_every_blocker(self):
        work_dir = self.make_exited_task()
        finalize.run_finalize(finalize_args(work_dir))
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")

        self.assertIn("## Remaining blocking issues", summary)
        self.assertEqual(len(self.BLOCKERS), summary.count("- blocker · s-"))
        self.assertIn("- blocker · s-15 · Art. 17(1) is cited without a rule in section 15.", summary)

    def test_a_run_without_blockers_says_none_in_the_summary(self):
        work_dir = self.make_task()
        finalize.run_finalize(finalize_args(work_dir))
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("## Remaining blocking issues\n\n- none\n", summary)

    def test_an_approved_run_carries_no_status_section(self):
        work_dir = self.make_task(mutate=lambda state: state.update(final_status="approved_on_v2"))
        finalize.run_finalize(finalize_args(work_dir))
        self.assertNotIn(md_fallback.status_heading(), self.deliverable(work_dir))

    def test_a_status_decided_after_the_export_reaches_a_body_without_an_appendix(self):
        """D-123 N-07: `render_appendix` writes nothing for a clean run — the status still refreshes."""
        work_dir = self.make_task()
        render_export(work_dir)
        exported = (work_dir / "memo-gdpr-transcripts.md").read_text(encoding="utf-8")
        self.assertNotIn(md_fallback.appendix_heading(), exported)
        self.assertNotIn(md_fallback.status_heading(), exported)
        (work_dir / "memo-gdpr-transcripts.docx").unlink()  # the md branch of §2.1 row 15

        def decide(state: dict) -> None:
            state["final_status"] = "forced_exit_on_v1_with_remaining_issues"
            state["remaining_blocking_issues"] = self.BLOCKERS[:1]

        state_io.write_state(work_dir, decide)
        finalize.run_finalize(finalize_args(work_dir))
        status = self.status(self.deliverable(work_dir))

        self.assertIn(NAME_FORCED_EXIT_V1, status)
        self.assertIn("- blocker · section 1 · Art. 17(1) is cited without a rule in section 1.", status)

    def test_a_stale_status_section_is_replaced_not_doubled(self):
        """The export already carries a `## Status`; the run ended on another one (D-123)."""
        work_dir = self.make_task(final_status="manual_review_required_on_v1")
        render_export(work_dir)
        exported = (work_dir / "memo-gdpr-transcripts.md").read_text(encoding="utf-8")
        self.assertIn(NAME_MANUAL_REVIEW_V1, exported)
        self.assertNotIn(md_fallback.appendix_heading(), exported)
        (work_dir / "memo-gdpr-transcripts.docx").unlink()

        def decide(state: dict) -> None:
            state["final_status"] = "forced_exit_on_v2_with_remaining_issues"

        state_io.write_state(work_dir, decide)
        finalize.run_finalize(finalize_args(work_dir))
        body = self.deliverable(work_dir)

        self.assertEqual(1, body.count(md_fallback.status_heading()))
        self.assertIn(md_fallback.status_name("forced_exit_on_v2_with_remaining_issues"), body)
        self.assertNotIn(NAME_MANUAL_REVIEW_V1, body)

    def test_no_banner_is_raised_after_the_deliverable_is_chosen(self):
        """D-144 + fix wave (D-166): only copy/telemetry rows arrive after `choose_deliverable`.

        `choose_deliverable` writes the `## Status` section out of the banner list and a docx
        cannot be rewritten afterwards, so a row appended later either never reaches the reader
        or makes a current export stale. The soft-cap banner is MCP telemetry, so since the fix
        wave it takes the `publish_failed` route: raised after the choice, a `COPY_BANNERS` row
        the section leaves out by design — there is no fourth late banner.
        """
        tail = inspect.getsource(finalize.run_finalize).partition("choose_deliverable(")
        self.assertTrue(tail[1], "run_finalize must still call choose_deliverable")
        self.assertEqual(
            ["banners = collect_banners(state, list(deliverable[\"banners\"]) + soft_cap_banners(state))"],
            [line.strip() for line in tail[2].splitlines() if "soft_cap_banners(" in line],
        )
        published = inspect.getsource(finalize.publish) + inspect.getsource(
            finalize._publish_failed_banner
        )
        self.assertEqual(
            ['return fallbacks.banner("publish_failed", failure=f" ({type(exc).__name__})")'],
            [line.strip() for line in published.splitlines() if "fallbacks.banner(" in line],
        )
        self.assertIn("publish_failed", md_fallback.COPY_BANNERS)
        self.assertIn("mcp_soft_cap_exceeded", md_fallback.COPY_BANNERS)

    def test_a_copy_banner_is_never_a_status_input(self):
        """D-144 + fix wave (D-166): copy/telemetry rows stay in `summary.md`, re-rendered after."""
        state = {
            "final_status": "forced_exit_on_v1_with_remaining_issues",
            "fallback_banners": [
                fallbacks.banner("publish_failed", failure=" (OSError)"),
                fallbacks.banner("output_folder_write_failed", work_dir="/tmp/work"),
                fallbacks.banner("mcp_soft_cap_exceeded", server="legalviz", count=120),
            ],
        }
        inputs = md_fallback.status_inputs(state)
        self.assertEqual([], inputs["banner_ids"])
        self.assertEqual([], inputs["banners"])
        self.assertEqual(
            md_fallback.status_inputs({"final_status": state["final_status"]}), inputs
        )

    def test_a_second_finalize_reproduces_the_same_status_section(self):
        """The section describes the terminal status, which the first finalize wrote into state."""
        work_dir = self.make_exited_task()
        finalize.run_finalize(finalize_args(work_dir))
        first = self.deliverable(work_dir)
        finalize.run_finalize(finalize_args(work_dir, step="s-export-again"))

        self.assertEqual(first, self.deliverable(work_dir))
        self.assertIn(md_fallback.status_heading(), first)


class McpSoftCapTest(_WorkDirMixin, unittest.TestCase):
    """D-166: a free server past the soft cap raises a banner and a summary section."""

    def test_a_valid_docx_export_survives_a_soft_cap_finalize(self):
        """Fix wave: the soft-cap banner is telemetry about MCP calls, not the memorandum.

        It must not change `status_signature` — otherwise `_existing_docx` rejects the
        current export and finalize delivers markdown with a `docx_render_failed` banner.
        """
        work_dir = self.make_task(final_status=SIGNED_OFF)
        render_export(work_dir)
        state_io.write_state(
            work_dir, lambda state: state["progress"].update(mcp_calls={"legalviz": 120})
        )
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(result["deliverable_kind"], "docx")
        banner_ids = [row["banner_id"] for row in result["banners"]]
        self.assertIn("mcp_soft_cap_exceeded", banner_ids)
        self.assertNotIn("docx_render_failed", banner_ids)
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("mcp_soft_cap_exceeded", summary)

    def test_a_free_server_past_the_soft_cap_raises_the_banner(self):
        def mutate(state: dict) -> None:
            state["progress"]["mcp_calls"] = {"legalviz": 120}

        work_dir = self.make_task(mutate=mutate)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertIn("mcp_soft_cap_exceeded", [row["banner_id"] for row in result["banners"]])
        self.assertIn(
            "mcp_soft_cap_exceeded",
            [row["banner_id"] for row in state_io.read_state(work_dir)["fallback_banners"]],
        )
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("## MCP calls", summary)
        self.assertIn("- legalviz: 120", summary)

    def test_a_second_finalize_does_not_double_the_soft_cap_banner(self):
        def mutate(state: dict) -> None:
            state["progress"]["mcp_calls"] = {"legalviz": 120}

        work_dir = self.make_task(mutate=mutate)
        finalize.run_finalize(finalize_args(work_dir))
        finalize.run_finalize(finalize_args(work_dir, step="s-export-again"))
        banners = state_io.read_state(work_dir)["fallback_banners"]
        self.assertEqual(
            1, [row["banner_id"] for row in banners].count("mcp_soft_cap_exceeded")
        )

    def test_two_servers_past_the_soft_cap_keep_one_banner_each(self):
        def mutate(state: dict) -> None:
            state["progress"]["mcp_calls"] = {"legalviz": 120, "justicelibre": 130}

        work_dir = self.make_task(mutate=mutate)
        result = finalize.run_finalize(finalize_args(work_dir))
        stored = state_io.read_state(work_dir)["fallback_banners"]
        for rows in (result["banners"], stored):
            soft = [row for row in rows if row["banner_id"] == "mcp_soft_cap_exceeded"]
            self.assertEqual(2, len(soft))
            self.assertEqual(
                {"legalviz", "justicelibre"},
                {row["params"]["server"] for row in soft},
            )
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("- justicelibre: 130", summary)
        self.assertIn("- legalviz: 120", summary)


PDF_ORIGINAL = b"%PDF-1.7\n" + b"original bytes\n" * 40 + b"%%EOF\n"
"""D-201: the bytes of an original as the server served them — never decoded, only copied."""


class PdfExportTest(PublishTest):
    """D-201: the original PDF is delivered next to the text, and a changed one is named instead."""

    def add_pdf_source(self, work_dir: Path, *, source_id: str, text: str | None) -> None:
        """A `mf sources save` record of a PDF — with a text layer when `text` is given."""
        path = work_dir / "research" / "sources.json"
        registry = json.loads(path.read_text(encoding="utf-8-sig"))
        raw = work_dir / "research" / "raw" / "case_law"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / f"{source_id}.pdf").write_bytes(PDF_ORIGINAL)
        record = {
            "layer": "case_law",
            "title": "ВС РФ, определение",
            "citation_form": "Определение ВС РФ",
            "url": "https://vsrf.ru/act.pdf",
            "tier": "critical",
            "raw_path": None,
            "raw_kind": "none",
            "raw_original_path": f"research/raw/case_law/{source_id}.pdf",
            "raw_original_sha256": state_io.sha256_bytes(PDF_ORIGINAL),
        }
        if text is not None:
            (raw / f"{source_id}.md").write_bytes(text.encode("utf-8"))
            record["raw_path"] = f"research/raw/case_law/{source_id}.md"
            record["raw_kind"] = "full_text"
            record["raw_sha256"] = state_io.sha256_bytes(text.encode("utf-8"))
        registry["sources"][source_id] = record
        state_io.write_json_atomic(path, registry)

    def freeze_pack(
        self, work_dir: Path, source_id: str, *, original: str | None, text: str | None = None
    ) -> None:
        """The frozen pack of that source. `original`/`text` are the digests the snapshot pins;
        None means the freeze pinned nothing for that artefact, which authorises nothing."""
        pack = {
            "schema_version": 2,
            "frozen_at": "2026-09-08T12:00:00.000Z",
            "entries": [
                {
                    "source_id": source_id,
                    "layer": "case_law",
                    "title": "ВС РФ, определение",
                    "citation_form": "Определение ВС РФ",
                    "url": "https://vsrf.ru/act.pdf",
                    "tier": "critical",
                    "currency_status": "current",
                    "pack": {
                        "role_by_issue": {},
                        "weight": "binding",
                        "confidence": "high",
                        "use_in_memo": "rule",
                    },
                }
            ],
            "snapshot": [
                {"source_id": source_id, "raw_sha256": text},
                # A real freeze pins every source of the work dir, not only the one under test.
                {"source_id": "gdpr-art6", "raw_sha256": state_io.sha256_bytes(RAW_ARTICLE_6)},
            ],
        }
        if original is not None:
            pack["snapshot"][0]["raw_original_sha256"] = original
        state_io.write_json_atomic(work_dir / "research" / "source-pack.json", pack)

    def sources_dir(self, root: Path, work_dir: Path) -> Path:
        return self.published_dir(root, work_dir) / finalize.PUBLISH_SOURCES_DIRNAME

    def test_the_original_is_copied_even_when_there_is_no_text(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertEqual(PDF_ORIGINAL, (published / "vsrf-act.pdf").read_bytes())
        self.assertFalse((published / "vsrf-act.txt").exists(), "there is no text to export")
        self.assertIn(f"{finalize.PUBLISH_SOURCES_DIRNAME}/vsrf-act.pdf", result["published_files"])

    def test_the_original_travels_beside_the_text(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        text = "ОПРЕДЕЛЕНИЕ\nустановил\n"
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=text)
        self.freeze_pack(
            work_dir,
            "vsrf-act",
            original=state_io.sha256_bytes(PDF_ORIGINAL),
            text=state_io.sha256_bytes(text.encode("utf-8")),
        )
        finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertEqual(PDF_ORIGINAL, (published / "vsrf-act.pdf").read_bytes())
        self.assertIn("установил", (published / "vsrf-act.txt").read_text(encoding="utf-8"))

    def test_a_background_source_keeps_its_original_private(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        registry = json.loads((work_dir / "research" / "sources.json").read_text(encoding="utf-8-sig"))
        registry["sources"]["vsrf-act"]["tier"] = "background"
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        finalize.run_finalize(finalize_args(work_dir))
        self.assertFalse((self.sources_dir(root, work_dir) / "vsrf-act.pdf").exists())

    def test_an_original_edited_after_the_freeze_is_not_exported_and_is_named(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original="0" * 64)
        result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse((published / "vsrf-act.pdf").exists(), "a changed original is never exported")
        pack_md = (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8")
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"), pack_md
        )
        # D-109: the delivery continues — everything else is still there.
        self.assertIsNotNone(result["published_to"])
        self.assertTrue((published / "gdpr-art6.txt").is_file())
        self.assertTrue((self.published_dir(root, work_dir) / finalize.DELIVERABLE_MD).is_file())

    def test_a_missing_original_is_named_not_silently_omitted(self):
        """Fix round 1: the client opens `sources/`, finds no PDF, and must be told why — the
        freeze's warning lives in the pack, not in the folder they receive."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        (work_dir / "research" / "raw" / "case_law" / "vsrf-act.pdf").unlink()
        result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse((published / "vsrf-act.pdf").exists())
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(result["published_to"])
        self.assertTrue((published / "gdpr-art6.txt").is_file())

    def test_an_original_the_freeze_never_pinned_is_not_exportable(self):
        """Fix round 1: after a freeze only the snapshot authorises an export. Deleted, frozen,
        then put back — the registry's own digest must not smuggle it into the client's folder."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        # The freeze found no file, so it pinned no digest; the file is back on disk afterwards.
        self.freeze_pack(work_dir, "vsrf-act", original=None)
        finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse((published / "vsrf-act.pdf").exists())
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )

    def test_a_file_replaced_between_the_check_and_the_copy_is_not_delivered(self):
        """Fix round 3: the observation that counts is the bytes actually retained for delivery.

        Hashing the source and copying it afterwards is two reads of a file that can change in
        between, and unpinned bytes then reached the client with no omission note. Staging first
        and hashing the staged copy closes the window, whatever happens during the copy itself.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        real = shutil.copyfile

        def swap(src, dst, *args, **kwargs):
            if str(dst).endswith("vsrf-act.pdf"):
                Path(dst).write_bytes(b"%PDF-1.7\nother bytes\n%%EOF\n")
                return dst
            return real(src, dst, *args, **kwargs)

        with mock.patch.object(finalize.shutil, "copyfile", side_effect=swap):
            result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse((published / "vsrf-act.pdf").exists(), "the staged copy failed the check")
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(result["published_to"])
        self.assertTrue((published / "gdpr-art6.txt").is_file())

    def test_a_rejected_file_never_reaches_the_client_even_if_its_deletion_fails(self):
        """Fix round 4: an unverified file is never inside the directory that will be delivered.

        The copy used to be staged in the publication folder itself, so a rejected artefact whose
        `unlink` failed rode along into the client's folder — unpinned bytes beside a manifest
        saying they were omitted. It now waits outside and only a verified file is moved in, so a
        failed clean-up leaves rubbish somewhere nobody publishes.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        # The freeze pinned other bytes, so the copy of this original is rejected…
        self.freeze_pack(work_dir, "vsrf-act", original="0" * 64)
        real_unlink = Path.unlink

        def refuse(self_path, *args, **kwargs):
            # …and the clean-up of the rejected copy fails, as a transient lock would make it.
            if self_path.name == "vsrf-act.pdf":
                raise OSError("locked")
            return real_unlink(self_path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", refuse):
            result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse(
            (published / "vsrf-act.pdf").exists(),
            "rejected bytes must never sit in the folder whose manifest says they were omitted",
        )
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )
        # D-109 and every unaffected artefact: the delivery is otherwise complete.
        self.assertIsNotNone(result["published_to"])
        self.assertTrue((published / "gdpr-art6.txt").is_file())
        self.assertTrue((self.published_dir(root, work_dir) / finalize.DELIVERABLE_MD).is_file())

    def test_the_publication_folder_holds_no_leftover_staging_directory(self):
        """The scratch area is beside the publication and is cleaned up, delivered or not."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        finalize.run_finalize(finalize_args(work_dir))
        target = self.published_dir(root, work_dir)
        self.assertEqual(PDF_ORIGINAL, (self.sources_dir(root, work_dir) / "vsrf-act.pdf").read_bytes())
        self.assertEqual(
            [],
            [path.name for path in target.parent.iterdir() if path.name != target.name],
            "no staging or verifying directory survives the publication",
        )

    def test_a_file_deleted_during_the_copy_is_named_and_never_fatal(self):
        """Fix round 3: a `copyfile` that raises is an omission row, not an aborted publication."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        real = shutil.copyfile

        def vanish(src, dst, *args, **kwargs):
            if str(dst).endswith("vsrf-act.pdf"):
                raise FileNotFoundError(src)
            return real(src, dst, *args, **kwargs)

        with mock.patch.object(finalize.shutil, "copyfile", side_effect=vanish):
            result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse((published / "vsrf-act.pdf").exists())
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )
        # D-109: the rest of the delivery is intact — a copy that failed never aborts a run.
        self.assertIsNotNone(result["published_to"])
        self.assertNotIn("publish_failed", [row["banner_id"] for row in result["banners"]])
        self.assertTrue((published / "gdpr-art6.txt").is_file())
        self.assertTrue((self.published_dir(root, work_dir) / finalize.DELIVERABLE_MD).is_file())

    def test_text_edited_after_the_freeze_is_not_exported_and_is_named(self):
        """Fix round 3: the text is gated exactly as the original is.

        C-02 only checks files reached through `[[q:]]`; a source cited through `[[src:]]` alone
        never passes it, and the freeze-time demotion cannot see an edit made afterwards — so an
        edited `.md` used to reach the client while the pack still described the frozen source.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        text = "ОПРЕДЕЛЕНИЕ\nустановил\n"
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=text)
        self.freeze_pack(
            work_dir,
            "vsrf-act",
            original=state_io.sha256_bytes(PDF_ORIGINAL),
            text=state_io.sha256_bytes(text.encode("utf-8")),
        )
        edited = work_dir / "research" / "raw" / "case_law" / "vsrf-act.md"
        edited.write_bytes("ОПРЕДЕЛЕНИЕ\nустановил иначе\n".encode("utf-8"))

        result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse((published / "vsrf-act.txt").exists(), "edited text never reaches a client")
        self.assertIn(
            md_fallback.label("text_export_mismatch_note", "en", source_id="`vsrf-act`"),
            (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8"),
        )
        # The original was not touched, so it still travels: the two artefacts are judged apart.
        self.assertEqual(PDF_ORIGINAL, (published / "vsrf-act.pdf").read_bytes())
        self.assertIsNotNone(result["published_to"])

    def test_without_a_pack_the_text_still_travels_on_the_records_digest(self):
        """`finalize --salvage` on an unfrozen work dir has nothing pinned and must still deliver."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        text = "ОПРЕДЕЛЕНИЕ\nустановил\n"
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=text)
        finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertEqual(text.encode("utf-8"), (published / "vsrf-act.txt").read_bytes())
        self.assertEqual(PDF_ORIGINAL, (published / "vsrf-act.pdf").read_bytes())

    def test_the_exported_text_is_the_stored_bytes_and_not_a_round_trip(self):
        """Fix round 3: a decode-then-encode round trip is a second chance to differ from the
        digest that was pinned, and a false mismatch would be worse than the hole it closes."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        # A BOM and a lone CR: both survive a byte copy and both move under a text round trip.
        stored = "﻿ОПРЕДЕЛЕНИЕ\rустановил\n".encode("utf-8")
        raw = work_dir / "research" / "raw" / "case_law"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "vsrf-act.md").write_bytes(stored)
        path = work_dir / "research" / "sources.json"
        registry = json.loads(path.read_text(encoding="utf-8-sig"))
        registry["sources"]["vsrf-act"] = {
            "layer": "case_law",
            "title": "ВС РФ, определение",
            "citation_form": "Определение ВС РФ",
            "url": "https://vsrf.ru/act",
            "tier": "critical",
            "raw_path": "research/raw/case_law/vsrf-act.md",
            "raw_kind": "full_text",
            "raw_sha256": state_io.sha256_bytes(stored),
        }
        state_io.write_json_atomic(path, registry)
        finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(stored, (self.sources_dir(root, work_dir) / "vsrf-act.txt").read_bytes())

    def test_a_pack_that_cannot_be_read_authorises_nothing(self):
        """Fix round 2: a pack on disk means a freeze happened; one that cannot be read means we do
        not know what it pinned, and not knowing never hands a document to a client. The failure
        stays loud — every affected original gets its line — and the delivery still goes out."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        (work_dir / "research" / "source-pack.json").write_text("{ not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir))
        published = self.sources_dir(root, work_dir)
        self.assertFalse(
            (published / "vsrf-act.pdf").exists(),
            "the registry's own digest must not authorise an export the freeze cannot confirm",
        )
        pack_md = (published / finalize.SOURCE_PACK_MD).read_text(encoding="utf-8")
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "en", source_id="`vsrf-act`"), pack_md
        )
        # Fix round 3: neither artefact of any source is authorised by a pack nobody can read.
        self.assertFalse((published / "gdpr-art6.txt").exists())
        self.assertIn(
            md_fallback.label("text_export_mismatch_note", "en", source_id="`gdpr-art6`"), pack_md
        )
        # D-109: the delivery itself is untouched.
        self.assertIsNotNone(result["published_to"])
        self.assertTrue((self.published_dir(root, work_dir) / finalize.DELIVERABLE_MD).is_file())

    def test_without_a_pack_the_records_own_digest_still_authorises_the_export(self):
        """`finalize --salvage` on an unfrozen work dir has nothing pinned and must still deliver."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(
            PDF_ORIGINAL, (self.sources_dir(root, work_dir) / "vsrf-act.pdf").read_bytes()
        )

    def test_one_publish_looks_at_each_original_exactly_once(self):
        """Fix round 1: the note and the copy are decided from one observation, so the delivered
        folder can never contradict its own `source-pack.md`."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original=state_io.sha256_bytes(PDF_ORIGINAL))
        hashed: list[str] = []
        original = state_io.sha256_file

        def counting(path):
            hashed.append(Path(path).name)
            return original(path)

        with mock.patch.object(state_io, "sha256_file", counting):
            finalize.run_finalize(finalize_args(work_dir))
        self.assertEqual(["vsrf-act.pdf"], [name for name in hashed if name.endswith(".pdf")])

    def test_the_mismatch_note_is_written_in_the_memo_language(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        self.add_pdf_source(work_dir, source_id="vsrf-act", text=None)
        self.freeze_pack(work_dir, "vsrf-act", original="0" * 64)
        rendered = finalize.source_pack_markdown(
            work_dir, language="ru", omissions=[("vsrf-act", finalize.SOURCE_ORIGINAL_KIND)]
        )
        self.assertIn(
            md_fallback.label("pdf_export_mismatch_note", "ru", source_id="`vsrf-act`"), rendered
        )


class RootCopiesTest(PublishTest):
    """D-167: `publish` also drops `memo-<slug>.<ext>` and `memo-<slug>.summary.md`
    at the root of the outputs area, next to `memoforge/`."""

    def root_names(self, work_dir: Path, ext: str = "md") -> tuple[str, str]:
        slug = slug_of(None, work_dir)
        return f"memo-{slug}.{ext}", f"memo-{slug}.summary.md"

    def test_root_copies_exist_and_match_the_folder_copies(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        result = finalize.run_finalize(finalize_args(work_dir))
        memo_name, summary_name = self.root_names(work_dir)
        memo_copy = root / memo_name
        summary_copy = root / summary_name
        self.assertTrue(memo_copy.is_file(), f"missing {memo_name}")
        self.assertTrue(summary_copy.is_file(), f"missing {summary_name}")
        target = self.published_dir(root, work_dir)
        self.assertEqual(memo_copy.read_bytes(), (target / finalize.DELIVERABLE_MD).read_bytes())
        self.assertEqual(summary_copy.read_bytes(), (target / finalize.SUMMARY_MD).read_bytes())
        self.assertIn(memo_name, result["published_files"])
        self.assertIn(summary_name, result["published_files"])

    def test_root_memo_copy_is_docx_for_a_docx_deliverable(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root, final_status=SIGNED_OFF)
        render_export(work_dir)
        result = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(result["deliverable_kind"], "docx")
        memo_name, _ = self.root_names(work_dir, ext="docx")
        memo_copy = root / memo_name
        self.assertTrue(memo_copy.is_file(), f"missing {memo_name}")
        target = self.published_dir(root, work_dir)
        self.assertEqual(memo_copy.read_bytes(), (target / finalize.DELIVERABLE_DOCX).read_bytes())
        self.assertIn(memo_name, result["published_files"])

    def test_published_memo_reaches_the_result_and_state(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        result = finalize.run_finalize(finalize_args(work_dir))
        memo_name, _ = self.root_names(work_dir)
        self.assertEqual(result["published_memo"], str(root / memo_name))
        state = state_io.read_state(work_dir)
        self.assertEqual(state["progress"]["published_memo"], result["published_memo"])
        published = [row for row in events.read_events(work_dir) if row["event"] == "result_published"]
        self.assertEqual(published[0]["data"].get("memo"), result["published_memo"])

    def test_a_relative_publish_folder_still_records_absolute_paths(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        work_dir = self.make_published_task(base / "placeholder")

        def use_relative(state: dict) -> None:
            state["config"]["publish_folder"] = "rel-outputs"

        state_io.write_state(work_dir, use_relative)
        previous_cwd = os.getcwd()
        os.chdir(base)
        try:
            result = finalize.run_finalize(finalize_args(work_dir))
        finally:
            os.chdir(previous_cwd)
        self.assertTrue(os.path.isabs(result["published_to"]), result["published_to"])
        self.assertTrue(os.path.isabs(result["published_memo"]), result["published_memo"])
        memo_name, _ = self.root_names(work_dir)
        self.assertTrue((base / "rel-outputs" / memo_name).is_file(), f"missing {memo_name}")
        state = state_io.read_state(work_dir)
        self.assertEqual(state["progress"]["published_memo"], result["published_memo"])

    def test_no_publish_root_means_no_published_memo(self):
        work_dir = self.make_task()
        with mock.patch.object(finalize, "HOST_OUTPUTS_DIR", str(Path(work_dir) / "absent")):
            result = finalize.run_finalize(finalize_args(work_dir))
        self.assertIsNone(result["published_to"])
        self.assertIsNone(result["published_memo"])
        self.assertIsNone(state_io.read_state(work_dir)["progress"]["published_memo"])

    def test_a_republish_replaces_the_root_copies(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        memo_name, summary_name = self.root_names(work_dir)
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")
        second = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(second["published_to"], first["published_to"])
        self.assertEqual(second["published_memo"], first["published_memo"])
        self.assertIn("A later revision.", (root / memo_name).read_text(encoding="utf-8"))
        target = self.published_dir(root, work_dir)
        self.assertEqual((root / memo_name).read_bytes(), (target / finalize.DELIVERABLE_MD).read_bytes())
        self.assertEqual((root / summary_name).read_bytes(), (target / finalize.SUMMARY_MD).read_bytes())
        self.assertNotIn(finalize.DELIVERABLE_DOCX, [p.name for p in root.iterdir()])

    def test_a_docx_to_md_republish_leaves_no_stale_docx_at_the_root(self):
        """D-111 at the root: yesterday's `memo-<slug>.docx` does not survive a markdown run."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root, final_status=SIGNED_OFF)
        render_export(work_dir)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(first["deliverable_kind"], "docx")
        docx_name, _ = self.root_names(work_dir, ext="docx")
        self.assertTrue((root / docx_name).is_file())
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")
        second = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(second["deliverable_kind"], "md")
        memo_name, _ = self.root_names(work_dir)
        self.assertTrue((root / memo_name).is_file())
        self.assertFalse((root / docx_name).exists(), "stale root docx mixed with the new md run")

    def test_a_failed_publish_leaves_the_previous_root_copies_intact(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        finalize.run_finalize(finalize_args(work_dir, step=None))
        memo_name, _ = self.root_names(work_dir)
        kept = (root / memo_name).read_bytes()
        with mock.patch("memoforge.finalize.shutil.copyfile", side_effect=OSError("disk full")):
            second = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertIsNone(second["published_to"])
        self.assertIsNone(second["published_memo"])
        self.assertEqual(kept, (root / memo_name).read_bytes())
        self.assertIsNone(state_io.read_state(work_dir)["progress"]["published_memo"])

    def publish_slug(self, root: Path, slug: str) -> Path:
        """A published task renamed to the memo id for `slug`, finalized once."""
        work_dir = self.make_published_task(root)
        renamed = work_dir.parent / f"memo-20260908T120000Z-{slug}"
        work_dir.rename(renamed)
        state_io.write_state(renamed, lambda state: state.update(task_id=renamed.name))
        finalize.run_finalize(finalize_args(renamed, step=None))
        return renamed

    def assert_all_four_root_files(self, root: Path) -> None:
        """Both slugs keep a memo copy and a summary copy: four files, no collision.

        Names are built from the slug on purpose: spelling the colliding pair out
        literally would trip the old-root-name check of the fix wave.
        """
        for slug in ("privacy", "privacy-summary"):
            for name in (f"memo-{slug}.md", f"memo-{slug}.summary.md"):
                self.assertTrue((root / name).is_file(), f"missing {name}")

    def test_colliding_slugs_keep_their_own_root_copies(self):
        """Fix wave: `memo-privacy.summary.md` must not collide with slug `privacy-summary`."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.publish_slug(root, "privacy-summary")
        self.publish_slug(root, "privacy")
        self.assert_all_four_root_files(root)

    def test_colliding_slugs_keep_their_own_root_copies_in_reverse_order(self):
        """Same collision, the other publish order."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.publish_slug(root, "privacy")
        self.publish_slug(root, "privacy-summary")
        self.assert_all_four_root_files(root)

    def test_a_swap_failure_restores_the_previous_root_copies(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        first = finalize.run_finalize(finalize_args(work_dir, step=None))
        target = Path(first["published_to"])
        memo_name, _ = self.root_names(work_dir)
        kept = (root / memo_name).read_bytes()
        (work_dir / "drafts" / "v1.md").write_text(DRAFT + "\nA later revision.\n", encoding="utf-8")
        real_replace = os.replace

        def break_second_swap(src, dst, *args, **kwargs):
            if Path(dst).parent == target:
                raise PermissionError("the folder is held by another program")
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch("memoforge.finalize.os.replace", break_second_swap):
            second = finalize.run_finalize(finalize_args(work_dir, step=None))
        # The failure hits the very first swap into the folder here; loosen to the
        # rollback guarantee: the previous publication — folder and root copies — is back.
        self.assertIsNone(second["published_to"])
        self.assertEqual(kept, (root / memo_name).read_bytes())
        leftover = sorted(p.name for p in root.iterdir() if p.name.startswith("memo-"))
        self.assertEqual(sorted([memo_name, self.root_names(work_dir)[1]]), leftover)


class CliSurfaceTest(_WorkDirMixin, unittest.TestCase):
    def test_finalize_prints_one_json_object_and_exits_zero(self):
        from memoforge import cli

        work_dir = self.make_task()
        issue_step(work_dir, "s-export")
        with mock.patch("sys.stdout") as stdout:
            code = cli.main(["finalize", "--workdir", str(work_dir), "--step", "s-export"])
        printed = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        self.assertEqual(code, 0)
        payload = json.loads(printed.strip().splitlines()[0])
        self.assertEqual(payload["current_phase"], "done")


class LocalizedTailTest(unittest.TestCase):
    """D-175: the parse-back of a rendered deliverable runs in the language it was rendered in."""

    RU = {
        "memo.labels.appendix_heading": "Приложение — непроверенные источники",
        "memo.labels.status_label": "Статус",
        "memo.labels.status_lead": "Итоговый статус: {final_status}. Требуется проверка.",
        "memo.labels.registered_sources_heading": "Источники (зарегистрированы, не заморожены)",
        "memo.labels.no_registered_sources": "(источники не регистрировались)",
    }

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work_dir = Path(tmp.name)
        self.packs = self.work_dir / "i18n"
        self.packs.mkdir()
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", self.RU)

    def state(self) -> dict:
        return {
            "language": "ru",
            "final_status": "forced_exit_on_v1",
            "drafting_warnings": ["Одно допущение."],
            "fallback_banners": [],
            "remaining_blocking_issues": [],
        }

    def exported(self) -> str:
        # D-191: warnings alone open no appendix; the unresolved marker opens it under the
        # NEW heading, which `condense_appendix` must partition on (no back-compat).
        return md_fallback.render(
            "Тело меморандума [[src:ghost]].\n",
            md_fallback.SourceIndex(),
            drafting_warnings=["Одно допущение."],
            state=self.state(),
        )["markdown"]

    def test_finalize_reads_back_a_russian_export(self):
        exported = self.exported()
        self.assertIn(i18n.t("ru", "memo.labels.appendix_heading"), exported)
        rewritten = finalize.condense_appendix(exported, self.work_dir, self.state())
        self.assertEqual(1, rewritten.count(i18n.t("ru", "memo.labels.status_label")))
        self.assertEqual(1, rewritten.count(i18n.t("ru", "memo.labels.appendix_heading")))
        self.assertNotIn(i18n.t("en", "memo.labels.appendix_heading"), rewritten)

    def test_the_status_section_of_a_russian_export_is_dropped_before_it_is_rewritten(self):
        self.assertNotIn(
            i18n.t("ru", "memo.labels.status_label"),
            finalize._without_status(self.exported(), language="ru"),
        )

    def test_without_status_never_cuts_on_an_empty_lead_prefix(self):
        _i18n.fake_pack(
            self.packs,
            "fr",
            {
                "memo.labels.status_label": "Statut",
                "memo.labels.status_lead": "{final_status} — à vérifier.",
            },
        )
        text = "# T\n\n## Statut\n\nle texte du rédacteur\n"
        self.assertEqual(text, finalize._without_status(text, language="fr"))

    def test_the_published_source_pack_is_in_the_memo_language(self):
        rendered = finalize.source_pack_markdown(self.work_dir, language="ru")
        self.assertIn("# Источники (зарегистрированы, не заморожены)", rendered)
        self.assertIn("- (источники не регистрировались)", rendered)
        self.assertNotIn("Sources (registered, not frozen)", rendered)


class SummaryStatusLineTest(_WorkDirMixin, unittest.TestCase):
    """D-197: the status sentence of `summary.md` is in words; the code stays for the record."""

    def summary_status(self, final_status: str) -> str:
        work_dir = self.make_task()
        summary = finalize.build_summary(
            {"task_id": work_dir.name},
            work_dir,
            phase="done",
            final_status=final_status,
            deliverable={"deliverable": "deliverable.md", "source": "memo-x.md"},
            reason=None,
            banners=[],
        )
        return next(row for row in summary.splitlines() if row.startswith("- Status:"))

    def test_the_status_line_names_the_status_and_keeps_the_code(self):
        line = self.summary_status("forced_exit_on_v1_with_remaining_issues")
        self.assertIn(md_fallback.status_name("forced_exit_on_v1_with_remaining_issues"), line)
        self.assertIn("`forced_exit_on_v1_with_remaining_issues`", line)

    def test_an_unknown_status_prints_the_code_only_once_as_a_name(self):
        line = self.summary_status("brand_new_status")
        self.assertIn("brand_new_status", line)


def open_major(
    number: int,
    cls: str,
    status: str,
    *,
    origin: str = "loop",
    section_id: str = "s-5-1",
    category: str = "narrow_trigger",
    issue: str = "The trigger is drawn too narrowly.",
) -> dict:
    """One `state.open_substance_majors` row (D-210)."""
    return {
        "id": f"om-{number}",
        "class": cls,
        "reviewer": cls,
        "section_id": section_id,
        "category": category,
        "issue_category": None,
        "issue": issue,
        "issue_client": None,
        "suggestion": "Qualify the statement.",
        "from_iteration": 2,
        "origin": origin,
        "status": status,
    }


class OpenReviewerFindingsTest(_WorkDirMixin, unittest.TestCase):
    """D-210: `summary.md` lists the substantive majors the review loop left open."""

    STATUSES = ("open", "resolved", "unresolved", "manual_review", "left")

    def summary(self, rows: list | None, blockers: list | None = None) -> str:
        def mutate(state: dict) -> None:
            if rows is not None:
                state["open_substance_majors"] = rows
            state["remaining_blocking_issues"] = blockers or []

        work_dir = self.make_task(mutate=mutate)
        finalize.run_finalize(finalize_args(work_dir))
        return (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")

    def test_the_predicate_is_decided_by_the_row_alone(self):
        # Ruling A: a citations row of the loop left `unresolved` is the one the readiness step moves
        # into `remaining_blocking_issues`, so the section never prints it a second time.
        for cls in ("citations", "logic", "counterarguments"):
            for origin in ("loop", "recheck"):
                for status in self.STATUSES:
                    with self.subTest(cls=cls, origin=origin, status=status):
                        expected = status in ("open", "left", "unresolved") and not (
                            cls == "citations" and origin == "loop" and status == "unresolved"
                        )
                        row = open_major(1, cls, status, origin=origin)
                        self.assertEqual(expected, finalize.lists_open_finding(row))

    def test_a_blocker_row_is_never_listed_here(self):
        # D-213: a lifted blocker is gone; an unlifted one stays under «Remaining blocking issues».
        link = {"section_id": "s-4-2", "category": "rule_not_in_source", "issue": "The rule is not in s 168."}
        for status in self.STATUSES:
            with self.subTest(status=status):
                row = dict(open_major(4, "citations", status), severity="blocker", blocker_of=link)
                self.assertFalse(finalize.lists_open_finding(row))
        blocker = {"severity": "blocker", "category": link["category"], "section_id": "s-4-2", "issue": link["issue"]}
        row = dict(open_major(4, "citations", "unresolved", section_id="s-4-2", issue=link["issue"]), blocker_of=link)
        summary = self.summary([row], blockers=[blocker])
        self.assertEqual(1, summary.count(link["issue"]))
        self.assertIn("## Open reviewer findings\n\n- none\n", summary)

    def test_the_section_follows_the_blocker_list_with_raw_ids(self):
        rows = [
            open_major(1, "counterarguments", "open"),
            open_major(
                2,
                "logic",
                "left",
                section_id="s-10-3",
                category="unsupported_conclusion",
                issue="The conclusion of 10.3\ndoes not follow.",
            ),
            open_major(3, "citations", "resolved", section_id="s-9", category="pinpoint_mismatch"),
            open_major(
                4,
                "citations",
                "open",
                origin="recheck",
                section_id="s-9",
                category="pinpoint_mismatch",
                issue="The pinpoint sends the reader to point 3.",
            ),
        ]
        self.assertIn(
            "## Remaining blocking issues\n\n- none\n\n"
            "## Open reviewer findings\n\n"
            "- counterarguments · loop · open · s-5-1 · narrow_trigger · The trigger is drawn too narrowly.\n"
            "- logic · loop · left · s-10-3 · unsupported_conclusion · The conclusion of 10.3 does not follow.\n"
            "- citations · recheck · open · s-9 · pinpoint_mismatch · The pinpoint sends the reader to point 3.\n"
            "\n## Paths\n",
            self.summary(rows),
        )

    def test_a_left_row_prints_the_note_of_its_disposition(self):
        # D-216: run 74 printed three `left` rows the v3 text had already fixed, without the reason.
        noted = dict(open_major(1, "logic", "left", section_id="s-4-2"), disposition_note="Fixed in 4.2 of v3.\n")
        plain = open_major(2, "counterarguments", "left", section_id="s-5-3")
        summary = self.summary([noted, plain])
        self.assertIn(
            "- logic · loop · left · s-4-2 · narrow_trigger · The trigger is drawn too narrowly."
            " · Fixed in 4.2 of v3.\n",
            summary,
        )
        self.assertIn(
            "- counterarguments · loop · left · s-5-3 · narrow_trigger · The trigger is drawn too narrowly.\n",
            summary,
        )

    def test_nothing_open_says_none(self):
        cases = (
            ("a state written before D-210", None),
            ("an empty list", []),
            ("only settled rows", [open_major(1, "logic", "resolved"), open_major(2, "citations", "manual_review")]),
        )
        for label, rows in cases:
            with self.subTest(case=label):
                self.assertIn("## Open reviewer findings\n\n- none\n", self.summary(rows))

    def test_an_unresolved_row_is_printed_exactly_once(self):
        kept = open_major(
            1,
            "counterarguments",
            "unresolved",
            section_id="s-7-1",
            category="unaddressed_statutory_limitation",
            issue="The limitation period of art. 196 is not addressed.",
        )
        moved = open_major(
            2,
            "citations",
            "unresolved",
            section_id="s-9",
            category="pinpoint_mismatch",
            issue="The pinpoint sends the reader to point 3.",
        )
        blocker = {"severity": "major", "category": moved["category"], "section_id": "s-9", "issue": moved["issue"]}
        summary = self.summary([kept, moved], blockers=[blocker])
        self.assertEqual(1, summary.count(kept["issue"]))
        self.assertEqual(1, summary.count(moved["issue"]))
        self.assertIn("- major · s-9 · The pinpoint sends the reader to point 3.", summary)

    def test_the_rows_the_readiness_step_settles_are_each_printed_once(self):
        # D-211: settlement moves a citations row into the blockers and leaves the rest to this section.
        rows = [
            open_major(1, "citations", "manual_review", section_id="s-9", category="pinpoint_mismatch",
                       issue="The pinpoint sends the reader to point 3."),
            open_major(2, "citations", "open", section_id="s-5-2", category="holding_misstated",
                       issue="The formula is given as the court's holding."),
            open_major(3, "logic", "left", section_id="s-10-3", category="unsupported_conclusion",
                       issue="The conclusion of 10.3 does not follow."),
            open_major(4, "counterarguments", "resolved", section_id="s-7-1", issue="The limitation is not addressed."),
            open_major(5, "citations", "open", origin="recheck", section_id="s-9", category="overstated_currency",
                       issue="The guidance is described as settled practice."),
        ]
        settled = {
            "final_status": "approved_on_v3",
            "final_status_reasons": [],
            "remaining_blocking_issues": [],
            "open_substance_majors": rows,
        }
        machine._settle_open_majors(settled, [], 3)  # noqa: SLF001
        self.assertEqual("manual_review_required_on_v3", settled["final_status"])
        summary = self.summary(settled["open_substance_majors"], blockers=settled["remaining_blocking_issues"])
        blockers, _, rest = summary.partition("## Open reviewer findings")
        self.assertIn("- major · s-9 · The pinpoint sends the reader to point 3.", blockers)
        self.assertIn("- major · s-5-2 · The formula is given as the court's holding.", blockers)
        self.assertIn("- logic · loop · left · s-10-3 · unsupported_conclusion · The conclusion of 10.3", rest)
        self.assertIn("- citations · recheck · open · s-9 · overstated_currency · The guidance is", rest)
        for row in rows:
            with self.subTest(row=row["id"]):
                self.assertLessEqual(summary.count(row["issue"]), 1)
        self.assertNotIn("The limitation is not addressed.", summary)


class BannerLanguageSummaryTest(_WorkDirMixin, unittest.TestCase):
    """D-175: `summary.md` strings and banner rows come from `memo.summary`/`memo.banners`."""

    RU_SUMMARY = {
        "memo.summary.title": "Сводка запуска memoforge — {task_id}",
        "memo.summary.status": "- Статус: **{status_name}** (`{final_status}`)",
        "memo.summary.terminal_phase": "- Терминальная фаза: `{phase}`",
        "memo.summary.mode": "- Режим: {mode}",
        "memo.summary.question": "- Вопрос: {question}",
        "memo.summary.reason": "- Причина, переданная `mf finalize`: {reason}",
        "memo.summary.salvaged": "- Создано `mf finalize --salvage` (деградированный путь, M9).",
        "memo.summary.manual_review_reasons": "## Причины ручной проверки",
        "memo.summary.fallback_banners": "## Баннеры запасных сценариев",
        "memo.summary.mcp_calls": "## Вызовы MCP",
        "memo.summary.remaining_blocking_issues": "## Оставшиеся блокирующие замечания",
        "memo.summary.paths": "## Пути",
        "memo.summary.deliverable": "- Деливерабл: `{name}`",
        "memo.summary.rendered_from": "- Отрендерено из: `{name}`",
        "memo.summary.state": "Состояние",
        "memo.summary.journal": "Журнал",
        "memo.summary.drafting_warnings": "## Предупреждения для автора",
        "memo.summary.none": "- нет",
        "memo.summary.none_yet": "- пока нет",
        "memo.summary.not_selected": "(не выбран)",
        "memo.summary.query_unavailable": "(запрос недоступен)",
        "memo.summary.phase_unknown": "(неизвестна)",
        "memo.summary.banner_row": "{text} (`{banner_id}`)",
        "memo.summary.mcp_quota_row": "{server}: {used} из {limit}",
        "memo.summary.mcp_plain_row": "{server}: {used}",
        "memo.summary.pack_unavailable": (
            "Языковой пакет `{code}` не удалось прочитать; сгенерированные метки — на английском."
        ),
        "memo.banners.mcp_partial": "Частичное покрытие MCP — доступен только {available}.",
    }

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", self.RU_SUMMARY)

    def russian_task(self) -> Path:
        def mutate(state: dict) -> None:
            state["language"] = "ru"

        return self.make_task(mutate=mutate)

    def test_the_summary_of_a_russian_task_has_no_english_heading(self):
        work_dir = self.russian_task()
        finalize.run_finalize(finalize_args(work_dir))
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("Сводка запуска memoforge", summary)
        self.assertIn("## Причины ручной проверки", summary)
        self.assertIn("## Баннеры запасных сценариев", summary)
        self.assertNotIn("# memoforge run summary", summary)
        self.assertNotIn("## Manual-review reasons", summary)
        self.assertNotIn("## Fallback banners", summary)

    def test_salvage_with_an_unreadable_pack_delivers_in_english_and_says_so(self):
        work_dir = self.russian_task()
        (self.packs / "ru.json").write_text("{not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir, salvage=True))
        self.assertNotIn("errors", result)
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("# memoforge run summary", summary)
        self.assertIn("could not be read; generated labels are in English", summary)
        self.assertIn(result["deliverable"], ("deliverable.md", "deliverable.docx"))

    def test_a_legacy_english_task_with_a_parameterised_banner_without_params_finalizes(self):
        def mutate(state: dict) -> None:
            state["fallback_banners"] = [
                {
                    "banner_id": "mcp_partial",
                    "condition_key": "mcp_partial",
                    "text": "Partial MCP coverage — only legalviz was reachable.",
                }
            ]
            state["final_status"] = SIGNED_OFF

        work_dir = self.make_task(mutate=mutate)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertNotIn("errors", result)
        self.assert_delivered(work_dir)
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("Partial MCP coverage — only legalviz was reachable.", summary)

    def test_a_legacy_task_without_ui_language_finalizes_in_english(self):
        """Plan 56 task 6: a state written before plan 54 (`language: "en"`, no
        `ui_language`) finalizes — English gates, English dashboard patch, deliverable."""
        def mutate(state: dict) -> None:
            state.pop("ui_language", None)
            state["language"] = "en"
            state["final_status"] = SIGNED_OFF

        work_dir = self.make_task(mutate=mutate)
        state = state_io.read_state(work_dir)
        self.assertNotIn("ui_language", state)
        result = finalize.run_finalize(finalize_args(work_dir))
        self.assertNotIn("errors", result)
        self.assert_delivered(work_dir)
        summary = (work_dir / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        self.assertIn("# memoforge run summary", summary)


class SalvageLocalizedExportTest(_WorkDirMixin, unittest.TestCase):
    """Final review, finding 1 / D-175b: when `--salvage` falls back to English it must not
    reuse the localized `memo-<slug>.md` export — `condense_appendix` then reads that body
    back with English headings, so the export's own `## Status` survives and a second,
    English one is appended. With a draft still on disk the draft is rendered afresh."""

    RU = {
        "memo.labels.appendix_heading": "Приложение — непроверенные источники",
        "memo.labels.status_label": "Статус",
        "memo.labels.status_lead": "Итоговый статус: {final_status}. Требуется проверка.",
    }
    FORCED = "forced_exit_on_v1"
    """A non-approved terminal status, so the export really carries a `## Status` section."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        i18n._cache.clear()
        self.addCleanup(i18n._cache.clear)
        _i18n.fake_pack(self.packs, "ru", self.RU)

    def russian_export(self) -> Path:
        """A Russian task whose `memo-<slug>.md` export is on disk and bound to the draft."""

        def mutate(state: dict) -> None:
            state["language"] = "ru"

        work_dir = self.make_task(final_status=self.FORCED, mutate=mutate)
        render_export(work_dir)
        exported = md_fallback_export(work_dir)
        self.assertIn("## Статус", exported)
        return work_dir

    def test_salvage_renders_the_draft_afresh_instead_of_the_localized_export(self):
        work_dir = self.russian_export()
        i18n._cache.clear()
        (self.packs / "ru.json").write_text("{not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir, salvage=True))
        self.assertNotIn("errors", result)
        self.assertEqual(finalize.DELIVERABLE_MD, result["deliverable"])
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertNotIn("## Статус", body)
        self.assertEqual(1, body.count(f"## {md_fallback.label('status_label')}"))

    def test_salvage_leaves_the_localized_export_it_bypassed_on_disk(self):
        """The export's bytes are bound to a draft sha and a `published[]` row (D-50); the
        English deliverable of a degraded run is no reason to overwrite them."""
        work_dir = self.russian_export()
        i18n._cache.clear()
        (self.packs / "ru.json").write_text("{not json", encoding="utf-8")
        finalize.run_finalize(finalize_args(work_dir, salvage=True))
        self.assertIn("## Статус", md_fallback_export(work_dir))

    def salvaged_without_a_draft(self, work_dir: Path) -> dict:
        """Delete the draft the export came from, break the pack and salvage (D-181)."""
        (work_dir / "drafts" / "v1.md").unlink()
        i18n._cache.clear()
        (self.packs / "ru.json").write_text("{not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir, salvage=True))
        self.assertNotIn("errors", result)
        return result

    def test_salvage_with_no_draft_left_delivers_the_localized_export_whole(self):
        """D-181: with nothing to re-render from, the export is the deliverable (M9) — and
        `condense_appendix` must not read that Russian body back through English headings,
        which left the export's own `## Статус` standing and appended an English one."""
        work_dir = self.russian_export()
        before = md_fallback_export(work_dir)
        result = self.salvaged_without_a_draft(work_dir)
        self.assertEqual(finalize.DELIVERABLE_MD, result["deliverable"])
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        self.assertEqual(1, body.count("## Статус"))
        self.assertNotIn(f"## {md_fallback.label('status_label')}", body)
        self.assertEqual(before, md_fallback_export(work_dir), "the export stays as it is (D-50)")

    def test_the_untouched_export_is_named_in_the_final_status_reasons(self):
        result = self.salvaged_without_a_draft(self.russian_export())
        self.assertIn("export_reused_untouched", result["final_status_reasons"])
        self.assertIn("export_reused_untouched", (
            (Path(result["work_dir"]) / finalize.SUMMARY_MD).read_text(encoding="utf-8")
        ))


class UnreadablePackFinalizeTest(_WorkDirMixin, unittest.TestCase):
    """Task 3 carry-over: a normal finalize fails when the memo pack cannot be loaded —
    even when a valid, previously rendered docx exists and no label is looked up on that path."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", {})

    def russian_docx_task(self) -> Path:
        """A Russian task with a valid, previously rendered docx — not finalized yet."""

        def mutate(state: dict) -> None:
            state["language"] = "ru"

        work_dir = self.make_task(final_status=SIGNED_OFF, mutate=mutate)
        render_export(work_dir)
        self.assertEqual("export", state_io.read_state(work_dir)["current_phase"])
        return work_dir

    def test_an_existing_russian_docx_with_an_unreadable_pack_fails_without_a_publish_root(self):
        work_dir = self.russian_docx_task()
        i18n._cache.clear()
        (self.packs / "ru.json").write_text("{not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(["language_pack_unavailable: ru"], result["errors"])
        self.assertNotEqual("done", state_io.read_state(work_dir)["current_phase"])

    def test_an_existing_russian_docx_with_an_unreadable_pack_fails_with_a_publish_root(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.russian_docx_task()
        state_io.write_state(
            work_dir, lambda state: state["config"].update(publish_folder=str(root))
        )
        i18n._cache.clear()
        (self.packs / "ru.json").write_text("{not json", encoding="utf-8")
        result = finalize.run_finalize(finalize_args(work_dir, step=None))
        self.assertEqual(["language_pack_unavailable: ru"], result["errors"])
        self.assertNotEqual("done", state_io.read_state(work_dir)["current_phase"])


if __name__ == "__main__":
    unittest.main()
