from typing import Dict, List, Optional


class RoleHierarchy:
    """
    Native-Discord-respecting role hierarchy helpers. Wraps (never replaces)
    discord.py's own enforcement with Miro-level pre-checks so AI and custom
    commands fail fast without hitting the API.
    """

    def __init__(self, hierarchy_config: Optional[Dict[str, List[int]]] = None):
        # guild_id -> ordered role ids considered protected (e.g. staff tiers)
        self._protected: Dict[int, List[int]] = dict(hierarchy_config or {})

    def set_protected_roles(self, guild_id: int, role_ids: List[int]):
        self._protected[guild_id] = list(role_ids)

    def get_protected_roles(self, guild_id: int) -> List[int]:
        return list(self._protected.get(guild_id, []))

    @staticmethod
    def can_act_on(actor_position: int, bot_position: int, target_position: Optional[int],
                   actor_is_owner: bool = False) -> bool:
        """Actor must outrank target; the bot must also outrank target to apply changes."""
        if actor_is_owner:
            return True
        if target_position is None:
            return True  # no role target involved
        if target_position >= actor_position and not actor_is_owner:
            return False
        return target_position < max(bot_position, actor_position)

    def is_protected(self, guild_id: int, role_id: int) -> bool:
        return role_id in self._protected.get(guild_id, [])
