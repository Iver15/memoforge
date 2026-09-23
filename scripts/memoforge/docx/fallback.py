"""Stdlib-only markdown fallback of the export (ТЗ §5.5: «Fallback (stdlib-only, без AST)»).

`[[src:<id> pinpoint]]` and `[[q:<quote_id>]]` become a citation — a `(compact form)` linked to the
source in the inline style, a `[n]` marker in the footnote style (D-150) — the `## Sources` annex is
built from the frozen `research/source-pack.json` snapshot plus `research/sources.json` and carries
the full record of every cited source, an id that resolves in neither becomes `[unresolved: <id>]`
and raises the `unresolved_reference` banner, a `## Status` section states the banners and the
unresolved blockers of a run that did not end approved (D34-11) and the «Unverified Sources»
appendix is generated from the per-source `verification`/`currency`/`liveness` records — never
from `drafting_warnings`, whose home is the facts section (D-191). No `python-docx`, no `mistune`,
no network.

The scan of the citation tokens and the numbering of the mentions live here and are shared with
`renderer.py`: both deliverables must decide every citation the same way (D-150).
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from .. import fallbacks, i18n, state_io
from ..sources import RAW_KINDS, TEXT_OUTCOME_KEY, canonical_id
from . import oscola

SRC_TOKEN = re.compile(r"\[\[src:\s*(?P<id>[^\]\s]+)(?P<pinpoint>[^\]]*)\]\]")
QUOTE_TOKEN = re.compile(r"\[\[q:\s*(?P<id>[^\]\s]+)\s*\]\]")
ANY_TOKEN = re.compile(
    r"\[\[(?:src:\s*(?P<sid>[^\]\s]+)(?P<pinpoint>[^\]]*)|q:\s*(?P<qid>[^\]\s]+)\s*)\]\]"
)
"""One pass over both token kinds so footnote numbers follow first mention in document order (§5.5)."""
SOURCES_MARKER = re.compile(r"^<!--\s*sources:\s*generated\s*-->\s*$", re.MULTILINE)

# --- the words of the deliverable (D-175) ----------------------------------


def memo_language(state: dict | None) -> str:
    """The memo language of a task; anything unusable is English (D-169, D-175)."""
    return i18n.normalize((state or {}).get("language")) or i18n.DEFAULT


def label(key: str, language: str = i18n.DEFAULT, **fmt) -> str:
    """One `memo.labels` string of the memo language, rendered with `**fmt` (D-175).

    The single door both deliverables go through: markdown and docx print the same words and
    differ only in the decoration they wrap them in.
    """
    return i18n.t(i18n.normalize(language) or i18n.DEFAULT, f"memo.labels.{key}", **fmt)


def sources_heading(language: str = i18n.DEFAULT) -> str:
    """`## Sources` of the markdown deliverable; the docx prints the label as a bold paragraph."""
    return f"## {label('sources_heading', language)}"


def appendix_heading(language: str = i18n.DEFAULT) -> str:
    """The appendix heading of the markdown deliverable; `finalize` partitions on it (D-113)."""
    return f"## {label('appendix_heading', language)}"


def status_heading(language: str = i18n.DEFAULT) -> str:
    """The `## Status` heading; the docx prints the same label bold, so both carry one word (D34-11)."""
    return f"## {label('status_label', language)}"


def status_lead_prefix(language: str = i18n.DEFAULT) -> str:
    """The fixed opening of the Status lead, before its first placeholder (D-175).

    What tells a Status section this pipeline wrote from a heading of the same name inside the
    memorandum itself. A lead that opens with its placeholder has no fixed opening at all, and the
    caller must then read the section back by its heading alone rather than match on nothing.
    """
    return label("status_lead", language).split("{", 1)[0]


# --- citations in the body and the annex -----------------------------------

PINPOINT_SEPARATOR = ", "
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

SAVED_TEXT_TIERS: tuple[str, ...] = ("critical", "supporting")
"""Tiers whose saved text the appendix speaks about (D-156, D-158, D-204) — `citations.RAW_TEXT_TIERS`.

