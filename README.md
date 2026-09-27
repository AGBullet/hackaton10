# Инспектор ИИ

Локальный сервис для сопоставления проектной, рабочей и исполнительной документации. Он извлекает данные из PDF, DOCX и XML, показывает кандидатов с исходными страницами и сохраняет решение инспектора в версионированном протоколе. Проектная документация служит базой сравнения с учётом выбранной редакции и согласованных изменений.

## Стек

- **Backend:** Python, FastAPI, Uvicorn
- **Frontend:** HTML / CSS / JS (`static/`)
- **БД:** PostgreSQL 16 (реестр, очередь задач, результаты)
- **Документы / OCR:** PyMuPDF, python-docx, openpyxl, Tesseract
- **Модели:** локальные модели qwen3.5-4b и qwen3-vl-8b-instruct
- **Инфраструктура:** Docker Compose (`web` + `worker` + `postgres`)

## Запуск

Нужны Docker Desktop (или Docker Engine + Compose) и локальный сервер моделей по адресу из `.env` (`DOCKER_LM_BASE_URL`, по умолчанию `http://host.docker.internal:1234/v1`).

```powershell
Copy-Item .env.example .env
# Заполните PGPASSWORD, APP_AUTH_SECRET, APP_ADMIN_PASSWORD, APP_ML_PASSWORD
docker compose up --build -d
```

Интерфейс: http://127.0.0.1:8000  
OpenAPI: http://127.0.0.1:8000/docs  

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Остановка (данные в `data/` и томе PostgreSQL сохраняются):

```powershell
docker compose down
```

Без Docker на Windows: установите зависимости из `requirements.txt`, PostgreSQL, Tesseract и локальный сервер моделей, затем `scripts/start.ps1`.

## Документация

- [Архитектура](ARCHITECTURE.md)
- [Развёртывание](DEPLOYMENT.md)
