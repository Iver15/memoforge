# Task parameters — legal-researcher (`statutes`)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/tooling-core.md`,
`{AGENT_CORE}/output-json.md`, `{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot statutes --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- layer: `statutes`
- layer rule (yours only): statutes — WebSearch as a primary tool: no (discovery only); citable: MCP documents; WebFetch of issuing-body portals
- issues: i1: How long may the client keep customer records?
- jurisdictions: EU
- available legal MCP namespaces: `unknown`
- tool order, preferred domains, LDH corpora and the note per jurisdiction: 
  - EU: legalviz_resolve > legalviz_get_law_part > ldh_resolve_reference > ldh_search > WebFetch (domains: publications.europa.eu, eur-lex.europa.eu)
    LDH sources: EU/EUR-Lex, EU/ConsolidatedLegislation
    legalviz_resolve (or legalviz_search_eu_law when only a title or keyword is known) turns the reference into a CELEX id, then legalviz_get_law_part with part=structure for the table of contents and one more call for the pinpointed article, version=<date> for a point-in-time reading; never pull a whole act, take get_law_part slices. legalviz_get_law_part with version="current" also reports the consolidated CELEX it read in versionCelex — for the AI Act that is 02024R1689-20260727, as amended by Regulation (EU) 2026/1744 (Digital Omnibus on AI, CELEX 32026R1744). If LegalViz is unavailable, ldh_resolve_reference with hint_country=EU and a minimal result_detail for metadata and status, then the article text via ldh_search with a pinpoint or a WebFetch of Cellar, https://publications.europa.eu/resource/celex/<CELEX>, sent with Accept: application/xhtml+xml and Accept-Language: eng — without that Accept header Cellar answers with 60 MB of RDF, and with text/html it answers 404. The pinpoint anchors id="art_N" exist only on the consolidated CELEX 0YYYYRNNNN-YYYYMMDD, not on the OJ form. eur-lex.europa.eu is the fallback behind Cellar: when it answers HTTP 202 with x-amzn-waf-action: challenge, retrying is useless — switch to Cellar or LegalViz.
- source access today (portals probed before this dispatch; use the alternative, do not retry): not checked
- follow-up prompts from an earlier pass: none
- your earlier findings for this layer: none - first pass of this layer
- research gaps to close in this pass (from the sufficiency review): none
- MCP calls already made this run: none yet — a server at its daily quota is not called; a free server past its soft cap is a sign to stop and write, not to keep searching.
- previous attempt errors to fix: none

A jurisdiction that appears in the tool order without a preferred domain is off-table: no route is
set for it. Find the official portal for that jurisdiction yourself, read the provision there, and
save it with `sources save --url` like any web source (below); a portal off the allowlist is the
`host_not_allowed` case.

After each MCP call, count it — on a host without the `PostToolUse` hook this is the only counter:
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot statutes --state step --mcp <ldh|legalviz|courtlistener|uklegal|justicelibre|opencaselaw|fedregs|lex|casus|fas> --detail <tool>`

## Save before you cite

Every `critical` and `supporting` source is saved or registered before it appears in a finding. Use the `source_id` the command returns; never invent one. `background` sources need no text. When two sources could share a short title, pass an explicit `--id <slug>` so the ids stay apart.

A web source is saved by code, not by you: `sources save` fetches the page, certifies that it is the document you named and that it is whole, and registers it.
`{MF} sources save --workdir {WORK_DIR} --layer statutes --title "<t>" --citation "<c>" --tier critical --url "<the public address of the document itself>"`
Add the requisites the code certifies, or the text stays an excerpt: `--expect-article <N>` for a statute article (`152` is not `152.1`); `--expect-number` for a judgment, and for a Russian court act `--expect-date` too. The number goes in as printed, suffix included (`305-ЭС24-8702 (1,3)`): never strip it — the code strips it for a portal's search by itself, and the suffix is what tells the twins of one chain apart. `--expect-date` is the date of the act itself, `YYYY-MM-DD`, never the date a portal's listing shows. A Russian court act can be found from its requisites instead of an address — `--resolve vsrf` or `--resolve sudact` in place of `--url`, both requisites required, as your routing note shows.
A document that needs headers takes the transport options of `sources fetch` — `--accept <mime>`, `--lang <code>`, `--method POST --json '<body>'` — so where a routing note names `sources fetch` for the document itself, `save` it with the same options. `sources fetch` stays for what you only read (an index, a manifest, a listing), and its file is never registered.

