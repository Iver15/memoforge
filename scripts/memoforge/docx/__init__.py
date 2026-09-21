"""`mf docx render|validate` — the export renderer (ТЗ §5.5).

`docx render` takes the `renderer.py` branch (mistune AST -> python-docx with real Word footnotes
and OSCOLA short forms) and writes `memo-<slug>.md` next to `memo-<slug>.docx` so the markdown view
always ships. Without `python-docx`/`mistune` (`ImportError`) or after any renderer exception the
markdown becomes the deliverable under the `docx_export_failed` banner (§5.5, §5.6).

D-117: `docx render` runs the structural checks of `validate.py` over the file it just wrote, before
publishing it — a docx that fails them is never published, goes to `memo-<slug>.invalid.docx`, hands
the deliverable back to the markdown and raises `docx_invalid`, and the whole verdict comes back in
the render answer. `docx validate` stays as the standalone command over an existing file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .. import events, fallbacks, i18n, state_io, stepctx
from . import fallback, validate

MEMO_STEM_PREFIX = "memo-"
EXPORT_PHASE = "export"
TASK_ID_RE = re.compile(r"^memo-[0-9]{8}T[0-9]{6}Z-(?P<slug>.+)$")

FOOTNOTES_MAP = "footnotes-map.json"
"""`steps/<step>/a<n>/cli/footnotes-map.json` — the map `docx validate` checks short forms against."""

RENDERER_DOCX = "docx"
RENDERER_FALLBACK = "md_fallback"

NO_CHECKED_DRAFT = "no_checked_draft"
"""`final_status_reasons[]` code of §2.1 row 15 / D-21 when no version passed lint + citations."""


def slug_of(state: dict | None, work_dir: Path) -> str:
    """Deliverable slug: the tail of `task_id` (`memo-<stamp>-<slug>`), else the work dir name."""
    for candidate in ((state or {}).get("task_id"), work_dir.name):
        if not isinstance(candidate, str):
            continue
        match = TASK_ID_RE.match(candidate)
        if match:
            return match.group("slug")
    return "memo"


def memo_md_path(work_dir: Path, slug: str) -> Path:
    """`<work_dir>/memo-<slug>.md` — the markdown deliverable of the fallback branch (§5.5)."""
    return work_dir / f"{MEMO_STEM_PREFIX}{slug}.md"


def memo_docx_path(work_dir: Path, slug: str) -> Path:
    """`<work_dir>/memo-<slug>.docx` — the deliverable of the renderer branch (§5.5)."""
    return work_dir / f"{MEMO_STEM_PREFIX}{slug}.docx"


def invalid_docx_path(work_dir: Path, slug: str) -> Path:
    """`<work_dir>/memo-<slug>.invalid.docx` — where a docx that failed validation goes (§5.5)."""
    return work_dir / f"{MEMO_STEM_PREFIX}{slug}.invalid.docx"


def _draft_candidates(state: dict, work_dir: Path) -> list[dict]:
    """`draft_versions[]` rows whose file is on disk, oldest version first, with the file's own sha."""
    rows: list[dict] = []
    for version in state.get("draft_versions") or []:
        if not isinstance(version, dict):
            continue
        relative = version.get("path")
        if not isinstance(relative, str) or not (work_dir / relative).is_file():
            continue
        rows.append(
            {
                "version": version.get("version"),
                "relative": relative,
                "path": work_dir / relative,
                "sha256": state_io.sha256_file(work_dir / relative),
                "recorded_sha256": version.get("sha256"),
                "lint_clean": bool(version.get("lint_clean")),
                "citations_clean": bool(version.get("citations_clean")),
            }
        )
    rows.sort(key=lambda row: int(row.get("version") or 0))
    return rows


def _loose_candidate(state: dict, work_dir: Path) -> dict | None:
    """Last resort of §2.1 row 15: a draft on disk that `draft_versions[]` never recorded."""
    paths: list[Path] = []
    current = state.get("current_draft_path")
    if isinstance(current, str) and current and (work_dir / current).is_file():
        paths.append(work_dir / current)
    if (work_dir / "drafts").is_dir():
        paths.extend(sorted((work_dir / "drafts").glob("v*.md")))
    for path in paths:
        return {
            "version": None,
            "relative": path.relative_to(work_dir).as_posix(),
            "path": path,
            "sha256": state_io.sha256_file(path),
            "recorded_sha256": None,
            "lint_clean": False,
            "citations_clean": False,
        }
    return None


