"""Camera manager tests using an in-memory replacement for OpenCV hardware."""

import asyncio
import threading
from collections import deque

import numpy as np
import pytest
import cv2

from app.camera import CameraManager
from app.config import Settings


def settings(**overrides) -> Settings:
    values = {
        "app_token": "test-token-that-is-long-enough",
        "camera_idle_interval_seconds": 0.001,
        "camera_backend": "auto",
        "camera_idle_width": 320,
        "camera_idle_height": 180,
        "camera_view_width": 1280,
        "camera_view_height": 720,
        "camera_view_fps": 30,
    }
    values.update(overrides)
    return Settings(**values)


class FakeCapture:
    def __init__(self, frames=(), opened=True):
        self.frames = deque(frames)
        self.opened = opened
        self.released = False
        self.properties = []

    def isOpened(self):
        return self.opened

    def set(self, property_id, value):
        self.properties.append((property_id, value))
        return True

    def get(self, property_id):
        for set_property, value in reversed(self.properties):
            if set_property == property_id:
                return value
        return 0

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.popleft()

    def release(self):
        self.released = True
        self.opened = False


class BlockingCapture(FakeCapture):
    def __init__(self):
        super().__init__()
        self.read_started = threading.Event()
        self.allow_read_to_finish = threading.Event()

    def read(self):
        self.read_started.set()
        self.allow_read_to_finish.wait()
        return False, None


class ThreadRecordingCapture(FakeCapture):
    def __init__(self, frames):
        super().__init__(frames)
        self.operation_threads = set()

    def set(self, property_id, value):
        self.operation_threads.add(threading.get_ident())
        return super().set(property_id, value)

    def read(self):
        self.operation_threads.add(threading.get_ident())
        return super().read()

    def release(self):
        self.operation_threads.add(threading.get_ident())
        return super().release()


class BlockingReleaseCapture(FakeCapture):
    def __init__(self, frames):
        super().__init__(frames)
        self.release_started = threading.Event()
        self.allow_release = threading.Event()

    def release(self):
        self.release_started.set()
        self.allow_release.wait()
        return super().release()


@pytest.mark.asyncio
async def test_idle_capture_shares_a_copy_and_releases_the_only_camera(monkeypatch):
    frame = np.full((20, 30, 3), 7, dtype=np.uint8)
    capture = FakeCapture([frame] * 1000)
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)
    errors = []

    async def publish_error(event):
        errors.append(event)

    manager = CameraManager(settings(), publish_error)
    await manager.start()
    sequence, shared = await asyncio.wait_for(manager.wait_for_frame(0), timeout=0.2)
    try:
        assert sequence == 1
        assert manager.is_available is True
        assert errors == []
        shared[0, 0] = 99
        assert (await manager.get_latest_frame())[0, 0, 0] == 7
        assert (await manager.read_frame_for_media())[0, 0, 0] == 7
    finally:
        await manager.stop()

    assert capture.released is True


@pytest.mark.asyncio
async def test_set_mode_reconfigures_the_single_capture(monkeypatch):
    capture = FakeCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)

    async def publish_error(event):
        raise AssertionError(f"unexpected camera error: {event}")

    manager = CameraManager(settings(), publish_error)
    await manager._capture_once()
    await manager.set_mode("view")
    await manager.stop()

    values = [value for _, value in capture.properties]
    assert 320 in values and 180 in values
    assert 1280 in values and 720 in values and 30 in values


@pytest.mark.asyncio
async def test_camera_reports_hd_to_4k_modes_that_the_device_accepts(monkeypatch):
    capture = FakeCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)
    manager = CameraManager(settings(), lambda event: asyncio.sleep(0))

    capabilities = await manager.get_view_capabilities()
    await manager.stop()

    assert capabilities[-1] == {"width": 3840, "height": 2160, "label": "4K"}


@pytest.mark.asyncio
async def test_selected_view_resolution_is_applied_to_the_camera(monkeypatch):
    capture = FakeCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)
    manager = CameraManager(settings(), lambda event: asyncio.sleep(0))
    await manager._capture_once()
    await manager.set_view_resolution(3840, 2160)
    await manager.set_mode("view")
    await manager.stop()

    assert (cv2.CAP_PROP_FRAME_WIDTH, 3840) in capture.properties
    assert (cv2.CAP_PROP_FRAME_HEIGHT, 2160) in capture.properties


@pytest.mark.asyncio
async def test_open_capture_uses_configured_windows_backend(monkeypatch):
    capture = FakeCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    calls = []

    def create_capture(*args):
        calls.append(args)
        return capture

    monkeypatch.setattr("app.camera.cv2.VideoCapture", create_capture)

    async def publish_error(event):
        raise AssertionError(f"unexpected camera error: {event}")

    manager = CameraManager(settings(camera_index=0, camera_backend="dshow"), publish_error)
    await manager._capture_once()
    await manager.stop()

    assert calls == [(0, cv2.CAP_DSHOW)]


@pytest.mark.asyncio
async def test_all_opencv_operations_use_one_dedicated_worker_thread(monkeypatch):
    capture = ThreadRecordingCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)

    async def publish_error(event):
        raise AssertionError(f"unexpected camera error: {event}")

    manager = CameraManager(settings(), publish_error)
    await manager._capture_once()
    await manager.set_mode("view")
    await manager.stop()

    assert len(capture.operation_threads) == 1


