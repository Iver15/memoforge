"""`mf draft audit-citations` — the deterministic C-rules over a frozen source pack (ТЗ §5.4, M5/M6)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import lint, quotes, sources, state_io, stepctx

CITATIONS_PATH = "citations.json"

SEVERITY: dict[str, str] = {
    "C-01": "blocker",
    "C-02": "blocker",
    "C-03": "blocker",
    "C-04": "blocker",
    "C-05": "blocker",
    "C-06": "major",
    "C-07": "major",
    "C-08": "major",
    "C-09": "major",
}

PINPOINT_NOT_IN_RAW = "C-09"
"""D-204: `pinpoint_not_in_raw` — a pinpoint names a number its source's saved text does not print."""

CYRILLIC_LABEL = r"(?:раздел|разд|прил|абз|пп|ст|гл|ч|п)"
"""D-186/D-195: the pinpoint labels a Russian source uses, longest alternative first.

`ст`, `п`, `пп`, `ч`, `абз` number a statute; `разд`/`раздел`, `гл` and `прил` number a contract
or an offer — its sections, chapters and annexes. `прил` precedes `п` and `раздел` precedes `разд`
so the longer label wins; `ч` and `п` come last for the same reason.
"""

_CYRILLIC_NUMBER = r"\d+(?:[.\-]\d+)*[а-яa-z]?(?:\([^()\s]+\))*"
_CYRILLIC_SEGMENT = rf"{CYRILLIC_LABEL}\.?\s*{_CYRILLIC_NUMBER}"
_CYRILLIC_HEADING = r"(?:раздел|разд)\.?\s*«[^«»]+»"
"""D-195: a section of a contract may be named by its heading instead of a number —
`разд. «Возмещение»`. Only `разд.` may stand without a digit; every other label needs one."""

CYRILLIC_PINPOINT = re.compile(
    rf"^(?:{_CYRILLIC_SEGMENT}|{_CYRILLIC_HEADING})"
    rf"(?:[\s,]+(?:{_CYRILLIC_SEGMENT}|{_CYRILLIC_HEADING}))*\s*$",
    re.IGNORECASE,
)
"""D-186: the pinpoint as the Russian source numbers it — `п. 2 ст. 152`, `ч. 1 ст. 14.3`.

Fix round 1: the number fragment is strict — digits with dots/hyphens, an optional trailing
letter (`10а`, `152.1`, `10-1`) and optional `(…)` subdivisions; `ст. foo 152` is rejected.
`пп` precedes `п` in the alternation so the two-letter label matches as one group.

Fix round 2: `re.IGNORECASE`, as in `docx/oscola.py::CYRILLIC_PINPOINT_RE` — a sentence-initial
`Ст. 152` is the same pinpoint and was drawing a C-06 major while the renderer printed it happily.
The case fold reaches the trailing letter too (`ст. 10А` as well as `ст. 10а`); it does not loosen
the number fragment, so `ст. foo 152` stays rejected and a bare `152` is still `BARE_PINPOINT`'s.

D-195: the labels of a contract or an offer join the statute ones, and a section heading in
guillemets may stand where a number would — `п. 3 разд. «Возмещение»`, `разд. «FBO»`.
"""

PINPOINT = re.compile(
    r"^(?P<label>art(?:icle)?|s|ss|sec(?:tion)?|§{1,2}|para(?:graph)?s?|recital|rec|ch(?:apter)?|annex|"
    r"sch(?:edule)?|pp?|page|reg(?:ulation)?|rule|point|r)\b\.?\s*[\w().,\-/ ]*\d",
    re.IGNORECASE,
)
"""The pinpoint of a non-Russian source, led by its label; D-204 reads the label through `label`."""
BARE_PINPOINT = re.compile(r"^\d+[\w().,\-/ ]*$")

MANUAL_CHECK_STATUSES: tuple[str, ...] = ("manual_check",)

RAW_TEXT_TIERS: tuple[str, ...] = ("critical", "supporting")
"""Tiers whose text must be saved before they are cited directly (A43-4 / D-156; `background` is exempt)."""

FULL_TEXT_KINDS: tuple[str, ...] = ("full_text", "client_file")
"""D-204: the `raw_kind`s a `critical` source may be cited on without the extended C-08 — the whole
document, as the code saved it or as the client supplied it."""

