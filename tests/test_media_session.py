import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import Settings
from app.media_session import CameraVideoTrack, MediaSessionManager
from app.models import MediaOffer


def settings(**overrides) -> Settings:
    return Settings(app_token="test-token-that-is-long-enough", **overrides)


class FakeCamera:
    def __init__(self):
        self.modes = []
        self.resolutions = []
        self.frame = np.zeros((20, 30, 3), dtype=np.uint8)

    async def set_mode(self, mode):
        self.modes.append(mode)

    async def set_view_resolution(self, width, height):
        self.resolutions.append((width, height))

    async def read_frame_for_media(self):
        return self.frame.copy()


class FakePeerConnection:
    def __init__(self, configuration=None):
        self.configuration = configuration
        self.connectionState = "new"
        self.iceGatheringState = "complete"
        self.localDescription = None
        self.handlers = {}
        self.tracks = []
        self.closed = False

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback

        return register

    def addTrack(self, track):
        self.tracks.append(track)

    async def setRemoteDescription(self, description):
        self.remote_description = description

    async def createAnswer(self):
        return SimpleNamespace(sdp="media-answer-sdp", type="answer")

    async def setLocalDescription(self, description):
        self.localDescription = description

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_media_session_uses_shared_camera_and_returns_to_idle(monkeypatch):
    peer_connection = FakePeerConnection()
    monkeypatch.setattr("app.media_session.RTCPeerConnection", lambda configuration: peer_connection)
    camera = FakeCamera()
    states = []

    async def publish_state(event):
        states.append(event)

    manager = MediaSessionManager(settings(), camera, publish_state)

    answer = await manager.create_answer(MediaOffer(client_id="browser-01", sdp="offer-sdp", type="offer"))

    assert answer.type == "answer"
    assert manager.session_count == 1
    assert camera.resolutions == [(1280, 720)]
    assert camera.modes == ["view"]
    assert len(peer_connection.tracks) == 1
    assert isinstance(peer_connection.tracks[0], CameraVideoTrack)

    assert await manager.stop_session(answer.session_id, "browser-01") is True
    assert peer_connection.closed is True
    assert manager.session_count == 0
    assert camera.modes == ["view", "idle"]
    assert [(event.state, event.session_id) for event in states] == [("started", answer.session_id), ("stopped", answer.session_id)]


@pytest.mark.asyncio
async def test_media_session_rejects_stop_from_another_client(monkeypatch):
    monkeypatch.setattr("app.media_session.RTCPeerConnection", FakePeerConnection)
    manager = MediaSessionManager(settings(), FakeCamera())
    answer = await manager.create_answer(MediaOffer(client_id="browser-01", sdp="offer-sdp", type="offer"))

    with pytest.raises(PermissionError):
        await manager.stop_session(answer.session_id, "browser-02")

    await manager.close()


@pytest.mark.asyncio
async def test_media_session_closes_when_connection_does_not_establish(monkeypatch):
    peer_connection = FakePeerConnection()
    monkeypatch.setattr("app.media_session.RTCPeerConnection", lambda configuration: peer_connection)
    camera = FakeCamera()
    manager = MediaSessionManager(settings(media_idle_timeout_seconds=1), camera)
    answer = await manager.create_answer(MediaOffer(client_id="browser-01", sdp="offer-sdp", type="offer"))

    await asyncio.sleep(1.05)

    assert manager.session_count == 0
    assert peer_connection.closed is True
    assert camera.modes == ["view", "idle"]


@pytest.mark.asyncio
async def test_connected_media_session_does_not_expire_after_connect_timeout(monkeypatch):
    peer_connection = FakePeerConnection()
    monkeypatch.setattr("app.media_session.RTCPeerConnection", lambda configuration: peer_connection)
    camera = FakeCamera()
    manager = MediaSessionManager(settings(media_idle_timeout_seconds=1), camera)
    answer = await manager.create_answer(MediaOffer(client_id="browser-01", sdp="offer-sdp", type="offer"))
    peer_connection.connectionState = "connected"

    await asyncio.sleep(1.05)

    assert manager.session_count == 1
    assert peer_connection.closed is False
    await manager.stop_session(answer.session_id, "browser-01")


@pytest.mark.asyncio
async def test_camera_video_track_converts_shared_bgr_frame():
    camera = FakeCamera()
    track = CameraVideoTrack(camera, fps=15)

    frame = await track.recv()

    assert frame.width == 30
    assert frame.height == 20
