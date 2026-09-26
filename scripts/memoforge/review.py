"""`mf review validate|aggregate|mediator-from-issues` — the deterministic half of the review loop (ТЗ §4.5)."""

from __future__ import annotations

import argparse
import copy
import functools
import re
from pathlib import Path

from . import events, fallbacks, i18n, limits, lint, schema, sources, state_io, stepctx

REVIEWER_KINDS: tuple[str, ...] = ("logic", "form", "citations", "counterarguments")
CLIENT_READINESS_KIND = "client-readiness"
KINDS: tuple[str, ...] = REVIEWER_KINDS + (CLIENT_READINESS_KIND,)

POLISH_RECHECK = "citations_polish"
"""D-211: slot and path name of the one `citations` re-check of the final polish — never a kind.

It is validated as `citations` against `lib/checklists/citations.json`; adding it to `KINDS` would
send `checklist_path` looking for a checklist of its own.
"""

DETERMINISTIC = "deterministic"
"""Source label of lint/citations blockers folded into the issue set (§4.5 п.3)."""

SUBSTANCE_SOURCES: frozenset[str] = frozenset({"logic", "citations", "counterarguments", DETERMINISTIC})
"""`substance_blockers` counts these; everything else (form) counts as `form_blockers` (§4.5 п.3)."""

GROUNDED_SOURCES: frozenset[str] = frozenset({DETERMINISTIC, "citations"})
"""«blocker с evidence из deterministic/citations/hard_fail logic» (§4.5 п.4.6); logic needs a hard_fail id."""

UNVERIFIED_HARD_FAIL = "unverified_hard_fail"
UNVERIFIED_SECTION_ID = "document"

DEDUP_JACCARD = 0.8  # TODO(S2): move to limits.py
"""§4.5 п.3: issues merge inside one category at Jaccard >= 0.8 on the issue text."""

MEDIATOR_MIN_ISSUES = 2  # TODO(S2): move to limits.py
MEDIATOR_MIN_REVIEWERS = 2  # TODO(S2): move to limits.py
"""§4.5 п.4.6: a mediator is dispatched for >=2 blocker/major issues from >=2 reviewers (or a conflict)."""

DETERMINISTIC_REPORTS: tuple[str, ...] = ("lint.json", "citations.json")
"""Deterministic inputs of `aggregate`; both use the `lint` schema (§5.4)."""

SEVERITY_RANK: dict[str, int] = {"blocker": 0, "major": 1, "minor": 2}

_TOKEN = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁ]+")
_NEGATIONS: frozenset[str] = frozenset(
    {
        "no",
        "not",
        "never",
        "nor",
        "none",
        "without",
        "lacks",
        "lack",
        "lacking",
        "missing",
        "absent",
        "fails",
        "fail",
        "unsupported",
        "undefined",
        "cannot",
        "нет",
        "не",
        "без",
        "отсутствует",
    }
)
"""Polarity markers: two otherwise near-identical issues that differ here are a `conflict`, not a duplicate."""


def plugin_root() -> Path:
    """`<plugin_root>` — the repository root that holds `lib/` and `schemas/`."""
    return Path(__file__).resolve().parents[2]


def checklist_path(kind: str) -> Path:
    """`lib/checklists/<kind>.json` — the binary grader list of one reviewer (§4.5 п.2, D-09)."""
    if kind not in KINDS:
        raise ValueError(f"unknown_review_kind: {kind!r}")
    return plugin_root() / "lib" / "checklists" / f"{kind}.json"


@functools.lru_cache(maxsize=None)
def load_checklist(kind: str) -> tuple[dict, ...]:
    """Load and cache one checklist file as a tuple of `{id, text, tier, hard_fail}` rows."""
    items = state_io.read_json(checklist_path(kind))
    if not isinstance(items, list):
        raise ValueError(f"invalid_checklist: {checklist_path(kind)}")
    return tuple(items)


def schema_name(kind: str) -> str:
    """Schema backing one kind: `client-readiness` has its own document shape (D-07, §6)."""
    return CLIENT_READINESS_KIND if kind == CLIENT_READINESS_KIND else "review"


def expected_reviewer(kind: str) -> str:
    """Value of the `reviewer` field for one kind."""
    return "client_readiness" if kind == CLIENT_READINESS_KIND else kind


# --- validation (§4.5 п.2) -------------------------------------------------


def _checklist_id_errors(kind: str, checklist: list[dict]) -> list[str]:
    """The id set of `checklist[]` must equal the checklist file exactly: no dupes, no unknowns (D-09)."""
    expected = [row["id"] for row in load_checklist(kind)]
    seen: list[str] = [str(item.get("id")) for item in checklist]
    errors: list[str] = []
    for identifier in sorted({i for i in seen if seen.count(i) > 1}):
        errors.append(f"duplicate_checklist_id: {identifier}")
    for identifier in sorted(set(seen) - set(expected)):
        errors.append(f"unknown_checklist_id: {identifier}")
    for identifier in [i for i in expected if i not in set(seen)]:
        errors.append(f"missing_checklist_id: {identifier}")
    return errors


def _blockers(document: dict) -> list[dict]:
    return [issue for issue in document.get("issues", []) if issue.get("severity") == "blocker"]


def _memo_language(state: dict | None) -> str:
    """The memo language of a task; anything unusable is English (D-169)."""
    return i18n.normalize((state or {}).get("language")) or i18n.DEFAULT


def _unverified_issue(checklist_id: str, language: str = i18n.DEFAULT) -> dict:
    code = i18n.normalize(language) or i18n.DEFAULT
    row = {
        "severity": "blocker",
        "category": UNVERIFIED_HARD_FAIL,
        "section_id": UNVERIFIED_SECTION_ID,
        "issue": f"Hard-fail checklist item {checklist_id} was graded `unknown`, so it stayed unverified.",
        "suggestion": f"Re-run the reviewer on this draft and grade {checklist_id} as pass or fail.",
        "checklist_id": checklist_id,
    }
    if code != i18n.DEFAULT:
        # D-173a: the client-facing sentence of a blocker, in the memo language.
        row["issue_client"] = i18n.t(code, "memo.blockers.hard_fail_unknown", checklist_id=checklist_id)
    return row


