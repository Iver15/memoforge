"""`mf task new|resolve|list|cancel` — task creation and re-entry (ТЗ §2.5, §2.4 d)."""

from __future__ import annotations

import argparse
import datetime
import os
import re
import unicodedata
from pathlib import Path

from . import events, fallbacks, hooks_common, i18n, limits, modes, phases, pylauncher, review, state_io

WORK_DIR_SUBDIRS: tuple[str, ...] = (
    "intake",
    "research",
    "research/raw",
    "drafts",
    "reviews",
    "steps",
    "logs",
    "events/.seen",
)

LEGACY_HINT = "task from v1 — finish it with memoforge 1.1.1 or start a new task"

LANGUAGE_OPTIONS = ("memo_language", "ui_language")
"""The two language options of D-169 — the memo language and the interface language."""

LANGUAGE_VALUES = ("auto",) + i18n.LANGUAGES
"""Every legal option value (`auto` resolves through `--detected-language`, D-170)."""

LANGUAGE_CODES = "|".join(i18n.LANGUAGES)
"""`en|de|fr|es|ru` — the named-flag error text (no `auto` there, D-169)."""

LANGUAGE_OPTION_VALUES = "|".join(LANGUAGE_VALUES)
"""`auto|en|de|fr|es|ru` — the option error text (`--option`, `mf config set`, D-169)."""



# --- helpers --------------------------------------------------------------


def utc_stamp() -> str:
    """Compact UTC stamp used inside `task_id`."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sanitize_slug(text: str | None) -> str:
    """Sanitise a slug to `[a-z0-9-]`, ≤ limits.SLUG_MAX_LENGTH, never empty (§2.5)."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if len(value) > limits.SLUG_MAX_LENGTH:
        value = value[: limits.SLUG_MAX_LENGTH]
        if "-" in value[1:]:
            value = value[: value.rindex("-")]
    value = value.strip("-")
    return value or "task"


def coerce_bool(value: object) -> bool:
    """Parse a plugin userConfig boolean coming from the environment."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in BOOL_TRUE


# --- userConfig options (§8.4, D-91) --------------------------------------

OPTIONS_FILENAME = "options.json"
"""`<plugin_data_dir>/options.json` — the mirror `hooks/ensure_deps.py` writes at SessionStart."""

OPTION_KEYS: tuple[str, ...] = (
    "output_folder",
    "publish_folder",
    "writer_model",
    "source_review_gate",
    "citation_style",
    "memo_language",
    "ui_language",
    "dashboard",
    "stop_guard",
    "websearch_autoallow",
)
"""The ten options declared in `.claude-plugin/plugin.json`, in manifest order (§8.4, D-109, D-152, D-169)."""

BOOL_OPTIONS: frozenset = frozenset(("dashboard", "stop_guard", "websearch_autoallow"))

PATH_OPTIONS: frozenset = frozenset(("output_folder", "publish_folder"))
"""Options whose value is a directory path; only a path the platform cannot hold is refused."""

BOOL_TRUE: tuple[str, ...] = ("1", "true", "yes", "on")
BOOL_FALSE: tuple[str, ...] = ("0", "false", "no", "off")

OPTION_DEFAULTS: dict = {
    "publish_folder": "",
    "writer_model": modes.DEFAULT_WRITER_MODEL,
    "source_review_gate": "auto",
    "citation_style": modes.DEFAULT_CITATION_STYLE,
    "memo_language": "en",
    "ui_language": "auto",
    "dashboard": "true",
    "stop_guard": "false",
    "websearch_autoallow": "true",
}
"""Manifest defaults of §8.4; `output_folder` has none — its default is the §2.5 chain itself, and
the empty `publish_folder` means «the host's outputs area if there is one, else copy nothing» (D-109)."""

is_placeholder = hooks_common.is_placeholder
"""D-93: one predicate for the whole chain, defined in `hooks_common` (stdlib-only, hook-safe), so
`output_folder_candidates` cannot hand back a `${…}` that `resolve_options` already skipped."""


def option_env_name(key: str) -> str:
    """`CLAUDE_PLUGIN_OPTION_<KEY>` — the variable a host exports for a `userConfig` option."""
    return "CLAUDE_PLUGIN_OPTION_" + key.upper()


def options_path(plugin_data: str | os.PathLike | None = None) -> Path:
    """`<plugin_data_dir>/options.json`; the same §2.5 chain the hook writes into (D-91)."""
    base = Path(plugin_data) if plugin_data is not None else pylauncher.plugin_data_dir()
    return base / OPTIONS_FILENAME


