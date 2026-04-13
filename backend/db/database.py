import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ---------------------------------------------------------------------------
# КРИТИЧНО для Windows с русской/нелатинской локалью (cp1251):
#
# psycopg2 читает PGCLIENTENCODING ещё до открытия первого соединения.
# Если переменная не выставлена, psycopg2 берёт системную кодировку Windows
# (cp1251). Когда PostgreSQL возвращает ошибку аутентификации по-русски,
# psycopg2 получает cp1251-байты, Python пробует декодировать как UTF-8
# и падает с UnicodeDecodeError('utf-8 codec can't decode byte 0xC2…').
#
# os.environ нужно установить ДО первого импорта psycopg2 (который происходит
# внутри create_engine). setdefault не перезапишет значение, если
# пользователь уже выставил переменную вручную.
# ---------------------------------------------------------------------------
os.environ.setdefault("PGCLIENTENCODING", "UTF8")

from backend.config import settings  # noqa: E402  (import после os.environ)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # Дополнительный уровень защиты: явно просим UTF-8 в параметрах соединения.
    # Работает уже после установки соединения, но в сочетании с
    # PGCLIENTENCODING покрывает все сценарии.
    connect_args={"client_encoding": "utf8"},
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# ---------------------------------------------------------------------------
# Declarative base (shared across all models)
# ---------------------------------------------------------------------------
Base = declarative_base()


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_db() -> Generator:
    """Dependency для внедрения сессии БД в обработчики FastAPI.

    Использование:
        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