def validate_document(
    kind: str,
    document: dict,
    *,
    current_draft_sha: str | None = None,
    iteration: int | None = None,
    language: str = i18n.DEFAULT,
) -> dict:
    """Validate one review document and return the corrected copy plus the `downgraded` flag (§4.5 п.2)."""
    if kind not in KINDS:
        raise ValueError(f"unknown_review_kind: {kind!r}")

    errors = schema.validate(document, schema_name(kind))
    if errors:
        return {
            "valid": False,
            "errors": errors,
            "document": document,
            "downgraded": False,
            "stub": False,
            "blockers": 0,
            "unverified_hard_fail": [],
        }

    corrected = copy.deepcopy(document)
    if (i18n.normalize(language) or i18n.DEFAULT) == i18n.DEFAULT:
        # D-173a: English never carries the field — a reviewer that wrote it did nothing wrong,
        # so it is removed silently and the review stays valid.
        for issue in corrected.get("issues", []):
            if isinstance(issue, dict):
                issue.pop("issue_client", None)
    stub = corrected.get("status") == "failed"
    if corrected.get("reviewer") != expected_reviewer(kind):
        errors.append(f"reviewer_kind_mismatch: {corrected.get('reviewer')!r} != {expected_reviewer(kind)!r}")

    if current_draft_sha and corrected.get("draft_sha") not in (None, current_draft_sha):
        errors.append(f"stale_draft_sha: {corrected.get('draft_sha')} != {current_draft_sha}")
    if iteration is not None and corrected.get("iteration") not in (None, iteration):
        errors.append(f"iteration_mismatch: {corrected.get('iteration')} != {iteration}")

    if stub:
        return {
            "valid": not errors,
            "errors": errors,
            "document": corrected,
            "downgraded": False,
            "stub": True,
            "blockers": 0,
            "unverified_hard_fail": [],
        }

    checklist = corrected.get("checklist") or []
    has_checklist = bool(checklist)
    if kind != CLIENT_READINESS_KIND or has_checklist:
        errors.extend(_checklist_id_errors(kind, checklist))

    hard_fail = {row["id"]: bool(row.get("hard_fail")) for row in load_checklist(kind)}
    graded = {str(item.get("id")): item.get("pass") for item in checklist}

    downgraded = False
    unverified: list[str] = []
    for identifier, verdict in sorted(graded.items()):
        if not hard_fail.get(identifier):
            continue
        if verdict == "unknown":
            unverified.append(identifier)

    if kind == CLIENT_READINESS_KIND:
        # D-07: the delivery review shares the id-set check only; its verdict enum is its own.
        return {
            "valid": not errors,
            "errors": errors,
            "document": corrected,
            "downgraded": False,
            "stub": False,
            "blockers": len(_blockers(corrected)),
            "unverified_hard_fail": unverified,
        }

    for identifier, verdict in sorted(graded.items()):
        if not hard_fail.get(identifier) or verdict is not False:
            continue
        if not any(
            issue.get("severity") == "blocker" and issue.get("checklist_id") == identifier
            for issue in corrected.get("issues", [])
        ):
            errors.append(f"missing_blocker_for_hard_fail: {identifier}")

    if unverified:
        known = {
            issue.get("checklist_id")
            for issue in corrected.get("issues", [])
            if issue.get("category") == UNVERIFIED_HARD_FAIL
        }
        for identifier in unverified:
            if identifier not in known:
                corrected.setdefault("issues", []).append(_unverified_issue(identifier, language=language))
                downgraded = True
        if corrected.get("verdict") == "approved":
            corrected["verdict"] = "needs_revision"
            downgraded = True

    blockers = _blockers(corrected)
    if corrected.get("verdict") == "approved" and blockers:
        errors.append(f"approved_with_blockers: {len(blockers)}")

    return {
        "valid": not errors,
        "errors": errors,
        "document": corrected,
        "downgraded": downgraded,
        "stub": False,
        "blockers": len(blockers),
        "unverified_hard_fail": unverified,
    }


# --- deduplication (§4.5 п.3) ---------------------------------------------


