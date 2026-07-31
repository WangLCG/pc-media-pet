"""Single-owner OpenCV camera capture with in-memory frame sharing."""

import asyncio
import logging
import queue
import threading
from collections.abc import Awaitable, Callable
from typing import Literal

import cv2
import numpy as np

from .config import Settings
from .models import CameraErrorEvent

logger = logging.getLogger(__name__)

CameraMode = Literal["idle", "view"]
CameraErrorPublisher = Callable[[CameraErrorEvent], Awaitable[None]]
VIEW_RESOLUTIONS = ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160))


class _CameraWorker:
    """Run all OpenCV operations serially on one daemon thread."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Callable[[], object], asyncio.Future[object], asyncio.AbstractEventLoop] | None] = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="camera-worker", daemon=True)
        self._thread.start()

    def submit(self, operation: Callable[[], object]) -> asyncio.Future[object]:
        if self._closed:
            raise RuntimeError("Camera worker is closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._jobs.put((operation, future, loop))
        return future

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._jobs.put(None)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            operation, future, loop = job
            try:
                result = operation()
            except BaseException as error:
                self._complete(loop, future, error=error)
            else:
                self._complete(loop, future, result=result)

    @staticmethod
    def _complete(
        loop: asyncio.AbstractEventLoop,
        future: asyncio.Future[object],
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        def set_completion() -> None:
            if future.done():
                return
            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)

        try:
            loop.call_soon_threadsafe(set_completion)
        except RuntimeError:
            # A timed-out shutdown may have already closed the application loop.
            pass


class CameraManager:
    """Own the sole ``cv2.VideoCapture`` instance and retain only the latest frame."""

    def __init__(self, settings: Settings, publish_error: CameraErrorPublisher) -> None:
        self._settings = settings
        self._publish_error = publish_error
        self._mode: CameraMode = "idle"
        self._view_width = settings.camera_view_width
        self._view_height = settings.camera_view_height
        self._capture: cv2.VideoCapture | None = None
        self._latest_frame: np.ndarray | None = None
        self._frame_sequence = 0
        self._capture_task: asyncio.Task[None] | None = None
        self._mode_lock = asyncio.Lock()
        self._capture_lock = asyncio.Lock()
        self._frame_ready = asyncio.Condition()
        self._last_error_code: str | None = None
        self._worker = _CameraWorker()

    @property
    def mode(self) -> CameraMode:
        return self._mode

    @property
    def is_available(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    async def start(self) -> None:
        """Start low-frequency capture; failures are reported but never fatal to the API."""
        if self._capture_task is None:
            self._capture_task = asyncio.create_task(self._capture_loop(), name="camera-capture")

    async def stop(self) -> None:
        """Stop capture and release the physical device."""
        task, self._capture_task = self._capture_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._capture_lock:
            capture, self._capture = self._capture, None
            if capture is not None:
                await self._release_capture(capture)
        self._latest_frame = None
        self._worker.close()

    async def set_mode(self, mode: CameraMode) -> None:
        """Apply resolution/FPS atomically without allowing a second camera owner."""
        if mode not in {"idle", "view"}:
            raise ValueError(f"Unsupported camera mode: {mode}")
        async with self._mode_lock:
            if self._mode == mode:
                return
            self._mode = mode
            async with self._capture_lock:
                if self._capture is not None:
                    await self._run_worker(lambda: self._configure_capture(self._capture, mode))
        logger.info("camera_mode_changed", extra={"mode": mode})

    async def set_view_resolution(self, width: int, height: int) -> None:
        """Select a supported view size and immediately apply it while viewing."""
        if (width, height) not in VIEW_RESOLUTIONS:
            raise ValueError("Unsupported camera resolution")
        async with self._mode_lock:
            self._view_width, self._view_height = width, height
            async with self._capture_lock:
                if self._capture is not None and self._mode == "view":
                    await self._run_worker(lambda: self._configure_capture(self._capture, "view"))

    async def get_view_capabilities(self) -> list[dict[str, int | str]]:
        """Return standard HD/4K sizes the current camera reports it can accept."""
        async with self._capture_lock:
            if self._capture is None or not self._capture.isOpened():
                self._capture = await self._open_capture_without_shutdown_race()
            capture = self._capture
            if capture is None:
                return []
            return await self._run_worker(lambda: self._probe_view_resolutions(capture))  # type: ignore[return-value]

    async def get_latest_frame(self) -> np.ndarray | None:
        """Return a copy so consumers cannot mutate the shared in-memory frame."""
        return None if self._latest_frame is None else self._latest_frame.copy()

    async def read_frame_for_media(self) -> np.ndarray:
        """Return the latest captured frame for a future media track without reopening the camera."""
        frame = await self.get_latest_frame()
        if frame is None:
            raise RuntimeError("Camera frame is not available")
        return frame

    async def wait_for_frame(self, after_sequence: int) -> tuple[int, np.ndarray]:
        """Wait until a newer frame is captured; used by motion detection, not by OpenCV clients."""
        async with self._frame_ready:
            await self._frame_ready.wait_for(lambda: self._frame_sequence > after_sequence)
            assert self._latest_frame is not None
            return self._frame_sequence, self._latest_frame.copy()

    async def _capture_loop(self) -> None:
        while True:
            retry_needed = False
            try:
                retry_needed = not await self._capture_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "camera_capture_unexpected_error",
                    extra={
                        "camera_error_type": type(error).__name__,
                        "camera_error_message": str(error),
                    },
                )
                await self._report_error("camera_read_failed", "USB camera frame capture failed")
                retry_needed = True
            await asyncio.sleep(self._settings.camera_retry_interval_seconds if retry_needed else self._interval_seconds())

    def _interval_seconds(self) -> float:
        return self._settings.camera_idle_interval_seconds if self._mode == "idle" else 1 / self._settings.camera_view_fps

    async def _capture_once(self) -> bool:
        async with self._capture_lock:
            if self._capture is None or not self._capture.isOpened():
                self._capture = await self._open_capture_without_shutdown_race()
            capture = self._capture
            if capture is None:
                await self._report_error("camera_unavailable", "USB camera cannot be opened")
                return False
            ok, frame = await self._read_without_shutdown_race(capture)
        if not ok or frame is None:
            await self._discard_failed_capture(capture)
            await self._report_error("camera_read_failed", "USB camera frame capture failed")
            return False
        self._last_error_code = None
        async with self._frame_ready:
            self._latest_frame = frame
            self._frame_sequence += 1
            self._frame_ready.notify_all()
        return True

    async def _discard_failed_capture(self, capture: cv2.VideoCapture) -> None:
        """Release a capture that no longer returns frames so the next cycle can reopen it."""
        async with self._capture_lock:
            if self._capture is capture:
                self._capture = None
                await self._release_capture(capture)
        async with self._frame_ready:
            self._latest_frame = None

    def _open_capture(self) -> cv2.VideoCapture | None:
        backend = {
            "dshow": cv2.CAP_DSHOW,
            "msmf": cv2.CAP_MSMF,
        }.get(self._settings.camera_backend)
        if backend is None:
            capture = cv2.VideoCapture(self._settings.camera_index)
        else:
            capture = cv2.VideoCapture(self._settings.camera_index, backend)
        if not capture.isOpened():
            capture.release()
            return None
        self._configure_capture(capture, self._mode)
        return capture

    async def _open_capture_without_shutdown_race(self) -> cv2.VideoCapture | None:
        """Bound shutdown while ensuring an opened capture is released on the camera worker."""
        release_after_open = threading.Event()
        state_lock = threading.Lock()
        opened_capture: cv2.VideoCapture | None = None

        def open_capture() -> object:
            nonlocal opened_capture
            capture = self._open_capture()
            with state_lock:
                opened_capture = capture
                release_now = release_after_open.is_set()
            if release_now and capture is not None:
                capture.release()
            return capture

        operation = self._worker.submit(open_capture)
        try:
            return await asyncio.shield(operation)  # type: ignore[return-value]
        except asyncio.CancelledError:
            try:
                capture = await asyncio.wait_for(asyncio.shield(operation), timeout=self._settings.camera_shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                with state_lock:
                    release_after_open.set()
                    capture = opened_capture
                if capture is not None:
                    self._worker.submit(capture.release)
            else:
                if capture is not None:
                    await self._release_capture(capture)
            raise

    async def _read_without_shutdown_race(self, capture: cv2.VideoCapture) -> tuple[bool, np.ndarray | None]:
        """Wait briefly on shutdown, then let a daemon worker release a still-blocked capture."""
        operation = self._worker.submit(capture.read)
        try:
            return await asyncio.shield(operation)  # type: ignore[return-value]
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(asyncio.shield(operation), timeout=self._settings.camera_shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                # Release is queued behind the blocked read on the same worker, never concurrently.
                if self._capture is capture:
                    self._capture = None
                self._worker.submit(capture.release)
            except Exception:
                # Shutdown still takes precedence if the blocked operation finishes with an error.
                pass
            raise

    async def _run_worker(self, operation: Callable[[], object]) -> object:
        """Await one serial OpenCV operation on the camera's dedicated worker."""
        return await asyncio.shield(self._worker.submit(operation))

    async def _release_capture(self, capture: cv2.VideoCapture) -> None:
        """Do not let a driver-blocked release prevent service shutdown."""
        operation = self._worker.submit(capture.release)
        try:
            await asyncio.wait_for(asyncio.shield(operation), timeout=self._settings.camera_shutdown_timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("camera_release_timeout")

    def _configure_capture(self, capture: cv2.VideoCapture, mode: CameraMode) -> None:
        if mode == "idle":
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._settings.camera_idle_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._settings.camera_idle_height)
        else:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._view_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._view_height)
            capture.set(cv2.CAP_PROP_FPS, self._settings.camera_view_fps)

    def _probe_view_resolutions(self, capture: cv2.VideoCapture) -> list[dict[str, int | str]]:
        """Probe requested sizes through the active device, then restore its current mode."""
        supported: list[dict[str, int | str]] = []
        for width, height in VIEW_RESOLUTIONS:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            actual_width = round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            # Some drivers do not expose get() values. In that case retain the
            # selectable standard sizes; the device will negotiate its closest mode.
            if (actual_width, actual_height) in {(0, 0), (width, height)}:
                label = "4K" if (width, height) == (3840, 2160) else f"{height}p"
                supported.append({"width": width, "height": height, "label": label})
        self._configure_capture(capture, self._mode)
        return supported

    async def _report_error(self, code: Literal["camera_unavailable", "camera_read_failed"], message: str) -> None:
        if self._last_error_code == code:
            return
        self._last_error_code = code
        logger.warning("camera_error", extra={"code": code})
        await self._publish_error(CameraErrorEvent(code=code, message=message))
