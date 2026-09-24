"""Tests for scripts/memoforge/citations.py — the C-rules of `draft audit-citations` (ТЗ §5.4, §9)."""

from __future__ import annotations

import argparse
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
from memoforge import citations, i18n, quotes, schema, sources, state_io, stepctx, task  # noqa: E402

TASK_ID = "memo-20260101T000000Z-citations"
DRAFTS = Path(__file__).resolve().parent / "fixtures" / "drafts"

RAW_ART_6 = (
    "# Article 6 - Lawfulness of processing\n"
    "\n"
    "1. Processing shall be lawful only if and to the extent that at least one of the following applies.\n"
    "\n"
    "(a) the data subject has given consent to the processing of his or her personal data;\n"
    "\n"
    "Consent must be freely given, specific, informed and unambiguous.\n"
)

RAW_ART_7 = (
    "# Article 7 - Conditions for consent\n"
    "\n"
    "1. The controller shall be able to demonstrate that the data subject has consented.\n"
    "\n"
    "3. The data subject shall have the right to withdraw his or her consent at any time.\n"
    "\n"
    "Withdrawal must be as easy as giving consent.\n"
)

QUOTE_LINE = "> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous."


def fixture(name: str) -> str:
    return (DRAFTS / f"{name}.md").read_text(encoding="utf-8-sig")


class CitationsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work_dir = self.root / TASK_ID
        task.create_work_dir_tree(self.work_dir)
        state = task.build_initial_state(
            task_id=TASK_ID,
            user_query="citations",
            language="en",
            work_dir=self.work_dir,
            output_folder=self.root,
            config={
                "writer_model": "opus",
                "source_review_gate": "auto",
                "intake_max_questions": 5,
                "template_id": "classical-memo",
            },
            created_at="2026-01-01T00:00:00.000Z",
        )
        state_io.create_state(self.work_dir, state)
        self.addCleanup(self._tmp.cleanup)
        self._seed()

    def _seed(self) -> None:
        raw = self.root / "art6.md"
        raw.write_bytes(RAW_ART_6.encode("utf-8"))
        sources.register_source(
            self.work_dir,
            layer="statutes",
            title="Regulation (EU) 2016/679, Article 6",
            citation="GDPR, Art. 6",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art6",
            tool="mcp__ldh__get_document",
            tier="critical",
            raw_file=raw,
            # D-204: the one `critical` source of the clean research is text the code saved whole;
            # an agent copy would draw the extended C-08 on every direct citation of it.
            raw_kind="full_text",
            source_id="gdpr-art-6",
        )
        art7 = self.root / "art7.md"
        art7.write_bytes(RAW_ART_7.encode("utf-8"))
        sources.register_source(
            self.work_dir,
            layer="statutes",
            title="Regulation (EU) 2016/679, Article 7",
            citation="GDPR, Art. 7",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art7",
            tool="mcp__ldh__get_document",
            tier="supporting",
            raw_file=art7,
            source_id="gdpr-art-7",
        )
        quotes.extract_quote(
            self.work_dir, "gdpr-art-6", "Consent must be freely given, specific, informed and unambiguous"
        )
        state_io.write_json_atomic(
            self.work_dir / "research" / "statutes.json",
            {
                "layer": "statutes",
                "issues": [
                    {
                        "issue_id": "i1",
                        "findings": [
                            {
                                "source_id": "gdpr-art-6",
                                "proposition": "Consent is a lawful basis.",
                                "pinpoint": "Art. 6(1)(a)",
                                "role": "rule",
                                "weight": "binding",
                                "confidence": "high",
                                "tier": "critical",
                                "quote_short": "consent",
                            },
                            {
                                "source_id": "gdpr-art-7",
                                "proposition": "Withdrawal must be as easy as consent.",
                                "pinpoint": "Art. 7(3)",
                                "role": "application",
                                "weight": "binding",
                                "confidence": "high",
                                "tier": "supporting",
                                "quote_short": "withdraw",
                            },
                        ],
                    }
                ],
                "_meta": {"task_id": TASK_ID, "step_id": "s-005", "attempt": 1, "slot": "statutes"},
            },
        )

    # -- helpers ----------------------------------------------------------

    def set_currency(self, rows: list[dict]) -> None:
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {
                "checked_at": "2026-01-02",
                "sources": rows,
                "blocking": [row["source_id"] for row in rows if row["status"] == "do_not_use"],
                "warnings": [],
            },
        )

    def set_us(self, source_id: str, verdict: str) -> None:
        registry = sources.read_registry(self.work_dir)
        registry["sources"][source_id]["verification"]["us"] = verdict
        registry["sources"][source_id]["verification"]["us_by"] = "agent"
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)

    def freeze(self, step: str = "s-010") -> dict:
        self.issue_step(step)
        args = argparse.Namespace(workdir=str(self.work_dir), freeze=True, step=step, attempt=1, phase=None)
        return sources.run_pack(args)

    def issue_step(self, step_id: str, attempt: int = 1) -> None:
        """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

        def mutator(state: dict) -> None:
            rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
            for row in rows:
                if row.get("step_id") == step_id:
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

        state_io.write_state(self.work_dir, mutator)

    def audit(self, text: str) -> list[dict]:
        return citations.audit(text, work_dir=self.work_dir)

    def uncited_rule_source(self, mention: str | None = None) -> str:
        """`classical-clean` with gdpr-art-6 packed as a rule source and no token citing it."""
        sentence = "Consent is a lawful basis for the processing at issue"
        text = fixture("classical-clean").replace(
            f"{sentence} [[src:gdpr-art-6 Art. 6(1)(a)]].",
            f"{sentence}{'' if mention is None else ' ' + mention}.",
        )
        return text.replace(QUOTE_LINE + "\n\n", "")

    def rules(self, findings: list[dict]) -> set[str]:
        return {row["rule"] for row in findings}

    def only(self, findings: list[dict], rule: str) -> list[dict]:
        return [row for row in findings if row["rule"] == rule]

    def add_source(
        self,
        source_id: str,
        text: str,
        *,
        raw_kind: str = "full_text",
        tier: str = "supporting",
        layer: str = "statutes",
    ) -> None:
        """One more registered source whose saved text is `text`, of the given kind (D-200)."""
        raw = self.root / f"{source_id}.md"
        raw.write_bytes(text.encode("utf-8"))
        sources.register_source(
            self.work_dir,
            layer=layer,
            title=f"Source {source_id}",
            citation=f"Source {source_id}",
            url=f"https://example.org/{source_id}",
            tool="mf-save example.org",
            tier=tier,
            raw_file=raw,
            raw_kind=raw_kind,
            source_id=source_id,
        )

    def patch_record(self, source_id: str, **fields) -> None:
        registry = sources.read_registry(self.work_dir)
        registry["sources"][source_id].update(fields)
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)

    def drop_field(self, source_id: str, name: str) -> None:
        registry = sources.read_registry(self.work_dir)
        registry["sources"][source_id].pop(name, None)
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)

    def add_original(self, source_id: str, layer: str = "case_law") -> None:
        """Give a record the PDF original a `save` keeps beside its text (D-201)."""
        original = self.work_dir / "research" / "raw" / layer / f"{source_id}.pdf"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"%PDF-1.4\n% a scanned page\n")
        self.patch_record(
            source_id,
            raw_original_path=f"research/raw/{layer}/{source_id}.pdf",
            raw_original_sha256=state_io.sha256_file(original),
        )

    def add_scan(self, source_id: str, *, tier: str = "critical") -> None:
        """A PDF saved without a text layer (D-201): `raw_kind: none`, an original and no text."""
        sources.register_source(
            self.work_dir,
            layer="case_law",
            title=f"Scan {source_id}",
            citation=f"Scan {source_id}",
            url=f"https://example.org/{source_id}.pdf",
            tool="mf-save example.org",
            tier=tier,
            source_id=source_id,
        )
        self.patch_record(source_id, raw_kind="none")
        self.add_original(source_id)


class CleanTest(CitationsTestCase):
    def test_the_clean_fixture_passes_every_c_rule(self):
        self.freeze()
        findings = self.audit(fixture("classical-clean"))
        self.assertEqual([], findings, [f"{row['rule']}: {row['hint']}" for row in findings])


class C01Test(CitationsTestCase):
    def test_unknown_src_token_is_a_blocker(self):
        self.freeze()
        text = fixture("classical-clean").replace("[[src:gdpr-art-7 Art. 7(3)]]", "[[src:made-up Art. 7(3)]]")
        findings = self.only(self.audit(text), "C-01")
        self.assertEqual(1, len(findings))
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertIn("made-up", findings[0]["hint"])

    def test_unknown_quote_token_is_a_blocker(self):
        self.freeze()
        text = fixture("classical-clean").replace("[[q:q-gdpr-art-6-1]]", "[[q:q-invented-1]]")
        findings = self.only(self.audit(text), "C-01")
        self.assertEqual(1, len(findings))
        self.assertIn("quotes.json", findings[0]["hint"])


class C02Test(CitationsTestCase):
    def test_altered_blockquote_text_is_a_blocker(self):
        self.freeze()
        text = fixture("classical-clean").replace(
            QUOTE_LINE, "> [[q:q-gdpr-art-6-1]] Consent must be freely given and nothing else."
        )
        findings = self.only(self.audit(text), "C-02")
        self.assertTrue(any("verbatim" in row["hint"] for row in findings))

    def test_typographic_variation_of_the_blockquote_still_passes(self):
        self.freeze()
        text = fixture("classical-clean").replace(
            QUOTE_LINE,
            "> [[q:q-gdpr-art-6-1]]  Consent  must be freely given, specific, informed and unambiguous.",
        )
        self.assertNotIn("C-02", self.rules(self.audit(text)))

    def test_substituted_raw_file_breaks_the_triple_sha_equality(self):
        self.freeze()
        raw = self.work_dir / sources.read_registry(self.work_dir)["sources"]["gdpr-art-6"]["raw_path"]
        raw.write_bytes(RAW_ART_6.replace("freely given", "loosely given").encode("utf-8"))
        findings = self.only(self.audit(fixture("classical-clean")), "C-02")
        self.assertTrue(any("sha mismatch" in row["hint"] for row in findings))
        self.assertTrue(all(row["severity"] == "blocker" for row in findings))

    def test_snapshot_is_never_updated_to_match_a_changed_raw_file(self):
        self.freeze()
        pinned = sources.snapshot_map(self.work_dir)["gdpr-art-6"]
        raw = self.work_dir / sources.read_registry(self.work_dir)["sources"]["gdpr-art-6"]["raw_path"]
        raw.write_bytes(b"replaced")
        self.audit(fixture("classical-clean"))
        self.assertEqual(pinned, sources.snapshot_map(self.work_dir)["gdpr-art-6"])

    def test_quote_token_outside_a_blockquote_is_a_blocker(self):
        self.freeze()
        text = fixture("classical-clean").replace(QUOTE_LINE, "[[q:q-gdpr-art-6-1]] Consent must be freely given.")
        findings = self.only(self.audit(text), "C-02")
        self.assertTrue(any("must introduce a blockquote" in row["hint"] for row in findings))

    def test_a_range_that_no_longer_holds_the_quote_is_a_blocker(self):
        self.freeze()
        registry = quotes.read_quotes(self.work_dir)
        record = registry["quotes"]["q-gdpr-art-6-1"]
        record["char_start"] = 0
        record["char_end"] = 40
        with sources.sources_lock(self.work_dir):
            quotes.write_quotes(self.work_dir, registry)
        findings = self.only(self.audit(fixture("classical-clean")), "C-02")
        self.assertTrue(any("no longer holds the quote" in row["hint"] for row in findings))


class C03Test(CitationsTestCase):
    def test_do_not_use_source_cited_directly_is_a_blocker(self):
        self.set_currency([{"source_id": "gdpr-art-7", "status": "do_not_use", "note": "repealed"}])
        self.freeze()
        findings = self.only(self.audit(fixture("classical-clean")), "C-03")
        self.assertEqual(1, len(findings))
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertIn("gdpr-art-7", findings[0]["hint"])

    def test_do_not_use_source_reached_through_a_quote_is_a_blocker(self):
        self.set_currency([{"source_id": "gdpr-art-6", "status": "do_not_use", "note": "superseded"}])
        self.freeze()
        findings = self.only(self.audit(fixture("classical-clean")), "C-03")
        hints = " ".join(row["hint"] for row in findings)
        self.assertIn("and its quote", hints, "the [[q:]] token resolves to its source_id too")


class C04Test(CitationsTestCase):
    def test_manual_check_source_in_a_risk_line_is_a_blocker(self):
        self.set_currency([{"source_id": "gdpr-art-7", "status": "manual_check", "note": "unverified"}])
        self.freeze()
        text = fixture("classical-clean").replace(
            "Risk: high. A regulator would treat the asymmetry as a defect of the consent itself.",
            "Risk: high. A regulator would treat this as a defect [[src:gdpr-art-7 Art. 7(3)]].",
        )
        findings = self.only(self.audit(text), "C-04")
        self.assertTrue(findings)
        self.assertEqual("blocker", findings[0]["severity"])

    def test_unresolved_us_citation_in_the_executive_summary_is_a_blocker(self):
        self.set_us("gdpr-art-7", "unresolved")
        self.freeze()
        text = fixture("classical-clean").replace(
            "- The withdrawal control is the operative gap and must ship before launch. Risk: high.",
            "- The withdrawal control is the gap [[src:gdpr-art-7 Art. 7(3)]]. Risk: high.",
        )
        findings = self.only(self.audit(text), "C-04")
        self.assertTrue(findings)

    def test_a_soft_wrap_does_not_take_a_citation_out_of_the_risk_line(self):
        # C-04 / D-12: the Risk line is a markdown paragraph. The same sentence with and without a
        # newline in it must give the same verdict, or a line break silently clears the rule.
        self.set_currency([{"source_id": "gdpr-art-7", "status": "manual_check", "note": "unverified"}])
        self.freeze()
        original = "Risk: high. A regulator would treat the asymmetry as a defect of the consent itself."
        variants = {
            "one_line": "Risk: high. A regulator would treat this as a defect "
            "from [[src:gdpr-art-7 Art. 7(3)]].",
            "soft_wrapped": "Risk: high. A regulator would treat this as a defect\n"
            "from [[src:gdpr-art-7 Art. 7(3)]].",
        }
        for label, replacement in variants.items():
            with self.subTest(risk_line=label):
                text = fixture("classical-clean").replace(original, replacement)
                findings = self.only(self.audit(text), "C-04")
                self.assertEqual(1, len(findings), [row["hint"] for row in findings])
                self.assertEqual("blocker", findings[0]["severity"])
                self.assertIn("gdpr-art-7", findings[0]["hint"])

    def test_a_soft_wrapped_body_paragraph_is_still_not_a_risk_line(self):
        self.set_currency([{"source_id": "gdpr-art-7", "status": "manual_check", "note": "unverified"}])
        self.freeze()
        original = "Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]]."
        for label, replacement in (
            ("one_line", original),
            ("soft_wrapped", "Withdrawal must be as easy as\ngiving consent [[src:gdpr-art-7 Art. 7(3)]]."),
        ):
            with self.subTest(body=label):
                text = fixture("classical-clean").replace(original, replacement)
                self.assertNotIn("C-04", self.rules(self.audit(text)))

    def test_manual_check_source_in_the_body_is_allowed(self):
        self.set_currency([{"source_id": "gdpr-art-7", "status": "manual_check", "note": "unverified"}])
        self.freeze()
        self.assertNotIn("C-04", self.rules(self.audit(fixture("classical-clean"))))


class LocalizedRiskLineTest(CitationsTestCase):
    """D-174: C-04 recognises the Risk line through the label of the run's memo language."""

    def setUp(self) -> None:
        super().setUp()
        self.packs = self.root / "i18n"
        self.packs.mkdir()
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", _i18n.RU)

    def set_language(self, code: str) -> None:
        """Record the memo language the way `task new` / `mf task language` does (D-171)."""

        def mutator(state: dict) -> None:
            state["language"] = code

        state_io.write_state(self.work_dir, mutator)

    def test_a_localized_risk_line_carries_c04_only_in_its_own_language(self):
        self.set_currency([{"source_id": "gdpr-art-7", "status": "manual_check", "note": "unverified"}])
        self.freeze()
        text = fixture("classical-clean").replace(
            "Risk: high. A regulator would treat the asymmetry as a defect of the consent itself.",
            "Риск: высокий. A regulator would treat this as a defect [[src:gdpr-art-7 Art. 7(3)]].",
        )
        self.set_language("ru")
        findings = self.only(self.audit(text), "C-04")
        self.assertEqual(1, len(findings), [row["hint"] for row in findings])
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertIn("gdpr-art-7", findings[0]["hint"])

        self.set_language("en")
        self.assertEqual([], self.only(self.audit(text), "C-04"), "the English label no longer matches")


