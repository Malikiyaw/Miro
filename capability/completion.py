"""CompletionGate — section 49 of MIRO V11.

The agent cannot say `done=true`. The backend calculates it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ToolResult
from .bulk import BulkResult, BulkOutcome


@dataclass
class CompletionCheck:
    goal_satisfied: bool
    required_actions_verified: bool
    no_unresolved_failures: bool

    @property
    def completed(self) -> bool:
        return (self.goal_satisfied
                and self.required_actions_verified
                and self.no_unresolved_failures)


class CompletionGate:
    """Single source of truth for "is the job done"."""

    def __init__(self) -> None:
        self._required_tools: List[str] = []
        self._required_verified: int = 0
        self._unresolved: int = 0
        self._goal_satisfied: bool = False

    def require(self, tool: str) -> None:
        self._required_tools.append(tool)

    def record_outcome(self, result: ToolResult) -> None:
        if not result.success:
            self._unresolved += 1
        if result.success and result.verified:
            self._required_verified += 1
            self._goal_satisfied = True

    def record_bulk(self, result: BulkResult) -> None:
        if result.outcome == BulkOutcome.COMPLETE:
            self._required_verified += result.verified
            self._goal_satisfied = True
        else:
            self._unresolved += result.failed

    def mark_goal(self, satisfied: bool) -> None:
        self._goal_satisfied = bool(satisfied)

    def check(self) -> CompletionCheck:
        return CompletionCheck(
            goal_satisfied=self._goal_satisfied,
            required_actions_verified=self._required_verified >= len(self._required_tools)
                                       and len(self._required_tools) > 0,
            no_unresolved_failures=self._unresolved == 0,
        )
