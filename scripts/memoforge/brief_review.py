"""Review validation and merge of the decision brief (plan 75A, D-225).

The two brief reviewers answer on their own contracts — fidelity on `brief-review`, the form
reviewer on its native `review` schema with the `brief-form` checklist. `validate_review` adds what
a schema cannot say: the draft sha, full and unique checklist coverage, an issue for every failed
blocker or major item, and blocks this brief actually has. `effective_issues` is fail-closed: an
item not graded `true` without an issue becomes a synthetic issue at its floor, and a review that
never parsed counts every item as missing. `after_reviews` is the `review_merge` step of the driver:
revise while rounds remain, shorten once over the hard cap, then the verdict.
"""

from __future__ import annotations

import functools
from pathlib import Path

from . import brief_lint, dispatch, i18n, limits, review, schema, state_io

KINDS: dict[str, dict[str, str]] = {
    "fidelity": {"checklist": "brief-fidelity", "schema": "brief-review", "block": "block"},
    "form": {"checklist": "brief-form", "schema": "review", "block": "section_id"},
}
"""Per reviewer: its checklist file, its schema and the issue field that names the brief block."""

CHECKLISTS: tuple[str, ...] = ("brief-fidelity", "brief-form")
BLOCKING: tuple[str, ...] = ("blocker", "major")
FAILED: tuple = (False, "unknown")
LENGTH_RULES: tuple[str, ...] = ("B-01", "B-12")
"""The length findings revise while rounds remain (B-01 above the hard cap only, D-228; B-12 a part over its word
budget, D-229) but never decide the verdict (O-12)."""
VERDICT_SOURCES: tuple[str, ...] = ("fidelity", "lint")
"""D-228: only these decide the verdict; form (clarity) findings reach the writer but never make it `unverified`."""
MAX_ROUND = 2
MINOR_HEADING = "## Minor (if easy)"
SHORTEN_INSTRUCTION = (
    "shorten to about {soft_cap} words (about three pages) by omission: move further leaves that meet no keep "
    "criterion to the omitted list and drop their assumptions and those of their actions the actions rule does not "
    "keep — an action due within 14 days of the memo date stays; never compress a kept sentence or drop a "
    "qualifier, condition, verdict or open point"
)
"""D-229: a brief is shortened by omitting leaves, never by compressing what it keeps; an omitted leaf's action due
within 14 days of the memo date stays (fix 3, replay 5)."""
FIDELITY_MISSING = "fidelity_review_missing"
"""The reason of a fidelity review that is missing or graded another version; a form review has none (D-228)."""


@functools.lru_cache(maxsize=None)
def load_checklist(name: str) -> tuple[dict, ...]:
    """`lib/checklists/<name>.json` of one brief reviewer, as a tuple of rows."""
    if name not in CHECKLISTS:
        raise ValueError(f"unknown_brief_checklist: {name!r}")
    rows = state_io.read_json(Path(dispatch.lib_path("lib", "checklists", f"{name}.json")))
    if not isinstance(rows, list):
        raise ValueError(f"invalid_checklist: {name}")
    return tuple(rows)


def _rows(kind: str) -> tuple[dict, ...]:
    return load_checklist(KINDS[kind]["checklist"])


def check_name(language: str, code: str) -> str:
    """The human name of a check id or an operational reason (`memo.brief.checks.<code>`)."""
    try:
        return i18n.t(language, f"memo.brief.checks.{code}")
    except (KeyError, i18n.PackUnavailable):
        return code


# --- validation --------------------------------------------------------------


def _grades(document: dict) -> dict[str, list]:
    grades: dict[str, list] = {}
    checklist = document.get("checklist")
    for item in checklist if isinstance(checklist, list) else []:
        if isinstance(item, dict):
            grades.setdefault(str(item.get("id")), []).append(item.get("pass"))
    return grades


