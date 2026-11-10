from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    """Преобразует текст в вектор."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Асинхронный метод для веб-запросов."""

    @abstractmethod
    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Синхронный метод для использования в Celery воркерах."""
