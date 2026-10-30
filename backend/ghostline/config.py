"""Every setting comes from the environment (backend/.env.example lists the keys). Nothing here holds a default that
would open the app: an empty owner id admits nobody, an empty worker token refuses every worker call."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    ghostline_env: str = Field(default="staging", alias="GHOSTLINE_ENV")
    database_url: str = Field(default="", alias="DATABASE_URL")
    db_pool_max: int = Field(default=3, alias="GHOSTLINE_DB_POOL_MAX")
    migrations_dir: str = Field(default=str(Path(__file__).resolve().parents[1] / "migrations"),
                                alias="GHOSTLINE_MIGRATIONS")
    session_secret: str = Field(default="", alias="GHOSTLINE_SESSION_SECRET")
    session_days: int = Field(default=30, alias="GHOSTLINE_SESSION_DAYS")
    owner_apple_id: str = Field(default="", alias="GHOSTLINE_OWNER_APPLE_ID")
    apple_audience: str = Field(default="com.wavetech.ghostline", alias="GHOSTLINE_APPLE_AUDIENCE")
    worker_token: str = Field(default="", alias="GHOSTLINE_WORKER_TOKEN")
    test_signer_key: str = Field(default="", alias="GHOSTLINE_TEST_SIGNER_KEY")
    api_internal_url: str = Field(default="", alias="GHOSTLINE_API_INTERNAL_URL")

    minio_endpoint: str = Field(default="", alias="MINIO_ENDPOINT")
    minio_bucket: str = Field(default="", alias="MINIO_BUCKET")
    minio_access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", alias="MINIO_SECRET_KEY")

    apns_enabled: bool = Field(default=False, alias="APNS_ENABLED")
    apns_key_id: str = Field(default="", alias="APNS_KEY_ID")
    apns_team_id: str = Field(default="", alias="APNS_TEAM_ID")
    apns_key_p8: str = Field(default="", alias="APNS_KEY_P8")
    apns_topic: str = Field(default="com.wavetech.ghostline", alias="APNS_TOPIC")

    @property
    def is_production(self) -> bool:
        return self.ghostline_env == "prod"


def load() -> Settings:
    return Settings()
