"""
Server vision: gives the agent EYES on the live Discord server.

Before an actionable run, the runtime injects a concise SERVER STATE block
(real data via ServerQueryEngine) so the model knows what it is operating on
— server name, member count, channels with IDs, roles — instead of guessing.
"""
from typing import Optional, Any, Dict


async def build_server_context(bot, guild) -> str:
    """Concise live snapshot. Truncated lists keep prompts fast."""
    if guild is None:
        return ""
    engine = getattr(bot, "server_query", None)
    lines = [f"SERVER STATE (live): name={guild.name} id={guild.id}"]

    info = {}
    if engine is not None and hasattr(engine, "query_server_info"):
        try:
            info = await engine.query_server_info(guild.id) or {}
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"server_info query failed: {e}")
    if isinstance(info, dict) and info:
        for key in ("member_count", "owner_id", "created_at"):
            if info.get(key) is not None:
                lines.append(f"  {key}: {info[key]}")

    channels = []
    if engine is not None and hasattr(engine, "query_channels"):
        try:
            channels = await engine.query_channels(guild.id) or []
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"channels query failed: {e}")
    if not channels:  # direct fallback from cache
        channels = [{"id": c.id, "name": c.name, "type": str(getattr(c, "type", ""))}
                    for c in list(guild.text_channels)[:60]]
    if isinstance(channels, list) and channels:
        shown = 0
        for c in channels[:40]:
            if not isinstance(c, dict):
                continue
            lines.append(f"  #{c.get('name','?')} (id={c.get('id')})")
            shown += 1
            if shown >= 40:
                lines.append(f"  … {len(channels)-shown} more channels")
                break

    roles = []
    if engine is not None and hasattr(engine, "query_roles"):
        try:
            roles = await engine.query_roles(guild.id) or []
        except Exception:
            pass
    if isinstance(roles, list) and roles:
        names = []
        for r in roles[:15]:
            if isinstance(r, dict):
                names.append(str(r.get("name") or r.get("id")))
        lines.append(f"  roles({len(roles)}): {', '.join(names)}")

    return "\n".join(lines)


