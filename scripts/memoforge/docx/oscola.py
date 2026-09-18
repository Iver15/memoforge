"""One deterministic OSCOLA (5th ed.) citation builder for both renderers (ТЗ §5.5, D-150).

`compact(view, pinpoint)` is what a citation looks like the first time a memorandum names a source,
`short(view, pinpoint, first_n)` every later time. Both are built per **source class** — EU
legislation, national legislation, CJEU case, other court, soft law/guidance, doctrine — detected
from `identifiers`/`meta`/`citation_form`, and neither ever carries a URL, a CELEX/ELI identifier or
a retrieval date: those belong to the `## Sources` annex (`sources_entry`), which carries the full
record of every cited source. The fallback when the fields are missing is the registry's own
`citation_form` with the URL, the CELEX tag and the `retrieved …` tail stripped off — never the
concatenation of title and citation form the run of 2026-09-10 produced.

The same module decides the **citation style** of a run (`resolve_style`): `inline` — parenthetical
citations hyperlinked in the body, the default of both templates — or `footnotes`, kept available
through the template front-matter and `config.citation_style`. `assign_forms` numbers the mentions
and picks full / short / `ibid` / omitted for each, so `fallback.py` (markdown) and `renderer.py`
(docx) cannot disagree about a single citation.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import i18n, pylauncher

# --- citation style (D-150) ------------------------------------------------

STYLE_FOOTNOTES = "footnotes"
STYLE_INLINE = "inline"
CITATION_STYLES: tuple[str, ...] = (STYLE_FOOTNOTES, STYLE_INLINE)

DEFAULT_CITATION_STYLE = STYLE_INLINE
"""Both templates ship `citation_style: inline`; this is what a run without a template falls to."""

FRONT_MATTER_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
STYLE_LINE_RE = re.compile(r"^[ \t]*citation_style[ \t]*:[ \t]*([A-Za-z_]+)[ \t]*$", re.MULTILINE)

TEMPLATE_DIR = "templates"


def normalise_style(value: object) -> str | None:
    """A configured style name, or None when it is absent or not one of the two (D-150)."""
    text = str(value or "").strip().lower()
    return text if text in CITATION_STYLES else None


def template_style(template_id: object, template_path: object = None) -> str | None:
    """`citation_style:` of a template's YAML front matter, or None (D-150)."""
    if template_path:
        path = Path(str(template_path))
    elif template_id:
        path = pylauncher.plugin_root() / TEMPLATE_DIR / f"{template_id}.md"
    else:
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return None
    front = FRONT_MATTER_RE.match(text)
    if front is None:
        return None
    found = STYLE_LINE_RE.search(front.group("body"))
    return normalise_style(found.group(1)) if found else None


def resolve_style(state: dict | None) -> str:
    """`config.citation_style` > the template's front matter > `inline` (D-150)."""
    config = (state or {}).get("config") or {}
    if not isinstance(config, dict):
        return DEFAULT_CITATION_STYLE
    explicit = normalise_style(config.get("citation_style"))
    if explicit:
        return explicit
    return template_style(config.get("template_id"), config.get("template_path")) or DEFAULT_CITATION_STYLE


# --- source classes --------------------------------------------------------

CLASS_EU_LEGISLATION = "eu_legislation"
CLASS_LEGISLATION = "legislation"
CLASS_CJEU = "cjeu"
CLASS_CASE = "case"
CLASS_SOFT_LAW = "soft_law"
CLASS_DOCTRINE = "doctrine"

SOURCE_CLASSES: tuple[str, ...] = (
    CLASS_EU_LEGISLATION,
    CLASS_LEGISLATION,
    CLASS_CJEU,
    CLASS_CASE,
    CLASS_SOFT_LAW,
    CLASS_DOCTRINE,
)

MAX_COMPACT_CHARS = 120
"""A citation in the body never runs longer than this; the annex carries the full record (D-150)."""

CELEX_RE = re.compile(r"\b(3\d{4}[A-Z]{1,2}\d{4})\b")
CELEX_ANY_RE = re.compile(r"\b([1-9]\d{4}[A-Z]{1,2}\d{4})\b")
CELEX_CASE_RE = re.compile(r"\b6(\d{4})[CTF][A-Z](\d{4})\b")
ELI_RE = re.compile(r"/eli/(reg|dir|dec)(?:_impl|_del)?/(\d{4})/(\d+)", re.IGNORECASE)
"""D-152: identifiers are parsed by type before any text fallback, so `celex=32016R0679` and
`eli/reg/2016/679` name one act and `celex=62018CJ0311` is a decision, not a regulation."""

CELEX_COURT: dict[str, str] = {"C": "C", "T": "T", "F": "F"}
"""First letter of the CELEX document type of sector 6 — the court of the case number."""

