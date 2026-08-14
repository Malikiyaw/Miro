import discord
from discord import ui
import time
import random
import string
from typing import Dict, List, Any, Optional
from data_manager import dm
from logger import logger

class VerificationSystem:
    """
    Complete verification system with CAPTCHA, role assignment, and anti-alt measures.
    Features:
    - CAPTCHA verification
    - Automatic role assignment/removal
    - Account age checks
    - Unverified role management
    - Verification logging
    """

    def __init__(self, bot):
        self.bot = bot
        self.pending_verifications = {}  # user_id -> captcha_code

    # Event handlers
    async def handle_member_join(self, member):
        """Handle new member joins."""
        config = dm.get_guild_data(member.guild.id, "verification_config", {})
        if not config.get("enabled", False):
            return

        # Check account age
        min_age_days = config.get("min_account_age_days", 0)
        if min_age_days > 0:
            account_age = (discord.utils.utcnow() - member.created_at).days
            if account_age < min_age_days:
                # Kick or assign unverified role
                if config.get("kick_new_accounts", False):
                    try:
                        await member.kick(reason=f"Account too new ({account_age} days old)")
                        return
                    except:
                        pass

        # Assign unverified role
        unverified_role_id = config.get("unverified_role")
        if unverified_role_id:
            try:
                role = member.guild.get_role(int(unverified_role_id))
                if role:
                    await member.add_roles(role)
            except Exception as e:
                logger.error(f"Failed to assign unverified role: {e}")

        # Send verification instructions
        await self.send_verification_message(member)

    async def send_verification_message(self, member):
        """Send verification message to new member."""
        config = dm.get_guild_data(member.guild.id, "verification_config", {})

        # Try to DM first
        try:
            embed = discord.Embed(
                title="🔐 Verification Required",
                description="Welcome to the server! Please verify yourself to gain full access.",
                color=discord.Color.blue()
            )

            embed.add_field(
                name="How to Verify",
                value="Click the **Verify Me** button below to complete CAPTCHA verification.",
                inline=False
            )

            embed.set_footer(text="Verification helps keep our server safe!")

            # Create verification view
            view = VerificationView(self, member.guild.id)
            await member.send(embed=embed, view=view)

        except discord.Forbidden:
            # Can't DM, try to send in verification channel
            verify_channel_id = config.get("verify_channel")
            if verify_channel_id:
                try:
                    channel = member.guild.get_channel(int(verify_channel_id))
                    if channel:
                        embed.description += f"\n\n{member.mention}, please verify here:"
                        await channel.send(embed=embed, view=VerificationView(self, member.guild.id))
                except:
                    pass

    # Verification process
    async def start_verification(self, interaction):
        """Start CAPTCHA verification process."""
        config = dm.get_guild_data(interaction.guild.id, "verification_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Verification system is disabled.", ephemeral=True)

        # Generate CAPTCHA
        captcha_code = self.generate_captcha()
        self.pending_verifications[interaction.user.id] = {
            "code": captcha_code,
            "timestamp": time.time(),
            "guild_id": interaction.guild.id
        }

        # Send CAPTCHA modal
        modal = CaptchaModal(self, captcha_code)
        await interaction.response.send_modal(modal)

    async def complete_verification(self, interaction, user_code: str):
        """Complete verification process."""
        user_id = interaction.user.id

        if user_id not in self.pending_verifications:
            return await interaction.response.send_message("❌ No active verification found.", ephemeral=True)

        verification = self.pending_verifications[user_id]
        correct_code = verification["code"]

        # Check if expired (5 minutes)
        if time.time() - verification["timestamp"] > 300:
            del self.pending_verifications[user_id]
            return await interaction.response.send_message("❌ Verification expired. Please try again.", ephemeral=True)

        # Check code
        if user_code.upper() != correct_code:
            return await interaction.response.send_message("❌ Incorrect code. Please try again.", ephemeral=True)

        # Verification successful
        guild_id = verification["guild_id"]
        config = dm.get_guild_data(guild_id, "verification_config", {})

        try:
            # Remove unverified role
            unverified_role_id = config.get("unverified_role")
            if unverified_role_id:
                role = interaction.guild.get_role(int(unverified_role_id))
                if role and role in interaction.user.roles:
                    await interaction.user.remove_roles(role)

            # Add verified role
            verified_role_id = config.get("verified_role")
            if verified_role_id:
                role = interaction.guild.get_role(int(verified_role_id))
                if role and role not in interaction.user.roles:
                    await interaction.user.add_roles(role)

            # Log verification
            logger.info(f"User {user_id} verified in guild {guild_id}")

            # Clean up
            del self.pending_verifications[user_id]

            await interaction.response.send_message("✅ Successfully verified! Welcome to the server!", ephemeral=True)

        except Exception as e:
            logger.error(f"Verification completion error: {e}")
            await interaction.response.send_message("❌ Verification failed. Please contact staff.", ephemeral=True)

    def generate_captcha(self) -> str:
        """Generate a simple text CAPTCHA."""
        # Generate 6 character alphanumeric code
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choice(chars) for _ in range(6))

    # Config panel
    def get_config_panel(self, guild_id: int):
        """Get verification config panel."""
        return VerificationConfigPanel(self.bot, guild_id)

    def get_persistent_views(self):
        """Get persistent views for verification buttons."""
        return [VerificationView(self, 0)]  # Guild ID determined at runtime

