"""The single gate parser and the CLI-generated gate prompts (ТЗ §2.4, M8)."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from . import events, fallbacks, i18n, limits, modes, routing, state_io, stepctx

GATES: tuple[str, ...] = ("intake", "plan", "sufficiency_followup", "insufficient", "source_review")
"""Gate names of the `gate-answers` schema; one per gate phase of §2.4."""

GATE_BY_PHASE: dict[str, str] = {
    "intake_questions_pending": "intake",
    "plan_approval_pending": "plan",
    "research_sufficiency_followup_pending": "sufficiency_followup",
    "research_insufficient_pending": "insufficient",
    "source_review_pending": "source_review",
}

PHASE_BY_GATE: dict[str, str] = {gate: phase for phase, gate in GATE_BY_PHASE.items()}

KIND_GATE_TEXT = "gate-text"
KIND_GATE_AUQ = "gate-auq"
"""`steps[].kind` of the two gate channels of §2.4; `machine.py` re-exports these names."""

QUESTION_GATES: tuple[str, ...] = ("intake", "sufficiency_followup")
"""Gates whose reply is `1A 2C 3: free text` (§2.4)."""

OPTION_LETTERS = "ABCD"

USER_FACTS_PATH = "intake/user-facts.md"
FOLLOWUP_RESPONSE_PATH = "intake/followup-response.json"
QUESTIONS_PATH = "intake/questions.json"
PLAN_PATH = "plan.json"
MCP_PROBE_PATH = "intake/mcp-probe.json"

MODE_SUMMARY: dict[str, str] = {
    "brief": i18n.t("en", "ui.gates.mode_summary_brief"),
    "full": i18n.t("en", "ui.gates.mode_summary_full"),
}
"""The two `Mode` options of gate 4 in English; the plan digest names the recommended one with the
same words. A gate rendered in another interface language reads `ui.gates.mode_summary_<mode>` of
that pack instead (D-176)."""

# D-86: how much of the plan the gate-4 digest prints before it points at `plan.json` instead.
PLAN_DIGEST_DETAIL_ISSUES = 8
PLAN_DIGEST_MAX_ISSUES = 25
PLAN_DIGEST_NOTES_CHARS = 300

# D34-01: a plan this size is not refused — nothing caps `plan.issues` — but the gate says in one
# sentence what Brief would do with it, in the digest and in the Brief option itself
# (`ui.gates.brief_mismatch_hint_one` / `_many`).
BRIEF_HINT_MAX_ISSUES = 3

STANDARD_STYLE = "standard"
"""The built-in house style. A text-channel token of §2.4: never translated (D-176a)."""

AUQ_CANONICAL = "canonical"
"""Key of the reverse map inside `build_auq`'s answer — in-process only, dropped by `run_render`."""

CANONICAL_UI: tuple[tuple[str, str], ...] = (
    ("header_plan", "Plan"),
    ("header_mode", "Mode"),
    ("header_style", "Style"),
    ("header_sources", "Sources"),
    ("option_approve", "Approve"),
    ("option_edit", "Edit"),
    ("option_cancel", "Cancel"),
    ("option_brief", "Brief"),
    ("option_full", "Full"),
    ("option_continue", "Continue"),
)
"""D-176a: every AUQ header and option label the machine reads, with its canonical English value.

`canonical_map` renders the left column in one interface language and maps it back to the right
one, so nothing below `parse_auq` — `machine.py`, `state.json`, the events — ever sees a
translated answer.
"""

# §2.2 `gate_parse_errors`: what an exhausted budget does, per gate.
EXHAUSTED_DEFAULT: dict[str, str] = {
    "intake": "proceed",
    "sufficiency_followup": "proceed",
    "plan": "cancel",
    "source_review": "continue",
    "insufficient": "cancel",
}

# §2.4 `1A 2C 3: free text`: the first `<n>:`/`<n>.` marker of a line opens the free-text answer,
# which runs to the end of that line; everything before it is still tokenised as letter answers.
_FREE_TEXT_ANSWER = re.compile(r"(?:^|\s)(0*\d{1,2})\s*[:.]\s*(\S.*)$")
_LETTER_ANSWER = re.compile(r"^(0*\d{1,2})([A-Da-d])$")
_STYLE_TOKEN = re.compile(r"^style:(.+)$", re.IGNORECASE)
_SOURCES_TOKEN = re.compile(r"^sources:(.+)$", re.IGNORECASE)


def gate_for_phase(phase: object) -> str | None:
    """Gate name of a gate phase, or None for every other phase (§2.4)."""
    return GATE_BY_PHASE.get(str(phase))


# --- parser ---------------------------------------------------------------


def _numbered_key(key: str, question_count: int | None) -> str | None:
    """D-61/D-71: canonical `str(int(key))` of a printed question (`1..N`), else None.

    Normalising before the bound check and before storing keeps `01A` ≡ `1A`: the stored key is
    the one every consumer builds from the question index (`user_facts_markdown`, `apply_defaults`).
    """
    try:
        number = int(key)
    except ValueError:
        return None
    if question_count is not None and not 1 <= number <= int(question_count):
        return None
    return str(number)


