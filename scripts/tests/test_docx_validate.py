"""Tests for `mf docx validate` — the structural checks of ТЗ §5.5 and the `docx_invalid` demotion."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402
from memoforge import docx, i18n, state_io, task  # noqa: E402
from memoforge.docx import fallback, oscola, renderer, validate  # noqa: E402
from test_docx_fallback import RU_DELIVERABLE, issue_step  # noqa: E402
from test_docx_renderer import sample_index  # noqa: E402

DRAFT = (
    "# 1. Executive summary\n\n"
    "Lawful basis [[src:gdpr art 6(1)(f)]] and the leading case [[src:schrems para 168]].\n\n"
    "Later again [[src:gdpr art 7]].\n\n"
    "<!-- sources: generated -->\n"
)

UNRESOLVED_DRAFT = (
    "# 1. Executive summary\n\n"
    "Lawful basis [[src:gdpr art 6(1)(f)]] and a source nobody registered [[src:ghost]].\n\n"
    "<!-- sources: generated -->\n"
)


def repack(source: Path, target: Path, edits: dict) -> Path:
    """Copy a docx, rewriting the named parts through `edits[name](text) -> text | None`."""
    with zipfile.ZipFile(source) as reader:
        items = [(item, reader.read(item.filename)) for item in reader.infolist()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as writer:
        for item, blob in items:
            edit = edits.get(item.filename)
            if edit is not None:
                changed = edit(blob.decode("utf-8"))
                if changed is None:
                    continue
                blob = changed.encode("utf-8")
            writer.writestr(item.filename, blob)
    return target


class _Rendered(unittest.TestCase):
    """A freshly rendered docx plus its footnotes map, in a throwaway directory."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.docx_path = self.tmp / "memo.docx"
        # D-150: the footnote apparatus is what these checks are about, so the fixture is rendered
        # in the footnote style; `InlineStyleTest` covers the inline one and its hyperlinks.
        self.result = renderer.render(
            DRAFT, sample_index(), self.docx_path, citation_style=oscola.STYLE_FOOTNOTES
        )
        self.map = renderer.footnotes_map(self.result)

    def check(self, path: Path | None = None, **kwargs) -> dict:
        kwargs.setdefault("footnotes_map", self.map)
        return validate.validate_path(path or self.docx_path, **kwargs)


class PositiveTest(_Rendered):
    def test_a_freshly_rendered_docx_is_valid(self):
        report = self.check()
        self.assertTrue(report["valid"], report["details"])
        self.assertEqual([], report["errors"])
        self.assertEqual([], report["warnings"])

    def test_every_reference_and_footnote_is_counted(self):
        report = self.check()
        self.assertEqual(3, report["footnotes"])
        self.assertEqual(3, report["references"])


class FootnotePairingTest(_Rendered):
    def test_a_deleted_footnote_leaves_an_orphan_reference(self):
        broken = repack(
            self.docx_path,
            self.tmp / "broken.docx",
            {
                "word/footnotes.xml": lambda text: text.replace(
                    text[text.index('<w:footnote w:id="3">') : text.index("</w:footnotes>")], ""
                )
            },
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_ORPHAN_REFERENCE, report["errors"])
        self.assertIn(validate.E_COUNT_MISMATCH, report["errors"])

    def test_a_duplicated_footnote_id_is_rejected(self):
        def duplicate(text: str) -> str:
            block = text[text.index('<w:footnote w:id="3">') : text.index("</w:footnotes>")]
            return text.replace(block, block + block)

        broken = repack(self.docx_path, self.tmp / "dup.docx", {"word/footnotes.xml": duplicate})
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_DUPLICATE_FOOTNOTE, report["errors"])

    def test_the_footnote_count_must_match_the_resolved_mentions(self):
        report = self.check(footnotes_map={"footnotes": [{"n": 1, "form": "full", "source_id": "x"}]})
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_COUNT_MISMATCH, report["errors"])


