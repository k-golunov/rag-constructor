# syntax=docker/dockerfile:1
# Backend-образ: зависимости ставятся из poetry.lock, в runtime Poetry не попадает.

# --- Stage 1: экспорт зафиксированных зависимостей ---------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install --no-cache-dir "poetry==2.5.1" "poetry-plugin-export>=1.8"

WORKDIR /build

COPY pyproject.toml poetry.lock ./
RUN poetry export --only main --without-hashes --format=requirements.txt \
        --output requirements.txt

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY --from=builder /build/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY --chown=app:app backend ./backend

RUN mkdir -p /app/uploads && chown -R app:app /app/uploads

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
