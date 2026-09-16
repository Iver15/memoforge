"""Single source of truth for the v2 phase list (ТЗ §2.1)."""

from __future__ import annotations

PHASES: list[str] = [
    "intake_preliminary_research",          # 1  inline-llm mcp-probe -> dispatch(1) analyst
    "intake_questions_pending",             # 2  gate-text
    "planning",                             # 3  inline-llm
    "plan_approval_pending",                # 4  gate-auq (text fallback)
    "research",                             # 5  dispatch(1-3, parallel)
    "research_sufficiency",                 # 6  dispatch(1) + script
    "research_sufficiency_followup_pending",  # 7  gate-text
    "research_insufficient_pending",        # 8  gate-text
    "currency_check",                       # 9  script + dispatch(1)
    "source_pack",                          # 10 script (freeze)
    "source_review_pending",                # 11 gate-text (conditional)
    "drafting",                             # 12 dispatch(1) + script
    "revision_loop",                        # 13 dispatch(K, parallel) + script
    "client_readiness",                     # 14 dispatch(1) + script
    "export",                               # 15 script
    "done",                                 # 16 terminal
    "failed",                               # 16 terminal
    "cancelled_by_user",                    # 16 terminal
]

PHASE_LABELS: dict[str, str] = {
    "intake_preliminary_research": "Checking which legal databases are available",
    "intake_questions_pending": "Your intake answers",
    "planning": "Drafting the research plan",
    "plan_approval_pending": "Plan approval",
    "research": "Legal research",
    "research_sufficiency": "Checking research coverage",
    "research_sufficiency_followup_pending": "Your follow-up answers",
    "research_insufficient_pending": "Your decision on thin research",
    "currency_check": "Checking sources are still current",
    "source_pack": "Building the source pack",
    "source_review_pending": "Your source review",
    "drafting": "Writing the memo",
    "revision_loop": "Review round",
    "client_readiness": "Client-readiness check",
    "export": "Exporting the memo (DOCX)",
    "done": "Done",
    "failed": "Stopped with a fallback deliverable",
    "cancelled_by_user": "Cancelled",
}
"""D-95: plain-English name of every phase, written from the user's point of view.

The single source of truth for what the dashboard (`machine.dashboard_patch`) and `docs/phases.md`
call a phase; `PHASES` above stays the machine's contract and nothing user-facing reads it directly.
`machine` appends the iteration number to `revision_loop` when the run knows one.
"""

TERMINAL: tuple[str, ...] = ("done", "failed", "cancelled_by_user")
"""Terminal phases; reached only after `mf finalize` has produced a deliverable (M9)."""

GATES: tuple[str, ...] = (
    "intake_questions_pending",
    "plan_approval_pending",
    "research_sufficiency_followup_pending",
    "research_insufficient_pending",
    "source_review_pending",
)
"""Phases that wait for the user; segment boundaries (M8, §3.2)."""

NON_TERMINAL: tuple[str, ...] = tuple(p for p in PHASES if p not in TERMINAL)

PHASE_INDEX: dict[str, int] = {phase: i for i, phase in enumerate(PHASES)}

INITIAL_PHASE = "intake_preliminary_research"
"""Phase written by `task new` (§2.1 row 1)."""


def is_gate(phase: str) -> bool:
    """True when the phase waits for a user answer (§2.4)."""
    return phase in GATES


def is_terminal(phase: str) -> bool:
    """True when the phase is one of done/failed/cancelled_by_user (§2.1 row 16)."""
    return phase in TERMINAL


def is_phase(phase: object) -> bool:
    """True when the value is a known v2 phase name."""
    return isinstance(phase, str) and phase in PHASE_INDEX


def label(phase: object) -> str:
    """Plain-English name of a phase for anything a user reads (D-95); unknown → the raw name."""
    return PHASE_LABELS.get(str(phase or ""), str(phase or ""))
