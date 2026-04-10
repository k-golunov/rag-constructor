from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # База данных
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/rag_studio"
    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None
    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    # LLM / Embeddings
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None  # для прокси типа VSEGPT
    # Путь для хранения загруженных файлов
    UPLOAD_DIR: str = "/tmp/rag_constructor_uploads"

    class Config:
        env_file = ".env"

settings = Settings()