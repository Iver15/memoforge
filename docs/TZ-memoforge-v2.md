# ТЗ: memoforge v2.0 — редизайн мультиагентного пайплайна юридических меморандумов

**Версия:** 1.5 (2026-09-08; само-adversarial раунд 0 — 70 находок; Codex раунды 1–5: FLAWED 35 → FLAWED 15 → FLAWED 6 → FLAWED 4 → **SOUND-WITH-CHANGES, 1 нит** — внесён) · **Статус:** СОГЛАСОВАНА — готова к реализации по волнам §12
**Заказ владельца:** «тщательно проанализировать пайплайн, исследовать, что можно поменять/оптимизировать/улучшить, подготовить подробное ТЗ, провалидировать с Codex» + «посмотреть, появились ли более лёгкие/нативные способы отображать движение пайплайна».
**Базлайн:** `github.com/gregmos/memoforge`, коммит `69a45b5` (v1.1.1, 2026-05-29). Валидация базлайна: `python -m unittest discover -s scripts/tests` → `Ran 171 tests … OK` (Windows 11, Python 3.13, python-docx 1.2.0). `pytest` не используется.
**Входные материалы:** `analysis/01-orchestration-audit.md`, `02-agents-audit.md`, `03-scripts-audit.md`, `04-platform-research.md` (раздел «Верификация» — проверенные по докам факты платформы), `05-best-practices-research.md`, `06-progress-display-research.md`, `07-spec-adversarial-r0.md` (adversarial-ревью v0.9). Все `file:line` ниже относятся к базлайну; критические перепроверены автором и adversarial-ридером (помечены ✓).
**Назначение:** единый оракул для реализации v2.0 и для ревью. Код обязан совпадать с ТЗ; расхождение = либо баг реализации, либо отдельное решение о правке ТЗ с новой версией и строкой диспозиции.

---

## §0. Рамка

### 0.1. Диагноз (почему редизайн, а не патч)

1. **Оркестратор — LLM, читающий за Full-прогон ≈70–110k токенов инструкций** (метод базлайнового аудита `bytes/4` даёт 95–110k; контрольный `wc -w` ≈54k слов ≈70–75k; 3 резидентных файла, 17 файлов фаз, 12 demand-контрактов ≈40k из которых 8–10 читаются практически всегда, `continue/SKILL.md` ≈14k) и выполняющий ≈250–300 «служебных» tool-вызовов ради ≈20 содержательных диспатчей (01 §1.2–1.3). ≈165 императивных вхождений (`MUST|MANDATORY|HARD RULE|NEVER|Do NOT|do not`), из них строго MUST/NEVER/HARD RULE ≈73.
2. **12 задокументированных инцидентов (01 §4.1): 9 из 12 — пропуск LLM бухгалтерского шага → усиление прозы → следующий пропуск; 3 — дефекты инфраструктуры** (хук с `${CLAUDE_PLUGIN_ROOT}` на Windows, matcher `^WebFetch$`, ложноположительная проба v0.2.0) — они закрываются §8, а не §3. Автор сам зафиксировал предел прозы при удалении Lessons Studio: «any future re-introduction needs a non-prompt-resident enforcement mechanism» (`CHANGELOG.md:81` ✓).
3. **Платформа ушла из-под ног** (04 §Верификация, 06 §1): Cowork Live artifacts отключены 19.08.2026 → весь live-progress-стек (`mcp__cowork__create_artifact/update_artifact`, `render_live_progress.py`, 15 секций «Live progress» в агентах) мёртв; `TodoWrite` не выдаётся на Sonnet 5 / Fable 5 без `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`; `visualize` MCP нестабилен; ограничение «субагенты не спавнят субагентов» снято (глубина 3); в плагине можно поставлять Workflow-скрипты и `subagentStatusLine`.
4. **Подтверждённые P0 в базлайне:** `scripts/validate_state.py:39-57` ✓ не знает фазу `research_sufficiency_followup_pending` (валидатор отвергает корректный state; `test_phase_machine_coverage.py:58` ✓ маскирует); `python3` на типовой Windows — заглушка Microsoft Store → все 4 хука и 40+ вызовов молча не работают (03 §2); обязательная verbatim-цитата ≤30 слов у писателя (`agents/memo-writer.md:183,249` ✓) при бюджете ≤15 слов у исследователей (`agents/statutory-researcher.md:157` ✓) и опциональном raw-слое (`:164` ✓) — структурное давление к галлюцинации цитат; нет сквозного `source_id` (`agents/currency-checker.md:89,107` ✓ «identifier you assign», `agents/source-pack-builder.md:67` ✓ join «by slug or title»); failure-stub ревьюера пишет `overall_score: 0` (`scripts/validate_review_json.py:84` ✓) и обваливает среднее → ложный regression revert.
5. **Мёртвые фичи:** «пропорциональный follow-up» v1.1.0 — поле `reply_scope` читается в `phase-6.md:95` и `continue/SKILL.md:437` ✓ и не пишется нигде; `drafting_warnings[]` не имеет владельца в state и не читается писателем (01 C12–C13); docx без сносок вопреки `README.md:19,33` ✓ (03 §4).
6. **Реальность MCP не отражена в контрактах** (05 §5, эмпирика 9 живых вызовов): Legal Data Hunter Free — 10 запросов/мин, **20/день**, 600/30 дней; CourtListener — 5/мин, 50/час, **125/день**; код страны `GB` возвращает пустой список без ошибки (нужен `UK`); лаг индекса EUR-Lex ≈1 месяц; `resolve_reference` возвращает весь акт (GDPR = 358 КБ), не статью; eCFR/FederalRegister блокируют WebFetch; BAILII запрещает автоматический доступ.
7. **Ревью-луп опирается на шумный сигнал** (05 §2): 5 персон-ревьюеров, холистический `overall_score` без рубрики, плато при Δ<1.0 — внутри flip-rate LLM-судьи (≈13.6%); исследования показывают выигрыш от бинарных чеклистов с детерминированной агрегацией и почти нулевой прирост после 1–2 итераций.

### 0.2. Цели v2.0 (измеримые; колонка «Как измеряется» обязательна для приёмки)

| # | Метрика | Базлайн v1.1.1 | Цель v2.0 | Как измеряется |
|---|---|---|---|---|
| G1 | Инструкций, полученных оркестратором за Full-прогон по всем каналам (файлы `Read` + тексты, выданные `next`: промпты диспатча, тексты гейтов, `chat_line`) | ≈95–110k токенов (`bytes/4`, только файлы) | **≤25k** | `bytes/4`; v1 — по `Read`-вызовам реального прогона 2026-05-29 с повторами; v2 — `mf probe metrics` суммирует байты всех ответов `next` и всех `Read` оркестратора за **реальный** Full-прогон (dry-run даёт только нижнюю оценку и так и помечается) |
| G2 | Обращений оркестратора к инструментам за Full-прогон | ≈250–300 | смоделировано (dry-run): `next` ≤60, `report` ≤60, `Agent` ≤25, Bash-скриптов ≤20, гейтов ≤5 — колонка dry-run описывает **чистый маршрут** (без раунда lint-fix и без дополнительной итерации ревизии), а не верхнюю границу реального прогона; **наблюдаемая метрика** (реальный прогон): `cli_call`+`step_issued` ≤150 — только вызовы `mf` и диспатчи, которые CLI видит сам; прочие обращения оркестратора (например, `Read`) плагину не наблюдаемы и в метрику не входят | dry-run — из `steps[]`; реальный прогон — `events analyze` по `cli_call`/`step_issued` (включая no-op повторы); `probe dry-run` печатает оценку наблюдаемой метрики `g2_observed_estimate` (сам `cli_call` в dry-run не эмитируется) |
| G3 | Записей `state.json` не через CLI | все | **0 — архитектурное требование (M2)**; проверка выборочная | `test_no_state_write_in_prompts.py` (grep) + выборочная сверка sha `state.json` с событиями `state_written` на реальном прогоне; полный tool-trace плагину недоступен — результат помечается `sampled`, не «доказанный ноль» |
| G4 | JSON-артефактов задачи, покрытых схемой (таблица §6) | 3 из 12 | **все строки таблицы §6** | `test_schemas.py` + `test_expected_outputs_have_schema.py` (каждый `expected_output` в `dispatch.py` сопоставлен схеме или lint) |
| G5 | Blockquote в драфте без `quote_id` из реестра | не проверяется | **0** (lint-блокер) | `test_lint.py::L08` + аудит цитат |
| G6 | Агентов / ревьюеров на итерацию / макс. итераций (Full) | 16 / 5 / 3 | **12 / 4 / 2** | `ls agents`, `modes.py` |
| G7 | Wall-clock Full-прогона (тот же вопрос, что 2026-05-29: 1h58m) | 1.5–4 ч | **≤60 мин — ориентир, не критерий приёмки** (зависит от MCP) | `events analyze` на реальном прогоне; фиксируется в `docs/probes/` |
| G8 | Действий оркестратора ради прогресса на шаг | 10–12 | **1** (печать `chat_line`); 2 при `dashboard: on`. `agent log --state step` субагентов — best-effort телеметрия, в G8 не входит (`start`/`done` — completion-контракт, не прогресс) | §7, `events analyze` |
| G9 | Тестов; CI | 171, нет CI | ≥250; GitHub Actions ubuntu+windows; 0 skipped при установленных зависимостях | CI |
| G10 | Docx: настоящие сноски, Sources из реестра | нет | да | golden-тесты + `docx validate` |

### 0.3. Non-goals (не входит и не «чинится заодно»)

- Новые юрисдикции/домены права, новые MCP-провайдеры (кроме безключевых публичных API из таблицы роутинга §4.3), многоязычный вывод (memo остаётся English-only).
- Возврат Lessons Studio (cross-run learning). База для него появляется (`events.jsonl`, `finalize`, хуки) — фиксируется в `docs/decisions.md` как «разблокировано, отложено».
- **Логика извлечения стиль-профиля** (`agents/style-extractor.md`, формат профиля `prose-style.md`/`template.md`/`meta.json`) не меняется. Меняются: точка выбора профиля (§4.6), CLI-обёртка и пути (§5.1), состав агентов, читающих профиль (§4.2).
- Полный переход оркестрации на Workflow tool. В v2.0 — только совместимость границ сегментов (§3.2) и пилот за флагом (§10).
- Interactive dashboard как обязательная функция (§7.5 — опционально, после пробы P3).
- Agent teams, cross-session messaging, Managed Agents, Agent SDK-переписывание плагина.
- «Accept as-is» после forced exit перед export; асинхронный source-review; `counterargument-reviewer` на Fable — на будущее (07 out-of-scope notes).

### 0.3a. Принцип минимализма (требование владельца: «без оверинжиниринга»)

Каждый механизм ТЗ обязан закрывать конкретный инцидент из 01 §4.1, P0/P1 из аудитов или цель G1–G10; иначе он вырезан или помечен «опционально, за флагом». Сознательно **не** делаем в v2.0: собственный JSON-Schema-валидатор (берём pip `jsonschema`); HTTP-клиенты к CELLAR SPARQL, legislation.gov.uk, GovInfo, CourtListener REST (агент работает через уже подключённые MCP и WebFetch; CLI знает только предпочтительные домены); deny-хук бюджета MCP (достаточно счётчика и вопроса на гейте); словарные lint-эвристики для «noun-phrase заголовков» и «action-verb + owner» (это остаётся пунктами чеклиста form-reviewer); отдельные md-виды для всего подряд (рендерим только то, что читает человек или агент); Workflow-пилот, Artifact-дашборд, Stop-guard, `subagentStatusLine` — опциональны и включаются только после проб. Оценка объёма пакета `scripts/memoforge/`: ≈3–3.5k строк Python + ≈2.5k строк тестов (против ≈3.5k строк скриптов и ≈2.9k тестов в базлайне) при удалении ≈9k строк markdown-инструкций.

### 0.4. Затронутые файлы (закрытый список; всё остальное — только по явному требованию ТЗ)

**Удаляются целиком (волна W1/W4, вместе с их субъектами):** `scripts/render_live_progress.py`, `scripts/resolve_work_dir.sh`, `scripts/validate_state.py`, `scripts/validate_review_json.py`, `scripts/log_event.py`, `scripts/analyze_run.py`, `scripts/tidy_workdir.py`, `scripts/resolve_style_profile.py` (все замещаются пакетом §5); `scripts/tests/{test_render_live_progress,test_validate_state,test_validate_review_json,test_log_event,test_analyze_run,test_tidy_workdir,test_resolve_style_profile,test_md_to_docx_banner,test_phase_machine_coverage}.py` (переносятся в тесты пакета); `skills/memo/references/{live-progress-contract,widget-schemas,progress-tracker,progress-contract,logging-contract,mcp-ratelimit-contract}.md`; `skills/memo/references/phases/*.md` (17 файлов); `skills/memo/PHASE-MACHINE.md`; `agents/statutory-researcher.md`, `agents/case-law-researcher.md`, `agents/doctrinal-researcher.md` (сливаются в `legal-researcher.md`); `agents/clarity-reviewer.md`, `agents/style-reviewer.md` (сливаются в `form-reviewer.md`); `agents/source-pack-builder.md` (становится скриптом). Итого агентов: 16 − 6 + 2 = 12.

**Архивируются в `docs/attic/` (исключены из grep-тестов):** `templates/research-summary-only.md`, `docs/postmortems/*`, `docs/probes/v0.5.0-probe-procedure.md` (методика 25-секундных пауз остаётся ссылкой из §11).

**Переписываются:** `skills/memo/SKILL.md`, `skills/continue/SKILL.md`, `skills/status/SKILL.md`, `skills/style/SKILL.md` (11 вызовов `python3 …/resolve_style_profile.py` `:69–300` → `mf style …`; логика не меняется), `skills/memo/state-schema.md` → `schemas/state.schema.json` + `docs/state.md`, `skills/memo/references/{operating-contract,pipeline-contract,events-contract,always-deliver,modes,INDEX}.md` → `docs/` (генерируемые/нормативные тексты, не читаемые оркестратором; `always-deliver` — генерируется из `fallbacks.py`), все оставшиеся `agents/*.md`, `lib/prose-style.md`, `lib/revision-loop.md`, `templates/classical-memo.md`, `templates/executive-brief.md`, `lib/docx-render/scripts/md_to_docx.py` (→ `scripts/memoforge/docx/`), `lib/docx-render/README.md`, `hooks/hooks.json`, `.mcp.json` (без изменений содержимого; фиксируются имена namespace), `.claude-plugin/plugin.json`, `README.md`, `CHANGELOG.md`, `.gitignore`, `scripts/tests/README.md`.

**Создаются:** `scripts/memoforge/` (пакет, §5), `scripts/mf`, `scripts/mf.cmd`, `schemas/*.schema.json` (§6), `lib/agent-core/*.md` (§4.2), `lib/checklists/*.json` (§4.5), `lib/ai-tells.txt` (§5.4), `lib/routing/discover-cache.json` (§4.3), `lib/models.md`, `agents/legal-researcher.md`, `agents/form-reviewer.md`, `hooks/permission_gate.py`, `hooks/progress_logger.py`, `hooks/stop_guard.py`, `hooks/ensure_deps.py`, `hooks/allowlist.txt`, `hooks/build_hooks.py`, `settings.json` (плагинный, только `subagentStatusLine`), `scripts/subagent_statusline.py`, `requirements.txt`, `.gitattributes`, `.github/workflows/ci.yml`, `docs/probes/v2-probes.md`, `docs/decisions.md`, `.claude-plugin/marketplace.json` (D-73), `docs/TZ-memoforge-v2.md` (D-76), `workflows/` (только при включённом пилоте, §10).

### 0.5. Инварианты v2 (M-слой; каждый — проверяемое утверждение)

- **M1 — Код держит инварианты, не память модели.** Переходы фаз, счётчики, бюджеты попыток, выбор списка агентов, эмиссия событий, атомарная запись state — только в `memoforge` CLI. Оркестратор-LLM не вычисляет ничего из этого.
- **M2 — Единственный писатель `state.json` — CLI** (`state_io.write` под файловым локом `state.lock`, tmp + `os.replace`, валидация по `schemas/state.schema.json` ДО замены). Ни агенты, ни хуки не пишут `state.json`; хуки только дописывают `events.jsonl`.
- **M3 — Оркестратор работает по протоколу `next → act → report`** (§3.1); `next` и `report` идемпотентны, а каждая изменяющая команда CLI принимает `--step` и фиксирует своё действие и закрытие шага одной транзакционной записью state, поэтому забытый или повторённый вызов не портит state и не повторяет побочный эффект; после суммаризации контекста работа восстанавливается одним `next`. (Протокол живёт в промпте ≈150 строк — риск вытеснения снижается размером, идемпотентностью и Stop-guard §8.3, но не исчезает.)
- **M4 — Все машиночитаемые артефакты — JSON по схеме; markdown-виды рендерятся скриптом** из JSON; ни один промпт не просит агента писать `.md`-вид параллельно с JSON.
- **M5 — Провенанс цитаты (ограниченная гарантия):** каждая blockquote в драфте имеет `quote_id`, который скрипт извлёк **точным совпадением** из raw-текста источника, сохранённого исследователем из ответа инструмента, с фиксацией `raw_sha256` в записи цитаты; каждая inline-ссылка — `[[src:<source_id>]]` из реестра; Sources-секцию генерирует рендерер. Цепочка хэшей начинается с файла, сохранённого агентом: платформа не даёт CLI идентификатора tool-вызова, поэтому соответствие «raw ↔ URL» подтверждается только `liveness` (сравнение sha тела ответа, где URL доступен) и помечается `confirmed|agent_saved`.
- **M6 — Freeze источников:** `sources pack --freeze` одной операцией под обоими локами публикует snapshot (`source-pack.json` + список `source_id` с версиями raw) и ставит `sources_frozen`; после этого регистрация возвращает ошибку; lint, аудит и рендер читают только snapshot.
- **M7 — Прогресс пишет рантайм:** `events.jsonl` заполняют хуки и CLI; агент делает максимум один Bash-вызов `mf agent log` на содержательный шаг; ни один агент не рендерит HTML.
- **M8 — Гейты пользователя — только на границах сегментов** и только через один парсер `gates.py` (для text-гейтов — `mf gate parse`, для AUQ-гейта — `mf report --answers`, который вызывает тот же парсер). После любого `Agent`-диспатча в сессии `AskUserQuestion` не используется, кроме гейта плана (фаза 4), который защищён text-fallback'ом (§2.4) до подтверждения пробой P4.
- **M9 — Always-deliver (для управляемых завершений при доступном writable `work_dir`):** терминальная фаза (`done|failed|cancelled_by_user`) записывается только после того, как `mf finalize` создал `deliverable.{docx|md}` и `summary.md`; `mf finalize --salvage` работает без `jsonschema` и на повреждённом `state.json` (собирает summary из имеющихся файлов). Прерывание сессии — не терминал, а resumable-состояние; Stop-guard §8.3 — дополнительная, не обязательная защита. Legacy v1-задачи — вне жизненного цикла v2 (`unsupported`, без finalize).
- **M10 — Детерминированное раньше LLM:** lint, аудит цитат, агрегация вердиктов, выбор итерации выполняются скриптом до/вместо LLM-ревью; LLM-ревьюеры видят lint-clean драфт либо драфт с приложенным `lint.json` после исчерпания раундов.
- **M11 — Один источник истины для перечислений:** фазы, режимы, события, лимиты, таблица деградаций определены в `scripts/memoforge/{phases,modes,events,limits,fallbacks}.py`; markdown-документы генерируются `mf docs render` и проверяются тестами.
- **M12 — Windows-first совместимость:** launcher discovery (`python`→`python3`→`py -3`), `utf-8-sig` чтение, `reconfigure(utf-8)` вывода, LF в репо, ни одного bash-only скрипта на критическом пути, хуки в трёх exec-form вариантах (`python`/`python3`/`py -3`) с дедупликацией событий.

