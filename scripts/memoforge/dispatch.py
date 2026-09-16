"""Dispatch plans: agent list, models, `description` and the rendered prompts (ТЗ §3.3, §4.1, §7.2 A)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from string import Template

from . import pylauncher, routing, schema, state_io

SUBAGENT_PREFIX = "memoforge:"
"""Plugin-qualified subagent type of every `Agent` call issued by `next` (§3.1)."""

AGENT_MODELS: dict[str, dict] = {
    # Duplicated literally from `lib/models.md`; test_dispatch.py asserts the two agree (§4.1, M11).
    "fact-assumption-analyst": {"model": "opus", "effort": "high"},
    "legal-researcher": {"model": "sonnet", "effort": "high"},
    "research-sufficiency-reviewer": {"model": "opus", "effort": "high"},
    "currency-checker": {"model": "sonnet", "effort": "medium"},
    "memo-writer": {"model": "opus", "effort": "high"},
    "logic-reviewer": {"model": "opus", "effort": "high"},
    "form-reviewer": {"model": "sonnet", "effort": "medium"},
    "citation-auditor": {"model": "opus", "effort": "high"},
    "counterargument-reviewer": {"model": "opus", "effort": "high"},
    "revision-mediator": {"model": "sonnet", "effort": "medium"},
    "client-readiness-reviewer": {"model": "sonnet", "effort": "medium"},
    "style-extractor": {"model": "opus", "effort": "high"},
}

REVIEWER_AGENTS: dict[str, str] = {
    "logic": "logic-reviewer",
    "form": "form-reviewer",
    "citations": "citation-auditor",
    "counterarguments": "counterargument-reviewer",
}
"""`config.reviewer_list` kind -> agent name (§2.3, §4.5)."""

PIPELINE_AGENTS: tuple[str, ...] = (
    "fact-assumption-analyst",
    "legal-researcher",
    "research-sufficiency-reviewer",
    "currency-checker",
    "memo-writer",
    "logic-reviewer",
    "form-reviewer",
    "citation-auditor",
    "counterargument-reviewer",
    "revision-mediator",
    "client-readiness-reviewer",
)
"""Agents that have a dispatch prompt in `prompts/` (style-extractor runs outside the pipeline)."""


# --- paths ----------------------------------------------------------------


def prompts_dir() -> Path:
    """`scripts/memoforge/prompts` — the `string.Template` sources of every dispatch prompt (§3.3)."""
    return Path(__file__).resolve().parent / "prompts"


def prompt_path(agent: str) -> Path:
    """Template file of one agent."""
    return prompts_dir() / f"{agent}.md"


def mf_path() -> str:
    """Absolute path of the `mf` wrapper (`mf.cmd` on Windows); substituted into every prompt (§3.1)."""
    root = pylauncher.plugin_root()
    name = "mf.cmd" if os.name == "nt" else "mf"
    return str(root / "scripts" / name)


def lib_path(*parts: str) -> str:
    """Absolute path inside the plugin's `lib/` (prompts never contain relative paths, §3.3)."""
    return str(pylauncher.plugin_root().joinpath(*parts))


# --- description (§7.2 A) --------------------------------------------------


def description(position: int, total: int, agent: str, label: str) -> str:
    """`P<n>/<N> · <agent> · <label>` — the only progress action of the orchestrator (§3.1 rule 4)."""
    return f"P{int(position)}/{int(total)} · {agent} · {label}"


# --- specs ----------------------------------------------------------------


def output(canonical: str, schema: str | None, step_id: str, attempt: int, slot: str) -> dict:
    """One `expected_outputs[]` entry: canonical path, attempt work path and schema (§3.1)."""
    basename = canonical.rsplit("/", 1)[-1]
    return {
        "canonical": canonical,
        "work_path": f"steps/{step_id}/a{int(attempt)}/{slot}/{basename}",
        "schema": schema,
    }


