"""Tests for `scripts/memoforge/docx/oscola.py` — one citation form per source class (ТЗ §5.5, D-150).

`compact` is the first mention, `short` every later one; neither may carry a URL, a CELEX/ELI tag or
a retrieval date — those belong to the `## Sources` annex (`sources_entry`). The last case renders
every citation of the real run `memo-20260910T095310Z-we-re-a-us-based-saas-company-planning`, whose
footnotes were the concatenated walls of text that motivated the decision.
"""

from __future__ import annotations

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
from memoforge import i18n  # noqa: E402
from memoforge.docx import oscola  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
REAL_RUN = FIXTURES / "real-run-sources.json"

# --- one record per source class -------------------------------------------

EU_LEGISLATION = {
    "layer": "statutes",
    "title": "GDPR Article 35 (Data protection impact assessment)",
    "citation_form": "Regulation (EU) 2016/679 (GDPR), art 35",
    "identifiers": {"celex": "32016R0679", "eli": "http://data.europa.eu/eli/reg/2016/679/oj"},
    "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
    "retrieved_at": "2026-09-10T09:55:07.615Z",
    "meta": {
        "issuing_body": "European Parliament and Council",
        "year": "2016",
        "short_name": "GDPR",
    },
    "currency": {"status": "unchecked"},
    # D-197: the annex prints `checked <date>` only for a link the run actually reached, so the
    # record of a source whose date is asserted below carries the successful check that earned it.
    "liveness": {"status": "ok", "code": 200, "checked_at": "2026-09-10T09:55:07.615Z"},
}
UK_LEGISLATION = {
    "layer": "statutes",
    "title": "Data Protection Act 2018",
    "citation_form": "Data Protection Act 2018",
    "url": "https://www.legislation.gov.uk/ukpga/2018/12",
    "meta": {"short_name": "DPA 2018", "jurisdiction": "UK"},
}
US_LEGISLATION = {
    "layer": "statutes",
    "title": "Federal Trade Commission Act, section 5",
    "citation_form": "15 USC § 45",
    "url": "https://www.law.cornell.edu/uscode/text/15/45",
    "meta": {"short_name": "FTC Act"},
}
CJEU = {
    "layer": "case_law",
    "title": "Data Protection Commissioner v Facebook Ireland Ltd and Schrems (Schrems II)",
    "citation_form": (
        "Case C-311/18, Data Protection Commissioner v Facebook Ireland Ltd and "
        "Maximillian Schrems, EU:C:2020:559"
    ),
    "identifiers": {"celex": "62018CJ0311", "ecli": "ECLI:EU:C:2020:559"},
    "url": "https://curia.europa.eu/juris/liste.jsf?num=C-311/18",
    "meta": {
        "court": "Court of Justice of the European Union (Grand Chamber)",
        "year": 2020,
        "date": "2020-07-16",
        "short_name": "Schrems II",
    },
}
OTHER_COURT = {
    "layer": "case_law",
    "title": "Mobley v Workday Inc (N.D. Cal. docket)",
    "citation_form": "Mobley v. Workday, Inc., No. 3:23-cv-00770 (N.D. Cal., filed Feb. 21, 2023)",
    "url": "https://www.courtlistener.com/docket/66886484/mobley-v-workday-inc/",
    "meta": {
        "court": "U.S. District Court for the Northern District of California",
        "year": 2023,
        "short_name": "Mobley v Workday",
    },
}
SOFT_LAW = {
    "layer": "doctrine",
    "title": "EDPB Recommendations 01/2020 on supplementary transfer measures",
    "citation_form": (
        "EDPB, Recommendations 01/2020 on measures that supplement transfer tools to ensure "
        "compliance with the EU level of protection of personal data, Version 2.0, 18 June 2021"
    ),
    "url": "https://www.edpb.europa.eu/sites/default/files/edpb_recommendations_202001_en.pdf",
    "meta": {
        "issuing_body": "European Data Protection Board",
        "date": "2021-06-18",
        "year": 2021,
        "short_name": "EDPB Recommendations 01/2020",
    },
}
DOCTRINE = {
    "layer": "doctrine",
    "title": "Abraha, A pragmatic compromise? Article 88 GDPR in the workplace, IDPL 2022",
    "citation_form": (
        "Halefom H Abraha, 'A pragmatic compromise? The role of Article 88 GDPR in upholding "
        "privacy in the workplace' (2022) 12(4) International Data Privacy Law 276"
    ),
    "url": "https://academic.oup.com/idpl/article/12/4/276/6668508",
    "meta": {
        "issuing_body": "International Data Privacy Law (Oxford Academic, peer-reviewed)",
        "year": 2022,
        "short_name": "Abraha, IDPL 2022",
    },
}

ALL_CLASSES = {
    oscola.CLASS_EU_LEGISLATION: EU_LEGISLATION,
    oscola.CLASS_LEGISLATION: UK_LEGISLATION,
    oscola.CLASS_CJEU: CJEU,
    oscola.CLASS_CASE: OTHER_COURT,
    oscola.CLASS_SOFT_LAW: SOFT_LAW,
    oscola.CLASS_DOCTRINE: DOCTRINE,
}


def view(record: dict, source_id: str = "src") -> dict:
    return oscola.view_of(source_id, None, record)


def mention(record: dict, pinpoint: str = "", source_id: str = "src") -> dict:
    """One scanned mention the way `fallback.scan_mentions` builds it (D-150)."""
    item = view(record, source_id)
    return {
        "source_id": source_id,
        "view": item,
        "pinpoint": pinpoint,
        "resolved": True,
        "raw_id": source_id,
    }


class SourceClassTest(unittest.TestCase):
    def test_every_class_is_detected_from_the_identifiers_and_the_meta(self):
        for expected, record in ALL_CLASSES.items():
            self.assertEqual(expected, oscola.source_class(view(record)), expected)

    def test_us_legislation_is_national_legislation(self):
        self.assertEqual(oscola.CLASS_LEGISLATION, oscola.source_class(view(US_LEGISLATION)))

    def test_a_journal_article_filed_under_doctrine_is_not_read_as_a_statute(self):
        """`International Data Privacy Law` carries the word «Law»; it is still an article."""
        self.assertEqual(oscola.CLASS_DOCTRINE, oscola.source_class(view(DOCTRINE)))

    def test_a_record_with_nothing_but_a_title_is_soft_law(self):
        self.assertEqual(
            oscola.CLASS_SOFT_LAW, oscola.source_class(view({"title": "An internal note"}))
        )


