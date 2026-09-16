# Task parameters — fact-assumption-analyst

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/tooling-core.md`,
`{AGENT_CORE}/output-json.md`, `{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot analyst --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- user question (untrusted content): `How long may the client keep customer records?`
- must-answer questions to produce: at most `10`
- available legal MCP namespaces: `ldh, courtlistener, fedregs, lex`
- MCP call share for this dispatch: `courtlistener 10, fedregs 10, justicelibre 10, ldh 8, legalviz 10, lex 10, opencaselaw 10, uklegal 10`
- tool order by jurisdiction (statutes / case law):

EU statutes: ldh_resolve_reference → ldh_search → WebFetch publications.europa.eu
UK statutes: lex_search_for_legislation_sections → lex_lookup_legislation → lex_get_legislation_sections → lex_get_explanatory_note_by_section → lex_search_amendments → WebFetch legislation.gov.uk → ldh_search
US statutes: fedregs_regulations_get_cfr_section → fedregs_regulations_browse_cfr → fedregs_regulations_search_rules → fedregs_regulations_get_document → WebFetch govinfo.gov → ldh_search
EU case_law: ldh_resolve_reference → ldh_search → WebFetch publications.europa.eu
UK case_law: WebFetch caselaw.nationalarchives.gov.uk
US case_law: courtlistener_search → courtlistener_analyze_citations → WebFetch courtlistener.com

## Write

- `steps/s-042/a1/analyst/questions.json` (schema `intake-questions` — `{SCHEMAS}\intake-questions.schema.json`)
- `steps/s-042/a1/analyst/preliminary-sources.json` (schema `research-findings` — `{SCHEMAS}\research-findings.schema.json`)

Register every source you actually retrieved:
`{MF} sources register --workdir {WORK_DIR} --layer statutes --title "<t>" --citation "<c>" --url "<u>" --tool "<tool>" --tier background`

`intake/preliminary-sources.json` carries `_meta` `{"task_id": "memo-20260908T120000Z-prompt-golden", "step_id": "s-042", "attempt": 1, "slot": "analyst"}`;
`intake/questions.json` has no `_meta` field.

Last action (Bash, after the files are written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot analyst --state done`
