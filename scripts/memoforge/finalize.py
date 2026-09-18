"""`mf finalize [--salvage]` and `mf tidy` — always-deliver (M9, ТЗ §2.1 rows 15-16, §5.5).

The invariant this module owns: a terminal phase (`done|failed|cancelled_by_user`) is written to
`state.json` **only after** `deliverable.{docx|md}` and `summary.md` exist on disk. `--salvage` is the
degraded entry point of M9: it needs neither `jsonschema` (it writes through
`state_io.write_state_unvalidated`) nor a readable `state.json` (it rebuilds the summary from the
files it finds in the work dir).
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from . import events, fallbacks, hooks_common, i18n, limits, phases, render, sources, state_io, stepctx
from .docx import fallback as md_fallback
from .docx import (
    memo_docx_path,
    memo_md_path,
    rendered_source_sha,
    rendered_status_inputs,
    select_draft,
    slug_of,
    status_signature,
)

DELIVERABLE_MD = "deliverable.md"
DELIVERABLE_DOCX = "deliverable.docx"
SUMMARY_MD = "summary.md"
FALLBACK_SUMMARY_MD = "fallback-summary.md"

PUBLISH_DIRNAME = "memoforge"
"""`<publish root>/memoforge/<slug>/` — the folder the finished result is copied into (D-109)."""

PUBLISH_SOURCES_DIRNAME = "sources"
SOURCE_PACK_MD = "source-pack.md"

PUBLISH_RUN_DIRNAME = "_run"
"""`<root>/memoforge/<slug>/_run/` — the small diagnostic files of the run (D-113)."""

PUBLICATION_FILES: tuple[str, ...] = (DELIVERABLE_DOCX, DELIVERABLE_MD, SUMMARY_MD)
"""What a publish owns inside `<root>/memoforge/<slug>/`, next to the `sources/` directory (D-111)."""


def root_memo_name(slug: str, deliverable: str) -> str:
    """`<root>/memo-<slug>.<ext>` — the chat-visible copy, next to `memoforge/` (D-167).

    Files at the root of the host outputs area are listed in the Cowork sidebar; nested
    folders are not — so the memo lands there under its own name, not inside the folder.
    """
    ext = ".docx" if deliverable == DELIVERABLE_DOCX else ".md"
    return f"memo-{slug}{ext}"


def root_summary_name(slug: str) -> str:
    """`<root>/memo-<slug>.summary.md` — the summary next to the root memo copy (D-167, fix wave).

    The dot keeps the two root families disjoint: `memo-privacy.summary.md` can never equal
    `memo-<slug>.<ext>` for slug `privacy-summary` (slugs never contain a dot).
    """
    return f"memo-{slug}.summary.md"

HOST_OUTPUTS_DIR = "/mnt/user-data/outputs"
"""The host's outputs area when there is one: present inside the Cowork container, absent elsewhere
(D-109). It is never created — an absent directory simply means «this host publishes nothing»."""

PUBLISHED_TIERS: tuple[str, ...] = ("critical", "supporting")
"""Tiers whose raw text travels with the result; `background` sources stay in the work dir."""

TIDY_KEEP_DIRS: tuple[str, ...] = ("steps",)
"""`tidy` never touches the attempt workspaces (§2.2) nor the permanent lock files (§2.2)."""

SEEN_DIR_RELATIVE = "events/.seen"
"""At-least-once dedup markers live until `tidy` (§7.2 C)."""


# --- terminal phase -------------------------------------------------------


def terminal_phase(state: dict, reason: str | None) -> str:
    """Pick the terminal phase: user cancel wins, an explicit `--reason` means `failed` (§2.1)."""
    if state.get("cancel_requested"):
        return "cancelled_by_user"
    if reason:
        return "failed"
    return "done"


def default_final_status(phase: str, state: dict) -> str:
    """`final_status` when the pipeline did not set one (§2.1 row 15)."""
    existing = state.get("final_status")
    if isinstance(existing, str) and existing:
        return existing
    return {
        "done": "delivered",
        "failed": "failed",
        "cancelled_by_user": "cancelled_by_user",
    }[phase]


# --- the tail of the client deliverable (D-113, D34-11) -------------------


def condense_appendix(body: str, work_dir: Path, state: dict) -> str:
    """Rewrite the deliverable's `## Status` + appendix tail (D-113, D34-11, D-123).

    `docx.fallback` already writes the client form of both — the status of a run that did not end
    approved, then condensed assumption bullets and one currency notice instead of one per source —
    so this only re-applies them to a body that was rendered before: the `memo-<slug>.md` export an
    earlier `docx render` left behind, whose bytes are bound to a draft sha and must not be
    rewritten in place. The unresolved ids are read back out of the body's own `[unresolved: …]`
    markers.

    D-123: the `## Status` pass does not depend on the appendix. `render_appendix` returns nothing
    when a run has no warnings, no unverified source and no unresolved id, and a body that ends
    approved has no status section to find either — so keying the rewrite on the appendix heading
    left exactly those exports carrying the status they were rendered with, not the one this
    finalize is about to write. A body that needs neither section is still returned untouched.

    D-175: the export was written in the memo language of the run, so the headings this pass reads
    back are that language's — an English partition would find nothing and double the appendix.
    """
    language = md_fallback.memo_language(state)
    head, marker, _ = body.partition(md_fallback.appendix_heading(language))
    appendix = ""
    if marker:
        index = md_fallback.SourceIndex.load(work_dir, state=state)
        appendix = md_fallback.render_appendix(
            state.get("drafting_warnings") or [],
            index.unverified_rows(language),
            md_fallback.unresolved_ids(head),
            currency_unavailable=index.currency_unavailable,
            language=language,
        )
    status = md_fallback.render_status(md_fallback.status_inputs(state))
    memo = _without_status(head, language=language)
    if not marker and not status and memo == head:
        return body  # no appendix, no status to write and none to drop: nothing to rewrite
    parts = [memo.rstrip()] + [part.rstrip() for part in (status, appendix) if part]
    return "\n\n".join(parts) + "\n"


def _without_status(head: str, *, language: str = "en") -> str:
    """Drop the `## Status` section a previous render wrote, so the rewrite never doubles it.

    Only a section opening with the lead line of `docx.fallback.render_status` is ours; a heading of
    the same name inside the memorandum itself belongs to the writer and is left alone. A language
    whose lead opens with its placeholder has no fixed opening to match on, and then nothing is
    dropped: cutting on an empty prefix would take the writer's own section with it.
    """
    lead = md_fallback.status_lead_prefix(language)
    if not lead:
        return head
    marker = md_fallback.status_heading(language) + "\n"
    memo, found, tail = head.rpartition(marker)
    if found and tail.lstrip().startswith(lead):
        return memo
    return head


# --- deliverable ----------------------------------------------------------


def build_fallback_summary(state: dict, work_dir: Path, reason: str | None) -> str:
    """Universal fallback body: what was learned and what failed (fallbacks `universal_fallback`)."""
    language = md_fallback.memo_language(state)
    task_id = state.get("task_id") or work_dir.name
    query = state.get("user_query") or md_fallback_summary("query_unavailable", language)
    last_phase = state.get("current_phase") or md_fallback_summary("phase_unknown", language)
    mode = state.get("mode") or md_fallback_summary("not_selected", language)
    lines = [
        f"# {md_fallback_summary('fallback_title', language, task_id=task_id)}",
        "",
        md_fallback_summary("fallback_lead", language),
        "",
        md_fallback_summary("fallback_task", language, query=query),
        md_fallback_summary("fallback_last_phase", language, phase=last_phase),
        md_fallback_summary("fallback_mode", language, mode=mode),
    ]
    if reason:
        lines.append(md_fallback_summary("fallback_reason", language, reason=reason))
    lines.append("")
    lines.append(md_fallback_summary("fallback_artifacts", language))
    lines.append("")
    artifacts = _artifact_inventory(work_dir)
    if artifacts:
        lines.extend(f"- `{path}`" for path in artifacts)
    else:
        lines.append(md_fallback_summary("fallback_none", language))
    lines.append("")
    # D-113: this file is delivered to the client too, so it carries the short form of the warnings.
    bullets = md_fallback.assumption_bullets(state.get("drafting_warnings") or [], language)
    if bullets:
        lines.append(md_fallback_summary("fallback_open_questions", language))
        lines.append("")
        lines.extend(f"- {bullet}" for bullet in bullets)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def md_fallback_summary(key: str, lang: str, **fmt) -> str:
    """One `memo.summary` string of the memo language, rendered with `**fmt` (D-175).

    Reads through `i18n.node` and formats here: one entry is rendered with a `{code}`
    placeholder, which would collide with the language argument of `i18n.t`.
    """
    text = str(i18n.node(i18n.normalize(lang) or i18n.DEFAULT, f"memo.summary.{key}"))
    return text.format(**fmt) if fmt else text


def _artifact_inventory(work_dir: Path) -> list[str]:
    found: list[str] = []
    for relative in ("intake", "research", "drafts", "reviews"):
        directory = work_dir / relative
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                found.append(path.relative_to(work_dir).as_posix())
    return found[:60]


def _status_view(state: dict, final_status: str | None, banners: list) -> dict:
    """A copy of state carrying the status and the banners *this* finalize is about to write (D34-11).

    The `## Status` section of the deliverable must describe the terminal status, and a second
    `mf finalize` over an already terminal run must produce the same bytes as the first — which it
    could not if the section were built from a `state.json` the first run had already changed.
    """
    view = dict(state)
    view["final_status"] = final_status or state.get("final_status")
    view["fallback_banners"] = collect_banners(state, banners)
    return view


def choose_deliverable(
    work_dir: Path,
    state: dict,
    reason: str | None,
    *,
    final_status: str | None = None,
    reuse_export: bool = True,
) -> dict:
    """Select and materialise `deliverable.{docx|md}` (M9, §2.1 row 15).

    Order: the `memo-<slug>.docx` export of the selected version (D-50) -> the rendered
    `memo-<slug>.md` -> a fresh markdown fallback over the last draft -> the universal fallback
    summary. Returns the deliverable description and the banners it raised.

    D-175b: `reuse_export=False` takes the `memo-<slug>.md` export out of that order — the caller
    is rendering in a different language than the export was written in.
    """
    slug = slug_of(state, work_dir)
    banners: list[dict] = []
    reasons: list[str] = []

    docx_candidate = _existing_docx(work_dir, state, slug, _status_view(state, final_status, banners))
    if docx_candidate is not None:
        target = work_dir / DELIVERABLE_DOCX
        if not _same_file(docx_candidate, target):
            shutil.copyfile(docx_candidate, target)
        return {
            "deliverable": DELIVERABLE_DOCX,
            "kind": "docx",
            "source": docx_candidate.relative_to(work_dir).as_posix(),
            "banners": banners,
            "final_status_reason": None,
            "reasons": reasons,
        }

    banners.append(fallbacks.banner("docx_render_failed"))
    body, extra, status_reason, extra_reasons = _markdown_body(
        work_dir, state, slug, reason, final_status=final_status, banners=banners,
        reuse_export=reuse_export,
    )
    banners.extend(extra)
    reasons.extend(extra_reasons)
    # D-113: the appendix of the *deliverable* is the short one; the export and `summary.md` keep
    # what they carry (the export's bytes are bound to a draft sha, §2.1 row 15 / D-50).
    # D34-11: the same pass rebuilds `## Status` over the banners this finalize collected.
    body = condense_appendix(body, work_dir, _status_view(state, final_status, banners))
    state_io.write_bytes_atomic(work_dir / DELIVERABLE_MD, body.encode("utf-8"))
    return {
        "deliverable": DELIVERABLE_MD,
        "kind": "md",
        "source": f"{memo_md_path(work_dir, slug).name}",
        "banners": banners,
        "final_status_reason": status_reason,
        "reasons": reasons,
    }


def _existing_docx(work_dir: Path, state: dict, slug: str, view: dict) -> Path | None:
    """`memo-<slug>.docx`, but only when it is the export of the selected version (D-50, §2.1 row 15).

    The same binding `_markdown_body` applies to `memo-<slug>.md`: the `docx render` step records the
    draft sha it exported, and `published[]` proves the bytes on disk are still that export. A docx
    of a superseded version (or one nobody can vouch for) is not a deliverable — the markdown branch
    re-renders from the version §2.1 row 15 selects. Validity itself stays with `mf docx validate`,
    which renames a failing export to `memo-<slug>.invalid.docx` (§5.5), so it is no longer here.

    Two things beyond the draft sha decide it (D-117 N-06, D-123 N-07, D-144):

    * the export must still be published. A render that produced no valid docx revokes the one an
      earlier render of the same draft left behind, and a file no `published[]` row vouches for is
      «one nobody can vouch for» — the sha binding alone cannot tell the two renders apart. That is
      also what makes the revoke survive an unlink that failed: the row is gone, so the bytes still
      sitting on the canonical path are ignored here (R2-02).
    * its `## Status` section must say what this finalize is about to say. The markdown is rewritten
      by `condense_appendix`; a docx cannot be, so an export whose `status_signature` — the sha of
      the whole rendered section: status, banner ids and texts, blocker lines — differs from this
      finalize's is handed back to the markdown branch instead.
    """
    candidate = memo_docx_path(work_dir, slug)
    if not candidate.is_file() or candidate.stat().st_size == 0:
        return None
    relative = candidate.relative_to(work_dir).as_posix()
    source_sha = rendered_source_sha(state, relative)
    if source_sha is None or source_sha != select_draft(state, work_dir)["sha256"]:
        return None
    if stepctx.published_sha(state, relative) is None:
        return None
    if stepctx.verify_published(work_dir, state, relative):
        return None
    if rendered_status_inputs(state, relative) != status_signature(view):
        return None
    return candidate


def _markdown_body(
    work_dir: Path,
    state: dict,
    slug: str,
    reason: str | None,
    *,
    final_status: str | None = None,
    banners: list | None = None,
    reuse_export: bool = True,
) -> tuple[str, list[dict], str | None, list[str]]:
    """The markdown deliverable, bound to the version §2.1 row 15 selects (not to whatever is on disk).

    D-175b: `reuse_export=False` says the export on disk is written in another language than this
    render — `finalize --salvage` switched the labels to English because the pack of the run's
    language cannot be read. Reusing it would leave the parse-back of `condense_appendix` looking
    for English headings in a localized body: the export's own `## Status` survives and a second,
    English one is appended. So the selected draft is rendered afresh instead, and the export is
    left exactly as it is — its bytes are bound to a draft sha and a `published[]` row (D-50), and
    a degraded run must not invalidate them. With no draft left the export is still the deliverable:
    M9 delivers what there is, and an unrewritten localized body is whole.
    """
    selection = select_draft(state, work_dir)
    rendered = memo_md_path(work_dir, slug)
    raised = list(selection["banners"])
    reasons = list(selection["reasons"])
    # D34-11: a fresh render already states the terminal status and every banner this finalize
    # collected, so a second finalize over the same run reproduces the same bytes.
    view = _status_view(state, final_status, list(banners or []) + raised)
    banners = raised

    if reuse_export and rendered.is_file():
        # An earlier `docx render` may have exported a different version; only its own record of the
        # source draft proves otherwise (§2.1 row 15 «выбор версии», §2.2 published[]).
        source_sha = rendered_source_sha(state, rendered.relative_to(work_dir).as_posix())
        drifted = stepctx.verify_published(work_dir, state, rendered.relative_to(work_dir).as_posix())
        if source_sha is not None and source_sha == selection["sha256"] and not drifted:
            return rendered.read_text(encoding="utf-8-sig"), banners, None, reasons

    if selection["path"] is not None:
        result = md_fallback.render_workdir(work_dir, selection["path"], state=view)
        if reuse_export:
            state_io.write_bytes_atomic(rendered, result["markdown"].encode("utf-8"))
        return result["markdown"], banners + list(result["banners"]), None, reasons

    if rendered.is_file():
        # No draft left to re-render from: the existing export is still better than nothing (M9).
        return rendered.read_text(encoding="utf-8-sig"), banners, None, reasons

    body = build_fallback_summary(state, work_dir, reason)
    state_io.write_bytes_atomic(work_dir / FALLBACK_SUMMARY_MD, body.encode("utf-8"))
    return body, [fallbacks.banner("universal_fallback")], "fallback_summary_delivered", reasons


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:  # pragma: no cover - resolve() only fails on exotic filesystems
        return False


# --- summary --------------------------------------------------------------


def build_summary(
    state: dict,
    work_dir: Path,
    *,
    phase: str,
    final_status: str,
    deliverable: dict,
    reason: str | None,
    banners: list,
    salvaged: bool = False,
    pack_unavailable: str | None = None,
) -> str:
    """`summary.md`: status, `final_status_reasons[]`, the banners, the open blockers and the paths."""
    language = md_fallback.memo_language(state)
    task_id = state.get("task_id") or work_dir.name
    lines = [
        f"# {md_fallback_summary('title', language, task_id=task_id)}",
        "",
        md_fallback_summary("status", language, final_status=final_status),
        md_fallback_summary("terminal_phase", language, phase=phase),
        md_fallback_summary("mode", language, mode=state.get("mode") or md_fallback_summary("not_selected", language)),
    ]
    if state.get("user_query"):
        lines.append(md_fallback_summary("question", language, question=state["user_query"]))
    if reason:
        lines.append(md_fallback_summary("reason", language, reason=reason))
    if salvaged:
        lines.append(md_fallback_summary("salvaged", language))
    if pack_unavailable is not None:
        lines.append(md_fallback_summary("pack_unavailable", language, code=pack_unavailable))
    lines.append("")

    reasons = [str(row) for row in (state.get("final_status_reasons") or [])]
    lines.append(md_fallback_summary("manual_review_reasons", language))
    lines.append("")
    lines.extend([f"- {row}" for row in reasons] or [md_fallback_summary("none", language)])
    lines.append("")

    lines.append(md_fallback_summary("fallback_banners", language))
    lines.append("")
    rows = [_banner_text(banner, language) for banner in banners]
    lines.extend([f"- {row}" for row in rows] or [md_fallback_summary("none", language)])
    lines.append("")

    lines.append(md_fallback_summary("mcp_calls", language))
    lines.append("")
    lines.extend([f"- {row}" for row in mcp_call_lines(state, language)] or [md_fallback_summary("none_yet", language)])
    lines.append("")

    # D34-11: the deliverable prints at most `STATUS_ISSUE_LIMIT` of these and points here for the
    # rest, so this list is the complete one.
    blockers = [
        md_fallback.blocking_issue_line(issue)
        for issue in (state.get("remaining_blocking_issues") or [])
    ]
    lines.append(md_fallback_summary("remaining_blocking_issues", language))
    lines.append("")
    lines.extend([f"- {row}" for row in blockers if row] or [md_fallback_summary("none", language)])
    lines.append("")

    lines.append(md_fallback_summary("paths", language))
    lines.append("")
    lines.append(md_fallback_summary("work_dir", language, path=state.get("work_dir") or work_dir))
    lines.append(md_fallback_summary("deliverable", language, name=deliverable["deliverable"]))
    if deliverable.get("source"):
        lines.append(md_fallback_summary("rendered_from", language, name=deliverable["source"]))
    for label_key, relative in (
        ("state", state_io.STATE_FILENAME),
        ("journal", events.EVENTS_FILENAME),
    ):
        if (work_dir / relative).exists():
            lines.append(
                f"- {md_fallback_summary(label_key, language)}: `{relative}`"
            )
    lines.append("")

    warnings = state.get("drafting_warnings") or []
    if warnings:
        lines.append(md_fallback_summary("drafting_warnings", language))
        lines.append("")
        lines.extend(f"- {md_fallback.warning_text(warning)}" for warning in warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _banner_text(banner: object, language: str = "en") -> str:
    if isinstance(banner, dict):
        return md_fallback_summary(
            "banner_row",
            language,
            text=fallbacks.banner_text_for(banner, language),
            banner_id=banner.get("banner_id"),
        ).strip()
    return str(banner)


def mcp_call_lines(state: dict, language: str = "en") -> list[str]:
    """One `## MCP calls` line per server of `progress.mcp_calls` (D-166, telemetry)."""
    calls = (state.get("progress") or {}).get("mcp_calls") or {}
    rows = []
    for server in sorted(calls):
        used = calls[server]
        used = used if isinstance(used, int) else 0
        if server in limits.MCP_QUOTA_SERVERS:
            rows.append(
                md_fallback_summary(
                    "mcp_quota_row",
                    language,
                    server=server,
                    used=used,
                    limit=limits.MCP_PROVIDER_DAILY_LIMITS[server],
                )
            )
        else:
            rows.append(md_fallback_summary("mcp_plain_row", language, server=server, used=used))
    return rows


def soft_cap_banners(state: dict) -> list[dict]:
    """One `mcp_soft_cap_exceeded` banner per free server past the per-run soft cap (D-166)."""
    calls = (state.get("progress") or {}).get("mcp_calls") or {}
    raised = []
    for server in sorted(calls):
        used = calls[server]
        used = used if isinstance(used, int) else 0
        if server not in limits.MCP_QUOTA_SERVERS and used > limits.MCP_SOFT_CAP_PER_RUN:
            banner = fallbacks.banner("mcp_soft_cap_exceeded", server=server, count=used)
            if banner is not None:
                raised.append(banner)
    return raised


def _banner_key(banner: object) -> str:
    """Dedup key: `banner_id`, plus the server for the per-server soft-cap banners (D-166)."""
    if isinstance(banner, dict):
        key = str(banner.get("banner_id"))
        if key == "mcp_soft_cap_exceeded":
            params = banner.get("params") if isinstance(banner.get("params"), dict) else {}
            return f"{key}:{params.get('server')}"
        return key
    return str(banner)


def collect_banners(state: dict, extra: list) -> list:
    """Merge the banners already in state with the ones this finalize raised, keeping order."""
    out: list = []
    seen: set[str] = set()
    for banner in list(state.get("fallback_banners") or []) + list(extra):
        key = _banner_key(banner)
        payload = banner if isinstance(banner, dict) else str(banner)
        if key in seen:
            continue
        seen.add(key)
        out.append(payload)
    return out


# --- publish (D-109) ------------------------------------------------------


def publish_root(state: dict) -> Path | None:
    """Where the result is copied: `config.publish_folder`, else the host outputs area, else None.

    The work dir stays private — dozens of protocol files nobody asked for. What the user gets is
    this folder: the deliverable, `summary.md` and the frozen source texts. A host with neither an
    option nor an outputs area publishes nothing, which is not a failure (§2.5, D-109).

    Always absolute (D-167): a relative `publish_folder` is resolved against the current directory
    here, once, so `published_to`, `published_memo` and every staging path derive from one root
    no matter where the router later runs from.
    """
    configured = str((state.get("config") or {}).get("publish_folder") or "").strip()
    if configured and not hooks_common.is_placeholder(configured):
        return Path(os.path.abspath(configured))
    host = Path(HOST_OUTPUTS_DIR)
    if host.is_dir() and os.access(host, os.W_OK):
        return Path(os.path.abspath(str(host)))
    return None


def source_pack_markdown(work_dir: Path, *, language: str = "en") -> str:
    """`sources/source-pack.md`: the frozen pack when there is one, else a list of what was found.

    D-175: the client reads this file next to the memorandum, so it is written in the memo
    language. `tier` and the currency status are the recorded tokens and are printed as they are.
    """
    pack = None
    try:
        pack = sources.read_pack(work_dir)
    except (OSError, ValueError):
        pack = None
    if isinstance(pack, dict) and pack.get("entries"):
        try:
            return render.render_source_pack(pack, language)
        except (TypeError, AttributeError, KeyError, IndexError):
            pass  # a structurally broken pack falls through to the registry listing (D-99, D-90)

    try:
        registry = sources.read_registry(work_dir)
    except (OSError, ValueError):
        registry = sources.empty_registry()
    lines = [f"# {md_fallback.label('registered_sources_heading', language)}", ""]
    rows = sorted((registry.get("sources") or {}).items())
    if not rows:
        lines.append(f"- {md_fallback.label('no_registered_sources', language)}")
    for source_id, record in rows:
        record = record if isinstance(record, dict) else {}
        currency = record.get("currency") if isinstance(record.get("currency"), dict) else {}
        lines.append(
            "- "
            + md_fallback.label(
                "registered_source_row",
                language,
                source_id=f"`{source_id}`",
                title=record.get("title") or md_fallback.label("untitled", language),
                citation_form=record.get("citation_form")
                or md_fallback.label("no_citation_form", language),
                tier=record.get("tier") or "unknown",
                status=currency.get("status") or "unchecked",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def published_source_texts(work_dir: Path) -> list[tuple[str, str]]:
    """`(source_id, raw text)` of every `critical`/`supporting` source that still has a raw file."""
    try:
        registry = sources.read_registry(work_dir)
    except (OSError, ValueError):
        return []
    found: list[tuple[str, str]] = []
    for source_id, record in sorted((registry.get("sources") or {}).items()):
        if not isinstance(record, dict) or record.get("tier") not in PUBLISHED_TIERS:
            continue
        try:
            text = sources.read_raw_text(work_dir, record)
        except (OSError, ValueError):
            text = None
        if text is None:  # the raw file was never saved, or is gone — skip it, never fail
            continue
        found.append((source_id, text))
    return found


RUN_FILES: tuple[str, ...] = (
    state_io.STATE_FILENAME,
    events.EVENTS_FILENAME,
    "plan.json",
    "intake/user-facts.md",
    "research/research-sufficiency.json",
)
RUN_GLOBS: tuple[str, ...] = ("reviews/*.json",)
"""What `_run/` holds: the small files that explain a run (D-113).

