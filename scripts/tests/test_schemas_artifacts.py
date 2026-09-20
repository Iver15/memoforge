"""Tests for the artifact JSON schemas in schemas/ (TZ v2 §6).

Run from the plugin root:
    python -m unittest scripts.tests.test_schemas_artifacts

Scope: the artifact schemas owned by this slice. `state`, `events`, `done-marker`
and `internal` belong to other slices and are deliberately not asserted here.
The test loads schemas directly with jsonschema and never imports
`scripts/memoforge/schema.py`, so it stays green independently of the CLI slice.

Each schema has fixtures under `fixtures/schemas/<name>/`:
  - `valid-*.json`   must validate;
  - `invalid-1.json` must not, and carries a `_why` field describing the
    expected error. `_why` is itself rejected everywhere (every top-level
    object is a self-contained branch with `additionalProperties: false`), so
    the fixtures additionally assert that a *real* error survives removing it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = PLUGIN_ROOT / "schemas"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "schemas"

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

# Artifact -> schema rows of TZ §6 owned by this slice.
SCHEMA_NAMES = (
    "plan",
    "intake-questions",
    "mcp-probe",
    "preflight",
    "gate-answers",
    "research-findings",
    "sources",
    "quotes",
    "research-sufficiency",
    "currency",
    "source-pack",
    "review",
    "mediator",
    "client-readiness",
    "lint",
    "style-meta",
)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def schema_path(name: str) -> Path:
    return SCHEMAS_DIR / f"{name}.schema.json"


def load_schema(name: str) -> dict:
    return read_json(schema_path(name))


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


def errors_for(name: str, instance: object) -> list[str]:
    return [e.message for e in validator(name).iter_errors(instance)]


def fixture_dir(name: str) -> Path:
    return FIXTURES_DIR / name


def fixtures(name: str, prefix: str) -> list[Path]:
    return sorted(fixture_dir(name).glob(f"{prefix}*.json"))


def iter_refs(node: object):
    """Yield every $ref string in a schema document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


class SchemaFilesTest(unittest.TestCase):
    def test_every_artifact_schema_exists(self):
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                self.assertTrue(schema_path(name).is_file(), f"missing schemas/{name}.schema.json")

    def test_schemas_are_valid_draft_2020_12(self):
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(load_schema(name))

    def test_schemas_declare_schema_id_and_title(self):
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                schema = load_schema(name)
                self.assertEqual(schema.get("$schema"), DRAFT_2020_12)
                self.assertTrue(
                    str(schema.get("$id", "")).endswith(f"/{name}.schema.json"),
                    f"$id of {name} must end with /{name}.schema.json, got {schema.get('$id')!r}",
                )
                self.assertTrue(str(schema.get("title", "")).strip(), f"{name} has no title")

    def test_refs_are_local_defs_only(self):
        # CONVENTIONS.md: "$ref — только локальные $defs".
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                schema = load_schema(name)
                defs = schema.get("$defs", {})
                for ref in iter_refs(schema):
                    self.assertTrue(ref.startswith("#/$defs/"), f"{name}: non-local $ref {ref!r}")
                    key = ref[len("#/$defs/"):]
                    self.assertIn(key, defs, f"{name}: $ref {ref!r} does not resolve")

    def test_defs_are_used(self):
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                schema = load_schema(name)
                used = {ref[len("#/$defs/"):] for ref in iter_refs(schema)}
                unused = sorted(set(schema.get("$defs", {})) - used)
                self.assertEqual([], unused, f"{name}: unused $defs {unused}")


