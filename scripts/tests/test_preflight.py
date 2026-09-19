"""Tests for scripts/memoforge/preflight.py — `mf sources preflight` (ТЗ §2.1 стр.3, §4.3, D-147)."""

from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Driver, temp_root  # noqa: E402
from memoforge import gates, i18n, machine, preflight, routing, schema, state_io  # noqa: E402

import _i18n  # noqa: E402

DOCUMENT = b"<html><body>" + b"Article 5 of the fixture instrument applies here. " * 700 + b"</body></html>"
"""A real document: over `LIVENESS_MIN_BODY_BYTES` and almost all visible text (D-146)."""

STUB = b"<html><body>Request Access. Programmatic access is limited to our developer APIs.</body></html>"
"""The eCFR-shaped false-positive 200 of analysis/38 §5.1 — short, genuine text, not the document."""


# --- mock http server (the `test_sources._Handler` pattern; localhost is not the network) ---


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - silence the test output
        return

    challenges = {
        "/waf": (202, {"x-amzn-waf-action": "challenge"}),
        "/cloudflare": (403, {"Cf-Mitigated": "challenge"}),
    }

    seen: list = []
    """Every request the server received, so a test can assert that offline mode sent none."""

    def _payload(self) -> tuple[int, bytes]:
        if self.path == "/ok":
            return 200, DOCUMENT
        if self.path == "/stub":
            return 200, STUB
        if self.path in self.challenges:
            return self.challenges[self.path][0], b""
        return 404, b"missing"

    def _respond(self, with_body: bool) -> None:
        type(self).seen.append(self.path)
        code, payload = self._payload()
        self.send_response(code)
        for name, value in self.challenges.get(self.path, (None, {}))[1].items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if with_body:
            self.wfile.write(payload)

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._respond(False)

    def do_GET(self):  # noqa: N802
        self._respond(True)


class LocalServer:
    """`http.server` on localhost — not an external network call (§9 allows the mock)."""

    def __init__(self) -> None:
        _Handler.seen = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        # `poll_interval` is what `shutdown()` waits for; the default 0.5 s is pure sleep per test.
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def local_table(base: str) -> dict:
    """`PREFLIGHT_URLS` pointed at the mock server, one fake host per outcome."""
    return {
        "answering.example": f"{base}/ok",
        "waf.example": f"{base}/waf",
        "turnstile.example": f"{base}/cloudflare",
        "stub.example": f"{base}/stub",
        "gone.example": f"{base}/missing",
    }


class ClassificationTest(unittest.TestCase):
    """D-147: every host gets one of the six statuses, and a probe error is one of them."""

    def probe(self, host: str) -> dict:
        with LocalServer() as base:
            with mock.patch.dict(preflight.PREFLIGHT_URLS, local_table(base), clear=True):
                with mock.patch.dict(os.environ, {preflight.OFFLINE_ENV: ""}):
                    return preflight.probe_host(host, timeout=5)

    def test_a_served_document_is_ok(self):
        row = self.probe("answering.example")
        self.assertEqual("ok", row["status"])
        self.assertEqual(200, row["code"])
        self.assertIsNone(row["error"])

    def test_an_aws_waf_challenge_is_named(self):
        row = self.probe("waf.example")
        self.assertEqual("waf_challenge", row["status"])
        self.assertEqual(202, row["code"])
        self.assertEqual("aws_waf_challenge", row["error"])

    def test_a_cloudflare_turnstile_is_named(self):
        row = self.probe("turnstile.example")
        self.assertEqual("cloudflare", row["status"])
        self.assertEqual(403, row["code"])

    def test_a_200_that_is_not_the_document_is_an_interstitial(self):
        """analysis/38 §5.1: eCFR answers 200 with a «Request Access» stub — never `ok`."""
        row = self.probe("stub.example")
        self.assertEqual("interstitial", row["status"])
        self.assertEqual(200, row["code"])

    def test_a_missing_page_is_dead(self):
        row = self.probe("gone.example")
        self.assertEqual("dead", row["status"])
        self.assertEqual(404, row["code"])

    def test_a_certificate_failure_is_its_own_status(self):
        """D-146 names the TLS failure; D-147 keeps it apart from «the site is gone»."""
        self.assertEqual("tls", preflight.classify({"status": "dead", "error": "tls_certificate"}))

    def test_a_redirect_still_counts_as_answering(self):
        self.assertEqual("ok", preflight.classify({"status": "redirect", "code": 200}))

    def test_a_raising_probe_is_a_status_not_a_failure(self):
        with mock.patch.dict(preflight.PREFLIGHT_URLS, {"boom.example": "https://boom.example/x"}):
            with mock.patch.object(preflight.sources, "probe_url", side_effect=OSError("boom")):
                row = preflight.probe_host("boom.example")
        self.assertEqual("dead", row["status"])
        self.assertEqual("OSError", row["error"])

    def test_every_blocked_host_carries_a_routed_alternative(self):
        row = self.probe("waf.example")
        self.assertIsNone(row["alternative"], "the fake host has no routing row")
        self.assertTrue(preflight.PREFLIGHT_ALTERNATIVES["eur-lex.europa.eu"])