def parse_reply(gate: str, text: str, question_count: int | None = None) -> dict:
    """The single parser of §2.4: `{action, answers, defaults_applied, errors}` for any gate text.

    `question_count` is the number of questions the gate printed; with it, a key outside `1..N`
    is an `unrecognized_token` instead of an answer (D-61). `None` leaves the reply unbounded.
    """
    if gate not in GATES:
        raise ValueError(f"unknown_gate: {gate!r}")
    raw = "" if text is None else str(text)
    answers: dict[str, str] = {}
    errors: list[str] = []
    action: str | None = None
    edit_text = ""
    mode_answer = ""
    style_answer = ""
    sources_answer = ""

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("edit:"):
            action = action or "edit"
            edit_text = (edit_text + " " + stripped[len("edit:") :].strip()).strip()
            continue
        free = _FREE_TEXT_ANSWER.search(stripped)
        if free is not None:
            # D-56: the mixed form is parsed whole — the letters before the marker and the text
            # after it are both answers, so nothing is dropped into `errors`.
            free_key = _numbered_key(free.group(1), question_count)
            if free_key is not None:
                answers[free_key] = free.group(2).strip()
            else:
                errors.append(f"unrecognized_token: {free.group(1)}:")
            stripped = stripped[: free.start(1)].strip()
            if not stripped:
                continue
        for token in stripped.split():
            bare = token.strip(",;")
            low = bare.lower()
            if low == "cancel":
                action = "cancel"
                continue
            if low == "approve":
                action = action if action == "cancel" else "approve"
                continue
            if low == "proceed":
                action = action if action in ("cancel", "approve") else "proceed"
                continue
            if low == "continue":
                action = action if action in ("cancel", "approve") else "continue"
                continue
            if low in ("brief", "full"):
                mode_answer = low
                continue
            if low == "standard":
                style_answer = "standard"
                continue
            style = _STYLE_TOKEN.match(bare)
            if style is not None:
                style_answer = style.group(1).strip()
                continue
            sources = _SOURCES_TOKEN.match(bare)
            if sources is not None:
                sources_answer = sources.group(1).strip().lower()
                continue
            letter = _LETTER_ANSWER.match(bare)
            if letter is not None:
                letter_key = _numbered_key(letter.group(1), question_count)
                if letter_key is not None:
                    answers[letter_key] = letter.group(2).upper()
                    continue
            errors.append(f"unrecognized_token: {bare}")

    if action is None and answers and gate in QUESTION_GATES:
        action = "proceed"
    if action == "cancel":
        answers = {}

    if action in ("approve", "edit") and gate != "plan":
        errors.append(f"action_not_allowed_for_gate: {action}")
        action = None
    if action == "proceed" and gate in ("plan", "insufficient", "source_review"):
        action = "continue" if gate != "plan" else None
    if action == "continue" and gate == "plan":
        action = "approve"

    if action in ("approve", "edit"):
        # D-66: `edit:` keeps the Mode/Style/Sources recognised in the same reply, so the forced
        # approval of the corrected plan reads them back instead of falling to `full` silently.
        if mode_answer:
            answers["Mode"] = mode_answer.capitalize()
        if style_answer:
            answers["Style"] = style_answer
        if sources_answer:
            answers["Sources"] = "Reduced" if sources_answer == "reduced" else sources_answer
    if action == "approve":
        answers["Plan"] = "Approve"
    elif action == "edit":
        answers["Plan"] = "Edit"
        answers["edit_text"] = edit_text
    elif action == "cancel":
        answers = {"Plan": "Cancel"} if gate == "plan" else {}

    if action is None and not errors:
        errors.append("empty_reply")

    return {
        "gate": gate,
        "action": action,
        "answers": answers,
        "defaults_applied": [],
        "errors": errors,
        "recognized": action is not None,
    }


def canonical_map(ui: str = "en") -> dict:
    """`{localized header or option label: canonical English}` of gate 4 (D-176a).

    The identity map for `en`, and for any interface language whose pack cannot be read — a
    `ui.*` key of an unreadable pack answers from the English floor (D-168).
    """
    return {i18n.t(ui, f"ui.gates.{key}"): canonical for key, canonical in CANONICAL_UI}


def parse_auq(answers: dict, ui: str = "en") -> dict:
    """AUQ answers of gate 4, applied in the §2.4 order: Cancel > Style > Mode > Sources > Plan.

    D-176a: the headers and labels `build_auq` issued in `ui` are mapped back to their canonical
    English first, so the checks below and everything downstream read one vocabulary. A value the
    map does not know — a style profile name, free text — is kept exactly as it came, and
    canonical English is accepted whatever `ui` is.

    The `Cancel` scan runs over **every** answer value before the keys are collapsed: a localized
    header and its canonical English name map to the same header, so collapsing first would let
    whichever answer came last overwrite a `Cancel` and turn a stop into an approval.
    """
    known = canonical_map(ui)
    given = [known.get(str(value), str(value)) for value in (answers or {}).values()]
    if any(value.strip().lower() == "cancel" for value in given):
        return {
            "gate": "plan",
            "action": "cancel",
            "answers": {"Plan": "Cancel"},
            "defaults_applied": [],
            "errors": [],
            "recognized": True,
        }
    values = {
        known.get(str(key), str(key)): known.get(str(value), str(value))
        for key, value in (answers or {}).items()
    }
    lowered = {key.lower(): value.strip() for key, value in values.items()}
    plan = lowered.get("plan", "").lower()
    if not plan:
        # D-56: approval is never inferred — a missing or blank Plan answer is an error, so the
        # gate stays open and the text fallback of §2.4 can ask again.
        return {
            "gate": "plan",
            "action": None,
            "answers": {},
            "defaults_applied": [],
            "errors": ["plan_answer_missing"],
            "recognized": False,
        }
    if plan.startswith("edit"):
        action = "edit"
    elif plan.startswith("approve"):
        action = "approve"
    else:
        return {
            "gate": "plan",
            "action": None,
            "answers": {},
            "defaults_applied": [],
            "errors": [f"unrecognized_plan_answer: {lowered.get('plan')}"],
            "recognized": False,
        }
    return {
        "gate": "plan",
        "action": action,
        "answers": values,
        "defaults_applied": [],
        "errors": [],
        "recognized": True,
    }


def apply_defaults(gate: str, questions: list[dict]) -> dict:
    """`gate_parse_errors` exhausted: the documented default, never `assumptions_accepted=true` (§2.4 c)."""
    action = EXHAUSTED_DEFAULT[gate]
    keys = [str(index + 1) for index in range(len(questions))] if gate in QUESTION_GATES else []
    return {
        "gate": gate,
        "action": action,
        "answers": {},
        "defaults_applied": keys,
        "errors": [],
        "recognized": True,
        "budget_exhausted": True,
    }


# --- prompts --------------------------------------------------------------


def _impact_rank(question: dict) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(question.get("impact")), 3)


def intake_questions(work_dir: Path, state: dict) -> tuple[list[dict], list[dict]]:
    """Must-answer questions ranked by `impact` and capped, plus everything defaulted (§2.4)."""
    document = _read_json(work_dir / QUESTIONS_PATH) or {}
    cap = int((state.get("config") or {}).get("intake_max_questions") or limits.INTAKE_MAX_QUESTIONS)
    must = sorted(list(document.get("must_answer") or []), key=_impact_rank)
    return must[:cap], must[cap:] + list(document.get("optional") or [])


def followup_questions(state: dict) -> list[dict]:
    """Questions written by `mf sufficiency route` for the phase-7 gate (§2.1 стр.6)."""
    followup = state.get("sufficiency_followup") or {}
    return [row for row in (followup.get("questions") or []) if isinstance(row, dict)]


def printed_questions(work_dir: str | os.PathLike, state: dict, gate: str) -> list[dict] | None:
    """The numbered questions one gate prints (`1..N` of D-61), or None where it numbers none."""
    if gate == "intake":
        return intake_questions(Path(work_dir), state)[0]
    if gate == "sufficiency_followup":
        return followup_questions(state)
    return None


def _question_block(index: int, question: dict, ui: str) -> list[str]:
    lines = [
        i18n.t(ui, "ui.gates.question_line", index=index, question=question.get("question", "").strip())
    ]
    for position, option in enumerate(question.get("options") or []):
        if position >= len(OPTION_LETTERS):
            break
        lines.append(
            "   "
            + i18n.t(
                ui,
                "ui.gates.option_line",
                letter=OPTION_LETTERS[position],
                label=option.get("label"),
                description=option.get("description"),
            )
        )
    return lines