def select_draft(state: dict, work_dir: Path, draft_sha: str | None = None) -> dict:
    """Pick the version to export and say how trustworthy it is (§2.1 row 15, §2.2).

    `--draft-sha` is matched against the **bytes on disk**, not against the `draft_versions[]`
    record: a state entry proves nothing about the file the export would read. Without a byte match
    the choice falls back to the rule of §2.1 row 15 — the last version that passed lint **and**
    citations while still hashing to the sha those checks saw; when there is none, the last version
    is exported with `no_checked_draft` and the matching banner, because export never blocks.
    """
    candidates = _draft_candidates(state, work_dir)
    selection: dict | None = None
    matched = None

    if draft_sha:
        matched = next((row for row in candidates if row["sha256"] == draft_sha), None)
        if matched is None and (loose := _loose_candidate(state, work_dir)) is not None:
            matched = loose if loose["sha256"] == draft_sha else None
        selection = matched

    if selection is None:
        selection = next(
            (
                row
                for row in reversed(candidates)
                if row["lint_clean"] and row["citations_clean"] and row["sha256"] == row["recorded_sha256"]
            ),
            None,
        )
    if selection is None:
        selection = candidates[-1] if candidates else _loose_candidate(state, work_dir)

    if selection is None:
        return {
            "path": None,
            "relative": None,
            "sha256": None,
            "version": None,
            "checked": False,
            "draft_sha_matched": False,
            "reasons": [],
            "banners": [],
        }

    checked = bool(
        selection["lint_clean"]
        and selection["citations_clean"]
        and selection["sha256"] == selection["recorded_sha256"]
    )
    reasons = [] if checked else [NO_CHECKED_DRAFT]
    banners = [] if checked else [fallbacks.banner(NO_CHECKED_DRAFT)]
    return {
        "path": selection["path"],
        "relative": selection["relative"],
        "sha256": selection["sha256"],
        "version": selection["version"],
        "checked": checked,
        "draft_sha_matched": bool(draft_sha) and matched is not None,
        "reasons": reasons,
        "banners": [banner for banner in banners if banner],
    }


def exported_pinpoints(work_dir: Path, draft: Path | None) -> list[dict]:
    """The C-09 findings of the draft chosen for export, for both renderers as data (D-204).

    The appendix describes the version the client receives, and `select_draft` may pick an earlier
    checked version than the last one audited — whose findings are all `citations.json` holds. So
    the findings are computed here, where the version is chosen, from that version's own bytes; the
    renderers stay free of the citation audit (D-195), which is imported only inside this function
    so that importing `docx.fallback` never loads it. Export never blocks (M9): a draft that cannot
    be read or audited costs the appendix its C-09 lines, never the delivery.
    """
    if draft is None:
        return []
    try:
        from .. import citations

        text = Path(draft).read_text(encoding="utf-8-sig")
        return citations.pinpoint_findings(text, work_dir=work_dir)
    except Exception:  # noqa: BLE001 - any failure here must not stop the export (M9)
        return []


def _step_results(state: dict):
    """Every closed step's stored `result` object, newest first (§2.2 `steps[].result_ref`)."""
    for row in reversed(state.get("steps") or []):
        if not isinstance(row, dict):
            continue
        ref = row.get("result_ref")
        result = ref.get("result") if isinstance(ref, dict) else None
        if not isinstance(result, dict):
            result = ref if isinstance(ref, dict) else None
        if isinstance(result, dict):
            yield result


def rendered_source_sha(state: dict, deliverable_rel: str) -> str | None:
    """sha of the draft an earlier `docx render` exported into `deliverable_rel` (§2.1 row 15).

    Recorded in the closed step's `result_ref`, so `finalize` can tell whether an existing
    `memo-<slug>.md` really came from the version it just selected.
    """
    for result in _step_results(state):
        if result.get("draft_sha") and deliverable_rel in (
            result.get("deliverable_path"),
            result.get("markdown_path"),
        ):
            return str(result["draft_sha"])
    return None