class C05Test(CitationsTestCase):
    def test_without_a_freeze_the_audit_refuses(self):
        findings = self.audit(fixture("classical-clean"))
        self.assertEqual(1, len(findings))
        self.assertEqual("C-05", findings[0]["rule"])
        self.assertIn("not frozen", findings[0]["hint"])

    def test_a_source_pack_changed_after_publication_is_a_c05_blocker(self):
        # D-41: the audit reads `research/source-pack.json` through `published[]`; a snapshot edited
        # after the freeze must not silently widen or narrow what the memo may cite.
        self.freeze()
        pack = state_io.read_json(self.work_dir / sources.PACK_PATH)
        pack["snapshot"] = []
        state_io.write_json_atomic(self.work_dir / sources.PACK_PATH, pack)

        findings = self.audit(fixture("classical-clean"))
        self.assertEqual(1, len(findings))
        self.assertEqual("C-05", findings[0]["rule"])
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertIn(stepctx.OUTPUT_MODIFIED, findings[0]["hint"])

    def test_a_source_added_after_the_freeze_never_reaches_the_memo(self):
        self.freeze()
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["late-source"] = dict(registry["sources"]["gdpr-art-7"], title="Late source")
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)
        text = fixture("classical-clean").replace(
            "[[src:gdpr-art-7 Art. 7(3)]]", "[[src:late-source Art. 7(3)]]"
        )
        findings = self.only(self.audit(text), "C-05")
        self.assertEqual(1, len(findings))
        self.assertIn("freeze snapshot", findings[0]["hint"])


