import asyncio

import numpy as np
import pytest

from app.config import Settings
from app.event_bus import EventBus
from app.motion import MotionDetector


def settings(**overrides) -> Settings:
    values = {
        "app_token": "test-token-that-is-long-enough",
        "motion_min_changed_area": 100,
        "motion_confirm_frames": 2,
        "motion_cooldown_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


class UnusedCamera:
    async def wait_for_frame(self, after_sequence: int):  # pragma: no cover - detector task is not started here
        raise AssertionError("synthetic tests call process_frame directly")


class QueueCamera:
    def __init__(self):
        self.frames: asyncio.Queue[tuple[int, np.ndarray]] = asyncio.Queue()

    async def wait_for_frame(self, after_sequence: int):
        sequence, frame = await self.frames.get()
        assert sequence > after_sequence
        return sequence, frame


@pytest.mark.asyncio
async def test_detector_confirms_sustained_motion_then_observes_cooldown():
    event_bus = EventBus()
    received = []

    async def receive(event):
        received.append(event)

    event_bus.subscribe_motion(receive)
    detector = MotionDetector(UnusedCamera(), event_bus, settings())
    still = np.zeros((120, 160, 3), dtype=np.uint8)
    moving = still.copy()
    moving[30:100, 40:120] = 255

    assert await detector.process_frame(still, now=0) is None
    assert await detector.process_frame(moving, now=1) is None
    event = await detector.process_frame(still, now=2)

    assert event is not None
    assert event.changed_area >= 100
    assert received == [event]

    assert await detector.process_frame(moving, now=3) is None
    assert await detector.process_frame(still, now=4) is None
    assert received == [event]


@pytest.mark.asyncio
async def test_detector_ignores_small_changes():
    event_bus = EventBus()
    received = []

    async def receive(event):
        received.append(event)

    event_bus.subscribe_motion(receive)
    detector = MotionDetector(UnusedCamera(), event_bus, settings(motion_min_changed_area=500))
    still = np.zeros((120, 160, 3), dtype=np.uint8)
    tiny_change = still.copy()
    tiny_change[50:54, 50:54] = 255

    await detector.process_frame(still, now=0)
    await detector.process_frame(tiny_change, now=1)
    await detector.process_frame(still, now=2)

    assert received == []


@pytest.mark.asyncio
async def test_detector_resets_its_baseline_when_camera_resolution_changes():
    detector = MotionDetector(UnusedCamera(), EventBus(), settings())

    assert await detector.process_frame(np.zeros((360, 640, 3), dtype=np.uint8), now=0) is None
    assert await detector.process_frame(np.zeros((1080, 1920, 3), dtype=np.uint8), now=1) is None
    assert detector._previous_frame is not None
    assert detector._previous_frame.shape == (1080, 1920)


@pytest.mark.asyncio
async def test_background_detector_consumes_camera_frames_and_stops_cleanly():
    event_bus = EventBus()
    received = []

    async def receive(event):
        received.append(event)

    camera = QueueCamera()
    event_bus.subscribe_motion(receive)
    detector = MotionDetector(camera, event_bus, settings(motion_confirm_frames=1, motion_cooldown_seconds=0))
    still = np.zeros((100, 100, 3), dtype=np.uint8)
    changed = still.copy()
    changed[20:80, 20:80] = 255

    await detector.start()
    try:
        await camera.frames.put((1, still))
        await camera.frames.put((2, changed))
        for _ in range(40):
            if received:
                break
            await asyncio.sleep(0.005)
        assert len(received) == 1
    finally:
        await detector.stop()

    assert detector._task is None


@pytest.mark.asyncio
async def test_background_detector_continues_after_event_delivery_failure():
    event_bus = EventBus()
    attempts = 0
    received = []

    async def flaky_receive(event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient notify failure")
        received.append(event)

    camera = QueueCamera()
    event_bus.subscribe_motion(flaky_receive)
    detector = MotionDetector(camera, event_bus, settings(motion_confirm_frames=1, motion_cooldown_seconds=0))
    still = np.zeros((100, 100, 3), dtype=np.uint8)
    changed = still.copy()
    changed[20:80, 20:80] = 255

    await detector.start()
    try:
        await camera.frames.put((1, still))
        await camera.frames.put((2, changed))
        await camera.frames.put((3, still))
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.005)
        assert attempts == 2
        assert len(received) == 1
        assert detector._task is not None and not detector._task.done()
    finally:
        await detector.stop()