def status_signature(state: dict | None, extra_banners: list | None = None) -> str:
    """sha256 of everything the `## Status` section of an export was built from (D-123, D-144).

    `fallback.status_inputs` *is* that section — the final status, the banners with their ids and
    their texts, the rendered blocker lines — and both rendered forms read it, so hashing the whole
    dict is the one comparison that cannot miss a displayed input. The earlier signature carried
    `final_status` plus the banner **ids** only, so a run whose `remaining_blocking_issues` changed
    between the export and `finalize` still matched (R2-03). A docx cannot be rewritten in place, so
    `finalize` compares this signature with its own before handing an export to the client.

    D-175: the memo language is a displayed input like any other, so it is hashed — except for
    English, where the payload stays exactly the set of fields it had before the language existed.
    That keeps the English signature byte-identical, so a docx an earlier plugin version exported
    for a task that is still in flight is still accepted here; and it keeps two otherwise identical
    sections in different languages apart, which is what stops `finalize` from delivering an export
    written in a language the run no longer uses.
    """
    inputs = fallback.status_inputs(state, extra_banners)
    if inputs.get("language") == i18n.DEFAULT:
        inputs = {key: value for key, value in inputs.items() if key != "language"}
    payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return state_io.sha256_bytes(payload.encode("utf-8"))


def rendered_status_inputs(state: dict, deliverable_rel: str) -> str | None:
    """The `status_signature` the `docx render` that wrote `deliverable_rel` recorded (D-123).

    None when no closed render step vouches for that file — including an export written before this
    field was a sha (D-144), which `finalize` then treats as stale rather than as current.
    """
    for result in _step_results(state):
        signature = result.get("status_inputs")
        if isinstance(signature, str) and signature and deliverable_rel in (
            result.get("deliverable_path"),
            result.get("markdown_path"),
        ):
            return signature
    return None


def rendered_footnotes_map(state: dict) -> str | None:
    """Work-dir-relative path of the `footnotes-map.json` the last `docx render` wrote (§5.5)."""
    for result in _step_results(state):
        path = result.get("footnotes_map_path")
        if isinstance(path, str) and path:
            return path
    return None


def step_work_dir(work_dir: Path, step_id: str | None, attempt: int | None) -> Path:
    """`steps/<step_id>/a<attempt>/cli/` — the attempt workspace of a `--step` command (§2.2)."""
    safe_step = re.sub(r"[^A-Za-z0-9._-]", "_", str(step_id or "docx-render"))
    return work_dir / "steps" / safe_step / f"a{int(attempt or 1)}" / "cli"


