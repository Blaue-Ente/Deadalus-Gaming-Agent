# Daedalus Continuity Memory — Implementation Spec v1

**Цел:** Дедал да помни какво е било вчера и какви задачи са отворени, така че при ново
съобщение от типа "продължи от снощи по X" да знае точно за какво става дума.

Комбинира два подхода:
- **A. Daily Digest + Task Ledger + auto-inject** — основата
- **C. Daily view в UI** — групиране на сесии по дни с дневен резюме отгоре

**Време за изпълнение:** ~1 ден работа в едно ходене. Никаква миграция на стари данни.
Всичко надгражда съществуващото — `memory.py`, `reflection.py`, `agent.py`, `admin.py`,
`daedalus.html`.

---

## 1. Нов модул: `continuity.py`

Създай `argos_data_nexus/modules/daedalus/continuity.py`.

### 1.1. Storage (Replit DB)

Ключове:
- `adn:task:{task_id}` → JSON един обект със задача
- `adn:tasks:index` → JSON списък от всички task IDs (newest first, max 200)
- `adn:digest:{YYYY-MM-DD}` → JSON един обект с дневен digest

### 1.2. Data shapes

```python
# Task
{
  "id": "task_<8hex>",                # генериран
  "title": "Кратко заглавие (max 80)",
  "summary": "1-3 изречения какво е (max 400)",
  "status": "open" | "done" | "rework",
  "created_ts": 1716123456,           # unix seconds
  "updated_ts": 1716123456,
  "session_ids": ["sess_abc", ...],   # където е била обсъждана
  "related_files": ["path/to/file.py", ...],  # опционално
}

# Daily Digest
{
  "date": "2026-05-20",
  "generated_ts": 1716200000,
  "session_count": 4,
  "summary": "3-5 параграфа на български",
  "key_topics": ["DRP integration", "mobile UI", ...],  # max 8 кратки етикета
  "decisions": ["Решихме X вместо Y", ...],             # max 5
  "artifacts": ["argos_data_nexus/modules/daedalus/knowledge_books.py", ...],
  "task_changes": [
    {"task_id": "task_abc", "change": "created"},
    {"task_id": "task_def", "change": "done"},
  ],
}
```

### 1.3. Публични функции

```python
# === Tasks ===
def create_task(title: str, summary: str = "", session_id: str | None = None,
                related_files: list[str] | None = None) -> dict
def get_task(task_id: str) -> dict | None
def update_task(task_id: str, *, status: str | None = None,
                summary: str | None = None,
                add_session_id: str | None = None,
                add_files: list[str] | None = None) -> dict | None
def delete_task(task_id: str) -> bool
def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]
def find_task_by_hint(hint: str) -> dict | None
    # Fuzzy match по title — за когато Daedalus emit-ва маркер с име, не ID.
    # Първо exact match на ID, после substring case-insensitive в title, после
    # най-новата open задача чието title съдържа поне 2 общи думи с hint.

# === Digests ===
def generate_daily_digest(date_str: str, force: bool = False) -> dict
    # Чете всички session turns за този ден от съществуващото
    # хранилище (memory.py / sessions storage), вика _call_claude
    # с конкретен промпт (виж 1.4), записва резултата.
    # Ако force=False и digest вече съществува — връща го без regenerate.
def get_digest(date_str: str) -> dict | None
def list_digests(limit: int = 30) -> list[dict]
    # Списък от метаданни на последните N digest-а (без full summary).
def ensure_yesterday_digest() -> dict | None
    # Извиква се при всеки нов чат. Ако днешната дата != датата на последния
    # активен ден И няма digest за вчера — генерира го асинхронно.
    # Никога не блокира основния chat поток повече от 100ms (запазва job в
    # background asyncio.create_task).

# === Continuity context за system prompt ===
def build_continuity_context(max_open_tasks: int = 10,
                             include_days: int = 3) -> str
    # Връща готов форматиран string (виж 1.5) за инжектиране в system prompt.
    # Връща празен string ако няма нищо за показване (нови инсталации).
```

### 1.4. Daily Digest промпт

В `generate_daily_digest`, събери всички turns за деня (user + assistant), форматирай ги
като numbered list, и извикай `_call_claude` от `agent.py` с примерно:

```
Резюмирай разговорите от един работен ден между Николай и Дедал.

Дата: {date_str}
Брой сесии: {session_count}
Общо съобщения: {turn_count}

РАЗГОВОРИ (хронологично):
{numbered_turns}

Върни STRICT JSON със следните полета:
{
  "summary": "3-5 кратки параграфа на български, фокус върху работата свършена",
  "key_topics": ["до 8 кратки етикета на теми"],
  "decisions": ["до 5 ключови решения"],
  "artifacts": ["файлове създадени/променени"],
}

Бъди фактически, не разкрасявай. Пиши на български, в трето лице ("обсъдиха X").
```

Parse-ни JSON-а с `_safe_json` от `reflection.py`. При failure — fallback на по-кратко
ръчно резюме (първите 50 думи на първото съобщение + "Generated fallback digest").

### 1.5. Формат на continuity context

`build_continuity_context()` връща нещо такова (или празен string):

```
━━━ CONTINUITY CONTEXT ━━━
📅 Today: 2026-05-20 (Wed) · Last seen: 14ч преди

📝 Yesterday (2026-05-19) — digest:
{summary first paragraph}
Ключови теми: DRP integration, mobile fullscreen, propose loop fixes
Решения: ...

📋 OPEN TASKS (3):
  • [task_abc12345] DRP guide finalization — отворена преди 2 дни, последно докосната вчера
    Останало: финализиране на response schema секцията
  • [task_def67890] Mobile fullscreen toggle — отворена днес
  • [task_ghi11223] Continuity memory implementation — отворена днес

🕘 Recent days:
  • 2026-05-19: DRP integration + guide page · 4 sessions
  • 2026-05-18: knowledge_books module + agent error handling · 6 sessions
  • 2026-05-17: mobile UI improvements · 2 sessions

ВАЖНО: Когато Николай попита "продължи от снощи/последния път/по тема X",
ПЪРВО провери OPEN TASKS и recent digests преди да приемеш че е нов въпрос.
━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 2. Маркери за task management в `tool_parser.py` / `agent.py`

Дедал ще emit-ва тези маркери в своите отговори. Парсваме ги ПРЕДИ да върнем
текста на потребителя — маркерите се изпълняват като side-effects, потребителят
вижда само чист текст + малка нотификация (виж 4).

### 2.1. Marker формат

```
<TASK_OPEN: Заглавие на задачата | Кратко обобщение какво е>
<TASK_DONE: task_id_или_подсказка_от_title>
<TASK_REWORK: task_id_или_подсказка_от_title | Причина защо нуждае от преработка>
<TASK_TOUCH: task_id_или_подсказка_от_title>
```

`TASK_TOUCH` = обновява `updated_ts` на open задача без да сменя status. Полезно
когато се продължава работа по нещо без да приключва.

### 2.2. Имплементация в `agent.py`

Намери секцията където се обработват други маркери (например `NEED_DRP_LAW`,
`NEED_PROPOSE_START`) — добави handler за task маркерите със същия pattern.

```python
import re
from . import continuity

_TASK_OPEN_RE = re.compile(r"<TASK_OPEN:\s*([^|>]+)\|\s*([^>]+)>")
_TASK_DONE_RE = re.compile(r"<TASK_DONE:\s*([^>]+)>")
_TASK_REWORK_RE = re.compile(r"<TASK_REWORK:\s*([^|>]+)\|\s*([^>]+)>")
_TASK_TOUCH_RE = re.compile(r"<TASK_TOUCH:\s*([^>]+)>")

def process_task_markers(raw: str, session_id: str) -> tuple[str, list[dict]]:
    """Изпълнява маркерите, премахва ги от текста, връща (cleaned_text, events).
    events = [{"action": "open|done|rework|touch", "task": {...}, "ok": bool, ...}]
    """
    events = []

    for m in _TASK_OPEN_RE.finditer(raw):
        title = m.group(1).strip()
        summary = m.group(2).strip()
        try:
            t = continuity.create_task(title, summary, session_id=session_id)
            events.append({"action": "open", "task": t, "ok": True})
        except Exception as e:
            events.append({"action": "open", "ok": False, "error": str(e)})

    for m in _TASK_DONE_RE.finditer(raw):
        hint = m.group(1).strip()
        t = continuity.find_task_by_hint(hint)
        if t:
            updated = continuity.update_task(t["id"], status="done", add_session_id=session_id)
            events.append({"action": "done", "task": updated, "ok": True})
        else:
            events.append({"action": "done", "ok": False, "error": f"task not found: {hint}"})

    # Аналогично за REWORK и TOUCH...

    cleaned = _TASK_OPEN_RE.sub("", raw)
    cleaned = _TASK_DONE_RE.sub("", cleaned)
    cleaned = _TASK_REWORK_RE.sub("", cleaned)
    cleaned = _TASK_TOUCH_RE.sub("", cleaned)

    return cleaned.strip(), events