def _tokens(text: object) -> frozenset[str]:
    return frozenset(match.group(0).lower() for match in _TOKEN.finditer(str(text or "")))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard similarity of two token sets; 0.0 for two empty sets."""
    if not left and not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _polarity(tokens: frozenset[str]) -> bool:
    return bool(tokens & _NEGATIONS)


def _max_severity(left: str, right: str) -> str:
    return left if SEVERITY_RANK.get(left, 9) <= SEVERITY_RANK.get(right, 9) else right


SOURCE_PRECEDENCE: tuple[str, ...] = (DETERMINISTIC, "citations", "logic", "counterarguments", "form")
"""Order in which a merged issue picks its `source_reviewer`: substance participants first (§4.5 п.3)."""


def tier_of(provenance) -> str:
    """§4.5 п.3: one substance participant makes the merged issue a substance blocker."""
    return "substance" if any(source in SUBSTANCE_SOURCES for source in provenance) else "form"


def primary_reviewer(provenance) -> str:
    """The single `source_reviewer` shown for a merged issue; `provenance[]` keeps the full set."""
    ordered = [source for source in SOURCE_PRECEDENCE if source in provenance]
    return ordered[0] if ordered else sorted(provenance)[0]


def deduplicate(issues: list[dict]) -> list[dict]:
    """Merge near-identical issues inside one (section_id, category); opposite ones become `conflict`."""
    merged: list[dict] = []
    for issue in issues:
        tokens = _tokens(issue.get("issue"))
        polarity = _polarity(tokens)
        target = None
        for candidate in merged:
            if candidate["section_id"] != issue["section_id"] or candidate["category"] != issue["category"]:
                continue
            if jaccard(candidate["_tokens"], tokens) < DEDUP_JACCARD:
                continue
            if candidate["_polarity"] != polarity:
                candidate["conflict"] = True
                issue = dict(issue, conflict=True)
                target = None
                break
            target = candidate
            break
        if target is not None:
            target["severity"] = _max_severity(target["severity"], issue["severity"])
            target["provenance"] = sorted(set(target["provenance"]) | set(issue["provenance"]))
            target["grounded"] = bool(target["grounded"] or issue.get("grounded"))
            # §4.5 п.3: the merged issue belongs to every participant, so the tier and the reviewer
            # follow the union — merging a substance finding into a form one never demotes it.
            target["tier"] = tier_of(target["provenance"])
            target["source_reviewer"] = primary_reviewer(target["provenance"])
            if issue.get("issue_category") and not target.get("issue_category"):
                # D-165: the first participant that classified the issue keeps the classification.
                target["issue_category"] = issue["issue_category"]
            if issue.get("issue_client") and not target.get("issue_client"):
                # D-173a: the survivor keeps its own client sentence, or takes the other's.
                target["issue_client"] = issue["issue_client"]
            if issue.get("source_evidence") and not target.get("source_evidence"):
                # D-239: likewise the saved passage the finding rests on.
                target["source_evidence"] = issue["source_evidence"]
            continue
        row = dict(issue)
        row["_tokens"] = tokens
        row["_polarity"] = polarity
        row.setdefault("conflict", False)
        merged.append(row)
    for row in merged:
        row.pop("_tokens", None)
        row.pop("_polarity", None)
    return merged


def _normalize_issue(issue: dict, source: str) -> dict:
    row = {
        "severity": issue.get("severity", "minor"),
        "category": issue.get("category", "unspecified"),
        "section_id": issue.get("section_id") or UNVERIFIED_SECTION_ID,
        "issue": issue.get("issue", ""),
        "suggestion": issue.get("suggestion", ""),
        "source_reviewer": source,
        "provenance": [source],
        "tier": tier_of([source]),
    }
    if issue.get("checklist_id"):
        row["checklist_id"] = issue["checklist_id"]
    if issue.get("issue_category"):
        # D-165: the `citations` discriminator of §4.5 п.2 travels into `iterations[].issues[]`,
        # because branch 9 of `revision.decide` matches on it.
        row["issue_category"] = issue["issue_category"]
    if issue.get("issue_client"):
        # D-173a: the client-facing sentence of a blocker travels with its finding.
        row["issue_client"] = issue["issue_client"]
    if issue.get("source_evidence"):
        # D-239: the passage reaches the writer through the CLI mediator (`build_mediator`).
        row["source_evidence"] = issue["source_evidence"]
    row["grounded"] = is_grounded(row, source)
    return row


def is_grounded(issue: dict, source: str) -> bool:
    """«blocker с evidence из deterministic/citations/hard_fail logic» (§4.5 п.4.6)."""
    if issue.get("severity") != "blocker":
        return False
    if source in GROUNDED_SOURCES:
        return True
    if source == "logic":
        identifier = issue.get("checklist_id")
        if not identifier:
            return False
        return any(row["id"] == identifier and row.get("hard_fail") for row in load_checklist("logic"))
    return False


def _deterministic_issues(work_dir: Path, state: dict, draft_sha: str | None) -> tuple[list[dict], list[str]]:
    """Blockers of the current `lint.json`/`citations.json`; stale reports are reported, not used."""
    issues: list[dict] = []
    stale: list[str] = []
    language = _memo_language(state)
    for name in DETERMINISTIC_REPORTS:
        path = work_dir / name
        if not path.is_file():
            continue
        try:
            # D-41: the published bytes, not whatever is on disk now.
            report = stepctx.read_published(work_dir, name, state=state)
        except ValueError:  # malformed JSON; a drift raises `OutputModifiedAfterPublish`
            stale.append(name)
            continue
        if schema.validate(report, "lint"):
            stale.append(name)
            continue
        if draft_sha and report.get("draft_sha") != draft_sha:
            stale.append(name)
            continue
        for finding in report.get("findings", []):
            if finding.get("severity") != "blocker":
                continue
            payload = {
                "severity": "blocker",
                "category": finding.get("rule", name),
                "section_id": finding.get("section_id"),
                "issue": finding.get("hint", ""),
                "suggestion": finding.get("hint", ""),
            }
            if language != i18n.DEFAULT:
                # D-173a: the short rule name in the memo language; the hint stays English.
                payload["issue_client"] = i18n.t(language, f"memo.rules.{finding.get('rule', name)}")
            issues.append(_normalize_issue(payload, DETERMINISTIC))
    return issues, stale


# --- state helpers shared by the review-loop commands (§2.2, §3.1) ---------


def publish_result(work_dir: Path, args: argparse.Namespace, canonical: str, payload: bytes) -> dict:
    """Write the payload into the attempt workspace and publish it through `stepctx` (§2.2, D-40)."""
    directory = stepctx.ensure_step_dir(work_dir, args.step, args.attempt, stepctx.CLI_SLOT)
    work_file = directory / Path(canonical).name
    state_io.write_bytes_atomic(work_file, payload)
    return stepctx.publish_file(work_dir, work_file, canonical, step_id=args.step)


def record_banner(state: dict, condition_key: str, **params: object) -> dict | None:
    """Append the banner of one `fallbacks.py` row to `state.fallback_banners` (§2.1), once (D-248)."""
    banner = fallbacks.banner(condition_key, **params)
    if banner is None:
        return None
    row = dict(banner, at=events.utc_now())
    if not fallbacks.same_banner(state.get("fallback_banners"), row):
        state.setdefault("fallback_banners", []).append(row)
    return row


def iteration_record(state: dict, iteration: int) -> dict | None:
    """The `iterations[]` entry of one iteration, or None (§2.2)."""
    for row in state.get("iterations", []):
        if isinstance(row, dict) and row.get("iteration") == iteration:
            return row
    return None


# --- aggregate (§4.5 п.3) --------------------------------------------------


def review_path(iteration: int, kind: str) -> str:
    """Canonical path of one reviewer output (§6)."""
    return f"reviews/v{iteration}-{kind}.json"


def mediator_path(iteration: int) -> str:
    """Canonical path of the mediator instructions (§6)."""
    return f"reviews/v{iteration}-mediator.json"


REVIEWER_JSON_RETRY = "reviewer_json_retry"
"""§2.2 counter `reviewer_json_retry{iteration,kind}`; `mf review aggregate` is its only writer (D-55)."""

MACHINE_FAILURE_REASON = "failure"
"""`steps[].reason` of the retry `mf next` issues for a failed dispatch slot (§2.2, `machine.REASON_FAILURE`)."""


def failure_retry_attempts(state: dict, iteration: int, kind: str) -> int:
    """`reason: failure` retries the machine issued for one reviewer kind of one iteration (D-65).

    Counted by the canonical review file rather than by `step_id`: `review aggregate` reruns a kind
    under a **new** `step_id` (D-32), which must not hand out a fresh retry budget.
    """
    target = review_path(int(iteration), str(kind))
    total = 0
    for row in state.get("steps") or []:
        if not isinstance(row, dict) or str(row.get("reason")) != MACHINE_FAILURE_REASON:
            continue
        for entry in row.get("expected_outputs") or []:
            if str(entry.get("canonical_path") or entry.get("canonical") or "") == target:
                total += 1
                break
    return total


def json_retry_used(state: dict, iteration: int, kind: str) -> int:
    """JSON retries already spent on one `(iteration, kind)` — the single count of D-69.

    Two callers issue them and both spend the same §2.2 budget: `mf review aggregate` writes
    `attempts.reviewer_json_retry{<iteration>:<kind>}` when the reviewer file is unusable, and
    `mf next` re-issues a slot the reviewer reported as failed (`reason: failure`). Reading only one
    of the two hands the pair a second retry, so the total is the sum — the machine's
    `budget_used`/`reviewer_failure_attempts` and `aggregate` both read it here.
    """
    counters = (state.get("attempts") or {}).get(REVIEWER_JSON_RETRY)
    spent = int(counters.get(f"{iteration}:{kind}", 0)) if isinstance(counters, dict) else 0
    return spent + failure_retry_attempts(state, iteration, kind)


def targeted_reviewers(state: dict, iteration: int) -> list[str]:
    """D-165: the reviewers a targeted citation pass dispatched for `iteration`, or `[]`.

    Branch 9 of §4.5 п.4 writes `state.targeted_fix` before the writer of v<N+1> is dispatched, so
    the aggregate of that one iteration expects exactly this set — the reviewers it deliberately
    skipped are not missing files and never become stubs or `failed_reviewers[]`.
    """
    row = state.get("targeted_fix")
    if not isinstance(row, dict) or int(row.get("iteration") or 0) != int(iteration):
        return []
    return [str(kind) for kind in (row.get("reviewers") or []) if kind in REVIEWER_KINDS]


def _stub_document(kind: str, reason: str, iteration: int, draft_sha: str | None) -> dict:
    stub = {"reviewer": kind, "status": "failed", "reason": reason, "iteration": iteration}
    if draft_sha:
        stub["draft_sha"] = draft_sha
    return stub


def _read_review(
    work_dir: Path,
    state: dict,
    iteration: int,
    kind: str,
    draft_sha: str | None,
    *,
    canonical: str | None = None,
) -> dict:
    canonical = canonical or review_path(iteration, kind)
    if not (work_dir / canonical).is_file():
        return {"valid": False, "errors": ["missing_review_file"], "stub": False, "document": None}
    try:
        # D-41: a valid schema and a matching `draft_sha` do not prove the bytes.
        document = stepctx.read_published(work_dir, canonical, state=state)
    except ValueError as exc:  # malformed JSON; a drift raises `OutputModifiedAfterPublish`
        return {"valid": False, "errors": [f"invalid_json: {exc}"], "stub": False, "document": None}
    if not isinstance(document, dict):
        return {"valid": False, "errors": ["review_not_an_object"], "stub": False, "document": None}
    return validate_document(kind, document, current_draft_sha=draft_sha, language=_memo_language(state))


def read_polish_recheck(work_dir: Path, state: dict, version: int, draft_sha: str) -> dict:
    """D-211: `reviews/v<N>-citations_polish.json`, read like a loop review and validated as `citations`.

    Same shape as `_read_review`. A file `published[]` does not list is no review at all, and a drifted
    one is answered as unusable rather than raised: the readiness step has no recovery branch for it.
    """
    canonical = review_path(int(version), POLISH_RECHECK)
    if stepctx.published_entry(state, canonical) is None:
        return {"valid": False, "errors": ["unpublished_review_file"], "stub": False, "document": None}
    try:
        return _read_review(work_dir, state, int(version), "citations", draft_sha, canonical=canonical)
    except stepctx.OutputModifiedAfterPublish:
        return {"valid": False, "errors": [stepctx.OUTPUT_MODIFIED], "stub": False, "document": None}


def run_aggregate(args: argparse.Namespace) -> dict:
    """`mf review aggregate` — validate, fold in deterministic blockers, write `iterations[N]` (§4.5 п.3)."""
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    iteration = args.iteration or int(state.get("current_iteration") or 1)
    args_key = f"review aggregate --iteration {iteration}"
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": list(identity["errors"]), "reason": identity.get("reason"), "step_id": args.step}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = identity.get("result")
        return stored if isinstance(stored, dict) else {"already_closed": True, "result": stored}

    config = state.get("config") or {}
    reviewers = targeted_reviewers(state, iteration) or list(config.get("reviewer_list") or REVIEWER_KINDS)
    draft_sha = state.get("current_draft_sha")
    retries = dict((state.get("attempts") or {}).get("reviewer_json_retry") or {})

    coverage: list[str] = []
    failed: list[str] = []
    retry: list[str] = []
    downgraded: list[str] = []
    stubs: list[dict] = []
    issues: list[dict] = []
    passed_items = 0
    total_items = 0

    try:
        reads = [(kind, _read_review(work_dir, state, iteration, kind, draft_sha)) for kind in reviewers]
        deterministic, stale = _deterministic_issues(work_dir, state, draft_sha)
    except stepctx.OutputModifiedAfterPublish as exc:  # D-41: changed after publication -> recovery
        return stepctx.drift_result(exc, iteration=iteration)

    for kind, result in reads:
        if result["valid"] and result.get("stub"):
            failed.append(kind)
            continue
        if result["valid"]:
            coverage.append(kind)
            if result.get("downgraded"):
                downgraded.append(kind)
            document = result["document"]
            for item in document.get("checklist", []):
                total_items += 1
                passed_items += 1 if item.get("pass") is True else 0
            for issue in document.get("issues", []):
                issues.append(_normalize_issue(issue, kind))
            continue

        key = f"{iteration}:{kind}"
        spent = int(retries.get(key, 0))
        # D-69: the retries `mf next` already issued for this pair come out of the same budget, so
        # the pair gets one dispatch after the initial one — never one here and one in the machine.
        if json_retry_used(state, iteration, kind) < limits.MAX_REVIEWER_JSON_RETRY:
            retries[key] = spent + 1
            retry.append(kind)
            continue
        reason = "; ".join(result["errors"])[:400] or "invalid reviewer output"
        stub = _stub_document(kind, f"invalid after reviewer_json_retry: {reason}", iteration, draft_sha)
        schema.validate_or_raise(stub, "review")
        stubs.append({"kind": kind, "document": stub, "canonical": review_path(iteration, kind)})
        failed.append(kind)

    if retry:
        result = {
            "next": "rerun_reviewers",
            "iteration": iteration,
            "retry_reviewers": retry,
            "reviewers": reviewers,
            "aggregated": False,
        }

        def retry_mutator(current: dict) -> None:
            current.setdefault("attempts", {})["reviewer_json_retry"] = retries

        stepctx.close_step(
            work_dir, args.step, args.attempt, result, args_key=args_key, mutate=retry_mutator
        )
        return result

    issues = deduplicate(deterministic + issues)

    published = [
        publish_result(work_dir, args, row["canonical"], state_io.dumps(row["document"]).encode("utf-8"))
        for row in stubs
    ]

    blockers = [issue for issue in issues if issue["severity"] == "blocker"]
    record = {
        "iteration": iteration,
        "draft_sha": draft_sha,
        "reviewers": reviewers,
        "coverage": sorted(coverage),
        "failed_reviewers": sorted(failed),
        "downgraded_reviewers": sorted(downgraded),
        "substance_blockers": len([i for i in blockers if i["tier"] == "substance"]),
        "form_blockers": len([i for i in blockers if i["tier"] == "form"]),
        "deterministic_blockers": len([i for i in blockers if DETERMINISTIC in i["provenance"]]),
        "pass_ratio": round(passed_items / total_items, 4) if total_items else 0.0,
        "conflict": any(issue.get("conflict") for issue in issues),
        "stale_reports": stale,
        "issues": issues,
        "aggregated_at": events.utc_now(),
    }

    def mutator(current: dict) -> None:
        current.setdefault("attempts", {})["reviewer_json_retry"] = retries
        rows = current.setdefault("iterations", [])
        for index, row in enumerate(rows):
            if isinstance(row, dict) and row.get("iteration") == iteration:
                rows[index] = record
                break
        else:
            rows.append(record)
        if stubs:
            record_banner(current, "reviewer_json_invalid", iteration=iteration, count=len(stubs))

    result = {
        "next": "revision_next",
        "iteration": iteration,
        "aggregated": True,
        "coverage": record["coverage"],
        "failed_reviewers": record["failed_reviewers"],
        "substance_blockers": record["substance_blockers"],
        "form_blockers": record["form_blockers"],
        "deterministic_blockers": record["deterministic_blockers"],
        "pass_ratio": record["pass_ratio"],
        "conflict": record["conflict"],
        "stale_reports": stale,
        "stubs_written": [row["canonical"] for row in stubs],
        "issues": len(issues),
    }
    stepctx.close_step(
        work_dir, args.step, args.attempt, result, args_key=args_key, published=published, mutate=mutator
    )
    return result


# --- mediator assembled by the CLI (§4.5 п.4.6, п.5) ----------------------


MINOR_DROP_REASON = "minor finding; substance blockers take priority this iteration"
TARGETED_DROP_REASON = "targeted pass: only the named blockers are fixed"
"""D-212: why a branch-9 mediator drops every issue it does not name."""


def _evidence_text(issue: dict) -> str:
    """D-239: the saved passage of `source_evidence`, for a writer who never reads the raw texts; else ''."""
    evidence = issue.get("source_evidence")
    if not isinstance(evidence, dict) or not evidence.get("passage"):
        return ""
    return f" Source text ({evidence.get('source_id')}): «{evidence['passage']}»"


def build_mediator(record: dict, *, only: list[dict] | None = None) -> dict:
    """Assemble `reviews/v<N>-mediator.json` from aggregated issues: substance first, minors dropped.

    D-212: with `only` (branch 9), the instructions are exactly those issues and every other issue of
    the record, major or minor, is dropped with `TARGETED_DROP_REASON`. D-239: an issue that carries a
    `source_evidence` passage ends its instruction with it.
    """
    instructions: list[dict] = []
    dropped: list[dict] = []
    ordered = sorted(
        record.get("issues", []),
        key=lambda issue: (
            0 if issue.get("tier") == "substance" else 1,
            SEVERITY_RANK.get(issue.get("severity", "minor"), 9),
            issue.get("section_id", ""),
            issue.get("category", ""),
            issue.get("issue", ""),
        ),
    )
    for issue in ordered:
        if only is not None:
            reason = None if issue in only else TARGETED_DROP_REASON
        else:
            reason = MINOR_DROP_REASON if issue.get("severity") == "minor" else None
        if reason:
            dropped.append(
                {
                    "section_id": issue["section_id"],
                    "source_reviewer": issue["source_reviewer"],
                    "category": issue["category"],
                    "severity": issue["severity"],
                    "issue": issue["issue"],
                    "reason": reason,
                }
            )
            continue
        instruction = {
            "section_id": issue["section_id"],
            "source_reviewer": issue["source_reviewer"],
            "category": issue["category"],
            "severity": issue["severity"],
            "instruction": " ".join(part for part in (issue.get("issue"), issue.get("suggestion")) if part)
            + _evidence_text(issue),
        }
        if issue.get("conflict"):
            instruction["resolution"] = (
                "Reviewers disagreed on this section; the substance finding above is the one to apply."
            )
        instructions.append(instruction)

    document: dict = {"instructions": instructions, "dropped": dropped}
    if record.get("iteration"):
        document = {"iteration": record["iteration"], **document}
    if record.get("draft_sha"):
        document["draft_sha"] = record["draft_sha"]
    return document


def run_mediator_from_issues(args: argparse.Namespace) -> dict:
    """`mf review mediator-from-issues` — build the mediator file without dispatching an agent (§4.5 п.4.6)."""
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    iteration = args.iteration or int(state.get("current_iteration") or 1)
    args_key = f"review mediator-from-issues --iteration {iteration}"
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": list(identity["errors"]), "reason": identity.get("reason"), "step_id": args.step}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = identity.get("result")
        return stored if isinstance(stored, dict) else {"already_closed": True, "result": stored}

    record = iteration_record(state, iteration)
    if record is None:
        return {"errors": [f"no_aggregate_for_iteration: {iteration}"], "iteration": iteration}

    document = build_mediator(record)
    schema.validate_or_raise(document, "mediator")
    canonical = mediator_path(iteration)
    entry = publish_result(work_dir, args, canonical, state_io.dumps(document).encode("utf-8"))

    result = {
        "iteration": iteration,
        "path": canonical,
        "sha256": entry["sha256"],
        "instructions": len(document["instructions"]),
        "dropped": len(document["dropped"]),
        "by": "cli",
    }
    stepctx.close_step(
        work_dir, args.step, args.attempt, result, args_key=args_key, published=[entry]
    )
    return result


# --- open substantive majors at the loop exit (D-210) ----------------------

OPEN_MAJOR_CLASSES: tuple[str, ...] = ("logic", "citations", "counterarguments")
"""D-210: the substance reviewers whose open majors reach the last reader; `form` is not one of them."""


MOVED_STATUSES: tuple[str, ...] = ("manual_review", "unresolved")
"""D-211/D-237: the statuses of the loop rows the readiness settlement moves into `remaining_blocking_issues`."""


def is_moved_row(row: object) -> bool:
    """D-237: a loop row of `OPEN_MAJOR_CLASSES` the settlement moves: `manual_review`/`unresolved`, no blocker row."""
    return (
        isinstance(row, dict)
        and row.get("origin") == "loop"
        and row.get("class") in OPEN_MAJOR_CLASSES
        and row.get("status") in MOVED_STATUSES
        and not isinstance(row.get("blocker_of"), dict)
    )


def moved_finding(row: dict) -> dict:
    """A settled loop row as a `remaining_blocking_issues[]` entry: `severity: major`, its client sentence.

    D-237: any class of `OPEN_MAJOR_CLASSES` moves, and `source_reviewer` names it. The
    `disposition_note` of a `manual_review` row — the question the lawyer must answer — comes along
    for `summary.md`; an English deliverable prints it for a `manual_review` row (D-252); another
    language keeps its localized line.
    """
    entry = {
        "severity": "major",
        "category": str(row.get("category") or ""),
        "section_id": str(row.get("section_id") or UNVERIFIED_SECTION_ID),
        "issue": str(row.get("issue") or ""),
        "suggestion": str(row.get("suggestion") or ""),
        "source_reviewer": str(row.get("reviewer") or row.get("class") or "citations"),
    }
    for field in ("issue_category", "issue_client", "disposition_note"):
        if row.get(field):
            entry[field] = row[field]
    if row.get("status") in MOVED_STATUSES:
        # D-252: which disposition moved it; the deliverable prints the note only of a `manual_review` row.
        entry["disposition"] = str(row["status"])
    return entry


def moved_finding_key(item: object) -> tuple[str, str, str] | None:
    """D-237: `(section_id, category, issue)` — what a moved `remaining_blocking_issues[]` entry keeps of its row.

    The identity of a moved open major whatever build moved it: an entry an earlier build wrote lacks the
    fields added since (`disposition_note`), so a whole-dict comparison cannot recognise it.
    """
    if not isinstance(item, dict):
        return None
    return (
        str(item.get("section_id") or UNVERIFIED_SECTION_ID),
        str(item.get("category") or ""),
        str(item.get("issue") or ""),
    )


def _stands_for(item: object, entry: dict) -> bool:
    """`item` of the list can be `entry`'s: the same key, and every field both carry agrees."""
    return (
        isinstance(item, dict)
        and moved_finding_key(item) == moved_finding_key(entry)
        and all(entry[field] == value for field, value in item.items() if field in entry)
    )


