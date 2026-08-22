"""Post-execution verification against REAL Discord state.

V7 rule: an unknown mutation is never considered verified. The agent must have
positive evidence that the requested Discord state now matches the operation.
"""
from typing import Any, Dict

from core.action_meta import get_meta


class Verifier:
    def __init__(self, bot):
        self.bot = bot

    async def verify(self, guild, name: str, params: Dict[str, Any]) -> bool:
        """Return True only when live Discord state proves the intended result."""
        try:
            meta = get_meta(name)
            operation = meta.get("operation")

            # Queries do not mutate state and therefore do not need mutation
            # verification. They are successful when the executor succeeded.
            if operation == "query":
                return True

            if name == "delete_channel":
                channel_id = params.get("channel_id")
                if not channel_id or not str(channel_id).isdigit():
                    return False
                return guild.get_channel(int(channel_id)) is None

            if name in ("bulk_delete_channels", "cleanup_duplicate_channels"):
                ids = params.get("channel_ids") or params.get("channels") or []
                if not isinstance(ids, list) or not ids:
                    return False
                protected = {
                    str(x) for x in (params.get("protected_channel_ids") or [])
                }
                one = params.get("protected_channel_id")
                if one:
                    protected.add(str(one))
                targets = [str(x) for x in ids if str(x) not in protected]
                if not targets:
                    return False
                return all(
                    (not x.isdigit()) or guild.get_channel(int(x)) is None
                    for x in targets
                )

            if name == "delete_role":
                role_id = params.get("role_id")
                if not role_id or not str(role_id).isdigit():
                    return False
                return guild.get_role(int(role_id)) is None

            if name in ("create_channel", "create_category", "create_shop_channel"):
                channel_id = params.get("channel_id")
                if not channel_id or not str(channel_id).isdigit():
                    return False
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    return False
                wanted = str(params.get("name") or "").strip().lower()
                return not wanted or channel.name.lower() == wanted

            if name in ("rename_channel", "edit_channel") and (
                params.get("new_name") or params.get("name")
            ):
                channel_id = params.get("channel_id")
                new_name = str(params.get("new_name") or params.get("name") or "").strip().lower()
                if not channel_id or not str(channel_id).isdigit() or not new_name:
                    return False
                channel = guild.get_channel(int(channel_id))
                return channel is not None and channel.name.lower() == new_name

            if name == "create_role":
                role_id = params.get("role_id")
                if not role_id or not str(role_id).isdigit():
                    return False
                role = guild.get_role(int(role_id))
                if role is None:
                    return False
                wanted = str(params.get("name") or "").strip().lower()
                return not wanted or role.name.lower() == wanted

            if name == "assign_role":
                user_id = params.get("user_id") or params.get("member_id")
                role_id = params.get("role_id")
                if not user_id or not role_id or not str(user_id).isdigit() or not str(role_id).isdigit():
                    return False
                member = guild.get_member(int(user_id))
                role = guild.get_role(int(role_id))
                return member is not None and role is not None and role in member.roles

            if name == "remove_role":
                user_id = params.get("user_id") or params.get("member_id")
                role_id = params.get("role_id")
                if not user_id or not role_id or not str(user_id).isdigit() or not str(role_id).isdigit():
                    return False
                member = guild.get_member(int(user_id))
                role = guild.get_role(int(role_id))
                return member is not None and role is not None and role not in member.roles

            # V7 fail-closed rule: do not turn an executor's success boolean into
            # a fake verification result for an unimplemented mutation verifier.
            return False
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"verify {name} failed: {e}")
            return False
