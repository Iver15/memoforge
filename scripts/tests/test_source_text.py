"""Tests for scripts/memoforge/source_text.py — where a document starts, its requisites, its wholeness (D-203).

Three fixtures under `fixtures/source_text/` are real pages saved through `mf sources fetch` and copied byte
for byte, artifacts and all: `sudact-cassation-a53-28950-2022` (sudact.ru, cassation ruling А53-28950/2022),
`vsrf-pdf-layer-305-es24-8702` (the `pypdf` text layer of the Supreme Court PDF `stor_pdf_ec/2383828`) and
`consultant-gk-article-152` (the consultant.ru page of ст. 152 ГК РФ); they carry only what those published
acts already made public. The other 18 are models written to the forms design §8 names, for shapes no
specimen was fetched for, and their parties, judges and addresses are invented. D-219 adds five `uk-*.md` byte
copies of run-74 raw texts (legislation.gov.uk and Find Case Law) and `de-gii-bgb-823.txt`, the gesetze-im-internet
page of § 823 BGB after the repo's own `markup_to_text`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import source_text, sources  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "source_text"


def fixture(name: str) -> str:
    """The saved text of one form of document."""
    return (FIXTURES / (name + ".txt")).read_text(encoding="utf-8")


class FlattenTest(unittest.TestCase):
    """`У С Т А Н О В И Л` and `2 7 о к т я б р я 2 0 2 5 года` have to read as the words they are."""

    def test_letter_spacing_collapses_into_the_word(self):
        self.assertEqual(source_text.flatten("У С Т А Н О В И Л А :"), "установила:")
        self.assertEqual(source_text.flatten("   О П Р Е Д Е Л Е Н И Е   "), "определение")

    def test_a_spaced_date_keeps_its_words_apart(self):
        self.assertEqual(source_text.flatten("2 7  о к т я б р я  2 0 2 5 года"), "27 октября 2025 года")

    def test_an_ordinary_line_only_loses_case_yo_and_dash_shape(self):
        self.assertEqual(source_text.flatten("Дело № 305–ЭС24—8702"), "дело № 305-эс24-8702")
        self.assertEqual(source_text.flatten("Учёт   и    отчётность"), "учет и отчетность")

    def test_a_lone_single_character_token_is_not_glued_to_its_neighbours(self):
        self.assertEqual(source_text.flatten("п. 1 ст. 2"), "п. 1 ст. 2")

    def test_punctuation_glued_to_the_last_letter_does_not_break_the_spacing(self):
        self.assertEqual(source_text.flatten("о п р е д е л и л:"), "определил:")
        self.assertEqual(source_text.flatten("у с т а н о в и л:"), "установил:")


class DocumentStartTest(unittest.TestCase):
    """The document begins below the portal menu and below the portal's own heading (D-203)."""

    def test_the_portal_menu_and_heading_are_cut_off_a_sudact_page(self):
        text = fixture("sudact-commercial-decision")
        start = source_text.document_start(text)
        self.assertEqual(start, text.index("РЕШЕНИЕ\n"))
        self.assertNotIn("Главная »", text[start:])
        self.assertNotIn("от 5 мая 2025 г. по делу", text[start:])

    def test_a_heading_with_a_tail_still_opens_the_document(self):
        text = fixture("cassation-ruling")
        self.assertEqual(
            source_text.document_start(text),
            text.index("ПОСТАНОВЛЕНИЕ арбитражного суда кассационной инстанции"),
        )

    def test_a_portal_heading_carrying_a_date_or_a_case_is_not_the_document(self):
        text = fixture("plenum-ruling")
        self.assertEqual(source_text.document_start(text), text.index("ПОСТАНОВЛЕНИЕ ПЛЕНУМА"))

    def test_imenem_rossiyskoy_federacii_opens_a_document_with_no_act_type_heading(self):
        text = "Портал судебных актов\nГлавная » Дела\n\nИменем Российской Федерации\n\nСуд решил\n"
        self.assertEqual(source_text.document_start(text), text.index("Именем"))

    def test_a_text_that_is_not_an_act_starts_at_zero(self):
        self.assertEqual(source_text.document_start(fixture("digest-opredelil-midline")), 0)

    def test_a_qualified_heading_of_a_second_instance_act_is_a_heading(self):
        head = "Портал судебных актов\n"
        for heading in (
            "АПЕЛЛЯЦИОННОЕ ОПРЕДЕЛЕНИЕ",
            "КАССАЦИОННОЕ ПОСТАНОВЛЕНИЕ",
            "НАДЗОРНОЕ ОПРЕДЕЛЕНИЕ",
            "ЧАСТНОЕ ОПРЕДЕЛЕНИЕ",
            "ДОПОЛНИТЕЛЬНОЕ РЕШЕНИЕ",
            "А П Е Л Л Я Ц И О Н Н О Е  О П Р Е Д Е Л Е Н И Е",
        ):
            with self.subTest(heading=heading):
                self.assertEqual(source_text.document_start(head + heading + "\nтело\n"), len(head))

    def test_the_qualifier_list_is_closed_and_the_rest_of_the_rule_stands(self):
        head = "Портал судебных актов\n"
        for line in (
            "Апелляционное определение от 26 октября 2025 г. по делу № А53-28950/2022",
            "Кассационное постановление по делу № А53-28950/2022",
            "ОСОБОЕ МНЕНИЕ",
            "ПРЕДВАРИТЕЛЬНОЕ РЕШЕНИЕ",
        ):
            with self.subTest(line=line):
                self.assertEqual(source_text.document_start(head + line + "\nтело\n"), 0)


class RussianActTest(unittest.TestCase):
    """Whether the text is a Russian judicial act is read off the text, never off a flag (D-203)."""

    def test_a_cyrillic_act_heading_makes_it_russian(self):
        self.assertTrue(source_text.is_russian_act(fixture("vs-html-act")))
        self.assertTrue(source_text.is_russian_act(fixture("plenum-ruling")))

    def test_an_english_judgment_is_not_a_russian_act(self):
        self.assertFalse(source_text.is_russian_act(fixture("non-russian-judgment")))


