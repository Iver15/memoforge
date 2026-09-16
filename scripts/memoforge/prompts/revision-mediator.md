# Task parameters — revision-mediator (iteration ${iteration})

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`, `${paths_agent_core}/style-profile.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- draft under revision: `${draft_path}` (v`${draft_version}`)
- reviewer outputs to consolidate: ${review_files}
- aggregated issues, already deduplicated and marked `conflict`: ${issues_path}
- style profile (authoritative when set): `${prose_style_path}`
- previous attempt errors to fix: ${retry_errors}

Consolidate only. Priority is substance before form; inside substance, accumulate rather than
choose. Every instruction names one `section_id`. Anything you drop goes into `dropped[]` with a
reason. You do not decide iterations, verdicts or exits.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