D-204 (controller's ruling): the kind lines follow `no_saved_text_note`, not the extended C-08 —
C-08 is a grade and stays `critical`; the appendix is disclosure, and a `supporting` source that is
an agent's copy is as much something the client should be told."""

KIND_NOTES: dict[str, str] = {"excerpt": "excerpt_note", "agent_summary": "agent_summary_note"}
"""D-204: the `memo.labels` line of a cited source whose pack entry declares a text that is not the
whole document. `full_text` and `client_file` print nothing; `none` is said by the snapshot
(`no_saved_text_note` / `pdf_unverified_note`)."""

NO_REASONING_OUTCOME = "excerpt:no_reasoning"
"""D-203/D-204: the outcome of a short act published without its reasoning — the appendix calls it
that, never «an excerpt» of a longer text that does not exist. Final review E: read from
`meta.text_outcome` (`sources.TEXT_OUTCOME_KEY`), the outcome of the text the record holds, never
from `meta.save_outcome`, which is the last attempt and moves when an attempt publishes nothing."""

# --- the appendix is written for the client (D-113, D-191) ------------------------

APPENDIX_WARNING_LIMIT = 12
"""How many condensed warning bullets `fallback-summary.md` carries before pointing at `summary.md` (D-113)."""

APPENDIX_WARNING_CHARS = 240
"""Hard cap per bullet, ellipsis included (D-113)."""

CURRENCY_UNAVAILABLE_BANNER = "currency_unavailable"
"""The `currency_checker_failed` row of `fallbacks.py`: the checker was down for the *whole* run."""

UNRESOLVED_MARKER = re.compile(r"\[unresolved:\s*([^\]]+)\]")
"""The marker left in a rendered body; lets a re-read of the deliverable recover the ids (D-113)."""

# --- the status of a run that did not end approved (D34-11) ----------------

APPROVED_STATUS_PREFIXES: tuple[str, ...] = ("approved", "client_ready")
"""`final_status` prefixes that mean the pipeline signed the memo off (§2.1 row 15)."""

STATUS_ISSUE_LIMIT = 12
"""How many `remaining_blocking_issues[]` rows the deliverable prints before pointing at the summary."""

STATUS_ISSUE_SEPARATOR = " · "
"""`severity · section_id · issue` — the shape of one blocker line (D34-11)."""

COPY_BANNERS: frozenset = frozenset({"publish_failed", "output_folder_unavailable", "mcp_soft_cap_exceeded"})
"""Banners kept out of the `## Status` section and `status_signature` (D-144, D-166 fix wave).

`publish_failed` and `output_folder_unavailable` are about the *copy* of the finished result,
not about the memorandum itself. `mcp_soft_cap_exceeded` is telemetry about MCP calls, likewise
not about the memorandum — and it is raised after the deliverable is chosen, so listing it in
the section (or the signature) could never be written into an already exported docx and would
make a perfectly current export stale. All three stay in `summary.md`, which is re-rendered
after the copy (D-109, D-111)."""

_WARNING_ID_TAG = re.compile(r"\s*\((?:`[^`()]+`|[a-z0-9]+(?:_[a-z0-9]+)+)\)")
"""The `(warning_id)` tag `warning_text` appends — machine talk, not client text."""

_INTERNAL_FILE = re.compile(r"[a-z_\-/]+\.json")
"""`research/doctrine.json`, `statutes.json`: protocol files the reader has no access to."""

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[^a-z0-9]|[a-z_\-/]+\.json)")
"""Sentence boundary that survives `s.26`, `no.6` and `e.g.` (the next word stays lowercase there).

A sentence opening with a protocol file — `… no layer's sources. research/doctrine.json states …` —
is a boundary as well, otherwise dropping the file name would glue the two sentences together.
"""


def assumption_bullets(warnings: list, language: str = i18n.DEFAULT) -> list[str]:
    """One short bullet per `drafting_warnings[]` entry for the fallback summary (D-113, D-191).

    D-191 removed the assumptions group from the deliverable appendix, so this is the only
    condensed client form left: `finalize.build_fallback_summary` prints it into
    `fallback-summary.md`, which is published when no docx, draft or prior export exists.
    The warnings are the sufficiency reviewer's prose *addressed to the writer* — the client
    gets the first sentence of each, without the `(warning_id)` tag and without the names of
    protocol files; `summary.md` keeps every warning verbatim, and the last bullet says so.
    """
    bullets: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        text = assumption_bullet(warning, language=language)
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
    return bullets[:APPENDIX_WARNING_LIMIT] + [label("appendix_more", language, count=rest)]


