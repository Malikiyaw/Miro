import discord
from discord.ext import commands
from discord import ui
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from enum import Enum
import time
import asyncio

from data_manager import dm
from logger import logger


class ButtonType(Enum):
    VERIFY = "verify"
    APPLY_STAFF = "apply_staff"
    CREATE_TICKET = "create_ticket"
    CUSTOM = "custom"


@dataclass
class EmbedConfig:
    """Configuration for an embed with buttons"""
    title: str
    description: str
    color: discord.Color = discord.Color.blue()
    fields: Optional[List[Dict[str, Any]]] = None
    footer: Optional[str] = None
    thumbnail: Optional[str] = None
    image: Optional[str] = None
    buttons: Optional[List[ButtonType]] = None
    custom_buttons: Optional[List[Dict[str, Any]]] = None  # For extensible custom buttons

    def __post_init__(self):
        if self.fields is None:
            self.fields = []
        if self.buttons is None:
            self.buttons = []


class EmbedSystem:
    """Robust system for creating embeds with buttons and modals"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_views: Dict[str, ui.View] = {}  # Track active views by message ID

    def get_persistent_views(self) -> List[ui.View]:
        """Persistent views that survive restarts (guild resolved at click time)."""
        return [
            EmbedVerifyView(0),
            EmbedApplyStaffView(0),
            EmbedCreateTicketView(0),
        ]
        
    async def create_embed_with_buttons(
        self,
        channel: discord.TextChannel,
        config: EmbedConfig,
        guild_id: int,
        timeout: int = 300  # 5 minutes default timeout
    ) -> discord.Message:
        """
        Create and send an embed with buttons
        
        Args:
            channel: The channel to send the embed to
            config: Configuration for the embed
            guild_id: The guild ID for context
            timeout: Timeout in seconds for button interactions
            
        Returns:
            The sent message
        """
        try:
            embed = self._build_embed(config)
            view = self._build_view(config.buttons, config.custom_buttons, guild_id, timeout)
            
            message = await channel.send(embed=embed, view=view)
            
            # Track the view for potential cleanup
            if timeout > 0:
                view_id = f"{guild_id}_{message.id}"
                self._active_views[view_id] = view
                
                # Schedule cleanup
                self.bot.loop.create_task(self._cleanup_view_later(view_id, timeout))
            
            logger.info(f"Created embed with buttons in {channel.name} (guild: {guild_id})")
            return message
            
        except Exception as e:
            logger.error(f"Failed to create embed with buttons: {e}")
            raise
    
    def _build_embed(self, config: EmbedConfig) -> discord.Embed:
        """Build a Discord embed from configuration"""
        embed = discord.Embed(
            title=config.title,
            description=config.description,
            color=config.color
        )
        
        for field in config.fields or []:
            embed.add_field(
                name=field['name'],
                value=field['value'],
                inline=field.get('inline', False)
            )
        
        if config.footer:
            embed.set_footer(text=config.footer)
        
        if config.thumbnail:
            embed.set_thumbnail(url=config.thumbnail)
        
        if config.image:
            embed.set_image(url=config.image)
        
        embed.timestamp = discord.utils.utcnow()
        return embed
    
    def _build_view(
        self,
        buttons: Optional[List[ButtonType]],
        custom_buttons: Optional[List[Dict[str, Any]]],
        guild_id: int,
        timeout: int
    ) -> ui.View:
        """Build a view with the specified buttons"""
        view = ui.View(timeout=timeout)

        for button_type in buttons or []:
            if button_type == ButtonType.VERIFY:
                view.add_item(EmbedVerifyButton(guild_id))
            elif button_type == ButtonType.APPLY_STAFF:
                view.add_item(EmbedApplyStaffButton(guild_id))
            elif button_type == ButtonType.CREATE_TICKET:
                view.add_item(EmbedCreateTicketButton(guild_id))

        # Add custom buttons
        for custom_config in custom_buttons or []:
            button = EmbedCustomButton(
                label=custom_config['label'],
                style=custom_config.get('style', discord.ButtonStyle.secondary),
                custom_id=custom_config['custom_id'],
                callback=custom_config.get('callback'),
                guild_id=guild_id
            )
            view.add_item(button)

        return view
    
    async def _cleanup_view_later(self, view_id: str, delay: int):
        """Clean up a view after timeout"""
        await asyncio.sleep(delay)
        if view_id in self._active_views:
            view = self._active_views[view_id]
            if not view.is_finished():
                try:
                    view.stop()
                except Exception as e:
                    logger.debug(f"Error stopping view {view_id}: {e}")
            del self._active_views[view_id]

    async def create_example_embed(self, channel: discord.TextChannel, guild_id: int) -> discord.Message:
        """
        Create an example embed with Verify, Apply Staff, and Create Ticket buttons

        Args:
            channel: The channel to send the embed to
            guild_id: The guild ID

        Returns:
            The sent message
        """
        config = EmbedConfig(
            title="Server Actions",
            description="Welcome to our server! Use the buttons below to interact with our systems.",
            color=discord.Color.blue(),
            fields=[
                {
                    "name": "Verification",
                    "value": "Click 'Verify' to get access to the rest of the server.",
                    "inline": False
                },
                {
                    "name": "Staff Applications",
                    "value": "Interested in joining our staff team? Click 'Apply Staff' to submit your application.",
                    "inline": False
                },
                {
                    "name": "Support Tickets",
                    "value": "Need help? Click 'Create Ticket' to open a support ticket.",
                    "inline": False
                }
            ],
            footer="All interactions are logged for moderation purposes",
            buttons=[
                ButtonType.VERIFY,
                ButtonType.APPLY_STAFF,
                ButtonType.CREATE_TICKET
            ]
        )

        return await self.create_embed_with_buttons(channel, config, guild_id)


# Button Classes
class EmbedVerifyButton(ui.Button):
    """Verify button for embed system"""

    def __init__(self, guild_id: int):
        super().__init__(
            label="Verify",
            style=discord.ButtonStyle.success,
            custom_id="embed_verify_button_persistent"
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message("❌ Error: Guild not found.", ephemeral=True)
                return

            # Get verification role from guild data
            guild_id = self.guild_id or guild.id
            role_id = dm.get_guild_data(guild_id, "verify_role")
            role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")
            
            if not role:
                await interaction.response.send_message("❌ Verification role not found. Please contact staff.", ephemeral=True)
                return
            
            if role in interaction.user.roles:
                await interaction.response.send_message("✅ You are already verified!", ephemeral=True)
                return

            # Handle Unverified role removal if applicable
            unverified = discord.utils.get(guild.roles, name="Unverified")
            if unverified and unverified in interaction.user.roles:
                await interaction.user.remove_roles(unverified)

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ You're verified! Enjoy the server!", ephemeral=True)
            
            logger.info(f"User {interaction.user} verified in guild {guild.id}")
            
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to assign the Verified role.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in verify button callback: {e}")
            await interaction.response.send_message("❌ An error occurred during verification.", ephemeral=True)


class EmbedApplyStaffButton(ui.Button):
    """Apply for staff button that opens a modal"""

    def __init__(self, guild_id: int):
        super().__init__(
            label="Apply Staff",
            style=discord.ButtonStyle.primary,
            custom_id="embed_apply_staff_button_persistent"
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            modal = EmbedStaffApplicationModal(self.guild_id or interaction.guild_id)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error opening staff application modal: {e}")
            await interaction.response.send_message("❌ An error occurred opening the application form.", ephemeral=True)


class EmbedCreateTicketButton(ui.Button):
    """Create ticket button for embed system"""

    def __init__(self, guild_id: int):
        super().__init__(
            label="Create Ticket",
            style=discord.ButtonStyle.primary,
            custom_id="embed_create_ticket_button_persistent"
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message("❌ Error: Guild not found.", ephemeral=True)
                return
            
            # Find ticket channel
            guild_id = self.guild_id or guild.id
            ch_id = dm.get_guild_data(guild_id, 'tickets_channel') or dm.get_guild_data(guild_id, 'ticket_queue_channel')
            channel = guild.get_channel(ch_id) if ch_id else discord.utils.get(guild.text_channels, name="ticket-queue")
            
            if not channel:
                await interaction.response.send_message("❌ Ticket channel not found. Please contact staff.", ephemeral=True)
                return

            try:
                thread = await channel.create_thread(
                    name=f"ticket-{interaction.user.display_name}",
                    type=discord.ChannelType.private_thread if guild.premium_tier >= 2 else discord.ChannelType.public_thread,
                    inviter=interaction.user
                )
                await thread.send(f"🎫 **New Ticket**\n{interaction.user.mention} has opened a ticket. Staff will be with you shortly.")
                await interaction.response.send_message(f"✅ Ticket created! Go to {thread.mention}", ephemeral=True)
                
                logger.info(f"User {interaction.user} created ticket in guild {guild.id}")
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ I lack permissions to create threads.", ephemeral=True)
            except Exception as e:
                logger.error(f"Failed to create ticket thread: {e}")
                await interaction.response.send_message("❌ Failed to create ticket thread.", ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error in create ticket button callback: {e}")
            await interaction.response.send_message("❌ An error occurred creating the ticket.", ephemeral=True)


# View Classes for Persistent Buttons
class EmbedVerifyView(ui.View):
    """Persistent view containing the verify button"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(EmbedVerifyButton(guild_id))


