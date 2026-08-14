import discord
from discord import ui
import time
import random
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class GiveawaySystem:
    """
    Complete giveaway system with entry, auto-end, reroll, and winner selection.
    Features:
    - Giveaway creation with duration
    - Entry via button
    - Automatic winner selection
    - Reroll functionality
    - Giveaway logging
    """

    def __init__(self, bot):
        self.bot = bot
        self.active_giveaways = {}  # message_id -> giveaway_data

    async def create_giveaway(self, interaction, prize: str, duration: int, winners: int = 1):
        """Create a new giveaway."""
        config = dm.get_guild_data(interaction.guild.id, "giveaways_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Giveaways system is disabled.", ephemeral=True)

        if duration < 60 or duration > 604800:  # 1 minute to 1 week
            return await interaction.response.send_message("❌ Duration must be between 1 minute and 1 week.", ephemeral=True)

        end_time = time.time() + duration

        embed = discord.Embed(
            title="🎉 Giveaway!",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(end_time)}:R>",
            color=discord.Color.gold()
        )
        embed.add_field(name="Entries", value="0", inline=True)
        embed.set_footer(text="Click the button below to enter!")

        view = GiveawayEntryView(self, interaction.user.id)
        message = await interaction.channel.send(embed=embed, view=view)

        # Store giveaway data
        giveaway_data = {
            "message_id": message.id,
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild.id,
            "host_id": interaction.user.id,
            "prize": prize,
            "winners": winners,
            "end_time": end_time,
            "entries": [],
            "status": "active"
        }

        self.active_giveaways[message.id] = giveaway_data

        # Save to persistent storage
        active_giveaways = dm.get_guild_data(interaction.guild.id, "active_giveaways", [])
        active_giveaways.append(giveaway_data)
        dm.update_guild_data(interaction.guild.id, "active_giveaways", active_giveaways)

        # Schedule end
        from task_scheduler import task_scheduler
        await task_scheduler.schedule_task(end_time, self.end_giveaway, giveaway_data)

        await interaction.response.send_message("✅ Giveaway created!", ephemeral=True)

    async def enter_giveaway(self, interaction, message_id: int):
        """Enter a user into a giveaway."""
        if message_id not in self.active_giveaways:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)

        giveaway = self.active_giveaways[message_id]
        user_id = interaction.user.id

        if user_id in giveaway["entries"]:
            return await interaction.response.send_message("❌ You have already entered this giveaway.", ephemeral=True)

        giveaway["entries"].append(user_id)

        # Update embed
        try:
            channel = interaction.guild.get_channel(giveaway["channel_id"])
            message = await channel.fetch_message(message_id)
            embed = message.embeds[0]
            embed.set_field_at(0, name="Entries", value=str(len(giveaway["entries"])), inline=True)
            await message.edit(embed=embed)
        except:
            pass

        # Update storage
        active_giveaways = dm.get_guild_data(interaction.guild.id, "active_giveaways", [])
        for i, g in enumerate(active_giveaways):
            if g["message_id"] == message_id:
                active_giveaways[i] = giveaway
                break
        dm.update_guild_data(interaction.guild.id, "active_giveaways", active_giveaways)

        await interaction.response.send_message("✅ Entered giveaway!", ephemeral=True)

    async def end_giveaway(self, giveaway_data: dict):
        """End a giveaway and select winners."""
        try:
            channel = self.bot.get_channel(giveaway_data["channel_id"])
            if not channel:
                return

            message = await channel.fetch_message(giveaway_data["message_id"])
            embed = message.embeds[0]

            entries = giveaway_data["entries"]
            winners_count = giveaway_data["winners"]

            if len(entries) == 0:
                embed.description += "\n\n❌ No valid entries. Giveaway cancelled."
                embed.color = discord.Color.red()
                await message.edit(embed=embed, view=None)
                return

            # Select winners
            winners = []
            available_entries = entries.copy()

            for _ in range(min(winners_count, len(available_entries))):
                winner = random.choice(available_entries)
                winners.append(winner)
                available_entries.remove(winner)

            # Update embed
            if len(winners) == 1:
                winner_mentions = f"<@{winners[0]}>"
            else:
                winner_mentions = ", ".join(f"<@{w}>" for w in winners)

            embed.description += f"\n\n🎉 **Winner(s):** {winner_mentions}"
            embed.color = discord.Color.green()
            embed.set_footer(text="Giveaway ended")

            await message.edit(embed=embed, view=None)

            # Announce winners
            winner_text = "Congratulations!" if len(winners) == 1 else "Congratulations to all winners!"
            await channel.send(f"🎉 **Giveaway Ended!**\n{winner_text} {winner_mentions} won **{giveaway_data['prize']}**!")

            # Save to ended giveaways
            giveaway_data["winners"] = winners
            giveaway_data["ended_at"] = time.time()
            giveaway_data["status"] = "ended"

            ended_giveaways = dm.get_guild_data(giveaway_data["guild_id"], "ended_giveaways", [])
            ended_giveaways.append(giveaway_data)
            dm.update_guild_data(giveaway_data["guild_id"], "ended_giveaways", ended_giveaways[-50:])  # Keep last 50

        except Exception as e:
            logger.error(f"Failed to end giveaway: {e}")

        finally:
            # Clean up
            if giveaway_data["message_id"] in self.active_giveaways:
                del self.active_giveaways[giveaway_data["message_id"]]

            # Remove from active storage
            active_giveaways = dm.get_guild_data(giveaway_data["guild_id"], "active_giveaways", [])
            active_giveaways = [g for g in active_giveaways if g["message_id"] != giveaway_data["message_id"]]
            dm.update_guild_data(giveaway_data["guild_id"], "active_giveaways", active_giveaways)

    async def reroll_giveaway(self, interaction, message_id: int):
        """Reroll a giveaway winner."""
        ended_giveaways = dm.get_guild_data(interaction.guild.id, "ended_giveaways", [])
        giveaway = next((g for g in ended_giveaways if g["message_id"] == message_id), None)

        if not giveaway:
            return await interaction.response.send_message("❌ Ended giveaway not found.", ephemeral=True)

        # Check permissions (host or admin)
        if (interaction.user.id != giveaway["host_id"] and
            not interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("❌ Only the giveaway host or admins can reroll.", ephemeral=True)

        entries = giveaway["entries"]
        if len(entries) == 0:
            return await interaction.response.send_message("❌ No entries to reroll from.", ephemeral=True)

        # Select new winner
        new_winner = random.choice(entries)

        # Update giveaway data
        giveaway["winners"] = [new_winner]
        giveaway["rerolled_at"] = time.time()
        giveaway["rerolled_by"] = interaction.user.id

        # Update in storage
        for i, g in enumerate(ended_giveaways):
            if g["message_id"] == message_id:
                ended_giveaways[i] = giveaway
                break
        dm.update_guild_data(interaction.guild.id, "ended_giveaways", ended_giveaways)

        # Update message
        try:
            channel = interaction.guild.get_channel(giveaway["channel_id"])
            message = await channel.fetch_message(message_id)
            embed = message.embeds[0]

            embed.description = embed.description.split("\n\n🎉")[0]  # Remove old winner
            embed.description += f"\n\n🎉 **Rerolled Winner:** <@{new_winner}>"

            await message.edit(embed=embed)
            await channel.send(f"🔄 Giveaway rerolled! New winner: <@{new_winner}> won **{giveaway['prize']}**!")

        except Exception as e:
            logger.error(f"Failed to reroll giveaway: {e}")

        await interaction.response.send_message("✅ Giveaway rerolled!", ephemeral=True)

    async def start_monitoring(self):
        """Load active giveaways on startup."""
        for guild in self.bot.guilds:
            active_giveaways = dm.get_guild_data(guild.id, "active_giveaways", [])
            current_time = time.time()

            for giveaway in active_giveaways:
                if giveaway["end_time"] > current_time:
                    self.active_giveaways[giveaway["message_id"]] = giveaway

                    # Reschedule end task
                    from task_scheduler import task_scheduler
                    await task_scheduler.schedule_task(giveaway["end_time"], self.end_giveaway, giveaway)

    # Config panel
    def get_config_panel(self, guild_id: int):
        return GiveawaysConfigPanel(self.bot, guild_id)

    def get_persistent_views(self):
        return [GiveawayEntryView(self, 0)]

class GiveawayEntryView(discord.ui.View):
    def __init__(self, giveaway_system, host_id: int):
        super().__init__(timeout=None)
        self.giveaway_system = giveaway_system
        self.host_id = host_id

    @discord.ui.button(emoji="🎉", style=discord.ButtonStyle.primary, custom_id="giveaway_enter")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.giveaway_system.enter_giveaway(interaction, interaction.message.id)

    @discord.ui.button(label="End Early", style=discord.ButtonStyle.danger, custom_id="giveaway_end_early")
    async def end_early(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only the host or admins can end giveaways early.", ephemeral=True)

        giveaway = self.giveaway_system.active_giveaways.get(interaction.message.id)
        if not giveaway:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)

        await interaction.response.defer()
        await self.giveaway_system.end_giveaway(giveaway)

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary, custom_id="giveaway_reroll")
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.giveaway_system.reroll_giveaway(interaction, interaction.message.id)

class GiveawaysConfigPanel(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.giveaways = GiveawaySystem(bot)

    @discord.ui.button(label="Toggle Giveaways", style=discord.ButtonStyle.primary, row=0)
    async def toggle_giveaways(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "giveaways_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "giveaways_config", config)
        await interaction.response.send_message(f"✅ Giveaways {'enabled' if not enabled else 'disabled'}", ephemeral=True)