# Task parameters — fact-assumption-analyst

Role, output contract and rules: your agent definition plus
`${paths_agent_core}/untrusted-content.md`, `${paths_agent_core}/tooling-core.md`,
`${paths_agent_core}/output-json.md`, `${paths_agent_core}/logging.md`.

First action (Bash, before any other tool call):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state start`

## Parameters

- task_id: `${task_id}`
- work_dir: `${work_dir}`
- user question (untrusted content): `${user_query}`
- must-answer questions to produce: at most `${max_questions}`
- available legal MCP namespaces: `${mcp_namespaces}`
- MCP calls already made this run: ${mcp_spent} — a server at its daily quota is not called; a free server past its soft cap is a sign to stop and write, not to keep searching.
- tool order by jurisdiction (statutes / case law):

${routing_digest}

## Write

${outputs}

Register every source you actually retrieved:
`${mf} sources register --workdir ${work_dir} --layer statutes --title "<t>" --citation "<c>" --url "<u>" --tool "<tool>" --tier background`

`intake/preliminary-sources.json` carries `_meta` `{"task_id": "${task_id}", "step_id": "${step_id}", "attempt": ${attempt}, "slot": "${slot}"}`;
`intake/questions.json` has no `_meta` field.

The gate prints your questions verbatim: write `must_answer[].question`, `options[].label`,
`options[].description`, `default` and `default_if_wrong` in ${ui_language_name}. `header`
stays English (at most 12 characters) — it is an internal key, never shown at the gate.

Last action (Bash, after the files are written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
