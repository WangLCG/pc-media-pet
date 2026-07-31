"""Regression specifications for known Phase 1 shortcomings.

Each strict xfail documents intended behaviour that is not implemented yet.
When a fix makes one pass, pytest reports XPASS so the marker can be removed.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.models import NotifyOffer
from app.notify_channel import NotifyChannelManager


class ImmediatePeerConnection:
    def __init__(self, configuration=None):
        self.configuration = configuration
        self.connectionState = "new"
        self.iceGatheringState = "complete"
        self.localDescription = None
        self.handlers = {}
        self.closed = False

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback

        return register

    async def setRemoteDescription(self, description):
        self.remote_description = description

    async def createAnswer(self):
        return type("Answer", (), {"sdp": "answer-sdp", "type": "answer"})()

    async def setLocalDescription(self, description):
        self.localDescription = description

    async def close(self):
        self.closed = True


class BlockingPeerConnection(ImmediatePeerConnection):
    def __init__(self, configuration=None):
        super().__init__(configuration)
        self.remote_description_started = asyncio.Event()
        self.allow_remote_description = asyncio.Event()

    async def setRemoteDescription(self, description):
        self.remote_description_started.set()
        await self.allow_remote_description.wait()
        self.remote_description = description


class OpenDataChannel:
    def __init__(self):
        self.readyState = "open"
        self.handlers = {}
        self.messages = []

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback

        return register

    def send(self, message):
        self.messages.append(message)


def settings(**overrides) -> Settings:
    return Settings(app_token="test-token-that-is-long-enough", **overrides)


@pytest.mark.asyncio
async def test_concurrent_offer_is_cancelled_when_same_client_reconnects(monkeypatch):
    first_connection = BlockingPeerConnection()
    connections = [first_connection, ImmediatePeerConnection()]
    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", lambda configuration: connections.pop(0))
    manager = NotifyChannelManager(settings())
    offer = NotifyOffer(client_id="browser-01", sdp="offer-sdp", type="offer")

    first_offer = asyncio.create_task(manager.create_answer(offer))
    await first_connection.remote_description_started.wait()
    await manager.create_answer(offer)
    first_connection.allow_remote_description.set()

    with pytest.raises(RuntimeError, match="replaced"):
        await first_offer
    await manager.close()


@pytest.mark.asyncio
async def test_offer_without_datachannel_expires(monkeypatch):
    peer_connection = ImmediatePeerConnection()
    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", lambda configuration: peer_connection)
    manager = NotifyChannelManager(settings(notify_channel_open_timeout_seconds=0.01))
    offer = NotifyOffer(client_id="silent-browser", sdp="offer-sdp", type="offer")

    try:
        await manager.create_answer(offer)
        await asyncio.sleep(0.05)

        assert manager.client_count == 0
        assert peer_connection.closed is True
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_client_is_closed_after_missed_pongs(monkeypatch):
    peer_connection = ImmediatePeerConnection()
    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", lambda configuration: peer_connection)
    manager = NotifyChannelManager(settings(notify_ping_interval_seconds=1, notify_missed_pong_limit=1))
    offer = NotifyOffer(client_id="unresponsive-browser", sdp="offer-sdp", type="offer")

    try:
        await manager.create_answer(offer)
        notify_client = manager._clients[offer.client_id]
        channel = OpenDataChannel()
        notify_client.channel = channel
        manager._bind_channel(notify_client, channel)
        channel.handlers["open"]()
        await asyncio.sleep(1.1)

        assert manager.client_count == 0
        assert peer_connection.closed is True
    finally:
        await manager.close()


def test_invalid_sdp_returns_a_client_error(monkeypatch):
    token = "test-token-that-is-long-enough"
    monkeypatch.setenv("APP_TOKEN", token)
    get_settings.cache_clear()

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/notify/offer",
            headers={"Authorization": f"Bearer {token}"},
            json={"client_id": "browser-01", "sdp": "not valid SDP", "type": "offer"},
        )

    assert response.status_code == 422
