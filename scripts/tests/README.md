# Tests

`unittest` only — memoforge does not use `pytest`. Every test is offline: no network, no MCP,
no Anthropic API. Temporary work directories come from `tempfile.TemporaryDirectory()`.

## Running

From the plugin root:

```bash
python -m unittest discover -s scripts/tests -v
```

One module, or one case:

```bash
python -m unittest scripts.tests.test_machine -v
python -m unittest scripts.tests.test_lint.LintRulesTest.test_l08_blockquote_needs_quote_id -v
```

`jsonschema`, `python-docx` and `mistune` must be importable or the suite reports skips, and
**skips are failures in CI** (§9, G9). Install them with `pip install -r requirements.txt`, or
`mf deps install` for the plugin-data copy the CLI uses at runtime.

## Structure

| Group | Modules | What it covers |
|---|---|---|
| Sources of truth | `test_phases`, `test_modes`, `test_events`, `test_fallbacks`, `test_schema`, `test_schemas_artifacts` | M11 — one definition per enumeration, every schema of §6 positive/negative |
| State | `test_state_io`, `test_state_cmd`, `test_stepctx`, `test_no_unvalidated_state_writes` | M2 — locking, atomic replace, schema-before-replace, step identity |
| Machine | `test_machine`, `test_dispatch`, `test_gates`, `test_task`, `test_probe_dryrun`, `test_cli` | M1/M3/M8 — phase × mode × outcome, idempotent `next`/`report`, gate parsing, the end-to-end dry run, one `cli_call` per invocation (D-43) |
| Determinism before LLM | `test_lint`, `test_citations`, `test_review`, `test_revision`, `test_sufficiency`, `test_sources`, `test_quotes`, `test_routing`, `test_style_profile` | M5/M6/M10 — 14 lint rules, citation audit, checklist aggregation, source freeze |
| Output | `test_render`, `test_docx_fallback`, `test_finalize`, `test_analyze`, `test_docs_render` | M4/M9 — JSON→md views, docx and its stdlib fallback, always-deliver, `events analyze` |
| Platform | `test_hooks`, `test_hooks_common`, `test_pylauncher`, `test_launcher_wrappers`, `test_deps` | M12 — three interpreters, paths with spaces, hook bypasses, `mf deps check\|install` |
| Contracts | `test_agent_frontmatter`, `test_agent_prompt_hygiene`, `test_agent_outputs_are_json_only`, `test_skills`, `test_risk_line_contract`, `test_version_matches_plugin_json`, `test_check_versions`, **`test_no_legacy`** | §9 — prompts, skills and the manifest stay in sync with the code, and its published copies with the manifest (D-73) |

`_pipeline.py` is a shared driver, not a test module: it runs the `next → act → report` loop
against the `mf probe dry-run` fixtures and lets a test inject a failure at any step.

## Fixtures

`scripts/tests/fixtures/` holds the canned agent outputs (`prompts/`, `drafts/`, `reviews/`,
`schemas/`, `sufficiency/`) that the dry run and the deterministic layers replay. They are data,
never code: a test that needs a variant builds it from a fixture in its temporary directory
rather than editing the fixture in place.

`test_no_legacy.py` skips `scripts/tests/*.py` and `fixtures/` on purpose — a guard test has to
name the identifiers it forbids, and the fixtures replay v1-shaped inputs.

## CI

`.github/workflows/ci.yml` runs `ubuntu-latest` and `windows-latest` × Python 3.9 and 3.13:
`compileall`, the discovery run above with a **no unexpected skips** guard
(`scripts/ci/check_no_skips.py`: only the platform/environment reasons listed in that script are
allowed — a skip for a missing dependency is a red build),
`ruff check --select E,F,W scripts hooks`, `mf docs render --check`, `mf probe dry-run` in both
modes, and `scripts/ci/check_versions.py` (manifest version == `marketplace.json`
`plugins[0].version` == README badge == top of the changelog). A red CI is a broken build, not a
flake — no test in this suite depends on timing, locale or network.

## Not covered, deliberately

- Real memo runs (they need MCP, credentials and a live session) — those are the probes of §11,
  recorded by hand in `docs/probes/v2-probes.md`.
- Visual docx layout: the golden tests assert `document.xml` / `footnotes.xml` content, not how
  Word paints it.
- Cowork-specific runtime behaviour (chat flush, `SubagentStart` payloads) — probes P5/P8.
