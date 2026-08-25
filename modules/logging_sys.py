"""Logging Sys systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: logging_mod.py, mod_logging.py
"""



# ======================================================================
# From: modules/logging_mod.py
# ======================================================================

# ======================================================================

import discord
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



# ======================================================================

# From: modules/mod_logging.py
# ======================================================================

import discord
import time
import asyncio
from typing import Dict, List, Optional, Any, Union
from data_manager import dm
from logger import logger

class ModLoggingSystem:
    """
    Moderation Logging System:
    Track staff actions with auto-incrementing case numbers.
    """
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> Dict[str, Any]:
        return dm.get_guild_data(guild_id, "mod_log_config", {
            "enabled": True,
            "log_channel_id": None,
            "next_case_number": 1,
            "enabled_logs": {
                "ban": True,
                "unban": True,
                "kick": True,
                "mute": True,
                "warn": True,
                "role": True,
                "nickname": True,
                "message_delete": True,
                "message_edit": True,
                "channel": True,
                "role_mgmt": True,
                "invite": True
            },
            "ignored_channels": [],
            "ignored_roles": []
        })

    def save_config(self, guild_id: int, config: Dict[str, Any]):
        dm.update_guild_data(guild_id, "mod_log_config", config)

    def _get_next_case(self, guild_id: int) -> int:
        config = self.get_config(guild_id)
        case_num = config.get("next_case_number", 1)
        config["next_case_number"] = case_num + 1
        self.save_config(guild_id, config)
        return case_num

    def save_case(self, guild_id: int, case_data: Dict[str, Any]):
        cases = dm.get_guild_data(guild_id, "mod_cases", {})
        cases[str(case_data["case_number"])] = case_data
        dm.update_guild_data(guild_id, "mod_cases", cases)

    async def create_log(self, guild: discord.Guild, action_type: str, moderator: Union[discord.Member, discord.User],
                         target: Union[discord.Member, discord.User, discord.Object], reason: str = "No reason provided",
                         details: Dict[str, Any] = None, severity: str = "info"):

        config = self.get_config(guild.id)
        if not config.get("enabled", True): return
        if not config.get("enabled_logs", {}).get(action_type, True): return

        case_num = self._get_next_case(guild.id)

        colors = {
            "info": discord.Color.green(),
            "warning": discord.Color.yellow(),
            "moderate": discord.Color.orange(),
            "severe": discord.Color.red()
        }

        embed = discord.Embed(
            title=f"Case #{case_num} | {action_type.title()}",
            color=colors.get(severity, discord.Color.blue()),
            timestamp=discord.utils.utcnow()
        )

        if moderator:
            embed.set_author(name=f"Moderator: {moderator}", icon_url=moderator.display_avatar.url)

        if isinstance(target, (discord.Member, discord.User)):
            embed.add_field(name="Target", value=f"{target.mention} ({target.id})", inline=True)
            embed.set_thumbnail(url=target.display_avatar.url)
        else:
            embed.add_field(name="Target ID", value=str(target.id), inline=True)

        embed.add_field(name="Reason", value=reason, inline=False)

        if details:
            for key, value in details.items():
                embed.add_field(name=key, value=str(value), inline=True)

        log_channel_id = config.get("log_channel_id")
        if log_channel_id:
            channel = guild.get_channel(int(log_channel_id))
            if channel:
                try:
                    msg = await channel.send(embed=embed)
                    jump_url = msg.jump_url
                except:
                    jump_url = None
            else:
                jump_url = None
        else:
            jump_url = None

        # Save to DB
        case_data = {
            "case_number": case_num,
            "action_type": action_type,
            "moderator_id": moderator.id if moderator else None,
            "target_id": target.id,
            "reason": reason,
            "details": details,
            "severity": severity,
            "timestamp": time.time(),
            "jump_url": jump_url
        }
        self.save_case(guild.id, case_data)

    # Specific Action Loggers (to be called from other modules or events)

    async def log_ban(self, guild, moderator, target, reason):
        await self.create_log(guild, "ban", moderator, target, reason, severity="severe")

    async def log_unban(self, guild, moderator, target, reason):
        await self.create_log(guild, "unban", moderator, target, reason, severity="info")

    async def log_kick(self, guild, moderator, target, reason):
        await self.create_log(guild, "kick", moderator, target, reason, severity="moderate")

    async def log_mute(self, guild, moderator, target, reason, duration=None):
        details = {"Duration": duration} if duration else None
        await self.create_log(guild, "mute", moderator, target, reason, details, severity="moderate")

    async def log_warn(self, guild, moderator, target, reason, warn_count=None):
        details = {"Warning #": warn_count} if warn_count else None
        await self.create_log(guild, "warning", moderator, target, reason, details, severity="warning")

    async def log_unmute(self, guild, moderator, target, reason):
        await self.create_log(guild, "unmute", moderator, target, reason, severity="info")

    async def log_nickname(self, guild, moderator, target, old_nick, new_nick):
        details = {"Old Nick": old_nick, "New Nick": new_nick}
        await self.create_log(guild, "nickname", moderator, target, "Manual Nickname Change", details, severity="info")

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """Setup for the moderation logging system"""
        guild = interaction.guild

        # Create default log channel
        log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
        if not log_channel:
            try:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                log_channel = await guild.create_text_channel("mod-logs", overwrites=overwrites, reason="Moderation logging setup")
            except:
                log_channel = interaction.channel

        config = self.get_config(guild.id)
        config["log_channel_id"] = log_channel.id
        self.save_config(guild.id, config)

        embed = discord.Embed(
            title="⚖️ Moderation Logging Active",
            description=f"Moderation actions will now be logged to {log_channel.mention}.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return True
