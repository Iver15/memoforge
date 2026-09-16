"""`mf quote extract|skip` — the exact, sentence-bounded quote contract (ТЗ §5.3, M5)."""

from __future__ import annotations

import argparse
import difflib
import re
import unicodedata
from pathlib import Path

from . import events, limits, schema, sources, state_io

QUOTES_PATH = "research/quotes.json"

SKIP_REASONS: tuple[str, ...] = (
    "too_long",
    "ambiguous",
    "not_found",
    "no_raw",
    "raw_changed",
    "already_used",
    "other",
)

EXTRACT_ERRORS: tuple[str, ...] = ("not_found", "ambiguous", "too_long", "no_raw", "raw_changed")

# --- narrow typographic normalisation (§5.3: NFKC, whitespace, quotes, dashes, ellipses) ---

TYPOGRAPHIC: dict[str, str] = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "«": '"',
    "»": '"',
    "″": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "′": "'",
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "…": "...",
}

ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "art", "arts", "artt", "no", "nos", "nr", "para", "paras", "pt", "p", "pp", "ch",
        "sec", "secs", "ss", "cf", "eg", "ie", "etc", "al", "fig", "vs", "reg", "dir",
        "ст", "стт", "п", "пп", "гл", "абз", "см", "др", "ред",
    }
)

_BLOCK_START = re.compile(r"^(?:[#>*+|]|-\s|\d+[.)]\s|\(\w{1,3}\)\s)")


def normalize(text: str) -> tuple[str, list[int]]:
    """Narrow normalisation with an index map back to the original offsets (§5.3)."""
    out: list[str] = []
    index: list[int] = []
    for position, char in enumerate(text):
        replacement = TYPOGRAPHIC.get(char)
        if replacement is None:
            replacement = unicodedata.normalize("NFKC", char)
        if replacement == "":
            continue
        if not replacement.strip():
            if out and out[-1] == " ":
                continue
            out.append(" ")
            index.append(position)
            continue
        for produced in replacement:
            out.append(produced)
            index.append(position)
    return "".join(out), index


def normalized_text(text: str) -> str:
    """Normalised form only — used by the citation audit for the exact comparison of C-02."""
    return normalize(text)[0].strip()


def count_words(text: str) -> int:
    """Words of a quote: whitespace-separated tokens of its normalised form."""
    return len(normalized_text(text).split())


def detect_lang(text: str) -> str:
    """Best-effort language tag of a quote (the source language is kept, §4.4)."""
    return "ru" if re.search(r"[Ѐ-ӿ]", text) else "en"


# --- sentence boundaries --------------------------------------------------


def _is_hard_break(text: str, newline: int) -> bool:
    """A line break bounds a sentence only at a block boundary, never inside soft-wrapped prose."""
    before = text.rfind("\n", 0, newline)
    line = text[before + 1 : newline].rstrip()
    if line.endswith(":"):
        return True
    rest = text[newline + 1 :]
    stripped = rest.lstrip(" \t")
    if stripped.startswith("\n") or not stripped:
        return True
    return bool(_BLOCK_START.match(stripped)) or bool(_BLOCK_START.match(line))


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """`[start, end)` of every sentence; block boundaries and terminal punctuation both close one."""
    spans: list[tuple[int, int]] = []
    start = 0
    position = 0
    length = len(text)
    while position < length:
        char = text[position]
        if char in ".!?…":
            after = position + 1
            while after < length and text[after] in "\"')]»”’":
                after += 1
            probe = after
            while probe < length and text[probe] in " \t":
                probe += 1
            ends = probe >= length or text[probe] == "\n" or probe > after
            if ends and char == "." :
                token = re.search(r"([\w§]+)[\s(]*$", text[:position])
                word = token.group(1).lower() if token else ""
                if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                    position = after
                    continue
            if ends:
                if text[start:after].strip():
                    spans.append((start, after))
                while after < length and text[after].isspace():
                    after += 1
                start = after
                position = after
                continue
            position = after
            continue
        if char == "\n" and _is_hard_break(text, position):
            if text[start:position].strip():
                spans.append((start, position))
            after = position + 1
            while after < length and text[after].isspace():
                after += 1
            start = after
            position = after
            continue
        position += 1
    if text[start:].strip():
        spans.append((start, length))
    return spans


def trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Move the boundaries of a span off surrounding whitespace and markdown markers."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def expand_to_sentences(spans: list[tuple[int, int]], start: int, end: int) -> tuple[int, int]:
    """Smallest sentence-bounded span covering `[start, end)` (§5.3 extraction contract)."""
    covering = [span for span in spans if span[1] > start and span[0] < end]
    if not covering:
        return start, end
    return covering[0][0], covering[-1][1]


# --- fuzzy candidates (never part of a successful audit, §5.3) ------------


def _ratio(needle: str, candidate: str) -> float:
    try:
        from rapidfuzz import fuzz  # type: ignore
    except ImportError:
        return difflib.SequenceMatcher(None, needle, candidate).ratio()
    return float(fuzz.partial_ratio(needle, candidate)) / 100.0


def _candidate(text: str, start: int, end: int) -> dict:
    start, end = trim_span(text, start, end)
    fragment = text[start:end]
    return {
        "text": fragment,
        "char_start": start,
        "char_end": end,
        "words": count_words(fragment),
    }


def nearest_candidates(text: str, spans: list[tuple[int, int]], needle: str, max_words: int) -> list[dict]:
    """Sentence-bounded fragments closest to a `not_found` request, best first."""
    target = normalized_text(needle)
    scored = []
    for start, end in spans:
        fragment = text[start:end]
        if count_words(fragment) > max_words:
            continue
        scored.append((_ratio(target, normalized_text(fragment)), start, end))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [_candidate(text, start, end) for _, start, end in scored[: limits.QUOTE_CANDIDATES_MAX]]


def shorter_candidates(text: str, spans: list[tuple[int, int]], start: int, end: int, max_words: int) -> list[dict]:
    """Maximal runs of consecutive sentences inside the match that still fit `max_words` (§5.3)."""
    inside = [span for span in spans if span[0] >= start and span[1] <= end]
    if not inside:
        return []
    runs: list[tuple[int, int, int]] = []
    for first in range(len(inside)):
        best: tuple[int, int, int] | None = None
        for last in range(first, len(inside)):
            span_start = inside[first][0]
            span_end = inside[last][1]
            words = count_words(text[span_start:span_end])
            if words > max_words:
                break
            best = (words, span_start, span_end)
        if best is not None:
            runs.append(best)
    runs.sort(key=lambda row: (-row[0], row[1]))
    seen: set[tuple[int, int]] = set()
    result = []
    for _, span_start, span_end in runs:
        key = (span_start, span_end)
        if key in seen:
            continue
        seen.add(key)
        result.append(_candidate(text, span_start, span_end))
        if len(result) >= limits.QUOTE_CANDIDATES_MAX:
            break
    return result


# --- registry io ----------------------------------------------------------


def quotes_path(work_dir: str | Path) -> Path:
    """`<work_dir>/research/quotes.json`."""
    return Path(work_dir) / QUOTES_PATH


def read_quotes(work_dir: str | Path) -> dict:
    """Read the quote registry, returning an empty one when it does not exist."""
    path = quotes_path(work_dir)
    if not path.is_file():
        return {"quotes": {}, "skips": []}
    data = state_io.read_json(path)
    if not isinstance(data, dict):
        raise ValueError("quotes_registry_malformed")
    data.setdefault("quotes", {})
    data.setdefault("skips", [])
    return data


def write_quotes(work_dir: str | Path, registry: dict) -> Path:
    """Validate and atomically write `quotes.json` (caller holds `sources.lock`)."""
    schema.validate_or_raise(registry, "quotes")
    return state_io.write_json_atomic(quotes_path(work_dir), registry)


def next_quote_id(registry: dict, source_id: str) -> str:
    """`q-<source_id>-<n>`, unique in the registry and within the 80-character id limit."""
    base = f"q-{source_id}"[:76]
    index = 1
    while f"{base}-{index}" in registry["quotes"]:
        index += 1
    return f"{base}-{index}"


