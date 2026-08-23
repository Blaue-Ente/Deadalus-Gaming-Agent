# SKILL: Iyveris Mesh обаждане до друга услуга

**Кога**: owner иска данни/анализ от StockArgos, Karthago, Argonaft и т.н.

**Стъпки**:
1. `<NEED_MESH_SNAPSHOT>` — провери коя услуга е online и какви actions поддържа.
2. `<NEED_MESH: target | action | payload_json>` — извикай. Target = slug (stockargos, karthago, ...).
3. Ако върне грешка или timeout — НЕ повтаряй същия call повече от 1 път. Обясни на owner какво си опитал.

**Известни услуги**:
- `stockargos` — forecast, catalyst signals, OHLCV
- `karthago` — strategy backtest, portfolio metrics
- `argonaft` — news feed, sentiment

**Anti-patterns**:
- НЕ извиквай mesh ако заявката може да се отговори от локален knowledge / DB.
- НЕ повтаряй mesh call при timeout — fallback към локален отговор + предупреждение.
