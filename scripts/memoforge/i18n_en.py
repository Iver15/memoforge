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
        # D-190: the three bold labels of the classical facts section — `memo.facts.*_label`.
        # The writer opens one block per label (`**Facts**`, `**Assumptions**`, `**Limitations**`)
        # and omits a block with nothing to say; the labels reach the writer as `${facts_labels}`.
        "facts": {
            "facts_label": "Facts",
            "assumptions_label": "Assumptions",
            "limitations_label": "Limitations",
        },
        # D-175: every label the code itself prints into `deliverable.md`, `deliverable.docx` and the
        # published `sources/source-pack.md`. Markdown and docx decoration — `## `, `**…**`, `_…_`,
        # backticks, bullets — stays in the renderers, so one label serves both deliverables.
        "labels": {
            "sources_heading": "Sources",
            "no_sources_cited": "No sources were cited in this draft.",
            "appendix_heading": "Appendix — Unverified Sources",
            "appendix_more": "… and {count} more in summary.md",
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
            # D-200: the freeze demoted a `full_text` file that changed or vanished; the gate-11
            # digest prints it as an exception row, so the warning is read, not just returned.
            "full_text_integrity_note": (
                "saved text of {source_id} changed after it was saved by code; "
                "it now counts as an agent copy"
            ),
            # D-201: a PDF whose text layer could not be read — no `pypdf`, or a scan. The original
            # is on disk and travels to the client; nothing about it was certified by code.
            "pdf_unverified_note": (
                "the original was saved; its requisites and quotations were not checked by code"
            ),
            # D-201: the original is not the file the freeze pinned — edited, deleted, or never
            # pinned at all — so `sources/<id>.pdf` is not exported. One sentence for all three,
            # because the client-facing fact is the same: the delivery continues, and this line
            # says why the folder holds no PDF.
            "pdf_export_mismatch_note": (
                "the original PDF of {source_id} no longer matches what the freeze recorded "
                "and was not exported"
            ),
            # D-201 fix round 3: the same rule for the saved text, and its own key — the client is
            # looking for a particular missing file, and a line that does not say which artefact it
            # means does not explain the absence.
            "text_export_mismatch_note": (
                "the saved text of {source_id} no longer matches what the freeze recorded "
                "and was not exported"
            ),
            # D-204: what the saved text of a cited `critical` source is, when it is not the whole
            # document — the lines that go with the extended C-08. A short act published without
            # its reasoning is not «an excerpt of a longer text», so it has a line of its own.
            # Final review E: each is true of every producer of its kind — an MCP answer registered
            # as `excerpt` was not saved by code, and a code-saved file the freeze demoted to
            # `agent_summary` was not copied by the agent.
            "excerpt_note": "an excerpt, not the whole document",
            "agent_summary_note": "a text the code has not certified as the document itself",
            "no_reasoning_note": "only the operative part was published; there is no reasoning to check",
            # D-204: a C-09 finding that survived the v1 lint-fix round — one per pinpoint.
            "pinpoint_not_in_raw_note": "pinpoint {pinpoint} was not found in the saved text of {source_id}",
            "link_note": "link {status}",
            # D-192: the Sources annex of a source with no public url — the database it came from
            # instead of an address. `{server}` is a bundled server's label, or `legal_database`
            # when the endpoint belongs to none.
            "retrieved_from_note": "text retrieved from {server}",
            "legal_database": "a legal database",
            "status_label": "Status",
            "status_lead": (
                "Final status: {final_status}. The pipeline did not sign this memorandum off; "
                "the points below are unresolved and must be checked before client use."
            ),
            "status_banners_label": "Pipeline notices",
            "status_issues_label": "Unresolved blocking issues",
            # D-197: a blocker row is `<severity> · <where> · <text>`; `s-5-1` is a machine anchor
            # and reaches the reader as «section 5.1», `general`/`document` as the whole memo.
            "section_word": "section",
            "whole_memo": "whole memo",
            # D-237: the text of a blocker row without its client sentence in a memo that is not English.
            "status_issue_without_client_text": (
                "the reviewer's finding here is not confirmed as fixed; its text is in summary.md"
            ),
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
        # D-197: no raw code reaches the reader. `final_status` is keyed by its FAMILY — the code
        # without its `_on_v<N>` / `_v<N>` version, which travels as `{version}` — and a family the
        # pack does not know falls back to the raw code rather than raising in the renderer.
        "status_names": {
            "approved": "approved on version {version}",
            "client_ready": "client-ready on version {version}",
            "accepted_early": "accepted early on version {version}",
            "manual_review_required": "manual review required on version {version}",
            "forced_exit_with_remaining_issues": (
                "released with reviewer notes unresolved on version {version}"
            ),
            "delivered": "delivered",
            "failed": "stopped without a finished memorandum",
            "cancelled_by_user": "cancelled at your request",
            "fallback_summary_delivered": "a research summary delivered instead of a memorandum",
        },
        # D-197: the codes `final_status_reasons[]` accumulates, as the banner states them.
        "status_reasons": {
            "unresolved_blockers": "blocking reviewer notes remain unresolved",
            "incomplete_review": "a review round did not complete",
            "all_reviewers_failed": "no reviewer returned a usable verdict",
            "regression_forced_exit": "a revision made the draft worse, so the review loop stopped",
            "step_loop": "a pipeline step repeated without making progress",
            "writer_failed": "the writer could not produce a revised draft",
            "no_checked_draft": "no draft version passed the automated checks",
            "export_reused_untouched": "an earlier export was delivered unchanged",
            # D-211: the settlement of the open majors at the last reader; D-237: of any substantive class.
            "open_substance_majors": (
                "a substantive reviewer finding is left for a lawyer's decision or was not verifiably fixed"
            ),
            "polish_out_of_scope": "the final polish went beyond its instructions, so the text before it is delivered",
            "polish_recheck_blocker": "the check of the final polish found a blocking problem",
        },
        # D-197: the severity of a blocker row. English keeps today's words, which are already human.
        "severity": {
            "blocker": "blocker",
            "major": "major",
            "minor": "minor",
            "info": "info",
        },
        # D-197: the recorded `currency.status` / `liveness.status` tokens as the reader sees them.
        # English keeps every token that is already a word and spells out only the snake_case ones.
        "currency_names": {
            "current": "current",
            "outdated_but_usable": "outdated but usable",
            "do_not_use": "do not use",
            "manual_check": "manual check",
            "unchecked": "unchecked",
            "amended": "amended",
            "repealed": "repealed",
            "superseded": "superseded",
        },
        "link_names": {
            "ok": "ok",
            "redirect": "redirect",
            "dead": "dead",
            "changed": "changed",
            "unchecked": "unchecked",
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
                "REVIEWER NOTES NOT FULLY RESOLVED — {count} blocking issue(s) remain (listed in the Status "
                "section)."
            ),
            "manual_review_required": (
                "Client-readiness: manual_review_required. Blocking issues listed in the Status section."
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
            # D-197: the first line is read by a human, so it names the status; the raw code stays
            # next to it in backticks, because `summary.md` is also the technical record of the run.
            "status": "- Status: **{status_name}** (`{final_status}`)",
            "terminal_phase": "- Terminal phase: `{phase}`",
            "mode": "- Mode: {mode}",
            "question": "- Question: {question}",
            "reason": "- Reason given to `mf finalize`: {reason}",
            "salvaged": "- Produced by `mf finalize --salvage` (degraded path, M9).",
            "manual_review_reasons": "## Manual-review reasons",
            "fallback_banners": "## Fallback banners",
            "mcp_calls": "## MCP calls",
            "remaining_blocking_issues": "## Remaining blocking issues",
            # D-210: the substantive majors the review loop left open (`state.open_substance_majors`).
            "open_reviewer_findings": "## Open reviewer findings",
            "paths": "## Paths",
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
            "C-09": "Pinpoint not in saved text",
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
        # D-222: the decision brief of `/memoforge:brief` (plan 75A), written in the memo language.
        # `sections` are the five part headings the brief lint recognises; the three header labels,
        # the risk literal and `unconfirmed` reach the writer through `brief_lint.writer_labels`.
        # `checks` names every code an unverified brief can list in its banner — the BF/BC
        # checklist items, the B-rules and the four operational reasons of the driver.
        # `self_reference` is the lower-case B-09 list of words that point at the full memo.
        "brief": {
            "sections": {
                "main": "Bottom line",
                "conclusions": "Conclusions",
                "actions": "What to do",
                "assumptions": "What the answer depends on",
                "other": "Other points assessed",
            },
            "question_label": "Question",
            "date_label": "Date",
            "jurisdictions_label": "Jurisdictions",
            "unconfirmed": "not confirmed; a lawyer has to check this before anyone relies on it",
            "banners": {
                "title": "DECISION BRIEF — CHECK BEFORE RELYING ON IT",
                "subtitle": "The notes below say what was not confirmed.",
                "unclean_memo": (
                    "The memorandum this brief summarises ended with open points; the conclusions "
                    "that rest on them are marked as not confirmed."
                ),
                "unverified": "This brief did not pass every fidelity and clarity check: {checks}.",
                "too_long": "This brief is longer than three pages.",
            },
            "checks": {
                "BF-01": "a statement the memorandum does not make",
                "BF-02": "a condition the conclusion depends on is missing",
                "BF-03": "the degree of certainty differs from the memorandum",
                "BF-04": "an open point is presented as settled",
                "BF-05": "a conclusion of the memorandum is not covered",
                "BF-06": "a conclusion names a rule or a court it does not rest on",
                "BF-07": "the actions differ from the memorandum's recommendations",
                "BF-08": "an assumption that changes the answer is missing",
                "BF-09": "the question is not restated faithfully",
                "BC-01": "the bottom line does not answer the question",
                "BC-02": "the reasoning of a conclusion is hard to follow",
                "BC-03": "the language is not plain",
                "BC-04": "the brief does not stand on its own",
                "BC-05": "an action lacks an owner, a step or a time",
                "BC-06": "quotations or too many sources in a conclusion",
                "BC-07": "repetition, filler or stock phrases",
                "BC-08": "a heading that does not say what follows",
                "B-01": "longer than about three pages",
                "B-02": "parts missing or out of order",
                "B-03": "a conclusion not tied to a section of the memorandum",
                "B-04": "a section is neither covered nor listed as omitted",
                "B-05": "a risk level differs from the memorandum",
                "B-06": "a citation differs from the memorandum",
                "B-07": "a quotation or a list of sources",
                "B-08": "a risk line in the wrong form",
                "B-09": "a reference to a document the reader does not have",
                "B-10": "a placeholder or a stock phrase left in the text",
                "B-11": "an amount that is not in the memorandum",
                "B-12": "a part is longer than its word budget",
                "writer_failed": "the brief could not be revised further",
                "fidelity_review_missing": "the fidelity check could not be run",
                "form_review_missing": "the clarity check could not be run",
                "render_failed": "the formatted document could not be produced",
            },
            # Whole words (B-09), so every form a reader would write is listed.
            "self_reference": [
                "memorandum", "memorandums", "memoranda", "memo", "memos", "see section", "full analysis",
            ],
        },
    },
    # D-176 / D-176a / D-172: the interface — every literal `phases.py`, `gates.py` and the two
    # user-facing functions of `machine.py` used to carry, moved verbatim. Indentation and
    # markdown decoration (`- `, `   `) stay in the renderers, the way `memo.labels` does it, so
    # one string serves every caller. Text-channel tokens (`approve`, `edit:`, `cancel`,
    # `proceed`, `continue`, `brief`, `full`, `standard`, `1A`, `3:`) are canonical and stand
    # unchanged inside their backticks in every pack.
    "ui": {
        # D-95: the plain name of every phase, from the user's point of view. `phases.label`
        # reads it; `docs/phases.md` keeps calling it in English.
        "phases": {
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
        },
        "gates": {
            # The numbered question block gates 2 and 7 share (§2.4 `1A 2C 3: free text`).
            "question_line": "{index}. {question}",
            "option_line": "{letter}) {label} — {description}",
            "default_line": "{question} — assuming: {default}",
            "default_line_confidence": "{question} — assuming: {default} (confidence: {confidence})",
            "default_if_wrong": "If that is wrong: {value}",
            "slash_line": "Reply here, or run `/memoforge:continue {task_id} {example}`.",
            # Gate 2 — intake.
            "intake_form": "Please answer, in the form `1A 2C 3: free text`:",
            "intake_no_questions": "No question needs your answer; reply `proceed` to continue.",
            "intake_defaults_heading": "Everything else is assumed as follows:",
            "intake_footer": "`proceed` accepts every assumption as written. `cancel` stops the task.",
            # Gate 7 — sufficiency follow-up.
            "followup_form": "Research left gaps only you can close. Answer as `1A 2C 3: free text`:",
            "followup_skipped": "Skipped, we assume: {default}",
            "followup_footer": "`proceed` accepts the assumptions above. `cancel` stops the task.",
            # Gate 8 — insufficient research. The gaps themselves are `drafting_warnings[]` and
            # stay in the memo language inside this frame (D-173b).
            "insufficient_lead": "Research did not reach the bar for a client-ready memo.",
            "insufficient_gaps_heading": "Open gaps:",
            "insufficient_continue": (
                "`continue` drafts anyway, with the gaps written into the memo as caveats."
            ),
            "insufficient_cancel": "`cancel` stops the task.",
            # Gate 4 — the plan digest of D-86 and its D-99 degradation.
            "plan_unreadable": "The research plan could not be read; edit or cancel.",
            "plan_digest_head": (
                "Plan — {classification}, jurisdictions: {jurisdictions}, "
                "estimated complexity: {complexity}."
            ),
            "plan_digest_unclassified": "unclassified",
            "plan_digest_unspecified": "unspecified",
            "plan_digest_unknown": "unknown",
            "plan_digest_file": "Full plan: `{path}` in the task work dir.",
            # D-172: printed only when the memo language or the interface language is not `en`.
            "memo_language_line": "Memo language: {name}",
            "plan_digest_issues_heading": "Legal issues to research ({count}):",
            "plan_digest_issue_line": "{issue_id} — {title}",
            "plan_digest_issue_line_where": "{issue_id} — {title} [{jurisdictions}]",
            "plan_digest_no_issues": "none recorded in `{path}`",
            "plan_digest_more_issues": "…and {count} more, listed in `{path}`",
            "plan_digest_layers": "Research layers: {layers} (doctrine {doctrine}).",
            "plan_digest_no_layers": "none",
            "plan_digest_doctrine_required": "required",
            "plan_digest_doctrine_not_required": "not required",
            "plan_digest_notes": "Planner notes: {notes}",
            # Gate 4 — the text channel of §2.4. The three reply lines are tokens only and read
            # the same in every pack.
            "plan_text_question": "{header}: {question}",
            "plan_text_options": "options: {labels}",
            "plan_text_reply_heading": "Reply with one of:",
            "plan_text_reply_approve": "`approve [style:<name>|standard] [sources:reduced]`",
            "plan_text_reply_edit": "`edit: <what to change>`",
            "plan_text_reply_cancel": "`cancel`",
            # D-242: what `mf gate parse` adds as `notice` when a plan-gate reply names `brief`.
            "brief_mode_removed": (
                "Brief mode no longer exists: every run is Full. After the memo is finished, "
                "/memoforge:brief makes a decision brief from it."
            ),
            # D-176a: the AUQ headers and option labels. `gates.canonical_map` builds the reverse
            # map from exactly these keys, so a localized answer comes back canonical.
            "header_plan": "Plan",
            "header_style": "Style",
            "header_sources": "Sources",
            "option_approve": "Approve",
            "option_edit": "Edit",
            "option_cancel": "Cancel",
            "option_continue": "Continue",
            "option_approve_description": "Start research on the plan as written.",
            "option_edit_description": "Tell me what to change; the plan is rebuilt.",
            "option_cancel_description": "Stop the task now.",
            "option_continue_description": "Run with reduced coverage.",
            "plan_question": "Approve this research plan?",
            "style_question": "Which writing style should the memo follow?",
            "style_option_profile_description": "Use the saved profile `{name}`.",
            "style_option_standard_description": "Use the built-in house style.",
            "sources_question": "Source coverage may be limited. {detail}",
            "sources_estimate": (
                "Estimated {total} legal-source calls against the daily quotas of {servers} "
                "({upper_bound} in total; a quota is an upper bound, not a remaining count)."
            ),
            "sources_missing_database": (
                "No legal database is connected for {layer} in {jurisdiction} ({servers})."
            ),
            "sources_missing_portal": (
                "No source answered today for {layer} in {jurisdiction} ({portals})."
            ),
        },
        # D-176: the user-facing lines `machine.py` writes around a gate. The chat lines of
        # `_chat` and the CLI error messages stay English (§10).
        "machine": {
            "plan_gate_dashboard": "The research plan is on your dashboard: {url}",
            "plan_gate_file": "File: {path} in the working folder {work_dir}",
            # The inline plural of D-176, kept as two English forms like `gate_pointer_one`/`_many`.
            "plan_gate_shape_one": "{count} legal issue · estimated complexity: {complexity}",
            "plan_gate_shape_many": "{count} legal issues · estimated complexity: {complexity}",
            "gate_pointer": "{label} is on the dashboard: {url}",
            "gate_pointer_one": "{label} ({count} question) are on the dashboard: {url}",
            "gate_pointer_many": "{label} ({count} questions) are on the dashboard: {url}",
            # D-177: the dashboard document. One key per literal, grouped by the module
            # constant or the function that prints it in lower snake case (plan 56 contract).
            "gate_hint": "waiting for your decision in the chat",
            "answer_hint_intake": "Reply in chat: 1A 2C 3: your text · proceed · cancel",
            "answer_hint_sufficiency_followup": (
                "Reply in chat: 1A 2C 3: your text · proceed · cancel"
            ),
            "answer_hint_insufficient": "Reply in chat: continue · cancel",
            "answer_hint_source_review": "Reply in chat: continue · cancel",
            "answer_hint_plan": (
                "Answer the question shown in chat "
                "(or reply in text: approve · edit: … · cancel)"
            ),
            "agent_fact_assumption_analyst": "Facts & assumptions analyst",
            "agent_legal_researcher": "Researcher",
            "agent_research_sufficiency_reviewer": "Coverage reviewer",
            "agent_currency_checker": "Currency checker",
            "agent_memo_writer": "Memo writer",
            "agent_logic_reviewer": "Logic reviewer",
            "agent_form_reviewer": "Form reviewer",
            "agent_citation_auditor": "Citation auditor",
            "agent_counterargument_reviewer": "Counter-argument reviewer",
            "agent_revision_mediator": "Revision mediator",
            "agent_client_readiness_reviewer": "Client-readiness reviewer",
            "agent_style_extractor": "Style extractor",
            "slot_statutes": "statutes",
            "slot_case_law": "case law",
            "slot_doctrine": "doctrine",
            "script_render_research_running": "Preparing the research for the writer",
            "script_render_research_finished": "Research prepared for the writer",
            "script_render_mediator_running": "Preparing the mediator's notes",
            "script_render_mediator_finished": "Mediator notes prepared",
            "script_sufficiency_route_running": "Checking research coverage",
            "script_sufficiency_route_finished": "Research coverage checked",
            "script_sources_preflight_running": "Checking which source portals answer today",
            "script_sources_preflight_finished": "Source portals checked",
            "script_sources_liveness_running": "Checking that every source link still works",
            "script_sources_liveness_finished": "Source links checked",
            "script_sources_verify_running": "Checking source identifiers",
            "script_sources_verify_finished": "Source identifiers checked",
            "script_sources_pack_running": "Building the source pack",
            "script_sources_pack_finished": "Source pack frozen",
            "script_draft_anchor_running": "Numbering the sections",
            "script_draft_anchor_finished": "Sections numbered",
            "script_draft_lint_running": "Style and structure check",
            "script_draft_lint_finished": "Style and structure checked",
            "script_draft_audit_citations_running": "Citation audit",
            "script_draft_audit_citations_finished": "Citations audited",
            "script_draft_finish_running": "Checking the draft",
            "script_draft_finish_finished": "Draft checked",
            "script_review_aggregate_running": "Aggregating the review verdicts",
            "script_review_aggregate_finished": "Review verdict aggregated",
            "script_revision_next_running": "Choosing what to revise next",
            "script_revision_next_finished": "Revision branch chosen",
            "script_docx_render_running": "Exporting the memo (DOCX)",
            "script_docx_render_finished": "Memo exported",
            "script_docx_validate_running": "Validating the exported file",
            "script_docx_validate_finished": "DOCX validated",
            "script_finalize_running": "Putting the deliverable together",
            "script_finalize_finished": "Deliverable ready",
            "inline_plan_json_running": "Drafting the research plan",
            "inline_plan_json_finished": "Plan drafted",
            "inline_mcp_probe_json_running": "Checking which legal databases are available",
            "inline_mcp_probe_json_finished": "Legal databases checked",
            "suffix_started": "started",
            "suffix_finished": "finished",
            "suffix_failed_retrying": "failed, retrying",
            "suffix_skipped": "skipped",
            "status_working": "Working",
            "status_your_turn": "Your turn",
            "status_cancelling": "Cancelling",
            "waiting_for_you": "Waiting for you: {label}",
            "skipped": "Skipped: {label}",
            "you_answered": "You answered: {label}",
            "phase_step": "{label} step",
            "more": "+{count} more",
            "review_no_blockers": "No blockers in this round.",
            "review_blockers_left": "Blockers were left for the next revision.",
            "review_incomplete": "A reviewer returned nothing usable, so the round is incomplete.",
            "intake_row": "{question} — assuming: {default}",
            "title": "memoforge · {task_id}",
            "description": "Live progress of a memoforge run",
        },
        # D-177: the 48 static strings of `lib/dashboard.html`, named after their
        # element id or function. The page reads them from `data.labels` with the
        # same bytes below as its English fallback.
        "dashboard": {
            "title": "memoforge run",
            "query_waiting": "Waiting for data…",
            "chat_waiting": "Waiting for data…",
            "tabs_label": "Run sections",
            "tab_overview": "Overview",
            "tab_intake": "Intake",
            "tab_plan": "Plan",
            "tab_sources": "Sources",
            "tab_reviews": "Reviews",
            "tab_memo": "Memo",
            "tab_brief": "Brief",
            "card_brief": "Decision brief",
            "label_brief_status": "Status",
            "label_brief_rounds": "Review rounds",
            "label_brief_checks": "Checks not passed",
            "card_your_turn": "Your turn",
            "card_running_now": "Running now",
            "card_timeline": "Timeline",
            "card_run_facts": "Run facts",
            "live_connecting": "connecting…",
            "label_mode": "Mode",
            "label_phase": "Phase",
            "label_steps": "Steps",
            "label_updated": "Updated",
            "card_notices": "Notices",
            "card_intake": "Intake",
            "card_assumed": "Assumed without asking",
            "card_plan": "Plan",
            "card_sources": "Sources",
            "card_reviews": "Review rounds",
            "card_memo": "Memo",
            "label_file": "File",
            "label_summary": "Summary",
            "label_published": "Published",
            "skip_note": "If you skip it: {default}",
            "plan_unclassified": "unclassified",
            "plan_complexity": "complexity {value}",
            "plan_layers": "layers: {value}",
            "plan_approved": "approved",
            "plan_awaiting": "awaiting your approval",
            "plan_decision_you_chose": "you chose {value}",
            "intake_answered": "You answered: {value}",
            "intake_not_answered": "Not answered, assumed: {value}",
            "intake_answered_count": "{answered} of {total} answered",
            "intake_assumed_mark": "assumed",
            "intake_confidence": "confidence {value}",
            "sources_frozen": "frozen {value}",
            "sources_critical": "{count} critical",
            "sources_supporting": "{count} supporting",
            "sources_background": "{count} background",
            "reviews_round": "round {value}",
            "reviews_draft": "draft v{value}",
            "reviews_blockers": "{count} blockers",
            "reviews_no_answer": "no answer from {value}",
            "query_fallback": "memoforge run",
            "live_unavailable": "live updates unavailable",
            "live_on": "live",
        },
        # D-176b: the nine parameter-free stand-ins of `fallbacks.DASHBOARD_LABELS` — the only
        # notice text §7.5 publishes for a banner whose rendered form carries diagnostics (an
        # absolute `work_dir`, an arbitrary error `reason`, per-server counts). The other banner
        # ids are constants and are read from `memo.banners` in the interface language instead.
        "banners": {
            "mcp_partial": "Partial MCP coverage; the gap is noted in the research files.",
            "mcp_soft_cap_exceeded": "An MCP server passed its per-run soft cap.",
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
        },
        # D-172: the endonym of every language, the name the «Memo language» line prints. The
        # same five values in every pack — a language is called what it calls itself.
        "language_names": {
            "en": "English",
            "de": "Deutsch",
            "fr": "Français",
            "es": "Español",
            "ru": "Русский",
        },
        # D-176 (sources/preflight): the gate-11 digest frame of `sources.render_digest`.
        # The exception rows stay raw machine tokens inside it — `[kind]`, `source_id`,
        # `tier`, currency status values, `do_not_use` — and the `drafting_warning`
        # rows keep the memo-language text (D-173b); only the frame is localized.
        "sources": {
            "digest_head": "Source review — {count} sources registered ({frozen}).",
            "digest_frozen": "frozen",
            "digest_not_frozen": "not frozen",
            "exceptions_heading": "Exceptions requiring your attention:",
            "no_exceptions": "No exceptions: every critical source is verified and current.",
            "reply_line": "Reply `continue` to draft on these sources, or `cancel` to stop.",
        },
        # D-176 (sources/preflight): the `Source access today:` block of
        # `preflight.source_access_block` and its one-line prompt facts. The block
        # head and the status labels are localized for the gates; the
        # `${source_access}` prompt value (`source_access_line`, `CLEAN_LINE`,
        # `UNKNOWN_LINE`) and `PREFLIGHT_ALTERNATIVES` stay English (§10).
        "preflight": {
            "status_ok": "answers",
            "status_waf_challenge": "WAF challenge",
            "status_cloudflare": "Cloudflare block",
            "status_interstitial": "200 with a challenge page, not the document",
            "status_dead": "did not answer",
            "status_tls": "TLS certificate not verified",
            "clean_line": "every routed portal answered today",
            "unknown_line": "not checked",
            "block_head": "Source access today:",
            "more_line": "…and {count} more in `{path}`",
        },
        # D-222: the chat lines of `/memoforge:brief` (plan 75A) — the status gate for a memo that
        # ended with open points, its text fallback with the yes/no words it recognises, the
        # outcome lines and one refusal per preflight code (`refused.<code>`).
        "brief": {
            "gate_question": (
                "The memorandum ended as «{status}» with {count} open point(s). Make the brief anyway?"
            ),
            "gate_header": "Brief",
            "option_yes": "Yes, with the open points marked",
            "option_no": "No",
            "gate_text": (
                "The memorandum ended as «{status}» with {count} open point(s). Make the brief anyway? "
                "Reply yes or no."
            ),
            "yes_words": ["yes", "y", "ok", "go"],
            "no_words": ["no", "n", "nope", "stop"],
            "done": "Brief ready: {path}",
            "done_unverified": "Brief ready, with open checks marked in it: {path}",
            "refused": {
                "no_memo": "There is no delivered memorandum to build a brief from.",
                "task_not_done": "The memo task has not finished yet; a brief can be made once it is done.",
                "draft_changed_after_export": (
                    "The memorandum draft changed after it was delivered, so a brief would not match "
                    "the delivered memo."
                ),
                "pack_changed_after_export": (
                    "The source pack changed after the memorandum was delivered, so a brief would not "
                    "match the delivered memo."
                ),
                "previous_run_locked": (
                    "The previous brief could not be moved aside ({error}); close it if it is open and "
                    "try again."
                ),
                "no_task": "There is no finished memo task to build a brief from.",
                "writer_failed": "The brief could not be written; the memorandum is unaffected.",
                # D-224: a promoted brief file no longer hashes to the sha recorded at promotion.
                "brief_changed_on_disk": (
                    "The brief files were changed by hand during the run; start again with /memoforge:brief."
                ),
            },
            "declined": "No brief made.",
            "stale_report": "That answer belongs to an older step; carrying on.",
            "copy_failed": "The brief is in {path}; copying it to the outputs folder failed: {error}",
            # D-224: the chat line of each brief dispatch — one phrase per agent step of the driver.
            "dispatch_line": "Brief: {step}, attempt {attempt}.",
            "steps": {
                "write": "writing",
                "lint_fix": "fixing the lint findings",
                "revise": "revising",
                "shorten": "shortening",
                "review": "checking fidelity and clarity",
                "status_gate": "your answer on the open points",
                "lint": "checking the form",
                "review_merge": "weighing the reviews",
                "render": "building the document",
                "publish": "copying it next to the memo",
            },
            # D-256: the dashboard page during a brief run, in the interface language.
            "dashboard": {
                "phase": "Decision brief",
                "attempt": "{step} (attempt {attempt})",
                "status": {"running": "brief in progress", "waiting": "waiting for your answer",
                           "clean": "brief ready", "unverified": "brief ready, checks open",
                           "declined": "no brief made", "refused": "brief refused"},
                "agents": {"writer": "Brief writer", "fidelity": "Fidelity reviewer", "form": "Clarity reviewer"},
            },
        },
    },
}