class CompactFormTest(unittest.TestCase):
    """The first mention of a source, one form per class (D-150)."""

    def test_eu_legislation(self):
        self.assertEqual(
            "Regulation (EU) 2016/679 (GDPR), art 35(3)(a)",
            oscola.compact(view(EU_LEGISLATION), "Art. 35(3)(a)"),
        )

    def test_eu_legislation_falls_back_to_the_article_of_the_record(self):
        self.assertEqual(
            "Regulation (EU) 2016/679 (GDPR), art 35", oscola.compact(view(EU_LEGISLATION))
        )

    def test_eu_legislation_is_derived_from_the_celex_when_the_form_has_no_designation(self):
        record = dict(EU_LEGISLATION, citation_form="GDPR", title="GDPR")
        self.assertEqual("Regulation (EU) 2016/679 (GDPR)", oscola.compact(view(record)))

    def test_uk_legislation(self):
        self.assertEqual(
            "Data Protection Act 2018, s 2(1)", oscola.compact(view(UK_LEGISLATION), "s. 2(1)")
        )

    def test_us_legislation(self):
        self.assertEqual("15 USC § 45", oscola.compact(view(US_LEGISLATION)))

    def test_a_cjeu_case(self):
        self.assertEqual(
            "Case C-311/18 Data Protection Commissioner v Facebook Ireland and Schrems "
            "EU:C:2020:559, para 2 of the operative part",
            oscola.compact(view(CJEU), "para 2 of the operative part"),
        )

    def test_another_court_keeps_its_conventional_form(self):
        self.assertEqual(
            "Mobley v. Workday, Inc., No. 3:23-cv-00770 (N.D. Cal., filed Feb. 21, 2023)",
            oscola.compact(view(OTHER_COURT)),
        )

    def test_soft_law(self):
        self.assertEqual(
            "EDPB, Recommendations 01/2020 (v 2.0, 18 June 2021) paras 44, 89",
            oscola.compact(view(SOFT_LAW), "paras 44, 89"),
        )

    def test_doctrine(self):
        self.assertEqual(
            "Abraha, 'A pragmatic compromise?' (2022) 12 IDPL 276, 280",
            oscola.compact(view(DOCTRINE), "280"),
        )

    def test_no_compact_form_carries_a_url_a_celex_or_a_retrieval_date(self):
        for name, record in ALL_CLASSES.items():
            rendered = oscola.compact(view(record), "art 6")
            self.assertNotIn("http", rendered, name)
            self.assertNotIn("CELEX", rendered, name)
            self.assertNotIn("retrieved", rendered, name)

    def test_a_case_name_that_would_run_past_the_cap_is_cited_by_its_short_name(self):
        record = dict(
            CJEU,
            title=(
                "Data Protection Commissioner v Facebook Ireland Limited, Maximillian Schrems "
                "and the Government of the United States of America intervening"
            ),
        )
        rendered = oscola.compact(view(record), "para 2 of the operative part")
        self.assertEqual(
            "Case C-311/18 Schrems II EU:C:2020:559, para 2 of the operative part", rendered
        )
        self.assertLessEqual(len(rendered), oscola.MAX_COMPACT_CHARS)

    def test_a_record_with_only_a_citation_form_is_trimmed_not_concatenated(self):
        record = {
            "title": "GDPR Article 35 - Data protection impact assessment",
            "citation_form": (
                "Regulation (EU) 2016/679, OJ L 119, 4.5.2016, p. 1 (CELEX 32016R0679) "
                "<https://eur-lex.europa.eu/x>, retrieved 2026-09-10"
            ),
        }
        rendered = oscola.compact(view(record), "art 35(3)(a)")
        self.assertEqual("Regulation (EU) 2016/679, art 35(3)(a)", rendered)
        self.assertNotIn("Data protection impact assessment", rendered)


class ShortFormTest(unittest.TestCase):
    """Every later mention; `(n N)` only in the footnote style (D-150)."""

    def test_eu_legislation_is_cited_by_its_short_name(self):
        self.assertEqual("GDPR, art 88(1)", oscola.short(view(EU_LEGISLATION), "Art. 88(1)"))

    def test_the_footnote_style_adds_the_back_reference(self):
        self.assertEqual("Schrems II (n 19)", oscola.short(view(CJEU), "", 19))
        self.assertEqual(
            "EDPB Recommendations 01/2020 (n 20)", oscola.short(view(SOFT_LAW), "", 20)
        )
        self.assertEqual("Abraha (n 11)", oscola.short(view(DOCTRINE), "", 11))

    def test_the_inline_style_never_carries_a_back_reference(self):
        for record in ALL_CLASSES.values():
            self.assertNotIn("(n ", oscola.short(view(record), "para 4"))

    def test_a_case_without_a_short_name_is_cited_by_its_distinctive_party(self):
        record = dict(CJEU, title="Lloyd v Google LLC", meta={"court": "UKSC"})
        self.assertEqual("Google", oscola.short_name(view(record)))

    def test_a_nickname_in_brackets_becomes_the_short_name(self):
        record = dict(CJEU, meta={"court": "CJEU"})
        self.assertEqual("Schrems II", oscola.short_name(view(record)))

    def test_there_is_no_short_form_without_a_name(self):
        for record in ALL_CLASSES.values():
            self.assertTrue(oscola.short_name(view(record)))


class PinpointTest(unittest.TestCase):
    def test_the_four_shapes_of_the_decision(self):
        self.assertEqual("art 6(1)(b)", oscola.normalise_pinpoint("Art. 6(1)(b)"))
        self.assertEqual("para 89", oscola.normalise_pinpoint("Paragraph 89"))
        self.assertEqual("s 2(1)", oscola.normalise_pinpoint("s.2(1)"))
        self.assertEqual("§ 26", oscola.normalise_pinpoint("§26"))

    def test_plurals_and_annexes(self):
        self.assertEqual("paras 44, 89", oscola.normalise_pinpoint("Paras. 44, 89"))
        self.assertEqual("arts 5, 6", oscola.normalise_pinpoint("Articles 5, 6"))
        self.assertEqual("annex III point 4(b)", oscola.normalise_pinpoint("Annex III point 4(b)"))

    def test_regulations_normalise_to_reg_and_regs(self):
        self.assertEqual("reg 22(1)", oscola.normalise_pinpoint("Reg. 22(1)"))
        self.assertEqual("reg 5", oscola.normalise_pinpoint("Regulation 5"))
        self.assertEqual("regs 2-3", oscola.normalise_pinpoint("regs 2-3"))
        self.assertEqual("reg 22(1)", oscola.normalise_pinpoint("reg 22(1)"))

    def test_a_pinpoint_with_no_label_is_left_alone(self):
        self.assertEqual(
            "point 2 of the operative part",
            oscola.normalise_pinpoint("point 2 of the operative part"),
        )
        self.assertEqual("", oscola.normalise_pinpoint(None))

    def test_a_word_beginning_with_a_roman_letter_is_not_a_numeral(self):
        # D-195: the `(?=[0-9IVXL(])` lookahead read `Insurance`, `Liability` and `Violation`
        # as Roman numerals, so a clause of an offer was relabelled as an article.
        for text in ("Art. Insurance para 1.7", "Art. Liability 3", "s Violation 2"):
            with self.subTest(text=text):
                self.assertEqual(text, oscola.normalise_pinpoint(text))

    def test_a_real_roman_numeral_still_gets_the_label(self):
        self.assertEqual("art IV", oscola.normalise_pinpoint("Art. IV"))
        self.assertEqual("art IV(2)", oscola.normalise_pinpoint("Art. IV(2)"))
        self.assertEqual("annex III point 4(b)", oscola.normalise_pinpoint("Annex III point 4(b)"))


