"""`mistune` AST -> python-docx renderer with real Word footnotes (ТЗ §5.5).

The main branch of `mf docx render`: the draft is parsed into a `mistune` AST and written through
python-docx with the v1 visual spec (Arial 12, 1" margins, justified, 6pt after, cyrillic `rFonts`),
every `[[src:<id> <pinpoint>]]` and every blockquote `[[q:<quote_id>]]` becomes a real Word footnote
(`word/footnotes.xml` + the `document.xml.rels` relationship + the `FootnoteText`/`FootnoteReference`
styles) — the citation of a blockquote as an attribution line under the quote, never inside it
(D34-22) — the §Sources section is generated at the `<!-- sources: generated -->` marker, a `Status`
section states the banners and the unresolved blockers of a run that did not end approved (D34-11)
and the «Assumptions & Unverified Sources» appendix comes from `drafting_warnings` plus the
per-source `verification`/`currency`/`liveness` records — the same data the stdlib fallback uses.

Importing this module requires `python-docx` and `mistune`; `docx/__init__.py` catches the
`ImportError` and takes the stdlib fallback branch instead (§5.5, §5.6).
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

import mistune
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Inches, Pt, Twips

from .. import fallbacks, state_io, stepctx
from . import fallback, oscola

# --- visual spec (ported from docs/attic/v1-docx-render/scripts/md_to_docx.py) ---

FONT_NAME = "Arial"
CODE_FONT_NAME = "Consolas"
FONT_SIZE_BODY = Pt(12)
FONT_SIZE_QUOTE = Pt(11)
FONT_SIZE_FOOTNOTE = Pt(10)
MARGIN = Inches(1)
INDENT_FIRSTLINE_BODY = Cm(1.11)
INDENT_LEFT_BLOCKQUOTE = Cm(1.59)
INDENT_LEFT_LIST_DXA = 720
"""0.5" per list level; the marker sits at `left - hanging(360)` (v1 spec)."""
SPACING_BEFORE = Pt(0)
SPACING_AFTER = Pt(6)
LINE_SPACING_VALUE = 1.0

WARNING_BG = "FFF3CD"
WARNING_BORDER = "FFE69C"

STYLE_FOOTNOTE_TEXT = "FootnoteText"
STYLE_FOOTNOTE_REFERENCE = "FootnoteReference"

FOOTNOTES_PARTNAME = "/word/footnotes.xml"

NUM_ID_BASE = 1000
ABSTRACT_NUM_ID_BASE = 900
"""Fixed high bases so `w:numId` values do not drift when the python-docx template changes."""

MAX_LIST_DEPTH = 2
"""`List Bullet`/`List Bullet 2`/`List Bullet 3` — deeper items reuse level 3 (§5.5 «вложенные»)."""

SOURCES_HEADING = "Sources"
APPENDIX_HEADING = fallback.APPENDIX_HEADING.lstrip("# ").strip()
STATUS_HEADING = fallback.STATUS_LABEL
"""D34-11: the same heading text the markdown deliverable prints, and what `docx validate` looks for."""

MENTION_OPEN = fallback.MENTION_OPEN
MENTION_CLOSE = fallback.MENTION_CLOSE
MENTION_RE = fallback.MENTION_RE
"""Private-use sentinel that carries a citation token through the markdown parser untouched.

D-150: the scan, the numbering and the choice of form live in `fallback.py`/`oscola.py`, so the two
deliverables cannot disagree about a citation; this module only writes the sentinels into Word."""

HYPERLINK_COLOR = "0563C1"
"""The blue of Word's own `Hyperlink` style; the inline citations are links, not footnotes (D-150)."""

_markdown = mistune.create_markdown(renderer=None, plugins=["table"])


class RenderError(RuntimeError):
    """The renderer could not produce a docx; `docx render` falls back to markdown (§5.5)."""


# --- low-level OXML helpers ------------------------------------------------


def _set_rfonts(rpr, name: str = FONT_NAME) -> None:
    """Pin ascii/hAnsi/cs/eastAsia so cyrillic runs stay in Arial on every Word build (v1 spec)."""
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        # EG_RPrBase order: `w:rStyle` first, `w:rFonts` right after it.
        r_style = rpr.find(qn("w:rStyle"))
        if r_style is not None:
            r_style.addnext(rfonts)
        else:
            rpr.insert(0, rfonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attribute), name)


def _style_run(run, *, bold=False, italic=False, size=None, code=False) -> None:
    name = CODE_FONT_NAME if code else FONT_NAME
    run.font.name = name
    run.font.size = size if size is not None else FONT_SIZE_BODY
    run.bold = bool(bold)
    run.italic = bool(italic)
    _set_rfonts(run._element.get_or_add_rPr(), name)


def _apply_std_paragraph_format(paragraph, first_line_indent=None, left_indent=None) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    fmt = paragraph.paragraph_format
    fmt.line_spacing_rule = WD_LINE_SPACING.SINGLE
    fmt.line_spacing = LINE_SPACING_VALUE
    fmt.space_before = SPACING_BEFORE
    fmt.space_after = SPACING_AFTER
    if first_line_indent is not None:
        fmt.first_line_indent = first_line_indent
    if left_indent is not None:
        fmt.left_indent = left_indent


