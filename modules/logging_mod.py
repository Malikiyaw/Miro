import discord
from discord.ext import commands
import time
import asyncio
from typing import Dict, List, Optional, Any, Union
from data_manager import dm
from logger import logger

class LoggingSystem:
    """
    General Logging System:
    Covers ALL server events (Messages, Members, Channels, Roles, Voice, etc.)
    Distinct from moderation logging.
    """
    def __init__(self, bot):
        self.bot = bot
        self._paused_until = {} # guild_id -> timestamp

    def get_config(self, guild_id: int) -> Dict[str, Any]:
        config = dm.get_guild_data(guild_id, "logging_config", {
            "enabled": True,
            "log_channel_id": None,
            "category_channels": {}, # event_type -> channel_id
            "enabled_events": {
                "message_edit": True,
                "message_delete": True,
                "member_join": True,
                "member_leave": True,
                "voice_state": True,
                "channel_update": True,
                "role_update": True,
                "server_update": True,
                "invite_update": True,
                "thread_update": True
            },
            "ignored_channels": [],
            "ignored_roles": [],
            "ignored_users": []
        })
        # Ensure ignored_users is a list
        if "ignored_users" not in config: config["ignored_users"] = []
        return config

    def save_config(self, guild_id: int, config: Dict[str, Any]):
        dm.update_guild_data(guild_id, "logging_config", config)

    def is_paused(self, guild_id: int) -> bool:
        until = self._paused_until.get(guild_id, 0)
        return time.time() < until

    async def _send_log(self, guild: discord.Guild, event_type: str, embed: discord.Embed):
        if self.is_paused(guild.id):
            return

        config = self.get_config(guild.id)
        if not config.get("enabled", True):
            return

        if not config.setdefault("enabled_events", {}).get(event_type, True):
            return

        # Check category-specific channel
        channel_id = config.get("category_channels", {}).get(event_type)
        if not channel_id:
            channel_id = config.get("log_channel_id")

        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))
        if not channel:
            return

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send log to channel {channel_id} in guild {guild.id}: {e}")

    # Event Handlers

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild: return
        if before.content == after.content: return

        config = self.get_config(before.guild.id)
        if before.channel.id in config.get("ignored_channels", []): return
        if any(r.id in config.get("ignored_roles", []) for r in before.author.roles): return
        if before.author.id in config.get("ignored_users", []): return

        embed = discord.Embed(
            title="📝 Message Edited",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=before.author.display_name, icon_url=before.author.display_avatar.url)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="User", value=before.author.mention, inline=True)
        embed.add_field(name="Before", value=before.content[:1024] or "_No content_", inline=False)
        embed.add_field(name="After", value=after.content[:1024] or "_No content_", inline=False)
        embed.set_footer(text=f"User ID: {before.author.id}")

        await self._send_log(before.guild, "message_edit", embed)

    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild: return

        config = self.get_config(message.guild.id)
        if message.channel.id in config.get("ignored_channels", []): return
        if any(r.id in config.get("ignored_roles", []) for r in message.author.roles): return
        if message.author.id in config.get("ignored_users", []): return

        embed = discord.Embed(
            title="🗑️ Message Deleted",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="User", value=message.author.mention, inline=True)
        embed.add_field(name="Content", value=message.content[:1024] or "_No content_", inline=False)
        if message.attachments:
            embed.add_field(name="Attachments", value=f"{len(message.attachments)} files", inline=True)
        embed.set_footer(text=f"User ID: {message.author.id}")

        await self._send_log(message.guild, "message_delete", embed)

    async def on_member_join(self, member: discord.Member):
        config = self.get_config(member.guild.id)
        if member.id in config.get("ignored_users", []): return

        embed = discord.Embed(
            title="📥 Member Joined",
            description=f"{member.mention} joined the server.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.set_footer(text=f"User ID: {member.id}")

        await self._send_log(member.guild, "member_join", embed)

    async def on_member_remove(self, member: discord.Member):
        config = self.get_config(member.guild.id)
        if member.id in config.get("ignored_users", []): return

        embed = discord.Embed(
            title="📤 Member Left",
            description=f"{member.mention} left the server.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles[:10]), inline=False)
        embed.set_footer(text=f"User ID: {member.id}")

        await self._send_log(member.guild, "member_leave", embed)

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        config = self.get_config(after.guild.id)
        if after.id in config.get("ignored_users", []): return
        if any(r.id in config.get("ignored_roles", []) for r in after.roles): return

        if before.display_name != after.display_name:
            embed = discord.Embed(title="🎭 Nickname Changed", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            embed.set_author(name=after.display_name, icon_url=after.display_avatar.url)
            embed.add_field(name="Before", value=before.display_name, inline=True)
            embed.add_field(name="After", value=after.display_name, inline=True)
            await self._send_log(after.guild, "member_update", embed)

        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]
            if added or removed:
                embed = discord.Embed(title="🎭 Roles Updated", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
                embed.set_author(name=after.display_name, icon_url=after.display_avatar.url)
                if added: embed.add_field(name="Added", value=", ".join(added), inline=False)
                if removed: embed.add_field(name="Removed", value=", ".join(removed), inline=False)
                await self._send_log(after.guild, "member_update", embed)

        if before.display_avatar != after.display_avatar:
            embed = discord.Embed(title="🎭 Avatar Changed", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            embed.set_author(name=after.display_name, icon_url=after.display_avatar.url)
            embed.set_thumbnail(url=after.display_avatar.url)
            await self._send_log(after.guild, "member_update", embed)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot: return
        config = self.get_config(member.guild.id)
        if member.id in config.get("ignored_users", []): return

        embed = discord.Embed(color=discord.Color.light_grey(), timestamp=discord.utils.utcnow())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        if before.channel is None and after.channel is not None:
            embed.title = "🔊 Joined Voice Channel"
            embed.description = f"{member.mention} joined {after.channel.mention}"
        elif before.channel is not None and after.channel is None:
            embed.title = "🔈 Left Voice Channel"
            embed.description = f"{member.mention} left {before.channel.mention}"
        elif before.channel != after.channel:
            embed.title = "🔁 Moved Voice Channel"
            embed.description = f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}"
        else:
            return # Other voice updates (mute/deafen) can be added here if needed

        await self._send_log(member.guild, "voice_state", embed)

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(
            title="🆕 Channel Created",
            description=f"Channel {channel.mention} was created.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Type", value=str(channel.type), inline=True)
        if channel.category:
            embed.add_field(name="Category", value=channel.category.name, inline=True)

        await self._send_log(channel.guild, "channel_update", embed)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(
            title="🚫 Channel Deleted",
            description=f"Channel #{channel.name} was deleted.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Type", value=str(channel.type), inline=True)

        await self._send_log(channel.guild, "channel_update", embed)

    async def on_guild_channel_update(self, before, after):
        changes = []
        if before.name != after.name: changes.append(f"Name: `{before.name}` -> `{after.name}`")
        if hasattr(before, "topic") and before.topic != after.topic: changes.append("Topic changed")
        if before.overwrites != after.overwrites: changes.append("Permissions updated")

        if changes:
            embed = discord.Embed(title="⚙️ Channel Updated", description="\n".join(changes), color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Channel", value=after.mention, inline=True)
            await self._send_log(after.guild, "channel_update", embed)

    async def on_guild_role_update(self, before, after):
        changes = []
        if before.name != after.name: changes.append(f"Name: `{before.name}` -> `{after.name}`")
        if before.color != after.color: changes.append(f"Color: `{before.color}` -> `{after.color}`")
        if before.permissions != after.permissions: changes.append("Permissions changed")
        if before.hoist != after.hoist: changes.append(f"Hoisted: `{before.hoist}` -> `{after.hoist}`")

        if changes:
            embed = discord.Embed(title="⚙️ Role Updated", description="\n".join(changes), color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Role", value=after.mention, inline=True)
            await self._send_log(after.guild, "role_update", embed)

    async def on_bulk_message_delete(self, messages):
        if not messages: return
        guild = messages[0].guild
        embed = discord.Embed(title="🗑️ Bulk Message Delete", description=f"{len(messages)} messages deleted in {messages[0].channel.mention}", color=discord.Color.red(), timestamp=discord.utils.utcnow())
        await self._send_log(guild, "message_delete", embed)

    async def on_guild_update(self, before, after):
        changes = []
        if before.name != after.name: changes.append(f"Name: `{before.name}` -> `{after.name}`")
        if before.icon != after.icon: changes.append("Icon changed")
        if before.premium_tier != after.premium_tier: changes.append(f"Boost Tier: `{before.premium_tier}` -> `{after.premium_tier}`")

        if changes:
            embed = discord.Embed(title="🏰 Server Updated", description="\n".join(changes), color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            await self._send_log(after, "server_update", embed)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """Setup for the logging system"""
        guild = interaction.guild

        # Create default log channel
        log_channel = discord.utils.get(guild.text_channels, name="server-logs")
        if not log_channel:
            try:
                log_channel = await guild.create_text_channel("server-logs", reason="Logging system setup")
            except:
                log_channel = interaction.channel

        config = self.get_config(guild.id)
        config["log_channel_id"] = log_channel.id
        self.save_config(guild.id, config)

        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["loggingpanel"] = "configpanel logging"
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        embed = discord.Embed(
            title="📊 Logging System Active",
            description=f"Server events are now being logged to {log_channel.mention}.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return True