class ZoneTest(unittest.TestCase):
    """The number zone reaches back over the court name and the case number; the date zone never does."""

    def test_the_number_zone_reaches_back_over_the_lines_above_the_heading(self):
        text = fixture("number-above-heading-only")
        start, end = source_text.number_zone(text)
        self.assertTrue(source_text.has_number(text, "2-987/2024", (start, end)))
        self.assertFalse(source_text.has_number(text, "2-987/2024", (source_text.document_start(text), end)))

    def test_the_backward_walk_stops_at_the_portal_heading(self):
        text = fixture("sudact-commercial-decision")
        start, _ = source_text.number_zone(text)
        self.assertGreater(start, text.index("Решение № А40-12345/2025 от 5 мая"))
        self.assertLessEqual(start, text.index("АРБИТРАЖНЫЙ СУД ГОРОДА МОСКВЫ"))

    def test_the_backward_walk_stops_at_a_breadcrumb(self):
        text = fixture("vs-html-act")
        start, _ = source_text.number_zone(text)
        self.assertGreater(start, text.index("Главная / Судебные акты"))
        self.assertLessEqual(start, text.index("ВЕРХОВНЫЙ СУД РОССИЙСКОЙ ФЕДЕРАЦИИ"))
        self.assertTrue(source_text.has_number(text, "5-КГ25-14-К2", source_text.number_zone(text)))

    def test_the_backward_walk_is_bounded_by_lines_and_characters(self):
        head = "\n".join(["Дело № 2-1/2025"] + ["строка %d" % n for n in range(source_text.PRE_HEADING_LINES + 2)])
        text = head + "\nРЕШЕНИЕ\nг. Тест 1 марта 2025 года\nУСТАНОВИЛ:\nтекст\n"
        self.assertFalse(source_text.has_number(text, "2-1/2025", source_text.number_zone(text)))

    def test_the_number_zone_ends_at_ustanovil_and_the_date_zone_at_rassmotrev(self):
        text = fixture("first-instance-number-after-rassmotrev")
        number_start, number_end = source_text.number_zone(text)
        date_start, date_end = source_text.date_zone(text)
        self.assertEqual(number_end, text.index("УСТАНОВИЛ:"))
        self.assertEqual(date_end, text.index("рассмотрев"))
        self.assertTrue(source_text.has_number(text, "2-1456/2025", (number_start, number_end)))
        self.assertFalse(source_text.has_number(text, "2-1456/2025", (date_start, date_end)))

    def test_without_an_anchor_word_both_zones_run_to_the_header_limit(self):
        text = fixture("plenum-ruling")
        for anchor in ("установил", "рассмотрев", "рассмотрела", "изучив"):
            self.assertNotIn(anchor, text.lower())
        start = source_text.document_start(text)
        self.assertEqual(source_text.number_zone(text)[1], start + source_text.HEADER_ZONE_CHARS)
        self.assertEqual(source_text.date_zone(text)[1], start + source_text.HEADER_ZONE_CHARS)

    def test_a_referral_order_closes_its_date_zone_at_izuchiv_and_its_number_zone_at_ustanovil(self):
        text = fixture("referral-order-izuchiv")
        self.assertNotIn("рассмотрев", text)
        self.assertEqual(source_text.number_zone(text)[1], text.index("установил:"))
        self.assertEqual(source_text.date_zone(text)[1], text.index("изучив"))

    def test_the_date_zone_begins_at_the_document_and_never_earlier(self):
        text = fixture("sudact-commercial-decision")
        self.assertEqual(source_text.date_zone(text)[0], source_text.document_start(text))


class HasNumberTest(unittest.TestCase):
    """A number is a whole token; a base number and a bracketed twin are different documents."""

    def test_the_number_printed_above_the_heading_is_found(self):
        text = fixture("vs-chamber-pdf-layer")
        self.assertTrue(source_text.has_number(text, "305-ЭС24-8702", source_text.number_zone(text)))

    def test_spacing_dashes_and_the_number_sign_are_forgiven(self):
        text = fixture("vs-chamber-pdf-layer")
        zone = source_text.number_zone(text)
        self.assertTrue(source_text.has_number(text, "305 - ЭС24 - 8702", zone))
        self.assertTrue(source_text.has_number(text, "№ 305–ЭС24—8702", zone))
        self.assertTrue(source_text.has_number(text, "N 305-эс24-8702", zone))
        self.assertTrue(source_text.has_number(text, "No 305-ЭС24-8702", zone))

    def test_twins_do_not_match_each_other_nor_the_base_number(self):
        twin = fixture("vs-twin-ruling")
        base = fixture("vs-chamber-pdf-layer")
        self.assertTrue(source_text.has_number(twin, "305-ЭС24-8702 (1,3)", source_text.number_zone(twin)))
        self.assertFalse(source_text.has_number(twin, "305-ЭС24-8702 (2,4)", source_text.number_zone(twin)))
        self.assertFalse(source_text.has_number(twin, "305-ЭС24-8702", source_text.number_zone(twin)))
        self.assertFalse(source_text.has_number(base, "305-ЭС24-8702 (1,3)", source_text.number_zone(base)))

    def test_a_longer_number_is_not_matched_by_its_prefix(self):
        text = fixture("vs-html-act")
        self.assertFalse(source_text.has_number(text, "5-КГ25-14", source_text.number_zone(text)))

    def test_an_appeal_does_not_pass_on_the_number_of_the_act_it_reviews(self):
        text = fixture("appeal-quoting-first-instance")
        zone = source_text.number_zone(text)
        self.assertTrue(source_text.has_number(text, "33-4567/2025", zone))
        self.assertFalse(source_text.has_number(text, "2-1456/2025", zone))
        self.assertTrue(source_text.has_number(text, "2-1456/2025", (0, len(text))))

    def test_a_neutral_citation_is_a_number_too(self):
        text = fixture("non-russian-judgment")
        self.assertTrue(source_text.has_number(text, "[2026] EWHC 9001 (Comm)", (0, source_text.HEADER_ZONE_CHARS)))
        self.assertFalse(source_text.has_number(text, "[2026] EWHC 9002 (Comm)", (0, source_text.HEADER_ZONE_CHARS)))


