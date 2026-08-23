"""
Server vision: gives the agent EYES on the live Discord server.

Before an actionable run, the runtime injects a concise SERVER STATE block
(real data via ServerQueryEngine) so the model knows what it is operating on
— server name, member count, channels with IDs, roles — instead of guessing.
"""
from typing import Optional


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