ELI_ACT_KIND: dict[str, str] = {"reg": "Regulation", "dir": "Directive", "dec": "Decision"}
CELEX_TAG_RE = re.compile(r"[\s,;(]*\bCELEX[:\s]+3?[0-9A-Z]{8,12}\)?", re.IGNORECASE)
EU_ACT_RE = re.compile(
    r"\b((?:Commission\s+|Council\s+|European\s+Parliament\s+and\s+Council\s+)?"
    r"(?:Implementing\s+|Delegated\s+)?(?:Regulation|Directive|Decision))"
    r"\s+\((EU|EC|EEC|Euratom)\)\s*(?:No\s+)?(\d{4}/\d+)"
)
EU_ACT_OLD_RE = re.compile(
    r"\b(Regulation|Directive|Decision)\s+(?:No\s+)?(\d+/\d+)/(EC|EEC|EU|Euratom)\b"
)
CASE_NUMBER_RE = re.compile(r"\bCase\s+([CTF]-\d+/\d+(?:\s*P)?)")
ECLI_SHORT_RE = re.compile(r"\b(EU:[CTF]:\d{4}:\d+)\b")
ECLI_RE = re.compile(r"^ECLI:(?P<country>[A-Z]{2}):(?P<court>[A-Z]+):(?P<year>[0-9]{4}):")
NEUTRAL_RE = re.compile(r"\[(?P<year>[0-9]{4})\]\s*(?P<court>[A-Za-z]+(?:\s+\([A-Za-z]+\))?)")
DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})")
YEAR_RE = re.compile(r"\b(1[5-9][0-9]{2}|2[0-9]{3})\b")
NICKNAME_RE = re.compile(r"\(([^()]{2,40})\)\s*$")
PARENTHETICAL_RE = re.compile(r"\(([^()]{2,40})\)")
PARTY_RE = re.compile(r"\s+v\.?\s+", re.IGNORECASE)
USC_RE = re.compile(r"\b\d+\s*U\.?\s?S\.?\s?C\.?\s*(?:§+\s*)?\d+")
ACT_RE = re.compile(r"\b(?:Act|Gesetz|Code|Statute)\s+(?:of\s+)?(?:1[5-9]|20)[0-9]{2}\b")
"""`Data Protection Act 2018`: a named statute, as opposed to prose that happens to say «law»."""
QUOTED_TITLE_RE = re.compile(r"['‘“]([^'’”]{4,})['’”]")
JOURNAL_RE = re.compile(r"\((\d{4})\)\s*(\d+)(?:\((\d+)\))?\s+([A-Z][A-Za-z&'\- ]+?)\s+(\d+)")
URL_RE = re.compile(r"<?https?://\S+?>?(?=[\s,;]|$)")
RETRIEVED_RE = re.compile(r"[\s,;(]*\bretrieved\b[^,;)]*\)?", re.IGNORECASE)

CJEU_COURTS: dict[str, str] = {"C": "CJEU", "T": "General Court", "F": "Civil Service Tribunal"}
"""Court segment of an `ECLI:EU:<court>:…` identifier (OSCOLA 5th, EU materials)."""

CELEX_ACT_KIND: dict[str, str] = {"R": "Regulation", "L": "Directive", "D": "Decision"}

MAX_SHORT_WORDS = 5
"""Words kept when a short name has to be cut out of a title (§5.5 «<short name>»)."""

COMPANY_SUFFIXES: frozenset = frozenset(
    {"ltd", "ltd.", "inc", "inc.", "llc", "plc", "gmbh", "bvba", "bv", "nv", "sa", "spa", "sarl"}
)
"""Corporate forms an OSCOLA case name drops when the citation has to be compact (D-150)."""

DOC_TYPE_WORDS: frozenset = frozenset(
    {
        "guidelines",
        "recommendations",
        "opinion",
        "guidance",
        "report",
        "statement",
        "notice",
        "faq",
        "decision",
        "resolution",
        "working",
        "communication",
    }
)
"""A soft-law short name splits into `<body>, <document>` only in front of one of these."""

NOT_A_BODY: frozenset = frozenset({"AI", "EU", "US", "UK", "EC"})
"""Two-letter openers of a short name that are part of the document, not the issuing body."""

_META_BODY_KEYS = ("issuing_body", "publisher", "author", "authority", "institution")
_META_DATE_KEYS = ("date", "published_at", "publication_date", "effective_date")


def citation_word(key: str, language: str = i18n.DEFAULT) -> str:
    """One `memo.citation` word of the memo language (D-175a).

    Only the words of a citation that are a label live here. Its identity — the case name, the
    court, the act kind, `(n N)`, CELEX/ECLI/ELI and `[unresolved: …]` — is the same in every
    language, because it is how a reader finds the source, not how the memo describes it.
    """
    return i18n.t(i18n.normalize(language) or i18n.DEFAULT, f"memo.citation.{key}")


def view_of(source_id: str, entry: dict | None = None, record: dict | None = None) -> dict:
    """One citation view over the frozen snapshot entry and the registry record (§5.3, M6).

    The snapshot wins field by field — after the freeze it is the only thing the renderer may cite
    from — and the registry only fills what the snapshot projection does not carry (`meta`,
    `currency`).
    """
    entry = entry if isinstance(entry, dict) else {}
    record = record if isinstance(record, dict) else {}

    def pick(name: str, default: object = "") -> object:
        for source in (entry, record):
            value = source.get(name)
            if value not in (None, "", {}, []):
                return value
        return default

    identifiers = pick("identifiers", {})
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    if not meta:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    currency = record.get("currency") if isinstance(record.get("currency"), dict) else {}
    return {
        "source_id": source_id,
        "layer": pick("layer", None),
        "title": str(pick("title", "")),
        "citation_form": str(pick("citation_form", "")),
        "identifiers": identifiers if isinstance(identifiers, dict) else {},
        "url": str(pick("url", "")),
        "retrieved_at": pick("retrieved_at", None),
        "currency_status": str(entry.get("currency_status") or currency.get("status") or ""),
        "meta": meta,
    }


# --- derived fields --------------------------------------------------------


