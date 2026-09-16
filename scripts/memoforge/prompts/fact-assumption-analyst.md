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
- MCP call share for this dispatch: `${mcp_budget_share}`
- tool order by jurisdiction (statutes / case law):

${routing_digest}

## Write

${outputs}

Register every source you actually retrieved:
`${mf} sources register --workdir ${work_dir} --layer statutes --title "<t>" --citation "<c>" --url "<u>" --tool "<tool>" --tier background`

`intake/preliminary-sources.json` carries `_meta` `{"task_id": "${task_id}", "step_id": "${step_id}", "attempt": ${attempt}, "slot": "${slot}"}`;
`intake/questions.json` has no `_meta` field.

Last action (Bash, after the files are written):
`${mf} agent log --workdir ${work_dir} --step ${step_id} --attempt ${attempt} --slot ${slot} --state done`
