"""Stdlib-only markdown fallback of the export (ТЗ §5.5: «Fallback (stdlib-only, без AST)»).

`[[src:<id> pinpoint]]` and `[[q:<quote_id>]]` become a citation — a `(compact form)` linked to the
source in the inline style, a `[n]` marker in the footnote style (D-150) — the `## Sources` annex is
built from the frozen `research/source-pack.json` snapshot plus `research/sources.json` and carries
the full record of every cited source, an id that resolves in neither becomes `[unresolved: <id>]`
and raises the `unresolved_reference` banner, a `## Status` section states the banners and the
unresolved blockers of a run that did not end approved (D34-11) and the «Assumptions & Unverified
Sources» appendix is generated from `drafting_warnings` and the per-source
`verification`/`currency`/`liveness` records. No `python-docx`, no `mistune`, no network.

The scan of the citation tokens and the numbering of the mentions live here and are shared with
`renderer.py`: both deliverables must decide every citation the same way (D-150).
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from .. import fallbacks, state_io
from ..sources import canonical_id
from . import oscola

SRC_TOKEN = re.compile(r"\[\[src:\s*(?P<id>[^\]\s]+)(?P<pinpoint>[^\]]*)\]\]")
QUOTE_TOKEN = re.compile(r"\[\[q:\s*(?P<id>[^\]\s]+)\s*\]\]")
ANY_TOKEN = re.compile(
    r"\[\[(?:src:\s*(?P<sid>[^\]\s]+)(?P<pinpoint>[^\]]*)|q:\s*(?P<qid>[^\]\s]+)\s*)\]\]"
)
"""One pass over both token kinds so footnote numbers follow first mention in document order (§5.5)."""
SOURCES_MARKER = re.compile(r"^<!--\s*sources:\s*generated\s*-->\s*$", re.MULTILINE)

SOURCES_HEADING = "## Sources"
APPENDIX_HEADING = "## Appendix — Assumptions & Unverified Sources"

PINPOINT_SEPARATOR = ", "
PINPOINT_FIRST_PREFIX = "cited at "
PINPOINT_LATER_PREFIX = "also cited at "
"""D34-22/D-124: one `## Sources` line carries every mention of its source, so it prints their
pinpoints in mention order — and says «also» when the first mention had none, instead of pinning a
later article on it. D-150 made the line a `; `-separated record, so the pinpoints join with `, `."""

MENTION_OPEN = "\ue000"
MENTION_CLOSE = "\ue001"
MENTION_RE = re.compile(f"{MENTION_OPEN}([0-9]+){MENTION_CLOSE}")
"""Private-use sentinel that carries a citation through the markdown parser and the blockquote pass
untouched; both renderers scan the tokens once and substitute the rendered citation last (D-150)."""

BLOCKQUOTE_LINE = re.compile(r"^[ \t]{0,3}>")
HEADING_LINE = re.compile(r"^[ \t]{0,3}#{1,6}\s")
"""D-152: a heading opens a new section, and a citation chain never runs across one."""
ATTRIBUTION_PREFIX = "— "
"""D34-22/D-124: the citation of a quotation is an attribution line under it (`— [2]` in the
footnote style, `— (Schrems II, para 2)` linked in the inline one), never a word inside the quoted
rule. `[unresolved: …]` is left where it is — it is not a citation."""

SPACED_MENTION_RE = re.compile(f"[ \t]*{MENTION_OPEN}([0-9]+){MENTION_CLOSE}")
"""D-150: the second of two adjacent identical citations is dropped in the inline style, and the
space that carried it goes with it — `… the operative one [[src:x p 44]].` must not end in ` .`."""

UNVERIFIED_US: tuple[str, ...] = ("unresolved", "ambiguous")
"""`verification.us` values that put a source into the appendix (§5.3 «Не найдено ≠ выдумано»)."""

UNVERIFIED_CURRENCY: tuple[str, ...] = ("manual_check", "do_not_use", "unchecked")
"""`currency.status` values that put a source into the appendix (§5.3, gate 11 digest)."""

UNVERIFIED_LIVENESS: tuple[str, ...] = ("dead", "changed")
"""`liveness.status` values that put a source into the appendix (§5.3)."""

# --- the appendix is written for the client (D-113) ------------------------

APPENDIX_WARNING_LIMIT = 12
"""How many assumption bullets the appendix carries before it points at `summary.md` (D-113)."""

APPENDIX_WARNING_CHARS = 240
"""Hard cap per bullet, ellipsis included (D-113)."""

APPENDIX_MORE = "… and {count} more in summary.md"

ASSUMPTIONS_LABEL = "Assumptions carried into the analysis"
UNVERIFIED_LABEL = "Unverified sources"
UNRESOLVED_LABEL = "Unresolved references"
"""The three sub-headings of the appendix; `renderer.py` prints them bold, markdown wraps them in `**`."""

ASSUMPTIONS_MD = f"**{ASSUMPTIONS_LABEL}**"
UNVERIFIED_MD = f"**{UNVERIFIED_LABEL}**"
UNRESOLVED_MD = f"**{UNRESOLVED_LABEL}**"

CURRENCY_UNAVAILABLE_BANNER = "currency_unavailable"
"""The `currency_checker_failed` row of `fallbacks.py`: the checker was down for the *whole* run."""

CURRENCY_UNAVAILABLE_NOTE = (
    "Source currency was not checked in this run (currency checker unavailable); "
    "verify before client use."
)

UNRESOLVED_MARKER = re.compile(r"\[unresolved:\s*([^\]]+)\]")
"""The marker left in a rendered body; lets a re-read of the deliverable recover the ids (D-113)."""

# --- the status of a run that did not end approved (D34-11) ----------------

STATUS_HEADING = "## Status"
STATUS_LABEL = "Status"
"""The section both deliverables carry when the run did not end approved; `renderer.py` prints the
label bold, markdown prints the heading. Kept in one place so `docx validate` can look for it."""

APPROVED_STATUS_PREFIXES: tuple[str, ...] = ("approved", "client_ready")
"""`final_status` prefixes that mean the pipeline signed the memo off (§2.1 row 15)."""

STATUS_LEAD = (
    "Final status: {final_status}. The pipeline did not sign this memorandum off; "
    "the points below are unresolved and must be checked before client use."
)

STATUS_BANNERS_LABEL = "Pipeline notices"
STATUS_ISSUES_LABEL = "Unresolved blocking issues"
STATUS_BANNERS_MD = f"**{STATUS_BANNERS_LABEL}**"
STATUS_ISSUES_MD = f"**{STATUS_ISSUES_LABEL}**"

STATUS_ISSUE_LIMIT = 12
"""How many `remaining_blocking_issues[]` rows the deliverable prints before pointing at the summary."""

STATUS_ISSUE_SEPARATOR = " · "
"""`severity · section_id · issue` — the shape of one blocker line (D34-11)."""

COPY_BANNERS: frozenset = frozenset({"publish_failed", "output_folder_unavailable"})
"""Banners about the *copy* of the finished result, not about the memorandum itself (D-144).

