"""IntentClassifier + CapabilityDiscovery — sections 2, 4, 40 of MIRO V11.

The AI doesn't see 150 tools. It sees the slice CapabilityDiscovery
returns for the current intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .contract import ToolCategory, ToolDefinition, ToolResult
from .registry import ToolRegistry


_INTENT_KEYWORDS = {
    "delete_duplicate": ["delete", "duplicate", "channel"],
    "make_server_private": ["private", "server", "lock"],
    "audit_security": ["audit", "security", "permissions"],
    "verify_member": ["verify", "member", "role"],
    "send_message": ["send", "message", "post"],
    "create_role": ["create", "role", "add"],
    "list_channels": ["list", "channels", "show"],
    "ban_user": ["ban", "user", "moderation"],
    "kick_user": ["kick", "user", "moderation"],
    "setup_verification": ["setup", "verification"],
    "setup_tickets": ["setup", "tickets"],
    "setup_logging": ["setup", "logging"],
    "setup_automod": ["setup", "automod"],
    "repair_system": ["repair", "system", "fix"],
    "test_system": ["test", "system", "diagnostic"],
}


class IntentClassifier:
    """Heuristic intent classifier used as the front of the discovery pipeline."""

    def classify(self, text: str) -> str:
        if not text:
            return "unknown"
        ql = text.lower()
        best = ("unknown", 0)
        for intent, kws in _INTENT_KEYWORDS.items():
            score = sum(1 for k in kws if k in ql)
            if score > best[1]:
                best = (intent, score)
        return best[0]

    def is_destructive(self, intent: str) -> bool:
        return any(k in intent for k in ("delete", "ban", "kick", "lock", "remove"))

    def is_multi_step(self, intent: str) -> bool:
        return intent.startswith("setup_") or intent in {"delete_duplicate", "make_server_private",
                                                        "secure_server", "audit_security"}


@dataclass
class DiscoverySlice:
    intent: str
    primary: List[ToolDefinition]
    helpers: List[ToolDefinition]
    verifiers: List[ToolDefinition]

    def all(self) -> List[ToolDefinition]:
        # Deduplicate by name, preserving order.
        seen: Set[str] = set()
        out: List[ToolDefinition] = []
        for t in self.primary + self.helpers + self.verifiers:
            if t.name not in seen:
                out.append(t)
                seen.add(t.name)
        return out


class CapabilityDiscovery:
    """Maps intent → tool slice (primary + helpers + verifiers)."""

    INTENTS = {
        "delete_duplicate": (("find_duplicate_channels", "delete_channel"), ("get_channel",), ("verify_channel_deleted",)),
        "make_server_private": (("configure_verification", "configure_automod", "set_channel_permissions"),
                                ("query_channels", "get_server_config"), ("verify_channel_permissions",)),
        "audit_security": (("audit_server",), ("get_server_config", "query_channels", "query_roles"),
                          ("verify_role_hierarchy",)),
        "verify_member": (("assign_role", "get_member", "resolve_member"), ("resolve_role",), ("verify_member_role",)),
        "send_message": (("send_message",), ("query_channels",), ("verify_message_exists",)),
        "create_role": (("create_role",), ("query_roles",), ("verify_role_exists",)),
        "list_channels": (("query_channels", "get_channel"), (), ()),
        "ban_user": (("ban_user",), ("get_member",), ("verify_member_banned",)),
        "kick_user": (("kick_member",), ("get_member",), ("verify_member_kicked",)),
        "setup_verification": (("setup_verification", "configure_verification", "create_channel", "create_role", "send_message"),
                                (), ()),
        "setup_tickets": (("setup_tickets", "configure_tickets", "create_category", "create_channel"), (), ()),
        "setup_logging": (("setup_logging", "configure_logging", "create_channel"), (), ()),
        "setup_automod": (("setup_automod", "configure_automod"), (), ()),
        "repair_system": (("repair_system",), ("audit_server",), ()),
        "test_system": (("test_system",), ("get_server_config",), ()),
    }

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def discover(self, intent: str, *, raw: Optional[str] = None) -> DiscoverySlice:
        classifier = IntentClassifier()
        detected = classifier.classify(raw or intent)
        primary, helpers, verifiers = self.INTENTS.get(detected, ((), (), ()))
        return DiscoverySlice(
            intent=detected,
            primary=self._safe(primary),
            helpers=self._safe(helpers),
            verifiers=self._safe(verifiers),
        )

    def _safe(self, names: Sequence[str]) -> List[ToolDefinition]:
        out: List[ToolDefinition] = []
        for n in names:
            try:
                out.append(self._registry.get(n))
            except KeyError:
                continue
        return out