class ShortFormTest(_Rendered):
    def test_a_short_form_pointing_at_another_footnote_is_rejected(self):
        broken = repack(
            self.docx_path,
            self.tmp / "shortform.docx",
            {"word/footnotes.xml": lambda text: text.replace("(n 1)", "(n 2)")},
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_SHORT_FORM_MISMATCH, report["errors"])

    def test_a_map_that_claims_the_wrong_first_footnote_is_rejected(self):
        broken_map = {
            "footnotes": [dict(row) for row in self.map["footnotes"]],
            "unresolved": [],
        }
        broken_map["footnotes"][2]["first_n"] = 2
        report = self.check(footnotes_map=broken_map)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_SHORT_FORM_MISMATCH, report["errors"])


class RelationshipAndStyleTest(_Rendered):
    def test_the_footnotes_relationship_is_required(self):
        def drop(text: str) -> str:
            start = text.index('<Relationship Id="rId9"')
            end = text.index("/>", start) + 2
            return text[:start] + text[end:]

        broken = repack(
            self.docx_path, self.tmp / "norel.docx", {"word/_rels/document.xml.rels": drop}
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_NO_FOOTNOTES_RELATIONSHIP, report["errors"])

    def test_the_footnotes_part_is_required(self):
        broken = repack(
            self.docx_path, self.tmp / "nopart.docx", {"word/footnotes.xml": lambda text: None}
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_NO_FOOTNOTES_PART, report["errors"])

    def test_both_footnote_styles_are_required(self):
        broken = repack(
            self.docx_path,
            self.tmp / "nostyle.docx",
            {
                "word/styles.xml": lambda text: text.replace(
                    'w:styleId="FootnoteText"', 'w:styleId="SomethingElse"'
                )
            },
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_MISSING_STYLE, report["errors"])


class LiteralTokenTest(_Rendered):
    def test_a_literal_src_token_in_any_part_is_rejected(self):
        broken = repack(
            self.docx_path,
            self.tmp / "literal.docx",
            {
                "word/document.xml": lambda text: text.replace(
                    "<w:t>1. Executive summary</w:t>", "<w:t>1. [[src:gdpr]]</w:t>"
                )
            },
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_LITERAL_TOKEN, report["errors"])

    def test_a_literal_quote_token_in_the_footnotes_is_rejected(self):
        broken = repack(
            self.docx_path,
            self.tmp / "literalq.docx",
            {"word/footnotes.xml": lambda text: text.replace("art 7", "[[q:q-001]]")},
        )
        report = self.check(broken)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_LITERAL_TOKEN, report["errors"])