class FixtureTest(unittest.TestCase):
    def test_every_schema_has_fixtures(self):
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                self.assertTrue(fixture_dir(name).is_dir(), f"no fixture dir for {name}")
                self.assertTrue(fixtures(name, "valid-"), f"no valid fixture for {name}")
                self.assertTrue(fixtures(name, "invalid-"), f"no invalid fixture for {name}")

    def test_no_orphan_fixture_dirs(self):
        found = sorted(p.name for p in FIXTURES_DIR.iterdir() if p.is_dir())
        self.assertEqual(sorted(SCHEMA_NAMES), found)

    def test_valid_fixtures_validate(self):
        for name in SCHEMA_NAMES:
            v = validator(name)
            for path in fixtures(name, "valid-"):
                with self.subTest(schema=name, fixture=path.name):
                    errs = [f"{list(e.path)}: {e.message}" for e in v.iter_errors(read_json(path))]
                    self.assertEqual([], errs)

    def test_invalid_fixtures_are_rejected(self):
        for name in SCHEMA_NAMES:
            v = validator(name)
            for path in fixtures(name, "invalid-"):
                with self.subTest(schema=name, fixture=path.name):
                    self.assertFalse(v.is_valid(read_json(path)), f"{path.name} should not validate")

    def test_invalid_fixtures_document_the_expected_error(self):
        for name in SCHEMA_NAMES:
            for path in fixtures(name, "invalid-"):
                with self.subTest(schema=name, fixture=path.name):
                    obj = read_json(path)
                    self.assertIsInstance(obj, dict)
                    self.assertTrue(str(obj.get("_why", "")).strip(), f"{path.name} lacks a _why note")

    def test_invalid_fixtures_fail_for_more_than_the_why_field(self):
        # The documented defect must be real: stripping _why leaves the fixture invalid.
        for name in SCHEMA_NAMES:
            v = validator(name)
            for path in fixtures(name, "invalid-"):
                with self.subTest(schema=name, fixture=path.name):
                    obj = {k: val for k, val in read_json(path).items() if k != "_why"}
                    self.assertFalse(v.is_valid(obj), f"{path.name} is only invalid because of _why")

    def test_why_field_is_itself_rejected(self):
        # Every top-level object is a self-contained branch, so a comment field cannot sneak in.
        for name in SCHEMA_NAMES:
            v = validator(name)
            for path in fixtures(name, "valid-"):
                with self.subTest(schema=name, fixture=path.name):
                    obj = dict(read_json(path))
                    obj["_why"] = "comment fields are not part of the contract"
                    self.assertFalse(v.is_valid(obj), f"{path.name} + _why should not validate")


