"""A finished memo task for the decision-brief driver tests (plan 75A, D-224).

`FinishedTask` copies a task that `_pipeline.Driver(...).run_to_end()` finished (built once per
language pair and process, then copied, so every case starts on its own fresh work dir) and drives
`mf brief next|report` through `cli.build_parser()`. The fixture agents write their output and then
run `mf agent log --state done`, exactly as a real agent does.
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from pathlib import Path

from _pipeline import Driver
from memoforge import brief, cli, dispatch, state_io

CLEAN_BRIEF = """# Customer records: decision brief

**Date:** 2026-09-23
**Jurisdictions:** EU
**Question:** How long may the client keep customer records?

## Bottom line

The client may keep customer records only while the purpose that justified them lasts. The risk is \
medium, and counsel should confirm the tax basis before the next audit.

## Conclusions

### Retention period

<!-- from §s-3 -->

Records may not be kept beyond their purpose [[src:fixture-case-law-source-1 art 5(1)(e)]]. A tax duty \
changes this only if it covers these records.

Risk: medium. The exposure turns on the tax carve-out.

## What to do

1. Counsel confirms the tax basis for the seven-year period before the next audit.
"""
"""A brief of the fixture memo that passes B-01…B-10 (one leaf, `s-3`, risk medium)."""

TASK_FILE_PATTERNS = ("state.json", "summary.md", "memo-*.*", "deliverable.*")
"""The task files a brief run never writes (success criterion 5)."""

_TEMPLATES: dict[tuple[str, str], Path] = {}


def _template(language: str, ui_language: str) -> Path:
    """One finished task per language pair and process; every case copies it."""
    key = (language, ui_language)
    if key not in _TEMPLATES:
        root = Path(tempfile.mkdtemp(prefix="mf-brief-template-"))
        atexit.register(shutil.rmtree, root, True)
        driver = Driver(root, language=language, ui_language=ui_language)
        driver.run_to_end()
        _TEMPLATES[key] = driver.work_dir
    return _TEMPLATES[key]


def _parse(argv: list[str]) -> dict:
    args = cli.build_parser().parse_args(argv)
    return args.func(args)


class FinishedTask:
    """A fresh finished task in its own temp dir, plus the brief CLI and the fixture agents."""

    def __init__(self, case, *, language: str = "en", ui_language: str = "en") -> None:
        holder = tempfile.TemporaryDirectory(prefix="mf-brief-")
        case.addCleanup(holder.cleanup)
        source = _template(language, ui_language)
        self.W = Path(holder.name) / source.name
        shutil.copytree(source, self.W)
        self.snapshot: dict[str, bytes] | None = None

    # -- the task -----------------------------------------------------------
    def state(self) -> dict:
        return state_io.read_state(self.W)

    def write_state(self, mutator) -> dict:
        return state_io.write_state(self.W, mutator, emit_event=False)

    def task_files(self) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for pattern in TASK_FILE_PATTERNS:
            for path in sorted(self.W.glob(pattern)):
                if path.is_file():
                    files[path.name] = path.read_bytes()
        return files

    def freeze(self) -> None:
        """Remember the task files the first time the brief CLI runs (after every test setup)."""
        if self.snapshot is None:
            self.snapshot = self.task_files()

    # -- the brief ----------------------------------------------------------
    def run(self) -> dict | None:
        return brief.read_run(self.W)

    def next(self) -> dict:
        self.freeze()
        return _parse(["brief", "next", "--workdir", str(self.W)])

    def report(self, action: dict, *, run: str | None = None, step: str | None = None,
               attempt: int | None = None, **options: str) -> dict:
        self.freeze()
        argv = [
            "brief", "report", "--workdir", str(self.W),
            "--run", run or action["run_id"],
            "--step", step or action["step_id"],
            "--attempt", str(attempt if attempt is not None else action["attempt"]),
        ]
        for key, value in options.items():
            argv += [f"--{key}", str(value)]
        return _parse(argv)

    # -- fixture agents -----------------------------------------------------
    def agent_done(self, action: dict, slot: str) -> dict:
        return _parse([
            "agent", "log", "--workdir", str(self.W), "--step", action["step_id"],
            "--attempt", str(action["attempt"]), "--slot", slot, "--state", "done",
        ])

    def write_output(self, action: dict, slot: str, payload: str) -> Path:
        agent = next(item for item in action["agents"] if item["slot"] == slot)
        target = self.W / agent["expected_outputs"][0]["work_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload.encode("utf-8"))
        return target

    def write_fixture_writer(self, action: dict, text: str = CLEAN_BRIEF) -> Path:
        target = self.write_output(action, "writer", text)
        self.agent_done(action, "writer")
        return target

    def write_fixture_reviews(self, action: dict, *, fidelity: dict | None = None, form: dict | None = None) -> None:
        sha = self.run()["versions"][-1]["sha256"]
        documents = {"fidelity": fidelity or approving_fidelity(sha), "form": form or approving_form(sha)}
        for agent in action["agents"]:
            slot = agent["slot"]
            self.write_output(action, slot, json.dumps(documents[slot], ensure_ascii=False))
            self.agent_done(action, slot)


def _checklist(name: str) -> list[dict]:
    return state_io.read_json(Path(dispatch.lib_path("lib", "checklists", f"{name}.json")))


def _all_pass(name: str) -> list[dict]:
    return [{"id": row["id"], "pass": True, "evidence": "Fixture evidence."} for row in _checklist(name)]


def approving_fidelity(draft_sha: str) -> dict:
    return {
        "reasoning": "Fixture grader: the brief says what the memorandum says.",
        "reviewer": "brief_fidelity",
        "draft_sha": draft_sha,
        "verdict": "approved",
        "checklist": _all_pass("brief-fidelity"),
        "issues": [],
    }


def approving_form(draft_sha: str) -> dict:
    return {
        "reasoning": "Fixture grader: the brief reads well.",
        "reviewer": "form",
        "draft_sha": draft_sha,
        "iteration": 1,
        "checklist": _all_pass("brief-form"),
        "issues": [],
        "verdict": "approved",
    }