def _relative(work_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(work_dir).as_posix()
    except ValueError:  # pragma: no cover - a deliverable always lives under work_dir
        return path.as_posix()


def begin_step(state: dict | None, args: argparse.Namespace, args_key: str) -> dict | None:
    """Identity gate of §3.1/D-40: None means «run», a dict is the command's whole answer.

    Skipped only when there is no `state.json` at all — then no step was ever issued and the command
    is running as a plain utility (`docx validate --path …`).
    """
    step_id = getattr(args, "step", None)
    if not step_id or not state:
        return None
    attempt = int(getattr(args, "attempt", 1) or 1)
    identity = stepctx.check_identity(state, step_id, attempt, args_key=args_key)
    if identity["status"] == stepctx.STATUS_MISMATCH:
        return {
            "errors": list(identity["errors"]),
            "reason": identity.get("reason"),
            "step_id": step_id,
            "attempt": attempt,
        }
    if identity["status"] == stepctx.STATUS_CLOSED:
        stored = identity.get("result")
        result = dict(stored) if isinstance(stored, dict) else {"result": stored}
        result["already_done"] = True
        return result
    return None


def run_render(args: argparse.Namespace) -> dict:
    """`mf docx render` — the AST renderer, with the markdown view always written next to it (§5.5).

    Both `memo-<slug>.docx` and `memo-<slug>.md` are produced on every run: the markdown is what the
    fallback branch and `finalize` fall back to, so it must exist even when the docx renders cleanly.
    `ImportError` (no `python-docx`/`mistune`) and any renderer exception downgrade the run to the
    markdown branch with the `docx_export_failed` banner (§5.5, §5.6).

    D-181: an unreadable memo pack is not one of those — a memo language is never silently replaced
    by English (D-175), so the command answers the documented `language_pack_unavailable: <code>`
    business error, the shape `finalize.run_finalize` already uses, and writes nothing.
    """
    work_dir = Path(args.workdir)
    state = state_io.read_state_or_none(work_dir)
    args_key = f"docx render --draft-sha={args.draft_sha or ''}"
    saved = begin_step(state, args, args_key)
    if saved is not None:
        return saved

    state = state or {}
    slug = slug_of(state, work_dir)
    selection = select_draft(state, work_dir, args.draft_sha)
    draft = selection["path"]
    if draft is None or not draft.is_file():
        return {"errors": ["no_draft_to_render"], "draft_sha": args.draft_sha}

    step_dir = step_work_dir(work_dir, args.step, args.attempt)
    language = fallback.memo_language(state)
    # D-204: the appendix of the export describes the version exported, which may be older than the
    # last one audited — so its C-09 findings are computed here, once, for both renderers.
    pinpoints = exported_pinpoints(work_dir, draft)
    try:
        rendered = fallback.render_workdir(work_dir, draft, state=state, pinpoint_findings=pinpoints)
    except i18n.PackUnavailable:
        # The first label of the run is looked up here, so this is where a pack that cannot be
        # read shows up — before any file of the step is written.
        return {"errors": [f"language_pack_unavailable: {language}"]}

    md_staged = step_dir / f"{MEMO_STEM_PREFIX}{slug}.md"
    payload = rendered["markdown"].encode("utf-8")
    state_io.write_bytes_atomic(md_staged, payload)
    md_target = memo_md_path(work_dir, slug)

    banners = list(selection["banners"]) + list(rendered["banners"])
    docx_staged = step_dir / f"{MEMO_STEM_PREFIX}{slug}.docx"
    try:
        exported = render_docx(
            work_dir,
            draft,
            docx_staged,
            state=state,
            banners=banners,
            reasons=selection["reasons"],
            pinpoint_findings=pinpoints,
        )
    except stepctx.OutputModifiedAfterPublish as exc:
        return stepctx.drift_result(exc, draft_sha=selection["sha256"])

    if not exported["ok"]:
        failed = fallbacks.banner("docx_render_failed")
        if failed is not None:
            banners.append(failed)

    # D-117: the file is validated inside this step, by the very code path `docx validate` runs, so
    # the orchestrator needs no second round trip (one live run idled 20 minutes between the two).
    validation = None
    if exported["ok"]:
        validation = dict(
            validate.validate_path(
                docx_staged, footnotes_map=exported["map"], language=language
            )
        )
        if not validation["valid"]:
            invalid = fallbacks.banner("docx_invalid")
            if invalid is not None:
                banners.append(invalid)

    valid_docx = bool(exported["ok"]) and (validation is None or bool(validation["valid"]))

    result = {
        "renderer": RENDERER_DOCX if exported["ok"] else RENDERER_FALLBACK,
        "deliverable_path": _relative(work_dir, md_target),
        "markdown_path": _relative(work_dir, md_target),
        "work_path": _relative(work_dir, md_staged),
        "sha256": state_io.sha256_bytes(payload),
        "markdown_sha256": state_io.sha256_bytes(payload),
        "draft_path": selection["relative"],
        "draft_sha": selection["sha256"],
        "draft_version": selection["version"],
        "draft_checked": selection["checked"],
        "draft_sha_matched": selection["draft_sha_matched"],
        "final_status_reasons": list(selection["reasons"]),
        "footnotes": exported["footnotes"] if exported["ok"] else rendered["footnotes"],
        "unresolved": exported["unresolved"] if exported["ok"] else rendered["unresolved"],
        "banners": list(banners),
        "docx": None,
        "footnotes_map_path": None,
        "render_error": exported["error"],
        "validation": validation,
        "valid": None if validation is None else bool(validation["valid"]),
        "demoted_to": None,
        "revoked": None,
        "revoke_error": None,
        # D-123: what the `## Status` section of this export says, so `finalize` can tell whether it
        # still matches the run's terminal status before delivering the file.
        "status_inputs": status_signature(state, banners),
    }

    publications = [(md_staged, md_target)]
    if exported["ok"]:
        map_path = step_dir / FOOTNOTES_MAP
        state_io.write_json_atomic(map_path, exported["map"])
        result["footnotes_map_path"] = _relative(work_dir, map_path)
    if valid_docx:
        docx_target = memo_docx_path(work_dir, slug)
        publications.append((docx_staged, docx_target))
        result["deliverable_path"] = _relative(work_dir, docx_target)
        result["docx"] = _relative(work_dir, docx_target)
        result["sha256"] = state_io.sha256_file(docx_staged)
    elif exported["ok"]:
        # §5.5 demotion, same file name as `docx validate` uses: the docx is kept for inspection
        # under `memo-<slug>.invalid.docx`, is never published, and the markdown is the deliverable.
        invalid_target = invalid_docx_path(work_dir, slug)
        state_io.write_bytes_atomic(invalid_target, docx_staged.read_bytes())
        result["demoted_to"] = _relative(work_dir, invalid_target)
    if not valid_docx:
        revocation = _revoke_docx(work_dir, slug, state)
        result["revoked"] = revocation["revoked"]
        result["revoke_error"] = revocation["error"]

    degradation = None
    if not exported["ok"]:
        degradation = "docx_render_failed"
    elif not valid_docx:
        degradation = "docx_invalid"

    if state:
        _publish(
            work_dir, args, result, banners, publications, args_key, degradation, result["revoked"]
        )
    else:
        for staged, target in publications:
            state_io.write_bytes_atomic(target, staged.read_bytes())
        result["state_written"] = False
    return result


def _revoke_docx(work_dir: Path, slug: str, state: dict) -> dict:
    """Revoke the `memo-<slug>.docx` an earlier render published when this one produced none (§5.5).

    D-117 demotion writes the fresh bytes to `memo-<slug>.invalid.docx` and clears
    `final_docx_path`, but a previous successful render of the *same* draft left its own export on
    the canonical path — still on disk, still in `published[]`, still bound to the sha `finalize`
    selects. `finalize._existing_docx` would deliver that file as if this render had never failed,
    so the export is revoked together with the verdict: the file and its `published[]` row both go.

    D-144: the **logical** revoke does not depend on the unlink. A file another process holds open
    (or a read-only volume) used to leave the `published[]` row and `final_docx_path` in place, so
    the next `finalize` delivered the stale export anyway (R2-02). The row is dropped in this step's
    state write either way; a file left behind is no deliverable, because `_existing_docx` hands
    back nothing that `published[]` does not vouch for. The unlink error travels as `revoke_error`.
    """
    target = memo_docx_path(work_dir, slug)
    relative = _relative(work_dir, target)
    published = stepctx.published_sha(state, relative) is not None
    if not target.is_file() and not published:
        return {"revoked": None, "error": None}
    error: str | None = None
    if target.is_file():
        try:
            target.unlink()
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
    return {"revoked": relative, "error": error}


def render_docx(
    work_dir: Path,
    draft: Path,
    target: Path,
    *,
    state: dict,
    banners: list,
    reasons: list,
    pinpoint_findings: list | None = None,
) -> dict:
    """Run `renderer.py`; a missing dependency or any renderer failure answers `ok: False` (§5.5)."""
    try:
        from . import renderer
    except ImportError as exc:  # no `python-docx`/`mistune` (§5.6) -> the markdown branch
        return {
            "ok": False,
            "error": f"ImportError: {exc}",
            "footnotes": [],
            "unresolved": [],
            "map": None,
        }
    try:
        result = renderer.render_workdir(
            work_dir,
            draft,
            target,
            state=state,
            banners=banners,
            final_status_reasons=reasons,
            pinpoint_findings=pinpoint_findings,
        )
    except stepctx.OutputModifiedAfterPublish:
        raise
    except (renderer.RenderError, OSError) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "footnotes": [],
            "unresolved": [],
            "map": None,
        }
    return {
        "ok": True,
        "error": None,
        "footnotes": result["footnotes"],
        "unresolved": result["unresolved"],
        "map": renderer.footnotes_map(result),
    }