def _identifier(view: dict, *names: str) -> str:
    identifiers = view.get("identifiers") or {}
    for name in names:
        value = identifiers.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _meta_value(view: dict, keys: tuple[str, ...]) -> str:
    meta = view.get("meta") or {}
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _text_of(view: dict) -> str:
    """Citation form and title as one haystack for the class and instrument regexes."""
    return f"{view.get('citation_form') or ''} | {view.get('title') or ''}"


def celex_of(view: dict) -> str:
    """`identifiers.celex`, else a CELEX number written into the citation form (D-152)."""
    value = _identifier(view, "celex")
    if value:
        return value.strip().upper()
    found = CELEX_ANY_RE.search(_text_of(view))
    return found.group(1) if found else ""


def act_from_celex(celex: str) -> str:
    """`32016R0679` -> `Regulation (EU) 2016/679`; empty for anything but sector 3 (D-152)."""
    if len(celex) < 10 or celex[0] != "3":
        return ""
    kind = CELEX_ACT_KIND.get(celex[5])
    if not kind:
        return ""
    year = celex[1:5]
    number = celex[6:].lstrip("0") or "0"
    return f"{kind} ({'EU' if int(year) >= 2009 else 'EC'}) {year}/{number}"


def case_from_celex(celex: str) -> str:
    """`62018CJ0311` -> `C-311/18`; empty for anything but sector 6 (D-152)."""
    match = CELEX_CASE_RE.fullmatch(celex)
    if match is None:
        return ""
    court = CELEX_COURT.get(celex[5], "C")
    return f"{court}-{match.group(2).lstrip('0') or '0'}/{match.group(1)[2:]}"


def act_from_eli(view: dict) -> str:
    """`…/eli/reg/2016/679/oj` -> `Regulation (EU) 2016/679` — the CELEX designation (D-152)."""
    match = ELI_RE.search(_identifier(view, "eli") or _text_of(view))
    if match is None:
        return ""
    kind = ELI_ACT_KIND[match.group(1).lower()]
    year = match.group(2)
    return f"{kind} ({'EU' if int(year) >= 2009 else 'EC'}) {year}/{int(match.group(3))}"


def source_class(view: dict) -> str:
    """The OSCOLA form this source takes, from its identifiers, meta and citation form (D-150)."""
    text = _text_of(view)
    ecli = _identifier(view, "ecli")
    layer = str(view.get("layer") or "")
    celex = celex_of(view)

    # D-152: the identifiers decide before any text does — a sector-6 CELEX is a decision even when
    # the record spells no case number out, and a sector-3 one is legislation even without an ELI.
    if ecli.startswith("ECLI:EU:") or case_from_celex(celex) or CASE_NUMBER_RE.search(text):
        return CLASS_CJEU
    if act_from_celex(celex) or act_from_eli(view):
        return CLASS_EU_LEGISLATION
    if CELEX_RE.search(text) or EU_ACT_RE.search(text) or EU_ACT_OLD_RE.search(text):
        return CLASS_EU_LEGISLATION
    if ecli or _identifier(view, "neutral", "reporter_cite", "docket") or layer == "case_law":
        return CLASS_CASE
    if QUOTED_TITLE_RE.search(text) and YEAR_RE.search(text):
        # An author, a quoted title and a year: a journal article, whatever its `layer` says.
        return CLASS_DOCTRINE
    if layer == "statutes" or USC_RE.search(text) or ACT_RE.search(text):
        return CLASS_LEGISLATION
    return CLASS_SOFT_LAW


def court_of(view: dict) -> str:
    """Deciding court: `meta.court`, else the court segment of the ECLI or neutral citation."""
    explicit = _meta_value(view, ("court",))
    if explicit:
        return explicit
    match = ECLI_RE.match(_identifier(view, "ecli"))
    if match:
        if match.group("country") == "EU":
            return CJEU_COURTS.get(match.group("court"), match.group("court"))
        return match.group("court")
    match = NEUTRAL_RE.search(_identifier(view, "neutral", "reporter_cite"))
    return match.group("court").strip() if match else ""


def year_of(view: dict) -> str:
    """Year of the decision: `meta.year`, the ECLI year, the neutral citation year, `effective_date`."""
    explicit = _meta_value(view, ("year",))
    if explicit:
        return explicit
    match = ECLI_RE.match(_identifier(view, "ecli"))
    if match:
        return match.group("year")
    match = NEUTRAL_RE.search(_identifier(view, "neutral", "reporter_cite"))
    if match:
        return match.group("year")
    match = YEAR_RE.search(_meta_value(view, ("effective_date", "date")))
    return match.group(1) if match else ""


def date_only(value: object) -> str:
    """`YYYY-MM-DD` out of a timestamp, or the value unchanged when it is not one."""
    text = str(value or "").strip()
    match = DATE_RE.match(text)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else text


def long_date(value: object, language: str = i18n.DEFAULT) -> str:
    """`18 June 2021` out of an ISO date; anything else comes back as it was written."""
    text = str(value or "").strip()
    match = DATE_RE.match(text)
    if match is None:
        return text
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return text
    months = i18n.node(i18n.normalize(language) or i18n.DEFAULT, "memo.months")
    return f"{int(match.group(3))} {months[month - 1]} {match.group(1)}"


# --- pinpoints -------------------------------------------------------------

_PINPOINT_LABELS: tuple[tuple[str, str], ...] = (
    (r"art(?:icle)?s", "arts"),
    (r"art(?:icle)?", "art"),
    (r"para(?:graph)?s", "paras"),
    (r"para(?:graph)?", "para"),
    (r"annexe?s", "annexes"),
    (r"annexe?", "annex"),
    (r"recitals", "recitals"),
    (r"recital", "recital"),
    (r"sections|secs|ss", "ss"),
    (r"section|sec|s", "s"),
    (r"pages|pp", "pp"),
    (r"page|p", "p"),
)
"""Leading label of a pinpoint -> its OSCOLA abbreviation; longest alternative first."""

