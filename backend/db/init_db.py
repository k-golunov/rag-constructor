"""
Автоматическая инициализация базы данных при старте приложения.

Порядок действий:
    1. Подключиться к служебной БД `postgres` (она всегда существует).
    2. Проверить, есть ли целевая БД (rag_constructor).
    3. Если нет — создать (CREATE DATABASE).
    4. Запустить Alembic `upgrade head` — применить все новые миграции.

Это означает, что разработчику не нужно вручную создавать БД или
запускать `alembic upgrade head` — всё происходит при старте uvicorn.
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _admin_url() -> str:
    """URL для подключения к служебной БД postgres (не к rag_constructor).

    Используется только для создания целевой БД. Служебная БД `postgres`
    присутствует в любой инсталляции PostgreSQL.
    """
    # Берём всё из DATABASE_URL, но заменяем имя БД на `postgres`
    url = settings.DATABASE_URL
    # DATABASE_URL вида: postgresql://user:pass@host:port/dbname
    # Обрезаем всё после последнего "/" и подставляем "postgres"
    base = url.rsplit("/", 1)[0]
    return f"{base}/postgres"


def _target_db_name() -> str:
    """Извлекает имя целевой БД из DATABASE_URL."""
    return settings.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]


def ensure_database_exists() -> None:
    """Создаёт целевую БД если она ещё не существует.

    Подключается к `postgres` (служебная БД), проверяет наличие
    rag_constructor, при необходимости создаёт.
    """
    db_name = _target_db_name()
    admin_url = _admin_url()

    # isolation_level="AUTOCOMMIT" обязателен для CREATE DATABASE
    admin_engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        connect_args={"client_encoding": "utf8"},
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).fetchone()

            if exists:
                logger.info("БД '%s' уже существует.", db_name)
            else:
                # Имя БД нельзя параметризовать — используем f-string.
                # Значение берётся только из настроек приложения, не от пользователя.
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                logger.info("БД '%s' успешно создана.", db_name)
    finally:
        admin_engine.dispose()


def run_migrations() -> None:
    """Запускает `alembic upgrade head` программно.

    Эквивалент команды `alembic upgrade head` из консоли, но без
    необходимости вручную запускать её перед стартом сервера.
    Alembic сам определяет какие миграции уже применены и применяет только новые.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    # Ищем alembic.ini рядом с этим файлом (backend/alembic.ini)
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"
    if not alembic_ini.exists():
        logger.warning(
            "alembic.ini не найден по пути %s — миграции пропущены.", alembic_ini
        )
        return

    alembic_cfg = AlembicConfig(str(alembic_ini))
    # Передаём DATABASE_URL напрямую — не зависим от alembic.ini
    alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    # Указываем папку с миграциями относительно alembic.ini
    alembic_cfg.set_main_option(
        "script_location", str(alembic_ini.parent / "alembic")
    )

    logger.info("Запуск миграций Alembic (upgrade head)…")
    command.upgrade(alembic_cfg, "head")
    logger.info("Миграции Alembic применены.")


def init_db() -> None:
    """Точка входа: создать БД (если нужно) + применить миграции.

    Вызывается один раз при старте приложения из lifespan FastAPI.
    """
    ensure_database_exists()
    run_migrations()