def read_options_file(path: str | os.PathLike | None = None) -> dict:
    """Raw `{key: str}` of `options.json`; unknown keys, empty values and read errors are dropped."""
    document = hooks_common.read_json_quiet(options_path() if path is None else path) or {}
    values: dict = {}
    for key in OPTION_KEYS:
        value = document.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        text = str(value).strip()
        if text:
            values[key] = text
    return values


def resolve_options(flags: dict | None = None, *, path: str | os.PathLike | None = None) -> dict:
    """Resolve the ten options: CLI flags > `CLAUDE_PLUGIN_OPTION_*` > `options.json` > defaults.

    D-91: a host exports the option variables to hooks only, so `options.json` (written by
    `hooks/ensure_deps.py`, or by hand through `mf config set`) is what a `Bash`-launched `mf`
    actually reads. Answers `{"values": {key: raw str}, "sources": {key: <source name>}}`.
    """
    explicit = {key: value for key, value in (flags or {}).items() if value is not None}
    from_file = read_options_file(path)
    values: dict = {}
    sources: dict = {}
    for key in OPTION_KEYS:
        candidates = (
            ("flag", explicit.get(key)),
            ("env", os.environ.get(option_env_name(key))),
            (OPTIONS_FILENAME, from_file.get(key)),
            ("default", OPTION_DEFAULTS.get(key)),
        )
        sources[key] = "default"
        for source, raw in candidates:
            if raw is None:
                continue
            text = str(raw).strip()
            if not text or is_placeholder(text):
                continue
            values[key] = text
            sources[key] = source
            break
    return {"values": values, "sources": sources}


def user_config_from_options(resolved: dict) -> dict:
    """Turn resolved raw option strings into the `userConfig` dict `modes.resolve_config` expects."""
    return {
        key: coerce_bool(value) if key in BOOL_OPTIONS else value
        for key, value in resolved["values"].items()
    }


def user_config_from_env(**overrides: object) -> dict:
    """`userConfig` of the §8.4 chain: explicit arguments > env > `options.json` > defaults."""
    return user_config_from_options(resolve_options(overrides))


def validate_option(key: str, value: object) -> str | None:
    """None when `key=value` is a legal `userConfig` pair, else the error string (§8.4, D-91)."""
    if key not in OPTION_KEYS:
        return f"unknown_option: {key}"
    text = str(value).strip()
    if not text:
        return f"empty_option_value: {key}"
    if is_placeholder(text):
        return f"placeholder_option_value: {key}={value}"
    if key in PATH_OPTIONS and "\x00" in text:
        return f"invalid_option_value: {key}={value} (expected a directory path)"
    if key in BOOL_OPTIONS and text.lower() not in BOOL_TRUE + BOOL_FALSE:
        return f"invalid_option_value: {key}={value} (expected true or false)"
    if key == "source_review_gate" and text.lower() not in modes.SOURCE_REVIEW_GATE_VALUES:
        expected = "|".join(modes.SOURCE_REVIEW_GATE_VALUES)
        return f"invalid_option_value: {key}={value} (expected {expected})"
    if key == "writer_model" and text not in limits.ALLOWED_WRITER_MODELS:
        expected = "|".join(sorted(limits.ALLOWED_WRITER_MODELS))
        return f"invalid_option_value: {key}={value} (expected {expected})"
    if key == "citation_style" and text.lower() not in modes.CITATION_STYLES:
        expected = "|".join(modes.CITATION_STYLES)
        return f"invalid_option_value: {key}={value} (expected {expected})"
    if key in LANGUAGE_OPTIONS and text.lower() not in LANGUAGE_VALUES:
        return f"invalid_option_value: {key}={value} (expected {LANGUAGE_OPTION_VALUES})"
    return None


def validate_language(value: object) -> str | None:
    """None when `value` is a legal named-flag language code, else the error string (D-169)."""
    if i18n.normalize(value) is not None:
        return None
    return f"invalid_language: {value} (expected {LANGUAGE_CODES})"


