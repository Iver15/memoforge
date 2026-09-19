"""Single source of truth for the v2 phase list (ТЗ §2.1)."""

from __future__ import annotations

from . import i18n, i18n_en

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

PHASE_LABELS: dict[str, str] = dict(i18n_en.EN["ui"]["phases"])
"""D-95: plain-English name of every phase, written from the user's point of view.

The English floor of `ui.phases` (D-176), which is where the labels themselves now live: the
dashboard (`machine.dashboard_patch`) and `docs/phases.md` read them through `label()`, and
`PHASES` above stays the machine's contract that nothing user-facing reads directly. `machine`
appends the iteration number to `revision_loop` when the run knows one.
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


def label(phase: object, ui: str = "en") -> str:
    """Name of a phase in the interface language for anything a user reads (D-95, D-176).

    `ui` defaults to English, so `docs/phases.md` and every machine-facing caller keep the
    English table; an unknown phase degrades to its own raw name, as it always has.
    """
    name = str(phase or "")
    if name not in PHASE_LABELS:
        return name
    return i18n.t(ui, f"ui.phases.{name}")
