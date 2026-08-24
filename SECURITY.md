# Сигурност — Дедал

## Докладване

Ако намериш уязвимост в Дедал (RCE извън jail-а, изтичане на ключове, auth bypass на Studio, когато е изложен в мрежата):

1. Отвори **confidential** GitLab issue: https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/issues/new
2. Или пиши на maintainer-а през GitLab (не публикувай PoC в публичен ticket).

Не commi-твай `.env`, токени, Chroma dump-ове с лични чатове.

## Какво Дедал прави сам

- Shell командите вървят в jail; тайните се махат от env преди `run_command`.
- `write_file` пише в Playground проект, не в harness-а. Пачове към `daedalus/` минават през предложения + Approve.
- Windows приложението слуша само `127.0.0.1:8787`.
- Офлайн китът отказва търсене, Jina и image-gen.

## Какво трябва да направиш ти

- На лаптоп: `daedalus serve --host 127.0.0.1`.
- На VPS: HTTPS reverse proxy, сменен peer секрет от `.env.example`, persist-нат volume само за теб.
- Не expосе-вай порт 8787 към интернет без TLS и без сменен секрет.
- Пази `GITLAB_TOKEN` / `NETLIFY_AUTH_TOKEN` / LLM ключове извън git.

## Поддържани версии

Поддържаме клона `main` на това репо. Няма отделен LTS. Обнови с `git pull` и `pip install -e .`.
