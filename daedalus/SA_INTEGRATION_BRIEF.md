# [SUPERSEDED] Brief за StockArgos Agent — Mesh API expansion + Indicator handoff

> **⚠️ SUPERSEDED от съществуващ по-детайлен бриф:**
> [`docs/daedalus/indicator_knowledge_endpoint_brief.md`](../../../docs/daedalus/indicator_knowledge_endpoint_brief.md)
>
> Този документ е написан 2026-05-20 без знание за оригиналния бриф от
> 2026-05-19, който вече е изпратен на SA и **одобрен изцяло** (виж
> [`docs/daedalus/2026-05-19-stockargos-approval-response.md`](../../../docs/daedalus/2026-05-19-stockargos-approval-response.md)).
>
> Оригиналният бриф е по-конкретен: 3 PostgreSQL таблици
> (`trading_concepts`, `signal_criteria`, `asset_behavior_patterns`) +
> 4 endpoint-а под `/api/v1/knowledge/*` + `/api/v1/indicator-brief/{ticker}`.
>
> Този по-широк подход (Vault500, insider, SEC, FRED pull) остава като
> **Phase 2 разширение** след като оригиналният бриф се имплементира.

---

# Оригинален текст (за reference):

**От:** Дедал (ADN) / Николай
**До:** StockArgos Agent
**Дата:** 2026-05-20
**Статус:** Superseded — виж оригиналния бриф

---

## TL;DR

Искаме SA да премине от **"local AI пише индикатори"** към
**"Дедал пише индикатори, SA само ги показва"**. За да стане това:

1. SA трябва да изложи **read-only mesh API** с всички свои данни за asset
2. Дедал ще има нов endpoint `POST /daedalus/indicators/generate`, който
   orchestrate-ва: Knowledge (книги) + SA mesh + ADN internal → връща готов
   indicator code с rationale и sources

**Полза за SA:** zero copyright concern (книгите/IP-то живеят в ADN), по-висок
quality output (retrieval-augmented), една по-малко отговорност за SA.

---

## Какво трябва SA да изложи (mesh endpoints)

Обединен snapshot endpoint + специализирани targeted endpoint-и.

### Препоръчителна форма

```
GET /sa/mesh/asset/{symbol}/full?period=5y
  → пълен read-only snapshot:
    {
      "forecasts": [...],           // всички horizons
      "alerts": {"active": [...], "history": [...]},
      "sentiment": {"current": ..., "history": [...]},
      "signals": [...],             // last signals & catalyst events
      "vault": {                    // Vault500 / VaultTreasury
        "positions": [...],
        "allocations": [...],
        "risk_metrics": {...}
      },
      "analyses": [...],            // всички записани SA анализи / reasoning блокове
      "insider": {                  // Form 4 filings
        "recent": [...],
        "clusters": [...]
      },
      "sec": {                      // 10-K, 10-Q, 8-K
        "filings": [...],
        "material_events": [...]
      },
      "edgar": {                    // deeper извадки
        "exhibits": [...],
        "related_entities": [...]
      },
      "fred": {                     // macro релевантно
        "series": [...]             // rates, CPI, sector ETFs
      }
    }
```

### Специализирани endpoint-и (за targeted pull когато Дедал иска САМО едно нещо)

```
GET /sa/mesh/asset/{symbol}/forecasts?horizon=...
GET /sa/mesh/asset/{symbol}/alerts?period=...&active_only=bool
GET /sa/mesh/asset/{symbol}/sentiment?period=...
GET /sa/mesh/asset/{symbol}/signals?period=...
GET /sa/mesh/asset/{symbol}/vault
GET /sa/mesh/asset/{symbol}/analyses?limit=N
GET /sa/mesh/asset/{symbol}/insider?period=5y
GET /sa/mesh/asset/{symbol}/sec?form=10-K|10-Q|8-K&period=5y
GET /sa/mesh/asset/{symbol}/edgar?period=5y
GET /sa/mesh/asset/{symbol}/fred?series=...
```

### Изисквания

- **Read-only** (Дедал няма да пише в SA)
- **Period parameter:** да поддържа поне `30d`, `1y`, `5y`, `max`
- **Auth:** mesh shared secret (Iyveris mesh key) — Дедал вече има достъп
- **Format:** JSON, ISO timestamps, structured (не markdown)
- **Performance:** `/full` може да е тежък — добре да има caching layer
  (60-300s TTL), специализираните бързи (<500ms)

---

## Какво ще прави SA differently (frontend side)

**Сегашно поведение (TV Indicator mode в SA):**
```
User: "напиши ми RSI индикатор за NVDA 4h adaptive с volatility"
  ↓
SA local AI: пише код локално (с потенциален copyright concern)
  ↓
Paste в чата
```

**Ново поведение:**
```
User: същата заявка
  ↓
SA: POST /daedalus/indicators/generate
    {
      "asset": "NVDA",
      "timeframe": "4h",
      "requested_logic": "RSI adaptive с volatility",
      "output_lang": "pinescript"
    }
  ↓
Дедал orchestrate-ва (паралелно):
  - retrieval от своя trading_knowledge collection (curated книги)
  - GET /sa/mesh/asset/NVDA/full?period=5y (SA данни)
  - internal ADN endpoints (quote, anomalies, intermarket)
  ↓
Дедал composeлва prompt → sonnet LLM → validation → return:
    {
      "code": "<pinescript>",
      "rationale": "...",
      "sources_used": [{"book": "...", "page": 142, ...}],
      "data_used": {"sa": [...], "adn": [...]},
      "task_id": "task_..."  // auto-opened за continuity
    }
  ↓
SA paste-ва code в чата + (optional) collapsible "Sources & data used"
```

---

## Въпроси към SA Agent

1. **Authentication:** използваме ли Iyveris mesh shared secret или искате
   отделен SA-specific API key за тези endpoint-и?
2. **Vault data:** има ли чувствителни полета (allocations, P&L), които НЕ
   трябва да излизат през mesh? Ако да — кои да филтрираме?
3. **Анализи storage:** анализите ви къде живеят (DB? files? JSON blobs?)
   и какво е разумен limit за `analyses?limit=N` (50? 200?)
4. **EDGAR / SEC raw:** имате ли вече pipeline за тях, или ги дърпате
   on-demand? Ако on-demand — каква е латентността?
5. **Period support:** има ли asset-и за които `5y` е невъзможен (нови IPO-та)?
   Как искате да indicate това в response-а?
6. **Estimated timeline:** колко време ви е нужно за да изложите минималната
   версия (поне `/full` endpoint-а)?

---

## Координация

- **Дедал ще чака това като активна задача в Task Ledger** (auto-opened)
- Когато SA отговори, синхронизираме и започваме:
  1. SA имплементира mesh endpoints
  2. Паралелно: Николай започва SP-13 Curator с първите 3-5 книги
  3. Когато SA mesh + 3-5 книги са готови → SP-14 indicator endpoint в ADN
- **Без работа от страна на SA до отговор** — само evaluation на feasibility

---

## Референции

- Roadmap: `argos_data_nexus/modules/daedalus/SUPERPOWERS.md` (SP-13, SP-14)
- Mesh документация: `.agents/skills/iyveris-mesh/SKILL.md`
- SA mesh интеграция: `.agents/skills/stock-argos-integration/SKILL.md`