def _sentence_spans_abbreviations(language: str) -> set[str]:
    """The abbreviation stems of the memo language, case-folded and without the period."""
    try:
        words = i18n.node(i18n.normalize(language) or i18n.DEFAULT, "memo.abbreviations")
    except (KeyError, i18n.PackUnavailable):
        return set()
    if not isinstance(words, list):
        return set()
    return {str(word).rstrip(".").casefold() for word in words if str(word).strip()}


def _first_sentence(raw: str, language: str) -> str:
    """The opening sentence, never cut right after an abbreviation of the memo language.

    English keeps exactly the pre-task code path (the plain `_SENTENCE_END` split) — the frozen
    output never consulted an abbreviation list. The abbreviation-aware cut applies only to the
    other languages.
    """
    code = i18n.normalize(language) or i18n.DEFAULT
    if code == i18n.DEFAULT:
        return _SENTENCE_END.split(raw, 1)[0]
    short = _sentence_spans_abbreviations(code)
    if not short:
        return _SENTENCE_END.split(raw, 1)[0]
    for match in _SENTENCE_END.finditer(raw):
        boundary = match.start()
        stem = raw[:boundary].rstrip()
        word = stem.split()[-1].rstrip(".") if stem.split() else ""
        if word.casefold() not in short:
            return raw[:boundary]
    return raw


def assumption_bullet(warning: object, language: str = i18n.DEFAULT) -> str:
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
    sentence = _first_sentence(raw, language)
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


STATUS_VERSION_RE = re.compile(r"_(?:on_)?v(\d+)")
"""D-197: the draft version inside a `final_status`, wherever the code puts it.

`approved_on_v1` and `approved_v3` carry it at the end; `forced_exit_on_v2_with_remaining_issues`
carries it in the middle. What is left after cutting it out is the status FAMILY, which is what a
pack names.
"""

SECTION_ID_RE = re.compile(r"^s-(\d+(?:-\d+)*)$")
"""`s-5-1` -> section 5.1; anything else is not a numbered section anchor."""

WHOLE_MEMO_IDS: frozenset = frozenset({"general", "document"})
"""The `section_id` values a reviewer uses for a finding that belongs to no section (§4.2)."""


def status_family(final_status: object) -> tuple[str, str]:
    """`forced_exit_on_v2_with_remaining_issues` -> `("forced_exit_with_remaining_issues", "2")`."""
    text = str(final_status or "").strip()
    match = STATUS_VERSION_RE.search(text)
    if match is None:
        return text, ""
    return (text[: match.start()] + text[match.end():]).strip("_"), match.group(1)


def status_name(final_status: object, language: str = i18n.DEFAULT) -> str:
    """`final_status` as a sentence in the memo language; an unknown family stays raw (D-197).

    The reader of a memorandum is not the reader of `state.json`: `approved_on_v1` is a record,
    «approved on version 1» is the sentence. A family no pack knows falls back to the code itself,
    because a status the pipeline grows tomorrow must not stop the export.
    """
    raw = str(final_status or "").strip()
    if not raw:
        return ""
    family, version = status_family(raw)
    sentence = oscola.token_name("status_names", family, language)
    if sentence == family:
        return raw  # unknown family: the code itself, never a half-translated sentence
    if "{version}" in sentence and not version:
        return raw  # a versioned family without a version would print «on version », which is worse
    return sentence.format(version=version) if "{version}" in sentence else sentence


def reason_name(reason: object, language: str = i18n.DEFAULT) -> str:
    """One `final_status_reasons[]` code as a sentence; free text and unknown codes stay (D-197)."""
    return oscola.token_name("status_reasons", reason, language)