class InlineStyleTest(unittest.TestCase):
    """D-150: `docx validate` over an inline-style export — no footnotes, every citation linked."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.path = self.tmp / "memo.docx"
        self.result = renderer.render(
            DRAFT, sample_index(), self.path, citation_style=oscola.STYLE_INLINE
        )
        self.map = renderer.footnotes_map(self.result)

    def test_an_inline_style_export_validates(self):
        report = validate.validate_path(self.path, footnotes_map=self.map)
        self.assertTrue(report["valid"], report["details"])
        self.assertEqual(0, report["footnotes"])
        self.assertEqual(0, report["references"])
        self.assertEqual(oscola.STYLE_INLINE, self.map["citation_style"])

    def test_the_footnotes_relationship_is_still_required(self):
        """The part is empty, but Word still expects the relationship the §5.5 check looks for."""
        with zipfile.ZipFile(self.path) as archive:
            rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        self.assertIn(validate.FOOTNOTES_RELATIONSHIP, rels)

    def test_a_hyperlink_whose_relationship_is_gone_invalidates_the_docx(self):
        def drop(text: str) -> str:
            start = text.index('<Relationship Id="rId9"')
            end = text.index("/>", start) + 2
            return text[:start] + text[end:]

        broken = repack(
            self.path, self.tmp / "nolink.docx", {"word/_rels/document.xml.rels": drop}
        )
        report = validate.validate_path(broken, footnotes_map=self.map)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_BROKEN_HYPERLINK, report["errors"])

    def test_a_hyperlink_pointing_at_the_wrong_kind_of_relationship_is_rejected(self):
        broken = repack(
            self.path,
            self.tmp / "wrongkind.docx",
            {
                "word/document.xml": lambda text: text.replace(
                    'r:id="rId9"', 'r:id="rIdNothing"', 1
                )
            },
        )
        report = validate.validate_path(broken, footnotes_map=self.map)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_BROKEN_HYPERLINK, report["errors"])


class UnresolvedMarkerTest(unittest.TestCase):
    """D-51: `[unresolved:` in any part invalidates the docx; no banner buys an exception (§5.5)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.path = self.tmp / "memo.docx"
        self.result = renderer.render("Body [[src:ghost]].\n", sample_index(), self.path)
        self.map = renderer.footnotes_map(self.result)

    def test_a_marker_the_renderer_itself_reported_is_still_an_error(self):
        """Finding 15: the `unresolved_reference` banner used to demote this to a warning."""
        report = validate.validate_path(self.path, footnotes_map=self.map)
        self.assertFalse(report["valid"], report["details"])
        self.assertIn(validate.E_UNRESOLVED_TOKEN, report["errors"])
        self.assertEqual([], report["warnings"])

    def test_an_unreported_unresolved_marker_is_an_error(self):
        report = validate.validate_path(self.path, footnotes_map={"footnotes": [], "unresolved": []})
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_UNRESOLVED_TOKEN, report["errors"])

    def test_the_offending_part_is_named_in_the_details(self):
        report = validate.validate_path(self.path, footnotes_map=self.map)
        self.assertTrue(
            any(validate.UNRESOLVED_TOKEN in row for row in report["details"]), report["details"]
        )


class FootnotesMapTest(_Rendered):
    """D-51: the render step's map is required — its absence is an error, not a skipped check."""

    def test_a_docx_validated_without_the_map_is_invalid(self):
        report = validate.validate_path(self.docx_path)
        self.assertFalse(report["valid"], report["details"])
        self.assertIn(validate.E_MAP_MISSING, report["errors"])

    def test_a_map_without_a_footnotes_list_is_invalid(self):
        report = self.check(footnotes_map={"unresolved": []})
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_MAP_MISSING, report["errors"])

    def test_the_map_checks_are_not_skipped_for_a_docx_that_would_otherwise_pass(self):
        """Without the map the count and short-form checks are unverifiable, so `valid` is False."""
        with_map = self.check()
        self.assertTrue(with_map["valid"], with_map["details"])
        self.assertFalse(validate.validate_path(self.docx_path)["valid"])


