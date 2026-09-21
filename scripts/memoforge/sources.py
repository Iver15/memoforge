"""`mf sources register|fetch|pack|digest|liveness|verify|slice` — registry and freeze (ТЗ §5.3, M5/M6)."""

from __future__ import annotations

import argparse
import codecs
import functools
import gzip
import http.cookiejar
import io
import json
import math
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


def meta_save_outcome_refusal(meta: dict | None) -> dict | None:
    """Why a `register --meta` `save_outcome` may not be written, or None when it may (D-205).

    `meta.save_outcome` is the record of an attempt of `mf sources save`, and the sufficiency reviewer
    reads it: with none, a critical source that is not whole goes back for a save; with one, it was
    tried already. `register` may carry exactly one kind of value there — the refusal of a save that
    failed, `refused:<code>` with a code from `SAVE_OUTCOME_REFUSALS` — so the researcher's fallback
    text is not sent round again for a save that cannot succeed (the design's D-202 line). Anything
    else is a claim only code may make: `full_text` would say code saved a text it never saw, and it
    would switch off the reviewer's «not attempted yet» remedy.

    D-205 fix round 1: `meta.save_method` is code's alone as well — it tells liveness not to probe the
    source — so `register` writes none, whatever its value. Final review: so are `meta.save_endpoint`
    (the address a POST save sent its request to) and `meta.text_outcome` (what code classified the
    text the record holds as) — every key of `CODE_META_KEYS`.
    """
    for key in CODE_META_KEYS:
        if meta and key in meta:
            return {"errors": [f"{key}_not_allowed: {meta[key]!r}"], "hint": CODE_META_HINTS[key]}
    if not meta or "save_outcome" not in meta:
        return None
    value = meta["save_outcome"]
    kind, _, code = value.partition(":") if isinstance(value, str) else ("", "", "")
    if kind == "refused" and code in SAVE_OUTCOME_REFUSALS:
        return None
    return {
        "errors": [f"save_outcome_not_allowed: {value!r}"],
        "hint": (
            "meta.save_outcome is written by `mf sources save`; register carries only the refusal of a save "
            "that failed — refused:<code>, e.g. refused:host_not_allowed, the code one of: "
            + ", ".join(sorted(SAVE_OUTCOME_REFUSALS))
        ),
    }


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
    refusal = meta_save_outcome_refusal(meta)
    if refusal is not None:
        # D-205: refused before anything is read or written — the raw file stays where it was.
        return refusal

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
            previous_url = normalize_url(record.get("url"))
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
            if normalize_url(record.get("url")) != previous_url and record.get("meta"):
                # Final review, Minor 3: the method and the endpoint describe how the OLD address
                # was reached. Left behind, a POST marker kept liveness off a new GET address.
                record["meta"] = {
                    name: value for name, value in record["meta"].items() if name not in ADDRESS_META_KEYS
                }
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
            if TEXT_OUTCOME_KEY in (record.get("meta") or {}):
                # Final review E: code classified a text that is no longer the one held. The attempt
                # history (`save_outcome`) stays; the description of the held text goes with it.
                record["meta"] = {name: value for name, value in record["meta"].items() if name != TEXT_OUTCOME_KEY}

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
    for canonical in sorted(set(merged.values())):
        # Final review D (D-201 × D34-04): the original a merged member carries survives the merge.
        # Only the canonical id gets a snapshot row, so an original left on an alias was pinned by
        # nothing and never reached the client — a text-only record merged with a PDF-backed one of
        # the same text lost the only original there was. A canonical with no original of its own
        # takes one from its group (the member `canonical_of` ranks first among those whose file is
        # on disk), and the row below pins the digest the integrity pass already took of that file.
        if sources[canonical].get("raw_original_path"):
            continue
        donors = sorted(alias for alias, target in merged.items() if target == canonical and originals.get(alias))
        if donors:
            donor = canonical_of(sources, donors)
            sources[canonical]["raw_original_path"] = sources[donor]["raw_original_path"]
            originals[canonical] = originals[donor]
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

    D-202: `stop` is a caller's own rule for a hop it will not follow — `stop(newurl)` names the
    reason, or returns None. The sudact channel refuses the hop to its portal's captcha this way, so
    the captcha page is never even requested.

    `hop(url)` is what the caller does before a followed hop **goes out**, and it runs at dispatch,
    not at the decision (task 7, round 3). urllib's `http_error_302` decides through
    `redirect_request`, then **drains the previous response's body** with `fp.read()` — a network
    wait, as long as the body is slow — and only then opens the new request. A hop charged at the
    decision would be charged before that wait: a channel closed during it would still receive the
    hop, and a body that took five seconds would put the hop and the next request at the same
    instant. So `redirect_request` only marks the request it builds, and this class is also the
    opener's request pre-processor (`http_request`/`https_request`): `OpenerDirector.open` runs every
    request it dispatches through its pre-processors, immediately before sending it, and a redirect
    is followed only through `self.parent.open(new)` — so no followed hop can skip `hop`, and none
    is charged before the drain. The sudact channel reserves a slot there, waits for it and rereads
    the shared state, exactly as for a first request; a `ChannelUnavailable` it raises stops the
    chain (`fetch_body` lets it through). The allowlist, the hop cap and `stop` stay at the decision:
    a refused hop is refused before anything else happens.
    """

    HOP_MARK = "_mf_redirect_hop"
    """The attribute `redirect_request` sets on the request it builds, so only a hop pays `hop`."""

    max_repeats = MAX_REDIRECT_HOPS + 1
    max_redirections = MAX_REDIRECT_HOPS + 1
    """urllib's own ceilings, lifted just above `MAX_REDIRECT_HOPS` so this class is the one that
    stops a chain: urllib's answer to a loop is an `HTTPError` carrying the 302, which every caller
    here would read as an ordinary redirect."""

    def __init__(self, allowed: frozenset | None = None, hops: list | None = None, stop=None, hop=None) -> None:
        self.allowed = allowed
        self.hops = [] if hops is None else hops
        self.stop = stop
        self.hop = hop

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib contract
        host = request_host(newurl)
        if not host:
            raise RedirectRefused("redirect_not_allowed", url_error(newurl) or "no_hostname")
        self.hops.append(host)
        if len(self.hops) > MAX_REDIRECT_HOPS:
            raise RedirectRefused("too_many_redirects", str(len(self.hops)))
        if self.allowed is not None and not host_on_allowlist(host, self.allowed):
            raise RedirectRefused("redirect_not_allowed", host)
        refused = self.stop(newurl) if self.stop is not None else None
        if refused:
            raise RedirectRefused(refused)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and self.hop is not None:
            # Charged at dispatch, after urllib has drained this response's body (`http_request`).
            setattr(new, self.HOP_MARK, True)
        return new

    def http_request(self, req):  # noqa: D102 - urllib pre-processor contract
        if self.hop is not None and getattr(req, self.HOP_MARK, False):
            self.hop(req.full_url)
        return req

    https_request = http_request


def _open(
    url: str,
    method: str,
    timeout: float,
    headers: dict | None = None,
    *,
    data: bytes | None = None,
    allowed: frozenset | None = None,
    hops: list | None = None,
    cookies: http.cookiejar.CookieJar | None = None,
    stop=None,
    hop=None,
):
    """One request whose redirects are validated hop by hop (D-151); the caller checked the url.

    D-202: `cookies` is a session's jar, which carries whatever cookie one request was given to the
    next (the sudact search) and requires none; `stop` is a hop the caller refuses by its own rule
    and `hop` what it does before any hop is followed (`RedirectGuard`).
    """
    request = urllib.request.Request(url, data=data, method=method, headers=headers or probe_headers(url))
    handlers: list = [RedirectGuard(allowed=allowed, hops=hops, stop=stop, hop=hop)]
    if cookies is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))
    opener = urllib.request.build_opener(*handlers)
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


_MEDIA_TYPE_RE = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+")
"""RFC 6838 `type/subtype`, each a non-empty run of the restricted-name characters, lower-cased."""


def declared_type(content_type: object) -> str:
    """The media type an answer declares, `type/subtype` lower-cased; `""` when it declares none.

    Final review B: **one** reading of `Content-Type` for every rule that asks — the challenge
    rules (`is_markup`, and through it `is_interstitial` and `markup_to_text`) and the save's
    admission (`save_text_type`). Task 9 read an empty subtype as no type on the save path only (the
    Commission newsroom serves the WP248 PDF as `application/`), while the transport still read it
    as a declared non-markup type: a «verify you are human» wall served as `application/` skipped
    every interstitial rule and was then admitted as text and certified. An empty or malformed type
    or subtype declares nothing, and the body is judged exactly as one that came without the header.
    """
    kind = str(content_type or "").split(";")[0].strip().lower()
    return kind if _MEDIA_TYPE_RE.fullmatch(kind) else ""


def is_markup(payload: bytes, content_type: str = "") -> bool:
    """True when the body is an html page — the only kind the size/ratio rules can judge (D-149).

    The declared type decides when there is one, so a JSON answer that happens to quote `<html` in a
    field is never read as a page; without it the first kilobyte is sniffed. «Declared» is
    `declared_type`: an empty or malformed type is none (final review B).
    """
    kind = declared_type(content_type)
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


_DECIMAL_LIST_TYPES: tuple[str, ...] = ("", "1")
"""D-204: the `<ol type>` values a browser numbers with digits; `a`/`A`/`i`/`I` print no digit."""


def _list_counter(tag: str, attrs: dict) -> int | None:
    """The number before the first item of a list that just opened; None when its items print none.

    D-204: `<ul>` and a list numbered by letters or Roman numerals carry no number into the text —
    a digit the page never showed would be a number the source does not print. So does a
    `reversed` list, whose numbers depend on how many items follow. `start` is honoured, because a
    list that continues at 4 is read as 4 by the person who reads the page.
    """
    if tag != "ol" or "reversed" in attrs:
        return None
    if str(attrs.get("type") or "").strip() not in _DECIMAL_LIST_TYPES:
        return None
    number = _html_int(attrs.get("start"))
    return 0 if number is None else number - 1


def _html_int(value: object) -> int | None:
    """An integer HTML attribute (`start`, `value`), or None when it holds none."""
    try:
        return int(str(value).strip()) if value is not None else None
    except ValueError:
        return None


class _TextExtractor(HTMLParser):
    """Block tags become line breaks, `script`/`style`/`head` vanish, entities are decoded (D-163).

    D-204: an item of an `<ol>` begins with its number (`N. `). A point numbered only by the list
    markup would otherwise vanish from the text, and C-09 would fire on a correct pinpoint. Every
    list keeps its own counter, so a nested list restarts; `<ul>` is unchanged.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._lists: list[int | None] = []
        """The open lists, innermost last: the number of the last item printed, None for no numbers."""

    def handle_starttag(self, tag, attrs):  # noqa: D102 - HTMLParser contract
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in ("ol", "ul"):
            self._lists.append(_list_counter(tag, dict(attrs)))
        elif tag == "li" and self._lists and self._lists[-1] is not None:
            # `<li value="7">` renumbers this item and the ones after it, as the browser does.
            number = _html_int(dict(attrs).get("value"))
            self._lists[-1] = number if number is not None else self._lists[-1] + 1
            if not self._skip:
                self.parts.append(f"{self._lists[-1]}. ")

    def handle_endtag(self, tag):  # noqa: D102
        if tag in _SKIP_TAGS:
            self._skip = max(self._skip - 1, 0)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in ("ol", "ul") and self._lists:
            self._lists.pop()

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

    D-204: the items of an `<ol>` keep the numbers the browser shows (`1. …`), so a point numbered
    only by the list markup is still in the text C-09 searches. The same page therefore converts to
    other bytes than before this rule, and there is no migration of texts saved earlier.
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


