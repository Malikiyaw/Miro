import discord
from discord.ext import commands
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
