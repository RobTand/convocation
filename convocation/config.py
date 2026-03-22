"""Application configuration via environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    secret_key: str = "change-me-to-a-random-secret"
    debug: bool = False
    base_dir: Path = Path(__file__).resolve().parent.parent

    # Database
    database_url: str = "sqlite+aiosqlite:///./convocation.db"

    # LLM — defaults to Anthropic, switchable to OpenAI-compatible
    llm_provider: str = "anthropic"  # "anthropic" or "openai"
    llm_base_url: str = "https://api.anthropic.com"
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"

    # Site
    site_title: str = "ConvocAItion"
    site_description: str = "Your community's site. Owned by everyone."
    site_url: str = "http://localhost:8080"

    # Content
    content_repo_path: Path = Path("./content-repo")

    # Static output
    output_path: Path = Path("./output")

    # Discord
    discord_webhook_url: str = ""
    discord_bot_token: str = ""

    # VAPID for push notifications
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_claims_email: str = "admin@example.com"

    # Bootstrap admin
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 hours

    # Invite
    invite_expire_hours: int = 72

    @property
    def content_abs_path(self) -> Path:
        if self.content_repo_path.is_absolute():
            return self.content_repo_path
        return self.base_dir / self.content_repo_path

    @property
    def output_abs_path(self) -> Path:
        if self.output_path.is_absolute():
            return self.output_path
        return self.base_dir / self.output_path


settings = Settings()