def _clear_inherited_tab(paragraph, pos_dxa: int) -> None:
    """`<w:tabs><w:tab w:val="clear" w:pos="N"/></w:tabs>` so wrapped list text honours `w:ind`."""
    tabs = paragraph._element.get_or_add_pPr().get_or_add_tabs()
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "clear")
    tab.set(qn("w:pos"), str(pos_dxa))
    tabs.append(tab)


def _disable_contextual_spacing(paragraph) -> None:
    """Restore the 6pt after-spacing the built-in list styles suppress (v1 spec)."""
    p_pr = paragraph._element.get_or_add_pPr()
    element = OxmlElement("w:contextualSpacing")
    element.set(qn("w:val"), "0")
    jc = p_pr.find(qn("w:jc"))
    if jc is not None:
        jc.addprevious(element)
    else:
        p_pr.append(element)


def _set_num_pr(paragraph, num_id: int, level: int) -> None:
    """Bind the paragraph to `num_id`/`ilvl` — the per-list `w:num` that restarts numbering (§5.5)."""
    num_pr = paragraph._element.get_or_add_pPr().get_or_add_numPr()
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), str(level))
    num = OxmlElement("w:numId")
    num.set(qn("w:val"), str(num_id))
    num_pr.append(ilvl)
    num_pr.append(num)


def _set_cell_background(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _set_cell_border(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "8")
        element.set(qn("w:color"), hex_color)
        borders.append(element)
    tc_pr.append(borders)


# --- document scaffolding --------------------------------------------------


def _apply_page_setup(doc) -> None:
    for section in doc.sections:
        section.top_margin = MARGIN
        section.bottom_margin = MARGIN
        section.left_margin = MARGIN
        section.right_margin = MARGIN


def _configure_default_style(doc) -> None:
    style = doc.styles["Normal"]
    style.font.name = FONT_NAME
    style.font.size = FONT_SIZE_BODY
    _set_rfonts(style.element.get_or_add_rPr())
    fmt = style.paragraph_format
    fmt.line_spacing_rule = WD_LINE_SPACING.SINGLE
    fmt.line_spacing = LINE_SPACING_VALUE
    fmt.space_before = SPACING_BEFORE
    fmt.space_after = SPACING_AFTER


FOOTNOTE_STYLES_XML = f"""
<w:styles {nsdecls("w")}>
  <w:style w:type="paragraph" w:styleId="{STYLE_FOOTNOTE_TEXT}">
    <w:name w:val="footnote text"/>
    <w:basedOn w:val="Normal"/>
    <w:uiPriority w:val="99"/>
    <w:semiHidden/>
    <w:unhideWhenUsed/>
    <w:pPr>
      <w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>
      <w:ind w:firstLine="0"/>
      <w:jc w:val="left"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="{FONT_NAME}" w:hAnsi="{FONT_NAME}" w:cs="{FONT_NAME}" w:eastAsia="{FONT_NAME}"/>
      <w:sz w:val="{int(FONT_SIZE_FOOTNOTE.pt * 2)}"/>
      <w:szCs w:val="{int(FONT_SIZE_FOOTNOTE.pt * 2)}"/>
    </w:rPr>
  </w:style>
  <w:style w:type="character" w:styleId="{STYLE_FOOTNOTE_REFERENCE}">
    <w:name w:val="footnote reference"/>
    <w:basedOn w:val="DefaultParagraphFont"/>
    <w:uiPriority w:val="99"/>
    <w:semiHidden/>
    <w:unhideWhenUsed/>
    <w:rPr>
      <w:vertAlign w:val="superscript"/>
    </w:rPr>
  </w:style>
</w:styles>
"""


def _ensure_footnote_styles(doc) -> None:
    """Add `FootnoteText`/`FootnoteReference` to `styles.xml`; `docx validate` requires both (§5.5)."""
    styles = doc.styles.element
    known = {style.get(qn("w:styleId")) for style in styles.findall(qn("w:style"))}
    template = parse_xml(FOOTNOTE_STYLES_XML.strip())
    for style in template.findall(qn("w:style")):
        if style.get(qn("w:styleId")) not in known:
            styles.append(style)


BULLET_LEVELS = (
    ("•", "Symbol"),
    ("o", "Courier New"),
    ("§", "Wingdings"),
)


def _abstract_num_xml(abstract_id: int, *, ordered: bool) -> str:
    levels = []
    for level in range(3):
        indent = INDENT_LEFT_LIST_DXA * (level + 1)
        if ordered:
            levels.append(
                f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
                f'<w:lvlText w:val="%{level + 1}."/><w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr></w:lvl>'
            )
        else:
            text, font = BULLET_LEVELS[level]
            levels.append(
                f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                f'<w:lvlText w:val="{escape(text)}"/><w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr>'
                f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:hint="default"/></w:rPr></w:lvl>'
            )
    return (
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abstract_id}">'
        '<w:multiLevelType w:val="hybridMultilevel"/>' + "".join(levels) + "</w:abstractNum>"
    )


