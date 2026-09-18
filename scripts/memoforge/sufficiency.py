"""`mf sufficiency route` — phase 6 routing over `research/research-sufficiency.json` (ТЗ §2.1 стр.6–7)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import events, i18n, limits, review, schema, state_io, stepctx

SUFFICIENCY_PATH = "research/research-sufficiency.json"

LAYERS: tuple[str, ...] = ("statutes", "case_law", "doctrine")
"""Gap targets that re-dispatch a researcher; `user` targets form subset_u (D-01, §2.1 стр.6)."""

OUT_OF_SCOPE_CODE = "out_of_scope_research_gap"
"""`drafting_warnings[].code` of a gap in a layer the mode does not research (D-112)."""

NEXT_CURRENCY = "currency_check"
NEXT_GATE = "gate_followup"
NEXT_RESEARCH = "research_subset"
NEXT_INSUFFICIENT = "insufficient_gate"

APPROVED_LAYERS = "approved_layers"
"""`sufficiency_followup[]` key holding the layers this route paid for (D-116, N-01).

The router is the only place where the research budget is charged, so it is also the only place
that may name the layers phase 5 re-dispatches. The set is written down instead of being re-derived
downstream from `subset_r`: with the user budget left and the research budget spent, `route`
answers `layers: []` while `subset_r` still lists the gap, and a consumer that re-derived the set
from the subset bought a research pass nobody paid for.
"""


def in_scope_layers(layers: list[str] | None = None) -> tuple[str, ...]:
    """`config.researcher_layers` as a gap scope; every layer when the caller names none (D-112)."""
    rows = tuple(str(layer) for layer in (layers or []) if layer in LAYERS)
    return rows or LAYERS


def partition(document: dict, layers: list[str] | None = None) -> dict:
    """Split `blocking_gaps` into subset_u / subset_r and by `missing`/`weak` status (§2.1 стр.6).

    D-112: a gap aimed at a layer this mode does not research is out of scope — it leaves
    subset_r for `out_of_scope`, so the follow-up never widens Brief into Full.
    """
    scope = in_scope_layers(layers)
    subset_u: list[dict] = []
    subset_r: list[dict] = []
    out_of_scope: list[dict] = []
    for gap in document.get("blocking_gaps", []):
        if gap.get("target") == "user":
            subset_u.append(gap)
        elif gap.get("target") in scope:
            subset_r.append(gap)
        elif gap.get("target") in LAYERS:
            out_of_scope.append(gap)
    return {
        "subset_u": subset_u,
        "subset_r": subset_r,
        "out_of_scope": out_of_scope,
        "missing_layers": sorted({gap["target"] for gap in subset_r if gap.get("status") == "missing"}),
        "weak_layers": sorted({gap["target"] for gap in subset_r if gap.get("status") == "weak"}),
    }


def _warning(gap: dict) -> dict:
    text = str(gap.get("gap", "")).strip()
    why = str(gap.get("why_blocking", "")).strip()
    return {
        "code": "unresolved_research_gap",
        "message": f"{text} {why}".strip(),
        "phase": "research_sufficiency",
        "at": events.utc_now(),
    }


def _first_sentence(text: str) -> str:
    """Normalised opening sentence — the key that says two warnings carry the same fact (D-113)."""
    head = re.split(r"(?<=[.!?])\s", str(text).strip(), maxsplit=1)[0]
    return " ".join(head.casefold().split()).rstrip(".!?")


def _warnings(document: dict, gaps: list[dict]) -> list[dict]:
    """One warning per gap plus the reviewer's own, with the pairs of D-113 collapsed.

    D-113 (addendum to D34-17): the reviewer states the same limitation twice — once as
    `blocking_gaps[].gap`, once as a `drafting_warnings[]` line written for the client. Where the
    first sentence is the same text, only the client-facing line survives.
    """
    reviewer = [str(text) for text in document.get("drafting_warnings", [])]
    already_said = {_first_sentence(text) for text in reviewer}
    rows = [
        row for row in (_warning(gap) for gap in gaps)
        if _first_sentence(row["message"]) not in already_said
    ]
    for text in reviewer:
        rows.append(
            {
                "code": "sufficiency_warning",
                "message": text,
                "phase": "research_sufficiency",
                "at": events.utc_now(),
            }
        )
    return rows


def _out_of_scope_warnings(
    document: dict, gaps: list[dict], mode: str, language: object = "en"
) -> list[dict]:
    """D-112: one warning per gap outside the mode's layers, so the writer caveats it instead."""
    code = i18n.normalize(language) or i18n.DEFAULT
    if mode:
        prefix = i18n.t(code, "memo.warnings.out_of_scope_prefix", mode=mode)
    else:
        prefix = i18n.t(code, "memo.warnings.out_of_scope_prefix_no_mode")
    texts = [f"{str(gap.get('gap', '')).strip()} {str(gap.get('why_blocking', '')).strip()}".strip() for gap in gaps]
    texts += [str(text).strip() for text in document.get("out_of_scope_gaps") or []]
    return [
        {
            "code": OUT_OF_SCOPE_CODE,
            "message": prefix + text,
            "phase": "research_sufficiency",
            "at": events.utc_now(),
        }
        for text in texts
        if text
    ]


