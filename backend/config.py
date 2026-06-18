from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Поиск файла .env
#
# Порядок поиска (первый найденный выигрывает):
#   1. .env рядом с этим файлом (backend/.env)        ← запуск из backend/
#   2. .env на уровень выше (project_root/.env)        ← запуск из корня проекта
#   3. .env в текущей рабочей директории              ← на случай нестандартного запуска
#
# Это нужно чтобы `uvicorn main:app` из папки backend/ и
# `uvicorn backend.main:app` из корня проекта одинаково находили файл.
# ---------------------------------------------------------------------------
_here = Path(__file__).parent  # backend/

_ENV_CANDIDATES = [
    _here / ".env",  # backend/.env
    _here.parent / ".env",  # project_root/.env
    Path(".env"),  # cwd/.env
]

_env_file = next((str(p) for p in _ENV_CANDIDATES if p.exists()), None)


class Settings(BaseSettings):
    """Настройки приложения.

    Все поля можно переопределить через переменные окружения или файл .env.
    Пример файла настроек: .env.example в корне проекта.
    """

    # ------------------------------------------------------------------
    # PostgreSQL
    # Задайте либо DATABASE_URL целиком, либо отдельные POSTGRES_* поля.
    # При наличии DATABASE_URL отдельные поля игнорируются.
    # ------------------------------------------------------------------
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "1"
    POSTGRES_DB: str = "rag_constructor"

    DATABASE_URL: Optional[str] = None

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_url(cls, v, info):
        """Собирает DATABASE_URL из отдельных POSTGRES_* полей если URL не задан.

        Пароль URL-кодируется через quote_plus — обязательно если пароль
        содержит спецсимволы: @, #, $, %, /, : и т.д.
        """
        if v:
            return v
        data = info.data
        user = quote_plus(str(data.get("POSTGRES_USER", "postgres")))
        password = quote_plus(str(data.get("POSTGRES_PASSWORD", "postgres")))
        host = data.get("POSTGRES_HOST", "localhost")
        port = data.get("POSTGRES_PORT", 5432)
        db = data.get("POSTGRES_DB", "rag_constructor")
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    # ------------------------------------------------------------------
    # Qdrant
    # ------------------------------------------------------------------
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None

    # ------------------------------------------------------------------
    # Redis / Celery
    # ------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # ------------------------------------------------------------------
    # LLM / Embeddings
    # ------------------------------------------------------------------
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None

    # ------------------------------------------------------------------
    # Файловое хранилище
    # ------------------------------------------------------------------
    UPLOAD_DIR: str = "./uploads"

    model_config = {
        "env_file": _env_file,
        # utf-8-sig корректно читает файлы с BOM (Windows Notepad) и без него
        "env_file_encoding": "utf-8-sig",
        "case_sensitive": False,
    }


settings = Settings()
