"""Tests for scripts/memoforge/quotes.py — the `quote extract`/`skip` contract (ТЗ §5.3, M5, §9)."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import limits, quotes, schema, sources, state_io, task  # noqa: E402

TASK_ID = "memo-20260101T000000Z-quotes"


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

    def test_the_default_cap_is_thirty_words_and_it_bites(self):
        long_sentence = "The controller " + " ".join(["shall"] * 30) + " document the decision."
        source_id = self.register(text=f"Recital 1\n\n{long_sentence}\n", name="long.md")
        self.assertEqual(30, limits.QUOTE_DEFAULT_MAX_WORDS)
        result = quotes.extract_quote(self.work_dir, source_id, "The controller shall")
        self.assertEqual("too_long", result["error"])
        self.assertEqual(limits.QUOTE_DEFAULT_MAX_WORDS, result["max_words"])
        self.assertGreater(result["words"], limits.QUOTE_DEFAULT_MAX_WORDS)

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
