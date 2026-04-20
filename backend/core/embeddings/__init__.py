from backend.core.embeddings.base import BaseEmbedder
from backend.core.embeddings.openai_embedder import OpenAIEmbedder
from backend.core.embeddings.huggingface_embedder import HuggingFaceEmbedder
from backend.core.factories import EMBEDDER_REGISTRY

EMBEDDER_REGISTRY["openai"] = OpenAIEmbedder
EMBEDDER_REGISTRY["huggingface"] = HuggingFaceEmbedder


__all__ = ["BaseEmbedder", "OpenAIEmbedder", "HuggingFaceEmbedder"]