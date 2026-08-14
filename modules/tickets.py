import discord
from discord import ui
import time
import asyncio
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class TicketSystem:
    """
    Complete ticket system with private channels, staff controls, and transcripts.
    Features:
    - Private ticket channels
    - Staff assignment
    - Ticket controls (close, add user, transcript)
    - Category organization
    - Ticket logging
    - Auto-close inactive tickets
    """

    def __init__(self, bot):
        self.bot = bot
        self.active_tickets = {}  # ticket_id -> ticket_data

    # Ticket creation
    async def create_ticket(self, interaction, reason: str = None):
        """Create a new support ticket."""
        config = dm.get_guild_data(interaction.guild.id, "tickets_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Ticket system is disabled.", ephemeral=True)

        # Check if user already has an open ticket
        user_tickets = dm.get_guild_data(interaction.guild.id, "user_tickets", {})
        if str(interaction.user.id) in user_tickets:
            ticket_id = user_tickets[str(interaction.user.id)]
            if ticket_id in self.active_tickets:
                return await interaction.response.send_message("❌ You already have an open ticket.", ephemeral=True)

        # Defer response
        await interaction.response.defer(ephemeral=True)

        try:
            # Create ticket channel
            ticket_id = self.generate_ticket_id()
            channel_name = f"ticket-{ticket_id}"

            # Get ticket category
            category_id = config.get("ticket_category")
            category = None
            if category_id:
                category = interaction.guild.get_channel(int(category_id))

            # Create channel
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }

            # Add staff roles
            staff_roles = config.get("staff_roles", [])
            for role_id in staff_roles:
                try:
                    role = interaction.guild.get_role(int(role_id))
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                except:
                    pass

            channel = await interaction.guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites
            )

            # Store ticket data
            ticket_data = {
                "id": ticket_id,
                "channel_id": channel.id,
                "user_id": interaction.user.id,
                "guild_id": interaction.guild.id,
                "reason": reason or "No reason provided",
                "created_at": time.time(),
                "status": "open",
                "staff_assigned": [],
                "messages": []
            }

            self.active_tickets[ticket_id] = ticket_data
            user_tickets[str(interaction.user.id)] = ticket_id
            dm.update_guild_data(interaction.guild.id, "user_tickets", user_tickets)

            # Send welcome message
            embed = discord.Embed(
                title=f"🎫 Ticket #{ticket_id}",
                description=f"Support ticket created by {interaction.user.mention}",
                color=discord.Color.blue()
            )

            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)

            embed.add_field(
                name="Staff Controls",
                value="Use the buttons below to manage this ticket.",
                inline=False
            )

            embed.set_footer(text="Click close when the issue is resolved")

            view = TicketControlView(self, ticket_id)
            await channel.send(embed=embed, view=view)

            # Confirm to user
            await interaction.followup.send(
                f"✅ Ticket created! Please check {channel.mention}",
                ephemeral=True
            )

            # Log ticket creation
            logger.info(f"Ticket {ticket_id} created by user {interaction.user.id} in guild {interaction.guild.id}")

        except Exception as e:
            logger.error(f"Failed to create ticket: {e}")
            await interaction.followup.send("❌ Failed to create ticket. Please try again.", ephemeral=True)

    async def close_ticket(self, interaction, ticket_id: str):
        """Close a ticket."""
        if ticket_id not in self.active_tickets:
            return await interaction.response.send_message("❌ Ticket not found.", ephemeral=True)

        ticket = self.active_tickets[ticket_id]

        # Check permissions
        is_staff = self.is_staff_member(interaction.guild, interaction.user)
        is_owner = ticket["user_id"] == interaction.user.id

        if not (is_staff or is_owner):
            return await interaction.response.send_message("❌ You don't have permission to close this ticket.", ephemeral=True)

        await interaction.response.defer()

        try:
            # Generate transcript
            transcript = await self.generate_transcript(ticket)

            # Send transcript to log channel
            log_channel_id = dm.get_guild_data(interaction.guild.id, "tickets_config", {}).get("log_channel")
            if log_channel_id:
                try:
                    log_channel = interaction.guild.get_channel(int(log_channel_id))
                    if log_channel:
                        await log_channel.send(embed=transcript)
                except:
                    pass

            # Close ticket
            ticket["status"] = "closed"
            ticket["closed_at"] = time.time()
            ticket["closed_by"] = interaction.user.id

            # Save to closed tickets
            closed_tickets = dm.get_guild_data(interaction.guild.id, "closed_tickets", [])
            closed_tickets.append(ticket)
            dm.update_guild_data(interaction.guild.id, "closed_tickets", closed_tickets[-100:])  # Keep last 100

            # Clean up active tickets
            del self.active_tickets[ticket_id]
            user_tickets = dm.get_guild_data(interaction.guild.id, "user_tickets", {})
            if str(ticket["user_id"]) in user_tickets:
                del user_tickets[str(ticket["user_id"])]
            dm.update_guild_data(interaction.guild.id, "user_tickets", user_tickets)

            # Delete or archive channel
            channel = interaction.guild.get_channel(ticket["channel_id"])
            if channel:
                try:
                    # Option to delete or rename
                    config = dm.get_guild_data(interaction.guild.id, "tickets_config", {})
                    if config.get("delete_closed_tickets", False):
                        await channel.delete()
                    else:
                        await channel.edit(name=f"closed-{ticket_id}")
                        await channel.send("🔒 **Ticket Closed** - This channel will be deleted in 24 hours.")

                        # Schedule deletion
                        await asyncio.sleep(86400)  # 24 hours
                        try:
                            await channel.delete()
                        except:
                            pass
                except Exception as e:
                    logger.error(f"Failed to close ticket channel: {e}")

            await interaction.followup.send("✅ Ticket closed successfully!", ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to close ticket: {e}")
            await interaction.followup.send("❌ Failed to close ticket.", ephemeral=True)

    async def add_user_to_ticket(self, interaction, ticket_id: str, user: discord.Member):
        """Add a user to a ticket."""
        if ticket_id not in self.active_tickets:
            return await interaction.response.send_message("❌ Ticket not found.", ephemeral=True)

        if not self.is_staff_member(interaction.guild, interaction.user):
            return await interaction.response.send_message("❌ Only staff can add users to tickets.", ephemeral=True)

        ticket = self.active_tickets[ticket_id]
        channel = interaction.guild.get_channel(ticket["channel_id"])

        if not channel:
            return await interaction.response.send_message("❌ Ticket channel not found.", ephemeral=True)

        try:
            await channel.set_permissions(user, read_messages=True, send_messages=True)
            await interaction.response.send_message(f"✅ Added {user.mention} to the ticket.", ephemeral=True)

            # Log in ticket channel
            await channel.send(f"👤 {interaction.user.mention} added {user.mention} to this ticket.")

        except Exception as e:
            logger.error(f"Failed to add user to ticket: {e}")
            await interaction.response.send_message("❌ Failed to add user to ticket.", ephemeral=True)

    async def generate_transcript(self, ticket: dict) -> discord.Embed:
        """Generate a transcript embed for the ticket."""
        channel = self.bot.get_channel(ticket["channel_id"])
        if not channel:
            # Channel deleted, create basic transcript
            embed = discord.Embed(
                title=f"🎫 Ticket #{ticket['id']} Transcript",
                description="Channel was deleted - limited transcript available",
                color=discord.Color.red()
            )
        else:
            # Fetch messages
            messages = []
            try:
                async for message in channel.history(limit=100, oldest_first=True):
                    messages.append(message)
            except:
                pass

            embed = discord.Embed(
                title=f"🎫 Ticket #{ticket['id']} Transcript",
                description=f"Ticket created by <@{ticket['user_id']}>",
                color=discord.Color.blue()
            )

            if messages:
                transcript_text = ""
                for msg in messages[-20:]:  # Last 20 messages
                    timestamp = msg.created_at.strftime("%H:%M")
                    author = msg.author.display_name
                    content = msg.content[:100]  # Truncate long messages
                    transcript_text += f"[{timestamp}] {author}: {content}\n"

                embed.add_field(
                    name="Recent Messages",
                    value=f"```\n{transcript_text[:1000]}```",
                    inline=False
                )

        # Add ticket info
        embed.add_field(name="Reason", value=ticket.get("reason", "No reason"), inline=True)
        embed.add_field(name="Created", value=time.strftime("%Y-%m-%d %H:%M", time.localtime(ticket["created_at"])), inline=True)
        embed.add_field(name="Status", value=ticket.get("status", "unknown"), inline=True)

        return embed

    def generate_ticket_id(self) -> str:
        """Generate a unique ticket ID."""
        import random
        import string

        while True:
            ticket_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if ticket_id not in self.active_tickets:
                return ticket_id

    def is_staff_member(self, guild: discord.Guild, user: discord.Member) -> bool:
        """Check if user is a staff member."""
        config = dm.get_guild_data(guild.id, "tickets_config", {})
        staff_roles = config.get("staff_roles", [])

        return any(role.id in [int(r) for r in staff_roles] for role in user.roles) or user.guild_permissions.administrator

    # Config panel
    def get_config_panel(self, guild_id: int):
        """Get tickets config panel."""
        return TicketsConfigPanel(self.bot, guild_id)

    def get_persistent_views(self):
        """Get persistent views for ticket buttons."""
        return [TicketControlView(self, "")]  # Ticket ID determined at runtime

class TicketControlView(discord.ui.View):
    """Control panel for active tickets."""

    def __init__(self, ticket_system, ticket_id: str):
        super().__init__(timeout=None)
        self.ticket_system = ticket_system
        self.ticket_id = ticket_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.ticket_system.close_ticket(interaction, self.ticket_id)

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.secondary, emoji="👤", custom_id="add_user")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddUserModal(self.ticket_system, self.ticket_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.primary, emoji="📄", custom_id="transcript")
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ticket_id not in self.ticket_system.active_tickets:
            return await interaction.response.send_message("❌ Ticket not found.", ephemeral=True)

        ticket = self.ticket_system.active_tickets[self.ticket_id]
        transcript_embed = await self.ticket_system.generate_transcript(ticket)
        await interaction.response.send_message(embed=transcript_embed, ephemeral=True)