def spec(
    slot: str,
    agent: str,
    label: str,
    outputs: list[tuple[str, str | None]],
    *,
    inputs: list[str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    **extra: object,
) -> dict:
    """Describe one agent slot of a dispatch step before the step id and attempt are known."""
    return {
        "slot": slot,
        "agent": agent,
        "label": label,
        "outputs": list(outputs),
        "inputs": list(inputs or []),
        "model": model,
        "effort": effort,
        "extra": dict(extra),
    }


# --- prompt context (§3.3: flat `${...}` names, `substitute` — never `safe_substitute`) ---


def layer_rules_line(layer: str) -> str:
    """The single §4.3 table row of one layer — never the whole table (§3.3)."""
    row = routing.layer_rules(layer)
    primary = "yes" if row["websearch_primary"] else "no"
    return (
        f"{layer} — WebSearch as a primary tool: {primary} ({row['websearch_note']}); "
        f"citable: {row['citable']}"
    )


def mcp_budget_share(config: dict, layers: list[str] | tuple[str, ...]) -> str:
    """This researcher's share of `config.mcp_budget` (§4.3: «в промпт подставляется его доля»)."""
    budget = dict((config or {}).get("mcp_budget") or {})
    count = max(len(list(layers)), 1)
    if not budget:
        return "not budgeted"
    return ", ".join(f"{name} {int(value) // count}" for name, value in sorted(budget.items()))


def _template_path(config: dict) -> str:
    explicit = (config or {}).get("template_path")
    if explicit:
        return str(explicit)
    return lib_path("templates", f"{(config or {}).get('template_id') or 'classical-memo'}.md")


def outputs_block(outputs: list[dict]) -> str:
    """`${outputs}` — work path, schema name and the absolute schema file (§4.2 «schema + пример», D-79)."""
    if not outputs:
        return "- (no file outputs)"
    lines = []
    for row in outputs:
        name = row.get("schema")
        if not name:
            lines.append(f"- `{row['work_path']}`")
            continue
        path = lib_path("schemas", f"{name}{schema.SCHEMA_SUFFIX}")
        lines.append(f"- `{row['work_path']}` (schema `{name}` — `{path}`)")
    return "\n".join(lines)


def _prose_style_path(config: dict) -> str:
    explicit = (config or {}).get("prose_style_path")
    return str(explicit) if explicit else lib_path("lib", "prose-style.md")


WARNING_ORDER: tuple[str, ...] = (
    # D34-17: severity of `${drafting_warnings}` — research gaps first, run notes last. A code
    # missing from this table sorts after every known one, in the order state records it.
    "unresolved_research_gap",
    "out_of_scope_research_gap",
    "research_layer_missing",
    "sufficiency_warning",
    "sufficiency_unavailable",
    "insufficient_research_accepted",
    "currency_unchecked",
)

NO_WARNINGS = "- (none)"
"""`${drafting_warnings}` of a run that carries none — the block is always a list (D34-17)."""


def warning_lines(warnings: list | None) -> str:
    """`${drafting_warnings}` — one line per warning, `- [<code>] (<issue_id|general>) <text>`.

    D34-17: the writer used to receive one joined string of every warning, so it could neither
    tell an instruction from a client-facing fact nor see which issue a warning belongs to.
    """
    rows: list[tuple[int, int, str]] = []
    for position, row in enumerate(warnings or []):
        if isinstance(row, dict):
            code = str(row.get("code") or "note")
            issue = str(row.get("issue_id") or "general")
            text = str(row.get("message") or "").strip()
        else:
            code, issue, text = "note", "general", str(row).strip()
        if not text:
            continue
        rank = WARNING_ORDER.index(code) if code in WARNING_ORDER else len(WARNING_ORDER)
        rows.append((rank, position, f"- [{code}] ({issue}) {text}"))
    return "\n".join(line for _, _, line in sorted(rows)) if rows else NO_WARNINGS


_DEFAULT_EXTRAS: dict[str, str] = {
    # Every `${…}` name a prompt may use gets a value, so `substitute` never fails on a retry or on
    # `mf dispatch render`, where the planner's extras are rebuilt from `steps[]` (§3.3).
    "issues": "see `plan.json`",
    "jurisdictions": "see `plan.json`",
    "routing": "see `lib/routing/discover-cache.json`",
    # D-110: with no probe to filter by, the honest digest is the one with nothing connected —
    # the web fallbacks alone. The analyst dispatch replaces it with the probed one.
    "routing_digest": routing.routing_digest({}),
    "mcp_namespaces": "see `intake/mcp-probe.json`",
    # D-147: filled in from `intake/preflight.json`; without one, nothing was measured.
    "source_access": "not checked",
    "followup_prompts": "none",
    "followup_gaps": "none",  # D-154: filled only on a sufficiency re-dispatch
    "previous_findings": "none - first pass of this layer",
    "retry_errors": "none",
    "max_questions": "10",
    "research_files": "`research/`",
    "drafting_warnings": "none",
    "verify_report": "`research/sources.json`",
    "sources_list": "see `research/sources.json`",
    "writer_task": "draft",
    "seed_path": "none",
    "inputs_list": "`plan.json`, `intake/`, `research/`",
    "draft_sha": "",
    "lint_attachment": "",  # D-39: filled in only on `lint_not_converged`, empty otherwise
    "claim_pairs": "`citations.json`",
    "review_files": "`reviews/`",
    "issues_path": "`state.iterations[]`",
    "polish_budget": "0",
    "known_blockers": "none",  # D-119: filled in only when the review loop already aggregated some
}


def build_context(
    work_dir: str | os.PathLike,
    state: dict,
    *,
    step_id: str,
    attempt: int,
    slot: str,
    agent: str,
    outputs: list[dict],
    extra: dict | None = None,
) -> dict:
    """Flat mapping for `Template.substitute` — a superset of the names any prompt may use (§3.3)."""
    config = state.get("config") or {}
    layers = list(config.get("researcher_layers") or [])
    layer = str((extra or {}).get("layer") or (slot if slot in routing.LAYERS else ""))
    iteration = int((extra or {}).get("iteration") or state.get("current_iteration") or 1)
    checklist_kind = str((extra or {}).get("checklist") or "")

    context = {
        "mf": mf_path(),
        "work_dir": str(Path(work_dir).absolute()),
        "task_id": str(state.get("task_id") or ""),
        "user_query": str(state.get("user_query") or ""),
        "step_id": step_id,
        "attempt": str(int(attempt)),
        "slot": slot,
        "agent_type": SUBAGENT_PREFIX + agent,
        "mode": str(state.get("mode") or "full"),
        # D-112: the layers the mode researches — what the sufficiency reviewer judges against.
        "researcher_layers": ", ".join(layers) or "statutes",
        "template_id": str(config.get("template_id") or "classical-memo"),
        "paths_agent_core": lib_path("lib", "agent-core"),
        "paths_schemas": lib_path("schemas"),
        "paths_checklists": lib_path("lib", "checklists"),
        "paths_checklist": lib_path("lib", "checklists", f"{checklist_kind}.json")
        if checklist_kind
        else lib_path("lib", "checklists"),
        "paths_template": _template_path(config),
        "prose_style_path": _prose_style_path(config),
        "layer": layer,
        "layer_rules": layer_rules_line(layer) if layer in routing.LAYER_RULES else "",
        "mcp_budget_share": mcp_budget_share(config, layers or [layer or "statutes"]),
        "iteration": str(iteration),
        "draft_path": str((extra or {}).get("draft_path") or state.get("current_draft_path") or ""),
        "draft_version": str((extra or {}).get("draft_version") or iteration),
        "instructions_path": str((extra or {}).get("instructions_path") or ""),
        "outputs": outputs_block(outputs),
        "primary_output": outputs[0]["work_path"] if outputs else "",
        "primary_output_schema": str(outputs[0]["schema"] or "") if outputs else "",
    }
    for key, value in _DEFAULT_EXTRAS.items():
        context.setdefault(key, value)
    for key, value in (extra or {}).items():
        context[key] = "" if value is None else str(value)
    # D34-17: `${drafting_warnings}` belongs to `dispatch` — a caller cannot express the list in
    # one extra, so the structured block is rendered from state and overrides whatever it passed.
    context["drafting_warnings"] = warning_lines(state.get("drafting_warnings"))
    return context


def render_prompt(agent: str, context: dict) -> str:
    """`substitute()` of `prompts/<agent>.md`; an unresolved placeholder raises (§3.3)."""
    text = prompt_path(agent).read_text(encoding="utf-8-sig")
    return Template(text).substitute(context)


# --- rendering a whole dispatch step --------------------------------------


def render_agents(
    work_dir: str | os.PathLike,
    state: dict,
    *,
    step_id: str,
    attempt: int,
    specs: list[dict],
    position: int,
    total: int,
) -> list[dict]:
    """Turn the phase's specs into the `agents[]` payload of a `dispatch` step (§3.1)."""
    config = state.get("config") or {}
    rendered: list[dict] = []
    for item in specs:
        slot = item["slot"]
        agent = item["agent"]
        outputs = [output(canonical, schema, step_id, attempt, slot) for canonical, schema in item["outputs"]]
        context = build_context(
            work_dir,
            state,
            step_id=step_id,
            attempt=attempt,
            slot=slot,
            agent=agent,
            outputs=outputs,
            extra=item.get("extra"),
        )
        model = item.get("model")
        if model is None:
            model = (
                config.get("writer_model")
                if agent == "memo-writer"
                else AGENT_MODELS[agent]["model"]
            )
        rendered.append(
            {
                "slot": slot,
                "subagent_type": SUBAGENT_PREFIX + agent,
                "agent": agent,
                "model": model,
                # D-75: no `effort` here — the `Agent` call does not take it; the agent's
                # frontmatter (mirrored in `AGENT_MODELS`) is the only channel.
                "description": description(position, total, agent, item["label"]),
                "prompt": render_prompt(agent, context),
                "expected_outputs": outputs,
            }
        )
    save_specs(work_dir, step_id, attempt, specs)
    return rendered


SPEC_STORE = "spec.json"
"""Basename of the per-attempt spec store written next to the slot workspaces (D-55)."""


def spec_store_path(work_dir: str | os.PathLike, step_id: str, attempt: int) -> Path:
    """`steps/<step_id>/a<attempt>/spec.json` — outside every slot dir, so `agent log` ignores it."""
    return Path(work_dir) / "steps" / str(step_id) / f"a{int(attempt)}" / SPEC_STORE


def _spec_document(item: dict) -> dict:
    return {
        "slot": str(item["slot"]),
        "agent": str(item["agent"]),
        "label": str(item["label"]),
        "outputs": [[canonical, schema] for canonical, schema in item["outputs"]],
        "inputs": list(item.get("inputs") or []),
        "model": item.get("model"),
        "effort": item.get("effort"),
        "extra": dict(item.get("extra") or {}),
    }


def save_specs(work_dir: str | os.PathLike, step_id: str, attempt: int, specs: list[dict]) -> Path:
    """D-55: keep the full spec of every rendered slot so a retry rebuilds the same prompt.

    D34-19: the bytes are written through `state_io.write_json_atomic`, which encodes utf-8
    explicitly — the store must not follow the console codepage of the host (cp1251 turned the
    en-dashes of the writer spec into `?` in a real run).
    """
    path = spec_store_path(work_dir, step_id, attempt)
    stored: dict = {}
    try:
        existing = state_io.read_json(path)
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict):
        stored = existing
    for item in specs:
        stored[str(item["slot"])] = _spec_document(item)
    return state_io.write_json_atomic(path, stored)


