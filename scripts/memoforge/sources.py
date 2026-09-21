"""`mf sources register|fetch|pack|digest|liveness|verify|slice` — registry and freeze (ТЗ §5.3, M5/M6)."""

from __future__ import annotations

import argparse
import codecs
import gzip
import io
import json
import os
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path

from . import events, i18n, limits, routing, schema, source_text, state_io, stepctx

REGISTRY_PATH = "research/sources.json"
PACK_PATH = "research/source-pack.json"
CURRENCY_PATH = "research/currency.json"
RAW_DIR = "research/raw"

LAYERS: tuple[str, ...] = ("statutes", "case_law", "doctrine")
TIERS: tuple[str, ...] = ("critical", "supporting", "background")
US_VERDICTS: tuple[str, ...] = ("resolved", "unresolved", "ambiguous", "n/a")

ROLE_ORDER: tuple[str, ...] = ("rule", "application", "risk", "background")
"""Precedence used to project `role_by_issue` onto `use_in_memo` (D-02 enum)."""

WEIGHT_ORDER: tuple[str, ...] = ("non_binding", "persuasive", "binding")
"""Strongest weight across the findings wins; tier never enters the computation (§5.3)."""

CONFIDENCE_ORDER: tuple[str, ...] = ("low", "medium", "high")
"""Lowest confidence across the findings wins (conservative projection, §5.3)."""

BLOCKING_CURRENCY: tuple[str, ...] = ("do_not_use",)
EXCEPTION_CURRENCY: tuple[str, ...] = ("do_not_use", "manual_check")

RAW_KINDS: tuple[str, ...] = ("full_text", "excerpt", "agent_summary", "client_file", "none")
"""D-200: what the saved text is. `full_text` = saved whole by code (`mf sources save`); `none` = no text."""

AGENT_RAW_KINDS: tuple[str, ...] = ("excerpt", "agent_summary", "client_file")
"""D-200: what `register --raw-kind` accepts. `full_text` and `none` never come from the agent."""

SOURCES_FROZEN_HINT = "the source pack is frozen; registrations after the freeze never reach the memo"
"""Answer of `register` and of `save` once `pack --freeze` has closed the registry (M6, §5.3)."""

FULL_TEXT_INTEGRITY = "full_text_integrity"
"""Warning code of the freeze integrity check (D-200): a `full_text` file that changed or vanished."""

MAX_SLUG_LENGTH = 80

# --- offline identifier syntax (§5.3 «оффлайн: синтаксис CELEX/ECLI/ELI/neutral citation») ---

CELEX_RE = re.compile(r"^[1-9CE]\d{4}[A-Z]{1,2}\d{4}(\(\d{2}\))?$")
ECLI_RE = re.compile(r"^ECLI:[A-Z]{2}:[A-Za-z0-9]{1,7}:\d{4}:[A-Za-z0-9._-]{1,25}$")
ELI_RE = re.compile(r"^(https?://[^\s]+/)?eli(/[A-Za-z0-9._~%-]+){2,}$")
NEUTRAL_RE = re.compile(r"^\[\d{4}\]\s+[A-Z][A-Za-z]*(\s+[A-Za-z()]+)*\s+\d+$")
REPORTER_RE = re.compile(r"^\d+\s+[A-Za-z0-9.'&\- ]+\s+\d+$")

IDENTIFIER_PATTERNS: dict[str, re.Pattern] = {
    "celex": CELEX_RE,
    "ecli": ECLI_RE,
    "eli": ELI_RE,
    "neutral": NEUTRAL_RE,
    "reporter_cite": REPORTER_RE,
}

EU_IDENTIFIERS: tuple[str, ...] = ("celex", "ecli", "eli")

BODY_COMPARABLE_TOOLS: tuple[str, ...] = ("curl", "wget", "mf-fetch", "mf-save")
"""D34-08: the only tools whose `raw_path` holds the bytes `url` served.

`mf-fetch` joined them with D-149: `mf sources fetch` writes the response body byte for byte, so
liveness may hash the page again and compare. `mf-save` joined them with D-199: the stored text is
the converted body of that very url, digested once by `prepare_raw` — the same normalisation
liveness applies before it compares, so a page that has not moved can promote `provenance`.

Everything else hands the agent processed text — an MCP server returns extracted markdown, and
WebFetch returns the model's rendering of the page, not its body — whose sha256 can never equal the
sha256 of the page it came from. Comparing them marked 64 of 70 sources `changed`.
"""

UNCHECKED_HTTP_CODES: frozenset = frozenset({202, 403, 429, 503})
"""D34-08: queue / anti-bot / throttle answers. The link is not dead, it was simply not served, so
the body is neither read nor hashed and the verdict is `unchecked`."""

LIVENESS_USER_AGENT = "memoforge/2 liveness (+https://github.com/gregmos/memoforge)"
"""D-146: the honest agent of D34-08 plus a contact url, the standard courtesy for an automated
reader (analysis/38 §7.4 item 5). Faking a browser string is pointless — every host checked answers
both agents alike — and would contradict what eCFR itself declares."""

DEFAULT_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
"""D-146: `_open` used to send no `Accept` at all, which is what makes Cellar answer with RDF."""

CELLAR_HOST = "publications.europa.eu"
CELLAR_PATH_PREFIX = "/resource/celex/"
CELLAR_HEADERS: dict[str, str] = {"Accept": "application/xhtml+xml", "Accept-Language": "eng"}
"""D-146: the Cellar call contract of analysis/38 §1.5 — strict, and not a preference list.

`application/xhtml+xml` → 200 and the full text; `text/html` → 404; `*/*` → 3.8 KB of RDF; **no
`Accept` at all → 60 946 711 bytes of RDF**, which `LIVENESS_MAX_BODY_BYTES` would truncate and hash
into nonsense. `Accept-Language` takes the three-letter code (`eng`, not `en`).
"""

AWS_WAF_HEADER = "x-amzn-waf-action"
CLOUDFLARE_HEADER = "Cf-Mitigated"
CLOUDFLARE_CODES: frozenset = frozenset({403, 503})

