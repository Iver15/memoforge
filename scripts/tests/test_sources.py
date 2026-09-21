"""Tests for scripts/memoforge/sources.py — registry, freeze, verifications (ТЗ §5.3, M5/M6, §9)."""

from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import multiprocessing
import shutil
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from memoforge import cli, i18n, limits, schema, source_text, sources, state_io, task  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _i18n  # noqa: E402

TASK_ID = "memo-20260101T000000Z-sources"
WORKERS = 6

RAW_TEXT = (
    "# Article 6 - Lawfulness of processing\n"
    "\n"
    "1. Processing shall be lawful only if and to the extent that at least one of the following applies.\n"
    "\n"
    "(a) the data subject has given consent to the processing of his or her personal data;\n"
    "\n"
    "Consent must be freely given, specific, informed and unambiguous.\n"
)

LIVE_TEXT = RAW_TEXT + "Consent must be freely given, specific, informed and unambiguous.\n" * 300
"""D-146: what a page has to be to pass liveness — over `LIVENESS_MIN_BODY_BYTES`, all text."""

CHANGED_BODY = b"a different document\n" * 1000
"""The `/changed` answer, long enough that `changed` is about the sha and not about the size."""

ECFR_STUB = (
    b"<!DOCTYPE html><html><head><title>Federal Register :: Request Access</title>"
    + b"<style>.wrapper{max-width:60rem;margin:0 auto;padding:2rem;font-family:system-ui,sans-serif}"
      b".captcha{display:block;margin-top:1rem}</style>" * 38
    + b"<script>window.captchaConfig={sitekey:'0x0000000000000000000000',action:'unblock'};</script>" * 38
    + b'</head><body class="wrapper"><h1>Request Access</h1>'
    b"<p>Due to aggressive automated scraping of FederalRegister.gov and eCFR.gov, programmatic "
    b"access to these sites is limited to access to our extensive developer APIs.</p>"
    b"<p>Please visit FederalRegister.gov API documentation or eCFR.gov API documentation to learn "
    b"more about how to access the API.</p>"
    b"<p>Your request has been flagged as potentially automated. If you are a human user receiving "
    b"this message, please complete the CAPTCHA (bot test) below and click &quot;Request "
    b"Access&quot;. You may occasionally be asked to complete the CAPTCHA again, this is normal and "
    b"part of our security measures.</p>"
    b"<p>An official website of the United States government. If you are experiencing issues with "
    b"the CAPTCHA or want to request a wider IP range, please contact the site administrators and "
    b"include the IP range, the user agent string and the reason programmatic access is needed.</p>"
    b"</body></html>"
)
"""The «Request Access» page eCFR serves under HTTP 200 (analysis/38 §5.1, D-149).

Shaped like the real one, measured on 2026-09-13 under the plugin UA: 10 596 bytes, `text/html`,
visible-text ratio 0.0939, 1 181 characters of prose (this fixture: 9 800 / 0.078 / 905). It is over
the size floor **and** over the text ratio, so only `CHALLENGE_PHRASES` can catch it.
"""

CURIA_SHELL = (
    b"<html><head><script>"
    + b"var tabs=function(){return 1;};" * 4000
    + b"</script><style>.x{color:#000}</style></head><body>RPEX</body></html>"
)
"""D-146: the 130 KB JS shell curia returns for every case, six characters of text (analysis/38 §2).

Far over the size floor, so only the text-ratio rule can catch it.
"""

COURDECASSATION_SHELL = (
    b'<html lang="en"><head></head><body><script>window.location.href='
    b"'/redirect_UUPBRX2EHTQ7LZSPMMI5YUOWB6JMCHEPNJG7PECWR2U6YBECRG5Q====/decision/"
    b"613727b1cd5801467742d425';</script><noscript>This website requires JS enabled and cookies"
    b"</noscript></body></html>"
)
"""D-149: `courdecassation.fr/decision/<id>` — HTTP 200, 255 bytes of JS redirect (analysis/39 §9.2).

Text ratio 0.149, so the ratio rule never sees it; the fourth shape of a false-positive 200.
"""

REDIRECT_SHELL = (
    b'<html><head><meta http-equiv="refresh" content="0;url=/atto/caricaArticolo?sessione=1">'
    b"<title>Normattiva</title></head><body>"
    + b'<div class="wrapper" data-role="placeholder" data-index="0000"></div>' * 28
    + b"<p>Attendere, reindirizzamento in corso verso la sessione dell'atto. Se la pagina non si "
      b"apre, abilitare i cookie del portale e ricaricare l'indirizzo richiesto.</p>"
    + b"</body></html>"
)
"""D-149: the same redirect, 2 236 bytes — over the size floor and over the ratio, 169 chars of text.

Only the JS/meta-refresh rule can catch this one, which is why that rule is not the size rule.
"""

BDSG_PAGE = (
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN">\n'
    b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>BDSG - Einzelnorm</title>'
    b'<link rel="stylesheet" href="/jportal/css/screen.css" media="screen"/></head><body>'
    b'<div class="jurAbsatz"><h3>&#167; 26 Datenverarbeitung f&#252;r Zwecke des '
    b"Besch&#228;ftigungsverh&#228;ltnisses</h3>"
    + b'<p class="jurAbsatz">Personenbezogene Daten von Besch&auml;ftigten d&uuml;rfen f&uuml;r '
      b"Zwecke des Besch&auml;ftigungsverh&auml;ltnisses verarbeitet werden, wenn dies fuer die "
      b"Entscheidung ueber die Begruendung eines Beschaeftigungsverhaeltnisses erforderlich "
      b"ist.</p>" * 26
    + b"</div></body></html>"
)
"""The canonical `§ 26 BDSG` pinpoint page: 9 560 bytes, ratio 0.506 on 2026-09-13 (analysis/39 §9.1).

`gesetze-im-internet.de/bdsg_2018/__26.html` is the *preferred* address of the DE statute row, and
the 15 KB floor of D-146 called it an interstitial.
"""

LEGISLATION_SNIPPET = (
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN">\n'
    b'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en"><head>'
    b"<title>Data Protection Act 2018, Section 2</title></head><body>"
    + b'<div class="LegSnippet" typeof="leg:LegislationSection" '
      b'about="http://www.legislation.gov.uk/id/ukpga/2018/12/section/2">'
      b'<span class="LegDS LegLHS LegP1No">(1)</span>'
      b'<span class="LegP1Text">The GDPR, the applied GDPR and this Act protect individuals.</span>'
      b"</div>" * 27
    + b"</body></html>"
)
"""`legislation.gov.uk/…/data.xht?view=snippet`: 7 367 bytes of `application/xhtml+xml`, ratio 0.147.

The «ideal 7 KB pinpoint» of analysis/38 §5 — the second address D-146 rejected by size.
"""

RIS_JSON = (
    b'{"OgdSearchResult":{"OgdDocumentResults":{"Hits":{"@pageNumber":"1","@pageSize":"10",'
    b'"#text":"436"},"OgdDocumentReference":['
    + b'{"Data":{"Metadaten":{"Technisch":{"ID":"NOR40200612"},"Allgemein":{"DokumentUrl":'
      b'"https://www.ris.bka.gv.at/Dokument.wxe?Abfrage=Bundesnormen&Dokumentnummer=NOR40200612"},'
      b'"Bundesrecht":{"Titel":"Datenschutzgesetz","Paragraphennummer":"1"}}}},' * 30
    + b'{"Data":{"Metadaten":{"Technisch":{"ID":"NOR40200613"}}}}]}}}'
)
"""The RIS OGD API answer — `application/json`, 8 112 bytes on the sweep (analysis/39 §9.1).

D-145 made `data.bka.gv.at` the **only** route into Austrian law, and the size rule rejected it.
"""

CELLAR_SNIPPET = (
    b'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
    b'<title>Regulation</title><style>p{margin:0}</style><script>var x=1;</script></head><body>'
    b'<div class="eli-container"><p class="oj-ti-art">Article 9</p>'
    b'<p class="oj-sti-art">Processing of special categories of personal data</p>'
    b'<p class="oj-normal">1.&#160;&#160;&#160;Processing of personal data revealing racial or ethnic origin '
    b'shall be prohibited.</p><p class="oj-ti-art">Article 10</p><p class="oj-normal">Processing of personal '
    b'data relating to criminal convictions.</p></div></body></html>'
)
"""D-163: Cellar/EUR-Lex answer XHTML, so a shortened GDPR shape for the markup-to-text tests."""

SOURCE_TEXT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "source_text"
"""D-203: the saved forms task 3 certifies; `mf sources save` serves them as pages (D-199)."""


def act_page(*names: str) -> bytes:
    """One or more D-203 fixtures inside the page a portal would serve them on (D-199).

    `<pre>` keeps the line structure `source_text` reads, and the declared charset is what
    `markup_to_text` decodes with — the conversion is part of what `save` has to get right.
    """
    texts = ((SOURCE_TEXT_FIXTURES / (name + ".txt")).read_text(encoding="utf-8") for name in names)
    return portal_page("\n".join(texts))


def portal_page(body: str) -> bytes:
    """`act_page` for a text already in hand — the same wrapping, so the same conversion (D-202)."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Портал</title></head><body><pre>'
        + body
        + "</pre></body></html>"
    ).encode("utf-8")


VS_ACT_PAGE = act_page("vs-html-act")
"""The page of определение ВС РФ № 5-КГ25-14-К2 — the whole act, requisites and all."""

VS_ACT_TEXT = (SOURCE_TEXT_FIXTURES / "vs-html-act.txt").read_bytes()
"""The same act as a portal that declares no `Content-Type` and sends no markup serves it."""

VS_ACT_CUT = VS_ACT_PAGE.index("определила:".encode("utf-8")) + len("определила:".encode("utf-8"))
"""Where the gate's cut fell: the body ends on the operative marker, its ruling and signatures gone.

Everything `source_text` looks for is still there — the requisites, «установила», the marker on its
own line — so before D-199's fix round 2 those bytes certified as `full_text`.
"""

VS_ACT_VARIANT = VS_ACT_PAGE.replace(b"</pre>", "\nОпубликовано на портале.\n</pre>".encode("utf-8"))
"""The same act with one more portal line: the same requisites, a different digest."""

VS_ACT_NUMBER = "5-КГ25-14-К2"
VS_ACT_DATE = "2025-03-04"
VS_ACT_TITLE = "ВС РФ, определение № 5-КГ25-14-К2"
VS_ACT_CITATION = "Определение ВС РФ от 04.03.2025 № 5-КГ25-14-К2"

CASSATION_PAGE = act_page("cassation-ruling")
"""Another act entirely — the page that must never land under an occupied id (D-143)."""

STATUTE_PAGE = act_page("statute-table-of-contents", "statute-article-152")
"""A statute page carrying ст. 152 whole and ст. 36 as a table-of-contents line only."""

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
"""A PDF with no cross-reference table: `pypdf` cannot read it, so it carries no text layer (D-201)."""

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(64)
"""A media type that is neither text nor a PDF — `unsupported_media_type` stands for those (D-201)."""

PDF_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pdf"
"""D-201: the two real PDFs this command is measured on, served exactly as a portal serves them."""

VSRF_PDF = (PDF_FIXTURES / "vsrf-305-es24-8702.pdf").read_bytes()
VSRF_PDF_SHA256 = "9216ab6a5bb553658b572b3be8c98a474dff7358416c05223b034c705438fdda"
"""The vsrf.ru PDF of a judge's referral order, and the digest of the bytes the server sends."""

VSRF_PDF_NUMBER = "305-ЭС24-8702 (1,3)"
VSRF_PDF_DATE = "2024-07-11"
VSRF_PDF_TITLE = "ВС РФ, определение № 305-ЭС24-8702 (1,3)"
VSRF_PDF_CITATION = "Определение ВС РФ от 11.07.2024 № 305-ЭС24-8702 (1,3)"

SCAN_PDF = (PDF_FIXTURES / "scan-no-text-layer.pdf").read_bytes()
SCAN_PDF_SHA256 = "f236ea6c0cd988b5c4a083e8faa2fcbb1656febd2029b2729646c16db6752db5"
"""One page whose only content stream is an inline image: `pypdf` extracts the empty string."""

SCAN_PDF_OTHER = SCAN_PDF + b"%edited\n"
"""Another scan: still a PDF, still no text layer, and not the same bytes (fix round 1)."""

VSRF_PDF_OTHER = VSRF_PDF + b"%tampered\n"
"""Another PDF that **does** carry a text layer, so a save of it has both halves to offer."""

VSRF_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vsrf"
"""D-202: the real `305-ЭС24-8702` chain as vsrf.ru served it — the listing and seven of the eight PDFs."""

VSRF_LISTING = (VSRF_FIXTURES / "listing-305-es24-8702.html").read_bytes()
"""`GET /lk/practice/acts?numberExact=true&actDateExact=off&number=305-ЭС24-8702`, gzip on the wire.

349 776 bytes of React shell: eight unique `/lk/practice/stor_pdf_ec/<id>` links, each printed six
times — three copies in the markup and three more inside the JSON the page hydrates from. Its
visible-text ratio is 0.029, under `LIVENESS_MIN_TEXT_RATIO`, which is why a listing is never
judged by the rules that judge a document.
"""

VSRF_CHAIN: tuple[str, ...] = (
    "2394482",
    "2394462",
    "2394388",
    "2394370",
    "2383940",
    "2383918",
    "2383886",
    "2383828",
)
"""The eight acts of the chain, in the order the real listing prints them (newest first).

Four pairs: every act is served under two ids with **different bytes** and an **identical**
normalised text layer — `2394482`/`2394462` are `(1,3)` of 14.08.2024, `2394388`/`2394370` are
`(2,4)` of the same day, `2383940`/`2383828` are `(1,3)` of 11.07.2024, `2383918`/`2383886` are
`(2,4)`. Deduplicating candidates by the byte digest would fail here every time.
"""

VSRF_TWIN = (VSRF_FIXTURES / "model-ambiguous-twin.pdf").read_bytes()
"""The only model among the fixtures: `2394482` with its second page printed twice.

It keeps the number, the date and the operative marker, so `verdict` answers `full_text` for it
exactly as for the original while its normalised text differs — the one case the real chain does
not hold: two genuinely different documents sharing both requisites.
"""

VSRF_CHAMBER_DATE = "2024-08-14"
"""The chamber's ruling. `VSRF_PDF_DATE` (11.07.2024) is the judge's referral order in the same
chain, carrying the same number **and** the same bracketed suffix: only the date tells them apart."""

VSRF_CHAMBER_SHA256 = "09268f5db09c3cd3cca682b5bc09115920562c3309b1b4dec1b1c760355afb88"
"""`2394482.pdf` — the bytes the winner of the twin trap must end up holding."""

VSRF_REFERRAL_SHA256 = "d9061f7da360fd21471dace14c764c47cd86c12206e31e1e3ab05de8f815a049"
"""`2383940.pdf` — the referral order of 11.07.2024 as the listing prints it first (before `2383828`)."""

VSRF_ACT_TITLE = "ВС РФ, определение № 305-ЭС24-8702 (1,3) от 14.08.2024"
VSRF_ACT_CITATION = "Определение ВС РФ от 14.08.2024 № 305-ЭС24-8702 (1,3)"


def vsrf_path(act: str, kind: str = "stor_pdf_ec") -> str:
    """The portal's address of one act's PDF; both `stor_pdf` and `stor_pdf_ec` are real (D-202)."""
    return f"/lk/practice/{kind}/{act}"


def vsrf_act(act: str) -> bytes:
    """The PDF the portal serves under `<id>` (D-202).

    `2383828` is not checked in twice: it is byte for byte the Task 5 fixture, and that file is
    served for it.
    """
    if act == "2383828":
        return VSRF_PDF
    return (VSRF_FIXTURES / f"{act}.pdf").read_bytes()


VSRF_CHAIN_BODIES: dict = {vsrf_path(act): vsrf_act(act) for act in VSRF_CHAIN}
"""`<path>: <bytes>` of the whole chain, read once — what the `LocalServer` serves as the portal."""


VSRF_LISTING_SCRIPTS = "<script>self.__next_f=self.__next_f||[];self.__next_f.push([1])</script>" * 36
"""The weight of a React page: script, no visible text (D-202).

It keeps a hand-written listing above `LIVENESS_MIN_BODY_BYTES` and, like the real page, far under
`LIVENESS_MIN_TEXT_RATIO` — so every test on a hand-written listing also proves that the ratio rule
is the one set aside, and the size rule is not.
"""


def vsrf_listing(*paths: str, repeat: int = 1) -> bytes:
    """A hand-written listing page carrying exactly `paths`, in that order (D-202).

    The real page is used wherever the portal's markup is the point; this one is for the rules that
    are about the resolver — the cap, an empty answer — and not about the portal.
    """
    links = "".join(f'<a href="{path}" target="_blank">Определение</a>' * repeat for path in paths)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Электронная справочная</title>'
        + VSRF_LISTING_SCRIPTS
        + "</head><body>"
        + links
        + "</body></html>"
    ).encode("utf-8")


def with_act_link(page: bytes, path: str) -> bytes:
    """`page` with one act link planted in it, so a resolver that read past it would fetch the act."""
    return page.replace(b"</body>", f'<a href="{path}">Определение</a></body>'.encode("utf-8"), 1)


def vsrf_routes(listing: bytes, bodies: dict) -> dict:
    """`_Handler.routes` for one listing and the acts it points at (D-202)."""
    routes = {sources.VSRF_LISTING_PATH: (listing, "text/html; charset=utf-8")}
    routes.update({path: (body, "application/pdf") for path, body in bodies.items()})
    return routes


SUDACT_ACT_TEXT = (SOURCE_TEXT_FIXTURES / "sudact-cassation-a53-28950-2022.txt").read_text(encoding="utf-8")
"""D-202: the real cassation ruling in case А53-28950/2022 as sudact.ru served it (Task 3's fixture).

The portal heading above the act says «Постановление от 26 октября 2025 г.» — sudact's own metadata,
a day early and inherited by LDH — while the act is of 27 October, printed in letter spacing.
"""

SUDACT_ACT_PAGE = act_page("sudact-cassation-a53-28950-2022")
SUDACT_NUMBER = "А53-28950/2022"
SUDACT_DATE = "2025-10-27"
SUDACT_PORTAL_DATE = "2025-10-26"
"""The date sudact's listing and the portal heading give that act: a day early, never to be trusted."""

SUDACT_TITLE = "АС Северо-Кавказского округа, постановление по делу № А53-28950/2022"
SUDACT_CITATION = "Постановление АС Северо-Кавказского округа от 27.10.2025 по делу № А53-28950/2022"

SUDACT_ACT = "/arbitral/doc/VLaG5lHDfBoJ/"
"""The portal's address of that act — the id sudact's own search and LDH `RU/Sudact` both give."""

SUDACT_COOKIE = "sessionid=TESTSESSION"
"""A cookie a portal *may* set — a placeholder, never a real one. The real section page set none."""

SUDACT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sudact"
"""D-202: three real answers of sudact.ru, captured by the controller on 2026-09-21, byte for byte.

Three polite requests 2.5-3 s apart, stopped at the first sign of a challenge — there was none.
"""

SUDACT_SECTION_PAGE = (SUDACT_FIXTURES / "section-arbitral.html").read_bytes()
"""`GET /arbitral/` — 200 `text/html`, 82 681 bytes, visible-text ratio 0.526: no interstitial.

**It set no cookie**, and the search that followed worked all the same: the session jar carries
whatever the portal sets and requires nothing. It is no court act either (`is_russian_act` is
False), which is what makes it the wall in the tests of the resolved save.
"""

SUDACT_NEW = (SUDACT_FIXTURES / "doc-ajax-a53-28950-2022-new.json").read_bytes()
"""The first answer of the search for А53-28950/2022 — 200 `application/json`, 41 bytes: `new`."""

SUDACT_FINISHED = (SUDACT_FIXTURES / "doc-ajax-a53-28950-2022-finished.json").read_bytes()
"""The same search 3 s later — 200 `application/json`, 5 856 bytes, `status: finished`.

`content` is the HTML list with four `/arbitral/doc/<id>/` links; `total_found` is **not a
number** but an HTML fragment («Найдено 4 документа»), and it is never read as a count.
"""

SUDACT_LISTED: tuple[str, ...] = (
    "/arbitral/doc/VLaG5lHDfBoJ/",
    "/arbitral/doc/jX0PqHjyDV5Y/",
    "/arbitral/doc/QlLbj6Wn1AA/",
    "/arbitral/doc/fOQWwXJVAwL9/",
)
"""The four acts of А53-28950/2022 in the real listing's order, which the listing dates a day early:
the cassation ruling (listed 26.10.2025, of 27.10.2025), the appeal (listed 18.06.2025), the first
instance (listed 16.04.2025) and an earlier appeal (listed 26.06.2024)."""

SUDACT_DEFENCE = (b"", "", {"Location": "/defence/?next=/arbitral/"}, 302)
"""A challenge as the portal answers one: a redirect to its captcha at `/defence/`."""


def sudact_finished(*paths: str, title: str = "Постановление от 26 октября 2025 г. по делу № А53-28950/2022") -> bytes:
    """`finished`, with the HTML list of documents the portal's own script renders (D-202).

    Every entry carries the listing's own date — a day early, as sudact's metadata always is — so a
    resolver that took the date from anywhere but the document's text would be caught by it.
    """
    items = "".join(f'<li><a href="{path}?snippet_pos=1#snippet">{title}</a></li>' for path in paths)
    content = f'<ul class="results">{items}</ul>'
    return json.dumps({"status": "finished", "content": content}, ensure_ascii=False).encode("utf-8")


def sudact_routes(search: list, documents: dict, section: str = "arbitral") -> dict:
    """`_Handler.routes` for the portal: the section page, its search, `/defence/` and the documents.

    `search` is what the search answers, one body per request, the last one repeating; a document
    is its page, or a whole route when a test needs a status code or a sequence of its own.
    `/defence/` is served, so a client that followed the challenge would be seen fetching it. The
    section page is the real one, and like the real one it sets no cookie.
    """
    routes = {
        f"/{section}/": (SUDACT_SECTION_PAGE, "text/html; charset=utf-8"),
        f"/{section}/doc_ajax/": [body if isinstance(body, tuple) else (body, "application/json") for body in search],
        "/defence/": (ECFR_STUB, "text/html"),
    }
    for path, body in documents.items():
        routes[path] = body if isinstance(body, (tuple, list)) else (body, "text/html; charset=utf-8")
    return routes


def sudact_search_path(number: str = SUDACT_NUMBER, section: str = "arbitral") -> str:
    """The address of the portal's search for `number`, as the server records it."""
    return f"/{section}/doc_ajax/?{section}-case_doc={urllib.parse.quote(number, safe='')}&page=1"


def sudact_model(date: str, portal_date: str) -> bytes:
    """A labelled MODEL of another act of case А53-28950/2022: the real page with its own dates moved.

    Not court text of its own. Four dates of the header change and nothing else — the act's date
    line (in the letter spacing the real page prints it in), the day the operative part was
    announced, the day the full text was made, and the portal heading a day earlier — because that
    is exactly where, for the resolver, the acts of one case differ from each other.
    """
    day, month, year = date.split()
    text = SUDACT_ACT_TEXT
    for old, new in (
        ("2 7 о к т я б р я 2025", " ".join(day + month) + f" {year}"),
        ("объявлена 23 октября 2025", f"объявлена {date}"),
        ("изготовлено 27 октября 2025", f"изготовлено {date}"),
        ("Постановление от 26 октября 2025 г.", f"Постановление от {portal_date} г."),
    ):
        if old not in text:
            raise AssertionError(f"the real page no longer carries {old!r}")
        text = text.replace(old, new, 1)
    return portal_page(text)


SUDACT_CASE: dict = {
    SUDACT_LISTED[0]: SUDACT_ACT_PAGE,
    SUDACT_LISTED[1]: sudact_model("19 июня 2025", "18 июня 2025"),
    SUDACT_LISTED[2]: sudact_model("17 апреля 2025", "16 апреля 2025"),
    SUDACT_LISTED[3]: sudact_model("27 июня 2024", "26 июня 2024"),
}
"""The four acts the real listing names, under its real ids and in its order (`SUDACT_LISTED`).

The first is the real cassation ruling. The other three pages were not captured, so they are the
labelled models of `sudact_model`, dated as the real listing dates them plus the day sudact's
metadata takes off (the appeal of 19.06.2025 and the first instance of 17.04.2025 are also the
dates the real ruling itself cites). Each carries the same number: only the date in the document's
own text tells the four apart.
"""


