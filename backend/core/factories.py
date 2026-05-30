from typing import Dict, Type

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


# Импорты провайдеров и регистрация должны происходить после определения реестров
def _register_components():
    """Функция для регистрации всех компонентов в реестрах."""
    # Регистрация эмбеддеров
    from .embeddings.openai_embedder import OpenAIEmbedder
    from .embeddings.huggingface_embedder import HuggingFaceEmbedder

    EMBEDDER_REGISTRY["openai"] = OpenAIEmbedder
    EMBEDDER_REGISTRY["huggingface"] = HuggingFaceEmbedder

    # Регистрация LLM
    from .llm.openai_llm import OpenAILLM

    LLM_REGISTRY["openai"] = OpenAILLM


# Вызов функции регистрации
_register_components()
