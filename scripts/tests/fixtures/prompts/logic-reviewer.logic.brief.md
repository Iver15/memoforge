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

A suggestion that changes the direction of a conclusion — "not required" to "required", "low" to
"medium", an obligation added or removed — names a source of the frozen source pack (its `source_id`,
as the draft's `[[src:]]` tokens give it) that supports the new conclusion. Without one, the suggestion
may only ask the writer to resolve a stated contradiction or to add the opposing argument, and the
direction stays the writer's call. Flagging a contradiction between the facts, the assumptions and
the draft's own conditions stays allowed.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.

## Write

- `steps/s-042/a1/logic/v1-logic.json` (schema `review` — `{SCHEMAS}\review.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot logic --state done`
