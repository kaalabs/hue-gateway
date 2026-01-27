from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import os


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class AppConfig:
    port: int
    bridge_host: Optional[str]
    application_key: Optional[str]
    auth_tokens: list[str]
    api_keys: list[str]
    cache_resync_seconds: int
    fuzzy_match_threshold: float
    fuzzy_match_autopick_threshold: float
    fuzzy_match_margin: float
    rate_limit_rps: float
    rate_limit_burst: int
    retry_max_attempts: int
    retry_base_delay_ms: int

    @staticmethod
    def from_env() -> "AppConfig":
        return AppConfig(
            port=int(os.getenv("PORT", "8000")),
            bridge_host=os.getenv("HUE_BRIDGE_HOST"),
            application_key=os.getenv("HUE_APPLICATION_KEY"),
            auth_tokens=_split_csv(os.getenv("GATEWAY_AUTH_TOKENS")),
            api_keys=_split_csv(os.getenv("GATEWAY_API_KEYS")),
            cache_resync_seconds=int(os.getenv("CACHE_RESYNC_SECONDS", "300")),
            fuzzy_match_threshold=float(os.getenv("FUZZY_MATCH_THRESHOLD", "0.90")),
            fuzzy_match_autopick_threshold=float(os.getenv("FUZZY_MATCH_AUTOPICK_THRESHOLD", "0.95")),
            fuzzy_match_margin=float(os.getenv("FUZZY_MATCH_MARGIN", "0.05")),
            rate_limit_rps=float(os.getenv("RATE_LIMIT_RPS", "5")),
            rate_limit_burst=int(os.getenv("RATE_LIMIT_BURST", "10")),
            retry_max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", "3")),
            retry_base_delay_ms=int(os.getenv("RETRY_BASE_DELAY_MS", "200")),
        )
