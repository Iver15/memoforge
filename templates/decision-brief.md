---
citation_style: inline
---

# Template: decision-brief

**Use when:** a finished memorandum has to reach a decision maker who is not a lawyer. The brief is built from the delivered memorandum only, is read on its own and may be forwarded on its own. Every sentence compresses one identified passage of the memorandum, with its qualifiers and conditions; nothing is added.

The brief is written in the memo language. The part headings, the three header labels, the risk-line literal, the label of the omitted leaves and the wording for anything not confirmed are given in your task prompt (`brief_labels`); use them exactly as given. The English forms below are the example.

## What the brief keeps

The brief gives only the main points, by omission, not compression. A leaf is a numbered analytical section without subsections, or a subsection such as `4.1`; its id is the memo's anchor (`s-4-1`, `s-5`). Every leaf of the memo is either kept or omitted, never both. A leaf is kept when any of these holds: its verdict is `high`; its executive-summary bullet names an amount or a sanction; it answers an explicit sub-question of the user's question (the main question's parts included); its verdict is `undetermined`, or an open issue touches it and a kept conclusion depends on it. Every other leaf is omitted. A date or a deadline alone does not keep a leaf: deadlines travel with the actions. A kept leaf is written in full — every condition, qualifier and deadline of its conclusion; an omitted leaf is only named in the "Other matters" sentence. Rewrite, don't copy: a brief sentence restates the memo in fewer, plainer words; copying memo sentences verbatim is not the method. Meaning, modality and conditions are preserved.

## Parts, in this order

1. **Title and header** — `# <Subject>: decision brief`, in the memo language, with no date inside the title line. Then three header lines, one per label, each a paragraph of its own — put a blank line between them, or the document joins them into one line:

   `**Date:** YYYY-MM-DD`

   `**Jurisdictions:** …`

   `**Question:** <one sentence in your own words, faithful to the user's question>`

   Then one comment listing the omitted leaves: `<!-- omitted §s-8 §s-10-1 -->` (`<!-- omitted -->` when every leaf is kept). The reader never sees it.
2. **`## Bottom line`** — two to four sentences. The first sentence answers the question; the overall risk; the first action with its deadline. A decision is presented as a choice only when the memo leaves that step conditional or open — a `must` of the memo stays a `must`. An amount keeps its qualifier: a ceiling, "up to", a formula, a condition.
3. **`## Conclusions`** — when leaves are omitted, it opens with one sentence before the first block: `Other matters: <subject> (<risk as the memo states it>); <subject> (…).`, each omitted leaf with its subject and its verdict as the memo writes it. Then `### <noun phrase>` blocks: one block per kept leaf while the brief keeps at most seven leaves. When it keeps more, group the kept leaves into at most seven blocks; a block joins leaves only when they carry the same risk verdict. Leaves with different conclusions may share a block, and each conclusion is then stated in its own sentence with its own condition — never blended into one statement. Every kept leaf is bound by a block. Keep a block compact: one or two sentences per leaf conclusion.
   - The first line under each `###` is the binding comment naming the leaf ids the block covers: `<!-- from §s-4-1 §s-5 -->`. Name leaves only, never a parent section that has subsections.
   - In the block, in this order: the conclusion (one or two sentences per leaf conclusion) → what it rests on: the memo's own `[[src:<id> <pinpoint>]]` token, copied exactly, at most two per block, with no quotation and no discussion of cases → the condition that would change the conclusion, if the memo gives one → the risk line exactly as the memo writes it: `Risk: <level>.` and one sentence, as the last paragraph of the block.
   - Every leaf a block covers carries the same risk verdict, and the block's risk line states it. Leaves with different verdicts — an `undetermined` leaf and a determinate one included — go to separate blocks. A risk rating is stated as the memo states it and never translated into a likelihood; a conditional rating ("medium; high without the warehouse mark") keeps its condition.
   - A leaf of the memo with no risk line of its own is written as `undetermined`, and its block carries the wording for anything not confirmed.
   - An open point of the memo (listed in the open issues you are given) is named in its block with that same wording, and no conclusion rests on it.
4. **`## What to do`** — a numbered list. «What to do» carries the recommendation of every kept leaf and any action of any leaf whose deadline falls within 14 days of the memo date. Each action is one line — who, what, by when — of at most 30 words; details stay out. The owner, the step and the deadline or event are the memo's; a `must` stays an action, never a choice. Other actions of omitted leaves are left out. Two actions merge only when their owner and deadline or trigger are identical.
5. **`## What the answer depends on`** — «What the answer depends on» is one short paragraph, no list, of at most 60 words, naming only the few conditions that would change the overall answer; a condition of a kept conclusion is written beside that conclusion in its block. Assumptions no kept conclusion depends on are left out. Omit the part when there are none.

Every explicit sub-question of the user is answered in one sentence somewhere in the brief: the bottom line, a block or the "Other matters" sentence.

## What the brief never contains

No blockquotes, no `[[q:]]` token, no `<!-- sources: generated -->` marker, no Sources list and no appendix: the code renders the citations as links and adds any banner itself. No reference to the memorandum or to a section of it — the reader does not have it. No new fact, conclusion, source, pinpoint, amount or action: every amount is written exactly as the memo gives it.

## Length and tone

About three pages; shorter is better. Word budgets (the lint checks them): the bottom line 80 words, each conclusion block 100, the "Other matters" sentence 60, each action 30, «What the answer depends on» 60. Plain language for a reader who is not a lawyer: an unavoidable legal term gets a half-sentence explanation where it first appears. Shorten by omission: move further leaves that meet no keep criterion to the omitted list and drop their assumptions and those of their actions the actions rule does not keep — an action due within 14 days of the memo date stays; never compress a kept sentence or drop a qualifier, condition, verdict or open point.
