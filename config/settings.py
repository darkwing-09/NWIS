from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of configuration for NWIS."""

    # App
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "nwis-api"
    debug: bool = False

    # DB
    database_url: str = "postgresql://postgres:postgres@localhost:5432/nwis"
    database_pool_size: int = 10

    # Object storage
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_bucket: str = "nwis-documents"
    object_storage_access_key: str = "minioadmin"
    object_storage_secret_key: str = "minioadmin"

    # Queue / Broker
    broker_url: str = "redis://localhost:6379/0"

    # OCR
    ocr_text_layer_threshold_chars: int = 100
    ocr_page_confidence_floor: float = 0.5
    ocr_header_repeat_threshold: float = 0.6

    # Extraction & Correlation
    correlation_depth_band_m: float = 100.0
    correlation_default_radius_km: float = 5.0

    # Risk
    risk_threshold_medium: int = 2
    risk_threshold_high: int = 3

    # Alerts
    alert_escalation_timeout_sec: int = 1800

    # eRTMAC
    ertmac_staleness_threshold_sec: int = 60
    ertmac_adapter_mode: Literal["simulator", "production"] = "simulator"

    # Auth
    oidc_issuer_url: str = "http://localhost:8080/realms/nwis"
    oidc_audience: str = "nwis-api"
    jwt_secret_key: str = "dev-insecure-secret-key-change-in-production"

    # Secrets
    secrets_backend: Literal["vault", "env"] = "env"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()