def probe_url(
    url: str, *, want_body: bool, timeout: float | None = None, stop=None, hop=None, before_dispatch=None
) -> dict:
    """HEAD, then GET when a body is needed; every failure is best effort (§5.3).

    Final review C: `stop`, `hop` and `before_dispatch` are what `fetch_allowed` takes — a redirect
    hop never followed, what a followed hop costs, and what runs immediately before **each** of the
    two requests goes out. The sudact channel passes its rules through them (`sudact_transport`), so
    a probe of the host is a request of the channel like any other. A `ChannelUnavailable` they raise
    is the channel's refusal and is let through — never read as a dead link. Every other caller
    passes none of them and gets exactly the probe it always got. `truncated` says that the GET body
    ended before the `Content-Length` its server promised, which is what the channel needs to tell a
    cut page from a challenge (`sudact_refusal`).
    """
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
    if before_dispatch is not None:
        before_dispatch()
    try:
        with _open(url, "HEAD", timeout, hops=hops, stop=stop, hop=hop) as response:
            code = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            headers = response.headers
    except urllib.error.HTTPError as exc:
        code = exc.code
        headers = exc.headers
    except RedirectRefused as exc:
        # D-151: the GET would follow the same hop, so it is never sent.
        return {"status": "unchecked", "code": None, "sha256": None, "error": exc.error_name, "redirects": hops}
    except ChannelUnavailable:
        # Final review C: a hop the channel refused to pay for — its answer, not a transport failure.
        raise
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
    if before_dispatch is not None:
        before_dispatch()
    try:
        with _open(url, "GET", timeout, hops=hops, stop=stop, hop=hop) as response:
            code = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            headers = response.headers
            if code in UNCHECKED_HTTP_CODES:
                return _unchecked_http(code, headers, hops)
            payload = response.read(limits.LIVENESS_MAX_BODY_BYTES)
            # Measured on the bytes as they arrived, before `_inflate`, exactly as `fetch_body` does.
            declared = header_value(headers, "Content-Length").strip()
            short = declared.isdigit() and len(payload) < int(declared)
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
    except ChannelUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - timeouts, DNS, TLS: all best effort
        return {"status": "dead", "code": code, "sha256": None, "error": probe_error(exc), "redirects": hops}

    # Final review B: a PDF is the signature, whatever the header says (`is_pdf`) — the save keeps a
    # signed body served as `text/html` as the PDF it is, so the probe neither judges those bytes by
    # the markup rules nor converts them: the original's digest is compared with the body as served.
    signed = is_pdf(payload)
    if code == 200 and not signed and is_interstitial(payload, content_type):
        # D-146: a 200 that carries a challenge page or a JS shell is never `ok` and never hashed.
        return {
            "status": "unchecked",
            "code": code,
            "sha256": None,
            "error": "interstitial_suspected",
            "redirects": hops,
            "truncated": short,
        }

    text = None if signed else markup_to_text(payload, content_type)
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
        "truncated": short,
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


SAVE_METHOD_KEY = "save_method"
"""D-205 fix round 1: `meta.save_method` — the request method `save` used, written by code alone and
only when it is not GET, so a record saved by GET (every record before this rule) carries none."""

SAVE_ENDPOINT_KEY = "save_endpoint"
"""Final review A (D-205): `meta.save_endpoint` — the address a save that is not a GET sent its
request to. The record's `url` is the public page of the document (`--public-url`), because the
endpoint names no document: the Normattiva API answers every article at one address, and the body
decides which. Written by code alone, beside `save_method`, and moved or removed with it."""

ADDRESS_META_KEYS: tuple[str, ...] = (SAVE_METHOD_KEY, SAVE_ENDPOINT_KEY)
"""Final review A: the `meta` keys that describe how the record's url is reached — rewritten together
by every accepted save that reached that url, and cleared when `register` moves the url (Minor 3)."""

TEXT_OUTCOME_KEY = "text_outcome"
"""Final review E (D-199, D-204): `meta.text_outcome` — the outcome of the save that published the text
the record holds, written only when a text is published. `meta.save_outcome` stays what D-199 made it,
the history of the last attempt, which also moves when an attempt publishes nothing (another excerpt
over a held one); the appendix describes the text the client receives, so it reads this key. A
`register --raw-file` that replaces the text, and a save that leaves the record with no text, remove
it: code classified a text that is no longer there."""

CODE_META_KEYS: tuple[str, ...] = (SAVE_METHOD_KEY, SAVE_ENDPOINT_KEY, TEXT_OUTCOME_KEY)
"""The `meta` keys only code writes: `register --meta` refuses each, `save --meta` drops each."""

CODE_META_HINTS: dict[str, str] = {
    SAVE_METHOD_KEY: (
        "meta.save_method is written by `mf sources save` alone: the request method liveness cannot replay"
    ),
    SAVE_ENDPOINT_KEY: "meta.save_endpoint is written by `mf sources save` alone: the address a POST save was sent to",
    TEXT_OUTCOME_KEY: "meta.text_outcome is written by `mf sources save` alone: what code found the text it published",
}
"""One line of advice per refused `CODE_META_KEYS` key."""

LIVENESS_NOT_REPLAYABLE = "method_not_replayable"
"""D-205 fix round 1: why liveness leaves a record `unchecked` without probing it — the record was
saved by a method (the Normattiva `POST`) that a HEAD/GET probe cannot replay."""


def not_probed(record: dict, source_id: str, error: str) -> dict:
    """A record liveness leaves `unchecked` without a request: its reason, nothing sent, nothing promoted.

    D-205 fix round 1 (a method a probe cannot replay) and final review C (a sudact host the run has
    closed, or whose budget is spent): the record says it was not checked, and the row says why.
    """
    record["liveness"] = {"status": "unchecked", "code": None, "checked_at": events.utc_now()}
    return {
        "source_id": source_id,
        "status": "unchecked",
        "code": None,
        "provenance": record.get("provenance"),
        "error": error,
    }


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
            method = str((record.get("meta") or {}).get(SAVE_METHOD_KEY) or "GET").upper()
            if method != "GET":
                # D-205 fix round 1: the address answers only the method `save` used (the Normattiva
                # API refuses HEAD and GET), so a probe would write a correctly saved article down as
                # `dead`. Liveness says it could not check instead of guessing, and promotes nothing.
                # Final review A: the url is now the public page of the document, which is not the
                # body that was saved either — comparing them would only produce a false `changed`.
                checked.append(not_probed(record, source_id, f"{LIVENESS_NOT_REPLAYABLE}: {method}"))
                continue
            on_sudact = sudact_address(url)
            if on_sudact:
                # Final review C: the channel is the host, whoever is asking. A host the run has closed
                # (the captcha marker) is asked nothing — not even after a politeness pause.
                try:
                    channel_check_open(work_dir, SUDACT_CHANNEL)
                except ChannelUnavailable as exc:
                    checked.append(not_probed(record, source_id, resolve_channel_refusal(exc)["errors"][0]))
                    continue
            # D-146: one loop over the registry is one crawler as far as the host is concerned.
            # D-151: the host is the one `probe_url` will really call, or `""` for a url it refuses.
            host = request_host(url)
            since = last_probe.get(host)
            if host and since is not None:
                _wait(host_delay(host) - (time.monotonic() - since))
            try:
                if on_sudact:
                    # Final review C: its slot, pace and count, the stop before `/defence/`, and the
                    # marker for any challenge the probe meets (`channel_probe`).
                    probe = channel_probe(work_dir, url, want_body=bool(expected), timeout=args.timeout)
                else:
                    probe = probe_url(url, want_body=bool(expected), timeout=args.timeout)
            except ChannelUnavailable as exc:
                # The channel refused before a request went out (a spent budget, a marker another
                # process wrote while this one waited): nothing was asked, nothing is known.
                checked.append(not_probed(record, source_id, resolve_channel_refusal(exc)["errors"][0]))
                continue
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
    extra_headers: dict | None = None,
    cookies: http.cookiejar.CookieJar | None = None,
    stop=None,
    hop=None,
) -> dict:
    """One request through the liveness client; an HTTP error answer is read, never raised (D-149).

    D-151: `allowed` is the fetch allowlist and every redirect hop is checked against it before it is
    followed; `method`/`body` carry the POST route the routing notes prescribe.

    D-202: the resolver channels speak through here too. `extra_headers` joins the liveness headers
    (the sudact search is an `XMLHttpRequest` with a `Referer`), `cookies` carries a session from
    one request to the next, `stop` is a redirect hop the caller never follows and `hop` what the
    caller does before any hop is followed (`RedirectGuard`). A `ChannelUnavailable` raised by `hop`
    is the caller's own refusal and is let through, not turned into a transport error. Every
    existing caller passes none of the four and gets exactly the request it always got.
    """
    timeout = limits.LIVENESS_TIMEOUT_SECONDS if timeout is None else timeout
    cap = limits.LIVENESS_MAX_BODY_BYTES
    headers = fetch_headers(url, accept=accept, lang=lang)
    if extra_headers:
        headers.update(extra_headers)
    if body is not None:
        headers.setdefault("Content-Type", JSON_CONTENT_TYPE)
    hops: list = []
    opened = {"allowed": allowed, "hops": hops, "cookies": cookies, "stop": stop, "hop": hop}
    try:
        with _open(url, method, timeout, headers=headers, data=body, **opened) as response:
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
    except ChannelUnavailable:
        # D-202: the caller's own `hop` refused to follow — its answer, not a transport failure.
        raise
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


