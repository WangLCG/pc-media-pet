"""VoiceChannelManager unit tests with fake audio track and player."""

import asyncio

from av import AudioFrame
import pytest

from app.config import Settings
from app.models import VoiceDeniedPayload, VoiceGrantedPayload, VoiceStartPayload
from app.voice_channel import AudioPlayer, VoiceChannelManager


def FakeAudioFrame(samples: int = 960, sample_rate: int = 48000, channels: int = 1) -> AudioFrame:
    """Create a silent frame matching the shape of an aiortc audio frame."""
    layout = "mono" if channels == 1 else "stereo"
    frame = AudioFrame(format="s16", layout=layout, samples=samples)
    frame.sample_rate = sample_rate
    frame.planes[0].update(bytes(samples * channels * 2))
    return frame


class FakeAudioTrack:
    """Simulates aiortc AudioStreamTrack for unit tests."""

    def __init__(self, frames: int | None = None) -> None:
        self.recv_count = 0
        self._frames = frames  # None = infinite frames
        self._recv_event = asyncio.Event()

    async def recv(self):
        if self._frames is not None:
            if self._frames <= 0:
                self._recv_event.set()
                await asyncio.sleep(10)
                raise asyncio.CancelledError
            self._frames -= 1
        self.recv_count += 1
        # Model a media source's scheduling point so test consumers cannot
        # busy-loop and starve the test runner (or the desktop on Windows).
        await asyncio.sleep(0)
        return FakeAudioFrame()


class FakeAudioPlayer:
    """Records written PCM data, never touches real hardware."""

    def __init__(self, sample_rate: int = 48000, channels: int = 1, frames_per_buffer: int = 960) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.sample_rate = sample_rate
        self.channels = channels

    def open(self) -> None:
        pass

    def write(self, pcm_bytes: bytes) -> None:
        self.writes.append(pcm_bytes)

    def close(self) -> None:
        self.closed = True


def settings(**overrides) -> Settings:
    return Settings(app_token="test-token-that-is-long-enough", **overrides)


def make_manager(**setting_overrides) -> VoiceChannelManager:
    return VoiceChannelManager(settings(**setting_overrides), _audio_player_factory=FakeAudioPlayer)


