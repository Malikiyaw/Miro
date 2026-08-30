"""LoopGuard, ToolCallBudget, Simulator — sections 50-52 of MIRO V11."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ToolDefinition, ToolResult
from .risk import RiskAssessment, RiskEngine, ScopeLimit


class BudgetExceeded(Exception):
    pass


@dataclass
class ToolCallBudget:
    """Per-job tool call budget."""

    max_steps: int = 100
    max_retries_per_action: int = 3
    max_runtime_seconds: float = 120.0
    max_mutations: int = 50
    max_parallel_actions: int = 3

    def check(self, *, steps: int, mutations: int, started_at: float,
              retries_used: int) -> None:
        if steps > self.max_steps:
            raise BudgetExceeded(f"max_steps={self.max_steps} exceeded ({steps})")
        if mutations > self.max_mutations:
            raise BudgetExceeded(f"max_mutations={self.max_mutations} exceeded ({mutations})")
        if time.time() - started_at > self.max_runtime_seconds:
            raise BudgetExceeded("max_runtime exceeded")
        if retries_used > self.max_retries_per_action:
            raise BudgetExceeded(f"max_retries_per_action={self.max_retries_per_action} exceeded")


class LoopGuard:
    """Detects repeated identical tool calls and forces a re-plan."""

    def __init__(self, *, max_repeats: int = 3) -> None:
        self.max_repeats = max_repeats
        self._history: List[str] = []

    def _signature(self, tool: str, params: Mapping[str, Any]) -> str:
        payload = json.dumps({"tool": tool, "params": dict(params)}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def record(self, tool: str, params: Mapping[str, Any]) -> None:
        self._history.append(self._signature(tool, params))

    def is_looping(self) -> bool:
        if len(self._history) < self.max_repeats:
            return False
        last = self._history[-1]
        # Check the last max_repeats entries are identical.
        return all(self._history[-i] == last for i in range(1, self.max_repeats + 1))

    def reset(self) -> None:
        self._history.clear()


@dataclass
class SimulationResult:
    target: str
    permissions_ok: bool
    risk: RiskAssessment
    expected_outcome: str
    reversible: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "permissions_ok": self.permissions_ok,
            "risk_level": self.risk.level.value,
            "risk_score": self.risk.score,
            "expected_outcome": self.expected_outcome,
            "reversible": self.reversible,
        }


class Simulator:
    """Pre-execution simulation. Pure function of tool + parameters + context."""

    def __init__(self, *, risk_engine: Optional[RiskEngine] = None) -> None:
        self._risk = risk_engine or RiskEngine()

    def simulate(
        self,
        tool: ToolDefinition,
        parameters: Mapping[str, Any],
        *,
        permission_check_passed: bool = True,
        target_count: int = 1,
    ) -> SimulationResult:
        risk = self._risk.assess(tool, target_count=target_count)
        if not permission_check_passed:
            return SimulationResult(
                target=str(parameters.get("target", "")),
                permissions_ok=False,
                risk=risk,
                expected_outcome="blocked: missing permissions",
                reversible=False,
            )
        outcome = "would_execute"
        if tool.mutates_state and not tool.supports_rollback:
            outcome = "would_execute (irreversible)"
        elif not tool.mutates_state:
            outcome = "read-only"
        return SimulationResult(
            target=str(parameters.get("target", parameters.get("channel_id",
                parameters.get("role_id", parameters.get("member_id", ""))))),
            permissions_ok=permission_check_passed,
            risk=risk,
            expected_outcome=outcome,
            reversible=bool(tool.supports_rollback),
        )