def skip_for(registry: dict, section_id: str, source_id: str) -> dict | None:
    """The recorded `quote skip` of one (section, source) pair, if any (L-08)."""
    for row in registry.get("skips") or []:
        if row.get("section_id") == section_id and row.get("source_id") == source_id:
            return row
    return None


# --- extract --------------------------------------------------------------


def raw_state(work_dir: str | Path, source_id: str) -> dict:
    """Resolve the raw file a quote must come from, honouring the freeze snapshot (M6)."""
    registry = sources.read_registry(work_dir)
    record = registry["sources"].get(source_id)
    if record is None:
        return {"error": "unknown_source"}
    raw_path = record.get("raw_path")
    if not raw_path:
        return {"error": "no_raw"}
    path = Path(work_dir) / raw_path
    if not path.is_file():
        return {"error": "no_raw"}
    current = state_io.sha256_file(path)
    frozen = sources.is_frozen(work_dir)
    if frozen:
        # D-143: the freeze may have collapsed this id into a duplicate's canonical entry; the
        # snapshot pins that id, while the alias's own raw file still holds the very same bytes.
        canonical = sources.canonical_id(sources.merged_map(work_dir), source_id)
        pinned = sources.snapshot_map(work_dir).get(canonical)
        if pinned is None:
            return {"error": "no_raw"}
        if pinned != current:
            return {"error": "raw_changed", "expected": pinned, "actual": current}
    else:
        pinned = record.get("raw_sha256")
        if pinned and pinned != current:
            return {"error": "raw_changed", "expected": pinned, "actual": current}
    return {
        "record": record,
        "path": path,
        "raw_sha256": current,
        "text": path.read_text(encoding="utf-8-sig", errors="replace"),
        "frozen": frozen,
    }


def extract_quote(
    work_dir: str | Path,
    source_id: str,
    text: str,
    *,
    max_words: int | None = None,
    lang: str | None = None,
) -> dict:
    """The §5.3 extraction contract: exact, sentence-bounded, `<= max_words`; else a typed error."""
    max_words = limits.QUOTE_DEFAULT_MAX_WORDS if max_words is None else int(max_words)
    if not str(text).strip():
        return {"errors": ["not_found"], "error": "not_found", "source_id": source_id, "candidates": []}

    resolved = raw_state(work_dir, source_id)
    if resolved.get("error"):
        result = {"errors": [resolved["error"]], "error": resolved["error"], "source_id": source_id}
        for key in ("expected", "actual"):
            if key in resolved:
                result[key] = resolved[key]
        return result

    raw = resolved["text"]
    haystack, index = normalize(raw)
    needle = normalized_text(text)
    spans = sentence_spans(raw)

    matches: list[tuple[int, int]] = []
    position = haystack.find(needle)
    while position != -1:
        end = position + len(needle)
        matches.append((index[position], index[end - 1] + 1))
        position = haystack.find(needle, position + 1)

    if not matches:
        return {
            "errors": ["not_found"],
            "error": "not_found",
            "source_id": source_id,
            "candidates": nearest_candidates(raw, spans, text, max_words),
        }
    if len(matches) > 1:
        return {
            "errors": ["ambiguous"],
            "error": "ambiguous",
            "source_id": source_id,
            "positions": [{"char_start": start, "char_end": end} for start, end in matches],
            "matches": len(matches),
        }

    start, end = expand_to_sentences(spans, *matches[0])
    start, end = trim_span(raw, start, end)
    fragment = raw[start:end]
    words = count_words(fragment)
    if words > max_words:
        return {
            "errors": ["too_long"],
            "error": "too_long",
            "source_id": source_id,
            "words": words,
            "max_words": max_words,
            "char_start": start,
            "char_end": end,
            "candidates": shorter_candidates(raw, spans, start, end, max_words),
        }

    quote = {
        "source_id": source_id,
        "raw_sha256": resolved["raw_sha256"],
        "text": fragment,
        "char_start": start,
        "char_end": end,
        "lang": lang or detect_lang(fragment),
        "words": words,
    }

    with sources.sources_lock(work_dir):
        registry = read_quotes(work_dir)
        for quote_id, existing in registry["quotes"].items():
            if (
                existing.get("source_id") == source_id
                and existing.get("raw_sha256") == quote["raw_sha256"]
                and existing.get("char_start") == start
                and existing.get("char_end") == end
            ):
                return {"quote_id": quote_id, "existing": True, **quote}
        quote_id = next_quote_id(registry, source_id)
        registry["quotes"][quote_id] = quote
        write_quotes(work_dir, registry)
    return {"quote_id": quote_id, "existing": False, **quote}


