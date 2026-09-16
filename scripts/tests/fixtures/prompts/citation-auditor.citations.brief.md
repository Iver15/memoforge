# Task parameters — citation-auditor (iteration 1)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot citations --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under review: `drafts/v1.md` (v`1`)
- `draft_sha` to put in your output: `0000000000000000000000000000000000000000000000000000000000000000`
- checklist (grade every id, no additions, no omissions): `{CHECKLISTS}\citations.json`
- deterministic findings attached to this draft (empty: none attached): 
- frozen source pack: `{WORK_DIR}/research/source-pack.json`
- deterministic citation audit already run for you (mechanical checks only — existence, verbatim text, freeze, pinpoints): `{WORK_DIR}/citations.json`
- research findings, one file per layer — the record each claim is graded against: `research/statutes.json`
- claim-to-authority pairs: pair every `[[src:<id>]]` claim of the draft with the finding in the research files whose `source_id` matches — its `proposition` and `pinpoint` are the record the draft must not go beyond
- previous attempt errors to fix: none

`approved` is a normal outcome and means zero blockers. Grade `unknown` only when the draft
does not let you decide; on a `hard_fail` item that costs the approval.

Every issue carries `issue_category`: `source_drift`, `source_pack_mismatch` or
`unsupported_claim`. Existence, verbatim accuracy and currency are already decided by
`mf draft audit-citations`; do not re-litigate them.

## Write

- `steps/s-042/a1/citations/v1-citations.json` (schema `review` — `{SCHEMAS}\review.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot citations --state done`
