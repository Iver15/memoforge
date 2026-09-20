"""Tests for scripts/memoforge/docx/ — the stdlib-only md fallback (ТЗ §5.5, §9 «md-fallback с токенами»)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402
from memoforge import docx, fallbacks, i18n, state_io, task  # noqa: E402
from memoforge.docx import fallback, oscola  # noqa: E402

FOOTNOTES = oscola.STYLE_FOOTNOTES
INLINE = oscola.STYLE_INLINE

SOURCES_HEADING = fallback.sources_heading()
APPENDIX_HEADING = fallback.appendix_heading()
STATUS_HEADING = fallback.status_heading()
"""The English headings of the markdown deliverable, read from the pack the same way it writes them."""


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


def index(**overrides) -> fallback.SourceIndex:
    """A `SourceIndex` with one registered source and one quote pointing at it."""
    payload = {
        "sources": {
            "gdpr-art6": {
                "citation_form": "Regulation (EU) 2016/679, art 6",
                "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            },
            "case-c-311-18": {"citation_form": "Case C-311/18 Schrems II", "url": "https://curia.eu/x"},
        },
        "quotes": {"q-001": {"source_id": "gdpr-art6"}},
    }
    payload.update(overrides)
    return fallback.SourceIndex(**payload)


class TokenReplacementTest(unittest.TestCase):
    def test_src_token_becomes_a_numbered_marker(self):
        result = fallback.replace_tokens("Lawful under [[src:gdpr-art6]].", index(), FOOTNOTES)
        self.assertEqual(result["text"], "Lawful under [1].")
        # D-150: the annex names the instrument; the article is one of its `cited at` places.
        self.assertEqual(result["footnotes"][0]["citation_form"], "Regulation (EU) 2016/679")

    def test_pinpoint_is_kept_in_the_footnote_not_in_the_marker(self):
        result = fallback.replace_tokens("See [[src:gdpr-art6 art 6(1)(f)]].", index(), FOOTNOTES)
        self.assertEqual(result["text"], "See [1].")
        self.assertEqual(result["footnotes"][0]["pinpoints"], ["art 6(1)(f)"])

    def test_quote_token_resolves_through_quotes_json(self):
        result = fallback.replace_tokens("> Text. [[q:q-001]]", index(), FOOTNOTES)
        self.assertEqual(result["text"], "> Text. [1]")
        self.assertEqual(result["footnotes"][0]["source_id"], "gdpr-art6")

    def test_same_source_keeps_one_number_across_mentions(self):
        result = fallback.replace_tokens(
            "[[src:gdpr-art6]] then [[src:case-c-311-18]] then [[src:gdpr-art6 art 7]].", index(), FOOTNOTES
        )
        self.assertEqual(result["text"], "[1] then [2] then [1].")
        self.assertEqual(len(result["footnotes"]), 2)

    def test_numbers_follow_first_mention_in_document_order(self):
        result = fallback.replace_tokens("[[q:q-001]] then [[src:case-c-311-18]].", index(), FOOTNOTES)
        self.assertEqual(result["text"], "[1] then [2].")
        self.assertEqual([note["source_id"] for note in result["footnotes"]], ["gdpr-art6", "case-c-311-18"])

    def test_no_literal_token_survives_the_pass(self):
        text = "[[src:gdpr-art6]] [[q:q-001]] [[src:ghost]]"
        result = fallback.replace_tokens(text, index(), FOOTNOTES)
        self.assertNotIn("[[src:", result["text"])
        self.assertNotIn("[[q:", result["text"])


class UnresolvedTest(unittest.TestCase):
    def test_unknown_source_id_is_marked_not_dropped(self):
        result = fallback.replace_tokens("Cited [[src:ghost-source]].", index(), FOOTNOTES)
        self.assertEqual(result["text"], "Cited [unresolved: ghost-source].")
        self.assertEqual(result["unresolved"], ["ghost-source"])

    def test_unknown_quote_id_is_marked(self):
        result = fallback.replace_tokens("> Text. [[q:q-999]]", index(), FOOTNOTES)
        self.assertIn("[unresolved: q-999]", result["text"])

    def test_unresolved_raises_the_banner(self):
        rendered = fallback.render("Cited [[src:ghost]].", index())
        self.assertEqual(
            [banner["banner_id"] for banner in rendered["banners"]], ["unresolved_reference"]
        )

    def test_a_source_outside_the_frozen_snapshot_is_unresolved(self):
        frozen = fallback.SourceIndex(
            snapshot_ids=["gdpr-art6"],
            entries={"gdpr-art6": {"source_id": "gdpr-art6", "citation_form": "GDPR art 6"}},
            sources={"late-registration": {"citation_form": "Registered after the freeze"}},
            frozen=True,
        )
        result = fallback.replace_tokens("[[src:gdpr-art6]] and [[src:late-registration]]", frozen, FOOTNOTES)
        self.assertEqual(result["text"], "[1] and [unresolved: late-registration]")

    def test_a_clean_draft_raises_no_banner(self):
        rendered = fallback.render("Cited [[src:gdpr-art6]].", index())
        self.assertEqual(rendered["banners"], [])


class UnverifiedRowsNoteTest(unittest.TestCase):
    def test_appendix_names_a_packed_source_without_saved_text(self):
        # A43-4 / D-156: the reader learns which citations could not be checked against the source.
        book = fallback.SourceIndex(
            sources={
                "s-nosave": {
                    "source_id": "s-nosave",
                    "tier": "supporting",
                    "title": "T",
                    "citation_form": "T 2024",
                    "url": "https://example.org/t",
                    "layer": "statutes",
                    "raw_sha256": None,
                }
            },
            entries={
                "s-nosave": {"source_id": "s-nosave", "tier": "supporting", "citation_form": "T 2024"}
            },
            snapshot_hashes={"s-nosave": None},
        )
        rows = book.unverified_rows()
        self.assertEqual(["s-nosave"], [row["source_id"] for row in rows])
        self.assertIn("no saved source text", " ".join(rows[0]["notes"]))

    @staticmethod
    def _packed(source_id: str, *, registry_sha, snapshot_sha) -> fallback.SourceIndex:
        """One `supporting` source in the pack, with the registry and the snapshot disagreeing."""
        return fallback.SourceIndex(
            sources={
                source_id: {
                    "source_id": source_id,
                    "tier": "supporting",
                    "title": "T",
                    "citation_form": "T 2024",
                    "url": "https://example.org/t",
                    "layer": "statutes",
                    "raw_sha256": registry_sha,
                }
            },
            entries={source_id: {"source_id": source_id, "tier": "supporting", "citation_form": "T 2024"}},
            snapshot_hashes={source_id: snapshot_sha},
        )

    def test_a_raw_file_lost_before_the_freeze_is_named_although_the_registry_kept_its_hash(self):
        # D-158: the raw file disappeared before `build_pack`, so the snapshot row is null while the
        # registry still carries yesterday's hash. C-08 reads the snapshot — the appendix must too.
        rows = self._packed("s-lost", registry_sha="a" * 64, snapshot_sha=None).unverified_rows()
        self.assertEqual(["s-lost"], [row["source_id"] for row in rows])
        self.assertIn("no saved source text", " ".join(rows[0]["notes"]))

    def test_a_source_the_freeze_saved_is_not_named(self):
        # The mirror image: the snapshot holds the text, so there is nothing to disclose.
        self.assertEqual([], self._packed("s-kept", registry_sha=None, snapshot_sha="b" * 64).unverified_rows())


class SourcesSectionTest(unittest.TestCase):
    def test_sources_section_lists_every_footnote(self):
        rendered = fallback.render("[[src:gdpr-art6]] and [[src:case-c-311-18]]", index())
        self.assertIn("## Sources", rendered["markdown"])
        self.assertIn("[1] Regulation (EU) 2016/679 — cited at art 6 —", rendered["markdown"])
        self.assertIn("[2] Case C-311/18 Schrems II", rendered["markdown"])

    def test_generated_marker_is_replaced_by_the_sources_section(self):
        rendered = fallback.render(
            "Body [[src:gdpr-art6]].\n\n<!-- sources: generated -->\n", index()
        )
        self.assertNotIn("sources: generated", rendered["markdown"])
        self.assertIn("## Sources", rendered["markdown"])

    def test_a_draft_without_citations_says_so(self):
        rendered = fallback.render("Body without citations.\n", index())
        self.assertIn("No sources were cited", rendered["markdown"])


class PinpointTest(unittest.TestCase):
    """D34-22: several mentions collapse into one `[n]`, so the list must not pin a later article
    on the first one (`[2] … — Art. 6(4)` under a blockquote of Art. 6(1)(b))."""

    def sources_line(self, draft: str, number: int = 1) -> str:
        markdown = fallback.render(draft, index())["markdown"]
        return next(row for row in markdown.splitlines() if row.startswith(f"[{number}] "))

    def test_the_pinpoint_of_the_first_mention_leads_and_the_later_one_follows(self):
        line = self.sources_line(
            "> [[src:gdpr-art6 Art. 6(1)(b)]] Necessary for a contract.\n\n"
            "Transfers under [[src:gdpr-art6 art 6(4)]].\n"
        )
        self.assertIn(" — cited at art 6(1)(b), art 6(4) — ", line)

    def test_a_first_mention_that_cited_the_source_whole_does_not_claim_a_later_pinpoint(self):
        """The case has no article of its own, so the first mention really did cite it whole."""
        line = self.sources_line(
            "> Essentially equivalent. [[src:case-c-311-18]]\n\n"
            "Applied in [[src:case-c-311-18 para 93]].\n",
            number=1,
        )
        self.assertIn(" — also cited at para 93 — ", line)

    def test_one_mention_with_a_pinpoint_reads_exactly_as_before(self):
        self.assertIn(
            " — cited at art 6(1)(f) — ", self.sources_line("See [[src:gdpr-art6 art 6(1)(f)]].\n")
        )

    def test_an_article_level_record_contributes_its_own_article(self):
        """D-150: `[[src:gdpr-art6]]` without a pinpoint still cites art 6, not the whole GDPR."""
        self.assertIn(" — cited at art 6, art 6(4) — ", self.sources_line(
            "> Necessary for a contract. [[q:q-001]]\n\nTransfers under [[src:gdpr-art6 art 6(4)]].\n"
        ))

    def test_the_footnote_records_whether_the_first_mention_was_pinpointed(self):
        quoted = fallback.replace_tokens(
            "[[src:case-c-311-18]] then [[src:case-c-311-18 para 93]]", index(), FOOTNOTES
        )
        self.assertFalse(quoted["footnotes"][0]["first_pinpointed"])
        cited = fallback.replace_tokens("[[src:gdpr-art6 art 6(1)(b)]]", index(), FOOTNOTES)
        self.assertTrue(cited["footnotes"][0]["first_pinpointed"])


class BlockquoteAttributionTest(unittest.TestCase):
    """D34-22: the citation of a quotation belongs under it, not inside the quoted rule."""

    def test_the_marker_leaves_the_quote_and_becomes_an_attribution_line(self):
        markdown = fallback.render(
            "> [[q:q-001]] (b) processing is necessary for the performance of a contract.\n",
            index(),
            citation_style=FOOTNOTES,
        )["markdown"]
        lines = markdown.splitlines()
        self.assertEqual(
            "> (b) processing is necessary for the performance of a contract.", lines[0]
        )
        self.assertEqual("", lines[1], "a touching line would be a lazy continuation of the quote")
        self.assertEqual("— [1]", lines[2])

    def test_a_marker_at_the_end_of_the_quote_moves_too(self):
        markdown = fallback.render(
            "> Essentially equivalent protection. [[q:q-001]]\n", index(), citation_style=FOOTNOTES
        )["markdown"]
        self.assertIn("> Essentially equivalent protection.\n\n— [1]", markdown)

    def test_a_marker_outside_a_blockquote_stays_where_it_is(self):
        markdown = fallback.render(
            "Lawful under [[src:gdpr-art6]].\n", index(), citation_style=FOOTNOTES
        )["markdown"]
        self.assertIn("Lawful under [1].", markdown)

    def test_two_quotes_get_one_attribution_line_each(self):
        markdown = fallback.render(
            "> First. [[src:gdpr-art6]]\n\nProse.\n\n> Second. [[src:case-c-311-18]]\n",
            index(),
            citation_style=FOOTNOTES,
        )["markdown"]
        self.assertEqual(2, markdown.count("\n— ["))
        self.assertIn("— [1]", markdown)
        self.assertIn("— [2]", markdown)

    def test_an_unresolved_marker_is_not_an_attribution(self):
        markdown = fallback.render(
            "> Quoted. [[src:ghost]]\n", index(), citation_style=FOOTNOTES
        )["markdown"]
        self.assertIn("> Quoted. [unresolved: ghost]", markdown)
        self.assertNotIn("— [", markdown)


class InlineCitationTest(unittest.TestCase):
    """D-150: the inline style — a parenthetical citation linked to the source, no `[n]` markers."""

    DRAFT = (
        "Lawful under [[src:gdpr-art6 Art. 6(1)(b)]].\n\n"
        "> [[q:q-001]] (b) processing is necessary for the performance of a contract.\n\n"
        "Transfers under [[src:gdpr-art6 art 6(4)]] and again [[src:gdpr-art6 art 6(4)]].\n\n"
        "The case [[src:case-c-311-18 para 93]] and a ghost [[src:ghost]].\n\n"
        "<!-- sources: generated -->\n"
    )

    def markdown(self, draft: str | None = None) -> str:
        return fallback.render(draft or self.DRAFT, index(), citation_style=INLINE)["markdown"]

    def test_the_citation_is_a_parenthetical_linked_to_the_registered_url(self):
        self.assertIn(
            "Lawful under ([Regulation (EU) 2016/679, art 6(1)(b)]"
            "(https://eur-lex.europa.eu/eli/reg/2016/679/oj)).",
            self.markdown(),
        )

    def test_no_footnote_markers_are_written(self):
        markdown = self.markdown()
        self.assertNotIn("[1]", markdown.split(SOURCES_HEADING)[0])
        self.assertNotIn("[2]", markdown.split(SOURCES_HEADING)[0])

    def test_the_blockquote_keeps_its_attribution_line(self):
        markdown = self.markdown()
        self.assertIn("> (b) processing is necessary for the performance of a contract.", markdown)
        self.assertIn(f"\n{fallback.ATTRIBUTION_PREFIX}([Regulation (EU) 2016/679", markdown)

    def test_the_second_of_two_identical_adjacent_citations_is_dropped(self):
        markdown = self.markdown()
        self.assertEqual(1, markdown.count("art 6(4)](https"))
        self.assertIn("and again.", markdown)

    def test_an_unresolved_id_is_still_marked_in_the_text(self):
        self.assertIn("[unresolved: ghost]", self.markdown())

    def test_the_sources_annex_is_numbered_in_citation_order(self):
        lines = [row for row in self.markdown().splitlines() if row.startswith("[")]
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].startswith("[1] Regulation (EU) 2016/679"))
        self.assertTrue(lines[1].startswith("[2] Case C-311/18 Schrems II"))

    def test_the_body_carries_no_url_outside_the_link_target(self):
        body = self.markdown().split(SOURCES_HEADING)[0]
        self.assertNotIn("<https", body)
        self.assertNotIn("retrieved", body)

    def test_both_styles_cite_the_same_sources_in_the_same_order(self):
        inline = fallback.render(self.DRAFT, index(), citation_style=INLINE)
        footnotes = fallback.render(self.DRAFT, index(), citation_style=FOOTNOTES)
        self.assertEqual(
            [row["source_id"] for row in inline["footnotes"]],
            [row["source_id"] for row in footnotes["footnotes"]],
        )
        self.assertEqual(INLINE, inline["citation_style"])
        self.assertEqual(FOOTNOTES, footnotes["citation_style"])

    def test_the_footnote_style_writes_ibid_where_the_inline_one_drops_the_citation(self):
        scanned = fallback.scan_mentions(self.DRAFT, index(), FOOTNOTES)
        self.assertIn(oscola.FORM_IBID, [row.get("form") for row in scanned["mentions"]])


CASUS_ENDPOINT = "https://mcp.casus.legal/case/34232"
"""D-192: the address a Casus tool answers with — recorded, never printed and never linked."""

LINK_LESS_RECORD = {
    "layer": "case_law",
    "title": "Определение ВС РФ от 12.03.2024 № 305-ЭС23-12345",
    "citation_form": "Определение СКЭС ВС РФ от 12.03.2024 № 305-ЭС23-12345 по делу № А40-1/2023",
    "url": "",
    "retrieved_from": CASUS_ENDPOINT,
    "retrieved_at": "2026-09-10T09:55:07Z",
    "meta": {"court": "Верховный Суд РФ", "year": 2024, "short_name": "ВС РФ № 305-ЭС23-12345"},
}


def annex_row(record: dict, *, pinpoints: str = "") -> dict:
    """One `## Sources` row the way `fallback.source_rows` builds it (D-150)."""
    view = oscola.view_of("vs-rf", None, record)
    return {"n": 1, "view": view, "members": [view], "pinpoints_text": pinpoints}


def link_less_index(record: dict | None = None) -> fallback.SourceIndex:
    """One source the client may not be linked to: no url, an endpoint behind it."""
    return fallback.SourceIndex(sources={"vs-rf": dict(record or LINK_LESS_RECORD)})


class LinkLessSourceTest(unittest.TestCase):
    """D-192: a source with no public url is cited in full and linked to nothing."""

    DRAFT = "Так решил суд [[src:vs-rf]].\n\n<!-- sources: generated -->\n"

    def test_the_annex_names_the_database_instead_of_a_url(self):
        rendered = oscola.sources_entry(annex_row(LINK_LESS_RECORD))
        self.assertIn("Определение СКЭС ВС РФ от 12.03.2024", rendered)
        self.assertIn("text retrieved from CasusLegal (RU)", rendered)
        self.assertNotIn("<", rendered)
        self.assertNotIn("mcp.casus.legal", rendered)

    def test_an_endpoint_of_no_bundled_server_is_a_legal_database(self):
        record = dict(LINK_LESS_RECORD, retrieved_from="https://endpoint.example/one/mcp")
        rendered = oscola.sources_entry(annex_row(record))
        self.assertIn("text retrieved from a legal database", rendered)
        self.assertNotIn("endpoint.example", rendered)

    def test_a_citation_only_source_without_an_endpoint_says_nothing_extra(self):
        record = dict(LINK_LESS_RECORD)
        record.pop("retrieved_from")
        rendered = oscola.sources_entry(annex_row(record))
        self.assertNotIn("retrieved from", rendered)

    def test_the_inline_citation_is_not_a_link(self):
        markdown = fallback.render(self.DRAFT, link_less_index(), citation_style=INLINE)["markdown"]
        body = markdown.split(SOURCES_HEADING)[0]
        self.assertIn("(Определение СКЭС ВС РФ", body)
        self.assertNotIn("](", body)
        self.assertNotIn("mcp.casus.legal", markdown)

    def test_an_old_record_carrying_an_endpoint_url_is_still_never_printed_or_linked(self):
        """Defence in depth: the rule runs at registration, the renderer never trusts the record."""
        record = dict(LINK_LESS_RECORD, url=CASUS_ENDPOINT)
        record.pop("retrieved_from")
        view = oscola.view_of("vs-rf", None, record)
        self.assertEqual("", oscola.canonical_url(view))
        self.assertEqual("", oscola.anchor_url(view))
        markdown = fallback.render(
            self.DRAFT, link_less_index(record), citation_style=INLINE
        )["markdown"]
        self.assertNotIn("mcp.casus.legal", markdown)
        self.assertNotIn("](", markdown.split(SOURCES_HEADING)[0])


class RendererCleansEveryUrlTest(unittest.TestCase):
    """Fix round 3: the renderer applies the whole public-url rule, not only the host check."""

    DRAFT = "Так решил суд [[src:vs-rf]].\n\n<!-- sources: generated -->\n"

    def markdown(self, record: dict) -> str:
        book = fallback.SourceIndex(sources={"vs-rf": dict(record)})
        return fallback.render(self.DRAFT, book, citation_style=INLINE)["markdown"]

    def test_a_token_in_an_old_records_url_is_never_printed_or_linked(self):
        """A5a: `public_url_of` cleans, it does not only classify."""
        record = dict(LINK_LESS_RECORD, url="https://sudact.ru/regular/doc/abc/?token=TESTTOKEN")
        record.pop("retrieved_from")
        view = oscola.view_of("vs-rf", None, record)
        self.assertEqual("https://sudact.ru/regular/doc/abc/", oscola.canonical_url(view))
        self.assertEqual("https://sudact.ru/regular/doc/abc/", oscola.anchor_url(view))
        markdown = self.markdown(record)
        self.assertNotIn("TESTTOKEN", markdown)
        self.assertIn("(https://sudact.ru/regular/doc/abc/)", markdown)

    def test_userinfo_and_an_auth_fragment_are_cleaned_off_an_old_record(self):
        record = dict(LINK_LESS_RECORD, url="https://user:TESTTOKEN@sudact.ru/x#access_token=TESTTOKEN")
        record.pop("retrieved_from")
        markdown = self.markdown(record)
        self.assertNotIn("TESTTOKEN", markdown)
        self.assertIn("<https://sudact.ru/x>", markdown)

    def test_every_disguised_shape_of_an_endpoint_host_is_still_refused(self):
        """A1: the trailing DNS dot and the IDNA dots reach the renderer too."""
        for host in ("mcp.casus.legal.", "mcp.casus。legal", "MCP.Casus.Legal"):
            with self.subTest(host=host):
                record = dict(LINK_LESS_RECORD, url=f"https://{host}/case/1")
                record.pop("retrieved_from")
                view = oscola.view_of("vs-rf", None, record)
                self.assertEqual("", oscola.canonical_url(view))
                self.assertEqual("", oscola.anchor_url(view))
                markdown = self.markdown(record)
                self.assertNotIn("casus", markdown.lower())
                self.assertNotIn("](", markdown.split(SOURCES_HEADING)[0])

    def test_an_address_the_cleaning_cannot_read_is_printed_by_nothing(self):
        """R1: the renderer fails closed too — an unreadable url is no url at all."""
        for url in (
            "https://user:TESTTOKEN@sudact.ru:bad/x?t=TESTTOKEN",
            "https://user:TESTTOKEN@mcp..casus.legal/case/1?t=TESTTOKEN",
        ):
            with self.subTest(url=url.split("@", 1)[0] + "@…"):
                record = dict(LINK_LESS_RECORD, url=url)
                record.pop("retrieved_from")
                view = oscola.view_of("vs-rf", None, record)
                self.assertEqual("", oscola.canonical_url(view))
                self.assertEqual("", oscola.anchor_url(view))
                markdown = self.markdown(record)
                self.assertNotIn("TESTTOKEN", markdown)
                self.assertNotIn("sudact", markdown)
                self.assertNotIn("casus", markdown.lower())
                self.assertNotIn("](", markdown.split(SOURCES_HEADING)[0])

    def test_a_scrubbed_citation_form_carries_no_address_into_the_annex(self):
        """A4: the registry scrubs those fields, and the annex prints what the registry holds.

        `oscola.RETRIEVED_RE` has always cut a `retrieved …` tail out of a citation form, so the
        marker the scrub leaves behind is dropped from the citation rather than printed — which is
        the safe direction. What matters here is that no address and no token reach the deliverable.
        """
        record = dict(
            LINK_LESS_RECORD,
            title="Определение ВС РФ [retrieved via CasusLegal (RU)]",
            citation_form="Определение ВС РФ, текст: [retrieved via CasusLegal (RU)]",
        )
        markdown = self.markdown(record)
        self.assertNotIn("mcp.casus.legal", markdown)
        self.assertNotIn("TESTTOKEN", markdown)
        self.assertIn("Определение ВС РФ", markdown)


class SectionBoundaryTest(unittest.TestCase):
    """D-152: `ibid` never crosses a heading, and a quotation keeps its own attribution."""

    DRAFT = (
        "## 1. First\n\n"
        "Claim [[src:gdpr-art6 art 6(1)(b)]].\n\n"
        "## 2. Second\n\n"
        "> [[q:q-001]] Processing is necessary for the performance of a contract.\n"
    )

    def body(self, style: str) -> str:
        return fallback.render(self.DRAFT, index(), citation_style=style)["markdown"].split(
            SOURCES_HEADING
        )[0]

    def test_the_quotation_in_the_next_section_keeps_its_attribution_inline(self):
        body = self.body(INLINE)
        self.assertIn("> Processing is necessary for the performance of a contract.", body)
        self.assertIn(
            f"{fallback.ATTRIBUTION_PREFIX}([Regulation (EU) 2016/679, art 6](", body
        )

    def test_the_quotation_in_the_next_section_keeps_its_marker_in_the_footnote_style(self):
        self.assertIn(f"\n{fallback.ATTRIBUTION_PREFIX}[1]", self.body(FOOTNOTES))

    def test_the_citation_of_the_quotation_is_a_short_form_not_an_ibid(self):
        scanned = fallback.scan_mentions(self.DRAFT, index(), FOOTNOTES)
        forms = [row["form"] for row in scanned["mentions"]]
        self.assertEqual([oscola.FORM_FULL, oscola.FORM_SHORT], forms)
        self.assertTrue(scanned["mentions"][1]["blockquote"])
        self.assertNotEqual(
            scanned["mentions"][0]["section_id"], scanned["mentions"][1]["section_id"]
        )

    def test_a_repeat_in_a_new_section_is_a_short_form_even_outside_a_quotation(self):
        draft = "## 1. First\n\nClaim [[src:gdpr-art6 art 6]].\n\n## 2. Second\n\nAgain [[src:gdpr-art6 art 6]].\n"
        scanned = fallback.scan_mentions(draft, index(), FOOTNOTES)
        self.assertEqual(
            [oscola.FORM_FULL, oscola.FORM_SHORT], [row["form"] for row in scanned["mentions"]]
        )

    def test_two_adjacent_citations_in_one_section_are_still_ibid(self):
        draft = "## 1. First\n\nClaim [[src:gdpr-art6 art 6]] and again [[src:gdpr-art6 art 6]].\n"
        scanned = fallback.scan_mentions(draft, index(), FOOTNOTES)
        self.assertEqual(
            [oscola.FORM_FULL, oscola.FORM_IBID], [row["form"] for row in scanned["mentions"]]
        )


class RealRunAnnexTest(unittest.TestCase):
    """D-150 over the registry of the run that motivated it: the annex is instrument-level.

    That export listed 22 article-level lines, each repeating the article's own title. The memo
    cites ten works; the annex has ten entries, and the GDPR is spelled out once in the body.
    """

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "docx" / "real-run-sources.json"

    def setUp(self) -> None:
        payload = json.loads(self.FIXTURE.read_text(encoding="utf-8-sig"))
        self.index = fallback.SourceIndex(sources=payload["sources"])
        self.draft = "\n\n".join(
            f"Sentence {position}. [[src:{row['source_id']}{' ' + row['pinpoint'] if row['pinpoint'] else ''}]]."
            for position, row in enumerate(payload["mentions"])
        ) + "\n\n<!-- sources: generated -->\n"

    def render(self, style: str) -> dict:
        return fallback.render(self.draft, self.index, citation_style=style)

    def annex(self, markdown: str) -> list[str]:
        tail = markdown.split(SOURCES_HEADING, 1)[1]
        return [row for row in tail.splitlines() if row.startswith("[")]

    def test_ten_annex_entries_instead_of_twenty_two(self):
        for style in (INLINE, FOOTNOTES):
            rendered = self.render(style)
            self.assertEqual(10, len(rendered["footnotes"]), style)
            self.assertEqual(10, len(self.annex(rendered["markdown"])), style)

    def test_the_gdpr_is_spelled_out_once_in_the_body(self):
        body = self.render(INLINE)["markdown"].split(SOURCES_HEADING)[0]
        self.assertEqual(1, body.count("Regulation (EU) 2016/679 (GDPR)"))
        self.assertIn("[GDPR, art 44]", body)
        self.assertIn("[GDPR, art 28(3)]", body)

    def test_the_gdpr_entry_names_the_act_once_and_lists_every_article_cited(self):
        line = next(
            row for row in self.annex(self.render(INLINE)["markdown"]) if "2016/679" in row
        )
        self.assertTrue(line.startswith("[1] Regulation (EU) 2016/679 (GDPR) — CELEX 32016R0679 — "))
        self.assertIn("cited at art 3(2)(a), art 6, art 6(4), art 22, art 35(3)(a)", line)
        self.assertIn("<https://eur-lex.europa.eu/eli/reg/2016/679/oj>", line)
        # D-197 (fix round 1): that run never ran a liveness check — no record in the fixture carries
        # one — so the annex may not claim a retrieval date it cannot stand behind.
        self.assertIn("currency unchecked", line)
        self.assertNotIn("checked 2026-09-10", line)
        self.assertNotIn("Territorial scope", line)
        self.assertNotIn("#art", line)

    def test_the_body_links_still_point_at_the_article_anchor(self):
        body = self.render(INLINE)["markdown"].split(SOURCES_HEADING)[0]
        self.assertIn("(https://eur-lex.europa.eu/eli/reg/2016/679/oj#art44)", body)


class AppendixTest(unittest.TestCase):
    def test_drafting_warnings_stay_out_of_the_appendix(self):
        # D-191: warnings live in the facts section; the appendix ignores them.
        rendered = fallback.render(
            "Body [[src:gdpr-art6]].",
            index(),
            drafting_warnings=[
                {"code": "research_partial", "message": "case_law layer did not complete"},
                "doctrine gap accepted by the user",
            ],
        )
        self.assertNotIn("case_law layer did not complete", rendered["markdown"])
        self.assertNotIn("doctrine gap accepted by the user", rendered["markdown"])
        self.assertNotIn(fallback.appendix_heading(), rendered["markdown"])

    def test_verification_puts_unverified_sources_into_the_appendix(self):
        unverified = fallback.SourceIndex(
            sources={
                "us-case": {
                    "citation_form": "Doe v Roe, 1 F.3d 1",
                    "verification": {"us": "unresolved", "us_by": "agent", "eu_syntax_ok": True},
                    "currency": {"status": "manual_check"},
                    "liveness": {"status": "dead", "code": 404},
                },
                "clean": {
                    "citation_form": "GDPR art 6",
                    "verification": {"us": "n/a", "us_by": None, "eu_syntax_ok": True},
                    "currency": {"status": "current"},
                    "liveness": {"status": "ok", "code": 200},
                },
            }
        )
        rendered = fallback.render("Body [[src:us-case]] and [[src:clean]].", unverified)
        self.assertIn("Unverified sources", rendered["markdown"])
        self.assertIn("Doe v Roe, 1 F.3d 1", rendered["markdown"])
        self.assertIn("US citation unresolved", rendered["markdown"])
        self.assertIn("currency manual check", rendered["markdown"])
        self.assertIn("link dead", rendered["markdown"])
        appendix = rendered["markdown"].partition(fallback.appendix_heading())[2]
        self.assertNotIn("GDPR art 6", appendix)

    def test_no_appendix_when_there_is_nothing_to_disclose(self):
        rendered = fallback.render("Body [[src:gdpr-art6]].", index())
        self.assertNotIn("Unverified Sources", rendered["markdown"])

    def test_unresolved_ids_are_listed_in_the_appendix(self):
        rendered = fallback.render("Body [[src:ghost]].", index())
        self.assertIn("Unresolved references", rendered["markdown"])
        self.assertIn("`ghost`", rendered["markdown"])

    def test_warnings_alone_write_no_appendix(self):
        # D-191: drafting warnings live in the facts section; only unverified sources, the
        # currency-unavailable note or unresolved markers open the appendix.
        rendered = fallback.render(
            "Body [[src:gdpr-art6]].",
            index(),
            drafting_warnings=["a gap the facts section already states"],
        )
        self.assertNotIn(fallback.appendix_heading(), rendered["markdown"])
        self.assertNotIn("Assumptions carried into the analysis", rendered["markdown"])

    def test_the_appendix_heading_names_unverified_sources_only(self):
        # D-191: the renamed heading carries no assumptions group.
        self.assertEqual("## Appendix — Unverified Sources", fallback.appendix_heading())


LONG_WARNING = (
    "Nothing in intake or in any research file states what the AI-derived agent scores are actually "
    "used for -- coaching and feedback only, or ranking, pay, discipline, promotion or termination, "
    "so the Article 22 limb has to be hedged conditionally throughout the whole analysis. "
    "research/doctrine.json records the rest at medium confidence, corroborated by commentary only."
)


def warnings_fixture() -> list:
    """15 `drafting_warnings[]` the way a real run leaves them: prose, a duplicate, a tagged string."""
    rows: list = [
        {"code": "unresolved_research_gap", "message": LONG_WARNING},
        {"code": "unresolved_research_gap", "message": LONG_WARNING},  # the same gap, seen twice
        "source currency could not be verified (`currency_unchecked`)",
    ]
    rows.extend(
        {
            "code": "sufficiency_warning",
            "message": f"Assumption {n} rests on statutes.json alone. Verify it before client use.",
        }
        for n in range(1, 13)
    )
    return rows


class WarningTextTest(unittest.TestCase):
    """D-191: `summary.md` keeps every warning verbatim; the appendix carries none of them."""

    def test_a_warning_is_kept_verbatim_with_its_tag(self):
        self.assertEqual(
            "case_law layer did not complete (`research_partial`)",
            fallback.warning_text({"code": "research_partial", "message": "case_law layer did not complete"}),
        )

    def test_a_plain_string_warning_is_kept_as_written(self):
        self.assertEqual(
            "doctrine gap accepted by the user", fallback.warning_text("doctrine gap accepted by the user")
        )


class AssumptionBulletsTest(unittest.TestCase):
    """D-113/D-191: the fallback summary carries the gist of each warning, not the reviewer's prose."""

    def test_a_warning_is_cut_to_its_first_sentence_without_tags_or_file_names(self):
        bullets = fallback.assumption_bullets([{"code": "gap", "message": LONG_WARNING}])
        self.assertEqual(1, len(bullets))
        self.assertTrue(bullets[0].startswith("Nothing in intake"))
        self.assertTrue(bullets[0].endswith("…"), bullets[0])
        self.assertNotIn("research/doctrine.json", bullets[0])
        self.assertLessEqual(len(bullets[0]), fallback.APPENDIX_WARNING_CHARS)

    def test_the_warning_id_tag_of_a_plain_string_is_dropped(self):
        bullets = fallback.assumption_bullets(["source currency could not be verified (`x_y`)"])
        self.assertEqual(["source currency could not be verified"], bullets)

    def test_repeats_are_listed_once_and_the_list_is_capped(self):
        bullets = fallback.assumption_bullets(warnings_fixture())
        self.assertEqual(fallback.APPENDIX_WARNING_LIMIT + 1, len(bullets))
        self.assertEqual("… and 2 more in summary.md", bullets[-1])
        self.assertEqual(1, len([row for row in bullets if row.startswith("Nothing in intake")]))
        self.assertTrue(all(len(row) <= fallback.APPENDIX_WARNING_CHARS for row in bullets))

    def test_an_empty_warning_yields_no_bullet(self):
        self.assertEqual([], fallback.assumption_bullets(["", {"code": "", "message": ""}]))


