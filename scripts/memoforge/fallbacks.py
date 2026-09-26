"""Single source of truth for the degradation matrix / always-deliver banners (M9, ТЗ §2.1, §5.5).

Rows are carried over from the v1 reference `skills/memo/references/always-deliver.md`; that
document is regenerated from this table by `mf docs render`. Rows whose subject was removed in v2
(the removed mid-run gate, the mode-pick "Other" branch, the pandoc export branch) are not carried over.

`banner_params` lists `str.format` placeholders that `finalize.py` substitutes; a row without a
user-facing banner has `banner_id = None` and `banner_text = None`.
"""

from __future__ import annotations

from . import i18n

FALLBACKS: list[dict] = [
    {
        "condition_key": "work_dir_not_writable",
        "phase": "intake_preliminary_research",
        "action": (
            "Take the next candidate of the work_dir chain (§2.5) and record the reason in the `work_dir_resolved` "
            "event."
        ),
        "banner_id": None,
        "banner_text": None,
        "banner_params": [],
    },
    {
        "condition_key": "mcp_all_unavailable",
        "phase": "research",
        "action": (
            "Researchers proceed in WebFetch-only mode against vetted official portals; each research file records "
            "`mcp_status: unavailable`."
        ),
        "banner_id": "mcp_unavailable",
        "banner_text": (
            "MCP servers unavailable. Research conducted via public WebFetch only — verify against primary sources "
            "before client use."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "mcp_partial",
        "phase": "research",
        "action": "Continue with the reachable MCP server and note the gap in each research file.",
        "banner_id": "mcp_partial",
        "banner_text": "Partial MCP coverage — only {available} was reachable.",
        "banner_params": ["available"],
    },
    {
        "condition_key": "portal_unreachable",
        "phase": "research",
        "action": "Continue with what is reachable; the researcher writes an explicit `gap:` entry per missing portal.",
        "banner_id": "sources_unreachable",
        "banner_text": "Some primary sources were unreachable; gaps disclosed in research files.",
        "banner_params": [],
    },
    {
        "condition_key": "mcp_rate_limited",
        "phase": "research",
        "action": (
            "Stop calling the throttled MCP, fall back to WebSearch + WebFetch on canonical URLs and tag each fallback "
            "item `[rate-limited fallback]`."
        ),
        "banner_id": "mcp_ratelimit_fallback",
        "banner_text": (
            "Some research sources were retrieved via web-search fallback due to MCP service rate limits. Items tagged "
            "`[rate-limited fallback]` in research files; verify the canonical URLs in the source pack."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "mcp_soft_cap_exceeded",
        "phase": "research",
        "action": (
            "A free MCP server passed its per-run soft cap (telemetry only): note it and write from "
            "what was gathered (§4.3, D-166)."
        ),
        "banner_id": "mcp_soft_cap_exceeded",
        "banner_text": (
            "MCP soft cap exceeded: {server} made {count} calls this run, past the soft cap. "
            "Verify against primary sources before client use."
        ),
        "banner_params": ["server", "count"],
    },
    {
        "condition_key": "research_layers_partial",
        "phase": "research",
        "action": (
            "After the re-dispatch budget is spent, continue with the valid layers and push the missing ones to "
            "`drafting_warnings[]` (§2.1 row 5)."
        ),
        "banner_id": "research_partial",
        "banner_text": "Some research layers did not complete; the memo rests on the layers that succeeded.",
        "banner_params": [],
    },
    {
        "condition_key": "research_insufficient_budget_consumed",
        "phase": "research_sufficiency",
        "action": "Proceed to drafting; the memo must carry an 'Open questions / unverified facts' section.",
        "banner_id": "research_insufficient",
        "banner_text": (
            "Research sufficiency: insufficient. Open questions disclosed in the memo — do not act on it without "
            "further investigation."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "sufficiency_reviewer_failed",
        "phase": "research_sufficiency",
        "action": "Re-dispatch once with error context; on a second failure treat the verdict as `insufficient`.",
        "banner_id": "sufficiency_unavailable",
        "banner_text": "Research sufficiency review unavailable; defaulting to insufficient status.",
        "banner_params": [],
    },
    {
        "condition_key": "sufficiency_subset_u_unresolved",
        "phase": "research_sufficiency_followup_pending",
        "action": (
            "Promote the remaining Subset U gaps to `drafting_warnings[]`; the writer carries assumption-based caveats "
            "into the draft."
        ),
        "banner_id": "assumptions_after_followup",
        "banner_text": (
            "Some facts material to the analysis remained ambiguous after follow-up. Memo proceeds on conservative "
            "default assumptions documented in the Assumptions section."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "research_insufficient_user_continue",
        "phase": "research_insufficient_pending",
        "action": "User answered `continue`: go to `currency_check` with `drafting_warnings[]` set (§2.1 row 8).",
        "banner_id": "continue_with_caveats",
        "banner_text": (
            "Research was insufficient and you chose to continue: findings are provisional — verify before client use."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "currency_blocking_issues",
        "phase": "currency_check",
        "action": (
            "Drop the unverifiable source from the source-pack candidates and reference the replacement guidance."
        ),
        "banner_id": "currency_blocking",
        "banner_text": "Currency check raised {count} blocking issue(s); affected sources flagged in the source pack.",
        "banner_params": ["count"],
    },
    {
        "condition_key": "currency_checker_failed",
        "phase": "currency_check",
        "action": "Mark every unchecked source `unchecked` and continue to `source_pack` (§2.1 row 9).",
        "banner_id": "currency_unavailable",
        "banner_text": "Currency check unavailable; verify every source manually before client use.",
        "banner_params": [],
    },
    {
        "condition_key": "source_pack_incomplete",
        "phase": "source_pack",
        "action": "Freeze the snapshot with the fields that exist and flag the missing ones.",
        "banner_id": "source_pack_incomplete",
        "banner_text": "Source pack incomplete; verify citations manually.",
        "banner_params": [],
    },
    {
        "condition_key": "gate_defaults_applied",
        "phase": None,
        "action": (
            "After `attempts.gate_parse_errors` is exhausted apply the documented defaults; never set "
            "`assumptions_accepted=true` (§2.4 c)."
        ),
        "banner_id": "gate_defaults",
        "banner_text": (
            "Some answers were not recognised; documented defaults were applied and assumptions were not accepted."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "plan_forced_approve",
        "phase": "plan_approval_pending",
        "action": "After `attempts.plan_edit` is exhausted force-approve the last submitted plan version (§2.2).",
        "banner_id": "plan_forced_approve",
        "banner_text": "Plan edit budget exhausted; the last submitted plan version was approved automatically.",
        "banner_params": [],
    },
    {
        "condition_key": "writer_failed",
        "phase": "drafting",
        "action": (
            "Re-dispatch once (`single_dispatch_retry`); on a second failure finalize as `failed` with whatever was "
            "salvaged (§2.1 row 12)."
        ),
        "banner_id": "drafting_incomplete",
        "banner_text": "Drafting incomplete — partial draft delivered; manual completion required.",
        "banner_params": [],
    },
    {
        "condition_key": "revision_writer_failed",
        "phase": "revision_loop",
        "action": (
            "The writer returned the pre-seeded draft unchanged on both attempts: the copy is not a new version. "
            "Leave the loop on the last reviewed version under manual review, with its open blockers recorded (D-153)."
        ),
        "banner_id": "revision_incomplete",
        "banner_text": (
            "Revision incomplete — the writer could not produce a changed draft; the last reviewed version is "
            "delivered for manual review with its open blockers listed."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "lint_not_converged",
        "phase": "drafting",
        "action": (
            "After `config.lint_fix_rounds` go to `revision_loop` with `lint.json` and `citations.json` as reviewer "
            "input (§2.1 row 12)."
        ),
        "banner_id": "lint_not_converged",
        "banner_text": "Automated checks did not converge; the remaining findings are attached for review.",
        "banner_params": [],
    },
    {
        "condition_key": "reviewer_json_invalid",
        "phase": "revision_loop",
        "action": "Retry once (`reviewer_json_retry`), then substitute a stub review and continue.",
        "banner_id": "reviewer_output_malformed",
        "banner_text": (
            "Revision loop forced exit at iteration {iteration} — {count} reviewer output(s) malformed; latest draft "
            "delivered."
        ),
        "banner_params": ["iteration", "count"],
    },
    {
        "condition_key": "mediator_failed",
        "phase": "revision_loop",
        "action": "Exit the loop at the last validated draft version.",
        "banner_id": "mediator_unavailable",
        "banner_text": "Mediation unavailable; exited at the last validated draft v{version}.",
        "banner_params": ["version"],
    },
    {
        "condition_key": "max_iterations_with_blockers",
        "phase": "revision_loop",
        "action": (
            "Forced exit: `final_status = forced_exit_on_v<N>_with_remaining_issues`, blockers listed in the "
            "Status section."
        ),
        "banner_id": "unresolved_blockers",
        "banner_text": (
            "REVIEWER NOTES NOT FULLY RESOLVED — {count} blocking issue(s) remain (listed in the Status "
            "section)."
        ),
        "banner_params": ["count"],
    },
    {
        "condition_key": "client_readiness_manual_review",
        "phase": "client_readiness",
        "action": "Proceed to export; the blocker list is printed in the Status section of the memo.",
        "banner_id": "manual_review_required",
        "banner_text": "Client-readiness: manual_review_required. Blocking issues listed in the Status section.",
        "banner_params": [],
    },
    {
        "condition_key": "client_polish_budget_consumed",
        "phase": "client_readiness",
        "action": "Proceed to export with the last lint-clean version.",
        "banner_id": "polish_concerns_remain",
        "banner_text": "Client-readiness: post-polish concerns remain; verify before client delivery.",
        "banner_params": [],
    },
    {
        "condition_key": "client_readiness_reviewer_failed",
        "phase": "client_readiness",
        "action": "After the retry budget treat the verdict as `manual_review_required` and export.",
        "banner_id": "readiness_unavailable",
        "banner_text": "Client-readiness review unavailable; treated as manual review required.",
        "banner_params": [],
    },
    {
        "condition_key": "no_checked_draft",
        "phase": "export",
        "action": (
            "Export the last draft version with `final_status = manual_review_required_on_v<N>` and the blocker list "
            "(§2.1 row 15)."
        ),
        "banner_id": "no_checked_draft",
        "banner_text": (
            "No draft version passed lint and citation checks; the last version is delivered for manual review."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "docx_render_failed",
        "phase": "export",
        "action": "Render the stdlib-only markdown fallback (`deliverable.md`) with `[n]` footnote markers (§5.5).",
        "banner_id": "docx_export_failed",
        "banner_text": (
            "docx export failed — the markdown deliverable is authoritative. Convert manually before client use."
        ),
        "banner_params": [],
    },
    {
        "condition_key": "docx_invalid",
        "phase": "export",
        "action": (
            "Rename the file to `memo-<slug>.invalid.docx` and make the markdown fallback the deliverable (§5.5)."
        ),
        "banner_id": "docx_invalid",
        "banner_text": "The generated docx failed validation; the markdown deliverable is authoritative.",
        "banner_params": [],
    },
    {
        "condition_key": "unresolved_reference_in_fallback",
        "phase": "export",
        "action": "Emit `[unresolved: <id>]` in the markdown fallback instead of dropping the reference (§5.5).",
        "banner_id": "unresolved_reference",
        "banner_text": "Some references could not be resolved and are marked `[unresolved: …]` in the deliverable.",
        "banner_params": [],
    },
    {
        "condition_key": "output_folder_write_failed",
        "phase": "export",
        "action": "Keep the artifact in the working directory and print its absolute path in chat.",
        "banner_id": "output_folder_unavailable",
        "banner_text": "Output folder write failed; the final artifact stays in the working directory at {work_dir}.",
        "banner_params": ["work_dir"],
    },
    {
        "condition_key": "publish_failed",
        "phase": "export",
        "action": (
            "Skip the copy into the publish folder and end the run normally; the deliverable, the summary and "
            "the source texts stay in the working directory (D-109)."
        ),
        "banner_id": "publish_failed",
        # D-181: the failure class travels as a parameter, so `banner_text_for` re-renders it into
        # the memo language with the diagnostic instead of dropping it. `finalize` passes the whole
        # ` (<ExceptionClass>)` suffix, so the English text stays the byte string it always was.
        "banner_text": (
            "The finished result could not be copied to the publish folder; it stays in the working directory, "
            "at the path the final message prints.{failure}"
        ),
        "banner_params": ["failure"],
    },
    {
        "condition_key": "dashboard_unavailable",
        "phase": None,
        "action": (
            "Continue without the dashboard: `mf task dashboard --unavailable` records the reason, the "
            "`next` answers stop carrying the §7.5 block and no step is retried (D-87)."
        ),
        "banner_id": "dashboard_unavailable",
        "banner_text": (
            "The live dashboard could not be published ({reason}); the run continued and reported progress "
            "through the ordinary step lines."
        ),
        "banner_params": ["reason"],
    },
    {
        "condition_key": "salvage_state_corrupt",
        "phase": None,
        "action": "`mf finalize --salvage` rebuilds the summary from the files on disk without jsonschema (M9).",
        "banner_id": "state_corrupt",
        "banner_text": "state.json was unreadable; the summary was reconstructed from the files on disk.",
        "banner_params": [],
    },
    {
        "condition_key": "universal_fallback",
        "phase": None,
        "action": (
            "Write `fallback-summary.md` with task_id, last successful phase, what was learned and what failed; set "
            "`final_status = fallback_summary_delivered`."
        ),
        "banner_id": "fallback_summary_delivered",
        "banner_text": (
            "The pipeline could not complete; a fallback summary of everything gathered is delivered instead."
        ),
        "banner_params": [],
    },
]

BY_CONDITION: dict[str, dict] = {row["condition_key"]: row for row in FALLBACKS}

BANNER_IDS: tuple[str, ...] = tuple(
    row["banner_id"] for row in FALLBACKS if row["banner_id"] is not None
)

BY_BANNER: dict[str, dict] = {
    row["banner_id"]: row for row in FALLBACKS if row["banner_id"] is not None
}

DASHBOARD_LABELS: dict[str, str] = {
    "mcp_partial": "Partial MCP coverage; the gap is noted in the research files.",
    "mcp_soft_cap_exceeded": (
        "An MCP server passed its per-run soft cap."
    ),
    "currency_blocking": (
        "Currency check raised blocking issues; affected sources are flagged in the source pack."
    ),
    "reviewer_output_malformed": (
        "Revision loop forced exit — reviewer output was malformed; the latest draft is delivered."
    ),
    "mediator_unavailable": "Mediation unavailable; the run exited at the last validated draft.",
    "unresolved_blockers": (
        "REVIEWER NOTES NOT FULLY RESOLVED — blocking issues remain (listed in the Status section)."
    ),
    "output_folder_unavailable": (
        "Output folder write failed; the final artifact stays in the working directory."
    ),
    "publish_failed": (
        "The finished result could not be copied to the publish folder; it stays in the working "
        "directory, at the path the final message prints."
    ),
    "dashboard_unavailable": (
        "The live dashboard could not be published; the run continued and reported progress in chat."
    ),
}
"""Parameter-free stand-ins for the banner texts of rows with `banner_params` (D-88).

The rendered text of those rows carries diagnostics — an absolute `work_dir`, an arbitrary error
`reason`, counts — which must never leave the machine for the published dashboard; `dashboard_label`
serves these instead. D-176b: `EN["ui"]["banners"]` mirrors this table key for key, and the four
packs translate it, so the page prints the stand-in in the interface language.
"""

REWORDED_BANNERS: frozenset[str] = frozenset({"unresolved_blockers", "manual_review_required"})
"""D-216: banners whose wording changed after states were saved with the old text ("listed in the
appendix"). `banner_text_for` re-renders them from `banner_id` + `params` in English too, so a state
saved before the change prints the current sentence; no state is migrated."""

DASHBOARD_UNAVAILABLE = "dashboard_unavailable"
"""Condition key and banner id of the §7.5 row; `machine` and `task` share this one name (D-87)."""


def get(condition_key: str) -> dict:
    """Return the fallback row for a condition key; raises KeyError when unknown."""
    return BY_CONDITION[condition_key]


def banner(condition_key: str, **params: object) -> dict | None:
    """Render the banner of a fallback row, or None when the row has no user-facing banner."""
    row = BY_CONDITION[condition_key]
    if row["banner_id"] is None:
        return None
    missing = [name for name in row["banner_params"] if name not in params]
    if missing:
        raise ValueError(f"missing_banner_params: {missing}")
    text = row["banner_text"].format(**params) if row["banner_params"] else row["banner_text"]
    rendered: dict = {
        "banner_id": row["banner_id"],
        "condition_key": condition_key,
        "text": text,
    }
    if row["banner_params"]:
        # D-166: the per-server soft-cap banners share one `banner_id`, so the params travel
        # with the banner — `finalize.collect_banners` dedups them by server.
        rendered["params"] = {name: str(params[name]) for name in row["banner_params"]}
    return rendered


def same_banner(rows: list | None, banner: dict) -> bool:
    """D-248: a banner is recorded once — a row is the same banner when it differs only in `at`."""
    wanted = dict(banner, at=None)
    return any(isinstance(row, dict) and dict(row, at=None) == wanted for row in rows or ())


def banner_text_for(row: object, language: str) -> str:
    """The banner text the deliverable prints: the memo language's, or the stored one (D-175).

    `banner_id` + `params` re-render the text from `memo.banners.<id>` of the memo language, so
    a banner raised before a language change prints in the language the run ends in. The stored
    `text` is kept — and returned — for English, for string rows, for unknown ids and for rows
    that predate per-banner params: none of those have a pack entry to render from. D-216: the
    `REWORDED_BANNERS` are re-rendered in English too.
    """
    if not isinstance(row, dict):
        return str(row or "")
    banner_id = row.get("banner_id")
    stored = str(row.get("text") or banner_id or "")
    code = i18n.normalize(language) or i18n.DEFAULT
    if not isinstance(banner_id, str):
        return stored
    if code == i18n.DEFAULT and banner_id not in REWORDED_BANNERS:
        return stored
    declared = BY_BANNER.get(banner_id)
    if declared is None:
        return stored
    if declared["banner_params"] and not isinstance(row.get("params"), dict):
        return stored
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    try:
        return i18n.t(code, f"memo.banners.{banner_id}", **params)
    except KeyError:
        return stored


def dashboard_label(banner_id: str, language: str = i18n.DEFAULT) -> str:
    """Static, parameter-free text for a banner id — the only banner content §7.5 publishes (D-88).

    Rows without `banner_params` are constants already, so their `banner_text` is served as is;
    formatted rows are served from `DASHBOARD_LABELS`. Unknown ids get an empty label.

    D-176b: the page is an interface surface, so the label is served in `language` — the run's
    **interface** language, not the memo one. A constant row is the same sentence as
    `memo.banners.<id>`, and is read from there; a formatted row has no pack entry of its own,
    because the page must never print the rendered text (an absolute `work_dir`, an arbitrary
    error `reason`, per-server counts), so its parameter-free stand-in lives in `ui.banners.<id>`.
    English is answered from the constants above, byte for byte as before, and an unreadable pack
    or a pack without the key degrades to English rather than failing a `next` answer.
    """
    row = BY_BANNER.get(banner_id)
    if row is None:
        return ""
    if row["banner_params"]:
        english, key = DASHBOARD_LABELS.get(banner_id, banner_id), f"ui.banners.{banner_id}"
    else:
        english, key = row["banner_text"], f"memo.banners.{banner_id}"
    code = i18n.normalize(language) or i18n.DEFAULT
    if code == i18n.DEFAULT:
        return english
    try:
        return i18n.t(code, key)
    except (KeyError, i18n.PackUnavailable):
        return english
