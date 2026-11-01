"""Реализация LLM на базе OpenAI Chat Completions API."""

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from backend.core.llm.base import BaseLLM
from backend.core.response_trace import token_usage


class OpenAILLM(BaseLLM):
    """LLM-адаптер для OpenAI-совместимых API (OpenAI, Azure, локальные прокси).

    Args:
        model: Идентификатор модели, например "gpt-4o-mini".
        api_key: Ключ API.
        api_url: Базовый URL (опционально, для прокси или OpenAI-совместимых провайдеров).
        system_prompt: Системный промпт, добавляемый перед контекстом.
    """

    MAX_CONTEXT_CHARS: int = 2000
    DEFAULT_SYSTEM_PROMPT = (
        "Вы полезный ассистент, отвечающий на вопросы по документам."
    )

    def __init__(
        self,
        model: str,
        api_key: str,
        api_url: Optional[str] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_context_chars: int = MAX_CONTEXT_CHARS,
    ) -> None:
        self._model = model
        if max_context_chars < 1:
            raise ValueError("max_context_chars должен быть положительным")
        self._max_context_chars = max_context_chars
        self._system_prompt = system_prompt
        self.last_usage = None
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_url or None,
            max_retries=0,
        )

    async def generate(
        self,
        prompt: str,
        context: str,
        history: Optional[List[dict]] = None,
    ) -> str:
        """Генерирует текстовый ответ на вопрос с учётом контекста и истории.

        Args:
            prompt: Вопрос пользователя.
            context: Текст, ограниченный явно заданным max_context_chars.
            history: История диалога в формате [{"role": ..., "content": ...}].

        Returns:
            Текстовый ответ модели.
        """
        self.last_usage = None
        response = await self._client.chat.completions.create(
            **self.generation_request(prompt, context, history)
        )
        self.last_usage = token_usage(response)
        answer = response.choices[0].message.content
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Модель не вернула текст ответа.")
        return answer

    def generation_request(self, prompt, context, history=None) -> dict:
        """Единый источник фактически отправляемых параметров для аудита."""
        return {
            "model": self._model,
            "stream": False,
            "messages": self._build_messages(
                prompt,
                context[: self._max_context_chars],
                history or [],
            ),
        }

    async def generate_stream(
        self,
        prompt: str,
        context: str,
        history: Optional[List[dict]] = None,
    ) -> AsyncIterator[str]:
        """Стриминговая генерация ответа — chunks строки.

        Интерфейс заложен для будущей реализации Server-Sent Events / WebSocket.
        Переопределите этот метод в подклассе или добавьте реализацию здесь.

        Raises:
            NotImplementedError: Стриминг ещё не реализован.
        """
        raise NotImplementedError("Streaming не реализован. Используйте generate().")

    def _build_messages(
        self,
        prompt: str,
        context: str,
        history: List[dict],
    ) -> List[dict]:
        """Формирует список сообщений для Chat Completions.

        Струкра: system (промпт + контекст) → history → user (вопрос).
        """
        system_content = (
            f"{self._system_prompt}\n\nКонтекст:\n{context}"
            if context
            else self._system_prompt
        )
        messages: List[dict] = [{"role": "system", "content": system_content}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        return messages
