from typing import List
from backend.core.embeddings.base import BaseEmbedder


class HuggingFaceEmbedder(BaseEmbedder):
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        raise NotImplementedError("HuggingFaceEmbedder not implemented yet")


    async def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("HuggingFaceEmbedder not implemented yet")


    def embed_sync(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("HuggingFaceEmbedder not implemented yet")
