import sys
from pathlib import Path

from backend.core.ingestion.parser import PDFParser, TextParser
from backend.core.ingestion.splitter import RecursiveCharacterTextSplitter


def main(file_arg: str | None = None) -> None:
    path = Path(file_arg) if file_arg else Path("test.pdf")
    if not path.exists():
        print(f"[ОШИБКА] Файл не найден: {path.resolve()}")
        sys.exit(1)

    parser = PDFParser() if path.suffix.lower() == ".pdf" else TextParser()
    text = parser.parse(str(path))
    print(f"Символов: {len(text)}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.split_text(text, metadata={"source": path.name})
    print(f"Чанков: {len(chunks)}\n")

    for doc in chunks[:5]:
        print(f"=== Chunk #{doc.metadata['chunk_index']} (len={len(doc.text)}) ===")
        print(doc.text[:200].replace("\n", " "))
        print()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
