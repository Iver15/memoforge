"""The English language pack — the floor every other pack falls back to (D-168).

`EN` holds today's literals moved verbatim: the same punctuation, the same placeholders.
Never reword English while extracting — other tasks move their keys here too, so the tree
grows with the plans (plan 56 fills `ui`). Non-English packs (`lib/i18n/<code>.json`)
carry the same key tree; access goes only through `memoforge.i18n`.
"""

from __future__ import annotations

EN: dict = {
    "code": "en",
    "name": "English",
    "memo": {
        # D-168 / D-174: the English literals lint, the citation audit and the sentence splitter
        # recognise a memo by — the canonical section titles, the Risk label and its four verdicts,
        # the disclaimer pattern, the placeholder list and the abbreviations (sorted) that must not
        # end a sentence. `lint.grammar("en")` compiles today's recognizers from them unchanged.
        "docx_lang": "en-US",
        "sections": {
            "executive_summary": "Executive summary",
            "background": "Background and definitions",
            "facts": "Facts, assumptions and limitations",
            "assumptions": "Key assumptions",
            "conclusion": "Conclusion and recommendations",
            "recommendations": "Recommendations",
        },
        "risk": {
            "label": "Risk",
            "levels": {
                "high": "high",
                "medium": "medium",
                "low": "low",
                "undetermined": "undetermined",
            },
        },
        "disclaimer_pattern": r"disclaimer|assumptions?\b[^.]{0,80}\bnot\b[^.]{0,40}\bconfirm",
        "placeholders": ["TODO", "TBD", "FIXME", "XXX", "PLACEHOLDER", "Lorem ipsum", "[insert"],
        "placeholders_ignore_case": True,
        "abbreviations": [
            "al", "art", "arts", "artt", "cf", "ch", "dir", "eg", "etc", "fig", "ie",
            "no", "nos", "nr", "p", "para", "paras", "pp", "pt", "reg", "sec", "secs",
            "ss", "vs", "абз", "гл", "др", "п", "пп", "ред", "см", "ст", "стт",
        ],
        "lint_off": [],
        # D-168: `en` reads `lib/ai-tells.txt`; the list stays empty here.
        "ai_tells": [],
        # D-175: every label the code itself prints into `deliverable.md`, `deliverable.docx` and the
        # published `sources/source-pack.md`. Markdown and docx decoration — `## `, `**…**`, `_…_`,
        # backticks, bullets — stays in the renderers, so one label serves both deliverables.
        "labels": {
            "sources_heading": "Sources",
            "no_sources_cited": "No sources were cited in this draft.",
            "appendix_heading": "Appendix — Assumptions & Unverified Sources",
            "appendix_more": "… and {count} more in summary.md",
            "assumptions_label": "Assumptions carried into the analysis",
            "unverified_label": "Unverified sources",
            "unresolved_label": "Unresolved references",
            "unresolved_bullet": (
                "{raw_id} — not in the frozen source pack or the quote registry; "
                "marked {marker} in the text."
            ),
            "currency_unavailable_note": (
                "Source currency was not checked in this run (currency checker unavailable); "
                "verify before client use."
            ),
            # The appendix notes of one unverified source; `{status}` is a machine token, printed raw.
            "us_citation_note": "US citation {status}",
            "eu_syntax_note": "EU identifier syntax not verified",
            "currency_note": "currency {status}",
            "no_saved_text_note": (
                "no saved source text — the citation could not be checked against the source"
            ),
            "link_note": "link {status}",
            "status_label": "Status",
            "status_lead": (
                "Final status: {final_status}. The pipeline did not sign this memorandum off; "
                "the points below are unresolved and must be checked before client use."
            ),
            "status_banners_label": "Pipeline notices",
            "status_issues_label": "Unresolved blocking issues",
            # The published `sources/source-pack.md`: the frozen pack, else the registry listing.
            "source_pack_heading": "Source pack (frozen)",
            "frozen_at": "Frozen at: {value}",
            "entries_heading": "Entries",
            "snapshot_heading": "Snapshot",
            "source_pack_columns": [
                "source_id", "layer", "tier", "use_in_memo", "weight", "currency", "citation",
            ],
            "snapshot_row": "{source_id} — raw_sha256 {raw_sha256}",
            "none": "none",
            "registered_sources_heading": "Sources (registered, not frozen)",
            "no_registered_sources": "(no sources were registered)",
            "registered_source_row": (
                "{source_id} — {title}; {citation_form}; tier {tier}; currency {status}"
            ),
            "untitled": "(untitled)",
            "no_citation_form": "(no citation form)",
        },
        # D-175: the yellow banner table of the docx — its headline per `final_status` prefix, its
        # subtitle and the two list headings. The banner texts themselves are `memo.banners`.
        "banner_titles": {
            "forced_exit": "REVIEWER NOTES NOT FULLY RESOLVED",
            "accepted_early": "USER ACCEPTED EARLY — REMAINING ISSUES",
            "manual_review_required": "MANUAL REVIEW REQUIRED",
            "fallback_notice": "PIPELINE FALLBACK NOTICE — REVIEW BEFORE CLIENT USE",
            "subtitle": "Manual check recommended before relying on this memorandum.",
            "final_status": "Final status: {final_status}.",
            "fallbacks_heading": "Pipeline fallbacks that fired during this run:",
            "reasons_heading": "Reasons recorded for manual review:",
        },
        # D-175a: the words of a citation that are a label, not an identity. The pinpoint written in
        # `[[src:<id> <pinpoint>]]` stays canonical English everywhere; these are its display forms,
        # and `ibid` the display form of a repeated one. Case names, court names, act kinds,
        # `(n N)`, CELEX/ECLI/ELI and `[unresolved: …]` are identity and stay English.
        "citation": {
            "ibid": "ibid",
            "art": "art",
            "arts": "arts",
            "para": "para",
            "paras": "paras",
            "recital": "recital",
            "recitals": "recitals",
            "annex": "annex",
            "annexes": "annexes",
            "s": "s",
            "ss": "ss",
            "reg": "reg",
            "regs": "regs",
            "p": "p",
            "pp": "pp",
            "cited_at": "cited at ",
            "also_cited_at": "also cited at ",
            "checked": "checked ",
            "currency": "currency ",
        },
        # D-175: month names of a soft-law date inside a citation (`18 June 2021`), January first.
        "months": [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        # D-175 (part 2): the banner texts are the `banner_text` rows of `fallbacks.py`,
        # moved verbatim — placeholders included. `fallbacks.banner()` keeps building the
        # stored English text; the deliverable re-renders it from here at render time.
        "banners": {
            "mcp_unavailable": (
                "MCP servers unavailable. Research conducted via public WebFetch only — verify against "
                "primary sources before client use."
            ),
            "mcp_partial": "Partial MCP coverage — only {available} was reachable.",
            "sources_unreachable": "Some primary sources were unreachable; gaps disclosed in research files.",
            "mcp_ratelimit_fallback": (
                "Some research sources were retrieved via web-search fallback due to MCP service rate limits. "
                "Items tagged `[rate-limited fallback]` in research files; verify the canonical URLs in the "
                "source pack."
            ),
            "mcp_soft_cap_exceeded": (
                "MCP soft cap exceeded: {server} made {count} calls this run, past the soft cap. "
                "Verify against primary sources before client use."
            ),
            "research_partial": "Some research layers did not complete; the memo rests on the layers that succeeded.",
            "research_insufficient": (
                "Research sufficiency: insufficient. Open questions disclosed in the memo — do not act on it "
                "without further investigation."
            ),
            "sufficiency_unavailable": "Research sufficiency review unavailable; defaulting to insufficient status.",
            "assumptions_after_followup": (
                "Some facts material to the analysis remained ambiguous after follow-up. Memo proceeds on "
                "conservative default assumptions documented in the Assumptions section."
            ),
            "continue_with_caveats": (
                "Research was insufficient and you chose to continue: findings are provisional — verify before "
                "client use."
            ),
            "currency_blocking": (
                "Currency check raised {count} blocking issue(s); affected sources flagged in the source pack."
            ),
            "currency_unavailable": "Currency check unavailable; verify every source manually before client use.",
            "source_pack_incomplete": "Source pack incomplete; verify citations manually.",
            "gate_defaults": (
                "Some answers were not recognised; documented defaults were applied and assumptions were not accepted."
            ),
            "plan_forced_approve": (
                "Plan edit budget exhausted; the last submitted plan version was approved automatically."
            ),
            "drafting_incomplete": "Drafting incomplete — partial draft delivered; manual completion required.",
            "revision_incomplete": (
                "Revision incomplete — the writer could not produce a changed draft; the last reviewed version is "
                "delivered for manual review with its open blockers listed."
            ),
            "lint_not_converged": (
                "Automated checks did not converge; the remaining findings are attached for review."
            ),
            "reviewer_output_malformed": (
                "Revision loop forced exit at iteration {iteration} — {count} reviewer output(s) malformed; "
                "latest draft delivered."
            ),
            "mediator_unavailable": "Mediation unavailable; exited at the last validated draft v{version}.",
            "unresolved_blockers": (
                "REVIEWER NOTES NOT FULLY RESOLVED — {count} blocking issue(s) remain (listed in the appendix)."
            ),
            "length_overflow": "The executive brief exceeds its word cap; a rerun in Full mode is recommended.",
            "manual_review_required": (
                "Client-readiness: manual_review_required. Blocking issues listed in the appendix."
            ),
            "polish_concerns_remain": "Client-readiness: post-polish concerns remain; verify before client delivery.",
            "readiness_unavailable": "Client-readiness review unavailable; treated as manual review required.",
            "no_checked_draft": (
                "No draft version passed lint and citation checks; the last version is delivered for manual review."
            ),
            "docx_export_failed": (
                "docx export failed — the markdown deliverable is authoritative. Convert manually before client use."
            ),
            "docx_invalid": "The generated docx failed validation; the markdown deliverable is authoritative.",
            "unresolved_reference": (
                "Some references could not be resolved and are marked `[unresolved: …]` in the deliverable."
            ),
            "output_folder_unavailable": (
                "Output folder write failed; the final artifact stays in the working directory at {work_dir}."
            ),
            "publish_failed": (
                "The finished result could not be copied to the publish folder; it stays in the working directory, "
                "at the path the final message prints.{failure}"
            ),
            "dashboard_unavailable": (
                "The live dashboard could not be published ({reason}); the run continued and reported progress "
                "through the ordinary step lines."
            ),
            "state_corrupt": "state.json was unreadable; the summary was reconstructed from the files on disk.",
            "fallback_summary_delivered": (
                "The pipeline could not complete; a fallback summary of everything gathered is delivered instead."
            ),
        },
        # D-175 (part 2): every string `finalize.build_summary` and `build_fallback_summary`
        # print, moved verbatim. Decoration (`# `, `## `, `- `, backticks, `**…**`) stays in the
        # module, so one entry serves every path that prints it.
        "summary": {
            "title": "memoforge run summary — {task_id}",
            "status": "- Status: **{final_status}**",
            "terminal_phase": "- Terminal phase: `{phase}`",
            "mode": "- Mode: {mode}",
            "question": "- Question: {question}",
            "reason": "- Reason given to `mf finalize`: {reason}",
            "salvaged": "- Produced by `mf finalize --salvage` (degraded path, M9).",
            "manual_review_reasons": "## Manual-review reasons",
            "fallback_banners": "## Fallback banners",
            "mcp_calls": "## MCP calls",
            "remaining_blocking_issues": "## Remaining blocking issues",
            "paths": "## Paths",
            "work_dir": "- Work dir: `{path}`",
            "deliverable": "- Deliverable: `{name}`",
            "rendered_from": "- Rendered from: `{name}`",
            "state": "State",
            "journal": "Journal",
            "drafting_warnings": "## Drafting warnings",
            "none": "- none",
            "none_yet": "- none yet",
            "not_selected": "(not selected)",
            "query_unavailable": "(query unavailable)",
            "phase_unknown": "(unknown)",
            "banner_row": "{text} (`{banner_id}`)",
            "mcp_quota_row": "{server}: {used} of {limit}",
            "mcp_plain_row": "{server}: {used}",
            "pack_unavailable": "Language pack `{code}` could not be read; generated labels are in English.",
            "fallback_title": "memoforge fallback summary — {task_id}",
            "fallback_lead": (
                "The pipeline could not produce a memorandum. Everything that was gathered is listed below."
            ),
            "fallback_task": "- Task: {query}",
            "fallback_last_phase": "- Last phase reached: {phase}",
            "fallback_mode": "- Mode: {mode}",
            "fallback_reason": "- Reported reason: {reason}",
            "fallback_artifacts": "## Artifacts on disk",
            "fallback_none": "- (none)",
            "fallback_open_questions": "## Open questions and unverified facts",
        },
        # D-173a: the client-facing sentence of a blocker. Deterministic blockers print the
        # short rule name (`memo.rules.<rule_id>`, one per L- and C-rule); synthesized
        # `unverified_hard_fail` blockers print `memo.blockers.hard_fail_unknown`. For `en` the
        # field is never created, so English output stays byte-identical.
        "rules": {
            "L-01": "Long sentence",
            "L-02": "Long paragraph",
            "L-03": "Em-dash usage",
            "L-04": "AI tell phrase",
            "L-05": "Heading structure",
            "L-06": "Summary-conclusion correspondence",
            "L-07": "Risk line format",
            "L-08": "Blockquote markup",
            "L-09": "Duplicate quotation",
            "L-10": "Brief word cap",
            "L-11": "Leftover placeholder",
            "L-12": "Template sections",
            "L-13": "Summary bullet format",
            "L-14": "Missing disclaimer",
            "L-15": "Duplicate section anchor",
            "C-01": "Unknown source",
            "C-02": "Quote mismatch",
            "C-03": "Prohibited source",
            "C-04": "Unverified risk carrier",
            "C-05": "Source outside freeze",
            "C-06": "Pinpoint format",
            "C-07": "Uncited rule source",
            "C-08": "Citation without saved text",
        },
        "blockers": {
            "hard_fail_unknown": "Checklist item {checklist_id} could not be verified.",
        },
        # D-175 (part 2): prose the code writes into `drafting_warnings[]`, created in the
        # memo language at creation time. The mode-less out-of-scope prefix carries the `this`
        # fallback word, so the English bytes of `{mode or 'this'}` are unchanged.
        "warnings": {
            "no_findings_for_layers": "no findings for layer(s): {layers}",
            "continue_with_incomplete_research": "the user chose to continue with incomplete research",
            "currency_unchecked": "source currency could not be verified",
            "out_of_scope_prefix": "Out of scope for {mode} mode: ",
            "out_of_scope_prefix_no_mode": "Out of scope for this mode: ",
        },
        # D-178a: the plain sentence `probe.fixture_draft` writes into the fixture draft, taken
        # from its own `disclaimer_pattern` so the L-14 check passes in every language.
        "probe_disclaimer": (
            "Assumptions in this memo were applied without confirmation and a disclaimer therefore "
            "applies: the retention conclusion is not confirmed by the client."
        ),
    },
    "ui": {},
}
