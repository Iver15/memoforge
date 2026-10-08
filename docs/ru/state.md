# `state.json` (v2)

[Указатель](README.md) · [Оригинал](../state.md). Перевод документации v2.0.0.

Нормативный источник — `schemas/state.schema.json` (draft 2020-12). Эта страница — краткий справочник; при расхождении приоритет имеет схема. Все записи проходят через `memoforge.state_io.write_state()`: блокировка, проверка схемы и атомарная замена файла. За пределами CLI редактировать состояние нельзя; оркестратор и навыки только читают его. См. [фазы](phases.md) и [режим](modes.md).

Пути внутри состояния записаны в формате POSIX относительно `work_dir`. Исключения — `work_dir` и `output_folder`: они абсолютные, в формате платформы.

## Идентификация и входные данные

| Поле | Значение |
|---|---|
| `schema_version` | Всегда `2`. Для меньшей версии `mf task resolve` возвращает `unsupported`. |
| `task_id`, `created_at`, `user_query`, `language`, `ui_language` | Идентификатор задачи, дата создания и исходный вопрос без изменений. `language` — язык меморандума (`en` \| `de` \| `fr` \| `es` \| `ru`); `ui_language` — язык интерфейса из того же списка, необязателен, отсутствие означает `en`. |
| `work_dir`, `output_folder` | Абсолютные пути рабочего каталога и каталога, выбранного для него по цепочке ТЗ §2.5. |
| `mode` | Всегда `full`: записывается `mf task new` и повторяется в событии `mode_selected` при одобрении плана (D-242). |
| `config` | Итоговая конфигурация: `python_cmd`, `plugin_data_dir`, `reviewer_list`, `researcher_layers`, `max_iterations`, `lint_fix_rounds`, `intake_max_questions`, `client_polish_enabled`, `max_client_polish`, `template_id`, `template_path`, `citation_style`, `writer_model`, `publish_folder`, `source_review_gate`, `dashboard`, `mcp_budget`, а также выбранный профиль: `style_profile`, `style_profile_path`, `prose_style_path`. `citation_style` равен `footnotes`, `inline` или `null`; при `null` решение берётся из метаданных шаблона, в обоих штатных шаблонах — `inline` (D-150). |

Две настройки хуков из `userConfig`, `stop_guard` и `websearch_autoallow`, **не копируются** в `config`: хуки читают `CLAUDE_PLUGIN_OPTION_*` из окружения, процесс не читает эти параметры из состояния (D-74). `dashboard` — исключение (D-87): `mf next` использует его для включения блока страницы прогресса §7.5. Поэтому параметр определяется один раз при `task new` и хранится в `config`. По умолчанию `true` (D-92). Приоритет, как для остальных `userConfig`: флаг → `CLAUDE_PLUGIN_OPTION_*` → `<plugin_data_dir>/options.json` → значение по умолчанию (D-91).

## Положение в процессе

| Поле | Значение |
|---|---|
| `current_phase` | Одна из 18 фаз; `done`, `failed`, `cancelled_by_user` — конечные. |
| `steps[]` | Все шаги протокола: `step_id`, `kind` (`dispatch` \| `script` \| `inline-llm` \| `gate-text` \| `gate-auq` \| `terminal`), `phase`, `attempt`, `reason`, `generation` для согласований, `agents[]` (слот, тип и статус агента), `inputs`, `inputs_sha`, `command`, `expected_outputs[]`, `status`, `closed_at`, `result_ref`, `superseded`. Идентификатор шага — пара `(step_id, attempt)`. `inputs_sha` содержит хеши входных файлов скриптового шага для оценки повторного выпуска с `reason: rerun` (D-58). |
| `published[]` | Канонические артефакты: `canonical_path`, `sha256`, `by`, `step_id`, `at`. Только `machine.publish` добавляет записи. Файл вне списка не считается подтверждённым результатом. |
| `progress` | `phase`, `phase_started_at`, `route`, `position`, `total`, `active`, `last_line` — последняя `chat_line`, `artifact_url`, `published_to` — каталог копирования результата при `mf finalize` (D-109), `published_memo` — копия `memo-<slug>.<ext>` в корне области результатов (D-167), `mcp_calls`. Пересчитывается при каждом `next`. |
| `attempts` | Счётчики лимитов (ТЗ §2.2): `plan_edit`, `sufficiency_user_followup` и `sufficiency_research_followup` (D-116), `research_dispatch_retry`, `currency_regate`, `client_polish`, `lint_fix`, `reviewer_json_retry`, `reviewer_rerun`, `targeted_fix` (D-165), `single_dispatch_retry`, `inline_llm_retry`, `gate_parse_errors` — отдельно для каждой точки согласования. |
| `cancel_requested` | Устанавливается `mf task cancel`. После этого `next` не выдаёт новую работу и направляет процесс к завершению (ТЗ §2.4 d). |

## Содержание задачи

