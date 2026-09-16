# Task parameters — memo-writer (draft)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`, `{AGENT_CORE}/style-profile.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot writer --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- mode: `brief` · template `executive-brief`: `{TEMPLATES}\executive-brief.md`
- prose style (authoritative): `{PROSE_STYLE}`
- draft version to produce: v`1`
- write the draft to: `steps/s-042/a1/writer/v1.md`
- seed copy already in place (edit it, do not rewrite from scratch): none - this is the first version
- inputs: `plan.json`, `intake/`, `research/*.md`, `research/source-pack.json`, `research/currency.json`
- instructions to apply (only these positions): none - this is the first version
- previous attempt errors to fix: none
- warnings that must reach the memo, `- [code] (issue_id or general) text`, research gaps first:

- (none)

A warning addressed to you is an instruction: execute it, do not quote it. Each warning that
states a fact for the client goes into Key assumptions as one sentence, in your own words.

Cite with `[[src:<source_id> <pinpoint>]]`. A blockquote is `> [[q:<quote_id>]] <text>` and the
`quote_id` comes from `{MF} quote extract --workdir {WORK_DIR} --source <source_id> --text "<fragment>"`.
When extraction fails for a (section, source) pair, record it once with
`{MF} quote skip --workdir {WORK_DIR} --section <section_id> --source <source_id> --reason <reason>`
and paraphrase the provision with `[[src:]]` instead. Do not write the Sources section.

Last action (Bash, after the draft is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot writer --state done`
