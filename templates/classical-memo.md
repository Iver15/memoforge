---
citation_style: inline
---

# Template: classical-memo

**Use when:** multi-issue analysis where the reader needs full coverage and an audit trail of the reasoning. The default for complex regulatory, transactional or cross-disciplinary questions.

The rhetorical surface is the four-beat analytical subsection from `lib/prose-style.md`. This is the longest form and the closest to the classical office memorandum.

## Sections, in this order

1. **Title** — `# <Subject>: <Analytical framing>`. No date or jurisdiction inside the title line.
2. **Header block** — date (YYYY-MM-DD), jurisdictions, the question, the template name. Body paragraphs, not a table. The `Question:` line carries the user's own question (`user_query`) word for word: copy it, do not rephrase, shorten, sharpen or re-scope it. A question with several limbs stays whole. The title above may name the analytical focus, but it never stands in for the question — a reader has to find what they asked in the document they get back.
3. **Context** — one or two unnumbered paragraphs above the first `## ` heading: who is considering what, for what purpose, and what is out of scope.
4. **`## 1. Executive summary`** — bullets only. Each bullet is one conclusion for one analytical subsection, ending with its risk verdict and a reference to that subsection. Literal form, the verdict last and closed by a period:
   `- Consent is available as a lawful basis for the marketing flow. Risk: medium.`
   No prose paragraphs in this section: facts belong in the facts section, framing in the Context paragraphs.
5. **`## 2. Background and definitions`** — optional. Terms the reader needs, one short paragraph each in the form `Term — definition.` Skip the whole section for a counsel-to-counsel memo; the following sections then take the next numbers.
6. **`## 3. Facts, assumptions and limitations`** — required. The facts the user supplied, the material assumptions the analysis rests on, and the limitations that affect confidence. Short.
7. **Analytical subsections** — `## 4.`, `## 5.`, … one per legal question, with `### 4.1.`, `### 4.2.` for sub-issues. Headings are noun phrases naming the subject. Each subsection follows the four beats below.
8. **`## N. Conclusion and recommendations`** — one item per analytical subsection, each naming the action, its trigger and its owner, and each pointing back at its subsection. Material assumptions and open questions as sub-lists here: every assumption either linked to the question that would resolve it or marked as affecting no conclusion.
9. **Sources** — you do not write this section. End the draft with the marker line `<!-- sources: generated -->`. The renderer builds the citations, the source list and the appendix of unverified sources from the frozen source pack, and adds the status banner when the run needs one. Citations reach the reader as short parentheticals linked to the source; the full record of each one is in the Sources annex.

## The four beats

Every numbered analytical subsection carries these, in order:

1. **Issue and conclusion.** Open with the answer for this issue, then the question and the controlling authority, cited as `[[src:<source_id> <pinpoint>]]`.
2. **The source.** `> [[q:<quote_id>]] <text>` from `mf quote extract`, introduced by its locator. When a `quote skip` is recorded for this section and source — for any reason — replace the quote with a close paraphrase of the provision carrying `[[src:]]`. That is a legitimate outcome; do not go looking for another passage to fill the shape.
3. **Rule explanation and application.** What the authority establishes, then what it means for the user's facts. This beat also carries the counterargument: one sentence giving the strongest contrary reading or contrary authority, and one resolving it — why the conclusion holds and what would have to change for it not to. This pair is required in every subsection, whatever the risk verdict.
4. **Risk line.** The last paragraph of the subsection opens with the verdict written literally, then carries the justification and the recommendation with its action, trigger and owner in the same paragraph:
   `Risk: medium. The basis holds only while the affirmative action stays in the flow. Product must keep the opt-in unticked before launch.`
   The verdict word is one of `high`, `medium`, `low`, `undetermined`, lower case and followed by a period.

## Anchors and versions

`<!-- §s-4 -->` on a `## ` heading and `<!-- §s-4-1 -->` on a `### ` heading are inserted by `mf draft anchor` after the first version — do not write them yourself, and leave the existing ones in place when you edit a later version. Later versions are targeted edits: change the sections the instructions name and nothing else.

## Tone and length

Formal, analytical, precise; English regardless of the query language. See `lib/prose-style.md`. Typically 3000–6000 words, but a straightforward subsection can be 200–300 words and still carry all four beats. Do not pad.

## Rules

- The four beats appear in every numbered analytical subsection, with the quote beat replaced by a paraphrase only where a `quote skip` is recorded.
- IRAC and CREAC are the underlying logic, never visible sub-headings: no heading reading `Rule`, `Application` or `Conclusion` inside a subsection.
- The facts section is required. Facts merged into the Context paragraphs or the executive summary is a structural defect.
- Cite with `[[src:]]` and `[[q:]]` tokens only; do not write citations, footnote numbers or a sources list as prose.
- Risk verdicts match wherever they appear — summary bullet, risk line, conclusion item.
- `mf draft lint` and `mf draft audit-citations` check the mechanical rules: sentence and paragraph caps, em-dash use, heading levels, the correspondence between summary bullets, subsections and conclusion items, risk-line format, quote and token form, placeholders and template section order. They run before review, and their findings come back to you as a list to fix.