class _Numbering:
    """One fresh `w:num` per list so every list restarts at its own first item (§5.5)."""

    def __init__(self, doc) -> None:
        self._element = doc.part.numbering_part.element
        used_abstract = [
            int(node.get(qn("w:abstractNumId")) or 0)
            for node in self._element.findall(qn("w:abstractNum"))
        ]
        used_num = [int(node.get(qn("w:numId")) or 0) for node in self._element.findall(qn("w:num"))]
        self._next_abstract = max([ABSTRACT_NUM_ID_BASE] + [value + 1 for value in used_abstract])
        self._next_num = max([NUM_ID_BASE] + [value + 1 for value in used_num])
        self._abstract: dict[bool, int] = {}

    def _abstract_id(self, ordered: bool) -> int:
        if ordered not in self._abstract:
            abstract_id = self._next_abstract
            self._next_abstract += 1
            node = parse_xml(_abstract_num_xml(abstract_id, ordered=ordered))
            first_num = self._element.find(qn("w:num"))
            if first_num is not None:
                first_num.addprevious(node)
            else:
                self._element.append(node)
            self._abstract[ordered] = abstract_id
        return self._abstract[ordered]

    def new_list(self, *, ordered: bool, level: int) -> int:
        """Allocate the `w:numId` of one markdown list and return it."""
        abstract_id = self._abstract_id(ordered)
        num_id = self._next_num
        self._next_num += 1
        override = (
            f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="1"/></w:lvlOverride>'
            if ordered
            else ""
        )
        node = parse_xml(
            f'<w:num {nsdecls("w")} w:numId="{num_id}">'
            f'<w:abstractNumId w:val="{abstract_id}"/>{override}</w:num>'
        )
        self._element.append(node)
        return num_id


# --- footnotes part --------------------------------------------------------

SEPARATOR_FOOTNOTES = (
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:pPr>'
    '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    "<w:r><w:separator/></w:r></w:p></w:footnote>"
    '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:pPr>'
    '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    "<w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"
)


def _footnotes_xml(notes: list[dict]) -> bytes:
    """Serialise `word/footnotes.xml`: the two Word separators plus one `w:footnote` per mention."""
    body = [SEPARATOR_FOOTNOTES]
    for note in notes:
        body.append(
            f'<w:footnote w:id="{int(note["n"])}"><w:p>'
            f'<w:pPr><w:pStyle w:val="{STYLE_FOOTNOTE_TEXT}"/></w:pPr>'
            f'<w:r><w:rPr><w:rStyle w:val="{STYLE_FOOTNOTE_REFERENCE}"/></w:rPr>'
            "<w:footnoteRef/></w:r>"
            f'<w:r><w:t xml:space="preserve"> {escape(note["text"])}</w:t></w:r>'
            "</w:p></w:footnote>"
        )
    xml = f'<w:footnotes {nsdecls("w")}>' + "".join(body) + "</w:footnotes>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml).encode("utf-8")


def _attach_footnotes(doc, notes: list[dict]) -> None:
    """Create `word/footnotes.xml` and relate it from `document.xml.rels` (§5.5, `docx validate`)."""
    part = Part(
        PackURI(FOOTNOTES_PARTNAME),
        CT.WML_FOOTNOTES,
        _footnotes_xml(notes),
        doc.part.package,
    )
    doc.part.relate_to(part, RT.FOOTNOTES)


def _add_hyperlink(paragraph, text: str, url: str, size=None) -> None:
    """`<w:hyperlink r:id="…">` with a real external relationship — the inline citation (D-150).

    `docx validate` checks that every `r:id` here resolves in `document.xml.rels`, the same way it
    checks that every `w:footnoteReference` has its footnote.
    """
    relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    _set_rfonts(r_pr)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), HYPERLINK_COLOR)
    r_pr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    r_pr.append(underline)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int((size or FONT_SIZE_BODY).pt * 2)))
    r_pr.append(sz)
    run.append(r_pr)
    node = OxmlElement("w:t")
    node.set(qn("xml:space"), "preserve")
    node.text = text
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


def _add_footnote_reference(paragraph, footnote_id: int) -> None:
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    r_style = OxmlElement("w:rStyle")
    r_style.set(qn("w:val"), STYLE_FOOTNOTE_REFERENCE)
    r_pr.append(r_style)
    _set_rfonts(r_pr)
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), "24")
    r_pr.append(size)
    vert = OxmlElement("w:vertAlign")
    vert.set(qn("w:val"), "superscript")
    r_pr.append(vert)
    run.append(r_pr)
    reference = OxmlElement("w:footnoteReference")
    reference.set(qn("w:id"), str(int(footnote_id)))
    run.append(reference)
    paragraph._p.append(run)


# --- citation tokens -------------------------------------------------------