def _publish(
    work_dir: Path,
    args: argparse.Namespace,
    result: dict,
    banners: list,
    publications: list,
    args_key: str,
    degradation: str | None,
    revoked: str | None = None,
) -> None:
    """Publish every deliverable through `stepctx` and close the step in one state write (§2.2, D-40)."""
    entries = [
        stepctx.publish_file(work_dir, staged, target, by="command", step_id=args.step)
        for staged, target in publications
    ]

    def mutate(state: dict) -> None:
        _merge_banners(state, banners)
        state["final_docx_path"] = result["docx"]
        if revoked:
            # D-117/D-144: the row goes with the verdict, in the same write that clears
            # `final_docx_path` — whether or not the bytes could be removed. A file the row no
            # longer vouches for is not a deliverable (`finalize._existing_docx`), and a row
            # outliving a deleted file would read as drift in `verify_published` (§2.2).
            state["published"] = [
                row
                for row in (state.get("published") or [])
                if not (isinstance(row, dict) and row.get("canonical_path") == revoked)
            ]
        reasons = [str(row) for row in (state.get("final_status_reasons") or [])]
        for reason in result["final_status_reasons"]:
            if reason not in reasons:
                reasons.append(reason)
        state["final_status_reasons"] = reasons

    try:
        if args.step:
            stepctx.close_step(
                work_dir,
                args.step,
                int(args.attempt or 1),
                result,
                phase=EXPORT_PHASE,
                args_key=args_key,
                published=entries,
                mutate=mutate,
            )
        else:

            def plain(state: dict) -> None:
                stepctx.merge_published(state, entries)
                mutate(state)

            state_io.write_state(work_dir, plain)
    except stepctx.IdentityMismatch as exc:
        result.clear()
        result.update(exc.as_result())
        return
    except (OSError, ValueError) as exc:
        result["state_written"] = False
        result["state_error"] = f"{type(exc).__name__}: {exc}"
        return
    result["state_written"] = True
    if degradation:
        events.append_event(
            work_dir,
            "fallback_invoked",
            "cli",
            {"condition_key": degradation, "renderer": RENDERER_FALLBACK},
            phase=EXPORT_PHASE,
            step_id=args.step,
            severity="warn",
        )


