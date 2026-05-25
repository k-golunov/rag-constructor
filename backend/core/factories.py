from typing import Optional

from backend.core.embeddings.base import BaseEmbedder
from backend.core.embeddings.openai_embedder import OpenAIEmbedder
from backend.core.llm.base import BaseLLM
from backend.core.llm.openai_llm import OpenAILLM
from backend.core.vector_store.base import BaseVectorStore
from backend.core.vector_store.qdrant_store import QdrantVectorStore
from backend.config import settings


def get_embedder(
    model_name: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> BaseEmbedder:
    """Возвращает эмбеддер по имени модели.

    Args:
        model_name: Идентификатор модели эмбеддингов.
        api_key: API-ключ (если не задан — берётся из settings).
        api_base: Базовый URL API.

    Returns:
        Экземпляр BaseEmbedder.

    Raises:
        NotImplementedError: Если модель HuggingFace ещё не реализована.
    """
    if model_name.startswith("sentence-transformers/"):
        from backend.core.embeddings.huggingface_embedder import HuggingFaceEmbedder
        return HuggingFaceEmbedder(model_name=model_name)
    return OpenAIEmbedder(
        model_name=model_name,
        api_key=api_key or settings.OPENAI_API_KEY,
        api_base=api_base or settings.OPENAI_BASE_URL,
    )


def get_vector_store() -> BaseVectorStore:
    """Возвращает подключённое векторное хранилище (Qdrant).

    Returns:
        Экземпляр QdrantVectorStore.
    """
    return QdrantVectorStore(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)


def get_llm(
    model_name: str,
    system_prompt: str = "",
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> BaseLLM:
    """Возвращает LLM по имени модели.

    Args:
        model_name: Идентификатор LLM.
        system_prompt: Системный промпт.
        api_key: API-ключ (если не задан — берётся из settings).
        api_base: Базовый URL API.

    Returns:
        Экземпляр BaseLLM.
    """
    return OpenAILLM(
        model_name=model_name,
        system_prompt=system_prompt,
        api_key=api_key or settings.OPENAI_API_KEY,
        api_base=api_base or settings.OPENAI_BASE_URL,
    )
