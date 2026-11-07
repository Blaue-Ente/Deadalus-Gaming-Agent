# Дедал — Superpowers Roadmap

Списък с **следващи смислени подобрения** върху continuity memory системата
(Task Ledger + Daily Digests), подредени по съотношение полза/усилие.

Текущо състояние (2026-05-20): фаза 1 завършена — task markers, daily digest,
auto-inject в system prompt, mobile UI с Tasks panel.

---

## 🥇 Tier 1 — Веднага следващи (висока полза, малко работа)

### SP-01: Релевантен филтър на continuity context
**Проблем:** при 20+ активни задачи блокът ще е > 2000 токена на всяко обаждане.
**Решение:** в `build_continuity_context()` приемай `query: str | None`. Когато
има query (= user_message от текущия turn), използвай BM25/Mem0 за scoring на
заглавията и инжектирай **top-5 най-релевантни + count("други: N open")**.
Винаги показвай rework задачи (по-критични).
**Файлове:** `continuity.py`, `agent.py` (подай `user_message` като query).

### SP-02: Task ↔ Proposal binding
**Проблем:** като отвориш proposal, не се знае коя задача го е генерирала.
**Решение:** в `process_task_markers` запомняй `last_open_task_id` per session.
Когато proposal се създаде в същата сесия, добавяй `task_id` в metadata.
При proposal APPLIED → auto `<TASK_DONE>` (или поне предложение в toast).
**Файлове:** `agent.py`, `proposals.py`, `continuity.py` (нов `link_proposal`).

### SP-03: Семантично `find_task_by_hint`
**Проблем:** ако кажеш "довърши онова с памет-та", string match няма да хване.
**Решение:** при `create_task` embed-вай title+summary в ChromaDB
(`daedalus_tasks` collection). `find_task_by_hint` прави semantic query първо,
fallback към текущия substring match. Бонус: `related_tasks(task_id)` UI бутон.
**Файлове:** `memory.py` (нов collection), `continuity.py`.

---

## 🥈 Tier 2 — Среден приоритет (по-голяма работа, голяма полза)

### SP-04: Daily Digest UI — преглед "по дни"
**Текущо:** mobile history групира сесиите по ден (визуално). Няма страница за
четене на самия digest.
**Решение:** нов tab в sidebar "📅 Days" — list от последните 30 digest-а със
expand/collapse и бутон "регенерирай". Calendar widget за избор на ден.
**Файлове:** `daedalus.html` (нов panel + JS), `admin.py` (digest endpoints вече има).

### SP-05: Weekly / Monthly rollups
**Идея:** в неделя вечер cron генерира weekly digest от 7-те дневни.
В края на месец — monthly. Складират се като `adn:weekly:{YYYY-Www}` и
`adn:monthly:{YYYY-MM}`. Включват се в continuity context като 1-line headers
("📆 Тази седмица: 3 нови фийчъра, 1 hotfix, 12 сесии").
**Файлове:** `continuity.py` (нови функции), `main.py` (background scheduler).

### SP-06: Stale task auto-flagging
**Идея:** ако task е open >7 дни без `updated_ts` промяна, ежедневен job:
- маркира го като `stale: true` в metadata
- пише в Daedalus inbox: "Задача 'X' стои отворена 9 дни — рework или close?"
**Файлове:** `continuity.py`, `main.py` (scheduler), `planner.py` (inbox).

### SP-07: Task search / филтри в UI
- Search box над Tasks panel — filter по title/id
- Sort: most recent / oldest / by status
- Sessions count badge ("работено в 5 сесии")
**Файлове:** `daedalus.html`.

---

## 🥉 Tier 3 — Дългосрочни ("nice to have")

### SP-08: Conversational task suggester
След N reply-та без TASK marker, фоновата проверка пита haiku: "Тази
работа заслужава ли task entry?". Ако да — emit-ва `<TASK_OPEN>` proxy
маркер. Опасно (може да наводни), пускай зад feature flag.

