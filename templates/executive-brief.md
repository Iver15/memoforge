---
citation_style: inline
---

# Template: executive-brief

**Use when:** a business stakeholder needs a defensible answer fast. Short, dense, no abstract. The default for a single compliance question or a product-team request.

Same rhetorical surface as `classical-memo` — the four beats from `lib/prose-style.md` — compressed: one or two sentences per beat, and no more than three issues.

The memo language, the section headings and the risk-line literal are given in your task prompt; the English forms below are the example.

## Sections, in this order

1. **Title** — `# <Subject>: <Analytical framing>`. No date or jurisdiction inside the title line.
2. **Header block** — date (YYYY-MM-DD), jurisdictions, the question. The `Question:` line carries the user's own question (`user_query`) word for word: copy it, do not rephrase, shorten, sharpen or re-scope it. The one exception is an instruction to the assistant about the form of the work that leads or trails the question — the language to write in, the format, the jurisdiction to apply ("напиши меморандум на русском по праву РФ", "write a memo in English under UK law"): that instruction is dropped, because the header already states the language and the jurisdictions. Nothing else is cut. A question with several limbs stays whole. The title above may name the analytical focus, but it never stands in for the question — a reader has to find what they asked in the document they get back.
3. **Context** — one short unnumbered paragraph: who is considering what, for what purpose, and the bottom line. There is no separate executive summary; this paragraph is it. The facts the analysis rests on go here too.
4. **Key assumptions** — optional, only when an assumption changes the answer. A few bullets at most.
5. **Analytical subsections** — `## 1.`, `## 2.`, `## 3.`; three at the outside. Related risks are combined into one subsection rather than split. Each follows the four beats below.
6. **`## N. Recommendations`** — one bullet per subsection linking its risk to an action, plus any recommendation that cuts across them. Each bullet points back at its subsection and names an action, a trigger and an owner.
7. **Sources** — you do not write this section. End the draft with the marker line `<!-- sources: generated -->`. The renderer builds the citations, the source list and the appendix of unverified sources from the frozen source pack, and adds the status banner when the run needs one. Citations reach the reader as short parentheticals linked to the source; the full record of each one is in the Sources annex.

## The four beats, compressed

1. **Issue and conclusion.** One sentence: the answer, the issue, and the controlling authority as `[[src:<source_id> <pinpoint>]]`.
2. **The source.** State the controlling text with its locator and citation; quote it verbatim only where the exact words carry the point, as `> [[q:<quote_id>]] <text>` from `mf quote extract`.
3. **Rule explanation and application.** One or two sentences on what the authority establishes and what it means here, including the sentence that names the strongest counterargument and resolves it. Compressed, but present — a brief is not an excuse to skip the other side.
4. **Risk line.** The last paragraph of the subsection opens with the verdict written literally, then carries a one-sentence justification and one concrete recommendation with its action, trigger and owner:
   `Risk: medium. The basis holds while the opt-in stays unticked. Product must keep it unticked before launch.`
   The verdict word is one of `high`, `medium`, `low`, `undetermined`, lower case and followed by a period.

## Anchors and versions

`<!-- §s-1 -->` anchors are inserted by `mf draft anchor` after the first version — do not write them yourself, and leave the existing ones in place when you edit a later version. Later versions are targeted edits: change the sections the instructions name and nothing else.

## Tone and length

Direct, plain language for a reader who is not a lawyer; define an unavoidable legal term in a phrase where it first appears, inline — this template has no background section. Keep it tight: the word cap for the brief is enforced by `mf draft lint`, which counts the body and the footnotes it will generate. If the question genuinely cannot be answered defensibly at this length, write the honest short version and say so in your final response rather than padding or over-compressing.

## Rules

- All four beats appear in every subsection.
- Prose for the beats; bullets only for key assumptions and recommendations.
- Three analytical subsections at most. Extra questions from the plan become one-line entries under recommendations, or the task belongs in `classical-memo`.
- Cite with `[[src:]]` and `[[q:]]` tokens only; do not write citations, footnote numbers or a sources list as prose.
- Risk verdicts match between each subsection's risk line and its recommendation bullet.
- `mf draft lint` and `mf draft audit-citations` check the mechanical rules: sentence and paragraph caps, em-dash use, heading levels, risk-line format, the word cap, quote and token form, placeholders and template section order. They run before review, and their findings come back to you as a list to fix.
