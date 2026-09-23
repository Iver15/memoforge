"""Single source of truth for every numeric limit of the pipeline (ТЗ §2.2, §5.4, §5.6, §7)."""

from __future__ import annotations

# --- Locking and atomic writes (§2.2) ------------------------------------
LOCK_TIMEOUT = 30
"""Seconds to wait for an advisory file lock before giving up (§2.2)."""

LOCK_POLL_INTERVAL = 0.05
"""Seconds between non-blocking lock retries (§2.2 «ожидание с таймаутом ... через повтор»)."""

ATOMIC_REPLACE_RETRIES = 5
"""os.replace retries on Windows sharing violations (WinError 32) (§2.2, CONVENTIONS)."""

ATOMIC_REPLACE_BACKOFF = 0.05
"""Base seconds for the exponential backoff between os.replace retries (§2.2)."""

# --- Attempt budgets (§2.2, table `attempts`) ----------------------------
MAX_PLAN_EDIT = 5
"""`attempts.plan_edit`: edit answers on gate 4 before forced approve (§2.2)."""

MAX_SUFFICIENCY_USER_FOLLOWUP = 2
"""`attempts.sufficiency_user_followup`: questions to the user are cheap, so two in any mode (D-116)."""

MAX_SUFFICIENCY_RESEARCH_FOLLOWUP: dict[str, int] = {"brief": 1, "full": 2}
"""`attempts.sufficiency_research_followup` per mode: a re-dispatch costs a research pass (D-116)."""


def research_followup_limit(mode: object) -> int:
    """The research follow-up budget of one mode; an unknown mode gets the Full allowance (D-116)."""
    return MAX_SUFFICIENCY_RESEARCH_FOLLOWUP.get(str(mode or ""), MAX_SUFFICIENCY_RESEARCH_FOLLOWUP["full"])

MAX_RESEARCH_DISPATCH_RETRY = 1
"""`attempts.research_dispatch_retry`: re-dispatch of unclosed research slots (§2.2)."""

MAX_CURRENCY_REGATE = 1
"""`attempts.currency_regate`: returns from phase 9 to phase 6 (§2.2)."""

MAX_REVIEWER_JSON_RETRY = 1
"""`attempts.reviewer_json_retry{iteration,kind}` before a stub review (§2.2)."""

MAX_REVIEWER_RERUN = 1
"""`attempts.reviewer_rerun{iteration}` before `manual_review_required` (§2.2)."""

MAX_TARGETED_FIX_PASSES = 1
"""`attempts.targeted_fix`: targeted citation passes of §4.5 п.4 branch 9 per run (D-165)."""

MAX_TARGETED_FIX_BLOCKERS = 2
"""Most `citations` blockers of a targeted category one pass may be asked to close (D-165, D-212).

The 2026-09-16 run ended with exactly one; beyond a couple of missing tokens the draft needs a
whole iteration, which the budget already refused.
"""

MAX_POLISH_RECHECK = 1
"""Dispatches of the `citations` re-check of the final polish per run (D-211).

Its JSON retries are the ordinary `reviewer_json_retry` of `<N>:citations_polish`; this caps the
dispatch itself, so a restart after a spent budget never issues a second re-check.
"""

MAX_SINGLE_DISPATCH_RETRY = 1
"""`attempts.single_dispatch_retry{step_id}` for dispatch(1) steps (§2.2)."""

MAX_INLINE_LLM_RETRY = 2
"""`attempts.inline_llm_retry{step_id}` before `failed` (§2.2)."""

MAX_GATE_PARSE_ERRORS = 3
"""`attempts.gate_parse_errors{gate}` before documented defaults (§2.2, §2.4)."""

MAX_STEP_ATTEMPTS = 8
"""Times one `step_id` may be issued by `mf next` before `finalize --reason step_loop` (D-58)."""

INTAKE_MAX_QUESTIONS = 10
"""Must-answer questions printed by the intake gate before a mode is chosen (§2.3, D-108)."""

# --- Progress (§2.2, §7.3) -----------------------------------------------
AGENT_STALE_SECONDS = 1800
"""TTL after which `progress.active[]` entries are dropped (§2.2)."""

SILENT_GAP_SECONDS = 300
"""`events analyze` flags an autonomous segment without `step_issued` (§7.3)."""

# --- Journal (§7.2 C) ----------------------------------------------------
EVENT_LINE_MAX_BYTES = 4096
"""One journal line is appended under `events.lock` and stays ≤4 KB (§7.2 C)."""

# --- Lint numbers (§5.4) -------------------------------------------------
MAX_SENTENCE_WORDS = 40
"""L-01: sentences longer than this are a finding (§5.4)."""

MAX_PARAGRAPH_SENTENCES = 3
"""L-02: paragraph sentence cap (§5.4)."""

MAX_PARAGRAPH_WORDS = 100
"""L-02: paragraph word cap (§5.4)."""

