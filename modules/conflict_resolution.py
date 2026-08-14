import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from data_manager import dm
from logger import logger


class ConflictSeverity(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ConflictType(Enum):
    HEATED_DEBATE = "heated_debate"
    PERSONAL_ATTACKS = "personal_attacks"
    RULE_SKATING = "rule_skating"
    GANGING_UP = "ganging_up"
    MISUNDERSTANDING = "misunderstanding"
    REPEATED_ARGUMENT = "repeated_argument"


class InterventionStrategy(Enum):
    SUGGEST_BREAK = "suggest_break"
    REDIRECT_TOPIC = "redirect_topic"
    MEDIATE_PRIVATE = "mediate_private"
    REMIND_RULES = "remind_rules"
    SEPARATE_PARTICIPANTS = "separate_participants"
    NOTIFY_MODERATOR = "notify_moderator"
    SUMMARIZE_BOTH_SIDES = "summarize_both_sides"
    OFFER_MEDIATION = "offer_mediation"


@dataclass
class ConflictData:
    channel_id: int
    guild_id: int
    participants: List[int]
    conflict_type: Optional[ConflictType]
    severity: ConflictSeverity
    start_time: float
    messages_analyzed: int = 0
    interventions_attempted: int = 0
    resolved: bool = False


@dataclass
class InterventionRecord:
    timestamp: float
    guild_id: int
    channel_id: int
    conflict_type: Optional[str]
    participants: List[int]
    strategy: str
    immediate_result: str
    follow_up_needed: bool
    long_term_outcome: Optional[str] = None


class ConflictResolution:
    def __init__(self, bot):
        self.bot = bot
        self.active_conflicts: Dict[int, ConflictData] = {}
        self._guild_configs: Dict[int, dict] = {}
        self._tension_scores: Dict[int, List[float]] = {}
        self._message_history: Dict[int, List[dict]] = {}
        self._cooldowns: Dict[int, float] = {}
        self._cooldown_seconds = 30

    def get_config(self, guild_id: int) -> dict:
        if guild_id in self._guild_configs:
            return self._guild_configs[guild_id]
        
        config = dm.get_guild_data(guild_id, "conflict_resolution_config", {
            "enabled": True,
            "sensitivity": "medium",
            "auto_intervene": True,
            "notify_mods": True,
            "exempt_channels": [],
            "exempt_roles": [],
            "intervention_aggressiveness": "balanced",
            "min_participants": 2,
            "max_interventions_per_hour": 10
        })
        
        sensitivity_map = {"low": 0.8, "medium": 0.6, "high": 0.4, "critical": 0.2}
        config["_sensitivity_threshold"] = sensitivity_map.get(config.get("sensitivity", "medium"), 0.6)
        
        self._guild_configs[guild_id] = config
        return config

    def update_config(self, guild_id: int, key: str, value):
        config = self.get_config(guild_id)
        config[key] = value
        self._guild_configs[guild_id] = config
        dm.update_guild_data(guild_id, "conflict_resolution_config", config)

    async def analyze_message(self, message: discord.Message) -> bool:
        if message.author.bot:
            return False
        
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        
        if not config.get("enabled", True):
            return False
        
        channel_id = message.channel.id
        if channel_id in config.get("exempt_channels", []):
            return False
        
        user = message.author
        for role in user.roles:
            if role.id in config.get("exempt_roles", []):
                return False
        
        now = time.time()
        if channel_id in self._cooldowns:
            if now - self._cooldowns[channel_id] < self._cooldown_seconds:
                return False
        
        await self._add_to_history(channel_id, message)
        
        recent_messages = self._message_history.get(channel_id, [])
        if len(recent_messages) < 5:
            return False
        
        analysis_result = await self._analyze_tension(message.channel, recent_messages)
        
        if analysis_result["tension_score"] > config.get("_sensitivity_threshold", 0.6):
            if not await self.moderation_has_violation(message):
                await self._proactive_intervene(message, analysis_result)
                self._cooldowns[channel_id] = time.time()
                return True
        
        return False

    async def _add_to_history(self, channel_id: int, message: discord.Message):
        if channel_id not in self._message_history:
            self._message_history[channel_id] = []
        
        msg_data = {
            "author_id": message.author.id,
            "content": message.content,
            "timestamp": message.created_at.timestamp()
        }
        
        self._message_history[channel_id].append(msg_data)
        self._message_history[channel_id] = self._message_history[channel_id][-50:]

    async def _analyze_tension(self, channel: discord.TextChannel, messages: List[dict]) -> dict:
        prompt = f"""Analyze the last {len(messages)} messages in #{channel.name} for conflict indicators.

Recent messages:
{chr(10).join([f"User {m['author_id']}: {m['content'][:200]}" for m in messages[-10:]])}

Analyze for:
1. Tension score (0.0-1.0): How heated is the conversation?
2. Conflict type:heated_debate, personal_attacks, rule_skating, ganging_up, misunderstanding, repeated_argument, or none
3. Participants: Which user IDs are involved
4. Severity: low, medium, high, critical
5. Should intervene: boolean

Respond in JSON format:
{{"tension_score": 0.0-1.0, "conflict_type": "type or null", "participants": [user_ids], "severity": "level", "should_intervene": true/false, "reason": "brief explanation"}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=channel.guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You are a conflict analysis system. Analyze conversations for tension and potential conflicts. Be accurate and brief."
            )
            
            summary = result.get("summary", "")
            import re
            json_match = re.search(r'\{.*\}', summary, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "tension_score": float(data.get("tension_score", 0.5)),
                    "conflict_type": data.get("conflict_type"),
                    "participants": [int(p) for p in data.get("participants", [])],
                    "severity": data.get("severity", "medium"),
                    "should_intervene": data.get("should_intervene", False),
                    "reason": data.get("reason", "")
                }
        except Exception as e:
            logger.error(f"Error analyzing tension: {e}")
        
        return {
            "tension_score": 0.5,
            "conflict_type": None,
            "participants": [],
            "severity": "medium",
            "should_intervene": False,
            "reason": "Analysis failed"
        }

    async def moderation_has_violation(self, message: discord.Message) -> bool:
        content_lower = message.content.lower()
        violation_keywords = ["spam", "scam", "nazi", "explicit", "illegal"]
        return any(kw in content_lower for kw in violation_keywords)

    async def _proactive_intervene(self, message: discord.Message, analysis: dict):
        guild_id = message.guild.id
        channel = message.channel
        config = self.get_config(guild_id)
        
        participants = analysis.get("participants", [])
        if not participants:
            participants = [message.author.id]
        
        conflict_type = analysis.get("conflict_type")
        severity = analysis.get("severity", "medium")
        
        strategy = self._select_strategy(conflict_type, severity, participants, config)
        
        await self._execute_intervention(channel, participants, strategy, conflict_type, severity)
        
        await self._record_intervention(
            guild_id=guild_id,
            channel_id=channel.id,
            conflict_type=conflict_type,
            participants=participants,
            strategy=strategy
        )
        
        if channel.id not in self.active_conflicts:
            self.active_conflicts[channel.id] = ConflictData(
                channel_id=channel.id,
                guild_id=guild_id,
                participants=participants,
                conflict_type=conflict_type,
                severity=ConflictSeverity[severity.upper()],
                start_time=time.time()
            )
        else:
            self.active_conflicts[channel.id].interventions_attempted += 1

    def _select_strategy(self, conflict_type: Optional[str], severity: str, participants: List[int], config: dict) -> str:
        strategies_by_type = {
            "heated_debate": ["suggest_break", "redirect_topic", "summarize_both_sides"],
            "personal_attacks": ["mediate_private", "remind_rules", "notify_moderator"],
            "rule_skating": ["remind_rules", "offer_mediation"],
            "ganging_up": ["separate_participants", "notify_moderator"],
            "repeated_argument": ["suggest_break", "redirect_topic"],
            "misunderstanding": ["summarize_both_sides", "offer_mediation"]
        }
        
        available = strategies_by_type.get(conflict_type, ["suggest_break", "redirect_topic"])
        
        learned_strategies = self._get_learned_strategies(conflict_type)
        if learned_strategies:
            available = learned_strategies + available
        
        return available[0]

    def _get_learned_strategies(self, conflict_type: Optional[str]) -> List[str]:
        if not conflict_type:
            return []
        
        try:
            outcomes = dm.load_json("conflict_outcomes", default={})
            if not outcomes:
                return []
            
            strategy_success = {}
            for entry in outcomes:
                if entry.get("conflict_type") == conflict_type:
                    strategy = entry.get("strategy", "")
                    result = entry.get("long_term_outcome", "")
                    if strategy and result == "resolved":
                        strategy_success[strategy] = strategy_success.get(strategy, 0) + 1
            
            if strategy_success:
                sorted_strategies = sorted(strategy_success.items(), key=lambda x: x[1], reverse=True)
                return [s[0] for s in sorted_strategies[:2]]
        except:
            pass
        
        return []

    async def _execute_intervention(self, channel: discord.TextChannel, participants: List[int], 
                                     strategy: str, conflict_type: Optional[str], severity: str):
        member = channel.guild.me
        
        strategy_templates = {
            "suggest_break": f"I've noticed this discussion is getting a bit heated. Maybe consider taking a break or continuing in a dedicated channel?",
            "redirect_topic": f"Let's keep #{channel.name} on-topic. For debates, try #debate-channel!",
            "mediate_private": "I'd love to help mediate this - would you both like to move to DMs or a private thread?",
            "remind_rules": f"Remember to keep discussions respectful! Check {channel.guild.rules_channel.mention if channel.guild.rules_channel else '#rules'} for guidelines.",
            "separate_participants": "Let's give each side some space. Perhaps continue in separate threads?",
            "summarize_both_sides": "I'd like to summarize what I've heard - does this sound accurate?",
            "offer_mediation": "Would you like me to help find common ground?",
            "notify_moderator": ""
        }
        
        message = strategy_templates.get(strategy, "")
        
        if strategy == "notify_moderator":
            config = self.get_config(channel.guild.id)
            if config.get("notify_mods", True):
                log_channel_id = dm.get_guild_data(channel.guild.id, "log_channel")
                if log_channel_id:
                    log_channel = channel.guild.get_channel(log_channel_id)
                    if log_channel:
                        embed = discord.Embed(
                            title="⚠️ Potential Conflict Detected",
                            description=f"Tension detected in #{channel.name}",
                            color=discord.Color.orange()
                        )
                        embed.add_field(name="Participants", value=f"<@{'> <@'.join(map(str, participants))}>", inline=False)
                        if conflict_type:
                            embed.add_field(name="Type", value=conflict_type, inline=True)
                        embed.add_field(name="Severity", value=severity, inline=True)
                        await log_channel.send(embed=embed)
        elif message:
            await channel.send(f"💡 {message}", delete_after=30)

    async def _record_intervention(self, guild_id: int, channel_id: int, conflict_type: Optional[str],
                                    participants: List[int], strategy: str):
        record = {
            "timestamp": time.time(),
            "guild_id": guild_id,
            "channel_id": channel_id,
            "conflict_type": conflict_type,
            "participants": participants,
            "strategy": strategy,
            "immediate_result": "pending",
            "follow_up_needed": False
        }
        
        outcomes = dm.load_json("conflict_outcomes", default=[])
        if not isinstance(outcomes, list):
            outcomes = []
        outcomes.append(record)
        outcomes = outcomes[-200:]
        dm.save_json("conflict_outcomes", outcomes)
        
        await self._learn_from_outcome(record)

    async def _learn_from_outcome(self, record: dict):
        await asyncio.sleep(3600)
        
        channel_id = record.get("channel_id")
        if channel_id in self.active_conflicts:
            conflict = self.active_conflicts[channel_id]
            
            if conflict.resolved or conflict.interventions_attempted >= 3:
                final_outcome = "resolved" if conflict.resolved else "recurring"
                
                outcomes = dm.load_json("conflict_outcomes", default=[])
                if isinstance(outcomes, list):
                    for i, entry in enumerate(outcomes):
                        if entry.get("channel_id") == channel_id and entry.get("timestamp") == record.get("timestamp"):
                            outcomes[i]["long_term_outcome"] = final_outcome
                            break
                    dm.save_json("conflict_outcomes", outcomes)
                
                if final_outcome == "resolved":
                    await self._store_successful_pattern(record)
                
                del self.active_conflicts[channel_id]

    async def _store_successful_pattern(self, record: dict):
        from vector_memory import vector_memory
        
        vector_memory.store_conversation(
            guild_id=record["guild_id"],
            user_id=0,
            user_message=f"RESOLVED_CONFLICT: {record.get('conflict_type', 'unknown')}",
            bot_response=f"Used {record['strategy']} with success",
            reasoning="Successful conflict resolution pattern",
            walkthrough="Learned from intervention outcome",
            importance_score=0.85
        )

    async def check_resolution_status(self, channel_id: int) -> bool:
        conflict = self.active_conflicts.get(channel_id)
        if not conflict:
            return True
        
        messages = self._message_history.get(channel_id, [])
        if len(messages) < 3:
            return True
        
        recent_tension = sum([1 for m in messages[-5:] if any(
            kw in m.get("content", "").lower() 
            for kw in ["calm", "agree", "sorry", "understand", "thanks", "ok", "okay"]
        )])
        
        if recent_tension >= 3:
            conflict.resolved = True
            return True
        
        return False

    async def get_metrics(self, guild_id: int) -> dict:
        outcomes = dm.load_json("conflict_outcomes", default=[])
        if not isinstance(outcomes, list):
            outcomes = []
        
        guild_outcomes = [o for o in outcomes if o.get("guild_id") == guild_id]
        
        total = len(guild_outcomes)
        resolved = sum(1 for o in guild_outcomes if o.get("long_term_outcome") == "resolved")
        
        strategy_stats = {}
        for o in guild_outcomes:
            strat = o.get("strategy", "unknown")
            if strat not in strategy_stats:
                strategy_stats[strat] = {"total": 0, "resolved": 0}
            strategy_stats[strat]["total"] += 1
            if o.get("long_term_outcome") == "resolved":
                strategy_stats[strat]["resolved"] += 1
        
        return {
            "total_interventions": total,
            "resolved": resolved,
            "success_rate": resolved / total if total > 0 else 0,
            "strategy_stats": strategy_stats
        }
