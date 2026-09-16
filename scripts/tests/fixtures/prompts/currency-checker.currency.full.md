# Task parameters — currency-checker

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/tooling-core.md`,
`{AGENT_CORE}/output-json.md`, `{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot currency --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- source registry (the only place `source_id` comes from): `{WORK_DIR}/research/sources.json`
- deterministic pre-checks already done for you: `research/sources.json` carries `liveness` and `verification` per source
- sources to judge: src-1, src-2
- available legal MCP namespaces: `ldh, courtlistener`
- your share of the run MCP budget: `courtlistener 40, justicelibre 40, ldh 10, legalviz 40, opencaselaw 40, uklegal 40` calls
- previous attempt errors to fix: none

After each MCP call, count it — on a host without the `PostToolUse` hook this is the only counter:
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot currency --state step --mcp <ldh|legalviz|courtlistener|uklegal|justicelibre|opencaselaw> --detail <tool>`

For an EU act, start with `legalviz_get_law_relations` on its CELEX id — it lists amendments, corrigenda, repeals and implementing acts, which is the whole question — and confirm the text in force with `legalviz_get_law_part` at `version="current"`, whose answer names the consolidated CELEX in `versionCelex`. On 2026-09-13 the AI Act reads as `02024R1689-20260727`, amended by Regulation (EU) 2026/1744 (Digital Omnibus on AI, CELEX `32026R1744`). Go to the registry URL, LDH or EUR-Lex only for what those two leave open, and if EUR-Lex answers HTTP 202 with a challenge header, read the same CELEX from `https://publications.europa.eu/resource/celex/<CELEX>` with `Accept: application/xhtml+xml` instead of retrying.
For UK legislation, `uklegal_legislation_get_section` returns the section with its extent and in-force metadata, which settles the same question in one call.

`research/sources.json` is the declared input of this step, and the pipeline computed its
`liveness` and `verification` fields on every source before dispatching you: read those fields
instead of recomputing them. Do not run `{MF} sources verify`, `{MF} sources liveness` or
`{MF} sources register` during your step — rewriting your own declared input gets the step
rejected. Your only `mf` calls are `{MF} agent log` and `{MF} events log`, and `currency.json`
is the only file you write; a US citation verdict you established through CourtListener goes into
that source's `note`.
A source you could not check is `unchecked`, never `current`.

## Write

- `steps/s-042/a1/currency/currency.json` (schema `currency` — `{SCHEMAS}\currency.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot currency --state done`
