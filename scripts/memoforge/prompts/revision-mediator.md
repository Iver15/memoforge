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
- frozen source pack, read-only (check that a source exists there; you do not review or add findings): `${source_pack_path}`
- style profile (authoritative when set): `${prose_style_path}`
- previous attempt errors to fix: ${retry_errors}

Consolidate only. Priority is substance before form; inside substance, accumulate rather than
choose. Every instruction names one `section_id`. Anything you drop goes into `dropped[]` with a
reason. You do not decide iterations, verdicts or exits.

An instruction never asks the writer to state what a provision or authority says unless that source
is in the frozen source pack (`${source_pack_path}`). If a reviewer's fix needs a norm that is not
there, the instruction is to remove or qualify the claim that relied on it, and `resolution` says
which source was missing.
Do not prescribe a pinpoint in a form the citation rules reject: a pinpoint is `art N`, `para N`,
`s N`, `reg N`, `p N`, `recital N`, `annex N` (with subdivisions), never a section heading or a
sentence.

The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in ${memo_language_name} saying what the client must check before relying on the memo.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
