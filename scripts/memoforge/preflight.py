"""`mf sources preflight` — which routed portal answers today, asked before research (D-147)."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from . import events, i18n, limits, routing, schema, sources, state_io, stepctx

PREFLIGHT_PATH = "intake/preflight.json"
PLAN_PATH = "plan.json"
"""`PLAN_PATH` is `gates.PLAN_PATH`, repeated here: `gates` reads this module, so importing it back
would close the cycle."""

OFFLINE_ENV = "MEMOFORGE_OFFLINE"
"""`1`/`true`/`yes`/`on` → no request leaves the process and every host answers `ok` (D-147).

The fixture route of `mf probe dry-run` and the test suite set it; a real run never does.
"""

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

STATUS_OK = "ok"
STATUSES: tuple[str, ...] = ("ok", "waf_challenge", "cloudflare", "interstitial", "dead", "tls")
"""What one host can be today. Everything that is not `ok` carries a routed alternative."""

MAX_BLOCK_LINES = 8
"""How many blocked hosts the gate digest prints before it counts the rest (D-86 keeps it short)."""


# --- the static probe table ------------------------------------------------

PREFLIGHT_URLS: dict[str, str] = {
    # EU — Cellar first: it stayed up inside the EUR-Lex WAF window (analysis/38 §1.5).
    "publications.europa.eu": "https://publications.europa.eu/resource/celex/32016R0679",
    "eur-lex.europa.eu": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
    "edpb.europa.eu": (
        "https://www.edpb.europa.eu/our-work-tools/general-guidance/endorsed-wp29-guidelines_en"
    ),
    "europa.eu": "https://ec.europa.eu/newsroom/article29/redirection/document/47711",
    # UK
    "legislation.gov.uk": "https://www.legislation.gov.uk/ukpga/2018/12/section/2",
    "caselaw.nationalarchives.gov.uk": "https://caselaw.nationalarchives.gov.uk/uksc/2021/50",
    "ico.org.uk": (
        "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/"
        "lawful-basis/a-guide-to-lawful-basis/"
    ),
    # US — the year-free USC form, and the document path of CourtListener, which is WAF-guarded.
    "govinfo.gov": "https://www.govinfo.gov/link/uscode/15/45?link-type=html",
    "courtlistener.com": "https://www.courtlistener.com/opinion/108713/roe-v-wade/",
    "supremecourt.gov": "https://www.supremecourt.gov/opinions/23pdf/22-451_7m58.pdf",
    "ftc.gov": "https://www.ftc.gov/legal-library/browse/rules",
    # member-state statute portals (analysis/38 §4)
    "gesetze-im-internet.de": "https://www.gesetze-im-internet.de/bdsg_2018/index.html",
    "dejure.org": "https://dejure.org/gesetze/BDSG/26.html",
    "buzer.de": "https://www.buzer.de/26_BDSG.htm",
    "legifrance.gouv.fr": "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006900785",
    # The article id is lower-case on this host: `/L1121-1` answers 404, `/l1121-1` the article.
    "code.travail.gouv.fr": "https://code.travail.gouv.fr/code-du-travail/l1121-1",
    "courdecassation.fr": "https://www.courdecassation.fr/",
    "irishstatutebook.ie": (
        "https://www.irishstatutebook.ie/eli/2018/act/7/section/36/enacted/en/html"
    ),
    "wetten.overheid.nl": "https://wetten.overheid.nl/BWBR0040940/2021-07-01",
    "boe.es": "https://www.boe.es/buscar/act.php?id=BOE-A-2018-16673",
    "normattiva.it": (
        "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:2003-06-30;196"
    ),
    "data.bka.gv.at": (
        "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
        "?Applikation=BrKons&Titel=Datenschutzgesetz&DokumenteProSeite=Ten"
    ),
    "ris.bka.gv.at": "https://www.ris.bka.gv.at/Bundesrecht/",
    # RU — the web channels are the primary statute source (D-185)
    "www.consultant.ru": (
        "https://www.consultant.ru/document/cons_doc_LAW_5142/1de6cd3cbb386056a2ecd2c64ff087b13c8de585/"
    ),
    "base.garant.ru": "https://base.garant.ru/10164072/63f1429d78ff04df7c3513d140a5b10a/",
    "sudact.ru": "https://sudact.ru/regular/doc/",
    # national supervisory authorities — the doctrine row of a member state
    "bfdi.bund.de": "https://www.bfdi.bund.de/DE/Home/home_node.html",
    "datenschutzkonferenz-online.de": "https://www.datenschutzkonferenz-online.de/",
    "cnil.fr": "https://www.cnil.fr/fr/reglement-europeen-protection-donnees",
    "dpc.ie": "https://www.dpc.ie/",
    "autoriteitpersoonsgegevens.nl": "https://www.autoriteitpersoonsgegevens.nl/",
    "aepd.es": "https://www.aepd.es/guias",
    "garanteprivacy.it": "https://www.garanteprivacy.it/home",
    "dsb.gv.at": "https://www.dsb.gv.at/",
}
"""One representative document per routed host — the page a researcher would really fetch.