| Поле | Значение |
|---|---|
| `intake`, `classification`, `plan_approval` | Исходные ответы, классификация и история согласования плана (`iterations[]`, `mode_selected`). |
| `dispatched_researchers[]` | Реально запущенные слои исследования. |
| `sufficiency_followup` | Открытое уточнение полноты исследования: `status`, `subset_u`, `subset_r`, `approved_layers`, `questions`, `user_response`, `asked_at`, `answered_at`. `approved_layers` — слои, на которые маршрутизатор выделил лимит повторного исследования и которые фаза 5 может запустить заново. `null`, если уточнения нет. |
| `mcp_exhausted` | Псевдоним маршрутизации → дата UTC исчерпания квоты MCP-сервера. До конца этого дня `mf next` исключает его инструменты из обзора маршрутизации (D-122). |
| `sources_frozen` | `true`, когда `sources pack --freeze` записал `research/source-pack.json` (D-03). |
| `current_iteration`, `iterations[]`, `targeted_fix` | Текущий раунд рецензирования и по одной записи `mf review aggregate` на раунд: `iteration`, `draft_sha`, `reviewers`, `coverage`, `failed_reviewers`, `downgraded_reviewers`, `substance_blockers`, `form_blockers`, `deterministic_blockers`, `pass_ratio`, `conflict`, `stale_reports`, `issues`, `aggregated_at` (ТЗ §4.5 п.3, D-77). `targeted_fix` равен `null` до единственного адресного прохода проверки ссылок по ветви 9 §4.5 п.4; затем содержит `{iteration, reviewers[]}` — созданный раунд и ожидаемых `mf review aggregate` рецензентов (D-165). |
| `current_draft_path`, `current_draft_sha`, `draft_versions[]` | Текущий черновик и все версии с признаками `lint_clean`, `citations_clean` и датой `checked_at`. |
| `client_readiness` | Результат рецензента готовности для клиента. |
| `drafting_warnings[]`, `remaining_blocking_issues[]`, `fallback_banners[]` | Предупреждения, переносимые в результат; тексты резервных предупреждений определены в `fallbacks.py`. |
| `open_substance_majors[]` | Необязательное поле (D-210): существенные замечания `logic`, `citations`, `counterarguments`, оставшиеся на версии при выходе из цикла. Каждая строка содержит `id` (`om-<n>`), `class`, `reviewer`, `section_id`, `category`, `issue_category`, `issue`, `issue_client`, `suggestion`, `from_iteration`, `origin` (`loop` или `recheck`), `status` (`open`, `resolved`, `unresolved`, `manual_review`, `left`). Записывается `mf revision next` при выходе к проверке готовности, а также при сбое автора. Проверка готовности (D-211) устанавливает `status` по решениям рецензента и повторной проверке финальной правки; новые существенные замечания добавляются с `origin: recheck`. Открытые замечания выводятся в `summary.md`. До D-210 поле отсутствует. |
| `polish_check` | Необязательное поле (D-211): проверка границ финальной правки относительно `reviews/v<N>-prepolish.md`. Записывается при первом входе после автора правки: `draft_sha` исправленного текста и `errors` (`section_out_of_scope: <id>`, `new_source_token: <id>: <ids>`, `baseline_unavailable`, `draft_unavailable`). Недоступность подтверждённого входного файла, включая утрату исходной версии до перепроверки, означает сбой проверки. Любая ошибка восстанавливает исходную версию в каноническом файле. Если её нет, закрепляется последняя версия, чьи байты были проверены циклом рецензирования. Процесс переходит к `export` с причиной `polish_out_of_scope`; статус одобренного, принятого или готового документа меняется на `manual_review_required_on_v<N>`, прочие статусы сохраняются. Без открытых существенных замечаний или финальной правки поле отсутствует. |
| `export_pin` | Необязательное поле (D-211): `{version, sha256}` версии, выбранной для экспорта после выхода правки за допустимые границы: восстановленная исходная версия либо последняя версия, проверенная циклом. `export_draft_sha` выбирает её независимо от признаков проверки в `draft_versions[]`. `docx.select_draft`, включая выбор `finalize` без `--draft-sha`, сначала сопоставляет её номер и хеш, затем ищет более старую версию с теми же байтами. Это обеспечивает соответствие экспортированного текста версии в статусе. Иначе поле отсутствует. |

## Итог

| Поле | Значение |
|---|---|
| `final_status` | Устанавливается `mf finalize` одновременно с конечной фазой. Пока задача выполняется, равен `null`. |
| `final_status_reasons[]` | Причины результата (D-21, D-30), например `unresolved_blockers`, `lint_not_converged`, `cli_error`, `step_loop`, `interrupted`. |
| `final_docx_path` | Путь результата относительно `work_dir`. `finalize` гарантирует документ и `summary.md` на всех ветвях (M9). |
| `delivered_draft_sha` | Необязательное поле (D-220): SHA-256 байтов черновика, из которого создан результат; записывается `mf finalize`. `null` означает, что результат не создан из черновика: универсальная резервная сводка либо повторно использованный экспорт при отсутствии черновика на диске. Отсутствие поля означает завершение задачи до плана 75A. |

Чтение: `mf state get --workdir W [--path <dotted.path>]`. Проверка: `mf state validate --workdir W`.
