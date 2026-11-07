# SKILL: Добавяне на trading_concept + criteria

**Кога**: owner иска нов концепт (Volume Profile, FVG, Order Block и т.н.).

**Стъпки (задължителни в този ред)**:
1. `<NEED_DB_SCHEMA: trading_concepts>` + `<NEED_DB_SCHEMA: trading_criteria>` — потвърди колоните преди да пишеш INSERT.
2. `<NEED_CHECK_PERM: insert_trading_concepts>` — провери дали owner вече е дал разрешение.
3. `<NEED_DATA_PROPOSAL: trading_concepts | Title | [json batch]>` — НЕ ползвай NEED_PG INSERT за curated таблици. ProposalEnforcer ще те блокира.
4. След като outbox-ът е готов, кратко резюме на български: какво си предложил + покритие на критериите.

**Schema reference**:
- `trading_concepts`: name, display_name, category, theory, when_works, when_fails, confidence, timeframe_applicability
- `trading_criteria`: concept_id (FK), criterion_type ('signal'|'invalidation'), text, weight

**Anti-patterns**:
- НЕ пиши INSERT INTO trading_concepts директно през NEED_PG.
- НЕ предполагай имена на колони — винаги NEED_DB_SCHEMA първо.