def resolve_languages(
    values: dict,
    *,
    language: str | None,
    ui_language: str | None,
    detected: str | None,
) -> dict:
    """Resolve the two language options to codes plus sources and fallbacks (D-169, D-170).

    Resolution order per option: named flag → ordinary chain (`--option` > env >
    `options.json` > default). If the result is `auto`: `normalize(detected)` or `en`,
    source `detected` (the chain's source stays when no detected value was given). An
    invalid chain value resolves to `en` and is reported as one fallback per option.
    `values` is the `resolve_options` answer (`{"values": ..., "sources": ...}`);
    the named flags were validated already.
    """
    raw_values = values.get("values", values)
    chain_sources = values.get("sources", {})
    out: dict = {"language": "en", "ui_language": "en", "sources": {}, "fallbacks": []}
    for option, flag in (("memo_language", language), ("ui_language", ui_language)):
        answer_key = "language" if option == "memo_language" else "ui_language"
        if flag is not None:
            out[answer_key] = i18n.normalize(flag) or "en"
            out["sources"][answer_key] = "flag"
            continue
        raw = raw_values.get(option, OPTION_DEFAULTS[option])
        text = str(raw).strip().lower()
        if text not in LANGUAGE_VALUES:
            out["sources"][answer_key] = "default"
            out["fallbacks"].append({"option": option, "requested": str(raw), "applied": "en"})
            continue
        if text == i18n.AUTO:
            # Presence beats normalization: a supplied `--detected-language` owns the source
            # even when it falls outside the five codes (then it means `en`).
            if detected is not None:
                out[answer_key] = i18n.normalize(detected) or "en"
                out["sources"][answer_key] = "detected"
            else:
                out[answer_key] = "en"
                out["sources"][answer_key] = chain_sources.get(option, "default")
            continue
        out[answer_key] = text
        out["sources"][answer_key] = chain_sources.get(option, "default")
    return out


def parse_option_flags(pairs: list | None) -> tuple:
    """`--option key=value` (repeatable) -> `({key: value}, errors)`; every pair is validated.

    D-93: an unexpanded `${…}` on a known key is dropped, not refused, so `task new` falls through
    to env → `options.json` → default exactly like the named `--output-folder` flag already does;
    `mf config set` validates the pair itself and keeps refusing it (§8.4, D-91).
    """
    values: dict = {}
    errors: list = []
    for pair in pairs or []:
        key, separator, value = str(pair).partition("=")
        if not separator:
            errors.append(f"invalid_option_flag: {pair} (expected key=value)")
            continue
        key = key.strip().lower()
        if key in OPTION_KEYS and is_placeholder(value):
            continue
        error = validate_option(key, value)
        if error:
            errors.append(error)
            continue
        values[key] = value.strip()
    return values, errors


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = path / ".mf-write-probe"
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        probe.unlink()
    except OSError:
        return False
    return True


def resolve_output_folder(explicit: str | None = None, cwd: str | os.PathLike | None = None) -> dict:
    """Walk the §2.5 chain and return the first writable output folder plus the rejected ones."""
    rejected: list[dict] = []
    for candidate in hooks_common.output_folder_candidates(explicit, cwd=cwd):
        path = candidate["path"]
        if _is_writable_dir(path):
            return {
                "output_folder": path.absolute(),
                "source": candidate["source"],
                "rejected": rejected,
            }
        rejected.append({"source": candidate["source"], "path": str(path), "reason": "not_writable"})
    raise OSError(f"no_writable_output_folder: tried {[row['source'] for row in rejected]}")


def allocate_work_dir(output_folder: Path, task_id: str) -> tuple[Path, str]:
    """Return a free `<output_folder>/<task_id>` (suffixing on collision)."""
    candidate = output_folder / task_id
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = output_folder / f"{task_id}-{suffix}"
    return candidate, candidate.name


