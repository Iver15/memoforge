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
- tool order, preferred domains, LDH corpora and the note per jurisdiction: 
  - EU: WebSearch > ldh_search > WebFetch (domains: edpb.europa.eu, europa.eu)
    LDH sources: EU/EDPB, EU/GDPRhub
    EDPB via LDH with a mandatory WebFetch fallback to edpb.europa.eu.
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
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot doctrine --state step --mcp <ldh|legalviz|courtlistener|uklegal|justicelibre|opencaselaw|fedregs|lex|casus|fas> --detail <tool>`

## Save before you cite

Every `critical` and `supporting` source is saved or registered before it appears in a finding. Use the `source_id` the command returns; never invent one. `background` sources need no text. When two sources could share a short title, pass an explicit `--id <slug>` so the ids stay apart.

A review of an act is not the act. A portal's news item or «Обзор документа» about a judgment or a law is saved under its own title as background (`--tier supporting`), never under the act's citation, and never as the act's full text.

A web source is saved by code, not by you: `sources save` fetches the page, certifies that it is the document you named and that it is whole, and registers it.
`{MF} sources save --workdir {WORK_DIR} --layer doctrine --title "<t>" --citation "<c>" --tier critical --url "<the public address of the document itself>"`
Add the requisites the code certifies, or the text stays an excerpt: `--expect-article <N>` for a statute article (`152` is not `152.1`), and for a unit other than an article or a section its label with the number (`--expect-article "reg 22"`, `"Sch 1"`, `"Rule 23"`); `--expect-number` for a judgment, and for a Russian court act `--expect-date` too. Save a unit from its own page, not from a chapter or section page that carries several; a paragraph of a Schedule is expected as `"para N"`. The number goes in as printed, suffix included (`305-ЭС24-8702 (1,3)`): never strip it — the code strips it for a portal's search by itself, and the suffix is what tells the twins of one chain apart. `--expect-date` is the date of the act itself, `YYYY-MM-DD`, never the date a portal's listing shows. A Russian court act can be found from its requisites instead of an address — `--resolve vsrf` or `--resolve sudact` in place of `--url`, both requisites required, as your routing note shows.
A document that needs headers takes the transport options of `sources fetch` — `--accept <mime>`, `--lang <code>`, `--method POST --json '<body>'` — so where a routing note names `sources fetch` for the document itself, `save` it with the same options. A `--method POST` save also takes `--public-url "<the public page of the document the body asks for>"`: the endpoint answers every document at one address, so it names none and a client cannot open it. That page becomes the source's url — for a Normattiva article, `https://www.normattiva.it/uri-res/N2Ls?` followed by the URN of the body — and the save is refused without it. `sources fetch` stays for what you only read (an index, a manifest, a listing), and its file is never registered.

`save` answers with one JSON object — `save_outcome` when it saved, `errors` (its code first) when it refused; act on its answer:
- `full_text` — the code saved the whole document: cite it.
- `excerpt:<reason>` — the text is kept but not certified whole: cite it as an excerpt, and do not retry the same address expecting a different outcome (the same document found at another address, saved with the same `--citation`, may still raise it).
- `requisites_mismatch` — the requisites you gave match no act the portal lists (with `--url`: the page does not carry them): look at the number and the date again, and do not switch channels. When its `hint` says the resolver had certified the address, the portal served something else the second time — treat that as `channel_unavailable`.
- `requisites_ambiguous` — several distinct acts match, and `candidates` lists their addresses: choose one yourself and save it with `--url` and the same `--expect-*`.
- `channel_unavailable: <reason>` — the channel did not work: go to the fallbacks (LDH `RU/Sudact`, a web search) and give the address you find to `save --url` with the same `--expect-*`.
- `channel_budget_spent` — go to the fallbacks too, but the budget is the host's: every request to sudact.ru counts against it, `save --url` included, so do not pass an address on sudact.ru to it — register LDH's own answer with `--raw-kind excerpt` and cite it as an excerpt; a copy of the act on another allowed host is still saved with `save --url` and the same `--expect-*`.
- `channel_unavailable: captcha` — the same, and never try the channel again in this run, never try to get round the page, never ask the user to solve it: this rule has no exceptions, whichever command met the captcha. sudact.ru is then closed to `save --url` as well — do not pass an address on sudact.ru to it, not even one LDH or a web search gives you; register LDH's own answer with `--raw-kind excerpt` and cite it as an excerpt; a copy of the act on another allowed host is still saved with `save --url` and the same `--expect-*`.
- `host_not_allowed` — the page is outside the allowlist: the one case where your own copy of it is registered, with `--raw-kind agent_summary`.
- any other refusal (`dead`, `unchecked`, `interstitial`, `access_stub`, `truncated`, `unsupported_media_type`, …) — nothing was saved and its `hint` says why; take another address or route, since the same call gives the same answer.

