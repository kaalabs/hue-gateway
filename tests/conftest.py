import pytest

from hue_gateway.config import AppConfig


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        port=8000,
        bridge_host=None,
        application_key=None,
        auth_tokens=["dev-token"],
        api_keys=["dev-key"],
        cache_resync_seconds=300,
        fuzzy_match_threshold=0.90,
        fuzzy_match_autopick_threshold=0.95,
        fuzzy_match_margin=0.05,
        rate_limit_rps=5.0,
        rate_limit_burst=10,
        retry_max_attempts=3,
        retry_base_delay_ms=1,
    )

