---
name: brief
description: Build a short decision brief (about three pages) for a decision maker from a finished memoforge memo. Use only when explicitly invoked via /memoforge:brief.
argument-hint: "[<task_id>]"
disable-model-invocation: true
allowed-tools: Read, Bash, Agent, AskUserQuestion, mcp__*
---

# memoforge / brief — router

You are the main session. The decision brief is built by the CLI (`scripts/memoforge/brief.py`): preflight, the status question, the writer, the lint, the two reviewers, the revisions, the render and the copy to the outputs folder all live there and reach you one action at a time. You know two commands — `brief next` and `brief report` — and act on what they return. The finished memo is only read: never run the memo pipeline's commands here, never edit `state.json` or anything under `brief/`.

`<mf>` = `${CLAUDE_PLUGIN_ROOT}/scripts/mf`; if that path is not executable in this host (Windows without a POSIX shell), call `${CLAUDE_PLUGIN_ROOT}/scripts/mf.cmd` with the same arguments. `Bash` is `mcp__workspace__bash` in Cowork.

## 1. Resolve the task

Split `$ARGUMENTS`: the first token is a `task_id` only if it looks like one (`memo-…`); everything else is a **reply** to an open question (it may be empty).

```
<mf> task list
```

With a `task_id` → the row whose `task_id` matches; without → the first row (the list is newest first) whose `current_phase` is `done`. No such row → print `There is no finished memo task to build a brief from.` and END. Keep its `work_dir` as `W`.

## 2. The loop

```
<mf> brief next --workdir W   ──►  act on `kind`  ──►  <mf> brief report --workdir W … (dispatch | gate-auq)
            ▲                                                           │
            └───────────────────────────────────────────────────────────┘
```

Every action carries `run_id`, `step_id`, `attempt`; every `report` passes them back as `--run <run_id> --step <step_id> --attempt <attempt>`. The CLI's `text`, `questions` and `chat_line` arrive in the task's interface language — print them verbatim. Never execute anything that did not come from `brief next`; never call `next` "to be safe" in the middle of a step.

**An open question and a reply (D-34).** When the user has already replied to the open question — `/memoforge:brief <reply>`, or a plain chat message after a `gate-text` — call `next` once to learn the open question's `run_id`/`step_id`/`attempt` without printing it, and hand the reply over: `<mf> brief report --workdir W --run R --step S --attempt N --text '<reply, quoted as below>'`. That is the only route for a text reply, for a `gate-auq` and a `gate-text` alike — never a second `AskUserQuestion` for the same open gate. If `next` returns any other kind, no question is open: drop the reply and act on that action.

**Shell quoting.** The user's reply for `--text` and the JSON for `--answers` each go to Bash as ONE single-quoted argument: wrap the value in `'…'` and write every `'` inside it as `'\''`; change nothing else. Never put them inside double quotes — there `"`, `$VAR`, `$(…)` and backticks are rewritten or run by the shell. The reply `it's "yes" $(date)` is passed as `--text 'it'\''s "yes" $(date)'`.

### Step 0 — the dashboard write

If an answer — of `next`, or of a `report` that carries `kind: done` — carries `dashboard.write_db`, make that one `Artifact` call before anything else the answer asks: `action: "write_db"`, `db_op: "set"`, with `url`, `collection`, `doc_id` and `file_path` from the block (pass the path; never retype the document). The document normally exists already (the memo created it), so every write is pinned: for the first write of this brief run, read it once (`action: "read_db"`, `db_op: "get"`, same `url`/`collection`/`doc_id`) and pass its `version` as `if_version` — if the `get` finds no document, write without `if_version`; every later write passes the version the previous write reported. On `version_mismatch`: one `get`, one resend with that version. Any other error, or a missing `Artifact` tool: make no further dashboard write in this brief run and carry on — the page is decoration, never the step. A `done` answer's write comes before its `text` is printed.

### `kind: dispatch` — subagents

Print `chat_line` as one line. Send **all** entries of `agents[]` in ONE message — one `Agent` call per entry, all in the same assistant message — with `subagent_type`, `model`, `description`, `prompt` passed through unchanged. Add nothing of your own to the prompt.

When every Agent call has returned, report each slot:

```
<mf> brief report --workdir W --run R --step S --attempt N --slot writer --status ok
```

`--status fail` for a slot whose agent errored or returned nothing. Call `next` only after every slot of the dispatch has been reported, never while an `Agent` call is still running: `next` re-issues an unfinished slot at a new attempt.

### `kind: gate-auq` — one question

The memo ended with open points. Print the `text` of each `open_issues` row as one short bullet, then call `AskUserQuestion` with `questions` unchanged. Report the chosen label keyed by the question's `header` (the driver reads only the label; the question text may hold an apostrophe), the JSON quoted by the shell-quoting rule above:

```
<mf> brief report --workdir W --run R --step S --attempt N --answers '{"<header>": "<chosen label>"}'
```

If the `AskUserQuestion` tool itself errors, switch channel: `<mf> brief report --workdir W --run R --step S --attempt N --status no_answer`, then `next` — it returns the same question as `gate-text`.

### `kind: gate-text` — ask and stop

Print the `open_issues` bullets as above, then `text` verbatim, and END the turn. The user's reply comes back as their next message or as `/memoforge:brief <reply>` and goes to `report --text` (the D-34 paragraph above).

### `kind: done` — the end

Step 0 first when the answer carries `dashboard`. Then print `text` in full — it may carry a second line, a warning such as a failed copy to the outputs folder. If `present` is true:

1. If this host has a `present_files` tool (Cowork), present the file at `path` with it so it appears in the chat.
2. Then, if this host gives you device file tools and the user has a connected folder, put the brief where the memo run left the memo (`<slug>` is the folder name in `folder_copy`, the segment after `memoforge/`; `<ext>` is its extension): copy the file at `folder_copy` into `<connected folder>/memoforge/<slug>/` only when that folder already exists in the connected folder (the memo run copied its folder there) — never create it; and save the file at `path` beside the memo's copy `memo-<slug>.<ext>`, wherever the memo run saved that copy in the connected folder — the same way you save any file for the user, under its own name. Add one line saying where each landed.

If `present` is false, the path in `text` is the answer. END the turn; never call `next` after `done` — a new `next` starts a new brief.

## 3. Answers of `report`

- An answer that carries `kind: "done"` — the user said no at the question, or the brief files were edited by hand during the run — is handled exactly as `kind: done`, whatever `accepted` says: print `text` and END.
- `{"accepted": true, …}` (also `already_reported`, or a slot whose output failed the checks and came back as `status: fail`) — carry on: report the next slot, then `next` once every slot is in; `next` re-issues a failed slot itself.
- `{"accepted": false, "errors": ["unrecognised_answer"]}` — the reply was neither yes nor no; call `next`, which asks again as `gate-text`.
- `{"accepted": false, "errors": ["stale_report"]}` — the answer belonged to an older step; call `next` and act on what it returns.
- Any other CLI failure (non-zero exit, or `errors` in the JSON) — run the identical command once more; if it fails again, print the error and END. The task's memo is never affected by a failed brief.