async def build_automation_context(bot, guild) -> str:
    """Give the agent EYES on the server's EXISTING automations and the full
    trigger/action vocabulary, so it can (a) avoid creating duplicates and
    (b) know exactly what can be automated.

    Injected into the planner context alongside ``build_server_context``.
    """
    if guild is None:
        return ""
    lines = ["AUTOMATION CONTEXT (live):"]

    # 1) Catalog of what the bot CAN automate (triggers + actions). This is the
    #    vocabulary the agent reasons over — never guess beyond this list.
    try:
        from agent.automation_knowledge import render_catalog
        lines.append(render_catalog())
    except Exception:
        pass

    # 2) What already exists on THIS server, so the agent merges/updates instead
    #    of blindly duplicating. (Avoids the #1 reported failure: duplicates.)
    try:
        dm = getattr(bot, "action_handler", None)
        get_autos = None
        if dm is not None and hasattr(dm, "_get_automations_dict"):
            get_autos = dm._get_automations_dict
        else:
            from data_manager import dm as _dm
            get_autos = lambda gid: _dm.get_guild_data(gid, "automations", {}) or {}
        autos = get_autos(guild.id) or {}
        if autos:
            lines.append(f"EXISTING AUTOMATIONS on this server ({len(autos)}):")
            for name, entry in list(autos.items())[:40]:
                if not isinstance(entry, dict):
                    continue
                atype = entry.get("type", "?")
                detail = ""
                if atype == "scheduled_task":
                    detail = f"cron={entry.get('cron')} channel={entry.get('channel_id')}"
                elif atype in ("auto_responder", "trigger_role"):
                    detail = f"keywords={entry.get('triggers') or entry.get('keywords')}"
                elif atype == "reminder":
                    detail = f"duration={entry.get('duration')}s"
                elif atype == "event_trigger":
                    detail = f"event={entry.get('event')}"
                # 1000x: show paused / fail state so agent avoids broken ones
                if entry.get("paused"):
                    detail += " [PAUSED]"
                if entry.get("fail_count"):
                    detail += f" fails={entry.get('fail_count')}"
                lines.append(f"  - {name} [{atype}] {detail}".rstrip())
            if len(autos) > 40:
                lines.append(f"  … and {len(autos) - 40} more")
        else:
            lines.append("EXISTING AUTOMATIONS: none yet — you may create the first.")

        # Quotas 1000x: so agent knows when to use bulk vs update/delete
        try:
            from modules.automation_manager import MAX_AUTOMATIONS_PER_GUILD, MAX_COMMANDS_PER_GUILD
            a_count = len(autos) if isinstance(autos, dict) else 0
            # need cmds count as well (computed below)
            from data_manager import dm as _dm_q
            _cmds_q = _dm_q.get_guild_data(guild.id, "custom_commands", {}) or {}
            c_count = len(_cmds_q) if isinstance(_cmds_q, dict) else 0
            lines.append(f"QUOTAS: automations {a_count}/{MAX_AUTOMATIONS_PER_GUILD}, prefix commands {c_count}/{MAX_COMMANDS_PER_GUILD}, bulk 25 per call (auto-pause after 10 fails)")
        except Exception:
            pass

        # Existing custom prefix commands (so the agent doesn't recreate them)
        cmds = None
        if dm is not None and hasattr(dm, "_get_param"):
            pass
        try:
            from data_manager import dm as _dm2
            cmds = _dm2.get_guild_data(guild.id, "custom_commands", {}) or {}
        except Exception:
            cmds = {}
        if cmds:
            names = ", ".join(list(cmds.keys())[:30])
            lines.append(f"EXISTING PREFIX COMMANDS: {names} ({len(cmds)} total)")
        else:
            lines.append("EXISTING PREFIX COMMANDS: none yet")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"automation context build failed: {e}")

    lines.append(
        "RULE: If an existing automation already does what the user wants (same "
        "trigger + similar action), UPDATE it with update_automation instead of "
        "creating a duplicate. If unsure what exists, call list_automations first."
    )
    return "\n".join(lines)


def build_actor_context(*, guild=None, user=None, channel=None, message=None) -> str:
    """Compact "who is asking + what channel + what message triggered this run".

    Injected into the planner context so the model knows the actor and the
    triggering message without guessing. The runtime ALSO uses this same
    object to auto-enrich tool parameters (e.g. add_reaction defaults to the
    most recent message in this channel when the LLM omits message_id).
    """
    if guild is None and user is None and channel is None and message is None:
        return ""

    def _id(o):
        return getattr(o, "id", None) or (o.get("id") if isinstance(o, dict) else None)

    def _name(o):
        return getattr(o, "name", None) or (o.get("name") if isinstance(o, dict) else "")

    lines: list[str] = ["ACTOR CONTEXT (live):"]
    if user is not None:
        lines.append(f"  user: id={_id(user)} name={_name(user) or ''}")
    if guild is not None:
        lines.append(f"  guild: id={_id(guild)} name={_name(guild) or ''}")
    if channel is not None:
        cid = _id(channel)
        cname = _name(channel)
        if cid is not None:
            lines.append(f"  channel: id={cid} name={cname or ''} (use channel_id={cid} to target the current channel)")
    if message is not None:
        mid = _id(message)
        if mid is not None:
            author = getattr(message, "author", None)
            author_id = _id(author) if author is not None else None
            lines.append(
                f"  message: id={mid} author_id={author_id} "
                f"(use message_id={mid} to react/edit/delete THIS message; "
                f"or omit message_id to react to the most recent message in channel_id)"
            )
    return "\n".join(lines)


def resolve_default_target_message(*, channel, message=None):
    """Pick the message the LLM most likely meant when it didn't say.

    Priority: the triggering message (if any) → the most recent message in
    the channel. Returns (channel, message_id) or (None, None).
    """
    if message is not None and getattr(message, "id", None):
        return channel, int(getattr(message, "id"))
    if channel is None:
        return None, None
    return channel, None  # caller will fetch latest.