def _defaults_block(questions: list[dict], ui: str) -> list[str]:
    lines: list[str] = []
    for question in questions:
        default = question.get("default") or question.get("default_assumption_if_skipped") or ""
        if not default:
            continue
        confidence = question.get("confidence")
        wrong = question.get("default_if_wrong") or ""
        key = "default_line_confidence" if confidence else "default_line"
        lines.append(
            "- "
            + i18n.t(
                ui,
                f"ui.gates.{key}",
                question=question.get("question", "").strip(),
                default=default,
                confidence=confidence,
            )
        )
        if wrong:
            lines.append("  " + i18n.t(ui, "ui.gates.default_if_wrong", value=wrong))
    return lines


def _slash_line(state: dict, example: str) -> str:
    ui = i18n.ui_language(state)
    return i18n.t(ui, "ui.gates.slash_line", task_id=state.get("task_id"), example=example)


def memo_language_line(state: dict) -> str:
    """D-172: `Memo language: <endonym>` for the plan digest and the plan card, else `""`.

    The line exists so the user can see — and correct with `mf task language` — the language the
    memo will be written in before the plan is approved. An `en`/`en` task prints nothing at all,
    so its gate text is byte-identical to the one before plan 56.
    """
    memo = i18n.normalize((state or {}).get("language")) or i18n.DEFAULT
    ui = i18n.ui_language(state)
    if memo == "en" and ui == "en":
        return ""
    return i18n.t(ui, "ui.gates.memo_language_line", name=i18n.t(ui, f"ui.language_names.{memo}"))


def render_intake(work_dir: Path, state: dict) -> str:
    """Text of gate 2 (§2.4 Intake)."""
    ui = i18n.ui_language(state)
    must, defaulted = intake_questions(work_dir, state)
    lines = [_slash_line(state, "1A 2C"), ""]
    if must:
        lines.append(i18n.t(ui, "ui.gates.intake_form"))
        lines.append("")
        for index, question in enumerate(must, start=1):
            lines.extend(_question_block(index, question, ui))
            lines.append("")
    else:
        lines.append(i18n.t(ui, "ui.gates.intake_no_questions"))
        lines.append("")
    defaults = _defaults_block(defaulted, ui)
    if defaults:
        lines.append(i18n.t(ui, "ui.gates.intake_defaults_heading"))
        lines.extend(defaults)
        lines.append("")
    lines.append(i18n.t(ui, "ui.gates.intake_footer"))
    return "\n".join(lines).rstrip() + "\n"


def render_followup(work_dir: Path, state: dict) -> str:
    """Text of gate 7 (§2.4 Sufficiency follow-up; same form as Intake)."""
    ui = i18n.ui_language(state)
    questions = followup_questions(state)
    lines = [_slash_line(state, "1A"), ""]
    lines.append(i18n.t(ui, "ui.gates.followup_form"))
    lines.append("")
    for index, question in enumerate(questions, start=1):
        lines.extend(_question_block(index, question, ui))
        default = question.get("default_assumption_if_skipped")
        if default:
            lines.append("   " + i18n.t(ui, "ui.gates.followup_skipped", default=default))
        lines.append("")
    lines.append(i18n.t(ui, "ui.gates.followup_footer"))
    return "\n".join(lines).rstrip() + "\n"


def render_insufficient(work_dir: Path, state: dict) -> str:
    """Text of gate 8 (§2.4 Insufficient research).

    D-173b: the gaps are `drafting_warnings[]` — memo-language text printed verbatim inside a
    frame written in the interface language.
    """
    ui = i18n.ui_language(state)
    warnings = [
        row.get("message") if isinstance(row, dict) else str(row)
        for row in (state.get("drafting_warnings") or [])
    ]
    lines = [_slash_line(state, "continue"), ""]
    lines.append(i18n.t(ui, "ui.gates.insufficient_lead"))
    if warnings:
        lines.append("")
        lines.append(i18n.t(ui, "ui.gates.insufficient_gaps_heading"))
        lines.extend(f"- {text}" for text in warnings if text)
    lines.append("")
    lines.append(i18n.t(ui, "ui.gates.insufficient_continue"))
    lines.append(i18n.t(ui, "ui.gates.insufficient_cancel"))
    return "\n".join(lines).rstrip() + "\n"


def render_source_review(work_dir: Path, state: dict) -> str:
    """Text of gate 11 — `mf sources digest --exceptions` (§2.4 Source review)."""
    from . import sources

    digest = sources.render_digest(work_dir, state, True, ui=i18n.ui_language(state))
    return _slash_line(state, "continue") + "\n\n" + str(digest.get("text") or "")


PLAN_UNREADABLE = i18n.t("en", "ui.gates.plan_unreadable")
"""D-99: what gate 4 prints in English instead of a digest when `plan.json` cannot be used; a
localized gate reads `ui.gates.plan_unreadable` of its own pack (D-176)."""


def _plan_view(work_dir: str | os.PathLike) -> dict | None:
    """D-99: `plan.json` when it is readable and schema-valid, else None — never raises.

    Every reader of gate 4 goes through this view, so a plan that is missing, not JSON or
    structurally wrong (`issues`/`jurisdictions` not a list, an issue field not a string) degrades
    to `PLAN_UNREADABLE` instead of raising `TypeError` inside the gate text `next` rebuilds.
    D-90 already did this for the dashboard projection; the prompts kept raising until D-99.
    """
    from . import schema

    plan = _read_json(Path(work_dir) / PLAN_PATH)
    if plan is None:
        return None
    try:
        if schema.validate(plan, "plan"):
            return None
    except (ValueError, schema.DependencyMissing):
        return None
    return plan