def blockers_fixture(count: int = 2) -> list:
    """`state.remaining_blocking_issues` the way `revision next` records them (`revision.py:332`)."""
    return [
        {
            "severity": "blocker",
            "section_id": f"s-{n}",
            "category": "unsupported_inference",
            "issue": f"Art. 17(1) is cited without a rule in section {n}.",
        }
        for n in range(1, count + 1)
    ]


FORCED_EXIT_STATE = {
    "final_status": "forced_exit_on_v1_with_remaining_issues",
    "fallback_banners": [
        {
            "banner_id": "forced_exit",
            "text": "REVIEWER NOTES NOT FULLY RESOLVED — 6 blocking issue(s) remain.",
        }
    ],
    "remaining_blocking_issues": blockers_fixture(),
}


class StatusSectionTest(unittest.TestCase):
    """D34-11: a run that did not end approved says so in the deliverable, not only in state."""

    def status(self, markdown: str) -> str:
        self.assertIn(STATUS_HEADING, markdown)
        return markdown.partition(STATUS_HEADING)[2].partition("\n## ")[0]

    def test_the_banners_and_the_blockers_reach_the_deliverable(self):
        # D-197: the reader sees the status as a sentence and the section as a word.
        markdown = fallback.render("Body.\n", index(), state=FORCED_EXIT_STATE)["markdown"]
        status = self.status(markdown)
        self.assertNotIn("forced_exit_on_v1_with_remaining_issues", status)
        self.assertIn(fallback.status_name("forced_exit_on_v1_with_remaining_issues"), status)
        self.assertIn("REVIEWER NOTES NOT FULLY RESOLVED", status)
        self.assertIn(
            "- blocker · section 1 · Art. 17(1) is cited without a rule in section 1.", status
        )
        self.assertIn(
            "- blocker · section 2 · Art. 17(1) is cited without a rule in section 2.", status
        )

    def test_the_status_section_stands_before_the_appendix(self):
        markdown = fallback.render(
            "Body [[src:ghost]].\n",
            index(),
            state=FORCED_EXIT_STATE,
        )["markdown"]
        self.assertLess(
            markdown.index(STATUS_HEADING), markdown.index(APPENDIX_HEADING)
        )

    def test_an_approved_run_carries_no_status_section(self):
        for status in ("approved_on_v2", "client_ready_on_v2"):
            with self.subTest(final_status=status):
                markdown = fallback.render("Body.\n", index(), state={"final_status": status})[
                    "markdown"
                ]
                self.assertNotIn(STATUS_HEADING, markdown)

    def test_a_run_without_a_final_status_yet_carries_no_status_section(self):
        markdown = fallback.render("Body.\n", index(), state={})["markdown"]
        self.assertNotIn(STATUS_HEADING, markdown)

    def test_the_blocker_list_is_capped_and_points_at_the_summary(self):
        state = dict(FORCED_EXIT_STATE, remaining_blocking_issues=blockers_fixture(15))
        status = self.status(fallback.render("Body.\n", index(), state=state)["markdown"])
        rows = [row for row in status.splitlines() if row.startswith("- blocker · section ")]
        self.assertEqual(fallback.STATUS_ISSUE_LIMIT, len(rows))
        self.assertIn("- … and 3 more in summary.md", status)

    def test_the_banner_this_render_raised_is_listed_too(self):
        markdown = fallback.render("Body [[src:ghost]].\n", index(), state=FORCED_EXIT_STATE)[
            "markdown"
        ]
        self.assertIn("unresolved", self.status(markdown).lower())

    def test_an_english_signature_is_the_one_it_was_before_the_language_existed(self):
        """D-175: for `en` the hashed payload is exactly the set of fields it carried before.

        A docx an earlier plugin version exported for a task still in flight has to keep matching.
        The expected value is the pre-D-175 expression: the same dict without the `language` key.
        """
        inputs = fallback.status_inputs(FORCED_EXIT_STATE)
        self.assertEqual("en", inputs["language"])
        without = {key: value for key, value in inputs.items() if key != "language"}
        payload = json.dumps(without, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(
            state_io.sha256_bytes(payload.encode("utf-8")),
            docx.status_signature(FORCED_EXIT_STATE),
        )

    def test_a_blocker_row_is_severity_section_and_issue(self):
        # Without a language the row keeps the raw ids: `summary.md` is the technical record.
        self.assertEqual(
            "blocker · s-4 · Something is wrong.",
            fallback.blocking_issue_line(
                {"severity": "blocker", "section_id": "s-4", "issue": "Something is wrong."}
            ),
        )
        self.assertEqual("a plain string", fallback.blocking_issue_line("a plain string"))
        self.assertEqual("", fallback.blocking_issue_line({}))

    def test_a_client_blocker_row_names_the_severity_and_the_section_in_words(self):
        # D-197: `blocker · s-5-1 · …` is machine talk; the deliverable spells both out.
        issue = {"severity": "blocker", "section_id": "s-5-1", "issue": "Something is wrong."}
        self.assertEqual(
            "blocker · section 5.1 · Something is wrong.",
            fallback.blocking_issue_line(issue, "en"),
        )
        self.assertEqual(
            "blocker · section 9 · Something is wrong.",
            fallback.blocking_issue_line(dict(issue, section_id="s-9"), "en"),
        )
        for whole in ("general", "document"):
            with self.subTest(section_id=whole):
                self.assertEqual(
                    "blocker · whole memo · Something is wrong.",
                    fallback.blocking_issue_line(dict(issue, section_id=whole), "en"),
                )
        self.assertEqual(
            "blocker · s-title · Something is wrong.",
            fallback.blocking_issue_line(dict(issue, section_id="s-title"), "en"),
        )


class StatusNameTest(unittest.TestCase):
    """D-197: `final_status` reaches the reader as a sentence, never as a code."""

    FAMILIES: dict = {
        "approved_on_v1": ("approved", "1"),
        "client_ready_on_v2": ("client_ready", "2"),
        "accepted_early_on_v3": ("accepted_early", "3"),
        "manual_review_required_on_v4": ("manual_review_required", "4"),
        "forced_exit_on_v5_with_remaining_issues": ("forced_exit_with_remaining_issues", "5"),
        "approved_v3": ("approved", "3"),
        "delivered": ("delivered", ""),
        "failed": ("failed", ""),
        "cancelled_by_user": ("cancelled_by_user", ""),
        "fallback_summary_delivered": ("fallback_summary_delivered", ""),
    }
    """Every `final_status` `finalize.py`, `revision.py` and `machine.py` can write."""

    def test_the_family_and_the_version_are_split_off(self):
        for status, expected in self.FAMILIES.items():
            with self.subTest(status=status):
                self.assertEqual(expected, fallback.status_family(status))

    def test_every_family_has_a_name_that_carries_its_version(self):
        for status, (_, version) in self.FAMILIES.items():
            with self.subTest(status=status):
                name = fallback.status_name(status)
                # `delivered` is already a word; every code that is not becomes one.
                self.assertNotIn("_", name)
                if "_" in status:
                    self.assertNotEqual(status, name)
                if version:
                    self.assertIn(version, name)

    def test_an_unknown_family_falls_back_to_the_raw_code(self):
        self.assertEqual("something_new_on_v9", fallback.status_name("something_new_on_v9"))
        self.assertEqual("", fallback.status_name(""))
        self.assertEqual("", fallback.status_name(None))


class StatusReasonNameTest(unittest.TestCase):
    """D-197: `final_status_reasons[]` are codes; the banner prints their sentences."""

    CODES: tuple[str, ...] = (
        "unresolved_blockers",
        "incomplete_review",
        "all_reviewers_failed",
        "regression_forced_exit",
        "length_overflow",
        "step_loop",
        "writer_failed",
        "no_checked_draft",
        "export_reused_untouched",
    )

    def test_every_recorded_reason_has_a_sentence(self):
        for code in self.CODES:
            with self.subTest(code=code):
                text = fallback.reason_name(code)
                self.assertNotEqual(code, text)
                self.assertNotIn("_", text)

    def test_an_unknown_reason_is_printed_as_it_stands(self):
        self.assertEqual("mystery_code", fallback.reason_name("mystery_code"))
        self.assertEqual("free text from --reason", fallback.reason_name("free text from --reason"))
        self.assertEqual("", fallback.reason_name(""))


class AppendixScopeTest(unittest.TestCase):
    """D-197: the appendix discloses the sources the memorandum actually cites."""

    def registry(self) -> fallback.SourceIndex:
        return fallback.SourceIndex(
            sources={
                "cited": {"citation_form": "Cited Act 2020", "currency": {"status": "manual_check"}},
                "placeholder": {
                    "citation_form": "Placeholder Act",
                    "currency": {"status": "manual_check"},
                },
            }
        )

    def test_a_record_no_token_cites_is_not_listed(self):
        markdown = fallback.render("Body [[src:cited]].\n", self.registry())["markdown"]
        self.assertIn("Cited Act 2020", markdown)
        self.assertNotIn("Placeholder Act", markdown)

    def test_the_index_still_lists_everything_when_no_citation_set_is_given(self):
        rows = self.registry().unverified_rows()
        self.assertEqual(["cited", "placeholder"], [row["source_id"] for row in rows])

    def test_the_currency_and_link_tokens_are_printed_through_the_pack(self):
        rows = fallback.SourceIndex(
            sources={
                "x": {
                    "citation_form": "Some Act",
                    "currency": {"status": "manual_check"},
                    "liveness": {"status": "dead"},
                }
            }
        ).unverified_rows()
        self.assertEqual(["currency manual check", "link dead"], rows[0]["notes"])

    def test_the_status_row_prints_the_client_sentence(self):
        issue = {
            "severity": "blocker",
            "section_id": "s-4",
            "issue": "Risk line format: …",
            "issue_client": "Формат строки риска",
        }
        self.assertTrue(fallback.blocking_issue_line(issue).endswith("Формат строки риска"))
        issue.pop("issue_client")
        self.assertTrue(fallback.blocking_issue_line(issue).endswith("Risk line format: …"))


class CurrencyUnavailableTest(unittest.TestCase):
    """D-113: a checker that was down for the whole run is one line, not one line per source."""

    def registry(self) -> dict:
        return {
            "only-currency": {
                "citation_form": "GDPR art 6",
                "currency": {"status": "unchecked"},
            },
            "also-dead": {
                "citation_form": "AI Act, Annex III(4)",
                "currency": {"status": "unchecked"},
                "liveness": {"status": "changed"},
            },
            "flagged": {
                "citation_form": "Some Circular 2011",
                "currency": {"status": "manual_check"},
            },
        }

    def test_the_unchecked_note_disappears_and_a_source_without_another_problem_drops_out(self):
        rows = fallback.SourceIndex(
            sources=self.registry(), currency_unavailable=True
        ).unverified_rows()
        self.assertEqual(["also-dead", "flagged"], [row["source_id"] for row in rows])
        self.assertNotIn("currency unchecked", str(rows))
        self.assertIn("currency manual check", str(rows))

    def test_a_checker_that_ran_keeps_its_per_source_lines(self):
        rows = fallback.SourceIndex(sources=self.registry()).unverified_rows()
        self.assertEqual(["also-dead", "flagged", "only-currency"], [row["source_id"] for row in rows])
        self.assertEqual(2, str(rows).count("currency unchecked"))

    def test_the_banner_in_state_switches_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"fallback_banners": [fallbacks.banner("currency_checker_failed")]}
            self.assertTrue(fallback.SourceIndex.load(Path(tmp), state=state).currency_unavailable)
            self.assertFalse(fallback.SourceIndex.load(Path(tmp), state={}).currency_unavailable)

    def test_the_appendix_prints_the_notice_once(self):
        rendered = fallback.render(
            "Body [[src:only-currency]], [[src:also-dead]], [[src:flagged]].",
            fallback.SourceIndex(sources=self.registry(), currency_unavailable=True),
            drafting_warnings=warnings_fixture(),
        )
        markdown = rendered["markdown"]
        appendix = markdown.partition(fallback.appendix_heading())[2]
        self.assertIn(f"- {fallback.label('currency_unavailable_note')}", markdown)
        self.assertEqual(1, markdown.count(fallback.label('currency_unavailable_note')))
        self.assertNotIn("currency unchecked", appendix)
        self.assertIn("- AI Act, Annex III(4) — link changed", markdown)
        self.assertNotIn("summary.md", markdown)