def _merge_banners(state: dict, banners: list) -> None:
    existing = state.setdefault("fallback_banners", [])
    known = {row.get("banner_id") for row in existing if isinstance(row, dict)}
    for banner in banners:
        if isinstance(banner, dict) and banner.get("banner_id") not in known:
            existing.append(banner)
            known.add(banner.get("banner_id"))


def _footnotes_map(work_dir: Path | None, state: dict, args: argparse.Namespace) -> dict | None:
    """`footnotes-map.json` of the `docx render` that produced the file under validation (§5.5)."""
    candidates: list[str] = []
    explicit = getattr(args, "map_path", None)
    if explicit:
        candidates.append(str(explicit))
    if work_dir is not None:
        recorded = rendered_footnotes_map(state)
        if recorded:
            candidates.append(str(work_dir / recorded))
    for candidate in candidates:
        try:
            value = state_io.read_json(Path(candidate))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def run_validate(args: argparse.Namespace) -> dict:
    """`mf docx validate` — the §5.5 checks; a failure demotes the docx to `memo-<slug>.invalid.docx`.

    D-28: it is a `--step` command like every other script step, so `machine.py` needs no completion
    predicate of its own; without `--step` it stays the plain utility the tests call.

    D-181: `--language <code>` is the memo language of a document validated without a `--workdir` —
    there is no `state.json` to read it from, and a localized `Status` section is invisible to the
    English run the check looks for. Nothing is inferred from the document or from the footnotes
    map; without the flag the language is the state's, exactly as before.
    """
    work_dir = Path(args.workdir) if args.workdir else None
    state = state_io.read_state_or_none(work_dir) if work_dir else None
    args_key = f"docx validate --path={args.path or ''}"
    saved = begin_step(state, args, args_key)
    if saved is not None:
        return saved

    requested = getattr(args, "language", None)
    language = fallback.memo_language(state) if requested is None else i18n.normalize(requested)
    if language is None:
        return {"errors": [f"invalid_language: {requested}"], "valid": None}

    slug = slug_of(state or {}, work_dir) if work_dir is not None else "memo"
    path = Path(args.path) if args.path else (memo_docx_path(work_dir, slug) if work_dir else None)
    if path is None:
        return {"errors": ["no_docx_path"], "valid": None}

    if not path.is_file():
        # `docx render` already fell back to markdown: there is nothing to invalidate (§5.5, M9).
        result = {
            "skipped": True,
            "reason": "no_docx_to_validate",
            "valid": None,
            "path": str(path),
            "exists": False,
            "demoted_to": None,
            "banners": [],
        }
        _close_validate(work_dir, state, args, result, args_key, status="skipped")
        return result

    # D-51: no banner is passed in — an `[unresolved:` literal invalidates the docx unconditionally,
    # and a missing `footnotes-map.json` is an error of the report, not a reason to skip checks.
    report = validate.validate_path(
        path, footnotes_map=_footnotes_map(work_dir, state or {}, args), language=language
    )
    result = dict(report)
    result["skipped"] = False
    result["demoted_to"] = None
    result["banners"] = []

    if not report["valid"] and work_dir is not None:
        result.update(_demote(work_dir, slug, path))
    _close_validate(
        work_dir, state, args, result, args_key, status="ok" if report["valid"] else "fail"
    )
    return result