class C06Test(CitationsTestCase):
    def test_missing_pinpoint_is_a_major_finding(self):
        self.freeze()
        text = "Consent is a lawful basis. [[src:gdpr-art-6]]\n"
        findings = self.only(self.audit(text), "C-06")
        self.assertEqual(1, len(findings))
        self.assertEqual("major", findings[0]["severity"])

    def test_malformed_pinpoint_is_a_major_finding(self):
        self.freeze()
        text = "Consent is a lawful basis. [[src:gdpr-art-6 somewhere near the end]]\n"
        self.assertIn("C-06", self.rules(self.audit(text)))

    def test_recognised_pinpoint_forms(self):
        forms = ("Art. 6(1)(a)", "Article 5", "para 42", "paras 42-45", "s 12", "recital 32", "p 15", "6(1)(f)")
        for pinpoint in forms:
            with self.subTest(pinpoint=pinpoint):
                self.assertTrue(citations.pinpoint_ok(pinpoint))
        for pinpoint in ("", "the middle bit", "passim"):
            with self.subTest(pinpoint=pinpoint):
                self.assertFalse(citations.pinpoint_ok(pinpoint))

    def test_cyrillic_pinpoint_forms(self):
        # D-186: the pinpoint is written as the Russian source numbers it. Fix round 1: the
        # number fragment is strict — `ст. 10а` accepted, `ст. foo 152` rejected. `BARE_PINPOINT`
        # is unchanged, so `pinpoint_ok("152")` stays True (established behaviour).
        for pinpoint in ("ст. 152", "ст. 10а", "п. 2 ст. 152", "ч. 1 ст. 14.3", "абз. 2 п. 1 ст. 10",
                         "пп. 3 п. 1 ст. 8"):
            with self.subTest(pinpoint=pinpoint):
                self.assertTrue(citations.pinpoint_ok(pinpoint))
        for pinpoint in ("ст. foo 152", "foo 152"):
            with self.subTest(pinpoint=pinpoint):
                self.assertFalse(citations.pinpoint_ok(pinpoint))

    def test_capitalised_cyrillic_pinpoint_forms(self):
        # D-186 fix round 2: a sentence-initial label is the same pinpoint — `docx/oscola.py` has
        # always recognised `Ст. 152`, and C-06 must not call it malformed.
        for pinpoint in ("Ст. 152", "П. 2 ст. 152", "Ч. 1 ст. 14.3", "Ст. 10А"):
            with self.subTest(pinpoint=pinpoint):
                self.assertTrue(citations.pinpoint_ok(pinpoint))
        for pinpoint in ("Ст. foo 152", "ст. foo 152", "foo 152"):
            with self.subTest(pinpoint=pinpoint):
                self.assertFalse(citations.pinpoint_ok(pinpoint))
        # The Cyrillic grammar itself never matches a bare number; `152` stays BARE_PINPOINT's.
        self.assertIsNone(citations.CYRILLIC_PINPOINT.match("152"))
        self.assertTrue(citations.pinpoint_ok("152"))

    def test_contract_and_offer_pinpoint_forms(self):
        # D-195: a contract or an offer is pinpointed by its own clause numbers and section
        # headings — `разд`, `раздел`, `гл` and `прил` next to the statute labels, and a heading
        # in guillemets may stand where a number would.
        for pinpoint in (
            "разд. 4",
            "раздел 4",
            "гл. 2 п. 3",
            "прил. 1",
            "п. 3 разд. «Возмещение»",
            "п. 5.1 разд. «FBO»",
            "разд. «Возмещение»",
        ):
            with self.subTest(pinpoint=pinpoint):
                self.assertTrue(citations.pinpoint_ok(pinpoint))
        # Only a heading after `разд.` may stand without a digit; nothing else may.
        for pinpoint in ("гл. «Возмещение»", "п. «Возмещение»", "разд.", "гл.", "прил."):
            with self.subTest(pinpoint=pinpoint):
                self.assertFalse(citations.pinpoint_ok(pinpoint))

    def test_the_audit_and_the_renderer_know_the_same_cyrillic_labels(self):
        # D-195: two copies, one list — a label C-06 accepts is one the renderer prints as written.
        from memoforge.docx import oscola

        self.assertEqual(citations.CYRILLIC_LABEL, oscola.CYRILLIC_LABEL)


class C07Test(CitationsTestCase):
    def test_uncited_rule_source_is_a_major_informational_finding(self):
        self.freeze()
        findings = self.only(self.audit(self.uncited_rule_source()), "C-07")
        self.assertEqual(1, len(findings))
        self.assertEqual("major", findings[0]["severity"])
        self.assertIsNone(findings[0]["line"])
        self.assertIn("gdpr-art-6", findings[0]["hint"])

    def test_a_cited_rule_source_produces_no_c07(self):
        self.freeze()
        self.assertNotIn("C-07", self.rules(self.audit(fixture("classical-clean"))))

    def test_c07_is_major_for_every_run_and_the_audit_takes_no_mode(self):
        # D-243: the Brief `info` grade of C-07 went with the mode; the audit no longer takes one.
        self.freeze()
        text = self.uncited_rule_source()
        self.assertEqual("major", citations.SEVERITY["C-07"])
        self.assertEqual(["major"], [row["severity"] for row in self.only(self.audit(text), "C-07")])
        for name in ("SEVERITY_BY_MODE", "BRIEF_MODE", "resolve_mode", "severity_for"):
            self.assertFalse(hasattr(citations, name), name)
        with self.assertRaises(TypeError):
            citations.audit(text, work_dir=self.work_dir, mode="full")
        with self.assertRaises(TypeError):
            citations.pinpoint_findings(text, work_dir=self.work_dir, mode="full")

    def test_c07_without_a_location_carries_null_and_never_an_empty_excerpt(self):
        # D34-10: the run shipped 25 findings with `line: null, section_id: null, excerpt: ""`.
        self.freeze()
        finding = self.only(self.audit(self.uncited_rule_source()), "C-07")[0]
        self.assertIsNone(finding["line"])
        self.assertIsNone(finding["section_id"])
        self.assertIsNone(finding["excerpt"])

    def test_c07_carries_the_line_and_section_when_the_source_token_can_be_located(self):
        self.freeze()
        text = self.uncited_rule_source("[src:gdpr-art-6 Art. 6(1)(a)]")
        finding = self.only(self.audit(text), "C-07")[0]
        self.assertEqual("s-3-1", finding["section_id"])
        self.assertIn("[src:gdpr-art-6 Art. 6(1)(a)]", text.split("\n")[finding["line"] - 1])
        self.assertIn("[src:gdpr-art-6 Art. 6(1)(a)]", finding["excerpt"])
        self.assertIn("malformed citation", finding["hint"])

    def test_c07_does_not_match_a_longer_source_id_that_contains_this_one(self):
        self.freeze()
        text = self.uncited_rule_source("see gdpr-art-60 instead")
        self.assertIsNone(self.only(self.audit(text), "C-07")[0]["line"])


