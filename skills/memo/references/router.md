# Router protocol — `next → act → report` (ТЗ §3.1, §2.4, §2.5)

`mf` = `${CLAUDE_PLUGIN_ROOT}/scripts/mf`. If that path is not executable in this host (Windows without a POSIX shell), call `${CLAUDE_PLUGIN_ROOT}/scripts/mf.cmd` with the same arguments; every example below is written with `<mf>` standing for whichever of the two ran.

## 1. Get `work_dir`

- `/memoforge:memo "<query>"` → `<mf> task new --query "$ARGUMENTS" --detected-language <code>`, `<code>` = the language the user wrote the question in, one of `en de fr es ru` (any other language → omit the flag). Add `--language <code>` **only** when they asked for the memo in a language ("memo in German") — never inferred from the question; a language outside the five → do not create the task, name the five. The answer carries `language`, `ui_language`, `language_source`; when `language` is not `en`, or differs from the language of the question, say it once: `Memo language: German`.
- `/memoforge:continue [task_id] [reply…]` → `<mf> task resolve [task_id]` (no id = last unfinished task).
- Both answer JSON with `work_dir`; `task resolve` also gives `current_phase`, `gate`, `cancel_requested`.
- Print that absolute path once, as one chat line: `Working folder: <work_dir>`. In a hosted VM it is the only way the user reaches the deliverable. `task new` also answers `options_source` (where each plugin option came from — `mf config show` explains it); say nothing about it unless asked.
- `{"unsupported": true, …}` → print `hint` and END the turn. `{"errors": […]}` → print them and END.

Store `work_dir` as `W` and pass `--workdir W` to every later call. Never guess a work dir, never read `state.json` to decide what to do.

## 2. The loop

Call `<mf> next --workdir W`. Print the `chat_line` verbatim as a single chat line when the answer has one (that is the only progress action you owe — `description` is already inside the `next` answer). Then act on `kind`, then call `next` again — except after a `gate-text` or `terminal` step, which END the turn.

**Step 0 of every answer — the dashboard write, before anything else.** If the answer carries `dashboard.publish`, publish the page and register the URL; if it carries `dashboard.write_db`, make that one `Artifact` call **before** you act on `kind`: before the `Agent` dispatch, before running `command[]`, before `AskUserQuestion`, before printing a terminal message. Never skip it, never do it after. It is an `Artifact` tool call, not a Bash command, so it can never be folded into the same Bash call as `next`. §2a has the arguments and the one failure rule.

**Never perform a step `next` did not hand you.** Do not skip, reorder or invent phases.

**Exception to "act on `kind`" (D-34, D-67):** when the user has already replied to a gate that is still open, that reply is the answer — hand it to `<mf> gate parse … --generation <g>` and never re-print the prompt or call `AskUserQuestion` a second time. Acting on `kind` means *issuing* the gate, not re-issuing one the user just answered.

### `kind: dispatch` — subagents

Send **all** slots in ONE message (one `Agent` call per slot, all tool calls in the same assistant message); `parallel: true` makes this mandatory and serial dispatch is detected by `mf events analyze`. For each entry of `agents[]` pass its fields through unchanged: `subagent_type`, `model`, `description`, `prompt`. Add nothing of your own to the prompt.

When every subagent has returned, report each slot:

```
<mf> report --workdir W --step s-017 --attempt 1 --agent statutes --status ok
```

Use `--status fail` for a slot that errored or returned nothing (optionally `--stdout <file>` with its final message). One call per slot; the step closes when all slots are in.

### `kind: script` — one CLI command

Run `command[]` **literally** through Bash (it already carries the absolute `mf` path, `--workdir`, `--step`, `--attempt`). No `report` is needed — the command closes its own step. Then call `next` again.

### `kind: gate-auq` — print the plan, then one AskUserQuestion

Print `text` verbatim first — it is what the user decides on: the plan digest (issues, layers, recommended mode, the `plan.json` path), or, when a dashboard page is live, the three lines that point at it (D-94). The options alone are not a decision. Then call `AskUserQuestion` with `questions` from the answer, unchanged. Then report the chosen labels keyed by question header:

```
<mf> report --workdir W --step s-004 --attempt 1 --generation 0 \
     --answers '{"Plan":"Approve","Mode":"Full","Style":"my-firm","Sources":"Continue"}'
```

