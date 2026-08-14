import discord
from discord.ext import commands
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
