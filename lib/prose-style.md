# House style

How memoforge memos read. Read by `memo-writer`, `form-reviewer`, `client-readiness-reviewer` and `revision-mediator`. When the task has a custom style profile, that profile is authoritative wherever it differs (see `lib/agent-core/style-profile.md`).

Mechanical rules are not repeated here. Sentence and paragraph caps, em-dash use, heading levels, the correspondence between summary bullets, subsections and conclusion items, risk-line format, blockquote and citation-token form, word caps, placeholders and the AI-tells dictionary are checked by `mf draft lint` and `mf draft audit-citations`. This file covers what a script cannot check.

## Audience and language

- The reader is in-house legal counsel, or the business stakeholder counsel is briefing.
- Company and registration jurisdiction: <set this before first run>.
- Primary jurisdictions in priority order: Cyprus, EU, US, Switzerland, Hong Kong.
- Memos are written in the memo language whatever language the query used. When the query is in another language, restate it in the memo language in the header block and continue in the memo language. Source quotations stay in the language of the source, with a gloss in the memo language beneath when the reader needs one. The memo language, the section headings and the risk-line literal are given in your task prompt; the English forms below are the example.

## Tone

- Conclusions are stated as results of the analysis, not as opinions. Direct statements outrank hedged ones.
- Hedge only where the law is genuinely uncertain — conflicting authority, an untested provision, a missing fact — and then name the uncertainty and what would resolve it. "It seems", "we believe", "could potentially" without such a reason weaken a memo that has an answer.
- No promotional or emotive vocabulary, no filler openers, no paragraph that announces what the next paragraph will say.
- Short declarative sentences, one idea each. Active voice where it is natural; passive is fine when the actor is irrelevant.
- Latin only where it carries meaning that the memo language would lose.

## How an analytical subsection is built

Every numbered analytical subsection carries four beats, in order.

1. **Issue and conclusion.** Open with the answer for this issue, then name the question and the controlling authority. The reader should not have to reach the end of the subsection to learn where it lands.
2. **The source.** State the controlling text with its locator and citation (`Article 5(1)(c) GDPR provides that …[[src:…]]`); quote it verbatim only where the exact words carry the point, as `> [[q:<quote_id>]] <text>` from `mf quote extract`.
3. **Rule explanation and application.** What the authority actually establishes, then what it means for the user's facts. Case law and commentary that modify the plain text are said to modify it. This beat also carries the counterargument: at least one sentence stating the strongest contrary reading or contrary authority, and one resolving it — why the conclusion still holds, and what would have to change for it not to. A subsection with no counterargument is incomplete regardless of how clear the answer looks.
4. **Risk line.** The verdict as a short sentence opening the last paragraph of the subsection, then the justification, then the recommendation — literally:
   `Risk: high. A regulator would treat the asymmetry as a defect of the consent itself. Legal must ship a one-click withdrawal control before launch.`
   The verdict word is one of `high`, `medium`, `low`, `undetermined`, lower case and followed by a period; `templates/<template_id>.md` shows where the line sits in each template.

The beats stay in order and are not interleaved. IRAC and CREAC are the writer's underlying logic, not visible labels: the reader sees the four beats, never a heading that reads `Rule` or `Application`.

## Recommendations

Every recommendation names three things: the action (a specific operational step, not "consider" or "ensure"), the trigger (before launch, within 30 days of X, if condition Y appears), and the owner (Legal, Privacy, the controller, the vendor). A recommendation missing any of the three leaves the reader with nothing to do.

Where more than one defensible path exists, present them as options with their trade-offs, and label any option that contradicts the risk verdict as the consequence of ignoring the recommendation rather than as a peer alternative.

## Structure

The per-template section list lives in `templates/<template_id>.md`; follow the template named in the dispatch. Across templates:

- The title states the subject and what the memo evaluates.
- Context paragraphs say who is considering what, for what purpose, and what is out of scope.
- Facts, material assumptions and limitations are visible where the template puts them, not implied inside the analysis.
- The conclusion is a list of operational outputs tied to the subsections above, not a recap. Unresolved facts and untested law go in open questions.
- The sources list and the appendix of unverified sources are generated from the frozen source pack. Do not write them.

In `classical-memo`, Context does not retell the facts or restate the question — the facts are in the facts section, the question is on the `Question:` line. Facts, assumptions and limitations are stated only in that section, under its three labels, and are not repeated in Context or the executive summary. Open questions in the conclusion name the assumption or limitation of the facts section they would resolve and the subsection they affect, and the conclusion carries no second list of assumptions.

## Citing

- Inline: `[[src:<source_id> <pinpoint>]]` at the end of the sentence whose claim it supports, not at the end of the paragraph. The token resolves to a footnote at render time; never type a citation as prose.
- Quotations: `> [[q:<quote_id>]] <text>`, one per subsection, the quote id from `mf quote extract`. A quote that was not extracted from a saved source cannot appear in the memo.
- Primary sources carry the analysis; commentary supports it. Where a source is only persuasive or only background, the sentence says so.
- Where a source's currency or verification is qualified, the memo says how that affects reliance on it.

## Definitions

A term that needs introduction gets its own short paragraph in the form `Term — short operational definition.`, unbolded, not a bulleted glossary. Standard abbreviations (GDPR, DPIA, CJEU, EDPB, API) are used as they are. A term of art in a language other than the memo language keeps its original form with a gloss in the memo language on first use.

## Confidentiality

Do not name specific clients, amounts, or internal artefacts unless the query supplied them. When in doubt, write "the company", "the product feature", "the data subject".

## Anti-patterns

- Vague attribution: "some scholars argue", "it is generally accepted", "commentators note" with no source.
- Grand phrasing that adds nothing: "comprehensive analysis reveals", "this landmark provision establishes".
- Hedging where the law is clear, and confident phrasing where it is not.
- Padding a subsection so that it looks as substantial as its neighbours.
- Answering an issue nobody asked about, or leaving a planned issue with a paragraph of throat-clearing and no conclusion.