class OfflineTest(unittest.TestCase):
    """D-147: `MEMOFORGE_OFFLINE=1` answers `ok` for every host and opens no socket."""

    def test_offline_mode_sends_nothing_and_answers_ok(self):
        with LocalServer() as base:
            with mock.patch.dict(preflight.PREFLIGHT_URLS, local_table(base), clear=True):
                with mock.patch.dict(os.environ, {preflight.OFFLINE_ENV: "1"}):
                    rows = preflight.probe_hosts(sorted(local_table(base)))
        self.assertEqual([], _Handler.seen)
        self.assertEqual({"ok"}, {row["status"] for row in rows})

    def test_the_flag_is_read_from_the_environment(self):
        with mock.patch.dict(os.environ, {preflight.OFFLINE_ENV: "yes"}):
            self.assertTrue(preflight.offline())
        with mock.patch.dict(os.environ, {preflight.OFFLINE_ENV: "0"}):
            self.assertFalse(preflight.offline())


class UrlTableTest(unittest.TestCase):
    """The table is the whole contract: a routed domain without an entry is never probed."""

    def routed_domains(self) -> set:
        domains = set()
        for table in (routing.ROUTING, routing.MEMBER_STATE_ROWS):
            for rows in table.values():
                for row in rows.values():
                    domains.update(row.get("domains") or [])
        return domains

    def test_every_routed_domain_has_a_representative_url(self):
        missing = sorted(self.routed_domains() - set(preflight.PREFLIGHT_URLS))
        self.assertEqual([], missing, "add the domain to `preflight.PREFLIGHT_URLS`")

    def test_every_url_is_https_and_names_its_own_host(self):
        for host, url in preflight.PREFLIGHT_URLS.items():
            with self.subTest(host=host):
                self.assertTrue(url.startswith("https://"), url)
                probed = preflight.sources.request_host(url)
                self.assertTrue(probed == host or probed.endswith("." + host), f"{probed} != {host}")

    def test_every_host_has_a_routed_alternative(self):
        self.assertEqual(
            sorted(preflight.PREFLIGHT_URLS), sorted(preflight.PREFLIGHT_ALTERNATIVES)
        )

    def test_every_alternative_names_something_the_routing_table_knows(self):
        """D-151: an alternative that names no routed host, tool or server has drifted from §4.3.

        The IT line said «a pinpoint needs a session, considered_excluded» long after D-148 routed
        the article through the official OpenAPI, and that sentence reached the user in the gate
        digest and in `${source_access}`.
        """
        known: set = {"mf sources fetch", "WebFetch", "WebSearch"}
        for layer in routing.LAYERS:
            for code in list(routing.ROUTING[layer]) + list(routing.MEMBER_STATES):
                row = routing.route(layer, code)
                known.update(row["tools"])
                known.update(row["domains"])
        known.update(routing.MCP_SERVER_LABELS.values())
        known.update(f"{alias}_" for alias in routing.MCP_SERVERS)
        vocabulary = sorted(token.lower() for token in known if len(token) >= 4)
        for host, alternative in sorted(preflight.PREFLIGHT_ALTERNATIVES.items()):
            with self.subTest(host=host):
                text = alternative.lower()
                named = [token for token in vocabulary if token in text]
                self.assertTrue(named, f"{host}: {alternative!r} names no routed tool or host")

    def test_the_italian_alternative_is_the_open_data_route_of_the_routing_note(self):
        alternative = preflight.PREFLIGHT_ALTERNATIVES["normattiva.it"]
        self.assertIn("api.normattiva.it", alternative)
        self.assertIn("mf sources fetch --method POST --json", alternative)
        self.assertNotIn("considered_excluded", alternative)
        note = routing.route("statutes", "IT")["note"]
        self.assertIn("api.normattiva.it", note)
        self.assertIn("--method POST --json", note)