def _issues(document: dict | None) -> list[dict]:
    issues = (document or {}).get("issues")
    return [issue for issue in issues if isinstance(issue, dict)] if isinstance(issues, list) else []


def _id_errors(kind: str, document: dict) -> list[str]:
    """The three messages of `review._checklist_id_errors`, against the brief checklist."""
    expected = [row["id"] for row in _rows(kind)]
    seen = [str(item.get("id")) for item in document["checklist"]]
    errors = [f"duplicate_checklist_id: {i}" for i in sorted({i for i in seen if seen.count(i) > 1})]
    errors += [f"unknown_checklist_id: {i}" for i in sorted(set(seen) - set(expected))]
    errors += [f"missing_checklist_id: {i}" for i in expected if i not in set(seen)]
    return errors


def validate_review(kind: str, document: dict, *, draft_sha: str, block_ids) -> dict:
    """`{"valid", "errors"}` of one brief review against the current version (D-225)."""
    spec = KINDS[kind]
    errors = [str(message) for message in schema.validate(document, spec["schema"])]
    if not errors and kind == "form" and document.get("reviewer") != "form":
        errors.append(f"reviewer_not_form: {document.get('reviewer')}")
    if errors:
        return {"valid": False, "errors": errors}
    if document["draft_sha"] != draft_sha:
        errors.append("stale_draft_sha")
    errors += _id_errors(kind, document)
    grades = _grades(document)
    issues = _issues(document)
    with_issue = {str(issue.get("checklist_id")) for issue in issues if issue.get("checklist_id")}
    for row in _rows(kind):
        failed = any(grade in FAILED for grade in grades.get(row["id"], []))
        if row["severity_floor"] in BLOCKING and failed and row["id"] not in with_issue:
            errors.append(f"missing_issue_for_failed_item: {row['id']}")
    allowed = set(block_ids) | {brief_lint.DOCUMENT_ID}
    for issue in issues:
        checklist_id = issue.get("checklist_id")
        if not checklist_id or not any(grade in FAILED for grade in grades.get(str(checklist_id), [])):
            errors.append(f"issue_without_failed_item: {checklist_id or 'none'}")
        block = issue.get(spec["block"])
        if block not in allowed:
            errors.append(f"unknown_block: {block}")
    return {"valid": not errors, "errors": errors}


# --- fail-closed issues and the merge ----------------------------------------


def _clamp(severity: str, row: dict) -> str:
    """`severity` held inside `[severity_floor, severity_max]` of its row (rank 0 = blocker)."""
    rank = review.SEVERITY_RANK
    value = rank.get(severity, rank[row["severity_floor"]])
    value = min(max(value, rank[row["severity_max"]]), rank[row["severity_floor"]])
    return next(name for name, number in rank.items() if number == value)


def _issue(source: str, checklist_id: str, severity: str, block: str, issue: str, suggestion: str = "",
           quote: str = "", synthetic: bool = False) -> dict:
    return {"source": source, "checklist_id": checklist_id, "severity": severity, "block": block,
            "issue": issue, "suggestion": suggestion, "quote": quote, "synthetic": synthetic}


def effective_issues(kind: str, document: dict | None, *, language: str = i18n.DEFAULT) -> list[dict]:
    """The issues a review stands for, fail-closed (D-225).

    Every row not graded `true` (false, unknown or missing) with no issue of its own becomes a
    synthetic issue at its `severity_floor` on `document`; every real issue is clamped to the
    `[severity_floor, severity_max]` of its row. `None` (no parseable review) grades nothing.
    """
    spec = KINDS[kind]
    rows = {row["id"]: row for row in _rows(kind)}
    grades = _grades(document or {})
    result: list[dict] = []
    covered: set[str] = set()
    for raw in _issues(document):
        checklist_id = str(raw.get("checklist_id") or "")
        severity = str(raw.get("severity") or "major")
        if checklist_id in rows:
            severity = _clamp(severity, rows[checklist_id])
            covered.add(checklist_id)
        result.append(_issue(
            kind, checklist_id, severity, str(raw.get(spec["block"]) or brief_lint.DOCUMENT_ID),
            str(raw.get("issue") or ""), str(raw.get("suggestion") or ""), str(raw.get("brief_quote") or ""),
        ))
    synthetic = [
        _issue(kind, identifier, row["severity_floor"], brief_lint.DOCUMENT_ID, check_name(language, identifier),
               synthetic=True)
        for identifier, row in rows.items()
        if identifier not in covered and not (grades.get(identifier) and all(g is True for g in grades[identifier]))
    ]
    return result + synthetic


