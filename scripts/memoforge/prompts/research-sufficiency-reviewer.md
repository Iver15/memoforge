# Task parameters — research-sufficiency-reviewer

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- mode: `${mode}`
- layers this mode researches: ${researcher_layers}
- plan: `${work_dir}/plan.json`
- research files to judge: ${research_files}
- source registry: `${work_dir}/research/sources.json`
- previous attempt errors to fix: ${retry_errors}
- warnings already carried by the run, `- [code] (issue_id or general) text`:

${drafting_warnings}

`overall_verdict` is exactly one of `sufficient`, `targeted_followup_needed`, `insufficient`.
A `critical` source with no saved raw text is a `missing` gap for its layer.
Judge sufficiency against the layers this mode researches: a gap in any other layer is one
sentence in `out_of_scope_gaps[]`, which the memo carries as a caveat, and not a `blocking_gaps`
entry — this mode runs no researcher for it.
`drafting_warnings[]` is addressed to the client and is printed in the memo: state the limitation
as the client should read it, with no instruction to the writer and no protocol file name
(`statutes.json` and the like). `blocking_gaps[].why_blocking` may stay technical — only the
pipeline reads it. Do not repeat a warning you already wrote as a gap: one of the two, not both.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