@pytest.mark.asyncio
async def test_voice_start_grants_when_under_limit():
    manager = make_manager()
    try:
        for i in range(3):
            cid = f"browser-0{i}"
            manager.register_track(cid, FakeAudioTrack())
            result = await manager.handle_voice_start(cid, VoiceStartPayload())
            assert isinstance(result, VoiceGrantedPayload)
        assert manager.sender_count == 3
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_start_denies_when_at_limit():
    manager = make_manager()
    try:
        for i in range(3):
            cid = f"browser-0{i}"
            manager.register_track(cid, FakeAudioTrack())
            await manager.handle_voice_start(cid, VoiceStartPayload())
        manager.register_track("browser-04", FakeAudioTrack())
        result = await manager.handle_voice_start("browser-04", VoiceStartPayload())
        assert isinstance(result, VoiceDeniedPayload)
        assert result.reason == "voice_senders_full"
        assert result.current_senders == 3
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_stop_releases_slot():
    manager = make_manager()
    try:
        manager.register_track("browser-01", FakeAudioTrack())
        await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert manager.sender_count == 1
        await manager.handle_voice_stop("browser-01")
        assert manager.sender_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_stop_nonexistent_client_is_noop():
    manager = make_manager()
    try:
        await manager.handle_voice_stop("nobody")
        assert manager.sender_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_start_replaces_same_client():
    manager = make_manager()
    try:
        manager.register_track("browser-01", FakeAudioTrack())
        first = await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert isinstance(first, VoiceGrantedPayload)
        second = await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert isinstance(second, VoiceGrantedPayload)
        assert second.voice_id != first.voice_id
        assert manager.sender_count == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_denied_when_no_track_registered():
    manager = make_manager()
    try:
        result = await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert isinstance(result, VoiceDeniedPayload)
        assert result.reason == "no_audio_track"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_close_sender_cleans_up_track_and_sender():
    manager = make_manager()
    try:
        manager.register_track("browser-01", FakeAudioTrack())
        await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert manager.sender_count == 1
        await manager.close_sender("browser-01")
        assert manager.sender_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_close_releases_all_senders():
    manager = make_manager()
    try:
        for i in range(3):
            cid = f"browser-0{i}"
            manager.register_track(cid, FakeAudioTrack())
            await manager.handle_voice_start(cid, VoiceStartPayload())
        assert manager.sender_count == 3
        await manager.close()
        assert manager.sender_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_sender_count_changes_are_accurate():
    manager = make_manager()
    try:
        assert manager.sender_count == 0
        for i in range(2):
            cid = f"browser-0{i}"
            manager.register_track(cid, FakeAudioTrack())
            await manager.handle_voice_start(cid, VoiceStartPayload())
        assert manager.sender_count == 2
        await manager.handle_voice_stop("browser-00")
        assert manager.sender_count == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_start_grants_up_to_max_senders_boundary():
    manager = make_manager()
    try:
        manager.register_track("a", FakeAudioTrack())
        manager.register_track("b", FakeAudioTrack())
        manager.register_track("c", FakeAudioTrack())
        manager.register_track("d", FakeAudioTrack())
        assert isinstance(await manager.handle_voice_start("a", VoiceStartPayload()), VoiceGrantedPayload)
        assert isinstance(await manager.handle_voice_start("b", VoiceStartPayload()), VoiceGrantedPayload)
        assert isinstance(await manager.handle_voice_start("c", VoiceStartPayload()), VoiceGrantedPayload)
        assert isinstance(await manager.handle_voice_start("d", VoiceStartPayload()), VoiceDeniedPayload)
    finally:
        await manager.close()


class FailingAudioTrack(FakeAudioTrack):
    """Raises after a few frames to test consumer-crash cleanup."""

    def __init__(self, frames: int = 1) -> None:
        super().__init__(frames=frames)

    async def recv(self):
        if self._frames is not None and self._frames <= 0:
            raise RuntimeError("simulated recv failure")
        if self._frames is not None:
            self._frames -= 1
        self.recv_count += 1
        return FakeAudioFrame()


class ImmediateAudioTrack(FakeAudioTrack):
    """A deliberately non-cooperative track used to guard against busy loops."""

    async def recv(self):
        self.recv_count += 1
        return FakeAudioFrame()


class LowRateStereoTrack(FakeAudioTrack):
    """Supplies a non-standard source format to verify playback normalization."""

    async def recv(self):
        self.recv_count += 1
        await asyncio.sleep(0)
        return FakeAudioFrame(samples=320, sample_rate=16_000, channels=2)


@pytest.mark.asyncio
async def test_consumer_crash_releases_sender_slot():
    manager = VoiceChannelManager(settings(), _audio_player_factory=FakeAudioPlayer)
    try:
        manager.register_track("browser-01", FailingAudioTrack(frames=2))
        result = await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert isinstance(result, VoiceGrantedPayload)
        assert manager.sender_count == 1

        await asyncio.sleep(0.1)
        assert manager.sender_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_consumer_yields_when_track_returns_frames_immediately():
    manager = make_manager()
    try:
        manager.register_track("browser-01", ImmediateAudioTrack())
        result = await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert isinstance(result, VoiceGrantedPayload)

        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        await manager.close()
        assert manager.sender_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_voice_playback_is_normalized_to_stable_device_format():
    manager = make_manager()
    try:
        manager.register_track("browser-01", LowRateStereoTrack())
        result = await manager.handle_voice_start("browser-01", VoiceStartPayload())
        assert isinstance(result, VoiceGrantedPayload)

        player = manager._senders["browser-01"]._audio_player
        assert isinstance(player, FakeAudioPlayer)
        assert player.sample_rate == 48_000
        assert player.channels == 1
    finally:
        await manager.close()