class UnresolvedIdsTest(unittest.TestCase):
    def test_the_ids_are_read_back_out_of_a_rendered_body(self):
        body = "Body [unresolved: ghost] and [unresolved: q-999], again [unresolved: ghost]."
        self.assertEqual(["ghost", "q-999"], fallback.unresolved_ids(body))

    def test_a_body_without_markers_has_no_ids(self):
        self.assertEqual([], fallback.unresolved_ids("Body [1]."))


class SourceIndexLoadTest(unittest.TestCase):
    def test_load_prefers_the_snapshot_and_reads_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            research = work_dir / "research"
            research.mkdir()
            state_io.write_json_atomic(
                research / "source-pack.json",
                {
                    "schema_version": 2,
                    "frozen_at": "2026-09-08T12:00:00Z",
                    "snapshot": [{"source_id": "gdpr-art6", "raw_sha256": "0" * 64}],
                    "entries": [{"source_id": "gdpr-art6", "citation_form": "GDPR art 6"}],
                },
            )
            state_io.write_json_atomic(
                research / "sources.json",
                {"schema_version": 2, "sources": {"other": {"citation_form": "Other"}}},
            )
            loaded = fallback.SourceIndex.load(work_dir)

        self.assertTrue(loaded.frozen)
        self.assertIsNotNone(loaded.resolve("gdpr-art6"))
        self.assertIsNone(loaded.resolve("other"), "after the freeze only the snapshot counts (M6)")

    def test_load_reads_the_merge_map_and_resolves_an_alias(self):
        """D-143: `merged_into` of the freeze — the draft still cites the id the finding carried."""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            research = work_dir / "research"
            research.mkdir()
            state_io.write_json_atomic(
                research / "source-pack.json",
                {
                    "schema_version": 2,
                    "frozen_at": "2026-09-08T12:00:00Z",
                    "snapshot": [{"source_id": "gdpr-art6", "raw_sha256": "0" * 64}],
                    "entries": [
                        {
                            "source_id": "gdpr-art6",
                            "citation_form": "GDPR art 6",
                            "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
                        }
                    ],
                    "merged_into": {"gdpr-art6-copy": "gdpr-art6"},
                },
            )
            state_io.write_json_atomic(
                research / "sources.json",
                {
                    "schema_version": 2,
                    "sources": {"gdpr-art6-copy": {"citation_form": "GDPR art 6 (consolidated)"}},
                },
            )
            loaded = fallback.SourceIndex.load(work_dir)

        resolved = loaded.resolve("gdpr-art6-copy")
        self.assertIsNotNone(resolved, "a merged id resolves through its canonical (M6)")
        self.assertEqual("gdpr-art6", resolved["source_id"])
        self.assertEqual("GDPR art 6", resolved["citation_form"])
        self.assertIsNone(loaded.resolve("gdpr-art6-ghost"), "only `merged_into` is an alias")

    def test_load_tolerates_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = fallback.SourceIndex.load(Path(tmp))
        self.assertFalse(loaded.frozen)
        self.assertIsNone(loaded.resolve("anything"))


