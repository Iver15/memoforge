# Task parameters — research-sufficiency-reviewer

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot sufficiency --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- mode: `full`
- layers this mode researches: statutes, case_law, doctrine
- plan: `{WORK_DIR}/plan.json`
- research files to judge: `research/statutes.json`, `research/case_law.json`, `research/doctrine.json`
- source registry: `{WORK_DIR}/research/sources.json`
- previous attempt errors to fix: none
- warnings already carried by the run, `- [code] (issue_id or general) text`:

- (none)

`overall_verdict` is exactly one of `sufficient`, `targeted_followup_needed`, `insufficient`.
A `critical` source with no saved raw text is a `missing` gap for its layer.
The provisions that decide each issue's risk verdict — the offence, the sanction, the remedy, the
liability basis — must be present in the record as findings, not only as cases that mention them;
every provision the memo will have to state must be recorded by some layer — one that is not is a
`missing` gap, whatever the cases say about it.
Judge sufficiency against the layers this mode researches: a gap in any other layer is one
sentence in `out_of_scope_gaps[]`, which the memo carries as a caveat, and not a `blocking_gaps`
entry — this mode runs no researcher for it.
`drafting_warnings[]` is addressed to the client and is printed in the memo: state the limitation
as the client should read it, with no instruction to the writer and no protocol file name
(`statutes.json` and the like). `blocking_gaps[].why_blocking` may stay technical, but it is written
in English too — the run summary prints it after the gap. Do not repeat a warning you already wrote as a gap: one of the two, not both.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.
`drafting_warnings[]`, `blocking_gaps[].gap`, `why_blocking` and `out_of_scope_gaps[]` are written
in English — they are printed in the memo as written.

The follow-up gate prints your questions verbatim: write `blocking_gaps[].followup_question`
— `question`, `options[].label`, `options[].description` and `default_assumption_if_skipped` —
in English. The `header` stays English (at most 12 characters) — it is an
internal key, never shown at the gate.

## Write

- `steps/s-042/a1/sufficiency/research-sufficiency.json` (schema `research-sufficiency` — `{SCHEMAS}\research-sufficiency.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot sufficiency --state done`
