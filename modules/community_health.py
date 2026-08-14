import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


@dataclass
class Interaction:
    from_user: int
    to_user: int
    weight: float
    timestamp: float
    channel_id: int


@dataclass
class RelationshipCluster:
    name: str
    members: List[int]
    avg_interaction_strength: float
    topics: List[str]


@dataclass
class MemberHealth:
    user_id: int
    inbound_interactions: int
    outbound_interactions: int
    mutual_connections: List[int]
    isolation_score: float
    influence_score: float
    activity_trend: str
    days_inactive: int


class CommunityHealth:
    def __init__(self, bot):
        self.bot = bot
        self._interaction_graph: Dict[int, Dict[int, Dict[int, float]]] = {}
        self._last_analysis: Dict[int, float] = {}
        self._analysis_interval = 3600 * 24
        self._pending_reports: Dict[int, asyncio.Task] = {}
        self._guild_configs: Dict[int, dict] = {}
        self._member_cache: Dict[int, Dict[int, MemberHealth]] = {}

    def get_config(self, guild_id: int) -> dict:
        if guild_id in self._guild_configs:
            return self._guild_configs[guild_id]
        
        config = dm.get_guild_data(guild_id, "community_health_config", {
            "enabled": True,
            "analysis_interval_hours": 24,
            "health_reports_enabled": True,
            "isolation_alerts": True,
            "bridge_events_enabled": True,
            "mentorship_enabled": True,
            "min_interactions_for_analysis": 10,
            "isolation_threshold": 0.3,
            "report_channel": None,
            "excluded_roles": []
        })
        
        self._guild_configs[guild_id] = config
        return config

    def update_config(self, guild_id: int, key: str, value):
        config = self.get_config(guild_id)
        config[key] = value
        self._guild_configs[guild_id] = config
        dm.update_guild_data(guild_id, "community_health_config", config)

    def _ensure_guild(self, guild_id: int):
        if guild_id not in self._interaction_graph:
            self._interaction_graph[guild_id] = {}
        if guild_id not in self._member_cache:
            self._member_cache[guild_id] = {}

    async def analyze_interaction(self, message: discord.Message) -> bool:
        if message.author.bot:
            return False
        
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        
        if not config.get("enabled", True):
            return False
        
        if message.author.guild_permissions.administrator:
            for role in message.author.roles:
                if role.id in config.get("excluded_roles", []):
                    return False
        
        self._ensure_guild(guild_id)
        author_id = message.author.id
        
        if author_id not in self._interaction_graph[guild_id]:
            self._interaction_graph[guild_id][author_id] = {}
        
        mentions = self._extract_mentions(message.content)
        for mentioned_id in mentions:
            if mentioned_id != author_id and not self.bot.get_user(mentioned_id).bot:
                self._update_interaction(guild_id, author_id, mentioned_id, 0.3, message.channel.id)
        
        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg.author.id != author_id:
                    self._update_interaction(guild_id, author_id, ref_msg.author.id, 0.5, message.channel.id)
            except:
                pass
        
        await self._trigger_periodic_analysis(guild_id)
        
        return True

    def _extract_mentions(self, content: str) -> List[int]:
        import re
        mention_pattern = r'<@!?(\d+)>'
        mentions = re.findall(mention_pattern, content)
        return [int(m) for m in mentions]

    def _update_interaction(self, guild_id: int, from_user: int, to_user: int, weight: float, channel_id: int = 0):
        self._ensure_guild(guild_id)
        
        if from_user not in self._interaction_graph[guild_id]:
            self._interaction_graph[guild_id][from_user] = {}
        
        current_weight = self._interaction_graph[guild_id][from_user].get(to_user, 0)
        decay_factor = 0.95
        new_weight = min(1.0, (current_weight * decay_factor) + weight)
        
        self._interaction_graph[guild_id][from_user][to_user] = new_weight

    async def _trigger_periodic_analysis(self, guild_id: int):
        now = time.time()
        last = self._last_analysis.get(guild_id, 0)
        
        interval = self.get_config(guild_id).get("analysis_interval_hours", 24) * 3600
        
        if now - last >= interval:
            self._last_analysis[guild_id] = now
            try:
                await self.generate_health_report(guild_id)
            except Exception as e:
                logger.error(f"Error generating health report for guild {guild_id}: {e}")

    async def generate_health_report(self, guild_id: int) -> dict:
        config = self.get_config(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            return {"error": "Guild not found"}
        
        self._ensure_guild(guild_id)
        
        interaction_data = self._build_interaction_summary(guild_id)
        
        member_health = await self._analyze_member_health(guild_id, guild, interaction_data)
        
        clusters = await self._identify_clusters(guild_id, guild, interaction_data)
        
        health_score = self._calculate_health_score(interaction_data, member_health, clusters)
        
        insights = await self._generate_ai_insights(guild_id, guild, member_health, clusters, health_score)
        
        suggestions = await self._suggest_actions(guild_id, guild, member_health, clusters, health_score)
        
        report = {
            "timestamp": time.time(),
            "guild_id": guild_id,
            "health_score": health_score,
            "member_count": len(member_health),
            "active_members": sum(1 for m in member_health.values() if m.days_inactive < 7),
            "isolated_members": sum(1 for m in member_health.values() if m.isolation_score > config.get("isolation_threshold", 0.3)),
            "clusters": [{"name": c.name, "members": c.members, "strength": c.avg_interaction_strength} for c in clusters],
            "insights": insights,
            "suggestions": suggestions
        }
        
        if config.get("health_reports_enabled", True):
            await self._send_health_report(guild, report)
        
        self._save_health_history(guild_id, report)
        
        return report

    def _build_interaction_summary(self, guild_id: int) -> dict:
        graph = self._interaction_graph.get(guild_id, {})
        
        total_interactions = 0
        interaction_pairs = 0
        mutual_connections = 0
        
        for user_id, targets in graph.items():
            for target_id, weight in targets.items():
                if weight > 0.1:
                    total_interactions += 1
                    if target_id in graph and user_id in graph.get(target_id, {}):
                        if graph[target_id].get(user_id, 0) > 0.1:
                            mutual_connections += 1
                    interaction_pairs += 1
        
        return {
            "total_interactions": total_interactions,
            "interaction_pairs": interaction_pairs,
            "mutual_connections": mutual_connections,
            "unique_users": len(graph)
        }

    async def _analyze_member_health(self, guild_id: int, guild: discord.Guild, interaction_data: dict) -> Dict[int, MemberHealth]:
        graph = self._interaction_graph.get(guild_id, {})
        config = self.get_config(guild_id)
        
        member_health = {}
        
        for member in guild.members:
            if member.bot:
                continue
            
            user_id = member.id
            user_graph = graph.get(user_id, {})
            
            inbound = sum(1 for u in graph if user_id in graph.get(u, {}))
            outbound = len(user_graph)
            
            mutual = [t for t in user_graph if t in graph and user_id in graph.get(t, {})]
            
            avg_outbound = sum(user_graph.values()) / len(user_graph) if user_graph else 0
            isolation_score = max(0, 1 - (avg_outbound * 2))
            
            members_above = sum(1 for u in graph.values() for w in u.values() if w > user_graph.get(member.id, 0))
            influence_score = min(1.0, members_above / max(1, len(graph)))
            
            last_active = await self._get_last_active(user_id, guild)
            days_inactive = int((time.time() - last_active) / 86400) if last_active else 999
            
            activity_trend = "stable"
            if days_inactive < 3:
                activity_trend = "active"
            elif days_inactive > 14:
                activity_trend = "declining"
            
            member_health[user_id] = MemberHealth(
                user_id=user_id,
                inbound_interactions=inbound,
                outbound_interactions=outbound,
                mutual_connections=mutual,
                isolation_score=isolation_score,
                influence_score=influence_score,
                activity_trend=activity_trend,
                days_inactive=days_inactive
            )
        
        return member_health

    async def _get_last_active(self, user_id: int, guild: discord.Guild) -> float:
        try:
            history = dm.get_guild_data(guild.id, "user_activity_history", {})
            if str(user_id) in history:
                return history[str(user_id)].get("last_message", 0)
        except:
            pass
        return 0

    async def _identify_clusters(self, guild_id: int, guild: discord.Guild, interaction_data: dict) -> List[RelationshipCluster]:
        graph = self._interaction_graph.get(guild_id, {})
        
        clusters = []
        visited = set()
        
        def get_strong_connections(user_id: int) -> List[int]:
            user_graph = graph.get(user_id, {})
            return [t for t, w in user_graph.items() if w > 0.4]
        
        for user_id in graph:
            if user_id in visited:
                continue
            
            connected = {user_id}
            queue = [user_id]
            
            while queue:
                current = queue.pop(0)
                for conn in get_strong_connections(current):
                    if conn not in connected:
                        connected.add(conn)
                        queue.append(conn)
            
            if len(connected) >= 3:
                clusters.append(RelationshipCluster(
                    name=f"Group {len(clusters) + 1}",
                    members=list(connected),
                    avg_interaction_strength=0.5,
                    topics=[]
                ))
                visited.update(connected)
        
        return clusters[:5]

    def _calculate_health_score(self, interaction_data: dict, member_health: Dict[int, MemberHealth], clusters: List[RelationshipCluster]) -> float:
        if not member_health:
            return 0.0
        
        unique_users = interaction_data.get("unique_users", 1)
        total_members = len(member_health)
        
        participation_ratio = unique_users / max(1, total_members)
        
        mutual_ratio = interaction_data.get("mutual_connections", 0) / max(1, interaction_data.get("interaction_pairs", 1))
        
        avg_isolation = sum(m.isolation_score for m in member_health.values()) / len(member_health)
        isolation_penalty = 1 - avg_isolation
        
        cluster_bonus = min(0.2, len(clusters) * 0.05)
        
        health_score = (
            (participation_ratio * 0.3) +
            (mutual_ratio * 0.3) +
            (isolation_penalty * 0.3) +
            cluster_bonus
        )
        
        return min(1.0, health_score)

    async def _generate_ai_insights(self, guild_id: int, guild: discord.Guild, 
                                     member_health: Dict[int, MemberHealth], 
                                     clusters: List[RelationshipCluster],
                                     health_score: float) -> List[str]:
        isolated = [m for m in member_health.values() if m.isolation_score > 0.5]
        influencers = sorted(member_health.values(), key=lambda x: x.influence_score, reverse=True)[:5]
        
        insights = []
        
        insights.append(f"Community health score: {health_score:.1f}/10 ({'Good' if health_score > 0.6 else 'Needs attention'})")
        
        if isolated:
            insights.append(f"{len(isolated)} members showing high isolation (low cross-interaction)")
        
        if influencers:
            top_influencer = guild.get_member(influencers[0].user_id)
            if top_influencer:
                insights.append(f"Top community builder: {top_influencer.display_name} (influence score: {influencers[0].influence_score:.2f})")
        
        if clusters:
            insights.append(f"Found {len(clusters)} active group clusters with strong internal connections")
        
        return insights

    async def _suggest_actions(self, guild_id: int, guild: discord.Guild,
                               member_health: Dict[int, MemberHealth],
                               clusters: List[RelationshipCluster],
                               health_score: float) -> List[dict]:
        config = self.get_config(guild_id)
        suggestions = []
        
        isolated = [m for m in member_health.values() if m.isolation_score > config.get("isolation_threshold", 0.3)]
        
        if isolated and config.get("isolation_alerts", True):
            isolated_sample = isolated[:3]
            member_mentions = [f"<@{m.user_id}>" for m in isolated_sample]
            suggestions.append({
                "type": "isolation_outreach",
                "priority": "high",
                "description": f"Reach out to isolated members: {', '.join(member_mentions)}",
                "action": "Private message welcoming them and finding shared interests"
            })
        
        if len(clusters) >= 2 and config.get("bridge_events_enabled", True):
            cluster_names = [c.name for c in clusters[:2]]
            suggestions.append({
                "type": "bridge_event",
                "priority": "medium",
                "description": f"Host event to connect {cluster_names[0]} and {cluster_names[1]}",
                "action": "Create collaborative event requiring cross-group participation"
            })
        
        if len(isolated) > 5 and config.get("mentorship_enabled", True):
            suggestions.append({
                "type": "mentorship_program",
                "priority": "medium",
                "description": "Start mentorship program pairing newcomers with established members",
                "action": "Match isolated members with active community builders"
            })
        
        if health_score < 0.5:
            suggestions.append({
                "type": "engagement_boost",
                "priority": "high",
                "description": "Low community cohesion - consider engagement-focused activities",
                "action": "Daily icebreakers, team activities, or discussion prompts"
            })
        
        return suggestions

    async def _send_health_report(self, guild: discord.Guild, report: dict):
        report_channel_id = dm.get_guild_data(guild.id, "report_channel")
        
        if not report_channel_id:
            config = self.get_config(guild.id)
            report_channel_id = config.get("report_channel")
        
        if not report_channel_id:
            return
        
        channel = guild.get_channel(report_channel_id)
        if not channel:
            return
        
        health = report.get("health_score", 0)
        color = discord.Color.green() if health > 0.6 else discord.Color.orange() if health > 0.4 else discord.Color.red()
        
        embed = discord.Embed(
            title="📊 Community Health Report",
            description=f"Health Score: **{health:.1f}/10**",
            color=color
        )
        
        embed.add_field(
            name="Members",
            value=f"Total: {report.get('member_count', 0)} | Active: {report.get('active_members', 0)} | Isolated: {report.get('isolated_members', 0)}",
            inline=False
        )
        
        for insight in report.get("insights", [])[:3]:
            embed.add_field(name="Insight", value=insight, inline=False)
        
        for suggestion in report.get("suggestions", [])[:3]:
            embed.add_field(
                name=f"💡 {suggestion.get('type', '').replace('_', ' ').title()}",
                value=f"{suggestion.get('description', '')}\n*Action: {suggestion.get('action', '')}*",
                inline=False
            )
        
        embed.set_footer(text="Community Health Analysis • AI-Powered")
        embed.timestamp = discord.utils.utcnow()
        
        await channel.send(embed=embed)

    def _save_health_history(self, guild_id: int, report: dict):
        history = dm.load_json("community_health_history", default={})
        
        if str(guild_id) not in history:
            history[str(guild_id)] = []
        
        history[str(guild_id)].append({
            "timestamp": report.get("timestamp"),
            "health_score": report.get("health_score"),
            "member_count": report.get("member_count"),
            "isolated_members": report.get("isolated_members")
        })
        
        history[str(guild_id)] = history[str(guild_id)][-30:]
        dm.save_json("community_health_history", history)

    async def get_member_health(self, guild_id: int, user_id: int) -> Optional[MemberHealth]:
        self._ensure_guild(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            return None
        
        interaction_data = self._build_interaction_summary(guild_id)
        member_health = await self._analyze_member_health(guild_id, guild, interaction_data)
        
        return member_health.get(user_id)

    async def suggest_connections(self, guild_id: int, user_id: int, limit: int = 5) -> List[Tuple[int, float]]:
        self._ensure_guild(guild_id)
        graph = self._interaction_graph.get(guild_id, {})
        
        user_graph = graph.get(user_id, {})
        existing_connections = set(user_graph.keys())
        
        candidates = []
        
        for other_user_id, other_graph in graph.items():
            if other_user_id == user_id or other_user_id in existing_connections:
                continue
            
            other_to_user = other_graph.get(user_id, 0)
            if other_to_user > 0.1:
                continue
            
            mutual = len(set(user_graph.keys()) & set(other_graph.keys()))
            score = mutual + other_to_user + (0.1 * len(other_graph))
            candidates.append((other_user_id, score))
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:limit]

    async def get_community_stats(self, guild_id: int) -> dict:
        self._ensure_guild(guild_id)
        
        history = dm.load_json("community_health_history", default={})
        guild_history = history.get(str(guild_id), [])
        
        if not guild_history:
            return {"error": "No data available yet"}
        
        latest = guild_history[-1]
        
        if len(guild_history) >= 2:
            prev = guild_history[-2]
            score_change = latest.get("health_score", 0) - prev.get("health_score", 0)
            trend = "up" if score_change > 0.05 else "down" if score_change < -0.05 else "stable"
        else:
            score_change = 0
            trend = "stable"
        
        return {
            "current_health": latest.get("health_score", 0),
            "trend": trend,
            "score_change": score_change,
            "member_count": latest.get("member_count", 0),
            "isolated_members": latest.get("isolated_members", 0),
            "data_points": len(guild_history)
        }