PINPOINT_CHECKED_KINDS: tuple[str, ...] = ("full_text", "excerpt", "client_file")
"""D-204: the `raw_kind`s whose saved text C-09 searches — text that is the document's own words.
An `agent_summary` is not searched (the agent's copy proves nothing either way); `none` has no text."""

C09_EXEMPT_CYRILLIC: tuple[str, ...] = ("абз", "ч")
"""D-204: the Russian labels whose number C-09 never looks for — an `абз.` or a `ч.` is counted, not
printed, in the source. Every other label of `CYRILLIC_LABEL` carries a printed number."""

C09_CHECKED_LATIN = re.compile(
    r"art(?:icle)?|s|ss|sec(?:tion)?|para(?:graph)?s?|ch(?:apter)?|reg(?:ulation)?|point",
    re.IGNORECASE,
)
"""D-204: the labels of `PINPOINT` whose number is printed in the source — article, section, paragraph,
chapter, regulation, point. A page, a recital, an annex, a schedule, a rule and `§` are exempt."""

_CYRILLIC_PART = re.compile(
    rf"(?P<label>{CYRILLIC_LABEL})\.?\s*(?P<number>{_CYRILLIC_NUMBER})|{_CYRILLIC_HEADING}",
    re.IGNORECASE,
)
"""One segment of a Russian pinpoint, built from the same pieces as `CYRILLIC_PINPOINT` (D-204)."""

_PRINTED_NUMBER = re.compile(r"\d+(?:\.\d+)*")
"""A printed number: digits, and a dot only between digits — `152.1` is one number, never `152`."""


def printed_numbers(pinpoint: str) -> list[str]:
    """The numbers of a pinpoint that its source must print, in order, each once (D-204, C-09).

    The grammar is not forked: a Russian pinpoint is read segment by segment through
    `CYRILLIC_LABEL`, a non-Russian one through the label `PINPOINT` matched. Exempt, and so never
    returned: an `абз.` or `ч.` segment, a heading in guillemets, a page, a recital, a bare number,
    Roman numerals (they are not digits) and anything the grammar does not recognise.
    """
    text = str(pinpoint or "").strip()
    numbers: list[str] = []
    if CYRILLIC_PINPOINT.match(text):
        for part in _CYRILLIC_PART.finditer(text):
            label = part.group("label")
            if label is None or label.lower() in C09_EXEMPT_CYRILLIC:
                continue
            numbers.extend(_PRINTED_NUMBER.findall(part.group("number")))
    else:
        match = PINPOINT.match(text)
        if match and C09_CHECKED_LATIN.fullmatch(match.group("label")):
            numbers.extend(_PRINTED_NUMBER.findall(text[match.end("label"):]))
    return list(dict.fromkeys(numbers))


def number_in_text(number: str, text: str) -> bool:
    """True when `number` stands in `text` as a whole number, anywhere (D-204).

    Neither a digit nor a dotted continuation may touch it: `152` is not in «152.1» or «1152», and
    `152.1` is not in «152.12»; a full stop that ends a sentence («ст. 152.») does not count as one.
    """
    pattern = rf"(?<!\d)(?<!\d\.){re.escape(number)}(?!\.?\d)"
    return re.search(pattern, text) is not None


def declared_raw_kind(entry: dict) -> str | None:
    """The `raw_kind` a pack entry declares, or None when it declares none (D-200, D-204).

    `sources.pack_raw_kind` reads a missing field as `agent_summary`/`none` — right for deciding what
    a text is, wrong for the extended C-08: a pack frozen before the field existed must not start
    drawing majors on every `critical` source it holds.
    """
    kind = (entry or {}).get("raw_kind")
    return sources.pack_raw_kind(entry) if kind in sources.RAW_KINDS else None


def finding(
    rule: str,
    line: int | None,
    section_id: str | None,
    excerpt: str | None,
    hint: str,
) -> dict:
    """One `citations.json` finding (schema `lint`); D34-10: an absent excerpt is `null`, never `""`."""
    return {
        "rule": rule,
        "severity": SEVERITY[rule],
        "line": line,
        "section_id": section_id,
        "excerpt": excerpt[:200] if excerpt else None,
        "hint": hint,
    }


def resolve_language(work_dir: str | Path) -> str:
    """Memo language of the run, the one the recognizers are built from (D-174); `en` when unreadable."""
    try:
        state = state_io.read_state(work_dir)
    except (OSError, ValueError):
        return "en"
    return (state or {}).get("language") or "en"


