import discord
from discord import ui
import time
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class SuggestionSystem:
    """
    Complete suggestion system with voting, staff review, and implementation tracking.
    Features:
    - Suggestion submission via modal
    - Upvote/downvote buttons
    - Staff approve/deny actions
    - Suggestion threads
    - Status tracking
    """

    def __init__(self, bot):
        self.bot = bot

    async def create_suggestion(self, interaction):
        """Create a new suggestion."""
        config = dm.get_guild_data(interaction.guild.id, "suggestions_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Suggestions system is disabled.", ephemeral=True)

        modal = SuggestionModal(self)
        await interaction.response.send_modal(modal)

    async def submit_suggestion(self, interaction, title: str, description: str):
        """Submit a suggestion."""
        config = dm.get_guild_data(interaction.guild.id, "suggestions_config", {})

        # Get suggestions channel
        channel_id = config.get("suggestions_channel")
        if not channel_id:
            return await interaction.response.send_message("❌ Suggestions channel not configured.", ephemeral=True)

        channel = interaction.guild.get_channel(int(channel_id))
        if not channel:
            return await interaction.response.send_message("❌ Suggestions channel not found.", ephemeral=True)

        # Create suggestion embed
        embed = discord.Embed(
            title=f"💡 {title}",
            description=description,
            color=discord.Color.blue()
        )
        embed.add_field(name="Suggested by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Status", value="📝 Pending Review", inline=True)
        embed.add_field(name="Votes", value="👍 0 | 👎 0", inline=True)
        embed.set_footer(text=f"Suggestion ID: {int(time.time())}")

        # Send suggestion
        view = SuggestionVoteView(self, int(time.time()))
        message = await channel.send(embed=embed, view=view)

        # Store suggestion data
        suggestion_data = {
            "id": int(time.time()),
            "message_id": message.id,
            "channel_id": channel.id,
            "user_id": interaction.user.id,
            "title": title,
            "description": description,
            "status": "pending",
            "votes": {"up": 0, "down": 0, "users": []},
            "created_at": time.time()
        }

        suggestions = dm.get_guild_data(interaction.guild.id, "suggestions", [])
        suggestions.append(suggestion_data)
        dm.update_guild_data(interaction.guild.id, "suggestions", suggestions)

        await interaction.response.send_message("✅ Suggestion submitted!", ephemeral=True)

    async def handle_vote(self, interaction, suggestion_id: int, vote_type: str):
        """Handle upvote or downvote."""
        suggestions = dm.get_guild_data(interaction.guild.id, "suggestions", [])
        suggestion = next((s for s in suggestions if s["id"] == suggestion_id), None)

        if not suggestion:
            return await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)

        user_id = interaction.user.id
        votes = suggestion.get("votes", {"up": 0, "down": 0, "users": []})

        # Check if user already voted
        if user_id in votes.get("users", []):
            return await interaction.response.send_message("❌ You have already voted on this suggestion.", ephemeral=True)

        # Add vote
        if vote_type == "up":
            votes["up"] += 1
        else:
            votes["down"] += 1

        votes["users"].append(user_id)
        suggestion["votes"] = votes

        # Update in storage
        for i, s in enumerate(suggestions):
            if s["id"] == suggestion_id:
                suggestions[i] = suggestion
                break
        dm.update_guild_data(interaction.guild.id, "suggestions", suggestions)

        # Update embed
        try:
            channel = interaction.guild.get_channel(suggestion["channel_id"])
            message = await channel.fetch_message(suggestion["message_id"])

            embed = message.embeds[0]
            embed.set_field_at(2, name="Votes", value=f"👍 {votes['up']} | 👎 {votes['down']}", inline=True)

            await message.edit(embed=embed)
        except:
            pass

        emoji = "👍" if vote_type == "up" else "👎"
        await interaction.response.send_message(f"{emoji} Vote recorded!", ephemeral=True)

    async def staff_review(self, interaction, suggestion_id: int, action: str, reason: str = None):
        """Staff approve or deny suggestion."""
        # Check staff permissions
        config = dm.get_guild_data(interaction.guild.id, "suggestions_config", {})
        is_staff = (interaction.user.guild_permissions.administrator or
                   any(role.id == int(rid) for rid in config.get("staff_roles", []) for role in interaction.user.roles))

        if not is_staff:
            return await interaction.response.send_message("❌ Only staff can review suggestions.", ephemeral=True)

        suggestions = dm.get_guild_data(interaction.guild.id, "suggestions", [])
        suggestion = next((s for s in suggestions if s["id"] == suggestion_id), None)

        if not suggestion:
            return await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)

        # Update status
        if action == "approve":
            suggestion["status"] = "approved"
            status_text = "✅ Approved"
            color = discord.Color.green()
        elif action == "deny":
            suggestion["status"] = "denied"
            status_text = "❌ Denied"
            color = discord.Color.red()
        else:
            suggestion["status"] = "implemented"
            status_text = "🎉 Implemented"
            color = discord.Color.gold()

        suggestion["reviewed_by"] = interaction.user.id
        suggestion["reviewed_at"] = time.time()
        if reason:
            suggestion["review_reason"] = reason

        # Update in storage
        for i, s in enumerate(suggestions):
            if s["id"] == suggestion_id:
                suggestions[i] = suggestion
                break
        dm.update_guild_data(interaction.guild.id, "suggestions", suggestions)

        # Update embed
        try:
            channel = interaction.guild.get_channel(suggestion["channel_id"])
            message = await channel.fetch_message(suggestion["message_id"])

            embed = message.embeds[0]
            embed.set_field_at(1, name="Status", value=status_text, inline=True)
            embed.color = color

            if reason:
                embed.add_field(name="Review Reason", value=reason, inline=False)

            # Remove vote buttons for approved/denied suggestions
            if action in ["approve", "deny"]:
                await message.edit(embed=embed, view=None)
            else:
                await message.edit(embed=embed)

        except Exception as e:
            logger.error(f"Failed to update suggestion embed: {e}")

        await interaction.response.send_message(f"✅ Suggestion {action}d!", ephemeral=True)

    # Config panel
    def get_config_panel(self, guild_id: int):
        return SuggestionsConfigPanel(self.bot, guild_id)

    def get_persistent_views(self):
        return [SuggestionVoteView(self, 0)]

