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
}

BRIEF_MODE = "brief"

SEVERITY_BY_MODE: dict[str, dict[str, str]] = {BRIEF_MODE: {"C-07": "info"}}
"""D34-10: in `brief` the pack is wider than 1 200 words can carry, so an uncited rule source is
informational; in `full` it stays the `major` of §5.4."""

CYRILLIC_PINPOINT = re.compile(
    r"^(?:ст|пп|п|ч|абз)\.?\s*\d+(?:[.\-]\d+)*[а-яa-z]?(?:\([^()\s]+\))*"
    r"(?:[\s,]+(?:ст|пп|п|ч|абз)\.?\s*\d+(?:[.\-]\d+)*[а-яa-z]?(?:\([^()\s]+\))*)*\s*$",
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
"""

PINPOINT = re.compile(
    r"^(?:art(?:icle)?|s|ss|sec(?:tion)?|§{1,2}|para(?:graph)?s?|recital|rec|ch(?:apter)?|annex|"
    r"sch(?:edule)?|pp?|page|reg(?:ulation)?|rule|point|r)\b\.?\s*[\w().,\-/ ]*\d",
    re.IGNORECASE,
)
BARE_PINPOINT = re.compile(r"^\d+[\w().,\-/ ]*$")

MANUAL_CHECK_STATUSES: tuple[str, ...] = ("manual_check",)

RAW_TEXT_TIERS: tuple[str, ...] = ("critical", "supporting")
"""Tiers whose text must be saved before they are cited directly (A43-4 / D-156; `background` is exempt)."""


def finding(
    rule: str,
    line: int | None,
    section_id: str | None,
    excerpt: str | None,
    hint: str,
    severity: str | None = None,
) -> dict:
    """One `citations.json` finding (schema `lint`); D34-10: an absent excerpt is `null`, never `""`."""
    return {
        "rule": rule,
        "severity": severity or SEVERITY[rule],
        "line": line,
        "section_id": section_id,
        "excerpt": excerpt[:200] if excerpt else None,
        "hint": hint,
    }


def severity_for(rule: str, mode: str) -> str:
    """Severity of one C-rule in one run mode (D34-10)."""
    return (SEVERITY_BY_MODE.get(mode) or {}).get(rule) or SEVERITY[rule]


def resolve_mode(work_dir: str | Path, explicit: str | None = None) -> str:
    """Run mode behind the C-07 severity: the argument, else `state.mode` (D34-10)."""
    if explicit:
        return str(explicit).strip().lower()
    try:
        state = state_io.read_state(work_dir)
    except (OSError, ValueError):
        return ""
    return str(state.get("mode") or "").strip().lower()


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


def audit(text: str, *, work_dir: str | Path, mode: str | None = None) -> list[dict]:
    """Apply C-01..C-08 to one draft against the registry, the quote store and the snapshot."""
    document = lint.parse_draft(text, lint.grammar(resolve_language(work_dir)))
    run_mode = resolve_mode(work_dir, mode)
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
                severity=severity_for("C-07", run_mode),
            )
        )

    findings.sort(key=lambda row: (row["rule"], row["line"] if row["line"] is not None else 0))
    return findings


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
    """C-01/C-03/C-04/C-05/C-08 for one source, whether cited directly or reached through `[[q:]]`."""
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

    if not via_quote and str(record.get("tier") or "") in RAW_TEXT_TIERS and snapshot.get(source_id) is None:
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

    entry = entries.get(source_id, {})
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
    findings = audit(text, work_dir=work_dir, mode=str(state.get("mode") or ""))
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
