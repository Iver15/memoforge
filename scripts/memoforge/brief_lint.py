"""The decision-brief lint B-01…B-12 of `/memoforge:brief` (plan 75A, DB-06, D-222, D-229).

The brief and the memo it summarises are both parsed by `lint.parse_draft` with the grammar of
the memo language, so the leaves, the risk verdicts and the citation tokens are the ones the main
lint already recognises. Nothing here reads a file: the driver passes both texts in. Findings
have the `lint.finding` shape; their `section_id` is a block of the brief (`BLOCK_IDS`) or
`document`, the vocabulary the two brief reviewers address.

D-229: every leaf of the memo is either kept — bound to a conclusion block — or omitted — listed
in the one `<!-- omitted §s-… -->` comment of the header; B-03/B-04/B-05 check that partition.
"""

from __future__ import annotations

import re

from . import i18n, limits, lint
from .docx import oscola

BLOCK_IDS: tuple[str, ...] = (
    "s-header",
    "s-main",
    "s-b1",
    "s-b2",
    "s-b3",
    "s-b4",
    "s-b5",
    "s-b6",
    "s-b7",
    "s-actions",
    "s-assumptions",
)
"""Every block id a brief can have: the header, the parts and up to seven conclusion blocks (D-228)."""

DOCUMENT_ID = "document"
"""The `section_id` of a finding about the brief as a whole."""

SEVERITY: dict[str, str] = {
    # B-01 is `major` only above `DECISION_BRIEF_HARD_CAP`; up to it, `minor` (D-228).
    "B-01": "major",
    "B-02": "major",
    "B-03": "major",
    "B-04": "major",
    "B-05": "blocker",
    "B-06": "blocker",
    "B-07": "major",
    "B-08": "major",
    "B-09": "major",
    # B-10: AI-tells are `major` like L-04; a placeholder is raised as `blocker` like L-11.
    "B-10": "major",
    "B-11": "major",  # D-229: an amount the memo does not give
    "B-12": "major",  # D-229 fix round 2: a part over its word budget; a length rule like B-01
}

PART_KINDS: tuple[str, ...] = ("main", "conclusions", "actions", "assumptions")
"""The four parts of the brief, in their order; `assumptions` is optional (DB-04)."""

PART_BLOCK_IDS: dict[str, str] = {"main": "s-main", "actions": "s-actions", "assumptions": "s-assumptions"}

HEADER_KEYS: tuple[str, ...] = ("date_label", "jurisdictions_label", "question_label")

BINDING = re.compile(r"<!--\s*from\s+((?:§s-[0-9][0-9-]*\s*)+)-->")
BINDING_ID = re.compile(r"§(s-[0-9][0-9-]*)")
OMITTED = re.compile(r"<!--\s*omitted\b((?:\s*§s-[0-9][0-9-]*)*)\s*-->")
"""D-229: the header comment listing the leaves the brief leaves out (zero or more ids)."""

_SPACE = "[ " + chr(0x00A0) + chr(0x2009) + chr(0x202F) + "]"
"""A space, a non-breaking space, a thin space or a narrow non-breaking space (B-11)."""
_NUMBER = rf"\d{{1,3}}(?:{_SPACE}\d{{3}})+(?:[.,]\d+)?|\d{{1,3}}(?:[.,]\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_CURRENCY_WORD = r"(?:RUB|USD|EUR|GBP)\b"
AMOUNT = re.compile(
    rf"(?<!\d)(?<!\d[.,])(?:{_NUMBER}){_SPACE}*(?:%|₽|руб|£|€|\$|{_CURRENCY_WORD})"
    rf"|(?<![A-Za-z])(?:£|€|\$|{_CURRENCY_WORD}){_SPACE}*(?:{_NUMBER})(?!\d)"
)
"""B-11 (D-229): a number attached to a currency sign or word, or to `%`; dates and day counts are no amount."""

_QUOTE_MARKER = re.compile(r"\s*(?:>\s*)*")
"""The indentation and blockquote markers that open a line; their `>` count is its blockquote depth (B-11)."""

VERDICT_RANK: dict[str, int] ={"high": 3, "medium": 2, "low": 1}
UNDETERMINED = "undetermined"


# --- memo -------------------------------------------------------------------


def _verdict_keys(language: str) -> dict[str, str]:
    """Localized level word -> `memo.risk.levels` key."""
    return {i18n.t(language, f"memo.risk.levels.{key}"): key for key in lint.RISK_LEVEL_KEYS}


def _pair(token: dict) -> tuple[str, str]:
    return token["id"], oscola.normalise_pinpoint(token["pinpoint"])


