import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import app
from tests.test_media_session import FakePeerConnection


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


def test_camera_capabilities_require_a_valid_bearer_token(monkeypatch):
    token = "test-token-that-is-long-enough"
    monkeypatch.setenv("APP_TOKEN", token)
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/camera/capabilities").status_code == 401
        response = client.get("/api/camera/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "resolutions" in response.json()


def test_example_token_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "change-me-long-random-token")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="unique, non-example secret"):
        Settings()


def test_media_endpoints_enforce_ownership(monkeypatch):
    token = "test-token-that-is-long-enough"
    monkeypatch.setenv("APP_TOKEN", token)
    monkeypatch.setattr("app.media_session.RTCPeerConnection", FakePeerConnection)
    get_settings.cache_clear()

    with TestClient(app) as client:
        offer = {"client_id": "browser-01", "sdp": "offer-sdp", "type": "offer", "video": True, "audio": False}
        assert client.post("/api/media/offer", json=offer).status_code == 401
        response = client.post("/api/media/offer", headers={"Authorization": f"Bearer {token}"}, json=offer)
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        forbidden = client.post(
            "/api/media/stop",
            headers={"Authorization": f"Bearer {token}"},
            json={"client_id": "browser-02", "session_id": session_id},
        )
        assert forbidden.status_code == 403
        stopped = client.post(
            "/api/media/stop",
            headers={"Authorization": f"Bearer {token}"},
            json={"client_id": "browser-01", "session_id": session_id},
        )
        assert stopped.status_code == 200
        assert stopped.json() == {"ok": True}
