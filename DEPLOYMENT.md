# Развёртывание

## Docker

1. Установите Docker Desktop (или Docker Engine + Compose) и локальный OpenAI-совместимый сервер моделей (`qwen3.5-4b`, `qwen3-vl-8b-instruct`).
2. Скопируйте `.env.example` в `.env`, задайте `PGPASSWORD`, `APP_AUTH_SECRET`, `APP_ADMIN_PASSWORD`, `APP_ML_PASSWORD`. При необходимости измените `DOCKER_LM_BASE_URL`. Файл `.env` не коммитьте.
3. Запуск:

```powershell
docker compose up --build -d
```

Интерфейс: `http://127.0.0.1:8000`.
OpenAPI: `http://127.0.0.1:8000/docs`.

```powershell
docker compose ps
docker compose logs --tail=80 web worker
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Первый запуск создаёт схему БД и импортирует матрицу параметров 1.1. Данные в `data/` и томе PostgreSQL сохраняются после `docker compose down`.

Остановка:

```powershell
docker compose down
```

## Временный стенд через Tuna

Для показа из Windows:

1. В `.env` задайте `ALLOW_REMOTE=1` и при необходимости `WEB_PORT` (порт на хосте, например `8000` или `8001`).
2. Пересоздайте web, если меняли порт:

```powershell
docker compose up -d --force-recreate web
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

3. Запустите туннель на тот же порт, что в `WEB_PORT`:

```powershell
tuna http 8000
# или с поддоменом:
tuna http 8000 --subdomain=msp
```

Публичный URL из вывода Tuna отдавайте для проверки. Туннель держите только на время показа; токен Tuna и `.env` в Git не кладите.
