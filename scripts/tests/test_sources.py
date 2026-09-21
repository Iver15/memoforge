"""Tests for scripts/memoforge/sources.py — registry, freeze, verifications (ТЗ §5.3, M5/M6, §9)."""

from __future__ import annotations

import argparse
import http.server
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
    body = "\n".join((SOURCE_TEXT_FIXTURES / (name + ".txt")).read_text(encoding="utf-8") for name in names)
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

    def _payload(self) -> tuple[int, bytes]:
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
        code, payload = self._payload()
        self.send_response(code)
        if code == 302:
            # `localhost` is the same socket under another host name: an off-allowlist hop (D-151).
            offsite = f"http://localhost:{self.server.server_address[1]}/ok"
            self.send_header("Location", offsite if self.path == "/offsite" else self.locations[self.path])
        for name, value in self.challenges.get(self.path, (None, {}))[1].items():
            self.send_header(name, value)
        if self.path in self.types:
            self.send_header("Content-Type", self.types[self.path])
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
        self, body: bytes, variants: tuple = (), short_bytes: int | None = None, length_suffix: str = ""
    ) -> None:
        _Handler.body = body
        _Handler.seen = []
        _Handler.posted = []
        _Handler.variants = list(variants)
        _Handler.served = 0
        _Handler.short_bytes = short_bytes
        _Handler.length_suffix = length_suffix
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

    def test_a_resolver_is_declared_but_not_available_yet(self):
        result = self.save("", resolve="vsrf")
        self.assertEqual(["resolver_not_available: vsrf"], result["errors"])
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