class VerificationView(discord.ui.View):
    """Persistent view for verification buttons."""

    def __init__(self, verification_system, guild_id: int):
        super().__init__(timeout=None)
        self.verification = verification_system
        self.guild_id = guild_id

    @discord.ui.button(label="Verify Me", style=discord.ButtonStyle.success, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.verification.start_verification(interaction)

class CaptchaModal(discord.ui.Modal, title="Verify Yourself"):
    """CAPTCHA verification modal."""

    captcha_input = discord.ui.TextInput(
        label="Enter the code shown above",
        placeholder="ABC123",
        max_length=6,
        min_length=6
    )

    def __init__(self, verification_system, captcha_code: str):
        super().__init__()
        self.verification = verification_system
        self.captcha_code = captcha_code

        # Add CAPTCHA display
        self.captcha_display = discord.ui.TextInput(
            label="CAPTCHA Code (copy this)",
            default=captcha_code,
            style=discord.TextStyle.short
        )
        self.add_item(self.captcha_display)
        self.add_item(self.captcha_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.verification.complete_verification(interaction, self.captcha_input.value)

class VerificationConfigPanel(discord.ui.View):
    """Config panel for verification system."""

    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.verification = VerificationSystem(bot)

    @discord.ui.button(label="Toggle Verification", style=discord.ButtonStyle.primary, row=0)
    async def toggle_verification(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "verification_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "verification_config", config)

        await interaction.response.send_message(
            f"✅ Verification system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Set Verified Role", style=discord.ButtonStyle.secondary, row=0)
    async def set_verified_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetVerifiedRoleModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Unverified Role", style=discord.ButtonStyle.secondary, row=1)
    async def set_unverified_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetUnverifiedRoleModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Verification Channel", style=discord.ButtonStyle.secondary, row=1)
    async def set_verify_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetVerifyChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Account Age Check", style=discord.ButtonStyle.secondary, row=2)
    async def set_account_age(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetAccountAgeModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class SetVerifiedRoleModal(discord.ui.Modal, title="Set Verified Role"):
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

            config = dm.get_guild_data(self.guild_id, "verification_config", {})
            config["verified_role"] = str(role_id)
            dm.update_guild_data(self.guild_id, "verification_config", config)

            await interaction.response.send_message(f"✅ Verified role set to {role.name}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid role ID", ephemeral=True)

class SetUnverifiedRoleModal(discord.ui.Modal, title="Set Unverified Role"):
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

            config = dm.get_guild_data(self.guild_id, "verification_config", {})
            config["unverified_role"] = str(role_id)
            dm.update_guild_data(self.guild_id, "verification_config", config)

            await interaction.response.send_message(f"✅ Unverified role set to {role.name}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid role ID", ephemeral=True)

class SetVerifyChannelModal(discord.ui.Modal, title="Set Verification Channel"):
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

            config = dm.get_guild_data(self.guild_id, "verification_config", {})
            config["verify_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "verification_config", config)

            await interaction.response.send_message(f"✅ Verification channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)

class SetAccountAgeModal(discord.ui.Modal, title="Set Account Age Check"):
    min_age = discord.ui.TextInput(label="Minimum Account Age (days)", placeholder="7")
    kick_new = discord.ui.TextInput(label="Kick new accounts? (yes/no)", placeholder="no")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            min_age = int(self.min_age.value)
            kick_new = self.kick_new.value.lower() in ['yes', 'y', 'true', '1']

            if min_age < 0:
                raise ValueError

            config = dm.get_guild_data(self.guild_id, "verification_config", {})
            config["min_account_age_days"] = min_age
            config["kick_new_accounts"] = kick_new
            dm.update_guild_data(self.guild_id, "verification_config", config)

            action = "kick" if kick_new else "assign unverified role to"
            await interaction.response.send_message(f"✅ Will {action} accounts younger than {min_age} days", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number", ephemeral=True)


class VerifyView(VerificationView):
    """Compatibility facade used by actions.py — guild ID resolved at runtime."""

    def __init__(self, verification_system, guild_id: int = 0):
        super().__init__(verification_system, guild_id)


class Verification(VerificationSystem):
    """Message-based compatibility facade used by actions.py custom-command handlers."""

    async def set_verify_channel(self, message, args=None):
        """!verify set #channel — set the verification channel."""
        if not message.author.guild_permissions.administrator:
            return await message.channel.send("❌ Only administrators can change the verification channel.")

        channel = None
        if message.channel_mentions:
            channel = message.channel_mentions[0]
        elif len(args or []) > 2:
            try:
                channel = message.guild.get_channel(int(args[2].strip("<#")))
            except ValueError:
                channel = None

        if not channel or not isinstance(channel, discord.TextChannel):
            return await message.channel.send("❌ Usage: `!verify set #channel`")

        config = dm.get_guild_data(message.guild.id, "verification_config", {})
        config["verify_channel"] = str(channel.id)
        dm.update_guild_data(message.guild.id, "verification_config", config)
        await message.channel.send(f"✅ Verification channel set to {channel.mention}")