BRIEF_WORD_CAP = 1200
"""L-10: `executive-brief` word cap (§5.4, §2.3)."""

BRIEF_SOURCE_WORD_WEIGHT = 12
"""L-10: each unique `[[src:]]` counts as this many words (§5.4)."""

EXEC_SUMMARY_BULLET_MAX_WORDS = 40
"""L-13: Exec Summary bullet cap (§5.4)."""

MAX_BLOCKQUOTES_PER_SUBSECTION = 1  # S3
"""L-08: at most one blockquote per analytical subsection (§5.4)."""

# --- Sources (§5.3) ------------------------------------------------------
LIVENESS_TIMEOUT_SECONDS = 10
"""`sources liveness` per-URL timeout, best effort (§5.3)."""

LIVENESS_MAX_BODY_BYTES = 8 * 1024 * 1024  # S3
"""`sources liveness` reads at most this much of a response body before giving up (§5.3)."""

LIVENESS_HOST_DELAY_SECONDS = 2.0
"""Seconds `sources liveness` waits between two probes of the **same** host (D-146).

The real run fired ~2.5 requests/s at one host (analysis/38 §7.4); the registry is walked in one
loop, so without this pause a 50-source run reads like a crawler to every anti-bot edge.
"""

LIVENESS_HOST_DELAYS: dict[str, float] = {"eur-lex.europa.eu": 10.0}
"""Hosts whose own `robots.txt` asks for more than `LIVENESS_HOST_DELAY_SECONDS` (D-146).

`eur-lex.europa.eu/robots.txt` says `Crawl-delay: 10` (analysis/38 §1.3). A subdomain inherits the
delay of its parent entry.
"""

LIVENESS_MIN_BODY_BYTES = 2 * 1024
"""A **markup** 200 body shorter than this is a shell, not the document (D-149).

D-146 set the floor at 15 KB and applied it to every content type. analysis/39 §9.1 measured what
that rejected: the canonical `§ 26 BDSG` page (9 560 B), the 7 367-byte `legislation.gov.uk`
pinpoint snippet and the RIS OGD JSON answer — all three the *preferred* address of the routing
table. Nobody serves a document in two kilobytes of html (`courdecassation.fr/decision/<id>` is a
255-byte JS shell), and the rule never applies to JSON/XML/PDF/plain at all (`sources.is_markup`).
"""

LIVENESS_MIN_TEXT_RATIO = 0.05
"""Share of a markup 200 body that must survive tag/script/style stripping to be a document (D-146).

`curia.europa.eu/juris/*` returns the same 130 226-byte JS shell for every case; six characters of
it are visible text (analysis/38 §2).
"""

LIVENESS_MAX_SHELL_BYTES = 4 * 1024
"""Ceiling of the JS-redirect-shell rule (D-149, analysis/39 §9.2).

`courdecassation.fr/decision/<id>` answers 200 with 255 bytes whose only content is
`window.location.href='/redirect_<token>/decision/<id>'` plus a `<noscript>` line — visible text
ratio 0.149, so the text-ratio rule alone never sees it. Above this size a page carrying a redirect
is a real document that also redirects.
"""

LIVENESS_MIN_ARTICLE_CHARS = 500
"""Visible characters below which a markup body holds no document text (D-149).

The redirect shells measured on 2026-09-13 hold 6 (curia) and 44 (courdecassation) characters; the
shortest legitimate pinpoint address of the routing table, the `legislation.gov.uk` snippet, holds
1 318.
"""

RESOLVE_MAX_CANDIDATES = 10
"""How many candidate acts `mf sources save --resolve` fetches and certifies from one listing (D-202).

A portal answers a case number with the whole chain of the case: the measured `305-ЭС24-8702`
returns eight acts, and every candidate costs one request and one politeness pause. The links are
deduplicated by address **before** the cap is applied — the real page prints each of its eight
links six times, so a cap on the raw list would spend all ten fetches on the first two acts.
"""

SUDACT_POLL_MAX = 5
"""How many times `--resolve sudact` asks the portal's search again after it answered `new` (D-202).

The search is asynchronous: the first answer is `{"status": "new"}` and, measured on 2026-09-20,
`finished` with the list of documents comes some 3 s later. With `CHANNEL_MIN_INTERVAL_S` between
polls that is a window of ten seconds; past it the channel answers «not resolved» and the fallbacks
take over — no exception, and no marker, because an unfinished search is not a challenge.
"""

CHANNEL_MIN_INTERVAL_S = 2
"""Seconds between two requests of a resolver channel, across every process of the run (D-202).

Every request counts — the section page, the search, each poll, each document, the save's own
fetch. The pace lives in `<work_dir>/channels.json`, because three researchers resolve at once and
a pause one process keeps to itself is no pace for the portal.
"""