def severity_name(severity: object, language: str = i18n.DEFAULT) -> str:
    """`blocker` / `major` / `minor` / `info` in the memo language (D-197)."""
    return oscola.token_name("severity", severity, language)


def section_label(section_id: object, language: str = i18n.DEFAULT) -> str:
    """`s-5-1` -> «section 5.1», `general`/`document` -> the pack's word for the whole memo (D-197).

    A `section_id` the convention does not cover (`s-title`) is printed as it stands: inventing a
    name for it would be worse than showing the anchor the reviewer used.
    """
    text = str(section_id or "").strip()
    if not text:
        return ""
    if text.lower() in WHOLE_MEMO_IDS:
        return label("whole_memo", language)
    match = SECTION_ID_RE.match(text)
    if match is None:
        return text
    return f"{label('section_word', language)} {match.group(1).replace('-', '.')}"


def blocking_issue_line(issue: object, language: str | None = None) -> str:
    """One `severity · section_id · issue` row of `state.remaining_blocking_issues` (D34-11).

    D-173a: a finding that carries its client-facing sentence prints that instead of `issue`.

    D-197: with a `language` the severity and the section anchor are printed in words — that is the
    row the deliverable carries. Without one the raw ids stand, which is what `summary.md` keeps:
    the technical record of the run has to stay greppable against `state.json`.
    """
    if not isinstance(issue, dict):
        return re.sub(r"\s+", " ", str(issue or "")).strip()
    text = str(issue.get("issue_client") or issue.get("issue") or issue.get("category") or "")
    severity = str(issue.get("severity") or "").strip()
    section_id = str(issue.get("section_id") or "").strip()
    if language is not None:
        severity = severity_name(severity, language)
        section_id = section_label(section_id, language)
    parts = [severity, section_id, re.sub(r"\s+", " ", text).strip()]
    return STATUS_ISSUE_SEPARATOR.join(part for part in parts if part)


def blocking_issue_lines(issues: list, language: str = i18n.DEFAULT) -> list[str]:
    """The blocker rows of the deliverable: capped, the rest pointed at `summary.md` (D34-11)."""
    rows = [line for line in (blocking_issue_line(issue, language) for issue in issues or ()) if line]
    if len(rows) <= STATUS_ISSUE_LIMIT:
        return rows
    rest = len(rows) - STATUS_ISSUE_LIMIT
    return rows[:STATUS_ISSUE_LIMIT] + [label("appendix_more", language, count=rest)]


def status_inputs(state: dict | None, extra_banners: list | None = None) -> dict:
    """What the `## Status` section says, out of state plus the banners this render raised (D34-11).

    Both rendered forms read the same dict, so `deliverable.md` and `deliverable.docx` cannot
    disagree about why a run needs a human. `required` is the invariant `docx validate` enforces:
    a `final_status` that is not an approved one must be visible in the document itself.

    D-144: this dict *is* the section, so it is also what `docx.status_signature` hashes — the ids
    next to their texts, and the rendered blocker lines, not only the ids. `COPY_BANNERS` are left
    out: they describe the copy of the result, not the memorandum.

    D-175: the memo language travels with it, so both rendered forms print the section in the
    language the run was asked for and neither has to be told a second time.
    """
    state = state or {}
    language = memo_language(state)
    final_status = str(state.get("final_status") or "")
    ids: list[str] = []
    texts: list[str] = []
    seen: set[str] = set()
    for row in list(state.get("fallback_banners") or []) + list(extra_banners or []):
        if isinstance(row, dict):
            key = str(row.get("banner_id"))
            text = fallbacks.banner_text_for(row, language)
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
        "language": language,
        "banner_ids": ids,
        "banners": texts,
        "issues": blocking_issue_lines(state.get("remaining_blocking_issues") or [], language),
    }


