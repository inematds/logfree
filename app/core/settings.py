from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/logfree.sqlite"

    cookie_secret: str = "change-me-in-prod-32-bytes-min-please-padding"
    session_ttl_minutes: int = 12 * 60

    tg_bot_token: str = ""
    tg_webhook_secret: str = ""
    tg_webhook_secret_prev: str = ""

    bot_service_token: str = "dev-bot-token-change-me"
    bot_service_token_prev: str = ""

    backup_key_age: str = ""
    audit_unblock_key: str = ""

    osrm_base_url: str = "https://router.project-osrm.org"
    osrm_connect_timeout_s: float = 1.0
    osrm_read_timeout_s: float = 2.0
    osrm_total_timeout_s: float = 3.0
    osrm_fanout_max: int = 30
    osrm_cache_max_bytes: int = 50 * 1024 * 1024
    osrm_cb_failures: int = 3
    osrm_cb_window_s: int = 30
    osrm_cb_open_s: int = 60

    admin_bootstrap_email: str = "admin@example.com"
    admin_bootstrap_password: str = "change-me-on-first-boot"

    owner_lease_ttl_s: int = 30
    owner_lease_heartbeat_s: int = 10

    consulta_retention_days: int = 90
    backup_retention_days: int = 14
    snapshot_retention_days: int = 30

    rate_limit_melhor_per_min: int = 30
    rate_limit_ip_per_min: int = 120

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
