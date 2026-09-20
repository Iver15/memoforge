"""Static source-routing table and the MCP call estimate (ТЗ §4.3; no new HTTP clients here)."""

from __future__ import annotations

import json
from pathlib import Path

from . import limits, pylauncher

LAYERS: tuple[str, ...] = ("statutes", "case_law", "doctrine")

DISCOVER_CACHE_PATH = "lib/routing/discover-cache.json"
"""Cached LDH source names; refreshed by hand, never at runtime (analysis/05 §5)."""

# --- MCP server aliases (§4.3) --------------------------------------------

MCP_SERVERS: dict[str, str] = {
    "ldh": "legal-data-hunter",
    "courtlistener": "courtlistener",
    "legalviz": "legalviz",
    "uklegal": "uk-legal",
    "justicelibre": "justicelibre",
    "opencaselaw": "opencaselaw",
    "fedregs": "federal-regulations",
    "lex": "lex",
    "casus": "casus",
    "fas": "fas-search",
}
"""Routing alias -> bundled server of `.mcp.json`, and the key of that server in `intake/mcp-probe.json`.

A tool named `<alias>_<suffix>` in the table below is that server's `<suffix>` tool, whatever
namespace the host puts it under: `mcp__plugin_memoforge_<server>__<suffix>` in Claude Code, an
opaque connector namespace in Cowork. The probe records the namespaces it found and the agent
matches them by tool suffix; the plan gate reads the same aliases out of a routing row to see
whether that row still has a legal database behind it (§4.3, `gates.sources_question_needed`).
"""

MCP_SERVER_LABELS: dict[str, str] = {
    "ldh": "Legal Data Hunter",
    "courtlistener": "CourtListener",
    "legalviz": "LegalViz",
    "uklegal": "UK Legal",
    "justicelibre": "JusticeLibre (FR)",
    "opencaselaw": "OpenCaseLaw (CH)",
    "fedregs": "Federal Regulations (US)",
    "lex": "Lex (UK, i.AI)",
    "casus": "CasusLegal (RU)",
    "fas": "FAS advertising practice (RU)",
}
"""How a server is named to the user — in the `Sources` question of the plan gate (§2.4)."""

MANIFEST_PATH = ".mcp.json"
"""`<plugin_root>/.mcp.json` — the one place that says which URL each bundled server answers on."""


def manifest_host(url: object) -> str:
    """Lower-case host of a manifest URL; `""` when there is none (D-192)."""
    text = str(url or "").strip()
    scheme, separator, rest = text.partition("://")
    if not separator:
        return ""
    return rest.split("/")[0].split("?")[0].rsplit("@", 1)[-1].split(":")[0].lower()


def manifest_hosts() -> dict[str, str]:
    """`{host: server key}` of `.mcp.json`; empty when the manifest cannot be read (D-192).

    Read on demand rather than cached: the file is tiny, and the only callers are the annex note
    and the classification test, neither of which runs in a loop.
    """
    path = pylauncher.plugin_root() / MANIFEST_PATH
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    servers = manifest.get("mcpServers") if isinstance(manifest, dict) else None
    hosts: dict[str, str] = {}
    for name, server in (servers or {}).items():
        host = manifest_host(server.get("url") if isinstance(server, dict) else "")
        if host:
            hosts[host] = str(name)
    return hosts


def server_label(host: object) -> str:
    """The label of the bundled server answering on `host`, or `""` for any other host (D-192).

    `.mcp.json` maps the host to the server key, `MCP_SERVERS` that key back to a routing alias and
    `MCP_SERVER_LABELS` the alias to the words the user already reads at the plan gate.
    """
    name = manifest_hosts().get(str(host or "").strip().lower())
    if not name:
        return ""
    for alias, server in MCP_SERVERS.items():
        if server == name:
            return MCP_SERVER_LABELS.get(alias, "")
    return ""

SPECIALIST_SERVERS: tuple[str, ...] = ("fas",)
"""D-184a: a specialist server answers one subject area only and never counts as coverage of a row.

FAS holds advertising, unfair-competition and antimonopoly decisions alone, so a Russian case-law
question outside those subjects is unserved even with `fas` connected. `row_servers` keeps naming
it — budgets, the intake digest and the researcher's routing block still route to it — and only
`gates.coverage_gaps` drops it, so «FAS is up» never silences the `Sources` question (§2.4).
"""


def row_servers(layer: str, jurisdiction: object) -> list[str]:
    """The bundled servers one routing row can use, in tool order and without repeats (§4.3).

    A row whose tools are only `WebFetch`/`WebSearch` answers with an empty list: it never had a
    legal database behind it, so no probe result can take one away from it.
    """
    aliases: list[str] = []
    for tool in route(layer, jurisdiction)["tools"]:
        alias = tool.split("_", 1)[0]
        if alias in MCP_SERVERS and alias not in aliases:
            aliases.append(alias)
    return aliases


DIGEST_LAYERS: tuple[str, ...] = ("statutes", "case_law")
"""Layers the intake digest covers (D-110); `doctrine` is WebSearch-first and needs no tool order."""


