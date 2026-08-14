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

        shop_items = dm.get_guild_data(interaction.guild.id, "shop_items", [])

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

        shop_items = dm.get_guild_data(interaction.guild.id, "shop_items", [])

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