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

QUOTE_DEFAULT_MAX_WORDS = 30  # S3
"""`quote extract --max-words` default (§4.4, §5.3)."""

QUOTE_CANDIDATES_MAX = 5  # S3
"""`quote extract` returns at most this many `candidates` on `too_long`/`not_found` (§5.3)."""

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
}
"""Upper bound of the provider's own daily quota; the real remainder is unknown (§4.3).

LegalViz, UK Legal, JusticeLibre, OpenCaseLaw, Federal Regulations and Lex are free hosted servers
that publish no quota at
all, so 60 is a safety ceiling rather than a modelled remainder — the run may not plan past it
(D-105, D-107, D-148, D-160, D-161). The Legal Data Hunter free plan died on the **second** `resolve_reference`
of one real run (code `-32029`) and on the **fifteenth** of another, so the quota floats and its
ceiling stays 10, not 20 (D-122, analysis/39 §9.3).
"""

MCP_INTAKE_CALLS = 2  # S3
"""Share of the estimate spent on the intake probe/preliminary sources (§4.3)."""

MCP_CURRENCY_CALLS_PER_LAYER = 2  # S3
"""Share of the estimate spent by `currency-checker` per research layer (§4.3)."""

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