class C08Test(CitationsTestCase):
    def _drop_saved_text(self) -> None:
        """Re-register `gdpr-art-7` the way a run without `--raw-file` leaves it (A43-4)."""
        registry = sources.read_registry(self.work_dir)
        record = registry["sources"]["gdpr-art-7"]
        record["raw_path"] = None
        record["raw_sha256"] = None
        record["raw_chars"] = 0
        raw = self.work_dir / "research" / "raw" / "statutes" / "gdpr-art-7.md"
        if raw.is_file():
            raw.unlink()
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)

    def test_c08_direct_citation_of_a_source_without_saved_text_is_a_major(self):
        # A43-4 / D-156
        self._drop_saved_text()
        self.freeze()
        findings = citations.audit(fixture("classical-clean"), work_dir=self.work_dir)
        c08 = [row for row in findings if row["rule"] == "C-08"]
        self.assertEqual(1, len(c08), findings)
        self.assertEqual("major", c08[0]["severity"])
        self.assertIn("gdpr-art-7", c08[0]["hint"])
        self.assertNotIn("blocker", {row["severity"] for row in findings}, "a caveat, not a refusal (M9)")

    def test_c08_is_silent_for_a_source_with_saved_text_and_for_background(self):
        sources.register_source(self.work_dir, layer="doctrine", title="Commentary", citation="Commentary 2024",
                                url="https://example.org/commentary", tool="WebFetch example.org",
                                tier="background", source_id="commentary-1")
        self.freeze()
        text = fixture("classical-clean") + "\nBackground reading [[src:commentary-1 p 2]].\n"
        findings = citations.audit(text, work_dir=self.work_dir)
        self.assertEqual([], [row for row in findings if row["rule"] == "C-08"], findings)


RU_ACT = (
    "Статья 5. Предмет регулирования\n"
    "\n"
    "1. Настоящий закон применяется согласно ст. 7 и пункту 2 статьи 3.\n"
    "\n"
    "Статья 152.1. Охрана изображения гражданина\n"
)
"""A saved Russian text: its whole number tokens are 5, 1, 7, 2, 3 and 152.1 — never 9, 15 or 152."""


class C08ExtendedTest(CitationsTestCase):
    """D-204: a `critical` source whose pack `raw_kind` is neither `full_text` nor `client_file`."""

    def c08(self, text: str) -> list[dict]:
        return self.only(self.audit(text), "C-08")

    def test_a_critical_excerpt_cited_directly_is_a_major(self):
        self.add_source("ru-act", RU_ACT, raw_kind="excerpt", tier="critical")
        self.freeze()
        findings = self.c08("Body [[src:ru-act ст. 5]].\n")
        self.assertEqual(1, len(findings), findings)
        self.assertEqual("major", findings[0]["severity"])
        self.assertIn("ru-act", findings[0]["hint"])
        self.assertIn("excerpt", findings[0]["hint"])

    def test_a_critical_agent_summary_is_the_same_major(self):
        # D-204: `excerpt` and `agent_summary` differ only in the appendix line.
        self.add_source("ru-copy", RU_ACT, raw_kind="agent_summary", tier="critical")
        self.freeze()
        findings = self.c08("Body [[src:ru-copy ст. 5]].\n")
        self.assertEqual(1, len(findings), findings)
        self.assertEqual("major", findings[0]["severity"])
        self.assertIn("agent_summary", findings[0]["hint"])

    def test_a_critical_client_file_and_a_full_text_are_silent(self):
        self.add_source("client-contract", RU_ACT, raw_kind="client_file", tier="critical")
        self.add_source("ru-full", RU_ACT.replace("гражданина", "лица"), raw_kind="full_text", tier="critical")
        self.freeze()
        self.assertEqual(
            [], self.c08("Body [[src:client-contract ст. 5]] and [[src:ru-full ст. 5]].\n")
        )

    def test_a_supporting_excerpt_is_silent(self):
        # The owner's rule concerns a `critical` claim; the null-digest rule keeps its two tiers.
        self.add_source("ru-support", RU_ACT, raw_kind="excerpt", tier="supporting")
        self.freeze()
        self.assertEqual([], self.c08("Body [[src:ru-support ст. 5]].\n"))

    def test_the_pack_entry_decides_after_the_freeze(self):
        # D-200: after the freeze the pack is authoritative; the registry is not read for the kind.
        self.add_source("ru-act", RU_ACT, raw_kind="excerpt", tier="critical")
        self.freeze()
        self.patch_record("ru-act", raw_kind="full_text")
        self.assertEqual(1, len(self.c08("Body [[src:ru-act ст. 5]].\n")))

    def test_an_entry_frozen_without_the_field_is_silent(self):
        # D-200: a pack frozen before `raw_kind` existed declares nothing, and the extended C-08
        # does not fire for it — only the null-digest rule can.
        self.add_source("old-act", RU_ACT, raw_kind="agent_summary", tier="critical")
        self.drop_field("old-act", "raw_kind")
        self.freeze()
        entry = next(row for row in sources.read_pack(self.work_dir)["entries"] if row["source_id"] == "old-act")
        self.assertNotIn("raw_kind", entry)
        self.assertEqual([], self.c08("Body [[src:old-act ст. 5]].\n"))

    def test_a_critical_source_without_text_draws_one_c08_per_token(self):
        sources.register_source(
            self.work_dir,
            layer="statutes",
            title="No text",
            citation="No text 2024",
            url="https://example.org/no-text",
            tool="WebFetch example.org",
            tier="critical",
            source_id="no-text",
        )
        self.patch_record("no-text", raw_kind="none")
        self.freeze()
        findings = self.c08("Body [[src:no-text ст. 5]].\n")
        self.assertEqual(1, len(findings), findings)
        self.assertIn("no saved text", findings[0]["hint"])

    def test_a_quote_on_a_critical_excerpt_is_not_a_direct_citation(self):
        self.add_source("ru-act", RU_ACT, raw_kind="excerpt", tier="critical")
        quote = quotes.extract_quote(self.work_dir, "ru-act", "Настоящий закон применяется согласно ст. 7")
        self.freeze()
        text = f"> [[q:{quote['quote_id']}]] {quote['text']}\n"
        self.assertEqual([], self.c08(text))

    def test_a_critical_scan_draws_c08(self):
        # Controller's addendum §2: a scan is `raw_kind: none` — its requisites were not checked by
        # code, which is exactly what C-08 says.
        self.add_scan("vs-scan", tier="critical")
        self.freeze()
        findings = self.c08("Body [[src:vs-scan п. 3]].\n")
        self.assertEqual(1, len(findings), findings)
        self.assertEqual("major", findings[0]["severity"])


