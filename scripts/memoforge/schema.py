"""Thin wrapper over pip `jsonschema` (Draft 2020-12) for the schemas in `<root>/schemas` (ТЗ §5.1, §6)."""

from __future__ import annotations

import functools
import json
from pathlib import Path

SCHEMA_SUFFIX = ".schema.json"


class DependencyMissing(RuntimeError):
    """Raised when `jsonschema` is not installed; carries the `mf deps install` hint (§5.6)."""

    code = "jsonschema_missing"
    hint = "run `mf deps install` (requirements.txt: jsonschema>=4.18)"

    def __init__(self, message: str = "jsonschema is not installed") -> None:
        super().__init__(f"{self.code}: {message}; {self.hint}")


class SchemaValidationError(ValueError):
    """Raised by `validate_or_raise`; `.errors` holds the human-readable messages."""

    def __init__(self, schema_name: str, errors: list[str]) -> None:
        self.schema_name = schema_name
        self.errors = list(errors)
        super().__init__(f"schema_invalid[{schema_name}]: " + "; ".join(self.errors))


def schemas_dir() -> Path:
    """`<plugin_root>/schemas` — the only place schemas are loaded from (§6)."""
    return Path(__file__).resolve().parents[2] / "schemas"


def schema_path(name: str) -> Path:
    """Path of one schema file by its short name (`state` -> `state.schema.json`)."""
    if name.endswith(SCHEMA_SUFFIX):
        name = name[: -len(SCHEMA_SUFFIX)]
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise ValueError(f"invalid_schema_name: {name!r}")
    return schemas_dir() / f"{name}{SCHEMA_SUFFIX}"


def available() -> bool:
    """True when `jsonschema` can be imported (commands of §5.6 run without it)."""
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return False
    return True


def known_schemas() -> list[str]:
    """Short names of every schema shipped in `<plugin_root>/schemas`."""
    return sorted(p.name[: -len(SCHEMA_SUFFIX)] for p in schemas_dir().glob("*" + SCHEMA_SUFFIX))


@functools.lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    """Load and cache one schema document."""
    path = schema_path(name)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ValueError(f"unknown_schema: {name!r}") from exc
    return json.loads(text)


@functools.lru_cache(maxsize=None)
def _validator(name: str):
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - exercised only without the dependency
        raise DependencyMissing(str(exc)) from exc
    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _format_error(error) -> str:
    location = "/".join(str(part) for part in error.absolute_path)
    return f"{location or '<root>'}: {error.message}"


def validate(obj: object, schema_name: str) -> list[str]:
    """Return the list of validation errors; an empty list means valid (CONVENTIONS)."""
    validator = _validator(schema_name)
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    return [_format_error(error) for error in errors]


def validate_or_raise(obj: object, schema_name: str) -> None:
    """Raise `SchemaValidationError` when `obj` does not satisfy the named schema."""
    errors = validate(obj, schema_name)
    if errors:
        raise SchemaValidationError(schema_name, errors)