Diagnostics only — never `steps/` (the attempt workspaces) and no raw source text beyond what
`sources/` already carries, so the published folder stays something a person can open.
"""


def run_diagnostics(work_dir: Path) -> list[Path]:
    """The `_run/` files that exist in this work dir, in the order of `RUN_FILES` (D-113)."""
    found = [work_dir / relative for relative in RUN_FILES]
    for pattern in RUN_GLOBS:
        found.extend(sorted(work_dir.glob(pattern)))
    return [path for path in found if path.is_file()]


def copy_run_diagnostics(work_dir: Path, target: Path) -> list[str]:
    """Copy `_run/` into the publication; a file that cannot be read is skipped, never raised (D-113)."""
    copied: list[str] = []
    for path in run_diagnostics(work_dir):
        relative = path.relative_to(work_dir).as_posix()
        destination = target / PUBLISH_RUN_DIRNAME / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        except OSError:
            continue  # M9/D-109: a diagnostic copy never turns a delivered run into a failed one
        copied.append(f"{PUBLISH_RUN_DIRNAME}/{relative}")
    return copied


def clear_publication(target: Path) -> None:
    """Remove what a previous publish wrote into `target`, and nothing else (D-111, §2.5).

    Kept for the unit contract only — `publish` itself moves the old set aside since D-158 and
    never calls this. A re-run of the same task publishes into the same `<root>/memoforge/<slug>/`.
    Overlaying the new files on the old ones leaves a mixture — yesterday's `deliverable.docx`
    next to today's `deliverable.md`, raw texts of sources this run dropped — and the router
    copies that mixture to the user whole. So the publication-owned set goes first and is then
    rewritten; anything else in the folder belongs to the user and is left alone.
    """
    if not target.is_dir():
        return
    for name in PUBLICATION_FILES:
        path = target / name
        if path.is_file():
            path.unlink()
    for dirname in (PUBLISH_SOURCES_DIRNAME, PUBLISH_RUN_DIRNAME):
        directory = target / dirname
        if directory.is_dir():
            shutil.rmtree(directory)


def publish(work_dir: Path, state: dict, deliverable: str, *, summary: str = SUMMARY_MD) -> dict:
    """Copy deliverable, summary, source texts and `_run/` into the publish folder; never raises (D-109).

    A copy that fails answers a `publish_failed` banner instead of an exception: the run has already
    delivered into the work dir, and M9 does not let a convenience copy turn a finished run into a
    failed one. That promise covers the *whole* boundary — picking the root, naming the folder,
    reading a corrupt registry, every write — so the guard is one broad `except` (D-111): a
    `TypeError` out of a malformed `sources.json` must end this run exactly like a read-only disk.
    """
    result: dict = {"published_to": None, "published_memo": None, "files": [], "banners": [], "error": None}
    try:
        root = publish_root(state)
        if root is None:
            return result
        slug = slug_of(state, work_dir)
        target = root / PUBLISH_DIRNAME / slug
        memo_name = root_memo_name(slug, deliverable)
        summary_name = root_summary_name(slug)
        # A43-5 / D-157: build the new publication next to the old one; the old set is cleared only
        # once every file of the new one exists, so a failed copy leaves yesterday's result in place.
        staging = target.parent / f"{target.name}.publishing"
        if staging.exists():
            shutil.rmtree(staging)
        staged_sources = staging / PUBLISH_SOURCES_DIRNAME
        staged_sources.mkdir(parents=True, exist_ok=True)
        try:
            for name in (deliverable, summary):
                origin = work_dir / name
                if origin.is_file():
                    shutil.copyfile(origin, staging / name)
                    result["files"].append(name)
            state_io.write_bytes_atomic(
                staged_sources / SOURCE_PACK_MD,
                source_pack_markdown(
                    work_dir, language=md_fallback.memo_language(state)
                ).encode("utf-8"),
            )
            result["files"].append(f"{PUBLISH_SOURCES_DIRNAME}/{SOURCE_PACK_MD}")
            for source_id, text in published_source_texts(work_dir):
                state_io.write_bytes_atomic(staged_sources / f"{source_id}.txt", text.encode("utf-8"))
                result["files"].append(f"{PUBLISH_SOURCES_DIRNAME}/{source_id}.txt")
            # D-113: `_run/` travels with the result — the state as it stood before the terminal write,
            # the journal, the plan, the intake facts, the sufficiency verdict and the reviews.
            result["files"].extend(copy_run_diagnostics(work_dir, staging))
            # D-167: the root copies land in `<root>/`, next to `memoforge/` — they are the files
            # the chat sidebar lists. They are staged outside the folder so the same swap moves them.
            staged_root = staging.parent / f"{target.name}.root"
            if staged_root.exists():
                shutil.rmtree(staged_root)
            staged_root.mkdir(parents=True, exist_ok=True)
            staged_memo = work_dir / deliverable if (work_dir / deliverable).is_file() else None
            staged_summary = work_dir / summary if (work_dir / summary).is_file() else None
            if staged_memo is not None:
                shutil.copyfile(staged_memo, staged_root / memo_name)
                result["files"].append(memo_name)
            if staged_summary is not None:
                shutil.copyfile(staged_summary, staged_root / summary_name)
                result["files"].append(summary_name)
            # D-158: the old set is renamed aside, never deleted, so a replacement that fails half
            # way — a docx open in Word, a read-only volume — is undone instead of leaving a
            # mixture of two runs. `<slug>.previous` is a rename on the same volume, like staging.
            previous = target.parent / f"{target.name}.previous"
            previous_root = target.parent / f"{target.name}.previous-root"
            shutil.rmtree(previous, ignore_errors=True)
            shutil.rmtree(previous_root, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            owned = [target / name for name in PUBLICATION_FILES] + [
                target / PUBLISH_SOURCES_DIRNAME,
                target / PUBLISH_RUN_DIRNAME,
            ]
            # D-111 at the root level too: a docx yesterday and markdown today must not
            # stand side by side, so the owned set covers both extensions, not just this ext.
            other = DELIVERABLE_DOCX if deliverable != DELIVERABLE_DOCX else DELIVERABLE_MD
            owned_root = [root / name for name in (memo_name, summary_name, root_memo_name(slug, other))]
            moved: list[tuple[Path, Path]] = []
            landed: list[Path] = []
            try:
                for path in owned:
                    if path.exists():
                        previous.mkdir(parents=True, exist_ok=True)
                        aside = previous / path.name
                        os.replace(path, aside)
                        moved.append((aside, path))
                for path in owned_root:
                    if path.exists() and path.is_file():
                        previous_root.mkdir(parents=True, exist_ok=True)
                        aside = previous_root / path.name
                        os.replace(path, aside)
                        moved.append((aside, path))
                for item in sorted(staging.iterdir()):
                    destination = target / item.name
                    os.replace(item, destination)
                    landed.append(destination)
                for item in sorted(staged_root.iterdir()):
                    destination = root / item.name
                    os.replace(item, destination)
                    landed.append(destination)
            except Exception:
                # Undo exactly what the swap did, by the two lists that record it and by nothing
                # else: the new items that landed go away, then the old ones are renamed back. A
                # path in neither list is an old file the swap never reached — when the failure is
                # in the move-aside loop it is still standing there, and deleting it by name (it
                # shares the name of a staged item) is precisely what must not happen.
                for destination in landed:
                    if destination.is_dir():
                        shutil.rmtree(destination, ignore_errors=True)
                    elif destination.is_file():
                        destination.unlink()
                for aside, original in reversed(moved):
                    os.replace(aside, original)
                raise
            finally:
                shutil.rmtree(previous, ignore_errors=True)
                shutil.rmtree(previous_root, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(staging.parent / f"{target.name}.root", ignore_errors=True)
        result["published_to"] = str(target)
        result["published_memo"] = str(root / memo_name) if staged_memo is not None else None
    except Exception as exc:  # noqa: BLE001 - M9: nothing here may stop a delivered run (D-111)
        result["published_to"] = None
        result["published_memo"] = None
        result["files"] = []
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["banners"] = [_publish_failed_banner(exc)]
        return result
    _log_published(work_dir, state, result)
    return result


def _publish_failed_banner(exc: BaseException) -> dict:
    """The `publish_failed` row of `fallbacks.py`, naming the failure class so the run is debuggable."""
    banner = fallbacks.banner("publish_failed")
    banner["text"] = f"{banner['text']} ({type(exc).__name__})"
    return banner


def _log_published(work_dir: Path, state: dict, result: dict) -> None:
    """`result_published` in the journal; best effort, exactly like every other M9 write (§7.2).

    Broad on purpose: it runs after the copy succeeded, so no journal failure may turn a published
    result into a `publish_failed` one — or into an exception `publish` promised never to raise.
    """
    try:
        events.append_event(
            work_dir,
            "result_published",
            "cli",
            {
                "path": result["published_to"],
                "files": result["files"],
                "memo": result.get("published_memo"),
            },
            phase=str(state.get("current_phase") or "") or None,
        )
    except Exception:  # noqa: BLE001 - the journal is best effort (§7.2)
        pass


# --- tidy -----------------------------------------------------------------


def tidy(work_dir: str | Path, *, dry_run: bool = False) -> dict:
    """Remove `*.tmp` and the `events/.seen/` markers; never touch lock files or `steps/` (§7.2 C).

    Best effort by design: a failure on one file never aborts the pass and never fails a finished
    pipeline (carried over from `scripts/tidy_workdir.py`).
    """
    work_dir = Path(work_dir)
    if not work_dir.is_dir():
        return {"skipped": True, "reason": f"work_dir_not_found: {work_dir}", "removed": [], "failed": []}

    targets: list[Path] = []
    for path in sorted(work_dir.rglob("*.tmp")):
        if path.is_file() and not _under(path, work_dir, TIDY_KEEP_DIRS):
            targets.append(path)
    seen = work_dir / SEEN_DIR_RELATIVE
    if seen.is_dir():
        targets.extend(sorted(p for p in seen.iterdir() if p.is_file()))
    # Canonical scripts are never copied into a work dir, so a top-level *.py is a stray helper
    # an agent wrote instead of calling the CLI (the 2026-05-29 incident behind tidy_workdir.py).
    targets.extend(sorted(p for p in work_dir.glob("*.py") if p.is_file()))

    removed: list[str] = []
    failed: list[dict] = []
    for path in targets:
        relative = path.relative_to(work_dir).as_posix()
        if dry_run:
            removed.append(relative)
            continue
        try:
            path.unlink()
        except OSError as exc:
            failed.append({"path": relative, "error": str(exc)})
        else:
            removed.append(relative)
    return {
        "skipped": False,
        "reason": None,
        "removed": removed,
        "failed": failed,
        "dry_run": dry_run,
        "kept": ["state.lock", "sources.lock", "events.lock", "steps/"],
    }


def _under(path: Path, work_dir: Path, names: tuple[str, ...]) -> bool:
    try:
        parts = path.relative_to(work_dir).parts
    except ValueError:  # pragma: no cover - rglob never leaves work_dir
        return False
    return bool(parts) and parts[0] in names


# --- commands -------------------------------------------------------------


def run_finalize(args: argparse.Namespace) -> dict:
    """`mf finalize` — deliverable + summary first, terminal phase second, then tidy (M9)."""
    work_dir = Path(args.workdir)
    if not work_dir.is_dir():
        return {"errors": [f"work_dir_not_found: {work_dir}"]}

    state = state_io.read_state_or_none(work_dir)
    state_corrupt = state is None
    if state_corrupt:
        if not args.salvage:
            return {
                "errors": ["state_unreadable"],
                "hint": "run `mf finalize --salvage` to still produce a deliverable (M9)",
            }
        state = _salvaged_state(work_dir)

    # D-40: identity before any side effect. `--salvage` is the single exception — a state that
    # cannot be read (or never issued the step) must still deliver (M9) — and it says so in the journal.
    identity_skipped = _check_identity(work_dir, args, state, state_corrupt)
    if isinstance(identity_skipped, dict):
        return identity_skipped

    phase = terminal_phase(state, args.reason)
    already_terminal = phases.is_terminal(str(state.get("current_phase")))
    terminal_status = default_final_status(phase, state)
    # D-175: no silent English memo — a normal finalize fails when the memo pack of the run's
    # language cannot be read, before the deliverable is chosen. `--salvage` is the exception:
    # it renders in English and says so in the summary.
    language = md_fallback.memo_language(state)
    pack_missing: str | None = None
    if language != i18n.DEFAULT and not args.salvage:
        try:
            i18n.load(language)
        except i18n.PackUnavailable:
            return {"errors": [f"language_pack_unavailable: {language}"]}
    if args.salvage and language != i18n.DEFAULT and not i18n.available(language):
        pack_missing = language
        state = dict(state, language=i18n.DEFAULT)
    # D-175b: the labels this run generates are now English while the `memo-<slug>.md` export on
    # disk is not, so the export is out of the deliverable order (`_markdown_body`).
    deliverable = choose_deliverable(
        work_dir,
        state,
        args.reason,
        final_status=terminal_status,
        reuse_export=pack_missing is None,
    )

    # Fix wave (D-166): the only banners raised past the choice above are the copy/telemetry
    # ones — `mcp_soft_cap_exceeded` here and `publish_failed` in `publish` — both
    # `docx.fallback.COPY_BANNERS` rows, both out of the `## Status` section (a docx cannot be
    # rewritten) and out of `status_signature`, both landing in `summary.md` only.
    # `salvage_state_corrupt` reaches this through `_salvaged_state`; there is no fourth late banner.
    banners = collect_banners(state, list(deliverable["banners"]) + soft_cap_banners(state))

    final_status = deliverable["final_status_reason"] or terminal_status
    reasons = [str(row) for row in (state.get("final_status_reasons") or [])]
    for row in deliverable.get("reasons") or ():
        if row not in reasons:
            reasons.append(row)
    if args.reason and args.reason not in reasons:
        reasons.append(args.reason)

    summary_state = dict(state)
    summary_state["final_status_reasons"] = reasons

    def write_summary(rows: list) -> None:
        body = build_summary(
            summary_state,
            work_dir,
            phase=phase,
            final_status=final_status,
            deliverable=deliverable,
            reason=args.reason,
            banners=rows,
            salvaged=bool(args.salvage),
            pack_unavailable=pack_missing,
        )
        state_io.write_bytes_atomic(work_dir / SUMMARY_MD, body.encode("utf-8"))

    write_summary(banners)

    # D-109: both files exist now, so the copy has something to publish. It runs on every terminal
    # path — salvage and the universal fallback included — and a failure only adds a banner. That
    # banner arrives after `summary.md` was written, so the summary is re-rendered over it: nothing
    # was copied in that branch, so there is no stale published copy to disagree with. `publish`
    # owns its own guard (D-111) and cannot raise, so there is nothing to catch here.
    # D-144: `publish_failed` is a `docx.fallback.COPY_BANNERS` row — it describes this copy, not
    # the memorandum — so it stays out of the `## Status` section of the deliverable (which is
    # already written) and out of `status_signature`, and lands in `summary.md` only.
    published = publish(work_dir, state, deliverable["deliverable"])
    if published["banners"]:
        banners = collect_banners({"fallback_banners": banners}, published["banners"])
        write_summary(banners)

    result = {
        "task_id": state.get("task_id"),
        "work_dir": str(work_dir),
        "current_phase": phase,
        "final_status": final_status,
        "final_status_reasons": reasons,
        "deliverable": deliverable["deliverable"],
        "deliverable_kind": deliverable["kind"],
        "summary": SUMMARY_MD,
        "published_to": published["published_to"],
        "published_memo": published["published_memo"],
        "published_files": published["files"],
        "banners": banners,
        "salvage": bool(args.salvage),
        "state_corrupt": state_corrupt,
        "already_terminal": already_terminal,
    }

    # M9: the terminal phase is written only now, with both files on disk.
    if state_corrupt:
        result["state_written"] = False
    else:
        result["state_written"] = _write_terminal(work_dir, args, phase, final_status, reasons, banners, result)
        if not result["state_written"]:
            # D-64: a finalize that delivered but left the phase non-terminal must exit 1. With
            # exit 0 the orchestrator counts the step as done, `plan_export` reissues `finalize`
            # (no `inputs`, so no D-58 dedup) and the `step_loop` guard deliberately skips it —
            # the run never ends. As an error the step closes `fail`: one `retry_failed_script`
            # recovery, then `transition(..., "failed")`.
            result["errors"] = [f"state_write_failed: {result.get('state_error')}"]

    result["tidy"] = tidy(work_dir)
    return result


ARGS_KEY = "finalize"


def _check_identity(work_dir: Path, args: argparse.Namespace, state: dict, state_corrupt: bool):
    """Run the strict identity check of §3.1 before `finalize` touches a file (D-40).

    Returns the command's answer on a mismatch or a closed step, and None when finalize may run.
    `--salvage` may proceed without an issued identity, and records why in the journal.
    """
    step_id = getattr(args, "step", None)
    if not step_id:
        return None
    attempt = int(getattr(args, "attempt", 1) or 1)
    if state_corrupt:
        _log_identity_skipped(work_dir, step_id, attempt, "state_unreadable")
        return None
    identity = stepctx.check_identity(state, step_id, attempt, args_key=ARGS_KEY)
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = identity.get("result")
        result = dict(stored) if isinstance(stored, dict) else {"result": stored}
        result["already_done"] = True
        return result
    if identity["status"] == stepctx.STATUS_MISMATCH:
        if args.salvage:
            _log_identity_skipped(work_dir, step_id, attempt, str(identity.get("reason")))
            return None
        return {
            "errors": list(identity["errors"]),
            "reason": identity.get("reason"),
            "step_id": step_id,
            "attempt": attempt,
        }
    return None


def _log_identity_skipped(work_dir: Path, step_id: str, attempt: int, reason: str) -> None:
    try:
        events.append_event(
            work_dir,
            "fallback_invoked",
            "cli",
            {"condition_key": "salvage_state_corrupt", "identity_check": "skipped", "reason": reason},
            step_id=step_id,
            severity="warn",
        )
    except (OSError, ValueError):
        # The journal is best effort; M9 must not depend on it (§7.2).
        pass


def _salvaged_state(work_dir: Path) -> dict:
    """Minimal state stand-in built from the work dir when `state.json` cannot be parsed (M9).

    D-144: it carries `salvage_state_corrupt` from the start. The banner was appended after
    `choose_deliverable` had already picked and written the deliverable, so the one degradation the
    reader most needed to see was the one the deliverable did not mention — and «`finalize` fixes
    its banner list before the deliverable is chosen» stopped being true.
    """
    corrupt = fallbacks.banner("salvage_state_corrupt")
    return {
        "task_id": work_dir.name,
        "work_dir": str(work_dir),
        "current_phase": None,
        "mode": None,
        "final_status": None,
        "final_status_reasons": [],
        "fallback_banners": [corrupt] if corrupt else [],
        "drafting_warnings": [],
        "draft_versions": [],
        "cancel_requested": False,
    }


def _write_terminal(
    work_dir: Path,
    args: argparse.Namespace,
    phase: str,
    final_status: str,
    reasons: list[str],
    banners: list,
    result: dict,
) -> bool:
    def mutate(state: dict) -> None:
        state["current_phase"] = phase
        state["final_status"] = final_status
        state["final_status_reasons"] = reasons
        state["fallback_banners"] = banners
        if result["deliverable_kind"] == "docx":
            state["final_docx_path"] = result["deliverable"]
        # D-111: the field describes *this* finalize, so a failed (or absent) publish clears it.
        # Left at the previous run's path it would send the router off to copy a stale result, and
        # the terminal text would keep printing a `Published:` line for a folder nothing refreshed.
        # D-167: the same holds for the root-level memo copy and its `Memo:` line.
        progress = state.get("progress")
        if isinstance(progress, dict):
            progress["published_to"] = result.get("published_to") or None
            progress["published_memo"] = result.get("published_memo") or None

    # D-13: salvage is the one caller allowed to skip the schema check; the step protocol itself
    # stays `stepctx.close_step` (D-40 — one publication/close implementation).
    writer = state_io.write_state_unvalidated if args.salvage else state_io.write_state
    try:
        if args.step:
            stepctx.close_step(
                work_dir,
                args.step,
                int(args.attempt or 1),
                {"deliverable": result["deliverable"], "summary": SUMMARY_MD},
                kind="terminal",
                phase=phase,
                args_key=ARGS_KEY,
                mutate=mutate,
                writer=writer,
            )
        else:
            writer(work_dir, mutate)
    except stepctx.IdentityMismatch as exc:
        result["state_error"] = str(exc)
        return False
    except (OSError, ValueError) as exc:
        result["state_error"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def run_tidy(args: argparse.Namespace) -> dict:
    """`mf tidy` — the same pass `finalize` runs, exposed standalone (§5.2)."""
    return tidy(args.workdir, dry_run=args.dry_run)


def register(subparsers) -> None:
    """Register `mf finalize` and `mf tidy` (both are top-level commands, §5.2)."""
    finalize = subparsers.add_parser("finalize", help="always-deliver: deliverable + summary + tidy")
    finalize.add_argument("--workdir", required=True)
    finalize.add_argument("--step", default=None)
    finalize.add_argument("--attempt", type=int, default=1)
    finalize.add_argument("--reason", default=None, help="free text; a reason means phase `failed`")
    finalize.add_argument(
        "--salvage",
        action="store_true",
        help="degraded path: no jsonschema, tolerates a corrupt state.json (M9)",
    )
    finalize.set_defaults(func=run_finalize)


    tidy_parser = subparsers.add_parser("tidy", help="remove *.tmp and events/.seen markers")
    tidy_parser.add_argument("--workdir", required=True)
    tidy_parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    tidy_parser.set_defaults(func=run_tidy)
