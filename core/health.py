import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from logger import logger


class Status(str, Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


@dataclass
class SubsystemHealth:
    name: str
    status: Status = Status.UNKNOWN
    last_check: float = 0.0
    latency_ms: float = 0.0
    detail: str = ""
    failures: int = 0


class HealthMonitor:
    """
    Subsystem watchdog. Registered checkers return True (ONLINE), False
    (OFFLINE) or raise (OFFLINE). Consecutive failures flip a subsystem to
    DEGRADED before OFFLINE and trigger registered recovery callbacks.
    """

    def __init__(self, degraded_after: int = 2, offline_after: int = 4):
        self._checkers: Dict[str, Callable[[], Any]] = {}
        self._recovery: Dict[str, List[Callable[[SubsystemHealth], Any]]] = {}
        self._state: Dict[str, SubsystemHealth] = {}
        self._degraded_after = degraded_after
        self._offline_after = offline_after
        self._running = False
        self.interval = 60

    def register(self, name: str, checker: Callable[[], Any],
                 on_failure: Optional[Callable[[SubsystemHealth], Any]] = None):
        self._checkers[name] = checker
        self._state.setdefault(name, SubsystemHealth(name=name))
        if on_failure:
            self._recovery.setdefault(name, []).append(on_failure)

    def snapshot(self) -> Dict[str, dict]:
        return {
            name: {
                "status": health.status.value,
                "last_check": health.last_check,
                "latency_ms": round(health.latency_ms, 1),
                "detail": health.detail,
                "failures": health.failures,
            }
            for name, health in self._state.items()
        }

    def overall(self) -> str:
        if not self._state:
            return Status.UNKNOWN.value
        statuses = [h.status for h in self._state.values()]
        if Status.OFFLINE in statuses:
            return Status.OFFLINE.value
        if Status.DEGRADED in statuses or Status.UNKNOWN in statuses:
            return Status.DEGRADED.value
        return Status.ONLINE.value

    async def check_all(self):
        for name, checker in list(self._checkers.items()):
            health = self._state.setdefault(name, SubsystemHealth(name=name))
            start = time.time()
            try:
                result = checker()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=15.0)
                ok = result is not False
                detail = ""
            except Exception as e:
                ok, detail = False, str(e)[:200]
            health.latency_ms = (time.time() - start) * 1000
            health.last_check = time.time()
            health.detail = detail
            if ok:
                health.failures = 0
                health.status = Status.ONLINE
            else:
                health.failures += 1
                if health.failures >= self._offline_after:
                    health.status = Status.OFFLINE
                elif health.failures >= self._degraded_after:
                    health.status = Status.DEGRADED
                logger.warning(f"Health: {name} is {health.status.value} ({health.failures} failures) {detail}")
                await self._trigger_recovery(health)

    async def _trigger_recovery(self, health: SubsystemHealth):
        for cb in self._recovery.get(health.name, []):
            try:
                result = cb(health)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Health recovery for {health.name} failed: {e}")

    async def run_forever(self):
        self._running = True
        while self._running:
            try:
                await self.check_all()
            except Exception as e:
                logger.error(f"Health monitor loop error: {e}")
            await asyncio.sleep(self.interval)

    def start(self):
        asyncio.create_task(self.run_forever())

    def stop(self):
        self._running = False
