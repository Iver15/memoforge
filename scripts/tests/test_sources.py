"""Tests for scripts/memoforge/sources.py — registry, freeze, verifications (ТЗ §5.3, M5/M6, §9)."""

from __future__ import annotations

import argparse
import http.server
import json
import multiprocessing
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

from memoforge import limits, sources, state_io, task  # noqa: E402

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

    pages = {"/stub": ECFR_STUB, "/shell": CURIA_SHELL, "/ris.json": RIS_JSON}
    """D-146: HTTP 200 answers that are not the document — plus the RIS JSON that is one (D-149)."""

    types = {"/ris.json": "application/json; charset=utf-8", "/stub": "text/html"}
    """D-149: the declared `Content-Type`, which is what `is_markup` and `fetch` read."""

    seen: list = []
    """Every request the server received, so a test can assert on the headers `probe_url` sent."""

    posted: list = []
    """D-151: the bodies of the POSTs, so a test can assert on the `--json` document that was sent."""

    locations = {"/redirect": "/ok", "/loop": "/loop", "/badhop": "ftp://evil.example/x"}
    """D-151: the `Location` of each redirecting path; `/offsite` needs the live port, see `_respond`."""

    def _payload(self) -> tuple[int, bytes]:
        if self.path == "/ok":
            return 200, type(self).body
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
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if with_body:
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

    def __init__(self, body: bytes) -> None:
        _Handler.body = body
        _Handler.seen = []
        _Handler.posted = []
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

    def test_an_explicit_source_id_is_never_suffixed(self):
        """D34-04: `<id>-2` is what put Art. 113 text under an `article-5` id in the audited run."""
        first = self.register(source_id="gdpr-art3", url="https://example.org/a")
        second = self.register(source_id="gdpr-art3", title="Other", url="https://example.org/b")
        self.assertEqual("gdpr-art3", first["source_id"])
        self.assertEqual("gdpr-art3", second["source_id"])
        self.assertNotIn("gdpr-art3-2", sources.read_registry(self.work_dir)["sources"])

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

    def test_exhausted_mcp_budget_is_an_exception(self):
        self._freeze_with()

        def mutate(state: dict) -> None:
            state["progress"]["mcp_calls"] = {"ldh": 8}

        state_io.write_state(self.work_dir, mutate)
        result = sources.render_digest(self.work_dir, state_io.read_state(self.work_dir), True)
        self.assertIn("mcp_budget_exhausted", {row["kind"] for row in result["exceptions"]})

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
        self.register(url="https://uk-legal-mcp.fly.dev/mcp", raw_file=self.raw_file(), tool="curl")
        row = self._liveness()["checked"][0]
        self.assertEqual("dead", row["status"])
        self.assertEqual("tls_certificate", row["error"])

    # --- 5. the url parser and the redirect chain (D-151) -------------------

    def test_a_url_with_credentials_is_never_probed(self):
        """D-151: the loose split read `127.0.0.1` out of `http://evil.example@127.0.0.1/`."""
        with LocalServer(LIVE_TEXT.encode("utf-8")) as base:
            authority = base.split("//", 1)[1]
            self.register(url=f"http://evil.example@{authority}/ok", raw_file=self.raw_file(), tool="curl")
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
        self.assertEqual(
            ["unsupported_scheme: file:///etc/passwd"],
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
        self.assertEqual(["userinfo_not_allowed: https://evil.example@govinfo.gov/link/uscode/15/45"], result["errors"])

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


if __name__ == "__main__":
    unittest.main()