class PlanHostsTest(unittest.TestCase):
    """§4.3: the hosts of `layers x jurisdictions`, plus the member states behind a generic plan."""

    def plan(self, *codes: str, doctrine: bool = True) -> dict:
        return {"jurisdictions": list(codes), "doctrine_required": doctrine, "issues": []}

    def test_an_eu_plan_takes_the_eu_portals_only(self):
        hosts = preflight.plan_hosts(self.plan("EU"))
        self.assertIn("publications.europa.eu", hosts)
        self.assertIn("eur-lex.europa.eu", hosts)
        self.assertIn("edpb.europa.eu", hosts)
        self.assertNotIn("legislation.gov.uk", hosts)
        self.assertNotIn("legifrance.gouv.fr", hosts)

    def test_a_member_state_contributes_its_own_portal(self):
        hosts = preflight.plan_hosts(self.plan("DE", "FR"))
        self.assertIn("gesetze-im-internet.de", hosts)
        self.assertIn("legifrance.gouv.fr", hosts)
        self.assertIn("cnil.fr", hosts)

    def test_a_plan_without_doctrine_skips_the_regulator_portals(self):
        hosts = preflight.plan_hosts(self.plan("UK", doctrine=False))
        self.assertIn("legislation.gov.uk", hosts)
        self.assertNotIn("ico.org.uk", hosts)

    def test_a_generic_member_state_plan_still_reaches_the_national_portals(self):
        """D-132: «EEA member states» names no ISO code, and that is where retrieval fails."""
        hosts = preflight.plan_hosts(self.plan("EEA member states"))
        self.assertIn("legifrance.gouv.fr", hosts)
        self.assertIn("normattiva.it", hosts)
        self.assertIn("publications.europa.eu", hosts)

    def test_an_unknown_jurisdiction_adds_no_host(self):
        self.assertEqual([], preflight.plan_hosts(self.plan("ZZ")))

    def test_each_host_appears_once(self):
        hosts = preflight.plan_hosts(self.plan("EU", "US"))
        self.assertEqual(len(hosts), len(set(hosts)))


class CommandTest(unittest.TestCase):
    """§3.1: `mf sources preflight` is an ordinary script step — identity, publish, replay."""

    def setUp(self) -> None:
        self.driver = Driver(temp_root(self), slug="preflight")
        self.action = None
        for _ in range(20):
            action = self.driver.next()
            self.assertNotIn("errors", action, action)
            if action.get("kind") == "script" and machine.command_key(action["command"]) == "sources.preflight":
                self.action = action
                break
            self.driver.act(action)
        self.assertIsNotNone(self.action, "the run never reached `sources preflight`")

    def run_step(self) -> dict:
        return machine.run_command(list(self.action["command"]))

    def test_the_step_publishes_a_schema_valid_document(self):
        result = self.run_step()
        self.assertEqual(preflight.PREFLIGHT_PATH, result["preflight_path"])
        document = state_io.read_json(self.driver.work_dir / preflight.PREFLIGHT_PATH)
        self.assertEqual([], schema.validate(document, "preflight"))
        published = [
            row["canonical_path"] for row in self.driver.state()["published"]
        ]
        self.assertIn(preflight.PREFLIGHT_PATH, published)

    def test_the_fixture_plan_names_the_eu_hosts(self):
        result = self.run_step()
        document = state_io.read_json(self.driver.work_dir / preflight.PREFLIGHT_PATH)
        self.assertEqual(result["hosts"], len(document["hosts"]))
        self.assertIn("publications.europa.eu", [row["host"] for row in document["hosts"]])
        self.assertEqual([], result["blocked"])

    def test_a_replay_of_the_closed_identity_is_a_no_op(self):
        first = self.run_step()
        second = self.run_step()
        self.assertTrue(second["already_done"])
        self.assertEqual(first["hosts"], second["hosts"])

    def test_a_stale_attempt_is_an_identity_mismatch(self):
        self.run_step()
        command = machine.with_identity(list(self.action["command"]), self.action["step_id"], 9)
        self.assertEqual(["identity_mismatch"], machine.run_command(command)["errors"])


