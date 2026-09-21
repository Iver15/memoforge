"""Tests for scripts/memoforge/routing.py — the static routing table and the MCP estimate (ТЗ §4.3)."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, limits, routing, schema, sources  # noqa: E402


def note_commands(note: str) -> list[argparse.Namespace]:
    """Every `` `mf …` `` command of a routing note, split as a shell would and parsed by the real CLI.

    A note that names a flag the command does not have, or leaves out a required one, fails here —
    the words are never read for themselves (D-151, D-205).
    """
    parsed = []
    for printed in re.findall(r"`(mf [^`]+)`", note):
        tokens = shlex.split(printed)
        try:
            parsed.append(cli.build_parser().parse_args(tokens[1:]))
        except SystemExit:  # argparse reports a bad command line by exiting
            raise AssertionError(f"the note prints a command the CLI refuses: {printed}") from None
    return parsed


class JurisdictionTest(unittest.TestCase):
    def test_gb_normalises_to_uk(self):
        # analysis/05 §5: discover_sources("GB") answers with an empty list, the code is UK.
        self.assertEqual("UK", routing.normalize_jurisdiction("GB"))
        self.assertEqual("UK", routing.normalize_jurisdiction("gb"))
        self.assertEqual("UK", routing.normalize_jurisdiction("United Kingdom"))

    def test_known_codes_pass_through_upper_cased(self):
        self.assertEqual("EU", routing.normalize_jurisdiction("eu"))
        self.assertEqual("US", routing.normalize_jurisdiction("USA"))
        self.assertEqual("CY", routing.normalize_jurisdiction(" cy "))

    def test_empty_jurisdiction_is_empty(self):
        self.assertEqual("", routing.normalize_jurisdiction(None))
        self.assertEqual("", routing.normalize_jurisdiction(""))

    def test_a_collective_spelling_of_the_member_states_resolves_to_eu(self):
        # D34-02: the plan of the 20260910 run said "EEA member states" and got the empty default.
        for spelling in ("EEA member states", "EEA_member_states", "eea states", "Member States"):
            with self.subTest(spelling=spelling):
                self.assertEqual("EU", routing.normalize_jurisdiction(spelling))


class MemberStateTest(unittest.TestCase):
    """D34-02: the national statute book behind the EU row, and the group spellings above it."""

    def test_a_collective_spelling_is_flagged_as_generic_and_still_routed(self):
        row = routing.route("statutes", "EEA member states")
        self.assertEqual("EU", row["jurisdiction"])
        self.assertTrue(row["known"])
        self.assertTrue(row["member_state_generic"])
        self.assertEqual(["publications.europa.eu", "eur-lex.europa.eu"], row["domains"])

    def test_a_concrete_code_is_not_generic(self):
        for code in ("EU", "DE", "ZZ"):
            with self.subTest(jurisdiction=code):
                self.assertFalse(routing.route("statutes", code)["member_state_generic"])

    def test_every_member_state_has_a_statute_portal_and_a_supervisory_authority(self):
        for code in routing.MEMBER_STATES:
            with self.subTest(jurisdiction=code):
                statutes = routing.route("statutes", code)
                self.assertTrue(statutes["known"])
                self.assertTrue(statutes["domains"])
                doctrine = routing.route("doctrine", code)
                self.assertTrue(doctrine["known"])
                self.assertTrue(doctrine["domains"])

    def test_the_portals_are_the_official_statute_books(self):
        expected = {
            "DE": "gesetze-im-internet.de",
            # D-148: legifrance.gouv.fr is Cloudflare-blocked, so the fetchable French portal is
            # code.travail.gouv.fr; Légifrance stays behind it as the registered address.
            "FR": "code.travail.gouv.fr",
            "IE": "irishstatutebook.ie",
            "NL": "wetten.overheid.nl",
            "ES": "boe.es",
            "IT": "normattiva.it",
            "AT": "data.bka.gv.at",
        }
        self.assertEqual(sorted(expected), sorted(routing.MEMBER_STATES))
        for code, domain in expected.items():
            with self.subTest(jurisdiction=code):
                self.assertEqual(domain, routing.route("statutes", code)["domains"][0])

    def test_german_statutes_name_the_english_translations_and_the_mirrors(self):
        row = routing.route("statutes", "DE")
        self.assertIn("/englisch_", row["note"])
        self.assertEqual(["gesetze-im-internet.de", "dejure.org", "buzer.de"], row["domains"])

    def test_german_statutes_say_the_english_page_has_no_pinpoint_and_ldh_no_bdsg(self):
        # analysis/38 §4.1: /englisch_bdsg is one 194 KB page with `name="p0000"` anchors only,
        # and `ldh_search` returned five hits for the BDSG with the act itself among none of them.
        note = routing.route("statutes", "DE")["note"]
        self.assertIn("no per-section anchors", note)
        self.assertIn("/bdsg_2018/__<N>.html", note)
        self.assertIn("ldh_search does not index the BDSG", note)

    def test_austrian_statutes_route_through_the_open_data_api_not_the_blocked_portal(self):
        # analysis/38 §4.3: www.ris.bka.gv.at answers 503 to a bot check on every path.
        row = routing.route("statutes", "AT")
        self.assertEqual(["data.bka.gv.at", "ris.bka.gv.at"], row["domains"])
        self.assertIn("data.bka.gv.at/ris/api/v2.6/Bundesrecht", row["note"])
        self.assertIn("ogd.ris.bka.gv.at/Dokumente/Bundesnormen/<NOR>/<NOR>.html", row["note"])
        self.assertIn("503", row["note"])

    def test_french_statutes_route_through_justicelibre_not_the_blocked_portal(self):
        # analysis/39 §2.1: get_law_article(code="CT", num="L1121-1") answers with the article text.
        row = routing.route("statutes", "FR")
        self.assertEqual(
            [
                "justicelibre_get_law_article",
                "justicelibre_resolve_law_number",
                "ldh_resolve_reference",
                "WebFetch",
            ],
            row["tools"],
        )
        self.assertEqual(["code.travail.gouv.fr", "legifrance.gouv.fr"], row["domains"])
        note = row["note"]
        self.assertIn('get_law_article(code="CT", num="L1121-1")', note)
        self.assertIn("79 abbreviations", note)
        self.assertIn("source_url", note)
        self.assertIn("Cloudflare", note)
        self.assertIn("code.travail.gouv.fr/code-du-travail/<article>", note)

    def test_french_statutes_name_the_code_base_not_the_case_law_base_as_the_reserve(self):
        # analysis/39 §9.2: FR/legifrance is 882 decisions; FR/LegifranceCodes is 229 364 articles.
        note = routing.route("statutes", "FR")["note"]
        self.assertIn("FR/LegifranceCodes", note)
        self.assertIn("229 364", note)
        self.assertNotIn("considered_excluded", note)
        self.assertIn("ldh_search is not a search engine", note)

    def test_french_case_law_is_the_one_member_state_court_row(self):
        # analysis/39 §2.1: justicelibre covers the judicial, administrative and constitutional orders.
        row = routing.route("case_law", "FR")
        self.assertTrue(row["known"])
        self.assertEqual(
            [
                "justicelibre_search_judiciaire",
                "justicelibre_get_decision_judiciaire",
                "justicelibre_search_conseil_etat",
                "justicelibre_get_ce_decision",
                "justicelibre_search_cc",
            ],
            row["tools"],
        )
        self.assertEqual(["courdecassation.fr"], row["domains"])
        self.assertIn("255-byte JavaScript redirect shell", row["note"])
        self.assertIn("is not a --url", row["note"])

    def test_french_doctrine_reads_the_cnil_through_justicelibre(self):
        row = routing.route("doctrine", "FR")
        self.assertEqual(
            ["justicelibre_search_cnil", "justicelibre_search_doctrine", "WebSearch", "WebFetch"],
            row["tools"],
        )
        self.assertEqual(["cnil.fr"], row["domains"])

    def test_italian_statutes_reach_the_article_through_the_open_data_api(self):
        # analysis/39 §8.5: Normattiva has published an OpenAPI without authorisation since 2025.
        note = routing.route("statutes", "IT")["note"]
        self.assertIn("uri-res/N2Ls?urn:nir:", note)
        self.assertIn("500", note)
        self.assertIn("api.normattiva.it/t/normattiva.api/bff-opendata/v1/api/v1", note)
        self.assertIn("POST /atto/dettaglio-atto-urn", note)
        self.assertIn("~artN!vig=YYYY-MM-DD", note)
        self.assertIn("CC BY 4.0", note)
        self.assertNotIn("not reachable statelessly", note)
        # D-205: the article is the source, so it is saved by code, not fetched and registered.
        self.assertIn("mf sources save", note)
        self.assertNotIn("mf sources fetch", note)

    def test_the_italian_note_shows_the_command_that_executes_the_post(self):
        """D-151, D-205: the POST the note prescribes is a command the real CLI accepts.

        The command is not read for its words: it is split and handed to the real CLI parser, so a
        note that names a flag the command does not have fails here. Since D-205 it is `save`, which
        shares `--method POST --json` with `fetch` (D-199), because the article it reads is the source.
        """
        posts = [args for args in note_commands(routing.route("statutes", "IT")["note"]) if args.method == "POST"]
        self.assertEqual(1, len(posts))
        args = posts[0]
        self.assertIs(sources.run_save, args.func)
        self.assertEqual("statutes", args.layer)
        self.assertTrue(args.url.startswith("https://api.normattiva.it/"), args.url)
        self.assertIn("/atto/dettaglio-atto-urn", args.url)
        self.assertEqual(
            {"urn": "urn:nir:stato:decreto.legislativo:2003-06-30;196~art7!vig=2026-01-01"},
            json.loads(args.json_body),
        )
        # Final review A: the endpoint names no article, so the command names the public page of the
        # one it asks for — built from the very URN of its body — and the save accepts it as that.
        urn = json.loads(args.json_body)["urn"]
        self.assertEqual("https://www.normattiva.it/uri-res/N2Ls?" + urn, args.public_url)
        self.assertIsNone(sources.public_url_refusal(args.method, args.public_url))

    def test_the_official_api_notes_save_the_text_with_the_header_options(self):
        """D-148 / D-205: WebFetch takes no headers, and the text those channels serve is a source.

        So it is saved by `mf sources save` with the transport options it shares with `fetch`; `fetch`
        stays only where a body is read and never registered — the NL manifest and SRU search.
        """
        for code in ("IT", "ES", "NL"):
            with self.subTest(jurisdiction=code):
                self.assertIn("mf sources save", routing.route("statutes", code)["note"])
        self.assertIn("--accept application/xml", routing.route("statutes", "ES")["note"])
        nl = routing.route("statutes", "NL")["note"]
        self.assertIn("--accept application/xml", nl)
        self.assertIn("only read, through mf sources fetch (D-149)", nl)

    def test_no_note_fetches_a_source_s_text_and_registers_it_itself(self):
        """D-205: an agent that sees two rules follows the older one, so the old one is nowhere.

        `mf sources fetch` survives in exactly two notes, and in both only for a body that is read and
        never registered: the Federal Register search API (US) and the BWB manifest with its SRU
        search (NL). No note registers a text under `--tool WebFetch` or `mf-fetch`.
        """
        readers = set()
        for layer in routing.LAYERS:
            for code in list(routing.ROUTING[layer]) + list(routing.MEMBER_STATES) + ["CH", "RU"]:
                note = routing.route(layer, code)["note"]
                with self.subTest(layer=layer, jurisdiction=code):
                    self.assertNotIn("--tool WebFetch", note)
                    self.assertNotIn("mf-fetch", note)
                    if "mf sources fetch" in note:
                        readers.add((layer, code))
        self.assertEqual({("statutes", "US"), ("statutes", "NL")}, readers)

    def test_every_command_line_of_every_note_parses(self):
        """D-205: every `` `mf …` `` a note prints is a command the real CLI accepts."""
        printed = 0
        for layer in routing.LAYERS:
            for code in list(routing.ROUTING[layer]) + list(routing.MEMBER_STATES) + ["CH", "RU"]:
                with self.subTest(layer=layer, jurisdiction=code):
                    printed += len(note_commands(routing.route(layer, code)["note"]))
        self.assertGreaterEqual(printed, 6, "IT's POST, the RU statute, three RU case-law saves, the LDH line")

    def test_spanish_statutes_name_the_boe_block_endpoint_and_its_accept_header(self):
        note = routing.route("statutes", "ES")["note"]
        self.assertIn(
            "boe.es/datosabiertos/api/legislacion-consolidada/id/{id}/texto/bloque/{aN}", note
        )
        self.assertIn("Accept: application/xml", note)

    def test_dutch_statutes_drop_the_dead_xml_php_for_the_repository_manifest(self):
        note = routing.route("statutes", "NL")["note"]
        self.assertIn("wetten.overheid.nl/xml.php no longer exists", note)
        self.assertIn("repository.officiele-overheidspublicaties.nl/bwb/{BWBID}/_manifest.xml", note)
        self.assertIn("_latestItem", note)

    def test_german_statutes_name_the_bulk_index_and_the_neuris_caveat(self):
        note = routing.route("statutes", "DE")["note"]
        self.assertIn("gesetze-im-internet.de/gii-toc.xml", note)
        self.assertIn("<act>/xml.zip", note)
        self.assertIn("<norm>", note)
        self.assertIn("NeuRIS", note)
        self.assertIn("not consolidated law", note)

    def test_irish_statutes_say_the_per_section_xml_is_404_and_the_html_is_not(self):
        note = routing.route("statutes", "IE")["note"]
        self.assertIn("404", note)
        self.assertIn("/enacted/en/html", note)
        self.assertIn("CC BY 4.0", note)

    def test_austrian_statutes_carry_the_ogd_usage_conditions(self):
        # analysis/39 §8.5: breaching them gets the IP blocked, and the note has to say so.
        note = routing.route("statutes", "AT")["note"]
        self.assertIn("0.5 requests per second", note)
        self.assertIn("no parallel connections", note)
        self.assertIn("20:00 and 05:00", note)
        self.assertIn("ris.it@bka.gv.at", note)

    def test_a_member_state_starts_at_its_portal_because_legalviz_is_eu_only(self):
        for code in routing.MEMBER_STATES:
            if code == "FR":
                continue  # D-148: France has a bundled server of its own.
            with self.subTest(jurisdiction=code):
                self.assertEqual(["WebFetch", "ldh_search"], routing.route("statutes", code)["tools"])

    def test_member_state_doctrine_points_at_the_national_authority(self):
        self.assertEqual(["cnil.fr"], routing.route("doctrine", "FR")["domains"])
        self.assertEqual(["dpc.ie"], routing.route("doctrine", "IE")["domains"])
        self.assertIn("Article 35(4)", routing.route("doctrine", "AT")["note"])

    def test_member_state_case_law_stays_off_table(self):
        # No national court row: `known: False` is what tells the researcher to find the portal.
        # France is the exception D-148 makes, because a bundled server covers its three orders.
        row = routing.route("case_law", "DE")
        self.assertFalse(row["known"])
        self.assertEqual([], row["domains"])
        self.assertEqual(["FR"], sorted(routing.MEMBER_STATE_ROWS["case_law"]))

    def test_an_unknown_jurisdiction_is_the_only_row_without_a_preferred_domain(self):
        # The researcher prompt reads "no preferred domain" as "off-table"; keep the two in step.
        for layer in routing.LAYERS:
            for code in ("EU", "UK", "US", "CH", "RU") + routing.MEMBER_STATES + ("ZZ", "PL"):
                with self.subTest(layer=layer, jurisdiction=code):
                    row = routing.route(layer, code)
                    self.assertEqual(row["known"], bool(row["domains"]))

    @staticmethod
    def allowlisted_hosts() -> set:
        allowed = set()
        raw = (PLUGIN_ROOT / "hooks" / "allowlist.txt").read_text(encoding="utf-8-sig")
        for line in raw.split("\n"):
            host = line.split("#", 1)[0].strip().lower()
            if host:
                allowed.add(host)
        return allowed

    def test_every_routed_domain_is_auto_allowed_by_the_fetch_gate(self):
        allowed = self.allowlisted_hosts()
        for layer in routing.LAYERS:
            for code in ("EU", "UK", "US") + routing.MEMBER_STATES:
                for domain in routing.route(layer, code)["domains"]:
                    with self.subTest(layer=layer, jurisdiction=code, domain=domain):
                        suffixes = [host for host in allowed if domain == host or domain.endswith("." + host)]
                        self.assertTrue(suffixes, f"{domain} is not in hooks/allowlist.txt")

    def test_the_two_hosts_the_sweep_added_to_the_table_are_covered_by_the_allowlist(self):
        # D-145: `data.bka.gv.at` is a line of its own, Cellar rides the `europa.eu` suffix rule.
        allowed = self.allowlisted_hosts()
        self.assertIn("data.bka.gv.at", allowed)
        self.assertNotIn("publications.europa.eu", allowed)
        self.assertIn("europa.eu", allowed)

    def test_the_hosts_the_catalogue_added_to_the_table_are_covered_by_the_allowlist(self):
        # D-148: the French keyless reader, the Cour de cassation and the two Swiss hosts.
        allowed = self.allowlisted_hosts()
        for host in ("code.travail.gouv.fr", "courdecassation.fr", "fedlex.admin.ch", "bger.ch"):
            with self.subTest(host=host):
                self.assertIn(host, allowed)


class ExtraJurisdictionTest(unittest.TestCase):
    """D-148: Switzerland is not an EU member state, so it sits beside `MEMBER_STATE_ROWS`."""

    def test_switzerland_is_not_smuggled_into_the_member_states(self):
        self.assertNotIn("CH", routing.MEMBER_STATES)
        self.assertEqual(["CH", "RU"], sorted(routing.EXTRA_JURISDICTION_ROWS["statutes"]))

    def test_every_layer_of_the_extra_table_is_a_known_row(self):
        for layer in routing.LAYERS:
            with self.subTest(layer=layer):
                row = routing.route(layer, "CH")
                self.assertTrue(row["known"])
                self.assertTrue(row["domains"])
                self.assertTrue(row["note"])

    def test_swiss_statutes_call_get_law_by_sr_number_and_article(self):
        # analysis/39 §2.2: get_law{"sr_number":"220","article":"328b"} returned Art. 328b OR.
        row = routing.route("statutes", "CH")
        self.assertEqual(
            [
                "opencaselaw_get_law",
                "opencaselaw_search_laws",
                "opencaselaw_get_article_history",
                "WebFetch",
            ],
            row["tools"],
        )
        self.assertEqual(["fedlex.admin.ch"], row["domains"])
        self.assertIn('get_law(sr_number="220", article="328b")', row["note"])

    def test_the_swiss_statute_note_makes_the_pending_consolidations_the_currency_check(self):
        note = routing.route("statutes", "CH")["note"]
        self.assertIn("consolidation date", note)
        self.assertIn("enters into force", note)
        self.assertIn("currency check", note)

    def test_swiss_case_law_names_the_four_tools_and_leaves_entscheidsuche_out(self):
        row = routing.route("case_law", "CH")
        self.assertEqual(
            [
                "opencaselaw_search_decisions",
                "opencaselaw_get_decision",
                "opencaselaw_get_regeste",
                "opencaselaw_find_leading_cases",
                "WebFetch",
            ],
            row["tools"],
        )
        self.assertEqual(["bger.ch"], row["domains"])
        self.assertIn("entscheidsuche.ch", row["note"])
        self.assertIn("not bundled", row["note"])

    def test_swiss_doctrine_reads_the_commentaries(self):
        row = routing.route("doctrine", "CH")
        self.assertEqual(
            [
                "opencaselaw_get_doctrine",
                "opencaselaw_search_commentaries",
                "WebSearch",
                "WebFetch",
            ],
            row["tools"],
        )
        self.assertEqual(["edoeb.admin.ch"], row["domains"])

    def test_the_swiss_rows_never_reach_the_intake_digest(self):
        # D-110: the digest is EU/UK/US only, whatever `route()` merges in behind it.
        self.assertNotIn("CH ", routing.routing_digest({"opencaselaw": "x"}))
        self.assertNotIn("opencaselaw", routing.routing_digest({"opencaselaw": "x"}))

    def test_every_routed_domain_of_the_extra_table_is_auto_allowed(self):
        allowed = MemberStateTest.allowlisted_hosts()
        for layer in routing.LAYERS:
            for domain in routing.route(layer, "CH")["domains"]:
                with self.subTest(layer=layer, domain=domain):
                    suffixes = [
                        host for host in allowed if domain == host or domain.endswith("." + host)
                    ]
                    self.assertTrue(suffixes, f"{domain} is not in hooks/allowlist.txt")


class RuJurisdictionTest(unittest.TestCase):
    """D-185: Russia is not an EU member state, so it sits beside `MEMBER_STATE_ROWS` like CH."""

    def test_russia_is_not_smuggled_into_the_member_states(self):
        self.assertNotIn("RU", routing.MEMBER_STATES)
        self.assertEqual(["CH", "RU"], sorted(routing.EXTRA_JURISDICTION_ROWS["statutes"]))

    def test_russian_spellings_normalise_to_ru(self):
        for spelling in ("RUS", "RF", "RUSSIA", "ru"):
            with self.subTest(spelling=spelling):
                self.assertEqual("RU", routing.normalize_jurisdiction(spelling))

    def test_every_layer_of_the_russian_table_is_a_known_row(self):
        for layer in routing.LAYERS:
            with self.subTest(layer=layer):
                row = routing.route(layer, "RU")
                self.assertTrue(row["known"])
                self.assertTrue(row["domains"])
                self.assertTrue(row["note"])

    def test_russian_statutes_start_at_the_ldh_pravogovru_corpus(self):
        row = routing.route("statutes", "RU")
        self.assertEqual(["ldh_search", "WebFetch"], row["tools"])
        self.assertEqual(["RU/PravoGovRu"], row["ldh_sources"])
        self.assertEqual(["www.consultant.ru", "base.garant.ru"], row["domains"])
        self.assertIn("RU/PravoGovRu", row["note"])
        # D-205: the article is saved by code; «Register with --raw-file» is gone, not softened.
        self.assertIn("mf sources save", row["note"])
        self.assertNotIn("Register with --raw-file", row["note"])

    def test_the_russian_statute_note_names_the_free_sections_and_the_pinpoint_form(self):
        note = routing.route("statutes", "RU")["note"]
        self.assertIn("www.consultant.ru/document/cons_doc_LAW_", note)
        self.assertIn("base.garant.ru", note)
        self.assertIn("pravo.gov.ru", note)
        self.assertIn("п. 2 ст. 152", note)
        self.assertIn("Гражданский кодекс РФ (часть первая), ст. 152", note)

    def test_the_russian_statute_note_covers_a_contract_or_an_offer_the_client_supplied(self):
        # D-196: its own clause numbers and section headings, headings never translated.
        note = routing.route("statutes", "RU")["note"]
        self.assertIn("п. 3 разд. «Возмещение»", note)
        self.assertIn("разд", note)

    def test_russian_case_law_names_the_casus_and_fas_tools_first(self):
        row = routing.route("case_law", "RU")
        self.assertEqual(
            [
                "casus_casuslegal_search_practice",
                "casus_casuslegal_find_term",
                "casus_casuslegal_get_case_details",
                "casus_casuslegal_browse_practice",
                "fas_search_fas_cases",
                "fas_get_case_details",
                "fas_get_filter_options",
                "ldh_search",
                "WebFetch",
            ],
            row["tools"],
        )
        self.assertEqual(["RU/Sudact"], row["ldh_sources"])
        self.assertEqual(["sudact.ru"], row["domains"])
        note = row["note"]
        self.assertIn("RU/Sudact", note)

    def test_the_case_law_note_forbids_registering_the_endpoint_address(self):
        """D-192: what a Casus or FAS tool answers with is an endpoint, not a page of the act."""
        note = routing.route("case_law", "RU")["note"]
        self.assertIn("endpoint address, not a page", note)
        self.assertIn("do not pass it as `--url`", note)
        self.assertIn("vsrf.ru", note)

    @staticmethod
    def commands(note: str) -> list[argparse.Namespace]:
        return note_commands(note)

    def test_every_command_line_of_the_two_russian_notes_parses(self):
        """D-205: the notes show commands, and every one of them is a command `mf` accepts."""
        for layer in ("statutes", "case_law"):
            with self.subTest(layer=layer):
                self.assertTrue(self.commands(routing.route(layer, "RU")["note"]), layer)

    def test_the_statute_note_saves_one_article_of_consultant_with_its_edition(self):
        """D-205: the article page → `save --url … --expect-article <N>`, the edition line in `--meta`."""
        saves = [
            args
            for args in self.commands(routing.route("statutes", "RU")["note"])
            if args.func is sources.run_save
        ]
        self.assertEqual(1, len(saves))
        args = saves[0]
        self.assertEqual("statutes", args.layer)
        self.assertTrue(args.url.startswith("https://www.consultant.ru/document/cons_doc_LAW_"), args.url)
        self.assertEqual("152", args.expect_article)
        self.assertIn("edition", json.loads(args.meta))

    def test_the_case_law_note_names_both_resolvers_and_the_plenum_url(self):
        """D-205: `save --resolve vsrf`, `save --resolve sudact`, and `save --url` for a Plenum act."""
        commands = self.commands(routing.route("case_law", "RU")["note"])
        saves = {args.resolve or "url": args for args in commands if args.func is sources.run_save}
        self.assertEqual({"vsrf", "sudact", "url"}, set(saves))
        for name, args in saves.items():
            with self.subTest(route=name):
                self.assertEqual("case_law", args.layer)
                self.assertTrue(args.expect_number, "a Russian act is certified by its number …")
                self.assertRegex(args.expect_date, r"^\d{4}-\d{2}-\d{2}$", "… and by its own date")
        # The ruling of the addendum: the number goes in whole, its bracketed suffix included.
        self.assertEqual("305-ЭС24-8702 (1,3)", saves["vsrf"].expect_number)
        self.assertTrue(saves["url"].url.startswith("https://base.garant.ru/"), saves["url"].url)

    def test_the_case_law_note_registers_ldh_as_an_excerpt_once_sudact_is_closed(self):
        """Addendum §9: after a captcha, LDH's sudact.ru address is not fetched; its answer is kept."""
        note = routing.route("case_law", "RU")["note"]
        registers = [args for args in self.commands(note) if args.func is sources.run_register]
        self.assertEqual(1, len(registers))
        self.assertEqual("excerpt", registers[0].raw_kind)
        self.assertTrue(registers[0].raw_file)
        self.assertIn("channel_unavailable: captcha", note)
        self.assertIn("closed for the run", note)
        # Final review C: a spent budget closes the host for every command too — `save --url` counts.
        self.assertIn("`channel_budget_spent`", note)
        self.assertIn("every request to sudact.ru counts against its one budget", note)

    def test_the_russian_notes_pass_the_number_whole_and_the_act_s_own_date(self):
        note = routing.route("case_law", "RU")["note"]
        self.assertIn("suffix included", note)
        self.assertIn("a day early", note)

    def test_every_russian_note_sends_a_moved_tool_name_to_the_host_tool_list(self):
        """D-187a: the probe records namespaces and status, never tool names — so it is not the
        fallback for a name that differs from the vendor's documentation."""
        for layer in routing.LAYERS:
            note = routing.route(layer, "RU")["note"]
            with self.subTest(layer=layer):
                self.assertIn("the exact names are the tools the host exposes under that", note)
                self.assertIn("match by tool suffix", note)
                self.assertIn("records the namespaces, not the tool names", note)
                self.assertNotIn("Exact tool names are in intake/mcp-probe.json", note)

    def test_russian_doctrine_reads_the_commentary_outlets(self):
        row = routing.route("doctrine", "RU")
        self.assertEqual(["WebSearch", "WebFetch"], row["tools"])
        self.assertEqual([], row["ldh_sources"])
        self.assertEqual(["zakon.ru", "cyberleninka.ru"], row["domains"])

    def test_the_russian_rows_never_reach_the_intake_digest(self):
        # D-110: the digest is EU/UK/US only, whatever `route()` merges in behind it.
        self.assertNotIn("RU ", routing.routing_digest({"casus": "x", "fas": "y", "ldh": "z"}))
        self.assertNotIn("casus", routing.routing_digest({"casus": "x", "fas": "y", "ldh": "z"}))

    def test_every_routed_domain_of_the_russian_table_is_auto_allowed(self):
        allowed = MemberStateTest.allowlisted_hosts()
        for layer in routing.LAYERS:
            for domain in routing.route(layer, "RU")["domains"]:
                with self.subTest(layer=layer, domain=domain):
                    suffixes = [
                        host for host in allowed if domain == host or domain.endswith("." + host)
                    ]
                    self.assertTrue(suffixes, f"{domain} is not in hooks/allowlist.txt")