def load_spec(work_dir: str | os.PathLike, step_id: str, attempt: int, slot: str) -> dict | None:
    """The stored spec of one slot, or None when the step was issued before D-55."""
    try:
        stored = state_io.read_json(spec_store_path(work_dir, step_id, attempt))
    except (OSError, ValueError):
        return None
    row = stored.get(str(slot)) if isinstance(stored, dict) else None
    if not isinstance(row, dict) or not row.get("agent"):
        return None
    outputs = [
        (str(entry[0]), entry[1] if len(entry) > 1 else None)
        for entry in (row.get("outputs") or [])
        if entry
    ]
    return spec(
        str(row.get("slot") or slot),
        str(row["agent"]),
        str(row.get("label") or slot),
        outputs,
        inputs=list(row.get("inputs") or []),
        model=row.get("model"),
        effort=row.get("effort"),
        **dict(row.get("extra") or {}),
    )


def inputs_map(work_dir: str | os.PathLike, specs: list[dict]) -> dict:
    """`steps[].inputs` — sha of every declared input at issue time (§3.1 completion check)."""
    work = Path(work_dir)
    result: dict[str, str] = {}
    for item in specs:
        for relative in item.get("inputs") or []:
            path = work / relative
            if path.is_file():
                result[relative] = state_io.sha256_file(path)
    return result