class FakeClock:
    """The channel's clock in a test: it moves only when the code waits (D-202).

    No test sleeps and no test reads a real clock. The pace is computed from `now`; every wait the
    code asks for is recorded and then added to `now`, as if it had been slept.
    """

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.waits: list = []

    def time(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


def sources_lock_is_free(work_dir: Path) -> bool:
    """Whether another thread could take `sources.lock` at this very moment — asked once, never waited.

    The lock stack is per thread, so the probe runs in a thread of its own and really asks the OS.
    """
    outcome: list = []

    def probe() -> None:
        try:
            with state_io.FileLock(state_io.lock_path(work_dir, "sources"), timeout=0):
                outcome.append(True)
        except state_io.LockTimeout:
            outcome.append(False)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(10)
    return outcome == [True]


def save_namespace(work_dir: str, url: str, **overrides) -> dict:
    """The namespace `mf sources save` parses, with the defaults its parser gives (D-199)."""
    payload = {
        "workdir": work_dir,
        "layer": "case_law",
        "title": VS_ACT_TITLE,
        "citation": VS_ACT_CITATION,
        "tier": "critical",
        "url": url,
        "resolve": None,
        "id": None,
        "meta": None,
        "identifiers": None,
        "expect_number": None,
        "expect_date": None,
        "expect_article": None,
        "method": "GET",
        "json_body": None,
        "accept": None,
        "lang": None,
        "timeout": 5.0,
    }
    payload.update(overrides)
    return payload


# --- helpers shared with the spawned children ------------------------------


def issue_step(work_dir: Path, step_id: str, attempt: int = 1) -> None:
    """Put an open `steps[]` record in state the way `mf next` issues it (§3.1, D-40)."""

    def mutator(state: dict) -> None:
        rows = [row for row in state.get("steps") or [] if isinstance(row, dict)]
        if any(row.get("step_id") == step_id and int(row.get("attempt") or 1) == attempt for row in rows):
            return
        rows.append(
            {
                "step_id": step_id,
                "kind": "script",
                "phase": state.get("current_phase"),
                "attempt": attempt,
                "reason": "initial",
                "issued_at": "2026-01-01T00:00:00.000Z",
                "status": None,
            }
        )
        state["steps"] = rows

    state_io.write_state(work_dir, mutator)


def publish(work_dir: Path, canonical: str) -> None:
    """Record the file at `canonical` in `published[]` with its current sha (§2.2)."""
    from memoforge import stepctx

    entry = {
        "canonical_path": canonical,
        "sha256": state_io.sha256_file(Path(work_dir) / canonical),
        "by": "step",
        "step_id": "s-005",
        "at": "2026-01-01T00:00:00.000Z",
    }
    state_io.write_state(work_dir, lambda state: stepctx.merge_published(state, [entry]))


def make_task(root: Path, task_id: str = TASK_ID) -> Path:
    """Work dir with a schema-valid v2 state.json."""
    work_dir = Path(root) / task_id
    task.create_work_dir_tree(work_dir)
    state = task.build_initial_state(
        task_id=task_id,
        user_query="sources",
        language="en",
        work_dir=work_dir,
        output_folder=Path(root),
        config={
            "writer_model": "opus",
            "source_review_gate": "auto",
            "intake_max_questions": 5,
            "template_id": "classical-memo",
            "mcp_budget": {"ldh": 8, "courtlistener": 10},
        },
        created_at="2026-01-01T00:00:00.000Z",
    )
    state_io.create_state(work_dir, state)
    return work_dir


def wait_for(path: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.02)
    return False


def worker_register(work_dir: str, index: int, shared: bool, ready: str, go: str, result: str) -> None:
    """Child process: one `sources register` call, reported back as JSON."""
    sys.path.insert(0, str(Path(work_dir).parents[1] / "scripts"))
    from memoforge import sources as child_sources

    Path(ready).write_text("ready", encoding="utf-8")
    wait_for(go)
    outcome: dict = {"index": index, "error": None}
    try:
        suffix = "shared" if shared else f"n{index}"
        outcome["result"] = child_sources.register_source(
            work_dir,
            layer="statutes",
            title=f"GDPR Article {suffix}",
            citation=f"GDPR, Art. {suffix}",
            url=f"https://eur-lex.europa.eu/{suffix}",
            tool="LDH",
            tier="supporting",
        )
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        outcome["error"] = repr(exc)
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def worker_freeze(work_dir: str, step: str, ready: str, go: str, result: str) -> None:
    """Child process: `sources pack --freeze`."""
    sys.path.insert(0, str(Path(work_dir).parents[1] / "scripts"))
    from memoforge import sources as child_sources

    Path(ready).write_text("ready", encoding="utf-8")
    wait_for(go)
    args = argparse.Namespace(workdir=work_dir, freeze=True, step=step, attempt=1, phase=None)
    outcome: dict = {"error": None}
    try:
        outcome["result"] = child_sources.run_pack(args)
    except BaseException as exc:  # noqa: BLE001
        outcome["error"] = repr(exc)
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def worker_save(work_dir: str, url: str, barrier, result: str) -> None:
    """Child process: one `mf sources save` of `url`, reported back as JSON (D-199 step 3).

    The barrier is what makes the two children race: both block in it and leave it together, so
    nothing here polls a clock or sleeps.
    """
    sys.path.insert(0, str(Path(work_dir).parents[1] / "scripts"))
    from memoforge import sources as child_sources

    # The allowlist of the child is its own: the test server lives on a loopback port.
    child_sources.allowlist_hosts = lambda root=None: frozenset({child_sources.url_host(url)})
    outcome: dict = {"error": None}
    try:
        barrier.wait(timeout=120)
        outcome["result"] = child_sources.run_save(
            argparse.Namespace(
                **save_namespace(work_dir, url, expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            )
        )
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        outcome["error"] = repr(exc)
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def worker_save_killed_before_publishing(work_dir: str, url: str, overrides: dict, paused, result: str) -> None:
    """Child process: a save that is terminated between the registry write and the publication.

    The parent kills it while it sits in `write_registry`'s successor, so neither `except` nor
    `finally` runs — a forced termination, not a mocked exception.
    """
    sys.path.insert(0, str(Path(work_dir).parents[1] / "scripts"))
    from memoforge import sources as child_sources

    child_sources.allowlist_hosts = lambda root=None: frozenset({child_sources.url_host(url)})
    written = child_sources.write_registry

    def write_then_wait_to_be_killed(child_work_dir, registry):
        path = written(child_work_dir, registry)
        paused.set()
        threading.Event().wait(120)  # blocks until the parent terminates this process
        return path

    child_sources.write_registry = write_then_wait_to_be_killed
    outcome: dict = {"error": None}
    try:
        outcome["result"] = child_sources.run_save(
            argparse.Namespace(**save_namespace(work_dir, url, **overrides))
        )
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        outcome["error"] = repr(exc)
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def worker_save_killed_between_publications(work_dir: str, url: str, paused, result: str) -> None:
    """Child process: a save terminated **between** the two renames that publish a PDF pair.

    D-201 fix round 3: the original lands first and the text second, so a kill in that window
    leaves the registry naming both files while only the `.pdf` exists. The pause is inside
    `os.replace` itself, so it is a forced termination at exactly that point and not a mock.
    """
    sys.path.insert(0, str(Path(work_dir).parents[1] / "scripts"))
    from memoforge import sources as child_sources

    child_sources.allowlist_hosts = lambda root=None: frozenset({child_sources.url_host(url)})
    replace = child_sources.os.replace

    def replace_then_wait_to_be_killed(src, dst):
        replace(src, dst)
        if str(dst).endswith(".pdf"):
            paused.set()
            threading.Event().wait(120)  # blocks until the parent terminates this process

    child_sources.os.replace = replace_then_wait_to_be_killed
    outcome: dict = {"error": None}
    try:
        outcome["result"] = child_sources.run_save(argparse.Namespace(**save_namespace(work_dir, url)))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        outcome["error"] = repr(exc)
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def worker_reserve(work_dir: str, count: int, barrier, result: str) -> None:
    """Child process: `count` slots of the sudact channel, reserved on a clock that never moves (D-202).

    Every child reads the same fixed time, so the only thing that can keep two of them off the same
    slot is the reservation made under `sources.lock` in `channels.json` — which is what is tested.
    The barrier releases the children together: no sleep and no real clock.
    """
    sys.path.insert(0, str(Path(work_dir).parents[1] / "scripts"))
    from memoforge import sources as child_sources

    child_sources._channel_clock = lambda: 1000.0
    outcome: dict = {"error": None, "slots": []}
    try:
        barrier.wait(timeout=120)
        for _ in range(count):
            outcome["slots"].append(1000.0 + child_sources.channel_reserve(work_dir, "sudact"))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        outcome["error"] = repr(exc)
    Path(result).write_text(json.dumps(outcome), encoding="utf-8")


def spawn(target, args):
    return multiprocessing.get_context("spawn").Process(target=target, args=args)


# --- mock http server ------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b""

    def log_message(self, *args):  # noqa: D102 - silence the test output
        return

    codes = {"/accepted": 202, "/forbidden": 403, "/throttled": 429, "/down": 503}
    """D34-08: the answers that mean «not served» rather than «gone»."""

    challenges = {
        "/waf": (202, {"x-amzn-waf-action": "challenge"}),
        "/cloudflare": (403, {"Cf-Mitigated": "challenge"}),
        "/cloudflare-down": (503, {"Cf-Mitigated": "challenge"}),
    }
    """D-146: the same codes, but with the header that names the interstitial (analysis/38 §7.4)."""

    pages = {
        "/stub": ECFR_STUB,
        "/shell": CURIA_SHELL,
        "/ris.json": RIS_JSON,
        "/act.pdf": PDF_BYTES,
        "/image.png": PNG_BYTES,
    }
    """D-146: HTTP 200 answers that are not the document — plus the RIS JSON that is one (D-149)."""

    types = {
        "/ris.json": "application/json; charset=utf-8",
        "/stub": "text/html",
        "/act.pdf": "application/pdf",
        "/image.png": "image/png",
        # D-201: the configured body, declared as the PDF it is — the ordinary portal answer.
        "/served.pdf": "application/pdf",
    }
    """D-149: the declared `Content-Type`, which is what `is_markup` and `fetch` read."""

    variants: list = []
    """D-199: consecutive bodies of `/varying`, so two saves of one url see two different texts."""

    served = 0

    short_bytes: int | None = None
    """D-199: how much of `/short` really reaches the client; the header still promises all of it."""

    length_suffix = ""
    """D-199: trailing whitespace on `Content-Length`, which Python's parser accepts (fix round 3)."""

    seen: list = []
    """Every request the server received, so a test can assert on the headers `probe_url` sent."""

    posted: list = []
    """D-151: the bodies of the POSTs, so a test can assert on the `--json` document that was sent."""

    locations = {"/redirect": "/ok", "/loop": "/loop", "/badhop": "ftp://evil.example/x"}
    """D-151: the `Location` of each redirecting path; `/offsite` needs the live port, see `_respond`."""

    routes: dict = {}
    """D-202: `<path without its query>: (body, content type)` — one table per test, empty by default.

    The resolver drives a whole portal rather than one address: a listing at `/lk/practice/acts`
    whose query carries the case number, and the acts of the chain at `/lk/practice/stor_pdf_ec/<id>`.
    The fixed `pages`/`types` tables cannot express that, and a path this table does not name falls
    through to them unchanged.

    Task 7 (sudact): a route may carry a third item, the extra headers of the answer, and a fourth,
    its status code — the section page sets the session cookie, a challenge answers `302` to
    `/defence/`. A **list** of routes is served one per request, the last one repeating, because the
    portal's search answers `new` first and `finished` later on the very same address.
    """

    route_hits: dict = {}
    """D-202: how many requests each list-valued route has answered so far."""

    def _route(self) -> tuple | None:
        """The `routes` entry of this request; the query is the resolver's, not the address's.

        Called once per request: a list-valued route advances by one on every call.
        """
        path = self.path.split("?")[0]
        route = type(self).routes.get(path)
        if isinstance(route, list):
            served = type(self).route_hits.get(path, 0)
            type(self).route_hits[path] = served + 1
            route = route[min(served, len(route) - 1)]
        return route

    def _payload(self, route: tuple | None) -> tuple[int, bytes]:
        if route is not None:
            return (route[3] if len(route) > 3 else 200), route[0]
        if self.path in ("/ok", "/short", "/served.pdf"):
            # D-199: `/short` declares this whole length and then sends two thirds of it.
            return 200, type(self).body
        if self.path == "/partial":
            return 206, type(self).body[: len(type(self).body) * 2 // 3]
        if self.path == "/varying" and type(self).variants:
            index = min(type(self).served, len(type(self).variants) - 1)
            type(self).served += 1
            return 200, type(self).variants[index]
        if self.path == "/changed":
            return 200, CHANGED_BODY
        if self.path == "/offsite" or self.path in self.locations:
            return 302, b""
        if self.path in self.pages:
            return 200, self.pages[self.path]
        if self.path in self.challenges:
            return self.challenges[self.path][0], b""
        if self.path in self.codes:
            return self.codes[self.path], b"<html>not the document</html>"
        return 404, b"missing"

    def _respond(self, with_body: bool) -> None:
        type(self).seen.append(
            {"path": self.path, "headers": {name.lower(): value for name, value in self.headers.items()}}
        )
        route = self._route()
        code, payload = self._payload(route)
        self.send_response(code)
        if code == 302 and route is None:
            # `localhost` is the same socket under another host name: an off-allowlist hop (D-151).
            offsite = f"http://localhost:{self.server.server_address[1]}/ok"
            self.send_header("Location", offsite if self.path == "/offsite" else self.locations[self.path])
        for name, value in self.challenges.get(self.path, (None, {}))[1].items():
            self.send_header(name, value)
        extra = route[2] if route is not None and len(route) > 2 else {}
        if route is not None:
            if route[1]:
                self.send_header("Content-Type", route[1])
            for name, value in extra.items():
                self.send_header(name, value)
        elif self.path in self.types:
            self.send_header("Content-Type", self.types[self.path])
        if "Content-Length" not in extra:
            # A route may promise more than it sends (task 7, round 2): a page cut short on the wire.
            self.send_header("Content-Length", str(len(payload)) + type(self).length_suffix)
        self.end_headers()
        if with_body:
            if self.path == "/short":
                # The body ends mid-document although the header promised the whole of it: the
                # bounded read returns what arrived and raises nothing (D-199 fix round 2).
                cut = type(self).short_bytes
                payload = payload[: cut if cut is not None else len(payload) * 2 // 3]
            self.wfile.write(payload)

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._respond(False)

    def do_GET(self):  # noqa: N802
        self._respond(True)

    def do_POST(self):  # noqa: N802 - D-151: the Normattiva URN route is a POST
        length = int(self.headers.get("Content-Length") or 0)
        type(self).posted.append(
            {
                "path": self.path,
                "body": self.rfile.read(length) if length else b"",
                "content_type": self.headers.get("Content-Type") or "",
            }
        )
        self._respond(True)


class LocalServer:
    """`http.server` on localhost — not an external network call (§9 allows the mock)."""

    def __init__(
        self,
        body: bytes,
        variants: tuple = (),
        short_bytes: int | None = None,
        length_suffix: str = "",
        routes: dict | None = None,
    ) -> None:
        _Handler.body = body
        _Handler.seen = []
        _Handler.posted = []
        _Handler.variants = list(variants)
        _Handler.served = 0
        _Handler.short_bytes = short_bytes
        _Handler.length_suffix = length_suffix
        _Handler.routes = dict(routes or {})
        _Handler.route_hits = {}
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        # `poll_interval` is what `shutdown()` waits for: the default 0.5 s was half a second of
        # sleep per server, and nearly every test in this file starts one.
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# --- base ------------------------------------------------------------------


class SourcesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work_dir = make_task(self.root)
        self.addCleanup(self._tmp.cleanup)
        # D-146 owes a host `LIVENESS_HOST_DELAY_SECONDS` between two probes of it, and the suite
        # must never sit through that: every test records the pause instead of sleeping it, and the
        # two classes that assert on the politeness rule read `self.waits`.
        self.waits: list = []
        original_wait = sources._wait
        sources._wait = self.waits.append
        self.addCleanup(setattr, sources, "_wait", original_wait)

    def client_export(self, source_id: str, suffix: str = ".txt") -> str:
        """What the client's `sources/<id>.txt` really holds, staged through `finalize` itself.

        D-201 fix round 3: the export is no longer «read the raw file and re-encode it» — the
        bytes are staged and checked against the digest the freeze pinned — so a test about what
        reaches a client drives that path instead of a reader that no longer describes it.
        """
        from memoforge import finalize

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        target = root / "sources"
        target.mkdir()
        # D-201 fix round 4: an unverified copy waits outside the folder that will be delivered.
        finalize.stage_source_files(self.work_dir, target, root / "verifying")
        return (target / f"{source_id}{suffix}").read_text(encoding="utf-8")

    def raw_file(self, name: str = "raw.md", text: str = RAW_TEXT) -> Path:
        path = self.root / name
        # bytes, not write_text: the agent's file is hashed exactly as it is on disk (M5).
        path.write_bytes(text.encode("utf-8"))
        return path

    def register(self, **overrides) -> dict:
        payload = {
            "layer": "statutes",
            "title": "GDPR Article 6",
            "citation": "GDPR, Art. 6",
            "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            "tool": "mcp__ldh__get_document",
            "tier": "critical",
        }
        payload.update(overrides)
        return sources.register_source(self.work_dir, **payload)

    def write_findings(self, rows: list[dict], layer: str = "statutes") -> None:
        document = {
            "layer": layer,
            "issues": [
                {
                    "issue_id": row.get("issue_id", "i1"),
                    "findings": [
                        {
                            "source_id": row["source_id"],
                            "proposition": row.get("proposition", "A rule applies."),
                            "pinpoint": row.get("pinpoint", "Art. 6(1)(a)"),
                            "role": row.get("role", "rule"),
                            "weight": row.get("weight", "binding"),
                            "confidence": row.get("confidence", "high"),
                            "tier": row.get("tier", "critical"),
                            "quote_short": "consent",
                        }
                    ],
                }
                for row in rows
            ],
            "_meta": {"task_id": TASK_ID, "step_id": "s-005", "attempt": 1, "slot": layer},
        }
        state_io.write_json_atomic(self.work_dir / "research" / f"{layer}.json", document)

    def freeze(self, step: str = "s-010", attempt: int = 1, *, issue: bool = True) -> dict:
        if issue:
            issue_step(self.work_dir, step, attempt)
        args = argparse.Namespace(workdir=str(self.work_dir), freeze=True, step=step, attempt=attempt, phase=None)
        return sources.run_pack(args)


# --- register --------------------------------------------------------------


class RegisterTest(SourcesTestCase):
    def test_register_creates_the_record_and_moves_the_raw_file(self):
        raw = self.raw_file()
        result = self.register(raw_file=raw)
        self.assertTrue(result["created"])
        self.assertEqual("research/raw/statutes/gdpr-article-6.md", result["raw_path"])
        self.assertFalse(raw.exists(), "the temporary raw file is moved, not copied")
        stored = self.work_dir / result["raw_path"]
        self.assertEqual(RAW_TEXT, stored.read_text(encoding="utf-8"))
        self.assertEqual(state_io.sha256_file(stored), result["raw_sha256"])
        self.assertEqual(len(RAW_TEXT), result["raw_chars"])
        self.assertEqual("agent_saved", result["provenance"])

    def test_register_is_idempotent_by_layer_and_url(self):
        first = self.register()
        second = self.register(title="GDPR Article 6 (consolidated)")
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        registry = sources.read_registry(self.work_dir)
        self.assertEqual(1, len(registry["sources"]))
        self.assertEqual("GDPR Article 6 (consolidated)", registry["sources"][first["source_id"]]["title"])

    def test_register_is_idempotent_by_citation_form_when_there_is_no_url(self):
        first = self.register(url="", citation="Bygrave, Data Privacy Law (OUP 2014) 121")
        second = self.register(url="", citation="Bygrave, Data Privacy Law (OUP 2014) 121", title="Bygrave")
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))

    def test_the_same_url_in_another_layer_is_another_source(self):
        first = self.register()
        second = self.register(layer="doctrine", title="EDPB commentary on Article 6")
        self.assertNotEqual(first["source_id"], second["source_id"])
        self.assertEqual(2, len(sources.read_registry(self.work_dir)["sources"]))

    def test_slug_collision_gets_a_suffix(self):
        first = self.register(url="https://example.org/a")
        second = self.register(url="https://example.org/b")
        self.assertEqual("gdpr-article-6", first["source_id"])
        self.assertEqual("gdpr-article-6-2", second["source_id"])

    def test_an_occupied_source_id_with_other_text_is_a_collision(self):
        """D34-04: `sources register` never overwrites and never re-slugs an explicit id."""
        self.register(source_id="gdpr-art88", raw_file=self.raw_file(), url="https://example.org/a")
        other = self.raw_file(name="other.md", text="# Article 113 - Entry into force\n\nText.\n")
        result = self.register(
            source_id="gdpr-art88",
            title="AI Act Article 113",
            url="https://example.org/b",
            raw_file=other,
        )
        self.assertEqual(
            ["source_id_collision: gdpr-art88 already holds 'GDPR Article 6'"], result["errors"]
        )
        registry = sources.read_registry(self.work_dir)
        self.assertEqual(1, len(registry["sources"]))
        self.assertEqual("GDPR Article 6", registry["sources"]["gdpr-art88"]["title"])
        self.assertTrue(other.exists(), "a refused registration does not consume the raw file")

    def test_an_occupied_source_id_with_the_same_text_is_a_no_op(self):
        first = self.register(source_id="gdpr-art88", raw_file=self.raw_file(), url="https://example.org/a")
        again = self.register(
            source_id="gdpr-art88",
            url="https://example.org/b",
            raw_file=self.raw_file(name="copy.md"),
        )
        self.assertEqual("gdpr-art88", again["source_id"])
        self.assertFalse(again["created"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))

    def test_the_dedup_key_never_opens_the_occupied_id_to_other_text(self):
        """D-143: a repeat registration matching by url must not skip the collision check of D-127."""
        url = "https://example.org/same"
        self.register(source_id="gdpr-art88", raw_file=self.raw_file(), url=url)
        other = self.raw_file(name="other.md", text="# Article 113 - Entry into force\n\nText.\n")
        result = self.register(
            source_id="gdpr-art88", title="AI Act Article 113", url=url, raw_file=other
        )
        self.assertEqual(
            ["source_id_collision: gdpr-art88 already holds 'GDPR Article 6'"], result["errors"]
        )
        self.assertNotIn("idempotent", result)
        record = sources.read_registry(self.work_dir)["sources"]["gdpr-art88"]
        self.assertEqual("GDPR Article 6", record["title"])
        self.assertEqual(RAW_TEXT, (self.work_dir / record["raw_path"]).read_text(encoding="utf-8"))
        self.assertTrue(other.exists(), "a refused registration does not consume the raw file")

    def test_the_same_id_url_and_bytes_stay_a_no_op(self):
        first = self.register(
            source_id="gdpr-art88", raw_file=self.raw_file(), url="https://example.org/same"
        )
        again = self.register(
            source_id="gdpr-art88",
            raw_file=self.raw_file(name="copy.md"),
            url="https://example.org/same",
        )
        self.assertFalse(again["created"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))

    def test_reregistering_identical_html_under_the_same_id_is_a_no_op(self):
        """D-163: the explicit-id check hashes the converted text, like `store_raw` does."""
        first_raw = self.root / "gdpr.html"
        first_raw.write_bytes(CELLAR_SNIPPET)
        first = self.register(source_id="gdpr-art9", raw_file=first_raw, url="https://example.org/gdpr")
        second_raw = self.root / "gdpr-copy.html"
        second_raw.write_bytes(CELLAR_SNIPPET)
        again = self.register(source_id="gdpr-art9", raw_file=second_raw, url="https://example.org/gdpr")
        self.assertNotIn("errors", again)
        self.assertFalse(again["created"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])

    def test_an_explicit_source_id_is_never_suffixed(self):
        """D34-04: `<id>-2` is what put Art. 113 text under an `article-5` id in the audited run."""
        first = self.register(source_id="gdpr-art3", url="https://example.org/a")
        second = self.register(source_id="gdpr-art3", title="Other", url="https://example.org/b")
        self.assertEqual("gdpr-art3", first["source_id"])
        self.assertEqual("gdpr-art3", second["source_id"])
        self.assertNotIn("gdpr-art3-2", sources.read_registry(self.work_dir)["sources"])

    def test_an_occupied_id_without_hashes_on_either_side_is_a_collision(self):
        """D-206: citation-only records carry `None` on both sides — a stranger's text must not update."""
        import json

        self.register(source_id="bygrave", url="", citation="Bygrave, Data Privacy Law (OUP 2014) 121")
        before = json.loads(
            json.dumps(sources.read_registry(self.work_dir)["sources"]["bygrave"], sort_keys=True)
        )
        result = self.register(
            source_id="bygrave", url="", citation="Another Author, Other Book (OUP 2020) 5", title="Stranger"
        )
        self.assertEqual(
            ["source_id_collision: bygrave already holds 'GDPR Article 6'"], result["errors"]
        )
        self.assertEqual("bygrave", result["source_id"])
        self.assertEqual("GDPR Article 6", result["held_title"])
        self.assertIsNone(result["held_raw_sha256"])
        self.assertIn("hint", result)
        after = json.loads(
            json.dumps(sources.read_registry(self.work_dir)["sources"]["bygrave"], sort_keys=True)
        )
        self.assertEqual(before, after, "a refused registration writes nothing")

    def test_an_occupied_id_without_hashes_and_the_same_citation_stays_a_no_op(self):
        self.register(source_id="bygrave", url="", citation="Bygrave, Data Privacy Law (OUP 2014) 121")
        again = self.register(
            source_id="bygrave",
            url="",
            citation="Bygrave, Data Privacy Law (OUP 2014) 121",
            title="Bygrave, revised",
        )
        self.assertFalse(again["created"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))

    def test_an_occupied_id_without_hashes_and_another_layer_is_a_layer_mismatch(self):
        """D-206 fix round 1: the dedup comparison uses the incoming layer on both sides, so a
        layer difference falls through to the dedicated check instead of reading as a collision."""
        import json

        self.register(source_id="bygrave", url="", citation="Bygrave, Data Privacy Law (OUP 2014) 121")
        before = json.loads(
            json.dumps(sources.read_registry(self.work_dir)["sources"]["bygrave"], sort_keys=True)
        )
        result = self.register(
            layer="case_law",
            source_id="bygrave",
            url="",
            citation="Bygrave, Data Privacy Law (OUP 2014) 121",
        )
        self.assertEqual(
            ["source_id_layer_mismatch: bygrave is registered under 'statutes'"], result["errors"]
        )
        self.assertEqual("statutes", result["held_layer"])
        after = json.loads(
            json.dumps(sources.read_registry(self.work_dir)["sources"]["bygrave"], sort_keys=True)
        )
        self.assertEqual(before, after, "a refused registration writes nothing")

    def test_registry_validates_against_the_sources_schema(self):
        self.register(raw_file=self.raw_file(), identifiers={"celex": "32016R0679"})
        registry = sources.read_registry(self.work_dir)
        from memoforge import schema

        self.assertEqual([], schema.validate(registry, "sources"))

    def test_background_source_without_raw_is_allowed(self):
        result = self.register(tier="background")
        self.assertIsNone(result["raw_path"])
        self.assertIsNone(result["raw_sha256"])

    def test_unknown_layer_or_tier_is_rejected(self):
        with self.assertRaises(ValueError):
            self.register(layer="secondary")
        with self.assertRaises(ValueError):
            self.register(tier="nice-to-have")

    def test_meta_from_the_tool_is_kept_verbatim(self):
        self.register(meta={"in_force": True, "effective_date": "2018-05-25", "status": "consolidated"})
        record = sources.read_registry(self.work_dir)["sources"]["gdpr-article-6"]
        self.assertEqual(True, record["meta"]["in_force"])
        self.assertEqual("2018-05-25", record["meta"]["effective_date"])

    def test_register_after_freeze_fails(self):
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze()
        result = self.register(url="https://eur-lex.europa.eu/late")
        self.assertEqual(["sources_frozen"], result["errors"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))


class ConcurrentRegisterTest(SourcesTestCase):
    def _run(self, shared: bool) -> list[dict]:
        go = self.root / "go"
        readies = [str(self.root / f"ready-{i}") for i in range(WORKERS)]
        results = [str(self.root / f"result-{i}") for i in range(WORKERS)]
        processes = [
            spawn(worker_register, (str(self.work_dir), i, shared, readies[i], str(go), results[i]))
            for i in range(WORKERS)
        ]
        for process in processes:
            process.start()
        for ready in readies:
            self.assertTrue(wait_for(ready), "a child never started")
        go.write_text("go", encoding="utf-8")
        for process in processes:
            process.join(timeout=120)
            self.assertEqual(0, process.exitcode, "a child crashed")
        return [json.loads(Path(path).read_text(encoding="utf-8")) for path in results]

    def test_six_processes_register_distinct_sources_without_loss(self):
        outcomes = self._run(shared=False)
        self.assertEqual([None] * WORKERS, [row["error"] for row in outcomes])
        registry = sources.read_registry(self.work_dir)
        self.assertEqual(WORKERS, len(registry["sources"]))
        self.assertEqual(WORKERS, sum(1 for row in outcomes if row["result"]["created"]))

    def test_six_processes_registering_the_same_source_produce_one_record(self):
        outcomes = self._run(shared=True)
        self.assertEqual([None] * WORKERS, [row["error"] for row in outcomes])
        registry = sources.read_registry(self.work_dir)
        self.assertEqual(1, len(registry["sources"]))
        ids = {row["result"]["source_id"] for row in outcomes}
        self.assertEqual(1, len(ids))
        self.assertEqual(1, sum(1 for row in outcomes if row["result"]["created"]))

    def test_concurrent_register_vs_freeze(self):
        self.register(url="https://eur-lex.europa.eu/seed")
        self.write_findings([{"source_id": "gdpr-article-6"}])
        issue_step(self.work_dir, "s-010")
        go = self.root / "go"
        ready_freeze = str(self.root / "ready-freeze")
        result_freeze = str(self.root / "result-freeze")
        readies = [str(self.root / f"ready-{i}") for i in range(WORKERS)]
        results = [str(self.root / f"result-{i}") for i in range(WORKERS)]
        processes = [spawn(worker_freeze, (str(self.work_dir), "s-010", ready_freeze, str(go), result_freeze))]
        processes += [
            spawn(worker_register, (str(self.work_dir), i, False, readies[i], str(go), results[i]))
            for i in range(WORKERS)
        ]
        for process in processes:
            process.start()
        for ready in [ready_freeze] + readies:
            self.assertTrue(wait_for(ready), "a child never started")
        go.write_text("go", encoding="utf-8")
        for process in processes:
            process.join(timeout=180)
            self.assertEqual(0, process.exitcode, "a child crashed")

        freeze_outcome = json.loads(Path(result_freeze).read_text(encoding="utf-8"))
        self.assertIsNone(freeze_outcome["error"])
        self.assertNotIn("errors", freeze_outcome["result"])
        snapshot = set(sources.snapshot_map(self.work_dir))
        self.assertIn("gdpr-article-6", snapshot)

        for path in results:
            outcome = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertIsNone(outcome["error"])
            result = outcome["result"]
            if result.get("errors"):
                # Registration after the freeze is refused (M6).
                self.assertEqual(["sources_frozen"], result["errors"])
            else:
                # Whatever got in before the freeze is inside the snapshot (§5.3).
                self.assertIn(result["source_id"], snapshot)

        # The state flag and the snapshot file agree in every interleaving.
        self.assertTrue(state_io.read_state(self.work_dir)["sources_frozen"])


# --- pack --freeze ---------------------------------------------------------


class PackTest(SourcesTestCase):
    def test_pack_requires_freeze(self):
        args = argparse.Namespace(workdir=str(self.work_dir), freeze=False, step="s-010", attempt=1, phase=None)
        self.assertEqual(["freeze_required"], sources.run_pack(args)["errors"])

    def test_freeze_publishes_the_snapshot_and_sets_the_state_flag(self):
        self.register(raw_file=self.raw_file())
        self.write_findings([{"source_id": "gdpr-article-6"}])
        result = self.freeze()
        self.assertTrue(result["sources_frozen"])
        self.assertFalse(result["replayed"])
        pack = sources.read_pack(self.work_dir)
        from memoforge import schema

        self.assertEqual([], schema.validate(pack, "source-pack"))
        state = state_io.read_state(self.work_dir)
        self.assertTrue(state["sources_frozen"])
        published = [row for row in state["published"] if row["canonical_path"] == sources.PACK_PATH]
        self.assertEqual(1, len(published))
        self.assertEqual(state_io.sha256_file(self.work_dir / sources.PACK_PATH), published[0]["sha256"])

    def _two_registrations_of_article_88(self) -> None:
        self.register(
            source_id="gdpr-article-88-processing-in-the-context-of-employment",
            title="GDPR Article 88 - Processing in the context of employment",
            citation="Regulation (EU) 2016/679 (GDPR), Art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="a88.md", text="Article 88 - Processing in the context of employment\n"),
        )
        self.register(
            source_id="gdpr-art88",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="a88b.md", text="Processing in the context of employment\n"),
        )

    def test_freeze_collapses_duplicates_and_records_merged_into(self):
        """D34-04: the pack the writer reads holds one entry per provision, not one per registration."""
        self._two_registrations_of_article_88()
        self.write_findings([{"source_id": "gdpr-art88"}])
        result = self.freeze()
        self.assertEqual(1, result["entries"])
        self.assertEqual(1, result["merged"])
        pack = sources.read_pack(self.work_dir)
        canonical = "gdpr-article-88-processing-in-the-context-of-employment"
        self.assertEqual({"gdpr-art88": canonical}, pack["merged_into"])
        self.assertEqual([canonical], [row["source_id"] for row in pack["entries"]])
        self.assertEqual([canonical], [row["source_id"] for row in pack["snapshot"]])
        from memoforge import schema

        self.assertEqual([], schema.validate(pack, "source-pack"))

    def test_a_finding_on_a_merged_id_is_projected_onto_the_canonical_entry(self):
        self._two_registrations_of_article_88()
        self.write_findings([{"source_id": "gdpr-art88", "issue_id": "i1", "role": "rule"}])
        self.freeze()
        entry = sources.read_pack(self.work_dir)["entries"][0]
        self.assertEqual({"i1": "rule"}, entry["pack"]["role_by_issue"])
        self.assertEqual("rule", entry["pack"]["use_in_memo"])

    def test_a_mislabelled_id_is_never_the_canonical_of_its_group(self):
        self.register(
            source_id="gdpr-article-3-territorial-scope",
            title="GDPR Article 88 (Processing in the context of employment)",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="m1.md", text="Processing in the context of employment\n"),
        )
        self.register(
            source_id="gdpr-art88",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="m2.md", text="Processing in the context of employment\n"),
        )
        self.write_findings([{"source_id": "gdpr-art88"}])
        self.freeze()
        pack = sources.read_pack(self.work_dir)
        self.assertEqual({"gdpr-article-3-territorial-scope": "gdpr-art88"}, pack["merged_into"])

    def test_freeze_refuses_a_group_whose_raw_texts_are_different_articles(self):
        self.register(
            source_id="ai-act-article-5",
            title="AI Act Article 5",
            citation="Regulation (EU) 2024/1689 (AI Act), art 5",
            url="https://eur-lex.europa.eu/eli/reg/2024/1689/oj#art5",
            identifiers={"celex": "32024R1689"},
            raw_file=self.raw_file(name="p1.md", text="Article 5 - Prohibited AI practices\n\n1. Text.\n"),
        )
        self.register(
            source_id="aiact-art5",
            title="AI Act Article 5",
            citation="Regulation (EU) 2024/1689 (AI Act), art 5",
            url="https://eur-lex.europa.eu/eli/reg/2024/1689/oj#art_5",
            identifiers={"celex": "32024R1689"},
            raw_file=self.raw_file(name="p2.md", text="Article 113 - Entry into force\n\n1. Text.\n"),
        )
        self.write_findings([{"source_id": "aiact-art5"}])
        result = self.freeze()
        self.assertEqual(1, len(result["errors"]))
        self.assertTrue(result["errors"][0].startswith("contradictory_duplicate_group:"))
        self.assertIsNone(sources.read_pack(self.work_dir))

    def test_sources_that_only_share_raw_text_under_other_instruments_are_not_merged(self):
        """The `probe dry-run` fixtures: identical template text, two different instruments."""
        self.register(
            source_id="fixture-one",
            title="Fixture statutes source 1",
            citation="Fixture statutes instrument 1, art 5",
            url="",
            raw_file=self.raw_file(name="f1.md"),
        )
        self.register(
            source_id="fixture-two",
            title="Fixture statutes source 2",
            citation="Fixture statutes instrument 2, art 5",
            url="",
            raw_file=self.raw_file(name="f2.md"),
        )
        self.write_findings([{"source_id": "fixture-one"}])
        result = self.freeze()
        self.assertEqual(0, result["merged"])
        self.assertEqual(2, result["entries"])

    def test_freeze_writes_input_and_result_into_the_step_workspace(self):
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze()
        workspace = self.work_dir / "steps" / "s-010" / "a1" / "cli"
        self.assertTrue((workspace / "source-pack.json").is_file())
        self.assertTrue((workspace / "inputs" / "sources.json").is_file())

    def test_repeated_freeze_of_the_same_step_is_a_no_op(self):
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        first = self.freeze()
        before = (self.work_dir / sources.PACK_PATH).read_text(encoding="utf-8")
        second = self.freeze()
        self.assertTrue(second["already_done"])
        self.assertEqual(first["frozen_at"], second["frozen_at"])
        self.assertEqual(before, (self.work_dir / sources.PACK_PATH).read_text(encoding="utf-8"))

    def test_freeze_replays_from_the_snapshot_when_the_state_write_was_lost(self):
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze()
        frozen_at = sources.read_pack(self.work_dir)["frozen_at"]

        # Simulate the crash window of §2.2: the file is published, the state write never landed.
        def rollback(state: dict) -> None:
            state["sources_frozen"] = False
            state["steps"] = []
            state["published"] = []

        state_io.write_state(self.work_dir, rollback)

        result = self.freeze(step="s-010", attempt=2)
        self.assertTrue(result["replayed"])
        self.assertTrue(result["sources_frozen"])
        self.assertEqual(frozen_at, result["frozen_at"], "the snapshot file is authoritative for freeze")
        self.assertTrue(state_io.read_state(self.work_dir)["sources_frozen"])

    def test_identity_mismatch_when_the_same_step_is_replayed_at_a_lower_attempt(self):
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze(step="s-010", attempt=2)
        result = self.freeze(step="s-010", attempt=1)
        self.assertEqual(["identity_mismatch"], result["errors"])

    def test_pack_never_derives_weight_or_confidence_from_tier(self):
        self.register(url="https://example.org/binding-background", title="Binding background")
        self.register(url="https://example.org/soft-critical", title="Soft critical", tier="critical")
        registry = sources.read_registry(self.work_dir)
        binding_id = "binding-background"
        soft_id = "soft-critical"
        self.assertIn(binding_id, registry["sources"])
        # A `background` tier source whose findings are binding stays binding.
        registry["sources"][binding_id]["tier"] = "background"
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)
        self.write_findings(
            [
                {"source_id": binding_id, "weight": "binding", "confidence": "high", "issue_id": "i1"},
                {"source_id": soft_id, "weight": "non_binding", "confidence": "low", "issue_id": "i2"},
            ]
        )
        self.freeze()
        entries = {row["source_id"]: row for row in sources.read_pack(self.work_dir)["entries"]}
        self.assertEqual("background", entries[binding_id]["tier"])
        self.assertEqual("binding", entries[binding_id]["pack"]["weight"])
        self.assertEqual("critical", entries[soft_id]["tier"])
        self.assertEqual("non_binding", entries[soft_id]["pack"]["weight"])
        self.assertEqual("low", entries[soft_id]["pack"]["confidence"])

    def test_role_by_issue_keeps_one_source_in_several_roles(self):
        self.register()
        document = {
            "layer": "statutes",
            "issues": [
                {
                    "issue_id": "i1",
                    "findings": [
                        {
                            "source_id": "gdpr-article-6",
                            "proposition": "Consent is a basis.",
                            "pinpoint": "Art. 6(1)(a)",
                            "role": "rule",
                            "weight": "binding",
                            "confidence": "high",
                            "tier": "critical",
                            "quote_short": "consent",
                        }
                    ],
                },
                {
                    "issue_id": "i2",
                    "findings": [
                        {
                            "source_id": "gdpr-article-6",
                            "proposition": "Background only here.",
                            "pinpoint": "Art. 6(1)(f)",
                            "role": "background",
                            "weight": "persuasive",
                            "confidence": "medium",
                            "tier": "supporting",
                            "quote_short": "interest",
                        }
                    ],
                },
            ],
            "_meta": {"task_id": TASK_ID, "step_id": "s-005", "attempt": 1, "slot": "statutes"},
        }
        state_io.write_json_atomic(self.work_dir / "research" / "statutes.json", document)
        self.freeze()
        entry = sources.read_pack(self.work_dir)["entries"][0]
        self.assertEqual({"i1": "rule", "i2": "background"}, entry["pack"]["role_by_issue"])
        self.assertEqual("binding", entry["pack"]["weight"])
        self.assertEqual("medium", entry["pack"]["confidence"])
        self.assertEqual("rule", entry["pack"]["use_in_memo"])

    def test_currency_do_not_use_forces_use_in_memo_do_not_use(self):
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {
                "checked_at": "2026-01-02",
                "sources": [{"source_id": "gdpr-article-6", "status": "do_not_use", "note": "repealed"}],
                "blocking": ["gdpr-article-6"],
                "warnings": [],
            },
        )
        self.freeze()
        entry = sources.read_pack(self.work_dir)["entries"][0]
        self.assertEqual("do_not_use", entry["currency_status"])
        self.assertEqual("do_not_use", entry["pack"]["use_in_memo"])

    def test_unresolved_us_citation_cannot_carry_a_rule(self):
        self.register(url="https://courtlistener.com/x", title="Smith v Acme", layer="case_law")
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["smith-v-acme"]["verification"]["us"] = "unresolved"
        registry["sources"]["smith-v-acme"]["verification"]["us_by"] = "agent"
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)
        self.write_findings([{"source_id": "smith-v-acme", "role": "rule"}], layer="case_law")
        self.freeze()
        entry = sources.read_pack(self.work_dir)["entries"][0]
        self.assertEqual("application", entry["pack"]["use_in_memo"], "unresolved is kept, but never a rule")
        self.assertIn("smith-v-acme", sources.snapshot_map(self.work_dir))

    def test_snapshot_records_null_for_a_source_without_raw(self):
        self.register(raw_file=self.raw_file())
        self.register(url="https://example.org/no-raw", title="No raw source", tier="background")
        self.freeze()
        snapshot = sources.snapshot_map(self.work_dir)
        self.assertIsNotNone(snapshot["gdpr-article-6"])
        self.assertIsNone(snapshot["no-raw-source"])


# --- digest ----------------------------------------------------------------


class DigestTest(SourcesTestCase):
    def _freeze_with(self, currency_status: str = "current", us: str = "n/a") -> None:
        self.register(raw_file=self.raw_file())
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["verification"]["us"] = us
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)
        self.write_findings([{"source_id": "gdpr-article-6"}])
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {
                "checked_at": "2026-01-02",
                "sources": [{"source_id": "gdpr-article-6", "status": currency_status, "note": ""}],
                "blocking": [],
                "warnings": [],
            },
        )
        self.freeze()

    def test_digest_without_exceptions_says_so(self):
        self._freeze_with()
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertFalse(result["has_exceptions"])
        self.assertIn("No exceptions", result["text"])
        self.assertIn("continue", result["text"])

    def test_manual_check_on_a_critical_source_is_an_exception(self):
        self._freeze_with(currency_status="manual_check")
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertTrue(result["has_exceptions"])
        self.assertEqual({"currency"}, {row["kind"] for row in result["exceptions"]})

    def test_unresolved_us_citation_is_an_exception(self):
        self._freeze_with(us="unresolved")
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertIn("unresolved_citation", {row["kind"] for row in result["exceptions"]})

    def test_drafting_warnings_become_exceptions(self):
        self._freeze_with()

        def mutate(state: dict) -> None:
            state["drafting_warnings"] = ["doctrine layer returned nothing"]

        state_io.write_state(self.work_dir, mutate)
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertIn("drafting_warning", {row["kind"] for row in result["exceptions"]})

    def test_exhausted_quota_server_is_an_exception(self):
        # D-166: a quota server at its daily limit fires (against the provider quota, not the
        # legacy per-run share); a free server at 85 — over its legacy share — does not.
        self._freeze_with()

        def mutate(state: dict) -> None:
            state["config"]["mcp_budget"] = {"ldh": 40, "legalviz": 40}
            state["progress"]["mcp_calls"] = {"ldh": 10, "legalviz": 85}

        state_io.write_state(self.work_dir, mutate)
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        kinds = {row["kind"] for row in result["exceptions"]}
        self.assertIn("mcp_budget_exhausted", kinds)
        details = " ".join(row["detail"] for row in result["exceptions"] if row["kind"] == "mcp_budget_exhausted")
        self.assertIn("ldh", details)
        self.assertIn("10/10", details)
        self.assertNotIn("legalviz", details)

    def test_a_free_server_below_the_quota_servers_never_fires(self):
        # D-166: LegalViz at 85 is over its legacy `mcp_budget` share of 40 — the old code
        # path fires here, the quota-only path stays quiet.
        self._freeze_with()

        def mutate(state: dict) -> None:
            state["config"]["mcp_budget"] = {"ldh": 40, "legalviz": 40}
            state["progress"]["mcp_calls"] = {"legalviz": 85}

        state_io.write_state(self.work_dir, mutate)
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertNotIn("mcp_budget_exhausted", {row["kind"] for row in result["exceptions"]})

    def test_full_digest_lists_every_source(self):
        self._freeze_with()
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), False)
        self.assertIn("gdpr-article-6", result["text"])
        self.assertIn("critical", result["text"])

    def test_conflicting_authority_is_an_exception(self):
        self.register(url="https://example.org/rule", title="Rule source")
        self.register(url="https://example.org/contra", title="Contrary source")
        self.write_findings(
            [
                {"source_id": "rule-source", "role": "rule", "issue_id": "i1"},
                {"source_id": "contrary-source", "role": "contrary", "issue_id": "i1"},
            ]
        )
        self.freeze()
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertIn("conflicting_authority", {row["kind"] for row in result["exceptions"]})


class DigestLanguageTest(SourcesTestCase):
    """D-176 (sources/preflight): the gate-11 digest speaks the UI language (frame only).

    The exception rows keep their machine tokens (`[kind]`, `source_id`, `tier`, currency
    status values, `do_not_use`) and the `drafting_warning` rows keep the memo-language text
    (D-173b); only the heading, the exception/no-exception lines and the closing reply line
    (tokens `continue`/`cancel` in backticks unchanged) are localized.
    """

    def setUp(self) -> None:
        super().setUp()
        holder = tempfile.TemporaryDirectory(prefix="mf-sources-ui-")
        self.addCleanup(holder.cleanup)
        self.packs = Path(holder.name)
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", _i18n.RU_UI)

    def _frozen(self) -> dict:
        self.register(raw_file=self.raw_file())
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["verification"]["us"] = "unresolved"
        with sources.sources_lock(self.work_dir):
            sources.write_registry(self.work_dir, registry)
        self.write_findings([{"source_id": "gdpr-article-6"}])
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {
                "checked_at": "2026-01-02",
                "sources": [{"source_id": "gdpr-article-6", "status": "current", "note": ""}],
                "blocking": [],
                "warnings": [],
            },
        )
        self.freeze()
        return state_io.read_state(self.work_dir)

    def test_english_digest_is_todays_bytes(self):
        state = self._frozen()
        result = sources.render_digest(self.work_dir, state, True)
        self.assertIn("Source review — 1 sources registered (frozen).", result["text"])
        self.assertIn("Exceptions requiring your attention:", result["text"])
        self.assertIn("[unresolved_citation] gdpr-article-6 — US citation unresolved", result["text"])
        self.assertIn("Reply `continue` to draft on these sources, or `cancel` to stop.", result["text"])

    def test_the_russian_digest_localizes_the_frame_but_not_the_rows(self):
        state = self._frozen()
        result = sources.render_digest(self.work_dir, state, True, ui="ru")
        self.assertIn(
            "Проверка источников — 1 источников зарегистрировано (заморожено).", result["text"]
        )
        self.assertNotIn("Source review —", result["text"])
        self.assertIn("Исключения, требующие вашего внимания:", result["text"])
        self.assertNotIn("Exceptions requiring your attention:", result["text"])
        self.assertIn("[unresolved_citation] gdpr-article-6 — US citation unresolved", result["text"])
        self.assertIn(
            "Ответьте `continue`, чтобы писать по этим источникам, или `cancel`, чтобы остановиться.",
            result["text"],
        )

    def test_the_russian_no_exception_digest_and_a_memo_language_warning(self):
        self.register(raw_file=self.raw_file())
        self.write_findings([{"source_id": "gdpr-article-6"}])
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {
                "checked_at": "2026-01-02",
                "sources": [{"source_id": "gdpr-article-6", "status": "current", "note": ""}],
                "blocking": [],
                "warnings": [],
            },
        )
        self.freeze()

        def mutate(state: dict) -> None:
            state["drafting_warnings"] = ["doctrine layer returned nothing"]

        state_io.write_state(self.work_dir, mutate)
        state = state_io.read_state(self.work_dir)
        result = sources.render_digest(self.work_dir, state, True, ui="ru")
        self.assertIn("Исключения, требующие вашего внимания:", result["text"])
        self.assertIn("- [drafting_warning] doctrine layer returned nothing", result["text"])


class PublishedInputTest(SourcesTestCase):
    """D-41: the freeze snapshots only inputs that still match `published[]` (§2.2)."""

    def test_a_findings_file_modified_after_publication_stops_the_freeze(self):
        self.register(raw_file=self.raw_file())
        self.write_findings([{"source_id": "gdpr-article-6"}])
        publish(self.work_dir, "research/statutes.json")
        document = state_io.read_json(self.work_dir / "research" / "statutes.json")
        document["issues"][0]["findings"][0]["proposition"] = "Edited after publication."
        state_io.write_json_atomic(self.work_dir / "research" / "statutes.json", document)

        result = self.freeze()

        self.assertEqual(["output_modified_after_publish"], result["errors"])
        self.assertEqual("research/statutes.json", result["path"])
        self.assertIsNone(sources.read_pack(self.work_dir))
        self.assertFalse(state_io.read_state(self.work_dir)["sources_frozen"])

    def test_a_currency_file_modified_after_publication_stops_the_freeze(self):
        self.register(raw_file=self.raw_file())
        self.write_findings([{"source_id": "gdpr-article-6"}])
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {"checked_at": "2026-01-01", "sources": [], "blocking": [], "warnings": []},
        )
        publish(self.work_dir, sources.CURRENCY_PATH)
        state_io.write_json_atomic(
            self.work_dir / sources.CURRENCY_PATH,
            {
                "checked_at": "2026-01-01",
                "sources": [{"source_id": "gdpr-article-6", "status": "do_not_use"}],
                "blocking": [],
                "warnings": [],
            },
        )

        result = self.freeze()

        self.assertEqual(["output_modified_after_publish"], result["errors"])
        self.assertEqual(sources.CURRENCY_PATH, result["path"])

    def test_untouched_published_inputs_freeze_normally(self):
        self.register(raw_file=self.raw_file())
        self.write_findings([{"source_id": "gdpr-article-6"}])
        publish(self.work_dir, "research/statutes.json")
        result = self.freeze()
        self.assertTrue(result["sources_frozen"])


