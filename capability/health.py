"""Tool health, versioning, JSON schema validation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass
class ToolVersion:
    tool: str
    version: str
    registered_at: float = field(default_factory=time.time)


@dataclass
class ToolHealth:
    tool: str
    success: int = 0
    failure: int = 0
    retries: int = 0
    total_latency_ms: float = 0.0
    verified: int = 0
    version: str = "1"
    last_used: float = 0.0

    def record(self, *, success: bool, latency_ms: float, verified: bool = False,
               retried: bool = False) -> None:
        if success:
            self.success += 1
        else:
            self.failure += 1
        if verified:
            self.verified += 1
        if retried:
            self.retries += 1
        self.total_latency_ms += latency_ms
        self.last_used = time.time()

    @property
    def calls(self) -> int:
        return self.success + self.failure

    def success_rate(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.success / self.calls

    def verification_rate(self) -> float:
        if self.success == 0:
            return 0.0
        return self.verified / self.success

    def avg_latency_ms(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.total_latency_ms / self.calls

    def snapshot(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "version": self.version,
            "calls": self.calls,
            "success_rate": round(self.success_rate(), 4),
            "verification_rate": round(self.verification_rate(), 4),
            "avg_latency_ms": round(self.avg_latency_ms(), 2),
            "retries": self.retries,
        }


class ToolHealthMonitor:
    def __init__(self) -> None:
        self._health: Dict[str, ToolHealth] = {}
        self._versions: Dict[str, ToolVersion] = {}

    def register(self, tool: str, version: str) -> None:
        self._versions[tool] = ToolVersion(tool=tool, version=version)
        self._health.setdefault(tool, ToolHealth(tool=tool, version=version))

    def record(self, tool: str, *, success: bool, latency_ms: float, verified: bool = False,
               retried: bool = False) -> None:
        h = self._health.setdefault(tool, ToolHealth(tool=tool))
        h.record(success=success, latency_ms=latency_ms, verified=verified, retried=retried)

    def health(self, tool: str) -> Optional[ToolHealth]:
        return self._health.get(tool)

    def report(self) -> List[Dict[str, Any]]:
        return [h.snapshot() for h in self._health.values()]

    def version(self, tool: str) -> Optional[ToolVersion]:
        return self._versions.get(tool)


class JSONSchemaValidator:
    """Lightweight JSON schema validator. We use it to reject obviously
    malformed parameters before they reach Discord.
    """

    @staticmethod
    def validate(parameters: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
        errors: List[str] = []
        for key, spec in (schema or {}).get("properties", {}).items():
            if key in parameters:
                value = parameters[key]
                t = spec.get("type")
                if t == "string" and not isinstance(value, str):
                    errors.append(f"{key}: expected string, got {type(value).__name__}")
                elif t == "integer" and not isinstance(value, int):
                    errors.append(f"{key}: expected integer, got {type(value).__name__}")
                elif t == "number" and not isinstance(value, (int, float)):
                    errors.append(f"{key}: expected number, got {type(value).__name__}")
                elif t == "boolean" and not isinstance(value, bool):
                    errors.append(f"{key}: expected boolean, got {type(value).__name__}")
                elif t == "array" and not isinstance(value, list):
                    errors.append(f"{key}: expected array, got {type(value).__name__}")
                elif t == "object" and not isinstance(value, dict):
                    errors.append(f"{key}: expected object, got {type(value).__name__}")
                if "enum" in spec and value not in spec["enum"]:
                    errors.append(f"{key}: value {value!r} not in {spec['enum']}")
                if "minLength" in spec and isinstance(value, str) and len(value) < spec["minLength"]:
                    errors.append(f"{key}: shorter than minLength={spec['minLength']}")
                if "maxLength" in spec and isinstance(value, str) and len(value) > spec["maxLength"]:
                    errors.append(f"{key}: longer than maxLength={spec['maxLength']}")
                if "minimum" in spec and isinstance(value, (int, float)) and value < spec["minimum"]:
                    errors.append(f"{key}: below minimum={spec['minimum']}")
                if "maximum" in spec and isinstance(value, (int, float)) and value > spec["maximum"]:
                    errors.append(f"{key}: above maximum={spec['maximum']}")
        required = (schema or {}).get("required", [])
        for key in required:
            if key not in parameters:
                errors.append(f"{key}: required")
        # Reject empty arrays for typed-id parameters.
        for key, spec in (schema or {}).get("properties", {}).items():
            if key in parameters and spec.get("type") == "array" and spec.get("minItems", 0) > 0 \
                    and len(parameters[key]) < spec["minItems"]:
                errors.append(f"{key}: minItems={spec['minItems']} violated (got {len(parameters[key])})")
        return errors
