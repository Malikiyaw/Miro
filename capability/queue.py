"""RateLimitManager, ActionQueue, PerGuildConcurrency, CircuitBreaker.

Sections 28-31 of MIRO V11.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass
class RateLimitBucket:
    route: str
    remaining: int = 5
    reset_at: float = 0.0
    retry_after: float = 0.0

    def consume(self) -> bool:
        if self.retry_after > 0 and time.time() < self.reset_at:
            return False
        if self.remaining > 0:
            self.remaining -= 1
            return True
        return False


class RateLimitManager:
    """Tracks route + guild buckets."""

    def __init__(self) -> None:
        self._routes: Dict[str, RateLimitBucket] = {}
        self._guilds: Dict[str, RateLimitBucket] = {}

    def update_route(self, route: str, *, remaining: int, reset_at: float,
                     retry_after: float = 0.0) -> None:
        self._routes[route] = RateLimitBucket(
            route=route, remaining=remaining, reset_at=reset_at, retry_after=retry_after,
        )

    def update_guild(self, guild_id: str, *, remaining: int, reset_at: float,
                     retry_after: float = 0.0) -> None:
        self._guilds[str(guild_id)] = RateLimitBucket(
            route=f"guild:{guild_id}", remaining=remaining, reset_at=reset_at, retry_after=retry_after,
        )

    def can_dispatch(self, route: str, guild_id: Optional[str]) -> bool:
        b = self._routes.get(route)
        if b is not None and not b.consume():
            return False
        if guild_id is not None:
            gb = self._guilds.get(str(guild_id))
            if gb is not None and not gb.consume():
                return False
        return True

    def wait_time(self, route: str, guild_id: Optional[str]) -> float:
        now = time.time()
        wait = 0.0
        b = self._routes.get(route)
        if b is not None and b.reset_at > now:
            wait = max(wait, b.reset_at - now)
        if guild_id is not None:
            gb = self._guilds.get(str(guild_id))
            if gb is not None and gb.reset_at > now:
                wait = max(wait, gb.reset_at - now)
        return wait


@dataclass
class QueueItem:
    name: str
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)


class ActionQueue:
    """Global FIFO with per-guild concurrency limits."""

    def __init__(self, *, per_guild_concurrency: int = 3) -> None:
        self._items: List[QueueItem] = []
        self._per_guild: Dict[str, int] = {}
        self._limit = per_guild_concurrency
        self._lock = threading.Lock()

    def enqueue(self, item: QueueItem) -> None:
        with self._lock:
            self._items.append(item)

    def next_for_guild(self, guild_id: str) -> Optional[QueueItem]:
        with self._lock:
            for i, it in enumerate(self._items):
                gid = str(it.kwargs.get("guild_id", ""))
                if gid == str(guild_id) or not gid:
                    if self._per_guild.get(str(guild_id), 0) < self._limit:
                        self._per_guild[str(guild_id)] = self._per_guild.get(str(guild_id), 0) + 1
                        return self._items.pop(i)
                    return None
        return None

    def finish(self, guild_id: str) -> None:
        with self._lock:
            cur = self._per_guild.get(str(guild_id), 0)
            if cur > 0:
                self._per_guild[str(guild_id)] = cur - 1

    def depth(self) -> int:
        with self._lock:
            return len(self._items)


class PerGuildConcurrency:
    """Per-guild concurrency gate (synchronous)."""

    def __init__(self, *, limit: int = 3) -> None:
        self._limit = limit
        self._active: Dict[str, int] = {}

    def try_acquire(self, guild_id: str) -> bool:
        cur = self._active.get(str(guild_id), 0)
        if cur >= self._limit:
            return False
        self._active[str(guild_id)] = cur + 1
        return True

    def release(self, guild_id: str) -> None:
        cur = self._active.get(str(guild_id), 0)
        if cur > 0:
            self._active[str(guild_id)] = cur - 1


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Open the circuit on N consecutive failures; cool down then half-open."""

    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: Optional[float] = None

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    def can_proceed(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if self._opened_at is not None and time.time() - self._opened_at >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow one probe.
        return True

    @property
    def state(self) -> CircuitState:
        # Refresh in case the cooldown elapsed.
        if self._state == CircuitState.OPEN and self._opened_at is not None \
                and time.time() - self._opened_at >= self.cooldown_seconds:
            self._state = CircuitState.HALF_OPEN
        return self._state
