"""Reject ineffective comparison axes and validate complete project settings."""

import pytest

BASE = "/api/v1/public/provider/projects"
PROJECT = {"name": "settings", "embedding_model": "control", "llm_model": "control"}


@pytest.mark.parametrize(
    "settings",
    [
        {"chunk_size": 100, "chunk_overlap": 100},
        {"chunk_size": 100, "chunk_overlap": 101},
        {"split_by": "sentences"},
        {"split_by": "tokens"},
        {"chunking_strategy": "semantic"},
        {"extract_tables": True},
        {"temperature": 0.5},
        {"top_k": 0},
        {"top_k": 101},
        {"max_context_chars": 0},
        {"max_context_chars": 100001},
    ],
)
def test_create_rejects_invalid_or_ineffective_settings(client, settings):
    response = client.post(f"{BASE}/", json={**PROJECT, **settings})
    assert response.status_code == 422
    assert client.get(f"{BASE}/").json()["total"] == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"chunk_size": 200},
        {"chunk_overlap": 500},
        {"split_by": "tokens"},
        {"extract_tables": True},
        {"temperature": 0.5},
        {"top_k": None},
        {"max_context_chars": None},
        {"chunk_size": None},
    ],
)
def test_patch_validates_merged_settings_without_mutation(client, settings):
    project = client.post(
        f"{BASE}/",
        json={
            **PROJECT,
            "chunk_size": 500,
            "chunk_overlap": 200,
        },
    ).json()
    url = f"{BASE}/{project['id']}"
    response = client.patch(url, json=settings)
    assert response.status_code == 422
    assert client.get(url).json() == project


def test_valid_patch_changes_both_chunk_parameters_and_retrieval(client):
    project = client.post(f"{BASE}/", json=PROJECT).json()
    url = f"{BASE}/{project['id']}"
    values = {
        "chunk_size": 100,
        "chunk_overlap": 99,
        "top_k": 12,
        "max_context_chars": 9000,
    }
    response = client.patch(url, json=values)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in values.items())
    assert client.get(url).json()["top_k"] == 12