def memo_facts(memo_text: str, language: str) -> dict:
    """What the brief is checked against: the memo's leaves, their verdicts and its citations.

    `leaves` maps every analytical leaf (`lint.analytical_sections`) to the key of its risk
    verdict; a leaf whose last paragraph is not a parseable risk line is `undetermined` and is
    also listed in `riskless`. `pairs` are the `(source id, normalised pinpoint)` of every
    `[[src:]]` token, `ids` the source ids alone, `headings` the leaf titles.
    """
    grammar = lint.grammar(language)
    document = lint.parse_draft(memo_text, grammar)
    keys = _verdict_keys(grammar.language)
    leaves: dict[str, str] = {}
    headings: dict[str, str] = {}
    riskless: set[str] = set()
    for section in lint.analytical_sections(document):
        paragraphs = lint.section_paragraphs(document, section)
        last = paragraphs[-1] if paragraphs else None
        match = None if last is None or last["bullet"] else grammar.risk_line.match(last["text"])
        section_id = section["section_id"]
        if match is None:
            leaves[section_id] = UNDETERMINED
            riskless.add(section_id)
        else:
            leaves[section_id] = keys.get(match.group(1), UNDETERMINED)
        headings[section_id] = section["title"]
    return {
        "leaves": leaves,
        "pairs": {_pair(token) for token in document["src_tokens"]},
        "ids": {token["id"] for token in document["src_tokens"]},
        "headings": headings,
        "riskless": riskless,
        "amounts": {normalise_amount(raw) for _, raw in amounts(document)},
    }


def normalise_amount(raw: str) -> str:
    """An amount token without its spaces, thin and non-breaking spaces (B-11, D-229)."""
    return re.sub(_SPACE, "", raw)


def _wrapped_runs(document: dict) -> list[list[tuple[int, str]]]:
    """The lines outside code grouped as Markdown renders them: a soft-wrapped paragraph, list item or blockquote
    is one run of `(line, text)`. A blank line, a hard break (two trailing spaces or a backslash), a new list item
    (inside a blockquote too) and a change of blockquote depth end a run; a heading, a table row or a line that
    opens with a comment stands alone (B-11, D-229 addendum)."""
    runs: list[list[tuple[int, str]]] = []
    previous = None  # the blockquote depth (0: prose) the next line may continue, or None
    for number, line in enumerate(document["lines"], start=1):
        stripped = line.strip()
        if number in document["code_lines"] or not stripped:
            previous = None
            continue
        if stripped.startswith(("#", "|", "<!--")):
            runs.append([(number, line)])
            previous = None
            continue
        marker = _QUOTE_MARKER.match(line)
        depth = marker.group(0).count(">")
        body = line[marker.end():].strip()
        if depth == previous and not lint.BULLET.match(body):
            runs[-1].append((number, body))
        else:
            runs.append([(number, line)])
        hard_break = line.rstrip("\r").endswith(("  ", "\\"))
        previous = None if hard_break or not body else depth
    return runs


def amounts(document: dict) -> list[tuple[int, str]]:
    """`(line, token)` of every amount outside code, comments and `[[src:]]` tokens (B-11, D-229).

    A soft wrap inside an amount (`297` ending one line, `600 ₽` opening the next) renders as a space, so the
    lines of one run are joined before the scan; `line` is the line where the amount starts.
    """
    found = []
    for run in _wrapped_runs(document):
        starts, pieces, offset = [], [], 0
        for number, line in run:
            piece = lint.SRC_TOKEN.sub(" ", re.sub(r"<!--.*?-->", " ", line))
            starts.append((offset, number))
            pieces.append(piece)
            offset += len(piece) + 1
        text = " ".join(pieces)
        for match in AMOUNT.finditer(text):
            number = next(number for start, number in reversed(starts) if start <= match.start())
            found.append((number, match.group(0)))
    return found


# --- brief ------------------------------------------------------------------


def _part_titles(language: str) -> dict[str, str]:
    """Normalized localized part heading -> part kind."""
    return {
        lint.normalize_title(i18n.t(language, f"memo.brief.sections.{kind}")): kind for kind in PART_KINDS
    }


def _clean_title(title: str) -> str:
    return re.sub(r"<!--.*?-->", "", title).strip()


def _block_verdict(document: dict, section: dict, keys: dict[str, str]) -> str | None:
    """The verdict key of the block's risk line, or None.

    As `lint.check_l07` reads a memo subsection: only the block's last paragraph counts, and only
    when it is not a bullet — a risk line followed by another paragraph is not the block's verdict.
    """
    paragraphs = lint.section_paragraphs(document, section)
    if not paragraphs or paragraphs[-1]["bullet"]:
        return None
    match = document["grammar"].risk_line.match(paragraphs[-1]["text"])
    return None if match is None else keys.get(match.group(1))


def _first_h2(document: dict) -> int:
    """The line of the first `##`, or one past the last line."""
    return next((s["line"] for s in document["sections"] if s["level"] == 2), len(document["lines"]) + 1)


def omitted_comments(document: dict) -> list[tuple[int, list[str]]]:
    """`(line, ids)` of every `<!-- omitted … -->` comment of the header, above the first `##` (D-229)."""
    found = []
    for number, line in enumerate(document["lines"][:_first_h2(document) - 1], start=1):
        if number in document["code_lines"]:
            continue
        for match in OMITTED.finditer(line):
            found.append((number, BINDING_ID.findall(match.group(1))))
    return found


