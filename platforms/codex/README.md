# Memoforge для Codex

Самостоятельный русский пакет 2.1.0. Общее ядро, отдельные данные и сценарии.

Сценарии: memo — новый меморандум; continue — продолжение и согласования; status — статус; style — профиль стиля; brief — краткая записка из готового меморандума.

Модель выбирается в Codex, writer_model=inherit. По умолчанию memo_language=ru, ui_language=ru, dashboard=false. Юрисдикция задаётся вопросом, русский язык не ограничивает её Россией.

Данные: ~/.codex/plugin-data/memoforge-codex; в Windows — LOCALAPPDATA/codex/plugin-data/memoforge-codex. Задачи сохраняются отдельно от кэша плагина. Другую платформу нельзя использовать для продолжения уже начатой задачи.

Включены десять MCP-сервисов. Установка не обеспечивает их авторизацию; недоступные источники отражаются как пробелы исследования.

После установки: `scripts/mf deps install`, затем `scripts/mf deps check`. Подробный [протокол](router.md), [сценарии](skills/memo/SKILL.md). MIT, оригинальный автор Grigorii Moskalev.
