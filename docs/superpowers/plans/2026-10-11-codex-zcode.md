# План реализации Memoforge для Codex и ZCode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать две самостоятельные русские сборки, установить их в Codex и ZCode и проверить выполнение задач и получение DOCX.

**Architecture:** Общее Python-ядро получает небольшой профиль хоста. Платформенные инструкции, манифесты и обёртки собираются в отдельные пакеты; юридические роли, схемы и протокол `next → act → report` остаются общими.

**Tech Stack:** Python 3.9+, unittest, JSON Schema, Bash/CMD, Markdown; действующие зависимости jsonschema, python-docx и mistune. Новые обязательные библиотеки не добавляются.

**Spec:** [Спецификация](../specs/2026-10-07-codex-zcode-design.md).

**Проверенная среда на 11 октября 2026 года:** Codex CLI 0.162.1; ZCode.app 3.14.4, встроенный CLI 0.16.9. Его вход — `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`. Сборок пока нет. Рекомендуемый способ выполнения — самостоятельно в этом чате; план ожидает проверки пользователя.

## Global Constraints

- Три профиля: `claude`, `codex`, `zcode`; пакет без профиля сохраняет Claude.
- Имена новых пакетов: `memoforge-codex` и `memoforge-zcode`.
- Новые значения по умолчанию: `memo_language=ru`, `ui_language=ru`, `dashboard=false`; модель наследуется от хоста.
- Новые резервные каталоги: `~/.codex/plugin-data/memoforge-codex`, `~/.zcode/plugin-data/memoforge-zcode`; затем собственный `<plugin_root>/.data`. В Windows — соответствующие пути в `%LOCALAPPDATA%`.
- Старые задачи без профиля — Claude; продолжение через другую платформу отклоняется.
- Только CLI публикует состояние; агенты пишут исключительно в выданные промежуточные пути.
- Общие файлы включаются в сборки автоматически. Их поддерживаемых вручную копий в `platforms/` нет.
- Каждый пакет самодостаточен, ссылки остаются внутри него, ZIP сохраняет исполняемость `scripts/mf`.
- Манифест Codex: `.codex-plugin/plugin.json`; ZCode: `.zcode-plugin/plugin.json`. MCP — `.mcp.json`, все десять исходных сервисов.
- Версия реализации — `2.1.0`, одна для ядра, манифестов, marketplace и документации. Оригинальные авторство и MIT сохраняются.
- Личная конфигурация, секреты, задачи и результаты проверки не входят в Git или ZIP.
- Основная приёмка выполняется на этом Mac. Проверка Windows-обёртки на Mac не объявляется проверкой Windows.

## Review Focus

1. Унаследованные переменные Claude не должны отправить новую сборку в чужой каталог данных — тест задачи 1.
2. Обновление или переезд пакета, пробелы и кириллица в пути не должны нарушить продолжение задачи — тесты задач 2 и 4.
3. Пустой ответ агента или JSON по неправильному пути не должны закрыть слот успешно — тест задачи 3.
4. Ответ на старое поколение gate не должен изменить текущую задачу — тест задачи 2.
5. Повторная установка сохраняет остальные плагины и настройки; частичный сбой не выдаётся за успех — тест задачи 5.

## Обязательные проверки перед изменением кода

GitNexus актуален для `5914e25`. Первичный impact:

- `render_agents`: CRITICAL, 21 затронутый символ, 4 непосредственных вызывающих функции, 10 потоков. Основные потребители — `machine.issue_dispatch`, `machine.resume_dispatch`, `brief._issue`, `dispatch.run_render`.
- `plugin_data_dir_candidates`: CRITICAL, 24 символа, 10 потоков; путь влияет на запуск, профили стиля и метаданные.
- `resolve_options`: LOW, 12 символов, 4 непосредственных вызова.

Перед фактической правкой повторить impact для затрагиваемых функций. UNKNOWN уточнять текстом и тестами, а не считать безопасным. Перед каждым коммитом выполнять `node .gitnexus/run.cjs detect-changes --scope all --repo . --limit 1000`; partial/truncated требуют повторной проверки. Служебные `.claude/`, AGENTS.md и CLAUDE.md не включать автоматически в продуктовый коммит.

