"""Validated signaling and notify-channel protocol models."""

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

CLIENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
MESSAGE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class NotifyOffer(BaseModel):
    """The browser's WebRTC offer for its long-lived notify connection."""

    client_id: str = Field(min_length=1, max_length=64, pattern=CLIENT_ID_PATTERN)
    sdp: str = Field(min_length=1)
    type: Literal["offer"]


class NotifyAnswer(BaseModel):
    """The server's WebRTC answer for a notify connection."""

    sdp: str = Field(min_length=1)
    type: Literal["answer"]


class MediaOffer(NotifyOffer):
    """The browser's on-demand media WebRTC offer."""

    video: bool = True
    audio: bool = False


class MediaAnswer(NotifyAnswer):
    """The SDP answer and server-owned identifier for a media session."""

    session_id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)


class MediaStopRequest(BaseModel):
    """A request to close a media session owned by the caller's browser ID."""

    client_id: str = Field(min_length=1, max_length=64, pattern=CLIENT_ID_PATTERN)
    session_id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)


class NotifyEnvelope(BaseModel):
    """Versioned JSON message sent through the notify DataChannel."""

    version: Literal[1]
    type: Literal["hello", "ping", "pong", "motion_detected", "camera_error", "media_state", "ack"]
    id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
    ts: int = Field(ge=0)
    payload: dict[str, Any]


class PongPayload(BaseModel):
    """Payload carried by a `pong` response."""

    ping_id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)


class AckPayload(BaseModel):
    """Payload returned by a client after receiving a reliable event."""

    message_id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
    status: Literal["received"]


class MotionDetectedEvent(BaseModel):
    """Typed, in-memory event emitted by the motion detector in a later phase."""

    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}", min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
    ts: int = Field(default_factory=lambda: int(time.time()), ge=0)
    zone: str = Field(default="default", min_length=1, max_length=64)
    confidence: float = Field(ge=0, le=1)
    changed_area: int = Field(ge=0)


class CameraErrorEvent(BaseModel):
    """Non-sensitive, in-memory camera failure notification."""

    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}", min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
    ts: int = Field(default_factory=lambda: int(time.time()), ge=0)
    code: Literal["camera_unavailable", "camera_read_failed"]
    message: str = Field(min_length=1, max_length=256)


class MediaStateEvent(BaseModel):
    """In-memory lifecycle event for an on-demand media session."""

    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}", min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
    ts: int = Field(default_factory=lambda: int(time.time()), ge=0)
    state: Literal["started", "stopped"]
    session_id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
