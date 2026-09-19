"""`mf sources register|fetch|pack|digest|liveness|verify|slice` — registry and freeze (ТЗ §5.3, M5/M6)."""

from __future__ import annotations

import argparse
import codecs
import gzip
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

from . import events, i18n, limits, schema, state_io, stepctx

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

BODY_COMPARABLE_TOOLS: tuple[str, ...] = ("curl", "wget", "mf-fetch")
"""D34-08: the only tools whose `raw_path` holds the bytes `url` served.

`mf-fetch` joined them with D-149: `mf sources fetch` writes the response body byte for byte, so
liveness may hash the page again and compare.

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


# --- helpers --------------------------------------------------------------


def slugify(text: object, fallback: str = "source") -> str:
    """Stable kebab-case source slug matching the `source_id` pattern of the schema."""
    value = unicodedata.normalize("NFKD", str(text or ""))
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


def _new_record(layer: str, title: str, citation: str, url: str, tool: str, tier: str) -> dict:
    return {
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


def store_raw(work_dir: str | os.PathLike, layer: str, source_id: str, raw_file: str | os.PathLike) -> dict:
    """Move the agent's temporary raw file to `research/raw/<layer>/<slug>.md` and hash it (§4.3)."""
    src = Path(raw_file)
    if not src.is_file():
        raise ValueError(f"raw_file_not_found: {src}")
    payload = src.read_bytes()
    converted = markup_to_text(payload)
    payload = converted if converted is not None else payload
    target = Path(work_dir) / RAW_DIR / layer / f"{source_id}.md"
    state_io.write_bytes_atomic(target, payload)
    if src.absolute() != target.absolute():
        try:
            src.unlink()
        except OSError:  # a read-only source stays where it is; the copy is authoritative
            pass
    text = payload.decode("utf-8-sig", errors="replace")
    return {
        "raw_path": stepctx.rel_path(work_dir, target),
        "raw_sha256": state_io.sha256_bytes(payload),
        "raw_chars": len(text),
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

    work_dir = Path(work_dir)
    with sources_lock(work_dir):
        # The freeze holds `sources.lock` for its whole transaction, so this check cannot race it.
        if is_frozen(work_dir):
            return {
                "errors": ["sources_frozen"],
                "hint": "the source pack is frozen; registrations after the freeze never reach the memo",
            }
        registry = read_registry(work_dir)
        sources = registry["sources"]
        explicit_id = slugify(source_id) if source_id else None
        if explicit_id and explicit_id in sources:
            # D34-04: an occupied `source_id` is never re-slugged into `<id>-2` and never overwritten.
            # D-143: the check runs before the dedup key of rule 5 can route the call into the update
            # branch — a repeat registration that matches by url is exactly how other raw bytes used
            # to overwrite the record under the same explicit id and answer `idempotent: true`.
            held = sources[explicit_id]
            incoming_sha = None
            if raw_file and Path(raw_file).is_file():
                # D-163: `store_raw` records the converted text, so the repeat check hashes the
                # same normalised bytes — identical HTML re-registered under its id is a no-op.
                incoming = Path(raw_file).read_bytes()
                converted = markup_to_text(incoming)
                incoming_sha = state_io.sha256_bytes(converted if converted is not None else incoming)
            if (held.get("raw_sha256") or None) != incoming_sha:
                return {
                    "errors": [f"source_id_collision: {explicit_id} already holds {held['title']!r}"],
                    "source_id": explicit_id,
                    "held_title": held["title"],
                    "held_raw_sha256": held.get("raw_sha256"),
                    "hint": "register the new text under its own id, or fix the existing record",
                }
            # Same bytes under the same id: answer with the id that already holds them.
            return {
                "source_id": explicit_id,
                "created": False,
                "idempotent": True,
                "layer": held["layer"],
                "raw_path": held.get("raw_path"),
                "raw_sha256": held.get("raw_sha256"),
                "raw_chars": held.get("raw_chars"),
                "provenance": held.get("provenance"),
                "tier": held.get("tier"),
            }

        key = dedup_key(layer, url, citation)
        existing_id = None
        for candidate_id, record in sources.items():
            if dedup_key(record["layer"], record.get("url"), record.get("citation_form")) == key:
                existing_id = candidate_id
                break

        if existing_id is None:
            new_id = explicit_id or unique_slug(slugify(title), set(sources))
            record = _new_record(layer, title, citation, str(url), tool, tier)
            sources[new_id] = record
            created = True
            existing_id = new_id
        else:
            record = sources[existing_id]
            record["title"] = title
            record["citation_form"] = citation
            record["tier"] = tier
            record["retrieval_tool"] = tool
            if str(url).strip():
                record["url"] = str(url)
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
            record.update(store_raw(work_dir, layer, existing_id, raw_file))
            record["provenance"] = "agent_saved"

        write_registry(work_dir, registry)

    result = dict(sources[existing_id])
    return {
        "source_id": existing_id,
        "created": created,
        "idempotent": not created,
        "layer": layer,
        "raw_path": result.get("raw_path"),
        "raw_sha256": result.get("raw_sha256"),
        "raw_chars": result.get("raw_chars"),
        "provenance": result.get("provenance"),
        "tier": result.get("tier"),
    }


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


def build_pack(work_dir: str | os.PathLike, registry: dict, *, state: dict | None = None) -> tuple[dict, dict]:
    """Build `source-pack.json` and the registry updates (currency + pack) it implies (§5.3)."""
    sources = registry["sources"]
    currency = load_currency(work_dir, state=state)
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
        if record.get("identifiers"):
            entry["identifiers"] = dict(record["identifiers"])
        if record.get("retrieved_at"):
            entry["retrieved_at"] = record["retrieved_at"]
        entries.append(entry)

    snapshot = []
    for source_id in kept:
        record = sources[source_id]
        raw_path = record.get("raw_path")
        digest = None
        if raw_path:
            path = Path(work_dir) / raw_path
            if path.is_file():
                digest = state_io.sha256_file(path)
                record["raw_sha256"] = digest
                record["raw_chars"] = len(path.read_text(encoding="utf-8-sig", errors="replace"))
        snapshot.append({"source_id": source_id, "raw_sha256": digest})

    pack_document = {
        "schema_version": 2,
        "frozen_at": events.utc_now(),
        "snapshot": snapshot,
        "entries": entries,
    }
    if merged:
        pack_document["merged_into"] = dict(sorted(merged.items()))
    return pack_document, registry


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
                pack_document, registry = build_pack(work_dir, registry, state=state)
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
    return {
        "source_pack_path": PACK_PATH,
        "frozen_at": pack_document.get("frozen_at"),
        "sources": len(pack_document.get("snapshot") or []),
        "entries": len(pack_document.get("entries") or []),
        "with_raw": sum(1 for row in pack_document.get("snapshot") or [] if row.get("raw_sha256")),
        "merged": len(pack_document.get("merged_into") or {}),
        "sources_frozen": True,
        "replayed": replayed,
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


def collect_exceptions(work_dir: str | os.PathLike, state: dict) -> list[dict]:
    """Gate-11 exceptions of §2.4: critical sources in doubt, warnings, exhausted MCP budget."""
    registry = read_registry(work_dir)
    pack = read_pack(work_dir) or {}
    pack_by_id = {row["source_id"]: row for row in pack.get("entries") or [] if isinstance(row, dict)}
    merged = pack.get("merged_into") or {}
    exceptions: list[dict] = []

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
    exceptions = collect_exceptions(work_dir, state)
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
            expected = record.get("raw_sha256") if body_comparable(record) else None
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
            if expected and probe["sha256"] and probe["sha256"] != expected:
                status = "changed"
            record["liveness"] = {
                "status": status,
                "code": probe["code"],
                "checked_at": events.utc_now(),
            }
            if expected and probe["sha256"] == expected:
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
) -> dict:
    """Classify one answer the way liveness does, and say whether the body may be cited (D-149)."""
    cap = limits.LIVENESS_MAX_BODY_BYTES
    truncated = len(payload) > cap
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
            payload = _inflate(payload, response.headers)
            return _fetch_answer(url, code, response.headers, payload, response.geturl(), hops)
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
    problem = url_error(url)
    if problem is not None:
        # D-151: one parser, the permission gate's — a url it cannot read is never requested.
        return {"errors": [f"{problem}: {url}"], "hint": "only http(s) urls without credentials can be fetched"}
    host = request_host(url)
    hosts = allowlist_hosts()
    if not host_on_allowlist(host, hosts):
        return {
            "errors": [f"host_not_allowed: {host}"],
            "hint": "hooks/allowlist.txt is the same list the fetch permission gate enforces (§8.1)",
        }
    method = str(getattr(args, "method", "GET") or "GET").strip().upper()
    if method not in FETCH_METHODS:
        return {"errors": [f"unsupported_method: {method}"], "hint": f"--method is one of {', '.join(FETCH_METHODS)}"}
    raw_body = getattr(args, "json_body", None)
    body: bytes | None = b"" if method == "POST" else None
    if raw_body not in (None, ""):
        if method != "POST":
            return {"errors": ["json_body_requires_post"], "hint": "--json is the body of a --method POST"}
        try:
            body = json.dumps(json.loads(raw_body), ensure_ascii=False).encode("utf-8")
        except ValueError as exc:
            return {"errors": [f"invalid_json_body: {exc}"], "hint": "--json takes one JSON document"}

    work_dir = Path(args.workdir)
    try:
        # `--out` is checked before the request, so a bad path costs the host nothing.
        target = fetch_target(work_dir, url, out=args.out) if args.out else None
    except ValueError as exc:
        return {"errors": [str(exc)], "hint": "--out is a relative path under research/raw/"}

    since = _LAST_FETCH.get(host)
    if since is not None:
        _wait(host_delay(host) - (time.monotonic() - since))
    answer = fetch_body(
        url,
        accept=args.accept,
        lang=args.lang,
        timeout=args.timeout,
        method=method,
        body=body,
        allowed=hosts,
    )
    _LAST_FETCH[host] = time.monotonic()

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
    """The id a duplicate group collapses into: not mislabelled, carrying raw text, registered first."""
    return sorted(
        group,
        key=lambda source_id: (
            is_mislabelled(source_id, sources[source_id]),
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
