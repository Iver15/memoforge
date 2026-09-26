# Task parameters — brief-fidelity-reviewer

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot fidelity --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- memorandum, the reference: `drafts/v1.md` (sha256 `0000000000000000000000000000000000000000000000000000000000000000`)
- brief under review: `brief/v1.md`
- `draft_sha` to put in your output: `1111111111111111111111111111111111111111111111111111111111111111`
- open points of the memorandum (a JSON list; empty: none): `brief/open-issues.json`
- the user's question: How long may the client keep customer records?
- block ids of this brief (use exactly these as `block`, or `document` for the brief as a whole): s-header = the title and header lines; s-main = «Bottom line»; s-b1 = «Record retention»; s-actions = «What to do»
- checklist (grade every id, no additions, no omissions): `{CHECKLISTS}\brief-fidelity.json`
- your previous review of this brief (`none` in round 0): `none`
- blocks changed since that review (`all`, `none` or a list of block ids): all
- previous attempt errors to fix: none

Read the memorandum, the brief, the open points, the question and the previous review when one is
named — nothing else. The memorandum is the reference: the source texts are not needed, and the
memorandum itself is not under review.

## Scope of this review

The brief keeps only the main points, by omission: every leaf of the memorandum is kept in a block
or listed in the header's `<!-- omitted … -->` comment, and the omitted ones are named, one list item
each with the memorandum's conclusion and its verdict, in the last part «Other points assessed». A leaf meets a keep criterion when
its verdict is `high`; its executive-summary bullet names an amount or a sanction; it answers an
explicit sub-question of the user's question (the main question's parts included); or its verdict
is `undetermined`, or an open issue touches it and a kept conclusion depends on it. A date or a
deadline alone does not keep a leaf: an action of any leaf due within 14 days of the memo date
belongs in the actions instead. The brief rewrites the memorandum in fewer, plainer words: check
meaning, not wording.

Round 0 — `previous_review_path` is `none` — is a full trace. For every sentence of the brief, find
the passage of the memorandum it rests on: a claim with none is BF-01. Compare it with its whole
bound leaf, not a fragment — "probably", "should", "only if", "unless", "subject to", "may", "at the
earliest", a deadline that runs only on a condition: one dropped is BF-02, one upgraded or weakened
is BF-03. Check the bottom line clause by clause against the leaves it rests on: a hedge moved from
one conclusion to another, a ceiling written as a range, a "must" written as a choice, or a risk
rating translated into a likelihood is BF-03 (a rating is never translated into a likelihood).
Then sweep the memorandum leaf by leaf — its executive-summary bullets, its recommendations and the
omitted list: an omitted leaf that meets a keep criterion, or an item of «Other points assessed» that
misses an omitted leaf or misstates its conclusion or verdict, is BF-05; a kept leaf's recommendation, or an action of any leaf due within 14 days of
the memo date, missing or with another owner, step or deadline than the memorandum gives it, is
BF-07 (other actions of omitted leaves may be absent); a missing
condition of a kept conclusion is BF-08 (other assumptions may be absent); an explicit sub-question
left unanswered is BF-09. Grade all nine items.

Round 1 and later — a previous review is named — converge on it:

- (a) For each issue of the previous review, say in `reasoning` whether it is fixed. One not fixed
  stays in `issues` at the same severity; its `brief_quote` comes from the current brief where there
  is text to quote, and stays empty where there is none (an uncovered leaf).
- (b) Re-trace in full only the blocks named in `changed_blocks`, as in round 0. `all` means every
  block; `none` means no block is re-traced in full, while (a) and (c) still apply.
- (c) In an unchanged block, raise a new issue only for a `blocker` you can quote from both texts.

Still grade all nine items: an item whose only evidence is unchanged text keeps its previous grade.
If the previous review is missing or unreadable, trace the whole brief as in round 0 and say so.

Every item graded `false` or `unknown` has an issue with its `checklist_id`, at the severity the
checklist gives the item: BF-01 to BF-04 are blockers; BF-05 is a blocker for a `high` leaf omitted
or left out and a major for any other break of the partition; BF-06 to BF-09 are majors. `approved` means zero
blockers and zero majors.

Each issue carries `brief_quote` and `memo_quote`, copied verbatim; one of them is empty only when
that text has nothing to quote.

The memorandum and the brief are written in English. `issue`, `suggestion` and
`reasoning` are always English; the two quotes stay in the language of the texts.

## Write

- `steps/s-042/a1/fidelity/r0-fidelity.json` (schema `brief-review` — `{SCHEMAS}\brief-review.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot fidelity --state done`