class ReviewSchemaTest(unittest.TestCase):
    def _logic_review(self) -> dict:
        return read_json(fixture_dir("review") / "valid-1.json")

    def test_review_kind_fixtures_cover_all_reviewers(self):
        seen = set()
        for path in fixtures("review", "valid-"):
            obj = read_json(path)
            seen.add((obj["reviewer"], obj.get("status")))
        self.assertEqual(
            {
                ("logic", None),
                ("form", None),
                ("citations", None),
                ("counterarguments", None),
                ("form", "failed"),
            },
            seen,
        )

    def test_every_issue_shape_accepts_issue_client(self):
        # D-173a: optional `issue_client` (1–400 chars) on every issue shape: one issue per
        # schema variant — logic, form, citations, unverified_hard_fail, counterarguments.
        base = {
            "logic": read_json(fixture_dir("review") / "valid-1.json"),
            "form": read_json(fixture_dir("review") / "valid-2.json"),
            "citations": read_json(fixture_dir("review") / "valid-3.json"),
            "counterarguments": read_json(fixture_dir("review") / "valid-4.json"),
        }
        base["counterarguments"]["issues"] = [
            {
                "severity": "blocker",
                "category": "missing_application",
                "section_id": "s-4-1",
                "issue": "The counterargument is stated but never answered.",
                "suggestion": "Answer it.",
                "attack_vector": "contrary_authority",
            }
        ]
        unverified_issue = {
            "severity": "blocker",
            "category": "unverified_hard_fail",
            "section_id": "document",
            "issue": "Hard-fail checklist item LOG-02 was graded `unknown`.",
            "suggestion": "Re-run the reviewer and grade LOG-02 as pass or fail.",
            "checklist_id": "LOG-02",
        }
        # Which `$defs` issue shape each exercised issue validates against.
        shapes = {
            "logic": "logic_issue",
            "form": "form_issue",
            "citations": "citations_issue",
            "counterarguments": "counterarguments_issue",
            "unverified_hard_fail": "unverified_hard_fail_issue",
        }
        exercised: set[str] = set()
        for kind, report in base.items():
            report["verdict"] = "needs_revision"
            report["issues"].append(dict(unverified_issue))
            with self.subTest(kind=kind):
                for issue in report["issues"]:
                    issue["issue_client"] = "Der Test wird nicht angewendet."
                self.assertEqual([], errors_for("review", report))
                report["issues"][0]["issue_client"] = "x" * 401
                self.assertTrue(errors_for("review", report))
            exercised.add(shapes[kind])
            exercised.add("unverified_hard_fail_issue")
        self.assertEqual(set(shapes.values()), exercised)

    def test_review_with_7_issues_is_valid(self):
        # §4.5: the "<=5 major" cap is prompt guidance, never maxItems in the schema.
        review = self._logic_review()
        template = review["issues"][0]
        review["issues"] = [
            {**template, "section_id": f"s-4-{n}", "issue": f"Unapplied rule in section 4.{n}.", "severity": "major"}
            for n in range(1, 8)
        ]
        self.assertEqual(7, len(review["issues"]))
        self.assertEqual([], errors_for("review", review))

    def test_review_stub_variant_valid(self):
        stub = {
            "reviewer": "citations",
            "status": "failed",
            "reason": "invalid JSON after reviewer_json_retry",
        }
        self.assertEqual([], errors_for("review", stub))

        with_sha = dict(stub, draft_sha=self._logic_review()["draft_sha"], iteration=2)
        self.assertEqual([], errors_for("review", with_sha))

        # A stub carries no checklist, no verdict and no score (the v1 overall_score:0 defect).
        self.assertTrue(errors_for("review", dict(stub, verdict="needs_revision")))
        self.assertTrue(errors_for("review", dict(stub, overall_score=0)))
        # `status` must be exactly "failed", and the stub is not a full review.
        self.assertTrue(errors_for("review", dict(stub, status="ok")))
        self.assertTrue(errors_for("review", {"reviewer": "citations", "status": "failed"}))

    def test_citations_issue_category_is_narrowed_to_three_judgement_calls(self):
        review = read_json(fixture_dir("review") / "valid-3.json")
        for category in ("source_drift", "source_pack_mismatch", "unsupported_claim"):
            with self.subTest(category=category):
                review["issues"][0]["issue_category"] = category
                self.assertEqual([], errors_for("review", review))
        # Deterministic checks (audit-citations C-01..C-07) own these; the LLM no longer labels them.
        for category in (
            "ignored_blocking_currency",
            "missing_in_sources_section",
            "unverified_against_source",
            "length_overflow_disclosure",
        ):
            with self.subTest(removed=category):
                review["issues"][0]["issue_category"] = category
                self.assertTrue(errors_for("review", review))

    def test_unverified_hard_fail_issue_is_accepted_for_every_kind(self):
        # §4.5 p.2: `unknown` on a hard_fail item makes `mf review validate` lower the verdict and
        # add this blocker. The CLI authored it, so it carries no reviewer-specific discriminator.
        added = {
            "severity": "blocker",
            "category": "unverified_hard_fail",
            "section_id": "document",
            "issue": "Hard-fail checklist item LOG-02 was graded `unknown`.",
            "suggestion": "Re-run the reviewer and grade LOG-02 as pass or fail.",
            "checklist_id": "LOG-02",
        }
        for path in fixtures("review", "valid-"):
            review = read_json(path)
            if review.get("status") == "failed":
                continue
            with self.subTest(fixture=path.name):
                review["issues"] = [added]
                review["verdict"] = "needs_revision"
                self.assertEqual([], errors_for("review", review))

        # The shape stays closed: blocker only, that one category, checklist_id required. Checked on
        # the citations branch, whose own issue variant demands an `issue_category` discriminator.
        for broken in (
            dict(added, severity="major"),
            dict(added, category="drafting"),
            {k: v for k, v in added.items() if k != "checklist_id"},
            dict(added, lens="clarity"),
        ):
            with self.subTest(broken=sorted(broken)):
                review = read_json(fixture_dir("review") / "valid-3.json")
                review["issues"] = [broken]
                self.assertTrue(errors_for("review", review))

    def test_review_branches_are_mutually_exclusive(self):
        # A form-only lens on a logic issue must match no branch of the oneOf.
        review = self._logic_review()
        review["issues"][0]["lens"] = "clarity"
        self.assertTrue(errors_for("review", review))

    def test_review_requires_draft_sha_and_reasoning(self):
        for missing in ("draft_sha", "reasoning", "checklist", "issues", "verdict"):
            with self.subTest(missing=missing):
                review = self._logic_review()
                review.pop(missing)
                self.assertTrue(errors_for("review", review))

    def test_reasoning_is_the_first_key_in_full_review_fixtures(self):
        # JSON objects are unordered, but the emitted contract puts reasoning first (§4.5).
        for path in fixtures("review", "valid-"):
            obj = read_json(path)
            if obj.get("status") == "failed":
                continue
            with self.subTest(fixture=path.name):
                self.assertEqual("reasoning", next(iter(obj)))