class RenderCommandTest(unittest.TestCase):
    def make_task(self) -> Path:
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
        (work_dir / "drafts" / "v1.md").write_text(
            "Body [[src:gdpr-art6]].\n\n<!-- sources: generated -->\n", encoding="utf-8"
        )
        state_io.write_json_atomic(
            work_dir / "research" / "sources.json",
            {"schema_version": 2, "sources": {"gdpr-art6": {"citation_form": "GDPR art 6"}}},
        )
        state_io.create_state(work_dir, state)
        return work_dir

    def render_args(self, work_dir: Path, **overrides) -> argparse.Namespace:
        payload = {
            "workdir": str(work_dir),
            "step": "s-render",
            "attempt": 1,
            "draft_sha": None,
            "human": False,
        }
        payload.update(overrides)
        args = argparse.Namespace(**payload)
        if args.step and (work_dir / "state.json").is_file():
            issue_step(work_dir, args.step, int(args.attempt or 1))
        return args

    def test_render_writes_the_markdown_view_next_to_the_docx(self):
        """§5.5: `docx render` always writes `memo-<slug>.md`, whichever branch produced the memo."""
        work_dir = self.make_task()
        result = docx.run_render(self.render_args(work_dir))
        self.assertEqual(result["markdown_path"], "memo-gdpr.md")
        self.assertTrue((work_dir / "memo-gdpr.md").is_file())
        self.assertTrue((work_dir / "steps" / "s-render" / "a1" / "cli" / "memo-gdpr.md").is_file())

    def test_render_publishes_the_sha_and_closes_the_step(self):
        work_dir = self.make_task()
        result = docx.run_render(self.render_args(work_dir))
        state = state_io.read_state(work_dir)
        published = [row for row in state["published"] if row["canonical_path"] == "memo-gdpr.md"]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["sha256"], result["markdown_sha256"])
        self.assertEqual(state["steps"][0]["status"], "ok")

    def test_render_is_idempotent(self):
        work_dir = self.make_task()
        first = docx.run_render(self.render_args(work_dir))
        second = docx.run_render(self.render_args(work_dir))
        self.assertEqual(first["sha256"], second["sha256"])
        state = state_io.read_state(work_dir)
        self.assertEqual(
            ["memo-gdpr.md", "memo-gdpr.docx"],
            [row["canonical_path"] for row in state["published"]],
        )
        self.assertEqual(len(state["steps"]), 1)

    def test_render_without_a_draft_is_a_business_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = docx.run_render(self.render_args(Path(tmp)))
        self.assertEqual(result["errors"], ["no_draft_to_render"])

    def test_validate_is_skipped_when_no_docx_was_rendered(self):
        """The markdown branch leaves nothing to invalidate; export never blocks (§5.5, M9)."""
        work_dir = self.make_task()
        result = docx.run_validate(argparse.Namespace(workdir=str(work_dir), path=None, human=False))
        self.assertTrue(result["skipped"])
        self.assertEqual("no_docx_to_validate", result["reason"])
        self.assertIsNone(result["valid"])
        self.assertFalse(result["exists"])


