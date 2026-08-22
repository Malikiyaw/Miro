import discord
from discord import ui, app_commands
import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from data_manager import dm
from logger import logger

class SetupState(Enum):
    PENDING = "pending"
    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"

@dataclass
class ServerSetup:
    guild_id: int
    state: SetupState
    started_at: float
    completed_at: Optional[float]
    steps_completed: List[str]
    config: dict
    selected_systems: Optional[List[str]] = None

class SystemCategory:
    """
    The 10 consolidated setup groups. Each group installs its underlying
    systems (all original functionality preserved underneath).
    """
    MEMBER_MANAGEMENT = {"name": "👤 Member Management", "emoji": "👤", "group_key": "member_management",
                         "systems": ["verification", "welcome_leave"]}
    PROGRESSION = {"name": "💰 Progression", "emoji": "💰", "group_key": "progression",
                   "systems": ["economy", "leveling"]}
    TICKETS = {"name": "🎫 Tickets", "emoji": "🎫", "group_key": "tickets",
               "systems": ["tickets"]}
    SUGGESTIONS = {"name": "💡 Suggestions", "emoji": "💡", "group_key": "suggestions",
                   "systems": ["suggestions"]}
    GIVEAWAYS = {"name": "🎁 Giveaways", "emoji": "🎁", "group_key": "giveaways",
                 "systems": ["giveaways"]}
    COMMUNICATIONS = {"name": "📢 Communications", "emoji": "📢", "group_key": "communications",
                      "systems": ["announcements", "reminders"]}
    ANTI_RAID = {"name": "🛡️ Anti-Raid", "emoji": "🛡️", "group_key": "anti_raid",
                 "systems": ["anti_raid"]}
    MODERATION = {"name": "🔨 Moderation", "emoji": "🔨", "group_key": "moderation",
                  "systems": ["automod", "warnings"]}
    AUTOMATION = {"name": "⚙️ Automation", "emoji": "⚙️", "group_key": "automation",
                  "systems": ["auto_responder", "reaction_roles"]}
    STAFF_MANAGEMENT = {"name": "👮 Staff Management", "emoji": "👮", "group_key": "staff_management",
                        "systems": ["staff_shifts", "staff_reviews"]}

    @classmethod
    def get_all_categories(cls):
        return [cls.MEMBER_MANAGEMENT, cls.PROGRESSION, cls.TICKETS, cls.SUGGESTIONS,
                cls.GIVEAWAYS, cls.COMMUNICATIONS, cls.ANTI_RAID, cls.MODERATION,
                cls.AUTOMATION, cls.STAFF_MANAGEMENT]

    @classmethod
    def find_by_key(cls, group_key: str):
        for category in cls.get_all_categories():
            if category["group_key"] == group_key:
                return category
        return None

