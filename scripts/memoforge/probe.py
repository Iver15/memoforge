"""`mf probe dry-run` — the whole pipeline with fixture agents — and the §11 platform probes."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from . import (
    events,
    gates,
    machine,
    modes,
    phases,
    preflight,
    pylauncher,
    quotes,
    review,
    schema,
    sources,
    state_io,
    task,
)

# §0.2 G2, dry-run column: the modelled ceilings the run must stay under. The column describes the
# clean route (no lint-fix round, no extra revision iteration); `script` = 20 per D-63 leaves the
# clean run (≈10 after D-117 merged `draft finish` and folded `docx validate` into `docx render`,
# 11 since D-147 added `sources preflight` to `planning`) room for one lint-fix round (+1) and one
# more revision iteration (+4).
G2_LIMITS: dict[str, int] = {
    "next": 60,
    "report": 60,
    "agent": 25,
    "script": 20,
    "gate": 5,
}

G2_OBSERVED_LIMIT = 150
"""§0.2 G2 observable metric (`cli_call` + `step_issued`), estimated by the dry run (D-63)."""

G2_OBSERVED_KEY = "observed_estimate"
"""Name the observable estimate carries inside `g2_exceeded` when it breaks the cap."""

MAX_LOOP = 200

DONE_PHASE = "done"
"""The only final phase a dry run may end on; anything else is an error of D-52."""

PROBES: dict[str, dict] = {
    "P1": {
        "question": "Plugin workflow `/memoforge:<name>` in Cowork and CLI; is progress visible?",
        "method": (
            "Create `workflows/ping.js` with two phases in a scratch copy of the plugin outside the shipped "
            "tree (§0.4 keeps `workflows/` undeclared until P1 passes); run it in both environments."
        ),
        "affects": "§10 (Workflow pilot)",
    },
    "P2": {
        "question": "Exec-form hook with ${CLAUDE_PLUGIN_ROOT} and python/python3 on Windows (Cowork, CLI).",
        "method": (
            "`hooks/probe_echo.py` writes a line into plugin_data_dir; wire it into `hooks/hooks.json` by hand "
            "(three exec forms — snippet in the probe document) and trigger it in both hosts."
        ),
        "affects": "§8.1 (hooks.json shape)",
    },
    "P3": {
        "question": "Artifact from a plugin skill in Cowork; `write_db` from a background subagent; live UI update.",
        "method": "set `dashboard: on`, run Brief, confirm the page updates live in Cowork and CLI",
        "affects": "§7.5 (optional dashboard)",
    },
    "P4": {
        "question": "Does AskUserQuestion still work after ANY Agent dispatch in Cowork (silent fail?).",
        "method": "One dispatch + AUQ; then three parallel dispatches + AUQ.",
        "affects": "§2.4 (plan gate, intake migration)",
    },
    "P5": {
        "question": "Do SubagentStart/Stop fire for `memoforge:*` in Cowork, and what payload do they carry?",
        "method": (
            "`events.jsonl` keeps only agent_id/agent_type/result, so it cannot answer the payload half: "
            "wire `hooks/probe_echo.py` into `hooks.SubagentStop` by hand (P2-shaped snippet plus a raw-stdin "
            "dump — both in the probe document), dispatch one `memoforge:*` agent, read the dumped payload, "
            "then remove the wiring."
        ),
        "affects": "§7.2 layer C",
    },
    "P6": {
        "question": "Nested spawn from a plugin agent in Cowork.",
        "method": (
            "`Agent` is disallowed for every shipped agent, probe-echo included, and its body allows no call "
            "but the prescribed `Bash`: temporarily edit frontmatter and body of `agents/probe-echo.md` (drop "
            "`Agent` from `disallowedTools:`; replace the Bash-only instruction with one `Agent(Explore)` call "
            "reported in the same echo format), dispatch it, record whether the nested spawn ran, then restore "
            "the whole shipped file byte-for-byte."
        ),
        "affects": "reserve",
    },
    "P7": {
        "question": "Stop-hook `decision: block` — re-entry on Windows CLI and in Cowork.",
        "method": "`stop_guard.py` enabled via `userConfig.stop_guard`; interrupt a non-gate phase.",
        "affects": "§8.3 (stop guard)",
    },
    "P8": {
        "question": "Chat flush mid-turn in Cowork; is the Agent tile with `description` visible?",
        "method": "Three text lines interleaved with two Agent calls.",
        "affects": "§7.2 A/B, §7.4",
    },
    "P9": {
        "question": "Matcher semantics (^memoforge:, ^Agent$ vs ^Task$) and the effective tool pool of a plugin agent.",
        "method": (
            "Echo agent prints its own tool list. (a) no `tools:` + "
            "`disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*` — the shipped `agents/probe-echo.md`; "
            "(b) the same file with its frontmatter edited in place to `tools: Read, Write, Bash, mcp__*`, then "
            "restored. Run both in CLI and Cowork."
        ),
        "affects": "§4.1, §7.2 C, §8.1",
    },
    "P11": {
        "question": "Does the terminal-step copy to the connected folder work through the device tools?",
        "method": (
            "Finish a Brief run in Cowork with a folder connected to the session. `mf finalize` copies the "
            "result into `/mnt/user-data/outputs/memoforge/<slug>/` and the terminal `text` prints it as "
            "`Published:`; then follow the terminal step of `router.md` — copy that folder into "
            "`<connected folder>/memoforge/<slug>/` with the session's own device file tools (the folder "
            "appears to them as `$HOME/mnt/<folder>`). Record whether the tools exist at all, whether the "
            "copy arrived, and what the user sees on their own machine. In the CLI there are no device "
            "tools: the expected result there is that the step does nothing beyond printing the paths."
        ),
        "affects": "§2.5 (publish folder, D-109)",
    },
}


# --- fixture documents -----------------------------------------------------


def _meta(step_id: str, attempt: int, slot: str, task_id: str) -> dict:
    return {"task_id": task_id, "step_id": step_id, "attempt": int(attempt), "slot": slot}


def fixture_mcp_probe() -> dict:
    return {
        "namespaces": {
            "ldh": "mcp__plugin_memoforge_legal-data-hunter",
            "courtlistener": "mcp__plugin_memoforge_courtlistener",
            "legalviz": "mcp__plugin_memoforge_legalviz",
            "uklegal": "mcp__plugin_memoforge_uk-legal",
            "justicelibre": "mcp__plugin_memoforge_justicelibre",
            "opencaselaw": "mcp__plugin_memoforge_opencaselaw",
            "fedregs": "mcp__plugin_memoforge_federal-regulations",
            "lex": "mcp__plugin_memoforge_lex",
            "other": [],
        },
        # D-147: the smoke call the orchestrator makes per connected server.
        "status": {
            "ldh": "ok",
            "courtlistener": "ok",
            "legalviz": "ok",
            "uklegal": "ok",
            "justicelibre": "ok",
            "opencaselaw": "ok",
            "fedregs": "ok",
            "lex": "ok",
        },
        "probed_at": events.utc_now(),
    }


def fixture_plan() -> dict:
    return {
        "classification": "regulatory_analysis",
        "jurisdictions": ["EU"],
        "doctrine_required": True,
        "estimated_complexity": "high",
        "issues": [
            {
                "issue_id": "i1",
                "title": "Retention of customer records",
                "question": "How long may the client keep customer records?",
                "jurisdictions": ["EU"],
            }
        ],
        "notes": "fixture plan produced by `mf probe dry-run`",
    }


def fixture_questions() -> dict:
    def question(index: int, header: str) -> dict:
        return {
            "question": f"Fixture question {index}: which assumption applies?",
            "header": header,
            "options": [
                {"label": "Option A", "description": "The first documented assumption."},
                {"label": "Option B", "description": "The alternative documented assumption."},
            ],
            "impact": "high" if index == 1 else "medium",
            "default": "Option A applies.",
            "default_if_wrong": "The retention period would be shorter.",
            "confidence": "medium",
        }

    return {
        "must_answer": [question(1, "Scope"), question(2, "Data")],
        "optional": [question(3, "Sector")],
        "default_assumptions_if_skipped": ["The records contain personal data."],
    }


RAW_TEMPLATE = (
    "Article 5(1)(e) of the fixture instrument. Personal data shall be kept in a form which "
    "permits identification of data subjects for no longer than is necessary for the purposes "
    "for which the personal data are processed. Further retention is permitted only for "
    "archiving purposes in the public interest.\n"
)


def _register_sources(work_dir: Path, layer: str) -> list[str]:
    """Two sources per layer with saved raw text, exactly as a researcher would (§4.3)."""
    ids = []
    for index in (1, 2):
        raw = work_dir / "steps" / "_fixture" / f"{layer}-{index}.md"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(RAW_TEMPLATE, encoding="utf-8")
        result = sources.register_source(
            work_dir,
            layer=layer,
            title=f"Fixture {layer} source {index}",
            citation=f"Fixture {layer} instrument {index}, art 5",
            url="",
            tool="fixture",
            tier="critical" if index == 1 else "supporting",
            raw_file=str(raw),
            meta={"in_force": True},
            identifiers={},
            source_id=None,
        )
        ids.append(str(result["source_id"]))
    return ids


def fixture_findings(work_dir: Path, layer: str, meta: dict) -> dict:
    ids = _register_sources(work_dir, layer)
    return {
        "layer": layer,
        "issues": [
            {
                "issue_id": "i1",
                "findings": [
                    {
                        "source_id": ids[0],
                        "proposition": "Personal data may be kept only as long as the purpose requires.",
                        "pinpoint": "art 5(1)(e)",
                        "role": "rule",
                        "weight": "binding",
                        "confidence": "high",
                        "tier": "critical",
                        "quote_short": "kept no longer than is necessary",
                    },
                    {
                        "source_id": ids[1],
                        "proposition": "Archiving in the public interest is a narrow exception.",
                        "pinpoint": "art 5(1)(e)",
                        "role": "contrary",
                        "weight": "persuasive",
                        "confidence": "medium",
                        "tier": "supporting",
                        "quote_short": "archiving purposes in the public interest",
                        "contrary_point": "A statutory retention duty may extend the period.",
                    },
                ],
            }
        ],
        "considered_excluded": [],
        "methodology": {
            "queried_sources": ["fixture"],
            "jurisdictions": ["EU"],
            "date_of_search": events.utc_now()[:10],
        },
        "_meta": meta,
    }


def fixture_sufficiency() -> dict:
    return {
        "reviewer": "research_sufficiency",
        "overall_verdict": "sufficient",
        "blocking_gaps": [],
        "drafting_warnings": [],
    }


def fixture_currency(work_dir: Path) -> dict:
    registry = sources.read_registry(work_dir)
    rows = [
        {"source_id": source_id, "status": "current", "note": "fixture check"}
        for source_id in sorted(registry.get("sources") or {})
    ]
    return {
        "checked_at": events.utc_now()[:10],
        "sources": rows,
        "blocking": [],
        "warnings": [],
    }


def fixture_review(kind: str, draft_sha: str, iteration: int) -> dict:
    checklist = review.load_checklist(kind)
    return {
        "reasoning": "Fixture grader: every checklist item is satisfied by the fixture draft.",
        "reviewer": review.expected_reviewer(kind),
        "draft_sha": draft_sha,
        "iteration": iteration,
        "checklist": [
            {"id": item["id"], "pass": True, "evidence": "Fixture evidence for the dry run."}
            for item in checklist
        ],
        "issues": [],
        "verdict": "approved",
    }


def fixture_client_readiness(draft_sha: str, version: int) -> dict:
    checklist = review.load_checklist("client-readiness")
    return {
        "reasoning": "Fixture delivery review: the draft is ready to send.",
        "reviewer": "client_readiness",
        "draft_sha": draft_sha,
        "version_reviewed": version,
        "checklist": [
            {"id": item["id"], "pass": True, "evidence": "Fixture evidence for the dry run."}
            for item in checklist
        ],
        "verdict": "client_ready",
        "issues": [],
    }


def fixture_mediator(iteration: int) -> dict:
    return {
        "iteration": iteration,
        "instructions": [
            {
                "section_id": "s-4",
                "source_reviewer": "logic",
                "category": "fixture",
                "severity": "major",
                "instruction": "Tighten the rule explanation in this subsection.",
            }
        ],
        "dropped": [],
    }


# --- fixture draft ---------------------------------------------------------

QUOTE_FRAGMENT = (
    "Personal data shall be kept in a form which permits identification of data subjects for no "
    "longer than is necessary for the purposes for which the personal data are processed."
)


def fixture_draft(work_dir: Path, state: dict, version: int, existing: str | None) -> str:
    """A classical-memo / executive-brief draft that satisfies the L-rules of §5.4."""
    if existing:
        # A later version is a targeted edit: change one sentence so the sha differs from the seed.
        marker = "The client keeps records for seven years, which exceeds that period."
        replacement = (
            f"The client keeps records for seven years, which exceeds that period (v{version} review)."
        )
        if marker in existing:
            return existing.replace(marker, replacement, 1)
        return existing.replace(
            "<!-- sources: generated -->", f"Reviewed in v{version}.\n\n<!-- sources: generated -->"
        )

    registry = sources.read_registry(work_dir)
    critical = sorted(
        source_id
        for source_id, record in (registry.get("sources") or {}).items()
        if record.get("raw_path")
    )
    primary = critical[0] if critical else "unknown"
    quote = quotes.extract_quote(work_dir, primary, QUOTE_FRAGMENT, max_words=40)
    quote_id = quote.get("quote_id")
    quote_line = f"> [[q:{quote_id}]] {quote.get('text')}" if quote_id else None
    if quote_id is None:
        quotes.record_skip(work_dir, "s-4", primary, "not_found")

    template = str((state.get("config") or {}).get("template_id") or "classical-memo")
    brief = template == "executive-brief"
    lines = [
        "# Retention of customer records: analytical framing",
        "",
        "Date: " + events.utc_now()[:10] + ". Jurisdiction: EU. Template: " + template + ".",
        "",
        "The client asked how long customer records may be kept. This memo answers that question.",
        "",
    ]
    if not brief:
        lines += [
            "## 1. Executive summary",
            "",
            "- Records may not be kept beyond the purpose that justified them. Risk: medium.",
            "",
            "## 2. Facts, assumptions and limitations",
            "",
            "The client keeps customer records for seven years. We assume the records hold personal "
            "data. The memo is limited to that assumption.",
            "",
        ]
    lines += [
        "## 3. Retention of customer records",
        "",
        f"Records may not be kept beyond their purpose [[src:{primary} art 5(1)(e)]].",
        "",
    ]
    if quote_line:
        lines += [quote_line, ""]
    lines += [
        "The provision requires deletion once the purpose ends. The client keeps records for seven "
        "years, which exceeds that period. A contrary reading is that tax law compels the longer "
        "period, and that reading fails here because the tax duty covers other data.",
        "",
        "Risk: medium. The exposure turns on the tax carve-out. Counsel must confirm the tax basis "
        "before the next audit.",
        "",
        "## 4. " + ("Recommendations" if brief else "Conclusion and recommendations"),
        "",
        "- Confirm the tax basis for the seven-year period before the next audit; owner: counsel.",
        "",
        "Assumptions in this memo were applied without confirmation and a disclaimer therefore "
        "applies: the retention conclusion is not confirmed by the client.",
        "",
        "<!-- sources: generated -->",
        "",
    ]
    return "\n".join(lines)


# --- fixture agents --------------------------------------------------------


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state_io.write_json_atomic(path, document)


def run_fixture_agent(work_dir: Path, state: dict, step: dict, agent: dict) -> None:
    """Produce the outputs one agent would produce, in its attempt workspace."""
    name = str(agent["subagent_type"]).split(":", 1)[-1]
    slot = str(agent["slot"])
    step_id = str(step["step_id"])
    attempt = int(step["attempt"])
    meta = _meta(step_id, attempt, slot, str(state.get("task_id")))
    outputs = {Path(entry["canonical"]).name: work_dir / entry["work_path"] for entry in agent["expected_outputs"]}

    if name == "fact-assumption-analyst":
        _write_json(outputs["questions.json"], fixture_questions())
        _write_json(
            outputs["preliminary-sources.json"],
            {"layer": "preliminary", "issues": [], "_meta": meta},
        )
    elif name == "legal-researcher":
        _write_json(outputs[f"{slot}.json"], fixture_findings(work_dir, slot, meta))
    elif name == "research-sufficiency-reviewer":
        _write_json(outputs["research-sufficiency.json"], fixture_sufficiency())
    elif name == "currency-checker":
        _write_json(outputs["currency.json"], fixture_currency(work_dir))
    elif name == "memo-writer":
        target = next(iter(outputs.values()))
        existing = target.read_text(encoding="utf-8-sig") if target.is_file() else None
        text = fixture_draft(work_dir, state, int(state.get("current_iteration") or 1), existing)
        target.parent.mkdir(parents=True, exist_ok=True)
        state_io.write_bytes_atomic(target, text.encode("utf-8"))
    elif name == "revision-mediator":
        iteration = int(state.get("current_iteration") or 1)
        _write_json(next(iter(outputs.values())), fixture_mediator(iteration))
    elif name == "client-readiness-reviewer":
        version = max(int(state.get("current_iteration") or 1), 1)
        _write_json(
            outputs["final-client-readiness.json"],
            fixture_client_readiness(str(state.get("current_draft_sha") or ""), version),
        )
    else:
        kind = slot if slot in review.REVIEWER_KINDS else "logic"
        iteration = max(int(state.get("current_iteration") or 1), 1)
        _write_json(
            next(iter(outputs.values())),
            fixture_review(kind, str(state.get("current_draft_sha") or ""), iteration),
        )

    machine.run_agent_log(
        argparse.Namespace(
            workdir=str(work_dir),
            step=step_id,
            attempt=attempt,
            slot=slot,
            state="done",
            detail=None,
            mcp=None,
            human=False,
        )
    )


GATE_REPLIES: dict[str, str] = {
    "intake_questions_pending": "1A 2B",
    "research_sufficiency_followup_pending": "proceed",
    "research_insufficient_pending": "continue",
    "source_review_pending": "continue",
    "plan_approval_pending": "approve",
}


# --- the dry run -----------------------------------------------------------


def _new_task(root: Path, mode: str) -> Path:
    work_dir = root / f"memo-20260908T120000Z-dry-run-{mode}"
    task.create_work_dir_tree(work_dir)
    state = task.build_initial_state(
        task_id=work_dir.name,
        user_query="How long may the client keep customer records?",
        language="en",
        work_dir=work_dir,
        output_folder=root,
        config=modes.resolve_config(None, {}),
    )
    state_io.create_state(work_dir, state)
    return work_dir


def _namespace(**kwargs) -> argparse.Namespace:
    kwargs.setdefault("human", False)
    return argparse.Namespace(**kwargs)


def _check_published(work_dir: Path, state: dict) -> list[str]:
    """Every `published[]` row still matches the canonical file it points at (§2.2)."""
    errors = []
    for row in state.get("published") or []:
        path = work_dir / str(row.get("canonical_path"))
        if not path.is_file():
            errors.append(f"published_missing: {row.get('canonical_path')}")
            continue
        if state_io.sha256_file(path) != row.get("sha256"):
            errors.append(f"published_drift: {row.get('canonical_path')}")
    return errors


def dry_run(work_dir: Path, mode: str) -> dict:
    """`task new` -> next/act/report with fixture agents -> `done`; counts the G2 metrics.

    Anything short of `done`, a broken invariant or an exceeded ceiling also fills `errors[]`, which
    is what turns `ok: false` into exit 1 for CI (D-52).

    D-147: the fixture route reaches a real script step that would otherwise open sockets
    (`mf sources preflight`), so the whole loop runs under `MEMOFORGE_OFFLINE` — a dry run answers
    the G2 question, not the «is EUR-Lex up» one.
    """
    previous_offline = os.environ.get(preflight.OFFLINE_ENV)
    os.environ[preflight.OFFLINE_ENV] = "1"
    try:
        return _dry_run(work_dir, mode)
    finally:
        if previous_offline is None:
            os.environ.pop(preflight.OFFLINE_ENV, None)
        else:
            os.environ[preflight.OFFLINE_ENV] = previous_offline


def _dry_run(work_dir: Path, mode: str) -> dict:
    """The loop itself; `dry_run` owns the offline switch around it (D-147)."""
    counts = {"next": 0, "report": 0, "agent": 0, "script": 0, "gate": 0, "inline": 0}
    trace: list[dict] = []
    invariants: list[str] = []
    plan_answers = json.dumps({"Plan": "Approve", "Mode": mode.capitalize()})

    for _ in range(MAX_LOOP):
        action = machine.run_next(_namespace(workdir=str(work_dir)))
        counts["next"] += 1
        if action.get("errors"):
            invariants.append(f"next_error: {action['errors']}")
            break
        kind = action.get("kind")
        trace.append({"step_id": action.get("step_id"), "kind": kind, "phase": action.get("phase")})
        state = state_io.read_state(work_dir)
        invariants.extend(f"state_invalid: {message}" for message in schema.validate(state, "state"))
        invariants.extend(_check_published(work_dir, state))
        if kind in ("gate-text", "gate-auq") and not phases.is_gate(str(action.get("phase"))):
            invariants.append(f"gate_inside_segment: {action.get('phase')}")

        if kind == "terminal":
            break
        if kind == "script":
            counts["script"] += 1
            machine.run_command(list(action["command"]))
            continue
        if kind == "inline-llm":
            counts["inline"] += 1
            target = work_dir / action["write_to"]
            document = fixture_plan() if target.name == "plan.json" else fixture_mcp_probe()
            _write_json(target, document)
            machine.run_report(
                _namespace(
                    workdir=str(work_dir),
                    step=action["step_id"],
                    attempt=action["attempt"],
                    agent=None,
                    status="ok",
                    answers=None,
                    generation=None,
                    stdout=None,
                )
            )
            counts["report"] += 1
            continue
        if kind == "gate-auq":
            counts["gate"] += 1
            machine.run_report(
                _namespace(
                    workdir=str(work_dir),
                    step=action["step_id"],
                    attempt=action["attempt"],
                    agent=None,
                    status="ok",
                    answers=plan_answers,
                    generation=action.get("generation", 0),
                    stdout=None,
                )
            )
            counts["report"] += 1
            continue
        if kind == "gate-text":
            counts["gate"] += 1
            reply = GATE_REPLIES.get(str(action.get("phase")), "continue")
            gates.run_parse(
                _namespace(
                    workdir=str(work_dir),
                    gate=None,
                    text=reply,
                    step=action["step_id"],
                    attempt=action["attempt"],
                    generation=action.get("generation", 0),
                )
            )
            continue
        if kind == "dispatch":
            step = {"step_id": action["step_id"], "attempt": action["attempt"]}
            for agent in action["agents"]:
                counts["agent"] += 1
                run_fixture_agent(work_dir, state_io.read_state(work_dir), step, agent)
                machine.run_report(
                    _namespace(
                        workdir=str(work_dir),
                        step=action["step_id"],
                        attempt=action["attempt"],
                        agent=agent["slot"],
                        status="ok",
                        answers=None,
                        generation=None,
                        stdout=None,
                    )
                )
                counts["report"] += 1
            continue
        invariants.append(f"unknown_kind: {kind}")
        break

    state = state_io.read_state(work_dir)
    over = {name: counts[name] for name, cap in G2_LIMITS.items() if counts.get(name, 0) > cap}
    observed = observed_estimate(work_dir, counts)
    if observed["total"] > G2_OBSERVED_LIMIT:
        over[G2_OBSERVED_KEY] = observed["total"]
    final_phase = state.get("current_phase")
    errors = dry_run_errors(final_phase, over, invariants)
    result = {
        "mode": mode,
        "work_dir": str(work_dir),
        "final_phase": final_phase,
        "final_status": state.get("final_status"),
        "counts": counts,
        "g2_limits": dict(G2_LIMITS),
        "g2_exceeded": over,
        "g2_observed_estimate": observed,
        "invariants": invariants,
        "steps": len({row.get("step_id") for row in state.get("steps") or []}),
        "trace": trace,
        "ok": not errors,
    }
    if errors:
        result["errors"] = errors
    return result


def observed_estimate(work_dir: Path, counts: dict) -> dict:
    """Estimate of the §0.2 G2 observable metric (`cli_call` + `step_issued`) for a dry run (D-63).

    An estimate, not a measurement: the dry run calls `args.func` in process, so `cli.main` — the
    only writer of `cli_call` (D-43) — never runs and the journal holds zero of those events. Each
    counted `next`/`report`/`script`/`gate` is one `mf` call on a real route and stands in for it;
    `step_issued` (agent dispatches, script and inline steps) is read from the journal as it is.
    """
    issued = sum(1 for record in events.read_events(work_dir) if record.get("event") == "step_issued")
    calls = sum(int(counts.get(name, 0)) for name in ("next", "report", "script", "gate"))
    return {
        "total": calls + issued,
        "limit": G2_OBSERVED_LIMIT,
        "cli_call_estimate": calls,
        "step_issued": issued,
    }


def g2_cap(name: str) -> int | None:
    """The §0.2 ceiling behind one `g2_exceeded` key — a counter name or the observable estimate."""
    if name == G2_OBSERVED_KEY:
        return G2_OBSERVED_LIMIT
    return G2_LIMITS.get(name)


def dry_run_errors(final_phase, g2_exceeded: dict, invariants: list) -> list[str]:
    """D-52: every reason a dry run is not `ok`, as the `errors[]` that make `cli.main` exit 1 (§9).

    `ok: false` alone is invisible to CI, which reads the exit code; a failed invariant, a run that
    stopped short of `done` and a broken G2 ceiling therefore each become an error string.
    """
    errors: list[str] = []
    if final_phase != DONE_PHASE:
        errors.append(f"final_phase: {final_phase}")
    for name in sorted(g2_exceeded or {}):
        errors.append(f"g2_exceeded: {name}={g2_exceeded[name]} > {g2_cap(name)}")
    errors.extend(f"invariant: {row}" for row in invariants or [])
    return errors


def run_dry_run(args: argparse.Namespace) -> dict:
    """`mf probe dry-run --mode full|brief [--workdir W]` (§9 «Сквозной dry-run»)."""
    mode = str(args.mode).lower()
    if mode not in modes.MODES:
        return {"errors": [f"unknown_mode: {args.mode!r}"]}
    if args.workdir:
        root = Path(args.workdir)
        root.mkdir(parents=True, exist_ok=True)
        return dry_run(_new_task(root, mode), mode)
    with tempfile.TemporaryDirectory(prefix="mf-dry-run-") as tmp:
        return dry_run(_new_task(Path(tmp), mode), mode)


def run_probe(args: argparse.Namespace) -> dict:
    """`mf probe <name>` — print the §11 procedure; the probe itself is run by a human."""
    name = str(args.name).upper()
    row = PROBES.get(name)
    if row is None:
        return {"errors": [f"unknown_probe: {args.name!r}"], "known": sorted(PROBES)}
    text = (
        f"{name} — {row['question']}\n"
        f"Method: {row['method']}\n"
        f"Affects: {row['affects']}\n"
        "Pauses of 25 s between observations (docs/attic/v0.5.0-probe-procedure.md).\n"
        f"Record the outcome in {pylauncher.plugin_root() / 'docs' / 'probes' / 'v2-probes.md'}."
    )
    return {"probe": name, **row, "text": text, "human": text, "executed": False}


def register(subparsers) -> None:
    """Register `mf probe dry-run` and `mf probe <name>` (the `probe` group is shared with analyze.py)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "probe", "platform probes and metrics (§11, §0.2)")

    dry = group.add_parser("dry-run", help="run the whole pipeline with fixture agents")
    dry.add_argument("--mode", required=True, choices=sorted(modes.MODES))
    dry.add_argument("--workdir", default=None, help="keep the run in this folder instead of a temp dir")
    dry.add_argument("--seed", type=int, default=0, help="accepted for reproducibility; fixtures are fixed")
    dry.set_defaults(func=run_dry_run)

    for name in sorted(PROBES):
        parser = group.add_parser(name, aliases=[name.lower()], help=PROBES[name]["question"])
        parser.set_defaults(func=run_probe, name=name)
