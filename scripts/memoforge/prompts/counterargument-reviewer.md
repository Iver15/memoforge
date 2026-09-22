# Task parameters — counterargument-reviewer (iteration ${iteration})

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- draft under review: `${draft_path}` (v`${draft_version}`)
- `draft_sha` to put in your output: `${draft_sha}`
- checklist (grade every id, no additions, no omissions): `${paths_checklist}`
- deterministic findings attached to this draft (empty: none attached): ${lint_attachment}
- research findings, including `considered_excluded`: ${research_files}
- source registry, with each source's `tier` and the `raw_path` of its saved text (relative to work_dir): `${work_dir}/research/sources.json`
- previous attempt errors to fix: ${retry_errors}

`approved` is a normal outcome and means zero blockers. Grade `unknown` only when the draft
does not let you decide; on a `hard_fail` item that costs the approval.

Every issue carries `attack_vector`: `contrary_authority`, `overconfidence`, `missing_fact`,
`weak_application` or `understated_risk`.

The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in ${memo_language_name} saying what the client must check before relying on the memo.

## Checking a source before you say what it holds

The research finding is the pairing key: it says what the researcher recorded. For a `critical` source
the saved text is the ceiling, and where the finding and the text disagree, the text wins.

Lookup budget: ${lookup_budget} units. A lookup costs 1, reading a whole statute article 1, reading a
whole court act 3. Look a statement up with

`${mf} quote locate --workdir ${work_dir} --source <id> --text "<phrase>" [--context N]`

Give `--text` a few distinctive words the source itself would use, not the draft's paraphrase. The
answer is JSON and exits 0 whatever it finds: `found` or `ambiguous` bring `passages`, whole sentences
around each match (`--context N` widens them); `not_found` brings `candidates`, the sentences holding
most of your words; `no_raw`, `raw_changed` and `unknown_source` mean the text cannot be checked here.
A whole read is the Read tool on the source's `raw_path`.

Before you raise an issue whose suggestion says what a source holds — a contrary holding, a rule the
draft passed over, the reading a court gave — list those statements and check each one against the saved
text first. This is required before any such issue, so the budget is never spent before them. Each
checked statement gets one row in `text_checks`: `{"source_id", "section_id", "status",
"finding_disagrees", "note"}`, with `status` `confirmed`, `contradicted`, `inconclusive` (looked up, not
settled, not read whole) or `not_reached` (the budget ran out first). `finding_disagrees` is true when the
research finding departs from the text.

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

Absence and contradiction need a reading, not a miss. "The rule is not in the source" or "the court did
not hold this" is concluded only after reading the whole saved text: the cited statute article, or the
court act in full with Read on its `raw_path` (3 units). A lookup that finds a contradicting passage of
the court's own reasoning is enough without the whole read. Any number of `not_found` answers is never
proof of absence: without the whole read the row is `inconclusive`.

An issue attaches to the draft sentence, never to the finding. When the draft agrees with the text and
the finding does not, the draft passes: the row carries `"finding_disagrees": true` and a `note` saying
where the finding departs from the text.

A suggestion that tells the writer what a source holds, or asks to strengthen, restate or re-attribute a
holding or a rule, carries `source_evidence` `{"source_id", "status": "confirmed", "passage"}` with the
passage, at most 600 characters. Without it the suggestion may only ask to withdraw the statement,
qualify it or mark it as unresolved. For example:

```json
{
  "severity": "blocker",
  "category": "omitted_contrary_authority",
  "section_id": "s-4-1",
  "issue": "The appellate decision recorded as contrary for this issue is not addressed anywhere.",
  "suggestion": "Take the holding that an override used only exceptionally leaves the decision solely automated into the counterargument beat, and say why the conclusion survives it.",
  "checklist_id": "CTR-02",
  "attack_vector": "contrary_authority",
  "source_evidence": {
    "source_id": "case-3",
    "status": "confirmed",
    "passage": "A human override that is available in principle but used only exceptionally does not make the decision one that is not based solely on automated processing."
  }
}
```

with its row in `text_checks`:
`{"source_id": "case-3", "section_id": "s-4-1", "status": "confirmed", "finding_disagrees": false, "note": "Holding located in the court's reasoning."}`

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