def routing_digest(connected: dict, exhausted: object = ()) -> str:
    """Tool order per `layer × jurisdiction`, filtered to the connected servers (§4.3, D-110).

    `connected` is the `namespaces` object of `intake/mcp-probe.json`: an alias with a truthy
    namespace is reachable in this session, everything else is not. A tool of an unreachable server
    is dropped from its line, the Web fallbacks always stay (with the row's preferred domain), so
    every line ends with something the analyst can actually call. Six lines — two layers × the three
    jurisdictions of `ROUTING`.

    `exhausted` is the set of aliases whose provider quota is spent for today (D-122): connected or
    not, their tools leave every line, so nobody is sent back to a server that already said no.
    """
    namespaces = connected if isinstance(connected, dict) else {}
    spent = {str(alias) for alias in (exhausted or ())}
    lines: list[str] = []
    for layer in DIGEST_LAYERS:
        for code in ROUTING[layer]:
            row = route(layer, code)
            tools: list[str] = []
            for tool in row["tools"]:
                alias = tool.split("_", 1)[0]
                if alias in MCP_SERVERS:
                    if namespaces.get(alias) and alias not in spent:
                        tools.append(tool)
                elif tool == "WebFetch" and row["domains"]:
                    tools.append(f"WebFetch {row['domains'][0]}")
                else:
                    tools.append(tool)
            lines.append(f"{code} {layer}: " + (" → ".join(tools) or "WebSearch"))
    return "\n".join(lines)


# --- jurisdiction codes ---------------------------------------------------

JURISDICTION_ALIASES: dict[str, str] = {
    "RUS": "RU",
    "RF": "RU",
    "RUSSIA": "RU",
    "GB": "UK",
    "GBR": "UK",
    "UNITED KINGDOM": "UK",
    "GREAT BRITAIN": "UK",
    "EN": "UK",
    "USA": "US",
    "UNITED STATES": "US",
    "EEA": "EU",
    "EEA MEMBER STATES": "EU",
    "EEA STATES": "EU",
    "MEMBER STATES": "EU",
    "EUROPEAN UNION": "EU",
}
"""`GB` is empty in LDH `discover_sources`; the working code is `UK` (§4.3, analysis/05 §5).

The three collective spellings resolve to `EU` so that a plan saying «EEA member states» gets the
EU row rather than the empty default row it used to get; `route()` marks that answer
`member_state_generic`, because the EU row carries Union law only — the national implementing law
behind it needs the concrete ISO codes of `MEMBER_STATES`.
"""

MEMBER_STATE_GENERIC: frozenset = frozenset({"EEA MEMBER STATES", "EEA STATES", "MEMBER STATES"})
"""Spellings that name a group of states instead of one; flagged in the `route()` answer."""


def _upper(code: object) -> str:
    """Trimmed upper-case spelling with `_` read as a space (`eea_member_states`)."""
    return str(code or "").strip().upper().replace("_", " ")


def normalize_jurisdiction(code: object) -> str:
    """Upper-case ISO-ish code with the §4.3 aliases applied (`GB` -> `UK`)."""
    upper = _upper(code)
    if not upper:
        return ""
    return JURISDICTION_ALIASES.get(upper, upper)


# --- layer rules (§4.3 table; only the agent's own row reaches its prompt) -

LAYER_RULES: dict[str, dict] = {
    "statutes": {
        "websearch_primary": False,
        "websearch_note": "discovery only",
        "citable": "MCP documents; WebFetch of issuing-body portals",
    },
    "case_law": {
        "websearch_primary": False,
        "websearch_note": "discovery only",
        "citable": "MCP documents; official courts / Find Case Law / CourtListener",
    },
    "doctrine": {
        "websearch_primary": True,
        "websearch_note": "primary discovery tool",
        "citable": (
            "MCP documents; official regulatory guidance, peer-reviewed publications, "
            "SSRN-class repositories"
        ),
    },
}


def layer_rules(layer: str) -> dict:
    """One row of the §4.3 layer table (the `${layer_rules}` placeholder of the dispatch prompt)."""
    try:
        return dict(LAYER_RULES[layer])
    except KeyError as exc:
        raise ValueError(f"unknown_layer: {layer!r}") from exc


# --- routing table (§4.3) -------------------------------------------------

_DEFAULT_ROWS: dict[str, dict] = {
    "statutes": {
        "tools": ["ldh_resolve_reference", "ldh_search", "WebFetch"],
        "domains": [],
        "note": "Resolve the reference first, then fetch only the pinpointed part; never the full act.",
    },
    "case_law": {
        "tools": ["ldh_search", "WebFetch"],
        "domains": [],
        "note": "Official court portals only.",
    },
    "doctrine": {
        "tools": ["WebSearch", "ldh_search", "WebFetch"],
        "domains": [],
        "note": "Regulatory guidance and peer-reviewed publications; WebSearch is primary.",
    },
}

