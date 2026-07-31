from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import CameraErrorEvent, NotifyEnvelope, NotifyOffer
from app.notify_channel import NotifyChannelManager


class FakePeerConnection:
    """Small aiortc replacement used to verify manager ownership behavior."""

    def __init__(self, configuration=None):
        self.configuration = configuration
        self.connectionState = "new"
        self.iceGatheringState = "complete"
        self.localDescription = None
        self.closed = False
        self.handlers = {}

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback

        return register

    async def setRemoteDescription(self, description):
        self.remote_description = description

    async def createAnswer(self):
        return SimpleNamespace(sdp="answer-sdp", type="answer")

    async def setLocalDescription(self, description):
        self.localDescription = description

    async def close(self):
        self.closed = True


def settings() -> Settings:
    return Settings(app_token="test-token-that-is-long-enough")


@pytest.mark.asyncio
async def test_offer_replaces_existing_connection(monkeypatch):
    created_connections = []

    def create_peer_connection(configuration):
        connection = FakePeerConnection(configuration)
        created_connections.append(connection)
        return connection

    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", create_peer_connection)
    manager = NotifyChannelManager(settings())
    offer = NotifyOffer(client_id="browser-01", sdp="offer-sdp", type="offer")

    first_answer = await manager.create_answer(offer)
    second_answer = await manager.create_answer(offer)

    assert first_answer.type == second_answer.type == "answer"
    assert manager.client_count == 1
    assert created_connections[0].closed is True
    assert created_connections[1].closed is False
    await manager.close()


def test_invalid_offer_and_message_envelope_are_rejected():
    with pytest.raises(ValidationError):
        NotifyOffer(client_id="invalid client id", sdp="offer", type="offer")
    with pytest.raises(ValidationError):
        NotifyEnvelope(version=1, type="ping", id="invalid id", ts=0, payload={})


def test_camera_error_envelope_type_and_payload_are_validated():
    event = CameraErrorEvent(code="camera_read_failed", message="USB camera frame capture failed")
    envelope = NotifyEnvelope(
        version=1,
        type="camera_error",
        id=event.id,
        ts=event.ts,
        payload=event.model_dump(exclude={"id", "ts"}),
    )

    assert envelope.type == "camera_error"
