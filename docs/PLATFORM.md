# FaxClip как сервис: Supabase, админка, фоновые задачи, мониторинг

## 1. Supabase (PostgreSQL)

1. supabase.com → New project. Регион — рядом с Render (например, Frankfurt).
2. Project Settings → Database → Connection string → вкладка **Session pooler** (IPv4).
   Вид: `postgresql://postgres.<ref>:<пароль>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres`.
3. Render → Environment → `DATABASE_URL` = эта строка → Save → Manual Deploy.
4. При первом запуске FaxClip сам создаёт таблицы и один раз переносит данные владельца из последнего
   бэкапа в Telegram (или из локального SQLite). Отметка: `core_settings.migrated_from_sqlite`.
5. Каждые 30 минут данные владельца дополнительно сохраняются снимком SQLite в Telegram.

Без `DATABASE_URL` всё работает на SQLite, как раньше.

Устройство: `db.py` переводит SQLite-диалект в PostgreSQL. Каждый пользователь — своя схема
(`t_main` — владелец, `t_u<telegram id>` — остальные); общие таблицы — схема `core`.
В режиме SQLite у каждого пользователя свой файл `data/tenants/*.db`.

## 2. Доступ для всех и админка

* Любой пользователь Telegram получает своё изолированное рабочее пространство.
* Владелец — первый ID из `TELEGRAM_ALLOWED_USER_IDS` (или `FAXCLIP_OWNER_ID`), его данные на месте.
* Админка доступна только владельцу (первый id в `FAXCLIP_OWNER_ID`/`TELEGRAM_ALLOWED_USER_IDS`). Чужие id в `FAXCLIP_ADMIN_IDS` игнорируются.

Админка: обзор, пользователи (тариф, лимиты, разделы, блокировка, заметка, сообщение), тарифы,
баннер и рассылка, ошибки, фоновые задачи, регистрация, техработы, журнал действий.

## 3. Фоновые задачи

`core_jobs`: аренда, повтор 30 с → 1 ч, после N попыток — «упала» (админ получает уведомление).
По умолчанию внутри веб-сервиса (`FAXCLIP_WORKER=embedded`). Отдельный процесс: `python worker.py`
и `FAXCLIP_WORKER=off` у веб-сервиса. `.github/workflows/keepalive.yml` пингует Render каждые 10 минут.

## 4. Мониторинг

Ошибки сервера, браузера и упавшие задачи — «Админка → Ошибки», уведомление админам в Telegram
не чаще раза в час. Молчащее больше 10 минут устройство — уведомление пользователю. Sentry — `SENTRY_DSN`.

## 5. Защита от банов (тестовый режим)

Только чтение (read-only соединение): показывает, что защита сделала бы. Правила хранятся в браузере.

## 6. Проверки

`.github/workflows/ci.yml`: ruff + prettier, тесты SQLite и PostgreSQL 16, e2e `tests/e2e/publish_flow.mjs`.
> Файлы workflow лежат в `docs/github-workflows/`: у токена Notion нет права `workflow`. Чтобы включить CI и пинг Render, скопируйте их в `.github/workflows/` через GitHub (Add file → Create new file).