An `Edit` answer that asks for another memo language is not an edit for the planner: run `<mf> task language --workdir W --memo <code>`, then re-issue the gate with `<mf> next`. The error `language_locked` means the plan was already approved — tell the user the memo language is fixed.

If the AskUserQuestion tool itself errors in this same turn, switch channel: `<mf> report --workdir W --step s-004 --attempt 1 --status no_answer`; `next` then returns the equivalent `gate-text` at `generation 1`. A **text** answer from the user to this gate needs no switch — hand it straight to `gate parse` (D-34), which bumps the generation itself.

### `kind: gate-text` — ask and stop

Print `text` verbatim (the complete CLI-generated prompt, slash-command line included; with a dashboard page live it is instead a short pointer — the questions are on the page, and `text_fallback` holds the full prompt, D-103) and END the turn. The user's reply comes back through `/memoforge:continue` or as a plain message, and is parsed with `<mf> gate parse --workdir W --step <id> --attempt <n> --generation <g> --text "<reply>"`, which closes the gate step itself. Then resume the loop.

### `kind: inline-llm` — you write one JSON file

Produce the document described by `instruction`, valid against the schema at `schema`, and Write it to `W/<write_to>`. Then `<mf> report --workdir W --step s-011 --attempt 1 --status ok`; the CLI validates and publishes it.

### `kind: terminal` — done

Print `text` verbatim (it lists the deliverable, the summary, the `Memo:` copy and, when the CLI copied the result out, a `Published:` folder) and END the turn — after one extra action: if this host has a `present_files` tool (Cowork), present the `Memo:` file and the summary with it so they appear in the chat; otherwise, if the host gives you device file tools and the user has a connected folder, copy the published folder into `<connected folder>/memoforge/<slug>/` and add one line saying where it landed. Without such tools, the paths in `text` are the answer.

## 2a. `dashboard` — only when the answer carries it (ТЗ §7.5, D-87)

An answer with `dashboard.publish` (the run has `dashboard: on` and no page yet): call `Artifact` **once** with exactly those arguments (`file`, `title`, `description`, `capabilities`, `favicon`), then run the `then` command with `<URL>` replaced by the URL it returned, and print that URL once in the same turn's text. An answer with `dashboard.write_db`: one `Artifact` call, `action: "write_db"`, `db_op: "set"`, with `url`, `collection`, `doc_id` and `file_path` from that block — the CLI wrote the whole document to that file, so pass the path and never retype the document as `data`.

The store pins writes to document versions. The first write after `publish` creates `run/state` and carries no `if_version`; every later write carries `if_version` = the version the previous write's result reported ("now at version N"). If a write fails with `version_mismatch`, read `run/state` once (`action: "read_db"`, `db_op: "get"`, same `url`/`collection`/`doc_id`), take the `version` it returns, resend the same write once with that `if_version`, and carry on whatever the result.

That is the one extra action per step this flag costs (G8 = 2 with the dashboard on; the rare `version_mismatch` recovery is two more), and it is always the **first** action of the step — Step 0 above, never after the `kind` work. It is decoration, never the step: if the `Artifact` tool is missing or errors on `publish`, run `<mf> task dashboard --workdir W --unavailable "<error>"` and carry on — no answer will ask again. Any other `write_db` error: ignore it and carry on; never repeat it, never finalize, never let it change what you do with `kind`.

## 3. Failures

- `{"accepted": false, "errors": […], "retry": {…}}` — print the errors, change nothing by hand, and call `next` again: it re-issues `retry.agents` at `retry.attempt`. `{"accepted": true, "already_reported": true}` is a normal no-op.
- An answer that carries `reprompt` (D-72, always from `gate parse`) is a **business outcome**, not a failure: the user's gate reply was not understood, nothing was recorded, the gate stays open. Print `reprompt` verbatim and END the turn — never repeat the command and never finalize; the next reply comes back through `/memoforge:continue`.
- A CLI call that fails — non-zero exit **or** a JSON answer that carries `errors` and no `reprompt` — is not a business outcome and never counts as the step being done: run the identical command once more. If it fails again, run `<mf> finalize --workdir W --reason cli_error`, print its result and END. Never call `next` in the hope the phase moved on.
- `cancel` from the user at any gate → `<mf> task cancel --workdir W`, then keep looping: `next` issues no new work and routes to the finalize/terminal steps.
