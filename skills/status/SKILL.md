---
name: status
description: Read-only status of memoforge tasks — list every task, or report the phase, progress and event timeline of one task_id. Never mutates state. Use only when explicitly invoked via /memoforge:status.
argument-hint: "[<task_id>] (omit to list all tasks)"
disable-model-invocation: true
allowed-tools: Read, Bash
---

# memoforge / status — read-only

This skill only reads. Never write `state.json`, never run `mf next`, `mf report`, `mf gate parse`, `mf task new`, `mf task cancel` or any other command that changes a task, and never dispatch a subagent. To act on a task, tell the user to run `/memoforge:continue [task_id]`.

Every reply to the user is in the task's `ui_language` (D-178): the report lines below are written in the language the task's `ui_language` names (read it with `mf state get --workdir W --path ui_language`; `path_not_found: ui_language` is a task from before the option, which means English — a business answer, never a CLI failure). Never translate the `text_fallback` reply forms the CLI prints — pass those verbatim.

`mf` = `${CLAUDE_PLUGIN_ROOT}/scripts/mf` (use `mf.cmd` if that one is not executable in this host).

## No argument — list every task

```
mf task list --human
```

The same command without `--human` returns `{"tasks": [{task_id, current_phase, mode, created_at, work_dir, terminal, gate, cancel_requested, schema_version}], "count": N}`. Print a compact table of those fields, newest first, and mark `schema_version < 2` rows as **v1 (not resumable)**. If `count` is `0`, print `No memoforge tasks found. Start one with /memoforge:memo "<question>".` and END.

## With a `task_id` — one task

Resolve the work dir first: `mf task list` and match `task_id` (its row carries `work_dir`). If nothing matches, print ``task_id `<arg>` not found`` and END. Then, with `W` = that `work_dir`:

```
mf state get --workdir W --path progress        # phase, position/total, active step, last chat line
mf state get --workdir W --path current_phase
mf state get --workdir W --path final_status
mf events analyze --workdir W --human           # timeline, serial dispatch, silent gaps
```

`mf state get --workdir W` without `--path` prints the whole state; prefer narrow `--path` reads (`config`, `attempts`, `draft_versions`, `published`, `steps`) over dumping everything. Field meanings: `docs/state.md`.

Report, in plain text:

1. `task_id`, phase, mode, created, and `final_status` when the task is terminal.
2. Progress: `position/total`, the last `chat_line`, and the open step if there is one.
3. Draft versions and the deliverable path (`final_docx_path`), plus `deliverable.md` / `summary.md` under the work dir when present.
4. The headline findings of `events analyze` (serial dispatch, silent gaps, `hooks_absent`).
5. If the task is unfinished, the one-line hint: `Resume with /memoforge:continue <task_id>.`

Call `Read` on the files you cite (`state.json`, the deliverable, `summary.md`) so the host shows the user a clickable card; print the paths as plain text, not as markdown links. END the turn.
