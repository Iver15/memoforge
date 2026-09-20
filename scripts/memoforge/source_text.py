"""What a saved legal text is: where the document starts, whose requisites it carries, whether it is whole (D-203).

Pure text judgement, and nothing else. The module imports `re` and `unicodedata` and nothing from this package:
`sources.py` imports it (task 4), the vsrf.ru and sudact.ru resolvers call it to pick the right act among
candidates, and keeping the dependency one-way is what keeps both possible and the module testable alone.

Admission is not decided here. Whether a response was truncated, dead, an access stub or an interstitial is the
caller's question about the *response*; this module answers only about the *text* it is handed.

The article heading grammar is not forked: `ARTICLE_HEADING_PREFIX` is the source string of
`sources.ARTICLE_HEADING_PREFIX`, copied rather than imported to avoid the circular import, and a test pins the
two strings equal so the alternation can never drift apart.
"""

from __future__ import annotations

import re
import unicodedata

HEADER_ZONE_CHARS = 2500
"""How far a header reaches when no anchor word closes it earlier."""

PRE_HEADING_LINES = 8
PRE_HEADING_CHARS = 600
"""How far the number zone may reach back over the lines printed above the act-type heading."""

FULL_TEXT_MIN_CHARS_CASE = 4000
FULL_TEXT_MIN_CHARS_DOCTRINE = 2500
ARTICLE_BODY_MIN_CHARS = 200

ARTICLE_HEADING_PREFIX = (
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*"
    r"(?:Article|Art\.|Section|Sec\.|§|Статья|Ст\.)[ \t]*"
)
"""The source string of `sources.ARTICLE_HEADING_PREFIX` — copied, never forked (a test pins them equal)."""

ANY_ARTICLE_HEADING_RE = re.compile(ARTICLE_HEADING_PREFIX + r"\d+", re.MULTILINE | re.IGNORECASE)

_ACT_TYPES = ("определение", "постановление", "решение", "приговор")
_IMENEM = "именем российской федерации"
_OPERATIVE_MARKERS = ("определил", "определила", "постановил", "постановила", "решил", "решила", "приговорил")
_PLENUM_SIGNATURES = (
    "председатель верховного суда",
    "председатель высшего арбитражного суда",
    "секретарь пленума",
)
_PLENUM_SIGNATURE_RES = tuple(
    re.compile(r"\s+".join(re.escape(word) for word in signature.split()))
    for signature in _PLENUM_SIGNATURES
)
"""A signature is a phrase, and a page wraps it where it likes: `Председатель\\nВерховного Суда` is the same
signature as `Председатель Верховного Суда`, so the words are matched across any run of whitespace."""

_ACT_QUALIFIERS = ("апелляционное", "кассационное", "надзорное", "частное", "дополнительное")
"""The closed list of qualifiers an act-type heading may carry in front of the act type (fix round 1).

`АПЕЛЛЯЦИОННОЕ ОПРЕДЕЛЕНИЕ` and `КАССАЦИОННОЕ ПОСТАНОВЛЕНИЕ` are the ordinary titles of Russian
second-instance acts; without them the document had no start and the number zone lost its walk back. Nothing
else about the rule moves — a line carrying `по делу` or `от <дата>` is still the portal's heading, not the
document's.
"""
_BREADCRUMB_MARKS = ("»", "›", "→", ">")

_SPACED_TOKEN_RE = re.compile(r"^.[.,:;]?$", re.DOTALL)
"""One character of a letter-spaced word, with at most one punctuation mark glued to it.

The real `pypdf` layer of a Supreme Court order prints `о п р е д е л и л:` — the colon stuck to the last
letter — so a rule that only knew single-character tokens broke the run there and read `определи л:`.
"""

_DASHES = "‐‑‒–—―−"
_WS_RUN_RE = re.compile(r"\s+")
_DIGIT_EDGE_RE = re.compile(r"(?<=\d)(?=\D)|(?<=\D)(?=\d)")
_NUMBER_SIGN_RE = re.compile(r"(?<![0-9a-zа-я])(?:№|no|n)\.?(?=\s*[0-9])")
"""`№`, `N` and `No` in front of a number are one and the same sign (NFKC has already spelt `№` as `No`)."""

_PORTAL_DATE_RE = re.compile(r"(?<![а-я])от\s+\d")
_LIST_LINE_RE = re.compile(r"^(?:[-‐-―−*•·]|\d{1,3}[.)])\s")

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_DMY_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_WORDS_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(sorted(_MONTHS)) + r")\s+(\d{4})(?!\d)")
"""Dates in words are Russian only; digits and ISO are read in any language (D-203)."""


def _spaced(word: str) -> str:
    """A pattern for `word` that survives letter spacing: `У С Т А Н О В И Л` is still `установил`."""
    return r"[ \t]*".join(re.escape(char) for char in word)


