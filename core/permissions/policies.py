from typing import FrozenSet

# Actions any source may perform without Administrator (mirrors the
# read-only set enforced in actions.py; single source of truth lives there,
# these policies add the Miro-level layer on top).
PUBLIC_ACTIONS: FrozenSet[str] = frozenset({
    "analyze_server_state", "query_server_info", "query_channels",
    "query_roles", "query_members", "query_member_details",
    "query_economy_leaderboard", "query_xp_leaderboard",
    "query_pending_applications", "query_active_shifts",
    "query_recent_messages", "send_message", "reply_message",
    "add_reaction", "send_notification",
})

# Destructive/irreversible actions that must always be audited loudly.
SENSITIVE_ACTIONS: FrozenSet[str] = frozenset({
    "ban_user", "unban_user", "kick_user", "softban_user", "timeout_user",
    "delete_channel", "delete_role", "delete_messages",
    "create_webhook", "setup_moderation",
})

# Sources that may never run sensitive actions without an admin human
# explicitly initiating them.
NEVER_ESCALATED_SOURCES = {"automod", "scheduled"}

MAX_ACTIONS_PER_REQUEST = 3


class PolicyResult:
    def __init__(self, allowed: bool, reason: str = "", confirm: bool = False):
        self.allowed = allowed
        self.reason = reason
        self.confirm = confirm  # True -> safe to run but should be confirmed/flagged

    def __bool__(self) -> bool:
        return self.allowed
