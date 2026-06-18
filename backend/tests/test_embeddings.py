import os
import pytest
from backend.core.embeddings.openai_embedder import OpenAIEmbedder


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY required")
class TestOpenAIEmbedder:
    @pytest.fixture
    def embedder(self):
        return OpenAIEmbedder(api_key=os.getenv("OPENAI_API_KEY"))

    @pytest.mark.asyncio
    async def test_embed_returns_correct_count(self, embedder):
        texts = ["Привет мир", "Hello world"]
        result = await embedder.embed(texts)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_embed_returns_1536_dimensions(self, embedder):
        texts = ["Привет мир", "Hello world"]
        result = await embedder.embed(texts)
        assert len(result[0]) == 1536
        assert len(result[1]) == 1536

    def test_embed_sync_returns_correct_count(self, embedder):
        texts = ["Привет мир", "Hello world"]
        result = embedder.embed_sync(texts)
        assert len(result) == 2

    def test_embed_sync_returns_1536_dimensions(self, embedder):
        texts = ["Привет мир", "Hello world"]
        result = embedder.embed_sync(texts)
        assert len(result[0]) == 1536
        assert len(result[1]) == 1536
