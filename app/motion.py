"""In-memory frame-difference motion detection."""

import asyncio
import logging
import time

import cv2
import numpy as np

from .camera import CameraManager
from .config import Settings
from .event_bus import EventBus
from .models import MotionDetectedEvent

logger = logging.getLogger(__name__)


class MotionDetector:
    """Confirm meaningful frame changes and publish events without retaining images."""

    def __init__(self, camera: CameraManager, event_bus: EventBus, settings: Settings) -> None:
        self._camera = camera
        self._event_bus = event_bus
        self._settings = settings
        self._previous_frame: np.ndarray | None = None
        self._consecutive_detections = 0
        self._last_motion_at: float | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> str:
        return "cooldown" if self._in_cooldown() else "quiet"

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="motion-detector")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._previous_frame = None
        self._consecutive_detections = 0

    async def _run(self) -> None:
        sequence = 0
        while True:
            try:
                sequence, frame = await self._camera.wait_for_frame(sequence)
                await self.process_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A bad frame or transient notification failure must not silently stop monitoring.
                logger.exception("motion_detector_frame_failed")
                await asyncio.sleep(0.1)

    async def process_frame(self, frame: np.ndarray, now: float | None = None) -> MotionDetectedEvent | None:
        """Process one supplied frame; exposed for deterministic synthetic-frame tests."""
        current = self._prepare(frame)
        if self._previous_frame is None or self._previous_frame.shape != current.shape:
            # A camera mode switch can change resolution. Frames with different
            # dimensions cannot be compared, so establish a new baseline.
            self._previous_frame = current
            self._consecutive_detections = 0
            return None
        changed_area = self._changed_area(self._previous_frame, current)
        self._previous_frame = current
        if changed_area < self._settings.motion_min_changed_area:
            self._consecutive_detections = 0
            return None
        self._consecutive_detections += 1
        if self._consecutive_detections < self._settings.motion_confirm_frames or self._in_cooldown(now):
            return None
        event = MotionDetectedEvent(
            confidence=min(1.0, changed_area / current.size),
            changed_area=changed_area,
        )
        self._last_motion_at = time.monotonic() if now is None else now
        self._consecutive_detections = 0
        await self._event_bus.publish_motion(event)
        return event

    @staticmethod
    def _prepare(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(frame, (5, 5), 0)

    @staticmethod
    def _changed_area(previous: np.ndarray, current: np.ndarray) -> int:
        delta = cv2.absdiff(previous, current)
        _, thresholded = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)
        dilated = cv2.dilate(thresholded, None, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return int(sum(cv2.contourArea(contour) for contour in contours))

    def _in_cooldown(self, now: float | None = None) -> bool:
        if self._last_motion_at is None:
            return False
        timestamp = time.monotonic() if now is None else now
        return timestamp - self._last_motion_at < self._settings.motion_cooldown_seconds
