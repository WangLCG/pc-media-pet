"""Validated signaling and notify-channel protocol models."""

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


class NotifyEnvelope(BaseModel):
    """Versioned JSON message sent through the notify DataChannel."""

    version: Literal[1]
    type: Literal["hello", "ping", "pong"]
    id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
    ts: int = Field(ge=0)
    payload: dict[str, Any]


class PongPayload(BaseModel):
    """Payload carried by a `pong` response."""

    ping_id: str = Field(min_length=1, max_length=128, pattern=MESSAGE_ID_PATTERN)
