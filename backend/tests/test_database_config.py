"""Driver selection must not depend on SQLAlchemy's changing default."""

from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from backend.config import Settings


def test_default_database_url_explicitly_selects_installed_driver():
    configured = Settings(
        _env_file=None,
        DATABASE_URL=None,
        POSTGRES_USER="test-user",
        POSTGRES_PASSWORD="test@password/#%",
        POSTGRES_HOST="localhost",
        POSTGRES_PORT=5432,
        POSTGRES_DB="test-db",
    )
    assert configured.DATABASE_URL == (
        f"postgresql+psycopg2://test-user:{quote_plus('test@password/#%')}"
        "@localhost:5432/test-db"
    )
    engine = create_engine(configured.DATABASE_URL)
    try:
        assert engine.dialect.driver == "psycopg2"
        assert engine.dialect.dbapi.__name__ == "psycopg2"
    finally:
        engine.dispose()


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_implicit_driver_is_normalized_without_changing_connection_data(scheme):
    suffix = "user:p%40ss%2Fword@localhost:5432/db?sslmode=require"
    configured = Settings(_env_file=None, DATABASE_URL=f"{scheme}://{suffix}")
    assert configured.DATABASE_URL == f"postgresql+psycopg2://{suffix}"
    assert make_url(configured.DATABASE_URL).get_dialect().driver == "psycopg2"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2://user:password@localhost/db",
        "postgresql+psycopg://user:password@localhost/db",
        "sqlite://",
    ],
)
def test_explicit_driver_is_preserved(url):
    assert Settings(_env_file=None, DATABASE_URL=url).DATABASE_URL == url