class HasDateTest(unittest.TestCase):
    """The portal's heading is a day early; the act's own date is the one that counts."""

    def test_the_portal_date_does_not_satisfy_the_document_date(self):
        text = fixture("sudact-commercial-decision")
        zone = source_text.date_zone(text)
        self.assertTrue(source_text.has_date(text, "2025-05-06", zone))
        self.assertFalse(source_text.has_date(text, "2025-05-05", zone))
        self.assertTrue(source_text.has_date(text, "2025-05-05", (0, len(text))))

    def test_both_dates_of_a_header_count(self):
        text = fixture("cassation-ruling")
        zone = source_text.date_zone(text)
        self.assertTrue(source_text.has_date(text, "2025-03-14", zone))
        self.assertTrue(source_text.has_date(text, "2025-03-12", zone))

    def test_the_expected_date_may_be_written_either_way(self):
        text = fixture("cassation-ruling")
        zone = source_text.date_zone(text)
        self.assertTrue(source_text.has_date(text, "14.03.2025", zone))
        self.assertFalse(source_text.has_date(text, "13.03.2025", zone))

    def test_a_spaced_date_in_a_pdf_layer_is_read(self):
        text = fixture("vs-chamber-pdf-layer").replace("27 октября 2025 г.", "2 7  о к т я б р я  2 0 2 5 г.")
        self.assertTrue(source_text.has_date(text, "2025-10-27", source_text.date_zone(text)))

    def test_digits_are_read_in_any_language_but_words_only_in_russian(self):
        text = fixture("non-russian-judgment")
        self.assertFalse(source_text.has_date(text, "2026-02-12", (0, source_text.HEADER_ZONE_CHARS)))
        iso = text.replace("Date: 12 February 2026", "Date: 2026-02-12")
        self.assertTrue(source_text.has_date(iso, "2026-02-12", (0, source_text.HEADER_ZONE_CHARS)))

    def test_an_appeal_does_not_pass_on_the_date_of_the_act_it_reviews(self):
        text = fixture("appeal-quoting-first-instance")
        zone = source_text.date_zone(text)
        self.assertTrue(source_text.has_date(text, "2025-07-09", zone))
        self.assertFalse(source_text.has_date(text, "2025-02-18", zone))
        self.assertTrue(source_text.has_date(text, "2025-02-18", (0, len(text))))

    def test_a_referral_order_does_not_pass_on_a_date_recited_in_its_body(self):
        text = fixture("referral-order-izuchiv")
        zone = source_text.date_zone(text)
        self.assertTrue(source_text.has_date(text, "2025-09-19", zone))
        self.assertFalse(source_text.has_date(text, "2025-07-14", zone))
        self.assertTrue(source_text.has_date(text, "2025-07-14", (0, len(text))))

    def test_a_date_on_a_list_line_is_not_used(self):
        text = "РЕШЕНИЕ\n\nг. Тест\n\n- 4 марта 2025 года\n\nтекст решения\n"
        self.assertFalse(source_text.has_date(text, "2025-03-04", source_text.date_zone(text)))