`finalize` fixes its banner list before `choose_deliverable` picks the deliverable — every other row
is therefore already in the `## Status` section the export carries. These two are the exception:
they are raised by the copy that runs *after* the deliverable exists, so a Status section that
listed them could never be written into a docx that was already exported, and comparing them would
make a perfectly current export stale over one sentence about a folder the memo never describes.
They stay in `summary.md`, which is re-rendered after the copy (D-109, D-111)."""

_WARNING_ID_TAG = re.compile(r"\s*\((?:`[^`()]+`|[a-z0-9]+(?:_[a-z0-9]+)+)\)")
"""The `(warning_id)` tag `warning_text` appends — machine talk, not client text."""

_INTERNAL_FILE = re.compile(r"[a-z_\-/]+\.json")
"""`research/doctrine.json`, `statutes.json`: protocol files the reader has no access to."""

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[^a-z0-9]|[a-z_\-/]+\.json)")
"""Sentence boundary that survives `s.26`, `no.6` and `e.g.` (the next word stays lowercase there).

A sentence opening with a protocol file — `… no layer's sources. research/doctrine.json states …` —
is a boundary as well, otherwise dropping the file name would glue the two sentences together.
"""


def assumption_bullets(warnings: list) -> list[str]:
    """One short bullet per `drafting_warnings[]` entry for the appendix (D-113).

    The warnings are the sufficiency reviewer's prose *addressed to the writer* — in the run that
    motivated this the 15 of them made a 2 670-word appendix under a 1 000-word memo. The client
    gets the first sentence of each, without the `(warning_id)` tag and without the names of
    protocol files; `summary.md` keeps every warning verbatim, and the last bullet says so.
    """
    bullets: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        text = assumption_bullet(warning)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        bullets.append(text)
    if len(bullets) <= APPENDIX_WARNING_LIMIT:
        return bullets
    rest = len(bullets) - APPENDIX_WARNING_LIMIT
    return bullets[:APPENDIX_WARNING_LIMIT] + [APPENDIX_MORE.format(count=rest)]


def assumption_bullet(warning: object) -> str:
    """First sentence of one warning, tag- and filename-free, at most `APPENDIX_WARNING_CHARS`."""
    if isinstance(warning, dict):
        raw = str(warning.get("message") or warning.get("code") or "")
    else:
        raw = str(warning or "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    # The sentence is cut before the file names are dropped: `research/doctrine.json states …` would
    # otherwise start the next sentence with a lowercase word and the cut would run past it.
    sentence = _SENTENCE_END.split(raw, 1)[0]
    sentence = _INTERNAL_FILE.sub(" ", _WARNING_ID_TAG.sub(" ", sentence))
    sentence = re.sub(r"\s+([,;:.])", r"\1", re.sub(r"\s+", " ", sentence)).strip()
    if len(sentence) <= APPENDIX_WARNING_CHARS:
        return sentence
    cut = sentence[: APPENDIX_WARNING_CHARS - 1]
    head, space, _ = cut.rpartition(" ")
    if space and len(head) > APPENDIX_WARNING_CHARS // 2:
        cut = head
    return cut.rstrip(" ,;:.") + "…"


def is_approved(final_status: object) -> bool:
    """True when `final_status` is one the pipeline itself signed off (D34-11)."""
    return str(final_status or "").startswith(APPROVED_STATUS_PREFIXES)


def blocking_issue_line(issue: object) -> str:
    """One `severity · section_id · issue` row of `state.remaining_blocking_issues` (D34-11)."""
    if not isinstance(issue, dict):
        return re.sub(r"\s+", " ", str(issue or "")).strip()
    parts = [
        str(issue.get("severity") or "").strip(),
        str(issue.get("section_id") or "").strip(),
        re.sub(r"\s+", " ", str(issue.get("issue") or issue.get("category") or "")).strip(),
    ]
    return STATUS_ISSUE_SEPARATOR.join(part for part in parts if part)


def blocking_issue_lines(issues: list) -> list[str]:
    """The blocker rows of the deliverable: capped, the rest pointed at `summary.md` (D34-11)."""
    rows = [line for line in (blocking_issue_line(issue) for issue in issues or ()) if line]
    if len(rows) <= STATUS_ISSUE_LIMIT:
        return rows
    rest = len(rows) - STATUS_ISSUE_LIMIT
    return rows[:STATUS_ISSUE_LIMIT] + [APPENDIX_MORE.format(count=rest)]


def status_inputs(state: dict | None, extra_banners: list | None = None) -> dict:
    """What the `## Status` section says, out of state plus the banners this render raised (D34-11).

    Both rendered forms read the same dict, so `deliverable.md` and `deliverable.docx` cannot
    disagree about why a run needs a human. `required` is the invariant `docx validate` enforces:
    a `final_status` that is not an approved one must be visible in the document itself.

    D-144: this dict *is* the section, so it is also what `docx.status_signature` hashes — the ids
    next to their texts, and the rendered blocker lines, not only the ids. `COPY_BANNERS` are left
    out: they describe the copy of the result, not the memorandum.
    """
    state = state or {}
    final_status = str(state.get("final_status") or "")
    ids: list[str] = []
    texts: list[str] = []
    seen: set[str] = set()
    for row in list(state.get("fallback_banners") or []) + list(extra_banners or []):
        if isinstance(row, dict):
            key = str(row.get("banner_id"))
            text = str(row.get("text") or row.get("banner_id") or "")
        else:
            key = text = str(row or "")
        text = re.sub(r"\s+", " ", text).strip()
        if not text or key in seen or key in COPY_BANNERS:
            continue
        seen.add(key)
        ids.append(key)
        texts.append(text)
    return {
        "required": bool(final_status) and not is_approved(final_status),
        "final_status": final_status,
        "banner_ids": ids,
        "banners": texts,
        "issues": blocking_issue_lines(state.get("remaining_blocking_issues") or []),
    }


def render_status(inputs: dict) -> str:
    """`## Status` block of the markdown deliverable; empty string when the run ended approved."""
    if not inputs.get("required"):
        return ""
    lines = [STATUS_HEADING, "", STATUS_LEAD.format(final_status=inputs["final_status"]), ""]
    if inputs["banners"]:
        lines.extend([STATUS_BANNERS_MD, ""])
        lines.extend(f"- {row}" for row in inputs["banners"])
        lines.append("")
    if inputs["issues"]:
        lines.extend([STATUS_ISSUES_MD, ""])
        lines.extend(f"- {row}" for row in inputs["issues"])
        lines.append("")
    return "\n".join(lines)


def currency_checker_unavailable(state: dict | None) -> bool:
    """True when `currency_unavailable` fired: the checker was down for the run, not per source."""
    for row in (state or {}).get("fallback_banners") or []:
        banner_id = row.get("banner_id") if isinstance(row, dict) else row
        if banner_id == CURRENCY_UNAVAILABLE_BANNER:
            return True
    return False


def unverified_line(row: dict) -> str:
    """One «Unverified sources» entry, shared by the markdown and the docx appendix."""
    return f"{row['citation_form']} — {'; '.join(row['notes'])}"


def unresolved_ids(body: str) -> list[str]:
    """The `[unresolved: <id>]` ids of an already rendered body, first mention first (D-113)."""
    found: list[str] = []
    for match in UNRESOLVED_MARKER.finditer(body):
        raw_id = match.group(1).strip()
        if raw_id and raw_id not in found:
            found.append(raw_id)
    return found


class SourceIndex:
    """Resolver over the frozen snapshot, the registry and the quote index (M6, §5.3)."""

    def __init__(
        self,
        *,
        snapshot_ids: list[str] | None = None,
        entries: dict | None = None,
        sources: dict | None = None,
        quotes: dict | None = None,
        merged: dict | None = None,
        frozen: bool = False,
        currency_unavailable: bool = False,
    ) -> None:
        self.snapshot_ids = list(snapshot_ids or [])
        self.entries = dict(entries or {})
        self.sources = dict(sources or {})
        self.quotes = dict(quotes or {})
        self.merged = {str(alias): str(target) for alias, target in (merged or {}).items() if target}
        """D-143: `merged_into` of the freeze — the aliases a draft may still cite by name."""
        self.frozen = frozen
        self.currency_unavailable = currency_unavailable
        """D-113: the whole run went unchecked, so «currency unchecked» is one line, not one per source."""

    @classmethod
    def load(cls, work_dir: str | Path, *, state: dict | None = None) -> "SourceIndex":
        """Read `research/{source-pack,sources,quotes}.json`; every file is optional."""
        research = Path(work_dir) / "research"
        pack_path = research / "source-pack.json"
        pack = _read_object(pack_path)
        registry = _read_object(research / "sources.json")
        quote_file = _read_object(research / "quotes.json")

        snapshot_ids = [
            row.get("source_id")
            for row in (pack.get("snapshot") or [])
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)
        ]
        entries = {
            row["source_id"]: row
            for row in (pack.get("entries") or [])
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)
        }
        sources = registry.get("sources")
        quotes = quote_file.get("quotes")
        merged = pack.get("merged_into")
        # D-03: freeze = `source-pack.json` exists (or `state.sources_frozen`), never the size of the
        # snapshot — an empty snapshot is a freeze that admitted nothing, not the absence of a freeze.
        frozen = pack_path.is_file() or bool((state or {}).get("sources_frozen"))
        return cls(
            snapshot_ids=snapshot_ids,
            entries=entries,
            sources=sources if isinstance(sources, dict) else {},
            quotes=quotes if isinstance(quotes, dict) else {},
            merged=merged if isinstance(merged, dict) else {},
            frozen=frozen,
            currency_unavailable=currency_checker_unavailable(state),
        )

    def source_of_quote(self, quote_id: str) -> str | None:
        """`quote_id` -> `source_id` through `research/quotes.json` (§5.3)."""
        record = self.quotes.get(quote_id)
        if isinstance(record, dict) and isinstance(record.get("source_id"), str):
            return record["source_id"]
        return None

    def resolve(self, source_id: str) -> dict | None:
        """Citation data for a source id, or None when it is unknown or outside the snapshot.

        D-143: the draft keeps citing the ids the findings carried, so an id the freeze collapsed
        into a duplicate (`merged_into`) resolves to the entry that survived instead of to nothing.
        """
        source_id = canonical_id(self.merged, source_id)
        if self.frozen and source_id not in self.snapshot_ids:
            return None
        entry = self.entries.get(source_id)
        record = self.sources.get(source_id)
        if entry is None and not isinstance(record, dict):
            return None
        entry = entry if isinstance(entry, dict) else {}
        record = record if isinstance(record, dict) else {}
        citation_form = (
            entry.get("citation_form")
            or record.get("citation_form")
            or entry.get("title")
            or record.get("title")
            or source_id
        )
        return {
            "source_id": source_id,
            "citation_form": str(citation_form),
            "title": entry.get("title") or record.get("title"),
            "url": entry.get("url") or record.get("url"),
        }

    def view(self, source_id: str) -> dict:
        """The `oscola` citation view of a canonical id — the one both renderers cite from (D-150)."""
        return oscola.view_of(source_id, self.entries.get(source_id), self.sources.get(source_id))

    def unverified_rows(self) -> list[dict]:
        """Sources whose verification/currency/liveness belongs in the appendix (§5.5).

        When the currency checker was unavailable for the whole run the `unchecked` status says
        nothing about the individual source, so it is left to the one notice line the appendix
        prints and a source with no other problem drops out of the list entirely (D-113).
        """
        rows: list[dict] = []
        for source_id in sorted(self.sources):
            record = self.sources.get(source_id)
            if not isinstance(record, dict):
                continue
            notes: list[str] = []
            verification = record.get("verification")
            if isinstance(verification, dict) and verification.get("us") in UNVERIFIED_US:
                notes.append(f"US citation {verification['us']}")
            if isinstance(verification, dict) and verification.get("eu_syntax_ok") is False:
                notes.append("EU identifier syntax not verified")
            currency = record.get("currency")
            status = currency.get("status") if isinstance(currency, dict) else None
            if status in UNVERIFIED_CURRENCY and not (
                self.currency_unavailable and status == "unchecked"
            ):
                notes.append(f"currency {status}")
            liveness = record.get("liveness")
            if isinstance(liveness, dict) and liveness.get("status") in UNVERIFIED_LIVENESS:
                notes.append(f"link {liveness['status']}")
            if notes:
                rows.append(
                    {
                        "source_id": source_id,
                        "citation_form": record.get("citation_form") or record.get("title") or source_id,
                        "notes": notes,
                    }
                )
        return rows


def _read_object(path: Path) -> dict:
    try:
        value = state_io.read_json(path)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def line_contexts(text: str) -> tuple[list, list, list]:
    """Per line: its offset, the section it belongs to and whether it is quoted (D-152).

    The scan needs both: `ibid` may not run across a heading, and the citation of a blockquote is an
    attribution of its own that never collapses into the sentence before the quote.
    """
    starts: list[int] = []
    sections: list[int] = []
    quotes: list[bool] = []
    offset = 0
    section = 0
    for line in text.split("\n"):
        if HEADING_LINE.match(line):
            section += 1
        starts.append(offset)
        sections.append(section)
        quotes.append(bool(BLOCKQUOTE_LINE.match(line)))
        offset += len(line) + 1
    return starts, sections, quotes


def scan_mentions(text: str, index: SourceIndex, style: str | None = None) -> dict:
    """Replace every `[[src:]]`/`[[q:]]` token with a sentinel and decide its citation (D-150).

    One pass for both deliverables: the mentions are collected in document order, `oscola` numbers
    them and picks full / short / `ibid` / omitted for each, and the caller substitutes the rendered
    citation into the sentinel — after the markdown parser (docx) or after the blockquote pass (md).

    D-144: the id a mention is filed under is the **canonical** one `SourceIndex.resolve` returns,
    so an alias the freeze collapsed (`merged_into`, D-143) and the id that survived share one first
    mention, one number and one `## Sources` line.
    """
    style = style or oscola.DEFAULT_CITATION_STYLE
    mentions: list[dict] = []
    unresolved: list[str] = []
    starts, sections, quotes = line_contexts(text)

    def on_token(match: "re.Match[str]") -> str:
        source_id = match.group("sid")
        if source_id is not None:
            raw_id = source_id
            pinpoint = (match.group("pinpoint") or "").strip()
        else:
            # §5.3/§5.5: `[[q:]]` is a quote registry reference and nothing else. A quote id missing
            # from `quotes.json` stays unresolved even when a source happens to carry the same id —
            # turning it into a plain citation would drop the verbatim-quote guarantee (M6).
            raw_id = match.group("qid")
            source_id = index.source_of_quote(raw_id)
            pinpoint = ""
        resolved = index.resolve(source_id) if source_id is not None else None
        if resolved is None and raw_id not in unresolved:
            unresolved.append(raw_id)
        line = bisect.bisect_right(starts, match.start()) - 1
        mentions.append(
            {
                "raw_id": raw_id,
                "source_id": resolved["source_id"] if resolved is not None else None,
                # D-150: `assign_forms` keys the mentions by instrument, which only the citation
                # view knows; the scan is the one place that has the index anyway.
                "view": index.view(resolved["source_id"]) if resolved is not None else None,
                "pinpoint": pinpoint,
                "resolved": resolved is not None,
                # D-152: where the citation stands decides whether it may be an `ibid` at all.
                "section_id": sections[line],
                "blockquote": quotes[line],
            }
        )
        return f"{MENTION_OPEN}{len(mentions) - 1}{MENTION_CLOSE}"

    text = ANY_TOKEN.sub(on_token, text)
    instruments = oscola.assign_forms(mentions, style)
    first_of: dict[str, str] = {}
    for mention in mentions:
        if mention["resolved"]:
            first_of.setdefault(mention["instrument"], mention["source_id"])
    return {
        "text": text,
        "mentions": mentions,
        "unresolved": unresolved,
        "instruments": instruments,
        # The id each instrument was first cited under — what `## Sources` is built from (D-150).
        "cited": [first_of[key] for key in instruments if key in first_of],
        "style": style,
    }


def source_rows(scanned: dict, index: SourceIndex) -> list[dict]:
    """One `## Sources` row per cited **instrument**, numbered in citation order (§5.5, D-150).

    The registry is article-level; the annex is not. Every article of one act collapses into a
    single entry that names the act once and lists every place the memorandum cites (D-124), and
    the members travel with it so the entry can print the identifiers and the currency of the work.
    """
    rows: dict[str, dict] = {}
    for mention in scanned["mentions"]:
        if not mention["resolved"]:
            continue
        instrument = mention["instrument"]
        view = mention.get("view") or index.view(mention["source_id"])
        row = rows.get(instrument)
        if row is None:
            row = rows[instrument] = {
                "n": mention["first_n"],
                "instrument": instrument,
                "source_id": mention["source_id"],
                "source_ids": [],
                "view": view,
                "members": [],
                "citation_form": oscola.instrument_form(view),
                "url": oscola.canonical_url(view) or None,
                "pinpoints": [],
                # D-124: several mentions share one row, so it has to say whether the pinpoints it
                # carries are the ones the *first* mention cited.
                "first_pinpointed": bool(mention.get("cited_place")),
            }
        if mention["source_id"] not in row["source_ids"]:
            row["source_ids"].append(mention["source_id"])
            row["members"].append(view)
        place = mention.get("cited_place") or ""
        if place and place not in row["pinpoints"]:
            row["pinpoints"].append(place)
    ordered = [rows[key] for key in scanned["instruments"] if key in rows]
    for row in ordered:
        row["pinpoints_text"] = pinpoint_text(row)
    return ordered


def mention_markdown(mention: dict, index: SourceIndex, style: str) -> str:
    """One rendered citation of the markdown deliverable (D-150).

    The markdown has no footnote apparatus, so in the footnote style its marker is the **source's**
    number — `[1] … [2] … [1]` — and the `## Sources` list is the footnote list. `renderer.py` uses
    `mention["n"]` instead: there one Word footnote is written per mention (§5.5).
    """
    if not mention["resolved"]:
        return oscola.unresolved_text(mention["raw_id"])
    if style == oscola.STYLE_FOOTNOTES:
        return f"[{mention['first_n']}]"
    if mention["form"] == oscola.FORM_OMITTED:
        return ""
    view = index.view(mention["source_id"])
    text = oscola.mention_text(view, mention, style)
    url = oscola.anchor_url(view, mention["pinpoint"])
    return f"([{text}]({url}))" if url else f"({text})"


def drop_omitted(text: str, mentions: list[dict]) -> str:
    """Delete the sentinel of a citation the style omits, together with the space before it (D-150).

    Both renderers run this before they write anything, so neither leaves ` .` where the second of
    two identical adjacent citations used to be.
    """

    def on_sentinel(match: "re.Match[str]") -> str:
        mention = mentions[int(match.group(1))]
        return "" if mention.get("form") == oscola.FORM_OMITTED else match.group(0)

    return SPACED_MENTION_RE.sub(on_sentinel, text)


def render_mentions(text: str, scanned: dict, index: SourceIndex) -> str:
    """Substitute every sentinel left in the text with its rendered citation."""
    style = scanned["style"]

    def on_sentinel(match: "re.Match[str]") -> str:
        return mention_markdown(scanned["mentions"][int(match.group(1))], index, style)

    return MENTION_RE.sub(on_sentinel, text)


def replace_tokens(text: str, index: SourceIndex, style: str | None = None) -> dict:
    """Scan, number and render every citation token of one body in one call (§5.5, D-150)."""
    scanned = scan_mentions(text, index, style)
    rows = source_rows(scanned, index)
    scanned["text"] = drop_omitted(scanned["text"], scanned["mentions"])
    return {
        "text": render_mentions(scanned["text"], scanned, index),
        "scanned": scanned,
        "footnotes": rows,
        "unresolved": scanned["unresolved"],
    }


def pinpoint_text(row: dict) -> str:
    """The `cited at …` field of one `## Sources` line, first mention first (D34-22, D-124, D-150).

    A row carries every mention of its instrument, so the annex prints every place the memorandum
    cites, in order (`cited at art 3(2)(a), art 6, art 44`), instead of only the last one. When the
    first mention cited the work whole — a blockquote `[[q:]]` on a source that is not article-level
    has no pinpoint — the later ones are labelled as such, so a reader coming from that first
    citation is not sent to another article.
    """
    pins = [str(pin).strip() for pin in (row.get("pinpoints") or []) if str(pin).strip()]
    if not pins:
        return ""
    prefix = PINPOINT_FIRST_PREFIX if row.get("first_pinpointed") else PINPOINT_LATER_PREFIX
    return prefix + PINPOINT_SEPARATOR.join(pins)


def attribute_blockquotes(text: str, mentions: list[dict]) -> str:
    """Move the citations of every blockquote into an attribution line under it (D34-22, D-124).

    Inside the quotation `> [2] (b) processing is necessary …` reads as part of the quoted rule;
    under it, `— [2]` — or `— (Schrems II, para 2)` in the inline style — reads as what it is. The
    sentinels keep their document order, so the numbering `scan_mentions` assigned is untouched, and
    an unresolved id stays where it is: it is not a citation.
    """
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        cited: list[str] = []

        def take(match: "re.Match[str]") -> str:
            if not mentions[int(match.group(1))]["resolved"]:
                return match.group(0)
            cited.append(match.group(1))
            return ""

        out.extend(re.sub(r"[ \t]*" + MENTION_RE.pattern, take, line).rstrip() for line in block)
        if cited:
            # A markdown line touching the quote would be a lazy continuation of it, so the
            # attribution needs the blank line in front.
            out.append("")
            markers = " ".join(
                f"{MENTION_OPEN}{number}{MENTION_CLOSE}" for number in dict.fromkeys(cited)
            )
            out.append(ATTRIBUTION_PREFIX + markers)
        block.clear()

    for line in text.split("\n"):
        if BLOCKQUOTE_LINE.match(line):
            block.append(line)
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


def sources_line(row: dict) -> str:
    """`[n] <full record>` — the annex line of one cited instrument (§5.5, D-150)."""
    return f"[{row['n']}] {oscola.sources_entry(row)}"


def render_sources_section(rows: list[dict]) -> str:
    """`## Sources` block: the full record of every cited source, in citation order (D-150)."""
    if not rows:
        return f"{SOURCES_HEADING}\n\n_No sources were cited in this draft._\n"
    lines = [SOURCES_HEADING, ""]
    lines.extend(sources_line(row) for row in rows)
    lines.append("")
    return "\n".join(lines)


def render_appendix(
    warnings: list,
    unverified: list[dict],
    unresolved: list[str],
    *,
    currency_unavailable: bool = False,
) -> str:
    """«Assumptions & Unverified Sources» appendix (§5.5); empty string when there is nothing to say.

    The client form of D-113: one condensed bullet per warning, capped, and a single currency
    notice instead of «currency unchecked» under every source.
    """
    bullets = assumption_bullets(warnings)
    if not bullets and not unverified and not unresolved and not currency_unavailable:
        return ""
    lines = [APPENDIX_HEADING, ""]
    if bullets:
        lines.append(ASSUMPTIONS_MD)
        lines.append("")
        for bullet in bullets:
            lines.append(f"- {bullet}")
        lines.append("")
    if unverified or currency_unavailable:
        lines.append(UNVERIFIED_MD)
        lines.append("")
        if currency_unavailable:
            lines.append(f"- {CURRENCY_UNAVAILABLE_NOTE}")
        for row in unverified:
            lines.append(f"- {unverified_line(row)}")
        lines.append("")
    if unresolved:
        lines.append(UNRESOLVED_MD)
        lines.append("")
        for raw_id in unresolved:
            lines.append(
                f"- `{raw_id}` — not in the frozen source pack or the quote registry; "
                f"marked `[unresolved: {raw_id}]` in the text."
            )
        lines.append("")
    return "\n".join(lines)


def warning_text(warning: object) -> str:
    """One `drafting_warnings[]` entry (string or {code,message}) verbatim, tag included.

    The long form, for `summary.md` — the report of the run. The appendix of the deliverable uses
    `assumption_bullet` instead (D-113).
    """
    if isinstance(warning, dict):
        message = warning.get("message") or warning.get("code") or ""
        code = warning.get("code")
        if code and message and code != message:
            return f"{message} (`{code}`)"
        return str(message or code or warning)
    return str(warning)


def render(
    draft_text: str,
    index: SourceIndex,
    *,
    drafting_warnings: list | None = None,
    state: dict | None = None,
    citation_style: str | None = None,
) -> dict:
    """Render one draft into the fallback deliverable; pure function over its inputs."""
    style = oscola.normalise_style(citation_style) or oscola.DEFAULT_CITATION_STYLE
    body = SOURCES_MARKER.sub("", draft_text).rstrip() + "\n"
    scanned = scan_mentions(body, index, style)
    rows = source_rows(scanned, index)
    body_text = attribute_blockquotes(
        drop_omitted(scanned["text"], scanned["mentions"]), scanned["mentions"]
    )
    text = render_mentions(body_text, scanned, index)
    replaced = {"footnotes": rows, "unresolved": scanned["unresolved"]}

    banners = []
    if replaced["unresolved"]:
        banners.append(fallbacks.banner("unresolved_reference_in_fallback"))

    parts = [text.rstrip() + "\n", "", render_sources_section(rows)]
    # D34-11: `## Status` sits between the memo and its appendix — the banners and the blockers the
    # run left open have to reach the client, not only `state.json`.
    status = render_status(status_inputs(state, banners))
    if status:
        parts.extend(["", status])
    appendix = render_appendix(
        list(drafting_warnings or []),
        index.unverified_rows(),
        replaced["unresolved"],
        currency_unavailable=index.currency_unavailable,
    )
    if appendix:
        parts.extend(["", appendix])

    return {
        "markdown": "\n".join(part for part in parts if part is not None).rstrip() + "\n",
        "footnotes": replaced["footnotes"],
        "unresolved": replaced["unresolved"],
        "banners": banners,
        "citation_style": style,
    }


def render_workdir(work_dir: str | Path, draft_path: str | Path, *, state: dict | None = None) -> dict:
    """Render `draft_path` using the registry files of `work_dir` and the state's warnings."""
    text = Path(draft_path).read_text(encoding="utf-8-sig")
    index = SourceIndex.load(work_dir, state=state)
    warnings = (state or {}).get("drafting_warnings") or []
    return render(
        text,
        index,
        drafting_warnings=warnings,
        state=state,
        citation_style=oscola.resolve_style(state),
    )
