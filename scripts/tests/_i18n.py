"""Synthetic language packs for tests — never the real `lib/i18n/*.json` files (D-168).

`fake_pack` starts from a deep copy of the English floor (`i18n_en.EN`), so a test pack
always has the full key tree and only the dotted-key overrides differ. `RU` is the shared
Russian overlay the language tests build on.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from memoforge import i18n, i18n_en

RU: dict = {
    "memo.sections.executive_summary": "Резюме",
    "memo.sections.background": "Контекст",
    "memo.sections.facts": "Факты",
    "memo.sections.assumptions": "Допущения",
    "memo.sections.conclusion": "Выводы",
    "memo.sections.recommendations": "Рекомендации",
    "memo.risk.label": "Риск",
    "memo.risk.levels.high": "высокий",
    "memo.risk.levels.medium": "средний",
    "memo.risk.levels.low": "низкий",
    "memo.risk.levels.undetermined": "не определён",
    "memo.facts.facts_label": "Факты",
    "memo.facts.assumptions_label": "Допущения",
    "memo.facts.limitations_label": "Ограничения",
    "memo.lint_off": ["L-03"],
    "memo.placeholders_ignore_case": False,
}
"""Dotted-key overrides for a Russian test pack: sections, the risk label and levels,
`L-03` off and case-sensitive placeholders."""

RU_UI: dict = {
    # D-176: the interface half of a Russian test pack — phases, gates and the user-facing
    # `machine` lines. `ui.language_names` is not overridden: the endonyms of `EN` are the same
    # in every pack. Text-channel tokens (`proceed`, `continue`, `approve`, `edit:`, `cancel`,
    # `standard`) stay English inside their backticks (D-176a).
    "ui.phases.intake_preliminary_research": "Проверяем, какие юридические базы доступны",
    "ui.phases.intake_questions_pending": "Ваши вводные ответы",
    "ui.phases.planning": "Готовим план исследования",
    "ui.phases.plan_approval_pending": "Утверждение плана",
    "ui.phases.research": "Юридическое исследование",
    "ui.phases.research_sufficiency": "Проверяем полноту исследования",
    "ui.phases.research_sufficiency_followup_pending": "Ваши ответы на уточняющие вопросы",
    "ui.phases.research_insufficient_pending": "Ваше решение при неполном исследовании",
    "ui.phases.currency_check": "Проверяем актуальность источников",
    "ui.phases.source_pack": "Собираем пакет источников",
    "ui.phases.source_review_pending": "Ваша проверка источников",
    "ui.phases.drafting": "Пишем мемо",
    "ui.phases.revision_loop": "Раунд ревью",
    "ui.phases.client_readiness": "Проверка готовности для клиента",
    "ui.phases.export": "Экспорт мемо (DOCX)",
    "ui.phases.done": "Готово",
    "ui.phases.failed": "Остановлено с резервным результатом",
    "ui.phases.cancelled_by_user": "Отменено",
    "ui.gates.mode_summary_brief": "Один слой исследования, два раунда ревью, ~1200 слов.",
    "ui.gates.mode_summary_full": "До трёх слоёв, два раунда ревью, полное мемо.",
    "ui.gates.brief_mismatch_hint_one": (
        "Вопросов в плане: {count}, сложность — {complexity}; «Кратко» исследует один слой "
        "(законодательство) и вмещает три раздела: практика и доктрина уйдут в оговорки."
    ),
    "ui.gates.brief_mismatch_hint_many": (
        "Вопросов в плане: {count}, сложность — {complexity}; «Кратко» исследует один слой "
        "(законодательство) и вмещает три раздела: практика и доктрина уйдут в оговорки."
    ),
    "ui.gates.question_line": "{index}. {question}",
    "ui.gates.option_line": "{letter}) {label} — {description}",
    "ui.gates.default_line": "{question} — допущение: {default}",
    "ui.gates.default_line_confidence": "{question} — допущение: {default} (уверенность: {confidence})",
    "ui.gates.default_if_wrong": "Если это не так: {value}",
    "ui.gates.slash_line": "Ответьте здесь или запустите `/memoforge:continue {task_id} {example}`.",
    "ui.gates.intake_form": "Ответьте в формате `1A 2C 3: свободный текст`:",
    "ui.gates.intake_no_questions": "Вопросов к вам нет; ответьте `proceed`, чтобы продолжить.",
    "ui.gates.intake_defaults_heading": "Остальное принимается так:",
    "ui.gates.intake_footer": "`proceed` принимает все допущения как есть. `cancel` останавливает задачу.",
    "ui.gates.followup_form": (
        "Исследование оставило пробелы, закрыть которые можете только вы. "
        "Ответьте в формате `1A 2C 3: свободный текст`:"
    ),
    "ui.gates.followup_skipped": "Если пропустить, примем: {default}",
    "ui.gates.followup_footer": "`proceed` принимает допущения выше. `cancel` останавливает задачу.",
    "ui.gates.insufficient_lead": "Исследование не дотянуло до планки клиентского мемо.",
    "ui.gates.insufficient_gaps_heading": "Открытые пробелы:",
    "ui.gates.insufficient_continue": "`continue` пишет мемо всё равно, пробелы уйдут в оговорки.",
    "ui.gates.insufficient_cancel": "`cancel` останавливает задачу.",
    "ui.gates.plan_unreadable": "План исследования не читается; измените его или отмените задачу.",
    "ui.gates.plan_digest_head": (
        "План — {classification}, юрисдикции: {jurisdictions}, оценка сложности: {complexity}."
    ),
    "ui.gates.plan_digest_unclassified": "без классификации",
    "ui.gates.plan_digest_unspecified": "не указаны",
    "ui.gates.plan_digest_unknown": "неизвестна",
    "ui.gates.plan_digest_file": "Полный план: `{path}` в рабочей папке задачи.",
    "ui.gates.memo_language_line": "Язык мемо: {name}",
    "ui.gates.plan_digest_issues_heading": "Правовых вопросов для исследования: {count}",
    "ui.gates.plan_digest_issue_line": "{issue_id} — {title}",
    "ui.gates.plan_digest_issue_line_where": "{issue_id} — {title} [{jurisdictions}]",
    "ui.gates.plan_digest_no_issues": "в `{path}` не записано ни одного",
    "ui.gates.plan_digest_more_issues": "…и ещё {count}, перечислены в `{path}`",
    "ui.gates.plan_digest_layers": "Слои исследования: {layers} (доктрина — {doctrine}).",
    "ui.gates.plan_digest_no_layers": "нет",
    "ui.gates.plan_digest_doctrine_required": "нужна",
    "ui.gates.plan_digest_doctrine_not_required": "не нужна",
    "ui.gates.plan_digest_recommended_mode": "Рекомендуемый режим: {mode} — {summary}",
    "ui.gates.plan_digest_notes": "Заметки планировщика: {notes}",
    "ui.gates.plan_text_question": "{header}: {question}",
    "ui.gates.plan_text_options": "варианты: {labels}",
    "ui.gates.plan_text_reply_heading": "Ответьте одним из:",
    "ui.gates.header_plan": "План",
    "ui.gates.header_mode": "Режим",
    "ui.gates.header_style": "Стиль",
    "ui.gates.header_sources": "Источники",
    "ui.gates.option_approve": "Утвердить",
    "ui.gates.option_edit": "Изменить",
    "ui.gates.option_cancel": "Отмена",
    "ui.gates.option_brief": "Кратко",
    "ui.gates.option_full": "Полный",
    "ui.gates.option_continue": "Продолжить",
    "ui.gates.option_approve_description": "Начать исследование по плану как есть.",
    "ui.gates.option_edit_description": "Скажите, что изменить; план будет перестроен.",
    "ui.gates.option_cancel_description": "Остановить задачу сейчас.",
    "ui.gates.option_continue_description": "Продолжить с ограниченным покрытием.",
    "ui.gates.plan_question": "Утвердить этот план исследования?",
    "ui.gates.mode_question": "Какой глубины должно быть мемо?",
    "ui.gates.mode_recommended": "(Рекомендуется) {description}",
    "ui.gates.style_question": "В каком стиле писать мемо?",
    "ui.gates.style_option_profile_description": "Использовать сохранённый профиль `{name}`.",
    "ui.gates.style_option_standard_description": "Использовать встроенный стиль.",
    "ui.gates.sources_question": "Покрытие источников может быть ограничено. {detail}",
    "ui.gates.sources_estimate": (
        "Оценка: {total} обращений к юридическим источникам при дневных квотах {servers} "
        "({upper_bound} всего; квота — верхняя граница, а не остаток)."
    ),
    "ui.gates.sources_missing_database": (
        "Для слоя {layer} в {jurisdiction} не подключена ни одна база ({servers})."
    ),
    "ui.gates.sources_missing_portal": (
        "Сегодня для слоя {layer} в {jurisdiction} не ответил ни один источник ({portals})."
    ),
    "ui.machine.plan_gate_dashboard": "План исследования — на вашей панели: {url}",
    "ui.machine.plan_gate_file": "Файл: {path} в рабочей папке {work_dir}",
    "ui.machine.plan_gate_shape_one": (
        "Правовых вопросов: {count} · рекомендуемый режим: {mode} · оценка сложности: {complexity}"
    ),
    "ui.machine.plan_gate_shape_many": (
        "Правовых вопросов: {count} · рекомендуемый режим: {mode} · оценка сложности: {complexity}"
    ),
    "ui.machine.gate_pointer": "{label} — на панели: {url}",
    "ui.machine.gate_pointer_one": "{label} (вопросов: {count}) — на панели: {url}",
    "ui.machine.gate_pointer_many": "{label} (вопросов: {count}) — на панели: {url}",
    # D-177: the dashboard document and page (plan 56 task 2).
    "ui.machine.gate_hint": "ждём вашего решения в чате",
    "ui.machine.answer_hint_intake": "Ответьте в чате: 1A 2C 3: ваш текст · proceed · cancel",
    "ui.machine.answer_hint_sufficiency_followup": (
        "Ответьте в чате: 1A 2C 3: ваш текст · proceed · cancel"
    ),
    "ui.machine.answer_hint_insufficient": "Ответьте в чате: continue · cancel",
    "ui.machine.answer_hint_source_review": "Ответьте в чате: continue · cancel",
    "ui.machine.answer_hint_plan": (
        "Ответьте на вопрос из чата (или текстом: approve [brief|full] · edit: … · cancel)"
    ),
    "ui.machine.agent_fact_assumption_analyst": "Аналитик фактов и допущений",
    "ui.machine.agent_legal_researcher": "Исследователь",
    "ui.machine.agent_research_sufficiency_reviewer": "Проверяющий полноту",
    "ui.machine.agent_currency_checker": "Проверяющий актуальность",
    "ui.machine.agent_memo_writer": "Автор мемо",
    "ui.machine.agent_logic_reviewer": "Проверяющий логику",
    "ui.machine.agent_form_reviewer": "Проверяющий форму",
    "ui.machine.agent_citation_auditor": "Проверяющий цитаты",
    "ui.machine.agent_counterargument_reviewer": "Проверяющий контраргументы",
    "ui.machine.agent_revision_mediator": "Медиатор правок",
    "ui.machine.agent_client_readiness_reviewer": "Проверяющий готовность",
    "ui.machine.agent_style_extractor": "Извлекатель стиля",
    "ui.machine.slot_statutes": "законодательство",
    "ui.machine.slot_case_law": "практика",
    "ui.machine.slot_doctrine": "доктрина",
    "ui.machine.script_render_research_running": "Готовим исследование автору",
    "ui.machine.script_render_research_finished": "Исследование готово автору",
    "ui.machine.script_render_mediator_running": "Готовим заметки медиатору",
    "ui.machine.script_render_mediator_finished": "Заметки медиатору готовы",
    "ui.machine.script_sufficiency_route_running": "Проверяем полноту исследования",
    "ui.machine.script_sufficiency_route_finished": "Полнота исследования проверена",
    "ui.machine.script_sources_preflight_running": "Проверяем, какие порталы отвечают сегодня",
    "ui.machine.script_sources_preflight_finished": "Порталы проверены",
    "ui.machine.script_sources_liveness_running": "Проверяем, что все ссылки работают",
    "ui.machine.script_sources_liveness_finished": "Ссылки проверены",
    "ui.machine.script_sources_verify_running": "Проверяем идентификаторы источников",
    "ui.machine.script_sources_verify_finished": "Идентификаторы проверены",
    "ui.machine.script_sources_pack_running": "Собираем пакет источников",
    "ui.machine.script_sources_pack_finished": "Пакет источников заморожен",
    "ui.machine.script_draft_anchor_running": "Нумеруем разделы",
    "ui.machine.script_draft_anchor_finished": "Разделы пронумерованы",
    "ui.machine.script_draft_lint_running": "Проверка стиля и структуры",
    "ui.machine.script_draft_lint_finished": "Стиль и структура проверены",
    "ui.machine.script_draft_audit_citations_running": "Проверка цитат",
    "ui.machine.script_draft_audit_citations_finished": "Цитаты проверены",
    "ui.machine.script_draft_finish_running": "Проверяем черновик",
    "ui.machine.script_draft_finish_finished": "Черновик проверен",
    "ui.machine.script_review_aggregate_running": "Сводим вердикты ревью",
    "ui.machine.script_review_aggregate_finished": "Вердикты ревью сведены",
    "ui.machine.script_revision_next_running": "Выбираем, что править дальше",
    "ui.machine.script_revision_next_finished": "Направление правок выбрано",
    "ui.machine.script_docx_render_running": "Экспорт мемо (DOCX)",
    "ui.machine.script_docx_render_finished": "Мемо экспортировано",
    "ui.machine.script_docx_validate_running": "Проверяем экспортированный файл",
    "ui.machine.script_docx_validate_finished": "DOCX проверен",
    "ui.machine.script_finalize_running": "Собираем результат",
    "ui.machine.script_finalize_finished": "Результат готов",
    "ui.machine.inline_plan_json_running": "Готовим план исследования",
    "ui.machine.inline_plan_json_finished": "План составлен",
    "ui.machine.inline_mcp_probe_json_running": "Проверяем, какие юридические базы доступны",
    "ui.machine.inline_mcp_probe_json_finished": "Юридические базы проверены",
    "ui.machine.suffix_started": "запущено",
    "ui.machine.suffix_finished": "завершено",
    "ui.machine.suffix_failed_retrying": "ошибка, повторяем",
    "ui.machine.suffix_skipped": "пропущено",
    "ui.machine.status_working": "Работаем",
    "ui.machine.status_your_turn": "Ваш ход",
    "ui.machine.status_cancelling": "Отменяем",
    "ui.machine.waiting_for_you": "Ваш ход: {label}",
    "ui.machine.skipped": "Пропущено: {label}",
    "ui.machine.you_answered": "Вы ответили: {label}",
    "ui.machine.phase_step": "Шаг: {label}",
    "ui.machine.more": "+ещё {count}",
    "ui.machine.review_no_blockers": "Блокеров в этом раунде нет.",
    "ui.machine.review_blockers_left": "Блокеры остались для следующей правки.",
    "ui.machine.review_incomplete": "Ревьюер не вернул ничего пригодного, раунд неполный.",
    "ui.machine.intake_row": "{question} — допущение: {default}",
    "ui.machine.title": "memoforge · {task_id}",
    "ui.machine.description": "Живой прогресс запуска memoforge",
    "ui.dashboard.title": "memoforge запуск",
    "ui.dashboard.query_waiting": "Ждём данные…",
    "ui.dashboard.chat_waiting": "Ждём данные…",
    "ui.dashboard.tabs_label": "Разделы запуска",
    "ui.dashboard.tab_overview": "Обзор",
    "ui.dashboard.tab_intake": "Вводные",
    "ui.dashboard.tab_plan": "План",
    "ui.dashboard.tab_sources": "Источники",
    "ui.dashboard.tab_reviews": "Ревью",
    "ui.dashboard.tab_memo": "Мемо",
    "ui.dashboard.card_your_turn": "Ваш ход",
    "ui.dashboard.card_running_now": "Выполняется",
    "ui.dashboard.card_timeline": "Хроника",
    "ui.dashboard.card_run_facts": "Факты запуска",
    "ui.dashboard.live_connecting": "подключаемся…",
    "ui.dashboard.label_mode": "Режим",
    "ui.dashboard.label_phase": "Фаза",
    "ui.dashboard.label_steps": "Шаги",
    "ui.dashboard.label_updated": "Обновлено",
    "ui.dashboard.card_notices": "Заметки",
    "ui.dashboard.card_intake": "Вводные",
    "ui.dashboard.card_assumed": "Принято без вопроса",
    "ui.dashboard.card_plan": "План",
    "ui.dashboard.card_sources": "Источники",
    "ui.dashboard.card_reviews": "Раунды ревью",
    "ui.dashboard.card_memo": "Мемо",
    "ui.dashboard.label_file": "Файл",
    "ui.dashboard.label_summary": "Сводка",
    "ui.dashboard.label_published": "Опубликовано",
    "ui.dashboard.skip_note": "Если пропустите: {default}",
    "ui.dashboard.plan_unclassified": "без классификации",
    "ui.dashboard.plan_complexity": "сложность {value}",
    "ui.dashboard.plan_recommended_mode": "рекомендуемый режим {value}",
    "ui.dashboard.plan_layers": "слои: {value}",
    "ui.dashboard.plan_approved": "утверждено",
    "ui.dashboard.plan_awaiting": "ждёт вашего утверждения",
    "ui.dashboard.plan_decision_you_chose": "вы выбрали {value}",
    "ui.dashboard.intake_answered": "Вы ответили: {value}",
    "ui.dashboard.intake_not_answered": "Не отвечено, принято: {value}",
    "ui.dashboard.intake_answered_count": "{answered} из {total} отвечено",
    "ui.dashboard.intake_assumed_mark": "принято",
    "ui.dashboard.intake_confidence": "уверенность {value}",
    "ui.dashboard.sources_frozen": "заморожено {value}",
    "ui.dashboard.sources_critical": "{count} ключевых",
    "ui.dashboard.sources_supporting": "{count} поддерживающих",
    "ui.dashboard.sources_background": "{count} фоновых",
    "ui.dashboard.reviews_round": "раунд {value}",
    "ui.dashboard.reviews_draft": "черновик v{value}",
    "ui.dashboard.reviews_blockers": "{count} блокеров",
    "ui.dashboard.reviews_no_answer": "нет ответа от {value}",
    "ui.dashboard.query_fallback": "запуск memoforge",
    "ui.dashboard.live_unavailable": "живые обновления недоступны",
    "ui.dashboard.live_on": "онлайн",
    # D-176b: the nine parameter-free notice stand-ins the page publishes for a banner whose
    # rendered text carries diagnostics; the constant banners are read from `memo.banners`.
    "ui.banners.mcp_partial": "Частичное покрытие MCP; пробел отмечен в файлах исследования.",
    "ui.banners.mcp_soft_cap_exceeded": "Один из серверов MCP превысил мягкий лимит за проход.",
    "ui.banners.currency_blocking": (
        "Проверка актуальности дала блокирующие замечания; затронутые источники помечены в "
        "пакете источников."
    ),
    "ui.banners.reviewer_output_malformed": (
        "Цикл доработки принудительно завершён — вывод рецензента был с ошибками; доставлен "
        "последний проект."
    ),
    "ui.banners.mediator_unavailable": (
        "Медиация недоступна; проход завершился на последнем валидированном проекте."
    ),
    "ui.banners.unresolved_blockers": (
        "ЗАМЕЧАНИЯ РЕЦЕНЗЕНТОВ ЗАКРЫТЫ НЕ ПОЛНОСТЬЮ — остались блокирующие замечания (перечислены "
        "в приложении)."
    ),
    "ui.banners.output_folder_unavailable": (
        "Запись в выходную папку не удалась; готовый артефакт остаётся в рабочем каталоге."
    ),
    "ui.banners.publish_failed": (
        "Готовый результат не удалось скопировать в папку публикации; он остаётся в рабочем "
        "каталоге по пути, который печатает финальное сообщение."
    ),
    "ui.banners.dashboard_unavailable": (
        "Живой дашборд не удалось опубликовать; проход продолжен, прогресс отражён в чате."
    ),
    # D-176 (sources/preflight, plan 56 task 3): the gate-11 digest rows and the
    # preflight block. Machine tokens (`[kind]`, `source_id`, `tier`, currency
    # status values, `do_not_use`, the backticked `continue`/`cancel`) stay raw
    # inside the Russian frame; alternatives and drafting-warning rows stay English.
    "ui.sources.digest_head": "Проверка источников — {count} источников зарегистрировано ({frozen}).",
    "ui.sources.digest_frozen": "заморожено",
    "ui.sources.digest_not_frozen": "не заморожено",
    "ui.sources.exceptions_heading": "Исключения, требующие вашего внимания:",
    "ui.sources.no_exceptions": "Исключений нет: все ключевые источники проверены и актуальны.",
    "ui.sources.reply_line": "Ответьте `continue`, чтобы писать по этим источникам, или `cancel`, чтобы остановиться.",
    "ui.preflight.status_ok": "отвечает",
    "ui.preflight.status_waf_challenge": "WAF-проверка",
    "ui.preflight.status_cloudflare": "блокировка Cloudflare",
    "ui.preflight.status_interstitial": "200 со страницей проверки, а не документом",
    "ui.preflight.status_dead": "не ответил",
    "ui.preflight.status_tls": "TLS-сертификат не проверен",
    "ui.preflight.clean_line": "сегодня ответили все подключённые порталы",
    "ui.preflight.unknown_line": "не проверялось",
    "ui.preflight.block_head": "Доступ к источникам сегодня:",
    "ui.preflight.more_line": "…и ещё {count} — в `{path}`",
}
"""The interface overlay of the Russian test pack (plan 56); `RU` covers the memo half."""


def _apply(pack: dict, dotted: str, value: object) -> None:
    """Set one dotted key (`memo.risk.label`) inside a nested pack dict."""
    node = pack
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def fake_pack(directory: str | Path, code: str, overrides: dict | None = None) -> Path:
    """Write `<directory>/<code>.json`: EN deep copy + dotted-key overrides, UTF-8, non-ASCII raw."""
    pack = copy.deepcopy(i18n_en.EN)
    pack["code"] = code
    pack["name"] = i18n.LANGUAGE_NAMES.get(code, code)
    for dotted, value in (overrides or {}).items():
        _apply(pack, dotted, value)
    path = Path(directory) / f"{code}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    return path