def _lint_issue(finding: dict) -> dict:
    rule = str(finding.get("rule") or "")
    return _issue("lint", rule, str(finding.get("severity") or "major"),
                  str(finding.get("section_id") or brief_lint.DOCUMENT_ID),
                  f"{rule}: {finding.get('hint') or ''}".strip(), quote=str(finding.get("excerpt") or ""))


def _block_rank(block: str) -> int:
    order = brief_lint.BLOCK_IDS
    return order.index(block) if block in order else len(order)


def merge(lint_findings, fidelity_issues, form_issues) -> list[dict]:
    """Lint + fidelity + form, by severity then block (`document` last); only exact duplicates go."""
    merged: list[dict] = []
    seen: set[tuple] = set()
    candidates = [_lint_issue(row) for row in lint_findings] + list(fidelity_issues) + list(form_issues)
    for issue in candidates:
        key = (issue["block"], issue["checklist_id"], issue["quote"], issue["issue"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(issue)
    return sorted(merged, key=lambda row: (review.SEVERITY_RANK.get(row["severity"], 9), _block_rank(row["block"])))


def block_headings(parsed: dict) -> dict[str, str]:
    """`{block id: "<heading> (<id>)"}` of the blocks this brief has, for the writer's instructions."""
    titles: dict[str, str] = {}
    for kind, block_id in brief_lint.PART_BLOCK_IDS.items():
        section = parsed["parts"][kind]
        if section is not None:
            titles[block_id] = section["title"]
    for row in parsed["blocks"]:
        titles[row["id"]] = row["title"]
    headings = {"s-header": "Title and header lines (s-header)", brief_lint.DOCUMENT_ID: "The whole brief (document)"}
    for block_id, title in titles.items():
        clean = " ".join(title.split("<!--")[0].split())
        headings[block_id] = f"{clean} ({block_id})" if clean else block_id
    return headings


def _line(issue: dict, *, with_block: bool = False) -> str:
    text = issue["issue"]
    if with_block:
        text = f"{issue['block']}: {text}"
    return f"- [{issue['severity']}] {text}" + (f" — {issue['suggestion']}" if issue["suggestion"] else "")


def instructions_markdown(merged: list[dict], headings: dict[str, str]) -> str:
    """`## <block heading>` groups of the blocking issues, then `## Minor (if easy)`."""
    lines: list[str] = []
    blocking = [issue for issue in merged if issue["severity"] in BLOCKING]
    for block in sorted({issue["block"] for issue in blocking}, key=_block_rank):
        lines += [f"## {headings.get(block, block)}", ""]
        lines += [_line(issue) for issue in blocking if issue["block"] == block]
        lines.append("")
    minor = [issue for issue in merged if issue["severity"] not in BLOCKING]
    if minor:
        lines += [MINOR_HEADING, ""] + [_line(issue, with_block=True) for issue in minor] + [""]
    return "\n".join(lines)


# --- the review_merge step ---------------------------------------------------


def _write(work_dir: Path, relative: str, text: str) -> str:
    state_io.write_bytes_atomic(work_dir / relative, text.encode("utf-8"))
    return relative


def after_reviews(work_dir: Path, run: dict, state: dict) -> tuple[str, dict]:
    """`(next_step, run_patch)` of the `review_merge` step (D-225).

    1. Anything blocking — form findings and B-01 above the hard cap included (D-228) — while
       `round < 2` → `revise`.
    2. Otherwise, over the hard cap and not yet shortened → `shorten` once; the form findings
       still open are carried, and the next merge (fidelity only) never revises or shortens.
    3. Otherwise → `render` with the verdict (D-228): `clean` when no fidelity or lint finding but B-01/B-12
       is blocking and the fidelity review graded this version; form findings, a missing or stale form
       review and the carried form findings never decide it. A missing or stale form review adds no
       synthetic issue to the merge, so it never sends the brief back on its own.
    """
    from . import brief  # the driver imports this module; its integrity readers are needed here

    work_dir = Path(work_dir)
    language = run["input"]["language"]
    round_ = int(run["round"])
    _, text, sha = brief.current_version(work_dir, run)
    parsed = brief_lint.parse_brief(text, language)
    memo = (work_dir / run["input"]["draft_path"]).read_text(encoding="utf-8-sig")
    findings = brief_lint.lint_brief(text, memo, language=language)

    missing: list[str] = []
    documents = {}
    for kind in ("fidelity", "form"):
        document = brief.load_review(work_dir, run, kind, round_)
        graded_here = kind == "fidelity" or not run["shorten_done"]  # the carried form review graded v(n-1)
        if document is not None and graded_here and document.get("draft_sha") != sha:
            # A review kept after exhaustion that graded another version: its findings stand, its
            # passes do not — every item it did not raise an issue on counts as missing (D-227).
            document = {**document, "checklist": []}
            missing.append(kind)
        elif document is None:
            missing.append(kind)
        documents[kind] = document
    fidelity = effective_issues("fidelity", documents["fidelity"], language=language)
    if run["shorten_done"]:
        form = [dict(issue) for issue in run["carried_form_findings"]]
    else:
        form = effective_issues("form", documents["form"], language=language)
        if "form" in missing:
            # D-228: a missing or stale form review stands for nothing the writer could fix — only the
            # real issues of a stale one go on; its fail-closed synthetics would cost passes for no verdict.
            form = [issue for issue in form if not issue["synthetic"]]
    merged = merge(findings, fidelity, form)
    words = brief_lint.brief_words(parsed)
    too_long = words > limits.DECISION_BRIEF_HARD_CAP
    blocking = [issue for issue in merged if issue["severity"] in BLOCKING]

    if not run["shorten_done"]:
        if blocking and round_ < MAX_ROUND:
            next_round = round_ + 1
            path = _write(work_dir, f"{brief.BRIEF_DIR}/instructions-r{next_round}.md",
                          instructions_markdown(merged, block_headings(parsed)))
            return "revise", {"round": next_round, "instructions_path": path, "too_long": too_long}
        if too_long:
            instruction = SHORTEN_INSTRUCTION.format(soft_cap=limits.DECISION_BRIEF_SOFT_CAP)
            path = _write(work_dir, f"{brief.BRIEF_DIR}/instructions-r{round_}-shorten.md", instruction + "\n")
            carried = [issue for issue in form if issue["severity"] in BLOCKING]
            return "shorten", {"shorten_done": True, "carried_form_findings": carried, "instructions_path": path,
                               "too_long": True}

    open_checks = [
        issue["checklist_id"]
        for issue in blocking
        if issue["source"] in VERDICT_SOURCES and issue["checklist_id"] not in LENGTH_RULES
    ]
    reasons = [FIDELITY_MISSING] if "fidelity" in missing else []
    patch: dict = {"too_long": too_long}
    if open_checks or reasons:
        patch["verdict"] = "unverified"
        patch["outcome_reasons"] = list(dict.fromkeys(list(run["outcome_reasons"]) + reasons + open_checks))
    else:
        patch["verdict"] = "clean"
    return "render", patch
