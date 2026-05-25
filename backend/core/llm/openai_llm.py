from typing import List, Optional

from openai import AsyncOpenAI

from backend.core.llm.base import BaseLLM


class OpenAILLM(BaseLLM):
    """LLM на базе OpenAI (и совместимых API — Anthropic через прокси, Ollama и т.д.).

    Args:
        model_name: Идентификатор модели (gpt-4o-mini, claude-3-haiku-*, и т.д.).
        system_prompt: Системный промпт, внедряется первым сообщением.
        api_key: API-ключ. При отсутствии подставляется «dummy» (для локальных моделей).
        api_base: Базовый URL API.
    """

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        system_prompt: str = "",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        self.model_name = model_name
        self.system_prompt = system_prompt
        self._client = AsyncOpenAI(
            api_key=api_key or "dummy",
            base_url=api_base or None,
        )

    async def generate(
        self,
        prompt: str,
        context: str,
        history: Optional[List[dict]] = None,
    ) -> str:
        """Генерирует ответ на основе вопроса, контекста и истории диалога.

        Args:
            prompt: Вопрос пользователя.
            context: Текст из релевантных чанков Qdrant.
            history: Список предыдущих сообщений [{"role": ..., "content": ...}].

        Returns:
            Текст ответа.
        """
        messages: List[dict] = []

        # Системный промпт + контекст
        system_parts = []
        if self.system_prompt:
            system_parts.append(self.system_prompt)
        if context:
            system_parts.append(f"Контекст из документов:\n\n{context}")
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # История диалога
        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
        )
        return response.choices[0].message.content or ""