# --- `mf dispatch render` --------------------------------------------------


def _spec_from_step(work_dir: str | os.PathLike, state: dict, row: dict, agent_row: dict) -> dict:
    """The spec of one slot for a re-render or a retry (§5.2 `dispatch render`, D-55).

    The spec stored when the attempt was issued is authoritative: a retry then changes only
    `step_id`/`attempt`/`slot`, the attempt work paths and `retry_errors`. The reconstruction
    below is the fallback for steps issued before the store existed.
    """
    from . import machine

    agent = str(agent_row.get("agent_type") or "").split(":", 1)[-1]
    slot = str(agent_row.get("slot"))
    stored = load_spec(work_dir, str(row.get("step_id")), int(row.get("attempt") or 1), slot)
    if stored is not None:
        return stored
    outputs = [
        (str(entry.get("canonical_path")), entry.get("schema"))
        for entry in (agent_row.get("outputs") or row.get("expected_outputs") or [])
        if entry.get("canonical_path")
    ]
    extra: dict = {"iteration": int(state.get("current_iteration") or 1)}
    if agent == "legal-researcher":
        extra["layer"] = slot
    if slot in REVIEWER_AGENTS:
        extra["checklist"] = slot
    if agent == "client-readiness-reviewer":
        extra["checklist"] = "client-readiness"
    if agent in ("memo-writer", "revision-mediator") or slot in REVIEWER_AGENTS:
        canonical = outputs[0][0] if outputs else ""
        extra["draft_path"] = str(state.get("current_draft_path") or "")
        extra["draft_version"] = int(state.get("current_iteration") or 1)
        extra["draft_sha"] = str(state.get("current_draft_sha") or "")
        if slot in REVIEWER_AGENTS:
            # D-39: the re-render must reproduce the attachment rule, not the default.
            extra["lint_attachment"] = machine.lint_attachment(
                Path(work_dir), state, extra["draft_sha"]
            )
        if agent == "memo-writer":
            extra["draft_path"] = canonical
            extra["seed_path"] = canonical
            extra["writer_task"] = "draft" if canonical.endswith("v1.md") else "revision"
    return spec(slot, agent, slot, outputs, **extra)