def pair_moved_entries(entries: list[dict], remaining: object) -> list[int | None]:
    """D-237: for each moved entry, the index of the `remaining_blocking_issues[]` item that already is it, or None.

    One-to-one (final fix wave): an item is claimed by at most one entry, so two rows of one finding (a major
    the targeted pass repeated, D-235) keep two entries with their own notes. An item equal to the entry is
    claimed first; then an item that agrees on every field it carries — an entry an earlier build moved,
    without the fields added since, is claimed and enriched instead of duplicated.
    """
    items = remaining if isinstance(remaining, list) else []
    pairs: list[int | None] = [None] * len(entries)
    claimed: set[int] = set()
    for exact in (True, False):
        for position, entry in enumerate(entries):
            if pairs[position] is not None:
                continue
            for index, item in enumerate(items):
                if index not in claimed and (item == entry if exact else _stands_for(item, entry)):
                    pairs[position] = index
                    claimed.add(index)
                    break
    return pairs


def _section_order(section_id: object) -> list:
    """Document order of section ids: their numbers compare as numbers, so `s-9` comes before `s-10-3`."""
    parts = re.split(r"(\d+)", str(section_id or ""))
    return [int(part) if index % 2 else part for index, part in enumerate(parts)]


def open_substance_majors(state: dict, version: int) -> list[dict]:
    """D-210: the substantive majors still open on `version`, one row per stored issue.

    Only the records up to `version` count. For each class the superseding record is the latest of
    them whose `coverage` holds it, so a class that failed later, or that a targeted pass did not
    re-run, keeps the majors it raised last: absence from a review is not closure. A row's `class` is
    a participant this record supersedes for, the first by `SOURCE_PRECEDENCE` (`citations` when it
    holds); `reviewer` is the stored `source_reviewer`. Rows run by `from_iteration`, section, position.
    D-235: the targeted pass (`state.targeted_fix.iteration`) supersedes the earlier majors of the
    classes it covers only in the sections of the blockers it targeted, which its writer rewrote and
    its reviewer re-read; every other major stays a row, so a major it repeats in an untouched section
    gives two rows, and a merged issue there keeps its `citations` class.
    """
    from . import revision  # noqa: PLC0415 - `revision` imports this module

    fix = state.get("targeted_fix")
    targeted = int(fix.get("iteration") or 0) if isinstance(fix, dict) else 0
    records = sorted(
        (
            row
            for row in state.get("iterations") or []
            if isinstance(row, dict) and 0 < int(row.get("iteration") or 0) <= int(version)
        ),
        key=lambda row: int(row["iteration"]),
    )
    rewritten: set[str] = set()
    covered_by_targeted: set[str] = set()
    for record in records:
        if int(record["iteration"]) == targeted:
            covered_by_targeted = set(OPEN_MAJOR_CLASSES) & set(record.get("coverage") or [])
            earlier = [item for item in records if int(item["iteration"]) < targeted]
            if earlier:
                rewritten = {str(issue.get("section_id") or "") for issue in revision.targeted_blockers(earlier[-1])}
    superseding: dict[str, dict] = {}
    for record in records:
        if int(record["iteration"]) == targeted:
            continue  # D-235: the targeted pass supersedes only in the sections it rewrote (below)
        for kind in OPEN_MAJOR_CLASSES:
            if kind in (record.get("coverage") or []):
                superseding[kind] = record

    found: list[tuple[tuple, dict]] = []
    for record in records:
        iteration = int(record["iteration"])
        if iteration == targeted:
            owned = covered_by_targeted
        else:
            owned = {kind for kind, latest in superseding.items() if latest is record}
        if not owned:
            continue
        for position, issue in enumerate(record.get("issues") or []):
            if not isinstance(issue, dict) or issue.get("severity") != "major" or issue.get("tier") != "substance":
                continue
            live = revision._participants(issue) & owned  # noqa: SLF001 - the loop's own participant rule
            if iteration != targeted and str(issue.get("section_id") or "") in rewritten:
                live -= covered_by_targeted  # D-235: re-graded by the targeted pass
            if not live:
                continue
            row = {
                "class": next(source for source in SOURCE_PRECEDENCE if source in live),
                "reviewer": str(issue.get("source_reviewer") or ""),
                "section_id": str(issue.get("section_id") or UNVERIFIED_SECTION_ID),
                "category": str(issue.get("category") or ""),
                "issue_category": issue.get("issue_category"),
                "issue": str(issue.get("issue") or ""),
                "issue_client": issue.get("issue_client"),
                "suggestion": str(issue.get("suggestion") or ""),
                "from_iteration": iteration,
                "origin": "loop",
                "status": "open",
            }
            found.append(((iteration, _section_order(row["section_id"]), position), row))
    found.sort(key=lambda item: item[0])
    return [{"id": f"om-{number}", **row} for number, (_, row) in enumerate(found, start=1)]


