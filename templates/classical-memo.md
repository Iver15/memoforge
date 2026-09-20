---
citation_style: inline
---

# Template: classical-memo

**Use when:** multi-issue analysis where the reader needs full coverage and an audit trail of the reasoning. The default for complex regulatory, transactional or cross-disciplinary questions.

The rhetorical surface is the four-beat analytical subsection from `lib/prose-style.md`. This is the longest form and the closest to the classical office memorandum.

The memo language, the section headings and the risk-line literal are given in your task prompt; the English forms below are the example.

## Sections, in this order

1. **Title** — `# <Subject>: <Analytical framing>`. No date or jurisdiction inside the title line.
2. **Header block** — date (YYYY-MM-DD), jurisdictions, the question, the template name. Body paragraphs, not a table. The `Question:` line carries the user's own question (`user_query`) word for word: copy it, do not rephrase, shorten, sharpen or re-scope it. The one exception is an instruction to the assistant about the form of the work that leads or trails the question — the language to write in, the format, the jurisdiction to apply ("напиши меморандум на русском по праву РФ", "write a memo in English under UK law"): that instruction is dropped, because the header already states the language and the jurisdictions. Nothing else is cut. A question with several limbs stays whole. The title above may name the analytical focus, but it never stands in for the question — a reader has to find what they asked in the document they get back.
3. **Context** — one or two unnumbered paragraphs above the first `## ` heading: who the memo is for, the purpose, what is out of scope — two or three sentences. It does NOT retell the facts (they are in the facts section) and does not restate the question (it is on the `Question:` line).
4. **`## 1. Executive summary`** — bullets only. Each bullet is one conclusion for one analytical subsection, ending with its risk verdict and a reference to that subsection. Literal form, the verdict last and closed by a period:
   `- Consent is available as a lawful basis for the marketing flow. Risk: medium.`
   No prose paragraphs in this section: facts belong in the facts section, framing in the Context paragraphs.
5. **`## 2. Background and definitions`** — optional. Terms the reader needs, one short paragraph each in the form `Term — definition.` Skip the whole section for a counsel-to-counsel memo; the following sections then take the next numbers.
6. **`## 3. Facts, assumptions and limitations`** — required. The only place where facts, assumptions and limitations are stated. Three blocks, each opened by a bold label paragraph on its own line — `**Facts**`, `**Assumptions**`, `**Limitations**` — never `###` headings (lint treats `###` as an analytical subsection). Facts = what the user stated in the question and in the intake / follow-up answers, merged into one account without saying which answer a fact came from. Assumptions = what the analysis takes as given without the user having confirmed it, each with the conclusion it affects (section reference). Limitations = what restricts confidence (no case law found, guidance not binding, document not reviewed), each with its section reference. A block with nothing to say is omitted. Short.
7. **Analytical subsections** — `## 4.`, `## 5.`, … one per legal question, with `### 4.1.`, `### 4.2.` for sub-issues. Headings are noun phrases naming the subject. Each subsection follows the four beats below.
8. **`## N. Conclusion and recommendations`** — one item per analytical subsection, each naming the action, its trigger and its owner, and each pointing back at its subsection. No risk verdicts here: the verdict belongs to the subsection's Risk line and the summary bullet. "Open questions" stay as a sub-list — each open question names the assumption or limitation of the facts section it would resolve and the subsection it affects.
9. **Sources** — you do not write this section. End the draft with the marker line `<!-- sources: generated -->`. The renderer builds the citations, the source list and the appendix of unverified sources from the frozen source pack, and adds the status banner when the run needs one. Citations reach the reader as short parentheticals linked to the source; the full record of each one is in the Sources annex.

## The four beats

Every numbered analytical subsection carries these, in order:

1. **Issue and conclusion.** Open with the answer for this issue, then the question and the controlling authority, cited as `[[src:<source_id> <pinpoint>]]`.
2. **The source.** State the controlling text with its locator and citation; quote it verbatim only where the exact words carry the point, as `> [[q:<quote_id>]] <text>` from `mf quote extract`.
3. **Rule explanation and application.** What the authority establishes, then what it means for the user's facts. This beat also carries the counterargument: one sentence giving the strongest contrary reading or contrary authority, and one resolving it — why the conclusion holds and what would have to change for it not to. This pair is required in every subsection, whatever the risk verdict.
4. **Risk line.** The last paragraph of the subsection opens with the verdict written literally, then carries the justification and the recommendation with its action, trigger and owner in the same paragraph:
   `Risk: medium. The basis holds only while the affirmative action stays in the flow. Product must keep the opt-in unticked before launch.`
   The verdict word is one of `high`, `medium`, `low`, `undetermined`, lower case and followed by a period.

## Anchors and versions

`<!-- §s-4 -->` on a `## ` heading and `<!-- §s-4-1 -->` on a `### ` heading are inserted by `mf draft anchor` after the first version — do not write them yourself, and leave the existing ones in place when you edit a later version. Later versions are targeted edits: change the sections the instructions name and nothing else.

## Tone and length

Formal, analytical, precise, in the memo language; see `lib/prose-style.md`. Typically 3000–6000 words, but a straightforward subsection can be 200–300 words and still carry all four beats. Do not pad.

## Rules

- The four beats appear in every numbered analytical subsection.
- IRAC and CREAC are the underlying logic, never visible sub-headings: no heading reading `Rule`, `Application` or `Conclusion` inside a subsection.
- The facts section is required. Facts merged into the Context paragraphs or the executive summary is a structural defect.
- Cite with `[[src:]]` and `[[q:]]` tokens only; do not write citations, footnote numbers or a sources list as prose.
- Risk verdicts match wherever they appear — summary bullet and risk line.
- `mf draft lint` and `mf draft audit-citations` check the mechanical rules: sentence and paragraph caps, em-dash use, heading levels, the correspondence between summary bullets, subsections and conclusion items, risk-line format, quote and token form, placeholders and template section order. They run before review, and their findings come back to you as a list to fix.
