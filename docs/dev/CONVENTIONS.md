# memoforge v2 — конвенции кода (для всех писателей пакета `scripts/memoforge`)

Оракул — `../../TZ-memoforge-v2.md` (v1.5, согласована). Этот файл только фиксирует соглашения, чтобы параллельно написанные модули стыковались. При конфликте с ТЗ прав ТЗ; сообщи оркестратору.

## Пакет и запуск
- Пакет: `scripts/memoforge/` (Python ≥3.9, stdlib + `jsonschema`, `python-docx`, `mistune`; optional `rapidfuzz`). `from __future__ import annotations` в каждом модуле; типы `list[str] | None` только в аннотациях.
- Entry-point: `python <root>/scripts/memoforge/__main__.py <group> <cmd> [...]`; обёртки `scripts/mf` (bash) и `scripts/mf.cmd` делают launcher discovery (ТЗ §5.6) и запускают `__main__.py`. Внутри пакета — только относительные импорты (`from . import state_io`).
- `cli.py`: argparse с subparsers; каждый модуль экспортирует `register(subparsers)` и функции `run_<cmd>(args) -> dict`. `__main__.py` вызывает `cli.main(argv)`.
- **Вывод**: подкоманда печатает ровно один JSON-объект в stdout (`json.dumps(result, ensure_ascii=False)`), при `--human` — текст. Диагностика — только в stderr. Exit codes: `0` ок, `1` невалидно/бизнес-ошибка (в JSON есть `"errors": [...]`), `2` usage/ошибка CLI.
- Кодировки: чтение JSON через `state_io.read_json(path)` (utf-8-sig), запись через `state_io.write_json_atomic(path, obj)` (tmp + `os.replace`, ретрай ×5 на `PermissionError`/`WinError 32`). В `__main__` — `sys.stdout.reconfigure(encoding="utf-8")` и то же для stderr.
- Пути в state — POSIX-строки относительно `work_dir`, кроме `work_dir` и `output_folder` (абсолютные, платформенные). Внутри кода — `pathlib.Path`.

## Общие модули (владелец — слайс S1; остальные только импортируют)
- `phases.py`: `PHASES: list[str]` (порядок §2.1), `TERMINAL`, `GATES`, `is_gate(phase)`, `is_terminal(phase)`.
- `modes.py`: `MODES: dict[str, dict]` (матрица §2.3), `resolve_config(mode, user_config) -> dict`.
- `limits.py`: все числа ТЗ как константы `UPPER_SNAKE` с комментарием-ссылкой на §.
- `events.py`: `EVENT_TYPES: set[str]`, `append_event(work_dir, event: str, actor: str, data: dict, *, event_key: str | None = None, phase=None, step_id=None)` — единственный writer журнала (протокол §7.2 C/C′ под `events.lock`), `read_events(work_dir) -> list[dict]` с дедупликацией по `event_key` и пропуском невалидных строк.
- `fallbacks.py`: `FALLBACKS: list[dict]` — таблица деградаций (условие-ключ → действие → banner_id → текст баннера).
- `state_io.py`: `FileLock(path, timeout=LOCK_TIMEOUT)` контекст-менеджер (§2.2: `fcntl.flock` / `msvcrt.locking` первого байта после `seek(0)`; lock-файлы постоянные; проверка порядка `state → sources → events` через thread-local стек, нарушение → `LockOrderViolation`); `read_state(work_dir)`, `write_state(work_dir, mutator: Callable[[dict], None])` (под `state.lock`, валидация `schemas/state.schema.json` до `os.replace`, событие `state_written`), `read_json`, `write_json_atomic`, `sha256_file`.
- `schema.py`: `validate(obj, schema_name) -> list[str]` (ошибки; пустой список = валидно) поверх `jsonschema.Draft202012Validator`; схемы грузятся из `<root>/schemas/<name>.schema.json`, `$ref` — только локальные `$defs`.
- `pylauncher.py`: `plugin_root()`, `plugin_data_dir()` (цепочка §2.5), `python_cmd()`.
- `hooks_common.py`: `find_active_task(cwd) -> Path | None` (по `CLAUDE_PROJECT_DIR`/cwd → `state.json` со `schema_version == 2` и нетерминальной фазой), stdlib only, без импорта `state_io` (хуки не пишут state).
- `task.py`: `run_new`, `run_resolve`, `run_list`, `run_cancel` (§2.5).

## Шаги и identity (§2.2, §3.1)
- Каждая изменяющая команда принимает `--workdir`, `--step`, `--attempt`; проверяет identity через `machine.check_identity(state, step, attempt)` → при уже закрытом шаге возвращает сохранённый `result_ref` (no-op), при несовпадении — `{"errors":["identity_mismatch"]}`, exit 1.
- Рабочий каталог попытки: `steps/<step_id>/a<attempt>/<slot>/`; для CLI-команд slot = `cli`, для inline-llm = `orchestrator`. Канонические файлы создаёт только публикация (`machine.publish`), которая обновляет `state.published[]`.
- Маркер завершения агента: `steps/<step>/a<n>/<slot>/done.json` (схема `done-marker`).

## Тесты
- `scripts/tests/test_<module>.py`, `unittest`, временные каталоги через `tempfile.TemporaryDirectory()`; никаких сетевых вызовов; фикстуры в `scripts/tests/fixtures/`. Запуск: `python -m unittest discover -s scripts/tests -v`. Старые тесты v1 остаются зелёными до волны W4.
- Тест обязан падать без реализации (не писать тавтологии). Названия тестов из ТЗ §9 использовать буквально.

## Стиль
- Без внешних зависимостей сверх перечисленных; без `print` вне CLI-слоя; без `os.system`; без `python3`-хардкодов; никаких `HARD RULE`-комментариев; docstring одной строкой на модуль и на публичную функцию.
- Не трогать файлы вне своего слайса. Вернуть оркестратору: список изменённых файлов + по каждому пункту своего § — как выполнен + вывод тестов дословно.

## Канонические имена секций драфта (для `lint.py` L-12/L-06 и шаблонов; источник — `templates/*.md`)
- `## 1. Executive summary` · `## 2. Background and definitions` (опционально; при пропуске нумерация сдвигается) · `## 3. Facts, assumptions and limitations` · аналитические `## N. <noun phrase>` с подсекциями `### N.M. <noun phrase>` · `## N. Conclusion and recommendations` (executive-brief: `## N. Recommendations`) · последняя строка драфта — маркер `<!-- sources: generated -->`. Якоря секций: `<!-- §s-N-M -->` сразу после заголовка (ставит `mf draft anchor`). Сравнение имён — без учёта регистра и завершающей пунктуации.
- Идентификаторы чеклистов: `LOG-nn`, `FRM-nn`, `CIT-nn`, `CTR-nn`, `CRD-nn` (`lib/checklists/*.json`).
