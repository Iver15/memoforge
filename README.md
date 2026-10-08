# memoforge

[English](README.en.md) · [Документация на русском](docs/ru/README.md)

> **От юридического вопроса до готового меморандума `.docx`: исследование, проверяемые цитаты, ссылки и критическая проверка выводов — одной командой.**

![Версия](https://img.shields.io/badge/version-2.0.0-blue) ![Лицензия](https://img.shields.io/badge/license-MIT-green) ![Платформы](https://img.shields.io/badge/built%20for-Claude%20Code%20%2B%20Cowork-purple)

![Memoforge: от юридического вопроса до меморандума .docx со ссылками](docs/media/memoforge-promo.webp)

<sub>Обзор примерно на 60 секунд, записанный на v1.1.1: уточнение фактов → план исследования → параллельный поиск источников → критическая проверка → экспорт <code>.docx</code>. В v2 процесс тот же, но шагов меньше, а проверок больше.</sub>

---

## Что делает memoforge

memoforge превращает юридический вопрос в структурированный меморандум со ссылками на источники. Он воспроизводит работу небольшой юридической команды: аналитик уточняет недостающие факты; исследователи параллельно собирают первичные источники — законодательство, судебную практику и разъяснения регуляторов; отдельный агент проверяет их актуальность; автор готовит черновик по схеме IRAC; рецензенты проверяют его по фиксированным чек-листам; медиатор сводит замечания, а автор дорабатывает текст перед экспортом. IRAC означает «вопрос → применимая норма → применение к фактам → вывод».

Юрисдикция не задана заранее: на этапе планирования запрос классифицируется, и под него выбираются источники. Сильные стороны исходного проекта — защита данных в ЕС (GDPR, AI Act, NIS2, DSA), регулирование конфиденциальности и отдельных отраслей в США, потребительское право Великобритании и трансграничный комплаенс.

Главное изменение v2 — перенос управления процессом в Python-пакет `scripts/memoforge/` и CLI `mf`. Этапы, лимиты, фиксация набора источников, происхождение ссылок и все записи `state.json` контролируются кодом, который сопровождают примерно 3600 автономных тестов. Модель-оркестратор следует протоколу из трёх команд — `next → act → report`. Логика процесса больше не зависит от инструкций, которые могут потеряться при сжатии контекста.

## Что вы получаете

- **`memo-<slug>.docx`** — Arial 12 пт, поля 2,54 см, нумерованные разделы и анализ каждого вопроса по IRAC. Поддерживаются русский, английский, немецкий, французский и испанский языки. Ссылки оформляются по OSCOLA: по умолчанию короткая ссылка в тексте ведёт к источнику, а полное описание приводится в приложении «Источники». При `citation_style: footnotes` создаются настоящие сноски Word. Если `python-docx` отсутствует, результат выдаётся в Markdown с пояснением причины.
- **Зафиксированный набор источников** — использованные нормативные акты, судебные решения и документы регуляторов с сохранённым исходным текстом, SHA-256 и датой проверки актуальности. После фиксации новые источники не добавляются.
- **Проверяемые цитаты** — каждый блок цитирования извлекается из сохранённого текста по точному совпадению. Неподтверждённая цитата отклоняется, а отказ записывается в журнал.
- **Результаты рецензирования** — решения по бинарным чек-листам и сведения о том, какие замечания медиатор принял, отклонил или признал конфликтующими.
- **Явный итоговый статус** — `approved_on_v<N>`; `forced_exit_on_v<N>_with_remaining_issues`, если лимит доработок исчерпан при нерешённых блокирующих замечаниях; `manual_review_required_on_v<N>`; либо `fallback_summary_delivered`, если сбой зависимости потребовал резервного результата. Причины указываются отдельно.
- **Полный журнал работы** — `state.json`, `events.jsonl`, все версии черновика и отчёты автоматических проверок.

---

## Установка

Это плагин для **Claude Code и Claude Cowork**. Он не содержит отдельного веб-приложения или сервера.

**Claude Code, эта версия репозитория:**

```text
/plugin marketplace add Iver15/memoforge
/plugin install memoforge@memoforge
```

Для работы с локальными изменениями добавьте каталог проекта как локальный маркетплейс:

```bash
claude plugin marketplace add /полный/путь/к/Memoforge
claude plugin install memoforge@memoforge --config memo_language=ru --config ui_language=ru
```

После установки начните новую сессию Claude Code. Разработка ведётся в этой рабочей копии; установленный плагин может храниться в отдельном кэше. После изменения исходников обновите установленную копию через `claude plugin update memoforge@memoforge` и начните новую сессию.

**Cowork:** откройте Settings → Plugins и перетащите ZIP плагина. Готовый архив `memoforge-2.0.0.zip` исходного проекта доступен в [релизах автора](https://github.com/gregmos/memoforge/releases). Он содержит исходную версию; локальные изменения этой рабочей копии туда не входят.

### Зависимости

CLI, хуки и строка статуса используют только стандартную библиотеку Python. Для проверки JSON-схем, исследования и экспорта Word нужны три пакета из [`requirements.txt`](requirements.txt): `jsonschema`, `python-docx`, `mistune`. Установите их в каталог данных плагина:

```bash
<plugin>/scripts/mf deps install      # Windows: scripts\mf.cmd deps install
<plugin>/scripts/mf deps check
```

Так зависимости не затрагивают окружение вашего проекта. Можно также выполнить `pip install -r requirements.txt` в выбранном Python-окружении. Хук `SessionStart` сообщает о недостающих пакетах. Без них процесс переходит к резервному результату в Markdown.

Нужен Python 3.9 или новее. На macOS, если системный Python требует настройки Xcode, используйте рабочий интерпретатор из Homebrew. Если оболочка отвечает `permission denied` при запуске `scripts/mf`, восстановите право выполнения: `chmod +x scripts/mf`.

### Включение русского языка

Настройте язык один раз после установки:

```bash
<plugin>/scripts/mf config set memo_language ru
<plugin>/scripts/mf config set ui_language ru
<plugin>/scripts/mf config show
```

Это использует уже встроенную поддержку русского языка. Язык документа и язык интерфейса настраиваются независимо. Язык запроса сам по себе не меняет язык документа: в исходной конфигурации `memo_language` равен `en`.

### Подключение правовых баз

Плагин регистрирует десять MCP-серверов через `.mcp.json`:

- `legal-data-hunter` — законодательство, судебная практика и разъяснения регуляторов более чем 230 юрисдикций.
- `courtlistener` — судебная практика США и проверка существования американских судебных ссылок.
- `legalviz` — [LegalViz.EU](https://legalviz.eu), бесплатный доступ к законодательству ЕС: поиск по CELEX, отдельные статьи, толкующие их решения Суда ЕС и изменения актов.
- `uk-legal` — [UK Legal MCP](https://github.com/paulieb89/uk-legal-mcp), бесплатно и без API-ключа: нормы legislation.gov.uk с данными о территории действия и вступлении в силу, решения Find Case Law с точностью до абзаца и разрешение ссылок OSCOLA.
- `justicelibre` — [JusticeLibre](https://github.com/Dahliyaal/justicelibre), бесплатно и без ключа: статьи французских кодексов, практика Кассационного суда, Государственного совета и Конституционного совета, решения CNIL, а также тексты решений Суда ЕС и ЕСПЧ. В исходной документации этот путь выбран из-за защиты Légifrance через Cloudflare и необходимости учётной записи PISTE для альтернативного доступа.
- `opencaselaw` — [OpenCaseLaw](https://github.com/jonashertner/opencaselaw), бесплатно и без ключа, данные CC0: федеральное право Швейцарии по номеру SR и статье, консолидированные редакции, около миллиона решений с 1875 года с официальными аннотациями и юридическая литература.
- `federal-regulations` — [federal-regulations-mcp-server](https://github.com/cyanheads/federal-regulations-mcp-server), бесплатно и без ключа: разделы Code of Federal Regulations из eCFR на выбранную дату, проекты и окончательные правила Federal Register. Сам U.S. Code читается с govinfo.gov.
- `lex` — [Lex](https://github.com/i-dot-ai/lex) от i.AI, государственного AI-инкубатора Великобритании, совместно с The National Archives и Ministry of Justice. Бесплатный экспериментальный сервис без ключа: законы, подзаконные акты, пояснения и история изменений. Для законодательства и решений приоритет остаётся у `uk-legal`.
- `casus` — [CasusLegal](https://mcp.casus.legal/), платный российский коннектор: правовые позиции КС РФ, ВС РФ и бывшего ВАС РФ, гибридный поиск, поиск фраз, полные тексты дел и специальные массивы практики Суда по интеллектуальным правам и административной коллегии. Нужна ваша учётная запись CasusLegal. В Claude Desktop: Settings → Connectors → custom connector `https://mcp.casus.legal/one/mcp`. В Claude Code: `claude mcp add --transport http casus https://mcp.casus.legal/one/mcp`.
- `fas-search` — [Практика ФАС по рекламе](https://blog.delay-rag.ru/mcp-konniektor-k-poisku-po-praktikie-fas/), бесплатно и без ключа: около 8000 решений ФАС России и территориальных управлений по законодательству о рекламе. В исходной документации указаны лимиты 20 вызовов в минуту и 300 в сутки на IP; облачные клиенты делят лимит. Используется для вопросов рекламы, недобросовестной конкуренции и антимонопольного регулирования.

Условия, объём баз и лимиты выше приведены по исходной документации v2.0.0; перед подключением проверьте их у провайдера.

Нажмите **Connect** в панели плагина для нужных серверов. Первый вызов может открыть OAuth-авторизацию. LegalViz, UK Legal, JusticeLibre, OpenCaseLaw и сервер ФАС не требуют ключа; CasusLegal требует платной учётной записи. Без подключений исследование использует WebFetch для официальных порталов, а в меморандум добавляется предупреждение о необходимости проверить ссылки.

**Другие юрисдикции.** Встроенные подключения охватывают ЕС, Великобританию, США, Францию, Швейцарию и Россию. Для России предусмотрены практика высших судов через CasusLegal, практика ФАС по рекламе, массивы pravo.gov.ru и Sudact в Legal Data Hunter, бесплатные страницы consultant.ru и base.garant.ru.

В исходной документации также перечислены серверы семейства `*-eli-mcp` от `matematicsolutions` для 33 юрисдикций (`de-eli-mcp`, `es-eli-mcp`, `nl-eli-mcp`, `ie-eli-mcp`, `at-eli-mcp` и другие), а также `ris-mcp-ts` для австрийской RIS. Это локальные stdio-серверы: добавляйте их в собственную конфигурацию MCP. Проверка сессии перечислит их в `namespaces.other` файла `intake/mcp-probe.json` и сообщит исследователю о доступности. Таблица маршрутизации не содержит их инструментов, поэтому они служат дополнительными источниками. Автор исходного проекта отмечает, что семейство опубликовано 24–27 августа 2026 года, версии ниже 1.0, репозитории имеют не более двух звёзд, запуск не проверялся, а `it-eli-mcp` отсутствовал в PyPI несмотря на описание.

### Первый запрос

```text
/memoforge:memo "Мы — SaaS-компания из США. Планируем анализировать с помощью ИИ переписку
поддержки с пользователями из ЕС и предлагать сотрудникам варианты ответов. В переписке есть
имена, email и иногда сведения об аккаунте. Нужно ли отдельное правовое основание по GDPR
или достаточно исполнения договора? Нужны ли DPIA и выполнение требований AI Act?
Подготовь меморандум на русском языке."
```

Можно задать несколько вопросов: каждый станет отдельным предметом анализа со своими источниками. `/memoforge:continue` возобновляет прерванную задачу или передаёт ответы на ожидающие вопросы. `/memoforge:status` показывает состояние задачи.

---

## Режим работы

Каждый запуск использует режим **Full**. Выбирать режим не нужно. Источник настроек — `scripts/memoforge/modes.py`; [русский справочник режимов](docs/ru/modes.md).

| Параметр | **Full** |
|---|---|
| Слои исследования | законодательство и судебная практика; доктрина, если нужна по плану |
| Рецензенты | логика, форма, ссылки, контраргументы |
| Итерации доработки | 2 |
| Проверка готовности для клиента | один проход финальной правки |
| Шаблон | классический меморандум |
| Согласование источников | при исключениях |
| Учёт квот MCP | LDH — 10 в сутки, CourtListener — 125 в сутки; для бесплатных серверов мягкий предел 100 вызовов на запуск, только для учёта |

Краткий документ для лица, принимающего решение, создаётся командой `/memoforge:brief` после завершения меморандума; см. [Краткая справка для принятия решения](#краткая-справка-для-принятия-решения).

## Когда нужны ваши ответы

Между этими остановками процесс работает самостоятельно.

1. **Уточнение фактов** — до десяти обязательных вопросов о сведениях, которые аналитик не смог установить. Ответьте, например, `1A 2C 3: обрабатываем только данные пользователей из ЕС`; команда `proceed` принимает заявленные значения по умолчанию, `cancel` отменяет задачу.
2. **План** — карточка с юрисдикциями, вопросами, типами источников и вашим профилем стиля, если он есть. Если оценка исследования не укладывается в квоты MCP, потребуется решение о сокращённом охвате. План можно одобрить, изменить или отменить. Если карточка недоступна, согласование проходит текстом.
3. **Проверка источников** — нужна при исключениях: нерешённый вопрос с критическим источником, противоречивые правовые позиции или исчерпание квоты MCP. Без исключений процесс сразу переходит к написанию. Настройка `source_review_gate` может принудительно включить (`on`) или отключить (`off`) этот шаг.

При недостаточном исследовании могут появиться ещё две остановки: адресное уточнение и выбор между продолжением с оговорками и отменой.

## Как отображается ход работы

`dashboard` **включён по умолчанию**. Через инструмент `Artifact` приложение публикует одну страницу и обновляет её после каждого шага. Ссылка выводится в чат сразу после публикации. Отключите страницу через `mf config set dashboard false` или настройки плагина, чтобы сэкономить по одному вызову инструмента на шаг. Если приложение не предоставляет `Artifact`, процесс продолжается без страницы.

По исходной документации Cowork Live artifacts были отключены 19 августа 2026 года, и старый механизм v1 — HTML-рендерер, обновления артефактов и widget MCP — перестал работать. v2 использует сигналы среды выполнения:

- **Карточки агентов.** Подпись вида `P<n>/<N> · <agent> · <label>`, например `P5/13 · legal-researcher · судебная практика, Суд ЕС`. Знаменатель — число доступных этапов процесса в вашей конфигурации.
- **Строка в чате после каждого шага.** В Cowork строки могут появляться вместе после завершения хода. Согласование с пользователем выводит накопленные сообщения.
- **`/tasks`** показывает активных агентов с теми же подписями, а `subagentStatusLine` добавляет время выполнения.
- **`mf events analyze`** анализирует `events.jsonl`: последовательность событий, время работы агентов, фактическую параллельность рецензирования и паузы более пяти минут. Хуки пишут журнал; CLI гарантированно записывает каждый выданный шаг.

Полные блоки прогресса выводятся при согласованиях и в конце. Команда `/memoforge:brief` обновляет ту же страницу: отдельная вкладка показывает состояние справки, раунды проверки и файл.

## Настройки

Все десять настроек необязательны:

| Настройка | Назначение и значения |
|---|---|
| `output_folder` | Каталог рабочих папок задач |
| `publish_folder` | Каталог копирования результата; пустое значение использует область результатов приложения, если она есть |
| `writer_model` | Модель автора: `opus`, `fable`, `sonnet` |
| `source_review_gate` | Согласование источников: `auto`, `on`, `off` |
| `citation_style` | `inline` — короткие ссылки в тексте и полные записи в приложении; `footnotes` — сноски Word. По умолчанию `inline` |
| `memo_language` | Язык документа: `auto`, `en`, `de`, `fr`, `es`, `ru`. Исходное значение — `en` |
| `ui_language` | Язык интерфейса: те же значения. Исходное значение — `auto`, по языку запроса |
| `dashboard` | Страница прогресса; по умолчанию включена |
| `stop_guard` | Защита от преждевременного завершения хода; по умолчанию выключена |
| `websearch_autoallow` | Автоматическое разрешение WebSearch; по умолчанию включено |

`writer_model` выбирает модель только для автора меморандума и справки. Агенты, оценивающие содержание — исследователи, проверка актуальности, рецензенты и медиатор — работают на Opus независимо от настройки.

**Язык документа.** Выберите `memo_language`, например `mf config set memo_language ru`, или укажите язык в запросе. В исходной конфигурации документ пишется по-английски независимо от языка вопроса. Значение `auto` выбирает язык запроса, если это один из пяти поддерживаемых языков; иначе остаётся английский. Язык фиксируется при одобрении плана. До этого его можно изменить: `mf task language --workdir <папка задачи> --memo ru`. Стандартное оформление ссылок сохраняется; заголовки, уровень риска, приложение «Источники», примечания о статусе и итоговая сводка используют язык документа.

**Язык интерфейса.** Вопросы, план и кнопки, страница прогресса, обзор источников и ответы ассистента следуют `ui_language`. По умолчанию выбирается язык запроса из пяти поддерживаемых. Для текущей задачи: `mf task language --workdir <папка задачи> --ui ru`. Команды, пути и служебные ответы `approve`, `edit:`, `proceed` сохраняют исходный вид. Сообщения об ошибках CLI, диагностические данные и замечания рецензентов пока остаются на английском.

Если приложение предоставляет экран настроек плагина, используйте его. Иначе настройте значения через CLI. Это также нужно, если приложение передаёт параметры только процессам хуков, но не запускаемому через `Bash` CLI:

```bash
<plugin>/scripts/mf config show                         # значения и их источники
<plugin>/scripts/mf config set memo_language ru
<plugin>/scripts/mf config set ui_language ru
<plugin>/scripts/mf config set dashboard false
<plugin>/scripts/mf config set output_folder ~/Documents/memoforge
<plugin>/scripts/mf config unset writer_model
```

`mf config` читает и пишет `<plugin_data_dir>/options.json`. При старте сессии хук `SessionStart` записывает в него переданные приложением параметры, поэтому настройки приложения сохраняют приоритет. Порядок разрешения параметров для `mf task new`: **явный флаг `--option key=value` → настройка приложения `CLAUDE_PLUGIN_OPTION_*` → `options.json` → значение по умолчанию**. Поле `options_source` показывает источник каждого значения. Нераскрытый шаблон `${…}` пропускается.

## Разрешения

Плагин не может поставлять правила разрешений самостоятельно. При исследовании приложение будет запрашивать подтверждения, пока вы не разрешите нужные адреса. Блок ниже можно добавить в `~/.claude/settings.json`. Он создаётся командой `mf docs render permissions` из разрешённого списка плагина. [Русская справка](docs/ru/permissions.md); [канонический сгенерированный файл](docs/permissions.md). Замените `${CLAUDE_PLUGIN_ROOT}` на путь установки. Правило `Agent(memoforge:*)` отсутствует, поскольку поддержка шаблонов для `Agent` не подтверждена.

<details>
<summary><b>Блок разрешений — 233 правила</b></summary>

```json
{"permissions": {"allow": [
  "WebFetch(domain:europa.eu)", "WebFetch(domain:*.europa.eu)", "WebFetch(domain:coe.int)", "WebFetch(domain:*.coe.int)",
  "WebFetch(domain:artificialintelligenceact.eu)", "WebFetch(domain:*.artificialintelligenceact.eu)", "WebFetch(domain:legalviz.eu)",
  "WebFetch(domain:*.legalviz.eu)", "WebFetch(domain:boe.es)", "WebFetch(domain:*.boe.es)", "WebFetch(domain:buzer.de)",
  "WebFetch(domain:*.buzer.de)", "WebFetch(domain:caselaw.nationalarchives.gov.uk)", "WebFetch(domain:*.caselaw.nationalarchives.gov.uk)",
  "WebFetch(domain:data.bka.gv.at)", "WebFetch(domain:*.data.bka.gv.at)", "WebFetch(domain:dejure.org)", "WebFetch(domain:*.dejure.org)",
  "WebFetch(domain:gesetze-im-internet.de)", "WebFetch(domain:*.gesetze-im-internet.de)", "WebFetch(domain:irishstatutebook.ie)",
  "WebFetch(domain:*.irishstatutebook.ie)", "WebFetch(domain:judiciary.uk)", "WebFetch(domain:*.judiciary.uk)",
  "WebFetch(domain:justice.gov.uk)", "WebFetch(domain:*.justice.gov.uk)", "WebFetch(domain:legifrance.gouv.fr)",
  "WebFetch(domain:*.legifrance.gouv.fr)",
  "WebFetch(domain:legislation.gov.uk)", "WebFetch(domain:*.legislation.gov.uk)", "WebFetch(domain:normattiva.it)",
  "WebFetch(domain:*.normattiva.it)", "WebFetch(domain:ris.bka.gv.at)", "WebFetch(domain:*.ris.bka.gv.at)", "WebFetch(domain:wetten.overheid.nl)",
  "WebFetch(domain:*.wetten.overheid.nl)", "WebFetch(domain:api.normattiva.it)", "WebFetch(domain:*.api.normattiva.it)",
  "WebFetch(domain:dati.normattiva.it)", "WebFetch(domain:*.dati.normattiva.it)", "WebFetch(domain:repository.officiele-overheidspublicaties.nl)",
  "WebFetch(domain:*.repository.officiele-overheidspublicaties.nl)", "WebFetch(domain:zoekservice.overheid.nl)",
  "WebFetch(domain:*.zoekservice.overheid.nl)", "WebFetch(domain:code.travail.gouv.fr)", "WebFetch(domain:*.code.travail.gouv.fr)",
  "WebFetch(domain:rechtsinformationen.bund.de)", "WebFetch(domain:*.rechtsinformationen.bund.de)", "WebFetch(domain:courdecassation.fr)",
  "WebFetch(domain:*.courdecassation.fr)", "WebFetch(domain:fedlex.admin.ch)", "WebFetch(domain:*.fedlex.admin.ch)", "WebFetch(domain:bger.ch)",
  "WebFetch(domain:*.bger.ch)", "WebFetch(domain:consultant.ru)", "WebFetch(domain:*.consultant.ru)", "WebFetch(domain:www.consultant.ru)",
  "WebFetch(domain:*.www.consultant.ru)", "WebFetch(domain:base.garant.ru)", "WebFetch(domain:*.base.garant.ru)", "WebFetch(domain:garant.ru)",
  "WebFetch(domain:*.garant.ru)", "WebFetch(domain:sudact.ru)", "WebFetch(domain:*.sudact.ru)", "WebFetch(domain:zakon.ru)",
  "WebFetch(domain:*.zakon.ru)", "WebFetch(domain:cyberleninka.ru)", "WebFetch(domain:*.cyberleninka.ru)", "WebFetch(domain:vsrf.ru)",
  "WebFetch(domain:*.vsrf.ru)", "WebFetch(domain:aepd.es)", "WebFetch(domain:*.aepd.es)", "WebFetch(domain:aki.ee)", "WebFetch(domain:*.aki.ee)",
  "WebFetch(domain:autoriteprotectiondonnees.be)", "WebFetch(domain:*.autoriteprotectiondonnees.be)",
  "WebFetch(domain:autoriteitpersoonsgegevens.nl)", "WebFetch(domain:*.autoriteitpersoonsgegevens.nl)", "WebFetch(domain:azop.hr)",
  "WebFetch(domain:*.azop.hr)", "WebFetch(domain:baylda.de)", "WebFetch(domain:*.baylda.de)", "WebFetch(domain:bfdi.bund.de)",
  "WebFetch(domain:*.bfdi.bund.de)", "WebFetch(domain:cnil.fr)", "WebFetch(domain:*.cnil.fr)", "WebFetch(domain:cnpd.public.lu)",
  "WebFetch(domain:*.cnpd.public.lu)", "WebFetch(domain:cnpd.pt)", "WebFetch(domain:*.cnpd.pt)", "WebFetch(domain:cpdp.bg)",
  "WebFetch(domain:*.cpdp.bg)", "WebFetch(domain:dataprotection.gov.cy)", "WebFetch(domain:*.dataprotection.gov.cy)",
  "WebFetch(domain:dataprotection.gov.sk)", "WebFetch(domain:*.dataprotection.gov.sk)", "WebFetch(domain:dataprotection.ro)",
  "WebFetch(domain:*.dataprotection.ro)", "WebFetch(domain:datatilsynet.dk)", "WebFetch(domain:*.datatilsynet.dk)",
  "WebFetch(domain:datatilsynet.no)", "WebFetch(domain:*.datatilsynet.no)", "WebFetch(domain:datenschutz-berlin.de)",
  "WebFetch(domain:*.datenschutz-berlin.de)", "WebFetch(domain:datenschutz.hessen.de)", "WebFetch(domain:*.datenschutz.hessen.de)",
  "WebFetch(domain:datenschutzkonferenz-online.de)", "WebFetch(domain:*.datenschutzkonferenz-online.de)", "WebFetch(domain:dpa.gr)",
  "WebFetch(domain:*.dpa.gr)", "WebFetch(domain:dpc.ie)", "WebFetch(domain:*.dpc.ie)", "WebFetch(domain:dsb.gv.at)", "WebFetch(domain:*.dsb.gv.at)",
  "WebFetch(domain:dvi.gov.lv)", "WebFetch(domain:*.dvi.gov.lv)", "WebFetch(domain:edoeb.admin.ch)", "WebFetch(domain:*.edoeb.admin.ch)",
  "WebFetch(domain:garanteprivacy.it)", "WebFetch(domain:*.garanteprivacy.it)", "WebFetch(domain:ico.org.uk)", "WebFetch(domain:*.ico.org.uk)",
  "WebFetch(domain:idpc.org.mt)", "WebFetch(domain:*.idpc.org.mt)", "WebFetch(domain:imy.se)", "WebFetch(domain:*.imy.se)",
  "WebFetch(domain:ip-rs.si)", "WebFetch(domain:*.ip-rs.si)", "WebFetch(domain:lda.bayern.de)", "WebFetch(domain:*.lda.bayern.de)",
  "WebFetch(domain:ldi.nrw.de)", "WebFetch(domain:*.ldi.nrw.de)", "WebFetch(domain:naih.hu)", "WebFetch(domain:*.naih.hu)",
  "WebFetch(domain:personuvernd.is)", "WebFetch(domain:*.personuvernd.is)", "WebFetch(domain:tietosuoja.fi)", "WebFetch(domain:*.tietosuoja.fi)",
  "WebFetch(domain:uodo.gov.pl)", "WebFetch(domain:*.uodo.gov.pl)", "WebFetch(domain:uoou.cz)", "WebFetch(domain:*.uoou.cz)",
  "WebFetch(domain:vdai.lrv.lt)", "WebFetch(domain:*.vdai.lrv.lt)", "WebFetch(domain:ada.gov)", "WebFetch(domain:*.ada.gov)",
  "WebFetch(domain:cisa.gov)", "WebFetch(domain:*.cisa.gov)", "WebFetch(domain:congress.gov)", "WebFetch(domain:*.congress.gov)",
  "WebFetch(domain:courtlistener.com)", "WebFetch(domain:*.courtlistener.com)", "WebFetch(domain:cppa.ca.gov)", "WebFetch(domain:*.cppa.ca.gov)",
  "WebFetch(domain:dol.gov)", "WebFetch(domain:*.dol.gov)", "WebFetch(domain:ecfr.gov)", "WebFetch(domain:*.ecfr.gov)",
  "WebFetch(domain:eeoc.gov)", "WebFetch(domain:*.eeoc.gov)", "WebFetch(domain:federalregister.gov)",
  "WebFetch(domain:*.federalregister.gov)", "WebFetch(domain:ftc.gov)",
  "WebFetch(domain:*.ftc.gov)", "WebFetch(domain:govinfo.gov)", "WebFetch(domain:*.govinfo.gov)", "WebFetch(domain:hhs.gov)",
  "WebFetch(domain:*.hhs.gov)", "WebFetch(domain:irs.gov)", "WebFetch(domain:*.irs.gov)", "WebFetch(domain:justice.gov)",
  "WebFetch(domain:*.justice.gov)", "WebFetch(domain:law.cornell.edu)", "WebFetch(domain:*.law.cornell.edu)", "WebFetch(domain:nist.gov)",
  "WebFetch(domain:*.nist.gov)", "WebFetch(domain:nlrb.gov)", "WebFetch(domain:*.nlrb.gov)", "WebFetch(domain:oag.ca.gov)",
  "WebFetch(domain:*.oag.ca.gov)", "WebFetch(domain:sec.gov)", "WebFetch(domain:*.sec.gov)", "WebFetch(domain:supremecourt.gov)",
  "WebFetch(domain:*.supremecourt.gov)", "WebFetch(domain:uscourts.gov)", "WebFetch(domain:*.uscourts.gov)", "WebFetch(domain:whitehouse.gov)",
  "WebFetch(domain:*.whitehouse.gov)", "WebFetch(domain:bis.org)", "WebFetch(domain:*.bis.org)", "WebFetch(domain:cen.eu)",
  "WebFetch(domain:*.cen.eu)", "WebFetch(domain:cenelec.eu)", "WebFetch(domain:*.cenelec.eu)", "WebFetch(domain:etsi.org)",
  "WebFetch(domain:*.etsi.org)", "WebFetch(domain:fatf-gafi.org)", "WebFetch(domain:*.fatf-gafi.org)", "WebFetch(domain:iec.ch)",
  "WebFetch(domain:*.iec.ch)", "WebFetch(domain:ietf.org)", "WebFetch(domain:*.ietf.org)", "WebFetch(domain:iso.org)", "WebFetch(domain:*.iso.org)",
  "WebFetch(domain:oecd.org)", "WebFetch(domain:*.oecd.org)", "WebFetch(domain:ohchr.org)", "WebFetch(domain:*.ohchr.org)",
  "WebFetch(domain:un.org)", "WebFetch(domain:*.un.org)", "WebFetch(domain:w3.org)", "WebFetch(domain:*.w3.org)", "WebFetch(domain:wipo.int)",
  "WebFetch(domain:*.wipo.int)", "WebFetch(domain:wto.org)", "WebFetch(domain:*.wto.org)", "WebFetch(domain:edri.org)",
  "WebFetch(domain:*.edri.org)", "WebFetch(domain:gdprhub.eu)", "WebFetch(domain:*.gdprhub.eu)", "WebFetch(domain:noyb.eu)",
  "WebFetch(domain:*.noyb.eu)", "mcp__plugin_memoforge_legal-data-hunter__*", "mcp__plugin_memoforge_courtlistener__*",
  "mcp__plugin_memoforge_legalviz__*", "mcp__plugin_memoforge_uk-legal__*", "mcp__plugin_memoforge_justicelibre__*",
  "mcp__plugin_memoforge_opencaselaw__*", "mcp__plugin_memoforge_federal-regulations__*",
  "mcp__plugin_memoforge_lex__*", "mcp__plugin_memoforge_casus__*", "mcp__plugin_memoforge_fas-search__*",
  "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/mf *)"
]}}
```

</details>

**В Cowork эти правила не действуют.** Bash и загрузка веб-страниц предоставляются инструментами `mcp__workspace__*`, которых нет в списке выше. В этой среде работает хук `permission_gate`: он разрешает загрузку только с разрешённого адреса, а Bash — только для одиночного вызова собственного `mf`, без операторов оболочки.

---

## Собственный стиль документов

По умолчанию автор использует встроенный стиль: краткий текст, без длинных тире, ссылки OSCOLA. Style Studio превращает ваши меморандумы или письменные правила в сохранённый профиль:

```text
/memoforge:style new my-firm --examples ~/memos/2025-q4/
/memoforge:style list
```

Профили хранятся в каталоге данных плагина как Markdown; их можно редактировать вручную. Если профили есть, на согласовании плана предлагается выбор. Профиль задаёт стиль и шаблон, но не меняет объём исследования. Рецензент формы следует вашим правилам; проверки ссылок, IRAC и противоположных правовых позиций остаются общими. Без профиля дополнительных вопросов нет.

## Где находятся результаты

Запуск создаёт **рабочий каталог** с файлами протокола, черновиками и исходными текстами, а также **копию результата**: документ, `summary.md` и `sources/` с набором источников и текстами всех критических и вспомогательных источников. Рабочий каталог остаётся на месте. В копии результата также есть `_run/`: `state.json`, `events.jsonl`, `plan.json`, исходные факты, оценка полноты исследования и рецензии. Поэтому результат можно проверить без открытия рабочего каталога.

Рабочая папка выбирается из первого доступного для записи варианта: `output_folder` → `$MEMOFORGE_OUTPUT_FOLDER` → `<папка сессии>/memoforge/` → `~/Documents/memoforge/` → `./outputs/memoforge-work/`. Папка сессии определяется через `$CLAUDE_PROJECT_DIR` либо текущий каталог; каталог самого плагина, домашняя папка пользователя и корень диска пропускаются. `mf task new` возвращает абсолютный путь, который навык повторяет в чате.

Копия результата размещается в `<publish folder>/memoforge/<slug>/`:

- **Claude Code, папка проекта.** Рабочая папка уже находится внутри подключённого каталога. Без `publish_folder` копирование не выполняется; итоговое сообщение показывает абсолютный путь документа.
- **Cowork.** Плагин работает в контейнере и не видит подключённую папку напрямую. Результат копируется в `/mnt/user-data/outputs`. В корне этой области также создаются `memo-<slug>.<docx|md>` и `memo-<slug>.summary.md`, которые навык показывает через `present_files`. Если инструмента нет, папка копируется через файловые инструменты сессии. В конце выводятся оба пути.
- **Другая среда.** Укажите доступный каталог в `publish_folder`. Если он не указан и область результатов отсутствует, единственной копией остаётся рабочая папка.

У плагина нет собственного серверного хранилища или телеметрии. Локальные файлы сохраняются на вашей машине или в рабочей среде приложения. Вызовы MCP отправляются выбранным провайдерам с вашей авторизацией; плагин не проксирует их и не хранит учётные данные.

## Краткая справка для принятия решения

Меморандум рассчитан на юриста. Для руководителя или другого лица, принимающего решение, выполните `/memoforge:brief` после завершения задачи. Без аргумента выбирается последняя завершённая задача; для другой — `/memoforge:brief <task_id>`.

Команда создаёт самостоятельную справку примерно на три страницы на языке меморандума. В ней остаются вопросы с высоким риском, суммами или санкциями, прямые ответы на ваш запрос, а также открытые или неопределённые вопросы, от которых зависят сохранённые выводы. Структура: главное; выводы с уровнем риска и ссылкой на норму или суд; необходимые действия; условия ответа; «Другие рассмотренные вопросы» — по одной строке на исключённый вопрос с выводом меморандума и риском. Действия со сроком до 14 дней сохраняются всегда. Цитат и отдельного списка источников нет.

Автор готовит справку. Рецензент верности проверяет соответствие меморандуму: отсутствие новых сведений, потерянных условий и изменённых уровней риска. Рецензент формы оценивает понятность для читателя без юридического образования. Допускаются две итерации правки. Статус проверки определяют только верность меморандуму и автоматические проверки.

Нового исследования нет; `state.json`, меморандум и `summary.md` исходной задачи не изменяются. Если в меморандуме остались открытые вопросы, сначала требуется ваше решение, а справка отмечает неподтверждённые пункты. Непройденные проверки перечисляются в предупреждении.

Результат — `brief/brief.docx` в рабочем каталоге, либо `.md` без `python-docx`. Рядом с опубликованным меморандумом создаётся `memo-<slug>.brief.<docx|md>`. Если меморандум сохранён в подключённую папку, справка помещается рядом с ним. Повторный запуск создаёт новую справку, а предыдущую сохраняет в `brief/previous/`.

## Ограничения

- **Нужна проверка юристом.** Меморандум — исследовательский черновик для квалифицированного рецензента.
- **Пять языков документа.** Русский, английский, немецкий, французский и испанский. Замечания рецензентов, ошибки CLI и диагностика остаются на английском.
- **Проверка происхождения имеет границы.** Код подтверждает наличие цитаты в сохранённом тексте. Происхождение текста по указанному URL подтверждается только при доступности страницы и совпадении хеша. Для каждого источника записывается уровень подтверждения.
- **Актуальность зависит от подключённых баз.** Значимые для судебного спора ссылки следует проверить отдельно.
- **Отмена выполняется на границе шага** или при согласовании, а не посреди сегмента.
- **Особенности Cowork:** сообщения могут накапливаться до конца хода, а правила разрешений выше не применяются.

## Подробная документация

[Русский указатель](docs/ru/README.md) ведёт к переводу актуального справочника: архитектура, этапы, режим, события, состояние, резервные результаты, разрешения, тестирование платформы и запуск тестов.

[ТЗ](docs/TZ-memoforge-v2.md), [соглашения разработчиков](docs/dev/CONVENTIONS.md) и [решения реализации](docs/dev/IMPL-DECISIONS.md) уже написаны на русском. Сгенерированные справочники в `docs/` остаются каноническими и проверяются командой `mf docs render --check`; их русские переводы находятся в `docs/ru/`. Исторические контракты v1 хранятся в `docs/attic/`. [История изменений](CHANGELOG.md) и архив v1 сохранены на языке оригинала.

## Лицензия и автор

MIT — см. [`LICENSE`](LICENSE). Автор исходного проекта: Grigorii Moskalev (Григорий Москалёв). [Исходный проект](https://github.com/gregmos/memoforge); [этот репозиторий](https://github.com/Iver15/memoforge); [обсуждение ошибок исходного проекта](https://github.com/gregmos/memoforge/issues).
