"""`mf style …` — user style profiles (ТЗ §4.6, §5.1; ported from `scripts/resolve_style_profile.py`).

The extraction logic and the on-disk profile format are unchanged (§0.3 non-goal). What changed:
profiles now live under `pylauncher.plugin_data_dir()/profiles/` (the single §2.5 chain instead of a
hard-coded `~/.claude/plugin-data`), `meta.json` is checked against the `style-meta` schema when
`jsonschema` is available, every subcommand that takes a name validates it first (03 S-23), and each
command prints one JSON object with the exit codes of CONVENTIONS instead of bare text.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
from pathlib import Path

from . import pylauncher, schema, state_io

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
NAME_MAX_LENGTH = 64

DEFAULT_FILENAME = "default-profile.txt"
PROFILES_DIRNAME = "profiles"
META_SCHEMA = "style-meta"

REQUIRED_META_KEYS: frozenset = frozenset(
    {"name", "created_at", "input_type", "mode_binding", "has_template"}
)
VALID_INPUT_TYPES: tuple[str, ...] = ("examples", "rules", "both")
VALID_MODE_BINDINGS: tuple[str, ...] = ("brief", "full")


# --- paths ----------------------------------------------------------------


def profiles_dir() -> Path:
    """`<plugin_data_dir>/profiles` — the same chain the `mf` wrappers use (§2.5)."""
    return pylauncher.plugin_data_dir() / PROFILES_DIRNAME


def default_file() -> Path:
    """`<plugin_data_dir>/default-profile.txt` — one line with the current default profile name."""
    return pylauncher.plugin_data_dir() / DEFAULT_FILENAME


def profile_dir(name: str) -> Path:
    """Directory of one profile."""
    return profiles_dir() / name


def profile_files(name: str) -> dict[str, Path]:
    """Canonical file layout of a profile (unchanged from v1)."""
    base = profile_dir(name)
    return {
        "dir": base,
        "prose_style": base / "prose-style.md",
        "template": base / "template.md",
        "meta": base / "meta.json",
        "rules": base / "rules.md",
        "sources_dir": base / "sources",
    }


def as_posix(path: Path) -> str:
    """POSIX form for the paths stored in `state.config` (CONVENTIONS)."""
    return str(path).replace("\\", "/")


# --- validation -----------------------------------------------------------


def validate_name(name: str) -> str | None:
    """Return None when the profile name is valid, else the error message (03 S-23)."""
    if not name:
        return "name is empty"
    if len(name) > NAME_MAX_LENGTH:
        return f"name too long ({len(name)} > {NAME_MAX_LENGTH})"
    if not NAME_PATTERN.match(name):
        return (
            "name must match [a-z0-9][a-z0-9_-]{0,63} (lowercase letters, digits, dashes, "
            "underscores; cannot start with - or _)"
        )
    return None


def validate_meta(meta: object) -> list[str]:
    """Check `meta.json` against the required keys and, when available, the `style-meta` schema."""
    if not isinstance(meta, dict):
        return ["meta.json is not a JSON object"]
    errors = []
    missing = REQUIRED_META_KEYS - set(meta.keys())
    if missing:
        errors.append(f"meta.json missing required keys: {sorted(missing)}")
    if meta.get("input_type") not in VALID_INPUT_TYPES:
        errors.append(
            f"meta.json input_type must be one of {list(VALID_INPUT_TYPES)}, got {meta.get('input_type')!r}"
        )
    if meta.get("mode_binding") not in VALID_MODE_BINDINGS:
        errors.append(
            f"meta.json mode_binding must be one of {list(VALID_MODE_BINDINGS)}, "
            f"got {meta.get('mode_binding')!r}"
        )
    if errors:
        return errors
    if schema.available():
        errors.extend(f"style-meta: {message}" for message in schema.validate(meta, META_SCHEMA))
    return errors


def validate_profile(name: str) -> tuple[bool, list[str]]:
    """Check the profile directory shape: `prose-style.md` + a schema-valid `meta.json`."""
    error = validate_name(name)
    if error:
        return False, [f"invalid profile name: {error}"]
    files = profile_files(name)
    if not files["dir"].is_dir():
        return False, [f"profile directory does not exist: {as_posix(files['dir'])}"]

    errors: list[str] = []
    if not files["prose_style"].is_file():
        errors.append("missing required file: prose-style.md")
    if not files["meta"].is_file():
        errors.append("missing required file: meta.json")
        return False, errors

    try:
        meta = state_io.read_json(files["meta"])
    except ValueError as exc:
        return False, errors + [f"meta.json is not valid JSON: {exc}"]
    errors.extend(validate_meta(meta))
    if isinstance(meta, dict):
        if meta.get("has_template") and not files["template"].is_file():
            errors.append("meta.json says has_template=true but template.md is missing")
        if not meta.get("has_template") and files["template"].is_file():
            errors.append("template.md exists but meta.json says has_template=false")
    return not errors, errors


# --- filesystem operations ------------------------------------------------


def ensure_dirs() -> Path:
    """Create the profiles directory if missing (no-op otherwise)."""
    directory = profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_meta(name: str) -> dict | None:
    """Parsed `meta.json` of a profile, or None when it is missing or unparseable."""
    path = profile_files(name)["meta"]
    if not path.is_file():
        return None
    try:
        meta = state_io.read_json(path)
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def list_profiles() -> list[dict]:
    """`{name, valid, errors, meta, is_default, dir}` per profile directory."""
    directory = profiles_dir()
    if not directory.is_dir():
        return []
    default_name = get_default()
    records: list[dict] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        valid, errors = validate_profile(entry.name)
        records.append(
            {
                "name": entry.name,
                "valid": valid,
                "errors": errors,
                "meta": read_meta(entry.name),
                "is_default": entry.name == default_name,
                "dir": as_posix(entry),
            }
        )
    return records


def get_default() -> str | None:
    """Current default profile name, or None."""
    path = default_file()
    if not path.is_file():
        return None
    try:
        name = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return name or None


def set_default(name: str) -> None:
    """Point `default-profile.txt` at an existing profile."""
    error = validate_name(name)
    if error:
        raise ValueError(f"invalid profile name: {error}")
    if not profile_dir(name).is_dir():
        raise FileNotFoundError(f"profile not found: {name}")
    ensure_dirs()
    state_io.write_bytes_atomic(default_file(), (name + "\n").encode("utf-8"))


def clear_default() -> None:
    """Remove `default-profile.txt` if present."""
    path = default_file()
    if path.is_file():
        path.unlink()


def delete_profile(name: str) -> None:
    """Remove a profile directory, clearing the default when it pointed at it."""
    error = validate_name(name)
    if error:
        raise ValueError(f"invalid profile name: {error}")
    target = profile_dir(name)
    if not target.is_dir():
        raise FileNotFoundError(f"profile not found: {name}")
    shutil.rmtree(target)
    if get_default() == name:
        clear_default()


def init_profile(
    name: str, input_type: str, mode_binding: str, rules_provided: bool = False
) -> dict[str, Path]:
    """Create an empty profile directory and a `meta.json` stub (called by `style-extractor`)."""
    error = validate_name(name)
    if error:
        raise ValueError(f"invalid profile name: {error}")
    if input_type not in VALID_INPUT_TYPES:
        raise ValueError(f"input_type must be one of {list(VALID_INPUT_TYPES)}, got {input_type!r}")
    if mode_binding not in VALID_MODE_BINDINGS:
        raise ValueError(
            f"mode_binding must be one of {list(VALID_MODE_BINDINGS)}, got {mode_binding!r}"
        )

    files = profile_files(name)
    files["dir"].mkdir(parents=True, exist_ok=True)
    files["sources_dir"].mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "created_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "input_type": input_type,
        "examples_count": 0,
        "rules_provided": rules_provided,
        "mode_binding": mode_binding,
        "has_template": False,
        "jurisdictions": [],
        "language": None,
        "confidence": None,
        "summary": "",
    }
    state_io.write_json_atomic(files["meta"], meta)
    return files


def write_meta(name: str, meta_json: str) -> dict:
    """Atomically replace `meta.json` from a JSON string, after validating it (§4.6)."""
    error = validate_name(name)
    if error:
        raise ValueError(f"invalid profile name: {error}")
    try:
        meta = json.loads(meta_json)
    except ValueError as exc:
        raise ValueError(f"meta is not valid JSON: {exc}") from exc
    errors = validate_meta(meta)
    if errors:
        raise ValueError("; ".join(errors))
    files = profile_files(name)
    if not files["dir"].is_dir():
        raise FileNotFoundError(f"profile not found: {name}")
    state_io.write_json_atomic(files["meta"], meta)
    return meta


def resolve_paths(name: str) -> dict:
    """POSIX paths written into `state.config` (`template_path` is None for a rules-only profile)."""
    error = validate_name(name)
    if error:
        raise ValueError(f"invalid profile name: {error}")
    files = profile_files(name)
    if not files["dir"].is_dir():
        raise FileNotFoundError(f"profile not found: {name}")
    meta = read_meta(name) or {}
    return {
        "style_profile": name,
        "style_profile_path": as_posix(files["dir"]),
        "prose_style_path": as_posix(files["prose_style"]),
        "template_path": as_posix(files["template"]) if files["template"].is_file() else None,
        "style_profile_mode_binding": meta.get("mode_binding"),
    }


# --- commands -------------------------------------------------------------


def _guard_name(args: argparse.Namespace) -> dict | None:
    """Every subcommand that takes a name validates it before touching the filesystem (03 S-23)."""
    error = validate_name(getattr(args, "name", "") or "")
    if error:
        return {"errors": [f"invalid_profile_name: {error}"], "name": getattr(args, "name", None)}
    return None


def _wrap(func, *args) -> dict:
    try:
        return func(*args)
    except FileNotFoundError as exc:
        return {"errors": [f"profile_not_found: {exc}"]}
    except (ValueError, OSError) as exc:
        return {"errors": [str(exc)]}


def run_list(args: argparse.Namespace) -> dict:
    """`mf style list`."""
    profiles = list_profiles()
    return {"profiles": profiles, "count": len(profiles), "default": get_default()}


def run_get_default(args: argparse.Namespace) -> dict:
    """`mf style get-default`."""
    return {"default": get_default()}


def run_set_default(args: argparse.Namespace) -> dict:
    """`mf style set-default <name>`."""
    guard = _guard_name(args)
    if guard:
        return guard
    def apply() -> dict:
        set_default(args.name)
        return {"default": args.name}

    return _wrap(apply)


def run_clear_default(args: argparse.Namespace) -> dict:
    """`mf style clear-default`."""
    clear_default()
    return {"default": None, "cleared": True}


def run_ensure_dirs(args: argparse.Namespace) -> dict:
    """`mf style ensure-dirs`."""
    return {"profiles_dir": as_posix(ensure_dirs())}


def run_validate_name(args: argparse.Namespace) -> dict:
    """`mf style validate-name <name>`."""
    error = validate_name(args.name or "")
    if error:
        return {"errors": [f"invalid_profile_name: {error}"], "name": args.name, "valid": False}
    return {"name": args.name, "valid": True}


def run_validate_profile(args: argparse.Namespace) -> dict:
    """`mf style validate-profile <name>`."""
    guard = _guard_name(args)
    if guard:
        return guard
    valid, errors = validate_profile(args.name)
    if not valid:
        return {"name": args.name, "valid": False, "errors": errors}
    return {"name": args.name, "valid": True}


def run_delete(args: argparse.Namespace) -> dict:
    """`mf style delete <name>`."""
    guard = _guard_name(args)
    if guard:
        return guard
    def apply() -> dict:
        delete_profile(args.name)
        return {"deleted": args.name}

    return _wrap(apply)


def run_read_meta(args: argparse.Namespace) -> dict:
    """`mf style read-meta <name>`."""
    guard = _guard_name(args)
    if guard:
        return guard
    meta = read_meta(args.name)
    if meta is None:
        return {"errors": [f"meta_not_found: {args.name}"], "name": args.name}
    return {"name": args.name, "meta": meta}


def run_resolve_paths(args: argparse.Namespace) -> dict:
    """`mf style resolve-paths <name>`."""
    guard = _guard_name(args)
    if guard:
        return guard
    return _wrap(resolve_paths, args.name)


def run_init_profile(args: argparse.Namespace) -> dict:
    """`mf style init-profile <name> <input_type> <mode_binding>`."""
    guard = _guard_name(args)
    if guard:
        return guard

    def create() -> dict:
        files = init_profile(
            args.name, args.input_type, args.mode_binding, rules_provided=args.rules_provided
        )
        return {
            "name": args.name,
            "created": True,
            "dir": as_posix(files["dir"]),
            "meta": as_posix(files["meta"]),
        }

    return _wrap(create)


def run_write_meta(args: argparse.Namespace) -> dict:
    """`mf style write-meta <name> <json>`."""
    guard = _guard_name(args)
    if guard:
        return guard
    def apply() -> dict:
        return {"name": args.name, "meta": write_meta(args.name, args.meta_json)}

    return _wrap(apply)


def register(subparsers) -> None:
    """Register the twelve `mf style` subcommands (§0.4: 11 v1 call sites map onto these)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "style", "user style profiles (§4.6)")

    group.add_parser("list", help="list profiles with metadata").set_defaults(func=run_list)
    group.add_parser("get-default", help="print the default profile name").set_defaults(
        func=run_get_default
    )
    group.add_parser("clear-default", help="remove default-profile.txt").set_defaults(
        func=run_clear_default
    )
    group.add_parser("ensure-dirs", help="create the profiles directory").set_defaults(
        func=run_ensure_dirs
    )

    for name, help_text, func in (
        ("set-default", "set the default profile", run_set_default),
        ("validate-name", "check a profile name", run_validate_name),
        ("validate-profile", "check a profile directory", run_validate_profile),
        ("delete", "delete a profile directory", run_delete),
        ("read-meta", "print meta.json", run_read_meta),
        ("resolve-paths", "print the paths for state.config", run_resolve_paths),
    ):
        parser = group.add_parser(name, help=help_text)
        parser.add_argument("name")
        parser.set_defaults(func=func)

    init = group.add_parser("init-profile", help="create an empty profile and a meta.json stub")
    init.add_argument("name")
    init.add_argument("input_type", choices=list(VALID_INPUT_TYPES))
    init.add_argument("mode_binding", choices=list(VALID_MODE_BINDINGS))
    init.add_argument("--rules-provided", dest="rules_provided", action="store_true")
    init.set_defaults(func=run_init_profile)

    write = group.add_parser("write-meta", help="replace meta.json from a JSON string")
    write.add_argument("name")
    write.add_argument("meta_json", help="full meta.json content as a JSON string")
    write.set_defaults(func=run_write_meta)