class AutoSetupSystem:
    """
    Complete auto-setup system that installs and configures all bot systems.
    Features:
    - Interactive system selection
    - Automatic channel/role creation
    - System configuration
    - Progress tracking
    - Resume interrupted setups
    """

    def __init__(self, bot):
        self.bot = bot

    async def start_setup(self, interaction):
        """Start the auto-setup process."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only administrators can use auto-setup.", ephemeral=True)

        # Check if setup already completed
        completed = dm.load_json("completed_setups", default={})
        if str(interaction.guild.id) in completed:
            return await interaction.response.send_message("✅ This server has already been set up!", ephemeral=True)

        embed = discord.Embed(
            title="🤖 Miro Bot Auto-Setup",
            description="Welcome to the automated setup wizard! This will configure all bot systems for your server.\n\n**What will be created:**\n• Roles and channels for each system\n• Default configurations\n• Permission settings\n\n⚠️ This process may take several minutes.",
            color=discord.Color.blue()
        )

        view = SetupStartView(self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def begin_system_selection(self, interaction):
        """Show system category selection."""
        embed = discord.Embed(
            title="🎯 Select Systems to Install",
            description="Choose which systems you want to set up. You can select entire categories or individual systems.\n\n**Recommended for most servers:** Security, Moderation, and Automation categories.",
            color=discord.Color.blue()
        )

        view = CategorySelectView(self, interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def show_category_systems(self, interaction, category):
        """Show systems within a category for selection."""
        embed = discord.Embed(
            title=f"{category['emoji']} {category['name']}",
            description="Select the systems you want to install:",
            color=discord.Color.blue()
        )

        for system in category["systems"]:
            embed.add_field(
                name=f"🔧 {system.replace('_', ' ').title()}",
                value=self.get_system_description(system),
                inline=False
            )

        view = SystemSelectView(self, interaction.guild.id, category)
        await interaction.response.edit_message(embed=embed, view=view)

    def get_system_description(self, system: str) -> str:
        """Get description for a system."""
        descriptions = {
            "verification": "CAPTCHA verification to prevent bots",
            "anti_raid": "Automatic raid detection, lockdown, and emergency controls",
            "guardian": "Bot token detection and removal",
            "automod": "Automated message moderation (13 rule types)",
            "auto_mod": "Automated message moderation",
            "warnings": "User warning and punishment system",
            "economy": "Coins, shop, and gambling system",
            "leveling": "XP and role rewards system",
            "giveaways": "Automated giveaway management",
            "gamification": "Fun games and challenges",
            "starboard": "Popular message highlighting",
            "mod_logging": "Moderation action logging",
            "logging": "General server event logging",
            "modmail": "Private staff messaging",
            "suggestions": "Community suggestion voting",
            "staff_promo": "Staff promotion management",
            "staff_shifts": "Staff shift tracking",
            "staff_reviews": "Staff performance reviews",
            "applications": "Staff application system",
            "appeals": "Punishment appeal system",
            "welcome_leave": "Welcome/leave messages",
            "tickets": "Support ticket system",
            "reminders": "Scheduled reminders",
            "announcements": "Announcement management",
            "auto_responder": "Automated keyword responses",
            "reaction_roles": "Role assignment via reactions",
            "reaction_menus": "Interactive reaction menus",
            "role_buttons": "Role buttons for self-assignment",
            "ai_chat": "AI-powered chat channels"
        }
        return descriptions.get(system, "System functionality")

    async def start_installation(self, interaction, selected_systems):
        """Begin system installation."""
        embed = discord.Embed(
            title="⚙️ Installing Systems...",
            description=f"Setting up {len(selected_systems)} systems. This may take a few minutes.\n\n**Installing:** {', '.join(selected_systems[:5])}{'...' if len(selected_systems) > 5 else ''}",
            color=discord.Color.orange()
        )

        await interaction.response.edit_message(embed=embed, view=None)

        # Create setup tracking
        setup_data = ServerSetup(
            guild_id=interaction.guild.id,
            state=SetupState.STARTED,
            started_at=time.time(),
            completed_at=None,
            steps_completed=[],
            config={},
            selected_systems=selected_systems
        )

        # Save setup state
        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups[str(interaction.guild.id)] = {
            "user_id": interaction.user.id,
            "selected_systems": selected_systems,
            "started_at": time.time(),
            "channel_id": interaction.channel.id,
            "actions_taken": []
        }
        dm.save_json("pending_setups", pending_setups)

        # Install systems
        success = await self.install_systems(interaction.guild, selected_systems, interaction.user, interaction.channel)

        if success:
            # Mark as completed
            completed_setups = dm.load_json("completed_setups", default={})
            completed_setups[str(interaction.guild.id)] = {
                "completed_at": time.time(),
                "systems_installed": selected_systems,
                "installed_by": interaction.user.id
            }
            dm.save_json("completed_setups", completed_setups)

            # Clean up pending
            if str(interaction.guild.id) in pending_setups:
                del pending_setups[str(interaction.guild.id)]
                dm.save_json("pending_setups", pending_setups)

            embed = discord.Embed(
                title="✅ Setup Complete!",
                description=f"Successfully installed {len(selected_systems)} systems!\n\n**Next steps:**\n• Use `/configpanel` to customize settings\n• Check the created channels\n• Test the systems with sample commands",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Setup Failed",
                description="Some systems may not have installed correctly. Please check the logs and try again.",
                color=discord.Color.red()
            )

        try:
            await interaction.edit_original_response(embed=embed)
        except:
            pass

    async def install_systems(self, guild, systems, user, channel) -> bool:
        """Install all selected systems."""
        success_count = 0

        for system in systems:
            try:
                await channel.send(f"🔧 Installing {system.replace('_', ' ').title()}...")

                if system == "verification":
                    success = await self.setup_verification(guild, user)
                elif system == "economy":
                    success = await self.setup_economy(guild, user)
                elif system == "leveling":
                    success = await self.setup_leveling(guild, user)
                elif system == "tickets":
                    success = await self.setup_tickets(guild, user)
                elif system == "welcome_leave":
                    success = await self.setup_welcome(guild, user)
                else:
                    # Generic setup for other systems
                    success = await self.setup_generic_system(guild, system, user)

                if success:
                    success_count += 1
                    await channel.send(f"✅ {system.replace('_', ' ').title()} installed!")
                else:
                    await channel.send(f"⚠️ {system.replace('_', ' ').title()} had issues during setup.")

                await asyncio.sleep(1)  # Rate limiting

            except Exception as e:
                logger.error(f"Failed to install {system}: {e}")
                await channel.send(f"❌ Failed to install {system.replace('_', ' ').title()}")

        return success_count > 0

    async def setup_verification(self, guild, user) -> bool:
        """Set up verification system."""
        try:
            # Create roles
            verified_role = await guild.create_role(name="Verified", color=discord.Color.green())
            unverified_role = await guild.create_role(name="Unverified", color=discord.Color.red())

            # Create channel
            verify_channel = await guild.create_text_channel("verify")

            # Set permissions
            await verify_channel.set_permissions(guild.default_role, read_messages=False)
            await verify_channel.set_permissions(unverified_role, read_messages=True, send_messages=True)

            # Configure system
            config = {
                "enabled": True,
                "verified_role": str(verified_role.id),
                "unverified_role": str(unverified_role.id),
                "verify_channel": str(verify_channel.id),
                "min_account_age_days": 1,
                "kick_new_accounts": False
            }
            dm.update_guild_data(guild.id, "verification_config", config)

            return True
        except Exception as e:
            logger.error(f"Verification setup failed: {e}")
            return False

    async def setup_economy(self, guild, user) -> bool:
        """Set up economy system."""
        try:
            # Create channels
            shop_channel = await guild.create_text_channel("shop")

            # Configure system
            config = {
                "enabled": True,
                "earn_rates": {
                    "coins_per_message": 5,
                    "gem_chance": 0.01
                },
                "daily_amount": 100,
                "daily_cooldown": 86400,
                "currency_name": "Coins",
                "currency_emoji": "🪙",
                "gem_name": "Gems",
                "gem_emoji": "💎",
                "starting_balance": 50
            }
            dm.update_guild_data(guild.id, "economy_config", config)

            return True
        except Exception as e:
            logger.error(f"Economy setup failed: {e}")
            return False

    async def setup_leveling(self, guild, user) -> bool:
        """Set up leveling system."""
        try:
            # Create leaderboard channel
            lb_channel = await guild.create_text_channel("leaderboard")

            # Configure system
            config = {
                "enabled": True,
                "xp_per_message": 10,
                "message_cooldown": 60,
                "announce_level_ups": True,
                "announce_channel": str(lb_channel.id),
                "role_rewards": {}
            }
            dm.update_guild_data(guild.id, "leveling_config", config)

            return True
        except Exception as e:
            logger.error(f"Leveling setup failed: {e}")
            return False

    async def setup_tickets(self, guild, user) -> bool:
        """Set up ticket system."""
        try:
            # Create category and channels
            ticket_category = await guild.create_category("Support Tickets")
            ticket_queue = await guild.create_text_channel("ticket-queue", category=ticket_category)

            # Create staff role
            staff_role = await guild.create_role(name="Support Staff", color=discord.Color.blue())

            # Configure system
            config = {
                "enabled": True,
                "ticket_category": str(ticket_category.id),
                "ticket_queue_channel": str(ticket_queue.id),
                "staff_roles": [str(staff_role.id)],
                "log_channel": str(ticket_queue.id)
            }
            dm.update_guild_data(guild.id, "tickets_config", config)

            return True
        except Exception as e:
            logger.error(f"Tickets setup failed: {e}")
            return False

    async def setup_welcome(self, guild, user) -> bool:
        """Set up welcome system."""
        try:
            # Create welcome channel
            welcome_channel = await guild.create_text_channel("welcome")

            # Configure system
            config = {
                "enabled": True,
                "welcome_channel": str(welcome_channel.id),
                "welcome_message": "Welcome {user} to {server}!",
                "leave_message": "{user} has left the server.",
                "welcome_dm": "Welcome to {server}! Please check the rules and enjoy your stay.",
                "welcome_dm_buttons": True
            }
            dm.update_guild_data(guild.id, "welcome_leave_config", config)

            return True
        except Exception as e:
            logger.error(f"Welcome setup failed: {e}")
            return False

    # Systems whose runtime module reads a different key than f"{system}_config"
    CONFIG_KEY_OVERRIDES = {
        "automod": "automod_config",
        "auto_mod": "auto_mod_config",
        "warnings": "warning_config",
        "reaction_roles": "reaction_roles",
    }

    async def setup_generic_system(self, guild, system, user) -> bool:
        """Generic setup for systems without specific setup logic."""
        try:
            config = {"enabled": True}
            key = self.CONFIG_KEY_OVERRIDES.get(system, f"{system}_config")
            dm.update_guild_data(guild.id, key, config)
            return True
        except Exception as e:
            logger.error(f"Generic setup failed for {system}: {e}")
            return False

    async def initialize_guild(self, guild):
        """Initialize basic data for a new guild."""
        # Set default prefix
        dm.update_guild_data(guild.id, "prefix", "!")

        # Initialize basic configs
        systems = [
            "verification", "anti_raid", "guardian", "auto_mod", "warnings",
            "economy", "leveling", "giveaways", "gamification", "starboard",
            "mod_logging", "logging", "modmail", "suggestions",
            "staff_promo", "staff_shifts", "staff_reviews", "applications", "appeals",
            "welcome_leave", "tickets", "reminders", "announcements", "auto_responder",
            "reaction_roles", "reaction_menus", "role_buttons", "ai_chat"
        ]

        for system in systems:
            config_key = f"{system}_config"
            if not dm.get_guild_data(guild.id, config_key):
                dm.update_guild_data(guild.id, config_key, {"enabled": False})


class AutoSetup(AutoSetupSystem):
    """Interaction-based facade used by the AI action framework (actions.py).

    The real system uses (guild, user) signatures; the action framework calls
    these with (interaction, params), so this class adapts the call and adds
    the missing per-system setup methods.
    """

    async def _create_setup_channel(self, guild, name) -> Optional[discord.TextChannel]:
        """Create a text channel by name, reusing one if it already exists."""
        try:
            existing = discord.utils.get(guild.text_channels, name=name)
            if existing:
                return existing
            return await guild.create_text_channel(name)
        except Exception as e:
            logger.error(f"Failed to create channel '{name}': {e}")
            return None

    async def setup_verification(self, interaction, params=None) -> bool:
        return await super().setup_verification(interaction.guild, interaction.user)

    async def setup_economy(self, interaction, params=None) -> bool:
        return await super().setup_economy(interaction.guild, interaction.user)

    async def setup_leveling(self, interaction, params=None) -> bool:
        return await super().setup_leveling(interaction.guild, interaction.user)

    async def setup_tickets(self, interaction, params=None) -> bool:
        return await super().setup_tickets(interaction.guild, interaction.user)

    async def setup_welcome(self, interaction, params=None) -> bool:
        return await super().setup_welcome(interaction.guild, interaction.user)

    async def setup_applications(self, interaction, params=None) -> bool:
        """Set up the applications system with a staff-apply channel."""
        try:
            guild = interaction.guild
            channel = await self._create_setup_channel(guild, "applications")
            if not channel:
                return False
            config = {
                "enabled": True,
                "channel_id": channel.id,
                "staff_roles": [],
                "questions": [
                    "Why do you want to join the staff team?",
                    "What experience do you have?",
                    "How active are you on this server?",
                    "What would you improve?",
                    "Anything else we should know?",
                ],
            }
            dm.update_guild_data(guild.id, "application_config", config)
            dm.update_guild_data(guild.id, "applications_channel", channel.id)
            embed = discord.Embed(
                title="📋 Staff Applications",
                description="Interested in joining the staff team? Click the button below to apply!",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed, view=ApplyStaffButton(guild_id=guild.id))
            return True
        except Exception as e:
            logger.error(f"Applications setup failed: {e}")
            return False

    async def setup_appeals(self, interaction, params=None) -> bool:
        """Set up the appeals system with an appeals channel."""
        try:
            guild = interaction.guild
            channel = await self._create_setup_channel(guild, "appeals")
            if not channel:
                return False
            config = {
                "appeals_channel_id": channel.id,
                "log_channel_id": channel.id,
                "cooldown_days": 30,
                "reviewer_role_id": None,
                "questions": [
                    "Why were you banned?",
                    "Why should you be unbanned?",
                    "What will you do differently?",
                    "Any evidence to provide?",
                ],
            }
            dm.update_guild_data(guild.id, "appeals_config", config)
            embed = discord.Embed(
                title="⚖️ Moderation Appeals",
                description="If you have been banned or punished and wish to appeal, click the button below.",
                color=discord.Color.blue(),
            )
            await channel.send(embed=embed, view=ApplyStaffButton(guild_id=guild.id))
            return True
        except Exception as e:
            logger.error(f"Appeals setup failed: {e}")
            return False

    async def setup_moderation(self, interaction, params=None) -> bool:
        """Set up moderation with a mod-log channel and moderator role."""
        try:
            guild = interaction.guild
            channel = await self._create_setup_channel(guild, "mod-log")
            if not channel:
                return False
            role = None
            try:
                role = await guild.create_role(name="Moderator", color=discord.Color.orange())
            except Exception as e:
                logger.error(f"Failed to create Moderator role: {e}")
            config = {
                "enabled": True,
                "log_channel": str(channel.id),
                "mod_roles": [str(role.id)] if role else [],
            }
            dm.update_guild_data(guild.id, "moderation_config", config)
            dm.update_guild_data(guild.id, "mod_logging_config", {"enabled": True, "log_channel": str(channel.id)})
            return True
        except Exception as e:
            logger.error(f"Moderation setup failed: {e}")
            return False

    async def setup_logging(self, interaction, params=None) -> bool:
        """Set up the logging system with a logs channel."""
        try:
            guild = interaction.guild
            channel = await self._create_setup_channel(guild, "logs")
            if not channel:
                return False
            dm.update_guild_data(guild.id, "logging_config", {"enabled": True, "log_channel": str(channel.id)})
            return True
        except Exception as e:
            logger.error(f"Logging setup failed: {e}")
            return False

# UI Classes
class SetupStartView(discord.ui.View):
    def __init__(self, auto_setup):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup

    @discord.ui.button(label="Start Setup", style=discord.ButtonStyle.success, emoji="🚀")
    async def start_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.auto_setup.begin_system_selection(interaction)

    @discord.ui.button(label="Quick Setup (Recommended)", style=discord.ButtonStyle.primary, emoji="⚡")
    async def quick_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        recommended = ["verification", "tickets", "economy", "leveling", "auto_mod", "welcome_leave"]
        await self.auto_setup.start_installation(interaction, recommended)

class CategorySelectView(discord.ui.View):
    def __init__(self, auto_setup, guild_id: int):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup
        self.guild_id = guild_id

        # Add category buttons (5 per row max — 10 groups use rows 0-1)
        categories = SystemCategory.get_all_categories()
        for i, category in enumerate(categories):
            button = CategoryButton(category, auto_setup, row=i // 5)
            self.add_item(button)

class CategoryButton(discord.ui.Button):
    def __init__(self, category, auto_setup, row: int = 0):
        super().__init__(
            label=category["name"],
            emoji=category["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row
        )
        self.category = category
        self.auto_setup = auto_setup

    async def callback(self, interaction: discord.Interaction):
        await self.auto_setup.show_category_systems(interaction, self.category)

class SystemSelectView(discord.ui.View):
    def __init__(self, auto_setup, guild_id: int, category):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.category = category
        self.selected_systems = []

        # Add system buttons
        for i, system in enumerate(category["systems"]):
            button = SystemButton(system, self)
            self.add_item(button)

        # Add control buttons
        self.add_item(InstallGroupButton(self, row=4))
        self.add_item(InstallSelectedButton(self, row=4))
        self.add_item(BackButton(auto_setup, guild_id, row=4))

    def toggle_all(self):
        """Mark every system in this group as selected."""
        self.selected_systems = list(self.category["systems"])

class SystemButton(discord.ui.Button):
    def __init__(self, system, parent_view):
        super().__init__(
            label=system.replace("_", " ").title(),
            style=discord.ButtonStyle.secondary
        )
        self.system = system
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if self.system in self.parent_view.selected_systems:
            self.parent_view.selected_systems.remove(self.system)
            self.style = discord.ButtonStyle.secondary
        else:
            self.parent_view.selected_systems.append(self.system)
            self.style = discord.ButtonStyle.success

        await interaction.response.edit_message(view=self.parent_view)

class InstallGroupButton(discord.ui.Button):
    """One-click install of every system in the merged group."""
    def __init__(self, parent_view, row=4):
        super().__init__(
            label="Install Entire Group",
            emoji="📦",
            style=discord.ButtonStyle.primary,
            row=row
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.toggle_all()
        await self.parent_view.auto_setup.start_installation(
            interaction, self.parent_view.selected_systems)

class InstallSelectedButton(discord.ui.Button):
    def __init__(self, parent_view, row=0):
        super().__init__(
            label="Install Selected",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=row
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_systems:
            return await interaction.response.send_message("❌ Please select at least one system.", ephemeral=True)

        await self.parent_view.auto_setup.start_installation(interaction, self.parent_view.selected_systems)

class BackButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, row=0):
        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️",
            row=row
        )
        self.auto_setup = auto_setup
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎯 Select Systems to Install",
            description="Choose which systems you want to set up.",
            color=discord.Color.blue()
        )
        view = CategorySelectView(self.auto_setup, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


# Persistent View Classes for Auto-Setup Buttons
class VerifyButton(discord.ui.View):
    """Persistent view for verification button during auto-setup."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify Me", style=discord.ButtonStyle.success, custom_id="verify_button_persistent")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")

        if not role:
            return await interaction.response.send_message("❌ Verification role not found. Please contact staff.", ephemeral=True)

        if role in interaction.user.roles:
            return await interaction.response.send_message("✅ You are already verified!", ephemeral=True)

        try:
            # Handle Unverified role removal if using the modules/verification system
            unverified = discord.utils.get(guild.roles, name="Unverified")
            if unverified and unverified in interaction.user.roles:
                await interaction.user.remove_roles(unverified)

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ You're verified! Enjoy the server!", ephemeral=True)
            # Log action
            logger.info(f"User {interaction.user.id} verified in guild {guild.id}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to assign the Verified role. Check my role position!", ephemeral=True)


