# Результат при сбоях: таблица резервных сценариев

[Указатель](README.md) · [Оригинал](../always-deliver.md). Перевод документации v2.0.0.

Источник — `scripts/memoforge/fallbacks.py` (M9, ТЗ §2.1, §5.5). На каждой ветви остаётся результат: `mf finalize` создаёт `deliverable.{docx|md}` и `summary.md` до записи конечной фазы. `mf finalize --salvage` делает это даже без `jsonschema` и при повреждённом `state.json`.

| Условие | Фаза | Действие | Предупреждение |
|---|---|---|---|
| `work_dir_not_writable` | `intake_preliminary_research` | Выбрать следующий каталог в цепочке work_dir (§2.5), записать причину в событии `work_dir_resolved`. | — |
| `mcp_all_unavailable` | `research` | Исследователи используют только WebFetch для проверенных официальных порталов; каждый файл исследования содержит `mcp_status: unavailable`. | `mcp_unavailable` |
| `mcp_partial` | `research` | Продолжить с доступным MCP-сервером и отметить пробел в каждом файле исследования. | `mcp_partial` |
| `portal_unreachable` | `research` | Продолжить с доступными источниками; для каждого недоступного портала исследователь записывает явный пункт `gap:`. | `sources_unreachable` |
| `mcp_rate_limited` | `research` | Прекратить вызовы MCP с исчерпанным лимитом, использовать WebSearch и WebFetch по каноническим URL; пометить каждый такой источник `[rate-limited fallback]`. | `mcp_ratelimit_fallback` |
| `mcp_soft_cap_exceeded` | `research` | Бесплатный MCP-сервер превысил мягкий предел вызовов на запуск: отметить это в журнале и писать по уже собранным материалам (§4.3, D-166). Это ограничение только для учёта. | `mcp_soft_cap_exceeded` |
| `research_layers_partial` | `research` | После исчерпания лимита повторных запусков продолжить с корректными слоями, а отсутствующие перенести в `drafting_warnings[]` (§2.1, строка 5). | `research_partial` |
| `research_insufficient_budget_consumed` | `research_sufficiency` | Перейти к написанию; меморандум должен содержать раздел открытых вопросов и непроверенных фактов. | `research_insufficient` |
| `sufficiency_reviewer_failed` | `research_sufficiency` | Один раз повторно запустить рецензента с контекстом ошибки; при повторном сбое считать результат `insufficient`. | `sufficiency_unavailable` |
| `sufficiency_subset_u_unresolved` | `research_sufficiency_followup_pending` | Оставшиеся пробелы Subset U перенести в `drafting_warnings[]`; автор включает в текст оговорки о принятых допущениях. | `assumptions_after_followup` |
| `research_insufficient_user_continue` | `research_insufficient_pending` | Пользователь выбрал `continue`: перейти к `currency_check` с заполненным `drafting_warnings[]` (§2.1, строка 8). | `continue_with_caveats` |
| `currency_blocking_issues` | `currency_check` | Исключить непроверяемый источник из кандидатов в набор источников, указать рекомендации по замене. | `currency_blocking` |
| `currency_checker_failed` | `currency_check` | Пометить все непроверенные источники `unchecked`, продолжить к `source_pack` (§2.1, строка 9). | `currency_unavailable` |
| `source_pack_incomplete` | `source_pack` | Зафиксировать снимок с имеющимися полями, отметить отсутствующие. | `source_pack_incomplete` |
| `gate_defaults_applied` | любая | После исчерпания `attempts.gate_parse_errors` применить документированные значения по умолчанию; никогда не устанавливать `assumptions_accepted=true` (§2.4 c). | `gate_defaults` |
| `plan_forced_approve` | `plan_approval_pending` | После исчерпания `attempts.plan_edit` автоматически одобрить последнюю представленную версию плана (§2.2). | `plan_forced_approve` |
| `writer_failed` | `drafting` | Один раз повторно запустить автора (`single_dispatch_retry`); после второго сбоя завершить задачу как `failed` с сохранёнными материалами (§2.1, строка 12). | `drafting_incomplete` |
| `revision_writer_failed` | `revision_loop` | Автор вернул подготовленный черновик без изменений в обеих попытках: копия не является новой версией. Выйти на последней проверенной версии со статусом ручной проверки и перечнем открытых блокирующих замечаний (D-153). | `revision_incomplete` |
| `lint_not_converged` | `drafting` | После `config.lint_fix_rounds` перейти к `revision_loop`, передав рецензентам `lint.json` и `citations.json` (§2.1, строка 12). | `lint_not_converged` |
| `reviewer_json_invalid` | `revision_loop` | Повторить один раз (`reviewer_json_retry`), затем заменить отчёт аварийной заглушкой и продолжить. | `reviewer_output_malformed` |
| `mediator_failed` | `revision_loop` | Выйти из цикла на последней проверенной версии черновика. | `mediator_unavailable` |
| `max_iterations_with_blockers` | `revision_loop` | Принудительный выход: `final_status = forced_exit_on_v<N>_with_remaining_issues`; блокирующие замечания перечислены в разделе статуса. | `unresolved_blockers` |
| `client_readiness_manual_review` | `client_readiness` | Перейти к экспорту; список блокирующих замечаний выводится в разделе статуса меморандума. | `manual_review_required` |
| `client_polish_budget_consumed` | `client_readiness` | Перейти к экспорту с последней версией без блокирующих замечаний автоматических проверок. | `polish_concerns_remain` |
| `client_readiness_reviewer_failed` | `client_readiness` | После исчерпания повторных попыток считать результат `manual_review_required` и выполнить экспорт. | `readiness_unavailable` |
| `no_checked_draft` | `export` | Экспортировать последнюю версию с `final_status = manual_review_required_on_v<N>` и списком блокирующих замечаний (§2.1, строка 15). | `no_checked_draft` |
| `docx_render_failed` | `export` | Создать резервный Markdown `deliverable.md` на стандартной библиотеке с маркерами сносок `[n]` (§5.5). | `docx_export_failed` |
| `docx_invalid` | `export` | Переименовать файл в `memo-<slug>.invalid.docx`, выдать резервный Markdown как основной результат (§5.5). | `docx_invalid` |
| `unresolved_reference_in_fallback` | `export` | Сохранить в резервном Markdown маркер `[unresolved: <id>]` вместо удаления неразрешённой ссылки (§5.5). | `unresolved_reference` |
| `output_folder_write_failed` | `export` | Оставить результат в рабочем каталоге, вывести абсолютный путь в чате. | `output_folder_unavailable` |
| `publish_failed` | `export` | Пропустить копирование в каталог публикации, завершить запуск штатно; документ, сводка и тексты источников остаются в рабочем каталоге (D-109). | `publish_failed` |
| `dashboard_unavailable` | любая | Продолжить без страницы прогресса: `mf task dashboard --unavailable` записывает причину, ответы `next` больше не содержат блок §7.5; шаги не повторяются (D-87). | `dashboard_unavailable` |
| `salvage_state_corrupt` | любая | `mf finalize --salvage` восстанавливает сводку из файлов на диске без jsonschema (M9). | `state_corrupt` |
| `universal_fallback` | любая | Создать `fallback-summary.md`: task_id, последний успешный этап, установленные сведения и сбой; установить `final_status = fallback_summary_delivered`. | `fallback_summary_delivered` |

