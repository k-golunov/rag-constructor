"""
Alembic environment configuration.

Запуск миграций:
    # Из директории backend/
    alembic upgrade head          # применить все миграции
    alembic downgrade -1          # откатить последнюю
    alembic revision --autogenerate -m "описание"  # создать новую миграцию
    alembic current               # текущая версия БД
    alembic history               # история миграций
"""

import os

# ---------------------------------------------------------------------------
# Подключаем настройки и модели проекта
# ---------------------------------------------------------------------------
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Добавляем корень проекта в PYTHONPATH, чтобы работали абсолютные импорты
# (например, запуск `alembic upgrade head` из папки backend/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backend.db.models  # noqa: F401  — регистрирует все модели в Base.metadata
from backend.config import settings
from backend.db.database import Base

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

# Подставляем DATABASE_URL из настроек приложения (переопределяет пустое значение из alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Настройка логирования из alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные, по которым Alembic определяет изменения схемы
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Режим offline (alembic upgrade head --sql → только SQL-скрипт, без коннекта к БД)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Режим online (подключение к реальной БД)
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