---

## §1. Целевая архитектура (обзор)

```
пользователь ──/memoforge:memo "<q>"──► SKILL.md (router, ≈150 строк)
                                          │  loop:
                                          │   a = mf next --workdir W            (JSON: что делать)
                                          │   выполнить a (Agent ×N параллельно | Bash | AskUserQuestion | print+END)
                                          │   mf report --workdir W --step <id> [--agent <slot> --status ok|fail] …
                                          ▼
                     scripts/memoforge (пакет, детерминированная state machine)
                     ├─ phases/modes/limits/events/fallbacks (источники истины)
                     ├─ state_io (lock + atomic + schema)   ├─ dispatch (планы диспатча + промпты из шаблонов)
                     ├─ gates (парсер ответов)              ├─ sources/quotes/routing (реестр, провенанс, роутинг MCP)
                     ├─ lint / citations / review / revision
                     ├─ docx (AST-рендер, сноски, OSCOLA)   └─ finalize / tidy / analyze / probe
                                          │
          агенты (12, JSON-выходы по schemas/) ◄──── промпты диспатча рендерит CLI
          hooks: permission_gate · progress_logger (только events.jsonl) · stop_guard · ensure_deps(check)
          прогресс: description у Agent-вызова · 1 строка в чат · subagentStatusLine · (опц.) Artifact
```

Ключевое отличие от v1: **оркестратор не знает пайплайн.** Он знает протокол из трёх команд. Всё знание о фазах, параллелизме, промптах, событиях и переходах — в Python-пакете с тестами. Это снижает класс инцидентов «LLM забыл шаг» (01 §4.1) до одного правила протокола, восстановление после суммаризации — один вызов.

---

## §2. Фазы и state machine

### 2.1. Перечень фаз v2 (`scripts/memoforge/phases.py`, единственный источник)

| # | `current_phase` | Тип шага (§3.1) | Кто работает | Исходы (первый совпавший) |
|---|---|---|---|---|
| 1 | `intake_preliminary_research` | inline-llm `mcp-probe` (записать доступные namespace легальных MCP в `intake/mcp-probe.json`; затем **ровно один дешёвый вызов на каждый подключённый сервер** — LegalViz `resolve`, UK Legal `legislation_search`, CourtListener `search`, LDH `discover_sources`, JusticeLibre `get_law_article`, OpenCaseLaw `get_law` — и результат в `status: {alias: ok\|quota\|auth\|error\|absent}`, D-147) → dispatch(1): `fact-assumption-analyst` | оркестратор, агент | `intake/questions.json` валиден → `intake_questions_pending`; невалиден после ретрая → `failed` |
| 2 | `intake_questions_pending` | **gate-text** | пользователь | `mf gate parse --gate intake` → `planning`; `cancel` → `cancelled_by_user`; ≥3 нераспознанных ответа → дефолты + баннер → `planning` |
| 3 | `planning` | inline-llm (классификация + `plan.json` по схеме §6) → script `mf sources preflight --step` (по одному представительному URL на каждый маршрутизируемый портал юрисдикций плана; `intake/preflight.json` по схеме `preflight`, статусы `ok\|waf_challenge\|cloudflare\|interstitial\|dead\|tls`; ошибка пробы — это статус, а не провал шага, D-147) | оркестратор, CLI | `plan.json` валиден → preflight → `plan_approval_pending`; невалиден после 2 ретраев → `failed` |
| 4 | `plan_approval_pending` | **gate-auq** (план + режим [+ стиль] [+ бюджет источников] в одном вызове; text-fallback §2.4) | пользователь | approve → `research`; edit → `planning` (`attempts.plan_edit` ≤5, затем forced approve последней версии с баннером); cancel → `cancelled_by_user` |
| 5 | `research` | dispatch(1–3, PARALLEL): `legal-researcher` ×layer | агенты | все слоты ok и валидны → `research_sufficiency`; часть слотов fail/невалидны → повторный dispatch **только незакрытых слотов** (новый `attempt`, `attempts.research_dispatch_retry` ≤1 на фазу) → затем продолжить с валидными + `drafting_warnings` (если валиден ≥1 слой) → `research_sufficiency`; 0 валидных слоёв → `research_insufficient_pending` |
| 6 | `research_sufficiency` | dispatch(1): `research-sufficiency-reviewer` → script `mf sufficiency route --step` | агент, CLI | `sufficient` → `currency_check`; `targeted_followup_needed` с `subset_u` → `research_sufficiency_followup_pending` (бюджеты независимы, D-116: `attempts.sufficiency_user_followup` ≤2 — вопрос пользователю; `attempts.sufficiency_research_followup` ≤1 brief / ≤2 full — повторный проход исследования; гейт открывается, пока есть пользовательский бюджет, слои едут только при живом исследовательском; при исчерпании обоих — гэпы → `drafting_warnings` → `currency_check`); `subset_r` с `missing` → повторный `research` для названных layer (тот же бюджет); `targeted_followup_needed` только с `weak`/пустыми subset → `drafting_warnings` → `currency_check`; `insufficient` → `research_insufficient_pending`; агент упал после ретрая → `insufficient` |
| 7 | `research_sufficiency_followup_pending` | **gate-text** | пользователь | ответ → (при `subset_r.missing` ∩ `config.researcher_layers` → `research` subset; слой вне режима не диспатчится — каждый такой гэп идёт в `drafting_warnings[]` с префиксом «Out of scope for <mode> mode: », а при пустом пересечении фаза `research` пропускается, как при `continue`) → **всегда** повторный `research_sufficiency` (повторный проход ревьюера бюджета не тратит; неразрешённые `subset_u` при живом пользовательском бюджете снова открывают гейт 7 — не более `sufficiency_user_followup` раз всего, — иначе возвращаются как `weak` → `drafting_warnings`; исследовательский проход после гейта идёт только по набору слоёв, разрешённому роутером при открытии гейта, D-141); `cancel` → `cancelled_by_user` |
| 8 | `research_insufficient_pending` | **gate-text** («continue with caveats / cancel») | пользователь | continue → `currency_check` (+`drafting_warnings`, баннер); cancel → `cancelled_by_user` |
| 9 | `currency_check` | script `mf sources liveness` + `mf sources verify` → dispatch(1): `currency-checker` | CLI, агент | → `source_pack`; при `do_not_use` у ≥1 `critical` → `research_sufficiency` (`attempts.currency_regate` ≤1; сегмент S3b завершается, router продолжает с S3a-маршрута); агент упал после ретрая → непроверенные = `unchecked`, → `source_pack` + баннер |
| 10 | `source_pack` | **script**: `mf sources pack --freeze --step` (одна транзакция, §5.3) | CLI | исключения есть и `source_review_gate ∈ {auto, on}` (или `on`) → `source_review_pending`; иначе → `drafting` |
| 11 | `source_review_pending` | **gate-text** | пользователь | continue → `drafting`; cancel → `cancelled_by_user` |
| 12 | `drafting` | dispatch(1): `memo-writer` v1 → script `mf draft finish` (anchor → lint → audit-citations одним шагом, D-117) → при findings dispatch `memo-writer` fix (≤`config.lint_fix_rounds`) | агент, CLI | lint clean ∧ citations clean → `revision_loop`; не сошлось за раунды → `revision_loop` с `lint.json`+`citations.json` как входом ревьюеров + баннер `lint_not_converged`; writer упал после ретрая → `failed` |
| 13 | `revision_loop` | dispatch(K, PARALLEL): ревьюеры → script `mf review aggregate` → [dispatch `revision-mediator`] → script `mf revision next` → [dispatch `memo-writer` vN+1 → lint/citations] | агенты, CLI | по §4.5 → `client_readiness` |
| 14 | `client_readiness` | dispatch(1): `client-readiness-reviewer` → [dispatch `memo-writer` polish (инструкции = `client-readiness.json[].issues` с `section_id`) → script lint + audit-citations (1 fix-раунд) → повторный reviewer] | агент, CLI | `client_ready` → `export` (с `draft_sha` проверенной версии); `needs_final_polish` ∧ `attempts.client_polish` < max → polish → lint/citations → повтор reviewer; `needs_final_polish` без бюджета или polish с неустранимыми lint/citation-блокерами → `export` (`final_status=manual_review_required_on_v<N>`, экспортируется последняя lint-clean версия); `manual_review_required` → `export` с тем же статусом; агент упал после ретрая → `export` + `manual_review_required` |
| 15 | `export` | **script**: `mf docx render --draft-sha <sha>` (сам валидирует записанный файл, D-117; отдельный `docx validate` остаётся standalone-командой) → `mf finalize` | CLI | → `done` (docx или md-fallback, M9). Выбор версии: последняя версия, прошедшая lint+citations; если такой нет (`no_checked_draft` — блокеры не устранены уже в v1) — экспортируется последняя версия драфта с `final_status = manual_review_required_on_v<N>` и баннером со списком блокеров; экспорт никогда не блокируется. Причины manual-review накапливаются в `final_status_reasons[]` (неполное ревью, no_checked_draft, overflow …) и не снимаются вердиктом readiness |
| 16 | `done` / `failed` / `cancelled_by_user` | terminal (`mf finalize` уже выполнен: `deliverable.*`, `summary.md`, tidy) | — | — |

**Переходы в `failed`:** невалидный выход после исчерпания соответствующего бюджета §2.2; ошибка CLI/схемы state; `mf finalize --reason <text>` от оркестратора; исчерпание `attempts.gate_parse_errors` на гейте без безопасного дефолта. Всегда через `mf finalize` (M9).
**`length_overflow_recommendation`** (Brief): lint L-10 — `major`; при сохранении на выходе из фазы 13 → `final_status = manual_review_required_on_v<N>` + баннер «rerun in Full recommended»; прогон не прерывается и в Full не переключается.
**Удалены:** `mode_pick_pending`, `heartbeat_pending`. Legacy state v1: `mf task resolve` на `schema_version < 2` возвращает `{unsupported: true, hint: "task from v1 — finish it with memoforge 1.1.1 or start a new task"}`; router печатает подсказку и завершает turn; в жизненный цикл v2 (`next`/`finalize`) такая задача не входит.

### 2.2. Схема `state.json` v2 (нормативно — `schemas/state.schema.json`; здесь — семантика)

Унаследованные поля: `task_id`, `user_query`, `created_at`, `language`, `work_dir`, `output_folder`, `mode`, `config`, `intake`, `classification`, `plan_approval`, `current_phase`, `dispatched_researchers`, `current_iteration`, `current_draft_path`, `iterations[]`, `client_readiness`, `final_status`, `final_docx_path`, `attempts`, `sufficiency_followup`, `remaining_blocking_issues`, `fallback_banners[]`.

Новые/изменённые:
- `schema_version: 2` (обязательно).
- `config.python_cmd`, `config.plugin_data_dir` (результаты discovery, §5.1), `config.source_review_gate: "auto"|"on"|"off"` (§2.3), `config.reviewer_list`, `config.max_iterations`, `config.lint_fix_rounds`, `config.intake_max_questions`, `config.mcp_budget {ldh, courtlistener, legalviz, uklegal, justicelibre, opencaselaw}`, `config.writer_model`, `config.style_profile*` (как в v1).
- `drafting_warnings[]` — владелец: CLI (`sufficiency route`, `research` partial, гейт 8); читает `memo-writer` и рендерер (приложение).
- `sufficiency_followup {status, subset_u[], subset_r[], questions[], user_response, asked_at, answered_at}` — владелец CLI.
- `steps[]` — журнал шагов протокола: `{step_id, kind, phase, issued_at, attempt: n, reason: initial|failure|recovery|rerun, agents: [{slot, agent_type, attempt, status: null|ok|fail|no_change|superseded, reported_at, payload_ref, outputs: [{canonical_path, work_path, agent_sha256}]}], status: null|ok|fail|no_change|skipped|autoclosed, closed_at, result_ref}`. Execution identity = `(step_id, attempt)`; повтор шага увеличивает `attempt`: `reason: failure` расходует бюджет §2.2; `reason: recovery` (переиздание после прерывания) — не расходует; `reason: rerun` (то же действие с изменившимися входами: lint после fix, aggregate после повтора ревьюеров) — не расходует, а смысловые лимиты (`lint_fix`, `client_polish`) считают только диспатчи `reason: initial` своего вида. **Рабочее пространство попытки:** агент пишет только в `steps/<step_id>/a<attempt>/<slot>/<basename>` (путь подставлен в промпт; уникален по полной identity); потребители читают **только канонические** пути (`research/…`, `drafts/…`, `reviews/…`), которые создаёт исключительно `report`/autoclose при **публикации** принятого выхода; поздняя запись старой попытки остаётся в её каталоге и никого не трогает. **Авторитетный sha опубликованного файла** хранится в `published[]` (`{canonical_path, sha256, by: step|command, at}`) и обновляется в той же записи state каждой CLI-командой, законно преобразующей файл (`report`-публикация, `draft anchor`, pre-seed); `agent_sha256` в `steps[]` — история, а не предмет сверки. Команды, читающие канонический файл, сверяют его с `published[]`; несовпадение → `output_modified_after_publish` → шаг переиздаётся (`reason: recovery`). Marker `done` содержит `{task_id, step_id, attempt, slot, input_sha: {…}, output_sha: {…}}`; `report` и autoclose применяют один и тот же набор проверок §3.1.
- `final_status_reasons[]` — накопительный список причин `manual_review_required` (§2.1 стр.15).
- `draft_versions[]` — `{version, path, sha256, lint_clean: bool, citations_clean: bool, checked_at}`; `current_draft_sha` — sha версии, к которой относятся текущие вердикты (ревью/readiness/export привязываются к sha).
- `progress` — производное поле: `{phase, phase_started_at, route: [phases…], position: n, total: N, active: [{slot, agent_type, label, started_at}], last_line, artifact_url|null, mcp_calls: {server: n}}`; вычисляется CLI в `next`/`report` из `steps[]` и `events.jsonl` (никто больше его не пишет). `active[]` очищается при `report` шага и по TTL `limits.AGENT_STALE_SECONDS` (1800).
- `sources_frozen: bool` (M6); `cancel_requested: bool` (§2.4).
- `attempts` — таблица бюджетов (все — владелец CLI):

| Ключ | Единица учёта | Инкремент | Лимит | При исчерпании |
|---|---|---|---|---|
| `plan_edit` | задача | ответ `edit` на гейте 4 | 5 | forced approve **последней поданной версии плана** (после применения последней правки) + баннер |
| `sufficiency_user_followup` / `sufficiency_research_followup` | задача | вопрос пользователю (гейт 7) / повторный researcher-проход | 2 / brief 1, full 2 (D-116) | гэпы → `drafting_warnings` |
| `research_dispatch_retry` | фаза `research` (сбрасывается при повторном входе в фазу из 6/7) | повторный dispatch незакрытых слотов | 1 | продолжить с валидными слоями или `research_insufficient_pending` |
| `currency_regate` | задача | возврат 9→6 | 1 | продолжить в `source_pack` |
| `lint_fix{draft_version}` | версия драфта (v1, v2, polish) | dispatch writer-fix | `config.lint_fix_rounds` (polish: 1) | в `revision_loop`/`export` с lint.json + баннер |
| `reviewer_json_retry{iteration,kind}` | итерация×ревьюер | невалидный JSON | 1 | stub |
| `reviewer_rerun{iteration}` | итерация | повтор упавших ревьюеров | 1 | `manual_review_required` (§4.5) |
| `client_polish` | задача | dispatch polish | `config.max_client_polish` | `manual_review_required` |
| `single_dispatch_retry{step_id}` | шаг вида dispatch(1): analyst, sufficiency, currency, writer v1/vN, **writer fix, writer polish**, client-readiness, mediator | `fail`/невалидный выход (`reason: failure`) | 1 | по таблице §2.1 (писатель → `failed`; fix/polish → продолжить с текущей версией + баннер; остальные — деградация по §2.1) |
| `inline_llm_retry{step_id}` | шаг inline-llm | невалидный результат | 2 | `failed` |
| `gate_parse_errors{gate}` | гейт | нераспознанный ответ | 3 | intake/followup → дефолты с `assumptions_accepted=false`; plan(text-fallback) → `cancel`; source_review → continue; insufficient → cancel |

Правила: каждый повтор с `reason: failure` относится ровно к одному бюджету выше (никакого общего `step_retry`); переиздание `reason: recovery` (прерывание между `next` и `report`) бюджет не расходует; `reviewer_json_retry{iteration,kind}` считается по итерации и **не сбрасывается** после `reviewer_rerun`; смысловые лимиты `lint_fix`/`client_polish` считают диспатчи, а провал такого диспатча — `single_dispatch_retry`. **Единая политика script-ошибок:** ошибка CLI на `script`-шаге → оркестратор повторяет команду один раз (`reason: recovery`), затем `mf finalize --reason cli_error`. Тесты бюджетов утверждают **число реальных диспатчей** при повторных `next`/`report`, а не значение счётчика.

Удалены: `live_progress`, `config.visualize_*`, `config.live_progress_enabled`, `rel_work_dir` (кликабельность дают карточки `Write`/`Read`; в чате печатается абсолютный путь), `max_intake_iterations`, `*_gate_choice`, `heartbeat_choice`, `exit_threshold_score`, `events_path` (файл всегда `<work_dir>/events.jsonl`).