### Task 1: Профиль, каталоги данных и запуск

**Files:** создать `scripts/memoforge/host_profile.py`, `scripts/tests/test_host_profile.py`; изменить `scripts/memoforge/pylauncher.py`, `scripts/mf`, `scripts/mf.cmd`, `scripts/memoforge/__init__.py`, `scripts/tests/test_launcher_wrappers.py`, `scripts/tests/test_pylauncher.py`, `scripts/tests/test_version_matches_plugin_json.py`.

**Interfaces:** `HostProfile` — frozen dataclass с `name`, `plugin_name`, `option_defaults`; `get_profile(root: Path | None = None) -> HostProfile`; `data_candidates(profile: HostProfile, root: Path) -> list[dict]`. Профиль пакета хранится в `memoforge-platform.json`; явный `MEMOFORGE_PLATFORM` должен совпадать с ним. В исходном дереве без этого файла действует Claude. Неверное имя или конфликт — ValueError с кодом `invalid_platform` или `platform_mismatch`.

- [ ] Написать тесты: `test_default_is_claude`, `test_unknown_platform_is_rejected`, `test_package_and_environment_must_agree`, `test_native_fallback_never_uses_claude_home`. Для codex/zcode проверить точные резервные пути и отсутствие `.claude/plugin-data/memoforge`; проверить LOCALAPPDATA, HOME отсутствует и недоступный первый каталог.

```python
def test_unknown_platform_is_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"MEMOFORGE_PLATFORM": "missing"}, clear=True):
            with self.assertRaisesRegex(ValueError, "invalid_platform"):
                get_profile(Path(tmp))
```

- [ ] Запустить `python -m unittest scripts.tests.test_host_profile -v`; подтвердить FAIL до реализации.
- [ ] Реализовать профиль и подключить цепочку в `pylauncher`. Для Codex проверять `PLUGIN_ROOT`/`PLUGIN_DATA`, затем совместимые `CLAUDE_PLUGIN_*`; для ZCode — `ZCODE_PLUGIN_ROOT` и совместимые переменные. Использовать только существующий runtime-корень и доступный каталог данных.
- [ ] Согласовать Bash/CMD с Python. Генерируемые обёртки задают свой профиль; существующий launcher сохраняет Claude по умолчанию. Проверить одинаковый результат `--print-plugin-data-dir` и Python, включая путь `Проба с пробелами`.
- [ ] Научить чтение версии выбирать нативный манифест самостоятельного пакета, сохранив исходный Claude-манифест. Проверить пакет без `.claude-plugin/` и повреждённый манифест.
- [ ] Запустить тесты профиля, launcher, pylauncher и версии. После проверки графа создать отдельный коммит этого компонента.

### Task 2: Настройки, принадлежность задачи и диалог

**Files:** изменить `scripts/memoforge/task.py`, `scripts/memoforge/modes.py`, `scripts/memoforge/machine.py`, `scripts/memoforge/brief.py`, `schemas/state.schema.json`; создать `scripts/tests/test_platform_tasks.py`; дополнить `scripts/tests/test_config_cmd.py` и `scripts/tests/test_gates.py`.

**Interfaces:** использовать `get_profile()`; добавить `require_task_platform(state: dict) -> None` в `host_profile.py`. Новое необязательное поле `state.runtime_platform` имеет enum `claude/codex/zcode`; отсутствие трактуется как Claude. Сигнатуру `modes.resolve_config(mode: object, user_config: dict | None = None) -> dict` сохранить.

- [ ] Написать тесты `test_native_defaults_are_russian`, `test_explicit_language_wins`, `test_model_inherits_host`, `test_other_platform_cannot_resume`, `test_legacy_task_is_claude`, `test_stale_gate_reply_does_not_advance`. Assert: ru/ru, dashboard false, writer_model inherit; старый gate оставляет шаг открытым.

```python
def test_model_inherits_host(self):
    with patch.dict(os.environ, {"MEMOFORGE_PLATFORM": "codex"}):
        config = modes.resolve_config(None, {"writer_model": "inherit"})
    self.assertEqual(config["writer_model"], "inherit")
    self.assertNotIn("writer_model_fallback", config)
```

