"""Communications systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: announcements.py, reminders.py, modmail.py, auto_announcer.py, auto_publisher.py, events.py
"""



# ======================================================================
# From: modules/announcements.py
# ======================================================================

import discord
from discord import ui
import time
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class AnnouncementSystem:
    """
    Complete announcement system with scheduling and cross-posting.
    Features:
    - Scheduled announcements
    - Cross-posting to announcement channels
    - Announcement approval workflow
    - Auto-pinning options
    """

    def __init__(self, bot):
        self.bot = bot

    async def create_announcement(self, interaction, title: str, content: str, channel_id: int = None, auto_pin: bool = False, cross_post: bool = False):
        """Create and send an announcement."""
        config = dm.get_guild_data(interaction.guild.id, "announcements_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Announcements system is disabled.", ephemeral=True)

        # Get target channel
        target_channel = None
        if channel_id:
            target_channel = interaction.guild.get_channel(channel_id)
        else:
            # Use configured announcement channel
            announce_channel_id = config.get("announcement_channel")
            if announce_channel_id:
                target_channel = interaction.guild.get_channel(int(announce_channel_id))

        if not target_channel:
            return await interaction.response.send_message("❌ No announcement channel configured.", ephemeral=True)

        # Check permissions
        if not interaction.user.guild_permissions.administrator:
            approval_required = config.get("require_approval", True)
            if approval_required:
                # Send for approval instead
                await self.submit_for_approval(interaction, title, content, target_channel.id, auto_pin, cross_post)
                return

        # Send announcement directly
        await self.send_announcement(interaction, title, content, target_channel, auto_pin, cross_post)

    async def submit_for_approval(self, interaction, title: str, content: str, channel_id: int, auto_pin: bool, cross_post: bool):
        """Submit announcement for staff approval."""
        config = dm.get_guild_data(interaction.guild.id, "announcements_config", {})

        # Get approval channel
        approval_channel_id = config.get("approval_channel")
        if not approval_channel_id:
            return await interaction.response.send_message("❌ No approval channel configured.", ephemeral=True)

        approval_channel = interaction.guild.get_channel(int(approval_channel_id))
        if not approval_channel:
            return await interaction.response.send_message("❌ Approval channel not found.", ephemeral=True)

        # Create approval embed
        embed = discord.Embed(
            title="📢 Announcement Awaiting Approval",
            description=f"**Title:** {title}\n**Content:** {content[:1000]}{'...' if len(content) > 1000 else ''}",
            color=discord.Color.orange()
        )
        embed.add_field(name="Submitted by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Target Channel", value=f"<#{channel_id}>", inline=True)
        embed.add_field(name="Options", value=f"Auto-pin: {auto_pin}, Cross-post: {cross_post}", inline=False)

        # Store pending announcement
        pending_id = int(time.time())
        pending_data = {
            "id": pending_id,
            "title": title,
            "content": content,
            "channel_id": channel_id,
            "auto_pin": auto_pin,
            "cross_post": cross_post,
            "submitted_by": interaction.user.id,
            "submitted_at": time.time()
        }

        pending_announcements = dm.get_guild_data(interaction.guild.id, "pending_announcements", [])
        pending_announcements.append(pending_data)
        dm.update_guild_data(interaction.guild.id, "pending_announcements", pending_announcements)

        # Send approval request
        view = AnnouncementApprovalView(self, pending_id)
        await approval_channel.send(embed=embed, view=view)

        await interaction.response.send_message("✅ Announcement submitted for approval!", ephemeral=True)

    async def approve_announcement(self, interaction, announcement_id: int):
        """Approve a pending announcement."""
        config = dm.get_guild_data(interaction.guild.id, "announcements_config", {})

        # Check staff permissions
        is_staff = (interaction.user.guild_permissions.administrator or
                   any(role.id == int(rid) for rid in config.get("staff_roles", []) for role in interaction.user.roles))

        if not is_staff:
            return await interaction.response.send_message("❌ Only staff can approve announcements.", ephemeral=True)

        # Find pending announcement
        pending_announcements = dm.get_guild_data(interaction.guild.id, "pending_announcements", [])
        announcement = next((a for a in pending_announcements if a["id"] == announcement_id), None)

        if not announcement:
            return await interaction.response.send_message("❌ Announcement not found.", ephemeral=True)

        # Send the announcement
        target_channel = interaction.guild.get_channel(announcement["channel_id"])
        if not target_channel:
            return await interaction.response.send_message("❌ Target channel not found.", ephemeral=True)

        embed = discord.Embed(
            title=f"📢 {announcement['title']}",
            description=announcement["content"],
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Approved by {interaction.user.display_name}")

        message = await target_channel.send(embed=embed)

        # Auto-pin if requested
        if announcement.get("auto_pin"):
            try:
                await message.pin()
            except:
                pass

        # Cross-post if requested and it's an announcement channel
        if announcement.get("cross_post") and target_channel.type == discord.ChannelType.news:
            try:
                await message.publish()
            except:
                pass

        # Remove from pending
        pending_announcements = [a for a in pending_announcements if a["id"] != announcement_id]
        dm.update_guild_data(interaction.guild.id, "pending_announcements", pending_announcements)

        # Log approval
        logger.info(f"Announcement approved by {interaction.user.id} in guild {interaction.guild.id}")

        await interaction.response.send_message("✅ Announcement approved and sent!", ephemeral=True)

    async def deny_announcement(self, interaction, announcement_id: int, reason: str = None):
        """Deny a pending announcement."""
        config = dm.get_guild_data(interaction.guild.id, "announcements_config", {})

        # Check staff permissions
        is_staff = (interaction.user.guild_permissions.administrator or
                   any(role.id == int(rid) for rid in config.get("staff_roles", []) for role in interaction.user.roles))

        if not is_staff:
            return await interaction.response.send_message("❌ Only staff can deny announcements.", ephemeral=True)

        # Find and remove pending announcement
        pending_announcements = dm.get_guild_data(interaction.guild.id, "pending_announcements", [])
        announcement = next((a for a in pending_announcements if a["id"] == announcement_id), None)

        if not announcement:
            return await interaction.response.send_message("❌ Announcement not found.", ephemeral=True)

        pending_announcements = [a for a in pending_announcements if a["id"] != announcement_id]
        dm.update_guild_data(interaction.guild.id, "pending_announcements", pending_announcements)

        # Notify submitter
        submitter = interaction.guild.get_member(announcement["submitted_by"])
        if submitter:
            try:
                embed = discord.Embed(
                    title="❌ Announcement Denied",
                    description=f"Your announcement **{announcement['title']}** was denied.",
                    color=discord.Color.red()
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                await submitter.send(embed=embed)
            except:
                pass

        await interaction.response.send_message("✅ Announcement denied.", ephemeral=True)

    async def send_announcement(self, interaction, title: str, content: str, channel, auto_pin: bool, cross_post: bool):
        """Send an announcement directly."""
        embed = discord.Embed(
            title=f"📢 {title}",
            description=content,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Posted by {interaction.user.display_name}")

        message = await channel.send(embed=embed)

        # Auto-pin
        if auto_pin:
            try:
                await message.pin()
            except:
                pass

        # Cross-post
        if cross_post and channel.type == discord.ChannelType.news:
            try:
                await message.publish()
            except:
                pass

        await interaction.response.send_message("✅ Announcement sent!", ephemeral=True)

    async def start_monitoring(self):
        """Start monitoring for scheduled announcements."""
        # Load any scheduled announcements
        pass

    def get_persistent_views(self):
        """Rebuild approval views for every pending announcement (used at bot startup)."""
        import os
        import glob
        import json
        views = []
        try:
            for path in glob.glob(os.path.join("data", "guild_*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                pending = data.get("pending_announcements", [])
                for announcement in pending:
                    try:
                        views.append(AnnouncementApprovalView(self, announcement["id"]))
                    except Exception as e:
                        logger.error(f"Failed to rebuild announcement view: {e}")
        except Exception as e:
            logger.error(f"Announcement persistent views rebuild failed: {e}")
        return views

class AnnouncementApprovalView(discord.ui.View):
    def __init__(self, announcement_system, announcement_id: int):
        super().__init__(timeout=None)
        self.announcement_system = announcement_system
        self.announcement_id = announcement_id

        approve = discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success, emoji="✅",
            custom_id=f"ann_approve_{announcement_id}"
        )
        approve.callback = self._approve_callback
        self.add_item(approve)

        deny = discord.ui.Button(
            label="Deny", style=discord.ButtonStyle.danger, emoji="❌",
            custom_id=f"ann_deny_{announcement_id}"
        )
        deny.callback = self._deny_callback
        self.add_item(deny)

    async def _approve_callback(self, interaction: discord.Interaction):
        system = self.announcement_system or getattr(interaction.client, "announcements", None)
        if system:
            await system.approve_announcement(interaction, self.announcement_id)

    async def _deny_callback(self, interaction: discord.Interaction):
        system = self.announcement_system or getattr(interaction.client, "announcements", None)
        if system:
            modal = DenyReasonModal(system, self.announcement_id)
            await interaction.response.send_modal(modal)

class DenyReasonModal(discord.ui.Modal, title="Deny Announcement"):
    reason = discord.ui.TextInput(label="Reason (optional)", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, announcement_system, announcement_id):
        super().__init__()
        self.announcement_system = announcement_system
        self.announcement_id = announcement_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.announcement_system.deny_announcement(
            interaction,
            self.announcement_id,
            self.reason.value.strip() if self.reason.value else None
        )



# ======================================================================
# From: modules/reminders.py
# ======================================================================

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
        task_scheduler.schedule_task(reminder_time, self.send_reminder, reminder_data)

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
                task_scheduler.schedule_task(next_time, self.send_reminder, reminder_data)

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
                    task_scheduler.schedule_task(reminder["reminder_time"], self.send_reminder, reminder)

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



# ======================================================================
# From: modules/modmail.py
# ======================================================================

import discord
from discord import ui, Interaction, TextStyle, Embed, ButtonStyle
from data_manager import dm
import datetime
import time
import json
from typing import List, Dict, Optional, Any
from logger import logger

class ModmailSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_persistent_views(self):
        return [ModmailThreadView()]

    async def handle_dm(self, message: discord.Message):
        """Handle incoming DMs for modmail."""
        user = message.author

        # Find guilds where modmail is enabled and user is a member
        shared_guilds = [g for g in self.bot.guilds if g.get_member(user.id)]

        enabled_guilds = []
        for guild in shared_guilds:
            config = dm.get_guild_data(guild.id, "modmail_config", {})
            if config.get("enabled", False):
                # Check if blocked
                blocked_users = dm.get_guild_data(guild.id, "modmail_blocked", [])
                if user.id in blocked_users:
                    continue
                enabled_guilds.append(guild)

        if not enabled_guilds:
            return # Modmail not enabled or user blocked in all shared servers

        if len(enabled_guilds) > 1:
            # Let user choose server
            view = ui.View(timeout=60)
            select = ui.Select(placeholder="Select Server to Contact Staff")
            for guild in enabled_guilds[:25]:
                select.add_option(label=guild.name, value=str(guild.id))

            async def select_callback(it: Interaction):
                guild_id = int(select.values[0])
                guild = self.bot.get_guild(guild_id)
                await it.response.send_message(f"Forwarding your message to **{guild.name}** staff...", ephemeral=True)
                await self._process_modmail(message, guild)

            select.callback = select_callback
            view.add_item(select)
            await user.send("Which server would you like to contact staff for?", view=view)
        else:
            await self._process_modmail(message, enabled_guilds[0])

    async def _process_modmail(self, message: discord.Message, guild: discord.Guild):
        user = message.author
        config = dm.get_guild_data(guild.id, "modmail_config", {})
        log_channel_id = config.get("log_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

        if not log_channel:
            return await user.send(f"❌ Modmail is currently unavailable for **{guild.name}**.")

        # Check for existing thread
        threads = dm.get_guild_data(guild.id, "modmail_threads", {})
        thread_data = threads.get(str(user.id))

        channel_to_send = None
        is_new = False

        if thread_data and thread_data.get("status") == "open":
            channel_id = thread_data.get("channel_id")
            channel_to_send = guild.get_channel(channel_id) or guild.get_thread(channel_id)

        if not channel_to_send:
            is_new = True
            # Create new thread or channel
            style = config.get("thread_style", "thread") # thread or channel

            if style == "channel":
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                staff_role_id = config.get("staff_role_id")
                if staff_role_id:
                    staff_role = guild.get_role(staff_role_id)
                    if staff_role:
                        overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                channel_to_send = await guild.create_text_channel(
                    name=f"modmail-{user.name}",
                    category=log_channel.category,
                    overwrites=overwrites
                )
            else:
                # Default to thread in log_channel
                try:
                    channel_to_send = await log_channel.create_thread(
                        name=f"modmail-{user.name}",
                        type=discord.ChannelType.private_thread if guild.premium_tier >= 2 else discord.ChannelType.public_thread
                    )
                except:
                    # Fallback to public thread if private fails
                    channel_to_send = await log_channel.create_thread(
                        name=f"modmail-{user.name}",
                        type=discord.ChannelType.public_thread
                    )

            thread_data = {
                "user_id": user.id,
                "channel_id": channel_to_send.id,
                "status": "open",
                "opened_at": time.time(),
                "messages": []
            }
            threads[str(user.id)] = thread_data
            dm.update_guild_data(guild.id, "modmail_threads", threads)

            # Initial embed in thread
            embed = Embed(title="📬 New Modmail Thread", color=discord.Color.blue())
            embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
            embed.add_field(name="Account Age", value=f"<t:{int(user.created_at.timestamp())}:R>")
            member = guild.get_member(user.id)
            embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member else "Not in server")

            view = ModmailThreadView()

            # Implementation of "Toggle Pings"
            ping_content = None
            if config.get("new_thread_pings", True):
                staff_role_id = config.get("staff_role_id")
                if staff_role_id:
                    ping_content = f"<@&{staff_role_id}>"

            await channel_to_send.send(content=ping_content, embed=embed, view=view)

            if is_new:
                auto_reply = config.get("auto_reply_message", "Your message has been forwarded to the staff. We'll get back to you soon.")
                try:
                    await user.send(auto_reply)
                except:
                    pass

        # Forward the message
        forward_embed = Embed(description=message.content, color=discord.Color.light_grey())
        forward_embed.set_author(name=user.name, icon_url=user.display_avatar.url)
        forward_embed.timestamp = datetime.datetime.now()

        if message.attachments:
            forward_embed.add_field(name="Attachments", value="\n".join([a.url for a in message.attachments]))

        await channel_to_send.send(embed=forward_embed)

        # Save to history
        thread_data["messages"].append({
            "sender": "user",
            "content": message.content,
            "timestamp": time.time(),
            "attachments": [a.url for a in message.attachments]
        })
        dm.update_guild_data(guild.id, "modmail_threads", threads)

class ModmailThreadView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _get_user(self, interaction: Interaction):
        guild_id = interaction.guild_id
        threads = dm.get_guild_data(guild_id, "modmail_threads", {})
        for uid_str, data in threads.items():
            if data.get("channel_id") == interaction.channel_id:
                return interaction.client.get_user(int(uid_str)) or await interaction.client.fetch_user(int(uid_str))
        return None

    @ui.button(label="Reply", style=ButtonStyle.primary, emoji="💬", custom_id="modmail_reply")
    async def reply(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        if not user: return await interaction.response.send_message("❌ User not found.", ephemeral=True)
        await interaction.response.send_modal(ModmailReplyModal(user))

    @ui.button(label="Send File", style=ButtonStyle.secondary, emoji="📎", custom_id="modmail_file")
    async def send_file(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(ModmailFileModal())

    @ui.button(label="Close", style=ButtonStyle.danger, emoji="🔒", custom_id="modmail_close")
    async def close(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        guild_id = interaction.guild_id
        threads = dm.get_guild_data(guild_id, "modmail_threads", {})
        config = dm.get_guild_data(guild_id, "modmail_config", {})

        if str(user.id) in threads:
            threads[str(user.id)]["status"] = "closed"
            threads[str(user.id)]["closed_at"] = time.time()
            dm.update_guild_data(guild_id, "modmail_threads", threads)

            # Transcript logic
            transcript = f"Modmail Transcript for {user} ({user.id})\n"
            for msg in threads[str(user.id)].get("messages", []):
                ts = datetime.datetime.fromtimestamp(msg['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                transcript += f"[{ts}] {msg['sender']}: {msg['content']}\n"

            # Save transcript
            transcripts = dm.get_guild_data(guild_id, "modmail_transcripts", [])
            transcripts.append({
                "user_id": user.id,
                "username": str(user),
                "closed_at": time.time(),
                "content": transcript
            })
            dm.update_guild_data(guild_id, "modmail_transcripts", transcripts)

            close_msg = config.get("close_message", "This modmail thread has been closed. If you have more questions, feel free to DM again.")
            try: await user.send(close_msg)
            except: pass

            await interaction.response.send_message("🔒 Thread closed.")
            await interaction.channel.edit(archived=True) if isinstance(interaction.channel, discord.Thread) else await interaction.channel.delete()

    @ui.button(label="Block", style=ButtonStyle.danger, emoji="🚫", custom_id="modmail_block")
    async def block(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        blocked = dm.get_guild_data(interaction.guild_id, "modmail_blocked", [])
        if user.id not in blocked:
            blocked.append(user.id)
            dm.update_guild_data(interaction.guild_id, "modmail_blocked", blocked)
            await interaction.response.send_message(f"🚫 User {user} has been blocked from Modmail.", ephemeral=True)
        else:
            await interaction.response.send_message("User is already blocked.", ephemeral=True)

    @ui.button(label="Escalate", style=ButtonStyle.secondary, emoji="⬆️", custom_id="modmail_escalate")
    async def escalate(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("⬆️ Thread escalated to senior staff.")

    @ui.button(label="History", style=ButtonStyle.secondary, emoji="📋", custom_id="modmail_history")
    async def history(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        transcripts = dm.get_guild_data(interaction.guild_id, "modmail_transcripts", [])
        user_history = [t for t in transcripts if t["user_id"] == user.id]

        if not user_history:
            return await interaction.response.send_message("No previous modmail history found.", ephemeral=True)

        desc = ""
        for t in user_history[-5:]:
            desc += f"- Closed at <t:{int(t['closed_at'])}:f>\n"

        await interaction.response.send_message(embed=Embed(title=f"History for {user}", description=desc), ephemeral=True)

    @ui.button(label="Add Note", style=ButtonStyle.secondary, emoji="🏷️", custom_id="modmail_note")
    async def add_note(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(NoteModal())

    @ui.button(label="Pin", style=ButtonStyle.secondary, emoji="📌", custom_id="modmail_pin")
    async def pin(self, interaction: Interaction, button: ui.Button):
        await interaction.message.pin()
        await interaction.response.send_message("📌 Thread message pinned.", ephemeral=True)

    @ui.button(label="User Info", style=ButtonStyle.secondary, emoji="👤", custom_id="modmail_user_info")
    async def user_info(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        member = interaction.guild.get_member(user.id)

        embed = Embed(title=f"User Info: {user}", color=discord.Color.blue())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="ID", value=user.id)
        embed.add_field(name="Created", value=f"<t:{int(user.created_at.timestamp())}:R>")
        if member:
            embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>")
            embed.add_field(name="Roles", value=" ".join([r.mention for r in member.roles[1:][:10]]) or "None")

        await interaction.response.send_message(embed=embed, ephemeral=True)

class ModmailReplyModal(ui.Modal, title="Reply to User"):
    message = ui.TextInput(label="Message", style=TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, user):
        super().__init__()
        self.user = user

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        threads = dm.get_guild_data(guild_id, "modmail_threads", {})

        try:
            embed = Embed(description=self.message.value, color=discord.Color.green())
            embed.set_author(name=f"Staff from {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            await self.user.send(embed=embed)

            # Log in thread
            log_embed = Embed(description=self.message.value, color=discord.Color.green())
            log_embed.set_author(name=f"Reply by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            await interaction.channel.send(embed=log_embed)

            # Save to history
            if str(self.user.id) in threads:
                threads[str(self.user.id)]["messages"].append({
                    "sender": f"staff ({interaction.user.name})",
                    "content": self.message.value,
                    "timestamp": time.time()
                })
                dm.update_guild_data(guild_id, "modmail_threads", threads)

            await interaction.response.send_message("✅ Reply sent.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to DM user: {e}", ephemeral=True)

class ModmailFileModal(ui.Modal, title="Send File URL"):
    url = ui.TextInput(label="File URL", placeholder="https://...", required=True)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.send_message("File URL forwarded to user (simulated).", ephemeral=True)

class NoteModal(ui.Modal, title="Add Staff Note"):
    note = ui.TextInput(label="Internal Note", style=TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: Interaction):
        embed = Embed(title="🏷️ Staff Note", description=self.note.value, color=discord.Color.gold())
        embed.set_footer(text=f"By {interaction.user}")
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Note added.", ephemeral=True)

async def modmail_extension_setup(bot):
    bot.modmail = ModmailSystem(bot)



# ======================================================================
# From: modules/auto_announcer.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


class AutoAnnouncer:
    def __init__(self, bot):
        self.bot = bot
        self._schedules: Dict[int, List[dict]] = {}
        self._reminders: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        data = dm.load_json("announcer_reminders", default={})
        self._schedules = data.get("schedules", {})
        self._reminders = data.get("reminders", {})

    def _save_data(self):
        data = {
            "schedules": self._schedules,
            "reminders": self._reminders
        }
        dm.save_json("announcer_reminders", data)

    def start_loops(self):
        asyncio.create_task(self._announcement_loop())
        asyncio.create_task(self._reminder_loop())

    async def _announcement_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            now = datetime.now()
            for guild_id, schedules in list(self._schedules.items()):
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                
                for schedule in schedules:
                    if schedule.get("posted"):
                        continue
                    
                    post_time = schedule.get("post_time", 0)
                    if now.timestamp() >= post_time:
                        channel_id = schedule.get("channel_id")
                        message = schedule.get("message", "")
                        embed_data = schedule.get("embed", {})
                        
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            logger.warning(f"Announcement channel {channel_id} not found in guild {guild_id}")
                            schedule["posted"] = True  # Mark as posted to avoid retrying
                            self._save_data()
                            continue
                        # Check if bot can send messages to the channel
                        perms = channel.permissions_for(guild.me)
                        if not perms.send_messages:
                            logger.warning(f"Bot lacks send permissions in announcement channel {channel_id} for guild {guild_id}")
                            schedule["posted"] = True
                            self._save_data()
                            continue
                        if embed_data:
                            embed = discord.Embed(
                                title=embed_data.get("title", ""),
                                description=message,
                                color=int(embed_data.get("color", "349ke5f"), 16)
                            )
                            await channel.send(embed=embed)
                        else:
                            await channel.send(message)
                        
                        schedule["posted"] = True
                        self._save_data()
            
            await asyncio.sleep(60)

    async def _reminder_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            now = datetime.now()
            for guild_id, reminders in list(self._reminders.items()):
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                
                for reminder in reminders[:]:
                    if reminder.get("sent"):
                        continue
                    
                    due_time = reminder.get("due_time", 0)
                    if now.timestamp() >= due_time:
                        target_id = reminder.get("target_id")
                        message = reminder.get("message", "")
                        
                        target = guild.get_member(target_id)
                        if target:
                            try:
                                await target.send(f"⏰ Reminder: {message}")
                            except:
                                pass
                        
                        reminder["sent"] = True
                        self._reminders[guild_id] = [r for r in self._reminders[guild_id] if not r.get("sent")]
                        self._save_data()
            
            await asyncio.sleep(60)

    async def handle_announce_create(self, message, parts):
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 2:
            await message.channel.send("Usage: !announce <time> <message>\nExample: !announce 1h Server restart in 30 minutes!")
            return
        
        time_str = parts[1]
        delay_seconds = self._parse_time(time_str)
        
        if delay_seconds is None:
            await message.channel.send("Invalid time! Use: 30s, 5m, 1h, 1d")
            return
        
        remaining_parts = parts[2:] if len(parts) > 2 else [""]
        ann_message = " ".join(remaining_parts)
        
        post_time = datetime.now().timestamp() + delay_seconds
        
        if guild_id not in self._schedules:
            self._schedules[guild_id] = []
        
        self._schedules[guild_id].append({
            "message": ann_message,
            "post_time": post_time,
            "channel_id": message.channel.id,
            "posted": False,
            "created_by": str(message.author)
        })
        
        self._save_data()
        
        wait_min = delay_seconds / 60
        await message.channel.send(f"✅ Scheduled announcement in {wait_min:.0f} minutes!")

    async def handle_announce_list(self, message):
        guild = message.guild
        guild_id = guild.id
        
        schedules = self._schedules.get(guild_id, [])
        
        if not schedules:
            await message.channel.send("No scheduled announcements!")
            return
        
        pending = [s for s in schedules if not s.get("posted")]
        
        embed = discord.Embed(
            title="📅 Scheduled Announcements",
            color=discord.Color.blue()
        )
        
        for s in pending[:10]:
            time_left = s.get("post_time", 0) - datetime.now().timestamp()
            mins = max(0, time_left / 60)
            embed.add_field(
                name=f"⏰ {mins:.0f} min",
                value=s.get("message", "")[:50],
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_remind(self, message, parts):
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 2:
            await message.channel.send("Usage: !remind <time> <message>\nExample: !remind 30m Check the server!")
            return
        
        time_str = parts[1]
        delay_seconds = self._parse_time(time_str)
        
        if delay_seconds is None:
            await message.channel.send("Invalid time! Use: 30s, 5m, 1h, 1d")
            return
        
        reminder_text = " ".join(parts[2:]) if len(parts) > 2 else "Reminder"
        
        due_time = datetime.now().timestamp() + delay_seconds
        
        if guild_id not in self._reminders:
            self._reminders[guild_id] = []
        
        self._reminders[guild_id].append({
            "message": reminder_text,
            "due_time": due_time,
            "target_id": message.author.id,
            "sent": False
        })
        
        self._save_data()
        
        wait_min = delay_seconds / 60
        await message.channel.send(f"✅ Reminder set for {wait_min:.0f} minutes!")

    async def handle_remind_user(self, message, parts):
        guild = message.guild
        
        if len(parts) < 3:
            await message.channel.send("Usage: !remind @user <time> <message>")
            return
        
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        time_str = parts[2]
        delay_seconds = self._parse_time(time_str)
        
        if delay_seconds is None:
            await message.channel.send("Invalid time!")
            return
        
        reminder_text = " ".join(parts[3:]) if len(parts) > 3 else "Reminder"
        
        guild_id = guild.id
        due_time = datetime.now().timestamp() + delay_seconds
        
        if guild_id not in self._reminders:
            self._reminders[guild_id] = []
        
        self._reminders[guild_id].append({
            "message": reminder_text,
            "due_time": due_time,
            "target_id": user_id,
            "sent": False
        })
        
        self._save_data()
        
        target = guild.get_member(user_id)
        wait_min = delay_seconds / 60
        await message.channel.send(f"✅ Will remind {target.display_name} in {wait_min:.0f} minutes!")

    async def handle_reminders_list(self, message):
        guild = message.guild
        guild_id = guild.id
        
        user_id = message.author.id
        reminders = self._reminders.get(guild_id, [])
        user_reminders = [r for r in reminders if r.get("target_id") == user_id and not r.get("sent")]
        
        if not user_reminders:
            await message.channel.send("No active reminders!")
            return
        
        embed = discord.Embed(
            title="⏰ Your Reminders",
            color=discord.Color.blue()
        )
        
        for r in user_reminders[:10]:
            time_left = r.get("due_time", 0) - datetime.now().timestamp()
            mins = max(0, time_left / 60)
            embed.add_field(
                name=f"⏰ {mins:.0f} min",
                value=r.get("message", ""),
                inline=False
            )
        
        await message.channel.send(embed=embed)

    def _parse_time(self, time_str: str) -> Optional[float]:
        time_str = time_str.lower()
        
        multipliers = {
            "s": 1,
            "sec": 1,
            "m": 60,
            "min": 60,
            "h": 3600,
            "hour": 3600,
            "d": 86400,
            "day": 86400
        }
        
        for unit, mult in multipliers.items():
            if time_str.endswith(unit):
                try:
                    num = float(time_str[:-len(unit)])
                    return num * mult
                except:
                    return None
        
        return None


def auto_announcer_extension_setup(bot):
    return AutoAnnouncer(bot)



# ======================================================================
# From: modules/auto_publisher.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional

from data_manager import dm
from logger import logger


class AutoPublisher:
    def __init__(self, bot):
        self.bot = bot
        self._publish_channels: Dict[int, List[int]] = {}
        self._load_settings()

    def _load_settings(self):
        """Settings are now loaded per-guild in get_guild_settings."""
        # No global settings to load
        return

    def _save_settings(self, guild_id: int, settings: dict):
        dm.update_guild_data(guild_id, "auto_publisher_settings", settings)

    def start_bump_monitor(self):
        asyncio.create_task(self._bump_monitor_loop())

    async def _bump_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    settings = self.get_guild_settings(guild.id)
                    bump_channel_id = settings.get("bump_channel")
                    
                    if bump_channel_id:
                        channel = guild.get_channel(int(bump_channel_id))
                        if channel:
                            await self._check_bump_reminder(channel, settings)
            except Exception as e:
                logger.error(f"Bump monitor error: {e}")
            
            await asyncio.sleep(3600)

    async def _check_bump_reminder(self, channel: discord.TextChannel, settings: dict):
        messages = []
        
        try:
            async for message in channel.history(limit=10):
                messages.append(message)
        except Exception as e:
            logger.error(f"Failed to get channel history: {e}")
            return
        
        # Common bump bot IDs (DISBOARD, etc.)
        BUMP_BOT_IDS = [302050872383242240]

        for message in messages:
            if message.author.id in BUMP_BOT_IDS:
                if "bump" in message.content.lower() or "success" in message.content.lower():
                    last_bump = message.created_at.timestamp()
                    time_since = time.time() - last_bump
                    
                    if time_since > 7200:
                        embed = discord.Embed(
                            title="💡 Bump Reminder",
                            description="The server can be bumped! Use `/bump` to help the server grow.",
                            color=discord.Color.blue()
                        )
                        
                        try:
                            await channel.send(embed=embed)
                        except Exception as e:
                            logger.error(f"Failed to send bump reminder: {e}")
                    
                    break

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "auto_publisher_settings", {
            "enabled": True,
            "auto_publish": True,
            "publish_channels": [],
            "announcement_channel": None,
            "bump_channel": None,
            "bump_reminder": True
        })

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        guild_id = message.guild.id
        settings = self.get_guild_settings(guild_id)
        
        if not settings.get("auto_publish", True):
            return
        
        if not isinstance(message.channel, discord.Thread):
            return
        
        thread = message.channel
        
        if thread.parent_id in settings.get("publish_channels", []):
            try:
                if not thread.pinned:
                    await thread.publish()
            except Exception as e:
                logger.error(f"Failed to publish thread: {e}")

    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if before.pinned or after.pinned:
            return
        
        guild_id = before.guild.id
        settings = self.get_guild_settings(guild_id)
        
        if not settings.get("auto_publish", True):
            return
        
        if before.parent_id in settings.get("publish_channels", []):
            try:
                if not after.pinned:
                    await after.publish()
            except Exception as e:
                logger.error(f"Failed to publish thread on update: {e}")

    def add_publish_channel(self, guild_id: int, channel_id: int):
        settings = self.get_guild_settings(guild_id)
        if "publish_channels" not in settings:
            settings["publish_channels"] = []
        
        if channel_id not in settings["publish_channels"]:
            settings["publish_channels"].append(channel_id)
            self._save_settings(guild_id, settings)

    async def create_announcement(self, guild_id: int, channel_id: int, title: str, 
                                  content: str, mention_roles: List[int] = None) -> discord.Message:
        if mention_roles is None:
            mention_roles = []
            
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None
            
        channel = guild.get_channel(channel_id)
        
        if not channel:
            return None

        embed = discord.Embed(
            title=title,
            description=content,
            color=discord.Color.blue()
        )

        mentions = []
        if mention_roles:
            for role_id in mention_roles:
                role = guild.get_role(role_id)
                if role:
                    mentions.append(role.mention)

        message_content = ", ".join(mentions) if mentions else ""

        try:
            msg = await channel.send(content=message_content, embed=embed)
            await msg.publish()
            return msg
        except Exception as e:
            logger.error(f"Failed to create announcement: {e}")
            return None

    async def schedule_announcement(self, guild_id: int, channel_id: int, title: str,
                                    content: str, post_at: float, mention_roles: List[int] = None):
        if mention_roles is None:
            mention_roles = []
            
        scheduled = {
            "id": f"scheduled_{guild_id}_{int(time.time())}",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "title": title,
            "content": content,
            "post_at": post_at,
            "mention_roles": mention_roles or [],
            "created_at": time.time()
        }

        scheduled_announcements = dm.get_guild_data(guild_id, "scheduled_announcements", {})
        scheduled_announcements[scheduled["id"]] = scheduled
        dm.update_guild_data(guild_id, "scheduled_announcements", scheduled_announcements)

        asyncio.create_task(self._post_scheduled(scheduled))

        return scheduled

    async def _post_scheduled(self, scheduled: dict):
        wait_time = scheduled["post_at"] - time.time()
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        await self.create_announcement(
            scheduled["guild_id"],
            scheduled["channel_id"],
            scheduled["title"],
            scheduled["content"],
            scheduled.get("mention_roles", [])
        )
        
        scheduled_announcements = dm.get_guild_data(scheduled["guild_id"], "scheduled_announcements", {})
        if scheduled["id"] in scheduled_announcements:
            del scheduled_announcements[scheduled["id"]]
            dm.update_guild_data(scheduled["guild_id"], "scheduled_announcements", scheduled_announcements)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "auto_publisher_settings", settings)
        
        help_embed = discord.Embed(
            title="📢 Auto-Publisher",
            description="Auto-publish threads and announcement management.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Automatically publishes new threads in selected channels. Supports scheduled announcements and bump reminders.",
            inline=False
        )
        help_embed.add_field(
            name="!announce",
            value="Create an announcement (admin).",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["announce"] = json.dumps({
            "command_type": "create_announcement"
        })
        custom_cmds["help publisher"] = json.dumps({
            "command_type": "help_embed",
            "title": "📢 Auto-Publisher",
            "description": "Auto-publish threads.",
            "fields": [
                {"name": "!announce", "value": "Create announcement.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


async def auto_publisher_extension_setup(bot):
    await bot.add_cog(AutoPublisher(bot))



# ======================================================================
# From: modules/events.py
# ======================================================================

import discord
import asyncio
import json
import time
import random
import re
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
import croniter

from data_manager import dm
from logger import logger


class EventType(Enum):
    TRIVIA = "trivia"
    STORY_BUILD = "story_build"
    DEBATE = "debate"
    QUIZ = "quiz"
    GAME = "game"
    GIVEAWAY = "giveaway"
    CONTEST = "contest"
    POLL = "poll"
    CUSTOM = "custom"


class EventStatus(Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledEvent:
    id: str
    guild_id: int
    channel_id: int
    name: str
    description: str
    event_type: EventType
    schedule: str
    next_run: float
    status: EventStatus
    rewards: dict
    settings: dict
    created_by: int
    created_at: float
    participants: List[int]
    message_id: Optional[int]


@dataclass
class ActiveEvent:
    id: str
    event_type: EventType
    message_id: int
    channel_id: int
    guild_id: int
    data: dict
    started_at: float
    participants: List[int]
    scores: Dict[int, int]


class EventScheduler:
    def __init__(self, bot):
        self.bot = bot
        self._scheduled_events: Dict[str, ScheduledEvent] = {}
        self._active_events: Dict[str, ActiveEvent] = {}
        self._guild_settings: Dict[int, dict] = {}
        self._load_scheduled_events()

    def _load_scheduled_events(self):
        events_data = dm.load_json("scheduled_events", default={})
        
        for event_id, data in events_data.items():
            try:
                event = ScheduledEvent(
                    id=event_id,
                    guild_id=data["guild_id"],
                    channel_id=data["channel_id"],
                    name=data["name"],
                    description=data["description"],
                    event_type=EventType(data["event_type"]),
                    schedule=data["schedule"],
                    next_run=data["next_run"],
                    status=EventStatus(data["status"]),
                    rewards=data["rewards"],
                    settings=data["settings"],
                    created_by=data["created_by"],
                    created_at=data["created_at"],
                    participants=data.get("participants", []),
                    message_id=data.get("message_id")
                )
                self._scheduled_events[event_id] = event
            except Exception as e:
                logger.error(f"Failed to load scheduled event {event_id}: {e}")

    def _save_scheduled_event(self, event: ScheduledEvent):
        events_data = dm.load_json("scheduled_events", default={})
        events_data[event.id] = {
            "guild_id": event.guild_id,
            "channel_id": event.channel_id,
            "name": event.name,
            "description": event.description,
            "event_type": event.event_type.value,
            "schedule": event.schedule,
            "next_run": event.next_run,
            "status": event.status.value,
            "rewards": event.rewards,
            "settings": event.settings,
            "created_by": event.created_by,
            "created_at": event.created_at,
            "participants": event.participants,
            "message_id": event.message_id
        }
        dm.save_json("scheduled_events", events_data)

    def get_guild_settings(self, guild_id: int) -> dict:
        if guild_id in self._guild_settings:
            return self._guild_settings[guild_id]
        
        settings = dm.get_guild_data(guild_id, "event_settings", {
            "enabled": True,
            "auto_rewards": True,
            "default_rewards": {
                "coins": 100,
                "xp": 50
            },
            "optimal_hours": [18, 19, 20, 21],
            "min_participants": 3,
            "max_duration_minutes": 30
        })
        self._guild_settings[guild_id] = settings
        return settings

    def calculate_next_run(self, schedule: str) -> Optional[float]:
        try:
            cron = croniter.croniter(schedule)
            return cron.get_next()
        except:
            return None

    def start_event_monitor(self):
        asyncio.create_task(self._event_monitor_loop())

    async def _event_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                current_time = time.time()
                
                for event_id, event in list(self._scheduled_events.items()):
                    if event.status == EventStatus.SCHEDULED and event.next_run <= current_time:
                        await self._start_event(event)
                
                for event_id, event in list(self._active_events.items()):
                    duration = event.data.get("duration_minutes", 15)
                    if current_time - event.started_at >= duration * 60:
                        await self._end_event(event)
            
            except Exception as e:
                logger.error(f"Event monitor error: {e}")
            
            await asyncio.sleep(30)

    async def _start_event(self, event: ScheduledEvent):
        event.status = EventStatus.ACTIVE
        self._save_scheduled_event(event)
        
        channel = self.bot.get_channel(event.channel_id)
        if not channel:
            logger.error(f"Event channel not found: {event.channel_id}")
            return
        
        embed = discord.Embed(
            title=f"🎮 {event.name}",
            description=event.description,
            color=discord.Color.gold()
        )
        embed.add_field(name="Type", value=event.event_type.value.title(), inline=True)
        embed.add_field(name="Rewards", value=f"💰 {event.rewards.get('coins', 0)} coins, XP {event.rewards.get('xp', 0)}", inline=True)
        
        view = discord.ui.View()
        join_btn = discord.ui.Button(label="Join Event", style=discord.ButtonStyle.primary, custom_id=f"event_join_{event.id}")
        
        async def join_callback(interaction: discord.Interaction):
            if interaction.message.id != event.message_id:
                return
            
            if interaction.user.id in event.participants:
                await interaction.response.send_message("You already joined!", ephemeral=True)
                return
            
            event.participants.append(interaction.user.id)
            self._save_scheduled_event(event)
            
            await interaction.response.send_message(f"✅ Joined {event.name}!", ephemeral=True)
            self._update_event_message(event)
        
        join_btn.callback = join_callback
        view.add_item(join_btn)
        
        message = await channel.send(embed=embed, view=view)
        event.message_id = message.id
        
        active_event = ActiveEvent(
            id=event.id,
            event_type=event.event_type,
            message_id=message.id,
            channel_id=channel.id,
            guild_id=event.guild_id,
            data={
                "name": event.name,
                "description": event.description,
                "settings": event.settings,
                "duration_minutes": event.settings.get("duration", 15),
                "questions": [],
                "current_question": 0
            },
            started_at=time.time(),
            participants=event.participants,
            scores={}
        )
        self._active_events[event.id] = active_event
        
        if event.event_type == EventType.TRIVIA:
            await self._run_trivia_event(event, channel, active_event)
        elif event.event_type == EventType.STORY_BUILD:
            await self._run_story_event(event, channel, active_event)
        
        logger.info(f"Started event: {event.name} in {channel.name}")

    async def _run_trivia_event(self, event: ScheduledEvent, channel: discord.TextChannel, active_event: ActiveEvent):
        topics = active_event.data["settings"].get("topics", ["general"])
        
        topics_str = ", ".join(topics)
        trivia_prompt = f"""Generate 5 trivia questions about {topics_str}.
        
Respond with JSON only:
{{
    "questions": [
        {{
            "question": "question text",
            "options": ["option A", "option B", "option C", "option D"],
            "correct": 0
        }}
    ]
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=event.guild_id,
                user_id=event.created_by,
                user_input=trivia_prompt,
                system_prompt="You generate fun trivia questions. Return exactly 5 questions with 4 options each and indicate which option is correct (0-3)."
            )
            
            active_event.data["questions"] = result.get("questions", [])
        except Exception as e:
            logger.error(f"Failed to generate trivia: {e}")
            active_event.data["questions"] = self._get_default_trivia()

    def _get_default_trivia(self) -> List[dict]:
        return [
            {"question": "What is 2 + 2?", "options": ["3", "4", "5", "6"], "correct": 1},
            {"question": "What color is the sky?", "options": ["Red", "Blue", "Green", "Yellow"], "correct": 1},
            {"question": "What is the capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "correct": 2},
            {"question": "How many days in a year?", "options": ["365", "366", "364", "360"], "correct": 0},
            {"question": "What is H2O?", "options": ["Salt", "Water", "Gold", "Oxygen"], "correct": 1}
        ]

    async def _run_story_event(self, event: ScheduledEvent, channel: discord.TextChannel, active_event: ActiveEvent):
        story_prompt = active_event.data["settings"].get("story_prompt", "Start a creative story with the theme: adventure")
        
        try:
            result = await self.bot.ai.chat(
                guild_id=event.guild_id,
                user_id=event.created_by,
                user_input=story_prompt,
                system_prompt="Continue a collaborative story. Keep each contribution to 1-2 sentences. Build on previous contributions."
            )
            
            active_event.data["story"] = result.get("summary", "The story begins...")
            active_event.data["contributions"] = []
        except Exception as e:
            logger.error(f"Failed to start story: {e}")
            active_event.data["story"] = "The story begins..."
            active_event.data["contributions"] = []
    
    """Poll System"""
    POLL_OPTIONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    async def create_poll(self, channel: discord.TextChannel, question: str, options: List[str], 
                        duration_minutes: int = 60, multiple_choice: bool = False) -> discord.Message:
        """Create a poll message with reactions."""
        if len(options) > 10:
            options = options[:10]
        
        options_text = "\n".join(f"{self.POLL_OPTIONS[i]} {opt}" for i, opt in enumerate(options))
        
        embed = discord.Embed(title="📊 New Poll!", color=discord.Color.blurple())
        embed.add_field(name=question, value=options_text, inline=False)
        embed.set_footer(text=f"Duration: {duration_minutes} minutes | {'Multiple choice' if multiple_choice else 'Single choice'}")
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="End Poll", style=discord.ButtonStyle.danger, custom_id="end_poll"))
        
        msg = await channel.send(embed=embed)
        
        # Add reactions
        for i in range(len(options)):
            await msg.add_reaction(self.POLL_OPTIONS[i])
        
        # Add vote counts to message
        await msg.add_reaction("📊")
        
        return msg
    
    async def create_contest(self, channel: discord.TextChannel, title: str, description: str,
                           submission_deadline: int = 7) -> discord.Message:
        """Create a contest with submissions."""
        embed = discord.Embed(title=f"🏆 {title}", description=description, color=discord.Color.gold())
        embed.add_field(name="How to Enter", value="DM the bot your submission!", inline=False)
        embed.add_field(name="Deadline", value=f"{submission_deadline} days", inline=False)
        
        msg = await channel.send(embed=embed)
        
        return msg
    
    async def _end_event(self, active_event: ActiveEvent):
        event_id = active_event.id
        
        if event_id in self._scheduled_events:
            scheduled = self._scheduled_events[event_id]
            scheduled.status = EventStatus.COMPLETED
            scheduled.next_run = self.calculate_next_run(scheduled.schedule) or (time.time() + 86400)
            scheduled.status = EventStatus.SCHEDULED
            scheduled.participants = []
            self._save_scheduled_event(scheduled)
        
        channel = self.bot.get_channel(active_event.channel_id)
        if channel and active_event.participants:
            embed = discord.Embed(
                title=f"🏆 Event Ended: {active_event.data['name']}",
                description="Thanks for participating!",
                color=discord.Color.gold()
            )
            
            if active_event.scores:
                top_scores = sorted(active_event.scores.items(), key=lambda x: x[1], reverse=True)[:5]
                score_text = "\n".join([f"**{i+1}.** <@{uid}> - {score} pts" for i, (uid, score) in enumerate(top_scores)])
                embed.add_field(name="Leaderboard", value=score_text, inline=False)
                
                await self._distribute_rewards(active_event, top_scores)
            else:
                embed.add_field(name="Results", value="No scores recorded.", inline=False)
            
            await channel.send(embed=embed)
        
        del self._active_events[event_id]
        logger.info(f"Event ended: {active_event.data['name']}")

    async def _distribute_rewards(self, active_event: ActiveEvent, top_scores: List[tuple]):
        event_id = active_event.id
        if event_id in self._scheduled_events:
            scheduled = self._scheduled_events[event_id]
            rewards = scheduled.rewards
            
            for i, (user_id, score) in enumerate(top_scores):
                multiplier = 1.0 if i == 0 else 0.5 if i == 1 else 0.25
                
                coins = int(rewards.get("coins", 100) * multiplier)
                xp = int(rewards.get("xp", 50) * multiplier)
                
                user_data = dm.get_guild_data(active_event.guild_id, f"user_{user_id}", {})
                user_data["coins"] = user_data.get("coins", 0) + coins
                user_data["xp"] = user_data.get("xp", 0) + xp
                dm.update_guild_data(active_event.guild_id, f"user_{user_id}", user_data)

    def _update_event_message(self, event: ScheduledEvent):
        """Update the event embed to reflect current participant count."""
        if not event.message_id:
            return

        async def _update():
            try:
                channel = self.bot.get_channel(event.channel_id)
                if not channel:
                    return
                message = await channel.fetch_message(event.message_id)
                if not message:
                    return

                embed = message.embeds[0]
                # Update participant count field
                field_found = False
                for i, field in enumerate(embed.fields):
                    if field.name == "Participants":
                        embed.set_field_at(i, name="Participants", value=str(len(event.participants)), inline=True)
                        field_found = True
                        break

                if not field_found:
                    embed.add_field(name="Participants", value=str(len(event.participants)), inline=True)

                await message.edit(embed=embed)
            except Exception as e:
                logger.debug(f"Could not update event message: {e}")

        asyncio.create_task(_update())

    async def create_event(self, guild_id: int, channel_id: int, name: str, description: str,
                          event_type: EventType, schedule: str, created_by: int, 
                          rewards: dict = None, settings: dict = None) -> ScheduledEvent:
        event_id = f"event_{guild_id}_{int(time.time())}"
        
        next_run = self.calculate_next_run(schedule)
        
        event = ScheduledEvent(
            id=event_id,
            guild_id=guild_id,
            channel_id=channel_id,
            name=name,
            description=description,
            event_type=event_type,
            schedule=schedule,
            next_run=next_run or time.time(),
            status=EventStatus.SCHEDULED,
            rewards=rewards or {"coins": 100, "xp": 50},
            settings=settings or {},
            created_by=created_by,
            created_at=time.time(),
            participants=[],
            message_id=None
        )
        
        self._scheduled_events[event_id] = event
        self._save_scheduled_event(event)
        
        return event

    async def ai_create_event(self, guild_id: int, user_id: int, request: str) -> ScheduledEvent:
        settings = self.get_guild_settings(guild_id)
        
        prompt = f"""Create a scheduled event based on this request: "{request}"

Available event types: trivia, story_build, debate, quiz, game, giveaway, contest
Schedule format: cron expression (e.g., "0 19 * * *" for daily at 7pm)

Respond with JSON only:
{{
    "name": "Event name",
    "description": "What happens in this event",
    "event_type": "trivia/story_build/etc",
    "schedule": "0 19 * * *",
    "topics": ["topic1", "topic2"],
    "rewards": {{"coins": 100, "xp": 50}},
    "duration": 15
}}"""

        result = await self.bot.ai.chat(
            guild_id=guild_id,
            user_id=user_id,
            user_input=prompt,
            system_prompt="You create fun Discord events. Be creative and specific. Use standard cron format for schedules."
        )
        
        channel = self.bot.get_guild(guild_id).text_channels[0]
        
        return await self.create_event(
            guild_id=guild_id,
            channel_id=channel.id,
            name=result.get("name", "AI Event"),
            description=result.get("description", "Fun event!"),
            event_type=EventType(result.get("event_type", "trivia")),
            schedule=result.get("schedule", "0 19 * * *"),
            created_by=user_id,
            rewards=result.get("rewards", settings["default_rewards"]),
            settings=result
        )

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "event_settings", settings)
        
        help_embed = discord.Embed(
            title="📅 Smart Event Scheduler",
            description="AI-powered event creation and scheduling with automatic rewards.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Tell the AI what kind of event you want, and it will create and schedule it automatically. Events run on cron schedules with automatic participation rewards.",
            inline=False
        )
        help_embed.add_field(
            name="AI Event Creation",
            value="Use /bot to create events: 'Create a weekly trivia event about science on Sundays at 8pm'",
            inline=False
        )
        help_embed.add_field(
            name="!events",
            value="List all scheduled events.",
            inline=False
        )
        help_embed.add_field(
            name="!join <event>",
            value="Join an active event.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["events"] = json.dumps({
            "command_type": "list_events"
        })
        custom_cmds["evenf"] = json.dumps({
            "command_type": "list_events"
        })
        custom_cmds["evenf create"] = json.dumps({
            "command_type": "create_event"
        })
        custom_cmds["help events"] = json.dumps({
            "command_type": "help_embed",
            "title": "📅 Smart Event Scheduler",
            "description": "AI-powered event creation and scheduling.",
            "fields": [
                {"name": "How it works", "value": "Tell the AI what kind of event you want.", "inline": False},
                {"name": "!events", "value": "List all scheduled events.", "inline": False},
                {"name": "!join <event>", "value": "Join an active event.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