PUBLIC_URL_HINT = (
    "a save that is not a GET names its document by the public page a client can open: pass it as "
    "--public-url (for a Normattiva article, https://www.normattiva.it/uri-res/N2Ls?<the URN of --json>)"
)
"""Final review A: the advice of `public_url_required`."""


def public_url_refusal(method: str, address: str) -> dict | None:
    """Why this save's `--public-url` cannot stand, or None when it can (final review A, D-205).

    A save that is not a GET must name the public page of the document it fetches: the transport
    endpoint serves many documents at one address, so it identifies none, and a client cannot open
    it. That page becomes the record's `url` — identity, the duplicate search and the client's link.
    It must be an address a client may be given (`public_url`), and it is checked before anything is
    read or sent. A GET takes none: the address it fetched is the public one, and a second address
    would be a second identity.
    """
    if method == "GET":
        if not address:
            return None
        return {
            "errors": ["public_url_requires_post"],
            "hint": "--public-url names the document of a --method POST; a GET's own --url is the public address",
        }
    if not address:
        return {"errors": ["public_url_required"], "hint": PUBLIC_URL_HINT}
    problem = url_error(address)
    if problem is None and not public_url(address)[0]:
        problem = URL_NOT_PUBLIC
    if problem is not None:
        return {"errors": [f"public_url_required: {problem}"], "hint": PUBLIC_URL_HINT}
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
    stop=None,
    hop=None,
    before_dispatch=None,
) -> dict:
    """The politeness pause this process owes the host, then one request (D-146, D-149, D-199).

    The caller has already run `fetch_refusal`; `hosts` travels on so every redirect hop is checked
    against the same list. D-202: `stop` and `hop` go to `RedirectGuard` (a hop never followed, and
    what is done before any hop is), and `before_dispatch` runs **after** the pause and immediately
    before the request goes out — `mf sources save` rereads the sudact marker there, because another
    process may have closed the host while this one waited.
    """
    host = request_host(url)
    since = _LAST_FETCH.get(host)
    if since is not None:
        _wait(host_delay(host) - (time.monotonic() - since))
    if before_dispatch is not None:
        before_dispatch()
    answer = fetch_body(
        url, accept=accept, lang=lang, timeout=timeout, method=method, body=body, allowed=hosts, stop=stop, hop=hop
    )
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

    # Final review C: a request to the sudact host is a request of its channel, whichever command
    # sends it — the shutdown check first (a closed host costs no pause either), then its slot, the
    # stop before `/defence/`, and the marker for any challenge the answer carries.
    on_sudact = sudact_address(url)
    try:
        if on_sudact:
            channel_check_open(work_dir, SUDACT_CHANNEL)
        answer = fetch_allowed(
            url,
            hosts,
            accept=args.accept,
            lang=args.lang,
            timeout=args.timeout,
            method=method,
            body=body,
            **(sudact_transport(work_dir) if on_sudact else {}),
        )
    except ChannelUnavailable as exc:
        return {key: value for key, value in resolve_channel_refusal(exc).items() if key != "candidates"}
    challenge = sudact_challenge_met(work_dir, answer, answer["payload"]) if on_sudact else None

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
    if challenge is not None:
        # The host is closed for the run from here on, for every command (`channel_unavailable: captcha`).
        result["challenge"] = challenge
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
"""D-199: `--resolve` is declared with the command; both resolvers have landed (D-202, tasks 6 and 7)."""

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

PDF_SIGNATURE = b"%PDF-"
PDF_EXTENSION = ".pdf"
"""D-201: what a PDF answer is (`is_pdf`: the signature, never the declared type), and the name its
original is kept under (`<source_id>.pdf`)."""

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


def is_pdf(payload: bytes) -> bool:
    """True when the body is a PDF: the `%PDF-` signature in its first `PDF_HEADER_WINDOW` bytes.

    Final review B: **one** rule for the resolver and the save, the one Task 6 proved on the real
    chain (D-202), and the header does not enter it — in either direction. A signed body is a PDF
    even when it is served as `application/octet-stream` or `text/html` (the real `2394482.pdf`
    certified in the resolver under both, and the save refused the one and decoded the other as
    markup); a body without the signature is not a PDF even when it declares `application/pdf`, so
    it is never kept as an «original». The window is the one the PDF specification allows for
    leading junk before the header.
    """
    return PDF_SIGNATURE in payload[:PDF_HEADER_WINDOW]


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

    A PDF never is (`is_pdf`, whatever it is declared as): it takes the PDF path of D-201 instead of
    being decoded with replacement characters and exported to the client as a `.txt` of mojibake.
    Otherwise a declared type decides, and with none declared the body is admitted as text — many
    portals send no header. «Declared» is `declared_type`: an empty or malformed type is none.
    """
    if is_pdf(payload):
        return False
    kind = declared_type(content_type)
    if not kind:
        return True
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
    pdf = is_pdf(payload)
    if error == "interstitial_suspected":
        # Final review B: the signature decides both ways, as it does for the resolver's candidates —
        # a signed body served as `text/html` is a PDF, whatever the markup rules suspected of it.
        if not pdf:
            return ("access_stub" if is_access_stub(payload) else "interstitial"), ""
    elif answer.get("status") == "unchecked":
        return "unchecked", error
    if answer.get("status") == "dead":
        return "dead", error
    if answer.get("truncated"):
        # A partial document is the exact failure this command exists to stop: never an excerpt.
        return ("pdf_truncated" if pdf else "truncated"), ""
    if pdf:
        return None
    if not save_text_type(answer.get("content_type"), payload):
        # Only a declared type reaches this: a PDF was admitted above, and with no type declared
        # every other body is text.
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


# --- resolve --------------------------------------------------------------


VSRF_BASE = "https://www.vsrf.ru"
"""D-202: the portal `--resolve vsrf` asks. Injectable, because the tests drive a `LocalServer`."""

VSRF_LISTING_PATH = "/lk/practice/acts"
"""The practice index of the Supreme Court; `robots.txt` allows it (`Allow: /`, `Disallow: /api/`)."""

VSRF_NUMBER_SUFFIX_RE = re.compile(r"\s*\([\d\s,]*\)$")
"""D-202: the trailing bracketed group of a Russian case number — the `(1,3)` that names the twin.

**Search broadly, certify precisely.** The portal's `numberExact` index does not know the suffix:
asked on 2026-09-21, `number=305-ЭС24-8702` answers with the whole chain of eight acts and
`number=305-ЭС24-8702 (1,3)` answers with none — so asking it verbatim would fail on exactly the
case the resolver exists to close, because a suffix is carried only where there are twins to tell
apart. Certification is untouched and keeps `--expect-number` whole: the suffix is the only thing
that tells `(1,3)` from `(2,4)`. The shape is the one `source_text.has_number` already refuses to
match across, and it is stripped only where a **Russian** resolver builds its query — vsrf and
sudact, through the one helper `resolve_query_number`, so the two never disagree about what «the
number» is — and in no helper shared with other jurisdictions: they put other things in brackets.
"""

PDF_HEADER_WINDOW = 1024
"""D-202: how far into a body the `%PDF-` signature may stand and the body still be a PDF.

The PDF specification allows leading junk before the header; readers look for it in the first
kilobyte, and real servers do prepend whitespace or a byte-order mark.
"""

VSRF_ACT_PATH_RE = re.compile(rb"/lk/practice/stor_pdf(?:_ec)?/\d+")
"""D-202: the two shapes of a link to an act's own PDF, matched on the bytes of the listing.

The page is a React shell that prints every link once in its markup and once inside the JSON it
hydrates from — six copies of each address on the measured page — and neither copy is reached by
parsing the markup alone. The addresses are deduplicated afterwards, which is what makes the cap
count acts rather than copies.
"""

SUDACT_BASE = "https://sudact.ru"
"""D-202: the portal `--resolve sudact` asks. Injectable, because the tests drive a `LocalServer`."""

SUDACT_CHANNEL = "sudact"
"""The channel's name — in `--resolve` and in `channels.json`."""

SUDACT_ARBITRAL = "arbitral"
SUDACT_REGULAR = "regular"
"""The two sections of the portal the resolver asks: arbitration courts, and general jurisdiction."""

SUDACT_ARBITRAL_NUMBER_RE = re.compile(r"^[АA]\d+-\d+/(?:\d{4}|\d{2})(?!\d)")
"""D-202: the naming rule that sends a number to `arbitral` — `А`/`A`, digits, `-`, digits, `/`, a year.

Cyrillic `А` and Latin `A` both, because they look alike and both occur. The year is four digits
(`А53-28950/2022`) or two with more after them (`А40-630/25-100-1`, the Moscow court's own form).
"""

SUDACT_SEARCH_ACCEPT = "application/json, text/javascript, */*; q=0.01"
"""What the portal's own search script asks for; the answer is JSON."""

SUDACT_FINISHED = "finished"
SUDACT_NOT_RESOLVED = "not_resolved"
"""The `status` of a search that has its list, and the reason printed when none came in time."""

SUDACT_DEFENCE_REDIRECT = "defence_redirect"
"""What a hop to the portal's captcha at `/defence/` is reported as; the hop itself is never followed."""