class AcceptRulesButton(discord.ui.View):
    """Persistent view for accept rules button during auto-setup."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="I Accept the Rules", style=discord.ButtonStyle.primary, custom_id="accept_rules_persistent")
    async def accept_rules_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")

        if role and role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Thanks for accepting! You now have full access.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("✅ Rules accepted (but I couldn't add your role).", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to create ticket thread.", ephemeral=True)


class CreateTicketButton(discord.ui.View):
    """Persistent view for create ticket button during auto-setup."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.secondary, custom_id="create_ticket_persistent")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would integrate with the ticket system
        await interaction.response.send_message("🎫 Ticket creation is handled through the ticket system.", ephemeral=True)


class SuggestionButton(discord.ui.View):
    """Persistent view for suggestion button during auto-setup."""
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Make Suggestion", style=discord.ButtonStyle.secondary, custom_id="suggestion_button_persistent")
    async def suggestion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would integrate with the suggestions system
        await interaction.response.send_message("💡 Suggestion creation is handled through the suggestions system.", ephemeral=True)


class ApplyStaffButton(discord.ui.View):
    """Persistent view for apply staff button during auto-setup."""
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Apply for Staff", style=discord.ButtonStyle.primary, custom_id="apply_staff_persistent")
    async def apply_staff_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would integrate with the applications system
        await interaction.response.send_message("👥 Staff applications are handled through the applications system.", ephemeral=True)