class SuggestionModal(discord.ui.Modal, title="Create Suggestion"):
    title = discord.ui.TextInput(label="Title", placeholder="Brief title for your suggestion")
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="Detailed description of your suggestion")

    def __init__(self, suggestion_system):
        super().__init__()
        self.suggestion_system = suggestion_system

    async def on_submit(self, interaction: discord.Interaction):
        if len(self.title.value.strip()) < 5:
            return await interaction.response.send_message("❌ Title must be at least 5 characters.", ephemeral=True)
        if len(self.description.value.strip()) < 10:
            return await interaction.response.send_message("❌ Description must be at least 10 characters.", ephemeral=True)

        await self.suggestion_system.submit_suggestion(interaction, self.title.value.strip(), self.description.value.strip())

class SuggestionVoteView(discord.ui.View):
    def __init__(self, suggestion_system, suggestion_id: int):
        super().__init__(timeout=None)
        self.suggestion_system = suggestion_system
        self.suggestion_id = suggestion_id

    @discord.ui.button(emoji="👍", style=discord.ButtonStyle.success, custom_id="suggestion_upvote")
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.suggestion_system.handle_vote(interaction, self.suggestion_id, "up")

    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.danger, custom_id="suggestion_downvote")
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.suggestion_system.handle_vote(interaction, self.suggestion_id, "down")

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="suggestion_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StaffReviewModal(self.suggestion_system, self.suggestion_id, "approve")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="suggestion_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StaffReviewModal(self.suggestion_system, self.suggestion_id, "deny")
        await interaction.response.send_modal(modal)

class StaffReviewModal(discord.ui.Modal, title="Staff Review"):
    reason = discord.ui.TextInput(label="Reason (optional)", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, suggestion_system, suggestion_id: int, action: str):
        super().__init__(title=f"{'Approve' if action == 'approve' else 'Deny'} Suggestion")
        self.suggestion_system = suggestion_system
        self.suggestion_id = suggestion_id
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        await self.suggestion_system.staff_review(
            interaction,
            self.suggestion_id,
            self.action,
            self.reason.value.strip() if self.reason.value else None
        )

class SuggestionsConfigPanel(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.suggestions = SuggestionSystem(bot)

    @discord.ui.button(label="Toggle Suggestions", style=discord.ButtonStyle.primary, row=0)
    async def toggle_suggestions(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "suggestions_config", config)
        await interaction.response.send_message(f"✅ Suggestions {'enabled' if not enabled else 'disabled'}", ephemeral=True)

    @discord.ui.button(label="Set Suggestions Channel", style=discord.ButtonStyle.secondary, row=0)
    async def set_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetSuggestionsChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class SetSuggestionsChannelModal(discord.ui.Modal, title="Set Suggestions Channel"):
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

            config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
            config["suggestions_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "suggestions_config", config)
            await interaction.response.send_message(f"✅ Suggestions channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid channel ID", ephemeral=True)