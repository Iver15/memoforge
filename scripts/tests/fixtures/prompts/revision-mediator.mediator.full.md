# Task parameters — revision-mediator (iteration 1)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`, `{AGENT_CORE}/style-profile.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot mediator --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under revision: `drafts/v1.md` (v`1`)
- reviewer outputs to consolidate: `reviews/v1-logic.json`
- aggregated issues, already deduplicated and marked `conflict`: `state.iterations[]` of this iteration (see `mf state get`)
- frozen source pack, read-only (check that a source exists there; you do not review or add findings): `{WORK_DIR}/research/source-pack.json`
- style profile (authoritative when set): `{PROSE_STYLE}`
- previous attempt errors to fix: none

Consolidate only. Priority is substance before form; inside substance, accumulate rather than
choose. Every instruction names one `section_id`. Anything you drop goes into `dropped[]` with a
reason. You do not decide iterations, verdicts or exits.

An instruction never asks the writer to state what a provision or authority says unless that source
is in the frozen source pack (`{WORK_DIR}/research/source-pack.json`). If a reviewer's fix needs a norm that is not
there, the instruction is to remove or qualify the claim that relied on it, and `resolution` says
which source was missing.

An instruction that changes the direction of a conclusion — "not required" to "required", "low" to
"medium", an obligation added or removed — names a source of the frozen source pack (its `source_id`)
that supports the new conclusion, as the reviewer's finding gave it. Without one, the instruction may
only ask the writer to resolve a stated contradiction or to add the opposing argument, and the
direction stays the writer's call. Flagging a contradiction between the facts, the assumptions and
the draft's own conditions stays allowed.

Do not prescribe a pinpoint in a form the citation rules reject: a pinpoint is `art N`, `para N`,
`s N`, `reg N`, `p N`, `recital N`, `annex N` (with subdivisions), never a section heading or a
sentence.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.

## Write

- `steps/s-042/a1/mediator/v1-mediator.json` (schema `mediator` — `{SCHEMAS}\mediator.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot mediator --state done`
