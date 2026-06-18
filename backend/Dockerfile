FROM python:3.14
WORKDIR /app

# Копируем requirements.txt (относительно корня репозитория, т.к. контекст сборки — корень)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/ /app/backend/
EXPOSE 8000

# Запускаем сервер с теми же параметрами, что и локально
CMD ["uvicorn", "backend.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]