_PINPOINT_RE = re.compile(
    r"^(?P<label>" + "|".join(pattern for pattern, _ in _PINPOINT_LABELS) + r")\.?\s*(?=[0-9IVXL(])",
    re.IGNORECASE,
)
_SECTION_SIGN_RE = re.compile(r"^(§{1,2})\s*")


def normalise_pinpoint(value: object) -> str:
    """`Art. 6(1)(b)` -> `art 6(1)(b)`, `§26` -> `§ 26`, `s.2(1)` -> `s 2(1)` (D-150)."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip(",;")
    if not text:
        return ""
    sign = _SECTION_SIGN_RE.match(text)
    if sign:
        return f"{sign.group(1)} {text[sign.end():].strip()}".strip()
    match = _PINPOINT_RE.match(text)
    if match is None:
        return text
    label = match.group("label").lower()
    for pattern, replacement in _PINPOINT_LABELS:
        if re.fullmatch(pattern, label, re.IGNORECASE):
            return f"{replacement} {text[match.end():].strip()}".strip()
    return text  # pragma: no cover - the alternation and the table are the same list


_DISPLAY_LABELS: tuple[str, ...] = tuple(dict.fromkeys(name for _, name in _PINPOINT_LABELS))
_DISPLAY_LABEL_RE = re.compile(r"^(?P<label>" + "|".join(_DISPLAY_LABELS) + r")(?=\s|$)")
"""The normalised label at the head of a canonical pinpoint, plural before singular."""


def display_pinpoint(pinpoint: str, language: str = i18n.DEFAULT) -> str:
    """The canonical pinpoint with its label in the memo language (D-175a).

    The only place a pinpoint label is translated, and it runs on the string that is about to be
    printed — after the anchor of the link and the `ibid` decision were taken on the canonical
    form. A pinpoint the normaliser did not label (`§ 26`, `point 2 of the operative part`) is
    printed as the draft wrote it.
    """
    text = str(pinpoint or "")
    match = _DISPLAY_LABEL_RE.match(text)
    if match is None:
        return text
    return citation_word(match.group("label"), language) + text[match.end():]


# --- names -----------------------------------------------------------------


def _cut(text: str, words: int = MAX_SHORT_WORDS) -> str:
    parts = text.split()
    return " ".join(parts[:words])


def _strip_company(name: str) -> str:
    """Drop the corporate forms of a case name: OSCOLA cites `Facebook Ireland`, not `… Ltd`."""
    kept = [
        word
        for word in name.split()
        if word.lower().strip(",") not in COMPANY_SUFFIXES
    ]
    return re.sub(r"\s+,", ",", " ".join(kept)).strip(" ,")


def _parenthetical(view: dict) -> str:
    """The short designation a title or citation form carries in brackets (`… (GDPR)`)."""
    for text in (view.get("citation_form") or "", view.get("title") or ""):
        for match in PARENTHETICAL_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate and not candidate.upper().startswith(("EU)", "EC)")) and candidate not in ("EU", "EC", "EEC"):
                if not re.match(r"^(CELEX|ELI|OJ)\b", candidate, re.IGNORECASE):
                    return candidate
    return ""


_ARTICLE_TAIL_RE = re.compile(
    r"\s+(?:art(?:icle)?s?|annexe?s?|recitals?|s|§)\b.*$", re.IGNORECASE
)


def _instrument_short(view: dict) -> str:
    """`GDPR` out of `GDPR Art 35`: the short name of the act, never of one of its articles."""
    explicit = _meta_value(view, ("short_name", "short_title"))
    if explicit:
        trimmed = _ARTICLE_TAIL_RE.sub("", explicit).strip(" ,")
        return trimmed or explicit
    parenthetical = _parenthetical(view)
    if parenthetical and len(parenthetical.split()) <= MAX_SHORT_WORDS:
        return parenthetical
    return ""


def _case_name(view: dict) -> str:
    """The parties of a decision: the title without its `(nickname)` tail."""
    title = view.get("title") or ""
    name = NICKNAME_RE.sub("", title).strip(" ,")
    if not name:
        cite = _strip_noise(view.get("citation_form") or "")
        name = CASE_NUMBER_RE.sub("", cite)
        name = ECLI_SHORT_RE.sub("", name).strip(" ,")
    return _strip_company(name)


def short_name(view: dict) -> str:
    """The short name a repeat mention is cited under (§5.5, D-150); never empty."""
    kind = source_class(view)
    explicit = _meta_value(view, ("short_name", "short_title"))
    if kind == CLASS_EU_LEGISLATION:
        return _instrument_short(view) or _instrument(view) or str(view.get("source_id") or "source")
    if explicit:
        # `Abraha, IDPL 2022` is a filing name; the citation names the author.
        return explicit.split(",")[0].strip() if kind == CLASS_DOCTRINE else explicit
    title = view.get("title") or view.get("citation_form") or ""
    nickname = NICKNAME_RE.search(title)
    if nickname:
        return nickname.group(1).strip()
    if kind in (CLASS_CJEU, CLASS_CASE):
        parties = PARTY_RE.split(_case_name(view))
        # OSCOLA cites the distinctive party; with `X v Y` that is the defendant (D-150).
        last = parties[-1].strip(" ,") if parties else ""
        if last:
            return _cut(_strip_company(last), 3)
    if kind == CLASS_DOCTRINE:
        author = (view.get("citation_form") or "").split(",")[0].strip()
        if author:
            return author.split()[-1]
    if kind == CLASS_SOFT_LAW:
        body, document = _soft_law_parts(view)
        joined = " ".join(part for part in (body, document) if part).strip()
        if joined:
            return joined
    head = title.split(",")[0].strip()
    return _cut(head) or str(view.get("source_id") or "source")


# --- trimming the registry's own citation form -----------------------------


def _strip_noise(text: str) -> str:
    """Drop URLs, CELEX tags and `retrieved …` tails — none of them belong in the body (D-150)."""
    text = URL_RE.sub("", str(text or ""))
    text = CELEX_TAG_RE.sub("", text)
    text = RETRIEVED_RE.sub("", text)
    text = re.sub(r"<\s*>", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;-")


def _trim(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace(" ,", ",").strip(" ,;")


def _join(head: str, pinpoint: str) -> str:
    head = _trim(head)
    return f"{head}, {pinpoint}" if head and pinpoint else head or pinpoint


# --- the six compact forms -------------------------------------------------


def _instrument(view: dict) -> str:
    """`Regulation (EU) 2016/679` — the designation of an EU act, never its article heading."""
    text = _text_of(view)
    match = EU_ACT_RE.search(text)
    if match:
        kind = re.sub(r"\s+", " ", match.group(1)).strip()
        return f"{kind} ({match.group(2)}) {match.group(3)}"
    match = EU_ACT_OLD_RE.search(text)
    if match:
        return f"{match.group(1)} {match.group(2)}/{match.group(3)}"
    # D-152: the identifiers carry the designation when the prose does not.
    return act_from_celex(celex_of(view)) or act_from_eli(view)


def act_identity(view: dict) -> str:
    """The identity of an EU act, identifiers first (D-152).

    `instrument_key` needs one string for a record that carries only a CELEX and a record that
    carries only an ELI; the displayed `_instrument` keeps whatever the citation form spells out
    (`Commission Implementing Decision (EU) 2023/1795`), which is richer but not comparable.
    """
    identity = act_from_celex(celex_of(view)) or act_from_eli(view)
    if identity:
        return identity
    designation = _instrument(view)
    match = EU_ACT_RE.search(designation)
    if match:
        # `Commission Implementing Decision (EU) 2023/1795` -> `Decision (EU) 2023/1795`.
        kind = re.sub(r"\s+", " ", match.group(1)).strip().split()[-1]
        return f"{kind} ({match.group(2)}) {match.group(3)}"
    return designation


def case_identity(view: dict) -> str:
    """The identity of a decision: its ECLI, else its case number, else its citation (D-152)."""
    ecli = _identifier(view, "ecli")
    if ecli:
        return ecli
    match = CASE_NUMBER_RE.search(_text_of(view))
    if match:
        return re.sub(r"\s+", " ", match.group(1))
    return case_from_celex(celex_of(view)) or _identifier(
        view, "neutral", "reporter_cite", "docket"
    )


_RECORD_PINPOINT_RE = re.compile(
    r"^(?:art(?:icle)?s?|annexe?s?|recitals?)\b[^,;]*", re.IGNORECASE
)
_TITLE_ARTICLE_RE = re.compile(r"\b(Article|Annex|Recital)\s+([0-9]+[a-z]?|[IVXL]+)", re.IGNORECASE)


def record_pinpoint(view: dict) -> str:
    """The article the *record* is about, for a mention that carries no pinpoint of its own (D-150).

    A registry keyed per article (`GDPR Article 6 — Lawfulness of processing`) must not be cited as
    the bare instrument just because the draft wrote `[[src:gdpr-art6]]` without a pinpoint.
    """
    if source_class(view) not in (CLASS_EU_LEGISLATION, CLASS_LEGISLATION):
        return ""
    citation = _strip_noise(view.get("citation_form") or "")
    instrument = _instrument(view)
    tail = ""
    if instrument:
        for match in (EU_ACT_RE.search(citation), EU_ACT_OLD_RE.search(citation)):
            if match:
                tail = citation[match.end():].strip(" ,;")
                break
    found = _RECORD_PINPOINT_RE.match(tail)
    if found:
        return normalise_pinpoint(found.group(0))
    found = _TITLE_ARTICLE_RE.search(view.get("title") or "")
    if found:
        label = {"article": "art", "annex": "annex", "recital": "recital"}[found.group(1).lower()]
        return f"{label} {found.group(2)}"
    return ""


def _eu_legislation(view: dict, pinpoint: str, language: str = i18n.DEFAULT) -> str:
    """`Regulation (EU) 2016/679 (GDPR), art 35(3)(a)` (D-150)."""
    instrument = _instrument(view)
    if not instrument:
        return _join(_strip_noise(view.get("citation_form") or view.get("title") or ""), pinpoint)
    short = _instrument_short(view)
    if short and short.lower() not in instrument.lower() and len(short) <= 24:
        instrument = f"{instrument} ({short})"
    return _join(instrument, pinpoint)


def _legislation(view: dict, pinpoint: str, language: str = i18n.DEFAULT) -> str:
    """`Data Protection Act 2018, s 2(1)`, `15 USC § 45` — national legislation (D-150)."""
    cite = _strip_noise(view.get("citation_form") or "")
    head = cite.split(",")[0].strip() if cite else ""
    if not head:
        head = _strip_noise(view.get("title") or "").split(" - ")[0].strip()
    return _join(head, pinpoint)


def _cjeu(view: dict, pinpoint: str, language: str = i18n.DEFAULT, name: str | None = None) -> str:
    """`Case C-311/18 Data Protection Commissioner v … EU:C:2020:559, para 2 …` (D-150)."""
    text = _text_of(view)
    number = ""
    match = CASE_NUMBER_RE.search(text)
    if match:
        number = re.sub(r"\s+", " ", match.group(1))
    else:
        number = case_from_celex(celex_of(view))
    ecli = _identifier(view, "ecli").replace("ECLI:", "").strip()
    if not ecli:
        found = ECLI_SHORT_RE.search(text)
        ecli = found.group(1) if found else ""
    parties = name if name is not None else _case_name(view)
    head = " ".join(part for part in (f"Case {number}" if number else "", parties, ecli) if part)
    return _join(head, pinpoint)


def _case(view: dict, pinpoint: str, language: str = i18n.DEFAULT) -> str:
    """Any other court by its conventional form — the registry's citation form, trimmed (D-150)."""
    cite = _strip_noise(view.get("citation_form") or "")
    if not cite:
        name = _case_name(view)
        identifier = _identifier(view, "neutral", "reporter_cite", "docket", "ecli")
        cite = " ".join(part for part in (name, identifier) if part)
    return _join(cite, pinpoint)