class QuoteResolutionTest(unittest.TestCase):
    """§5.3/§5.5: `[[q:]]` is a quote-registry reference, never a source id in disguise (finding 9)."""

    def test_a_quote_id_that_is_also_a_source_id_stays_unresolved(self):
        shadowed = fallback.SourceIndex(
            sources={"act": {"citation_form": "Some Act 2020"}},
            quotes={"q-001": {"source_id": "act"}},
        )
        result = fallback.replace_tokens("> Text. [[q:act]]", shadowed, FOOTNOTES)
        self.assertEqual("> Text. [unresolved: act]", result["text"])
        self.assertEqual(["act"], result["unresolved"])

    def test_the_shadowed_quote_id_raises_the_banner(self):
        shadowed = fallback.SourceIndex(
            sources={"act": {"citation_form": "Some Act 2020"}},
            quotes={"q-001": {"source_id": "act"}},
        )
        rendered = fallback.render("> Text. [[q:act]]", shadowed)
        self.assertEqual(
            ["unresolved_reference"], [banner["banner_id"] for banner in rendered["banners"]]
        )


class MergedSourceTest(unittest.TestCase):
    """D-143: a `[[src:]]`/`[[q:]]` on a collapsed duplicate renders as the canonical footnote."""

    def merged_index(self) -> fallback.SourceIndex:
        return fallback.SourceIndex(
            snapshot_ids=["gdpr-art6"],
            entries={
                "gdpr-art6": {
                    "source_id": "gdpr-art6",
                    "citation_form": "Regulation (EU) 2016/679, art 6",
                    "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
                }
            },
            sources={"gdpr-art6-copy": {"citation_form": "GDPR art 6 (consolidated)"}},
            quotes={"q-001": {"source_id": "gdpr-art6-copy"}},
            merged={"gdpr-art6-copy": "gdpr-art6"},
            frozen=True,
        )

    def test_an_alias_token_is_a_footnote_and_never_unresolved(self):
        result = fallback.replace_tokens("Lawful under [[src:gdpr-art6-copy art 6]].", self.merged_index(), FOOTNOTES)
        self.assertEqual("Lawful under [1].", result["text"])
        self.assertEqual([], result["unresolved"])
        self.assertEqual(
            "Regulation (EU) 2016/679", result["footnotes"][0]["citation_form"]
        )

    def test_a_quote_on_an_alias_resolves_too(self):
        result = fallback.replace_tokens("> Text. [[q:q-001]]", self.merged_index(), FOOTNOTES)
        self.assertEqual([], result["unresolved"])
        self.assertEqual(1, len(result["footnotes"]))

    def test_an_alias_and_its_canonical_share_one_number(self):
        """D-144: numbering keyed by the raw id gave the same source two footnotes and two lines."""
        result = fallback.replace_tokens(
            "Under [[src:gdpr-art6-copy art 6]], and again [[src:gdpr-art6 art 7]].",
            self.merged_index(),
            FOOTNOTES,
        )
        self.assertEqual("Under [1], and again [1].", result["text"])
        self.assertEqual(1, len(result["footnotes"]))
        self.assertEqual("gdpr-art6", result["footnotes"][0]["source_id"])
        self.assertEqual(["art 6", "art 7"], result["footnotes"][0]["pinpoints"])

    def test_a_quote_on_an_alias_shares_the_number_of_the_canonical(self):
        result = fallback.replace_tokens(
            "Rule [[src:gdpr-art6]].\n\n> Text. [[q:q-001]]", self.merged_index(), FOOTNOTES
        )
        self.assertEqual("Rule [1].\n\n> Text. [1]", result["text"])
        self.assertEqual(1, len(result["footnotes"]))

    def test_the_sources_section_lists_the_merged_source_once(self):
        markdown = fallback.render(
            "Under [[src:gdpr-art6-copy]] and [[src:gdpr-art6]].\n", self.merged_index()
        )["markdown"]
        self.assertEqual(1, markdown.count("[1] Regulation (EU) 2016/679 —"))
        self.assertNotIn("[2]", markdown)