def blocker_rows(record: dict, start: int) -> list[dict]:
    """D-213: one open-finding row per targeted blocker of `record`, numbered `om-<start>`… in record order.

    The row is a `citations` row of the loop with `severity: blocker` and `blocker_of` — the section,
    category and text of the `remaining_blocking_issues[]` entry it stands for, which only a clean
    re-check of the final polish removes at the settlement.
    """
    from . import revision  # noqa: PLC0415 - `revision` imports this module

    rows = []
    for number, issue in enumerate(revision.targeted_blockers(record), start=int(start)):
        rows.append(
            {
                "id": f"om-{number}",
                "class": "citations",
                "reviewer": str(issue.get("source_reviewer") or ""),
                "section_id": str(issue.get("section_id") or UNVERIFIED_SECTION_ID),
                "category": str(issue.get("category") or ""),
                "issue_category": issue.get("issue_category"),
                "issue": str(issue.get("issue") or ""),
                "issue_client": issue.get("issue_client"),
                "suggestion": str(issue.get("suggestion") or ""),
                "from_iteration": int(record.get("iteration") or 1),
                "origin": "loop",
                "status": "open",
                "severity": "blocker",
                "blocker_of": blocker_link(issue),
            }
        )
    return rows


def blocker_link(issue: dict) -> dict:
    """D-213: what ties a blocker row to its `remaining_blocking_issues[]` entry — section, category, text."""
    return {field: str(issue.get(field) or "") for field in ("section_id", "category", "issue")}