class RouteTest(unittest.TestCase):
    def test_eu_statutes_resolve_reference_first_then_eur_lex(self):
        # D-105: LegalViz resolves the CELEX id and slices the act; LDH and EUR-Lex stay behind it.
        row = routing.route("statutes", "EU")
        self.assertEqual(["legalviz_resolve", "legalviz_get_law_part"], row["tools"][:2])
        self.assertEqual(
            ["ldh_resolve_reference", "ldh_search", "WebFetch"], row["tools"][2:]
        )
        self.assertEqual(["publications.europa.eu", "eur-lex.europa.eu"], row["domains"])
        self.assertIn("EU/ConsolidatedLegislation", row["ldh_sources"])

    def test_eu_statutes_take_a_slice_never_the_whole_act(self):
        note = routing.route("statutes", "EU")["note"]
        self.assertIn("part=structure", note)
        self.assertIn("version=", note)
        self.assertIn("never pull a whole act", note)

    def test_eu_statutes_carry_the_cellar_call_contract_and_the_waf_escape(self):
        # D-145 / analysis/38 §1.5: Cellar is the one EU host that never went into a WAF window,
        # and it only answers with the document for exactly `Accept: application/xhtml+xml`.
        row = routing.route("statutes", "EU")
        self.assertEqual("publications.europa.eu", row["domains"][0])
        note = row["note"]
        self.assertIn("https://publications.europa.eu/resource/celex/<CELEX>", note)
        self.assertIn("Accept: application/xhtml+xml", note)
        self.assertIn("Accept-Language: eng", note)
        self.assertIn("60 MB", note)
        self.assertIn("404", note)
        self.assertIn("x-amzn-waf-action: challenge", note)
        self.assertIn("retrying is useless", note)

    def test_eu_statutes_name_the_consolidated_celex_as_the_pinpoint_form(self):
        # analysis/38 §1.5: `id="art_N"` exists only on `0YYYYRNNNN-YYYYMMDD`, not on the OJ form.
        note = routing.route("statutes", "EU")["note"]
        self.assertIn('id="art_N"', note)
        self.assertIn("0YYYYRNNNN-YYYYMMDD", note)
        self.assertIn("versionCelex", note)
        self.assertIn("02024R1689-20260727", note)
        self.assertIn("32026R1744", note)

    def test_eu_case_law_finds_the_cjeu_judgments_through_legalviz(self):
        # D-105: the two LegalViz tools name the interpreting judgments; the text comes from CURIA.
        row = routing.route("case_law", "EU")
        self.assertEqual(
            ["legalviz_get_case_law", "legalviz_get_citing_provisions"], row["tools"][:2]
        )
        # D-148: justicelibre is the alternative text source, behind LegalViz and before LDH.
        self.assertEqual(
            ["justicelibre_search_cjue", "justicelibre_get_decision_cjue"], row["tools"][2:4]
        )
        self.assertEqual(["ldh_resolve_reference", "ldh_search", "WebFetch"], row["tools"][4:])
        self.assertIn("EU/CURIA", row["ldh_sources"])
        self.assertEqual(["publications.europa.eu", "eur-lex.europa.eu"], row["domains"])

    def test_eu_case_law_note_says_legalviz_answers_with_metadata_not_judgment_text(self):
        note = routing.route("case_law", "EU")["note"]
        self.assertIn("not with the judgment text", note)

    def test_eu_case_law_registers_a_judgment_at_its_celex_address_on_either_host(self):
        # D34-14: all eight CJEU sources of the 20260910 run were registered on juris/liste.jsf.
        row = routing.route("case_law", "EU")
        self.assertEqual("publications.europa.eu", row["domains"][0])
        self.assertIn("6<year>CJ<number>", row["note"])
        self.assertIn("https://publications.europa.eu/resource/celex/62021CJ0252", row["note"])
        self.assertIn("uri=CELEX:62021CJ0252", row["note"])

    def test_curia_is_gone_from_the_eu_case_law_row_and_from_every_other_row(self):
        # D-145 / analysis/38 §2: every curia path answers with one byte-identical 130 KB shell.
        row = routing.route("case_law", "EU")
        self.assertNotIn("curia.europa.eu", row["domains"])
        self.assertIn("off the table", row["note"])
        self.assertIn("JavaScript shell", row["note"])
        for layer in routing.LAYERS:
            for code in ("EU", "UK", "US") + routing.MEMBER_STATES:
                with self.subTest(layer=layer, jurisdiction=code):
                    self.assertNotIn("curia.europa.eu", routing.route(layer, code)["domains"])

    def test_the_eu_case_law_note_no_longer_claims_older_judgments_need_curia(self):
        note = routing.route("case_law", "EU")["note"]
        self.assertNotIn("covers 2015 onwards", note)
        self.assertIn("older judgments", note)

    def test_eu_doctrine_is_unchanged_by_legalviz(self):
        row = routing.route("doctrine", "EU")
        self.assertEqual(["WebSearch", "ldh_search", "WebFetch"], row["tools"])

    def test_no_non_eu_row_routes_through_legalviz(self):
        for layer in routing.LAYERS:
            for code in ("UK", "US"):
                with self.subTest(layer=layer, jurisdiction=code):
                    tools = " ".join(routing.route(layer, code)["tools"])
                    self.assertNotIn("legalviz", tools)

    def test_no_non_uk_row_routes_through_uk_legal(self):
        for layer in routing.LAYERS:
            for code in ("EU", "US", "ZZ"):
                with self.subTest(layer=layer, jurisdiction=code):
                    tools = " ".join(routing.route(layer, code)["tools"])
                    self.assertNotIn("uklegal", tools)

    def test_uk_doctrine_is_unchanged_by_the_uk_legal_server(self):
        row = routing.route("doctrine", "UK")
        self.assertEqual(["WebSearch", "ldh_search", "WebFetch"], row["tools"])
        self.assertIn("UK/ICO", row["ldh_sources"])

    def test_uk_statutes_go_to_legislation_gov_uk(self):
        # D-105: the UK Legal server slices the act; legislation.gov.uk is the fail-soft portal.
        # D-161: Lex (i.AI) follows uklegal for sections, explanatory notes and amendments.
        row = routing.route("statutes", "gb")
        self.assertEqual("UK", row["jurisdiction"])
        self.assertEqual(
            [
                "uklegal_legislation_search",
                "uklegal_legislation_get_toc",
                "uklegal_legislation_get_section",
                "lex_search_for_legislation_sections",
                "lex_lookup_legislation",
                "lex_get_legislation_sections",
                "lex_get_explanatory_note_by_section",
                "lex_search_amendments",
                "WebFetch",
                "ldh_search",
            ],
            row["tools"],
        )
        self.assertEqual(["legislation.gov.uk"], row["domains"])

    def test_uk_statutes_note_carries_the_in_force_metadata_and_the_citation_tools(self):
        note = routing.route("statutes", "UK")["note"]
        self.assertIn("extent and in-force metadata", note)
        self.assertIn("uklegal_citations_resolve", note)
        self.assertIn("uklegal_citations_format_oscola", note)

    def test_uk_statutes_route_legislation_through_uklegal_and_lex(self):
        # D-161: Lex (i.AI) over the same legislation.gov.uk data, for explanatory
        # notes and amendment history; judgments stay on uklegal.
        row = routing.route("statutes", "UK")
        self.assertEqual(
            ["uklegal_legislation_search", "uklegal_legislation_get_toc",
             "uklegal_legislation_get_section", "lex_search_for_legislation_sections",
             "lex_lookup_legislation", "lex_get_legislation_sections",
             "lex_get_explanatory_note_by_section", "lex_search_amendments",
             "WebFetch", "ldh_search"],
            row["tools"],
        )
        self.assertEqual(["uklegal", "lex", "ldh"], routing.row_servers("statutes", "UK"))
        self.assertIn("lex_lookup_legislation", row["note"])
        self.assertIn("lex_get_explanatory_note_by_section", row["note"])
        self.assertIn("lex_search_amendments", row["note"])

    def test_uk_case_law_uses_find_case_law_and_never_bailii(self):
        row = routing.route("case_law", "UK")
        self.assertEqual(
            [
                "uklegal_case_law_search",
                "uklegal_judgment_get_header",
                "uklegal_judgment_get_index",
                "uklegal_judgment_get_paragraph",
                "WebFetch",
            ],
            row["tools"],
        )
        self.assertIn("caselaw.nationalarchives.gov.uk", row["domains"])
        self.assertNotIn("bailii.org", " ".join(row["domains"]))
        self.assertIn("BAILII is excluded", row["note"])

    def test_us_case_law_uses_courtlistener(self):
        row = routing.route("case_law", "US")
        self.assertEqual("courtlistener_search", row["tools"][0])
        self.assertIn("courtlistener_analyze_citations", row["tools"])

    def test_us_case_law_note_separates_the_blocked_html_from_the_anonymous_rest_search(self):
        # analysis/38 §5: courtlistener.com/opinion/** is a stable AWS WAF 202; the MCP wants OAuth.
        note = routing.route("case_law", "US")["note"]
        self.assertIn("OAuth", note)
        self.assertIn("202", note)
        self.assertIn("courtlistener.com/api/rest/v4/search/", note)

    def test_us_regulation_never_routes_to_ecfr_or_federal_register(self):
        row = routing.route("statutes", "US")
        self.assertIn("govinfo.gov", row["domains"])
        joined = " ".join(row["domains"])
        self.assertNotIn("ecfr", joined)
        self.assertNotIn("federalregister", joined)

    def test_us_statutes_prefer_the_year_free_govinfo_link_form(self):
        # analysis/38 §5: /link/uscode/15/45?link-type=html redirected to the USCODE-2024 edition.
        note = routing.route("statutes", "US")["note"]
        self.assertIn("govinfo.gov/link/uscode/<title>/<section>?link-type=html", note)

    def test_us_statutes_call_the_ecfr_stub_a_200_and_send_the_reader_to_the_apis(self):
        # analysis/38 §5.1: the stub answers 200, so liveness alone cannot tell it from a document.
        note = routing.route("statutes", "US")["note"]
        self.assertIn("HTTP 200", note)
        self.assertIn("Request Access", note)
        self.assertIn("federalregister.gov/api/v1/documents.json", note)

    def test_us_statutes_route_cfr_through_the_federal_regulations_server(self):
        row = routing.route("statutes", "US")
        self.assertEqual(
            ["fedregs_regulations_get_cfr_section", "fedregs_regulations_browse_cfr",
             "fedregs_regulations_search_rules", "fedregs_regulations_get_document", "WebFetch", "ldh_search"],
            row["tools"],
        )
        self.assertEqual(["fedregs", "ldh"], routing.row_servers("statutes", "US"))
        self.assertIn("ecfr.gov/current/title-<title>/section-<section>", row["note"])
        self.assertIn("federalregister.gov/d/", row["note"])

    def test_doctrine_falls_back_to_edpb_domain_for_the_eu(self):
        row = routing.route("doctrine", "EU")
        self.assertIn("edpb.europa.eu", row["domains"])
        self.assertIn("EU/EDPB", row["ldh_sources"])

    def test_unknown_jurisdiction_falls_back_to_the_layer_default(self):
        row = routing.route("doctrine", "ZZ")
        self.assertFalse(row["known"])
        self.assertEqual("WebSearch", row["tools"][0])

    def test_unknown_layer_raises(self):
        with self.assertRaises(ValueError):
            routing.route("secondary", "EU")

    def test_routing_for_is_the_cartesian_product(self):
        rows = routing.routing_for(["statutes", "case_law"], ["EU", "GB"])
        self.assertEqual(4, len(rows))
        self.assertEqual({"EU", "UK"}, {row["jurisdiction"] for row in rows})


