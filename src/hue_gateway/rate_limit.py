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
            return True
        return False