Static on purpose: a preflight that guessed URLs would report the guess, not the portal. Every
domain of `routing.ROUTING` and `routing.MEMBER_STATE_ROWS` has an entry (`test_preflight` asserts
it), so a new routed domain is a table edit, never a silent hole.
"""

PREFLIGHT_ALTERNATIVES: dict[str, str] = {
    "publications.europa.eu": "legalviz_get_law_part, or eur-lex.europa.eu",
    "eur-lex.europa.eu": "Cellar (publications.europa.eu, Accept: application/xhtml+xml) or LegalViz",
    "edpb.europa.eu": "ldh_search on EU/EDPB",
    "europa.eu": "the EDPB landing page on edpb.europa.eu",
    "legislation.gov.uk": "uklegal_legislation_get_section",
    "caselaw.nationalarchives.gov.uk": "uklegal_case_law_search + judgment_get_paragraph",
    "ico.org.uk": "ldh_search on UK/ICO",
    "govinfo.gov": "ldh_search on US/USCode",
    "courtlistener.com": "the CourtListener MCP, or the anonymous /api/rest/v4/search/",
    "supremecourt.gov": "courtlistener_search",
    "ftc.gov": "WebSearch for the regulator's own page",
    "gesetze-im-internet.de": "dejure.org or buzer.de",
    "dejure.org": "gesetze-im-internet.de or buzer.de",
    "buzer.de": "gesetze-im-internet.de or dejure.org",
    "legifrance.gouv.fr": "justicelibre_get_law_article, or ldh_resolve_reference on FR/LegifranceCodes",
    "code.travail.gouv.fr": "justicelibre_get_law_article (it covers every code, not only the CT)",
    "courdecassation.fr": "justicelibre_search_judiciaire + justicelibre_get_decision_judiciaire",
    "irishstatutebook.ie": "ldh_search on the IE sources",
    "wetten.overheid.nl": "ldh_search on the NL sources",
    "boe.es": "ldh_search on the ES sources",
    "normattiva.it": "the OpenAPI route on api.normattiva.it: mf sources save --method POST --json --public-url",
    "data.bka.gv.at": "ogd.ris.bka.gv.at/Dokumente/… taken from the API answer",
    "ris.bka.gv.at": "the OGD API on data.bka.gv.at",
    "www.consultant.ru": "base.garant.ru, or ldh_search on RU/PravoGovRu",
    "base.garant.ru": "www.consultant.ru, or ldh_search on RU/PravoGovRu",
    "sudact.ru": "casus_casuslegal_search_practice, fas_search_fas_cases, or ldh_search on RU/Sudact",
    "bfdi.bund.de": "ldh_search on DE/BfDI, DE/LDI-NRW",
    "datenschutzkonferenz-online.de": "ldh_search on DE/BfDI, DE/LDI-NRW",
    "cnil.fr": "ldh_search on the FR sources",
    "dpc.ie": "ldh_search on the IE sources",
    "autoriteitpersoonsgegevens.nl": "ldh_search on the NL sources",
    "aepd.es": "ldh_search on the ES sources",
    "garanteprivacy.it": "ldh_search on the IT sources",
    "dsb.gv.at": "ldh_search on the AT sources",
}
"""Where the researcher goes when the host of the same key did not answer — the routing note, short.

D-151: every line names something the routing table knows — a routed domain, a tool of a routing
row, a bundled MCP server, `mf sources fetch` or `mf sources save` (D-205: the IT article is a
source, so its route saves it). `test_preflight` asserts it, so an alternative
can no longer drift away from the note it is supposed to summarise (the IT line used to say a
pinpoint «needs a session, considered_excluded» long after D-148 routed it through the official
OpenAPI).
"""

_ERROR_STATUS: dict[str, str] = {
    "aws_waf_challenge": "waf_challenge",
    "cloudflare_challenge": "cloudflare",
    "interstitial_suspected": "interstitial",
    "tls_certificate": "tls",
    sources.SUDACT_DEFENCE_REDIRECT: "interstitial",
    f"channel_unavailable: {sources.CHANNEL_CAPTCHA}": "interstitial",
}
"""`sources.probe_url` names the interstitial in `error` (D-146); the status is that name, classed.