scan_mentions = fallback.scan_mentions
"""D-150: one scan for both deliverables — `fallback.scan_mentions` (§5.5, `docx/fallback.py`)."""


def pull_mentions(nodes: list) -> list[int]:
    """Take every mention sentinel out of an inline tree and return them in document order (D34-22).

    Used for blockquotes: the sentinels are removed from the quoted runs so the quote stays the
    verbatim rule, and the caller writes them as an attribution paragraph under it. Mutating the
    tree is safe — `mistune` parses a fresh one per render.
    """
    found: list[int] = []
    _strip_mentions(nodes, found)
    _trim_edges(nodes)
    return found


def _strip_mentions(nodes: list, found: list[int]) -> None:
    for node in nodes or ():
        raw = node.get("raw")
        if isinstance(raw, str) and MENTION_OPEN in raw:
            pieces = MENTION_RE.split(raw)
            found.extend(int(piece) for piece in pieces[1::2])
            node["raw"] = re.sub(r"[ \t]{2,}", " ", "".join(pieces[0::2]))
        _strip_mentions(node.get("children"), found)


def _trim_edges(blocks: list) -> None:
    """Drop the space a removed sentinel left at the start or the end of each quoted paragraph."""
    for block in blocks or ():
        leaves = [
            node for node in _leaves(block.get("children")) if isinstance(node.get("raw"), str)
        ]
        if not leaves:
            continue
        leaves[0]["raw"] = leaves[0]["raw"].lstrip()
        leaves[-1]["raw"] = leaves[-1]["raw"].rstrip()


def _leaves(nodes: list) -> list:
    out: list = []
    for node in nodes or ():
        children = node.get("children")
        if children:
            out.extend(_leaves(children))
        else:
            out.append(node)
    return out


def footnote_texts(mentions: list[dict], index: fallback.SourceIndex, style: str) -> list[dict]:
    """The `footnotes-map.json` rows: number, source, form and rendered OSCOLA text (§5.5, D-150).

    Empty in the inline style: there are no footnotes to write, so the part `docx validate` requires
    carries the two Word separators and nothing else.
    """
    if style != oscola.STYLE_FOOTNOTES:
        return []
    notes: list[dict] = []
    for mention in mentions:
        if not mention["resolved"]:
            continue
        view = mention.get("view") or index.view(mention["source_id"])
        notes.append(
            {
                "n": mention["n"],
                "source_id": mention["source_id"],
                # D-150: the short form points at the first footnote of the **instrument**, which is
                # what `validate._check_short_forms` has to compare against in an article-level
                # registry — `art 44` is a later mention of the GDPR, not a first one of its own.
                "instrument": mention.get("instrument") or mention["source_id"],
                "form": mention["form"],
                "first_n": mention["first_n"],
                "pinpoint": mention["pinpoint"],
                "text": oscola.mention_text(view, mention, style),
            }
        )
    return notes


# --- the renderer ----------------------------------------------------------