ROUTING: dict[str, dict[str, dict]] = {
    "statutes": {
        "EU": {
            "tools": [
                "legalviz_resolve",
                "legalviz_get_law_part",
                "ldh_resolve_reference",
                "ldh_search",
                "WebFetch",
            ],
            "ldh_sources": ["EU/EUR-Lex", "EU/ConsolidatedLegislation"],
            "domains": ["publications.europa.eu", "eur-lex.europa.eu"],
            "note": (
                "legalviz_resolve (or legalviz_search_eu_law when only a title or keyword is known) "
                "turns the reference into a CELEX id, then legalviz_get_law_part with part=structure "
                "for the table of contents and one more call for the pinpointed article, version=<date> "
                "for a point-in-time reading; never pull a whole act, take get_law_part slices. "
                "legalviz_get_law_part with version=\"current\" also reports the consolidated CELEX it "
                "read in versionCelex — for the AI Act that is 02024R1689-20260727, as amended by "
                "Regulation (EU) 2026/1744 (Digital Omnibus on AI, CELEX 32026R1744). If LegalViz is "
                "unavailable, ldh_resolve_reference with hint_country=EU and a minimal result_detail "
                "for metadata and status, then the article text via ldh_search with a pinpoint or a "
                "WebFetch of Cellar, https://publications.europa.eu/resource/celex/<CELEX>, sent with "
                "Accept: application/xhtml+xml and Accept-Language: eng — without that Accept header "
                "Cellar answers with 60 MB of RDF, and with text/html it answers 404. The pinpoint "
                "anchors id=\"art_N\" exist only on the consolidated CELEX 0YYYYRNNNN-YYYYMMDD, not on "
                "the OJ form. eur-lex.europa.eu is the fallback behind Cellar: when it answers HTTP 202 "
                "with x-amzn-waf-action: challenge, retrying is useless — switch to Cellar or LegalViz."
            ),
        },
        "UK": {
            "tools": [
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
            "ldh_sources": ["UK/Legislation"],
            "domains": ["legislation.gov.uk"],
            "note": (
                "uklegal_legislation_search finds the act, uklegal_legislation_get_toc its "
                "structure, uklegal_legislation_get_section the parsed text of the one section you "
                "need — that answer carries extent and in-force metadata, so it is also the currency "
                "check for UK legislation. uklegal_citations_resolve confirms a citation points at a "
                "real document and uklegal_citations_format_oscola formats it. "
                "lex_* is the i.AI (GDS) service over the same legislation.gov.uk data: "
                "lex_lookup_legislation takes the official citation (ukpga/2018/12), "
                "lex_get_legislation_sections and lex_search_for_legislation_sections read or find "
                "sections, lex_get_explanatory_note_by_section adds the explanatory note of a "
                "section and lex_search_amendments the amendments that touched it — use it for "
                "explanatory notes and amendment history, or when uklegal is down; its case law is "
                "disabled, so judgments stay on uklegal. Fall back to a "
                "WebFetch of legislation.gov.uk, which is authoritative and point-in-time addressable."
            ),
        },
        "US": {
            "tools": [
                "fedregs_regulations_get_cfr_section",
                "fedregs_regulations_browse_cfr",
                "fedregs_regulations_search_rules",
                "fedregs_regulations_get_document",
                "WebFetch",
                "ldh_search",
            ],
            "ldh_sources": ["US/USCode"],
            "domains": ["govinfo.gov"],
            "note": (
                "Two federal sources, two routes. Statutes (U.S. Code): govinfo.gov only, and there the "
                "year-free form govinfo.gov/link/uscode/<title>/<section>?link-type=html is preferred: "
                "it redirects to the current edition by itself. Regulations (CFR) and rulemaking: "
                "fedregs_regulations_get_cfr_section with title, part and section (16, 312, 312.3) "
                "returns the codified text as of a date; register it with --tool "
                "fedregs_regulations_get_cfr_section and the official page as --url, "
                "https://www.ecfr.gov/current/title-<title>/section-<section>. "
                "fedregs_regulations_browse_cfr walks a title or part when the section is not known. "
                "fedregs_regulations_search_rules finds Federal Register proposed and final rules and "
                "fedregs_regulations_get_document reads one by its FR document number; register those at "
                "https://www.federalregister.gov/d/<FR number>. ecfr.gov and federalregister.gov pages "
                "answer HTTP 200 with a 10 KB «Request Access» stub to any non-browser client, so a "
                "WebFetch of them is never a source; their open APIs "
                "(federalregister.gov/api/v1/documents.json, and the eCFR API, which needs a compressed "
                "Accept-Encoding) are the fallback through mf sources fetch (Accept-Encoding gzip is sent "
                "for you) when the server is down."
            ),
        },
    },
    "case_law": {
        "EU": {
            "tools": [
                "legalviz_get_case_law",
                "legalviz_get_citing_provisions",
                "justicelibre_search_cjue",
                "justicelibre_get_decision_cjue",
                "ldh_resolve_reference",
                "ldh_search",
                "WebFetch",
            ],
            "ldh_sources": ["EU/CURIA"],
            "domains": ["publications.europa.eu", "eur-lex.europa.eu"],
            "note": (
                "legalviz_get_case_law and legalviz_get_citing_provisions find which CJEU judgments "
                "interpret the provision (case number, ECLI, date, name, articles) — they answer with "
                "that metadata, not with the judgment text; read the judgment itself through "
                "ldh_resolve_reference / ldh_search on EU/CURIA or a WebFetch of its CELEX text. "
                "justicelibre_search_cjue and justicelibre_get_decision_cjue are the alternative "
                "text source for a CJEU judgment when Cellar and LDH are both unavailable; the "
                "citation stays the CELEX address. A "
                "judgment carries the CELEX form 6<year>CJ<number> (C-252/21 is 62021CJ0252) on both "
                "hosts: https://publications.europa.eu/resource/celex/62021CJ0252 with "
                "Accept: application/xhtml+xml, and "
                "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62021CJ0252. Cellar serves "
                "the older judgments too and stays up while EUR-Lex is in a WAF window, so it comes "
                "first. curia.europa.eu is off the table altogether: liste.jsf, celex.jsf, "
                "document.jsf and showPdf.jsf all answer with the same 130 KB JavaScript shell for "
                "every case number, so it is neither a document nor a citation."
            ),
        },
        "UK": {
            "tools": [
                "uklegal_case_law_search",
                "uklegal_judgment_get_header",
                "uklegal_judgment_get_index",
                "uklegal_judgment_get_paragraph",
                "WebFetch",
            ],
            "ldh_sources": [],
            "domains": ["caselaw.nationalarchives.gov.uk"],
            "note": (
                "uklegal_case_law_search over Find Case Law, then judgment_get_header for the "
                "parties, neutral citation, court and dates, judgment_get_index for the structure "
                "and judgment_get_paragraph (or case_law_grep_judgment) for the pinpoint paragraph. "
                "uklegal_citations_resolve confirms the citation exists and "
                "uklegal_citations_format_oscola formats it. WebFetch "
                "caselaw.nationalarchives.gov.uk when the server is unavailable; BAILII is excluded "
                "by its own terms."
            ),
        },
        "US": {
            "tools": ["courtlistener_search", "courtlistener_analyze_citations", "WebFetch"],
            "ldh_sources": [],
            "domains": ["courtlistener.com", "supremecourt.gov"],
            "note": (
                "CourtListener MCP; extract_citations/analyze_citations verify existence. The MCP "
                "endpoint needs an OAuth token, and the courtlistener.com HTML pages sit behind an "
                "AWS WAF challenge (HTTP 202, empty body), so such a page is not a --url. With the "
                "server unavailable, the anonymous REST search "
                "courtlistener.com/api/rest/v4/search/ still answers; the document endpoints under "
                "/api/rest/v4/ require a token."
            ),
        },
    },
    "doctrine": {
        "EU": {
            "tools": ["WebSearch", "ldh_search", "WebFetch"],
            "ldh_sources": ["EU/EDPB", "EU/GDPRhub"],
            "domains": ["edpb.europa.eu", "europa.eu"],
            "note": "EDPB via LDH with a mandatory WebFetch fallback to edpb.europa.eu.",
        },
        "UK": {
            "tools": ["WebSearch", "ldh_search", "WebFetch"],
            "ldh_sources": ["UK/ICO"],
            "domains": ["ico.org.uk"],
            "note": "ICO guidance is the primary regulatory source for the UK.",
        },
        "US": {
            "tools": ["WebSearch", "WebFetch"],
            "ldh_sources": [],
            "domains": ["ftc.gov", "govinfo.gov"],
            "note": "Regulator guidance and peer-reviewed publications.",
        },
    },
}


# --- member states (§4.3; the national law behind the EU row) --------------

MEMBER_STATES: tuple[str, ...] = ("DE", "FR", "IE", "NL", "ES", "IT", "AT")
"""EU/EEA states whose own statute book and supervisory authority the table knows."""

_MEMBER_STATE_PORTALS: dict[str, tuple[str, ...]] = {
    "DE": ("gesetze-im-internet.de", "dejure.org", "buzer.de"),
    "FR": ("code.travail.gouv.fr", "legifrance.gouv.fr"),
    "IE": ("irishstatutebook.ie",),
    "NL": ("wetten.overheid.nl",),
    "ES": ("boe.es",),
    "IT": ("normattiva.it",),
    "AT": ("data.bka.gv.at", "ris.bka.gv.at"),
}
"""Official statute portals, first entry preferred; all of them are in `hooks/allowlist.txt`."""

_MEMBER_STATE_DPAS: dict[str, tuple[str, ...]] = {
    "DE": ("bfdi.bund.de", "datenschutzkonferenz-online.de"),
    "FR": ("cnil.fr",),
    "IE": ("dpc.ie",),
    "NL": ("autoriteitpersoonsgegevens.nl",),
    "ES": ("aepd.es",),
    "IT": ("garanteprivacy.it",),
    "AT": ("dsb.gv.at",),
}
"""National supervisory authorities — the doctrine source for a member state (Article 35(4) lists)."""

_MEMBER_STATE_STATUTE_NOTE = (
    "LegalViz reads Union law only, so a member state starts at its own official statute portal "
    "through WebFetch and uses ldh_search for what the portal will not serve. Register the "
    "provision under the portal URL with --tool WebFetch <domain>."
)

_MEMBER_STATE_STATUTE_BASE: dict[str, str] = {
    "FR": (
        "LegalViz reads Union law only, and France is the one member state whose statute book is "
        "not fetched at all: the bundled justicelibre server serves it."
    ),
}
"""States whose first sentence is not the generic «start at the portal» rule (D-148)."""

_MEMBER_STATE_STATUTE_TOOLS: dict[str, list[str]] = {
    "FR": [
        "justicelibre_get_law_article",
        "justicelibre_resolve_law_number",
        "ldh_resolve_reference",
        "WebFetch",
    ],
}
"""Tool order of a state whose row is not the default `WebFetch` → `ldh_search` (D-148)."""

_MEMBER_STATE_STATUTE_EXTRA: dict[str, str] = {
    "DE": (
        " gesetze-im-internet.de carries the official English translations under "
        "/englisch_<act>/ (BDSG is /englisch_bdsg), but that translation is one 194 KB page with no "
        "per-section anchors, so a pinpoint citation takes the German original "
        "/bdsg_2018/__<N>.html. ldh_search does not index the BDSG at all — dejure.org and buzer.de "
        "are the mirrors for a section the portal refuses. The bulk channel is "
        "gesetze-im-internet.de/gii-toc.xml, the index of all 6 130 consolidated acts, each with an "
        "<act>/xml.zip whose <norm> elements are one section apiece (<enbez>§ N). NeuRIS "
        "(rechtsinformationen.bund.de) is a declared trial with an incomplete dataset and holds "
        "texts as promulgated in the BGBl, not consolidated law, so it does not replace the portal."
    ),
    "FR": (
        " justicelibre_get_law_article is the route, and its contract is exact: the parameters are "
        "code=<short abbreviation> and num=<article>, not a title and not `article` — "
        "get_law_article(code=\"CT\", num=\"L1121-1\") answers with the text of Article L1121-1 of "
        "the Code du travail plus legiarti, legitext, etat: VIGUEUR, date_debut and a source_url on "
        "Légifrance. That source_url is the registered address (--tool "
        "justicelibre_get_law_article), never the MCP endpoint. A wrong code is not a dead end: the "
        "server answers with the full list of the 79 abbreviations it supports (CC, CP, CPC, CPP, "
        "CT, CSP, CJA, CGI, CSS, CESEDA, CONST …), and justicelibre_resolve_law_number finds the "
        "LEGITEXT/JORFTEXT of a law or decree outside the codes. The reserve is "
        "ldh_resolve_reference on FR/LegifranceCodes — 229 364 articles of every consolidated "
        "French code, not the 882-decision FR/legifrance case-law base — called with the exact "
        "citation; ldh_search is not a search engine for it and returns noise. "
        "legifrance.gouv.fr itself is closed by a Cloudflare managed challenge: 403 on every path, "
        "/robots.txt included, for any client without JavaScript and cookies, so it is the "
        "registered URL and never a WebFetch target. code.travail.gouv.fr is the keyless fallback "
        "that does serve text — code.travail.gouv.fr/code-du-travail/<article> answers 200 with the "
        "article and its LEGIARTI — but it carries the Code du travail alone."
    ),
    "IT": (
        " normattiva.it serves article text only inside a session (a cookie plus the landing page "
        "as Referer); a bare GET of atto/caricaArticolo answers 500. The stateless pinpoint is the "
        "official open-data API, https://api.normattiva.it/t/normattiva.api/bff-opendata/v1/api/v1 "
        "— OpenAPI 3.0.1 with no authorisation: POST /atto/dettaglio-atto-urn with an URN of the "
        "form urn:nir:stato:decreto.legislativo:2003-06-30;196~artN!vig=YYYY-MM-DD returns that one "
        "article as it stood on that date, under CC BY 4.0. The call needs a request body and a "
        "Content-Type, so it goes through mf sources fetch (D-149), not WebFetch — exactly this "
        "command (D-151): mf sources fetch --workdir <task> --url "
        "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1/api/v1/atto/dettaglio-atto-urn "
        "--method POST --json "
        "'{\"urn\": \"urn:nir:stato:decreto.legislativo:2003-06-30;196~art7!vig=2026-01-01\"}' "
        "--layer statutes, where the date after !vig= is the version date asked for. Register the "
        "act landing page, uri-res/N2Ls?urn:nir:…, or the article URN."
    ),
    "ES": (
        " the consolidated text comes from the BOE open-data API, "
        "boe.es/datosabiertos/api/legislacion-consolidada/id/{id}/texto/bloque/{aN}, one block per "
        "article. It refuses a request without an Accept header (400 «No soportado ningún mime "
        "type») and that endpoint answers only to Accept: application/xml, so it goes through "
        "mf sources fetch (D-149), not WebFetch."
    ),
    "NL": (
        " wetten.overheid.nl/xml.php no longer exists (404). The BWB text is reached through "
        "repository.officiele-overheidspublicaties.nl/bwb/{BWBID}/_manifest.xml, whose _latestItem "
        "names the current toestand XML with one <artikel> per article; the SRU endpoint at "
        "zoekservice.overheid.nl is the search front for it. Both need Accept: application/xml, so "
        "they go through mf sources fetch (D-149), not WebFetch."
    ),
    "IE": (
        " irishstatutebook.ie serves a section as HTML (/eli/<year>/act/<n>/section/<s>/enacted/"
        "en/html answers 200) but not as XML — the per-section XML path is 404 and only the whole "
        "act is available at /enacted/en/xml. The data is CC BY 4.0, the cleanest licence of the "
        "member-state portals."
    ),
    "AT": (
        " www.ris.bka.gv.at answers 503 to a bot check on every path, so the route is the open-data "
        "API in two steps: data.bka.gv.at/ris/api/v2.6/Bundesrecht"
        "?Applikation=BrKons&Gesetzesnummer=<N>&DokumenteProSeite=Ten returns JSON whose "
        "Dokumentliste…ContentUrl carries the Html entry, and that entry is "
        "ogd.ris.bka.gv.at/Dokumente/Bundesnormen/<NOR>/<NOR>.html. On the ogd host only the "
        "/Dokumente/… paths work. The OGD usage conditions bind: at most 0.5 requests per second, "
        "no parallel connections, bulk reading only between 20:00 and 05:00 or at weekends and on "
        "public holidays, and a notice to ris.it@bka.gv.at before any mass use — breaching them "
        "gets the IP blocked."
    ),
}

_MEMBER_STATE_DOCTRINE_NOTE = (
    "The national supervisory authority is the doctrine source for this state: its own guidance "
    "and its published Article 35(4) list of processing that always needs a DPIA. WebSearch "
    "locates the page, the authority's own site is the citation."
)

_MEMBER_STATE_DOCTRINE_EXTRA: dict[str, str] = {
    "FR": (
        " justicelibre_search_cnil reads the CNIL's own deliberations and sanctions, and "
        "justicelibre_search_doctrine the wider French commentary; both come before WebSearch."
    ),
}

MEMBER_STATE_ROWS: dict[str, dict[str, dict]] = {
    "statutes": {
        code: {
            "tools": list(_MEMBER_STATE_STATUTE_TOOLS.get(code, ["WebFetch", "ldh_search"])),
            "ldh_sources": [],
            "domains": list(domains),
            "note": _MEMBER_STATE_STATUTE_BASE.get(code, _MEMBER_STATE_STATUTE_NOTE)
            + _MEMBER_STATE_STATUTE_EXTRA.get(code, ""),
        }
        for code, domains in _MEMBER_STATE_PORTALS.items()
    },
    "case_law": {
        "FR": {
            "tools": [
                "justicelibre_search_judiciaire",
                "justicelibre_get_decision_judiciaire",
                "justicelibre_search_conseil_etat",
                "justicelibre_get_ce_decision",
                "justicelibre_search_cc",
            ],
            "ldh_sources": ["FR/Judilibre"],
            "domains": ["courdecassation.fr"],
            "note": (
                "justicelibre covers the three French orders: search_judiciaire and "
                "get_decision_judiciaire for the Cour de cassation and the courts below it, "
                "search_conseil_etat and get_ce_decision for the administrative order, search_cc "
                "for the Conseil constitutionnel. The decision text the tool returns is the source; "
                "courdecassation.fr/decision/<id> is not a --url, because it answers HTTP 200 with "
                "a 255-byte JavaScript redirect shell and no decision in it. Register the decision "
                "under the tool that returned it, with the ECLI in --citation."
            ),
        },
    },
    "doctrine": {
        code: {
            "tools": (
                ["justicelibre_search_cnil", "justicelibre_search_doctrine", "WebSearch", "WebFetch"]
                if code == "FR"
                else ["WebSearch", "ldh_search", "WebFetch"]
            ),
            "ldh_sources": [],
            "domains": list(domains),
            "note": _MEMBER_STATE_DOCTRINE_NOTE + _MEMBER_STATE_DOCTRINE_EXTRA.get(code, ""),
        }
        for code, domains in _MEMBER_STATE_DPAS.items()
    },
}
"""Rows `route()` finds after `ROUTING`, kept apart from it because `ROUTING` also drives the six
lines of `routing_digest` (D-110): the intake analyst is told about EU/UK/US, not about every state.
`case_law` holds France alone (D-148): the other states' own courts stay off-table, and
`known: False` says so."""

_RU_TOOL_NAMES_NOTE = (
    "The tool names in this row are the vendor's documented ones; the exact names are the tools "
    "the host exposes under that server's namespace — mcp__…casus__casuslegal_search_practice in "
    "Claude Code, an opaque connector namespace in Cowork — so match by tool suffix and call what "
    "the host offers when the two differ; intake/mcp-probe.json records the namespaces, not the "
    "tool names."
)
"""D-187a: the researcher's fallback for a tool name that moved — the host's own tool list.

The RU rows used to send it to `intake/mcp-probe.json` for the name itself, which that document
has never carried (`namespaces` and `status` only, and its schema is closed).
"""

EXTRA_JURISDICTION_ROWS: dict[str, dict[str, dict]] = {
    "statutes": {
        "RU": {
            "tools": ["ldh_search", "WebFetch"],
            "ldh_sources": ["RU/PravoGovRu"],
            "domains": ["www.consultant.ru", "base.garant.ru"],
            "note": (
                "LDH's RU/PravoGovRu corpus carries the official pravo.gov.ru texts with date "
                "filters — the first stop; pravo.gov.ru itself does not answer WebFetch — never "
                "fetch it. consultant.ru's free section serves article pages "
                "(www.consultant.ru/document/cons_doc_LAW_<id>/<hash>/ — the current wording with "
                "the amendment history inside the document), base.garant.ru is the second copy "
                "for verification; Plenum rulings and practice reviews of the Supreme Court are "
                "indexed there as ordinary documents (vsrf.ru has no stable document ids). "
                "Register with --raw-file. " + _RU_TOOL_NAMES_NOTE + " Write the pinpoint exactly "
                "as the source numbers it — ст. 152, "
                "п. 2 ст. 152, ч. 1 ст. 14.3, абз. 2 п. 1 ст. 10 (Cyrillic labels ст, п, пп, ч, "
                "абз, each with its number) — never art 152(2); the statute's registered citation "
                "form is the Russian one (Гражданский кодекс РФ (часть первая), ст. 152). "
                "D-196: a contract or an offer the client supplied is pinpointed the same way, by "
                "its own clause numbers and section headings (разд, раздел, гл, прил) with the "
                "heading left as the document wrote it — п. 3 разд. «Возмещение», п. 5.1 разд. "
                "«FBO» — never sec Reimbursement para 3."
            ),
        },
        "CH": {
            "tools": [
                "opencaselaw_get_law",
                "opencaselaw_search_laws",
                "opencaselaw_get_article_history",
                "WebFetch",
            ],
            "ldh_sources": [],
            "domains": ["fedlex.admin.ch"],
            "note": (
                "Switzerland is not an EU member state and LegalViz does not read it; the bundled "
                "opencaselaw server does. opencaselaw_get_law takes sr_number and article — "
                "get_law(sr_number=\"220\", article=\"328b\") returns Art. 328b OR with the "
                "consolidation date and a Fedlex source_url "
                "(fedlex.admin.ch/eli/cc/…/de#art_328b), which is the address to register. The "
                "answer also names the consolidations already scheduled («a consolidation of this "
                "act enters into force on <date>»), so it is the currency check for Swiss law as "
                "well. opencaselaw_search_laws finds the SR number, opencaselaw_get_article_history "
                "the earlier wordings. An unrecognised parameter is reported, not swallowed."
            ),
        },
    },
    "case_law": {
        "RU": {
            "tools": [
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
            "ldh_sources": ["RU/Sudact"],
            "domains": ["sudact.ru"],
            "note": (
                "CasusLegal is the paid higher-courts corpus (КС/ВС/ВАС) — search_practice with "
                "mode hybrid/semantic/bm25 and an article filter (article=\"ст. 619 ГК\"), find_term "
                "for a fixed phrase, get_case_details(case_id) for the exact quote in sections (a "
                "КС act carries its position inline); read constitutional_context and "
                "latest_practice, not only results; specialised corpora sip_* (intellectual "
                "property court) and kas_* (administrative chamber) have the same tool shapes. "
                + _RU_TOOL_NAMES_NOTE + " The FAS server "
                "(free, 8,000 decisions of the Federal Antimonopoly Service and its regional "
                "offices under the Law on Advertising, 20 calls/min and 300/day per IP shared "
                "with other users) is for advertising, unfair-competition and antimonopoly "
                "questions only: search_fas_cases (semantic, filters year/region/article), "
                "get_case_details, get_filter_options first to learn the filter values; cite a "
                "decision by the office, date and case number. LDH's RU/Sudact covers "
                "general-jurisdiction and arbitration courts 2021–2026; sudact.ru pages for a "
                "decision by case number; kad.arbitr.ru is captcha-gated — not a source. The "
                "address a Casus or FAS tool returns is an endpoint address, not a page — do not "
                "pass it as `--url`; pass the public page when you found one (sudact.ru, vsrf.ru), "
                "else no URL at all: the citation form identifies the decision. Register "
                "the decision's citation form in Russian (Определение СКЭС ВС РФ от 12.03.2024 "
                "№ 305-ЭС23-12345 по делу № А40-…; Постановление Пленума ВС РФ от … № …) and "
                "pinpoint by п. N (пункт мотивировочной части) where the text is numbered, else "
                "no pinpoint; a FAS decision is cited by the office, date and case number."
            ),
        },
        "CH": {
            "tools": [
                "opencaselaw_search_decisions",
                "opencaselaw_get_decision",
                "opencaselaw_get_regeste",
                "opencaselaw_find_leading_cases",
                "WebFetch",
            ],
            "ldh_sources": [],
            "domains": ["bger.ch"],
            "note": (
                "opencaselaw_search_decisions over the federal and cantonal corpus, "
                "opencaselaw_get_decision for the text, opencaselaw_get_regeste for the official "
                "headnote and opencaselaw_find_leading_cases for the BGE line on a question. The "
                "data is CC0, the citation is the BGE/BGer reference and the registered URL is the "
                "decision on bger.ch. entscheidsuche.ch has its own MCP server, but it is not "
                "bundled — do not route to it."
            ),
        },
    },
    "doctrine": {
        "RU": {
            "tools": ["WebSearch", "WebFetch"],
            "ldh_sources": [],
            "domains": ["zakon.ru", "cyberleninka.ru"],
            "note": (
                "Commentary and articles; consultant.ru's free section sometimes carries "
                "commentaries — cite the author and the outlet. " + _RU_TOOL_NAMES_NOTE
            ),
        },
        "CH": {
            "tools": [
                "opencaselaw_get_doctrine",
                "opencaselaw_search_commentaries",
                "WebSearch",
                "WebFetch",
            ],
            "ldh_sources": [],
            "domains": ["edoeb.admin.ch"],
            "note": (
                "opencaselaw_get_doctrine and opencaselaw_search_commentaries read the Swiss "
                "commentary literature; the federal supervisory authority (edoeb.admin.ch) is the "
                "regulatory source and the citation for its own guidance."
            ),
        },
    },
}
"""Jurisdictions outside `MEMBER_STATES` that a bundled server covers end to end (D-148).

`MEMBER_STATES` stays an EU/EEA list — it drives the member-state sweep of `preflight` and the
«EEA member states» expansion — so Switzerland lives here and is merged in by `route()` alone.
"""


def route(layer: str, jurisdiction: object) -> dict:
    """Tool order and preferred domains for one `layer x jurisdiction` pair (§4.3)."""
    if layer not in ROUTING:
        raise ValueError(f"unknown_layer: {layer!r}")
    code = normalize_jurisdiction(jurisdiction)
    row = (
        ROUTING[layer].get(code)
        or MEMBER_STATE_ROWS.get(layer, {}).get(code)
        or EXTRA_JURISDICTION_ROWS.get(layer, {}).get(code)
    )
    known = row is not None
    if row is None:
        row = _DEFAULT_ROWS[layer]
    result = {
        "layer": layer,
        "jurisdiction": code,
        "tools": list(row.get("tools", [])),
        "ldh_sources": list(row.get("ldh_sources", [])),
        "domains": list(row.get("domains", [])),
        "note": row.get("note", ""),
        "known": known,
        "member_state_generic": _upper(jurisdiction) in MEMBER_STATE_GENERIC,
    }
    return result


def routing_for(layers: list[str] | tuple[str, ...], jurisdictions: list[str] | tuple[str, ...]) -> list[dict]:
    """The ready-made `routing[]` parameter of `legal-researcher` (§4.3)."""
    rows: list[dict] = []
    for layer in layers:
        for jurisdiction in jurisdictions or [""]:
            rows.append(route(layer, jurisdiction))
    return rows


# --- MCP budget estimate (§4.3) -------------------------------------------


def estimate_calls(layers: list[str] | tuple[str, ...], issues: int) -> dict:
    """`layers x issues x 2` plus the intake/currency share, compared against the daily caps (§4.3)."""
    layer_count = len(list(layers))
    issue_count = max(int(issues), 0)
    research = layer_count * issue_count * 2
    currency = layer_count * limits.MCP_CURRENCY_CALLS_PER_LAYER
    intake = limits.MCP_INTAKE_CALLS
    total = research + currency + intake
    return {
        "layers": layer_count,
        "issues": issue_count,
        "research": research,
        "currency": currency,
        "intake": intake,
        "total": total,
        "provider_daily_limits": dict(limits.MCP_PROVIDER_DAILY_LIMITS),
    }


def budget_verdict(layers: list[str] | tuple[str, ...], issues: int, mcp_budget: dict | None) -> dict:
    """True `exceeds` when the estimate is above the daily quota of the quota servers (D-166)."""
    estimate = estimate_calls(layers, issues)
    budget = dict(mcp_budget or {})
    daily = sum(limits.MCP_PROVIDER_DAILY_LIMITS[s] for s in limits.MCP_QUOTA_SERVERS if s in budget)
    estimate["daily_upper_bound"] = daily
    estimate["exceeds_daily_upper_bound"] = bool(daily) and estimate["total"] > daily
    estimate["exceeds"] = estimate["exceeds_daily_upper_bound"]
    return estimate


# --- discover cache -------------------------------------------------------


def discover_cache_path() -> Path:
    """Absolute path of `lib/routing/discover-cache.json` inside the plugin."""
    return pylauncher.plugin_root() / "lib" / "routing" / "discover-cache.json"


def discover_cache() -> dict:
    """Read the cached LDH source names; an unreadable cache is not fatal (§4.3)."""
    from . import state_io

    try:
        data = state_io.read_json(discover_cache_path())
    except (OSError, ValueError):
        return {"schema_version": 1, "kind": "routing", "entries": {}}
    return data if isinstance(data, dict) else {"schema_version": 1, "kind": "routing", "entries": {}}


def ldh_sources(country: object) -> list[str]:
    """Cached LDH source names for one country code (normalised: `GB` -> `UK`)."""
    entries = discover_cache().get("entries") or {}
    row = entries.get(normalize_jurisdiction(country)) or {}
    return [str(name) for name in (row.get("sources") or [])]