SUDACT_SAVE_CHALLENGES: frozenset = frozenset({"unchecked", "host_not_allowed"})
SUDACT_SAVE_BODY_CHALLENGES: frozenset = frozenset({"interstitial", "access_stub"})
"""D-202: the admission refusals of a save to the sudact host that mean the portal would not serve us.

Each writes the captcha marker, exactly as on the resolver's own requests, for a resolved save and
a plain `--url` alike: the wall is the host's, whoever meets it first. They are two kinds, in the
order `sudact_refusal` already judges them (task 7, round 3):

- `SUDACT_SAVE_CHALLENGES` are named **without the body** — an anti-bot or throttle status and the
  WAF and Cloudflare headers among them (`unchecked`), a redirect hop off the allowlist
  (`host_not_allowed`: at admission the address itself has already passed it). They close the host
  **whatever the body's length**: a 202 with `x-amzn-waf-action` cut short of its `Content-Length`
  is a challenge all the same.
- `SUDACT_SAVE_BODY_CHALLENGES` are judged **on the body** (`interstitial`, `access_stub`), and a
  body cut short of its `Content-Length` proves nothing about a challenge — Task 4's admission still
  refuses it, but it closes nothing.
"""

CHANNEL_STATE_FILENAME = "channels.json"
CHANNEL_STATE_VERSION = 1
CHANNEL_CAPTCHA = "captcha"
"""D-202: `<work_dir>/channels.json`, its schema version, and the reason a closed channel prints."""

RESOLVE_HINTS: dict[str, str] = {
    "requisites_required": (
        "--resolve searches for --expect-number and tells the acts of one case apart by --expect-date"
    ),
    "channel_unavailable": "the portal did not answer with its index of acts; use a fallback route and --url",
    "captcha": (
        "the portal answered with a challenge: it is never solved or retried, and the channel stays closed "
        "for this run; use a fallback route (LDH RU/Sudact, web search) and --url"
    ),
    "channel_budget_spent": (
        "this run has spent the sudact.ru request budget, which every request to the host counts against, "
        "save --url included: nothing more is sent there in this run — use a fallback route (LDH RU/Sudact, "
        "web search), keep LDH's own answer as an excerpt, and save a copy on another allowed host with --url"
    ),
    "clock": (
        "channels.json holds a reservation no run could have made (a clock stepped back, or an edited "
        "file); nothing was sent — use a fallback route and --url"
    ),
    "requisites_ambiguous": "several different acts carry these requisites; pass one of `candidates` as --url",
    "requisites_mismatch": "no candidate carried both requisites; `candidates` is what the portal offered",
    "resolved_save_mismatch": (
        "the resolver certified this address, but the save's own fetch of it did not carry both requisites: "
        "the portal served something else the second time, and nothing was saved"
    ),
}
"""One line of advice per resolver refusal (D-202).

`requisites_mismatch` is the refusal of D-199 under its own name, and its advice differs here for
one reason: at `--url` a single page did not carry the requisites, while here a whole chain was
read and the answer can hand the researcher the addresses it read. It is also the answer when the
index named no act at all — `candidates` is then empty, and the number is what to look at again.
`channel_unavailable` is kept for the other fact: the index did not arrive. `captcha` is the advice
of `channel_unavailable: captcha`, and `channel_budget_spent` the answer of a spent budget (sudact).
"""

SAVE_OUTCOME_REFUSALS: frozenset = frozenset(SAVE_HINTS) | {
    CHANNEL_CAPTCHA,
    "channel_unavailable",
    "channel_budget_spent",
}
"""D-205: the codes a `register --meta` `save_outcome` may name — `refused:<code>`, nothing else.

The refusals `save` stamps itself (`SAVE_HINTS`, and `captcha` for a closed host) plus the two
answers of a resolver's channel, which stamp nothing because the resolver stops before the save
transaction begins. `requisites_ambiguous` is not among them: it asks for a choice, not a
fallback. A `full_text` or `excerpt:*` outcome is written by `save` alone.
"""


class ChannelUnavailable(RuntimeError):
    """D-202: the resolver's channel did not answer with a listing at all.

    Not «this number lists nothing»: that is a listing that arrived intact and named no act, and it
    is a `requisites_mismatch`. The two facts send the researcher to different places — a broken
    channel means «use the fallbacks of the routing table», a number that lists nothing means «look
    at the number again» — and answering the first when it is the second costs them the right move.
    `resolve_vsrf` returns the addresses it read, so the failure needs a channel of its own, and
    this is the narrowest one. Its `str` is the transport's own reason (`http_404`,
    `host_not_allowed`, `truncated`, `cloudflare_challenge`, a timeout…), which is what the refusal
    prints.
    """


class ChannelCaptcha(ChannelUnavailable):
    """D-202: the portal answered a request of the channel with a challenge. Its `str` is `captcha`.

    **A captcha is never solved, never worked around and never retried.** By the time this is
    raised the marker is already in `channels.json`, so the channel stays closed for the rest of the
    run — in this call and in every later one, in any process. `seen` says what gave the challenge
    away (`defence_redirect`, `http_429`, `not_json`, `access_stub`, `marker` …).
    """

    def __init__(self, seen: str) -> None:
        super().__init__(CHANNEL_CAPTCHA)
        self.seen = seen


class ChannelBudgetSpent(ChannelUnavailable):
    """D-202: the run has made `CHANNEL_MAX_REQUESTS_PER_RUN` requests of this channel already."""

    def __init__(self) -> None:
        super().__init__("channel_budget_spent")


class ChannelClock(ChannelUnavailable):
    """D-202 (round 2): `channels.json` holds a slot no run could have reserved. Its `str` is `clock`.

    Further ahead than the whole budget queued at once — a clock stepped back, or an edited file. It
    is neither permission to send nor a reason to wait until then: the channel refuses, writes no
    marker and sends nothing.
    """

    def __init__(self) -> None:
        super().__init__("clock")


def resolve_query_number(number: str) -> str:
    """The number a Russian resolver asks its portal for: the trailing bracketed group dropped (D-202).

    **Search broadly, certify precisely** (`VSRF_NUMBER_SUFFIX_RE`): certification keeps
    `--expect-number` whole. A number that is nothing but a bracketed group is sent whole — an empty
    query is a search for everything, which is the one thing this request must never be.
    """
    return VSRF_NUMBER_SUFFIX_RE.sub("", number.strip()) or number.strip()


def vsrf_listing_url(number: str, base: str) -> str:
    """The portal's answer for one case number (D-202); the parameter order is the channel's.

    The number is asked **without its bracketed suffix** (`resolve_query_number`).
    """
    return (
        base.rstrip("/")
        + VSRF_LISTING_PATH
        + "?numberExact=true&actDateExact=off&number="
        + urllib.parse.quote(resolve_query_number(number), safe="")
    )


def vsrf_candidates(payload: bytes, listing_url: str) -> list[str]:
    """The act addresses this listing prints — unique by address, in page order, capped (D-202)."""
    seen: dict[str, None] = {}
    for match in VSRF_ACT_PATH_RE.finditer(payload):
        seen.setdefault(urllib.parse.urljoin(listing_url, match.group().decode("ascii")), None)
    return list(seen)[: limits.RESOLVE_MAX_CANDIDATES]


def challenge_name(payload: bytes) -> str:
    """The name a refused challenge body is reported under — the vocabulary `save_admission` uses."""
    return "access_stub" if is_access_stub(payload) else "interstitial"


def vsrf_listing_refusal(payload: bytes, content_type: str) -> str | None:
    """Why this body is not the portal's index: `is_interstitial` with the text-ratio rule set aside.

    The ratio rule is the one that cannot be applied: the real index is a React shell whose visible
    text is 0.029 of its bytes — under `LIVENESS_MIN_TEXT_RATIO`. That rule measures *heaviness*,
    and a listing is heavy by construction. The other three measure something a listing never is,
    so they stay: a markup body
    under `LIVENESS_MIN_BODY_BYTES` is a shell (the portal's own «nothing found» is 195 457 bytes),
    `is_redirect_shell` is a page whose only content is a redirect, and `is_access_stub` is a bot
    wall saying so in its own words. Setting those aside too would read a captcha as «a page with no
    act links» and send the researcher to re-check a number when the channel was in fact blocked.
    """
    if not is_markup(payload, content_type):
        return None
    if len(payload) < limits.LIVENESS_MIN_BODY_BYTES or is_redirect_shell(payload) or is_access_stub(payload):
        return challenge_name(payload)
    return None


def resolve_vsrf(number: str, *, base: str, timeout: float) -> list[str]:
    """Addresses of the acts the Supreme Court portal lists for `number`, in page order (D-202).

    One GET of the practice index, through the same client as everything else: the allowlist on the
    address and on every redirect hop, the politeness pause, the liveness headers.

    A listing is **not** a document and is not judged as one: of the four interstitial rules, the
    text-ratio rule is set aside and the other three are not (`vsrf_listing_refusal`). A challenge,
    and every transport failure, raises `ChannelUnavailable` — the address was refused, the request
    did not complete, the server answered with something that is not the index, the body is a
    challenge, or it was cut at the ceiling (a chain cut short may be missing exactly the act being
    looked for). An empty list means the opposite: the index arrived whole and named no act at all.
    """
    hosts = allowlist_hosts()
    url = vsrf_listing_url(number, base)
    refused = fetch_refusal(url, hosts)
    if refused is not None:
        raise ChannelUnavailable(refused[0])
    answer = fetch_allowed(url, hosts, timeout=timeout)
    payload = answer.pop("payload")
    error = answer["error"]
    if error is not None and error != "interstitial_suspected":
        raise ChannelUnavailable(error)
    if answer["truncated"]:
        raise ChannelUnavailable("truncated")
    # `interstitial_suspected` is judged again, with the ratio rule set aside and nothing else.
    challenge = vsrf_listing_refusal(payload, answer["content_type"])
    if challenge is not None:
        raise ChannelUnavailable(challenge)
    return vsrf_candidates(payload, url)


def resolve_text_key(text: str) -> str:
    """The digest that tells two candidates apart: the text layer, whitespace runs collapsed (D-202).

    Never the bytes. On the measured chain every act is served under two ids with **different** PDF
    bytes and the **same** text, so a byte digest would count one act twice and answer
    `requisites_ambiguous` on every real search. The normalisation is the one `source_text.flatten`
    applies to whitespace and nothing more — `\\s+` runs collapsed to one space, the ends stripped —
    because case, letter spacing and dashes are what `flatten` folds to *match* a token, while here
    two texts are being told apart and a fold can only hide a difference.
    """
    return state_io.sha256_bytes(" ".join(text.split()).encode("utf-8"))


