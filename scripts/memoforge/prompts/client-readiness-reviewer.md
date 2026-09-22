# Task parameters — client-readiness-reviewer

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/output-json.md`,
`${paths_agent_core}/logging.md`, `${paths_agent_core}/style-profile.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- draft under review: `${draft_path}` (v`${draft_version}`)
- `draft_sha` to put in your output: `${draft_sha}`
- checklist (grade every id, no additions, no omissions): `${paths_checklist}`
- style profile (authoritative when set): `${prose_style_path}`
- polish rounds still available: `${polish_budget}`
- already known blockers: ${known_blockers}
- previous attempt errors to fix: ${retry_errors}

`verdict` is `client_ready`, `needs_final_polish` or `manual_review_required`. Every issue carries a
`section_id`: the list is handed to the writer verbatim as the polish instructions.

The memo itself is written in ${memo_language_name}. Your findings stay in English: `issue`,
`suggestion` and `reasoning` are always English, whatever the memo language. When the memo
language above is not English, a finding with `severity: blocker` also carries `issue_client` —
one sentence in ${memo_language_name} saying what the client must check before relying on the memo.

## Open reviewer findings

${open_findings}

These are the substantive majors the review loop left open on this memo, one per line:
`id · class · section · issue · suggestion`. When the list is `none`, write no `dispositions`.

The list is not a re-review: do not grade these findings again, decide what happens to each one.
Every finding of the review loop gets one row in `dispositions`:
`{"id": "om-<n>", "action": "polish" | "manual_review" | "leave", "note": "<one sentence why>"}`.

- A `citations` finding allows `polish` or `manual_review`. A `logic` or `counterarguments` finding
  allows `polish` or `leave`. A missing row, or an action the finding's class does not allow,
  counts as `manual_review` for `citations` and `leave` for the others.
- `polish`, for any class, means one issue of yours in `issues[]` for that finding's section, telling
  the writer to withdraw or soften the flagged statement,
  with no new statement of law and no new authority.
  For a CIT-04 finding (the pinpoint points elsewhere), removing the pinpoint from the token and
  keeping the source id is a softening. A polish needs the polish pass, so the verdict is then
  `needs_final_polish` unless something else makes it `manual_review_required`.
- `manual_review` hands a `citations` finding to a lawyer: the memo is delivered under manual review
  and the finding is printed in its Status section.
- `leave` keeps a `logic` or `counterarguments` finding as it is: it is listed in `summary.md` and does
  not change the run's status.

After the polish, the list comes back with each finding's status appended (`resolved`, `unresolved`,
`manual_review`, `left`). Findings the citations re-check of the polish raised are listed
for information only: they get no row in `dispositions`.

## Write

${outputs}

Last action (Bash, after the file is written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