`sources register --raw-file` is for exactly three cases, each with its `--raw-kind`:
- the whole answer of an MCP tool, in a file exactly as the tool returned it — `--raw-kind excerpt`;
- a file the client supplied — `--raw-kind client_file`;
- a page on a host outside the allowlist, after `save` refused it with `host_not_allowed` — your WebFetch copy, `--raw-kind agent_summary`.
`{MF} sources register --workdir {WORK_DIR} --layer doctrine --title "<t>" --citation "<c>" --tier critical --tool "<the tool that returned the text>" --raw-kind excerpt --raw-file "<a real file you wrote — not a process substitution such as /dev/fd/N; HTML is converted to text for you>"`
A `register` that follows a refused `save` of the same source carries the refusal, so the sufficiency review does not ask for that save again: `"save_outcome": "refused:<code>"` in `--meta` — `refused:host_not_allowed`, `refused:captcha`; `register` refuses any other `save_outcome`, since `full_text` and `excerpt:*` are written by `save` alone. An excerpt or a summary is raised to the full text by running `save` over the same record — the same `--citation` or the same `--url` — once you find the document on an allowed page.

Add `--meta '{"court": …, "year": …, "issuing_body": …, "date": …, "short_name": …}'` to either command — the OSCOLA footnotes are built from it. `court`, `year`, `issuing_body` and `date` are the keys the tool's answer gives you; drop the ones it does not. `short_name` is always there, and you set it (below).
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

## One form per act

Every record of one act names it the same way, in any legal system:
- `--citation` is "act, unit" — the act as it is cited, a comma, then the unit you rely on: `UK GDPR, art 82`; `Data Protection Act 2018, s 168`; `42 U.S.C., § 1983`; `BGB, § 823`; `Code du travail, art. L1234-5`; `Федеральный закон от 31.07.2025 № 289-ФЗ, ст. 23`. A judgment cited as a whole has no unit. A text that holds the whole act (a consolidated regulation) is registered under the act without a unit (`GDPR`), never under one article of it.
- `meta.short_name` is always set, by you, not taken from a tool: the act without its unit, the same for every record of that act — `UK GDPR`, `DPA 2018`, `42 U.S.C.`, `BGB`, `ГК РФ`, `289-ФЗ`, `Определение № 66-КГ18-9`.
- `--title` carries no database or retrieval tag in brackets — no `[legislation.gov.uk]`, `[LDH]` or `[via uklegal]`.
- A multi-page guide is saved from its content page, the page that carries the passage you rely on, not from its table of contents.

## Find a case by name

When a case is expected — an appeal from a decision you hold, the leading case an issue is known for — search for it by the parties' names. A failed lookup of a guessed citation (not found, or a match with confidence 0) does not mean the case is absent: search by name before you drop it or enter it in `considered_excluded`.

## One finding, one thought

- One finding is one self-contained, verifiable thought about one unit of the source, with the smallest coherent set of pinpoints. A condition or an exception stays in the finding of the rule it qualifies. Claims that stand on their own — different sub-sections, different holdings, different acts — are separate findings, each with its own `pinpoint` and its own `quote_short` taken from that unit. Example: DPA 2018 s 168(1) (distress counts as non-material damage) and s 168(2)–(3) (compensation in proceedings a representative body has already brought) are two findings; a condition is never split from its consequence.

## What a court held

- A finding that says what a court held or applied rests on the court's own statement. A clause, a party's position or a lower court's view that the act recites is described as such ("суд воспроизвёл условие оферты …", "истец полагал …"), and `quote_short` for a holding comes from the court's own words. A finding on a court act says whether the saved text reaches the outcome; when it breaks off before the operative part, the `proposition` says so («итог в сохранённом тексте не виден»). A figure the court found arithmetically correct is not the sum it awarded unless the court says so. Reasoning under one qualification (agency) is not a holding against another (storage) unless the court rejects it.
- When the saved text does not establish who is speaking — the court, a party, a lower court, a recited clause, a dissent or concurrence, an Advocate General, an editor — the words are not credited to the court. The finding says what the text states and that its authorship is not established («в тексте акта указано …; чья это позиция, из текста не видно» / "the judgment states …; whose position this is is not clear from the text"), and lowers `confidence`.
- A conclusion the text does not carry is not written as the court's.
- Findings about one source under different issues are read together before you finish, and they do not contradict each other.
- A higher-court act that a finding's own `proposition` names as the basis of the court's reasoning is saved with `sources save` — `--resolve vsrf` for a Supreme Court chamber act, `--url` for a review of practice or a Plenum act — or entered in `considered_excluded` with the reason. Acts the decision cites that no finding names are left alone.

## Check the quotes before you finish

Before the `--state done` line below, look up the `quote_short` of every `critical` finding in the text saved for its source:
`{MF} quote locate --workdir {WORK_DIR} --source <source_id> --text "<quote_short>"`
- `found` — the quote stands.
- `not_found` or `ambiguous` — replace the quote with an exact contiguous run the answer shows (a `candidates` sentence for `not_found`; for `ambiguous`, a run from the `passages` that occurs once), at most 15 words and, for a holding, the court's own words; or leave `quote_short` empty and lower `confidence`.

One lookup per `critical` finding, plus one retry of a quote you replaced.

## Write

- `steps/s-042/a1/doctrine/doctrine.json` (schema `research-findings` — `{SCHEMAS}\research-findings.schema.json`)

`_meta` is `{"task_id": "memo-20260908T120000Z-prompt-golden", "step_id": "s-042", "attempt": 1, "slot": "doctrine"}`.

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot doctrine --state done`
