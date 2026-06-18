from abc import ABC, abstractmethod
from typing import List


class BaseEmbedder(ABC):
    """Преобразует текст в вектор."""

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Асинхронный метод для веб-запросов."""
        pass

    @abstractmethod
    def embed_sync(self, texts: List[str]) -> List[List[float]]:
        """Синхронный метод для использования в Celery воркерах."""
        pass