def parse_brief(brief_text: str, language: str) -> dict:
    """The brief as parts, conclusion blocks, a map from parse ids to block ids and its omitted leaves.

    Parts are recognised by their localized heading (`memo.brief.sections.*`, normalised); the
    first H2 of each kind counts. `s-b<k>` is the k-th `###` inside the conclusions part. `omitted`
    lists the leaf ids of the header's `<!-- omitted … -->` comment, in order, once each (D-229).
    """
    grammar = lint.grammar(language)
    document = lint.parse_draft(brief_text, grammar)
    titles = _part_titles(grammar.language)
    keys = _verdict_keys(grammar.language)

    parts: dict[str, dict | None] = {kind: None for kind in PART_KINDS}
    for section in document["sections"]:
        if section["level"] != 2:
            continue
        kind = titles.get(lint.normalize_title(section["title"]))
        if kind is not None and parts[kind] is None:
            parts[kind] = section

    block_map: dict[str, str] = {}
    for section in document["sections"]:
        if section["level"] == 1:
            block_map.setdefault(section["section_id"], "s-header")
    for kind, section in parts.items():
        if section is not None and kind in PART_BLOCK_IDS:
            block_map[section["section_id"]] = PART_BLOCK_IDS[kind]

    blocks: list[dict] = []
    conclusions = parts["conclusions"]
    if conclusions is not None:
        for section in document["sections"]:
            if section["level"] != 3:
                continue
            if not conclusions["line"] < section["line"] <= conclusions["end_line"]:
                continue
            block_id = f"s-b{len(blocks) + 1}"
            bindings: list[str] = []
            for number in range(section["line"] + 1, section["end_line"] + 1):
                for match in BINDING.finditer(document["lines"][number - 1]):
                    bindings += [sid for sid in BINDING_ID.findall(match.group(1)) if sid not in bindings]
            blocks.append(
                {
                    "id": block_id,
                    "title": _clean_title(section["title"]),
                    "line": section["line"],
                    "end_line": section["end_line"],
                    "bindings": bindings,
                    "verdict_key": _block_verdict(document, section, keys),
                    "tokens": [
                        token
                        for token in document["src_tokens"]
                        if section["line"] <= token["line"] <= section["end_line"]
                    ],
                }
            )
            block_map[section["section_id"]] = block_id
    omitted: list[str] = []
    for _, ids in omitted_comments(document):
        omitted += [section_id for section_id in ids if section_id not in omitted]
    return {"document": document, "parts": parts, "blocks": blocks, "block_ids": block_map, "omitted": omitted}


def block_ids(parsed: dict) -> list[str]:
    """The block ids present in this brief, in `BLOCK_IDS` order; `s-header` is always there."""
    present = {"s-header"}
    for kind, part_id in PART_BLOCK_IDS.items():
        if parsed["parts"][kind] is not None:
            present.add(part_id)
    present.update(row["id"] for row in parsed["blocks"])
    return [block_id for block_id in BLOCK_IDS if block_id in present]


def _heading_of(parsed: dict, block_id: str) -> str:
    for kind, part_id in PART_BLOCK_IDS.items():
        if part_id == block_id and parsed["parts"][kind] is not None:
            return _clean_title(parsed["parts"][kind]["title"])
    for row in parsed["blocks"]:
        if row["id"] == block_id:
            return row["title"]
    return ""


def section_list(parsed: dict) -> str:
    """`s-header = the title and header lines; s-main = «<heading>»; …` for the blocks present."""
    items = []
    for block_id in block_ids(parsed):
        if block_id == "s-header":
            items.append("s-header = the title and header lines")
        else:
            items.append(f"{block_id} = «{_heading_of(parsed, block_id)}»")
    return "; ".join(items)


def block_texts(parsed: dict) -> dict[str, str]:
    """`{block id: heading and body on one line}`; `s-header` is everything above the first `##` (D-228).

    Whitespace is normalised, so a re-wrapped line or an extra blank line is no change. A conclusion
    block past the last `BLOCK_IDS` entry keeps its parse id (`s-b8`, …).
    """
    document = parsed["document"]
    lines = document["lines"]

    def text(first: int, last: int) -> str:
        return " ".join(" ".join(lines[first - 1:last]).split())

    first_h2 = next((s["line"] for s in document["sections"] if s["level"] == 2), len(lines) + 1)
    texts = {"s-header": text(1, first_h2 - 1)}
    for kind, block_id in PART_BLOCK_IDS.items():
        section = parsed["parts"][kind]
        if section is not None:
            texts[block_id] = text(section["line"], section["end_line"])
    for row in parsed["blocks"]:
        texts[row["id"]] = text(row["line"], row["end_line"])
    return texts