# --- carry-over of the pairs no text check reached (D-214) -----------------

CARRY_OVER_LAYERS: frozenset[str] = frozenset({"statutes", "case_law"})
"""D-214: only a statute or a court act is checked against its saved text pair by pair."""


def _counted_citations_review(work_dir: Path, state: dict, iteration: int) -> dict | None:
    """D-214 (gate R1-7): the citations review of `iteration`, or None when it does not count.

    It counts only when `published[]` lists it, its bytes are the published ones, it is not a stub, and
    it validates against the draft sha of **its own** iteration — never the current one.
    """
    canonical = review_path(int(iteration), "citations")
    record = iteration_record(state, int(iteration)) or {}
    draft_sha = str(record.get("draft_sha") or "")
    if not draft_sha or stepctx.published_entry(state, canonical) is None:
        return None
    try:
        result = _read_review(work_dir, state, int(iteration), "citations", draft_sha)
    except stepctx.OutputModifiedAfterPublish:
        return None
    if not result.get("valid") or result.get("stub"):
        return None
    document = result.get("document")
    return document if isinstance(document, dict) else None


def unchecked_pairs(work_dir: Path, state: dict, iteration: int, draft_text: str) -> list[dict]:
    """D-214: the cited statute and case-law pairs of the draft that no earlier text check reached.

    A pair is the `(source_id, section_id)` of a `[[src:]]` token. `not_reached`: its latest `text_checks`
    row across the counted citations reviews of iterations before `iteration` says so; `never_checked`:
    no counted review has a row for it. `not_reached` first, then `never_checked`, each with critical sources
    first, then in section order and by source id (D-253), at most `limits.CARRY_OVER_MAX`. Iteration 1
    carries nothing.
    """
    if int(iteration) <= 1:
        return []
    try:
        registry = sources.read_registry(work_dir).get("sources") or {}
    except (OSError, ValueError):
        return []
    document = lint.parse_draft(str(draft_text or ""), lint.grammar(_memo_language(state)))
    cited: list[tuple[str, str]] = []
    for token in document["src_tokens"]:
        pair = (str(token.get("id") or ""), str(token.get("section_id") or ""))
        record = registry.get(pair[0])
        if not all(pair) or pair in cited or not isinstance(record, dict):
            continue
        if record.get("layer") in CARRY_OVER_LAYERS:
            cited.append(pair)
    latest: dict[tuple[str, str], str] = {}
    for earlier in range(1, int(iteration)):
        counted = _counted_citations_review(work_dir, state, earlier) or {}
        for row in counted.get("text_checks") or []:
            if isinstance(row, dict):
                latest[(str(row.get("source_id") or ""), str(row.get("section_id") or ""))] = str(row.get("status"))

    def order(pair: tuple[str, str]) -> tuple:
        # D-253: a critical source before a supporting one, then section order and source id (D-214).
        tier = (registry.get(pair[0]) or {}).get("tier")
        return (0 if tier == "critical" else 1, _section_order(pair[1]), pair[0])

    rows = [
        {"source_id": source_id, "section_id": section_id, "reason": reason}
        for reason in ("not_reached", "never_checked")
        for source_id, section_id in sorted(cited, key=order)
        if (latest.get((source_id, section_id)) or "never_checked") == reason
    ]
    return rows[: limits.CARRY_OVER_MAX]