### SP-09: Task dependencies
Task може да има `depends_on: [task_id, ...]`. В continuity context подреждай:
**Готови → блокирани → в процес**. Визуализация като DAG в Days panel.

### SP-10: Multi-agent task visibility
Когато mesh service-и (Asteria, Iyveris, ...) през ADN извикат Дедал, те
също да могат да отварят/затварят tasks с маркер. Тогава "АКТИВНИ ЗАДАЧИ"
показва източник: 🧑 Николай / 🤖 Asteria / 🌐 mesh.

### SP-11: Export tasks → GitHub Issues
`POST /daedalus/tasks/{id}/export-issue` — създава GitHub issue в свързано
repo с title/body от task, добавя `gh_issue_url` в metadata. Двупосочен sync:
GH issue closed → task done.

### SP-12: Digest как "memory" за други агенти
Daily digest през mesh е достъпен на Asteria/Caduceus. "Помниш ли какво
правеше Николай с Дедал миналата седмица?" → Asteria му дава тематичен преглед.

---

## 🚀 Tier 0 — Trading Knowledge Track (отделен паралелен поток)

Изолиран track защото обхваща друга услуга (StockArgos) и нов knowledge volume
(40+ книги ~ хиляди chunks). НЕ замества Tier 1 — върви паралелно.

### SP-13: Trading Knowledge — Curator Mode (chat-driven, НЕ batch ingest)
**Цел:** Николай дава книги една по една в чата с Дедал. Дедал ги ЧЕТЕ,
извлича само **най-важната теория + техническата част**, категоризира
и записва в правилната Knowledge категория. Без masово auto-ingestion.

**Защо този подход е по-добър от batch ingest:**
- Качество > количество: Дедал решава кое е essential (формули, алгоритми,
  паттерни), а не сляпо chunk-ва всичко (10000+ chunks шум)
- Авторска интерпретация: персонализира към твоя стил/нужди
- Категорията се избира contextually, не по zero-shot guess
- Ти потвърждаваш преди запис → 0 шум в knowledge base

**Workflow:**
```
User → Дедал: [качва Hands-On_Financial_Trading_with_Python.pdf]
              "Прочети я и извади важното за algo + Python библиотеки"
Дедал:
  1. PyMuPDF извличане → пълен текст + ToC
  2. Скан по глави → summary per chapter (haiku, бързо)
  3. Identify "essential" chunks: формули, code snippets, design patterns,
     pitfalls, decision rules — не narrative и не examples
  4. Категоризира всеки essential chunk: {algo, indicators, ml, risk,
     execution, microstructure, pinescript, python_lib, backtesting, ...}
  5. Връща preview: "Намерих 47 important chunks от 312 страници.
     Категории: algo=18, ml=12, indicators=9, risk=8. Покажи ли преди да запиша?"
  6. След User OK → записва в `daedalus_trading_knowledge` collection
     с metadata {book, chapter, page, topic_tags, importance_score, added_via:'curator'}
```

**Файлове (нови):**
- `modules/daedalus/book_curator.py` — pipeline (extract → analyze → categorize → preview)
- `templates/daedalus.html` — нов "Books" panel (upload, list, preview confirmation)

**Файлове (тач):**
- `memory.py` — нова collection `daedalus_trading_knowledge` (отделна от `global`)
- `admin.py` — endpoints: upload, analyze, confirm/save, list, delete
- `agent.py` — нов tool `curator_analyze_book(path)` за да може Дедал да го извика

**Без:** background batch ingestion job, mass tagging classifier, ingest CLI.
Това е чат-driven workflow от ден 1.

### SP-14: StockArgos → Дедал indicator handoff
**Цел:** SA преминава от "local AI пише индикатора" към "Дедал пише
индикатора" → copyright isolation + по-висока quality чрез retrieval.