class C09Test(CitationsTestCase):
    """D-204: a pinpoint whose printed number the saved text of its source does not contain."""

    def c09(self, pinpoint: str, source_id: str = "ru-act") -> list[dict]:
        return self.only(self.audit(f"Body [[src:{source_id} {pinpoint}]].\n"), "C-09")

    def seed_ru(self, raw_kind: str = "full_text", text: str = RU_ACT, source_id: str = "ru-act") -> None:
        self.add_source(source_id, text, raw_kind=raw_kind)

    def test_c09_is_a_major_rule(self):
        self.assertEqual("major", citations.SEVERITY["C-09"])

    def test_a_number_the_saved_text_lacks_is_a_major_with_the_source_and_the_pinpoint(self):
        self.seed_ru()
        self.freeze()
        findings = self.c09("ст. 9")
        self.assertEqual(1, len(findings), findings)
        row = findings[0]
        self.assertEqual("major", row["severity"])
        self.assertEqual("ru-act", row["source_id"])
        self.assertEqual("ст. 9", row["pinpoint"])
        self.assertIn("ru-act", row["hint"])
        self.assertIn("ст. 9", row["hint"])
        self.assertEqual(1, row["line"])

    def test_a_number_anywhere_in_the_text_satisfies_it(self):
        # Anywhere, not only in a heading: «согласно ст. 7» in the middle of a sentence counts.
        self.seed_ru()
        self.freeze()
        for pinpoint in ("ст. 7", "ст. 5", "п. 2 ст. 3", "п. 1 ст. 5", "Ст. 5", "ст. 152.1"):
            with self.subTest(pinpoint=pinpoint):
                self.assertEqual([], self.c09(pinpoint))

    def test_a_dotted_number_is_matched_whole(self):
        # `152` is not in «152.1», and `152.1` is not in «152.».
        self.seed_ru()
        self.seed_ru(text="Статья 152. Охрана частной жизни\n", source_id="ru-152")
        self.freeze()
        self.assertEqual(1, len(self.c09("ст. 152")))
        self.assertEqual(1, len(self.c09("ст. 152.1", "ru-152")))
        self.assertEqual([], self.c09("ст. 152", "ru-152"))

    def test_every_checked_number_must_be_there(self):
        self.seed_ru()
        self.freeze()
        findings = self.c09("п. 9 ст. 5")
        self.assertEqual(1, len(findings))
        self.assertIn("9", findings[0]["hint"])

    def test_a_part_a_paragraph_and_a_digitless_pinpoint_are_exempt(self):
        self.seed_ru()
        self.freeze()
        for pinpoint in ("ч. 9 ст. 5", "абз. 9 п. 1 ст. 5", "ч. 15", "абз. 9", "разд. «Предмет регулирования»"):
            with self.subTest(pinpoint=pinpoint):
                self.assertEqual([], self.c09(pinpoint))

    def test_the_latin_labels_that_carry_a_number_are_checked(self):
        # The seed's `gdpr-art-6` is code-saved text: «Article 6», «1. Processing», «(a)».
        self.freeze()
        self.assertEqual([], self.c09("Art. 6(1)(a)", "gdpr-art-6"))
        for pinpoint in ("art 9", "Article 9", "para 42", "paras 42-45", "s 12", "section 12", "reg 12",
                         "point 12", "ch 9", "art 6(4)"):
            with self.subTest(pinpoint=pinpoint):
                self.assertEqual(1, len(self.c09(pinpoint, "gdpr-art-6")), pinpoint)

    def test_a_page_a_recital_a_bare_number_and_a_roman_numeral_are_exempt(self):
        self.freeze()
        for pinpoint in ("p 15", "pp 15-17", "page 15", "recital 44", "6(4)", "art IV(1)"):
            with self.subTest(pinpoint=pinpoint):
                self.assertEqual([], self.c09(pinpoint, "gdpr-art-6"))
        # The Roman numeral is exempt, the digit beside it is not.
        self.assertEqual(1, len(self.c09("art IV(9)", "gdpr-art-6")))

    def test_the_numbers_a_pinpoint_prints(self):
        # The grammar is not forked: the numbers are read through `CYRILLIC_LABEL` and `PINPOINT`.
        cases = {
            "ст. 152.1": ["152.1"],
            "п. 2 ст. 152": ["2", "152"],
            "пп. 3 п. 1 ст. 8": ["3", "1", "8"],
            "ч. 5 ст. 36": ["36"],
            "абз. 2 п. 1 ст. 10": ["1", "10"],
            "п. 3 разд. «Возмещение»": ["3"],
            "раздел 4": ["4"],
            "гл. 2 п. 3": ["2", "3"],
            "прил. 1": ["1"],
            "ст. 10а": ["10"],
            "п. 2(1) ст. 7": ["2", "1", "7"],
            "разд. «Возмещение»": [],
            "Art. 6(1)(a)": ["6", "1"],
            "paras 42-45": ["42", "45"],
            "art IV(2)": ["2"],
            "p 15": [],
            "recital 32": [],
            "6(1)(f)": [],
            "§ 26": [],
            "": [],
        }
        for pinpoint, expected in cases.items():
            with self.subTest(pinpoint=pinpoint):
                self.assertEqual(expected, citations.printed_numbers(pinpoint))

    def test_an_agent_summary_is_not_checked(self):
        self.seed_ru(raw_kind="agent_summary")
        self.freeze()
        self.assertEqual([], self.c09("ст. 9"))

    def test_an_excerpt_and_a_client_file_are_checked(self):
        self.seed_ru(raw_kind="excerpt")
        self.seed_ru(raw_kind="client_file", text=RU_ACT + "\nКлиент.\n", source_id="client-contract")
        self.freeze()
        self.assertEqual(1, len(self.c09("ст. 9")))
        self.assertEqual(1, len(self.c09("п. 9", "client-contract")))

    def test_an_entry_frozen_without_the_field_is_not_checked(self):
        # D-200: no field in the pack and a digest in the snapshot reads as `agent_summary`.
        self.seed_ru()
        self.drop_field("ru-act", "raw_kind")
        self.freeze()
        self.assertEqual([], self.c09("ст. 9"))

    def test_a_scan_is_not_checked(self):
        # Controller's addendum §2: no text to search, so no C-09 — C-08 says what is missing.
        self.add_scan("vs-scan", tier="supporting")
        self.freeze()
        self.assertEqual([], self.c09("п. 9", "vs-scan"))

    def test_a_pdf_text_layer_is_checked_like_any_saved_text(self):
        self.add_source("vs-pdf", RU_ACT, raw_kind="full_text", layer="case_law")
        self.add_original("vs-pdf")
        self.freeze()
        self.assertEqual(1, len(self.c09("п. 9", "vs-pdf")))
        self.assertEqual([], self.c09("п. 1 ст. 5", "vs-pdf"))

    def test_a_malformed_pinpoint_is_c06_and_not_c09(self):
        self.seed_ru()
        self.freeze()
        findings = self.audit("Body [[src:ru-act ст. foo 9]].\n")
        self.assertIn("C-06", self.rules(findings))
        self.assertNotIn("C-09", self.rules(findings))

    def test_an_unknown_source_is_c01_and_not_c09(self):
        self.freeze()
        findings = self.audit("Body [[src:ghost ст. 9]].\n")
        self.assertIn("C-01", self.rules(findings))
        self.assertNotIn("C-09", self.rules(findings))

    def test_the_report_carries_c09_and_stays_clean(self):
        # D-204: a major — `clean` still means «no blocker», and the finding validates.
        self.seed_ru()
        self.freeze()
        path = self.work_dir / "drafts" / "v1.md"
        path.write_text(fixture("classical-clean") + "\nSee [[src:ru-act ст. 9]].\n", encoding="utf-8")
        self.issue_step("s-012c")
        result = citations.run_audit(
            argparse.Namespace(
                workdir=str(self.work_dir), step="s-012c", attempt=1, draft="drafts/v1.md", phase="drafting"
            )
        )
        self.assertTrue(result["clean"])
        report = state_io.read_json(self.work_dir / citations.CITATIONS_PATH)
        self.assertEqual([], schema.validate(report, "lint"))
        c09 = self.only(report["findings"], "C-09")
        self.assertEqual(1, len(c09))
        self.assertEqual(("ru-act", "ст. 9"), (c09[0]["source_id"], c09[0]["pinpoint"]))