def locate_source(document: dict, source_id: str) -> dict | None:
    """First draft line naming `source_id` outside a code block, or None (D34-10).

    A C-07 source carries no `[[src:]]` token by definition, but its id often *is* in the draft —
    inside a malformed token or in the sources list. When it is, the finding gets a line, a
    section_id and an excerpt instead of a hint nobody can act on.
    """
    # The boundary class leaves `.` out so a sentence-final period does not hide the id.
    pattern = re.compile(r"(?<![a-z0-9_-])" + re.escape(source_id) + r"(?![a-z0-9_-])")
    for number, line in enumerate(document["lines"], start=1):
        if number in document["code_lines"]:
            continue
        if pattern.search(line):
            return {
                "line": number,
                "section_id": lint.section_of(document, number),
                "text": line.strip(),
            }
    return None


def pinpoint_ok(pinpoint: str) -> bool:
    """A pinpoint locates an article, paragraph, page or recital and carries a number (C-06)."""
    text = pinpoint.strip()
    if not text:
        return False
    return bool(PINPOINT.match(text) or BARE_PINPOINT.match(text) or CYRILLIC_PINPOINT.match(text))


def exec_summary_lines(document: dict) -> set[int]:
    """Line numbers inside the executive summary (C-04)."""
    section = next((row for row in document["sections"] if row["kind"] == "executive_summary"), None)
    if section is None:
        return set()
    return set(range(section["line"], section["end_line"] + 1))


def risk_lines(document: dict) -> set[int]:
    """Every line of every Risk-line paragraph, soft wrap included (C-04, D-12).

    A Risk line is a markdown paragraph, not a physical line: a newline inside it does not move the
    citation out of the Risk line.
    """
    numbers: set[int] = set()
    risk_prefix = document["grammar"].risk_prefix
    for paragraph in document["paragraphs"]:
        if risk_prefix.match(paragraph["text"]):
            numbers.update(range(paragraph["start_line"], paragraph["end_line"] + 1))
    return numbers


def read_frozen_pack(work_dir: str | Path) -> dict | None:
    """`research/source-pack.json` as published (D-41); None when the freeze never happened."""
    if not (Path(work_dir) / sources.PACK_PATH).is_file():
        return None
    pack = stepctx.read_published(work_dir, sources.PACK_PATH)
    return pack if isinstance(pack, dict) else None


