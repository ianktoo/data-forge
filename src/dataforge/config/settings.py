"""Application settings — loaded from environment / .env file."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DATAFORGE_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # Pipeline
    rate_limit: float = Field(2.0, description="Requests per second per domain")
    max_pages: int = Field(500, description="Max pages scraped per session")
    max_crawl_pages: int = Field(50, description="Max pages discovered by BFS crawler (sitemap fallback)")
    max_crawl_depth: int = Field(3, description="Max link depth for BFS crawler")
    chunk_size: int = Field(512, description="Tokens per chunk")
    chunk_overlap: int = Field(64, description="Token overlap between chunks")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    output_dir: Path = Path("./output")
    db_path: Path = Path("./dataforge.db")
    ignore_robots: bool = Field(False, description="Skip robots.txt enforcement")

    # Export
    huggingface_token: str = ""
    kaggle_username: str = ""
    kaggle_key: str = ""

    # Provider keys (passed through to litellm via env, not stored here)
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    groq_api_key: str = Field("", alias="GROQ_API_KEY")
    together_api_key: str = Field("", alias="TOGETHER_API_KEY")
    ollama_base_url: str = Field("http://localhost:11434", alias="OLLAMA_BASE_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("output_dir", "db_path", mode="before")
    @classmethod
    def expand(cls, v: str | Path) -> Path:
        return Path(v).expanduser().resolve()

    def session_dir(self, session_id: str) -> Path:
        return self.output_dir / "sessions" / session_id

    def logs_dir(self) -> Path:
        return self.output_dir / "logs"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
