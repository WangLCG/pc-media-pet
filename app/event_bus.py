"""Small in-process event bus for non-persistent monitoring events."""

from collections.abc import Awaitable, Callable

from .models import CameraErrorEvent, MotionDetectedEvent, SoundDetectedEvent

MotionEventHandler = Callable[[MotionDetectedEvent], Awaitable[None]]
CameraErrorHandler = Callable[[CameraErrorEvent], Awaitable[None]]
SoundEventHandler = Callable[[SoundDetectedEvent], Awaitable[None]]


class EventBus:
    """Publish typed events to local subscribers without retaining history."""

    def __init__(self) -> None:
        self._motion_handlers: list[MotionEventHandler] = []
        self._camera_error_handlers: list[CameraErrorHandler] = []
        self._sound_handlers: list[SoundEventHandler] = []

    def subscribe_motion(self, handler: MotionEventHandler) -> None:
        """Register a handler for future motion events."""
        self._motion_handlers.append(handler)

    async def publish_motion(self, event: MotionDetectedEvent) -> None:
        """Deliver an event to current subscribers; events are never persisted."""
        for handler in tuple(self._motion_handlers):
            await handler(event)

    def subscribe_sound(self, handler: SoundEventHandler) -> None:
        """Register a handler for future non-persistent loud-sound events."""
        self._sound_handlers.append(handler)

    async def publish_sound(self, event: SoundDetectedEvent) -> None:
        """Deliver a sound event without retaining audio samples."""
        for handler in tuple(self._sound_handlers):
            await handler(event)

    def subscribe_camera_error(self, handler: CameraErrorHandler) -> None:
        """Register a handler for camera availability failures."""
        self._camera_error_handlers.append(handler)

    async def publish_camera_error(self, event: CameraErrorEvent) -> None:
        """Deliver a camera failure without retaining sensitive capture data."""
        for handler in tuple(self._camera_error_handlers):
            await handler(event)
