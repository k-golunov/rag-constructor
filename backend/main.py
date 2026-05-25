import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.chat import router as chat_router
from backend.api.projects import router as projects_router
from backend.api.upload import router as upload_router
from backend.config import settings
from backend.db.database import engine
from backend.db.init_db import init_db

logger = logging.getLogger(__name__)


def _masked_db_url(url: str) -> str:
    """Скрывает пароль в DATABASE_URL для безопасного логирования."""
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url or "")


# ---------------------------------------------------------------------------
# Lifespan: автоматическая инициализация БД при каждом старте сервера
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    При старте:
      1. Создаёт БД rag_constructor если она не существует.
      2. Применяет все новые Alembic-миграции (upgrade head).
    Ничего не нужно делать вручную — достаточно запустить uvicorn.
    """
    logger.info("Инициализация БД: %s", _masked_db_url(settings.DATABASE_URL))
    try:
        init_db()
        logger.info("БД готова к работе.")
    except Exception as exc:
        logger.error(
            "Ошибка инициализации БД: %s\n"
            "URL (маскированный): %s\n"
            "Убедитесь что PostgreSQL запущен и настройки в .env верны:\n"
            "  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB",
            exc,
            _masked_db_url(settings.DATABASE_URL),
            exc_info=True,   # печатает полный traceback в лог — упрощает отладку
        )

    yield  # приложение принимает запросы

    engine.dispose()
    logger.info("Пул соединений с БД закрыт.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RAG Constructor API",
    description="Low-Code RAG Pipeline Constructor",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Системные эндпоинты
# ---------------------------------------------------------------------------
@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Роутеры
# ---------------------------------------------------------------------------
app.include_router(projects_router)
app.include_router(upload_router)   # из main
app.include_router(chat_router) 
