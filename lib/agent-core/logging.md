# Logging

Two Bash calls frame every dispatch. Your prompt supplies the `mf` path and the values for `--step`, `--attempt` and `--slot`; substitute them literally.

- First action, before any other tool call:

  `${mf} agent log --workdir "${work_dir}" --step ${step_id} --attempt ${attempt} --slot ${slot} --state start --detail "<what you are starting>"`

- Last action, after your output file is written:

  `${mf} agent log --workdir "${work_dir}" --step ${step_id} --attempt ${attempt} --slot ${slot} --state done --detail "<what you produced>"`

  This call writes the completion marker the pipeline waits for, so nothing comes after it.
- Between the two, `--state step` is optional telemetry: at most one call per meaningful step (a layer searched, a section drafted), same flags, `--state step`. Best-effort — if it fails, keep working.
- `--detail` is one short line of plain text. Leave it out rather than pad it.
- If the `start` call fails, do the work anyway and call `done` at the end as usual.
