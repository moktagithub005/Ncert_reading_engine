from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://ncert:ncert@localhost:5432/ncert"
    redis_url: str = "redis://localhost:6379/0"
    storage_backend: str = "local"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "ncert"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    public_asset_base_url: str = ""
    jwt_secret: str = ""
    jwt_access_ttl_minutes: int = 30
    jwt_refresh_ttl_days: int = 7
    max_upload_mb: int = 500
    max_pages: int = 1500
    login_rate_limit_per_minute: int = 10
    upload_rate_limit_per_minute: int = 5
    ocr_enabled: bool = True
    ocr_langs: str = "eng+hin"
    cors_origins: str = ""
    sentry_dsn: str = ""
    local_storage_path: str = "/tmp/ncert-storage"
    request_timeout_seconds: int = 30
    page_timeout_seconds: int = 60
    job_timeout_seconds: int = 1800
    body_size_limit_bytes: int = 524_288_000
    body_font_tolerance: float = 0.15
    text_layer_min_chars: int = 30
    topic_min_words: int = 20
    topic_max_words: int = 3000
    heading_max_chars: int = 120
    boilerplate_repeat_ratio: float = 0.25
    body_text_min_font: float = 0.0
    page_number_regex: str = r"^\s*(?:page\s*)?\d{1,4}\s*$"
    chapter_heading_regex: str = r"^\s*(?:chapter|अध्याय)\s*(\d+)\s*[:.\-–]?\s*(.*)$"
    numbered_heading_regex: str = r"^\s*(\d+(?:\.\d+){0,5})\s+(.+?)\s*$"
    figure_caption_regex: str = r"\b(?:Fig(?:ure)?\.?\s*[\d.]+)\b"
    special_block_regex: str = (
        r"activity|let us recall|do you know|keywords|exercise|summary|what you have learnt"
    )
    activity_regex: str = r"activity|let us recall"
    exercise_regex: str = r"exercise|exercises"
    summary_regex: str = r"summary|what you have learnt"
    boxed_block_regex: str = r"do you know|keywords|boxed"
    formula_symbol_ratio: float = 0.25
    list_item_regex: str = r"^\s*(?:[•▪◦]|[-–])\s+"
    table_min_cells: int = 4

    @field_validator("storage_backend")
    @classmethod
    def valid_storage_backend(cls, value: str) -> str:
        if value not in {"local", "s3"}:
            raise ValueError("STORAGE_BACKEND must be 'local' or 's3'")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