class AddUserModal(discord.ui.Modal, title="Add User to Ticket"):
    user_id = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")

    def __init__(self, ticket_system, ticket_id):
        super().__init__()
        self.ticket_system = ticket_system
        self.ticket_id = ticket_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_id.value.strip("<@!>"))
            user = interaction.guild.get_member(user_id)

            if not user:
                return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

            await self.ticket_system.add_user_to_ticket(interaction, self.ticket_id, user)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid user ID or mention.", ephemeral=True)

class TicketsConfigPanel(discord.ui.View):
    """Config panel for ticket system."""

    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.tickets = TicketSystem(bot)

    @discord.ui.button(label="Toggle Tickets", style=discord.ButtonStyle.primary, row=0)
    async def toggle_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "tickets_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "tickets_config", config)

        await interaction.response.send_message(
            f"✅ Ticket system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Set Ticket Category", style=discord.ButtonStyle.secondary, row=0)
    async def set_ticket_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetTicketCategoryModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Staff Role", style=discord.ButtonStyle.success, row=1)
    async def add_staff_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddStaffRoleModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.secondary, row=1)
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetLogChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View Active Tickets", style=discord.ButtonStyle.primary, row=2)
    async def view_active_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        active_count = len(self.tickets.active_tickets)
        embed = discord.Embed(
            title="🎫 Active Tickets",
            description=f"There are currently {active_count} open tickets.",
            color=discord.Color.blue()
        )

        if active_count > 0:
            ticket_list = ""
            for ticket_id, ticket in list(self.tickets.active_tickets.items())[:10]:
                try:
                    user = self.bot.get_user(ticket["user_id"])
                    name = user.display_name if user else f"User {ticket['user_id']}"
                    ticket_list += f"• **#{ticket_id}** - {name} - {ticket.get('reason', 'No reason')}\n"
                except:
                    pass

            if ticket_list:
                embed.add_field(name="Open Tickets", value=ticket_list[:1000], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

class SetTicketCategoryModal(discord.ui.Modal, title="Set Ticket Category"):
    category_id = discord.ui.TextInput(label="Category ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            category_id = int(self.category_id.value)
            category = interaction.guild.get_channel(category_id)

            if not category or not isinstance(category, discord.CategoryChannel):
                return await interaction.response.send_message("❌ Category not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "tickets_config", {})
            config["ticket_category"] = str(category_id)
            dm.update_guild_data(self.guild_id, "tickets_config", config)

            await interaction.response.send_message(f"✅ Ticket category set to {category.name}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid category ID", ephemeral=True)

class AddStaffRoleModal(discord.ui.Modal, title="Add Staff Role"):
    role_id = discord.ui.TextInput(label="Role ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_id = int(self.role_id.value)
            role = interaction.guild.get_role(role_id)

            if not role:
                return await interaction.response.send_message("❌ Role not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "tickets_config", {})
            staff_roles = config.get("staff_roles", [])
            if str(role_id) not in staff_roles:
                staff_roles.append(str(role_id))
                config["staff_roles"] = staff_roles
                dm.update_guild_data(self.guild_id, "tickets_config", config)

            await interaction.response.send_message(f"✅ Added {role.name} as staff role", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid role ID", ephemeral=True)

class SetLogChannelModal(discord.ui.Modal, title="Set Ticket Log Channel"):
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

            config = dm.get_guild_data(self.guild_id, "tickets_config", {})
            config["log_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "tickets_config", config)

            await interaction.response.send_message(f"✅ Ticket log channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)