def _conclusions_preamble(parsed: dict) -> str | None:
    """The conclusions heading and the text before its first `###` (no block covers it); None without the part."""
    section = parsed["parts"]["conclusions"]
    if section is None:
        return None
    last = parsed["blocks"][0]["line"] - 1 if parsed["blocks"] else section["end_line"]
    lines = parsed["document"]["lines"][section["line"] - 1:last]
    return " ".join(" ".join(lines).split())


def changed_blocks(before_text: str, after_text: str, language: str) -> list[str] | None:
    """The block ids whose text differs between two versions of a brief, in `BLOCK_IDS` order (D-228).

    A block that exists in only one of the two versions counts as changed. None when the text between
    the conclusions heading and its first block changed, or the omitted list did (D-229): no block id
    covers either, so nothing can be scoped.
    """
    before_parsed, after_parsed = parse_brief(before_text, language), parse_brief(after_text, language)
    if _conclusions_preamble(before_parsed) != _conclusions_preamble(after_parsed):
        return None
    if set(before_parsed["omitted"]) != set(after_parsed["omitted"]):
        return None
    before, after = block_texts(before_parsed), block_texts(after_parsed)
    changed = {block_id for block_id in set(before) | set(after) if before.get(block_id) != after.get(block_id)}
    rank = {block_id: index for index, block_id in enumerate(BLOCK_IDS)}
    return sorted(changed, key=lambda block_id: (rank.get(block_id, len(BLOCK_IDS)), block_id))


def writer_labels(language: str) -> str:
    """The localized labels the brief writer copies exactly: headings, header labels, the omitted leaves, risk
    line, wording."""
    code = lint.grammar(language).language
    heading = {kind: i18n.t(code, f"memo.brief.sections.{kind}") for kind in PART_KINDS}
    label = {key: i18n.t(code, f"memo.brief.{key}") for key in HEADER_KEYS}
    other = i18n.t(code, "memo.brief.other_label")
    return "\n".join(
        [
            "Part headings, in this order (each an H2, written exactly as here):",
            f"- `## {heading['main']}`",
            f"- `## {heading['conclusions']}`",
            f"- `## {heading['actions']}`",
            f"- `## {heading['assumptions']}` (only when there are assumptions that change the answer)",
            "Header lines under the title:",
            f"- `**{label['date_label']}:** YYYY-MM-DD`",
            f"- `**{label['jurisdictions_label']}:** …`",
            f"- `**{label['question_label']}:** …`",
            "The leaves you omit, in one comment under the header lines, above the first part: "
            "`<!-- omitted §s-8 §s-10-1 -->` (`<!-- omitted -->` when every leaf is kept).",
            "The omitted leaves in one sentence of the conclusions part, before its first `###`: "
            f"`{other}: <subject> (<risk as the memorandum states it>); <subject> (…).`",
            f"Word budgets, at most (body words; citation tokens and comments are not counted): «{heading['main']}» "
            f"{limits.DECISION_BRIEF_MAIN_WORDS}; each conclusion block {limits.DECISION_BRIEF_BLOCK_WORDS}; the "
            f"{other} sentence {limits.DECISION_BRIEF_OTHER_WORDS}; each action {limits.DECISION_BRIEF_ACTION_WORDS}; "
            f"«{heading['assumptions']}» one paragraph, no list, {limits.DECISION_BRIEF_ASSUMPTIONS_WORDS}.",
            f"Risk line of every conclusion block: `{lint.grammar(code).risk_literal}` followed by one sentence.",
            f"Wording for anything not confirmed: «{i18n.t(code, 'memo.brief.unconfirmed')}»",
        ]
    )


def brief_words(parsed: dict) -> int:
    """B-01 count: body words + heading words + a fixed weight per unique cited source."""
    document = parsed["document"]
    headings = 0
    for heading in document["headings"]:
        title = lint.SRC_TOKEN.sub("", _clean_title(heading["title"]))
        headings += len(title.split())
    unique = {token["id"] for token in document["src_tokens"]}
    return lint.body_words(document) + headings + limits.DECISION_BRIEF_SOURCE_WORD_WEIGHT * len(unique)


# --- rules --------------------------------------------------------------------


def _finding(
    rule: str, line: int | None, section_id: str, excerpt: str, hint: str, severity: str | None = None
) -> dict:
    """One finding in the `lint.finding` shape, with the B-rule severity unless one is given."""
    return lint.finding(rule, line, section_id, excerpt, hint, severity=severity or SEVERITY[rule])


def _block_at(parsed: dict, line: int | None) -> str:
    """The block id a line of the brief belongs to, or `document`."""
    if line is None:
        return DOCUMENT_ID
    document = parsed["document"]
    section_id = lint.section_of(document, line)
    if section_id is None:
        first_h2 = next((s["line"] for s in document["sections"] if s["level"] == 2), None)
        return "s-header" if first_h2 is None or line < first_h2 else DOCUMENT_ID
    block_id = parsed["block_ids"].get(section_id)
    return block_id if block_id in BLOCK_IDS else DOCUMENT_ID