def render_status(inputs: dict) -> str:
    """`## Status` block of the markdown deliverable; empty string when the run ended approved."""
    if not inputs.get("required"):
        return ""
    language = inputs.get("language") or i18n.DEFAULT
    lines = [
        status_heading(language),
        "",
        label("status_lead", language, final_status=status_name(inputs["final_status"], language)),
        "",
    ]
    if inputs["banners"]:
        lines.extend([f"**{label('status_banners_label', language)}**", ""])
        lines.extend(f"- {row}" for row in inputs["banners"])
        lines.append("")
    if inputs["issues"]:
        lines.extend([f"**{label('status_issues_label', language)}**", ""])
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
        snapshot_hashes: dict | None = None,
        entries: dict | None = None,
        sources: dict | None = None,
        quotes: dict | None = None,
        merged: dict | None = None,
        frozen: bool = False,
        currency_unavailable: bool = False,
        snapshot_originals: dict | None = None,
        pinpoints_not_in_raw: dict | None = None,
    ) -> None:
        self.snapshot_ids = list(snapshot_ids or [])
        self.snapshot_hashes = dict(snapshot_hashes or {})
        """D-158: `snapshot[].raw_sha256` of the freeze — the text C-08 checked the citation against."""
        self.snapshot_originals = {sid: sha for sid, sha in (snapshot_originals or {}).items() if sha}
        """D-201/D-204: `snapshot[].raw_original_sha256` — the PDF originals the freeze pinned."""
        self.entries = dict(entries or {})
        self.sources = dict(sources or {})
        self.quotes = dict(quotes or {})
        self.merged = {str(alias): str(target) for alias, target in (merged or {}).items() if target}
        """D-143: `merged_into` of the freeze — the aliases a draft may still cite by name."""
        self.frozen = frozen
        self.currency_unavailable = currency_unavailable
        """D-113: the whole run went unchecked, so «currency unchecked» is one line, not one per source."""
        self.pinpoints_not_in_raw = {
            str(source_id): list(pins) for source_id, pins in (pinpoints_not_in_raw or {}).items() if pins
        }
        """D-204: canonical id -> the pinpoints of the C-09 findings of the draft being rendered."""

    @classmethod
    def load(
        cls,
        work_dir: str | Path,
        *,
        state: dict | None = None,
        pinpoint_findings: list | None = None,
    ) -> "SourceIndex":
        """Read `research/{source-pack,sources,quotes}.json`; every file is optional.

        D-204: `pinpoint_findings` are the C-09 findings of the draft being rendered, computed by the
        caller that chose it (`docx.exported_pinpoints`) — the renderer never runs the audit (D-195) —
        and filed here under the canonical id of the source each one names.
        """
        research = Path(work_dir) / "research"
        pack_path = research / "source-pack.json"
        pack = _read_object(pack_path)
        registry = _read_object(research / "sources.json")
        quote_file = _read_object(research / "quotes.json")

        snapshot_rows = [
            row
            for row in (pack.get("snapshot") or [])
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)
        ]
        snapshot_ids = [row["source_id"] for row in snapshot_rows]
        snapshot_hashes = {row["source_id"]: row.get("raw_sha256") for row in snapshot_rows}
        snapshot_originals = {row["source_id"]: row.get("raw_original_sha256") for row in snapshot_rows}
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
        merged = merged if isinstance(merged, dict) else {}
        return cls(
            snapshot_ids=snapshot_ids,
            snapshot_hashes=snapshot_hashes,
            snapshot_originals=snapshot_originals,
            entries=entries,
            sources=sources if isinstance(sources, dict) else {},
            quotes=quotes if isinstance(quotes, dict) else {},
            merged=merged,
            frozen=frozen,
            currency_unavailable=currency_checker_unavailable(state),
            pinpoints_not_in_raw=pinpoints_by_source(pinpoint_findings, merged),
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

    def unverified_rows(
        self, language: str = i18n.DEFAULT, cited: set | None = None
    ) -> list[dict]:
        """Sources whose verification/currency/liveness belongs in the appendix (§5.5).

        When the currency checker was unavailable for the whole run the `unchecked` status says
        nothing about the individual source, so it is left to the one notice line the appendix
        prints and a source with no other problem drops out of the list entirely (D-113).

        D-175: the note is a label around the recorded status.

        D-197: `cited` is the set of source ids the memorandum actually names. A registry record no
        `[[src:]]` token cites — a placeholder the research left behind — is not a source of this
        memorandum and is not disclosed as one. `None` means the caller does not know the citations
        and every unverified record is listed, which is what it always did. The recorded currency
        and liveness tokens are printed through `memo.currency_names` / `memo.link_names`.

        D-204: the appendix also says what the saved text is (`text_note`), and every C-09 of the
        rendered draft that survived the lint-fix round is a line of its source, one per pinpoint.
        A `client_file` with `unchecked` currency is not an unverified source: its currency line is
        not printed, so on its own it prints nothing at all.
        """
        rows: list[dict] = []
        for source_id in sorted(self.sources):
            record = self.sources.get(source_id)
            if not isinstance(record, dict):
                continue
            if cited is not None and source_id not in cited:
                continue
            kind = self.declared_kind(source_id)
            notes: list[str] = []
            verification = record.get("verification")
            if isinstance(verification, dict) and verification.get("us") in UNVERIFIED_US:
                notes.append(label("us_citation_note", language, status=verification["us"]))
            if isinstance(verification, dict) and verification.get("eu_syntax_ok") is False:
                notes.append(label("eu_syntax_note", language))
            currency = record.get("currency")
            status = currency.get("status") if isinstance(currency, dict) else None
            if (
                status in UNVERIFIED_CURRENCY
                and not (self.currency_unavailable and status == "unchecked")
                and not (kind == "client_file" and status == "unchecked")
            ):
                notes.append(
                    label("currency_note", language, status=oscola.currency_name(status, language))
                )
            text_note = self.text_note(source_id, record, kind)
            if text_note:
                notes.append(label(text_note, language))
            for pinpoint in self.pinpoints_not_in_raw.get(source_id, ()):
                notes.append(
                    label(
                        "pinpoint_not_in_raw_note",
                        language,
                        pinpoint=oscola.display_pinpoint(oscola.normalise_pinpoint(pinpoint), language),
                        source_id=source_id,
                    )
                )
            liveness = record.get("liveness")
            if isinstance(liveness, dict) and liveness.get("status") in UNVERIFIED_LIVENESS:
                notes.append(
                    label(
                        "link_note",
                        language,
                        status=oscola.link_name(liveness["status"], language),
                    )
                )
            if notes:
                rows.append(
                    {
                        "source_id": source_id,
                        "citation_form": record.get("citation_form") or record.get("title") or source_id,
                        "notes": notes,
                    }
                )
        return rows

    def declared_kind(self, source_id: str) -> str | None:
        """The `raw_kind` the pack entry declares, or None when it declares none (D-200, D-204).

        After the freeze the pack decides, not the registry. An entry frozen before the field
        existed declares nothing: it draws no extended C-08, so it has no line to go with one.
        """
        entry = self.entries.get(source_id)
        kind = entry.get("raw_kind") if isinstance(entry, dict) else None
        return kind if kind in RAW_KINDS else None

    def text_note(self, source_id: str, record: dict, kind: str | None) -> str | None:
        """The `memo.labels` key that says what the saved text of a packed source is, or None.

        D-158: the frozen snapshot decides, exactly as it does for C-08 — the registry may still
        carry the hash of a raw file that was gone by the time the freeze ran. Controller's addendum
        §1 (D-201): a snapshot row with an original and no text digest is a PDF nobody could read —
        its original was saved and pinned, so it prints `pdf_unverified_note` **instead of**
        `no_saved_text_note`, never both. D-204: a text the snapshot holds is judged by its kind — an
        excerpt or an agent copy says so, for the same `critical`/`supporting` sources whose missing
        text is named (the extended C-08 grades `critical` only; the appendix discloses), and a short
        act without reasoning says that instead of «an excerpt».
        """
        if source_id not in self.entries:
            return None
        tier = str(record.get("tier") or "")
        if tier not in SAVED_TEXT_TIERS:
            return None
        if self.snapshot_hashes.get(source_id) is None:
            return "pdf_unverified_note" if self.snapshot_originals.get(source_id) else "no_saved_text_note"
        if kind not in KIND_NOTES:
            return None
        meta = record.get("meta")
        if kind == "excerpt" and isinstance(meta, dict) and meta.get(TEXT_OUTCOME_KEY) == NO_REASONING_OUTCOME:
            return "no_reasoning_note"
        return KIND_NOTES[kind]


def _read_object(path: Path) -> dict:
    try:
        value = state_io.read_json(path)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def pinpoints_by_source(findings: list | None, merged: dict | None = None) -> dict[str, list[str]]:
    """Canonical id -> the distinct pinpoints of the C-09 findings handed in, in order (D-204).

    A finding names the id its `[[src:]]` token carried; an alias the freeze collapsed is filed under
    the source that survived it (D-143), whose row the appendix prints. Rows without a `source_id`
    and a `pinpoint` are not C-09 findings and are ignored.
    """
    found: dict[str, list[str]] = {}
    for row in findings or []:
        if not isinstance(row, dict):
            continue
        source_id, pinpoint = row.get("source_id"), row.get("pinpoint")
        if not isinstance(source_id, str) or not isinstance(pinpoint, str) or not pinpoint.strip():
            continue
        pins = found.setdefault(canonical_id(merged or {}, source_id), [])
        if pinpoint not in pins:
            pins.append(pinpoint)
    return found


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


def cited_source_ids(mentions: list) -> set:
    """The canonical ids of every source the draft cites, quotes included (D-197).

    What the appendix is allowed to disclose: a registry record no `[[src:]]` or `[[q:]]` token
    names is not a source of this memorandum.
    """
    return {
        mention["source_id"]
        for mention in mentions or ()
        if isinstance(mention, dict) and mention.get("resolved") and mention.get("source_id")
    }


def source_rows(scanned: dict, index: SourceIndex, language: str = i18n.DEFAULT) -> list[dict]:
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
        row["pinpoints_text"] = pinpoint_text(row, language)
    return ordered


def mention_markdown(
    mention: dict, index: SourceIndex, style: str, language: str = i18n.DEFAULT
) -> str:
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
    text = oscola.mention_text(view, mention, style, language)
    # D-175a: the link is built from the canonical pinpoint the token carried, never from its
    # display form — the anchor of a consolidated act is `#art_6` in every language.
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


def render_mentions(
    text: str, scanned: dict, index: SourceIndex, language: str = i18n.DEFAULT
) -> str:
    """Substitute every sentinel left in the text with its rendered citation."""
    style = scanned["style"]

    def on_sentinel(match: "re.Match[str]") -> str:
        return mention_markdown(scanned["mentions"][int(match.group(1))], index, style, language)

    return MENTION_RE.sub(on_sentinel, text)


def replace_tokens(
    text: str, index: SourceIndex, style: str | None = None, language: str = i18n.DEFAULT
) -> dict:
    """Scan, number and render every citation token of one body in one call (§5.5, D-150)."""
    scanned = scan_mentions(text, index, style)
    rows = source_rows(scanned, index, language)
    scanned["text"] = drop_omitted(scanned["text"], scanned["mentions"])
    return {
        "text": render_mentions(scanned["text"], scanned, index, language),
        "scanned": scanned,
        "footnotes": rows,
        "unresolved": scanned["unresolved"],
    }


def pinpoint_text(row: dict, language: str = i18n.DEFAULT) -> str:
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
    key = "cited_at" if row.get("first_pinpointed") else "also_cited_at"
    prefix = oscola.citation_word(key, language)
    return prefix + PINPOINT_SEPARATOR.join(
        oscola.display_pinpoint(pin, language) for pin in pins
    )


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


def sources_line(row: dict, language: str = i18n.DEFAULT) -> str:
    """`[n] <full record>` — the annex line of one cited instrument (§5.5, D-150)."""
    return f"[{row['n']}] {oscola.sources_entry(row, language)}"


def render_sources_section(rows: list[dict], language: str = i18n.DEFAULT) -> str:
    """`## Sources` block: the full record of every cited source, in citation order (D-150)."""
    heading = sources_heading(language)
    if not rows:
        return f"{heading}\n\n_{label('no_sources_cited', language)}_\n"
    lines = [heading, ""]
    lines.extend(sources_line(row, language) for row in rows)
    lines.append("")
    return "\n".join(lines)


def render_appendix(
    warnings: list,
    unverified: list[dict],
    unresolved: list[str],
    *,
    currency_unavailable: bool = False,
    language: str = i18n.DEFAULT,
) -> str:
    """«Unverified Sources» appendix (§5.5, D-191); empty string when there is nothing to say.

    D-191: the condensed `drafting_warnings` bullets repeated what the writer already placed in
    the facts section — the appendix opens only for unverified sources, the currency-unavailable
    note or unresolved markers. `summary.md` still keeps every warning verbatim (`warning_text`).
    """
    _ = warnings
    if not unverified and not unresolved and not currency_unavailable:
        return ""
    lines = [appendix_heading(language), ""]
    if unverified or currency_unavailable:
        if unresolved:
            # D-216: the group label only tells two groups apart; alone it repeats the heading.
            lines.append(f"**{label('unverified_label', language)}**")
            lines.append("")
        if currency_unavailable:
            lines.append(f"- {label('currency_unavailable_note', language)}")
        for row in unverified:
            lines.append(f"- {unverified_line(row)}")
        lines.append("")
    if unresolved:
        lines.append(f"**{label('unresolved_label', language)}**")
        lines.append("")
        for raw_id in unresolved:
            # The markdown sets the id and the marker in code spans; the docx prints them plain.
            lines.append(
                "- "
                + label(
                    "unresolved_bullet",
                    language,
                    raw_id=f"`{raw_id}`",
                    marker=f"`{oscola.unresolved_text(raw_id)}`",
                )
            )
        lines.append("")
    return "\n".join(lines)


def warning_text(warning: object) -> str:
    """One `drafting_warnings[]` entry (string or {code,message}) verbatim, tag included.

    The long form, for `summary.md` — the report of the run. The deliverable appendix carries no
    warnings at all (D-191); `summary.md` is its only reader — `fallback-summary.md` prints the
    condensed `assumption_bullets` form instead.
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
    """Render one draft into the fallback deliverable; pure function over its inputs.

    D-175: the memo language comes from the state of the task and reaches every label from here;
    an unreadable pack raises `i18n.PackUnavailable` rather than delivering an English memo.
    """
    style = oscola.normalise_style(citation_style) or oscola.DEFAULT_CITATION_STYLE
    language = memo_language(state)
    body = SOURCES_MARKER.sub("", draft_text).rstrip() + "\n"
    scanned = scan_mentions(body, index, style)
    cited = cited_source_ids(scanned["mentions"])
    rows = source_rows(scanned, index, language)
    body_text = attribute_blockquotes(
        drop_omitted(scanned["text"], scanned["mentions"]), scanned["mentions"]
    )
    text = render_mentions(body_text, scanned, index, language)
    replaced = {"footnotes": rows, "unresolved": scanned["unresolved"]}

    banners = []
    if replaced["unresolved"]:
        banners.append(fallbacks.banner("unresolved_reference_in_fallback"))

    parts = [text.rstrip() + "\n", "", render_sources_section(rows, language)]
    # D34-11: `## Status` sits between the memo and its appendix — the banners and the blockers the
    # run left open have to reach the client, not only `state.json`.
    status = render_status(status_inputs(state, banners))
    if status:
        parts.extend(["", status])
    appendix = render_appendix(
        list(drafting_warnings or []),
        index.unverified_rows(language, cited),
        replaced["unresolved"],
        currency_unavailable=index.currency_unavailable,
        language=language,
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


def render_workdir(
    work_dir: str | Path,
    draft_path: str | Path,
    *,
    state: dict | None = None,
    pinpoint_findings: list | None = None,
) -> dict:
    """Render `draft_path` using the registry files of `work_dir` and the state's warnings.

    D-204: `pinpoint_findings` — the C-09 findings of this very draft, from whoever chose it.
    """
    text = Path(draft_path).read_text(encoding="utf-8-sig")
    index = SourceIndex.load(work_dir, state=state, pinpoint_findings=pinpoint_findings)
    warnings = (state or {}).get("drafting_warnings") or []
    return render(
        text,
        index,
        drafting_warnings=warnings,
        state=state,
        citation_style=oscola.resolve_style(state),
    )
