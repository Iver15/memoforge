# Task parameters — logic-reviewer (iteration ${iteration})

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
- previous attempt errors to fix: ${retry_errors}

`approved` is a normal outcome and means zero blockers. Grade `unknown` only when the draft
does not let you decide; on a `hard_fail` item that costs the approval.

A suggestion that changes the direction of a conclusion — "not required" to "required", "low" to
"medium", an obligation added or removed — names a source of the frozen source pack (its `source_id`,
as the draft's `[[src:]]` tokens give it) that supports the new conclusion. Without one, the suggestion
may only ask the writer to resolve a stated contradiction or to add the opposing argument, and the
direction stays the writer's call. Flagging a contradiction between the facts, the assumptions and
the draft's own conditions stays allowed.

The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in ${memo_language_name} saying what the client must check before relying on the memo.
In this review a finding with `severity: major` carries `issue_client` too, on the same terms.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