**Запись (M2):** `state_io.write(mutator)`: взаимное исключение — **системный advisory-лок на постоянно существующем файле `state.lock`** через один общий helper `state_io.FileLock` (`fcntl.flock(LOCK_EX)` на POSIX; на Windows `msvcrt.locking(LK_NBLCK, 1)` **после `seek(0)` — всегда один и тот же первый байт файла**, т.к. API блокирует диапазон от текущей позиции; ожидание с таймаутом `limits.LOCK_TIMEOUT=30` с через повтор неблокирующей попытки). Lock-файлы создаются при `task new`, никогда не удаляются и не заменяются (`tidy` и `finalize` их не трогают). Лок освобождается ОС при смерти процесса — никаких TTL, PID-проверок и reclamation не требуется. Под локом: read → mutate → сериализация → валидация по схеме → `NamedTemporaryFile(dir=work_dir, prefix=".state-", delete=False)` → `os.replace` (ретрай ×5 на `WinError 32`) → unlock. Невалидный результат — исключение, файл не меняется. Мутация и закрытие шага (`steps[]`) — в одном `write`. **Порядок вложенности локов — строго `state.lock` → `sources.lock` → `events.lock`**; захват «вверх» (например, `state` под `events`) запрещён и проверяется helper'ом (исключение `lock_order_violation`). Тесты: два процесса, один убит под локом — второй получает лок; взаимное исключение на Windows (два процесса, счётчик).

**Файловые побочные эффекты команд (замена «транзакциям»):** каждая `--step`-команда, создающая или преобразующая файлы (`sources pack --freeze`, `draft anchor`, pre-seed драфта в `revision next`, `render`, `docx render`, `finalize`), сначала копирует свои **входы** и пишет **результат** в рабочий каталог шага `steps/<step>/a<n>/cli/`, затем публикует канонический файл через tmp + `os.replace` и закрывает шаг в state (`published[]` с новым sha) одной записью. Все такие команды **детерминированны по входам**, поэтому окно «канонический файл уже заменён, state ещё не записан» восстанавливается так: при повторе незакрытого шага команда берёт вход из рабочего каталога, пересчитывает результат и, если канонический файл равен пересчитанному — просто закрывает шаг (никакой произвольный sha авторитетным не признаётся); иначе публикует пересчитанный результат заново. Сверка потребителей с `published[]` выполняется **после** такого replay (порядок в `next`: сначала закрыть/переиграть незакрытые script-шаги, потом выдавать следующий). Для freeze авторитетен файл snapshot. Тесты `test_crash_between_file_publish_and_state_close` для каждой такой команды, включая `anchor replace → crash → resume → lint`.

### 2.3. Режимы (`modes.py`)

| Поле | Brief | Full |
|---|---|---|
| `researcher_layers` | `["statutes"]` | `["statutes","case_law","doctrine"]` (doctrine только при `plan.doctrine_required`) |
| `reviewer_list` | logic, citations, counterarguments | logic, form, citations, counterarguments |
| `max_iterations` | 2 | 2 |
| `client_polish_enabled` / `max_client_polish` | false / 0 | true / 1 |
| `template_id` | `executive-brief` (cap 1200 слов) | `classical-memo` |
| `source_review_gate` (при `userConfig = auto`) | `off` | `auto` |
| `lint_fix_rounds` | 1 | 2 |
| `intake_max_questions` | 10 (режим ещё не выбран — константа `limits.INTAKE_MAX_QUESTIONS`) | 10 |
| `mcp_budget` (LDH / CourtListener / LegalViz / UK Legal / JusticeLibre / OpenCaseLaw на прогон) | 8 / 10 / 10 / 10 / 10 / 10 | 10 / 40 / 40 / 40 / 40 / 40 |

Цепочка приоритета для `source_review_gate`: `userConfig` явно `on|off` > режим > `auto`. Матрица существует только в коде; `docs/modes.md` генерируется `mf docs render` и проверяется тестом.

### 2.4. Гейты (M8)

| Гейт | Фаза | Механизм | Формат ответа (единый парсер `gates.py`) |
|---|---|---|---|
| Intake | 2 | **gate-text**: `mf gate render --gate intake` печатает ≤`intake_max_questions` must-answer вопросов (аналитик ранжирует по полю `impact`), остальные — дефолты с `confidence` и «что изменится, если дефолт неверен»; END turn | `1A 2C 3: free text` · `proceed` · `cancel`. Слэш-форма `/memoforge:continue <task_id> 1A 2C` — первой строкой подсказки |
| Plan + Mode (+ Style, + Sources budget) | 4 | **gate-auq**: один `AskUserQuestion` с вопросами `Plan` (Approve / Edit / Cancel), `Mode` (Brief / Full; рекомендованный первым с «(Recommended)» по `plan.estimated_complexity`), `Style` (только если есть профили), `Sources` (только если `intake/mcp-probe.json` показал отсутствие LDH/CourtListener ИЛИ оценка потребности для **рекомендованного** режима превышает `mcp_budget` — оценка считается при формировании гейта для обоих режимов и показывается в тексте вопроса: Continue with reduced coverage / Cancel). Порядок применения: `Cancel` в любом компоненте → `cancelled_by_user`; иначе Style (`mode_binding` профиля переопределяет Mode с пометкой в ответе) → Mode → Sources → Plan. Ответы → `mf report --step … --answers '{"Plan":"Approve","Mode":"Full","Style":"my-firm","Sources":"Continue"}'` → `gates.parse(gate="plan")` → `plan_approval.iterations[]`, `mode_selected`, `gate_answered`. **Text-fallback (эквивалентный):** гейт имеет `generation` (0 = AUQ). `mf report --step … --status no_answer` разрешён только (а) при ошибке инструмента AUQ в том же turn или (б) в **новом** turn, когда router видит гейт с `generation 0` без ответа (pending ≠ unavailable внутри turn); он увеличивает `generation` (событие `gate_channel_switched`), и `next` выдаёт `gate-text` с полным форматом `approve [brief\|full] [style:<name>\|standard] [sources:reduced]` · `edit: <text>` · `cancel`. `generation` входит в ответ `next` для гейта и передаётся в `report --answers … --generation <g>` (для text-гейтов — в `gate parse --generation`); ответ с иным `generation` отклоняется (`stale_generation`) **до** проверки «шаг уже закрыт», поэтому поздний ответ старого канала никогда не становится no-op-подтверждением |
| | | | при `Edit` — free-text (второй AUQ `Other` или text-fallback); `attempts.plan_edit` |
| Sufficiency follow-up | 7 | gate-text | как Intake |
| Insufficient research | 8 | gate-text | `continue` · `cancel` |
| Source review (условный) | 11 | gate-text: **срабатывает только при исключениях** (`auto`): `critical`-источники со статусом `unresolved`/`manual_check`/`do_not_use`, конфликтующие авторитеты, непустой `drafting_warnings`, исчерпанный MCP-бюджет; текст — `mf sources digest --exceptions`. `on` — всегда; `off` — никогда (исключения → приложение «Assumptions & Unverified Sources») | `continue` · `cancel` |

Правила: (a) все подсказки генерирует CLI, ключевые слова единые (`proceed`, `continue`, `cancel`, `<n><A-D>`, `<n>: text`, `approve`, `edit:`); (b) `gates.parse --step` возвращает `{action, answers, defaults_applied[], errors[]}`, пишет `intake/user-facts.md` / `followup-response.json`, событие `gate_answered`, и закрывает шаг гейта в той же записи state (повторный parse того же шага — no-op); (c) нераспознанный ответ → `errors` → подсказка повторяется, `attempts.gate_parse_errors[gate]++`; дефолты по исчерпанию бюджета никогда не ставят `assumptions_accepted=true`; (d) **`cancel`:** router распознаёт слово `cancel` в аргументах `continue` **до** проверки фазы и вызывает `mf task cancel` (ставит `cancel_requested=true`, событие); `next` при `cancel_requested` не выдаёт новых dispatch/script-шагов, принимает поздние `report` уже выданных шагов и возвращает `finalize(cancelled_by_user)`; отмена **посреди** автономного блока в v2.0 невозможна (ограничение хоста) — действует на гейтах и при следующем `next` после прерывания.

### 2.5. Re-entry и объединение `memo`/`continue`

Оба скилла вызывают один router (`skills/memo/references/router.md`, ≈60 строк). `/memoforge:memo "<q>"` → `mf task new --query "<q>"` → loop. `/memoforge:continue [task_id] [reply…]` → `mf task resolve [task_id]` (последняя незавершённая) → если `reply` непустой и фаза — гейт: `mf gate parse` → loop. Бесслэшевый ответ на гейт в той же сессии обрабатывает тот же router; подсказка гейта содержит слэш-команду первой строкой.

**`task new` (`task.py`):** slug санитизируется (`[a-z0-9-]`, ≤40), `work_dir` резолвится по цепочке `userConfig.output_folder` → `$MEMOFORGE_OUTPUT_FOLDER` → `<$CLAUDE_PROJECT_DIR|cwd>/memoforge` (source `project_folder` — рабочая папка сессии; пропускается, если это plugin root — `pylauncher.plugin_root()`, `$CLAUDE_PLUGIN_ROOT` или любой каталог с `.claude-plugin/plugin.json` — сам домашний каталог или корень ФС) → `Path.home()/Documents/memoforge` (не `$HOME`) → `<cwd>/outputs/memoforge-work`; первый writable; результат и причина — в событии `work_dir_resolved`. **`plugin_data_dir` — единая цепочка для `mf`-обёрток и пакета:** `${CLAUDE_PLUGIN_DATA}` → `%LOCALAPPDATA%/claude/plugin-data/memoforge` (Windows) / `~/.claude/plugin-data/memoforge` → `<plugin_root>/.data` (последний fallback не зависит от задачи). **Опции `userConfig` резолвятся цепочкой** явный флаг (`--option key=value`) → `CLAUDE_PLUGIN_OPTION_*` → `<plugin_data_dir>/options.json` (зеркало, которое SessionStart-хук пишет из env, а `mf config set` — вручную) → default манифеста; значение с неразвёрнутым `${…}` игнорируется, а источник каждого ключа возвращается в ответе `task new` (`options_source`). **Публикация результата (D-109):** после `deliverable` и `summary.md` `mf finalize` копирует их вместе с `sources/` (source-pack плюс сырой текст каждого `critical`/`supporting` источника) в `<publish_folder>/memoforge/<slug>/`; корень — опция `publish_folder`, иначе `/mnt/user-data/outputs`, если он есть и доступен на запись, иначе не копируется ничего; сбой копирования — баннер `publish_failed`, а не ошибка прогона. Тесты: пустой `$HOME`, slug с пробелами/`..`.

---

## §3. Протокол оркестратора `next → act → report`

### 3.1. Команды

`mf next --workdir W` → JSON одного из видов (`command[]` всегда содержит абсолютный путь к `scripts/mf` (`mf.cmd` на Windows), подставленный CLI):

```jsonc
{"step_id":"s-017","kind":"dispatch","parallel":true,"phase":"research",
 "agents":[{"slot":"statutes","subagent_type":"memoforge:legal-researcher","model":"sonnet","description":"P5/13 · legal-researcher · statutes",
            "prompt":"<полный текст из prompts/legal-researcher.md с путями, ${step_id}, ${attempt}, ${slot}>",
            "expected_outputs":[{"canonical":"research/statutes.json","work_path":"steps/s-017/a1/statutes/statutes.json","schema":"research-findings"}]}, …],
 "attempt":1,"reason":"initial","chat_line":"Phase 5/13 — research: 3 researchers dispatched (statutes, case_law, doctrine)"}
{"step_id":"s-018","kind":"script","attempt":1,"phase":"drafting","command":["<abs>/scripts/mf","draft","lint","--workdir","W","--step","s-018","--attempt","1","--draft","drafts/v1.md"],"chat_line":"…"}
{"step_id":"s-004","kind":"gate-auq","attempt":1,"generation":0,"phase":"plan_approval_pending","questions":[…AskUserQuestion-schema…],"text":"<plan digest>","text_fallback":"<подсказка>"}
{"step_id":"s-002","kind":"gate-text","attempt":1,"generation":0,"phase":"intake_questions_pending","text":"<готовая подсказка>","end_turn":true}
{"step_id":"s-011","kind":"inline-llm","attempt":1,"phase":"planning","instruction":"<что сгенерировать>","write_to":"steps/s-011/a1/orchestrator/plan.json","schema":"schemas/plan.schema.json"}
{"step_id":"s-099","kind":"terminal","phase":"done","text":"<итог с путями>"}
```

`mf report --workdir W --step <id> --attempt <n> [--agent <slot>] --status ok|fail|no_answer [--answers <json> --generation <g>] [--stdout <file>]` — для `dispatch`, `inline-llm` и `gate-auq`. **Порядок проверок:** (0) identity `(step, attempt)` не совпадает с текущей → `identity_mismatch` (ошибка, ничего не меняется); для гейта `generation` ≠ текущему → `stale_generation` (ответ отклонён); (1) шаг/слот уже закрыт → no-op `{accepted:true, already_reported:true}`; (2) содержательная проверка:
- для `dispatch` — по одному вызову на слот (или один вызов без `--agent` = все слоты ok); шаг закрывается, когда отчитались все слоты. CLI проверяет **completion** слота: marker `steps/<step_id>/a<attempt>/<slot>/done.json` (пишет агент последним действием `mf agent log --state done` — **обязательная** часть контракта, в отличие от best-effort `--state step`; `agent log` сам вычисляет `input_sha` по списку входов из промпта и `output_sha` по файлам рабочего каталога), совпадение `input_sha` marker'а с sha входов, зафиксированных CLI при выдаче попытки (`steps[].inputs`), существование и валидность по схеме файлов в рабочем каталоге попытки, совпадение их sha с `output_sha` marker'а; для писателя — сравнение с seed: sha == seed → `no_change` (v1: `fail`; fix/polish: закрыть шаг `no_change`, продолжить с прежней версией; vN-ревизия: `fail` с бюджетом `single_dispatch_retry`, после исчерпания — `revision next` считает блокеры неустранёнными). Затем **публикует** файлы в канонические пути (tmp+replace), обновляет `published[]`, копирует `--stdout` в `steps/<step_id>/a<attempt>/<slot>/stdout.txt`, пишет `agent_returned`, обновляет state;
- для `inline-llm` — `report --status ok` после записи файла оркестратором в `steps/<step>/a<n>/orchestrator/<basename>`; CLI валидирует по схеме, публикует и закрывает шаг;
- ответ `{accepted: true, next_hint}` | `{accepted:false, errors:[…], retry:{step_id, attempt, agents:[slots]}}`.

`script`- и `gate-text`-шаги **не требуют `report`**: каждая изменяющая команда (`sufficiency route`, `sources pack --freeze`, `draft finish` (= anchor|lint|audit-citations), `review aggregate`, `revision next`, `gate parse`, `docx render|validate`, `finalize`) принимает `--step <id> --attempt <n>`, выполняет действие и закрывает шаг одной записью state; повторный вызов с той же identity — no-op с прежним результатом (`steps[].result_ref`); та же identity с другими аргументами — `identity_mismatch`. Повтор операции с новыми входами (lint после fix, aggregate после повтора ревьюеров) `next` выдаёт как тот же `step_id` с `attempt+1`, `reason: rerun`. Незакрытый `script`-шаг `next` возвращает повторно (`reissued`), не продвигая фазу. `draft anchor` и pre-seed (`revision next`) обновляют `published[]` в своей записи state — последующий lint сверяет файл с новым авторитетным sha (сценарий `report v1 → anchor → lint` проходит без ложного recovery). Pre-seed для vN создаётся CLI **в рабочем каталоге попытки писателя** (`steps/<step>/a1/writer/v<N+1>.md`) и одновременно публикуется как `drafts/v<N+1>.md` (seed); писатель правит рабочую копию, `report` сравнивает с seed и публикует. Оркестратор после `script`-шага просто вызывает `next`.

Исходы шага (`machine.py`):

| kind | `fail` или невалидный выход | бюджет (§2.2) | после исчерпания |
|---|---|---|---|
| dispatch (N слотов) | повтор только незакрытых слотов с новым `attempt` и текстом ошибок в промпте | `research_dispatch_retry` / `reviewer_json_retry` / `reviewer_rerun` / `single_dispatch_retry` | по таблице §2.1 |
| script | ошибка CLI — не бизнес-исход; повтор оркестратором один раз, затем `finalize(failed)` | — | `failed` |
| inline-llm | повтор с ошибками схемы | `inline_llm_retry` 2 | `failed` |
| gate-* | `gate_parse_errors` | 3 | §2.2 |

Правила: (1) оркестратор НИКОГДА не выполняет шаг, не полученный от `next`; (2) `dispatch parallel:true` — все `Agent`-вызовы в ОДНОМ сообщении (единственное правило параллелизма в промпте; `events analyze` детектирует серийность); (3) `next` без закрытия предыдущего dispatch-шага: слоты, у которых есть marker текущего `attempt` и валидные attempt-специфичные выходы, закрываются автоматически с публикацией (событие `step_autoclosed`); остальные слоты переиздаются с `attempt+1`, `reason: recovery` (старая попытка помечена `superseded`, её поздние файлы `.a<n>` игнорируются); пустой `expected_outputs` никогда не даёт autoclose; каждый вызов `next`/`report` пишет событие `cli_call` (для G2); (4) `description` формируется CLI по шаблону `P<n>/<N> · <agent> · <label>`, где `N` = число нетерминальных фаз, достижимых при текущем `config` (пересчитывается в `next`); (5) `sources register` идемпотентен по `(layer, url|citation_form)`; (6) `next` пишет событие `step_issued` (гарантированный след старта, независимо от хуков и агентов).

### 3.2. Сегменты и границы (совместимость с Workflow)

Сегмент = фактический маршрут от текущей точки до следующего гейта или терминала; фиксированного набора фаз сегмент не обещает. Типовые сегменты: S1 (фаза 1), S2 (фаза 3), S3a (5→6, возможно с возвратом 6→5), S3b (9→10; при regate 9→6 сегмент завершается и router продолжает по маршруту S3a), S4 (12→15). Внутри сегмента `next` не возвращает `gate-*`. Workflow-пилот (§10) представляет только S3a и S4 как скрипты; S3b — нет.

### 3.3. Промпты диспатча

`scripts/memoforge/prompts/<agent>.md` — стандартный `string.Template` с плоскими именами `${work_dir}`, `${paths_agent_core}`, `${paths_checklist}`, `${paths_template}`, `${prose_style_path}`, `${layer}`, `${layer_rules}` (**только строка своего layer** из таблицы §4.3), `${iteration}`, `${step_id}`, `${attempt}`, `${slot}`, `${mf}` (абсолютный путь к `mf`/`mf.cmd`); рендер через `substitute()` (не `safe_substitute`) — нераскрытый плейсхолдер = исключение. Каждый промпт начинается с первого действия агента `${mf} agent log --workdir … --step ${step_id} --attempt ${attempt} --slot ${slot} --state start` и заканчивается последним `… --state done` (пишет completion marker, §3.1). `test_prompts.py` сравнивает рендер каждого шаблона для Brief и Full с golden-файлами и проверяет абсолютность путей.

### 3.4. Router `skills/memo/SKILL.md` (≈150 строк)

