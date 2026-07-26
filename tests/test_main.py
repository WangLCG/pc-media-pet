import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import app


def test_health_is_public(monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "test-token-that-is-long-enough")
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_status_requires_a_valid_bearer_token(monkeypatch):
    token = "test-token-that-is-long-enough"
    monkeypatch.setenv("APP_TOKEN", token)
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        response = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["service"] == "running"


def test_example_token_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "change-me-long-random-token")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="unique, non-example secret"):
        Settings()