class FreezeDetectionTest(unittest.TestCase):
    """D-03: freeze = `source-pack.json` exists / `state.sources_frozen`, not «snapshot is non-empty»."""

    def make_pack(self, work_dir: Path, snapshot: list) -> None:
        research = work_dir / "research"
        research.mkdir(parents=True, exist_ok=True)
        state_io.write_json_atomic(
            research / "source-pack.json",
            {
                "schema_version": 2,
                "frozen_at": "2026-09-08T12:00:00Z",
                "snapshot": snapshot,
                "entries": [],
            },
        )
        state_io.write_json_atomic(
            research / "sources.json",
            {"schema_version": 2, "sources": {"late": {"citation_form": "Registered after the freeze"}}},
        )

    def test_an_empty_snapshot_is_still_a_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            self.make_pack(work_dir, [])
            loaded = fallback.SourceIndex.load(work_dir)
        self.assertTrue(loaded.frozen)
        self.assertIsNone(loaded.resolve("late"), "an empty freeze admits nothing (M6)")

    def test_state_sources_frozen_freezes_without_a_pack_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            (work_dir / "research").mkdir()
            state_io.write_json_atomic(
                work_dir / "research" / "sources.json",
                {"schema_version": 2, "sources": {"late": {"citation_form": "Late"}}},
            )
            loaded = fallback.SourceIndex.load(work_dir, state={"sources_frozen": True})
        self.assertTrue(loaded.frozen)
        self.assertIsNone(loaded.resolve("late"))