def audit(text: str, *, work_dir: str | Path, language: str | None = None) -> list[dict]:
    """Apply C-01..C-09 to one draft against the registry, the quote store and the snapshot.

    `language` is the memo language the draft is read in; None reads it from `state.json`. D-204:
    a caller rendering with another effective state — `finalize --salvage` renders in English when
    the pack of the run's language cannot be read — passes the language it actually renders in.
    """
    document = lint.parse_draft(text, lint.grammar(language or resolve_language(work_dir)))
    registry = sources.read_registry(work_dir)
    quote_registry = quotes.read_quotes(work_dir)
    findings: list[dict] = []

    try:
        pack = read_frozen_pack(work_dir)
    except stepctx.OutputModifiedAfterPublish:
        return [
            finding(
                "C-05",
                None,
                None,
                "",
                f"{stepctx.OUTPUT_MODIFIED}: {sources.PACK_PATH} no longer matches the sha it was "
                "published with; restore it or re-run `mf sources pack --freeze`.",
            )
        ]

    if pack is None:
        return [
            finding(
                "C-05",
                None,
                None,
                "",
                "The source pack is not frozen; run `mf sources pack --freeze` before auditing a draft.",
            )
        ]

    snapshot = {row["source_id"]: row.get("raw_sha256") for row in pack.get("snapshot") or []}
    entries = {row["source_id"]: row for row in pack.get("entries") or []}
    # D-143: findings and quotes keep the ids they were written with, so a draft cites the duplicate
    # the freeze collapsed as often as the canonical; the alias is resolved before every lookup and
    # before the used-source accounting, or it draws a C-05 while the canonical draws a C-07.
    merged = pack.get("merged_into")
    merged = merged if isinstance(merged, dict) else {}
    summary_lines = exec_summary_lines(document)
    risk_line_numbers = risk_lines(document)

    used_sources: set[str] = set()
    raw_texts: dict[str, str | None] = {}
    """C-09 reads each saved text once per audit, however many tokens cite it."""

    for token in document["q_tokens"]:
        record = quote_registry["quotes"].get(token["id"])
        if record is None:
            findings.append(
                finding(
                    "C-01",
                    token["line"],
                    token["section_id"],
                    token["text"],
                    f"Quote {token['id']} is not in research/quotes.json.",
                )
            )
            continue
        source_id = sources.canonical_id(merged, record["source_id"])
        used_sources.add(source_id)
        findings.extend(
            _check_source(
                token,
                source_id,
                registry,
                snapshot,
                entries,
                summary_lines,
                risk_line_numbers,
                via_quote=True,
            )
        )
        findings.extend(
            _check_quote_text(document, token, record, registry, snapshot, work_dir, canonical=source_id)
        )

    for token in document["src_tokens"]:
        source_id = sources.canonical_id(merged, token["id"])
        used_sources.add(source_id)
        findings.extend(
            _check_source(
                token,
                source_id,
                registry,
                snapshot,
                entries,
                summary_lines,
                risk_line_numbers,
                via_quote=False,
            )
        )
        if not pinpoint_ok(token["pinpoint"]):
            findings.append(
                finding(
                    "C-06",
                    token["line"],
                    token["section_id"],
                    token["text"],
                    "Cite as `[[src:<source_id> <pinpoint>]]`, e.g. `Art. 6(1)(f)`, `para 42`, `p 15`.",
                )
            )
        else:
            findings.extend(
                _check_pinpoint_in_raw(token, source_id, registry, snapshot, entries, work_dir, raw_texts)
            )

    for source_id, entry in sorted(entries.items()):
        if (entry.get("pack") or {}).get("use_in_memo") != "rule" or source_id in used_sources:
            continue
        mention = locate_source(document, source_id)
        hint = f"Source {source_id} is packed as a rule source but never cited in the draft."
        if mention is not None:
            hint += (
                f" Its id does appear on line {mention['line']} outside a `[[src:]]` token; "
                "check for a malformed citation."
            )
        findings.append(
            finding(
                "C-07",
                mention["line"] if mention else None,
                mention["section_id"] if mention else None,
                mention["text"] if mention else None,
                hint,
            )
        )

    findings.sort(key=lambda row: (row["rule"], row["line"] if row["line"] is not None else 0))
    return findings


def pinpoint_findings(text: str, *, work_dir: str | Path, language: str | None = None) -> list[dict]:
    """The C-09 findings of one draft, computed afresh from its text (D-204).

    What the appendix of an export discloses: the version chosen for export is not always the last
    one audited, and `citations.json` describes only that last one. The renderers never import this
    module (D-195); whoever chooses the exported version calls this and hands them the result, with
    the language it renders in (None: that of `state.json`).
    """
    findings = audit(text, work_dir=work_dir, language=language)
    return [row for row in findings if row["rule"] == PINPOINT_NOT_IN_RAW]