## Тексты предупреждений

Ниже приведены русские тексты, уже используемые плагином из `lib/i18n/ru.json`. Параметры в фигурных скобках заполняются при завершении задачи.

- **`mcp_unavailable`** — MCP-серверы недоступны. Исследование проведено только через публичный WebFetch — сверить с первичными источниками перед использованием клиентом.
- **`mcp_partial`** — Частичное покрытие MCP — доступен был только {available}.
- **`sources_unreachable`** — Некоторые первичные источники были недоступны; пробелы раскрыты в файлах исследования.
- **`mcp_ratelimit_fallback`** — Некоторые источники получены через запасной веб-поиск из-за ограничений частоты MCP. В файлах исследования помечены [rate-limited fallback]; сверить канонические URL в пакете источников.
- **`mcp_soft_cap_exceeded`** — Превышен мягкий лимит MCP ({server}; вызовов за проход: {count}). Сверить с первичными источниками перед использованием клиентом.
- **`research_partial`** — Некоторые слои исследования не завершены; меморандум опирается на слои, завершившиеся успешно.
- **`research_insufficient`** — Достаточность исследования: недостаточно. Открытые вопросы раскрыты в меморандуме — не действовать без дополнительной проверки.
- **`sufficiency_unavailable`** — Проверка достаточности исследования недоступна; по умолчанию статус «недостаточно».
- **`assumptions_after_followup`** — Некоторые существенные для анализа факты остались неоднозначными после уточнений. Меморандум исходит из консервативных допущений по умолчанию, задокументированных в разделе «Допущения».
- **`continue_with_caveats`** — Исследование было недостаточным, и вы решили продолжить: выводы предварительные — проверить перед использованием клиентом.
- **`currency_blocking`** — Проверка актуальности дала блокирующие замечания: {count}; затронутые источники помечены в пакете источников.
- **`currency_unavailable`** — Проверка актуальности недоступна; проверить каждый источник вручную перед использованием клиентом.
- **`source_pack_incomplete`** — Пакет источников неполный; проверить цитаты вручную.
- **`gate_defaults`** — Некоторые ответы не распознаны; применены задокументированные значения по умолчанию, допущения не приняты.
- **`plan_forced_approve`** — Бюджет правок плана исчерпан; последняя представленная версия плана утверждена автоматически.
- **`drafting_incomplete`** — Проект неполный — частичный проект доставлен; требуется ручная доработка.
- **`revision_incomplete`** — Доработка неполная — автор не смог дать изменённый проект; последняя проверенная версия доставлена для ручной проверки с открытыми блокерами.
- **`lint_not_converged`** — Автоматические проверки не сошлись; оставшиеся замечания приложены для проверки.
- **`reviewer_output_malformed`** — Цикл доработки принудительно завершён на итерации {iteration} — выводов рецензентов с ошибками: {count}; последний проект доставлен.
- **`mediator_unavailable`** — Медиация недоступна; выход на последнем валидированном проекте v{version}.
- **`unresolved_blockers`** — ЗАМЕЧАНИЯ РЕЦЕНЗЕНТОВ ЗАКРЫТЫ НЕ ПОЛНОСТЬЮ — остались блокирующие замечания (перечислены в разделе «Статус»): {count}.
- **`manual_review_required`** — Готовность к передаче клиенту: требуется ручная проверка. Блокирующие замечания перечислены в разделе «Статус».
- **`polish_concerns_remain`** — Готовность для клиента: после чистовой правки остались замечания; проверить перед передачей клиенту.
- **`readiness_unavailable`** — Проверка готовности для клиента недоступна; считается требующей ручной проверки.
- **`no_checked_draft`** — Ни одна версия проекта не прошла автоматические проверки (lint) и проверку цитат; последняя версия доставлена для ручной проверки.
- **`docx_export_failed`** — Экспорт docx не удался — авторитетен markdown-результат. Перед использованием клиентом сконвертировать вручную.
- **`docx_invalid`** — Сгенерированный docx не прошёл валидацию; авторитетен markdown-результат.
- **`unresolved_reference`** — Некоторые ссылки не разрешились и помечены [unresolved: …] в результате.
- **`output_folder_unavailable`** — Запись в выходную папку не удалась; готовый артефакт остаётся в рабочем каталоге: {work_dir}.
- **`publish_failed`** — Готовый результат не удалось скопировать в папку публикации; он остаётся в рабочем каталоге по пути, который печатает финальное сообщение.{failure}
- **`dashboard_unavailable`** — Живой дашборд не удалось опубликовать ({reason}); проход продолжен, прогресс отражён обычными строками шагов.
- **`state_corrupt`** — state.json не читается; резюме восстановлено по файлам на диске.
- **`fallback_summary_delivered`** — Конвейер не смог завершиться; вместо этого доставлено запасное резюме всего собранного.
