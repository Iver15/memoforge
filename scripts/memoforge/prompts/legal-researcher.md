# Task parameters — legal-researcher (`${layer}`)

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/tooling-core.md`,
`${paths_agent_core}/output-json.md`, `${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- layer: `${layer}`
- layer rule (yours only): ${layer_rules}
- issues: ${issues}
- jurisdictions: ${jurisdictions}
- available legal MCP namespaces: `${mcp_namespaces}`
- tool order, preferred domains, LDH corpora and the note per jurisdiction: ${routing}
- source access today (portals probed before this dispatch; use the alternative, do not retry): ${source_access}
- follow-up prompts from an earlier pass: ${followup_prompts}
- your earlier findings for this layer: ${previous_findings}
- research gaps to close in this pass (from the sufficiency review): ${followup_gaps}
- MCP calls already made this run: ${mcp_spent} — a server at its daily quota is not called; a free server past its soft cap is a sign to stop and write, not to keep searching.
- previous attempt errors to fix: ${retry_errors}

A jurisdiction that appears in the tool order without a preferred domain is off-table: no route is
set for it. Find the official portal for that jurisdiction yourself, read the provision there, and
register it with `--tool WebFetch <domain>`.

After each MCP call, count it — on a host without the `PostToolUse` hook this is the only counter:
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state step --mcp <ldh|legalviz|courtlistener|uklegal|justicelibre|opencaselaw|fedregs|lex|casus|fas> --detail <tool>`

## Register before you cite

Every `critical` and `supporting` source, before it appears in a finding:
`${mf} sources register --workdir ${work_dir} --layer ${layer} --title "<t>" --citation "<c>" --url "<u>" --tool "<tool>" --tier <critical|supporting> --raw-file "<a real file you wrote with the full tool text — not a process substitution such as /dev/fd/N; HTML is converted to text for you>"`
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

A call that needs headers goes through `${mf} sources fetch --workdir ${work_dir} --url <U> [--accept <mime>] [--lang <code>] [--out <path under research/raw/>]`: WebFetch sends only a URL and curl is not auto-allowed, so the Cellar contract, the BOE block endpoint, the NL manifest and the RIS JSON are read with this command — it sends the plugin's headers, refuses hosts outside the allowlist, saves the body and answers `{status, code, content_type, bytes, sha256, path, interstitial}`; an HTML page is saved as plain text (`.txt`); register that file with `--tool "mf-fetch <host>"`, and never cite a body whose `status` is `unchecked`.

To read one provision back out of a raw file you already saved:
`${mf} sources slice --workdir ${work_dir} --source <source_id> --article <N>`

## Write

${outputs}

`_meta` is `{"task_id": "${task_id}", "step_id": "${step_id}", "attempt": ${attempt}, "slot": "${slot}"}`.

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
