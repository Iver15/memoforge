# Task parameters — memo-writer (draft)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`, `{AGENT_CORE}/style-profile.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot writer --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- mode: `full` · template `classical-memo`: `{TEMPLATES}\classical-memo.md`
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
states a fact for the client goes into the Assumptions block of the facts section as one sentence,
in your own words. A research-gap warning (`unresolved_research_gap`) is stated as a limitation of the
memo, in the facts section's limitations block, and next to the conclusion it touches.

A currency note naming a later change to the provision relied on is stated in that section in one
sentence: what changes and from when.

A summary bullet, a risk line or a recommendation whose conclusion depends on an assumption rather
than on a stated fact says so in the same sentence: "on the assumed facts", or its equivalent in
English.

A court is credited only with its own reasoning and its own conclusion. A party's submission, a lower
court's view, a dissenting or concurring opinion, an Advocate General's opinion, an editor's headnote,
or a clause the judgment recites is attributed to whoever said it («истец полагал …», «суд воспроизвёл
условие оферты …»). A conclusion you draw from the court's reasoning is written as yours, not the
court's: the court discussed agency, so write «суд рассуждал об агентировании», not «суд квалифицировал
договор как агентский, а не как хранение». A figure the court only checked arithmetically is not the
measure it adopted, and an outcome the saved finding does not show is written as unknown.

Money and required steps are conclusions. A loss and the cost of replacing the same item are one
computation. A figure called reliable excludes the parts the memo rates high-risk, or names them. A
step presented as required before another needs a cited rule; otherwise write it as a prudent step.

On `polish`, an instruction with `severity: blocker` is withdrawn or qualified, never re-argued: take
the statement out or qualify it as unresolved, together with every risk line and summary bullet that
rests on it, with no new norm and no new source.

On `polish`, a source the memo already cites may be cited again only in the section of the finding
you are polishing; the summary bullet, the conclusion item and the risk line you keep in step get no
new `[[src:]]` token.

On `polish`, a limitation you move into a section is stated as a limitation of the memo; it never
turns into an instruction to the client to delay a statutory step (a notification or a filing inside
its deadline).

Cite with `[[src:<source_id> <pinpoint>]]`. A quotation is optional: when the exact words of a provision or
a judgment matter, quote them as `> [[q:<quote_id>]] <text>` with the `quote_id` from
`{MF} quote extract --workdir {WORK_DIR} --source <source_id> --text "<fragment>"` (`--text` is at
most 60 words; a `too_long` answer returns `candidates` — take one of them or
shorten the fragment; never send the same text twice). Never write a blockquote without `[[q:]]`;
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
The three blocks of the classical facts section open with these bold labels, one on its own line:
facts: `Facts`
assumptions: `Assumptions`
limitations: `Limitations`
The risk line follows Risk: medium.
exactly, with one of high, medium, low, undetermined as the verdict. A pinpoint in `[[src:<id> <pinpoint>]]` of a
source numbered in Latin script (EU, UK, US and the like) stays in the English machine form
(`art 6`, `para 3`) whatever the memo language. A source written in another script is pinpointed
exactly as it numbers itself, in its own language — `п. 1 ст. 887`, not `art 887 para 1` — and a
contract or an offer by its own clause numbers and section headings, the heading left as the
source wrote it: `п. 3 разд. «Возмещение»`, not `sec Reimbursement para 3`.
The `Question:` line of the header block is the user's own question word for word, except a
leading or trailing instruction to you about the form of the work — the language, the format, the
jurisdiction to apply ("напиши меморандум на русском по праву РФ", "write a memo in English under
UK law"): that instruction is dropped, because the header already states the language and the
jurisdictions. Nothing else is cut, rephrased or shortened. Quotations stay in the
language of the source, with a gloss in English where the reader needs one.
Use the settled legal terminology of English and call each concept by one term
throughout — the term a section heading above uses is the term the body uses;
give the source-language original in parentheses at first use where the reader may need it; do not
calque English phrases word for word.

Last action (Bash, after the draft is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot writer --state done`
