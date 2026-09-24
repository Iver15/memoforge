# Architecture decisions

Short records of the choices that shape memoforge v2. Each one names the section of
[`TZ-memoforge-v2.md`](TZ-memoforge-v2.md) that specifies it; the spec is the oracle, this file is
the reason. Implementation-level Q&A lives in [`dev/IMPL-DECISIONS.md`](dev/IMPL-DECISIONS.md).

---

## ADR-01 — Control flow lives in code, judgement lives in the leaves

**Status:** accepted (v2.0-dev) · **Spec:** §1, §3, §13 · **Invariants:** M1, M3

In v1 the orchestrating LLM read ~95–110k tokens of pipeline instructions per Full run and still
dropped steps: 9 of 12 recorded incidents were a missed bookkeeping step, and the answer each time
was more prose, which context summarisation then compressed away. v2 moves phase transitions,
counters, retry budgets, agent selection and every state write into `scripts/memoforge/` and leaves
the orchestrator a three-command protocol (`next → act → report`, ~150 lines). Agents keep the
judgement calls. Consequence: recovery after summarisation is one `next` call, but the protocol
prompt itself is now the single remaining prose dependency — kept small and idempotent on purpose.

## ADR-02 — A Python package, not a Workflow script (pilot behind a flag)

**Status:** accepted (v2.0-dev) · **Spec:** §3.2, §5, §10

The Workflow tool expresses "the script holds control flow" natively, but it is not confirmed to
run from a plugin in every target environment, and rewriting the pipeline onto it would couple v2
to an unproven host feature. v2 therefore ships a plain Python package driven by a router skill,
while keeping segment boundaries (S1–S4) shaped so a workflow script can own a segment unchanged.
`workflows/` stays undeclared until probe P1 passes; the pilot then covers S3a and S4 only.
Consequence: portability and testability now, at the cost of the orchestrator still being an LLM.

## ADR-03 — Native progress signals, not a live artifact dashboard

**Status:** accepted (v2.0-dev) · **Spec:** §7.1, §7.2, §7.5 · **Invariant:** M7

Cowork Live artifacts were switched off on 2026-08-19, which killed the whole v1 progress stack —
the HTML renderer, the artifact-update calls, the widget MCP server, the 15 "Live progress"
sections in agent prompts. Rather than rebuild it, v2 uses what the runtime already emits: the
`Agent` call `description` (`P<n>/<N> · agent · label`), one chat line per step, hooks appending to
`events.jsonl`, a `subagentStatusLine`, and `mf events analyze` after the fact. The orchestrator's
obligatory progress work drops from 10–12 actions per step to one printed line (G8). An optional
`Artifact` dashboard is implemented on the built-in tool and its page `db` capability (§7.5, D-87) —
one publish, then one `write_db` per step — but stays off by default, gated on probe P3.

## ADR-04 — Binary checklists with deterministic aggregation, not holistic scores

**Status:** accepted (v2.0-dev) · **Spec:** §4.5, §13 · **Invariant:** M10

v1 asked five persona reviewers for a holistic `overall_score` and exited on a Δ<1.0 plateau —
inside the ≈13.6% flip rate of an LLM judge, so the loop thrashed and a failure stub scoring 0
could force a false regression revert. v2 gives each reviewer a fixed list of binary checklist
items (`LOG-`, `FRM-`, `CIT-`, `CTR-`, `CRD-`) validated by `mf review validate`, aggregates them
in code, and runs lint and the citation audit *before* any reviewer sees the draft. Iterations drop
to ≤2, the second one only for grounded blockers. Consequence: less nuance in
the signal, far less variance in the verdict.

## ADR-05 — python-docx plus raw OXML, not a JavaScript docx library

**Status:** accepted (v2.0-dev) · **Spec:** §5.5

The deliverable needs real Word footnotes (`footnotes.xml`, `w:footnoteReference`), OSCOLA short
forms and list-numbering restarts — none of which python-docx exposes, and all of which are a few
dozen lines of OXML on top of it. Adding a Node toolchain for docx-js would put a second runtime on
the critical path of a Python plugin that must work on a bare Windows box. v2 renders a `mistune`
AST through python-docx helpers, drops down to OXML for footnotes, and verifies the result with
`mf docx validate` plus golden tests over `document.xml`. Consequence: a stdlib-only markdown
fallback is mandatory, because a missing `python-docx` must never cost the user a deliverable (M9).

## ADR-06 — System advisory locks, not a bespoke lock protocol

**Status:** accepted (v2.0-dev) · **Spec:** §2.2, §7.2 · **Invariants:** M2, M6

Parallel agents and at-least-once hooks write to the same task folder, so `state.json`,
`sources.json` and `events.jsonl` each need mutual exclusion that survives a killed process. v2
uses the OS primitives — `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, over a permanent
lock file — with a fixed acquisition order (`state → sources → events`) checked at runtime, and
writes through a temp file plus `os.replace`. A lock-file-as-marker scheme was rejected: it leaks
on crash and needs a stale-lock heuristic nobody can test. Consequence: no cross-machine locking
(a network share with two hosts is out of scope) and one platform branch to maintain.

## ADR-07 — Lessons Studio stays removed, now unblocked

**Status:** deferred (v2.0-dev) · **Spec:** §0.3 · **Invariant:** M1

Cross-run learning was removed in v1.0.0 because every safeguard keeping it alive was prose in
`SKILL.md`, and all three failed at once on a four-hour run. The v1 changelog set the condition for
its return: "any future re-introduction needs a non-prompt-resident enforcement mechanism." v2
builds exactly that — a code-owned state machine, `events.jsonl`, `mf finalize` as a guaranteed
terminal step, and Stop/SessionStart hooks — so the blocker is gone. It is still out of scope for
v2.0 by the minimalism rule of §0.3a: nothing ships that does not close a recorded incident or a
G-metric. Revisit once G1–G10 are measured on real runs.

## ADR-08 — The package is five times the §0.3a estimate, and that is accepted

**Status:** accepted (v2.0-dev) · **Spec:** §0.3a · **Decision:** D-80

§0.3a estimated ≈3–3.5k lines of Python plus ≈2.5k lines of tests; the build came in at ≈18k and
≈17.6k. The gap is the estimate being wrong, not scope creep: the same spec mandates fifteen phase
planners, fourteen lint rules, an OSCOLA renderer that writes real `footnotes.xml`, the source
freeze protocol, step identity and replay, nineteen schemas, a dry-run harness and eight revision
branches — each of them small on its own and none of them optional. The excess is therefore accepted
as written rather than cut under time pressure, and no further reduction is planned before 2.0.0.
Consequence: the minimalism rule of §0.3a still governs new mechanisms, and the candidates for a
post-release trim are named in the 17a review (`mf sources slice`, unread `config.*` passthrough).