class StyleResolutionTest(unittest.TestCase):
    def test_both_templates_ship_the_inline_style(self):
        for template in ("executive-brief", "classical-memo"):
            self.assertEqual(oscola.STYLE_INLINE, oscola.template_style(template), template)

    def test_the_config_overrides_the_template(self):
        state = {"config": {"template_id": "classical-memo", "citation_style": "footnotes"}}
        self.assertEqual(oscola.STYLE_FOOTNOTES, oscola.resolve_style(state))

    def test_an_unknown_value_is_no_value(self):
        state = {"config": {"template_id": "classical-memo", "citation_style": "endnotes"}}
        self.assertEqual(oscola.STYLE_INLINE, oscola.resolve_style(state))

    def test_a_run_without_a_template_falls_back_to_the_default(self):
        self.assertEqual(oscola.DEFAULT_CITATION_STYLE, oscola.resolve_style({}))
        self.assertEqual(oscola.STYLE_INLINE, oscola.DEFAULT_CITATION_STYLE)


class FormAssignmentTest(unittest.TestCase):
    """`assign_forms` is the one place both renderers ask what a mention is (D-150)."""

    def mentions(self, *rows: tuple) -> list[dict]:
        return [
            {"source_id": source_id, "pinpoint": pinpoint, "resolved": True, "raw_id": source_id}
            for source_id, pinpoint in rows
        ]

    def test_first_then_short_then_ibid_in_the_footnote_style(self):
        rows = self.mentions(("a", "art 6"), ("b", ""), ("a", "art 7"), ("a", "art 7"))
        cited = oscola.assign_forms(rows, oscola.STYLE_FOOTNOTES)
        self.assertEqual(["a", "b"], cited)
        self.assertEqual(
            [("full", 1, 1), ("full", 2, 2), ("short", 3, 1), ("ibid", 4, 1)],
            [(row["form"], row["n"], row["first_n"]) for row in rows],
        )

    def test_the_repeat_is_dropped_instead_of_ibid_in_the_inline_style(self):
        rows = self.mentions(("a", "art 6"), ("a", "art 6"), ("b", ""))
        cited = oscola.assign_forms(rows, oscola.STYLE_INLINE)
        self.assertEqual(["a", "b"], cited)
        self.assertEqual(
            [("full", 1), ("omitted", 1), ("full", 2)],
            [(row["form"], row["first_n"]) for row in rows],
        )
        self.assertEqual([None, None, None], [row["n"] for row in rows])

    def test_the_same_source_with_a_different_pinpoint_is_a_short_form_not_an_ibid(self):
        rows = self.mentions(("a", "art 6"), ("a", "art 7"))
        oscola.assign_forms(rows, oscola.STYLE_INLINE)
        self.assertEqual(["full", "short"], [row["form"] for row in rows])

    def test_an_unresolved_mention_breaks_the_ibid_chain(self):
        rows = self.mentions(("a", "art 6")) + [
            {"source_id": None, "pinpoint": "", "resolved": False, "raw_id": "ghost"}
        ] + self.mentions(("a", "art 6"))
        oscola.assign_forms(rows, oscola.STYLE_FOOTNOTES)
        self.assertEqual("short", rows[2]["form"])

    def test_the_pinpoints_are_normalised_in_place(self):
        rows = self.mentions(("a", "Art. 6(1)(b)"))
        oscola.assign_forms(rows, oscola.STYLE_INLINE)
        self.assertEqual("art 6(1)(b)", rows[0]["pinpoint"])


def row(record: dict, *members: dict, pinpoints: str = "") -> dict:
    """One `## Sources` row the way `fallback.source_rows` builds it (D-150)."""
    views = [view(record)] + [view(extra) for extra in members]
    return {"n": 1, "view": views[0], "members": views, "pinpoints_text": pinpoints}


