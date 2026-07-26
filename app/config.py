"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the local media-pet service."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_token: str = Field(min_length=16)

    camera_index: int = Field(default=0, ge=0)
    camera_idle_width: int = Field(default=640, ge=1)
    camera_idle_height: int = Field(default=360, ge=1)
    camera_idle_interval_seconds: float = Field(default=2.0, gt=0)
    camera_view_width: int = Field(default=1280, ge=1)
    camera_view_height: int = Field(default=720, ge=1)
    camera_view_fps: int = Field(default=15, ge=1)

    motion_min_changed_area: int = Field(default=1800, ge=1)
    motion_confirm_frames: int = Field(default=2, ge=1)
    motion_cooldown_seconds: int = Field(default=300, ge=0)

    notify_ping_interval_seconds: int = Field(default=20, ge=1)
    notify_missed_pong_limit: int = Field(default=3, ge=1)
    notify_channel_open_timeout_seconds: float = Field(default=15.0, gt=0)
    notify_ack_timeout_seconds: float = Field(default=5.0, gt=0)
    notify_max_retries: int = Field(default=3, ge=0)
    media_idle_timeout_seconds: int = Field(default=120, ge=1)

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
