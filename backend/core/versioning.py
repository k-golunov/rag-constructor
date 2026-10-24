"""Content-addressed document sets and isolated, reusable Qdrant indexes."""

import hashlib
import json
import asyncio
from weakref import WeakKeyDictionary, WeakValueDictionary
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid5

from backend.config import settings
from backend.core.execution import _public_url, resolved_api_url
from backend.core.ingestion.pipeline import assign_embeddings, process_file
from backend.db.models import DataSource, DocumentVersion, IndexVersion, Project

_index_locks = WeakKeyDictionary()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def indexing_settings(project):
    return {
        "pipeline_version": 1,
        "parser": "pdf-txt-docx-v1",
        "splitter": "recursive-characters-v1",
        "chunk_size": project.chunk_size,
        "chunk_overlap": project.chunk_overlap,
        "embedding_model": project.embedding_model,
        "embedding_dimension": project.embedding_dimension,
        "embedding_api_url": _public_url(resolved_api_url(project.embedding_api_url)),
        "distance": "Cosine",
        "component_versions": {
            name: version(name)
            for name in ("pypdf", "python-docx", "langchain-text-splitters")
        },
    }


def lock_project(db, project_id):
    return db.query(Project).filter(Project.id == project_id).with_for_update().one()


def register_document(db, project, name, content):
    """Сохраняет неизменяемый файл и переключает только текущий источник."""
    checksum = hashlib.sha256(content).hexdigest()
    path = (
        Path(settings.UPLOAD_DIR)
        / str(project.id)
        / "versions"
        / f"{checksum}{Path(name).suffix.lower()}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError("Повреждена сохранённая версия документа.")
    else:
        # Exclusive creation prevents overwriting an existing version.
        try:
            with path.open("xb") as stored:
                stored.write(content)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise ValueError("Конфликт сохранения версии документа.")
    version = (
        db.query(DocumentVersion)
        .filter_by(project_id=project.id, file_name=name, sha256=checksum)
        .first()
    )
    if version is None:
        version = DocumentVersion(
            project_id=project.id,
            file_name=name,
            sha256=checksum,
            size_bytes=len(content),
            file_path=str(path),
        )
        db.add(version)
        db.flush()
    source = (
        db.query(DataSource)
        .filter_by(project_id=project.id, file_name=name)
        .order_by(DataSource.created_at.desc(), DataSource.id)
        .first()
    )
    if source is None:
        source = DataSource(project_id=project.id, file_name=name, file_path=str(path))
        db.add(source)
    source.document_version_id = version.id
    source.file_path = str(path)
    source.is_active = True
    source.status = "processing"
    source.chunks_count = 0
    source.error_message = None
    db.flush()
    return source


def document_manifest(db, project_id):
    sources = (
        db.query(DataSource)
        .filter_by(project_id=project_id, is_active=True)
        .order_by(DataSource.created_at.desc(), DataSource.id)
        .all()
    )
    manifest = []
    seen = set()
    for source in sources:
        if source.file_name in seen:
            continue
        seen.add(source.file_name)
        version = (
            db.get(DocumentVersion, source.document_version_id)
            if source.document_version_id
            else None
        )
        if version is None or version.project_id != project_id:
            raise ValueError(
                "Старый источник не имеет контрольной суммы. Загрузите его повторно."
            )
        manifest.append(
            {
                "document_version_id": str(version.id),
                "file_name": version.file_name,
                "sha256": version.sha256,
                "size_bytes": version.size_bytes,
            }
        )
    manifest.sort(key=lambda entry: (entry["file_name"], entry["document_version_id"]))
    return manifest


def plan_index(db, project, spec=None):
    """Фиксирует полный состав документов, независимо от последующих изменений."""
    lock_project(db, project.id)
    manifest = document_manifest(db, project.id)
    spec = spec or indexing_settings(project)
    digest = fingerprint({"settings": spec, "documents": manifest})
    index_id = uuid5(project.id, digest)
    index = db.get(IndexVersion, index_id)
    if index is None:
        index = IndexVersion(
            id=index_id,
            project_id=project.id,
            fingerprint=digest,
            collection_name=f"rag_{project.id.hex}_{digest[:32]}",
            settings=spec,
            documents=manifest,
            documents_sha256=fingerprint(manifest),
        )
        db.add(index)
        db.flush()
    return index


def index_snapshot(index):
    return {
        "index_resolution": "resolved",
        "index_version_id": str(index.id),
        "index_fingerprint": index.fingerprint,
        "collection_name": index.collection_name,
        "index_settings": index.settings,
        "documents_sha256": index.documents_sha256,
        "documents": index.documents,
    }


async def ensure_index(db, index, vector_store, embedder):
    # Avoid blocking this event loop on a DB lock held by another local coroutine.
    locks = _index_locks.setdefault(asyncio.get_running_loop(), WeakValueDictionary())
    key = str(index.id)
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    async with lock:
        return await _ensure_index(db, index, vector_store, embedder)


async def _ensure_index(db, index, vector_store, embedder):
    """Готовая коллекция неизменяема; новые параметры строятся отдельно."""
    index = (
        db.query(IndexVersion)
        .filter_by(id=index.id)
        .with_for_update(key_share=True)
        .populate_existing()
        .one()
    )
    if index.status == "ready":
        await vector_store.validate_collection(
            index.collection_name, index.settings["embedding_dimension"]
        )
        db.commit()
        return False
    try:
        chunks = []
        counts = {}
        spec = index.settings
        for entry in index.documents:
            version = db.get(DocumentVersion, UUID(entry["document_version_id"]))
            path = Path(version.file_path)
            content = path.read_bytes()
            if (
                len(content) != entry["size_bytes"]
                or hashlib.sha256(content).hexdigest() != entry["sha256"]
            ):
                raise ValueError(
                    "Контрольная сумма сохранённого документа не совпадает с манифестом."
                )
            parts = process_file(
                path, entry["file_name"], spec["chunk_size"], spec["chunk_overlap"]
            )
            if not parts:
                raise ValueError("Документ не содержит фрагментов для индексации.")
            for part in parts:
                part.id = str(
                    uuid5(index.id, f"{version.id}:{part.metadata['chunk_index']}")
                )
                part.metadata.update(
                    document_version_id=str(version.id),
                    document_sha256=version.sha256,
                    index_version_id=str(index.id),
                )
            chunks.extend(parts)
            counts[str(version.id)] = len(parts)
        if chunks:
            embeddings = await embedder.embed([chunk.text for chunk in chunks])
            assign_embeddings(
                chunks, embeddings, spec["embedding_dimension"], spec["embedding_model"]
            )
        await vector_store.create_collection(
            index.collection_name, spec["embedding_dimension"]
        )
        if chunks:
            await vector_store.upsert(index.collection_name, chunks)
        index.status = "ready"
        index.chunks_count = len(chunks)
        index.document_chunks = counts
        db.commit()
        return True
    except Exception:
        index.status = "failed"
        db.commit()
        raise


def mark_sources(db, index):
    for source in (
        db.query(DataSource)
        .filter_by(project_id=index.project_id, is_active=True)
        .all()
    ):
        count = index.document_chunks.get(str(source.document_version_id))
        if count is not None:
            source.status = "completed"
            source.error_message = None
            source.chunks_count = count