def create_work_dir_tree(work_dir: Path) -> None:
    """Create the working tree, the permanent lock files and an empty journal (§2.2)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    for relative in WORK_DIR_SUBDIRS:
        (work_dir / relative).mkdir(parents=True, exist_ok=True)
    state_io.ensure_lock_files(work_dir)
    journal = events.events_path(work_dir)
    if not journal.exists():
        journal.touch()


def initial_attempts() -> dict:
    """Zeroed budget table of §2.2."""
    return {
        "plan_edit": 0,
        "research_dispatch_retry": 0,
        "currency_regate": 0,
        "client_polish": 0,
        "lint_fix": {},
        "reviewer_json_retry": {},
        "reviewer_rerun": {},
        "single_dispatch_retry": {},
        "inline_llm_retry": {},
        "gate_parse_errors": {},
    }


def build_initial_state(
    *,
    task_id: str,
    user_query: str,
    language: str,
    work_dir: Path,
    output_folder: Path,
    config: dict,
    created_at: str | None = None,
    ui_language: str = "en",
) -> dict:
    """Build a schema-valid v2 `state.json` for a fresh task (§2.2)."""
    created_at = created_at or events.utc_now()
    return {
        "schema_version": 2,
        "task_id": task_id,
        "user_query": user_query,
        "created_at": created_at,
        "language": language,
        "ui_language": ui_language,
        "work_dir": str(work_dir),
        "output_folder": str(output_folder),
        "mode": "full",
        "config": config,
        "intake": {
            "status": "pending",
            "assumptions_accepted": None,
            "questions_path": None,
            "user_facts_path": None,
            "answered_at": None,
        },
        "classification": None,
        "plan_approval": {
            "status": "pending",
            "generation": 0,
            "iterations": [],
            "approved_at": None,
            "plan_path": None,
        },
        "current_phase": phases.INITIAL_PHASE,
        "dispatched_researchers": [],
        "current_iteration": 0,
        "current_draft_path": None,
        "current_draft_sha": None,
        "draft_versions": [],
        "iterations": [],
        "client_readiness": None,
        "final_status": None,
        "final_status_reasons": [],
        "final_docx_path": None,
        "attempts": initial_attempts(),
        "sufficiency_followup": None,
        "drafting_warnings": [],
        "remaining_blocking_issues": [],
        "fallback_banners": [],
        "steps": [],
        "published": [],
        "progress": {
            "phase": phases.INITIAL_PHASE,
            "phase_started_at": created_at,
            "route": [],
            "position": 0,
            "total": 0,
            "active": [],
            "last_line": None,
            "artifact_url": None,
            "published_to": None,
            "published_memo": None,
            "mcp_calls": {},
        },
        "sources_frozen": False,
        "cancel_requested": False,
    }


def _task_roots(workdir: str | None, cwd: str | os.PathLike | None = None) -> list[Path]:
    if workdir:
        return [Path(workdir)]
    # D-91: `task new` may have used an `output_folder` that only `options.json` knows about;
    # `resolve`/`list` have to look there too or the task they created becomes unreachable.
    explicit = resolve_options()["values"].get("output_folder")
    return [row["path"] for row in hooks_common.output_folder_candidates(explicit, cwd=cwd)]


def _describe(work_dir: Path, state: dict | None) -> dict:
    state = state or {}
    phase = state.get("current_phase")
    schema_version = state.get("schema_version", 1)
    return {
        "task_id": state.get("task_id", work_dir.name),
        "work_dir": str(work_dir.absolute()),
        "schema_version": schema_version,
        "current_phase": phase,
        "mode": state.get("mode"),
        "created_at": state.get("created_at"),
        "cancel_requested": bool(state.get("cancel_requested", False)),
        "terminal": bool(isinstance(phase, str) and phases.is_terminal(phase)),
        "gate": bool(isinstance(phase, str) and phases.is_gate(phase)),
    }


def _updated_at(work_dir: Path) -> str | None:
    """Last write of `state.json` as a UTC stamp — the `updated` column of D-36."""
    try:
        stamp = (work_dir / state_io.STATE_FILENAME).stat().st_mtime
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _list_tasks(workdir: str | None) -> list[dict]:
    rows = []
    for work_dir in hooks_common.task_dirs(_task_roots(workdir)):
        state = hooks_common.read_state_quiet(work_dir)
        rows.append(dict(_describe(work_dir, state), updated=_updated_at(work_dir)))
    rows.sort(key=lambda row: (row.get("created_at") or "", row["task_id"]), reverse=True)
    return rows


def _list_table(rows: list[dict]) -> str:
    """D-36: `mf task list --human` — one line per task, columns of the decision."""
    columns = (
        ("task_id", "task_id"),
        ("current_phase", "phase"),
        ("mode", "mode"),
        ("updated", "updated"),
        ("work_dir", "work_dir"),
    )
    table = [[header for _, header in columns]]
    table += [[str(row.get(key) or "-") for key, _ in columns] for row in rows]
    widths = [max(len(cell[index]) for cell in table) for index in range(len(columns))]
    lines = ["  ".join(cell.ljust(widths[index]) for index, cell in enumerate(table[0])).rstrip()]
    lines.append("  ".join("-" * width for width in widths).rstrip())
    for cell in table[1:]:
        lines.append("  ".join(text.ljust(widths[index]) for index, text in enumerate(cell)).rstrip())
    if not rows:
        lines.append("(no tasks)")
    return "\n".join(lines)


def _locate(args: argparse.Namespace) -> dict:
    """Find one task by explicit work dir, task id, or 'last unfinished' (§2.5)."""
    workdir = getattr(args, "workdir", None)
    task_id = getattr(args, "task_id", None)
    if workdir and (Path(workdir) / state_io.STATE_FILENAME).is_file() and not task_id:
        work_dir = Path(workdir)
        return {"work_dir": work_dir, "state": hooks_common.read_state_quiet(work_dir)}

    rows = hooks_common.task_dirs(_task_roots(workdir))
    if task_id:
        for work_dir in rows:
            state = hooks_common.read_state_quiet(work_dir)
            if work_dir.name == task_id or (state or {}).get("task_id") == task_id:
                return {"work_dir": work_dir, "state": state}
        return {"errors": ["task_not_found"], "task_id": task_id}

    unfinished = []
    for work_dir in rows:
        state = hooks_common.read_state_quiet(work_dir)
        phase = (state or {}).get("current_phase")
        if isinstance(phase, str) and phases.is_terminal(phase):
            continue
        unfinished.append((work_dir, state))
    if not unfinished:
        return {"errors": ["no_unfinished_task"]}
    unfinished.sort(key=lambda item: ((item[1] or {}).get("created_at") or "", item[0].name))
    work_dir, state = unfinished[-1]
    return {"work_dir": work_dir, "state": state}


# --- commands -------------------------------------------------------------


def run_new(args: argparse.Namespace) -> dict:
    """`mf task new` — resolve the work dir, create the tree and write state v2 (§2.5)."""
    query = args.query
    if not query or not query.strip():
        return {"errors": ["empty_query"]}

    flags, flag_errors = parse_option_flags(getattr(args, "option", None))
    if flag_errors:
        return {"errors": flag_errors, "known_options": list(OPTION_KEYS)}
    for name, value in (
        ("output_folder", args.output_folder),
        ("writer_model", args.writer_model),
        ("source_review_gate", args.source_review_gate),
    ):
        if value is not None:
            flags[name] = value  # a named flag beats the same key given as `--option key=value`
    # D-169: the named language flags accept the five codes only, not `auto`.
    language_errors = []
    for value in (getattr(args, "language", None), getattr(args, "ui_language", None)):
        error = validate_language(value) if value is not None else None
        if error is not None:
            language_errors.append(error)
    if language_errors:
        return {"errors": language_errors}
    options = resolve_options(flags)
    languages = resolve_languages(
        options,
        language=getattr(args, "language", None),
        ui_language=getattr(args, "ui_language", None),
        detected=getattr(args, "detected_language", None),
    )
    language = languages["language"]
    ui_language = languages["ui_language"]
    # D-169: a memo pack that cannot be read refuses the task before anything lands on disk.
    if not i18n.available(language):
        return {"errors": [f"language_pack_unavailable: {language}"]}
    user_config = user_config_from_options(options)
    # D-242: one run mode — the Full config is resolved at creation, so every reader of `config`
    # before the plan gate already sees what the run will use.
    config = modes.resolve_config("full", user_config)
    # D-15: an unknown `writer_model` never fails `task new`; it degrades and is logged below.
    writer_model_fallback = config.pop(modes.WRITER_MODEL_FALLBACK_KEY, None)
    config["plugin_data_dir"] = str(pylauncher.plugin_data_dir())
    config["python_cmd"] = pylauncher.python_cmd()

    slug = sanitize_slug(args.slug if args.slug else query)
    resolved = resolve_output_folder(user_config.get("output_folder"))
    output_folder = resolved["output_folder"]
    work_dir, task_id = allocate_work_dir(output_folder, f"memo-{utc_stamp()}-{slug}")
    create_work_dir_tree(work_dir)

    events.append_event(
        work_dir,
        "task_created",
        "cli",
        {"task_id": task_id, "slug": slug, "language": language, "ui_language": ui_language},
        phase=phases.INITIAL_PHASE,
    )
    events.append_event(
        work_dir,
        "work_dir_resolved",
        "cli",
        {
            "work_dir": str(work_dir),
            "output_folder": str(output_folder),
            "source": resolved["source"],
            "rejected": resolved["rejected"],
        },
        phase=phases.INITIAL_PHASE,
    )
    if writer_model_fallback is not None:
        events.append_event(
            work_dir,
            "writer_model_fallback",
            "cli",
            {"requested": writer_model_fallback, "applied": config["writer_model"]},
            phase=phases.INITIAL_PHASE,
            severity="warn",
        )
    # D-169: an invalid chain value degrades to `en` and is logged, like `writer_model_fallback`.
    for fallback in languages["fallbacks"]:
        events.append_event(
            work_dir,
            "language_fallback",
            "cli",
            fallback,
            phase=phases.INITIAL_PHASE,
            severity="warn",
        )

    state = build_initial_state(
        task_id=task_id,
        user_query=query,
        language=language,
        ui_language=ui_language,
        work_dir=work_dir,
        output_folder=output_folder,
        config=config,
    )
    state_io.create_state(work_dir, state)

    # TODO(§3.1): the first protocol step is issued by `machine.next` (slice S2);
    # `task new` never plans steps and never writes `steps[]` itself.
    return {
        "task_id": task_id,
        "work_dir": str(work_dir),
        "output_folder": str(output_folder),
        "work_dir_source": resolved["source"],
        "state_path": str(state_io.state_path(work_dir)),
        "schema_version": 2,
        "current_phase": state["current_phase"],
        "language": language,
        "ui_language": ui_language,
        "language_source": languages["sources"],
        "slug": slug,
        # D-91: where each option came from, so a host with no options UI is diagnosable.
        "options_source": options["sources"],
        "options_path": str(options_path()),
        "next": ["mf", "next", "--workdir", str(work_dir)],
    }


def run_resolve(args: argparse.Namespace) -> dict:
    """`mf task resolve [task_id]` — re-entry; v1 tasks answer `unsupported` (§2.1, §2.5)."""
    located = _locate(args)
    if located.get("errors"):
        return located
    work_dir = located["work_dir"]
    state = located["state"]
    if state is None:
        return {"errors": ["state_unreadable"], "work_dir": str(work_dir.absolute())}
    if not isinstance(state.get("schema_version"), int) or state["schema_version"] < 2:
        return {
            "unsupported": True,
            "hint": LEGACY_HINT,
            "task_id": state.get("task_id", work_dir.name),
            "work_dir": str(work_dir.absolute()),
            "schema_version": state.get("schema_version", 1),
        }
    result = _describe(work_dir, state)
    result["unsupported"] = False
    result["state_path"] = str(state_io.state_path(work_dir))
    result["next"] = ["mf", "next", "--workdir", str(work_dir.absolute())]
    return result


def run_list(args: argparse.Namespace) -> dict:
    """`mf task list` — every task found through the resolution chain (works without jsonschema)."""
    rows = _list_tasks(args.workdir)
    if args.active:
        rows = [row for row in rows if not row["terminal"]]
    if args.limit:
        rows = rows[: args.limit]
    result = {"tasks": rows, "count": len(rows)}
    if getattr(args, "human", False):
        result["human"] = _list_table(rows)
    return result


def run_cancel(args: argparse.Namespace) -> dict:
    """`mf task cancel` — set `cancel_requested`; `next` then routes to finalize (§2.4 d)."""
    located = _locate(args)
    if located.get("errors"):
        return located
    work_dir = located["work_dir"]
    state = located["state"]
    if state is None:
        return {"errors": ["state_unreadable"], "work_dir": str(work_dir.absolute())}
    if not isinstance(state.get("schema_version"), int) or state["schema_version"] < 2:
        return {
            "unsupported": True,
            "hint": LEGACY_HINT,
            "task_id": state.get("task_id", work_dir.name),
            "work_dir": str(work_dir.absolute()),
            "schema_version": state.get("schema_version", 1),
        }

    phase = state.get("current_phase")
    if isinstance(phase, str) and phases.is_terminal(phase):
        result = _describe(work_dir, state)
        result["already_terminal"] = True
        result["cancelled"] = False
        return result

    if state.get("cancel_requested"):
        result = _describe(work_dir, state)
        result["already_terminal"] = False
        result["cancelled"] = True
        result["already_requested"] = True
        return result

    def mutator(current: dict) -> None:
        current["cancel_requested"] = True

    new_state = state_io.write_state(work_dir, mutator)
    events.append_event(
        work_dir,
        "cancel_requested",
        "cli",
        {"task_id": new_state.get("task_id")},
        phase=new_state.get("current_phase"),
        severity="warn",
    )
    result = _describe(work_dir, new_state)
    result["already_terminal"] = False
    result["cancelled"] = True
    result["already_requested"] = False
    return result


def run_dashboard(args: argparse.Namespace) -> dict:
    """`mf task dashboard --workdir W (--url <url> | --unavailable "<reason>")` — ТЗ §7.5, D-87.

    The CLI never calls the `Artifact` tool (it has no CLI or API): the orchestrator publishes the
    shipped page once and hands the URL back here, or reports why it could not — which raises the
    `dashboard_unavailable` banner and silences the `dashboard` block of `next` for good. Neither
    branch touches the pipeline: a run without a dashboard is a run with one less chat decoration.
    """
    url = (getattr(args, "url", None) or "").strip()
    reason = (getattr(args, "unavailable", None) or "").strip()
    if bool(url) == bool(reason):
        return {"errors": ["dashboard_requires_one_of: --url or --unavailable"]}

    work_dir = Path(args.workdir)
    state = state_io.read_state_or_none(work_dir)
    if state is None:
        return {"errors": ["state_unreadable"], "work_dir": str(work_dir.absolute())}

    if url:
        def mutator(current: dict) -> None:
            progress = current.get("progress")
            if not isinstance(progress, dict):
                progress = {
                    "phase": current.get("current_phase"),
                    "route": [],
                    "position": 0,
                    "total": 0,
                    "active": [],
                }
                current["progress"] = progress
            progress["artifact_url"] = url

        new_state = state_io.write_state(work_dir, mutator)
        events.append_event(
            work_dir,
            "dashboard_published",
            "cli",
            {"url": url},
            phase=new_state.get("current_phase"),
        )
        result = _describe(work_dir, new_state)
        result["artifact_url"] = url
        result["human"] = f"Live dashboard: {url}"
        return result

    def mutator(current: dict) -> None:
        review.record_banner(current, fallbacks.DASHBOARD_UNAVAILABLE, reason=reason)

    new_state = state_io.write_state(work_dir, mutator)
    events.append_event(
        work_dir,
        "dashboard_unavailable",
        "cli",
        {"reason": reason[:400]},
        phase=new_state.get("current_phase"),
        severity="warn",
    )
    result = _describe(work_dir, new_state)
    result["artifact_url"] = None
    result["dashboard_unavailable"] = reason
    result["human"] = "Dashboard unavailable; the run continues without it."
    return result


class _LanguageRefused(Exception):
    """Control flow: the `run_language` mutator declined the write.

    Raised inside `state_io.write_state`, so the file is never rewritten and no
    `state_written` event is emitted. `.reason` is one of `terminal|locked|unavailable|noop`.
    """

    def __init__(self, reason: str) -> None:
        super().__init__("language change refused")
        self.reason = reason


def run_language(args: argparse.Namespace) -> dict:
    """`mf task language` — change the memo and/or interface language of a task (D-172).

    `--memo` is allowed while the plan is not approved yet (at most `plan_approval_pending`);
    later it answers `language_locked` and leaves the state untouched. `--ui` is allowed in
    any non-terminal phase. Eligibility is decided inside the locked mutator, so a phase
    change between the first read and the write cannot slip a change through: the mutator
    raises `_LanguageRefused` and `write_state` never writes, never emits `state_written`.
    """
    memo = getattr(args, "memo", None)
    ui = getattr(args, "ui", None)
    if memo is None and ui is None:
        return {"errors": ["language_requires_one_of: --memo or --ui"]}
    for value in (memo, ui):
        error = validate_language(value) if value is not None else None
        if error is not None:
            return {"errors": [error]}
    memo_code = i18n.normalize(memo) if memo is not None else None
    ui_code = i18n.normalize(ui) if ui is not None else None

    work_dir = Path(args.workdir)
    # Fast path only: detect a missing state before touching the lock.
    if state_io.read_state_or_none(work_dir) is None:
        return {"errors": ["state_unreadable"], "work_dir": str(work_dir.absolute())}

    outcome: dict = {}

    def mutator(current: dict) -> None:
        phase = current.get("current_phase")
        if isinstance(phase, str) and phases.is_terminal(phase):
            outcome["state"] = dict(current)
            raise _LanguageRefused("terminal")
        changed: list[str] = []
        if memo_code is not None and memo_code != current.get("language"):
            gate = phases.PHASE_INDEX["plan_approval_pending"]
            if phase not in phases.PHASE_INDEX or phases.PHASE_INDEX[phase] > gate:
                raise _LanguageRefused("locked")
            if not i18n.available(memo_code):
                raise _LanguageRefused("unavailable")
            current["language"] = memo_code
            changed.append("language")
        if ui_code is not None and ui_code != current.get("ui_language", "en"):
            current["ui_language"] = ui_code
            changed.append("ui_language")
        if not changed:
            raise _LanguageRefused("noop")
        outcome["changed"] = changed

    try:
        new_state = state_io.write_state(work_dir, mutator)
    except _LanguageRefused as refused:
        if refused.reason == "terminal":
            result = _describe(work_dir, outcome["state"])
            result["already_terminal"] = True
            result["changed"] = []
            return result
        if refused.reason == "locked":
            return {"errors": ["language_locked: memo language is fixed once the plan is approved"]}
        if refused.reason == "unavailable":
            return {"errors": [f"language_pack_unavailable: {memo}"]}
        state = state_io.read_state_or_none(work_dir)
        return {
            "language": (state or {}).get("language"),
            "ui_language": (state or {}).get("ui_language", "en"),
            "changed": [],
        }
    except OSError:
        return {"errors": ["state_unreadable"], "work_dir": str(work_dir.absolute())}
    return {
        "language": new_state.get("language"),
        "ui_language": new_state.get("ui_language", "en"),
        "changed": outcome.get("changed", []),
    }


def register(subparsers) -> None:
    """Register the `task` command group."""
    from . import cli

    group = cli.group_subparsers(subparsers, "task", "task lifecycle (new/resolve/list/cancel)")

    new = group.add_parser("new", help="create a task and its working directory")
    new.add_argument("--query", required=True, help="user question verbatim")
    new.add_argument("--slug", default=None, help="explicit slug (sanitised to [a-z0-9-])")
    new.add_argument("--output-folder", dest="output_folder", default=None)
    new.add_argument("--writer-model", dest="writer_model", default=None)
    new.add_argument(
        "--source-review-gate",
        dest="source_review_gate",
        default=None,
        choices=list(modes.SOURCE_REVIEW_GATE_VALUES),
    )
    new.add_argument(
        "--option",
        action="append",
        default=None,
        metavar="key=value",
        help="userConfig option, repeatable (e.g. --option dashboard=false); highest priority",
    )
    new.add_argument(
        "--language",
        dest="language",
        default=None,
        help="memo language, one of en|de|fr|es|ru (beats every option level, D-170)",
    )
    new.add_argument(
        "--ui-language",
        dest="ui_language",
        default=None,
        help="interface language, one of en|de|fr|es|ru (beats every option level, D-170)",
    )
    new.add_argument(
        "--detected-language",
        dest="detected_language",
        default=None,
        help="language the user writes in; applies only to options resolved to auto (D-170)",
    )
    new.set_defaults(func=run_new)

    resolve = group.add_parser("resolve", help="resolve a task id (default: last unfinished)")
    resolve.add_argument("task_id", nargs="?", default=None)
    resolve.add_argument("--workdir", default=None, help="work dir or folder holding work dirs")
    resolve.set_defaults(func=run_resolve)

    listing = group.add_parser("list", help="list known tasks")
    listing.add_argument("--workdir", default=None, help="work dir or folder holding work dirs")
    listing.add_argument("--active", action="store_true", help="only non-terminal tasks")
    listing.add_argument("--limit", type=int, default=0)
    listing.set_defaults(func=run_list)

    cancel = group.add_parser("cancel", help="request cancellation of a task")
    cancel.add_argument("task_id", nargs="?", default=None)
    cancel.add_argument("--workdir", default=None)
    cancel.set_defaults(func=run_cancel)

    language = group.add_parser("language", help="change the memo and/or interface language (D-172)")
    language.add_argument("--workdir", required=True)
    language.add_argument("--memo", default=None, help="memo language, one of en|de|fr|es|ru")
    language.add_argument("--ui", default=None, help="interface language, one of en|de|fr|es|ru")
    language.set_defaults(func=run_language)

    dashboard = group.add_parser("dashboard", help="record the live dashboard URL (ТЗ §7.5)")
    dashboard.add_argument("--workdir", required=True)
    dashboard.add_argument("--url", default=None, help="URL the Artifact tool returned")
    dashboard.add_argument(
        "--unavailable", default=None, help="reason the dashboard could not be published"
    )
    dashboard.set_defaults(func=run_dashboard)