_SCRIPT_STYLE_RE = re.compile(rb"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(rb"<[^>]*>", re.DOTALL)
_HTML_SNIFF_RE = re.compile(rb"<(!doctype\s+html|html)\b", re.IGNORECASE)
"""D-149: what a body without a `Content-Type` has to look like to be judged as a page."""

_META_REFRESH_RE = re.compile(rb"<meta[^>]+http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE)

MARKUP_CONTENT_TYPES: frozenset = frozenset({"text/html", "application/xhtml+xml"})
"""D-149: the two types the size and text-ratio rules apply to.

Everything else — `application/json` (RIS OGD), `application/xml` (`data.xml` of Find Case Law),
`application/pdf` (SCOTUS), `text/plain` — is a document at any size, and stripping `<…>` out of it
measures nothing.
"""

CHALLENGE_PHRASES: tuple[bytes, ...] = (
    b"aggressive automated scraping",
    b"flagged as potentially automated",
    b"complete the captcha",
    b"checking your browser",
    b"verify you are human",
    b"requires js enabled",
    b"enable javascript and cookies",
)
"""D-149: what a bot wall says, matched in the visible text of a markup body.

Measured on 2026-09-13, `ecfr.gov/current/title-16/part-312/section-312.3` under the plugin UA:
HTTP 200, `text/html`, **10 596 bytes, visible-text ratio 0.0939, 1 181 characters of prose** — over
the 2 KB floor and over the 5 % ratio, so neither of the other two rules sees it. The page says it
itself: «Due to aggressive automated scraping … programmatic access … is limited». No statute or
judgment carries these sentences.
"""

# --- what a client may be given (D-192, D-193) -----------------------------

NON_PUBLIC_SOURCE_HOSTS: tuple[str, ...] = (
    "mcp.casus.legal",
    "search.delay-rag.ru",
    "mcp.courtlistener.com",
    "api.legalviz.eu",
    "uk-legal-mcp.fly.dev",
    "mcp.opencaselaw.ch",
    "federal-regulations.caseyjhand.com",
    "lex.lab.i.ai.gov.uk",
)
"""D-192: hosts that serve an MCP endpoint and nothing a client can open.

The first Russian run delivered a memorandum whose sources pointed at `mcp.casus.legal` with a
session token in the query — a paid endpoint, not a page of the act. A url on one of these hosts is
never stored as a source `url`, never printed in the annex and never linked from the body.
`test_sources.ManifestClassificationTest` fails when a newly bundled server of `.mcp.json` is
listed in neither this tuple nor `PUBLIC_MCP_HOSTS`, so classification cannot be forgotten.
"""

PUBLIC_MCP_HOSTS: tuple[str, ...] = ("justicelibre.org", "legaldatahunter.com")
"""D-192: bundled servers whose host also serves public pages — their urls stay as they are."""

AUTH_QUERY_PARAMS: tuple[str, ...] = (
    "t",
    "token",
    "access_token",
    "auth",
    "key",
    "api_key",
    "apikey",
    "sig",
    "signature",
    "session",
    "sid",
    "jwt",
)
"""D-192: query parameter names (matched without case) that carry a credential, never a location."""

RETRIEVED_VIA_TEMPLATE = "[retrieved via {server}]"
"""D-193: what an endpoint address becomes inside a stored raw text."""

GENERIC_DATABASE = "a legal database"
"""D-193: the stand-in for a host of no bundled server; the annex has a pack key for the same idea."""

URL_NOT_PUBLIC = "url_not_public"
URL_NOT_PUBLIC_HINT = (
    "register the public page of the act (sudact.ru, vsrf.ru, the court's site) or leave the URL "
    "empty; the citation form identifies the act"
)
"""D-192: the warning `sources register` answers with, and the one line that says what to do."""

IDNA_DOTS = ".。．｡"
"""A1: the four characters IDNA reads as a label separator — a host may be written with any of them."""

JSON_ESCAPED_SLASH = "\\/"
"""A3: how a slash appears when an agent saves a tool's JSON answer as the raw text."""

JSON_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
"""R2: `\\u0026`, `\\u003d`, `\\u003f` — how Go, .NET and PHP serialisers write `&`, `=` and `?`."""

ADDRESS_REMOVED = "[address removed]"
"""R1: what stands where an address the cleaning could not read used to be.

Nothing in it needs JSON escaping, so it can replace a url inside a JSON raw text as it is.
"""

URL_IN_TEXT_RE = re.compile(r"https?:\\?/\\?/(?:\\/|\\u[0-9a-fA-F]{4}|[^\s<>\"'`\\])+", re.IGNORECASE)
"""D-193: a url inside a raw text, plain or JSON-escaped (A3, R2).

Only `\\/` and `\\uXXXX` carry the match past a backslash, so a url that runs into an escape
sequence of some other kind ends there. Trailing punctuation is trimmed by `scrub_urls`.
"""

URL_TRAILING_PUNCTUATION = ".,;:!?)]}»"
"""Characters a sentence puts after a url, which are never part of it."""

PINPOINT_KINDS: dict[str, str] = {
    "article": "art",
    "art": "art",
    "annex": "annex",
    "annexe": "annex",
    "section": "s",
    "sec": "s",
    "recital": "rec",
    "rec": "rec",
}
"""Normalised names of the pinpoint kinds that take part in the duplicate key (D34-04)."""

PINPOINT_RE = re.compile(
    r"(?:\b|_|-)(articles?|arts?|annexes?|annex|sections?|sec|recitals?|rec)\.?[\s_-]*"
    r"([0-9]{1,4}|[ivxlcdm]{1,6})(?![a-z0-9])",
    re.IGNORECASE,
)
"""`Art 88`, `art. 88`, `-art88`, `Annex III` — the provision a citation_form or a source_id names."""

ARTICLE_HEADING_PREFIX = (
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*"
    r"(?:Article|Art\.|Section|Sec\.|§|Статья|Ст\.)[ \t]*"
)
ANY_ARTICLE_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*"
    r"(?:Article|Art\.|Section|Sec\.|§|Статья|Ст\.)[ \t]*\d+",
    re.MULTILINE | re.IGNORECASE,
)
ARTICLE_HEADING_NUMBER_RE = re.compile(ARTICLE_HEADING_PREFIX + r"(\d+)", re.MULTILINE | re.IGNORECASE)
"""Same headings as `ANY_ARTICLE_HEADING_RE`, capturing the number (duplicate contradiction, D34-04)."""


class ContradictoryDuplicates(ValueError):
    """A duplicate group whose members hold visibly different provisions (D34-04).

    The freeze collapses duplicates into one id; it must never do so when the raw texts disagree
    about which article they are.
    """

    def __init__(self, groups: list[dict]) -> None:
        self.groups = groups
        super().__init__("contradictory_duplicate_group")


# --- paths and io ---------------------------------------------------------


def registry_path(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/research/sources.json`."""
    return Path(work_dir) / REGISTRY_PATH


def pack_path(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/research/source-pack.json` (the frozen snapshot, D-03)."""
    return Path(work_dir) / PACK_PATH


def sources_lock(work_dir: str | os.PathLike) -> state_io.FileLock:
    """`sources.lock`; taken after `state.lock` and before `events.lock` (§2.2)."""
    return state_io.FileLock(state_io.lock_path(work_dir, "sources"))


def empty_registry() -> dict:
    """A registry with no sources yet."""
    return {"schema_version": 2, "sources": {}}


def read_registry(work_dir: str | os.PathLike) -> dict:
    """Read `research/sources.json`, returning an empty registry when it does not exist."""
    path = registry_path(work_dir)
    if not path.is_file():
        return empty_registry()
    data = state_io.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        raise ValueError("sources_registry_malformed")
    return data


def write_registry(work_dir: str | os.PathLike, registry: dict) -> Path:
    """Validate and atomically write the registry (caller holds `sources.lock`)."""
    schema.validate_or_raise(registry, "sources")
    return state_io.write_json_atomic(registry_path(work_dir), registry)


def read_pack(work_dir: str | os.PathLike) -> dict | None:
    """Read the frozen `source-pack.json`, or None when the freeze has not happened."""
    path = pack_path(work_dir)
    if not path.is_file():
        return None
    data = state_io.read_json(path)
    return data if isinstance(data, dict) else None


def is_frozen(work_dir: str | os.PathLike, state: dict | None = None) -> bool:
    """`state.sources_frozen` OR an existing snapshot file (D-03)."""
    if state is None:
        state = state_io.read_state_or_none(work_dir) or {}
    return bool(state.get("sources_frozen")) or pack_path(work_dir).is_file()


def snapshot_map(work_dir: str | os.PathLike) -> dict[str, str | None]:
    """`{source_id: raw_sha256}` of the freeze snapshot; empty before the freeze."""
    pack = read_pack(work_dir)
    if not pack:
        return {}
    return {
        row["source_id"]: row.get("raw_sha256")
        for row in pack.get("snapshot") or []
        if isinstance(row, dict) and row.get("source_id")
    }


def snapshot_originals(work_dir: str | os.PathLike) -> dict[str, str]:
    """`{source_id: raw_original_sha256}` of the freeze snapshot; empty before it (D-201).

    Only the rows that carry the field: a record with no original, and one whose original was gone
    when the freeze ran, have nothing pinned and are simply absent.
    """
    pack = read_pack(work_dir)
    if not pack:
        return {}
    return {
        row["source_id"]: row["raw_original_sha256"]
        for row in pack.get("snapshot") or []
        if isinstance(row, dict) and row.get("source_id") and row.get("raw_original_sha256")
    }


def merged_map(work_dir: str | os.PathLike) -> dict[str, str]:
    """`{alias_id: canonical_id}` the freeze collapsed duplicates into; empty before it (D34-04)."""
    rows = (read_pack(work_dir) or {}).get("merged_into")
    if not isinstance(rows, dict):
        return {}
    return {str(alias): str(canonical) for alias, canonical in rows.items() if canonical}


def canonical_id(merged: dict, source_id: str) -> str:
    """The id a citation resolves to after the freeze, following `merged_into` (D-143).

    Findings and quotes keep the id they were written with, so every consumer of a citation has to
    resolve the alias before it looks the source up in the snapshot or in `entries[]`.
    """
    seen: set[str] = set()
    current = str(source_id)
    while current in merged and current not in seen:
        seen.add(current)
        current = str(merged[current])
    return current


def raw_kind_of(record: dict) -> str:
    """What the saved text is (D-200): the record's field when present, else `agent_summary` when it
    holds a raw path or digest, else `none`. The default lives here, never in the data — an old
    registry keeps working and is never rewritten."""
    kind = record.get("raw_kind")
    if kind in RAW_KINDS:
        return kind
    if record.get("raw_path") or record.get("raw_sha256"):
        return "agent_summary"
    return "none"


def pack_raw_kind(entry: dict, snapshot_digest: str | None = None) -> str:
    """What the saved text is after the freeze (D-200): the pack entry is authoritative. The field
    wins when present; otherwise `agent_summary` when a digest was passed, else `none`.

    The entry itself carries no digest (the pack keeps digests in `snapshot[]`), so the caller
    passes the entry's snapshot-row digest explicitly — a bare `pack_raw_kind(entry)` answers
    `none` for an entry without the field.
    """
    kind = entry.get("raw_kind")
    if kind in RAW_KINDS:
        return kind
    return "agent_summary" if snapshot_digest else "none"


# --- helpers --------------------------------------------------------------


CYRILLIC_TRANSLITERATION: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "iu",
    "я": "ia", "№": "no",
}
"""D-194: the fixed table `slugify` applies before the ASCII fold.

Without it `unicodedata.normalize(...).encode("ascii", "ignore")` deletes every Cyrillic letter, so
`ГК РФ, ст. 428` and a blog post titled `428` both slugged to `428` and the second registration
became `428-2` — an id that names nothing. The table is transliteration, not a standard: it only has
to be stable and readable.
"""


def transliterate(text: str) -> str:
    """Cyrillic (and `№`) to Latin by `CYRILLIC_TRANSLITERATION`, lower-cased (D-194)."""
    out = []
    for char in text:
        replacement = CYRILLIC_TRANSLITERATION.get(char.lower())
        out.append(char if replacement is None else replacement)
    return "".join(out)


def slugify(text: object, fallback: str = "source") -> str:
    """Stable kebab-case source slug matching the `source_id` pattern of the schema."""
    value = unicodedata.normalize("NFKD", transliterate(str(text or "")))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if len(value) > MAX_SLUG_LENGTH:
        value = value[:MAX_SLUG_LENGTH]
        if "-" in value[1:]:
            value = value[: value.rindex("-")]
    value = value.strip("-")
    return value or fallback


def unique_slug(base: str, taken: set) -> str:
    """`base`, `base-2`, `base-3` … until the id is free."""
    if base not in taken:
        return base
    index = 2
    while True:
        suffix = f"-{index}"
        candidate = base[: MAX_SLUG_LENGTH - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        index += 1


def normalize_url(url: object) -> str:
    """Compare urls without the scheme case, a trailing slash or an empty fragment."""
    value = str(url or "").strip()
    if not value:
        return ""
    value = re.sub(r"#$", "", value)
    value = re.sub(r"/+$", "", value)
    scheme, sep, rest = value.partition("://")
    if sep:
        value = scheme.lower() + sep + rest
    return value


def canonical_host(host: object) -> str:
    """One canonical form of a host before it is classified; `""` when there is none (A1, D-192).

    `urlsplit(...).hostname` lower-cases, and nothing else: `mcp.casus.legal.` (the trailing DNS
    dot) and `mcp.casus。legal` (U+3002, an IDNA-equivalent dot) both resolve to the endpoint host
    and both used to read as a stranger. The idna codec splits on all four dot characters and folds
    every label, so it is the one normalisation both registration and the renderer apply. A host it
    refuses — an empty or over-long label, an IP literal in brackets — canonicalises to `""`, and
    every caller reads that as «not an address a client may be given».
    """
    text = str(host or "").strip().lower().rstrip(IDNA_DOTS)
    if not text:
        return ""
    if "%" in text:
        # D-206: a percent-encoded host (`mcp%2ecasus%2elegal`) survives `urlsplit(...).hostname`
        # undecided, so no classifier can read it — an address this code cannot read is an address
        # a client must not be handed.
        return ""
    try:
        return text.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError):
        return ""


def source_host(url: object) -> str:
    """Canonical host of a registered url; `""` when it has none or it cannot be canonicalised.

    `urlsplit(...).hostname` is the parser of D-151, so `https://outside.example#@mcp.casus.legal`
    reads as `outside.example` and its mirror image reads as the endpoint it really is. Unlike
    `request_host` this answers for a url the fetch client refuses as well: `request_host` returns
    `""` for userinfo, and a url that may not be *fetched* is still a url that may not be
    *published* — `https://user:pw@mcp.casus.legal/case/1` must not survive registration.
    """
    try:
        return canonical_host(urllib.parse.urlsplit(str(url or "").strip()).hostname)
    except ValueError:
        return ""


def _strip_auth_query(query: str) -> str:
    """The query without its `AUTH_QUERY_PARAMS`; unchanged when it carries none (D-192)."""
    if not query:
        return query
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    kept = [(name, value) for name, value in pairs if name.lower() not in AUTH_QUERY_PARAMS]
    # Nothing to remove: the query is returned byte for byte, never re-encoded.
    return query if len(kept) == len(pairs) else urllib.parse.urlencode(kept)


def _is_auth_fragment(fragment: str) -> bool:
    """True when a fragment carries an `AUTH_QUERY_PARAMS` name as `name=value` (A2).

    Portals put a real anchor there (`#dst100`, `#art_6`, `#p123`, `#/document/12`) and OAuth-style
    flows put the credential there; only the second shape is dropped, and then the whole fragment
    goes — a credential never travels with the route that carried it.

    FF1: a hash-router address carries a query of its own inside the fragment — `#?t=…`,
    `#/document?t=…`. Read as one query string the first name comes out as `?t` or `/document?t`,
    which is in no list, so the token survived `public_url`, the raw-text scrub and the inline
    hyperlink. The part after the first `?` is therefore examined as a parameter string in its own
    right, next to the whole fragment.
    """
    if not fragment or "=" not in fragment:
        return False
    for candidate in (fragment, fragment.partition("?")[2]):
        if not candidate:
            continue
        pairs = urllib.parse.parse_qsl(candidate, keep_blank_values=True)
        if any(name.lower() in AUTH_QUERY_PARAMS for name, _ in pairs):
            return True
    return False


def _json_unescape(url: str) -> str:
    """The url a JSON-escaped token stands for: `\\/` and every `\\uXXXX` decoded (A3, R2)."""
    plain = url.replace(JSON_ESCAPED_SLASH, "/")
    return JSON_UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), plain)


def _json_escape(url: str) -> str:
    """The url written back into a JSON string, slashes escaped as the source text had them (R2).

    `json.dumps` decides what must be escaped — a quote, a backslash, a control character — so a
    `\\uXXXX` that decoded into one of those cannot break the document it is written back into.
    """
    return json.dumps(url)[1:-1].replace("/", JSON_ESCAPED_SLASH)


def clean_public_url(url: str) -> str:
    """A public url without its credentials: userinfo, auth query parameters, auth fragment (A2).

    The host is rewritten to its canonical form at the same time (A1). A url that carries none of
    those is returned byte for byte — nothing is re-encoded for its own sake.

    R1: the cleaning fails **closed**. `urlsplit(...).port` raises on `sudact.ru:bad`, and a host
    with an empty label canonicalises to nothing; returning the input in either case published the
    userinfo and the token it carried. An address this parser cannot read is answered with `""`,
    and every caller reads that as «there is no address here».
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host = canonical_host(parts.hostname)
        port = parts.port
        query = _strip_auth_query(parts.query)
        fragment = "" if _is_auth_fragment(parts.fragment) else parts.fragment
    except ValueError:
        return ""
    if not host:
        # An authority that cannot be read is suppressed; a url with no authority at all (a bare
        # path, `mailto:`) keeps today's behaviour and only loses its credentials.
        if parts.netloc:
            return ""
        rebuilt = urllib.parse.urlunsplit((parts.scheme, "", parts.path, query, fragment))
        return url if rebuilt == url else rebuilt
    netloc = f"{host}:{port}" if port else host
    rebuilt = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    return url if rebuilt == url else rebuilt


def public_url(url: object) -> tuple[str, str]:
    """`(clean_url, retrieved_from)` — the address a client may be given, and the one it may not.

    D-192. A url on a `NON_PUBLIC_SOURCE_HOSTS` host is not an address at all — it is an MCP
    endpoint, usually with a session token in the query — so it leaves no `url` and is recorded as
    `<scheme>://<host><path>` for the annex note alone. A host that cannot be canonicalised, or a
    url `urlsplit` cannot read, leaves no `url` either and nothing to record (A1, R1). Any other url
    keeps its location and loses its credentials (A2); one with no authority at all — a bare path,
    `mailto:` — keeps today's behaviour, minus the credentials it may still carry.
    """
    text = str(url or "").strip()
    if not text:
        return "", ""
    try:
        parts = urllib.parse.urlsplit(text)
        raw_host = parts.hostname or ""
    except ValueError:
        return "", ""
    if raw_host:
        host = canonical_host(raw_host)
        if not host:
            # A1: an address this code cannot read is an address a client must not be handed either.
            return "", ""
        if host in NON_PUBLIC_SOURCE_HOSTS:
            return "", f"{parts.scheme}://{host}{parts.path}"
    return clean_public_url(text), ""


def scrub_urls(text: str) -> str:
    """Every endpoint address out of a raw text, every credential out of the rest (D-193).

    The stored text travels to the client as `sources/<id>.txt` and into `source-pack.md`, and its
    sha256 is the C-02 anchor — so the scrub happens once, at ingest, before the hash.

    A3/R2: an agent that saves a tool's JSON answer as the raw text saves `https:\\/\\/host\\/path`,
    and a Go/.NET/PHP serialiser writes `&`, `=` and `?` as `\\u0026`, `\\u003d` and `\\u003f`. Both
    escapes are part of the url token, are decoded before the address is read, and are written back
    through `json.dumps` so the text stays valid JSON where it was. Neither marker — the
    `[retrieved via …]` one nor `[address removed]` — contains anything JSON escapes.

    R1: an address the cleaning cannot read is replaced whole by `ADDRESS_REMOVED` rather than left
    where it is. A stored raw text is exported to the client as `sources/<id>.txt`; an address this
    code could not parse is exactly the one nobody has checked.
    """

    def on_url(match: "re.Match[str]") -> str:
        found = match.group(0)
        tail = ""
        while found and found[-1] in URL_TRAILING_PUNCTUATION:
            tail = found[-1] + tail
            found = found[:-1]
        if not found:
            return match.group(0)
        escaped = "\\" in found
        plain = _json_unescape(found) if escaped else found
        host = source_host(plain)
        if host and host in NON_PUBLIC_SOURCE_HOSTS:
            label = routing.server_label(host) or GENERIC_DATABASE
            return RETRIEVED_VIA_TEMPLATE.format(server=label) + tail
        cleaned = clean_public_url(plain)
        if not cleaned:
            return ADDRESS_REMOVED + tail
        return (_json_escape(cleaned) if escaped else cleaned) + tail

    return URL_IN_TEXT_RE.sub(on_url, text)


def scrub_values(value: object) -> object:
    """`scrub_urls` over every string inside a `--meta` / `--identifiers` object (A4).

    A token-bearing endpoint address in `identifiers.eli` or `meta.short_name` reaches the docx,
    `deliverable.md`, `source-pack.md` and the dashboard exactly as one in `url` would.
    """
    if isinstance(value, str):
        return scrub_urls(value)
    if isinstance(value, dict):
        return {key: scrub_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_values(item) for item in value]
    return value


def redacted_url(url: object) -> str:
    """`scheme://host/path` — all an error message or a telemetry line may repeat of a url (A6).

    `mf sources fetch` refuses a url with credentials in it and used to name the whole address in
    `errors[0]`, which `cli.rejection_of` writes into `events.jsonl` and `finalize` exports in
    `_run/`. Userinfo, query and fragment are exactly where a token lives, so none of them is
    echoed; R1: an address with no readable host is answered with `ADDRESS_REMOVED`, never with a
    piece of the input — cutting the input at the first separator is one parser too many.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
        host = canonical_host(parts.hostname)
        parts.port  # noqa: B018 - R1: the same parse the cleaning does, so both fail on the same urls
    except ValueError:
        return ADDRESS_REMOVED
    if not host:
        return ADDRESS_REMOVED
    return f"{parts.scheme}://{host}{parts.path}" if parts.scheme else f"{host}{parts.path}"


def prepare_raw(payload: bytes) -> bytes:
    """The bytes a raw file is stored and hashed as: markup converted (D-163), urls scrubbed (D-193).

    One function for both callers — `store_raw` and the explicit-id repeat check of `register_source`
    — so re-registering the same file is still a no-op instead of a `source_id_collision`.
    """
    converted = markup_to_text(payload)
    payload = converted if converted is not None else payload
    text = payload.decode("utf-8-sig", errors="replace")
    scrubbed = scrub_urls(text)
    # A text with nothing to scrub keeps its bytes exactly, BOM and all: no hash of any existing
    # raw file moves because this pass now runs.
    return payload if scrubbed == text else scrubbed.encode("utf-8")


def dedup_key(layer: str, url: object, citation_form: object) -> tuple[str, str]:
    """Idempotency key of `sources register`: `(layer, url)`, or `(layer, citation_form)` (§3.1 rule 5)."""
    normalized = normalize_url(url)
    if normalized:
        return (layer, "url:" + normalized)
    return (layer, "cite:" + " ".join(str(citation_form or "").split()).lower())


def pinpoint(text: object) -> str:
    """`art:88` / `annex:iii` — the first provision a piece of text names, or `""` (D34-04)."""
    match = PINPOINT_RE.search(str(text or ""))
    if not match:
        return ""
    kind = match.group(1).lower().rstrip("s")
    return f"{PINPOINT_KINDS.get(kind, kind)}:{match.group(2).lower()}"


def declared_pinpoint(record: dict) -> str:
    """The provision the record claims to hold, read from `citation_form` and then `title`."""
    return pinpoint(record.get("citation_form")) or pinpoint(record.get("title"))


def instrument_identity(record: dict) -> str:
    """The citation with its pinpoint removed: two records of the same act share it (D34-04).

    Two fixtures that happen to carry identical raw text but cite different instruments are then
    never merged; `Art 88` and `art 88` of the same regulation always are.
    """
    text = str(record.get("citation_form") or record.get("title") or "")
    return re.sub(r"[^a-z0-9]+", " ", PINPOINT_RE.sub(" ", text).lower()).strip()


def is_mislabelled(source_id: str, record: dict) -> bool:
    """True when the `source_id` names one provision and the citation another (D34-04).

    `gdpr-article-3-territorial-scope` holding the text of Art. 88 is the real-run case: the id, and
    with it the name of the raw file, lies about its content.
    """
    from_id = pinpoint(source_id)
    from_citation = declared_pinpoint(record)
    return bool(from_id and from_citation and from_id != from_citation)


def mislabelled_sources(sources: dict) -> list[dict]:
    """Every `source_id` whose pinpoint contradicts its own citation_form/title (D34-04)."""
    return [
        {
            "source_id": source_id,
            "id_pinpoint": pinpoint(source_id),
            "declared_pinpoint": declared_pinpoint(sources[source_id]),
            "title": sources[source_id].get("title"),
        }
        for source_id in sorted(sources)
        if is_mislabelled(source_id, sources[source_id])
    ]


def parse_json_argument(raw: object, name: str) -> dict:
    """Parse a `--meta` / `--identifiers` JSON object argument."""
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"invalid_{name}_json: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid_{name}_json: object expected")
    return value


def _new_record(
    layer: str,
    title: str,
    citation: str,
    url: str,
    tool: str,
    tier: str,
    retrieved_from: str = "",
) -> dict:
    record = {
        "layer": layer,
        "title": title,
        "citation_form": citation,
        "url": url,
        "retrieved_at": events.utc_now(),
        "retrieval_tool": tool,
        "provenance": "agent_saved",
        "tier": tier,
        "raw_path": None,
        "raw_sha256": None,
        "raw_chars": 0,
        "liveness": {"status": "unchecked", "code": None, "checked_at": None},
        "verification": {"us": "n/a", "us_by": None, "eu_syntax_ok": None, "checked_at": None},
        "currency": None,
        "pack": None,
    }
    if retrieved_from:
        # D-192: optional, so a record registered from a public page keeps the shape it always had.
        record["retrieved_from"] = retrieved_from
    return record


def store_raw(
    work_dir: str | os.PathLike,
    layer: str,
    source_id: str,
    raw_file: str | os.PathLike,
    *,
    raw_kind: str = "agent_summary",
) -> dict:
    """Move the agent's temporary raw file to `research/raw/<layer>/<slug>.md` and hash it (§4.3).

    D-200: the returned `raw_kind` is the seam `mf sources save` (Task 4) will use; `register_source`
    is its only caller today.
    """
    src = Path(raw_file)
    if not src.is_file():
        raise ValueError(f"raw_file_not_found: {src}")
    payload = prepare_raw(src.read_bytes())
    target = Path(work_dir) / RAW_DIR / layer / f"{source_id}.md"
    state_io.write_bytes_atomic(target, payload)
    if src.absolute() != target.absolute():
        try:
            src.unlink()
        except OSError:  # a read-only source stays where it is; the copy is authoritative
            pass
    return raw_fields(work_dir, target, payload, raw_kind)


def raw_fields(work_dir: str | os.PathLike, target: Path, payload: bytes, raw_kind: str) -> dict:
    """The four fields a stored raw text writes into its record (§4.3, D-200).

    One function for both storers — `store_raw` for the agent's file, `run_save` for the text the
    code fetched and published itself (D-199) — so the two can never disagree about a record.
    """
    return {
        "raw_path": stepctx.rel_path(work_dir, target),
        "raw_sha256": state_io.sha256_bytes(payload),
        "raw_chars": len(payload.decode("utf-8-sig", errors="replace")),
        "raw_kind": raw_kind,
    }


def read_raw_text(work_dir: str | os.PathLike, record: dict) -> str | None:
    """Text of a source's raw file, or None when it has none."""
    raw_path = record.get("raw_path")
    if not raw_path:
        return None
    path = Path(work_dir) / raw_path
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8-sig", errors="replace")


# --- register -------------------------------------------------------------


def register_source(
    work_dir: str | os.PathLike,
    *,
    layer: str,
    title: str,
    citation: str,
    url: str = "",
    tool: str = "unknown",
    tier: str = "supporting",
    raw_file: str | os.PathLike | None = None,
    raw_kind: str = "agent_summary",
    meta: dict | None = None,
    identifiers: dict | None = None,
    source_id: str | None = None,
) -> dict:
    """Idempotent registration under `sources.lock`; refuses after the freeze (M6, §5.3)."""
    if layer not in LAYERS:
        raise ValueError(f"unknown_layer: {layer!r}")
    if tier not in TIERS:
        raise ValueError(f"unknown_tier: {tier!r}")
    if not str(title).strip():
        raise ValueError("empty_title")
    if not str(citation).strip():
        raise ValueError("empty_citation_form")
    if not str(url).strip() and not str(citation).strip():
        raise ValueError("url_or_citation_required")

    # D-192: from here on `url` means the address a client may be given. An endpoint address never
    # becomes one — it is kept in `retrieved_from` and answered for with a warning. The citation
    # still satisfies `url_or_citation_required`, so the registration itself never fails.
    clean_url, retrieved_from = public_url(url)
    url_refused = bool(str(url).strip()) and not clean_url
    # A4: `url` is not the only field an address travels in. The title, the citation form and every
    # string under `--meta`/`--identifiers` are printed by the docx, `deliverable.md`,
    # `source-pack.md` and the dashboard, so they go through the same scrub as the raw text.
    title = scrub_urls(str(title))
    citation = scrub_urls(str(citation))
    meta = scrub_values(meta) if meta else meta
    identifiers = scrub_values(identifiers) if identifiers else identifiers
    work_dir = Path(work_dir)
    with sources_lock(work_dir):
        # The freeze holds `sources.lock` for its whole transaction, so this check cannot race it.
        if is_frozen(work_dir):
            return {"errors": ["sources_frozen"], "hint": SOURCES_FROZEN_HINT}
        registry = read_registry(work_dir)
        sources = registry["sources"]
        explicit_id = slugify(source_id) if source_id else None
        existing_id = None
        if explicit_id and explicit_id in sources:
            # D34-04: an occupied `source_id` is never re-slugged into `<id>-2` and never overwritten.
            # D-143: the check runs before the dedup key of rule 5 can route the call into the update
            # branch — a repeat registration that matches by url is exactly how other raw bytes used
            # to overwrite the record under the same explicit id and answer `idempotent: true`.
            held = sources[explicit_id]
            if raw_file and raw_kind_of(held) == "full_text":
                # D-200: `full_text` is text saved by code (`mf sources save`); the agent's
                # `--raw-file` never overwrites it, with same bytes or different ones. Refused
                # before anything is written — and before the collision guard, whose
                # "register under its own id" hint is the wrong advice for code-saved text.
                return {
                    "errors": [f"source_is_code_saved: {explicit_id} holds text saved by mf sources save"],
                    "source_id": explicit_id,
                    "hint": "raise the text with `mf sources save` over the same record, "
                    "or register it under its own id",
                }
            incoming_sha = None
            if raw_file and Path(raw_file).is_file():
                # D-163: `store_raw` records the converted text, so the repeat check hashes the
                # same normalised bytes — identical HTML re-registered under its id is a no-op.
                incoming_sha = state_io.sha256_bytes(prepare_raw(Path(raw_file).read_bytes()))
            held_sha = held.get("raw_sha256") or None
            if held_sha is None and incoming_sha is None:
                # D-206: a citation-only or `background` record carries no hash on either side, so
                # the hash comparison alone cannot tell a re-registration from a stranger's text —
                # the dedup identity decides instead. Both keys use the incoming layer, so a layer
                # difference is not mistaken for a different source and falls through to the
                # layer-mismatch check below; equal keys take today's update branch, different
                # keys the `source_id_collision` answer, and nothing is written.
                if dedup_key(layer, held.get("url"), held.get("citation_form")) != dedup_key(
                    layer, clean_url, citation
                ):
                    return {
                        "errors": [f"source_id_collision: {explicit_id} already holds {held['title']!r}"],
                        "source_id": explicit_id,
                        "held_title": held["title"],
                        "held_raw_sha256": held.get("raw_sha256"),
                        "hint": "register the new text under its own id, or fix the existing record",
                    }
            elif held_sha != incoming_sha:
                return {
                    "errors": [f"source_id_collision: {explicit_id} already holds {held['title']!r}"],
                    "source_id": explicit_id,
                    "held_title": held["title"],
                    "held_raw_sha256": held.get("raw_sha256"),
                    "hint": "register the new text under its own id, or fix the existing record",
                }
            if held.get("layer") != layer:
                # Fix round 2: a record's layer is immutable. The update branch below keeps the old
                # layer while `store_raw` would file the text under the newly named one and the
                # answer would report that one — three places, three answers. Refused before
                # anything is written, like the different-bytes case above.
                return {
                    "errors": [f"source_id_layer_mismatch: {explicit_id} is registered under {held['layer']!r}"],
                    "source_id": explicit_id,
                    "held_layer": held["layer"],
                    "held_title": held["title"],
                    "hint": (
                        f"a record never changes layer: re-register with --layer {held['layer']}, "
                        "or register this text under its own id"
                    ),
                }
            # Same bytes, same layer, same id: an update of that record, not a dead end. D-192 fix
            # round 1 — the early return this replaces skipped the metadata update and the common
            # answer builder, so `--id X --url <endpoint>` recorded no `retrieved_from` and answered
            # without `url_not_public`. The guard above still refuses *different* bytes.
            existing_id = explicit_id

        if existing_id is None:
            key = dedup_key(layer, clean_url, citation)
            for candidate_id, record in sources.items():
                if dedup_key(record["layer"], record.get("url"), record.get("citation_form")) == key:
                    existing_id = candidate_id
                    break

        if existing_id is not None and existing_id != explicit_id and raw_file:
            # D-200: the dedup path reaches a held record without the explicit-`--id` guard above,
            # and the same rule applies — code-saved text is never overwritten by the agent.
            held = sources[existing_id]
            if raw_kind_of(held) == "full_text":
                return {
                    "errors": [f"source_is_code_saved: {existing_id} holds text saved by mf sources save"],
                    "source_id": existing_id,
                    "hint": "raise the text with `mf sources save` over the same record, "
                    "or register it under its own id",
                }

        if existing_id is None:
            new_id = explicit_id or unique_slug(slugify(title), set(sources))
            record = _new_record(layer, title, citation, clean_url, tool, tier, retrieved_from)
            sources[new_id] = record
            created = True
            existing_id = new_id
        else:
            record = sources[existing_id]
            record["title"] = title
            record["citation_form"] = citation
            record["tier"] = tier
            record["retrieval_tool"] = tool
            if clean_url:
                record["url"] = clean_url
            else:
                # A5b: an endpoint or token url written before this rule does not survive an update
                # just because the incoming call carried no url of its own.
                held_url, held_from = public_url(record.get("url"))
                record["url"] = held_url
                retrieved_from = retrieved_from or held_from
            if retrieved_from:
                # An endpoint url never clears the public address a previous registration found.
                record["retrieved_from"] = retrieved_from
            created = False

        if identifiers:
            merged = dict(record.get("identifiers") or {})
            merged.update({k: v for k, v in identifiers.items() if v})
            if merged:
                record["identifiers"] = merged
        if meta:
            merged_meta = dict(record.get("meta") or {})
            merged_meta.update(meta)
            record["meta"] = merged_meta
        if raw_file:
            record.update(store_raw(work_dir, layer, existing_id, raw_file, raw_kind=raw_kind))
            record["provenance"] = "agent_saved"

        write_registry(work_dir, registry)

    result = dict(sources[existing_id])
    answer = {
        "source_id": existing_id,
        "created": created,
        "idempotent": not created,
        "layer": layer,
        "raw_path": result.get("raw_path"),
        "raw_sha256": result.get("raw_sha256"),
        "raw_chars": result.get("raw_chars"),
        "raw_kind": result.get("raw_kind"),
        "provenance": result.get("provenance"),
        "tier": result.get("tier"),
    }
    if url_refused:
        # A1: a host that could not be canonicalised leaves no `retrieved_from` either, and the
        # caller still has to hear that the url it passed did not become the source's address.
        answer["warnings"] = [URL_NOT_PUBLIC]
        answer["hint"] = URL_NOT_PUBLIC_HINT
    return answer


def run_register(args: argparse.Namespace) -> dict:
    """`mf sources register` (§4.3: mandatory for critical/supporting findings)."""
    try:
        meta = parse_json_argument(args.meta, "meta")
        identifiers = parse_json_argument(args.identifiers, "identifiers")
    except ValueError as exc:
        return {"errors": [str(exc)]}
    return register_source(
        args.workdir,
        layer=args.layer,
        title=args.title,
        citation=args.citation,
        url=args.url or "",
        tool=args.tool,
        tier=args.tier,
        raw_file=args.raw_file,
        raw_kind=args.raw_kind,
        meta=meta,
        identifiers=identifiers,
        source_id=args.id,
    )


# --- pack --freeze --------------------------------------------------------


def load_findings(work_dir: str | os.PathLike, *, state: dict | None = None) -> list[dict]:
    """Every `research/<layer>.json` findings document, in a deterministic file order (§4.3).

    D-41: a findings file that drifted from `published[]` raises `OutputModifiedAfterPublish` —
    the freeze must not snapshot bytes nobody published.
    """
    research = Path(work_dir) / "research"
    documents = []
    if not research.is_dir():
        return documents
    for path in sorted(research.glob("*.json")):
        try:
            data = stepctx.read_published(work_dir, stepctx.rel_path(work_dir, path), state=state)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("issues"), list) and data.get("layer"):
            documents.append(data)
    return documents


def load_currency(work_dir: str | os.PathLike, *, state: dict | None = None) -> dict[str, dict]:
    """`{source_id: {status, note}}` from `research/currency.json` (phase 9 output; D-41 checked)."""
    path = Path(work_dir) / CURRENCY_PATH
    if not path.is_file():
        return {}
    try:
        data = stepctx.read_published(work_dir, CURRENCY_PATH, state=state)
    except (OSError, ValueError):
        return {}
    rows = data.get("sources") if isinstance(data, dict) else None
    result: dict[str, dict] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("source_id"):
            result[row["source_id"]] = {
                "status": row.get("status", "unchecked"),
                "note": row.get("note") or "",
                "checked_at": data.get("checked_at"),
            }
    return result


def build_pack_entry(roles: dict, weights: list[str], confidences: list[str], currency_status: str, us: str) -> dict:
    """Project findings onto `pack`: role per issue, strongest weight, lowest confidence (§5.3)."""
    weight = "non_binding"
    for candidate in WEIGHT_ORDER:
        if candidate in weights:
            weight = candidate
    confidence = "high"
    for candidate in reversed(CONFIDENCE_ORDER):
        if candidate in confidences:
            confidence = candidate
    use = use_in_memo(roles, currency_status, us)
    return {
        "role_by_issue": dict(sorted(roles.items())),
        "weight": weight,
        "confidence": confidence,
        "use_in_memo": use,
    }


def use_in_memo(roles: dict, currency_status: str, us: str) -> str:
    """D-02 enum; `do_not_use` currency wins, an `unresolved` US citation cannot be a `rule`."""
    if currency_status in BLOCKING_CURRENCY:
        return "do_not_use"
    mapped = {"risk" if role == "contrary" else role for role in roles.values()}
    chosen = "background"
    for candidate in ROLE_ORDER:
        if candidate in mapped:
            chosen = candidate
            break
    if chosen == "rule" and us == "unresolved":
        # "Не найдено ≠ выдумано": the source stays, but it cannot carry a rule unverified (§5.3).
        chosen = "application"
    return chosen


def build_pack(work_dir: str | os.PathLike, registry: dict, *, state: dict | None = None) -> tuple[dict, dict, list]:
    """Build `source-pack.json` and the registry updates (currency + pack) it implies (§5.3).

    Returns `(pack_document, registry, warnings)`. D-200: first the integrity pass — a record
    whose `raw_kind_of` is `full_text` is checked against the file on disk (edited → `agent_summary`,
    missing → `none`, each with a `full_text_integrity` warning) — so duplicate selection below sees
    the corrected kinds. Records of any other kind are untouched. Non-empty warnings are also
    written into the pack document itself (`integrity_warnings`).

    Fix round 3: a demotion also stamps its evidence into the record (`meta.full_text_integrity`),
    in the same write as the demotion itself, and the pass re-emits a stamped marker when the
    record no longer classifies — so a retry after a crash between the registry write and the
    publication reproduces exactly the interrupted run's warnings.

    D-201: the same pass pins the original of a record that carries one (`raw_original_path`) and
    warns by the same rule — but demotes only a `full_text` record, because a record at `none`
    holds no text to lose. Each file is hashed exactly once per freeze.
    """
    sources = registry["sources"]
    currency = load_currency(work_dir, state=state)
    # D-200 fix round 2: the freeze believes the file BEFORE duplicates are selected, so
    # `canonical_of` sees the corrected kinds — a `full_text` record whose file vanished must not
    # win canonicality over the usable copy. One observation per file: the digest classified here
    # is reused for `raw_sha256` and the snapshot row below instead of hashing again.
    warnings: list[dict] = []
    observed: dict[str, str | None] = {}
    originals: dict[str, str | None] = {}
    for source_id in sorted(sources):
        record = sources[source_id]
        kind = raw_kind_of(record)
        # D-201: the original a PDF save kept is pinned by the same rule as the text, and hashed
        # exactly once per freeze — the digest taken here is the one the snapshot row records.
        original_changed = False
        if record.get("raw_original_path"):
            path = Path(work_dir) / record["raw_original_path"]
            if path.is_file():
                digest = state_io.sha256_file(path)
                originals[source_id] = digest
                original_changed = digest != (record.get("raw_original_sha256") or None)
            else:
                originals[source_id] = None
                original_changed = True
        if kind == "full_text":
            # D-200: the recorded sha is what the code saved; any other bytes — or no file at
            # all — mean the text is no longer that document. A null recorded digest never equals
            # the file's digest, so it demotes like any mismatch.
            held_sha = record.get("raw_sha256") or None
            path = Path(work_dir) / (record.get("raw_path") or "") if record.get("raw_path") else None
            if path is None or not path.is_file():
                now = "none"
                observed[source_id] = None
            else:
                digest = state_io.sha256_file(path)
                observed[source_id] = digest
                now = "agent_summary" if digest != held_sha else None
            if now is None and original_changed:
                # D-201: a text layer that still matches, over an original that no longer does, is
                # no longer the document the code saved either.
                now = "agent_summary"
            if now is not None:
                record["raw_kind"] = now
                # Fix round 3: the evidence is part of the same write as the demotion — `meta`
                # is open by design, so no schema change and no effect on old registries.
                record.setdefault("meta", {})["full_text_integrity"] = {"was": "full_text", "now": now}
                warnings.append(
                    {"code": FULL_TEXT_INTEGRITY, "source_id": source_id, "was": "full_text", "now": now}
                )
            continue
        marker = (record.get("meta") or {}).get("full_text_integrity")
        if isinstance(marker, dict) and marker.get("was") == "full_text" and marker.get("now") in (
            "agent_summary",
            "none",
        ):
            # Fix round 3: a retry after a crash between the registry write and the publication —
            # the record no longer classifies, but the stamped evidence re-emits the warning.
            warnings.append(
                {
                    "code": FULL_TEXT_INTEGRITY,
                    "source_id": source_id,
                    "was": "full_text",
                    "now": marker["now"],
                }
            )
        elif original_changed:
            # D-201: there is nothing to demote — a record at `none` holds no text to lose — but
            # the freeze says out loud that the original is not the file it pinned. One warning per
            # source, so the stamped evidence above wins when a record carries both.
            warnings.append({"code": FULL_TEXT_INTEGRITY, "source_id": source_id, "was": kind, "now": kind})
    # D34-04: duplicates collapse into one canonical id before anything is projected onto the pack.
    merged, contradictory = merge_map(work_dir, sources)
    if contradictory:
        raise ContradictoryDuplicates(contradictory)
    kept = [source_id for source_id in sorted(sources) if source_id not in merged]
    roles: dict[str, dict] = {sid: {} for sid in sources}
    weights: dict[str, list[str]] = {sid: [] for sid in sources}
    confidences: dict[str, list[str]] = {sid: [] for sid in sources}

    for document in load_findings(work_dir, state=state):
        for issue in document.get("issues") or []:
            issue_id = issue.get("issue_id")
            for finding in issue.get("findings") or []:
                source_id = merged.get(finding.get("source_id"), finding.get("source_id"))
                if source_id not in sources or not issue_id:
                    continue
                roles[source_id].setdefault(issue_id, finding.get("role", "background"))
                weights[source_id].append(finding.get("weight", "non_binding"))
                confidences[source_id].append(finding.get("confidence", "low"))

    for source_id in merged:
        sources[source_id]["pack"] = None

    entries = []
    for source_id in kept:
        record = sources[source_id]
        row = currency.get(source_id)
        if row:
            record["currency"] = {
                "status": row["status"],
                "checked_at": _as_timestamp(row.get("checked_at")),
                "note": row.get("note") or None,
            }
        currency_status = (record.get("currency") or {}).get("status", "unchecked")
        us = (record.get("verification") or {}).get("us", "n/a")
        pack = build_pack_entry(roles[source_id], weights[source_id], confidences[source_id], currency_status, us)
        record["pack"] = pack
        entry = {
            "source_id": source_id,
            "layer": record["layer"],
            "title": record["title"],
            "citation_form": record["citation_form"],
            "url": record.get("url", ""),
            "tier": record["tier"],
            "currency_status": currency_status,
            "verification_us": us,
            "pack": pack,
        }
        if record.get("raw_kind") in RAW_KINDS:
            # D-200: after the integrity check, so the pack records the corrected value.
            entry["raw_kind"] = record["raw_kind"]
        if record.get("identifiers"):
            entry["identifiers"] = dict(record["identifiers"])
        if record.get("retrieved_at"):
            entry["retrieved_at"] = record["retrieved_at"]
        if record.get("retrieved_from"):
            # D-192: the annex needs the database's name after the freeze; `render_source_pack`
            # prints seven named columns, so the endpoint never reaches the client's copy.
            entry["retrieved_from"] = record["retrieved_from"]
        entries.append(entry)

    snapshot = []
    for source_id in kept:
        record = sources[source_id]
        if source_id in observed:
            # Fix round 2: reuse the classified digest — hashing again could pin bytes the check
            # never saw. A record whose file vanished keeps today's behaviour (null row, the
            # registry sha untouched).
            digest = observed[source_id]
            if digest is not None:
                record["raw_sha256"] = digest
                path = Path(work_dir) / (record.get("raw_path") or "")
                if path.is_file():
                    record["raw_chars"] = len(path.read_text(encoding="utf-8-sig", errors="replace"))
        else:
            digest = None
            raw_path = record.get("raw_path")
            if raw_path:
                path = Path(work_dir) / raw_path
                if path.is_file():
                    digest = state_io.sha256_file(path)
                    record["raw_sha256"] = digest
                    record["raw_chars"] = len(path.read_text(encoding="utf-8-sig", errors="replace"))
        row = {"source_id": source_id, "raw_sha256": digest}
        original = originals.get(source_id)
        if original is not None:
            # D-201: the digest taken by the integrity pass above, never a second hash of the same
            # file. A vanished original writes no row at all, exactly as a vanished text writes no
            # registry sha — there is nothing on disk to pin, and `publish` exports nothing.
            record["raw_original_sha256"] = original
            row["raw_original_sha256"] = original
        snapshot.append(row)

    pack_document = {
        "schema_version": 2,
        "frozen_at": events.utc_now(),
        "snapshot": snapshot,
        "entries": entries,
    }
    if merged:
        pack_document["merged_into"] = dict(sorted(merged.items()))
    if warnings:
        # Fix round 2: the evidence lives in the frozen pack, not only in the step result — an
        # interrupted-then-retried freeze and a replay recover it from here.
        pack_document["integrity_warnings"] = warnings
    return pack_document, registry, warnings


def _as_timestamp(value: object) -> str | None:
    """Accept the `YYYY-MM-DD` of `currency.json` and widen it to the registry timestamp form."""
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text + "T00:00:00Z"
    return text


def run_pack(args: argparse.Namespace) -> dict:
    """`mf sources pack --freeze --step` — one transaction under `state.lock` -> `sources.lock` (M6)."""
    if not args.freeze:
        return {"errors": ["freeze_required"], "hint": "`sources pack` exists only as `--freeze`"}
    work_dir = Path(args.workdir)
    args_key = "sources pack --freeze"
    state = state_io.read_state(work_dir)
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": identity["errors"], "reason": identity.get("reason")}
    if identity["status"] == stepctx.STATUS_CLOSED:
        result = dict(identity.get("result") or {})
        result["already_done"] = True
        return result

    with state_io.FileLock(state_io.lock_path(work_dir, "state")):
        with sources_lock(work_dir):
            existing = read_pack(work_dir)
            if existing is not None:
                # Replay (§2.2): the snapshot file is authoritative, only the state write is missing.
                entry = {
                    "canonical_path": PACK_PATH,
                    "sha256": state_io.sha256_file(pack_path(work_dir)),
                    "by": "command",
                    "step_id": args.step,
                    "at": events.utc_now(),
                }
                result = _pack_result(existing, replayed=True)
                _close_freeze(work_dir, args, result, entry, args_key)
                return result

            registry = read_registry(work_dir)
            try:
                # D-41: the freeze snapshots only inputs that still match `published[]`.
                pack_document, registry, _ = build_pack(work_dir, registry, state=state)
            except stepctx.OutputModifiedAfterPublish as exc:
                return stepctx.drift_result(exc)
            except ContradictoryDuplicates as exc:
                return {
                    "errors": [
                        "contradictory_duplicate_group: "
                        + "; ".join(",".join(group["source_ids"]) for group in exc.groups)
                    ],
                    "contradictory": exc.groups,
                    "hint": "these ids share an identifier but their raw texts are different articles",
                }
            schema.validate_or_raise(pack_document, "source-pack")
            write_registry(work_dir, registry)

            stepctx.stage_input(work_dir, args.step, args.attempt, registry_path(work_dir))
            stepctx.stage_input(work_dir, args.step, args.attempt, Path(work_dir) / CURRENCY_PATH)
            for document in sorted((Path(work_dir) / "research").glob("*.json")):
                stepctx.stage_input(work_dir, args.step, args.attempt, document)
            work_file = stepctx.stage_result(
                work_dir,
                args.step,
                args.attempt,
                "source-pack.json",
                state_io.dumps(pack_document).encode("utf-8"),
            )
            entry = stepctx.publish_file(work_dir, work_file, PACK_PATH, step_id=args.step)
            result = _pack_result(pack_document, replayed=False)
            _close_freeze(work_dir, args, result, entry, args_key)
    return result


def _pack_result(pack_document: dict, *, replayed: bool) -> dict:
    # Fix round 2: warnings come from the pack document itself, so the fresh freeze and the replay
    # answer the same thing — and an interrupted-then-retried freeze recovers the evidence.
    return {
        "source_pack_path": PACK_PATH,
        "frozen_at": pack_document.get("frozen_at"),
        "sources": len(pack_document.get("snapshot") or []),
        "entries": len(pack_document.get("entries") or []),
        "with_raw": sum(1 for row in pack_document.get("snapshot") or [] if row.get("raw_sha256")),
        "merged": len(pack_document.get("merged_into") or {}),
        "sources_frozen": True,
        "replayed": replayed,
        "warnings": list(pack_document.get("integrity_warnings") or []),
    }


def _close_freeze(work_dir: Path, args: argparse.Namespace, result: dict, entry: dict, args_key: str) -> None:
    def mutate(state: dict) -> None:
        state["sources_frozen"] = True

    stepctx.close_step(
        work_dir,
        args.step,
        args.attempt,
        result,
        phase=args.phase,
        args_key=args_key,
        published=[entry],
        mutate=mutate,
    )
    events.append_event(
        work_dir,
        "sources_frozen",
        "cli",
        {"sources": result.get("sources"), "replayed": result.get("replayed")},
        step_id=args.step,
    )


# --- digest ---------------------------------------------------------------


def collect_exceptions(work_dir: str | os.PathLike, state: dict, ui: str = "en") -> list[dict]:
    """Gate-11 exceptions of §2.4: critical sources in doubt, warnings, exhausted MCP budget."""
    registry = read_registry(work_dir)
    pack = read_pack(work_dir) or {}
    pack_by_id = {row["source_id"]: row for row in pack.get("entries") or [] if isinstance(row, dict)}
    merged = pack.get("merged_into") or {}
    exceptions: list[dict] = []

    # D-200 fix round 2: the freeze demotion surfaces here too, for every tier, through the pack
    # label — read from the frozen pack, which survives interruption and replay, never from the
    # step result (whose `result_ref` shapes must stay none of this function's business).
    for warning in pack.get("integrity_warnings") or []:
        if not isinstance(warning, dict) or warning.get("code") != FULL_TEXT_INTEGRITY:
            continue
        source_id = warning.get("source_id")
        if not source_id:
            continue
        exceptions.append(
            {
                "kind": FULL_TEXT_INTEGRITY,
                "source_id": source_id,
                "detail": i18n.t(ui, "memo.labels.full_text_integrity_note", source_id=source_id),
            }
        )

    for source_id in sorted(registry["sources"]):
        if source_id in merged:
            continue  # D34-04: a collapsed duplicate raises no exception of its own
        record = registry["sources"][source_id]
        entry = pack_by_id.get(source_id, {})
        currency_status = entry.get("currency_status") or (record.get("currency") or {}).get("status", "unchecked")
        us = entry.get("verification_us") or (record.get("verification") or {}).get("us", "n/a")
        use = (entry.get("pack") or record.get("pack") or {}).get("use_in_memo")
        if record.get("tier") != "critical":
            continue
        if us == "unresolved":
            exceptions.append(
                {
                    "kind": "unresolved_citation",
                    "source_id": source_id,
                    "detail": f"US citation unresolved ({record['citation_form']})",
                }
            )
        if currency_status in EXCEPTION_CURRENCY:
            exceptions.append(
                {"kind": "currency", "source_id": source_id, "detail": f"currency: {currency_status}"}
            )
        if use == "do_not_use":
            exceptions.append({"kind": "do_not_use", "source_id": source_id, "detail": "marked do_not_use"})

    for issue, ids in conflicting_authorities(pack).items():
        exceptions.append(
            {
                "kind": "conflicting_authority",
                "source_id": None,
                "detail": f"issue {issue}: contrary authority {', '.join(ids)}",
            }
        )

    for warning in state.get("drafting_warnings") or []:
        message = warning if isinstance(warning, str) else warning.get("message", "")
        exceptions.append({"kind": "drafting_warning", "source_id": None, "detail": message})

    calls = ((state.get("progress") or {}).get("mcp_calls")) or {}
    for server in limits.MCP_QUOTA_SERVERS:
        allowed = limits.MCP_PROVIDER_DAILY_LIMITS.get(server, 0)
        used = calls.get(server)
        used = used if isinstance(used, int) else 0
        if allowed and used >= allowed:
            exceptions.append(
                {
                    "kind": "mcp_budget_exhausted",
                    "source_id": None,
                    "detail": f"{server}: {used}/{allowed} calls used",
                }
            )
    return exceptions


def conflicting_authorities(pack: dict) -> dict[str, list[str]]:
    """Issues where one source carries `rule` and another `contrary` (§2.4 «конфликтующие авторитеты»)."""
    by_issue: dict[str, dict[str, list[str]]] = {}
    for entry in pack.get("entries") or []:
        roles = (entry.get("pack") or {}).get("role_by_issue") or {}
        for issue, role in roles.items():
            by_issue.setdefault(issue, {}).setdefault(role, []).append(entry["source_id"])
    conflicts = {}
    for issue, roles in sorted(by_issue.items()):
        if roles.get("rule") and roles.get("contrary"):
            conflicts[issue] = sorted(roles["contrary"])
    return conflicts


def render_digest(
    work_dir: str | os.PathLike, state: dict, exceptions_only: bool, ui: str = "en"
) -> dict:
    """Text of gate 11 (`mf sources digest --exceptions`, §2.4).

    D-176 (sources/preflight): the frame speaks the interface language `ui`
    (`ui.sources.*`); the exception rows stay raw inside it — machine tokens
    (`[kind]`, `source_id`, `tier`, currency status values, `do_not_use`) and the
    memo-language `drafting_warning` rows (D-173b). The default keeps the English
    bytes for the agent-side callers.
    """
    registry = read_registry(work_dir)
    pack = read_pack(work_dir)
    exceptions = collect_exceptions(work_dir, state, ui)
    lines: list[str] = []
    frozen = (
        i18n.t(ui, "ui.sources.digest_frozen")
        if pack
        else i18n.t(ui, "ui.sources.digest_not_frozen")
    )
    lines.append(i18n.t(ui, "ui.sources.digest_head", count=len(registry["sources"]), frozen=frozen))
    lines.append("")

    if not exceptions_only:
        pack_by_id = {row["source_id"]: row for row in (pack or {}).get("entries") or []}
        merged = (pack or {}).get("merged_into") or {}
        for source_id in sorted(registry["sources"]):
            if source_id in merged:
                continue  # D34-04: a collapsed duplicate is not a separate line for the user
            record = registry["sources"][source_id]
            entry = pack_by_id.get(source_id, {})
            use = (entry.get("pack") or record.get("pack") or {}).get("use_in_memo", "-")
            currency_status = entry.get("currency_status") or (record.get("currency") or {}).get(
                "status", "unchecked"
            )
            lines.append(
                f"- [{record['tier']}] {source_id} — {record['citation_form']} "
                f"({record['layer']}; currency: {currency_status}; use: {use})"
            )
        lines.append("")

    if exceptions:
        lines.append(i18n.t(ui, "ui.sources.exceptions_heading"))
        for row in exceptions:
            prefix = f"{row['source_id']} — " if row.get("source_id") else ""
            lines.append(f"- [{row['kind']}] {prefix}{row['detail']}")
    else:
        lines.append(i18n.t(ui, "ui.sources.no_exceptions"))
    lines.append("")
    lines.append(i18n.t(ui, "ui.sources.reply_line"))
    text = "\n".join(lines)
    return {
        "text": text,
        "human": text,
        "exceptions": exceptions,
        "has_exceptions": bool(exceptions),
        "sources": len(registry["sources"]),
        "frozen": bool(pack),
    }


def run_digest(args: argparse.Namespace) -> dict:
    """`mf sources digest [--exceptions]`."""
    state = state_io.read_state_or_none(args.workdir) or {}
    return render_digest(args.workdir, state, bool(args.exceptions), ui=i18n.ui_language(state))


# --- liveness -------------------------------------------------------------


def is_cellar(url: str) -> bool:
    """True for `publications.europa.eu/resource/celex/<CELEX>` (D-146, analysis/38 §1.5)."""
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if host != CELLAR_HOST and not host.endswith("." + CELLAR_HOST):
        return False
    return parts.path.lower().startswith(CELLAR_PATH_PREFIX)


def probe_headers(url: str) -> dict[str, str]:
    """Request headers of one probe: the contact UA, and the `Accept` the host needs (D-146)."""
    headers = {"User-Agent": LIVENESS_USER_AGENT}
    headers.update(CELLAR_HEADERS if is_cellar(url) else {"Accept": DEFAULT_ACCEPT})
    # D-162: the eCFR API refuses (406) any request that does not allow a compressed answer; every
    # other host simply ignores the header, and the caller inflates what comes back compressed.
    headers.setdefault("Accept-Encoding", "gzip")
    return headers


FETCH_SCHEMES: tuple[str, ...] = ("http", "https")
"""D-151: the only two schemes a probe or a fetch may speak."""

MAX_REDIRECT_HOPS = 5
"""D-151: how many `Location` hops one request may follow before it gives up."""

JSON_CONTENT_TYPE = "application/json"
"""D-151: the `Content-Type` of a `--json` body (the Normattiva OpenAPI route takes nothing else)."""


def url_error(url: object) -> str | None:
    """Why this url may not be requested at all, or None when it may (D-151).

    One parser for the whole pipeline, and the one `hooks/permission_gate.py` already uses:
    `urlsplit(...).hostname`, which lower-cases and keeps IDNA labels intact. The hand-rolled split
    this replaces read the *fragment* of `https://outside.example#@govinfo.gov` as the host and
    fetched `outside.example` under the allowlist's blessing. Userinfo is refused outright rather
    than parsed away: nothing here needs credentials in a url, and `@` is exactly what made the two
    parsers disagree.
    """
    text = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(text)
        if parts.scheme.lower() not in FETCH_SCHEMES:
            return "unsupported_scheme"
        if "@" in parts.netloc:
            return "userinfo_not_allowed"
        host = parts.hostname or ""
    except ValueError:
        return "malformed_url"
    if not host:
        return "no_hostname"
    return None


def request_host(url: object) -> str:
    """Lower-case hostname of a url that may be requested; `""` when it may not be (D-151)."""
    if url_error(url) is not None:
        return ""
    return (urllib.parse.urlsplit(str(url).strip()).hostname or "").lower()


class RedirectRefused(urllib.error.URLError):
    """A `Location` this client will not follow (D-151): off-allowlist, unparsable, or too deep.

    `probe_error` reads `error_name` off it, so the report says `redirect_not_allowed: <host>`
    instead of the name of the class urllib happened to raise.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.error_name = f"{reason}: {detail}" if detail else reason
        super().__init__(self.error_name)


class RedirectGuard(urllib.request.HTTPRedirectHandler):
    """Checks every hop before `urlopen` follows it (D-151).

    `urlopen` follows redirects on its own, so validating the url the caller passed proves nothing:
    a 302 from an allow-listed portal to any other host used to be fetched, hashed and saved. Each
    hop is parsed with `request_host`, matched against `allowed` when the caller has an allowlist
    (`mf sources fetch`), and appended to `hops` either way (liveness records, fetch enforces).
    """

    max_repeats = MAX_REDIRECT_HOPS + 1
    max_redirections = MAX_REDIRECT_HOPS + 1
    """urllib's own ceilings, lifted just above `MAX_REDIRECT_HOPS` so this class is the one that
    stops a chain: urllib's answer to a loop is an `HTTPError` carrying the 302, which every caller
    here would read as an ordinary redirect."""

    def __init__(self, allowed: frozenset | None = None, hops: list | None = None) -> None:
        self.allowed = allowed
        self.hops = [] if hops is None else hops

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib contract
        host = request_host(newurl)
        if not host:
            raise RedirectRefused("redirect_not_allowed", url_error(newurl) or "no_hostname")
        self.hops.append(host)
        if len(self.hops) > MAX_REDIRECT_HOPS:
            raise RedirectRefused("too_many_redirects", str(len(self.hops)))
        if self.allowed is not None and not host_on_allowlist(host, self.allowed):
            raise RedirectRefused("redirect_not_allowed", host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(
    url: str,
    method: str,
    timeout: float,
    headers: dict | None = None,
    *,
    data: bytes | None = None,
    allowed: frozenset | None = None,
    hops: list | None = None,
):
    """One request whose redirects are validated hop by hop (D-151); the caller checked the url."""
    request = urllib.request.Request(url, data=data, method=method, headers=headers or probe_headers(url))
    opener = urllib.request.build_opener(RedirectGuard(allowed=allowed, hops=hops))
    return opener.open(request, timeout=timeout)  # noqa: S310 - http/https only, best effort


def header_value(headers: object, name: str) -> str:
    """One response header, case-insensitively, from an `HTTPMessage` or a plain dict."""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    value = getter(name)
    if value is None and isinstance(headers, dict):
        lowered = name.lower()
        value = next((item for key, item in headers.items() if str(key).lower() == lowered), None)
    return str(value or "")


def _inflate(payload: bytes, headers: object) -> bytes:
    """Undo a `Content-Encoding: gzip` answer (D-162); anything else, or a broken stream, is returned as is."""
    if header_value(headers, "Content-Encoding").strip().lower() != "gzip":
        return payload
    try:
        return gzip.decompress(payload)
    except (OSError, EOFError, zlib.error):
        return payload


def challenge_error(code: int | None, headers: object) -> str | None:
    """Name of the interstitial behind a «not served» code, or None (D-146, analysis/38 §7.4 item 2).

    The report must say *why* the document did not arrive: AWS WAF answers 202 with
    `x-amzn-waf-action`, Cloudflare Turnstile answers 403/503 with `Cf-Mitigated`.
    """
    if code == 202 and header_value(headers, AWS_WAF_HEADER):
        return "aws_waf_challenge"
    if code in CLOUDFLARE_CODES and header_value(headers, CLOUDFLARE_HEADER):
        return "cloudflare_challenge"
    return None


def visible_text(payload: bytes) -> bytes:
    """The body with comments, scripts, styles and tags removed, whitespace collapsed (D-146)."""
    stripped = _TAG_RE.sub(b" ", _SCRIPT_STYLE_RE.sub(b" ", _COMMENT_RE.sub(b" ", payload)))
    return b" ".join(stripped.split())


def visible_text_ratio(payload: bytes) -> float:
    """Share of a body left after comments, scripts, styles and tags are stripped (D-146)."""
    if not payload:
        return 0.0
    return len(visible_text(payload).replace(b" ", b"")) / len(payload)


def is_markup(payload: bytes, content_type: str = "") -> bool:
    """True when the body is an html page — the only kind the size/ratio rules can judge (D-149).

    The declared type decides when there is one, so a JSON answer that happens to quote `<html` in a
    field is never read as a page; without it the first kilobyte is sniffed.
    """
    kind = str(content_type or "").split(";")[0].strip().lower()
    if kind:
        return kind in MARKUP_CONTENT_TYPES
    return bool(_HTML_SNIFF_RE.search(payload[:1024]))


_BLOCK_TAGS = frozenset(
    "p div br li ul ol h1 h2 h3 h4 h5 h6 tr table section article blockquote pre dt dd dl header footer "
    "main nav aside figure figcaption hr title".split()
)
_SKIP_TAGS = frozenset("script style head noscript template svg".split())

_WS_RUN_RE = re.compile(r"[ \t\r\f\v]+")
_CHARSET_RE = re.compile(rb"(?:charset|encoding)\s*=\s*[\"']?\s*([A-Za-z0-9_.\-:]+)", re.IGNORECASE)


class _TextExtractor(HTMLParser):
    """Block tags become line breaks, `script`/`style`/`head` vanish, entities are decoded (D-163)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: D102 - HTMLParser contract
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: D102
        if tag in _SKIP_TAGS:
            self._skip = max(self._skip - 1, 0)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: D102
        if not self._skip:
            self.parts.append(data)


def _declared_charset(payload: bytes) -> str | None:
    """Charset named in the first 2 KB (`<meta charset>`, XML declaration), or None (D-163)."""
    match = _CHARSET_RE.search(payload[:2048])
    if not match:
        return None
    try:
        return codecs.lookup(match.group(1).decode("ascii")).name
    except (LookupError, UnicodeDecodeError):
        return None


def markup_to_text(payload: bytes, content_type: str = "") -> bytes | None:
    """Plain text of an HTML/XHTML body, or None when the body is not markup (D-163).

    Article headings (`<p class="oj-ti-art">Article 9</p>` on Cellar, `<h2>Article 9</h2>` elsewhere)
    end up on a line of their own, which is what `article_spans` and `quote extract` need; the
    delivered `sources/*.txt` become readable for the same reason.
    """
    if not is_markup(payload, content_type):
        return None
    text = payload.decode(_declared_charset(payload) or "utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    joined = "".join(parser.parts)
    # Collapse ASCII whitespace runs only, then turn every nbsp into one plain space: archived
    # AI Act pages write `Article&#160;N` and Cellar writes `1.` + three nbsp, while the
    # article-heading expressions accept only spaces/tabs — so `&#160;` must become spaces here,
    # one character for one, keeping paragraph spacing and character offsets intact (fix wave).
    lines = [_WS_RUN_RE.sub(" ", line).replace(" ", " ").strip(" \t\r\f\v") for line in joined.splitlines()]
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return ("\n".join(out).strip() + "\n").encode("utf-8")


def is_redirect_shell(payload: bytes) -> bool:
    """True for a small markup body whose only content is a redirect (D-149, analysis/39 §9.2).

    The fourth shape of a false-positive 200, after the curia shell, the eCFR stub and the Normattiva
    landing page: `courdecassation.fr/decision/<id>` answers 255 bytes of
    `window.location.href='/redirect_<token>/…'` and a `<noscript>` line. Its text ratio is 0.149, so
    only the redirect itself gives it away.
    """
    if len(payload) >= limits.LIVENESS_MAX_SHELL_BYTES:
        return False
    lowered = payload.lower()
    if b"window.location" not in lowered and not _META_REFRESH_RE.search(lowered):
        return False
    return len(visible_text(payload)) < limits.LIVENESS_MIN_ARTICLE_CHARS


def is_access_stub(payload: bytes) -> bool:
    """True when the visible text of a markup body is a bot wall, not a document (D-149)."""
    text = visible_text(payload).lower()
    return any(phrase in text for phrase in CHALLENGE_PHRASES)


def is_interstitial(payload: bytes, content_type: str = "") -> bool:
    """True when a 200 body is a challenge page or a JS shell, not the document (D-146, D-149).

    Four independent rules, because the four measured fakes fail differently, and all four apply to
    markup only — a JSON answer, an XML judgment or a PDF is a document at any size (D-149 reverses
    the unconditional size rule of D-146, which rejected the 9.5 KB `§ 26 BDSG` page, the 7.4 KB
    `legislation.gov.uk` snippet and the RIS JSON, i.e. the preferred address of three routing rows).
    """
    if not is_markup(payload, content_type):
        return False
    if len(payload) < limits.LIVENESS_MIN_BODY_BYTES:
        return True
    if visible_text_ratio(payload) < limits.LIVENESS_MIN_TEXT_RATIO:
        return True
    return is_redirect_shell(payload) or is_access_stub(payload)


def probe_error(exc: BaseException) -> str:
    """Error name of a failed probe: a verifiable TLS failure is named, not classed (D-146).

    `urlopen` wraps the certificate error in `URLError`, so the generic `type(exc).__name__` printed
    `URLError` for a stale Windows root store (analysis/38 §6.2) — indistinguishable from a dead host.
    """
    reason = getattr(exc, "reason", None)
    named = getattr(exc, "error_name", None) or getattr(reason, "error_name", None)
    if named:  # D-151: `redirect_not_allowed: <host>` says which hop, not which class
        return str(named)
    if isinstance(exc, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLCertVerificationError):
        return "tls_certificate"
    return type(exc).__name__


def _unchecked_http(code: int, headers: object = None, hops: object = ()) -> dict:
    """D34-08: the server answered, but not with the document — no verdict, no body, no hash."""
    return {
        "status": "unchecked",
        "code": code,
        "sha256": None,
        "error": challenge_error(code, headers) or f"http_{code}",
        "redirects": list(hops or []),
    }


def probe_url(url: str, *, want_body: bool, timeout: float | None = None) -> dict:
    """HEAD, then GET when a body is needed; every failure is best effort (§5.3)."""
    timeout = limits.LIVENESS_TIMEOUT_SECONDS if timeout is None else timeout
    problem = url_error(url)
    if problem is not None:
        # D-151: the url is parsed once, the way the permission gate parses it, before any request.
        return {"status": "unchecked", "code": None, "sha256": None, "error": problem, "redirects": []}

    code: int | None = None
    final_url = url
    headers: object = None
    content_type = ""
    hops: list = []
    try:
        with _open(url, "HEAD", timeout, hops=hops) as response:
            code = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            headers = response.headers
    except urllib.error.HTTPError as exc:
        code = exc.code
        headers = exc.headers
    except RedirectRefused as exc:
        # D-151: the GET would follow the same hop, so it is never sent.
        return {"status": "unchecked", "code": None, "sha256": None, "error": exc.error_name, "redirects": hops}
    except Exception:  # noqa: BLE001 - liveness never fails the pipeline; the GET below decides
        code = None

    if code in UNCHECKED_HTTP_CODES:
        return _unchecked_http(code, headers, hops)

    if code is not None and code >= 400 and not want_body:
        return {"status": "dead", "code": code, "sha256": None, "error": f"http_{code}", "redirects": hops}

    if not want_body and code is not None:
        status = "redirect" if normalize_url(final_url) != normalize_url(url) else "ok"
        return {"status": status, "code": code, "sha256": None, "error": None, "redirects": hops}

    hops = []  # the GET walks the same chain again; the HEAD hops must not count against the cap
    try:
        with _open(url, "GET", timeout, hops=hops) as response:
            code = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            headers = response.headers
            if code in UNCHECKED_HTTP_CODES:
                return _unchecked_http(code, headers, hops)
            payload = response.read(limits.LIVENESS_MAX_BODY_BYTES)
            payload = _inflate(payload, headers)
            # the same cap fetch applies after inflating (D-162): both sides hash the same prefix
            payload = payload[: limits.LIVENESS_MAX_BODY_BYTES]
            content_type = header_value(headers, "Content-Type")
    except urllib.error.HTTPError as exc:
        if exc.code in UNCHECKED_HTTP_CODES:
            return _unchecked_http(exc.code, exc.headers, hops)
        return {"status": "dead", "code": exc.code, "sha256": None, "error": f"http_{exc.code}", "redirects": hops}
    except RedirectRefused as exc:
        return {"status": "unchecked", "code": code, "sha256": None, "error": exc.error_name, "redirects": hops}
    except Exception as exc:  # noqa: BLE001 - timeouts, DNS, TLS: all best effort
        return {"status": "dead", "code": code, "sha256": None, "error": probe_error(exc), "redirects": hops}

    if code == 200 and is_interstitial(payload, content_type):
        # D-146: a 200 that carries a challenge page or a JS shell is never `ok` and never hashed.
        return {
            "status": "unchecked",
            "code": code,
            "sha256": None,
            "error": "interstitial_suspected",
            "redirects": hops,
        }

    text = markup_to_text(payload, content_type)
    payload = text if text is not None else payload
    status = "redirect" if normalize_url(final_url) != normalize_url(url) else "ok"
    return {
        "status": status,
        "code": code,
        "sha256": state_io.sha256_bytes(payload),
        # A7: `store_raw` hashes the scrubbed text (D-193), so the probe offers that hash too —
        # otherwise an unchanged page carrying a url with an auth parameter reports `changed`.
        "sha256_normalised": state_io.sha256_bytes(prepare_raw(payload)),
        "error": None,
        "redirects": hops,
    }


def url_host(value: object) -> str:
    """Lower-case host of a url or of a bare `example.org` token; `""` when there is none."""
    text = str(value or "").strip().strip("`,;()")
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text, flags=re.IGNORECASE)
    host = text.split("/")[0].split("?")[0].lower()
    host = host[host.rfind("@") + 1 :].split(":")[0]
    return host if "." in host else ""


def host_delay(host: object) -> float:
    """Seconds owed to `host` between two probes of it (D-146, analysis/38 §7.4 item 1)."""
    name = str(host or "").lower()
    for candidate, delay in limits.LIVENESS_HOST_DELAYS.items():
        if name == candidate or name.endswith("." + candidate):
            return float(delay)
    return float(limits.LIVENESS_HOST_DELAY_SECONDS)


def _wait(seconds: float) -> None:
    """The politeness pause itself; the tests replace this function instead of sleeping (D-146)."""
    if seconds > 0:
        time.sleep(seconds)


def body_comparable(record: dict) -> bool:
    """True when `raw_sha256` may be compared with the body fetched from `url` (D34-08).

    Only a byte-for-byte fetch of the same URL (`curl`, `wget`) stores the page itself; an MCP tool
    stores extracted markdown and WebFetch stores the model's rendering, and neither can ever hash
    to the page they came from.
    """
    tool = str(record.get("retrieval_tool") or "").strip()
    if not tool:
        return False
    head, _, rest = tool.partition(" ")
    if head.strip().strip("`").lower() not in BODY_COMPARABLE_TOOLS:
        return False
    host = url_host(record.get("url"))
    named = [item for item in (url_host(token) for token in rest.split()) if item]
    if not named:
        return True
    # `curl edpb.europa.eu` names the domain, the url is `www.edpb.europa.eu`.
    return any(host == item or host.endswith("." + item) or item.endswith("." + host) for item in named)


def begin_step(work_dir: Path, args: argparse.Namespace, args_key: str) -> dict | None:
    """Identity gate of §3.1 for the `--step`-capable utilities (D-28, D-40).

    Without `--step` they stay plain utilities; with it they are ordinary script steps, so a replay
    of a closed identity answers with the stored result instead of hitting the network again.
    """
    step_id = getattr(args, "step", None)
    if not step_id:
        return None
    state = state_io.read_state_or_none(work_dir)
    if not state:
        return None
    attempt = int(getattr(args, "attempt", 1) or 1)
    identity = stepctx.check_identity(state, step_id, attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {
            "errors": list(identity["errors"]),
            "reason": identity.get("reason"),
            "step_id": step_id,
            "attempt": attempt,
        }
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = identity.get("result")
        result = dict(stored) if isinstance(stored, dict) else {"result": stored}
        result["already_done"] = True
        return result
    return None


def finish_step(work_dir: Path, args: argparse.Namespace, result: dict, args_key: str) -> dict:
    """Close the step of a `--step`-capable utility; a no-op without `--step` (D-28)."""
    step_id = getattr(args, "step", None)
    if not step_id:
        return result
    try:
        stepctx.close_step(
            work_dir,
            step_id,
            int(getattr(args, "attempt", 1) or 1),
            result,
            args_key=args_key,
        )
    except stepctx.IdentityMismatch as exc:
        return exc.as_result()
    return result


def run_liveness(args: argparse.Namespace) -> dict:
    """`mf sources liveness` — stdlib HEAD/GET, 10 s, best effort; sha match promotes provenance (M5)."""
    work_dir = Path(args.workdir)
    args_key = f"sources liveness --source={getattr(args, 'source', None) or ''}"
    saved = begin_step(work_dir, args, args_key)
    if saved is not None:
        return saved
    checked: list[dict] = []
    last_probe: dict[str, float] = {}
    with sources_lock(work_dir):
        registry = read_registry(work_dir)
        wanted = [args.source] if args.source else sorted(registry["sources"])
        for source_id in wanted:
            record = registry["sources"].get(source_id)
            if record is None:
                checked.append({"source_id": source_id, "status": "unchecked", "error": "unknown_source"})
                continue
            url = str(record.get("url") or "")
            # D34-08: only a body that came from this url by this tool may be compared with the sha.
            # D-201: a record that kept an original is identified by those bytes — the text layer
            # is a convenience, and nothing here extracts one: the body as served is what the
            # server sends again, so the digest of the original is what may be compared with it.
            comparable = body_comparable(record)
            original_sha = record.get("raw_original_sha256") if comparable else None
            expected = original_sha or (record.get("raw_sha256") if comparable else None)
            if not url:
                record["liveness"] = {"status": "unchecked", "code": None, "checked_at": events.utc_now()}
                checked.append({"source_id": source_id, "status": "unchecked", "error": "no_url"})
                continue
            # D-146: one loop over the registry is one crawler as far as the host is concerned.
            # D-151: the host is the one `probe_url` will really call, or `""` for a url it refuses.
            host = request_host(url)
            since = last_probe.get(host)
            if host and since is not None:
                _wait(host_delay(host) - (time.monotonic() - since))
            probe = probe_url(url, want_body=bool(expected), timeout=args.timeout)
            if host:
                last_probe[host] = time.monotonic()
            status = probe["status"]
            # A7: the registry holds the sha of the scrubbed text; a record written before D-193
            # holds the sha of the body as served. Either equality is the same page.
            # D-201 fix round 3: that licence is for *text* only. An original has no normalised
            # form — it is bytes — so a PDF whose new body differs but scrubs to the same value is
            # `changed`, and is never promoted to `confirmed` on the strength of a normalisation
            # that was never applied to the file on disk.
            offered = (probe["sha256"],) if original_sha else (probe["sha256"], probe.get("sha256_normalised"))
            digests = {value for value in offered if value}
            if expected and digests and expected not in digests:
                status = "changed"
            record["liveness"] = {
                "status": status,
                "code": probe["code"],
                "checked_at": events.utc_now(),
            }
            if expected and expected in digests:
                record["provenance"] = "confirmed"
            checked.append(
                {
                    "source_id": source_id,
                    "status": status,
                    "code": probe["code"],
                    "provenance": record["provenance"],
                    "error": probe["error"],
                }
            )
        write_registry(work_dir, registry)
    by_status: dict[str, int] = {}
    for row in checked:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    result = {
        "checked": checked,
        "count": len(checked),
        "by_status": dict(sorted(by_status.items())),
        "confirmed": sum(1 for row in checked if row.get("provenance") == "confirmed"),
    }
    return finish_step(work_dir, args, result, args_key)


# --- fetch ----------------------------------------------------------------

FETCH_TOOL = "mf-fetch"
"""D-149: the `retrieval_tool` an agent records for a body this command saved (`mf-fetch <host>`)."""

FETCH_DIRNAME = "fetch"
"""Sub-directory of `research/raw/` a fetch without `--layer` and without `--out` writes into."""

FETCH_METHODS: tuple[str, ...] = ("GET", "POST")
"""D-151: the two methods `mf sources fetch` speaks.

`POST` exists for one prescribed route — the Normattiva OpenAPI of D-148, whose stateless pinpoint
is `POST /atto/dettaglio-atto-urn` with a JSON body. Nothing here writes to a portal: the body
carries an URN, and the answer is a document.
"""

FETCH_EXTENSIONS: dict[str, str] = {
    "application/json": ".json",
    "application/ld+json": ".json",
    "application/pdf": ".pdf",
    "application/rdf+xml": ".xml",
    "application/xhtml+xml": ".xhtml",
    "application/xml": ".xml",
    "text/html": ".html",
    "text/json": ".json",
    "text/plain": ".txt",
    "text/xml": ".xml",
}
DEFAULT_FETCH_EXTENSION = ".bin"

_LAST_FETCH: dict[str, float] = {}
"""Monotonic time of the last fetch of a host **in this process** (D-149 politeness, D-146 delays).

One `mf` invocation is one process, so a fresh call owes nothing; what this stops is a loop inside
one run — the same thing `run_liveness` keeps with its local `last_probe`.
"""


def allowlist_hosts(root: str | os.PathLike | None = None) -> frozenset:
    """Hosts of `hooks/allowlist.txt` — the list `permission_gate.py --mode fetch` enforces (§8.1)."""
    from . import docs_render

    hosts = docs_render.read_allowlist(Path(root) if root else None) or []
    return frozenset(str(host).lower() for host in hosts)


def host_on_allowlist(host: object, hosts: frozenset | None = None) -> bool:
    """Exact host or a dot-anchored suffix of one, exactly like `permission_gate.host_allowed`."""
    name = str(host or "").lower()
    if not name:
        return False
    hosts = allowlist_hosts() if hosts is None else hosts
    return name in hosts or any(name.endswith("." + entry) for entry in hosts)


def fetch_headers(url: str, *, accept: object = None, lang: object = None) -> dict[str, str]:
    """The liveness headers of D-146 (Cellar included), with the per-call overrides of D-149."""
    headers = probe_headers(url)
    if accept:
        headers["Accept"] = str(accept)
    if lang:
        headers["Accept-Language"] = str(lang)
    return headers


def fetch_extension(content_type: object) -> str:
    """`.json` / `.xml` / `.pdf` / `.html` … from the declared type; `.bin` when it is unknown."""
    kind = str(content_type or "").split(";")[0].strip().lower()
    return FETCH_EXTENSIONS.get(kind, DEFAULT_FETCH_EXTENSION)


def fetch_name(url: str, content_type: object = "", *, method: str = "GET", body: bytes | None = None) -> str:
    """Deterministic file name of a fetch without `--out`: host and path, plus 8 hex of the call.

    The digest is what keeps two RIS API calls that differ only in their query string apart, and what
    makes a repeated fetch of one url overwrite its own file instead of growing a second one. D-151:
    a POST addresses one endpoint with many bodies — `/atto/dettaglio-atto-urn` is one url per
    article — so the method and the body join the digest; a plain GET keeps the name it always had.
    """
    parts = urllib.parse.urlsplit(url)
    stem = slugify((parts.hostname or "") + "-" + parts.path, fallback=FETCH_DIRNAME)
    seed = normalize_url(url).encode("utf-8")
    if method.upper() != "GET" or body:
        seed += b"\n" + method.upper().encode("ascii", "ignore") + b"\n" + (body or b"")
    digest = state_io.sha256_bytes(seed)[:8]
    return f"{stem}-{digest}{fetch_extension(content_type)}"


def fetch_target(
    work_dir: str | os.PathLike,
    url: str,
    *,
    out: object = None,
    layer: object = None,
    content_type: object = "",
    method: str = "GET",
    body: bytes | None = None,
) -> Path:
    """Where the body is written: `--out` under `research/raw/`, else `research/raw/<layer|fetch>/`."""
    if out:
        relative = str(out).replace("\\", "/").strip("/")
        if relative.startswith(RAW_DIR + "/"):
            relative = relative[len(RAW_DIR) + 1 :]
        if not relative or ":" in relative or ".." in relative.split("/"):
            raise ValueError(f"invalid_out_path: {out}")
        return Path(work_dir) / RAW_DIR / relative
    name = fetch_name(url, content_type, method=method, body=body)
    return Path(work_dir) / RAW_DIR / str(layer or FETCH_DIRNAME) / name


def _fetch_answer(
    url: str,
    code: int | None,
    headers: object,
    payload: bytes,
    final_url: str,
    hops: object = (),
    short: bool = False,
) -> dict:
    """Classify one answer the way liveness does, and say whether the body may be cited (D-149).

    `truncated` is «this is not the whole body», from any of three causes: our own ceiling, a body
    that ended before the `Content-Length` the server declared (`short`, measured on the bytes as
    they arrived — before `_inflate`, since the header counts the compressed ones), and a
    `206 Partial Content`, which says so itself. A page cut short reads like a whole document —
    the act ends at «определила:» with the operative part simply absent — and `save` refuses every
    one of the three rather than certify a part of a document as the whole of it (D-199).
    """
    cap = limits.LIVENESS_MAX_BODY_BYTES
    truncated = short or code == 206 or len(payload) > cap
    payload = payload[:cap]
    content_type = header_value(headers, "Content-Type")
    interstitial = is_interstitial(payload, content_type)
    error = challenge_error(code, headers)
    if error is None and code in UNCHECKED_HTTP_CODES:
        error = f"http_{code}"
    if error is not None:
        status = "unchecked"
    elif code is not None and code >= 400:
        status, error = "dead", f"http_{code}"
    elif interstitial:
        status, error = "unchecked", "interstitial_suspected"
    else:
        status = "redirect" if normalize_url(final_url) != normalize_url(url) else "ok"
    return {
        "status": status,
        "code": code,
        "content_type": content_type,
        "truncated": truncated,
        "interstitial": interstitial,
        "error": error,
        "redirects": list(hops or []),
        "payload": payload,
    }


def fetch_body(
    url: str,
    *,
    accept: object = None,
    lang: object = None,
    timeout: float | None = None,
    method: str = "GET",
    body: bytes | None = None,
    allowed: frozenset | None = None,
) -> dict:
    """One request through the liveness client; an HTTP error answer is read, never raised (D-149).

    D-151: `allowed` is the fetch allowlist and every redirect hop is checked against it before it is
    followed; `method`/`body` carry the POST route the routing notes prescribe.
    """
    timeout = limits.LIVENESS_TIMEOUT_SECONDS if timeout is None else timeout
    cap = limits.LIVENESS_MAX_BODY_BYTES
    headers = fetch_headers(url, accept=accept, lang=lang)
    if body is not None:
        headers.setdefault("Content-Type", JSON_CONTENT_TYPE)
    hops: list = []
    try:
        with _open(url, method, timeout, headers=headers, data=body, allowed=allowed, hops=hops) as response:
            code = getattr(response, "status", None) or response.getcode()
            # cap + 1: one byte over the ceiling is how `truncated` is told from «exactly this long».
            payload = response.read(cap + 1)
            # A bounded read that ends early raises nothing, so the promise the server made is the
            # only way to know the document stopped mid-sentence; the header counts the bytes on the
            # wire, so this is measured before `_inflate`.
            # `Content-Length: 3661 ` is a header Python's parser accepts, so the value is stripped
            # before it is read: an unstripped digit test silently skipped the whole comparison.
            declared = header_value(response.headers, "Content-Length").strip()
            short = declared.isdigit() and len(payload) < int(declared)
            payload = _inflate(payload, response.headers)
            return _fetch_answer(url, code, response.headers, payload, response.geturl(), hops, short=short)
    except urllib.error.HTTPError as exc:
        return _fetch_answer(url, exc.code, exc.headers, _inflate(exc.read(cap + 1), exc.headers), url, hops)
    except RedirectRefused as exc:
        # D-151: the hop was refused before the request to it was sent; nothing was read.
        return {
            "status": "unchecked",
            "code": None,
            "content_type": "",
            "truncated": False,
            "interstitial": False,
            "error": exc.error_name,
            "redirects": hops,
            "payload": b"",
        }
    except Exception as exc:  # noqa: BLE001 - timeouts, DNS, TLS: the same best effort as liveness
        return {
            "status": "dead",
            "code": None,
            "content_type": "",
            "truncated": False,
            "interstitial": False,
            "error": probe_error(exc),
            "redirects": hops,
            "payload": b"",
        }


def fetch_refusal(url: str, hosts: frozenset) -> tuple[str, dict] | None:
    """`(reason, answer)` when an address may not be requested at all, or None (D-149, D-151).

    One parser and one allowlist for both commands: `fetch` returns the answer as it is, `save`
    records the reason as `meta.save_outcome` as well (D-199). A6: the refusal is journalled by
    `cli.rejection_of`, so it names a redacted address.
    """
    problem = url_error(url)
    if problem is not None:
        return "url_error", {
            "errors": [f"{problem}: {redacted_url(url)}"],
            "hint": "only http(s) urls without credentials can be fetched",
        }
    host = request_host(url)
    if not host_on_allowlist(host, hosts):
        return "host_not_allowed", {
            "errors": [f"host_not_allowed: {host}"],
            "hint": "hooks/allowlist.txt is the same list the fetch permission gate enforces (§8.1)",
        }
    return None


def fetch_request_body(method: str, raw_body: object) -> tuple[bytes | None, dict | None]:
    """`(body, refusal)` — the bytes a POST sends, or why this call cannot be made (D-151).

    D-151: `POST` exists for one prescribed route, the Normattiva URN endpoint; `save` speaks it too
    (D-199), so the argument is read in one place for both commands.
    """
    body: bytes | None = b"" if method == "POST" else None
    if raw_body in (None, ""):
        return body, None
    if method != "POST":
        return None, {"errors": ["json_body_requires_post"], "hint": "--json is the body of a --method POST"}
    try:
        return json.dumps(json.loads(raw_body), ensure_ascii=False).encode("utf-8"), None
    except ValueError as exc:
        return None, {"errors": [f"invalid_json_body: {exc}"], "hint": "--json takes one JSON document"}


def fetch_allowed(
    url: str,
    hosts: frozenset,
    *,
    accept: object = None,
    lang: object = None,
    timeout: float | None = None,
    method: str = "GET",
    body: bytes | None = None,
) -> dict:
    """The politeness pause this process owes the host, then one request (D-146, D-149, D-199).

    The caller has already run `fetch_refusal`; `hosts` travels on so every redirect hop is checked
    against the same list.
    """
    host = request_host(url)
    since = _LAST_FETCH.get(host)
    if since is not None:
        _wait(host_delay(host) - (time.monotonic() - since))
    answer = fetch_body(url, accept=accept, lang=lang, timeout=timeout, method=method, body=body, allowed=hosts)
    _LAST_FETCH[host] = time.monotonic()
    return answer


def run_fetch(args: argparse.Namespace) -> dict:
    """`mf sources fetch` — the allow-listed, header-aware GET an agent reads a portal with (D-149).

    `WebFetch` sends no headers, so the Cellar contract of D-146 (analysis/38 §1.5), the BOE
    `Accept: application/xml` and the RIS JSON answer are unreachable through it, and `curl` is not
    auto-allowed by `hooks/permission_gate.py --mode bash` (only `<plugin_root>/scripts/mf`). This
    command is that missing hand: the liveness client, the allowlist of §8.1, and a file on disk.

    D-151: `--method POST --json '<body>'` makes the Normattiva OpenAPI route of D-148 executable,
    the url is parsed by `url_error` before anything is sent, and the allowlist now follows the
    request through every redirect instead of guarding the first hop only.
    """
    url = str(getattr(args, "url", "") or "").strip()
    hosts = allowlist_hosts()
    # D-151: one parser, the permission gate's — a url it cannot read is never requested.
    refusal = fetch_refusal(url, hosts)
    if refusal is not None:
        return refusal[1]
    host = request_host(url)
    method = str(getattr(args, "method", "GET") or "GET").strip().upper()
    if method not in FETCH_METHODS:
        return {"errors": [f"unsupported_method: {method}"], "hint": f"--method is one of {', '.join(FETCH_METHODS)}"}
    body, body_refusal = fetch_request_body(method, getattr(args, "json_body", None))
    if body_refusal is not None:
        return body_refusal

    work_dir = Path(args.workdir)
    try:
        # `--out` is checked before the request, so a bad path costs the host nothing.
        target = fetch_target(work_dir, url, out=args.out) if args.out else None
    except ValueError as exc:
        return {"errors": [str(exc)], "hint": "--out is a relative path under research/raw/"}

    answer = fetch_allowed(
        url,
        hosts,
        accept=args.accept,
        lang=args.lang,
        timeout=args.timeout,
        method=method,
        body=body,
    )

    payload = answer.pop("payload")
    if payload and answer["status"] in ("ok", "redirect"):
        text = markup_to_text(payload, answer["content_type"])
        if text is not None:
            payload = text
            answer = {**answer, "content_type": "text/plain", "converted": "text"}
    result = {
        "url": url,
        "host": host,
        "method": method,
        **answer,
        "bytes": len(payload),
        "sha256": None,
        "path": None,
        "retrieval_tool": f"{FETCH_TOOL} {host}",
    }
    if payload:
        if target is None:
            target = fetch_target(
                work_dir,
                url,
                layer=args.layer,
                content_type=answer["content_type"],
                method=method,
                body=body,
            )
        state_io.write_bytes_atomic(target, payload)
        result["sha256"] = state_io.sha256_bytes(payload)
        result["path"] = stepctx.rel_path(work_dir, target)
    if result["status"] == "unchecked":
        # D-149: a body that did arrive is kept so a human can look at it, but it is never a source.
        # D-151: a refused redirect brings no body at all, and the hint must not claim a file.
        result["hint"] = (
            "not the document: saved for inspection, never cite or register it"
            if result["path"]
            else "not the document: nothing was saved"
        )
    return result


# --- save -----------------------------------------------------------------


SAVE_TOOL = "mf-save"
"""D-199: the `retrieval_tool` of a text this command fetched and stored (`mf-save <host>`)."""

SAVE_RESOLVERS: tuple[str, ...] = ("vsrf", "sudact")
"""D-199: `--resolve` is declared with the command; the two resolvers land with their own tasks."""

SAVE_PDF_TEXT_UNAVAILABLE = "excerpt:pdf_text_unavailable"
"""D-201: the outcome of a PDF whose text layer could not be read — no `pypdf`, or a scan.

The original is saved and the record holds no text at all (`raw_kind: none`), so the identity is
never certified: `[[q:]]` is impossible on such a source (C-02 is not bypassed) and the appendix
says in one line that the requisites and quotations were not checked by code.
"""

SAVE_TEXT_TYPES: frozenset = frozenset(
    {"application/json", "application/ld+json", "application/rdf+xml", "application/xhtml+xml", "application/xml"}
)
"""Media types outside `text/*` whose body is still text — the BOE XML and the RIS/Normattiva JSON.

A PDF has its own path (D-201). Anything else — an image, an archive, an unknown binary — is
refused `unsupported_media_type`: only a text answer can be certified.
"""

SAVE_PDF_TYPE = "application/pdf"
PDF_SIGNATURE = b"%PDF-"
PDF_EXTENSION = ".pdf"
"""D-201: what a PDF answer is, and the name its original is kept under (`<source_id>.pdf`)."""

SAVE_HINTS: dict[str, str] = {
    "unchecked": "the server did not serve the document; nothing was registered",
    "dead": "the address did not answer with a document; nothing was registered",
    "interstitial": "the answer is a challenge page, not the document; nothing was registered",
    "access_stub": "the answer is an access wall, not the document; nothing was registered",
    "truncated": "the body was cut at the ceiling, and a partial document is never registered",
    "pdf_truncated": "the pdf did not arrive whole, and half a file is neither a document nor an original",
    "host_not_allowed": "hooks/allowlist.txt is the same list the fetch permission gate enforces (§8.1)",
    "unsupported_media_type": "only a text answer or a pdf can be saved",
    "requisites_mismatch": "the page does not carry --expect-number and --expect-date in their zones",
    "url_error": "only http(s) urls without credentials can be fetched",
}
"""One line of advice per refusal; `meta.save_outcome` records `refused:<key>` (D-199)."""


def save_pdf_answer(content_type: object, payload: bytes = b"") -> bool:
    """True when the answer is a PDF, by its declared type or — with none declared — its signature.

    D-201: a declared type decides, the way it decides everywhere else in this module. With none
    declared, `%PDF-` is the one signature that really is served that way and really is not text;
    Task 4 refused those bytes rather than decode them into mojibake, and this is where they go
    instead. One signature, not a type sniffer.
    """
    kind = str(content_type or "").split(";")[0].strip().lower()
    if kind:
        return kind == SAVE_PDF_TYPE
    return payload.startswith(PDF_SIGNATURE)


def extract_pdf_text(payload: bytes) -> str | None:
    """The text layer of a PDF, or None when there is none to read (D-201).

    `pypdf` is imported here and nowhere else, because it is optional (§5.6): its absence is one
    more PDF without a text layer, not an error. So is a scan, whose pages carry an image and no
    characters, and so is a file `pypdf` cannot parse at all — in each case the original has still
    arrived and is still saved; only the certification is impossible. There is no second
    extractor: no `pdftotext`, no OCR. A whitespace-only layer counts as none.
    """
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - absent, broken or shadowed: all «no text layer» (§5.6)
        return None
    try:
        reader = PdfReader(io.BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # noqa: BLE001 - a damaged or encrypted file has no readable layer either
        return None
    return text if text.strip() else None


def save_text_type(content_type: object, payload: bytes = b"") -> bool:
    """True when the answer is text this command can certify (D-199).

    A declared type decides. With none declared the body is admitted as text — many portals send
    no header — except for the one signature that really is served that way and really is not
    text: `%PDF-`, which takes the PDF path of D-201 instead of being decoded with replacement
    characters and exported to the client as a `.txt` of mojibake.
    """
    kind = str(content_type or "").split(";")[0].strip().lower()
    if not kind:
        return not payload.startswith(PDF_SIGNATURE)
    return kind.startswith("text/") or kind in SAVE_TEXT_TYPES


def save_admission(answer: dict, payload: bytes) -> tuple[str, str] | None:
    """`(reason, detail)` — why this answer may not be registered at all, or None (D-199).

    These rules judge the *answer*, which is why they live here and not in `source_text`: a status
    that means «not served», a challenge page, an access wall, a body cut at the ceiling and a
    media type that is neither text nor a PDF never become a source, whatever the text of them
    would say. D-201: a PDF is admitted, and a truncated one is refused under its own name —
    half a file is neither a document to certify nor an original to keep.
    """
    error = str(answer.get("error") or "")
    if error.startswith("redirect_not_allowed"):
        # D-151 refused the hop before it was requested; the outcome names the hop.
        return "host_not_allowed", error.partition(": ")[2]
    if error == "interstitial_suspected":
        return ("access_stub" if is_access_stub(payload) else "interstitial"), ""
    if answer.get("status") == "unchecked":
        return "unchecked", error
    if answer.get("status") == "dead":
        return "dead", error
    pdf = save_pdf_answer(answer.get("content_type"), payload)
    if answer.get("truncated"):
        # A partial document is the exact failure this command exists to stop: never an excerpt.
        return ("pdf_truncated" if pdf else "truncated"), ""
    if pdf:
        return None
    if not save_text_type(answer.get("content_type"), payload):
        # Only a declared type reaches this: with none declared, the single signature that fails
        # `save_text_type` is `%PDF-`, and it was admitted above.
        kind = str(answer.get("content_type") or "").split(";")[0].strip()
        return "unsupported_media_type", kind or "application/octet-stream"
    return None


def same_source(layer: str, record: dict, url: object, citation: object) -> bool:
    """D-199 identity guard: the same normalised url, or the same normalised citation form (D-206).

    The incoming layer is used on both sides, so a record of another layer is never mistaken for a
    different source — it falls through to the layer check, exactly as in `register_source`.
    """
    if dedup_key(layer, url, citation) == dedup_key(layer, record.get("url"), record.get("citation_form")):
        return True
    return dedup_key(layer, "", citation) == dedup_key(layer, "", record.get("citation_form"))


def find_source(sources: dict, layer: str, url: object, citation: object) -> str | None:
    """The id of the record this save is about, by the dedup identity of D-206, or None."""
    for source_id, record in sources.items():
        if record.get("layer") == layer and same_source(layer, record, url, citation):
            return source_id
    return None


def save_guard(sources: dict, *, layer: str, source_id: str | None, url: str, citation: str) -> dict | None:
    """The registry checks of D-199 steps 1 and 3 — before the network, and again after it."""
    if not source_id or source_id not in sources:
        return None
    held = sources[source_id]
    if not same_source(layer, held, url, citation):
        # D-143: a stranger's text never lands under an occupied id, whatever it proves about itself.
        return {
            "errors": [f"source_id_collision: {source_id} already holds {held['title']!r}"],
            "source_id": source_id,
            "held_title": held["title"],
            "held_raw_sha256": held.get("raw_sha256"),
            "hint": "save the new text under its own id, or fix the existing record",
        }
    if held.get("layer") != layer:
        return {
            "errors": [f"source_id_layer_mismatch: {source_id} is registered under {held['layer']!r}"],
            "source_id": source_id,
            "held_layer": held["layer"],
            "held_title": held["title"],
            "hint": (
                f"a record never changes layer: save again with --layer {held['layer']}, "
                "or save this text under its own id"
            ),
        }
    return None


def save_refused(work_dir: Path, identity: dict, reason: str, answer: dict) -> dict:
    """The refusal answer, and `meta.save_outcome` in a record that already existed (D-199).

    That one field is the whole exception to «a failure never mutates»: the sufficiency reviewer has
    to see that this source was attempted and refused, or it asks for the attempt to be repeated.
    Nothing else moves — no file, no other field, and nothing at all when there is no such record.
    """
    outcome = f"refused:{reason}"
    answer = {**answer, "save_outcome": outcome}
    with sources_lock(work_dir):
        if is_frozen(work_dir):
            return answer
        registry = read_registry(work_dir)
        sources = registry["sources"]
        if save_guard(sources, **identity) is not None:
            # The id was free at step 1 and another process claimed it while this request was on
            # the network: the record under it is not this source, and a failure never touches a
            # record that is not its own.
            return answer
        held_id = identity["source_id"] if identity["source_id"] in sources else None
        if held_id is None:
            held_id = find_source(sources, identity["layer"], identity["url"], identity["citation"])
        if held_id is None:
            return answer
        record = sources[held_id]
        record["meta"] = {**(record.get("meta") or {}), "save_outcome": outcome}
        write_registry(work_dir, registry)
    return {**answer, "source_id": held_id}


def _stage_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` to a temporary file and flush it to the platter before any rename (D-199).

    Exactly what `state_io.write_bytes_atomic` does on its own temp file: the registry must never
    name a file whose bytes a crash could still lose.
    """
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def run_save(args: argparse.Namespace) -> dict:
    """`mf sources save` — one transaction from an address to a registered, certified text (D-199).

    The agent was the glue between fetching and registering, and in the run this plan comes from 43
    of 45 saved texts were retyped or summarised. Here the code fetches the page, converts it,
    certifies with `source_text` that this is that document and that it is whole, and only then
    writes: `full_text` means the code saved it.

    Three steps, and the lock is held on the first and the third only, because three researchers
    work at once and the network step is the long one: (1) the registry checks, nothing written;
    (2) without the lock — politeness, the allowlist on the url and on every hop, the admission
    rules, the conversion, one `prepare_raw`, the verdict, and the bytes into a temporary file
    inside the target folder; (3) the same checks again, then and only then the duplicate search,
    the id, the atomic publication and the registry write. A refusal or a crash leaves every file
    and every record as it was; `meta.save_outcome` in an already existing record is the exception.

    D-201: an answer that is a PDF takes a second path through the same transaction. The bytes as
    served are kept whole as `research/raw/<layer>/<id>.pdf` and recorded as `raw_original_path` +
    `raw_original_sha256`; the `pypdf` text layer is a convenience, extracted once, stored as
    `<id>.md` like any other text and judged by `source_text.verdict`. Without a text layer the
    original is saved alone and the source is registered uncertified (`raw_kind: none`,
    `excerpt:pdf_text_unavailable`).
    """
    resolver = str(getattr(args, "resolve", "") or "").strip()
    if resolver:
        return {
            "errors": [f"resolver_not_available: {resolver}"],
            "hint": "pass --url: the resolvers that find an address from the requisites land later",
        }
    try:
        meta = parse_json_argument(args.meta, "meta")
        identifiers = parse_json_argument(args.identifiers, "identifiers")
    except ValueError as exc:
        return {"errors": [str(exc)]}
    method = str(getattr(args, "method", "GET") or "GET").strip().upper()
    body, body_refusal = fetch_request_body(method, getattr(args, "json_body", None))
    if body_refusal is not None:
        return body_refusal

    work_dir = Path(args.workdir)
    layer = str(args.layer)
    tier = str(getattr(args, "tier", "supporting") or "supporting")
    # A4/D-193: an address in any string the memo prints is scrubbed before it is stored.
    title = scrub_urls(str(args.title))
    citation = scrub_urls(str(args.citation))
    meta = scrub_values(meta) if meta else {}
    identifiers = scrub_values(identifiers) if identifiers else {}
    url = str(getattr(args, "url", "") or "").strip()
    # D-192: the address a client may be given; the request itself goes to the url as it was passed.
    clean_url, retrieved_from = public_url(url)
    explicit_id = slugify(args.id) if getattr(args, "id", None) else None
    identity = {"layer": layer, "source_id": explicit_id, "url": clean_url, "citation": citation}

    # Step 1 — under the lock, before the network: nothing here writes.
    with sources_lock(work_dir):
        if is_frozen(work_dir):
            return {"errors": ["sources_frozen"], "hint": SOURCES_FROZEN_HINT}
        refusal = save_guard(read_registry(work_dir)["sources"], **identity)
    if refusal is not None:
        return refusal

    # Step 2 — without the lock: the network, the conversion, the certification.
    hosts = allowlist_hosts()
    refused = fetch_refusal(url, hosts)
    if refused is not None:
        return save_refused(work_dir, identity, refused[0], refused[1])
    host = request_host(url)
    answer = fetch_allowed(
        url, hosts, accept=args.accept, lang=args.lang, timeout=args.timeout, method=method, body=body
    )
    payload = answer.pop("payload")
    transport = {"url": clean_url, "host": host, "status": answer["status"], "code": answer["code"]}
    admission = save_admission(answer, payload)
    if admission is not None:
        reason, detail = admission
        errors = [f"{reason}: {detail}" if detail else reason]
        return save_refused(work_dir, identity, reason, {"errors": errors, "hint": SAVE_HINTS[reason], **transport})

    # D-201: a PDF is kept as the bytes the server served — it never goes through `prepare_raw`,
    # which decodes as `utf-8-sig` with `errors="replace"` and would corrupt the file it is meant
    # to preserve. Its text layer is read once, here, and is then text like any other.
    original = payload if save_pdf_answer(answer["content_type"], payload) else None
    original_digest = state_io.sha256_bytes(payload) if original is not None else None
    if original is not None:
        pdf_text = extract_pdf_text(payload)
        stored = None if pdf_text is None else prepare_raw(pdf_text.encode("utf-8"))
    else:
        converted = markup_to_text(payload, answer["content_type"])
        # `prepare_raw` runs once, on the converted text, so the digest stored is the one liveness
        # can confirm against the page it came from (D-163, D-193, D-199).
        stored = prepare_raw(converted if converted is not None else payload)
    if stored is None:
        # D-201: no text layer at all. The original is still saved and the source is registered —
        # uncertified, with no text to quote from and no requisites checked by code.
        call = {
            "raw_kind": "none",
            "outcome": SAVE_PDF_TEXT_UNAVAILABLE,
            "error": None,
            "found": {"number": False, "date": False, "russian": False, "chars": 0},
        }
    else:
        text = stored.decode("utf-8-sig", errors="replace")
        call = source_text.verdict(
            text,
            layer=layer,
            expect_number=args.expect_number,
            expect_date=args.expect_date,
            expect_article=args.expect_article,
        )
    if call["error"] is not None:
        # D-203: a Russian judicial act missing a requisite in its own zone is not this document.
        missing = ", ".join(name for name in ("number", "date") if not call["found"].get(name))
        errors = [f"{call['error']}: {missing}" if missing else call["error"]]
        return save_refused(
            work_dir, identity, call["error"], {"errors": errors, "hint": SAVE_HINTS[call["error"]], **transport}
        )
    outcome = call["outcome"]
    # D-201: «are these the same bytes» is asked of the original wherever there is one. A PDF with
    # no text layer has no text digest at all, so a comparison that reached for `raw_sha256` there
    # would compare None with None and call two different scans one document.
    digest = original_digest if original is not None else state_io.sha256_bytes(stored)

    target_dir = work_dir / RAW_DIR / layer
    target_dir.mkdir(parents=True, exist_ok=True)
    # Inside the target folder, so step 3 publishes with one atomic rename and writes nothing under
    # the lock; the pid keeps two processes saving the same document out of each other's way.
    stem = f".{explicit_id or slugify(title)}.{os.getpid()}"
    temp = target_dir / f"{stem}.tmp"
    temp_original = target_dir / f"{stem}{PDF_EXTENSION}.tmp"
    try:
        if stored is not None:
            _stage_bytes(temp, stored)
        if original is not None:
            _stage_bytes(temp_original, original)

        # Step 3 — under the lock again: the tree may have changed while the network ran.
        with sources_lock(work_dir):
            if is_frozen(work_dir):
                return {"errors": ["sources_frozen"], "hint": SOURCES_FROZEN_HINT}
            registry = read_registry(work_dir)
            sources = registry["sources"]
            refusal = save_guard(sources, **identity)
            if refusal is not None:
                return refusal
            source_id = explicit_id if explicit_id in sources else find_source(sources, layer, clean_url, citation)
            created = source_id is None
            # Fix round 1: «the same bytes» is the digest the record already carries, and nothing
            # else. Not the presence of a file: a record holding an uncertified scan would then
            # answer `idempotent` over bytes it had just discarded, and two different documents
            # would be reported as one. The recorded digest is the original's where there is one
            # (a PDF with no text layer has no text digest at all) and the text's otherwise —
            # one rule for both, which is also the rule the downgrade guard below asks.
            same_bytes = False
            if created:
                source_id = explicit_id or unique_slug(slugify(title), set(sources))
                record = _new_record(layer, title, citation, clean_url, f"{SAVE_TOOL} {host}", tier, retrieved_from)
                sources[source_id] = record
            else:
                record = sources[source_id]
                held_digest = record.get("raw_original_sha256") or record.get("raw_sha256")
                same_bytes = bool(held_digest) and held_digest == digest
                if raw_kind_of(record) == "full_text":
                    # Text saved by code is replaced by the same document, whole, and by nothing
                    # else: an excerpt of it is a downgrade, other bytes are another text under an
                    # occupied identity — the answer the loser of a race for one url gets.
                    if call["raw_kind"] != "full_text":
                        return {
                            "errors": [f"source_is_code_saved: {source_id} holds text saved by mf sources save"],
                            "source_id": source_id,
                            "hint": "a shorter text never replaces one the code saved whole",
                        }
                    if not same_bytes:
                        return {
                            "errors": [f"source_id_collision: {source_id} already holds another saved text"],
                            "source_id": source_id,
                            "held_title": record["title"],
                            "held_raw_sha256": record.get("raw_sha256"),
                            "hint": "the address served other bytes: save this text under its own id",
                        }
            # The stored text is replaced only when the new outcome is `full_text` — a shorter
            # excerpt never displaces a longer one, and then only `meta.save_outcome` is written —
            # but where no text is stored nothing is displaced, so a new record and a record that
            # holds none take what they are given. «Holds none» is read from the disk, not from the
            # field alone: a publication interrupted after the registry write leaves a `raw_path`
            # whose file was never written, and that record has no text to displace either — the
            # next save repairs it instead of answering a hollow `idempotent` over a missing file.
            # D-201: an original that exists is text enough to hold — a scan is the document the
            # client was promised, and a save that finds the file gone republishes it, exactly as
            # it repairs an interrupted text publication. Fix round 1: publication is disk-aware,
            # the answer is not — when the file is gone and the bytes differ, publishing them is
            # still right (the record would otherwise name a file that is not there), but a
            # replacement is not a repair and `idempotent` says so.
            # Fix round 3: the two artefacts are decided **separately**. The pair is published in
            # two renames, so a kill between them leaves the registry naming both files while only
            # the `.pdf` is on disk — a third state the old single boolean could not see («holds
            # one of two»), in which an identical retry saw `held_original`, published nothing and
            # called the half-written pair idempotent.
            held_text = bool(record.get("raw_path")) and (work_dir / record["raw_path"]).is_file()
            held_original = bool(record.get("raw_original_path")) and (
                work_dir / record["raw_original_path"]
            ).is_file()
            target = target_dir / f"{source_id}.md"
            target_original = target_dir / f"{source_id}{PDF_EXTENSION}"
            # Fix round 4: three outcomes, not two. A repair is only ever the same document
            # arriving again, so it is conditional on `same_bytes` and on nothing else — filling a
            # missing half with *other* bytes would record a pair that never existed as one
            # document, and a later freeze would pin the mixture without noticing, because the
            # integrity check asks only whether each file still matches its own digest.
            replacing = repairing = False
            if created or call["raw_kind"] == "full_text":
                # The record takes this answer whole: a new record, or text the code saved entire.
                replacing = True
            elif same_bytes:
                # The repair: whatever the record names and the disk lacks is written back and
                # nothing else moves. `idempotent` stays true.
                repairing = True
            elif not (held_text or held_original):
                # Other bytes over a record holding nothing on disk: there is nothing to displace.
                replacing = True
            # …and other bytes over a record that still holds *either* component publish nothing:
            # only `meta.save_outcome` is written, and `idempotent` is false.
            if replacing:
                publish_text = stored is not None
                publish_original = original is not None
            elif repairing:
                publish_text = stored is not None and bool(record.get("raw_path")) and not held_text
                publish_original = (
                    original is not None and bool(record.get("raw_original_path")) and not held_original
                )
            else:
                publish_text = publish_original = False
            if replacing:
                if stored is not None:
                    record.update(raw_fields(work_dir, target, stored, call["raw_kind"]))
                else:
                    record.update(
                        {"raw_path": None, "raw_sha256": None, "raw_chars": 0, "raw_kind": call["raw_kind"]}
                    )
                if original is not None:
                    record["raw_original_path"] = stepctx.rel_path(work_dir, target_original)
                    record["raw_original_sha256"] = original_digest
                else:
                    # A text answer over a record that used to hold one: a record never names an
                    # original that is no longer its own. The stale file is left on disk, unnamed.
                    record.pop("raw_original_path", None)
                    record.pop("raw_original_sha256", None)
            elif repairing:
                # A repair writes the fields of the artefact it republishes, from the bytes it is
                # about to publish — `same_bytes` is one digest, and a text layer re-extracted by
                # another `pypdf` may differ from the one the record recorded. The record never
                # describes a file it does not have.
                if publish_text:
                    record.update(raw_fields(work_dir, target, stored, call["raw_kind"]))
                if publish_original:
                    record["raw_original_path"] = stepctx.rel_path(work_dir, target_original)
                    record["raw_original_sha256"] = original_digest
            if publish_text or publish_original:
                record["title"] = title
                record["citation_form"] = citation
                record["tier"] = tier
                record["retrieval_tool"] = f"{SAVE_TOOL} {host}"
                record["provenance"] = "agent_saved"  # only liveness promotes a record to `confirmed`
                if clean_url:
                    record["url"] = clean_url
                if retrieved_from:
                    record["retrieved_from"] = retrieved_from
                if identifiers:
                    merged = dict(record.get("identifiers") or {})
                    merged.update({name: value for name, value in identifiers.items() if value})
                    record["identifiers"] = merged
                if meta:
                    record["meta"] = {**(record.get("meta") or {}), **meta}
            record["meta"] = {**(record.get("meta") or {}), "save_outcome": outcome}
            # Validate the complete candidate, persist the registry, publish the file last. The
            # two writes cannot be made one transaction, so the order is chosen by what the
            # surviving window costs: publishing first and being killed before the registry write
            # loses the old text for good and leaves a record describing bytes that no longer
            # exist. This way a kill in the window leaves the registry naming a digest the file
            # does not have yet and the **old bytes intact** — the disagreement Task 2's freeze
            # integrity check demotes with a `full_text_integrity` warning and C-02's triple sha
            # equality blocks. Loud and safe rather than silent and lossy; no journal, no backup.
            schema.validate_or_raise(registry, "sources")
            write_registry(work_dir, registry)
            # D-201: the original first — it is the source itself, and the text layer only the
            # convenience read out of it. Fix round 3: a kill between the two renames is repaired
            # by the next identical save, which now sees «holds one of two» for what it is.
            if publish_original:
                os.replace(temp_original, target_original)
            if publish_text:
                os.replace(temp, target)
            result = dict(record)
    finally:
        temp.unlink(missing_ok=True)
        temp_original.unlink(missing_ok=True)

    return {
        "source_id": source_id,
        "created": created,
        # Fix round 1: the record already existed AND the address served the bytes it holds.
        # Nothing else may set it — «this call changed nothing» is a statement about the bytes.
        "idempotent": (not created) and same_bytes,
        "layer": layer,
        "raw_path": result.get("raw_path"),
        "raw_sha256": result.get("raw_sha256"),
        "raw_chars": result.get("raw_chars"),
        "raw_kind": raw_kind_of(result),
        "raw_original_path": result.get("raw_original_path"),
        "raw_original_sha256": result.get("raw_original_sha256"),
        "save_outcome": outcome,
        "provenance": result.get("provenance"),
        "tier": result.get("tier"),
        "bytes": len(original if original is not None else stored),
        **transport,
    }


# --- verify ---------------------------------------------------------------


def identifier_errors(identifiers: dict) -> list[str]:
    """Names of the identifiers whose syntax does not match their pattern (offline check, §5.3)."""
    bad = []
    for name, pattern in IDENTIFIER_PATTERNS.items():
        value = identifiers.get(name)
        if value and not pattern.match(str(value).strip()):
            bad.append(name)
    return sorted(bad)


def eu_syntax_ok(identifiers: dict) -> bool | None:
    """Tri-state EU identifier syntax (D34-08).

    `True` = every EU identifier present is well formed, `False` = at least one is malformed,
    `None` = the source carries no EU identifier at all, so the check does not apply. An EDPB
    guideline or a US docket is not a defective citation for having no CELEX.
    """
    present = [name for name in EU_IDENTIFIERS if identifiers.get(name)]
    if not present:
        return None
    return all(IDENTIFIER_PATTERNS[name].match(str(identifiers[name]).strip()) for name in present)


def duplicate_keys(record: dict) -> set:
    """Keys under which a record can be a duplicate: its raw sha, and each identifier + pinpoint.

    D34-04: one CELEX covers a whole regulation, so `(identifier, value)` alone put all 17 GDPR
    articles in one group; the pinpoint is what makes Art. 6 and Art. 88 different sources.
    """
    keys = set()
    digest = record.get("raw_sha256")
    if digest:
        keys.add(("raw_sha256", str(digest), ""))
    point = declared_pinpoint(record)
    for name, value in (record.get("identifiers") or {}).items():
        if value:
            keys.add((name, re.sub(r"\s+", "", str(value)).lower(), point))
    return keys


def canonical_of(sources: dict, group: list[str]) -> str:
    """The canonical id of a duplicate group: labelled right, holding the full text, then first."""
    return sorted(
        group,
        key=lambda source_id: (
            is_mislabelled(source_id, sources[source_id]),
            raw_kind_of(sources[source_id]) != "full_text",
            not sources[source_id].get("raw_sha256"),
            str(sources[source_id].get("retrieved_at") or ""),
            source_id,
        ),
    )[0]


def find_duplicates(sources: dict) -> list[dict]:
    """Groups of sources sharing a raw sha or an `(identifier, pinpoint)` key (§5.3, D34-04).

    A group is `mergeable` only when every member cites the same instrument; records that happen to
    carry identical raw text under different instruments are reported and left alone.
    """
    parent: dict[str, str] = {source_id: source_id for source_id in sources}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    index: dict = {}
    for source_id in sorted(sources):
        for key in sorted(duplicate_keys(sources[source_id])):
            index.setdefault(key, []).append(source_id)
    for _key, ids in sorted(index.items()):
        for other in ids[1:]:
            root_a, root_b = find(ids[0]), find(other)
            if root_a != root_b:
                parent[root_b] = root_a

    members: dict[str, list[str]] = {}
    for source_id in sorted(sources):
        members.setdefault(find(source_id), []).append(source_id)

    groups = []
    for group in sorted(members.values()):
        if len(group) < 2:
            continue
        keys = sorted({key for source_id in group for key in duplicate_keys(sources[source_id])})
        mergeable = len({instrument_identity(sources[source_id]) for source_id in group}) == 1
        groups.append(
            {
                "source_ids": group,
                "keys": [{"identifier": name, "value": value, "pinpoint": point} for name, value, point in keys],
                "mergeable": mergeable,
                "canonical": canonical_of(sources, group) if mergeable else None,
            }
        )
    return groups


def raw_article_numbers(work_dir: str | os.PathLike, record: dict) -> set:
    """Article numbers that appear as headings in a source's raw text (empty when there are none)."""
    text = read_raw_text(work_dir, record)
    if not text:
        return set()
    return set(ARTICLE_HEADING_NUMBER_RE.findall(text))


def contradictory_members(work_dir: str | os.PathLike, sources: dict, group: list[str]) -> list[list[str]]:
    """Pairs in a duplicate group whose raw texts are headed by different articles (D34-04)."""
    numbers = {source_id: raw_article_numbers(work_dir, sources[source_id]) for source_id in group}
    pairs = []
    for index, left in enumerate(group):
        for right in group[index + 1 :]:
            if numbers[left] and numbers[right] and not (numbers[left] & numbers[right]):
                pairs.append([left, right])
    return pairs


def merge_map(work_dir: str | os.PathLike, sources: dict) -> tuple:
    """`({dropped_id: canonical_id}, contradictory_groups)` for `sources pack --freeze` (D34-04)."""
    merged: dict[str, str] = {}
    contradictory: list[dict] = []
    for group in find_duplicates(sources):
        if not group["mergeable"]:
            continue
        clashes = contradictory_members(work_dir, sources, group["source_ids"])
        if clashes:
            contradictory.append({"source_ids": group["source_ids"], "conflicting_pairs": clashes})
            continue
        canonical = group["canonical"]
        for source_id in group["source_ids"]:
            if source_id != canonical:
                merged[source_id] = canonical
    return merged, contradictory


def run_verify(args: argparse.Namespace) -> dict:
    """`mf sources verify [--set <id> us=…]` — offline syntax, duplicates and the agent's US verdict."""
    work_dir = Path(args.workdir)
    args_key = f"sources verify --set={'  '.join(args.set) if args.set else ''}"
    saved = begin_step(work_dir, args, args_key)
    if saved is not None:
        return saved
    assignment = None
    if args.set:
        source_id, raw = args.set
        field, _, value = str(raw).partition("=")
        if field.strip() != "us":
            return {"errors": [f"unsupported_field: {field!r}"], "hint": "only `us=` can be set"}
        if value.strip() not in US_VERDICTS:
            return {"errors": [f"unknown_us_verdict: {value!r}"], "allowed": list(US_VERDICTS)}
        assignment = (source_id, value.strip())

    with sources_lock(work_dir):
        registry = read_registry(work_dir)
        sources = registry["sources"]
        if assignment is not None:
            source_id, value = assignment
            if source_id not in sources:
                return {"errors": [f"unknown_source: {source_id}"]}
            verification = dict(sources[source_id].get("verification") or {})
            verification["us"] = value
            verification["us_by"] = "agent"
            verification["checked_at"] = events.utc_now()
            verification.setdefault("eu_syntax_ok", None)
            sources[source_id]["verification"] = verification
            write_registry(work_dir, registry)
            return finish_step(
                work_dir, args, {"source_id": source_id, "verification": verification, "set": True}, args_key
            )

        invalid = []
        for source_id in sorted(sources):
            record = sources[source_id]
            identifiers = record.get("identifiers") or {}
            bad = identifier_errors(identifiers)
            if bad:
                invalid.append({"source_id": source_id, "identifiers": bad})
            verification = dict(record.get("verification") or {})
            verification["eu_syntax_ok"] = eu_syntax_ok(identifiers)
            verification.setdefault("us", "n/a")
            verification.setdefault("us_by", None)
            verification["checked_at"] = events.utc_now()
            record["verification"] = verification
        duplicates = find_duplicates(sources)
        write_registry(work_dir, registry)

    result = {
        "checked": len(sources),
        "invalid": invalid,
        "duplicates": duplicates,
        "mislabelled": mislabelled_sources(sources),
        "eu_syntax_ok": sorted(
            sid
            for sid in sources
            if (sources[sid].get("verification") or {}).get("eu_syntax_ok") is True
        ),
        "eu_syntax_not_applicable": sorted(
            sid
            for sid in sources
            if (sources[sid].get("verification") or {}).get("eu_syntax_ok") is None
        ),
    }
    return finish_step(work_dir, args, result, args_key)


# --- slice ----------------------------------------------------------------


def article_spans(text: str, article: str) -> list[tuple[int, int]]:
    """Heading positions of `Article <n>` in a raw file; cross-references never match (§4.3)."""
    pattern = re.compile(
        ARTICLE_HEADING_PREFIX + re.escape(str(article)) + r"\b",
        re.MULTILINE | re.IGNORECASE,
    )
    starts = [match.start() for match in pattern.finditer(text)]
    if not starts:
        return []
    boundaries = sorted({match.start() for match in ANY_ARTICLE_HEADING_RE.finditer(text)} | {len(text)})
    spans = []
    for start in starts:
        end = next((position for position in boundaries if position > start), len(text))
        spans.append((start, end))
    return spans


def run_slice(args: argparse.Namespace) -> dict:
    """`mf sources slice --source <id> --article N` over an already saved raw file (§4.3)."""
    work_dir = Path(args.workdir)
    registry = read_registry(work_dir)
    record = registry["sources"].get(args.source)
    if record is None:
        return {"errors": [f"unknown_source: {args.source}"]}
    text = read_raw_text(work_dir, record)
    if text is None:
        return {"errors": ["no_raw"], "error": "no_raw", "source_id": args.source}

    spans = article_spans(text, args.article)
    if not spans:
        return {"errors": ["not_found"], "error": "not_found", "source_id": args.source, "article": args.article}
    if len(spans) > 1:
        return {
            "errors": ["ambiguous"],
            "error": "ambiguous",
            "source_id": args.source,
            "article": args.article,
            "positions": [{"char_start": start, "char_end": end} for start, end in spans],
        }
    start, end = spans[0]
    fragment = text[start:end].rstrip()
    return {
        "source_id": args.source,
        "article": args.article,
        "char_start": start,
        "char_end": start + len(fragment),
        "chars": len(fragment),
        "text": fragment,
    }


# --- CLI ------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the `sources` command group (§5.2)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "sources", "source registry, freeze and verifications")

    reg = group.add_parser("register", help="register one source (idempotent by layer + url/citation)")
    reg.add_argument("--workdir", required=True)
    reg.add_argument("--layer", required=True, choices=list(LAYERS))
    reg.add_argument("--title", required=True)
    reg.add_argument("--citation", required=True, help="citation_form as it appears in the memo")
    reg.add_argument("--url", default="")
    reg.add_argument("--tool", dest="tool", default="unknown", help="retrieval tool name")
    reg.add_argument("--tier", default="supporting", choices=list(TIERS))
    reg.add_argument("--raw-file", dest="raw_file", default=None, help="full text saved by the agent")
    reg.add_argument(
        "--raw-kind",
        dest="raw_kind",
        default="agent_summary",
        choices=list(AGENT_RAW_KINDS),
        help="what the saved text is (D-200); full_text is saved by code only",
    )
    reg.add_argument("--meta", default=None, help="JSON object of tool metadata (in_force, status …)")
    reg.add_argument("--identifiers", default=None, help="JSON object: celex/ecli/eli/neutral/reporter_cite")
    reg.add_argument("--id", default=None, help="explicit source_id slug")
    reg.set_defaults(func=run_register)

    fetch = group.add_parser("fetch", help="GET an allow-listed url with the liveness headers (D-149)")
    fetch.add_argument("--workdir", required=True)
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--method", default="GET", choices=list(FETCH_METHODS), help="POST for the IT URN route")
    fetch.add_argument(
        "--json",
        dest="json_body",
        default=None,
        help="JSON body of a --method POST (sent as application/json)",
    )
    fetch.add_argument("--accept", default=None, help="Accept header (BOE needs application/xml)")
    fetch.add_argument("--lang", default=None, help="Accept-Language (Cellar takes eng/deu/fra)")
    fetch.add_argument("--out", dest="out", default=None, help="relative path under research/raw/")
    fetch.add_argument("--layer", default=None, choices=list(LAYERS), help="research/raw/<layer>/")
    fetch.add_argument("--timeout", type=float, default=limits.LIVENESS_TIMEOUT_SECONDS)
    fetch.set_defaults(func=run_fetch)

    save = group.add_parser("save", help="fetch, certify and register one source text (D-199)")
    save.add_argument("--workdir", required=True)
    save.add_argument("--layer", required=True, choices=list(LAYERS))
    save.add_argument("--title", required=True)
    save.add_argument("--citation", required=True, help="citation_form as it appears in the memo")
    save.add_argument("--tier", default="supporting", choices=list(TIERS))
    address = save.add_mutually_exclusive_group(required=True)
    address.add_argument("--url", default="", help="the address the text is fetched from")
    address.add_argument(
        "--resolve",
        default=None,
        choices=list(SAVE_RESOLVERS),
        help="find the address from the requisites instead of passing --url",
    )
    save.add_argument("--id", default=None, help="explicit source_id slug")
    save.add_argument("--meta", default=None, help="JSON object of tool metadata (in_force, status …)")
    save.add_argument("--identifiers", default=None, help="JSON object: celex/ecli/eli/neutral/reporter_cite")
    save.add_argument("--expect-number", dest="expect_number", default=None, help="case number the act must carry")
    save.add_argument("--expect-date", dest="expect_date", default=None, help="date of the act (ISO or DD.MM.YYYY)")
    save.add_argument("--expect-article", dest="expect_article", default=None, help="article for --layer statutes")
    save.add_argument("--method", default="GET", choices=list(FETCH_METHODS), help="POST for the IT URN route")
    save.add_argument("--json", dest="json_body", default=None, help="JSON body of a --method POST")
    save.add_argument("--accept", default=None, help="Accept header (BOE needs application/xml)")
    save.add_argument("--lang", default=None, help="Accept-Language (Cellar takes eng/deu/fra)")
    save.add_argument("--timeout", type=float, default=limits.LIVENESS_TIMEOUT_SECONDS)
    save.set_defaults(func=run_save)

    pack = group.add_parser("pack", help="freeze the source pack (one transaction, M6)")
    pack.add_argument("--workdir", required=True)
    pack.add_argument("--freeze", action="store_true", required=False)
    pack.add_argument("--step", required=True)
    pack.add_argument("--attempt", type=int, required=True)
    pack.add_argument("--phase", default=None)
    pack.set_defaults(func=run_pack)

    digest = group.add_parser("digest", help="gate-11 text (§2.4)")
    digest.add_argument("--workdir", required=True)
    digest.add_argument("--exceptions", action="store_true", help="only the exceptions block")
    digest.set_defaults(func=run_digest)

    liveness = group.add_parser("liveness", help="HEAD/GET each url, best effort")
    liveness.add_argument("--workdir", required=True)
    liveness.add_argument("--source", default=None)
    liveness.add_argument("--timeout", type=float, default=limits.LIVENESS_TIMEOUT_SECONDS)
    liveness.add_argument("--step", default=None)
    liveness.add_argument("--attempt", type=int, default=1)
    liveness.set_defaults(func=run_liveness)

    verify = group.add_parser("verify", help="offline identifier syntax; --set records the US verdict")
    verify.add_argument("--workdir", required=True)
    verify.add_argument("--set", nargs=2, metavar=("SOURCE_ID", "ASSIGNMENT"), default=None)
    verify.add_argument("--step", default=None)
    verify.add_argument("--attempt", type=int, default=1)
    verify.set_defaults(func=run_verify)

    slicer = group.add_parser("slice", help="cut one article out of a saved raw file")
    slicer.add_argument("--workdir", required=True)
    slicer.add_argument("--source", required=True)
    slicer.add_argument("--article", required=True)
    slicer.set_defaults(func=run_slice)
