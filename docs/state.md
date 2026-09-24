# `state.json` (v2)

Normative source: `schemas/state.schema.json` (draft 2020-12). This page is the short reading guide;
on any disagreement the schema wins. Every write goes through `memoforge.state_io.write_state()`
(lock + atomic replace + schema validation), so nothing outside the CLI may edit the file — the
orchestrator and the skills only read it. Phase names: `docs/phases.md`. Mode matrix: `docs/modes.md`.

Paths inside the state are POSIX strings relative to `work_dir`, except `work_dir` and
`output_folder`, which are absolute and platform-native.

## Identity and inputs

| Field | Meaning |
|---|---|
| `schema_version` | Always `2`. `mf task resolve` answers `unsupported` for anything lower. |
| `task_id`, `created_at`, `user_query`, `language`, `ui_language` | Task identity and the question verbatim; `language` is the memo language (`en` \| `de` \| `fr` \| `es` \| `ru`), `ui_language` the interface language (same enum, optional — absence means `en`). |
| `work_dir`, `output_folder` | Absolute run directory and the folder it was resolved into (ТЗ §2.5 chain). |
| `mode` | Always `full`: `mf task new` writes it, and plan approval repeats it in the `mode_selected` event (D-242). |
| `config` | Effective run configuration: `python_cmd`, `plugin_data_dir`, `reviewer_list`, `researcher_layers`, `max_iterations`, `lint_fix_rounds`, `intake_max_questions`, `client_polish_enabled`, `max_client_polish`, `template_id`, `template_path`, `citation_style` (D-150: footnotes, inline or null; null leaves the choice to the template's own front matter, which is inline in both templates), `writer_model`, `publish_folder`, `source_review_gate`, `dashboard`, `mcp_budget`, and the resolved style profile: `style_profile`, `style_profile_path`, `prose_style_path`. |

The two hook options of `userConfig` — `stop_guard` and `websearch_autoallow` — are **not** copied
into `config`: the hooks read them from `CLAUDE_PLUGIN_OPTION_*` in the environment and nothing in
the pipeline reads them from state (D-74). `dashboard` is the exception (D-87): `mf next` reads it
to decide whether its answer carries the §7.5 `dashboard` block, so it is resolved once at
`task new` and kept in `config`. Its default is `true` (D-92), and `task new` resolves it — like
every `userConfig` option — as flag > `CLAUDE_PLUGIN_OPTION_*` > `<plugin_data_dir>/options.json` >
default (D-91).

## Position in the pipeline

| Field | Meaning |
|---|---|
| `current_phase` | One of the 18 phases; `done` / `failed` / `cancelled_by_user` are terminal. |
| `steps[]` | Every protocol step: `step_id`, `kind` (`dispatch` \| `script` \| `inline-llm` \| `gate-text` \| `gate-auq` \| `terminal`), `phase`, `attempt`, `reason`, `generation` (gates), `agents[]` (slot, agent_type, status), `inputs`, `inputs_sha` (script steps: sha of the files a `reason: rerun` re-issue is judged against, D-58), `command`, `expected_outputs[]`, `status`, `closed_at`, `result_ref`, `superseded`. Identity is the pair `(step_id, attempt)`. |
| `published[]` | Canonical artifacts: `canonical_path`, `sha256`, `by`, `step_id`, `at`. Only `machine.publish` appends here — a file not listed is not authoritative. |
| `progress` | `phase`, `phase_started_at`, `route`, `position`, `total`, `active`, `last_line` (the last `chat_line`), `artifact_url`, `published_to` (the folder `mf finalize` copied the result into, D-109), `published_memo` (the root-level `memo-<slug>.<ext>` copy beside it, D-167), `mcp_calls`. Recomputed on each `next`. |
| `attempts` | Budget counters (ТЗ §2.2): `plan_edit`, `sufficiency_user_followup` (D-116), `sufficiency_research_followup` (D-116), `research_dispatch_retry`, `currency_regate`, `client_polish`, `lint_fix`, `reviewer_json_retry`, `reviewer_rerun`, `targeted_fix` (D-165), `single_dispatch_retry`, `inline_llm_retry`, `gate_parse_errors` (per gate). |
| `cancel_requested` | Set by `mf task cancel`; `next` then issues no new work and routes to finalize (ТЗ §2.4 d). |

## Content of the run

| Field | Meaning |
|---|---|
| `intake`, `classification`, `plan_approval` | Intake answers, the classification block, and the plan-gate history (`iterations[]`, `mode_selected`). |
| `dispatched_researchers[]` | Research layers actually dispatched. |
| `sufficiency_followup` | The open research-sufficiency follow-up: `status`, `subset_u`, `subset_r`, `approved_layers` (the layers the router charged the research budget for — what phase 5 may re-dispatch), `questions`, `user_response`, `asked_at`, `answered_at`; `null` when none is open. |
| `mcp_exhausted` | Routing alias → the UTC date on which that MCP server reported its quota gone; for the rest of that day `mf next` drops its tools from the routing digest (D-122). |
| `sources_frozen` | `true` once `sources pack --freeze` wrote `research/source-pack.json` (D-03). |
| `current_iteration`, `iterations[]`, `targeted_fix` | Review-loop position and one `mf review aggregate` record per iteration: `iteration`, `draft_sha`, `reviewers`, `coverage`, `failed_reviewers`, `downgraded_reviewers`, `substance_blockers`, `form_blockers`, `deterministic_blockers`, `pass_ratio`, `conflict`, `stale_reports`, `issues`, `aggregated_at` (ТЗ §4.5 п.3, D-77). `targeted_fix` is `null` until branch 9 of §4.5 п.4 buys the one targeted citation pass of a run, then `{iteration, reviewers[]}` — the iteration that pass produced and the reviewers `mf review aggregate` expects for it (D-165). |
| `current_draft_path`, `current_draft_sha`, `draft_versions[]` | The active draft and every version with `lint_clean` / `citations_clean` / `checked_at`. |
| `client_readiness` | Result of the client-readiness reviewer. |
| `drafting_warnings[]`, `remaining_blocking_issues[]`, `fallback_banners[]` | Warnings carried into the deliverable; banners come from `fallbacks.py`. |
| `open_substance_majors[]` | Optional (D-210): the substantive majors of `logic`, `citations` and `counterarguments` still open on the version the review loop leaves on, one row per stored issue — `id` (`om-<n>`), `class`, `reviewer`, `section_id`, `category`, `issue_category`, `issue`, `issue_client`, `suggestion`, `from_iteration`, `origin` (`loop` or `recheck`), `status` (`open`, `resolved`, `unresolved`, `manual_review` or `left`). Written by `mf revision next` on every exit to client readiness and by the writer-failed exit; the readiness step (D-211) settles `status` from the reviewer's dispositions and the polish re-check, and adds the re-check's new majors as `origin: recheck` rows. `summary.md` lists the open ones under «Open reviewer findings». Absent in a state written before D-210. |
| `polish_check` | Optional (D-211): the scope check of the final polish against `reviews/v<N>-prepolish.md`, stored once at the first entry after the polish writer — `draft_sha` of the polished draft and `errors` (`section_out_of_scope: <id>`, `new_source_token: <id>: <ids>`, or `baseline_unavailable` / `draft_unavailable` when an input cannot be read as published — a failed check, also when the baseline is lost before the re-check). Any error puts the baseline back onto the polished canonical (or, with no baseline, pins the last version whose bytes the review loop reviewed) and leaves for `export`, reason `polish_out_of_scope`: an approved, accepted or client-ready status becomes `manual_review_required_on_v<N>`, any other label is kept. Absent when the run had no open majors or no polish. |
| `export_pin` | Optional (D-211): `{version, sha256}` of the version an out-of-scope polish leaves to export (its restored baseline, or the last version the loop reviewed); `export_draft_sha` delivers it whatever the check flags of `draft_versions[]` say, and `docx.select_draft` — also without a `--draft-sha`, the selection of `finalize` — matches its version and sha before any older version with the same bytes, so the exported bytes and the version the status names agree. Absent otherwise. |

## Outcome

| Field | Meaning |
|---|---|
| `final_status` | Set by `mf finalize` together with the terminal phase; `null` while the run is live. |
| `final_status_reasons[]` | Codes explaining the outcome (D-21, D-30), e.g. `unresolved_blockers`, `lint_not_converged`, `cli_error`, `step_loop`, `interrupted`. |
| `final_docx_path` | The deliverable relative to `work_dir`; `finalize` guarantees a deliverable and `summary.md` on every path (M9). |
| `delivered_draft_sha` | Optional (D-220): the sha256 of the draft bytes the deliverable was rendered from, written by `mf finalize`; `null` = the deliverable was not rendered from a draft (the universal fallback summary, or an existing export reused with no draft on disk); absent = the task was finalized before plan 75A. |

Read it with `mf state get --workdir W [--path <dotted.path>]`; validate with `mf state validate --workdir W`.