```

**Извикай `process_task_markers` точно преди да върнеш отговора към UI** —
след всички други marker handlers. Прехвърли `events` в response payload-а
като ново поле `task_events`, така че фронтендът да може да покаже toast.

---

## 3. Inject continuity context в system prompt

### 3.1. В `agent.py`

Намери къде се конструира final system prompt (вероятно има функция тип
`_build_system_prompt()` или директно concatенация на текст от `identity.py`).

Добави в края на този prompt (или като отделен system message):

```python
from . import continuity

continuity_block = continuity.build_continuity_context()
if continuity_block:
    system_prompt = system_prompt + "\n\n" + continuity_block
```

Това трябва да се случва **на всяко ново user message** — не само в началото на
сесия — защото open tasks могат да се променят между турновете.

### 3.2. В `identity.py` — нова секция 7k

Добави секция към identity prompt-а:

```
### 7k. Continuity Memory & Task Ledger

Имаш постоянна памет между сесии под формата на:
- **Daily digests** — резюме на всеки изминал ден
- **Task Ledger** — открита/в преработка/завършена задача

В началото на всеки разговор получаваш CONTINUITY CONTEXT блок с:
- Резюме на вчерашния ден
- Списък от ВСИЧКИ open tasks
- Последните 3 дни като headers

**Когато Николай попита нещо от типа:**
- "продължи от снощи"
- "помниш ли вчера какво работехме"
- "по тема X какво стана"
- "довърши X-то"

→ ПЪРВО провери CONTINUITY CONTEXT (open tasks + recent digests),
   и едва тогава отговори. Не приемай че е нов въпрос ако вече има
   подобна тема в паметта.

**Маркери за управление на задачи** (emit-вай ги в края на отговор,
ще се изпълнят автоматично и ще се скрият от UI):

`<TASK_OPEN: Заглавие | Кратко обобщение>`
  Когато започвате нова задача с Николай. Заглавието да е 2-6 думи.

`<TASK_DONE: task_id или част от title>`
  Когато задача очевидно е приключена (фийчърът работи, тестван е,
  Николай е потвърдил, кодът е merge-нат).

`<TASK_REWORK: task_id или title | защо>`
  Когато открием проблем в нещо което считахме за завършено.

`<TASK_TOUCH: task_id или title>`
  Когато продължавате работа по open задача — refresh-ва updated_ts.

Бъди умерен — не emit-вай маркер за всяко мъничко нещо. Една задача =
смислена единица работа (фийчър, баг фикс, рефактор). Малки tweaks не са
задачи.

**Никога не emit-вай маркерите вътре в code block.** Винаги извън, обикновено
в последния параграф на отговора.
```

---

## 4. API endpoints в `admin.py`

Добави под `router = APIRouter(prefix="/admin", tags=["admin"])`:

```python
# === Tasks ===
@router.get("/daedalus/tasks")
async def list_daedalus_tasks(status: str | None = None, limit: int = 50,
                              _: str = Depends(require_admin)) -> dict:
    return {"tasks": continuity.list_tasks(status=status, limit=limit)}

@router.post("/daedalus/tasks")
async def create_daedalus_task(payload: dict = Body(...),
                               _: str = Depends(require_admin)) -> dict:
    return continuity.create_task(
        title=payload["title"],
        summary=payload.get("summary", ""),
    )

@router.patch("/daedalus/tasks/{task_id}")
async def update_daedalus_task(task_id: str, payload: dict = Body(...),
                               _: str = Depends(require_admin)) -> dict:
    t = continuity.update_task(task_id, **payload)
    if not t:
        raise HTTPException(404, "task not found")
    return t

@router.delete("/daedalus/tasks/{task_id}")
async def delete_daedalus_task(task_id: str,
                               _: str = Depends(require_admin)) -> dict:
    return {"deleted": continuity.delete_task(task_id)}

# === Digests ===
@router.get("/daedalus/digests")
async def list_daedalus_digests(limit: int = 30,
                                _: str = Depends(require_admin)) -> dict:
    return {"digests": continuity.list_digests(limit=limit)}

