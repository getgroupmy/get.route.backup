"""Runtime configuration. Every setting can be overridden with a GM_* env var."""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GM_", env_file=".env", extra="ignore")

    # --- required secrets -------------------------------------------------
    admin_token: str  # the owner's way in to the console; at least 24 characters
    secret_key: str  # base64 of 32 random bytes: encrypts the servers' keys, signs console sessions

    # --- paths ------------------------------------------------------------
    data_dir: Path = Path("/data")
    database_url: str = ""  # default: a SQLite file in data_dir

    # --- defaults the console can change (stored in the database) -----------
    total_timeout_ms: int = 8000  # the most one request may take, every server tried included
    default_rate_per_minute: int = 300  # a new API client's limit
    log_retention_days: int = 30  # the request log is kept this long
    health_every_seconds: int = 60  # how often each server's /health is checked

    public_url: str = ""  # e.g. https://gmap.getcdn.link, shown in the console

    @field_validator("admin_token")
    @classmethod
    def _admin_token_strong(cls, v: str) -> str:
        if len(v) < 24:
            raise ValueError("GM_ADMIN_TOKEN must be at least 24 characters")
        return v

    @field_validator("secret_key")
    @classmethod
    def _secret_key_valid(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("GM_SECRET_KEY must be base64") from exc
        if len(raw) != 32:
            raise ValueError("GM_SECRET_KEY must decode to exactly 32 bytes")
        return v

    @property
    def secret_key_bytes(self) -> bytes:
        return base64.b64decode(self.secret_key)

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'console.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