def _anchor(*words: str) -> re.Pattern:
    body = "|".join(_spaced(word) for word in words)
    return re.compile(r"(?<![а-яa-z])(?:" + body + r")(?![а-яa-z])", re.IGNORECASE)


_USTANOVIL_RE = _anchor("установила", "установил")
_RASSMOTREV_RE = _anchor("рассмотрела", "рассмотрев")
_DATE_ANCHOR_RE = _anchor("рассмотрела", "рассмотрев", "изучив")
"""The verb after which the reviewed acts' dates begin, and with them the end of the date zone.

`рассмотрев`/`рассмотрела` in an act that hears a case, `изучив` in a judge's referral order — the same
sentence in the same place, so the same boundary (fix round 2). D-203 names `изучив` only as an example of a
text with no anchor; on the real `pypdf` layer of a referral order the reviewed acts are recited immediately
after it, well inside `HEADER_ZONE_CHARS`, which is what made the fallback let a stranger's date through.
"""

_POSTANOVLYAET_RE = _anchor("постановляет")


def flatten(line: str) -> str:
    """One line with its letter spacing removed, lower-cased, `ё` folded to `е`, dashes and `№` normalised.

    Every anchor word, act-type heading, operative marker and date in words is matched on this form, so a PDF
    text layer that prints `О П Р Е Д Е Л Е Н И Е` or `2 7 о к т я б р я 2 0 2 5 года` reads like any other.
    A run of single-character tokens is glued back into one word and then cut again wherever digits meet
    letters, which is what keeps a spaced date three words instead of one.
    """
    text = unicodedata.normalize("NFKC", line).lower().replace("ё", "е")
    for dash in _DASHES:
        text = text.replace(dash, "-")
    text = _WS_RUN_RE.sub(" ", text).strip()
    if not text:
        return ""
    text = _NUMBER_SIGN_RE.sub("№", text)
    out: list[str] = []
    run: list[str] = []
    for token in text.split(" "):
        if _SPACED_TOKEN_RE.match(token):
            run.append(token)
            continue
        out.extend(_unspace(run))
        run = []
        out.append(token)
    out.extend(_unspace(run))
    return " ".join(part for part in out if part)


def _unspace(run: list[str]) -> list[str]:
    if len(run) < 2:
        return list(run)
    return _DIGIT_EDGE_RE.sub(" ", "".join(run)).split(" ")


def _lines(text: str):
    offset = 0
    for line in text.splitlines(True):
        yield offset, line
        offset += len(line)


def _starts_with_act_type(flat: str) -> bool:
    """Whether the line opens with an act type, optionally behind one qualifier of the closed list."""
    for qualifier in _ACT_QUALIFIERS:
        if flat.startswith(qualifier):
            return flat[len(qualifier) :].lstrip().startswith(_ACT_TYPES)
    return flat.startswith(_ACT_TYPES)


def _is_act_heading(flat: str) -> bool:
    """A heading is a line that *begins* with the act type and names neither a date nor a case.

    Headings carry tails («ПОСТАНОВЛЕНИЕ арбитражного суда кассационной инстанции») and qualifiers
    («АПЕЛЛЯЦИОННОЕ ОПРЕДЕЛЕНИЕ»), while the portal's own heading above the document carries `от <дата>` or
    `по делу` — and its date is often a day early.
    """
    if not _starts_with_act_type(flat):
        return False
    return "по делу" not in flat and not _PORTAL_DATE_RE.search(flat)


def _own_start(text: str) -> int | None:
    for offset, line in _lines(text):
        flat = flatten(line)
        if _is_act_heading(flat) or flat.startswith(_IMENEM):
            return offset
    return None


def document_start(text: str) -> int:
    """Offset of the document's own beginning — below the portal menu and below the portal heading (D-203).

    The earlier of the first act-type heading and the first «Именем Российской Федерации». A PDF text layer has
    no portal above it, so the rule finds the heading at the top or nothing at all and the zone starts at 0.
    """
    start = _own_start(text)
    return 0 if start is None else start


def is_russian_act(text: str) -> bool:
    """Whether this is a Russian judicial act — read off the text, never off a flag (`save` has no jurisdiction).

    A Cyrillic act-type heading, «Именем Российской Федерации», or an anchor word inside the header zone.
    """
    if _own_start(text) is not None:
        return True
    start = document_start(text)
    header = text[start : start + HEADER_ZONE_CHARS]
    return bool(_USTANOVIL_RE.search(header) or _RASSMOTREV_RE.search(header))


def _zone_end(text: str, anchor: re.Pattern, start: int) -> int:
    limit = start + HEADER_ZONE_CHARS
    match = anchor.search(text, start)
    if match is not None and match.start() < limit:
        return match.start()
    return limit


