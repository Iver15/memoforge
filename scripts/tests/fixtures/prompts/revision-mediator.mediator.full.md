# Task parameters — revision-mediator (iteration 1)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`, `{AGENT_CORE}/style-profile.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot mediator --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under revision: `drafts/v1.md` (v`1`)
- reviewer outputs to consolidate: `reviews/v1-logic.json`
- aggregated issues, already deduplicated and marked `conflict`: `state.iterations[]` of this iteration (see `mf state get`)
- style profile (authoritative when set): `{PROSE_STYLE}`
- previous attempt errors to fix: none

Consolidate only. Priority is substance before form; inside substance, accumulate rather than
choose. Every instruction names one `section_id`. Anything you drop goes into `dropped[]` with a
reason. You do not decide iterations, verdicts or exits.

## Write

- `steps/s-042/a1/mediator/v1-mediator.json` (schema `mediator` — `{SCHEMAS}\mediator.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot mediator --state done`
