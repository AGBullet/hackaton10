FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY inspector ./inspector
COPY static ./static
COPY docs/Матрица_параметров_редакция1.1.xlsx ./docs/Матрица_параметров_редакция1.1.xlsx

RUN mkdir -p /app/data /app/_extracted /app/reports
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4)" || exit 1
CMD ["python", "-m", "uvicorn", "inspector.app:app", "--host", "0.0.0.0", "--port", "8000"]