class DraftSelectionTest(unittest.TestCase):
    """§2.1 row 15 / §2.2: `--draft-sha` binds the export to bytes, not to a state record."""

    def make_task(self) -> Path:
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
        state_io.create_state(work_dir, state)
        return work_dir

    def put_version(
        self, work_dir: Path, version: int, body: str, *, lint_clean: bool, citations_clean: bool
    ) -> str:
        relative = f"drafts/v{version}.md"
        (work_dir / relative).write_text(body, encoding="utf-8")
        sha = state_io.sha256_file(work_dir / relative)

        def mutator(state: dict) -> None:
            rows = [row for row in state.get("draft_versions") or [] if row.get("version") != version]
            rows.append(
                {
                    "version": version,
                    "path": relative,
                    "sha256": sha,
                    "lint_clean": lint_clean,
                    "citations_clean": citations_clean,
                    "checked_at": "2026-09-08T12:00:00.000Z",
                }
            )
            state["draft_versions"] = sorted(rows, key=lambda row: row["version"])
            state["current_draft_path"] = relative

        state_io.write_state(work_dir, mutator)
        return sha

    def test_draft_sha_selects_the_version_whose_bytes_hash_to_it(self):
        work_dir = self.make_task()
        sha1 = self.put_version(work_dir, 1, "# v1\n", lint_clean=True, citations_clean=True)
        self.put_version(work_dir, 2, "# v2\n", lint_clean=True, citations_clean=True)
        state = state_io.read_state(work_dir)
        selection = docx.select_draft(state, work_dir, sha1)
        self.assertEqual("drafts/v1.md", selection["relative"])
        self.assertTrue(selection["draft_sha_matched"])
        self.assertTrue(selection["checked"])

    def test_a_draft_sha_recorded_in_state_but_not_on_disk_is_not_accepted(self):
        work_dir = self.make_task()
        recorded = self.put_version(work_dir, 1, "# v1\n", lint_clean=True, citations_clean=True)
        self.put_version(work_dir, 2, "# v2 clean\n", lint_clean=True, citations_clean=True)
        # Somebody edits v1 after it was checked: the state row still carries the old sha.
        (work_dir / "drafts/v1.md").write_text("# v1 tampered\n", encoding="utf-8")
        state = state_io.read_state(work_dir)
        selection = docx.select_draft(state, work_dir, recorded)
        self.assertFalse(selection["draft_sha_matched"])
        self.assertEqual("drafts/v2.md", selection["relative"], "§2.1 row 15: last lint+citations-clean")
        self.assertTrue(selection["checked"])

    def test_without_a_checked_version_the_last_one_is_exported_with_no_checked_draft(self):
        work_dir = self.make_task()
        self.put_version(work_dir, 1, "# v1\n", lint_clean=True, citations_clean=False)
        self.put_version(work_dir, 2, "# v2\n", lint_clean=False, citations_clean=False)
        state = state_io.read_state(work_dir)
        selection = docx.select_draft(state, work_dir)
        self.assertEqual("drafts/v2.md", selection["relative"])
        self.assertFalse(selection["checked"])
        self.assertEqual(["no_checked_draft"], selection["reasons"])
        self.assertEqual(["no_checked_draft"], [banner["banner_id"] for banner in selection["banners"]])

    def test_a_checked_version_that_drifted_no_longer_counts_as_checked(self):
        work_dir = self.make_task()
        self.put_version(work_dir, 1, "# v1\n", lint_clean=True, citations_clean=True)
        (work_dir / "drafts/v1.md").write_text("# v1 edited outside the pipeline\n", encoding="utf-8")
        state = state_io.read_state(work_dir)
        selection = docx.select_draft(state, work_dir)
        self.assertEqual("drafts/v1.md", selection["relative"], "export never blocks (§2.1 row 15)")
        self.assertFalse(selection["checked"])
        self.assertEqual(["no_checked_draft"], selection["reasons"])

    def test_render_records_no_checked_draft_in_state(self):
        work_dir = self.make_task()
        self.put_version(work_dir, 1, "# v1\n", lint_clean=False, citations_clean=False)
        issue_step(work_dir, "s-render")
        result = docx.run_render(
            argparse.Namespace(
                workdir=str(work_dir), step="s-render", attempt=1, draft_sha=None, human=False
            )
        )
        self.assertEqual(["no_checked_draft"], result["final_status_reasons"])
        state = state_io.read_state(work_dir)
        self.assertIn("no_checked_draft", state["final_status_reasons"])
        self.assertIn("no_checked_draft", [row["banner_id"] for row in state["fallback_banners"]])


class RenderIdentityTest(unittest.TestCase):
    """D-40: `docx render|validate` pass the strict identity check before any side effect."""

    def make_task(self) -> Path:
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

    def render_args(self, work_dir: Path, step: str | None, attempt: int = 1) -> argparse.Namespace:
        return argparse.Namespace(
            workdir=str(work_dir), step=step, attempt=attempt, draft_sha=None, human=False
        )

    def test_render_of_a_step_that_was_never_issued_is_identity_mismatch(self):
        work_dir = self.make_task()
        result = docx.run_render(self.render_args(work_dir, "s-never-issued"))
        self.assertEqual(["identity_mismatch"], result["errors"])
        self.assertEqual("unknown_step", result["reason"])
        self.assertFalse((work_dir / "memo-gdpr.md").exists(), "no side effect before the check")

    def test_render_of_an_attempt_above_the_issued_one_is_identity_mismatch(self):
        work_dir = self.make_task()
        issue_step(work_dir, "s-render", 1)
        result = docx.run_render(self.render_args(work_dir, "s-render", attempt=99))
        self.assertEqual(["identity_mismatch"], result["errors"])
        self.assertEqual("unissued_attempt", result["reason"])
        self.assertFalse((work_dir / "memo-gdpr.md").exists())

    def test_a_stale_attempt_cannot_overwrite_the_current_result(self):
        work_dir = self.make_task()
        issue_step(work_dir, "s-render", 1)
        docx.run_render(self.render_args(work_dir, "s-render", attempt=1))
        issue_step(work_dir, "s-render", 2)
        stale = docx.run_render(self.render_args(work_dir, "s-render", attempt=1))
        self.assertEqual(["identity_mismatch"], stale["errors"])
        self.assertEqual("stale_attempt", stale["reason"])

    def test_validate_closes_its_step_and_repeats_as_a_no_op(self):
        work_dir = self.make_task()
        issue_step(work_dir, "s-validate")
        args = argparse.Namespace(
            workdir=str(work_dir), path=None, step="s-validate", attempt=1, human=False
        )
        first = docx.run_validate(args)
        self.assertTrue(first["skipped"])
        state = state_io.read_state(work_dir)
        row = next(row for row in state["steps"] if row["step_id"] == "s-validate")
        self.assertEqual("skipped", row["status"])
        again = docx.run_validate(args)
        self.assertTrue(again["already_done"])
        self.assertEqual(first["path"], again["path"])

    def test_validate_without_a_step_stays_a_utility(self):
        work_dir = self.make_task()
        result = docx.run_validate(argparse.Namespace(workdir=str(work_dir), path=None, human=False))
        self.assertTrue(result["skipped"])
        self.assertEqual([], state_io.read_state(work_dir)["steps"])


class SlugTest(unittest.TestCase):
    def test_slug_comes_from_the_task_id(self):
        state = {"task_id": "memo-20260908T120000Z-gdpr-ai-transcripts"}
        self.assertEqual(docx.slug_of(state, Path("/tmp/x")), "gdpr-ai-transcripts")

    def test_slug_falls_back_to_the_work_dir_name(self):
        self.assertEqual(
            docx.slug_of({}, Path("/tmp/memo-20260908T120000Z-fallback-slug")), "fallback-slug"
        )

    def test_slug_of_an_unrecognised_directory(self):
        self.assertEqual(docx.slug_of({}, Path("/tmp/scratch")), "memo")


