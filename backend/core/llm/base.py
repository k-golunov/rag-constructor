from abc import ABC, abstractmethod
from typing import List, Optional

class BaseLLM(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        context: str,
        history: Optional[List[dict]] = None
    ) -> str:
        """
        Генерирует ответ на основе вопроса пользователя (prompt),
        извлечённого контекста (context) и истории диалога.
        """
        pass