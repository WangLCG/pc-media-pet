"""In-memory frame-difference motion detection."""

import asyncio
import logging
import time
from collections import deque

import cv2
import numpy as np

from .camera import CameraManager
from .config import Settings
from .event_bus import EventBus
from .models import MotionDetectedEvent

logger = logging.getLogger(__name__)
BoundingBox = tuple[int, int, int, int]


class MotionDetector:
    """Confirm meaningful frame changes and publish events without retaining images."""

    def __init__(self, camera: CameraManager, event_bus: EventBus, settings: Settings) -> None:
        self._camera = camera
        self._event_bus = event_bus
        self._settings = settings
        self._previous_frame: np.ndarray | None = None
        self._motion_hits: deque[tuple[float, BoundingBox]] = deque()
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
        self._reset_hits()

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
            self._reset_hits()
            return None
        previous = self._previous_frame
        changed_area, region = self._motion_geometry(previous, current)
        brightness_delta = float(
            np.mean(np.abs(previous.astype(np.int16) - current.astype(np.int16)))
        )
        self._previous_frame = current
        if changed_area < self._settings.motion_min_changed_area:
            self._reset_hits()
            return None
        if (
            changed_area / current.size >= self._settings.motion_global_change_ratio
            and brightness_delta >= self._settings.motion_global_brightness_delta
        ):
            # A broad brightness jump is generally auto exposure or a light
            # switching on/off, not a local moving subject. Current is kept as
            # the next baseline so the reverse jump is suppressed as well.
            self._reset_hits()
            return None
        # Do not allow activity during cooldown to pre-fill the next
        # confirmation window. A new notification must always earn its own
        # full sequence of confirmed detections.
        if self._in_cooldown(now):
            self._reset_hits()
            return None
        timestamp = time.monotonic() if now is None else now
        if self._record_matching_hit(timestamp, region) < self._settings.motion_confirm_frames:
            return None
        event = MotionDetectedEvent(
            confidence=min(1.0, changed_area / current.size),
            changed_area=changed_area,
        )
        self._last_motion_at = timestamp
        self._reset_hits()
        await self._event_bus.publish_motion(event)
        return event

    @staticmethod
    def _prepare(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(frame, (5, 5), 0)

    @staticmethod
    def _motion_geometry(previous: np.ndarray, current: np.ndarray) -> tuple[int, BoundingBox]:
        delta = cv2.absdiff(previous, current)
        _, thresholded = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)
        dilated = cv2.dilate(thresholded, None, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 0, (0, 0, 0, 0)
        changed_area = int(sum(cv2.contourArea(contour) for contour in contours))
        x, y, width, height = cv2.boundingRect(np.vstack(contours))
        return changed_area, (x, y, width, height)

    def _record_matching_hit(self, timestamp: float, region: BoundingBox) -> int:
        window_start = timestamp - self._settings.motion_confirm_window_seconds
        while self._motion_hits and self._motion_hits[0][0] < window_start:
            self._motion_hits.popleft()
        if self._motion_hits and self._region_iou(self._motion_hits[-1][1], region) < self._settings.motion_min_region_iou:
            self._reset_hits()
        self._motion_hits.append((timestamp, region))
        return len(self._motion_hits)

    def _reset_hits(self) -> None:
        self._motion_hits.clear()

    @staticmethod
    def _region_iou(first: BoundingBox, second: BoundingBox) -> float:
        first_x, first_y, first_width, first_height = first
        second_x, second_y, second_width, second_height = second
        left, top = max(first_x, second_x), max(first_y, second_y)
        right = min(first_x + first_width, second_x + second_width)
        bottom = min(first_y + first_height, second_y + second_height)
        intersection = max(0, right - left) * max(0, bottom - top)
        union = first_width * first_height + second_width * second_height - intersection
        return intersection / union if union else 0.0

    def _in_cooldown(self, now: float | None = None) -> bool:
        if self._last_motion_at is None:
            return False
        timestamp = time.monotonic() if now is None else now
        return timestamp - self._last_motion_at < self._settings.motion_cooldown_seconds
