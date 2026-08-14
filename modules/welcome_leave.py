import discord
from discord import ui
import time
from data_manager import dm
from logger import logger

class WelcomeLeaveSystem:
    """Welcome and leave message system."""

    def __init__(self, bot):
        self.bot = bot

    async def handle_member_join(self, member):
        config = dm.get_guild_data(member.guild.id, "welcome_leave_config", {})
        if not config.get("enabled", False):
            return

        channel_id = config.get("welcome_channel")
        if channel_id:
            channel = member.guild.get_channel(int(channel_id))
            if channel:
                message = config.get("welcome_message", "Welcome {user} to {server}!")
                message = message.replace("{user}", member.mention).replace("{server}", member.guild.name)

                embed = discord.Embed(
                    title="👋 Welcome!",
                    description=message,
                    color=discord.Color.green()
                )
                embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)

                try:
                    await channel.send(embed=embed)
                except:
                    pass

        # Send DM welcome
        dm_message = config.get("welcome_dm", "")
        if dm_message:
            dm_message = dm_message.replace("{user}", member.display_name).replace("{server}", member.guild.name)
            try:
                embed = discord.Embed(
                    title=f"Welcome to {member.guild.name}!",
                    description=dm_message,
                    color=discord.Color.blue()
                )
                await member.send(embed=embed, view=WelcomeDMView())
            except:
                pass

    async def handle_member_remove(self, member):
        config = dm.get_guild_data(member.guild.id, "welcome_leave_config", {})
        if not config.get("enabled", False):
            return

        channel_id = config.get("leave_channel") or config.get("welcome_channel")
        if channel_id:
            channel = member.guild.get_channel(int(channel_id))
            if channel:
                message = config.get("leave_message", "{user} has left the server.")
                message = message.replace("{user}", member.display_name)

                embed = discord.Embed(
                    description=message,
                    color=discord.Color.red()
                )

                try:
                    await channel.send(embed=embed)
                except:
                    pass

    async def handle_trigger_roles(self, message):
        """Handle trigger role keywords."""
        if message.author.bot:
            return

        config = dm.get_guild_data(message.guild.id, "trigger_roles_config", {})
        if not config.get("enabled", False):
            return

        triggers = config.get("triggers", {})
        content_lower = message.content.lower()

        for keyword, role_id in triggers.items():
            if keyword.lower() in content_lower:
                try:
                    role = message.guild.get_role(int(role_id))
                    if role and role not in message.author.roles:
                        await message.author.add_roles(role)
                        await message.channel.send(
                            f"🎭 {message.author.mention} received the {role.name} role!",
                            delete_after=5
                        )
                except:
                    pass

    def get_persistent_views(self):
        return [WelcomeDMView()]

class WelcomeDMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify Account", style=discord.ButtonStyle.success, custom_id="welcome_verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Verification system is handled in the verify channel!", ephemeral=True)

    @discord.ui.button(label="Get Roles", style=discord.ButtonStyle.primary, custom_id="welcome_roles")
    async def roles_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🎭 Use reaction roles in the server to get roles!", ephemeral=True)