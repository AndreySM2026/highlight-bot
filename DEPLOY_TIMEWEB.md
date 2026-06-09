# Деплой Highlight Bot на Timeweb App Platform

## Что понадобится

- Аккаунт Timeweb Cloud
- GitHub-аккаунт (бесплатный)
- Заполненные токены: `BOT_TOKEN`, `TIMEWEB_API_TOKEN`, `TIMEWEB_AGENT_ID`

---

## Шаг 1. Загрузить код на GitHub

В терминале:

```bash
cd "/Users/zhabinas/Автопостер"
git init
git add .
git commit -m "Initial commit: highlight bot MVP"
```

Создайте репозиторий на https://github.com/new (название: `highlight-bot`, **без** README).

Затем:

```bash
git remote add origin https://github.com/ВАШ_ЛОГИН/highlight-bot.git
git branch -M main
git push -u origin main
```

> `.env` в Git не попадёт — он в `.gitignore`.

---

## Шаг 2. Создать приложение в Timeweb

1. Панель Timeweb → **App Platform** (или карточка **«Приложение»** на главной)
2. **Создать приложение** → тип **Backend**
3. Подключить **GitHub** и выбрать репозиторий `highlight-bot`
4. Ветка: `main`
5. Сборка: **Dockerfile** (файл в корне репозитория)
6. Порт приложения: **8080**
7. **Количество инстансов: 1** (важно для SQLite)

---

## Шаг 3. Первый деплой (без WEBHOOK_URL)

На первом деплое URL приложения ещё неизвестен. Добавьте переменные:

| Переменная | Значение |
|------------|----------|
| `BOT_TOKEN` | токен от @BotFather |
| `TIMEWEB_API_TOKEN` | токен из API и Terraform |
| `TIMEWEB_AGENT_ID` | Access ID агента (UUID) |
| `WEBHOOK_SECRET` | `a3f8c2e91b047d6e8f2a1c5b9d0e4f7a` (или своя строка) |
| `DATABASE_PATH` | `/app/data/bot.db` |
| `TEMP_DIR` | `/app/data/temp` |
| `HOST` | `0.0.0.0` |
| `PORT` | `8080` |

`WEBHOOK_URL` **пока не добавляйте**.

Нажмите **Деплой** и дождитесь статуса «Запущено».

---

## Шаг 4. Узнать URL приложения

После деплоя скопируйте URL вида:

```
https://highlight-bot-xxxxx.timeweb.cloud
```

Проверка в терминале:

```bash
curl https://ВАШ-URL.timeweb.cloud/health
```

Ожидаемый ответ: `{"status": "ok"}`

> Если `/health` не отвечает — посмотрите логи приложения в панели Timeweb.

---

## Шаг 5. Второй деплой (включить webhook)

1. App Platform → ваше приложение → **Переменные окружения**
2. Добавьте:

```
WEBHOOK_URL=https://ВАШ-URL.timeweb.cloud
```

(без слэша в конце)

3. **Перезапустите** или **пересоберите** приложение

В логах должно появиться:
```
Бот запущен: @ваш_бот | webhook: https://...
```

---

## Шаг 6. Тест в Telegram

1. Откройте бота → `/start`
2. Отправьте короткое видео (1–2 мин) **файлом**
3. Должен появиться прогресс `Скачивание: 0%` → анализ → кнопки выбора клипов

---

## Логи и отладка

- **Логи:** App Platform → ваше приложение → вкладка «Логи»
- **Бот не отвечает:** проверьте `WEBHOOK_URL`, логи, `curl .../health`
- **Ошибка Qwen:** проверьте `TIMEWEB_API_TOKEN` и `TIMEWEB_AGENT_ID`
- **Видео не обрабатывается:** смотрите логи — ffmpeg есть в Docker-образе

---

## Обновление бота

После изменений в коде:

```bash
git add .
git commit -m "описание изменений"
git push
```

Timeweb пересоберёт приложение автоматически (если включён auto-deploy).