def _questions(gaps: list[dict]) -> list[dict]:
    return [gap["followup_question"] for gap in gaps if gap.get("followup_question")]


def _decision(next_step: str, layers: list[str], *, user: bool, research: bool, warn: list[dict], parts: dict) -> dict:
    return {
        "next": next_step,
        "layers": layers,
        "spend_user_budget": user,
        "spend_research_budget": research,
        "spend_budget": user or research,
        "warn_gaps": warn,
        **parts,
    }


def route(
    document: dict,
    *,
    user_followup_used: int = 0,
    research_followup_used: int = 0,
    layers: list[str] | None = None,
    mode: object = None,
) -> dict:
    """Pure routing of §2.1 стр.6 — first matching outcome wins.

    `layers` is `config.researcher_layers`: gaps outside it never reach `missing_layers`, so an
    empty intersection continues the run instead of dispatching a second research pass (D-112).

    D-116: asking the user and re-dispatching a researcher are two different purchases — a question
    costs ~100 s of waiting, a research pass half an hour — so they draw on two counters. A user gap
    never consumes the research budget, and a run whose research budget is gone can still ask.
    """
    verdict = document.get("overall_verdict")
    parts = partition(document, layers)
    user_left = user_followup_used < limits.MAX_SUFFICIENCY_USER_FOLLOWUP
    research_left = research_followup_used < limits.research_followup_limit(mode)

    if verdict == "sufficient":
        return _decision(NEXT_CURRENCY, [], user=False, research=False, warn=[], parts=parts)
    if verdict == "insufficient":
        return _decision(NEXT_INSUFFICIENT, [], user=False, research=False, warn=[], parts=parts)

    if parts["subset_u"] and user_left:
        # The gate is opened on the user budget; the layers ride along only if research is still
        # affordable, and then — and only then — the research counter moves.
        target = parts["missing_layers"] if research_left else []
        return _decision(NEXT_GATE, target, user=True, research=bool(target), warn=[], parts=parts)

    if parts["missing_layers"] and research_left:
        # The user budget is spent but the research one is not: repeat the layer and caveat the
        # unanswered user gaps in the draft.
        return _decision(
            NEXT_RESEARCH, parts["missing_layers"], user=False, research=True, warn=parts["subset_u"], parts=parts
        )

    # Nothing affordable is left (or only weak/empty subsets): warnings, then the currency check.
    return _decision(
        NEXT_CURRENCY, [], user=False, research=False, warn=parts["subset_u"] + parts["subset_r"], parts=parts
    )


def _commit(work_dir: Path, args: argparse.Namespace, attempt: int, result: dict, mutate) -> None:
    """The one state write of `route`: the step close with the mutation, or the mutation alone (D-40)."""
    if args.step:
        stepctx.close_step(work_dir, args.step, attempt, result, mutate=mutate)
    else:
        state_io.write_state(work_dir, mutate)


