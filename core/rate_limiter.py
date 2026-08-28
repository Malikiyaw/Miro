import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from logger import logger


@dataclass
class Bucket:
    rate: float          # tokens per second
    capacity: float      # burst size
    tokens: float
    updated: float = field(default_factory=time.time)


class RateLimiter:
    """
    Multi-tier token-bucket rate limiter protecting Discord API quotas and
    AI spend. Tiers are independent; the global emergency switch overrides
    everything except explicitly exempted callers (e.g. admins on commands).
    """

    TIERS = ("user", "guild", "command", "ai", "global_emergency")

    DEFAULTS = {
        # key -> (events per minute, burst)
        "user": (30, 45),
        "guild": (300, 450),
        "command": (60, 90),
        "ai": (10, 15),
        "global_emergency": (600, 900),
    }

    def __init__(self, config: Optional[Dict[str, Tuple[float, float]]] = None):
        self._buckets: Dict[Tuple[str, str], Bucket] = {}
        self._lock = threading.Lock()
        self._limits = dict(self.DEFAULTS)
        if config:
            self._limits.update(config)
        self._emergency = False

    def configure_tier(self, tier: str, per_minute: float, burst: float):
        self._limits[tier] = (per_minute, burst)

    def trip_emergency(self):
        """Block all non-exempt consumption (e.g. Discord 429 storm detected)."""
        self._emergency = True
        logger.warning("Rate limiter: GLOBAL EMERGENCY mode engaged")

    def clear_emergency(self):
        self._emergency = False
        logger.info("Rate limiter: emergency mode cleared")

    @property
    def emergency(self) -> bool:
        return self._emergency

    def check(self, tier: str, key: str, cost: float = 1.0, exempt: bool = False) -> Tuple[bool, float]:
        """
        Try to consume `cost` tokens for (tier, key).
        Returns (allowed, retry_after_seconds).
        """
        if tier not in self._limits:
            return True, 0.0
        if self._emergency and not exempt and tier != "global_emergency":
            return False, 5.0

        rate_per_min, burst = self._limits[tier]
        rate = rate_per_min / 60.0
        now = time.time()
        bucket_key = (tier, str(key))

        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = Bucket(rate=rate, capacity=float(burst), tokens=float(burst))
                self._buckets[bucket_key] = bucket
            elapsed = now - bucket.updated
            bucket.updated = now
            bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0.0
            deficit = cost - bucket.tokens
            retry_after = deficit / bucket.rate if bucket.rate > 0 else 60.0
            return False, min(retry_after, 300.0)

    def peek(self, tier: str, key: str) -> float:
        """Current token count without consuming (for diagnostics)."""
        with self._lock:
            bucket = self._buckets.get((tier, str(key)))
            return bucket.tokens if bucket else float(self._limits.get(tier, (0, 0))[1])
