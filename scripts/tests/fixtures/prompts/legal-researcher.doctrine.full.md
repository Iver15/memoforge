# Task parameters — legal-researcher (`doctrine`)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/tooling-core.md`,
`{AGENT_CORE}/output-json.md`, `{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot doctrine --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- layer: `doctrine`
- layer rule (yours only): doctrine — WebSearch as a primary tool: yes (primary discovery tool); citable: MCP documents; official regulatory guidance, peer-reviewed publications, SSRN-class repositories
- issues: i1: How long may the client keep customer records?
- jurisdictions: EU
- available legal MCP namespaces: `unknown`
- tool order and preferred domains: EU: WebSearch > ldh_search > WebFetch (domains: edpb.europa.eu, europa.eu)
- source access today (portals probed before this dispatch; use the alternative, do not retry): not checked
- follow-up prompts from an earlier pass: none
- your earlier findings for this layer: none - first pass of this layer
- research gaps to close in this pass (from the sufficiency review): none
- your share of the run MCP budget: `courtlistener 13, justicelibre 13, ldh 3, legalviz 13, opencaselaw 13, uklegal 13` calls
- previous attempt errors to fix: none

A jurisdiction that appears in the tool order without a preferred domain is off-table: no route is
set for it. Find the official portal for that jurisdiction yourself, read the provision there, and
register it with `--tool WebFetch <domain>`.

After each MCP call, count it — on a host without the `PostToolUse` hook this is the only counter:
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot doctrine --state step --mcp <ldh|legalviz|courtlistener|uklegal|justicelibre|opencaselaw> --detail <tool>`

## Register before you cite

Every `critical` and `supporting` source, before it appears in a finding:
`{MF} sources register --workdir {WORK_DIR} --layer doctrine --title "<t>" --citation "<c>" --url "<u>" --tool "<tool>" --tier <critical|supporting> --raw-file "<tmp file with the full tool text>"`
Add `--meta '{"court": …, "year": …, "issuing_body": …, "date": …, "short_name": …}'` with the keys the tool's answer gives you — the OSCOLA footnotes are built from them — and drop the ones it does not.
Use the `source_id` the command returns; never invent one. `background` sources need no raw file.

For an EU act the CELEX id is the key: `legalviz_search_eu_law` or `legalviz_resolve` to get it, `legalviz_get_law_part` with `part="structure"` for the table of contents, then one more call for the article you need — register that slice with `--tool legalviz_get_law_part` (or the tool that actually returned the text: `legalviz_get_case_law`, `ldh_search`, `WebFetch <domain>`).

For a CJEU judgment the registered `--url` is its CELEX text, `6<year>CJ<number>` — so C-252/21 is
`62021CJ0252`, served both at `https://publications.europa.eu/resource/celex/62021CJ0252` (fetched
with `Accept: application/xhtml+xml`) and at
`https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62021CJ0252`. A `curia.europa.eu` URL is
not registered and not cited: every path there answers with the same JavaScript shell for every
case number.

WP29 guidance endorsed by the EDPB is cited at its official PDF on the Commission newsroom, e.g.
WP248 rev.01 (DPIA guidelines) at
`https://ec.europa.eu/newsroom/article29/redirection/document/47711` — it is a real PDF even though
the server labels it `Content-Type: application/`. Commercial re-uploads of the same file are not
the citation.

A call that needs headers goes through `{MF} sources fetch --workdir {WORK_DIR} --url <U> [--accept <mime>] [--lang <code>] [--out <path under research/raw/>]`: WebFetch sends only a URL and curl is not auto-allowed, so the Cellar contract, the BOE block endpoint, the NL manifest and the RIS JSON are read with this command — it sends the plugin's headers, refuses hosts outside the allowlist, saves the body and answers `{status, code, content_type, bytes, sha256, path, interstitial}`; register that file with `--tool "mf-fetch <host>"`, and never cite a body whose `status` is `unchecked`.

To read one provision back out of a raw file you already saved:
`{MF} sources slice --workdir {WORK_DIR} --source <source_id> --article <N>`

## Write

- `steps/s-042/a1/doctrine/doctrine.json` (schema `research-findings` — `{SCHEMAS}\research-findings.schema.json`)

`_meta` is `{"task_id": "memo-20260908T120000Z-prompt-golden", "step_id": "s-042", "attempt": 1, "slot": "doctrine"}`.

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot doctrine --state done`
