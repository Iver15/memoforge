# memoforge

> **From one legal question to a finished `.docx` memorandum — researched, quoted, footnoted and stress-tested — in a single command.**

![version](https://img.shields.io/badge/version-2.0.0-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![built for](https://img.shields.io/badge/built%20for-Claude%20Code%20%2B%20Cowork-purple)

![Memoforge — from a legal question to a cited .docx memorandum](docs/media/memoforge-promo.webp)

<sub>~60-second walkthrough, recorded on v1.1.1: intake → research plan → parallel researchers → reviewer stress test → exported <code>.docx</code>. v2 is the same shape with fewer, better-checked steps.</sub>

---

## What it does

memoforge turns a research-grade legal question into a structured, footnoted memorandum. It runs the pipeline a small legal team would run: an analyst clarifies the missing facts, researchers pull primary sources in parallel (statutes, case law, regulator guidance), a currency check confirms the law is still good, a writer produces an IRAC draft, reviewers stress-test it against fixed checklists, a mediator consolidates the findings, and the writer revises before export. It assumes no jurisdiction — the plan step classifies the query and routes research accordingly. EU data protection (GDPR, AI Act, NIS2, DSA), US privacy and sectoral regulation, UK consumer law and cross-border compliance are its strong ground.

What changed in v2 is where the pipeline lives. Phases, budgets, source freezing, citation provenance and every `state.json` write belong to a Python package (`scripts/memoforge/`, the `mf` CLI) with ~3,600 offline tests behind it. The orchestrating model follows one three-command protocol — `next → act → report` — and holds no pipeline knowledge that context summarisation can eat.

## What you get

- **`memo-<slug>.docx`** — Arial 12pt, 1″ margins, numbered sections, IRAC per issue, in English, German, French, Spanish or Russian. Citations are OSCOLA-style: by default a short inline citation linked to the source, with the full record in a Sources annex; with `citation_style: footnotes`, *real* Word footnotes. Without `python-docx` the run still delivers a markdown memo with a banner explaining the downgrade: a run never ends empty-handed.
- **A frozen source pack** — every statute, case and regulator document the analysis relies on, each with a saved raw copy, a SHA-256 and a currency date. Once frozen, nothing new gets in.
- **Verifiable quotations** — every blockquote was extracted from that raw copy by exact string match, not recalled by a model. A quote that cannot be matched is refused, and the refusal is recorded.
- **Reviewer findings** — binary checklist verdicts per reviewer, plus what the mediator kept, dropped or flagged as a conflict.
- **An honest verdict** — `approved_on_v<N>`, `forced_exit_on_v<N>_with_remaining_issues` (revision budget spent with blockers left), `manual_review_required_on_v<N>`, or `fallback_summary_delivered` when a dependency failed, with reasons. No false confidence.
- **A full audit trail** — `state.json`, `events.jsonl`, every draft version and every lint report.

---

## Install

**Claude Code:** `/plugin marketplace add gregmos/memoforge` then `/plugin install memoforge`.
**Cowork:** Settings → Plugins → drag and drop `memoforge-2.0.0.zip` from the [Releases page](../../releases).

### Dependencies

The CLI, the hooks and the status line are stdlib-only, so the plugin installs and answers without anything extra. Schema validation, research and the docx renderer need three packages ([`requirements.txt`](requirements.txt): `jsonschema`, `python-docx`, `mistune`). Install them into the plugin's own data directory, where they never touch your project environment:

```
<plugin>/scripts/mf deps install      # Windows: scripts\mf.cmd deps install
<plugin>/scripts/mf deps check
```

`pip install -r requirements.txt` works too if you prefer the ambient interpreter. A `SessionStart` hook reports what is missing; without the packages the pipeline degrades to the markdown fallback instead of failing.

### Connect the legal databases

The plugin registers ten MCP servers through `.mcp.json`:

- `legal-data-hunter` — multi-jurisdictional statutes, case law and regulator guidance (230+ jurisdictions).
- `courtlistener` — US case law, plus the citation check that says whether a US citation exists at all.
- `legalviz` — [LegalViz.EU](https://legalviz.eu), a free reader for EU legislation: CELEX lookup, article-level slices of an act, the CJEU judgments interpreting a provision, and the amendments that say whether it is still current.
- `uk-legal` — [UK Legal MCP](https://github.com/paulieb89/uk-legal-mcp), free and keyless: legislation.gov.uk sections with their extent and in-force metadata, Find Case Law judgments down to the paragraph, and an OSCOLA citation resolver.
- `justicelibre` — [JusticeLibre](https://github.com/Dahliyaal/justicelibre), free and keyless: French code articles by abbreviation and number, the Cour de cassation, Conseil d'État and Conseil constitutionnel, CNIL deliberations, and CJEU/ECtHR judgments as a second text source. Légifrance itself is behind a Cloudflare challenge, so this is the only way into French law that does not need a PISTE account.
- `opencaselaw` — [OpenCaseLaw](https://github.com/jonashertner/opencaselaw), free and keyless, CC0 data: Swiss federal law by SR number and article (with the consolidations already scheduled), a million decisions back to 1875 with their official headnotes, and the commentary literature.
- `federal-regulations` — [federal-regulations-mcp-server](https://github.com/cyanheads/federal-regulations-mcp-server), free and keyless: the Code of Federal Regulations from eCFR, section by section and as of a date, and the Federal Register's proposed and final rules by number or search. The U.S. Code itself is read from govinfo.gov.
- `lex` — [Lex](https://github.com/i-dot-ai/lex) by i.AI (the UK government's AI incubator, with The National Archives and the Ministry of Justice), free and keyless: Acts and SIs by citation or search, the explanatory note of a section, and the amendments that changed it. An experimental service — the plugin uses it for explanatory notes and amendment history and keeps `uk-legal` first for statutes and judgments.
- `casus` — [CasusLegal](https://mcp.casus.legal/), a paid Russian connector: legal positions of the Constitutional, Supreme and former Supreme Arbitration Courts with hybrid search, a phrase index, full case texts and the specialised IP-court and administrative-chamber corpora. Sign in with your CasusLegal account (Claude Desktop: Settings → Connectors → custom connector `https://mcp.casus.legal/one/mcp`; Claude Code: `claude mcp add --transport http casus https://mcp.casus.legal/one/mcp`).
- `fas-search` — [FAS advertising practice](https://blog.delay-rag.ru/mcp-konniektor-k-poisku-po-praktikie-fas/), free and keyless: 8,000 decisions of the Russian Federal Antimonopoly Service and its regional offices under the Law on Advertising (20 calls a minute, 300 a day per IP, shared by cloud clients). Used for advertising, unfair-competition and antimonopoly questions only.

Click **Connect** on each in the plugin panel; the first call may open an OAuth sign-in (LegalViz, UK Legal, JusticeLibre, OpenCaseLaw and the FAS server need no key; CasusLegal needs a paid account). Skipping this is supported — research falls back to WebFetch against official portals, and the memo carries a banner asking you to verify each citation.

**More jurisdictions.** The bundled ten cover the EU, the UK, the US, France, Switzerland and Russia (higher-court practice through CasusLegal, FAS advertising practice, Legal Data Hunter's pravo.gov.ru and Sudact corpora, and the free article pages of consultant.ru and base.garant.ru). For the rest, `matematicsolutions` publishes an `*-eli-mcp` server for 33 jurisdictions (`de-eli-mcp`, `es-eli-mcp`, `nl-eli-mcp`, `ie-eli-mcp`, `at-eli-mcp`, …), and `ris-mcp-ts` wraps the Austrian RIS. All of them are local stdio servers, so you add them to your own MCP config rather than to the plugin's: the session probe then lists them under `namespaces.other` in `intake/mcp-probe.json` and the researcher is told they are available, but the routing table does not name their tools, so they act as an extra fail-soft source, not as a route. Be aware of what you are enabling: the whole `*-eli-mcp` family was batch-published on 24–27 August 2026, every repository is below version 1.0 with two stars or fewer, none has been verified by us by running it, and `it-eli-mcp` is not on PyPI at all despite its listing.

### Ask

```
/memoforge:memo "We're a US-based SaaS company launching a feature that uses AI to analyse customer
support chat transcripts from EU users and suggest replies to agents. The transcripts contain names,
email addresses and sometimes account details. Do we need a separate legal basis under GDPR, or does
this fall under our existing 'contract performance' basis? Does it trigger a DPIA or AI Act duties?"
```

Multi-part questions are fine: each part becomes its own analysed issue with its own citations. `/memoforge:continue` resumes an interrupted task or answers a pending question; `/memoforge:status` shows where a task stands.

---

## One mode

Every run is Full: there is no mode to pick. Source of truth: `scripts/memoforge/modes.py`, rendered into [`docs/modes.md`](docs/modes.md).

| | **Full** |
|---|---|
| Research layers | statutes and case law; doctrine when the plan requires it |
| Reviewers | logic, form, citations, counterarguments |
| Revision iterations | 2 |
| Client-readiness polish | one pass |
| Template | classical memo |
| Source-review gate | on exceptions only |
| MCP quotas tracked | LDH 10/day, CourtListener 125/day; free servers soft-capped at 100 calls per run (telemetry only) |

For a short document for the person who decides, run `/memoforge:brief` once the memo is finished — see [Decision brief](#decision-brief).

## Where it stops to ask you

Everything between these pauses runs on its own.

1. **Intake** — up to ten must-answer questions about facts the analyst could not infer. Answer `1A 2C 3: we only process EU users`, or `proceed` to accept the stated defaults, or `cancel`.
2. **Plan** — one card carrying the research plan (jurisdictions, issues, source types), your style profile if you have one, and a reduced-coverage question if the estimate will not fit the MCP quotas. Approve, edit or cancel; if the card cannot render, the same gate arrives as text.
3. **Source review — conditional.** It fires only on exceptions: a critical source left unresolved, conflicting authority, an exhausted MCP quota. Clean research goes straight to drafting. The `source_review_gate` setting forces it `on` or `off`.

Two more gates appear only when research came back thin: a targeted follow-up question, and a continue-or-cancel when coverage is too weak to draft from.

## What progress looks like

`dashboard` is **on by default**: the run publishes one live page through the host's `Artifact` tool, updated once per step; the link is printed in the chat right after it is published, and the page then re-renders itself as the run moves. Turn it off (`mf config set dashboard false`, or the plugin settings) to save one tool call per step; a host without an `Artifact` tool simply continues without the page. Cowork's Live artifacts were shut off on 2026-08-19, taking the v1 progress stack with them (the HTML renderer, the artifact updates and the widget MCP server), and v2 does not rebuild that. Either way the run leans on signals the runtime already shows:

- **Subagent tiles.** Every dispatch is labelled `P<n>/<N> · <agent> · <label>`, e.g. `P5/13 · legal-researcher · case law, CJEU`. The denominator is the number of pipeline phases reachable in your configuration — not a step count — so the tiles alone say how far along the run is.
- **One line per step** in the chat, printed between steps. In Cowork these buffer until the turn ends and read as a segment summary; a gate always flushes them.
- **`/tasks`** lists running subagents with those same labels, and a plugin `subagentStatusLine` adds elapsed time per task.
- **`mf events analyze`** reads `events.jsonl` afterwards: timeline, per-agent durations, whether reviewers really ran in parallel, and any gap over five minutes where the run went dark. Hooks write that journal; the CLI writes a guaranteed record of every step it issued.

Full `**Progress —**` blocks are printed at gates and at the end, where you are reading anyway. A later `/memoforge:brief` moves the same page: it shows the brief's own steps, and a «Brief» tab carries its status, review rounds and file.

## Options

Ten settings, all optional: `output_folder`, `publish_folder` (where the finished result is copied; empty means the host's outputs area if it has one), `writer_model` (`opus` | `fable` | `sonnet`), `source_review_gate` (`auto` | `on` | `off`), `citation_style` (`inline` | `footnotes`; `inline` is the default — short parenthetical citations linked to the source, with the full record of each one in the Sources annex), `memo_language` (`auto` | `en` | `de` | `fr` | `es` | `ru`; `en` is the default), `ui_language` (same values; `auto` is the default — the language you write in), `dashboard` (on), `stop_guard` (off), `websearch_autoallow` (on).

`writer_model` picks the model of the writer only (the memo writer and the brief writer). Every agent that judges substance — the researchers, the currency checker, the reviewers, the mediator — runs on Opus whatever you set.

**Memo language.** The memorandum can be written in English, German, French, Spanish or Russian. Set a default with the `memo_language` option (or `mf config set memo_language de`), or just say it in the request — “…memo in German”. The default is English whatever language you ask in; set `memo_language` to `auto` to get the memo in the language of your question — when that is one of the five, anything else stays English. The language is fixed once you approve the research plan; until then `mf task language --workdir <task folder> --memo fr` changes it. Citations keep their standard form; section headings, the risk line, the Sources annex, status notes and the run summary follow the memo language.

**Interface language.** The questions memoforge asks you, the plan card and its buttons, the live dashboard, the source-review digest and the assistant's replies follow `ui_language` — by default the language you write in, one of the same five; say “talk to me in German” to set it explicitly, or `mf task language --workdir <task folder> --ui de` on a running task. Reply tokens (`approve`, `edit:`, `proceed`), file paths and commands stay as they are. What is not localized: CLI error messages, run diagnostics and the reviewers' findings, which stay English.

Set them in the host's plugin settings when it offers a UI for them. When it does not — and a host that exports plugin options only to hook processes never reaches a `Bash`-launched `mf` with them — set them yourself, once:

```
mf config show                                  # effective value and where each one came from
mf config set dashboard false
mf config set output_folder ~/Documents/memoforge   # or the working folder your host shows you
mf config unset writer_model
```

`mf config` reads and writes `<plugin_data_dir>/options.json`, and the SessionStart hook mirrors every option the host did export into that same file at each session start, so a real host setting keeps winning. `mf task new` resolves each option as **explicit flag (`--option key=value`) → host setting (`CLAUDE_PLUGIN_OPTION_*`) → `options.json` → default**, and its answer carries `options_source` — the level each value actually came from. A value that still reads `${…}` is an unexpanded placeholder and is skipped.

## Permissions

A plugin cannot ship permission rules, so research prompts for approval unless you allow the hosts yourself. Paste the block below into `~/.claude/settings.json`; it is generated from the plugin allowlist by `mf docs render permissions`, and [`docs/permissions.md`](docs/permissions.md) is the canonical copy. Replace `${CLAUDE_PLUGIN_ROOT}` with your install path. `Agent(memoforge:*)` is deliberately absent: globs for `Agent` are not confirmed.

<details>
<summary><b>Permission block — 233 rules</b></summary>

```json
{"permissions": {"allow": [
  "WebFetch(domain:europa.eu)", "WebFetch(domain:*.europa.eu)", "WebFetch(domain:coe.int)", "WebFetch(domain:*.coe.int)",
  "WebFetch(domain:artificialintelligenceact.eu)", "WebFetch(domain:*.artificialintelligenceact.eu)", "WebFetch(domain:legalviz.eu)",
  "WebFetch(domain:*.legalviz.eu)", "WebFetch(domain:boe.es)", "WebFetch(domain:*.boe.es)", "WebFetch(domain:buzer.de)",
  "WebFetch(domain:*.buzer.de)", "WebFetch(domain:caselaw.nationalarchives.gov.uk)", "WebFetch(domain:*.caselaw.nationalarchives.gov.uk)",
  "WebFetch(domain:data.bka.gv.at)", "WebFetch(domain:*.data.bka.gv.at)", "WebFetch(domain:dejure.org)", "WebFetch(domain:*.dejure.org)",
  "WebFetch(domain:gesetze-im-internet.de)", "WebFetch(domain:*.gesetze-im-internet.de)", "WebFetch(domain:irishstatutebook.ie)",
  "WebFetch(domain:*.irishstatutebook.ie)", "WebFetch(domain:judiciary.uk)", "WebFetch(domain:*.judiciary.uk)",
  "WebFetch(domain:justice.gov.uk)", "WebFetch(domain:*.justice.gov.uk)", "WebFetch(domain:legifrance.gouv.fr)",
  "WebFetch(domain:*.legifrance.gouv.fr)",
  "WebFetch(domain:legislation.gov.uk)", "WebFetch(domain:*.legislation.gov.uk)", "WebFetch(domain:normattiva.it)",
  "WebFetch(domain:*.normattiva.it)", "WebFetch(domain:ris.bka.gv.at)", "WebFetch(domain:*.ris.bka.gv.at)", "WebFetch(domain:wetten.overheid.nl)",
  "WebFetch(domain:*.wetten.overheid.nl)", "WebFetch(domain:api.normattiva.it)", "WebFetch(domain:*.api.normattiva.it)",
  "WebFetch(domain:dati.normattiva.it)", "WebFetch(domain:*.dati.normattiva.it)", "WebFetch(domain:repository.officiele-overheidspublicaties.nl)",
  "WebFetch(domain:*.repository.officiele-overheidspublicaties.nl)", "WebFetch(domain:zoekservice.overheid.nl)",
  "WebFetch(domain:*.zoekservice.overheid.nl)", "WebFetch(domain:code.travail.gouv.fr)", "WebFetch(domain:*.code.travail.gouv.fr)",
  "WebFetch(domain:rechtsinformationen.bund.de)", "WebFetch(domain:*.rechtsinformationen.bund.de)", "WebFetch(domain:courdecassation.fr)",
  "WebFetch(domain:*.courdecassation.fr)", "WebFetch(domain:fedlex.admin.ch)", "WebFetch(domain:*.fedlex.admin.ch)", "WebFetch(domain:bger.ch)",
  "WebFetch(domain:*.bger.ch)", "WebFetch(domain:consultant.ru)", "WebFetch(domain:*.consultant.ru)", "WebFetch(domain:www.consultant.ru)",
  "WebFetch(domain:*.www.consultant.ru)", "WebFetch(domain:base.garant.ru)", "WebFetch(domain:*.base.garant.ru)", "WebFetch(domain:garant.ru)",
  "WebFetch(domain:*.garant.ru)", "WebFetch(domain:sudact.ru)", "WebFetch(domain:*.sudact.ru)", "WebFetch(domain:zakon.ru)",
  "WebFetch(domain:*.zakon.ru)", "WebFetch(domain:cyberleninka.ru)", "WebFetch(domain:*.cyberleninka.ru)", "WebFetch(domain:vsrf.ru)",
  "WebFetch(domain:*.vsrf.ru)", "WebFetch(domain:aepd.es)", "WebFetch(domain:*.aepd.es)", "WebFetch(domain:aki.ee)", "WebFetch(domain:*.aki.ee)",
  "WebFetch(domain:autoriteprotectiondonnees.be)", "WebFetch(domain:*.autoriteprotectiondonnees.be)",
  "WebFetch(domain:autoriteitpersoonsgegevens.nl)", "WebFetch(domain:*.autoriteitpersoonsgegevens.nl)", "WebFetch(domain:azop.hr)",
  "WebFetch(domain:*.azop.hr)", "WebFetch(domain:baylda.de)", "WebFetch(domain:*.baylda.de)", "WebFetch(domain:bfdi.bund.de)",
  "WebFetch(domain:*.bfdi.bund.de)", "WebFetch(domain:cnil.fr)", "WebFetch(domain:*.cnil.fr)", "WebFetch(domain:cnpd.public.lu)",
  "WebFetch(domain:*.cnpd.public.lu)", "WebFetch(domain:cnpd.pt)", "WebFetch(domain:*.cnpd.pt)", "WebFetch(domain:cpdp.bg)",
  "WebFetch(domain:*.cpdp.bg)", "WebFetch(domain:dataprotection.gov.cy)", "WebFetch(domain:*.dataprotection.gov.cy)",
  "WebFetch(domain:dataprotection.gov.sk)", "WebFetch(domain:*.dataprotection.gov.sk)", "WebFetch(domain:dataprotection.ro)",
  "WebFetch(domain:*.dataprotection.ro)", "WebFetch(domain:datatilsynet.dk)", "WebFetch(domain:*.datatilsynet.dk)",
  "WebFetch(domain:datatilsynet.no)", "WebFetch(domain:*.datatilsynet.no)", "WebFetch(domain:datenschutz-berlin.de)",
  "WebFetch(domain:*.datenschutz-berlin.de)", "WebFetch(domain:datenschutz.hessen.de)", "WebFetch(domain:*.datenschutz.hessen.de)",
  "WebFetch(domain:datenschutzkonferenz-online.de)", "WebFetch(domain:*.datenschutzkonferenz-online.de)", "WebFetch(domain:dpa.gr)",
  "WebFetch(domain:*.dpa.gr)", "WebFetch(domain:dpc.ie)", "WebFetch(domain:*.dpc.ie)", "WebFetch(domain:dsb.gv.at)", "WebFetch(domain:*.dsb.gv.at)",
  "WebFetch(domain:dvi.gov.lv)", "WebFetch(domain:*.dvi.gov.lv)", "WebFetch(domain:edoeb.admin.ch)", "WebFetch(domain:*.edoeb.admin.ch)",
  "WebFetch(domain:garanteprivacy.it)", "WebFetch(domain:*.garanteprivacy.it)", "WebFetch(domain:ico.org.uk)", "WebFetch(domain:*.ico.org.uk)",
  "WebFetch(domain:idpc.org.mt)", "WebFetch(domain:*.idpc.org.mt)", "WebFetch(domain:imy.se)", "WebFetch(domain:*.imy.se)",
  "WebFetch(domain:ip-rs.si)", "WebFetch(domain:*.ip-rs.si)", "WebFetch(domain:lda.bayern.de)", "WebFetch(domain:*.lda.bayern.de)",
  "WebFetch(domain:ldi.nrw.de)", "WebFetch(domain:*.ldi.nrw.de)", "WebFetch(domain:naih.hu)", "WebFetch(domain:*.naih.hu)",
  "WebFetch(domain:personuvernd.is)", "WebFetch(domain:*.personuvernd.is)", "WebFetch(domain:tietosuoja.fi)", "WebFetch(domain:*.tietosuoja.fi)",
  "WebFetch(domain:uodo.gov.pl)", "WebFetch(domain:*.uodo.gov.pl)", "WebFetch(domain:uoou.cz)", "WebFetch(domain:*.uoou.cz)",
  "WebFetch(domain:vdai.lrv.lt)", "WebFetch(domain:*.vdai.lrv.lt)", "WebFetch(domain:ada.gov)", "WebFetch(domain:*.ada.gov)",
  "WebFetch(domain:cisa.gov)", "WebFetch(domain:*.cisa.gov)", "WebFetch(domain:congress.gov)", "WebFetch(domain:*.congress.gov)",
  "WebFetch(domain:courtlistener.com)", "WebFetch(domain:*.courtlistener.com)", "WebFetch(domain:cppa.ca.gov)", "WebFetch(domain:*.cppa.ca.gov)",
  "WebFetch(domain:dol.gov)", "WebFetch(domain:*.dol.gov)", "WebFetch(domain:ecfr.gov)", "WebFetch(domain:*.ecfr.gov)",
  "WebFetch(domain:eeoc.gov)", "WebFetch(domain:*.eeoc.gov)", "WebFetch(domain:federalregister.gov)",
  "WebFetch(domain:*.federalregister.gov)", "WebFetch(domain:ftc.gov)",
  "WebFetch(domain:*.ftc.gov)", "WebFetch(domain:govinfo.gov)", "WebFetch(domain:*.govinfo.gov)", "WebFetch(domain:hhs.gov)",
  "WebFetch(domain:*.hhs.gov)", "WebFetch(domain:irs.gov)", "WebFetch(domain:*.irs.gov)", "WebFetch(domain:justice.gov)",
  "WebFetch(domain:*.justice.gov)", "WebFetch(domain:law.cornell.edu)", "WebFetch(domain:*.law.cornell.edu)", "WebFetch(domain:nist.gov)",
  "WebFetch(domain:*.nist.gov)", "WebFetch(domain:nlrb.gov)", "WebFetch(domain:*.nlrb.gov)", "WebFetch(domain:oag.ca.gov)",
  "WebFetch(domain:*.oag.ca.gov)", "WebFetch(domain:sec.gov)", "WebFetch(domain:*.sec.gov)", "WebFetch(domain:supremecourt.gov)",
  "WebFetch(domain:*.supremecourt.gov)", "WebFetch(domain:uscourts.gov)", "WebFetch(domain:*.uscourts.gov)", "WebFetch(domain:whitehouse.gov)",
  "WebFetch(domain:*.whitehouse.gov)", "WebFetch(domain:bis.org)", "WebFetch(domain:*.bis.org)", "WebFetch(domain:cen.eu)",
  "WebFetch(domain:*.cen.eu)", "WebFetch(domain:cenelec.eu)", "WebFetch(domain:*.cenelec.eu)", "WebFetch(domain:etsi.org)",
  "WebFetch(domain:*.etsi.org)", "WebFetch(domain:fatf-gafi.org)", "WebFetch(domain:*.fatf-gafi.org)", "WebFetch(domain:iec.ch)",
  "WebFetch(domain:*.iec.ch)", "WebFetch(domain:ietf.org)", "WebFetch(domain:*.ietf.org)", "WebFetch(domain:iso.org)", "WebFetch(domain:*.iso.org)",
  "WebFetch(domain:oecd.org)", "WebFetch(domain:*.oecd.org)", "WebFetch(domain:ohchr.org)", "WebFetch(domain:*.ohchr.org)",
  "WebFetch(domain:un.org)", "WebFetch(domain:*.un.org)", "WebFetch(domain:w3.org)", "WebFetch(domain:*.w3.org)", "WebFetch(domain:wipo.int)",
  "WebFetch(domain:*.wipo.int)", "WebFetch(domain:wto.org)", "WebFetch(domain:*.wto.org)", "WebFetch(domain:edri.org)",
  "WebFetch(domain:*.edri.org)", "WebFetch(domain:gdprhub.eu)", "WebFetch(domain:*.gdprhub.eu)", "WebFetch(domain:noyb.eu)",
  "WebFetch(domain:*.noyb.eu)", "mcp__plugin_memoforge_legal-data-hunter__*", "mcp__plugin_memoforge_courtlistener__*",
  "mcp__plugin_memoforge_legalviz__*", "mcp__plugin_memoforge_uk-legal__*", "mcp__plugin_memoforge_justicelibre__*",
  "mcp__plugin_memoforge_opencaselaw__*", "mcp__plugin_memoforge_federal-regulations__*",
  "mcp__plugin_memoforge_lex__*", "mcp__plugin_memoforge_casus__*", "mcp__plugin_memoforge_fas-search__*",
  "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/mf *)"
]}}
```

</details>

**In Cowork these rules do not apply.** There, Bash and web fetching arrive as `mcp__workspace__*` tools that the list above does not cover. The only protection in that environment is the plugin's `permission_gate` hook, which approves a fetch only for an allowlisted host and a Bash command only when it is a single, operator-free call to the plugin's own `mf`.

---

## Your own house style

By default the writer follows a built-in house style (concise, no em-dashes, OSCOLA citations). The Style Studio turns your own memos or written rules into a saved profile:

```
/memoforge:style new my-firm --examples ~/memos/2025-q4/
/memoforge:style list
```

Profiles live under the plugin data directory as plain markdown — open and edit them by hand. When profiles exist, the plan gate offers them as a choice; a profile sets the prose style and the template, never the run's scope. Form review then defers to your rules while the substantive checks (citations, IRAC, contrary authority) stay uniform. No profile means no extra prompts and default behaviour.

## Where the results land

A run produces two things: a **work dir** — one folder per question, holding the protocol files, the drafts and the raw sources — and a **published result**: the deliverable, `summary.md` and `sources/` (the source pack plus the raw text of every critical and supporting source). The work dir is the machine room and stays where it is; the published copy is the part meant for you. The published copy also carries a `_run/` folder with the small diagnostic files of the run — `state.json`, `events.jsonl`, `plan.json`, the intake facts, the sufficiency verdict and the reviews — so a finished result can be inspected without opening the work dir.

The work dir goes to the first writable of: the `output_folder` option (host setting or `mf config set output_folder <dir>`) → `$MEMOFORGE_OUTPUT_FOLDER` → `<session folder>/memoforge/` — the folder attached to the session (`$CLAUDE_PROJECT_DIR`, else the current directory), skipped when that is the plugin's own folder, your home directory itself or a drive root → `~/Documents/memoforge/` → `./outputs/memoforge-work/`. `mf task new` prints the absolute path it chose and the skill repeats it as one chat line.

The published copy goes to `<publish folder>/memoforge/<slug>/`, and where that is depends on the host:

- **Claude Code, project folder.** The work dir is already inside the folder you attached, so unless you set `publish_folder` nothing is copied — the deliverable is where you are working. The final message prints its absolute path.
- **Cowork.** The plugin runs in a container that cannot see your connected folder. The result is copied into the session's outputs area (`/mnt/user-data/outputs`), and the memo plus its summary are also written to the root of that area as `memo-<slug>.<docx|md>` / `memo-<slug>.summary.md`, which the skill presents in the chat through `present_files`; only when that tool is absent does the skill copy the folder into your connected folder through the session's own file tools. Both paths are printed when the run ends.
- **Anywhere else** (a hosted VM with neither): set `publish_folder` to a directory you can reach and the result lands there; with nothing set and no outputs area, nothing is copied and the work dir stays the single source.

Everything stays on your machine: no backend, no telemetry. MCP calls go to the providers you authenticated, with your credentials; the plugin never proxies or stores them.

## Decision brief

The memorandum is written for lawyers. For the person who has to decide, run `/memoforge:brief` once a task has finished (no argument = the newest finished task; `/memoforge:brief <task_id>` for another one). It condenses the delivered memo into a self-contained brief of about three pages, in the memo's language, keeping only the main points: a memo section stays when its risk is high, when it names an amount or a sanction, when it answers something you asked explicitly, or when it is undetermined or open and a kept conclusion depends on it. The brief has a bottom line; one block per kept conclusion with its risk level and the rule or court it rests on (linked inline, no quotations, no Sources list); what to do, where an action due within 14 days always stays; what the answer depends on; and, last, «Other points assessed» — one line per point it left out, with the memo's conclusion on it and its risk. A writer drafts it; a fidelity reviewer checks it against the memo (nothing added, no condition dropped, no risk level moved) and the form reviewer checks that a non-lawyer can read it, for up to two revision rounds. Only the fidelity review and the automatic checks decide whether the brief counts as verified.

What it never does: no new research, and it never changes the memo — the task's `state.json`, the memorandum and `summary.md` keep their bytes. A memo that ended with open points asks you first, and the brief then marks those points as not confirmed; a brief that did not pass every check carries a banner naming the open ones. The result is `brief/brief.docx` in the work dir (`.md` without `python-docx`), copied as `memo-<slug>.brief.<docx|md>` next to the memo's published copy and, when the memo run saved the memo into your connected folder, beside it there too. Running the command again makes a fresh brief and keeps the previous one in `brief/previous/`.

## Limits

- **Not a substitute for a lawyer.** The memo is a research-grade draft for a qualified reviewer.
- **Five memo languages** — English, German, French, Spanish, Russian. Reviewers' findings, CLI errors and run diagnostics stay English.
- **Provenance is bounded.** A quote is proven to come from the raw file the researcher saved; that the file came from the cited URL is confirmed only where the URL is still live and its hash matches. Each source records which of the two it is.
- **Currency is best-effort** against the connected databases. Verify litigation-sensitive citations yourself.
- **No cancelling mid-segment.** Cancellation is honoured at gates and at the next step boundary.
- **Cowork caveats:** buffered chat output, and permission rules that do not apply (above).

## Going deeper

[`docs/decisions.md`](docs/decisions.md) records why v2 looks like this, and [`docs/TZ-memoforge-v2.md`](docs/TZ-memoforge-v2.md) is the specification it was built from. Phases, modes, events, state fields and the always-deliver matrix are generated from the code into [`docs/`](docs/); the v1 contracts they replaced are kept in `docs/attic/`. Release history is in [`CHANGELOG.md`](CHANGELOG.md); `scripts/tests/README.md` explains how to run the suite.

## License and contact

MIT — see [`LICENSE`](LICENSE). Author: Grigorii Moskalev. Issues and production stories: [github.com/gregmos/memoforge/issues](https://github.com/gregmos/memoforge/issues).
