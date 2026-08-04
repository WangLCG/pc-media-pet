"""Integration tests: voice messages routed through NotifyChannelManager."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import NotifyOffer, VoiceStartPayload
from app.notify_channel import NotifyChannelManager
from app.voice_channel import VoiceChannelManager
from tests.test_voice_channel import FakeAudioPlayer


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
        return SimpleNamespace(sdp="answer-sdp", type="answer")

    async def setLocalDescription(self, description):
        self.localDescription = description

    async def close(self):
        self.closed = True


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


async def connected_voice_manager(monkeypatch, **setting_overrides):
    peer_connection = ImmediatePeerConnection()
    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", lambda configuration: peer_connection)
    voice_manager = VoiceChannelManager(settings(**setting_overrides), _audio_player_factory=FakeAudioPlayer)
    notify_manager = NotifyChannelManager(settings(**setting_overrides), voice_manager=voice_manager)
    offer = NotifyOffer(client_id="browser-01", sdp="offer-sdp", type="offer")
    await notify_manager.create_answer(offer)
    client = notify_manager._clients[offer.client_id]
    channel = OpenDataChannel()
    client.channel = channel
    notify_manager._bind_channel(client, channel)
    channel.handlers["open"]()
    return notify_manager, voice_manager, peer_connection, channel


@pytest.mark.asyncio
async def test_voice_start_is_routed_to_voice_manager(monkeypatch):
    notify_manager, voice_manager, _, channel = await connected_voice_manager(monkeypatch)
    try:
        from tests.test_voice_channel import FakeAudioTrack
        voice_manager.register_track("browser-01", FakeAudioTrack())
        channel.handlers["message"](json.dumps({
            "version": 1, "type": "voice_start", "id": "vstart_01", "ts": 0,
            "payload": VoiceStartPayload().model_dump(),
        }))
        await asyncio.sleep(0.05)
        assert voice_manager.sender_count == 1
        sent = [json.loads(m) for m in channel.messages if json.loads(m)["type"] == "voice_granted"]
        assert len(sent) == 1
        assert sent[0]["payload"]["voice_id"].startswith("voice_")
    finally:
        await voice_manager.close()
        await notify_manager.close()


@pytest.mark.asyncio
async def test_voice_denied_when_limit_reached(monkeypatch):
    notify_manager, voice_manager, _, channel = await connected_voice_manager(monkeypatch)
    try:
        from tests.test_voice_channel import FakeAudioTrack
        for i in range(3):
            cid = f"filler-{i:02d}"
            voice_manager.register_track(cid, FakeAudioTrack())
            await voice_manager.handle_voice_start(cid, VoiceStartPayload())
        assert voice_manager.sender_count == 3

        voice_manager.register_track("browser-01", FakeAudioTrack())
        channel.handlers["message"](json.dumps({
            "version": 1, "type": "voice_start", "id": "vstart_01", "ts": 0,
            "payload": VoiceStartPayload().model_dump(),
        }))
        await asyncio.sleep(0.05)
        denied = [json.loads(m) for m in channel.messages if json.loads(m)["type"] == "voice_denied"]
        assert len(denied) == 1
        assert denied[0]["payload"]["reason"] == "voice_senders_full"
    finally:
        await voice_manager.close()
        await notify_manager.close()


@pytest.mark.asyncio
async def test_voice_stop_releases_slot_and_allows_new_sender(monkeypatch):
    notify_manager, voice_manager, _, channel = await connected_voice_manager(monkeypatch)
    try:
        from tests.test_voice_channel import FakeAudioTrack
        voice_manager.register_track("browser-01", FakeAudioTrack())
        channel.handlers["message"](json.dumps({
            "version": 1, "type": "voice_start", "id": "vstart_01", "ts": 0,
            "payload": VoiceStartPayload().model_dump(),
        }))
        await asyncio.sleep(0.05)
        assert voice_manager.sender_count == 1

        channel.handlers["message"](json.dumps({
            "version": 1, "type": "voice_stop", "id": "vstop_01", "ts": 0,
            "payload": {},
        }))
        await asyncio.sleep(0.05)
        assert voice_manager.sender_count == 0
    finally:
        await voice_manager.close()
        await notify_manager.close()


@pytest.mark.asyncio
async def test_client_disconnect_cleans_voice_sender(monkeypatch):
    peer_connection = ImmediatePeerConnection()
    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", lambda configuration: peer_connection)
    voice_manager = VoiceChannelManager(settings(), _audio_player_factory=FakeAudioPlayer)
    notify_manager = NotifyChannelManager(settings(), voice_manager=voice_manager)
    try:
        offer = NotifyOffer(client_id="browser-01", sdp="offer-sdp", type="offer")
        await notify_manager.create_answer(offer)
        client = notify_manager._clients[offer.client_id]
        channel = OpenDataChannel()
        client.channel = channel
        notify_manager._bind_channel(client, channel)
        channel.handlers["open"]()
        from tests.test_voice_channel import FakeAudioTrack
        voice_manager.register_track("browser-01", FakeAudioTrack())
        await voice_manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert voice_manager.sender_count == 1

        peer_connection.connectionState = "failed"
        handler = peer_connection.handlers.get("connectionstatechange")
        assert handler is not None
        await handler()
        await asyncio.sleep(0.05)
        assert voice_manager.sender_count == 0
    finally:
        await voice_manager.close()
        await notify_manager.close()
