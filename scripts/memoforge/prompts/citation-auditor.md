# Task parameters — citation-auditor (iteration ${iteration})

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- draft under review: `${draft_path}` (v`${draft_version}`)
- `draft_sha` to put in your output: `${draft_sha}`
- checklist (grade every id, no additions, no omissions): `${paths_checklist}`
- deterministic findings attached to this draft (empty: none attached): ${lint_attachment}
- frozen source pack: `${work_dir}/research/source-pack.json`
- deterministic citation audit already run for you (mechanical checks only — existence, verbatim text, freeze, pinpoints): `${work_dir}/citations.json`
- research findings, one file per layer — the record each claim is graded against: ${research_files}
- claim-to-authority pairs: ${claim_pairs}
- previous attempt errors to fix: ${retry_errors}

`approved` is a normal outcome and means zero blockers. Grade `unknown` only when the draft
does not let you decide; on a `hard_fail` item that costs the approval.

Every issue carries `issue_category`: `source_drift`, `source_pack_mismatch` or
`unsupported_claim`. Existence, verbatim accuracy and currency are already decided by
`mf draft audit-citations`; do not re-litigate them.

The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in ${memo_language_name} saying what the client must check before relying on the memo.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
