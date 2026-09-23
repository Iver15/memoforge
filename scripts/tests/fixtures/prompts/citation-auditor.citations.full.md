# Task parameters — citation-auditor (iteration 1)

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot citations --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under review: `drafts/v1.md` (v`1`)
- `draft_sha` to put in your output: `0000000000000000000000000000000000000000000000000000000000000000`
- checklist (grade every id, no additions, no omissions): `{CHECKLISTS}\citations.json`
- deterministic findings attached to this draft (empty: none attached): 
- frozen source pack: `{WORK_DIR}/research/source-pack.json`
- deterministic citation audit already run for you (mechanical checks only — existence, verbatim text, freeze, pinpoints): `{WORK_DIR}/citations.json`
- research findings, one file per layer — the pairing key of each claim: `research/statutes.json`, `research/case_law.json`, `research/doctrine.json`
- claim-to-authority pairs: pair every `[[src:<id>]]` claim of the draft with the finding in the research files whose `source_id` matches. The finding is the pairing key: its `proposition` and `pinpoint` say what the researcher recorded. For a `critical` source the saved text is the ceiling, and where the finding and the text disagree, the text wins
- source registry, with each source's `tier` and the `raw_path` of its saved text (relative to work_dir): `{WORK_DIR}/research/sources.json`
- polish re-check (none: an ordinary review of the whole draft): none
- previous attempt errors to fix: none

`approved` is a normal outcome and means zero blockers. Grade `unknown` only when the draft
does not let you decide; on a `hard_fail` item that costs the approval.

Every issue carries `issue_category`: `source_drift`, `source_pack_mismatch` or
`unsupported_claim`. Existence, verbatim accuracy and currency are already decided by
`mf draft audit-citations`; do not re-litigate them.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.
In this review a finding with `severity: major` carries `issue_client` too, on the same terms.

## Checking claims against the saved text

The research finding says what the researcher recorded. For a `critical` source the saved text is the
ceiling: where the finding and the text disagree, the text wins.

Lookup budget: 20 units. A lookup costs 1, reading a whole statute article 1, reading a
whole court act 3. Look a statement up with

`{MF} quote locate --workdir {WORK_DIR} --source <id> --text "<phrase>" [--context N]`

Give `--text` a few distinctive words the source itself would use, not the draft's paraphrase. The
answer is JSON and exits 0 whatever it finds: `found` or `ambiguous` bring `passages`, whole sentences
around each match (`--context N` widens them); `not_found` brings `candidates`, the sentences holding
most of your words; `no_raw`, `raw_changed` and `unknown_source` mean the text cannot be checked here.
A whole read is the Read tool on the source's `raw_path`.

Spend the budget in this order:

1. The statements you intend to flag or propose new wording for, and every CIT-01 candidate — a
   statement of law whose paired research finding does not record the rule the draft states: no finding
   at all, or a finding about something else. List them first and check each one before items 2–4: read
   the cited statute article whole (1 unit), or look up the court's own words. This is required before
   any such issue, so the budget is never spent before them.

   The pairs the last review did not reach come next, before items 2–4: the cited statute and case-law
   pairs that no earlier citations review checked against the saved text, one per line as
   `source_id · section_id · reason` (`not_reached`: that review's budget ran out first; `never_checked`:
   no review checked it; `none`: nothing is carried over).

none

2. Every statement of what a court held or applied, for a `critical` case-law source that carries a
   conclusion of the draft.
3. A statute rule on which a summary bullet, a conclusion or a risk line rests.
4. The rest, while budget remains.

What a court did is confirmed only by the court's own sentence. An issue or a suggestion that says what
a court did — held, applied, followed, used, measured by, awarded on the basis of — needs
`source_evidence` with `"status": "confirmed"` and a passage in which the court itself states it, in its
own reasoning or its operative part. A contract clause, an offer, a party's position or a lower court's
view that the act quotes or recites is never that evidence, even when the words match and even when the
court quotes it approvingly nearby. An issue about what an offer or a contract says may cite the recited
clause, but its suggestion then attributes the words to the offer or the contract, never to the court.
If your lookups find no sentence in which the court itself states what it did, the suggestion may only
ask to withdraw the attribution or to qualify it as unresolved. A statute's rule is confirmed by the text
of the cited article.

The passage is copied, not abbreviated. `source_evidence.passage` is one exact, contiguous run of the
saved text as `mf quote locate` returned it, at most 600 characters, cut only at its two ends: no
ellipses, no joined fragments and no words of your own, so the reader can find it in the source.