class ClientReadinessSchemaTest(unittest.TestCase):
    def _report(self) -> dict:
        return read_json(fixture_dir("client-readiness") / "valid-1.json")

    def test_reasoning_is_first_and_required(self):
        report = self._report()
        self.assertEqual("reasoning", next(iter(report)))
        report.pop("reasoning")
        self.assertTrue(errors_for("client-readiness", report))

    def test_checklist_is_optional_but_shaped_like_the_review_one(self):
        report = self._report()
        report.pop("checklist")
        self.assertEqual([], errors_for("client-readiness", report))

        report = self._report()
        self.assertEqual({"id", "pass", "evidence"}, set(report["checklist"][0]))
        for value in (True, False, "unknown"):
            with self.subTest(pass_value=value):
                report["checklist"][0]["pass"] = value
                self.assertEqual([], errors_for("client-readiness", report))
        report["checklist"][0]["pass"] = "maybe"
        self.assertTrue(errors_for("client-readiness", report))

    def test_every_issue_carries_a_section_id(self):
        report = self._report()
        report["issues"][0].pop("section_id")
        self.assertTrue(errors_for("client-readiness", report))

    def test_an_issue_may_carry_a_client_sentence(self):
        # D-173a: optional `issue_client` (1–400 chars).
        report = self._report()
        report["issues"][0]["issue_client"] = "Der Test wird nicht angewendet."
        self.assertEqual([], errors_for("client-readiness", report))
        report["issues"][0]["issue_client"] = "x" * 401
        self.assertTrue(errors_for("client-readiness", report))


class QuotesSchemaTest(unittest.TestCase):
    def _skip(self, **overrides) -> dict:
        skip = {
            "section_id": "s-4-2",
            "source_id": "gdpr-art-6",
            "reason": "too_long",
            "at": "2026-09-08T11:12:00Z",
        }
        skip.update(overrides)
        return {"quotes": {}, "skips": [skip]}

    def test_quotes_skip_other_requires_note(self):
        self.assertTrue(errors_for("quotes", self._skip(reason="other")))
        self.assertEqual([], errors_for("quotes", self._skip(reason="other", note="paywalled source")))
        # Every other reason stands on its own without a note.
        for reason in ("too_long", "ambiguous", "not_found", "no_raw", "raw_changed", "already_used"):
            with self.subTest(reason=reason):
                self.assertEqual([], errors_for("quotes", self._skip(reason=reason)))

    def test_quotes_skip_reason_enum_is_closed(self):
        self.assertTrue(errors_for("quotes", self._skip(reason="too-long")))


class SufficiencySchemaTest(unittest.TestCase):
    def test_user_targeted_gap_needs_a_followup_question(self):
        report = read_json(fixture_dir("research-sufficiency") / "valid-1.json")
        report["blocking_gaps"][0]["followup_question"] = None
        self.assertTrue(errors_for("research-sufficiency", report))

    def test_layer_targeted_gap_may_omit_the_followup_question(self):
        report = read_json(fixture_dir("research-sufficiency") / "valid-1.json")
        report["blocking_gaps"] = [g for g in report["blocking_gaps"] if g["target"] != "user"]
        report["overall_verdict"] = "sufficient"
        self.assertEqual([], errors_for("research-sufficiency", report))


class IntakeQuestionsSchemaTest(unittest.TestCase):
    def _question(self, **overrides) -> dict:
        question = read_json(fixture_dir("intake-questions") / "valid-1.json")["must_answer"][0]
        question.update(overrides)
        return {"must_answer": [question], "optional": [], "default_assumptions_if_skipped": []}

    def test_header_limit_is_twelve_characters(self):
        self.assertEqual([], errors_for("intake-questions", self._question(header="Train vs use")))
        self.assertTrue(errors_for("intake-questions", self._question(header="Art. 27 + DPIA")))

    def test_options_are_two_to_four(self):
        option = {"label": "Yes", "description": "One sentence."}
        self.assertTrue(errors_for("intake-questions", self._question(options=[option])))
        self.assertEqual([], errors_for("intake-questions", self._question(options=[option] * 4)))
        self.assertTrue(errors_for("intake-questions", self._question(options=[option] * 5)))

    def test_option_description_limit_is_200_characters(self):
        long_option = {"label": "Yes", "description": "x" * 201}
        self.assertTrue(errors_for("intake-questions", self._question(options=[long_option, long_option])))

    def test_impact_default_and_confidence_are_required(self):
        """D-05/D-15: `default_if_wrong` feeds the intake gate line, so it is required too."""
        for field in ("impact", "default", "default_if_wrong", "confidence"):
            with self.subTest(field=field):
                payload = self._question()
                payload["must_answer"][0].pop(field)
                self.assertTrue(errors_for("intake-questions", payload))


