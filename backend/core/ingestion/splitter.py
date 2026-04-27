from typing import List

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter as _LCRecursiveSplitter,
)

from backend.core.Document import Document
from backend.core.ingestion.base import BaseSplitter


class RecursiveCharacterTextSplitter(BaseSplitter):
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap должен быть меньше chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter = _LCRecursiveSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
        )

    def split_text(self, text: str, metadata: dict) -> List[Document]:
        raw_chunks = self._splitter.split_text(text or "")
        docs: List[Document] = []
        for idx, chunk in enumerate(raw_chunks):
            meta = {**(metadata or {}), "chunk_index": idx}
            docs.append(Document(text=chunk, metadata=meta))
        return docs