Each checked statement gets one row in `text_checks`: `{"source_id", "section_id", "status",
"finding_disagrees", "note"}`. `finding_disagrees` is true when the research finding departs from the text.

- `confirmed` — the text says what the draft says. No issue.
- `contradicted` — the located text says otherwise, or the rule is absent. The draft sentence gets a
  blocker: `issue_category: unsupported_claim`, `checklist_id` `CIT-01` (the rule is not in the source)
  or `CIT-02` (the holding is misstated), and `source_evidence` `{"source_id", "status": "contradicted",
  "passage"}` with the passage, at most 600 characters. That checklist item grades `false`.
- `inconclusive` — you looked it up, it did not settle, and you did not read the whole text. Not an
  issue: grade CIT-02 on the finding as before, and say in `note` what you tried.
- `not_reached` — the budget ran out before you checked it. Not an issue either: grade CIT-02 on the
  finding; the row tells the next reader what was not checked against the text.

Absence and contradiction need a reading, not a miss. "The rule is not in the source" or "the court did
not hold this" is concluded only after reading the whole saved text: the cited statute article, or the
court act in full with Read on its `raw_path` (3 units). A lookup that finds a contradicting passage of
the court's own reasoning is enough for `contradicted` without the whole read. Any number of `not_found`
answers is never proof of absence: without the whole read the row is `inconclusive`.

The blocker attaches to the draft sentence, never to the finding. When the draft agrees with the text
and the finding does not, the draft passes: the row carries `"finding_disagrees": true` and a `note`
saying where the finding departs from the text, so a corrected draft is never held by an old finding.

A suggestion that tells the writer what a source holds, or asks to strengthen, restate or re-attribute a
holding or a rule, carries `source_evidence` with `"status": "confirmed"` and the passage. Without it the
suggestion may only ask to withdraw the statement, qualify it or mark it as unresolved.

A rule stated on a source that does not contain it is CIT-01, `unsupported_claim`, even when a token is
present. CIT-04 is only for "the rule is in the cited source, and the pinpoint points elsewhere".

A statute rule on a time limit, a threshold or an exception is checked against the whole paragraph it sits
in. A sibling limb of that paragraph that the facts engage and that changes the advice — a shorter limit,
an exception — and that the draft leaves out means the paraphrase does not say what the source says:
CIT-02 grades `false`, and the draft sentence gets a blocker with `checklist_id` `CIT-02` and
`issue_category: source_drift`.

For example, a draft that gives a submission the judgment records as the court's holding, where the
court's own reasoning says otherwise:

```json
{
  "severity": "blocker",
  "category": "holding_misstated",
  "section_id": "s-4-2",
  "issue": "4.2 says the court held that consent was required; the court's reasoning rejects that submission.",
  "suggestion": "Withdraw the statement that the court required consent, or qualify it as unresolved.",
  "checklist_id": "CIT-02",
  "issue_category": "unsupported_claim",
  "source_evidence": {
    "source_id": "case-1",
    "status": "contradicted",
    "passage": "The court does not accept that consent was the only lawful basis open to the controller."
  }
}
```

and the top-level rows for it and for a statement the budget did not reach:

```json
"text_checks": [
  {"source_id": "case-1", "section_id": "s-4-2", "status": "contradicted", "finding_disagrees": true,
   "note": "The finding also reads the claimant's submission as the holding."},
  {"source_id": "case-2", "section_id": "s-5-1", "status": "not_reached", "finding_disagrees": false,
   "note": "Budget spent before the practice statement in 5.1."}
]
```

## Polish re-check

When the polish re-check parameter above is not `none`, this review re-checks the final polish of the
open findings it lists, and the draft is the polished one. Follow that line: grade CIT-01, CIT-02 and
CIT-04 on the sections it names, checking their statements against the saved text as above; every
other checklist item still gets its answer, on the same sections. Add a top-level `resolutions` array
with one row per listed id: `{"id": "om-<n>", "status": "resolved" | "open", "note": "<one sentence>"}`
— `resolved` when the polished text no longer carries the problem the finding names, `open` when it
still does. A new problem in those sections is an ordinary issue of this review. When the parameter is
`none`, write no `resolutions`.

## Write

- `steps/s-042/a1/citations/v1-citations.json` (schema `review` — `{SCHEMAS}\review.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot citations --state done`