class McpServerAliasTest(unittest.TestCase):
    """D-105: a routing tool is `<alias>_<suffix>` of a bundled server, not a host namespace."""

    def test_every_bundled_server_of_the_manifest_has_an_alias(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8-sig")
        )["mcpServers"]
        self.assertEqual(sorted(manifest), sorted(routing.MCP_SERVERS.values()))
        self.assertEqual("legalviz", routing.MCP_SERVERS["legalviz"])
        self.assertEqual("uk-legal", routing.MCP_SERVERS["uklegal"])
        self.assertEqual("https://api.legalviz.eu/mcp", manifest["legalviz"]["url"])
        self.assertEqual("https://uk-legal-mcp.fly.dev/mcp", manifest["uk-legal"]["url"])
        # D-148: two more hosted keyless servers, France and Switzerland.
        self.assertEqual("justicelibre", routing.MCP_SERVERS["justicelibre"])
        self.assertEqual("opencaselaw", routing.MCP_SERVERS["opencaselaw"])
        self.assertEqual("https://justicelibre.org/mcp", manifest["justicelibre"]["url"])
        self.assertEqual("https://mcp.opencaselaw.ch/mcp", manifest["opencaselaw"]["url"])
        # D-160: US federal regulations (eCFR + Federal Register), hosted keyless.
        self.assertEqual("federal-regulations", routing.MCP_SERVERS["fedregs"])
        self.assertEqual("https://federal-regulations.caseyjhand.com/mcp", manifest["federal-regulations"]["url"])
        # D-161: Lex (UK legislation, explanatory notes and amendments by i.AI), hosted keyless.
        self.assertEqual("lex", routing.MCP_SERVERS["lex"])
        self.assertEqual("https://lex.lab.i.ai.gov.uk/mcp", manifest["lex"]["url"])
        # D-184: CasusLegal + FAS advertising practice (RU), following the CH template.
        self.assertEqual("casus", routing.MCP_SERVERS["casus"])
        self.assertEqual("fas-search", routing.MCP_SERVERS["fas"])
        self.assertEqual("https://mcp.casus.legal/one/mcp", manifest["casus"]["url"])
        self.assertEqual("https://search.delay-rag.ru/mcp/", manifest["fas-search"]["url"])

    def test_every_server_is_named_for_the_sources_question_of_the_plan_gate(self):
        self.assertEqual(sorted(routing.MCP_SERVERS), sorted(routing.MCP_SERVER_LABELS))
        self.assertEqual("JusticeLibre (FR)", routing.MCP_SERVER_LABELS["justicelibre"])
        self.assertEqual("OpenCaseLaw (CH)", routing.MCP_SERVER_LABELS["opencaselaw"])
        self.assertEqual("Federal Regulations (US)", routing.MCP_SERVER_LABELS["fedregs"])
        self.assertEqual("Lex (UK, i.AI)", routing.MCP_SERVER_LABELS["lex"])
        self.assertEqual("CasusLegal (RU)", routing.MCP_SERVER_LABELS["casus"])
        self.assertEqual("FAS advertising practice (RU)", routing.MCP_SERVER_LABELS["fas"])

    def test_every_mcp_tool_of_the_table_starts_with_a_known_alias(self):
        for layer in routing.LAYERS:
            for code in ("EU", "UK", "US", "ZZ", "CH", "RU") + routing.MEMBER_STATES:
                for tool in routing.route(layer, code)["tools"]:
                    if tool in ("WebFetch", "WebSearch"):
                        continue
                    with self.subTest(layer=layer, jurisdiction=code, tool=tool):
                        alias = tool.split("_", 1)[0]
                        self.assertIn(alias, routing.MCP_SERVERS)