- [ ] Запустить новый модуль и подтвердить FAIL.
- [ ] Подключить профильные defaults в цепочку `flags → host env → options.json → defaults`. Для новых профилей единственное значение writer_model — `inherit`: конкретную модель выбирают в хосте. Claude сохраняет opus/fable/sonnet и прежнее поведение неизвестного значения.
- [ ] Сохранять профиль через существующую валидируемую запись состояния. Проверять совпадение при `task resolve`, `next`, `report`, `brief next` и `brief report`; при конфликте вернуть `platform_mismatch` до изменения шагов и артефактов.
- [ ] Проверить повторное открытие той же задачи после переноса каталога пакета и текстовый gate: существующий переход `no_answer → gate-text`, передача generation в `gate parse`, отмена и непонятый ответ. Не создавать альтернативный парсер согласований.
- [ ] Запустить новый модуль, config, task, gates и соответствующие проверки machine/brief. После анализа графа закоммитить компонент.

### Task 3: Роли и сценарии обеих платформ

**Files:** создать `scripts/memoforge/host_dispatch.py`, `platforms/codex/skills/{memo,continue,status,style,brief}/SKILL.md`, `platforms/zcode/skills/{memo,continue,status,style,brief}/SKILL.md`, `platforms/{codex,zcode}/router.md`, `platforms/{codex,zcode}/tooling-core.md`; изменить `scripts/memoforge/dispatch.py`; создать `scripts/tests/test_host_dispatch.py`, `scripts/tests/test_platform_instructions.py`.

**Interfaces:** `adapt_agent(payload: dict, profile: HostProfile, root: Path) -> dict` вызывается в `render_agents` после общего построения задания. Для Claude возвращает исходный payload. Для новых профилей добавляет системный текст роли и путь к адаптированным общим правилам, убирает принудительную модель Claude; `slot`, `agent`, `description`, `expected_outputs` сохраняются. Codex использует доступный инструмент подагентов, ZCode — нативный `Agent` и namespace своего пакета.

- [ ] Написать тесты: `test_claude_payload_is_unchanged`, `test_native_prompt_includes_role_and_task`, `test_native_payload_does_not_force_claude_model`, `test_parallel_slots_preserve_identity`, `test_missing_agent_output_is_a_failed_slot`. Сравнить Claude с существующим эталоном; проверить роль legal-researcher, динамический prompt и все staging paths.

```python
def test_claude_payload_is_unchanged(self):
    payload = {"slot": "logic", "model": "opus", "prompt": "original"}
    with patch.dict(os.environ, {"MEMOFORGE_PLATFORM": "claude"}):
        result = adapt_agent(payload, get_profile(PLUGIN_ROOT), PLUGIN_ROOT)
    self.assertEqual(result, payload)
```

- [ ] Запустить новый модуль и получить FAIL.
- [ ] Реализовать адаптацию без массовой замены текста. При сборке нативных `agents/*.md` сохранить тело роли, исключить Claude model/effort и адаптировать только платформенные разрешения инструментов.
- [ ] Написать пять русских сценариев для каждого хоста. Router явно разрешает делегирование; запускает все слоты параллельной группы до ожидания; проверяет файл результата и сообщает каждый слот CLI. Если инструмента нет или результат пуст, сообщает fail, а не имитирует успех.
- [ ] Сохранить общий контракт script/inline-llm, schema-проверку, retry, cancellation и terminal. Использовать текстовые gates; Cowork Artifact не требовать. Не менять общие правила достоверности источников.
- [ ] Проверить сценарии style и brief отдельно: собственный запуск роли, наследуемая модель, неизменность исходного меморандума и продолжение brief. Запустить новые тесты, test_dispatch, test_agent_core_inclusion, test_brief и тесты машины. Закоммитить после графа.

### Task 4: Самостоятельные сборки

**Files:** создать `scripts/build_platforms.py`, `platforms/codex/.codex-plugin/plugin.json`, `platforms/zcode/.zcode-plugin/plugin.json`, `platforms/{codex,zcode}/README.md`, `scripts/tests/test_platform_packages.py`; обновить версии в исходных манифестах, README.md/README.en.md и CHANGELOG.md до 2.1.0.

