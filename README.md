# VerticalOS

Рабочий MVP управляющей панели для вертикального контента.

## Запуск локально

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Открыть http://localhost:8000

## Docker

```bash
docker compose up --build
```

## Реализовано

- Dashboard
- аккаунты и целевая аудитория
- устройства и device token
- activity plans
- вертикальные clips
- загрузка файлов
- базовый 9:16 render через FFmpeg, если FFmpeg установлен
- публикационная очередь
- задачи с 0–100% и автоматическим прогрессом после успешной публикации
- аналитика аккаунтов/площадок
- Telegram Mini App-ready web UI

## Что подключается следующим слоем

- Telegram WebApp init-data authentication
- реальный Android Device Agent
- официальные API площадок
- фоновые workers/Redis
- AI clip scoring/transcription
- субтитры, banner templates, smart face crop
- production analytics ingestion

Система не содержит обход CAPTCHA, антифрод-защиты, rate limits или других защитных механизмов платформ.

## Telegram Mini App

1. Отзовите любой токен, который когда-либо отправлялся в открытый чат, и создайте новый через BotFather.
2. Скопируйте `.env.example` в `.env` и заполните секреты только на сервере.
3. Укажите Telegram user ID владельца в `TELEGRAM_ALLOWED_USER_IDS`.
4. После получения HTTPS-адреса выполните:

```bash
TELEGRAM_BOT_TOKEN='...' python configure_bot.py https://your-domain.example
```

Backend проверяет подпись и срок действия Telegram `initData`. При локальной разработке можно временно установить `ALLOW_DEV_AUTH=1`; в публичном окружении этот режим должен оставаться выключенным.

## Device Agent API (этап 1)

При создании устройства backend один раз возвращает `device_token`. Device Agent передаёт его как `Authorization: Bearer <token>`.

- `POST /api/devices/{device_id}/heartbeat` — статус и заряд.
- `GET /api/devices/{device_id}/jobs` — получить разрешённые задания.
- `POST /api/devices/{device_id}/jobs/{job_id}/claim` — забрать задание.
- `POST /api/devices/{device_id}/jobs/{job_id}/complete` — завершить (`{"ok":true,"result":{"external_id":"..."}}`).
- `GET /api/health` — состояние backend, БД и FFmpeg.

Токены устройств хранятся только в виде SHA-256 хеша. Данные и загрузки сохраняются в `./data`.

## Ограничение безопасной автоматизации

Device Agent предназначен для разрешённых сценариев публикации. Проект не реализует обход CAPTCHA, антифрода, rate limits, массовые автолайки или автоподписки. Для площадок приоритетны официальные API публикации.