def _row_id(block: dict) -> str:
    return block["id"] if block["id"] in BLOCK_IDS else DOCUMENT_ID


def check_b01(parsed: dict) -> list[dict]:
    """Over the soft cap; `minor` up to the hard cap, so only a brief above it is revised for length (D-228).

    The hint asks for shortening by omission, never by compressing a kept sentence (D-229).
    """
    words = brief_words(parsed)
    cap = limits.DECISION_BRIEF_SOFT_CAP
    if words <= cap:
        return []
    return [
        _finding(
            "B-01",
            None,
            DOCUMENT_ID,
            "",
            f"The brief is {words} words; the target is {cap} (about three pages). Shorten by omission: "
            "list further leaves that meet no keep criterion as omitted and drop their assumptions and those of "
            "their actions the actions rule does not keep — an action due within 14 days of the memo date stays; "
            "never compress a kept sentence or drop a condition or a risk.",
            None if words > limits.DECISION_BRIEF_HARD_CAP else "minor",
        )
    ]


def check_b02(parsed: dict, language: str) -> list[dict]:
    out: list[dict] = []
    document = parsed["document"]
    parts = parsed["parts"]
    for kind in ("main", "conclusions", "actions"):
        if parts[kind] is None:
            name = i18n.t(language, f"memo.brief.sections.{kind}")
            out.append(_finding("B-02", None, DOCUMENT_ID, "", f"The «{name}» part is missing."))
    present = [(section["line"], kind) for kind, section in parts.items() if section is not None]
    order = [kind for _, kind in sorted(present)]
    if order != [kind for kind in PART_KINDS if kind in order]:
        names = ", ".join(f"«{i18n.t(language, f'memo.brief.sections.{kind}')}»" for kind in PART_KINDS)
        out.append(_finding("B-02", None, DOCUMENT_ID, "", f"The parts are out of order; the order is {names}."))
    if parts["conclusions"] is not None:
        count = len(parsed["blocks"])
        if not 1 <= count <= limits.DECISION_BRIEF_MAX_BLOCKS:
            section = parts["conclusions"]
            out.append(
                _finding(
                    "B-02",
                    section["line"],
                    DOCUMENT_ID,
                    section["raw"],
                    f"The conclusions part has {count} blocks; it needs 1 to {limits.DECISION_BRIEF_MAX_BLOCKS}. "
                    "Merge secondary sections into one block.",
                )
            )
    first_h2 = next((s["line"] for s in document["sections"] if s["level"] == 2), len(document["lines"]) + 1)
    header = [line for number, line in enumerate(document["lines"], start=1) if number < first_h2]
    for key in HEADER_KEYS:
        label = re.escape(i18n.t(language, f"memo.brief.{key}"))
        pattern = re.compile(rf"^\s*(?:\*\*)?\s*{label}\s*(?:\*\*)?\s*:\s*(?:\*\*)?\s*\S", re.IGNORECASE)
        if not any(pattern.match(line) for line in header):
            name = i18n.t(language, f"memo.brief.{key}")
            out.append(
                _finding("B-02", None, "s-header", "", f"The «{name}» line is missing above the first part.")
            )
    return out


def _omitted_line(parsed: dict, section_id: str) -> int | None:
    """The line of the header comment that omits `section_id`."""
    return next((line for line, ids in omitted_comments(parsed["document"]) if section_id in ids), None)


def check_b03_b04(parsed: dict, facts: dict) -> list[dict]:
    """Bindings name leaves, the omitted list names leaves, and every leaf is kept or omitted, never both (D-229)."""
    out: list[dict] = []
    leaves = facts["leaves"]
    bound: set[str] = set()
    for block in parsed["blocks"]:
        if not block["bindings"]:
            out.append(
                _finding(
                    "B-03",
                    block["line"],
                    _row_id(block),
                    block["title"],
                    "The block has no binding: its first line is `<!-- from §s-… -->` naming the memo "
                    "sections it covers.",
                )
            )
            continue
        for section_id in block["bindings"]:
            if section_id in leaves:
                bound.add(section_id)
                continue
            out.append(
                _finding(
                    "B-03",
                    block["line"],
                    _row_id(block),
                    block["title"],
                    f"§{section_id} is not an analytical leaf section of the memo; bind the leaves "
                    f"({', '.join(leaves) or 'none'}).",
                )
            )
    omitted = parsed["omitted"]
    for section_id in omitted:
        line = _omitted_line(parsed, section_id)
        if section_id not in leaves:
            hint = (f"§{section_id} in the omitted comment is not an analytical leaf section of the memo; list "
                    f"leaves only ({', '.join(leaves) or 'none'}).")
        elif section_id in bound:
            hint = (f"Leaf {section_id} is both bound by a block and listed as omitted; a leaf is either kept in "
                    "a block or omitted, never both.")
        else:
            continue
        out.append(_finding("B-03", line, "s-header", f"§{section_id}", hint))
    for section_id in leaves:
        if section_id not in bound and section_id not in omitted:
            heading = facts["headings"].get(section_id, "")
            out.append(
                _finding(
                    "B-04",
                    None,
                    DOCUMENT_ID,
                    heading,
                    f"Leaf {section_id} ({heading}) of the memo is neither covered by a block nor listed as "
                    "omitted: bind it to a block, or list it in the header's `<!-- omitted … -->` comment when it "
                    "meets no keep criterion.",
                )
            )
    return out


