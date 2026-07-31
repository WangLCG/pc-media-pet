"""In-memory DirectShow audio monitoring using WebRTC VAD and loudness gating."""

import asyncio
import logging
import math
import threading
import time

import av
import numpy as np
import webrtcvad

from .config import Settings
from .event_bus import EventBus
from .models import SoundDetectedEvent

logger = logging.getLogger(__name__)
_SAMPLE_RATE = 16_000
_FRAME_MS = 20
_FRAME_BYTES = _SAMPLE_RATE * _FRAME_MS // 1000 * 2  # s16le mono


class SoundDetector:
    """Detect sustained loud audio without storing, streaming, or logging samples."""

    def __init__(self, settings: Settings, event_bus: EventBus) -> None:
        self._settings = settings
        self._event_bus = event_bus
        self._vad = webrtcvad.Vad(settings.audio_vad_mode)
        self._stop_event = threading.Event()
        self._capture_lock = threading.Lock()
        self._container: av.container.InputContainer | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer = bytearray()
        self._consecutive_detections = 0
        self._last_sound_at: float | None = None
        self._available = False

    @property
    def state(self) -> str:
        if not self._settings.audio_enabled:
            return "disabled"
        if not self._available:
            return "unavailable"
        return "cooldown" if self._in_cooldown() else "quiet"

    async def start(self) -> None:
        if not self._settings.audio_enabled or self._thread is not None:
            return
        if not self._settings.audio_device:
            logger.warning("audio_disabled_missing_device")
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, name="audio-capture", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        with self._capture_lock:
            container, self._container = self._container, None
        if container is not None:
            try:
                container.close()
            except Exception:
                logger.debug("audio_capture_close_failed", exc_info=True)
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join, self._settings.audio_shutdown_timeout_seconds)
            if thread.is_alive():
                logger.warning("audio_capture_shutdown_timeout")
        self._available = False
        self._buffer.clear()
        self._consecutive_detections = 0

    def process_pcm_frame(self, pcm_s16le: bytes, now: float | None = None) -> SoundDetectedEvent | None:
        """Evaluate exactly one 20 ms, 16 kHz mono PCM frame; exposed for tests."""
        if len(pcm_s16le) != _FRAME_BYTES:
            raise ValueError("Expected a 20 ms 16 kHz mono s16le PCM frame")
        pcm = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float64)
        rms = float(np.sqrt(np.mean(np.square(pcm))))
        rms_dbfs = max(-100.0, 20 * math.log10(max(rms, 1.0) / 32_768.0))
        vad_speech = self._vad.is_speech(pcm_s16le, _SAMPLE_RATE)
        loud = rms_dbfs >= self._settings.audio_loudness_threshold_dbfs
        # VAD supplies WebRTC's noise/silence gate. The high-loudness override
        # deliberately retains non-speech sounds, including potential barks.
        active = loud and (vad_speech or rms_dbfs >= self._settings.audio_loudness_override_dbfs)
        if not active:
            self._consecutive_detections = 0
            return None
        # Ignore and clear hits while cooling down so sound that spans the
        # cooldown boundary cannot immediately create another notification.
        if self._in_cooldown(now):
            self._consecutive_detections = 0
            return None
        self._consecutive_detections += 1
        if self._consecutive_detections < self._settings.audio_confirm_frames:
            return None
        self._consecutive_detections = 0
        occurred_at = time.monotonic() if now is None else now
        self._last_sound_at = occurred_at
        threshold = self._settings.audio_loudness_threshold_dbfs
        confidence = min(1.0, max(0.0, (rms_dbfs - threshold) / -threshold))
        return SoundDetectedEvent(rms_dbfs=rms_dbfs, vad_speech=vad_speech, confidence=confidence)

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            container: av.container.InputContainer | None = None
            try:
                container = av.open(f"audio={self._settings.audio_device}", format="dshow", mode="r")
                with self._capture_lock:
                    if self._stop_event.is_set():
                        container.close()
                        return
                    self._container = container
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="s16", layout="mono", rate=_SAMPLE_RATE)
                self._available = True
                logger.info("audio_capture_started")
                for packet in container.demux(stream):
                    if self._stop_event.is_set():
                        return
                    for decoded in packet.decode():
                        for frame in resampler.resample(decoded):
                            samples = frame.samples * 2
                            self._consume_pcm(bytes(frame.planes[0])[:samples])
            except Exception:
                if not self._stop_event.is_set():
                    self._available = False
                    logger.exception("audio_capture_failed")
            finally:
                with self._capture_lock:
                    if self._container is container:
                        self._container = None
                if container is not None:
                    try:
                        container.close()
                    except Exception:
                        pass
            self._stop_event.wait(self._settings.audio_retry_interval_seconds)

    def _consume_pcm(self, samples: bytes) -> None:
        self._buffer.extend(samples)
        while len(self._buffer) >= _FRAME_BYTES:
            frame = bytes(self._buffer[:_FRAME_BYTES])
            del self._buffer[:_FRAME_BYTES]
            event = self.process_pcm_frame(frame)
            if event is not None:
                self._publish_from_capture_thread(event)

    def _publish_from_capture_thread(self, event: SoundDetectedEvent) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._create_publish_task, event)

    def _create_publish_task(self, event: SoundDetectedEvent) -> None:
        task = asyncio.create_task(self._event_bus.publish_sound(event), name="sound-event-publish")
        task.add_done_callback(self._log_publish_failure)

    @staticmethod
    def _log_publish_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.exception("sound_event_delivery_failed", exc_info=task.exception())

    def _in_cooldown(self, now: float | None = None) -> bool:
        if self._last_sound_at is None:
            return False
        current = time.monotonic() if now is None else now
        return current - self._last_sound_at < self._settings.audio_cooldown_seconds
