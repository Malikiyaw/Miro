"""
Guardian: AI-Powered Anti-Raid & Server Security Layer for Miro Bot
Distinct from Anti-Raid, Guardian handles silent monitoring and escalation.
"""

import discord
from discord.ext import commands
import asyncio
import time
import re
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta

from data_manager import dm
from logger import logger

DEFAULT_GUARDIAN_CONFIG = {
    "enabled": False,
    "alert_channel": None,
    "toxicity_level": "OFF",    # OFF, WARN, MUTE, KICK, BAN
    "scam_level": "OFF",
    "impersonation_level": "OFF",
    "mass_dm_threshold": 10,     # msgs/min
    "nuke_level": "OFF",
    "token_detection": False,
    "malware_level": "OFF",
    "selfbot_level": "OFF",
    "escalation": False,
    "whitelist": [],
    "guardian_log": []
}

# Discord bot token and API key regex (Discord tokens, OpenAI keys, etc.)
_TOKEN_PATTERN = re.compile(
    r"("
    r"(?:[MNO][A-Za-z\d]{23}|[A-Za-z\d]{24})\.(?:[A-Za-z\d]{6}|[A-Za-z\d_-]{4,8})\.[A-Za-z\d_-]{27,38}" # Discord Token
    r"|sk-[a-zA-Z0-9]{48}" # OpenAI Key
    r"|bot_[a-zA-Z0-9]{20,}" # Generic Bot Token pattern
    r")"
)

class GuardianSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._dm_tracking: Dict[int, Dict[int, list]] = {}
        self._typing_patterns: Dict[int, Dict[int, list]] = {}

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "guardian_config", DEFAULT_GUARDIAN_CONFIG.copy())

    def save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "guardian_config", config)

    def _log_incident(self, guild_id: int, type: str, user_id: int, action: str, details: str = ""):
        config = self.get_config(guild_id)
        log = config.get("guardian_log", [])
        log.append({
            "ts": time.time(),
            "type": type,
            "user_id": user_id,
            "action": action,
            "details": details
        })
        config["guardian_log"] = log[-200:]
        self.save_config(guild_id, config)
        asyncio.create_task(self._send_alert(guild_id, type, user_id, action, details))

    async def _send_alert(self, guild_id: int, type: str, user_id: int, action: str, details: str):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        config = self.get_config(guild_id)
        ch_id = config.get("alert_channel")
        channel = guild.get_channel(ch_id) if ch_id else None

        embed = discord.Embed(
            title="⚔️ Guardian Intervention",
            description=f"**Detection:** {type.replace('_', ' ').title()}",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        if details:
            embed.add_field(name="Details", value=details[:1024], inline=False)

        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass

    async def handle_message(self, message: discord.Message):
        """Called by bot.py on_message — safe wrapper around _process_message."""
        if message.author.bot or not message.guild:
            return
        try:
            await self._process_message(message)
        except Exception as e:
            logger.error(f"Guardian handle_message error: {e}")

    async def _process_message(self, message: discord.Message):
        guild = message.guild
        config = self.get_config(guild.id)
        if not config.get("enabled", True):
            return
        if message.author.id in config.get("whitelist", []):
            return

        content = message.content
        author = message.author

        # 1. Bot Token Detection
        if config.get("token_detection", True) and _TOKEN_PATTERN.search(content):
            try:
                await message.delete()
            except Exception:
                pass
            action_level = config.get("token_action_level", "MUTE")
            await self._take_action(author, action_level, "Discord Bot Token Leaked")
            self._log_incident(guild.id, "token_leak", author.id, action_level, "Bot token pattern detected")
            try:
                await author.send(
                    "⚠️ **SECURITY ALERT** — Your message was deleted because it contained "
                    "what appears to be a Discord bot token. Please regenerate your token immediately."
                )
            except Exception:
                pass
            return

        # 2. Scam / Phishing Detection
        scam_keywords = ["nitro", "gift", "steam", "free", "airdrop", "robux", "crypto"]
        if any(p in content.lower() for p in scam_keywords) and ("http" in content or "discord.gg" in content):
            is_scam = True  # default; AI can override
            if hasattr(self.bot, "ai"):
                try:
                    analysis = await self.bot.ai.analyze_content(content, "scam_check")
                    is_scam = analysis.get("is_scam", True)
                except Exception:
                    pass

            if is_scam:
                try:
                    await message.delete()
                except Exception:
                    pass
                action_level = config.get("scam_level", "MUTE")
                await self._take_action(author, action_level, "Scam / Phishing Link")
                self._log_incident(guild.id, "scam_link", author.id, action_level, content[:200])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Cog listener — delegates to handle_message for unified processing."""
        await self.handle_message(message)

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        if user.bot or not hasattr(channel, "guild"):
            return
        guild = channel.guild
        config = self.get_config(guild.id)
        if not config.get("enabled", True):
            return
        if guild.id not in self._typing_patterns:
            self._typing_patterns[guild.id] = {}
        if user.id not in self._typing_patterns[guild.id]:
            self._typing_patterns[guild.id][user.id] = []
        self._typing_patterns[guild.id][user.id].append(time.time())

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        guild = entry.guild
        config = self.get_config(guild.id)
        if not config.get("enabled", True):
            return
        # Nuke protection: rapid channel/role deletions
        if entry.action in [discord.AuditLogAction.channel_delete, discord.AuditLogAction.role_delete]:
            # Track rapid deletions for nuke protection
            if not hasattr(self, '_deletion_tracker'):
                self._deletion_tracker = {}
            
            user_id = entry.user.id if entry.user else 0
            now = time.time()
            
            if user_id not in self._deletion_tracker:
                self._deletion_tracker[user_id] = []
            
            # Add current deletion timestamp
            self._deletion_tracker[user_id].append(now)
            
            # Keep only deletions from last 10 seconds
            self._deletion_tracker[user_id] = [t for t in self._deletion_tracker[user_id] if now - t < 10]
            
            # If user deleted 5+ channels/roles in 10 seconds, consider it a nuke
            if len(self._deletion_tracker[user_id]) >= 5:
                # Reset tracker to prevent repeated triggers
                self._deletion_tracker[user_id] = []
                
                # Take action based on nuke_level config
                action_level = config.get("nuke_level", "BAN")
                await self._take_action(
                    guild.get_member(user_id) or await guild.fetch_member(user_id),
                    action_level,
                    f"Nuke protection: {len(self._deletion_tracker[user_id] + [now])} rapid deletions detected"
                )
                
                self._log_incident(
                    guild.id, 
                    "nuke_detected", 
                    user_id, 
                    action_level, 
                    f"Rapid deletion of {len(self._deletion_tracker[user_id] + [now])} channels/roles"
                )

    async def _take_action(self, member: discord.Member, level: str, reason: str):
        if level in ("OFF", None):
            return
        try:
            if level == "WARN":
                try:
                    await member.send(
                        f"⚠️ **Guardian Warning**\n"
                        f"Server: {member.guild.name}\n"
                        f"Reason: {reason}"
                    )
                except Exception:
                    pass
                if hasattr(self.bot, "warnings"):
                    await self.bot.warnings.issue_warning(
                        member.guild, member.id, self.bot.user.id,
                        f"Guardian: {reason}", "moderate"
                    )
            elif level == "MUTE":
                await member.timeout(timedelta(hours=2), reason=f"Guardian: {reason}")
            elif level == "KICK":
                await member.kick(reason=f"Guardian: {reason}")
            elif level == "BAN":
                await member.ban(reason=f"Guardian: {reason}")
        except Exception as e:
            logger.error(f"Guardian failed to take action {level} on {member.id}: {e}")


async def setup(bot):
    await bot.add_cog(GuardianSystem(bot))