class SourceAccessTest(unittest.TestCase):
    """D-147: the `Source access today:` block names the host and the alternative, nothing else."""

    def work_dir(self, rows: list[dict] | None) -> Path:
        holder = tempfile.TemporaryDirectory(prefix="mf-preflight-")
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        if rows is not None:
            state_io.write_json_atomic(
                root / preflight.PREFLIGHT_PATH,
                {
                    "schema_version": 1,
                    "checked_at": "2026-09-13T06:05:00Z",
                    "offline": False,
                    "hosts": rows,
                },
            )
        return root

    def row(self, host: str, status: str) -> dict:
        return {
            "host": host,
            "url": preflight.PREFLIGHT_URLS.get(host, "https://example.org/"),
            "status": status,
            "code": None,
            "error": None,
            "alternative": preflight.PREFLIGHT_ALTERNATIVES.get(host),
        }

    def test_a_clean_preflight_prints_no_block(self):
        root = self.work_dir([self.row("publications.europa.eu", "ok")])
        self.assertEqual("", preflight.source_access_block(root, {}))
        self.assertEqual(preflight.CLEAN_LINE, preflight.source_access_line(root, {}))

    def test_a_blocked_host_names_its_alternative(self):
        root = self.work_dir(
            [
                self.row("publications.europa.eu", "ok"),
                self.row("eur-lex.europa.eu", "waf_challenge"),
                self.row("legifrance.gouv.fr", "cloudflare"),
            ]
        )
        block = preflight.source_access_block(root, {})
        self.assertIn("Source access today:", block)
        self.assertIn("- eur-lex.europa.eu: WAF challenge → Cellar", block)
        self.assertIn(
            "- legifrance.gouv.fr: Cloudflare block → "
            + preflight.PREFLIGHT_ALTERNATIVES["legifrance.gouv.fr"],
            block,
        )
        self.assertNotIn("- publications.europa.eu", block, "an answering host is not a line")

    def test_the_prompt_form_is_one_line(self):
        root = self.work_dir(
            [self.row("eur-lex.europa.eu", "waf_challenge"), self.row("ftc.gov", "dead")]
        )
        line = preflight.source_access_line(root, {})
        self.assertEqual(1, len(line.splitlines()))
        self.assertIn("eur-lex.europa.eu: WAF challenge", line)
        self.assertIn("ftc.gov: did not answer", line)

    def test_a_long_block_is_capped_and_points_at_the_file(self):
        rows = [self.row(host, "dead") for host in sorted(preflight.PREFLIGHT_URLS)]
        block = preflight.source_access_block(self.work_dir(rows), {})
        self.assertEqual(preflight.MAX_BLOCK_LINES + 2, len(block.splitlines()))
        self.assertIn(f"more in `{preflight.PREFLIGHT_PATH}`", block)

    def test_without_a_preflight_file_the_prompt_says_so(self):
        root = self.work_dir(None)
        self.assertEqual(preflight.UNKNOWN_LINE, preflight.source_access_line(root, {}))
        self.assertEqual("", preflight.source_access_block(root, {}))


class McpStatusTest(unittest.TestCase):
    """D-147: a connected server that failed its smoke call is treated like an exhausted quota."""

    def probe(self, status: dict | None) -> dict:
        document = {
            "namespaces": {
                "ldh": "mcp__ldh",
                "courtlistener": "mcp__cl",
                "legalviz": "mcp__lv",
                "uklegal": "mcp__uk",
                "other": [],
            }
        }
        if status is not None:
            document["status"] = status
        return document

    def test_a_probe_without_status_is_read_as_it_always_was(self):
        usable = preflight.usable_namespaces(self.probe(None))
        self.assertEqual({"ldh", "courtlistener", "legalviz", "uklegal", "other"}, set(usable))

    def test_a_quota_refusal_removes_the_server(self):
        usable = preflight.usable_namespaces(
            self.probe({"ldh": "quota", "legalviz": "ok", "courtlistener": "auth", "uklegal": "error"})
        )
        self.assertEqual({"legalviz", "other"}, set(usable))

    def test_a_failed_server_leaves_every_line_of_the_routing_digest(self):
        usable = preflight.usable_namespaces(self.probe({"ldh": "quota", "legalviz": "ok"}))
        digest = routing.routing_digest(usable)
        self.assertNotIn("ldh_", digest)
        self.assertIn("legalviz_resolve", digest)

    def test_an_absent_server_is_not_usable(self):
        usable = preflight.usable_namespaces(self.probe({"legalviz": "absent"}))
        self.assertNotIn("legalviz", usable)

    def test_a_malformed_probe_answers_an_empty_mapping(self):
        self.assertEqual({}, preflight.usable_namespaces(None))
        self.assertEqual({}, preflight.usable_namespaces({"namespaces": "nope"}))


