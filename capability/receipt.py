"""ExecutionReceipt v2 — section 22 of MIRO V11."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ErrorClass, FailureKind, ToolResult, parameters_hash
from .permissions import PermissionCheck
from .risk import RiskAssessment


@dataclass
class ExecutionReceipt:
    execution_id: str
    job_id: str
    guild_id: str
    actor_id: str
    tool: str
    version: str
    parameters_hash: str
    target_ids: List[str]
    started_at: float
    finished_at: float
    permission_result: PermissionCheck
    safety_result: RiskAssessment
    discord_result: ToolResult
    observed_state: Dict[str, Any] = field(default_factory=dict)
    verified_state: Dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    success: bool = False
    verified: bool = False
    error_code: Optional[ErrorClass] = None
    error_message: str = ""
    rollback_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "guild_id": self.guild_id,
            "actor_id": self.actor_id,
            "tool": self.tool,
            "version": self.version,
            "parameters_hash": self.parameters_hash,
            "target_ids": list(self.target_ids),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "permission": {
                "allowed": self.permission_result.allowed,
                "missing": self.permission_result.missing,
                "explanation": self.permission_result.explain(),
            },
            "risk": {
                "level": self.safety_result.level.value,
                "score": self.safety_result.score,
                "factors": self.safety_result.factors,
            },
            "discord": self.discord_result.to_dict(),
            "observed": self.observed_state,
            "verified": self.verified_state,
            "retry_count": self.retry_count,
            "success": self.success,
            "verified": self.verified,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
            "rollback_available": self.rollback_available,
        }


class ReceiptManager:
    """In-memory history of execution receipts."""

    def __init__(self, *, max_receipts: int = 10000) -> None:
        self._receipts: List[ExecutionReceipt] = []
        self._max = max_receipts

    def record(self, receipt: ExecutionReceipt) -> None:
        self._receipts.append(receipt)
        if len(self._receipts) > self._max:
            self._receipts = self._receipts[-self._max:]

    def all(self) -> List[ExecutionReceipt]:
        return list(self._receipts)

    def for_guild(self, guild_id: str) -> List[ExecutionReceipt]:
        return [r for r in self._receipts if r.guild_id == str(guild_id)]

    def for_tool(self, tool: str) -> List[ExecutionReceipt]:
        return [r for r in self._receipts if r.tool == tool]