class StepUtilitiesTest(SourcesTestCase):
    """D-28: `liveness` and `verify` accept `--step/--attempt` and close their step like any script."""

    def liveness_args(self, **overrides) -> argparse.Namespace:
        payload = {"workdir": str(self.work_dir), "source": "ghost", "timeout": 5.0}
        payload.update(overrides)
        return argparse.Namespace(**payload)

    def verify_args(self, **overrides) -> argparse.Namespace:
        payload = {"workdir": str(self.work_dir), "set": None}
        payload.update(overrides)
        return argparse.Namespace(**payload)

    def test_liveness_closes_its_step_and_replays_as_a_no_op(self):
        self.register()
        issue_step(self.work_dir, "s-020")
        args = self.liveness_args(step="s-020", attempt=1)
        first = sources.run_liveness(args)
        state = state_io.read_state(self.work_dir)
        row = next(row for row in state["steps"] if row["step_id"] == "s-020")
        self.assertEqual("ok", row["status"])
        again = sources.run_liveness(args)
        self.assertTrue(again["already_done"])
        self.assertEqual(first["count"], again["count"])

    def test_liveness_without_a_step_stays_a_utility(self):
        self.register()
        result = sources.run_liveness(self.liveness_args())
        self.assertEqual(1, result["count"])
        self.assertEqual([], state_io.read_state(self.work_dir)["steps"])

    def test_verify_closes_its_step_and_replays_as_a_no_op(self):
        self.register()
        issue_step(self.work_dir, "s-021")
        args = self.verify_args(step="s-021", attempt=1)
        first = sources.run_verify(args)
        state = state_io.read_state(self.work_dir)
        row = next(row for row in state["steps"] if row["step_id"] == "s-021")
        self.assertEqual("ok", row["status"])
        again = sources.run_verify(args)
        self.assertTrue(again["already_done"])
        self.assertEqual(first["checked"], again["checked"])

    def test_a_step_that_was_never_issued_is_identity_mismatch(self):
        self.register()
        result = sources.run_verify(self.verify_args(step="s-never-issued", attempt=1))
        self.assertEqual(["identity_mismatch"], result["errors"])
        self.assertEqual("unknown_step", result["reason"])


# --- liveness --------------------------------------------------------------


