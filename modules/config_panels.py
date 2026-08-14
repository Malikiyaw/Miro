import discord
from discord import ui
import time
from data_manager import dm
from logger import logger

class ConfigPanelView(ui.View):
    """Base class for all configuration panels."""
    def __init__(self, bot, guild_id: int, system_name: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.system_name = system_name

    def get_config(self):
        return dm.get_guild_data(self.guild_id, f"{self.system_name}_config", {})

    async def save_config(self, config):
        dm.update_guild_data(self.guild_id, f"{self.system_name}_config", config)

    async def interaction_check(self, interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only administrators can use this panel.", ephemeral=True)
            return False
        return True

def get_config_panel(guild_id: int, system: str):
    """Factory function to get the appropriate config panel."""
    panels = {
        "verification": VerificationConfigPanel,
        "economy": EconomyConfigPanel,
        "leveling": LevelingConfigPanel,
        "tickets": TicketsConfigPanel,
        "suggestions": SuggestionsConfigPanel,
        "giveaways": GiveawaysConfigPanel,
        "welcome_leave": WelcomeConfigPanel,
        "anti_raid": AntiRaidConfigPanel,
        "auto_mod": AutoModConfigPanel,
        "warnings": WarningsConfigPanel,
        "reminders": RemindersConfigPanel,
        "announcements": AnnouncementsConfigPanel,
        "auto_responder": AutoResponderConfigPanel,
        "reaction_roles": ReactionRolesConfigPanel,
        "staff_shifts": StaffShiftsConfigPanel,
        "staff_reviews": StaffReviewsConfigPanel,
        "starboard": StarboardConfigPanel,
        "ai_chat": AIChatConfigPanel,
        "modmail": ModmailConfigPanel,
        "logging": LoggingConfigPanel
    }

    panel_class = panels.get(system)
    if panel_class:
        return panel_class(guild_id)
    return None

def get_system_info(system: str) -> tuple:
    """Get emoji and description for a system."""
    info = {
        "verification": ("🔐", "Verify new members with CAPTCHA"),
        "economy": ("💰", "Coins, shop, and gambling system"),
        "leveling": ("📈", "XP rewards and role progression"),
        "tickets": ("🎫", "Support ticket management"),
        "suggestions": ("💡", "Community suggestion voting"),
        "giveaways": ("🎉", "Automated giveaway management"),
        "welcome_leave": ("👋", "Welcome and leave messages"),
        "anti_raid": ("🛡️", "Raid detection and prevention"),
        "auto_mod": ("🤖", "Automated content moderation"),
        "warnings": ("⚠️", "User warning and punishment system"),
        "reminders": ("⏰", "Scheduled reminders"),
        "announcements": ("📢", "Announcement management"),
        "auto_responder": ("💬", "Automated keyword responses"),
        "reaction_roles": ("🎭", "Role assignment via reactions"),
        "staff_shifts": ("👷", "Staff shift tracking"),
        "staff_reviews": ("📊", "Staff performance reviews"),
        "starboard": ("⭐", "Popular message highlighting"),
        "ai_chat": ("🤖", "AI-powered chat channels"),
        "modmail": ("📬", "Private staff messaging"),
        "logging": ("📋", "Server event logging")
    }
    return info.get(system, ("⚙️", "System configuration"))

# Import all panel classes (defined below)
from modules.verification import VerificationConfigPanel
from modules.economy import EconomyConfigPanel
from modules.leveling import LevelingConfigPanel
from modules.tickets import TicketsConfigPanel
from modules.suggestions import SuggestionsConfigPanel
from modules.giveaways import GiveawaysConfigPanel

# Additional config panels
class WelcomeConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "welcome_leave")

    @discord.ui.button(label="Toggle Welcome", style=discord.ButtonStyle.primary, row=0)
    async def toggle_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Welcome system {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

    @discord.ui.button(label="Set Welcome Channel", style=discord.ButtonStyle.secondary, row=0)
    async def set_welcome_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetChannelModal(self, "welcome_channel", "Welcome Channel")
        await interaction.response.send_modal(modal)

class AntiRaidConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "anti_raid")

    @discord.ui.button(label="Toggle Anti-Raid", style=discord.ButtonStyle.primary, row=0)
    async def toggle_anti_raid(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Anti-raid {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class AutoModConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "auto_mod")

    @discord.ui.button(label="Toggle Auto-Mod", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Auto-mod {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class WarningsConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "warnings")

    @discord.ui.button(label="Toggle Warnings", style=discord.ButtonStyle.primary, row=0)
    async def toggle_warnings(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Warnings {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class RemindersConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "reminders")

    @discord.ui.button(label="Toggle Reminders", style=discord.ButtonStyle.primary, row=0)
    async def toggle_reminders(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Reminders {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class AnnouncementsConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "announcements")

    @discord.ui.button(label="Toggle Announcements", style=discord.ButtonStyle.primary, row=0)
    async def toggle_announcements(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Announcements {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class AutoResponderConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "auto_responder")

    @discord.ui.button(label="Toggle Auto-Responder", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_responder(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Auto-responder {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class ReactionRolesConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "reaction_roles")

    @discord.ui.button(label="Toggle Reaction Roles", style=discord.ButtonStyle.primary, row=0)
    async def toggle_reaction_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Reaction roles {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class StaffShiftsConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "staff_shifts")

    @discord.ui.button(label="Toggle Staff Shifts", style=discord.ButtonStyle.primary, row=0)
    async def toggle_staff_shifts(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Staff shifts {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class StaffReviewsConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "staff_reviews")

    @discord.ui.button(label="Toggle Staff Reviews", style=discord.ButtonStyle.primary, row=0)
    async def toggle_staff_reviews(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Staff reviews {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class StarboardConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "starboard")

    @discord.ui.button(label="Toggle Starboard", style=discord.ButtonStyle.primary, row=0)
    async def toggle_starboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Starboard {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class AIChatConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "ai_chat")

    @discord.ui.button(label="Toggle AI Chat", style=discord.ButtonStyle.primary, row=0)
    async def toggle_ai_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ AI chat {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class ModmailConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "modmail")

    @discord.ui.button(label="Toggle Modmail", style=discord.ButtonStyle.primary, row=0)
    async def toggle_modmail(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Modmail {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

class LoggingConfigPanel(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(None, guild_id, "logging")

    @discord.ui.button(label="Toggle Logging", style=discord.ButtonStyle.primary, row=0)
    async def toggle_logging(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        await self.save_config(config)
        await interaction.response.send_message(f"✅ Logging {'enabled' if config['enabled'] else 'disabled'}", ephemeral=True)

# Reusable modal classes
class SetChannelModal(discord.ui.Modal):
    def __init__(self, parent_panel, config_key: str, title: str):
        super().__init__(title=f"Set {title}")
        self.parent_panel = parent_panel
        self.config_key = config_key

    channel_id = discord.ui.TextInput(label="Channel ID", placeholder="123456789")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ Text channel not found", ephemeral=True)

            config = self.parent_panel.get_config()
            config[self.config_key] = str(channel_id)
            await self.parent_panel.save_config(config)
            await interaction.response.send_message(f"✅ {self.config_key.replace('_', ' ').title()} set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid channel ID", ephemeral=True)

def handle_config_panel_command(guild_id: int, system: str):
    """Handle the config panel command."""
    panel = get_config_panel(guild_id, system)
    return panel


class ConfigPanels:
    """Message-based compatibility facade used by actions.py custom-command handlers."""

    def __init__(self, bot):
        self.bot = bot

    async def show_panel(self, message):
        """!configpanel [system] — send the config panel for a system."""
        if not message.author.guild_permissions.administrator and message.author.id != message.guild.owner_id:
            return await message.channel.send("❌ Only administrators can access configuration panels.")

        args = message.content.split()
        system = args[1].lower() if len(args) > 1 else None

        if not system:
            embed = discord.Embed(
                title="⚙️ Configuration Panels",
                description="Select a system to configure:\n\n"
                            "`!configpanel verification` — 🔐 Verify new members\n"
                            "`!configpanel economy` — 💰 Coins, shop, gambling\n"
                            "`!configpanel leveling` — 📈 XP and roles\n"
                            "`!configpanel tickets` — 🎫 Support tickets\n"
                            "`!configpanel auto_responder` — 💬 Auto responses\n"
                            "`!configpanel reaction_roles` — 🎭 Reaction roles\n"
                            "`!configpanel warnings` — ⚠️ Warnings system\n"
                            "`!configpanel modmail` — 📬 Modmail\n"
                            "`!configpanel logging` — 📋 Event logging",
                color=discord.Color.blue()
            )
            return await message.channel.send(embed=embed)

        panel = get_config_panel(message.guild.id, system)
        if not panel:
            return await message.channel.send(f"❌ Unknown system '{system}'. Use `!configpanel` to list systems.")

        emoji, description = get_system_info(system)
        await message.channel.send(f"{emoji} **{system.title()}** Configuration — {description}", view=panel)

def register_all_persistent_views(bot):
    """Register all persistent views for immortal buttons."""
    logger.info("register_all_persistent_views called - persistent view registration monitored")