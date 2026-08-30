"""Risk Engine, Dry Run, Confirmation Engine — sections 12-15, 39 of MIRO V11."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .contract import DangerLevel, ToolDefinition


class ScopeLimit(str, Enum):
    SINGLE_RESOURCE = "single_resource"
    CHANNEL = "channel"
    CATEGORY = "category"
    ROLE = "role"
    MEMBER = "member"
    SERVER = "server"


@dataclass
class RiskAssessment:
    level: DangerLevel
    factors: List[str] = field(default_factory=list)
    score: int = 0  # 0..100

    def above(self, threshold: DangerLevel) -> bool:
        order = [DangerLevel.LOW, DangerLevel.MEDIUM, DangerLevel.HIGH, DangerLevel.CRITICAL]
        return order.index(self.level) >= order.index(threshold)


class RiskEngine:
    """Calculates risk from tool metadata + runtime context.

    Static factors come from ToolDefinition.danger_level. Runtime factors
    are target_count, irreversibility, and security-impact.
    """

    DESTRUCTIVE_OPS = {
        "delete_channel", "delete_role", "ban_user", "kick_member",
        "bulk_delete_messages", "lock_server", "remove_integration",
    }
    SERVER_WIDE = {"lock_server", "nuke_guild_settings", "bulk_delete_messages"}

    def assess(self, tool: ToolDefinition, *, target_count: int = 1,
               scope: Optional[ScopeLimit] = None) -> RiskAssessment:
        factors: List[str] = []
        score = 0
        level = tool.danger_level

        if tool.mutates_state and level == DangerLevel.LOW:
            level = DangerLevel.MEDIUM
            factors.append("mutates_state with LOW declared level → promoted to MEDIUM")

        if tool.name in self.DESTRUCTIVE_OPS:
            factors.append("destructive op")
            score += 40

        if tool.name in self.SERVER_WIDE:
            factors.append("server-wide effect")
            score += 30

        if not tool.supports_rollback:
            factors.append("irreversible")
            score += 20

        if target_count > 1:
            factors.append(f"target_count={target_count}")
            score += min(40, 10 * (target_count - 1))

        if scope == ScopeLimit.SERVER:
            factors.append("scope=server")
            score += 25

        if "ban_members" in tool.required_permissions or "kick_members" in tool.required_permissions:
            factors.append("moderation impact")
            score += 15

        # Lift the level based on score.
        if score >= 70 and level in (DangerLevel.LOW, DangerLevel.MEDIUM):
            level = DangerLevel.CRITICAL
        elif score >= 50 and level == DangerLevel.LOW:
            level = DangerLevel.HIGH
        elif score >= 30 and level == DangerLevel.LOW:
            level = DangerLevel.MEDIUM

        return RiskAssessment(level=level, factors=factors, score=score)


class ConfirmationDecision:
    AUTO = "auto"
    ASK = "ask"
    EXPLICIT = "explicit"
    NEVER = "never"


class HumanConfirmationPolicy:
    """Maps DangerLevel to a confirmation policy."""

    TABLE = {
        DangerLevel.LOW: ConfirmationDecision.AUTO,
        DangerLevel.MEDIUM: ConfirmationDecision.AUTO,  # if explicitly requested
        DangerLevel.HIGH: ConfirmationDecision.ASK,
        DangerLevel.CRITICAL: ConfirmationDecision.EXPLICIT,
    }

    def decide(self, level: DangerLevel, *, explicit_request: bool = False) -> str:
        decision = self.TABLE[level]
        if level == DangerLevel.MEDIUM and not explicit_request:
            return ConfirmationDecision.ASK
        if decision == ConfirmationDecision.AUTO and not explicit_request and level != DangerLevel.LOW:
            return ConfirmationDecision.ASK
        return decision


class ConfirmationEngine:
    """Tracks pending confirmations and resolves them.

    The ConfigPanel uses this; the AI Agent never sees it directly.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, Dict[str, Any]] = {}

    def request(self, token: str, *, summary: str, preview: Any) -> str:
        self._pending[token] = {"summary": summary, "preview": preview, "resolved": None}
        return token

    def confirm(self, token: str) -> bool:
        rec = self._pending.get(token)
        if not rec:
            return False
        rec["resolved"] = True
        return True

    def cancel(self, token: str) -> bool:
        rec = self._pending.get(token)
        if not rec:
            return False
        rec["resolved"] = False
        return True

    def get(self, token: str) -> Optional[Dict[str, Any]]:
        return self._pending.get(token)


@dataclass
class DryRunReport:
    tool: str
    would_execute: List[Dict[str, Any]] = field(default_factory=list)
    permission_ok: bool = True
    risk: RiskAssessment = field(default_factory=lambda: RiskAssessment(level=DangerLevel.LOW))
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["DRY RUN"]
        lines.append(f"tool: {self.tool}")
        if self.would_execute:
            lines.append(f"would execute {len(self.would_execute)} step(s):")
            for step in self.would_execute:
                lines.append(f"  - {step}")
        else:
            lines.append("no side effects")
        lines.append(f"permission: {'OK' if self.permission_ok else 'BLOCKED'}")
        lines.append(f"risk: {self.risk.level.value} (score {self.risk.score})")
        for f in self.risk.factors:
            lines.append(f"  factor: {f}")
        if self.notes:
            lines.append("notes:")
            for n in self.notes:
                lines.append(f"  - {n}")
        return "\n".join(lines)


class DryRun:
    """Static helper for producing dry-run summaries without executing."""

    @staticmethod
    def report(tool_name: str, *, would_execute: Sequence[Mapping[str, Any]],
               risk: RiskAssessment, permission_ok: bool = True,
               notes: Sequence[str] = ()) -> "DryRunReport":
        return DryRunReport(
            tool=tool_name,
            would_execute=[dict(s) for s in would_execute],
            permission_ok=permission_ok,
            risk=risk,
            notes=list(notes),
        )
