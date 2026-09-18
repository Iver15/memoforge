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

Cite with `[[src:<source_id> <pinpoint>]]`. A quotation is optional: when the exact words of a provision or
a judgment matter, quote them as `> [[q:<quote_id>]] <text>` with the `quote_id` from
`${mf} quote extract --workdir ${work_dir} --source <source_id> --text "<fragment>"` (a `too_long` answer
lists shorter candidates — pick one or shorten the fragment). Never write a blockquote without `[[q:]]`;
at most one per subsection. Otherwise state the provision in your own words with the citation. Do not
write the Sources section.

The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in ${memo_language_name} saying what the client must check before relying on the memo.
Section headings follow these exactly:
${section_titles}
The risk line follows ${risk_line_example}
exactly, with one of ${risk_levels} as the verdict. Pinpoints in `[[src:<id> <pinpoint>]]` stay in
the English machine form (`art 6`, `para 3`) whatever the memo language. Quotations stay in the
language of the source, with a gloss in ${memo_language_name} where the reader needs one.

Last action (Bash, after the draft is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