def certify_candidates(candidates: list, *, number: str, date: str, timeout: float, text_of=None) -> dict:
    """Which candidates carry both requisites — one address per distinct act (D-202).

    Each candidate is certified on its text alone: `source_text` must find the number as a whole
    token in the number zone (the bracketed suffix is what tells the twins apart) and the date in
    the date zone — never in the metadata around the document.

    `text_of(url)` fetches one candidate and returns its text, None for one that cannot be certified
    (it is skipped), or raises `ChannelUnavailable`, which stops the channel before any later
    candidate is asked. The default is the vsrf PDF store (`vsrf_candidate_text`); `--resolve sudact`
    passes its own document fetch (`sudact_document_text`). The certification is the same for both.
    """
    fetch_text = text_of or functools.partial(vsrf_candidate_text, hosts=allowlist_hosts(), timeout=timeout)
    found = {"number": False, "date": False}
    distinct: dict[str, str] = {}
    for url in candidates:
        text = fetch_text(url)
        if text is None:
            continue
        carries_number = source_text.has_number(text, number, source_text.number_zone(text))
        carries_date = source_text.has_date(text, date, source_text.date_zone(text))
        found["number"] = found["number"] or carries_number
        found["date"] = found["date"] or carries_date
        if carries_number and carries_date:
            distinct.setdefault(resolve_text_key(text), url)
    return {"found": found, "distinct": list(distinct.values())}


def vsrf_candidate_text(url: str, *, hosts: frozenset, timeout: float) -> str | None:
    """The text layer of one vsrf candidate, or None for a whole PDF without one (D-202).

    Each is fetched under the same politeness pause and the same allowlist as any other request.

    **A captcha is never solved, never worked around and never retried**, and the next fetch after a
    challenge *is* the retry. So a candidate that did not arrive as a whole PDF stops the channel at
    once and no later candidate is asked: a refused address, a request that did not complete, a
    non-2xx or challenge answer, a truncated body, or a body that is **not a PDF at all** — no
    `%PDF-` signature in its first `PDF_HEADER_WINDOW` bytes, **whatever `Content-Type` says**. That
    last rule is what catches a challenge served as 200 in place of the document — declared
    `application/pdf` included — without a list of challenge vendors to keep: `stor_pdf_ec/<id>` is
    a PDF store, and anything else in its place is not an act. It is also the honest answer to a
    partial chain — the act that did not arrive may be exactly the one looked for. The signature
    decides in **both** directions: a signed body is a PDF even when it is served as `text/html` and
    the transport's interstitial rules suspect it, so the body is never judged by those rules here.

    A PDF that arrived whole and has no text layer — a scan, a file `pypdf` cannot parse, no
    `pypdf` in the environment — is a different thing: it cannot be certified, so it is **skipped**,
    and the caller lists the addresses for the researcher to decide with `--url`.
    """
    refused = fetch_refusal(url, hosts)
    if refused is not None:
        raise ChannelUnavailable(refused[0])
    answer = fetch_allowed(url, hosts, timeout=timeout)
    payload = answer.pop("payload")
    # The failures that are not about the body stop the channel before the body is judged.
    # `interstitial_suspected` is left out on purpose: it is a verdict *about the body*, and for
    # a candidate the body is judged by its signature alone, in both directions — so that
    # verdict has nothing left to decide here and is not consulted at all.
    error = answer["error"]
    if error is not None and error != "interstitial_suspected":
        raise ChannelUnavailable(error)
    if answer["truncated"]:
        raise ChannelUnavailable("truncated")
    # A PDF is a body carrying the `%PDF-` signature, whatever `Content-Type` says, and the
    # signature decides both ways. Without it the body is not an act, whatever it declares: a
    # challenge that declares `application/pdf` is not markup to `is_interstitial`, and `pypdf`
    # would only find it has no text layer and skip it as a scan — the next fetch being the
    # retry. With it the body is a PDF, whatever it declares: a signed scan served as `text/html`
    # is markup under the size floor to `is_interstitial`, yet it is not a challenge. The
    # signature may stand anywhere in the first `PDF_HEADER_WINDOW` bytes, because the PDF
    # specification allows leading junk before the header and real servers do emit it.
    if not is_pdf(payload):
        raise ChannelUnavailable("not_a_pdf")
    return extract_pdf_text(payload)


# --- resolve: the channel state (D-202) ---------------------------------------------------------


def channel_state_path(work_dir: str | os.PathLike) -> Path:
    """`<work_dir>/channels.json` — beside `research/`, deliberately outside the globs the freeze stages.

    The freeze stages `research/*.json` as the inputs of the pack. The pace of a portal is not an
    input of the memo, and a file three processes rewrite all through the research phase must never
    be pinned into a step's inputs.
    """
    return Path(work_dir) / CHANNEL_STATE_FILENAME


def _channel_entry() -> dict:
    """The state of a channel nobody has asked yet: no request, no marker."""
    return {"last_request": 0, "requests": 0, "captcha": False}


def _is_channel_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    last, count = entry.get("last_request"), entry.get("requests")
    return (
        isinstance(last, (int, float))
        and not isinstance(last, bool)
        and math.isfinite(last)
        and type(count) is int
        and count >= 0
        and isinstance(entry.get("captcha"), bool)
    )


def read_channel_state(work_dir: str | os.PathLike) -> dict:
    """`channels.json`, or a fresh document when there is none that can be read as one (D-202).

    `{"schema_version": 1, "channels": {"<channel>": {"last_request": <epoch>, "requests": <int>,
    "captcha": <bool>}}}`. A file that cannot be parsed, or parses into anything else, is treated as
    absent and rewritten by the next reservation — **never raised on**: a corrupt pace file must not
    stop a researcher from working.
    """
    fresh = {"schema_version": CHANNEL_STATE_VERSION, "channels": {}}
    try:
        document = state_io.read_json(channel_state_path(work_dir))
    except (OSError, ValueError, RecursionError):
        return fresh
    if not isinstance(document, dict) or document.get("schema_version") != CHANNEL_STATE_VERSION:
        return fresh
    channels = document.get("channels")
    if not isinstance(channels, dict) or not all(_is_channel_entry(entry) for entry in channels.values()):
        return fresh
    return {"schema_version": CHANNEL_STATE_VERSION, "channels": channels}


def _channel_clock() -> float:
    """Epoch seconds — the clock a channel's pace is kept in; the tests replace this function (D-202).

    Wall-clock time and not `time.monotonic()`: the value is written to a file and compared by other
    processes, and a monotonic reading means nothing outside the process that took it.
    """
    return time.time()


def channel_reserve(work_dir: str | os.PathLike, channel: str) -> float:
    """Reserve the next request slot of `channel`; the seconds to wait before it comes (D-202).

    Briefly, under the existing `sources.lock`: read `channels.json`, refuse on the captcha marker
    (`ChannelCaptcha`), a spent budget (`ChannelBudgetSpent`) or an impossible reservation
    (`ChannelClock`), take the slot — `CHANNEL_MIN_INTERVAL_S` after the last one reserved by anyone,
    or now — count the request and write the file. **The slot is reserved by writing its time**, so a
    process that arrives while this one is still waiting gets the slot after it, not the same one.

    **The wait is not here: it happens outside the lock** (`channel_slot`). That is the whole reason
    the reservation and the wait are two steps — three researcher processes must never queue on a
    held lock, and a lock held through a two-second pause would make every other researcher's save,
    registration and freeze wait out the portal's pace as well.
    """
    with sources_lock(work_dir):
        document = read_channel_state(work_dir)
        entry = document["channels"].setdefault(channel, _channel_entry())
        if entry["captcha"]:
            raise ChannelCaptcha("marker")
        if entry["requests"] >= limits.CHANNEL_MAX_REQUESTS_PER_RUN:
            raise ChannelBudgetSpent()
        now = _channel_clock()
        last = entry["last_request"]
        if last > now + limits.CHANNEL_MAX_REQUESTS_PER_RUN * limits.CHANNEL_MIN_INTERVAL_S:
            # Round 2, item 4: no run can have reserved a slot that far ahead — even its whole budget
            # queued at once ends sooner — so this is not a state this code wrote honestly: a clock
            # stepped back, or an edited file. Never permission to send (a reset would buy an
            # immediate request), never an unbounded wait (the strict rule would hang until then):
            # a refusal, no marker, nothing written.
            raise ChannelClock()
        slot = max(now, last + limits.CHANNEL_MIN_INTERVAL_S)
        entry["last_request"] = slot
        entry["requests"] += 1
        state_io.write_json_atomic(channel_state_path(work_dir), document)
    return slot - now


def channel_check_open(work_dir: str | os.PathLike, channel: str) -> None:
    """Refuse (`ChannelCaptcha("marker")`) if `channel` was closed — the reread before a dispatch (D-202).

    Round 2, item 1: a process that reserved its slot and then waited outside the lock may wake to a
    channel another process closed meanwhile; this is asked after every wait and immediately before
    the request goes out. The read is taken under `sources.lock`, briefly, and that is deliberate:
    `read_channel_state` fails **open** by design (a file it cannot read is treated as absent, so as
    «no marker»), and the lock is what guarantees this read never meets a writer mid-replace.

    **Do not try to close the window left.** Between this read under the lock and the bytes leaving
    the socket there is still a gap, and it cannot close without holding the lock through the
    request, which the pace forbids; it is microseconds against the two-second window this reread
    closes.
    """
    with sources_lock(work_dir):
        closed = read_channel_state(work_dir)["channels"].get(channel, {}).get("captcha")
    if closed:
        raise ChannelCaptcha("marker")


def channel_slot(work_dir: str | os.PathLike, channel: str) -> None:
    """One request's slot of `channel`: reserved under the lock, waited for **outside** it, then the
    state reread immediately before the request goes out (D-202)."""
    wait = channel_reserve(work_dir, channel)
    if wait > 0:
        _wait(wait)
    channel_check_open(work_dir, channel)


def channel_hop(work_dir: str | os.PathLike, channel: str, newurl: str = "") -> None:
    """What a redirect hop to the sudact host costs before it is followed (`RedirectGuard.hop`, D-202).

    Round 2, item 2: a followed hop is a request, so it takes a slot exactly as a first request does —
    reservation, wait, count and reread — and a hop can neither skip the pace nor go past the budget.
    Final review C: every request to the host is a request of the channel, whoever sends it, so there
    is no longer a hop that only rereads the marker (a plain save's used to).
    """
    channel_slot(work_dir, channel)