@router.get("/daedalus/digests/{date}")
async def get_daedalus_digest(date: str,
                              _: str = Depends(require_admin)) -> dict:
    d = continuity.get_digest(date)
    if not d:
        raise HTTPException(404, "digest not found")
    return d

@router.post("/daedalus/digests/generate")
async def gen_daedalus_digest(date: str, force: bool = False,
                              _: str = Depends(require_admin)) -> dict:
    return continuity.generate_daily_digest(date, force=force)

@router.get("/daedalus/continuity-context")
async def preview_continuity_context(_: str = Depends(require_admin)) -> dict:
    """Debug: показва точно какво се инжектира в system prompt."""
    return {"context": continuity.build_continuity_context()}
```

---

## 5. UI в `daedalus.html` — Mobile History Sheet update + Tasks panel

### 5.1. Mobile History Sheet — групиране по дни

Намери `renderMobileHistory()` (което вече добавихме). Промени логиката:
вместо плосък списък от сесии, **групирай ги по дата** и за всяка дата покажи
дневния digest като header (ако има такъв).

```javascript
async function renderMobileHistory() {
  const mobList = $('mob-hist-list');
  if (!lastSessions.length) {
    mobList.innerHTML = '<div class="text-argos-dim text-xs italic">— няма сесии —</div>';
    return;
  }

  // Групиране по YYYY-MM-DD
  const byDay = {};
  for (const s of lastSessions) {
    const d = new Date((s.last_ts || 0) * 1000);
    const key = d.toLocaleDateString('sv'); // YYYY-MM-DD
    (byDay[key] = byDay[key] || []).push(s);
  }

  // За всяка дата извикай /admin/daedalus/digests/{date} паралелно
  const dates = Object.keys(byDay).sort().reverse();
  const digests = await Promise.all(
    dates.map(d => fetch(`/admin/daedalus/digests/${d}`, {credentials:'same-origin'})
      .then(r => r.ok ? r.json() : null).catch(() => null))
  );

  mobList.innerHTML = '';
  dates.forEach((date, i) => {
    const dayHeader = document.createElement('div');
    dayHeader.className = 'sticky top-0 bg-argos-panel/95 backdrop-blur px-2 py-1.5 border-b border-argos-border';
    const dateLabel = new Date(date).toLocaleDateString('bg', {weekday:'short', day:'2-digit', month:'short'});
    dayHeader.innerHTML = `
      <div class="text-[10px] uppercase tracking-wider text-argos-accent font-mono font-semibold">${dateLabel}</div>
      ${digests[i]?.summary ? `<div class="text-[10px] text-argos-dim mt-1 line-clamp-2">${escapeHtml(digests[i].key_topics?.join(' · ') || digests[i].summary.slice(0,120))}</div>` : ''}
    `;
    mobList.appendChild(dayHeader);

    for (const s of byDay[date]) {
      const btn = _buildSessionBtn(s, () => { loadSession(s.session_id); closeMobHist(); });
      btn.className = btn.className.replace('text-[10px]', 'text-xs') + ' py-2.5 mt-1';
      mobList.appendChild(btn);
    }
  });
}
```

### 5.2. Tasks panel в right sidebar

Добави нова секция в `#right-sidebar` (СЛЕД Code Proposals секцията, ПРЕДИ
Navigator Telemetry), със същия pattern като другите панели:

```html
<!-- ── Open Tasks ── -->
<div class="bg-argos-panel border border-argos-border rounded-lg p-4 mobile-panel-section" data-panel="tasks">
  <div class="flex items-center justify-between mb-3">
    <div class="flex items-center gap-2">
      <span class="text-argos-accent2 text-xs">📋</span>
      <h2 class="text-sm font-semibold text-white uppercase tracking-wider">Tasks</h2>
      <span id="tasks-open-count" class="hidden px-1.5 py-0.5 rounded-full bg-argos-accent2 text-black text-[9px] font-bold">0</span>
    </div>
    <button onclick="loadTasksPanel()" class="text-[10px] text-argos-dim hover:text-argos-accent uppercase tracking-wider">↺</button>
  </div>
  <div class="flex gap-1 mb-3 text-[10px] font-mono uppercase tracking-wider">
    <button data-ttab="open" class="task-tab flex-1 px-2 py-1.5 rounded border border-argos-accent2/40 bg-argos-accent2/10 text-argos-accent2">Open</button>
    <button data-ttab="rework" class="task-tab flex-1 px-2 py-1.5 rounded border border-argos-border text-argos-dim hover:border-argos-warn/40">Rework</button>
    <button data-ttab="done" class="task-tab flex-1 px-2 py-1.5 rounded border border-argos-border text-argos-dim hover:border-green-500/40">Done</button>
  </div>
  <div id="tasks-list" class="space-y-2 overflow-auto pr-1" style="max-height:380px"></div>
</div>
```