class EmbedApplyStaffView(ui.View):
    """Persistent view containing the apply staff button"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(EmbedApplyStaffButton(guild_id))


class EmbedCreateTicketView(ui.View):
    """Persistent view containing the create ticket button"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(EmbedCreateTicketButton(guild_id))


class EmbedCustomButton(ui.Button):
    """Custom button for extensibility"""

    def __init__(self, label: str, style: discord.ButtonStyle, custom_id: str, callback: Optional[Callable], guild_id: int):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.custom_callback = callback
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            if self.custom_callback:
                await self.custom_callback(interaction, self.guild_id)
            else:
                await interaction.response.send_message("❌ This button is not configured.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in custom button callback: {e}")
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


# Modal Classes
class EmbedStaffApplicationModal(ui.Modal):
    """Modal for staff applications"""
    
    def __init__(self, guild_id: int):
        super().__init__(title="Staff Application", timeout=600)  # 10 minutes
        self.guild_id = guild_id
        
        self.reason_input = ui.TextInput(
            label="Why do you want to be staff?",
            style=discord.TextStyle.paragraph,
            placeholder="Tell us about yourself and why you'd be a good fit...",
            required=True,
            min_length=50,
            max_length=1000
        )
        
        self.experience_input = ui.TextInput(
            label="Experience",
            style=discord.TextStyle.paragraph,
            placeholder="Any previous moderation experience? (optional)",
            required=False,
            min_length=0,
            max_length=1000
        )
        
        self.add_item(self.reason_input)
        self.add_item(self.experience_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message("❌ Error: Guild not found.", ephemeral=True)
                return
            
            # Find applications/log channel
            apps_channel = None
            
            # Try applications channel first
            apps_channel_id = dm.get_guild_data(guild.id, "applications_channel")
            if apps_channel_id:
                apps_channel = guild.get_channel(apps_channel_id)
            
            # Fallback to log channel
            if not apps_channel:
                log_channel_id = dm.get_guild_data(guild.id, "log_channel")
                if log_channel_id:
                    apps_channel = guild.get_channel(log_channel_id)
            
            # Final fallback
            if not apps_channel:
                apps_channel = discord.utils.get(guild.text_channels, name="applications")
            
            if not apps_channel:
                await interaction.response.send_message("❌ Applications channel not found. Please contact staff.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="📝 New Staff Application",
                description=f"Application from {interaction.user.mention}",
                color=discord.Color.purple()
            )
            embed.add_field(name="Reason", value=self.reason_input.value or "Not provided", inline=False)
            embed.add_field(name="Experience", value=self.experience_input.value or "Not provided", inline=False)
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            
            await apps_channel.send(embed=embed)
            await interaction.response.send_message("✅ Your application has been submitted!", ephemeral=True)
            
            logger.info(f"User {interaction.user} submitted staff application in guild {guild.id}")
            
        except Exception as e:
            logger.error(f"Error in staff application modal submit: {e}")
            await interaction.response.send_message("❌ An error occurred while submitting your application.", ephemeral=True)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in staff application modal: {error}")
        await interaction.response.send_message("❌ An error occurred while submitting your application.", ephemeral=True)
    
    async def on_timeout(self):
        # Modal timed out, no action needed
        pass