class ServerLabelForHostTest(unittest.TestCase):
    """D-192: the host of an endpoint address is named to the reader by its server's label."""

    def test_every_manifest_host_maps_to_the_label_of_its_server(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8-sig")
        )["mcpServers"]
        aliases = {server: alias for alias, server in routing.MCP_SERVERS.items()}
        self.assertEqual(sorted(manifest), sorted(routing.manifest_hosts().values()))
        for name, server in manifest.items():
            host = routing.manifest_host(server["url"])
            with self.subTest(server=name):
                self.assertEqual(name, routing.manifest_hosts()[host])
                self.assertEqual(routing.MCP_SERVER_LABELS[aliases[name]], routing.server_label(host))

    def test_the_russian_servers_are_named_the_way_the_plan_gate_names_them(self):
        self.assertEqual("CasusLegal (RU)", routing.server_label("mcp.casus.legal"))
        self.assertEqual("FAS advertising practice (RU)", routing.server_label("search.delay-rag.ru"))

    def test_the_host_is_matched_without_case_and_a_stranger_has_no_label(self):
        self.assertEqual("CasusLegal (RU)", routing.server_label("MCP.Casus.Legal"))
        self.assertEqual("", routing.server_label("sudact.ru"))
        self.assertEqual("", routing.server_label(""))


