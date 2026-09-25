# Task parameters — currency-checker

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/tooling-core.md`,
`${paths_agent_core}/output-json.md`, `${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- source registry (the only place `source_id` comes from): `${work_dir}/research/sources.json`
- deterministic pre-checks already done for you: ${verify_report}
- sources to judge: ${sources_list}
- the questions and the facts with their dates: `plan.json`, `intake/user-facts.md`; judge "the part relied on" and "governs the facts" against them
- available legal MCP namespaces: `${mcp_namespaces}`
- MCP calls already made this run: ${mcp_spent} — a server at its daily quota is not called; a free server past its soft cap is a sign to stop and write, not to keep searching.
- previous attempt errors to fix: ${retry_errors}

After each MCP call, count it — on a host without the `PostToolUse` hook this is the only counter:
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state step --mcp <ldh|legalviz|courtlistener|uklegal|justicelibre|opencaselaw|fedregs|lex|casus|fas> --detail <tool>`

## The saved text first

Before any lookup, read the text saved for the source: the `raw_path` of its registry record, relative
to work_dir, when it has one. What that text says about itself comes before any tool:
- An official consolidated text that lists changes not yet applied to it — a "Changes to legislation"
  box on legislation.gov.uk that lists outstanding effects (not the standing line that there are none),
  a German consolidation noting an amendment "noch nicht abschließend bearbeitet", any portal's list of
  amendments not yet incorporated: name the amending instrument and resolve in one lookup what it changes
  and from when, in the part relied on. If that lookup fails, the status is `manual_check`, and the
  `note` names the amending instrument and says its effect is unresolved — never a bare "not
  reviewed" or "not re-fetched". This includes a Russian portal's announcement («вступает в силу с …»,
  «будет действовать с …»). Resolve whether the change reaches the part relied on for the facts: when
  it does not, the status stays `current` and the `note` names the amending act, the date and the
  change; `manual_check` only when that lookup fails or the change governs the facts. An act, or the
  part relied on, not yet in force on the memo date is `current` with a `note` "in force from <date>" —
  the writer states that date; it is never marked as governing earlier facts.
- A judgment: its opening paragraphs name the court, the appeal and the statute it was decided under.
  Read them before you search, put the statute it was decided under in the `note`, and judge the
  judgment's standing against that; a judgment is not left `unchecked` for a question its own first
  paragraphs answer.
- A page flagged "under review", "being updated" or the like (a regulator's guidance awaiting a new
  act, a consultation draft): record the flag in the `note`, whatever the status you give it.

Check the sources the conclusions rest on first — `critical`, then `supporting` — and mark a source
`unchecked` only after those.

For an EU act, start with `legalviz_get_law_relations` on its CELEX id — it lists amendments, corrigenda, repeals and implementing acts, which is the whole question — and confirm the text in force with `legalviz_get_law_part` at `version="current"`, whose answer names the consolidated CELEX in `versionCelex`. On 2026-09-13 the AI Act reads as `02024R1689-20260727`, amended by Regulation (EU) 2026/1744 (Digital Omnibus on AI, CELEX `32026R1744`). Go to the registry URL, LDH or EUR-Lex only for what those two leave open, and if EUR-Lex answers HTTP 202 with a challenge header, read the same CELEX from `https://publications.europa.eu/resource/celex/<CELEX>` with `Accept: application/xhtml+xml` instead of retrying.
For UK legislation, `uklegal_legislation_get_section` returns the section with its extent and in-force metadata, which settles the same question in one call.

`research/sources.json` is the declared input of this step, and the pipeline computed its
`liveness` and `verification` fields on every source before dispatching you: read those fields
instead of recomputing them. Do not run `${mf} sources verify`, `${mf} sources liveness` or
`${mf} sources register` during your step — rewriting your own declared input gets the step
rejected. Your only `mf` calls are `${mf} agent log` and `${mf} events log`, and `currency.json`
is the only file you write; a US citation verdict you established through CourtListener goes into
that source's `note`.
A source you could not check is `unchecked`, never `current`.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