def render_plan_digest(work_dir: Path, state: dict) -> str:
    """The plan itself, read before the answer of gate 4 — no answer instructions (D-86).

    `next` hands this back as the `text` of the `gate-auq` step, so the plan is on screen before the
    `AskUserQuestion`; `render_plan_text` prefixes the same digest to the text-channel fallback.
    An unusable `plan.json` degrades to `ui.gates.plan_unreadable` (D-99), never to an exception.
    D-172: the `Memo language` line joins the header while the memo language can still be changed.
    """
    ui = i18n.ui_language(state)
    plan = _plan_view(work_dir)
    if plan is None:
        # D-172: the memo language is still changeable at this gate, plan or no plan, so the line
        # belongs on the degraded text too; an `en`/`en` task keeps today's single sentence.
        unreadable = [i18n.t(ui, "ui.gates.plan_unreadable")]
        degraded_language_line = memo_language_line(state)
        if degraded_language_line:
            unreadable.append(degraded_language_line)
        return "\n".join(unreadable) + "\n"
    issues = [row for row in (plan.get("issues") or []) if isinstance(row, dict)]
    mode = recommended_mode(plan)
    layers = [
        layer
        for layer in modes.MODES[mode]["researcher_layers"]
        if layer != "doctrine" or plan.get("doctrine_required")
    ]
    lines = [
        i18n.t(
            ui,
            "ui.gates.plan_digest_head",
            classification=plan.get("classification") or i18n.t(ui, "ui.gates.plan_digest_unclassified"),
            jurisdictions=", ".join(plan.get("jurisdictions") or [])
            or i18n.t(ui, "ui.gates.plan_digest_unspecified"),
            complexity=plan.get("estimated_complexity") or i18n.t(ui, "ui.gates.plan_digest_unknown"),
        ),
        i18n.t(ui, "ui.gates.plan_digest_file", path=PLAN_PATH),
    ]
    language_line = memo_language_line(state)
    if language_line:
        lines.append(language_line)
    lines.append("")
    lines.append(i18n.t(ui, "ui.gates.plan_digest_issues_heading", count=len(issues)))
    detailed = len(issues) <= PLAN_DIGEST_DETAIL_ISSUES
    for issue in issues[:PLAN_DIGEST_MAX_ISSUES]:
        where = ", ".join(issue.get("jurisdictions") or [])
        key = "plan_digest_issue_line_where" if where else "plan_digest_issue_line"
        lines.append(
            "- "
            + i18n.t(
                ui,
                f"ui.gates.{key}",
                issue_id=issue.get("issue_id"),
                title=issue.get("title"),
                jurisdictions=where,
            )
        )
        question = str(issue.get("question") or "").strip()
        if detailed and question:
            lines.append(f"  {question}")
    if not issues:
        lines.append("- " + i18n.t(ui, "ui.gates.plan_digest_no_issues", path=PLAN_PATH))
    elif len(issues) > PLAN_DIGEST_MAX_ISSUES:
        lines.append(
            "- "
            + i18n.t(
                ui,
                "ui.gates.plan_digest_more_issues",
                count=len(issues) - PLAN_DIGEST_MAX_ISSUES,
                path=PLAN_PATH,
            )
        )
    doctrine = i18n.t(
        ui,
        "ui.gates.plan_digest_doctrine_required"
        if plan.get("doctrine_required")
        else "ui.gates.plan_digest_doctrine_not_required",
    )
    lines.append("")
    lines.append(
        i18n.t(
            ui,
            "ui.gates.plan_digest_layers",
            layers=", ".join(layers) or i18n.t(ui, "ui.gates.plan_digest_no_layers"),
            doctrine=doctrine,
        )
    )
    lines.append(
        i18n.t(
            ui,
            "ui.gates.plan_digest_recommended_mode",
            mode=mode,
            summary=i18n.t(ui, f"ui.gates.mode_summary_{mode}"),
        )
    )
    hint = brief_mismatch_hint(plan, ui)
    if hint:
        lines.append(hint)
    notes = " ".join(str(plan.get("notes") or "").split())
    if notes:
        lines.append(i18n.t(ui, "ui.gates.plan_digest_notes", notes=notes[:PLAN_DIGEST_NOTES_CHARS]))
    # D-147: what the preflight found before research — only the hosts that did not answer.
    access = preflight_block(work_dir, state)
    if access:
        lines.extend(["", access])
    return "\n".join(lines).rstrip() + "\n"


def preflight_block(work_dir: Path, state: dict) -> str:
    """The `Source access today:` block of gate 4; `""` when every routed portal answered (D-147)."""
    from . import preflight

    return preflight.source_access_block(work_dir, state, ui=i18n.ui_language(state))


def render_plan_text(work_dir: Path, state: dict) -> str:
    """Equivalent text fallback of gate 4 — the digest of D-86 plus the full answer format of §2.4.

    D-99: without a readable plan the only answers offered are the ones the AUQ still offers.
    """
    ui = i18n.ui_language(state)
    plan = _plan_view(work_dir)
    auq = build_auq(work_dir, state)
    lines = [_slash_line(state, "approve full" if plan is not None else "cancel"), ""]
    lines.append(render_plan_digest(work_dir, state).rstrip())
    lines.append("")
    for question in auq["questions"]:
        labels = " / ".join(str(option.get("label")) for option in question.get("options") or [])
        lines.append(
            i18n.t(
                ui,
                "ui.gates.plan_text_question",
                header=question.get("header"),
                question=question.get("question"),
            )
        )
        lines.append("  " + i18n.t(ui, "ui.gates.plan_text_options", labels=labels))
    lines.append("")
    lines.append(i18n.t(ui, "ui.gates.plan_text_reply_heading"))
    if plan is not None:
        lines.append("- " + i18n.t(ui, "ui.gates.plan_text_reply_approve"))
    lines.append("- " + i18n.t(ui, "ui.gates.plan_text_reply_edit"))
    lines.append("- " + i18n.t(ui, "ui.gates.plan_text_reply_cancel"))
    return "\n".join(lines).rstrip() + "\n"


RENDERERS = {
    "intake": render_intake,
    "sufficiency_followup": render_followup,
    "insufficient": render_insufficient,
    "source_review": render_source_review,
    "plan": render_plan_text,
}


def render(work_dir: str | os.PathLike, state: dict, gate: str) -> str:
    """Prompt text of one gate (§2.4 rule (a): every prompt is generated by the CLI)."""
    if gate not in RENDERERS:
        raise ValueError(f"unknown_gate: {gate!r}")
    return RENDERERS[gate](Path(work_dir), state)


# --- the AUQ payload of gate 4 --------------------------------------------


def recommended_mode(plan: dict | None) -> str:
    """Recommended mode from `plan.estimated_complexity` (§2.4); `full` without a plan (D-99)."""
    return "brief" if str((plan or {}).get("estimated_complexity")) == "low" else "full"


def brief_mismatch_hint(plan: dict | None, ui: str = "en") -> str:
    """D34-01: what Brief would cost this plan, in one sentence; empty where they agree.

    Fires only where the recommendation is not Brief and the plan is big enough for the mismatch
    to matter — more than `BRIEF_HINT_MAX_ISSUES` issues, or `estimated_complexity: high`.
    """
    if plan is None or recommended_mode(plan) == "brief":
        return ""
    count = len([row for row in (plan.get("issues") or []) if isinstance(row, dict)])
    complexity = str(plan.get("estimated_complexity") or "unknown")
    if count <= BRIEF_HINT_MAX_ISSUES and complexity != "high":
        return ""
    key = "brief_mismatch_hint_one" if count == 1 else "brief_mismatch_hint_many"
    return i18n.t(ui, f"ui.gates.{key}", count=count, complexity=complexity)


