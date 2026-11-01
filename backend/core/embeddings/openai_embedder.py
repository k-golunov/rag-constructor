from typing import List, Optional
from openai import OpenAI, AsyncOpenAI
from backend.core.embeddings.base import BaseEmbedder
from backend.core.response_trace import token_usage


class OpenAIEmbedder(BaseEmbedder):
    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.api_base = api_base
        self._client = None
        self._async_client = None
        self.last_usage = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key, base_url=self.api_base, max_retries=0
            )
        return self._client

    @property
    def async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self.api_key, base_url=self.api_base, max_retries=0
            )
        return self._async_client

    async def embed(self, texts: List[str]) -> List[List[float]]:
        self.last_usage = None
        response = await self.async_client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        self.last_usage = token_usage(response)
        return [item.embedding for item in response.data]

    def embed_sync(self, texts: List[str]) -> List[List[float]]:
        self.last_usage = None
        response = self.client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        self.last_usage = token_usage(response)
        return [item.embedding for item in response.data]
