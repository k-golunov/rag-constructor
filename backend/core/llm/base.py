from abc import ABC, abstractmethod
from typing import List, Optional


class BaseLLM(ABC):
    def generation_request(self, prompt, context, history=None) -> dict:
        """Параметры подготовленного запроса для фиксации запуска."""
        raise NotImplementedError

    @abstractmethod
    async def generate(
        self, prompt: str, context: str, history: Optional[List[dict]] = None
    ) -> str:
        """
        Генерирует ответ на основе вопроса пользователя (prompt),
        извлечённого контекста (context) и истории диалога.
        """
        pass
