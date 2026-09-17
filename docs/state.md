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
| `task_id`, `created_at`, `user_query`, `language` | Task identity and the question verbatim; `language` is always `en` (ТЗ §0.3). |
| `work_dir`, `output_folder` | Absolute run directory and the folder it was resolved into (ТЗ §2.5 chain). |
| `mode` | `brief` \| `full` \| `null` until the plan gate fixes it. |
| `config` | Effective run configuration: `python_cmd`, `plugin_data_dir`, `reviewer_list`, `researcher_layers`, `max_iterations`, `lint_fix_rounds`, `intake_max_questions`, `client_polish_enabled`, `max_client_polish`, `template_id`, `template_path`, `citation_style` (D-150: footnotes, inline or null; null leaves the choice to the template's own front matter, which is inline in both templates), `writer_model`, `publish_folder`, `source_review_gate`, `dashboard`, `mcp_budget`, and the resolved style profile: `style_profile`, `style_profile_path`, `style_profile_mode_binding`, `prose_style_path`. |

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

## Outcome

| Field | Meaning |
|---|---|
| `final_status` | Set by `mf finalize` together with the terminal phase; `null` while the run is live. |
| `final_status_reasons[]` | Codes explaining the outcome (D-21, D-30), e.g. `unresolved_blockers`, `lint_not_converged`, `cli_error`, `step_loop`, `interrupted`. |
| `final_docx_path` | The deliverable relative to `work_dir`; `finalize` guarantees a deliverable and `summary.md` on every path (M9). |

Read it with `mf state get --workdir W [--path <dotted.path>]`; validate with `mf state validate --workdir W`.