RU_DELIVERABLE: dict = {
    "memo.labels.sources_heading": "Источники",
    "memo.labels.no_sources_cited": "В этом проекте не процитировано ни одного источника.",
    "memo.labels.appendix_heading": "Приложение — непроверенные источники",
    "memo.labels.appendix_more": "… и ещё {count} в summary.md",
    "memo.labels.unverified_label": "Непроверенные источники",
    "memo.labels.unresolved_label": "Неразрешённые ссылки",
    "memo.labels.unresolved_bullet": "{raw_id} — нет во замороженном пакете; помечено {marker}.",
    "memo.labels.currency_unavailable_note": "Актуальность источников в этом прогоне не проверялась.",
    "memo.labels.currency_note": "актуальность {status}",
    "memo.labels.link_note": "ссылка {status}",
    "memo.labels.status_label": "Статус",
    "memo.labels.status_lead": "Итоговый статус: {final_status}. Конвейер не подписал меморандум.",
    "memo.labels.status_banners_label": "Уведомления конвейера",
    "memo.labels.status_issues_label": "Нерешённые блокирующие замечания",
    "memo.labels.retrieved_from_note": "текст получен из базы {server}",
    "memo.labels.legal_database": "юридических данных",
    "memo.banner_titles.forced_exit": "ЗАМЕЧАНИЯ РЕЦЕНЗЕНТОВ СНЯТЫ НЕ ПОЛНОСТЬЮ",
    "memo.banner_titles.subtitle": "Перед использованием требуется ручная проверка.",
    "memo.banner_titles.final_status": "Итоговый статус: {final_status}.",
    "memo.banner_titles.fallbacks_heading": "Сработавшие запасные сценарии:",
    "memo.banner_titles.reasons_heading": "Причины, записанные для ручной проверки:",
    "memo.labels.section_word": "раздел",
    "memo.labels.whole_memo": "весь меморандум",
    "memo.status_names.forced_exit_with_remaining_issues": (
        "выпущен с незакрытыми замечаниями (версия {version})"
    ),
    "memo.status_names.manual_review_required": "требуется ручная проверка (версия {version})",
    "memo.status_reasons.unresolved_blockers": "остались незакрытые блокирующие замечания",
    "memo.severity.blocker": "блокирующее замечание",
    "memo.severity.major": "существенное замечание",
    "memo.currency_names.manual_check": "нужна ручная проверка",
    "memo.link_names.dead": "не открывается",
    "memo.citation.art": "ст.",
    "memo.citation.cited_at": "цитируется в ",
    "memo.citation.also_cited_at": "также цитируется в ",
    "memo.citation.checked": "проверено ",
    "memo.citation.currency": "актуальность ",
    "memo.citation.ibid": "там же",
}
"""The Russian labels of the deliverable; the docx test family builds its packs from this too."""

LOCALIZED_KEYS: tuple[str, ...] = (
    "sources_heading",
    "appendix_heading",
    "status_label",
    "unverified_label",
    "unresolved_label",
)
"""The labels a Russian deliverable must carry, and must not carry in English (D-175)."""


class _PackedTestCase(unittest.TestCase):
    """A temp pack directory with one Russian pack; `i18n.PACK_DIR` points at it for the test."""

    OVERRIDES: dict = RU_DELIVERABLE

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", self.OVERRIDES)


def localized_index() -> fallback.SourceIndex:
    """One consolidated EU act whose currency and liveness put it into the appendix."""
    return fallback.SourceIndex(
        sources={
            "gdpr": {
                "citation_form": "Regulation (EU) 2016/679 (GDPR), art 6",
                "identifiers": {
                    "celex": "32016R0679",
                    "celex_consolidated": "02016R0679-20160504",
                },
                "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
                "retrieved_at": "2026-09-10T09:55:07Z",
                "currency": {"status": "manual_check"},
                "liveness": {"status": "dead"},
            }
        }
    )


class LocalizedMarkdownTest(_PackedTestCase):
    """D-175: every label the markdown deliverable prints comes from the memo language."""

    DRAFT = (
        "Правомерно по [[src:gdpr art 6(1)(f)]].\n\n"
        "И ещё раз [[src:gdpr art 6(1)(f)]] плюс призрак [[src:ghost]].\n\n"
        "<!-- sources: generated -->\n"
    )

    def render_md(self, language: str = "ru") -> str:
        state = dict(FORCED_EXIT_STATE, language=language)
        return fallback.render(
            self.DRAFT,
            localized_index(),
            drafting_warnings=["Одно допущение осталось непроверенным."],
            state=state,
            citation_style=INLINE,
        )["markdown"]

    def test_a_russian_deliverable_carries_no_english_label(self):
        result = self.render_md()
        for key in LOCALIZED_KEYS:
            self.assertNotIn(i18n.t("en", f"memo.labels.{key}"), result, key)
            self.assertIn(i18n.t("ru", f"memo.labels.{key}"), result, key)

    def test_pinpoint_labels_are_translated_only_on_display(self):
        body = self.render_md().split(fallback.sources_heading("ru"))[0]
        self.assertIn("ст. 6(1)(f)", body)
        # The deep link still comes from the canonical pinpoint.
        self.assertIn("#art_6", body)
        self.assertNotIn("art 6(1)(f)]", body)

    def test_the_sources_line_names_the_cited_places_in_the_memo_language(self):
        self.assertIn("цитируется в ст. 6(1)(f)", self.render_md())
        # D-197: the link check says `dead`, so the annex prints no retrieval date at all.
        self.assertIn("актуальность нужна ручная проверка", self.render_md())
        self.assertNotIn("проверено 2026-09-10", self.render_md())

    def test_the_appendix_notes_and_their_statuses_are_localized(self):
        result = self.render_md()
        self.assertIn("актуальность нужна ручная проверка", result)
        self.assertIn("ссылка не открывается", result)
        self.assertNotIn("manual_check", result)
        self.assertNotIn("dead", result)

    def test_the_status_lead_and_its_sub_headings_are_localized(self):
        status = self.render_md().partition(fallback.status_heading("ru"))[2]
        self.assertIn(
            "Итоговый статус: выпущен с незакрытыми замечаниями (версия 1).", status
        )
        self.assertNotIn("forced_exit_on_v1_with_remaining_issues", status)
        self.assertIn("Уведомления конвейера", status)
        self.assertIn("Нерешённые блокирующие замечания", status)

    def test_the_blocker_rows_name_the_severity_and_the_section_in_russian(self):
        status = self.render_md().partition(fallback.status_heading("ru"))[2]
        self.assertIn("- блокирующее замечание · раздел 1 · ", status)
        self.assertNotIn("· s-1 ·", status)

    def test_english_is_what_it_was_before_the_language_existed(self):
        without = fallback.render(
            self.DRAFT,
            localized_index(),
            drafting_warnings=["Одно допущение осталось непроверенным."],
            state=dict(FORCED_EXIT_STATE),
            citation_style=INLINE,
        )["markdown"]
        self.assertEqual(without, self.render_md(language="en"))
        self.assertIn("cited at art 6(1)(f)", without)


class LocalizedLinkLessSourceTest(_PackedTestCase):
    """D-192: the `retrieved from` note of the annex is written in the memo language."""

    def test_the_russian_annex_names_the_database_in_russian(self):
        rendered = oscola.sources_entry(annex_row(LINK_LESS_RECORD), "ru")
        self.assertIn("текст получен из базы CasusLegal (RU)", rendered)
        self.assertNotIn("text retrieved from", rendered)
        self.assertNotIn("<", rendered)

    def test_an_unknown_endpoint_falls_back_to_the_russian_generic_word(self):
        record = dict(LINK_LESS_RECORD, retrieved_from="https://endpoint.example/one/mcp")
        rendered = oscola.sources_entry(annex_row(record), "ru")
        self.assertIn("текст получен из базы юридических данных", rendered)


class BannerLanguageSignatureTest(_PackedTestCase):
    """D-175: render and finalize hash the same banner strings, in the memo language."""

    OVERRIDES: dict = {
        **RU_DELIVERABLE,
        "memo.banners.mcp_partial": "Частичное покрытие MCP — доступен только {available}.",
    }

    def test_the_status_signature_is_the_same_at_render_and_at_finalize_for_a_russian_task(self):
        from memoforge import finalize as _finalize

        banners = [fallbacks.banner("mcp_partial", available="legalviz")]
        render_state = dict(FORCED_EXIT_STATE, language="ru", fallback_banners=list(banners))
        render_signature = docx.status_signature(render_state)
        gathered = _finalize.collect_banners(
            {"fallback_banners": []}, list(banners) + list(render_state["fallback_banners"])
        )
        view = _finalize._status_view(render_state, render_state["final_status"], gathered)
        self.assertEqual(render_signature, docx.status_signature(view))

    def test_a_german_warning_is_not_cut_at_an_abbreviation(self):
        _i18n.fake_pack(self.packs, "de", {"memo.abbreviations": ["gem", "art", "abs"]})
        warning = {
            "code": "unresolved_research_gap",
            "message": (
                "Die Verarbeitung ist gem. Art. 6 Abs. 1 DSGVO nur zulässig, "
                "wenn ein Vertrag besteht. Zweiter Satz."
            ),
        }
        self.assertEqual(
            "Die Verarbeitung ist gem. Art. 6 Abs. 1 DSGVO nur zulässig, wenn ein Vertrag besteht.",
            fallback.assumption_bullet(warning, language="de"),
        )

    def test_an_english_warning_is_cut_at_the_first_boundary_abbreviations_notwithstanding(self):
        """English is frozen: the cut never consulted an abbreviation list, `art` included."""
        warning = {
            "code": "unresolved_research_gap",
            "message": "See art. Contract scope remains open. Second sentence.",
        }
        self.assertEqual("See art.", fallback.assumption_bullet(warning, language="en"))


class LocalizedStatusSignatureTest(_PackedTestCase):
    """D-175: `en` keeps the signature it always had; every other language is hashed with its code.

    Two runs whose `## Status` section differs only in the language it is written in must not share
    a signature, or `finalize` would hand the client an export in the language the run left behind.
    """

    def setUp(self) -> None:
        super().setUp()
        _i18n.fake_pack(self.packs, "de", {})

    def signature(self, language: str) -> str:
        return docx.status_signature(dict(FORCED_EXIT_STATE, language=language))

    def test_two_languages_with_the_same_section_do_not_share_a_signature(self):
        self.assertNotEqual(self.signature("ru"), self.signature("de"))
        self.assertNotEqual(self.signature("ru"), self.signature("en"))
        self.assertNotEqual(self.signature("de"), self.signature("en"))

    def test_a_non_english_signature_hashes_the_whole_dict(self):
        inputs = fallback.status_inputs(dict(FORCED_EXIT_STATE, language="ru"))
        self.assertEqual("ru", inputs["language"])
        payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(state_io.sha256_bytes(payload.encode("utf-8")), self.signature("ru"))


if __name__ == "__main__":
    unittest.main()