class LayerRulesTest(unittest.TestCase):
    def test_only_doctrine_uses_websearch_as_a_primary_tool(self):
        self.assertFalse(routing.layer_rules("statutes")["websearch_primary"])
        self.assertFalse(routing.layer_rules("case_law")["websearch_primary"])
        self.assertTrue(routing.layer_rules("doctrine")["websearch_primary"])

    def test_layer_rules_are_copies(self):
        row = routing.layer_rules("statutes")
        row["websearch_primary"] = True
        self.assertFalse(routing.layer_rules("statutes")["websearch_primary"])

    def test_unknown_layer_raises(self):
        with self.assertRaises(ValueError):
            routing.layer_rules("nope")


class EstimateTest(unittest.TestCase):
    def test_research_share_is_layers_times_issues_times_two(self):
        estimate = routing.estimate_calls(["statutes", "case_law", "doctrine"], 4)
        self.assertEqual(24, estimate["research"])

    def test_total_adds_the_intake_and_currency_share(self):
        estimate = routing.estimate_calls(["statutes"], 2)
        self.assertEqual(
            4 + limits.MCP_CURRENCY_CALLS_PER_LAYER + limits.MCP_INTAKE_CALLS, estimate["total"]
        )

    def test_zero_issues_still_costs_the_fixed_share(self):
        estimate = routing.estimate_calls(["statutes"], 0)
        self.assertEqual(0, estimate["research"])
        self.assertGreater(estimate["total"], 0)

    def test_budget_verdict_flags_a_full_run_over_the_quota_servers(self):
        verdict = routing.budget_verdict(["statutes", "case_law", "doctrine"], 30, {"ldh": 8, "courtlistener": 10})
        self.assertTrue(verdict["exceeds"])
        self.assertNotIn("exceeds_run_budget", verdict)
        self.assertNotIn("run_budget", verdict)

    def test_budget_verdict_is_quiet_for_a_small_brief_run(self):
        verdict = routing.budget_verdict(["statutes"], 1, {"ldh": 8, "courtlistener": 10})
        self.assertFalse(verdict["exceeds"])

    def test_daily_upper_bound_comes_from_limits(self):
        verdict = routing.budget_verdict(["statutes"], 1, {"ldh": 8})
        self.assertEqual(limits.MCP_PROVIDER_DAILY_LIMITS["ldh"], verdict["daily_upper_bound"])

    def test_the_estimate_carries_the_new_server_daily_ceilings(self):
        # D-105: every bundled server the routing table names has a modelled daily upper bound.
        estimate = routing.estimate_calls(["statutes"], 1)
        self.assertEqual(
            sorted(routing.MCP_SERVERS), sorted(estimate["provider_daily_limits"])
        )
        # D-107: 60 is a safety ceiling — neither free server publishes a quota.
        self.assertEqual(60, estimate["provider_daily_limits"]["legalviz"])
        self.assertEqual(60, estimate["provider_daily_limits"]["uklegal"])
        self.assertEqual(60, estimate["provider_daily_limits"]["fedregs"])
        self.assertEqual(60, estimate["provider_daily_limits"]["lex"])
        # D-184: CasusLegal publishes no quota (100); FAS publishes 300/day but stays out of
        # `MCP_QUOTA_SERVERS` (fix round 1) — both ceilings are an orientation only.
        self.assertEqual(100, estimate["provider_daily_limits"]["casus"])
        self.assertEqual(300, estimate["provider_daily_limits"]["fas"])

    def test_a_free_server_does_not_move_the_quota_verdict(self):
        # D-166: only the quota servers count — a free server never trips `exceeds`.
        for name in ("legalviz", "uklegal", "fedregs", "lex", "casus", "fas"):
            with self.subTest(server=name):
                verdict = routing.budget_verdict(["statutes", "case_law", "doctrine"], 30, {name: 8})
                self.assertEqual(0, verdict["daily_upper_bound"])
                self.assertFalse(verdict["exceeds"])


