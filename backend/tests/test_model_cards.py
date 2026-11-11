"""Model cards freeze evidence without generating answers or inventing measurements."""

import importlib.util
import io
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from docx import Document
from sqlalchemy import create_engine, inspect, text

from backend.db.models import DataSource, ExecutionRun, IndexVersion, ModelCardVersion
from backend.tests import test_rag_pipeline as pipeline
from backend.tests.conftest import TestingSessionLocal

rag = pipeline.rag
BASE = pipeline.BASE


@pytest.fixture
def card_project(client):
    created = client.post(
        f"{BASE}/projects/",
        json={
            "name": "Помощник по регламентам",
            "llm_model": "my-llm",
            "embedding_model": "my-embedder",
            "llm_api_key": "private-model-key",
            "embedding_api_key": "private-embed-key",
            "llm_api_url": "https://user:password@provider.example/private-path?token=secret#hidden",
            "system_prompt": "A private system instruction",
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    return client, project, f"{BASE}/projects/{project['id']}/model-cards"


def prepare(client, url, **payload):
    response = client.post(url + "/preview", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def save(client, url, draft=None, base=None):
    draft = draft or prepare(client, url)
    response = client.post(
        url,
        json={
            "source_sha256": draft["source_sha256"],
            "base_version_id": base,
            "context": draft["context"],
            "measurements": draft["measurements"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_preview_new_project_is_read_only_and_has_google_sections(
    card_project, monkeypatch
):
    client, _project, url = card_project
    monkeypatch.setattr(
        "backend.core.factories.build_llm", lambda *a, **kw: pytest.fail("AI called")
    )
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("Document read"))
    for _ in range(2):
        draft = prepare(client, url)
        assert len(draft["sections"]) == 9
        assert draft["sections"][0]["english_title"] == "Model Details"
        assert draft["sections"][-1]["english_title"] == "Caveats and Recommendations"
        assert draft["measurements"] == []
        assert "не оценивались" in str(draft["sections"])
        assert draft["source_snapshot"]["configuration"]["llm_model"] == "my-llm"
        assert draft["source_snapshot"]["index"] is None
    with TestingSessionLocal() as db:
        assert db.query(ModelCardVersion).count() == 0
        assert db.query(ExecutionRun).count() == 0
        assert db.query(IndexVersion).count() == 0
    assert client.get(url).json() == {"total": 0, "items": []}


def test_saved_versions_are_immutable_and_conflicting_edits_are_rejected(card_project):
    client, _project, url = card_project
    first = save(
        client,
        url,
        prepare(client, url, context={"intended_use": "Ответы по регламенту"}),
    )
    assert first["number"] == 1
    draft = prepare(client, url, context={"intended_use": "Новая редакция"})
    second = save(client, url, draft, first["id"])
    assert second["number"] == 2
    assert (
        client.get(url + f"/{first['id']}").json()["context"]["intended_use"]
        == "Ответы по регламенту"
    )
    conflict = client.post(
        url,
        json={"source_sha256": draft["source_sha256"], "base_version_id": first["id"]},
    )
    assert conflict.status_code == 409
    page = client.get(url + "?limit=1").json()
    assert page["total"] == 2 and page["items"][0]["id"] == second["id"]
    assert client.get(url + "?offset=1&limit=1").json()["items"][0]["id"] == first["id"]
    with TestingSessionLocal() as db:
        record = db.get(ModelCardVersion, UUID(first["id"]))
        record.context = {"intended_use": "changed"}
        with pytest.raises(ValueError, match="неизменяема"):
            db.commit()
        db.rollback()


@pytest.mark.parametrize(
    "change",
    [
        {"llm_model": "different"},
        {"embedding_model": "different"},
        {"system_prompt": "changed"},
        {"top_k": 8},
        {"chunk_size": 400},
        {"max_context_chars": 900},
        {"name": "Changed"},
        {"llm_api_url": "https://provider.example/another-private-path"},
    ],
)
def test_changes_invalidate_card_and_preview_but_keep_exports_frozen(
    card_project, change
):
    client, project, url = card_project
    draft = prepare(client, url)
    saved = save(client, url, draft)
    before = client.get(f"{url}/{saved['id']}/export").content
    assert (
        client.patch(f"{BASE}/projects/{project['id']}", json=change).status_code == 200
    )
    old = client.get(f"{url}/{saved['id']}").json()
    assert old["is_stale"] and old["changed_fields"]
    assert old["source_snapshot"] == saved["source_snapshot"]
    assert client.get(f"{url}/{saved['id']}/export").content == before
    rejected = client.post(
        url,
        json={"base_version_id": saved["id"], "source_sha256": draft["source_sha256"]},
    )
    assert rejected.status_code == 409


def test_credentials_rotation_does_not_invalidate_public_configuration(card_project):
    client, project, url = card_project
    card = save(client, url)
    client.patch(f"{BASE}/projects/{project['id']}", json={"llm_api_key": "new-key"})
    assert not client.get(f"{url}/{card['id']}").json()["is_stale"]


def test_no_credentials_prompt_document_names_or_dialogues_in_exports(card_project):
    client, _project, url = card_project
    card = save(client, url)
    markdown = client.get(f"{url}/{card['id']}/export").text
    response = client.get(f"{url}/{card['id']}/export?format=docx")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        xml = "\n".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xml"))
    combined = str(card) + markdown + xml
    for secret in [
        "private-model-key",
        "private-embed-key",
        "private-path",
        "token=secret",
        "user:password",
        "A private system instruction",
    ]:
        assert secret not in combined
    assert "provider.example" in combined
    doc = Document(io.BytesIO(response.content))
    headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert len(headings) == 10
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "context",
    [
        {"intended_use": "private-model-key"},
        {"owner": "Bearer unknown-credential"},
        {"license": "https://name:password@example.com"},
    ],
)
def test_authored_credentials_are_rejected_without_echo(card_project, context):
    client, _project, url = card_project
    response = client.post(url + "/preview", json={"context": context})
    assert response.status_code == 422
    assert all(value not in response.text for value in context.values())


def measurement(**extra):
    return {
        "name": "Корректность",
        "value": 0,
        "unit": "%",
        "sample_size": 40,
        "dataset": "Контроль v1, test",
        "method": "Проверка по эталону",
        "measured_at": "2026-11-11",
        "reviewer": "Проверяющий",
        **extra,
    }


def test_manual_measurements_have_provenance_and_preserve_zero(card_project):
    client, _project, url = card_project
    card = save(client, url, prepare(client, url, measurements=[measurement()]))
    text = client.get(f"{url}/{card['id']}/export").text
    assert "Ручная оценка" in text and "Проверка по эталону" in text
    assert card["measurements"][0]["value"] == 0
    assert "40" in text and "2026" in text
    bad = measurement()
    del bad["method"]
    assert (
        client.post(url + "/preview", json={"measurements": [bad]}).status_code == 422
    )
    assert (
        client.post(
            url + "/preview", json={"measurements": [measurement(sample_size=0)]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url + "/preview", json={"context": {"invented": "field"}}
        ).status_code
        == 422
    )


def test_scope_and_invalid_requests(card_project):
    client, _project, url = card_project
    card = save(client, url)
    other = client.post(
        f"{BASE}/projects/",
        json={"name": "Other", "llm_model": "other", "embedding_model": "other"},
    ).json()
    foreign = f"{BASE}/projects/{other['id']}/model-cards"
    assert client.get(f"{foreign}/{card['id']}").status_code == 404
    assert client.get(f"{foreign}/{card['id']}/export?format=docx").status_code == 404
    assert client.get(f"{url}/{uuid4()}").status_code == 404
    assert (
        client.post(
            f"{BASE}/projects/{uuid4()}/model-cards/preview", json={}
        ).status_code
        == 404
    )
    assert client.get(f"{url}/{card['id']}/export?format=html").status_code == 422
    assert client.post(url, json={"source_sha256": "bad"}).status_code == 422


def test_documents_change_freshness_without_reading_or_exporting_content(
    rag, monkeypatch
):
    assert pipeline.upload(rag).json()["status"] == "completed"
    url = f"{BASE}/projects/{rag.project_id}/model-cards"
    draft = prepare(rag.client, url)
    assert draft["source_snapshot"]["index"]["status"] == "ready"
    assert len(draft["source_snapshot"]["documents"]) == 1
    card = save(rag.client, url, draft)
    exported = rag.client.get(f"{url}/{card['id']}/export").text
    assert pipeline.ANSWER not in exported and pipeline.CONTROL.name not in exported
    calls = rag.completion.call_count
    assert pipeline.upload(rag, b"replacement corpus").json()["status"] == "completed"
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("document read"))
    old = rag.client.get(f"{url}/{card['id']}").json()
    assert old["is_stale"] and "documents" in old["changed_fields"]
    assert rag.completion.call_count == calls


def test_legacy_documents_are_reported_without_fake_checksums(card_project):
    client, project, url = card_project
    with TestingSessionLocal() as db:
        db.add(
            DataSource(
                project_id=UUID(project["id"]),
                file_name="private-name.txt",
                file_path="/private/path",
                status="completed",
            )
        )
        db.commit()
    draft = prepare(client, url)
    assert draft["source_snapshot"]["documents"][0]["sha256"] is None
    assert "private-name" not in str(draft) and "/private/path" not in str(draft)
    assert "Без версии документа: 1" in str(draft["sections"])


def test_project_delete_cleans_up_cards(card_project):
    client, project, url = card_project
    card = save(client, url)
    with TestingSessionLocal() as db:
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.commit()
    assert client.delete(f"{BASE}/projects/{project['id']}").status_code == 204
    with TestingSessionLocal() as db:
        assert db.get(ModelCardVersion, UUID(card["id"])) is None


def test_migration_upgrade_and_downgrade_preserves_project():
    migration_path = (
        Path(__file__).parents[1] / "alembic/versions/20261111_0011_model_cards.py"
    )
    spec = importlib.util.spec_from_file_location(
        "model_card_migration", migration_path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE projects (id UUID PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "model_card_versions" in inspect(connection).get_table_names()
            migration.downgrade()
        assert inspect(connection).get_table_names() == ["projects"]
    engine.dispose()


def test_markdown_escapes_authored_html_and_xml_control_is_rejected(card_project):
    client, _project, url = card_project
    card = save(
        client,
        url,
        prepare(
            client,
            url,
            context={
                "intended_use": "<script>alert(1)</script> [click](javascript:alert(1))"
            },
        ),
    )
    text = client.get(f"{url}/{card['id']}/export").text
    assert "<script>" not in text and "[click](javascript:" not in text
    response = client.post(
        url + "/preview", json={"context": {"owner": "invalid\x00control"}}
    )
    assert response.status_code == 422
