from typing import Type, Dict
from .ingestion.base import BaseParser, BaseSplitter
from .embeddings.base import BaseEmbedder
from .vector_store.base import BaseVectorStore
from .llm.base import BaseLLM

# Пример: реестр эмбеддеров
EMBEDDER_REGISTRY: Dict[str, Type[BaseEmbedder]] = {}
VECTOR_STORE_REGISTRY: Dict[str, Type[BaseVectorStore]] = {}
LLM_REGISTRY: Dict[str, Type[BaseLLM]] = {}

def get_embedder(name: str, **kwargs) -> BaseEmbedder:
    if name not in EMBEDDER_REGISTRY:
        raise ValueError(f"Embedder '{name}' not found.")
    return EMBEDDER_REGISTRY[name](**kwargs)

# Аналогичные функции для get_vector_store, get_llm