class MergedSourceTest(CitationsTestCase):
    """D-143 (D-127): findings and quotes keep the ids the freeze collapsed, so the draft cites them.

    Without the alias resolution the `[[src:]]`/`[[q:]]` token drew a C-05 («not in the freeze
    snapshot») while the canonical drew a C-07 («packed as a rule source but never cited»).
    """

    ALIAS = "gdpr-art-6-copy"

    def register_duplicate(self) -> str:
        """The same Article 6 text under a second id — the duplicate `pack --freeze` collapses."""
        raw = self.root / "art6-copy.md"
        raw.write_bytes(RAW_ART_6.encode("utf-8"))
        sources.register_source(
            self.work_dir,
            layer="statutes",
            title="Regulation (EU) 2016/679, Article 6 (consolidated)",
            citation="GDPR, Art. 6",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj/cons#art6",
            tool="mcp__ldh__get_document",
            tier="critical",
            raw_file=raw,
            source_id=self.ALIAS,
        )
        quote = quotes.extract_quote(
            self.work_dir, self.ALIAS, "Consent must be freely given, specific, informed and unambiguous"
        )
        return quote["quote_id"]

    def alias_draft(self, quote_id: str) -> str:
        text = fixture("classical-clean").replace(
            "[[src:gdpr-art-6 Art. 6(1)(a)]]", f"[[src:{self.ALIAS} Art. 6(1)(a)]]"
        )
        return text.replace("[[q:q-gdpr-art-6-1]]", f"[[q:{quote_id}]]")

    def test_the_freeze_collapses_the_duplicate_into_the_first_id(self):
        self.register_duplicate()
        self.freeze()
        pack = sources.read_pack(self.work_dir)
        self.assertEqual({self.ALIAS: "gdpr-art-6"}, pack["merged_into"])
        self.assertNotIn(self.ALIAS, {row["source_id"] for row in pack["snapshot"]})

    def test_tokens_on_a_merged_id_resolve_to_the_canonical_entry(self):
        quote_id = self.register_duplicate()
        self.freeze()
        findings = self.audit(self.alias_draft(quote_id))
        self.assertEqual([], findings, [f"{row['rule']}: {row['hint']}" for row in findings])

    def test_the_canonical_is_cited_by_an_alias_token(self):
        quote_id = self.register_duplicate()
        self.freeze()
        rules = self.rules(self.audit(self.alias_draft(quote_id)))
        self.assertNotIn("C-05", rules, "the alias is inside the freeze through its canonical")
        self.assertNotIn("C-07", rules, "the canonical is cited, by the id the finding carried")

    def test_a_source_outside_the_snapshot_is_still_a_c05(self):
        self.register_duplicate()
        self.freeze()
        text = fixture("classical-clean").replace(
            "[[src:gdpr-art-6 Art. 6(1)(a)]]", "[[src:gdpr-art-6-ghost Art. 6(1)(a)]]"
        )
        rules = self.rules(self.audit(text))
        self.assertIn("C-01", rules, "an id that is in no registry is not an alias")


