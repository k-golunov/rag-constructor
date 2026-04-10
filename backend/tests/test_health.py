import pytest
from fastapi.testclient import TestClient
from ..main import app

@pytest.fixture(scope="function")
def client():
    return TestClient(app)

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}