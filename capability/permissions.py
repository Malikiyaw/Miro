"""Permission preflight — sections 8-11 of MIRO V11.

Before every mutation: bot permissions, channel overwrites, role
hierarchy, target permissions. Produce an explanatory PermissionCheck,
not just "403".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


PERMISSION_FLAGS = {
    "manage_channels": 1 << 3,
    "manage_roles": 1 << 28,
    "manage_guild": 1 << 5,
    "manage_messages": 1 << 13,
    "manage_threads": 1 << 34,
    "kick_members": 1 << 1,
    "ban_members": 1 << 2,
    "moderate_members": 1 << 40,
    "administrator": 1 << 3,
    "send_messages": 1 << 11,
    "view_channel": 1 << 10,
    "read_message_history": 1 << 16,
    "mention_everyone": 1 << 17,
    "manage_webhooks": 1 << 29,
    "manage_emojis": 1 << 30,
}


@dataclass
class PermissionCheck:
    allowed: bool
    missing: List[str] = field(default_factory=list)
    hierarchy_valid: bool = True
    explanation: str = ""
    target: str = ""
    tool: str = ""

    def explain(self) -> str:
        if self.allowed:
            return self.explanation or "All required permissions present."
        if not self.hierarchy_valid:
            return (f"Bot cannot {self.tool} {self.target}. "
                    "Role hierarchy blocks this action.")
        return (f"Bot cannot {self.tool} {self.target}. "
                f"Missing: {', '.join(self.missing) or 'unknown'}. "
                f"Required by: {self.tool}. No action was executed.")


class PermissionPreflight:
    """Generic, testable permission preflight.

    Real Discord adapter plugs in by populating bot_perms, target_perms,
    and a hierarchy oracle. Tests can drive it directly.
    """

    def __init__(self) -> None:
        self._bot_perms: Dict[str, Set[str]] = {}
        self._member_perms: Dict[str, Set[str]] = {}
        self._hierarchy: Dict[str, List[str]] = {}  # guild → ordered role ids (lowest→highest)

    def set_bot_permissions(self, guild_id: str, perms: Iterable[str]) -> None:
        self._bot_perms[str(guild_id)] = set(perms)

    def set_member_permissions(self, guild_id: str, perms: Iterable[str]) -> None:
        self._member_perms[str(guild_id)] = set(perms)

    def set_role_hierarchy(self, guild_id: str, ordered_role_ids: Sequence[str]) -> None:
        self._hierarchy[str(guild_id)] = [str(r) for r in ordered_role_ids]

    def check(
        self,
        *,
        guild_id: str,
        tool: str,
        target: str,
        required: Sequence[str],
    ) -> PermissionCheck:
        bot = self._bot_perms.get(str(guild_id), set())
        missing = [p for p in required if p not in bot and "administrator" not in bot]
        if missing:
            return PermissionCheck(
                allowed=False,
                missing=missing,
                hierarchy_valid=True,
                target=target,
                tool=tool,
            )
        return PermissionCheck(allowed=True, target=target, tool=tool,
                                explanation="all required permissions present")

    def check_hierarchy(self, guild_id: str, bot_top_role_id: str,
                        target_role_id: str) -> bool:
        order = self._hierarchy.get(str(guild_id), [])
        if not order:
            return True
        try:
            return order.index(str(bot_top_role_id)) > order.index(str(target_role_id))
        except ValueError:
            return True


class RoleHierarchyEngine:
    """Order role ids highest-first; validate that the bot's top role
    strictly outranks the target role.

    In production this queries Discord live; the in-memory implementation
    is a deterministic oracle for the test suite.
    """

    def __init__(self) -> None:
        self._order: Dict[str, List[str]] = {}

    def set_order(self, guild_id: str, ordered_role_ids: Sequence[str]) -> None:
        # Caller passes highest → lowest; we keep that direction.
        self._order[str(guild_id)] = [str(r) for r in ordered_role_ids]

    def bot_outranks(self, guild_id: str, bot_role_id: str, target_role_id: str) -> bool:
        order = self._order.get(str(guild_id), [])
        try:
            return order.index(str(bot_role_id)) < order.index(str(target_role_id))
        except ValueError:
            return False

    def effective_position(self, guild_id: str, role_id: str) -> int:
        order = self._order.get(str(guild_id), [])
        try:
            return order.index(str(role_id))
        except ValueError:
            return -1


class ChannelPermissionEngine:
    """Effective permissions = guild ⊕ role overwrites ⊕ member overwrites.

    Bitwise, so the test suite can compose exact cases without Discord.
    """

    def __init__(self) -> None:
        self._guild_perms: Dict[str, int] = {}
        self._role_overwrites: Dict[Tuple[str, str], int] = {}  # (channel, role) → bits
        self._member_overwrites: Dict[Tuple[str, str], int] = {}

    def set_guild(self, guild_id: str, perms_value: int) -> None:
        self._guild_perms[str(guild_id)] = perms_value & 0xFFFFFFFF

    def set_role_overwrite(self, channel_id: str, role_id: str, *, allow: int = 0, deny: int = 0) -> None:
        cur = self._role_overwrites.get((channel_id, role_id), 0)
        self._role_overwrites[(channel_id, role_id)] = (cur | allow) & ~deny & 0xFFFFFFFF

    def set_member_overwrite(self, channel_id: str, member_id: str, *, allow: int = 0, deny: int = 0) -> None:
        cur = self._member_overwrites.get((channel_id, member_id), 0)
        self._member_overwrites[(channel_id, member_id)] = (cur | allow) & ~deny & 0xFFFFFFFF

    def effective(self, guild_id: str, channel_id: str, member_id: str,
                  member_role_ids: Sequence[str]) -> int:
        perms = self._guild_perms.get(str(guild_id), 0)
        for rid in member_role_ids:
            perms = (perms | self._role_overwrites.get((channel_id, rid), 0)) & 0xFFFFFFFF
        perms = (perms | self._member_overwrites.get((channel_id, member_id), 0)) & 0xFFFFFFFF
        return perms

    def can(self, perms: int, flag: str) -> bool:
        bit = PERMISSION_FLAGS.get(flag)
        if bit is None:
            return False
        return (perms & bit) == bit
