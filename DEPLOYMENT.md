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