class ResearchFindingsSchemaTest(unittest.TestCase):
    def _findings(self) -> dict:
        return read_json(fixture_dir("research-findings") / "valid-1.json")

    def test_quote_short_caps_at_fifteen_words(self):
        payload = self._findings()
        finding = payload["issues"][0]["findings"][0]
        finding["quote_short"] = " ".join(f"w{n}" for n in range(15))
        self.assertEqual([], errors_for("research-findings", payload))
        finding["quote_short"] = " ".join(f"w{n}" for n in range(16))
        self.assertTrue(errors_for("research-findings", payload))

    def test_excluded_issue_id_may_be_null(self):
        """D-114: an exclusion that maps to no planned issue carries `issue_id: null`."""
        payload = self._findings()
        payload["considered_excluded"][0]["issue_id"] = None
        self.assertEqual([], errors_for("research-findings", payload))

    def test_excluded_entry_still_needs_its_title(self):
        payload = self._findings()
        payload["considered_excluded"][0].pop("title")
        self.assertTrue(errors_for("research-findings", payload))

    def test_meta_identity_is_required(self):
        payload = self._findings()
        payload.pop("_meta")
        self.assertTrue(errors_for("research-findings", payload))
        for field in ("task_id", "step_id", "attempt", "slot"):
            with self.subTest(field=field):
                payload = self._findings()
                payload["_meta"].pop(field)
                self.assertTrue(errors_for("research-findings", payload))


class SourcesSchemaTest(unittest.TestCase):
    def _sources(self) -> dict:
        return read_json(fixture_dir("sources") / "valid-1.json")

    def test_registry_fields_of_tz_5_3_are_enforced(self):
        payload = self._sources()
        record = payload["sources"]["gdpr-art-6"]
        for field in ("provenance", "liveness", "verification", "tier", "raw_sha256"):
            with self.subTest(field=field):
                broken = self._sources()
                broken["sources"]["gdpr-art-6"].pop(field)
                self.assertTrue(errors_for("sources", broken))
        self.assertEqual(
            {"us", "us_by", "eu_syntax_ok", "checked_at"},
            set(record["verification"]),
        )

    def test_retrieved_from_is_an_optional_string_of_the_record(self):
        """D-192: the endpoint address the public url rule kept out of `url`."""
        payload = self._sources()
        payload["sources"]["gdpr-art-6"]["retrieved_from"] = "https://mcp.casus.legal/case/34232"
        self.assertEqual([], errors_for("sources", payload))

        payload = self._sources()
        payload["sources"]["gdpr-art-6"]["retrieved_from"] = 7
        self.assertTrue(errors_for("sources", payload))

    def test_the_pack_entry_carries_retrieved_from_too(self):
        payload = read_json(fixture_dir("source-pack") / "valid-1.json")
        payload["entries"][0]["retrieved_from"] = "https://mcp.casus.legal/case/34232"
        self.assertEqual([], errors_for("source-pack", payload))

        payload = read_json(fixture_dir("source-pack") / "valid-1.json")
        payload["entries"][0]["retrieved_from"] = None
        self.assertTrue(errors_for("source-pack", payload))

    def test_tool_meta_stays_open_but_pack_is_closed(self):
        payload = self._sources()
        payload["sources"]["gdpr-art-6"]["meta"]["repealed_by"] = "CELEX:32024R1689"
        self.assertEqual([], errors_for("sources", payload))

        payload = self._sources()
        payload["sources"]["gdpr-art-6"]["pack"]["score"] = 7
        self.assertTrue(errors_for("sources", payload))


class McpProbeRuServersTest(unittest.TestCase):
    """D-184: the RU servers are keys of `mcp-probe` — a document carrying them validates."""

    def test_a_probe_document_carrying_casus_and_fas_validates(self):
        document = {
            "namespaces": {
                "casus": "mcp__claude_ai_CasusLegal",
                "fas": "mcp__plugin_memoforge_fas-search",
                "other": [],
            },
            "status": {"casus": "ok", "fas": "ok"},
            "probed_at": "2026-09-18T10:00:00Z",
        }
        self.assertEqual([], errors_for("mcp-probe", document))


if __name__ == "__main__":
    unittest.main()