class LivenessTest(SourcesTestCase):
    def _liveness(self, source: str | None = None) -> dict:
        args = argparse.Namespace(workdir=str(self.work_dir), source=source, timeout=5.0)
        return sources.run_liveness(args)

    def test_matching_body_confirms_the_provenance(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/ok", raw_file=self.raw_file(text=LIVE_TEXT), tool="curl")
            result = self._liveness()
        row = result["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertEqual(200, row["code"])
        self.assertEqual("confirmed", row["provenance"])
        record = sources.read_registry(self.work_dir)["sources"]["gdpr-article-6"]
        self.assertEqual("confirmed", record["provenance"])
        self.assertEqual("ok", record["liveness"]["status"])

    def test_changed_body_is_reported_and_never_confirms(self):
        with LocalServer(RAW_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/changed", raw_file=self.raw_file(), tool="curl")
            result = self._liveness()
        row = result["checked"][0]
        self.assertEqual("changed", row["status"])
        self.assertEqual("agent_saved", row["provenance"])

    def test_processed_text_is_never_compared_with_the_page(self):
        """D34-08: an MCP tool saves extracted markdown and WebFetch the model's rendering."""
        for tool in ("legalviz_get_law_part", "WebFetch", "WebFetch edpb.europa.eu"):
            with self.subTest(tool=tool):
                with LocalServer(RAW_TEXT.encode("utf-8")) as base:
                    self.register(
                        url=f"{base}/changed",
                        raw_file=self.raw_file(),
                        tool=tool,
                        source_id=f"src-{sources.slugify(tool)}",
                    )
                    result = self._liveness(source=f"src-{sources.slugify(tool)}")
                row = result["checked"][0]
                self.assertEqual("ok", row["status"])
                self.assertEqual({"ok": 1}, result["by_status"])
                self.assertEqual("agent_saved", row["provenance"])

    def test_a_fetch_of_another_host_is_not_comparable(self):
        """`curl twobirds.com` next to a eur-lex url did not read that url."""
        with LocalServer(RAW_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/changed", raw_file=self.raw_file(), tool="curl example.org")
            result = self._liveness()
        self.assertEqual("ok", result["checked"][0]["status"])

    def test_a_fetch_naming_the_registrable_domain_is_comparable(self):
        with LocalServer(RAW_TEXT.encode("utf-8")) as base:
            host = base.split("//", 1)[1].split(":")[0]
            self.register(url=f"{base}/changed", raw_file=self.raw_file(), tool=f"curl {host}")
            result = self._liveness()
        self.assertEqual("changed", result["checked"][0]["status"])

    def test_queue_and_throttle_codes_are_unchecked_without_hashing(self):
        """D34-08: 202/403/429/503 mean «not served», not «dead» — and no body is hashed."""
        for path, code in (("/accepted", 202), ("/forbidden", 403), ("/throttled", 429), ("/down", 503)):
            with self.subTest(code=code):
                with LocalServer(RAW_TEXT.encode("utf-8")) as base:
                    self.register(
                        url=f"{base}{path}",
                        raw_file=self.raw_file(),
                        tool="curl",
                        source_id=f"src-{code}",
                    )
                    result = self._liveness(source=f"src-{code}")
                row = result["checked"][0]
                self.assertEqual("unchecked", row["status"])
                self.assertEqual(code, row["code"])
                self.assertEqual(f"http_{code}", row["error"])
                self.assertEqual("agent_saved", row["provenance"])

    def test_missing_document_is_dead(self):
        with LocalServer(b"") as base:
            self.register(url=f"{base}/gone", raw_file=self.raw_file())
            result = self._liveness()
        self.assertEqual("dead", result["checked"][0]["status"])
        self.assertEqual(404, result["checked"][0]["code"])

    def test_redirect_is_recorded_as_such(self):
        with LocalServer(RAW_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/redirect", raw_file=self.raw_file())
            result = self._liveness()
        self.assertEqual("redirect", result["checked"][0]["status"])

    def test_unreachable_host_is_best_effort(self):
        self.register(url="http://127.0.0.1:9/never", raw_file=self.raw_file())
        result = self._liveness()
        self.assertEqual("dead", result["checked"][0]["status"])
        self.assertIsNone(result["checked"][0]["code"])

    def test_source_without_url_stays_unchecked(self):
        self.register(url="", citation="Bygrave, Data Privacy Law (OUP 2014) 121")
        result = self._liveness()
        self.assertEqual("unchecked", result["checked"][0]["status"])

    def test_single_source_can_be_probed(self):
        with LocalServer(RAW_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/ok", raw_file=self.raw_file())
            self.register(url=f"{base}/changed", title="Other source")
            result = self._liveness(source="gdpr-article-6")
        self.assertEqual(1, result["count"])


class LivenessContractTest(SourcesTestCase):
    """D-146: politeness, the headers a host requires, named interstitials, TLS (analysis/38 §7.4)."""

    def _liveness(self, source: str | None = None) -> dict:
        args = argparse.Namespace(workdir=str(self.work_dir), source=source, timeout=5.0)
        return sources.run_liveness(args)

    def _recorded_waits(self) -> list:
        """The pauses this test asked for; `SourcesTestCase.setUp` records instead of sleeping."""
        return self.waits

    def _probe(self, path: str, source_id: str) -> dict:
        """One probe of `path` by a body-comparable tool, so the GET branch really runs."""
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(
                url=f"{base}{path}",
                raw_file=self.raw_file(name=f"{source_id}.md"),
                tool="curl",
                source_id=source_id,
            )
            return self._liveness(source=source_id)["checked"][0]

    # --- 1. per-host politeness --------------------------------------------

    def test_the_first_probe_of_a_host_never_waits(self):
        waits = self._recorded_waits()
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/ok")
            self._liveness()
        self.assertEqual([], waits)

    def test_consecutive_probes_of_one_host_are_spaced_by_the_delay(self):
        """analysis/38 §7.4 item 1: the real run fired ~2.5 requests/s at one host."""
        waits = self._recorded_waits()
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/ok", title="First source")
            self.register(url=f"{base}/stub", title="Second source")
            self.register(url=f"{base}/shell", title="Third source")
            result = self._liveness()
        self.assertEqual(3, result["count"])
        self.assertEqual(2, len(waits), "one pause before every repeat probe of the host")
        for pause in waits:
            self.assertAlmostEqual(limits.LIVENESS_HOST_DELAY_SECONDS, pause, delta=0.5)

    def test_a_host_that_asks_for_more_gets_more(self):
        """`eur-lex.europa.eu/robots.txt` says `Crawl-delay: 10` (analysis/38 §1.3)."""
        self.assertEqual(10.0, sources.host_delay("eur-lex.europa.eu"))
        self.assertEqual(10.0, sources.host_delay("www.eur-lex.europa.eu"))
        self.assertEqual(limits.LIVENESS_HOST_DELAY_SECONDS, sources.host_delay("publications.europa.eu"))

    def test_run_liveness_waits_by_the_override_table(self):
        waits = self._recorded_waits()
        original = limits.LIVENESS_HOST_DELAYS
        limits.LIVENESS_HOST_DELAYS = {"127.0.0.1": 7.0}
        self.addCleanup(setattr, limits, "LIVENESS_HOST_DELAYS", original)
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/ok", title="First source")
            self.register(url=f"{base}/stub", title="Second source")
            self._liveness()
        self.assertEqual(1, len(waits))
        self.assertAlmostEqual(7.0, waits[0], delta=0.5)

    # --- 2. the headers a probe sends --------------------------------------

    def test_the_default_accept_and_the_contact_agent_reach_the_host(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(url=f"{base}/ok")
            self._liveness()
        headers = _Handler.seen[-1]["headers"]
        self.assertEqual("text/html,application/xhtml+xml;q=0.9,*/*;q=0.8", headers["accept"])
        self.assertEqual("memoforge/2 liveness (+https://github.com/gregmos/memoforge)", headers["user-agent"])

    def test_the_cellar_call_contract_is_exact(self):
        """analysis/38 §1.5: without these two headers Cellar answers with 60 MB of RDF."""
        headers = sources.probe_headers("https://publications.europa.eu/resource/celex/32016R0679")
        self.assertEqual("application/xhtml+xml", headers["Accept"])
        self.assertEqual("eng", headers["Accept-Language"])
        self.assertEqual(sources.LIVENESS_USER_AGENT, headers["User-Agent"])
        self.assertEqual("gzip", headers["Accept-Encoding"])

    def test_only_the_cellar_resource_path_gets_the_cellar_contract(self):
        """`Accept: application/xhtml+xml` is a 404 on most of the web — it is not a default."""
        for url in (
            "https://publications.europa.eu/en/publication-detail/-/publication/1",
            "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            "https://notpublications.europa.eu/resource/celex/32016R0679",
        ):
            with self.subTest(url=url):
                headers = sources.probe_headers(url)
                self.assertEqual(sources.DEFAULT_ACCEPT, headers["Accept"])
                self.assertNotIn("Accept-Language", headers)
                self.assertEqual("gzip", headers["Accept-Encoding"])

    # --- 3. challenges and interstitials ------------------------------------

    def test_an_aws_waf_challenge_is_named_not_just_coded(self):
        row = self._probe("/waf", "waf-source")
        self.assertEqual("unchecked", row["status"])
        self.assertEqual(202, row["code"])
        self.assertEqual("aws_waf_challenge", row["error"])

    def test_a_cloudflare_challenge_is_named_on_both_of_its_codes(self):
        for path, code, source_id in (("/cloudflare", 403, "cf-403"), ("/cloudflare-down", 503, "cf-503")):
            with self.subTest(code=code):
                row = self._probe(path, source_id)
                self.assertEqual("unchecked", row["status"])
                self.assertEqual(code, row["code"])
                self.assertEqual("cloudflare_challenge", row["error"])

    def test_a_202_without_the_waf_header_stays_a_bare_code(self):
        self.assertEqual("http_202", self._probe("/accepted", "plain-202")["error"])

    def test_a_js_shell_under_200_is_unchecked_not_ok(self):
        """analysis/38 §2: 130 226 identical bytes for every case, six characters of text."""
        row = self._probe("/shell", "curia-shell")
        self.assertEqual("unchecked", row["status"])
        self.assertEqual(200, row["code"])
        self.assertEqual("interstitial_suspected", row["error"])
        self.assertEqual("agent_saved", row["provenance"], "an interstitial confirms nothing")

    def test_a_request_access_stub_under_200_is_unchecked_not_ok(self):
        """analysis/38 §5.1: eCFR answers a non-browser agent with a 10.6 KB stub, code 200."""
        row = self._probe("/stub", "ecfr-stub")
        self.assertEqual("unchecked", row["status"])
        self.assertEqual(200, row["code"])
        self.assertEqual("interstitial_suspected", row["error"])

    def test_the_interstitial_rules_pass_a_real_page(self):
        document = b"<html><body><p>" + b"Processing shall be lawful with consent. " * 500 + b"</p></body></html>"
        self.assertFalse(sources.is_interstitial(document))
        self.assertTrue(sources.is_interstitial(CURIA_SHELL))
        self.assertTrue(sources.is_interstitial(ECFR_STUB))
        self.assertGreater(sources.visible_text_ratio(document), 0.8)  # spaces do not count as text
        self.assertLess(sources.visible_text_ratio(CURIA_SHELL), 0.01)

    def test_a_binary_body_is_never_judged_by_size_or_ratio(self):
        """D-149: a PDF has no markup to strip, and 8 KB of it is a document like any other."""
        self.assertFalse(sources.is_interstitial(b"%PDF-1.5\n" + bytes(range(256)) * 200))
        self.assertFalse(sources.is_interstitial(b"%PDF-1.5\n" + bytes(range(256)), "application/pdf"))

    # --- 4. TLS -------------------------------------------------------------

    def test_a_certificate_failure_is_named_not_classed(self):
        expired = ssl.SSLCertVerificationError(1, "certificate verify failed: certificate has expired")
        self.assertEqual("tls_certificate", sources.probe_error(expired))
        self.assertEqual("tls_certificate", sources.probe_error(urllib.error.URLError(expired)))
        self.assertEqual("TimeoutError", sources.probe_error(TimeoutError()))

    def test_a_stale_root_store_is_diagnosable_from_the_registry(self):
        """analysis/38 §6.2: `URLError` read as «the site died»; the local store had expired."""

        def refuse(url, method, timeout, headers=None, **kwargs):
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate has expired"))

        original = sources._open
        sources._open = refuse
        self.addCleanup(setattr, sources, "_open", original)
        # D-192: the url is a public page — an MCP host never reaches `url`, so it is never probed.
        self.register(url="https://www.legislation.gov.uk/ukpga/2018/12", raw_file=self.raw_file(), tool="curl")
        row = self._liveness()["checked"][0]
        self.assertEqual("dead", row["status"])
        self.assertEqual("tls_certificate", row["error"])

    # --- 5. the url parser and the redirect chain (D-151) -------------------

    def test_a_url_with_credentials_is_never_probed(self):
        """D-151: the loose split read `127.0.0.1` out of `http://evil.example@127.0.0.1/`.

        A2 strips userinfo at registration, so the only way a record can still carry it is to have
        been written before that rule — which is what the registry is patched to here, and which is
        exactly the state this guard exists for.
        """
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            authority = base.split("//", 1)[1]
            self.register(url=f"http://{authority}/ok", raw_file=self.raw_file(), tool="curl")
            registry = sources.read_registry(self.work_dir)
            registry["sources"]["gdpr-article-6"]["url"] = f"http://evil.example@{authority}/ok"
            sources.write_registry(self.work_dir, registry)
            _Handler.seen = []
            row = self._liveness()["checked"][0]
            self.assertEqual([], _Handler.seen, "nothing left the process")
        self.assertEqual("unchecked", row["status"])
        self.assertEqual("userinfo_not_allowed", row["error"])
        record = sources.read_registry(self.work_dir)["sources"]["gdpr-article-6"]
        self.assertEqual("agent_saved", record["provenance"], "a refused url never confirms anything")

    def test_a_probe_records_every_hop_it_followed(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            probe = sources.probe_url(f"{base}/redirect", want_body=True, timeout=5.0)
        self.assertEqual("redirect", probe["status"])
        self.assertEqual(["127.0.0.1"], probe["redirects"])

    def test_a_hop_that_is_not_http_is_refused(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            probe = sources.probe_url(f"{base}/badhop", want_body=True, timeout=5.0)
        self.assertEqual("unchecked", probe["status"])
        self.assertEqual("redirect_not_allowed: unsupported_scheme", probe["error"])

    def test_a_probe_stops_a_redirect_loop(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            probe = sources.probe_url(f"{base}/loop", want_body=True, timeout=5.0)
            requests = len(_Handler.seen)
        self.assertEqual("unchecked", probe["status"])
        self.assertEqual(f"too_many_redirects: {sources.MAX_REDIRECT_HOPS + 1}", probe["error"])
        self.assertEqual(sources.MAX_REDIRECT_HOPS + 1, requests, "the HEAD chain stops at the cap")

    def test_an_unsupported_scheme_is_still_unchecked(self):
        probe = sources.probe_url("ftp://example.org/x", want_body=False, timeout=5.0)
        self.assertEqual("unchecked", probe["status"])
        self.assertEqual("unsupported_scheme", probe["error"])

    def test_a_short_json_answer_is_a_document_and_is_hashed(self):
        """D-149 end to end: the RIS OGD answer is 8 KB of JSON, and D-146 called it an interstitial."""
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.register(
                url=f"{base}/ris.json",
                raw_file=self.raw_file(name="ris.md", text=RIS_JSON.decode("ascii")),
                tool="curl",
                source_id="ris-json",
            )
            row = self._liveness(source="ris-json")["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertIsNone(row["error"])
        self.assertEqual("confirmed", row["provenance"], "a JSON answer is hashed like any document")


# --- the interstitial rules (P1, analysis/39 §9.1) --------------------------


class InterstitialRuleTest(unittest.TestCase):
    """D-149: what a false-positive 200 is, and what it is not (analysis/39 §9.1, §9.2)."""

    def test_the_preferred_pinpoint_addresses_of_the_routing_table_are_documents(self):
        """All three were `interstitial_suspected` under the unconditional 15 KB floor of D-146."""
        for name, body, content_type in (
            ("§ 26 BDSG", BDSG_PAGE, "text/html"),
            ("legislation.gov.uk snippet", LEGISLATION_SNIPPET, "application/xhtml+xml"),
            ("RIS OGD JSON", RIS_JSON, "application/json; charset=utf-8"),
        ):
            with self.subTest(source=name):
                self.assertLess(len(body), 15 * 1024, "the shape D-146 rejected by size")
                self.assertFalse(sources.is_interstitial(body, content_type))

    def test_a_json_or_xml_answer_is_never_judged_by_size_or_ratio(self):
        for content_type in ("application/json", "application/xml", "text/plain", "application/pdf"):
            with self.subTest(content_type=content_type):
                self.assertFalse(sources.is_interstitial(b'{"Hits":{"#text":"436"}}', content_type))

    def test_the_declared_type_beats_the_sniff(self):
        """A JSON field may quote markup; the `Content-Type` is what says the body is a page."""
        body = b'{"html":"<html><body>see the act</body></html>"}'
        self.assertFalse(sources.is_interstitial(body, "application/json"))
        self.assertTrue(sources.is_interstitial(body), "without a type the first kilobyte is sniffed")

    def test_a_markup_body_under_two_kilobytes_is_a_shell(self):
        self.assertLess(len(COURDECASSATION_SHELL), limits.LIVENESS_MIN_BODY_BYTES)
        self.assertTrue(sources.is_interstitial(COURDECASSATION_SHELL, "text/html"))

    def test_a_js_redirect_over_the_floor_is_still_a_shell(self):
        """The rule is not the size rule: 2 236 bytes, ratio 0.065, 169 characters of text."""
        self.assertGreater(len(REDIRECT_SHELL), limits.LIVENESS_MIN_BODY_BYTES)
        self.assertGreater(sources.visible_text_ratio(REDIRECT_SHELL), limits.LIVENESS_MIN_TEXT_RATIO)
        self.assertTrue(sources.is_redirect_shell(REDIRECT_SHELL))
        self.assertTrue(sources.is_interstitial(REDIRECT_SHELL, "text/html"))
        self.assertTrue(sources.is_redirect_shell(COURDECASSATION_SHELL))

    def test_a_document_that_also_redirects_is_not_a_shell(self):
        body = BDSG_PAGE.replace(b"<body>", b'<body><meta http-equiv="refresh" content="600">')
        self.assertGreater(len(body), limits.LIVENESS_MAX_SHELL_BYTES)
        self.assertFalse(sources.is_redirect_shell(body))
        self.assertFalse(sources.is_interstitial(body, "text/html"))

    def test_the_access_stub_is_caught_by_what_it_says(self):
        """Measured today: 10 596 bytes, ratio 0.0939 — neither the size nor the ratio rule sees it."""
        self.assertGreater(len(ECFR_STUB), limits.LIVENESS_MIN_BODY_BYTES)
        self.assertGreater(sources.visible_text_ratio(ECFR_STUB), limits.LIVENESS_MIN_TEXT_RATIO)
        self.assertFalse(sources.is_redirect_shell(ECFR_STUB))
        self.assertTrue(sources.is_access_stub(ECFR_STUB))
        self.assertTrue(sources.is_interstitial(ECFR_STUB, "text/html"))

    def test_the_curia_shell_is_caught_by_its_text_ratio(self):
        self.assertFalse(sources.is_access_stub(CURIA_SHELL))
        self.assertTrue(sources.is_interstitial(CURIA_SHELL, "text/html"))

    def test_a_statute_page_is_not_a_bot_wall(self):
        """The phrases are what a wall says; a document that mentions javascript is still a document."""
        body = BDSG_PAGE + b"<noscript>Bitte aktivieren Sie JavaScript.</noscript>"
        self.assertFalse(sources.is_access_stub(body))
        self.assertFalse(sources.is_interstitial(body, "text/html"))


# --- fetch (D-149) ----------------------------------------------------------


class FetchTest(SourcesTestCase):
    """D-149: `mf sources fetch` — the allow-listed GET that can send headers."""

    def setUp(self) -> None:
        super().setUp()
        sources._LAST_FETCH.clear()
        self.addCleanup(sources._LAST_FETCH.clear)

    def allow(self, *hosts: str) -> None:
        """Point the command at a test allowlist; the real file is exercised by its own test."""
        original = sources.allowlist_hosts
        sources.allowlist_hosts = lambda root=None: frozenset(hosts)
        self.addCleanup(setattr, sources, "allowlist_hosts", original)

    def fetch(self, url: str, **overrides) -> dict:
        payload = {
            "workdir": str(self.work_dir),
            "url": url,
            "method": "GET",
            "json_body": None,
            "accept": None,
            "lang": None,
            "out": None,
            "layer": None,
            "timeout": 5.0,
        }
        payload.update(overrides)
        return sources.run_fetch(argparse.Namespace(**payload))

    # --- the happy path -----------------------------------------------------

    def test_a_fetch_saves_the_body_and_reports_its_digest(self):
        body = LIVE_TEXT.encode("utf-8")
        with LocalServer(body) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/ok")
        self.assertEqual("ok", result["status"])
        self.assertEqual(200, result["code"])
        self.assertEqual(len(body), result["bytes"])
        self.assertEqual(state_io.sha256_bytes(body), result["sha256"])
        self.assertFalse(result["interstitial"])
        self.assertFalse(result["truncated"])
        self.assertTrue(result["path"].startswith("research/raw/fetch/"), result["path"])
        self.assertEqual(body, (self.work_dir / result["path"]).read_bytes())
        self.assertEqual("mf-fetch 127.0.0.1", result["retrieval_tool"])

    def test_the_same_url_always_lands_on_the_same_file(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            first = self.fetch(f"{base}/ok")
            second = self.fetch(f"{base}/ok")
        self.assertEqual(first["path"], second["path"], "a repeat fetch overwrites its own file")

    def test_out_puts_the_body_where_the_agent_asked(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/ok", out="statutes/bdsg-26.html")
            prefixed = self.fetch(f"{base}/ok", out="research/raw/statutes/bdsg-26.html")
        self.assertEqual("research/raw/statutes/bdsg-26.html", result["path"])
        self.assertEqual(result["path"], prefixed["path"], "the `research/raw/` prefix is optional")

    def test_out_never_leaves_the_raw_directory(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/ok", out="../../escape.md")
        self.assertEqual(["invalid_out_path: ../../escape.md"], result["errors"])
        self.assertEqual([], _Handler.seen, "the path is checked before the host is called")

    def test_the_layer_chooses_the_directory(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/ok", layer="case_law")
        self.assertTrue(result["path"].startswith("research/raw/case_law/"), result["path"])

    def test_a_json_answer_keeps_its_type_and_its_extension(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/ris.json")
        self.assertEqual("ok", result["status"])
        self.assertTrue(result["content_type"].startswith("application/json"), result["content_type"])
        self.assertTrue(result["path"].endswith(".json"), result["path"])
        self.assertFalse(result["interstitial"], "8 KB of JSON is a document (D-149)")

    # --- the allowlist ------------------------------------------------------

    def test_a_host_outside_the_allowlist_is_refused(self):
        result = self.fetch("https://example.org/whatever")
        self.assertEqual(["host_not_allowed: example.org"], result["errors"])
        written = list((self.work_dir / sources.RAW_DIR).rglob("*"))
        self.assertEqual([], written, "nothing is written for a refusal")

    def test_the_command_enforces_the_file_the_permission_gate_reads(self):
        """§8.1: exact host or a dot-anchored suffix — `evil-europa.eu` is not `europa.eu`."""
        self.assertIn("europa.eu", sources.allowlist_hosts())
        self.assertTrue(sources.host_on_allowlist("publications.europa.eu"))
        self.assertTrue(sources.host_on_allowlist("legislation.gov.uk"))
        self.assertFalse(sources.host_on_allowlist("evil-europa.eu"))
        self.assertFalse(sources.host_on_allowlist(""))

    def test_a_url_that_is_not_http_is_refused(self):
        # R1: the refusal names the scheme, which is the whole reason for it; the address itself has
        # no readable host, so `redacted_url` answers with the marker rather than echoing the input.
        self.assertEqual(
            ["unsupported_scheme: [address removed]"],
            self.fetch("file:///etc/passwd")["errors"],
        )

    # --- headers ------------------------------------------------------------

    def test_the_cellar_contract_applies_without_being_asked(self):
        """analysis/38 §1.5: this is the call `WebFetch` cannot make (analysis/39 §9.4)."""
        headers = sources.fetch_headers("https://publications.europa.eu/resource/celex/32016R0679")
        self.assertEqual("application/xhtml+xml", headers["Accept"])
        self.assertEqual("eng", headers["Accept-Language"])
        self.assertEqual(sources.LIVENESS_USER_AGENT, headers["User-Agent"])

    def test_accept_and_lang_override_the_defaults(self):
        headers = sources.fetch_headers(
            "https://publications.europa.eu/resource/celex/32016R0679", accept="application/xml", lang="deu"
        )
        self.assertEqual("application/xml", headers["Accept"])
        self.assertEqual("deu", headers["Accept-Language"])

    def test_the_headers_reach_the_host(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            self.fetch(f"{base}/ok", accept="application/xml", lang="spa")
            headers = _Handler.seen[-1]["headers"]
        self.assertEqual("application/xml", headers["accept"], "BOE answers XML only when asked")
        self.assertEqual("spa", headers["accept-language"])
        self.assertEqual(sources.LIVENESS_USER_AGENT, headers["user-agent"])

    # --- gzip (D-162) ---------------------------------------------------------

    def test_fetch_asks_for_gzip_and_decompresses_a_gzip_answer(self):
        # D-162: the eCFR API answers 406 to a request that does not allow compression.
        import gzip

        body = gzip.compress(b"<DIV8 N=\"312.3\">verifiable parental consent</DIV8>")
        seen = {}

        class Response:
            status = 200
            headers = {"Content-Type": "text/xml", "Content-Encoding": "gzip"}

            def read(self, n=-1):
                return body

            def geturl(self):
                return "https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-16.xml"

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(url, method, timeout, headers=None, **kwargs):
            seen["headers"] = dict(headers or {})
            return Response()

        with mock.patch("memoforge.sources._open", fake_open):
            answer = sources.fetch_body(
                "https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-16.xml"
            )
        self.assertEqual("gzip", seen["headers"].get("Accept-Encoding"))
        self.assertEqual("ok", answer["status"])
        self.assertIn(b"verifiable parental consent", answer["payload"])

    def test_a_plain_answer_is_left_alone(self):
        # no Content-Encoding → bytes pass through unchanged (every existing fixture stays valid)
        body = b"<html>x</html>"
        seen = {}

        class Response:
            status = 200
            headers = {"Content-Type": "text/html"}

            def read(self, n=-1):
                return body

            def geturl(self):
                return "https://www.ecfr.gov/current/title-16/part-312/section-312.3"

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(url, method, timeout, headers=None, **kwargs):
            seen["headers"] = dict(headers or {})
            return Response()

        with mock.patch("memoforge.sources._open", fake_open):
            answer = sources.fetch_body("https://www.ecfr.gov/current/title-16/part-312/section-312.3")
        self.assertEqual("gzip", seen["headers"].get("Accept-Encoding"))
        self.assertEqual(b"<html>x</html>", answer["payload"])

    def test_a_corrupt_gzip_answer_passes_through_unchanged(self):
        # D-162: a gzip member with a valid header but corrupt DEFLATE data must survive as
        # bytes — `zlib.error` is not an `OSError`, so it needs its own except arm.
        import gzip

        good = gzip.compress(b"x" * 64)
        body = good[:15] + bytes([good[15] ^ 0xFF]) + good[16:]

        class Response:
            status = 200
            headers = {"Content-Type": "text/xml", "Content-Encoding": "gzip"}

            def read(self, n=-1):
                return body

            def geturl(self):
                return "https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-16.xml"

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(url, method, timeout, headers=None, **kwargs):
            return Response()

        with mock.patch("memoforge.sources._open", fake_open):
            answer = sources.fetch_body(
                "https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-16.xml"
            )
        self.assertEqual(body, answer["payload"])

    def test_a_fetched_gzip_document_verifies_by_liveness(self):
        # D-162 (final Astra review): fetch negotiates gzip, so liveness must too — otherwise
        # the eCFR API answers its HEAD/GET with 406 and a fetched source reads `dead`.
        import gzip

        document = b"<DIV8 N=\"312.3\">verifiable parental consent</DIV8>" * 40
        body = gzip.compress(document)
        seen = []
        url = "https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-16.xml"

        class Response:
            status = 200
            headers = {"Content-Type": "text/xml", "Content-Encoding": "gzip"}

            def read(self, n=-1):
                return body

            def geturl(self):
                return url

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(request_url, method, timeout, headers=None, **kwargs):
            # the real `_open` fills in `probe_headers(url)` when none are passed (as
            # `probe_url` calls it); the fake mirrors that default, then plays the eCFR API.
            sent = dict(headers or sources.probe_headers(request_url))
            seen.append((method, sent))
            if sent.get("Accept-Encoding") != "gzip":
                raise urllib.error.HTTPError(request_url, 406, "Not Acceptable", {}, None)
            return Response()

        with mock.patch("memoforge.sources._open", fake_open):
            fetched = sources.fetch_body(url)
            probe = sources.probe_url(url, want_body=True)
        self.assertEqual("ok", probe["status"])
        self.assertEqual(fetched["payload"], document)
        self.assertEqual(probe["sha256"], state_io.sha256_bytes(fetched["payload"]))
        # one GET of `fetch_body`, then the HEAD + GET of `probe_url`
        self.assertEqual(["GET", "HEAD", "GET"], [method for method, _ in seen])
        for _, sent in seen:
            self.assertEqual("gzip", sent.get("Accept-Encoding"))

    def test_an_oversized_gzip_document_hashes_the_same_prefix_on_both_sides(self):
        # D-162 (re-review): compressed bytes fit under the cap but inflate beyond it — fetch
        # keeps the capped prefix, so liveness must hash that same prefix, not the whole body.
        import gzip

        body = gzip.compress(b"a" * 2200)
        url = "https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-16.xml"

        class Response:
            status = 200
            headers = {"Content-Type": "text/xml", "Content-Encoding": "gzip"}

            def read(self, n=-1):
                return body

            def geturl(self):
                return url

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(request_url, method, timeout, headers=None, **kwargs):
            return Response()

        with mock.patch.object(limits, "LIVENESS_MAX_BODY_BYTES", 1024):
            with mock.patch("memoforge.sources._open", fake_open):
                fetched = sources.fetch_body(url)
                probe = sources.probe_url(url, want_body=True)
        self.assertTrue(fetched["truncated"])
        self.assertEqual("ok", probe["status"])
        self.assertEqual(probe["sha256"], state_io.sha256_bytes(fetched["payload"]))

    # --- limits and refusals -------------------------------------------------

    def test_the_body_is_capped(self):
        original = limits.LIVENESS_MAX_BODY_BYTES
        limits.LIVENESS_MAX_BODY_BYTES = 1024
        self.addCleanup(setattr, limits, "LIVENESS_MAX_BODY_BYTES", original)
        body = LIVE_TEXT.encode("utf-8")
        with LocalServer(body) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/ok")
        self.assertEqual(1024, result["bytes"])
        self.assertTrue(result["truncated"])
        self.assertEqual(state_io.sha256_bytes(body[:1024]), result["sha256"])
        self.assertEqual(1024, (self.work_dir / result["path"]).stat().st_size)

    def test_a_body_shorter_than_its_declared_length_is_truncated(self):
        """D-199 fix round 2: a bounded read that ends early raises nothing — the header tells."""
        body = LIVE_TEXT.encode("utf-8")
        with LocalServer(body) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/short")
        self.assertTrue(result["truncated"], "the server promised more than it sent")
        self.assertLess(result["bytes"], len(body))

    def test_a_partial_content_answer_is_never_a_whole_document(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/partial")
        self.assertEqual(206, result["code"])
        self.assertTrue(result["truncated"])

    def test_a_declared_length_with_trailing_whitespace_still_counts(self):
        """Python's parser accepts `Content-Length: 3661 `; the value is stripped before it is read."""
        body = LIVE_TEXT.encode("utf-8")
        for suffix, name in ((" ", "space"), ("\t", "tab")):
            with self.subTest(suffix=name):
                with LocalServer(body, length_suffix=suffix) as base:
                    self.allow(sources.url_host(base))
                    short = self.fetch(f"{base}/short")
                    whole = self.fetch(f"{base}/ok")
                self.assertTrue(short["truncated"], "the server promised more than it sent")
                self.assertLess(short["bytes"], len(body))
                self.assertFalse(whole["truncated"], "a whole body is whole whatever the header's spacing")

    def test_an_interstitial_is_saved_but_never_ok(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/shell")
        self.assertEqual("unchecked", result["status"])
        self.assertEqual(200, result["code"])
        self.assertEqual("interstitial_suspected", result["error"])
        self.assertTrue(result["interstitial"])
        self.assertTrue((self.work_dir / result["path"]).is_file(), "kept for inspection")
        self.assertIn("cite", result["hint"])

    def test_a_challenge_is_named_and_the_status_is_unchecked(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/cloudflare")
        self.assertEqual("unchecked", result["status"])
        self.assertEqual("cloudflare_challenge", result["error"])

    def test_a_missing_document_is_dead(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            result = self.fetch(f"{base}/gone")
        self.assertEqual("dead", result["status"])
        self.assertEqual("http_404", result["error"])

    def test_an_unreachable_host_is_best_effort(self):
        self.allow("127.0.0.1")
        result = self.fetch("http://127.0.0.1:9/never")
        self.assertEqual("dead", result["status"])
        self.assertIsNone(result["path"])
        self.assertNotIn("errors", result, "a dead host is a status, not a refusal")

    def test_a_second_fetch_of_one_host_waits(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.url_host(base))
            self.fetch(f"{base}/ok")
            self.assertEqual([], self.waits, "the first fetch of a host owes nothing")
            self.fetch(f"{base}/ris.json")
        self.assertEqual(1, len(self.waits))
        self.assertAlmostEqual(limits.LIVENESS_HOST_DELAY_SECONDS, self.waits[0], delta=0.5)

    # --- the url parser (D-151) ---------------------------------------------

    def test_a_host_smuggled_into_the_fragment_is_refused_before_any_request(self):
        """D-151: `https://outside.example#@govinfo.gov` read as `govinfo.gov` and was fetched."""
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            crafted = f"{base}#@govinfo.gov"
            self.allow("govinfo.gov")
            result = self.fetch(crafted)
            self.assertEqual([], _Handler.seen, "refused before anything left the process")
        self.assertEqual(["host_not_allowed: 127.0.0.1"], result["errors"])
        self.assertIsNone(result.get("path"))
        self.assertEqual([], list((self.work_dir / sources.RAW_DIR).rglob("*")))

    def test_credentials_in_the_url_are_refused_outright(self):
        self.allow("govinfo.gov")
        result = self.fetch("https://evil.example@govinfo.gov/link/uscode/15/45")
        # A6: the refusal is journalled, so it names the address redacted — never the userinfo.
        self.assertEqual(["userinfo_not_allowed: https://govinfo.gov/link/uscode/15/45"], result["errors"])

    def test_the_parser_is_the_one_the_permission_gate_uses(self):
        self.assertEqual("outside.example", sources.request_host("https://outside.example#@govinfo.gov"))
        self.assertEqual("govinfo.gov", sources.request_host("HTTPS://GovInfo.GOV/link"))
        self.assertEqual("", sources.request_host("https://evil.example@govinfo.gov/"))
        self.assertEqual("", sources.request_host("ftp://govinfo.gov/x"))
        self.assertEqual("", sources.request_host("https:///path"))
        self.assertIsNone(sources.url_error("https://govinfo.gov/x"))
        self.assertEqual("unsupported_scheme", sources.url_error("file:///etc/passwd"))
        self.assertEqual("no_hostname", sources.url_error("https:///path"))

    # --- redirects (D-151) ---------------------------------------------------

    def test_an_allow_listed_hop_is_followed(self):
        body = LIVE_TEXT.encode("utf-8")
        with LocalServer(body) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/redirect")
        self.assertEqual("redirect", result["status"])
        self.assertEqual(["127.0.0.1"], result["redirects"])
        self.assertEqual(state_io.sha256_bytes(body), result["sha256"])

    def test_a_hop_to_a_host_outside_the_allowlist_is_refused(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/offsite")
            paths = [row["path"] for row in _Handler.seen]
        self.assertEqual("unchecked", result["status"])
        self.assertEqual("redirect_not_allowed: localhost", result["error"])
        self.assertIsNone(result["path"])
        self.assertEqual(["/offsite"], paths, "the hop was refused before it was requested")

    def test_a_redirect_loop_is_capped(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/loop")
            requests = len(_Handler.seen)
        self.assertEqual("unchecked", result["status"])
        self.assertEqual(f"too_many_redirects: {sources.MAX_REDIRECT_HOPS + 1}", result["error"])
        self.assertEqual(sources.MAX_REDIRECT_HOPS + 1, requests, "one request per hop, then it stops")

    # --- POST (D-151) --------------------------------------------------------

    def test_a_post_sends_the_json_body_as_application_json(self):
        """D-151: the Normattiva route of D-148 — `POST /atto/dettaglio-atto-urn` with an URN."""
        urn = "urn:nir:stato:decreto.legislativo:2003-06-30;196~art7!vig=2026-01-01"
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/ris.json", method="POST", json_body=json.dumps({"urn": urn}))
            sent = _Handler.posted[-1]
        self.assertEqual("ok", result["status"])
        self.assertEqual("POST", result["method"])
        self.assertEqual("/ris.json", sent["path"])
        self.assertEqual({"urn": urn}, json.loads(sent["body"]))
        self.assertTrue(sent["content_type"].startswith("application/json"), sent["content_type"])
        self.assertTrue(result["path"].endswith(".json"), result["path"])
        self.assertEqual(f"mf-fetch {sources.request_host(base)}", result["retrieval_tool"])

    def test_two_urns_on_one_endpoint_are_two_files(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            first = self.fetch(f"{base}/ris.json", method="POST", json_body='{"urn": "a"}')
            second = self.fetch(f"{base}/ris.json", method="POST", json_body='{"urn": "b"}')
            same = self.fetch(f"{base}/ris.json", method="POST", json_body='{"urn": "a"}')
        self.assertNotEqual(first["path"], second["path"], "the body is part of the file name")
        self.assertEqual(first["path"], same["path"], "the same call overwrites its own file")

    def test_a_json_body_needs_a_post(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/ris.json", json_body='{"urn": "a"}')
            self.assertEqual([], _Handler.seen)
        self.assertEqual(["json_body_requires_post"], result["errors"])

    def test_a_malformed_json_body_never_reaches_the_host(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/ris.json", method="POST", json_body="{not json}")
            self.assertEqual([], _Handler.seen)
        self.assertTrue(result["errors"][0].startswith("invalid_json_body:"), result["errors"])

    def test_the_method_is_one_of_two(self):
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            self.allow(sources.request_host(base))
            result = self.fetch(f"{base}/ok", method="DELETE")
            self.assertEqual([], _Handler.seen)
        self.assertEqual(["unsupported_method: DELETE"], result["errors"])
        self.assertEqual(("GET", "POST"), sources.FETCH_METHODS)

    # --- what liveness does with it ------------------------------------------

    def test_a_fetched_body_is_comparable_by_liveness(self):
        """D-149 adds `mf-fetch` to `BODY_COMPARABLE_TOOLS`: these bytes are the page's own."""
        self.assertIn("mf-fetch", sources.BODY_COMPARABLE_TOOLS)
        body = LIVE_TEXT.encode("utf-8")
        with LocalServer(body) as base:
            host = sources.url_host(base)
            self.allow(host)
            fetched = self.fetch(f"{base}/ok")
            sources.register_source(
                self.work_dir,
                layer="statutes",
                title="GDPR Article 6",
                citation="GDPR, Art. 6",
                url=f"{base}/ok",
                tool=fetched["retrieval_tool"],
                tier="critical",
                raw_file=self.work_dir / fetched["path"],
            )
            row = sources.run_liveness(
                argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
            )["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertEqual("confirmed", row["provenance"])


# --- save (D-199) -----------------------------------------------------------


class SaveTestCase(SourcesTestCase):
    """The helpers the `mf sources save` tests share (D-199)."""

    def setUp(self) -> None:
        super().setUp()
        sources._LAST_FETCH.clear()
        self.addCleanup(sources._LAST_FETCH.clear)

    def allow(self, *hosts: str) -> None:
        """Point the command at a test allowlist; the real file is exercised by its own test."""
        original = sources.allowlist_hosts
        sources.allowlist_hosts = lambda root=None: frozenset(hosts)
        self.addCleanup(setattr, sources, "allowlist_hosts", original)

    def save(self, url: str, **overrides) -> dict:
        return sources.run_save(argparse.Namespace(**save_namespace(str(self.work_dir), url, **overrides)))

    def records(self) -> dict:
        return sources.read_registry(self.work_dir)["sources"]

    def raw_files(self) -> list:
        root = self.work_dir / sources.RAW_DIR
        return sorted(path.name for path in root.rglob("*") if path.is_file())

    def temp_files(self) -> list:
        return [name for name in self.raw_files() if name.endswith(".tmp") or name.startswith(".")]

    def stored(self, record: dict) -> bytes:
        return (self.work_dir / record["raw_path"]).read_bytes()

    def snapshot(self, source_id: str) -> dict:
        return json.loads(json.dumps(self.records()[source_id], sort_keys=True))

    def assert_only_the_outcome_moved(self, before: dict, after: dict, outcome: str) -> None:
        """Failure never mutates: `meta.save_outcome` is the one field a failed save may write."""
        self.assertEqual(outcome, after["meta"]["save_outcome"])
        for field in sorted(set(before) | set(after)):
            with self.subTest(field=field):
                if field == "meta":
                    self.assertEqual({**before.get("meta", {}), "save_outcome": outcome}, after["meta"])
                else:
                    self.assertEqual(before.get(field), after.get(field))


class SaveTest(SaveTestCase):
    """D-199: one transaction from an address to a registered, certified source text."""

    # --- the happy path -----------------------------------------------------

    def test_a_full_russian_act_page_is_saved_as_full_text(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual([], result.get("errors", []))
        self.assertTrue(result["created"])
        self.assertFalse(result["idempotent"])
        self.assertEqual("case_law", result["layer"])
        self.assertEqual("full_text", result["raw_kind"])
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual("ok", result["status"])
        self.assertEqual(200, result["code"])
        self.assertEqual("agent_saved", result["provenance"])
        self.assertEqual("critical", result["tier"])
        source_id = result["source_id"]
        self.assertEqual(1, len(self.records()))
        record = self.records()[source_id]
        self.assertEqual(f"research/raw/case_law/{source_id}.md", record["raw_path"])
        self.assertEqual(result["raw_path"], record["raw_path"])
        self.assertEqual(f"mf-save {sources.url_host(record['url'])}", record["retrieval_tool"])
        self.assertTrue(record["retrieval_tool"].startswith("mf-save "), record["retrieval_tool"])
        self.assertEqual("full_text", sources.raw_kind_of(record))
        self.assertEqual("full_text", record["meta"]["save_outcome"])
        saved = self.stored(record)
        self.assertEqual(state_io.sha256_bytes(saved), record["raw_sha256"])
        self.assertEqual(result["raw_sha256"], record["raw_sha256"])
        self.assertEqual(len(saved), result["bytes"])
        text = saved.decode("utf-8")
        self.assertEqual(len(text), record["raw_chars"])
        self.assertIn(VS_ACT_NUMBER, text)
        self.assertNotIn("<pre>", text, "the page is converted before it is stored")
        self.assertEqual([f"{source_id}.md"], self.raw_files())

    def test_a_saved_text_is_body_comparable_so_liveness_can_confirm_it(self):
        """`prepare_raw` ran once, on the converted text: the digest is the one liveness recomputes."""
        self.assertIn("mf-save", sources.BODY_COMPARABLE_TOOLS)
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            self.assertTrue(sources.body_comparable(self.records()[result["source_id"]]))
            row = sources.run_liveness(
                argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
            )["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertEqual("confirmed", row["provenance"])

    def test_without_the_two_flags_the_page_is_only_an_excerpt(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok")
        self.assertEqual("excerpt:identity_unverified", result["save_outcome"])
        self.assertEqual("excerpt", result["raw_kind"])
        record = self.records()[result["source_id"]]
        self.assertEqual("excerpt", record["raw_kind"])
        self.assertEqual("excerpt:identity_unverified", record["meta"]["save_outcome"])
        self.assertTrue((self.work_dir / record["raw_path"]).is_file())

    def test_a_wrong_expected_number_registers_nothing(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number="5-КГ25-15-К2", expect_date=VS_ACT_DATE)
        self.assertEqual(["requisites_mismatch: number"], result["errors"])
        self.assertEqual("refused:requisites_mismatch", result["save_outcome"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files(), "a refusal writes no file")

    def test_an_article_page_reaches_full_text(self):
        with LocalServer(STATUTE_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(
                f"{base}/ok",
                layer="statutes",
                title="ГК РФ, ст. 152",
                citation="ГК РФ, ст. 152",
                expect_article="152",
            )
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual("full_text", result["raw_kind"])
        self.assertTrue(result["raw_path"].startswith("research/raw/statutes/"), result["raw_path"])

    def test_a_table_of_contents_entry_is_not_the_article(self):
        with LocalServer(STATUTE_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(
                f"{base}/ok",
                layer="statutes",
                title="ТК РФ, ст. 36",
                citation="ТК РФ, ст. 36",
                expect_article="36",
            )
        self.assertEqual("excerpt:too_short", result["save_outcome"])
        self.assertEqual("excerpt", result["raw_kind"])

    # --- the admission rules ------------------------------------------------

    def test_an_answer_that_is_not_the_document_registers_nothing(self):
        cases = (
            ("/forbidden", "unchecked"),
            ("/waf", "unchecked"),
            ("/missing", "dead"),
            ("/stub", "access_stub"),
            ("/shell", "interstitial"),
        )
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            for path, reason in cases:
                with self.subTest(path=path):
                    result = self.save(f"{base}{path}", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
                    self.assertEqual(f"refused:{reason}", result["save_outcome"])
                    self.assertTrue(result["errors"][0].startswith(reason), result["errors"])
                    self.assertEqual({}, self.records())
                    self.assertEqual([], self.raw_files())

    def test_a_truncated_body_is_refused_outright(self):
        """A partial document is exactly what this command exists to stop: never an excerpt."""
        original = limits.LIVENESS_MAX_BODY_BYTES
        limits.LIVENESS_MAX_BODY_BYTES = 3072
        self.addCleanup(setattr, limits, "LIVENESS_MAX_BODY_BYTES", original)
        self.assertGreater(len(VS_ACT_PAGE), limits.LIVENESS_MAX_BODY_BYTES)
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual("refused:truncated", result["save_outcome"])
        self.assertEqual(["truncated"], result["errors"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_body_cut_short_by_the_server_is_never_certified(self):
        """The act ends on «определила:» with its ruling absent — and used to certify as whole."""
        with LocalServer(VS_ACT_PAGE, short_bytes=VS_ACT_CUT) as base:
            self.allow(sources.url_host(base))
            for path in ("/short", "/partial"):
                with self.subTest(path=path):
                    result = self.save(f"{base}{path}", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
                    self.assertEqual("refused:truncated", result["save_outcome"])
                    self.assertEqual(["truncated"], result["errors"])
                    self.assertEqual({}, self.records())
                    self.assertEqual([], self.raw_files())

    def test_a_declared_length_with_trailing_whitespace_is_still_a_declaration(self):
        """`Content-Length: 3661 ` parses; an unstripped digit test skipped the comparison."""
        for suffix, name in ((" ", "space"), ("\t", "tab")):
            with self.subTest(suffix=name):
                with LocalServer(VS_ACT_PAGE, short_bytes=VS_ACT_CUT, length_suffix=suffix) as base:
                    self.allow(sources.url_host(base))
                    result = self.save(f"{base}/short", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
                self.assertEqual("refused:truncated", result["save_outcome"])
                self.assertEqual({}, self.records())
                self.assertEqual([], self.raw_files())

    def test_a_non_text_media_type_is_refused(self):
        """D-201 admits a PDF; every other non-text answer is still refused as it always was."""
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/image.png", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual("refused:unsupported_media_type", result["save_outcome"])
        self.assertEqual(["unsupported_media_type: image/png"], result["errors"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_an_undeclared_pdf_is_saved_by_its_signature(self):
        """D-201: a portal that sends a PDF with no `Content-Type` still sends a PDF, and it is kept.

        Task 4 refused those bytes (`unsupported_media_type`) so they could never be decoded into
        mojibake; now the signature routes them to the PDF path instead of to a refusal.
        """
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save(
                f"{base}/ok",
                title=VSRF_PDF_TITLE,
                citation=VSRF_PDF_CITATION,
                expect_number=VSRF_PDF_NUMBER,
                expect_date=VSRF_PDF_DATE,
            )
        self.assertEqual([], result.get("errors", []))
        self.assertEqual("full_text", result["save_outcome"])
        record = self.records()[result["source_id"]]
        self.assertEqual(VSRF_PDF_SHA256, record["raw_original_sha256"])
        self.assertEqual(VSRF_PDF, (self.work_dir / record["raw_original_path"]).read_bytes())

    def test_a_plain_text_answer_without_a_declared_type_is_still_saved(self):
        with LocalServer(VS_ACT_TEXT) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(VS_ACT_TEXT, self.stored(self.records()[result["source_id"]]))

    def test_a_redirect_off_the_allowlist_names_the_hop(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/offsite", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual(["host_not_allowed: localhost"], result["errors"])
        self.assertEqual("refused:host_not_allowed", result["save_outcome"])
        self.assertEqual({}, self.records())

    def test_a_host_outside_the_allowlist_is_never_requested(self):
        result = self.save("https://example.org/act")
        self.assertEqual(["host_not_allowed: example.org"], result["errors"])
        self.assertEqual("refused:host_not_allowed", result["save_outcome"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_url_that_is_not_http_is_refused(self):
        result = self.save("file:///etc/passwd")
        self.assertEqual(["unsupported_scheme: [address removed]"], result["errors"])
        self.assertEqual("refused:url_error", result["save_outcome"])
        self.assertEqual({}, self.records())

    def test_a_resolver_that_is_not_declared_is_not_available(self):
        """D-202: both declared resolvers have landed (task 6 `vsrf`, task 7 `sudact`); the parser
        admits no other name, and a caller that builds the namespace itself is still refused."""
        self.assertEqual(("vsrf", "sudact"), sources.SAVE_RESOLVERS)
        result = self.save("", resolve="casus")
        self.assertEqual(["resolver_not_available: casus"], result["errors"])
        self.assertEqual({}, self.records())

    # --- the freeze ---------------------------------------------------------

    def test_a_save_after_the_freeze_is_refused_before_the_network(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(layer="case_law", title=VS_ACT_TITLE, citation=VS_ACT_CITATION, url=f"{base}/ok")
            self.write_findings([{"source_id": next(iter(self.records()))}], layer="case_law")
            self.freeze()
            _Handler.seen = []
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            self.assertEqual([], _Handler.seen, "the freeze is checked before the address is called")
        self.assertEqual(["sources_frozen"], result["errors"])
        self.assertEqual([], self.raw_files())

    def test_an_occupied_id_after_the_freeze_changes_nothing(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                raw_file=self.raw_file(),
                raw_kind="excerpt",
            )
            self.write_findings([{"source_id": "vs-act"}], layer="case_law")
            self.freeze()
            before = self.snapshot("vs-act")
            before_bytes = self.stored(self.records()["vs-act"])
            result = self.save(f"{base}/ok", id="vs-act", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual(["sources_frozen"], result["errors"])
        self.assertEqual(before, self.snapshot("vs-act"))
        self.assertEqual(before_bytes, self.stored(self.records()["vs-act"]))

    # --- failure never mutates ----------------------------------------------

    def test_a_refusal_on_an_existing_record_writes_only_the_outcome(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                raw_file=self.raw_file(),
                raw_kind="excerpt",
            )
            before = self.snapshot("vs-act")
            before_bytes = self.stored(self.records()["vs-act"])
            result = self.save(f"{base}/ok", expect_number="5-КГ25-15-К2", expect_date=VS_ACT_DATE)
        self.assertEqual("refused:requisites_mismatch", result["save_outcome"])
        self.assertEqual("vs-act", result["source_id"])
        self.assert_only_the_outcome_moved(before, self.snapshot("vs-act"), "refused:requisites_mismatch")
        self.assertEqual(before_bytes, self.stored(self.records()["vs-act"]))

    def test_a_refusal_never_stamps_a_record_that_is_not_this_source(self):
        """`--id X` was free at step 1; another process claims it while this request is on the net."""
        stranger: dict = {}

        def claim_the_id_then_fail(url, hosts, **kwargs):
            self.register(
                layer="case_law",
                source_id="vs-act",
                title="АС МО, постановление № А40-12345/2024",
                citation="Постановление АС МО от 14.03.2025 № А40-12345/2024",
                url="https://example.org/other-act",
                raw_file=self.raw_file(),
                raw_kind="excerpt",
            )
            stranger.update(self.snapshot("vs-act"))
            return {
                "status": "dead",
                "code": 404,
                "content_type": "",
                "truncated": False,
                "interstitial": False,
                "error": "http_404",
                "redirects": [],
                "payload": b"",
            }

        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            original = sources.fetch_allowed
            sources.fetch_allowed = claim_the_id_then_fail
            self.addCleanup(setattr, sources, "fetch_allowed", original)
            result = self.save(f"{base}/ok", id="vs-act", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual("refused:dead", result["save_outcome"])
        self.assertNotIn("source_id", result, "nothing was written, so no record is named")
        self.assertEqual(stranger, self.snapshot("vs-act"))
        self.assertNotIn("save_outcome", self.records()["vs-act"].get("meta") or {})

    # --- the candidate is validated before anything is published ------------

    def test_a_registry_the_schema_rejects_publishes_no_file(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            with self.assertRaises(ValueError):
                self.save(
                    f"{base}/ok",
                    expect_number=VS_ACT_NUMBER,
                    expect_date=VS_ACT_DATE,
                    identifiers='{"unexpected": "x"}',
                )
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files(), "the file is published only after the candidate validates")

    def test_a_registry_write_that_fails_leaves_the_old_text_in_place(self):
        """The earlier window: the registry is persisted before the file, so nothing was published."""

        def refuse_to_write(work_dir, registry):
            raise OSError("disk gone")

        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                raw_file=self.raw_file(text="Выдержка из определения.\n"),
                raw_kind="excerpt",
            )
            before = self.snapshot("vs-act")
            before_bytes = self.stored(self.records()["vs-act"])
            original = sources.write_registry
            sources.write_registry = refuse_to_write
            self.addCleanup(setattr, sources, "write_registry", original)
            with self.assertRaises(OSError):
                self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            sources.write_registry = original
        self.assertEqual(before, self.snapshot("vs-act"))
        self.assertEqual(before_bytes, self.stored(self.records()["vs-act"]))
        self.assertEqual([], self.temp_files())

    # --- the upward path ----------------------------------------------------

    def test_a_record_holding_no_text_takes_the_excerpt(self):
        """Nothing is displaced where nothing is stored: a citation-only record gains the text."""
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                tier="background",
            )
            self.assertIsNone(self.records()["vs-act"]["raw_path"])
            result = self.save(f"{base}/ok")
        self.assertEqual("excerpt:identity_unverified", result["save_outcome"])
        # Fix round 1: the record held no digest at all, so this call did not find the bytes it
        # already had — it brought new ones. Not idempotent, however little was displaced.
        self.assertFalse(result["idempotent"])
        self.assertEqual("vs-act", result["source_id"])
        record = self.records()["vs-act"]
        self.assertEqual("excerpt", record["raw_kind"])
        self.assertEqual("research/raw/case_law/vs-act.md", record["raw_path"])
        self.assertIn(VS_ACT_NUMBER, self.stored(record).decode("utf-8"))
        self.assertEqual(state_io.sha256_bytes(self.stored(record)), record["raw_sha256"])
        self.assertEqual("excerpt:identity_unverified", record["meta"]["save_outcome"])

    def test_the_upward_path_replaces_an_excerpt_with_the_saved_full_text(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                raw_file=self.raw_file(text="Выдержка из определения.\n"),
                raw_kind="excerpt",
            )
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual("vs-act", result["source_id"])
        self.assertFalse(result["created"])
        # Fix round 1: the excerpt's digest is not the act's, so the save replaced text rather
        # than confirming it. `created` is false and `idempotent` is false: both are true facts.
        self.assertFalse(result["idempotent"])
        self.assertEqual("full_text", result["raw_kind"])
        record = self.records()["vs-act"]
        self.assertEqual("full_text", record["raw_kind"])
        self.assertEqual("full_text", record["meta"]["save_outcome"])
        self.assertIn(VS_ACT_NUMBER, self.stored(record).decode("utf-8"))
        self.assertEqual(state_io.sha256_bytes(self.stored(record)), record["raw_sha256"])
        self.assertEqual(1, len(self.records()))
        self.assertEqual(["vs-act.md"], self.raw_files())

    def test_a_new_excerpt_never_displaces_the_text_already_stored(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                raw_file=self.raw_file(text="Развёрнутая выдержка из определения. " * 200),
                raw_kind="excerpt",
            )
            before = self.snapshot("vs-act")
            before_bytes = self.stored(self.records()["vs-act"])
            result = self.save(f"{base}/ok")
        self.assertEqual("excerpt:identity_unverified", result["save_outcome"])
        # Fix round 1: other bytes arrived and were discarded — nothing was published, but the
        # answer never calls a discarded document the same document.
        self.assertFalse(result["idempotent"])
        self.assertEqual("vs-act", result["source_id"])
        self.assert_only_the_outcome_moved(before, self.snapshot("vs-act"), "excerpt:identity_unverified")
        self.assertEqual(before_bytes, self.stored(self.records()["vs-act"]))

    def test_another_act_under_an_occupied_id_is_a_collision(self):
        with LocalServer(CASSATION_PAGE) as base:
            self.allow(sources.url_host(base))
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/other",
                raw_file=self.raw_file(),
                raw_kind="excerpt",
            )
            before = self.snapshot("vs-act")
            _Handler.seen = []
            result = self.save(
                f"{base}/ok",
                id="vs-act",
                title="АС МО, постановление № А40-12345/2024",
                citation="Постановление АС МО от 14.03.2025 № А40-12345/2024",
                expect_number="А40-12345/2024",
                expect_date="2025-03-14",
            )
            self.assertEqual([], _Handler.seen, "a collision is answered before the address is called")
        self.assertTrue(result["errors"][0].startswith("source_id_collision: vs-act"), result["errors"])
        self.assertEqual(before, self.snapshot("vs-act"))
        self.assertNotIn("save_outcome", self.records()["vs-act"].get("meta") or {})

    def test_another_text_over_a_code_saved_record_is_refused(self):
        with LocalServer(VS_ACT_PAGE, variants=(VS_ACT_PAGE, VS_ACT_VARIANT)) as base:
            self.allow(sources.url_host(base))
            first = self.save(f"{base}/varying", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            before = self.snapshot(first["source_id"])
            before_bytes = self.stored(self.records()[first["source_id"]])
            second = self.save(f"{base}/varying", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual("full_text", first["save_outcome"])
        self.assertTrue(second["errors"][0].startswith("source_id_collision"), second["errors"])
        self.assertEqual(before, self.snapshot(first["source_id"]), "the held record is intact")
        self.assertEqual(before_bytes, self.stored(self.records()[first["source_id"]]))
        self.assertEqual([f"{first['source_id']}.md"], self.raw_files())
        self.assertTrue(self.waits, "the second call waited out the politeness pause it owed the host")

    def test_a_downgrade_over_a_code_saved_record_is_refused(self):
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            first = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            before = self.snapshot(first["source_id"])
            second = self.save(f"{base}/ok")
        self.assertEqual(
            [f"source_is_code_saved: {first['source_id']} holds text saved by mf sources save"],
            second["errors"],
        )
        self.assertEqual(before, self.snapshot(first["source_id"]))

    # --- the temporary file -------------------------------------------------

    def test_the_temporary_file_is_gone_after_a_success_and_after_a_refusal(self):
        with LocalServer(VS_ACT_PAGE, variants=(VS_ACT_PAGE, VS_ACT_VARIANT)) as base:
            self.allow(sources.url_host(base))
            first = self.save(f"{base}/varying", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            self.assertEqual("full_text", first["save_outcome"])
            self.assertEqual([], self.temp_files(), "the success path removes its temporary file")
            second = self.save(f"{base}/varying", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            self.assertTrue(second["errors"])
            self.assertEqual([], self.temp_files(), "the refusal path removes its temporary file")
        self.assertEqual(1, len(self.raw_files()))


class ExtractPdfTextTest(unittest.TestCase):
    """D-201: the text layer is read once, with `pypdf`, and there is no second extractor."""

    def test_a_pdf_with_a_text_layer_yields_its_text(self):
        text = sources.extract_pdf_text(VSRF_PDF)
        self.assertIsNotNone(text)
        # The exact rendering belongs to `pypdf` and is never pinned: a version bump must not
        # redden this suite. What is pinned is that the act is readable in what came out.
        self.assertGreaterEqual(len(text), source_text.FULL_TEXT_MIN_CHARS_CASE)
        self.assertIn(VSRF_PDF_NUMBER, text)
        self.assertIn("установил", text)

    def test_a_scan_has_no_text_layer_at_all(self):
        self.assertIsNone(sources.extract_pdf_text(SCAN_PDF))

    def test_a_pdf_pypdf_cannot_parse_has_no_text_layer_either(self):
        self.assertIsNone(sources.extract_pdf_text(PDF_BYTES))

    def test_without_pypdf_there_is_no_text_layer_and_no_crash(self):
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            self.assertIsNone(sources.extract_pdf_text(VSRF_PDF))


class SavePdfTest(SaveTestCase):
    """D-201: the PDF arrives as bytes, and the text layer is a convenience."""

    def save_pdf(self, base: str, path: str = "/served.pdf", **overrides) -> dict:
        payload = {
            "title": VSRF_PDF_TITLE,
            "citation": VSRF_PDF_CITATION,
            "expect_number": VSRF_PDF_NUMBER,
            "expect_date": VSRF_PDF_DATE,
        }
        payload.update(overrides)
        return self.save(f"{base}{path}", **payload)

    # --- bytes first --------------------------------------------------------

    def test_a_pdf_is_stored_as_the_bytes_the_server_served(self):
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save_pdf(base)
        self.assertEqual([], result.get("errors", []))
        source_id = result["source_id"]
        record = self.records()[source_id]
        original = self.work_dir / record["raw_original_path"]
        self.assertEqual(f"research/raw/case_law/{source_id}.pdf", record["raw_original_path"])
        self.assertEqual(VSRF_PDF, original.read_bytes(), "the body is stored exactly as served")
        self.assertEqual(VSRF_PDF_SHA256, record["raw_original_sha256"])
        self.assertEqual(VSRF_PDF_SHA256, state_io.sha256_bytes(original.read_bytes()))
        self.assertEqual(record["raw_original_path"], result["raw_original_path"])
        self.assertEqual(record["raw_original_sha256"], result["raw_original_sha256"])

    def test_the_text_layer_is_stored_beside_the_original_and_reaches_full_text(self):
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save_pdf(base)
        source_id = result["source_id"]
        record = self.records()[source_id]
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual("full_text", result["raw_kind"])
        self.assertEqual(f"research/raw/case_law/{source_id}.md", record["raw_path"])
        text = (self.work_dir / record["raw_path"]).read_text(encoding="utf-8")
        self.assertGreaterEqual(len(text), source_text.FULL_TEXT_MIN_CHARS_CASE)
        self.assertIn(VSRF_PDF_NUMBER, text)
        self.assertIn("установил", text)
        self.assertEqual(state_io.sha256_bytes(text.encode("utf-8")), record["raw_sha256"])
        self.assertEqual(sorted([f"{source_id}.md", f"{source_id}.pdf"]), self.raw_files())

    def test_a_pdf_whose_requisites_do_not_match_registers_nothing_at_all(self):
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save_pdf(base, expect_number="305-ЭС24-8702")
        self.assertEqual(["requisites_mismatch: number"], result["errors"])
        self.assertEqual("refused:requisites_mismatch", result["save_outcome"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files(), "not even the original is kept")

    # --- without a text layer ----------------------------------------------

    def test_a_scan_keeps_its_original_and_is_not_certified(self):
        with LocalServer(SCAN_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save_pdf(base)
        self.assertEqual([], result.get("errors", []))
        source_id = result["source_id"]
        record = self.records()[source_id]
        self.assertEqual("excerpt:pdf_text_unavailable", result["save_outcome"])
        self.assertEqual("none", result["raw_kind"])
        self.assertIsNone(record["raw_path"])
        self.assertIsNone(record["raw_sha256"])
        self.assertEqual(0, record["raw_chars"])
        self.assertEqual("none", record["raw_kind"])
        self.assertEqual("excerpt:pdf_text_unavailable", record["meta"]["save_outcome"])
        self.assertEqual(SCAN_PDF_SHA256, record["raw_original_sha256"])
        self.assertEqual([f"{source_id}.pdf"], self.raw_files())
        self.assertIsNone(sources.read_raw_text(self.work_dir, record))

    def test_without_pypdf_a_readable_pdf_is_saved_uncertified_too(self):
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            with mock.patch.dict(sys.modules, {"pypdf": None}):
                result = self.save_pdf(base)
        self.assertEqual("excerpt:pdf_text_unavailable", result["save_outcome"])
        self.assertEqual("none", result["raw_kind"])
        record = self.records()[result["source_id"]]
        self.assertIsNone(record["raw_path"])
        self.assertEqual(VSRF_PDF_SHA256, record["raw_original_sha256"])
        self.assertEqual([f"{result['source_id']}.pdf"], self.raw_files())

    # --- refusals -----------------------------------------------------------

    def test_a_truncated_pdf_is_refused_and_nothing_is_written(self):
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            for path in ("/short", "/partial"):
                with self.subTest(path=path):
                    result = self.save_pdf(base, path)
                    self.assertEqual(["pdf_truncated"], result["errors"])
                    self.assertEqual("refused:pdf_truncated", result["save_outcome"])
                    self.assertEqual({}, self.records())
                    self.assertEqual([], self.raw_files())

    def test_a_pdf_over_the_ceiling_is_refused_too(self):
        original = limits.LIVENESS_MAX_BODY_BYTES
        limits.LIVENESS_MAX_BODY_BYTES = 4096
        self.addCleanup(setattr, limits, "LIVENESS_MAX_BODY_BYTES", original)
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save_pdf(base)
        self.assertEqual("refused:pdf_truncated", result["save_outcome"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    # --- idempotence over bytes --------------------------------------------

    def test_the_same_scan_saved_twice_leaves_one_record_and_one_file(self):
        with LocalServer(SCAN_PDF) as base:
            self.allow(sources.url_host(base))
            first = self.save_pdf(base)
            second = self.save_pdf(base)
        self.assertTrue(first["created"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(1, len(self.records()))
        self.assertEqual([f"{first['source_id']}.pdf"], self.raw_files())

    def test_another_scan_under_the_same_identity_is_never_called_idempotent(self):
        """Fix round 1: two different scans at one address are two documents, not one."""
        with LocalServer(SCAN_PDF, variants=(SCAN_PDF, SCAN_PDF_OTHER)) as base:
            self.allow(sources.url_host(base))
            first = self.save_pdf(base, "/varying")
            self.assertEqual("excerpt:pdf_text_unavailable", first["save_outcome"])
            before = self.snapshot(first["source_id"])
            second = self.save_pdf(base, "/varying")
        self.assertFalse(second["created"])
        self.assertFalse(second["idempotent"], "different bytes are never the same document")
        self.assertEqual(first["source_id"], second["source_id"])
        # Nothing was displaced: the original the code saved is still byte for byte on disk, and
        # `meta.save_outcome` is the one field a save that publishes nothing may write.
        record = self.records()[first["source_id"]]
        self.assertEqual(SCAN_PDF, (self.work_dir / record["raw_original_path"]).read_bytes())
        self.assertEqual(SCAN_PDF_SHA256, record["raw_original_sha256"])
        self.assert_only_the_outcome_moved(
            before, self.snapshot(first["source_id"]), "excerpt:pdf_text_unavailable"
        )
        self.assertEqual([f"{first['source_id']}.pdf"], self.raw_files())

    def test_a_missing_original_replaced_by_other_bytes_is_not_a_repair(self):
        """Fix round 1: publishing is right — the record named a file that is not there — but the
        answer says `idempotent: false`, because a replacement is not a repair."""
        with LocalServer(SCAN_PDF, variants=(SCAN_PDF, SCAN_PDF_OTHER)) as base:
            self.allow(sources.url_host(base))
            first = self.save_pdf(base, "/varying")
            (self.work_dir / self.records()[first["source_id"]]["raw_original_path"]).unlink()
            second = self.save_pdf(base, "/varying")
        self.assertFalse(second["idempotent"])
        record = self.records()[first["source_id"]]
        self.assertEqual(SCAN_PDF_OTHER, (self.work_dir / record["raw_original_path"]).read_bytes())
        self.assertEqual(state_io.sha256_bytes(SCAN_PDF_OTHER), record["raw_original_sha256"])

    def test_other_bytes_never_fill_a_missing_original_beside_a_surviving_text(self):
        """Fix round 4: «holds something» means either component, and a repair is only ever the
        same document arriving again.

        Filling the gap with another document would record a pair that never existed as one —
        and a later freeze would pin the mixture without noticing, because the integrity check
        asks only whether each file still matches its own digest, which after the rewrite it does.
        """
        with LocalServer(VSRF_PDF, variants=(VSRF_PDF, SCAN_PDF_OTHER)) as base:
            self.allow(sources.url_host(base))
            first = self.save(f"{base}/varying")
            source_id = first["source_id"]
            self.assertEqual("excerpt:identity_unverified", first["save_outcome"])
            (self.work_dir / self.records()[source_id]["raw_original_path"]).unlink()
            before = self.snapshot(source_id)
            held = (self.work_dir / self.records()[source_id]["raw_path"]).read_bytes()
            second = self.save(f"{base}/varying")
        self.assertFalse(second["idempotent"], "other bytes are never the same document")
        self.assertEqual([f"{source_id}.md"], self.raw_files(), "the gap stays a gap")
        self.assertEqual(held, (self.work_dir / self.records()[source_id]["raw_path"]).read_bytes())
        self.assert_only_the_outcome_moved(
            before, self.snapshot(source_id), "excerpt:pdf_text_unavailable"
        )

    def test_other_bytes_never_fill_a_missing_text_beside_a_surviving_original(self):
        """The mirror of the case above: the surviving component is the original this time.

        The second document carries a text layer of its own, so the old repair branch really did
        have a half to offer into the gap — and offering it would pair B's text with A's original.
        """
        with LocalServer(VSRF_PDF, variants=(VSRF_PDF, VSRF_PDF_OTHER)) as base:
            self.allow(sources.url_host(base))
            first = self.save(f"{base}/varying")
            source_id = first["source_id"]
            (self.work_dir / self.records()[source_id]["raw_path"]).unlink()
            before = self.snapshot(source_id)
            second = self.save(f"{base}/varying")
        self.assertEqual("excerpt:identity_unverified", second["save_outcome"])
        self.assertFalse(second["idempotent"])
        self.assertEqual([f"{source_id}.pdf"], self.raw_files(), "the gap stays a gap")
        self.assertEqual(
            VSRF_PDF, (self.work_dir / self.records()[source_id]["raw_original_path"]).read_bytes()
        )
        self.assert_only_the_outcome_moved(
            before, self.snapshot(source_id), "excerpt:identity_unverified"
        )

    def test_a_missing_original_is_republished_by_the_next_save(self):
        with LocalServer(SCAN_PDF) as base:
            self.allow(sources.url_host(base))
            first = self.save_pdf(base)
            (self.work_dir / self.records()[first["source_id"]]["raw_original_path"]).unlink()
            self.assertEqual([], self.raw_files())
            again = self.save_pdf(base)
        # Fix round 1: the same bytes over a record that names a missing file is a repair, and a
        # repair is idempotent — the record ends holding exactly what it always claimed to hold.
        self.assertTrue(again["idempotent"])
        self.assertEqual([f"{first['source_id']}.pdf"], self.raw_files())
        self.assertEqual(SCAN_PDF, (self.work_dir / f"research/raw/case_law/{first['source_id']}.pdf").read_bytes())

    def test_another_pdf_over_a_code_saved_original_is_a_collision(self):
        with LocalServer(VSRF_PDF, variants=(VSRF_PDF, VSRF_PDF + b"%tampered\n")) as base:
            self.allow(sources.url_host(base))
            first = self.save_pdf(base, "/varying")
            self.assertEqual("full_text", first["save_outcome"])
            second = self.save_pdf(base, "/varying")
        self.assertTrue(second["errors"][0].startswith("source_id_collision:"))
        self.assertEqual(VSRF_PDF, (self.work_dir / f"research/raw/case_law/{first['source_id']}.pdf").read_bytes())

    # --- liveness -----------------------------------------------------------

    def test_liveness_compares_the_original_digest_and_extracts_no_text(self):
        with LocalServer(SCAN_PDF) as base:
            self.allow(sources.url_host(base))
            result = self.save_pdf(base)
            with mock.patch.object(sources, "extract_pdf_text", side_effect=AssertionError("no extraction")):
                row = sources.run_liveness(
                    argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
                )["checked"][0]
        self.assertEqual(result["source_id"], row["source_id"])
        self.assertEqual("ok", row["status"])
        self.assertEqual("confirmed", row["provenance"])

    def test_a_body_that_only_normalises_to_the_pin_is_reported_changed(self):
        """Fix round 3: an original has no normalised form — it is bytes.

        `probe_url` offers `sha256_normalised` so a text page carrying a token is not reported
        `changed` after the scrub (A7). A PDF never went through that scrub, so accepting the
        normalised value would confirm a file whose bytes are not the ones on disk.
        """
        with LocalServer(SCAN_PDF) as base:
            self.allow(sources.url_host(base))
            source_id = self.save_pdf(base)["source_id"]
            # The body that came back is not the file on disk; only its scrubbed form is.
            probe = {
                "status": "ok",
                "code": 200,
                "sha256": "0" * 64,
                "sha256_normalised": SCAN_PDF_SHA256,
                "error": None,
                "redirects": [],
            }
            with mock.patch.object(sources, "probe_url", return_value=probe):
                row = sources.run_liveness(
                    argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
                )["checked"][0]
        self.assertEqual(source_id, row["source_id"])
        self.assertEqual("changed", row["status"])
        self.assertEqual("agent_saved", row["provenance"], "never promoted on a normalised match")

    def test_a_text_record_still_matches_on_the_normalised_digest(self):
        """A7 is untouched for text: only a record carrying an original loses the licence."""
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
            record = self.records()[result["source_id"]]
            self.assertNotIn("raw_original_sha256", record)
            probe = {
                "status": "ok",
                "code": 200,
                "sha256": "0" * 64,
                "sha256_normalised": record["raw_sha256"],
                "error": None,
                "redirects": [],
            }
            with mock.patch.object(sources, "probe_url", return_value=probe):
                row = sources.run_liveness(
                    argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
                )["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertEqual("confirmed", row["provenance"])

    def test_an_address_serving_other_bytes_is_reported_changed(self):
        with LocalServer(SCAN_PDF) as base:
            self.allow(sources.url_host(base))
            source_id = self.save_pdf(base)["source_id"]
        with LocalServer(VSRF_PDF) as base:
            # The same address, another document: the comparison is on the original digest, so
            # this is `changed` and not a silent `ok` over two files that share no bytes.
            registry = sources.read_registry(self.work_dir)
            registry["sources"][source_id]["url"] = f"{base}/served.pdf"
            sources.write_registry(self.work_dir, registry)
            row = sources.run_liveness(
                argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
            )["checked"][0]
        self.assertEqual("changed", row["status"])
        self.assertEqual("agent_saved", row["provenance"])


class ResolveVsrfTest(SaveTestCase):
    """D-202: from the requisites of a Supreme Court chamber act to the address of its own PDF.

    Every fixture here is the real `305-ЭС24-8702` chain as vsrf.ru served it — the listing page
    and seven of its eight PDFs, the eighth being the Task 5 fixture byte for byte — with one
    labelled model (`model-ambiguous-twin.pdf`) for the case the chain does not contain. Nothing
    leaves `127.0.0.1`: the portal is a `LocalServer` and the base address is injected.
    """

    def resolve(self, base: str, **overrides) -> dict:
        """`mf sources save --resolve vsrf` against the `LocalServer` standing in for the portal."""
        payload = {
            "title": VSRF_ACT_TITLE,
            "citation": VSRF_ACT_CITATION,
            "expect_number": VSRF_PDF_NUMBER,
            "expect_date": VSRF_CHAMBER_DATE,
            "resolve": "vsrf",
        }
        payload.update(overrides)
        with mock.patch.object(sources, "VSRF_BASE", base):
            return self.save("", **payload)

    def chain(self) -> LocalServer:
        """The real listing page with the eight acts of the chain behind it."""
        return LocalServer(b"", routes=vsrf_routes(VSRF_LISTING, VSRF_CHAIN_BODIES))

    def paths(self) -> list:
        return [row["path"] for row in _Handler.seen]

    # --- the chain, the twins, the winner ------------------------------------

    def test_the_chain_of_one_case_number_resolves_to_the_chamber_ruling(self):
        """The trap of input 02: eight acts, two of them `(1,3)`, only the date telling them apart."""
        with self.chain() as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual([], result.get("errors", []), result)
        self.assertTrue(result["created"])
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual("full_text", result["raw_kind"])
        self.assertEqual(f"{base}{vsrf_path('2394482')}", result["url"])
        source_id = result["source_id"]
        record = self.records()[source_id]
        self.assertEqual(1, len(self.records()))
        self.assertEqual(f"mf-save {sources.url_host(base)}", record["retrieval_tool"])
        # The chamber's ruling of 14.08.2024, not the referral order of 11.07.2024 that carries
        # the very same number and the very same bracketed suffix.
        self.assertEqual(VSRF_CHAMBER_SHA256, record["raw_original_sha256"])
        self.assertNotIn(record["raw_original_sha256"], (VSRF_PDF_SHA256, VSRF_REFERRAL_SHA256))
        self.assertEqual(sorted([f"{source_id}.md", f"{source_id}.pdf"]), self.raw_files())

    def test_the_listing_is_asked_for_the_base_number_without_the_bracketed_suffix(self):
        """Search broadly, certify precisely: the portal's index does not know the suffix.

        Measured against vsrf.ru: `305-ЭС24-8702` answers with the whole chain of eight acts,
        `305-ЭС24-8702 (1,3)` answers with none — so asking it verbatim would fail on exactly the
        case the resolver exists to close.
        """
        with self.chain() as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertIn(" (1,3)", VSRF_PDF_NUMBER, "the flag carries the suffix")
        expected = (
            sources.VSRF_LISTING_PATH
            + "?numberExact=true&actDateExact=off&number="
            + urllib.parse.quote("305-ЭС24-8702", safe="")
        )
        self.assertEqual(expected, self.paths()[0])
        self.assertNotIn("%28", self.paths()[0], "no bracket reaches the portal")
        self.assertEqual("full_text", result["save_outcome"], "and the chain is still resolved")

    def test_the_listing_url_strips_only_a_trailing_bracketed_group(self):
        """Stripped where the vsrf query is built and nowhere shared: brackets mean other things."""
        cases = {
            "305-ЭС24-8702 (1,3)": "305-ЭС24-8702",
            "305-ЭС24-8702(1, 3)": "305-ЭС24-8702",
            "5-КГ25-14-К2": "5-КГ25-14-К2",
            "А40-1234/2024 (Б)": "А40-1234/2024 (Б)",
            "(1,3)": "(1,3)",
        }
        for number, sent in cases.items():
            with self.subTest(number=number):
                url = sources.vsrf_listing_url(number, "https://www.vsrf.ru")
                self.assertTrue(url.endswith("&number=" + urllib.parse.quote(sent, safe="")), url)

    def test_the_candidates_are_read_in_page_order(self):
        """Dedupe **before** the cap: the page prints its eight links six times each."""
        with self.chain() as base:
            self.allow(sources.url_host(base))
            self.resolve(base)
        paths = self.paths()
        self.assertEqual(sources.VSRF_LISTING_PATH, paths[0].split("?")[0])
        self.assertEqual([vsrf_path(act) for act in VSRF_CHAIN], paths[1:9], "eight distinct acts, in page order")
        self.assertEqual(vsrf_path("2394482"), paths[9], "the save fetches the address it was handed")
        self.assertEqual(10, len(paths), "48 printed links, 8 candidates, 1 listing and 1 save")

    def test_the_politeness_pause_is_owed_between_every_candidate(self):
        with self.chain() as base:
            self.allow(sources.url_host(base))
            self.resolve(base)
        self.assertEqual(len(_Handler.seen) - 1, len(self.waits), "every request but the first waited")
        self.assertEqual(9, len(self.waits))
        self.assertTrue(all(pause > 0 for pause in self.waits), self.waits)

    def test_the_date_alone_decides_between_the_twins(self):
        """The same chain and the same number: 11.07.2024 picks the judge's referral order instead."""
        with self.chain() as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base, expect_date=VSRF_PDF_DATE, citation=VSRF_PDF_CITATION)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{vsrf_path('2383940')}", result["url"])
        self.assertEqual(VSRF_REFERRAL_SHA256, self.records()[result["source_id"]]["raw_original_sha256"])

    def test_a_duplicate_pair_of_one_act_is_saved_once(self):
        """`2394462` and `2394482` are the same act under two ids: different bytes, one text."""
        bodies = {path: VSRF_CHAIN_BODIES[path] for path in (vsrf_path("2394482"), vsrf_path("2394462"))}
        listing = vsrf_listing(*bodies)
        with LocalServer(b"", routes=vsrf_routes(listing, bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual([], result.get("errors", []), result)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{vsrf_path('2394482')}", result["url"], "the first of the pair in page order")
        self.assertEqual(1, len(self.records()))

    def test_two_different_acts_with_the_same_requisites_are_ambiguous(self):
        """The model twin: the same number, the same date, another document — nothing is written."""
        bodies = {
            vsrf_path("2394482"): vsrf_act("2394482"),
            vsrf_path("2394482", "stor_pdf"): VSRF_TWIN,
        }
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(*bodies), bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            addresses = [f"{base}{path}" for path in bodies]
        self.assertEqual(["requisites_ambiguous: 2 distinct acts"], result["errors"])
        self.assertEqual(addresses, result["candidates"], "both shapes of the link are collected")
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files(), "nothing at all is written")

    # --- nothing won ---------------------------------------------------------

    def test_no_candidate_carrying_both_requisites_is_a_mismatch(self):
        with self.chain() as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base, expect_date="2024-09-01")
        self.assertEqual(["requisites_mismatch: date"], result["errors"])
        self.assertEqual({"number": True, "date": False}, result["found"])
        self.assertEqual(8, len(result["candidates"]))
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_each_requisite_in_a_different_act_is_a_mismatch_that_says_so(self):
        """`(1,3)` of 11.07 carries the number, `(2,4)` of 14.08 the date — no single act carries both."""
        bodies = {path: VSRF_CHAIN_BODIES[path] for path in (vsrf_path("2383940"), vsrf_path("2394388"))}
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(*bodies), bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual(["requisites_mismatch: no candidate carries both"], result["errors"])
        self.assertEqual({"number": True, "date": True}, result["found"])
        self.assertEqual(2, len(result["candidates"]))
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_chain_with_no_text_layer_anywhere_lists_its_candidates(self):
        """A candidate that cannot be certified is skipped, never refused on; the addresses remain."""
        bodies = {path: SCAN_PDF for path in (vsrf_path("2394482"), vsrf_path("2394462"))}
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(*bodies), bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            addresses = [f"{base}{path}" for path in bodies]
        self.assertEqual(["requisites_mismatch: number, date"], result["errors"])
        self.assertEqual(addresses, result["candidates"], "the researcher can pass one of these to --url")
        self.assertEqual({"number": False, "date": False}, result["found"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_genuine_scan_among_the_candidates_is_skipped_and_the_resolution_completes(self):
        """A PDF that arrived whole with no text layer is a scan, not a challenge: skipped, not stopped."""
        bodies = {vsrf_path("2394370"): SCAN_PDF, vsrf_path("2394482"): vsrf_act("2394482")}
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(*bodies), bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{vsrf_path('2394482')}", result["url"])
        self.assertEqual(
            [vsrf_path("2394370"), vsrf_path("2394482"), vsrf_path("2394482")],
            self.paths()[1:],
            "the scan was fetched, the next candidate too, and then the save",
        )

    # --- a challenge stops the channel: never solved, never worked around, never retried --------

    def test_a_challenge_served_as_the_listing_stops_the_channel(self):
        """Only the text-ratio rule is set aside for the listing; the size, redirect and wall rules are not.

        Each page carries a planted act link, so a resolver that read past the challenge would have
        fetched it: the server recording a single request is what proves it did not.
        """
        act = vsrf_path("2394482")
        tiny = b'<html><body><a href="/lk/practice/stor_pdf_ec/2394482">x</a></body></html>'
        cases = (
            ("wall", with_act_link(ECFR_STUB, act), "access_stub"),
            ("redirect", with_act_link(REDIRECT_SHELL, act), "interstitial"),
            ("shell", tiny, "interstitial"),
        )
        for name, page, reason in cases:
            with self.subTest(listing=name):
                routes = {sources.VSRF_LISTING_PATH: (page, "text/html"), act: (vsrf_act("2394482"), "application/pdf")}
                with LocalServer(b"", routes=routes) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual(1, len(_Handler.seen), "no candidate is fetched behind a challenge")
                self.assertEqual([f"channel_unavailable: {reason}"], result["errors"])
                self.assertEqual([], result["candidates"])
                self.assertEqual({}, self.records())
                self.assertEqual([], self.raw_files())

    def test_a_challenge_in_place_of_the_first_candidate_stops_the_channel_at_once(self):
        """The next fetch would be the retry the rule forbids: nothing after the challenge is asked.

        The reason printed is `not_a_pdf`, not `access_stub`: for a candidate the signature alone
        judges the body, in both directions, and a wall carries none.
        """
        paths = (vsrf_path("2394482"), vsrf_path("2394462"))
        routes = vsrf_routes(vsrf_listing(*paths), {path: VSRF_CHAIN_BODIES[path] for path in paths})
        routes[paths[0]] = (ECFR_STUB, "text/html")
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual([paths[0]], self.paths()[1:], "no request for any later candidate")
        self.assertEqual(["channel_unavailable: not_a_pdf"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_candidate_that_is_not_a_pdf_stops_the_channel_at_once(self):
        """A challenge served as 200 is not a scan: `stor_pdf_ec` is a PDF store, and this is not a PDF.

        The body here is an ordinary document page that passes every interstitial rule, so only
        «not a PDF at all» can stop it — and it does, even after a candidate that had certified.
        """
        paths = (vsrf_path("2394482"), vsrf_path("2394388"), vsrf_path("2394462"))
        routes = vsrf_routes(vsrf_listing(*paths), {path: VSRF_CHAIN_BODIES[path] for path in paths})
        routes[paths[1]] = (VS_ACT_PAGE, "text/html; charset=utf-8")
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual(list(paths[:2]), self.paths()[1:], "the third candidate is never asked")
        self.assertEqual(["channel_unavailable: not_a_pdf"], result["errors"])
        self.assertEqual({}, self.records(), "no partial answer from the candidate that did certify")
        self.assertEqual([], self.raw_files())

    def test_a_challenge_that_declares_application_pdf_stops_the_channel_at_once(self):
        """The header is not trusted for this question: a PDF is a body with the `%PDF-` signature.

        Declared `application/pdf`, the wall is not markup to `is_interstitial`, so no transport rule
        sees it; `pypdf` would find no text layer in it and it used to be skipped as a scan — and the
        next candidate fetched, which is the retry.
        """
        paths = (vsrf_path("2394482"), vsrf_path("2394462"))
        routes = vsrf_routes(vsrf_listing(*paths), {path: VSRF_CHAIN_BODIES[path] for path in paths})
        routes[paths[0]] = (ECFR_STUB, "application/pdf")
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual([paths[0]], self.paths()[1:], "no request for any later candidate")
        self.assertEqual(["channel_unavailable: not_a_pdf"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_the_pdf_signature_may_stand_anywhere_in_the_first_kilobyte(self):
        """The PDF specification allows leading junk before the header, and real servers emit it."""
        # The refusal first: it writes nothing, so the save of the second case starts from a clean tree.
        cases = (
            (b" " * 1024, ["2394370"], "channel_unavailable: not_a_pdf"),
            (b" " * 1000, ["2394370", "2394482", "2394482"], None),
        )
        for junk, asked, refusal in cases:
            with self.subTest(leading=len(junk)):
                bodies = {vsrf_path("2394370"): junk + SCAN_PDF, vsrf_path("2394482"): vsrf_act("2394482")}
                with LocalServer(b"", routes=vsrf_routes(vsrf_listing(*bodies), bodies)) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual([vsrf_path(act) for act in asked], self.paths()[1:])
                if refusal is None:
                    self.assertEqual("full_text", result["save_outcome"], "a scan within the window is skipped")
                else:
                    self.assertEqual([refusal], result["errors"])
                    self.assertEqual([], self.raw_files())

    def test_a_signed_scan_served_as_text_html_is_still_a_scan(self):
        """The signature decides in both directions: a signed body is a PDF whatever its header says.

        Served as `text/html`, the 632-byte scan is markup under the 2 KB floor to `is_interstitial`,
        which calls it `interstitial_suspected`; it is a PDF all the same, so it is skipped as the
        textless scan it is and the resolution completes.
        """
        self.assertLess(len(SCAN_PDF), limits.LIVENESS_MIN_BODY_BYTES)
        paths = (vsrf_path("2394370"), vsrf_path("2394482"))
        routes = vsrf_routes(vsrf_listing(*paths), {paths[1]: vsrf_act("2394482")})
        routes[paths[0]] = (SCAN_PDF, "text/html")
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual([paths[0], paths[1], paths[1]], self.paths()[1:], "the scan, the act, the save")
        self.assertEqual([], result.get("errors", []), result)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{paths[1]}", result["url"])

    def test_a_save_whose_own_fetch_is_not_the_certified_act_refuses_and_writes_nothing(self):
        """Task 7 finding 1: the resolver proved the act carries both requisites, so the save's own
        fetch must find both too, or refuse.

        Before, a second body that `source_text` did not read as a Russian act fell under the
        non-Russian rule and was stored as an excerpt: an HTML page served as 200 in place of the
        PDF (here the real sudact section page — any page that is not the act), or a PDF with no
        text layer (stored as `excerpt:pdf_text_unavailable`). The resolved save now refuses both.
        """
        act = vsrf_path("2394482")
        cases = (
            ("a page that is not the act", (SUDACT_SECTION_PAGE, "text/html; charset=utf-8")),
            ("a pdf with no text layer", (SCAN_PDF, "application/pdf")),
        )
        for name, second in cases:
            with self.subTest(second=name):
                routes = vsrf_routes(vsrf_listing(act), {})
                routes[act] = [(vsrf_act("2394482"), "application/pdf"), second]
                with LocalServer(b"", routes=routes) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual([act, act], self.paths()[1:], "certified once, fetched again by the save")
                self.assertEqual(["requisites_mismatch: number, date"], result["errors"])
                self.assertEqual("refused:requisites_mismatch", result["save_outcome"])
                self.assertEqual({"number": False, "date": False}, {k: result["found"][k] for k in ("number", "date")})
                self.assertEqual({}, self.records())
                self.assertEqual([], self.raw_files())

    def test_a_failed_second_fetch_leaves_an_existing_record_untouched(self):
        """Task 7: on the resolved path **no** refusal of the save writes anything, not even `meta.save_outcome`.

        An earlier resolve saved the chamber's ruling whole; a later resolve whose own second fetch
        comes back as something else — a page that is not the act, or an access wall — must not
        rewrite that record's history. The same rule for every refusal, not a list of codes.
        """
        act = vsrf_path("2394482")
        layer_dir = self.work_dir / sources.RAW_DIR / "case_law"
        cases = (
            ("a page that is not the act", (SUDACT_SECTION_PAGE, "text/html; charset=utf-8"), "requisites_mismatch"),
            ("an access wall", (ECFR_STUB, "text/html"), "access_stub"),
        )
        for name, second_body, reason in cases:
            with self.subTest(second=name):
                routes = vsrf_routes(vsrf_listing(act), {})
                routes[act] = [(vsrf_act("2394482"), "application/pdf")] * 3 + [second_body]
                with LocalServer(b"", routes=routes) as base:
                    self.allow(sources.url_host(base))
                    first = self.resolve(base)
                    self.assertEqual("full_text", first["save_outcome"])
                    record_before = self.snapshot(first["source_id"])
                    files_before = {name: (layer_dir / name).read_bytes() for name in self.raw_files()}
                    second = self.resolve(base)
                self.assertEqual(reason, second["errors"][0].split(":")[0])
                self.assertEqual(f"refused:{reason}", second["save_outcome"], "the answer still carries the outcome")
                self.assertEqual(record_before, self.snapshot(first["source_id"]), "record and meta untouched")
                self.assertEqual(files_before, {name: (layer_dir / name).read_bytes() for name in self.raw_files()})

    def test_a_candidate_that_is_not_served_stops_the_channel_at_once(self):
        """A partial chain is no chain: the act that did not arrive may be the one looked for."""
        missing = vsrf_path("9999999")
        bodies = {vsrf_path("2394482"): vsrf_act("2394482")}
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(missing, *bodies), bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual([missing], self.paths()[1:])
        self.assertEqual(["channel_unavailable: http_404"], result["errors"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_number_that_lists_nothing_is_a_mismatch_and_not_a_broken_channel(self):
        """The two send the researcher to different places: look at the number, or use a fallback."""
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(), {})) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual(["requisites_mismatch: number, date"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertEqual({"number": False, "date": False}, result["found"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_channel_that_does_not_answer_with_a_listing_is_unavailable(self):
        """Only a transport failure is `channel_unavailable`, and the answer says which one."""
        with LocalServer(b"", routes={}) as base:
            self.allow(sources.url_host(base))
            not_served = self.resolve(base)
        self.assertEqual(["channel_unavailable: http_404"], not_served["errors"])
        self.assertEqual([], not_served["candidates"])
        with self.chain() as base:
            # D-151: the allowlist is asked before the address is called, here as everywhere.
            self.allow("vsrf.ru")
            refused = self.resolve(base)
            self.assertEqual([], _Handler.seen, "an address off the allowlist is never requested")
        self.assertEqual(["channel_unavailable: host_not_allowed"], refused["errors"])
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_listing_cut_at_the_ceiling_is_an_unavailable_channel(self):
        """A chain cut short may be missing exactly the act that was looked for."""
        original = limits.LIVENESS_MAX_BODY_BYTES
        limits.LIVENESS_MAX_BODY_BYTES = 4096
        self.addCleanup(setattr, limits, "LIVENESS_MAX_BODY_BYTES", original)
        with self.chain() as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual(["channel_unavailable: truncated"], result["errors"])
        self.assertEqual(1, len(_Handler.seen), "no candidate is fetched from a partial chain")
        self.assertEqual({}, self.records())
        self.assertEqual([], self.raw_files())

    def test_a_frozen_registry_is_found_out_before_the_portal_is_troubled(self):
        with self.chain() as base:
            self.allow(sources.url_host(base))
            self.register(layer="case_law", title=VSRF_ACT_TITLE, citation=VSRF_ACT_CITATION, url=f"{base}/ok")
            self.write_findings([{"source_id": next(iter(self.records()))}], layer="case_law")
            self.freeze()
            _Handler.seen = []
            result = self.resolve(base)
            self.assertEqual([], _Handler.seen, "the freeze is checked before the first request")
        self.assertEqual(["sources_frozen"], result["errors"])
        self.assertEqual([], self.raw_files())

    # --- the cap and the flags ----------------------------------------------

    def test_only_ten_candidates_are_ever_fetched(self):
        acts = [f"90000{index:02d}" for index in range(12)]
        bodies = {vsrf_path(act): SCAN_PDF for act in acts}
        with LocalServer(b"", routes=vsrf_routes(vsrf_listing(*bodies), bodies)) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual(10, limits.RESOLVE_MAX_CANDIDATES)
        self.assertEqual(11, len(_Handler.seen), "the listing and ten of its twelve links")
        self.assertEqual([vsrf_path(act) for act in acts[:10]], self.paths()[1:])
        self.assertEqual(10, len(result["candidates"]))

    def test_both_requisites_are_required_before_any_request_is_made(self):
        cases = (({"expect_date": None}, "--expect-date"), ({"expect_number": None}, "--expect-number"))
        with self.chain() as base:
            self.allow(sources.url_host(base))
            for overrides, flag in cases:
                with self.subTest(missing=flag):
                    _Handler.seen = []
                    result = self.resolve(base, **overrides)
                    self.assertEqual([f"requisites_required: {flag}"], result["errors"])
                    self.assertEqual([], _Handler.seen, "refused before anything left the process")
                    self.assertEqual({}, self.records())
                    self.assertEqual([], self.raw_files())

    def test_a_resolver_and_a_url_are_mutually_exclusive(self):
        """Declared with the command in Task 4; the resolver never guesses beside a given address."""
        parser = cli.build_parser()
        base = ["sources", "save", "--workdir", ".", "--layer", "case_law", "--title", "t", "--citation", "c"]
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(base + ["--url", "https://www.vsrf.ru/x", "--resolve", "vsrf"])
        self.assertEqual("vsrf", parser.parse_args(base + ["--resolve", "vsrf"]).resolve)


class _FakeClockTestCase(SaveTestCase):
    """A `SaveTestCase` whose channel clock is a `FakeClock`, and whose waits advance it (D-202)."""

    def setUp(self) -> None:
        super().setUp()
        self.clock = FakeClock()
        for name, value in (("_channel_clock", self.clock.time), ("_wait", self.clock.wait)):
            patcher = mock.patch.object(sources, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def state(self) -> dict:
        return json.loads(sources.channel_state_path(self.work_dir).read_text(encoding="utf-8"))

    def write_state(self, payload) -> None:
        document = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        sources.channel_state_path(self.work_dir).write_bytes(document)

    def write_entry(self, last_request: float, requests: int, captcha: bool) -> None:
        """A well-formed `channels.json` holding this state of the sudact channel."""
        entry = {"last_request": last_request, "requests": requests, "captcha": captcha}
        self.write_state({"schema_version": 1, "channels": {"sudact": entry}})

    def counts(self) -> dict:
        """The requests counted and the marker, without the time of the last slot."""
        entry = self.state()["channels"]["sudact"]
        return {"requests": entry["requests"], "captcha": entry["captcha"]}


class ChannelStateTest(_FakeClockTestCase):
    """D-202: `channels.json` — the pace, the budget and the captcha marker every researcher shares.

    It is read and updated briefly under `sources.lock`: the next slot is reserved by writing its
    time, and the waiting happens outside the lock, because three researcher processes must never
    queue on a held lock. No test here sleeps and none reads a real clock.
    """

    def test_the_file_lives_beside_research_and_outside_its_globs(self):
        path = sources.channel_state_path(self.work_dir)
        self.assertEqual(Path(self.work_dir) / "channels.json", path)
        self.assertFalse(path.match("research/*.json"))

    def test_the_freeze_never_stages_channels_json(self):
        """The pace of a portal is no input of the memo: the freeze stages `research/*.json` only."""
        sources.channel_slot(self.work_dir, "sudact")
        self.assertTrue(sources.channel_state_path(self.work_dir).is_file())
        self.register()
        self.write_findings([{"source_id": "gdpr-article-6"}])
        result = self.freeze()
        self.assertNotIn("errors", result)
        inputs = self.work_dir / "steps" / "s-010" / "a1" / "cli" / "inputs"
        staged = sorted(path.name for path in inputs.iterdir())
        self.assertIn("sources.json", staged, "the freeze did stage its inputs")
        self.assertIn("statutes.json", staged)
        self.assertNotIn("channels.json", staged)

    def test_the_first_slot_is_now_and_every_next_one_two_seconds_later(self):
        start = self.clock.now
        for _ in range(3):
            sources.channel_slot(self.work_dir, "sudact")
        self.assertEqual(2, limits.CHANNEL_MIN_INTERVAL_S)
        self.assertEqual([2.0, 2.0], self.clock.waits)
        self.assertEqual(
            {"schema_version": 1, "channels": {"sudact": {"last_request": start + 4, "requests": 3, "captcha": False}}},
            self.state(),
        )

    def test_the_slot_is_reserved_under_the_lock_and_waited_for_outside_it(self):
        """By the time a process waits, its slot is in the file and the lock is free for the others."""
        observed: list = []

        def wait(seconds: float) -> None:
            reserved = self.state()["channels"]["sudact"]["last_request"]
            observed.append((seconds, sources_lock_is_free(self.work_dir), reserved))
            self.clock.wait(seconds)

        with sources.sources_lock(self.work_dir):
            self.assertFalse(sources_lock_is_free(self.work_dir), "the probe does see a held lock")
        start = self.clock.now
        with mock.patch.object(sources, "_wait", wait):
            sources.channel_slot(self.work_dir, "sudact")
            sources.channel_slot(self.work_dir, "sudact")
        self.assertEqual([(2, True, start + 2)], observed)

    def test_three_processes_never_reserve_the_same_slot(self):
        """Three real processes on one frozen clock: only the reservation under the lock keeps them apart."""
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(3)
        results = [str(self.root / f"slots-{index}.json") for index in range(3)]
        processes = [
            context.Process(target=worker_reserve, args=(str(self.work_dir), 5, barrier, results[index]))
            for index in range(3)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
            self.assertEqual(0, process.exitcode, "a child crashed")
        outcomes = [json.loads(Path(path).read_text(encoding="utf-8")) for path in results]
        self.assertEqual([None, None, None], [row["error"] for row in outcomes])
        slots = sorted(slot for row in outcomes for slot in row["slots"])
        self.assertEqual([1000.0 + 2 * index for index in range(15)], slots, "fifteen slots, none taken twice")
        self.assertEqual({"last_request": 1028.0, "requests": 15, "captcha": False}, self.state()["channels"]["sudact"])

    def test_a_corrupt_file_is_treated_as_absent_and_rewritten(self):
        """A corrupt pace file must not stop a researcher from working: it is never raised on."""

        def document(**fields) -> dict:
            entry = {"last_request": 0, "requests": 0, "captcha": False, **fields}
            return {"schema_version": 1, "channels": {"sudact": entry}}

        cases = {
            "cut short": b'{"schema_version": 1, "chan',
            "not utf-8": b"\xff\xfe\x00\x81",
            "a list": b"[]",
            "another version": {"schema_version": 2, "channels": {}},
            "no channels": {"schema_version": 1},
            "a counter that is text": document(requests="7"),
            "a counter that is a flag": document(requests=True),
            "a negative counter": document(requests=-1),
            "a marker that is not a flag": document(captcha="yes"),
            "a time that is not a number": document(last_request="soon"),
            "a time that is not finite": b'{"schema_version": 1, "channels": {"sudact": '
            b'{"last_request": NaN, "requests": 0, "captcha": false}}}',
        }
        for name, payload in cases.items():
            with self.subTest(file=name):
                self.write_state(payload)
                sources.channel_slot(self.work_dir, "sudact")
                self.assertEqual([], self.clock.waits, "nothing is owed to a file that could not be read")
                fresh = {"last_request": self.clock.now, "requests": 1, "captcha": False}
                self.assertEqual({"schema_version": 1, "channels": {"sudact": fresh}}, self.state())

    def test_a_slot_no_run_could_have_reserved_is_refused_never_sent_never_waited(self):
        """Round 2, item 4: a reservation further ahead than a whole budget queued at once
        (`CHANNEL_MAX_REQUESTS_PER_RUN × CHANNEL_MIN_INTERVAL_S`) is not a state this code could have
        written honestly — a clock stepped back, or an edited file. It is neither permission to send
        (the old reset) nor a reason to wait until then (a hang): `channel_unavailable: clock`, no
        marker, the file untouched. A slot within the horizon is still honoured to the second.
        """
        horizon = limits.CHANNEL_MAX_REQUESTS_PER_RUN * limits.CHANNEL_MIN_INTERVAL_S
        now = self.clock.now
        self.write_entry(now + 3600, 5, False)
        with self.assertRaises(sources.ChannelUnavailable) as caught:
            sources.channel_slot(self.work_dir, "sudact")
        self.assertEqual("clock", str(caught.exception))
        self.assertEqual([], self.clock.waits)
        untouched = {"last_request": now + 3600, "requests": 5, "captcha": False}
        self.assertEqual(untouched, self.state()["channels"]["sudact"], "nothing counted, no marker")
        self.write_entry(now + horizon - limits.CHANNEL_MIN_INTERVAL_S, 5, False)
        sources.channel_slot(self.work_dir, "sudact")
        self.assertEqual([horizon], self.clock.waits)

    def test_the_state_is_reread_after_the_wait_before_the_request_goes_out(self):
        """Round 2, item 1: the slot was reserved, then another process closed the channel during
        the wait — the reread after the wait refuses. The slot stays counted: it was reserved."""

        def wait(seconds: float) -> None:
            sources.channel_mark_captcha(self.work_dir, "sudact")
            self.clock.wait(seconds)

        sources.channel_slot(self.work_dir, "sudact")
        with mock.patch.object(sources, "_wait", wait):
            with self.assertRaises(sources.ChannelUnavailable) as caught:
                sources.channel_slot(self.work_dir, "sudact")
        self.assertEqual("captcha", str(caught.exception))
        self.assertEqual("marker", caught.exception.seen)
        self.assertEqual({"requests": 2, "captcha": True}, self.counts())

    def test_the_marker_refuses_before_any_slot_is_taken(self):
        self.write_entry(0, 7, True)
        with self.assertRaises(sources.ChannelUnavailable) as caught:
            sources.channel_slot(self.work_dir, "sudact")
        self.assertEqual("captcha", str(caught.exception))
        self.assertEqual({"last_request": 0, "requests": 7, "captcha": True}, self.state()["channels"]["sudact"])


class ResolveSudactTest(_FakeClockTestCase):
    """D-202: `--resolve sudact` — the portal's ordinary search, its session cookie, its asynchronous
    answer, its captcha, and a pace every researcher process shares through `channels.json`.

    **A captcha is never solved, never worked around and never retried.** Every test runs against a
    `LocalServer` with the base address injected. The one real document is Task 3's
    `sudact-cassation-a53-28950-2022` page; the other acts of its case are labelled models of it
    (`sudact_model`). Request counts are asserted on `_Handler.seen`, what the server received.
    """

    def resolve(self, base: str, **overrides) -> dict:
        """`mf sources save --resolve sudact` against the `LocalServer` standing in for the portal."""
        payload = {
            "title": SUDACT_TITLE,
            "citation": SUDACT_CITATION,
            "expect_number": SUDACT_NUMBER,
            "expect_date": SUDACT_DATE,
            "resolve": "sudact",
        }
        payload.update(overrides)
        with mock.patch.object(sources, "SUDACT_BASE", base):
            return self.save("", **payload)

    def resolve_directly(self, base: str) -> list:
        """`sources.resolve_sudact` itself, with the signature the plan pins plus the work dir."""
        return sources.resolve_sudact(SUDACT_NUMBER, section="arbitral", base=base, timeout=5.0, work_dir=self.work_dir)

    def portal(self, search: list, documents: dict, section: str = "arbitral") -> LocalServer:
        return LocalServer(b"", routes=sudact_routes(search, documents, section))

    def paths(self) -> list:
        return [row["path"] for row in _Handler.seen]

    def channel(self) -> dict:
        return self.state()["channels"]["sudact"]

    def forget_the_channel(self) -> None:
        """A fresh run's `channels.json`, for a subtest that must not inherit the previous marker."""
        sources.channel_state_path(self.work_dir).unlink(missing_ok=True)

    def assert_nothing_written(self, result: dict) -> None:
        self.assertEqual({}, self.records(), result)
        self.assertEqual([], self.raw_files())

    # --- the section, the query ------------------------------------------------

    def test_the_section_is_decided_by_the_shape_of_the_number(self):
        """One naming rule: `А`/`A`, digits, `-`, digits, `/`, a year asks `arbitral`; anything else `regular`."""
        cases = {
            "А53-28950/2022": "arbitral",
            "A53-28950/2022": "arbitral",
            " А53-28950/2022 ": "arbitral",
            "А40-630/25-100-1": "arbitral",
            "2-1456/2025": "regular",
            "33-4567/2025": "regular",
            "5-КГ25-14-К2": "regular",
            "305-ЭС24-8702 (1,3)": "regular",
            "А53-28950": "regular",
            "А53-28950/202": "regular",
        }
        for number, section in cases.items():
            with self.subTest(number=number):
                self.assertEqual(section, sources.sudact_section(number))

    def test_each_section_is_asked_where_its_number_sends_it(self):
        for number, section in (("А53-28950/2022", "arbitral"), ("2-1456/2025", "regular")):
            with self.subTest(section=section):
                self.forget_the_channel()
                with self.portal([sudact_finished()], {}, section=section) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base, expect_number=number)
                    self.assertEqual([f"/{section}/", sudact_search_path(number, section)], self.paths())
                self.assertEqual(["requisites_mismatch: number, date"], result["errors"])

    def test_the_query_carries_the_number_the_vsrf_resolver_asks(self):
        """The two resolvers never disagree about what «the number» is: one helper, Task 6's."""
        self.assertEqual("305-ЭС24-8702", sources.resolve_query_number("305-ЭС24-8702 (1,3)"))
        for number in ("305-ЭС24-8702 (1,3)", "А53-28950/2022", "(1,3)"):
            with self.subTest(number=number):
                asked = urllib.parse.quote(sources.resolve_query_number(number), safe="")
                sudact = sources.sudact_search_url(number, "regular", "https://sudact.ru")
                self.assertEqual(f"https://sudact.ru/regular/doc_ajax/?regular-case_doc={asked}&page=1", sudact)
                self.assertTrue(sources.vsrf_listing_url(number, "https://www.vsrf.ru").endswith(f"&number={asked}"))

    def test_the_real_finished_answer_lists_its_four_acts_in_page_order(self):
        """The captured `finished` answer: the links of `content`, and never the count in `total_found`."""
        search = "https://sudact.ru" + sudact_search_path()
        document = json.loads(SUDACT_FINISHED.decode("utf-8"))
        self.assertEqual("\n\n\nНайдено 4 документа", document["total_found"], "an HTML fragment, not a number")
        listed = sources.sudact_candidates(document, "arbitral", search)
        self.assertEqual(["https://sudact.ru" + path for path in SUDACT_LISTED], listed)

    def test_only_document_addresses_of_the_asked_section_in_content_are_candidates(self):
        """`/doc/save/<id>/` is a real trap — it leads to another document entirely — each act is asked
        once, and only `content` is the list: nothing else in the answer is read."""
        search = "https://sudact.ru/arbitral/doc_ajax/?arbitral-case_doc=x&page=1"
        document = {
            "status": "finished",
            "content": (
                '<a href="/arbitral/doc/save/455AVcwC6HrR/">сохранить</a>'
                '<a href="/arbitral/doc/AbC123/?snippet_pos=1#snippet">акт</a>'
                '<a href="/arbitral/doc/AbC123/">он же</a>'
                '<a href="/regular/doc/Zz9/">другой раздел</a>'
                '<a href="https://sudact.ru/arbitral/doc/Next7/">ещё акт</a>'
            ),
            "total_found": '<a href="/arbitral/doc/NotAList1/">Найдено 3 документа</a>',
        }
        self.assertEqual(
            ["https://sudact.ru/arbitral/doc/AbC123/", "https://sudact.ru/arbitral/doc/Next7/"],
            sources.sudact_candidates(document, "arbitral", search),
        )
        many = {"status": "finished", "content": "".join(f'<a href="/arbitral/doc/Act{i}/">a</a>' for i in range(12))}
        self.assertEqual(limits.RESOLVE_MAX_CANDIDATES, len(sources.sudact_candidates(many, "arbitral", search)))

    # --- the session, the search, the polls -------------------------------------

    def test_a_cookie_the_section_page_sets_is_sent_with_the_search(self):
        """Whatever the portal sets is carried to the search; the real page happened to set nothing."""
        routes = sudact_routes([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE})
        sets_cookie = {"Set-Cookie": f"{SUDACT_COOKIE}; Path=/"}
        routes["/arbitral/"] = (SUDACT_SECTION_PAGE, "text/html; charset=utf-8", sets_cookie)
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            self.resolve(base)
        first, second = _Handler.seen[0], _Handler.seen[1]
        self.assertEqual("/arbitral/", first["path"], "a plain GET of the section page comes first")
        self.assertNotIn("cookie", first["headers"])
        self.assertEqual(sudact_search_path(), second["path"])
        self.assertEqual(SUDACT_COOKIE, second["headers"].get("cookie"))
        self.assertEqual("XMLHttpRequest", second["headers"].get("x-requested-with"))
        self.assertEqual(f"{base}/arbitral/", second["headers"].get("referer"))
        self.assertEqual(sources.LIVENESS_USER_AGENT, second["headers"].get("user-agent"), "the honest agent")

    def test_a_section_page_that_sets_no_cookie_does_not_stop_the_search(self):
        """Measured: the real section page set no cookie and the search worked. A missing cookie is
        never a refusal — nobody may later «fix» it into one."""
        with self.portal([SUDACT_NEW, SUDACT_FINISHED], SUDACT_CASE) as base:
            self.allow(sources.url_host(base))
            listed = self.resolve_directly(base)
        self.assertNotIn("cookie", _Handler.seen[1]["headers"], "no cookie was set, so none is sent")
        self.assertEqual("XMLHttpRequest", _Handler.seen[1]["headers"].get("x-requested-with"))
        self.assertEqual([f"{base}{path}" for path in SUDACT_LISTED], listed)
        self.assertFalse(self.channel()["captcha"])

    def test_new_then_finished_within_the_cap_saves_the_act(self):
        search = sudact_search_path()
        with self.portal([SUDACT_NEW, SUDACT_NEW, sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            paths = self.paths()
        self.assertEqual([], result.get("errors", []), result)
        self.assertTrue(result["created"])
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{SUDACT_ACT}", result["url"])
        self.assertEqual(f"mf-save {sources.url_host(base)}", self.records()[result["source_id"]]["retrieval_tool"])
        self.assertEqual(["/arbitral/", search, search, search, SUDACT_ACT, SUDACT_ACT], paths)
        self.assertEqual([2.0] * 5, self.clock.waits, "every request after the first waited: polls and the save too")
        self.assertEqual(6, self.channel()["requests"], "every request the channel made is counted")
        self.assertFalse(self.channel()["captcha"])

    def test_a_search_that_never_finishes_is_not_resolved_and_nothing_raises(self):
        search = sudact_search_path()
        with self.portal([SUDACT_NEW], {}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            paths = self.paths()
        self.assertEqual(5, limits.SUDACT_POLL_MAX)
        self.assertEqual(["/arbitral/"] + [search] * (1 + limits.SUDACT_POLL_MAX), paths, "the search and five polls")
        self.assertEqual(["channel_unavailable: not_resolved"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertFalse(self.channel()["captcha"], "an unfinished search is not a challenge")
        self.assert_nothing_written(result)

    def test_the_search_answer_is_never_judged_by_the_markup_rules(self):
        """JSON declared `text/html` and far under the 2 KB floor is still the search's own answer."""
        self.assertLess(len(SUDACT_NEW), limits.LIVENESS_MIN_BODY_BYTES)
        answers = [(SUDACT_NEW, "text/html"), (sudact_finished(SUDACT_ACT), "text/html")]
        with self.portal(answers, {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual([], result.get("errors", []), result)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertFalse(self.channel()["captcha"])

    # --- a challenge: never solved, never worked around, never retried ---------

    def test_a_defence_redirect_closes_the_channel_for_the_whole_run(self):
        search = sudact_search_path()
        with self.portal([SUDACT_DEFENCE], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual(["/arbitral/", search], self.paths(), "the captcha page itself is never requested")
            _Handler.seen = []
            with self.assertRaises(sources.ChannelUnavailable) as caught:
                self.resolve_directly(base)
            self.assertEqual([], _Handler.seen, "a second resolve_sudact of the run refuses without a request")
        self.assertEqual("captcha", str(caught.exception))
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("defence_redirect", result["challenge"])
        self.assertEqual([], result["candidates"], "no address behind a blocked channel")
        self.assertEqual({"requests": 2, "captcha": True}, self.counts())
        self.assert_nothing_written(result)

    def test_a_challenge_on_any_request_stops_the_channel_and_marks_it(self):
        """The section page, the search, a poll, a document: nothing after the challenge is asked,
        in this call or in any later call of the run."""
        search = sudact_search_path()
        other = SUDACT_LISTED[1]
        slow_down =(b"<html><body>slow down</body></html>", "text/html", {}, 429)
        listed = [sudact_finished(other, SUDACT_ACT)]
        wall = (ECFR_STUB, "text/html")
        cases = (
            ("the section page", {"/arbitral/": SUDACT_DEFENCE}, [SUDACT_NEW], ["/arbitral/"], "defence_redirect"),
            ("the search", {}, [wall], ["/arbitral/", search], "not_json"),
            ("a poll", {}, [SUDACT_NEW, slow_down], ["/arbitral/", search, search], "http_429"),
            ("a document", {other: SUDACT_DEFENCE}, listed, ["/arbitral/", search, other], "defence_redirect"),
            ("a document page", {other: wall}, listed, ["/arbitral/", search, other], "access_stub"),
        )
        for name, overrides, answers, asked, seen in cases:
            with self.subTest(request=name):
                self.forget_the_channel()
                routes = sudact_routes(answers, {SUDACT_ACT: SUDACT_ACT_PAGE})
                routes.update(overrides)
                with LocalServer(b"", routes=routes) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual(asked, self.paths(), "nothing is asked after the challenge")
                    _Handler.seen = []
                    again = self.resolve(base)
                    self.assertEqual([], _Handler.seen, "nor in any later call of the run")
                self.assertEqual(["channel_unavailable: captcha"], result["errors"])
                self.assertEqual(seen, result["challenge"])
                self.assertEqual([], result["candidates"])
                self.assertEqual(["channel_unavailable: captcha"], again["errors"])
                self.assertTrue(self.channel()["captcha"])
                self.assert_nothing_written(result)

    def test_a_redirect_the_channel_will_not_follow_is_a_challenge(self):
        """A hop off the allowlist is refused before it is requested — and the portal that sent it is closed."""
        with self.portal([SUDACT_NEW], {}) as base:
            offsite = f"http://localhost:{urllib.parse.urlsplit(base).port}/elsewhere"
            _Handler.routes["/arbitral/doc_ajax/"] = (b"", "", {"Location": offsite}, 302)
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual(["/arbitral/", sudact_search_path()], self.paths(), "the hop itself is never requested")
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("redirect_not_allowed: localhost", result["challenge"])
        self.assertTrue(self.channel()["captcha"])
        self.assert_nothing_written(result)

    def test_a_challenge_on_the_saves_own_fetch_marks_the_channel_too(self):
        """The save fetches the chosen act once more — a request of the channel like any other.

        A redirect to the captcha is refused before it is followed, so the save never requests the
        captcha page; a page that is not the act (the real section page) is refused as
        `requisites_mismatch` — never stored as an excerpt — and, being no court act at all, closes
        the channel as `not_a_document` does on the resolver's own fetch.
        """
        throttle = (b"<html>slow down</html>", "text/html", {}, 429)
        not_the_act = (SUDACT_SECTION_PAGE, "text/html; charset=utf-8")
        cases = (
            ("a wall", (ECFR_STUB, "text/html"), "access_stub", "refused:access_stub"),
            ("a throttle", throttle, "unchecked: http_429", "refused:unchecked"),
            ("a redirect to the captcha", SUDACT_DEFENCE, "channel_unavailable: captcha", "refused:captcha"),
            ("not the act", not_the_act, "requisites_mismatch: number, date", "refused:requisites_mismatch"),
        )
        for name, second, error, outcome in cases:
            with self.subTest(answer=name):
                self.forget_the_channel()
                document = [(SUDACT_ACT_PAGE, "text/html; charset=utf-8"), second]
                with self.portal([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: document}) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual(["/arbitral/", sudact_search_path(), SUDACT_ACT, SUDACT_ACT], self.paths())
                    _Handler.seen = []
                    again = self.resolve(base)
                    self.assertEqual([], _Handler.seen, "the next call of the run asks nothing")
                self.assertEqual([error], result["errors"])
                self.assertEqual(outcome, result["save_outcome"])
                self.assertEqual({"requests": 4, "captcha": True}, self.counts())
                self.assertEqual(["channel_unavailable: captcha"], again["errors"])
                self.assert_nothing_written(result)

    def test_a_search_answer_that_is_not_json_with_a_status_is_a_challenge_not_no_results(self):
        no_list = json.dumps({"status": "finished", "total_found": "Найдено 4 документа"}).encode("utf-8")
        no_status = json.dumps({"content": "<ul></ul>"}).encode("utf-8")
        cases = {
            "an html page": ((SUDACT_SECTION_PAGE, "text/html; charset=utf-8"), "not_json"),
            "a body that does not parse": ((b'{"status": "fini', "application/json"), "not_json"),
            "json without a status": ((no_status, "application/json"), "not_json"),
            "a json list": ((b"[]", "application/json"), "not_json"),
            "finished without its list": ((no_list, "application/json"), "no_content"),
        }
        for name, (answer, seen) in cases.items():
            with self.subTest(answer=name):
                self.forget_the_channel()
                with self.portal([answer], {}) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual(["/arbitral/", sudact_search_path()], self.paths())
                self.assertEqual(["channel_unavailable: captcha"], result["errors"])
                self.assertEqual(seen, result["challenge"])
                self.assertTrue(self.channel()["captcha"])
                self.assert_nothing_written(result)

    def test_a_document_page_that_is_not_a_court_act_is_a_challenge(self):
        """`/<section>/doc/<id>/` serves court acts; anything else in its place is not what it serves."""
        other = "/arbitral/doc/Statute1/"
        documents = {other: STATUTE_PAGE, SUDACT_ACT: SUDACT_ACT_PAGE}
        with self.portal([sudact_finished(other, SUDACT_ACT)], documents) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual(["/arbitral/", sudact_search_path(), other], self.paths(), "the next act is never asked")
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("not_a_document", result["challenge"])
        self.assertTrue(self.channel()["captcha"])
        self.assert_nothing_written(result)

    def test_an_address_off_the_allowlist_is_never_asked_and_costs_nothing(self):
        """D-151 as everywhere: the allowlist is asked first, and a refused address takes no slot."""
        with self.portal([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow("sudact.ru")
            result = self.resolve(base)
            self.assertEqual([], _Handler.seen)
        self.assertEqual(["channel_unavailable: host_not_allowed"], result["errors"])
        self.assertFalse(sources.channel_state_path(self.work_dir).exists(), "no slot was reserved for it")
        self.assert_nothing_written(result)

    def test_a_document_that_is_not_served_stops_the_call_without_a_marker(self):
        """A transport failure is not a challenge: this call stops, the run's channel stays open."""
        missing = "/arbitral/doc/Missing1/"
        with self.portal([sudact_finished(missing, SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual(["/arbitral/", sudact_search_path(), missing], self.paths(), "the next act is never asked")
        self.assertEqual(["channel_unavailable: http_404"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertFalse(self.channel()["captcha"])
        self.assert_nothing_written(result)

    # --- choosing the act ---------------------------------------------------------

    def test_four_acts_of_one_case_resolve_to_the_one_carrying_both_requisites(self):
        """End to end on the real answers: the captured section page, `new` and `finished` — four acts
        in page order, each read, and only the one carrying both requisites saved."""
        search = sudact_search_path()
        with self.portal([SUDACT_NEW, SUDACT_FINISHED], SUDACT_CASE) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            paths = self.paths()
        self.assertEqual([], result.get("errors", []), result)
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{SUDACT_ACT}", result["url"])
        records = self.records()
        self.assertEqual([result["source_id"]], list(records))
        self.assertIn("2 7 о к т я б р я 2025 года", self.stored(records[result["source_id"]]).decode("utf-8"))
        self.assertEqual(["/arbitral/", search, search, *SUDACT_LISTED, SUDACT_ACT], paths, "four read, one saved")
        self.assertEqual({"requests": len(paths), "captcha": False}, self.counts())

    def test_the_date_in_the_text_decides_between_the_acts_of_one_case(self):
        with self.portal([SUDACT_FINISHED], SUDACT_CASE) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(
                base,
                expect_date="2025-06-19",
                citation="Постановление по делу № А53-28950/2022 от 19.06.2025",
            )
        self.assertEqual("full_text", result["save_outcome"])
        self.assertEqual(f"{base}{SUDACT_LISTED[1]}", result["url"])
        self.assertEqual(1, len(self.records()))

    def test_the_listing_date_a_day_early_never_satisfies_the_check(self):
        """sudact's metadata says 26 October — the listing and the portal heading both — and the act is of the 27th."""
        listing = sudact_finished(SUDACT_ACT)
        self.assertIn("от 26 октября 2025 г.", listing.decode("utf-8"))
        self.assertIn("от 26 октября 2025 г.", SUDACT_ACT_PAGE.decode("utf-8"))
        with self.portal([listing], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base, expect_date=SUDACT_PORTAL_DATE)
        self.assertEqual(["requisites_mismatch: date"], result["errors"])
        self.assertEqual({"number": True, "date": False}, result["found"])
        self.assertEqual([f"{base}{SUDACT_ACT}"], result["candidates"])
        self.assert_nothing_written(result)

    def test_a_finished_search_that_names_no_document_is_a_mismatch_not_a_broken_channel(self):
        with self.portal([sudact_finished()], {}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
        self.assertEqual(["requisites_mismatch: number, date"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertEqual({"number": False, "date": False}, result["found"])
        self.assertFalse(self.channel()["captcha"])
        self.assert_nothing_written(result)

    def test_two_different_acts_carrying_both_requisites_are_ambiguous(self):
        """A labelled model: the real page with one more portal line — the same requisites, another text."""
        twin = "/arbitral/doc/ModelTwin1/"
        other_text = portal_page(SUDACT_ACT_TEXT + "\nОпубликовано на портале повторно.\n")
        documents = {SUDACT_ACT: SUDACT_ACT_PAGE, twin: other_text}
        with self.portal([sudact_finished(*documents)], documents) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            addresses = [f"{base}{path}" for path in documents]
        self.assertEqual(["requisites_ambiguous: 2 distinct acts"], result["errors"])
        self.assertEqual(addresses, result["candidates"])
        self.assert_nothing_written(result)

    # --- the pace, the budget, the flags -----------------------------------------

    def test_the_pace_is_kept_across_two_calls_that_share_the_file(self):
        with self.portal([sudact_finished()], {}) as base:
            self.allow(sources.url_host(base))
            start = self.clock.now
            first = self.resolve_directly(base)
            self.assertEqual(start + 2, self.channel()["last_request"], "the first call left its last slot in the file")
            self.clock.now += 0.5  # the next call starts half a second after that request
            second = self.resolve_directly(base)
        self.assertEqual(([], []), (first, second))
        self.assertEqual(4, len(_Handler.seen))
        self.assertEqual([2.0, 1.5, 2.0], self.clock.waits)
        self.assertEqual({"last_request": start + 6, "requests": 4, "captcha": False}, self.channel())

    # --- round 2: shutdown during a wait, followed hops, short bodies, the clock, a good record ---

    def test_a_marker_set_during_a_wait_stops_the_request_that_waited(self):
        """Round 2, item 1: process A reserved its slot and waits outside the lock; process B meets a
        challenge meanwhile. A rereads the state after the wait, right before dispatch, and sends nothing."""
        closed_by_another_process = []

        def wait(seconds: float) -> None:
            if not closed_by_another_process:
                sources.channel_mark_captcha(self.work_dir, "sudact")
                closed_by_another_process.append(seconds)
            self.clock.wait(seconds)

        with self.portal([SUDACT_NEW, SUDACT_FINISHED], SUDACT_CASE) as base:
            self.allow(sources.url_host(base))
            with mock.patch.object(sources, "_wait", wait):
                result = self.resolve(base)
            self.assertEqual(["/arbitral/"], self.paths(), "the search that waited through the shutdown is never sent")
        self.assertEqual([2.0], closed_by_another_process)
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("marker", result["challenge"])
        self.assert_nothing_written(result)

    def test_a_followed_redirect_is_a_request_of_the_channel(self):
        """Round 2, item 2: a hop urllib follows costs its own slot, its own wait and its own count."""
        routes = sudact_routes([sudact_finished()], {})
        routes["/arbitral/"] = (b"", "", {"Location": "/landing/"}, 302)
        routes["/landing/"] = (SUDACT_SECTION_PAGE, "text/html; charset=utf-8")
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            listed = self.resolve_directly(base)
            paths = self.paths()
        self.assertEqual([], listed)
        self.assertEqual(["/arbitral/", "/landing/", sudact_search_path()], paths)
        self.assertEqual([2.0, 2.0], self.clock.waits, "the hop waited its interval like any request")
        self.assertEqual({"requests": 3, "captcha": False}, self.counts(), "and was counted")

    def test_a_redirect_reached_on_the_last_slot_is_not_followed(self):
        """Round 2, item 2, the case the gate reproduced: 59 requests spent, the section page answers
        302 — the 60th request goes out, the 61st is never sent."""
        self.write_entry(0, limits.CHANNEL_MAX_REQUESTS_PER_RUN - 1, False)
        routes = sudact_routes([sudact_finished()], {})
        routes["/arbitral/"] = (b"", "", {"Location": "/landing/"}, 302)
        routes["/landing/"] = (SUDACT_SECTION_PAGE, "text/html; charset=utf-8")
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual(["/arbitral/"], self.paths(), "the hop past the budget is not followed")
        self.assertEqual(["channel_budget_spent"], result["errors"])
        self.assertEqual({"requests": limits.CHANNEL_MAX_REQUESTS_PER_RUN, "captcha": False}, self.counts())
        self.assert_nothing_written(result)

    def test_a_page_cut_short_is_truncated_and_never_a_captcha(self):
        """Round 2, item 3: a body shorter than its `Content-Length` proves nothing about a challenge.

        Its first 1 500 bytes are under the 2 KB floor, so a rule that judged the body before the
        truncation marked the host closed for the whole run over a network hiccup.
        """
        html = "text/html; charset=utf-8"
        short_page = (SUDACT_SECTION_PAGE[:1500], html, {"Content-Length": str(len(SUDACT_SECTION_PAGE))})
        short_act = (SUDACT_ACT_PAGE[:1500], html, {"Content-Length": str(len(SUDACT_ACT_PAGE))})
        cases = (
            ("the section page", {"/arbitral/": short_page}, ["/arbitral/"]),
            ("a document page", {SUDACT_ACT: short_act}, ["/arbitral/", sudact_search_path(), SUDACT_ACT]),
        )
        for name, overrides, asked in cases:
            with self.subTest(page=name):
                self.forget_the_channel()
                routes = sudact_routes([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE})
                routes.update(overrides)
                with LocalServer(b"", routes=routes) as base:
                    self.allow(sources.url_host(base))
                    result = self.resolve(base)
                    self.assertEqual(asked, self.paths(), "the next request is never sent")
                self.assertEqual(["channel_unavailable: truncated"], result["errors"])
                self.assertFalse(self.channel()["captcha"], "no marker: a short body is not a challenge")
                self.assert_nothing_written(result)

    def test_an_impossible_reservation_refuses_without_a_request(self):
        """Round 2, item 4: a slot no run could have reserved is `channel_unavailable: clock`."""
        self.write_entry(self.clock.now + 3600, 0, False)
        with self.portal([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            result = self.resolve(base)
            self.assertEqual([], _Handler.seen, "never permission to send")
        self.assertEqual(["channel_unavailable: clock"], result["errors"])
        self.assertEqual([], self.clock.waits, "never an unbounded wait")
        self.assertEqual({"requests": 0, "captcha": False}, self.counts(), "no marker, nothing counted")
        self.assert_nothing_written(result)

    def test_a_failed_second_fetch_leaves_an_existing_record_untouched(self):
        """On the resolved path **no** refusal of the save writes anything, not even `meta.save_outcome`.

        The record holds a good `full_text` saved by an earlier resolve; a later resolve whose own
        second fetch comes back as something else — a page that is not the act, or a redirect into
        the captcha — must not rewrite that record's history. One rule, not a list of codes.
        """
        html = "text/html; charset=utf-8"
        layer_dir = self.work_dir / sources.RAW_DIR / "case_law"
        cases = (
            ("a page that is not the act", (SUDACT_SECTION_PAGE, html), "requisites_mismatch", "requisites_mismatch"),
            ("a captcha redirect", SUDACT_DEFENCE, "channel_unavailable", "captcha"),
        )
        for name, second_body, error, reason in cases:
            with self.subTest(second=name):
                self.forget_the_channel()
                document = [(SUDACT_ACT_PAGE, html)] * 3 + [second_body]
                with self.portal([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: document}) as base:
                    self.allow(sources.url_host(base))
                    first = self.resolve(base)
                    self.assertEqual("full_text", first["save_outcome"])
                    record_before = self.snapshot(first["source_id"])
                    files_before = {name: (layer_dir / name).read_bytes() for name in self.raw_files()}
                    second = self.resolve(base)
                    self.assertNotIn("/defence/", "".join(self.paths()))
                self.assertEqual(error, second["errors"][0].split(":")[0])
                self.assertEqual(f"refused:{reason}", second["save_outcome"], "the answer still carries the outcome")
                self.assertEqual(record_before, self.snapshot(first["source_id"]), "record and meta untouched")
                self.assertEqual(files_before, {name: (layer_dir / name).read_bytes() for name in self.raw_files()})

    def test_the_request_budget_is_enforced(self):
        self.assertEqual(60, limits.CHANNEL_MAX_REQUESTS_PER_RUN)
        search = sudact_search_path()
        with mock.patch.object(limits, "CHANNEL_MAX_REQUESTS_PER_RUN", 3):
            with self.portal([SUDACT_NEW], {}) as base:
                self.allow(sources.url_host(base))
                result = self.resolve(base)
                self.assertEqual(["/arbitral/", search, search], self.paths(), "three requests and no fourth")
        self.assertEqual(["channel_budget_spent"], result["errors"])
        self.assertEqual([], result["candidates"])
        self.assertEqual({"requests": 3, "captcha": False}, self.counts())
        self.assert_nothing_written(result)
        self.write_entry(0, limits.CHANNEL_MAX_REQUESTS_PER_RUN, False)
        with self.portal([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            spent = self.resolve(base)
            self.assertEqual([], _Handler.seen, "a run that spent its budget asks nothing at all")
        self.assertEqual(["channel_budget_spent"], spent["errors"])
        self.assert_nothing_written(spent)

    def test_both_requisites_are_required_before_any_request(self):
        cases = (({"expect_date": None}, "--expect-date"), ({"expect_number": None}, "--expect-number"))
        with self.portal([sudact_finished(SUDACT_ACT)], {SUDACT_ACT: SUDACT_ACT_PAGE}) as base:
            self.allow(sources.url_host(base))
            for overrides, flag in cases:
                with self.subTest(missing=flag):
                    _Handler.seen = []
                    result = self.resolve(base, **overrides)
                    self.assertEqual([f"requisites_required: {flag}"], result["errors"])
                    self.assertEqual([], _Handler.seen, "refused before anything left the process")
                    self.assertFalse(sources.channel_state_path(self.work_dir).exists(), "not even a slot was taken")
                    self.assert_nothing_written(result)


class SudactHostTest(_FakeClockTestCase):
    """D-202, Task 7 findings 2 and 3: the wall is the host's, so the host is closed — `save --url` too.

    A save to an address on the sudact host (the host of `SUDACT_BASE`, injected here as the
    `LocalServer`) never follows a redirect into `/defence/`, and after the captcha marker is set it
    refuses before any request: a portal that challenged the search challenges its documents too, and
    walking into the same wall with `save --url` would be the retry by other hands.
    """

    def save_on_sudact(self, base: str, path: str) -> dict:
        """`mf sources save --url <base><path>`, with the sudact host injected as `base`."""
        with mock.patch.object(sources, "SUDACT_BASE", base):
            return self.save(
                f"{base}{path}",
                title=SUDACT_TITLE,
                citation=SUDACT_CITATION,
                expect_number=SUDACT_NUMBER,
                expect_date=SUDACT_DATE,
            )

    def paths(self) -> list:
        return [row["path"] for row in _Handler.seen]

    def assert_nothing_written(self, result: dict) -> None:
        self.assertEqual({}, self.records(), result)
        self.assertEqual([], self.raw_files())

    def test_the_sudact_host_is_the_host_of_the_base_and_its_subdomains(self):
        cases = {
            "https://sudact.ru/arbitral/doc/VLaG5lHDfBoJ/": True,
            "https://www.sudact.ru/regular/doc/x/": True,
            "https://notsudact.ru/arbitral/doc/x/": False,
            "https://sudact.ru.example.org/arbitral/doc/x/": False,
            "file:///etc/passwd": False,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(expected, sources.sudact_address(url))

    def test_after_a_captcha_a_save_to_the_host_refuses_before_any_request(self):
        """Checked in step 1, under the lock and before the network — so a closed host costs no
        politeness pause either (the reread before dispatch would refuse too, but only after it)."""
        self.write_entry(0, 3, True)
        with LocalServer(b"", routes={SUDACT_ACT: (SUDACT_ACT_PAGE, "text/html; charset=utf-8")}) as base:
            self.allow(sources.url_host(base))
            sources._LAST_FETCH[sources.url_host(base)] = 0.0  # a pause would be owed if it got that far
            result = self.save_on_sudact(base, SUDACT_ACT)
            self.assertEqual([], _Handler.seen, "the closed host is not asked at all")
        self.assertEqual([], self.clock.waits, "and no pause is taken for it")
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("marker", result["challenge"])
        self.assertEqual({"requests": 3, "captcha": True}, self.counts(), "the file is read, never rewritten")
        self.assert_nothing_written(result)

    def test_a_marker_set_during_a_plain_saves_wait_stops_it_before_dispatch(self):
        """Round 2, item 1: the window between the step-1 check and the politeness pause of
        `fetch_allowed`. Another process closes the host while this save waits; it sends nothing."""
        waited: list = []

        def wait(seconds: float) -> None:
            sources.channel_mark_captcha(self.work_dir, "sudact")
            waited.append(seconds)

        with LocalServer(b"", routes={SUDACT_ACT: (SUDACT_ACT_PAGE, "text/html; charset=utf-8")}) as base:
            self.allow(sources.url_host(base))
            # This process has fetched the host before, so `fetch_allowed` owes it a politeness pause.
            sources._LAST_FETCH[sources.url_host(base)] = 0.0
            with mock.patch.object(sources, "_wait", wait):
                result = self.save_on_sudact(base, SUDACT_ACT)
            self.assertEqual([], _Handler.seen, "the save that waited through the shutdown is never sent")
        self.assertEqual(1, len(waited), "the pause was taken, and the marker landed during it")
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("marker", result["challenge"])
        self.assert_nothing_written(result)

    def test_a_page_cut_short_never_closes_the_host(self):
        """Round 2, item 3, on the save path: a partial body under the 2 KB floor reads as an
        interstitial to Task 4's admission, which still refuses it — but it is no challenge."""
        promised = {"Content-Length": str(len(SUDACT_ACT_PAGE))}
        route = (SUDACT_ACT_PAGE[:1500], "text/html; charset=utf-8", promised)
        with LocalServer(b"", routes={SUDACT_ACT: route}) as base:
            self.allow(sources.url_host(base))
            result = self.save_on_sudact(base, SUDACT_ACT)
        self.assertEqual(["interstitial"], result["errors"], "Task 4's own refusal, unchanged")
        self.assertFalse(sources.channel_state_path(self.work_dir).exists(), "no marker was written")
        self.assert_nothing_written(result)

    def test_a_save_never_requests_the_captcha_page(self):
        """The redirect into `/defence/` is refused before it is followed, and the host is closed."""
        routes = {SUDACT_ACT: SUDACT_DEFENCE, "/defence/": (ECFR_STUB, "text/html")}
        with LocalServer(b"", routes=routes) as base:
            self.allow(sources.url_host(base))
            result = self.save_on_sudact(base, SUDACT_ACT)
            self.assertEqual([SUDACT_ACT], self.paths(), "the captcha page itself is never requested")
            _Handler.seen = []
            again = self.save_on_sudact(base, SUDACT_ACT)
            self.assertEqual([], _Handler.seen, "and the next save of the run asks nothing")
        self.assertEqual(["channel_unavailable: captcha"], result["errors"])
        self.assertEqual("refused:captcha", result["save_outcome"])
        self.assertEqual("defence_redirect", result["challenge"])
        self.assertTrue(self.state()["channels"]["sudact"]["captcha"])
        self.assertEqual(["channel_unavailable: captcha"], again["errors"])
        self.assert_nothing_written(result)

    def test_a_challenge_a_plain_save_meets_closes_the_host(self):
        """An access wall, a throttle: the wall is the host's, whoever walks into it first."""
        throttle = (b"<html>slow down</html>", "text/html", {}, 429)
        cases = (("a wall", (ECFR_STUB, "text/html"), "access_stub"), ("a throttle", throttle, "unchecked: http_429"))
        for name, route, error in cases:
            with self.subTest(answer=name):
                sources.channel_state_path(self.work_dir).unlink(missing_ok=True)
                with LocalServer(b"", routes={SUDACT_ACT: route}) as base:
                    self.allow(sources.url_host(base))
                    result = self.save_on_sudact(base, SUDACT_ACT)
                self.assertEqual([error], result["errors"])
                self.assertTrue(self.state()["channels"]["sudact"]["captcha"])
                self.assert_nothing_written(result)

    def test_the_marker_closes_the_sudact_host_and_no_other(self):
        """`SUDACT_BASE` is not patched here: the local server is simply another host, and it is asked."""
        self.write_entry(0, 3, True)
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            result = self.save(f"{base}/ok", expect_number=VS_ACT_NUMBER, expect_date=VS_ACT_DATE)
        self.assertEqual([], result.get("errors", []), result)
        self.assertEqual("full_text", result["save_outcome"])

    def test_a_plain_save_keeps_the_rules_of_task_4(self):
        """The resolved save's «both requisites or refuse» is the resolved path's only: a plain `--url`
        of a page that is no court act is still an excerpt, and it is no challenge for the host."""
        with LocalServer(b"", routes={"/arbitral/": (SUDACT_SECTION_PAGE, "text/html; charset=utf-8")}) as base:
            self.allow(sources.url_host(base))
            result = self.save_on_sudact(base, "/arbitral/")
        self.assertEqual([], result.get("errors", []), result)
        self.assertEqual("excerpt:identity_unverified", result["save_outcome"])
        self.assertFalse(sources.channel_state_path(self.work_dir).exists(), "no challenge, no marker")


class PackPdfOriginalTest(SaveTestCase):
    """D-201: the freeze pins the original, and a changed original is said out loud."""

    def save_fixture(self, payload: bytes, **overrides) -> str:
        with LocalServer(payload) as base:
            self.allow(sources.url_host(base))
            result = self.save(
                f"{base}/served.pdf",
                title=VSRF_PDF_TITLE,
                citation=VSRF_PDF_CITATION,
                expect_number=VSRF_PDF_NUMBER,
                expect_date=VSRF_PDF_DATE,
                **overrides,
            )
        self.assertEqual([], result.get("errors", []), result)
        self.write_findings([{"source_id": result["source_id"]}], layer="case_law")
        return result["source_id"]

    def original_of(self, source_id: str) -> Path:
        return self.work_dir / self.records()[source_id]["raw_original_path"]

    def snapshot_rows(self) -> dict:
        pack = sources.read_pack(self.work_dir)
        return {row["source_id"]: row for row in pack["snapshot"]}

    def test_the_snapshot_pins_the_original_next_to_the_text(self):
        source_id = self.save_fixture(VSRF_PDF)
        self.freeze()
        row = self.snapshot_rows()[source_id]
        self.assertEqual(VSRF_PDF_SHA256, row["raw_original_sha256"])
        self.assertEqual(self.records()[source_id]["raw_sha256"], row["raw_sha256"])
        self.assertEqual([], sources.read_pack(self.work_dir).get("integrity_warnings", []))

    def test_a_scan_freezes_with_an_original_row_and_no_text_digest(self):
        source_id = self.save_fixture(SCAN_PDF)
        self.freeze()
        row = self.snapshot_rows()[source_id]
        self.assertIsNone(row["raw_sha256"])
        self.assertEqual(SCAN_PDF_SHA256, row["raw_original_sha256"])

    def test_an_edited_original_demotes_the_full_text_record_and_warns(self):
        source_id = self.save_fixture(VSRF_PDF)
        self.original_of(source_id).write_bytes(VSRF_PDF + b"%edited\n")
        result = self.freeze()
        self.assertEqual(
            [{"code": "full_text_integrity", "source_id": source_id, "was": "full_text", "now": "agent_summary"}],
            result["warnings"],
        )
        self.assertEqual("agent_summary", self.records()[source_id]["raw_kind"])
        self.assertEqual(
            state_io.sha256_bytes(VSRF_PDF + b"%edited\n"),
            self.snapshot_rows()[source_id]["raw_original_sha256"],
            "the snapshot records the truth of what is on disk",
        )

    def test_an_edited_original_of_a_scan_warns_without_demoting(self):
        source_id = self.save_fixture(SCAN_PDF)
        self.original_of(source_id).write_bytes(SCAN_PDF + b"%edited\n")
        result = self.freeze()
        self.assertEqual(
            [{"code": "full_text_integrity", "source_id": source_id, "was": "none", "now": "none"}],
            result["warnings"],
        )
        self.assertEqual("none", self.records()[source_id]["raw_kind"])

    def test_a_missing_original_writes_no_digest_and_warns(self):
        source_id = self.save_fixture(SCAN_PDF)
        self.original_of(source_id).unlink()
        result = self.freeze()
        self.assertEqual(1, len(result["warnings"]))
        self.assertEqual(source_id, result["warnings"][0]["source_id"])
        self.assertNotIn("raw_original_sha256", self.snapshot_rows()[source_id])
        self.assertEqual("none", self.records()[source_id]["raw_kind"])

    def test_one_freeze_never_hashes_one_file_twice(self):
        source_id = self.save_fixture(VSRF_PDF)
        hashed: list = []
        original = state_io.sha256_file

        def counting(path):
            hashed.append(Path(path).name)
            return original(path)

        with mock.patch.object(state_io, "sha256_file", counting):
            self.freeze()
        raw = sorted(name for name in hashed if name.startswith(source_id))
        self.assertEqual([f"{source_id}.md", f"{source_id}.pdf"], raw, "each file is hashed once")

    def test_one_source_never_raises_two_integrity_warnings(self):
        source_id = self.save_fixture(VSRF_PDF)
        self.original_of(source_id).write_bytes(VSRF_PDF + b"%edited\n")
        (self.work_dir / self.records()[source_id]["raw_path"]).write_text("other", encoding="utf-8")
        result = self.freeze()
        self.assertEqual(1, len(result["warnings"]))

    def test_a_pack_frozen_before_this_task_still_validates(self):
        source_id = self.save_fixture(VSRF_PDF)
        self.freeze()
        pack = sources.read_pack(self.work_dir)
        for row in pack["snapshot"]:
            row.pop("raw_original_sha256", None)
        self.assertEqual([], schema.validate(pack, "source-pack"))
        self.assertIn(source_id, {row["source_id"] for row in pack["snapshot"]})


class KilledSaveTest(SaveTestCase):
    """D-199 fix round 2: the registry is persisted first, so a kill cannot lose the old text."""

    def test_a_kill_between_the_registry_write_and_the_publication_keeps_the_old_text(self):
        expected = state_io.sha256_bytes(sources.prepare_raw(sources.markup_to_text(VS_ACT_PAGE, "")))
        context = multiprocessing.get_context("spawn")
        paused = context.Event()
        result = str(self.root / "result")
        with LocalServer(VS_ACT_PAGE) as base:
            self.register(
                layer="case_law",
                source_id="vs-act",
                title=VS_ACT_TITLE,
                citation=VS_ACT_CITATION,
                url=f"{base}/ok",
                raw_file=self.raw_file(text="Выдержка из определения.\n"),
                raw_kind="excerpt",
            )
            before = (self.work_dir / "research/raw/case_law/vs-act.md").read_bytes()
            process = context.Process(
                target=worker_save_killed_before_publishing,
                args=(
                    str(self.work_dir),
                    f"{base}/ok",
                    {"expect_number": VS_ACT_NUMBER, "expect_date": VS_ACT_DATE},
                    paused,
                    result,
                ),
            )
            process.start()
            self.assertTrue(paused.wait(timeout=120), "the child never reached the registry write")
            process.terminate()
            process.join(timeout=120)
        self.assertFalse(Path(result).exists(), "the child was killed, not allowed to finish")
        record = sources.read_registry(self.work_dir)["sources"]["vs-act"]
        self.assertEqual(expected, record["raw_sha256"], "the registry was persisted first")
        self.assertEqual("full_text", record["raw_kind"])
        stored = self.work_dir / record["raw_path"]
        self.assertEqual(before, stored.read_bytes(), "the old text is still on disk, whole")
        self.assertNotEqual(expected, state_io.sha256_file(stored))
        # The disagreement is loud, not silent: the freeze demotes the record and says so.
        self.write_findings([{"source_id": "vs-act"}], layer="case_law")
        answer = self.freeze()
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": "vs-act",
                "was": "full_text",
                "now": "agent_summary",
            },
            answer["warnings"],
        )

    def test_an_interrupted_excerpt_publication_is_repaired_by_the_retry(self):
        """A record whose file is missing has no text, so the next save publishes instead of idling."""
        context = multiprocessing.get_context("spawn")
        paused = context.Event()
        result = str(self.root / "result")
        with LocalServer(VS_ACT_PAGE) as base:
            self.allow(sources.url_host(base))
            process = context.Process(
                target=worker_save_killed_before_publishing,
                args=(str(self.work_dir), f"{base}/ok", {}, paused, result),
            )
            process.start()
            self.assertTrue(paused.wait(timeout=120), "the child never reached the registry write")
            process.terminate()
            process.join(timeout=120)
            self.assertFalse(Path(result).exists(), "the child was killed, not allowed to finish")
            records = self.records()
            self.assertEqual(1, len(records))
            source_id, record = next(iter(records.items()))
            self.assertTrue(record["raw_path"], "the registry was committed first")
            self.assertFalse(
                (self.work_dir / record["raw_path"]).is_file(), "the file was never published"
            )
            # A killed process cannot run its `finally`, so its temporary file stays behind: hidden,
            # `.tmp`, named by no record and matched by no `<id>.md` glob.
            self.assertEqual(self.temp_files(), self.raw_files(), "nothing was published")
            answer = self.save(f"{base}/ok")
        self.assertEqual(source_id, answer["source_id"])
        self.assertTrue(answer["idempotent"])
        self.assertEqual("excerpt:identity_unverified", answer["save_outcome"])
        self.assertEqual("excerpt", answer["raw_kind"])
        repaired = self.records()[source_id]
        stored = self.work_dir / repaired["raw_path"]
        self.assertTrue(stored.is_file(), "the retry published the text it fetched")
        self.assertEqual(repaired["raw_sha256"], state_io.sha256_file(stored))
        self.assertEqual(record["raw_sha256"], repaired["raw_sha256"], "the same bytes as the first run")
        self.assertIn(VS_ACT_NUMBER, stored.read_bytes().decode("utf-8"))

    def published_files(self) -> list:
        """The files a record could name: the killed child's leftover `.tmp` is not one of them."""
        return sorted(set(self.raw_files()) - set(self.temp_files()))

    def test_a_kill_between_the_two_renames_leaves_the_pair_half_published(self):
        """D-201 fix round 3: the original lands first, so the `.pdf` is there and the `.md` is not."""
        source_id, record = self._half_published_pair()
        self.assertTrue(record["raw_path"], "the registry was committed first and names both files")
        self.assertTrue(record["raw_original_path"])
        self.assertTrue((self.work_dir / record["raw_original_path"]).is_file())
        self.assertFalse((self.work_dir / record["raw_path"]).is_file(), "the text never landed")
        self.assertEqual([f"{source_id}.pdf"], self.published_files())

    def test_an_identical_retry_repairs_the_missing_half_and_stays_idempotent(self):
        source_id, _ = self._half_published_pair()
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            answer = self.save(f"{base}/served.pdf")
        self.assertEqual(source_id, answer["source_id"])
        self.assertTrue(answer["idempotent"], "the same bytes over a half-written pair is a repair")
        repaired = self.records()[source_id]
        text = self.work_dir / repaired["raw_path"]
        self.assertTrue(text.is_file(), "the retry published the half that was missing")
        self.assertEqual(repaired["raw_sha256"], state_io.sha256_file(text))
        original = self.work_dir / repaired["raw_original_path"]
        self.assertEqual(VSRF_PDF, original.read_bytes(), "the half that was there is untouched")
        self.assertEqual(VSRF_PDF_SHA256, repaired["raw_original_sha256"])
        self.assertEqual([f"{source_id}.md", f"{source_id}.pdf"], self.published_files())

    def test_the_mirror_case_repairs_a_missing_original_the_same_way(self):
        source_id, _ = self._half_published_pair()
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            # Complete the pair, then take the original away instead of the text.
            self.save(f"{base}/served.pdf")
            (self.work_dir / self.records()[source_id]["raw_original_path"]).unlink()
            self.assertEqual([f"{source_id}.md"], self.published_files())
            answer = self.save(f"{base}/served.pdf")
        self.assertTrue(answer["idempotent"])
        repaired = self.records()[source_id]
        self.assertEqual(VSRF_PDF, (self.work_dir / repaired["raw_original_path"]).read_bytes())
        self.assertEqual(VSRF_PDF_SHA256, repaired["raw_original_sha256"])
        self.assertEqual([f"{source_id}.md", f"{source_id}.pdf"], self.published_files())

    def _half_published_pair(self) -> tuple[str, dict]:
        """Run a PDF save in a child and kill it between the two renames; return `(id, record)`.

        The retries reuse the child's own `--title`/`--citation` (the `save_namespace` defaults),
        so the record is found again by its citation form: the mock server takes a fresh port each
        time and the url identity would not survive it.
        """
        context = multiprocessing.get_context("spawn")
        paused = context.Event()
        result = str(self.root / "half-result")
        with LocalServer(VSRF_PDF) as base:
            self.allow(sources.url_host(base))
            process = context.Process(
                target=worker_save_killed_between_publications,
                args=(str(self.work_dir), f"{base}/served.pdf", paused, result),
            )
            process.start()
            self.assertTrue(paused.wait(timeout=120), "the child never reached the second rename")
            process.terminate()
            process.join(timeout=120)
            self.assertFalse(Path(result).exists(), "the child was killed, not allowed to finish")
        records = self.records()
        self.assertEqual(1, len(records))
        source_id, record = next(iter(records.items()))
        return source_id, record


class ConcurrentSaveTest(SourcesTestCase):
    """D-199 step 3: the lock names the winner, and the loser answers with the winner's id."""

    def _run(self, url: str) -> list[dict]:
        """Two real processes, released together by a barrier: no sleep and no clock (§9)."""
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = [str(self.root / f"result-{i}") for i in range(2)]
        processes = [
            context.Process(target=worker_save, args=(str(self.work_dir), url, barrier, results[i]))
            for i in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
            self.assertEqual(0, process.exitcode, "a child crashed")
        return [json.loads(Path(path).read_text(encoding="utf-8")) for path in results]

    def raw_files(self) -> list:
        root = self.work_dir / sources.RAW_DIR
        return sorted(path.name for path in root.rglob("*") if path.is_file())

    def test_two_saves_of_one_url_leave_one_record_and_one_file(self):
        with LocalServer(VS_ACT_PAGE) as base:
            outcomes = self._run(f"{base}/ok")
        self.assertEqual([None, None], [row["error"] for row in outcomes])
        answers = [row["result"] for row in outcomes]
        created = [answer for answer in answers if answer.get("created")]
        idempotent = [answer for answer in answers if answer.get("idempotent")]
        self.assertEqual(1, len(created), answers)
        self.assertEqual(1, len(idempotent), answers)
        self.assertEqual(created[0]["source_id"], idempotent[0]["source_id"])
        registry = sources.read_registry(self.work_dir)["sources"]
        self.assertEqual(1, len(registry))
        record = registry[created[0]["source_id"]]
        self.assertEqual("full_text", record["raw_kind"])
        self.assertEqual([f"{created[0]['source_id']}.md"], self.raw_files())
        self.assertEqual(
            state_io.sha256_bytes((self.work_dir / record["raw_path"]).read_bytes()), record["raw_sha256"]
        )

    def test_two_saves_of_one_url_with_different_bodies_refuse_the_loser(self):
        with LocalServer(VS_ACT_PAGE, variants=(VS_ACT_PAGE, VS_ACT_VARIANT)) as base:
            outcomes = self._run(f"{base}/varying")
        self.assertEqual([None, None], [row["error"] for row in outcomes])
        answers = [row["result"] for row in outcomes]
        winners = [answer for answer in answers if answer.get("created")]
        losers = [answer for answer in answers if answer.get("errors")]
        self.assertEqual(1, len(winners), answers)
        self.assertEqual(1, len(losers), answers)
        self.assertTrue(losers[0]["errors"][0].startswith("source_id_collision"), losers[0]["errors"])
        registry = sources.read_registry(self.work_dir)["sources"]
        self.assertEqual(1, len(registry))
        record = registry[winners[0]["source_id"]]
        self.assertEqual(winners[0]["raw_sha256"], record["raw_sha256"])
        self.assertEqual(
            state_io.sha256_bytes((self.work_dir / record["raw_path"]).read_bytes()), record["raw_sha256"]
        )
        self.assertEqual([f"{winners[0]['source_id']}.md"], self.raw_files())


# --- verify ----------------------------------------------------------------


class VerifyTest(SourcesTestCase):
    def _verify(self, assignment=None) -> dict:
        args = argparse.Namespace(workdir=str(self.work_dir), set=assignment)
        return sources.run_verify(args)

    def test_celex_syntax(self):
        self.assertTrue(sources.CELEX_RE.match("32016R0679"))
        self.assertTrue(sources.CELEX_RE.match("62019CJ0311"))
        self.assertIsNone(sources.CELEX_RE.match("2016R0679"))
        self.assertIsNone(sources.CELEX_RE.match("32016R679"))

    def test_ecli_syntax(self):
        self.assertTrue(sources.ECLI_RE.match("ECLI:EU:C:2020:559"))
        self.assertIsNone(sources.ECLI_RE.match("ECLI:EU:C:20:559"))
        self.assertIsNone(sources.ECLI_RE.match("EU:C:2020:559"))

    def test_eli_syntax(self):
        self.assertTrue(sources.ELI_RE.match("eli/reg/2016/679/oj"))
        self.assertTrue(sources.ELI_RE.match("https://eur-lex.europa.eu/eli/reg/2016/679/oj"))
        self.assertIsNone(sources.ELI_RE.match("reg/2016/679/oj"))

    def test_neutral_citation_syntax(self):
        self.assertTrue(sources.NEUTRAL_RE.match("[2023] UKSC 12"))
        self.assertTrue(sources.NEUTRAL_RE.match("[2021] EWCA Civ 1234"))
        self.assertIsNone(sources.NEUTRAL_RE.match("2021 EWCA Civ 1234"))

    def test_reporter_citation_syntax(self):
        self.assertTrue(sources.REPORTER_RE.match("991 F.3d 112"))
        self.assertIsNone(sources.REPORTER_RE.match("F.3d 112"))

    def test_verify_reports_malformed_identifiers_and_sets_eu_syntax_ok(self):
        self.register(identifiers={"celex": "32016R0679", "eli": "eli/reg/2016/679/oj"})
        self.register(url="https://example.org/bad", title="Bad identifiers", identifiers={"celex": "nope"})
        result = self._verify()
        self.assertEqual(2, result["checked"])
        self.assertEqual([{"source_id": "bad-identifiers", "identifiers": ["celex"]}], result["invalid"])
        self.assertEqual(["gdpr-article-6"], result["eu_syntax_ok"])

    def test_source_without_eu_identifiers_has_eu_syntax_not_applicable(self):
        """D34-08: `null`, not `False` — a US docket is not a defective EU citation."""
        self.register(layer="case_law", title="Smith v Acme", identifiers={"reporter_cite": "991 F.3d 112"})
        result = self._verify()
        record = sources.read_registry(self.work_dir)["sources"]["smith-v-acme"]
        self.assertIsNone(record["verification"]["eu_syntax_ok"])
        self.assertEqual("n/a", record["verification"]["us"])
        self.assertEqual([], result["eu_syntax_ok"])
        self.assertEqual(["smith-v-acme"], result["eu_syntax_not_applicable"])

    def test_malformed_eu_identifier_is_false_not_null(self):
        self.register(identifiers={"celex": "nope"})
        self._verify()
        record = sources.read_registry(self.work_dir)["sources"]["gdpr-article-6"]
        self.assertIs(False, record["verification"]["eu_syntax_ok"])

    def test_verify_reports_mislabelled_ids(self):
        """D34-04: `gdpr-article-3-territorial-scope` holding Art. 88 — the id lies about its text."""
        self.register(
            source_id="gdpr-article-3-territorial-scope",
            title="GDPR Article 88 (Processing in the context of employment)",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
        )
        result = self._verify()
        self.assertEqual(["gdpr-article-3-territorial-scope"], [row["source_id"] for row in result["mislabelled"]])
        self.assertEqual("art:3", result["mislabelled"][0]["id_pinpoint"])
        self.assertEqual("art:88", result["mislabelled"][0]["declared_pinpoint"])

    def test_duplicate_key_separates_two_articles_of_one_regulation(self):
        """D34-04: one CELEX is not one source — the pinpoint is part of the key."""
        self.register(
            source_id="gdpr-art6",
            citation="Regulation (EU) 2016/679 (GDPR), art 6",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art6",
            identifiers={"celex": "32016R0679"},
        )
        self.register(
            source_id="gdpr-art88",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art88",
            identifiers={"celex": "32016R0679"},
        )
        self.assertEqual([], self._verify()["duplicates"])

    def test_the_same_article_registered_twice_is_one_duplicate_group(self):
        self.register(
            source_id="gdpr-art88",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_88",
            identifiers={"celex": "32016R0679"},
        )
        self.register(
            source_id="gdpr-article-88-processing-in-the-context-of-employment",
            title="GDPR Article 88 - Processing in the context of employment",
            citation="Regulation (EU) 2016/679 (GDPR), Art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art88",
            identifiers={"celex": "32016R0679"},
        )
        groups = self._verify()["duplicates"]
        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0]["mergeable"])
        self.assertEqual("gdpr-art88", groups[0]["canonical"])

    def test_duplicates_by_normalised_identifier(self):
        self.register(identifiers={"celex": "32016R0679"})
        self.register(url="https://example.org/dup", title="Same act again", identifiers={"celex": "3 2016 R0679"})
        result = self._verify()
        self.assertEqual(1, len(result["duplicates"]))
        self.assertEqual(["gdpr-article-6", "same-act-again"], sorted(result["duplicates"][0]["source_ids"]))

    def test_set_records_the_agent_us_verdict(self):
        self.register(layer="case_law", title="Smith v Acme")
        result = self._verify(assignment=["smith-v-acme", "us=unresolved"])
        self.assertEqual("unresolved", result["verification"]["us"])
        self.assertEqual("agent", result["verification"]["us_by"])
        record = sources.read_registry(self.work_dir)["sources"]["smith-v-acme"]
        self.assertEqual("unresolved", record["verification"]["us"])

    def test_set_rejects_an_unknown_verdict_or_field(self):
        self.register(layer="case_law", title="Smith v Acme")
        self.assertTrue(self._verify(assignment=["smith-v-acme", "us=maybe"])["errors"])
        self.assertTrue(self._verify(assignment=["smith-v-acme", "eu=ok"])["errors"])
        self.assertTrue(self._verify(assignment=["nope", "us=resolved"])["errors"])


# --- slice -----------------------------------------------------------------


class SliceTest(SourcesTestCase):
    ARTICLES = (
        "Article 5\n\nPersonal data shall be processed lawfully.\n\n"
        "Article 6\n\nProcessing shall be lawful only if consent is given.\n\n"
        "Article 7\n\nThe controller shall be able to demonstrate consent.\n"
    )

    def _slice(self, article: str) -> dict:
        args = argparse.Namespace(workdir=str(self.work_dir), source="gdpr-article-6", article=article)
        return sources.run_slice(args)

    def test_slice_returns_only_the_named_article(self):
        self.register(raw_file=self.raw_file(text=self.ARTICLES))
        result = self._slice("6")
        self.assertIn("Processing shall be lawful only if consent is given.", result["text"])
        self.assertNotIn("Article 7", result["text"])
        self.assertNotIn("Article 5", result["text"])

    def test_ambiguous_headings_are_an_error_not_the_first_hit(self):
        text = self.ARTICLES + "\nArticle 6\n\nA second, consolidated version of the same article.\n"
        self.register(raw_file=self.raw_file(text=text))
        result = self._slice("6")
        self.assertEqual("ambiguous", result["error"])
        self.assertEqual(2, len(result["positions"]))

    def test_cross_reference_in_running_text_is_not_a_heading(self):
        text = "Article 6\n\nSee also Article 9 for special categories of data.\n"
        self.register(raw_file=self.raw_file(text=text))
        self.assertEqual("not_found", self._slice("9")["error"])

    def test_slice_without_raw_is_no_raw(self):
        self.register()
        self.assertEqual("no_raw", self._slice("6")["error"])

    def test_slice_of_an_unknown_source_errors(self):
        args = argparse.Namespace(workdir=str(self.work_dir), source="nope", article="6")
        self.assertTrue(sources.run_slice(args)["errors"])


class MarkupToTextTest(unittest.TestCase):
    """D-163: HTML/XHTML bodies are stored as plain text, at the storage boundary."""

    def test_markup_to_text_keeps_article_headings_on_their_own_line(self):
        text = sources.markup_to_text(CELLAR_SNIPPET, "application/xhtml+xml").decode("utf-8")
        self.assertNotIn("<", text)
        self.assertNotIn("var x=1", text)
        self.assertNotIn("margin:0", text)
        self.assertRegex(text, r"(?m)^Article 9$")
        self.assertRegex(text, r"(?m)^Article 10$")
        self.assertIn("1.   Processing of personal data revealing", text.replace("\xa0", " "))
        self.assertEqual(1, len(sources.article_spans(text, "9")), "slice finds the converted heading")

    def test_markup_to_text_leaves_non_markup_alone(self):
        self.assertIsNone(sources.markup_to_text(b'{"a": 1}', "application/json"))
        self.assertIsNone(sources.markup_to_text(b"<DIV8 N=\"312.3\"><HEAD>x</HEAD></DIV8>", "text/xml"))
        self.assertIsNotNone(sources.markup_to_text(CELLAR_SNIPPET, ""), "no content type: sniffed as html")

    def test_nbsp_article_headings_convert_to_spaces_and_slice(self):
        """Archived AI Act pages write `Article&#160;N`; the heading must still slice (fix wave)."""
        for number in ("3", "5", "6", "9", "113"):
            payload = f"<p>Article&#160;{number}</p>".encode("ascii")
            text = sources.markup_to_text(payload, "text/html").decode("utf-8")
            self.assertNotIn("\xa0", text)
            self.assertRegex(text, rf"(?m)^Article {number}$")
            self.assertTrue(sources.article_spans(text, number), f"slice finds Article {number}")

    def test_nbsp_paragraph_spacing_survives_as_plain_spaces(self):
        """`1.` + three nbsp keeps three spaces, one character for one (fix wave)."""
        text = sources.markup_to_text(b"<p>1.&#160;&#160;&#160;Text</p>", "text/html").decode("utf-8")
        self.assertIn("1.   Text", text)
        self.assertNotIn("\xa0", text)


class MarkupStorageTest(SourcesTestCase):
    """D-163: register and fetch store the converted text; liveness hashes the same text."""

    def test_register_stores_html_raw_files_as_text(self):
        raw = self.root / "gdpr.html"
        raw.write_bytes(CELLAR_SNIPPET)
        record = sources.register_source(
            self.work_dir,
            layer="statutes",
            title="GDPR",
            citation="GDPR",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            tool="WebFetch",
            tier="critical",
            raw_file=raw,
            source_id="gdpr",
        )
        stored = (self.work_dir / record["raw_path"]).read_bytes()
        self.assertNotIn(b"<html", stored)
        self.assertRegex(stored.decode("utf-8"), r"(?m)^Article 9$")
        self.assertEqual(sources.state_io.sha256_bytes(stored), record["raw_sha256"], "the hash is of the text")

    def test_fetch_saves_the_text_of_an_html_body_and_liveness_hashes_the_same_text(self):
        url = "https://publications.europa.eu/resource/celex/32016R0679"
        body = CELLAR_SNIPPET * 4

        class Response:
            status = 200
            headers = {"Content-Type": "application/xhtml+xml"}

            def read(self, n=-1):
                return body

            def geturl(self):
                return url

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(request_url, method, timeout, headers=None, **kwargs):
            return Response()

        sources._LAST_FETCH.clear()
        self.addCleanup(sources._LAST_FETCH.clear)
        original = sources.allowlist_hosts
        sources.allowlist_hosts = lambda root=None: frozenset({"publications.europa.eu"})
        self.addCleanup(setattr, sources, "allowlist_hosts", original)
        with mock.patch("memoforge.sources._open", fake_open):
            result = sources.run_fetch(
                argparse.Namespace(
                    workdir=str(self.work_dir),
                    url=url,
                    method="GET",
                    json_body=None,
                    accept=None,
                    lang=None,
                    out=None,
                    layer=None,
                    timeout=5.0,
                )
            )
            probe = sources.probe_url(url, want_body=True)
        saved = (self.work_dir / result["path"]).read_bytes()
        self.assertNotIn(b"<html", saved)
        self.assertEqual(state_io.sha256_bytes(saved), result["sha256"])
        self.assertEqual("ok", probe["status"])
        self.assertEqual(result["sha256"], probe["sha256"])


CASUS_URL = "https://mcp.casus.legal/case/34232?t=TESTTOKEN"
"""D-192: the address a Casus tool answers with — an endpoint plus a session token, never a page.

`t=TESTTOKEN` is the literal every test in this file uses: no real token is ever written down.
"""


class PublicUrlTest(unittest.TestCase):
    """D-192: what a client may be given, and what the registry keeps to itself instead."""

    def test_a_non_public_host_leaves_no_url_and_records_the_endpoint(self):
        self.assertEqual(
            ("", "https://mcp.casus.legal/case/34232"), sources.public_url(CASUS_URL)
        )

    def test_every_non_public_host_is_handled_the_same_way(self):
        for host in sources.NON_PUBLIC_SOURCE_HOSTS:
            with self.subTest(host=host):
                clean, retrieved_from = sources.public_url(f"https://{host}/x/y?t=TESTTOKEN")
                self.assertEqual("", clean)
                self.assertEqual(f"https://{host}/x/y", retrieved_from)

    def test_an_auth_parameter_is_dropped_and_the_others_keep_their_order(self):
        self.assertEqual(
            ("https://www.consultant.ru/document/x/?page=2", ""),
            sources.public_url("https://www.consultant.ru/document/x/?token=TESTTOKEN&page=2"),
        )

    def test_the_parameter_name_is_matched_without_case(self):
        self.assertEqual(
            ("https://www.consultant.ru/document/x/?page=2", ""),
            sources.public_url("https://www.consultant.ru/document/x/?T=TESTTOKEN&page=2"),
        )

    def test_every_auth_parameter_name_is_removed(self):
        for name in sources.AUTH_QUERY_PARAMS:
            with self.subTest(parameter=name):
                clean, _ = sources.public_url(f"https://example.org/a?{name.upper()}=TESTTOKEN&p=1")
                self.assertEqual("https://example.org/a?p=1", clean)

    def test_a_url_without_an_auth_parameter_is_returned_unchanged(self):
        url = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62021CJ0252"
        self.assertEqual((url, ""), sources.public_url(url))

    def test_the_userinfo_trick_of_d_151_cannot_smuggle_a_host(self):
        """D-151: the hand-rolled parser read the fragment as the host; `urlsplit` does not."""
        clean, retrieved_from = sources.public_url("https://mcp.casus.legal/one/mcp#@sudact.ru")
        self.assertEqual("", clean)
        self.assertEqual("https://mcp.casus.legal/one/mcp", retrieved_from)
        self.assertEqual(
            ("https://sudact.ru/regular/doc/1/#@mcp.casus.legal", ""),
            sources.public_url("https://sudact.ru/regular/doc/1/#@mcp.casus.legal"),
        )

    def test_credentials_in_front_of_a_non_public_host_do_not_publish_it(self):
        clean, retrieved_from = sources.public_url("https://user:pw@mcp.casus.legal/case/1")
        self.assertEqual("", clean)
        self.assertEqual("https://mcp.casus.legal/case/1", retrieved_from)

    def test_a_host_that_serves_both_an_endpoint_and_public_pages_stays(self):
        for host in sources.PUBLIC_MCP_HOSTS:
            with self.subTest(host=host):
                self.assertEqual(
                    (f"https://{host}/doc/1", ""), sources.public_url(f"https://{host}/doc/1")
                )

    def test_an_empty_or_unparsable_url_keeps_todays_behaviour(self):
        self.assertEqual(("", ""), sources.public_url(""))
        self.assertEqual(("", ""), sources.public_url(None))
        self.assertEqual(("sudact.ru/doc/1", ""), sources.public_url("sudact.ru/doc/1"))


class CanonicalHostTest(unittest.TestCase):
    """A1: one canonical host before classification — a trailing dot or an IDNA dot is not a disguise."""

    NON_PUBLIC_SHAPES: tuple[str, ...] = (
        "https://mcp.casus.legal./case/1",
        "https://mcp.casus。legal/case/1",
        "https://mcp.casus．legal/case/1",
        "https://mcp｡casus.legal/case/1",
        "https://MCP.Casus.Legal/case/1",
    )
    """Shapes of one endpoint host that `urlsplit(...).hostname` alone does not collapse."""

    def test_the_canonical_host_collapses_case_the_trailing_dot_and_the_idna_dots(self):
        for shape in self.NON_PUBLIC_SHAPES:
            with self.subTest(url=shape):
                self.assertEqual("mcp.casus.legal", sources.source_host(shape))

    def test_every_shape_of_a_non_public_host_is_classified_as_one(self):
        for shape in self.NON_PUBLIC_SHAPES:
            with self.subTest(url=shape):
                clean, retrieved_from = sources.public_url(shape + "?t=TESTTOKEN")
                self.assertEqual("", clean)
                self.assertEqual("https://mcp.casus.legal/case/1", retrieved_from)

    def test_a_host_that_cannot_be_canonicalised_is_never_published(self):
        for shape in ("https://mcp..casus.legal/case/1", "https://" + "a" * 70 + ".example/x"):
            with self.subTest(url=shape):
                self.assertEqual(("", ""), sources.public_url(shape + "?t=TESTTOKEN"))

    def test_a_public_host_survives_canonicalisation_byte_for_byte(self):
        url = "https://www.consultant.ru/document/cons_doc_LAW_5142/"
        self.assertEqual((url, ""), sources.public_url(url))

    def test_the_scrub_collapses_the_same_shapes_inside_a_raw_text(self):
        for shape in self.NON_PUBLIC_SHAPES:
            with self.subTest(url=shape):
                scrubbed = sources.scrub_urls(f"See {shape}?t=TESTTOKEN for the text.")
                self.assertEqual("See [retrieved via CasusLegal (RU)] for the text.", scrubbed)

    def test_a_percent_encoded_host_has_no_readable_address(self):
        """D-206: `%2e` survives `urlsplit(...).hostname`, so the host is unreadable, not public."""
        self.assertEqual("", sources.canonical_host("mcp%2ecasus%2elegal"))
        self.assertEqual(("", ""), sources.public_url("https://mcp%2ecasus%2elegal/case/34232?t=TESTTOKEN"))

    def test_the_scrub_removes_a_percent_encoded_host_whole(self):
        scrubbed = sources.scrub_urls("See https://mcp%2ecasus%2elegal/case/34232?t=TESTTOKEN for the text.")
        self.assertIn(sources.ADDRESS_REMOVED, scrubbed)
        self.assertNotIn("casus", scrubbed)
        self.assertNotIn("TESTTOKEN", scrubbed)

    def test_a_percent_encoded_path_is_not_a_host(self):
        """D-206: a `%` in the path or query is normal and keeps working — only the host kills it."""
        url = "https://sudact.ru/regular/doc/%D0%90/"
        self.assertEqual((url, ""), sources.public_url(url))
        self.assertEqual(url, sources.clean_public_url(url))


class CredentialsOutsideTheQueryTest(unittest.TestCase):
    """A2: userinfo and an auth-bearing fragment are credentials too."""

    def test_userinfo_never_survives_on_a_public_host(self):
        self.assertEqual(
            ("https://www.consultant.ru/x", ""),
            sources.public_url("https://user:TESTTOKEN@www.consultant.ru/x"),
        )

    def test_a_fragment_carrying_an_auth_parameter_is_dropped(self):
        for fragment in ("access_token=TESTTOKEN", "a=1&t=TESTTOKEN", "jwt=TESTTOKEN"):
            with self.subTest(fragment=fragment):
                self.assertEqual(
                    ("https://www.consultant.ru/x", ""),
                    sources.public_url(f"https://www.consultant.ru/x#{fragment}"),
                )

    def test_a_router_fragment_carrying_an_auth_parameter_is_dropped_whole(self):
        # FF1: the fragment of a hash-router address is a path plus a query of its own, so parsing
        # it as one query string read the first name as `?t` / `/document?t` — in no list — and the
        # token reached the stored text and the inline hyperlink.
        for fragment in (
            "?t=TESTTOKEN",
            "/document?t=TESTTOKEN",
            "/document/12?a=1&access_token=TESTTOKEN",
            "/viewer?sid=TESTTOKEN&page=3",
        ):
            with self.subTest(fragment=fragment):
                cleaned, _ = sources.public_url(f"https://www.consultant.ru/x#{fragment}")
                self.assertEqual("https://www.consultant.ru/x", cleaned)
                self.assertNotIn("TESTTOKEN", cleaned)

    def test_an_ordinary_anchor_is_preserved(self):
        for anchor in ("#p123", "#dst100", "#art_6", "#@mcp.casus.legal"):
            with self.subTest(anchor=anchor):
                url = f"https://www.consultant.ru/document/x/{anchor}"
                self.assertEqual((url, ""), sources.public_url(url))

    def test_a_router_anchor_without_a_credential_is_preserved(self):
        # FF1: the whole fragment is dropped only for a credential; a route stays a location.
        for anchor in ("#/document/12", "#/document/12?page=3", "#art_6", "#dst100"):
            with self.subTest(anchor=anchor):
                url = f"https://www.consultant.ru/document/x{anchor}"
                self.assertEqual((url, ""), sources.public_url(url))

    def test_the_scrub_applies_the_same_rule_inside_a_raw_text(self):
        scrubbed = sources.scrub_urls(
            "A: https://user:TESTTOKEN@www.consultant.ru/x "
            "B: https://www.consultant.ru/y#access_token=TESTTOKEN "
            "C: https://www.consultant.ru/z#dst100"
        )
        self.assertNotIn("TESTTOKEN", scrubbed)
        self.assertIn("A: https://www.consultant.ru/x", scrubbed)
        self.assertIn("B: https://www.consultant.ru/y", scrubbed)
        self.assertIn("C: https://www.consultant.ru/z#dst100", scrubbed)

    def test_the_scrub_drops_a_router_fragment_of_a_stored_raw_text(self):
        # FF1: the stored text travels to the client as `sources/<id>.txt`, so the same rule.
        scrubbed = sources.scrub_urls(
            "A: https://www.consultant.ru/y#?t=TESTTOKEN "
            "B: https://www.consultant.ru/z#/document?t=TESTTOKEN "
            "C: https://www.consultant.ru/w#/document/12"
        )
        self.assertNotIn("TESTTOKEN", scrubbed)
        self.assertIn("A: https://www.consultant.ru/y", scrubbed)
        self.assertIn("B: https://www.consultant.ru/z", scrubbed)
        self.assertIn("C: https://www.consultant.ru/w#/document/12", scrubbed)

    def test_prepare_raw_drops_a_router_fragment_before_the_hash(self):
        cleaned = sources.prepare_raw(
            "Текст акта. Источник: https://www.consultant.ru/doc#/document?t=TESTTOKEN\n".encode("utf-8")
        ).decode("utf-8")
        self.assertNotIn("TESTTOKEN", cleaned)
        self.assertIn("https://www.consultant.ru/doc", cleaned)


class ManifestClassificationTest(unittest.TestCase):
    """D-192: a newly bundled server must be classified before it can be registered from."""

    def test_every_bundled_server_host_is_public_or_not(self):
        manifest = json.loads(
            (Path(sources.__file__).resolve().parents[2] / ".mcp.json").read_text(encoding="utf-8-sig")
        )
        known = set(sources.NON_PUBLIC_SOURCE_HOSTS) | set(sources.PUBLIC_MCP_HOSTS)
        for name, server in sorted((manifest.get("mcpServers") or {}).items()):
            with self.subTest(server=name):
                host = sources.source_host(server.get("url"))
                self.assertTrue(host, f"{name} has no parsable host")
                self.assertIn(host, known, f"{name} ({host}) is classified in neither tuple")

    def test_the_two_tuples_are_disjoint(self):
        self.assertEqual(
            set(), set(sources.NON_PUBLIC_SOURCE_HOSTS) & set(sources.PUBLIC_MCP_HOSTS)
        )


class RegisterPublicUrlTest(SourcesTestCase):
    """D-192: registration keeps the endpoint address out of `url` and warns about it."""

    def test_a_casus_url_is_not_stored_as_the_source_url(self):
        result = self.register(url=CASUS_URL, citation="Определение ВС РФ от 12.03.2024 № 305-ЭС23-1")
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertEqual("", record["url"])
        self.assertEqual("https://mcp.casus.legal/case/34232", record["retrieved_from"])
        self.assertIn("url_not_public", result["warnings"])
        self.assertIn("sudact.ru", result["hint"])
        self.assertNotIn("errors", result)

    def test_the_registry_still_validates_against_the_schema(self):
        self.register(url=CASUS_URL, citation="Определение ВС РФ от 12.03.2024 № 305-ЭС23-1")
        from memoforge import schema

        self.assertEqual([], schema.validate(sources.read_registry(self.work_dir), "sources"))

    def test_a_public_url_registers_without_a_warning(self):
        result = self.register()
        self.assertNotIn("warnings", result)
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertNotIn("retrieved_from", record)

    def test_the_dedup_key_is_computed_on_the_cleaned_url(self):
        first = self.register(url="https://sudact.ru/doc/1/?t=TESTTOKEN")
        second = self.register(url="https://sudact.ru/doc/1/", title="The same page")
        self.assertEqual(first["source_id"], second["source_id"])
        record = sources.read_registry(self.work_dir)["sources"][first["source_id"]]
        self.assertEqual("https://sudact.ru/doc/1/", record["url"])

    def test_the_update_path_records_the_endpoint_and_leaves_the_url_empty(self):
        """A citation-only record re-registered with the endpoint url keeps an empty `url`."""
        citation = "Определение ВС РФ от 12.03.2024 № 305-ЭС23-1"
        first = self.register(url="", citation=citation)
        again = self.register(url=CASUS_URL, citation=citation, title="The same decision")
        self.assertEqual(first["source_id"], again["source_id"])
        record = sources.read_registry(self.work_dir)["sources"][first["source_id"]]
        self.assertEqual("", record["url"])
        self.assertEqual("https://mcp.casus.legal/case/34232", record["retrieved_from"])
        self.assertIn("url_not_public", again["warnings"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))

    def test_an_explicit_id_reregistration_records_the_endpoint_and_warns(self):
        """D-192: the same bytes under an occupied `--id` is an update, not a dead end.

        The early return of D34-04/D-143 guards *different* bytes. Identical bytes have to reach the
        url rule, the metadata update and the warning like any other repeat registration — otherwise
        `--id X --url <casus endpoint>` silently records nothing.
        """
        citation = "Определение ВС РФ от 12.03.2024 № 305-ЭС23-1"
        first = self.register(url="", citation=citation, source_id="vs-rf-305", raw_file=self.raw_file())
        again = self.register(
            url=CASUS_URL,
            citation=citation,
            source_id="vs-rf-305",
            raw_file=self.raw_file(name="copy.md"),
        )
        self.assertEqual("vs-rf-305", again["source_id"])
        self.assertFalse(again["created"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])
        self.assertEqual(["url_not_public"], again["warnings"])
        self.assertIn("sudact.ru", again["hint"])
        record = sources.read_registry(self.work_dir)["sources"]["vs-rf-305"]
        self.assertEqual("", record["url"])
        self.assertEqual("https://mcp.casus.legal/case/34232", record["retrieved_from"])
        self.assertEqual(1, len(sources.read_registry(self.work_dir)["sources"]))

    def test_an_explicit_id_reregistration_stores_a_public_url_cleaned(self):
        self.register(url="", source_id="vs-rf-305", raw_file=self.raw_file())
        again = self.register(
            url="https://sudact.ru/regular/doc/abc/?t=TESTTOKEN&page=2",
            source_id="vs-rf-305",
            raw_file=self.raw_file(name="copy.md"),
            meta={"court": "ВС РФ"},
        )
        self.assertNotIn("warnings", again)
        record = sources.read_registry(self.work_dir)["sources"]["vs-rf-305"]
        self.assertEqual("https://sudact.ru/regular/doc/abc/?page=2", record["url"])
        self.assertEqual("ВС РФ", record["meta"]["court"])
        self.assertNotIn("retrieved_from", record)

    def test_a_cross_layer_reregistration_under_an_occupied_id_is_refused(self):
        """A record's layer never changes: identical bytes under another layer are refused.

        Fix round 2 — the round-1 fall-through accepted identical bytes whatever layer the call
        named, and then the update branch kept the old layer while `store_raw` wrote the file under
        the new one and the answer reported the new one. Three places, three different answers.
        """
        self.register(
            layer="statutes",
            url="https://sudact.ru/regular/doc/abc/",
            source_id="vs-rf-305",
            raw_file=self.raw_file(),
        )
        before = dict(sources.read_registry(self.work_dir)["sources"]["vs-rf-305"])
        again = self.raw_file(name="copy.md")
        result = self.register(
            layer="case_law", url=CASUS_URL, source_id="vs-rf-305", raw_file=again
        )
        self.assertEqual(
            ["source_id_layer_mismatch: vs-rf-305 is registered under 'statutes'"], result["errors"]
        )
        self.assertEqual("statutes", result["held_layer"])
        self.assertIn("statutes", result["hint"])
        self.assertNotIn("warnings", result)
        self.assertEqual(before, sources.read_registry(self.work_dir)["sources"]["vs-rf-305"])
        self.assertFalse(
            (self.work_dir / "research" / "raw" / "case_law").exists(),
            "a refused registration writes no raw file under the layer it named",
        )
        self.assertTrue(again.exists(), "a refused registration does not consume the raw file")

    def test_the_slug_derived_path_cannot_cross_a_layer_at_all(self):
        """`dedup_key` carries the layer, so a record of another layer never matches (§3.1 rule 5)."""
        first = self.register(layer="statutes", url="https://sudact.ru/regular/doc/abc/")
        second = self.register(layer="case_law", url="https://sudact.ru/regular/doc/abc/")
        self.assertNotEqual(first["source_id"], second["source_id"])
        registry = sources.read_registry(self.work_dir)["sources"]
        self.assertEqual("statutes", registry[first["source_id"]]["layer"])
        self.assertEqual("case_law", registry[second["source_id"]]["layer"])

    def test_different_bytes_under_an_occupied_id_still_collide(self):
        self.register(url="", source_id="vs-rf-305", raw_file=self.raw_file())
        other = self.raw_file(name="other.md", text="# Другое решение\n\nТекст.\n")
        result = self.register(
            url=CASUS_URL, title="Другое решение", source_id="vs-rf-305", raw_file=other
        )
        self.assertEqual(
            ["source_id_collision: vs-rf-305 already holds 'GDPR Article 6'"], result["errors"]
        )
        self.assertNotIn("warnings", result)
        record = sources.read_registry(self.work_dir)["sources"]["vs-rf-305"]
        self.assertEqual("GDPR Article 6", record["title"])
        self.assertNotIn("retrieved_from", record)
        self.assertTrue(other.exists(), "a refused registration does not consume the raw file")

    def test_every_string_field_of_the_record_is_scrubbed(self):
        """A4: a token-bearing address in `title`, the citation, `meta` or `identifiers` reaches
        the docx, the markdown, `source-pack.md` and the dashboard — so it is scrubbed at ingest."""
        result = self.register(
            title=f"Определение по делу ({CASUS_URL})",
            citation=f"Определение ВС РФ, текст: {CASUS_URL}",
            url="https://sudact.ru/regular/doc/abc/",
            identifiers={"eli": "https://mcp.casus.legal/eli/1?t=TESTTOKEN"},
            meta={
                "short_name": "ВС РФ (https://mcp.casus.legal/case/1?t=TESTTOKEN)",
                "year": 2024,
                "sources": ["https://sudact.ru/x?token=TESTTOKEN"],
            },
        )
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        dumped = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("mcp.casus.legal", dumped)
        self.assertNotIn("TESTTOKEN", dumped)
        self.assertIn("[retrieved via CasusLegal (RU)]", record["title"])
        self.assertIn("[retrieved via CasusLegal (RU)]", record["citation_form"])
        self.assertEqual(2024, record["meta"]["year"], "a non-string value is untouched")
        self.assertEqual(["https://sudact.ru/x"], record["meta"]["sources"])

    def test_a_held_endpoint_url_does_not_survive_a_re_registration(self):
        """A5b: the held url is re-cleaned even when the incoming call carries none."""
        self.register(url="https://sudact.ru/regular/doc/abc/", source_id="vs-rf-305",
                      raw_file=self.raw_file())
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["vs-rf-305"]["url"] = CASUS_URL  # a record written before D-192
        sources.write_registry(self.work_dir, registry)
        again = self.register(url="", source_id="vs-rf-305", raw_file=self.raw_file(name="copy.md"))
        self.assertEqual("vs-rf-305", again["source_id"])
        record = sources.read_registry(self.work_dir)["sources"]["vs-rf-305"]
        self.assertEqual("", record["url"])
        self.assertEqual("https://mcp.casus.legal/case/34232", record["retrieved_from"])

    def test_a_held_token_url_is_re_cleaned_without_losing_the_page(self):
        self.register(url="https://sudact.ru/regular/doc/abc/", source_id="vs-rf-305",
                      raw_file=self.raw_file())
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["vs-rf-305"]["url"] = "https://sudact.ru/regular/doc/abc/?t=TESTTOKEN"
        sources.write_registry(self.work_dir, registry)
        self.register(url="", source_id="vs-rf-305", raw_file=self.raw_file(name="copy.md"))
        record = sources.read_registry(self.work_dir)["sources"]["vs-rf-305"]
        self.assertEqual("https://sudact.ru/regular/doc/abc/", record["url"])
        self.assertNotIn("retrieved_from", record)

    def test_the_frozen_pack_carries_the_endpoint_but_the_client_view_never_prints_it(self):
        from memoforge import render

        result = self.register(url=CASUS_URL, citation="Определение ВС РФ от 12.03.2024 № 305-ЭС23-1")
        self.write_findings([{"source_id": result["source_id"]}])
        self.freeze()
        pack = sources.read_pack(self.work_dir)
        entry = pack["entries"][0]
        self.assertEqual("", entry["url"])
        self.assertEqual("https://mcp.casus.legal/case/34232", entry["retrieved_from"])
        markdown = render.render_source_pack(pack)
        self.assertNotIn("mcp.casus.legal", markdown)
        self.assertNotIn("retrieved_from", markdown)


class RawScrubTest(SourcesTestCase):
    """D-193: the stored text of a source never carries an endpoint address or a token."""

    RAW_WITH_ENDPOINT = (
        "# Определение ВС РФ\n"
        "\n"
        f"URL: {CASUS_URL}\n"
        "Публикация: https://sudact.ru/regular/doc/abc/?token=TESTTOKEN&page=2\n"
        "\n"
        "Текст решения.\n"
    )

    def test_the_endpoint_is_replaced_by_the_name_of_the_database(self):
        result = self.register(
            raw_file=self.raw_file(name="casus.md", text=self.RAW_WITH_ENDPOINT),
            url="https://sudact.ru/regular/doc/abc/",
        )
        stored = (self.work_dir / result["raw_path"]).read_text(encoding="utf-8")
        self.assertNotIn("mcp.casus.legal", stored)
        self.assertNotIn("TESTTOKEN", stored)
        self.assertIn("[retrieved via CasusLegal (RU)]", stored)

    def test_a_public_url_keeps_its_address_and_loses_its_token(self):
        result = self.register(
            raw_file=self.raw_file(name="casus.md", text=self.RAW_WITH_ENDPOINT),
            url="https://sudact.ru/regular/doc/abc/",
        )
        stored = (self.work_dir / result["raw_path"]).read_text(encoding="utf-8")
        self.assertIn("https://sudact.ru/regular/doc/abc/?page=2", stored)

    def test_the_scrubbed_text_is_what_the_sha_is_computed_over(self):
        result = self.register(
            raw_file=self.raw_file(name="casus.md", text=self.RAW_WITH_ENDPOINT),
            url="https://sudact.ru/regular/doc/abc/",
            source_id="vs-rf-305",
        )
        stored = self.work_dir / result["raw_path"]
        self.assertEqual(state_io.sha256_file(stored), result["raw_sha256"])
        self.assertEqual(len(stored.read_text(encoding="utf-8")), result["raw_chars"])

    def test_registering_the_same_file_again_is_idempotent(self):
        first = self.register(
            raw_file=self.raw_file(name="casus.md", text=self.RAW_WITH_ENDPOINT),
            url="https://sudact.ru/regular/doc/abc/",
            source_id="vs-rf-305",
        )
        again = self.register(
            raw_file=self.raw_file(name="casus-copy.md", text=self.RAW_WITH_ENDPOINT),
            url="https://sudact.ru/regular/doc/abc/",
            source_id="vs-rf-305",
        )
        self.assertNotIn("errors", again)
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])

    def test_a_text_without_a_url_is_stored_byte_for_byte(self):
        result = self.register(raw_file=self.raw_file())
        self.assertEqual(
            RAW_TEXT, (self.work_dir / result["raw_path"]).read_text(encoding="utf-8")
        )


class JsonEscapedRawScrubTest(SourcesTestCase):
    """A3: an agent saves a tool's JSON answer as the raw text, and JSON escapes every slash."""

    RAW_JSON = (
        '{\n'
        '  "source": "https:\\/\\/mcp.casus.legal\\/case\\/1?t=TESTTOKEN",\n'
        '  "page": "https:\\/\\/sudact.ru\\/regular\\/doc\\/abc\\/?token=TESTTOKEN&page=2",\n'
        '  "text": "Текст решения."\n'
        '}\n'
    )

    def stored(self, name: str = "casus.json") -> str:
        result = self.register(
            raw_file=self.raw_file(name=name, text=self.RAW_JSON),
            url="https://sudact.ru/regular/doc/abc/",
            source_id="vs-rf-305",
        )
        self.last = result
        return (self.work_dir / result["raw_path"]).read_text(encoding="utf-8")

    def test_the_escaped_endpoint_is_replaced_and_the_text_stays_json(self):
        stored = self.stored()
        self.assertNotIn("mcp.casus.legal", stored)
        self.assertNotIn("TESTTOKEN", stored)
        self.assertIn("[retrieved via CasusLegal (RU)]", stored)
        self.assertEqual("[retrieved via CasusLegal (RU)]", json.loads(stored)["source"])

    def test_an_escaped_public_url_keeps_its_escaping_and_loses_the_token(self):
        stored = self.stored()
        self.assertIn("https:\\/\\/sudact.ru\\/regular\\/doc\\/abc\\/?page=2", stored)
        self.assertEqual("https://sudact.ru/regular/doc/abc/?page=2", json.loads(stored)["page"])

    def test_the_second_registration_of_the_same_json_is_idempotent(self):
        first_sha = self.stored()
        first = self.last
        again = self.register(
            raw_file=self.raw_file(name="copy.json", text=self.RAW_JSON),
            url="https://sudact.ru/regular/doc/abc/",
            source_id="vs-rf-305",
        )
        self.assertNotIn("errors", again)
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])
        self.assertEqual(first_sha, (self.work_dir / again["raw_path"]).read_text(encoding="utf-8"))


class FailClosedTest(SourcesTestCase):
    """R1: when the cleaning cannot read an address, it suppresses it — never returns the input."""

    BAD_PORT = "https://user:TESTTOKEN@sudact.ru:bad/x?t=TESTTOKEN"
    """A public host behind a non-numeric port: `urlsplit(...).port` raises on the way through."""

    BAD_MCP_HOST = "https://user:TESTTOKEN@mcp..casus.legal/case/1?t=TESTTOKEN"
    """An endpoint host with an empty label: the idna codec refuses it, so it classifies as nothing."""

    def test_an_unreadable_port_suppresses_the_whole_address(self):
        self.assertEqual(("", ""), sources.public_url(self.BAD_PORT))
        self.assertEqual("", sources.clean_public_url(self.BAD_PORT))

    def test_an_unreadable_host_suppresses_the_whole_address(self):
        self.assertEqual(("", ""), sources.public_url(self.BAD_MCP_HOST))
        self.assertEqual("", sources.clean_public_url(self.BAD_MCP_HOST))

    def test_registration_stores_nothing_and_warns(self):
        for index, url in enumerate((self.BAD_PORT, self.BAD_MCP_HOST)):
            with self.subTest(url=url.split("@", 1)[0] + "@…"):
                result = self.register(url=url, citation=f"C {index} 2024", source_id=f"bad-{index}")
                record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
                self.assertEqual("", record["url"])
                self.assertNotIn("retrieved_from", record)
                self.assertEqual(["url_not_public"], result["warnings"])
                self.assertNotIn("TESTTOKEN", json.dumps(record, ensure_ascii=False))

    def test_the_scrubber_replaces_an_unreadable_address_with_a_marker(self):
        for url in (self.BAD_PORT, self.BAD_MCP_HOST):
            with self.subTest(url=url.split("@", 1)[0] + "@…"):
                scrubbed = sources.scrub_urls(f"Источник: {url} — конец.")
                self.assertEqual("Источник: [address removed] — конец.", scrubbed)

    def test_an_unreadable_address_in_meta_is_replaced_too(self):
        self.assertEqual(
            {"eli": "[address removed]"}, sources.scrub_values({"eli": self.BAD_MCP_HOST})
        )

    def test_redacted_url_never_echoes_an_address_it_cannot_read(self):
        self.assertEqual(sources.ADDRESS_REMOVED, sources.redacted_url(self.BAD_PORT))
        self.assertEqual(sources.ADDRESS_REMOVED, sources.redacted_url("https:///path?t=TESTTOKEN"))

    def test_the_stored_raw_text_and_the_client_export_carry_neither(self):
        raw = f"# Решение\n\nИсточник: {self.BAD_MCP_HOST}\n\nТекст.\n"
        result = self.register(raw_file=self.raw_file(name="bad.md", text=raw), url="")
        stored = (self.work_dir / result["raw_path"]).read_text(encoding="utf-8")
        exported = self.client_export(result["source_id"])
        for text in (stored, exported):
            self.assertNotIn("TESTTOKEN", text)
            self.assertNotIn("casus", text)
            self.assertIn("[address removed]", text)

    def test_a_url_without_a_host_still_loses_its_credentials(self):
        self.assertEqual(("sudact.ru/doc/1", ""), sources.public_url("sudact.ru/doc/1"))
        self.assertEqual(("sudact.ru/doc/1", ""), sources.public_url("sudact.ru/doc/1?t=TESTTOKEN"))


class JsonUnicodeEscapeScrubTest(SourcesTestCase):
    """R2: a `\\uXXXX` escape used to end the url token before the credential behind it."""

    RAW_JSON = (
        '{\n'
        '  "endpoint": "https:\\/\\/mcp.casus.legal\\/case\\/1?page=2\\u0026t=TESTTOKEN",\n'
        '  "amp": "https:\\/\\/sudact.ru\\/x?page=2\\u0026t=TESTTOKEN",\n'
        '  "eq": "https:\\/\\/sudact.ru\\/y?page=2&t\\u003dTESTTOKEN",\n'
        '  "qm": "https:\\/\\/sudact.ru\\/z\\u003ft=TESTTOKEN",\n'
        '  "text": "Текст решения."\n'
        '}\n'
    )

    def stored(self, name: str = "escaped.json") -> tuple[dict, str]:
        result = self.register(
            raw_file=self.raw_file(name=name, text=self.RAW_JSON),
            url="",
            source_id="vs-rf-305",
        )
        return result, (self.work_dir / result["raw_path"]).read_text(encoding="utf-8")

    def test_no_escape_shape_lets_a_token_survive(self):
        _, stored = self.stored()
        self.assertNotIn("TESTTOKEN", stored)
        self.assertNotIn("mcp.casus.legal", stored)

    def test_the_stored_text_is_still_valid_json(self):
        _, stored = self.stored()
        parsed = json.loads(stored)
        self.assertEqual("[retrieved via CasusLegal (RU)]", parsed["endpoint"])
        self.assertEqual("https://sudact.ru/x?page=2", parsed["amp"])
        self.assertEqual("https://sudact.ru/y?page=2", parsed["eq"])
        self.assertEqual("https://sudact.ru/z", parsed["qm"])
        self.assertEqual("Текст решения.", parsed["text"])

    def test_a_second_registration_of_the_same_file_keeps_the_same_sha(self):
        first, first_text = self.stored()
        again = self.register(
            raw_file=self.raw_file(name="copy.json", text=self.RAW_JSON),
            url="",
            source_id="vs-rf-305",
        )
        self.assertNotIn("errors", again)
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["raw_sha256"], again["raw_sha256"])
        self.assertEqual(first_text, (self.work_dir / again["raw_path"]).read_text(encoding="utf-8"))

    def test_the_client_export_carries_no_token(self):
        result, _ = self.stored()
        exported = self.client_export(result["source_id"])
        self.assertNotIn("TESTTOKEN", exported)
        self.assertNotIn("mcp.casus.legal", exported)
        self.assertEqual("[retrieved via CasusLegal (RU)]", json.loads(exported)["endpoint"])


class RedactedUrlTest(SourcesTestCase):
    """A6: an error or a telemetry line never repeats the address it was handed."""

    LEAKY = "https://user:TESTTOKEN@mcp.casus.legal/case/1?t=TESTTOKEN#access_token=TESTTOKEN"

    def test_redacted_url_keeps_the_scheme_the_host_and_the_path_only(self):
        self.assertEqual("https://mcp.casus.legal/case/1", sources.redacted_url(self.LEAKY))
        self.assertEqual("", sources.redacted_url(""))

    def test_a_refused_fetch_names_no_credential(self):
        result = sources.run_fetch(
            argparse.Namespace(
                workdir=str(self.work_dir),
                url=self.LEAKY,
                method="GET",
                json_body=None,
                accept=None,
                lang=None,
                out=None,
                layer=None,
                timeout=5.0,
            )
        )
        joined = json.dumps(result, ensure_ascii=False)
        self.assertEqual(["userinfo_not_allowed: https://mcp.casus.legal/case/1"], result["errors"])
        self.assertNotIn("TESTTOKEN", joined)
        self.assertNotIn("?", joined)
        self.assertNotIn("@", joined)


class LivenessNormalisationTest(SourcesTestCase):
    """A7: storage hashes the scrubbed text, so liveness must compare the same normalisation."""

    BODY = ("Публикация: https://sudact.ru/regular/doc/abc/?t=TESTTOKEN\n" + LIVE_TEXT).encode("utf-8")

    def _liveness(self) -> dict:
        return sources.run_liveness(
            argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
        )

    def _register(self, base: str) -> None:
        path = self.root / "body.md"
        path.write_bytes(self.BODY)
        self.register(url=f"{base}/ok", raw_file=path, tool="curl", source_id="vs-rf-305")

    def test_an_unchanged_page_with_a_token_in_it_is_not_reported_changed(self):
        with LocalServer(self.BODY) as base:
            self._register(base)
            row = self._liveness()["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertEqual("confirmed", row["provenance"])

    def test_a_genuinely_changed_page_is_still_reported_changed(self):
        with LocalServer(self.BODY) as base:
            self._register(base)
            registry = sources.read_registry(self.work_dir)
            registry["sources"]["vs-rf-305"]["url"] = f"{base}/changed"
            sources.write_registry(self.work_dir, registry)
            row = self._liveness()["checked"][0]
        self.assertEqual("changed", row["status"])
        self.assertEqual("agent_saved", row["provenance"])

    def test_a_record_whose_sha_predates_the_scrub_still_matches(self):
        with LocalServer(self.BODY) as base:
            self._register(base)
            registry = sources.read_registry(self.work_dir)
            registry["sources"]["vs-rf-305"]["raw_sha256"] = state_io.sha256_bytes(self.BODY)
            sources.write_registry(self.work_dir, registry)
            row = self._liveness()["checked"][0]
        self.assertEqual("ok", row["status"])
        self.assertEqual("confirmed", row["provenance"])


class SlugifyTest(unittest.TestCase):
    """D-194: a Cyrillic title keeps its words instead of folding away to its digits."""

    def test_the_cyrillic_examples_of_the_decision(self):
        self.assertEqual("gk-rf-st-428", sources.slugify("ГК РФ, ст. 428"))
        self.assertEqual("oferta-ozon", sources.slugify("Оферта Ozon"))

    def test_the_numero_sign_becomes_a_word(self):
        self.assertEqual(
            "opredelenie-vs-rf-no-305-es23-12345",
            sources.slugify("Определение ВС РФ № 305-ЭС23-12345"),
        )

    def test_the_whole_transliteration_table_is_applied(self):
        self.assertEqual(
            "abvgdeezhziiklmnoprstufkhtschshshchyeiuia",
            sources.slugify("абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        )
        self.assertEqual(sources.slugify("ЖУРНАЛ"), sources.slugify("журнал"))

    def test_latin_titles_of_existing_fixtures_are_unchanged(self):
        self.assertEqual("gdpr-article-6", sources.slugify("GDPR Article 6"))
        self.assertEqual(
            "regulation-eu-2016-679-gdpr-article-6",
            sources.slugify("Regulation (EU) 2016/679 (GDPR), Article 6"),
        )
        self.assertEqual("smith-v-acme-analytics-llc", sources.slugify("Smith v Acme Analytics LLC"))

    def test_a_cyrillic_citation_no_longer_collides_with_a_bare_number(self):
        self.assertNotEqual(sources.slugify("ГК РФ, ст. 428"), sources.slugify("428"))


class EmptyUrlLivenessTest(SourcesTestCase):
    """D-192: a record whose url the registry refused to keep is never probed."""

    def test_a_record_without_a_url_is_reported_unchecked_and_not_requested(self):
        self.register(url=CASUS_URL, citation="Определение ВС РФ от 12.03.2024 № 305-ЭС23-1")
        with mock.patch.object(sources, "probe_url", side_effect=AssertionError("probed")):
            result = sources.run_liveness(
                argparse.Namespace(workdir=str(self.work_dir), source=None, timeout=5.0)
            )
        self.assertEqual(1, len(result["checked"]))
        self.assertEqual("unchecked", result["checked"][0]["status"])
        self.assertEqual("no_url", result["checked"][0]["error"])


class RawKindTest(SourcesTestCase):
    """D-200: `raw_kind` — what the saved text is. Writers write it, readers default it."""

    def test_the_closed_enum_is_published(self):
        self.assertEqual(("full_text", "excerpt", "agent_summary", "client_file", "none"), sources.RAW_KINDS)
        self.assertEqual(("excerpt", "agent_summary", "client_file"), sources.AGENT_RAW_KINDS)

    def test_raw_kind_of_answers_the_field_when_present(self):
        for kind in sources.RAW_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(kind, sources.raw_kind_of({"raw_kind": kind}))

    def test_raw_kind_of_defaults_by_what_the_record_holds(self):
        self.assertEqual("agent_summary", sources.raw_kind_of({"raw_path": "research/raw/statutes/x.md"}))
        self.assertEqual("agent_summary", sources.raw_kind_of({"raw_sha256": "a" * 64}))
        self.assertEqual("agent_summary", sources.raw_kind_of({"raw_path": None, "raw_sha256": "a" * 64}))
        self.assertEqual("none", sources.raw_kind_of({}))
        self.assertEqual("none", sources.raw_kind_of({"raw_path": None, "raw_sha256": None}))

    def test_store_raw_writes_the_default_kind(self):
        result = self.register(raw_file=self.raw_file())
        self.assertEqual("agent_summary", result["raw_kind"])
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertEqual("agent_summary", record["raw_kind"])

    def test_store_raw_accepts_an_explicit_kind(self):
        raw = self.raw_file()
        stored = sources.store_raw(self.work_dir, "statutes", "gdpr-article-6", raw, raw_kind="client_file")
        self.assertEqual("client_file", stored["raw_kind"])

    def test_register_raw_kind_client_file_is_stored(self):
        result = sources.register_source(
            self.work_dir,
            layer="statutes",
            title="GDPR Article 6",
            citation="GDPR, Art. 6",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            tool="mcp__ldh__get_document",
            tier="critical",
            raw_file=self.raw_file(),
            raw_kind="client_file",
        )
        self.assertEqual("client_file", result["raw_kind"])
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertEqual("client_file", record["raw_kind"])

    def test_register_rejects_full_text_and_none_at_the_parser(self):
        import contextlib
        import io

        parser = cli.build_parser()
        for kind in ("full_text", "none"):
            with self.subTest(kind=kind):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                    parser.parse_args(
                        [
                            "sources", "register",
                            "--workdir", str(self.work_dir),
                            "--layer", "statutes",
                            "--title", "T",
                            "--citation", "C",
                            "--raw-file", str(self.raw_file(name=f"{kind}.md")),
                            "--raw-kind", kind,
                        ]
                    )
                self.assertIn("--raw-kind", err.getvalue())

    def test_raw_file_over_a_code_saved_record_is_refused(self):
        import json

        first = self.register(raw_file=self.raw_file(), source_id="code-saved")
        registry = sources.read_registry(self.work_dir)
        record = registry["sources"][first["source_id"]]
        record["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        before = json.loads(json.dumps(record, sort_keys=True))
        other = self.raw_file(name="other.md", text="# Article 113 - Entry into force\n\nText.\n")
        result = self.register(source_id=first["source_id"], raw_file=other)
        self.assertEqual(
            [f"source_is_code_saved: {first['source_id']} holds text saved by mf sources save"],
            result["errors"],
        )
        self.assertEqual(first["source_id"], result["source_id"])
        self.assertIn("mf sources save", result["hint"])
        after = sources.read_registry(self.work_dir)["sources"][first["source_id"]]
        self.assertEqual(before, json.loads(json.dumps(after, sort_keys=True)))
        self.assertTrue(other.exists(), "a refused registration does not consume the raw file")
        stored = (self.work_dir / after["raw_path"]).read_text(encoding="utf-8")
        self.assertEqual(RAW_TEXT, stored)


class PackRawKindIntegrityTest(SourcesTestCase):
    """D-200: the freeze believes the file — an edited or deleted `full_text` file is demoted."""

    def _register_full_text(self, source_id: str = "gdpr-article-6") -> dict:
        result = self.register(raw_file=self.raw_file(), source_id=source_id)
        registry = sources.read_registry(self.work_dir)
        registry["sources"][result["source_id"]]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        return result

    def test_a_demoted_duplicate_loses_canonicality_to_the_copy_with_text(self):
        """Fix round 2: the integrity pass runs before duplicate selection, so a `full_text`
        record whose file vanished cannot win canonicality over the usable copy."""
        intact = self.register(
            source_id="dup-intact",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="dup-a.md", text="Processing in the context of employment\n"),
        )
        lost = self.register(
            source_id="dup-lost",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="dup-b.md", text="Processing in the context of employment\n"),
        )
        registry = sources.read_registry(self.work_dir)
        registry["sources"][lost["source_id"]]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        (self.work_dir / lost["raw_path"]).unlink()
        self.write_findings([{"source_id": intact["source_id"]}])
        answer = self.freeze()
        pack = sources.read_pack(self.work_dir)
        self.assertEqual({"dup-lost": "dup-intact"}, pack["merged_into"])
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": "dup-lost",
                "was": "full_text",
                "now": "none",
            },
            answer["warnings"],
        )
        snapshot = {row["source_id"]: row["raw_sha256"] for row in pack["snapshot"]}
        self.assertEqual(
            state_io.sha256_file(self.work_dir / intact["raw_path"]), snapshot["dup-intact"]
        )

    def test_an_edited_full_text_file_freezes_as_agent_summary_with_a_warning(self):
        from memoforge import schema

        result = self._register_full_text()
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": result["source_id"]}])
        answer = self.freeze()
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": result["source_id"],
                "was": "full_text",
                "now": "agent_summary",
            },
            answer["warnings"],
        )
        pack = sources.read_pack(self.work_dir)
        self.assertEqual([], schema.validate(pack, "source-pack"))
        entry = next(row for row in pack["entries"] if row["source_id"] == result["source_id"])
        self.assertEqual("agent_summary", entry["raw_kind"])
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertEqual("agent_summary", record["raw_kind"])
        self.assertEqual(state_io.sha256_file(stored), record["raw_sha256"])

    def test_a_deleted_full_text_file_freezes_as_none_with_a_warning(self):
        result = self._register_full_text()
        (self.work_dir / result["raw_path"]).unlink()
        self.write_findings([{"source_id": result["source_id"]}])
        answer = self.freeze()
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": result["source_id"],
                "was": "full_text",
                "now": "none",
            },
            answer["warnings"],
        )
        pack_entries = sources.read_pack(self.work_dir)["entries"]
        entry = next(row for row in pack_entries if row["source_id"] == result["source_id"])
        self.assertEqual("none", entry["raw_kind"])
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertEqual("none", record["raw_kind"])

    def test_a_full_text_record_with_no_recorded_digest_freezes_as_agent_summary(self):
        """Fix round 1: a null recorded digest never equals the file's digest — unverified bytes
        must not be stamped as code-saved text."""
        from memoforge import schema

        result = self._register_full_text()
        registry = sources.read_registry(self.work_dir)
        registry["sources"][result["source_id"]]["raw_sha256"] = None
        sources.write_registry(self.work_dir, registry)
        self.write_findings([{"source_id": result["source_id"]}])
        answer = self.freeze()
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": result["source_id"],
                "was": "full_text",
                "now": "agent_summary",
            },
            answer["warnings"],
        )
        pack = sources.read_pack(self.work_dir)
        self.assertEqual([], schema.validate(pack, "source-pack"))
        pack_entries = pack["entries"]
        entry = next(row for row in pack_entries if row["source_id"] == result["source_id"])
        self.assertEqual("agent_summary", entry["raw_kind"])
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertEqual("agent_summary", record["raw_kind"])
        self.assertEqual(
            state_io.sha256_file(self.work_dir / result["raw_path"]), record["raw_sha256"]
        )

    def test_an_untouched_full_text_file_freezes_quietly(self):
        result = self._register_full_text()
        self.write_findings([{"source_id": result["source_id"]}])
        answer = self.freeze()
        self.assertEqual([], answer["warnings"])
        pack_entries = sources.read_pack(self.work_dir)["entries"]
        entry = next(row for row in pack_entries if row["source_id"] == result["source_id"])
        self.assertEqual("full_text", entry["raw_kind"])

    def test_the_snapshot_reuses_the_classified_digest(self):
        """Fix round 2: one observation per file — bytes edited between classification and the
        snapshot write cannot end up pinned as code-saved text."""
        result = self._register_full_text()
        raw_path = str(self.work_dir / result["raw_path"])
        real = state_io.sha256_file
        classified = real(raw_path)
        calls: list[str] = []

        def spy(path):
            calls.append(str(path))
            if str(path) == raw_path and calls.count(raw_path) > 1:
                return "0" * 64  # bytes the integrity pass never saw
            return real(path)

        with mock.patch.object(state_io, "sha256_file", side_effect=spy):
            self.write_findings([{"source_id": result["source_id"]}])
            answer = self.freeze()
        self.assertEqual(1, calls.count(raw_path))
        self.assertEqual([], answer["warnings"])
        pack = sources.read_pack(self.work_dir)
        snapshot = {row["source_id"]: row["raw_sha256"] for row in pack["snapshot"]}
        self.assertEqual(classified, snapshot[result["source_id"]])
        entry = next(row for row in pack["entries"] if row["source_id"] == result["source_id"])
        self.assertEqual("full_text", entry["raw_kind"])

    def test_a_non_full_text_record_is_untouched_by_the_integrity_check(self):
        result = self.register(raw_file=self.raw_file())
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": result["source_id"]}])
        answer = self.freeze()
        self.assertEqual([], answer["warnings"])
        pack_entries = sources.read_pack(self.work_dir)["entries"]
        entry = next(row for row in pack_entries if row["source_id"] == result["source_id"])
        self.assertEqual("agent_summary", entry["raw_kind"])

    def test_an_old_registry_without_raw_kind_freezes_byte_for_byte(self):
        import copy

        result = self.register(raw_file=self.raw_file())
        # An old work dir never heard of `raw_kind`: strip what `store_raw` wrote.
        registry = sources.read_registry(self.work_dir)
        del registry["sources"][result["source_id"]]["raw_kind"]
        sources.write_registry(self.work_dir, registry)
        expected = copy.deepcopy(sources.read_registry(self.work_dir)["sources"][result["source_id"]])
        self.assertNotIn("raw_kind", expected)
        expected.pop("pack")
        expected.pop("currency")
        self.write_findings([{"source_id": result["source_id"]}])
        self.freeze()
        pack = sources.read_pack(self.work_dir)
        entry = next(row for row in pack["entries"] if row["source_id"] == result["source_id"])
        self.assertNotIn("raw_kind", entry)
        record = sources.read_registry(self.work_dir)["sources"][result["source_id"]]
        self.assertNotIn("raw_kind", record)
        for key, value in expected.items():
            self.assertEqual(value, record[key], key)

    def test_pack_raw_kind_reads_the_pack_entry_first(self):
        self.assertEqual("full_text", sources.pack_raw_kind({"raw_kind": "full_text"}))
        self.assertEqual("excerpt", sources.pack_raw_kind({"raw_kind": "excerpt"}, "a" * 64))
        self.assertEqual("agent_summary", sources.pack_raw_kind({}, "a" * 64))
        self.assertEqual("none", sources.pack_raw_kind({}))
        self.assertEqual("none", sources.pack_raw_kind({}, None))

    def test_canonical_of_prefers_the_full_text_member(self):
        first = self.register(
            source_id="gdpr-art88-a",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="a88a.md", text="Processing in the context of employment\n"),
        )
        second = self.register(
            source_id="gdpr-art88-b",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="a88b.md", text="Processing in the context of employment\n"),
        )
        registry = sources.read_registry(self.work_dir)
        registry["sources"][second["source_id"]]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        group = sorted(registry["sources"])
        canonical = sources.canonical_of(registry["sources"], group)
        self.assertEqual(second["source_id"], canonical)
        self.assertEqual(first["source_id"], sorted(group)[0])

    def test_canonical_of_still_prefers_a_correct_label_over_a_mislabelled_full_text(self):
        self.register(
            source_id="gdpr-article-3-territorial-scope",
            title="GDPR Article 88 (Processing in the context of employment)",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="m1.md", text="Processing in the context of employment\n"),
        )
        self.register(
            source_id="gdpr-art88",
            title="GDPR Article 88",
            citation="Regulation (EU) 2016/679 (GDPR), art 88",
            url="https://eur-lex.europa.eu/eli/reg/2016/679/oj#art_88",
            identifiers={"celex": "32016R0679"},
            raw_file=self.raw_file(name="m2.md", text="Processing in the context of employment\n"),
        )
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-3-territorial-scope"]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        group = sorted(registry["sources"])
        self.assertEqual("gdpr-art88", sources.canonical_of(registry["sources"], group))


class FullTextIntegrityDigestTest(SourcesTestCase):
    """D-200: the freeze warning also surfaces in the gate-11 digest."""

    def test_the_integrity_warning_appears_in_the_gate_11_digest(self):
        result = self.register(raw_file=self.raw_file(), source_id="gdpr-article-6")
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze()
        rendered = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertTrue(rendered["has_exceptions"])
        self.assertIn("full_text_integrity", {row["kind"] for row in rendered["exceptions"]})
        self.assertIn("gdpr-article-6", rendered["text"])

    def test_a_replayed_freeze_answers_with_the_warning_from_the_pack(self):
        """Fix round 2: the evidence lives in the frozen pack, so the replay recovers it."""
        result = self.register(raw_file=self.raw_file(), source_id="gdpr-article-6")
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze()

        def rollback(state: dict) -> None:
            state["sources_frozen"] = False
            state["steps"] = []
            state["published"] = []

        state_io.write_state(self.work_dir, rollback)
        answer = self.freeze(step="s-010", attempt=2)
        self.assertTrue(answer["replayed"])
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": "gdpr-article-6",
                "was": "full_text",
                "now": "agent_summary",
            },
            answer["warnings"],
        )

    def test_an_interrupted_freeze_retried_before_publication_still_warns(self):
        """Fix round 2: a crash before anything was written leaves the record intact, so the
        retry classifies it fresh and warns."""
        result = self.register(raw_file=self.raw_file(), source_id="gdpr-article-6")
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": "gdpr-article-6"}])
        real_write = sources.write_registry
        with mock.patch.object(sources, "write_registry", side_effect=[RuntimeError("crash"), None]) as patched:
            with self.assertRaises(RuntimeError):
                self.freeze()

            def passthrough(work_dir, registry):
                return real_write(work_dir, registry)

            patched.side_effect = passthrough
            answer = self.freeze()
        self.assertIn(
            {
                "code": "full_text_integrity",
                "source_id": "gdpr-article-6",
                "was": "full_text",
                "now": "agent_summary",
            },
            answer["warnings"],
        )
        pack = sources.read_pack(self.work_dir)
        self.assertEqual(
            [
                {
                    "code": "full_text_integrity",
                    "source_id": "gdpr-article-6",
                    "was": "full_text",
                    "now": "agent_summary",
                }
            ],
            pack["integrity_warnings"],
        )

    def test_a_crash_between_registry_write_and_publication_loses_no_warning_on_retry(self):
        """Fix round 3: the evidence is stamped into the record in the same write as the
        demotion, so the retry re-emits the warning even though the record no longer classifies."""
        from memoforge import stepctx

        result = self.register(raw_file=self.raw_file(), source_id="gdpr-article-6")
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": "gdpr-article-6"}])
        real_publish = stepctx.publish_file
        calls: list[str] = []

        def crash_once(work_dir, work_path, canonical_path, **kwargs):
            calls.append(str(canonical_path))
            if len(calls) == 1:
                raise RuntimeError("crash before publication")
            return real_publish(work_dir, work_path, canonical_path, **kwargs)

        with mock.patch.object(stepctx, "publish_file", side_effect=crash_once):
            with self.assertRaises(RuntimeError):
                self.freeze()
        self.assertIsNone(sources.read_pack(self.work_dir))
        demoted = sources.read_registry(self.work_dir)["sources"]["gdpr-article-6"]
        self.assertEqual("agent_summary", demoted["raw_kind"])
        self.assertEqual(
            {"was": "full_text", "now": "agent_summary"},
            (demoted.get("meta") or {}).get("full_text_integrity"),
        )
        answer = self.freeze()
        expected = {
            "code": "full_text_integrity",
            "source_id": "gdpr-article-6",
            "was": "full_text",
            "now": "agent_summary",
        }
        self.assertIn(expected, answer["warnings"])
        pack = sources.read_pack(self.work_dir)
        self.assertEqual([expected], pack["integrity_warnings"])
        rendered = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertIn("full_text_integrity", {row["kind"] for row in rendered["exceptions"]})
        record = sources.read_registry(self.work_dir)["sources"]["gdpr-article-6"]
        self.assertEqual("agent_summary", record["raw_kind"])

    def test_a_string_result_ref_renders_the_digest_without_raising(self):
        """Fix round 2: `result_ref` may be a plain string (state schema allows it); the digest
        reads warnings from the pack, so the shape must never break it."""
        result = self.register(raw_file=self.raw_file(), source_id="gdpr-article-6")
        registry = sources.read_registry(self.work_dir)
        registry["sources"]["gdpr-article-6"]["raw_kind"] = "full_text"
        sources.write_registry(self.work_dir, registry)
        stored = self.work_dir / result["raw_path"]
        stored.write_bytes((RAW_TEXT + "An agent appended a sentence.\n").encode("utf-8"))
        self.write_findings([{"source_id": "gdpr-article-6"}])
        self.freeze()
        state = state_io.read_state(self.work_dir)
        state["steps"] = [{"step_id": "s-005", "result_ref": "research/statutes.json"}]
        rendered = sources.render_digest(self.work_dir, state, True)
        self.assertTrue(rendered["has_exceptions"])
        self.assertIn("full_text_integrity", {row["kind"] for row in rendered["exceptions"]})


if __name__ == "__main__":
    unittest.main()
