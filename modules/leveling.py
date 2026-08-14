import discord
from discord import ui
import time
import random
import math
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class LevelingSystem:
    """
    Complete leveling system with XP, leveling, rewards, and leaderboards.
    Features:
    - XP from messages with cooldowns
    - Level-up announcements
    - Role rewards at levels
    - Level leaderboards
    - XP multipliers for special roles/channels
    - Blacklisted channels
    """

    def __init__(self, bot):
        self.bot = bot

    # Core data methods
    def get_user_data(self, guild_id: int, user_id: int) -> dict:
        """Get user's leveling data."""
        users = dm.get_guild_data(guild_id, "leveling_users", {})
        return users.get(str(user_id), {"xp": 0, "level": 1, "last_message": 0})

    def update_user_data(self, guild_id: int, user_id: int, data: dict):
        """Update user's leveling data."""
        users = dm.get_guild_data(guild_id, "leveling_users", {})
        users[str(user_id)] = data
        dm.update_guild_data(guild_id, "leveling_users", users)

    def calculate_level(self, xp: int) -> int:
        """Calculate level from XP using quadratic formula."""
        # Level = (-1 + sqrt(1 + 8*xp/100)) / 2
        # This gives levels like: 1=100, 2=300, 3=600, 4=1000, etc.
        if xp < 100:
            return 1
        return int((-1 + math.sqrt(1 + 8 * xp / 100)) / 2) + 1

    def calculate_xp_needed(self, level: int) -> int:
        """Calculate XP needed for next level."""
        # XP = 100 * level * (level + 1) / 2
        return int(100 * level * (level + 1) / 2)

    # Message handling
    async def handle_message(self, message):
        """Award XP for messages."""
        if message.author.bot or not message.guild:
            return

        config = dm.get_guild_data(message.guild.id, "leveling_config", {})
        if not config.get("enabled", False):
            return

        user_id = message.author.id
        guild_id = message.guild.id

        # Check blacklisted channels
        blacklisted = config.get("blacklisted_channels", [])
        if message.channel.id in blacklisted:
            return

        # Check cooldown
        user_data = self.get_user_data(guild_id, user_id)
        cooldown = config.get("message_cooldown", 60)

        if time.time() - user_data.get("last_message", 0) < cooldown:
            return

        # Calculate XP reward
        base_xp = config.get("xp_per_message", 10)

        # Apply multipliers
        multiplier = self.get_xp_multiplier(guild_id, message.author, message.channel)
        xp_reward = int(base_xp * multiplier)

        # Add XP
        current_xp = user_data.get("xp", 0)
        new_xp = current_xp + xp_reward
        new_level = self.calculate_level(new_xp)

        old_level = user_data.get("level", 1)
        leveled_up = new_level > old_level

        # Update user data
        user_data["xp"] = new_xp
        user_data["level"] = new_level
        user_data["last_message"] = time.time()
        self.update_user_data(guild_id, user_id, user_data)

        # Handle level up
        if leveled_up:
            await self.handle_level_up(message, old_level, new_level)

    def get_xp_multiplier(self, guild_id: int, member: discord.Member, channel: discord.TextChannel) -> float:
        """Calculate XP multiplier for user/channel."""
        config = dm.get_guild_data(guild_id, "leveling_config", {})
        multiplier = 1.0

        # Role multipliers
        role_multipliers = config.get("role_multipliers", {})
        for role in member.roles:
            if str(role.id) in role_multipliers:
                multiplier *= role_multipliers[str(role.id)]

        # Channel multipliers
        channel_multipliers = config.get("channel_multipliers", {})
        if str(channel.id) in channel_multipliers:
            multiplier *= channel_multipliers[str(channel.id)]

        return multiplier

    async def handle_level_up(self, message, old_level: int, new_level: int):
        """Handle level up event."""
        config = dm.get_guild_data(message.guild.id, "leveling_config", {})

        # Send level up message
        if config.get("announce_level_ups", True):
            channel_id = config.get("announce_channel")
            channel = None

            if channel_id:
                channel = message.guild.get_channel(channel_id)
            else:
                channel = message.channel

            if channel:
                embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=f"{message.author.mention} reached **Level {new_level}**!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Previous Level", value=str(old_level), inline=True)
                embed.add_field(name="New Level", value=str(new_level), inline=True)

                try:
                    await channel.send(embed=embed)
                except:
                    pass

        # Assign role rewards
        await self.assign_level_rewards(message.guild, message.author, new_level)

    async def assign_level_rewards(self, guild: discord.Guild, member: discord.Member, level: int):
        """Assign role rewards for reaching levels."""
        config = dm.get_guild_data(guild.id, "leveling_config", {})
        role_rewards = config.get("role_rewards", {})

        if str(level) in role_rewards:
            role_id = role_rewards[str(level)]
            try:
                role = guild.get_role(int(role_id))
                if role and role not in member.roles:
                    await member.add_roles(role)

                    # Announce role reward
                    if config.get("announce_role_rewards", True):
                        announce_channel = config.get("announce_channel")
                        if announce_channel:
                            channel = guild.get_channel(int(announce_channel))
                            if channel:
                                embed = discord.Embed(
                                    title="🏆 Role Reward!",
                                    description=f"{member.mention} earned the **{role.name}** role for reaching Level {level}!",
                                    color=role.color if role.color != discord.Color.default() else discord.Color.blue()
                                )
                                try:
                                    await channel.send(embed=embed)
                                except:
                                    pass
            except Exception as e:
                logger.error(f"Failed to assign level reward role {role_id}: {e}")

    # Commands
    async def rank(self, interaction):
        """Show user's rank card."""
        config = dm.get_guild_data(interaction.guild.id, "leveling_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Leveling system is disabled.", ephemeral=True)

        user_data = self.get_user_data(interaction.guild.id, interaction.user.id)
        xp = user_data.get("xp", 0)
        level = user_data.get("level", 1)

        # Calculate progress to next level
        current_level_xp = self.calculate_xp_needed(level - 1)
        next_level_xp = self.calculate_xp_needed(level)
        progress_xp = xp - current_level_xp
        needed_xp = next_level_xp - current_level_xp

        progress_percent = int((progress_xp / needed_xp) * 100) if needed_xp > 0 else 100

        # Get rank
        rank = self.get_user_rank(interaction.guild.id, interaction.user.id)

        embed = discord.Embed(
            title=f"🏆 {interaction.user.display_name}'s Rank",
            color=discord.Color.blue()
        )

        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=f"{xp:,}", inline=True)
        embed.add_field(name="Rank", value=f"#{rank}", inline=True)

        # Progress bar
        progress_bar = self.create_progress_bar(progress_xp, needed_xp)
        embed.add_field(
            name=f"Progress to Level {level + 1}",
            value=f"{progress_bar}\n{progress_xp:,}/{needed_xp:,} XP ({progress_percent}%)",
            inline=False
        )

        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def leaderboard(self, interaction):
        """Show leveling leaderboard (accepts an Interaction or a Message)."""
        guild_id = getattr(interaction, "guild", None)
        if guild_id is None:
            return
        guild_id = guild_id.id
        is_message = not hasattr(interaction, "response")

        config = dm.get_guild_data(guild_id, "leveling_config", {})
        if not config.get("enabled", False):
            if is_message:
                return await interaction.channel.send("❌ Leveling system is disabled.")
            return await interaction.response.send_message("❌ Leveling system is disabled.", ephemeral=True)

        users = dm.get_guild_data(guild_id, "leveling_users", {})

        if not users:
            if is_message:
                return await interaction.channel.send("📊 No leveling data yet!")
            return await interaction.response.send_message("📊 No leveling data yet!", ephemeral=True)

        # Sort by XP
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("xp", 0), reverse=True)[:10]

        embed = discord.Embed(
            title="🏆 Leveling Leaderboard",
            color=discord.Color.gold()
        )

        for i, (user_id, data) in enumerate(sorted_users, 1):
            try:
                user = self.bot.get_user(int(user_id))
                name = user.display_name if user else f"User {user_id}"
            except:
                name = f"User {user_id}"

            level = data.get("level", 1)
            xp = data.get("xp", 0)

            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
            embed.add_field(
                name=f"{medal} {name}",
                value=f"Level {level} • {xp:,} XP",
                inline=False
            )

        if is_message:
            return await interaction.channel.send(embed=embed)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def get_user_rank(self, guild_id: int, user_id: int) -> int:
        """Get user's rank in leaderboard."""
        users = dm.get_guild_data(guild_id, "leveling_users", {})
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("xp", 0), reverse=True)

        for rank, (uid, _) in enumerate(sorted_users, 1):
            if uid == str(user_id):
                return rank

        return len(sorted_users) + 1

    async def rewards(self, interaction):
        """Show level rewards."""
        config = dm.get_guild_data(interaction.guild.id, "leveling_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Leveling system is disabled.", ephemeral=True)

        role_rewards = config.get("role_rewards", {})

        if not role_rewards:
            return await interaction.response.send_message("🎁 No level rewards configured yet.", ephemeral=True)

        embed = discord.Embed(
            title="🎁 Level Rewards",
            description="Roles you can earn by leveling up!",
            color=discord.Color.purple()
        )

        # Sort by level
        sorted_rewards = sorted(role_rewards.items(), key=lambda x: int(x[0]))

        for level_str, role_id in sorted_rewards:
            try:
                role = interaction.guild.get_role(int(role_id))
                if role:
                    embed.add_field(
                        name=f"Level {level_str}",
                        value=role.mention,
                        inline=True
                    )
            except:
                pass

        await interaction.response.send_message(embed=embed, ephemeral=True)

    def create_progress_bar(self, current: int, target: int, length: int = 15) -> str:
        """Create a visual progress bar."""
        if target == 0:
            return "█" * length

        filled = int((current / target) * length)
        empty = length - filled

        return "█" * filled + "░" * empty

    # ---- Compatibility API (consumed by actions.py, reaction_roles, gamification, shop) ----

    def get_xp(self, guild_id: int, user_id: int) -> int:
        """Get a user's total XP."""
        return self.get_user_data(guild_id, user_id).get("xp", 0)

    def add_xp(self, guild_id: int, user_id: int, amount: int):
        """Grant XP directly (used by reward systems like starboard)."""
        if amount <= 0:
            return
        users = dm.get_guild_data(guild_id, "leveling_users", {})
        user_data = users.get(str(user_id), {"xp": 0, "level": 1, "last_message": 0})
        user_data["xp"] = user_data.get("xp", 0) + amount
        user_data["level"] = self.calculate_level(user_data["xp"])
        users[str(user_id)] = user_data
        dm.update_guild_data(guild_id, "leveling_users", users)

    def get_level_from_xp(self, xp: int) -> int:
        """Get level from XP (alias for calculate_level)."""
        return self.calculate_level(xp)

    def get_xp_for_next_level(self, level: int) -> int:
        """XP needed to reach the next level (alias for calculate_xp_needed)."""
        return self.calculate_xp_needed(level)

    def get_gems(self, guild_id: int, user_id: int) -> int:
        """Get a user's gem balance (shared with the economy system)."""
        gems = dm.get_guild_data(guild_id, "economy_gems", {})
        return int(gems.get(str(user_id), 0))

    def add_gems(self, guild_id: int, user_id: int, amount: int):
        """Add gems to a user's balance."""
        gems = dm.get_guild_data(guild_id, "economy_gems", {})
        gems[str(user_id)] = int(gems.get(str(user_id), 0)) + amount
        dm.update_guild_data(guild_id, "economy_gems", gems)

    def spend_gems(self, guild_id: int, user_id: int, amount: int) -> bool:
        """Spend gems if the user has enough. Returns True on success."""
        gems = dm.get_guild_data(guild_id, "economy_gems", {})
        current = int(gems.get(str(user_id), 0))
        if current < amount:
            return False
        gems[str(user_id)] = current - amount
        dm.update_guild_data(guild_id, "economy_gems", gems)
        return True

    def get_prestige(self, guild_id: int, user_id: int) -> int:
        """Get a user's prestige level."""
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        return int(user_data.get("prestige", 0))

    def get_streak(self, guild_id: int, user_id: int) -> int:
        """Get a user's daily streak."""
        streaks = dm.get_guild_data(guild_id, "daily_streaks", {})
        return int(streaks.get(str(user_id), 0))

    def get_leaderboard(self, guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the XP leaderboard as a list of {rank, user_id, level, xp, streak}."""
        users = dm.get_guild_data(guild_id, "leveling_users", {})
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("xp", 0), reverse=True)
        board = []
        for rank, (uid, data) in enumerate(sorted_users[:limit], 1):
            board.append({
                "rank": rank,
                "user_id": int(uid),
                "level": data.get("level", self.calculate_level(data.get("xp", 0))),
                "xp": data.get("xp", 0),
                "streak": self.get_streak(guild_id, int(uid)),
            })
        return board

    def get_hourly_stats(self, guild_id: int, hours: int = 24) -> Dict[str, Any]:
        """Get leveling activity stats for the last N hours (used by server analytics)."""
        users = dm.get_guild_data(guild_id, "leveling_users", {})
        now = time.time()
        window_start = now - hours * 3600
        active = 0
        messages = 0
        xp_gained = 0
        for uid, data in users.items():
            if data.get("last_message", 0) >= window_start:
                active += 1
                messages += 1
            xp_gained += data.get("xp", 0)
        return {
            "active_users": active,
            "messages": messages,
            "xp_total": xp_gained,
            "hours": hours,
            "timestamp": now,
        }

    # Config panel
    def get_config_panel(self, guild_id: int):
        """Get leveling config panel view."""
        return LevelingConfigPanel(self.bot, guild_id)

class LevelingConfigPanel(discord.ui.View):
    """Config panel for leveling system."""

    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.leveling = LevelingSystem(bot)

    @discord.ui.button(label="Toggle Leveling", style=discord.ButtonStyle.primary, row=0)
    async def toggle_leveling(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "leveling_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "leveling_config", config)

        await interaction.response.send_message(
            f"✅ Leveling system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Set XP Rate", style=discord.ButtonStyle.secondary, row=0)
    async def set_xp_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetXPRateModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Role Reward", style=discord.ButtonStyle.success, row=1)
    async def add_role_reward(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddRoleRewardModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Announce Channel", style=discord.ButtonStyle.secondary, row=1)
    async def set_announce_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetAnnounceChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View Leaderboard", style=discord.ButtonStyle.primary, row=2)
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.leveling.leaderboard(interaction)

class SetXPRateModal(discord.ui.Modal, title="Set XP Rate"):
    xp_per_message = discord.ui.TextInput(label="XP per Message", placeholder="10")
    cooldown = discord.ui.TextInput(label="Message Cooldown (seconds)", placeholder="60")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            xp = int(self.xp_per_message.value)
            cd = int(self.cooldown.value)

            if xp < 1 or cd < 1:
                raise ValueError

            config = dm.get_guild_data(self.guild_id, "leveling_config", {})
            config["xp_per_message"] = xp
            config["message_cooldown"] = cd
            dm.update_guild_data(self.guild_id, "leveling_config", config)

            await interaction.response.send_message(f"✅ XP rate set to {xp} per message with {cd}s cooldown", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers", ephemeral=True)

class AddRoleRewardModal(discord.ui.Modal, title="Add Role Reward"):
    level = discord.ui.TextInput(label="Level", placeholder="5")
    role_id = discord.ui.TextInput(label="Role ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            level = int(self.level.value)
            role_id = int(self.role_id.value)

            if level < 1:
                raise ValueError

            # Verify role exists
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message("❌ Role not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "leveling_config", {})
            role_rewards = config.get("role_rewards", {})
            role_rewards[str(level)] = str(role_id)
            config["role_rewards"] = role_rewards
            dm.update_guild_data(self.guild_id, "leveling_config", config)

            await interaction.response.send_message(f"✅ Added {role.name} reward for Level {level}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers", ephemeral=True)

class SetAnnounceChannelModal(discord.ui.Modal, title="Set Announce Channel"):
    channel_id = discord.ui.TextInput(label="Channel ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ Text channel not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "leveling_config", {})
            config["announce_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "leveling_config", config)

            await interaction.response.send_message(f"✅ Level up announcements set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)


Leveling = LevelingSystem