def check_b05(parsed: dict, facts: dict, language: str) -> list[dict]:
    out: list[dict] = []
    document = parsed["document"]
    literal = document["grammar"].risk_literal
    unconfirmed = i18n.t(language, "memo.brief.unconfirmed").lower()
    open_level = i18n.t(language, f"memo.risk.levels.{UNDETERMINED}")
    for block in parsed["blocks"]:
        row_id = _row_id(block)
        known = [section_id for section_id in block["bindings"] if section_id in facts["leaves"]]
        verdicts = [facts["leaves"][section_id] for section_id in known]
        written = block["verdict_key"]
        if written is None:
            out.append(
                _finding(
                    "B-05",
                    block["line"],
                    row_id,
                    block["title"],
                    f"The block does not end with a risk line: its last paragraph is `{literal}` and one "
                    "sentence, as the memo writes it.",
                )
            )
        if len(set(verdicts)) > 1:
            # D-228: a block merges only leaves of one verdict — a mix is never summed up by the highest.
            if UNDETERMINED in verdicts:
                hint = (f"The block merges a section without a determinate risk with sections that have one "
                        f"({', '.join(known)}); give the undetermined section its own block.")
            else:
                levels = ", ".join(
                    f"{section_id} «{i18n.t(language, 'memo.risk.levels.' + verdict)}»"
                    for section_id, verdict in zip(known, verdicts)
                )
                hint = (f"The block merges sections with different risk verdicts in the memo ({levels}); merge "
                        "only sections of one verdict and give each other verdict its own block.")
            out.append(_finding("B-05", block["line"], row_id, block["title"], hint))
        elif verdicts and written is not None and written != verdicts[0]:
            out.append(
                _finding(
                    "B-05",
                    block["line"],
                    row_id,
                    block["title"],
                    f"The block's risk is «{i18n.t(language, f'memo.risk.levels.{written}')}»; the memo "
                    f"gives «{i18n.t(language, f'memo.risk.levels.{verdicts[0]}')}» for {', '.join(known)}.",
                )
            )
        text = _block_text(document, block).lower()
        for section_id in known:
            if section_id in facts["riskless"] and unconfirmed not in text:
                out.append(
                    _finding(
                        "B-05",
                        block["line"],
                        row_id,
                        block["title"],
                        f"Leaf {section_id} has no risk line in the memo: write its risk as «{open_level}» "
                        f"and say in the block that it is «{unconfirmed}».",
                    )
                )
    high = i18n.t(language, "memo.risk.levels.high")
    for section_id in parsed["omitted"]:
        if facts["leaves"].get(section_id) == "high":  # D-229: a high-risk leaf is always kept
            heading = facts["headings"].get(section_id, "")
            out.append(
                _finding(
                    "B-05",
                    _omitted_line(parsed, section_id),
                    "s-header",
                    f"§{section_id}",
                    f"Leaf {section_id} ({heading}) has the risk «{high}» in the memo and is listed as omitted; a "
                    "high-risk leaf is always kept: bind it to a block and take it off the omitted list.",
                )
            )
    return out


def _section(document: dict, block: dict) -> dict:
    return next(s for s in document["sections"] if s["line"] == block["line"])


def _block_text(document: dict, block: dict) -> str:
    """The prose of one block on a single line, so a wrapped wording still matches."""
    paragraphs = lint.section_paragraphs(document, _section(document, block))
    return " ".join(" ".join(paragraph["text"] for paragraph in paragraphs).split())


def check_b06(parsed: dict, facts: dict) -> list[dict]:
    out: list[dict] = []
    for token in parsed["document"]["src_tokens"]:
        if _pair(token) in facts["pairs"]:
            continue
        shown = f"[[src:{token['id']} {token['pinpoint']}]]" if token["pinpoint"] else f"[[src:{token['id']}]]"
        out.append(
            _finding(
                "B-06",
                token["line"],
                _block_at(parsed, token["line"]),
                token["text"],
                f"Token {shown} is not cited that way in the memo.",
            )
        )
    return out


