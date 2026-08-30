"""BulkExecutor, TransactionPlanner, ActionLock, JobControl — sections 16-18, 20-22."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ToolResult


class BulkMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL_SAFE = "parallel_safe"
    TRANSACTIONAL = "transactional"
    BEST_EFFORT = "best_effort"


class BulkOutcome(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BulkResult:
    requested: int
    attempted: int = 0
    succeeded: int = 0
    verified: int = 0
    failed: int = 0
    outcomes: List[ToolResult] = field(default_factory=list)
    outcome: BulkOutcome = BulkOutcome.PENDING

    def finalize(self) -> None:
        if self.requested == 0:
            self.outcome = BulkOutcome.COMPLETE
        elif self.succeeded >= self.requested:
            self.outcome = BulkOutcome.COMPLETE
        elif self.succeeded == 0:
            self.outcome = BulkOutcome.FAILED
        else:
            self.outcome = BulkOutcome.PARTIAL

    def ratio(self) -> str:
        return f"{self.succeeded}/{self.requested}"


class BulkExecutor:
    """Runs a list of ToolResults (or callables) under a policy.

    The LLM cannot override the success numbers; the executor reports
    what actually happened.
    """

    def __init__(self, *, lock_manager: Optional["LockManager"] = None) -> None:
        self._lock_manager = lock_manager or LockManager()

    def run_sync(
        self,
        steps: Sequence[Callable[[], ToolResult]],
        *,
        mode: BulkMode = BulkMode.BEST_EFFORT,
        targets: Optional[Sequence[str]] = None,
    ) -> BulkResult:
        result = BulkResult(requested=len(steps))
        if not steps:
            result.outcome = BulkOutcome.COMPLETE
            return result
        if mode == BulkMode.PARALLEL_SAFE:
            return self._run_parallel(steps, result, targets or [])
        if mode == BulkMode.TRANSACTIONAL:
            return self._run_transactional(steps, result, targets or [])
        return self._run_sequential(steps, result, targets or [])

    def _run_sequential(
        self,
        steps: Sequence[Callable[[], ToolResult]],
        result: BulkResult,
        targets: Sequence[str],
    ) -> BulkResult:
        for i, step in enumerate(steps):
            target = targets[i] if i < len(targets) else ""
            lock_token = self._lock_manager.acquire(target) if target else None
            try:
                result.attempted += 1
                outcome = step()
                if outcome.success and outcome.verified:
                    result.succeeded += 1
                    result.verified += 1
                else:
                    result.failed += 1
                result.outcomes.append(outcome)
            finally:
                if lock_token is not None:
                    self._lock_manager.release(lock_token)
        result.finalize()
        return result

    def _run_parallel(
        self,
        steps: Sequence[Callable[[], ToolResult]],
        result: BulkResult,
        targets: Sequence[str],
    ) -> BulkResult:
        out: List[ToolResult] = [None] * len(steps)  # type: ignore[list-item]
        def runner(i: int, step: Callable[[], ToolResult], target: str) -> None:
            lock_token = self._lock_manager.acquire(target) if target else None
            try:
                out[i] = step()
            finally:
                if lock_token is not None:
                    self._lock_manager.release(lock_token)
        threads = []
        for i, (step, target) in enumerate(zip(steps, targets + [""] * (len(steps) - len(targets)))):
            t = threading.Thread(target=runner, args=(i, step, target), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        for o in out:
            if o is None:
                o = ToolResult.fail("parallel step produced no result")
            result.attempted += 1
            if o.success and o.verified:
                result.succeeded += 1
                result.verified += 1
            else:
                result.failed += 1
            result.outcomes.append(o)
        result.finalize()
        return result

    def _run_transactional(
        self,
        steps: Sequence[Callable[[], ToolResult]],
        result: BulkResult,
        targets: Sequence[str],
    ) -> BulkResult:
        # Transactional: stop on first failure and mark as FAILED.
        for i, step in enumerate(steps):
            target = targets[i] if i < len(targets) else ""
            lock_token = self._lock_manager.acquire(target) if target else None
            try:
                result.attempted += 1
                outcome = step()
                if not outcome.success:
                    result.failed += 1
                    result.outcomes.append(outcome)
                    result.outcome = BulkOutcome.FAILED
                    return result
                result.outcomes.append(outcome)
                if outcome.verified:
                    result.succeeded += 1
                    result.verified += 1
            finally:
                if lock_token is not None:
                    self._lock_manager.release(lock_token)
        result.finalize()
        return result


@dataclass
class ActionLock:
    target: str
    owner: str
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    acquired_at: float = field(default_factory=time.time)


class LockManager:
    """Prevents concurrent conflicting actions on the same target."""

    def __init__(self) -> None:
        self._locks: Dict[str, ActionLock] = {}
        self._waiters: Dict[str, List[str]] = {}

    def acquire(self, target: str, *, owner: str = "default") -> Optional[str]:
        target = str(target)
        existing = self._locks.get(target)
        if existing is None:
            lock = ActionLock(target=target, owner=owner)
            self._locks[target] = lock
            return lock.token
        # Conflict: best-effort fail-safe, return None to signal "wait or skip".
        return None

    def release(self, token: str) -> bool:
        for target, lock in list(self._locks.items()):
            if lock.token == token:
                del self._locks[target]
                return True
        return False

    def is_locked(self, target: str) -> bool:
        return str(target) in self._locks


@dataclass
class TransactionStep:
    name: str
    status: JobStatus = JobStatus.PENDING
    result: Optional[ToolResult] = None
    error: str = ""


class TransactionPlanner:
    """Plans a multi-step setup with known per-step state.

    If step N fails, the planner returns PARTIAL and tells the runtime
    which steps exist on disk.
    """

    def __init__(self) -> None:
        self._steps: List[TransactionStep] = []

    def add(self, name: str) -> None:
        self._steps.append(TransactionStep(name=name))

    def mark(self, name: str, *, status: JobStatus, result: Optional[ToolResult] = None,
             error: str = "") -> None:
        for s in self._steps:
            if s.name == name:
                s.status = status
                s.result = result
                s.error = error
                return

    def state(self) -> Dict[str, Any]:
        out = {
            "total": len(self._steps),
            "completed": sum(1 for s in self._steps if s.status == JobStatus.COMPLETED),
            "failed": sum(1 for s in self._steps if s.status == JobStatus.FAILED),
            "pending": [s.name for s in self._steps if s.status == JobStatus.PENDING],
            "done": [s.name for s in self._steps if s.status == JobStatus.COMPLETED],
        }
        if out["failed"] > 0 and out["completed"] < out["total"]:
            out["state"] = "PARTIAL"
        elif out["completed"] == out["total"] and out["total"] > 0:
            out["state"] = "COMPLETE"
        elif out["total"] == 0:
            out["state"] = "EMPTY"
        else:
            out["state"] = "IN_PROGRESS"
        return out


@dataclass
class JobControl:
    """Identity record for a long-running action."""

    job_id: str
    execution_id: str
    guild_id: str
    actor_id: str
    target_id: str
    tool: str
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, *, guild_id: str, actor_id: str, target_id: str, tool: str) -> "JobControl":
        return cls(
            job_id=uuid.uuid4().hex,
            execution_id=uuid.uuid4().hex,
            guild_id=str(guild_id),
            actor_id=str(actor_id),
            target_id=str(target_id),
            tool=tool,
        )

    def update(self, status: JobStatus) -> None:
        self.status = status
        self.updated_at = time.time()