def _acronym(text: str) -> str:
    """`European Data Protection Board` -> `EDPB`; a text that is already short comes back whole."""
    plain = re.sub(r"\(.*?\)", " ", str(text or ""))
    words = [word for word in re.split(r"[^A-Za-z0-9]+", plain) if word]
    if len(words) <= 1:
        return " ".join(words)
    initials = "".join(word[0].upper() for word in words if word[0].isupper())
    return initials if len(initials) >= 2 else " ".join(words)


def _soft_law_parts(view: dict) -> tuple[str, str]:
    """`(EDPB, Recommendations 01/2020)` out of the short name, else the body and the title."""
    explicit = _meta_value(view, ("short_name", "short_title"))
    if explicit:
        words = explicit.split()
        opener = words[0] if words else ""
        rest = " ".join(words[1:])
        splits = (
            len(words) > 1
            and opener.upper() == opener
            and opener not in NOT_A_BODY
            and len(opener) >= 2
            and words[1].strip("(").lower() in DOC_TYPE_WORDS
        )
        if splits:
            return opener, rest
        return "", explicit
    body = _meta_value(view, _META_BODY_KEYS)
    document = _strip_noise(view.get("title") or view.get("citation_form") or "")
    if body and document.lower().startswith(body.lower()):
        document = document[len(body):].strip(" ,")
    return body, document