Frontmatter: `name: memo`, `disable-model-invocation: true`, `argument-hint`, `allowed-tools: Read, Write, Bash, Agent, AskUserQuestion, WebFetch, WebSearch, mcp__*` (роль allowed-tools — снятие промптов на turn; MCP-инструменты главной сессии наследуются агентами). Содержание: authority hierarchy (6 строк); untrusted-content (3 строки); протокол §3.1 с примером каждого `kind`; правило «параллельный dispatch = одно сообщение»; печать `chat_line`; поведение при `accepted:false`; при ошибке CLI на `script`-шаге — повторить команду один раз, при повторной ошибке `mf finalize --reason cli_error` и END (та же политика, что §2.2). Никаких фазовых процедур.

---

## §4. Агентный слой

### 4.1. Состав (16 → 12) и модели

| Агент | Фаза | `model` | `effort` | `tools` (frontmatter) | Изменения |
|---|---|---|---|---|---|
| `fact-assumption-analyst` | 1 | `opus` | high | **не задаётся** (наследование, MCP) | JSON `intake/questions.json` (+ `impact`, `default`, `confidence`) и `intake/preliminary-sources.json` |
| `legal-researcher` (НОВЫЙ, параметр `layer`) | 5 | `sonnet` | high | **не задаётся** | §4.3 |
| `research-sufficiency-reviewer` | 6 | `opus` (↑) | high | Read, Write, Bash | JSON по схеме; raw отсутствует у critical → `missing` |
| `currency-checker` | 9 | `sonnet` | medium | **не задаётся** | Только JSON; статус `unchecked`; `source_id` только из реестра; вход — результаты `sources verify` |
| `memo-writer` | 12–14 | `config.writer_model` (default `opus`; `limits.ALLOWED_WRITER_MODELS={opus,fable,sonnet}`, иначе fallback opus + событие) | high (v1) / medium (vN) | Read, Write, Edit, Bash | §4.4 |
| `logic-reviewer` | 13 | `opus` (↑) | high | Read, Write, Bash | чеклист §4.5 |
| `form-reviewer` (НОВЫЙ = clarity+style) | 13 (Full) | `sonnet` | medium | Read, Write, Bash | lens `clarity|style`; читает профиль стиля |
| `citation-auditor` | 13 | `opus` (остаётся: `source_drift` — суждение о смысле, не сверка строк) | high | Read, Write, Bash | вход — `citations.json` + пары «утверждение ↔ Layer-1»; категории `source_drift`, `source_pack_mismatch`, `unsupported_claim` |
| `counterargument-reviewer` | 13 | `opus` | high | Read, Write, Bash | описание: «surface contrary authority present in research / considered-excluded» |
| `revision-mediator` | 13 | `sonnet` (↓) | medium | Read, Write, Bash | только консолидация → `reviews/v<N>-mediator.json`; не пишет state |
| `client-readiness-reviewer` | 14 | `sonnet` | medium | Read, Write, Bash | delivery-проверки; читает профиль стиля; JSON по схеме |
| `style-extractor` | Style Studio | `opus` | high | Read, Write, Glob, Bash | без изменений логики; вызовы CLI через `mf style …` |

`Bash` есть у всех (нужен для `mf agent log` и `mf quote extract`); `permission_gate --mode bash` (§8.1) — это **auto-allow** только для вызовов `mf`, а не запрет прочих команд (прочие идут через обычный permission-flow пользователя). Для трёх MCP-зависимых агентов `tools:` не задаётся (урок `agents/statutory-researcher.md:8` ✓: явный allowlist «silently strips MCP inheritance»; глоб `mcp__*` в `tools` не верифицирован — проба P9), а ограничение задаётся **`disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*`** (поле подтверждено 04 §Верификация; не входит в список игнорируемых у plugin-агентов — проверяется P9). У остальных агентов явный `tools:` без этих инструментов. `test_agent_frontmatter.py` проверяет эффективную политику: либо `tools` без запрещённых, либо `disallowedTools` с полным списком. Обоснование моделей — 02 §3.4; таблица дублируется в `lib/models.md`.

### 4.2. Общие блоки `lib/agent-core/` (пути подставляет промпт диспатча, не переменная в теле агента)

`untrusted-content.md`, `tooling-core.md` (MCP-first; fail-soft порталы; 429 → один ретрай по `Retry-After`, затем fail-soft + событие; WebSearch-политика — по строке layer), `output-json.md` (JSON-only, схема, один пример; «не пишите md-виды»), `logging.md` (`${mf} agent log --step --attempt --slot --state start|step|done --detail "<d>"` через Bash; `--state start` и `--state done` — обязательные первое и последнее действия агента (done пишет completion marker), `--state step` — best-effort телеметрия), `style-profile.md` (если `${prose_style_path}` задан — он авторитетен вместо `lib/prose-style.md`; читают `memo-writer`, `form-reviewer`, `client-readiness-reviewer`, `revision-mediator`). Единый порядок секций промпта: Role → Task → Inputs → Output contract (schema + пример) → Rules (только не проверяемое скриптом) → Failure modes → Final response (≤100 слов). Удаляются «Pre-return checklist», «Live progress», «Tool-call telemetry», исторические нарративы, усилители «HARD RULE/MANDATORY/STOP».

### 4.3. `legal-researcher`

- Параметры: `layer ∈ {statutes, case_law, doctrine}`, `issues[]`, `jurisdictions[]`, `mcp_namespaces` (из `intake/mcp-probe.json`), `followup_prompts[]`, `routing[]` (готовый порядок инструментов из `routing.py`).
- **Таблица правил по layer** (в промпт попадает только своя строка):

| layer | WebSearch как первичный инструмент | Что можно цитировать |
|---|---|---|
| statutes | нет (discovery only) | MCP-документы; WebFetch issuing-body порталов |
| case_law | нет | MCP-документы; официальные суды/Find Case Law/CourtListener |
| doctrine | **да** (как `agents/doctrinal-researcher.md:81` ✓) | + официальные регуляторные руководства, рецензируемые публикации, SSRN-класса репозитории (критерии `:87-91` ✓) |

