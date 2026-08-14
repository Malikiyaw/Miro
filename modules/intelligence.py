import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict

from data_manager import dm
from logger import logger


@dataclass
class UserActivity:
    user_id: int
    messages_sent: int
    voice_time: int
    commands_used: int
    last_active: float
    joined_at: float
    interaction_scores: List[float]


@dataclass
class ServerMetrics:
    guild_id: int
    total_members: int
    active_members: int
    messages_today: int
    commands_today: int
    new_members_today: int
    left_members_today: int
    avg_response_time: float
    ai_interactions: int
    engagement_score: float


class ServerIntelligence:
    def __init__(self, bot):
        self.bot = bot
        self._activity_data: Dict[int, Dict[int, UserActivity]] = {}
        self._topic_trends: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        saved_data = dm.load_json("server_intelligence", default={})
        
        for guild_id_str, guild_data in saved_data.items():
            try:
                guild_id = int(guild_id_str)
                self._activity_data[guild_id] = {}
                
                for user_id_str, user_data in guild_data.get("users", {}).items():
                    self._activity_data[guild_id][int(user_id_str)] = UserActivity(
                        user_id=int(user_id_str),
                        messages_sent=user_data.get("messages_sent", 0),
                        voice_time=user_data.get("voice_time", 0),
                        commands_used=user_data.get("commands_used", 0),
                        last_active=user_data.get("last_active", 0),
                        joined_at=user_data.get("joined_at", time.time()),
                        interaction_scores=user_data.get("interaction_scores", [])
                    )
            except Exception as e:
                logger.error(f"Failed to load intelligence data for guild {guild_id_str}: {e}")

    def _save_data(self):
        data = {}
        
        for guild_id, users in self._activity_data.items():
            data[str(guild_id)] = {
                "users": {}
            }
            
            for user_id, activity in users.items():
                data[str(guild_id)]["users"][str(user_id)] = {
                    "messages_sent": activity.messages_sent,
                    "voice_time": activity.voice_time,
                    "commands_used": activity.commands_used,
                    "last_active": activity.last_active,
                    "joined_at": activity.joined_at,
                    "interaction_scores": activity.interaction_scores
                }
        
        dm.save_json("server_intelligence", data)

    def start_monitoring(self):
        asyncio.create_task(self._intelligence_monitor_loop())

    async def _intelligence_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                await self._analyze_server_health()
                await self._detect_topic_trends()
                await self._identify_at_risk_members()
            except Exception as e:
                logger.error(f"Intelligence monitor error: {e}")
            
            await asyncio.sleep(300)

    async def _analyze_server_health(self):
        for guild in self.bot.guilds:
            metrics = await self.get_server_metrics(guild.id)
            
            health_data = dm.get_guild_data(guild.id, "server_health", {})
            health_data["last_check"] = time.time()
            health_data["engagement_score"] = metrics.engagement_score
            health_data["active_members"] = metrics.active_members
            health_data["messages_today"] = metrics.messages_today
            
            dm.update_guild_data(guild.id, "server_health", health_data)

    async def _detect_topic_trends(self):
        for guild in self.bot.guilds:
            command_usage = dm.get_guild_data(guild.id, "command_usage", {})
            
            recent_commands = []
            for cmd, data in command_usage.items():
                last_used = data.get("last_used", 0)
                if time.time() - last_used < 86400:
                    recent_commands.append({"command": cmd, "uses": data.get("count", 0), "last_used": last_used})
            
            self._topic_trends[guild.id] = recent_commands

    async def _identify_at_risk_members(self):
        for guild in self.bot.guilds:
            if guild.id not in self._activity_data:
                continue
            
            at_risk = []
            cutoff = time.time() - (7 * 24 * 60 * 60)
            
            for user_id, activity in self._activity_data[guild.id].items():
                if activity.last_active < cutoff:
                    member = guild.get_member(user_id)
                    if member:
                        days_inactive = int((time.time() - activity.last_active) / 86400)
                        at_risk.append({"user_id": user_id, "days_inactive": days_inactive, "join_date": member.joined_at})
            
            risk_data = dm.get_guild_data(guild.id, "at_risk_members", {})
            risk_data["members"] = at_risk
            risk_data["last_updated"] = time.time()
            dm.update_guild_data(guild.id, "at_risk_members", risk_data)

    async def get_server_metrics(self, guild_id: int) -> ServerMetrics:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return ServerMetrics(guild_id, 0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        
        total_members = guild.member_count
        active_members = 0
        
        if guild_id in self._activity_data:
            cutoff = time.time() - (24 * 60 * 60)
            active_members = sum(1 for a in self._activity_data[guild_id].values() if a.last_active > cutoff)
        
        messages_today = 0
        commands_today = 0
        
        command_usage = dm.get_guild_data(guild_id, "command_usage", {})
        for cmd, data in command_usage.items():
            last_used = data.get("last_used", 0)
            if time.time() - last_used < 86400:
                commands_today += data.get("count", 0)
        
        health_data = dm.get_guild_data(guild_id, "server_health", {})
        messages_today = health_data.get("messages_today", 0)
        
        new_members = len([m for m in guild.members if m.joined_at and (discord.utils.utcnow() - m.joined_at).days == 0])
        
        engagement_score = self._calculate_engagement_score(total_members, active_members, messages_today, commands_today)
        
        return ServerMetrics(
            guild_id=guild_id,
            total_members=total_members,
            active_members=active_members,
            messages_today=messages_today,
            commands_today=commands_today,
            new_members_today=new_members,
            left_members_today=0,
            avg_response_time=0.0,
            ai_interactions=0,
            engagement_score=engagement_score
        )

    def _calculate_engagement_score(self, total_members: int, active_members: int, 
                                    messages: int, commands: int) -> float:
        if total_members == 0:
            return 0.0
        
        activity_rate = active_members / total_members
        message_rate = messages / max(active_members, 1)
        command_rate = commands / max(active_members, 1)
        
        score = (activity_rate * 40) + (min(message_rate / 10, 1) * 30) + (min(command_rate / 5, 1) * 30)
        
        return min(100.0, score)

    async def track_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        guild_id = message.guild.id
        user_id = message.author.id
        
        if guild_id not in self._activity_data:
            self._activity_data[guild_id] = {}
        
        if user_id not in self._activity_data[guild_id]:
            self._activity_data[guild_id][user_id] = UserActivity(
                user_id=user_id,
                messages_sent=0,
                voice_time=0,
                commands_used=0,
                last_active=time.time(),
                joined_at=message.author.joined_at.timestamp() if message.author.joined_at else time.time(),
                interaction_scores=[]
            )
        
        activity = self._activity_data[guild_id][user_id]
        activity.messages_sent += 1
        activity.last_active = time.time()
        
        if time.time() % 60 == 0:
            self._save_data()

    def get_topic_trends(self, guild_id: int) -> List[dict]:
        return self._topic_trends.get(guild_id, [])

    def get_at_risk_members(self, guild_id: int) -> List[dict]:
        risk_data = dm.get_guild_data(guild_id, "at_risk_members", {})
        return risk_data.get("members", [])

    async def generate_health_report(self, guild_id: int) -> discord.Embed:
        metrics = await self.get_server_metrics(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        embed = discord.Embed(
            title=f"📊 Server Intelligence Report: {guild.name}",
            color=discord.Color.blue()
        )
        
        health_emoji = "🟢" if metrics.engagement_score >= 70 else "🟡" if metrics.engagement_score >= 40 else "🔴"
        
        embed.add_field(
            name=f"{health_emoji} Engagement Score",
            value=f"**{metrics.engagement_score:.1f}**/100",
            inline=True
        )
        embed.add_field(
            name="👥 Members",
            value=f"{metrics.active_members}/{metrics.total_members} active",
            inline=True
        )
        embed.add_field(
            name="💬 Messages (24h)",
            value=str(metrics.messages_today),
            inline=True
        )
        embed.add_field(
            name="🤖 Commands (24h)",
            value=str(metrics.commands_today),
            inline=True
        )
        
        at_risk = self.get_at_risk_members(guild_id)
        if at_risk:
            at_risk_text = "\n".join([f"<@{m['user_id']}> - {m['days_inactive']}d inactive" for m in at_risk[:5]])
            embed.add_field(
                name="⚠️ At Risk Members",
                value=at_risk_text,
                inline=False
            )
        
        trends = self.get_topic_trends(guild_id)
        if trends:
            top_commands = "\n".join([f"`!{t['command']}` ({t['uses']} uses)" for t in sorted(trends, key=lambda x: x["uses"], reverse=True)[:5]])
            embed.add_field(
                name="📈 Trending Commands",
                value=top_commands,
                inline=False
            )
        
        embed.timestamp = datetime.now()
        
        return embed

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        help_embed = discord.Embed(
            title="🔍 Server Intelligence Dashboard",
            description="Real-time analytics and insights about your server.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Tracks member activity, engagement scores, topic trends, and identifies at-risk members for retention.",
            inline=False
        )
        help_embed.add_field(
            name="!serverstats",
            value="View complete server health report.",
            inline=False
        )
        help_embed.add_field(
            name="!mystats",
            value="View your personal activity stats.",
            inline=False
        )
        help_embed.add_field(
            name="!atrisk",
            value="List members at risk of leaving.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["serverstats"] = json.dumps({
            "command_type": "server_stats"
        })
        custom_cmds["mystats"] = json.dumps({
            "command_type": "my_stats"
        })
        custom_cmds["atrisk"] = json.dumps({
            "command_type": "at_risk"
        })
        custom_cmds["help intelligence"] = json.dumps({
            "command_type": "help_embed",
            "title": "🔍 Server Intelligence Dashboard",
            "description": "Real-time analytics and insights.",
            "fields": [
                {"name": "!serverstats", "value": "View complete server health report.", "inline": False},
                {"name": "!mystats", "value": "View your personal activity stats.", "inline": False},
                {"name": "!atrisk", "value": "List members at risk of leaving.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