class _Body:
    """Walks the `mistune` AST and writes paragraphs, lists, tables and footnote references."""

    def __init__(
        self,
        doc,
        mentions: list[dict],
        numbering: _Numbering,
        on_sources,
        *,
        index: fallback.SourceIndex,
        style: str,
    ) -> None:
        self.doc = doc
        self.mentions = mentions
        self.numbering = numbering
        self.on_sources = on_sources
        self.index = index
        self.style = style
        self.sources_emitted = False

    # -- blocks --

    def blocks(self, tokens: list, level: int = 0) -> None:
        for token in tokens or ():
            self.block(token, level)

    def block(self, token: dict, level: int = 0) -> None:
        kind = token.get("type")
        if kind in ("blank_line", "linebreak"):
            return
        if kind == "heading":
            self.heading(token)
        elif kind == "paragraph":
            self.paragraph(token)
        elif kind == "block_text":
            self.paragraph(token)
        elif kind == "block_quote":
            self.block_quote(token)
        elif kind == "list":
            self.list(token, level)
        elif kind == "table":
            self.table(token)
        elif kind == "block_html":
            self.block_html(token)
        elif kind == "block_code":
            self.code_block(token)
        elif kind == "thematic_break":
            self.doc.add_paragraph()
        else:  # pragma: no cover - defensive: unknown block types degrade to a paragraph
            self.paragraph(token)

    def heading(self, token: dict) -> None:
        """H1/H2/H3 as bold body paragraphs — the numbering comes from the markdown text (v1 spec)."""
        paragraph = self.doc.add_paragraph()
        _apply_std_paragraph_format(paragraph, first_line_indent=Cm(0))
        self.inline(paragraph, token.get("children"), {"bold": True})

    def paragraph(self, token: dict) -> None:
        paragraph = self.doc.add_paragraph()
        _apply_std_paragraph_format(paragraph, first_line_indent=INDENT_FIRSTLINE_BODY)
        self.inline(paragraph, token.get("children"), {})

    def block_quote(self, token: dict) -> None:
        # D34-22: the citation of a quotation is an attribution under it, never a marker inside the
        # quoted rule — so the mentions come out of the inline tree before the quote is written.
        cited = pull_mentions(token.get("children"))
        for child in token.get("children") or ():
            if child.get("type") not in ("paragraph", "block_text"):
                self.block(child)
                continue
            paragraph = self.doc.add_paragraph()
            _apply_std_paragraph_format(
                paragraph, first_line_indent=Cm(0), left_indent=INDENT_LEFT_BLOCKQUOTE
            )
            self.inline(
                paragraph, child.get("children"), {"italic": True, "size": FONT_SIZE_QUOTE}
            )
        if cited:
            self.attribution(cited)

    def attribution(self, cited: list[int]) -> None:
        """`— [n]` under a blockquote: the footnote reference of every mention it carried (D34-22)."""
        paragraph = self.doc.add_paragraph()
        _apply_std_paragraph_format(
            paragraph, first_line_indent=Cm(0), left_indent=INDENT_LEFT_BLOCKQUOTE
        )
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = paragraph.add_run(fallback.ATTRIBUTION_PREFIX)
        _style_run(run, size=FONT_SIZE_QUOTE)
        for position, index in enumerate(cited):
            if position and self.style != oscola.STYLE_FOOTNOTES:
                _style_run(paragraph.add_run(" "), size=FONT_SIZE_QUOTE)
            self.mention(paragraph, index, {"size": FONT_SIZE_QUOTE})

    def list(self, token: dict, level: int) -> None:
        attrs = token.get("attrs") or {}
        ordered = bool(attrs.get("ordered"))
        start = attrs.get("start")
        items = [row for row in token.get("children") or () if row.get("type") == "list_item"]
        if ordered and isinstance(start, int) and start != 1 and len(items) == 1 and level == 0:
            self.standalone_numbered(items[0], start)
            return
        num_id = self.numbering.new_list(ordered=ordered, level=min(level, MAX_LIST_DEPTH))
        for item in items:
            self.list_item(item, ordered=ordered, level=level, num_id=num_id)

    def standalone_numbered(self, item: dict, start: int) -> None:
        """`2. Text` on its own is prose, not a one-item list — keep the literal number (§5.5)."""
        paragraph = self.doc.add_paragraph()
        _apply_std_paragraph_format(paragraph, first_line_indent=INDENT_FIRSTLINE_BODY)
        run = paragraph.add_run(f"{int(start)}. ")
        _style_run(run)
        for child in item.get("children") or ():
            if child.get("type") in ("block_text", "paragraph"):
                self.inline(paragraph, child.get("children"), {})

    def list_item(self, item: dict, *, ordered: bool, level: int, num_id: int) -> None:
        depth = min(level, MAX_LIST_DEPTH)
        base = "List Number" if ordered else "List Bullet"
        style = base if depth == 0 else f"{base} {depth + 1}"
        paragraph = self.doc.add_paragraph(style=style)
        # No `first_line_indent`: the hanging=360 of the list style keeps the marker at
        # `left - 360` while wrapped text starts at `left` (v1 spec).
        _apply_std_paragraph_format(
            paragraph, left_indent=Twips(INDENT_LEFT_LIST_DXA * (depth + 1))
        )
        _set_num_pr(paragraph, num_id, depth)
        _clear_inherited_tab(paragraph, 360)
        _disable_contextual_spacing(paragraph)
        nested: list[dict] = []
        for child in item.get("children") or ():
            if child.get("type") == "list":
                nested.append(child)
            elif child.get("type") in ("block_text", "paragraph"):
                self.inline(paragraph, child.get("children"), {})
            else:  # pragma: no cover - block content inside a list item is not used by the template
                self.block(child, level + 1)
        for child in nested:
            self.list(child, level + 1)

    def table(self, token: dict) -> None:
        rows: list[list[dict]] = []
        for section in token.get("children") or ():
            if section.get("type") == "table_head":
                rows.append(list(section.get("children") or ()))
            elif section.get("type") == "table_body":
                for row in section.get("children") or ():
                    rows.append(list(row.get("children") or ()))
        if not rows:
            return
        columns = max(len(row) for row in rows)
        table = self.doc.add_table(rows=0, cols=columns)
        try:
            table.style = "Table Grid"
        except KeyError:  # pragma: no cover - the default template always defines it
            pass
        for row_index, cells in enumerate(rows):
            docx_cells = table.add_row().cells
            for column in range(columns):
                paragraph = docx_cells[column].paragraphs[0]
                _apply_std_paragraph_format(paragraph, first_line_indent=Cm(0))
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                if column < len(cells):
                    self.inline(
                        paragraph, cells[column].get("children"), {"bold": row_index == 0}
                    )
        self.doc.add_paragraph()

    def code_block(self, token: dict) -> None:
        paragraph = self.doc.add_paragraph()
        _apply_std_paragraph_format(paragraph, first_line_indent=Cm(0))
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = paragraph.add_run((token.get("raw") or "").rstrip("\n"))
        _style_run(run, code=True)

    def block_html(self, token: dict) -> None:
        """HTML comments are anchors and markers, never content (D-23, §5.5)."""
        raw = token.get("raw") or ""
        if fallback.SOURCES_MARKER.search(raw) and not self.sources_emitted:
            self.sources_emitted = True
            self.on_sources()

    # -- inline --

    def inline(self, paragraph, nodes: list, fmt: dict) -> None:
        for node in nodes or ():
            kind = node.get("type")
            if kind == "text":
                self.text(paragraph, node.get("raw") or "", fmt)
            elif kind == "strong":
                self.inline(paragraph, node.get("children"), {**fmt, "bold": True})
            elif kind == "emphasis":
                self.inline(paragraph, node.get("children"), {**fmt, "italic": True})
            elif kind == "codespan":
                self.text(paragraph, node.get("raw") or "", {**fmt, "code": True})
            elif kind == "link":
                self.link(paragraph, node, fmt)
            elif kind in ("softbreak", "linebreak"):
                self.text(paragraph, " ", fmt)
            elif kind == "inline_html":
                raw = node.get("raw") or ""
                if not raw.lstrip().startswith("<!--"):
                    self.text(paragraph, raw, fmt)
            elif kind == "image":  # pragma: no cover - memos carry no images
                self.inline(paragraph, node.get("children"), fmt)
            else:  # pragma: no cover - defensive
                self.inline(paragraph, node.get("children"), fmt)

    def link(self, paragraph, node: dict, fmt: dict) -> None:
        """`[text](url)` renders as the text followed by the URL — no hyperlink part (§5.5)."""
        url = str((node.get("attrs") or {}).get("url") or "")
        before = len(paragraph.runs)
        self.inline(paragraph, node.get("children"), fmt)
        text = "".join(run.text for run in paragraph.runs[before:])
        if url and url != text:
            self.text(paragraph, f" <{url}>", fmt)

    def text(self, paragraph, text: str, fmt: dict) -> None:
        for piece_index, piece in enumerate(MENTION_RE.split(text)):
            if piece_index % 2 == 1:
                self.mention(paragraph, int(piece), fmt)
            elif piece:
                run = paragraph.add_run(piece)
                _style_run(
                    run,
                    bold=fmt.get("bold", False),
                    italic=fmt.get("italic", False),
                    size=fmt.get("size"),
                    code=fmt.get("code", False),
                )

    def mention(self, paragraph, mention_index: int, fmt: dict) -> None:
        mention = self.mentions[mention_index]
        if not mention["resolved"]:
            run = paragraph.add_run(oscola.unresolved_text(mention["raw_id"]))
            _style_run(
                run,
                bold=fmt.get("bold", False),
                italic=fmt.get("italic", False),
                size=fmt.get("size"),
            )
            return
        if self.style == oscola.STYLE_FOOTNOTES:
            _add_footnote_reference(paragraph, mention["n"])
            return
        if mention["form"] == oscola.FORM_OMITTED:
            # D-150: two adjacent citations of the same source and pinpoint; the second says nothing.
            return
        view = self.index.view(mention["source_id"])
        text = oscola.mention_text(view, mention, self.style)
        url = oscola.anchor_url(view, mention["pinpoint"])
        size = fmt.get("size")
        open_run = paragraph.add_run("(")
        _style_run(open_run, size=size)
        if url:
            _add_hyperlink(paragraph, text, url, size=size)
        else:
            plain = paragraph.add_run(text)
            _style_run(plain, size=size)
        close_run = paragraph.add_run(")")
        _style_run(close_run, size=size)


