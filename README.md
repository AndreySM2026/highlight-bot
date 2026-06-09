# Highlight Bot — Telegram-бот для нарезки вертикальных хайлайтов

Бот принимает длинные видео (до 20 мин), анализирует яркие моменты через **Qwen 3.5 Plus** (Timeweb Cloud) и отправляет вертикальные клипы 9:16.

## Возможности MVP

- Приём видеофайлов из Telegram (mp4, mov)
- Анализ активности аудио/видео (громкость, паузы, смены сцен)
- Детектирование хайлайтов через Qwen 3.5 Plus + fallback-эвристика
- Прогресс обработки в процентах
- Выбор количества клипов
- Лимит: 10 видео в день на пользователя
- Автоудаление временных файлов

## Требования

- Python 3.12+
- ffmpeg
- Аккаунт Timeweb Cloud (AI-агент на Qwen 3.5 Plus)
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))

## Быстрый старт (локально)

```bash
cp .env.example .env
# Заполните BOT_TOKEN, TIMEWEB_API_TOKEN, TIMEWEB_AGENT_ID

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Локально: polling (USE_POLLING=1 в .env)
python -m bot.main
```

## Настройка Timeweb

1. **API-токен:** Панель → Аккаунт → API и Terraform → Добавить токен
2. **AI-агент:** AI-агенты → Создать → модель `Qwen 3.5 Plus`
3. **agent_id:** вкладка «Дашборд» у агента
4. Проверка:

```bash
python scripts/test_timeweb.py
```

## Деплой на Timeweb App Platform

1. Залейте репозиторий в Git
2. App Platform → Создать приложение → Backend → Dockerfile
3. Порт: `8080`, **1 инстанс**
4. Переменные окружения:

```
BOT_TOKEN=...
TIMEWEB_API_TOKEN=...
TIMEWEB_AGENT_ID=...
WEBHOOK_URL=https://your-app.timeweb.cloud
WEBHOOK_SECRET=<случайная_строка_32_символа>
DATABASE_PATH=/app/data/bot.db
TEMP_DIR=/app/data/temp
```

5. Примонтируйте volume `/app/data` (если доступен)
6. Проверка: `curl https://your-app.timeweb.cloud/health`

## Структура проекта

```
bot/              # Telegram handlers, FSM, keyboards
config/           # settings, constants
services/
  video/          # ffmpeg, normalize, clips, activity map
  highlights/     # Qwen + heuristic detection
  timeweb/        # Timeweb API client
  jobs/           # background pipeline
  storage/        # SQLite
scripts/          # debug scripts
```

## Команды бота

- `/start` — начать
- `/help` — инструкция
- `/status` — лимит и статус обработки

## v2.0 (планируется)

- ASR и впаивание субтитров
- Загрузка по YouTube-ссылкам
- Отдельный AI-агент для нормализации текста