def _is_stop_line(flat: str) -> bool:
    """The portal heading and a breadcrumb line end the walk back — they are the portal's text, not the act's."""
    if flat.startswith("главная") or any(mark in flat for mark in _BREADCRUMB_MARKS):
        return True
    return _starts_with_act_type(flat) and not _is_act_heading(flat)


def _walk_back(text: str, start: int) -> int:
    if start <= 0:
        return 0
    above = [(offset, line) for offset, line in _lines(text) if offset < start]
    zone_start = start
    for offset, line in reversed(above[-PRE_HEADING_LINES:]):
        if start - offset > PRE_HEADING_CHARS or _is_stop_line(flatten(line)):
            break
        zone_start = offset
    return zone_start


def number_zone(text: str) -> tuple[int, int]:
    """Where the case number may stand: the header down to «установил(а)», plus the lines above the heading.

    Acts of the Supreme Court and many first-instance decisions print the number above the act-type heading and
    never repeat it, so the zone reaches back over the court name, `№ …`, `Дело № …` and the УИД — stopping at
    the portal heading or a breadcrumb. The number in the portal heading is harmless: it belongs to the whole
    chain of acts.
    """
    start = document_start(text)
    return _walk_back(text, start), _zone_end(text, _USTANOVIL_RE, start)


def date_zone(text: str) -> tuple[int, int]:
    """Where the act's own date may stand: the header down to «рассмотрев»/«рассмотрела»/«изучив», never back.

    Backwards lies the portal heading, whose date is a day early; forwards, past the verb, lie the dates of the
    acts under review — that is true of a referral order's «изучив» exactly as it is of «рассмотрев». A text
    with none of the three (a Plenum ruling) keeps the whole header zone.
    """
    start = document_start(text)
    return start, _zone_end(text, _DATE_ANCHOR_RE, start)


def _slice(text: str, zone: tuple[int, int]) -> str:
    start, end = zone
    start = max(0, start)
    end = min(len(text), end)
    return text[start:end] if end > start else ""


