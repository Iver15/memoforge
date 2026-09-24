# Agent models, effort and tool policy

Single source of truth for the frontmatter of every agent in `agents/`. `scripts/tests/test_agent_frontmatter.py` reads this table and compares it with the files; a change belongs here first.

## How to read the table

- **Agent** — the file `agents/<name>.md` and the `name:` in its frontmatter.
- **model** and **effort** — the literal frontmatter values.
- **tools / disallowedTools** — the two frontmatter fields, separated by `;`. `tools: —` means the field is deliberately absent: an explicit allowlist silently strips inherited MCP tools, so the three agents that need legal MCP servers inherit the session's tool pool and are constrained by `disallowedTools` instead. `disallowedTools: —` means the field is absent because `tools` already excludes everything that matters.
- Every agent has `Bash`, directly or by inheritance: it runs `mf agent log` and, for the writer, `mf quote extract`.
- No agent may spawn agents or ask the user a question. For allowlisted agents that follows from `tools`; for the inheriting three it is spelled out in `disallowedTools`.

## Table

| Agent | model | effort | tools / disallowedTools | Why |
|---|---|---|---|---|
| `fact-assumption-analyst` | `opus` | `high` | `tools: —`; `disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*` | One dispatch, small input, and the judgement that matters — which facts would change the answer — is the whole task. Probes the legal MCP namespaces, so it inherits tools. |
| `legal-researcher` | `sonnet` | `high` | `tools: —`; `disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*` | Retrieval and structuring against an explicit routing table, no interpretation. Runs up to three times in parallel, so cost matters; needs the legal MCP servers. The `case_law` slot is dispatched on `opus` (`dispatch.RESEARCH_LAYER_MODELS`): since plan 68 it reads whole court acts and states their holdings, which is interpretation. |
| `research-sufficiency-reviewer` | `opus` | `high` | `tools: Read, Write, Bash`; `disallowedTools: —` | One or two dispatches that decide the most expensive branch in the run — re-research or a user gate. Works from files already on disk. |
| `currency-checker` | `sonnet` | `medium` | `tools: —`; `disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*` | Status verification over MCP and official portals; the deterministic part is already done by `mf sources liveness` and `mf sources verify`. |
| `memo-writer` | `opus` | `high` | `tools: Read, Write, Edit, Bash`; `disallowedTools: —` | The only agent that decides what the product reads like. `config.writer_model` may override at dispatch (`opus`, `fable`, `sonnet`); an unknown value falls back to `opus` with an event. `effort` is the frontmatter value above and is the same for every version (D-75). |
| `logic-reviewer` | `opus` | `high` | `tools: Read, Write, Bash`; `disallowedTools: —` | Substance grader: argument soundness and cross-section consistency are the findings that force a second iteration. |
| `form-reviewer` | `sonnet` | `medium` | `tools: Read, Write, Bash`; `disallowedTools: —` | Clarity and style merged into one isolated grader with a `lens` per issue. With the caps, em dashes and heading levels moved into `mf draft lint`, what is left is readability judgement. |
| `citation-auditor` | `opus` | `high` | `tools: Read, Write, Bash`; `disallowedTools: —` | Existence and exact-text checks are deterministic; this agent judges meaning — source drift, pack mismatch, unsupported claims — which is where a weaker model misses. |
| `counterargument-reviewer` | `opus` | `high` | `tools: Read, Write, Bash`; `disallowedTools: —` | Adversarial reading is the one lens where the strongest available model earns its cost. |
| `revision-mediator` | `sonnet` | `medium` | `tools: Read, Write, Bash`; `disallowedTools: —` | Consolidates issues under an explicit priority; the aggregation and the exit decision belong to `mf review aggregate` and `mf revision next`, not to the model. |
| `client-readiness-reviewer` | `sonnet` | `medium` | `tools: Read, Write, Bash`; `disallowedTools: —` | Short delivery pass over a draft that is already lint-clean and audited. |
| `style-extractor` | `opus` | `high` | `tools: Read, Write, Glob, Bash`; `disallowedTools: —` | One-off, and inferring a house style from samples is judgement. Runs outside the memo pipeline. |
| `brief-writer` | `opus` | `high` | `tools: Read, Write, Edit, Bash`; `disallowedTools: —` | Writes the decision brief of `/memoforge:brief` from the delivered memo and edits it on the lint fix, the revision and the shortening pass. Compressing a memo for a reader who is not a lawyer without adding or dropping anything is the writer's judgement; the brief driver dispatches it on the task's `config.writer_model`, as the memo writer is (D-223). |
| `brief-fidelity-reviewer` | `opus` | `high` | `tools: Read, Write, Bash`; `disallowedTools: —` | Grades whether the brief says what the memo says: a dropped condition, a shifted degree of certainty, an open point presented as settled. The brief's reader cannot check it against the memo, so this is the one check between them (D-223). |

Fourteen agents. The v1 researchers (`statutory`, `case-law`, `doctrinal`) are one `legal-researcher` with a `layer` parameter; `clarity-reviewer` and `style-reviewer` are one `form-reviewer`; `source-pack-builder` is now `mf sources pack`. The last two rows serve `/memoforge:brief` only; its driver dispatches them after a run is `done` (D-223).
