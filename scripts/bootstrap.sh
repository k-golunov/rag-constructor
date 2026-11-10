#!/usr/bin/env bash
# Единая точка входа для локального окружения: создаёт ./.venv из poetry.lock,
# ставит зависимости и git-хуки. Повторный запуск безопасен.
set -euo pipefail

cd "$(dirname "$0")/.."

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v poetry >/dev/null 2>&1 || die "poetry не найден. Установи: https://python-poetry.org/docs/#installation"

REQUIRED_PY="$(tr -d '[:space:]' < .python-version)"
log "Нужен Python ${REQUIRED_PY}"
poetry env use "${REQUIRED_PY}" >/dev/null 2>&1 || poetry env use python3

log "Устанавливаю зависимости (poetry install)"
poetry install

log "Ставлю pre-commit хуки"
poetry run pre-commit install --install-hooks

log "Окружение готово: $(poetry env info --path)"
log "Python: $(poetry run python -V)"
log "Дальше: make test | make lint | make run"
