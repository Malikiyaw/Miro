"""Progression systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: economy.py, leveling.py, shop.py, gamification.py, tournaments.py
"""



# ======================================================================
# From: modules/economy.py
# ======================================================================

import discord
from discord import ui
import time
import random
import asyncio
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class EconomySystem:
    """
    Complete economy system with coins, gems, shop, daily challenges, and transactions.
    Features:
    - Coins and gems currency
    - Daily rewards and streaks
    - Shop system with items
    - Daily challenges for bonus rewards
    - Transaction logging
    - Leaderboards
    - Work commands
    - Gambling (safe, no loss of real money)
    """

    def __init__(self, bot):
        self.bot = bot

    # Core data methods
    def get_coins(self, guild_id: int, user_id: int) -> int:
        """Get user's coin balance."""
        balances = dm.get_guild_data(guild_id, "economy_balances", {})
        return balances.get(str(user_id), 0)

    def get_gems(self, guild_id: int, user_id: int) -> int:
        """Get user's gem balance."""
        balances = dm.get_guild_data(guild_id, "economy_gems", {})
        return balances.get(str(user_id), 0)

    def add_coins(self, guild_id: int, user_id: int, amount: int):
        """Add coins to user's balance."""
        balances = dm.get_guild_data(guild_id, "economy_balances", {})
        current = balances.get(str(user_id), 0)
        balances[str(user_id)] = max(0, current + amount)
        dm.update_guild_data(guild_id, "economy_balances", balances)

        if amount != 0:
            self.log_transaction(guild_id, user_id, amount, "coins", "balance_update")

    def add_gems(self, guild_id: int, user_id: int, amount: int):
        """Add gems to user's balance."""
        gems = dm.get_guild_data(guild_id, "economy_gems", {})
        current = gems.get(str(user_id), 0)
        gems[str(user_id)] = max(0, current + amount)
        dm.update_guild_data(guild_id, "economy_gems", gems)

    def transfer_coins(self, guild_id: int, from_user: int, to_user: int, amount: int) -> bool:
        """Transfer coins between users."""
        if amount <= 0:
            return False

        from_balance = self.get_coins(guild_id, from_user)
        if from_balance < amount:
            return False

        self.add_coins(guild_id, from_user, -amount)
        self.add_coins(guild_id, to_user, amount)

        self.log_transaction(guild_id, from_user, -amount, "transfer_out", f"To {to_user}")
        self.log_transaction(guild_id, to_user, amount, "transfer_in", f"From {from_user}")

        return True

    def log_transaction(self, guild_id: int, user_id: int, amount: int, tx_type: str, reason: str):
        """Log a transaction."""
        transactions = dm.get_guild_data(guild_id, "economy_transactions", [])
        transactions.append({
            "user_id": user_id,
            "amount": amount,
            "type": tx_type,
            "reason": reason,
            "timestamp": time.time()
        })

        # Keep last 1000 transactions
        if len(transactions) > 1000:
            transactions = transactions[-1000:]

        dm.update_guild_data(guild_id, "economy_transactions", transactions)

    # Passive income system
    async def handle_message(self, message):
        """Handle passive coin earning from messages."""
        if message.author.bot or not message.guild:
            return

        config = dm.get_guild_data(message.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return

        # Cooldown check
        last_earn = dm.get_guild_data(message.guild.id, f"last_earn_{message.author.id}", 0)
        cooldown = config.get("message_cooldown", 60)

        if time.time() - last_earn < cooldown:
            return

        # Award coins
        rates = config.get("earn_rates", {})
        coins = rates.get("coins_per_message", 2)
        self.add_coins(message.guild.id, message.author.id, coins)

        # Chance for gems
        gem_chance = rates.get("gem_chance", 0.01)
        if random.random() < gem_chance:
            self.add_gems(message.guild.id, message.author.id, 1)
            try:
                await message.channel.send(f"✨ {message.author.mention} found a **Gem**!", delete_after=5)
            except:
                pass

        dm.update_guild_data(message.guild.id, f"last_earn_{message.author.id}", time.time())

    # Commands
    async def balance(self, interaction):
        """Show user's balance."""
        coins = self.get_coins(interaction.guild.id, interaction.user.id)
        gems = self.get_gems(interaction.guild.id, interaction.user.id)

        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        coin_emoji = config.get("coin_emoji", "🪙")
        gem_emoji = config.get("gem_emoji", "💎")

        embed = discord.Embed(
            title="💰 Your Balance",
            color=discord.Color.gold()
        )
        embed.add_field(name=f"{coin_emoji} Coins", value=f"{coins:,}", inline=True)
        embed.add_field(name=f"{gem_emoji} Gems", value=f"{gems:,}", inline=True)
        embed.set_footer(text="Use /daily for daily rewards!")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def daily(self, interaction):
        """Claim daily reward."""
        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Economy system is disabled.", ephemeral=True)

        user_id = interaction.user.id
        guild_id = interaction.guild.id

        # Check cooldown
        daily_data = dm.get_guild_data(guild_id, "daily_claims", {})
        last_claim = daily_data.get(str(user_id), 0)
        cooldown = config.get("daily_cooldown", 86400)  # 24 hours

        if time.time() - last_claim < cooldown:
            remaining = int(cooldown - (time.time() - last_claim))
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            return await interaction.response.send_message(
                f"⏳ Daily reward available in {hours}h {minutes}m",
                ephemeral=True
            )

        # Calculate reward with streak bonus
        base_reward = config.get("daily_amount", 100)
        streak = self.get_daily_streak(guild_id, user_id)
        streak_bonus = config.get("streak_bonus", 50)

        # Bonus every 7 days
        bonus = 0
        if streak > 0 and streak % 7 == 0:
            bonus = streak_bonus * (streak // 7)

        total_reward = base_reward + bonus

        # Award coins
        self.add_coins(guild_id, user_id, total_reward)
        self.log_transaction(guild_id, user_id, total_reward, "daily", f"Streak: {streak}")

        # Update streak and claim time
        self.update_daily_streak(guild_id, user_id)
        daily_data[str(user_id)] = time.time()
        dm.update_guild_data(guild_id, "daily_claims", daily_data)

        # Response
        coin_emoji = config.get("coin_emoji", "🪙")
        embed = discord.Embed(
            title="🎉 Daily Reward Claimed!",
            color=discord.Color.green()
        )
        embed.add_field(name="Reward", value=f"{coin_emoji} {total_reward:,}", inline=True)

        if bonus > 0:
            embed.add_field(name="Streak Bonus", value=f"{coin_emoji} +{bonus:,}", inline=True)

        embed.add_field(name="Current Streak", value=f"{streak + 1} days", inline=True)
        embed.set_footer(text="Keep claiming daily for bigger rewards!")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    def get_daily_streak(self, guild_id: int, user_id: int) -> int:
        """Get user's daily streak."""
        streaks = dm.get_guild_data(guild_id, "daily_streaks", {})
        return streaks.get(str(user_id), 0)

    def update_daily_streak(self, guild_id: int, user_id: int):
        """Update daily streak for user."""
        streaks = dm.get_guild_data(guild_id, "daily_streaks", {})
        current_streak = streaks.get(str(user_id), 0)
        streaks[str(user_id)] = current_streak + 1
        dm.update_guild_data(guild_id, "daily_streaks", streaks)

    async def work(self, interaction):
        """Work command for earning coins."""
        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Economy system is disabled.", ephemeral=True)

        user_id = interaction.user.id
        guild_id = interaction.guild.id

        # Check cooldown
        work_data = dm.get_guild_data(guild_id, "work_cooldowns", {})
        last_work = work_data.get(str(user_id), 0)
        cooldown = config.get("work_cooldown", 3600)  # 1 hour

        if time.time() - last_work < cooldown:
            remaining = int(cooldown - (time.time() - last_work))
            return await interaction.response.send_message(
                f"⏳ You can work again in {remaining // 3600}h {(remaining % 3600) // 60}m",
                ephemeral=True
            )

        # Random work reward
        jobs = [
            ("Programmer", (50, 200)),
            ("Chef", (30, 150)),
            ("Teacher", (40, 180)),
            ("Artist", (25, 120)),
            ("Musician", (35, 160)),
            ("Writer", (20, 100)),
            ("Designer", (45, 190)),
            ("Scientist", (60, 250))
        ]

        job_name, (min_reward, max_reward) = random.choice(jobs)
        reward = random.randint(min_reward, max_reward)

        self.add_coins(guild_id, user_id, reward)
        self.log_transaction(guild_id, user_id, reward, "work", job_name)

        # Update cooldown
        work_data[str(user_id)] = time.time()
        dm.update_guild_data(guild_id, "work_cooldowns", work_data)

        coin_emoji = config.get("coin_emoji", "🪙")
        embed = discord.Embed(
            title="💼 Work Complete!",
            description=f"You worked as a **{job_name}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="Earned", value=f"{coin_emoji} {reward:,}", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def transfer(self, interaction, target: discord.Member, amount: int):
        """Transfer coins to another user."""
        if amount <= 0:
            return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)

        if target.id == interaction.user.id:
            return await interaction.response.send_message("❌ You can't transfer to yourself.", ephemeral=True)

        if target.bot:
            return await interaction.response.send_message("❌ You can't transfer to bots.", ephemeral=True)

        success = self.transfer_coins(interaction.guild.id, interaction.user.id, target.id, amount)

        if not success:
            return await interaction.response.send_message("❌ Insufficient funds.", ephemeral=True)

        coin_emoji = dm.get_guild_data(interaction.guild.id, "economy_config", {}).get("coin_emoji", "🪙")
        await interaction.response.send_message(
            f"✅ Transferred {coin_emoji} {amount:,} to {target.mention}",
            ephemeral=True
        )

    async def shop(self, interaction):
        """Show shop items."""
        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Economy system is disabled.", ephemeral=True)

        _raw = dm.get_guild_data(interaction.guild.id, "shop_items", [])
        if isinstance(_raw, dict):
            shop_items = [{"name": k, **v} for k, v in _raw.items() if isinstance(v, dict)]
        elif isinstance(_raw, list):
            shop_items = [x for x in _raw if isinstance(x, dict)]
        else:
            shop_items = []

        if not shop_items:
            return await interaction.response.send_message("🛒 Shop is empty. Add items via config panel.", ephemeral=True)

        embed = discord.Embed(
            title="🛒 Server Shop",
            description="Purchase items with your coins!",
            color=discord.Color.blue()
        )

        gem_emoji = config.get("gem_emoji", "💎")

        for item in shop_items[:10]:  # Show first 10 items
            currency = f"{gem_emoji} Gems" if item.get("gem_cost") else f"{config.get('coin_emoji', '🪙')} Coins"
            cost = item.get("gem_cost", item.get("price", 0))

            embed.add_field(
                name=f"{item['name']} - {currency} {cost:,}",
                value=item.get("description", "No description"),
                inline=False
            )

        embed.set_footer(text="Use /buy <item_name> to purchase")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def buy(self, interaction, item_name: str):
        """Buy an item from the shop."""
        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Economy system is disabled.", ephemeral=True)

        _raw = dm.get_guild_data(interaction.guild.id, "shop_items", [])
        if isinstance(_raw, dict):
            shop_items = [{"name": k, **v} for k, v in _raw.items() if isinstance(v, dict)]
        elif isinstance(_raw, list):
            shop_items = [x for x in _raw if isinstance(x, dict)]
        else:
            shop_items = []

        # Find item
        item = None
        for shop_item in shop_items:
            if shop_item["name"].lower() == item_name.lower():
                item = shop_item
                break

        if not item:
            return await interaction.response.send_message(f"❌ Item '{item_name}' not found in shop.", ephemeral=True)

        user_id = interaction.user.id
        guild_id = interaction.guild.id

        # Check if using gems or coins
        if item.get("gem_cost"):
            currency = "gems"
            cost = item["gem_cost"]
            balance = self.get_gems(guild_id, user_id)
            currency_emoji = config.get("gem_emoji", "💎")
        else:
            currency = "coins"
            cost = item.get("price", 0)
            balance = self.get_coins(guild_id, user_id)
            currency_emoji = config.get("coin_emoji", "🪙")

        if balance < cost:
            return await interaction.response.send_message(
                f"❌ Insufficient {currency}. You have {currency_emoji} {balance:,} but need {cost:,}",
                ephemeral=True
            )

        # Process purchase
        if currency == "gems":
            self.add_gems(guild_id, user_id, -cost)
        else:
            self.add_coins(guild_id, user_id, -cost)

        # Assign role if applicable
        role_assigned = False
        if item.get("role_id"):
            try:
                role = interaction.guild.get_role(int(item["role_id"]))
                if role:
                    await interaction.user.add_roles(role)
                    role_assigned = True
            except:
                pass

        self.log_transaction(guild_id, user_id, -cost, f"purchase_{currency}", item["name"])

        embed = discord.Embed(
            title="✅ Purchase Successful!",
            description=f"You bought **{item['name']}**",
            color=discord.Color.green()
        )

        if role_assigned:
            embed.add_field(name="Role Assigned", value=f"You now have the {role.name} role!", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def leaderboard(self, interaction):
        """Show economy leaderboard."""
        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Economy system is disabled.", ephemeral=True)

        balances = dm.get_guild_data(interaction.guild.id, "economy_balances", {})

        if not balances:
            return await interaction.response.send_message("📊 No one has coins yet!", ephemeral=True)

        # Sort by balance
        sorted_users = sorted(balances.items(), key=lambda x: x[1], reverse=True)[:10]

        embed = discord.Embed(
            title="🏆 Economy Leaderboard",
            color=discord.Color.gold()
        )

        coin_emoji = config.get("coin_emoji", "🪙")

        for i, (user_id, balance) in enumerate(sorted_users, 1):
            try:
                user = self.bot.get_user(int(user_id))
                name = user.display_name if user else f"User {user_id}"
            except:
                name = f"User {user_id}"

            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
            embed.add_field(
                name=f"{medal} {name}",
                value=f"{coin_emoji} {balance:,}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Daily challenges
    async def challenge(self, interaction):
        """View daily challenge progress."""
        config = dm.get_guild_data(interaction.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Economy system is disabled.", ephemeral=True)

        challenge_data = self.get_daily_challenge(interaction.guild.id)
        user_progress = self.get_user_challenge_progress(interaction.guild.id, interaction.user.id)

        embed = discord.Embed(
            title="🎯 Daily Challenge",
            description=challenge_data.get("desc", "Complete daily tasks for bonus rewards!"),
            color=discord.Color.blue()
        )

        progress = user_progress.get("progress", 0)
        target = challenge_data.get("target", 1)
        completed = user_progress.get("completed", False)

        if completed:
            embed.add_field(
                name="✅ Completed!",
                value=f"You earned {challenge_data.get('reward', 0)} bonus coins!",
                inline=False
            )
        else:
            percent = int((progress / target) * 100)
            progress_bar = self.create_progress_bar(progress, target)
            embed.add_field(
                name=f"Progress: {progress}/{target} ({percent}%)",
                value=progress_bar,
                inline=False
            )
            embed.add_field(
                name="Reward",
                value=f"🪙 {challenge_data.get('reward', 0)} coins",
                inline=True
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    def get_daily_challenge(self, guild_id: int) -> dict:
        """Get today's daily challenge."""
        challenges = dm.get_guild_data(guild_id, "daily_challenges", {})

        # Check if we need a new challenge
        today = time.strftime("%Y-%m-%d")
        if challenges.get("date") != today:
            challenge = self.generate_daily_challenge()
            challenge["date"] = today
            challenge["progress"] = {}
            dm.update_guild_data(guild_id, "daily_challenges", challenge)
            return challenge

        return challenges

    def generate_daily_challenge(self) -> dict:
        """Generate a random daily challenge."""
        challenges = [
            {"id": "messages", "name": "Chatty", "desc": "Send 25 messages", "target": 25, "reward": 150},
            {"id": "reactions", "name": "Reactor", "desc": "Add 10 reactions", "target": 10, "reward": 100},
            {"id": "voice", "name": "Social", "desc": "Join voice channel for 30 minutes", "target": 1800, "reward": 200},
            {"id": "invite", "name": "Inviter", "desc": "Create 1 invite", "target": 1, "reward": 300},
            {"id": "help", "name": "Helper", "desc": "Use 3 help commands", "target": 3, "reward": 75}
        ]
        return random.choice(challenges)

    def get_user_challenge_progress(self, guild_id: int, user_id: int) -> dict:
        """Get user's progress on current challenge."""
        challenge = self.get_daily_challenge(guild_id)
        progress = challenge.get("progress", {}).get(str(user_id), 0)
        completed_users = dm.get_guild_data(guild_id, "challenge_completed", {})
        completed = str(user_id) in completed_users

        return {
            "progress": progress,
            "completed": completed
        }

    def update_challenge_progress(self, guild_id: int, user_id: int, challenge_type: str):
        """Update user's challenge progress."""
        challenge = self.get_daily_challenge(guild_id)

        if challenge.get("id") != challenge_type:
            return

        progress = challenge.get("progress", {})
        current = progress.get(str(user_id), 0)
        progress[str(user_id)] = current + 1

        challenge["progress"] = progress
        dm.update_guild_data(guild_id, "daily_challenges", challenge)

        # Check completion
        if current + 1 >= challenge.get("target", 1):
            self.complete_challenge(guild_id, user_id, challenge)

    def complete_challenge(self, guild_id: int, user_id: int, challenge: dict):
        """Mark challenge as completed and award reward."""
        completed = dm.get_guild_data(guild_id, "challenge_completed", {})
        if str(user_id) in completed:
            return  # Already completed

        reward = challenge.get("reward", 0)
        self.add_coins(guild_id, user_id, reward)

        completed[str(user_id)] = {
            "date": time.strftime("%Y-%m-%d"),
            "challenge_id": challenge.get("id"),
            "reward": reward
        }

        dm.update_guild_data(guild_id, "challenge_completed", completed)

    def create_progress_bar(self, current: int, target: int, length: int = 10) -> str:
        """Create a visual progress bar."""
        if target == 0:
            return "█" * length

        filled = int((current / target) * length)
        empty = length - filled

        return "█" * filled + "░" * empty

    # Config panel
    def get_config_panel(self, guild_id: int):
        """Get economy config panel view."""
        return EconomyConfigPanel(self.bot, guild_id)

class EconomyConfigPanel(discord.ui.View):
    """Config panel for economy system."""

    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.economy = EconomySystem(bot)

    @discord.ui.button(label="Toggle Economy", style=discord.ButtonStyle.primary, row=0)
    async def toggle_economy(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "economy_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "economy_config", config)

        await interaction.response.send_message(
            f"✅ Economy system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Set Daily Reward", style=discord.ButtonStyle.secondary, row=0)
    async def set_daily_reward(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetDailyRewardModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Shop Item", style=discord.ButtonStyle.success, row=1)
    async def add_shop_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddShopItemModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View Leaderboard", style=discord.ButtonStyle.primary, row=1)
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.economy.leaderboard(interaction)

    @discord.ui.button(label="Reset User Balance", style=discord.ButtonStyle.danger, row=2)
    async def reset_balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ResetBalanceModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class SetDailyRewardModal(discord.ui.Modal, title="Set Daily Reward"):
    amount = discord.ui.TextInput(label="Daily Coin Amount", placeholder="100")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value)
            if amount < 0:
                raise ValueError

            config = dm.get_guild_data(self.guild_id, "economy_config", {})
            config["daily_amount"] = amount
            dm.update_guild_data(self.guild_id, "economy_config", config)

            await interaction.response.send_message(f"✅ Daily reward set to {amount} coins", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number", ephemeral=True)

class AddShopItemModal(discord.ui.Modal, title="Add Shop Item"):
    name = discord.ui.TextInput(label="Item Name", placeholder="VIP Role")
    price = discord.ui.TextInput(label="Price (Coins)", placeholder="1000")
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="Grants VIP access")
    role_id = discord.ui.TextInput(label="Role ID (optional)", required=False, placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(self.price.value)
            if price < 0:
                raise ValueError

            items = dm.get_guild_data(self.guild_id, "shop_items", [])
            item = {
                "id": len(items) + 1,
                "name": self.name.value,
                "price": price,
                "description": self.description.value,
                "role_id": self.role_id.value if self.role_id.value else None
            }
            items.append(item)
            dm.update_guild_data(self.guild_id, "shop_items", items)

            await interaction.response.send_message(f"✅ Added '{self.name.value}' to shop", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid price", ephemeral=True)

class ResetBalanceModal(discord.ui.Modal, title="Reset User Balance"):
    user_id = discord.ui.TextInput(label="User ID", placeholder="123456789")
    confirm = discord.ui.TextInput(label="Type 'RESET' to confirm", placeholder="RESET")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.economy = EconomySystem(bot)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "RESET":
            return await interaction.response.send_message("❌ Confirmation failed", ephemeral=True)

        try:
            user_id = int(self.user_id.value)
            old_balance = self.economy.get_coins(self.guild_id, user_id)

            balances = dm.get_guild_data(self.guild_id, "economy_balances", {})
            balances[str(user_id)] = 0
            dm.update_guild_data(self.guild_id, "economy_balances", balances)

            self.economy.log_transaction(self.guild_id, user_id, -old_balance, "admin_reset", "Admin reset")

            await interaction.response.send_message(f"✅ Reset balance from {old_balance} to 0", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID", ephemeral=True)


class Economy(EconomySystem):
    """Message-based compatibility facade used by actions.py custom-command handlers."""

    async def rob(self, message, args=None):
        """!economy rob @target — attempt to steal coins from another member."""
        config = dm.get_guild_data(message.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await message.channel.send("❌ Economy system is disabled.")

        if message.mentions:
            target = message.mentions[0]
        elif len(args or []) > 2:
            target = await self.bot.fetch_user(int(args[2].strip("<@!>")))
        else:
            return await message.channel.send("❌ Usage: `!economy rob @user`")

        if target.id == message.author.id:
            return await message.channel.send("❌ You can't rob yourself!")

        target_bal = self.get_coins(message.guild.id, target.id)
        if target_bal <= 0:
            return await message.channel.send(f"💨 {target.display_name} has no coins to steal!")

        chance = config.get("rob_chance", 0.4)
        if random.random() < chance:
            steal = min(int(target_bal * config.get("rob_max_percent", 0.15)), target_bal)
            self.add_coins(message.guild.id, target.id, -steal)
            self.add_coins(message.guild.id, message.author.id, steal)
            self.log_transaction(message.guild.id, message.author.id, steal, "rob", f"Robbed {target.display_name}")
            await message.channel.send(f"🦹 You robbed **{target.display_name}** and got away with {config.get('coin_emoji', '🪙')} **{steal:,}**!")
        else:
            fine = min(int(target_bal * 0.05), self.get_coins(message.guild.id, message.author.id))
            if fine > 0:
                self.add_coins(message.guild.id, message.author.id, -fine)
                self.add_coins(message.guild.id, target.id, fine)
            await message.channel.send(f"🚨 You got caught! **{target.display_name}** caught you and took {config.get('coin_emoji', '🪙')} **{fine:,}**!")

    async def buy(self, message, args=None):
        """!economy buy <item> — buy an item from the shop."""
        config = dm.get_guild_data(message.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await message.channel.send("❌ Economy system is disabled.")

        item_name = " ".join((args or message.content.split())[2:]).strip('"')
        if not item_name:
            return await message.channel.send("❌ Usage: `!economy buy <item name>`")

        shop_items = dm.get_guild_data(message.guild.id, "shop_items", [])
        item = next((i for i in shop_items if i["name"].lower() == item_name.lower()), None)
        if not item:
            return await message.channel.send(f"❌ Item '{item_name}' not found in shop.")

        user_id = message.author.id
        guild_id = message.guild.id
        if item.get("gem_cost"):
            currency, cost, balance = "gems", item["gem_cost"], self.get_gems(guild_id, user_id)
            emoji = config.get("gem_emoji", "💎")
        else:
            currency, cost, balance = "coins", item.get("price", 0), self.get_coins(guild_id, user_id)
            emoji = config.get("coin_emoji", "🪙")

        if balance < cost:
            return await message.channel.send(f"❌ Insufficient {currency}. You have {emoji} {balance:,} but need {cost:,}")

        if currency == "gems":
            self.add_gems(guild_id, user_id, -cost)
        else:
            self.add_coins(guild_id, user_id, -cost)

        role_assigned = False
        if item.get("role_id"):
            try:
                role = message.guild.get_role(int(item["role_id"]))
                if role:
                    await message.author.add_roles(role)
                    role_assigned = True
            except Exception:
                pass

        self.log_transaction(guild_id, user_id, -cost, f"purchase_{currency}", item["name"])

        embed = discord.Embed(
            title="✅ Purchase Successful!",
            description=f"You bought **{item['name']}**",
            color=discord.Color.green()
        )
        if role_assigned:
            embed.add_field(name="Role Assigned", value=f"You now have the {role.name} role!", inline=False)
        await message.channel.send(embed=embed)

    async def leaderboard(self, message):
        """!economy leaderboard — show the richest members."""
        config = dm.get_guild_data(message.guild.id, "economy_config", {})
        if not config.get("enabled", False):
            return await message.channel.send("❌ Economy system is disabled.")

        balances = dm.get_guild_data(message.guild.id, "economy_balances", {})
        if not balances:
            return await message.channel.send("📊 No one has coins yet!")

        sorted_users = sorted(balances.items(), key=lambda x: x[1], reverse=True)[:10]
        embed = discord.Embed(title="🏆 Economy Leaderboard", color=discord.Color.gold())
        coin_emoji = config.get("coin_emoji", "🪙")
        for i, (uid, balance) in enumerate(sorted_users, 1):
            user = self.bot.get_user(int(uid))
            name = user.display_name if user else f"User {uid}"
            medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
            embed.add_field(name=f"{medal} {name}", value=f"{coin_emoji} {balance:,}", inline=False)
        await message.channel.send(embed=embed)



# ======================================================================
# From: modules/leveling.py
# ======================================================================

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



# ======================================================================
# From: modules/shop.py
# ======================================================================

import discord
from data_manager import dm
import datetime
import time

class Shop:
    """
    Shop for Coins and Gems.
    Now includes limited items, discounts, and more item types!
    """
    def __init__(self, bot):
        self.bot = bot
    
    def get_shop_items(self, guild_id: int):
        return dm.get_guild_data(guild_id, "shop_items", self._default_items())
    
    def _default_items(self):
        return {
            "VIP Role": {"price": 1000, "currency": "coins", "type": "role", "role_id": 0, "stock": -1},
            "Gem Master": {"price": 50, "currency": "gems", "type": "role", "role_id": 0, "stock": -1},
            "Custom Color": {"price": 500, "currency": "coins", "type": "color", "stock": -1},
            "Server Banner": {"price": 1000, "currency": "coins", "type": "banner", "stock": 10},
            "Private Channel": {"price": 2000, "currency": "coins", "type": "channel", "stock": 5}
        }
    
    """Limited Items System"""
    def get_stock(self, guild_id: int, item_name: str) -> int:
        stock_data = dm.get_guild_data(guild_id, "shop_stock", {})
        return stock_data.get(item_name, -1)  # -1 = unlimited
    
    def update_stock(self, guild_id: int, item_name: str, change: int):
        if change == 0:
            return
        
        stock_data = dm.get_guild_data(guild_id, "shop_stock", {})
        
        if item_name not in stock_data:
            stock_data[item_name] = 0
        
        stock_data[item_name] += change
        
        dm.update_guild_data(guild_id, "shop_stock", stock_data)
    
    """Discounts System"""
    def get_active_discounts(self, guild_id: int) -> dict:
        discounts = dm.get_guild_data(guild_id, "shop_discounts", {})
        active = {}
        
        for item_name, discount_data in discounts.items():
            if discount_data.get("expires", 0) > time.time():
                active[item_name] = discount_data
        
        return active
    
    def apply_discount(self, guild_id: int, item_name: str, percent: int, duration_hours: int = 24):
        """Apply discount to item."""
        discounts = dm.get_guild_data(guild_id, "shop_discounts", {})
        
        discounts[item_name] = {
            "percent": percent,
            "original_price": 0,  # Will be set when used
            "expires": time.time() + (duration_hours * 3600)
        }
        
        dm.update_guild_data(guild_id, "shop_discounts", discounts)
    
    def get_discounted_price(self, guild_id: int, item_name: str, base_price: int) -> int:
        """Get price with discount applied."""
        discounts = self.get_active_discounts(guild_id)
        
        if item_name not in discounts:
            return base_price
        
        discount = discounts[item_name]
        percent = discount["percent"]
        
        # Calculate discounted price
        discount_amount = int(base_price * (percent / 100))
        return max(1, base_price - discount_amount)
    
    """Limited Time Offers"""
    LIMITED_OFFERS = {
        "flash_sale": {"duration": 4, "discount": 50, "name": "⚡ Flash Sale"},
        "weekend": {"duration": 48, "discount": 25, "name": "� weekend Deal"},
        "daily": {"duration": 24, "discount": 15, "name": "Daily Deal"}
    }
    
    def start_limited_offer(self, guild_id: int, offer_type: str):
        """Start a limited time offer."""
        if offer_type not in self.LIMITED_OFFERS:
            return
        
        offer = self.LIMITED_OFFERS[offer_type]
        
        # Apply discount to random item
        items = self.get_shop_items(guild_id)
        if not items:
            return
        
        import random
        item_name = random.choice(list(items.keys()))
        
        self.apply_discount(guild_id, item_name, offer["discount"], offer["duration"])
        
        return item_name, offer
    
    """Item Categories"""
    ITEM_CATEGORIES = {
        "roles": {"emoji": "🎭", "description": "Server roles"},
        "colors": {"emoji": "🎨", "description": "Custom colors"},
        "channels": {"emoji": "#️⃣", "description": "Private channels"},
        "banners": {"emoji": "🖼️", "description": "Server banners"},
        "emotes": {"emoji": "😀", "description": "Custom emotes"}
    }
    
    async def show_shop(self, interaction: discord.Interaction, category: str = None):
        guild_id = interaction.guild.id
        items = self.get_shop_items(guild_id)
        discounts = self.get_active_discounts(guild_id)
        
        embed = discord.Embed(title="🛒 Server Shop", color=discord.Color.blue())
        
        for name, data in items.items():
            if category and data.get("category") != category:
                continue
            
            price = data['price']
            currency = "💰 Coins" if data['currency'] == "coins" else "💎 Gems"
            
            # Show stock
            stock = data.get("stock", -1)
            if stock > 0:
                stock_text = f" | Stock: {stock}"
            elif stock == 0:
                stock_text = " | ❌ SOLD OUT"
            else:
                stock_text = ""
            
            # Show discount
            if name in discounts:
                old_price = price
                new_price = self.get_discounted_price(guild_id, name, price)
                price_text = f"~~{old_price}~~ **{new_price}** {currency} 🔥"
            else:
                price_text = f"**{price}** {currency}"
            
            embed.add_field(
                name=f"{name}{stock_text}",
                value=f"Price: {price_text}\nType: {data['type']}",
                inline=True
            )
        
        if discounts:
            embed.set_footer(text="🔥 Limited time offers active!")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def buy_item(self, interaction: discord.Interaction, item_name: str):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        items = self.get_shop_items(guild_id)
        
        if item_name not in items:
            return await interaction.response.send_message("Item not found in shop.", ephemeral=True)
        
        item = items[item_name]
        
        # Check stock
        stock = item.get("stock", -1)
        if stock == 0:
            return await interaction.response.send_message("❌ This item is sold out!", ephemeral=True)
        
        # Calculate price with discount
        base_price = item['price']
        price = self.get_discounted_price(guild_id, item_name, base_price)
        currency = item['currency']
        
        # Deduct payment
        if currency == "coins":
            if self.bot.economy.get_coins(guild_id, user_id) < price:
                return await interaction.response.send_message("Not enough coins!", ephemeral=True)
            self.bot.economy.add_coins(guild_id, user_id, -price)
        else:
            if not self.bot.leveling.spend_gems(guild_id, user_id, price):
                return await interaction.response.send_message("Not enough gems!", ephemeral=True)
        
        # Grant item
        if item['type'] == 'role':
            role = interaction.guild.get_role(item['role_id'])
            if role:
                await interaction.user.add_roles(role)
        
        # Decrease stock
        if stock > 0:
            self.update_stock(guild_id, item_name, -1)
        
        # Log purchase
        purchases = dm.get_guild_data(guild_id, "purchases", [])
        purchases.append({
            "user_id": user_id,
            "item": item_name,
            "price": price,
            "timestamp": str(datetime.datetime.now())
        })
        dm.update_guild_data(guild_id, "purchases", purchases)
        
        await interaction.response.send_message(f"✅ Purchased **{item_name}** for {price}!", ephemeral=True)
    
    """Admin: Add custom items"""
    async def add_item(self, guild_id: int, name: str, price: int, item_type: str, 
                      currency: str = "coins", stock: int = -1):
        items = self.get_shop_items(guild_id)
        
        items[name] = {
            "price": price,
            "currency": currency,
            "type": item_type,
            "stock": stock,
            "added_at": time.time()
        }
        
        dm.update_guild_data(guild_id, "shop_items", items)
    
    """Admin: Remove item"""
    async def remove_item(self, guild_id: int, name: str):
        items = self.get_shop_items(guild_id)
        
        if name in items:
            del items[name]
            dm.update_guild_data(guild_id, "shop_items", items)
    
    """Admin: Set discount"""
    async def set_discount(self, guild_id: int, item_name: str, percent: int, hours: int = 24):
        self.apply_discount(guild_id, item_name, percent, hours)



# ======================================================================
# From: modules/gamification.py
# ======================================================================

import discord
import asyncio
import json
import time
import random
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from data_manager import dm
from logger import logger
import os


class QuestType(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    PERSONAL = "personal"
    SOCIAL = "social"
    CHALLENGE = "challenge"


class QuestStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CLAIMED = "claimed"


@dataclass
class Quest:
    id: str
    guild_id: int
    user_id: int
    quest_type: QuestType
    title: str
    description: str
    requirements: dict
    rewards: dict
    expires_at: float
    status: QuestStatus
    progress: int
    created_at: float


@dataclass
class Skill:
    name: str
    level: int
    xp: int
    xp_to_next: int


class AdaptiveGamification:
    def __init__(self, bot):
        self.bot = bot
        self._active_quests: Dict[str, Quest] = {}
        self._user_skills: Dict[int, Dict[int, Dict[str, Skill]]] = {}
        self._seasonal_events: Dict[int, dict] = {}
        self._server_challenges: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        """Load quests and events from guild-specific files."""
        count = 0
        data_dir = "data"
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_id_str = filename[6:-5]
                        if not guild_id_str.isdigit(): continue
                        guild_id = int(guild_id_str)
                        guild_data = dm.load_json(filename[:-5], default={})

                        # Load quests
                        quests_data = guild_data.get("quests", {})
                        for quest_id, data in quests_data.items():
                            quest = Quest(
                                id=quest_id,
                                guild_id=guild_id,
                                user_id=data["user_id"],
                                quest_type=QuestType(data["quest_type"]),
                                title=data["title"],
                                description=data["description"],
                                requirements=data["requirements"],
                                rewards=data["rewards"],
                                expires_at=data["expires_at"],
                                status=QuestStatus(data["status"]),
                                progress=data["progress"],
                                created_at=data["created_at"]
                            )
                            if quest.status == QuestStatus.ACTIVE and quest.expires_at > time.time():
                                self._active_quests[quest_id] = quest
                                count += 1

                        # Load seasonal events
                        guild_seasonal = guild_data.get("seasonal_events", {})
                        if guild_seasonal:
                            self._seasonal_events[guild_id] = guild_seasonal

                    except Exception as e:
                        logger.error(f"Failed to load gamification data from {filename}: {e}")
        logger.info(f"Loaded {count} active quests from guild files.")

    def _save_quests(self, quest: Quest):
        """Save a single quest to its guild-specific file."""
        guild_id = quest.guild_id
        quests = dm.get_guild_data(guild_id, "quests", {})
        
        quests[quest.id] = {
            "user_id": quest.user_id,
            "quest_type": quest.quest_type.value,
            "title": quest.title,
            "description": quest.description,
            "requirements": quest.requirements,
            "rewards": quest.rewards,
            "expires_at": quest.expires_at,
            "status": quest.status.value,
            "progress": quest.progress,
            "created_at": quest.created_at
        }
        dm.update_guild_data(guild_id, "quests", quests)


    def start_quest_refresh(self):
        asyncio.create_task(self._quest_refresh_loop())

    async def _quest_refresh_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                await self._refresh_daily_quests()
                await self._refresh_server_challenges()
                await self._check_quest_progress()
                await self._update_ranking_titles()
            except Exception as e:
                logger.error(f"Quest refresh error: {e}")
            
            await asyncio.sleep(60)

    async def _refresh_server_challenges(self):
        for guild in self.bot.guilds:
            challenges = dm.get_guild_data(guild.id, "server_challenges", [])
            if not challenges:
                # Generate new set
                new_challenges = [
                    {"id": "daily_msg", "name": "Chat Fever", "desc": "Send 1000 messages collectively", "target": 1000, "progress": 0, "type": "daily", "reward": {"coins": 500}},
                    {"id": "weekly_voice", "name": "Talkative Community", "desc": "Spend 100 hours in voice collectively", "target": 6000, "progress": 0, "type": "weekly", "reward": {"coins": 2000}}
                ]
                dm.update_guild_data(guild.id, "server_challenges", new_challenges)

    FALLBACK_QUESTS = [
        {"title": "Chatterbox", "description": "Send 10 messages in the server today!",
         "requirements": {"type": "messages", "count": 10}, "rewards": {"coins": 50, "xp": 25}},
        {"title": "Explorer", "description": "Use 3 different commands today.",
         "requirements": {"type": "commands", "count": 3}, "rewards": {"coins": 40, "xp": 20}},
        {"title": "Social Butterfly", "description": "Send 15 messages and earn your daily bonus.",
         "requirements": {"type": "messages", "count": 15}, "rewards": {"coins": 60, "xp": 30}},
        {"title": "Dedicated", "description": "Claim your daily reward and send 5 messages.",
         "requirements": {"type": "messages", "count": 5}, "rewards": {"coins": 45, "xp": 25}},
        {"title": "Community Voice", "description": "Spend 10 minutes in voice chat with the community.",
         "requirements": {"type": "voice", "count": 10}, "rewards": {"coins": 70, "xp": 35}},
        {"title": "Regular", "description": "Send 8 messages today to keep your streak alive.",
         "requirements": {"type": "messages", "count": 8}, "rewards": {"coins": 50, "xp": 25}},
    ]

    async def _refresh_daily_quests(self):
        # Bound concurrency: quest generation hits the AI API, and generating for
        # every member of every guild at once caused 429 storms and CPU spikes.
        sem = asyncio.Semaphore(3)
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                # Only generate for members who have actually interacted with the bot
                if not dm.get_guild_data(guild.id, f"user_{member.id}", {}):
                    continue
                async with sem:
                    await self._generate_daily_quest(guild.id, member.id)
                await asyncio.sleep(1)  # stagger requests to respect provider rate limits

    async def _generate_daily_quest(self, guild_id: int, user_id: int):
        # Skip AI quest generation silently if no API key is configured for this guild
        keys = self.bot.ai._get_all_guild_keys(guild_id)
        if not keys:
            return

        existing_quest_count = sum(
            1 for q in self._active_quests.values()
            if q.guild_id == guild_id and q.user_id == user_id and q.quest_type == QuestType.DAILY and q.status == QuestStatus.ACTIVE
        )
        
        if existing_quest_count >= 3:
            return
        
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        interests = user_data.get("interests", ["general"])
        
        prompt = f"""Generate a daily quest for a Discord user.

User interests: {', '.join(interests)}

Respond with EXACTLY ONE complete, valid JSON object and nothing else - no markdown, no code fences, no explanation. Include every field shown:
{{
    "title": "Quest title",
    "description": "What the player needs to do",
    "type": "daily",
    "requirements": {{"type": "messages", "count": 10}},
    "rewards": {{"coins": 50, "xp": 25}},
    "duration_hours": 24
}}

The requirements.type must be one of: messages, commands, voice.
Make it fun and varied. Consider message sending, reactions, voice chat, command usage, etc."""

        system_prompt = ("You create fun daily quests for Discord users. Keep them achievable (5-20 actions). "
                         "Your entire reply must be a single complete valid JSON object with all fields closed "
                         "- never truncate, never add text outside the JSON.")

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=user_id,
                user_input=prompt,
                system_prompt=system_prompt
            )
            
            if not result or "error" in result:
                logger.warning(f"AI failed to provide quest data: {result.get('error', 'Unknown error') if result else 'empty response'}")
                self._create_fallback_quest(guild_id, user_id)
                return

            # Guard against malformed AI output: requirements/rewards must be dicts
            requirements = result.get("requirements")
            rewards = result.get("rewards")
            if not isinstance(requirements, dict) or not isinstance(rewards, dict):
                logger.warning(f"AI quest for user {user_id} had invalid structure; using fallback")
                self._create_fallback_quest(guild_id, user_id)
                return

            quest_id = f"quest_{guild_id}_{user_id}_{int(time.time())}"
            
            quest = Quest(
                id=quest_id,
                guild_id=guild_id,
                user_id=user_id,
                quest_type=QuestType.DAILY,
                title=str(result.get("title", "Daily Quest"))[:100],
                description=str(result.get("description", "Complete this quest!"))[:500],
                requirements=requirements,
                rewards=rewards,
                expires_at=time.time() + (result.get("duration_hours", 24) * 3600),
                status=QuestStatus.ACTIVE,
                progress=0,
                created_at=time.time()
            )
            
            self._active_quests[quest_id] = quest
            self._save_quests(quest)
            
        except Exception as e:
            error_str = str(e)
            if "No API key" in error_str or "API key" in error_str or "RetryError" in error_str:
                return
            logger.warning(f"Failed to generate daily quest for user {user_id} in guild {guild_id}: {e}")
            self._create_fallback_quest(guild_id, user_id)

    def _create_fallback_quest(self, guild_id: int, user_id: int):
        """Non-AI fallback so users always get a quest even when the AI is down."""
        template = random.choice(self.FALLBACK_QUESTS)
        quest_id = f"quest_{guild_id}_{user_id}_{int(time.time())}"
        quest = Quest(
            id=quest_id,
            guild_id=guild_id,
            user_id=user_id,
            quest_type=QuestType.DAILY,
            title=template["title"],
            description=template["description"],
            requirements=dict(template["requirements"]),
            rewards=dict(template["rewards"]),
            expires_at=time.time() + 24 * 3600,
            status=QuestStatus.ACTIVE,
            progress=0,
            created_at=time.time()
        )
        self._active_quests[quest_id] = quest
        self._save_quests(quest)
        logger.info(f"Created fallback quest '{template['title']}' for user {user_id} in guild {guild_id}")

    async def _check_quest_progress(self):
        current_time = time.time()
        
        for quest_id, quest in list(self._active_quests.items()):
            if quest.status != QuestStatus.ACTIVE:
                continue
            
            if quest.expires_at < current_time:
                quest.status = QuestStatus.EXPIRED
                self._save_quests(quest)
                continue
            
            if quest.quest_type == QuestType.DAILY:
                user_data = dm.get_guild_data(quest.guild_id, f"user_{quest.user_id}", {})
                
                req_type = quest.requirements.get("type")
                req_count = quest.requirements.get("count", 10)
                
                if req_type == "messages":
                    current = user_data.get("messages_sent_today", 0)
                    quest.progress = min(current, req_count)
                elif req_type == "commands":
                    current = user_data.get("commands_used_today", 0)
                    quest.progress = min(current, req_count)
                elif req_type == "voice":
                    current = user_data.get("voice_minutes_today", 0)
                    quest.progress = min(current, req_count)
                
                if quest.progress >= req_count:
                    quest.status = QuestStatus.COMPLETED
                    await self._notify_quest_complete(quest)
                
                self._save_quests(quest)


    async def _update_ranking_titles(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot: continue

                xp = self.bot.leveling.get_xp(guild.id, member.id)
                level = self.bot.leveling.get_level_from_xp(xp)

                title_name = None
                if level >= 100: title_name = "Legend"
                elif level >= 50: title_name = "Elite"
                elif level >= 25: title_name = "Veteran"
                elif level >= 10: title_name = "Regular"
                else: title_name = "Newcomer"

                current_title = dm.get_guild_data(guild.id, f"ranking_title_{member.id}")
                if title_name != current_title:
                    dm.update_guild_data(guild.id, f"ranking_title_{member.id}", title_name)
                    # Optionally assign role
                    role = discord.utils.get(guild.roles, name=title_name)
                    if role:
                        try: await member.add_roles(role)
                        except Exception as e: logger.error(f"Failed to add role {title_name}: {e}")

    async def prestige(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        xp = self.bot.leveling.get_xp(guild_id, user_id)
        level = self.bot.leveling.get_level_from_xp(xp)

        prestige_config = dm.get_guild_data(guild_id, "gamification_config", {}).get("prestige_level", 100)

        if level < prestige_config:
            return await interaction.response.send_message(f"You must reach level {prestige_config} to prestige!", ephemeral=True)

        # Reset XP/Level
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        xp_data[str(user_id)] = 0
        dm.update_guild_data(guild_id, "leveling_xp", xp_data)

        # Increase Prestige Level
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        current_prestige = user_data.get("prestige", 0)
        user_data["prestige"] = current_prestige + 1
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)

        await interaction.response.send_message(f"🔱 **PRESTIGE!** You have reset to level 1 and reached Prestige **{current_prestige + 1}**!", ephemeral=True)

    async def mini_game_dice(self, interaction: discord.Interaction, bet: int):
        if bet <= 0: return await interaction.response.send_message("Bet must be positive!", ephemeral=True)
        coins = self.bot.economy.get_coins(interaction.guild.id, interaction.user.id)
        if coins < bet: return await interaction.response.send_message("Insufficient coins!", ephemeral=True)
        user_roll, bot_roll = random.randint(1, 6), random.randint(1, 6)
        if user_roll > bot_roll:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, bet)
            result = f"You rolled {user_roll}, I rolled {bot_roll}. **You win {bet} coins!**"
        elif bot_roll > user_roll:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, -bet)
            result = f"You rolled {user_roll}, I rolled {bot_roll}. **You lost {bet} coins.**"
        else: result = f"Both rolled {user_roll}. **It's a draw!**"
        await interaction.response.send_message(f"🎲 {result}", ephemeral=True)

    async def mini_game_flip(self, interaction: discord.Interaction, side: str, bet: int):
        if bet <= 0: return await interaction.response.send_message("Bet must be positive!", ephemeral=True)
        coins = self.bot.economy.get_coins(interaction.guild.id, interaction.user.id)
        if coins < bet: return await interaction.response.send_message("Insufficient coins!", ephemeral=True)
        result_side = random.choice(["heads", "tails"])
        if side.lower() == result_side:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, bet)
            res_text = f"It was **{result_side}**! **You win {bet} coins!**"
        else:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, -bet)
            res_text = f"It was **{result_side}**... **You lost {bet} coins.**"
        await interaction.response.send_message(f"🪙 {res_text}", ephemeral=True)

    async def mini_game_slots(self, interaction: discord.Interaction, bet: int):
        if bet <= 0: return await interaction.response.send_message("Bet must be positive!", ephemeral=True)
        coins = self.bot.economy.get_coins(interaction.guild.id, interaction.user.id)
        if coins < bet: return await interaction.response.send_message("Insufficient coins!", ephemeral=True)
        emojis = ["🍒", "🍋", "🍇", "🍊", "🍎", "💎", "7️⃣"]
        res = [random.choice(emojis) for _ in range(3)]
        slot_str = " | ".join(res)
        if res[0] == res[1] == res[2]:
            mult = 10 if res[0] == "7️⃣" else 5
            win = bet * mult
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, win)
            res_text = f"**JACKPOT!** {slot_str}\n**You win {win} coins!**"
        elif res[0] == res[1] or res[1] == res[2] or res[0] == res[2]:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, bet)
            res_text = f"**Match!** {slot_str}\n**You win {bet} coins!**"
        else:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, -bet)
            res_text = f"{slot_str}\n**You lost {bet} coins.**"
        await interaction.response.send_message(f"🎰 {res_text}", ephemeral=True)

    async def mini_game_trivia(self, interaction: discord.Interaction):
        # Sample trivia questions
        questions = [
            {"q": "What is the capital of France?", "a": "Paris"},
            {"q": "Who wrote 'Romeo and Juliet'?", "a": "Shakespeare"},
            {"q": "What is the largest planet in our solar system?", "a": "Jupiter"}
        ]
        q_data = random.choice(questions)
        await interaction.response.send_message(f"❓ **Trivia:** {q_data['q']}\n(Reply with the answer in 15s)", ephemeral=True)
        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=15.0)
            if msg.content.lower() == q_data['a'].lower():
                self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, 50)
                await interaction.channel.send(f"✅ Correct! **+50 coins**")
            else:
                await interaction.channel.send(f"❌ Wrong! The answer was **{q_data['a']}**.")
        except asyncio.TimeoutError:
            await interaction.channel.send(f"⏰ Time's up! The answer was **{q_data['a']}**.")

    async def _notify_quest_complete(self, quest: Quest):
        member = self.bot.get_guild(quest.guild_id).get_member(quest.user_id)
        if not member:
            return
        
        embed = discord.Embed(
            title="✅ Quest Completed!",
            description=f"**{quest.title}** - {quest.description}",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Rewards",
            value=f"💰 {quest.rewards.get('coins', 0)} coins, ✨ {quest.rewards.get('xp', 0)} XP",
            inline=False
        )
        
        try:
            await member.send(embed=embed)
        except:
            pass

    async def claim_quest_reward(self, guild_id: int, user_id: int, quest_id: str) -> bool:
        if quest_id not in self._active_quests:
            return False
        
        quest = self._active_quests[quest_id]
        
        if quest.user_id != user_id or quest.guild_id != guild_id:
            return False
        
        if quest.status != QuestStatus.COMPLETED:
            return False
        
        self.bot.economy.add_coins(guild_id, user_id, quest.rewards.get("coins", 0))
        self.bot.leveling.add_xp(guild_id, user_id, quest.rewards.get("xp", 0))

        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        user_data["quests_completed"] = user_data.get("quests_completed", 0) + 1
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)
        
        quest.status = QuestStatus.CLAIMED
        self._save_quests(quest)
        
        return True

    def get_user_quests(self, guild_id: int, user_id: int) -> List[dict]:
        user_quests = []
        for quest in self._active_quests.values():
            if quest.guild_id == guild_id and quest.user_id == user_id and quest.status in [QuestStatus.ACTIVE, QuestStatus.COMPLETED]:
                user_quests.append({
                    "id": quest.id, "title": quest.title, "description": quest.description,
                    "type": quest.quest_type.value, "progress": quest.progress,
                    "requirements": quest.requirements, "rewards": quest.rewards,
                    "status": quest.status.value, "expires_at": quest.expires_at
                })
        return user_quests

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["quests"] = json.dumps({"command_type": "list_quests"})
        custom_cmds["quest"] = json.dumps({"command_type": "list_quests"})
        custom_cmds["prestige"] = json.dumps({"command_type": "prestige"})
        custom_cmds["dice"] = json.dumps({"command_type": "dice"})
        custom_cmds["flip"] = json.dumps({"command_type": "flip"})
        custom_cmds["slots"] = json.dumps({"command_type": "slots"})
        custom_cmds["trivia"] = json.dumps({"command_type": "trivia"})

        custom_cmds["gamificationpanel"] = "configpanel gamification"

        custom_cmds["help gamification"] = json.dumps({
            "command_type": "help_embed",
            "title": "Gamification System Help",
            "description": "Earn rewards through games and challenges.",
            "fields": [
                {"name": "!quests", "value": "List available quests.", "inline": False},
                {"name": "!prestige", "value": "Prestige system.", "inline": False},
                {"name": "!dice", "value": "Dice game.", "inline": False},
                {"name": "!flip", "value": "Coin flip game.", "inline": False},
                {"name": "!slots", "value": "Slot machine game.", "inline": False},
                {"name": "!trivia", "value": "Trivia game.", "inline": False},
                {"name": "!help gamification", "value": "Show this help message.", "inline": False}
            ]
        })

        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("Gamification system set up! Try `!quests`, `!dice`, or `!flip <bet>`.", ephemeral=True)
        return True


Gamification = AdaptiveGamification



# ======================================================================
# From: modules/tournaments.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from data_manager import dm
from logger import logger


class TournamentStatus(Enum):
    SETUP = "setup"
    REGISTRATION = "registration"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TournamentType(Enum):
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"
    ROUND_ROBIN = "round_robin"
    FREE_FOR_ALL = "free_for_all"
    TEAM_VS_TEAM = "team_vs_team"


@dataclass
class Tournament:
    id: str
    guild_id: int
    name: str
    description: str
    tournament_type: TournamentType
    status: TournamentStatus
    max_participants: int
    min_participants: int
    prize_pool: dict
    registration_end: float
    start_time: float
    rounds: List[dict]
    participants: List[int]
    teams: Dict[str, List[int]]
    bracket: List[dict]
    winner: Optional[int]
    created_by: int
    created_at: float
    channel_id: Optional[int]


@dataclass
class Match:
    id: str
    tournament_id: str
    round: int
    match_number: int
    player1: Optional[int]
    player2: Optional[int]
    player1_score: int
    player2_score: int
    winner: Optional[int]
    status: str
    next_match: Optional[str]


class TournamentSystem:
    def __init__(self, bot):
        self.bot = bot
        self._tournaments: Dict[str, Tournament] = {}
        self._active_matches: Dict[str, Match] = {}
        self._load_tournaments()

    def _load_tournaments(self):
        data = dm.load_json("tournaments", default={})
        
        for tourney_id, t_data in data.items():
            try:
                tournament = Tournament(
                    id=tourney_id,
                    guild_id=t_data["guild_id"],
                    name=t_data["name"],
                    description=t_data["description"],
                    tournament_type=TournamentType(t_data["tournament_type"]),
                    status=TournamentStatus(t_data["status"]),
                    max_participants=t_data["max_participants"],
                    min_participants=t_data["min_participants"],
                    prize_pool=t_data["prize_pool"],
                    registration_end=t_data["registration_end"],
                    start_time=t_data["start_time"],
                    rounds=t_data.get("rounds", []),
                    participants=t_data.get("participants", []),
                    teams=t_data.get("teams", {}),
                    bracket=t_data.get("bracket", []),
                    winner=t_data.get("winner"),
                    created_by=t_data["created_by"],
                    created_at=t_data["created_at"],
                    channel_id=t_data.get("channel_id")
                )
                self._tournaments[tourney_id] = tournament
            except Exception as e:
                logger.error(f"Failed to load tournament {tourney_id}: {e}")

    def _save_tournament(self, tournament: Tournament):
        data = dm.load_json("tournaments", default={})
        data[tournament.id] = {
            "guild_id": tournament.guild_id,
            "name": tournament.name,
            "description": tournament.description,
            "tournament_type": tournament.tournament_type.value,
            "status": tournament.status.value,
            "max_participants": tournament.max_participants,
            "min_participants": tournament.min_participants,
            "prize_pool": tournament.prize_pool,
            "registration_end": tournament.registration_end,
            "start_time": tournament.start_time,
            "rounds": tournament.rounds,
            "participants": tournament.participants,
            "teams": tournament.teams,
            "bracket": tournament.bracket,
            "winner": tournament.winner,
            "created_by": tournament.created_by,
            "created_at": tournament.created_at,
            "channel_id": tournament.channel_id
        }
        dm.save_json("tournaments", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "tournament_settings", {
            "enabled": True,
            "default_max": 32,
            "default_min": 4,
            "default_prize": {"coins": 500, "xp": 250}
        })


    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "tournament_settings", settings)
        
        help_embed = discord.Embed(
            title="🏆 Tournament System",
            description="Create competitive tournaments with brackets, prizes, and seasons.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Create tournaments with auto-generated brackets. Prize pool comes from economy. Supports single elimination, round robin, and team vs team.",
            inline=False
        )
        help_embed.add_field(
            name="!tournaments",
            value="List active tournaments.",
            inline=False
        )
        help_embed.add_field(
            name="!join <tournament>",
            value="Join a tournament.",
            inline=False
        )
        help_embed.add_field(
            name="!tournamentleaderboard",
            value="View tournament winners leaderboard.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["tournaments"] = json.dumps({
            "command_type": "list_tournaments"
        })
        custom_cmds["join"] = json.dumps({
            "command_type": "join_tournament"
        })
        custom_cmds["tournamentleaderboard"] = json.dumps({
            "command_type": "tournament_leaderboard"
        })
        custom_cmds["tournament create"] = json.dumps({
            "command_type": "create_tournament"
        })
        custom_cmds["help tournaments"] = json.dumps({
            "command_type": "help_embed",
            "title": "🏆 Tournament System",
            "description": "Create competitive tournaments.",
            "fields": [
                {"name": "!tournaments", "value": "List active tournaments.", "inline": False},
                {"name": "!join <tournament>", "value": "Join a tournament.", "inline": False},
                {"name": "!tournamentleaderboard", "value": "View leaderboard.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
