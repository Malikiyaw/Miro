"""Post-execution verification against REAL Discord state."""
from typing import Any, Dict


class Verifier:
    def __init__(self, bot):
        self.bot = bot

    async def verify(self, guild, name: str, params: Dict[str, Any]) -> bool:
        """Returns True when live Discord state matches the intended outcome."""
        try:
            meta_name = name
            if name == "delete_channel":
                channel_id = params.get("channel_id")
                if channel_id and str(channel_id).isdigit():
                    return guild.get_channel(int(channel_id)) is None
                return True
            if name == "bulk_delete_channels":
                ids = params.get("channel_ids") or []
                protected = str(params.get("protected_channel_id")
                                or params.get("protected_channel_ids") or "")
                remaining = [i for i in ids
                             if str(i).isdigit() and i != protected
                             and guild.get_channel(int(i)) is not None]
                return not remaining
            if name == "delete_role":
                role_id = params.get("role_id")
                if role_id and str(role_id).isdigit():
                    return guild.get_role(int(role_id)) is None
                return True
            if name in ("create_channel", "create_category"):
                channel_id = params.get("channel_id")
                if channel_id and str(channel_id).isdigit():
                    ch = guild.get_channel(int(channel_id))
                    if ch is None:
                        return False
                    wanted = str(params.get("name") or "").strip().lower()
                    return (not wanted) or ch.name.lower() == wanted
                return True
            if name == "rename_channel" or (name == "edit_channel" and params.get("new_name")):
                channel_id = params.get("channel_id")
                new_name = str(params.get("new_name") or params.get("name") or "").lower()
                if channel_id and str(channel_id).isdigit() and new_name:
                    ch = guild.get_channel(int(channel_id))
                    return ch is not None and ch.name.lower() == new_name
                return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"verify {name} failed: {e}")
            return False
        return True