class CompletenessTest(unittest.TestCase):
    """Only a positive sign promotes a text; anything undecided stays an excerpt (D-203)."""

    def test_an_operative_marker_after_ustanovil_makes_an_act_whole(self):
        self.assertTrue(source_text.is_complete_ru_act(fixture("vs-chamber-pdf-layer")))
        self.assertTrue(source_text.is_complete_ru_act(fixture("vs-html-act")))
        self.assertTrue(source_text.is_complete_ru_act(fixture("cassation-ruling")))

    def test_a_long_portal_tail_after_a_short_act_does_not_hide_the_marker(self):
        text = fixture("short-act-long-portal-tail")
        self.assertGreater(len(text) - text.index("РЕШИЛ:"), len(text) // 2)
        self.assertTrue(source_text.is_complete_ru_act(text))

    def test_an_operative_marker_before_ustanovil_does_not_count(self):
        text = "РЕШЕНИЕ\n\nРЕШИЛ:\n\nвзыскать\n\nУСТАНОВИЛ:\n\nобстоятельства\n"
        self.assertFalse(source_text.is_complete_ru_act(text))

    def test_the_verb_in_the_middle_of_a_line_is_not_an_operative_marker(self):
        self.assertFalse(source_text.is_complete_ru_act(fixture("digest-opredelil-midline")))
        self.assertFalse(source_text.is_complete_ru_act(fixture("commentary-sud-postanovil")))

    def test_an_act_with_only_an_operative_part_is_not_whole(self):
        self.assertFalse(source_text.is_complete_ru_act(fixture("operative-only-decision")))

    def test_a_plenum_ruling_is_whole_on_postanovlyaet_and_a_signature(self):
        text = fixture("plenum-ruling")
        self.assertFalse(source_text.is_complete_ru_act(text))
        self.assertTrue(source_text.is_complete_plenum(text))

    def test_a_plenum_signature_wrapped_over_two_lines_is_still_a_signature(self):
        whole = fixture("plenum-ruling")
        only_chairman = whole[: whole.index("Секретарь Пленума")]
        wrapped = only_chairman.replace("Председатель Верховного Суда", "Председатель\nВерховного Суда")
        self.assertNotIn("Секретарь Пленума", wrapped)
        self.assertNotIn("Председатель Верховного Суда", wrapped)
        self.assertTrue(source_text.is_complete_plenum(only_chairman))
        self.assertTrue(source_text.is_complete_plenum(wrapped))

    def test_a_plenum_ruling_without_its_signature_is_not_whole(self):
        text = fixture("plenum-ruling")
        cut = text[: text.index("Председатель Верховного Суда")]
        self.assertFalse(source_text.is_complete_plenum(cut))

    def test_a_presidium_ruling_passes_the_ordinary_act_predicate(self):
        text = fixture("presidium-sac-7074-06")
        self.assertTrue(source_text.is_complete_ru_act(text))


class ArticleBodyTest(unittest.TestCase):
    """The article heading grammar is the one `sources.py` already uses; a table of contents has no body."""

    def test_the_heading_alternation_is_not_forked(self):
        self.assertEqual(source_text.ARTICLE_HEADING_PREFIX, sources.ARTICLE_HEADING_PREFIX)
        self.assertEqual(source_text.ANY_ARTICLE_HEADING_RE.pattern, sources.ANY_ARTICLE_HEADING_RE.pattern)
        self.assertEqual(source_text.ANY_ARTICLE_HEADING_RE.flags, sources.ANY_ARTICLE_HEADING_RE.flags)

    def test_the_body_runs_to_the_next_article_heading(self):
        text = fixture("statute-article-152")
        expected = text.index("Статья 152.1") - text.index("Статья 152. Защита")
        self.assertEqual(source_text.article_body_chars(text, "152"), expected)
        self.assertGreaterEqual(source_text.article_body_chars(text, "152.1"), source_text.ARTICLE_BODY_MIN_CHARS)

    def test_a_bare_number_never_matches_a_dotted_article(self):
        text = "Статья 152.1. Охрана изображения гражданина\n\nОбнародование изображения допускается.\n"
        self.assertEqual(source_text.article_body_chars(text, "152"), 0)

    def test_a_missing_article_has_no_body(self):
        self.assertEqual(source_text.article_body_chars(fixture("statute-article-152"), "155"), 0)

    def test_a_table_of_contents_gives_a_body_under_the_minimum(self):
        body = source_text.article_body_chars(fixture("statute-table-of-contents"), "36")
        self.assertGreater(body, 0)
        self.assertLess(body, source_text.ARTICLE_BODY_MIN_CHARS)


class VerdictRussianCaseLawTest(unittest.TestCase):
    """A Russian judicial act needs both requisites; a mismatch is the caller's refusal, not an excerpt."""

    def test_both_requisites_found_and_the_act_whole_gives_full_text(self):
        answer = source_text.verdict(
            fixture("vs-chamber-pdf-layer"), layer="case_law", expect_number="305-ЭС24-8702", expect_date="2025-10-27"
        )
        self.assertEqual(answer["raw_kind"], "full_text")
        self.assertEqual(answer["outcome"], "full_text")
        self.assertIsNone(answer["error"])
        self.assertTrue(answer["found"]["number"])
        self.assertTrue(answer["found"]["date"])
        self.assertTrue(answer["found"]["russian"])
        self.assertEqual(answer["found"]["chars"], len(fixture("vs-chamber-pdf-layer")))

    def test_a_wrong_date_is_a_requisites_mismatch(self):
        answer = source_text.verdict(
            fixture("vs-chamber-pdf-layer"), layer="case_law", expect_number="305-ЭС24-8702", expect_date="2025-10-26"
        )
        self.assertEqual(answer["error"], "requisites_mismatch")
        self.assertEqual(answer["raw_kind"], "excerpt")
        self.assertFalse(answer["found"]["date"])

    def test_a_twin_number_is_a_requisites_mismatch(self):
        answer = source_text.verdict(
            fixture("vs-chamber-pdf-layer"),
            layer="case_law",
            expect_number="305-ЭС24-8702 (1,3)",
            expect_date="2025-10-27",
        )
        self.assertEqual(answer["error"], "requisites_mismatch")
        self.assertFalse(answer["found"]["number"])

    def test_a_missing_expectation_never_refuses_and_never_promotes(self):
        for kwargs in ({"expect_number": "305-ЭС24-8702"}, {"expect_date": "2025-10-27"}, {}):
            answer = source_text.verdict(fixture("vs-chamber-pdf-layer"), layer="case_law", **kwargs)
            self.assertEqual(answer["outcome"], "excerpt:identity_unverified")
            self.assertIsNone(answer["error"])

    def test_a_plenum_ruling_reaches_full_text(self):
        answer = source_text.verdict(
            fixture("plenum-ruling"), layer="case_law", expect_number="25", expect_date="2015-06-23"
        )
        self.assertEqual(answer["outcome"], "full_text")

    def test_a_presidium_ruling_reaches_full_text(self):
        answer = source_text.verdict(
            fixture("presidium-sac-7074-06"), layer="case_law", expect_number="7074/06", expect_date="2006-11-21"
        )
        self.assertEqual(answer["outcome"], "full_text")

    def test_an_act_with_no_reasoning_is_named_as_such(self):
        answer = source_text.verdict(
            fixture("operative-only-decision"), layer="case_law", expect_number="А40-777/2025", expect_date="2025-06-03"
        )
        self.assertEqual(answer["outcome"], "excerpt:no_reasoning")
        self.assertIsNone(answer["error"])
        self.assertTrue(answer["found"]["number"])
        self.assertTrue(answer["found"]["date"])

    def test_a_long_act_whose_wholeness_is_undecided_stays_an_excerpt(self):
        text = fixture("referral-order-izuchiv").replace("определил:", "определил далее:")
        answer = source_text.verdict(
            text, layer="case_law", expect_number="305-ЭС24-8702", expect_date="2025-09-19"
        )
        self.assertEqual(answer["outcome"], "excerpt:not_verified")
        self.assertIsNone(answer["error"])


class VerdictOtherLayersTest(unittest.TestCase):
    """Outside the Russian rule a failed identity is an excerpt, never a refusal (D-203)."""

    def test_a_non_russian_judgment_with_its_neutral_citation_reaches_full_text(self):
        answer = source_text.verdict(
            fixture("non-russian-judgment"), layer="case_law", expect_number="[2026] EWHC 9001 (Comm)"
        )
        self.assertEqual(answer["outcome"], "full_text")
        self.assertIsNone(answer["error"])
        self.assertFalse(answer["found"]["russian"])

    def test_the_same_judgment_without_a_number_is_unverified_and_not_a_refusal(self):
        answer = source_text.verdict(fixture("non-russian-judgment"), layer="case_law")
        self.assertEqual(answer["outcome"], "excerpt:identity_unverified")
        self.assertIsNone(answer["error"])

    def test_a_wrong_number_outside_the_russian_rule_is_not_a_refusal(self):
        answer = source_text.verdict(
            fixture("non-russian-judgment"), layer="case_law", expect_number="[2026] EWHC 9002 (Comm)"
        )
        self.assertEqual(answer["outcome"], "excerpt:identity_unverified")
        self.assertIsNone(answer["error"])

    def test_the_date_is_never_checked_outside_the_russian_rule(self):
        answer = source_text.verdict(
            fixture("non-russian-judgment"),
            layer="case_law",
            expect_number="[2026] EWHC 9001 (Comm)",
            expect_date="1999-01-01",
        )
        self.assertEqual(answer["outcome"], "full_text")

    def test_a_short_foreign_act_is_too_short(self):
        answer = source_text.verdict("Judgment of the court. Appeal dismissed.", layer="case_law")
        self.assertEqual(answer["outcome"], "excerpt:too_short")

    def test_statutes_need_the_article_and_a_body(self):
        text = fixture("statute-article-152")
        self.assertEqual(source_text.verdict(text, layer="statutes", expect_article="152")["outcome"], "full_text")
        self.assertEqual(
            source_text.verdict(text, layer="statutes", expect_article="155")["outcome"], "excerpt:article_not_found"
        )
        self.assertEqual(
            source_text.verdict(fixture("statute-table-of-contents"), layer="statutes", expect_article="36")["outcome"],
            "excerpt:too_short",
        )

    def test_statutes_without_an_article_never_reach_full_text(self):
        answer = source_text.verdict(fixture("statute-article-152"), layer="statutes")
        self.assertEqual(answer["outcome"], "excerpt:identity_unverified")
        self.assertEqual(answer["raw_kind"], "excerpt")
        self.assertIsNone(answer["error"])

    def test_doctrine_is_decided_by_length_alone(self):
        long_enough = source_text.verdict(fixture("non-russian-judgment"), layer="doctrine")
        self.assertEqual(long_enough["outcome"], "full_text")
        short = source_text.verdict(fixture("commentary-sud-postanovil"), layer="doctrine")
        self.assertEqual(short["outcome"], "excerpt:too_short")


NUMBERED_FIXTURES = (
    ("vs-chamber-pdf-layer", "305-ЭС24-8702", "305-ЭС24-8703"),
    ("sudact-commercial-decision", "А40-12345/2025", "А40-12346/2025"),
    ("cassation-ruling", "А40-12345/2024", "А40-12345/2025"),
    ("first-instance-number-after-rassmotrev", "2-1456/2025", "2-1457/2025"),
    ("plenum-ruling", "25", "27"),
    ("short-act-long-portal-tail", "2-88/2025", "2-89/2025"),
    ("vs-html-act", "5-КГ25-14-К2", "5-КГ25-15-К2"),
    ("number-above-heading-only", "2-987/2024", "2-988/2024"),
    ("referral-order-izuchiv", "305-ЭС24-8702", "305-ЭС24-8703"),
    ("presidium-sac-7074-06", "7074/06", "7075/06"),
    ("operative-only-decision", "А40-777/2025", "А40-778/2025"),
    ("vs-twin-ruling", "305-ЭС24-8702 (1,3)", "305-ЭС24-8702 (2,4)"),
    ("appeal-quoting-first-instance", "33-4567/2025", "33-4568/2025"),
    ("non-russian-judgment", "[2026] EWHC 9001 (Comm)", "[2026] EWHC 9002 (Comm)"),
    ("sudact-cassation-a53-28950-2022", "А53-28950/2022", "А53-28950/2023"),
    ("vsrf-pdf-layer-305-es24-8702", "305-ЭС24-8702 (1,3)", "305-ЭС24-8702 (2,4)"),
)
"""Every fixture that prints a number, with the number it prints and a neighbouring number it does not."""


class NumberedFixtureTest(unittest.TestCase):
    """The brief asks for the number of *each* numbered form; a zone that lost one would refuse valid text."""

    def test_every_numbered_fixture_carries_its_number_in_its_number_zone(self):
        for name, number, neighbour in NUMBERED_FIXTURES:
            with self.subTest(fixture=name):
                text = fixture(name)
                zone = source_text.number_zone(text)
                self.assertTrue(source_text.has_number(text, number, zone), "%s: %s not found" % (name, number))
                self.assertFalse(
                    source_text.has_number(text, neighbour, zone), "%s: %s wrongly found" % (name, neighbour)
                )


class RealSudactPageTest(unittest.TestCase):
    """sudact.ru, cassation ruling of the Commercial Court of the North Caucasus District, А53-28950/2022.

    Saved through `mf sources fetch` and copied byte for byte — portal menu, a portal heading dated a day
    early, the court name, «Именем Российской Федерации», a heading with a tail, and a line that carries the
    town, the case number and the date `2 7 о к т я б р я 2025 года` in letter spacing all at once.
    """

    def setUp(self):
        self.text = fixture("sudact-cassation-a53-28950-2022")

    def test_the_document_starts_below_the_portal(self):
        start = source_text.document_start(self.text)
        self.assertEqual(start, self.text.index("Именем Российской Федерации"))
        self.assertLess(self.text.index("Судебные и нормативные акты РФ"), start)
        self.assertLess(self.text.index("Постановление от 26 октября 2025 г. по делу"), start)
        self.assertNotIn("Суть спора:", self.text[start:])

    def test_the_backward_walk_stops_at_the_portal_heading(self):
        start, _ = source_text.number_zone(self.text)
        self.assertGreater(start, self.text.index("Постановление от 26 октября 2025 г."))

    def test_the_spaced_date_is_the_act_date_and_the_portal_date_is_a_day_early(self):
        zone = source_text.date_zone(self.text)
        self.assertIn("2 7 о к т я б р я 2025 года", self.text)
        self.assertTrue(source_text.has_date(self.text, "2025-10-27", zone))
        self.assertTrue(source_text.has_date(self.text, "2025-10-23", zone))
        self.assertFalse(source_text.has_date(self.text, "2025-10-26", zone))
        self.assertTrue(source_text.has_date(self.text, "2025-10-26", (0, len(self.text))))

    def test_the_page_certifies_as_the_whole_act(self):
        answer = source_text.verdict(
            self.text, layer="case_law", expect_number="А53-28950/2022", expect_date="2025-10-27"
        )
        self.assertEqual(answer["outcome"], "full_text")
        self.assertIsNone(answer["error"])

    def test_the_portal_date_alone_is_a_requisites_mismatch(self):
        answer = source_text.verdict(
            self.text, layer="case_law", expect_number="А53-28950/2022", expect_date="2025-10-26"
        )
        self.assertEqual(answer["error"], "requisites_mismatch")


class RealVsrfPdfLayerTest(unittest.TestCase):
    """The `pypdf` text layer of a Supreme Court referral order, № 305-ЭС24-8702 (1,3).

    Saved through `mf sources fetch` and copied byte for byte — the extractor's own artifact line at the top,
    the court name split over two lines, the number above the heading, `О П Р Е Д Е Л Е Н И Е` and
    `о п р е д е л и л:` in letter spacing, and «изучив» with no «рассмотрев».
    """

    def setUp(self):
        self.text = fixture("vsrf-pdf-layer-305-es24-8702")

    def test_the_document_starts_at_the_spaced_heading(self):
        self.assertEqual(source_text.document_start(self.text), self.text.index("О П Р Е Д Е Л Е Н И Е"))

    def test_the_number_is_reached_only_through_the_walk_back(self):
        start = source_text.document_start(self.text)
        zone = source_text.number_zone(self.text)
        self.assertTrue(source_text.has_number(self.text, "305-ЭС24-8702 (1,3)", zone))
        self.assertFalse(source_text.has_number(self.text, "305-ЭС24-8702 (1,3)", (start, zone[1])))
        self.assertFalse(source_text.has_number(self.text, "305-ЭС24-8702", zone))
        self.assertFalse(source_text.has_number(self.text, "305-ЭС24-8702 (2,4)", zone))

    def test_izuchiv_closes_the_date_zone_where_rassmotrev_would(self):
        zone = source_text.date_zone(self.text)
        self.assertEqual(zone[1], self.text.index("изучив"))
        self.assertTrue(source_text.has_date(self.text, "2024-07-11", zone))
        self.assertFalse(source_text.has_date(self.text, "2023-09-15", zone))
        self.assertTrue(source_text.has_date(self.text, "2023-09-15", (0, len(self.text))))

    def test_the_order_is_not_certified_as_the_act_it_reviews(self):
        reviewed = source_text.verdict(
            self.text, layer="case_law", expect_number="А40-256596/2020", expect_date="2023-09-15"
        )
        self.assertEqual(reviewed["error"], "requisites_mismatch")
        self.assertEqual(reviewed["raw_kind"], "excerpt")
        self.assertTrue(reviewed["found"]["number"])
        self.assertFalse(reviewed["found"]["date"])

    def test_the_spaced_operative_marker_makes_the_order_whole(self):
        self.assertIn("о п р е д е л и л:", self.text)
        self.assertTrue(source_text.is_complete_ru_act(self.text))
        answer = source_text.verdict(
            self.text, layer="case_law", expect_number="305-ЭС24-8702 (1,3)", expect_date="2024-07-11"
        )
        self.assertEqual(answer["outcome"], "full_text")


class RealConsultantArticleTest(unittest.TestCase):
    """The consultant.ru free page of Article 152 of the Civil Code, saved through `mf sources fetch`.

    Portal menu, `Главная / Документы` breadcrumbs, the heading printed twice (once line-anchored as
    `Статья 152. …`, once behind the code name as `ГК РФ Статья 152. …`), and the edition line.
    """

    def setUp(self):
        self.text = fixture("consultant-gk-article-152")

    def test_the_article_heading_and_its_body_are_found(self):
        body = source_text.article_body_chars(self.text, "152")
        self.assertEqual(body, self.text.index("Ст. 152 ГК РФ ч.1.") - self.text.index("Статья 152. Защита"))
        self.assertGreaterEqual(body, source_text.ARTICLE_BODY_MIN_CHARS)

    def test_the_repeated_heading_behind_the_code_name_does_not_cut_the_body(self):
        self.assertIn("ГК РФ Статья 152. Защита", self.text)
        self.assertGreater(
            source_text.article_body_chars(self.text, "152"),
            self.text.index("ГК РФ Статья 152. Защита") - self.text.index("Статья 152. Защита"),
        )

    def test_the_page_certifies_as_the_whole_article(self):
        self.assertEqual(source_text.verdict(self.text, layer="statutes", expect_article="152")["outcome"], "full_text")

    def test_an_article_the_page_only_links_to_is_not_found(self):
        self.assertIn("Статья 152.1. Охрана изображения", self.text)
        self.assertEqual(
            source_text.verdict(self.text, layer="statutes", expect_article="152.1")["outcome"],
            "excerpt:article_not_found",
        )


def saved(name: str) -> str:
    """A saved raw text by its full file name — the run-74 pages are `.md`, as `mf sources save` wrote them."""
    return (FIXTURES / name).read_text(encoding="utf-8")


FILLER = "The court considered the submissions of both parties at length. " * 70
"""4 000+ characters with no act marker in any language: the length test passes and only the number decides."""


def long_judgment(head: str) -> str:
    return head + "\n\n" + FILLER


class RunSeventyFourUkTest(unittest.TestCase):
    """D-219: the complete UK sources run 74 saved and `verdict` called excerpts.

    Byte copies of the run-74 raw texts: three legislation.gov.uk pages of the PECR 2003 (a `Regulation N`
    and a `SCHEDULE 1` heading, which the article grammar did not know) and two Find Case Law judgments whose
    neutral citation is printed glued to its label (`Number[2017] EWHC 3113 (QB)`, `EWHC3113`, `00362`).
    """

    PECR_PAGES = (("uk-pecr-reg-22.md", "reg 22", "22"), ("uk-pecr-reg-31.md", "reg 31", "31"),
                  ("uk-pecr-sch-1.md", "Sch 1", "1"))

    def test_the_regulation_and_schedule_pages_are_whole_on_their_labelled_unit(self):
        for name, unit, _ in self.PECR_PAGES:
            with self.subTest(page=name):
                answer = source_text.verdict(saved(name), layer="statutes", expect_article=unit)
                self.assertEqual(answer["outcome"], "full_text")

    def test_a_bare_number_does_not_reach_a_regulation_or_a_schedule(self):
        for name, _, number in self.PECR_PAGES:
            with self.subTest(page=name):
                answer = source_text.verdict(saved(name), layer="statutes", expect_article=number)
                self.assertEqual(answer["outcome"], "excerpt:article_not_found")

    def test_the_judgments_are_whole_on_their_neutral_citation(self):
        for name, number in (
            ("uk-ewhc-qb-2017-3113-morrisons.md", "[2017] EWHC 3113 (QB)"),
            ("uk-ukftt-grc-2024-362-triboo.md", "[2024] UKFTT 362 (GRC)"),
        ):
            with self.subTest(page=name):
                answer = source_text.verdict(saved(name), layer="case_law", expect_number=number)
                self.assertEqual(answer["outcome"], "full_text")
                self.assertIsNone(answer["error"])
                self.assertFalse(answer["found"]["russian"])

    def test_a_neighbouring_neutral_citation_still_fails(self):
        for name, number in (
            ("uk-ewhc-qb-2017-3113-morrisons.md", "[2017] EWHC 3114 (QB)"),
            ("uk-ukftt-grc-2024-362-triboo.md", "[2024] UKFTT 36 (GRC)"),
        ):
            with self.subTest(page=name):
                answer = source_text.verdict(saved(name), layer="case_law", expect_number=number)
                self.assertEqual(answer["outcome"], "excerpt:identity_unverified")

    def test_a_german_statute_page_classifies_as_before(self):
        """gesetze-im-internet § 823 BGB after `markup_to_text`: whole before D-219, whole after it."""
        text = saved("de-gii-bgb-823.txt")
        self.assertEqual(source_text.verdict(text, layer="statutes", expect_article="823")["outcome"], "full_text")
        self.assertEqual(
            source_text.verdict(text, layer="statutes", expect_article="82")["outcome"], "excerpt:article_not_found"
        )


class HeadingUnitTest(unittest.TestCase):
    """D-219: a requested unit ending in a digit is not the unit that continues with a letter."""

    BODY = "Article 12A.\n\n" + "The controller shall provide the information in a concise and intelligible form. " * 4

    def test_a_bare_number_does_not_find_the_lettered_article(self):
        self.assertEqual(source_text.article_body_chars(self.BODY, "12"), 0)
        answer = source_text.verdict(self.BODY, layer="statutes", expect_article="12")
        self.assertEqual(answer["outcome"], "excerpt:article_not_found")

    def test_the_lettered_article_still_finds_itself(self):
        self.assertEqual(source_text.article_body_chars(self.BODY, "12A"), len(self.BODY))
        answer = source_text.verdict(self.BODY, layer="statutes", expect_article="12A")
        self.assertEqual(answer["outcome"], "full_text")

    def test_a_labelled_unit_finds_the_heading_of_its_own_kind(self):
        for heading, unit, neighbour in (
            ("Regulation 5", "reg 5", "Regulation 6"),
            ("Reg. 5", "regulation 5", "Reg. 6"),
            ("SCHEDULE 5", "Sch 5", "SCHEDULE 6"),
            ("Sch. 5", "schedule 5", "Sch 6"),
            ("Rule 5", "Rule 5", "Rule 6"),
            ("Rule. 5", "Rule 5", "Rule. 6"),
            ("Rule 5", "Rule. 5", "Rule 6"),
            ("Rule. 5", "rule. 5", "Rule 6"),
            ("Regulation. 5", "Reg. 5", "Regulation. 6"),
            ("Schedule. 5", "sch. 5", "Schedule. 6"),
        ):
            with self.subTest(heading=heading, unit=unit):
                text = heading + "\n\n" + "x " * 150 + "\n" + neighbour + "\n\n" + "y " * 150
                self.assertEqual(source_text.article_body_chars(text, unit), text.index("\n" + neighbour) + 1)

    def test_a_labelled_unit_finds_no_other_kind_of_unit(self):
        text = "Article 5\n\n" + "x " * 150 + "\nRule 5\n\n" + "y " * 150
        self.assertEqual(source_text.article_body_chars(text, "reg 5"), 0)
        self.assertEqual(source_text.article_body_chars(text, "Sch 5"), 0)
        self.assertEqual(source_text.article_body_chars(text, "reg 5A"), 0)

    BODY_TEXT = "\n\n" + "The Commissioner may serve a notice on the person concerned. " * 6

    def test_a_labelled_unit_never_finds_a_unit_that_continues_it(self):
        """Final review: `reg 5A` is not `Regulation 5AB`, `Sch I` is not `SCHEDULE II`, `Rule 2` is not `Rule 23`."""
        for heading, unit in (
            ("Regulation 5AB", "reg 5A"),
            ("Regulation 5A1", "reg 5A"),
            ("Regulation 5A.1", "reg 5A"),
            ("Regulation 5A-B", "reg 5A"),
            ("Regulation 5A/2", "reg 5A"),
            ("SCHEDULE II", "Sch I"),
            ("SCHEDULE IA", "Sch I"),
            ("Rule 23", "Rule 2"),
        ):
            with self.subTest(heading=heading, unit=unit):
                text = heading + self.BODY_TEXT
                self.assertEqual(source_text.article_body_chars(text, unit), 0)
                answer = source_text.verdict(text, layer="statutes", expect_article=unit)
                self.assertEqual(answer["outcome"], "excerpt:article_not_found")

    def test_a_labelled_unit_finds_itself_however_its_heading_ends(self):
        for heading, unit in (
            ("Regulation 5A", "reg 5A"),
            ("Regulation 5A.", "reg 5A"),
            ("Regulation 5A—Title of the regulation", "reg 5A"),
            ("SCHEDULE I", "Sch I"),
            ("Schedule I. Enforcement", "Sch I"),
        ):
            with self.subTest(heading=heading, unit=unit):
                text = heading + self.BODY_TEXT
                self.assertEqual(source_text.article_body_chars(text, unit), len(text))
                answer = source_text.verdict(text, layer="statutes", expect_article=unit)
                self.assertEqual(answer["outcome"], "full_text")

    def test_a_bare_number_never_finds_a_part_a_chapter_or_a_labelled_unit(self):
        for heading in ("Part 5", "Chapter 5", "Regulation 5", "Schedule 5", "Rule 5"):
            with self.subTest(heading=heading):
                text = heading + "\n\n" + "x " * 150
                self.assertEqual(source_text.article_body_chars(text, "5"), 0)
                answer = source_text.verdict(text, layer="statutes", expect_article="5")
                self.assertEqual(answer["outcome"], "excerpt:article_not_found")


class NonRussianNumberTest(unittest.TestCase):
    """D-219: outside the Russian rule, the expected number is also accepted token by token."""

    def outcome(self, head: str, number: str) -> str:
        return source_text.verdict(long_judgment(head), layer="case_law", expect_number=number)["outcome"]

    def test_the_number_glued_to_its_label_and_zero_padded_is_found(self):
        self.assertEqual(self.outcome("Neutral Citation Number[2017] EWHC3113 (QB)Case No: HQ15X05099",
                                      "[2017] EWHC 3113 (QB)"), "full_text")
        self.assertEqual(self.outcome("Neutral Citation Number: [2024] UKFTT 00362 (GRC)",
                                      "[2024] UKFTT 362 (GRC)"), "full_text")

    def test_a_bracketed_year_after_whitespace_does_not_continue_the_number(self):
        self.assertEqual(self.outcome("Obergefell v. Hodges, 576 U.S. 644 (2015)", "576 U.S. 644"), "full_text")

    def test_a_longer_number_never_matches(self):
        for head, number in (
            ("Neutral Citation Number: [2020] UKSC 12", "[2020] UKSC 1"),
            ("Neutral Citation Number: [2020] UKSC 1A", "[2020] UKSC 1"),
            ("Obergefell v. Hodges, 576 U.S. 6440", "576 U.S. 644"),
            ("Judgment in Case C-3402/1", "C-340/21"),
            ("Judgment in Case C-340/210", "C-340/21"),
        ):
            with self.subTest(head=head):
                self.assertEqual(self.outcome(head, number), "excerpt:identity_unverified")

    def test_a_number_continued_without_whitespace_never_matches(self):
        self.assertEqual(self.outcome("ECLI:NL:HR:2023:123.1", "ECLI:NL:HR:2023:123"), "excerpt:identity_unverified")
        self.assertEqual(self.outcome("Case 7-C-340/21", "C-340/21"), "excerpt:identity_unverified")

    def test_a_russian_twin_is_not_matched_even_when_the_text_reads_as_non_russian(self):
        text = "Дело № 305-ЭС24-8702 (1,3)-2\n\n" + "Текст страницы портала без реквизитов акта. " * 100
        self.assertGreater(len(text), source_text.FULL_TEXT_MIN_CHARS_CASE)
        self.assertFalse(source_text.is_russian_act(text))
        answer = source_text.verdict(text, layer="case_law", expect_number="305-ЭС24-8702 (1,3)")
        self.assertEqual(answer["outcome"], "excerpt:identity_unverified")
        base = source_text.verdict(
            "Дело № 305-ЭС24-8702 (1,3)\n\n" + "Текст страницы портала без реквизитов акта. " * 100,
            layer="case_law",
            expect_number="305-ЭС24-8702",
        )
        self.assertEqual(base["outcome"], "excerpt:identity_unverified")

    def test_the_russian_branch_does_not_use_the_token_rule(self):
        text = fixture("vs-chamber-pdf-layer")
        answer = source_text.verdict(
            text, layer="case_law", expect_number="305-ЭС24-8702 (1,3)", expect_date="2025-10-27"
        )
        self.assertEqual(answer["error"], "requisites_mismatch")


class RunEightyFourUkTest(unittest.TestCase):
    """D-251: the complete UK texts run 84 saved and `verdict` called excerpts (`03-sources.md` N1).

    Byte copies: the legislation.gov.uk page of UK GDPR Chapter III Section 1, whose art 12A heading carries
    an amendment marker and a territorial tag (`[F18Article 12A.U.K.Meaning of …`), and two S.I. 2026/386
    Schedule paragraphs, whose heading is `Paragraph 23` / `Paragraph 41`.
    """

    def test_the_lettered_article_under_an_amendment_marker_is_whole(self):
        answer = source_text.verdict(saved("uk-gdpr-ch3-s1-art-12a.txt"), layer="statutes", expect_article="12A")
        self.assertEqual("full_text", answer["outcome"])

    def test_the_schedule_paragraphs_are_whole_on_their_label(self):
        for name, unit in (("uk-si-2026-386-sch-2-para-23.txt", "para 23"),
                           ("uk-si-2026-386-sch-3-para-41.txt", "paragraph 41")):
            with self.subTest(page=name):
                answer = source_text.verdict(saved(name), layer="statutes", expect_article=unit)
                self.assertEqual("full_text", answer["outcome"])

    def test_a_neighbouring_paragraph_and_a_bare_number_are_not_found(self):
        for name, unit in (("uk-si-2026-386-sch-2-para-23.txt", "para 22"),
                           ("uk-si-2026-386-sch-3-para-41.txt", "para 40"),
                           ("uk-si-2026-386-sch-2-para-23.txt", "23")):
            with self.subTest(page=name, unit=unit):
                answer = source_text.verdict(saved(name), layer="statutes", expect_article=unit)
                self.assertEqual("excerpt:article_not_found", answer["outcome"])


class TerritorialTagTest(unittest.TestCase):
    """D-251: `.U.K.` after a unit is legislation.gov.uk's extent tag, not a continuation of the unit."""

    BODY = "\n\n" + "The controller shall provide the information in a concise and intelligible form. " * 4

    def test_the_tag_does_not_hide_the_unit(self):
        for heading, unit in (("[F18Article 12A.U.K.Meaning", "12A"), ("Article 12U.K.Transparent", "12"),
                              ("Regulation 22U.K.Use of electronic mail", "reg 22")):
            with self.subTest(heading=heading):
                text = heading + self.BODY
                self.assertEqual(len(text), source_text.article_body_chars(text, unit))

    def test_the_tag_never_lets_a_shorter_unit_through(self):
        for heading, unit in (("[F18Article 12A.U.K.Meaning", "12"), ("Article 12AU.K.", "12"),
                              ("Paragraph 41", "para 4")):
            with self.subTest(heading=heading, unit=unit):
                self.assertEqual(0, source_text.article_body_chars(heading + self.BODY, unit))

    def test_the_marker_is_not_a_russian_heading(self):
        text = "Статья 12\n\n" + "Текст статьи о порядке предоставления информации. " * 6
        self.assertEqual(len(text), source_text.article_body_chars(text, "12"))
        self.assertEqual(0, source_text.article_body_chars(text, "12A"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