def check_b07(parsed: dict) -> list[dict]:
    out: list[dict] = []
    document = parsed["document"]
    marker = re.compile(r"<!--\s*sources\s*:\s*generated\s*-->", re.IGNORECASE)
    for number, line in enumerate(document["lines"], start=1):
        if number in document["code_lines"]:
            continue
        if "[[q:" in line:
            out.append(
                _finding(
                    "B-07",
                    number,
                    _block_at(parsed, number),
                    line.strip(),
                    "The brief carries no quotation: drop the `[[q:]]` token and state the point in "
                    "your own words with its `[[src:]]` token.",
                )
            )
        if marker.search(line):
            out.append(
                _finding(
                    "B-07",
                    number,
                    _block_at(parsed, number),
                    line.strip(),
                    "The brief has no Sources list: remove the sources marker.",
                )
            )
    for quote in document["blockquotes"]:
        out.append(
            _finding(
                "B-07",
                quote["start_line"],
                _block_at(parsed, quote["start_line"]),
                quote["text"],
                "The brief carries no quotation: turn the blockquote into a sentence of your own.",
            )
        )
    return out


def check_b08(parsed: dict) -> list[dict]:
    out: list[dict] = []
    document = parsed["document"]
    grammar = document["grammar"]
    label = re.escape(i18n.t(grammar.language, "memo.risk.label"))
    looks_like = re.compile(rf"^\s*(?:\*\*)?{label}(?:\*\*)?\s*[:\-–—]", re.IGNORECASE)
    for block in parsed["blocks"]:
        paragraphs = lint.section_paragraphs(document, _section(document, block))
        for index, paragraph in enumerate(paragraphs):
            if not looks_like.match(paragraph["text"]):
                continue
            if not paragraph["bullet"] and grammar.risk_line.match(paragraph["text"]):
                if index < len(paragraphs) - 1:
                    out.append(
                        _finding(
                            "B-08",
                            paragraph["start_line"],
                            _row_id(block),
                            paragraph["text"],
                            "The risk line is the last paragraph of the block, as in the memo; move it to "
                            "the end.",
                        )
                    )
                continue
            out.append(
                _finding(
                    "B-08",
                    paragraph["start_line"],
                    _row_id(block),
                    paragraph["text"],
                    f"Risk line format: a paragraph of its own that starts with `{grammar.risk_literal}` "
                    "(exact case, closing period, no bullet or bold), as in the memo.",
                )
            )
    return out


def check_b09(parsed: dict, language: str) -> list[dict]:
    out: list[dict] = []
    # Whole words, case-blind: a word edge wherever the entry starts or ends alphanumeric (the
    # `lint.placeholder_pattern` rule), so `memo` does not flag «memory» or «memorial».
    words = [
        (str(word), re.compile(lint.placeholder_pattern(str(word)), re.IGNORECASE))
        for word in i18n.node(language, "memo.brief.self_reference")
    ]
    for paragraph in parsed["document"]["paragraphs"]:
        text = lint.Q_TOKEN.sub("", lint.SRC_TOKEN.sub("", paragraph["text"]))
        hit = next((word for word, pattern in words if pattern.search(text)), None)
        if hit is None:
            continue
        out.append(
            _finding(
                "B-09",
                paragraph["start_line"],
                _block_at(parsed, paragraph["start_line"]),
                paragraph["text"],
                f"«{hit}» points at a document the reader does not have; the brief stands on its own.",
            )
        )
    return out


def check_b10(parsed: dict) -> list[dict]:
    """Placeholders (as L-11, `blocker`) and AI-tells (as L-04, `major`) under the brief's own rule id."""
    document = parsed["document"]
    out: list[dict] = []
    for rows, severity in ((lint.check_l11(document), "blocker"), (lint.check_l04(document), SEVERITY["B-10"])):
        for row in rows:
            block_id = _block_at(parsed, row["line"])
            out.append(_finding("B-10", row["line"], block_id, row["excerpt"], row["hint"], severity))
    return out


def check_b11(parsed: dict, facts: dict) -> list[dict]:
    """Every amount of the brief occurs among the memo's amounts, spaces aside (D-229); dates are not checked."""
    out: list[dict] = []
    for line, raw in amounts(parsed["document"]):
        if normalise_amount(raw) in facts["amounts"]:
            continue
        out.append(
            _finding(
                "B-11",
                line,
                _block_at(parsed, line),
                raw,
                f"The amount «{raw}» is not in the memo; write every amount exactly as the memo gives it, with "
                "its qualifier (a ceiling, «up to», a formula, a condition).",
            )
        )
    return out


def _paragraphs(document: dict, first: int, last: int) -> list[dict]:
    """The prose paragraphs that start between two lines of the brief, both included."""
    return [paragraph for paragraph in document["paragraphs"] if first <= paragraph["start_line"] <= last]


def _quotes(document: dict, first: int, last: int) -> list[dict]:
    """The blockquotes that start between two lines of the brief, both included."""
    return [quote for quote in document["blockquotes"] if first <= quote["start_line"] <= last]


def _words(document: dict, first: int, last: int) -> int:
    """Body words between two lines exactly as `lint.body_words` counts them for B-01: paragraphs without their
    tokens, plus blockquotes (fix round 3) — comments and headings excluded."""
    prose = sum(len(lint.Q_TOKEN.sub("", lint.SRC_TOKEN.sub("", row["text"])).split())
                for row in _paragraphs(document, first, last))
    return prose + sum(len(lint.Q_TOKEN.sub("", quote["text"]).split()) for quote in _quotes(document, first, last))