@pytest.mark.asyncio
async def test_open_failure_emits_one_non_sensitive_error_until_the_camera_recovers(monkeypatch):
    capture = FakeCapture(opened=False)
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)
    errors = []

    async def publish_error(event):
        errors.append(event)

    manager = CameraManager(settings(), publish_error)
    await manager._capture_once()
    await manager._capture_once()

    assert [event.code for event in errors] == ["camera_unavailable"]
    assert capture.released is True
    await manager.stop()


@pytest.mark.asyncio
async def test_read_failure_emits_camera_error_without_crashing_manager(monkeypatch):
    capture = FakeCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)
    errors = []

    async def publish_error(event):
        errors.append(event)

    manager = CameraManager(settings(), publish_error)
    await manager._capture_once()
    assert await manager.get_latest_frame() is not None
    await manager._capture_once()

    assert [event.code for event in errors] == ["camera_read_failed"]
    assert await manager.get_latest_frame() is None
    assert capture.released is True
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_read_before_releasing_camera(monkeypatch):
    capture = BlockingCapture()
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)

    async def publish_error(event):
        pass

    manager = CameraManager(settings(), publish_error)
    await manager.start()
    for _ in range(20):
        if capture.read_started.is_set():
            break
        await asyncio.sleep(0.005)
    assert capture.read_started.is_set()

    stopping = asyncio.create_task(manager.stop())
    try:
        await asyncio.sleep(0.01)
        assert capture.released is False

        capture.allow_read_to_finish.set()
        await asyncio.wait_for(stopping, timeout=0.2)
        assert capture.released is True
    finally:
        capture.allow_read_to_finish.set()
        if not stopping.done():
            await asyncio.wait_for(stopping, timeout=0.2)


@pytest.mark.asyncio
async def test_stop_times_out_without_releasing_a_permanently_blocked_read(monkeypatch):
    capture = BlockingCapture()
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)

    async def publish_error(event):
        pass

    manager = CameraManager(settings(camera_shutdown_timeout_seconds=0.01), publish_error)
    await manager.start()
    for _ in range(20):
        if capture.read_started.is_set():
            break
        await asyncio.sleep(0.005)
    assert capture.read_started.is_set()

    try:
        await asyncio.wait_for(manager.stop(), timeout=0.2)
        assert capture.released is False
    finally:
        capture.allow_read_to_finish.set()
        for _ in range(20):
            if capture.released:
                break
            await asyncio.sleep(0.005)

    assert capture.released is True


@pytest.mark.asyncio
async def test_read_failure_reopens_camera_on_next_capture(monkeypatch):
    failed_capture = FakeCapture()
    recovered_capture = FakeCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    captures = deque([failed_capture, recovered_capture])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: captures.popleft())

    async def publish_error(event):
        pass

    manager = CameraManager(settings(), publish_error)
    await manager._capture_once()
    await manager._capture_once()

    assert failed_capture.released is True
    assert await manager.get_latest_frame() is not None
    await manager.stop()


@pytest.mark.asyncio
async def test_capture_loop_uses_configured_retry_interval_after_failure(monkeypatch):
    manager = CameraManager(settings(camera_retry_interval_seconds=0.01), lambda event: asyncio.sleep(0))
    delays = []

    async def capture_once():
        delays.append("capture")
        return False

    async def fake_sleep(delay):
        delays.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(manager, "_capture_once", capture_once)
    monkeypatch.setattr("app.camera.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await manager._capture_loop()

    assert delays == ["capture", 0.01]
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_times_out_while_camera_open_is_blocked(monkeypatch):
    open_started = threading.Event()
    allow_open = threading.Event()
    capture = FakeCapture()

    def blocking_video_capture(index):
        open_started.set()
        allow_open.wait()
        return capture

    monkeypatch.setattr("app.camera.cv2.VideoCapture", blocking_video_capture)
    manager = CameraManager(settings(camera_shutdown_timeout_seconds=0.01), lambda event: asyncio.sleep(0))
    await manager.start()
    for _ in range(20):
        if open_started.is_set():
            break
        await asyncio.sleep(0.005)
    assert open_started.is_set()
    try:
        await asyncio.wait_for(manager.stop(), timeout=0.2)
    finally:
        allow_open.set()
    for _ in range(20):
        if capture.released:
            break
        await asyncio.sleep(0.005)
    assert capture.released is True


@pytest.mark.asyncio
async def test_stop_times_out_while_camera_release_is_blocked(monkeypatch):
    capture = BlockingReleaseCapture([np.zeros((10, 10, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.camera.cv2.VideoCapture", lambda index: capture)
    manager = CameraManager(settings(camera_shutdown_timeout_seconds=0.01), lambda event: asyncio.sleep(0))
    await manager._capture_once()
    try:
        await asyncio.wait_for(manager.stop(), timeout=0.2)
        assert capture.release_started.is_set()
        assert capture.released is False
    finally:
        capture.allow_release.set()
    for _ in range(20):
        if capture.released:
            break
        await asyncio.sleep(0.005)
    assert capture.released is True