def _demote(work_dir: Path, slug: str, path: Path) -> dict:
    """Rename the docx, hand the deliverable back to the markdown and raise `docx_invalid` (§5.5)."""
    invalid = invalid_docx_path(work_dir, slug)
    try:
        os.replace(path, invalid)
    except OSError as exc:  # pragma: no cover - a locked file leaves the docx where it is
        return {"demoted_to": None, "banners": [], "demote_error": f"{type(exc).__name__}: {exc}"}
    banner = fallbacks.banner("docx_invalid")
    return {
        "demoted_to": _relative(work_dir, invalid),
        "banners": [banner] if banner else [],
        "deliverable_path": _relative(work_dir, memo_md_path(work_dir, slug)),
    }


def _close_validate(
    work_dir: Path | None,
    state: dict | None,
    args: argparse.Namespace,
    result: dict,
    args_key: str,
    *,
    status: str,
) -> None:
    """Close the step and, when the docx was demoted, record the banner and drop its publication."""
    demoted = result.get("demoted_to")
    banners = list(result.get("banners") or [])
    stale = result.get("path")

    def mutate(inner: dict) -> None:
        if not demoted:
            return
        _merge_banners(inner, banners)
        inner["final_docx_path"] = None
        if work_dir is not None and stale:
            relative = _relative(work_dir, Path(stale))
            inner["published"] = [
                row
                for row in (inner.get("published") or [])
                if not (isinstance(row, dict) and row.get("canonical_path") == relative)
            ]

    if work_dir is None or state is None:
        return
    step_id = getattr(args, "step", None)
    if not step_id:
        if demoted:
            state_io.write_state(work_dir, mutate)
        return
    try:
        stepctx.close_step(
            work_dir,
            step_id,
            int(getattr(args, "attempt", 1) or 1),
            result,
            phase=EXPORT_PHASE,
            status=status,
            args_key=args_key,
            mutate=mutate,
        )
    except stepctx.IdentityMismatch as exc:
        result.clear()
        result.update(exc.as_result())
        return
    if demoted:
        events.append_event(
            work_dir,
            "fallback_invoked",
            "cli",
            {"condition_key": "docx_invalid", "path": demoted},
            phase=EXPORT_PHASE,
            step_id=step_id,
            severity="warn",
        )


def register(subparsers) -> None:
    """Register the `docx` command group (`render`, `validate`)."""
    from .. import cli

    group = cli.group_subparsers(subparsers, "docx", "export renderer (§5.5)")

    render = group.add_parser("render", help="render memo-<slug>.docx plus the markdown view")
    render.add_argument("--workdir", required=True)
    render.add_argument("--step", default=None)
    render.add_argument("--attempt", type=int, default=1)
    render.add_argument("--draft-sha", dest="draft_sha", default=None)
    render.set_defaults(func=run_render)

    validate_cmd = group.add_parser("validate", help="run the §5.5 checks over a rendered docx")
    validate_cmd.add_argument("--workdir", default=None)
    validate_cmd.add_argument("--path", default=None)
    validate_cmd.add_argument(
        "--map", dest="map_path", default=None, help="footnotes-map.json of the render step"
    )
    validate_cmd.add_argument(
        "--language",
        default=None,
        help="memo language of the document; default: the task's with --workdir, else English (D-181)",
    )
    validate_cmd.add_argument("--step", default=None)
    validate_cmd.add_argument("--attempt", type=int, default=1)
    validate_cmd.set_defaults(func=run_validate)
