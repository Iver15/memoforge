# Task parameters — client-readiness-reviewer

Role, output contract and rules: your agent definition plus
`{AGENT_CORE}/untrusted-content.md`, `{AGENT_CORE}/output-json.md`,
`{AGENT_CORE}/logging.md`, `{AGENT_CORE}/style-profile.md`.

First action (Bash, before any other tool call):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot client_readiness --state start`

## Parameters

- task_id: `memo-20260908T120000Z-prompt-golden`
- work_dir: `{WORK_DIR}`
- draft under review: `drafts/v1.md` (v`1`)
- `draft_sha` to put in your output: `0000000000000000000000000000000000000000000000000000000000000000`
- checklist (grade every id, no additions, no omissions): `{CHECKLISTS}\client-readiness.json`
- style profile (authoritative when set): `{PROSE_STYLE}`
- polish rounds still available: `1`
- already known blockers: none
- warnings the memo must disclose: - (none)
- currency notes of the sources the memo cites: none
- previous attempt errors to fix: none

`verdict` is `client_ready`, `needs_final_polish` or `manual_review_required`. Every issue carries a
`section_id`: the list is handed to the writer verbatim as the polish instructions.

An issue that changes the direction of a conclusion — "not required" to "required", "low" to
"medium", an obligation added or removed — names a source of the frozen source pack (its `source_id`,
as the draft's `[[src:]]` tokens give it) that supports the new conclusion. Without one, the issue may
only ask the writer to resolve a stated contradiction or to add the opposing argument, and the
direction stays the writer's call. Flagging a contradiction between the facts, the assumptions and
the draft's own conditions stays allowed.

A limitation moved into a section is stated as a limitation of the memo; it never turns into an
instruction to the client to delay a statutory step (a notification or a filing inside its deadline).
Raise no blocker for a limitation the memo already discloses and that changes no conclusion.
CRD-03 fails for a warning that touches a conclusion and that the memo does not disclose; the issue
names the warning. A note that names a later change to a provision the memo relies on is disclosed in
the section that relies on it; CRD-03 fails otherwise, and the issue names the source.

The memo itself is written in English. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in English saying what the client must check before relying on the memo.

## Open reviewer findings

none

These are the substantive majors the review loop left open on this memo, one per line:
`id · class · section · issue · suggestion`. When the list is `none`, write no `dispositions`.
A finding marked `blocker` after its class is a blocking `citations` finding the loop had no pass
left for; it is also printed in the memo's Status section until a clean citations re-check of the
polish lifts it.

The list is not a re-review: do not grade these findings again, decide what happens to each one.
Every finding of the review loop gets one row in `dispositions`:
`{"id": "om-<n>", "action": "polish" | "manual_review" | "leave", "note": "<one sentence why>"}`.

- A `citations` finding allows `polish` or `manual_review`. A `logic` or `counterarguments` finding
  allows `polish`, `manual_review` or `leave`. A missing row, or an action the finding's class does
  not allow, counts as `manual_review`.
- Decide by the repair you choose. Withdrawing, narrowing, qualifying or disclosing, with the words
  and sources already in the memo, is `polish`; when a suggestion offers such a repair among
  alternatives ("name the obligation … or narrow the claim"), choose it. A finding that no such
  repair answers — it needs a new reasoning step, an argument stated in its strong form and
  answered, or a rule the memo does not state — is `manual_review`, whatever its class; its `note`
  is the question the lawyer must answer, in one sentence. `leave` is for a finding that does not
  change what the client is told or does.
- `polish`, for any class, means one issue of yours in `issues[]` for that finding's section, telling
  the writer to withdraw or soften the flagged statement,
  with no new statement of law and no new authority; a polish issue may ask for an authority the
  memo already cites only in the finding's own section.
  For a CIT-04 finding (the pinpoint points elsewhere), removing the pinpoint from the token and
  keeping the source id is a softening. A polish needs the polish pass, so the verdict is then
  `needs_final_polish` unless something else makes it `manual_review_required`.
- A `blocker` finding allows `polish` or `manual_review`, like any `citations` finding. Its `polish`
  is one issue with `severity: blocker` on that finding's section: "withdraw or qualify the statement
  and every risk line or summary bullet that rests on it; no new norm, no new source".
  `severity: blocker` on an issue of yours is used only for a finding the list marks `blocker`.
- `manual_review` hands the finding to a lawyer, whatever its class: the memo is delivered under
  manual review and the finding is printed in its Status section.
  A `manual_review` disposition does not by itself make the verdict `manual_review_required`: when any
  open finding is `polish`, or any issue of yours can be fixed by a polish, the verdict is
  `needs_final_polish`, and the `manual_review` findings reach the Status section anyway.
- `leave` keeps a `logic` or `counterarguments` finding as it is: it is listed in `summary.md` and does
  not change the run's status.

After the polish, the list comes back with each finding's status appended (`resolved`, `unresolved`,
`manual_review`, `left`). Findings the citations re-check of the polish raised are listed
for information only: they get no row in `dispositions`.

## Write

- `steps/s-042/a1/client_readiness/final-client-readiness.json` (schema `client-readiness` — `{SCHEMAS}\client-readiness.schema.json`)

Last action (Bash, after the file is written):
`{MF} agent log --workdir {WORK_DIR} --step s-042 --attempt 1 --slot client_readiness --state done`