def _check_source(
    token: dict,
    source_id: str,
    registry: dict,
    snapshot: dict,
    entries: dict,
    summary_lines: set[int],
    risk_line_numbers: set[int],
    *,
    via_quote: bool,
) -> list[dict]:
    """C-01/C-03/C-04/C-05/C-08 for one source, whether cited directly or reached through `[[q:]]`.

    D-204: C-08 also fires for a direct citation of a `critical` source whose **pack** entry declares
    a `raw_kind` outside `FULL_TEXT_KINDS`. The pack decides after the freeze, not the registry; an
    entry frozen before the field existed declares nothing and draws only the null-digest C-08.
    """
    out: list[dict] = []
    record = registry["sources"].get(source_id)
    if record is None:
        out.append(
            finding(
                "C-01",
                token["line"],
                token["section_id"],
                token["text"],
                f"Source {source_id} is not in research/sources.json.",
            )
        )
        return out
    if source_id not in snapshot:
        out.append(
            finding(
                "C-05",
                token["line"],
                token["section_id"],
                token["text"],
                f"Source {source_id} is not in the freeze snapshot; it cannot appear in the memo.",
            )
        )
        return out

    entry = entries.get(source_id, {})
    tier = str(record.get("tier") or "")
    declared = declared_raw_kind(entry)
    if not via_quote and tier in RAW_TEXT_TIERS and snapshot.get(source_id) is None:
        # D-156: the freeze kept the source but has no text for it — the claim cannot be checked
        # against the source. A caveat (major), not a refusal: the memo still ships with the note.
        out.append(
            finding(
                "C-08",
                token["line"],
                token["section_id"],
                token["text"],
                f"Source {source_id} ({record.get('tier')}) has no saved text in the freeze snapshot; the citation "
                "cannot be checked against the source and the appendix says so.",
            )
        )
    elif not via_quote and tier == "critical" and declared is not None and declared not in FULL_TEXT_KINDS:
        # D-204: the owner's rule — a `critical` claim on a text that is not the whole document is a
        # major, never a blocker. One finding per token: the null-digest branch above already spoke
        # for a source with no text at all. `excerpt` and `agent_summary` differ only in the appendix.
        out.append(
            finding(
                "C-08",
                token["line"],
                token["section_id"],
                token["text"],
                f"Source {source_id} (critical) is cited on `{declared}` text, not on the whole document "
                "saved by code; the citation is checked against a partial text and the appendix says so.",
            )
        )

    use = (entry.get("pack") or {}).get("use_in_memo")
    currency_status = entry.get("currency_status") or (record.get("currency") or {}).get("status", "unchecked")
    us = entry.get("verification_us") or (record.get("verification") or {}).get("us", "n/a")

    if use == "do_not_use" or currency_status == "do_not_use":
        out.append(
            finding(
                "C-03",
                token["line"],
                token["section_id"],
                token["text"],
                f"Source {source_id} is marked do_not_use; remove the citation"
                + (" and its quote." if via_quote else "."),
            )
        )
    if currency_status in MANUAL_CHECK_STATUSES or us == "unresolved":
        risky_line = token["line"] in risk_line_numbers  # C-04: the whole Risk paragraph, soft wrap included
        if risky_line or token["line"] in summary_lines:
            out.append(
                finding(
                    "C-04",
                    token["line"],
                    token["section_id"],
                    token["text"],
                    f"Source {source_id} is {currency_status}/{us}; it cannot carry a Risk line or an "
                    "executive-summary conclusion.",
                )
            )
    return out


def _check_pinpoint_in_raw(
    token: dict,
    canonical: str,
    registry: dict,
    snapshot: dict,
    entries: dict,
    work_dir: str | Path,
    texts: dict,
) -> list[dict]:
    """C-09 (D-204): every printed number of a `[[src:]]` pinpoint stands whole in the saved text.

    The text is that of the id the token names — the very file C-02 reads for a quote — and it is
    searched only when it holds the document's own words (`PINPOINT_CHECKED_KINDS`); after the freeze
    the pack entry says which kind it is. A number found anywhere satisfies the rule: this filters
    phantoms, it does not prove that the provision exists. The finding carries `source_id` and
    `pinpoint`, the two things the appendix line names.
    """
    own_id = token["id"]
    record = registry["sources"].get(own_id)
    if record is None or canonical not in snapshot:
        return []  # C-01 or C-05 has already said all there is to say about this token
    entry = entries.get(own_id)
    kind = sources.pack_raw_kind(entry, snapshot.get(own_id)) if entry else sources.raw_kind_of(record)
    if kind not in PINPOINT_CHECKED_KINDS:
        return []
    numbers = printed_numbers(token["pinpoint"])
    if not numbers:
        return []
    if own_id not in texts:
        texts[own_id] = sources.read_raw_text(work_dir, record)
    text = texts[own_id]
    if text is None:
        return []
    missing = [number for number in numbers if not number_in_text(number, text)]
    if not missing:
        return []
    row = finding(
        PINPOINT_NOT_IN_RAW,
        token["line"],
        token["section_id"],
        token["text"],
        f"Pinpoint `{token['pinpoint']}` names {', '.join(missing)}, which the saved text of {own_id} "
        "does not print anywhere; fix the pinpoint or remove it.",
    )
    row["source_id"] = own_id
    row["pinpoint"] = token["pinpoint"]
    return [row]


