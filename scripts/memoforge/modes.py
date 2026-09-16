"""Single source of truth for the mode matrix and config resolution (ТЗ §2.3)."""

from __future__ import annotations

from . import limits

MODES: dict[str, dict] = {
    "brief": {
        "researcher_layers": ["statutes"],
        "reviewer_list": ["logic", "citations", "counterarguments"],
        "max_iterations": 2,
        "client_polish_enabled": False,
        "max_client_polish": 0,
        "template_id": "executive-brief",
        "source_review_gate": "off",
        "lint_fix_rounds": 1,
        "intake_max_questions": limits.INTAKE_MAX_QUESTIONS,
        "mcp_budget": {
            "ldh": 8,
            "courtlistener": 10,
            "legalviz": 10,
            "uklegal": 10,
            "justicelibre": 10,
            "opencaselaw": 10,
            "fedregs": 10,
            "lex": 10,
        },
    },
    "full": {
        "researcher_layers": ["statutes", "case_law", "doctrine"],
        "reviewer_list": ["logic", "form", "citations", "counterarguments"],
        "max_iterations": 2,
        "client_polish_enabled": True,
        "max_client_polish": 1,
        "template_id": "classical-memo",
        "source_review_gate": "auto",
        "lint_fix_rounds": 2,
        "intake_max_questions": limits.INTAKE_MAX_QUESTIONS,
        # D-148: `ldh` is 10, not 16 — the observed free-plan day died at the 14th-15th call.
        "mcp_budget": {
            "ldh": 10,
            "courtlistener": 40,
            "legalviz": 40,
            "uklegal": 40,
            "justicelibre": 40,
            "opencaselaw": 40,
            "fedregs": 40,
            "lex": 40,
        },
    },
}

SOURCE_REVIEW_GATE_VALUES: tuple[str, ...] = ("auto", "on", "off")

DEFAULT_WRITER_MODEL = "opus"

DEFAULT_DASHBOARD = True
"""D-92: `userConfig.dashboard` is on by default — the same value as `.claude-plugin/plugin.json`."""

WRITER_MODEL_FALLBACK_KEY = "writer_model_fallback"
"""Transient key of `resolve_config` output: the rejected model (D-15). Never part of `state.config`."""

# Keys copied from userConfig into state.config regardless of mode (§2.2, §8.4).
# `stop_guard` and `websearch_autoallow` stay out: only the hooks read them, and they read them
# from the environment (D-74). `dashboard` is the exception D-87 makes: `mf next` reads it to
# decide whether its answer carries the §7.5 block, so it is resolved once and kept in state.
_USER_CONFIG_PASSTHROUGH: tuple[tuple[str, object], ...] = (
    ("writer_model", DEFAULT_WRITER_MODEL),
    ("publish_folder", None),
    ("style_profile", None),
    ("style_profile_path", None),
    ("style_profile_mode_binding", None),
    ("citation_style", None),
)

CITATION_STYLES: tuple[str, ...] = ("footnotes", "inline")
DEFAULT_CITATION_STYLE = "inline"
"""D-150/D-152: the `userConfig` default; `null` means «whatever the template's front matter says»,
which is `inline` in both templates too."""


def normalize_mode(mode: object) -> str | None:
    """Return the canonical mode key ('brief'/'full'), or None when no mode is chosen yet."""
    if mode is None or mode == "":
        return None
    if not isinstance(mode, str):
        raise ValueError(f"unknown_mode: {mode!r}")
    key = mode.strip().lower()
    if key not in MODES:
        raise ValueError(f"unknown_mode: {mode!r}")
    return key


def resolve_source_review_gate(mode: str | None, user_config: dict | None) -> str:
    """Priority chain of §2.3: explicit userConfig `on|off` > mode default > `auto`."""
    user_config = user_config or {}
    user_value = user_config.get("source_review_gate")
    if isinstance(user_value, str):
        user_value = user_value.strip().lower()
        if user_value in ("on", "off"):
            return user_value
        if user_value not in ("auto", ""):
            raise ValueError(f"invalid_source_review_gate: {user_value!r}")
    if mode is None:
        return "auto"
    return MODES[mode]["source_review_gate"]


def resolve_config(mode: object, user_config: dict | None = None) -> dict:
    """Build `state.config` for a mode (None = mode not chosen yet) from userConfig (§2.3).

    An unknown `writer_model` degrades to `DEFAULT_WRITER_MODEL` and is reported through the
    transient `writer_model_fallback` key (§4.1, D-15) — never through an exception. The caller
    logs the event and drops the key: it is not a `state.config` field.
    """
    key = normalize_mode(mode)
    user_config = dict(user_config or {})

    config: dict = {}
    for name, default in _USER_CONFIG_PASSTHROUGH:
        value = user_config.get(name, default)
        config[name] = default if value is None and default is not None else value

    writer_model = config.get("writer_model") or DEFAULT_WRITER_MODEL
    if writer_model not in limits.ALLOWED_WRITER_MODELS:
        config[WRITER_MODEL_FALLBACK_KEY] = str(writer_model)
        writer_model = DEFAULT_WRITER_MODEL
    config["writer_model"] = writer_model

    for name in ("python_cmd", "plugin_data_dir"):
        if user_config.get(name) is not None:
            config[name] = user_config[name]

    style = str(config.get("citation_style") or "").strip().lower()
    # D-150: an unknown value is no value — the template's own front matter decides instead.
    config["citation_style"] = style if style in CITATION_STYLES else None

    config["source_review_gate"] = resolve_source_review_gate(key, user_config)
    config["intake_max_questions"] = limits.INTAKE_MAX_QUESTIONS
    config["dashboard"] = bool(user_config.get("dashboard", DEFAULT_DASHBOARD))

    if key is None:
        return config

    mode_row = MODES[key]
    config.update(
        {
            "researcher_layers": list(mode_row["researcher_layers"]),
            "reviewer_list": list(mode_row["reviewer_list"]),
            "max_iterations": mode_row["max_iterations"],
            "client_polish_enabled": mode_row["client_polish_enabled"],
            "max_client_polish": mode_row["max_client_polish"],
            "template_id": mode_row["template_id"],
            "lint_fix_rounds": mode_row["lint_fix_rounds"],
            "intake_max_questions": mode_row["intake_max_questions"],
            "mcp_budget": dict(mode_row["mcp_budget"]),
        }
    )
    return config
