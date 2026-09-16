"""Tests for scripts/memoforge/docx/ — the stdlib-only md fallback (ТЗ §5.5, §9 «md-fallback с токенами»)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import docx, fallbacks, state_io, task  # noqa: E402
from memoforge.docx import fallback, oscola  # noqa: E402

FOOTNOTES = oscola.STYLE_FOOTNOTES
INLINE = oscola.STYLE_INLINE


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
        self.assertIn(f" — {fallback.PINPOINT_LATER_PREFIX}para 93 — ", line)

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
        self.assertNotIn("[1]", markdown.split(fallback.SOURCES_HEADING)[0])
        self.assertNotIn("[2]", markdown.split(fallback.SOURCES_HEADING)[0])

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
        body = self.markdown().split(fallback.SOURCES_HEADING)[0]
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
            fallback.SOURCES_HEADING
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
        tail = markdown.split(fallback.SOURCES_HEADING, 1)[1]
        return [row for row in tail.splitlines() if row.startswith("[")]

    def test_ten_annex_entries_instead_of_twenty_two(self):
        for style in (INLINE, FOOTNOTES):
            rendered = self.render(style)
            self.assertEqual(10, len(rendered["footnotes"]), style)
            self.assertEqual(10, len(self.annex(rendered["markdown"])), style)

    def test_the_gdpr_is_spelled_out_once_in_the_body(self):
        body = self.render(INLINE)["markdown"].split(fallback.SOURCES_HEADING)[0]
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
        self.assertIn("checked 2026-09-10, currency unchecked", line)
        self.assertNotIn("Territorial scope", line)
        self.assertNotIn("#art", line)

    def test_the_body_links_still_point_at_the_article_anchor(self):
        body = self.render(INLINE)["markdown"].split(fallback.SOURCES_HEADING)[0]
        self.assertIn("(https://eur-lex.europa.eu/eli/reg/2016/679/oj#art44)", body)


class AppendixTest(unittest.TestCase):
    def test_drafting_warnings_go_into_the_appendix(self):
        rendered = fallback.render(
            "Body [[src:gdpr-art6]].",
            index(),
            drafting_warnings=[
                {"code": "research_partial", "message": "case_law layer did not complete"},
                "doctrine gap accepted by the user",
            ],
        )
        self.assertIn("Assumptions & Unverified Sources", rendered["markdown"])
        self.assertIn("case_law layer did not complete", rendered["markdown"])
        self.assertIn("doctrine gap accepted by the user", rendered["markdown"])
        # D-113: the `(warning_id)` tag is machine talk and stays in `summary.md`.
        self.assertNotIn("`research_partial`", rendered["markdown"])

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
        rendered = fallback.render("Body.", unverified)
        self.assertIn("Unverified sources", rendered["markdown"])
        self.assertIn("Doe v Roe, 1 F.3d 1", rendered["markdown"])
        self.assertIn("US citation unresolved", rendered["markdown"])
        self.assertIn("currency manual_check", rendered["markdown"])
        self.assertIn("link dead", rendered["markdown"])
        self.assertNotIn("GDPR art 6", rendered["markdown"])

    def test_no_appendix_when_there_is_nothing_to_disclose(self):
        rendered = fallback.render("Body [[src:gdpr-art6]].", index())
        self.assertNotIn("Assumptions & Unverified Sources", rendered["markdown"])

    def test_unresolved_ids_are_listed_in_the_appendix(self):
        rendered = fallback.render("Body [[src:ghost]].", index())
        self.assertIn("Unresolved references", rendered["markdown"])
        self.assertIn("`ghost`", rendered["markdown"])


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


class AssumptionBulletsTest(unittest.TestCase):
    """D-113: the appendix carries the gist of each warning, not the reviewer's prose."""

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
        self.assertIn(fallback.STATUS_HEADING, markdown)
        return markdown.partition(fallback.STATUS_HEADING)[2].partition("\n## ")[0]

    def test_the_banners_and_the_blockers_reach_the_deliverable(self):
        markdown = fallback.render("Body.\n", index(), state=FORCED_EXIT_STATE)["markdown"]
        status = self.status(markdown)
        self.assertIn("forced_exit_on_v1_with_remaining_issues", status)
        self.assertIn("REVIEWER NOTES NOT FULLY RESOLVED", status)
        self.assertIn("- blocker · s-1 · Art. 17(1) is cited without a rule in section 1.", status)
        self.assertIn("- blocker · s-2 · Art. 17(1) is cited without a rule in section 2.", status)

    def test_the_status_section_stands_before_the_appendix(self):
        markdown = fallback.render(
            "Body.\n",
            index(),
            state=FORCED_EXIT_STATE,
            drafting_warnings=["one warning"],
        )["markdown"]
        self.assertLess(
            markdown.index(fallback.STATUS_HEADING), markdown.index(fallback.APPENDIX_HEADING)
        )

    def test_an_approved_run_carries_no_status_section(self):
        for status in ("approved_on_v2", "client_ready_on_v2"):
            with self.subTest(final_status=status):
                markdown = fallback.render("Body.\n", index(), state={"final_status": status})[
                    "markdown"
                ]
                self.assertNotIn(fallback.STATUS_HEADING, markdown)

    def test_a_run_without_a_final_status_yet_carries_no_status_section(self):
        markdown = fallback.render("Body.\n", index(), state={})["markdown"]
        self.assertNotIn(fallback.STATUS_HEADING, markdown)

    def test_the_blocker_list_is_capped_and_points_at_the_summary(self):
        state = dict(FORCED_EXIT_STATE, remaining_blocking_issues=blockers_fixture(15))
        status = self.status(fallback.render("Body.\n", index(), state=state)["markdown"])
        rows = [row for row in status.splitlines() if row.startswith("- blocker · ")]
        self.assertEqual(fallback.STATUS_ISSUE_LIMIT, len(rows))
        self.assertIn("- … and 3 more in summary.md", status)

    def test_the_banner_this_render_raised_is_listed_too(self):
        markdown = fallback.render("Body [[src:ghost]].\n", index(), state=FORCED_EXIT_STATE)[
            "markdown"
        ]
        self.assertIn("unresolved", self.status(markdown).lower())

    def test_a_blocker_row_is_severity_section_and_issue(self):
        self.assertEqual(
            "blocker · s-4 · Something is wrong.",
            fallback.blocking_issue_line(
                {"severity": "blocker", "section_id": "s-4", "issue": "Something is wrong."}
            ),
        )
        self.assertEqual("a plain string", fallback.blocking_issue_line("a plain string"))
        self.assertEqual("", fallback.blocking_issue_line({}))


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
        self.assertIn("currency manual_check", str(rows))

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
            "Body.",
            fallback.SourceIndex(sources=self.registry(), currency_unavailable=True),
            drafting_warnings=warnings_fixture(),
        )
        markdown = rendered["markdown"]
        self.assertIn(f"- {fallback.CURRENCY_UNAVAILABLE_NOTE}", markdown)
        self.assertEqual(1, markdown.count(fallback.CURRENCY_UNAVAILABLE_NOTE))
        self.assertNotIn("currency unchecked", markdown)
        self.assertIn("- AI Act, Annex III(4) — link changed", markdown)
        self.assertIn("- … and 2 more in summary.md", markdown)


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


if __name__ == "__main__":
    unittest.main()
