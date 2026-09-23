"""Tests for scripts/memoforge/quotes.py — the `quote extract`/`skip`/`locate` contract (ТЗ §5.3, M5, §9)."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, limits, quotes, schema, sources, state_io, task  # noqa: E402

TASK_ID = "memo-20260101T000000Z-quotes"

A40_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "source_text" / "a40-630-25-decision.md"
"""The saved text of decision А40-630/2025 from a real run, byte for byte (D-207)."""


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
                "issued_at": "2026-01-01T00:00:00.000Z",
                "status": None,
            }
        )
        state["steps"] = rows

    state_io.write_state(work_dir, mutator)

RAW_EN = (
    "# Article 6 - Lawfulness of processing\n"
    "\n"
    "1. Processing shall be lawful only if and to the extent that at least one of the following applies.\n"
    "\n"
    "(a) the data subject has given consent to the processing of his or her personal data;\n"
    "\n"
    "Consent must be freely given. It must be specific. It must be informed.\n"
)

RAW_RU = (
    "Статья 6\n"
    "\n"
    "Обработка персональных данных допускается только с согласия субъекта. "
    "Согласие должно быть свободным.\n"
)

RAW_TYPOGRAPHIC = (
    "Recital 32\n"
    "\n"
    "Consent should be given by a clear affirmative act — for example a written statement — "
    "establishing a “freely given” indication… of the data subject’s wishes.\n"
)


class QuotesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work_dir = self.root / TASK_ID
        task.create_work_dir_tree(self.work_dir)
        state = task.build_initial_state(
            task_id=TASK_ID,
            user_query="quotes",
            language="en",
            work_dir=self.work_dir,
            output_folder=self.root,
            config={"writer_model": "opus", "source_review_gate": "auto", "intake_max_questions": 5},
            created_at="2026-01-01T00:00:00.000Z",
        )
        state_io.create_state(self.work_dir, state)
        self.addCleanup(self._tmp.cleanup)

    def register(self, *, text: str | None = RAW_EN, name: str = "raw.md", **overrides) -> str:
        raw_file = None
        if text is not None:
            raw_file = self.root / name
            raw_file.write_bytes(text.encode("utf-8"))
        payload = {
            "layer": "statutes",
            "title": "GDPR Article 6",
            "citation": "GDPR, Art. 6",
            "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            "tool": "mcp__ldh__get_document",
            "tier": "critical",
            "raw_file": raw_file,
        }
        payload.update(overrides)
        return sources.register_source(self.work_dir, **payload)["source_id"]

    def freeze(self, step: str = "s-010") -> dict:
        issue_step(self.work_dir, step)
        args = argparse.Namespace(workdir=str(self.work_dir), freeze=True, step=step, attempt=1, phase=None)
        return sources.run_pack(args)

    def raw_path(self, source_id: str) -> Path:
        return self.work_dir / sources.read_registry(self.work_dir)["sources"][source_id]["raw_path"]


# --- normalisation and sentence bounds -------------------------------------


class NormalisationTest(unittest.TestCase):
    def test_narrow_normalisation_unifies_quotes_dashes_and_ellipses(self):
        self.assertEqual('"freely given"', quotes.normalized_text("“freely given”"))
        self.assertEqual("a - b", quotes.normalized_text("a — b"))
        self.assertEqual("wishes...", quotes.normalized_text("wishes…"))
        self.assertEqual("subject's", quotes.normalized_text("subject’s"))

    def test_whitespace_is_collapsed_but_the_index_map_stays_aligned(self):
        text = "one  two\n\tthree"
        normalized, index = quotes.normalize(text)
        self.assertEqual("one two three", normalized)
        self.assertEqual(len(normalized), len(index))
        self.assertEqual("three", text[index[normalized.index("three")] :])

    def test_word_count_uses_the_normalised_form(self):
        self.assertEqual(3, quotes.count_words("  one\ttwo\nthree  "))

    def test_sentence_spans_split_on_terminal_punctuation(self):
        text = "One sentence. Another sentence. A third one."
        self.assertEqual(3, len(quotes.sentence_spans(text)))

    def test_sentence_spans_do_not_split_on_abbreviations(self):
        text = "See Art. 6 of the Regulation. The next sentence follows."
        spans = quotes.sentence_spans(text)
        self.assertEqual(2, len(spans))
        self.assertEqual("See Art. 6 of the Regulation.", text[spans[0][0] : spans[0][1]])

    def test_sentence_spans_take_the_abbreviations_of_another_language(self):
        # D-174: the caller passes the language's own set; the default stays the English one, because
        # a source text is not written in the memo language.
        text = "Die Verarbeitung ist gem. Art. 6 Abs. 1 DSGVO zulässig."
        self.assertGreater(len(quotes.sentence_spans(text)), 1)
        self.assertEqual(1, len(quotes.sentence_spans(text, frozenset({"gem", "art", "abs"}))))

    def test_block_boundaries_close_a_sentence(self):
        text = "# Heading\n\n(a) first item;\n(b) second item;\n"
        self.assertEqual(3, len(quotes.sentence_spans(text)))

    def test_soft_wrapped_prose_is_not_split_at_the_line_break(self):
        text = "Consent must be freely given and\nunambiguous in every case."
        self.assertEqual(1, len(quotes.sentence_spans(text)))


# --- extract ---------------------------------------------------------------


class ExtractTest(QuotesTestCase):
    def test_exact_sentence_bounded_range_is_stored(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "Consent must be freely given")
        self.assertNotIn("errors", result)
        self.assertEqual("Consent must be freely given.", result["text"])
        self.assertEqual(5, result["words"])
        raw = self.raw_path(source_id).read_text(encoding="utf-8")
        self.assertEqual(result["text"], raw[result["char_start"] : result["char_end"]])
        self.assertEqual(state_io.sha256_file(self.raw_path(source_id)), result["raw_sha256"])

    def test_a_partial_fragment_is_widened_to_its_sentence(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "must be specific")
        self.assertEqual("It must be specific.", result["text"])

    def test_registry_is_written_and_validates(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "Consent must be freely given")
        registry = quotes.read_quotes(self.work_dir)
        self.assertEqual([], schema.validate(registry, "quotes"))
        self.assertIn(result["quote_id"], registry["quotes"])
        self.assertEqual(source_id, registry["quotes"][result["quote_id"]]["source_id"])

    def test_the_same_range_reuses_its_quote_id(self):
        source_id = self.register()
        first = quotes.extract_quote(self.work_dir, source_id, "Consent must be freely given")
        second = quotes.extract_quote(self.work_dir, source_id, "must be freely given")
        self.assertEqual(first["quote_id"], second["quote_id"])
        self.assertTrue(second["existing"])
        self.assertEqual(1, len(quotes.read_quotes(self.work_dir)["quotes"]))

    def test_too_long_returns_shorter_sentence_bounded_candidates(self):
        source_id = self.register()
        result = quotes.extract_quote(
            self.work_dir, source_id, "freely given. It must be specific", max_words=5
        )
        self.assertEqual("too_long", result["error"])
        self.assertNotIn("quote_id", result)
        self.assertEqual(9, result["words"])
        self.assertTrue(result["candidates"])
        for candidate in result["candidates"]:
            self.assertLessEqual(candidate["words"], 5)
        self.assertIn("Consent must be freely given.", [row["text"] for row in result["candidates"]])
        self.assertEqual({}, quotes.read_quotes(self.work_dir)["quotes"])

    def test_the_default_cap_is_sixty_words_and_it_bites(self):
        """D-217: one sentence of a provision (Art 33(1) GDPR is 56 words) fits; 61 words do not."""
        fits = "The controller " + " ".join(["shall"] * 50) + " document the decision."
        too_long = "The processor " + " ".join(["must"] * 56) + " record the breach."
        self.assertEqual((55, 61), (quotes.count_words(fits), quotes.count_words(too_long)))
        source_id = self.register(text=f"Recital 1\n\n{fits} {too_long}\n", name="long.md")
        self.assertEqual(60, limits.QUOTE_DEFAULT_MAX_WORDS)

        result = quotes.extract_quote(self.work_dir, source_id, "The controller shall")
        self.assertNotIn("errors", result)
        self.assertEqual(fits, result["text"])
        self.assertEqual(55, result["words"])

        result = quotes.extract_quote(self.work_dir, source_id, "The processor must")
        self.assertEqual("too_long", result["error"])
        self.assertEqual(limits.QUOTE_DEFAULT_MAX_WORDS, result["max_words"])
        self.assertEqual(61, result["words"])

    def test_too_long_without_any_short_enough_sentence_has_no_candidates(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "the data subject has given consent", max_words=4)
        self.assertEqual("too_long", result["error"])
        self.assertEqual([], result["candidates"])

    def test_ambiguous_returns_every_position(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "It must be")
        self.assertEqual("ambiguous", result["error"])
        self.assertEqual(2, result["matches"])
        self.assertEqual(2, len(result["positions"]))
        raw = self.raw_path(source_id).read_text(encoding="utf-8")
        for position in result["positions"]:
            self.assertEqual("It must be", raw[position["char_start"] : position["char_end"]])

    def test_not_found_returns_candidates(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "legitimate interests of the controller")
        self.assertEqual("not_found", result["error"])
        self.assertTrue(result["candidates"])

    def test_no_raw_for_a_source_registered_without_one(self):
        source_id = self.register(text=None, tier="background")
        self.assertEqual("no_raw", quotes.extract_quote(self.work_dir, source_id, "anything")["error"])

    def test_no_raw_when_the_file_disappeared(self):
        source_id = self.register()
        self.raw_path(source_id).unlink()
        self.assertEqual("no_raw", quotes.extract_quote(self.work_dir, source_id, "Consent")["error"])

    def test_unknown_source_is_rejected(self):
        result = quotes.extract_quote(self.work_dir, "nope", "Consent")
        self.assertEqual("unknown_source", result["error"])

    def test_raw_changed_after_the_freeze(self):
        source_id = self.register()
        self.freeze()
        self.raw_path(source_id).write_bytes(b"a different document entirely")
        result = quotes.extract_quote(self.work_dir, source_id, "different document")
        self.assertEqual("raw_changed", result["error"])
        self.assertIn("expected", result)
        self.assertNotEqual(result["expected"], result["actual"])

    def test_raw_changed_before_the_freeze_too(self):
        source_id = self.register()
        self.raw_path(source_id).write_bytes(b"tampered text about consent")
        self.assertEqual("raw_changed", quotes.extract_quote(self.work_dir, source_id, "consent")["error"])

    def test_after_the_freeze_the_snapshot_version_is_the_one_used(self):
        source_id = self.register()
        self.freeze()
        result = quotes.extract_quote(self.work_dir, source_id, "Consent must be freely given")
        self.assertEqual(sources.snapshot_map(self.work_dir)[source_id], result["raw_sha256"])

    def test_after_the_freeze_a_merged_id_is_pinned_by_its_canonical(self):
        """D-143: `pack --freeze` collapsed this id into a duplicate; its raw file is untouched."""
        canonical = self.register()
        alias = self.register(
            name="copy.md",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj/cons",
            source_id="gdpr-article-6-copy",
        )
        self.freeze()
        pack = sources.read_pack(self.work_dir)
        self.assertEqual({alias: canonical}, pack["merged_into"])
        result = quotes.extract_quote(self.work_dir, alias, "Consent must be freely given")
        self.assertNotIn("errors", result)
        self.assertEqual(alias, result["source_id"])
        self.assertEqual(sources.snapshot_map(self.work_dir)[canonical], result["raw_sha256"])

    def test_typographic_variants_of_the_request_still_match(self):
        source_id = self.register(text=RAW_TYPOGRAPHIC, name="recital.md")
        result = quotes.extract_quote(self.work_dir, source_id, 'establishing a "freely given" indication')
        self.assertNotIn("errors", result)
        self.assertIn("“freely given”", result["text"], "the stored text keeps the source typography")

    def test_cyrillic_source_keeps_its_language_and_offsets(self):
        source_id = self.register(text=RAW_RU, name="raw-ru.md", title="ФЗ-152, статья 6", url="https://pravo.gov.ru/x")
        result = quotes.extract_quote(self.work_dir, source_id, "Согласие должно быть свободным")
        self.assertEqual("ru", result["lang"])
        self.assertEqual("Согласие должно быть свободным.", result["text"])
        self.assertEqual(4, result["words"])
        raw = self.raw_path(source_id).read_text(encoding="utf-8")
        self.assertEqual(result["text"], raw[result["char_start"] : result["char_end"]])

    def test_explicit_language_wins(self):
        source_id = self.register()
        result = quotes.extract_quote(self.work_dir, source_id, "Consent must be freely given", lang="en-GB")
        self.assertEqual("en-GB", result["lang"])

    def test_empty_request_is_not_found(self):
        source_id = self.register()
        self.assertEqual("not_found", quotes.extract_quote(self.work_dir, source_id, "   ")["error"])

    def test_cli_accepts_a_text_file(self):
        source_id = self.register()
        payload = self.root / "fragment.txt"
        payload.write_text("Consent must be freely given", encoding="utf-8")
        args = argparse.Namespace(
            workdir=str(self.work_dir),
            source=source_id,
            text=None,
            text_file=str(payload),
            max_words=limits.QUOTE_DEFAULT_MAX_WORDS,
            lang=None,
        )
        self.assertEqual("Consent must be freely given.", quotes.run_extract(args)["text"])

    def test_cli_without_text_errors(self):
        args = argparse.Namespace(
            workdir=str(self.work_dir),
            source="x",
            text=None,
            text_file=None,
            max_words=limits.QUOTE_DEFAULT_MAX_WORDS,
            lang=None,
        )
        self.assertEqual(["missing_text"], quotes.run_extract(args)["errors"])


# --- locate (D-207) --------------------------------------------------------


class LocateTest(QuotesTestCase):
    """`mf quote locate` finds a passage in the saved text of a real decision and writes nothing."""

    def setUp(self) -> None:
        super().setUp()
        copy = self.root / A40_FIXTURE.name
        copy.write_bytes(A40_FIXTURE.read_bytes())
        answer = sources.register_source(
            self.work_dir,
            layer="case_law",
            title="Решение АС г. Москвы по делу № А40-630/2025",
            citation="Решение АС г. Москвы по делу № А40-630/2025",
            tool="mf sources save",
            tier="critical",
            raw_file=copy,
            raw_kind="full_text",
            source_id="a40",
        )
        self.assertEqual("a40", answer["source_id"])
        self.assertEqual(state_io.sha256_bytes(A40_FIXTURE.read_bytes()), answer["raw_sha256"])
        self.raw = A40_FIXTURE.read_text(encoding="utf-8-sig")

    def locate(self, text: str, **options) -> dict:
        """One call against `a40`, with the invariants every answer and every passage must hold."""
        result = quotes.locate_passage(self.work_dir, "a40", text, **options)
        self.assertEqual("a40", result["source_id"])
        self.assertEqual("full_text", result["raw_kind"])
        for passage in result.get("passages", []):
            self.assertLessEqual(passage["char_start"], passage["match_start"])
            self.assertLessEqual(passage["match_end"], passage["char_end"])
            self.assertEqual(self.raw[passage["char_start"] : passage["char_end"]], passage["text"])
            self.assertEqual(
                quotes.normalized_text(text),
                quotes.normalized_text(self.raw[passage["match_start"] : passage["match_end"]]),
            )
        self.assertFalse(quotes.quotes_path(self.work_dir).exists(), "locate never writes quotes.json")
        return result

    def test_the_i2_quote_is_not_found_and_the_formula_is_a_candidate(self):
        result = self.locate("размер возмещения определяется по Формуле: [Действительная стоимость товара]")
        self.assertEqual("not_found", result["status"])
        self.assertNotIn("passages", result)
        self.assertLessEqual(len(result["candidates"]), limits.QUOTE_CANDIDATES_MAX)
        self.assertTrue(any("[Размер возмещения] = (" in row["text"] for row in result["candidates"]))

    def test_the_i4_quote_is_not_found_and_its_long_sentence_is_a_candidate(self):
        result = self.locate("включение явно обременительных положений в договор... не допускается")
        self.assertEqual("not_found", result["status"])
        hits = [row for row in result["candidates"] if "явно обременительных" in row["text"]]
        self.assertTrue(hits)
        # Locate has no word cap: this sentence is longer than the extractor's old 30-word default
        # (D-207; the default is 60 since D-217) and still comes back.
        self.assertGreater(hits[0]["words"], 30)
        for row in result["candidates"]:
            self.assertEqual(self.raw[row["char_start"] : row["char_end"]], row["text"])

    def test_a_phrase_is_found_with_the_rest_of_its_sentence(self):
        result = self.locate("ограничила пределы своей ответственности")
        self.assertEqual("found", result["status"])
        self.assertEqual(1, result["matches"])
        self.assertEqual(1, len(result["passages"]))
        self.assertIn("частичного реального ущерба", result["passages"][0]["text"])

    def test_the_courts_own_finding_is_found(self):
        result = self.locate("Суд признает, что рассчитать стоимость оказанных по товару услуг Ozon невозможно")
        self.assertEqual("found", result["status"])
        self.assertEqual(1, result["matches"])

    def test_a_repeated_phrase_is_ambiguous_and_the_passages_are_capped(self):
        phrase = "Ozon"
        occurrences = quotes.normalized_text(self.raw).count(phrase)
        self.assertGreater(occurrences, limits.LOCATE_MAX_PASSAGES, "the phrase must repeat for the cap to bite")
        result = self.locate(phrase)
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual(occurrences, result["matches"])
        self.assertEqual(limits.LOCATE_MAX_PASSAGES, len(result["passages"]))
        starts = [passage["match_start"] for passage in result["passages"]]
        self.assertEqual(sorted(starts), starts, "the passages follow the text")

    def test_an_empty_request_is_not_found_without_candidates(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                result = self.locate(text)
                self.assertEqual("not_found", result["status"])
                self.assertEqual([], result["candidates"])

    def test_a_context_above_the_ceiling_is_clamped_not_refused(self):
        result = self.locate("Ozon", context=10_000)
        self.assertEqual("ambiguous", result["status"])
        lengths = []
        for passage in result["passages"]:
            length = passage["char_end"] - passage["char_start"]
            match = passage["match_end"] - passage["match_start"]
            self.assertLessEqual(length, 2 * limits.LOCATE_CONTEXT_MAX + match)
            lengths.append(length - match)
        self.assertGreater(max(lengths), 2 * limits.LOCATE_CONTEXT_CHARS, "clamped to the ceiling, not the default")

    def test_a_small_context_inside_a_long_sentence_never_cuts_the_match(self):
        # Source line 306 carries one ~750-character "sentence" of claim numbers: no sentence edge
        # lies within 50 characters of this match, so the passage is the bare window around it.
        phrase = "39883391; 38505830"
        self.assertEqual(1, quotes.normalized_text(self.raw).count(phrase))
        result = self.locate(phrase, context=50)
        self.assertEqual("found", result["status"])
        passage = result["passages"][0]
        self.assertEqual(passage["match_start"] - 50, passage["char_start"])
        self.assertEqual(passage["match_end"] + 50, passage["char_end"])

    def test_the_recital_frame_reaches_the_reader(self):
        # The formula (source line 288) sits inside the court's account of the defendant's offer:
        # the default context carries the frame before it (line 284) and after it (line 294).
        result = self.locate("Стороны согласились, что размер возмещения определяется по Формуле")
        self.assertEqual("found", result["status"])
        text = result["passages"][0]["text"]
        self.assertIn("В разделе оферты", text)
        self.assertIn("Ответчик предлагает", text)

    def test_the_passage_keeps_whole_sentences_inside_the_window(self):
        source_id = self.register(text="Alpha beta. Gamma delta. Epsilon zeta. Eta theta.\n", name="short.md")
        wide = quotes.locate_passage(self.work_dir, source_id, "Epsilon", context=15)
        self.assertEqual("found", wide["status"])
        self.assertEqual("agent_summary", wide["raw_kind"])
        expected = {"char_start": 12, "char_end": 38, "match_start": 25, "match_end": 32}
        self.assertEqual({**expected, "text": "Gamma delta. Epsilon zeta."}, wide["passages"][0])
        # No sentence ends inside a narrow window: its edge stands, and the match is never cut.
        narrow = quotes.locate_passage(self.work_dir, source_id, "Epsilon", context=5)
        self.assertEqual("Epsilon zeta", narrow["passages"][0]["text"])

    def test_a_blank_request_is_not_found_before_the_source_is_resolved(self):
        # Fix round 1: blank text is answered before `raw_state`, so no refusal of the source shadows it.
        bare = self.register(text=None, tier="background")
        for source_id, text in (("nope", ""), (bare, "   ")):
            with self.subTest(source_id=source_id):
                self.assertEqual(
                    {"source_id": source_id, "status": "not_found", "raw_kind": "none", "candidates": []},
                    quotes.locate_passage(self.work_dir, source_id, text),
                )

    def test_a_repeated_word_of_the_request_counts_each_time(self):
        # Fix round 1: the share is over the request's tokens as written, not over their set — here 4/5
        # against 1/5. Over the set both would score 1/2 and `_ratio` would put the long word first.
        raw = "An extraordinarilylong word stands here.\n\nThe cat sat on the mat.\n"
        candidates = quotes.locate_candidates(raw, quotes.sentence_spans(raw), "cat cat cat cat extraordinarilylong")
        self.assertEqual("The cat sat on the mat.", candidates[0]["text"])

    def test_zero_passages_returns_the_count_alone(self):
        # Fix round 1: `max_passages` is clamped to zero, not floored at one; a negative value becomes 0.
        source_id = self.register(text="Alpha beta. Gamma delta. Epsilon zeta. Eta theta.\n", name="short.md")
        for requested in (0, -2):
            with self.subTest(max_passages=requested):
                result = quotes.locate_passage(self.work_dir, source_id, "Epsilon", max_passages=requested)
                self.assertEqual("found", result["status"])
                self.assertEqual(1, result["matches"])
                self.assertEqual([], result["passages"])

    def test_a_long_sentence_is_a_candidate_cut_at_a_word_boundary(self):
        sentence = "The court " + " ".join(["examined the claim numbers"] * 80) + " and dismissed it."
        raw = f"Heading\n\n{sentence}\n"
        spans = quotes.sentence_spans(raw)
        candidates = quotes.locate_candidates(raw, spans, "the court dismissed the claim")
        best = candidates[0]
        self.assertTrue(sentence.startswith(best["text"]))
        self.assertLessEqual(len(best["text"]), limits.LOCATE_CANDIDATE_MAX_CHARS)
        self.assertGreaterEqual(len(best["text"]), limits.LOCATE_CANDIDATE_MAX_CHARS - len("examined "))
        self.assertTrue(raw[best["char_end"]].isspace(), "the cut falls between two words")

    def test_no_raw_and_unknown_source_come_from_the_raw_state(self):
        bare = self.register(text=None, tier="background")
        result = quotes.locate_passage(self.work_dir, bare, "anything")
        self.assertEqual({"source_id": bare, "status": "no_raw", "raw_kind": "none"}, result)
        result = quotes.locate_passage(self.work_dir, "nope", "anything")
        self.assertEqual({"source_id": "nope", "status": "unknown_source", "raw_kind": "none"}, result)

    def test_raw_changed_carries_both_digests(self):
        path = self.raw_path("a40")
        path.write_bytes(path.read_bytes() + b"\ntampered\n")
        result = quotes.locate_passage(self.work_dir, "a40", "Ozon")
        self.assertEqual("raw_changed", result["status"])
        self.assertEqual("full_text", result["raw_kind"])
        self.assertEqual(state_io.sha256_bytes(A40_FIXTURE.read_bytes()), result["expected"])
        self.assertEqual(state_io.sha256_file(path), result["actual"])

    def run_cli(self, source_id: str, *options: str) -> tuple[int, dict]:
        argv = ["quote", "locate", "--workdir", str(self.work_dir), "--source", source_id, "--text", "Ozon", *options]
        self.assertIs(quotes.run_locate, cli.build_parser().parse_args(argv).func)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(argv)
        return code, json.loads(buffer.getvalue())

    def test_the_cli_returns_the_same_answer_with_exit_zero(self):
        expected = self.locate("Ozon", context=200, max_passages=1)
        self.assertEqual(1, len(expected["passages"]))
        self.assertEqual((0, expected), self.run_cli("a40", "--context", "200", "--max-passages", "1"))
        # Every status is data, not a refusal: an unknown source exits 0 as well.
        self.assertEqual(
            (0, {"source_id": "nope", "status": "unknown_source", "raw_kind": "none"}), self.run_cli("nope")
        )


# --- skip ------------------------------------------------------------------


class SkipTest(QuotesTestCase):
    def _skip(self, reason: str, note: str | None = None, section: str = "s-3-2") -> dict:
        args = argparse.Namespace(
            workdir=str(self.work_dir),
            section=section,
            source="gdpr-article-6",
            reason=reason,
            note=note,
        )
        return quotes.run_skip(args)

    def test_every_enum_reason_is_accepted(self):
        self.register()
        for reason in ("too_long", "ambiguous", "not_found", "no_raw", "raw_changed", "already_used"):
            with self.subTest(reason=reason):
                result = self._skip(reason)
                self.assertEqual(reason, result["skip"]["reason"])
        self.assertEqual([], schema.validate(quotes.read_quotes(self.work_dir), "quotes"))

    def test_unknown_reason_is_rejected(self):
        self.register()
        result = quotes.record_skip(self.work_dir, "s-3-2", "gdpr-article-6", "too-long")
        self.assertTrue(result["errors"])
        self.assertEqual([], quotes.read_quotes(self.work_dir)["skips"])

    def test_other_requires_a_note(self):
        self.register()
        self.assertEqual(["note_required_for_other"], self._skip("other")["errors"])
        self.assertEqual([], quotes.read_quotes(self.work_dir)["skips"])

    def test_other_with_a_note_reaches_drafting_warnings(self):
        self.register()
        result = self._skip("other", note="the passage is behind a paywall")
        self.assertEqual("the passage is behind a paywall", result["skip"]["note"])
        warnings = state_io.read_state(self.work_dir)["drafting_warnings"]
        self.assertEqual(1, len(warnings))
        self.assertIn("paywall", warnings[0])

    def test_one_skip_per_section_and_source(self):
        self.register()
        self._skip("too_long")
        result = self._skip("already_used")
        self.assertTrue(result["replaced"])
        registry = quotes.read_quotes(self.work_dir)
        self.assertEqual(1, len(registry["skips"]))
        self.assertEqual("already_used", registry["skips"][0]["reason"])

    def test_skips_of_different_sections_coexist(self):
        self.register()
        self._skip("too_long", section="s-3-1")
        self._skip("already_used", section="s-3-2")
        registry = quotes.read_quotes(self.work_dir)
        self.assertEqual(2, len(registry["skips"]))
        self.assertEqual("too_long", quotes.skip_for(registry, "s-3-1", "gdpr-article-6")["reason"])
        self.assertIsNone(quotes.skip_for(registry, "s-3-3", "gdpr-article-6"))


if __name__ == "__main__":
    unittest.main()
