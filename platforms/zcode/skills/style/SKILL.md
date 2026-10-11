---
name: style
description: Создать, выбрать и проверить профиль стиля юридических меморандумов.
---

# Memoforge zcode — style

Прочитай [протокол](../../router.md). Действия: list→`mf style list`; use NAME→set-default NAME; use --clear→clear-default; show NAME→validate-profile NAME, read-meta NAME, resolve-paths NAME и чтение prose-style.md/template.md; delete NAME→delete NAME. Пустые аргументы — покажи действия и жди выбора.
Для new NAME с examples и/или rules сначала validate-name NAME и list (не перезаписывай существующий). Проверь доступность входов, поддержаны md/txt/pdf/docx. Создай временную рабочую папку. Запусти ОДНОГО style-extractor нативным инструментом подагентов, без model, с полным телом `agents/style-extractor.md` и заданием: profile_name, абсолютные examples, rules (текст/путь), input_type examples|rules|both, work_dir и абсолютный mf. Роль выполняет init-profile, пишет prose-style.md, template.md при структурном входе, rules.md и sources/, завершает write-meta атомарно. После ожидания `mf style validate-profile NAME`; при ошибке покажи её, не объявляй профиль готовым и не устанавливай default. Успешный профиль выбирается только по запросу use. Роль не должна выдумывать содержание примеров.
