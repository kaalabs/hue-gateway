from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    def __init__(self, *, rate_per_sec: float, burst: int) -> None:
        self._rate = max(0.0, float(rate_per_sec))
        self._capacity = max(0.0, float(burst))
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        allowed, _ = self.allow_with_retry_after_ms(key, cost=cost)
        return allowed

    def allow_with_retry_after_ms(self, key: str, *, cost: float = 1.0) -> tuple[bool, int]:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated_at=now)
            self._buckets[key] = bucket

        elapsed = now - bucket.updated_at
        bucket.updated_at = now
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True, 0

        # Compute an actionable retry hint.
        deficit = max(0.0, float(cost) - bucket.tokens)
        if self._rate <= 0.0:
            return False, 0
        retry_after_ms = int((deficit / self._rate) * 1000.0) + 1
        return False, retry_after_ms