Final review C: the sudact host's own challenge — the refused hop into `/defence/`, or a host the run
has already closed — is a challenge page as far as the researcher is concerned."""

STATUS_LABELS: dict[str, str] = {
    "ok": i18n.t("en", "ui.preflight.status_ok"),
    "waf_challenge": i18n.t("en", "ui.preflight.status_waf_challenge"),
    "cloudflare": i18n.t("en", "ui.preflight.status_cloudflare"),
    "interstitial": i18n.t("en", "ui.preflight.status_interstitial"),
    "dead": i18n.t("en", "ui.preflight.status_dead"),
    "tls": i18n.t("en", "ui.preflight.status_tls"),
}
"""The English labels of `ui.preflight.status_*` (D-176); the gate-visible rows read the
pack, the `${source_access}` prompt value keeps these English bytes."""

CLEAN_LINE = i18n.t("en", "ui.preflight.clean_line")
"""`${source_access}` of a run whose preflight found nothing wrong."""

UNKNOWN_LINE = i18n.t("en", "ui.preflight.unknown_line")
"""`${source_access}` before the preflight step ran, or when its file cannot be read."""


# --- offline ---------------------------------------------------------------


def offline() -> bool:
    """True when `MEMOFORGE_OFFLINE` forbids the probe (dry run, tests)."""
    return str(os.environ.get(OFFLINE_ENV) or "").strip().lower() in _TRUE_VALUES


# --- which hosts this plan needs -------------------------------------------


def _raw_jurisdictions(plan: dict) -> list[str]:
    """Every jurisdiction the plan names — at the top and per issue — as the planner spelled it."""
    raw = list((plan or {}).get("jurisdictions") or [])
    for issue in (plan or {}).get("issues") or []:
        if isinstance(issue, dict):
            raw.extend(issue.get("jurisdictions") or [])
    return [str(value) for value in raw]


def plan_jurisdictions(plan: dict) -> list[str]:
    """Every jurisdiction the plan names, normalised and without repeats (`GB` -> `UK`, §4.3)."""
    codes: list[str] = []
    for value in _raw_jurisdictions(plan):
        code = routing.normalize_jurisdiction(value)
        if code and code not in codes:
            codes.append(code)
    return codes


def names_member_states_generically(plan: dict) -> bool:
    """True when the plan says «EEA member states» instead of naming ISO codes (D-132)."""
    return any(routing.route("statutes", value)["member_state_generic"] for value in _raw_jurisdictions(plan))


def plan_layers(plan: dict) -> list[str]:
    """The layers the plan can be researched in; `doctrine` only when the plan asked for it."""
    if (plan or {}).get("doctrine_required"):
        return list(routing.LAYERS)
    return [layer for layer in routing.LAYERS if layer != "doctrine"]


def plan_hosts(plan: dict) -> list[str]:
    """Every routed portal host of `layers x jurisdictions`, in table order and without repeats.

    `routing.routing_for` already answers with the member-state rows for a code the plan names, so
    the only addition is the generic spelling: a plan that says «EEA member states» names no code at
    all, and the national statute portals behind it are exactly where retrieval fails (analysis/38
    §4.2–4.4). A host without a `PREFLIGHT_URLS` entry is not probed — there is nothing to ask for.
    """
    hosts: list[str] = []
    for row in routing.routing_for(plan_layers(plan), plan_jurisdictions(plan)):
        for domain in row.get("domains") or []:
            if domain in PREFLIGHT_URLS and domain not in hosts:
                hosts.append(domain)
    if names_member_states_generically(plan):
        for code in routing.MEMBER_STATES:
            for domain in routing.MEMBER_STATE_ROWS["statutes"][code]["domains"]:
                if domain in PREFLIGHT_URLS and domain not in hosts:
                    hosts.append(domain)
    return hosts


# --- one probe -------------------------------------------------------------


def classify(probe: dict) -> str:
    """`ok | waf_challenge | cloudflare | interstitial | dead | tls` from one `probe_url` answer."""
    if str(probe.get("status") or "") in ("ok", "redirect"):
        return STATUS_OK
    return _ERROR_STATUS.get(str(probe.get("error") or ""), "dead")


def probe_host(host: str, *, timeout: float | None = None, work_dir: str | os.PathLike | None = None) -> dict:
    """One row of `intake/preflight.json`; a probe error is a status, never a raised exception.

    Final review C: a probe of the sudact host is a request of its channel like any other — with the
    work dir whose `channels.json` holds the run's pace, budget and captcha marker, it takes its slot,
    never follows the hop into `/defence/`, and a challenge it meets closes the host for the run.
    """
    url = PREFLIGHT_URLS[host]
    try:
        # `want_body=True`: the three false-positive 200s of analysis/38 §7.4 (eCFR stub, curia
        # shell, an act tree without article text) are only visible in the body.
        if work_dir is not None and sources.sudact_address(url):
            probe = sources.channel_probe(work_dir, url, want_body=True, timeout=timeout)
        else:
            probe = sources.probe_url(url, want_body=True, timeout=timeout)
    except sources.ChannelUnavailable as exc:
        # The channel refused before a request went out: nothing was asked of the host.
        probe = {"status": "unchecked", "code": None, "error": sources.resolve_channel_refusal(exc)["errors"][0]}
    except Exception as exc:  # noqa: BLE001 - preflight never fails the pipeline (D-147)
        probe = {"status": "dead", "code": None, "error": sources.probe_error(exc)}
    return {
        "host": host,
        "url": url,
        "status": classify(probe),
        "code": probe.get("code"),
        "error": probe.get("error"),
        "alternative": PREFLIGHT_ALTERNATIVES.get(host),
    }


def offline_host(host: str) -> dict:
    """The row `MEMOFORGE_OFFLINE` produces: the host is assumed reachable and nothing is sent."""
    return {
        "host": host,
        "url": PREFLIGHT_URLS[host],
        "status": STATUS_OK,
        "code": None,
        "error": None,
        "alternative": PREFLIGHT_ALTERNATIVES.get(host),
    }


def probe_hosts(
    hosts: list[str], *, timeout: float | None = None, work_dir: str | os.PathLike | None = None
) -> list[dict]:
    """Probe each host once, with the politeness rule of `sources.run_liveness` (D-146)."""
    if offline():
        return [offline_host(host) for host in hosts]
    rows: list[dict] = []
    last_probe: dict[str, float] = {}
    for host in hosts:
        since = last_probe.get(host)
        if since is not None:
            sources._wait(sources.host_delay(host) - (time.monotonic() - since))
        rows.append(probe_host(host, timeout=timeout, work_dir=work_dir))
        last_probe[host] = time.monotonic()
    return rows


# --- reading the result back -----------------------------------------------


def read_preflight(work_dir: str | os.PathLike, state: dict | None = None) -> dict | None:
    """`intake/preflight.json` read against `published[]`; None when it cannot be used (D-41)."""
    try:
        document = stepctx.read_published(work_dir, PREFLIGHT_PATH, state=state)
    except (stepctx.OutputModifiedAfterPublish, OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def portal_status(work_dir: str | os.PathLike, state: dict | None = None) -> dict[str, str]:
    """`{host: status}` of the last preflight; `{}` before it ran (D-147)."""
    document = read_preflight(work_dir, state)
    rows = (document or {}).get("hosts")
    if not isinstance(rows, list):
        return {}
    return {
        str(row["host"]): str(row.get("status") or "")
        for row in rows
        if isinstance(row, dict) and row.get("host")
    }


def blocked_hosts(work_dir: str | os.PathLike, state: dict | None = None) -> list[dict]:
    """Every preflight row whose status is not `ok`, in table order (D-147)."""
    document = read_preflight(work_dir, state)
    rows = (document or {}).get("hosts")
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict) and row.get("host") and row.get("status") != STATUS_OK
    ]


def usable_namespaces(probe: dict | None) -> dict:
    """`namespaces` of `intake/mcp-probe.json` without the servers whose smoke call failed (D-147).

    A server that answered `quota`, `auth` or `error` is connected and useless, which is exactly the
    state `routing_digest` already knows how to drop (`exhausted`, D-122). A probe written before
    D-147 carries no `status` at all — then a namespace alone still means «reachable», as it did.
    """
    probe = probe if isinstance(probe, dict) else {}
    namespaces = probe.get("namespaces")
    if not isinstance(namespaces, dict):
        return {}
    status = probe.get("status")
    if not isinstance(status, dict) or not status:
        return dict(namespaces)
    usable = {}
    for alias, value in namespaces.items():
        if alias == "other" or str(status.get(alias, STATUS_OK)) == STATUS_OK:
            usable[alias] = value
    return usable


# --- the `Source access today:` block --------------------------------------


def _row_line(row: dict, ui: str = "en") -> str:
    """`{host}: {label} → {alternative}` — the label in the interface language `ui`.

    The default keeps the English bytes for the agent-side `${source_access}` prompt
    value; the alternative stays English (§10) in every language. A status no pack
    knows prints raw, exactly as it did before the pack lookup existed.
    """
    value = str(row.get("status"))
    try:
        label = i18n.t(ui, f"ui.preflight.status_{value}")
    except KeyError:
        label = STATUS_LABELS.get(value, value)
    alternative = str(row.get("alternative") or "").strip()
    tail = f" → {alternative}" if alternative else ""
    return f"{row.get('host')}: {label}{tail}"


def source_access_block(
    work_dir: str | os.PathLike, state: dict | None = None, ui: str = "en"
) -> str:
    """The gate-4 block: one line per host that did not answer today; `""` when none did fail.

    D-176 (sources/preflight): the caller that has the state passes
    `i18n.ui_language(state)`; the default keeps the English bytes for the callers
    that must stay agent-facing.
    """
    rows = blocked_hosts(work_dir, state)
    if not rows:
        return ""
    lines = [i18n.t(ui, "ui.preflight.block_head")]
    lines += [f"- {_row_line(row, ui)}" for row in rows[:MAX_BLOCK_LINES]]
    if len(rows) > MAX_BLOCK_LINES:
        lines.append(
            "- "
            + i18n.t(
                ui,
                "ui.preflight.more_line",
                count=len(rows) - MAX_BLOCK_LINES,
                path=PREFLIGHT_PATH,
            )
        )
    return "\n".join(lines)


def source_access_line(work_dir: str | os.PathLike, state: dict | None = None) -> str:
    """`${source_access}` — the same facts on one line, for the researcher prompt (§3.3).

    Agent-facing: always the English text, whatever the interface language of the task
    is — only the gate-visible block is localized (D-176, sources/preflight).
    """
    if read_preflight(work_dir, state) is None:
        return UNKNOWN_LINE
    rows = blocked_hosts(work_dir, state)
    if not rows:
        return CLEAN_LINE
    return "; ".join(_row_line(row) for row in rows)


# --- command ---------------------------------------------------------------


def build_document(
    hosts: list[str], *, timeout: float | None = None, work_dir: str | os.PathLike | None = None
) -> dict:
    """`intake/preflight.json` for one plan (schema `preflight`)."""
    return {
        "schema_version": 1,
        "checked_at": events.utc_now(),
        "offline": offline(),
        "hosts": probe_hosts(hosts, timeout=timeout, work_dir=work_dir),
    }


def run_preflight(args: argparse.Namespace) -> dict:
    """`mf sources preflight --workdir W --step S --attempt N` (§2.1 row 3, D-147)."""
    work_dir = Path(args.workdir)
    args_key = "sources preflight"
    state = state_io.read_state(work_dir)
    identity = stepctx.check_identity(state, args.step, args.attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {"errors": identity["errors"], "reason": identity.get("reason")}
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = dict(identity.get("result") or {})
        stored["already_done"] = True
        return stored

    try:
        # D-41: the jurisdictions come from the published plan, not from a file edited since.
        plan = stepctx.read_published(work_dir, PLAN_PATH, state=state)
    except stepctx.OutputModifiedAfterPublish as exc:
        return stepctx.drift_result(exc)
    except (OSError, ValueError):
        plan = {}
    if not isinstance(plan, dict):
        plan = {}

    document = build_document(plan_hosts(plan), timeout=args.timeout, work_dir=work_dir)
    schema.validate_or_raise(document, "preflight")

    stepctx.stage_input(work_dir, args.step, args.attempt, work_dir / PLAN_PATH)
    work_file = stepctx.stage_result(
        work_dir, args.step, args.attempt, "preflight.json", state_io.dumps(document).encode("utf-8")
    )
    entry = stepctx.publish_file(work_dir, work_file, PREFLIGHT_PATH, step_id=args.step)

    by_status: dict[str, int] = {}
    for row in document["hosts"]:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    result = {
        "preflight_path": PREFLIGHT_PATH,
        "hosts": len(document["hosts"]),
        "offline": document["offline"],
        "by_status": dict(sorted(by_status.items())),
        "blocked": [row["host"] for row in document["hosts"] if row["status"] != STATUS_OK],
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


def register(subparsers) -> None:
    """Register `mf sources preflight` (the `sources` group is shared with sources.py)."""
    from . import cli

    group = cli.group_subparsers(subparsers, "sources", "source registry, freeze and verifications")
    parser = group.add_parser("preflight", help="probe one url per routed portal before research")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=limits.LIVENESS_TIMEOUT_SECONDS)
    parser.add_argument("--phase", default=None)
    parser.set_defaults(func=run_preflight)