# --- generated sections ----------------------------------------------------


def _plain_paragraph(container, text: str, *, bold=False, first_line=Cm(0), left=None, align=None):
    paragraph = container.add_paragraph()
    _apply_std_paragraph_format(paragraph, first_line_indent=first_line, left_indent=left)
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text)
    _style_run(run, bold=bold)
    return paragraph


def _render_sources(doc, rows: list[dict]) -> None:
    """§Sources at the `<!-- sources: generated -->` marker: the full record of every cited source.

    D-150: the body carries compact citations, so everything else — the official title, the
    identifiers, the URL, the retrieval date and the currency status — lives in this annex, numbered
    in citation order and in the same words as the markdown deliverable prints them.
    """
    _plain_paragraph(doc, SOURCES_HEADING, bold=True)
    if not rows:
        _plain_paragraph(doc, "No sources were cited in this draft.")
        return
    for row in rows:
        _plain_paragraph(doc, fallback.sources_line(row), first_line=Cm(0))


def _render_status(doc, status: dict) -> None:
    """`Status` — the banners and the unresolved blockers of a run that did not end approved (D34-11).

    The same text, in the same order, as `fallback.render_status` writes into `deliverable.md`: a
    document whose `final_status` needs a human must say so where the reader is, not in `state.json`.
    """
    if not status.get("required"):
        return
    _plain_paragraph(doc, STATUS_HEADING, bold=True)
    _plain_paragraph(doc, fallback.STATUS_LEAD.format(final_status=status["final_status"]))
    if status["banners"]:
        _plain_paragraph(doc, fallback.STATUS_BANNERS_LABEL, bold=True)
        for row in status["banners"]:
            _plain_paragraph(doc, row)
    if status["issues"]:
        _plain_paragraph(doc, fallback.STATUS_ISSUES_LABEL, bold=True)
        for row in status["issues"]:
            _plain_paragraph(doc, row)


