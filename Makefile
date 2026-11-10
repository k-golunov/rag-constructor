.PHONY: help setup install lock hooks lint format format-check test cov run worker clean

PY := poetry run

help:  ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: install hooks  ## Полная настройка окружения: зависимости + git-хуки

install:  ## Создать ./.venv и установить зависимости из poetry.lock
	poetry install

lock:  ## Пересобрать poetry.lock после правки зависимостей
	poetry lock

hooks:  ## Установить pre-commit хуки
	$(PY) pre-commit install --install-hooks

lint:  ## Проверить код линтерами (ruff)
	$(PY) ruff check backend

format:  ## Отформатировать код (ruff --fix + black)
	$(PY) ruff check --fix backend
	$(PY) black backend

format-check:  ## Проверить форматирование без изменений файлов
	$(PY) black --check backend

test:  ## Запустить тесты
	$(PY) pytest

cov:  ## Тесты с покрытием
	$(PY) pytest --cov=backend --cov-report=term-missing

run:  ## Поднять API локально
	$(PY) uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

worker:  ## Поднять Celery-воркер
	$(PY) celery -A backend.tasks.worker.celery_app worker --loglevel=info

clean:  ## Удалить окружение и кеши
	rm -rf .venv .ruff_cache .pytest_cache backend/.pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
