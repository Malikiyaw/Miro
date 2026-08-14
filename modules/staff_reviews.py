import discord
from discord.ext import commands, tasks
import asyncio
import json
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from data_manager import dm
from logger import logger


class StaffReviewSystem:
    def __init__(self, bot):
        self.bot = bot

    def start_tasks(self):
        self._review_monitor.start()

    def _get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "staff_reviews_config", {
            "enabled": True,
            "cycle": "monthly", # weekly, bi-weekly, monthly
            "start_day": 0, # 0=Mon
            "last_cycle_start": 0,
            "next_cycle_start": 0,
            "review_channel_id": None,
            "notifications_enabled": True,
            "criteria": [
                {"name": "Responsiveness", "weight": 1.0},
                {"name": "Helpfulness", "weight": 1.0},
                {"name": "Professionalism", "weight": 1.0},
                {"name": "Activity", "weight": 1.0},
                {"name": "Initiative", "weight": 1.0},
                {"name": "Rule Knowledge", "weight": 1.0}
            ],
            "thresholds": {
                "warning": 2.5,
                "promotion": 4.5
            },
            "weights": {
                "admin": 0.5,
                "peer": 0.3,
                "self": 0.2
            },
            "staff_roles": []
        })

    def _save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "staff_reviews_config", config)

    def _get_active_reviews(self, guild_id: int) -> dict:
        """Returns { user_id: { self: {}, peer: { voter_id: {} }, admin: {} } }"""
        return dm.get_guild_data(guild_id, "staff_active_reviews", {})

    def _save_active_reviews(self, guild_id: int, reviews: dict):
        dm.update_guild_data(guild_id, "staff_active_reviews", reviews)

    def _get_history(self, guild_id: int) -> List[dict]:
        return dm.get_guild_data(guild_id, "staff_reviews_history", [])

    def _save_history(self, guild_id: int, history: List[dict]):
        dm.update_guild_data(guild_id, "staff_reviews_history", history[-500:])

    @tasks.loop(hours=24)
    async def _review_monitor(self):
        """Monitor and trigger review cycles."""
        for guild in self.bot.guilds:
            config = self._get_config(guild.id)
            if not config.get("enabled"): continue

            now = time.time()
            if config.get("next_cycle_start", 0) > 0 and config.get("next_cycle_start", 0) <= now:
                await self.start_review_cycle(guild.id)

    @_review_monitor.before_loop
    async def before_review_monitor(self):
        await self.bot.wait_until_ready()

    async def start_review_cycle(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild: return

        # 1. Compile existing active reviews before clearing
        active = self._get_active_reviews(guild_id)
        if active:
            await self.compile_reviews(guild_id)

        config = self._get_config(guild_id)
        config["last_cycle_start"] = time.time()
        
        # Calculate next cycle start
        days = 30
        if config["cycle"] == "weekly": days = 7
        elif config["cycle"] == "bi-weekly": days = 14
        
        config["next_cycle_start"] = time.time() + (days * 86400)
        self._save_config(guild_id, config)

        # Clear active reviews and start fresh
        self._save_active_reviews(guild_id, {})
        
        # Notify staff
        staff_members = []
        for role_id in config.get("staff_roles", []):
            role = guild.get_role(role_id)
            if role:
                for m in role.members:
                    if not m.bot and m not in staff_members:
                        staff_members.append(m)
        
        if not staff_members:
            # Fallback to members with manage_messages
            staff_members = [m for m in guild.members if m.guild_permissions.manage_messages and not m.bot]

        if config.get("notifications_enabled", True):
            for member in staff_members:
                try:
                    embed = discord.Embed(
                        title="📝 Staff Review Cycle Started!",
                        description=f"A new review cycle has started in **{guild.name}**. Please complete your self-review and peer reviews.",
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="How to complete", value="Use `!review` in the server to open the review menu.")
                    await member.send(embed=embed)
                except:
                    pass

        if config.get("review_channel_id"):
            channel = guild.get_channel(config.get("review_channel_id"))
            if channel:
                await channel.send("🚀 **A new Staff Review cycle has begun.** Staff members have been notified via DM.")

    async def handle_review_command(self, message, parts=None):
        """Handle !review command - opens review selection menu"""
        guild = message.guild
        if not guild: return
        
        config = self._get_config(guild.id)
        # Check if staff
        is_staff = False
        if config.get("staff_roles"):
            is_staff = any(r.id in config["staff_roles"] for r in message.author.roles)
        else:
            is_staff = message.author.guild_permissions.manage_messages
            
        if not is_staff and not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is for staff members only.")
            return

        embed = discord.Embed(title="📝 Staff Review Menu", description="Select what kind of review you'd like to perform.", color=discord.Color.blue())
        view = ReviewSelectionView(self, guild.id, message.author.id)
        await message.channel.send(embed=embed, view=view, delete_after=60)

    async def handle_myreview(self, message, parts=None):
        """Handle !myreview command - shows user's own performance trend"""
        guild = message.guild
        if not guild: return
        
        history = self._get_history(guild.id)
        user_reviews = [r for r in history if r.get("user_id") == message.author.id]
        
        if not user_reviews:
            await message.channel.send("You have no completed reviews in history yet.")
            return

        recent = user_reviews[-6:]
        scores = [r["composite_score"] for r in recent]
        
        embed = discord.Embed(title=f"📈 Performance Trend: {message.author.display_name}", color=discord.Color.green())
        
        # Simple text graph
        graph = ""
        for score in scores:
            bar = "█" * int(score * 2)
            graph += f"`{score:.1f}` {bar}\n"
        
        embed.add_field(name="Last 6 Cycles", value=graph or "N/A")
        embed.add_field(name="Current Status", value="✅ Excellent" if scores[-1] >= 4.0 else "⚠️ Needs Improvement" if scores[-1] < 3.0 else "🟢 Good", inline=False)
        
        await message.channel.send(embed=embed)

    async def submit_self_review(self, guild_id: int, user_id: int, ratings: dict):
        active = self._get_active_reviews(guild_id)
        uid_str = str(user_id)
        if uid_str not in active: active[uid_str] = {"self": {}, "peer": {}, "admin": {}}
        active[uid_str]["self"] = ratings
        self._save_active_reviews(guild_id, active)

    async def submit_peer_review(self, guild_id: int, voter_id: int, target_id: int, ratings: dict):
        active = self._get_active_reviews(guild_id)
        target_str = str(target_id)
        if target_str not in active: active[target_str] = {"self": {}, "peer": {}, "admin": {}}
        active[target_str]["peer"][str(voter_id)] = ratings
        self._save_active_reviews(guild_id, active)

    async def submit_admin_review(self, guild_id: int, admin_id: int, target_id: int, ratings: dict):
        active = self._get_active_reviews(guild_id)
        target_str = str(target_id)
        if target_str not in active: active[target_str] = {"self": {}, "peer": {}, "admin": {}}
        active[target_str]["admin"] = ratings # Admin review is authoritative
        self._save_active_reviews(guild_id, active)

    async def compile_reviews(self, guild_id: int):
        """Compile all active reviews into history and generate report."""
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        
        config = self._get_config(guild_id)
        active = self._get_active_reviews(guild_id)
        history = self._get_history(guild_id)
        
        cycle_id = int(time.time())
        report_data = []

        for uid_str, data in active.items():
            user_id = int(uid_str)
            member = guild.get_member(user_id)
            if not member: continue
            
            # Calculate average peer score
            peer_scores = data.get("peer", {})
            avg_peer = {}
            if peer_scores:
                for criteria in config["criteria"]:
                    name = criteria["name"]
                    vals = [p[name] for p in peer_scores.values() if name in p]
                    if vals: avg_peer[name] = sum(vals) / len(vals)
            
            # Composite calculation with configurable weights
            weights = config.get("weights", {"admin": 0.5, "peer": 0.3, "self": 0.2})
            composite = 0
            weights_found = 0
            
            def get_avg_rating(ratings):
                if not ratings: return 0
                return sum(ratings.values()) / len(ratings)

            admin_score = get_avg_rating(data.get("admin"))
            peer_score = get_avg_rating(avg_peer)
            self_score = get_avg_rating(data.get("self"))

            scores = []
            if admin_score: scores.append(admin_score * weights.get("admin", 0.5)); weights_found += weights.get("admin", 0.5)
            if peer_score: scores.append(peer_score * weights.get("peer", 0.3)); weights_found += weights.get("peer", 0.3)
            if self_score: scores.append(self_score * weights.get("self", 0.2)); weights_found += weights.get("self", 0.2)

            final_score = sum(scores) / weights_found if weights_found > 0 else 0

            entry = {
                "user_id": user_id,
                "username": str(member),
                "cycle_id": cycle_id,
                "timestamp": time.time(),
                "self_ratings": data.get("self"),
                "peer_ratings_avg": avg_peer,
                "admin_ratings": data.get("admin"),
                "composite_score": final_score
            }
            history.append(entry)
            report_data.append(entry)

            # DM results to staff member
            if config.get("notifications_enabled"):
                try:
                    embed = discord.Embed(title="📊 Your Review Results", color=discord.Color.blue())
                    embed.add_field(name="Composite Score", value=f"{final_score:.2f} / 5.0")
                    status = "✅ Promotion Eligible" if final_score >= config["thresholds"]["promotion"] else "⚠️ Warning/Probation" if final_score <= config["thresholds"]["warning"] else "🟢 Satisfactory"
                    embed.add_field(name="Status", value=status)
                    await member.send(embed=embed)
                except: pass

        self._save_history(guild_id, history)
        self._save_active_reviews(guild_id, {}) # Clear active
        
        # Post report to channel
        if config.get("review_channel_id") and report_data:
            channel = guild.get_channel(config.get("review_channel_id"))
            if channel:
                report_data.sort(key=lambda x: x["composite_score"], reverse=True)
                desc = "\n".join([f"• **{x['username']}**: {x['composite_score']:.2f}" for x in report_data[:10]])
                embed = discord.Embed(title="📋 Staff Review Report", description=f"Cycle: {datetime.now().strftime('%Y-%m-%d')}\n\n{desc}", color=discord.Color.gold())
                await channel.send(embed=embed)

    async def setup(self, interaction):
        """Initial setup via autosetup"""
        guild = interaction.guild
        config = self._get_config(guild.id)
        
        category = discord.utils.get(guild.categories, name="Staff Hub")
        if not category:
            category = await guild.create_category("Staff Hub")
        
        channel = discord.utils.get(guild.text_channels, name="staff-reviews")
        if not channel:
            channel = await guild.create_text_channel("staff-reviews", category=category)
            await channel.set_permissions(guild.default_role, read_messages=False)
        
        config["review_channel_id"] = channel.id
        self._save_config(guild.id, config)
        
        return True


class ReviewSelectionView(discord.ui.View):
    def __init__(self, system, guild_id, user_id):
        super().__init__(timeout=60)
        self.system = system
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.button(label="Self Review", style=discord.ButtonStyle.primary, custom_id="rev_cfg_self_review")
    async def self_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        await interaction.response.send_modal(ReviewModal(self.system, self.guild_id, "self", interaction.user))

    @discord.ui.button(label="Peer Review", style=discord.ButtonStyle.secondary, custom_id="rev_cfg_peer_review")
    async def peer_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        
        class PeerSelect(discord.ui.UserSelect):
            def __init__(self, system, guild_id):
                super().__init__(placeholder="Select staff member to review...", min_values=1, max_values=1)
                self.system = system
                self.guild_id = guild_id
            async def callback(self, it):
                target = self.values[0]
                if target.id == it.user.id:
                    return await it.response.send_message("You cannot peer-review yourself! Use Self Review.", ephemeral=True)
                from modules.staff_reviews import ReviewModal
                await it.response.send_modal(ReviewModal(self.system, self.guild_id, "peer", target, it.user.id))
        
        v = discord.ui.View(); v.add_item(PeerSelect(self.system, self.guild_id))
        await interaction.response.send_message("Select a staff member to peer review:", view=v, ephemeral=True)


class ReviewModal(discord.ui.Modal):
    def __init__(self, system, guild_id, review_type, target_member, voter_id=None):
        super().__init__(title=f"{review_type.title()} Review: {target_member.display_name}")
        self.system = system
        self.guild_id = guild_id
        self.review_type = review_type
        self.target_id = target_member.id
        self.voter_id = voter_id or target_member.id
        
        config = system._get_config(guild_id)
        # Combine all criteria into one multi-line text input to bypass the 5-field limit
        self.criteria_names = [c["name"] for c in config["criteria"]]
        
        instruction = ", ".join(self.criteria_names)
        self.ratings_input = discord.ui.TextInput(
            label=f"Ratings for: {instruction}",
            placeholder="Format: 5, 4, 5, 3, 4, 5 (one per criteria)",
            style=discord.TextStyle.paragraph,
            default=", ".join(["5"] * len(self.criteria_names)),
            required=True
        )
        self.add_item(self.ratings_input)
        
        self.notes_input = discord.ui.TextInput(
            label="Additional Notes",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        ratings = {}
        try:
            scores = [int(s.strip()) for s in self.ratings_input.value.split(",")]
            if len(scores) < len(self.criteria_names):
                return await interaction.response.send_message(f"❌ Please provide {len(self.criteria_names)} ratings.", ephemeral=True)

            for i, name in enumerate(self.criteria_names):
                score = scores[i]
                if not 1 <= score <= 5: raise ValueError
                ratings[name] = score
        except:
            return await interaction.response.send_message("❌ Invalid ratings. Use comma-separated numbers 1-5.", ephemeral=True)
        
        if self.review_type == "self":
            await self.system.submit_self_review(self.guild_id, self.target_id, ratings)
        elif self.review_type == "peer":
            await self.system.submit_peer_review(self.guild_id, self.voter_id, self.target_id, ratings)
        elif self.review_type == "admin":
            await self.system.submit_admin_review(self.guild_id, self.voter_id, self.target_id, ratings)

        await interaction.response.send_message(f"✅ {self.review_type.title()} review submitted!", ephemeral=True)


def setup(bot):
    return StaffReviewSystem(bot)