def _render_appendix(
    doc,
    warnings: list,
    unverified: list[dict],
    unresolved: list[str],
    *,
    currency_unavailable: bool = False,
) -> None:
    """«Assumptions & Unverified Sources» — the same three groups, and the same condensed text as
    the markdown fallback: both deliverables carry one appendix (§5.5, D-113)."""
    bullets = fallback.assumption_bullets(warnings)
    if not bullets and not unverified and not unresolved and not currency_unavailable:
        return
    _plain_paragraph(doc, APPENDIX_HEADING, bold=True)
    if bullets:
        _plain_paragraph(doc, fallback.ASSUMPTIONS_LABEL, bold=True)
        for bullet in bullets:
            _plain_paragraph(doc, bullet)
    if unverified or currency_unavailable:
        _plain_paragraph(doc, fallback.UNVERIFIED_LABEL, bold=True)
        if currency_unavailable:
            _plain_paragraph(doc, fallback.CURRENCY_UNAVAILABLE_NOTE)
        for row in unverified:
            _plain_paragraph(doc, fallback.unverified_line(row))
    if unresolved:
        _plain_paragraph(doc, fallback.UNRESOLVED_LABEL, bold=True)
        for raw_id in unresolved:
            _plain_paragraph(
                doc,
                f"{raw_id} — not in the frozen source pack or the quote registry; "
                f"marked {oscola.unresolved_text(raw_id)} in the text.",
            )


BANNER_TITLES = (
    ("forced_exit", "REVIEWER NOTES NOT FULLY RESOLVED"),
    ("accepted_early", "USER ACCEPTED EARLY — REMAINING ISSUES"),
    ("manual_review_required", "MANUAL REVIEW REQUIRED"),
)


def banner_title(final_status: str | None, banners: list[dict]) -> str:
    """The yellow banner's headline for a final status (v1 titles, v2 statuses §2.1 row 15)."""
    status = str(final_status or "")
    for prefix, title in BANNER_TITLES:
        if status.startswith(prefix):
            return title
    if status.startswith("approved") and banners:
        return "PIPELINE FALLBACK NOTICE — REVIEW BEFORE CLIENT USE"
    return "MANUAL REVIEW REQUIRED"


def _render_banner(doc, final_status: str | None, banners: list[dict], reasons: list[str]) -> None:
    """The yellow status table at the top of the memo (§5.5 «Баннеры статусов — как в v1»)."""
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cell = table.cell(0, 0)
    _set_cell_background(cell, WARNING_BG)
    _set_cell_border(cell, WARNING_BORDER)

    title_paragraph = cell.paragraphs[0]
    _apply_std_paragraph_format(title_paragraph, first_line_indent=Cm(0))
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title_run = title_paragraph.add_run(banner_title(final_status, banners))
    _style_run(title_run, bold=True)

    left = WD_ALIGN_PARAGRAPH.LEFT
    subtitle = "Manual check recommended before relying on this memorandum."
    if final_status:
        subtitle += f" Final status: {final_status}."
    _plain_paragraph(cell, subtitle, align=left)

    if banners:
        _plain_paragraph(cell, "Pipeline fallbacks that fired during this run:", bold=True, align=left)
        for row in banners:
            text = row.get("text") if isinstance(row, dict) else str(row)
            _plain_paragraph(cell, f"- {text}", align=left)
    if reasons:
        _plain_paragraph(cell, "Reasons recorded for manual review:", bold=True, align=left)
        for reason in reasons:
            _plain_paragraph(cell, f"- {reason}", align=left)
    doc.add_paragraph()


def needs_banner(final_status: str | None, banners: list[dict], reasons: list[str]) -> bool:
    """v1 rule in v2 terms: anything but a clean `approved*` run carries the banner (§5.5)."""
    if banners or reasons:
        return True
    status = str(final_status or "")
    return bool(status) and not status.startswith("approved")


# --- public API ------------------------------------------------------------


def load_index(work_dir: str | Path, *, state: dict | None = None) -> fallback.SourceIndex:
    """`SourceIndex` over the frozen snapshot, checked against `published[]` first (D-41, M6).

    The renderer reads `research/{source-pack,sources,quotes}.json`; each one that `published[]`
    knows must still hash to its published sha, exactly the guarantee `stepctx.read_published`
    gives, otherwise the step is reissued with `output_modified_after_publish`.
    """
    if state is None:
        state = state_io.read_state_or_none(work_dir) or {}
    for relative in ("research/source-pack.json", "research/sources.json", "research/quotes.json"):
        if stepctx.verify_published(work_dir, state, relative):
            raise stepctx.OutputModifiedAfterPublish(relative)
    return fallback.SourceIndex.load(work_dir, state=state)