def _style_options(state: dict) -> list[dict]:
    from . import style_profile

    ui = i18n.ui_language(state)
    try:
        profiles = style_profile.list_profiles()
    except OSError:
        return []
    options = [
        {
            "label": row["name"],
            "description": i18n.t(ui, "ui.gates.style_option_profile_description", name=row["name"]),
        }
        for row in profiles
        if row.get("valid")
    ][:3]
    if not options:
        return []
    options.append(
        {
            "label": STANDARD_STYLE,
            "description": i18n.t(ui, "ui.gates.style_option_standard_description"),
        }
    )
    return options


def _plan_jurisdictions(plan: dict) -> list[str]:
    """Every jurisdiction the plan names — at the top and per issue — normalised, without repeats."""
    codes: list[str] = []
    raw = list(plan.get("jurisdictions") or [])
    for issue in plan.get("issues") or []:
        if isinstance(issue, dict):
            raw.extend(issue.get("jurisdictions") or [])
    for value in raw:
        code = routing.normalize_jurisdiction(value)
        if code and code not in codes:
            codes.append(code)
    return codes


def _mode_layers(plan: dict, mode: str) -> list[str]:
    """The research layers `mode` would run for this plan (§2.3; doctrine only when asked for)."""
    layers = list(modes.MODES[mode]["researcher_layers"])
    if mode == "full" and not plan.get("doctrine_required"):
        layers = [layer for layer in layers if layer != "doctrine"]
    return layers


def coverage_gaps(
    namespaces: dict,
    layers: list[str],
    jurisdictions: list[str],
    portals: dict | None = None,
) -> list[dict]:
    """The `layer x jurisdiction` rows nothing can serve today (§2.4, D-106, D-147).

    A row is covered when at least one of the MCP servers it routes to answered the smoke call
    (`namespaces` is already filtered to those, D-147) **or** at least one of its portals answered
    the preflight. So one connected server still covers EU statutes, and an EU row with no server
    left is no longer a gap when Cellar is up.

    `portals` is `{host: status}` of `intake/preflight.json`. Without it the old rule stands: a row
    that names no server at all (US doctrine is WebSearch plus WebFetch) is never a gap, because
    nothing was measured. Once the preflight has spoken, a row whose only route is a portal and
    whose every portal is blocked is a real gap — that is what makes the `Sources` question fire on
    a dead portal instead of only on a missing database.

    D-184a: a `routing.SPECIALIST_SERVERS` entry (FAS, advertising and antimonopoly only) is left
    out of both the coverage test and the label list — it cannot answer the general question the row
    stands for. A row whose only servers are specialist ones and whose portals were never checked
    reads as a row with no server at all: never a gap, as nothing was measured.
    """
    statuses = portals if isinstance(portals, dict) else {}
    gaps: list[dict] = []
    for layer in layers:
        for code in jurisdictions:
            row = routing.route(layer, code)
            servers = [
                alias
                for alias in routing.row_servers(layer, code)
                if alias not in routing.SPECIALIST_SERVERS
            ]
            checked = [host for host in row["domains"] if statuses.get(host)]
            if any(namespaces.get(alias) for alias in servers):
                continue
            if any(statuses.get(host) == "ok" for host in checked):
                continue
            if not servers and not checked:
                continue
            gaps.append(
                {
                    "layer": layer,
                    "jurisdiction": code,
                    "servers": [routing.MCP_SERVER_LABELS[alias] for alias in servers],
                    "portals": checked,
                }
            )
    return gaps


def sources_question_needed(work_dir: Path, state: dict, plan: dict | None, mode: str) -> dict:
    """§2.4: ask about the source budget when a research row lost its databases, or the estimate
    exceeds the daily quotas of the quota servers.

    `plan` is the view of D-99: `None` (no usable plan) is estimated as zero issues and names no
    jurisdiction, so the question does not fire on coverage — a plan that cannot be read cannot be
    approved either, and its `Plan` question already offers only Edit and Cancel.
    """
    from . import preflight

    plan = plan or {}
    probe = _read_json(work_dir / MCP_PROBE_PATH) or {}
    # D-147: a connected server that failed its smoke call covers nothing, and a portal that did
    # not answer the preflight covers nothing either.
    namespaces = preflight.usable_namespaces(probe)
    portals = preflight.portal_status(work_dir, state)
    missing = coverage_gaps(namespaces, _mode_layers(plan, mode), _plan_jurisdictions(plan), portals)
    issues = len(plan.get("issues") or [])
    estimates = {}
    for candidate in ("brief", "full"):
        estimates[candidate] = routing.budget_verdict(
            _mode_layers(plan, candidate), issues, modes.MODES[candidate]["mcp_budget"]
        )
    return {
        "needed": bool(missing) or bool(estimates[mode]["exceeds"]),
        "missing": missing,
        "estimates": estimates,
    }


def _plan_question(plan: dict | None, ui: str) -> dict:
    """The `Plan` question of gate 4; D-99: an unreadable plan is not a plan one can approve."""
    edit = {
        "label": i18n.t(ui, "ui.gates.option_edit"),
        "description": i18n.t(ui, "ui.gates.option_edit_description"),
    }
    cancel = {
        "label": i18n.t(ui, "ui.gates.option_cancel"),
        "description": i18n.t(ui, "ui.gates.option_cancel_description"),
    }
    header = i18n.t(ui, "ui.gates.header_plan")
    if plan is None:
        return {
            "question": i18n.t(ui, "ui.gates.plan_unreadable"),
            "header": header,
            "multiSelect": False,
            "options": [edit, cancel],
        }
    return {
        "question": i18n.t(ui, "ui.gates.plan_question"),
        "header": header,
        "multiSelect": False,
        "options": [
            {
                "label": i18n.t(ui, "ui.gates.option_approve"),
                "description": i18n.t(ui, "ui.gates.option_approve_description"),
            },
            edit,
            cancel,
        ],
    }


