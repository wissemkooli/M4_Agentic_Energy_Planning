"""Central configuration for the M4 backend.

All settings are read from environment variables (optionally from a `.env`
file located in the M4-Mahdi folder). No absolute paths are hard-coded: every
relative path is resolved against the M4-Mahdi root directory.
"""
from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# M4-Mahdi/ (the folder that contains the `m4` package)
BACKEND_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger("m4.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---------------------------------------------------
    app_env: str = Field(default="development", description="development | test | production")
    app_name: str = "M4 Agentic Energy Planning API"
    log_level: str = "INFO"

    # --- Data -------------------------------------------------------------
    # YAML manifest that declares where each input lives and whether it is MOCK or REAL.
    data_sources_file: str = "data/input/sources.yml"

    # --- Database -----------------------------------------------------------
    # SQLite by default (no external service needed). PostgreSQL example:
    # postgresql+psycopg://m4_user:password@localhost:5432/m4
    database_url: str = "sqlite:///./data/m4.db"

    # --- Security -----------------------------------------------------------
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    refresh_cookie_secure: bool = False  # set true when served over HTTPS
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- LLM provider (OpenAI-compatible API, e.g. OpenRouter) --------------------
    llm_provider: str = "openrouter"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 45.0
    llm_temperature: float = 0.1

    # --- Optimization -------------------------------------------------------
    solver_name: str = "appsi_highs"
    solver_time_limit_seconds: float = 120.0

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        return v

    # ------------------------------------------------------------------
    def resolve_path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (BACKEND_ROOT / p).resolve()

    @property
    def data_sources_path(self) -> Path:
        return self.resolve_path(self.data_sources_file)

    @property
    def resolved_database_url(self) -> str:
        """Resolve relative SQLite paths against the backend root."""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix) and not url.startswith("sqlite:////") and ":memory:" not in url:
            rel = url[len(prefix):]
            path = self.resolve_path(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{path.as_posix()}"
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model and self.llm_base_url)


_EPHEMERAL_SECRET: str | None = None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    global _EPHEMERAL_SECRET
    if not settings.jwt_secret_key:
        if settings.app_env == "production":
            raise RuntimeError(
                "JWT_SECRET_KEY is not set. Generate one with:\n"
                "  python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
                "and add it to M4-Mahdi/.env"
            )
        if _EPHEMERAL_SECRET is None:
            _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
            logger.warning(
                "JWT_SECRET_KEY not set: using an ephemeral development secret. "
                "Sessions will be invalidated whenever the server restarts."
            )
        settings.jwt_secret_key = _EPHEMERAL_SECRET
    return settings
