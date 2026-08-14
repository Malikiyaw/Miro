import discord
from data_manager import dm
from logger import logger
import time
from typing import Dict, Optional, List

class ReactionRoleSystem:
    """
    Reaction Roles System:
    - Bind any emoji to any role on any message
    - Supports restrictions (age, level, roles)
    - Logs all actions
    """
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> Dict:
        """Get reaction role configurations for a guild"""
        return dm.get_guild_data(guild_id, "reaction_roles", {})

    def save_config(self, guild_id: int, config: Dict):
        """Save reaction role configurations for a guild"""
        dm.update_guild_data(guild_id, "reaction_roles", config)

    def log_action(self, guild_id: int, user_id: int, action: str, role_id: int, message_id: int):
        """Log a reaction role action"""
        logs = dm.get_guild_data(guild_id, "reaction_role_log", [])
        logs.append({
            "ts": time.time(),
            "user_id": user_id,
            "action": action,
            "role_id": role_id,
            "message_id": message_id
        })
        # Keep last 100 logs
        dm.update_guild_data(guild_id, "reaction_role_log", logs[-100:])

    async def handle_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle role assignment on reaction add"""
        if payload.member and payload.member.bot:
            return

        guild_id = payload.guild_id
        if not guild_id:
            return

        config = self.get_config(guild_id)
        msg_id_str = str(payload.message_id)
        if msg_id_str not in config:
            return

        emoji_str = str(payload.emoji)
        role_data = config[msg_id_str].get(emoji_str)
        if not role_data:
            return

        role_id = role_data.get("role_id")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        member = payload.member
        if not member:
            member = guild.get_member(payload.user_id)
        if not member:
            return

        # Check restrictions
        # 1. Min Account Age (days)
        min_age = role_data.get("min_age", 0)
        if min_age > 0:
            days_old = (discord.utils.utcnow() - member.created_at).days
            if days_old < min_age:
                return

        # 2. Min Level
        min_level = role_data.get("min_level", 0)
        if min_level > 0:
            xp = self.bot.leveling.get_xp(guild_id, member.id)
            user_level = self.bot.leveling.get_level_from_xp(xp)
            if user_level < min_level:
                return

        # 3. Prerequisite Role
        prereq_id = role_data.get("prerequisite_role_id")
        if prereq_id:
            if not any(r.id == int(prereq_id) for r in member.roles):
                return

        # 4. Incompatible Role
        incomp_id = role_data.get("incompatible_role_id")
        if incomp_id:
            if any(r.id == int(incomp_id) for r in member.roles):
                return

        try:
            await member.add_roles(role, reason="Reaction Role assignment")
            self.log_action(guild_id, member.id, "add", role_id, payload.message_id)
        except Exception as e:
            logger.error(f"Failed to add reaction role {role_id} to user {member.id}: {e}")

    async def handle_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle role removal on reaction remove"""
        guild_id = payload.guild_id
        if not guild_id:
            return

        config = self.get_config(guild_id)
        msg_id_str = str(payload.message_id)
        if msg_id_str not in config:
            return

        emoji_str = str(payload.emoji)
        role_data = config[msg_id_str].get(emoji_str)
        if not role_data:
            return

        role_id = role_data.get("role_id")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        member = guild.get_member(payload.user_id)
        if not member:
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction Role removal")
                self.log_action(guild_id, member.id, "remove", role_id, payload.message_id)
        except Exception as e:
            logger.error(f"Failed to remove reaction role {role_id} from user {member.id}: {e}")

    def add_reaction_role(self, guild_id: int, message_id: int, emoji: str, role_id: int,
                           min_age: int = 0, min_level: int = 0,
                           prereq_role_id: Optional[int] = None,
                           incomp_role_id: Optional[int] = None):
        """Add a reaction role binding to the config"""
        config = self.get_config(guild_id)
        msg_id_str = str(message_id)
        if msg_id_str not in config:
            config[msg_id_str] = {}

        config[msg_id_str][emoji] = {
            "role_id": role_id,
            "min_age": min_age,
            "min_level": min_level,
            "prerequisite_role_id": prereq_role_id,
            "incompatible_role_id": incomp_role_id
        }
        self.save_config(guild_id, config)

    def remove_reaction_role(self, guild_id: int, message_id: int, emoji: str):
        """Remove a reaction role binding from the config"""
        config = self.get_config(guild_id)
        msg_id_str = str(message_id)
        if msg_id_str in config and emoji in config[msg_id_str]:
            del config[msg_id_str][emoji]
            if not config[msg_id_str]:
                del config[msg_id_str]
            self.save_config(guild_id, config)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """AI-driven setup for reaction roles"""
        guild = interaction.guild

        # Example help embed for the system
        help_embed = discord.Embed(
            title="🎭 Reaction Roles System",
            description="Assign roles instantly by reacting to messages.",
            color=discord.Color.blue()
        )
        help_embed.add_field(
            name="How to use",
            value="Staff can use the `!reactionrolespanel` to bind emojis to roles on specific messages.",
            inline=False
        )
        help_embed.add_field(
            name="Restrictions",
            value="You can set minimum account age, level requirements, and role dependencies.",
            inline=False
        )

        await interaction.followup.send(embed=help_embed, ephemeral=True)

        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["reactionrolespanel"] = "configpanel reactionroles"
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        return True