def render(
    draft_text: str,
    index: fallback.SourceIndex,
    output_path: str | Path,
    *,
    drafting_warnings: list | None = None,
    final_status: str | None = None,
    banners: list | None = None,
    final_status_reasons: list | None = None,
    remaining_blocking_issues: list | None = None,
    citation_style: str | None = None,
) -> dict:
    """Render one draft into `output_path`; returns the footnote map, unresolved ids and banners."""
    try:
        style = oscola.normalise_style(citation_style) or oscola.DEFAULT_CITATION_STYLE
        text = draft_text.replace(MENTION_OPEN, "").replace(MENTION_CLOSE, "")
        scanned = scan_mentions(text, index, style)
        notes = footnote_texts(scanned["mentions"], index, style)
        rows = fallback.source_rows(scanned, index)
        unresolved_banner = fallbacks.banner("unresolved_reference_in_fallback")
        banner_id = unresolved_banner["banner_id"] if unresolved_banner else None
        raised = [row for row in (banners or []) if isinstance(row, dict)]
        if scanned["unresolved"]:
            if unresolved_banner and not any(row.get("banner_id") == banner_id for row in raised):
                raised.append(unresolved_banner)
        else:
            # The banner claims `[unresolved: …]` markers are in the deliverable; a run of an
            # earlier draft may have left it in `state.fallback_banners` when this one has none.
            raised = [row for row in raised if row.get("banner_id") != banner_id]

        doc = Document()
        _apply_page_setup(doc)
        _configure_default_style(doc)
        _ensure_footnote_styles(doc)
        reasons = [str(row) for row in (final_status_reasons or [])]
        if needs_banner(final_status, raised, reasons):
            _render_banner(doc, final_status, raised, reasons)

        cited = list(scanned["cited"])
        scanned["text"] = fallback.drop_omitted(scanned["text"], scanned["mentions"])
        body = _Body(
            doc,
            scanned["mentions"],
            _Numbering(doc),
            lambda: _render_sources(doc, rows),
            index=index,
            style=style,
        )
        body.blocks(_markdown(scanned["text"]))
        if not body.sources_emitted:
            _render_sources(doc, rows)
        # D34-11: `Status` before the appendix, built from the very banners this render carries.
        status = fallback.status_inputs(
            {
                "final_status": final_status,
                "remaining_blocking_issues": list(remaining_blocking_issues or []),
            },
            raised,
        )
        _render_status(doc, status)
        _render_appendix(
            doc,
            list(drafting_warnings or []),
            index.unverified_rows(),
            scanned["unresolved"],
            currency_unavailable=index.currency_unavailable,
        )
        _attach_footnotes(doc, notes)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    except stepctx.OutputModifiedAfterPublish:
        raise
    except Exception as exc:  # noqa: BLE001 - any renderer failure degrades to the md fallback (§5.5)
        raise RenderError(f"{type(exc).__name__}: {exc}") from exc
    return {
        "path": str(output_path),
        "footnotes": notes,
        "unresolved": list(scanned["unresolved"]),
        "cited": cited,
        "banners": raised,
        "status_required": bool(status["required"]),
        "citation_style": style,
    }


def footnotes_map(result: dict) -> dict:
    """The `footnotes-map.json` `docx validate` reads back (§5.5).

    It also carries `status_required` (D34-11): the renderer knows the `final_status` of the run,
    `validate.py` only ever sees the package, so this is how the check «a run that did not end
    approved must carry its `Status` section» reaches the validator.
    """
    return {
        "footnotes": [dict(row) for row in result["footnotes"]],
        "unresolved": list(result["unresolved"]),
        "cited": list(result["cited"]),
        "status_required": bool(result.get("status_required")),
        # D-150: the inline style has no footnotes at all, so the validator has to be told that the
        # empty footnote part is the expected one and not a render that lost its citations.
        "citation_style": result.get("citation_style") or oscola.DEFAULT_CITATION_STYLE,
    }


def render_workdir(
    work_dir: str | Path,
    draft_path: str | Path,
    output_path: str | Path,
    *,
    state: dict | None = None,
    banners: list | None = None,
    final_status_reasons: list | None = None,
) -> dict:
    """Render `draft_path` into `output_path` using the registry files of `work_dir`."""
    text = Path(draft_path).read_text(encoding="utf-8-sig")
    index = load_index(work_dir, state=state)
    state = state or {}
    return render(
        text,
        index,
        output_path,
        drafting_warnings=state.get("drafting_warnings") or [],
        final_status=state.get("final_status"),
        banners=list(banners or []) + [
            row for row in (state.get("fallback_banners") or []) if isinstance(row, dict)
        ],
        final_status_reasons=list(final_status_reasons or [])
        + [str(row) for row in (state.get("final_status_reasons") or [])],
        remaining_blocking_issues=state.get("remaining_blocking_issues") or [],
        citation_style=oscola.resolve_style(state),
    )
