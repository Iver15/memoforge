# Task parameters — memo-writer (${writer_task})

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`, `${paths_agent_core}/style-profile.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- mode: `${mode}` · template `${template_id}`: `${paths_template}`
- prose style (authoritative): `${prose_style_path}`
- draft version to produce: v`${draft_version}`
- write the draft to: `${primary_output}`
- seed copy already in place (edit it, do not rewrite from scratch): ${seed_path}
- inputs: ${inputs_list}
- instructions to apply (only these positions): ${instructions_path}
- previous attempt errors to fix: ${retry_errors}
- warnings that must reach the memo, `- [code] (issue_id or general) text`, research gaps first:

${drafting_warnings}

A warning addressed to you is an instruction: execute it, do not quote it. Each warning that
states a fact for the client goes into Key assumptions as one sentence, in your own words.

Cite with `[[src:<source_id> <pinpoint>]]`. A blockquote is `> [[q:<quote_id>]] <text>` and the
`quote_id` comes from `${mf} quote extract --workdir ${work_dir} --source <source_id> --text "<fragment>"`.
When extraction fails for a (section, source) pair, record it once with
`${mf} quote skip --workdir ${work_dir} --section <section_id> --source <source_id> --reason <reason>`
and paraphrase the provision with `[[src:]]` instead. Do not write the Sources section.

Last action (Bash, after the draft is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