class SourcesEntryTest(unittest.TestCase):
    """The annex carries what the body does not, one entry per instrument (D-150)."""

    def test_the_full_record_of_a_regulation(self):
        rendered = oscola.sources_entry(row(EU_LEGISLATION, pinpoints="cited at art 35(3)(a)"))
        self.assertTrue(rendered.startswith("Regulation (EU) 2016/679 (GDPR) — "))
        self.assertIn("cited at art 35(3)(a)", rendered)
        self.assertIn("CELEX 32016R0679", rendered)
        self.assertIn("ELI http://data.europa.eu/eli/reg/2016/679/oj", rendered)
        self.assertIn("<https://eur-lex.europa.eu/eli/reg/2016/679/oj>", rendered)
        self.assertIn("checked 2026-09-10, currency unchecked", rendered)

    def test_the_article_level_titles_of_the_registry_are_not_printed(self):
        """The wall of text D-150 removed: eleven lines of `GDPR Article N - <subject>`."""
        rendered = oscola.sources_entry(row(EU_LEGISLATION))
        self.assertNotIn("Data protection impact assessment", rendered)
        self.assertNotIn(", art 35", rendered)

    def test_the_ecli_label_is_not_doubled(self):
        rendered = oscola.sources_entry(row(CJEU))
        self.assertIn("ECLI:EU:C:2020:559", rendered)
        self.assertNotIn("ECLI ECLI", rendered)

    def test_one_entry_carries_the_identifiers_of_every_article_cited(self):
        other = dict(EU_LEGISLATION, identifiers={"celex": "32016R0679"}, meta={"short_name": "GDPR"})
        rendered = oscola.sources_entry(row(EU_LEGISLATION, other))
        self.assertEqual(1, rendered.count("CELEX 32016R0679"))
        self.assertEqual(1, rendered.count("ELI "))

    def test_an_amended_article_is_named_next_to_the_currency_of_the_instrument(self):
        amended = dict(
            EU_LEGISLATION,
            citation_form="Regulation (EU) 2016/679 (GDPR), art 6",
            title="GDPR Article 6 (Lawfulness of processing)",
            currency={"status": "amended"},
        )
        rendered = oscola.sources_entry(row(EU_LEGISLATION, amended))
        self.assertIn("checked 2026-09-10, currency unchecked; art 6 amended", rendered)

    def test_the_url_is_the_instrument_not_an_article_anchor(self):
        anchored = dict(EU_LEGISLATION, url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art35")
        self.assertIn(
            "<https://eur-lex.europa.eu/eli/reg/2016/679/oj>", oscola.sources_entry(row(anchored))
        )


class InstrumentTest(unittest.TestCase):
    """D-150: the registry is article-level, the citation and the annex are instrument-level."""

    def articles(self) -> list[dict]:
        return [
            view(
                dict(
                    EU_LEGISLATION,
                    citation_form=f"Regulation (EU) 2016/679 (GDPR), art {number}",
                    title=f"GDPR Article {number}",
                    meta={"short_name": f"GDPR Art {number}"},
                ),
                f"gdpr-art{number}",
            )
            for number in (3, 6, 44)
        ]

    def test_every_article_of_one_act_shares_its_instrument_key(self):
        keys = {oscola.instrument_key(item) for item in self.articles()}
        self.assertEqual(1, len(keys), keys)

    def test_the_instrument_form_drops_the_article_of_the_record(self):
        self.assertEqual(
            "Regulation (EU) 2016/679 (GDPR)", oscola.instrument_form(self.articles()[0])
        )

    def test_two_acts_are_two_instruments(self):
        ai_act = view(
            {
                "layer": "statutes",
                "citation_form": "Regulation (EU) 2024/1689 (AI Act), art 26",
                "identifiers": {"celex": "32024R1689"},
            }
        )
        self.assertNotEqual(oscola.instrument_key(self.articles()[0]), oscola.instrument_key(ai_act))

    def test_a_celex_and_an_eli_for_one_act_share_a_key(self):
        """D-152: identifiers are parsed by type, so neither record is a first mention of its own."""
        by_celex = view(
            {"layer": "statutes", "title": "GDPR Article 6", "citation_form": "GDPR art 6",
             "identifiers": {"celex": "32016R0679"}},
            "by-celex",
        )
        by_eli = view(
            {"layer": "statutes", "title": "GDPR", "citation_form": "GDPR",
             "identifiers": {"eli": "http://data.europa.eu/eli/reg/2016/679/oj"}},
            "by-eli",
        )
        self.assertEqual(oscola.CLASS_EU_LEGISLATION, oscola.source_class(by_eli))
        self.assertEqual(oscola.instrument_key(by_celex), oscola.instrument_key(by_eli))
        mentions = [
            {"source_id": item["source_id"], "view": item, "pinpoint": "", "resolved": True}
            for item in (by_celex, by_eli)
        ]
        self.assertEqual(1, len(oscola.assign_forms(mentions, oscola.STYLE_INLINE)))
        self.assertEqual(["full", "short"], [row["form"] for row in mentions])

    def test_a_sector_six_celex_is_a_decision_not_a_regulation(self):
        record = view(
            {"layer": "case_law", "title": "DPC v Facebook Ireland and Schrems",
             "citation_form": "Schrems II", "identifiers": {"celex": "62018CJ0311"}},
            "schrems",
        )
        self.assertEqual(oscola.CLASS_CJEU, oscola.source_class(record))
        self.assertEqual("C-311/18", oscola.case_from_celex("62018CJ0311"))
        self.assertEqual("cjeu:c-311/18", oscola.instrument_key(record))
        self.assertTrue(oscola.compact(record).startswith("Case C-311/18 "))

    def test_the_celex_and_eli_parsers_only_answer_for_their_own_sector(self):
        self.assertEqual("Regulation (EU) 2016/679", oscola.act_from_celex("32016R0679"))
        self.assertEqual("Directive (EC) 1995/46", oscola.act_from_celex("31995L0046"))
        self.assertEqual("", oscola.act_from_celex("62018CJ0311"))
        self.assertEqual("", oscola.case_from_celex("32016R0679"))
        self.assertEqual(
            "Decision (EU) 2023/1795",
            oscola.act_from_eli(view({"identifiers": {"eli": "/eli/dec_impl/2023/1795/oj"}})),
        )

    def test_a_case_is_keyed_by_its_ecli(self):
        other_title = dict(CJEU, title="Schrems v Data Protection Commissioner")
        self.assertEqual(oscola.instrument_key(view(CJEU)), oscola.instrument_key(view(other_title)))

    def test_only_the_first_article_of_an_act_is_a_full_citation(self):
        mentions = [
            {"source_id": item["source_id"], "view": item, "pinpoint": "", "resolved": True}
            for item in self.articles()
        ]
        instruments = oscola.assign_forms(mentions, oscola.STYLE_INLINE)
        self.assertEqual(1, len(instruments))
        self.assertEqual(["full", "short", "short"], [row["form"] for row in mentions])
        rendered = [
            oscola.mention_text(row["view"], row, oscola.STYLE_INLINE) for row in mentions
        ]
        self.assertEqual(
            [
                "Regulation (EU) 2016/679 (GDPR), art 3",
                "GDPR, art 6",
                "GDPR, art 44",
            ],
            rendered,
        )

    def test_two_articles_of_one_act_are_never_ibid(self):
        mentions = [
            {"source_id": item["source_id"], "view": item, "pinpoint": "", "resolved": True}
            for item in self.articles()[:2]
        ]
        oscola.assign_forms(mentions, oscola.STYLE_FOOTNOTES)
        self.assertEqual(["full", "short"], [row["form"] for row in mentions])

    def test_the_same_article_twice_running_is_ibid(self):
        first = self.articles()[0]
        mentions = [
            {"source_id": first["source_id"], "view": first, "pinpoint": "", "resolved": True}
            for _ in range(2)
        ]
        oscola.assign_forms(mentions, oscola.STYLE_FOOTNOTES)
        self.assertEqual(["full", "ibid"], [row["form"] for row in mentions])


class AnchorTest(unittest.TestCase):
    def test_without_a_consolidated_celex_the_link_is_the_registered_url(self):
        self.assertEqual(
            EU_LEGISLATION["url"], oscola.anchor_url(view(EU_LEGISLATION), "art 35(3)(a)")
        )

    def test_a_consolidated_celex_earns_the_article_anchor(self):
        record = dict(
            EU_LEGISLATION,
            identifiers=dict(EU_LEGISLATION["identifiers"], celex_consolidated="02016R0679-20160504"),
        )
        self.assertEqual(
            "https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_35",
            oscola.anchor_url(view(record), "Art. 35(3)(a)"),
        )

    def test_a_source_without_a_url_is_not_linked(self):
        self.assertEqual("", oscola.anchor_url(view({"title": "Unpublished note"}), "para 1"))

    def test_a_router_fragment_carrying_a_credential_is_never_linked_or_printed(self):
        # FF1: `#?t=…` and `#/document?t=…` survived the fragment check, so the token reached the
        # inline hyperlink and the angle-bracket URL of the annex.
        for fragment in ("#?t=TESTTOKEN", "#/document?t=TESTTOKEN"):
            with self.subTest(fragment=fragment):
                record = dict(EU_LEGISLATION, url=EU_LEGISLATION["url"] + fragment)
                linked = oscola.anchor_url(view(record), "art 35(3)(a)")
                entry = oscola.sources_entry(row(record, pinpoints="cited at art 35(3)(a)"))
                self.assertEqual(EU_LEGISLATION["url"], linked)
                self.assertIn(f"<{EU_LEGISLATION['url']}>", entry)
                self.assertNotIn("TESTTOKEN", linked)
                self.assertNotIn("TESTTOKEN", entry)

    def test_a_router_anchor_without_a_credential_still_reaches_the_link(self):
        record = dict(EU_LEGISLATION, url=EU_LEGISLATION["url"] + "#/document/12")
        self.assertEqual(
            EU_LEGISLATION["url"] + "#/document/12",
            oscola.anchor_url(view(record), "art 35(3)(a)"),
        )


class RealRunTest(unittest.TestCase):
    """The run whose footnotes were «GDPR Article 35 …, CELEX 32016R0679, Art. 35(3)(a)» (D-150).

    Every citation of that memorandum, in both styles, must fit in a line and carry no URL, no
    CELEX and no retrieval date.
    """

    def setUp(self) -> None:
        payload = json.loads(REAL_RUN.read_text(encoding="utf-8-sig"))
        self.sources = payload["sources"]
        self.raw = payload["mentions"]

    def mentions(self, style: str) -> list[dict]:
        rows = [
            dict(
                row,
                resolved=True,
                raw_id=row["source_id"],
                view=oscola.view_of(row["source_id"], None, self.sources[row["source_id"]]),
            )
            for row in self.raw
        ]
        oscola.assign_forms(rows, style)
        return rows

    def rendered(self, style: str) -> list[tuple[str, str]]:
        return [
            (row["source_id"], oscola.mention_text(row["view"], row, style))
            for row in self.mentions(style)
        ]

    def test_every_citation_of_the_run_fits_in_a_line_in_both_styles(self):
        for style in oscola.CITATION_STYLES:
            for source_id, text in self.rendered(style):
                self.assertLessEqual(len(text), 120, f"{style}: {source_id}: {text}")

    def test_no_citation_of_the_run_carries_a_url_a_celex_or_a_retrieval_date(self):
        for style in oscola.CITATION_STYLES:
            for source_id, text in self.rendered(style):
                for forbidden in ("http", "CELEX", "celex", "retrieved", "ELI ", "ECLI"):
                    self.assertNotIn(forbidden, text, f"{style}: {source_id}: {text}")

    def test_the_twenty_two_sources_of_the_run_keep_one_form_per_class(self):
        first = {
            source_id: text
            for source_id, text in self.rendered(oscola.STYLE_INLINE)
        }
        self.assertEqual(
            "GDPR, art 35(3)(a)",
            first["gdpr-article-35-data-protection-impact-assessment"],
        )
        self.assertEqual(
            "EDPB, Recommendations 01/2020 (v 2.0, 18 June 2021) paras 44, 89",
            first["edpb-recommendations-01-2020-on-supplementary-transfer-measures"],
        )
        self.assertTrue(
            first["data-protection-commissioner-v-facebook-ireland-ltd-and-schrems-schrems-ii"]
            .startswith("Case C-311/18 ")
        )
        self.assertTrue(
            first["abraha-a-pragmatic-compromise-article-88-gdpr-in-the-workplace-idpl-2022"]
            .startswith("Abraha, 'A pragmatic compromise?' (2022) 12 IDPL 276")
        )

    def test_the_run_cites_ten_instruments_not_twenty_two_article_level_sources(self):
        """The annex of that run listed 22 article-level lines; the memo cites ten works (D-150)."""
        for style in oscola.CITATION_STYLES:
            instruments = {row["instrument"] for row in self.mentions(style)}
            self.assertEqual(10, len(instruments), sorted(instruments))
            self.assertEqual(22, len({row["source_id"] for row in self.mentions(style)}))

    def test_the_gdpr_is_a_full_citation_exactly_once(self):
        for style in oscola.CITATION_STYLES:
            full = [
                row
                for row in self.mentions(style)
                if row["form"] == oscola.FORM_FULL and "2016/679" in row["instrument"]
            ]
            self.assertEqual(1, len(full), [row["source_id"] for row in full])
            self.assertEqual(
                "Regulation (EU) 2016/679 (GDPR), art 3(2)(a)",
                oscola.mention_text(full[0]["view"], full[0], style),
            )

    def test_every_later_article_of_the_gdpr_is_a_short_form(self):
        texts = dict(self.rendered(oscola.STYLE_INLINE))
        self.assertEqual("GDPR, art 28(3)", texts["gdpr-article-28-processor"])
        self.assertEqual("GDPR, art 44", texts["gdpr-article-44-general-principle-for-transfers"])
        self.assertEqual("AI Act, art 113", texts["ai-act-article-113-entry-into-force-and-application"])

    def test_the_annex_of_the_run_is_where_the_identifiers_and_the_urls_live(self):
        members = [
            oscola.view_of(source_id, None, record)
            for source_id, record in self.sources.items()
            if source_id.startswith("gdpr-article-")
        ]
        rendered = oscola.sources_entry(
            {"n": 1, "view": members[0], "members": members, "pinpoints_text": "cited at art 35(3)(a)"}
        )
        self.assertTrue(rendered.startswith("Regulation (EU) 2016/679 (GDPR) — "))
        self.assertIn("CELEX 32016R0679", rendered)
        self.assertIn("<https://eur-lex.europa.eu/eli/reg/2016/679/oj>", rendered)
        # D-197 (fix round 1): no record of that run carries a liveness check, so the annex states
        # the currency and nothing about a retrieval it cannot vouch for.
        self.assertIn("currency unchecked", rendered)
        self.assertNotIn("checked 2026-09-10", rendered)
        self.assertNotIn("Territorial scope", rendered)
        self.assertNotIn("#art", rendered)


class ViewTest(unittest.TestCase):
    def test_the_snapshot_entry_wins_over_the_registry_record(self):
        entry = {"title": "From the snapshot", "url": "https://snapshot.example"}
        merged = oscola.view_of("x", entry, {"title": "Stale", "url": "https://stale.example"})
        self.assertEqual("From the snapshot", merged["title"])
        self.assertEqual("https://snapshot.example", merged["url"])

    def test_the_registry_fills_what_the_snapshot_does_not_carry(self):
        merged = oscola.view_of("x", {"layer": "statutes"}, {"title": "From the registry"})
        self.assertEqual("From the registry", merged["title"])

    def test_the_currency_status_reaches_the_view_from_either_side(self):
        self.assertEqual(
            "unchecked", oscola.view_of("x", {"currency_status": "unchecked"}, None)["currency_status"]
        )
        self.assertEqual(
            "manual_check",
            oscola.view_of("x", None, {"currency": {"status": "manual_check"}})["currency_status"],
        )

    def test_a_source_with_no_data_at_all_still_renders_its_id(self):
        self.assertEqual("x", oscola.compact(oscola.view_of("x", None, None)))


class DateTest(unittest.TestCase):
    def test_a_timestamp_is_cut_to_its_date(self):
        self.assertEqual("2026-09-01", oscola.date_only("2026-09-01T10:00:00Z"))

    def test_a_value_that_is_not_a_date_is_left_alone(self):
        self.assertEqual("Michaelmas term 2020", oscola.date_only("Michaelmas term 2020"))

    def test_none_becomes_the_empty_string(self):
        self.assertEqual("", oscola.date_only(None))

    def test_the_long_form_is_the_one_oscola_prints(self):
        self.assertEqual("18 June 2021", oscola.long_date("2021-06-18"))
        self.assertEqual("not a date", oscola.long_date("not a date"))


class UnresolvedTest(unittest.TestCase):
    def test_the_marker_is_the_one_the_fallback_writes(self):
        self.assertEqual("[unresolved: x]", oscola.unresolved_text("x"))


RU_CITATION: dict = {
    "memo.citation.art": "ст.",
    "memo.citation.arts": "стт.",
    "memo.citation.para": "п.",
    "memo.citation.paras": "пп.",
    "memo.citation.reg": "рег.",
    "memo.citation.regs": "рег.",
    "memo.citation.s": "ст.",
    "memo.citation.ss": "ст.",
    "memo.citation.annex": "приложение",
    "memo.currency_names.unchecked": "не проверялась",
    "memo.link_names.dead": "не открывается",
    "memo.citation.ibid": "там же",
    "memo.citation.cited_at": "цитируется в ",
    "memo.citation.also_cited_at": "также цитируется в ",
    "memo.citation.checked": "проверено ",
    "memo.citation.currency": "актуальность ",
    "memo.months": [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ],
}
"""A Russian citation vocabulary; everything else in the pack stays the English floor."""


class LocalizedCitationTest(unittest.TestCase):
    """D-175a: the pinpoint is canonical English everywhere but the moment it is printed."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packs = Path(tmp.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", RU_CITATION)

    def test_pinpoint_labels_are_translated_only_on_display(self):
        self.assertEqual("ст. 6(1)(f)", oscola.display_pinpoint("art 6(1)(f)", "ru"))
        self.assertEqual("art 6(1)(f)", oscola.display_pinpoint("art 6(1)(f)", "en"))
        # The form the tokens, the anchors and the `ibid` decision run on never moves.
        self.assertEqual("art 6(1)(f)", oscola.normalise_pinpoint("Art. 6(1)(f)"))

    def test_the_plural_label_is_not_taken_for_the_singular_one(self):
        self.assertEqual("стт. 5, 6", oscola.display_pinpoint("arts 5, 6", "ru"))
        self.assertEqual("пп. 44, 89", oscola.display_pinpoint("paras 44, 89", "ru"))

    def test_reg_is_localized_on_display(self):
        self.assertEqual("рег. 22(1)", oscola.display_pinpoint("reg 22(1)", "ru"))
        self.assertEqual("reg 22(1)", oscola.display_pinpoint("reg 22(1)", "en"))

    def test_a_pinpoint_without_a_label_is_displayed_as_written(self):
        self.assertEqual("§ 26", oscola.display_pinpoint("§ 26", "ru"))
        self.assertEqual("", oscola.display_pinpoint("", "ru"))
        self.assertEqual(
            "point 2 of the operative part",
            oscola.display_pinpoint("point 2 of the operative part", "ru"),
        )

    def test_a_citation_carries_the_localized_pinpoint(self):
        self.assertEqual(
            "GDPR, ст. 88(1)", oscola.short(view(EU_LEGISLATION), "Art. 88(1)", language="ru")
        )
        self.assertIn(
            "ст. 35(3)(a)", oscola.compact(view(EU_LEGISLATION), "art 35(3)(a)", language="ru")
        )

    def test_ibid_is_decided_on_canonical_pinpoints_and_printed_localized(self):
        rows = [
            {"source_id": "a", "pinpoint": "Art. 6", "resolved": True, "raw_id": "a"},
            {"source_id": "a", "pinpoint": "art 6", "resolved": True, "raw_id": "a"},
        ]
        oscola.assign_forms(rows, oscola.STYLE_FOOTNOTES)
        self.assertEqual(["full", "ibid"], [row["form"] for row in rows])
        self.assertEqual(
            "там же",
            oscola.mention_text(
                view(EU_LEGISLATION), rows[1], oscola.STYLE_FOOTNOTES, language="ru"
            ),
        )

    def test_a_soft_law_date_uses_localized_month_names(self):
        self.assertEqual("18 июня 2021", oscola.long_date("2021-06-18", "ru"))
        self.assertEqual("18 June 2021", oscola.long_date("2021-06-18"))
        self.assertIn("18 июня 2021", oscola.compact(view(SOFT_LAW), language="ru"))

    def test_the_annex_provenance_and_its_status_are_both_localized(self):
        # D-197: the recorded currency token reaches the reader through `memo.currency_names`.
        rendered = oscola.sources_entry(row(EU_LEGISLATION), language="ru")
        self.assertIn("проверено 2026-09-10", rendered)
        self.assertIn("актуальность не проверялась", rendered)
        self.assertNotIn("checked 2026-09-10", rendered)
        self.assertNotIn("unchecked", rendered)

    def test_every_label_of_the_pinpoint_is_translated_not_only_the_head(self):
        # D-195: `art 998 para 1` is two labels, and both belong to the memo language.
        self.assertEqual("ст. 998 п. 1", oscola.display_pinpoint("art 998 para 1", "ru"))
        self.assertEqual("ст. 170 п. 2", oscola.display_pinpoint("s 170 para 2", "ru"))
        self.assertEqual("art 998 para 1", oscola.display_pinpoint("art 998 para 1", "en"))
        self.assertEqual("s 170 para 2", oscola.display_pinpoint("s 170 para 2", "en"))

    def test_a_label_inside_guillemets_is_left_as_the_heading_wrote_it(self):
        self.assertEqual(
            "ст. 5 «art 3 of the offer»",
            oscola.display_pinpoint("s 5 «art 3 of the offer»", "ru"),
        )

    def test_an_internal_label_with_a_capital_or_a_dot_is_translated(self):
        """D-206: the display pattern carries the `\\.?` and IGNORECASE of `_PINPOINT_RE`."""
        self.assertEqual("ст. 998 п. 1", oscola.display_pinpoint("art 998 Para. 1", "ru"))
        self.assertEqual("art 998 para 1", oscola.display_pinpoint("Art. 998 para 1", "en"))
        self.assertEqual("ст. 998 п. 1", oscola.display_pinpoint("Art. 998 Para. 1", "ru"))

    def test_a_single_letter_label_and_a_non_label_are_not_matched(self):
        self.assertEqual("art 6 s 2", oscola.display_pinpoint("art 6 s 2", "en"))
        self.assertEqual("ст. 6 ст. 2", oscola.display_pinpoint("art 6 s 2", "ru"))
        self.assertEqual("art analysis", oscola.display_pinpoint("art analysis", "en"))
        self.assertEqual("art maps 2", oscola.display_pinpoint("art maps 2", "en"))

    def test_a_quoted_capitalised_label_and_a_cyrillic_pinpoint_stay_as_written(self):
        self.assertEqual(
            "ст. 5 «Para. 1»", oscola.display_pinpoint("s 5 «Para. 1»", "ru")
        )
        self.assertEqual(
            "п. 3 разд. «Возмещение»",
            oscola.display_pinpoint("п. 3 разд. «Возмещение»", "en"),
        )

    def test_a_word_is_not_taken_for_a_roman_numeral_on_display_either(self):
        self.assertTrue(
            oscola.display_pinpoint("art Insurance para 1.7", "ru").startswith("art Insurance")
        )
        self.assertEqual("ст. IV(2)", oscola.display_pinpoint("art IV(2)", "ru"))

    def test_the_identity_of_a_citation_stays_english(self):
        rendered = oscola.compact(view(CJEU), "para 2", language="ru")
        self.assertIn("Case C-311/18", rendered)
        self.assertIn("п. 2", rendered)
        self.assertEqual(
            "Schrems II (n 19)", oscola.short(view(CJEU), "", 19, language="ru")
        )


class CyrillicPinpointTest(unittest.TestCase):
    """D-186: a Cyrillic pinpoint is printed as written in every memo language."""

    def test_a_cyrillic_pinpoint_is_not_normalised_away(self):
        self.assertEqual("п. 2 ст. 152", oscola.normalise_pinpoint("п.  2 ст. 152"))

    def test_a_cyrillic_pinpoint_is_displayed_as_written(self):
        for language in ("ru", "en", "de"):
            with self.subTest(language=language):
                self.assertEqual("п. 2 ст. 152", oscola.display_pinpoint("п. 2 ст. 152", language))

    def test_a_capitalised_cyrillic_pinpoint_is_printed_as_written(self):
        self.assertEqual("Ст. 152", oscola.normalise_pinpoint("Ст.  152"))
        self.assertEqual("П. 2 ст. 152", oscola.display_pinpoint("П. 2 ст. 152", "en"))

    def test_a_russian_statute_renders_with_the_cyrillic_text_intact(self):
        statute = {
            "layer": "statutes",
            "title": "Гражданский кодекс РФ (часть первая)",
            "citation_form": "Гражданский кодекс РФ (часть первая), ст. 152",
        }
        self.assertEqual(
            "Гражданский кодекс РФ (часть первая), п. 2 ст. 152",
            oscola.compact(view(statute), "п. 2 ст. 152"),
        )

    def test_a_russian_decision_renders_with_the_cyrillic_text_intact(self):
        decision = {
            "layer": "case_law",
            "title": "Определение СКЭС ВС РФ от 12.03.2024 № 305-ЭС23-12345",
            "citation_form": "Определение СКЭС ВС РФ от 12.03.2024 № 305-ЭС23-12345 по делу № А40-1",
            "meta": {"court": "СКЭС ВС РФ", "year": "2024"},
        }
        self.assertEqual(
            "Определение СКЭС ВС РФ от 12.03.2024 № 305-ЭС23-12345 по делу № А40-1, п. 2",
            oscola.compact(view(decision), "п. 2"),
        )

    def test_a_contract_pinpoint_keeps_its_own_labels_and_heading(self):
        # D-195: `разд`, `гл` and `прил` are pinpoint labels of a contract or an offer, and a
        # heading in guillemets may stand where a number would.
        for pinpoint in (
            "п. 3 разд. «Возмещение»",
            "п. 5.1 разд. «FBO»",
            "разд. «Возмещение»",
            "гл. 4",
            "прил. 2",
            "раздел 7",
        ):
            with self.subTest(pinpoint=pinpoint):
                self.assertEqual(pinpoint, oscola.normalise_pinpoint(pinpoint))
                for language in ("ru", "en", "de"):
                    self.assertEqual(pinpoint, oscola.display_pinpoint(pinpoint, language))


class CitationFormFixesTest(unittest.TestCase):
    """D-219: the three local citation-form fixes (map rows F1, F11, F5) on the real records of runs 71 and 74.

    `citation-records-71-74.json` holds each registry record verbatim with its `source_id`; the view is built
    the way the renderer builds it, through `oscola.view_of`.
    """

    @classmethod
    def setUpClass(cls):
        path = PLUGIN_ROOT / "scripts" / "tests" / "fixtures" / "source_text" / "citation-records-71-74.json"
        cls.records = json.loads(path.read_text(encoding="utf-8"))

    def real(self, key: str) -> dict:
        entry = self.records[key]
        return oscola.view_of(entry["source_id"], None, entry["record"])

    def test_f1_the_cut_name_carries_no_trailing_comma(self):
        """Run 71, А40-630/2025 with no `meta.short_name`: printed nine times as `АС города Москвы,,`."""
        self.assertEqual("АС города Москвы", oscola._cut("АС города Москвы, решение", 3))
        decision = self.real("f1-a40-630")
        self.assertEqual("АС города Москвы", oscola.short_name(decision))
        self.assertEqual(
            "АС города Москвы, разд. «мотивировочная часть»",
            oscola.short(decision, "разд. «мотивировочная часть»"),
        )

    def test_f11_the_fallback_name_stops_at_a_spaced_dash(self):
        """Run 74: article-level records with no `meta.short_name`, printed as `UK GDPR Article 82 -`."""
        article = self.real("f11-uk-gdpr-article-82-right-to-compensation-and-liability")
        self.assertEqual("UK GDPR Article 82", oscola.short_name(article))
        self.assertEqual("UK GDPR Article 82, art 82(1)-(2)", oscola.short(article, "art 82(1)-(2)"))
        regulation = self.real("f11-pecr-2003-regulation-31-enforcement-provisions-applied")
        self.assertEqual("PECR 2003 regulation 31", oscola.short_name(regulation))
        # The dash of s.168 stood sixth, past the five-word cut: the name is the one of today.
        section = self.real("f11-data-protection-act-2018-s-168-compensation-for-contravention-of-gdpr")
        self.assertEqual("Data Protection Act 2018 s.168", oscola.short_name(section))
        french = {"layer": "statutes", "title": "Code civil Article 1240 – Responsabilité extracontractuelle"}
        self.assertEqual("Code civil Article 1240", oscola.short_name(view(french)))

    def test_f11_an_unspaced_dash_and_a_designation_are_left_alone(self):
        directive = {"layer": "statutes", "title": "Directive 95/46/EC"}
        self.assertEqual("Directive 95/46/EC", oscola.short_name(view(directive)))
        law = {"layer": "statutes", "title": "Law No. 2016-1321 of 7 October 2016 for a Digital Republic"}
        self.assertEqual("Law No. 2016-1321 of 7", oscola.short_name(view(law)))

    def test_f5_the_name_is_cut_and_the_pinpoint_is_printed_whole(self):
        """Run 71, the GARANT encyclopedia: `_hard_cut` ate the section pinpoint."""
        pinpoint = self.records["f5-garant-encyclopedia"]["pinpoint"]
        rendered = oscola.compact(self.real("f5-garant-encyclopedia"), pinpoint)
        tail = ", " + pinpoint
        self.assertTrue(rendered.endswith(tail), rendered)
        self.assertLessEqual(len(rendered), oscola.MAX_COMPACT_CHARS)
        name = rendered[: -len(tail)]
        self.assertTrue(name.startswith("ГАРАНТ Энциклопедия"), name)
        self.assertTrue(name.endswith("…"), name)

    def test_f5_a_long_pinpoint_leaves_the_name_only_the_room_that_is_left(self):
        record = self.real("f5-garant-encyclopedia")
        pinpoint = self.records["f5-garant-encyclopedia"]["pinpoint"][:-1] + " и порядок их доказывания»"
        tail = ", " + pinpoint
        self.assertLess(oscola.MAX_COMPACT_CHARS - len(tail), 40)
        rendered = oscola.compact(record, pinpoint)
        self.assertTrue(rendered.endswith(tail), rendered)
        self.assertLessEqual(len(rendered), oscola.MAX_COMPACT_CHARS)

    def test_f5_a_pinpoint_that_leaves_no_room_for_a_name_keeps_the_cut_of_today(self):
        record = self.real("f5-garant-encyclopedia")
        pinpoint = "разд. «" + "Обстоятельства непреодолимой силы " * 4 + "»"
        self.assertGreaterEqual(len(", " + pinpoint), oscola.MAX_COMPACT_CHARS)
        self.assertEqual(oscola._hard_cut(oscola.short(record, pinpoint)), oscola.compact(record, pinpoint))

    def pinpoint_of_tail(self, length: int) -> str:
        """A section pinpoint whose `, <pinpoint>` tail is exactly `length` characters."""
        pinpoint = "разд. «" + "Обстоятельства непреодолимой силы " * 4
        pinpoint = pinpoint[: length - 3].rstrip() + "»"
        pinpoint = pinpoint[:-1] + "ы" * (length - 2 - len(pinpoint)) + "»"
        self.assertEqual(len(", " + pinpoint), length)
        return pinpoint

    def test_f5_a_pinpoint_that_leaves_exactly_no_room_is_printed_whole(self):
        record = self.real("f5-garant-encyclopedia")
        pinpoint = self.pinpoint_of_tail(oscola.MAX_COMPACT_CHARS)
        rendered = oscola.compact(record, pinpoint)
        self.assertEqual(pinpoint, rendered)
        self.assertLessEqual(len(rendered), oscola.MAX_COMPACT_CHARS)

    def test_f5_one_character_past_the_room_keeps_the_cut_of_today(self):
        record = self.real("f5-garant-encyclopedia")
        pinpoint = self.pinpoint_of_tail(oscola.MAX_COMPACT_CHARS + 1)
        self.assertEqual(oscola._hard_cut(oscola.short(record, pinpoint)), oscola.compact(record, pinpoint))


class ProvenanceLivenessTest(unittest.TestCase):
    """D-197: `checked <date>` is printed only when the link check actually succeeded."""

    def record(self, status: str | None) -> dict:
        record = dict(EU_LEGISLATION)
        record.pop("liveness", None)
        if status is not None:
            record["liveness"] = {"status": status, "code": None, "checked_at": None}
        return record

    def test_a_successful_check_keeps_the_date(self):
        for status in oscola.LIVENESS_SUCCEEDED:
            with self.subTest(status=status):
                self.assertIn("checked 2026-09-10", oscola.provenance_field([view(self.record(status))]))

    def test_no_date_without_a_successful_check(self):
        # D-197 (fix round 1): membership in `LIVENESS_SUCCEEDED` is what prints the date. A record
        # with no `liveness` at all was not checked either, and printed «checked <date>» regardless.
        for status in (None, "dead", "changed", "unchecked"):
            with self.subTest(status=status):
                rendered = oscola.provenance_field([view(self.record(status))])
                self.assertNotIn("2026-09-10", rendered)
                self.assertEqual("currency unchecked", rendered)


if __name__ == "__main__":
    unittest.main()