И в JS — функции `loadTasksPanel()`, `renderTasks(items, tab)`, `updateTaskStatus(id, status)`,
включително mobile-panel-btn за "tasks" в горната mobile навигация.

### 5.3. Toast при task event

Когато response payload-ът съдържа `task_events`, покажи кратък toast:
- "📋 Нова задача: {title}" (open)
- "✅ Задача затворена: {title}" (done)
- "↻ Задача нуждае от преработка: {title}" (rework)

Просто div с position-fixed, fade-out след 3 секунди. Има подобен pattern вече
в кода — намери го и преизползвай.

---

## 6. Auto-trigger на yesterday's digest

В `agent.py`, на mястото където се **handle-ва нов user message** (началото на
`run_daedalus()` или подобна entry функция), извикай:

```python
import asyncio
from . import continuity

# Non-blocking — generate yesterday's digest if missing
asyncio.create_task(asyncio.to_thread(continuity.ensure_yesterday_digest))
```

Това няма да забавя първото съобщение — digest генерацията се случва във фонов
поток. Следващото съобщение вече ще получи готовия digest в continuity context.

---

## 7. Acceptance criteria — как да тествам че работи

След като приключиш всичко, направи следните проверки:

1. **`curl /admin/daedalus/continuity-context`** → връща непразен `context` ако
   има поне една open задача или един digest.

2. **Manually create task:** `POST /admin/daedalus/tasks` с `{"title":"Test","summary":"x"}`
   → връща обект с генериран `id`, `status="open"`.

3. **Generate digest за вчера:** `POST /admin/daedalus/digests/generate?date=2026-05-19`
   → връща digest обект с `summary` от LLM-а.

4. **Send chat message** като user с маркер в Дедаловия отговор:
   "test message please respond with `<TASK_OPEN: Test task | Just testing>`"
   → след отговора `GET /admin/daedalus/tasks?status=open` показва новата задача,
   а маркерът НЕ се вижда в UI текста.

5. **System prompt sanity:** в логовете на първия chat call се вижда `CONTINUITY CONTEXT`
   блока в края на system message.

6. **Mobile History Sheet:** на мобилно отвори "☰ История" — сесиите са
   групирани по дни, всеки ден показва дневния digest като header (ако има).

7. **Right sidebar Tasks panel:** показва списък с open задачи, tab-овете
   филтрират правилно, статус update работи.

8. **Restart smoke test:** restart на workflow → app boot-ва чисто, не гърми
   import-и, `/admin/daedalus/tasks` връща JSON.

---

## 8. Какво НЕ е в обхвата на v1 (умишлено)

За да остане усилието умерено, следните НЕ са в spec-а — ако работи v1 ще
обсъдим v2:

- ❌ Топ-level "теми като папки" (Вариант B) — нужна е миграция, тежко
- ❌ 72-часов sliding window винаги в context (от D) — после
- ❌ Vector search върху task summaries — за сега fuzzy string match стига
- ❌ Auto-classification на нов чат към open задача — Дедал просто чете
  open tasks-овете в system prompt и сам решава
- ❌ Cross-session continuous log refactor — сесиите остават както са, само
  ги показваме групирани по дни

---

## 9. Файлове за докосване — резюме

| Файл | Промяна |
|---|---|
| `argos_data_nexus/modules/daedalus/continuity.py` | **NEW** — целият модул |
| `argos_data_nexus/modules/daedalus/agent.py` | Inject continuity context + process_task_markers + asyncio trigger |
| `argos_data_nexus/modules/daedalus/identity.py` | Нова секция 7k |
| `argos_data_nexus/modules/daedalus/tool_parser.py` | Опционално — ако marker parsing-ът се събира на едно място там |
| `argos_data_nexus/admin.py` | 6 нови endpoints |
| `argos_data_nexus/templates/daedalus.html` | Update mobile history, Tasks panel, toast |

---

**Край на спецификация v1.**