class PublishedBytesTest(unittest.TestCase):
    """D-41: a preflight file edited after publication is not read as a verdict."""

    def test_a_drifted_file_is_not_read(self):
        driver = Driver(temp_root(self), slug="preflight-drift")
        driver.run_until("plan_approval_pending")
        path = driver.work_dir / preflight.PREFLIGHT_PATH
        self.assertTrue(path.is_file())
        state = driver.state()
        self.assertIsNotNone(preflight.read_preflight(driver.work_dir, state))
        path.write_text('{"schema_version": 1, "checked_at": "x", "hosts": []}', encoding="utf-8")
        self.assertIsNone(preflight.read_preflight(driver.work_dir, state))
        self.assertEqual(preflight.UNKNOWN_LINE, preflight.source_access_line(driver.work_dir, state))


class GateDigestTest(unittest.TestCase):
    """§2.4: gate 4 reads the preflight; a clean one adds nothing to the digest."""

    def test_a_clean_preflight_leaves_the_digest_alone(self):
        driver = Driver(temp_root(self), slug="preflight-digest")
        driver.run_until("plan_approval_pending")
        digest = gates.render_plan_digest(driver.work_dir, driver.state())
        self.assertNotIn("Source access today:", digest)

    def test_the_blocked_hosts_reach_the_digest(self):
        driver = Driver(temp_root(self), slug="preflight-digest-blocked")
        driver.run_until("plan_approval_pending")
        state = driver.state()
        with mock.patch.object(
            preflight,
            "blocked_hosts",
            return_value=[
                {
                    "host": "eur-lex.europa.eu",
                    "status": "waf_challenge",
                    "alternative": preflight.PREFLIGHT_ALTERNATIVES["eur-lex.europa.eu"],
                }
            ],
        ):
            digest = gates.render_plan_digest(driver.work_dir, state)
        self.assertIn("Source access today:", digest)
        self.assertIn("eur-lex.europa.eu: WAF challenge → Cellar", digest)


class RegistrationTest(unittest.TestCase):
    """§5.2: the command lives in the `sources` group and takes the identity options of §3.1."""

    def test_the_command_is_registered(self):
        from memoforge import cli

        parser = cli.build_parser()
        args = parser.parse_args(
            ["sources", "preflight", "--workdir", "W", "--step", "s-1", "--attempt", "2"]
        )
        self.assertIs(preflight.run_preflight, args.func)
        self.assertEqual(2, args.attempt)

    def test_a_plan_that_cannot_be_read_probes_nothing(self):
        """D-99: an unusable plan names no jurisdiction, so the preflight has nothing to ask."""
        self.assertEqual([], preflight.plan_hosts({}))
        self.assertEqual([], preflight.build_document([])["hosts"])


