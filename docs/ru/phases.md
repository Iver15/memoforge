# Этапы процесса (v2)

[Указатель](README.md) · [Оригинал](../phases.md). Перевод документации v2.0.0.

Источник — `scripts/memoforge/phases.py` (ТЗ §2.1). Конечное состояние записывается только после того, как `mf finalize` создал `deliverable.{docx|md}` и `summary.md` (M9).

| № | Фаза | Тип | Название (D-95) |
|---|---|---|---|
| 1 | `intake_preliminary_research` | работа | Проверка доступности правовых баз |
| 2 | `intake_questions_pending` | согласование | Ваши ответы на исходные вопросы |
| 3 | `planning` | работа | Подготовка плана исследования |
| 4 | `plan_approval_pending` | согласование | Одобрение плана |
| 5 | `research` | работа | Правовое исследование |
| 6 | `research_sufficiency` | работа | Проверка полноты исследования |
| 7 | `research_sufficiency_followup_pending` | согласование | Ваши ответы на дополнительные вопросы |
| 8 | `research_insufficient_pending` | согласование | Ваше решение при недостаточном исследовании |
| 9 | `currency_check` | работа | Проверка актуальности источников |
| 10 | `source_pack` | работа | Подготовка набора источников |
| 11 | `source_review_pending` | согласование | Проверка источников пользователем |
| 12 | `drafting` | работа | Написание меморандума |
| 13 | `revision_loop` | работа | Рецензирование и доработка |
| 14 | `client_readiness` | работа | Проверка готовности для клиента |
| 15 | `export` | работа | Экспорт меморандума в DOCX |
| 16 | `done` | конечное состояние | Готово |
| 17 | `failed` | конечное состояние | Остановлено с резервным результатом |
| 18 | `cancelled_by_user` | конечное состояние | Отменено пользователем |

Начальная фаза: `intake_preliminary_research`.

Точки согласования, определяющие границы сегментов (M8): `intake_questions_pending`, `plan_approval_pending`, `research_sufficiency_followup_pending`, `research_insufficient_pending`, `source_review_pending`.

Конечные фазы: `done`, `failed`, `cancelled_by_user`.
