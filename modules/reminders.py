import discord
from discord import ui
import time
import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class ReminderSystem:
    """
    Complete reminder system with scheduling and notifications.
    Features:
    - Create personal reminders
    - Scheduled announcements
    - Recurring reminders
    - Reminder management
    """

    def __init__(self, bot):
        self.bot = bot
        self.active_reminders = {}  # user_id -> [reminder_data]

    async def create_reminder(self, interaction, message: str, delay_seconds: int, recurring: bool = False):
        """Create a new reminder."""
        config = dm.get_guild_data(interaction.guild.id, "reminders_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Reminders system is disabled.", ephemeral=True)

        if delay_seconds < 60 or delay_seconds > 2592000:  # 1 minute to 30 days
            return await interaction.response.send_message("❌ Delay must be between 1 minute and 30 days.", ephemeral=True)

        reminder_time = time.time() + delay_seconds

        reminder_data = {
            "id": int(time.time()),
            "user_id": interaction.user.id,
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "message": message,
            "reminder_time": reminder_time,
            "recurring": recurring,
            "recurring_interval": delay_seconds if recurring else None,
            "created_at": time.time()
        }

        # Save reminder
        reminders = dm.get_guild_data(interaction.guild.id, "scheduled_reminders", [])
        reminders.append(reminder_data)
        dm.update_guild_data(interaction.guild.id, "scheduled_reminders", reminders)

        # Schedule execution
        from task_scheduler import task_scheduler
        await task_scheduler.schedule_task(reminder_time, self.send_reminder, reminder_data)

        # Add to active reminders
        if interaction.user.id not in self.active_reminders:
            self.active_reminders[interaction.user.id] = []
        self.active_reminders[interaction.user.id].append(reminder_data)

        await interaction.response.send_message(
            f"✅ Reminder set for {self.format_time(delay_seconds)} from now!",
            ephemeral=True
        )

    async def send_reminder(self, reminder_data: dict):
        """Send a reminder notification."""
        try:
            channel = self.bot.get_channel(reminder_data["channel_id"])
            if not channel:
                return

            user = self.bot.get_user(reminder_data["user_id"])
            if not user:
                return

            embed = discord.Embed(
                title="⏰ Reminder!",
                description=reminder_data["message"],
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Set by {user.display_name}")

            await channel.send(f"{user.mention}", embed=embed)

            # Handle recurring reminders
            if reminder_data.get("recurring"):
                # Reschedule for next occurrence
                next_time = time.time() + reminder_data["recurring_interval"]
                reminder_data["reminder_time"] = next_time

                # Update in storage
                reminders = dm.get_guild_data(reminder_data["guild_id"], "scheduled_reminders", [])
                for i, r in enumerate(reminders):
                    if r["id"] == reminder_data["id"]:
                        reminders[i] = reminder_data
                        break
                dm.update_guild_data(reminder_data["guild_id"], "scheduled_reminders", reminders)

                # Reschedule
                from task_scheduler import task_scheduler
                await task_scheduler.schedule_task(next_time, self.send_reminder, reminder_data)

            else:
                # Remove one-time reminder
                reminders = dm.get_guild_data(reminder_data["guild_id"], "scheduled_reminders", [])
                reminders = [r for r in reminders if r["id"] != reminder_data["id"]]
                dm.update_guild_data(reminder_data["guild_id"], "scheduled_reminders", reminders)

                # Remove from active reminders
                user_id = reminder_data["user_id"]
                if user_id in self.active_reminders:
                    self.active_reminders[user_id] = [
                        r for r in self.active_reminders[user_id] if r["id"] != reminder_data["id"]
                    ]

        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")

    def format_time(self, seconds: int) -> str:
        """Format seconds into human readable time."""
        if seconds < 3600:
            return f"{seconds // 60}m"
        elif seconds < 86400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            return f"{days}d {hours}h"

    async def list_reminders(self, interaction):
        """List user's active reminders."""
        user_reminders = self.active_reminders.get(interaction.user.id, [])

        if not user_reminders:
            return await interaction.response.send_message("📝 You have no active reminders.", ephemeral=True)

        embed = discord.Embed(
            title="⏰ Your Reminders",
            color=discord.Color.blue()
        )

        for reminder in user_reminders[:10]:  # Show first 10
            remaining = int(reminder["reminder_time"] - time.time())
            time_str = self.format_time(remaining) if remaining > 0 else "Overdue"

            embed.add_field(
                name=f"ID: {reminder['id']}",
                value=f"⏰ {time_str}\n💬 {reminder['message'][:50]}{'...' if len(reminder['message']) > 50 else ''}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def delete_reminder(self, interaction, reminder_id: int):
        """Delete a reminder."""
        user_reminders = self.active_reminders.get(interaction.user.id, [])
        reminder = next((r for r in user_reminders if r["id"] == reminder_id), None)

        if not reminder:
            return await interaction.response.send_message("❌ Reminder not found.", ephemeral=True)

        # Remove from active reminders
        self.active_reminders[interaction.user.id] = [
            r for r in self.active_reminders[interaction.user.id] if r["id"] != reminder_id
        ]

        # Remove from storage
        reminders = dm.get_guild_data(interaction.guild.id, "scheduled_reminders", [])
        reminders = [r for r in reminders if r["id"] != reminder_id]
        dm.update_guild_data(interaction.guild.id, "scheduled_reminders", reminders)

        # Cancel scheduled task
        from task_scheduler import task_scheduler
        task_scheduler.cancel_task(reminder_id)

        await interaction.response.send_message("✅ Reminder deleted!", ephemeral=True)

    async def start_monitoring(self):
        """Load active reminders on startup."""
        for guild in self.bot.guilds:
            reminders = dm.get_guild_data(guild.id, "scheduled_reminders", [])
            current_time = time.time()

            for reminder in reminders:
                if reminder["reminder_time"] > current_time:
                    user_id = reminder["user_id"]
                    if user_id not in self.active_reminders:
                        self.active_reminders[user_id] = []
                    self.active_reminders[user_id].append(reminder)

                    # Reschedule
                    from task_scheduler import task_scheduler
                    await task_scheduler.schedule_task(reminder["reminder_time"], self.send_reminder, reminder)

    # Config panel
    def get_config_panel(self, guild_id: int):
        return RemindersConfigPanel(self.bot, guild_id)

class RemindersConfigPanel(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.reminders = ReminderSystem(bot)

    @discord.ui.button(label="Toggle Reminders", style=discord.ButtonStyle.primary, row=0)
    async def toggle_reminders(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "reminders_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "reminders_config", config)
        await interaction.response.send_message(f"✅ Reminders {'enabled' if not enabled else 'disabled'}", ephemeral=True)