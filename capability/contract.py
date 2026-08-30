"""Canonical tool contract — section 1 of MIRO V11.

Every Miro action must implement the same ToolDefinition + ToolResult pair.
Nothing enters the registry without both.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class ToolCategory(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    SECURITY = "security"
    AUTOMATION = "automation"
    SYSTEM = "system"
    AGENT = "agent"
    DIAGNOSTIC = "diagnostic"


class DangerLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Idempotency(str, Enum):
    SAFE = "safe"  # call N times = call once, no side effects of repetition
    RESOURCE = "resource"  # keyed by target id, re-target is fine
    UNSAFE = "unsafe"  # every call mutates state


class ErrorClass(str, Enum):
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    INVALID_PARAMS = "invalid_params"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    HIERARCHY_ERROR = "hierarchy_error"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    REPLAN_REQUIRED = "replan_required"
    USER_ACTION_REQUIRED = "user_action_required"


_RETRYABLE_FAILURE = {FailureKind.TRANSIENT}
_REPLAN_FAILURE = {FailureKind.REPLAN_REQUIRED, FailureKind.USER_ACTION_REQUIRED}


@dataclass
class ToolDefinition:
    """Canonical metadata for a Miro capability.

    Fields follow the V11 contract (section 1). `executor` is a dotted
    path that the registry resolves to a callable at registration time.
    `verifier` is optional and defaults to a generic live-state check.
    """

    name: str
    description: str
    category: ToolCategory = ToolCategory.AGENT
    parameters: Mapping[str, Any] = field(default_factory=dict)
    required_permissions: Sequence[str] = field(default_factory=tuple)
    danger_level: DangerLevel = DangerLevel.LOW
    mutates_state: bool = False
    supports_dry_run: bool = False
    supports_rollback: bool = False
    idempotency: Idempotency = Idempotency.UNSAFE
    executor: Optional[str] = None
    verifier: Optional[str] = None
    timeout_seconds: float = 15.0
    retry_policy: str = "TRANSIENT_ONLY"
    audit: bool = True
    keywords: Sequence[str] = field(default_factory=tuple)
    intent_examples: Sequence[str] = field(default_factory=tuple)
    dependencies: Sequence[str] = field(default_factory=tuple)
    version: str = "1"
    scope: str = "single_resource"

    # -- helpers --
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["danger_level"] = self.danger_level.value
        d["idempotency"] = self.idempotency.value
        d["parameters"] = dict(self.parameters)
        d["required_permissions"] = list(self.required_permissions)
        d["keywords"] = list(self.keywords)
        d["intent_examples"] = list(self.intent_examples)
        d["dependencies"] = list(self.dependencies)
        return d

    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "category": self.category.value,
            "version": self.version,
            "danger_level": self.danger_level.value,
            "mutates_state": self.mutates_state,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


@dataclass
class ToolResult:
    """The single shape returned by every capability execution.

    No more raw strings, no more ad-hoc tuples. AI Agent, /configpanel,
    and /autosetup all consume the same shape.
    """

    success: bool
    verified: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    observations: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    error_class: Optional[ErrorClass] = None
    failure_kind: Optional[FailureKind] = None
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    finished_at: float = field(default_factory=time.time)

    def add_observation(self, text: str) -> "ToolResult":
        if text:
            self.observations.append(text)
        return self

    def add_error(self, text: str, *, cls: ErrorClass = ErrorClass.UNKNOWN,
                  kind: FailureKind = FailureKind.PERMANENT) -> "ToolResult":
        if text:
            self.errors.append(text)
        self.error_class = cls
        self.failure_kind = kind
        return self

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["error_class"] = self.error_class.value if self.error_class else None
        d["failure_kind"] = self.failure_kind.value if self.failure_kind else None
        return d

    @classmethod
    def ok(cls, **data: Any) -> "ToolResult":
        return cls(success=True, verified=True, data=dict(data))

    @classmethod
    def fail(cls, message: str, *, cls_err: ErrorClass = ErrorClass.UNKNOWN,
             kind: FailureKind = FailureKind.PERMANENT, **data: Any) -> "ToolResult":
        r = cls(success=False, verified=False, data=dict(data))
        r.add_error(message, cls=cls_err, kind=kind)
        return r


def parameters_hash(parameters: Mapping[str, Any]) -> str:
    """Stable hash of a parameter dict for receipt dedup."""
    payload = json.dumps(dict(parameters), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
