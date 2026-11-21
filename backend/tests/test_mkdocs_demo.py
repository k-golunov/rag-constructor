import json
from pathlib import Path
from unittest.mock import Mock

from backend.config import settings
from backend.tests import test_rag_pipeline as pipeline

PROJECTS = "/api/v1/public/provider/projects"
DEMO = f"{PROJECTS}/demo/mkdocs"
DATASET = Path(__file__).parents[1] / "datasets" / "mkdocs_demo"
PROJECT_SETTINGS = {
    "name": "Помощник по MkDocs",
    "embedding_model": "text-embedding-3-small",
    "llm_model": "gpt-4o-mini",
}
rag = pipeline.rag


def test_demo_creates_isolated_versioned_sets_without_calling_providers(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    provider = Mock(side_effect=AssertionError("При создании проекта вызвана модель"))
    monkeypatch.setattr("backend.core.embeddings.openai_embedder.AsyncOpenAI", provider)
    monkeypatch.setattr("backend.core.llm.openai_llm.AsyncOpenAI", provider)
    created = client.post(DEMO, json=PROJECT_SETTINGS)
    assert created.status_code == 201, created.text
    project = f"{PROJECTS}/{created.json()['id']}"
    sources = client.get(f"{project}/data-sources").json()["items"]
    assert len(sources) == 20
    assert {source["status"] for source in sources} == {"pending"}

    [question_set] = client.get(f"{project}/question-sets").json()
    version_id = question_set["latest_version"]["id"]
    version = client.get(
        f"{project}/question-sets/{question_set['id']}/versions/{version_id}"
    ).json()
    assert {source["document_version_id"] for source in sources} == {
        source["document_version_id"] for source in version["documents"]
    }
    examples = version["examples"]
    assert len(examples) == 90
    assert sum(example["unanswerable"] for example in examples) == 16
    assert {example["review"]["status"] for example in examples} == {"pending"}
    categories = {
        "Факты",
        "Условия и исключения",
        "Перечисления",
        "Несколько разделов",
        "Нет ответа",
    }
    for split in ("tuning", "test"):
        assert {
            row["category"] for row in examples if row["split"] == split
        } == categories
    corpus = json.loads((DATASET / "sources.json").read_text(encoding="utf-8"))
    assert set(corpus) == {source["file_name"] for source in sources}
    for row in examples:
        assert row["reference_answer"]
        if row["unanswerable"]:
            assert row["reference_answer"].startswith("В корпусе нет ответа. ")
            assert not row["evidence"]
        else:
            assert row["evidence"]
            for evidence in row["evidence"]:
                text = (DATASET / "corpus" / evidence["file_name"]).read_text(
                    encoding="utf-8"
                )
                assert (
                    text[evidence["start_char"] : evidence["end_char"]]
                    == evidence["quote"]
                )

    second = client.post(DEMO, json=PROJECT_SETTINGS)
    assert second.status_code == 201
    second_id = second.json()["id"]
    assert second_id != created.json()["id"]
    second_sources = client.get(f"{PROJECTS}/{second_id}/data-sources").json()["items"]
    assert not {source["document_version_id"] for source in sources} & {
        source["document_version_id"] for source in second_sources
    }
    provider.assert_not_called()


def test_demo_with_invalid_citation_rolls_back_project(client, tmp_path, monkeypatch):
    from backend.api import mkdocs_demo

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    data = json.loads((DATASET / "questions.json").read_text(encoding="utf-8"))
    data["examples"][39]["evidence"][0]["quote"] = "Несуществующая выдержка."
    invalid_data = tmp_path / "invalid.json"
    invalid_data.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(mkdocs_demo, "QUESTIONS_PATH", invalid_data)
    response = client.post(DEMO, json=PROJECT_SETTINGS)
    assert response.status_code == 500, response.text
    assert client.get(f"{PROJECTS}/").json()["total"] == 0
    assert list((tmp_path / "uploads").glob("*")) == []


def test_demo_corpus_is_indexed_on_first_chat(rag):
    response = rag.client.post(
        DEMO,
        json={**PROJECT_SETTINGS, "embedding_dimension": 3, "llm_api_key": "test-key"},
    )
    assert response.status_code == 201, response.text
    project = f"{PROJECTS}/{response.json()['id']}"

    async def embeddings(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    rag.embedder.embed.side_effect = embeddings
    chat = rag.client.post(
        f"{project}/chat", json={"question": "Как проверить версию MkDocs?"}
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["sources"]
    sources = rag.client.get(f"{project}/data-sources").json()["items"]
    assert len(sources) == 20
    assert all(source["status"] == "completed" for source in sources)
    assert all(source["chunks_count"] > 0 for source in sources)
