"""
Universal Resource Manager — safe, idempotent channel/role/message ops.
Every panel Repair/Setup/Test/Open uses this, not ad-hoc helpers.
"""
import discord
from typing import Optional, Dict, Any
from logger import logger


class ResourceManager:
    def __init__(self, bot):
        self.bot = bot

    def resolve_channel(self, guild: discord.Guild, channel_id: Any) -> Optional[discord.abc.GuildChannel]:
        try:
            if not channel_id:
                return None
            return guild.get_channel(int(str(channel_id)))
        except (TypeError, ValueError):
            return None

    def resolve_role(self, guild: discord.Guild, role_id: Any) -> Optional[discord.Role]:
        try:
            if not role_id:
                return None
            return guild.get_role(int(str(role_id)))
        except (TypeError, ValueError):
            return None

    async def find_existing_channel(self, guild: discord.Guild, name: str, kind=discord.ChannelType.text) -> Optional[discord.abc.GuildChannel]:
        low = name.lower().strip().lstrip("#")
        for ch in guild.channels:
            if isinstance(ch, discord.TextChannel) and ch.name.lower() == low:
                return ch
        return None

    async def find_existing_role(self, guild: discord.Guild, name: str) -> Optional[discord.Role]:
        low = name.lower().strip().lstrip("@")
        for r in guild.roles:
            if r.name.lower() == low:
                return r
        return None

    async def create_channel(self, guild: discord.Guild, name: str, channel_type=discord.ChannelType.text, reason="Miro: repair") -> Optional[discord.TextChannel]:
        # reuse existing before create
        existing = await self.find_existing_channel(guild, name)
        if existing:
            return existing
        try:
            # only text channels for panels; extend if needed
            return await guild.create_text_channel(name, reason=reason)
        except Exception as e:
            logger.warning(f"ResourceManager create_channel {name} failed: {e}")
            return None

    async def create_role(self, guild: discord.Guild, name: str, reason="Miro: repair", color: discord.Colour = discord.Colour.blue()) -> Optional[discord.Role]:
        existing = await self.find_existing_role(guild, name)
        if existing:
            return existing
        try:
            return await guild.create_role(name=name, colour=color, reason=reason)
        except Exception as e:
            logger.warning(f"ResourceManager create_role {name} failed: {e}")
            return None

    async def find_panel_message(self, channel: discord.TextChannel, custom_id_prefix: str, limit: int = 30) -> Optional[discord.Message]:
        try:
            async for msg in channel.history(limit=limit):
                if msg.author.id == self.bot.user.id:
                    # view-bearing messages have components; heuristic
                    if msg.components:
                        return msg
            return None
        except Exception:
            return None

    async def post_panel(self, channel: discord.TextChannel, embed: discord.Embed, view: discord.ui.View) -> Optional[discord.Message]:
        # reuse existing panel message if present
        existing = await self.find_panel_message(channel, view.__class__.__name__)
        if existing:
            try:
                await existing.edit(embed=embed, view=view)
                return existing
            except Exception:
                pass
        try:
            return await channel.send(embed=embed, view=view)
        except Exception as e:
            logger.warning(f"post_panel failed: {e}")
            return None

    def channel_health(self, guild: discord.Guild, channel_id: Any) -> Dict[str, Any]:
        ch = self.resolve_channel(guild, channel_id)
        me = guild.me
        return {
            "exists": ch is not None,
            "id": str(channel_id) if channel_id else None,
            "channel": ch,
            "can_view": bool(ch and me and ch.permissions_for(me).view_channel) if ch else False,
            "can_send": bool(ch and me and ch.permissions_for(me).send_messages) if ch else False,
        }

    def role_health(self, guild: discord.Guild, role_id: Any) -> Dict[str, Any]:
        r = self.resolve_role(guild, role_id)
        me = guild.me
        top = me.top_role if me else None
        bot_can_manage = False
        hierarchy_ok = False
        if r and me and top:
            bot_can_manage = guild.me.guild_permissions.manage_roles and r.position < top.position
            hierarchy_ok = r.position < top.position
        return {"exists": r is not None, "id": str(role_id) if role_id else None, "role": r,
                "bot_can_manage": bot_can_manage, "hierarchy_ok": hierarchy_ok}

    async def repair_resource(self, guild: discord.Guild, kind: str, name: str, config: Dict[str, Any], key: str) -> Dict[str, Any]:
        """Repair one resource idempotently; updates config in place and returns report."""
        if kind == "channel":
            ch = self.resolve_channel(guild, config.get(key))
            if ch:
                return {"status": "healthy", "channel": ch}
            # create
            new_ch = await self.create_channel(guild, name)
            if new_ch:
                config[key] = str(new_ch.id)
                return {"status": "created", "channel": new_ch}
            return {"status": "failed"}
        if kind == "role":
            r = self.resolve_role(guild, config.get(key))
            if r:
                return {"status": "healthy", "role": r}
            new_r = await self.create_role(guild, name)
            if new_r:
                config[key] = str(new_r.id)
                return {"status": "created", "role": new_r}
            return {"status": "failed"}
        return {"status": "unknown"}
