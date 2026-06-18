"""
Общие фикстуры pytest для тестирования backend.

Стратегия:
- Используем SQLite in-memory вместо реального PostgreSQL.
- Переопределяем зависимость get_db через app.dependency_overrides,
  чтобы каждый тест получал изолированную чистую БД.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.database import Base, get_db
from backend.main import app

# ---------------------------------------------------------------------------
# SQLite in-memory engine для тестов
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite://"  # чисто in-memory, не создаёт файл

engine_test = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    # StaticPool — один и тот же коннект для всех потоков (нужно для TestClient)
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine_test,
)


def override_get_db():
    """Замена get_db, возвращающая сессию тестовой БД."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function", autouse=False)
def db_session():
    """Фикстура, предоставляющая чистую SQLite-сессию для каждого теста."""
    Base.metadata.create_all(bind=engine_test)
    session = TestingSessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine_test)


@pytest.fixture(scope="function")
def client(db_session):
    """
    FastAPI TestClient с подменённой зависимостью get_db.

    Каждый тест получает свежую пустую базу данных.
    """
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