def run_extract(args: argparse.Namespace) -> dict:
    """`mf quote extract --source <id> --text "<fragment>"`."""
    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8-sig")
    if text is None:
        return {"errors": ["missing_text"], "hint": "pass --text or --text-file"}
    return extract_quote(args.workdir, args.source, text, max_words=args.max_words, lang=args.lang)


# --- skip -----------------------------------------------------------------


def record_skip(
    work_dir: str | Path,
    section_id: str,
    source_id: str,
    reason: str,
    note: str | None = None,
) -> dict:
    """`mf quote skip` — a deliberate refusal to quote; `other` also raises a drafting warning (§5.3)."""
    if reason not in SKIP_REASONS:
        return {"errors": [f"unknown_reason: {reason!r}"], "allowed": list(SKIP_REASONS)}
    if reason == "other" and not (note or "").strip():
        return {"errors": ["note_required_for_other"]}

    entry = {
        "section_id": section_id,
        "source_id": source_id,
        "reason": reason,
        "at": events.utc_now(),
    }
    if note and note.strip():
        entry["note"] = note.strip()

    def write_registry() -> bool:
        with sources.sources_lock(work_dir):
            registry = read_quotes(work_dir)
            rows = [
                row
                for row in registry["skips"]
                if not (row.get("section_id") == section_id and row.get("source_id") == source_id)
            ]
            replaced = len(rows) != len(registry["skips"])
            rows.append(entry)
            registry["skips"] = rows
            write_quotes(work_dir, registry)
        return replaced

    if reason == "other":
        # `state.lock` first, then `sources.lock` — the nesting order of §2.2.
        with state_io.FileLock(state_io.lock_path(work_dir, "state")):
            replaced = write_registry()

            def mutate(state: dict) -> None:
                warnings = list(state.get("drafting_warnings") or [])
                message = f"quote skip ({section_id}/{source_id}): {entry.get('note')}"
                if message not in warnings:
                    warnings.append(message)
                state["drafting_warnings"] = warnings

            state_io.write_state(work_dir, mutate)
    else:
        replaced = write_registry()

    return {"skip": entry, "replaced": replaced}


def run_skip(args: argparse.Namespace) -> dict:
    """`mf quote skip --section <id> --source <id> --reason <enum> [--note …]`."""
    return record_skip(args.workdir, args.section, args.source, args.reason, args.note)


# --- CLI ------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the `quote` command group (§5.2)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "quote", "verbatim quotes extracted from saved raw text")

    extract = group.add_parser("extract", help="extract an exact, sentence-bounded quote")
    extract.add_argument("--workdir", required=True)
    extract.add_argument("--source", required=True)
    extract.add_argument("--text", default=None, help="fragment to locate in the raw file")
    extract.add_argument("--text-file", dest="text_file", default=None)
    extract.add_argument("--max-words", dest="max_words", type=int, default=limits.QUOTE_DEFAULT_MAX_WORDS)
    extract.add_argument("--lang", default=None)
    extract.set_defaults(func=run_extract)

    skip = group.add_parser("skip", help="record a deliberate refusal to quote a source in a section")
    skip.add_argument("--workdir", required=True)
    skip.add_argument("--section", required=True)
    skip.add_argument("--source", required=True)
    skip.add_argument("--reason", required=True, choices=list(SKIP_REASONS))
    skip.add_argument("--note", default=None, help="mandatory when --reason other")
    skip.set_defaults(func=run_skip)
