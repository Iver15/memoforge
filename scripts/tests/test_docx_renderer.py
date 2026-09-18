"""Golden tests for `scripts/memoforge/docx/renderer.py` — the AST branch of the export (ТЗ §5.5).

Each case renders one draft and compares the normalised `word/document.xml` and `word/footnotes.xml`
against the fixtures in `fixtures/docx/`. Normalisation drops the namespace declarations and the
`w:rsid*` revision-tracking attributes Word writes and prints one element per line, so a fixture is
readable in review and stable across runs. `MEMOFORGE_UPDATE_GOLDEN=1` rewrites the fixtures.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402
from memoforge import i18n  # noqa: E402
from memoforge.docx import fallback, oscola, renderer, validate  # noqa: E402
from test_docx_fallback import (  # noqa: E402
    RU_DELIVERABLE,
    issue_step,
    warnings_fixture,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "docx"
UPDATE_GOLDEN = os.environ.get("MEMOFORGE_UPDATE_GOLDEN") == "1"

DOCUMENT_PART = "word/document.xml"
FOOTNOTES_PART = "word/footnotes.xml"
STYLES_PART = "word/styles.xml"

STATUS_LABEL = fallback.label("status_label")
STATUS_BANNERS_LABEL = fallback.label("status_banners_label")
STATUS_ISSUES_LABEL = fallback.label("status_issues_label")
APPENDIX_HEADING = fallback.label("appendix_heading")
"""The English labels of the docx deliverable, read the same way the renderer writes them."""

NAMESPACE_PREFIX = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "w",
    "http://schemas.openxmlformats.org/markup-compatibility/2006": "mc",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships": "r",
    "http://schemas.microsoft.com/office/word/2010/wordml": "w14",
    "http://www.w3.org/XML/1998/namespace": "xml",
}


def _name(tag: str) -> str:
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        return f"{NAMESPACE_PREFIX.get(uri, uri)}:{local}"
    return tag


def _attributes(element) -> str:
    parts = []
    for key, value in sorted(element.attrib.items()):
        name = _name(key)
        if name.startswith("w:rsid") or name == "mc:Ignorable":
            continue
        parts.append(f'{name}="{quoteattr(value)[1:-1]}"')
    return (" " + " ".join(parts)) if parts else ""


def _dump(element, depth: int, out: list[str]) -> None:
    tag = _name(element.tag)
    head = f"{'  ' * depth}<{tag}{_attributes(element)}"
    text = escape(element.text or "")
    if len(element) == 0:
        out.append(f"{head}>{text}</{tag}>" if text else f"{head}/>")
        return
    out.append(f"{head}>")
    for child in element:
        _dump(child, depth + 1, out)
    out.append(f"{'  ' * depth}</{tag}>")


def normalise(blob: bytes) -> str:
    """One element per line, without namespace declarations or `w:rsid*` attributes."""
    out: list[str] = []
    _dump(etree.fromstring(blob), 0, out)
    return "\n".join(out) + "\n"


def sample_index() -> fallback.SourceIndex:
    """One source of each layer plus a quote pointing at the case (§5.3 registry shape)."""
    return fallback.SourceIndex(
        snapshot_ids=["gdpr", "schrems", "edpb"],
        entries={
            "gdpr": {
                "source_id": "gdpr",
                "layer": "statutes",
                "title": "General Data Protection Regulation",
                "citation_form": "Regulation (EU) 2016/679",
                "identifiers": {"celex": "32016R0679"},
                "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
                "retrieved_at": "2026-09-01T10:00:00Z",
            },
            "schrems": {
                "source_id": "schrems",
                "layer": "case_law",
                "title": "Data Protection Commissioner v Facebook Ireland Ltd (Schrems II)",
                "citation_form": "Case C-311/18",
                "identifiers": {"ecli": "ECLI:EU:C:2020:559"},
                "url": "https://curia.europa.eu/juris/c-311-18",
                "retrieved_at": "2026-09-01T10:00:00Z",
            },
            "edpb": {
                "source_id": "edpb",
                "layer": "doctrine",
                "title": "Guidelines 05/2020 on consent",
                "citation_form": "EDPB Guidelines 05/2020",
                "url": "https://edpb.europa.eu/guidelines-05-2020",
                "retrieved_at": "2026-09-02T09:00:00Z",
            },
        },
        sources={
            "gdpr": {"meta": {"short_name": "GDPR"}, "currency": {"status": "ok"}},
            "edpb": {
                "meta": {
                    "issuing_body": "European Data Protection Board",
                    "date": "2020-05-04",
                    "short_name": "EDPB Guidelines 05/2020",
                }
            },
        },
        quotes={"q-001": {"source_id": "schrems"}},
        frozen=True,
    )


class GoldenCase(unittest.TestCase):
    """Shared machinery: render one draft, compare both parts against `fixtures/docx/`."""

    def render(self, draft: str, **kwargs) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "memo.docx"
        kwargs.setdefault("index", sample_index())
        # D-150: the styles are covered by `CitationStyleTest`; every other case keeps the footnote
        # apparatus it was written for, so its golden stays a golden of that machinery.
        kwargs.setdefault("citation_style", oscola.STYLE_FOOTNOTES)
        index = kwargs.pop("index")
        self.result = renderer.render(draft, index, target, **kwargs)
        return target

    def assertGolden(self, name: str, path: Path) -> None:
        with zipfile.ZipFile(path) as archive:
            parts = {
                "document.xml": archive.read(DOCUMENT_PART),
                "footnotes.xml": archive.read(FOOTNOTES_PART),
            }
        for suffix, blob in parts.items():
            golden = GOLDEN_DIR / f"{name}.{suffix}"
            actual = normalise(blob)
            if UPDATE_GOLDEN:
                golden.parent.mkdir(parents=True, exist_ok=True)
                golden.write_bytes(actual.encode("utf-8"))
                continue
            self.assertTrue(golden.is_file(), f"missing golden fixture {golden}")
            self.assertEqual(
                golden.read_text(encoding="utf-8"),
                actual,
                f"{name}/{suffix} drifted from its golden fixture",
            )


class HeadingsAndParagraphsTest(GoldenCase):
    """Headings keep their markdown numbering; soft-wrapped lines join into one paragraph (§5.5)."""

    DRAFT = (
        "# 1. Executive summary\n\n"
        "<!-- §s-1 -->\n\n"
        "First line of the paragraph,\nsoft wrapped onto a second line.\n\n"
        "## 2. Background\n\n"
        "### 2.1. Definitions\n\n"
        "Body with **bold**, *italic*, `code` and [the register](https://example.org/reg).\n"
    )

    def test_headings_paragraphs_and_soft_wrap(self):
        self.assertGolden("headings", self.render(self.DRAFT))

    def test_soft_wrap_stays_in_one_paragraph(self):
        path = self.render(self.DRAFT)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn("soft wrapped onto a second line.", text)
        self.assertNotIn("<w:br/>", text)

    def test_a_link_renders_as_text_plus_url(self):
        path = self.render(self.DRAFT)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn("the register", text)
        self.assertIn("&lt;https://example.org/reg&gt;", text)


class ListsTest(GoldenCase):
    """§5.5: a new `w:num` per list, so the second numbered list restarts at 1."""

    TWO_LISTS = "1. alpha\n2. beta\n\nProse between the lists.\n\n1. gamma\n2. delta\n"
    NESTED = "- top level\n- second\n  - nested a\n  - nested b\n"
    STANDALONE = "Prose paragraph.\n\n2. This line is prose, not a list item.\n"

    def test_two_numbered_lists_get_separate_numbering(self):
        self.assertGolden("lists_restart", self.render(self.TWO_LISTS))

    def test_each_list_allocates_its_own_num_id(self):
        path = self.render(self.TWO_LISTS)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        num_ids = sorted({line.split('"')[1] for line in text.splitlines() if "<w:numId" in line})
        self.assertEqual(2, len(num_ids), "each markdown list restarts on its own w:num")

    def test_nested_bullets_use_the_level_two_style(self):
        self.assertGolden("nested_bullets", self.render(self.NESTED))

    def test_a_standalone_numbered_line_is_a_paragraph(self):
        path = self.render(self.STANDALONE)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn('<w:t xml:space="preserve">2. </w:t>', text)
        self.assertNotIn("ListNumber", text)


class TableTest(GoldenCase):
    DRAFT = "| Rule | Effect |\n|---|---|\n| a \\| b | applies |\n"

    def test_table_with_an_escaped_pipe(self):
        self.assertGolden("table_escaped_pipe", self.render(self.DRAFT))

    def test_the_escaped_pipe_survives_as_text(self):
        path = self.render(self.DRAFT)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn("<w:t>|</w:t>", text)
        self.assertIn('<w:tblStyle w:val="TableGrid"/>', text)


class FootnoteTest(GoldenCase):
    """One footnote per mention; the repeat mention gets the short form (§5.5)."""

    QUOTE = "Adequacy is the test.\n\n> Essentially equivalent protection. [[q:q-001]]\n"
    REPEAT = (
        "Lawful basis [[src:gdpr art 6(1)(f)]] and the case [[src:schrems para 168]].\n\n"
        "Later again [[src:gdpr art 7]].\n"
    )
    TYPES = "Legislation [[src:gdpr]], case [[src:schrems]], guidance [[src:edpb]].\n"
    UNRESOLVED = "Cited [[src:ghost-source]] and quoted [[q:q-999]].\n"

    def test_blockquote_with_a_quote_token(self):
        self.assertGolden("blockquote_quote", self.render(self.QUOTE))

    def test_the_quote_token_is_removed_from_the_blockquote(self):
        path = self.render(self.QUOTE)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertNotIn("[[q:", text)
        self.assertIn('<w:footnoteReference w:id="1"/>', text)

    def test_the_citation_is_an_attribution_under_the_quote_not_inside_it(self):
        """D34-22: `> [2] (b) processing …` reads as part of the quoted rule; `— [2]` does not."""
        path = self.render(self.QUOTE)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        paragraphs = text.split("<w:p>")
        quoted = next(row for row in paragraphs if "Essentially equivalent" in row)
        self.assertNotIn("footnoteReference", quoted, "the quote stays the verbatim rule")
        self.assertIn("<w:t>Essentially equivalent protection.</w:t>", quoted)
        attribution = paragraphs[paragraphs.index(quoted) + 1]
        self.assertIn(f'<w:t xml:space="preserve">{escape(fallback.ATTRIBUTION_PREFIX)}</w:t>', attribution)
        self.assertIn('<w:footnoteReference w:id="1"/>', attribution)

    def test_a_marker_leading_the_quote_leaves_no_space_behind(self):
        path = self.render("> [[q:q-001]] Essentially equivalent protection.\n")
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn("<w:t>Essentially equivalent protection.</w:t>", text)

    def test_first_and_repeat_mention(self):
        self.assertGolden("repeat_mention", self.render(self.REPEAT))

    def test_the_repeat_mention_is_a_short_form_pointing_at_the_first_footnote(self):
        self.render(self.REPEAT)
        forms = [(row["n"], row["form"], row["first_n"]) for row in self.result["footnotes"]]
        self.assertEqual([(1, "full", 1), (2, "full", 2), (3, "short", 1)], forms)
        self.assertIn("(n 1)", self.result["footnotes"][2]["text"])

    def test_three_oscola_types(self):
        self.assertGolden("oscola_types", self.render(self.TYPES))

    def test_unresolved_ids_stay_in_the_text_without_a_footnote(self):
        self.assertGolden("unresolved", self.render(self.UNRESOLVED))

    def test_unresolved_raises_the_banner_and_no_footnote(self):
        self.render(self.UNRESOLVED)
        self.assertEqual(["ghost-source", "q-999"], self.result["unresolved"])
        self.assertEqual([], self.result["footnotes"])
        self.assertEqual(
            ["unresolved_reference"], [row["banner_id"] for row in self.result["banners"]]
        )

    def test_every_reference_has_a_footnote_of_the_same_id(self):
        path = self.render(self.REPEAT)
        with zipfile.ZipFile(path) as archive:
            document = archive.read(DOCUMENT_PART).decode("utf-8")
            footnotes = archive.read(FOOTNOTES_PART).decode("utf-8")
        for number in (1, 2, 3):
            self.assertIn(f'<w:footnoteReference w:id="{number}"/>', document)
            self.assertIn(f'<w:footnote w:id="{number}">', footnotes)


class BannerTest(GoldenCase):
    DRAFT = "Body of the memo.\n"

    def test_status_banner(self):
        path = self.render(
            self.DRAFT,
            final_status="manual_review_required_on_v2",
            final_status_reasons=["no_checked_draft"],
            banners=[
                {
                    "banner_id": "no_checked_draft",
                    "condition_key": "no_checked_draft",
                    "text": "No draft version passed lint and citation checks.",
                }
            ],
        )
        self.assertGolden("banner", path)

    def test_no_banner_on_an_approved_run(self):
        path = self.render(self.DRAFT, final_status="approved_v3")
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertNotIn("MANUAL REVIEW REQUIRED", text)
        self.assertNotIn("FFF3CD", text)

    def test_banner_titles_follow_the_final_status(self):
        self.assertEqual(
            "REVIEWER NOTES NOT FULLY RESOLVED", renderer.banner_title("forced_exit_on_v2", [])
        )
        self.assertEqual(
            "USER ACCEPTED EARLY — REMAINING ISSUES", renderer.banner_title("accepted_early", [])
        )
        self.assertEqual(
            "MANUAL REVIEW REQUIRED", renderer.banner_title("manual_review_required_on_v2", [])
        )
        self.assertEqual(
            "PIPELINE FALLBACK NOTICE — REVIEW BEFORE CLIENT USE",
            renderer.banner_title("approved_v3", [{"banner_id": "x", "text": "y"}]),
        )


class SourcesSectionTest(GoldenCase):
    def test_sources_are_generated_at_the_marker(self):
        path = self.render(
            "Body [[src:gdpr]].\n\nTail paragraph.\n\n<!-- sources: generated -->\n"
        )
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertNotIn("sources: generated", text)
        self.assertIn("<w:t>Sources</w:t>", text)
        self.assertIn("CELEX 32016R0679", text)

    def test_only_cited_sources_are_listed(self):
        path = self.render("Body [[src:gdpr]].\n\n<!-- sources: generated -->\n")
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        # D-150: the annex names the instrument once; article-level titles are not printed.
        self.assertIn("[1] Regulation (EU) 2016/679 (GDPR) —", text)
        self.assertNotIn("General Data Protection Regulation", text)
        self.assertNotIn("Schrems II", text)

    def test_the_appendix_carries_warnings_and_unresolved_ids(self):
        index = sample_index()
        index.sources["stale"] = {
            "citation_form": "Some Circular 2011",
            "verification": {"us": "unresolved", "us_by": "agent", "eu_syntax_ok": True},
            "currency": {"status": "manual_check"},
            "liveness": {"status": "dead", "code": 404},
        }
        path = self.render(
            "Body [[src:ghost]].\n",
            index=index,
            drafting_warnings=[{"code": "research_partial", "message": "case_law incomplete"}],
        )
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn("Assumptions &amp; Unverified Sources", text)
        self.assertIn("case_law incomplete", text)
        self.assertIn("Some Circular 2011", text)
        self.assertIn("ghost", text)

    def test_the_docx_appendix_is_the_condensed_one_of_the_markdown_fallback(self):
        """D-113: `deliverable.docx` and `deliverable.md` carry the same appendix."""
        index = sample_index()
        index.currency_unavailable = True
        index.sources["stale"] = {
            "citation_form": "Some Circular 2011",
            "currency": {"status": "unchecked"},
            "liveness": {"status": "changed"},
        }
        index.sources["quiet"] = {
            "citation_form": "Only currency was unchecked",
            "currency": {"status": "unchecked"},
        }
        path = self.render("Body.\n", index=index, drafting_warnings=warnings_fixture())
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))

        bullets = [row for row in text.splitlines() if "<w:t>Assumption " in row]
        self.assertLessEqual(len(bullets), fallback.APPENDIX_WARNING_LIMIT)
        self.assertIn(escape("… and 2 more in summary.md"), text)
        self.assertEqual(1, text.count(escape(fallback.label('currency_unavailable_note'))))
        self.assertNotIn("currency unchecked", text)
        self.assertIn(escape("Some Circular 2011 — link changed"), text)
        self.assertNotIn("Only currency was unchecked", text)
        self.assertNotIn("research/doctrine.json", text)
        self.assertNotIn("unresolved_research_gap", text)


class StatusSectionTest(GoldenCase):
    """D34-11: the docx carries the same `Status` section as the markdown deliverable."""

    def status_paragraphs(self, path: Path) -> list[str]:
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        tail = text.partition(f"<w:t>{STATUS_LABEL}</w:t>")[2]
        return [row.split(">", 1)[1].rsplit("<", 1)[0] for row in tail.splitlines() if "<w:t" in row]

    def render_forced_exit(self, **kwargs):
        payload = {
            "final_status": "forced_exit_on_v1_with_remaining_issues",
            "banners": [
                {"banner_id": "forced_exit", "text": "REVIEWER NOTES NOT FULLY RESOLVED."}
            ],
            "remaining_blocking_issues": [
                {"severity": "blocker", "section_id": "s-2", "issue": "Art. 17(1) carries no rule."}
            ],
        }
        payload.update(kwargs)
        return self.render("Body of the memo.\n", **payload)

    def test_the_banners_and_the_blockers_are_printed_under_the_heading(self):
        rows = self.status_paragraphs(self.render_forced_exit())
        self.assertIn("REVIEWER NOTES NOT FULLY RESOLVED.", rows)
        self.assertIn(escape("blocker · s-2 · Art. 17(1) carries no rule."), rows)
        self.assertTrue(any("forced_exit_on_v1_with_remaining_issues" in row for row in rows))
        self.assertIn(STATUS_BANNERS_LABEL, rows)
        self.assertIn(STATUS_ISSUES_LABEL, rows)

    def test_the_status_section_stands_before_the_appendix(self):
        path = self.render_forced_exit(drafting_warnings=[{"code": "gap", "message": "A gap."}])
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertLess(
            text.index(f"<w:t>{STATUS_LABEL}</w:t>"),
            text.index(escape(APPENDIX_HEADING)),
        )

    def test_an_approved_run_carries_no_status_section(self):
        path = self.render("Body of the memo.\n", final_status="approved_v3")
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertNotIn(f"<w:t>{STATUS_LABEL}</w:t>", text)
        self.assertFalse(self.result["status_required"])

    def test_the_footnotes_map_tells_the_validator_the_section_was_owed(self):
        self.render_forced_exit()
        self.assertTrue(renderer.footnotes_map(self.result)["status_required"])
        self.render("Body of the memo.\n", final_status="client_ready_on_v2")
        self.assertFalse(renderer.footnotes_map(self.result)["status_required"])


class HtmlCommentTest(GoldenCase):
    def test_section_anchors_are_dropped(self):
        path = self.render("## 2. Analysis\n\n<!-- §s-2 -->\n\nBody.\n")
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertNotIn("s-2", text)
        self.assertIn("2. Analysis", text)


class StylesAndPartsTest(GoldenCase):
    def test_the_footnote_styles_and_relationship_are_written(self):
        path = self.render("Body [[src:gdpr]].\n")
        with zipfile.ZipFile(path) as archive:
            styles = archive.read("word/styles.xml").decode("utf-8")
            rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            content_types = archive.read("[Content_Types].xml").decode("utf-8")
        self.assertIn('w:styleId="FootnoteText"', styles)
        self.assertIn('w:styleId="FootnoteReference"', styles)
        self.assertIn("relationships/footnotes", rels)
        self.assertIn("wordprocessingml.footnotes+xml", content_types)

    def test_page_setup_is_one_inch_all_round(self):
        path = self.render("Body.\n")
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertIn('w:bottom="1440"', text)
        self.assertIn('w:left="1440"', text)
        self.assertIn('w:right="1440"', text)
        self.assertIn('w:top="1440"', text)


class MergedSourceTest(GoldenCase):
    """D-144: an alias the freeze collapsed (`merged_into`, D-143) is the canonical source.

    Keyed by the raw id both mentions were a *first* mention: two full OSCOLA forms, two entries in
    §Sources and two `first_n` for one source.
    """

    DRAFT = "Lawful under [[src:gdpr-copy art 6(1)(f)]], and again [[src:gdpr art 7]].\n"

    def merged_index(self) -> fallback.SourceIndex:
        base = sample_index()
        return fallback.SourceIndex(
            snapshot_ids=base.snapshot_ids,
            entries=base.entries,
            sources=dict(base.sources, **{"gdpr-copy": {"citation_form": "GDPR (consolidated)"}}),
            quotes={"q-001": {"source_id": "gdpr-copy"}},
            merged={"gdpr-copy": "gdpr"},
            frozen=True,
        )

    def test_the_alias_is_the_first_mention_and_the_canonical_its_short_form(self):
        self.render(self.DRAFT, index=self.merged_index())
        self.assertEqual([], self.result["unresolved"])
        self.assertEqual(
            [(1, "gdpr", "full", 1), (2, "gdpr", "short", 1)],
            [(row["n"], row["source_id"], row["form"], row["first_n"])
             for row in self.result["footnotes"]],
        )
        self.assertIn("(n 1)", self.result["footnotes"][1]["text"])

    def test_the_full_form_is_built_from_the_entry_of_the_canonical_id(self):
        self.render(self.DRAFT, index=self.merged_index())
        self.assertIn("Regulation (EU) 2016/679", self.result["footnotes"][0]["text"])
        self.assertNotIn("consolidated", self.result["footnotes"][0]["text"])

    def test_the_sources_section_lists_the_merged_source_once(self):
        path = self.render(self.DRAFT, index=self.merged_index())
        self.assertEqual(["gdpr"], self.result["cited"])
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        self.assertNotIn("consolidated", text)

    def test_a_quote_on_an_alias_shares_the_number_of_the_canonical(self):
        self.render("Rule [[src:gdpr]].\n\n> Text. [[q:q-001]]\n", index=self.merged_index())
        self.assertEqual([], self.result["unresolved"])
        # D-152: the attribution under a quotation stands on its own — a short form, never `ibid`.
        self.assertEqual(
            [(1, "full", 1), (2, "short", 1)],
            [(row["n"], row["form"], row["first_n"]) for row in self.result["footnotes"]],
        )
        self.assertIn("(n 1)", self.result["footnotes"][1]["text"])


class CitationStyleTest(GoldenCase):
    """D-150: one golden per citation style over the same draft — footnotes and inline links."""

    DRAFT = (
        "## 1. Lawful basis\n\n"
        "The basis is legitimate interests [[src:gdpr art 6(1)(f)]], not contract necessity.\n\n"
        "> The transfer must be essentially equivalent. [[q:q-001]]\n\n"
        "Transfers follow the case [[src:schrems para 168]] and the guidance [[src:edpb para 44]]; "
        "the guidance is the operative one [[src:edpb para 44]].\n\n"
        "Later again [[src:gdpr art 7]].\n\n"
        "<!-- sources: generated -->\n"
    )

    def document(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            return normalise(archive.read(DOCUMENT_PART))

    def relationships(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            return archive.read("word/_rels/document.xml.rels").decode("utf-8")

    def test_the_footnote_style_golden(self):
        self.assertGolden(
            "citation_footnotes", self.render(self.DRAFT, citation_style=oscola.STYLE_FOOTNOTES)
        )

    def test_the_inline_style_golden(self):
        self.assertGolden(
            "citation_inline", self.render(self.DRAFT, citation_style=oscola.STYLE_INLINE)
        )

    def test_the_inline_style_writes_citations_instead_of_footnotes(self):
        text = self.document(self.render(self.DRAFT, citation_style=oscola.STYLE_INLINE))
        self.assertNotIn("footnoteReference", text)
        self.assertEqual([], self.result["footnotes"])
        self.assertIn(">Regulation (EU) 2016/679 (GDPR), art 6(1)(f)</w:t>", text)
        self.assertIn(">Schrems II, para 168</w:t>", text)

    def test_every_inline_citation_is_a_hyperlink_whose_relationship_resolves(self):
        path = self.render(self.DRAFT, citation_style=oscola.STYLE_INLINE)
        text = self.document(path)
        rels = self.relationships(path)
        used = sorted(set(re.findall(r'<w:hyperlink r:id="([^"]+)"', text)))
        self.assertTrue(used, "the inline style links every citation")
        for identifier in used:
            self.assertIn(f'Id="{identifier}"', rels)
        self.assertTrue(
            validate.validate_path(path, footnotes_map=renderer.footnotes_map(self.result))["valid"]
        )

    def test_the_second_of_two_identical_adjacent_citations_is_dropped_inline(self):
        text = self.document(self.render(self.DRAFT, citation_style=oscola.STYLE_INLINE))
        self.assertEqual(1, text.count("Guidelines 05/2020 (4 May 2020) para 44"))
        # the space that carried the dropped citation goes with it, so the sentence still ends `one.`
        self.assertIn("the guidance is the operative one.</w:t>", text)

    def test_the_second_of_two_identical_adjacent_citations_is_ibid_in_footnotes(self):
        self.render(self.DRAFT, citation_style=oscola.STYLE_FOOTNOTES)
        texts = [row["text"] for row in self.result["footnotes"]]
        self.assertIn(i18n.t('en', 'memo.citation.ibid'), texts)

    def test_the_footnote_style_keeps_the_footnotes_and_links_nothing(self):
        path = self.render(self.DRAFT, citation_style=oscola.STYLE_FOOTNOTES)
        text = self.document(path)
        self.assertIn("footnoteReference", text)
        self.assertNotIn("<w:hyperlink", text)

    def test_both_styles_carry_the_same_sources_annex(self):
        inline = self.document(self.render(self.DRAFT, citation_style=oscola.STYLE_INLINE))
        footnotes = self.document(self.render(self.DRAFT, citation_style=oscola.STYLE_FOOTNOTES))
        for text in (inline, footnotes):
            self.assertIn("CELEX 32016R0679", text)
            self.assertIn("checked 2026-09-01", text)
        self.assertNotIn("CELEX", inline.split("<w:t>Sources</w:t>")[0])


class SectionBoundaryTest(GoldenCase):
    """D-152: the docx keeps the attribution of a quotation that opens a new section."""

    DRAFT = (
        "## 1. First\n\n"
        "Claim [[src:gdpr art 6(1)(b)]].\n\n"
        "## 2. Second\n\n"
        "> [[q:q-001]] Essentially equivalent protection.\n"
    )

    def document(self, style: str) -> str:
        path = self.render(self.DRAFT, citation_style=style)
        with zipfile.ZipFile(path) as archive:
            return normalise(archive.read(DOCUMENT_PART))

    def test_the_inline_style_keeps_the_attribution_line(self):
        """The quotation is the first mention of its own source, so it carries the full form."""
        text = self.document(oscola.STYLE_INLINE)
        self.assertIn(escape(fallback.ATTRIBUTION_PREFIX), text)
        self.assertIn(">Case C-311/18 ", text)

    def test_the_footnote_style_writes_a_short_form_not_an_ibid(self):
        self.render(self.DRAFT, citation_style=oscola.STYLE_FOOTNOTES)
        forms = [(row["n"], row["form"]) for row in self.result["footnotes"]]
        self.assertEqual([(1, "full"), (2, "full")], forms)

    def test_a_quotation_of_a_source_already_cited_is_a_short_form(self):
        draft = (
            "## 1. First\n\nClaim [[src:schrems para 168]].\n\n"
            "## 2. Second\n\n> [[q:q-001]] Essentially equivalent protection.\n"
        )
        self.render(draft, citation_style=oscola.STYLE_FOOTNOTES)
        self.assertEqual(
            [(1, "full"), (2, "short")],
            [(row["n"], row["form"]) for row in self.result["footnotes"]],
        )
        self.assertIn("(n 1)", self.result["footnotes"][1]["text"])

    def test_the_inline_style_of_that_quotation_still_carries_its_citation(self):
        draft = (
            "## 1. First\n\nClaim [[src:schrems para 168]].\n\n"
            "## 2. Second\n\n> [[q:q-001]] Essentially equivalent protection.\n"
        )
        path = self.render(draft, citation_style=oscola.STYLE_INLINE)
        with zipfile.ZipFile(path) as archive:
            text = normalise(archive.read(DOCUMENT_PART))
        attribution = text.split("Essentially equivalent protection.")[1]
        self.assertIn(escape(fallback.ATTRIBUTION_PREFIX), attribution)
        self.assertIn(">Schrems II</w:t>", attribution)


class FootnotesMapTest(GoldenCase):
    def test_the_map_carries_every_resolved_mention(self):
        self.render(FootnoteTest.REPEAT)
        payload = renderer.footnotes_map(self.result)
        self.assertEqual([1, 2, 3], [row["n"] for row in payload["footnotes"]])
        self.assertEqual(["gdpr", "schrems"], payload["cited"])
        self.assertEqual([], payload["unresolved"])


class RenderErrorTest(unittest.TestCase):
    def test_a_render_failure_becomes_a_render_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "memo.docx"
            with self.assertRaises(renderer.RenderError):
                renderer.render("Body.\n", "not an index", target)

    def test_unresolved_text_is_the_marker_the_fallback_uses(self):
        self.assertEqual("[unresolved: x]", oscola.unresolved_text("x"))


class FallbackBranchTest(unittest.TestCase):
    """§5.5/§5.6: a renderer failure or a missing dependency degrades `docx render` to markdown."""

    def make_task(self) -> Path:
        from memoforge import state_io, task

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
            config={},
        )
        state["current_phase"] = "export"
        state["current_draft_path"] = "drafts/v1.md"
        (work_dir / "drafts" / "v1.md").write_text("Body.\n", encoding="utf-8")
        state_io.create_state(work_dir, state)
        return work_dir

    def run_render(self, work_dir: Path) -> dict:
        import argparse

        from memoforge import docx

        issue_step(work_dir, "s-render")
        return docx.run_render(
            argparse.Namespace(
                workdir=str(work_dir), step="s-render", attempt=1, draft_sha=None, human=False
            )
        )

    def test_a_renderer_exception_falls_back_to_markdown(self):
        from unittest import mock

        from memoforge import state_io

        work_dir = self.make_task()
        with mock.patch.object(renderer, "render_workdir", side_effect=renderer.RenderError("boom")):
            result = self.run_render(work_dir)
        self.assertEqual("md_fallback", result["renderer"])
        self.assertEqual("memo-gdpr.md", result["deliverable_path"])
        self.assertIsNone(result["docx"])
        self.assertIn("RenderError: boom", result["render_error"])
        self.assertFalse((work_dir / "memo-gdpr.docx").exists())
        state = state_io.read_state(work_dir)
        self.assertIn("docx_export_failed", [row["banner_id"] for row in state["fallback_banners"]])
        self.assertIsNone(state["final_docx_path"])

    def test_a_missing_dependency_falls_back_to_markdown(self):
        """Without `python-docx`/`mistune` the `from . import renderer` inside the command fails."""
        from memoforge import docx as docx_package

        work_dir = self.make_task()
        module = sys.modules["memoforge.docx.renderer"]
        sys.modules["memoforge.docx.renderer"] = None
        delattr(docx_package, "renderer")
        try:
            result = self.run_render(work_dir)
        finally:
            sys.modules["memoforge.docx.renderer"] = module
            docx_package.renderer = module
        self.assertEqual("md_fallback", result["renderer"])
        self.assertTrue(result["render_error"].startswith("ImportError"))
        self.assertTrue((work_dir / "memo-gdpr.md").is_file())
        self.assertFalse((work_dir / "memo-gdpr.docx").exists())

    def test_the_docx_branch_writes_both_deliverables(self):
        work_dir = self.make_task()
        result = self.run_render(work_dir)
        self.assertEqual("docx", result["renderer"])
        self.assertEqual("memo-gdpr.docx", result["deliverable_path"])
        self.assertEqual("memo-gdpr.md", result["markdown_path"])
        self.assertTrue((work_dir / "memo-gdpr.docx").is_file())
        self.assertTrue((work_dir / "memo-gdpr.md").is_file())


class LocalizedDocxTest(GoldenCase):
    """D-175: the docx prints its labels in the memo language and declares it to Word."""

    DRAFT = "Правомерно по [[src:gdpr art 6(1)(f)]].\n"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", dict(RU_DELIVERABLE, **{"memo.docx_lang": "ru-RU"}))

    def part(self, name: str, draft: str | None = None, **kwargs) -> str:
        path = self.render(draft or self.DRAFT, **kwargs)
        with zipfile.ZipFile(path) as archive:
            return archive.read(name).decode("utf-8")

    def test_the_docx_declares_the_memo_language(self):
        self.assertIn('<w:lang w:val="ru-RU"', self.part(STYLES_PART, language="ru"))

    def test_english_keeps_the_declaration_the_template_ships(self):
        self.assertEqual(self.part(STYLES_PART, language="en"), self.part(STYLES_PART))
        self.assertIn('<w:lang w:val="en-US"', self.part(STYLES_PART))

    def test_the_generated_sections_are_localized(self):
        text = normalise(
            self.part(
                DOCUMENT_PART,
                language="ru",
                drafting_warnings=["Одно допущение."],
                final_status="forced_exit_on_v1",
                remaining_blocking_issues=[{"severity": "blocker", "issue": "Нет нормы."}],
            ).encode("utf-8")
        )
        for key in ("sources_heading", "appendix_heading", "status_label", "assumptions_label"):
            self.assertIn(escape(i18n.t("ru", f"memo.labels.{key}")), text, key)
            self.assertNotIn(f"<w:t>{i18n.t('en', f'memo.labels.{key}')}</w:t>", text, key)

    def test_the_banner_table_is_localized(self):
        text = self.part(
            DOCUMENT_PART,
            language="ru",
            final_status="forced_exit_on_v1",
            banners=[{"banner_id": "forced_exit", "text": "Замечания остались."}],
            final_status_reasons=["no_checked_draft"],
        )
        self.assertIn("ЗАМЕЧАНИЯ РЕЦЕНЗЕНТОВ СНЯТЫ НЕ ПОЛНОСТЬЮ", text)
        self.assertIn("Перед использованием требуется ручная проверка. Итоговый статус:", text)
        self.assertIn("Сработавшие запасные сценарии:", text)
        self.assertIn("Причины, записанные для ручной проверки:", text)
        self.assertNotIn("REVIEWER NOTES NOT FULLY RESOLVED", text)

    def test_the_banner_title_follows_the_pack(self):
        self.assertEqual(
            "ЗАМЕЧАНИЯ РЕЦЕНЗЕНТОВ СНЯТЫ НЕ ПОЛНОСТЬЮ",
            renderer.banner_title("forced_exit_on_v2", [], language="ru"),
        )
        self.assertEqual(
            "REVIEWER NOTES NOT FULLY RESOLVED", renderer.banner_title("forced_exit_on_v2", [])
        )

    def test_the_footnote_carries_the_localized_pinpoint(self):
        text = self.part(DOCUMENT_PART, language="ru", citation_style=oscola.STYLE_FOOTNOTES)
        notes = self.part(FOOTNOTES_PART, language="ru", citation_style=oscola.STYLE_FOOTNOTES)
        self.assertIn("ст. 6(1)(f)", notes)
        self.assertNotIn("art 6(1)(f)", notes)
        self.assertIn('<w:footnoteReference w:id="1"/>', normalise(text.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
