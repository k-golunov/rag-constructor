"""Реализация LLM на базе OpenAI Chat Completions API."""

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from backend.core.llm.base import BaseLLM


class OpenAILLM(BaseLLM):
    """LLM-адаптер для OpenAI-совместимых API (OpenAI, Azure, локальные прокси).

    Args:
        model: Идентификатор модели, например "gpt-4o-mini".
        api_key: Ключ API.
        api_url: Базовый URL (опционально, для прокси или OpenAI-совместимых провайдеров).
        system_prompt: Системный промпт, добавляемый перед контекстом.
    """

    MAX_CONTEXT_CHARS: int = 2000

    def __init__(
        self,
        model: str,
        api_key: str,
        api_url: Optional[str] = None,
        system_prompt: str = "Вы полезный ассистент, отвечающий на вопросы по документам.",
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_url or None,
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
            context: Строка найденных чанков (обрезается до MAX_CONTEXT_CHARS).
            history: История диалога в формате [{"role": ..., "content": ...}].

        Returns:
            Текстовый ответ модели.
        """
        truncated_context = context[: self.MAX_CONTEXT_CHARS]
        messages = self._build_messages(prompt, truncated_context, history or [])
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
        )
        return response.choices[0].message.content

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
