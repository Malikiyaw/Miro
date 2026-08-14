import discord
from discord import ui
import datetime
import json
from data_manager import dm
from typing import Dict, Optional
from logger import logger

class StaffApplicationPersistentView(ui.View):
    """Persistent view for the 'Apply Now' button."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(label="Apply Now", style=discord.ButtonStyle.success, custom_id="staff_apply_now")
    async def apply_now(self, interaction: discord.Interaction, button: ui.Button):
        # 30-day cooldown check
        apps = dm.load_json("applications", default={})
        last_app = apps.get(str(interaction.user.id))
        if last_app and (datetime.datetime.now() - datetime.datetime.fromisoformat(last_app['timestamp'])).days < 30:
            return await interaction.response.send_message("[ERROR] You can only apply once every 30 days.", ephemeral=True)
        
        await interaction.response.send_modal(StaffApplicationModal(self.bot))

class StaffReviewPersistentView(ui.View):
    """Persistent view for staff logs (Approve/Deny)."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="staff_approve_app")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        embed = interaction.message.embeds[0]
        try:
            applicant_id = int(embed.fields[0].value)
        except (IndexError, ValueError, AttributeError) as e:
            logger.error("Failed to parse applicant ID: %s", e)
            return await interaction.followup.send("[ERROR] Could not identify applicant.", ephemeral=True)

        guild = interaction.guild
        applicant = guild.get_member(applicant_id)
        if applicant:
            try:
                await applicant.send(f"[AI] Your staff application for {guild.name} has been approved!")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.debug("Could not DM applicant: %s", e)
        
        await interaction.message.edit(content=f"[SUCCESS] Approved by {interaction.user.name}", view=None)
        
        # Store in applications.json
        apps = dm.load_json("applications", default={})
        apps[str(applicant_id)] = {"status": "approved", "timestamp": str(datetime.datetime.now())}
        dm.save_json("applications", apps)

    @ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="staff_deny_app")
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        embed = interaction.message.embeds[0]
        try:
            applicant_id = int(embed.fields[0].value)
        except (IndexError, ValueError, AttributeError) as e:
            logger.error("Failed to parse applicant ID: %s", e)
            return await interaction.followup.send("[ERROR] Could not identify applicant.", ephemeral=True)

        guild = interaction.guild
        applicant = guild.get_member(applicant_id)
        if applicant:
            try:
                await applicant.send(f"[AI] Your staff application for {guild.name} has been denied.")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.debug("Could not DM applicant: %s", e)
        
        await interaction.message.edit(content=f"[ERROR] Denied by {interaction.user.name}", view=None)
        
        apps = dm.load_json("applications", default={})
        apps[str(applicant_id)] = {"status": "denied", "timestamp": str(datetime.datetime.now())}
        dm.save_json("applications", apps)

class StaffApplicationModal(ui.Modal):
    def __init__(self, bot):
        super().__init__(title='Staff Application')
        self.bot = bot
        
    q1 = ui.TextInput(label='Why do you want to be staff?', style=discord.TextStyle.paragraph, min_length=20, max_length=1000)
    q2 = ui.TextInput(label='What experience do you have?', style=discord.TextStyle.paragraph, min_length=10, max_length=1000)
    q3 = ui.TextInput(label='Weekly Activity (hours)?', placeholder='e.g. 15-20 hours', min_length=1, max_length=50)
    q4 = ui.TextInput(label='What skills do you bring?', style=discord.TextStyle.paragraph, min_length=10, max_length=1000)
    q5 = ui.TextInput(label='Anything else?', required=False, style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        log_channel_id = dm.get_guild_data(guild_id, "staff_log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id)

        if not log_channel:
            return await interaction.followup.send("[ERROR] Staff logs channel not found. Please contact an admin.", ephemeral=True)

        embed = discord.Embed(title=f"New Staff Application: {interaction.user.name}", color=discord.Color.gold())
        embed.add_field(name="User ID", value=interaction.user.id)
        embed.add_field(name="1. Why?", value=self.q1.value, inline=False)
        embed.add_field(name="2. Experience", value=self.q2.value, inline=False)
        embed.add_field(name="3. Activity", value=self.q3.value, inline=False)
        embed.add_field(name="4. Skills", value=self.q4.value, inline=False)
        embed.add_field(name="5. Extra", value=self.q5.value or "N/A", inline=False)

        # Use the Persistent Review View
        view = StaffReviewPersistentView()
        await log_channel.send(embed=embed, view=view)
        await interaction.followup.send("[SUCCESS] Staff application submitted! We will DM you shortly.", ephemeral=True)

class StaffSystem:
    def __init__(self, bot):
        self.bot = bot

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # 1. Create Channels
        public_channel = await guild.create_text_channel("apply-staff")
        log_channel = await guild.create_text_channel("apply-staff-logs", overwrites={
            guild.default_role: discord.PermissionOverwrite(read_messages=False)
        })

        dm.update_guild_data(guild.id, "staff_log_channel_id", log_channel.id)

        # 2. Public Panel with Persistent View
        embed = discord.Embed(title="Join Our Staff Team", description="Click below to apply for a staff position!", color=discord.Color.green())
        view = StaffApplicationPersistentView(self.bot)
        await public_channel.send(embed=embed, view=view)

        # 3. Auto-Documentation
        help_embed = discord.Embed(title="Apply Staff System Help", description="Information about staff application system.", color=discord.Color.blue())
        help_embed.add_field(name="!apply status", value="Checks the status of your application.")
        help_embed.add_field(name="!help staffapply", value="Shows this help embed.")
        await public_channel.send(embed=help_embed)
        
        # 4. Register Custom Commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["apply"] = json.dumps({"command_type": "application_status"})
        custom_cmds["help staffapply"] = json.dumps({
            "command_type": "help_embed",
            "title": "Apply Staff System Help",
            "description": "Information about staff application system.",
            "fields": [
                {"name": "!apply status", "value": "Checks the status of your application.", "inline": False},
                {"name": "!help staffapply", "value": "Shows this help embed.", "inline": False}
            ]
        })
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        return True
