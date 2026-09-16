# Revision loop — methodology

Why the loop is shaped the way it is. The loop itself is executed by the CLI: `mf draft lint`, `mf draft audit-citations`, `mf review aggregate` and `mf revision next` own the counters, the aggregation and the exit decision. The branch order is specified in the v2 spec §4.5 and implemented in `scripts/memoforge/revision.py` — read the code, not a copy of it here.

## Deterministic before judgement

A draft reaches the reviewers only after `mf draft lint` and `mf draft audit-citations` are clean, or after the fix rounds for them are spent. Findings that a script can produce — caps, heading levels, missing tokens, quotes that do not match their source, sources outside the frozen pack — are never a reviewer's job, and a reviewer's approval does not clear them: `mf review aggregate` folds the current `lint.json` and `citations.json` into the blocker set with source `deterministic`.

## Graders, not personas

Each reviewer is an isolated grader working from one binary checklist in `lib/checklists/`:

| Reviewer | Checklist | Sees | Does not see |
|---|---|---|---|
| `logic-reviewer` | `logic.json` | the draft | research, reviews, state, prior iterations |
| `form-reviewer` (Full only) | `form.json` | the draft, the style profile | research, reviews, state |
| `citation-auditor` | `citations.json` | the draft, `citations.json`, the source pack, claim-to-finding pairs | prior reviews, the changelog |
| `counterargument-reviewer` | `counterarguments.json` | the draft, the source pack, research findings, intake | prior reviews, the changelog |
| `client-readiness-reviewer` (phase 14) | `client-readiness.json` | the draft, drafting warnings, run status | prior reviews |

What isolation buys: no reviewer sees another's findings, its own previous verdict, or any label saying the draft is final or polished. The writer sees consolidated instructions, never the raw reviews.

Checklist items are yes/no and each answer carries evidence — a quote or a section id. `unknown` is a legitimate answer, but on a `hard_fail` item it is incompatible with approval and the validator lowers the verdict. Item order is shuffled deterministically per task to blunt position bias. `approved` is a normal outcome: a grader that must find something is a grader that invents something.

## One second iteration, at most

Almost all of the gain is in the first pass; later passes drift and over-edit. So both modes allow two iterations (D-115), and the second happens only when a grounded blocker exists — deterministic, a citation finding, or a failed `hard_fail` item on substance. Blockers with no evidence behind them do not buy an iteration; the run exits early and says so in a banner.

## Substance before form

`substance` items (logic, citations, counterarguments, and the deterministic blockers) gate the exit. `form` items do not: a draft with form blockers and no substance blockers leaves the loop with a banner rather than another round. The mediator consolidates under the same priority, never dropping one substance finding in favour of another — both go into the instructions, with a resolution note when they conflict.

## Failure is not approval

A reviewer that produces nothing valid is re-dispatched once. If it fails again, the run does not treat the missing verdict as consent: the iteration is recorded as incomplete coverage and the task carries `manual_review_required` into export. The same holds when every reviewer fails.
