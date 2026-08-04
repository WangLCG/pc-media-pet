"""Voice sender management with concurrency-limit enforcement and audio output."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiortc.mediastreams import AudioStreamTrack

from .config import Settings
from .models import VoiceDeniedPayload, VoiceGrantedPayload, VoiceStartPayload

logger = logging.getLogger(__name__)


@dataclass
class VoiceSender:
    """Resources owned by one active voice sender."""

    client_id: str
    voice_id: str
    _consumer_task: asyncio.Task[None] | None = None
    _audio_player: "AudioPlayer | None" = None


class AudioPlayer:
    """Thin wrapper around PyAudio for blocking speaker output."""

    def __init__(self, sample_rate: int = 48000, channels: int = 1, frames_per_buffer: int = 960) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._frames_per_buffer = frames_per_buffer
        self._stream = None
        self._pyaudio = None

    def open(self) -> None:
        import pyaudio

        self._pyaudio = pyaudio.PyAudio()
        self._stream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=self._channels,
            rate=self._sample_rate,
            output=True,
            frames_per_buffer=self._frames_per_buffer,
        )

    def write(self, pcm_bytes: bytes) -> None:
        if self._stream is None:
            return
        try:
            self._stream.write(pcm_bytes, exception_on_underflow=False)
        except OSError:
            pass

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except OSError:
                pass
            self._stream.close()
            self._stream = None
        if self._pyaudio is not None:
            self._pyaudio.terminate()
            self._pyaudio = None


class VoiceChannelManager:
    """Enforce concurrent-sender cap and route Opus-decoded audio to speakers."""

    def __init__(self, settings: Settings, *, _audio_player_factory: type[AudioPlayer] | None = None, _on_sender_lost: "Callable[[str], Awaitable[None]] | None" = None) -> None:
        self._max_senders = settings.voice_max_senders
        self._senders: dict[str, VoiceSender] = {}
        self._tracks: dict[str, AudioStreamTrack] = {}
        self._lock = asyncio.Lock()
        self._create_player: type[AudioPlayer] = _audio_player_factory or AudioPlayer
        self._on_sender_lost = _on_sender_lost

    @property
    def sender_count(self) -> int:
        return len(self._senders)

    def register_track(self, client_id: str, track: AudioStreamTrack) -> None:
        """Remember the AudioStreamTrack for a connected client."""
        self._tracks[client_id] = track

    async def handle_voice_start(self, client_id: str, _payload: VoiceStartPayload) -> VoiceGrantedPayload | VoiceDeniedPayload:
        """Grant or deny a voice-start request from a connected client."""
        async with self._lock:
            if client_id in self._senders:
                await self._release_sender(client_id)
            if len(self._senders) >= self._max_senders:
                return VoiceDeniedPayload(
                    reason="voice_senders_full",
                    current_senders=len(self._senders),
                    max_senders=self._max_senders,
                )
            track = self._tracks.get(client_id)
            if track is None:
                return VoiceDeniedPayload(
                    reason="no_audio_track",
                    current_senders=len(self._senders),
                    max_senders=self._max_senders,
                )
            sender = VoiceSender(client_id=client_id, voice_id=VoiceGrantedPayload().voice_id)
            try:
                player = self._create_player()
                player.open()
            except Exception:
                logger.warning("voice_audio_device_failed", extra={"client_id": client_id})
                return VoiceDeniedPayload(
                    reason="audio_device_error",
                    current_senders=len(self._senders),
                    max_senders=self._max_senders,
                )
            sender._audio_player = player
            sender._consumer_task = asyncio.create_task(
                self._consume_audio(track, player, sender),
                name=f"voice-consume-{client_id}",
            )
            self._senders[client_id] = sender
            logger.info("voice_sender_granted", extra={"client_id": client_id, "voice_id": sender.voice_id, "sender_count": len(self._senders)})
            return VoiceGrantedPayload(voice_id=sender.voice_id)

    async def handle_voice_stop(self, client_id: str) -> None:
        """Release a voice sender's resources."""
        async with self._lock:
            await self._release_sender(client_id)

    async def close_sender(self, client_id: str) -> None:
        """Clean up a disconnected client's voice resources."""
        async with self._lock:
            self._tracks.pop(client_id, None)
            await self._release_sender(client_id)

    async def close(self) -> None:
        """Release all voice senders during application shutdown."""
        async with self._lock:
            for client_id in list(self._senders):
                await self._release_sender(client_id)
            self._tracks.clear()

    async def _release_sender(self, client_id: str) -> None:
        sender = self._senders.pop(client_id, None)
        if sender is None:
            return
        if sender._consumer_task is not None and sender._consumer_task is not asyncio.current_task():
            sender._consumer_task.cancel()
        player = sender._audio_player
        if player is not None:
            sender._audio_player = None
            loop = asyncio.get_running_loop()
            loop.call_soon(player.close)
        logger.info("voice_sender_released", extra={"client_id": client_id, "voice_id": sender.voice_id, "sender_count": len(self._senders)})

    async def _consume_audio(self, track: AudioStreamTrack, player: AudioPlayer, sender: VoiceSender) -> None:
        """Read Opus-decoded PCM frames and write them to the speaker. Runs until cancelled."""
        try:
            while True:
                frame = await track.recv()
                pcm = frame.to_ndarray().tobytes()
                player.write(pcm)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("voice_consume_error", extra={"client_id": sender.client_id}, exc_info=True)
            async with self._lock:
                self._senders.pop(sender.client_id, None)
            if self._on_sender_lost is not None:
                try:
                    await self._on_sender_lost(sender.client_id)
                except Exception:
                    logger.warning("voice_sender_lost_callback_error", extra={"client_id": sender.client_id}, exc_info=True)
        finally:
            player.close()
