"""Service configuration from environment (.env supported)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_port: int = 8000
    log_level: str = "info"

    database_url: str = "postgresql://qd:qd@localhost:5432/qd"
    payswift_base_url: str = "http://localhost:8081"

    llm_provider: str = "stub"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout_s: float = 3.5
    llm_max_calls: int = 2

    opstoken: str = ""


settings = Settings()
