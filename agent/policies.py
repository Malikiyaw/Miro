"""
Agent policies (V6): execution limits, tool tiers, target protection.

Non-negotiables:
- MAX_AGENT_STEPS = 15
- Only transient errors retry; permission/hierarchy violations NEVER do
- Protected targets are hard-rejected server-side, even if the LLM insists
"""
from dataclasses import dataclass, field
from typing import Dict, Set

MAX_AGENT_STEPS = 15
MAX_TOOL_RETRIES = 3
LOOP_SIGNATURE_WINDOW = 3

READ_ONLY_TOOLS: Set[str] = {
    "analyze_server_state", "query_server_info", "query_channels", "query_roles",
    "query_members", "query_member_details", "query_economy_leaderboard",
    "query_xp_leaderboard", "query_pending_applications", "query_active_shifts",
    "query_recent_messages", "find_duplicate_channels",
}
MUTATING_TOOLS: Set[str] = {
    "create_channel", "create_category", "create_role", "edit_channel", "edit_role",
    "send_message", "reply_message", "add_reaction", "send_notification",
    "assign_role", "remove_role", "assign_role_by_name", "create_webhook",
    "connect_systems", "move_system", "bulk_delete_channels",
}
DANGEROUS_TOOLS: Set[str] = {
    "delete_channel", "delete_role", "delete_messages", "bulk_delete_messages",
    "ban_user", "kick_user", "softban_user", "timeout_user", "setup_moderation",
    "cleanup_duplicate_channels",
}

TOOL_TIMEOUTS = {"query": 15.0, "default": 30.0,
                 # Batch deletions: 0.25-0.4s per target + API latency.
                 # 30s killed real cleanups mid-run and discarded receipts.
                 "cleanup_duplicate_channels": 300.0,
                 "bulk_delete_channels": 300.0,
                 "bulk_delete_messages": 120.0}


def tool_timeout(name: str) -> float:
    if name in TOOL_TIMEOUTS:
        return TOOL_TIMEOUTS[name]
    from core.action_meta import get_meta
    if get_meta(name).get("operation") == "query":
        return TOOL_TIMEOUTS["query"]
    return TOOL_TIMEOUTS["default"]


def is_retryable_error(error_text: str) -> bool:
    """Transient only. Permission/hierarchy/parameter violations never retry."""
    low = (error_text or "").lower()
    if any(m in low for m in ("permission", "lacks", "forbidden", "protected",
                              "invalid parameter", "requires", "not found")):
        return False
    return any(m in low for m in ("timeout", "timed out", "rate limit", "429",
                                  "network", "temporarily", "502", "503",
                                  "connection"))


@dataclass
class ProtectionPolicy:
    """Runtime-owned protected targets. Backend-enforced, LLM-agnostic."""
    guild_id: int
    channel_ids: Set[str] = field(default_factory=set)
    role_ids: Set[str] = field(default_factory=set)

    def protect_channel(self, channel_id) -> str:
        cid = str(channel_id)
        self.channel_ids.add(cid)
        return cid

    def is_protected(self, object_type: str, object_id) -> bool:
        bucket = self.channel_ids if object_type == "channel" else \
            self.role_ids if object_type == "role" else set()
        return str(object_id) in bucket


# Per-run protections live on the runtime instance; cross-run/system-level
# protections stay in core.action_meta.protected_targets + system scan.