def channel_mark_captcha(work_dir: str | os.PathLike, channel: str) -> None:
    """Close `channel` for the rest of the run: the captcha marker, written under `sources.lock` (D-202)."""
    with sources_lock(work_dir):
        document = read_channel_state(work_dir)
        document["channels"].setdefault(channel, _channel_entry())["captcha"] = True
        state_io.write_json_atomic(channel_state_path(work_dir), document)


# --- resolve: sudact (D-202) --------------------------------------------------------------------


def sudact_section(number: str) -> str:
    """The section of the portal a case number is asked in — one naming rule, not a network fact (D-202).

    An arbitration case number — `А` or `A` (the Cyrillic and the Latin letter look alike, and both
    occur), digits, `-`, digits, `/`, a year — asks `arbitral`: `А53-28950/2022`, and the Moscow
    court's `А40-630/25-100-1`, whose year has two digits and something after it. Everything else
    asks `regular`.
    """
    return SUDACT_ARBITRAL if SUDACT_ARBITRAL_NUMBER_RE.match(resolve_query_number(number)) else SUDACT_REGULAR


def sudact_search_url(number: str, section: str, base: str) -> str:
    """The portal's ordinary search for one case number: `/<section>/doc_ajax/`, first page (D-202)."""
    asked = urllib.parse.quote(resolve_query_number(number), safe="")
    return f"{base.rstrip('/')}/{section}/doc_ajax/?{section}-case_doc={asked}&page=1"


def sudact_defence(newurl: str) -> str | None:
    """`RedirectGuard.stop` of the channel: the hop to the portal's captcha is never followed (D-202)."""
    path = urllib.parse.urlsplit(newurl).path
    return SUDACT_DEFENCE_REDIRECT if path == "/defence" or path.startswith("/defence/") else None


def sudact_address(url: object) -> bool:
    """Whether `url` is on the sudact host — the host of `SUDACT_BASE`, or a subdomain of it (D-202).

    The wall is the host's, not the search endpoint's: a portal that challenged the search challenges
    its document pages too. So every save to this host — resolved or a plain `--url` — refuses the
    hop into `/defence/` before following it, and after the captcha marker is set refuses before
    any request (`run_save`). Final review C: so does every other request to the host — a fetch,
    liveness, the preflight probe — and each is paced and counted by the channel (`sudact_transport`).
    """
    host = request_host(url)
    channel = request_host(SUDACT_BASE)
    return bool(host) and bool(channel) and (host == channel or host.endswith("." + channel))


def channel_challenge(work_dir: str | os.PathLike, seen: str) -> ChannelCaptcha:
    """Write the captcha marker, and return the exception that says so (D-202)."""
    channel_mark_captcha(work_dir, SUDACT_CHANNEL)
    return ChannelCaptcha(seen)


def sudact_transport(work_dir: str | os.PathLike) -> dict:
    """The channel's rules on one request to the sudact host, for any command that sends one.

    Final review C (D-202): **the channel is the host, whoever is asking.** The resolver's requests
    (`sudact_fetch`), a save's — resolved or a plain `--url` — a fetch's, liveness's and the
    preflight probe's all pay the same: `before_dispatch` reserves a slot under `sources.lock`, waits
    for it outside the lock and rereads the marker immediately before the request goes out
    (`channel_slot`), so the request is paced and counted against the one budget; `stop` refuses the
    hop into `/defence/` before it is requested; `hop` makes every followed hop pay its own slot.
    The three keys are the ones `fetch_allowed` and `probe_url` take.
    """
    return {
        "stop": sudact_defence,
        "hop": functools.partial(channel_hop, work_dir, SUDACT_CHANNEL),
        "before_dispatch": functools.partial(channel_slot, work_dir, SUDACT_CHANNEL),
    }


def sudact_challenge_met(work_dir: str | os.PathLike, answer: dict, payload: bytes = b"") -> str | None:
    """Write the marker when this answer of the sudact host is a challenge; what gave it away, or None.

    Final review C: one rule for every command, and it is the resolver's own (`sudact_refusal`, an HTML
    endpoint): a refused hop into `/defence/` or off the chain, an anti-bot or throttle status, and —
    on a body that arrived whole — the interstitial rules. A broken request (a timeout, a 404, a body
    cut short) is no challenge and closes nothing. Used by the commands whose answer is not already
    judged by the resolver or the save's admission: liveness, fetch and the preflight probe.
    """
    try:
        sudact_refusal(work_dir, {"truncated": False, **answer}, payload, markup=True)
    except ChannelCaptcha as exc:
        return exc.seen
    except ChannelUnavailable:
        return None
    return None


def channel_probe(work_dir: str | os.PathLike, url: str, *, want_body: bool, timeout: float | None = None) -> dict:
    """`probe_url` of an address on the sudact host, under the channel's rules (final review C).

    Each of the probe's requests takes its slot (`sudact_transport`), the hop into `/defence/` is
    never followed, and a challenge the probe meets writes the marker. Raises `ChannelUnavailable`
    when the channel refuses before a request goes out — a spent budget, a closed host.
    """
    probe = probe_url(url, want_body=want_body, timeout=timeout, **sudact_transport(work_dir))
    sudact_challenge_met(work_dir, probe)
    return probe


def sudact_fetch(
    work_dir: str | os.PathLike,
    url: str,
    *,
    hosts: frozenset,
    timeout: float,
    cookies: http.cookiejar.CookieJar | None = None,
    headers: dict | None = None,
    accept: str | None = None,
) -> tuple[dict, bytes]:
    """One request of the sudact channel: its slot, then the request itself (D-202).

    The address is checked first — a refused address is no request and costs the budget nothing.
    Then the slot is reserved under the lock, waited for outside it and the state reread
    (`channel_slot`), and the request goes out through the ordinary client: the allowlist on the
    address and on every hop, the honest liveness agent, the hop to `/defence/` refused before it is
    requested, and every other hop that **is** followed paying its own slot first (`channel_hop`).
    """
    refused = fetch_refusal(url, hosts)
    if refused is not None:
        raise ChannelUnavailable(refused[0])
    channel_slot(work_dir, SUDACT_CHANNEL)
    answer = fetch_body(
        url,
        accept=accept,
        timeout=timeout,
        allowed=hosts,
        extra_headers=headers,
        cookies=cookies,
        stop=sudact_defence,
        hop=functools.partial(channel_hop, work_dir, SUDACT_CHANNEL),
    )
    return answer, answer.pop("payload")


def sudact_refusal(work_dir: str | os.PathLike, answer: dict, payload: bytes, *, markup: bool) -> None:
    """Stop the channel when this answer is not what the endpoint serves (D-202).

    Two kinds of failure, told apart because they mean different things for the rest of the run:

    - **A challenge** — the portal answered, and not with what the endpoint serves: a redirect this
      client will not follow (to `/defence/`, off the allowlist, a loop), an anti-bot or throttle
      answer (`UNCHECKED_HTTP_CODES` — 202, 403, 429, 503 — and the WAF and Cloudflare challenges
      among them) and, for the HTML endpoints only, a body the interstitial rules recognise. The
      marker is written and `ChannelCaptcha` raised: the channel is closed for the whole run.
    - **A broken request** — the portal did not answer: a timeout, a refused connection, `404`,
      `500`, a body cut at the ceiling. `ChannelUnavailable` stops this call, because the next
      request after a failed one is the retry; the run's channel stays open.

    `markup=False` is the search, whose answer is JSON: no markup rule applies to it at all —
    `{"status": "new"}` is far under the 2 KB floor of a page — so `interstitial_suspected` decides
    nothing for it, and its body is judged by `sudact_search_answer` alone, in both directions.

    **The order is the rule (round 2, item 3).** First what the transport sees without reading the
    body — a redirect into `/defence/`, a challenge status, a refused hop. Then truncation: a body
    shorter than its `Content-Length` proves nothing about a challenge (its first kilobyte is under
    every page's 2 KB floor), so it is `truncated`, without a marker. Only then the body rules.
    """
    error = answer["error"]
    if (
        error == SUDACT_DEFENCE_REDIRECT
        or answer["code"] in UNCHECKED_HTTP_CODES
        or str(error or "").startswith(("redirect_not_allowed", "too_many_redirects"))
    ):
        raise channel_challenge(work_dir, error or f"http_{answer['code']}")
    if answer["truncated"]:
        raise ChannelUnavailable("truncated")
    if error == "interstitial_suspected":
        if markup:
            raise channel_challenge(work_dir, challenge_name(payload))
    elif error is not None:
        raise ChannelUnavailable(error)


def sudact_search_answer(work_dir: str | os.PathLike, answer: dict, payload: bytes) -> dict:
    """The search's answer: JSON carrying a `status`, or a challenge — never «no results» (D-202).

    An HTML page, a body that does not parse, JSON without a `status`, a `finished` answer without
    its list in `content`: none of them is what `doc_ajax` serves, and reading one as an empty list
    would send the researcher to re-check a number when the channel was in fact blocked. Measured on
    2026-09-21: `{"status": "new", "search_status": "new"}`, then `finished` with keys
    `search_status`, `search_task_id`, `content`, `total_found` and `status`.
    """
    sudact_refusal(work_dir, answer, payload, markup=False)
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        document = None
    if not isinstance(document, dict) or not isinstance(document.get("status"), str):
        raise channel_challenge(work_dir, "not_json")
    if document["status"] == SUDACT_FINISHED and not isinstance(document.get("content"), str):
        raise channel_challenge(work_dir, "no_content")
    return document


def sudact_candidates(document: dict, section: str, search_url: str) -> list[str]:
    """The document addresses a finished search lists — unique, in page order, capped (D-202).

    The list is the HTML in `content`, and nothing else in the answer is read. In particular
    `total_found` is **never parsed as a count**: measured, it is an HTML fragment («Найдено 4
    документа»), not a number — the links in `content` are the only list there is. Only
    `/<section>/doc/<id>/` of the section asked is a document: `/doc/save/<id>/`, `/doc/print/…` and
    `/doc/send/…` are the portal's own functions (the measured `…/doc/save/455AVcwC6HrR/` leads to
    another document entirely), and a link elsewhere is not this search's result. The listing's
    titles and dates are never read — sudact's metadata puts an act a day early.
    """
    pattern = re.compile(r"/" + re.escape(section) + r"/doc/([A-Za-z0-9]+)/(?=[?#\"'\s<>]|$)")
    seen: dict[str, None] = {}
    for match in pattern.finditer(str(document.get("content") or "")):
        seen.setdefault(urllib.parse.urljoin(search_url, f"/{section}/doc/{match.group(1)}/"), None)
    return list(seen)[: limits.RESOLVE_MAX_CANDIDATES]


