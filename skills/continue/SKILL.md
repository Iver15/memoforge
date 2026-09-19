---
name: continue
description: Resume a memoforge legal-memo task and answer its open gate. Use only when explicitly invoked via /memoforge:continue, optionally with a task_id and a gate reply (proceed, continue, approve, edit: …, cancel, or answers like 1A 2C).
argument-hint: "[<task_id>] [1A 2C 3: <text> | proceed | continue | approve [brief|full] [style:<name>] | edit: <text> | cancel]"
disable-model-invocation: true
allowed-tools: Read, Write, Bash, Agent, AskUserQuestion, WebFetch, WebSearch, mcp__*
---

# memoforge / continue — re-entry into the router

Same protocol as `/memoforge:memo`: **read `skills/memo/references/router.md` now** and follow it, plus the authority and untrusted-content rules of `skills/memo/SKILL.md`. This file only covers getting back into the loop.

Every reply to the user is in the task's `ui_language` (D-178): `text`/`questions`/`reprompt` arrive localized from the CLI — print them verbatim; translate the English `chat_line`, the terminal response and error explanations yourself. Never translate tokens in backticks, file paths, commands or the `text_fallback` reply forms.

## 1. Split `$ARGUMENTS`

The first token is a `task_id` only if it looks like one (`memo-…`). Everything else is the **reply** — it may be empty.

## 2. Resolve the task

```
mf task resolve [task_id] --workdir <dir>   # both optional; no id = last unfinished task
```

- `{"unsupported": true, …}` → print `hint` ("task from v1 — finish it with memoforge 1.1.1 or start a new task") and END.
- `{"errors": ["no_unfinished_task"]}` → print `No unfinished memoforge task. Start one with /memoforge:memo "<question>".` and END; `task_not_found` → print it and suggest `/memoforge:status`.
- Otherwise keep `work_dir` as `W` and note `current_phase`, `gate`, `cancel_requested`.
- The descriptor carries no language: read it once with `mf state get --workdir W --path ui_language` and answer in it from here on — `path_not_found: ui_language` is a task from before the option, which means English; it is a business answer, never a CLI failure, so do not retry or finalize on it.

## 3. `cancel` first — before looking at the phase

If the reply contains the word `cancel` (ТЗ §2.4 d), run `mf task cancel --workdir W` immediately, print one line confirming the cancellation, then continue at step 5: `next` issues no new work and walks the task to its finalized terminal step.

## 4. A reply to an open gate

- At the plan gate an `Edit` answer that asks for another memo language is not an edit for the planner: run `mf task language --workdir W --memo <code>`, then re-issue the gate with `mf next`. The error `language_locked` means the plan was already approved — tell the user the memo language is fixed.
- This case never goes to `gate parse` — check it first, before the parser step below.

If the reply is non-empty, is not that memo-language case, and the task is at a gate (`"gate": true`), call `mf next --workdir W` to learn the open gate step — do **not** print its prompt again — and take `step_id`, `attempt`, `generation` from it. Hand the reply to the parser, which also closes the step:

```
mf gate parse --workdir W --step s-002 --attempt 1 --generation 0 --text "<reply verbatim>"
```

This is the **only** route for a text reply, `kind: gate-text` and `kind: gate-auq` alike (ТЗ D-34): for an AUQ gate at `generation 0` the command performs the channel switch itself, writes `gate_channel_switched` and then parses the reply. Never convert the reply into AUQ answers by hand and never switch the channel yourself.

`--generation` is mandatory — pass the value `next` just returned; an answer at another generation is rejected as `stale_generation`.

A `{"errors": […], "reprompt": "<text>"}` answer is a **business outcome**, not a CLI failure (D-72): the reply was not understood or only partly understood and nothing is recorded. Print `reprompt` verbatim and END the turn — never repeat the command and never finalize; the user's next reply re-enters here.

If the reply is empty, or the task is not at a gate, skip this step — the loop below re-issues whatever is open.

## 5. Run the loop

Continue exactly as `router.md` describes: `mf next --workdir W` → render `chat_line` in the task's `ui_language` → act on `kind` → `mf report` where the kind requires it → `next` again, until a `gate-text` or `terminal` step ends the turn.

An answer that carries a `dashboard` key is handled by `router.md` §2a — one `Artifact` call per step (publish once, then `write_db`) — with `file_path` from the block and `if_version` = the version the previous write reported; `version_mismatch` → one `get`, one resend. It is **Step 0** of that answer: make the call before acting on `kind` (before the `Agent` dispatch, before `command[]`, before `AskUserQuestion`, before a terminal message), never after, and never inside the same Bash call as `next` — it is an `Artifact` tool call, not a Bash command. An error there is never a step failure.
