import time
from enum import IntEnum
from typing import Optional

from logger import logger


class AccessLevel(IntEnum):
    """Ascending privilege levels used by every Miro panel and command."""
    EVERYONE = 0
    MODERATOR = 10
    STAFF = 20
    ADMIN = 30
    OWNER = 40


class AccessControl:
    """
    Central permission validation for panels, buttons, and commands.
    Resolves a caller's effective level once per interaction, then compares
    against the operation's requirement. Staff/Moderator roles come from the
    relevant system config so servers can delegate without Discord roles.
    """

    def __init__(self):
        self._cache = {}          # (guild_id, user_id) -> (level, expires_at)
        self.cache_ttl = 30       # seconds; role changes apply quickly

    # -- resolution ---------------------------------------------------------

    def resolve_level(self, interaction_or_ctx, system_config: Optional[dict] = None) -> AccessLevel:
        guild = getattr(interaction_or_ctx, "guild", None)
        user = getattr(interaction_or_ctx, "user", None)
        if guild is None or user is None:
            return AccessLevel.EVERYONE
        if guild.owner_id == user.id:
            return AccessLevel.OWNER

        cache_key = (guild.id, user.id)
        cached = self._cache.get(cache_key)
        if cached and cached[1] > time.time():
            return cached[0]

        perms = getattr(user, "guild_permissions", None)
        if perms is not None and perms.administrator:
            level = AccessLevel.ADMIN
        else:
            level = AccessLevel.EVERYONE
            staff_role_ids = set()
            if isinstance(system_config, dict):
                for key in ("staff_roles",):
                    value = system_config.get(key)
                    if isinstance(value, list):
                        staff_role_ids.update(str(r) for r in value)
            member_roles = {str(r.id) for r in getattr(user, "roles", [])}
            if member_roles & staff_role_ids:
                level = AccessLevel.STAFF
            elif perms is not None and (perms.manage_guild or perms.manage_messages or perms.ban_members):
                level = AccessLevel.MODERATOR

        self._cache[cache_key] = (level, time.time() + self.cache_ttl)
        return level

    # -- checking -----------------------------------------------------------

    def check(self, interaction_or_ctx, required: AccessLevel,
              system_config: Optional[dict] = None) -> tuple[bool, str]:
        """
        Returns (allowed, reason). OWNER passes everything below it;
        ADMIN satisfies MODERATOR/STAFF requirements automatically.
        """
        actual = self.resolve_level(interaction_or_ctx, system_config)
        if actual >= required:
            return True, ""
        pretty = {
            AccessLevel.MODERATOR: "Moderator",
            AccessLevel.STAFF: "Staff",
            AccessLevel.ADMIN: "Administrator",
            AccessLevel.OWNER: "Server Owner",
        }
        needed = pretty.get(required, required.name.title())
        have = pretty.get(actual, actual.name.title())
        return False, f"Requires **{needed}** permissions (you are: {have})."

    def invalidate(self, guild_id: int, user_id: int):
        self._cache.pop((guild_id, user_id), None)


# Shared instance owned by the bot (bot.access_control); module-level default
# keeps standalone usage simple.
access_control = AccessControl()
