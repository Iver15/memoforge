# Task parameters — brief-writer (${brief_task})

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- task: `${brief_task}` (`write`, `lint_fix`, `revise` or `shorten`)
- memorandum to build the brief from (the only source of what the brief says): `${memo_path}` (sha256 `${memo_sha}`)
- open points of the memorandum (a JSON list; empty: none): `${open_issues_path}`
- the user's question: ${user_question}
- template: `${brief_template_path}`
- write the brief to: `${primary_output}`
- seed copy already in place (edit it, do not rewrite from scratch; none on `write`): ${seed_path}
- instructions to apply (only these positions; none on `write`): ${instructions_path}
- previous attempt errors to fix: ${retry_errors}

The brief is written in ${memo_language_name}, the language of the memorandum. Use these exactly:

${brief_labels}

The brief gives only the main points — by omission, not compression. Every leaf of the memorandum
is kept or omitted, never both. A leaf is kept when any of these holds: its verdict is `high`; its executive-summary bullet names an amount or a sanction; it answers an explicit sub-question of the user's question (the main question's parts included); its verdict is `undetermined`, or an open issue touches it and a kept conclusion depends on it. Every other leaf is omitted. A date or a deadline alone does not keep a leaf: deadlines travel with the actions. List the omitted leaves in one header comment, `<!-- omitted §s-8 §s-10-1 -->`, and name each as one list item of the last part (its heading is above): the memorandum's conclusion on that leaf as one short statement, never a question, and its verdict as the memorandum writes it in brackets — «EU representative required (medium)», not «whether an EU representative is needed (medium)». Nothing stands in «Conclusions» before its first block. A kept leaf is written in full.

Trace, not paraphrase: every sentence of the brief compresses one identified passage of the
memorandum, and you keep that passage in view while you write the sentence. No new fact, conclusion,
source, pinpoint or action, and no stronger or weaker certainty than the memorandum's.
Rewrite, don't copy: a brief sentence restates the memo in fewer, plainer words; copying memo sentences verbatim is not the method. Meaning, modality and conditions are preserved.

Qualifiers carry over in meaning, in the language of the memorandum: "probably", "should", "only if",
"unless", "subject to", "may", "at the earliest" and a deadline that runs only on a condition are
never upgraded ("should" to "must") or dropped. A deadline in an action is the memorandum's deadline
for that exact step, not the deadline of a neighbouring one. Shortening removes repetition and whole
leaves, never a qualifier. Before you finish a `write` or a `revise`, reread each block
against the memorandum leaves it binds, for these qualifiers alone; on a `revise`, a block the
instructions do not name (nor a partition fix below) still stays as it is — name a qualifier it lost
in your final response.

The bottom line's first sentence answers the question; a decision is presented as a choice only when
the memorandum leaves that step conditional or open; an amount keeps its qualifier (a ceiling, "up
to", a formula, a condition) and is written exactly as the memorandum gives it; the first step
carries its deadline. The bottom line keeps the memo's urgency word for its first action
(«now», «today»). Every explicit sub-question of the user is answered in one sentence somewhere
in the brief. A risk rating is stated as the memorandum states it and never translated into a
likelihood; a conditional rating keeps its condition.

A citation
token is copied from the memorandum exactly as it is written there — the same source id and the
same pinpoint — at most two per conclusion block; no quotation, no blockquote, no `[[q:]]`.
A citation token follows the complete statement it supports, at the end of that clause or
sentence; it never replaces a word or finishes a sentence for you.

One block per kept leaf while the brief keeps at most seven leaves. When it keeps more, group the
kept leaves into at most seven blocks; a block joins leaves only when they carry the same risk verdict
(undetermined included). Leaves with different conclusions may share a block, and each conclusion is
then stated in its own sentence with its own condition — never blended into one statement. Every kept
leaf is bound by a block. Keep a block compact: one or two sentences per leaf conclusion; the length target
is about three pages. Each block opens with its binding comment, `<!-- from §s-4-1 §s-5 -->`, naming
the leaf sections of the memorandum it covers by their anchors — leaves only, never a parent section
that has subsections. Its risk line is the leaves' shared verdict in the risk-line literal above. A leaf with no risk line of its
own is written as undetermined, with the wording for anything not confirmed. Each open point the
brief touches carries that same wording, and no conclusion rests on it. An action
that depends on an open point says so in a short clause inside its line — «after counsel confirms
<the point>».

Actions follow the kept leaves: «What to do» carries the recommendation of every kept leaf and any action of any leaf whose deadline falls within 14 days of the memo date. An action timed from another step («within 14 days of the notification») takes that step's deadline: it is within 14 days of the memo date when the chain is. Each action is one line — who, what, by when — of at most 30 words; details stay out.
Owner, step and deadline or trigger are exactly the memorandum's — a "must" stays an action, never a
choice. Other actions of omitted leaves are left out; two actions merge only when their owner and
deadline or trigger are identical. Assumptions by closure: every condition on which a kept
conclusion depends appears, beside that conclusion in its block or in the closing paragraph;
assumptions no kept conclusion depends on are left out. «What the answer depends on» is one short paragraph, no list, of at most 60 words, naming only the few conditions that would change the overall answer; a condition of a kept conclusion is written beside that conclusion in its block.
Keep each part within the word budgets above.

On `lint_fix`, fix exactly the findings of the report and nothing else. On `revise`, change only
the blocks the instructions name; a length instruction is met by omission, as on `shorten`.
Partition fix: when an issue concerns the partition — BF-05, or B-03, B-04 or B-05 on an omitted
leaf — you may change together the omitted comment, the «Other points assessed» items, the conclusion
block of that leaf (added or removed), its actions, its assumptions and the bottom line; every other
block stays as it is. On `shorten`, bring it to about three pages by omission: move further leaves that meet no keep criterion
to the omitted list and drop their assumptions and those of their actions the actions rule does not
keep — an action due within 14 days of the memo date stays; never compress a kept sentence or drop a
qualifier, condition, verdict or open point.

No reference to the memorandum or to a section of it, no Sources list, no sources marker, no banner.
About three pages; shorter is better.

Last action (Bash, after the brief is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
