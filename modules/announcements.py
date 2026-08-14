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