def _check_quote_text(
    document: dict,
    token: dict,
    record: dict,
    registry: dict,
    snapshot: dict,
    work_dir: str | Path,
    *,
    canonical: str | None = None,
) -> list[dict]:
    """C-02: exact blockquote text plus the triple sha equality of §5.3.

    D-143: the quote names the source it was extracted from and the snapshot pins the id that
    survived the freeze, so the bytes are read from the quote's own raw file and compared with the
    sha of `canonical` — merging duplicates never moved a raw file.
    """
    out: list[dict] = []
    source_id = record["source_id"]
    source = registry["sources"].get(source_id)
    if source is None:
        return out

    block = next(
        (row for row in document["blockquotes"] if row["start_line"] <= token["line"] <= row["end_line"]),
        None,
    )
    if block is None:
        out.append(
            finding(
                "C-02",
                token["line"],
                token["section_id"],
                token["text"],
                f"`[[q:{token['id']}]]` must introduce a blockquote of the extracted text.",
            )
        )
        return out

    drafted = quotes.normalized_text(lint.Q_TOKEN.sub("", block["text"]))
    stored = quotes.normalized_text(record["text"])
    if drafted != stored:
        out.append(
            finding(
                "C-02",
                block["start_line"],
                token["section_id"],
                block["text"],
                f"Blockquote does not match quote {token['id']} verbatim; re-run `mf quote extract`.",
            )
        )

    pinned = snapshot.get(canonical or source_id)
    raw_path = source.get("raw_path")
    current = None
    if raw_path:
        path = Path(work_dir) / raw_path
        if path.is_file():
            current = state_io.sha256_file(path)
    if not (record["raw_sha256"] == pinned == current) or current is None:
        out.append(
            finding(
                "C-02",
                block["start_line"],
                token["section_id"],
                block["text"],
                f"sha mismatch for {source_id}: quote={record['raw_sha256'][:12]}, "
                f"snapshot={str(pinned)[:12]}, file={str(current)[:12]}.",
            )
        )
        return out

    text = Path(work_dir, raw_path).read_text(encoding="utf-8-sig", errors="replace")
    fragment = text[record["char_start"] : record["char_end"]]
    if quotes.normalized_text(fragment) != stored:
        out.append(
            finding(
                "C-02",
                block["start_line"],
                token["section_id"],
                block["text"],
                f"Range {record['char_start']}..{record['char_end']} of {source_id} no longer holds the quote.",
            )
        )
    return out


# --- command --------------------------------------------------------------


def run_audit(args: argparse.Namespace) -> dict:
    """`mf draft audit-citations --step --attempt --draft <path>` (§5.4)."""
    work_dir = Path(args.workdir)
    draft_rel = stepctx.rel_path(work_dir, args.draft)
    args_key = f"draft audit-citations --draft {draft_rel}"
    state = state_io.read_state(work_dir)
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": identity["errors"], "reason": identity.get("reason")}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = dict(identity.get("result") or {})
        stored["already_done"] = True
        report_path = Path(work_dir) / stored.get("report_path", CITATIONS_PATH)
        if report_path.is_file():
            stored["report"] = state_io.read_json(report_path)
            stored["findings"] = stored["report"].get("findings") or []
        return stored

    draft_path = stepctx.abs_path(work_dir, draft_rel)
    if not draft_path.is_file():
        return {"errors": [f"draft_not_found: {draft_rel}"]}
    drift = stepctx.verify_published(work_dir, state, draft_rel)
    if drift:
        return {"errors": [drift], "draft": draft_rel}

    text = draft_path.read_text(encoding="utf-8-sig")
    findings = audit(text, work_dir=work_dir)
    report = lint.build_report(state_io.sha256_file(draft_path), findings)

    stepctx.stage_input(work_dir, args.step, args.attempt, draft_path)
    stepctx.stage_input(work_dir, args.step, args.attempt, work_dir / sources.PACK_PATH)
    work_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, "citations.json", state_io.dumps(report).encode("utf-8")
    )
    entry = stepctx.publish_file(work_dir, work_file, CITATIONS_PATH, step_id=args.step)

    # D34-10: `clean` alone reads as «citations are fine» even with 25 majors behind it, so the
    # result the orchestrator sees counts both severities.
    result = {
        "report_path": CITATIONS_PATH,
        "draft": draft_rel,
        "draft_sha": report["draft_sha"],
        "clean": report["clean"],
        "findings_count": len(findings),
        "blockers": sum(1 for row in findings if row["severity"] == "blocker"),
        "majors": sum(1 for row in findings if row["severity"] == "major"),
    }
    stepctx.close_step(
        work_dir,
        args.step,
        args.attempt,
        result,
        phase=args.phase,
        args_key=args_key,
        published=[entry],
    )
    return {**result, "findings": findings, "report": report}


def register(subparsers) -> None:
    """Register `mf draft audit-citations` (the `draft` group is shared with lint.py)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "draft", "draft anchors and deterministic checks")
    parser = group.add_parser("audit-citations", help="run the C-rules and write citations.json")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--phase", default=None)
    parser.set_defaults(func=run_audit)
