from typing import Dict, Optional, Type

from backend.config import settings

from .embeddings.base import BaseEmbedder
from .ingestion.base import BaseParser, BaseSplitter
from .llm.base import BaseLLM
from .vector_store.base import BaseVectorStore

EMBEDDER_REGISTRY: Dict[str, Type[BaseEmbedder]] = {}
VECTOR_STORE_REGISTRY: Dict[str, Type[BaseVectorStore]] = {}
LLM_REGISTRY: Dict[str, Type[BaseLLM]] = {}


def get_embedder(name: str, **kwargs) -> BaseEmbedder:
    if name not in EMBEDDER_REGISTRY:
        raise ValueError(f"Embedder '{name}' not found.")
    return EMBEDDER_REGISTRY[name](**kwargs)


def get_vector_store(name: str, **kwargs) -> BaseVectorStore:
    if name not in VECTOR_STORE_REGISTRY:
        raise ValueError(f"VectorStore '{name}' not found.")
    return VECTOR_STORE_REGISTRY[name](**kwargs)


def get_llm(name: str, **kwargs) -> BaseLLM:
    if name not in LLM_REGISTRY:
        raise ValueError(
            f"LLM '{name}' not registered. Available: {list(LLM_REGISTRY)}"
        )
    return LLM_REGISTRY[name](**kwargs)


# ---------------------------------------------------------------------------
# Высокоуровневые билдеры для RAG-пайплайна.
#
# Реестры выше — низкоуровневый слой (провайдер выбирается по имени). Билдеры
# инкапсулируют выбор провайдера по имени модели и подстановку дефолтов из
# settings, чтобы API-роутеры и Celery-задачи не дублировали эту логику.
# ---------------------------------------------------------------------------
def build_embedder(
    model_name: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> BaseEmbedder:
    """Возвращает эмбеддер по имени модели.

    Модели ``sentence-transformers/*`` обслуживаются локальным HuggingFace,
    остальные — OpenAI-совместимым провайдером.

    Args:
        model_name: Идентификатор модели эмбеддингов.
        api_key: API-ключ (если не задан — берётся из settings).
        api_base: Базовый URL API.

    Returns:
        Экземпляр BaseEmbedder.
    """
    if model_name.startswith("sentence-transformers/"):
        return get_embedder("huggingface", model_name=model_name)
    return get_embedder(
        "openai",
        model_name=model_name,
        api_key=api_key or settings.OPENAI_API_KEY,
        api_base=api_base or settings.OPENAI_BASE_URL,
    )


def build_vector_store() -> BaseVectorStore:
    """Возвращает подключённое векторное хранилище (Qdrant) из настроек."""
    return get_vector_store(
        "qdrant",
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )


def build_llm(
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
    kwargs = {
        "model": model_name,
        "api_key": api_key or settings.OPENAI_API_KEY,
        "api_url": api_base,
    }
    if system_prompt:
        kwargs["system_prompt"] = system_prompt
    return get_llm("openai", **kwargs)


# Импорты провайдеров и регистрация должны происходить после определения реестров
def _register_components():
    """Функция для регистрации всех компонентов в реестрах."""
    # Регистрация эмбеддеров
    from .embeddings.openai_embedder import OpenAIEmbedder
    from .embeddings.huggingface_embedder import HuggingFaceEmbedder

    EMBEDDER_REGISTRY["openai"] = OpenAIEmbedder
    EMBEDDER_REGISTRY["huggingface"] = HuggingFaceEmbedder

    # Регистрация векторных хранилищ
    from .vector_store.qdrant_store import QdrantVectorStore

    VECTOR_STORE_REGISTRY["qdrant"] = QdrantVectorStore

    # Регистрация LLM
    from .llm.openai_llm import OpenAILLM

    LLM_REGISTRY["openai"] = OpenAILLM


# Вызов функции регистрации
_register_components()
