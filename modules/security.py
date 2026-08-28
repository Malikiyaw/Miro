"""Security systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: anti_raid.py, guardian.py, moderation.py, warnings.py, automod.py, appeals.py
"""



# ======================================================================
# From: modules/anti_raid.py
# ======================================================================

import discord
import asyncio
import json
import time
import re
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger

class AntiRaidSystem:
    def __init__(self, bot):
        self.bot = bot
        self._join_history: Dict[int, List[float]] = {}
        # Track mentions: {guild_id: {user_id: [timestamp, ...]}}
        self._mention_history: Dict[int, Dict[int, List[float]]] = {}
        # Track messages for duplicate detection: {guild_id: {user_id: [content, ...]}}
        self._msg_content_history: Dict[int, Dict[int, List[str]]] = {}

    def get_guild_settings(self, guild_id: int) -> dict:
        """Retrieve guild anti-raid settings, unified with config panel key."""
        return dm.get_guild_data(guild_id, "anti_raid_config", {
            "enabled": True,
            "mass_join_threshold": 10,
            "mass_join_window": 10,
            "auto_lockdown": True,
            "action": "lockdown", # lockdown, kick, ban, mute
            "min_account_age_days": 0,
            "age_filter_enabled": False,
            "avatar_filter_enabled": False,
            "quarantine_role_id": None,
            "alert_channel_id": None,
            "raid_log": [],
            "rules": {
                "link_spam": {"enabled": True},
                "invite_filter": {"enabled": True},
                "mention_filter": {"enabled": True, "threshold": 5},
                "duplicate_filter": {"enabled": True, "threshold": 3},
                "emoji_filter": {"enabled": True, "threshold": 15}
            }
        })

    def save_settings(self, guild_id: int, settings: dict):
        dm.update_guild_data(guild_id, "anti_raid_config", settings)

    def _log_incident(self, guild_id: int, type: str, members: List[int], action: str):
        settings = self.get_guild_settings(guild_id)
        log = settings.get("raid_log", [])
        log.append({
            "ts": time.time(),
            "type": type,
            "members": members,
            "action": action
        })
        settings["raid_log"] = log[-100:]
        self.save_settings(guild_id, settings)
        
        # Send alert
        asyncio.create_task(self._send_alert(guild_id, type, members, action))

    async def _send_alert(self, guild_id: int, type: str, members: List[int], action: str):
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        settings = self.get_guild_settings(guild_id)
        ch_id = settings.get("alert_channel_id")
        channel = guild.get_channel(ch_id) if ch_id else None
        
        embed = discord.Embed(
            title="🛡️ Anti-Raid Alert",
            description=f"**Trigger:** {type.replace('_', ' ').title()}",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Action Taken", value=action.upper(), inline=True)
        embed.add_field(name="Impacted Members", value=f"{len(members)} users" if len(members) > 5 else ", ".join([f"<@{m}>" for m in members]), inline=False)
        
        if channel:
            try: await channel.send(embed=embed)
            except: pass
            
        # Notify owner on major raids
        if type == "mass_join" and guild.owner:
            try: await guild.owner.send(f"⚠️ **Raid Alert in {guild.name}**\nMass join detected! Action taken: {action.upper()}")
            except: pass

    async def handle_join(self, member: discord.Member):
        guild = member.guild
        settings = self.get_guild_settings(guild.id)
        if not settings.get("enabled", True): return

        # 1. New Account Check
        if settings.get("age_filter_enabled"):
            min_age = settings.get("min_account_age_days", 0)
            if (discord.utils.utcnow() - member.created_at).days < min_age:
                await self._take_action(member, settings.get("action"), "Account too young")
                self._log_incident(guild.id, "new_account_join", [member.id], settings.get("action"))
                return

        # 2. Mass Join Detection
        if guild.id not in self._join_history: self._join_history[guild.id] = []
        now = time.time()
        window = settings.get("mass_join_window", 10)
        threshold = settings.get("mass_join_threshold", 10)
        
        self._join_history[guild.id] = [t for t in self._join_history[guild.id] if now - t < window]
        self._join_history[guild.id].append(now)
        
        if len(self._join_history[guild.id]) >= threshold:
            if settings.get("auto_lockdown"):
                await self._lockdown(guild)
                self._log_incident(guild.id, "mass_join", [], "lockdown")

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        guild = message.guild
        settings = self.get_guild_settings(guild.id)
        if not settings.get("enabled", True): return

        content = message.content
        author = message.author

        # 1. Mention Spam
        mention_filter = settings.get("rules", {}).get("mention_filter", {})
        if mention_filter.get("enabled", True):
            mentions = len(message.mentions) + len(message.role_mentions)
            threshold = mention_filter.get("threshold", 5)
            if mentions >= threshold or message.mention_everyone:
                await message.delete()
                await self._take_action(author, "mute", "Mention spam")
                self._log_incident(guild.id, "mention_spam", [author.id], "mute")
                return

        # 2. Duplicate Spam
        dup_filter = settings.get("rules", {}).get("duplicate_filter", {})
        if dup_filter.get("enabled", True):
            threshold = dup_filter.get("threshold", 3)
            if guild.id not in self._msg_content_history: self._msg_content_history[guild.id] = {}
            if author.id not in self._msg_content_history[guild.id]: self._msg_content_history[guild.id][author.id] = []
            
            history = self._msg_content_history[guild.id][author.id]
            history.append(content)
            if len(history) > 10: history.pop(0)
            
            if len(history) >= threshold and all(m == content for m in history[-threshold:]):
                await message.delete()
                await self._take_action(author, "mute", "Duplicate message spam")
                self._log_incident(guild.id, "duplicate_spam", [author.id], "mute")
                return

        # 3. Link/Invite Spam
        link_filter = settings.get("rules", {}).get("link_spam", {})
        if link_filter.get("enabled", True) and re.search(r"https?://", content):
            # Check for discord invites
            inv_filter = settings.get("rules", {}).get("invite_filter", {})
            if inv_filter.get("enabled", True) and ("discord.gg/" in content or "discord.com/invite/" in content):
                await message.delete()
                await self._take_action(author, "warn", "Invite link spam")
                return
            
        # 4. Emoji Spam
        emoji_filter = settings.get("rules", {}).get("emoji_filter", {})
        if emoji_filter.get("enabled", True):
            emojis = len(re.findall(r"<a?:\w+:\d+>|[\u263a-\U0001f645]", content))
            threshold = emoji_filter.get("threshold", 15)
            if emojis > threshold:
                await message.delete()
                await self._take_action(author, "warn", "Emoji spam")
                return

    async def _take_action(self, member: discord.Member, action: str, reason: str):
        try:
            if action == "kick": await member.kick(reason=reason)
            elif action == "ban": await member.ban(reason=reason)
            elif action == "mute":
                await member.timeout(timedelta(hours=1), reason=reason)
            elif action == "lockdown":
                await self._lockdown(member.guild)
        except: pass

    async def _lockdown(self, guild: discord.Guild):
        for ch in guild.text_channels:
            try:
                await ch.set_permissions(guild.default_role, send_messages=False, reason="Anti-Raid Auto-Lockdown")
            except: pass

    async def lift_lockdown(self, guild: discord.Guild):
        for ch in guild.text_channels:
            try:
                await ch.set_permissions(guild.default_role, send_messages=None, reason="Anti-Raid Lockdown Lifted")
            except: pass

    def start_monitoring(self):
        logger.info("AntiRaidSystem started - using event listeners for real-time response")

    async def handle_member_remove(self, member: discord.Member):
        logger.info(f"Member left: {member.display_name} ({member.id}) in {member.guild.name}")

async def anti_raid_extension_setup(bot):
    bot.anti_raid = AntiRaidSystem(bot)



# ======================================================================
# From: modules/guardian.py
# ======================================================================

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
                    analysis = await self.bot.ai.analyze_content(content, "scam_check",
                                                                 guild_id=guild.id)
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


async def guardian_extension_setup(bot):
    await bot.add_cog(GuardianSystem(bot))



# ======================================================================
# From: modules/moderation.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum
from datetime import timedelta

from data_manager import dm
from logger import logger


class ViolationSeverity(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ModerationAction(Enum):
    NOTHING = 0
    WARN = 1
    TEMP_MUTE = 2
    PERM_MUTE = 3
    KICK = 4
    TEMP_BAN = 5
    PERM_BAN = 6


@dataclass
class ModerationViolation:
    user_id: int
    guild_id: int
    severity: ViolationSeverity
    reason: str
    message_content: str
    context_messages: List[str]
    timestamp: float
    ai_reasoning: str


@dataclass
class UserModerationHistory:
    warnings: int
    mutes: int
    kicks: int
    bans: int
    last_violation: float
    violation_count: int
    trust_score: float


class ModerationSystem:
    def __init__(self, bot):
        self.bot = bot
        self._guild_configs: Dict[int, dict] = {}
        self._user_histories: Dict[int, Dict[int, UserModerationHistory]] = {}
        self._pending_analyses: Dict[int, asyncio.Task] = {}
        self._message_buffers: Dict[int, Dict[int, List[dict]]] = {}

    def get_config(self, guild_id: int) -> dict:
        if guild_id in self._guild_configs:
            return self._guild_configs[guild_id]
        
        config = dm.get_guild_data(guild_id, "moderation_config", {
            "enabled": False,
            "ai_enabled": True,
            "log_channel": None,
            "auto_moderation": False,
            "sensitivity": "medium",
            "ignored_roles": [],
            "ignored_channels": [],
            "response_tiers": {
                "first_offense": ModerationAction.WARN,
                "second_offense": ModerationAction.TEMP_MUTE,
                "third_offense": ModerationAction.PERM_MUTE,
                "extreme": ModerationAction.PERM_BAN
            },
            "keywords": {
                "critical": [],
                "high": [],
                "medium": [],
                "low": []
            },
            "cooldown_seconds": 60,
            "appeal_enabled": True
        })
        self._guild_configs[guild_id] = config
        return config

    def update_config(self, guild_id: int, key: str, value):
        config = self.get_config(guild_id)
        config[key] = value
        self._guild_configs[guild_id] = config
        dm.update_guild_data(guild_id, "moderation_config", config)

    def get_user_history(self, guild_id: int, user_id: int) -> UserModerationHistory:
        if guild_id not in self._user_histories:
            self._user_histories[guild_id] = {}
        
        if user_id in self._user_histories[guild_id]:
            return self._user_histories[guild_id][user_id]
        
        history_data = dm.get_guild_data(guild_id, f"mod_history_{user_id}", {
            "warnings": 0,
            "mutes": 0,
            "kicks": 0,
            "bans": 0,
            "last_violation": 0,
            "violation_count": 0,
            "trust_score": 1.0
        })
        
        history = UserModerationHistory(
            warnings=history_data["warnings"],
            mutes=history_data["mutes"],
            kicks=history_data["kicks"],
            bans=history_data["bans"],
            last_violation=history_data["last_violation"],
            violation_count=history_data["violation_count"],
            trust_score=history_data["trust_score"]
        )
        self._user_histories[guild_id][user_id] = history
        return history

    def save_user_history(self, guild_id: int, user_id: int, history: UserModerationHistory):
        history_data = {
            "warnings": history.warnings,
            "mutes": history.mutes,
            "kicks": history.kicks,
            "bans": history.bans,
            "last_violation": history.last_violation,
            "violation_count": history.violation_count,
            "trust_score": history.trust_score
        }
        dm.update_guild_data(guild_id, f"mod_history_{user_id}", history_data)
        self._user_histories[guild_id][user_id] = history

    async def analyze_message(self, message: discord.Message) -> Optional[ModerationViolation]:
        config = self.get_config(message.guild.id)
        if not config.get("enabled", False):
            return None
        
        if message.author.bot:
            return None
        
        if message.author.guild_permissions.administrator:
            return None
        
        if config.get("ignored_roles"):
            for role in message.author.roles:
                if role.id in config["ignored_roles"]:
                    return None
        
        if message.channel.id in config.get("ignored_channels", []):
            return None
        
        await self._buffer_message(message)
        
        if not config.get("ai_enabled", True):
            return await self._keyword_analysis(message, config)
        
        return await self._ai_analysis(message, config)

    async def _buffer_message(self, message: discord.Message):
        guild_id = message.guild.id
        user_id = message.author.id
        
        if guild_id not in self._message_buffers:
            self._message_buffers[guild_id] = {}
        
        if user_id not in self._message_buffers[guild_id]:
            self._message_buffers[guild_id][user_id] = []
        
        self._message_buffers[guild_id][user_id].append({
            "content": message.content,
            "channel": message.channel.name,
            "timestamp": message.created_at.timestamp()
        })
        
        buffer_size = 10
        self._message_buffers[guild_id][user_id] = self._message_buffers[guild_id][user_id][-buffer_size:]

    async def _keyword_analysis(self, message: discord.Message, config: dict) -> Optional[ModerationViolation]:
        keywords = config.get("keywords", {})
        content_lower = message.content.lower()
        
        for severity in ["critical", "high", "medium", "low"]:
            for keyword in keywords.get(severity, []):
                if keyword.lower() in content_lower:
                    severity_enum = ViolationSeverity[severity.upper()]
                    return ModerationViolation(
                        user_id=message.author.id,
                        guild_id=message.guild.id,
                        severity=severity_enum,
                        reason=f"Keyword detected: {keyword}",
                        message_content=message.content,
                        context_messages=[m["content"] for m in self._message_buffers.get(message.guild.id, {}).get(message.author.id, [])],
                        timestamp=time.time(),
                        ai_reasoning=f"Matched keyword '{keyword}' at {severity} severity level"
                    )
        
        return None

    async def _ai_analysis(self, message: discord.Message, config: dict) -> Optional[ModerationViolation]:
        user_history = self.get_user_history(message.guild.id, message.author.id)
        
        context_msgs = self._message_buffers.get(message.guild.id, {}).get(message.author.id, [])
        context_str = "\n".join([f"[{m['channel']}] {m['content']}" for m in context_msgs[-5:]])
        
        analysis_prompt = f"""Analyze this Discord message for moderation concerns.

MESSAGE TO ANALYZE:
{message.content}

RECENT CONTEXT (last few messages from this user):
{context_str}

USER HISTORY:
- Warnings: {user_history.warnings}
- Mutes: {user_history.mutes}
- Kicks: {user_history.kicks}
- Bans: {user_history.bans}
- Trust Score: {user_history.trust_score:.2f}/1.0
- Total Violations: {user_history.violation_count}

Respond with JSON only (no other text):
{{
    "violation_detected": true/false,
    "severity": "low/medium/high/critical",
    "reason": "brief reason for the determination",
    "reasoning": "your chain-of-thought analysis",
    "sarcasm_detected": true/false,
    "context_considered": true/false
}}

Consider:
- Sarcasm, irony, or jokes (don't punish humor)
- Context from previous messages
- User's trust score (trusted users get benefit of doubt)
- Whether it's clearly malicious vs ambiguous
- Cultural differences in expression"""

        try:
            result = await self.bot.ai.chat(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_input=analysis_prompt,
                system_prompt="You are a fair, nuanced Discord moderator. You analyze messages contextually and avoid false positives. You give users benefit of doubt when content is ambiguous."
            )
            
            if result.get("violation_detected"):
                severity_str = result.get("severity", "medium").lower()
                severity = ViolationSeverity[severity_str.upper()] if severity_str.upper() in ["LOW", "MEDIUM", "HIGH", "CRITICAL"] else ViolationSeverity.MEDIUM
                
                return ModerationViolation(
                    user_id=message.author.id,
                    guild_id=message.guild.id,
                    severity=severity,
                    reason=result.get("reason", "AI-detected violation"),
                    message_content=message.content,
                    context_messages=[m["content"] for m in context_msgs],
                    timestamp=time.time(),
                    ai_reasoning=result.get("reasoning", "No reasoning provided")
                )
        except Exception as e:
            logger.error(f"AI moderation analysis failed: {e}")
            return await self._keyword_analysis(message, config)
        
        return None

    async def handle_violation(self, violation: ModerationViolation) -> ModerationAction:
        config = self.get_config(violation.guild_id)
        history = self.get_user_history(violation.guild_id, violation.user_id)
        guild = self.bot.get_guild(violation.guild_id)
        member = guild.get_member(violation.user_id)
        
        if not member:
            return ModerationAction.NOTHING
        
        action = self._determine_action(violation, history, config)
        
        if action == ModerationAction.NOTHING:
            return action
        
        log_channel = guild.get_channel(config.get("log_channel")) if config.get("log_channel") else None
        
        if action == ModerationAction.WARN:
            history.warnings += 1
            await member.send(f"⚠️ **Warning:** {violation.reason}")
            if log_channel:
                embed = self._create_log_embed(violation, history, "Warning Issued")
                await log_channel.send(embed=embed)
        
        elif action == ModerationAction.TEMP_MUTE:
            history.mutes += 1
            mute_duration = self._get_mute_duration(history.violation_count)
            await member.timeout(discord.utils.utcnow() + timedelta(minutes=mute_duration), reason=violation.reason)
            await member.send(f"🔇 **Temporarily Muted** for {mute_duration} minutes. Reason: {violation.reason}")
            if log_channel:
                embed = self._create_log_embed(violation, history, f"Tempmute ({mute_duration}m)")
                await log_channel.send(embed=embed)
        
        elif action == ModerationAction.PERM_MUTE:
            history.mutes += 1
            await member.timeout(discord.utils.utcnow() + timedelta(days=365), reason=violation.reason)
            await member.send(f"🔇 **Muted Indefinitely.** Reason: {violation.reason}")
            if log_channel:
                embed = self._create_log_embed(violation, history, "Permanent Mute")
                await log_channel.send(embed=embed)
        
        elif action == ModerationAction.KICK:
            history.kicks += 1
            await member.kick(reason=violation.reason)
            if log_channel:
                embed = self._create_log_embed(violation, history, "Kicked")
                await log_channel.send(embed=embed)
        
        elif action == ModerationAction.PERM_BAN:
            history.bans += 1
            await member.ban(reason=violation.reason)
            if log_channel:
                embed = self._create_log_embed(violation, history, "Banned")
                await log_channel.send(embed=embed)
        
        history.last_violation = violation.timestamp
        history.violation_count += 1
        history.trust_score = max(0.0, history.trust_score - 0.1)
        
        self.save_user_history(violation.guild_id, violation.user_id, history)
        
        if config.get("appeal_enabled"):
            await self._create_appeal_ticket(violation, history, member)
        
        return action

    def _determine_action(self, violation: ModerationViolation, history: UserModerationHistory, config: dict) -> ModerationAction:
        tiers = config.get("response_tiers", {})
        
        if violation.severity == ViolationSeverity.CRITICAL:
            return ModerationAction.PERM_BAN
        
        violation_count = history.violation_count
        
        if violation_count == 0:
            return ModerationAction(tiers.get("first_offense", ModerationAction.WARN))
        elif violation_count == 1:
            return ModerationAction(tiers.get("second_offense", ModerationAction.TEMP_MUTE))
        elif violation_count == 2:
            return ModerationAction(tiers.get("third_offense", ModerationAction.PERM_MUTE))
        else:
            return ModerationAction(tiers.get("extreme", ModerationAction.PERM_BAN))

    def _get_mute_duration(self, violation_count: int) -> int:
        durations = {0: 5, 1: 15, 2: 30, 3: 60, 4: 120}
        return durations.get(violation_count, 180)

    def _create_log_embed(self, violation: ModerationViolation, history: UserModerationHistory, action_taken: str) -> discord.Embed:
        import datetime
        embed = discord.Embed(
            title=f"⚖️ Moderation Action: {action_taken}",
            color=discord.Color.red() if "Ban" in action_taken else discord.Color.orange()
        )
        embed.add_field(name="User", value=f"<@{violation.user_id}>", inline=True)
        embed.add_field(name="Violation Count", value=str(history.violation_count), inline=True)
        embed.add_field(name="Trust Score", value=f"{history.trust_score:.2f}", inline=True)
        embed.add_field(name="Reason", value=violation.reason, inline=False)
        embed.add_field(name="AI Reasoning", value=violation.ai_reasoning[:500], inline=False)
        embed.add_field(name="Message", value=violation.message_content[:200], inline=False)
        embed.timestamp = discord.utils.utcnow()
        return embed

    async def _create_appeal_ticket(self, violation: ModerationViolation, history: UserModerationHistory, member: discord.Member):
        appeals = dm.get_guild_data(violation.guild_id, "appeals", {})
        user_appeals = appeals.get(str(violation.user_id), [])
        
        if len(user_appeals) >= 3:
            return
        
        dm.update_guild_data(violation.guild_id, "appeals", appeals)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        config = self.get_config(guild.id)
        config["enabled"] = True
        
        self.update_config(guild.id, "enabled", True)
        self.update_config(guild.id, "log_channel", interaction.channel.id)
        
        help_embed = discord.Embed(
            title="🛡️ AI Contextual Moderation System",
            description="Intelligent, context-aware moderation that learns from decisions.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Analyzes messages using AI for context, sarcasm detection, and nuanced understanding. Considers user history and trust scores.",
            inline=False
        )
        help_embed.add_field(
            name="Features",
            value="• Contextual analysis (not just keywords)\n• Sarcasm detection\n• Trust scoring system\n• Escalating responses\n• Auto-appeal tickets",
            inline=False
        )
        help_embed.add_field(
            name="!modstats",
            value="Check your moderation status and trust score.",
            inline=False
        )
        help_embed.add_field(
            name="!appeal",
            value="Appeal a moderation action if you believe it was wrong.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["modstats"] = json.dumps({
            "command_type": "moderation_stats"
        })
        custom_cmds["appeal"] = json.dumps({
            "command_type": "appeal"
        })
        custom_cmds["help moderation"] = json.dumps({
            "command_type": "help_embed",
            "title": "🛡️ AI Contextual Moderation System",
            "description": "Intelligent, context-aware moderation that learns from decisions.",
            "fields": [
                {"name": "How it works", "value": "Analyzes messages using AI for context, sarcasm detection, and nuanced understanding.", "inline": False},
                {"name": "!modstats", "value": "Check your moderation status and trust score.", "inline": False},
                {"name": "!appeal", "value": "Appeal a moderation action if you believe it was wrong.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from datetime import timedelta



# ======================================================================
# From: modules/warnings.py
# ======================================================================

import discord
import time
import json
import re
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from data_manager import dm
from logger import logger

class WarningSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "warning_config", {
            "enabled": True,
            "thresholds": {
                "minor": {"count": 2, "action": "none"},
                "moderate": {"count": 3, "action": "mute_60"},
                "severe": {"count": 4, "action": "kick"},
                "critical": {"count": 5, "action": "ban"}
            },
            "expiry_days": 30,
            "dm_enabled": True,
            "dm_template": "Hello {user}, you have been warned for: {reason}. Severity: {severity}. Total active warnings: {count}. Next threshold action: {next_action}."
        })

    def save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "warning_config", config)

    def get_warnings(self, guild_id: int, user_id: int) -> List[dict]:
        all_warnings = dm.get_guild_data(guild_id, f"user_warnings_{user_id}", [])

        # Filter out expired warnings
        config = self.get_config(guild_id)
        expiry_days = config.get("expiry_days", 30)

        if expiry_days > 0:
            now = time.time()
            expiry_seconds = expiry_days * 24 * 3600
            for w in all_warnings:
                if not w.get("pardoned") and now - w.get("timestamp", 0) > expiry_seconds:
                    w["active"] = False

        return all_warnings

    def get_stats(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "warning_stats", {
            "today": 0,
            "week": 0,
            "severity_breakdown": {"minor": 0, "moderate": 0, "severe": 0},
            "total_pardoned": 0
        })

    def get_history(self, guild_id: int) -> List[dict]:
        return dm.get_guild_data(guild_id, "warning_history", [])

    def get_most_warned(self, guild_id: int) -> List[dict]:
        """Find top 10 most warned users."""
        guild_data = dm.load_json(f"guild_{guild_id}", default={})
        warn_counts = []
        for key, value in guild_data.items():
            if key.startswith("user_warnings_") and isinstance(value, list):
                uid = key.replace("user_warnings_", "")
                active_count = len([w for w in value if w.get("active") and not w.get("pardoned")])
                if active_count > 0:
                    warn_counts.append({"user_id": int(uid), "count": active_count})

        warn_counts.sort(key=lambda x: x["count"], reverse=True)
        return warn_counts[:10]

    async def issue_warning(self, guild: discord.Guild, user_id: int, moderator_id: int, reason: str, severity: str = "minor"):
        config = self.get_config(guild.id)
        all_warnings = self.get_warnings(guild.id, user_id) # Use get_warnings to apply expiry

        warning_id = len(all_warnings) + 1
        new_warning = {
            "id": warning_id,
            "moderator_id": moderator_id,
            "reason": reason,
            "severity": severity,
            "timestamp": time.time(),
            "active": True,
            "pardoned": False
        }

        all_warnings.append(new_warning)
        dm.update_guild_data(guild.id, f"user_warnings_{user_id}", all_warnings)

        # Check thresholds
        active_count = len([w for w in all_warnings if w.get("active") and not w.get("pardoned")])

        action = "none"
        for level, data in config.get("thresholds", {}).items():
            if active_count >= data["count"]:
                action = data["action"]

        # DM User
        if config.get("dm_enabled"):
            member = guild.get_member(user_id)
            if member:
                next_action = "None"
                # Find next action
                sorted_thresholds = sorted(config.get("thresholds", {}).items(), key=lambda x: x[1]["count"])
                for level, data in sorted_thresholds:
                    if data["count"] > active_count:
                        next_action = f"{data['action']} at {data['count']} warnings"
                        break

                dm_text = config.get("dm_template").format(
                    user=member.name,
                    reason=reason,
                    severity=severity,
                    count=active_count,
                    next_action=next_action
                )
                try: await member.send(dm_text)
                except: pass

        # Update Stats
        gid = guild.id
        stats = dm.get_guild_data(gid, "warning_stats", {
            "today": 0,
            "week": 0,
            "severity_breakdown": {"minor": 0, "moderate": 0, "severe": 0},
            "total_pardoned": 0,
            "last_reset": time.time()
        })
        now = time.time()
        if now - stats.get("last_reset", 0) > 86400:
            stats["today"] = 0
            stats["last_reset"] = now
        stats["today"] += 1
        stats["week"] += 1
        stats["severity_breakdown"][severity] = stats["severity_breakdown"].get(severity, 0) + 1
        dm.update_guild_data(gid, "warning_stats", stats)

        # Global Log for Panel
        history = dm.get_guild_data(gid, "warning_history", [])
        history.append({
            "ts": time.time(),
            "user_id": user_id,
            "mod_id": moderator_id,
            "reason": reason,
            "severity": severity
        })
        dm.update_guild_data(gid, "warning_history", history[-20:])

        # Log action
        await self._log_warning(guild, user_id, moderator_id, new_warning, active_count, action)

        # Apply punishment if needed
        if action != "none":
            member = guild.get_member(user_id)
            if member:
                await self._apply_punishment(member, action, f"Threshold met: {active_count} warnings")

        return warning_id

    async def pardon_warning(self, guild: discord.Guild, user_id: int, warning_id: int, reason: str):
        all_warnings = dm.get_guild_data(guild.id, f"user_warnings_{user_id}", [])
        found = False
        for w in all_warnings:
            if w.get("id") == warning_id:
                w["pardoned"] = True
                w["pardon_reason"] = reason
                w["pardon_timestamp"] = time.time()
                found = True
                break

        if found:
            dm.update_guild_data(guild.id, f"user_warnings_{user_id}", all_warnings)
            stats = dm.get_guild_data(guild.id, "warning_stats", {})
            stats["total_pardoned"] = stats.get("total_pardoned", 0) + 1
            dm.update_guild_data(guild.id, "warning_stats", stats)
        return found

    async def delete_warning(self, guild: discord.Guild, user_id: int, warning_id: int):
        all_warnings = dm.get_guild_data(guild.id, f"user_warnings_{user_id}", [])
        new_warnings = [w for w in all_warnings if w.get("id") != warning_id]
        if len(new_warnings) != len(all_warnings):
            dm.update_guild_data(guild.id, f"user_warnings_{user_id}", new_warnings)
            return True
        return False

    async def clear_all_warnings(self, guild: discord.Guild, user_id: int, reason: str):
        all_warnings = dm.get_guild_data(guild.id, f"user_warnings_{user_id}", [])
        for w in all_warnings:
            if not w.get("pardoned"):
                w["pardoned"] = True
                w["pardon_reason"] = f"Mass clear: {reason}"
                w["pardon_timestamp"] = time.time()
        dm.update_guild_data(guild.id, f"user_warnings_{user_id}", all_warnings)
        return len(all_warnings)

    async def _apply_punishment(self, member, action, reason):
        full_reason = f"Warning Threshold: {reason}"
        try:
            if action == "mute_10":
                await member.timeout(timedelta(minutes=10), reason=full_reason)
            elif action == "mute_60":
                await member.timeout(timedelta(hours=1), reason=full_reason)
            elif action == "kick":
                await member.kick(reason=full_reason)
            elif action == "ban":
                await member.ban(reason=full_reason)
        except Exception as e:
            logger.error(f"Failed to apply warning punishment {action}: {e}")

    async def _log_warning(self, guild, user_id, moderator_id, warning, count, action):
        log_ch_id = dm.get_guild_data(guild.id, "log_channel")
        if not log_ch_id: return
        channel = guild.get_channel(log_ch_id)
        if not channel: return

        embed = discord.Embed(title="⚠️ User Warning Issued", color=discord.Color.yellow())
        embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Moderator", value=f"<@{moderator_id}>", inline=True)
        embed.add_field(name="Severity", value=warning["severity"].upper(), inline=True)
        embed.add_field(name="Reason", value=warning["reason"], inline=False)
        embed.add_field(name="Total Active", value=str(count), inline=True)
        if action != "none":
            embed.add_field(name="Action Taken", value=action.upper(), inline=True)
        embed.timestamp = discord.utils.utcnow()
        try: await channel.send(embed=embed)
        except: pass

    # Prefix command handlers
    async def cmd_warn(self, message, parts):
        if not message.author.guild_permissions.manage_messages: return
        if len(parts) < 3:
            return await message.channel.send("Usage: `!warn @user <reason>`")

        target = message.mentions[0] if message.mentions else None
        if not target: return await message.channel.send("User not found.")

        reason = " ".join(parts[2:])
        wid = await self.issue_warning(message.guild, target.id, message.author.id, reason)
        await message.channel.send(f"✅ Warning issued (ID: {wid}) to {target.display_name}")

    async def cmd_warnings(self, message, parts):
        target = message.mentions[0] if message.mentions else message.author
        warns = self.get_warnings(message.guild.id, target.id)

        if not warns:
            return await message.channel.send(f"{target.display_name} has no warnings.")

        embed = discord.Embed(title=f"Warnings for {target.display_name}", color=discord.Color.orange())
        active_warns = [w for w in warns if w.get("active") and not w.get("pardoned")]

        desc = ""
        for w in warns[-10:]:
            status = "✅ Active" if w.get("active") and not w.get("pardoned") else "⚪ Inactive/Pardoned"
            date = datetime.fromtimestamp(w.get("timestamp", 0)).strftime("%Y-%m-%d")
            desc += f"**ID: {w['id']}** | {status} | {w['severity']} | {date}\nReason: {w['reason']}\n\n"

        embed.description = desc
        embed.add_field(name="Total Active", value=str(len(active_warns)))
        await message.channel.send(embed=embed)

    async def cmd_clearwarn(self, message, parts):
        if not message.author.guild_permissions.manage_messages: return
        if len(parts) < 3:
            return await message.channel.send("Usage: `!clearwarn @user <id>`")

        target = message.mentions[0] if message.mentions else None
        if not target: return await message.channel.send("User not found.")

        try: wid = int(parts[2])
        except: return await message.channel.send("Invalid ID.")

        success = await self.pardon_warning(message.guild, target.id, wid, "Manual clear")
        if success: await message.channel.send(f"✅ Warning {wid} pardoned for {target.display_name}")
        else: await message.channel.send("Warning ID not found.")

    async def cmd_clearallwarns(self, message, parts):
        if not message.author.guild_permissions.administrator: return
        if len(parts) < 2:
            return await message.channel.send("Usage: `!clearallwarns @user`")

        target = message.mentions[0] if message.mentions else None
        if not target: return await message.channel.send("User not found.")

        count = await self.clear_all_warnings(message.guild, target.id, "Manual mass clear")
        await message.channel.send(f"✅ Cleared all ({count}) warnings for {target.display_name}")

    async def cmd_kick(self, message, parts):
        if not message.author.guild_permissions.kick_members:
            return await message.channel.send("❌ You need kick permissions to use this command.")

        if len(parts) < 2:
            return await message.channel.send("Usage: `!kick @user [reason]`")

        target = message.mentions[0] if message.mentions else None
        if not target:
            return await message.channel.send("❌ User not found.")

        if target == message.author:
            return await message.channel.send("❌ You cannot kick yourself.")

        if target.guild_permissions.administrator:
            return await message.channel.send("❌ You cannot kick an administrator.")

        reason = " ".join(parts[1:]) if len(parts) > 1 else "No reason provided"

        try:
            await target.kick(reason=reason)
            embed = discord.Embed(
                title="👢 User Kicked",
                description=f"**{target.display_name}** has been kicked from the server.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=message.author.display_name, inline=True)
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            await message.channel.send("❌ I don't have permission to kick this user.")
        except Exception as e:
            logger.error(f"Error kicking user: {e}")
            await message.channel.send("❌ Failed to kick user.")

    async def cmd_ban(self, message, parts):
        if not message.author.guild_permissions.ban_members:
            return await message.channel.send("❌ You need ban permissions to use this command.")

        if len(parts) < 2:
            return await message.channel.send("Usage: `!ban @user [reason]`")

        target = message.mentions[0] if message.mentions else None
        if not target:
            return await message.channel.send("❌ User not found.")

        if target == message.author:
            return await message.channel.send("❌ You cannot ban yourself.")

        if target.guild_permissions.administrator:
            return await message.channel.send("❌ You cannot ban an administrator.")

        reason = " ".join(parts[1:]) if len(parts) > 1 else "No reason provided"

        try:
            await target.ban(reason=reason)
            embed = discord.Embed(
                title="🔨 User Banned",
                description=f"**{target.display_name}** has been banned from the server.",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=message.author.display_name, inline=True)
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            await message.channel.send("❌ I don't have permission to ban this user.")
        except Exception as e:
            logger.error(f"Error banning user: {e}")
            await message.channel.send("❌ Failed to ban user.")

    async def cmd_mute(self, message, parts):
        if not message.author.guild_permissions.moderate_members:
            return await message.channel.send("❌ You need moderate members permissions to use this command.")

        if len(parts) < 3:
            return await message.channel.send("Usage: `!mute @user <duration> [reason]`\nExample: `!mute @user 1h Spamming`")

        target = message.mentions[0] if message.mentions else None
        if not target:
            return await message.channel.send("❌ User not found.")

        if target == message.author:
            return await message.channel.send("❌ You cannot mute yourself.")

        # Parse duration
        duration_str = parts[1]
        try:
            # Simple duration parsing: 1h, 30m, 2d, etc.
            import re
            match = re.match(r'^(\d+)([smhd])$', duration_str.lower())
            if not match:
                return await message.channel.send("❌ Invalid duration format. Use: 30m, 1h, 2d, etc.")

            amount, unit = match.groups()
            amount = int(amount)

            if unit == 's':
                duration = amount
            elif unit == 'm':
                duration = amount * 60
            elif unit == 'h':
                duration = amount * 3600
            elif unit == 'd':
                duration = amount * 86400
            else:
                return await message.channel.send("❌ Invalid time unit. Use: s, m, h, d")

            if duration > 2419200:  # 28 days max
                return await message.channel.send("❌ Maximum mute duration is 28 days.")

        except Exception as e:
            return await message.channel.send("❌ Invalid duration format.")

        reason = " ".join(parts[2:]) if len(parts) > 2 else "No reason provided"

        try:
            await target.timeout(discord.utils.utcnow() + timedelta(seconds=duration), reason=reason)
            embed = discord.Embed(
                title="🔇 User Muted",
                description=f"**{target.display_name}** has been muted.",
                color=discord.Color.yellow()
            )
            embed.add_field(name="Duration", value=duration_str, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=message.author.display_name, inline=True)
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            await message.channel.send("❌ I don't have permission to mute this user.")
        except Exception as e:
            logger.error(f"Error muting user: {e}")
            await message.channel.send("❌ Failed to mute user.")

    async def cmd_modstats(self, message, parts):
        """Show moderation statistics"""
        if not message.author.guild_permissions.administrator:
            return await message.channel.send("❌ Only administrators can view moderation statistics.")

        try:
            # Get warning stats
            warning_stats = self.get_stats(message.guild.id)
            most_warned = self.get_most_warned(message.guild.id)

            embed = discord.Embed(
                title="📊 Moderation Statistics",
                description="Server moderation overview",
                color=discord.Color.blue()
            )

            embed.add_field(
                name="⚠️ Warnings Today",
                value=str(warning_stats.get("today", 0)),
                inline=True
            )
            embed.add_field(
                name="📅 Warnings This Week",
                value=str(warning_stats.get("week", 0)),
                inline=True
            )
            embed.add_field(
                name="🗑️ Total Pardoned",
                value=str(warning_stats.get("total_pardoned", 0)),
                inline=True
            )

            severity_breakdown = warning_stats.get("severity_breakdown", {})
            severity_text = "\n".join([f"{k.title()}: {v}" for k, v in severity_breakdown.items()])
            embed.add_field(
                name="📈 Severity Breakdown",
                value=severity_text or "No data",
                inline=False
            )

            if most_warned:
                most_warned_text = "\n".join([
                    f"{i+1}. <@{entry['user_id']}> - {entry['count']} warnings"
                    for i, entry in enumerate(most_warned[:5])
                ])
                embed.add_field(
                    name="👥 Most Warned Users",
                    value=most_warned_text,
                    inline=False
                )

            embed.set_footer(text=f"Stats for {message.guild.name}")
            await message.channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in cmd_modstats: {e}")
            await message.channel.send("❌ Failed to retrieve moderation statistics.")

    async def setup(self, interaction: discord.Interaction):
        self.save_config(interaction.guild_id, self.get_config(interaction.guild_id))
        return True

    # ---- Slash-command adapters ----

    async def warn_user(self, interaction, user: discord.Member, reason: str, severity: str = "medium"):
        """Slash-command adapter for issuing a warning."""
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ You need **Manage Messages** permission.", ephemeral=True)
        sev = {"low": "minor", "medium": "moderate", "high": "severe"}.get(severity, "moderate")
        await self.issue_warning(interaction.guild, user.id, interaction.user.id, reason, sev)
        embed = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"{user.mention} has been warned.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Severity", value=sev.title(), inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    async def get_user_warnings(self, interaction, user: discord.Member):
        """Slash-command adapter for viewing a user's warnings."""
        warnings = self.get_warnings(interaction.guild.id, user.id)
        active = [w for w in warnings if w.get("active") and not w.get("pardoned")]
        if not warnings:
            return await interaction.response.send_message(f"✅ {user.mention} has no warnings.", ephemeral=True)
        embed = discord.Embed(
            title=f"⚠️ Warnings for {user.display_name}",
            description=f"**{len(active)} active** / {len(warnings)} total",
            color=discord.Color.orange()
        )
        for w in warnings[-10:]:
            active_flag = "❌" if w.get("active") and not w.get("pardoned") else "✅"
            embed.add_field(
                name=f"{active_flag} #{w.get('id')} — {str(w.get('severity', 'minor')).title()}",
                value=f"{w.get('reason', 'No reason')}\n<@{w.get('moderator_id', 0)}> • <t:{int(w.get('timestamp', 0))}:R>",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)



# ======================================================================
# From: modules/automod.py
# ======================================================================

# ======================================================================

import discord
import time
import re
from typing import Dict, List, Optional
from datetime import timedelta
from data_manager import dm
from logger import logger

class AutoModSystem:
    def __init__(self, bot):
        self.bot = bot
        self._message_history: Dict[int, Dict[int, List[float]]] = {} # guild_id -> user_id -> [timestamps]
        self._mention_history: Dict[int, Dict[int, List[float]]] = {}
        self._link_history: Dict[int, Dict[int, List[float]]] = {}
        self._attachment_history: Dict[int, Dict[int, List[float]]] = {}

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "automod_config", {
            "enabled": False,
            "log_channel_id": None,
            "whitelist_channels": [],
            "whitelist_roles": [],
            "rules": {
                "spam": {"enabled": False, "max_messages": 5, "window": 5, "action": "mute"},
                "mentions": {"enabled": False, "max_mentions": 5, "window": 10, "action": "warn"},
                "caps": {"enabled": False, "threshold_pct": 70, "min_chars": 20, "action": "warn"},
                "emojis": {"enabled": False, "max_emojis": 10, "action": "warn"},
                "links": {"enabled": False, "max_links": 3, "window": 10, "action": "warn", "whitelisted_domains": []},
                "invites": {"enabled": False, "action": "warn"},
                "banned_words": {"enabled": False, "words": [], "action": "warn"},
                "zalgo": {"enabled": False, "action": "warn"},
                "mass_ping": {"enabled": False, "action": "warn"},
                "repeated_chars": {"enabled": False, "action": "delete"},
                "new_account": {"enabled": False, "min_age_days": 3, "action": "flag"},
                "attachments": {"enabled": False, "max_attachments": 5, "window": 10, "action": "warn"},
                "newlines": {"enabled": False, "max_newlines": 15, "action": "delete"}
            },
            "escalation": {
                "1": "warn",
                "2": "mute_10",
                "3": "mute_60",
                "4": "kick",
                "5": "ban",
                "reset_hours": 24
            }
        })

    def save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "automod_config", config)

    async def handle_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        config = self.get_config(message.guild.id)
        if not config.get("enabled"):
            return

        if message.author.guild_permissions.administrator:
            return

        if message.channel.id in config.get("whitelist_channels", []):
            return

        for role in message.author.roles:
            if role.id in config.get("whitelist_roles", []):
                return

        content = message.content
        violations = []
        rules = config.get("rules", {})

        # 1. Spam (X messages in Y seconds)
        rule = rules.get("spam", {})
        if rule.get("enabled"):
            if await self._check_spam(message, rule):
                violations.append("Spam")

        # 2. Mention Spam
        rule = rules.get("mentions", {})
        if rule.get("enabled"):
            if await self._check_mentions(message, rule):
                violations.append("Mention Spam")

        # 3. Caps Spam
        rule = rules.get("caps", {})
        if rule.get("enabled"):
            if self._check_caps(content, rule):
                violations.append("Caps Spam")

        # 4. Emoji Spam
        rule = rules.get("emojis", {})
        if rule.get("enabled"):
            if self._check_emojis(content, rule):
                violations.append("Emoji Spam")

        # 5. Link Spam
        rule = rules.get("links", {})
        if rule.get("enabled"):
            if await self._check_links(message, rule):
                violations.append("Link Spam")

        # 6. Discord Invites
        rule = rules.get("invites", {})
        if rule.get("enabled"):
            if await self._check_invites(content, message.guild):
                violations.append("Discord Invite")

        # 7. Banned Words
        rule = rules.get("banned_words", {})
        if rule.get("enabled"):
            if self._check_banned_words(content, rule.get("words", [])):
                violations.append("Banned Word")

        # 8. Zalgo Text
        rule = rules.get("zalgo", {})
        if rule.get("enabled"):
            if self._check_zalgo(content):
                violations.append("Zalgo Text")

        # 9. Mass Ping
        rule = rules.get("mass_ping", {})
        if rule.get("enabled"):
            if "@everyone" in content or "@here" in content:
                violations.append("Mass Ping")

        # 10. Repeated Characters
        rule = rules.get("repeated_chars", {})
        if rule.get("enabled"):
            if re.search(r'(.)\1{9,}', content):
                violations.append("Repeated Characters")

        # 11. New Account
        rule = rules.get("new_account", {})
        if rule.get("enabled"):
            days = (discord.utils.utcnow() - message.author.created_at).days
            if days < rule.get("min_age_days", 3):
                # Special action: flag (log but don't delete yet unless other rules trigger)
                await self._log_violation(message, "New Account Message")

        # 12. Attachment Spam
        rule = rules.get("attachments", {})
        if rule.get("enabled"):
            if await self._check_attachments(message, rule):
                violations.append("Attachment Spam")

        # 13. Newline Spam
        rule = rules.get("newlines", {})
        if rule.get("enabled"):
            if content.count('\n') > rule.get("max_newlines", 15):
                violations.append("Newline Spam")

        if violations:
            await self._process_violations(message, violations, config)

    # --- Detection Helpers ---

    async def _check_spam(self, message, rule):
        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._message_history: self._message_history[gid] = {}
        if uid not in self._message_history[gid]: self._message_history[gid][uid] = []

        window = rule.get("window", 5)
        self._message_history[gid][uid] = [t for t in self._message_history[gid][uid] if now - t < window]
        self._message_history[gid][uid].append(now)
        return len(self._message_history[gid][uid]) >= rule.get("max_messages", 5)

    async def _check_mentions(self, message, rule):
        count = len(message.mentions) + len(message.role_mentions)
        if count >= rule.get("max_mentions", 5): return True

        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._mention_history: self._mention_history[gid] = {}
        if uid not in self._mention_history[gid]: self._mention_history[gid][uid] = []

        window = rule.get("window", 10)
        self._mention_history[gid][uid] = [t for t in self._mention_history[gid][uid] if now - t < window]
        for _ in range(count): self._mention_history[gid][uid].append(now)
        return len(self._mention_history[gid][uid]) >= rule.get("max_mentions", 5)

    def _check_caps(self, content, rule):
        if len(content) < rule.get("min_chars", 20): return False
        caps = sum(1 for c in content if c.isupper())
        pct = (caps / len(content)) * 100
        return pct > rule.get("threshold_pct", 70)

    def _check_emojis(self, content, rule):
        emojis = len(re.findall(r'<a?:\w+:\d+>|[\U00010000-\U0010ffff]', content))
        return emojis > rule.get("max_emojis", 10)

    async def _check_links(self, message, rule):
        links = re.findall(r'https?://[^\s]+', message.content)
        if not links: return False

        whitelisted = rule.get("whitelisted_domains", [])
        filtered_links = []
        for link in links:
            is_whitelisted = False
            for domain in whitelisted:
                if domain.lower() in link.lower():
                    is_whitelisted = True
                    break
            if not is_whitelisted:
                filtered_links.append(link)

        if not filtered_links: return False

        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._link_history: self._link_history[gid] = {}
        if uid not in self._link_history[gid]: self._link_history[gid][uid] = []

        window = rule.get("window", 10)
        self._link_history[gid][uid] = [t for t in self._link_history[gid][uid] if now - t < window]
        for _ in range(len(links)): self._link_history[gid][uid].append(now)
        return len(self._link_history[gid][uid]) >= rule.get("max_links", 3)

    async def _check_invites(self, content, guild):
        invites = re.findall(r'discord(?:\.gg|app\.com/invite)/([a-zA-Z0-9\-]+)', content)
        for code in invites:
            try:
                invite = await self.bot.fetch_invite(code)
                if invite.guild and invite.guild.id != guild.id:
                    return True
            except:
                # If invite is invalid, still might want to block it if it looks like an invite
                return True
        return False

    def _check_banned_words(self, content, words):
        if not words: return False
        content_lower = content.lower()
        for word in words:
            if word.lower() in content_lower:
                return True
        return False

    def _check_zalgo(self, content):
        return bool(re.search(r'[\u0300-\u036F\u0483-\u0489\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]{3,}', content))

    async def _check_attachments(self, message, rule):
        count = len(message.attachments)
        if count == 0: return False

        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._attachment_history: self._attachment_history[gid] = {}
        if uid not in self._attachment_history[gid]: self._attachment_history[gid][uid] = []

        window = rule.get("window", 10)
        self._attachment_history[gid][uid] = [t for t in self._attachment_history[gid][uid] if now - t < window]
        for _ in range(count): self._attachment_history[gid][uid].append(now)
        return len(self._attachment_history[gid][uid]) >= rule.get("max_attachments", 5)

    # --- Punishment Logic ---

    async def _process_violations(self, message, violations, config):
        try:
            await message.delete()
        except:
            pass

        user_violations = dm.get_guild_data(message.guild.id, f"automod_violations_{message.author.id}", {
            "count": 0,
            "last_violation": 0
        })

        now = time.time()
        reset_time = config.get("escalation", {}).get("reset_hours", 24) * 3600

        if now - user_violations["last_violation"] > reset_time:
            user_violations["count"] = 1
        else:
            user_violations["count"] += 1

        user_violations["last_violation"] = now
        dm.update_guild_data(message.guild.id, f"automod_violations_{message.author.id}", user_violations)

        count = str(user_violations["count"])
        action = config.get("escalation", {}).get(count, config.get("escalation", {}).get("5", "ban"))

        await self._apply_punishment(message.author, action, ", ".join(violations))
        await self._log_violation(message, ", ".join(violations), action, user_violations["count"])

    async def _apply_punishment(self, member, action, reason):
        full_reason = f"Auto-Mod: {reason}"
        try:
            if action == "warn":
                await member.send(f"⚠️ **Auto-Mod Warning:** {reason}")
            elif action == "mute_10":
                await member.timeout(timedelta(minutes=10), reason=full_reason)
                try: await member.send(f"🔇 **Auto-Mod Mute (10m):** {reason}")
                except: pass
            elif action == "mute_60":
                await member.timeout(timedelta(hours=1), reason=full_reason)
                try: await member.send(f"🔇 **Auto-Mod Mute (1h):** {reason}")
                except: pass
            elif action == "kick":
                try: await member.send(f"👢 **Auto-Mod Kick:** {reason}")
                except: pass
                await member.kick(reason=full_reason)
            elif action == "ban":
                try: await member.send(f"🔨 **Auto-Mod Ban:** {reason}")
                except: pass
                await member.ban(reason=full_reason)
        except Exception as e:
            logger.error(f"Failed to apply automod punishment {action}: {e}")

    async def _log_violation(self, message, violation_type, action=None, count=None):
        gid = message.guild.id
        # Update Stats
        stats = dm.get_guild_data(gid, "automod_stats", {
            "today": 0,
            "week": 0,
            "types": {},
            "users": {},
            "actions": {},
            "last_reset": time.time()
        })

        now = time.time()
        # Reset daily stats if needed
        if now - stats.get("last_reset", 0) > 86400:
            stats["today"] = 0
            stats["last_reset"] = now

        stats["today"] += 1
        stats["week"] += 1
        stats["types"][violation_type] = stats["types"].get(violation_type, 0) + 1
        stats["users"][str(message.author.id)] = stats["users"].get(str(message.author.id), 0) + 1
        if action:
            stats["actions"][action] = stats["actions"].get(action, 0) + 1

        # Keep track of last 30 actions
        history = dm.get_guild_data(gid, "automod_history", [])
        history.append({
            "ts": now,
            "user": str(message.author),
            "user_id": message.author.id,
            "type": violation_type,
            "action": action or "FLAG",
            "message": message.content[:100]
        })
        dm.update_guild_data(gid, "automod_history", history[-30:])
        dm.update_guild_data(gid, "automod_stats", stats)

        config = self.get_config(gid)
        log_ch_id = config.get("log_channel_id")
        if not log_ch_id: return

        channel = message.guild.get_channel(log_ch_id)
        if not channel: return

        embed = discord.Embed(title="🛡️ Auto-Mod Violation", color=discord.Color.orange())
        embed.add_field(name="User", value=f"{message.author.mention} ({message.author.id})", inline=True)
        embed.add_field(name="Violation", value=violation_type, inline=True)
        if count:
            embed.add_field(name="Violation Count", value=str(count), inline=True)
        if action:
            embed.add_field(name="Action Taken", value=action.upper(), inline=True)
        embed.add_field(name="Message Preview", value=message.content[:500] or "_No content_", inline=False)
        embed.timestamp = discord.utils.utcnow()

        try: await channel.send(embed=embed)
        except: pass

    async def setup(self, interaction: discord.Interaction):
        """Initial setup for Auto-Mod"""
        guild = interaction.guild
        # Create log channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        channel = await guild.create_text_channel("automod-log", overwrites=overwrites)

        config = self.get_config(guild.id)
        config["log_channel_id"] = channel.id
        config["enabled"] = True
        self.save_config(guild.id, config)

        return True



# ======================================================================

# From: modules/appeals.py
# ======================================================================

import discord
from discord import ui, Interaction, TextStyle, Embed, ButtonStyle
from data_manager import dm
import datetime
import time
import json
from typing import List, Dict, Optional, Any
from logger import logger

class BanAppealModal(ui.Modal, title="Submit Ban Appeal"):
    q1 = ui.TextInput(label="Why were you banned?", style=TextStyle.paragraph, required=True, max_length=1000)
    q2 = ui.TextInput(label="Why should you be unbanned?", style=TextStyle.paragraph, required=True, max_length=1000)
    q3 = ui.TextInput(label="What will you do differently?", style=TextStyle.paragraph, required=True, max_length=1000)
    q4 = ui.TextInput(label="Any evidence to provide?", style=TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "appeals_config", {})
        
        # Save appeal to guild_data
        appeal_id = f"{interaction.user.id}_{int(time.time())}"
        appeal_data = {
            "id": appeal_id,
            "user_id": interaction.user.id,
            "username": str(interaction.user),
            "timestamp": time.time(),
            "status": "pending",
            "answers": {
                "why_banned": self.q1.value,
                "why_unban": self.q2.value,
                "different": self.q3.value,
                "evidence": self.q4.value
            }
        }
        
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        if str(interaction.user.id) not in appeals:
            appeals[str(interaction.user.id)] = []
        appeals[str(interaction.user.id)].append(appeal_data)
        dm.update_guild_data(guild_id, "appeals", appeals)
        
        # Post to #appeals-log
        log_channel_id = config.get("log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None
        
        if log_channel:
            embed = Embed(title="⚖️ New Ban Appeal Received", color=discord.Color.orange())
            embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)

            embed.add_field(name="Why were you banned?", value=self.q1.value[:1024], inline=False)
            embed.add_field(name="Why should you be unbanned?", value=self.q2.value[:1024], inline=False)
            embed.add_field(name="What will you do differently?", value=self.q3.value[:1024], inline=False)
            embed.add_field(name="Evidence", value=self.q4.value[:1024] or "None provided", inline=False)

            history = appeals.get(str(interaction.user.id), [])
            embed.add_field(name="Appeal History", value=f"This is appeal #{len(history)} from this user.")

            embed.set_footer(text=f"Appeal ID: {appeal_id}")

            view = AppealReviewView()
            await log_channel.send(embed=embed, view=view)

            # Ping reviewer role
            reviewer_role_id = config.get("reviewer_role_id")
            if reviewer_role_id:
                await log_channel.send(f"<@&{reviewer_role_id}> New ban appeal submitted!", delete_after=5)
        
        # DM user
        try:
            await interaction.user.send("Your appeal has been received. You will be notified of the decision.")
        except:
            pass

        await interaction.response.send_message("✅ Your appeal has been submitted and staff have been notified.", ephemeral=True)

class AppealPersistentView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @ui.button(label="Submit Appeal", style=ButtonStyle.primary, custom_id="appeal_submit_button")
    async def submit_appeal(self, interaction: Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "appeals_config", {})
        
        # Check blacklist
        blacklist = dm.get_guild_data(guild_id, "appeals_blacklist", [])
        if interaction.user.id in blacklist:
            return await interaction.response.send_message("❌ You are blacklisted from submitting appeals.", ephemeral=True)

        # Enforce cooldown
        cooldown_days = config.get("cooldown_days", 30)
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_appeals = appeals.get(str(interaction.user.id), [])
        
        if user_appeals:
            last_appeal = user_appeals[-1]
            elapsed_days = (time.time() - last_appeal.get("timestamp", 0)) / (24 * 3600)
            if elapsed_days < cooldown_days:
                remaining = cooldown_days - elapsed_days
                return await interaction.response.send_message(f"❌ You must wait {remaining:.1f} more days before appealing again.", ephemeral=True)
        
        await interaction.response.send_modal(BanAppealModal())

class AppealReviewView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_appeal_info(self, embed: Embed):
        footer = embed.footer.text
        if footer and "Appeal ID: " in footer:
            return footer.replace("Appeal ID: ", "").split("_")
        return None, None

    async def _update_appeal_status(self, interaction: Interaction, status: str, staff_note: str = None):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        if not user_id_str:
            return None

        guild_id = interaction.guild_id
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_appeals = appeals.get(user_id_str, [])
        
        target_app = None
        for app in user_appeals:
            if str(int(app["timestamp"])) == ts_str:
                app["status"] = status
                if staff_note:
                    if "staff_notes" not in app: app["staff_notes"] = []
                    app["staff_notes"].append(staff_note)
                target_app = app
                break
        
        if target_app:
            dm.update_guild_data(guild_id, "appeals", appeals)
            return target_app
        return None

    @ui.button(label="Approve", style=ButtonStyle.success, emoji="✅", custom_id="appeal_review_approve")
    async def approve(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        await interaction.response.send_modal(ApproveModal(user_id_str, ts_str))

    @ui.button(label="Deny", style=ButtonStyle.danger, emoji="❌", custom_id="appeal_review_deny")
    async def deny(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        await interaction.response.send_modal(DenyModal(user_id_str, ts_str))

    @ui.button(label="Escalate", style=ButtonStyle.secondary, emoji="⏸️", custom_id="appeal_review_escalate")
    async def escalate(self, interaction: Interaction, button: ui.Button):
        config = dm.get_guild_data(interaction.guild_id, "appeals_config", {})
        reviewer_role_id = config.get("reviewer_role_id")
        
        embed = interaction.message.embeds[0]
        embed.title = "⚖️ [ESCALATED] Ban Appeal"
        embed.color = discord.Color.dark_red()
        
        await interaction.message.edit(embed=embed)
        
        msg = "⏸️ Appeal escalated to senior staff."
        if reviewer_role_id:
            msg += f" <@&{reviewer_role_id}>"
        
        await interaction.response.send_message(msg, ephemeral=False)

    @ui.button(label="Check Ban Reason", style=ButtonStyle.secondary, emoji="🔍", custom_id="appeal_review_ban_reason")
    async def check_ban_reason(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_appeal_info(interaction.message.embeds[0])
        try:
            ban_entry = await interaction.guild.fetch_ban(discord.Object(id=int(user_id_str)))
            await interaction.response.send_message(f"🔍 **Original Ban Reason:** {ban_entry.reason or 'No reason provided.'}", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ User is not currently banned.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error fetching ban reason: {e}", ephemeral=True)

    @ui.button(label="View History", style=ButtonStyle.secondary, emoji="📋", custom_id="appeal_review_history")
    async def view_history(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_appeal_info(interaction.message.embeds[0])
        appeals = dm.get_guild_data(interaction.guild_id, "appeals", {})
        user_appeals = appeals.get(user_id_str, [])
        
        if not user_appeals:
            return await interaction.response.send_message("No previous appeals found.", ephemeral=True)

        desc = ""
        for app in user_appeals:
            status_emoji = {"accepted": "✅", "denied": "❌", "pending": "⏳", "on_hold": "🕐"}.get(app["status"], "❓")
            desc += f"{status_emoji} **{app['status'].title()}** - <t:{int(app['timestamp'])}:R> (ID: `{app['id']}`)\n"

        embed = Embed(title=f"Appeal History: {user_id_str}", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Request Info", style=ButtonStyle.secondary, emoji="💬", custom_id="appeal_review_info")
    async def request_info(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        await interaction.response.send_modal(RequestInfoModal(user_id_str, ts_str))

    @ui.button(label="Put on Hold", style=ButtonStyle.secondary, emoji="🕐", custom_id="appeal_review_hold")
    async def hold(self, interaction: Interaction, button: ui.Button):
        app = await self._update_appeal_status(interaction, "on_hold")
        if not app: return
        
        config = dm.get_guild_data(interaction.guild_id, "appeals_config", {})
        user = interaction.guild.get_member(int(app["user_id"]))
        if user:
            try: await user.send("Your appeal needs more time and has been put on hold.")
            except: pass
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.gold()
        embed.add_field(name="Status", value=f"🕐 Put on hold by {interaction.user.mention}", inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("🕐 Appeal put on hold.", ephemeral=True)

    @ui.button(label="Blacklist", style=ButtonStyle.danger, emoji="🚫", custom_id="appeal_review_blacklist")
    async def blacklist(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_appeal_info(interaction.message.embeds[0])
        blacklist = dm.get_guild_data(interaction.guild_id, "appeals_blacklist", [])
        if int(user_id_str) not in blacklist:
            blacklist.append(int(user_id_str))
            dm.update_guild_data(interaction.guild_id, "appeals_blacklist", blacklist)
            await interaction.response.send_message(f"🚫 User <@{user_id_str}> has been blacklisted from appeals.", ephemeral=True)
        else:
            await interaction.response.send_message("User is already blacklisted.", ephemeral=True)

class ApproveModal(ui.Modal, title="Approve Appeal"):
    note = ui.TextInput(label="Optional note to user", style=TextStyle.paragraph, required=False, max_length=500)
    
    def __init__(self, user_id_str, ts_str):
        super().__init__()
        self.user_id_str = user_id_str
        self.ts_str = ts_str

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_apps = appeals.get(self.user_id_str, [])
        
        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                app["status"] = "accepted"
                target_app = app
                break
        
        if target_app:
            dm.update_guild_data(guild_id, "appeals", appeals)
            
            # Unban user
            try:
                await interaction.guild.unban(discord.Object(id=int(self.user_id_str)), reason=f"Appeal accepted by {interaction.user}")
            except Exception as e:
                logger.error(f"Failed to unban user {self.user_id_str}: {e}")

            # DM user
            config = dm.get_guild_data(guild_id, "appeals_config", {})
            user = await interaction.client.fetch_user(int(self.user_id_str))
            if user:
                invite = ""
                # Try to generate an invite if configured
                try:
                    channels = interaction.guild.text_channels
                    if channels:
                        inv = await channels[0].create_invite(max_uses=1, unique=True)
                        invite = inv.url
                except: pass

                msg = config.get("approval_dm", "Your appeal has been accepted! You have been unbanned. {invite}").format(
                    user=user.name, invite=invite
                )
                try: await user.send(msg)
                except: pass

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.add_field(name="Decision", value=f"✅ Approved by {interaction.user.mention}\nNote: {self.note.value or 'None'}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"✅ Approved appeal and unbanned <@{self.user_id_str}>.", ephemeral=True)

class DenyModal(ui.Modal, title="Deny Appeal"):
    reason = ui.TextInput(label="Reason for Denial", style=TextStyle.paragraph, required=True, max_length=1000)
    
    def __init__(self, user_id_str, ts_str):
        super().__init__()
        self.user_id_str = user_id_str
        self.ts_str = ts_str

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_apps = appeals.get(self.user_id_str, [])
        
        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                app["status"] = "denied"
                app["deny_reason"] = self.reason.value
                target_app = app
                break
        
        if target_app:
            dm.update_guild_data(guild_id, "appeals", appeals)

            # DM user
            config = dm.get_guild_data(guild_id, "appeals_config", {})
            user = await interaction.client.fetch_user(int(self.user_id_str))
            if user:
                cooldown_days = config.get("cooldown_days", 30)
                next_date = (datetime.datetime.now() + datetime.timedelta(days=cooldown_days)).strftime("%Y-%m-%d")

                msg = config.get("denial_dm", "Your appeal was denied. Reason: {reason}\nYou can appeal again after {next_date}.").format(
                    user=user.name, reason=self.reason.value, next_date=next_date
                )
                try: await user.send(msg)
                except: pass

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.add_field(name="Decision", value=f"❌ Denied by {interaction.user.mention}\nReason: {self.reason.value}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"❌ Denied appeal for <@{self.user_id_str}>.", ephemeral=True)

class RequestInfoModal(ui.Modal, title="Request More Info"):
    question = ui.TextInput(label="Question", style=TextStyle.paragraph, required=True, max_length=1000)

    def __init__(self, user_id_str, ts_str):
        super().__init__()
        self.user_id_str = user_id_str
        self.ts_str = ts_str

    async def on_submit(self, interaction: Interaction):
        user = await interaction.client.fetch_user(int(self.user_id_str))
        if user:
            try: await user.send(f"Staff have requested more information regarding your appeal:\n\n> {self.question.value}")
            except: pass
        
        embed = interaction.message.embeds[0]
        embed.add_field(name="Info Requested", value=f"By {interaction.user.mention}: {self.question.value}", inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Information requested.", ephemeral=True)

class AppealSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_persistent_views(self):
        return [AppealPersistentView(), AppealReviewView()]

    async def create_appeal(self, interaction):
        """Slash-command adapter: open the ban appeal modal."""
        await interaction.response.send_modal(BanAppealModal())

    async def setup(self, interaction: Interaction):
        """Standard setup for appeals system."""
        guild = interaction.guild
        
        # Create category
        category = await guild.create_category("Appeals")
        
        # Create #appeals
        appeals_ch = await guild.create_text_channel("appeals", category=category)
        
        # Create #appeals-log (private)
        log_ch = await guild.create_text_channel("appeals-log", category=category)
        await log_ch.set_permissions(guild.default_role, read_messages=False)
        
        # Initial config
        config = {
            "appeals_channel_id": appeals_ch.id,
            "log_channel_id": log_ch.id,
            "cooldown_days": 30,
            "reviewer_role_id": None,
            "questions": [
                "Why were you banned?",
                "Why should you be unbanned?",
                "What will you do differently?",
                "Any evidence to provide?"
            ]
        }
        dm.update_guild_data(guild.id, "appeals_config", config)
        
        # Post panel to #appeals
        embed = Embed(title="⚖️ Moderation Appeals", description="If you have been banned or punished and wish to appeal, click the button below.", color=discord.Color.blue())
        await appeals_ch.send(embed=embed, view=AppealPersistentView())
        
        return True

async def appeals_extension_setup(bot):
    # This function is kept for compatibility but the actual setup is handled in bot.py
    # to ensure proper initialization order
    return True