class SourceAccessLanguageTest(unittest.TestCase):
    """D-176 (sources/preflight): the gate-visible preflight block speaks the UI language.

    The agent prompt value (`source_access_line`, `CLEAN_LINE`/`UNKNOWN_LINE`) keeps the
    English text — only the gate-visible block is localized, read in the language the
    caller passes (`ui`, default `"en"`).
    """

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="mf-preflight-ui-")
        self.addCleanup(holder.cleanup)
        self.packs = Path(holder.name) / "packs"
        self.packs.mkdir()
        patcher = mock.patch.object(i18n, "PACK_DIR", self.packs)
        patcher.start()
        self.addCleanup(patcher.stop)
        _i18n.fake_pack(self.packs, "ru", _i18n.RU_UI)
        self.root = Path(holder.name) / "work"
        self.root.mkdir()
        state_io.write_json_atomic(
            self.root / preflight.PREFLIGHT_PATH,
            {
                "schema_version": 1,
                "checked_at": "2026-09-13T06:05:00Z",
                "offline": False,
                "hosts": [
                    {
                        "host": "eur-lex.europa.eu",
                        "url": preflight.PREFLIGHT_URLS["eur-lex.europa.eu"],
                        "status": "waf_challenge",
                        "code": None,
                        "error": None,
                        "alternative": preflight.PREFLIGHT_ALTERNATIVES["eur-lex.europa.eu"],
                    },
                    {
                        "host": "ftc.gov",
                        "url": preflight.PREFLIGHT_URLS["ftc.gov"],
                        "status": "dead",
                        "code": None,
                        "error": None,
                        "alternative": preflight.PREFLIGHT_ALTERNATIVES["ftc.gov"],
                    },
                ],
            },
        )

    def test_english_block_is_todays_bytes(self):
        block = preflight.source_access_block(self.root, {})
        self.assertIn("Source access today:", block)
        self.assertIn("- eur-lex.europa.eu: WAF challenge → Cellar", block)
        self.assertIn("- ftc.gov: did not answer → WebSearch", block)

    def test_the_russian_block_localizes_head_and_labels_but_not_alternatives(self):
        block = preflight.source_access_block(self.root, {}, ui="ru")
        self.assertIn("Доступ к источникам сегодня:", block)
        self.assertNotIn("Source access today:", block)
        self.assertIn(
            "- eur-lex.europa.eu: WAF-проверка → "
            + preflight.PREFLIGHT_ALTERNATIVES["eur-lex.europa.eu"],
            block,
        )
        self.assertIn(
            "- ftc.gov: не ответил → " + preflight.PREFLIGHT_ALTERNATIVES["ftc.gov"], block
        )

    def test_the_prompt_line_stays_english(self):
        line = preflight.source_access_line(self.root, {})
        self.assertEqual(1, len(line.splitlines()))
        self.assertIn("eur-lex.europa.eu: WAF challenge", line)
        self.assertIn(preflight.PREFLIGHT_ALTERNATIVES["eur-lex.europa.eu"], line)
        clean = Path(self.root) / "clean"
        clean.mkdir()
        state_io.write_json_atomic(
            clean / preflight.PREFLIGHT_PATH,
            {
                "schema_version": 1,
                "checked_at": "2026-09-13T06:05:00Z",
                "offline": False,
                "hosts": [],
            },
        )
        self.assertEqual(preflight.CLEAN_LINE, preflight.source_access_line(clean, {}))

    def test_a_long_russian_block_is_capped_and_points_at_the_file(self):
        rows = [
            {
                "host": host,
                "url": preflight.PREFLIGHT_URLS[host],
                "status": "dead",
                "code": None,
                "error": None,
                "alternative": preflight.PREFLIGHT_ALTERNATIVES[host],
            }
            for host in sorted(preflight.PREFLIGHT_URLS)
        ]
        state_io.write_json_atomic(
            self.root / preflight.PREFLIGHT_PATH,
            {
                "schema_version": 1,
                "checked_at": "2026-09-13T06:05:00Z",
                "offline": False,
                "hosts": rows,
            },
        )
        block = preflight.source_access_block(self.root, {}, ui="ru")
        self.assertEqual(preflight.MAX_BLOCK_LINES + 2, len(block.splitlines()))
        self.assertIn(f"…и ещё {len(rows) - preflight.MAX_BLOCK_LINES} — в `{preflight.PREFLIGHT_PATH}`", block)

    def test_an_unknown_status_prints_raw_in_every_language(self):
        """Sol fix round 1: a status no pack knows (`future_status`) must not crash.

        Before the fix `_row_line` called `i18n.t` unguarded (`KeyError`), where the old
        code printed the raw status via `STATUS_LABELS.get(status, status)` — both the
        localized block and the English/agent-facing prompt value are covered.
        """
        row = {
            "host": "example.org",
            "url": "https://example.org/",
            "status": "future_status",
            "code": None,
            "error": None,
            "alternative": "",
        }
        self.assertEqual("example.org: future_status", preflight._row_line(row))  # noqa: SLF001
        self.assertEqual(
            "example.org: future_status", preflight._row_line(row, "ru")  # noqa: SLF001
        )
        work = self.root / "unknown"
        work.mkdir()
        state_io.write_json_atomic(
            work / preflight.PREFLIGHT_PATH,
            {
                "schema_version": 1,
                "checked_at": "2026-09-13T06:05:00Z",
                "offline": False,
                "hosts": [row],
            },
        )
        block = preflight.source_access_block(work, {}, ui="ru")
        self.assertIn("Доступ к источникам сегодня:", block)
        self.assertIn("- example.org: future_status", block)
        line = preflight.source_access_line(work, {})
        self.assertEqual("example.org: future_status", line)


if __name__ == "__main__":
    unittest.main()
