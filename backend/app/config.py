"""Application settings, loaded from environment variables / the project .env file.

Nothing secret is ever written in code: every value comes from the environment.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] is the project root, where .env lives
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",          # .env has variables for other parts (n8n, frontend)
    )

    # ---- App ----
    app_env: str = "development"
    app_name: str = "WhatsApp AI Business Agent"

    # ---- Database ----
    postgres_user: str = "wa_agent"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "wa_agent"
    postgres_host: str = "127.0.0.1"  # "localhost" adds ~2s per connection on Windows (IPv6 first)
    postgres_port: int = 5432
    database_url_override: str = Field(default="", validation_alias="DATABASE_URL")

    # ---- Seed admin ----
    admin_email: str = "admin@example.com"
    admin_password: SecretStr = SecretStr("")

    # ---- API / Auth ----
    frontend_url: str = "http://localhost:5173"   # the only website allowed to call the API (CORS)
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ---- Gemini AI ----
    gemini_api_key: SecretStr = SecretStr("")    # empty -> AI falls back to FAQ search + handoff
    gemini_model: str = "gemini-3.8-flash"       # default; the dashboard's ai_config can override
    gemini_timeout_seconds: float = 30.0

    # ---- n8n -> backend shared secret ----
    backend_internal_api_key: SecretStr = SecretStr("")

    # ---- n8n public API (read-only, for the Automation Monitoring page) ----
    n8n_base_url: str = "http://127.0.0.1:5690"
    n8n_api_key: SecretStr = SecretStr("")

    # ---- Notifications (who n8n emails) ----
    sales_notify_email: str = "sales@example.com"
    admin_alert_email: str = "admin@example.com"

    # ---- WhatsApp Cloud API ----
    wa_mock_mode: bool = True              # True = never call Meta; sends are only recorded
    wa_env: Literal["test", "production"] = "test"   # Meta test number vs real business number
    wa_graph_api_version: str = "v27.0"
    wa_graph_base_url: str = "https://graph.facebook.com"
    wa_phone_number_id: str = ""
    wa_business_account_id: str = ""
    wa_access_token: SecretStr = SecretStr("")
    wa_app_secret: SecretStr = SecretStr("")      # verifies X-Hub-Signature-256 on webhooks
    wa_verify_token: SecretStr = SecretStr("")    # Meta's GET webhook verification
    wa_api_timeout_seconds: float = 15.0
    wa_max_send_attempts: int = Field(default=3, ge=1, le=5)
    wa_template_language: str = "en_US"

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_be_strong(cls, v: SecretStr) -> SecretStr:
        # A short secret can be brute-forced, which would let attackers forge logins.
        if len(v.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters (set it in .env)")
        return v

    @model_validator(mode="after")
    def check_whatsapp_config(self):
        """Fail at startup (not at 3 a.m. on the first customer message) if config is incomplete."""
        if not self.wa_mock_mode:
            required = {
                "WA_PHONE_NUMBER_ID": self.wa_phone_number_id,
                "WA_ACCESS_TOKEN": self.wa_access_token.get_secret_value(),
                "WA_APP_SECRET": self.wa_app_secret.get_secret_value(),
                "WA_VERIFY_TOKEN": self.wa_verify_token.get_secret_value(),
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"WA_MOCK_MODE=false needs these in .env: {', '.join(missing)}")
        if self.app_env == "production" and self.wa_mock_mode:
            raise ValueError("APP_ENV=production must not run with WA_MOCK_MODE=true")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL. DATABASE_URL in .env wins if it is set."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Create the settings once and reuse them (cached)."""
    return Settings()
