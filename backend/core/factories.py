from typing import Type, Dict
from .ingestion.base import BaseParser, BaseSplitter
from .embeddings.base import BaseEmbedder
from .vector_store.base import BaseVectorStore
from .vector_store.qdrant_store import QdrantStore
from .llm.base import BaseLLM

# Пример: реестр эмбеддеров
EMBEDDER_REGISTRY: Dict[str, Type[BaseEmbedder]] = {}
VECTOR_STORE_REGISTRY: Dict[str, Type[BaseVectorStore]] = {
    "qdrant": QdrantStore,
}
LLM_REGISTRY: Dict[str, Type[BaseLLM]] = {}


def get_embedder(name: str, **kwargs) -> BaseEmbedder:
    if name not in EMBEDDER_REGISTRY:
        raise ValueError(f"Embedder '{name}' not found.")
    return EMBEDDER_REGISTRY[name](**kwargs)


def get_vector_store(name: str, **kwargs) -> BaseVectorStore:
    """Создаёт экземпляр векторного хранилища по имени из реестра.

    Args:
        name: Ключ из VECTOR_STORE_REGISTRY (например, ``"qdrant"``).
        **kwargs: Аргументы конструктора конкретного хранилища.

    Returns:
        Экземпляр BaseVectorStore.

    Raises:
        ValueError: Если ``name`` не зарегистрирован.
    """
    if name not in VECTOR_STORE_REGISTRY:
        raise ValueError(
            f"VectorStore '{name}' not found. Available: {list(VECTOR_STORE_REGISTRY)}"
        )
    return VECTOR_STORE_REGISTRY[name](**kwargs)