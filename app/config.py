"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the local media-pet service."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_token: str = Field(min_length=16)

    camera_index: int = Field(default=0, ge=0)
    camera_backend: Literal["auto", "dshow", "msmf"] = "auto"
    camera_idle_width: int = Field(default=640, ge=1)
    camera_idle_height: int = Field(default=360, ge=1)
    camera_idle_interval_seconds: float = Field(default=2.0, gt=0)
    camera_view_width: int = Field(default=1280, ge=1)
    camera_view_height: int = Field(default=720, ge=1)
    camera_view_fps: int = Field(default=15, ge=1)
    camera_shutdown_timeout_seconds: float = Field(default=5.0, gt=0)
    camera_retry_interval_seconds: float = Field(default=30.0, gt=0)

    # Audio capture is deliberately separate from the WebRTC viewing session:
    # monitoring stays active even when no browser is streaming video.
    audio_enabled: bool = False
    audio_device: str = Field(default="", max_length=512)
    audio_vad_mode: int = Field(default=3, ge=0, le=3)
    audio_loudness_threshold_dbfs: float = Field(default=-35.0, ge=-100, le=0)
    audio_loudness_override_dbfs: float = Field(default=-22.0, ge=-100, le=0)
    # Audio frames are 20 ms each. Ten positive frames require roughly 200 ms
    # of sustained sound before a notification can be emitted.
    audio_confirm_frames: int = Field(default=10, ge=1)
    audio_cooldown_seconds: int = Field(default=60, ge=0)
    audio_retry_interval_seconds: float = Field(default=30.0, gt=0)
    audio_shutdown_timeout_seconds: float = Field(default=5.0, gt=0)

    motion_min_changed_area: int = Field(default=1800, ge=1)
    # Require several successive frame changes so a brief exposure shift or
    # a single dropped frame does not generate a notification.
    motion_confirm_frames: int = Field(default=3, ge=1)
    motion_cooldown_seconds: int = Field(default=300, ge=0)

    notify_ping_interval_seconds: int = Field(default=20, ge=1)
    notify_missed_pong_limit: int = Field(default=3, ge=1)
    notify_channel_open_timeout_seconds: float = Field(default=15.0, gt=0)
    notify_ack_timeout_seconds: float = Field(default=5.0, gt=0)
    notify_max_retries: int = Field(default=3, ge=0)
    media_idle_timeout_seconds: int = Field(default=120, ge=1)
    webrtc_ipv6_enabled: bool = False

    @field_validator("app_token")
    @classmethod
    def validate_app_token(cls, value: str) -> str:
        """Reject empty and placeholder credentials before starting the service."""
        normalized = value.strip()
        placeholders = {"change-me-long-random-token", "changeme", "example", "password"}
        if not normalized or normalized.lower() in placeholders:
            raise ValueError("APP_TOKEN must be a unique, non-example secret with at least 16 characters")
        return normalized


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""
    return Settings()