def _actions(document: dict, first: int, last: int) -> list[tuple[dict, int]]:
    """`(item, last line)` per numbered or bulleted item: each action runs to the next item or the part's end, so the
    paragraphs and blockquotes that continue it count with it; text before the first item is no action."""
    items = [row for row in _paragraphs(document, first, last) if row["bullet"]]
    ends = [row["start_line"] - 1 for row in items[1:]] + [last]
    return list(zip(items, ends))


def check_b12(parsed: dict, language: str) -> list[dict]:
    """Every part within its word budget (D-229 fix round 2); one finding per part over it."""
    document = parsed["document"]
    parts = parsed["parts"]
    out: list[dict] = []

    def heading(kind: str) -> str:
        return i18n.t(language, f"memo.brief.sections.{kind}")

    def over(section_id: str, line: int, excerpt: str, what: str, count: int, budget: int, how: str = "") -> None:
        out.append(_finding(
            "B-12", line, section_id, excerpt,
            f"{what} is {count} words; its budget is {budget}. {how or 'Rewrite it in fewer, plainer words'}, or "
            "leave out detail the decision does not need; keep every condition, qualifier and risk.",
        ))

    main = parts["main"]
    if main is not None:
        count = _words(document, main["line"], main["end_line"])
        if count > limits.DECISION_BRIEF_MAIN_WORDS:
            over("s-main", main["line"], main["raw"], f"«{heading('main')}»", count, limits.DECISION_BRIEF_MAIN_WORDS)
    conclusions = parts["conclusions"]
    if conclusions is not None:
        last = parsed["blocks"][0]["line"] - 1 if parsed["blocks"] else conclusions["end_line"]
        count = _words(document, conclusions["line"] + 1, last)
        if count > limits.DECISION_BRIEF_OTHER_WORDS:
            label = i18n.t(language, "memo.brief.other_label")
            over(DOCUMENT_ID, conclusions["line"] + 1, "", f"The «{label}» text before the first block", count,
                 limits.DECISION_BRIEF_OTHER_WORDS, "Name each omitted section by its subject and its risk only")
    for block in parsed["blocks"]:
        count = _words(document, block["line"], block["end_line"])
        if count > limits.DECISION_BRIEF_BLOCK_WORDS:
            over(_row_id(block), block["line"], block["title"], f"The block «{block['title']}»", count,
                 limits.DECISION_BRIEF_BLOCK_WORDS)
    actions = parts["actions"]
    if actions is not None:
        for item, end in _actions(document, actions["line"], actions["end_line"]):
            count = _words(document, item["start_line"], end)
            if count > limits.DECISION_BRIEF_ACTION_WORDS:
                over("s-actions", item["start_line"], item["text"], f"The action «{item['text'][:60]}…»",
                     count, limits.DECISION_BRIEF_ACTION_WORDS, "An action is one line — who, what, by when")
    assumptions = parts["assumptions"]
    if assumptions is not None:
        first, last = assumptions["line"], assumptions["end_line"]
        paragraphs = _paragraphs(document, first, last) + _quotes(document, first, last)
        count = _words(document, first, last)
        shaped = len(paragraphs) <= 1 and not any(row.get("bullet") for row in paragraphs)
        if count > limits.DECISION_BRIEF_ASSUMPTIONS_WORDS or not shaped:
            over("s-assumptions", assumptions["line"], assumptions["raw"],
                 f"«{heading('assumptions')}» ({len(paragraphs)} paragraph(s) or list items)", count,
                 limits.DECISION_BRIEF_ASSUMPTIONS_WORDS,
                 "Write it as one paragraph, no list, naming only the few conditions that would change the overall "
                 "answer; the condition of one conclusion goes beside it in its block")
    return out


def lint_brief(brief_text: str, memo_text: str, *, language: str) -> list[dict]:
    """Run B-01…B-12 over one brief against the memo it summarises."""
    code = lint.grammar(language).language
    parsed = parse_brief(brief_text, code)
    facts = memo_facts(memo_text, code)
    findings: list[dict] = []
    findings += check_b01(parsed)
    findings += check_b02(parsed, code)
    findings += check_b03_b04(parsed, facts)
    findings += check_b05(parsed, facts, code)
    findings += check_b06(parsed, facts)
    findings += check_b07(parsed)
    findings += check_b08(parsed)
    findings += check_b09(parsed, code)
    findings += check_b10(parsed)
    findings += check_b11(parsed, facts)
    findings += check_b12(parsed, code)
    findings.sort(key=lambda row: (row["rule"], row["line"] if row["line"] is not None else 0))
    return findings


def build_report(brief_sha: str, findings: list[dict]) -> dict:
    """The `lint.json` shape of one brief version (validated against `lint.schema.json`)."""
    return lint.build_report(brief_sha, findings)