# --- validate (§4.5 п.2) ---------------------------------------------------


def run_validate(args: argparse.Namespace) -> dict:
    """`mf review validate --kind <k> --path <file>` — structural + §4.5 п.2 rules."""
    path = Path(args.path)
    if args.workdir and not path.is_absolute():
        path = Path(args.workdir) / args.path
    if not path.is_file():
        return {"errors": [f"missing_file: {args.path}"], "kind": args.kind}

    draft_sha = args.draft_sha
    language = i18n.DEFAULT
    if args.workdir:
        state = state_io.read_state_or_none(args.workdir)
        if draft_sha is None:
            draft_sha = (state or {}).get("current_draft_sha")
        language = _memo_language(state)

    try:
        document = state_io.read_json(path)
    except ValueError as exc:
        return {"errors": [f"invalid_json: {exc}"], "kind": args.kind, "path": args.path}
    if not isinstance(document, dict):
        return {"errors": ["review_not_an_object"], "kind": args.kind, "path": args.path}

    result = validate_document(
        args.kind,
        document,
        current_draft_sha=draft_sha,
        iteration=args.iteration,
        language=language,
    )
    return {
        "kind": args.kind,
        "path": args.path,
        "valid": result["valid"],
        "errors": result["errors"],
        "downgraded": result["downgraded"],
        "stub": result["stub"],
        "blockers": result["blockers"],
        "unverified_hard_fail": result["unverified_hard_fail"],
        "verdict": result["document"].get("verdict"),
        "document": result["document"],
    }