def build_auq(work_dir: str | os.PathLike, state: dict) -> dict:
    """`questions[]` of the single `AskUserQuestion` of gate 4 plus its text fallback (§2.4).

    D-99: `Mode`, `Style` and `Sources` are unchanged by a plan that cannot be read; only `Plan`
    loses its `Approve` option, so the gate stays answerable instead of raising.

    D-176a: every header, label and description is written in the interface language of the task,
    and `canonical` carries the reverse map `parse_auq` needs to read the answer back as the
    canonical English the machine stores.
    """
    work_dir = Path(work_dir)
    ui = i18n.ui_language(state)
    plan = _plan_view(work_dir)
    mode = recommended_mode(plan)
    questions: list[dict] = [_plan_question(plan, ui)]
    # D34-01: the Brief option carries the consequence of choosing it against the recommendation.
    hint = brief_mismatch_hint(plan, ui)
    brief = {
        "label": i18n.t(ui, "ui.gates.option_brief"),
        "description": (i18n.t(ui, "ui.gates.mode_summary_brief") + " " + hint).strip(),
    }
    full = {
        "label": i18n.t(ui, "ui.gates.option_full"),
        "description": i18n.t(ui, "ui.gates.mode_summary_full"),
    }
    ordered = [full, brief] if mode == "full" else [brief, full]
    ordered[0] = dict(
        ordered[0],
        label=ordered[0]["label"],
        description=i18n.t(ui, "ui.gates.mode_recommended", description=ordered[0]["description"]),
    )
    questions.append(
        {
            "question": i18n.t(ui, "ui.gates.mode_question"),
            "header": i18n.t(ui, "ui.gates.header_mode"),
            "multiSelect": False,
            "options": ordered,
        }
    )
    style_options = _style_options(state)
    if style_options:
        questions.append(
            {
                "question": i18n.t(ui, "ui.gates.style_question"),
                "header": i18n.t(ui, "ui.gates.header_style"),
                "multiSelect": False,
                "options": style_options,
            }
        )
    budget = sources_question_needed(work_dir, state, plan, mode)
    if budget["needed"]:
        estimate = budget["estimates"][mode]
        detail = [
            i18n.t(
                ui,
                "ui.gates.sources_estimate",
                total=estimate["total"],
                servers=", ".join(limits.MCP_QUOTA_SERVERS),
                upper_bound=estimate["daily_upper_bound"],
            )
        ]
        for row in budget["missing"]:
            if row.get("servers"):
                detail.append(
                    i18n.t(
                        ui,
                        "ui.gates.sources_missing_database",
                        layer=row["layer"],
                        jurisdiction=row["jurisdiction"],
                        servers=", ".join(row["servers"]),
                    )
                )
            else:
                # D-147: the row never had a database; today its portals did not answer either.
                detail.append(
                    i18n.t(
                        ui,
                        "ui.gates.sources_missing_portal",
                        layer=row["layer"],
                        jurisdiction=row["jurisdiction"],
                        portals=", ".join(row.get("portals") or []),
                    )
                )
        questions.append(
            {
                "question": i18n.t(ui, "ui.gates.sources_question", detail=" ".join(detail)),
                "header": i18n.t(ui, "ui.gates.header_sources"),
                "multiSelect": False,
                "options": [
                    {
                        "label": i18n.t(ui, "ui.gates.option_continue"),
                        "description": i18n.t(ui, "ui.gates.option_continue_description"),
                    },
                    {
                        "label": i18n.t(ui, "ui.gates.option_cancel"),
                        "description": i18n.t(ui, "ui.gates.option_cancel_description"),
                    },
                ],
            }
        )
    canonical = canonical_map(ui)
    for option in style_options:
        # A profile name and `standard` are identity: the machine stores them as they were shown.
        canonical.setdefault(str(option["label"]), str(option["label"]))
    return {
        "questions": questions,
        AUQ_CANONICAL: canonical,
        "recommended_mode": mode,
        "sources_budget": budget,
        "style_options": [option["label"] for option in style_options],
    }


# --- persistence ----------------------------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        document = state_io.read_json(path)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _answer_bullet(question: dict, given: str | None) -> str:
    """One answered (or defaulted) question as a bullet of `intake/user-facts.md` (§6)."""
    text = question.get("question", "").strip()
    if given is None:
        default = question.get("default") or question.get("default_assumption_if_skipped") or "(none)"
        return f"- **{text}** — not answered; assumption applied: {default}"
    options = question.get("options") or []
    if len(given) == 1 and given in OPTION_LETTERS:
        position = OPTION_LETTERS.index(given)
        label = options[position].get("label") if position < len(options) else given
        return f"- **{text}** — {label}"
    return f"- **{text}** — {given}"


def user_facts_markdown(state: dict, questions: list[dict], parsed: dict) -> str:
    """Markdown view of the intake answers written to `intake/user-facts.md` (§6)."""
    lines = ["# User facts and answers", "", f"Task: `{state.get('task_id')}`", ""]
    for index, question in enumerate(questions, start=1):
        lines.append(_answer_bullet(question, parsed["answers"].get(str(index))))
    if parsed["defaults_applied"]:
        lines.append("")
        lines.append("Defaults were applied without confirmation; assumptions are not accepted.")
    return "\n".join(lines).rstrip() + "\n"


FOLLOWUP_HEADING = "## Follow-up answers"
"""D34-06: the section gate 7 appends to `intake/user-facts.md`, the canonical file of user facts."""

FOLLOWUP_ROUND_HEADING = "### Round"
"""D-143: one sub-block per gate identity inside that section.

D-116 allows two user follow-ups, and the second gate prints its own questions; splitting the file
on `FOLLOWUP_HEADING` and rewriting the whole section dropped the answers of the first round.
"""

def followup_round_identity(state: dict, step_id: str | None = None) -> str:
    """Key of the round's block in `## Follow-up answers` (D-143, R2-01).

    `mf gate parse` may close a gate without `--step`, and a constant stand-in for the missing
    step gave two different rounds the same key: the second answer replaced the first — exactly
    the loss D-143 removed on the machine path. A round is identified by its opening, not by the
    call that closes it: `asked_at` is written once, by `mf sufficiency route`, when the gate is
    opened, so every close of that round rewrites its own block and a round opened later gets a
    key of its own. The questions themselves are the last resort for a state without `asked_at`.
    """
    if step_id:
        return " ".join(str(step_id).split())
    followup = state.get("sufficiency_followup") or {}
    asked_at = " ".join(str(followup.get("asked_at") or "").split())
    if asked_at:
        return f"asked-{asked_at}"
    digest = state_io.sha256_bytes(state_io.dumps(followup_questions(state)).encode("utf-8"))
    return f"questions-{digest[:12]}"


def followup_round_block(round_id: str, questions: list[dict], parsed: dict) -> str:
    """One round of `## Follow-up answers`: question, chosen option or free text, defaults (D34-06)."""
    lines = [f"{FOLLOWUP_ROUND_HEADING} {round_id}", ""]
    for index, question in enumerate(questions, start=1):
        lines.append(_answer_bullet(question, parsed["answers"].get(str(index))))
    if not questions:
        lines.append("- (the gate printed no question)")
    if parsed["defaults_applied"]:
        lines.append("")
        lines.append("Defaults were applied without confirmation; assumptions are not accepted.")
    return "\n".join(lines).rstrip() + "\n"


def split_followup_rounds(text: str) -> tuple[str, list[tuple[str, str]]]:
    """`(text before the section, [(round_id, block)])` of `intake/user-facts.md` (D-143)."""
    head, separator, tail = text.partition(FOLLOWUP_HEADING)
    if not separator:
        return text.rstrip(), []
    rounds: list[tuple[str, str]] = []
    for chunk in tail.split(FOLLOWUP_ROUND_HEADING)[1:]:
        identity = chunk.split("\n", 1)[0].strip()
        rounds.append((identity, (FOLLOWUP_ROUND_HEADING + chunk).rstrip() + "\n"))
    return head.rstrip(), rounds