def _version(view: dict) -> str:
    """`v 2.0` when the registry records a version of the guidance."""
    match = re.search(
        r"\b(?:version|v)\s*\.?\s*([0-9]+(?:\.[0-9]+)?)", _text_of(view), re.IGNORECASE
    )
    return f"v {match.group(1)}" if match else ""


def _soft_law(view: dict, pinpoint: str, language: str = i18n.DEFAULT) -> str:
    """`EDPB, Recommendations 01/2020 (v 2.0, 18 June 2021) paras 44, 89` (D-150)."""
    body, document = _soft_law_parts(view)
    head = ", ".join(part for part in (body, document) if part)
    inside = ", ".join(
        part
        for part in (_version(view), long_date(_meta_value(view, _META_DATE_KEYS), language))
        if part
    )
    if inside:
        head = f"{head} ({inside})" if head else f"({inside})"
    return f"{_trim(head)} {pinpoint}".strip() if pinpoint else _trim(head)


def _doctrine(view: dict, pinpoint: str, language: str = i18n.DEFAULT) -> str:
    """`Abraha, 'A pragmatic compromise?' (2022) 12 IDPL 276, 280` (D-150)."""
    cite = _strip_noise(view.get("citation_form") or "")
    author = short_name(view)
    title = ""
    quoted = QUOTED_TITLE_RE.search(cite)
    if quoted:
        title = _first_clause(quoted.group(1))
    tail = ""
    journal = JOURNAL_RE.search(cite)
    if journal:
        name = journal.group(4).strip()
        abbreviated = _acronym(name) if len(name.split()) >= 3 else name
        tail = f"({journal.group(1)}) {journal.group(2)} {abbreviated} {journal.group(5)}"
    if author and title and tail:
        head = f"{author}, '{title}' {tail}"
        return f"{head}, {pinpoint}" if pinpoint else head
    return _join(cite or view.get("title") or "", pinpoint)


_CLAUSE_END_RE = re.compile(r"^(.{4,}?[?!.:;])(?:\s|$)")


def _first_clause(text: str) -> str:
    """`A pragmatic compromise? The role of …` -> `A pragmatic compromise?` (D-150)."""
    text = _trim(text)
    match = _CLAUSE_END_RE.match(text)
    if match:
        return match.group(1).strip()
    words = text.split()
    return text if len(words) <= 8 else " ".join(words[:8]) + "…"


_COMPACT = {
    CLASS_EU_LEGISLATION: _eu_legislation,
    CLASS_LEGISLATION: _legislation,
    CLASS_CJEU: _cjeu,
    CLASS_CASE: _case,
    CLASS_SOFT_LAW: _soft_law,
    CLASS_DOCTRINE: _doctrine,
}
"""One builder per source class; all six take `(view, pinpoint, language)` so the dispatch is one
call. The pinpoint arrives already in its display form, and only the soft-law form has a word of its
own to translate — the date inside the citation."""


