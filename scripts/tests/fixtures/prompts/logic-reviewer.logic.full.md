# Task parameters — logic-reviewer (iteration 1)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot logic --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under review: `drafts/v1.md` (v`1`)
- `draft_sha` to put in your output: `0000000000000000000000000000000000000000000000000000000000000000`
- checklist (grade every id, no additions, no omissions): `{CHECKLISTS}\logic.json`
- deterministic findings attached to this draft (empty: none attached): 
- previous attempt errors to fix: none

`approved` is a normal outcome and means zero blockers. Grade `unknown` only when the draft
does not let you decide; on a `hard_fail` item that costs the approval.

## Write

- `steps/s-042/a1/logic/v1-logic.json` (schema `review` — `{SCHEMAS}\review.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot logic --state done`