- Регистрация ОБЯЗАТЕЛЬНА для `critical`/`supporting`: `mf sources register --layer L --title … --citation … --url … --tool <name> --tier T --raw-file <tmp>` (полный текст из инструмента; скрипт переносит в `research/raw/<layer>/<slug>.md`, считает `raw_sha256`, назначает `source_id`, идемпотентно по `(layer, url|citation)`). `background` — без raw.
- **Роутинг источников — `routing.py` = статическая таблица предпочтений (без новых HTTP-клиентов):** для каждой пары `layer × jurisdiction` — порядок инструментов и предпочтительные домены WebFetch. EU statutes → `legalviz_resolve`/`legalviz_get_law_part` (срез нужной статьи), резерв — Cellar `publications.europa.eu/resource/celex/<CELEX>` через `mf sources fetch` (`Accept: application/xhtml+xml`; EUR-Lex периодически отвечает 202 WAF-challenge, ретраи бесполезны), затем LDH `resolve_reference` с `hint_country=EU` (D-145, D-149); **полный текст акта в контекст агента не запрашивать** (MCP-ответ всегда попадает в контекст агента — способа сохранить его на диск минуя контекст у платформы нет, поэтому экономия достигается выбором инструмента/детализации, а не slicer'ом); консолидации → `EU/ConsolidatedLegislation`; EDPB → LDH, fallback WebFetch `edpb.europa.eu`; UK законы → WebFetch `legislation.gov.uk`; UK прецеденты → WebFetch Find Case Law (`caselaw.nationalarchives.gov.uk`; BAILII исключён); US прецеденты → CourtListener MCP (`extract_citations`/`analyze_citations` для проверки существования); US регуляции → WebFetch `govinfo.gov` (eCFR/FR HTML не использовать). `mf sources slice --article N` — вспомогательная команда над уже сохранённым raw (вырезка статьи для `quote extract`; при неоднозначности заголовков — ошибка `ambiguous`, не первое совпадение). Коды стран нормализует CLI (`GB`→`UK`). Актуальные имена источников LDH — `lib/routing/discover-cache.json`. **EU-право идёт через LegalViz** (`legalviz`, `https://api.legalviz.eu/mcp`, третий bundled-сервер `.mcp.json`; свободный, без ключа): `legalviz_resolve`/`legalviz_search_eu_law` → CELEX, `legalviz_get_law_part` (`part=structure`, затем нужная статья, `version` — point-in-time) даёт срез вместо целого акта, `legalviz_get_case_law`/`legalviz_get_citing_provisions` находят толкующие решения CJEU (метаданные, не текст — текст решения по CELEX `6<year>CJ<number>` из Cellar/EUR-Lex через `mf sources fetch` либо `justicelibre_get_decision_cjue`; `curia.europa.eu` отдаёт пустой JS-каркас и источником не является, D-145), `legalviz_get_law_relations` — первая проверка актуальности акта у `currency-checker`; **UK-право идёт через UK Legal** (`uk-legal`, `https://uk-legal-mcp.fly.dev/mcp`, четвёртый bundled-сервер; свободный, без ключа): статуты — `uklegal_legislation_search` → `uklegal_legislation_get_toc` → `uklegal_legislation_get_section` (разобранный текст секции вместе с extent и in-force — он же проверка актуальности UK-законодательства у `currency-checker`), прецеденты — `uklegal_case_law_search` → `uklegal_judgment_get_header` → `uklegal_judgment_get_index` → `uklegal_judgment_get_paragraph`, дальше WebFetch `legislation.gov.uk` / `caselaw.nationalarchives.gov.uk` (BAILII по-прежнему исключён); `uklegal_citations_resolve`/`uklegal_citations_format_oscola` подтверждают и оформляют UK-цитату (в citation-аудитор не заводятся). Дневные потолки `legalviz` и `uklegal` — в `limits.py`, отсутствие любого из namespace попадает в вопрос `Sources` гейта плана (D-105). **Франция идёт через JusticeLibre** (`justicelibre`, `https://justicelibre.org/mcp`, пятый bundled-сервер; свободный, без ключа): статуты — `justicelibre_get_law_article` (контракт `code` = короткая аббревиатура кодекса, `num` = номер статьи; при неверном коде сервер сам отдаёт все 79 поддерживаемых) и `justicelibre_resolve_law_number` для актов вне кодексов, резерв — `ldh_resolve_reference` по `FR/LegifranceCodes` (229 364 статьи; `ldh_search` по ней — шум, LDH резолвит точную цитату и не является поисковиком); практика — `justicelibre_search_judiciaire`/`get_decision_judiciaire`, `search_conseil_etat`/`get_ce_decision`, `search_cc` (`courdecassation.fr/decision/<id>` — 255-байтовый JS-каркас, не `--url`); доктрина — `justicelibre_search_cnil`, `search_doctrine`; для EU case_law `justicelibre_search_cjue`/`get_decision_cjue` — альтернативный источник текста после LegalViz. Регистрируется `source_url` на Légifrance; сам портал закрыт managed challenge Cloudflare, бесключевой резерв с текстом — `code.travail.gouv.fr`. **Швейцария идёт через OpenCaseLaw** (`opencaselaw`, `https://mcp.opencaselaw.ch/mcp`, шестой bundled-сервер; свободный, без ключа, данные CC0): `opencaselaw_get_law(sr_number, article)` плюс `search_laws`, `get_article_history` — статуты с `source_url` на `fedlex.admin.ch` и перечнем уже назначенных консолидаций (он же ответ на вопрос актуальности); `opencaselaw_search_decisions`/`get_decision`/`get_regeste`/`find_leading_cases` — практика (`bger.ch`); `opencaselaw_get_doctrine`/`search_commentaries` — доктрина; `entscheidsuche` не бандлится. `CH` живёт в `routing.EXTRA_JURISDICTION_ROWS` и подмешивается `route()`, список `MEMBER_STATES` остаётся EU-only. Дневные потолки `justicelibre` и `opencaselaw` — 60, как у LegalViz/UK Legal (D-148).
- **Бюджет MCP-вызовов (наблюдаемый run-бюджет; остаток провайдера CLI не знает):** `config.mcp_budget`; `PostToolUse`-хук пишет `mcp_call {server, tool, ok|error}` по всем namespace из `intake/mcp-probe.json` (matcher генерируется `build_hooks` из probe? — нет: matcher `^mcp__` с фильтром по алиасам серверов в самом хуке (D-134), а для иных namespace в Cowork счётчик ведёт сам агент через `mf agent log --mcp <server>`, best-effort); CLI агрегирует `progress.mcp_calls` (успешные и ошибочные отдельно); оценка потребности считается при формировании гейта плана для обоих режимов (`layers×issues×2` + доля intake/currency) и сравнивается с `config.mcp_budget` и с дневным лимитом провайдера из `limits.py` **как с верхней границей** (реальный остаток неизвестен — текст вопроса `Sources` так и говорит); в промпт исследователя подставляется его доля; ожидание `Retry-After` ограничено `limits.RETRY_AFTER_CAP=60` с — дневной throttle не подвешивает прогон, а переводит в fail-soft.
- Выход: `research/<layer>.json` (`research-findings.schema.json`): по каждому `issue_id` — `findings[] {source_id, proposition, pinpoint, role: rule|application|risk|background|contrary, weight: binding|persuasive|non_binding, confidence: high|medium|low, tier, quote_short ≤15 слов, contrary_point?}` — смысловые поля source-pack базлайна (`agents/source-pack-builder.md:40,44-48,59-63`) сохраняются на уровне «источник × вопрос»; `mf sources pack` переносит их без вывода веса из tier (один источник может иметь разные роли по разным issues). Md-вид рендерит `mf render research`. Промпт ≤120 строк.

### 4.4. `memo-writer`

- Вход v1: `plan.json`, шаблон, `intake/*`, `research/*.md` (рендер), `research/source-pack.json`, `research/currency.json`, `state.drafting_warnings`, профиль стиля/`lib/prose-style.md` (сокращённый). Не читает raw и reviews.
- Цитирование: `[[src:<source_id> <pinpoint>]]`; blockquote только `> [[q:<quote_id>]] <текст>` с `quote_id` от `mf quote extract` (контракт extraction, ошибки и `mf quote skip` — §5.3). Если для пары (секция, источник) записан `skip` (по любой причине: `too_long`, `ambiguous`, `not_found`, `no_raw`, `raw_changed`), quote-бит четырёхбитной структуры заменяется пересказом нормы с `[[src:]]` — это законный исход, писатель не обязан перебирать подстроки; контраргумент и его разрешение остаются обязательными всегда. `quote_id` однозначно определяет `source_id`: сноска и все C-проверки применяются к источнику цитаты и без соседнего `[[src:]]`. Sources-секцию писатель не пишет.
- vN: pre-seeded копия (делает `mf revision next`), `Edit` только секций из `mediator.json[].section_id`; якоря `<!-- §s-4-1 -->` ставит `mf draft finish` (`draft anchor` внутри него) после v1.
- Fix-цикл после lint: writer получает `lint.json`/`citations.json` и правит только указанные позиции.
- Промпт ≤150 строк, обычный тон. Шаблон `classical-memo`: conclusion-first, Rule explanation, **обязательное предложение с контраргументом и его разрешением** внутри Analysis (CREAC/CRRACC; проверяет чеклист `counterargument-reviewer`); четырёхбитная структура сохраняется.

### 4.5. Ревью-луп (Full ≤2 итерации; Brief 1)

1. `mf draft lint` → fix-цикл (≤`lint_fix_rounds`) → `mf draft audit-citations` → fix (те же раунды). Не сошлось → в луп с `lint.json`+`citations.json` как входом ревьюеров + баннер.
2. Диспатч ревьюеров одним сообщением. Каждый — изолированный грейдер по **бинарному чеклисту** `lib/checklists/<kind>.json` (`{id, text, tier: substance|form, hard_fail}`; источники — Columbia/Georgetown/UNH memo checklists, CRRACC, house-style); порядок пунктов перемешивается детерминированно по `task_id`. Выход `reviews/v<N>-<kind>.json` (`review.schema.json`, `oneOf` по `reviewer` + отдельный вариант `status: failed` для stub): `reasoning` (первым), `draft_sha`, `checklist[] {id, pass: true|false|unknown, evidence}`, `issues[] {severity: blocker|major|minor, category, section_id, issue, suggestion, checklist_id?, lens|issue_category|attack_vector}`, `verdict ∈ {approved, needs_revision}`. Валидатор (`review validate`): набор `checklist[].id` **в точности** равен набору из файла чеклиста (без дублей и неизвестных); каждый `pass=false` у `hard_fail`-пункта имеет issue с `severity=blocker` и `checklist_id`; `unknown` у `hard_fail`-пункта несовместим с `approved` (валидатор понижает вердикт до `needs_revision` и добавляет issue `unverified_hard_fail`); `approved ⇔ 0 blockers`; `draft_sha` == текущий. Кап «≤5 major» — рекомендация в промпте, не `maxItems`. Промпт явно: «`approved` — нормальный исход». Ревьюеры не видят `changelog.md`, прошлых оценок, пометок «final». Писатель не видит сырые ревью.
3. `mf review aggregate --iteration N --step`: валидация (ретрай ×1; stub = `status: failed`, без чеклиста и вердикта), **включение детерминированных блокеров**: актуальные `lint.json`/`citations.json` для `draft_sha` добавляются в набор блокеров как источник `deterministic` (LLM-вердикт их не снимает), дедупликация issues только внутри одной категории (`section_id` + категория + Jaccard ≥0.8; сохраняются provenance всех ревьюеров и максимальная severity; issues с противоположными выводами по одному `section_id` не сливаются, а помечаются `conflict`), подсчёт `substance_blockers` (logic/citations/counterarguments/deterministic), `form_blockers`, `failed_reviewers[]`, `coverage` (набор успешных ревьюеров), `pass_ratio` (информационно), запись `iterations[N]`.
4. `mf revision next --step` (единственный владелец `current_iteration`) — **первая совпавшая ветка**:
   1. `failed_reviewers ≠ ∅ ∧ attempts.reviewer_rerun[N] < 1` → повторный dispatch только упавших (`attempt+1`), `reviewer_rerun[N]++`, возврат к п.3;
   2. `failed_reviewers ≠ ∅` (бюджет исчерпан) → неполное обязательное ревью: `final_status = manual_review_required_on_v<N>` → `client_readiness` (никогда не `approved`; при `failed_reviewers == все` — reason `all_reviewers_failed`);
   3. `N ≥ 2 ∧ coverage_N == coverage_{N-1} ∧ substance_blockers_N > substance_blockers_{N-1} ∧ ∃ новый substance-блокер с evidence, отсутствовавший в N−1 (по `section_id`+категория)` → regression: `current_draft_path = drafts/v<N-1>.md`, `forced_exit_on_v<N-1>_with_remaining_issues` (в Brief недостижима; тест — только Full); при несопоставимом покрытии regression не проверяется;
   4. `substance_blockers == 0 ∧ form_blockers == 0` → `approved_on_v<N>` → `client_readiness`;
   5. `substance_blockers == 0 ∧ form_blockers > 0` → `accepted_early_on_v<N>` (form-блокеры → баннер) → `client_readiness`;
   6. `N < max ∧ ∃ blocker с evidence из deterministic/citations/hard_fail logic` → [медиатор, если блокеров/major ≥2 от ≥2 ревьюеров или есть `conflict`; иначе CLI собирает `mediator.json` сам] → pre-seed `drafts/v<N+1>.md` → dispatch writer → lint/citations → N+1;
   7. `N < max` без заземлённых блокеров → `accepted_early_on_v<N>` + баннер;
   8. `N == max` → `forced_exit_on_v<N>_with_remaining_issues`.
5. Медиатор: вход — issues всех ревьюеров; выход `reviews/v<N>-mediator.json` (`instructions[] {section_id, source_reviewer, category, severity, instruction, resolution?}`, `dropped[]`); md рендерит CLI. Приоритет: substance > form; внутри substance — накапливать.

### 4.6. Style Studio

Логика извлечения не меняется. Профили — в `config.plugin_data_dir/profiles/`. Выбор профиля — вопрос `Style` в AUQ-гейте фазы 4 (только если профили есть); `mode_binding` профиля применяется в том же вызове до фиксации режима (один `mode_selected`). `skills/style/SKILL.md` переводится на `mf style <subcommand>`.

---

## §5. Кодовый слой: пакет `scripts/memoforge/`

### 5.1. Структура и CLI

```
scripts/mf, scripts/mf.cmd                  # обёртки: launcher discovery → python <root>/scripts/memoforge/__main__.py "$@"
scripts/memoforge/
  __init__.py  __main__.py  cli.py
  phases.py  modes.py  limits.py  events.py  fallbacks.py   # источники истины (M11)
  state_io.py   # lock + read (utf-8-sig) + write (atomic + schema)
  schema.py     # тонкая обёртка над pip `jsonschema` (Draft 2020-12); без своего валидатора
  task.py       # new / resolve / list (цепочки резолва §2.5)
  machine.py    # next / report / autoclose / retry-бюджеты
  dispatch.py   # планы диспатча, рендер промптов, description, знаменатель N
  gates.py      # render / parse (text и AUQ-answers)
  sources.py    # register / pack / digest / freeze / liveness / verify / slice
  routing.py    # статическая таблица предпочтений, нормализация стран, оценка потребности в вызовах
  quotes.py     # extract
  lint.py       # draft lint (L-01…L-15)
  citations.py  # draft audit-citations (C-*)
  review.py     # validate / aggregate / mediator-from-issues
  revision.py   # revision next (§4.5 п.4)
  sufficiency.py# route (subset_u/subset_r, missing/weak, drafting_warnings)
  render.py     # md-виды из JSON
  docx/         # renderer (AST), oscola.py, validate
  finalize.py   # always-deliver (fallbacks.py) + tidy
  analyze.py    # events analyze; probe metrics
  style_profile.py
  prompts/*.md
  pylauncher.py # discovery python_cmd; резолв plugin_data_dir
  hooks_common.py # поиск активной задачи по cwd/CLAUDE_PROJECT_DIR (используют хуки и statusline)
```

Все подкоманды печатают JSON (`--human` для текста), exit 0/1/2; ни одного `python3`-хардкода в промптах.

### 5.2. Публичные подкоманды

`task new|resolve|list|cancel` · `next` · `report` · `state get|validate` · `gate render|parse` · `dispatch render` · `sources register|pack --freeze|digest|liveness|verify|slice` · `quote extract|skip` · `draft finish` (= anchor|lint|audit-citations) · `review validate|aggregate` · `revision next` · `sufficiency route` · `render <view>` · `docx render|validate` · `finalize [--salvage]` · `tidy` · `events log|analyze` · `agent log` · `style …` · `deps check|install` · `docs render [--check]` · `probe <name>|dry-run|metrics`.

### 5.3. Реестр источников и цитат (M5, M6)

`research/sources.json`: `{schema_version, sources: {<source_id>: {layer, title, citation_form, identifiers {celex?, ecli?, eli?, reporter_cite?, neutral?}, url, retrieved_at, retrieval_tool, provenance: agent_saved|confirmed (confirmed = `liveness` получил тело по `url` и его sha совпал с `raw_sha256`), tier, raw_path|null, raw_sha256|null, raw_chars, meta {in_force?, effective_date?, status?} (из ответа инструмента, как передал агент), liveness {status: ok|redirect|dead|changed|unchecked, code, checked_at}, verification {us: resolved|unresolved|ambiguous|n/a, us_by: agent|cli|null, eu_syntax_ok: bool, checked_at} (единая enum для всех предикатов C-04, digest'а гейта 11 и баннеров; `us_by` — только происхождение), currency {status, checked_at, note}|null, pack {role_by_issue{}, weight, confidence, use_in_memo}|null}}}`. Пишет только `sources.py` под `sources.lock` (протокол как у `state.lock`). `research/quotes.json`: `{quotes: {<quote_id>: {source_id, raw_sha256, text, char_start, char_end, lang, words}}, skips: [{section_id, source_id, reason, at}]}` (схема `quotes`).
`sources pack --freeze --step` — одна операция под `state.lock` → `sources.lock`: строит `research/source-pack.json` (роли/вес/уверенность из findings, currency, tier — без вывода веса из tier), фиксирует список `{source_id, raw_sha256}` snapshot'а, ставит `state.sources_frozen=true`, закрывает шаг. После этого `register` — exit 1; lint/audit/render читают только snapshot (регистрации, успевшие после pack, в snapshot не попадают и в меморандум не проходят).
**Верификации (до LLM, без новых сетевых клиентов):** `sources liveness` (stdlib `urllib` HEAD/GET, 10 с, best-effort); `sources verify` — оффлайн: синтаксис CELEX/ECLI/ELI/neutral citation по регексам, `in_force`/`effective_date` из метаданных, сохранённых при регистрации из ответа LDH (`--meta <json>`), дубликаты по нормализованным идентификаторам. Существование US-цитат проверяет **сам `currency-checker` через CourtListener MCP** (`extract_citations`/`analyze_citations`) и записывает результат командой `mf sources verify --set <source_id> us=resolved|unresolved|ambiguous` (CLI сохраняет статус в ту же enum и `us_by: agent`). **«Не найдено ≠ выдумано»:** `unresolved` не удаляет источник; исключает его из `use_in_memo=rule` без второго подтверждения, попадает в исключения гейта 11 и в приложение.
Сопоставление цитаты (C-02): **только точное совпадение** текста blockquote с фрагментом `quotes.json` после узкой типографской нормализации (NFKC, схлопывание пробелов, унификация кавычек/тире/многоточий); дополнительно тройное равенство `quote.raw_sha256 == snapshot[source_id].raw_sha256 == sha256(текущий raw-файл)` и совпадение текста в диапазоне `char_start..char_end` (подмена raw или диапазона → блокер, без автообновления snapshot). После freeze `quote extract` работает только по версии raw из snapshot и отказывает (`raw_changed`), если файл изменился. Fuzzy (`difflib`) используется только внутри `quote extract` для списка `candidates`, никогда — для успешного аудита. **Контракт extraction:** успех = точный диапазон, начинающийся и заканчивающийся на границах предложений, длиной ≤`max-words`; иначе ошибка без `quote_id` (`too_long` → `candidates` из sentence-bounded подфрагментов ≤`max-words`, если они существуют; `ambiguous` → позиции всех совпадений; `not_found`; `no_raw`; `raw_changed`). `mf quote skip --section <id> --source <id> --reason too_long|ambiguous|not_found|no_raw|raw_changed|already_used|other --note "<текст, обязателен при other>"` фиксирует в `quotes.json` осознанный отказ от цитаты для секции (`already_used` — единственный подходящий диапазон уже процитирован в другой секции; `other` — свободная причина, попадает в `drafting_warnings`).

### 5.4. Lint (`draft lint`; 14 правил, числа из `limits.py`)

L-01 предложения >40 слов; L-02 абзацы >3 предложений или >100 слов; L-03 em-dash вне «Term — definition»; L-04 AI-tells (`lib/ai-tells.txt`); L-05 уровни заголовков (H1→H2→H3, без H4/пропусков; заголовок не заканчивается `?`); L-06 биекция Exec Summary ↔ подсекции ↔ Conclusion; L-07 Risk-line формат и наличие; **L-08 blockquote: ≤1 на подсекцию, каждая с `[[q:]]`; ≥1 обязательна только если у секции есть `[[src:]]` с raw и для этой пары (секция, источник) нет записи `quote skip`** (иначе `major` + строка в `drafting_warnings`; отказ по `skip` — законный исход, lint не требует перебора подстрок); L-09 один и тот же фрагмент — не более одного раза в драфте, где фрагмент = `(source_id, raw_sha256, char_start, char_end)` (новый `quote_id` на тот же диапазон не обходит правило; один источник может цитироваться в нескольких секциях разными диапазонами); L-10 word cap для `executive-brief`: `слова тела + len(unique [[src:]]) × 12` ≤ 1200 (`major`); L-11 плейсхолдеры; L-12 секции шаблона и порядок; L-13 Exec Summary — буллеты ≤40 слов с `Risk:`; L-14 дисклеймер при `assumptions_accepted=false`. «Noun-phrase заголовки» и «конкретность рекомендации» — пункты чеклиста `form-reviewer`, не lint. Четырёхбитная структура §4.4 допускает замену quote-бита пересказом с `[[src:]]` при любой записи `skip` для (секция, источник); чеклист `form-reviewer` содержит пункт «quote present OR skip recorded», чеклист `counterargument-reviewer` не зависит от skip. Fixture: две секции с единственным допустимым диапазоном одного источника — первая цитирует, вторая законно использует skip. Выход `lint.json` `{draft_sha, clean, findings[{rule, severity, line, section_id, excerpt, hint}]}`; `clean` = нет `blocker`.
`draft audit-citations` (все правила применяются и к источникам, полученным через `[[q:]]`→`source_id`): C-01 `[[src:]]`/`[[q:]]` в реестре и snapshot; C-02 `[[q:]]` == raw-фрагмент точно + `raw_sha256` совпадает; C-03 не `do_not_use`; C-04 `manual_check`/`unresolved` не в Risk-line/Exec Summary; C-05 freeze (источник в snapshot, `use_in_memo ≠ do_not_use`); C-06 pinpoint-формат; C-07 неиспользованные `rule`-источники (`major`, информационно). Выход `citations.json` того же вида, с `draft_sha`.

### 5.5. Docx-рендер (`docx/`)

- Парсер: `mistune` AST → хелперы python-docx (Arial 12, 1″, justify, 6pt, cyrillic rFonts).
- `[[src:id pinpoint]]` и каждая blockquote с `[[q:id]]` (через `source_id` цитаты) → цитата, форматируемая единственным билдером `oscola.py` (OSCOLA 5th, **D-150**): `compact` при первом упоминании инструмента (акта, решения, документа — не записи статейного реестра) и `short` при любом последующем упоминании любой его статьи, по классу источника (EU-legislation, национальное законодательство, CJEU, иные суды, soft law, доктрина), который определяется по `identifiers`/`meta`/`citation_form`; в теле ни одна форма не несёт URL, CELEX/ELI/ECLI и `retrieved …` (фолбэк при пустых полях — обрезанный `citation_form`, не склейка title+form), пинпойнты нормализованы (`art 6(1)(b)`, `para 89`, `s 2(1)`, `§ 26`), длина ≤ 120 символов. Стиль цитирования — `citation_style` (front-matter шаблона, поверх него `config.citation_style`): **`inline`** (дефолт обоих шаблонов) даёт `(<цитата>)` гиперссылкой на зарегистрированный URL (`w:hyperlink` в docx, `[text](url)` в md) и не создаёт сносок; **`footnotes`** даёт настоящую сноску Word (`footnotes.xml`, `w:footnoteReference`, стили в шаблоне) на каждое упоминание, с обратной ссылкой `<short name> (n <номер первой сноски этого инструмента>)` в короткой форме. Два подряд идущих упоминания одного инструмента с тем же местом → `ibid` (footnotes) или снятие второй цитаты (inline). `[[q:id]]` удаляется из blockquote, её цитата выносится в строку атрибуции под цитатой. §Sources несёт по одной записи на инструмент в порядке первого цитирования: `<цитата инструмента> — CELEX/ECLI/ELI — cited at <все места, в порядке упоминания> — <канонический URL, без якоря статьи> — checked <date>, currency <status>` (статья со статусом `amended`/`repealed` называется отдельно); статейные заголовки не печатаются, инлайн-ссылка при этом может вести на якорь статьи; нумерация — по первой сноске (footnotes) либо по порядку цитирования (inline); §Sources и приложение «Assumptions & Unverified Sources» генерируются из snapshot, `drafting_warnings`, `verification`.
- Списки: новый `w:num` на список; `2. Text` вне списка — абзац; вложенные bullets; экранирование `\|`; склейка soft-wrap.
- Баннеры статусов — как в v1. **Fallback (stdlib-only, без AST):** при отсутствии `python-docx`/`mistune` или исключении в рендере — `deliverable.md`: регекс-замена `[[src:…]]`/`[[q:…]]` на цитату того же стиля (`[n]` в footnote-стиле) с генерацией §Sources из snapshot; неизвестный/нерезолвимый id → `[unresolved: <id>]` + баннер; pandoc-ветка удаляется.
- `docx validate`: архив открывается python-docx; в `document.xml.rels` есть relationship на `footnotes.xml`; каждая `w:footnoteReference` имеет footnote с тем же id и ровно одну; число footnotes == число разрешённых упоминаний; каждая короткая форма ссылается на номер полной формы **того же инструмента** (`instrument` в карте сносок; карта строится рендерером и сверяется, для старых карт — по `source_id`); нет литеральных `[[src:`/`[[q:`/`[unresolved:` ни в одной части; стили `FootnoteText`/`FootnoteReference` присутствуют; каждая `w:hyperlink` ссылается на существующий relationship типа hyperlink в `document.xml.rels` (D-150). Провал → docx переименовывается в `memo-<slug>.invalid.docx`, главным deliverable становится md-fallback, баннер `docx_invalid`. Полная OPC/XSD-валидация не делается (минимализм §0.3a).
- Golden-тесты `document.xml`/`footnotes.xml`. Решение «python-docx + OXML, не docx-js» — ADR-05 в `docs/decisions.md`.

### 5.6. Зависимости и упаковка

`requirements.txt`: `jsonschema>=4.18`, `python-docx>=1.1`, `mistune>=3.0`; optional `rapidfuzz`. **Bootstrap без зависимостей:** `scripts/mf` (bash) и `scripts/mf.cmd` (cmd) сами перебирают `python` → `python3` → `py -3` (проверка `-c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)"`, Store-alias отсеивается по коду возврата), вычисляют `plugin_data_dir` по цепочке §2.5 (`${CLAUDE_PLUGIN_DATA}` → `%LOCALAPPDATA%/claude/plugin-data/memoforge` / `~/.claude/plugin-data/memoforge` → `<plugin_root>/.data`; та же цепочка в `pylauncher.py`, тест на идентичность), добавляют `<plugin_data_dir>/site-packages` в `PYTHONPATH` и запускают `__main__.py`; результат discovery кэшируется в `<plugin_data_dir>/launcher.json`. Команды, работающие **без** `jsonschema`: `deps check|install`, `task list|resolve`, `finalize --salvage`, `events log`; остальные печатают подсказку `mf deps install`. Без `python-docx`/`mistune` — md-fallback экспорта (§5.5). `hooks/ensure_deps.py` (SessionStart) — только проверка и запись `deps.json`; ничего не устанавливает. Хуки и `subagent_statusline.py` — stdlib only. Тесты launcher'а: путь с пробелами, Store-alias `python3`, Windows только с `py`, Ubuntu только с `python3`, отсутствие `jsonschema`. Python ≥3.9. `.gitattributes`: `* text=auto`, `*.py/*.sh/*.md/*.cmd text eol=lf` (`*.cmd` — crlf). `.gitignore`: `outputs/`, `*.docx` (кроме `docs/`), `*.tmp`, `dist/`, `__pycache__/`.

---

## §6. Схемы артефактов (`schemas/`, draft 2020-12, валидатор — `jsonschema`)

Нормативная таблица «артефакт → схема → писатель → читатели» (схемы переиспользуются; `test_expected_outputs_have_schema.py` сверяет её с `dispatch.py`):

| Артефакт | Схема | Пишет | Читают |
|---|---|---|---|
| `state.json` | `state` | CLI | все |
| `plan.json` (`issues[]{issue_id, title, question, jurisdictions[]}`, `jurisdictions[]`, `doctrine_required`, `estimated_complexity`, `classification`, `notes`) | `plan` | оркестратор (inline-llm) | CLI, агенты |
| `intake/mcp-probe.json` (`{namespaces: {ldh?, courtlistener?, other[]}}`) | `mcp-probe` | оркестратор (inline-llm) | CLI, researcher |
| `intake/questions.json` | `intake-questions` | analyst | CLI |
| `intake/preliminary-sources.json` | `research-findings` (подмножество) | analyst | researcher |
| `intake/user-facts.md`, `followup-response.json` | `gate-answers` (json) / md — рендер CLI | CLI | writer, sufficiency |
| `research/<layer>.json` | `research-findings` | researcher | CLI, sufficiency, currency, writer (через md) |
| `research/sources.json`, `research/quotes.json` | `sources`, `quotes` | CLI | CLI, auditor, docx |
| `research/research-sufficiency.json` | `research-sufficiency` | sufficiency-reviewer | CLI |
| `research/currency.json` | `currency` | currency-checker | CLI |
| `research/source-pack.json` (snapshot) | `source-pack` | CLI | writer (md), auditor, docx |
| `drafts/v<N>.md` | не JSON — lint + audit-citations | writer | ревьюеры, docx |
| `reviews/v<N>-<kind>.json` | `review` (`oneOf` по kind + stub-вариант) | ревьюеры/CLI | CLI, mediator |
| `reviews/v<N>-mediator.json` | `mediator` | mediator/CLI | writer (md) |
| `reviews/final-client-readiness.json` | `client-readiness` | reviewer | CLI, writer (polish) |
| `lint.json`, `citations.json` | `lint` | CLI | writer, aggregate |
| `steps/<step>/a<n>/<slot>/done.json` | `done-marker` (`{task_id, step_id, attempt, slot, input_sha{}, output_sha{}}`) | агент (`agent log --state done`) | CLI (`report`/autoclose сверяют `input_sha` с входами выданной попытки и `output_sha` с файлами) |
| `events.jsonl` | `events` (генерируется из `events.py`) | CLI, хуки | analyze |
| `profiles/<name>/meta.json` | `style-meta` | style CLI | style, gate 4 |
| `<plugin_data_dir>/{deps,launcher}.json`, `lib/routing/discover-cache.json` | `internal` (минимальная схема) | CLI | CLI |

Три уровня проверки выходов в `report`: (1) **completion** — marker + существование attempt-файлов (все агенты); (2) **структурная валидация** — JSON по схеме (все JSON-выходы), для `drafts/*.md` — непустой файл с обязательными заголовками шаблона (`draft anchor` проходит); (3) **quality gate** — lint/citations (только драфты, отдельным `script`-шагом после публикации). Исход провала (1)–(2) по типу агента: ревьюеры → ретрай `reviewer_json_retry` → stub; analyst/sufficiency/currency/mediator/client-readiness → `single_dispatch_retry` → деградация по §2.1; writer → `single_dispatch_retry` → `failed` (v1) или «продолжить с прежней версией» (fix/polish). Схемы содержат `$schema`, `$defs`, `oneOf`; `additionalProperties:false` только в самодостаточных ветках. Исключение из M4/`test_agent_outputs_are_json_only`: авторские markdown-артефакты — `drafts/*.md` (writer) и файлы стиль-профиля `prose-style.md`/`template.md` (style-extractor) — это первичные тексты, а не md-виды JSON.

---

## §7. Прогресс и наблюдаемость

### 7.1. Удаляется
Четыре канала v1 целиком (см. §0.4): рендерер, контракты, секции в агентах, `mcp__cowork__update_artifact` из `tools`, visualize-виджеты, обязательный `TodoWrite`, `mark_chapter`, hook-блоки auto-allow cowork/visualize, `state.live_progress`, `widgets/`, `live-progress*.html`.

### 7.2. Нативный стек (M7)

| Слой | Механизм | CLI | Cowork | Кто пишет |
|---|---|---|---|---|
| A | `description` `Agent`-вызова = `P<n>/<N> · <agent> · <label>` | ✓ | эмпирически подтверждено до смены артефактной системы 19.08.2026; перепроверяется P8 | CLI |
| B | Одна строка `chat_line` между шагами (в Cowork — буферизуется до конца turn, служит итогом сегмента) | ✓ | частично | CLI генерирует, LLM печатает |
| C | Хуки `progress_logger.py`: `PreToolUse(^(Agent\|Task)$)` → событие `subagent_requested {description}`; `SubagentStart/Stop (^memoforge:)` → `subagent_started/stopped {agent_id, agent_type, result}`. **Только append в `events.jsonl`** (файл найден через `hooks_common`); state не трогают. **Единый writer-протокол журнала (CLI и хуки, `events.py`/`hooks_common`):** запись под advisory-локом `events.lock` (тот же механизм, что §2.2): захват → если файл не заканчивается `\n`, дописать `\n` (оборванный хвост становится отдельной невалидной строкой) → append одной строки ≤4 КБ → unlock. Семантика хук-событий — **at-least-once с дедупликацией при чтении** по `event_key`: `PreToolUse`/`PostToolUse` — `sha1(session_id+event+tool_use_id)`, `SubagentStart/Stop` — `sha1(session_id+event+agent_id)`; для `PreCompact`/`SessionStart` стабильного host-идентификатора нет — эти события best-effort без обещания точной дедупликации. Порядок под `events.lock`: (1) если `events/.seen/<event_key>` уже существует — выйти без записи; (2) append строки; (3) **после успешной записи** создать маркер (`O_CREAT\|O_EXCL`). Crash между (2) и (3) даёт в худшем случае дубль строки (второй интерпретатор допишет ещё раз), который снимает дедупликация при чтении (`events analyze`, `state`); потеря события невозможна. `.seen/` живёт до `tidy`. Читатели пропускают невалидные строки, включая последнюю | ✓ | P5/P9 | хук |
| C′ | CLI: `next` пишет `step_issued {step_id, attempt, agents}` (гарантированный след старта), `report` — `agent_returned {step_id, slot, duration_seconds}`; корреляция с хук-событиями — по `agent log --state start` и по времени; типы событий C и C′ различны, между собой не дедуплицируются. Запись — тем же writer-протоколом под `events.lock`, что и хуки (слой C); при crash между записью state и append события следующий `next` дописывает недостающее `step_issued` по `steps[]` (событие помечено `recovered:true`) | ✓ | ✓ | CLI |
| D | `subagentStatusLine` (плагинный `settings.json` → `scripts/subagent_statusline.py`): использует **только** `tasks[].description/label/startTime` (формат слоя A) — файлов не читает | ✓ | не подтверждено, безвредно | скрипт UI |
| E | `logs/<agent_type>-<slot>.log` через `mf agent log` (Bash, best-effort) — для `events analyze` и отладки | ✓ | ✓ | агент |
| F | `mf events analyze`: таймлайн, серийность, `silent_gap`, «events went dark» | ✓ | ✓ | CLI |
| G | Полные `**Progress —**` блоки — только на гейтах и в финале | ✓ | ✓ | CLI |
| H | Опционально (P3): Artifact-дашборд, §7.5 | ✓ | P3 | LLM 1 вызов/шаг |

Итого обязательных действий оркестратора ради прогресса: печать `chat_line`. Гарантированный след — `step_issued`/`agent_returned` от CLI; у субагента обязательны только `agent log --state start|done` (это completion-контракт, а не прогресс); хуки и `agent log --state step` — дополнительные best-effort каналы (их отсутствие не ломает ни аудит, ни пайплайн; `events analyze` помечает прогон `hooks_absent`/`agent_logs_absent`).

### 7.3. Гранулярность (измеряемая цель, не гарантия рантайма)
Цель: автономный сегмент не идёт >5 мин без нового `step_issued` (`events analyze` → `silent_gap`, фиксируется в `docs/probes/`). Writer v1 — один диспатч + `agent log` по секциям (best-effort).

### 7.4. Cowork: буферизация чата (#26805 открыт)
GATE-END на текстовых гейтах сохраняется как гарантированный flush.

### 7.5. Опциональный дашборд через `Artifact` tool (только при P3 = PASS в обоих окружениях)
Оркестратор один раз публикует статичную страницу (`capabilities: {db: {}}`), подписанную на документ `run/state` (одна страница на прогон); далее **LLM делает один вызов `Artifact write_db` на шаг** с payload из ответа `next` (`dashboard_patch`). Это +1 обязательное действие LLM ⇒ G8 = 2 при `dashboard: on`. CLI/хуки Artifact не трогают (у него нет CLI/API). Флаг `userConfig.dashboard`, default `on` (D-92).

---

## §8. Хуки, права, платформа

### 8.1. `hooks/hooks.json` (генерируется `hooks/build_hooks.py`; **каждый command-хук — в трёх exec-form вариантах `python`, `python3`, `py -3`**; двойное/тройное срабатывание дедуплицируется маркером `events/.seen/<event_key>` — §7.2 C; для `permission_gate` повтор безвреден, т.к. результат детерминирован; JSON проверяется тестом)

| Событие | Matcher | Хук | Назначение |
|---|---|---|---|
| `PreToolUse` | `^WebFetch$\|^mcp__.*fetch$` | `permission_gate.py --mode fetch` (exec-form `{"command":"python","args":["${CLAUDE_PLUGIN_ROOT}/hooks/permission_gate.py","--mode","fetch"]}`; inline-fallback генерируется build-скриптом на случай провала P2) | allow по `allowlist.txt` (suffix-match с точкой) **только** по ключам `url`,`uri`,`link`,`href`; иначе `{}` |
| `PreToolUse` | `^WebSearch$` | тот же, `--mode websearch` | allow только при активной задаче memoforge (по `hooks_common`) и `userConfig.websearch_autoallow` |
| `PreToolUse` | `^Bash$\|^mcp__workspace__bash$` | `--mode bash` | **auto-allow** только если команда после `shlex`-разбора (posix и Windows-варианты) — ровно один вызов без операторов `&&`, `;`, `\|`, `\|\|`, redirect'ов, `$( )`, backticks, переносов строк, и её исполняемый файл после нормализации пути равен `<abs>/scripts/mf` или `<abs>/scripts/mf.cmd` (абсолютный путь плагина; bare `mf.cmd` не принимается), а аргументы не содержат метасимволов; иначе `{}` (обычный permission-flow, не запрет) |
| `PostToolUse` | `^mcp__` (фильтр по алиасам серверов в хуке, D-134) | `progress_logger.py --mcp` (`async: true`) | событие `mcp_call {server, tool}` (только учёт; LDH/CourtListener разрешает пользователь) |
| `PreToolUse` | `^(Agent\|Task)$` | `progress_logger.py --pre` (`async`) | `subagent_requested {description}` |
| `SubagentStart`, `SubagentStop` | `^memoforge:` (fallback при провале P9: перечисление `^memoforge:(legal-researcher\|…)$`, генерируется из списка агентов) | `progress_logger.py` (`async`) | `subagent_started/stopped` |
| `Stop` | — | `stop_guard.py` | §8.3 |
| `SessionStart` | `startup\|resume` | `ensure_deps.py` (`async`) | проверка зависимостей |
| `PreCompact` | — | `progress_logger.py --compact` | событие `context_compacted` |

Все скрипты: stdlib, `utf-8-sig`, exit 0 + JSON, «нет активной задачи или `schema_version != 2` → `{}`»; тесты в `test_hooks.py` (обходы из 03 §6, не-dict `tool_input`, не-JSON stdin). При провале P2 без exec-form: `permission_gate` — inline-fallback; `progress_logger`/`stop_guard` — деградация: слой C отсутствует (C′ достаточно для аудита), M9 обеспечивается только `finalize` (фиксируется в `docs/probes/`). Allowlist: `justia.com`, `findlaw.com`, `iapp.org` — группа `optional`, по умолчанию выключена.

### 8.2. Permissions (CLI и Cowork)
Плагин не может поставлять permission-правила. `mf docs render permissions` генерирует README-блок для `~/.claude/settings.json` из `allowlist.txt` (все хосты, без `…`): `WebFetch(domain:<host>)` + `WebFetch(domain:*.<host>)`, `mcp__plugin_memoforge_legal-data-hunter__*`, `mcp__plugin_memoforge_courtlistener__*`, `mcp__plugin_memoforge_legalviz__*`, `mcp__plugin_memoforge_uk-legal__*`, `mcp__plugin_memoforge_justicelibre__*`, `mcp__plugin_memoforge_opencaselaw__*`, `Bash(<abs>/scripts/mf *)`; правило `Agent(memoforge:*)` не включается (глобы для Agent не подтверждены). README прямо предупреждает: **в Cowork Bash/WebFetch — `mcp__workspace__*`, allow-правила на них не действуют, защищает только хук §8.1.** Тест `docs render permissions --check` валидирует блок.

### 8.3. Stop-guard (M9)
`stop_guard.py` (read-only к state): если активная задача в нетерминальной не-гейтовой фазе — `{"decision":"block","reason":"memoforge: task <id> is in phase <p>; run `mf next` to continue or `mf finalize --reason interrupted`"}` (блокирует и при закрытом последнем шаге: пока фаза нетерминальна и не гейт, работа не окончена). Счётчик блокировок — по событиям `stop_guard_blocked` в `events.jsonl` (≤2 на фазу, затем `stop_guard_gave_up`). Несколько активных задач → та, чей `work_dir` совпадает с cwd/`CLAUDE_PROJECT_DIR`, иначе `{}`. Это дополнительная защита (M9 на неё не опирается); включается `userConfig.stop_guard` (default `off`) до подтверждения P7.

### 8.4. `plugin.json`
`name`, `displayName`, `version: 2.0.0`, `description`, `author`, `repository`, `homepage`, `license: MIT`, `keywords`, `userConfig`: `output_folder` (directory), `writer_model` (string, default `opus`; валидируется `limits.ALLOWED_WRITER_MODELS`), `source_review_gate` (string `auto|on|off`, default `auto`), `dashboard` (boolean, true), `stop_guard` (boolean, false), `websearch_autoallow` (boolean, true). Хост экспортирует `CLAUDE_PLUGIN_OPTION_*` только в процессы хуков, поэтому SessionStart-хук зеркалит их в `<plugin_data_dir>/options.json`, откуда их читает CLI (цепочка §2.5); `mf config show|set|unset` — тот же файл. Плагинный `settings.json`: только `subagentStatusLine`. `workflows/` объявляется только при включённом пилоте.

---

## §9. Тест-план и CI

- **Юнит:** `phases/modes/limits/events/fallbacks` (единственность; каждая фаза схемы — в `phases.py`); `state_io` (12 процессов инкрементируют счётчик → финал 12, 0 торн-JSON; schema-reject не меняет файл; процесс, убитый под локом, не блокирует остальных; порядок вложенности локов проверяется); `schema.py` (positive/negative на каждую схему из таблицы §6 через `jsonschema`; `review` `oneOf` принимает валидные документы всех kinds и документ с 7 issues); `machine.py` (для каждой фазы × режима × исхода — ожидаемый `next`; идемпотентность `next`/`report`; autoclose при готовых выходах; все бюджеты `attempts.*`; частичный провал исследователей; `test_all_reviewers_failed_is_not_approved`; `test_reviewers_not_dispatched_until_lint_clean_or_rounds_exhausted`; `test_report_clears_active_without_subagent_stop`; `test_progress_denominator_matches_route` Brief/Full); `gates.py` (все форматы, `--answers` для AUQ, ошибки, `cancel_requested`); `dispatch.py` (рендер промптов, layer-строка, `description`, знаменатель); `task.py` (цепочки резолва, пустой `$HOME`, slug); `sources/quotes` (параллельная регистрация, идемпотентность, freeze, освобождение `sources.lock` после смерти процесса, `quote extract` контракт: not_found/ambiguous/too_long/no_raw, языки); `lint.py` (каждое из 14 L-правил positive/negative, юникод, L-08 без raw → major, fixture «две подсекции — один raw-источник» проходит L-08+L-09); `citations.py` (каждое C-правило); `review/revision` (агрегация с deterministic-блокерами: `test_unresolved_citation_blocker_prevents_approved_even_if_all_reviewers_approve`; валидатор чеклиста: пропущенный hard_fail, дубль id, неизвестный id, все `unknown`, валидный stub; порядок веток 1–8; `test_one_failed_substance_reviewer_never_yields_approved` для обеих итераций; regression только при равном покрытии и новом заземлённом блокере; fixtures split/merge и противоположные issues → `conflict`); `machine.py` дополнительно: `test_autoclose_rejects_previous_attempt_output`, `test_preseeded_draft_is_not_completed_revision`, `test_partial_dispatch_resume_keeps_accepted_slots`, `test_late_superseded_result_rejected`, `test_crash_between_action_and_step_close_is_idempotent` (для каждого `--step`-command), `test_cancel_after_interruption_in_each_autonomous_phase`, `test_polish_changes_go_through_lint_and_citations`, `test_auq_no_answer_switches_to_equivalent_text_gate`, `test_currency_regate_route_to_followup_gate`; `state_io`: взаимное исключение двух процессов на Windows и POSIX (счётчик), `lock_order_violation`; `sources`: concurrent `register` против `pack --freeze`; `hooks`: три интерпретатора, конкурентный append, оборванный хвост; `finalize`: crash/без зависимостей/повреждённый state → salvage создаёт deliverable+summary; `sufficiency.py`; `render.py` (M4: каждый JSON → md); `docx` (golden, сноски, OSCOLA короткие формы, restart нумерации, вложенные списки, soft-wrap, `docx validate`, md-fallback с токенами); `hooks` (обходы, формы входа, exit-коды, три варианта python/python3/py, bash-gate); `pylauncher`; `finalize` (каждый терминальный путь и каждая строка `fallbacks.py` оставляет deliverable + summary); `analyze`.
- **Контрактные:** `test_agent_frontmatter.py` (model/tools/effort ↔ `lib/models.md`; у MCP-агентов нет `tools:`; ни у кого нет запрещённых tools); `test_prompts.py`; `test_agent_outputs_are_json_only.py` (ни один промпт не просит `.md`-вид); `test_docs_generated.py`; `test_no_legacy.py` (область: `skills/`, `agents/`, `lib/`, `scripts/`, `hooks/`, `templates/`, `schemas/`; исключения `docs/attic/`, `docs/postmortems/`, `CHANGELOG.md`; идентификаторы: `heartbeat`, `visualize`, `update_artifact`, `TodoWrite`, `mark_chapter`, `research-summary-only`, `Quick/Standard/Deep`, `${CLAUDE_PLUGIN_DATA}/work`, `python3 "`); `test_schema_fields_documented.py`.
- **Сквозной dry-run:** `mf probe dry-run --mode full|brief` с fixture-выходами от `task new` до `done`; проверяет G2 и инварианты. `mf probe metrics` печатает G1/G2/G7.
- **CI:** GitHub Actions matrix `ubuntu-latest, windows-latest × 3.9, 3.13`; `pip install -r requirements.txt`; `python -m compileall`; `python -m unittest discover -s scripts/tests -v` + страж «0 skipped»; `ruff check` (E/F/W); `mf docs render --check`; `mf probe dry-run` оба режима; валидатор манифеста (`claude plugin validate --strict` при наличии CLI); проверка «version == README badge == CHANGELOG».
- **Пробы (§11)** — честная фиксация результатов в `docs/probes/v2-probes.md`.

---

## §10. Пилот Workflow (за флагом)

`workflows/research.js` покрывает **только S3a** (фазы 5–6); `workflows/draft.js` — S4. S3b не представляется скриптом (маршрут 9→6 выходит из сегмента). `meta.phases` = фазы сегмента; каждый `agent()` получает путь к промпту, отрендеренному `mf dispatch render --step …`; агент **всегда** вызывает `mf report` в конце и обязан включить его ответ (`accepted`) в свой финальный текст; скрипт после фазы читает `steps[]` через агента-проверяющего и решает о ретрае. Критерии принятия: P1 = PASS в целевом окружении; прогресс виден в `/workflows`/Background tasks; `events analyze` показывает те же переходы, что router-путь. До этого `workflows/` не объявляется.

---

## §11. Пробы платформы (ДО соответствующих решений; результаты — `docs/probes/v2-probes.md`, методика пауз 25 с — из архивной v0.5.0-процедуры)

| ID | Вопрос | Метод | Влияет на |
|---|---|---|---|
| P1 | Plugin-workflow `/memoforge:<name>` в Cowork и CLI; прогресс | `workflows/ping.js` с 2 фазами | §10 |
| P2 | Exec-form хук с `${CLAUDE_PLUGIN_ROOT}` и `python`/`python3` на Windows (Cowork, CLI) | `hooks/probe_echo.py` → лог в `plugin_data_dir` | §8.1 |
| P3 | `Artifact` из plugin-skill в Cowork; `write_db` из фонового субагента; живое обновление в UI | `write_db` → sleep 25 → `write_db` → sleep 25 | §7.5 |
| P4 | `AskUserQuestion` после **любого** `Agent`-диспатча в Cowork (в т.ч. после одиночного) — silent-fail? | 1 dispatch + AUQ; 3 параллельных + AUQ | §2.4 (гейт плана; миграция intake) |
| P5 | `SubagentStart/Stop` срабатывают в Cowork для `memoforge:*`; **payload** (есть ли `initial_prompt`/`last_assistant_message`) | `progress_logger` → `events.jsonl` | §7.2 C |
| P6 | Вложенный спавн из плагинного агента в Cowork | `Agent(Explore)` из агента | резерв |
| P7 | Stop-hook `decision: block` — re-entry на Windows CLI и Cowork | `stop_guard.py` тестовый режим | §8.3 |
| P8 | Чат-флаш в Cowork посреди turn; видна ли плитка `Agent` с `description` | 3 строки текста + 2 `Agent` | §7.2 A/B, §7.4 |
| P9 | Matcher-семантика: префикс `^memoforge:`; `^Agent$` vs `^Task$`; **эффективный tool pool плагинного агента**: (a) без `tools:` + `disallowedTools: Agent, Task, AskUserQuestion, mcp__cowork__*` — агент должен видеть MCP-инструменты и **не** видеть перечисленные (агент-эхо печатает список своих инструментов); (b) `tools: Read, Write, Bash, mcp__*` — наследуются ли MCP. Проверяется в CLI и Cowork | тестовый агент-эхо + хуки-эхо | §4.1, §7.2 C, §8.1. **Fallback при провале (a):** если `disallowedTools` игнорируется — использовать вариант (b), если он прошёл; если оба провалились — MCP-агенты остаются без `tools:` (наследуют всё), а запрет `Agent/AskUserQuestion` обеспечивается только промптом, и это окружение помечается в README как «ограниченно поддерживаемое» до решения платформы |

---

## §12. План реализации (волны; каждая заканчивается зелёным CI)

| Волна | Содержание | Критерий готовности |
|---|---|---|
| W1 | Пакет `scripts/memoforge/` (все модули §5.1 с fixture-агентами), схемы, `mf`/`mf.cmd`, тесты пакета, CI, requirements. **Старые скрипты и их тесты остаются** (CI гоняет оба набора) | dry-run Full и Brief от `task new` до `done`; ≥180 новых тестов; 0 skipped; старые 171 зелёные |
| W2 | Хуки + `build_hooks`, `settings.json` + statusline; **пробы P2, P4, P5, P7, P8, P9** (P4 — до принятия AUQ-пути гейта плана; text-fallback тестируется независимо от исхода P4) | результаты проб записаны; хуки протестированы |
| W3 | Агенты v2 (12) + `lib/agent-core` + чеклисты + `lib/prose-style.md` + шаблоны; `test_agent_frontmatter`, `test_prompts` | все выходы валидируются схемами на fixtures |
| W4 | Router `skills/memo`, `continue`, `status`, `style` → на `mf`; **удаление старых скриптов вместе с последними вызывающими и их тестов**; README/CHANGELOG/docs render; `test_no_legacy` | реальный Brief-прогон в CLI: `done`; G1–G3 измерены `probe metrics` |
| W5 | Docx AST-рендер со сносками, OSCOLA, `docx validate`, golden-тесты; **финальная приёмка G1–G10** | реальный Full-прогон в CLI и Cowork; G7 зафиксирован; G9 ≥250 |
| W6 (опц.) | P1, P3 → пилот Workflow, дашборд | только при PASS проб |

---

## §13. Обоснования решений и источники (полные ссылки — `analysis/05-best-practices-research.md`)

| Решение | Основание |
|---|---|
| Control-flow в коде, LLM в «листьях» (§1, §3) | Anthropic Workflow tool («The script holds control flow; agents hold judgment»); «Governance Decay» (arXiv 2606.22528): нарушения 0% → 30–59% после компакции; OpenAI Agents SDK / Google ADK / Temporal / Restate — детерминированная оркестрация, replay без повторных LLM-вызовов |
| Бинарные чеклисты + детерминированная агрегация (§4.5) | TICK/STICK (2410.03608), RocketEval (2503.05142), DeCE на legal QA r=0.78 vs 0.35 (2509.16093); Anthropic «Demystifying evals» |
| ≤2 итерации, вторая по заземлённым блокерам (§4.5) | Self-Refine (2303.17651); Huang et al. (2310.01798); AstroReview (2512.24754); flip rate 13.6% (2606.13685) |
| Судья ≠ автор, CoT до вердикта, перемешивание пунктов | self-preference (2410.21819); position bias в rubric-оценке 16–39% (2602.02219) |
| Детерминированная верификация цитат, «404 ≠ выдумано» (§5.3) | Stanford RegLab (2405.20362); Ovcharov (2606.00898); BriefCatch RealityCheck (rule-based слой + AI-слой); CourtListener `extract_citations`/`analyze_citations` через MCP |
| OSCOLA 5th, без ibid, ECLI (§5.5) | OSCOLA 5th (03.2026); нейро-символьный форматтер +32 pp на Bluebook (2505.02763) |
| CRRACC с обязательным контраргументом (§4.4) | Columbia/Georgetown/UNH memo checklists |
| Лимиты и роутинг MCP (§4.3) | legaldatahunter.com/docs/rate-limits; CourtListener REST v4; эмпирика 05 §5; BAILII copyright; Open Justice Licence |
| Меньше вопросов на intake, условный source-review (§2.4) | OpenAI Deep Research; Clarify-When-Necessary (2311.09469); Magentic-UI; Plan-Then-Execute (2502.01390) |
| Prompt caching для fan-out | code.claude.com/docs/en/prompt-caching |

---

## §конформанс. Карта соответствия (формат обязателен; заполняется по мере реализации)

| Правило | Код | Проверка |
|---|---|---|
| M1/M3 протокол, идемпотентность | `machine.py` | `test_machine.py` (idempotent next/report, autoclose), dry-run |
| M2 единственный писатель state, лок | `state_io.py`; хуки без записи state | `test_state_io.py` (lost-update = 0); хуки не меняют байты `state.json`: `test_hooks.py::test_the_hook_never_writes_state`, `::test_state_is_not_modified` |
| M4 JSON-only + рендер | `render.py`, схемы, промпты | `test_render.py`, `test_agent_outputs_are_json_only.py` |
| M5 провенанс цитат | `quotes.py`, `citations.py`, `docx/` | `test_quotes.py`, `test_citations.py`, golden docx |
| M6 freeze | `sources.py` (`pack --freeze`), `citations.py` C-05 | `test_sources.py::test_register_after_freeze_fails`, `test_concurrent_register_vs_freeze` |
| M7 прогресс пишет рантайм | `hooks/progress_logger.py`, `report` | `test_hooks.py`, `test_machine.py::test_report_emits_agent_returned` |
| M8 гейты | `gates.py`, `machine.py` | `test_gates.py`, `test_machine.py::test_no_gate_inside_segment` |
| M9 always-deliver | `finalize.py`, `fallbacks.py`, `stop_guard.py` | `test_finalize.py` (все терминальные пути и строки fallbacks) |
| M10 lint-first | `machine.py` (drafting) | `test_machine.py::test_reviewers_not_dispatched_until_lint_clean_or_rounds_exhausted` |
| M11 один источник истины | `phases/modes/events/limits/fallbacks.py`; `docs render --check` | `test_docs_render.py`, `test_phases.py` |
| M12 Windows | `mf`/`mf.cmd`, `pylauncher.py`, `state_io`, хуки ×3, CI windows | CI matrix, `test_hooks.py`, `test_launcher_wrappers.py` |

Изменение кода этих подсистем без сверки с таблицей = нарушение процесса.

---

## §диспозиции

### Раунд 0 — само-adversarial проход (2026-09-08; отчёт `analysis/07-spec-adversarial-r0.md`, вердикт SOUND-WITH-CHANGES, 70 находок)

**Итог: 68 ПРИНЯТО, 2 ПРИНЯТО ЧАСТИЧНО, 0 ОТКЛОНЕНО.** Все ✓-цитаты подтверждены ридером; расхождения были в агрегатах и в дизайне.

| # | Тег | Находка (кратко) | Диспозиция |
|---|---|---|---|
| F-01 | INCORRECT-CLAIM | «11 инцидентов, все — пропуск LLM» | ПРИНЯТО → §0.1 п.2: 12 инцидентов, 9 LLM / 3 инфраструктура |
| F-02 | INCORRECT-CLAIM | «167 MUST/NEVER/HARD RULE» | ПРИНЯТО → ≈165 по 5 паттернам, строго ≈73 |
| F-03 | INCORRECT-CLAIM/RISK | 95–110k без метода; G1/G2/G7 непроверяемы | ПРИНЯТО → §0.1 п.1 диапазон 70–110k с методами; §0.2 колонка «Как измеряется»; G7 — ориентир |
| F-04 | NIT | «8–12 контрактов 30–40k» | ПРИНЯТО → §0.1 п.1 |
| F-05 | INCORRECT-CLAIM | `doctrinal:81-91` склейка; потеряно правило WebSearch-as-primary | ПРИНЯТО → §4.3 таблица по layer, в промпт — только своя строка |
| F-06 | CONTRADICTION | `statutory-researcher.md` не в списках; 16→13 | ПРИНЯТО → §0.4 |
| F-07 | NIT | «подтверждено постмортемом» устарело | ПРИНЯТО → §7.2 A, P8 |
| F-08 | HOLE (блок.) | `report` бинарен | ПРИНЯТО → §3.1 `--agent <slot>`, §2.1 стр.5 частичный провал |
| F-09 | HOLE | `next` после `fail` | ПРИНЯТО → §3.1 таблица исходов; бюджеты §2.2 (общий `step_retry` заменён именованными бюджетами в раунде 1) |
| F-10 | HOLE | идемпотентность `report` | ПРИНЯТО → §3.1 |
| F-11 | HOLE | повторный `next` перезапускает диспатч; дубли источников | ПРИНЯТО → §3.1 п.3 autoclose; п.5 идемпотентный register |
| F-12 | HOLE/CONTRADICTION | `cancel` внутри сегмента; поле не объявлено | ПРИНЯТО → §2.2 `cancel_requested`; §2.4 (d); утверждение о закрытии 01 §6 п.3 снято |
| F-13 | HOLE | `attempts.*` не специфицированы; intake без бюджета | ПРИНЯТО → §2.2 таблица бюджетов |
| F-14 | HOLE | нет переходов в `failed`; client-readiness вердикты; overflow | ПРИНЯТО → §2.1 стр.14, абзац «Переходы в failed», `length_overflow` |
| F-15 | HOLE | deadlock lint (blockquote-правила) | ПРИНЯТО → §5.4 L-08 условие raw; §2.1 стр.12 исход «не сошлось»; доработано в раунде 1 (#33) |
| F-16 | HOLE | контракт `quote extract` | ПРИНЯТО → §4.4 |
| F-17 | HOLE (блок.) | CLI не знает про MCP | ПРИНЯТО → §2.1 стр.1 inline-llm `mcp-probe`; §2.4 |
| F-18 | HOLE | `always-deliver` без владельца | ПРИНЯТО → `fallbacks.py` (M11), генерация docs, `test_finalize` по строкам |
| F-19 | HOLE | GC для `active[]`/`steps[]` | ПРИНЯТО → §2.2 `progress` производное, TTL, очистка в `report` |
| F-20 | HOLE | stale `sources.lock` | ПРИНЯТО → §2.2 протокол лока (общий для state и sources) |
| F-21 | HOLE/CONTRADICTION | два писателя state | ПРИНЯТО (вариант «хуки пишут только events») → M2, §7.2 C, §8.1 |
| F-22 | CONTRADICTION | двойная эмиссия `agent_returned` | ПРИНЯТО → разные типы событий (C vs C′), без дедупликации |
| F-23 | HOLE | корреляция PreToolUse ↔ SubagentStart | ПРИНЯТО → `${step_id}`/`${slot}` в промпте + `agent log --state start`; P5 проверяет payload |
| F-24 | RISK | «не зависит от суммаризации» переоценено | ПРИНЯТО → M3 и §1 переформулированы |
| F-25 | CONTRADICTION (блок.) | гейты внутри S3 | ПРИНЯТО → §3.2 сегменты по гейтам; §10 только S3a/S3b |
| F-26 | CONTRADICTION | ревьюеры без Bash не могут логировать | ПРИНЯТО → §4.1 Bash у всех + bash-gate §8.1 |
| F-27 | CONTRADICTION | M7 «Write» vs Bash | ПРИНЯТО → M7 |
| F-28 | CONTRADICTION (блок.) | Artifact `write_db` из Python | ПРИНЯТО → §7.5 переписан; G8 = 2 при dashboard |
| F-29 | CONTRADICTION/RISK | exec-form `python` хардкод | ПРИНЯТО → §8.1 два варианта хуков; деградация описана; M12 |
| F-30 | CONTRADICTION | область `test_no_legacy` | ПРИНЯТО → §9 |
| F-31 | CONTRADICTION (блок.) | §0.4 неполон | ПРИНЯТО → §0.4 дополнен (style skill, 8 скриптов, 9 тестов, .mcp.json, probes) |
| F-32 | NIT | G4 «12 из 12» vs 14 схем | ПРИНЯТО → 14 из 14 |
| F-33 | AMBIGUITY | знаменатель `N` | ПРИНЯТО → §3.1 п.4 достижимые фазы; тест |
| F-34 | CONTRADICTION (блок.) | порядок веток; все упали = approved | ПРИНЯТО → §4.5 п.4 ветки 0–7 «первая совпавшая»; тест |
| F-35 | CONTRADICTION | M8 слабее реального риска AUQ | ПРИНЯТО → M8 «после любого диспатча», text-fallback гейта плана; P4 |
| F-36 | CONTRADICTION | `python -m memoforge` без sys.path | ПРИНЯТО → `next` возвращает абсолютный путь к `mf`; §5.1 обёртки |
| F-37 | AMBIGUITY | `plan.json` не определён | ПРИНЯТО → §6 |
| F-38 | CONTRADICTION | «≤2» vs `lint_fix_rounds` | ПРИНЯТО → §2.1 стр.12 |
| F-39 | AMBIGUITY | приоритет userConfig/режим | ПРИНЯТО → трёхзначный `auto|on|off`, §2.3 |
| F-40 | RISK | `mcp__*` в `tools` не верифицирован | ПРИНЯТО → §4.1 без `tools:` у MCP-агентов; P9 |
| F-41 | RISK | matcher'ы не верифицированы, нет пробы | ПРИНЯТО → P9; fallback-перечисление; `^(Agent\|Task)$` |
| F-42 | RISK | `Bash(...)`-правила не работают в Cowork | ПРИНЯТО → §8.1 bash-gate; §8.2 предупреждение; без `Agent(memoforge:*)` |
| F-43 | RISK | `${CLAUDE_PLUGIN_DATA}`/`$HOME` | ПРИНЯТО → §2.5 цепочки резолва + тесты |
| F-44 | RISK | subset валидатора vs `allOf`+`additionalProperties` | ПРИНЯТО → собственный валидатор убран, pip `jsonschema` (§5.1, §5.6); §6 `review` через `oneOf` |
| F-45 | RISK | `ensure_deps` ставит пакеты молча | ПРИНЯТО → только проверка; `mf deps install` |
| F-46 | HOLE | statusline не знает work_dir; один лог на 3 researcher | ПРИНЯТО → слой D только по `tasks[].description`; логи `<agent_type>-<slot>.log` |
| F-47 | HOLE | счётчик stop-guard негде хранить | ПРИНЯТО → события в `events.jsonl`; поведение без задачи |
| F-48 | RISK | Workflow-пилот и `report` | ПРИНЯТО → §10 |
| F-49 | CONTRADICTION | non-goal Style Studio vs правки | ПРИНЯТО → §0.3 переформулирован |
| F-50 | HOLE | профиль стиля исчезает у ревьюеров | ПРИНЯТО → `style-profile.md` в §4.2; читатели названы |
| F-51 | AMBIGUITY | `research-summary-only` в двух местах | ПРИНЯТО → категория «Архивируются» |
| F-52 | HOLE | резолв work_dir теряется | ПРИНЯТО → §2.5 `task.py`; `rel_work_dir` — не нужен, кликабельность от карточек |
| F-53 | HOLE | адрес `events.jsonl` | ПРИНЯТО → §2.2, `hooks_common` |
| F-54 | MISSING-TEST | M4 не в конформансе | ПРИНЯТО → §конформанс, §9 |
| F-55 | MISSING-TEST | G1/G2/G7 без процедуры | ПРИНЯТО → §0.2, `probe metrics` |
| F-56 | MISSING-TEST | skip = OK | ПРИНЯТО → CI «0 skipped» |
| F-57 | MISSING-TEST | кап ≤5 без исполнителя | ПРИНЯТО → рекомендация в промпте, не `maxItems`; тест 7 issues |
| F-58 | MISSING-TEST | L-06/L-15 без словарей; L-11 «including footnotes» | ПРИНЯТО → правила «noun-phrase заголовки» и «конкретность рекомендации» убраны из lint в чеклист form-reviewer (минимализм §0.3a); формула word cap в L-10 |
| F-59/60 | MISSING-TEST | дубли F-30/F-41 | ПРИНЯТО (через F-30, F-41) |
| F-61 | AMBIGUITY | маршрут после follow-up | ПРИНЯТО → §2.1 стр.7, `subset_u/subset_r` в §2.2 |
| F-62 | AMBIGUITY | `gate_answered` для AUQ | ПРИНЯТО → §2.4 `report --answers` → `gates.parse`; M8 |
| F-63 | AMBIGUITY | `--stdout`/`payload_ref` | ПРИНЯТО → §3.1 |
| F-64 | AMBIGUITY | пути к `lib/agent-core` | ПРИНЯТО → подстановка промптом, `test_prompts` |
| F-65 | AMBIGUITY | токены в md-fallback | ПРИНЯТО → §5.5 |
| F-66 | AMBIGUITY | `liveness` не в структуре | ПРИНЯТО → §5.3 |
| F-67 | AMBIGUITY | `…` в доменах; `lib/ai-tells.txt` не в §0.4 | ПРИНЯТО → §8.2 генерация; §0.4 |
| F-68 | NIT | `writer_model` без enum | ПРИНЯТО → `ALLOWED_WRITER_MODELS` |
| F-69 | NIT | `citation-auditor` opus без обоснования | ПРИНЯТО ЧАСТИЧНО → остаётся opus, обоснование добавлено в §4.1 |
| F-70 | NIT | regression недостижима в Brief | ПРИНЯТО ЧАСТИЧНО → сноска в §4.5 п.4 (regression-ветка 3); тест только Full |

Авторские правки сверх ридера: интеграция отчёта 05 (бюджет MCP, роутинг, чеклисты, условный source-review, OSCOLA, §13); по требованию владельца «без оверинжиниринга» — §0.3a и вырезание собственного JSON-Schema-валидатора, HTTP-клиентов CELLAR/GovInfo/legislation.gov.uk/CourtListener REST, deny-хука бюджета, словарных lint-эвристик.

### Раунд 1 — критика Codex (2026-09-08; `analysis/08-codex-r1.md`; модель gpt-6-astra, read-only)

**Вердикт: FLAWED.** 35 находок: **30 ПРИНЯТО, 5 ПРИНЯТО ЧАСТИЧНО (#2, #11, #14, #28, #34), 0 ОТКЛОНЕНО.** Codex подтвердил все `file:line`, commit, число агентов/фаз и явно заключил, что ядро ТЗ не является оверинжинирингом. Тесты в его read-only песочнице падали на создании temp-каталогов (107 errors) — ограничение среды, зафиксировано.

| # | Тег | Находка (кратко) | Диспозиция |
|---|---|---|---|
| 1 | HOLE | autoclose принимает старый результат за новый | ПРИНЯТО → completion marker `steps/<step>.<attempt>.<slot>.done` с sha выходов, пишет агент через `agent log --state done`; §3.1, §2.2 `steps[].attempt`; тесты |
| 2 | HOLE | resume parallel-dispatch повторяет живую работу | ПРИНЯТО ЧАСТИЧНО → per-slot закрытие, `attempt`, `superseded`; поздняя перезапись файла старым агентом не предотвратима на платформе — обнаруживается по sha marker'а и лечится переизданием слота (§3.1 п.3) |
| 3 | HOLE | идемпотентность `report` ≠ идемпотентность действия | ПРИНЯТО → все изменяющие команды принимают `--step` и закрывают шаг транзакционно; `script`/`gate` без `report`; пустой `expected_outputs` не autoclose; M3; тест crash-between |
| 4 | AMBIGUITY | конкурирующие бюджеты ретраев | ПРИНЯТО → таблица §2.2 с единицей учёта/инкрементом/сбросом; `step_retry` удалён; `attempt` вместо нового `step_id`; тесты считают диспатчи |
| 5 | HOLE | TTL-lock допускает двух владельцев | ПРИНЯТО → отбор только у мёртвого pid, token при release; тесты |
| 6 | HOLE | pack→freeze не атомарны | ПРИНЯТО → `sources pack --freeze` одной операцией под обоими локами, snapshot; M6 |
| 7 | HOLE | двойные хуки/конкурентный append событий | ПРИНЯТО → маркер `events/.seen/<event_key>`, append одной строкой ≤4 КБ, recovered `step_issued`; §7.2 C/C′ |
| 8 | HOLE | чеклист допускает необоснованное approve | ПРИНЯТО → валидатор: точный набор id, `unknown` на hard_fail ≠ approved, stub отдельный вариант; §4.5 п.2 |
| 9 | HOLE | детерминированные блокеры не участвуют в решении | ПРИНЯТО → aggregate включает lint/citations блокеры как `deterministic`; тест |
| 10 | HOLE | частичные stubs искажают approve/regression | ПРИНЯТО → неполное ревью → `manual_review_required`; rerun до all-failed; regression только при равном покрытии; §4.5 п.4 ветки 1–3 |
| 11 | RISK | дедуп/регрессия по числу блокеров | ПРИНЯТО ЧАСТИЧНО → дедуп только внутри категории с provenance/max severity и `conflict`; regression требует новый заземлённый блокер; полное «сравнимое ухудшение» не формализуется дальше (минимализм) |
| 12 | HOLE | polish обходит проверки | ПРИНЯТО → фаза 14: polish → lint+citations → повтор reviewer; export по `draft_sha`; тесты |
| 13 | HOLE | fuzzy несовместим с verbatim | ПРИНЯТО → C-02 только точное совпадение + `raw_sha256` в quote; fuzzy только для candidates |
| 14 | RISK | provenance начинается с файла агента | ПРИНЯТО ЧАСТИЧНО → M5 переформулирован как ограниченная гарантия; `provenance: agent_saved\|confirmed` через liveness-sha; `verify --set` даёт `agent_reported_*`; tool-call identity недоступна на платформе — не выдумываем |
| 15 | HOLE | `[[q:]]` обходит ограничения источника | ПРИНЯТО → `quote_id`→`source_id`; все C-правила и сноски применяются к источнику цитаты; §4.4, §5.4, §5.5 |
| 16 | UNDER-ENG | потерян смысловой контракт source-pack | ПРИНЯТО → `research-findings` несёт role/weight/confidence/contrary на уровне источник×issue; pack без вывода веса из tier; §4.3 |
| 17 | HOLE | после follow-up нет проверки разрешения гэпов | ПРИНЯТО → фаза 7 всегда → повторный sufficiency; all-weak переход; §2.1 стр.6–7 |
| 18 | CONTRADICTION | cancel после прерывания недостижим через router | ПРИНЯТО → router распознаёт `cancel` до проверки фазы; `task cancel`; `next` не выдаёт новых шагов; §2.4 (d) |
| 19 | HOLE | AUQ fallback не эквивалентен, нет события | ПРИНЯТО → `report --status no_answer`, полный текстовый формат, приоритет Cancel, поздний AUQ = no-op; §2.4 |
| 20 | AMBIGUITY | режим нужен раньше выбора; gate budgets | ПРИНЯТО → `intake_max_questions` константа; оценка Sources на гейте для обоих режимов; порядок Style→Mode→Sources→Plan; forced approve последней поданной версии; дефолты не ставят `assumptions_accepted=true` |
| 21 | CONTRADICTION | currency-regate выходит за сегмент | ПРИНЯТО → §3.2 сегмент = маршрут до гейта; S3b завершается при regate; §10 только S3a/S4 |
| 22 | HOLE | M9 не покрывает терминальные пути | ПРИНЯТО → M9 ограничен управляемыми завершениями; `finalize --salvage` без зависимостей/на повреждённом state; терминал только после deliverable; legacy вне цикла |
| 23 | CONTRADICTION | ограничения tools отсутствуют | ПРИНЯТО → `disallowedTools` для MCP-агентов; bash-gate назван auto-allow; тест эффективной политики; P9 |
| 24 | RISK | auto-allow Bash по префиксу | ПРИНЯТО → shlex-разбор, один вызов без операторов/подстановок, абсолютный путь исполняемого; тесты |
| 25 | HOLE | MCP budget не знает остатка провайдера | ПРИНЯТО → «run-бюджет vs неизвестный остаток», ошибки считаются, оценка на гейте плана, cap `Retry-After` 60 с |
| 26 | HOLE | slicer не получает полный текст | ПРИНЯТО → честно: MCP-ответ всегда в контексте агента; экономия — выбором инструмента/детализации; `slice` — вспомогательная над сохранённым raw с `ambiguous`-ошибкой |
| 27 | CONTRADICTION | прогресс требует памяти модели сверх `chat_line` | ПРИНЯТО → `step_issued` от CLI как гарантированный след; `agent log` best-effort; G8 уточнён; 5 минут — измеряемая цель |
| 28 | RISK | `docx validate` недостаточен; OSCOLA формы | ПРИНЯТО ЧАСТИЧНО → добавлены проверки rels/footnote↔reference↔source_id/стили; три типизированные формы OSCOLA; полная OPC/XSD-валидация не делается (минимализм) |
| 29 | HOLE | md fallback не определён | ПРИНЯТО → stdlib регекс-fallback, `[unresolved: id]`, выбор главного deliverable, `.invalid.docx` |
| 30 | CONTRADICTION | 14 схем не покрывают артефакты | ПРИНЯТО → §6 нормативная таблица артефакт→схема→писатель→читатели; G4 по таблице |
| 31 | INCORRECT-CLAIM | `string.Template` не раскрывает `{{…}}` | ПРИНЯТО → `${name}` плоские имена, `substitute()`, golden-тесты промптов |
| 32 | AMBIGUITY | bootstrap Windows/зависимостей замкнут | ПРИНЯТО → discovery в `mf`/`mf.cmd` (shell), `py -3`, команды без зависимостей, три варианта хуков; §5.6 |
| 33 | CONTRADICTION | L-08/L-09 взаимоисключающи | ПРИНЯТО → L-09 = один `quote_id` ≤1 раза; L-08 не требует нарушить L-09; fixture; устаревшие ссылки исправлены |
| 34 | MISSING-TEST | методы измерения G1–G3 | ПРИНЯТО ЧАСТИЧНО → G1 симметричный учёт реальных чтений, G2 по категориям, G3 mtime/sha vs события; полный tool-trace недоступен плагину — не обещаем |
| 35 | MISSING-TEST | пробы и порядок волн | ПРИНЯТО → P4 в W2; старые скрипты живут до W4; финальная приёмка в W5 |

### Раунд 2 — проверка сходимости Codex (2026-09-08; `analysis/09-codex-r2.md`)

**Вердикт: FLAWED** (17/35 раунда 1 — RESOLVED, 18 — PARTIALLY-RESOLVED, 0 — NOT-RESOLVED; 15 новых находок, все про стыки восстановления/идентичности, без архитектурных разворотов). Codex подтвердил новые `file:line` (`source-pack-builder.md:40,44-48,59-63`), арифметику агентов и согласованность §3.2/§10. **Диспозиция: 15 ПРИНЯТО (из них #2 раунда 1 — принято полностью упрощённым решением), 0 ОТКЛОНЕНО.**

| # | Тег | Находка (кратко) | Диспозиция |
|---|---|---|---|
| 1 | HOLE | marker удостоверяет байты, не публикацию; `done` без изменений | ПРИНЯТО → attempt-специфичные пути выходов + публикация копии через `report` с sha; исход `no_change`; повторная сверка sha при чтении (§2.2 `steps[]`, §3.1); `--state done` = обязательный completion, `--state step` = best-effort |
| 2 | HOLE | протокол закрытия неполон (inline-llm, script без `--step`, повтор aggregate) | ПРИНЯТО → `report` для inline-llm; `--step` во всех командах примера; execution identity `(step_id, attempt)`, `identity_mismatch`, `attempt+1` для новых входов; незакрытый script переиздаётся (§3.1) |
| 3 | HOLE | state-транзакция не покрывает файлы | ПРИНЯТО → контракт «tmp+replace, затем state; команды детерминированны → replay = redo; snapshot авторитетен» + crash-тесты (§2.2) |
| 4 | AMBIGUITY | не каждый повтор отнесён к бюджету | ПРИНЯТО → `reason: failure\|recovery`; строки для fix/polish; JSON-бюджет не сбрасывается после rerun; единая политика script-ошибок (§2.2) |
| 5 | HOLE | принадлежность snapshot ≠ версия raw | ПРИНЯТО → тройное равенство sha, extractor по snapshot, `raw_changed` (§5.3) |
| 6 | MISSING-TEST | P9 не проверяет denylist | ПРИНЯТО → P9 (a)/(b) с агентом-эхо и явным fallback (§11) |
| 7 | HOLE | stale-lock: два reclaimer'а, пустой лок | ПРИНЯТО (в v1.2 — rename-протокол; в раунде 3 заменён системными advisory-локами, §2.2) |
| 8 | RISK | дедуп хуков: потеря при crash, ключи, хвост, cleanup | ПРИНЯТО → at-least-once: append → marker; ключи по типу события; починка хвоста `\n`; `.seen` до tidy (§7.2 C) |
| 9 | HOLE | AUQ→text: два конкурирующих ответа | ПРИНЯТО → `generation` гейта, `no_answer` только при ошибке инструмента или в новом turn, ответы старого канала отклоняются (§2.4) |
| 10 | HOLE | export требует несуществующую версию; sticky manual-review | ПРИНЯТО → `no_checked_draft` → экспорт последней версии с `manual_review_required`; `final_status_reasons[]` накопительный (§2.1 стр.15, §2.2) |
| 11 | AMBIGUITY | L-08 «доступные фрагменты»; extractor `too_long` | ПРИНЯТО → контракт extraction (успех только точный sentence-bounded диапазон), `quote skip` как законный исход, L-09 по диапазону (§5.3, §5.4) |
| 12 | CONTRADICTION | verification-статусы не согласованы | ПРИНЯТО → единая enum + `us_by` (§5.3) |
| 13 | MISSING-TEST | G1–G3 не подтверждают величины | ПРИНЯТО → G1 по всем каналам на реальном прогоне, G2 смоделировано/наблюдено с `cli_call`, G3 — архитектурное требование с выборочной проверкой (§0.2) |
| 14 | CONTRADICTION | §6: completion vs validation vs quality; md-исключения; bootstrap path | ПРИНЯТО → три уровня проверки и исходы по типу агента; исключение для drafts/профилей; единая цепочка `plugin_data_dir` (§6, §2.5, §5.6) |
| 15 | NIT | устаревшие числа/ссылки/CLI/плейсхолдеры | ПРИНЯТО → проход синхронизации (версия, 30/5, `step_retry`, ×3 хуки, `{{}}`→`${}`, `task cancel`/`dispatch render`/`quote skip` в §5.2, F-70, §9) |

### Раунд 3 — проверка сходимости Codex (2026-09-08; `analysis/10-codex-r3.md`)

**Вердикт: FLAWED** (из 33 отслеживаемых: 12 RESOLVED, 21 PARTIALLY; 6 новых находок — все механика стыков, без архитектурных разворотов). **Диспозиция: 6 ПРИНЯТО, 0 ОТКЛОНЕНО.** Ключевое упрощение: самодельный протокол stale-lock заменён системными advisory-локами.

| # | Тег | Находка (кратко) | Диспозиция |
|---|---|---|---|
| 1 | HOLE | rename не сериализует reclamation (два reclaimer'а) | ПРИНЯТО → `fcntl.flock`/`msvcrt.locking` на постоянном `state.lock`/`sources.lock`/`events.lock`; лок освобождается ОС при смерти процесса; TTL/PID/reclamation удалены (§2.2) |
| 2 | HOLE | attempt-path не изолирован; pre-seed vN; `no_change` не в enum | ПРИНЯТО → рабочее пространство `steps/<step>/a<n>/<slot>/`; потребители читают только опубликованные canonical; seed создаётся в рабочем каталоге писателя и публикуется; `no_change` в enum статусов, исход для vN; marker с identity и sha входов/выходов (§2.2, §3.1) |
| 3 | CONTRADICTION | `draft anchor` ломает sha-контроль | ПРИНЯТО → `published[]` как авторитетный sha, обновляется anchor/pre-seed/публикацией; `agent_sha256` — история (§2.2, §3.1) |
| 4 | HOLE | identity/generation не в вызовах; порядок проверок; rerun-классификация; §3.4 script-policy | ПРИНЯТО → `--attempt` во всех `--step`-командах и `report`, `--generation` в ответах гейта; порядок identity → generation → closed → содержание; `reason: rerun`; смысловые лимиты считают `initial`; §3.4 согласован с §2.2 |
| 5 | CONTRADICTION | `quote skip` не в контракте документа | ПРИНЯТО → `skips[]` в `quotes.json`; skip освобождает только от verbatim-цитаты при любой причине; чеклист form «quote OR skip»; контраргумент обязателен; §4.4 ссылается на §5.3; fixture двух секций (§4.4, §5.3, §5.4) |
| 6 | RISK | журнал: гонка починки хвоста, ключи PreCompact | ПРИНЯТО → единый writer-протокол под `events.lock` для CLI и хуков; `.seen` — оптимизация до append; PreCompact/SessionStart честно best-effort (§7.2 C/C′) |
| — | NIT | `{{mf}}`, «оба варианта», best-effort `done`, ошибка в строках диспозиции R2 #5/#7 | ПРИНЯТО → исправлено |

**Историческая пометка к таблицам раундов 1–3:** формулировки в колонке «Диспозиция», описывающие механизмы, заменённые позже (rename-локи → системные локи; `agent_reported_*` → единая enum + `us_by`; «поздний AUQ = no-op» → `generation`; дедуп L-09 по `quote_id` → по диапазону; marker `.done` → `done.json` в рабочем каталоге; путь `<canonical>.a<n>` → `steps/<step>/a<n>/<slot>/`), сохраняются как история сходимости; действующее решение — всегда в теле ТЗ.

### Раунд 4 — Codex (2026-09-08; `analysis/11-codex-r4.md`)

**Вердикт: FLAWED** (из 27 отслеживаемых: 12 RESOLVED, 15 PARTIALLY; 4 новых находки + редакционные остатки). **Диспозиция: 4 ПРИНЯТО, 0 ОТКЛОНЕНО.**

| # | Тег | Находка (кратко) | Диспозиция |
|---|---|---|---|
| 1 | HOLE | `published[]` блокирует recovery после `draft anchor` при crash до записи state | ПРИНЯТО → преобразующие команды сохраняют вход и результат в `steps/<step>/a<n>/cli/`; replay пересчитывает и сравнивает с каноническим файлом; сверка потребителей — после replay незакрытых шагов; тест `anchor replace → crash → resume → lint` (§2.2) |
| 2 | AMBIGUITY | порядок `.seen`/append | ПРИНЯТО → под `events.lock`: проверка маркера → append → создание маркера после записи; crash даёт дубль, не потерю (§7.2 C) |
| 3 | CONTRADICTION | enum причин `skip` не покрывает `raw_changed`/fixture | ПРИНЯТО → `raw_changed`, `already_used`, `other --note` (§5.3) |
| 4 | RISK | `msvcrt.locking` байтовый диапазон; lifecycle; вложенность | ПРИНЯТО → общий helper `FileLock`: `seek(0)` + 1 байт; lock-файлы не удаляются; строгий порядок `state → sources → events` с проверкой (§2.2) |
| — | CONTRADICTION/NIT | примеры §3.1 без `--attempt`/workspace/generation; `steps/*.done`; TTL-тесты; F-23; R1-14/19/33; G8; G2 | ПРИНЯТО → примеры переписаны; строка §6; §9; F-23; историческая пометка над таблицами; G8/G2 уточнены (наблюдаемая метрика G2 сужена до `cli_call`/`step_issued`) |

### Раунд 5 — подтверждающий раунд Codex (2026-09-08; `analysis/12-codex-r5.md`)

**Вердикт: SOUND-WITH-CHANGES.** Все 4 находки раунда 4 и все пункты согласованности — RESOLVED; новых дыр на проверенных стыках нет; один нит.

| # | Тег | Находка | Диспозиция |
|---|---|---|---|
| 1 | NIT | в §9 осталось слово `stale-lock` в списке тестов `sources/quotes` | ПРИНЯТО → заменено на «освобождение `sources.lock` после смерти процесса» |

**Гейт сходимости выполнен:** вердикт последнего раунда — SOUND-WITH-CHANGES только с нитом; нерешённых блокеров нет; число находок падало монотонно (70 → 35 → 15 → 6 → 4 → 1) без архитектурных разворотов с раунда 1. ТЗ переводится в статус «согласована» и становится оракулом для реализации (§12, §конформанс).
