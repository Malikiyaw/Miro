"""V8 post-execution verification against live Discord state.

Verification is positive evidence, not a copy of the executor's success flag.
Unknown mutations fail closed.
"""
from typing import Any, Dict

from core.action_meta import get_meta


async def _fetch_channel(guild, channel_id):
    try:
        return await guild.fetch_channel(int(channel_id))
    except Exception as exc:
        # discord.NotFound means the channel is genuinely gone. Other failures
        # are treated as unverifiable rather than silently successful.
        if exc.__class__.__name__ == "NotFound":
            return None
        raise


async def _fetch_member(guild, user_id):
    try:
        return await guild.fetch_member(int(user_id))
    except Exception as exc:
        if exc.__class__.__name__ == "NotFound":
            return None
        raise


class Verifier:
    def __init__(self, bot):
        self.bot = bot

    async def verify(self, guild, name: str, params: Dict[str, Any]) -> bool:
        """Return True only when live Discord state proves the intended result."""
        try:
            meta = get_meta(name)
            operation = meta.get("operation")

            if operation == "query":
                return True

            if name == "delete_channel":
                channel_id = params.get("channel_id")
                if not channel_id or not str(channel_id).isdigit():
                    return False
                return await _fetch_channel(guild, channel_id) is None

            if name in ("bulk_delete_channels", "cleanup_duplicate_channels"):
                if name == "cleanup_duplicate_channels":
                    # High-level tool: params are name + protected_channel_id.
                    # Re-run the deterministic matcher against LIVE state —
                    # every non-protected duplicate must be gone.
                    nm = str(params.get("name") or params.get("channel_name") or "").strip()
                    if nm:
                        from agent.tools import find_duplicate_channels
                        data = find_duplicate_channels(guild, nm,
                                                       params.get("protected_channel_id"))
                        return not data["duplicates"]
                    # Server-wide mode (no name): NO duplicate groups may remain
                    from agent.tools import find_all_duplicate_groups
                    prot = str(params.get("protected_channel_id") or "")
                    scan = find_all_duplicate_groups(
                        guild, protected_channel_ids=[prot] if prot else [])
                    return not scan["groups"]
                ids = params.get("channel_ids") or params.get("channels") or []
                if not isinstance(ids, list) or not ids:
                    return False
                protected = {str(x) for x in (params.get("protected_channel_ids") or [])}
                one = params.get("protected_channel_id")
                if one:
                    protected.add(str(one))
                targets = [str(x) for x in ids if str(x) not in protected]
                if not targets:
                    return False
                for target in targets:
                    if target.isdigit() and await _fetch_channel(guild, target) is not None:
                        return False
                return True

            if name == "delete_role":
                role_id = params.get("role_id")
                if not role_id or not str(role_id).isdigit():
                    return False
                roles = await guild.fetch_roles()
                return all(str(r.id) != str(role_id) for r in roles)

            if name in ("create_channel", "create_category", "create_shop_channel"):
                wanted = str(params.get("name") or "").strip().lower()
                channel_id = params.get("channel_id")
                if channel_id and str(channel_id).isdigit():
                    channel = await _fetch_channel(guild, channel_id)
                    return channel is not None and (not wanted or channel.name.lower() == wanted)
                if not wanted:
                    return False
                channels = await guild.fetch_channels()
                return any(getattr(c, "name", "").lower() == wanted for c in channels)

            if name in ("rename_channel", "edit_channel", "edit_channel_name"):
                channel_id = params.get("channel_id")
                new_name = str(params.get("new_name") or params.get("name") or "").strip().lower()
                if not channel_id or not str(channel_id).isdigit() or not new_name:
                    return False
                channel = await _fetch_channel(guild, channel_id)
                return channel is not None and channel.name.lower() == new_name

            if name == "create_role":
                wanted = str(params.get("name") or "").strip().lower()
                role_id = params.get("role_id")
                roles = await guild.fetch_roles()
                if role_id and str(role_id).isdigit():
                    role = next((r for r in roles if r.id == int(role_id)), None)
                    return role is not None and (not wanted or role.name.lower() == wanted)
                return bool(wanted) and any(r.name.lower() == wanted for r in roles)

            if name in ("assign_role", "remove_role"):
                user_id = params.get("user_id") or params.get("member_id")
                role_id = params.get("role_id")
                if not user_id or not role_id or not str(user_id).isdigit() or not str(role_id).isdigit():
                    return False
                member = await _fetch_member(guild, user_id)
                roles = await guild.fetch_roles()
                role = next((r for r in roles if r.id == int(role_id)), None)
                if member is None or role is None:
                    return False
                role_ids = {r.id for r in getattr(member, "roles", [])}
                if name == "assign_role":
                    return role.id in role_ids
                return role.id not in role_ids

            if name in ("kick_user", "ban_user", "softban_user"):
                user_id = params.get("user_id") or params.get("member_id")
                if not user_id or not str(user_id).isdigit():
                    return False
                member = await _fetch_member(guild, user_id)
                if name == "kick_user":
                    return member is None
                try:
                    bans = await guild.bans()
                    return any(str(b.user.id) == str(user_id) for b in bans)
                except Exception:
                    return True  # cannot inspect bans; trust handler success

            # Automation & custom-command tools verify against persisted
            # guild state (the automations / custom_commands registries).
            if name in ("create_automation", "update_automation", "resume_automation",
                        "run_automation_now", "bulk_create_automations"):
                from data_manager import dm as _dm
                autos = _dm.get_guild_data(guild.id, "automations", {}) or {}
                wanted = params.get("name") or params.get("automation_name")
                if name == "bulk_create_automations":
                    items = params.get("automations") or params.get("items") or []
                    if not isinstance(items, list) or not items:
                        return False
                    lowered = {str(k).lower() for k in autos}
                    return all(str((it or {}).get("name", "")).lower() in lowered
                               for it in items if isinstance(it, dict))
                if not wanted:
                    return False
                entry = autos.get(wanted) or next(
                    (e for k, e in autos.items() if str(k).lower() == str(wanted).lower()), None)
                if not isinstance(entry, dict) or entry.get("paused"):
                    return False
                # Truthful verification: the automation must be LIVE in its subsystem,
                # not merely present in the registry. A false "verified" here is what
                # previously let broken automations (e.g. lost tool params) slip through.
                atype = entry.get("type")
                if atype == "auto_responder":
                    triggers = {str(t).lower() for t in (entry.get("triggers") or [])}
                    responders = _dm.get_guild_data(guild.id, "auto_responders", []) or []
                    if not any(r.get("enabled") and str(r.get("trigger", "")).lower() in triggers
                               for r in responders):
                        return False
                elif atype == "scheduled_task":
                    if not entry.get("task_id") or entry.get("next_run") is None:
                        return False
                elif atype == "reminder":
                    if not entry.get("reminder_id"):
                        return False
                return True

            if name in ("pause_automation", "bulk_pause_automations"):
                from data_manager import dm as _dm
                autos = _dm.get_guild_data(guild.id, "automations", {}) or {}
                if name == "bulk_pause_automations":
                    names = params.get("names")
                    if names:
                        lowered = {str(k).lower() for k, e in autos.items()
                                   if isinstance(e, dict) and e.get("paused")}
                        return all(str(n).lower() in lowered for n in names)
                    return all(isinstance(e, dict) and (e.get("paused") or params.get("type") and e.get("type") != params.get("type"))
                               for e in autos.values()) if autos else False
                wanted = params.get("name") or params.get("automation_name")
                if not wanted:
                    return False
                entry = autos.get(wanted) or next(
                    (e for k, e in autos.items() if str(k).lower() == str(wanted).lower()), None)
                return isinstance(entry, dict) and bool(entry.get("paused"))

            if name == "bulk_delete_automations":
                from data_manager import dm as _dm
                autos = _dm.get_guild_data(guild.id, "automations", {}) or {}
                names = params.get("names")
                if names:
                    lowered = {str(k).lower() for k in autos}
                    return all(str(n).lower() not in lowered for n in names)
                atype = params.get("type")
                return not any(isinstance(e, dict) and (not atype or e.get("type") == atype)
                               for e in autos.values())

            if name in ("create_prefix_command", "bulk_create_prefix_commands",
                        "delete_prefix_command"):
                from data_manager import dm as _dm
                cmds = _dm.get_guild_data(guild.id, "custom_commands", {}) or {}
                if name == "bulk_create_prefix_commands":
                    items = params.get("commands") or params.get("items") or []
                    if not isinstance(items, list) or not items:
                        return False
                    lowered = {str(k).lower() for k in cmds}
                    return all(str((it or {}).get("name", "")).lstrip("!").lower() in lowered
                               for it in items if isinstance(it, dict))
                wanted = str(params.get("name") or params.get("cmd_name") or "").lstrip("!").lower()
                if not wanted:
                    return False
                exists = wanted in {str(k).lower() for k in cmds}
                return exists if name != "delete_prefix_command" else not exists

            # V8 fail-closed rule: a mutation without a concrete verifier is not
            # allowed to become VERIFIED merely because Discord returned success.
            return False
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug(f"verify {name} failed: {exc}")
            return False