def run_route(args: argparse.Namespace) -> dict:
    """`mf sufficiency route --step` — write `sufficiency_followup{}` / warnings and answer the router."""
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

    path = work_dir / SUFFICIENCY_PATH
    document: dict | None = None
    errors: list[str] = []
    if not path.is_file():
        errors = ["missing_sufficiency_file"]
    else:
        try:
            # D-41: the routing decision rests on the published bytes; a file edited after
            # publication is a recovery case, never a new verdict (§2.2).
            document = stepctx.read_published(work_dir, SUFFICIENCY_PATH, state=state)
        except stepctx.OutputModifiedAfterPublish as exc:
            return stepctx.drift_result(exc)
        except ValueError as exc:
            errors = [f"invalid_json: {exc}"]
        else:
            if not isinstance(document, dict):
                errors = ["sufficiency_not_an_object"]
            else:
                errors = schema.validate(document, "research-sufficiency")

    if errors:
        # §2.1 стр.6: an unusable reviewer output degrades to `insufficient`, never to `sufficient`.
        def failure_mutator(current: dict) -> None:
            review.record_banner(current, "sufficiency_reviewer_failed")
            current.setdefault("drafting_warnings", []).append(
                {
                    "code": "sufficiency_unavailable",
                    "message": "; ".join(errors)[:400],
                    "phase": "research_sufficiency",
                    "at": events.utc_now(),
                }
            )
            current["sufficiency_followup"] = {"status": "unavailable", APPROVED_LAYERS: []}

        result = {
            "next": NEXT_INSUFFICIENT,
            "layers": [],
            "verdict": "insufficient",
            "degraded": True,
            "errors_seen": errors,
        }
        _commit(work_dir, args, attempt, result, failure_mutator)
        return result

    attempts = state.get("attempts") or {}
    user_used = int(attempts.get("sufficiency_user_followup", 0))
    research_used = int(attempts.get("sufficiency_research_followup", 0))
    config = state.get("config") or {}
    decision = route(
        document,
        user_followup_used=user_used,
        research_followup_used=research_used,
        layers=config.get("researcher_layers"),
        mode=state.get("mode"),
    )
    carry = decision["next"] == NEXT_CURRENCY or bool(decision["warn_gaps"])
    warnings = _warnings(document, decision["warn_gaps"]) if carry else []
    # D-112: the out-of-scope gaps are carried on every route, including the one that opens the
    # gate — they are never re-researched, so there is no later pass that would rediscover them.
    out_of_scope = _out_of_scope_warnings(
        document,
        decision["out_of_scope"],
        str(state.get("mode") or ""),
        state.get("language"),
    )
    warnings += out_of_scope
    unresolved_user_gaps = any(gap.get("target") == "user" for gap in decision["warn_gaps"])

    questions = _questions(decision["subset_u"])
    status = {
        NEXT_GATE: "pending",
        NEXT_RESEARCH: "research_subset",
        NEXT_CURRENCY: "resolved" if document["overall_verdict"] == "sufficient" else "closed_with_warnings",
        NEXT_INSUFFICIENT: "insufficient",
    }[decision["next"]]

    def mutator(current: dict) -> None:
        # D-116: two counters, charged independently — a question to the user leaves the research
        # budget untouched and vice versa.
        counters = current.setdefault("attempts", {})
        if decision["spend_user_budget"]:
            counters["sufficiency_user_followup"] = user_used + 1
        if decision["spend_research_budget"]:
            counters["sufficiency_research_followup"] = research_used + 1
        current["sufficiency_followup"] = {
            "status": status,
            "subset_u": decision["subset_u"],
            "subset_r": decision["subset_r"],
            # D-116 / N-01: exactly the layers this decision paid for — `[]` when the research
            # budget is gone, even though `subset_r` still names the gap for the warnings.
            APPROVED_LAYERS: list(decision["layers"]),
            "questions": questions,
            "user_response": (current.get("sufficiency_followup") or {}).get("user_response"),
            "asked_at": events.utc_now() if decision["next"] == NEXT_GATE else None,
            "answered_at": (current.get("sufficiency_followup") or {}).get("answered_at"),
        }
        # D-112: two passes of the reviewer report the same gap, and only `at` differs — one
        # warning per gap means comparing what the writer reads, not the timestamp.
        rows = current.setdefault("drafting_warnings", [])
        seen = {(row.get("code"), row.get("message")) for row in rows if isinstance(row, dict)}
        for warning in warnings:
            key = (warning["code"], warning["message"])
            if warning["message"] and key not in seen:
                rows.append(warning)
                seen.add(key)
        if unresolved_user_gaps:
            review.record_banner(current, "sufficiency_subset_u_unresolved")

    result = {
        "next": decision["next"],
        "layers": decision["layers"],
        "verdict": document["overall_verdict"],
        "subset_u": len(decision["subset_u"]),
        "subset_r": len(decision["subset_r"]),
        "questions": len(questions),
        "out_of_scope_gaps": len(out_of_scope),
        "drafting_warnings_added": len(warnings),
        "budget_spent": bool(decision["spend_budget"]),
        "user_budget_spent": bool(decision["spend_user_budget"]),
        "research_budget_spent": bool(decision["spend_research_budget"]),
        "sufficiency_user_followup_used": user_used + (1 if decision["spend_user_budget"] else 0),
        "sufficiency_research_followup_used": research_used + (1 if decision["spend_research_budget"] else 0),
        "degraded": False,
    }
    _commit(work_dir, args, attempt, result, mutator)
    return result


def register(subparsers) -> None:
    """Register the `sufficiency` command group."""
    from . import cli

    group = cli.group_subparsers(subparsers, "sufficiency", "research sufficiency routing (§2.1 стр.6)")

    parser = group.add_parser("route", help="route phase 6 from research/research-sufficiency.json")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--step", default=None)
    parser.add_argument("--attempt", type=int, default=1)
    parser.set_defaults(func=run_route)