**Interfaces:** `build_package(platform: str, destination: Path) -> Path`; `validate_package(root: Path) -> list[str]`; CLI `python scripts/build_platforms.py --platform codex|zcode|all --out dist`. Выход: `dist/memoforge-<platform>/` и `dist/memoforge-<platform>-2.1.0.zip`.

- [ ] Написать тесты `test_package_runs_outside_checkout`, `test_paths_with_spaces_and_cyrillic`, `test_zip_preserves_launcher_mode`, `test_missing_schema_is_rejected`, `test_package_excludes_private_files`, `test_versions_agree`. Assertions: CLI --version = 2.1.0, все ресурсы существуют, ZIP не содержит options.json/auth/config личного хоста, outputs/.data/.git/__pycache__ отсутствуют.

```python
def test_package_excludes_private_files(self):
    with tempfile.TemporaryDirectory(prefix="Проба с пробелами ") as tmp:
        root = build_package("codex", Path(tmp))
        self.assertEqual(validate_package(root), [])
        for name in (".git", ".data", "outputs", ".claude-plugin"):
            self.assertFalse((root / name).exists(), name)
```

- [ ] Запустить новый модуль и подтвердить FAIL.
- [ ] Создать сборщик с явным перечнем общих runtime-каталогов. Включить `scripts/memoforge`, launchers, `schemas`, `lib`, `templates`, runtime-файлы `hooks`, requirements и LICENSE; для пробных прогонов включить нужные offline-фикстуры. Адаптер перекрывает только свой router/tooling и входные сценарии.
- [ ] Добавить standalone version/profile metadata. Для ZCode создать нативные agents и пять команд входа. Для Codex — skills с разрешённым frontmatter. Все ссылки разрешать относительно корня пакета, запрещать выход через `..` и symlink.
- [ ] Создать отдельные marketplace roots внутри `dist/marketplaces/{codex,zcode}` с пакетами и каталогами соответствующего формата. Не редактировать личные marketplace-файлы вручную.
- [ ] Собрать обе версии, распаковать во временные внешние папки, проверить CLI, defaults, `probe dry-run` и русский DOCX. Fixtures означают offline-проверку; это не доказательство работы модели или MCP. Закоммитить исходники, не dist, после графа.

### Task 5: Установка и настоящие запуски

**Files:** создать `scripts/install_platform.py`, `scripts/verify_platform_runtime.py`, `scripts/tests/test_platform_install.py`; дополнить русские README пакетов. Отчёт: игнорируемый `outputs/platform-validation/README.md`.

**Interfaces:** `install_platform(platform: str, marketplace_root: Path) -> dict` возвращает `ok: bool`, `plugin: str`, `errors: list[str]`; неизвестная платформа даёт ValueError `invalid_platform`. `verify_runtime(platform: str, work_dir: Path, timeout_seconds: int = 300) -> dict`. Отчёт содержит host_version, installed_plugin, loaded_skills, task_path, dispatch_result, gate_result, export_path, errors; отсутствие доказательства — отдельный незавершённый пункт, а не true.

- [ ] Написать тесты установки на подставном CLI: `test_reinstall_preserves_other_plugins`, `test_cli_failure_is_not_success`, `test_timeout_keeps_diagnostics`, `test_readback_must_match_plugin`. Проверить argv без shell-интерполяции; повтор не удаляет чужой плагин; неверный readback означает ошибку.

```python
def test_unknown_platform_cannot_install(self):
    with tempfile.TemporaryDirectory() as tmp:
        with patch("subprocess.run") as runner:
            with self.assertRaisesRegex(ValueError, "invalid_platform"):
                install_platform("missing", Path(tmp))
            runner.assert_not_called()
```