def user_facts_with_followup(
    work_dir: Path, state: dict, parsed: dict, *, step_id: str | None = None
) -> str:
    """`intake/user-facts.md` with this round's follow-up block rewritten — the whole file.

    D34-06: the follow-up answer used to live only in `state.sufficiency_followup`, so the writer
    and the second sufficiency reviewer had to reconstruct it from a researcher's notes.
    D-143: the section accumulates one block per round identity (`followup_round_identity`), so a
    second follow-up adds its round; re-closing the same identity replaces that block and no other.
    """
    try:
        text = (work_dir / USER_FACTS_PATH).read_text(encoding="utf-8-sig")
    except OSError:
        text = f"# User facts and answers\n\nTask: `{state.get('task_id')}`\n"
    head, rounds = split_followup_rounds(text)
    round_id = followup_round_identity(state, step_id)
    block = followup_round_block(round_id, followup_questions(state), parsed)
    blocks = [block if identity == round_id else body for identity, body in rounds]
    if all(identity != round_id for identity, _ in rounds):
        blocks.append(block)
    return head + "\n\n" + FOLLOWUP_HEADING + "\n\n" + "\n".join(blocks)


def answers_document(gate: str, parsed: dict, generation: int, raw: str) -> dict:
    """`gate-answers` document persisted for the follow-up gate (§6)."""
    return {
        "gate": gate,
        "action": parsed["action"],
        "answers": dict(parsed["answers"]),
        "defaults_applied": list(parsed["defaults_applied"]),
        "errors": list(parsed["errors"]),
        "generation": int(generation),
        "raw_response": raw,
        "answered_at": events.utc_now(),
    }


def _record_answer(
    work_dir: Path,
    state: dict,
    gate: str,
    parsed: dict,
    *,
    generation: int,
    raw: str,
    step_id: str | None = None,
) -> tuple[list[dict], object]:
    """Files and the state mutation of one answered gate; returns `(published, mutator)`."""
    from . import schema

    published: list[dict] = []
    document = answers_document(gate, parsed, generation, raw)
    accepted = not parsed["defaults_applied"] and not parsed.get("budget_exhausted")
    # D34-01: the recommendation this answer was given against, kept next to the answer itself —
    # a run that chose Brief over a `full` recommendation is readable afterwards.
    recommended = recommended_mode(_plan_view(work_dir)) if gate == "plan" else None

    if gate == "intake":
        questions, _ = intake_questions(work_dir, state)
        markdown = user_facts_markdown(state, questions, parsed)
        state_io.write_bytes_atomic(work_dir / USER_FACTS_PATH, markdown.encode("utf-8"))
        published.append(
            {
                "canonical_path": USER_FACTS_PATH,
                "sha256": state_io.sha256_bytes(markdown.encode("utf-8")),
                "by": "command",
                "step_id": None,
                "at": events.utc_now(),
            }
        )
    if gate in QUESTION_GATES:
        schema.validate_or_raise(document, "gate-answers")
    if gate == "sufficiency_followup":
        payload = state_io.dumps(document).encode("utf-8")
        state_io.write_bytes_atomic(work_dir / FOLLOWUP_RESPONSE_PATH, payload)
        published.append(
            {
                "canonical_path": FOLLOWUP_RESPONSE_PATH,
                "sha256": state_io.sha256_bytes(payload),
                "by": "command",
                "step_id": None,
                "at": events.utc_now(),
            }
        )
        # D34-06: the same answer also belongs in the canonical file of user facts, republished
        # the way intake publishes it — `intake/` is what the writer's spec names as an input.
        facts = user_facts_with_followup(work_dir, state, parsed, step_id=step_id).encode("utf-8")
        state_io.write_bytes_atomic(work_dir / USER_FACTS_PATH, facts)
        published.append(
            {
                "canonical_path": USER_FACTS_PATH,
                "sha256": state_io.sha256_bytes(facts),
                "by": "command",
                "step_id": None,
                "at": events.utc_now(),
            }
        )

    def mutator(current: dict) -> None:
        if parsed["action"] == "cancel":
            current["cancel_requested"] = True
        if parsed["defaults_applied"] or parsed.get("budget_exhausted"):
            banner = fallbacks.banner("gate_defaults_applied", gate=gate)
            if banner and dict(banner, at=None) not in current.get("fallback_banners", []):
                current.setdefault("fallback_banners", []).append(dict(banner, at=events.utc_now()))
        if gate == "intake":
            intake = dict(current.get("intake") or {})
            intake.update(
                {
                    "status": "cancelled" if parsed["action"] == "cancel" else "answered",
                    "assumptions_accepted": bool(accepted),
                    "questions_path": QUESTIONS_PATH,
                    "user_facts_path": USER_FACTS_PATH,
                    "answered_at": events.utc_now(),
                }
            )
            current["intake"] = intake
        if gate == "sufficiency_followup":
            followup = dict(current.get("sufficiency_followup") or {"status": "pending"})
            followup["status"] = "answered"
            followup["user_response"] = raw
            followup["answered_at"] = events.utc_now()
            current["sufficiency_followup"] = followup
        if gate == "plan":
            approval = dict(current.get("plan_approval") or {})
            iterations = list(approval.get("iterations") or [])
            iterations.append(
                {
                    "generation": int(generation),
                    "action": parsed["action"],
                    "answers": dict(parsed["answers"]),
                    "recommended_mode": recommended,
                    "at": events.utc_now(),
                }
            )
            approval["iterations"] = iterations
            approval["generation"] = int(generation)
            approval["status"] = {"approve": "approved", "edit": "edit_requested"}.get(
                str(parsed["action"]), "cancelled"
            )
            if parsed["action"] == "approve":
                approval["approved_at"] = events.utc_now()
                approval["plan_path"] = PLAN_PATH
            current["plan_approval"] = approval

    return published, mutator


def commit(
    work_dir: str | os.PathLike,
    state: dict,
    gate: str,
    parsed: dict,
    *,
    generation: int,
    raw: str,
    step_id: str | None,
    attempt: int,
    phase: str | None = None,
    kind: str = "gate-text",
) -> dict:
    """Write the answer files, close the gate step and emit `gate_answered` — one state write (§2.4 b)."""
    work_dir = Path(work_dir)
    published, mutator = _record_answer(
        work_dir, state, gate, parsed, generation=generation, raw=raw, step_id=step_id
    )
    result = {
        "gate": gate,
        "action": parsed["action"],
        "answers": dict(parsed["answers"]),
        "defaults_applied": list(parsed["defaults_applied"]),
        "errors": list(parsed["errors"]),
        "generation": int(generation),
    }
    if step_id:
        stepctx.close_step(
            work_dir,
            step_id,
            attempt,
            result,
            kind=kind,
            phase=phase,
            published=published,
            mutate=mutator,
        )
    else:
        def plain(current: dict) -> None:
            stepctx.merge_published(current, published)
            mutator(current)

        state_io.write_state(work_dir, plain)

    events.append_event(
        work_dir,
        "gate_answered",
        "cli",
        {
            "gate": gate,
            "action": parsed["action"],
            "generation": int(generation),
            "defaults_applied": list(parsed["defaults_applied"]),
        },
        phase=phase,
        step_id=step_id,
    )
    if gate == "plan" and parsed["action"] == "approve":
        events.append_event(
            work_dir,
            "plan_approved",
            "cli",
            {"generation": int(generation), "answers": dict(parsed["answers"])},
            phase=phase,
            step_id=step_id,
        )
    return result