def resolve_sudact(
    number: str, *, section: str, base: str, timeout: float, work_dir: str | os.PathLike
) -> list[str]:
    """Addresses of the documents the portal's ordinary search lists for `number` (D-202).

    A plain GET of the section page opens the session, and the search —
    `/<section>/doc_ajax/?<section>-case_doc=<number>&page=1` with `X-Requested-With` and a
    `Referer`, and with whatever cookie the section page set — answers `{"status": "new"}` and,
    asked again after a pause, `finished` with the list. **The cookie is carried, never required:**
    measured on 2026-09-21 the section page set none and the search worked, so a missing cookie is
    never a refusal. At most `SUDACT_POLL_MAX` polls after the first answer; then
    `ChannelUnavailable("not_resolved")`, and the fallbacks take over.

    Every request is a request of the channel (`sudact_fetch`): paced and counted in
    `channels.json`, and the first challenge on **any** of them — the section page, the search,
    any poll — writes the marker and stops the channel at once (`ChannelCaptcha`). An empty list
    means the search finished and named no document. `work_dir` is the one argument beyond the
    plan's signature: the pace, the budget and the marker live in its `channels.json`.
    """
    hosts = allowlist_hosts()
    page = f"{base.rstrip('/')}/{section}/"
    search = sudact_search_url(number, section, base)
    session = http.cookiejar.CookieJar()
    answer, payload = sudact_fetch(work_dir, page, hosts=hosts, timeout=timeout, cookies=session)
    sudact_refusal(work_dir, answer, payload, markup=True)
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": page}
    for _ in range(1 + limits.SUDACT_POLL_MAX):
        answer, payload = sudact_fetch(
            work_dir,
            search,
            hosts=hosts,
            timeout=timeout,
            cookies=session,
            headers=headers,
            accept=SUDACT_SEARCH_ACCEPT,
        )
        document = sudact_search_answer(work_dir, answer, payload)
        if document["status"] == SUDACT_FINISHED:
            return sudact_candidates(document, section, search)
    raise ChannelUnavailable(SUDACT_NOT_RESOLVED)


def sudact_document_text(work_dir: str | os.PathLike, url: str, *, hosts: frozenset, timeout: float) -> str:
    """The text of one listed document, as the save will store it — or the channel stops (D-202).

    The page is a request of the channel like any other (`sudact_fetch`) and is judged as HTML: a
    challenge the interstitial rules recognise closes the channel. It is then converted exactly as
    `run_save` converts it — `markup_to_text`, one `prepare_raw` — so the text certified here is the
    text the save will judge. `/<section>/doc/<id>/` serves court acts: a page whose text is no
    Russian judicial act at all (`source_text.is_russian_act`) is not what the endpoint serves — a
    challenge in the portal's own words, which the English `CHALLENGE_PHRASES` cannot read — and it
    closes the channel too. Nothing is ever skipped: the next fetch after a failed one is the retry.
    """
    answer, payload = sudact_fetch(work_dir, url, hosts=hosts, timeout=timeout)
    sudact_refusal(work_dir, answer, payload, markup=True)
    converted = markup_to_text(payload, answer["content_type"])
    text = prepare_raw(converted if converted is not None else payload).decode("utf-8-sig", errors="replace")
    if not source_text.is_russian_act(text):
        raise channel_challenge(work_dir, "not_a_document")
    return text


def resolve_channel_refusal(exc: ChannelUnavailable) -> dict:
    """The answer of a channel that did not work — with no candidate address in it (D-202).

    The addresses sit behind the same blocked channel, and pointing the researcher at them would be
    the retry by other hands.
    """
    if isinstance(exc, ChannelBudgetSpent):
        return {"errors": ["channel_budget_spent"], "hint": RESOLVE_HINTS["channel_budget_spent"], "candidates": []}
    answer = {"errors": [f"channel_unavailable: {exc}"], "hint": RESOLVE_HINTS["channel_unavailable"], "candidates": []}
    if isinstance(exc, ChannelCaptcha):
        answer.update(hint=RESOLVE_HINTS["captcha"], challenge=exc.seen)
    elif isinstance(exc, ChannelClock):
        answer["hint"] = RESOLVE_HINTS["clock"]
    return answer


def run_save_resolved(args: argparse.Namespace) -> dict:
    """`mf sources save --resolve vsrf|sudact` — from the requisites of an act to its address (D-202).

    `vsrf`: acts of the **judicial chambers** of the Supreme Court. A Plenum or Presidium document
    is saved with `--url` from its published page: the resolver refuses nothing for them, it is
    simply not the way to them.

    `sudact`: acts of the arbitration and general-jurisdiction courts on sudact.ru, through the
    portal's ordinary search (`resolve_sudact`, the section chosen by `sudact_section`). Each listed
    document is certified exactly as a vsrf candidate is; every request of the channel — the save's
    own fetch of the chosen act included — is paced and counted in `channels.json`, and the first
    challenge on any of them closes the channel for the run. No captcha is ever solved, worked
    around or retried: the fallbacks (LDH `RU/Sudact`, the agent's web search, then `--url`) need
    no code.

    The resolver writes nothing and saves nothing. It chooses one address among the chain the
    portal returns for the case number and hands it to the transaction of D-199 and D-201
    unchanged — same lock discipline, same admission rules, same verdict, same
    `retrieval_tool = "mf-save <host>"`, same refusal codes.

    **The chosen document is therefore fetched twice, and that is deliberate — do not optimise it
    away.** Threading the certified bytes into the save would mean a second entry into the save
    path, which is the one thing this task refuses to build, and it would record a digest of bytes
    the transaction never saw. As it stands the save certifies what *it* fetched, with the same
    `--expect-number` and `--expect-date`, and it is called with `resolved=True`: a second body
    whose own verdict did not find **both** requisites is refused as `requisites_mismatch` and
    nothing is stored. The verdict alone was not enough — it refuses only a text it reads as a
    Russian act, and anything else (a wall the English phrases cannot read, an HTML page served in
    place of a PDF, a scan) fell under the non-Russian rule and was stored as an excerpt (Task 7,
    finding 1). One extra request and one extra politeness pause is the price, and it is the right
    one.

    Both requisites are mandatory here, and missing one is refused before the first request: the
    measured chain holds the judge's referral order of 11.07.2024 and the chamber's ruling of
    14.08.2024 under the same number *and* the same bracketed suffix, so the number alone cannot
    identify the act and the date alone cannot either.
    """
    resolver = str(getattr(args, "resolve", "") or "").strip()
    number = str(getattr(args, "expect_number", "") or "").strip()
    date = str(getattr(args, "expect_date", "") or "").strip()
    missing = [flag for flag, value in (("--expect-number", number), ("--expect-date", date)) if not value]
    if missing:
        return {
            "errors": [f"requisites_required: {', '.join(missing)}"],
            "hint": RESOLVE_HINTS["requisites_required"],
        }
    work_dir = Path(args.workdir)
    with sources_lock(work_dir):
        frozen = is_frozen(work_dir)
    if frozen:
        # A cheap pre-check, not the guard: nine requests to a live portal answered by
        # `sources_frozen` is work nobody needed and a portal nobody should have troubled. The
        # authoritative refusal is still the one `run_save` makes under the lock at step 1 and
        # again at step 3 — this answer may be stale by the time the network comes back, and a
        # freeze that lands meanwhile is caught there, not here.
        return {"errors": ["sources_frozen"], "hint": SOURCES_FROZEN_HINT}
    timeout = float(getattr(args, "timeout", None) or limits.LIVENESS_TIMEOUT_SECONDS)
    try:
        if resolver == SUDACT_CHANNEL:
            candidates = resolve_sudact(
                number, section=sudact_section(number), base=SUDACT_BASE, timeout=timeout, work_dir=work_dir
            )
            text_of = functools.partial(sudact_document_text, work_dir, hosts=allowlist_hosts(), timeout=timeout)
        else:
            candidates = resolve_vsrf(number, base=VSRF_BASE, timeout=timeout)
            text_of = None
        # An index that arrived and named no act is not a broken channel: it is a number that lists
        # nothing, and it falls through to `requisites_mismatch` with no candidate and nothing found.
        answer = certify_candidates(candidates, number=number, date=date, timeout=timeout, text_of=text_of)
    except ChannelUnavailable as exc:
        # No candidate is handed out either: the addresses sit behind the same blocked channel, and
        # pointing the researcher at them would be the retry by other hands.
        return resolve_channel_refusal(exc)
    distinct = answer["distinct"]
    if len(distinct) > 1:
        # Two documents that are not the same document carry the same requisites: the code cannot
        # choose between them, and choosing the first would be a guess dressed as a certification.
        return {
            "errors": [f"requisites_ambiguous: {len(distinct)} distinct acts"],
            "hint": RESOLVE_HINTS["requisites_ambiguous"],
            "candidates": distinct,
            "found": answer["found"],
        }
    if not distinct:
        # Each requisite may have been seen — just never in the same act. The detail is never empty.
        absent = ", ".join(name for name in ("number", "date") if not answer["found"][name])
        return {
            "errors": [f"requisites_mismatch: {absent or 'no candidate carries both'}"],
            "hint": RESOLVE_HINTS["requisites_mismatch"],
            "candidates": candidates,
            "found": answer["found"],
        }
    chosen = argparse.Namespace(**{**vars(args), "url": distinct[0], "resolve": None})
    # The save fetches the chosen act once more (see above), and on the sudact host that fetch is a
    # request of the channel like any other: `run_save` takes its slot at dispatch — as it does for
    # every save to the host since the final review (C) — and a challenge in its answer closes the
    # host for the run.
    return run_save(chosen, resolved=True)


