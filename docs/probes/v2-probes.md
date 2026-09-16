# memoforge v2 — platform probes (ТЗ §11)

The probes below answer questions the design depends on. Each one is run **before** the decision it
affects is locked in. `mf probe P<n>` prints the procedure; it never executes it — the probe is a
human observation in a real host. Record the outcome here, honestly, including a failure.

Method note: leave **25 s** between observations (the pause methodology of
`docs/attic/v0.5.0-probe-procedure.md`) so a host that batches UI updates has time to flush.

Fill in one row per environment: `CLI` (Claude Code on Windows 11) and `Cowork`.

## Results

| ID | Question | Affects | Env | Date | Result | Evidence | Decision |
|---|---|---|---|---|---|---|---|
| P1 | Plugin workflow `/memoforge:<name>` in Cowork and CLI; is progress visible? | §10 | CLI | | | | |
| P1 | | §10 | Cowork | | | | |
| P2 | Exec-form hook with `${CLAUDE_PLUGIN_ROOT}` and `python`/`python3` on Windows | §8.1 | CLI | | | | |
| P2 | | §8.1 | Cowork | | | | |
| P3 | `Artifact` from a plugin skill; `write_db` from a background subagent; live update | §7.5 | CLI | | | | |
| P3 | | §7.5 | Cowork | 2026-09-10 | PARTIAL | User run (Windows host, Cowork Linux VM): `Artifact` publish from the memo skill worked, page updated live from `write_db`; `write_db` from a background subagent not exercised (design uses the orchestrator only). Plan card lagged once because the orchestrator skipped a write — fixed by D-94 ordering. | Dashboard on by default (D-92); write is step 0 of every `next` (D-94) |
| P4 | `AskUserQuestion` after **any** `Agent` dispatch — silent fail? | §2.4 | CLI | | | | |
| P4 | | §2.4 | Cowork | 2026-09-10 | PASS | User run: plan-gate `AskUserQuestion` (Plan/Mode/Sources) rendered and accepted answers after the intake `Agent` dispatch; `report --answers` closed the gate. | AUQ path of gate 4 accepted |
| P5 | `SubagentStart`/`SubagentStop` for `memoforge:*`, and their payload | §7.2 C | CLI | | | | |
| P5 | | §7.2 C | Cowork | | | | |
| P6 | Nested spawn from a plugin agent | reserve | CLI | | | | |
| P6 | | reserve | Cowork | | | | |
| P7 | Stop-hook `decision: block` — re-entry | §8.3 | CLI | | | | |
| P7 | | §8.3 | Cowork | | | | |
| P8 | Chat flush mid-turn; is the `Agent` tile with `description` visible? | §7.2 A/B, §7.4 | CLI | | | | |
| P8 | | §7.2 A/B, §7.4 | Cowork | 2026-09-09 | PARTIAL | User run: `chat_line` between tool calls collapses under "Ran N commands · 1 note" until the turn ends (#26805); `Agent` tiles with `P5/12 · legal-researcher · statutes` are visible live. | Chat lines stay (cheap); dashboard is the live channel in Cowork |
| P9 | Matcher semantics and the effective tool pool of a plugin agent | §4.1, §7.2 C, §8.1 | CLI | | | | |
| P9 | | §4.1, §7.2 C, §8.1 | Cowork | | | | |
| P11 | Does the terminal-step copy to the connected folder work through the device tools? | §2.5 (D-109) | CLI | | | | |
| P11 | | (same) | Cowork | 2026-09-11 | PARTIAL | Windows desktop app (plugin runs in a container): the terminal-step copy reached the connected folder through the host bridge, but Cowork placed the files flat under `<connected folder>/Claude outputs/` (docx, md, summary.md) — no `memoforge/<slug>/` structure and no `sources/`. | Keep the step; instruct copying the whole published folder as a folder; record the device tool names when known |

Legend for **Result**: `PASS`, `FAIL`, `PARTIAL`, `BLOCKED` (could not be run — say why).

## Procedures

`mf probe P<n>` prints the same question, method and affected section; the summaries below carry the
extra detail a tester needs to actually run them.

- **P1** — do **not** add `workflows/` to this repository: §0.4 and §10 keep it undeclared until P1
  passes. Copy the plugin to a scratch directory outside the shipped tree
  (`%TEMP%\memoforge-probe\` on Windows, `$TMPDIR/memoforge-probe/` elsewhere), create
  `workflows/ping.js` there with two phases, install that copy as a local plugin, and run
  `/memoforge:ping` in both environments. Watch `/workflows` and Background tasks for progress.
  Delete the scratch copy once the row below is filled in.
- **P2** — `hooks/probe_echo.py` appends one JSON line to `<plugin_data_dir>/probe-echo.log`. It is
  deliberately absent from `hooks/hooks.json` (`build_hooks.py` never emits it — it is not part of
  the §8.1 table), so wire it in by hand for the probe and remove it afterwards. Add this block to
  `hooks.PreToolUse` in the installed plugin's `hooks/hooks.json`, restart the host, and run any
  `Read`:

  ```json
  {
    "matcher": "^Read$",
    "hooks": [
      {"type": "command", "command": "python", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/probe_echo.py", "--variant", "python"]},
      {"type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/probe_echo.py", "--variant", "python3"]},
      {"type": "command", "command": "py", "args": ["-3", "${CLAUDE_PLUGIN_ROOT}/hooks/probe_echo.py", "--variant", "py-3"]}
    ]
  }
  ```

  Then read the log: `argv` says which of the three exec forms fired, and
  `env.CLAUDE_PLUGIN_ROOT` still holding the literal `${CLAUDE_PLUGIN_ROOT}` (or an argv path that
  does not exist on disk) is the failure signature. `python hooks/build_hooks.py --check` reports
  the hand edit as drift while the block is in place — expected; delete the block when the row is
  filled in.
- **P3** — set `dashboard: on` in the plugin settings, run a Brief task, and confirm the page
  updates live in Cowork and CLI. The first `mf next` answer carries `dashboard.publish`: publish
  `lib/dashboard.html` with `capabilities: {db: {}}`, hand the URL to
  `mf task dashboard --workdir W --url <URL>`, and watch the page while every later `next` answer
  is applied with one `Artifact write_db` call (`run/state`). Failure mode to record: the page
  never leaves "waiting for data", or it stops re-rendering while `write_db` keeps succeeding.
  A host without the tool must degrade cleanly — `mf task dashboard --workdir W --unavailable
  "<error>"` raises the banner and the run finishes normally.
- **P4** — one dispatch followed by `AskUserQuestion`; then three parallel dispatches followed by
  `AskUserQuestion`. A silent no-answer is the failure mode being probed.
- **P5** — `events.jsonl` cannot answer the payload half of this question: `build_subagent()` in
  `hooks/progress_logger.py` keeps only `agent_id`, `agent_type` and (on stop) `result`, so the
  absence of `initial_prompt` / `last_assistant_message` there is a property of the event format,
  not of the host. Read the hook's own stdin instead. Wire `hooks/probe_echo.py` in by hand exactly
  as P2 does (it is never emitted into `hooks/hooks.json`): add this block to `hooks.SubagentStop`
  in the installed plugin's `hooks/hooks.json`, restart the host, and dispatch one `memoforge:*`
  agent (`memoforge:probe-echo` is enough; a real pipeline slot works too):

  ```json
  {
    "matcher": "^memoforge:",
    "hooks": [
      {"type": "command", "command": "python", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/probe_echo.py", "--variant", "subagent-stop"]},
      {"type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/probe_echo.py", "--variant", "subagent-stop"]},
      {"type": "command", "command": "py", "args": ["-3", "${CLAUDE_PLUGIN_ROOT}/hooks/probe_echo.py", "--variant", "subagent-stop"]},
      {"type": "command", "command": "python", "args": ["-c", "import sys,pathlib;pathlib.Path(r'<abs>/probe-p5-stop.json').write_bytes(sys.stdin.buffer.read())"]}
    ]
  }
  ```

  Then read two files. `<plugin_data_dir>/probe-echo.log`: its last `"--variant", "subagent-stop"`
  line answers the *firing* half — `hook_event_name` is `SubagentStop`, `agent_type` starts with
  `memoforge:` (the `^memoforge:` matcher matched), `stdin_bytes` is non-zero. `probe_echo.py`
  records only those fields and never dumps the top-level keys of the payload, so the *payload* half
  is read from `<abs>/probe-p5-stop.json` — the verbatim stdin of the same call. Record which of
  `initial_prompt` and `last_assistant_message` the JSON has as top-level keys and whether they
  carry real text (`stdin_bytes` alone proves nothing). The same block under `hooks.SubagentStart`,
  with `--variant subagent-start` and a second dump path, answers the start half. Delete both blocks
  when the row is filled in; `python hooks/build_hooks.py --check` reports them as drift meanwhile —
  expected, exactly as in P2.
- **P6** — no shipped agent can make this call: every agent of the §4.1 roster either omits `Agent`
  from `tools:` or lists it in `disallowedTools`, and `agents/probe-echo.md` disallows it too, so
  `Agent(Explore)` from an unmodified memoforge agent measures the plugin's own policy, not the
  host's. The frontmatter is only half of that policy: the body of `agents/probe-echo.md` forbids
  every call except the one prescribed `Bash` (the opening "do not ... call any tool other than the
  one call named below" and step 3 of "What to do"), so an agent whose `disallowedTools` alone was
  relaxed would still refuse to spawn. The temporary diagnostic variant therefore edits **the whole
  file** in the installed plugin — copy the shipped `agents/probe-echo.md` aside first, it has to
  come back byte-for-byte:

  1. Frontmatter: drop `Agent` from the `disallowedTools:` line, leaving
     `disallowedTools: Task, AskUserQuestion, mcp__cowork__*`.
  2. Body: replace the Bash-only instruction — the "call any tool other than the one call named
     below" sentence and step 3 of "What to do" — with exactly one
     `Agent(subagent_type="Explore", prompt="list the files in the plugin root")` call and a report
     of what it returned, in the same `TOOLS:`/echo output format (one `AGENT_CALL:` line in place
     of `BASH_CALL:`).
  3. Restart the host and dispatch `memoforge:probe-echo`. No extra line in the dispatch prompt is
     needed — the edited body carries the instruction, and a prompt line alone would not work,
     because the body forbids the call.

  Record both halves: whether `Agent` shows up in the agent's `TOOLS:` line (the frontmatter edit
  was honoured at all) and whether the nested spawn actually started and returned. Then restore the
  shipped file in full from the copy — frontmatter *and* body, byte-for-byte: §4.1 keeps `Agent`
  out of the production roster, and this probe only asks whether the platform *would* allow the
  nesting. This is the same file P9 variant (b) edits, so do not run the two probes at the same
  time.
- **P7** — interrupt a run in a non-gate phase with `stop_guard.py` enabled via the
  `stop_guard` plugin setting; try to re-enter.
- **P8** — print three text lines interleaved with two `Agent` calls; note when the chat flushes and
  whether the tile shows the `P<n>/<N> · <agent> · <label>` description.
- **P9** — an echo agent prints its own tool list under (a) no `tools:` plus
  `disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*`, and (b)
  `tools: Read, Write, Bash, mcp__*`. Run both in CLI and Cowork. `agents/probe-echo.md` ships as
  variant (a), carrying the same `disallowedTools` list as the three MCP agents; for variant (b)
  edit that one frontmatter in place — drop the `disallowedTools:` line, add
  `tools: Read, Write, Bash, mcp__*` — run the agent again, then restore the shipped frontmatter.
  No second agent file ships for this.
- **P11** — finish a Brief run in Cowork with a folder connected to the session. `mf finalize`
  copies the deliverable, `summary.md` and `sources/` into
  `/mnt/user-data/outputs/memoforge/<slug>/` (D-109) and the terminal `text` prints that path as
  `Published:`. Then do what the terminal step of `router.md` asks: copy the same folder into
  `<connected folder>/memoforge/<slug>/` with the session's own device file tools — the connected
  folder is not mounted in the plugin's container and reaches those tools as `$HOME/mnt/<folder>`.
  Record three things: whether such tools exist in this session at all, whether the copy arrived,
  and whether the user sees the files on their own machine without opening the outputs sidebar.
  The CLI row is the control: there are no device tools there, so the step must do nothing beyond
  printing the paths, and a CLI run that tries to copy anything is a failure.

## Dry run (not a platform probe)

`mf probe dry-run --mode full|brief` runs the whole pipeline with fixture agents and reports the
modelled G2 counts and the invariants. It is a lower bound for G1 and is labelled as such; the real
numbers come from `mf probe metrics` on an actual run.

| Mode | Date | `next` | `report` | `Agent` | scripts | gates | Final phase | Notes |
|---|---|---|---|---|---|---|---|---|
| full | 2026-09-12 | 23 | 15 | 12 | 10 | 3 | done | `inline-llm` 2; `g2_exceeded: {}`; observed estimate 74 ≤ 150; `final_status: approved_on_v1` (D-117 merged `draft finish` and dropped the separate `docx validate`: scripts 13 → 10) |
| brief | 2026-09-12 | 22 | 12 | 9 | 10 | 2 | done | `inline-llm` 2; `g2_exceeded: {}`; observed estimate 68 ≤ 150; `final_status: approved_on_v1` (D-117 merged `draft finish` and dropped the separate `docx validate`: scripts 13 → 10) |