**Mesh protokoл (минимален вход от SA):**
```
POST /daedalus/indicators/generate
{
  "asset": "NVDA",
  "timeframe": "4h",
  "requested_logic": "RSI с adaptive overbought/oversold based на volatility",
  "output_lang": "pinescript"   // или "thinkscript", "python", ...
}
→
{
  "code": "<pinescript code>",
  "rationale": "Кратко обяснение защо точно тази логика",
  "sources_used": [
    {"book": "Algorithmic Trading 2", "chapter": "Adaptive RSI", "page": 142, "score": 0.83},
    ...
  ],
  "data_used": {"sa": [...], "adn": [...]},  // traceability на pull-овете
  "session_id": "...",
  "task_id": "task_..."  // auto-opened за continuity
}
```

**Pipeline в Дедал (orchestrator подход):**
1. **Knowledge pull:** retrieval през `daedalus_trading_knowledge` с filter
   `topic_tags ⊃ {indicators, requested_logic_keywords}`
2. **SA pull** (mesh callback към StockArgos — read-only "give me everything"):
   - Прогнози / forecasts (всички horizons)
   - Активни и исторически аларми
   - Sentiment (текущ + tracking history)
   - Last signals & catalyst events
   - **Vault500 / VaultTreasury** данни (positions, allocations, risk metrics)
   - **Всички записани SA анализи** за asset-а (note-ове, минали reasoning блокове)
   - **Insider activity** (Form 4 filings, clusters, recent transactions)
   - **SEC filings** (10-K, 10-Q, 8-K — material events)
   - **EDGAR raw** (по-дълбоки извадки, exhibits, related entities)
   - **FRED macro данни** (релевантни за sector/asset — rates, CPI, sector ETFs)
   - **По-дълъг период:** не само last 30d, а **1y / 5y** history когато е налично
3. **ADN internal pull:** цена/volume/anomalies/correlations от собствените
   endpoint-и (`/data/quote`, `/data/anomalies`, `/data/intermarket`, ...)
4. **Compose prompt:** persona + knowledge chunks + SA context + ADN metrics +
   requested_logic
5. **LLM call** (**sonnet**, не haiku — индикаторите са нетривиален код)
6. **Syntax validation:** PineScript parser ако намерим — иначе regex sanity check
7. **Safety check:** без external HTTP, без infinite loops
8. **Запис** като `episode` + auto-open task в Task Ledger ("Indicator за NVDA 4h")

**Ключово:** SA подава САМО трите неща (asset, timeframe, requested_logic).
Дедал САМ дърпа всичко останало — от Knowledge, от SA mesh endpoint-и,
от ADN internal. Това го прави независим orchestrator, не consumer на готов context.

**StockArgos промени:**
1. Замяна на local LLM call с mesh call към ADN
2. Paste на върнатия code в чата
3. **Експорт на пълен read-only mesh API** (`/sa/mesh/*`) с всичко, което SA
   има за asset: forecasts, alerts, sentiment, signals, Vault500/Treasury,
   записани анализи, insider activity, SEC/EDGAR filings, FRED macro.
   Препоръка: един обединен endpoint `GET /sa/mesh/asset/{symbol}/full?period=5y`
   връща структуриран snapshot + отделни специализирани endpoint-и за
   targeted pull (когато Дедал иска само insider или само FRED)
4. Никаква локална persona/knowledge не е нужна в SA

**Принцип:** SA дава **access**, не **context selection**. Дедал решава какво
да дръпне на базата на конкретната заявка → по-малко токени, по-релевантен output.

**Бонус приложения на същия endpoint:** backtest strategy, screener filter,
risk model, position sizing rule — всичко с `output_lang` flag.

**Зависимост:** SP-13 трябва да е готов (или поне partial) преди SP-14, защото
без trading knowledge retrieval-ът ще е плитък.

---

## Приоритети за **следваща сесия** (моят препорък)

1. **SP-01 (релевантен филтър)** — критично преди да се натрупат повече задачи
2. **SP-02 (task↔proposal)** — затваря loop-а "обсъждане → код → задача готова"
3. **SP-04 (Days UI)** — visual completeness от спецификацията (вариант C)
4. **SP-06 (stale flagging)** — само 30 реда код, голяма UX полза

Останалите са кандидати за следващите 2-3 итерации.