def _normalise(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return _NUMBER_SIGN_RE.sub("№", _WS_RUN_RE.sub(" ", text))


_NUMBER_LEFT = r"(?<![0-9a-zа-я/\-])"
_NUMBER_RIGHT = r"(?![0-9a-zа-я/\-])(?!\.[0-9])(?!\s*\([\d\s,]*\))"
"""The last guard is the twins trap: `305-ЭС24-8702` is not the act numbered `305-ЭС24-8702 (1,3)`."""


def has_number(text: str, expected: str, zone: tuple[int, int]) -> bool:
    """Whether `expected` is printed as a whole token inside `zone` (D-203).

    Whitespace runs collapse and spaces inside the token are optional, `-`/`–`/`—` are one dash, `№`/`N`/`No`
    are one sign, `ё` is `е`; the match must be bounded by whitespace or punctuation, and a base number never
    matches a number with a bracketed suffix or the other way round.
    """
    core = _normalise(expected).replace(" ", "")
    if not core:
        return False
    pattern = _NUMBER_LEFT + _spaced(core) + _NUMBER_RIGHT
    return re.search(pattern, _normalise(_slice(text, zone))) is not None


def _dates_in(chunk: str):
    for line in chunk.splitlines():
        if _LIST_LINE_RE.match(line.lstrip()):
            continue
        flat = flatten(line)
        for day, month, year in _DMY_RE.findall(flat):
            yield _date(year, month, day)
        for year, month, day in _ISO_RE.findall(flat):
            yield _date(year, month, day)
        for day, name, year in _WORDS_DATE_RE.findall(flat):
            yield _date(year, _MONTHS[name], day)


def _date(year, month, day) -> tuple[int, int, int] | None:
    year, month, day = int(year), int(month), int(day)
    if 1 <= month <= 12 and 1 <= day <= 31:
        return year, month, day
    return None


def has_date(text: str, expected: str, zone: tuple[int, int]) -> bool:
    """Whether `expected` is one of the dates printed inside `zone` (D-203).

    `DD.MM.YYYY`, `YYYY-MM-DD` and Russian words are all read; words only in Russian. Two dates in a header
    (the operative part announced, the full text produced) both count. A date on a list line is the portal's
    index of neighbouring acts, not the act's own date, and is not used.
    """
    wanted = _parse_date(expected)
    if wanted is None:
        return False
    return wanted in set(_dates_in(_slice(text, zone)))


def _parse_date(value: str) -> tuple[int, int, int] | None:
    for parsed in _dates_in(value):
        if parsed is not None:
            return parsed
    return None


def is_complete_ru_act(text: str) -> bool:
    """Whether a Russian judicial act carries its operative marker after its «установил(а)» (D-203).

    The marker stands alone on its line (`определил(а)`, `постановил(а)`, `решил(а)`, `приговорил`, a trailing
    colon allowed), which keeps a digest whose prose says «суд определил взыскать» out. The test is order, not
    a fraction of the text, so a long portal tail behind a short act changes nothing.
    """
    opened = _USTANOVIL_RE.search(text, document_start(text))
    if opened is None:
        return False
    for offset, line in _lines(text):
        if offset <= opened.start():
            continue
        flat = flatten(line)
        word = flat[:-1] if flat.endswith(":") else flat
        if word in _OPERATIVE_MARKERS:
            return True
    return False


def is_complete_plenum(text: str) -> bool:
    """Whether a normative Plenum ruling is whole: «постановляет» in the header zone and a closing signature.

    A Plenum ruling has neither «установил» nor an operative marker, so the ordinary predicate cannot see it.
    The predicate is chosen by the text and not by the title: `verdict` tries the ordinary one first, which is
    how a Presidium ruling with «установил … постановил:» passes as the judicial act it is.
    """
    start = document_start(text)
    if not _POSTANOVLYAET_RE.search(text[start : start + HEADER_ZONE_CHARS]):
        return False
    body = "\n".join(flatten(line) for line in text.splitlines())
    return any(signature.search(body) is not None for signature in _PLENUM_SIGNATURE_RES)


def article_body_chars(text: str, article: str) -> int:
    """Length of the requested article, from its heading to the next article heading or the end; 0 if absent.

    The heading is matched as a whole token with the same alternation `sources.py` uses, so `152` never finds
    `152.1`, and a table of contents («Статья 36 / Статья 37») yields a body far under `ARTICLE_BODY_MIN_CHARS`.
    """
    wanted = article.strip()
    if not wanted:
        return 0
    heading = re.compile(
        ARTICLE_HEADING_PREFIX + re.escape(wanted) + r"(?!\d)(?!\.\d)",
        re.MULTILINE | re.IGNORECASE,
    )
    match = heading.search(text)
    if match is None:
        return 0
    following = ANY_ARTICLE_HEADING_RE.search(text, match.end())
    end = following.start() if following is not None else len(text)
    return end - match.start()


def verdict(
    text: str,
    *,
    layer: str,
    expect_number: str | None = None,
    expect_date: str | None = None,
    expect_article: str | None = None,
) -> dict:
    """What the saved text is, in the terms `mf sources save` and the resolvers need (D-203).

    `{"raw_kind": "full_text" | "excerpt", "outcome": "full_text" | "excerpt:<reason>",
      "error": None | "requisites_mismatch", "found": {...}}`. Only a positive signal promotes a text;
    anything undecided stays an excerpt.

    A Russian judicial act needs both requisites, and a requisite missing from its zone is
    `requisites_mismatch` — the caller refuses and nothing is registered. Outside that rule the same failure is
    only `excerpt:identity_unverified`: the asymmetry is deliberate, because the Russian rule is the one v1
    certifies.
    """
    russian = is_russian_act(text)
    found = {"number": False, "date": False, "russian": russian, "chars": len(text)}

    def answer(outcome: str, error: str | None = None) -> dict:
        return {
            "raw_kind": "full_text" if outcome == "full_text" else "excerpt",
            "outcome": outcome,
            "error": error,
            "found": found,
        }

    if layer == "case_law" and russian:
        if not expect_number or not expect_date:
            return answer("excerpt:identity_unverified")
        found["number"] = has_number(text, expect_number, number_zone(text))
        found["date"] = has_date(text, expect_date, date_zone(text))
        if not (found["number"] and found["date"]):
            return answer("excerpt:identity_unverified", "requisites_mismatch")
        if is_complete_ru_act(text) or is_complete_plenum(text):
            return answer("full_text")
        if len(text) < FULL_TEXT_MIN_CHARS_CASE:
            return answer("excerpt:no_reasoning")
        return answer("excerpt:not_verified")

    if layer == "case_law":
        if len(text) < FULL_TEXT_MIN_CHARS_CASE:
            return answer("excerpt:too_short")
        if expect_number:
            found["number"] = has_number(text, expect_number, (0, HEADER_ZONE_CHARS))
        if not found["number"]:
            return answer("excerpt:identity_unverified")
        return answer("full_text")

    if layer == "statutes":
        if not expect_article:
            return answer("excerpt:identity_unverified")
        body = article_body_chars(text, expect_article)
        found["number"] = body > 0
        if body == 0:
            return answer("excerpt:article_not_found")
        if body < ARTICLE_BODY_MIN_CHARS:
            return answer("excerpt:too_short")
        return answer("full_text")

    if layer == "doctrine":
        if len(text) >= FULL_TEXT_MIN_CHARS_DOCTRINE:
            return answer("full_text")
        return answer("excerpt:too_short")

    return answer("excerpt:not_verified")
