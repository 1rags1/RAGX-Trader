# RAGX-Trader — production image for Railway / Docker hosts
FROM python:3.12-slim-bookworm

WORKDIR /app

# Runtime deps only (pandas wheels are prebuilt for linux/amd64)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# Writable runtime data (SQLite signal history, marker JSONL)
RUN mkdir -p /app/data

EXPOSE 8000

# Railway and other hosts set PORT; default 8000 for local docker run
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/health', timeout=8)"

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