class StatusSectionTest(unittest.TestCase):
    """D34-11: a run that did not end approved must carry its `Status` section, or the docx is invalid."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.path = self.tmp / "memo.docx"
        self.result = renderer.render(
            DRAFT,
            sample_index(),
            self.path,
            final_status="forced_exit_on_v1_with_remaining_issues",
            remaining_blocking_issues=[
                {"severity": "blocker", "section_id": "s-1", "issue": "Art. 17(1) carries no rule."}
            ],
        )
        self.map = renderer.footnotes_map(self.result)

    def test_the_rendered_docx_carries_the_section_and_validates(self):
        self.assertTrue(self.map["status_required"])
        report = validate.validate_path(self.path, footnotes_map=self.map)
        self.assertTrue(report["valid"], report["details"])

    def test_a_docx_that_lost_the_section_is_invalid(self):
        broken = repack(
            self.path,
            self.tmp / "nostatus.docx",
            {
                "word/document.xml": lambda text: text.replace(
                    validate.status_run(), "<w:t>Postscript</w:t>"
                )
            },
        )
        report = validate.validate_path(broken, footnotes_map=self.map)
        self.assertFalse(report["valid"])
        self.assertIn(validate.E_MISSING_STATUS, report["errors"])

    def test_an_approved_run_is_not_asked_for_the_section(self):
        path = self.tmp / "approved.docx"
        result = renderer.render(DRAFT, sample_index(), path, final_status="approved_on_v2")
        report = validate.validate_path(path, footnotes_map=renderer.footnotes_map(result))
        self.assertFalse(renderer.footnotes_map(result)["status_required"])
        self.assertTrue(report["valid"], report["details"])
        self.assertNotIn(validate.E_MISSING_STATUS, report["errors"])


class LocalizedStatusTest(unittest.TestCase):
    """D-175: `docx validate` looks for the Status run of the memo language, not for `Status`."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        packs = self.tmp / "i18n"
        packs.mkdir()
        patcher = mock.patch.object(i18n, "PACK_DIR", packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(packs, "ru", RU_DELIVERABLE)
        self.path = self.tmp / "memo.docx"
        self.result = renderer.render(
            DRAFT,
            sample_index(),
            self.path,
            citation_style=oscola.STYLE_FOOTNOTES,
            final_status="forced_exit_on_v1_with_remaining_issues",
            language="ru",
        )
        self.map = renderer.footnotes_map(self.result)

    def test_validate_finds_the_localized_status_section(self):
        report = validate.validate_path(self.path, footnotes_map=self.map, language="ru")
        self.assertNotIn(validate.E_MISSING_STATUS, report["errors"])
        self.assertTrue(report["valid"], report["details"])

    def test_the_english_run_is_not_in_a_russian_document(self):
        report = validate.validate_path(self.path, footnotes_map=self.map)
        self.assertIn(validate.E_MISSING_STATUS, report["errors"])

    def test_the_run_the_validator_looks_for_follows_the_pack(self):
        self.assertEqual("<w:t>Status</w:t>", validate.status_run())
        self.assertEqual("<w:t>Статус</w:t>", validate.status_run("ru"))


class StandaloneLanguageTest(unittest.TestCase):
    """D-181: `mf docx validate --language <code>` tells the standalone command the memo language.

    Without `--workdir` there is no `state.json` to read it from, so a localized document whose map
    says `status_required` was validated against the English `Status` run and reported
    `status_section_missing`. The flag is the only source: nothing is inferred from the document or
    from the footnotes map, and without it English stands exactly as before.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        packs = self.tmp / "i18n"
        packs.mkdir()
        patcher = mock.patch.object(i18n, "PACK_DIR", packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        i18n._cache.clear()
        self.addCleanup(i18n._cache.clear)
        _i18n.fake_pack(packs, "ru", RU_DELIVERABLE)
        self.path = self.tmp / "memo.docx"
        result = renderer.render(
            DRAFT,
            sample_index(),
            self.path,
            citation_style=oscola.STYLE_FOOTNOTES,
            final_status="forced_exit_on_v1_with_remaining_issues",
            language="ru",
        )
        self.map_path = self.tmp / "footnotes-map.json"
        state_io.write_json_atomic(self.map_path, renderer.footnotes_map(result))

    def args(self, language: str | None) -> argparse.Namespace:
        return argparse.Namespace(
            workdir=None,
            path=str(self.path),
            map_path=str(self.map_path),
            step=None,
            attempt=1,
            language=language,
            human=False,
        )

    def test_the_flag_makes_the_localized_document_valid(self):
        result = docx.run_validate(self.args("ru"))
        self.assertTrue(result["valid"], result["details"])
        self.assertNotIn(validate.E_MISSING_STATUS, result["errors"])

    def test_without_the_flag_the_document_is_still_validated_as_english(self):
        """Characterisation of the unchanged default, not a RED case: it was green before the flag
        existed and must stay green — without `--workdir` and without `--language` the standalone
        command keeps validating as English."""
        result = docx.run_validate(self.args(None))
        self.assertFalse(result["valid"])
        self.assertIn(validate.E_MISSING_STATUS, result["errors"])

    def test_an_unknown_code_is_a_business_error(self):
        result = docx.run_validate(self.args("xx"))
        self.assertEqual(["invalid_language: xx"], result["errors"])
        self.assertIsNone(result["valid"])

    def test_the_flag_is_registered_and_defaults_to_none(self):
        from memoforge import cli

        parser = cli.build_parser()
        self.assertEqual("ru", parser.parse_args(["docx", "validate", "--language", "ru"]).language)
        self.assertIsNone(parser.parse_args(["docx", "validate"]).language)


class UnreadableTest(unittest.TestCase):
    def test_a_missing_file_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = validate.validate_path(Path(tmp) / "absent.docx")
        self.assertFalse(report["valid"])
        self.assertEqual([validate.E_UNREADABLE], report["errors"])
        self.assertFalse(report["exists"])

    def test_a_file_that_is_not_a_zip_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.docx"
            path.write_bytes(b"not a docx at all")
            report = validate.validate_path(path)
        self.assertFalse(report["valid"])
        self.assertEqual([validate.E_UNREADABLE], report["errors"])


class CommandTest(unittest.TestCase):
    """`mf docx validate --step --attempt` over a real work dir (§5.5, D-40)."""

    def make_task(self, draft: str = DRAFT, config: dict | None = None) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work_dir = Path(tmp.name) / "memo-20260908T120000Z-gdpr"
        task.create_work_dir_tree(work_dir)
        state = task.build_initial_state(
            task_id=work_dir.name,
            user_query="q",
            language="en",
            work_dir=work_dir,
            output_folder=work_dir.parent,
            # D-150: the footnote apparatus is what this suite checks, so the runs it drives ask
            # for it; `CitationStyleCommandTest` covers the inline default of both templates.
            config=dict(config or {"citation_style": "footnotes"}),
        )
        state["current_phase"] = "export"
        state["current_draft_path"] = "drafts/v1.md"
        (work_dir / "drafts" / "v1.md").write_text(draft, encoding="utf-8")
        state_io.write_json_atomic(
            work_dir / "research" / "source-pack.json",
            {
                "schema_version": 2,
                "frozen_at": "2026-09-08T12:00:00Z",
                "snapshot": [
                    {"source_id": "gdpr", "raw_sha256": None},
                    {"source_id": "schrems", "raw_sha256": None},
                ],
                "entries": [sample_index().entries["gdpr"], sample_index().entries["schrems"]],
            },
        )
        state_io.create_state(work_dir, state)
        return work_dir

    def render(self, work_dir: Path, step: str = "s-render") -> dict:
        issue_step(work_dir, step)
        return docx.run_render(
            argparse.Namespace(
                workdir=str(work_dir), step=step, attempt=1, draft_sha=None, human=False
            )
        )

    def validate_args(self, work_dir: Path, step: str | None = "s-validate") -> argparse.Namespace:
        if step:
            issue_step(work_dir, step)
        return argparse.Namespace(
            workdir=str(work_dir),
            path=None,
            map_path=None,
            step=step,
            attempt=1,
            human=False,
        )

    def test_a_rendered_memo_validates_and_stays_the_deliverable(self):
        work_dir = self.make_task()
        rendered = self.render(work_dir)
        self.assertEqual("docx", rendered["renderer"])
        result = docx.run_validate(self.validate_args(work_dir))
        self.assertTrue(result["valid"], result["details"])
        self.assertIsNone(result["demoted_to"])
        self.assertTrue((work_dir / "memo-gdpr.docx").is_file())
        state = state_io.read_state(work_dir)
        self.assertEqual("memo-gdpr.docx", state["final_docx_path"])
        row = next(row for row in state["steps"] if row["step_id"] == "s-validate")
        self.assertEqual("ok", row["status"])

    def test_the_render_step_writes_the_footnotes_map(self):
        work_dir = self.make_task()
        rendered = self.render(work_dir)
        self.assertEqual(
            "steps/s-render/a1/cli/footnotes-map.json", rendered["footnotes_map_path"]
        )
        payload = state_io.read_json(work_dir / rendered["footnotes_map_path"])
        self.assertEqual([1, 2, 3], [row["n"] for row in payload["footnotes"]])

    def test_an_invalid_docx_is_renamed_and_the_markdown_takes_over(self):
        work_dir = self.make_task()
        self.render(work_dir)
        docx_path = work_dir / "memo-gdpr.docx"
        repack(
            shutil.copyfile(docx_path, work_dir / "source.docx"),
            docx_path,
            {"word/footnotes.xml": lambda text: text.replace("(n 1)", "(n 2)")},
        )
        result = docx.run_validate(self.validate_args(work_dir))

        self.assertFalse(result["valid"])
        self.assertEqual("memo-gdpr.invalid.docx", result["demoted_to"])
        self.assertEqual("memo-gdpr.md", result["deliverable_path"])
        self.assertFalse(docx_path.exists())
        self.assertTrue((work_dir / "memo-gdpr.invalid.docx").is_file())

        state = state_io.read_state(work_dir)
        self.assertIn("docx_invalid", [row["banner_id"] for row in state["fallback_banners"]])
        self.assertIsNone(state["final_docx_path"])
        self.assertNotIn(
            "memo-gdpr.docx", [row["canonical_path"] for row in state["published"]]
        )
        row = next(row for row in state["steps"] if row["step_id"] == "s-validate")
        self.assertEqual("fail", row["status"])

    def test_an_unresolved_reference_is_rendered_and_then_demoted(self):
        """D-51 / D-117 end to end: the render step validates what it wrote and demotes it itself."""
        work_dir = self.make_task(draft=UNRESOLVED_DRAFT)
        rendered = self.render(work_dir)
        self.assertEqual("docx", rendered["renderer"])
        self.assertTrue(rendered["unresolved"], rendered)
        self.assertIn("unresolved_reference", [row["banner_id"] for row in rendered["banners"]])

        self.assertFalse(rendered["valid"], rendered["validation"])
        self.assertIn(validate.E_UNRESOLVED_TOKEN, rendered["validation"]["errors"])
        self.assertEqual("memo-gdpr.invalid.docx", rendered["demoted_to"])
        self.assertEqual("memo-gdpr.md", rendered["deliverable_path"])
        self.assertTrue((work_dir / "memo-gdpr.md").is_file(), "export never blocks (M9)")
        self.assertTrue((work_dir / "memo-gdpr.invalid.docx").is_file())
        self.assertFalse((work_dir / "memo-gdpr.docx").exists())

        state = state_io.read_state(work_dir)
        banners = [row["banner_id"] for row in state["fallback_banners"]]
        self.assertIn("unresolved_reference", banners)
        self.assertIn("docx_invalid", banners)
        self.assertIsNone(state["final_docx_path"])

    def test_a_demoted_re_render_revokes_the_export_the_first_one_published(self):
        """D-117: a render that publishes no docx leaves none behind either (N-06).

        `final_docx_path` is None after the demotion, but the export of the earlier successful
        render sits on the canonical path under the very same draft sha — `finalize._existing_docx`
        would deliver it as if nothing had failed.
        """
        work_dir = self.make_task()
        first = self.render(work_dir)
        self.assertTrue(first["valid"], first["validation"])
        self.assertTrue((work_dir / "memo-gdpr.docx").is_file())

        # The same draft (same sha), rendered again after the frozen pack lost its entries: every
        # reference resolves to `[unresolved: …]`, so this render is demoted instead of published.
        state_io.write_json_atomic(
            work_dir / "research" / "source-pack.json",
            {
                "schema_version": 2,
                "frozen_at": "2026-09-08T12:00:00Z",
                "snapshot": [],
                "entries": [],
            },
        )
        second = self.render(work_dir, step="s-render-2")

        self.assertEqual(first["draft_sha"], second["draft_sha"])
        self.assertFalse(second["valid"], second["validation"])
        self.assertEqual("memo-gdpr.invalid.docx", second["demoted_to"])
        self.assertEqual("memo-gdpr.docx", second["revoked"])
        self.assertFalse((work_dir / "memo-gdpr.docx").exists(), "the earlier export is revoked")
        self.assertTrue((work_dir / "memo-gdpr.invalid.docx").is_file())

        state = state_io.read_state(work_dir)
        self.assertIsNone(state["final_docx_path"])
        self.assertNotIn("memo-gdpr.docx", [row["canonical_path"] for row in state["published"]])
        self.assertIsNone(second["revoke_error"])

    def test_a_revoke_whose_unlink_fails_still_drops_the_publication(self):
        """D-144 (R2-02): the logical revoke does not depend on removing the bytes.

        A file another process holds open used to leave `published[]` and `final_docx_path` intact,
        so the next `finalize` delivered the export of a render that produced nothing.
        """
        work_dir = self.make_task()
        self.render(work_dir)
        self.assertTrue((work_dir / "memo-gdpr.docx").is_file())
        state_io.write_json_atomic(
            work_dir / "research" / "source-pack.json",
            {
                "schema_version": 2,
                "frozen_at": "2026-09-08T12:00:00Z",
                "snapshot": [],
                "entries": [],
            },
        )

        real_unlink = Path.unlink

        def locked(self, *args, **kwargs):
            if self.name == "memo-gdpr.docx":
                raise OSError("locked by another process")
            return real_unlink(self, *args, **kwargs)

        with mock.patch.object(Path, "unlink", locked):
            second = self.render(work_dir, step="s-render-2")

        self.assertFalse(second["valid"], second["validation"])
        self.assertEqual("memo-gdpr.docx", second["revoked"])
        self.assertIn("OSError", second["revoke_error"])
        self.assertTrue((work_dir / "memo-gdpr.docx").is_file(), "the bytes could not be removed")

        state = state_io.read_state(work_dir)
        self.assertIsNone(state["final_docx_path"])
        self.assertNotIn("memo-gdpr.docx", [row["canonical_path"] for row in state["published"]])

    def test_a_render_that_never_published_a_docx_revokes_nothing(self):
        work_dir = self.make_task(draft=UNRESOLVED_DRAFT)
        rendered = self.render(work_dir)
        self.assertFalse(rendered["valid"], rendered["validation"])
        self.assertIsNone(rendered["revoked"])
        self.assertIsNone(rendered["revoke_error"])

    def test_a_docx_without_the_render_map_is_invalid_instead_of_unchecked(self):
        """D-51: no `footnotes-map.json` to check against -> `footnotes_map_missing`, then demotion."""
        source = self.make_task()
        self.render(source)
        work_dir = self.make_task()
        shutil.copyfile(source / "memo-gdpr.docx", work_dir / "memo-gdpr.docx")

        result = docx.run_validate(self.validate_args(work_dir))
        self.assertFalse(result["valid"], result["details"])
        self.assertEqual([validate.E_MAP_MISSING], result["errors"])
        self.assertEqual("memo-gdpr.invalid.docx", result["demoted_to"])
        state = state_io.read_state(work_dir)
        self.assertIn("docx_invalid", [row["banner_id"] for row in state["fallback_banners"]])

    def test_a_forced_exit_carries_its_status_into_both_deliverables(self):
        """D34-11 end to end: `state.final_status` -> render -> footnotes map -> `docx validate`."""
        work_dir = self.make_task()

        def mutator(state: dict) -> None:
            state["final_status"] = "forced_exit_on_v1_with_remaining_issues"
            state["remaining_blocking_issues"] = [
                {"severity": "blocker", "section_id": "s-1", "issue": "Art. 17(1) carries no rule."}
            ]

        state_io.write_state(work_dir, mutator)
        rendered = self.render(work_dir)

        payload = state_io.read_json(work_dir / rendered["footnotes_map_path"])
        self.assertTrue(payload["status_required"])
        # D-117: the render step already ran the same check over the file it wrote.
        self.assertTrue(rendered["valid"], rendered["validation"])
        result = docx.run_validate(self.validate_args(work_dir))
        self.assertTrue(result["valid"], result["details"])
        self.assertIsNone(result["demoted_to"])

        markdown = (work_dir / "memo-gdpr.md").read_text(encoding="utf-8")
        self.assertIn(fallback.status_heading(), markdown)
        # D-197: the deliverable spells the severity and the section anchor out for the reader.
        self.assertIn("- blocker · section 1 · Art. 17(1) carries no rule.", markdown)

    def test_validate_without_a_docx_is_skipped(self):
        work_dir = self.make_task()
        result = docx.run_validate(self.validate_args(work_dir))
        self.assertTrue(result["skipped"])
        self.assertEqual("no_docx_to_validate", result["reason"])

    def test_validate_repeats_as_a_no_op(self):
        work_dir = self.make_task()
        self.render(work_dir)
        args = self.validate_args(work_dir)
        first = docx.run_validate(args)
        second = docx.run_validate(args)
        self.assertTrue(first["valid"])
        self.assertTrue(second["already_done"])

    def test_validate_of_a_step_that_was_never_issued_is_identity_mismatch(self):
        work_dir = self.make_task()
        self.render(work_dir)
        args = self.validate_args(work_dir, step=None)
        args.step = "s-never-issued"
        result = docx.run_validate(args)
        self.assertEqual(["identity_mismatch"], result["errors"])
        self.assertTrue((work_dir / "memo-gdpr.docx").is_file(), "no side effect before the check")


class CitationStyleCommandTest(CommandTest):
    """D-150: `mf docx render` takes the style from the template's front matter and the config.

    Inherits the work-dir helpers of `CommandTest`; its own cases are the style ones, and the
    inherited cases run again against the same helpers, which costs nothing and proves the footnote
    style still works end to end.
    """

    def style_of(self, work_dir: Path, rendered: dict) -> str:
        return state_io.read_json(work_dir / rendered["footnotes_map_path"])["citation_style"]

    def test_the_classical_memo_template_renders_inline_by_default(self):
        work_dir = self.make_task(config={"template_id": "classical-memo"})
        rendered = self.render(work_dir)
        self.assertEqual("docx", rendered["renderer"])
        self.assertEqual([], rendered["footnotes"])
        self.assertEqual(oscola.STYLE_INLINE, self.style_of(work_dir, rendered))
        self.assertTrue(rendered["valid"], rendered["validation"])

    def test_the_executive_brief_template_renders_inline_by_default(self):
        work_dir = self.make_task(config={"template_id": "executive-brief"})
        rendered = self.render(work_dir)
        self.assertEqual(oscola.STYLE_INLINE, self.style_of(work_dir, rendered))

    def test_the_config_key_switches_the_run_to_footnotes(self):
        work_dir = self.make_task(
            config={"template_id": "executive-brief", "citation_style": "footnotes"}
        )
        rendered = self.render(work_dir)
        self.assertEqual(oscola.STYLE_FOOTNOTES, self.style_of(work_dir, rendered))
        payload = state_io.read_json(work_dir / rendered["footnotes_map_path"])
        self.assertEqual([1, 2, 3], [row["n"] for row in payload["footnotes"]])

    def test_the_markdown_view_follows_the_same_style(self):
        work_dir = self.make_task(config={"template_id": "classical-memo"})
        self.render(work_dir)
        markdown = (work_dir / "memo-gdpr.md").read_text(encoding="utf-8")
        self.assertIn("([Regulation (EU) 2016/679, art 6(1)(f)](", markdown)
        self.assertIn("## Sources", markdown)
        self.assertNotIn("Lawful basis [1]", markdown)

    def test_an_inline_style_export_passes_docx_validate(self):
        work_dir = self.make_task(config={"template_id": "classical-memo"})
        self.render(work_dir)
        result = docx.run_validate(self.validate_args(work_dir))
        self.assertTrue(result["valid"], result["details"])
        self.assertEqual(0, result["footnotes"])


if __name__ == "__main__":
    unittest.main()