def run_render(args: argparse.Namespace) -> dict:
    """`mf dispatch render --step <id> --attempt <n> [--slot <slot>]` — re-render an issued prompt."""
    from . import machine

    work_dir = Path(args.workdir)
    state = state_io.read_state(work_dir)
    row = machine.step_row(state, args.step, int(args.attempt))
    if row is None:
        return {"errors": [f"unknown_step: {args.step} a{args.attempt}"]}
    if row.get("kind") != "dispatch":
        return {"errors": [f"not_a_dispatch_step: {args.step}"], "kind": row.get("kind")}

    agent_rows = [a for a in (row.get("agents") or []) if not args.slot or a.get("slot") == args.slot]
    if not agent_rows:
        return {"errors": [f"unknown_slot: {args.slot}"]}

    progress = state.get("progress") or {}
    specs = [_spec_from_step(work_dir, state, row, agent_row) for agent_row in agent_rows]
    agents = render_agents(
        work_dir,
        state,
        step_id=str(row["step_id"]),
        attempt=int(row["attempt"]),
        specs=specs,
        position=int(progress.get("position") or 0),
        total=int(progress.get("total") or 0),
    )
    return {"step_id": row["step_id"], "attempt": row["attempt"], "agents": agents}


def register(subparsers) -> None:
    """Register `mf dispatch render` (§5.2)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "dispatch", "dispatch plans and prompts (§3.3)")
    parser = group.add_parser("render", help="re-render the prompt(s) of an issued dispatch step")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--slot", default=None)
    parser.set_defaults(func=run_render)