def compact(view: dict, pinpoint: str = "", language: str = i18n.DEFAULT) -> str:
    """The citation of a first mention: one form per source class, no URL, no CELEX, no date (D-150)."""
    pin = display_pinpoint(normalise_pinpoint(pinpoint) or record_pinpoint(view), language)
    kind = source_class(view)
    text = _trim(_COMPACT[kind](view, pin, language))
    if len(text) <= MAX_COMPACT_CHARS:
        return text or short(view, pinpoint, language=language)
    if kind == CLASS_CJEU:
        text = _trim(_cjeu(view, pin, language, name=short_name(view)))
    else:
        text = _trim(short(view, pinpoint, language=language))
    if len(text) <= MAX_COMPACT_CHARS:
        return text
    return _hard_cut(text)


def _hard_cut(text: str) -> str:
    cut = text[: MAX_COMPACT_CHARS - 1]
    head, space, _ = cut.rpartition(" ")
    if space and len(head) > MAX_COMPACT_CHARS // 2:
        cut = head
    return cut.rstrip(" ,;:.") + "…"


def short(
    view: dict, pinpoint: str = "", first_n: int | None = None, language: str = i18n.DEFAULT
) -> str:
    """`GDPR, art 88(1)` — with `(n 19)` in front of the pinpoint in the footnote style only (D-150)."""
    pin = display_pinpoint(normalise_pinpoint(pinpoint) or record_pinpoint(view), language)
    name = short_name(view)
    if first_n:
        name = f"{name} (n {int(first_n)})"
    return f"{name}, {pin}" if pin else name


def instrument_form(view: dict, language: str = i18n.DEFAULT) -> str:
    """The citation of the **work**, without the article the record happens to be about (D-150).

    `Regulation (EU) 2016/679 (GDPR)` for every one of the eleven GDPR articles the registry keeps
    as separate sources: what the `## Sources` annex lists, and what the short form cites.
    """
    return _trim(_COMPACT[source_class(view)](view, "", language))


def instrument_key(view: dict) -> str:
    """The identity of that work: the registry is article-level, the citation is not (D-150).

    An EU act is its designation (CELEX and ELI collapse into it), a decision its ECLI or case
    number, anything else the citation stem without a pinpoint. Keying the mentions by this instead
    of by `source_id` is what makes `art 28(3)` and `art 44` the second and third mention of one
    GDPR rather than two more first mentions.
    """
    kind = source_class(view)
    if kind == CLASS_EU_LEGISLATION:
        stem = act_identity(view) or _identifier(view, "celex", "eli")
    elif kind in (CLASS_CJEU, CLASS_CASE):
        stem = case_identity(view) or instrument_form(view)
    else:
        stem = instrument_form(view)
    stem = re.sub(r"\s+", " ", str(stem or "")).strip().casefold()
    return f"{kind}:{stem}" if stem else f"{kind}:{view.get('source_id') or ''}"


# --- the plan both renderers follow ----------------------------------------

FORM_FULL = "full"
FORM_SHORT = "short"
FORM_IBID = "ibid"
FORM_OMITTED = "omitted"


def assign_forms(mentions: list, style: str) -> list[str]:
    """Number the mentions in document order and pick each one's form; returns the cited instruments.

    One form per source class (`compact` first, `short` later) and one first mention **per
    instrument** (`instrument_key`), not per `source_id`: an article-level registry must not make
    every article of the GDPR a first mention of its own. `(n N)` back-references appear only in the
    footnote style, and two adjacent citations of the same instrument **and** the same pinpoint
    collapse into `ibid` (footnotes) or into nothing at all (inline) — D-150.

    D-152: `ibid` is a claim about the citation *immediately before it on the page*, so the chain
    breaks at a section heading, and the citation of a blockquote is an attribution of its own — it
    is never an `ibid`, and in the inline style it is never dropped, because dropping it would take
    the attribution line under the quotation with it.

    A mention carrying its `view` is keyed by the instrument; without one (a caller that only has
    ids) the `source_id` is the key, which is the same thing for a registry of whole works.
    """
    footnotes = style == STYLE_FOOTNOTES
    first_n: dict[str, int] = {}
    cited: list[str] = []
    number = 0
    previous: tuple | None = None
    for mention in mentions:
        if not mention.get("resolved"):
            previous = None
            continue
        view = mention.get("view") if isinstance(mention.get("view"), dict) else None
        instrument = instrument_key(view) if view else str(mention["source_id"])
        mention["instrument"] = instrument
        pinpoint = normalise_pinpoint(mention.get("pinpoint"))
        mention["pinpoint"] = pinpoint
        # Two mentions of one instrument are only `ibid` when they cite the very same place; an
        # article-level record without a written pinpoint still means its own article.
        cited_place = pinpoint or (record_pinpoint(view) if view else "")
        mention["cited_place"] = cited_place
        quoted = bool(mention.get("blockquote"))
        key = (instrument, cited_place, mention.get("section_id"))
        seen = instrument in first_n
        if quoted:
            mention["form"] = FORM_SHORT if seen else FORM_FULL
        elif previous == key:
            mention["form"] = FORM_IBID if footnotes else FORM_OMITTED
        elif seen:
            mention["form"] = FORM_SHORT
        else:
            mention["form"] = FORM_FULL
        if instrument not in cited:
            cited.append(instrument)
        if footnotes and mention["form"] != FORM_OMITTED:
            number += 1
            mention["n"] = number
            if not seen:
                first_n[instrument] = number
        else:
            mention["n"] = None
            if not seen:
                first_n[instrument] = len(cited)
        mention["first_n"] = first_n[instrument]
        # D-152: an attribution under a quotation stands on its own, in both directions — the next
        # citation of the same place is not `ibid` of a line the reader reads as the quote's source.
        previous = None if quoted else key
    return cited