# --- CLI ------------------------------------------------------------------


def switch_to_text_channel(work_dir: str | os.PathLike, row: dict, phase: str | None) -> int:
    """D-34: move an AUQ gate step onto the text channel and return the new generation (§2.4).

    A text reply to the generation-0 AUQ gate is the channel switch itself; `mf report --status
    no_answer` stays available but is no longer required before `mf gate parse`.
    """
    work_dir = Path(work_dir)
    generation = int(row.get("generation") or 0)
    step_id = str(row["step_id"])
    attempt = int(row.get("attempt") or 1)

    def mutate(current: dict) -> None:
        for item in current.get("steps") or []:
            if str(item.get("step_id")) == step_id and int(item.get("attempt") or 1) == attempt:
                item["generation"] = generation + 1
                item["kind"] = KIND_GATE_TEXT

    state_io.write_state(work_dir, mutate)
    events.append_event(
        work_dir,
        "gate_channel_switched",
        "cli",
        {"step_id": step_id, "from": generation, "to": generation + 1, "via": "gate parse"},
        phase=phase,
        step_id=step_id,
    )
    return generation + 1


def _resolve_gate(state: dict, requested: str | None) -> str | None:
    if requested:
        return requested if requested in GATES else None
    return gate_for_phase(state.get("current_phase"))


def run_render(args: argparse.Namespace) -> dict:
    """`mf gate render --gate <g>` — the prompt text; the CLI owns every gate wording (§2.4).

    D-176a: `canonical` is an in-process detail — `parse_auq`, the machine and the probe read it
    from `build_auq(...)` directly. It is dropped here so the printed answer of the CLI carries
    exactly the keys it carried before plan 56, in every interface language.
    """
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    gate = _resolve_gate(state, args.gate)
    if gate is None:
        return {"errors": [f"unknown_gate: {args.gate!r}"], "phase": state.get("current_phase")}
    text = render(work_dir, state, gate)
    payload = {"gate": gate, "phase": PHASE_BY_GATE[gate], "text": text, "human": text}
    if gate == "plan":
        payload["auq"] = {
            key: value for key, value in build_auq(work_dir, state).items() if key != AUQ_CANONICAL
        }
    return payload


def run_parse(args: argparse.Namespace) -> dict:
    """`mf gate parse --gate <g> --text "<reply>" --step --attempt --generation` (§2.4 b)."""
    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    gate = _resolve_gate(state, args.gate)
    if gate is None:
        return {"errors": [f"unknown_gate: {args.gate!r}"], "phase": state.get("current_phase")}

    phase = PHASE_BY_GATE[gate]
    row = stepctx.current_step(state, args.step) if args.step else None
    current_generation = int((row or {}).get("generation") or 0)
    if args.step:
        # §3.1 order of checks: identity -> generation -> already closed -> content.
        identity = stepctx.check_identity(state, args.step, int(args.attempt or 1))
        if identity["status"] == stepctx.STATUS_MISMATCH:
            return {"errors": list(identity["errors"]), "reason": identity.get("reason")}
        if args.generation is None:
            # §2.4/D-34: an answer to an issued gate always names the channel it answers.
            return {"errors": ["generation_required"], "expected_generation": current_generation}
        if int(args.generation) != current_generation:
            # §2.4: a stale channel is rejected before the «already closed» check.
            return {
                "errors": ["stale_generation"],
                "expected_generation": current_generation,
                "generation": int(args.generation),
            }
        if identity["status"] == stepctx.STATUS_CLOSED:
            stored = dict(identity.get("result") or {})
            stored["already_parsed"] = True
            return stored

    channel_switched = False
    if row is not None and str(row.get("kind")) == KIND_GATE_AUQ and current_generation == 0:
        # D-34: text answer to the AUQ gate — bump the generation here, then parse it.
        current_generation = switch_to_text_channel(work_dir, row, phase)
        state = state_io.read_state(work_dir)
        channel_switched = True

    # D-61: the reply is bounded by the questions this gate actually printed, so a key outside
    # `1..N` cannot pass as an answer and reach `commit`.
    questions = printed_questions(work_dir, state, gate)
    parsed = parse_reply(gate, args.text, None if questions is None else len(questions))
    if not parsed["recognized"] or parsed["errors"]:
        # D-56: a partially recognised reply is re-prompted, never committed with its gaps
        # silently defaulted. D-72: this is the only place the budget is spent — it counts
        # misunderstood *user replies*; the technical errors above (unknown gate, identity,
        # generation) return before it, and the `reprompt` answer is a business outcome the
        # router must never repeat.
        seen = int((state.get("attempts", {}).get("gate_parse_errors") or {}).get(gate, 0)) + 1

        def bump(current: dict) -> None:
            counters = current.setdefault("attempts", {}).setdefault("gate_parse_errors", {})
            counters[gate] = seen

        state_io.write_state(work_dir, bump)
        if seen < limits.MAX_GATE_PARSE_ERRORS:
            return {
                "gate": gate,
                "action": None,
                "answers": {},
                "defaults_applied": [],
                "errors": parsed["errors"],
                "parse_errors": seen,
                "reprompt": render(work_dir, state_io.read_state(work_dir), gate),
            }
        parsed = apply_defaults(gate, questions or [])

    result = commit(
        work_dir,
        state,
        gate,
        parsed,
        generation=current_generation,
        raw=str(args.text or ""),
        step_id=args.step,
        attempt=int(args.attempt or 1),
        phase=phase,
    )
    if channel_switched:
        result["channel_switched"] = True
    return result


def register(subparsers) -> None:
    """Register `mf gate render|parse` (§5.2)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "gate", "user gates: one parser, CLI-generated prompts")

    renderer = group.add_parser("render", help="print the prompt of one gate")
    renderer.add_argument("--workdir", required=True)
    renderer.add_argument("--gate", default=None, choices=list(GATES))
    renderer.set_defaults(func=run_render)

    parser = group.add_parser("parse", help="parse a gate reply and close the gate step")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--gate", default=None, choices=list(GATES))
    parser.add_argument("--text", required=True)
    parser.add_argument("--step", default=None)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--generation", type=int, default=None)
    parser.set_defaults(func=run_parse)