def register(subparsers) -> None:
    """Register the `review` command group."""
    from . import cli

    group = cli.group_subparsers(subparsers, "review", "review loop (validate/aggregate/mediator)")

    validate = group.add_parser("validate", help="validate one review document against schema + §4.5")
    validate.add_argument("--workdir", default=None)
    validate.add_argument("--kind", required=True, choices=list(KINDS))
    validate.add_argument("--path", required=True, help="review file (absolute or relative to --workdir)")
    validate.add_argument("--iteration", type=int, default=None)
    validate.add_argument("--draft-sha", dest="draft_sha", default=None)
    validate.set_defaults(func=run_validate)

    aggregate = group.add_parser("aggregate", help="aggregate one iteration of reviews")
    aggregate.add_argument("--workdir", required=True)
    aggregate.add_argument("--iteration", type=int, default=None)
    aggregate.add_argument("--step", required=True)  # D-40: identity is checked, never skipped
    aggregate.add_argument("--attempt", type=int, default=1)
    aggregate.set_defaults(func=run_aggregate)

    mediator = group.add_parser("mediator-from-issues", help="build the mediator file from aggregated issues")
    mediator.add_argument("--workdir", required=True)
    mediator.add_argument("--iteration", type=int, default=None)
    mediator.add_argument("--step", required=True)  # D-40
    mediator.add_argument("--attempt", type=int, default=1)
    mediator.set_defaults(func=run_mediator_from_issues)
