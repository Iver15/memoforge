# События журнала (v2)

[Указатель](README.md) · [Оригинал](../events.md). Перевод документации v2.0.0.

Источник — `scripts/memoforge/events.py` (ТЗ §7.2 C/C′). Журнал всегда находится в `<work_dir>/events.jsonl`. Каждое событие занимает одну строку не более 4096 байт; запись выполняется под блокировкой `events.lock`. Хуки могут записывать событие несколько раз; при чтении повторы удаляются по `event_key`.

| Событие |
|---|
| `agent_log` |
| `agent_returned` |
| `cancel_requested` |
| `cli_call` |
| `context_compacted` |
| `dashboard_published` |
| `dashboard_unavailable` |
| `fallback_invoked` |
| `gate_answered` |
| `gate_channel_switched` |
| `language_fallback` |
| `mcp_call` |
| `mcp_ratelimit_fallback` |
| `mode_selected` |
| `plan_approved` |
| `result_published` |
| `sources_frozen` |
| `state_written` |
| `step_autoclosed` |
| `step_issued` |
| `stop_guard_blocked` |
| `stop_guard_gave_up` |
| `subagent_requested` |
| `subagent_started` |
| `subagent_stopped` |
| `task_created` |
| `work_dir_resolved` |
| `writer_model_fallback` |

Уровни: `info`, `warn`, `error`.

Структура записи: `{ts, event, actor, severity, phase, step_id, event_key, data}`.