CHANNEL_MAX_REQUESTS_PER_RUN = 60
"""Requests a resolver channel may make in one run, all processes together (D-202).

Counted the same way as the pace, request by request. Over the cap the channel answers
`channel_budget_spent` and the fallbacks take over: the budget is politeness, and it is spent.
"""

QUOTE_DEFAULT_MAX_WORDS = 60  # S3
"""`quote extract --max-words` default (§4.4, §5.3); D-217: one sentence of a provision fits (Art 33(1) GDPR, 56)."""

QUOTE_CANDIDATES_MAX = 5  # S3
"""`quote extract` returns at most this many `candidates` on `too_long`/`not_found` (§5.3)."""

LOCATE_CONTEXT_CHARS = 1500
"""`quote locate --context` default: characters of saved text read on each side of a match (D-207)."""

LOCATE_CONTEXT_MAX = 4000
"""The ceiling of `quote locate --context`; a larger value is clamped to it, never refused (D-207)."""

LOCATE_MAX_PASSAGES = 3
"""`quote locate --max-passages` default: passages returned when the words occur more than once (D-207)."""

LOCATE_CANDIDATE_MAX_CHARS = 1500
"""A `quote locate` candidate longer than this is its sentence's head, cut at a word boundary (D-207)."""

REVIEWER_LOOKUP_BUDGET: dict[str, int] = {"citations": 20, "counterarguments": 8}
"""`${lookup_budget}` per reviewer kind (D-208): a lookup or a whole-article read costs 1, a whole court act 3.

Only these two reviewers check a claim against the saved text; every other reviewer and agent gets 0.
"""

CARRY_OVER_MAX = 10
"""`${carry_over}` of the next citations review (D-214): at most this many unchecked `(source, section)` pairs."""

# --- MCP budget (§4.3) ---------------------------------------------------
RETRY_AFTER_CAP = 60  # S3
"""Seconds a `Retry-After` wait may reach before the run degrades fail-soft (§4.3)."""

MCP_PROVIDER_DAILY_LIMITS: dict[str, int] = {  # S3
    "ldh": 10,
    "courtlistener": 125,
    "legalviz": 60,
    "uklegal": 60,
    "justicelibre": 60,
    "opencaselaw": 60,
    "fedregs": 60,
    "lex": 60,
    "casus": 100,
    "fas": 300,
}
"""Upper bound of the provider's own daily quota; the real remainder is unknown (§4.3).

LegalViz, UK Legal, JusticeLibre, OpenCaseLaw, Federal Regulations, Lex and CasusLegal are free
hosted servers
that publish no quota at
all, so 60 is a safety ceiling rather than a modelled remainder — the run may not plan past it
(D-105, D-107, D-148, D-160, D-161, D-184). CasusLegal publishes no quota; its ceiling is 100
as an orientation, not a modelled remainder. FAS publishes 20/min and 300/day per IP, shared
across cloud clients, so 300 is the orientation all the same — but neither server joins
`MCP_QUOTA_SERVERS`: the per-jurisdiction quota filtering is out of scope, so the plan-gate
estimate keeps comparing against `ldh` and `courtlistener` only (D-184, fix round 1).
The Legal Data Hunter free plan died on the **second** `resolve_reference`
of one real run (code `-32029`) and on the **fifteenth** of another, so the quota floats and its
ceiling stays 10, not 20 (D-122, analysis/39 §9.3).
"""

MCP_INTAKE_CALLS = 2  # S3
"""Share of the estimate spent on the intake probe/preliminary sources (§4.3)."""

MCP_CURRENCY_CALLS_PER_LAYER = 2  # S3
"""Share of the estimate spent by `currency-checker` per research layer (§4.3)."""

MCP_QUOTA_SERVERS: tuple[str, ...] = ("ldh", "courtlistener")
"""Servers with a published daily quota — the only ones the plan-gate estimate is compared to (D-166)."""

MCP_SOFT_CAP_PER_RUN = 100
"""Calls to one free server per run before `finalize` raises `mcp_soft_cap_exceeded` (D-166)."""

# --- Stop-guard (§8.3) ---------------------------------------------------
STOP_GUARD_MAX_BLOCKS = 2
"""Blocks per phase before `stop_guard_gave_up` (§8.3)."""

# --- Task identity / launcher (§2.5, §5.6, §8.4) -------------------------
SLUG_MAX_LENGTH = 40
"""`task new` slug is sanitised to `[a-z0-9-]` and truncated to this length (§2.5)."""

PYTHON_MIN_VERSION = (3, 9)
"""Launcher discovery accepts an interpreter only from this version on (§5.6)."""

LAUNCHER_PROBE_TIMEOUT = 15
"""Seconds allowed for one interpreter probe during discovery (§5.6)."""

ALLOWED_WRITER_MODELS = ("opus", "fable", "sonnet")
"""`userConfig.writer_model` allowlist (§4.1: `{opus, fable, sonnet}`, otherwise fallback to opus)."""
