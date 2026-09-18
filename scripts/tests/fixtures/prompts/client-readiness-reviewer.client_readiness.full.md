# Task parameters — client-readiness-reviewer

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`, `{AGENT_CORE}/style-profile.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot client_readiness --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under review: `drafts/v1.md` (v`1`)
- `draft_sha` to put in your output: `0000000000000000000000000000000000000000000000000000000000000000`
- checklist (grade every id, no additions, no omissions): `{CHECKLISTS}\client-readiness.json`
- style profile (authoritative when set): `{PROSE_STYLE}`
- polish rounds still available: `1`
- already known blockers: none
- previous attempt errors to fix: none

`verdict` is `client_ready`, `needs_final_polish` or `manual_review_required`. Every issue carries a
`section_id`: the list is handed to the writer verbatim as the polish instructions.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.

## Write

- `steps/s-042/a1/client_readiness/final-client-readiness.json` (schema `client-readiness` — `{SCHEMAS}\client-readiness.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot client_readiness --state done`
