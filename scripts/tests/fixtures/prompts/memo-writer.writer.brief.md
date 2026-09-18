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

Cite with `[[src:<source_id> <pinpoint>]]`. A quotation is optional: when the exact words of a provision or
a judgment matter, quote them as `> [[q:<quote_id>]] <text>` with the `quote_id` from
`{MF} quote extract --workdir {WORK_DIR} --source <source_id> --text "<fragment>"` (a `too_long` answer
lists shorter candidates — pick one or shorten the fragment). Never write a blockquote without `[[q:]]`;
at most one per subsection. Otherwise state the provision in your own words with the citation. Do not
write the Sources section.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.
Section headings follow these exactly:
executive_summary: `Executive summary`
background: `Background and definitions`
facts: `Facts, assumptions and limitations`
assumptions: `Key assumptions`
conclusion: `Conclusion and recommendations`
recommendations: `Recommendations`
The risk line follows Risk: medium.
exactly, with one of high, medium, low, undetermined as the verdict. Pinpoints in `[[src:<id> <pinpoint>]]` stay in
the English machine form (`art 6`, `para 3`) whatever the memo language. Quotations stay in the
language of the source, with a gloss in English where the reader needs one.

Last action (Bash, after the draft is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot writer --state done`