class AuditCommandTest(CitationsTestCase):
    def write_draft(self, text: str | None = None) -> str:
        path = self.work_dir / "drafts" / "v1.md"
        path.write_text(text if text is not None else fixture("classical-clean"), encoding="utf-8")
        return "drafts/v1.md"

    def run_audit(self, step: str = "s-012c", attempt: int = 1, draft: str = "drafts/v1.md") -> dict:
        self.issue_step(step, attempt)
        args = argparse.Namespace(
            workdir=str(self.work_dir), step=step, attempt=attempt, draft=draft, phase="drafting"
        )
        return citations.run_audit(args)

    def test_audit_publishes_citations_json_and_closes_the_step(self):
        self.freeze()
        draft = self.write_draft()
        result = self.run_audit(draft=draft)
        self.assertTrue(result["clean"])
        report = state_io.read_json(self.work_dir / citations.CITATIONS_PATH)
        self.assertEqual([], schema.validate(report, "lint"))
        self.assertEqual(state_io.sha256_file(self.work_dir / draft), report["draft_sha"])
        state = state_io.read_state(self.work_dir)
        self.assertEqual(
            state_io.sha256_file(self.work_dir / citations.CITATIONS_PATH),
            stepctx.published_sha(state, citations.CITATIONS_PATH),
        )
        self.assertEqual("ok", stepctx.current_step(state, "s-012c")["status"])

    def test_audit_writes_input_and_result_into_the_step_workspace(self):
        self.freeze()
        self.write_draft()
        self.run_audit()
        workspace = self.work_dir / "steps" / "s-012c" / "a1" / "cli"
        self.assertTrue((workspace / "citations.json").is_file())
        self.assertTrue((workspace / "inputs" / "v1.md").is_file())
        self.assertTrue((workspace / "inputs" / "source-pack.json").is_file())

    def test_audit_repeat_of_a_closed_step_is_a_no_op(self):
        self.freeze()
        self.write_draft()
        first = self.run_audit()
        second = self.run_audit()
        self.assertTrue(second["already_done"])
        self.assertEqual(first["draft_sha"], second["draft_sha"])
        self.assertIn("report", second)

    def test_audit_with_other_arguments_on_the_same_identity_is_identity_mismatch(self):
        self.freeze()
        self.write_draft()
        self.run_audit()
        (self.work_dir / "drafts" / "v2.md").write_text(fixture("classical-clean"), encoding="utf-8")
        self.assertEqual(["identity_mismatch"], self.run_audit(draft="drafts/v2.md")["errors"])

    def test_audit_refuses_a_draft_modified_after_publication(self):
        self.freeze()
        draft = self.write_draft()
        entry = stepctx.publish(self.work_dir, "s-011", 1, self.work_dir / draft, draft)
        self.assertTrue(entry["sha256"])
        (self.work_dir / draft).write_text("tampered", encoding="utf-8")
        self.assertEqual([stepctx.OUTPUT_MODIFIED], self.run_audit()["errors"])

    def test_blocker_makes_the_report_not_clean(self):
        self.freeze()
        self.write_draft(fixture("classical-clean").replace("[[src:gdpr-art-7", "[[src:made-up"))
        result = self.run_audit()
        self.assertFalse(result["clean"])
        self.assertGreater(result["blockers"], 0)

    def test_the_report_counts_blockers_and_majors_beside_clean(self):
        # D34-10: the run returned `clean: true` over 25 majors and the orchestrator read it as «ok».
        self.freeze()
        self.write_draft(self.uncited_rule_source())
        result = self.run_audit()
        self.assertTrue(result["clean"])
        self.assertEqual(0, result["blockers"])
        self.assertEqual(1, result["majors"])
        self.assertEqual(result["findings_count"], len(result["findings"]))
        self.assertEqual(["C-07"], sorted({row["rule"] for row in result["findings"]}))
        stored = state_io.read_state(self.work_dir)
        self.assertEqual(1, stepctx.current_step(stored, "s-012c")["result_ref"]["result"]["majors"])

    def test_an_info_finding_keeps_the_published_report_schema_valid(self):
        """`schemas/lint.schema.json` keeps the `info` severity: a report carrying one still publishes.

        The finding is made `info` here rather than by a run mode (D-242), so the check does not
        depend on which rules `citations.audit` grades `info`.
        """
        self.freeze()
        self.write_draft(self.uncited_rule_source())
        real_audit = citations.audit

        def audit_as_info(text: str, **kwargs) -> list[dict]:
            return [dict(row, severity="info") for row in real_audit(text, **kwargs)]

        with mock.patch.object(citations, "audit", side_effect=audit_as_info):
            result = self.run_audit()
        self.assertTrue(result["clean"])
        self.assertEqual(0, result["majors"])
        self.assertEqual(0, result["blockers"])
        report = state_io.read_json(self.work_dir / citations.CITATIONS_PATH)
        self.assertEqual([], schema.validate(report, "lint"))
        self.assertEqual(["C-07"], sorted({row["rule"] for row in report["findings"]}))
        self.assertEqual(["info"], sorted({row["severity"] for row in report["findings"]}))


if __name__ == "__main__":
    unittest.main()