- [ ] Запустить модуль и получить FAIL.
- [ ] Реализовать установку через штатные команды. Codex: `plugin marketplace add`, затем `plugin add memoforge-codex@memoforge-codex-local --json`, readback `plugin list`. ZCode: validate пакета, добавление marketplace и install через его `plugins`; использовать фактически подтверждённый синтаксис текущего CLI, сохранённый в README. Секреты не читать в вывод и не копировать между хостами.
- [ ] Установить обе сборки на этом Mac. В новой сессии каждого хоста проверить обнаружение всех пяти сценариев и настоящую загрузку системных инструкций. Подтвердить имя установленного пакета и разрешённые пути его ресурсов.
- [ ] Через headless Codex/ZCode в отдельных рабочих папках выполнить короткую задачу с заведомо достаточными фактами до первого gate. Независимо проверить созданное состояние, runtime_platform, русский текст и сохранённый ответ аналитика. Ответить на gate по тому же протоколу и проверить продолжение.
- [ ] В отдельном запуске проверить native dispatch двух безопасных слотов без внешнего исследования: оба staging-файла должны существовать, иметь корректный JSON и пройти report. Для ошибки одного слота проверить повтор и сохранность второго.
- [ ] Проверить русский DOCX по содержимому document.xml и архиву OOXML. Короткое реальное исследование выполнить только при доступной авторизации MCP; недоступность записать с точным сервисом. Не объявлять offline-экспорт доказательством внешнего исследования. Лимит каждого пробного процесса 300 секунд; при завершении сохранить диагностический итог без секретов.
- [ ] Закоммитить установщик, проверки и инструкции после тестов и графа. Пользовательские кэши и отчёты оставить локальными.

### Task 6: Общая регрессия и публикация результата

**Files:** обновить README.md, README.en.md, docs/ru/README.md, документацию профилей и `.github/workflows/ci.yml`; дополнить `scripts/ci/check_versions.py` проверкой манифестов адаптеров. Добавить CI-проверку сборки обоих пакетов и их offline-прогона.

- [ ] Добавить тест `test_adapter_version_mismatch_fails` в `scripts/tests/test_check_versions.py`, получить FAIL, затем подключить манифесты к guard. Обновить автоматически генерируемые справочники только через renderer при необходимом изменении их источников; сверить русский перевод.
- [ ] Выполнить на текущем дереве: `python -m unittest discover -s scripts/tests -v`, guard `check_no_skips`, compileall, `ruff check --select E,F,W scripts hooks`, `mf docs render --check`, `check_versions`, оба package dry-run, проверку локальных Markdown-ссылок и `git diff --check`.
- [ ] Самостоятельно проверить diff по пяти пунктам Review Focus. Получить независимое итоговое ревью готового patch через доступный инструмент code review; исправить подтверждённые дефекты и повторить затронутые проверки.
- [ ] Перед коммитом выполнить graph detect_changes; перед отправкой проверить Git remote и состав коммитов. Опубликовать только файлы этой работы, проверить SHA удалённой ветки после push. Повторно обновить индекс GitNexus.
- [ ] Открыть русские README и передать ссылки на два ZIP. Итоговый ответ отдельно сообщает: создано/установлено, реально проверено каждым хостом, что проверено с фикстурами, и оставшиеся внешние ограничения. Если установка или native dispatch не доказаны, не считать соответствующую версию готовой.

## Самопроверка плана

Профиль/данные/launcher/version покрывает задача 1; настройки и сохранённые задачи — 2;
роли и протокол — 3; самостоятельность, MCP и упаковка — 4; установка и live-проверки — 5;
документация, общая регрессия и публикация — 6. Все пять Review Focus имеют назначенные
тесты. Подписи новых функций определены в Interfaces; существующий интерфейс
`resolve_config` сохраняется. Новых независимых подсистем вне спецификации нет.

## Текущие первичные источники

- [OpenAI: упаковка и локальная установка](https://developers.openai.com/plugins/build/plugins).
- [ZCode: структура и установка плагинов](https://zcode.z.ai/en/docs/plugin).
- Проверенные локальные help: `codex plugin add`, `codex plugin marketplace add`,
  `codex plugin list`, `zcode.cjs --help`; ZCode `plugins` подтвердил действующий registry.

Следующий этап — проверка этого плана пользователем и выбор способа исполнения.
Этим документом не подтверждаются создание, установка или работа новых сборок.
