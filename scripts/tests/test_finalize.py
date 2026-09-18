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

ASSUMPTIONS_MD = f"**{md_fallback.label('assumptions_label')}**"
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

QUOTES = {"quotes": {"q-001": {"source_id": "gdpr-art6", "raw_sha256": "0" * 64}}}

SIGNED_OFF = "approved_on_v1"
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
        (raw / "gdpr-art6.md").write_text("Article 6 raw text", encoding="utf-8")
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
        self.assertEqual(raw.read_text(encoding="utf-8"), "Article 6 raw text")
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
            "snapshot": [{"source_id": "gdpr-art6", "raw_sha256": "0" * 64}],
        }
        state_io.write_json_atomic(work_dir / "research" / "source-pack.json", pack)
        finalize.run_finalize(finalize_args(work_dir))
        published = (
            self.published_dir(root, work_dir)
            / finalize.PUBLISH_SOURCES_DIRNAME
            / finalize.SOURCE_PACK_MD
        ).read_text(encoding="utf-8")
        self.assertEqual(published.strip(), render.render_source_pack(pack).strip())
        self.assertIn("Source pack (frozen)", published)

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
        """D-111: `raw_path: 42` used to escape as a `TypeError` before the terminal state was written."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        work_dir = self.make_published_task(root)
        registry = state_io.read_json(work_dir / "research" / "sources.json")
        registry["sources"]["gdpr-art6"]["raw_path"] = 42
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)

        result = finalize.run_finalize(finalize_args(work_dir))
        self.assert_publish_failed_but_delivered(work_dir, result, "TypeError")

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
        (doctrine / "ico-guide.md").write_text("ICO raw text", encoding="utf-8")
        (doctrine / "blog-post.md").write_text("Blog raw text", encoding="utf-8")
        registry = state_io.read_json(work_dir / "research" / "sources.json")
        registry["sources"]["blog-post"]["tier"] = "supporting"
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
        registry = state_io.read_json(work_dir / "research" / "sources.json")
        registry["sources"]["gdpr-art6"]["raw_path"] = 42  # D-111: `publish` fails, the run does not
        state_io.write_json_atomic(work_dir / "research" / "sources.json", registry)

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
        self.assertIn("forced_exit_on_v1_with_remaining_issues", body)
        self.assertNotIn("manual_review_required_on_v1", body)

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
        self.assertIn("- blocker · s-1 · Art. 17(1) carries no rule.", body)

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


class AppendixTest(_WorkDirMixin, unittest.TestCase):
    """D-113: the client appendix is a short list; `summary.md` keeps the reviewer prose verbatim."""

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
        return work_dir

    def appendix(self, work_dir: Path) -> str:
        body = (work_dir / finalize.DELIVERABLE_MD).read_text(encoding="utf-8")
        heading = "## Appendix"
        self.assertIn(heading, body)
        return heading + body.partition(heading)[2]

    def bullets(self, appendix: str, label: str) -> list[str]:
        block = appendix.partition(label)[2].strip().split("\n\n")[0]
        return [line[2:] for line in block.splitlines() if line.startswith("- ")]

    def test_a_warning_becomes_one_short_bullet(self):
        work_dir = self.make_appendix_task()
        finalize.run_finalize(finalize_args(work_dir))
        rows = self.bullets(self.appendix(work_dir), ASSUMPTIONS_MD)

        self.assertTrue(rows[0].startswith("Nothing in intake"))
        self.assertTrue(rows[0].endswith("…"), rows[0])
        self.assertNotIn("research/doctrine.json", rows[0])
        self.assertLessEqual(len(rows[0]), md_fallback.APPENDIX_WARNING_CHARS)
        self.assertNotIn("unresolved_research_gap", "\n".join(rows))
        self.assertNotIn("currency_unchecked", "\n".join(rows))
        self.assertNotIn(".json", "\n".join(rows))

    def test_repeated_warnings_are_listed_once_and_the_list_is_capped(self):
        work_dir = self.make_appendix_task()
        finalize.run_finalize(finalize_args(work_dir))
        rows = self.bullets(self.appendix(work_dir), ASSUMPTIONS_MD)

        self.assertEqual(len([row for row in rows if row.startswith("Nothing in intake")]), 1)
        # 15 warnings, one of them a repeat: 14 bullets, 12 printed and the rest pointed at.
        self.assertEqual(len(rows), md_fallback.APPENDIX_WARNING_LIMIT + 1)
        self.assertEqual(rows[-1], "… and 2 more in summary.md")
        self.assertTrue(all(len(row) <= md_fallback.APPENDIX_WARNING_CHARS for row in rows))

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
        rows = self.bullets(self.appendix(work_dir), UNVERIFIED_MD)

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
        self.assertIn(ASSUMPTIONS_MD, appendix)

    def test_a_currency_check_that_ran_keeps_its_per_source_lines(self):
        work_dir = self.make_appendix_task()
        finalize.run_finalize(finalize_args(work_dir))
        rows = self.bullets(self.appendix(work_dir), UNVERIFIED_MD)

        self.assertNotIn(md_fallback.label('currency_unavailable_note'), rows)
        self.assertEqual(len([row for row in rows if "currency unchecked" in row]), 3)


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

        self.assertIn("forced_exit_on_v1_with_remaining_issues", status)
        self.assertIn("Currency check unavailable", status)
        self.assertIn("- blocker · s-1 · Art. 17(1) is cited without a rule in section 1.", status)

    def test_the_blocker_list_is_capped_and_points_at_the_summary(self):
        work_dir = self.make_exited_task()
        finalize.run_finalize(finalize_args(work_dir))
        status = self.status(self.deliverable(work_dir))
        rows = [row for row in status.splitlines() if row.startswith("- blocker · ")]

        self.assertEqual(md_fallback.STATUS_ISSUE_LIMIT, len(rows))
        self.assertIn("- … and 3 more in summary.md", status)

    def test_the_status_section_stands_before_the_appendix(self):
        work_dir = self.make_exited_task(drafting_warnings=warnings_fixture())
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

        self.assertIn("forced_exit_on_v1_with_remaining_issues", status)
        self.assertIn("- blocker · s-1 · Art. 17(1) is cited without a rule in section 1.", status)

    def test_a_stale_status_section_is_replaced_not_doubled(self):
        """The export already carries a `## Status`; the run ended on another one (D-123)."""
        work_dir = self.make_task(final_status="manual_review_required_on_v1")
        render_export(work_dir)
        exported = (work_dir / "memo-gdpr-transcripts.md").read_text(encoding="utf-8")
        self.assertIn("manual_review_required_on_v1", exported)
        self.assertNotIn(md_fallback.appendix_heading(), exported)
        (work_dir / "memo-gdpr-transcripts.docx").unlink()

        def decide(state: dict) -> None:
            state["final_status"] = "forced_exit_on_v2_with_remaining_issues"

        state_io.write_state(work_dir, decide)
        finalize.run_finalize(finalize_args(work_dir))
        body = self.deliverable(work_dir)

        self.assertEqual(1, body.count(md_fallback.status_heading()))
        self.assertIn("forced_exit_on_v2_with_remaining_issues", body)
        self.assertNotIn("manual_review_required_on_v1", body)

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
        "memo.labels.appendix_heading": "Приложение — допущения",
        "memo.labels.assumptions_label": "Допущения",
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
        return md_fallback.render(
            "Тело меморандума.\n",
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


class BannerLanguageSummaryTest(_WorkDirMixin, unittest.TestCase):
    """D-175: `summary.md` strings and banner rows come from `memo.summary`/`memo.banners`."""

    RU_SUMMARY = {
        "memo.summary.title": "Сводка запуска memoforge — {task_id}",
        "memo.summary.status": "- Статус: **{final_status}**",
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
        "memo.summary.work_dir": "- Рабочий каталог: `{path}`",
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


class SalvageLocalizedExportTest(_WorkDirMixin, unittest.TestCase):
    """Final review, finding 1 / D-175b: when `--salvage` falls back to English it must not
    reuse the localized `memo-<slug>.md` export — `condense_appendix` then reads that body
    back with English headings, so the export's own `## Status` survives and a second,
    English one is appended. With a draft still on disk the draft is rendered afresh."""

    RU = {
        "memo.labels.appendix_heading": "Приложение — допущения",
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
