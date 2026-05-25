import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from backend.core.Document import Document
from backend.core.ingestion.parser import EmptyPDFError, ParserError, get_parser_for
from backend.core.ingestion.splitter import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


def process_file(
    file_path: Path,
    source_name: str,
    chunk_size: int,
    chunk_overlap: int,
) -> List[Document]:
    parser = get_parser_for(source_name)
    if parser is None:
        raise ParserError(f"Неподдерживаемое расширение: {source_name}")
    text = parser.parse(str(file_path))
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return splitter.split_text(text, metadata={"source": source_name})


def process_archive(
    archive_path: Path,
    tmp_dir: Path,
    chunk_size: int,
    chunk_overlap: int,
    operations: Dict[str, Dict[str, Any]],
    operation_id: str,
) -> None:
    extract_dir = tmp_dir / "extracted"
    all_chunks: List[Document] = []
    skipped: List[str] = []
    try:
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)

        for path in extract_dir.rglob("*"):
            if not path.is_file():
                continue
            if get_parser_for(path.name) is None:
                logger.warning("Пропущен неподдерживаемый файл: %s", path.name)
                skipped.append(path.name)
                continue
            try:
                all_chunks.extend(
                    process_file(path, path.name, chunk_size, chunk_overlap)
                )
            except EmptyPDFError as exc:
                logger.warning("Пропущен пустой PDF %s: %s", path.name, exc)
                skipped.append(path.name)
            except ParserError as exc:
                logger.warning("Ошибка парсинга %s: %s", path.name, exc)
                skipped.append(path.name)

        operations[operation_id] = {
            "status": "completed",
            "result": {
                "chunks_count": len(all_chunks),
                "skipped_files": skipped,
                "chunks": [c.model_dump() for c in all_chunks],
            },
            "error": None,
        }
    except Exception as exc:
        logger.exception("Фоновая обработка %s упала", operation_id)
        operations[operation_id] = {"status": "failed", "result": None, "error": str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