`save` answers with one JSON object — `save_outcome` when it saved, `errors` (its code first) when it refused; act on its answer:
- `full_text` — the code saved the whole document: cite it.
- `excerpt:<reason>` — the text is kept but not certified whole: cite it as an excerpt, and do not retry the same address expecting a different outcome (the same document found at another address, saved with the same `--citation`, may still raise it).
- `requisites_mismatch` — the requisites you gave match no act the portal lists (with `--url`: the page does not carry them): look at the number and the date again, and do not switch channels. When its `hint` says the resolver had certified the address, the portal served something else the second time — treat that as `channel_unavailable`.
- `requisites_ambiguous` — several distinct acts match, and `candidates` lists their addresses: choose one yourself and save it with `--url` and the same `--expect-*`.
- `channel_unavailable: <reason>` or `channel_budget_spent` — the channel did not work: go to the fallbacks (LDH `RU/Sudact`, a web search) and give the address you find to `save --url` with the same `--expect-*`.
- `channel_unavailable: captcha` — the same, and never try the channel again in this run, never try to get round the page, never ask the user to solve it: this rule has no exceptions, whichever command met the captcha. sudact.ru is then closed to `save --url` as well — do not pass an address on sudact.ru to it, not even one LDH or a web search gives you; register LDH's own answer with `--raw-kind excerpt` and cite it as an excerpt; a copy of the act on another allowed host is still saved with `save --url` and the same `--expect-*`.
- `host_not_allowed` — the page is outside the allowlist: the one case where your own copy of it is registered, with `--raw-kind agent_summary`.
- any other refusal (`dead`, `unchecked`, `interstitial`, `access_stub`, `truncated`, `unsupported_media_type`, …) — nothing was saved and its `hint` says why; take another address or route, since the same call gives the same answer.

`sources register --raw-file` is for exactly three cases, each with its `--raw-kind`:
- the whole answer of an MCP tool, in a file exactly as the tool returned it — `--raw-kind excerpt`;
- a file the client supplied — `--raw-kind client_file`;
- a page on a host outside the allowlist, after `save` refused it with `host_not_allowed` — your WebFetch copy, `--raw-kind agent_summary`.
`{MF} sources register --workdir {WORK_DIR} --layer statutes --title "<t>" --citation "<c>" --tier critical --tool "<the tool that returned the text>" --raw-kind excerpt --raw-file "<a real file you wrote — not a process substitution such as /dev/fd/N; HTML is converted to text for you>"`
A `register` that follows a refused `save` of the same source carries the refusal, so the sufficiency review does not ask for that save again: `"save_outcome": "refused:<code>"` in `--meta` — `refused:host_not_allowed`, `refused:captcha`; `register` refuses any other `save_outcome`, since `full_text` and `excerpt:*` are written by `save` alone. An excerpt or a summary is raised to the full text by running `save` over the same record — the same `--citation` or the same `--url` — once you find the document on an allowed page.

Add `--meta '{"court": …, "year": …, "issuing_body": …, "date": …, "short_name": …}'` to either command with the keys the tool's answer gives you — the OSCOLA footnotes are built from them — and drop the ones it does not.
An address an MCP tool returns is an endpoint, never a `--url` — for `save` or for `register`: what a legal-database tool answers with is not a page the client can open. Pass the public page when you found one, else register the MCP answer with no `--url` at all.

For an EU act the CELEX id is the key: `legalviz_search_eu_law` or `legalviz_resolve` to get it, `legalviz_get_law_part` with `part="structure"` for the table of contents, then one more call for the article you need — register that slice as the MCP answer it is (`--raw-kind excerpt`) with `--tool legalviz_get_law_part`, or the tool that actually returned the text (`legalviz_get_case_law`, `ldh_search`).

For a CJEU judgment the `--url` you save is its CELEX text, `6<year>CJ<number>` — so C-252/21 is
`62021CJ0252`, served both at `https://publications.europa.eu/resource/celex/62021CJ0252` (saved
with `--accept application/xhtml+xml`) and at
`https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62021CJ0252`. A `curia.europa.eu` URL is
not saved, not registered and not cited: every path there answers with the same JavaScript shell
for every case number.

WP29 guidance endorsed by the EDPB is cited at its official PDF on the Commission newsroom, e.g.
WP248 rev.01 (DPIA guidelines) at
`https://ec.europa.eu/newsroom/article29/redirection/document/47711` — a real PDF even though the
server labels it `Content-Type: application/`, and `save` keeps it as one: save that address like
any web source. Commercial re-uploads of the same file are not the citation.

To read one provision back out of a raw file you already saved:
`{MF} sources slice --workdir {WORK_DIR} --source <source_id> --article <N>`

## Write

- `steps/s-042/a1/statutes/statutes.json` (schema `research-findings` — `{SCHEMAS}\research-findings.schema.json`)

`_meta` is `{"task_id": "memo-20260908T120000Z-prompt-golden", "step_id": "s-042", "attempt": 1, "slot": "statutes"}`.

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot statutes --state done`
