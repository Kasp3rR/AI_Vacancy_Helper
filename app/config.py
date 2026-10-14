from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Vacancy Helper"
    database_url: str = "postgresql+psycopg://jobhelper:jobhelper@localhost:5432/jobhelper"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    hh_url: str = "https://api.hh.ru"
    hh_user_agent: str = "AI-Vacancy-Helper/0.1 (https://github.com/Kasp3rR/AI_Vacancy_Helper)"
    hh_timeout_seconds: float = Field(default=10, ge=1, le=60)
    jwt_secret: SecretStr = Field(min_length=32)
    access_minutes: int = Field(default=15, ge=1, le=60)
    refresh_days: int = Field(default=7, ge=1, le=90)
    upload_dir: Path = Path("uploads")
    max_pdf_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=50, ge=1, le=200)
    max_text_chars: int = Field(default=40000, ge=100, le=100000)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_timeout_seconds: float = Field(default=180, ge=1, le=600)

    @property
    def job_time_limit(self) -> int:
        return int(
            max(self.ollama_timeout_seconds * 3 + 90, 3 * (self.hh_timeout_seconds * 3 + 20) + 60)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
