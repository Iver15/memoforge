# Тестирование

[Указатель](README.md) · [Оригинал](../../scripts/tests/README.md). Перевод документации v2.0.0.

Используется только `unittest`; memoforge не использует `pytest`. Все тесты автономны: без сети, MCP и Anthropic API. Временные рабочие каталоги создаются через `tempfile.TemporaryDirectory()`.

## Запуск

Из корня плагина, рабочим Python с установленными зависимостями:

```bash
python -m unittest discover -s scripts/tests -v
```

Отдельный модуль или тест:

```bash
python -m unittest scripts.tests.test_machine -v
python -m unittest scripts.tests.test_lint.LintRulesTest.test_l08_blockquote_needs_quote_id -v
```

`jsonschema`, `python-docx` и `mistune` должны импортироваться, иначе тесты пропускаются. **Пропуски из-за отсутствующих зависимостей означают ошибку CI** (§9, G9). Установка: `pip install -r requirements.txt`; команда `mf deps install` устанавливает отдельную копию для CLI в каталог данных плагина. При прямом запуске `unittest` добавьте этот каталог `site-packages` в `PYTHONPATH` либо установите зависимости в используемое тестовое окружение. Для PDF-тестов нужен `pypdf`, как в CI; в рабочей среде плагина он необязателен.

## Структура

| Группа | Модули | Что проверяется |
|---|---|---|
| Источники истины | `test_phases`, `test_modes`, `test_events`, `test_fallbacks`, `test_schema`, `test_schemas_artifacts` | M11: одно определение каждого перечисления; положительные и отрицательные случаи схем §6 |
| Состояние | `test_state_io`, `test_state_cmd`, `test_stepctx`, `test_no_unvalidated_state_writes` | M2: блокировки, атомарная замена, проверка схемы до замены, идентификация шага |
| Машина процесса | `test_machine`, `test_dispatch`, `test_gates`, `test_task`, `test_probe_dryrun`, `test_cli` | M1/M3/M8: фаза × режим × исход, идемпотентность `next`/`report`, разбор ответов, сквозной тестовый прогон, одно событие `cli_call` на вызов (D-43) |
| Проверки до LLM | `test_lint`, `test_citations`, `test_review`, `test_revision`, `test_sufficiency`, `test_sources`, `test_quotes`, `test_routing`, `test_style_profile` | M5/M6/M10: 14 правил, аудит ссылок, агрегация чек-листов, фиксация источников |
| Результат | `test_render`, `test_docx_fallback`, `test_finalize`, `test_analyze`, `test_docs_render` | M4/M9: представления JSON→Markdown, DOCX и резервный экспорт на стандартной библиотеке, гарантированный результат при сбоях, `events analyze` |
| Платформа | `test_hooks`, `test_hooks_common`, `test_pylauncher`, `test_launcher_wrappers`, `test_deps` | M12: три интерпретатора, пути с пробелами, обходы хуков, `mf deps check\|install` |
| Контракты | `test_agent_frontmatter`, `test_agent_prompt_hygiene`, `test_agent_outputs_are_json_only`, `test_skills`, `test_risk_line_contract`, `test_version_matches_plugin_json`, `test_check_versions`, `test_no_legacy` | §9: соответствие инструкций, навыков и манифеста коду; соответствие опубликованных копий манифесту (D-73) |

`_pipeline.py` — общий исполнитель тестового цикла `next → act → report`, а не модуль тестов. Использует фикстуры `mf probe dry-run` и позволяет внедрить сбой на любом шаге.

## Фикстуры

`scripts/tests/fixtures/` содержит подготовленные ответы агентов (`prompts/`, `drafts/`, `reviews/`, `schemas/`, `sufficiency/`). Это данные; варианты создаются в отдельной временной папке, а исходные фикстуры не редактируются.

`test_no_legacy.py` намеренно исключает `scripts/tests/*.py` и `fixtures/`: тест должен называть запрещаемые идентификаторы, а фикстуры воспроизводят входы старого формата v1.

## CI

`.github/workflows/ci.yml` проверяет `ubuntu-latest` и `windows-latest` с Python 3.9 и 3.13: `compileall`, полный запуск тестов и проверку допустимости пропусков через `scripts/ci/check_no_skips.py`, `ruff check --select E,F,W scripts hooks`, `mf docs render --check`, `mf probe dry-run`, `scripts/ci/check_versions.py`.

Допускаются только причины пропуска, перечисленные в проверяющем скрипте. Отсутствующая зависимость означает ошибку сборки. Версии должны совпадать в `plugin.json`, `marketplace.json`, значке README и первой записи CHANGELOG. Сбой CI рассматривается как дефект сборки: тесты не должны зависеть от времени, локали или сети. В текущей v2.0.0 после D-242 тестовый прогон использует единственный режим Full; упоминание двух режимов в оригинальной памятке устарело.

## Что тесты не проверяют

- Настоящие меморандумы с MCP, авторизацией и живой сессией: это [проверки платформы](v2-probes.md), ТЗ §11.
- Внешний вид DOCX в Word: эталонные тесты проверяют содержимое `document.xml` и `footnotes.xml`.
- Поведение Cowork, включая вывод сообщений и состав `SubagentStart`: проверки P5/P8.
