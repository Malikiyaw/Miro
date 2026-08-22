"""Agent state machine + canonical result objects."""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentState(str, Enum):
    PLANNING = "PLANNING"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    VALIDATING = "VALIDATING"
    AUTHORIZED = "AUTHORIZED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    OBSERVING = "OBSERVING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECOVER_FAILED = "RECOVER_FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


# Back-compat alias used across the codebase
JobStatus = AgentState


class ErrorType(str, Enum):
    NONE = "NONE"
    MISSING_PERMISSION = "MISSING_PERMISSION"
    PROTECTED_TARGET = "PROTECTED_TARGET"
    NOT_FOUND = "NOT_FOUND"
    INVALID_PARAMS = "INVALID_PARAMS"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SEMANTIC_MISMATCH = "SEMANTIC_MISMATCH"
    REFUSED_POLICY = "REFUSED_POLICY"
    UNKNOWN = "UNKNOWN"


def classify_error(text: str) -> ErrorType:
    t = (text or "").lower()
    if not t:
        return ErrorType.UNKNOWN
    if "protected" in t:
        return ErrorType.PROTECTED_TARGET
    if "permission" in t or "lacks" in t or "forbidden" in t:
        return ErrorType.MISSING_PERMISSION
    if "not found" in t or "does not exist" in t or "no channels matching" in t:
        return ErrorType.NOT_FOUND
    if "invalid parameter" in t or "requires" in t and "param" in t:
        return ErrorType.INVALID_PARAMS
    if "rejected" in t or "object type" in t:
        return ErrorType.SEMANTIC_MISMATCH
    if "refused" in t:
        return ErrorType.REFUSED_POLICY
    if "timed out" in t or "timeout" in t:
        return ErrorType.TIMEOUT
    if "rate limit" in t or "429" in t:
        return ErrorType.RATE_LIMIT
    if "connection" in t or "network" in t:
        return ErrorType.NETWORK_ERROR
    return ErrorType.UNKNOWN


@dataclass
class Receipt:
    """One source of truth for agent/audit/analytics/user."""
    action: str
    target_id: str = ""
    target_type: str = ""
    success: bool = False
    verified: bool = False
    error_type: ErrorType = ErrorType.NONE
    message: str = ""
    request_id: str = field(default_factory=lambda: f"ai_{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"action": self.action, "target_id": self.target_id,
                "success": self.success, "verified": self.verified,
                "error_type": self.error_type.value, "message": self.message[:200],
                "request_id": self.request_id, "timestamp": self.timestamp}


@dataclass
class Observation:
    tool: str
    params: Dict[str, Any]
    success: bool
    verified: bool
    detail: str = ""
    receipt: Optional[Receipt] = None
    timestamp: float = field(default_factory=time.time)

    def render(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        if self.success and not self.verified:
            status = "UNVERIFIED"
        et = f" [{self.receipt.error_type.value}]" if self.receipt else ""
        return f"TOOL {self.tool} -> {status}{et}: {self.detail[:300]}"


@dataclass
class AgentJob:
    job_id: str
    guild_id: int
    user_id: int
    goal: str
    status: JobStatus = JobStatus.PLANNING
    current_step: int = 0
    total_steps: int = 10
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    last_tool: str = ""
    last_result: str = ""

    def snapshot(self) -> dict:
        return {"agent_job_id": self.job_id, "guild_id": self.guild_id,
                "status": self.status.value, "step": f"{self.current_step}/{self.total_steps}",
                "last_tool": self.last_tool}


@dataclass
class AgentExecutionResult:
    actions: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    completed_steps: int = 0
    final_state: JobStatus = JobStatus.PLANNING
    hit_step_limit: bool = False
    loop_detected: bool = False
    job: Optional[AgentJob] = None
    receipts: List[Receipt] = field(default_factory=list)

    def summary_line(self) -> str:
        ok = sum(1 for o in self.observations if o.success and o.verified)
        return f"{ok}/{len(self.observations)} verified"


@dataclass
class FinalAIResponse:
    text: str
    state: JobStatus
    request_id: str = ""
