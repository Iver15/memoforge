"""`mf docx validate` — the structural checks of ТЗ §5.5 over a rendered `memo-<slug>.docx`.

Not an OPC/XSD validation (§0.3a minimalism): the archive must open with python-docx, carry the
`footnotes.xml` relationship and the `FootnoteText`/`FootnoteReference` styles, pair every
`w:footnoteReference` with exactly one footnote of the same id, resolve every `w:hyperlink` against
a hyperlink relationship of `document.xml.rels` (D-150: the inline citation style links every
citation), hold as many footnotes as the renderer resolved mentions — none at all in the inline
style — point every short form at the first footnote of its own `source_id`,
carry the `Status` section when the run did not end approved (D34-11) and contain no literal
`[[src:` / `[[q:` / `[unresolved:` token in any part. A failure renames the file to
`memo-<slug>.invalid.docx`, makes the markdown fallback the deliverable and raises `docx_invalid`.

D-51: `[unresolved:` is unconditional — the `unresolved_reference` banner explains the markdown
fallback, it does not license the literal inside a docx — and the `footnotes-map.json` of the render
step is required: without it the count and short-form checks cannot run, so its absence is an error
of its own instead of a silent skip.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .. import i18n
from . import fallback

FOOTNOTES_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
)
HYPERLINK_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
DOCUMENT_RELS = "word/_rels/document.xml.rels"
DOCUMENT_PART = "word/document.xml"
FOOTNOTES_PART = "word/footnotes.xml"
STYLES_PART = "word/styles.xml"

REQUIRED_STYLES: tuple[str, ...] = ("FootnoteText", "FootnoteReference")

LITERAL_TOKENS: tuple[str, ...] = ("[[src:", "[[q:")
UNRESOLVED_TOKEN = "[unresolved:"

HYPERLINK_RE = re.compile(r'<w:hyperlink[^>]*\sr:id="([^"]+)"')
RELATIONSHIP_RE = re.compile(r"<Relationship\b([^>]*)>")
RELATIONSHIP_ID_RE = re.compile(r'\bId="([^"]+)"')
RELATIONSHIP_TYPE_RE = re.compile(r'\bType="([^"]+)"')
"""D-150: the inline citation style makes every citation a `w:hyperlink`; a link whose `r:id` is not
in `document.xml.rels` is dead in Word, exactly the way a `w:footnoteReference` without its footnote
is — so it is checked the same way."""

FOOTNOTE_REFERENCE_RE = re.compile(r'<w:footnoteReference[^>]*\sw:id="(-?[0-9]+)"')
FOOTNOTE_OPEN_RE = re.compile(r"<w:footnote(?![A-Za-z])([^>]*)>")
"""The opening tag of a `w:footnote`; `(?![A-Za-z])` keeps `w:footnoteRef(erence)` out."""
ATTR_ID_RE = re.compile(r'\bw:id="(-?[0-9]+)"')
ATTR_TYPE_RE = re.compile(r'\bw:type="[^"]+"')
RUN_TEXT_RE = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")
STYLE_ID_RE = re.compile(r'w:styleId="([^"]+)"')
SHORT_FORM_RE = re.compile(r"\(n\s+([0-9]+)\)")


def status_run(language: str = i18n.DEFAULT) -> str:
    """The run `renderer._render_status` writes as the Status heading, in the memo language (D34-11).

    The render step records in `footnotes-map.json` whether this run owed the reader that section;
    a docx that owes it and does not carry it is invalid, so the markdown — which always carries it
    — becomes the deliverable instead. D-175: the word looked for is the one the memo was written
    in, otherwise every non-English export would fail this check.
    """
    return f"<w:t>{escape(fallback.label('status_label', language))}</w:t>"


TEXT_PART_SUFFIXES: tuple[str, ...] = (".xml", ".rels")
"""Parts the literal-token scan reads; `.rels` carries the footnotes relationship (§5.5)."""

E_UNREADABLE = "docx_unreadable"
E_NO_FOOTNOTES_RELATIONSHIP = "missing_footnotes_relationship"
E_NO_FOOTNOTES_PART = "missing_footnotes_part"
E_ORPHAN_REFERENCE = "footnote_reference_without_footnote"
E_DUPLICATE_FOOTNOTE = "duplicate_footnote_id"
E_COUNT_MISMATCH = "footnote_count_mismatch"
E_SHORT_FORM_MISMATCH = "short_form_points_at_another_source"
E_LITERAL_TOKEN = "literal_citation_token"
E_UNRESOLVED_TOKEN = "unresolved_marker"
E_MISSING_STYLE = "missing_footnote_style"
E_MAP_MISSING = "footnotes_map_missing"
E_MISSING_STATUS = "status_section_missing"
E_BROKEN_HYPERLINK = "hyperlink_relationship_unresolved"


def _parts(archive: zipfile.ZipFile) -> dict[str, str]:
    """Every XML part of the package as text; the literal-token check reads all of them (§5.5)."""
    parts: dict[str, str] = {}
    for name in archive.namelist():
        if not name.lower().endswith(TEXT_PART_SUFFIXES):
            continue
        try:
            parts[name] = archive.read(name).decode("utf-8", errors="replace")
        except (KeyError, OSError):  # pragma: no cover - unreadable member of a readable zip
            continue
    return parts


def _opens_with_python_docx(path: Path) -> str | None:
    try:
        from docx import Document
    except ImportError:  # pragma: no cover - `docx validate` only runs on the renderer branch
        return None
    try:
        Document(str(path))
    except Exception as exc:  # noqa: BLE001 - any open failure is the same verdict (§5.5)
        return f"{type(exc).__name__}: {exc}"
    return None


def validate_path(
    path: str | Path, *, footnotes_map: dict | None = None, language: str = i18n.DEFAULT
) -> dict:
    """Run the §5.5 checks over one docx; `valid` is False as soon as `errors` is non-empty."""
    path = Path(path)
    errors: list[str] = []
    details: list[str] = []
    # `warnings` stays in the answer for a stable shape; since D-51 every §5.5 deviation is an error.

    if not path.is_file():
        return {
            "valid": False,
            "path": str(path),
            "exists": False,
            "errors": [E_UNREADABLE],
            "warnings": [],
            "details": ["file not found"],
            "footnotes": 0,
            "references": 0,
        }

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return {
            "valid": False,
            "path": str(path),
            "exists": True,
            "errors": [E_UNREADABLE],
            "warnings": [],
            "details": [f"{type(exc).__name__}: {exc}"],
            "footnotes": 0,
            "references": 0,
        }

    with archive:
        parts = _parts(archive)

    open_error = _opens_with_python_docx(path)
    if open_error:
        errors.append(E_UNREADABLE)
        details.append(open_error)

    rels = parts.get(DOCUMENT_RELS, "")
    if FOOTNOTES_RELATIONSHIP not in rels:
        errors.append(E_NO_FOOTNOTES_RELATIONSHIP)

    document = parts.get(DOCUMENT_PART, "")
    references = [int(value) for value in FOOTNOTE_REFERENCE_RE.findall(document)]

    if FOOTNOTES_PART not in parts:
        errors.append(E_NO_FOOTNOTES_PART)
        footnote_ids: list[int] = []
    else:
        footnote_ids = [number for number, _ in _footnotes(parts[FOOTNOTES_PART])]

    counted = {value: footnote_ids.count(value) for value in set(footnote_ids)}
    duplicates = sorted(value for value, count in counted.items() if count > 1)
    if duplicates:
        errors.append(E_DUPLICATE_FOOTNOTE)
        details.append(f"footnote ids declared more than once: {duplicates}")
    orphans = sorted({value for value in references if counted.get(value, 0) != 1})
    if orphans:
        errors.append(E_ORPHAN_REFERENCE)
        details.append(f"references without exactly one footnote: {orphans}")

    errors.extend(_check_hyperlinks(document, rels, details))

    expected = footnotes_map.get("footnotes") if isinstance(footnotes_map, dict) else None
    if not isinstance(expected, list):
        # D-51: without the render step's map the count and short-form checks are unverifiable,
        # and an unverifiable check is a failed one, never a skipped one.
        errors.append(E_MAP_MISSING)
        details.append("footnotes-map.json of the render step is missing or unreadable")
    else:
        if len(footnote_ids) != len(expected):
            errors.append(E_COUNT_MISMATCH)
            details.append(f"{len(footnote_ids)} footnotes for {len(expected)} resolved mentions")
        errors.extend(_check_short_forms(expected, parts.get(FOOTNOTES_PART, ""), details))

    # D34-11: a run whose `final_status` is not an approved one owes the reader a `Status` section.
    if isinstance(footnotes_map, dict) and footnotes_map.get("status_required"):
        if status_run(language) not in document:
            errors.append(E_MISSING_STATUS)
            details.append(
                "final_status is not an approved one, but the document carries no Status section"
            )

    styles = parts.get(STYLES_PART, "")
    declared = set(STYLE_ID_RE.findall(styles))
    for style in REQUIRED_STYLES:
        if style not in declared:
            errors.append(E_MISSING_STYLE)
            details.append(f"style {style} is not declared in styles.xml")

    for name, text in sorted(parts.items()):
        for token in LITERAL_TOKENS:
            if token in text:
                errors.append(E_LITERAL_TOKEN)
                details.append(f"{name} still contains {token}")
        if UNRESOLVED_TOKEN in text:
            # D-51: no banner makes the literal acceptable inside the docx (§5.5).
            errors.append(E_UNRESOLVED_TOKEN)
            details.append(f"{name} contains {UNRESOLVED_TOKEN}")

    return {
        "valid": not errors,
        "path": str(path),
        "exists": True,
        "errors": _dedupe(errors),
        "warnings": [],
        "details": details,
        "footnotes": len(footnote_ids),
        "references": len(references),
    }


def _check_hyperlinks(document: str, rels: str, details: list[str]) -> list[str]:
    """Every `w:hyperlink` of the body must name a hyperlink relationship of the part (D-150)."""
    used = HYPERLINK_RE.findall(document)
    if not used:
        return []
    declared: dict[str, str] = {}
    for match in RELATIONSHIP_RE.finditer(rels):
        identifier = RELATIONSHIP_ID_RE.search(match.group(1))
        kind = RELATIONSHIP_TYPE_RE.search(match.group(1))
        if identifier is not None:
            declared[identifier.group(1)] = kind.group(1) if kind is not None else ""
    broken = sorted(
        {value for value in used if declared.get(value) != HYPERLINK_RELATIONSHIP}
    )
    if not broken:
        return []
    details.append(f"hyperlinks without a relationship in {DOCUMENT_RELS}: {broken}")
    return [E_BROKEN_HYPERLINK]


def _instrument_of(row: dict) -> str:
    """The work a footnote row belongs to (D-150); the `source_id` for a map written before it."""
    return str(row.get("instrument") or row.get("source_id"))


def _check_short_forms(expected: list, footnotes_xml: str, details: list[str]) -> list[str]:
    """Every `<short name> (n N)` must name the first footnote of its own instrument (§5.5, D-150)."""
    errors: list[str] = []
    first_of: dict[str, int] = {}
    for row in expected:
        if isinstance(row, dict) and row.get("form") == "full":
            first_of.setdefault(_instrument_of(row), int(row.get("n") or 0))
    texts = dict(_footnotes(footnotes_xml))
    for row in expected:
        if not isinstance(row, dict) or row.get("form") != "short":
            continue
        number = int(row.get("n") or 0)
        instrument = _instrument_of(row)
        claimed = int(row.get("first_n") or 0)
        rendered = SHORT_FORM_RE.search(texts.get(number, ""))
        rendered_n = int(rendered.group(1)) if rendered else None
        if claimed != first_of.get(instrument) or rendered_n != claimed:
            errors.append(E_SHORT_FORM_MISMATCH)
            details.append(
                f"footnote {number} ({instrument}) points at n {rendered_n or claimed}, "
                f"the first footnote of that instrument is {first_of.get(instrument)}"
            )
    return errors


def _footnotes(footnotes_xml: str) -> list[tuple[int, str]]:
    """`(id, concatenated w:t text)` of every footnote that is not a Word separator."""
    rows: list[tuple[int, str]] = []
    blocks = re.split(r"(?=<w:footnote(?![A-Za-z]))", footnotes_xml)
    for block in blocks:
        opening = FOOTNOTE_OPEN_RE.match(block)
        if opening is None or ATTR_TYPE_RE.search(opening.group(1)):
            continue
        number = ATTR_ID_RE.search(opening.group(1))
        if number is None:
            continue
        rows.append((int(number.group(1)), "".join(RUN_TEXT_RE.findall(block))))
    return rows


def _dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