class DiscoverCacheTest(unittest.TestCase):
    def test_cache_file_exists_and_validates_against_the_internal_schema(self):
        path = routing.discover_cache_path()
        self.assertTrue(path.is_file(), f"missing {path}")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual([], schema.validate(data, "internal"))
        self.assertEqual("routing", data["kind"])

    def test_cache_carries_the_ldh_source_names_of_analysis_05(self):
        self.assertEqual(
            ["EU/EUR-Lex", "EU/ConsolidatedLegislation", "EU/CURIA", "EU/EDPB", "EU/GDPRhub"],
            routing.ldh_sources("EU"),
        )
        self.assertIn("UK/Legislation", routing.ldh_sources("UK"))
        self.assertIn("UK/ICO", routing.ldh_sources("UK"))
        self.assertTrue(routing.ldh_sources("US"))

    def test_cache_is_read_through_the_normalised_country_code(self):
        self.assertEqual(routing.ldh_sources("UK"), routing.ldh_sources("gb"))

    def test_unknown_country_has_no_cached_sources(self):
        self.assertEqual([], routing.ldh_sources("ZZ"))


class RoutingDigestTest(unittest.TestCase):
    """D-110: the intake analyst is told what to call first, per jurisdiction."""

    ALL = {"ldh": "a", "courtlistener": "b", "legalviz": "c", "uklegal": "d", "other": []}

    def lines(self, connected: dict) -> dict:
        rows = {}
        for line in routing.routing_digest(connected).splitlines():
            head, _, tail = line.partition(": ")
            rows[head] = [tool.strip() for tool in tail.split("→")]
        return rows

    def test_it_is_two_layers_by_three_jurisdictions_and_stays_short(self):
        digest = routing.routing_digest(self.ALL).splitlines()
        self.assertEqual(len(digest), 6)
        self.assertLessEqual(len(digest), 8)
        self.assertEqual(
            [line.split(":")[0] for line in digest],
            ["EU statutes", "UK statutes", "US statutes", "EU case_law", "UK case_law", "US case_law"],
        )
        self.assertNotIn("doctrine", routing.routing_digest(self.ALL))

    def test_every_connected_server_keeps_its_place_in_the_tool_order(self):
        rows = self.lines(self.ALL)
        self.assertEqual(
            rows["EU statutes"],
            [
                "legalviz_resolve",
                "legalviz_get_law_part",
                "ldh_resolve_reference",
                "ldh_search",
                "WebFetch publications.europa.eu",
            ],
        )
        self.assertEqual(rows["US case_law"][0], "courtlistener_search")

    def test_a_server_that_is_not_connected_drops_out_of_its_line(self):
        rows = self.lines({"ldh": "a", "other": []})
        self.assertEqual(
            rows["EU statutes"],
            ["ldh_resolve_reference", "ldh_search", "WebFetch publications.europa.eu"],
        )
        self.assertEqual(rows["UK case_law"], ["WebFetch caselaw.nationalarchives.gov.uk"])
        for line in routing.routing_digest({"ldh": "a"}).splitlines():
            self.assertNotIn("legalviz", line)
            self.assertNotIn("uklegal", line)

    def test_with_nothing_connected_every_line_still_names_a_reachable_tool(self):
        rows = self.lines({})
        self.assertEqual(len(rows), 6)
        for label, tools in rows.items():
            with self.subTest(row=label):
                for tool in tools:
                    self.assertTrue(tool.startswith("Web"), tools)

    def test_the_web_fallback_carries_the_preferred_domain(self):
        rows = self.lines({})
        self.assertEqual(rows["US statutes"], ["WebFetch govinfo.gov"])
        self.assertEqual(rows["EU case_law"], ["WebFetch publications.europa.eu"])

    def test_the_member_state_rows_stay_out_of_the_digest(self):
        # D-110 keeps the intake digest at six lines; the national rows are `route()`-only.
        digest = routing.routing_digest(self.ALL)
        self.assertEqual(6, len(digest.splitlines()))
        for code in routing.MEMBER_STATES:
            self.assertNotIn(f"{code} statutes", digest)

    def test_a_probe_that_could_not_be_read_is_the_same_as_nothing_connected(self):
        self.assertEqual(routing.routing_digest({}), routing.routing_digest(None))


if __name__ == "__main__":
    unittest.main()