def run_save(args: argparse.Namespace, *, resolved: bool = False) -> dict:
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

    D-202 (Task 7). `resolved` is internal — never a CLI flag: `run_save_resolved` passes it for the
    address a resolver has already certified, and then the save refuses as `requisites_mismatch`
    unless its **own** verdict found both requisites; a plain `--url` keeps the rules above. An
    address on the sudact host (`sudact_address`) is closed once the captcha marker is set — the
    save refuses before any request — its redirect into `/defence/` is refused before it is followed
    and closes the host, and so does any other challenge the save meets there
    (`SUDACT_SAVE_CHALLENGES` whatever the body's length, `SUDACT_SAVE_BODY_CHALLENGES` only on a whole
    body): the wall is the host's, not the search endpoint's. Final review C: its request — resolved
    or plain — and every hop it follows are requests of the channel (`sudact_transport`): a slot
    reserved, waited for and counted against the run's one budget.
    """
    resolver = str(getattr(args, "resolve", "") or "").strip()
    if resolver in SAVE_RESOLVERS:
        # D-202: the resolver only chooses an address, and comes back here with it.
        return run_save_resolved(args)
    if resolver:
        # The parser admits only `SAVE_RESOLVERS`; this answers a caller that built its own namespace.
        return {
            "errors": [f"resolver_not_available: {resolver}"],
            "hint": "pass --url, or --resolve " + "|".join(SAVE_RESOLVERS),
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
    public_address = str(getattr(args, "public_url", "") or "").strip()
    address_refusal = public_url_refusal(method, public_address)
    if address_refusal is not None:
        return address_refusal

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
    # Final review A: a save that is not a GET is given that address by `--public-url` — the
    # endpoint names no document — and keeps the endpoint in `meta.save_endpoint`, scrubbed like any
    # string the memo may print.
    clean_url, retrieved_from = public_url(public_address or url)
    endpoint = scrub_urls(url) if method != "GET" else ""
    explicit_id = slugify(args.id) if getattr(args, "id", None) else None
    identity = {"layer": layer, "source_id": explicit_id, "url": clean_url, "citation": citation}
    on_sudact = sudact_address(url)

    def refuse(reason: str, answer: dict) -> dict:
        """Every refusal of this save goes through here — use it for any refusal added later.

        D-202, Task 7: **on the resolved path no refusal writes anything to the registry**, whatever
        its code. There, every refusal of this fetch is the same event: a document the resolver had
        already certified came back as something else on the second fetch — a wall, a throttle, a
        captcha, a cut body, a changed page. An existing record of the same identity may hold a good
        `full_text`, and none of these may overwrite its history; the answer still carries the
        outcome. One rule in one place, never a list of codes: a list would miss the next refusal
        someone adds. On a plain `--url` the refusal IS the first attempt, and Task 4's exception
        (`save_refused` stamping `meta.save_outcome`) stands untouched.
        """
        if resolved:
            return {**answer, "save_outcome": f"refused:{reason}"}
        return save_refused(work_dir, identity, reason, answer)

    # Step 1 — under the lock, before the network: nothing here writes.
    with sources_lock(work_dir):
        if is_frozen(work_dir):
            return {"errors": ["sources_frozen"], "hint": SOURCES_FROZEN_HINT}
        if on_sudact and read_channel_state(work_dir)["channels"].get(SUDACT_CHANNEL, {}).get("captcha"):
            # D-202: the host challenged this run already. Walking into the same wall with a plain
            # `--url` would be the retry by other hands, so nothing is asked of it.
            return {
                "errors": [f"channel_unavailable: {CHANNEL_CAPTCHA}"],
                "hint": RESOLVE_HINTS["captcha"],
                "challenge": "marker",
            }
        refusal = save_guard(read_registry(work_dir)["sources"], **identity)
    if refusal is not None:
        return refusal

    # Step 2 — without the lock: the network, the conversion, the certification.
    hosts = allowlist_hosts()
    refused = fetch_refusal(url, hosts)
    if refused is not None:
        return refuse(refused[0], refused[1])
    host = request_host(url)
    # D-202, round 2: a request to the sudact host rereads the marker after the politeness pause and
    # immediately before it goes out (another process may have closed the host while this one
    # waited), and every hop it would follow costs what a request costs. Final review C: that
    # request is a request of the channel for a plain `--url` as much as for the resolved save — the
    # prescribed fallback after `channel_budget_spent` used to reach the host unpaced and uncounted —
    # so both take their slot at dispatch (`sudact_transport`: reserve, wait, reread).
    channel = sudact_transport(work_dir) if on_sudact else {}
    try:
        answer = fetch_allowed(
            url, hosts, accept=args.accept, lang=args.lang, timeout=args.timeout, method=method, body=body, **channel
        )
    except ChannelUnavailable as exc:
        # Refused before a request went out (or before a hop was followed): nothing to record.
        return {key: value for key, value in resolve_channel_refusal(exc).items() if key != "candidates"}
    payload = answer.pop("payload")
    transport = {"url": clean_url, "host": host, "status": answer["status"], "code": answer["code"]}
    if answer["error"] == SUDACT_DEFENCE_REDIRECT:
        # D-202: the hop into the captcha was refused before it was followed — the captcha page is
        # never requested — and the host is closed for the run.
        channel_mark_captcha(work_dir, SUDACT_CHANNEL)
        errors = [f"channel_unavailable: {CHANNEL_CAPTCHA}"]
        closed = {"errors": errors, "hint": RESOLVE_HINTS["captcha"], "challenge": SUDACT_DEFENCE_REDIRECT}
        return refuse(CHANNEL_CAPTCHA, {**closed, **transport})
    admission = save_admission(answer, payload)
    if admission is not None:
        reason, detail = admission
        named_without_the_body = reason in SUDACT_SAVE_CHALLENGES
        judged_on_a_whole_body = reason in SUDACT_SAVE_BODY_CHALLENGES and not answer["truncated"]
        if on_sudact and (named_without_the_body or judged_on_a_whole_body):
            # D-202: the wall is the host's, whoever meets it first — the resolver or a plain `--url`.
            # A status or a header names a challenge whatever the body's length; only what is judged
            # on the body is set aside when the body was cut short (task 7, round 3) — the order
            # `sudact_refusal` uses.
            channel_mark_captcha(work_dir, SUDACT_CHANNEL)
        errors = [f"{reason}: {detail}" if detail else reason]
        return refuse(reason, {"errors": errors, "hint": SAVE_HINTS[reason], **transport})

    # D-201: a PDF is kept as the bytes the server served — it never goes through `prepare_raw`,
    # which decodes as `utf-8-sig` with `errors="replace"` and would corrupt the file it is meant
    # to preserve. Its text layer is read once, here, and is then text like any other.
    original = payload if is_pdf(payload) else None
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
    if resolved and not (call["found"].get("number") and call["found"].get("date")):
        # D-202, Task 7 finding 1: the resolver certified this address — both requisites in the
        # text it fetched — so a second body without both is not that document, whatever else it
        # is. The verdict alone refuses only a text it reads as a Russian act; a wall the English
        # phrases cannot read, a page served in place of a PDF, or a scan would otherwise be stored
        # as an excerpt. A plain `--url` never comes here.
        missing = ", ".join(name for name in ("number", "date") if not call["found"].get(name))
        refusal = {
            "errors": [f"requisites_mismatch: {missing}"],
            "hint": RESOLVE_HINTS["resolved_save_mismatch"],
            "found": call["found"],
            **transport,
        }
        if on_sudact and not call["found"].get("russian"):
            # What `sudact_document_text` calls `not_a_document` on the resolver's own fetch: a page
            # of `/<section>/doc/<id>/` that is no court act at all — a challenge in the portal's words.
            channel_mark_captcha(work_dir, SUDACT_CHANNEL)
            refusal["challenge"] = "not_a_document"
        # `refuse` writes nothing on the resolved path, and this is the resolved path.
        return refuse("requisites_mismatch", refusal)
    if call["error"] is not None:
        # D-203: a Russian judicial act missing a requisite in its own zone is not this document.
        missing = ", ".join(name for name in ("number", "date") if not call["found"].get(name))
        errors = [f"{call['error']}: {missing}" if missing else call["error"]]
        return refuse(call["error"], {"errors": errors, "hint": SAVE_HINTS[call["error"]], **transport})
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
                    # D-205 fix round 1: `save_method` is code's to write, never the agent's `--meta` —
                    # and so is every other key of `CODE_META_KEYS` (final review).
                    agent_meta = {name: value for name, value in meta.items() if name not in CODE_META_KEYS}
                    record["meta"] = {**(record.get("meta") or {}), **agent_meta}
            if clean_url and normalize_url(record.get("url")) == normalize_url(clean_url):
                # D-205 fix rounds 1-2: `save_method` says how the record's address must be reached,
                # which is bookkeeping of the address, not of the publication. So every accepted save
                # whose request reached that address writes it — a publication that moved the url to
                # it, a repair, and the idempotent answer that publishes nothing (a record a POST save
                # wrote before the key existed is marked on its next save). Set only for a method a
                # HEAD/GET probe cannot replay, removed for GET. A save that reached another address
                # and did not move the url leaves it alone: the mark describes the record's url, not
                # this request. Refusals return before this point and never write it.
                # Final review A: for such a save the record's url is its `--public-url`, and the
                # endpoint it reached travels with the method (`ADDRESS_META_KEYS`).
                held = {
                    name: value for name, value in (record.get("meta") or {}).items() if name not in ADDRESS_META_KEYS
                }
                if method != "GET":
                    held[SAVE_METHOD_KEY] = method
                    held[SAVE_ENDPOINT_KEY] = endpoint
                record["meta"] = held
            held_meta = dict(record.get("meta") or {})
            if publish_text:
                # Final review E: the outcome of the text the record holds from now on — written only
                # when a text is published, so another excerpt that publishes nothing leaves it alone.
                held_meta[TEXT_OUTCOME_KEY] = outcome
            elif replacing:
                # This answer replaced the record and published no text (a PDF with no text layer):
                # the record holds none, so no outcome describes one.
                held_meta.pop(TEXT_OUTCOME_KEY, None)
            record["meta"] = {**held_meta, "save_outcome": outcome}
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
    save.add_argument(
        "--public-url",
        dest="public_url",
        default=None,
        help="required with --method POST: the public page of the document, stored as the source's url",
    )
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
