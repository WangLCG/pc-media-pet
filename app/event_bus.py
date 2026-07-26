"""Small in-process event bus for non-persistent monitoring events."""

from collections.abc import Awaitable, Callable

from .models import MotionDetectedEvent

MotionEventHandler = Callable[[MotionDetectedEvent], Awaitable[None]]


class EventBus:
    """Publish typed events to local subscribers without retaining history."""

    def __init__(self) -> None:
        self._motion_handlers: list[MotionEventHandler] = []

    def subscribe_motion(self, handler: MotionEventHandler) -> None:
        """Register a handler for future motion events."""
        self._motion_handlers.append(handler)

    async def publish_motion(self, event: MotionDetectedEvent) -> None:
        """Deliver an event to current subscribers; events are never persisted."""
        for handler in tuple(self._motion_handlers):
            await handler(event)
