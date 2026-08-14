import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


class StaffExtras:
    def __init__(self, bot):
        self.bot = bot
        self._reviews: Dict[int, Dict[int, dict]] = {}  # guild_id -> {user_id: review_data}
        self._training_tasks: Dict[int, Dict[str, dict]] = {}  # guild_id -> {task_name: task_data}
        self._promotion_history: Dict[int, List[dict]] = {}  # guild_id -> [promotion_records]
        self._appeals: Dict[int, Dict[int, dict]] = {}  # guild_id -> {user_id: appeal_data}
        self._exit_interviews: Dict[int, List[dict]] = {}  # guild_id -> [exit_data]
        self._load_data()

    def _load_data(self):
        """Preload all guild data for staff extras on startup"""
        # Get all guild IDs from data manager (assuming dm has a method to list guilds)
        # If not, load per-guild on demand via _get_guild_data
        import os
        data_dir = dm.data_dir if hasattr(dm, "data_dir") else "./data"
        if not os.path.exists(data_dir):
            return
        for guild_folder in os.listdir(data_dir):
            if not guild_folder.isdigit():
                continue
            guild_id = int(guild_folder)
            self._get_guild_data(guild_id)

    def _save_guild_data(self, guild_id: int):
        """Save all staff extras data for a specific guild."""
        dm.update_guild_data(guild_id, "staff_reviews", self._reviews.get(guild_id, {}))
        dm.update_guild_data(guild_id, "training_tasks", self._training_tasks.get(guild_id, {}))
        dm.update_guild_data(guild_id, "promotion_history", self._promotion_history.get(guild_id, []))
        dm.update_guild_data(guild_id, "staff_appeals", self._appeals.get(guild_id, {}))
        dm.update_guild_data(guild_id, "exit_interviews", self._exit_interviews.get(guild_id, []))

    def _get_guild_data(self, guild_id: int):
        """Ensure guild data is loaded into memory."""
        if guild_id not in self._reviews:
            self._reviews[guild_id] = dm.get_guild_data(guild_id, "staff_reviews", {})
            self._training_tasks[guild_id] = dm.get_guild_data(guild_id, "training_tasks", {})
            self._promotion_history[guild_id] = dm.get_guild_data(guild_id, "promotion_history", [])
            self._appeals[guild_id] = dm.get_guild_data(guild_id, "staff_appeals", {})
            self._exit_interviews[guild_id] = dm.get_guild_data(guild_id, "exit_interviews", [])

    async def on_member_remove(self, member):
        """Handle exit interviews when staff leave"""
        guild = member.guild
        guild_id = guild.id
        
        config = dm.get_guild_data(guild_id, "staff_promo_config", {})
        is_staff = config.get("is_staff", False)
        
        if not is_staff:
            return
        
        staff_roles = config.get("staff_roles", [])
        member_roles = [r.id for r in member.roles]
        
        if any(r in member_roles for r in staff_roles):
            await self._do_exit_interview(member, guild)

    async def _do_exit_interview(self, member, guild):
        """Send exit interview DM"""
        guild_id = guild.id
        
        embed = discord.Embed(
            title="👋 Goodbye from Staff",
            description=f"You've left **{guild.name}** where you were on the staff team.",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Feedback",
            value="We'd love to hear your thoughts! What prompted your departure?",
            inline=False
        )
        embed.add_field(
            name="Options",
            value="React with:\n🟢 Happy to help\n🟡 Neutral\n🔴 Unhappy",
            inline=False
        )
        
        try:
            msg = await member.send(embed=embed)
            await msg.add_reaction("🟢")
            await msg.add_reaction("🟡")
            await msg.add_reaction("🔴")
            
            self._get_guild_data(guild_id)
            self._exit_interviews[guild_id].append({
                "user_id": member.id,
                "username": str(member),
                "timestamp": time.time(),
                "guild_id": guild_id,
                "reaction": None
            })
            self._save_guild_data(guild_id)
        except:
            pass

    async def on_reaction_add(self, reaction, user):
        """Handle exit interview responses"""
        if user.bot:
            return
        
        if not reaction.message.author.bot:
            return
        
        if "Goodbye from Staff" not in str(reaction.message.embeds):
            return
        
        guild = reaction.message.guild
        if not guild:
            return
        
        guild_id = guild.id
        emoji = str(reaction.emoji)
        
        for entry in self._exit_interviews.get(guild_id, []):
            if entry.get("user_id") == user.id and entry.get("reaction") is None:
                entry["reaction"] = emoji
                self._save_data()
                break

    async def get_staff_leaderboard(self, guild_id: int, limit: int = 10) -> List[dict]:
        """Get staff ranked by performance"""
        self._get_guild_data(guild_id)
        config = dm.get_guild_data(guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        
        staff_members = []
        guild = self.bot.get_guild(guild_id)
        
        staff_role_ids = config.get("staff_roles", [])
        
        for role_id in staff_role_ids:
            role = guild.get_role(role_id)
            if role:
                for member in role.members:
                    if not member.bot:
                        score = await self._calculate_member_score(guild_id, member)
                        staff_members.append({
                            "member": member,
                            "score": score,
                            "tier": self._get_tier_for_score(score, tiers)
                        })
        
        staff_members.sort(key=lambda x: x["score"], reverse=True)
        return staff_members[:limit]

    async def _calculate_member_score(self, guild_id: int, member) -> float:
        """Calculate member's promotion score"""
        try:
            config = dm.get_guild_data(guild_id, "staff_promo_config", {})
            metrics = config.get("metrics", {})
            
            xp = dm.get_guild_data(guild_id, f"xp_{member.id}", 0)
            messages = dm.get_guild_data(guild_id, f"messages_{member.id}", 0)
            
            score = (
                (xp / 5000 * 0.25) +
                (messages / 1000 * 0.20)
            )
            
            return min(1.0, score)
        except:
            return 0.0

    def _get_tier_for_score(self, score: float, tiers: List[dict]) -> str:
        """Get tier name for score"""
        current_tier = "Trial Staff"
        for tier in sorted(tiers, key=lambda x: x.get("threshold", 0), reverse=True):
            if score >= tier.get("threshold", 0):
                current_tier = tier.get("name", "Staff")
                break
        return current_tier

    async def record_promotion(self, guild_id: int, member: discord.Member, 
                               old_tier: str, new_tier: str, reason: str):
        """Record a promotion in history"""
        if guild_id not in self._promotion_history:
            self._promotion_history[guild_id] = []
        
        record = {
            "user_id": member.id,
            "username": str(member),
            "old_tier": old_tier,
            "new_tier": new_tier,
            "reason": reason,
            "timestamp": time.time(),
            "guild_id": guild_id
        }
        
        self._promotion_history[guild_id].append(record)
        self._promotion_history[guild_id] = self._promotion_history[guild_id][-100:]
        self._save_data()
        
        await self._log_promotion(member.guild, record)

    async def _log_promotion(self, guild: discord.Guild, record: dict):
        """Log promotion to channel"""
        config = dm.get_guild_data(guild.id, "staff_promo_config", {})
        log_channel_id = config.get("log_channel")
        
        if not log_channel_id:
            return
        
        channel = guild.get_channel(log_channel_id)
        if not channel:
            return
        
        is_promotion = record.get("old_tier") != record.get("new_tier")
        
        if is_promotion:
            embed = discord.Embed(
                title="📢 Staff Promotion" if "promotion" in record.get("reason", "").lower() 
                     else "📉 Staff Demotion",
                color=discord.Color.green() if is_promotion else discord.Color.red()
            )
        else:
            embed = discord.Embed(
                title="📋 Staff Change",
                color=discord.Color.blue()
            )
        
        member = guild.get_member(record["user_id"])
        embed.add_field(name="Member", value=member.mention if member else record["username"], inline=True)
        embed.add_field(name="From", value=record.get("old_tier", "N/A"), inline=True)
        embed.add_field(name="To", value=record.get("new_tier", "N/A"), inline=True)
        embed.add_field(name="Reason", value=record.get("reason", "Manual"), inline=False)
        embed.timestamp = datetime.fromtimestamp(record["timestamp"])
        
        await channel.send(embed=embed)

    async def create_training_task(self, guild_id: int, name: str, description: str, 
                              required_score: float, reward_boost: float):
        """Create a training task"""
        self._get_guild_data(guild_id)
        self._training_tasks[guild_id][name] = {
            "description": description,
            "required_score": required_score,
            "reward_boost": reward_boost,
            "created_at": time.time()
        }
        self._save_guild_data(guild_id)

    async def get_training_tasks(self, guild_id: int) -> List[dict]:
        """Get available training tasks"""
        self._get_guild_data(guild_id)
        tasks = self._training_tasks.get(guild_id, {})
        return [{"name": k, **v} for k, v in tasks.items()]

    async def complete_training(self, member: discord.Member, task_name: str) -> bool:
        """Mark training as completed"""
        guild_id = member.guild.id
        
        tasks = self._training_tasks.get(guild_id, {})
        if task_name not in tasks:
            return False
        
        task = tasks[task_name]
        
        dm.update_guild_data(guild_id, f"training_{member.id}_{task_name}", {
            "completed": True,
            "completed_at": time.time()
        })
        
        return True

    async def submit_appeal(self, guild_id: int, user_id: int, message: str):
        """Submit appeal for demotion"""
        if guild_id not in self._appeals:
            self._appeals[guild_id] = {}
        
        self._appeals[guild_id][user_id] = {
            "message": message,
            "timestamp": time.time(),
            "votes": [],
            "status": "pending"
        }
        self._save_data()

    async def vote_on_appeal(self, guild_id: int, user_id: int, voter_id: int, approve: bool):
        """Vote on appeal"""
        if guild_id not in self._appeals:
            return False
        
        appeal = self._appeals[guild_id].get(user_id)
        if not appeal or appeal.get("status") != "pending":
            return False
        
        appeal["votes"].append({
            "voter_id": voter_id,
            "approve": approve,
            "timestamp": time.time()
        })
        
        approve_votes = sum(1 for v in appeal["votes"] if v.get("approve"))
        total_votes = len(appeal["votes"])
        
        if total_votes >= 3:
            if approve_votes > total_votes / 2:
                appeal["status"] = "approved"
            else:
                appeal["status"] = "rejected"
            
            self._save_data()
        
        return True

    async def get_promotion_history(self, guild_id: int, limit: int = 20) -> List[dict]:
        """Get promotion history"""
        history = self._promotion_history.get(guild_id, [])
        return history[-limit:]

    async def request_peer_review(self, guild_id: int, reviewer_id: int, 
                                target_id: int, rating: int, comment: str):
        """Submit peer review"""
        if guild_id not in self._reviews:
            self._reviews[guild_id] = {}
        
        if target_id not in self._reviews[guild_id]:
            self._reviews[guild_id][target_id] = {"reviews": []}
        
        self._reviews[guild_id][target_id]["reviews"].append({
            "reviewer_id": reviewer_id,
            "rating": rating,
            "comment": comment,
            "timestamp": time.time()
        })
        
        self._reviews[guild_id][target_id]["reviews"] = \
            self._reviews[guild_id][target_id]["reviews"][-10:]
        
        self._save_data()

    async def get_peer_review_score(self, guild_id: int, user_id: int) -> float:
        """Get average peer review score"""
        reviews = self._reviews.get(guild_id, {}).get(user_id, {}).get("reviews", [])
        
        if not reviews:
            return 0.5
        
        avg_rating = sum(r["rating"] for r in reviews) / len(reviews)
        return avg_rating / 5.0


class StaffExtrasCommands:
    def __init__(self, bot):
        self.bot = bot
        self.extras = StaffExtras(bot)

    async def handle_staff_leaderboard(self, message):
        """Handle !staffleaderboard command"""
        guild = message.guild
        leaderboard = await self.extras.get_staff_leaderboard(guild.id)
        
        if not leaderboard:
            await message.channel.send("No staff to display!")
            return
        
        embed = discord.Embed(
            title="🏆 Staff Leaderboard",
            description="Top staff by performance",
            color=discord.Color.gold()
        )
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, entry in enumerate(leaderboard[:10]):
            member = entry["member"]
            score = entry["score"]
            tier = entry["tier"]
            
            embed.add_field(
                name=f"{medals[i]} {member.display_name}",
                value=f"Score: {score:.0%} | {tier}",
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_promotion_history(self, message):
        """Handle !promotionhistory command"""
        guild = message.guild
        history = await self.extras.get_promotion_history(guild.id)
        
        if not history:
            await message.channel.send("No promotion history yet!")
            return
        
        embed = discord.Embed(
            title="📜 Promotion History",
            color=discord.Color.blue()
        )
        
        for record in history[-10:]:
            member = guild.get_member(record["user_id"])
            name = member.display_name if member else record["username"]
            
            is_promo = "promotion" in record.get("reason", "").lower()
            emoji = "📈" if is_promo else "📉"
            
            date = datetime.fromtimestamp(record["timestamp"]).strftime("%m/%d")
            embed.add_field(
                name=f"{emoji} {name}",
                value=f"{record.get('old_tier')} → {record.get('new_tier')} | {date}",
                inline=True
            )
        
        await message.channel.send(embed=embed)

    async def handle_training_tasks(self, message):
        """Handle !trainingtasks command"""
        guild = message.guild
        tasks = await self.extras.get_training_tasks(guild.id)
        
        if not tasks:
            await message.channel.send("No training tasks available. Ask an admin to create some!")
            return
        
        embed = discord.Embed(
            title="📚 Training Tasks",
            description="Complete these for promotion boost!",
            color=discord.Color.green()
        )
        
        for task in tasks:
            embed.add_field(
                name=task["name"],
                value=f"{task['description']}\nReward: +{task['reward_boost']:.0%} boost",
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_appeal(self, message, parts):
        """Handle !appeal command"""
        if len(parts) < 2:
            await message.channel.send("Usage: !appeal <reason for appeal>")
            return
        
        guild = message.guild
        reason = " ".join(parts[1:])
        
        await self.extras.submit_appeal(guild.id, message.author.id, reason)
        
        await message.channel.send("✅ Appeal submitted! Staff will vote on it.")


def setup(bot):
    return StaffExtras(bot)