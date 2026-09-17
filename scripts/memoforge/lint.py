"""`mf draft anchor|lint` — section anchors and the 15 deterministic L-rules (ТЗ §5.4, M10)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import limits, pylauncher, quotes, schema, sources, state_io, stepctx

LINT_PATH = "lint.json"

SRC_TOKEN = re.compile(r"\[\[src:\s*([a-z0-9][a-z0-9._-]*)([^\]]*)\]\]")
Q_TOKEN = re.compile(r"\[\[q:\s*([a-z0-9][a-z0-9._-]*)\s*\]\]")
ANCHOR = re.compile(r"<!--\s*§(s-[0-9][0-9-]*)\s*-->")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
HEADING_NUMBER = re.compile(r"^(\d+)(?:\.(\d+))?\.?\s+")
RISK_LINE = re.compile(r"^Risk: (high|medium|low|undetermined)\.")
"""D-12: the literal Risk-line format — exact case, mandatory period, no bullet or bold prefix."""

RISK_LIKE = re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?risk\s*:", re.IGNORECASE)
"""Anything the writer meant as a Risk line; used to tell «malformed» from «missing» (D-12)."""

EXEC_BULLET_RISK = re.compile(r"Risk: (high|medium|low|undetermined)\.\s*$")
"""D-12: an executive-summary bullet ends with the same literal verdict."""

RISK_LEVELS: tuple[str, ...] = ("high", "medium", "low", "undetermined")
BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
SOURCES_MARKER = "<!-- sources: generated -->"
EM_DASH = "\u2014"
DISCLAIMER = re.compile(r"disclaimer|assumptions?\b[^.]{0,80}\bnot\b[^.]{0,40}\bconfirm", re.IGNORECASE)

PLACEHOLDERS: tuple[str, ...] = ("TODO", "TBD", "FIXME", "XXX", "PLACEHOLDER", "Lorem ipsum", "[insert")
PLACEHOLDER_ANGLE = re.compile(r"<[a-z][^<>\n]{2,60}>")

CANONICAL_SECTIONS: dict[str, str] = {
    "executive summary": "executive_summary",
    "background and definitions": "background",
    "facts, assumptions and limitations": "facts",
    "key assumptions": "assumptions",
    "conclusion and recommendations": "conclusion",
    "recommendations": "recommendations",
}
"""Canonical names of CONVENTIONS «Канонические имена секций»; compared case- and punctuation-blind."""

SEVERITY: dict[str, str] = {
    # Structure, provenance and freeze rules stop the pipeline (`clean` = no blocker, §5.4, G5).
    "L-01": "major",
    "L-02": "major",
    "L-03": "minor",
    "L-04": "major",
    "L-05": "blocker",
    "L-06": "blocker",
    "L-07": "blocker",
    "L-08": "blocker",
    "L-09": "blocker",
    "L-10": "major",
    "L-11": "blocker",
    "L-12": "blocker",
    "L-13": "major",
    "L-14": "blocker",
    "L-15": "blocker",
}

TITLE_SECTION_ID = "s-title"
"""D34-09: the H1 and the header block above the first H2; `s-0` now belongs to the front-matter H2."""

TEMPLATE_BRIEF = "executive-brief"
TEMPLATE_CLASSICAL = "classical-memo"


# --- draft parsing --------------------------------------------------------


def normalize_title(title: str) -> str:
    """Heading title without its numbering, lowercase, without trailing punctuation."""
    text = re.sub(r"<!--.*?-->", "", title)
    text = re.sub(r"[*_`]", "", text).strip()
    text = HEADING_NUMBER.sub("", text)
    text = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", text)
    return text.strip().rstrip(".:;,! ").lower()


def parse_draft(text: str) -> dict:
    """Headings, sections, blockquotes, paragraphs and citation tokens of one draft."""
    lines = text.split("\n")
    in_code = False
    code_lines: set[int] = set()
    headings: list[dict] = []

    for number, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
            code_lines.add(number)
            continue
        if in_code:
            code_lines.add(number)
            continue
        match = HEADING.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2)
            anchor = ANCHOR.search(title)
            headings.append(
                {
                    "level": level,
                    "line": number,
                    "raw": line,
                    "title": ANCHOR.sub("", title).strip(),
                    "anchor": anchor.group(1) if anchor else None,
                }
            )

    # anchors sit on the line right after the heading (`mf draft anchor`)
    for heading in headings:
        if heading["anchor"]:
            continue
        for probe in range(heading["line"] + 1, min(heading["line"] + 3, len(lines) + 1)):
            candidate = lines[probe - 1].strip()
            if not candidate:
                continue
            match = ANCHOR.fullmatch(candidate)
            if match:
                heading["anchor"] = match.group(1)
            break

    assign_section_ids(headings)

    sections: list[dict] = []
    for index, heading in enumerate(headings):
        end = len(lines)
        for following in headings[index + 1 :]:
            if following["level"] <= heading["level"]:
                end = following["line"] - 1
                break
        else:
            end = len(lines)
        section = dict(heading)
        section["end_line"] = end
        section["kind"] = CANONICAL_SECTIONS.get(normalize_title(heading["title"]))
        sections.append(section)

    for index, section in enumerate(sections):
        section["has_children"] = any(
            other["level"] == section["level"] + 1 and section["line"] < other["line"] <= section["end_line"]
            for other in sections
        )
        section["parent"] = None
        for previous in reversed(sections[:index]):
            if previous["level"] < section["level"]:
                section["parent"] = previous["section_id"]
                break

    document = {
        "lines": lines,
        "code_lines": code_lines,
        "headings": headings,
        "sections": sections,
        "duplicate_sections": duplicate_sections(headings),
        "text": text,
    }
    document["blockquotes"] = collect_blockquotes(document)
    document["paragraphs"] = collect_paragraphs(document)
    document["src_tokens"] = collect_tokens(document, SRC_TOKEN, "src")
    document["q_tokens"] = collect_tokens(document, Q_TOKEN, "q")
    return document


def assign_section_ids(headings: list[dict]) -> None:
    """`s-N` for H2 and `s-N-M` for H3; un-numbered H2 before the first numbered one are front matter.

    D34-09: «## Key assumptions» (and an un-numbered «## Executive summary») used to increment the
    implicit H2 counter, which the next «## 1. …» then reset — both became `s-1`. They now take
    `s-0`, `s-0-1`, … from the same counter the H3 of the front matter use, so the derived ids
    cannot collide; whatever still collides (a repeated heading number, a hand-written anchor) is
    left for `duplicate_sections` and L-15 to report.
    """
    h2 = 0
    h3 = 0
    numbered_seen = False
    front_seen = False
    for heading in headings:
        if heading["level"] <= 1:
            heading["number"] = None
            heading["section_id"] = heading["anchor"] or TITLE_SECTION_ID
            continue
        match = HEADING_NUMBER.match(heading["title"])
        if heading["level"] == 2:
            if match:
                numbered_seen = True
                h2 = int(match.group(1))
                h3 = 0
                derived = f"s-{h2}"
            elif numbered_seen:
                h2 += 1
                h3 = 0
                derived = f"s-{h2}"
            elif not front_seen:
                front_seen = True
                h2 = 0
                h3 = 0
                derived = "s-0"
            else:
                h2 = 0
                h3 += 1
                derived = f"s-0-{h3}"
        else:
            if match and match.group(2):
                h2 = int(match.group(1))
                h3 = int(match.group(2))
            else:
                h3 += 1
            derived = f"s-{h2}-{h3}"
        heading["number"] = derived[2:]
        heading["section_id"] = heading["anchor"] or derived


def duplicate_sections(headings: list[dict]) -> list[dict]:
    """Headings whose `section_id` repeats an earlier one — the L-15 collision set (D34-09)."""
    first: dict[str, dict] = {}
    clashes: list[dict] = []
    for heading in headings:
        section_id = heading.get("section_id")
        if not section_id:
            continue
        earlier = first.get(section_id)
        if earlier is None:
            first[section_id] = heading
            continue
        clashes.append({"section_id": section_id, "heading": heading, "first": earlier})
    return clashes


def section_of(document: dict, line: int) -> str | None:
    """Innermost section id containing a line."""
    found = None
    for section in document["sections"]:
        if section["line"] <= line <= section["end_line"]:
            found = section["section_id"]
    return found


def analytical_sections(document: dict) -> list[dict]:
    """Sections carrying the four beats: leaf sections that are not a canonical named one (§4.4)."""
    result = []
    for section in document["sections"]:
        if section["level"] not in (2, 3) or section["kind"] is not None:
            continue
        if section["level"] == 2 and section["has_children"]:
            continue
        if section["level"] == 3:
            parent = next((s for s in document["sections"] if s["section_id"] == section["parent"]), None)
            if parent is not None and parent["kind"] is not None:
                continue
        result.append(section)
    return result


def collect_blockquotes(document: dict) -> list[dict]:
    """Consecutive `>` lines merged into one blockquote."""
    quotes_found: list[dict] = []
    current: dict | None = None
    for number, line in enumerate(document["lines"], start=1):
        if number in document["code_lines"]:
            continue
        if line.lstrip().startswith(">"):
            body = line.lstrip()[1:].strip()
            if current is None:
                current = {"start_line": number, "end_line": number, "text": body}
            else:
                current["end_line"] = number
                current["text"] = (current["text"] + " " + body).strip()
            continue
        if current is not None:
            quotes_found.append(current)
            current = None
    if current is not None:
        quotes_found.append(current)
    for quote in quotes_found:
        quote["section_id"] = section_of(document, quote["start_line"])
    return quotes_found


def collect_paragraphs(document: dict) -> list[dict]:
    """Prose units: bullets stand alone, blockquotes/headings/comments/code never count."""
    paragraphs: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and current["text"].strip():
            paragraphs.append(current)
        current = None

    for number, line in enumerate(document["lines"], start=1):
        stripped = line.strip()
        skip = (
            number in document["code_lines"]
            or not stripped
            or stripped.startswith(">")
            or stripped.startswith("#")
            or stripped.startswith("<!--")
            or stripped.startswith("|")
        )
        if skip:
            flush()
            continue
        if BULLET.match(line):
            flush()
            current = {
                "start_line": number,
                "end_line": number,
                "text": BULLET.sub("", line).strip(),
                "bullet": True,
            }
            flush()
            continue
        if current is None:
            current = {"start_line": number, "end_line": number, "text": stripped, "bullet": False}
        else:
            current["end_line"] = number
            current["text"] = current["text"] + " " + stripped
    flush()
    for paragraph in paragraphs:
        paragraph["section_id"] = section_of(document, paragraph["start_line"])
    return paragraphs


def collect_tokens(document: dict, pattern: re.Pattern, kind: str) -> list[dict]:
    """Every `[[src:]]` / `[[q:]]` token with its line, section and pinpoint."""
    tokens = []
    for number, line in enumerate(document["lines"], start=1):
        if number in document["code_lines"]:
            continue
        for match in pattern.finditer(line):
            tokens.append(
                {
                    "kind": kind,
                    "id": match.group(1),
                    "pinpoint": (match.group(2).strip() if pattern.groups > 1 else ""),
                    "line": number,
                    "section_id": section_of(document, number),
                    "text": line.strip(),
                    "in_blockquote": line.lstrip().startswith(">"),
                }
            )
    return tokens


# --- findings -------------------------------------------------------------


def finding(
    rule: str,
    line: int | None,
    section_id: str | None,
    excerpt: str,
    hint: str,
    severity: str | None = None,
) -> dict:
    """One `lint.json` finding (schema `lint`)."""
    return {
        "rule": rule,
        "severity": severity or SEVERITY[rule],
        "line": line,
        "section_id": section_id,
        "excerpt": excerpt[:200],
        "hint": hint,
    }


def ai_tells() -> list[str]:
    """Phrases of `lib/ai-tells.txt` (L-04)."""
    path = pylauncher.plugin_root() / "lib" / "ai-tells.txt"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return [line.strip().lower() for line in text.split("\n") if line.strip() and not line.startswith("#")]


def body_words(document: dict) -> int:
    """Words of the draft body: no headings, anchors, tokens, comments or code (L-10)."""
    total = 0
    for paragraph in document["paragraphs"]:
        text = SRC_TOKEN.sub("", paragraph["text"])
        text = Q_TOKEN.sub("", text)
        total += len(text.split())
    for quote in document["blockquotes"]:
        total += len(Q_TOKEN.sub("", quote["text"]).split())
    return total


def check_l01(document: dict) -> list[dict]:
    """L-01: sentences longer than `limits.MAX_SENTENCE_WORDS` (quotes excluded — they are verbatim)."""
    out = []
    for paragraph in document["paragraphs"]:
        text = SRC_TOKEN.sub("", paragraph["text"])
        text = Q_TOKEN.sub("", text)
        for start, end in quotes.sentence_spans(text):
            sentence = text[start:end].strip()
            words = len(sentence.split())
            if words > limits.MAX_SENTENCE_WORDS:
                out.append(
                    finding(
                        "L-01",
                        paragraph["start_line"],
                        paragraph["section_id"],
                        sentence,
                        f"Sentence has {words} words; the cap is {limits.MAX_SENTENCE_WORDS}.",
                    )
                )
    return out


def check_l02(document: dict) -> list[dict]:
    """L-02: paragraphs above the sentence or word cap."""
    out = []
    for paragraph in document["paragraphs"]:
        if paragraph["bullet"]:
            continue
        text = Q_TOKEN.sub("", SRC_TOKEN.sub("", paragraph["text"]))
        words = len(text.split())
        sentences = len(quotes.sentence_spans(text))
        if sentences > limits.MAX_PARAGRAPH_SENTENCES or words > limits.MAX_PARAGRAPH_WORDS:
            out.append(
                finding(
                    "L-02",
                    paragraph["start_line"],
                    paragraph["section_id"],
                    text,
                    f"Paragraph has {sentences} sentences / {words} words; caps are "
                    f"{limits.MAX_PARAGRAPH_SENTENCES} and {limits.MAX_PARAGRAPH_WORDS}.",
                )
            )
    return out


def check_l03(document: dict) -> list[dict]:
    """L-03: em-dash only in `Term — definition` (§Definitions of `lib/prose-style.md`)."""
    out = []
    for paragraph in document["paragraphs"]:
        text = paragraph["text"]
        if EM_DASH not in text:
            continue
        head, _, _ = text.partition(EM_DASH)
        definition_form = text.count(EM_DASH) == 1 and 0 < len(head.split()) <= 6
        if not definition_form:
            out.append(
                finding(
                    "L-03",
                    paragraph["start_line"],
                    paragraph["section_id"],
                    text,
                    "Em-dash is reserved for `Term — definition.`; rewrite the sentence.",
                )
            )
    return out


def check_l04(document: dict) -> list[dict]:
    """L-04: AI-tells from `lib/ai-tells.txt`."""
    out = []
    tells = ai_tells()
    for paragraph in document["paragraphs"]:
        lowered = paragraph["text"].lower()
        for tell in tells:
            if tell in lowered:
                out.append(
                    finding(
                        "L-04",
                        paragraph["start_line"],
                        paragraph["section_id"],
                        paragraph["text"],
                        f"AI-tell «{tell}»; say the thing plainly instead.",
                    )
                )
    return out


def check_l05(document: dict) -> list[dict]:
    """L-05: H1 -> H2 -> H3 without gaps, no H4+, no heading ending in a question mark."""
    out = []
    previous = 0
    for heading in document["headings"]:
        level = heading["level"]
        if level > 3:
            out.append(
                finding(
                    "L-05",
                    heading["line"],
                    heading.get("section_id"),
                    heading["raw"],
                    "Headings deeper than H3 are not used.",
                )
            )
        elif previous and level > previous + 1:
            out.append(
                finding(
                    "L-05",
                    heading["line"],
                    heading.get("section_id"),
                    heading["raw"],
                    f"Heading level jumps H{previous} -> H{level}.",
                )
            )
        if heading["title"].rstrip().endswith("?"):
            out.append(
                finding(
                    "L-05",
                    heading["line"],
                    heading.get("section_id"),
                    heading["raw"],
                    "A heading is a noun phrase, not a question.",
                )
            )
        previous = level
    first = document["headings"][0] if document["headings"] else None
    if first is None or first["level"] != 1:
        excerpt = first["raw"] if first else ""
        out.append(finding("L-05", 1, None, excerpt, "The draft must open with a single H1 title."))
    if sum(1 for heading in document["headings"] if heading["level"] == 1) > 1:
        out.append(finding("L-05", first["line"] if first else 1, None, "", "Only one H1 title is allowed."))
    return out


def exec_summary_bullets(document: dict) -> list[dict]:
    """Bullets of the executive summary section (L-06, L-13)."""
    section = next((s for s in document["sections"] if s["kind"] == "executive_summary"), None)
    if section is None:
        return []
    return [
        paragraph
        for paragraph in document["paragraphs"]
        if paragraph["bullet"] and section["line"] < paragraph["start_line"] <= section["end_line"]
    ]


def conclusion_bullets(document: dict) -> list[dict]:
    """Top-level items of the conclusion / recommendations section (L-06)."""
    section = next((s for s in document["sections"] if s["kind"] in ("conclusion", "recommendations")), None)
    if section is None:
        return []
    return [
        paragraph
        for paragraph in document["paragraphs"]
        if paragraph["bullet"]
        and section["line"] < paragraph["start_line"] <= section["end_line"]
        and not document["lines"][paragraph["start_line"] - 1].startswith(("  ", "\t"))
    ]


def check_l06(document: dict, template: str) -> list[dict]:
    """L-06: bijection Exec Summary <-> analytical subsections <-> Conclusion (§5.4)."""
    out = []
    analytical = analytical_sections(document)
    conclusions = conclusion_bullets(document)
    if template == TEMPLATE_BRIEF:
        # D-11: for the brief the bijection is «subsection <-> Recommendations item»; the template
        # additionally allows recommendations that cut across subsections, so the check is `>=`.
        if len(conclusions) < len(analytical):
            out.append(
                finding(
                    "L-06",
                    None,
                    None,
                    "",
                    f"{len(conclusions)} recommendation bullets for {len(analytical)} subsections; "
                    "every subsection needs one.",
                )
            )
        return out
    bullets = exec_summary_bullets(document)
    if len(bullets) != len(analytical):
        out.append(
            finding(
                "L-06",
                None,
                None,
                "",
                f"Executive summary has {len(bullets)} bullets for {len(analytical)} analytical "
                "subsections; the correspondence is one to one.",
            )
        )
    if len(conclusions) != len(analytical):
        out.append(
            finding(
                "L-06",
                None,
                None,
                "",
                f"Conclusion has {len(conclusions)} items for {len(analytical)} analytical "
                "subsections; the correspondence is one to one.",
            )
        )
    return out


def section_paragraphs(document: dict, section: dict) -> list[dict]:
    """Prose paragraphs of one section, in document order."""
    return [
        paragraph
        for paragraph in document["paragraphs"]
        if section["line"] < paragraph["start_line"] <= section["end_line"]
    ]


def check_l07(document: dict) -> list[dict]:
    """L-07: the last paragraph of every analytical subsection is the literal Risk line (D-12)."""
    out = []
    for section in analytical_sections(document):
        paragraphs = section_paragraphs(document, section)
        risk_like = [row for row in paragraphs if RISK_LIKE.match(row["text"])]
        if not paragraphs:
            out.append(
                finding(
                    "L-07",
                    section["line"],
                    section["section_id"],
                    section["raw"],
                    "No Risk line: end the subsection with `Risk: <high|medium|low|undetermined>.` "
                    "plus justification and recommendation.",
                )
            )
            continue

        last = paragraphs[-1]
        match = None if last["bullet"] else RISK_LINE.match(last["text"])
        if match is None:
            if risk_like:
                out.append(
                    finding(
                        "L-07",
                        risk_like[-1]["start_line"],
                        section["section_id"],
                        risk_like[-1]["text"],
                        "Risk line format: the last paragraph of the subsection must start with "
                        "`Risk: <high|medium|low|undetermined>.` (exact case, closing period, no "
                        "bullet or bold) and carry the justification and the recommendation.",
                    )
                )
            else:
                out.append(
                    finding(
                        "L-07",
                        section["line"],
                        section["section_id"],
                        section["raw"],
                        "No Risk line: end the subsection with `Risk: <high|medium|low|undetermined>.` "
                        "plus justification and recommendation.",
                    )
                )
        elif not last["text"][match.end() :].strip():
            out.append(
                finding(
                    "L-07",
                    last["start_line"],
                    section["section_id"],
                    last["text"],
                    "Risk line format: the verdict is followed by the justification and the "
                    "recommendation in the same paragraph.",
                )
            )

        for paragraph in paragraphs[:-1]:
            if RISK_LIKE.match(paragraph["text"]):
                out.append(
                    finding(
                        "L-07",
                        paragraph["start_line"],
                        section["section_id"],
                        paragraph["text"],
                        "The Risk line is the last paragraph of the subsection (D-12); move it to the end.",
                    )
                )
    return out


def check_l08(document: dict, quote_registry: dict, registry: dict) -> tuple[list[dict], list[str]]:
    """L-08: every blockquote carries `[[q:]]`; at most one per analytical subsection (D-164: optional)."""
    out: list[dict] = []
    warnings: list[str] = []
    # §5.4 L-08 / G5: «каждая с `[[q:]]`» is a rule about every blockquote of the document — Context,
    # Executive summary, Facts and Conclusion included. Only the «≤1» cap is per
    # analytical subsection (D-164: a quotation is optional). Without this, an unmarked quote
    # escapes both L-08 and the C-rules.
    for quote in document["blockquotes"]:
        if not Q_TOKEN.search(quote["text"]):
            out.append(
                finding(
                    "L-08",
                    quote["start_line"],
                    quote["section_id"],
                    quote["text"],
                    "Blockquote has no `[[q:]]` marker: extract it with `mf quote extract`, or turn it "
                    "into ordinary prose that states the provision with its `[[src:]]` token.",
                )
            )
    for section in analytical_sections(document):
        block = [
            quote
            for quote in document["blockquotes"]
            if section["line"] < quote["start_line"] <= section["end_line"]
        ]
        if len(block) > limits.MAX_BLOCKQUOTES_PER_SUBSECTION:
            out.append(
                finding(
                    "L-08",
                    block[1]["start_line"],
                    section["section_id"],
                    block[1]["text"],
                    f"{len(block)} blockquotes in one subsection; at most "
                    f"{limits.MAX_BLOCKQUOTES_PER_SUBSECTION} is allowed.",
                )
            )
        if block:
            continue
    return out, warnings


def check_l09(document: dict, quote_registry: dict) -> list[dict]:
    """L-09: the same raw fragment is quoted at most once in the draft (§5.4)."""
    out = []
    seen: dict[tuple, dict] = {}
    for token in document["q_tokens"]:
        record = quote_registry["quotes"].get(token["id"])
        if record is None:
            continue
        key = (
            record["source_id"],
            record["raw_sha256"],
            record["char_start"],
            record["char_end"],
        )
        if key in seen:
            out.append(
                finding(
                    "L-09",
                    token["line"],
                    token["section_id"],
                    token["text"],
                    f"The fragment of {record['source_id']} ({record['char_start']}..{record['char_end']}) "
                    f"is already quoted on line {seen[key]['line']}; a new quote_id does not make it new.",
                )
            )
            continue
        seen[key] = token
    return out


def check_l10(document: dict, template: str) -> list[dict]:
    """L-10: `executive-brief` word cap = body words + unique `[[src:]]` x 12 (§5.4)."""
    if template != TEMPLATE_BRIEF:
        return []
    unique_sources = {token["id"] for token in document["src_tokens"]}
    words = body_words(document)
    total = words + len(unique_sources) * limits.BRIEF_SOURCE_WORD_WEIGHT
    if total <= limits.BRIEF_WORD_CAP:
        return []
    return [
        finding(
            "L-10",
            None,
            None,
            "",
            f"Brief is {total} words ({words} body + {len(unique_sources)} sources x "
            f"{limits.BRIEF_SOURCE_WORD_WEIGHT}); the cap is {limits.BRIEF_WORD_CAP}.",
        )
    ]


def check_l11(document: dict) -> list[dict]:
    """L-11: leftover placeholders."""
    out = []
    for number, line in enumerate(document["lines"], start=1):
        if number in document["code_lines"] or line.strip().startswith("<!--"):
            continue
        for placeholder in PLACEHOLDERS:
            if placeholder.lower() in line.lower():
                out.append(
                    finding(
                        "L-11",
                        number,
                        section_of(document, number),
                        line.strip(),
                        f"Placeholder «{placeholder}» left in the draft.",
                    )
                )
        stripped = SRC_TOKEN.sub("", Q_TOKEN.sub("", line))
        for match in PLACEHOLDER_ANGLE.finditer(stripped):
            if match.group(0).startswith(("<http", "<!--")):
                continue
            out.append(
                finding(
                    "L-11",
                    number,
                    section_of(document, number),
                    line.strip(),
                    f"Template slot «{match.group(0)}» was never filled.",
                )
            )
    return out


def check_l12(document: dict, template: str) -> list[dict]:
    """L-12: canonical section names, their order and the generated-sources marker (CONVENTIONS)."""
    out = []
    h2 = [section for section in document["sections"] if section["level"] == 2]
    kinds = [section["kind"] for section in h2]
    required = ("executive_summary", "facts") if template != TEMPLATE_BRIEF else ()
    closing = "conclusion" if template != TEMPLATE_BRIEF else "recommendations"

    for kind in required:
        if kind not in kinds:
            out.append(finding("L-12", None, None, "", f"Template section «{kind}» is missing."))
    if not h2:
        out.append(finding("L-12", None, None, "", "The draft has no H2 sections."))
    elif h2[-1]["kind"] != closing:
        out.append(
            finding(
                "L-12",
                h2[-1]["line"],
                h2[-1]["section_id"],
                h2[-1]["raw"],
                f"The last section must be the «{closing.replace('_', ' ')}» section.",
            )
        )

    order = [kind for kind in kinds if kind in ("executive_summary", "background", "facts")]
    expected = [kind for kind in ("executive_summary", "background", "facts") if kind in order]
    if order != expected:
        out.append(finding("L-12", None, None, "", "Template sections are out of order (summary, background, facts)."))

    if template != TEMPLATE_BRIEF and not analytical_sections(document):
        # D-11: for the brief only the Recommendations section and the sources marker are required.
        out.append(finding("L-12", None, None, "", "The draft has no analytical subsection."))

    body = [line for line in document["lines"] if line.strip()]
    if not body or body[-1].strip() != SOURCES_MARKER:
        excerpt = body[-1] if body else ""
        hint = f"The draft must end with `{SOURCES_MARKER}`."
        out.append(finding("L-12", len(document["lines"]), None, excerpt, hint))
    return out


def check_l13(document: dict, template: str) -> list[dict]:
    """L-13: Exec Summary bullets stay under the cap and end with the verdict (D-11: classical only)."""
    out = []
    if template == TEMPLATE_BRIEF:
        return out
    for bullet in exec_summary_bullets(document):
        text = Q_TOKEN.sub("", SRC_TOKEN.sub("", bullet["text"])).strip()
        words = len(text.split())
        if words > limits.EXEC_SUMMARY_BULLET_MAX_WORDS:
            out.append(
                finding(
                    "L-13",
                    bullet["start_line"],
                    bullet["section_id"],
                    text,
                    f"Summary bullet has {words} words; the cap is {limits.EXEC_SUMMARY_BULLET_MAX_WORDS}.",
                )
            )
        if EXEC_BULLET_RISK.search(text) is None:
            out.append(
                finding(
                    "L-13",
                    bullet["start_line"],
                    bullet["section_id"],
                    text,
                    "Summary bullet must end with `Risk: <high|medium|low|undetermined>.` "
                    "(exact case, closing period).",
                )
            )
    return out


def check_l14(document: dict, state: dict) -> list[dict]:
    """L-14: a disclaimer is required when the user never accepted the intake assumptions."""
    intake = state.get("intake") or {}
    if intake.get("assumptions_accepted") is not False:
        return []
    if DISCLAIMER.search(document["text"]):
        return []
    return [
        finding(
            "L-14",
            None,
            None,
            "",
            "`intake.assumptions_accepted` is false: the draft must carry a disclaimer about the "
            "unconfirmed assumptions.",
        )
    ]


def check_l15(document: dict) -> list[dict]:
    """L-15 duplicate_section_anchor: two headings resolve to the same `s-…` id (D34-09)."""
    out = []
    for clash in document["duplicate_sections"]:
        heading = clash["heading"]
        out.append(
            finding(
                "L-15",
                heading["line"],
                clash["section_id"],
                heading["raw"],
                f"Section anchor {clash['section_id']} is already taken by the heading on line "
                f"{clash['first']['line']}; reviewers address findings by section_id, so renumber "
                "the heading or fix its `<!-- §s-… -->` anchor.",
            )
        )
    return out


# --- lint runner ----------------------------------------------------------


def resolve_template(state: dict, explicit: str | None) -> str:
    """Template id: the explicit argument, else `config.template_id`, else the classical memo."""
    if explicit:
        return explicit
    return (state.get("config") or {}).get("template_id") or TEMPLATE_CLASSICAL


def lint_text(text: str, *, work_dir: str | Path, state: dict, template: str) -> tuple[list[dict], list[str]]:
    """Run all 15 L-rules over one draft; returns findings and an empty warning list (kept for call shape)."""
    document = parse_draft(text)
    registry = sources.read_registry(work_dir)
    quote_registry = quotes.read_quotes(work_dir)

    findings: list[dict] = []
    findings += check_l01(document)
    findings += check_l02(document)
    findings += check_l03(document)
    findings += check_l04(document)
    findings += check_l05(document)
    findings += check_l06(document, template)
    findings += check_l07(document)
    l08, warnings = check_l08(document, quote_registry, registry)
    findings += l08
    findings += check_l09(document, quote_registry)
    findings += check_l10(document, template)
    findings += check_l11(document)
    findings += check_l12(document, template)
    findings += check_l13(document, template)
    findings += check_l14(document, state)
    findings += check_l15(document)

    findings.sort(key=lambda row: (row["rule"], row["line"] if row["line"] is not None else 0))
    return findings, warnings


def build_report(draft_sha: str, findings: list[dict]) -> dict:
    """`lint.json` / `citations.json` shape; `clean` = no blocker (§5.4)."""
    report = {
        "draft_sha": draft_sha,
        "clean": not any(row["severity"] == "blocker" for row in findings),
        "findings": findings,
    }
    schema.validate_or_raise(report, "lint")
    return report


def run_lint(args: argparse.Namespace) -> dict:
    """`mf draft lint --step --attempt --draft <path>` (§5.4)."""
    work_dir = Path(args.workdir)
    draft_rel = stepctx.rel_path(work_dir, args.draft)
    args_key = f"draft lint --draft {draft_rel}"
    state = state_io.read_state(work_dir)
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": identity["errors"], "reason": identity.get("reason")}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = dict(identity.get("result") or {})
        stored["already_done"] = True
        report_path = Path(work_dir) / stored.get("report_path", LINT_PATH)
        if report_path.is_file():
            stored["report"] = state_io.read_json(report_path)
        return stored

    draft_path = stepctx.abs_path(work_dir, draft_rel)
    if not draft_path.is_file():
        return {"errors": [f"draft_not_found: {draft_rel}"]}
    drift = stepctx.verify_published(work_dir, state, draft_rel)
    if drift:
        return {"errors": [drift], "draft": draft_rel}

    text = draft_path.read_text(encoding="utf-8-sig")
    template = resolve_template(state, args.template)
    findings, warnings = lint_text(text, work_dir=work_dir, state=state, template=template)
    report = build_report(state_io.sha256_file(draft_path), findings)

    stepctx.stage_input(work_dir, args.step, args.attempt, draft_path)
    work_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, "lint.json", state_io.dumps(report).encode("utf-8")
    )
    entry = stepctx.publish_file(work_dir, work_file, LINT_PATH, step_id=args.step)

    result = {
        "report_path": LINT_PATH,
        "draft": draft_rel,
        "draft_sha": report["draft_sha"],
        "template": template,
        "clean": report["clean"],
        "findings_count": len(findings),
        "blockers": sum(1 for row in findings if row["severity"] == "blocker"),
    }

    def mutate(state_doc: dict) -> None:
        existing = list(state_doc.get("drafting_warnings") or [])
        for warning in warnings:
            if warning not in existing:
                existing.append(warning)
        state_doc["drafting_warnings"] = existing

    stepctx.close_step(
        work_dir,
        args.step,
        args.attempt,
        result,
        phase=args.phase,
        args_key=args_key,
        published=[entry],
        mutate=mutate,
    )
    return {**result, "report": report}


# --- anchor ---------------------------------------------------------------


def anchor_errors(document: dict) -> list[str]:
    """`duplicate_section_anchor` lines for a draft whose headings collide (D34-09)."""
    return [
        f"duplicate_section_anchor: {clash['section_id']} on lines "
        f"{clash['first']['line']} and {clash['heading']['line']}"
        for clash in document["duplicate_sections"]
    ]


def anchor_text(text: str) -> tuple[str, int]:
    """Insert `<!-- §s-N[-M] -->` after every H2/H3 that has none; returns the text and the count."""
    return insert_anchors(parse_draft(text))


def checked_anchor(text: str) -> tuple[str, int, list[str]]:
    """`anchor_text` behind the refusal of D34-09 — the one anchor path both commands take.

    A draft whose headings collide is refused rather than anchored: two `<!-- §s-3 -->` markers
    make every finding on `s-3` ambiguous. `draft anchor` and the anchor half of `draft finish`
    (D-117) answer that the same way, so they share this function instead of each deciding for
    itself; on a refusal the text comes back unchanged and nothing has been published.
    """
    document = parse_draft(text)
    errors = anchor_errors(document)
    if errors:
        return text, 0, errors
    anchored, inserted = insert_anchors(document)
    return anchored, inserted, []


def insert_anchors(document: dict) -> tuple[str, int]:
    """The anchored text and the number of anchors inserted, for an already parsed draft."""
    lines = list(document["lines"])
    inserts: list[tuple[int, str]] = []
    for heading in document["headings"]:
        if heading["level"] not in (2, 3) or heading["anchor"]:
            continue
        inserts.append((heading["line"], f"<!-- §{heading['section_id']} -->"))
    for line_number, marker in reversed(inserts):
        lines.insert(line_number, marker)
    return "\n".join(lines), len(inserts)


def run_anchor(args: argparse.Namespace) -> dict:
    """`mf draft anchor --step --attempt --draft <path>` — publishes and updates `published[]` (§3.1)."""
    work_dir = Path(args.workdir)
    draft_rel = stepctx.rel_path(work_dir, args.draft)
    args_key = f"draft anchor --draft {draft_rel}"
    state = state_io.read_state(work_dir)
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": identity["errors"], "reason": identity.get("reason")}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = dict(identity.get("result") or {})
        stored["already_done"] = True
        return stored

    draft_path = stepctx.abs_path(work_dir, draft_rel)
    if not draft_path.is_file():
        return {"errors": [f"draft_not_found: {draft_rel}"]}

    staged = stepctx.stage_input(work_dir, args.step, args.attempt, draft_path)
    source_text = (stepctx.abs_path(work_dir, staged) if staged else draft_path).read_text(encoding="utf-8-sig")
    anchored, inserted, errors = checked_anchor(source_text)
    if errors:
        # D34-09: an anchor that names a section twice makes every reviewer finding on it ambiguous;
        # the command refuses instead of publishing the collision the way it did before.
        return {"errors": errors, "draft": draft_rel}
    work_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, draft_path.name, anchored.encode("utf-8")
    )
    entry = stepctx.publish_file(work_dir, work_file, draft_rel, step_id=args.step)

    document = parse_draft(anchored)
    result = {
        "draft": draft_rel,
        "draft_sha": entry["sha256"],
        "anchors_inserted": inserted,
        "sections": [
            section["section_id"] for section in document["sections"] if section["level"] in (2, 3)
        ],
    }
    stepctx.close_step(
        work_dir,
        args.step,
        args.attempt,
        result,
        phase=args.phase,
        args_key=args_key,
        published=[entry],
    )
    return result


# --- CLI ------------------------------------------------------------------


def register(subparsers) -> None:
    """Register `mf draft anchor` and `mf draft lint` (the `draft` group is shared with citations.py)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "draft", "draft anchors and deterministic checks")

    anchor = group.add_parser("anchor", help="insert section anchors and republish the draft")
    anchor.add_argument("--workdir", required=True)
    anchor.add_argument("--step", required=True)
    anchor.add_argument("--attempt", type=int, required=True)
    anchor.add_argument("--draft", required=True)
    anchor.add_argument("--phase", default=None)
    anchor.set_defaults(func=run_anchor)

    linter = group.add_parser("lint", help="run the 15 L-rules and write lint.json")
    linter.add_argument("--workdir", required=True)
    linter.add_argument("--step", required=True)
    linter.add_argument("--attempt", type=int, required=True)
    linter.add_argument("--draft", required=True)
    linter.add_argument("--template", default=None, choices=[TEMPLATE_CLASSICAL, TEMPLATE_BRIEF])
    linter.add_argument("--phase", default=None)
    linter.set_defaults(func=run_lint)
