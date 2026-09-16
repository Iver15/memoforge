"""`mf render <view>` — deterministic markdown views of the JSON artifacts (ТЗ §5.1, M4)."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import events, review, state_io, stepctx

LAYERS: tuple[str, ...] = ("statutes", "case_law", "doctrine")

VIEWS: tuple[str, ...] = (
    "research",
    "currency",
    "source-pack",
    "mediator",
    "sufficiency",
)
"""D34-23: there is no `summary` view — `summary.md` has exactly one writer, `finalize.write_summary`
(§2.1 row 16). A second renderer of the same file could only disagree with it."""


def _lines(*parts: object) -> str:
    """Join rendered blocks into one markdown document with a single trailing newline."""
    out: list[str] = []
    for part in parts:
        if part is None:
            continue
        text = str(part)
        if text:
            out.append(text)
    return "\n".join(out).rstrip("\n") + "\n"


def _table(header: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_none_"
    widths = "|".join(["---"] * len(header))
    body = "\n".join("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows)
    return "| " + " | ".join(header) + " |\n|" + widths + "|\n" + body


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "_none_"


# --- views ----------------------------------------------------------------


def render_research(document: dict) -> str:
    """`research/<layer>.json` → the layer digest the writer reads (§4.4)."""
    blocks = [f"# Research — {document.get('layer', 'unknown')}"]
    methodology = document.get("methodology") or {}
    if methodology:
        blocks.append("## Methodology")
        blocks.append(
            _bullets(
                [
                    f"Searched: {', '.join(methodology.get('queried_sources', [])) or 'n/a'}",
                    f"Jurisdictions: {', '.join(methodology.get('jurisdictions', [])) or 'n/a'}",
                    f"Date of search: {methodology.get('date_of_search', 'n/a')}",
                ]
                + ([f"Notes: {methodology['notes']}"] if methodology.get("notes") else [])
            )
        )
    for issue in document.get("issues", []):
        blocks.append(f"## Issue {issue.get('issue_id')}")
        rows = [
            [
                finding.get("source_id", ""),
                finding.get("role", ""),
                finding.get("weight", ""),
                finding.get("tier", ""),
                finding.get("confidence", ""),
                finding.get("pinpoint", ""),
                finding.get("proposition", ""),
            ]
            for finding in issue.get("findings", [])
        ]
        blocks.append(
            _table(
                ["source_id", "role", "weight", "tier", "confidence", "pinpoint", "proposition"],
                rows,
            )
        )
        contrary = [
            f"`{finding.get('source_id')}` — {finding.get('contrary_point')}"
            for finding in issue.get("findings", [])
            if finding.get("contrary_point")
        ]
        if contrary:
            blocks.append("### Contrary points")
            blocks.append(_bullets(contrary))
    excluded = document.get("considered_excluded") or []
    if excluded:
        blocks.append("## Considered and excluded")
        blocks.append(_bullets([f"{row.get('title')} — {row.get('reason')}" for row in excluded]))
    return _lines(*blocks)


def render_currency(document: dict) -> str:
    """`research/currency.json` → the currency table used by the source review gate (§2.1 стр.9)."""
    rows = [
        [
            source.get("source_id", ""),
            source.get("layer", ""),
            source.get("status", ""),
            source.get("note", ""),
        ]
        for source in document.get("sources", [])
    ]
    return _lines(
        "# Currency check",
        f"Checked at: {document.get('checked_at', 'n/a')}",
        "## Sources",
        _table(["source_id", "layer", "status", "note"], rows),
        "## Blocking",
        _bullets(list(document.get("blocking", []))),
        "## Warnings",
        _bullets(list(document.get("warnings", []))),
    )


def render_source_pack(document: dict) -> str:
    """`research/source-pack.json` → the frozen pack the writer cites from (§5.3)."""
    rows = [
        [
            entry.get("source_id", ""),
            entry.get("layer", ""),
            entry.get("tier", ""),
            (entry.get("pack") or {}).get("use_in_memo", ""),
            (entry.get("pack") or {}).get("weight", ""),
            entry.get("currency_status", ""),
            entry.get("citation_form", ""),
        ]
        for entry in document.get("entries", [])
    ]
    snapshot = [
        f"`{row.get('source_id')}` — raw_sha256 {row.get('raw_sha256') or 'none'}"
        for row in document.get("snapshot", [])
    ]
    return _lines(
        "# Source pack (frozen)",
        f"Frozen at: {document.get('frozen_at', 'n/a')}",
        "## Entries",
        _table(
            ["source_id", "layer", "tier", "use_in_memo", "weight", "currency", "citation"],
            rows,
        ),
        "## Snapshot",
        _bullets(snapshot),
    )


def render_mediator(document: dict) -> str:
    """`reviews/v<N>-mediator.json` → the revision instructions the writer edits from (§4.5 п.5)."""
    rows = [
        [
            instruction.get("section_id", ""),
            instruction.get("severity", ""),
            instruction.get("source_reviewer", ""),
            instruction.get("category", ""),
            " ".join(
                part
                for part in (instruction.get("instruction"), instruction.get("resolution"))
                if part
            ),
        ]
        for instruction in document.get("instructions", [])
    ]
    dropped = [
        f"{row.get('section_id', 'document')} — {row.get('issue')} ({row.get('reason')})"
        for row in document.get("dropped", [])
    ]
    heading = "# Revision instructions"
    if document.get("iteration"):
        heading += f" — iteration {document['iteration']}"
    return _lines(
        heading,
        "## Instructions",
        _table(["section_id", "severity", "source", "category", "instruction"], rows),
        "## Dropped",
        _bullets(dropped),
    )


def render_sufficiency(document: dict) -> str:
    """`research/research-sufficiency.json` → the gap table behind the phase-7 gate (§2.1 стр.6)."""
    rows = [
        [
            gap.get("target", ""),
            gap.get("status", ""),
            gap.get("gap", ""),
            gap.get("why_blocking", ""),
        ]
        for gap in document.get("blocking_gaps", [])
    ]
    questions = [
        gap["followup_question"]["question"]
        for gap in document.get("blocking_gaps", [])
        if gap.get("followup_question")
    ]
    return _lines(
        "# Research sufficiency",
        f"Verdict: {document.get('overall_verdict', 'n/a')}",
        "## Blocking gaps",
        _table(["target", "status", "gap", "why blocking"], rows),
        "## Follow-up questions",
        _bullets(questions),
        "## Drafting warnings",
        _bullets([str(text) for text in document.get("drafting_warnings", [])]),
    )


# --- CLI ------------------------------------------------------------------


def _plan(view: str, work_dir: Path, state: dict, args: argparse.Namespace) -> list[dict]:
    """Return the `{source, target, render}` jobs of one view."""
    iteration = args.iteration or int(state.get("current_iteration") or 1)
    if view == "research":
        layers = [args.layer] if args.layer else list(LAYERS)
        jobs = []
        for layer in layers:
            source = f"research/{layer}.json"
            if (work_dir / source).is_file():
                jobs.append({"source": source, "target": f"research/{layer}.md", "render": render_research})
        return jobs
    if view == "currency":
        return [{"source": "research/currency.json", "target": "research/currency.md", "render": render_currency}]
    if view == "source-pack":
        return [
            {
                "source": "research/source-pack.json",
                "target": "research/source-pack.md",
                "render": render_source_pack,
            }
        ]
    if view == "mediator":
        return [
            {
                "source": review.mediator_path(iteration),
                "target": f"reviews/v{iteration}-mediator.md",
                "render": render_mediator,
            }
        ]
    return [
        {
            "source": "research/research-sufficiency.json",
            "target": "research/research-sufficiency.md",
            "render": render_sufficiency,
        }
    ]


def run_render(args: argparse.Namespace) -> dict:
    """`mf render <view>` — write the markdown view(s); deterministic, no network."""
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    attempt = int(getattr(args, "attempt", 1) or 1)
    if args.step:
        # §3.1 / D-40: the identity gate, called on `stepctx` directly.
        identity = stepctx.check_identity(state, args.step, attempt)
        if identity["status"] == stepctx.STATUS_CLOSED:
            stored = identity.get("result")
            return stored if isinstance(stored, dict) else {"already_closed": True, "result": stored}
        if identity["status"] == stepctx.STATUS_MISMATCH:
            return {"errors": list(identity["errors"]), "step_id": args.step, "attempt": attempt}

    jobs = _plan(args.view, work_dir, state, args)
    if not jobs:
        return {"errors": [f"no_input_for_view: {args.view}"], "view": args.view}

    outputs: list[dict] = []
    published: list[dict] = []
    markdown: dict[str, str] = {}
    for job in jobs:
        path = work_dir / job["source"]
        if not path.is_file():
            return {"errors": [f"missing_input: {job['source']}"], "view": args.view}
        try:
            # D-41: a view must not render bytes that drifted from `published[]` — the markdown
            # would carry an authority the canonical file no longer has (§2.2).
            document = stepctx.read_published(work_dir, job["source"], state=state)
        except stepctx.OutputModifiedAfterPublish as exc:
            return stepctx.drift_result(exc, view=args.view)
        text = job["render"](document)
        target = args.out if (args.out and len(jobs) == 1) else job["target"]
        payload = text.encode("utf-8")
        if args.step:
            work_file = stepctx.stage_result(work_dir, args.step, attempt, Path(target).name, payload)
            entry = stepctx.publish_file(work_dir, work_file, target, step_id=args.step)
        else:
            state_io.write_bytes_atomic(work_dir / target, payload)
            entry = {
                "canonical_path": target,
                "sha256": state_io.sha256_bytes(payload),
                "by": "command",
                "step_id": None,
                "at": events.utc_now(),
            }
        published.append(entry)
        outputs.append({"source": job["source"], "path": target, "sha256": entry["sha256"], "bytes": len(payload)})
        markdown[target] = text

    result = {"view": args.view, "outputs": outputs, "count": len(outputs)}
    if args.print_markdown:
        result["markdown"] = markdown
    if args.step:
        stepctx.close_step(work_dir, args.step, attempt, result, published=published)
    else:
        state_io.write_state(work_dir, lambda state: stepctx.merge_published(state, published))
    return result


def register(subparsers) -> None:
    """Register the `render` command (a single command, not a group)."""
    parser = subparsers.add_parser("render", help="markdown views of the JSON artifacts (M4)")
    parser.add_argument("view", choices=list(VIEWS))
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--layer", default=None, choices=list(LAYERS), help="research view: one layer only")
    parser.add_argument("--iteration", type=int, default=None, help="mediator view: iteration number")
    parser.add_argument("--out", default=None, help="override the target path (single-output views)")
    parser.add_argument("--print", dest="print_markdown", action="store_true", help="include the markdown")
    parser.add_argument("--step", default=None)
    parser.add_argument("--attempt", type=int, default=1)
    parser.set_defaults(func=run_render)
