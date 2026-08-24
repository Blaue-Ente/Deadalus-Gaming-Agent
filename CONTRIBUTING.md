# Как да допринасяш към Дедал

Благодарим. Това репо е самият продукт — Python пакет `daedalus`, Studio, Playground.

## Бърз dev setup

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
export DAEDALUS_STANDALONE=1
python3 -m pytest tests -q
```

Опционално за браузърна проверка: `pip install -e ".[playwright]"` и `python -m playwright install chromium`.  
Опционално за Windows GUI: `pip install -e ".[gui]"`.

## Правила за промяна

1. **Игри първо.** Ако пипаш builder-а, вертикалният slice трябва да се играе, не само да се компилира.
2. **Verify е част от строежа.** Нов път в Playground → smoke (и браузър, ако е UI).
3. **Без мълчаливо саморедактиране.** `edit_modules` и harness пачове минават през Approve.
4. **Без втори оркестратор.** Не вендорирай CrewAI / пет MCP моста / in-cloud Godot Editor.
5. **Публичните текстове говорят само за Дедал.** README, ARCHITECTURE, DEPLOY, този файл.

## Клонове и merge requests

1. Клонирай, създай клон от `main`.
2. Дръж MR-а фокусиран: една идея, зелени тестове.
3. Описанието казва *какво* се променя за потребителя, не само списък файлове.
4. Не комитвай `.env`, ключове, `data/`, `playground/projects/`, `.venv/`.

GitLab: [Merge requests](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/merge_requests).

## Тестове

```bash
python3 -m pytest tests -q
```

CI пуска същото в `.gitlab-ci.yml`. Ако добавяш Godot/Blender път, покрий detector/scaffold с unit тест дори когато бинарният Godot липсва в CI.

## Стил

- Python 3.11+, без нови runtime npm зависимости в hook/scripts на продукта.
- Имена: `daedalus.*` за пакета, `playground/projects/{slug}/` за потребителски проекти.
- UI копието на Studio е на български; коментарите в кода могат да са EN или BG, но бъди последователен във файла.

## Докладване на бъгове

Използвай GitLab Issue. Попълни: ОС, Python версия, как си инсталирал (venv / Docker / Windows installer), какво очакваш, какво става, лог от конзолата (без ключове).

Шаблон: `.gitlab/issue_templates/Bug.md`.

## Сигурност

Не отваряй публичен issue за уязвимост с експлойт. Виж [SECURITY.md](SECURITY.md).

## Лиценз

С contrib-а приемаш, че патчът е под [MIT](LICENSE), както останалата част от репото.