def mention_text(view: dict, mention: dict, style: str, language: str = i18n.DEFAULT) -> str:
    """The rendered citation of one mention, in the style of the run (D-150).

    D-175a: `assign_forms` has already decided what this mention is, on the canonical pinpoints;
    the language only decides how the decision is written down.
    """
    form = mention.get("form") or FORM_FULL
    pinpoint = mention.get("pinpoint") or ""
    if form == FORM_FULL:
        return compact(view, pinpoint, language)
    if form == FORM_IBID:
        # The pinpoint is the one the citation before it carried — that is what makes this `ibid`.
        return citation_word("ibid", language)
    back_reference = mention.get("first_n") if style == STYLE_FOOTNOTES else None
    return short(view, pinpoint, back_reference, language)


# --- links and the annex ---------------------------------------------------

ARTICLE_NUMBER_RE = re.compile(r"^(?:art|arts)\s+([0-9]+)")


def anchor_url(view: dict, pinpoint: str = "") -> str:
    """The registered URL, with `#art_<N>` when a consolidated CELEX makes the anchor real (D-150).

    Without a consolidated version the link goes to the top of the act: eur-lex numbers the anchors
    of a consolidated text only, and a guessed fragment is a broken link.
    """
    url = str(view.get("url") or "").strip()
    if not url:
        return ""
    identifiers = view.get("identifiers") or {}
    consolidated = identifiers.get("celex_consolidated") or _meta_value(view, ("version_celex",))
    if not consolidated or source_class(view) != CLASS_EU_LEGISLATION:
        return url
    article = ARTICLE_NUMBER_RE.match(normalise_pinpoint(pinpoint))
    if article is None:
        return url
    return f"{url.split('#', 1)[0]}#art_{article.group(1)}"


SOURCES_SEPARATOR = " — "
"""D-150: the annex fields of one instrument — citation, identifiers, pinpoints, URL, provenance."""

AMENDED_STATUSES: tuple[str, ...] = ("amended", "repealed", "superseded")
"""Currency statuses the annex names the article for, instead of folding into one line (D-150)."""


def canonical_url(view: dict) -> str:
    """The URL of the instrument, not of one of its articles: the fragment goes (D-150)."""
    return str(view.get("url") or "").strip().split("#", 1)[0]


def identifiers_field(views: list) -> str:
    """`CELEX 32016R0679, ELI …` — the identifiers of the instrument, each printed once (D-150)."""
    parts: list[str] = []
    for view in views:
        for label, name in (("CELEX", "celex"), ("ECLI", "ecli"), ("ELI", "eli")):
            value = _identifier(view, name)
            if not value:
                continue
            text = value if value.upper().startswith(f"{label}:") else f"{label} {value}"
            if text not in parts:
                parts.append(text)
    return ", ".join(parts)


def provenance_field(views: list, language: str = i18n.DEFAULT) -> str:
    """`checked 2026-09-10, currency unchecked; art 6 amended` (D-150).

    One currency verdict per instrument — the run checks the work, not each article — plus the
    articles whose own status says the text moved under the memorandum. D-175a: the article is
    named by its display form, the status it carries is the recorded token and stays as it is.
    """
    retrieved = ""
    status = ""
    for view in views:
        retrieved = retrieved or date_only(view.get("retrieved_at"))
        status = status or str(view.get("currency_status") or "").strip()
    notes: list[str] = []
    for view in views:
        value = str(view.get("currency_status") or "").strip().lower()
        if value not in AMENDED_STATUSES:
            continue
        where = display_pinpoint(record_pinpoint(view), language) or short_name(view)
        note = f"{where} {value}".strip()
        if note not in notes:
            notes.append(note)
    head = ", ".join(
        part
        for part in (
            f"{citation_word('checked', language)}{retrieved}" if retrieved else "",
            f"{citation_word('currency', language)}{status}" if status else "",
        )
        if part
    )
    return "; ".join(part for part in ([head] if head else []) + notes)


def sources_entry(row: dict, language: str = i18n.DEFAULT) -> str:
    """One `## Sources` line: the full record of one **instrument** (D-150).

    The annex is where everything the body does not carry lives — the identifiers, every place the
    memorandum cites, the canonical URL of the work, the retrieval date and the currency status.
    The article-level titles of a registry keyed per article are not printed: eleven lines reading
    `GDPR Article 3 - Territorial scope` are the wall of text the body was freed from.
    """
    views = [view for view in (row.get("members") or [row.get("view")]) if isinstance(view, dict)]
    if not views:
        return ""
    parts = [
        instrument_form(views[0], language),
        identifiers_field(views),
        row.get("pinpoints_text") or "",
    ]
    url = canonical_url(views[0])
    if url:
        parts.append(f"<{url}>")
    parts.append(provenance_field(views, language))
    return SOURCES_SEPARATOR.join(part for part in parts if part)


def unresolved_text(raw_id: str) -> str:
    """Footnote-less marker of an id the frozen snapshot does not know (§5.5)."""
    return f"[unresolved: {raw_id}]"
