"""
AURON Backend Configuration
Loads settings from environment variables and .env file.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Vultr Serverless Inference (OpenAI-compatible)
    vultr_inference_api_key: str = Field(default="", alias="VULTR_INFERENCE_API_KEY")
    vultr_inference_base_url: str = Field(
        default="https://api.vultrinference.com/v1",
        alias="VULTR_INFERENCE_BASE_URL"
    )
    model_name: str = Field(
        default="deepseek-v4.1-flash-1017",
        alias="MODEL_NAME"
    )

    # Server settings
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    debug: bool = Field(default=False, alias="DEBUG")

    # Sandbox execution limits
    sandbox_image_tag: str = Field(default="latest", alias="SANDBOX_IMAGE_TAG")
    max_concurrent_sandboxes: int = Field(default=2, alias="MAX_CONCURRENT_SANDBOXES")
    sandbox_timeout_seconds: int = Field(default=60, alias="SANDBOX_TIMEOUT_SECONDS")
    max_retries: int = Field(default=2, alias="MAX_RETRIES")

    # Upload validation limits (PRD §6 FR-01)
    max_upload_size_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_UPLOAD_SIZE_BYTES")  # 10 MB
    max_rows: int = Field(default=100000, alias="MAX_ROWS")
    max_cols: int = Field(default=200, alias="MAX_COLS")
    max_question_chars: int = Field(default=2000, alias="MAX_QUESTION_CHARS")

    # Red Team Arena settings (PRD §11 & §14)
    red_team_enabled: bool = Field(default=False, alias="RED_TEAM_ENABLED")
    red_team_token: str = Field(default="", alias="RED_TEAM_TOKEN")

    # File and directory paths
    data_dir: Path = Field(default=Path("data"))
    db_path: Path = Field(default=Path("data/auron.db"))
    uploads_dir: Path = Field(default=Path("data/uploads"))
    workspace_dir: Path = Field(default=Path("workspace"))
    receipts_dir: Path = Field(default=Path("receipts"))
    samples_dir: Path = Field(default=Path("data/samples"))

    def ensure_directories(self) -> None:
        """Create necessary host directories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        self.samples_dir.mkdir(parents=True, exist_ok=True)

    def sanitized_dict(self) -> dict:
        """Return configuration as dict with sensitive secrets redacted."""
        d = self.model_dump()
        if d.get("vultr_inference_api_key"):
            d["vultr_inference_api_key"] = "[REDACTED]"
        if d.get("red_team_token"):
            d["red_team_token"] = "[REDACTED]"
        return d


# Global settings singleton
settings = Settings()

