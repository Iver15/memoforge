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
Read the `raw_kind` of every `critical` source in the registry (a record without the field is
`agent_summary` when it has a `raw_path`, else `none`). A `critical` source whose text is
neither `full_text` nor `client_file` is a `missing` gap for its layer, and its remedy is
`mf sources save` over that record — name the source and the remedy in `why_blocking` — but only
when the record carries no `meta.save_outcome`, because then no save was ever tried. A record whose
`meta.save_outcome` is `refused:…` or `excerpt:…` was attempted already and is not sent back,
whatever its `raw_kind`: the memo's appendix tells the client what that text is. A record with
`raw_original_path` — a PDF kept as the server sent it — is never missing text, even with no text
layer: it is a source whose requisites and quotations were not checked by code, which is what the
appendix says of it.
The provisions that decide each issue's risk verdict — the offence, the sanction, the remedy, the
liability basis — must be present in the record as findings, not only as cases that mention them;
every provision the memo will have to state must be recorded by some layer — one that is not is a
`missing` gap, whatever the cases say about it.
Each right, duty or remedy a planned issue asks about needs a finding that carries the provision
establishing it; one without such a finding is a `missing` gap for `statutes`.
You may spot-check up to 5 `critical` case-law findings against the text saved for their source,
one lookup each and 5 lookups at most:
`{MF} quote locate --workdir {WORK_DIR} --source <source_id> --text "<quote_short>"`
The answer shows the passage around the words. A holding you locate only in a clause the act
recites — a contract term, a party's position, a lower court's view — and not in the court's own
reasoning is a gap for `case_law`.
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
