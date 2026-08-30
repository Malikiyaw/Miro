"""CompositeTools, audit_server, repair_system, test_system — sections 41-45."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ToolResult


@dataclass
class CompositeResult:
    composite: str
    steps: List[ToolResult] = field(default_factory=list)
    success: bool = True
    notes: List[str] = field(default_factory=list)

    def add(self, step: ToolResult) -> None:
        self.steps.append(step)
        if not step.success:
            self.success = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite": self.composite,
            "success": self.success,
            "steps": [s.to_dict() for s in self.steps],
            "notes": list(self.notes),
        }


class CompositeTools:
    """High-level Miro capabilities. Each composite calls canonical low-level tools.

    The runtime does the actual work; the composite is a recipe.
    """

    def __init__(self, registry, executor: Optional[Callable[..., ToolResult]] = None) -> None:
        self._registry = registry
        self._executor = executor  # callable(tool_name, params) → ToolResult

    def _call(self, tool_name: str, **params: Any) -> ToolResult:
        if self._executor is None:
            return ToolResult.fail(f"no executor bound for composite {tool_name}",
                                  cls_err=__import__("capability.contract", fromlist=["ErrorClass"]).ErrorClass.UNKNOWN)
        return self._executor(tool_name, params)

    def setup_verification(self, *, channel_name: str = "verify",
                           role_name: str = "Verified",
                           account_age_days: int = 7) -> CompositeResult:
        r = CompositeResult(composite="setup_verification")
        r.add(self._call("create_channel", name=channel_name, kind="text"))
        r.add(self._call("create_role", name=role_name, hoist=True))
        r.add(self._call("send_message", channel=channel_name,
                         content="React with ✅ to verify."))
        r.add(self._call("configure_verification", account_age_days=account_age_days))
        r.notes.append(f"verification system configured with account_age={account_age_days}")
        return r

    def setup_tickets(self, *, category_name: str = "Tickets",
                      channel_name: str = "open-ticket") -> CompositeResult:
        r = CompositeResult(composite="setup_tickets")
        r.add(self._call("create_category", name=category_name))
        r.add(self._call("create_channel", name=channel_name, parent=category_name))
        r.add(self._call("configure_tickets", panel_channel=channel_name))
        r.notes.append("ticket system configured")
        return r

    def setup_staff_system(self) -> CompositeResult:
        r = CompositeResult(composite="setup_staff_system")
        r.add(self._call("create_role", name="Staff"))
        r.add(self._call("create_role", name="Moderator"))
        r.add(self._call("create_role", name="Admin"))
        r.add(self._call("configure_staff_system"))
        r.notes.append("staff system configured")
        return r

    def setup_logging(self, *, channel_name: str = "mod-logs") -> CompositeResult:
        r = CompositeResult(composite="setup_logging")
        r.add(self._call("create_channel", name=channel_name, kind="text"))
        r.add(self._call("configure_logging", log_channel=channel_name))
        r.notes.append("logging configured")
        return r

    def setup_automod(self) -> CompositeResult:
        r = CompositeResult(composite="setup_automod")
        r.add(self._call("configure_automod", enabled=True))
        r.notes.append("automod enabled with default rules")
        return r

    def audit_server(self) -> CompositeResult:
        r = CompositeResult(composite="audit_server")
        r.add(self._call("query_channels"))
        r.add(self._call("query_roles"))
        r.add(self._call("get_server_config"))
        r.notes.append("audit complete; review the receipts for broken resources")
        return r

    def repair_system(self, *, system: str) -> CompositeResult:
        r = CompositeResult(composite=f"repair_system:{system}")
        r.add(self._call(f"configure_{system}"))
        r.notes.append(f"repair of {system} attempted via canonical installer")
        return r

    def test_system(self, *, system: str) -> CompositeResult:
        r = CompositeResult(composite=f"test_system:{system}")
        r.add(self._call("test_system", system=system))
        r.notes.append(f"runtime test of {system} executed")
        return r

    def secure_server(self) -> CompositeResult:
        r = CompositeResult(composite="secure_server")
        r.add(self._call("configure_verification"))
        r.add(self._call("configure_automod"))
        r.add(self._call("configure_logging"))
        r.notes.append("verification + automod + logging applied")
        return r


# Module-level shortcuts for the V11 API.
def audit_server(registry, executor=None) -> CompositeResult:
    return CompositeTools(registry, executor).audit_server()


def repair_system(system: str, registry, executor=None) -> CompositeResult:
    return CompositeTools(registry, executor).repair_system(system=system)


def run_system_test(system: str, registry, executor=None) -> CompositeResult:
    return CompositeTools(registry, executor).test_system(system=system)
