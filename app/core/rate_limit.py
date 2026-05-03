"""Rate limit token-bucket in-process (PLAN.md item 39).

Cada bucket tem chave (ex.: "melhor:user:42", "ip:1.2.3.4"), capacidade e
recarga por segundo. Não usa SQLite no caminho hot.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Bucket:
    capacity: float
    refill_per_s: float
    tokens: float
    last_ts: float


class TokenBucketLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = {}

    def configure(self, key_prefix: str, capacity: int, per_seconds: int) -> None:
        # capacidade total = `capacity` em janela de `per_seconds`
        # refill = capacity / per_seconds
        self._defaults = getattr(self, "_defaults", {})
        self._defaults[key_prefix] = (capacity, per_seconds)

    def allow(self, key: str, *, capacity: int = 30, per_seconds: int = 60) -> bool:
        prefix = key.split(":", 1)[0] if ":" in key else key
        if hasattr(self, "_defaults") and prefix in self._defaults:
            capacity, per_seconds = self._defaults[prefix]
        now = time.monotonic()
        b = self._buckets.get(key)
        if b is None:
            b = Bucket(
                capacity=float(capacity),
                refill_per_s=float(capacity) / float(per_seconds),
                tokens=float(capacity),
                last_ts=now,
            )
            self._buckets[key] = b
        # recarga
        elapsed = now - b.last_ts
        b.tokens = min(b.capacity, b.tokens + elapsed * b.refill_per_s)
        b.last_ts = now
        if b.tokens >= 1.0:
            b.tokens -= 1.0
            return True
        return False

    def reset(self) -> None:
        self._buckets.clear()


# Limiter singleton.
limiter = TokenBucketLimiter()
limiter.configure("melhor", capacity=30, per_seconds=60)
limiter.configure("ip", capacity=120, per_seconds=60)
limiter.configure("login", capacity=10, per_seconds=60)
limiter.configure("export", capacity=1, per_seconds=3600)
