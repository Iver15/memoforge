# Режим работы (v2)

[Указатель](README.md) · [Оригинал](../modes.md). Перевод документации v2.0.0.

Источник — `scripts/memoforge/modes.py` (ТЗ §2.3). Приоритет настройки `source_review_gate`: явно заданное `userConfig on|off` → режим → `auto`.

| Поле | full |
|---|---|
| `researcher_layers` | `statutes`, `case_law`, `doctrine` |
| `reviewer_list` | `logic`, `form`, `citations`, `counterarguments` |
| `max_iterations` | `2` |
| `client_polish_enabled` | true |
| `max_client_polish` | `1` |
| `template_id` | `classical-memo` |
| `source_review_gate` | `auto` |
| `lint_fix_rounds` | `2` |
| `intake_max_questions` | `10` |
| `mcp_budget` | casus: 20, courtlistener: 40, fas: 12, fedregs: 40, justicelibre: 40, ldh: 10, legalviz: 40, lex: 40, opencaselaw: 40, uklegal: 40 |

Модель автора по умолчанию: `opus`. Допустимые значения: `opus`, `fable`, `sonnet` (§4.1).

Оформление ссылок по умолчанию: `inline`. Допустимые значения: `footnotes`, `inline` (§5.5, D-150/D-152). `inline` помещает в текст короткую ссылку в скобках с переходом к источнику, а полную запись — в приложение «Источники». `footnotes` создаёт сноску Word для каждого цитирования. Выбор для одной задачи: `mf task new --option citation_style=footnotes`. Также можно задать его в метаданных самого шаблона.
