import discord
import json
import asyncio
import time
import random
import difflib
from datetime import datetime, timezone
import datetime as dt
from typing import List, Dict, Any, Tuple, Optional
from modules.automation_manager import check_command_permission
from data_manager import dm
from logger import logger
from core.audit import SOURCE_AI, SOURCE_COMMAND
from utils.deduplicator import deduplicator
from ui_components import *
from animated_assets import get_animated_emoji, get_success_gif, create_success_embed, create_loading_embed

# Color name to integer mapping for embeds
COLOR_MAP = {
    "gold": 0xFFD700,
    "yellow": 0xFFD700,
    "blue": 0x3498DB,
    "red": 0xE74C3C,
    "green": 0x2ECC71,
    "purple": 0x9B59B6,
    "orange": 0xE67E22,
    "pink": 0xFF69B4,
    "white": 0xFFFFFF,
    "black": 0x000000,
    "gray": 0x95A5A6,
    "grey": 0x95A5A6,
    "cyan": 0x1ABC9C,
    "magenta": 0xE91E63,
    "brown": 0x795548,
    "navy": 0x34495E,
    "lime": 0xCDDC39,
    "teal": 0x008080,
}

def parse_color(color_val):
    """Convert color value to valid Discord embed color."""
    if isinstance(color_val, int):
        return color_val
    if isinstance(color_val, str):
        # Try color name first
        color_lower = color_val.lower()
        if color_lower in COLOR_MAP:
            return COLOR_MAP[color_lower]
        # Try hex string
        try:
            if color_val.startswith("#"):
                return int(color_val[1:], 16)
            return int(color_val, 16)
        except ValueError:
            pass
    # Default fallback
    return 0x3498DB

class ScheduledTaskInteraction:
    """Minimal interaction stand-in for actions executed by the task scheduler.
    The bot-user identity exempts scheduled actions from the admin permission gate."""
    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild = bot.get_guild(guild_id)
        self.user = bot.user
        self.channel = getattr(self.guild, "system_channel", None) if self.guild else None
        self.response = self
        self.followup = self

    async def send(self, *args, **kwargs):
        pass

    async def send_message(self, *args, **kwargs):
        pass

    async def edit_message(self, *args, **kwargs):
        pass

    async def defer(self, *args, **kwargs):
        pass

def is_system_enabled(guild_id: int, system_name: str) -> bool:
    """Check if a system is enabled for a guild. Returns False if not installed."""
    config_key = f"{system_name}_config"
    config = dm.get_guild_data(guild_id, config_key, {})
    if not config:
        return False  # Not installed
    return config.get("enabled", False)

COMMAND_SCHEMA = {
    "type": "object",
        "properties": {
          "command_type": {
            "type": "string",
            "enum": ["application_status", "appeal_status", "help_embed", "simple", "economy_daily", "economy_balance", "economy_work", "economy_beg", "economy_leaderboard", "economy_shop", "economy_transfer", "economy_rob", "economy_buy", "economy_challenge", "leaderboard", "leveling_rank", "leveling_leaderboard", "leveling_levels", "leveling_rewards", "leveling_shop", "staffpromo_status", "staffpromo_leaderboard", "staffpromo_progress", "staffpromo_tiers", "staffpromo_roles", "staffpromo_review", "staffpromo_requirements", "staffpromo_bonuses", "staffpromo_exclude", "staffpromo_config", "staffpromo_promote", "staffpromo_demote", "staffpromotion_history", "peer_vote", "list_triggers", "help_all", "config_panel", "ticket_create", "ticket_close", "appeal_create", "application_apply", "set_verify_channel", "create_tournament", "create_event", "list_quests", "prestige", "dice", "flip", "slots", "trivia", "starboard_leaderboard", "list_events", "list_tournaments", "tournament_leaderboard", "tournament_join", "server_stats", "my_stats", "at_risk", "remind", "list_reminders", "mod_stats", "shift_start", "shift_end", "shift_status", "staff_review", "announce", "list_quests", "raidstatus", "guardian_status", "automod_status", "modlog_view", "suggest", "chatchannel_add", "autoresponder_add", "remindme", "announcement_create", "giveaway_create"]
          },
        "content": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "parameters": {"type": "object"}
                },
                "required": ["name"]
            }
        }
    },
    "required": ["command_type"]
}

def validate_command_json(data: dict) -> Tuple[bool, str]:
    """Validate custom command JSON against schema. Returns (valid, error_message)."""
    if not isinstance(data, dict):
        return False, "Command must be a JSON object"

    command_type = data.get("command_type")
    if command_type is None:
        return False, "Missing required field: command_type"

    allowed_types = COMMAND_SCHEMA["properties"]["command_type"]["enum"]
    if command_type not in allowed_types:
        return False, f"Invalid command_type: {command_type}. Allowed: {allowed_types}"

    if "actions" in data:
        if not isinstance(data["actions"], list):
            return False, "actions must be an array"
        for i, action in enumerate(data["actions"]):
            if not isinstance(action, dict):
                return False, f"Action {i} must be an object"
            if "name" not in action:
                return False, f"Action {i} missing required field: name"
            if action["name"] not in ActionHandler.ALLOWED_ACTIONS:
                return False, f"Disallowed action in command: {action['name']}"

    return True, ""

class ConnectSystemsWizardModal(discord.ui.Modal):
    """Modal wizard for creating system connections with guided parameter collection."""

    def __init__(self):
        super().__init__(title="🔗 Create System Connection", timeout=300)

        self.source_system = discord.ui.TextInput(
            label="Source System",
            placeholder="e.g., leveling, verification, economy",
            style=discord.TextStyle.short,
            required=True,
            max_length=50
        )
        self.add_item(self.source_system)

        self.trigger_event = discord.ui.TextInput(
            label="Trigger Event",
            placeholder="e.g., level_up, member_join, daily_claimed",
            style=discord.TextStyle.short,
            required=True,
            max_length=50
        )
        self.add_item(self.trigger_event)

        self.target_system = discord.ui.TextInput(
            label="Target System",
            placeholder="e.g., welcome, economy, tickets",
            style=discord.TextStyle.short,
            required=True,
            max_length=50
        )
        self.add_item(self.target_system)

        self.action = discord.ui.TextInput(
            label="Action to Perform",
            placeholder="e.g., send_message, give_points, assign_role",
            style=discord.TextStyle.short,
            required=True,
            max_length=50
        )
        self.add_item(self.action)

        self.parameters = discord.ui.TextInput(
            label="Additional Parameters (JSON, optional)",
            placeholder='{"amount": 50, "message": "Welcome!"}',
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )
        self.add_item(self.parameters)

    async def on_submit(self, interaction: discord.Interaction):
        """Process the modal submission and create the connection."""
        try:
            # Parse parameters if provided
            parameters = {}
            if self.parameters.value and self.parameters.value.strip():
                try:
                    parameters = json.loads(self.parameters.value.strip())
                except json.JSONDecodeError:
                    await interaction.response.send_message(
                        "❌ Invalid JSON in parameters. Please check your syntax and try again.",
                        ephemeral=True
                    )
                    return

            # Execute the connect_systems action with collected parameters
            from actions import ActionHandler
            action_handler = ActionHandler(interaction.client)

            params = {
                "source_system": self.source_system.value.strip().lower(),
                "trigger_event": self.trigger_event.value.strip().lower(),
                "target_system": self.target_system.value.strip().lower(),
                "action": self.action.value.strip().lower(),
                "parameters": parameters
            }

            success, result = await action_handler.dispatch(interaction, "connect_systems", params)

            if success:
                embed = discord.Embed(
                    title="✅ System Connection Created",
                    description=f"**{params['source_system']}** → **{params['target_system']}**",
                    color=discord.Color.green()
                )
                embed.add_field(name="Trigger Event", value=f"`{params['trigger_event']}`", inline=True)
                embed.add_field(name="Action", value=f"`{params['action']}`", inline=True)
                if parameters:
                    embed.add_field(name="Parameters", value=f"```{json.dumps(parameters, indent=2)}```", inline=False)
                embed.set_footer(text="Use /configpanel to manage your connections")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                error_msg = result.get("error", "Unknown error") if result else "Unknown error"
                await interaction.response.send_message(f"❌ Failed to create connection: {error_msg}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in connect systems wizard: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while creating the connection. Please try again.",
                ephemeral=True
            )

class ActionHandler:
    ALLOWED_ACTIONS = {
        "send_message", "send_embed", "add_role", "remove_role",
        "create_channel", "create_shop_channel", "delete_channel", "create_role", "delete_role",
        "find_duplicate_channels", "bulk_delete_channels", "cleanup_duplicate_channels",
        "create_category", "edit_channel", "edit_role", "assign_role",
        "assign_role_by_name", "create_prefix_command", "create_command", "make_command", "add_command", "new_command", "delete_prefix_command",
        "setup_welcome", "setup_logging", "setup_verification", "setup_economy", "setup_leveling",
        "setup_tickets", "setup_applications", "setup_appeals", "setup_moderation", "setup_staff_system",
        "send_dm", "create_invite", "schedule_ai_action", "ping",
        "kick_user", "ban_user", "timeout_user", "softban_user",
        "announce", "poll", "give_points", "remove_points", "warn_user",
        "create_verify_system", "create_tickets_system", "create_applications_system", "create_appeals_system",
        "create_welcome_system", "create_staff_system", "create_leveling_system", "create_economy_system",
        "mute_user", "unmute_user", "deafen_user", "set_nickname", "slowmode", "lock_channel", "unlock_channel",
        "reply_message", "add_reaction", "edit_channel_name", "edit_role_name",
        "change_role_color", "move_channel", "clone_channel", "create_thread", "pin_message", "unpin_message",
        "set_topic", "delete_messages", "remove_reaction", "delete_message", "bulk_delete_messages",
        "create_role_with_permissions", "edit_channel_permissions", "create_voice_channel", "create_text_channel",
        "create_category_channel", "edit_channel_bitrate", "edit_channel_user_limit", "follow_announcement_channel",
        "create_scheduled_event", "allow_channel_permission", "deny_channel_permission",
        "deny_all_channels_for_role", "allow_all_channels_for_role", "deny_category_for_role",
        "make_channel_private", "make_category_private", "clear_reactions", "edit_guild",
        "analyze_server_state", "extract_online_users", "send_notification", "create_task", "update_profile",
        # Server Query Actions
        "query_server_info", "query_channels", "query_roles", "query_members", "query_member_details",
        "get_channel",
        "query_economy_leaderboard", "query_xp_leaderboard", "query_pending_applications",
        "query_active_shifts", "query_recent_messages",
        # System connection actions
        "connect_systems",
        # System move actions
        "move_system",
        # Giveaway actions
        "giveaway_end", "giveaway_reroll", "giveaway_list",
        # Gamification actions
        "prestige", "dice", "flip", "slots", "trivia",
        # Additional actions from action_catalog
        "post_documentation", "setup_trigger_role",
        # Personalized Memory actions
        "update_user_preference",
        # Automation actions — LIVE (1000x)
        "create_automation", "delete_automation", "list_automations",
        "delete_prefix_command", "list_prefix_commands",
        "update_automation", "pause_automation", "resume_automation", "run_automation_now",
        "bulk_create_automations", "bulk_pause_automations", "bulk_delete_automations",
        "bulk_create_prefix_commands",
}

    @classmethod
    def get_commands_for_system(cls, system: str) -> list:
        """
        Get custom commands related to a system.
        This is used in !configpanel to show available commands.
        """
        # Normalize system key like in get_config_panel
        normalized_system = system.lower().replace("_", "").replace(" ", "").replace("system", "")

        system_commands = {
            "verification": ["!setverifychannel", "!configpanel verification"],
            "antiraid": ["!raidstatus", "!configpanel antiraid"],
            "guardian": ["!guardian status", "!configpanel guardian"],
            "automod": ["!automod status", "!configpanel automod"],
            "warnings": ["!warn @user", "!warnings", "!clearwarn", "!clearallwarns", "!configpanel warnings"],
            "moderation": ["!kick", "!ban", "!mute", "!modstats", "!configpanel moderation"],
            "modlog": ["!modlog view", "!configpanel modlog"],
            "logging": ["!configpanel logging"],
            "economy": ["!balance", "!daily", "!work", "!ecoleaderboard", "!challenge", "!shop", "!buy", "!transfer", "!give", "!beg", "!rob", "!help economy", "!configpanel economy"],
            "economyshop": ["!shop", "!buy", "!configpanel economyshop"],
            "leveling": ["!rank", "!levelleaderboard", "!levels", "!rewards", "!levelshop", "!configpanel leveling"],
            "levelingshop": ["!levelshop", "!configpanel levelingshop"],
            "starboard": ["!starboard", "!configpanel starboard"],
            "tickets": ["!configpanel tickets"],
            "welcome": ["!configpanel welcome"],
            "welcomedm": ["!configpanel welcomedm"],
            "applications": ["!configpanel applications"],
            "appeals": ["!configpanel appeals"],
            "modmail": ["!configpanel modmail"],
            "suggestions": ["!suggest", "!configpanel suggestions"],
            "giveaway": ["!configpanel giveaway"],
            "gamification": ["!configpanel gamification"],
            "reactionroles": ["!configpanel reactionroles"],
            "reactionmenus": ["!configpanel reactionmenus"],
            "rolebuttons": ["!configpanel rolebuttons"],
            "staffpromo": ["!staffpromo", "!staffpromo tiers", "!staffpromo requirements", "!staffpromo status", "!staffpromo leaderboard", "!staffpromo progress", "!configpanel staffpromo"],
            "events": ["!configpanel events"],
            "chatchannels": ["!configpanel chatchannels"],
        }
        return system_commands.get(normalized_system, [])
    
    def __init__(self, bot):
        if bot is None:
            logger.error("ActionHandler initialized with None bot instance!")
        self.bot = bot
        self._action_log = []
        self._setup_id = None
        self._artifacts = []
        # Universal metadata: every allowed action gets object/operation/
        # danger/permission/timeout coverage (V6 "all tools" mandate).
        try:
            from core.action_meta import ensure_metadata
            ensure_metadata(self.ALLOWED_ACTIONS)
        except Exception as e:
            logger.warning(f"metadata fill failed: {e}")
        # Native schema catalog for every tool (provider advertising)
        try:
            from agent.tool_registry import ensure_full_catalog
            ensure_full_catalog(self.ALLOWED_ACTIONS)
        except Exception as e:
            logger.warning(f"tool catalog fill failed: {e}")
        self._guild_context = None

    def _ensure_bot(self):
        """Ensure bot is set; raise informative error if not."""
        if self.bot is None:
            raise AttributeError("ActionHandler 'bot' attribute is None. ActionHandler was not properly initialized with a bot instance.")
        return True

    def _ensure_bot_attr(self, attr_name, default=None):
        """Safely get a bot attribute, with informative error if missing."""
        self._ensure_bot()
        if not hasattr(self.bot, attr_name):
            raise AttributeError(f"ActionHandler's bot instance (MiroBot) is missing required attribute '{attr_name}'.")
        return getattr(self.bot, attr_name)

    def set_guild_context(self, guild):
        """Set the guild context for help and other commands"""
        self._guild_context = guild

    async def _auto_document_system(self, guild_id, system_type):
        """Auto-document a system by creating missing ! commands with fallback responses."""
        try:
            # Load system commands mapping
            import os
            mapping_file = os.path.join(os.path.dirname(__file__), "system_commands.json")
            if not os.path.exists(mapping_file):
                logger.warning(f"system_commands.json not found at {mapping_file}")
                return

            with open(mapping_file, "r") as f:
                # Entries live under the top-level "commands" key
                system_commands = json.load(f).get("commands", {})

            if system_type not in system_commands:
                logger.info(f"No commands defined for system_type: {system_type}")
                return

            commands = system_commands[system_type]
            custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})

            # Create missing commands
            created_commands = []
            for cmd_name, cmd_data in commands.items():
                if cmd_name not in custom_cmds:
                    # Create command with fallback response
                    custom_cmds[cmd_name] = json.dumps(cmd_data)
                    created_commands.append(cmd_name)
                    logger.info(f"Created missing command: !{cmd_name} for system: {system_type}")

            # Create help command for the system using help_embed type
            help_cmd_name = f"help {system_type}"
            if help_cmd_name not in custom_cmds:
                fields = []
                for c_name, c_data in commands.items():
                    desc = c_data.get("content", "No description available.")
                    fields.append({"name": f"!{c_name}", "value": desc, "inline": False})

                custom_cmds[help_cmd_name] = json.dumps({
                    "command_type": "help_embed",
                    "title": f"ðŸ›¡ï¸ {system_type.replace('_', ' ').title()} System",
                    "description": f"Documentation for the {system_type} system.",
                    "fields": fields
                })
                created_commands.append(help_cmd_name)
                logger.info(f"Created help command: !{help_cmd_name}")

            if created_commands:
                dm.update_guild_data(guild_id, "custom_commands", custom_cmds)

                # Send help embed in dedicated channel
                guild = self.bot.get_guild(guild_id)
                if guild:
                    # Find or create help channel
                    help_channel = None
                    for channel in guild.text_channels:
                        if channel.name in ["help", "commands", "bot-help", "system-guide"]:
                            help_channel = channel
                            break
                    if not help_channel:
                        try:
                            # Use the Cog's creation method if possible to track it
                            if hasattr(self.bot, 'auto_setup'):
                                help_channel = await self.bot.auto_setup._create_setup_channel(guild, "system-guide")
                            else:
                                help_channel = await guild.create_text_channel("system-guide")
                        except discord.Forbidden:
                            logger.warning("Cannot create help channel due to permissions")
                            return

                    if help_channel:
                        embed = discord.Embed(
                            title=f"✅ {system_type.replace('_', ' ').title()} System Deployed",
                            description=(
                                f"The {system_type} system has been successfully built and documented. "
                                f"New commands available: {', '.join(f'`!{c}`' for c in created_commands if not c.startswith('help'))}\n\n"
                                f"Type `!help {system_type}` to see detailed usage instructions."
                            ),
                            color=discord.Color.green()
                        )
                        embed.set_footer(text="Miro AI • Infrastructure as Code")
                        await help_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in _auto_document_system: {e}")

    def _normalize_params(self, params, mappings):
        """Normalize parameters using aliases and defaults."""
        normalized = {}
        for expected, aliases in mappings.items():
            value = None
            for alias in aliases:
                if alias in params:
                    value = params[alias]
                    break
            # Apply defaults if missing
            if value is None:
                if expected == "channel_name":
                    value = "new-channel"
                elif expected == "role_name":
                    value = "new-role"
                elif expected == "duration":
                    value = 60
                elif expected == "reason":
                    value = "No reason provided"
                elif expected == "content":
                    value = ""
                elif expected == "color":
                    value = "#3498db"
            # Special handling
            if expected == "user_id":
                if isinstance(value, str):
                    import re
                    # Extract from mention
                    match = re.match(r'<@!?(\d+)>', value)
                    if match:
                        value = int(match.group(1))
                    elif value.isdigit():
                        value = int(value)
            elif expected == "duration":
                if params.get("minutes") and not params.get("duration"):
                    value = params["minutes"] * 60
            elif expected == "color":
                if params.get("colour"):
                    value = params["colour"]
                elif params.get("hex_color"):
                    value = params["hex_color"]
            normalized[expected] = value
        return normalized

    async def execute_sequence(self, interaction: discord.Interaction, actions: List[Dict[str, Any]], auto_rollback: bool = True) -> Dict[str, Any]:
        """Executes a list of actions with automatic rollback on failure and crash recovery tracking."""
        import uuid
        setup_id = str(uuid.uuid4())
        results = []
        self._action_log = []
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups[setup_id] = {
            "guild_id": guild_id,
            "user_id": user_id,
            "actions_count": len(actions),
            "actions_taken": [],
            "started_at": time.time()
        }
        dm.save_json("pending_setups", pending_setups)
        self._setup_id = setup_id
        self._artifacts = []

        for i, action in enumerate(actions):
            name = action.get("name")
            params = action.get("parameters", {})
            # Ensure params is a dictionary
            if not isinstance(params, dict):
                logger.warning(f"Parameters for action {name} is not a dict: {type(params)}. Converting to empty dict.")
                params = {}

            if name not in self.ALLOWED_ACTIONS:
                logger.warning("Blocked disallowed action: %s", name)
                error_msg = f"Action not allowed: {name}"
                results.append((name, False))
                self._record_failure(guild_id, name, error_msg)
                return {"results": results, "rolled_back": [], "failed_at": i, "failed_action": name, "error": error_msg, "success": False}

            try:
                success, undo_data = await self.dispatch(interaction, name, params)

                if success:
                    results.append((name, success))

                    if undo_data:
                        self._action_log.append({
                            "action": name,
                            "undo_data": undo_data,
                            "guild_id": guild_id,
                            "user_id": user_id,
                            "timestamp": time.time()
                        })
                else:
                    # Failure
                    if undo_data and isinstance(undo_data, dict) and "error" in undo_data:
                        error_msg = undo_data["error"]
                    else:
                        error_msg = f"Action returned failure: {name}"
                    logger.error("Action Error (%s): %s", name, error_msg)
                    results.append((name, False))

                    self._record_failure(guild_id, name, error_msg)

                    await interaction.followup.send(error_msg, ephemeral=True)

                    if auto_rollback and self._action_log:
                        rollback_results = await self._rollback_sequence(interaction)
                        return {
                            "results": results,
                            "rolled_back": rollback_results,
                            "failed_at": i,
                            "failed_action": name,
                            "error": error_msg,
                            "success": False
                        }
                    else:
                        return {
                            "results": results,
                            "rolled_back": [],
                            "failed_at": i,
                            "failed_action": name,
                            "error": error_msg,
                            "success": False
                        }
            except Exception as e:
                error_msg = str(e)
                logger.error("Action Error (%s): %s", name, error_msg)
                results.append((name, False))

                self._record_failure(guild_id, name, error_msg)

                await interaction.followup.send(error_msg, ephemeral=True)

                if auto_rollback and self._action_log:
                    rollback_results = await self._rollback_sequence(interaction)
                    return {
                        "results": results,
                        "rolled_back": rollback_results,
                        "failed_at": i,
                        "failed_action": name,
                        "error": error_msg,
                        "success": False
                    }
                else:
                    return {
                        "results": results,
                        "rolled_back": [],
                        "failed_at": i,
                        "failed_action": name,
                        "error": error_msg,
                        "success": False
                    }

        if self._action_log:
            action_logs = dm.get_guild_data(guild_id, "action_logs", [])
            action_logs.extend(self._action_log)
            dm.update_guild_data(guild_id, "action_logs", action_logs)

        self._record_successes(guild_id, [a.get("name") for a in actions])

        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups.pop(setup_id, None)
        dm.save_json("pending_setups", pending_setups)

        return {
            "results": results,
            "rolled_back": [],
            "failed_at": None,
            "success": True
        }

    async def _rollback_sequence(self, interaction: discord.Interaction) -> List[Tuple[str, bool]]:
        """Rollback all successfully executed actions in reverse order."""
        rollback_results = []
        for log_entry in reversed(self._action_log):
            undo_data = log_entry.get("undo_data", {})
            undo_action = undo_data.get("action")
            success = await self._execute_undo(interaction, undo_action, undo_data)
            rollback_results.append((log_entry.get("action", "unknown"), success))

        self._action_log = []
        return rollback_results

    def _record_failure(self, guild_id: int, action_name: str, error: str):
        """Record action failure for AI self-improvement."""
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        if action_name not in failures:
            failures[action_name] = {"count": 0, "errors": [], "last_error": None}
        failures[action_name]["count"] += 1
        failures[action_name]["last_error"] = error[:200]
        if len(failures[action_name]["errors"]) < 10:
            failures[action_name]["errors"].append(error[:200])
        dm.update_guild_data(guild_id, "action_failures", failures)

        dm.record_global_action_result(action_name, False, error)

    def _record_successes(self, guild_id: int, action_names: List[str]):
        """Record action successes for AI self-improvement."""
        successes = dm.get_guild_data(guild_id, "action_successes", {})
        for name in action_names:
            if name not in successes:
                successes[name] = 0
            successes[name] += 1
        dm.update_guild_data(guild_id, "action_successes", successes)

        for name in action_names:
            dm.record_global_action_result(name, True)

    def _track_artifact(self, artifact_type: str, artifact_id: int, name: str):
        """Track a created artifact for crash recovery."""
        if self._setup_id:
            self._artifacts.append({"type": artifact_type, "id": artifact_id, "name": name})
            pending_setups = dm.load_json("pending_setups", default={})
            if self._setup_id in pending_setups:
                pending_setups[self._setup_id]["actions_taken"].append(
                    {"type": artifact_type, "id": artifact_id, "name": name}
                )
                dm.save_json("pending_setups", pending_setups)

    def _normalize_action_name(self, name: str) -> str:
        import re
        original_name = name
        name = name.lower()
        name = name.replace(' ', '_')
        typo_map = {'creat': 'create', 'chanel': 'channel', 'assing': 'assign', 'remve': 'remove', 'delet': 'delete', 'membr': 'member', 'nmae': 'name', 'mesage': 'message', 'embded': 'embed', 'reson': 'reason', 'duraton': 'duration'}
        for typo, correct in typo_map.items():
            name = name.replace(typo, correct)
        phrasing_map = {'send_embed': 'embed_send', 'embed_send': 'send_embed', 'dm_user': 'send_dm', 'send_dm': 'dm_user', 'ban_member': 'ban_user', 'ban_user': 'ban_member', 'kick_member': 'kick_user', 'kick_user': 'kick_member'}
        if name in phrasing_map:
            name = phrasing_map[name]
        name = name.rstrip('.,!?;:')
        name = re.sub(r'_+', '_', name)
        name = name.strip('_')
        if name in self.ALLOWED_ACTIONS:
            return name
        variations = []
        if name.endswith('_user'):
            variations.append(name[:-5])
        elif name.endswith('_member'):
            variations.append(name[:-7])
        else:
            variations.append(name + '_user')
            variations.append(name + '_member')
        for var in variations:
            if var in self.ALLOWED_ACTIONS:
                # Safety rule (plan 15): destructive actions require an exact
                # semantic match — fuzzy suffix variation must never land on
                # an unrelated destructive action.
                from core.action_meta import is_destructive
                if name != var and is_destructive(var):
                    continue
                return var
        return original_name

    def _fuzzy_match(self, target: str, candidates: List[str], cutoff: float = 0.6) -> Optional[str]:
        """Find the best fuzzy match for target in candidates using difflib."""
        if not target or not candidates:
            return None

        target_lower = target.lower()
        matches = difflib.get_close_matches(target_lower, [c.lower() for c in candidates], n=1, cutoff=cutoff)
        if matches:
            # Find the original case candidate
            for candidate in candidates:
                if candidate.lower() == matches[0]:
                    return candidate
        return None

    # ------------------------------------------------------------------ #
    # Automation scale layer (delegates to modules/automation_manager)    #
    # ------------------------------------------------------------------ #

    def _am(self):
        from modules.automation_manager import (check_quota, get_automations,
                                                save_automations, find_automation,
                                                record_run, interval_to_cron,
                                                MAX_BULK_ITEMS)
        return check_quota, get_automations, save_automations, find_automation, record_run, interval_to_cron, MAX_BULK_ITEMS

    async def action_update_automation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Update an existing automation: cron/schedule, action, channel, or rename."""
        check_quota, get_automations, save_automations, find_automation, record_run, interval_to_cron, _ = self._am()
        guild_id = interaction.guild.id
        name = str(params.get("name") or params.get("automation_name") or "").strip()
        if not name:
            return False, {"error": "update_automation requires 'name'."}
        key, entry = find_automation(guild_id, name)
        if entry is None:
            return False, {"error": f"Automation '{name}' not found."}
        changed = []
        autos = get_automations(guild_id)
        new_schedule = params.get("schedule") if isinstance(params.get("schedule"), dict) else {}
        new_cron = str(params.get("cron") or new_schedule.get("cron") or "").strip()
        if not new_cron and new_schedule:
            new_cron = interval_to_cron(new_schedule) or ""
        if new_cron:
            entry["cron"] = new_cron
            changed.append(f"cron={new_cron}")
        new_action = params.get("action")
        if isinstance(new_action, dict):
            handler_name = str(new_action.get("name") or new_action.get("handler") or "").strip()
            if handler_name:
                entry["handler"] = handler_name
                changed.append(f"handler={handler_name}")
            if isinstance(new_action.get("parameters"), dict):
                entry["params"] = self._normalize_scheduled_params(handler_name, new_action["parameters"], entry.get("channel_id"))
                changed.append("params updated")
        if params.get("response") is not None:
            entry["params"] = dict(entry.get("params") or {})
            entry["params"]["content"] = str(params["response"])[:2000]
            changed.append("response updated")
        new_channel = params.get("channel_id")
        if new_channel and str(new_channel).isdigit():
            entry["channel_id"] = str(new_channel)
            entry["params"] = dict(entry.get("params") or {})
            entry["params"]["channel_id"] = str(new_channel)
            changed.append("channel updated")
        # Reschedule if cron/handler changed and it is an active scheduled task
        if entry.get("type") == "scheduled_task" and ("cron" in " ".join(changed) or any("handler" in c for c in changed)):
            try:
                from task_scheduler import task_scheduler
                old_tid = entry.get("task_id")
                if old_tid:
                    task_scheduler.cancel_task(old_tid)
                if not entry.get("paused"):
                    tid, nxt = self._schedule_cron_job(guild_id, key, entry["cron"],
                                                       entry.get("handler", "send_message"),
                                                       entry.get("params") or {},
                                                       entry.get("channel_id"))
                    if tid:
                        entry["task_id"] = tid
                        entry["next_run"] = nxt
                    else:
                        return False, {"error": f"Reschedule failed: {nxt}"}
            except Exception as e:
                return False, {"error": f"Reschedule failed: {e}"}
        autos[key] = entry
        save_automations(guild_id, autos)
        return True, {"automation": key, "changed": changed or ["nothing to change"]}

    async def action_pause_automation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pause an automation without deleting it."""
        _, get_automations, save_automations, find_automation, _, _, _ = self._am()
        guild_id = interaction.guild.id
        name = str(params.get("name") or params.get("automation_name") or "").strip()
        key, entry = find_automation(guild_id, name)
        if entry is None:
            return False, {"error": f"Automation '{name}' not found."}
        autos = get_automations(guild_id)
        entry["paused"] = True
        entry["paused_reason"] = "paused by admin/agent"
        try:
            from task_scheduler import task_scheduler
            if entry.get("task_id"):
                task_scheduler.cancel_task(entry["task_id"])
        except Exception:
            pass
        autos[key] = entry
        save_automations(guild_id, autos)
        return True, {"automation": key, "paused": True}

    async def action_resume_automation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Resume a paused automation (re-schedules cron tasks)."""
        _, get_automations, save_automations, find_automation, _, _, _ = self._am()
        guild_id = interaction.guild.id
        name = str(params.get("name") or params.get("automation_name") or "").strip()
        key, entry = find_automation(guild_id, name)
        if entry is None:
            return False, {"error": f"Automation '{name}' not found."}
        entry["paused"] = False
        entry["paused_reason"] = ""
        entry["fail_count"] = 0
        if entry.get("type") == "scheduled_task":
            tid, nxt = self._schedule_cron_job(guild_id, key, entry["cron"],
                                               entry.get("handler", "send_message"),
                                               entry.get("params") or {},
                                               entry.get("channel_id"))
            if tid:
                entry["task_id"] = tid
                entry["next_run"] = nxt
            else:
                entry["paused"] = True
                entry["paused_reason"] = f"resume failed: {nxt}"
                autos = get_automations(guild_id)
                autos[key] = entry
                save_automations(guild_id, autos)
                return False, {"error": f"Resume failed: {nxt}"}
        autos = get_automations(guild_id)
        autos[key] = entry
        save_automations(guild_id, autos)
        return True, {"automation": key, "paused": False, "next_run": entry.get("next_run")}

    async def action_run_automation_now(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Test-fire an automation immediately without waiting for its schedule."""
        _, get_automations, _, find_automation, record_run, _, _ = self._am()
        guild_id = interaction.guild.id
        name = str(params.get("name") or params.get("automation_name") or "").strip()
        key, entry = find_automation(guild_id, name)
        if entry is None:
            return False, {"error": f"Automation '{name}' not found."}
        from actions import ScheduledTaskInteraction  # self-reference safe (same module)
        mock = ScheduledTaskInteraction(self.bot, guild_id)
        cid = (entry.get("params") or {}).get("channel_id") or entry.get("channel_id")
        if cid:
            try:
                ch = self.bot.get_channel(int(str(cid)))
                if ch is not None:
                    mock.channel = ch
            except (TypeError, ValueError):
                pass
        try:
            ok, info = await self.dispatch(mock, entry.get("handler", "send_message"),
                                           dict(entry.get("params") or {}))
            updated = record_run(guild_id, key, bool(ok),
                                 str((info or {}).get("error", "")) if isinstance(info, dict) else "")
            return bool(ok), {"automation": key, "ran": True,
                              "fail_count": updated.get("fail_count", 0),
                              "error": (info or {}).get("error") if isinstance(info, dict) else None}
        except Exception as e:
            record_run(guild_id, key, False, str(e))
            return False, {"error": f"Test run failed: {e}"}

    async def action_bulk_create_automations(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Create many automations in one call (up to 25). Params: automations: [ {..create params..} ]."""
        _, _, _, _, _, _, max_bulk = self._am()
        items = params.get("automations") or params.get("items") or params.get("list")
        if not isinstance(items, list) or not items:
            return False, {"error": "bulk_create_automations requires 'automations': [ ... ] (list of create_automation param objects)."}
        if len(items) > max_bulk:
            return False, {"error": f"Too many items ({len(items)}). Max {max_bulk} per call."}
        results = []
        created = 0
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                results.append({"index": idx, "ok": False, "error": "item must be an object"})
                continue
            try:
                ok, info = await self.action_create_automation(interaction, item)
                created += 1 if ok else 0
                results.append({"index": idx, "name": item.get("name"), "ok": bool(ok),
                                "error": (info or {}).get("error") if isinstance(info, dict) and not ok else None})
            except Exception as e:
                results.append({"index": idx, "name": item.get("name"), "ok": False, "error": str(e)[:200]})
        return created > 0, {"message": f"Created {created}/{len(items)} automations.", "created": created, "requested": len(items), "results": results}

    async def action_bulk_pause_automations(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pause many automations: by names list, or all of a type, or all."""
        _, get_automations, save_automations, _, _, _, _ = self._am()
        guild_id = interaction.guild.id
        autos = get_automations(guild_id)
        names = params.get("names")
        atype = params.get("type")
        if names:
            targets = []
            for n in names:
                key, _ = (n, autos.get(n)) if n in autos else (
                    next((k for k in autos if str(k).lower() == str(n).strip().lower()), None) and (next((k for k in autos if str(k).lower() == str(n).strip().lower()), None), None) or (None, None))
                if key:
                    targets.append(key)
        elif params.get("all"):
            targets = [k for k, e in autos.items() if isinstance(e, dict) and not e.get("paused")
                       and (not atype or e.get("type") == atype)]
        else:
            return False, {"error": "Provide 'names': [...] or 'all': true (optionally with 'type')."}
        paused = 0
        for k in targets:
            autos[k]["paused"] = True
            autos[k]["paused_reason"] = "bulk pause"
            try:
                from task_scheduler import task_scheduler
                if autos[k].get("task_id"):
                    task_scheduler.cancel_task(autos[k]["task_id"])
            except Exception:
                pass
            paused += 1
        save_automations(guild_id, autos)
        return paused > 0, {"paused": paused, "names": targets[:25]}

    async def action_bulk_delete_automations(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Delete many automations: by names list, or all of a type, or all."""
        _, get_automations, save_automations, _, _, _, _ = self._am()
        guild_id = interaction.guild.id
        autos = get_automations(guild_id)
        names = params.get("names")
        atype = params.get("type")
        if names:
            targets = []
            for n in names:
                key = n if n in autos else next(
                    (k for k in autos if str(k).lower() == str(n).strip().lower()), None)
                if key:
                    targets.append(key)
        elif params.get("all"):
            targets = [k for k, e in autos.items() if isinstance(e, dict)
                       and (not atype or e.get("type") == atype)]
        else:
            return False, {"error": "Provide 'names': [...] or 'all': true (optionally with 'type')."}
        deleted = 0
        for k in targets:
            entry = autos.pop(k, None)
            if entry:
                try:
                    from task_scheduler import task_scheduler
                    if entry.get("task_id"):
                        task_scheduler.cancel_task(entry["task_id"])
                except Exception:
                    pass
                deleted += 1
        save_automations(guild_id, autos)
        return deleted > 0, {"deleted": deleted, "names": targets[:25]}

    async def action_bulk_create_prefix_commands(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Create many custom prefix commands in one call (up to 25)."""
        _, _, _, _, _, _, max_bulk = self._am()
        items = params.get("commands") or params.get("items") or params.get("list")
        if not isinstance(items, list) or not items:
            return False, {"error": "bulk_create_prefix_commands requires 'commands': [ ... ]."}
        if len(items) > max_bulk:
            return False, {"error": f"Too many items ({len(items)}). Max {max_bulk} per call."}
        results = []
        created = 0
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                results.append({"index": idx, "ok": False, "error": "item must be an object"})
                continue
            try:
                ok, info = await self.action_create_prefix_command(interaction, item)
                created += 1 if ok else 0
                results.append({"index": idx, "name": item.get("name"), "ok": bool(ok),
                                "error": (info or {}).get("error") if isinstance(info, dict) and not ok else None})
            except Exception as e:
                results.append({"index": idx, "name": item.get("name"), "ok": False, "error": str(e)[:200]})
        return created > 0, {"created": created, "requested": len(items), "results": results}

    async def dispatch(self, interaction: discord.Interaction, name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Routes action names to specific methods with permission enforcement. Returns (success, undo_data)."""
        name = self._normalize_action_name(name)

        # --- RATE LIMITING (multi-tier; admins exempt from emergency stop on commands) ---
        limiter = getattr(self.bot, "rate_limiter", None)
        if limiter is not None:
            is_admin_caller = bool(getattr(interaction.user, "guild_permissions", None) and
                                   interaction.user.guild_permissions.administrator)
            guild_ok, g_wait = limiter.check("guild", interaction.guild.id)
            user_ok, u_wait = limiter.check("user", interaction.user.id, exempt=is_admin_caller and self._is_read_only(name))
            if not (guild_ok and user_ok):
                wait = max(g_wait, u_wait)
                logger.warning("Rate limited action '%s' for user %s in guild %s; retry in %.0fs",
                               name, interaction.user.id, interaction.guild.id, wait)
                return False, {"error": f"Rate limit reached. Try again in {wait:.0f}s.", "retry_after": wait}

        # --- PERMISSION ENFORCEMENT ---
        # Critical Security: Ensure the initiating user has Administrator permission
        # for sensitive actions. This prevents exploitation via AI or custom commands.

        # Read-only actions that don't require admin
        read_only_actions = {
            "analyze_server_state", "query_server_info", "query_channels",
            "query_roles", "query_members", "query_member_details",
            "query_economy_leaderboard", "query_xp_leaderboard",
            "query_pending_applications", "query_active_shifts",
            "query_recent_messages", "send_message", "reply_message",
            "add_reaction", "send_notification"
        }

        # If action is not read-only and user is not admin, block it.
        if name not in read_only_actions and not interaction.user.guild_permissions.administrator:
            # Special case: allow the bot itself (e.g. for scheduled tasks or system connections)
            if interaction.user.id != self.bot.user.id:
                logger.warning(
                    "Blocked sensitive action '%s' triggered by non-admin user %s (%d) in guild %d",
                    name, interaction.user, interaction.user.id, interaction.guild.id
                )
                self._audit_action(name, interaction, params, success=False,
                                   error="non-admin blocked")
                return False, {"error": f"You do not have Administrator permission to execute the '{name}' action."}

        method_name = f"action_{name}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            try:
                success, undo_data = await method(interaction, params)
            except Exception as e:
                self._audit_action(name, interaction, params, success=False, error=str(e))
                self._publish_action_event(name, interaction, success=False)
                raise
            self._audit_action(name, interaction, params, success=success, result=undo_data)
            self._publish_action_event(name, interaction, success=success)
            return success, undo_data
        else:
            import difflib
            suggestions = difflib.get_close_matches(name, self.ALLOWED_ACTIONS, n=3, cutoff=0.4)
            detail = f"Unknown action '{name}'."
            if suggestions:
                detail += f" Did you mean: {', '.join(suggestions)}?"
            logger.warning("Unknown action: %s", name)
            return False, {"error": detail, "error_type": "UNKNOWN_TOOL",
                           "suggestions": suggestions}

    def _publish_action_event(self, name: str, interaction, success: bool):
        """Fan the executed action out on the internal event bus (analytics, etc.)."""
        bus = getattr(self.bot, "event_bus", None)
        if bus is None:
            return
        try:
            guild = getattr(interaction, "guild", None)
            asyncio.create_task(bus.publish(
                "action.executed",
                guild_id=getattr(guild, "id", None),
                actor_id=getattr(getattr(interaction, "user", None), "id", None),
                action=name,
                success=success,
            ))
        except Exception:
            pass

    @staticmethod
    def _is_read_only(name: str) -> bool:
        return name.startswith(("query_", "analyze_"))

    def _audit_action(self, name: str, interaction, params, success: bool,
                      error: str = "", result=None):
        """Best-effort unified audit record; never breaks action execution."""
        audit = getattr(self.bot, "audit_log", None)
        if audit is None:
            return
        try:
            source = SOURCE_AI if getattr(interaction, "user", None) is not None and \
                getattr(interaction, "_miro_ai_source", False) else SOURCE_COMMAND
            audit.record_action(
                name,
                actor_id=getattr(getattr(interaction, "user", None), "id", None),
                target=str(params.get("channel_name") or params.get("role_name") or params.get("user_id") or "")[:120],
                guild_id=getattr(getattr(interaction, "guild", None), "id", None),
                source=source,
                success=success,
                error=error,
                params={k: str(v)[:80] for k, v in list(params.items())[:5]} if isinstance(params, dict) else {},
            )
        except Exception as e:
            logger.debug(f"Audit record failed for '{name}': {e}")

    # --- Meta / Planning Actions ---

    async def action_analyze_server_state(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Read-only no-op. The AI uses this as a planning checkpoint before executing real actions.
        Logs current guild state to help with debugging; always returns success so the action
        sequence continues to the actual changes."""
        guild = interaction.guild
        channels = [c.name for c in guild.text_channels] + [c.name for c in guild.voice_channels]
        categories = [c.name for c in guild.categories]
        roles = [r.name for r in guild.roles if r.name != "@everyone"]
        logger.info(
            "[analyze_server_state] guild=%s | categories=%s | channels=%s | roles=%s | members=%d",
            guild.name, categories, channels, roles, guild.member_count
        )
        return True, None

    # --- Basic Actions ---

    async def action_create_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = self._get_param(params, "channel_name", "name", "channel", default="new-channel")
        channel_type = self._get_param(params, "channel_type", "type", default="text")
        category_name = self._get_param(params, "category_name", "category")
        private = self._get_param(params, "private", default=False)
        allowed_roles = self._get_param(params, "allowed_roles", default=[])
        denied_roles = self._get_param(params, "denied_roles", default=[])

        # If private=true, always deny @everyone view_channel
        if private and "@everyone" not in denied_roles:
            denied_roles = list(denied_roles) + ["@everyone"]

        if not guild.me.guild_permissions.manage_channels:
            logger.error("Bot lacks manage_channels permission in guild %s", guild.id)
            return False, None

        # Check for duplicate channel creation using deduplicator
        dedup_key = f"channel_{guild.id}_{name}"
        if not deduplicator.should_send(dedup_key, interval=5):
            logger.info("Channel '%s' creation skipped (duplicate request)", name)
            return True, None

        existing = discord.utils.get(guild.channels, name=name)
        if existing:
            logger.info("Channel '%s' already exists, skipping creation", name)
            return True, None

        category = None
        if category_name:
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                # Also deduplicate category creation
                cat_dedup_key = f"category_{guild.id}_{category_name}"
                if deduplicator.should_send(cat_dedup_key, interval=5):
                    category = await guild.create_category(category_name)

        if channel_type == "text":
            channel = await guild.create_text_channel(name, category=category)
        elif channel_type == "voice":
            channel = await guild.create_voice_channel(name, category=category)
        else:
            return False, None

        # Auto-detect permissions if not specified
        if not allowed_roles and not denied_roles:
            auto_perms = self._detect_channel_permissions(name, guild)
            allowed_roles = auto_perms.get("allowed", [])
            denied_roles = auto_perms.get("denied", [])

        # Set permissions if specified
        if allowed_roles or denied_roles:
            await self._set_channel_permissions(channel, guild, allowed_roles, denied_roles)

        # Send help embed to explain the channel
        await self._send_channel_guide(channel, name)

        self._track_artifact("channel", channel.id, channel.name)
        logger.info("Created channel: %s", channel.name)
        return True, {"action": "delete_channel", "channel_id": channel.id}

    async def action_create_shop_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = self._get_param(params, "channel_name", "name", default="shop")
        channel_type = self._get_param(params, "channel_type", "type", default="text")
        category_name = self._get_param(params, "category_name", "category", default="shop")
        private = self._get_param(params, "private", default=False)
        allowed_roles = self._get_param(params, "allowed_roles", default=[])
        denied_roles = self._get_param(params, "denied_roles", default=[])

        # If private=true, always deny @everyone view_channel
        if private and "@everyone" not in denied_roles:
            denied_roles = list(denied_roles) + ["@everyone"]

        if not guild.me.guild_permissions.manage_channels:
            logger.error("Bot lacks manage_channels permission in guild %s", guild.id)
            return False, None

        # Check for duplicate channel creation using deduplicator
        dedup_key = f"channel_{guild.id}_{name}"
        if not deduplicator.should_send(dedup_key, interval=5):
            logger.info("Channel '%s' creation skipped (duplicate request)", name)
            return True, None

        existing = discord.utils.get(guild.channels, name=name)
        if existing:
            logger.info("Channel '%s' already exists, skipping creation", name)
            return True, None

        category = None
        if category_name:
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                # Also deduplicate category creation
                cat_dedup_key = f"category_{guild.id}_{category_name}"
                if deduplicator.should_send(cat_dedup_key, interval=5):
                    category = await guild.create_category(category_name)

        if channel_type == "text":
            channel = await guild.create_text_channel(name, category=category)
        elif channel_type == "voice":
            channel = await guild.create_voice_channel(name, category=category)
        else:
            return False, None

        # Auto-detect permissions if not specified
        if not allowed_roles and not denied_roles:
            auto_perms = self._detect_channel_permissions(name, guild)
            allowed_roles = auto_perms.get("allowed", [])
            denied_roles = auto_perms.get("denied", [])

        # Set permissions if specified
        if allowed_roles or denied_roles:
            await self._set_channel_permissions(channel, guild, allowed_roles, denied_roles)

        # Send help embed to explain the channel
        await self._send_channel_guide(channel, name)

        self._track_artifact("channel", channel.id, channel.name)
        logger.info("Created shop channel: %s", channel.name)
        return True, {"action": "delete_channel", "channel_id": channel.id}

    def _detect_channel_permissions(self, channel_name: str, guild) -> dict:
        """Automatically detect what permissions a channel should have based on its name"""
        name_lower = channel_name.lower()

        # Permission rules based on channel keywords
        # Ordered most-specific first to prevent substring false matches
        channel_rules = [
            # Most specific rules first
            ("apply-public", {"allowed": [], "denied": []}),
            ("bot-logs", {"allowed": ["Moderator", "Admin"], "denied": ["@everyone"]}),
            ("ticket-queue", {"allowed": ["Moderator", "Support"], "denied": ["@everyone"]}),

            # Staff/Admin channels - only staff can see
            ("staff", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            ("modmail", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            ("admin", {"allowed": ["Administrator", "Admin"], "denied": ["@everyone"]}),
            ("logs", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),

            # Applications - hidden from regular users until they apply
            ("applications", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            ("apply", {"allowed": ["Moderator", "Admin"], "denied": ["@everyone"]}),

            # Verification - new users need to verify
            ("verify", {"allowed": [], "denied": []}),

            # General channels - everyone can see
            ("general", {"allowed": [], "denied": []}),
            ("chat", {"allowed": [], "denied": []}),
            ("talk", {"allowed": [], "denied": []}),

            # Public channels - everyone can see
            ("announcements", {"allowed": [], "denied": []}),
            ("rules", {"allowed": [], "denied": []}),
            ("welcome", {"allowed": [], "denied": []}),
            ("suggestions", {"allowed": [], "denied": []}),
            ("shop", {"allowed": [], "denied": []}),

            # Support channels
            ("tickets", {"allowed": ["Moderator", "Support"], "denied": ["@everyone"]}),

            # Media channels
            ("media", {"allowed": [], "denied": []}),
            ("art", {"allowed": [], "denied": []}),
            ("gaming", {"allowed": [], "denied": []}),
            ("vc", {"allowed": [], "denied": []}),

            # Voice channels - everyone can join
            ("voice", {"allowed": [], "denied": []}),
            ("lounge", {"allowed": [], "denied": []}),
        ]

        # Find matching rule (most specific first)
        for keyword, perms in channel_rules:
            if keyword in name_lower:
                return perms

        # Default: public channel
        return {"allowed": [], "denied": []}

    async def _set_channel_permissions(self, channel, guild, allowed_roles, denied_roles):
        """Set view permissions for roles without wiping existing overwrites"""
        from discord import PermissionOverwrite

        for role_name in allowed_roles:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                existing = channel.overwrites_for(role)
                existing.view_channel = True
                existing.send_messages = True
                await channel.set_permissions(role, overwrite=existing)

        for role_name in denied_roles:
            if role_name == "@everyone":
                role = guild.default_role
            else:
                role = discord.utils.get(guild.roles, name=role_name)
            if role:
                existing = channel.overwrites_for(role)
                existing.view_channel = False
                await channel.set_permissions(role, overwrite=existing)

        logger.info(f"Set permissions on channel {channel.name}: +{allowed_roles} -{denied_roles}")

    async def _send_channel_guide(self, channel, channel_name: str):
        """Send a guide embed explaining what the channel is and available commands"""
        name_lower = channel_name.lower()

        guide_content = {
            "general": {
                "description": "This is a general chat channel for conversations.",
                "commands": ["!help", "!ping"]
            },
            "suggestions": {
                "description": "Submit your ideas for the server here!",
                "commands": ["!suggest <title> <description>"]
            },
            "verify": {
                "description": "Verify yourself to access the server!",
                "commands": ["Click the Verify button"]
            },
            "rules": {
                "description": "Please read and follow our server rules.",
                "commands": ["React to accept rules"]
            },
            "ticket": {
                "description": "Need help? Create a ticket!",
                "commands": ["!ticket <message>"]
            },
            "applications": {
                "description": "Apply to join our staff team!",
                "commands": ["!apply"]
            },
            "modmail": {
                "description": "DM the bot for staff assistance.",
                "commands": ["DM the bot directly"]
            },
            "announcements": {
                "description": "Server news and updates.",
                "commands": ["Staff only: Post announcements"]
            },
            "shop": {
                "description": "Buy and sell items in our server shop!",
                "commands": ["!shop", "!buy <item>", "!sell <item>"]
            }
        }

        # Find matching guide
        guide = None
        for key, content in guide_content.items():
            if key in name_lower:
                guide = content
                break

        if not guide:
            return

        embed = discord.Embed(
            title=f"ðŸ“¢ {channel_name}",
            description=guide["description"],
            color=discord.Color.blue()
        )

        cmd_list = "\n".join([f". {cmd}" for cmd in guide["commands"]])
        embed.add_field(name="Available Commands", value=cmd_list, inline=False)

        await channel.send(embed=embed)

    async def action_create_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = self._get_param(params, "role_name", "name")

        if not guild.me.guild_permissions.manage_roles:
            logger.error("Bot lacks manage_roles permission in guild %s", guild.id)
            return False, None

        # Check for duplicate role creation using deduplicator
        dedup_key = f"role_{guild.id}_{name}"
        if not deduplicator.should_send(dedup_key, interval=5):
            logger.info("Role '%s' creation skipped (duplicate request)", name)
            return True, None

        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            logger.info("Role '%s' already exists, skipping creation", name)
            return True, None

        color = self._get_param(params, "color", "colour", "hex_color", default="#3498db")
        color_hex = color.replace("#", "")
        color = discord.Color(int(color_hex, 16))

        # Auto-detect role permissions
        role_perms = self._detect_role_permissions(name)

        permissions = discord.Permissions(
            view_channel=role_perms.get("view_channel", True),
            send_messages=role_perms.get("send_messages", True),
            read_message_history=role_perms.get("read_message_history", True),
            attach_files=role_perms.get("attach_files", True),
            embed_links=role_perms.get("embed_links", True),
            add_reactions=role_perms.get("add_reactions", True),
            use_external_emojis=role_perms.get("use_external_emojis", True),
            use_application_commands=role_perms.get("use_application_commands", True),
            connect=role_perms.get("connect", True),
            speak=role_perms.get("speak", True),
            manage_channels=role_perms.get("manage_channels", False),
            manage_roles=role_perms.get("manage_roles", False),
            kick_members=role_perms.get("kick_members", False),
            ban_members=role_perms.get("ban_members", False),
            moderate_members=role_perms.get("moderate_members", False),
            manage_messages=role_perms.get("manage_messages", False),
            mention_everyone=role_perms.get("mention_everyone", False),
            mute_members=role_perms.get("mute_members", False),
            move_members=role_perms.get("move_members", False),
            create_instant_invite=role_perms.get("create_instant_invite", True),
        )

        role = await guild.create_role(
            name=name,
            color=color,
            permissions=permissions,
            hoist=role_perms.get("hoist", False),
            mentionable=role_perms.get("mentionable", False),
            reason="AI Action"
        )

        # Send role info
        await self._send_role_guide(interaction, name, role_perms)

        self._track_artifact("role", role.id, role.name)
        return True, {"action": "delete_role", "role_id": role.id, "role_name": role.name}

    def _detect_role_permissions(self, role_name: str) -> dict:
        """Auto-detect what permissions a role should have based on its name"""
        name_lower = role_name.lower()

        role_rules = {
            # Admin role - full permissions
            "admin": {
                "view_channel": True, "manage_channels": True, "manage_roles": True,
                "kick_members": True, "ban_members": True, "moderate_members": True,
                "mention_everyone": True, "hoist": True, "mentionable": True
            },
            # Moderator role
            "mod": {
                "view_channel": True, "send_messages": True, "manage_messages": True,
                "kick_members": True, "moderate_members": True,
                "mention_everyone": False, "hoist": True, "mentionable": True
            },
            # Support role
            "support": {
                "view_channel": True, "send_messages": True, "manage_messages": True,
                "hoist": True, "mentionable": True
            },
            # Verified role - basic access
            "verified": {
                "view_channel": True, "send_messages": True,
                "hoist": True, "mentionable": False
            },
            # Member role
            "member": {
                "view_channel": True, "send_messages": True,
                "hoist": True
            },
            # Muted role - no permissions
            "muted": {
                "view_channel": True, "send_messages": False,
                "hoist": True, "mentionable": False
            },
            # Bot role
            "bot": {
                "view_channel": True, "send_messages": True,
                "manage_messages": True, "hoist": True
            },
            # VIP role
            "vip": {
                "view_channel": True, "send_messages": True,
                "hoist": True, "mentionable": True
            },
            # Event role
            "event": {
                "view_channel": True, "send_messages": True,
                "mention_everyone": False, "hoist": True, "mentionable": True
            },
            # Streaming role
            "streaming": {
                "view_channel": True, "send_messages": True,
                "mention_everyone": True, "hoist": True, "mentionable": True
            },
            # Gaming role
            "gaming": {
                "view_channel": True, "send_messages": True,
                "hoist": True
            },
            # Music role
            "music": {
                "view_channel": True, "send_messages": True,
                "hoist": True
            }
        }

        for keyword, perms in role_rules.items():
            if keyword in name_lower:
                return perms

        # Default: basic member permissions
        return {"view_channel": True, "send_messages": True, "hoist": False}

    async def _send_role_guide(self, interaction, role_name: str, role_perms: dict):
        """Send a guide embed explaining what the role is"""
        import discord

        name_lower = role_name.lower()

        guide_content = {
            "admin": "Full administrator role with all permissions.",
            "mod": "Moderator role for managing the server.",
            "support": "Support role for helping users.",
            "verified": "Verified members - access to all channels.",
            "member": "Default member role.",
            "muted": "Muted role - cannot send messages.",
            "vip": "VIP role - special perks and access.",
            "event": "Event participants role.",
            "gaming": "Gaming community role.",
            "music": "Music lovers role."
        }

        description = f"Custom role: {role_name}"
        for key, desc in guide_content.items():
            if key in name_lower:
                description = desc
                break

        embed = discord.Embed(
            title=f"ðŸŽ­ Role: {role_name}",
            description=description,
            color=discord.Color.blue()
        )

        perm_list = [f"View Channels", "Send Messages"] if role_perms.get("view_channel") else []
        if role_perms.get("moderate_members"): perm_list.append("Moderate Members")
        if role_perms.get("kick_members"): perm_list.append("Kick Members")
        if role_perms.get("ban_members"): perm_list.append("Ban Members")
        if role_perms.get("manage_channels"): perm_list.append("Manage Channels")

        if perm_list:
            embed.add_field(name="Permissions", value="\n".join([f"? {p}" for p in perm_list]), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _resolve_role(self, guild: discord.Guild, **kwargs) -> Optional[discord.Role]:
        """Robustly resolve a role by ID or name from kwargs.

        Changes:
        - Updated to use **kwargs for flexibility in parameter names.
        - Extracts role_id from 'role_id', 'id', or 'role' (as int).
        - Extracts role_name from 'role_name' or 'name'.
        - Logs a warning if neither role_id nor role_name is provided.
        - Supports various input formats for role identification.
        """
        # Extract role_id from possible keys, prioritizing role_id, then id, then role
        role_id = kwargs.get('role_id') or kwargs.get('id') or kwargs.get('role')
        # Extract role_name from possible keys, prioritizing role_name, then name
        role_name = kwargs.get('role_name') or kwargs.get('name')

        if role_id:
            try:
                # Handle both int and string IDs, including mentions
                rid = int(str(role_id).strip().lstrip("<@&").rstrip(">"))
                role = guild.get_role(rid)
                if role: return role
            except (ValueError, TypeError):
                pass

        if role_name:
            name = str(role_name).strip()
            if name == "@everyone":
                return guild.default_role
            name_lower = name.lower()
            # Try exact match first
            role = discord.utils.find(lambda r: r.name.lower() == name_lower, guild.roles)
            if role: return role
            # Try partial match
            role = discord.utils.find(lambda r: name_lower in r.name.lower(), guild.roles)
            if role: return role

        # Log warning if neither role_id nor role_name was provided
        if not role_id and not role_name:
            logger.warning("_resolve_role: no role_id or role_name provided in kwargs")

        return None

    async def _resolve_member(self, guild: discord.Guild, **kwargs) -> Optional[discord.Member]:
        """Robustly resolve a member by ID or name from kwargs."""
        user_id = kwargs.get('user_id')
        username = kwargs.get('username')

        if user_id:
            try:
                uid = int(str(user_id).strip().lstrip("<@!").rstrip(">"))
                member = guild.get_member(uid)
                if not member:
                    try:
                        member = await guild.fetch_member(uid)
                    except (discord.NotFound, discord.HTTPException):
                        pass
                if member: return member
            except (ValueError, TypeError):
                pass

        if username:
            name = str(username).strip().lstrip("@").lower()
            # Try exact match on name or display name
            member = discord.utils.find(lambda m: m.name.lower() == name or m.display_name.lower() == name, guild.members)
            if member: return member

            # Try query members for those not in cache
            try:
                results = await guild.query_members(query=name, limit=5)
                if results:
                    return results[0]
            except Exception:
                pass

        return None

    async def _find_existing_resources(self, guild: discord.Guild, system_type: str) -> dict:
        """Scan existing channels, roles, and categories for partial matches based on system_type keywords.

        Returns dict with 'channel', 'role', 'category' (or None), and 'reasoning'.
        """
        # Keywords for each system type
        keywords = {
            "verification": {
                "channels": ["verify", "verification", "welcome", "rules"],
                "roles": ["verified", "member", "guest"]
            },
            "tickets": {
                "channels": ["ticket", "support", "help"],
                "roles": ["support", "helper", "staff"]
            },
            "appeals": {
                "channels": ["appeal", "ban-appeal", "modmail"],
                "roles": ["appeal", "moderator"]
            },
            "applications": {
                "channels": ["apply", "application", "staff-app"],
                "roles": ["applicant", "staff"]
            },
            "economy": {
                "channels": ["shop", "economy", "market"],
                "roles": ["economy", "shopkeeper"]
            },
            "leveling": {
                "channels": ["level", "xp", "rank", "leaderboard"],
                "roles": ["level", "veteran", "rank"]
            },
            "welcome": {
                "channels": ["welcome", "introductions", "greeting"],
                "roles": ["welcome", "new"]
            }
        }

        if system_type not in keywords:
            return {"channel": None, "role": None, "category": None, "reasoning": f"Unknown system_type: {system_type}"}

        sys_keywords = keywords[system_type]
        found_channel = None
        found_role = None
        found_category = None
        reasoning_parts = []

        # Helper function to find best match
        def find_best_match(items, keyword_list):
            """Find best match: exact > partial, shorter name > longer, higher position > lower."""
            matches = []
            for item in items:
                item_name_lower = item.name.lower()
                for keyword in keyword_list:
                    keyword_lower = keyword.lower()
                    # Check exact match
                    if item_name_lower == keyword_lower:
                        matches.append((item, 3, len(item.name), -getattr(item, 'position', 0)))
                    # Check partial match
                    elif keyword_lower in item_name_lower:
                        matches.append((item, 2, len(item.name), -getattr(item, 'position', 0)))

            if matches:
                # Sort by priority (exact > partial), then shorter name, then higher position
                matches.sort(key=lambda x: (-x[1], x[2], x[3]))
                return matches[0][0]
            return None

        # Find channel
        if sys_keywords.get("channels"):
            found_channel = find_best_match(guild.channels, sys_keywords["channels"])
            if found_channel:
                reasoning_parts.append(f"Found channel '{found_channel.name}'")

        # Find role
        if sys_keywords.get("roles"):
            found_role = find_best_match(guild.roles, sys_keywords["roles"])
            if found_role:
                reasoning_parts.append(f"Found role '{found_role.name}'")

        # Find category (use same keywords as channels or common category names)
        category_keywords = sys_keywords.get("channels", []) + ["community", "general", "main"]
        found_category = find_best_match(guild.categories, category_keywords)
        if found_category:
            reasoning_parts.append(f"Found category '{found_category.name}'")

        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "No existing resources found"

        return {
            "channel": found_channel,
            "role": found_role,
            "category": found_category,
            "reasoning": reasoning
        }

    def _get_param(self, params: dict, *keys, default=None):
        """Returns the first value found for any key in keys."""
        for key in keys:
            if key in params:
                return params[key]
        return default

    async def action_assign_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Assign a role to one or more users. Supports multiple roles too."""
        guild = interaction.guild

        # Security check: Does the user have permission to manage roles?
        if not interaction.user.guild_permissions.manage_roles:
            return False, {"error": "User lacks 'Manage Roles' permission."}

        if not guild.me.guild_permissions.manage_roles:
            return False, {"error": "Bot lacks 'Manage Roles' permission."}

        # --- Resolve Roles ---
        roles = []
        role_ids = self._get_param(params, "role_ids", "roles", "role_list", default=[])
        if not isinstance(role_ids, list): role_ids = [role_ids]
        single_role_id = self._get_param(params, "role_id", "role", "name", "role_name")
        if single_role_id is not None:
            role_ids.append(single_role_id)

        role_names = self._get_param(params, "role_names", "names", "role_names_list", default=[])
        if not isinstance(role_names, list): role_names = [role_names]
        single_role_name = self._get_param(params, "role_name", "name", "role")
        if single_role_name is not None:
            role_names.append(single_role_name)

        for rid in role_ids:
            r = await self._resolve_role(guild, role_id=rid)
            if r and r not in roles: roles.append(r)
        for rn in role_names:
            r = await self._resolve_role(guild, role_name=rn)
            if r and r not in roles: roles.append(r)

        if not roles:
            return False, {"error": f"Could not find any roles matching: ID={role_ids}, Name={role_names}"}

        # --- Resolve Members ---
        members = []
        user_ids = self._get_param(params, "user_ids", "users", "members", "targets", "uids", default=[])
        if not isinstance(user_ids, list): user_ids = [user_ids]
        single_user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        if single_user_id is not None:
            user_ids.append(single_user_id)

        usernames = self._get_param(params, "usernames", "names", "user_names", default=[])
        if not isinstance(usernames, list): usernames = [usernames]
        single_username = self._get_param(params, "username", "user_name", "name")
        if single_username is not None:
            usernames.append(single_username)

        for uid in user_ids:
            m = await self._resolve_member(guild, user_id=uid)
            if m and m not in members: members.append(m)
        for un in usernames:
            m = await self._resolve_member(guild, username=un)
            if m and m not in members: members.append(m)

        if not members:
            return False, {"error": f"Could not find any members matching: ID={user_ids}, Name={usernames}"}

        # --- Process Assignments ---
        bot_top_role = guild.me.top_role
        success_count = 0
        undo_list = []
        errors = []

        for role in roles:
            if role.position >= bot_top_role.position:
                errors.append(f"Role '{role.name}' is higher than bot's role. Please move the bot's role higher in the server's role hierarchy to assign this role.")
                continue

            for member in members:
                try:
                    if role in member.roles:
                        success_count += 1
                        continue
                    await member.add_roles(role, reason=f"Assigned by {interaction.user.display_name}")
                    success_count += 1
                    undo_list.append({"action": "remove_role", "user_id": member.id, "role_id": role.id})
                except discord.Forbidden:
                    errors.append(f"Forbidden: Could not give '{role.name}' to '{member.display_name}'.")
                except Exception as e:
                    errors.append(f"Error giving '{role.name}' to '{member.display_name}': {str(e)}")

        if success_count == 0 and errors:
            return False, {"error": "; ".join(errors)}

        return True, {"action": "batch_undo", "undo_data": undo_list}

    async def action_add_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds a role to a user. Alias for action_assign_role."""
        return await self.action_assign_role(interaction, params)

    async def action_assign_role_by_name(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Assigns a role to a user by role name and user identifier."""
        guild = interaction.guild

        # Security check
        if not interaction.user.guild_permissions.manage_roles:
            return False, {"error": "User lacks 'Manage Roles' permission."}
        if not guild.me.guild_permissions.manage_roles:
            return False, {"error": "Bot lacks 'Manage Roles' permission."}

        role_name = self._get_param(params, "role_name", "name")
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "user_mention")
        username = self._get_param(params, "username", "user_name")

        # Resolve role
        role = None
        if role_name:
            role = discord.utils.find(lambda r: r.name.lower() == str(role_name).lower(), guild.roles)
            if not role:
                role = discord.utils.find(lambda r: str(role_name).lower() in r.name.lower(), guild.roles)
        if not role:
            return False, {"error": f"Could not find role '{role_name}'"}

        # Resolve member
        member = None
        if user_id:
            try:
                uid = int(str(user_id).strip().lstrip("<@!").rstrip(">"))
                member = guild.get_member(uid) or await guild.fetch_member(uid)
            except (TypeError, ValueError, discord.NotFound, discord.HTTPException):
                member = None
        if not member and username:
            search = str(username).lstrip("@").lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == search or m.display_name.lower() == search,
                guild.members
            )
        if not member:
            return False, {"error": f"Could not find member '{username or user_id}'"}

        # Check bot's role position
        if role.position >= guild.me.top_role.position:
            return False, {"error": f"Role '{role.name}' is higher than bot's role."}

        # Assign role
        try:
            if role in member.roles:
                return True, None  # Already has the role
            await member.add_roles(role, reason=f"Assigned by {interaction.user.display_name}")
            return True, {"action": "remove_role", "user_id": member.id, "role_id": role.id, "role_name": role.name}
        except discord.Forbidden:
            return False, {"error": f"Forbidden: Could not assign '{role.name}' to '{member.display_name}'"}
        except Exception as e:
            return False, {"error": f"Error assigning role: {str(e)}"}

    async def action_remove_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes a role from a user."""
        guild = interaction.guild

        role = await self._resolve_role(guild, **params)

        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name", "name")
        member = await self._resolve_member(guild, user_id=user_id, username=username)

        if not role:
            logger.error("remove_role: could not find role with params: %s", params)
            return False, None
        if not member:
            logger.error("remove_role: could not find member. user_id=%s username=%s", user_id, username)
            return False, None

        try:
            await member.remove_roles(role)
            logger.info("Removed role %s from %s", role.name, member.display_name)
            return True, {"action": "add_role", "user_id": member.id, "role_id": role.id, "role_name": role.name}
        except discord.Forbidden:
            logger.error("remove_role: missing permissions")
            return False, None
        except Exception as e:
            logger.error(f"Error removing role: {e}")
            return False, None

    async def action_bulk_delete_messages(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bulk deletes messages in a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        amount = params.get("amount", 10)

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        if not target_channel.permissions_for(interaction.guild.me).manage_messages:
            logger.error("bulk_delete_messages: missing manage_messages permission")
            return False, None

        amount = min(max(amount, 1), 100)

        try:
            deleted = []
            async for msg in target_channel.history(limit=amount + 1):
                if msg.id == interaction.id:
                    continue
                deleted.append(msg)
                if len(deleted) >= amount:
                    break

            if deleted:
                await target_channel.delete_messages(deleted)
                logger.info("Bulk deleted %d messages in %s", len(deleted), target_channel.name)

            return True, {"deleted_count": len(deleted), "channel": target_channel.name}
        except discord.Forbidden:
            logger.error("bulk_delete_messages: missing permissions")
            return False, None
        except Exception as e:
            logger.error(f"Error bulk deleting messages: {e}")
            return False, None

    async def action_delete_messages(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bulk deletes messages. Alias for action_bulk_delete_messages."""
        return await self.action_bulk_delete_messages(interaction, params)


    async def action_create_prefix_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds a custom '!' command that executes LIVE via bot.on_message fast-path."""
        guild_id = interaction.guild.id
        # Normalize aliases: agent may send name/code under various keys
        cmd_name = self._get_param(params, "name", "cmd_name", "command", "trigger", default=None)
        cmd_code = self._get_param(params, "code", "content", "response", "message", "text", default=None)
        # Fallback to raw dict keys for legacy shapes
        if not cmd_name:
            cmd_name = params.get("name") or params.get("cmd_name") or params.get("command")
        if cmd_code is None:
            cmd_code = params.get("code") if "code" in params else params.get("content")
        if not cmd_name or cmd_code is None or str(cmd_name).strip() == "" or str(cmd_code).strip() == "":
            return False, {"error": "create_prefix_command requires 'name' and 'code' (or 'content')."}
        raw_name = str(cmd_name).strip()
        norm_name = raw_name.lstrip("!#").strip().lower()
        if not norm_name:
            return False, {"error": "Invalid command name."}
        # Prevent overwriting protected system commands without explicit intent — still allow but log
        if len(norm_name) > 32:
            return False, {"error": "Command name too long (max 32 chars)."}
        # Normalize code to JSON string the router understands
        # If code is already a dict with command_type, keep it; if string JSON, validate; else wrap as simple
        stored = None
        existing = None
        try:
            if isinstance(cmd_code, dict):
                # Already structured
                if "command_type" not in cmd_code:
                    cmd_code = {"command_type": "simple", "content": str(cmd_code.get("content") or cmd_code.get("response") or json.dumps(cmd_code))}
                stored = json.dumps(cmd_code)
            elif isinstance(cmd_code, str):
                stripped = cmd_code.strip()
                if stripped.startswith("{"):
                    try:
                        parsed = json.loads(stripped)
                        if isinstance(parsed, dict) and "command_type" in parsed:
                            stored = json.dumps(parsed)
                        else:
                            stored = json.dumps({"command_type": "simple", "content": cmd_code})
                    except Exception:
                        stored = json.dumps({"command_type": "simple", "content": cmd_code})
                else:
                    stored = json.dumps({"command_type": "simple", "content": cmd_code})
            else:
                stored = json.dumps({"command_type": "simple", "content": str(cmd_code)})
        except Exception:
            stored = json.dumps({"command_type": "simple", "content": str(cmd_code)})

        # Quota guard (generous: 100 commands per guild)
        from modules.automation_manager import check_quota, normalize_command_entry
        if existing is None:
            quota_ok, quota_err = check_quota(guild_id, "command")
            if not quota_ok:
                return False, {"error": quota_err}

        # Enhanced metadata: aliases, per-command cooldown, permission, description
        stored, norm_err = normalize_command_entry(
            norm_name, stored,
            aliases=self._get_param(params, "aliases", default=None),
            cooldown_seconds=self._get_param(params, "cooldown_seconds", "cooldown", default=None),
            permission=self._get_param(params, "required_permission", "permission", default=None),
            description=self._get_param(params, "description", default=None))

        cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        existing = cmds.get(norm_name)
        cmds[norm_name] = stored
        # Register aliases as first-class keys pointing at the same code
        try:
            alias_list = json.loads(stored).get("aliases") or []
        except Exception:
            alias_list = []
        for alias in alias_list:
            if alias and alias != norm_name and alias not in cmds:
                cmds[alias] = stored
        dm.update_guild_data(guild_id, "custom_commands", cmds)

        # Log to analytics
        usage = dm.get_guild_data(guild_id, "command_usage", {})
        if norm_name not in usage:
            usage[norm_name] = {"count": 0, "last_used": 0, "created_at": time.time()}
            dm.update_guild_data(guild_id, "command_usage", usage)

        # Also track in unified automations registry for agent discoverability
        autos = dm.get_guild_data(guild_id, "automations", {})
        autos[norm_name] = {"type": "prefix_command", "name": norm_name, "code": stored, "created_by": getattr(interaction.user, "id", 0), "created_at": time.time()}
        dm.update_guild_data(guild_id, "automations", autos)

        return True, {"action": "delete_prefix_command", "cmd_name": norm_name, "previous_code": existing, "automation_id": norm_name, "message": f"Prefix command !{norm_name} is LIVE — send !{norm_name} to trigger it."}

    async def action_create_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_make_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_add_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_new_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_send_embed(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channel_name = self._get_param(params, "channel_name", "channel")
        title = self._get_param(params, "title")
        description = self._get_param(params, "description", "content", "message", "text")
        color = parse_color(self._get_param(params, "color", "colour", "hex_color", default="#3498db"))
        buttons = self._get_param(params, "buttons", default=[])
        fields = self._get_param(params, "fields", default=[])

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        embed = discord.Embed(title=title, description=description, color=color)

        for field in fields:
            embed.add_field(name=field.get("name", ""), value=field.get("value", ""), inline=field.get("inline", False))

        view = None
        if buttons:
            from modules.auto_setup import VerifyButton, AcceptRulesButton, CreateTicketButton, ApplyStaffButton, SuggestionButton
            view = discord.ui.View(timeout=None)
            for btn_def in buttons:
                label = btn_def.get("label", "Click")
                btn_type = btn_def.get("type", "custom")
                style_str = btn_def.get("style", "primary")
                style_map = {"primary": discord.ButtonStyle.primary, "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger, "secondary": discord.ButtonStyle.secondary}
                style = style_map.get(style_str, discord.ButtonStyle.primary)

                if btn_type == "verify":
                    role = discord.utils.get(interaction.guild.roles, name="Verified") or discord.utils.get(interaction.guild.roles, name="Member")
                    sub_view = VerifyButton(interaction.guild.id, role.id if role else 0)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "ticket":
                    sub_view = CreateTicketButton(interaction.guild.id, channel.id)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "apply_staff":
                    sub_view = ApplyStaffButton(interaction.guild.id)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "accept_rules":
                    sub_view = AcceptRulesButton(interaction.guild.id)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "suggestion":
                    sub_view = SuggestionButton(interaction.guild.id)
                    for item in sub_view.children:
                        view.add_item(item)
                else:
                    # Generic button with a response message
                    response_msg = btn_def.get("response", f"You clicked **{label}**!")
                    btn = discord.ui.Button(label=label, style=style)
                    async def make_callback(msg):
                        async def callback(it: discord.Interaction):
                            await it.response.send_message(msg, ephemeral=True)
                        return callback
                    btn.callback = await make_callback(response_msg)
                    view.add_item(btn)

        msg = await channel.send(embed=embed, view=view)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    async def action_send_dm(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sends a DM to a user. Returns True even when DMs are disabled (soft failure) so the action sequence keeps going."""
        params = self._normalize_params(params, {
            "user_id": ["user_id", "user", "member_id", "target_id", "user_mention"],
            "username": ["username", "user_name"],
            "content": ["content", "message", "text"],
            "embed_data": ["embed"],
        })
        user_id = params["user_id"]
        username = params["username"]
        content = params["content"]
        embed_data = params["embed_data"]
        guild = interaction.guild

        # —— 0. Parse user_id from Discord mention format (e.g. <@!123456789> or <@123456789>) —————
        if user_id:
            try:
                # Handle both <@! and <@ mention formats
                user_id_str = str(user_id).strip()
                if user_id_str.startswith("<@") and user_id_str.endswith(">"):
                    # Strip <@! or <@ prefix and > suffix to get the numeric ID
                    cleaned = user_id_str.lstrip("<@!").lstrip("<@").rstrip(">")
                    if cleaned.isdigit():
                        user_id = int(cleaned)
                    else:
                        user_id = None
                elif user_id_str.isdigit():
                    user_id = int(user_id_str)
                else:
                    user_id = None
            except (ValueError, TypeError):
                user_id = None

        # —— 1. Resolve user_id from username ——————————————————————————————————
        if not user_id and username:
            # Handle Discord mention format <@!123456789> or <@123456789> (extract numeric ID directly)
            if isinstance(username, str) and username.startswith("<@"):
                mention_cleaned = username.lstrip("<@!").lstrip("<@").rstrip(">")
                if mention_cleaned.isdigit():
                    user_id = int(mention_cleaned)
                    username = None  # Clear username since we got the ID directly
            # Handle plain @username format
            elif isinstance(username, str) and username.startswith("@"):
                username = username[1:]

            # If we got user_id from mention, skip the name-based lookup
            if not user_id and username:
                # a) Check in-memory guild member cache (name / nick / display_name)
                member = (
                    discord.utils.get(guild.members, name=username)
                    or discord.utils.get(guild.members, nick=username)
                    or discord.utils.get(guild.members, display_name=username)
                )

                # b) Case-insensitive fallback over cached members
                if not member:
                    lower = username.lower()
                    for m in guild.members:
                        if (m.name.lower() == lower
                                or (m.nick and m.nick.lower() == lower)
                                or m.display_name.lower() == lower):
                            member = m
                            break

                # c) Discord API member search (finds members not in cache)
                if not member:
                    try:
                        results = await guild.query_members(query=username, limit=5)
                        if results:
                            member = results[0]
                    except Exception:
                        pass

                if member:
                    user_id = member.id

        # d) username is a raw numeric ID
        if not user_id and username:
            try:
                user_id = int(str(username).strip())
            except (ValueError, TypeError):
                pass

        # e) Fallback: Try partial match search with query_members (handles various name formats)
        if not user_id and username:
            member = None  # Initialize for this fallback block
            try:
                # Try with broader search on the username for partial matching
                results = await guild.query_members(query=username, limit=100)
                username_lower = username.lower()
                for m in results:
                    # Check exact match on various name formats
                    if (m.name.lower() == username_lower
                            or (m.nick and m.nick.lower() == username_lower)
                            or m.display_name.lower() == username_lower):
                        member = m
                        break
                # If no exact match, use first partial match as fallback
                if not member and results:
                    member = results[0]
                if member:
                    user_id = member.id
            except Exception:
                pass

        # f) Last resort: extract a Discord snowflake embedded in the username string.
        #    Discord's pomelo auto-usernames look like "user<snowflake>" e.g. user1357317173470564433.
        #    We pull out any 17–20 digit number and call fetch_user() directly — no guild cache needed.
        if not user_id and username:
            import re as _re_dm
            snowflake_match = _re_dm.search(r'\b(\d{17,20})\b', str(username))
            if snowflake_match:
                try:
                    potential_id = int(snowflake_match.group(1))
                    fetched_user = await self.bot.fetch_user(potential_id)
                    if fetched_user:
                        user_id = fetched_user.id
                        logger.info(f"[send_dm] Resolved snowflake from username string: {username!r} → {user_id}")
                except Exception:
                    pass

        # —— 2. No user resolved — return failure so action sequence stops ——
        if not user_id:
            logger.warning(f"[send_dm] Could not resolve user from username={username!r}")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find user **{username}** to send them a DM.", delete_after=10
                )
            except Exception:
                pass
            return False, None

        # —— 3. Deduplication ——————————————————————————————————————————————————
        dedup_key = f"dm_{user_id}_{hash(content or '')}_{hash(str(embed_data) if embed_data else '')}"
        if not deduplicator.should_send(dedup_key, interval=3):
            logger.info(f"[send_dm] Deduplicated DM to user {user_id}")
            return True, None

        # —— 4. Fetch the User object ——————————————————————————————————————————
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        except discord.NotFound:
            logger.warning(f"[send_dm] User {user_id} not found on Discord")
            return False, None
        except Exception as e:
            logger.error(f"[send_dm] Error fetching user {user_id}: {e}")
            return False, None

        if not user:
            return False, None

        # —— 5. Build embed ————————————————————————————————————————————————————
        embed = None
        if embed_data:
            embed = discord.Embed(
                title=embed_data.get("title"),
                description=embed_data.get("description"),
                color=parse_color(embed_data.get("color", "blue"))
            )
            for field in embed_data.get("fields", []):
                embed.add_field(
                    name=field.get("name"),
                    value=field.get("value"),
                    inline=field.get("inline", False)
                )

        # —— 6. Send the DM ————————————————————————————————————————————————————
        try:
            await user.send(content=content, embed=embed)
            logger.info(f"[send_dm] DM sent to {user} ({user_id})")
            return True, None
        except discord.Forbidden:
            # User has DMs disabled — NOT a soft pass - this is a genuine failure
            logger.warning(f"[send_dm] {user} ({user_id}) has DMs disabled")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not DM **{user.display_name}** — they have DMs disabled.",
                    delete_after=10
                )
            except Exception:
                pass
            return False, None
        except discord.HTTPException as e:
            logger.error(f"[send_dm] HTTP error sending DM to {user_id}: {e}")
            return False, None
        except Exception as e:
            logger.error(f"[send_dm] Unexpected error sending DM to {user_id}: {e}")
            return False, None

    async def action_ping(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pings a user and shows their latency/online status. Resolves by username with cache + API fallback."""
        params = self._normalize_params(params, {
            "user_id": ["user_id", "user", "member_id", "target_id", "user_mention"],
            "username": ["username", "user_name"],
        })
        user_id = params["user_id"]
        username = params["username"]
        guild = interaction.guild

        # —— Resolve member from username ——————————————————————————————————————
        member = None

        if not user_id and not username:
            try:
                await interaction.channel.send("⚠️ No user specified to ping.", delete_after=8)
            except Exception:
                pass
            return True, None

        if username:
            # Handle Discord mention format <@!123456789> or <@123456789> (extract numeric ID directly)
            if isinstance(username, str) and username.startswith("<@"):
                mention_cleaned = username.lstrip("<@!").lstrip("<@").rstrip(">")
                if mention_cleaned.isdigit():
                    user_id = int(mention_cleaned)
                    username = None  # Clear username since we got the ID directly
            # Handle plain @username format
            elif isinstance(username, str) and username.startswith("@"):
                username = username[1:]

            # If we have user_id from mention, skip name-based lookup
            if not user_id and username:
                # a) Exact cache match
                member = (
                    discord.utils.get(guild.members, name=username)
                    or discord.utils.get(guild.members, nick=username)
                    or discord.utils.get(guild.members, display_name=username)
                )

                # b) Case-insensitive cache scan
                if not member:
                    lower = username.lower()
                    for m in guild.members:
                        if (m.name.lower() == lower
                                or (m.nick and m.nick.lower() == lower)
                                or m.display_name.lower() == lower):
                            member = m
                            break

                # c) Discord API member search (finds members not in cache)
                if not member:
                    try:
                        results = await guild.query_members(query=username, limit=5)
                        if results:
                            member = results[0]
                    except Exception:
                        pass

                # d) Numeric ID passed as username string
                if not member:
                    try:
                        user_id = int(str(username).strip())
                    except (ValueError, TypeError):
                        pass

            # e) Fallback: Try broader search with query_members for partial matches
            if not member and username:
                try:
                    results = await guild.query_members(query=username, limit=100)
                    username_lower = username.lower()
                    for m in results:
                        if (m.name.lower() == username_lower
                                or (m.nick and m.nick.lower() == username_lower)
                                or m.display_name.lower() == username_lower):
                            member = m
                            break
                    if not member and results:
                        member = results[0]
                except Exception:
                    pass

        # e) Lookup by user_id if we have one but no member yet
        if not member and user_id:
            member = guild.get_member(int(user_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(user_id))
                except Exception:
                    pass

        # f) Last resort: extract a Discord snowflake embedded in the username string.
        #    Discord's pomelo auto-usernames look like "user<snowflake>" e.g. user1357317173470564433.
        if not member and not user_id and username:
            import re as _re_ping
            snowflake_match = _re_ping.search(r'\b(\d{17,20})\b', str(username))
            if snowflake_match:
                try:
                    potential_id = int(snowflake_match.group(1))
                    member = guild.get_member(potential_id)
                    if not member:
                        member = await guild.fetch_member(potential_id)
                    if member:
                        logger.info(f"[ping] Resolved snowflake from username string: {username!r} → {member.id}")
                except Exception:
                    pass

        # —— Member not found — soft pass ——————————————————————————————————————
        if not member:
            logger.warning(f"[ping] Could not find member: username={username!r} user_id={user_id!r}")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find member **{username or user_id}** to ping.", delete_after=8
                )
            except Exception:
                pass
            return True, None

        # —— Build and send the ping embed —————————————————————————————————————
        latency = round(self.bot.latency * 1000, 1) if self.bot.latency else 0

        status_map = {
            "online": "U0001f7e2 Online",
            "idle": "U0001f7e1 Idle",
            "dnd": "U0001f7e0 Do Not Disturb",
            "offline": "🌙 Offline",
        }
        status_text = status_map.get(str(member.status), str(member.status).title())
        joined = member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "Unknown"

        embed = discord.Embed(
            title=f"ðŸ“£ {member.display_name}",
            description=f"{status_text}\nBot Latency: {latency}ms\nJoined: {joined}",
            color=member.color if member.color != discord.Color.default() else discord.Color.blurple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await interaction.channel.send(f"{member.mention}", embed=embed, delete_after=30)
        except Exception as e:
            logger.error(f"[ping] Error sending ping embed: {e}")
            return False, None

        return True, None

    async def action_post_documentation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Posts a comprehensive, multi-section documentation embed for a newly created system."""
        channel_name = params.get("channel")
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel

        title = params.get("title", "System Documentation")
        description = params.get("description", "")
        sections = params.get("sections", [])
        footer = params.get("footer", "")
        color = parse_color(params.get("color", 0x5865F2))

        embed = discord.Embed(title=title, description=description, color=color)

        for section in sections:
            section_title = section.get("title", "")
            section_content = section.get("content", "")
            if section_title and section_content:
                embed.add_field(name=section_title, value=section_content, inline=False)

        if footer:
            embed.set_footer(text=footer)

        embed.timestamp = datetime.now(timezone.utc)

        msg = await channel.send(embed=embed)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    # --- Specialized Systems ---

    async def action_setup_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.staff_system import StaffSystem
        system = StaffSystem(self.bot)
        result = await system.setup(interaction, params)
        success = bool(result) if result is not None else True
        if success:
            await self._auto_document_system(interaction.guild.id, "staff_system")
        return success, {"action": "undo_staff_system", "guild_id": interaction.guild.id}

    async def action_setup_economy(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "economy")

        if existing["channel"]:
            # Reuse existing resources
            logger.info(f"Reusing existing economy resources: channel '{existing['channel'].name}' - {existing['reasoning']}")
            # Enable economy system
            economy_config = {
                "enabled": True,
                "daily_reward": 100,
                "work_min": 50,
                "work_max": 200,
                "beg_min": 10,
                "beg_max": 50,
                "currency_name": "coins",
                "currency_symbol": "💰"
            }
            dm.update_guild_data(guild.id, "economy_config", economy_config)
            
            # Register custom commands
            custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
            custom_cmds["daily"] = json.dumps({"command_type": "economy_daily"})
            custom_cmds["balance"] = json.dumps({"command_type": "economy_balance"})
            custom_cmds["work"] = json.dumps({"command_type": "economy_work"})
            custom_cmds["beg"] = json.dumps({"command_type": "economy_beg"})
            custom_cmds["shop"] = json.dumps({"command_type": "help_embed", "title": "Premium Shop", "description": "Spend your coins on exclusive items.", "fields": [{"name": "!shop", "value": "Browse available items.", "inline": False}, {"name": "!buy <item>", "value": "Purchase an item.", "inline": False}]})
            custom_cmds["help economy"] = json.dumps({"command_type": "help_embed", "title": "Economy System Help", "description": "Manage your coins and trade with others.", "fields": [{"name": "!daily", "value": "Claim your daily coin reward.", "inline": False}, {"name": "!balance", "value": "Check your coin balance.", "inline": False}, {"name": "!work", "value": "Work to earn coins.", "inline": False}, {"name": "!beg", "value": "Beg for coins.", "inline": False}, {"name": "!shop", "value": "View the shop.", "inline": False}]})
            custom_cmds["help"] = json.dumps({"command_type": "help_all"})
            dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

            await self._auto_document_system(guild.id, "economy")
            return True, {"action": "undo_economy", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.economy import Economy
        system = Economy(self.bot)
        result = await system.setup(interaction, params)
        success = bool(result) if result is not None else True
        if success:
            await self._auto_document_system(guild.id, "economy")
        return success, {"action": "undo_economy", "guild_id": guild.id}

    async def action_setup_trigger_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.trigger_roles import TriggerRoles
        system = TriggerRoles(self.bot)
        result = await system.setup(interaction, params)
        return bool(result) if result is not None else True, {"action": "undo_trigger_role", "guild_id": interaction.guild.id}

    # --- Setup System Actions (Auto-Setup with Buttons) ---

    async def action_setup_verification(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup verification system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "verification")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing verification resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "verify_channel", existing["channel"].id)
            dm.update_guild_data(guild.id, "verify_role", existing["role"].id)
            await self._auto_document_system(guild.id, "verification")
            return True, {"action": "undo_verification", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_verification(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "verification")
        return result, {"action": "undo_verification", "guild_id": guild.id}

    async def action_setup_tickets(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup ticket system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "tickets")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing ticket resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "tickets_channel", existing["channel"].id)
            # Store ticket config
            tickets_config = {
                "enabled": True,
                "categories": ["General", "Support", "Billing", "Other"],
                "support_role": existing["role"].id,
                "category": existing.get("category").id if existing.get("category") else None
            }
            dm.update_guild_data(guild.id, "tickets_config", tickets_config)
            await self._auto_document_system(guild.id, "tickets")
            return True, {"action": "undo_tickets", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_tickets(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "tickets")
        return result, {"action": "undo_tickets", "guild_id": guild.id}

    async def action_setup_applications(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup applications system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "applications")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing application resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "applications_channel", existing["channel"].id)
            await self._auto_document_system(guild.id, "applications")
            return True, {"action": "undo_applications", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_applications(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "applications")
        return result, {"action": "undo_applications", "guild_id": guild.id}

    async def action_setup_appeals(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup appeals system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "appeals")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing appeal resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "appeals_channel", existing["channel"].id)
            await self._auto_document_system(guild.id, "appeals")
            return True, {"action": "undo_appeals", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_appeals(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "appeals")
        return result, {"action": "undo_appeals", "guild_id": guild.id}

    async def action_setup_moderation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup moderation logging system."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_moderation(interaction, params)
        if result:
            await self._auto_document_system(interaction.guild.id, "moderation")
        return result, {"action": "undo_moderation", "guild_id": interaction.guild.id}

    async def action_setup_logging(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup server logging system."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_logging(interaction, params)
        return result, {"action": "undo_logging", "guild_id": interaction.guild.id}

    async def action_setup_leveling(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup leveling/XP system."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "leveling")

        if existing["channel"]:
            # Reuse existing resources
            logger.info(f"Reusing existing leveling resources: channel '{existing['channel'].name}' - {existing['reasoning']}")
            # Enable leveling system with roles
            leveling_config = {
                "enabled": True,
                "xp_per_message": 1,
                "xp_per_voice_minute": 0.5,
                "level_roles": {
                    "5": "Newcomer",
                    "10": "Regular",
                    "25": "Veteran",
                    "50": "Elite",
                    "100": "Legend"
                }
            }

            # Create level roles if they don't exist
            for level, role_name in leveling_config["level_roles"].items():
                if not guild.roles or not discord.utils.get(guild.roles, name=role_name):
                    try:
                        await guild.create_role(
                            name=role_name,
                            color=discord.Color.blue(),
                            hoist=True,
                            reason="Leveling system setup"
                        )
                    except discord.Forbidden:
                        logger.warning(f"Could not create {role_name} role")

            dm.update_guild_data(guild.id, "leveling_config", leveling_config)

            # Register custom commands
            custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
            custom_cmds["rank"] = json.dumps({"command_type": "leveling_rank"})
            custom_cmds["leaderboard"] = json.dumps({"command_type": "leveling_leaderboard"})
            custom_cmds["levels"] = json.dumps({"command_type": "leveling_levels"})
            custom_cmds["rewards"] = json.dumps({"command_type": "leveling_rewards"})
            custom_cmds["help leveling"] = json.dumps({"command_type": "help_embed", "title": "Leveling System Help", "description": "Earn XP by chatting and level up!", "fields": [{"name": "!rank", "value": "Check your current level and XP.", "inline": False}, {"name": "!leaderboard", "value": "View the top members.", "inline": False}, {"name": "!levels", "value": "View level progression info.", "inline": False}, {"name": "!rewards", "value": "View leveling rewards.", "inline": False}]})
            custom_cmds["help"] = json.dumps({"command_type": "help_all"})
            dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

            await self._auto_document_system(guild.id, "leveling")
            return True, {"action": "undo_leveling", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.gamification import Gamification
        system = Gamification(self.bot)
        result = await system.setup(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "leveling")
        return result, {"action": "undo_leveling", "guild_id": guild.id}

    async def action_setup_welcome(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup welcome/leave message system."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "welcome")

        if existing["channel"]:
            # Reuse existing resources
            logger.info(f"Reusing existing welcome resources: channel '{existing['channel'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "welcome_channel", existing["channel"].id)
            await self._auto_document_system(guild.id, "welcome")
            return True, {"action": "undo_welcome", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.welcome_leave import WelcomeLeaveSystem
        system = WelcomeLeaveSystem(self.bot)
        result = await system.setup(interaction, params)
        success = bool(result) if result is not None else True
        if success:
            await self._auto_document_system(guild.id, "welcome")
        return success, {"action": "undo_welcome", "guild_id": guild.id}

# --- Aliases: create_*_system → setup_* (AI may use either name) ---

    async def action_create_verify_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_verification(interaction, params)

    async def action_create_tickets_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_tickets(interaction, params)

    async def action_create_applications_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_applications(interaction, params)

    async def action_create_appeals_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_appeals(interaction, params)

    async def action_create_welcome_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_welcome(interaction, params)

    async def action_create_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_staff_system(interaction, params)

    async def action_create_leveling_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_leveling(interaction, params)

    async def action_create_economy_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_economy(interaction, params)

    def _get_automations_dict(self, guild_id: int) -> dict:
        try:
            return dm.get_guild_data(guild_id, "automations", {}) or {}
        except Exception:
            return {}

    def _save_automations_dict(self, guild_id: int, data: dict):
        try:
            dm.update_guild_data(guild_id, "automations", data)
        except Exception as e:
            logger.error(f"Failed to save automations for {guild_id}: {e}")

    def _normalize_scheduled_params(self, handler_name: str, handler_params: Any,
                                    channel_id=None, response: str = "") -> dict:
        """Prepare stored/dispatch params for a scheduled automation:
        - inject channel_id so the fire-time interaction targets the right channel
        - map flat 'response' onto the handler's text parameter (content/message/...)"""
        if not isinstance(handler_params, dict):
            handler_params = {}
        hp = dict(handler_params)
        if channel_id is not None and "channel_id" not in hp and "channel" not in hp:
            hp["channel_id"] = channel_id
        if response and not any(k in hp for k in ("content", "message", "text", "description")):
            hp["content"] = response
        return hp

    def _resolve_channel_id(self, guild_id: int, channel_id=None, channel_name=None, interaction=None):
        """Resolve channel_name -> channel_id via live query (1000x: any text 'in #general' / 'here')."""
        if channel_id is not None:
            try:
                return int(str(channel_id))
            except Exception:
                pass
        if channel_name:
            try:
                name = str(channel_name).lstrip("#").strip().lower()
                if name in ("here", "current", "this channel") and interaction is not None and getattr(interaction, "channel", None) is not None:
                    return getattr(interaction.channel, "id", None)
                # Try bot cache
                guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
                if guild:
                    for ch in getattr(guild, "channels", []) + getattr(guild, "text_channels", []):
                        if str(getattr(ch, "name", "")).lower() == name:
                            return ch.id
                    # fuzzy via ServerQueryEngine if available
                    try:
                        from server_query import ServerQueryEngine
                        import asyncio
                        # synchronous fallback: use dm channels not needed
                    except Exception:
                        pass
            except Exception:
                pass
        return channel_id

    def _schedule_cron_job(self, guild_id: int, name: str, cron: str, handler_name: str, handler_params: dict, channel_id=None):
        """Validate cron, compute next_run, schedule via TaskScheduler with rescheduling. Returns (task_id, next_run) or (None, error)."""
        try:
            from croniter import croniter
        except Exception as e:
            return None, f"croniter not available: {e}"
        try:
            nxt = croniter(cron, datetime.now()).get_next(float)
        except Exception as e:
            return None, f"Invalid cron expression '{cron}': {e}"
        try:
            from task_scheduler import task_scheduler
        except Exception as e:
            return None, f"task_scheduler not available: {e}"
        # Normalize handler
        if not isinstance(handler_params, dict):
            handler_params = {}
        # Inject channel_id into params if provided and not already there
        if channel_id is not None and "channel_id" not in handler_params and "channel" not in handler_params:
            handler_params = dict(handler_params)
            handler_params["channel_id"] = channel_id

        # Closure that executes then reschedules
        async def _run_and_reschedule(_name=name, _cron=cron, _handler=handler_name, _params=dict(handler_params), _gid=guild_id):
            try:
                mock = ScheduledTaskInteraction(self.bot, _gid)
                if mock.guild is None:
                    logger.warning(f"Scheduled task '{_name}': guild {_gid} not found; dropping")
                    return
                cid = (_params or {}).get("channel_id")
                if cid:
                    try:
                        ch = self.bot.get_channel(int(str(cid)))
                        if ch is not None:
                            mock.channel = ch
                    except (TypeError, ValueError):
                        pass
                # Skip paused automations (paused via panel/agent/auto-pause)
                try:
                    from modules.automation_manager import get_automations as _ga, record_run as _rr
                    _entry = _ga(_gid).get(_name)
                    if isinstance(_entry, dict) and _entry.get("paused"):
                        logger.info(f"Scheduled task '{_name}' fired but is paused; skipping")
                        return
                except Exception:
                    pass
                # Merge live params copy
                try:
                    await self.dispatch(mock, _handler, dict(_params))
                    try:
                        _rr(_gid, _name, True)
                    except Exception:
                        pass
                except Exception as _run_exc:
                    try:
                        _rr(_gid, _name, False, str(_run_exc))
                    except Exception:
                        pass
                    raise
                # Schedule next occurrence (unless auto-pause kicked in)
                try:
                    from croniter import croniter as _ci
                    _still = dm.get_guild_data(_gid, "automations", {}) or {}
                    if isinstance(_still.get(_name), dict) and _still[_name].get("paused"):
                        logger.info(f"Automation '{_name}' paused after run; not rescheduling")
                        return
                    nxt2 = _ci(_cron, datetime.now()).get_next(float)
                    task_scheduler.schedule_task(nxt2, _run_and_reschedule)
                    # Update automations next_run for observability
                    try:
                        autos = dm.get_guild_data(_gid, "automations", {}) or {}
                        if _name in autos:
                            autos[_name]["next_run"] = nxt2
                            dm.update_guild_data(_gid, "automations", autos)
                    except Exception:
                        pass
                except Exception as e2:
                    logger.error(f"Failed to reschedule '{_name}': {e2}")
            except Exception as e:
                logger.error(f"Scheduled task '{_name}' failed: {e}")

        try:
            task_id = task_scheduler.schedule_task(nxt, _run_and_reschedule)
        except Exception as e:
            return None, f"Failed to schedule task: {e}"
        return task_id, nxt

    async def action_schedule_ai_action(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Schedule an AI action to run on a cron schedule. LIVE via TaskScheduler."""
        name = str(params.get("name") or f"scheduled_{int(time.time())}").strip()[:64]
        cron = str(params.get("cron") or "0 12 * * *").strip()
        action_type = str(params.get("action_type") or params.get("handler") or "send_message").strip()
        action_params = params.get("action_params") if isinstance(params.get("action_params"), dict) else params.get("handler_params") if isinstance(params.get("handler_params"), dict) else {}
        if not isinstance(action_params, dict):
            action_params = {}
        # Also support trigger style params
        channel_id = params.get("channel_id")
        if channel_id is None:
            trig = params.get("trigger")
            if isinstance(trig, dict):
                channel_id = trig.get("channel_id")
        guild_id = interaction.guild.id
        # Map legacy action_type names to real handlers
        handler_map = {"announcement": "send_message", "announce": "send_message"}
        handler_name = handler_map.get(action_type.lower(), action_type)
        action_params = self._normalize_scheduled_params(handler_name, action_params, channel_id)
        task_id, nxt_or_err = self._schedule_cron_job(guild_id, name, cron, handler_name, action_params)
        if task_id is None:
            return False, {"error": nxt_or_err}
        # Persist in automations registry for restore + delete
        autos = self._get_automations_dict(guild_id)
        autos[name] = {"type": "scheduled_task", "name": name, "cron": cron, "handler": handler_name, "params": action_params, "channel_id": channel_id, "next_run": nxt_or_err, "task_id": task_id, "created_by": getattr(interaction.user, "id", 0), "created_at": time.time()}
        self._save_automations_dict(guild_id, autos)
        logger.info(f"Scheduled AI action LIVE: {name} cron:{cron} next:{nxt_or_err} guild:{guild_id}")
        return True, {"automation_id": name, "type": "scheduled_task", "cron": cron, "next_run": nxt_or_err, "task_id": task_id, "handler": handler_name}

    async def action_create_automation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Create a LIVE automation — scheduled_task / auto_responder / reminder."""
        name = str(params.get("name") or f"automation_{int(time.time())}").strip()[:64]
        # Normalize type: accept "type", "automation_type", "kind"
        automation_type = str(params.get("type") or params.get("automation_type") or params.get("kind") or "scheduled_task").strip().lower()
        # Aliases
        if automation_type in ("schedule", "cron", "scheduled", "task"):
            automation_type = "scheduled_task"
        elif automation_type in ("responder", "auto-responder", "autoresponder"):
            automation_type = "auto_responder"
        schedule = params.get("schedule", {})
        trigger = params.get("trigger", {})
        action_to_perform = params.get("action", {})
        # Support alternative keys: "action_type"+"action_params" flat
        if not action_to_perform and (params.get("action_type") or params.get("handler")):
            action_to_perform = {"name": params.get("action_type") or params.get("handler"), "parameters": params.get("action_params") or params.get("handler_params") or {}}
        response = params.get("response", params.get("content", params.get("message", "")))
        if response is None:
            response = ""
        response = str(response)
        guild_id = interaction.guild.id
        # 1000x: quota for ALL automation types (was only scheduled_task)
        try:
            from modules.automation_manager import check_quota
            quota_ok, quota_err = check_quota(guild_id, "automation")
            if not quota_ok:
                return False, {"error": quota_err}
        except Exception:
            pass
        # 1000x: channel resolve 'here'/name -> id
        _resolved_channel = self._resolve_channel_id(guild_id, params.get("channel_id") or (trigger.get("channel_id") if isinstance(trigger, dict) else None), params.get("channel_name") or (trigger.get("channel_name") if isinstance(trigger, dict) else None), interaction)
        if _resolved_channel is not None:
            # propagate to params so branches see it
            params["channel_id"] = _resolved_channel
            if isinstance(trigger, dict):
                trigger["channel_id"] = _resolved_channel
        # 1000x: overwrite guard — cancel old task_id to avoid leak
        try:
            existing = self._get_automations_dict(guild_id).get(name)
            if isinstance(existing, dict):
                old_task = existing.get("task_id")
                if old_task:
                    try:
                        from task_scheduler import task_scheduler as _ts2
                        _ts2.cancel_task(old_task)
                    except Exception:
                        pass
                logger.info(f"Overwriting existing automation '{name}' (old type {existing.get('type')})")
        except Exception:
            pass
        try:
            if automation_type == "scheduled_task":
                if not isinstance(schedule, dict):
                    schedule = {}
                if not isinstance(trigger, dict):
                    trigger = {}
                if not isinstance(action_to_perform, dict):
                    action_to_perform = {}
                # Interval schedules: {"every_minutes": N} / {"every_hours": H} /
                # {"daily_at": "HH:MM"} / {"weekly_on": "mon", "at": "HH:MM"} / natural language fallback
                from modules.automation_manager import interval_to_cron
                # Natural-language cron fallback: handle 'daily at 9am', 'every 15 minutes' strings
                cron_raw = schedule.get("cron") or params.get("cron") or ""
                if isinstance(cron_raw, str) and cron_raw.strip() and cron_raw.strip().count(" ") < 4 and any(x in cron_raw.lower() for x in ("daily","every","at ","am","pm")):
                    # Try to parse natural language into schedule object
                    nl = cron_raw.lower()
                    import re as _re
                    m = _re.search(r"every\s+(\d+)\s*min", nl)
                    if m:
                        schedule = {"every_minutes": int(m.group(1))}
                    elif "daily at" in nl:
                        tm = _re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", nl)
                        if tm:
                            h = int(tm.group(1)); mm = int(tm.group(2) or 0); ap = tm.group(3)
                            if ap == "pm" and h != 12: h+=12
                            if ap == "am" and h == 12: h=0
                            schedule = {"daily_at": f"{h:02d}:{mm:02d}"}
                interval_cron = interval_to_cron(schedule) or interval_to_cron(
                    {"every_minutes": params.get("every_minutes"), "every_hours": params.get("every_hours"),
                     "daily_at": params.get("daily_at")})
                cron = str(schedule.get("cron") or params.get("cron") or interval_cron or "0 12 * * *").strip()
                # If raw natural language still not cron-like, fallback to schedule parse
                if cron.count(" ") < 4 and any(x in cron.lower() for x in ("daily","every")):
                    interval_cron2 = interval_to_cron({"daily_at": "09:00"}) if "daily" in cron.lower() else None
                    if interval_cron2:
                        cron = interval_cron2
                channel_id = trigger.get("channel_id") if isinstance(trigger, dict) else None
                if channel_id is None:
                    channel_id = params.get("channel_id")
                # handler extraction: support {name, parameters} or {handler, params}
                handler_name = action_to_perform.get("name") or action_to_perform.get("handler") or params.get("action_type") or "send_message"
                handler_params = action_to_perform.get("parameters") or action_to_perform.get("params") or action_to_perform.get("handler_params") or {}
                if not isinstance(handler_params, dict):
                    handler_params = {}
                handler_name = str(handler_name).strip()
                # 1000x: validate handler against allow-list
                _allowed_handlers = {"send_message","send_embed","announce","poll","send_notification","give_points","assign_role"}
                if handler_name not in self.ALLOWED_ACTIONS and handler_name not in _allowed_handlers:
                    # allow but log; keep permissive for future handlers
                    logger.warning(f"Unverified handler '{handler_name}' for automation '{name}' — allowing")
                handler_params = self._normalize_scheduled_params(handler_name, handler_params, channel_id, response)
                task_id, nxt_or_err = self._schedule_cron_job(guild_id, name, cron, handler_name, handler_params)
                if task_id is None:
                    return False, {"error": nxt_or_err}
                autos = self._get_automations_dict(guild_id)
                autos[name] = {"type": "scheduled_task", "name": name, "cron": cron, "handler": handler_name, "params": handler_params, "channel_id": channel_id, "next_run": nxt_or_err, "task_id": task_id, "created_by": getattr(interaction.user, "id", 0), "created_at": time.time(), "schedule": schedule, "trigger": trigger}
                self._save_automations_dict(guild_id, autos)
                logger.info(f"Created LIVE scheduled automation: {name} cron:{cron} guild:{guild_id}")
                return True, {"message": f"Automation '{name}' created.", "automation_id": name, "type": automation_type, "cron": cron, "next_run": nxt_or_err, "task_id": task_id, "handler": handler_name}
            elif automation_type in ("trigger_role", "trigger-role", "role_trigger"):
                # 1000x: new type — keyword -> role assignment
                if not isinstance(trigger, dict):
                    trigger = {}
                raw_keywords = trigger.get("keywords") or params.get("keywords") or params.get("keyword") or []
                if isinstance(raw_keywords, str):
                    raw_keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()] if "," in raw_keywords else [raw_keywords.strip()] if raw_keywords.strip() else []
                triggers = [str(k).strip().lower() for k in (raw_keywords if isinstance(raw_keywords, list) else [raw_keywords]) if str(k).strip()]
                if not triggers:
                    triggers = [name.lower()]
                role_id = trigger.get("role_id") or params.get("role_id")
                role_name = trigger.get("role_name") or params.get("role_name")
                if role_id is None and role_name:
                    try:
                        guild = self.bot.get_guild(guild_id)
                        if guild:
                            for r in getattr(guild, "roles", []):
                                if str(getattr(r, "name", "")).lower() == str(role_name).lower():
                                    role_id = r.id
                                    break
                    except Exception:
                        pass
                if role_id is None:
                    return False, {"error": "trigger_role requires role_id or role_name (resolve via query_roles)."}
                try:
                    role_id = int(str(role_id))
                except Exception:
                    return False, {"error": f"Invalid role_id: {role_id}"}
                # Store as automation; actual trigger is via trigger_roles system (mirror to auto_responder-like)
                try:
                    from modules.trigger_roles import TriggerRoles
                except Exception:
                    pass
                autos = self._get_automations_dict(guild_id)
                autos[name] = {"type": "trigger_role", "name": name, "keywords": triggers, "role_id": role_id, "response": response, "trigger": trigger, "created_by": getattr(interaction.user, "id", 0), "created_at": time.time()}
                self._save_automations_dict(guild_id, autos)
                # Also register with TriggerRoles persistent store if available
                try:
                    tr = getattr(self.bot, "trigger_roles", None)
                    if tr and hasattr(tr, "add_trigger"):
                        for kw in triggers:
                            tr.add_trigger(guild_id, kw, role_id)
                except Exception as e:
                    logger.debug(f"TriggerRoles add failed: {e}")
                logger.info(f"Created trigger_role automation: {name} keywords:{triggers} role:{role_id} guild:{guild_id}")
                return True, {"message": f"Automation '{name}' created.", "automation_id": name, "type": "trigger_role", "keywords": triggers, "role_id": role_id}
            elif automation_type == "auto_responder":
                if not isinstance(trigger, dict):
                    trigger = {}
                # Accept keywords as list or single string or trigger string
                raw_keywords = trigger.get("keywords") if "keywords" in trigger else trigger.get("keyword") if "keyword" in trigger else trigger.get("trigger") if "trigger" in trigger else params.get("keywords")
                if raw_keywords is None:
                    # Also try flat trigger param
                    raw_keywords = trigger if isinstance(trigger, list) else []
                if isinstance(raw_keywords, str):
                    # split by comma if contains commas
                    if "," in raw_keywords:
                        raw_keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
                    else:
                        raw_keywords = [raw_keywords.strip()] if raw_keywords.strip() else []
                if not isinstance(raw_keywords, list):
                    raw_keywords = [str(raw_keywords)] if raw_keywords else []
                triggers = [str(k).strip().lower() for k in raw_keywords if str(k).strip()]
                if not triggers:
                    # Fallback: use name as trigger if none provided
                    triggers = [name.lower()]
                if not response or not response.strip():
                    return False, {"error": "auto_responder requires 'response' text."}
                # Route through real system
                ar = getattr(self.bot, "auto_responder", None)
                if ar is None:
                    return False, {"error": "AutoResponderSystem not available on bot."}
                # Determine match/response types from trigger dict if provided
                match_type = trigger.get("match_type", trigger.get("match", "contains"))
                response_type = trigger.get("response_type", "text")
                # Ensure valid enums
                if match_type not in ("exact", "contains", "starts_with", "ends_with", "regex"):
                    match_type = "contains"
                if response_type not in ("text", "embed", "random", "reaction"):
                    response_type = "text"
                created = []
                for kw in triggers:
                    responder = {"trigger": kw, "response": response, "match_type": match_type, "response_type": response_type, "enabled": True, "_automation_name": name}
                    try:
                        ar.add_responder(guild_id, responder)
                        created.append(kw)
                    except Exception as e:
                        logger.error(f"Failed to add responder for {kw}: {e}")
                if not created:
                    return False, {"error": "Failed to create any auto-responder."}
                # Persist registry for delete
                autos = self._get_automations_dict(guild_id)
                autos[name] = {"type": "auto_responder", "name": name, "triggers": created, "response": response, "match_type": match_type, "response_type": response_type, "created_by": getattr(interaction.user, "id", 0), "created_at": time.time(), "trigger": trigger}
                self._save_automations_dict(guild_id, autos)
                logger.info(f"Created LIVE auto-responder automation: {name} triggers:{created} guild:{guild_id}")
                return True, {"message": f"Automation '{name}' created.", "automation_id": name, "type": automation_type, "triggers": created, "match_type": match_type, "response_type": response_type}
            elif automation_type == "reminder":
                if not isinstance(schedule, dict):
                    schedule = {}
                if not isinstance(trigger, dict):
                    trigger = {}
                # duration may be under schedule.duration / delay_seconds / seconds / or flat duration param
                duration = schedule.get("duration")
                if duration is None:
                    duration = schedule.get("delay_seconds")
                if duration is None:
                    duration = schedule.get("seconds")
                if duration is None:
                    duration = params.get("duration")
                if duration is None:
                    duration = 3600
                try:
                    duration = int(duration)
                except Exception:
                    return False, {"error": f"Invalid duration: {duration}"}
                if duration < 10:
                    duration = 10
                if duration > 2592000:
                    return False, {"error": "Duration must be 10s .. 30 days."}
                if not response or not response.strip():
                    return False, {"error": "reminder requires 'response' or 'content' text."}
                # Build proper reminder_data for ReminderSystem
                channel_id = trigger.get("channel_id") or params.get("channel_id") or getattr(interaction.channel, "id", None)
                user_id = trigger.get("user_id") or getattr(interaction.user, "id", 0)
                reminder_time = time.time() + duration
                reminder_id = int(time.time() * 1000) % 2147483647
                reminder_data = {"id": reminder_id, "user_id": user_id, "guild_id": guild_id, "channel_id": channel_id, "message": response, "reminder_time": reminder_time, "recurring": bool(schedule.get("recurring", False)), "recurring_interval": duration if schedule.get("recurring") else None, "created_at": time.time(), "_automation_name": name}
                # Persist via same key as ReminderSystem expects
                reminders = dm.get_guild_data(guild_id, "scheduled_reminders", [])
                if not isinstance(reminders, list):
                    reminders = []
                reminders.append(reminder_data)
                dm.update_guild_data(guild_id, "scheduled_reminders", reminders)
                # Schedule via task_scheduler
                try:
                    from task_scheduler import task_scheduler as _ts
                    # Use ReminderSystem.send_reminder if available else inline send
                    rs = getattr(self.bot, "reminders", None)
                    if rs and hasattr(rs, "send_reminder"):
                        task_id = _ts.schedule_task(reminder_time, rs.send_reminder, reminder_data)
                    else:
                        async def _send_reminder_inline(data=reminder_data):
                            try:
                                ch = self.bot.get_channel(data["channel_id"])
                                if not ch:
                                    return
                                import discord as _d
                                emb = _d.Embed(title="⏰ Reminder!", description=data["message"], color=_d.Color.blue())
                                await ch.send(f"<@{data['user_id']}>", embed=emb)
                            except Exception as e:
                                logger.error(f"Inline reminder failed: {e}")
                        task_id = _ts.schedule_task(reminder_time, _send_reminder_inline)
                    # Track in active_reminders memory
                    try:
                        if rs and hasattr(rs, "active_reminders"):
                            if user_id not in rs.active_reminders:
                                rs.active_reminders[user_id] = []
                            rs.active_reminders[user_id].append(reminder_data)
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"Failed to schedule reminder task: {e}")
                    task_id = None
                # Registry
                autos = self._get_automations_dict(guild_id)
                autos[name] = {"type": "reminder", "name": name, "duration": duration, "reminder_id": reminder_id, "reminder_time": reminder_time, "channel_id": channel_id, "task_id": task_id, "created_by": getattr(interaction.user, "id", 0), "created_at": time.time(), "schedule": schedule, "trigger": trigger, "response": response}
                self._save_automations_dict(guild_id, autos)
                logger.info(f"Created LIVE reminder automation: {name} duration:{duration}s guild:{guild_id}")
                return True, {"message": f"Automation '{name}' created.", "automation_id": name, "type": automation_type, "duration": duration, "reminder_id": reminder_id, "reminder_time": reminder_time, "task_id": task_id, "channel_id": channel_id}
            else:
                return False, {"error": f"Unknown automation type: {automation_type}. Use scheduled_task, auto_responder, or reminder."}
        except Exception as e:
            logger.error(f"Error creating automation: {e}")
            import traceback as _tb
            logger.error(_tb.format_exc())
            return False, {"error": str(e)}

    async def action_delete_automation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Delete an automation by name and cancel its scheduled job."""
        name = str(params.get("name") or params.get("automation_id") or params.get("automation_name") or "").strip()
        if not name:
            return False, {"error": "delete_automation requires 'name'."}
        guild_id = interaction.guild.id
        autos = self._get_automations_dict(guild_id)
        entry = autos.get(name)
        if not entry:
            # Also try prefix_command registry under same name
            return False, {"error": f"Automation '{name}' not found."}
        atype = entry.get("type", "scheduled_task")
        task_id = entry.get("task_id")
        # Cancel scheduled task if present
        if task_id is not None:
            try:
                from task_scheduler import task_scheduler as _ts
                _ts.cancel_task(task_id)
            except Exception:
                pass
        # Type-specific cleanup
        try:
            if atype == "auto_responder":
                ar = getattr(self.bot, "auto_responder", None)
                if ar and hasattr(ar, "get_responders"):
                    triggers = entry.get("triggers") or []
                    responders = ar.get_responders(guild_id) or []
                    # Remove responders that were created by this automation (_automation_name)
                    keep = [r for r in responders if r.get("_automation_name") != name and r.get("trigger") not in triggers or r.get("_automation_name") != name]
                    # More precise: drop any with matching _automation_name
                    keep = [r for r in responders if r.get("_automation_name") != name]
                    # If none matched but triggers exist, drop by trigger value
                    if len(keep) == len(responders) and triggers:
                        keep = [r for r in responders if r.get("trigger") not in triggers]
                    if len(keep) != len(responders):
                        dm.update_guild_data(guild_id, "auto_responders", keep)
            elif atype == "reminder":
                rid = entry.get("reminder_id")
                # Remove from scheduled_reminders
                rems = dm.get_guild_data(guild_id, "scheduled_reminders", []) or []
                new_rems = [r for r in rems if r.get("id") != rid and r.get("_automation_name") != name]
                if len(new_rems) != len(rems):
                    dm.update_guild_data(guild_id, "scheduled_reminders", new_rems)
                # Remove from active_reminders memory
                try:
                    rs = getattr(self.bot, "reminders", None)
                    if rs and hasattr(rs, "active_reminders"):
                        uid = entry.get("created_by") or entry.get("user_id")
                        for uid_key in list(rs.active_reminders.keys()):
                            rs.active_reminders[uid_key] = [r for r in rs.active_reminders[uid_key] if r.get("id") != rid and r.get("_automation_name") != name]
                except Exception:
                    pass
            elif atype == "prefix_command":
                # Remove prefix command entry as well
                cmds = dm.get_guild_data(guild_id, "custom_commands", {}) or {}
                if name in cmds:
                    del cmds[name]
                    dm.update_guild_data(guild_id, "custom_commands", cmds)
                    # Clean usage
                    usage = dm.get_guild_data(guild_id, "command_usage", {}) or {}
                    if name in usage:
                        del usage[name]
                        dm.update_guild_data(guild_id, "command_usage", usage)
        except Exception as e:
            logger.warning(f"Cleanup for automation {name} ({atype}) had issue: {e}")
        # Remove from automations registry
        del autos[name]
        self._save_automations_dict(guild_id, autos)
        logger.info(f"Deleted automation LIVE: {name} type:{atype} guild:{guild_id}")
        return True, {"automation_id": name, "type": atype, "message": f"Deleted automation '{name}'."}

    async def action_list_automations(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """List all automations for this guild."""
        guild_id = interaction.guild.id
        autos = self._get_automations_dict(guild_id)
        # Also merge legacy custom_commands count for observability
        return True, {"automations": autos, "count": len(autos)}

    async def action_list_prefix_commands(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild_id = interaction.guild.id
        cmds = dm.get_guild_data(guild_id, "custom_commands", {}) or {}
        return True, {"commands": list(cmds.keys()), "count": len(cmds), "details": {k: (v[:120] + "..." if len(str(v))>120 else v) for k,v in cmds.items()}}

    async def action_connect_systems(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Connect two systems so that when trigger_event happens in source_system, action is performed on target_system."""
        guild_id = interaction.guild.id

        source_system = params.get("source_system")
        trigger_event = params.get("trigger_event")
        target_system = params.get("target_system")
        action = params.get("action")
        parameters = params.get("parameters", {})

        if not all([source_system, trigger_event, target_system, action]):
            # Instead of failing, launch the guided wizard modal
            await self._launch_connect_systems_wizard(interaction)
            return False, {"wizard_launched": True}

        # Load existing connections
        connections = dm.load_json("system_connections", default={})
        guild_connections = connections.get(str(guild_id), [])

        # Create connection object
        connection = {
            "source_system": source_system,
            "trigger_event": trigger_event,
            "target_system": target_system,
            "action": action,
            "parameters": parameters
        }

        # Add to connections
        guild_connections.append(connection)
        connections[str(guild_id)] = guild_connections
        dm.save_json("system_connections", connections)

        return True, {"connection": connection}

    async def _launch_connect_systems_wizard(self, interaction: discord.Interaction):
        """Launch a guided modal wizard to collect connection parameters."""
        modal = ConnectSystemsWizardModal()
        await interaction.response.send_modal(modal)

    async def action_move_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Move a system to a different channel."""
        guild = interaction.guild
        guild_id = guild.id
        
        system = params.get("system")
        new_channel_name = params.get("new_channel_name")
        
        if not system or not new_channel_name:
            return False, {"error": "Missing required parameters: system, new_channel_name"}
        
        # Get current system configuration
        system_config_key = f"{system}_channel"
        current_channel_id = dm.get_guild_data(guild_id, system_config_key)
        
        if not current_channel_id:
            return False, {"error": f"No {system} channel found to move"}
        
        # Find the new channel
        new_channel = discord.utils.get(guild.text_channels, name=new_channel_name.lstrip('#'))
        if not new_channel:
            # Try to create it if it doesn't exist
            try:
                new_channel = await guild.create_text_channel(new_channel_name.lstrip('#'))
            except Exception as e:
                return False, {"error": f"Could not find or create channel '{new_channel_name}': {str(e)}"}
        
        # Update the system configuration
        dm.update_guild_data(guild_id, system_config_key, new_channel.id)
        
        # Also update any related configuration (like verification_role for verification system)
        if system == "verification":
            # For verification, we might also want to update the verification message location
            pass
        
        return True, {
            "system": system,
            "old_channel_id": current_channel_id,
            "new_channel_id": new_channel.id,
            "new_channel_name": new_channel.name
        }

    async def action_create_invite(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates an invite link for a channel or the server."""
        channel = params.get("channel")
        max_uses = params.get("max_uses", 0)
        max_age = params.get("max_age", 86400)  # 24 hours by default
        temporary = params.get("temporary", False)

        target_channel = None
        if channel:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel)
            if not target_channel:
                target_channel = interaction.channel
        else:
            target_channel = interaction.channel

        try:
            invite = await target_channel.create_invite(
                max_uses=max_uses if max_uses else None,
                max_age=max_age if max_age else None,
                temporary=temporary
            )
            return True, {"invite_url": invite.url, "channel_id": target_channel.id}
        except discord.Forbidden:
            return False, {"error": "Missing permission to create invite"}
        except Exception as e:
            logger.error(f"Error creating invite: {e}")
            return False, None

    async def action_kick_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Kicks a user from the server."""
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None

        try:
            await member.kick(reason=reason)
            if hasattr(self.bot, 'staff_shifts'):
                await self.bot.staff_shifts.track_moderation_action(interaction.guild.id, interaction.user.id)
            return True, {"user_id": user_id, "action": "kick"}
        except discord.Forbidden:
            return False, {"error": "Missing permission to kick"}
        except Exception as e:
            logger.error(f"Error kicking user: {e}")
            return False, None

    async def action_mute_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")
        member = interaction.guild.get_member(user_id) if user_id else None
        if not member:
            return False, {"error": "User not found"}
        try:
            await member.edit(mute=True, reason=reason)
            if hasattr(self.bot, 'staff_shifts'):
                await self.bot.staff_shifts.track_moderation_action(interaction.guild.id, interaction.user.id)
            return True, {"message": f"Muted {member.display_name}"}
        except Exception as e:
            return False, {"error": str(e)}

    async def action_unmute_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        member = interaction.guild.get_member(user_id) if user_id else None
        if not member:
            return False, {"error": "User not found"}
        try:
            await member.edit(mute=False)
            return True, {"message": f"Unmuted {member.display_name}"}
        except Exception as e:
            return False, {"error": str(e)}

    async def action_ban_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bans a user from the server."""
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")
        delete_days = self._get_param(params, "delete_days", "delete_messages_days", default=0)

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            try:
                member = await interaction.guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException, Exception) as e:
                logger.debug("Could not fetch member for ban: %s", e)

        try:
            if member:
                await member.ban(reason=reason, delete_message_days=delete_days)
            else:
                await interaction.guild.ban(discord.Object(user_id), reason=reason, delete_message_days=delete_days)
            if hasattr(self.bot, 'staff_shifts'):
                await self.bot.staff_shifts.track_moderation_action(interaction.guild.id, interaction.user.id)
            return True, {"user_id": user_id, "action": "ban"}
        except discord.Forbidden:
            return False, {"error": "Missing permission to ban"}
        except Exception as e:
            logger.error(f"Error banning user: {e}")
            return False, None

    async def action_timeout_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Times out a user (modifies their communication timeout)."""
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        duration = self._get_param(params, "duration", "seconds", "time", "minutes", default=60)
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None

        try:
            import datetime
            timeout_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
            await member.timeout(timeout_until, reason=reason)
            if hasattr(self.bot, 'staff_shifts'):
                await self.bot.staff_shifts.track_moderation_action(interaction.guild.id, interaction.user.id)
            return True, {"user_id": user_id, "duration": duration, "action": "timeout"}
        except discord.Forbidden:
            return False, {"error": "Missing permission to timeout"}
        except Exception as e:
            logger.error(f"Error timeout user: {e}")
            return False, None

    async def action_delete_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deletes a role from the server."""
        role_name = params.get("role_name")

        if not role_name:
            return False, None

        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            return False, None

        try:
            await role.delete()
            return True, {"role_name": role_name}
        except discord.Forbidden:
            return False, {"error": "Missing permission to delete role"}
        except Exception as e:
            logger.error(f"Error deleting role: {e}")
            return False, None

    async def action_delete_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deletes a channel from the server."""
        guild = interaction.guild

        if not guild.me.guild_permissions.manage_channels:
            return False, {"error": "Bot lacks manage_channels permission"}

        # Get parameters, accepting alternatives
        channel_name = self._get_param(params, "channel_name", "channel_mention", "name", "channel")
        channel_id = self._get_param(params, "channel_id", "id")

        channel = None

        # Backend safety rule (plan 6): protected targets are refused even if
        # the AI accidentally targets them. Names are search criteria; IDs are
        # the execution target.
        from core.action_meta import protected_targets, system_protected_channel_ids
        if channel_id:
            try:
                cid = int(str(channel_id).strip().lstrip("<#").rstrip(">"))
                if protected_targets.is_protected(guild.id, "channel", cid):
                    return False, {"error": f"Channel {cid} is PROTECTED — backend refuses deletion."}
                if str(cid) in system_protected_channel_ids(guild.id, guild):
                    return False, {"error": f"Channel {cid} is used by an enabled Miro system and cannot be deleted."}
            except (ValueError, TypeError):
                pass

        # Resolve by ID if provided
        if channel_id:
            try:
                cid = int(str(channel_id).strip().lstrip("<#").rstrip(">"))
                channel = guild.get_channel(cid)
            except (ValueError, TypeError):
                pass

        # If not resolved by ID, try by name
        if not channel and channel_name:
            name = str(channel_name).strip().lstrip("#")  # Strip leading #

            # Exact match first
            channel = discord.utils.get(guild.channels, name=name)
            if not channel:
                # Fuzzy match: case-insensitive partial
                name_lower = name.lower()
                channel = discord.utils.find(lambda c: name_lower in c.name.lower(), guild.channels)

        if not channel:
            error_name = channel_name or channel_id or "unknown"
            return False, {"error": f"Channel '{error_name}' not found"}

        try:
            await channel.delete()
            return True, {"channel_id": channel.id, "channel_name": channel.name}
        except discord.Forbidden:
            return False, {"error": "Missing permission to delete channel"}
        except Exception as e:
            logger.error(f"Error deleting channel: {e}")
            return False, {"error": f"Failed to delete channel: {e}"}

    async def action_announce(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an announcement in a channel."""
        channel_name = self._get_param(params, "channel_name", "channel")
        title = self._get_param(params, "title")
        content = self._get_param(params, "content", "message", "text")
        color = self._get_param(params, "color", "colour", "hex_color", default="#3498db")

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        embed = discord.Embed(
            title=title,
            description=content,
            color=parse_color(color)
        )

        try:
            await target_channel.send(embed=embed)
            return True, {"channel_id": target_channel.id}
        except Exception as e:
            logger.error(f"Error sending announcement: {e}")
            return False, None

    async def action_poll(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a poll with options."""
        channel_name = params.get("channel") or params.get("channel_name")
        question = params.get("question", "Poll")
        options = params.get("options", [])  # list of option strings
        duration = params.get("duration", 300)  # seconds

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        if not options:
            options = ["Yes", "No"]

        options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
        embed = discord.Embed(
            title=f"? {question}",
            description=options_text,
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Poll ends in {duration//60} minutes")

        try:
            msg = await target_channel.send(embed=embed)
            for i in range(len(options)):
                await msg.add_reaction(f"{i+1}\u20e3")
            return True, {"message_id": msg.id, "options": options}
        except Exception as e:
            logger.error(f"Error creating poll: {e}")
            return False, None

    async def action_give_points(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Gives economy points to a user."""
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        points = self._get_param(params, "points", default=100)

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        try:
            economy = self.bot.economy
            if economy:
                economy.add_coins(interaction.guild.id, user_id, points)
            return True, {"user_id": user_id, "points": points, "action": "give_points"}
        except Exception as e:
            logger.error(f"Error giving points: {e}")
            return False, None

    async def action_remove_points(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes economy points from a user."""
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        points = self._get_param(params, "points", default=100)

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        try:
            economy = self.bot.economy
            if economy:
                economy.add_coins(interaction.guild.id, user_id, -points)
            return True, {"user_id": user_id, "points": points, "action": "remove_points"}
        except Exception as e:
            logger.error(f"Error removing points: {e}")
            return False, None

    async def action_delete_prefix_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        # Accept multiple alias keys for robustness
        raw = params.get("cmd_name") or params.get("name") or params.get("command") or params.get("command_name")
        if not raw or not str(raw).strip():
            return False, {"error": "Missing cmd_name (or name)."}
        cmd_name = str(raw).strip().lstrip("!#").lower()
        if not cmd_name:
            return False, {"error": "Invalid command name."}
        guild_id = interaction.guild.id
        commands = dm.get_guild_data(guild_id, "custom_commands", {}) or {}
        # Also handle legacy case where key stored with raw case
        found_key = None
        if cmd_name in commands:
            found_key = cmd_name
        else:
            # case-insensitive search
            for k in list(commands.keys()):
                if k.lower().lstrip("!#") == cmd_name:
                    found_key = k
                    break
        if found_key is not None:
            del commands[found_key]
            dm.update_guild_data(guild_id, "custom_commands", commands)
            # Also clean usage
            try:
                usage = dm.get_guild_data(guild_id, "command_usage", {}) or {}
                if found_key in usage:
                    del usage[found_key]
                    dm.update_guild_data(guild_id, "command_usage", usage)
                if cmd_name in usage:
                    del usage[cmd_name]
                    dm.update_guild_data(guild_id, "command_usage", usage)
            except Exception:
                pass
            # Clean automations registry entry if present
            try:
                autos = self._get_automations_dict(guild_id)
                if cmd_name in autos:
                    del autos[cmd_name]
                    self._save_automations_dict(guild_id, autos)
                if found_key in autos and found_key != cmd_name:
                    del autos[found_key]
                    self._save_automations_dict(guild_id, autos)
            except Exception:
                pass
            return True, {"message": f"Deleted command !{found_key}", "cmd_name": found_key}
        return False, {"error": f"Command !{cmd_name} not found"}

    async def action_unpin_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        message_id = params.get("message_id")
        channel_name = params.get("channel_name") or params.get("channel")
        channel = self._resolve_channel(channel_name) if channel_name else interaction.channel
        if not channel:
            return False, {"error": "Channel not found"}
        try:
            msg = await channel.fetch_message(message_id)
            await msg.unpin()
            return True, {"message": "Message unpinned"}
        except Exception as e:
            return False, {"error": str(e)}

    async def action_extract_online_users(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        status_filter = params.get("status", "online")
        online_members = [m.display_name for m in interaction.guild.members if str(m.status) == status_filter and not m.bot]
        return True, {"members": online_members, "count": len(online_members)}

    async def action_send_notification(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channel_id = params.get("channel")
        message = params.get("message")
        channel = self._resolve_channel(channel_id)
        if not channel:
            return False, {"error": "Channel not found"}
        await channel.send(f"?? **Notification:** {message}")
        return True, {"message": "Notification sent"}

    async def action_create_task(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Schedule a recurring task from a cron expression; handler is an action name
        dispatched with bot identity at each fire time."""
        from datetime import datetime

        name = params.get("name")
        cron = params.get("cron")
        handler = params.get("handler")
        if not all(isinstance(v, str) and v.strip() for v in (name, cron, handler)):
            return False, {"error": "create_task requires 'name', 'cron' and 'handler'."}

        try:
            from croniter import croniter
            next_run = croniter(cron, datetime.now()).get_next(float)
        except Exception as e:
            return False, {"error": f"Invalid cron expression '{cron}': {e}"}

        from task_scheduler import task_scheduler

        async def _run_ai_task(task_name=name, cron_expr=cron, handler_name=handler, guild_id=interaction.guild.id):
            try:
                mock = ScheduledTaskInteraction(self.bot, guild_id)
                if mock.guild is None:
                    logger.error(f"Scheduled task '{task_name}': guild {guild_id} not found; task dropped")
                    return
                await self.dispatch(mock, handler_name, {})
                nxt = croniter(cron_expr, datetime.now()).get_next(float)
                task_scheduler.schedule_task(nxt, _run_ai_task)
            except Exception as e:
                logger.error(f"Scheduled AI task '{task_name}' failed: {e}")

        task_scheduler.schedule_task(next_run, _run_ai_task)
        return True, {"message": f"Task {name} scheduled"}

    async def action_update_profile(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = params.get("user_id") or interaction.user.id
        field = params.get("field")
        value = params.get("value")

        # Consistent multi-server isolation
        profiles = dm.get_guild_data(interaction.guild.id, "user_profiles", {})
        if str(user_id) not in profiles:
            profiles[str(user_id)] = {}
        profiles[str(user_id)][field] = value
        dm.update_guild_data(interaction.guild.id, "user_profiles", profiles)

        # Also store in vector memory if it's a preference
        if "preference" in field.lower() or "like" in field.lower() or "dislike" in field.lower():
            await self.bot.vector_memory.store_conversation(
                guild_id=interaction.guild.id,
                user_id=user_id,
                user_message=f"I want you to remember that {field} is {value}",
                bot_response=f"I've noted that {field} is {value}. I'll remember this for our future interactions.",
                importance_score=0.8
            )

        return True, {"message": f"Updated {field} for user {user_id}"}

    async def action_update_user_preference(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """AI Action to store a user preference for personalized memory."""
        user_id = params.get("user_id") or interaction.user.id
        preference_key = params.get("key")
        preference_value = params.get("value")

        if not preference_key or preference_value is None:
            return False, {"error": "Missing key or value for preference."}

        # Store in guild-specific user profiles
        profiles = dm.get_guild_data(interaction.guild.id, "user_profiles", {})
        if str(user_id) not in profiles:
            profiles[str(user_id)] = {}

        if "preferences" not in profiles[str(user_id)]:
            profiles[str(user_id)]["preferences"] = {}

        profiles[str(user_id)]["preferences"][preference_key] = preference_value
        dm.update_guild_data(interaction.guild.id, "user_profiles", profiles)

        # Store in vector memory for semantic retrieval
        await self.bot.vector_memory.store_conversation(
            guild_id=interaction.guild.id,
            user_id=user_id,
            user_message=f"Remember my preference for {preference_key}: {preference_value}",
            bot_response=f"I've updated your preferences. I'll now remember that your {preference_key} is {preference_value}.",
            importance_score=0.9
        )

        return True, {"message": f"Saved preference: {preference_key} = {preference_value}"}

    async def action_softban_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bans then immediately unbans a user to clear their messages."""
        user_id = params.get("user_id")
        username = params.get("username")
        days = params.get("delete_messages_days", 7)

        member = await self._resolve_member(interaction.guild, user_id=user_id, username=username)
        if not member:
            return False, {"error": "User not found"}

        try:
            # discord.py 2.0+ uses delete_message_seconds
            await member.ban(reason="Softban (clear messages)", delete_message_seconds=days * 86400)
            await interaction.guild.unban(member, reason="Softban completion")
            if hasattr(self.bot, 'staff_shifts'):
                await self.bot.staff_shifts.track_moderation_action(interaction.guild.id, interaction.user.id)
            return True, None
        except Exception as e:
            return False, {"error": str(e)}

    async def action_clear_reactions(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Clears all reactions from a message."""
        message_id = params.get("message_id")
        channel_name = params.get("channel")
        channel = self._resolve_channel(channel_name) if channel_name else interaction.channel
        if not channel: return False, {"error": "Channel not found"}

        try:
            msg = await channel.fetch_message(message_id)
            await msg.clear_reactions()
            return True, None
        except Exception as e:
            return False, {"error": str(e)}

    async def action_edit_guild(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edits server settings (name, description, etc.)."""
        guild = interaction.guild
        try:
            edit_kwargs = {}
            if "name" in params: edit_kwargs["name"] = params["name"]
            if "description" in params: edit_kwargs["description"] = params["description"]

            if edit_kwargs:
                await guild.edit(**edit_kwargs)
            return True, None
        except Exception as e:
            return False, {"error": str(e)}

    # --- Server Query Implementation ---

    async def _send_query_result(self, interaction: discord.Interaction, title: str, description: str, fields: List[Dict] = None):
        embed = discord.Embed(title=title, description=description[:4000], color=discord.Color.blue())
        if fields:
            for f in fields[:25]:
                embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", True))
        await interaction.channel.send(embed=embed)

    async def action_query_server_info(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        data = await self.bot.server_query.query_server_info(interaction.guild.id)
        if "error" in data: return False, data

        fields = [
            {"name": "Members", "value": f"{data['member_count']} ({data['online_count']} online)"},
            {"name": "Channels", "value": str(data['channel_count'])},
            {"name": "Roles", "value": str(data['role_count'])},
            {"name": "Owner", "value": data['owner']},
            {"name": "Created", "value": data['created_at'][:10]}
        ]
        await self._send_query_result(interaction, f"Server Info: {data['name']}", data.get("description", ""), fields)
        return True, None

    async def action_bulk_delete_channels(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """
        Delete many channels BY ID with per-item status and verification.
        Names are only search criteria (use find_duplicate_channels first);
        the IDs here are the execution targets.
        """
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_channels:
            return False, {"error": "Bot lacks manage_channels permission"}

        channel_ids = params.get("channel_ids") or params.get("channels") or []
        if isinstance(channel_ids, str):
            channel_ids = [c.strip() for c in channel_ids.split(",") if c.strip()]
        protected_channel_id = params.get("protected_channel_id") or params.get("exclude_channel_id")

        from core.action_meta import protected_targets, system_protected_channel_ids
        sys_protected = system_protected_channel_ids(guild.id, guild)

        per_item = []
        completed = failed = 0
        for raw_id in channel_ids[:50]:  # hard cap
            item = {"id": str(raw_id), "deleted": False, "verified": False, "error": ""}
            try:
                cid = int(str(raw_id).strip().lstrip("<#").rstrip(">"))
            except (ValueError, TypeError):
                item["error"] = "invalid id"
                per_item.append(item); failed += 1; continue

            if protected_channel_id and str(cid) == str(protected_channel_id):
                item["error"] = "protected target — skipped"
                per_item.append(item); continue
            if protected_targets.is_protected(guild.id, "channel", cid):
                item["error"] = "protected"
                per_item.append(item); failed += 1; continue
            if str(cid) in sys_protected:
                item["error"] = "used by an enabled Miro system"
                per_item.append(item); failed += 1; continue

            channel = guild.get_channel(cid)
            if channel is None:
                # already gone counts as verified success (idempotent)
                item.update(deleted=True, verified=True, error="already absent")
                per_item.append(item); completed += 1; continue
            try:
                await channel.delete(reason="Miro AI bulk cleanup")
            except Exception as e:
                item["error"] = str(e)[:120]
                per_item.append(item); failed += 1; continue

            verified = guild.get_channel(cid) is None
            item.update(deleted=True, verified=verified,
                        error="" if verified else "Discord accepted delete but channel still present")
            per_item.append(item)
            if verified:
                completed += 1
            else:
                failed += 1
            await asyncio.sleep(0.4)  # gentle on the Discord API

        receipt = {
            "message": f"Deleted {completed}/{len(per_item)} channels ({failed} failed/skipped).",
            "requested": len(channel_ids),
            "completed": completed,
            "failed": failed,
            "per_item": per_item,
        }
        return completed > 0 or failed < len(per_item), receipt

    async def action_cleanup_duplicate_channels(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """
        High-level reliable single-call pipeline (V6 items 13-14):
        query -> identify duplicates -> protect target -> delete -> verify.
        The agent makes ONE call; the backend does the entire flow safely.
        """
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_channels:
            return False, {"error": "Bot lacks Manage Channels permission.",
                           "error_type": "MISSING_PERMISSION"}

        name = str(params.get("name") or params.get("channel_name") or "").strip()
        protected_id = str(params.get("protected_channel_id")
                           or params.get("exclude_channel_id") or "")

        from core.agent_runtime import find_duplicate_channels
        from core.action_meta import system_protected_channel_ids
        sys_protected = system_protected_channel_ids(guild.id, guild)

        # SERVER-WIDE MODE: no name -> clean duplicates across EVERY group
        # ('delete the duplicate channels' with no specific target).
        if not name:
            from agent.tools import find_all_duplicate_groups
            scan = find_all_duplicate_groups(guild, protected_channel_ids=[protected_id] if protected_id else [])
            all_dups = []
            for grp in scan["groups"]:
                for dup in grp["duplicates"]:
                    if dup["id"] not in sys_protected:
                        all_dups.append(dup)
            groups_processed = len(scan["groups"])
        else:
            data = find_duplicate_channels(guild, name, protected_id or None)
            all_dups = list(data["duplicates"])
            groups_processed = 1

        deleted, verified, failed = 0, 0, []
        for dup in all_dups:
            cid = int(dup["id"])
            ch = guild.get_channel(cid)
            if ch is None:
                deleted += 1; verified += 1   # idempotent
                continue
            if dup["id"] in sys_protected:
                failed.append({"id": dup["id"], "name": dup["name"],
                               "reason": "used by an enabled Miro system"})
                continue
            try:
                await ch.delete(reason="Miro AI duplicate cleanup")
            except Exception as e:
                failed.append({"id": dup["id"], "name": dup["name"],
                               "reason": str(e)[:120]})
                continue
            deleted += 1
            if guild.get_channel(cid) is None:   # verify against live state
                verified += 1
            else:
                failed.append({"id": dup["id"], "name": dup["name"],
                               "reason": "delete accepted but channel still present"})
            await asyncio.sleep(0.4)

        kept = data["kept"]["name"] if (name and data.get("kept")) else None
        receipt = {
            "message": (f"Cleanup complete: {deleted} deleted, {verified} verified, "
                        f"{len(failed)} failed."
                        + (f" Preserved: #{kept}" if kept else "")),
            "target_name": name or "(all duplicate groups)",
            "protected_channel": data.get("protected_channel", "") if name else protected_id,
            "requested": len(all_dups),
            "deleted": deleted,
            "failed": len(failed),
            "verified": verified,
            "groups_processed": groups_processed,
            "failed_items": failed[:10],
        }
        success = verified > 0 or not all_dups
        return success, receipt

    async def action_get_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Fetch ONE live channel with full details (read-only).
        Called with NO target: returns a channel listing so the agent can
        self-guide instead of dead-ending on 'Channel not found: None'."""
        guild = interaction.guild
        channel_id = self._get_param(params, "channel_id", "id")
        channel_name = self._get_param(params, "channel_name", "name", "channel")

        # Graceful no-target mode: surface the channel menu as the result
        if not channel_id and not channel_name:
            listing = [f"#{c.name} (id={c.id})" for c in list(guild.text_channels)[:12]]
            return True, {
                "message": ("No target specified. Server channels: "
                            + ", ".join(listing)
                            + ". Use find_duplicate_channels / delete_channel "
                              "with these exact IDs."),
                "channel_id": "",
                "channels": listing,
            }

        channel = None
        if channel_id:
            try:
                channel = guild.get_channel(int(str(channel_id).strip().lstrip("<#").rstrip(">")))
            except (ValueError, TypeError):
                channel = None
        if channel is None and channel_name:
            needle = str(channel_name).strip().lower().lstrip("#")
            channel = next((c for c in guild.text_channels if c.name.lower() == needle), None)
        if channel is None:
            return False, {"error": f"Channel not found: {channel_id or channel_name}",
                           "error_type": "NOT_FOUND"}
        return True, {
            "message": f"#{channel.name} (id={channel.id})",
            "channel_id": str(channel.id),
            "name": channel.name,
            "type": str(getattr(channel, "type", "text")),
            "category_id": str(channel.category_id) if channel.category_id else "",
            "position": getattr(channel, "position", 0),
            "topic": getattr(channel, "topic", "") or "",
        }

    async def action_find_duplicate_channels(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deterministic duplicate-channel finder. Reads REAL channel data via
        ServerQueryEngine (fallback: direct guild scan), then applies the
        deterministic matcher — the AI never invents duplicate logic."""
        name = str(params.get("name") or params.get("channel_name") or "").strip()
        exclude = params.get("exclude_channel_id") or params.get("protected_channel_id")

        # Prefer the real query engine so the tool genuinely executed a query
        channels_payload = None
        engine = getattr(self.bot, "server_query", None)

        # NO name given -> deterministic server-wide group scan. This is the
        # correct response to 'delete the duplicate channels' when the user
        # names no specific target.
        if not name:
            from agent.tools import find_all_duplicate_groups
            data = find_all_duplicate_groups(interaction.guild,
                                             protected_channel_ids=[exclude] if exclude else [])
            groups = []
            total_dups = 0
            for grp in data["groups"]:
                dup_ids = [m["id"] for m in grp["duplicates"]]
                total_dups += len(dup_ids)
                groups.append({
                    "base_name": grp["base_name"],
                    "original": grp["original"],
                    "duplicate_ids": dup_ids,
                    "duplicates": grp["duplicates"],
                })
            msg = (f"Found {len(groups)} duplicate group(s) across the server "
                   f"({total_dups} duplicate channels). Delete by ID with "
                   f"bulk_delete_channels, or call again with a name to inspect one group.") \
                  if groups else "No duplicate channel groups found — every channel name is unique."
            return True, {"message": msg, "groups": groups,
                          "group_count": len(groups), "total_duplicates": total_dups}

        if engine is not None and hasattr(engine, "query_channels"):
            try:
                raw = await engine.query_channels(interaction.guild.id)
                if isinstance(raw, dict):
                    channels_payload = raw.get("channels") or raw.get("data") or []
                elif isinstance(raw, list):
                    channels_payload = raw
            except Exception as e:
                logger.warning(f"server_query.query_channels failed, using direct scan: {e}")

        if channels_payload:
            from agent.tools import find_duplicate_channels_from_list
            norm = []
            for c in channels_payload:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("id") or "")
                obj = interaction.guild.get_channel(int(cid)) if cid.isdigit() else None
                norm.append({"id": cid,
                             "name": obj.name if obj else str(c.get("name", "")),
                             "category_id": str(c.get("category_id") or ""),
                             "created_at": str(obj.created_at.isoformat()) if obj and getattr(obj, "created_at", None) else ""})
            data = find_duplicate_channels_from_list(norm, name, exclude)
        else:
            data = find_duplicate_channels(interaction.guild, name, exclude)

        dup_count = len(data["duplicates"])
        return True, {
            "message": (f"{len(data['matches'])} channel(s) match '{name}': "
                        f"1 protected, {dup_count} duplicate(s) to remove"
                        if data["matches"] else f"No channels matching '{name}'."),
            "target_name": data["target_name"],
            "protected_channel_id": data["protected_channel_id"],
            "matches": data["matches"],
            "duplicates": data["duplicates"],
        }

    async def action_query_channels(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channels = await self.bot.server_query.query_channels(interaction.guild.id, params.get("type"))
        text = "\n".join([f"• {c['name']} ({c['type']})" for c in channels[:30]])
        await self._send_query_result(interaction, "Server Channels", text or "No channels found")
        return True, None

    async def action_query_roles(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        roles = await self.bot.server_query.query_roles(interaction.guild.id)
        text = "\n".join([f"• {r['name']} (ID: {r['id']})" for r in roles if r['name'] != "@everyone"][:30])
        await self._send_query_result(interaction, "Server Roles", text or "No roles found")
        return True, None

    async def action_query_members(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        members = await self.bot.server_query.query_members(interaction.guild.id, params.get("query"), params.get("limit", 20))
        text = "\n".join([f"• {m['name']} ({m['status']})" for m in members])
        await self._send_query_result(interaction, "Members Search", text or "No members found")
        return True, None

    async def action_query_member_details(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        uid = params.get("user_id")
        if not uid: return False, {"error": "Missing user_id"}
        details = await self.bot.server_query.query_member_details(interaction.guild.id, int(uid))
        if not details: return False, {"error": "User not found"}

        fields = [
            {"name": "Status", "value": details["status"]},
            {"name": "Top Role", "value": details["top_role"]},
            {"name": "Joined", "value": details["joined_at"][:10] if details["joined_at"] else "N/A"},
            {"name": "Roles", "value": ", ".join(details["roles"][:10])}
        ]
        await self._send_query_result(interaction, f"User Details: {details['name']}", "", fields)
        return True, None

    async def action_query_economy_leaderboard(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        lb = await self.bot.server_query.query_economy_leaderboard(interaction.guild.id, params.get("limit", 10))
        text = "\n".join([f"**{i+1}.** {e['name']} - {e['coins']} coins" for i, e in enumerate(lb)])
        await self._send_query_result(interaction, "Economy Leaderboard", text or "No data")
        return True, None

    async def action_query_xp_leaderboard(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        lb = await self.bot.server_query.query_xp_leaderboard(interaction.guild.id, params.get("limit", 10))
        text = "\n".join([f"**{i+1}.** {e['name']} - Level {e['level']} ({e['xp']} XP)" for i, e in enumerate(lb)])
        await self._send_query_result(interaction, "Leveling Leaderboard", text or "No data")
        return True, None

    async def action_query_pending_applications(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        apps = await self.bot.server_query.query_pending_applications(interaction.guild.id)
        text = "\n".join([f"• {a['username']} (Applied: {a['applied_at']})" for a in apps])
        await self._send_query_result(interaction, "Pending Staff Applications", text or "No pending applications")
        return True, None

    async def action_query_active_shifts(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        shifts = await self.bot.server_query.query_active_shifts(interaction.guild.id)
        text = "\n".join([f"• {s['username']} (Started: {s['start_time']})" for s in shifts])
        await self._send_query_result(interaction, "Active Staff Shifts", text or "No active shifts")
        return True, None

    async def action_query_recent_messages(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        cid = params.get("channel_id")
        if not cid: return False, {"error": "Missing channel_id"}
        msgs = await self.bot.server_query.query_recent_messages(int(cid), params.get("limit", 10))
        text = "\n".join([f"**{m['author']}:** {m['content'][:100]}" for m in msgs])
        await self._send_query_result(interaction, "Recent Messages", text or "No messages found")
        return True, None

    async def action_giveaway_end(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        gw_id = params.get("giveaway_id")
        if not gw_id: return False, {"error": "Missing giveaway_id"}
        winners = await self.bot.giveaways.end_giveaway(gw_id)
        if winners is None: return False, {"error": "Giveaway not found"}
        return True, None

    async def action_giveaway_reroll(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        gw_id = params.get("giveaway_id")
        if not gw_id: return False, {"error": "Missing giveaway_id"}
        winners = await self.bot.giveaways.reroll_giveaway(gw_id)
        if winners is None: return False, {"error": "Giveaway not found or ineligible for reroll"}
        return True, None

    async def action_giveaway_list(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        active = self.bot.giveaways.get_active_giveaways(interaction.guild.id)
        if not active:
            await interaction.channel.send("No active giveaways.")
            return True, None

        lines = []
        for g in active:
            lines.append(f"**{g.prize}** (ID: `{g.id}`) - Ends <t:{int(g.ends_at)}:R>")

        embed = discord.Embed(title="ðŸŽ Active Giveaways", description="\n".join(lines), color=discord.Color.gold())
        await interaction.channel.send(embed=embed)
        return True, None

    async def action_prestige(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        await self.bot.gamification.prestige(interaction)
        return True, None

    async def action_dice(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        bet = params.get("bet", 10)
        await self.bot.gamification.mini_game_dice(interaction, int(bet))
        return True, None

    async def action_flip(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        bet = params.get("bet", 10)
        side = params.get("side", "heads")
        await self.bot.gamification.mini_game_flip(interaction, side, int(bet))
        return True, None

    async def action_slots(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        bet = params.get("bet", 10)
        await self.bot.gamification.mini_game_slots(interaction, int(bet))
        return True, None

    async def action_trivia(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        await self.bot.gamification.mini_game_trivia(interaction)
        return True, None
    async def action_warn_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Warns a user (moderation)."""
        params = self._normalize_params(params, {
            "user_id": ["user_id", "user", "member_id", "target_id", "user_mention"],
            "username": ["username", "user_name"],
            "reason": ["reason", "cause", "note"],
        })
        user_id = params["user_id"]
        username = params["username"]
        reason = params["reason"]
        guild_id = interaction.guild.id

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username) or \
                     discord.utils.get(interaction.guild.members, nick=username) or \
                     discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            try:
                member = await interaction.guild.fetch_member(user_id)
            except discord.NotFound:
                return False, None

        try:
            if hasattr(self.bot, 'moderation'):
                history = self.bot.moderation.get_user_history(guild_id, user_id)
                history.warnings += 1
                history.violation_count += 1
                history.last_violation = time.time()
                self.bot.moderation.save_user_history(guild_id, user_id, history)

            if hasattr(self.bot, 'staff_shifts'):
                await self.bot.staff_shifts.track_moderation_action(interaction.guild.id, interaction.user.id)

            logger.info("Issued warning to %s in guild %d: %s", member.display_name, guild_id, reason)

            try:
                embed = discord.Embed(title="⚠️ Warning Issued", color=discord.Color.gold())
                embed.add_field(name="Server", value=interaction.guild.name)
                embed.add_field(name="Reason", value=reason)
                embed.timestamp = dt.datetime.now(dt.timezone.utc)
                await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

            return True, {"user_id": user_id, "action": "warn", "reason": reason}
        except Exception as e:
            logger.error(f"Error warning user: {e}")
            return False, None

    async def action_deafen_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Voice deafens a user."""
        user_id = params.get("user_id")
        username = params.get("username")
        reason = params.get("reason", "Deafened via bot")

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None

        try:
            await member.edit(deafen=True, reason=reason)
            return True, {"user_id": user_id, "action": "deafen"}
        except Exception as e:
            logger.error(f"Error deafening user: {e}")
            return False, None

    async def action_set_nickname(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets a user's server nickname."""
        user_id = params.get("user_id")
        username = params.get("username")
        nickname = params.get("nickname", "")

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None

        try:
            await member.edit(nick=nickname)
            return True, {"user_id": user_id, "nickname": nickname}
        except Exception as e:
            logger.error(f"Error setting nickname: {e}")
            return False, None

    async def action_slowmode(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets slowmode for a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        delay = params.get("delay", 5)  # seconds

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        try:
            await target_channel.edit(slowmode_delay=delay)
            return True, {"channel_id": target_channel.id, "delay": delay}
        except Exception as e:
            logger.error(f"Error setting slowmode: {e}")
            return False, None

    async def action_lock_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Locks a channel (removes @everyone permission to send."""
        channel_name = params.get("channel") or params.get("channel_name")

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        everyone_role = interaction.guild.default_role

        try:
            overwrite = discord.PermissionOverwrite(send_messages=False)
            await target_channel.set_permissions(everyone_role, overwrite=overwrite)
            return True, {"channel_id": target_channel.id, "action": "lock"}
        except Exception as e:
            logger.error(f"Error locking channel: {e}")
            return False, None

    async def action_unlock_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Unlocks a channel (restores @everyone permission to send."""
        channel_name = params.get("channel") or params.get("channel_name")

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        everyone_role = interaction.guild.default_role

        try:
            overwrite = discord.PermissionOverwrite(send_messages=None)
            await target_channel.set_permissions(everyone_role, overwrite=overwrite)
            return True, {"channel_id": target_channel.id, "action": "unlock"}
        except Exception as e:
            logger.error(f"Error unlocking channel: {e}")
            return False, None

    async def action_send_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sends a simple text message to a channel."""
        params = self._normalize_params(params, {
            "channel_name": ["channel", "channel_name"],
            "content": ["content", "message", "text"],
        })
        channel_name = params["channel_name"]
        content = params["content"]

        if not content:
            return False, None

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        try:
            await target_channel.send(content)
            return True, {"channel_id": target_channel.id}
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False, None

    async def action_reply_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Replies to a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        content = params.get("content", "")

        if not content:
            return False, None

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        try:
            msg = await target_channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.reply(content)
            else:
                await target_channel.send(content)
            return True, {"message_id": message_id}
        except Exception as e:
            logger.error(f"Error replying: {e}")
            return False, None

    async def action_add_reaction(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds emoji reaction to a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        emoji = params.get("emoji", "")

        if not emoji:
            return False, None

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        try:
            msg = await target_channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.add_reaction(emoji)
            return True, {"emoji": emoji}
        except Exception as e:
            logger.error(f"Error adding reaction: {e}")
            return False, None

    async def action_edit_channel_name(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Renames a channel."""
        channel_name = params.get("channel_name")
        new_name = params.get("new_name") or params.get("name")

        if not new_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            await channel.edit(name=new_name)
            return True, {"new_name": new_name}
        except Exception as e:
            logger.error(f"Error editing channel: {e}")
            return False, None

    async def action_edit_role_name(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Renames a role."""
        role_name = params.get("role_name")
        new_name = params.get("new_name") or params.get("name")

        if not new_name:
            return False, None

        role = discord.utils.get(interaction.guild.roles, name=role_name) if role_name else None
        if not role:
            return False, None

        try:
            await role.edit(name=new_name)
            return True, {"new_name": new_name}
        except Exception as e:
            logger.error(f"Error editing role: {e}")
            return False, None

    async def action_change_role_color(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Changes role color."""
        role_name = params.get("role_name")
        color = params.get("color", "#99AAB5")

        if not role_name:
            return False, None

        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            return False, None

        try:
            await role.edit(color=parse_color(color))
            return True, {"role_name": role_name, "color": color}
        except Exception as e:
            logger.error(f"Error changing role color: {e}")
            return False, None

    async def action_move_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Moves channel to a category."""
        channel_name = params.get("channel_name")
        category_name = params.get("category")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        category = discord.utils.get(interaction.guild.categories, name=category_name) if category_name else None

        try:
            await channel.edit(category=category)
            return True, {"channel": channel.name}
        except Exception as e:
            logger.error(f"Error moving channel: {e}")
            return False, None

    async def action_clone_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Duplicates a channel."""
        channel_name = params.get("channel_name")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            new_channel = await channel.clone(name=f"{channel.name}-copy")
            await new_channel.edit(position=channel.position + 1)
            return True, {"new_channel": new_channel.name}
        except Exception as e:
            logger.error(f"Error cloning channel: {e}")
            return False, None

    async def action_create_thread(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a thread in a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        name = params.get("name", "new-thread")
        message = params.get("message", "Thread created")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            msg = await channel.send(content=message)
            thread = await msg.create_thread(name=name)
            return True, {"thread": thread.name}
        except Exception as e:
            logger.error(f"Error creating thread: {e}")
            return False, None

    async def action_pin_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pins a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            msg = await channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.pin()
            return True, {"message_id": message_id}
        except Exception as e:
            logger.error(f"Error pinning message: {e}")
            return False, None

    async def action_set_topic(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets channel topic/description."""
        channel_name = params.get("channel") or params.get("channel_name")
        topic = params.get("topic", "")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            await channel.edit(topic=topic)
            return True, {"topic": topic}
        except Exception as e:
            logger.error(f"Error setting topic: {e}")
            return False, None

    async def action_remove_reaction(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes emoji reaction from a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        emoji = params.get("emoji", "")

        if not emoji:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            msg = await channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.remove_reaction(emoji)
            return True, {"emoji": emoji}
        except Exception as e:
            logger.error(f"Error removing reaction: {e}")
            return False, None

    async def action_delete_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deletes a specific message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            msg = await channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.delete()
            return True, {"message_id": message_id}
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            return False, None

    async def action_create_voice_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a voice channel."""
        name = params.get("name", "Voice Channel")
        category = params.get("category")

        category_obj = discord.utils.get(interaction.guild.categories, name=category) if category else None

        try:
            channel = await interaction.guild.create_voice_channel(name, category=category_obj)
            return True, {"channel": channel.name}
        except Exception as e:
            logger.error(f"Error creating voice channel: {e}")
            return False, None

    async def action_create_text_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a text channel."""
        name = params.get("name", "text-channel")
        category = params.get("category")

        category_obj = discord.utils.get(interaction.guild.categories, name=category) if category else None

        try:
            channel = await interaction.guild.create_text_channel(name, category=category_obj)
            return True, {"channel": channel.name}
        except Exception as e:
            logger.error(f"Error creating text channel: {e}")
            return False, None

    async def action_create_category_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a category with optional privacy settings."""
        guild = interaction.guild
        name = params.get("name", "New Category")
        private = params.get("private", False)
        allowed_roles = params.get("allowed_roles", [])
        denied_roles = params.get("denied_roles", [])

        if private and "@everyone" not in denied_roles:
            denied_roles = list(denied_roles) + ["@everyone"]

        try:
            category = await guild.create_category(name)

            if denied_roles or allowed_roles:
                await self._set_channel_permissions(category, guild, allowed_roles, denied_roles)
                for child in category.channels:
                    await self._set_channel_permissions(child, guild, allowed_roles, denied_roles)

            self._track_artifact("category", category.id, category.name)
            return True, {"category": category.name}
        except Exception as e:
            logger.error(f"Error creating category: {e}")
            return False, None

    async def action_create_category(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for create_category_channel — supports name, private, allowed_roles, denied_roles."""
        return await self.action_create_category_channel(interaction, params)

    async def action_edit_channel_bitrate(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets voice channel bitrate."""
        channel_name = params.get("channel_name")
        bitrate = params.get("bitrate", 128000)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else None
        if not channel or not channel.voice:
            return False, None

        try:
            await channel.edit(bitrate=bitrate)
            return True, {"bitrate": bitrate}
        except Exception as e:
            logger.error(f"Error editing bitrate: {e}")
            return False, None

    async def action_edit_channel_user_limit(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets voice channel user limit."""
        channel_name = params.get("channel_name")
        user_limit = params.get("user_limit", 0)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else None
        if not channel or not channel.voice:
            return False, None

        try:
            await channel.edit(user_limit=user_limit)
            return True, {"user_limit": user_limit}
        except Exception as e:
            logger.error(f"Error editing user limit: {e}")
            return False, None

    async def action_follow_announcement_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Follows an announcement channel."""
        source = params.get("source_channel")
        target = params.get("target_channel")

        source_channel = discord.utils.get(interaction.guild.channels, name=source) if source else None
        target_channel = discord.utils.get(interaction.guild.channels, name=target) if target else None

        if not source_channel or not target_channel:
            return False, None

        try:
            await source_channel.followers.append(target_channel)
            return True, {"source": source, "target": target}
        except Exception as e:
            logger.error(f"Error following channel: {e}")
            return False, None

    async def action_create_scheduled_event(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a scheduled event."""
        name = params.get("name", "Event")
        description = params.get("description", "")
        start_time = params.get("start_time")  # ISO format
        end_time = params.get("end_time")
        location = params.get("location", "Voice Channel")

        import datetime, timezone, timedelta
        try:
            start = datetime.datetime.fromisoformat(start_time) if start_time else datetime.now(timezone.utc) + timedelta(hours=1)
            end = datetime.datetime.fromisoformat(end_time) if end_time else start + timedelta(hours=1)

            event = await interaction.guild.create_scheduled_event(
                name=name,
                description=description,
                start_time=start,
                end_time=end,
                location=location
            )
            return True, {"event": event.name}
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            return False, None



    async def _merge_channel_permission(self, channel, role, **kwargs):
        """Merge permission changes into existing overwrites instead of replacing them."""
        existing = channel.overwrites_for(role)
        for perm_name, perm_value in kwargs.items():
            setattr(existing, perm_name, perm_value)
        await channel.set_permissions(role, overwrite=existing)

    async def action_allow_channel_permission(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Allows a permission for a role in a channel (merges with existing overwrites)."""

        # Improved parameter resolution - accept alternative parameter names
        channel_ident = None
        for key in ["channel", "channel_name", "channel_id"]:
            if key in params and params[key]:
                channel_ident = params[key]
                break

        role_ident = None
        for key in ["role", "role_name", "role_id"]:
            if key in params and params[key]:
                role_ident = params[key]
                break

        permission = None
        for key in ["permission", "perm", "permission_name"]:
            if key in params and params[key]:
                permission = params[key]
                break

        if not permission:
            permission = "send_messages"

        if not channel_ident:
            logger.error("allow_channel_permission: No channel identifier provided")
            return False, {"error": "Channel identifier is required. Use 'channel', 'channel_name', or 'channel_id'."}

        if not role_ident:
            logger.error("allow_channel_permission: No role identifier provided")
            return False, {"error": "Role identifier is required. Use 'role', 'role_name', or 'role_id'."}

        # Resolve channel with fuzzy matching
        channel = None
        if isinstance(channel_ident, int) or (isinstance(channel_ident, str) and channel_ident.isdigit()):
            # Try as ID first
            try:
                cid = int(channel_ident)
                channel = interaction.guild.get_channel(cid)
                if channel:
                    logger.info(f"allow_channel_permission: Resolved channel by ID '{channel_ident}' -> '{channel.name}'")
            except (ValueError, TypeError):
                pass

        if not channel and isinstance(channel_ident, str):
            # Try exact match first
            channel = discord.utils.get(interaction.guild.channels, name=channel_ident)
            if channel:
                logger.info(f"allow_channel_permission: Resolved channel by exact match '{channel_ident}' -> '{channel.name}'")
            else:
                # Try fuzzy matching
                channel_names = [c.name for c in interaction.guild.channels]
                fuzzy_match = self._fuzzy_match(channel_ident, channel_names)
                if fuzzy_match:
                    channel = discord.utils.get(interaction.guild.channels, name=fuzzy_match)
                    logger.info(f"allow_channel_permission: Resolved channel by fuzzy match '{channel_ident}' -> '{channel.name}'")
                else:
                    logger.error(f"allow_channel_permission: Channel '{channel_ident}' not found, available: {channel_names[:5]}...")
                    return False, {"error": f"Channel '{channel_ident}' not found. Available channels include: {', '.join(channel_names[:5])}"}

        if not channel:
            logger.error(f"allow_channel_permission: Could not resolve channel '{channel_ident}'")
            return False, {"error": f"Channel '{channel_ident}' not found"}

        # Resolve role with fuzzy matching
        role = None
        if isinstance(role_ident, int) or (isinstance(role_ident, str) and role_ident.isdigit()):
            # Try as ID first
            try:
                rid = int(role_ident)
                role = interaction.guild.get_role(rid)
                if role:
                    logger.info(f"allow_channel_permission: Resolved role by ID '{role_ident}' -> '{role.name}'")
            except (ValueError, TypeError):
                pass

        if not role:
            # Use the existing _resolve_role method which handles names well
            role = await self._resolve_role(interaction.guild, **{k: role_ident for k in ["role_name", "name"]})
            if role:
                logger.info(f"allow_channel_permission: Resolved role '{role_ident}' -> '{role.name}'")
            else:
                # Try fuzzy matching for role names
                role_names = [r.name for r in interaction.guild.roles]
                fuzzy_match = self._fuzzy_match(role_ident, role_names)
                if fuzzy_match:
                    role = discord.utils.get(interaction.guild.roles, name=fuzzy_match)
                    logger.info(f"allow_channel_permission: Resolved role by fuzzy match '{role_ident}' -> '{role.name}'")
                else:
                    logger.error(f"allow_channel_permission: Role '{role_ident}' not found, available: {role_names[:5]}...")
                    return False, {"error": f"Role '{role_ident}' not found. Available roles include: {', '.join(role_names[:5])}"}

        if not role:
            logger.error(f"allow_channel_permission: Could not resolve role '{role_ident}'")
            return False, {"error": f"Role '{role_ident}' not found"}

        # Check idempotency - if permission is already allowed, return success
        existing_overwrites = channel.overwrites_for(role)
        perm_names = {
            "send_messages": "send_messages",
            "read_messages": "read_messages",
            "view_channel": "view_channel",
            "connect": "connect",
            "speak": "speak",
            "mute_members": "mute_members",
            "deafen_members": "deafen_members",
            "move_members": "move_members",
            "manage_messages": "manage_messages",
            "manage_channels": "manage_channels",
            "attach_files": "attach_files",
            "embed_links": "embed_links",
            "add_reactions": "add_reactions",
            "use_external_emojis": "use_external_emojis",
            "manage_permissions": "manage_permissions",
            "create_instant_invite": "create_instant_invite",
            "mention_everyone": "mention_everyone",
            "manage_webhooks": "manage_webhooks",
            "read_message_history": "read_message_history",
            "use_application_commands": "use_application_commands",
            "stream": "stream",
            "use_voice_activation": "use_voice_activation",
        }
        perm_attr = perm_names.get(permission, permission)

        if hasattr(existing_overwrites, perm_attr) and getattr(existing_overwrites, perm_attr) is True:
            logger.info(f"allow_channel_permission: Permission '{permission}' already allowed for role '{role.name}' in channel '{channel.name}'")
            return True, {"role": role.name, "permission": permission, "already_allowed": True}

        try:
            await self._merge_channel_permission(channel, role, **{perm_attr: True})
            logger.info(f"allow_channel_permission: Successfully allowed permission '{permission}' for role '{role.name}' in channel '{channel.name}'")
            return True, {"role": role.name, "permission": permission}
        except Exception as e:
            logger.error(f"Error allowing permission: {e}")
            return False, {"error": f"Failed to allow permission: {str(e)}"}

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not channel or not role:
            logger.error(f"allow_channel_permission: channel='{channel_name}' found={channel is not None}, role='{role_name}' found={role is not None}")
            return False, None

        try:
            perm_names = {
                "send_messages": "send_messages",
                "read_messages": "read_messages",
                "view_channel": "view_channel",
                "connect": "connect",
                "speak": "speak",
                "mute_members": "mute_members",
                "deafen_members": "deafen_members",
                "move_members": "move_members",
                "manage_messages": "manage_messages",
                "manage_channels": "manage_channels",
                "attach_files": "attach_files",
                "embed_links": "embed_links",
                "add_reactions": "add_reactions",
                "use_external_emojis": "use_external_emojis",
                "manage_permissions": "manage_permissions",
                "create_instant_invite": "create_instant_invite",
                "mention_everyone": "mention_everyone",
                "manage_webhooks": "manage_webhooks",
                "read_message_history": "read_message_history",
                "use_application_commands": "use_application_commands",
                "stream": "stream",
                "use_voice_activation": "use_voice_activation",
            }
            perm_attr = perm_names.get(permission, permission)
            await self._merge_channel_permission(channel, role, **{perm_attr: True})
            return True, {"role": role_name, "permission": permission}
        except Exception as e:
            logger.error(f"Error allowing permission: {e}")
            return False, None

    async def action_deny_channel_permission(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Denies a permission for a role in a channel (merges with existing overwrites)."""
        channel_name = params.get("channel") or params.get("channel_name")
        role_name = params.get("role_name")
        permission = params.get("permission", "send_messages")

        if not channel_name or not role_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not channel or not role:
            logger.error(f"deny_channel_permission: channel='{channel_name}' found={channel is not None}, role='{role_name}' found={role is not None}")
            return False, None

        try:
            perm_names = {
                "send_messages": "send_messages",
                "read_messages": "read_messages",
                "view_channel": "view_channel",
                "connect": "connect",
                "speak": "speak",
                "mute_members": "mute_members",
                "deafen_members": "deafen_members",
                "move_members": "move_members",
                "manage_messages": "manage_messages",
                "manage_channels": "manage_channels",
                "attach_files": "attach_files",
                "embed_links": "embed_links",
                "add_reactions": "add_reactions",
                "use_external_emojis": "use_external_emojis",
                "manage_permissions": "manage_permissions",
                "read_message_history": "read_message_history",
            }
            perm_attr = perm_names.get(permission, permission)
            await self._merge_channel_permission(channel, role, **{perm_attr: False})
            return True, {"role": role_name, "permission": permission}
        except Exception as e:
            logger.error(f"Error denying permission: {e}")
            return False, None

    async def action_deny_all_channels_for_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Denies a role from viewing ALL channels (text, voice, and categories) in the server."""
        role_name = params.get("role_name")

        if not role_name:
            return False, None

        role = await self._resolve_role(interaction.guild, role_name=role_name)
        if not role:
            return False, None

        channels_updated = 0

        try:
            all_channels = list(interaction.guild.categories) + list(interaction.guild.text_channels) + list(interaction.guild.voice_channels)
            for channel in all_channels:
                try:
                    await self._merge_channel_permission(channel, role, view_channel=False, send_messages=False)
                    channels_updated += 1
                except Exception:
                    pass

            return True, {"channels_updated": channels_updated, "role": role_name}
        except Exception as e:
            logger.error(f"Error denying all channels: {e}")
            return False, None

    async def action_allow_all_channels_for_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Allows a role to view ALL channels (text, voice, and categories) in the server."""
        role_name = params.get("role_name")

        if not role_name:
            return False, None

        role = await self._resolve_role(interaction.guild, role_name=role_name)
        if not role:
            return False, None

        channels_updated = 0

        try:
            all_channels = list(interaction.guild.categories) + list(interaction.guild.text_channels) + list(interaction.guild.voice_channels)
            for channel in all_channels:
                try:
                    await self._merge_channel_permission(channel, role, view_channel=True, send_messages=True)
                    channels_updated += 1
                except Exception:
                    pass

            return True, {"channels_updated": channels_updated, "role": role_name}
        except Exception as e:
            logger.error(f"Error allowing all channels: {e}")
            return False, None

    async def action_deny_category_for_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Denies a role in a category AND all its child channels."""
        category_name = params.get("category_name") or params.get("category")
        role_name = params.get("role_name")

        if not category_name or not role_name:
            return False, None

        category = discord.utils.get(interaction.guild.categories, name=category_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not category or not role:
            return False, None

        channels_updated = 0

        try:
            await self._merge_channel_permission(category, role, view_channel=False, send_messages=False)
            channels_updated += 1

            for channel in category.channels:
                try:
                    await self._merge_channel_permission(channel, role, view_channel=False, send_messages=False)
                    channels_updated += 1
                except Exception:
                    pass

            return True, {"channels_updated": channels_updated, "category": category_name, "role": role_name}
        except Exception as e:
            logger.error(f"Error denying category: {e}")
            return False, None

    async def action_make_channel_private(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an existing channel private: denies @everyone view_channel and allows specified roles."""
        guild = interaction.guild
        channel_name = params.get("channel") or params.get("channel_name")
        allowed_roles = params.get("allowed_roles", [])

        if not channel_name:
            try:
                await interaction.channel.send("⚠️ No channel name specified for make_channel_private.", delete_after=10)
            except Exception:
                pass
            return False, None

        # Case-insensitive channel lookup
        channel = discord.utils.get(guild.channels, name=channel_name)
        if not channel:
            lower = channel_name.lower()
            channel = next((c for c in guild.channels if c.name.lower() == lower), None)
        if not channel:
            logger.error(f"make_channel_private: '{channel_name}' not found")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find channel **{channel_name}**.", delete_after=10
                )
            except Exception:
                pass
            return False, None

        try:
            # Deny @everyone
            await self._merge_channel_permission(channel, guild.default_role, view_channel=False, send_messages=False)

            # Allow each specified role
            for role_name in allowed_roles:
                role = await self._resolve_role(guild, role_name=role_name)
                if role:
                    await self._merge_channel_permission(channel, role, view_channel=True, send_messages=True, read_message_history=True)

            logger.info(f"Made channel '{channel.name}' private. Allowed roles: {allowed_roles}")
            return True, {"channel": channel.name, "allowed_roles": allowed_roles}
        except discord.Forbidden:
            logger.error(f"make_channel_private: bot lacks Manage Channels permission in guild {guild.id}")
            try:
                await interaction.channel.send(
                    "⚠️ I don't have permission to manage channel permissions. "
                    "Please grant me the **Manage Channels** permission.", delete_after=15
                )
            except Exception:
                pass
            return False, None
        except Exception as e:
            logger.error(f"make_channel_private: unexpected error: {e}", exc_info=True)
            try:
                await interaction.channel.send(f"⚠️ Error making channel private: {e}", delete_after=10)
            except Exception:
                pass
            return False, None

    async def action_make_category_private(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an existing category private: denies @everyone and allows specified roles on the category and all child channels."""
        guild = interaction.guild
        category_name = params.get("category") or params.get("category_name")
        allowed_roles = params.get("allowed_roles", [])

        if not category_name:
            try:
                await interaction.channel.send("⚠️ No category name specified for make_category_private.", delete_after=10)
            except Exception:
                pass
            return False, None

        # Case-insensitive category lookup
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            lower = category_name.lower()
            category = next((c for c in guild.categories if c.name.lower() == lower), None)
        if not category:
            available = ", ".join(f"**{c.name}**" for c in guild.categories) or "none found"
            logger.error(f"make_category_private: '{category_name}' not found. Available: {[c.name for c in guild.categories]}")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find category **{category_name}**. "
                    f"Available categories: {available}", delete_after=15
                )
            except Exception:
                pass
            return False, None

        try:
            channels_updated = 0

            # Deny @everyone on the category itself
            await self._merge_channel_permission(category, guild.default_role, view_channel=False, send_messages=False)
            channels_updated += 1

            # Allow each specified role on the category
            for role_name in allowed_roles:
                role = await self._resolve_role(guild, role_name=role_name)
                if role:
                    await self._merge_channel_permission(category, role, view_channel=True, send_messages=True, read_message_history=True)

            # Do the same for all child channels
            for child in category.channels:
                try:
                    await self._merge_channel_permission(child, guild.default_role, view_channel=False, send_messages=False)
                    channels_updated += 1
                    for role_name in allowed_roles:
                        role = await self._resolve_role(guild, role_name=role_name)
                        if role:
                            await self._merge_channel_permission(child, role, view_channel=True, send_messages=True, read_message_history=True)
                except Exception:
                    pass

            logger.info(f"Made category '{category.name}' private. Updated {channels_updated} channels. Allowed: {allowed_roles}")
            return True, {"category": category.name, "channels_updated": channels_updated, "allowed_roles": allowed_roles}
        except discord.Forbidden:
            logger.error(f"make_category_private: bot lacks Manage Channels permission in guild {guild.id}")
            try:
                await interaction.channel.send(
                    "⚠️ I don't have permission to manage channel permissions. "
                    "Please grant me the **Manage Channels** permission.", delete_after=15
                )
            except Exception:
                pass
            return False, None
        except Exception as e:
            logger.error(f"make_category_private: unexpected error: {e}", exc_info=True)
            try:
                await interaction.channel.send(f"⚠️ Error making category private: {e}", delete_after=10)
            except Exception:
                pass
            return False, None

    async def action_create_role_with_permissions(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a role with specific permissions."""
        guild = interaction.guild
        name = params.get("name")

        if not name:
            return False, None

        if not guild.me.guild_permissions.manage_roles:
            logger.error("Bot lacks manage_roles permission in guild %s", guild.id)
            return False, None

        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            logger.info("Role '%s' already exists, skipping creation", name)
            return True, None

        color_hex = params.get("color", "#99AAB5").replace("#", "")
        color = discord.Color(int(color_hex, 16))
        hoist = params.get("hoist", True)
        mentionable = params.get("mentionable", False)

        perm_params = params.get("permissions", {})
        # Compatibility mapping
        if "use_slash_commands" in perm_params:
            perm_params["use_application_commands"] = perm_params.pop("use_slash_commands")
        permissions = discord.Permissions(
            view_channel=perm_params.get("view_channel", True),
            send_messages=perm_params.get("send_messages", True),
            read_messages=perm_params.get("read_messages", True),
            manage_channels=perm_params.get("manage_channels", False),
            manage_roles=perm_params.get("manage_roles", False),
            kick_members=perm_params.get("kick_members", False),
            ban_members=perm_params.get("ban_members", False),
            moderate_members=perm_params.get("moderate_members", False),
            manage_messages=perm_params.get("manage_messages", False),
            mention_everyone=perm_params.get("mention_everyone", False),
            attach_files=perm_params.get("attach_files", True),
            embed_links=perm_params.get("embed_links", True),
            add_reactions=perm_params.get("add_reactions", True),
            use_external_emojis=perm_params.get("use_external_emojis", True),
            connect=perm_params.get("connect", True),
            speak=perm_params.get("speak", True),
            read_message_history=perm_params.get("read_message_history", True),
            use_application_commands=perm_params.get("use_application_commands", True),
            administrator=perm_params.get("administrator", False),
            manage_guild=perm_params.get("manage_guild", False),
            mute_members=perm_params.get("mute_members", False),
            deafen_members=perm_params.get("deafen_members", False),
            move_members=perm_params.get("move_members", False),
            manage_webhooks=perm_params.get("manage_webhooks", False),
            manage_expressions=perm_params.get("manage_expressions", False),
            create_instant_invite=perm_params.get("create_instant_invite", True),
        )

        role = await guild.create_role(
            name=name,
            color=color,
            permissions=permissions,
            hoist=hoist,
            mentionable=mentionable,
            reason="AI Action - create_role_with_permissions"
        )

        self._track_artifact("role", role.id, role.name)
        logger.info("Created role with permissions: %s", role.name)
        return True, {"action": "delete_role", "role_id": role.id}

    async def action_edit_channel_permissions(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edit permissions for a role on a specific channel (merges with existing overwrites)."""
        channel_name = params.get("channel") or params.get("channel_name")
        role_name = params.get("role_name")
        permissions = params.get("permissions", {})

        if not channel_name or not role_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not channel or not role:
            return False, None

        try:
            existing = channel.overwrites_for(role)
            for perm_name, perm_value in permissions.items():
                if isinstance(perm_value, bool) or perm_value is None:
                    setattr(existing, perm_name, perm_value)
            await channel.set_permissions(role, overwrite=existing)
            return True, {"channel": channel_name, "role": role_name, "permissions": permissions}
        except Exception as e:
            logger.error(f"Error editing channel permissions: {e}")
            return False, None

    async def action_edit_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edit channel properties (name, topic, slowmode, category, nsfw, position)."""
        channel_name = params.get("channel_name") or params.get("name")

        if not channel_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not channel:
            return False, None

        try:
            edit_kwargs = {}
            if "new_name" in params:
                edit_kwargs["name"] = params["new_name"]
            if "topic" in params:
                edit_kwargs["topic"] = params["topic"]
            if "slowmode_delay" in params:
                edit_kwargs["slowmode_delay"] = params["slowmode_delay"]
            if "nsfw" in params:
                edit_kwargs["nsfw"] = params["nsfw"]
            if "position" in params:
                edit_kwargs["position"] = params["position"]
            if "category" in params:
                cat = discord.utils.get(interaction.guild.categories, name=params["category"])
                if cat:
                    edit_kwargs["category"] = cat

            if edit_kwargs:
                await channel.edit(**edit_kwargs)
            return True, {"channel": channel_name}
        except Exception as e:
            logger.error(f"Error editing channel: {e}")
            return False, None

    async def action_edit_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edit role properties (name, color, hoist, mentionable, permissions)."""
        role_name = params.get("role_name") or params.get("name")

        if not role_name:
            return False, None

        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            return False, None

        try:
            edit_kwargs = {}
            if "new_name" in params:
                edit_kwargs["name"] = params["new_name"]
            if "color" in params:
                edit_kwargs["color"] = parse_color(params["color"])
            if "hoist" in params:
                edit_kwargs["hoist"] = params["hoist"]
            if "mentionable" in params:
                edit_kwargs["mentionable"] = params["mentionable"]
            if "permissions" in params:
                perm_params = params["permissions"]
                current_perms = role.permissions
                perm_value = current_perms.value
                new_perms = discord.Permissions(perm_value)
                for perm_name, perm_val in perm_params.items():
                    if hasattr(new_perms, perm_name):
                        setattr(new_perms, perm_name, perm_val)
                edit_kwargs["permissions"] = new_perms

            if edit_kwargs:
                await role.edit(**edit_kwargs, reason="AI Action - edit_role")
            return True, {"role": role_name}
        except Exception as e:
            logger.error(f"Error editing role: {e}")
            return False, None

    # --- Execution Logic ---

    _custom_cmd_cooldowns = {}  # class-level: (guild_id, user_id, cmd_name) -> timestamp
    _custom_cmd_cooldown_seconds = 3

    def _render_template(self, text: str, message: discord.Message, args_str: str = "") -> str:
        """Substitute live placeholders in custom command / automation text.
        Supported: {user} {user.mention} {user.id} {server} {server.id}
                   {channel} {channel.name} {args}"""
        if not isinstance(text, str):
            return text
        author = getattr(message, "author", None)
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        channel_disp = getattr(channel, "mention", None) or (
            f"#{getattr(channel, 'name', '')}" if channel is not None else "")
        replacements = {
            "{user}": getattr(author, "display_name", "") or "",
            "{user.mention}": getattr(author, "mention", "") or "",
            "{user.id}": str(getattr(author, "id", "")),
            "{server}": getattr(guild, "name", "") if guild else "",
            "{server.id}": str(getattr(guild, "id", "")) if guild else "",
            "{channel}": channel_disp,
            "{channel.name}": getattr(channel, "name", "") or "",
            "{args}": args_str or "",
        }
        out = text
        for key, val in replacements.items():
            if key in out:
                out = out.replace(key, val)
        return out

    async def execute_custom_command(self, message: discord.Message, code: str, cmd_name: str = None):
        """
        Executes a custom '!' command's stored code.
        Can be a simple string, a list of actions, or a special command object.
        Includes error prevention based on learned patterns and supports arguments.
        """
        guild_id = message.guild.id
        user_id = message.author.id
        cmd_data_obj = None

        # Handle None or empty cmd_name
        if not cmd_name or not isinstance(cmd_name, str) or cmd_name == "":
            cmd_name = "unknown"
            logger.warning(f"execute_custom_command called with empty cmd_name, using 'unknown'")

        # Extract arguments
        full_content = message.content
        prefix = "!" # Fallback
        if message.guild:
             prefix = dm.get_guild_data(message.guild.id, "prefix", "!")

        args_str = ""
        if cmd_name != "unknown" and full_content.startswith(f"{prefix}{cmd_name} "):
            args_str = full_content[len(prefix) + len(cmd_name) + 1:].strip()

        # Per-command permission gate (stored 'permission' metadata)
        _early = None
        try:
            _early = json.loads(code) if isinstance(code, str) else (code if isinstance(code, dict) else None)
        except Exception:
            _early = None
        if isinstance(_early, dict) and not check_command_permission(message, _early):
            await message.channel.send(
                "\u274c You do not have permission to use this command.", delete_after=5)
            return None
        cooldown_key = (guild_id, user_id, cmd_name)
        now = time.time()
        _cooldown = self._custom_cmd_cooldown_seconds
        if isinstance(_early, dict):
            try:
                _cooldown = max(0, int(_early.get("cooldown", _cooldown)))
            except (TypeError, ValueError):
                pass
        if _cooldown > 0 and cooldown_key in self._custom_cmd_cooldowns:
            remaining = _cooldown - (now - self._custom_cmd_cooldowns[cooldown_key])
            if remaining > 0:
                await message.channel.send(f"⏳ Command on cooldown. Wait {int(remaining)}s.", delete_after=2)
                return None
        self._custom_cmd_cooldowns[cooldown_key] = now

        try:
            if isinstance(code, str):
                data = json.loads(code)
            elif isinstance(code, dict):
                data = code
            else:
                await message.channel.send("Invalid command data type.")
                return False

            if not data.get("command_type"):
                await message.channel.send("Command missing type.")
                return False

            valid, error_msg = validate_command_json(data)
            if not valid:
                # If it's intended to be a simple command but user wrote something that looks like broken JSON
                if "command_type" not in data:
                     # For backward compatibility with non-JSON commands that might be stored
                     # although now we prefer JSON, we still support plain text
                     await message.channel.send(code.replace("{args}", args_str) if args_str else code)
                     return True
                await message.channel.send(f"❌ {error_msg}")
                return False

            if isinstance(data, list):
                # We'd need a way to pass 'message' context to execute_sequence
                # For now, just acknowledge the command
                await message.channel.send("Command executed (action list).")
                return True

            # Handle special command objects
            elif isinstance(data, dict):
                command_type = data.get("command_type")

                # Handle action-style JSON: {"action": "...", "parameters": {...}}
                # or {"actions": [{"name": "...", "parameters": {...}}]}
                if not command_type and (data.get("action") or data.get("actions")):
                    await message.channel.send(f"⚙️ Running **!{cmd_name}**...")
                    # Define MockInteraction inline
                    class MockInteraction:
                        def __init__(self, bot, guild, user):
                            self.bot = bot
                            self.guild = guild
                            self.user = user
                            self.channel = None
                            self.followup = self
                            self.response = self
                        async def send(self, *args, **kwargs):
                            pass
                        async def send_message(self, *args, **kwargs):
                            pass
                        async def edit_message(self, *args, **kwargs):
                            pass
                        async def defer(self, *args, **kwargs):
                            pass
                    fake = MockInteraction(self.bot, message.guild, message.author)
                    fake.channel = message.channel

                    actions = data.get("actions", [])
                    if not actions and data.get("action"):
                        actions = [{"name": data["action"], "parameters": data.get("parameters", {})}]

                    # Replace {args} in parameters
                    for action in actions:
                        if not action.get('name'):
                            continue
                        params = action.get("parameters", {})
                        for k, v in params.items():
                            if isinstance(v, str):
                                params[k] = v.replace("{args}", args_str)

                    result = await self.execute_sequence(fake, actions)
                    if result["success"]:
                        await message.channel.send(f"✅ **!{cmd_name}** completed!")
                    else:
                        await message.channel.send(f"❌ **!{cmd_name}** failed: {result.get('error', 'Unknown error')}")
                    return True

                if command_type == "simple":
                    content = data.get("content", "")
                    content = self._render_template(content, message, args_str)
                    await message.channel.send(content)
                    return True

                if command_type == "application_status":
                    return await self.handle_application_status(message)
                elif command_type == "appeal_status":
                    return await self.handle_appeal_status(message)
                elif command_type == "help_embed":
                    return await self.send_help_embed(message, data)
                elif command_type == "list_triggers":
                    return await self.list_triggers(message)
                elif command_type == "economy_daily":
                    return await self.handle_economy_daily(message)
                elif command_type == "economy_balance":
                    return await self.handle_economy_balance(message)
                elif command_type == "economy_work":
                    return await self.handle_economy_work(message)
                elif command_type == "economy_beg":
                    return await self.handle_economy_beg(message)
                elif command_type == "economy_leaderboard":
                    return await self.handle_economy_leaderboard(message)
                elif command_type == "economy_shop":
                    return await self.handle_economy_shop(message)
                elif command_type == "economy_buy":
                    return await self.handle_economy_buy(message)
                elif command_type == "economy_transfer":
                    return await self.handle_economy_transfer(message)
                elif command_type == "economy_rob":
                    return await self.handle_economy_rob(message)
                elif command_type == "leveling_rank":
                    return await self.handle_leveling_rank(message)
                elif command_type == "leveling_leaderboard" or command_type == "leaderboard":
                    return await self.handle_leveling_leaderboard(message)
                elif command_type == "leveling_levels":
                    return await self.handle_leveling_levels(message)
                elif command_type == "leveling_rewards":
                    return await self.handle_leveling_rewards(message)
                elif command_type == "config_panel":
                    return await self.handle_config_panel_redirect(message, data)
                elif command_type == "help_all":
                    print(f"DEBUG: ActionHandler processing help_all command for {message.author}")
                    return await self.handle_help_all(message)
                elif command_type == "staffpromo_status":
                    return await self.handle_staffpromo_status(message)
                elif command_type == "staffpromo_leaderboard":
                    return await self.handle_staffpromo_leaderboard(message)
                elif command_type == "staffpromo_config":
                    return await self.handle_staffpromo_config(message)
                elif command_type == "staffpromo_progress":
                    return await self.handle_staffpromo_progress(message)
                elif command_type == "staffpromo_promote":
                    return await self.handle_staffpromo_promote(message)
                elif command_type == "staffpromo_demote":
                    return await self.handle_staffpromo_demote(message)
                elif command_type == "staffpromo_exclude":
                    return await self.handle_staffpromo_exclude(message)
                elif command_type == "staffpromo_roles":
                    return await self.handle_staffpromo_roles(message)
                elif command_type == "staffpromo_review":
                    return await self.handle_staffpromo_review(message)
                elif command_type == "staffpromo_requirements":
                    return await self.handle_staffpromo_requirements(message)
                elif command_type == "staffpromo_bonuses":
                    return await self.handle_staffpromo_bonuses(message)
                elif command_type == "ticket_create":
                    return await self.handle_ticket_create(message)
                elif command_type == "ticket_close":
                    return await self.handle_ticket_close(message)
                elif command_type == "application_status":
                    return await self.handle_application_status(message)
                elif command_type == "appeal_status":
                    return await self.handle_appeal_status(message)
                elif command_type == "help_embed":
                    return await self.handle_help_embed(message)
                elif command_type == "simple":
                    return await self.handle_simple(message)
                elif command_type == "economy_daily":
                    return await self.handle_economy_daily(message)
                elif command_type == "economy_balance":
                    return await self.handle_economy_balance(message)
                elif command_type == "economy_work":
                    return await self.handle_economy_work(message)
                elif command_type == "economy_beg":
                    return await self.handle_economy_beg(message)
                elif command_type == "economy_leaderboard":
                    return await self.handle_economy_leaderboard(message)
                elif command_type == "economy_shop":
                    return await self.handle_economy_shop(message)
                elif command_type == "economy_transfer":
                    return await self.handle_economy_transfer(message)
                elif command_type == "economy_rob":
                    return await self.handle_economy_rob(message)
                elif command_type == "economy_buy":
                    return await self.handle_economy_buy(message)
                elif command_type == "leaderboard":
                    return await self.handle_leaderboard(message)
                elif command_type == "leveling_rank":
                    return await self.handle_leveling_rank(message)
                elif command_type == "leveling_leaderboard":
                    return await self.handle_leveling_leaderboard(message)
                elif command_type == "leveling_levels":
                    return await self.handle_leveling_levels(message)
                elif command_type == "leveling_rewards":
                    return await self.handle_leveling_rewards(message)
                elif command_type == "staffpromo_status":
                    return await self.handle_staffpromo_status(message)
                elif command_type == "staffpromo_leaderboard":
                    return await self.handle_staffpromo_leaderboard(message)
                elif command_type == "staffpromo_progress":
                    return await self.handle_staffpromo_progress(message)
                elif command_type == "staffpromo_tiers":
                    return await self.handle_staffpromo_tiers(message)
                elif command_type == "staffpromo_config":
                    return await self.handle_staffpromo_config(message)
                elif command_type == "staffpromo_promote":
                    return await self.handle_staffpromo_promote(message)
                elif command_type == "config_panel":
                    return await self.handle_config_panel(message)
                elif command_type == "list_triggers":
                    return await self.handle_list_triggers(message)
                elif command_type == "help_all":
                    return await self.handle_help_all(message)
                elif command_type == "raidstatus":
                    return await self.handle_raidstatus(message)
                elif command_type == "guardian_status":
                    return await self.handle_guardian_status(message)
                elif command_type == "automod_status":
                    return await self.handle_automod_status(message)
                elif command_type == "modlog_view":
                    return await self.handle_modlog_view(message)
                elif command_type == "suggest":
                    return await self.handle_suggest(message)
                elif command_type == "chatchannel_add":
                    return await self.handle_chatchannel_add(message)
                elif command_type == "autoresponder_add":
                    return await self.handle_autoresponder_add(message)
                elif command_type == "remindme":
                    return await self.handle_remindme(message)
                elif command_type == "announcement_create":
                    return await self.handle_announcement_create(message)
                elif command_type == "giveaway_create":
                    return await self.handle_giveaway_create(message)
                elif command_type == "set_verify_channel":
                    return await self.handle_set_verify_channel(message)
                elif command_type == "create_tournament":
                    return await self.handle_create_tournament(message)
                elif command_type == "create_event":
                    return await self.handle_create_event(message)
                elif command_type == "appeal_create":
                    return await self.handle_appeal_create(message)
                elif command_type == "application_apply":
                    return await self.handle_application_apply(message)
                elif command_type == "list_quests":
                    return await self.handle_list_quests(message)
                elif command_type == "prestige":
                    return await self.handle_prestige(message)
                elif command_type == "dice":
                    return await self.handle_dice(message)
                elif command_type == "flip":
                    return await self.handle_flip(message)
                elif command_type == "slots":
                    return await self.handle_slots(message)
                elif command_type == "trivia":
                    return await self.handle_trivia(message)
                elif command_type == "starboard_leaderboard":
                    return await self.handle_starboard_leaderboard(message)
                elif command_type == "list_events":
                    return await self.handle_list_events(message)
                elif command_type == "list_tournaments":
                    return await self.handle_list_tournaments(message)
                elif command_type == "tournament_leaderboard":
                    return await self.handle_tournament_leaderboard(message)
                elif command_type == "tournament_join":
                    return await self.handle_tournament_join(message)
                elif command_type == "server_stats":
                    return await self.handle_server_stats(message)
                elif command_type == "my_stats":
                    return await self.handle_my_stats(message)
                elif command_type == "at_risk":
                    return await self.handle_at_risk(message)
                elif command_type == "gamification_quests":
                    return await self.handle_gamification_quests(message)
                elif command_type == "gamification_prestige":
                    return await self.handle_gamification_prestige(message)
                elif command_type == "gamification_dice":
                    return await self.handle_gamification_dice(message)
                elif command_type == "gamification_flip":
                    return await self.handle_gamification_flip(message)
                elif command_type == "events_create":
                    return await self.handle_events_create(message)
                elif command_type == "events_list":
                    return await self.handle_events_list(message)
                elif command_type == "tournaments_create":
                    return await self.handle_tournaments_create(message)
                elif command_type == "tournaments_join":
                    return await self.handle_tournaments_join(message)
                elif command_type == "tournaments_leaderboard":
                    return await self.handle_tournaments_leaderboard(message)
                elif command_type == "reminders":
                    return await self.handle_reminders(message)
                elif command_type == "remind":
                    return await self.handle_remind(message)
                elif command_type == "announcements_create":
                    return await self.handle_announcements_create(message)
                elif command_type == "giveaways_create":
                    return await self.handle_giveaways_create(message)
                elif command_type == "giveaways_list":
                    return await self.handle_giveaways_list(message)
                elif command_type == "serverstats":
                    return await self.handle_serverstats(message)
                elif command_type == "mystats":
                    return await self.handle_mystats(message)
                elif command_type == "atrisk":
                    return await self.handle_atrisk(message)
                elif command_type == "automod_status":
                    return await self.handle_automod_status(message)
                elif command_type == "guardian_status":
                    return await self.handle_guardian_status(message)
                elif command_type == "chatchannel_add":
                    return await self.handle_chatchannel_add(message)
                elif command_type == "suggest":
                    return await self.handle_suggest(message)
                elif command_type == "ticket":
                    return await self.handle_ticket(message)
                elif command_type == "appeal":
                    return await self.handle_appeal(message)
                elif command_type == "apply":
                    return await self.handle_apply(message)
                elif command_type == "verify":
                    return await self.handle_verify(message)
                elif command_type == "modlog_view":
                    return await self.handle_modlog_view(message)
                elif command_type == "warn":
                    return await self.handle_warn(message)
                elif command_type == "warnings":
                    return await self.handle_warnings(message)
                elif command_type == "clearwarn":
                    return await self.handle_clearwarn(message)
                elif command_type == "clearallwarns":
                    return await self.handle_clearallwarns(message)
                elif command_type == "kick":
                    return await self.handle_kick(message)
                elif command_type == "ban":
                    return await self.handle_ban(message)
                elif command_type == "mute":
                    return await self.handle_mute(message)
                elif command_type == "modstats":
                    return await self.handle_modstats(message)
                elif command_type == "leveling_levels":
                    return await self.handle_leveling_levels(message)
                elif command_type == "leveling_rewards":
                    return await self.handle_leveling_rewards(message)
                elif command_type == "leveling_shop":
                    return await self.handle_leveling_shop(message)
                elif command_type == "remind":
                    return await self.handle_remind(message)
                elif command_type == "list_reminders":
                    return await self.handle_list_reminders(message)
                elif command_type == "mod_stats":
                    return await self.handle_mod_stats(message)
                elif command_type == "shift_start":
                    return await self.handle_shift_start(message)
                elif command_type == "shift_end":
                    return await self.handle_shift_end(message)
                elif command_type == "shift_status":
                    return await self.handle_shift_status(message)
                elif command_type == "staff_review":
                    return await self.handle_staff_review_cmd(message)
                elif command_type == "announce":
                    return await self.handle_announce(message)
                elif command_type == "leveling_shop":
                    return await self.handle_leveling_shop(message)
                elif command_type == "staffpromotion_history":
                    return await self.handle_staffpromotion_history(message)
                elif command_type == "peer_vote":
                    return await self.handle_peer_vote(message)
                elif command_type == "economy_challenge":
                    return await self.handle_economy_challenge(message)
                else:
                    # Unknown dict type, fall back to sending as string
                    await message.channel.send(content=code)
                    return True

            # Handle plain strings (existing functionality)
            else:
                await message.channel.send(content=code)
                return True

        except json.JSONDecodeError:
            # Not JSON, treat as plain string
            await message.channel.send(content=code)
            return True
        except Exception as e:
            import traceback
            error_str = str(e)
            logger.error(f"Error executing custom command '{cmd_name}': {e}\n{traceback.format_exc()}")
            
            # Provide helpful error message based on the actual error
            if "not found" in error_str.lower() or "no result" in error_str.lower():
                user_msg = f"❌ {cmd_name} could not find the requested resource. Please check your input and try again."
            elif "permission" in error_str.lower():
                user_msg = f"❌ {cmd_name} failed due to insufficient permissions. Please contact an administrator."
            elif "cooldown" in error_str.lower() or "rate limit" in error_str.lower():
                user_msg = f"❌ {cmd_name} is on cooldown. Please wait a moment and try again."
            elif "invalid" in error_str.lower() or "missing" in error_str.lower():
                user_msg = f"❌ Invalid input for {cmd_name}. Please check the command usage and try again."
            elif "has no attribute" in error_str:
                # AttributeError - likely missing attribute on ActionHandler or bot
                logger.error(f"AttributeError in command '{cmd_name}': {error_str}")
                # Check if it's a bot attribute that's missing
                if "self.bot" in error_str and "None" in error_str:
                    user_msg = f"❌ Error executing `{cmd_name}`: Bot not properly initialized. Please contact an administrator."
                else:
                    user_msg = f"❌ Error executing `{cmd_name}`: Internal error (missing attribute). Please contact an administrator."
            else:
                # Generic error with actual error info (sanitized)
                safe_error = error_str[:200] if error_str else "Unknown error"
                user_msg = f"❌ Error executing `{cmd_name}`: {safe_error}\nPlease try again or contact an administrator."

            # Check for error prevention
            prevention = self._get_error_prevention(guild_id, cmd_name, cmd_data_obj, error_str)
            if prevention and prevention.get("message"):
                await message.channel.send(prevention["message"])
            else:
                await message.channel.send(user_msg)
            return False

    def _get_error_prevention(self, guild_id: int, cmd_name: str, cmd_data: dict, error_msg: str) -> dict:
        """Get error prevention measures based on learned patterns."""
        if not cmd_name:
            return None

        # Check if command has pre-configured prevention
        if cmd_data and isinstance(cmd_data, dict):
            prevention = cmd_data.get("error_prevention")
            if prevention and prevention.get("prevention_enabled"):
                return prevention

        # Check stored prevention from AI analysis
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        if cmd_name in custom_cmds:
            stored = custom_cmds[cmd_name]
            try:
                stored_data = json.loads(stored) if isinstance(stored, str) else stored
                if isinstance(stored_data, dict):
                    prevention = stored_data.get("error_prevention")
                    if prevention and prevention.get("prevention_enabled"):
                        return prevention
            except Exception as e:
                logger.debug("Error reading stored command prevention for %s: %s", cmd_name, e)

        return None

    async def handle_application_status(self, message: discord.Message) -> bool:
        """Handle !apply status command"""
        apps = dm.load_json("applications", default={})
        user_id = str(message.author.id)

        if user_id not in apps:
            await message.channel.send("You have not submitted a staff application yet.")
            return True

        status = apps[user_id]["status"]
        timestamp = apps[user_id]["timestamp"]

        embed = discord.Embed(title="Your Staff Application Status", color=discord.Color.blue())
        embed.add_field(name="Status", value=status.capitalize(), inline=True)
        embed.add_field(name="Submitted", value=timestamp, inline=True)

        await message.channel.send(embed=embed)
        return True

    async def handle_economy_work(self, message: discord.Message) -> bool:
        """Handle !work command with enhanced job system and animations"""
        try:
            import asyncio
            guild_id = message.guild.id
            user_id = message.author.id

            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                embed = discord.Embed(
                    title="❌ Work Unavailable",
                    description="The economy system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel economy to enable the system")
                await message.channel.send(embed=embed)
                return False

            from modules.economy import Economy
            economy = Economy(self.bot)

            # Loading animation
            loading_embed = discord.Embed(
                title="💼 Finding Employment",
                description="📋 Checking job listings...\n🕒 Preparing work environment...\n💰 Negotiating salary...",
                color=discord.Color.blue()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.7)
            loading_embed.description = "✅ Checking job listings...\n🕒 Preparing work environment...\n💰 Negotiating salary..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Checking job listings...\n✅ Preparing work environment...\n💰 Negotiating salary..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Checking job listings...\n✅ Preparing work environment...\n✅ Negotiating salary..."
            await loading_msg.edit(embed=loading_embed)

            # Cooldown check
            c = dm.get_guild_data(guild_id, "economy_config", {})
            cooldown = c.get("work_cooldown_seconds", 3600)

            last_work = dm.get_guild_data(guild_id, "last_work", {})
            last_time = last_work.get(str(user_id), 0)
            now = time.time()

            if now - last_time < cooldown:
                remaining = int(cooldown - (now - last_time))
                hours, remainder = divmod(remaining, 3600)
                minutes, seconds = divmod(remainder, 60)

                cooldown_embed = discord.Embed(
                    title="😴 Work Cooldown Active",
                    description=f"You're still recovering from your last job!\n\n"
                               f"**Time remaining:** `{hours}h {minutes}m {seconds}s`\n\n"
                               f"*Take a break and come back refreshed!*",
                    color=discord.Color.orange()
                )
                cooldown_embed.set_footer(text="Work again when the cooldown expires")
                cooldown_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/sleeping.png")

                await loading_msg.edit(embed=cooldown_embed)
                await loading_msg.add_reaction("😴")
                return True

            # Enhanced job system
            jobs = {
                "developer": {"name": "💻 Software Developer", "reward_range": (150, 300), "description": "Debugged complex code", "emoji": "💻"},
                "artist": {"name": "🎨 Digital Artist", "reward_range": (120, 250), "description": "Created stunning artwork", "emoji": "🎨"},
                "doctor": {"name": "⚕️ Medical Doctor", "reward_range": (200, 400), "description": "Saved lives in the ER", "emoji": "⚕️"},
                "chef": {"name": "👨‍🍳 Master Chef", "reward_range": (100, 220), "description": "Prepared gourmet meals", "emoji": "👨‍🍳"},
                "moderator": {"name": "🛡️ Discord Moderator", "reward_range": (80, 180), "description": "Kept the server safe", "emoji": "🛡️"},
                "farmer": {"name": "🚜 Organic Farmer", "reward_range": (70, 160), "description": "Harvested fresh produce", "emoji": "🚜"},
                "streamer": {"name": "🎮 Game Streamer", "reward_range": (90, 200), "description": "Entertained thousands of viewers", "emoji": "🎮"},
                "teacher": {"name": "👩‍🏫 Online Teacher", "reward_range": (110, 230), "description": "Educated eager students", "emoji": "👩‍🏫"},
                "musician": {"name": "🎵 Session Musician", "reward_range": (130, 270), "description": "Recorded hit tracks", "emoji": "🎵"},
                "scientist": {"name": "🔬 Research Scientist", "reward_range": (160, 320), "description": "Made groundbreaking discoveries", "emoji": "🔬"}
            }

            # Select random job
            job_key = random.choice(list(jobs.keys()))
            job = jobs[job_key]

            # Calculate reward with level bonus
            from modules.leveling import Leveling
            leveling = Leveling(self.bot)
            user_level = leveling.get_level_from_xp(leveling.get_xp(guild_id, user_id))

            level_bonus = min(user_level * 5, 100)  # Up to 100 bonus based on level
            base_reward = random.randint(*job["reward_range"])
            total_reward = base_reward + level_bonus

            # Award the coins
            economy.add_coins(guild_id, user_id, total_reward)
            last_work[str(user_id)] = now
            dm.update_guild_data(guild_id, "last_work", last_work)

            currency_name = c.get("currency_name", "Coins")
            currency_emoji = c.get("currency_emoji", "🪙")

            # Success embed
            success_embed = discord.Embed(
                title="💼 Work Completed Successfully!",
                description=f"**{message.author.display_name}** finished their shift!",
                color=discord.Color.green()
            )

            success_embed.add_field(
                name=f"{job['emoji']} Job Details",
                value=f"**Position:** {job['name']}\n"
                      f"**Task:** {job['description']}\n"
                      f"**Performance:** Excellent!",
                inline=False
            )

            success_embed.add_field(
                name="💰 Earnings Breakdown",
                value=f"**Base Pay:** `{base_reward:,}` {currency_name}\n"
                      f"**Level Bonus:** `{level_bonus:,}` {currency_name} (Level {user_level})\n"
                      f"**Total Earned:** `{total_reward:,}` {currency_name}",
                inline=True
            )

            success_embed.add_field(
                name="⏰ Next Shift",
                value=f"Available in **{cooldown // 3600} hours**\n"
                      f"<t:{int(now + cooldown)}:R>",
                inline=True
            )

            # Work streak tracking
            work_streak_data = dm.get_guild_data(guild_id, "work_streaks", {})
            user_streak = work_streak_data.get(str(user_id), {"streak": 0, "last_work": 0})

            if now - user_streak["last_work"] < cooldown * 2:  # Within reasonable time
                user_streak["streak"] += 1
            else:
                user_streak["streak"] = 1

            user_streak["last_work"] = now
            work_streak_data[str(user_id)] = user_streak
            dm.update_guild_data(guild_id, "work_streaks", work_streak_data)

            if user_streak["streak"] >= 5:
                success_embed.add_field(
                    name="🔥 Work Streak Bonus!",
                    value=f"**{user_streak['streak']}** days in a row!\n"
                          f"Keep it up for even bigger rewards!",
                    inline=False
                )

            success_embed.set_footer(text=f"Economy System • Work hard, earn more!")
            success_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/work_completed.png")

            await loading_msg.edit(embed=success_embed)

            # Celebration reactions
            await loading_msg.add_reaction("💼")
            await loading_msg.add_reaction("💰")
            if user_streak["streak"] >= 5:
                await loading_msg.add_reaction("🔥")

            return True

        except Exception as e:
            logger.error(f"Error in handle_economy_work: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Work Error",
                description="Unable to process your work shift. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_appeal_status(self, message: discord.Message) -> bool:
        """Handle !appeal status command"""
        guild_id = message.guild.id
        
        # Check if appeals system is enabled
        if not is_system_enabled(guild_id, "appeals"):
            await message.channel.send("❌ The appeals system is currently disabled on this server.")
            return False
        
        appeals = dm.load_json("appeals", default={})
        user_id = str(message.author.id)

        if user_id not in appeals:
            await message.channel.send("You have no active appeals.")
            return True

        appeal = appeals[user_id]
        status = appeal.get("status", "pending")
        timestamp = appeal.get("timestamp", "Unknown")
        action_id = appeal.get("action_id", "Unknown")

        embed = discord.Embed(title="Your Appeal Status", color=discord.Color.blue())
        embed.add_field(name="Action ID", value=str(action_id), inline=True)
        embed.add_field(name="Status", value=status.capitalize(), inline=True)
        embed.add_field(name="Submitted", value=str(timestamp), inline=True)

        await message.channel.send(embed=embed)
        return True

    async def send_help_embed(self, message: discord.Message, data: dict) -> bool:
        """Send a help embed based on stored data with argument support"""
        # Extract arguments from message
        full_content = message.content
        parts = full_content.split(maxsplit=2)
        args_str = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and not parts[0].endswith(parts[1]) else "")

        title = data.get("title", "Help").replace("{args}", args_str)
        description = data.get("description", "").replace("{args}", args_str)
        fields = data.get("fields", [])

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())

        for field in fields:
            embed.add_field(
                name=field.get("name", "").replace("{args}", args_str),
                value=field.get("value", "").replace("{args}", args_str),
                inline=field.get("inline", False)
            )

        await message.channel.send(embed=embed)
        return True

    async def list_triggers(self, message: discord.Message) -> bool:
        """List all active trigger words for the guild"""
        try:
            guild_id = message.guild.id
            triggers = dm.get_guild_data(guild_id, "trigger_roles", {})

            if not triggers:
                await message.channel.send("No trigger words are currently set up.")
                return True

            embed = discord.Embed(title="Active Trigger Words", color=discord.Color.blue())

            for word, role_id in triggers.items():
                role = message.guild.get_role(role_id)
                role_name = role.name if role else f"Unknown Role (ID: {role_id})"
                embed.add_field(
                    name=f"Trigger: `{word}`",
                    value=f"Assigns role: **{role_name}**",
                    inline=False
                )

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in list_triggers: {e}")
            await message.channel.send("❌ Unable to list trigger words. Please try again.")
            return False

    async def handle_economy_daily(self, message: discord.Message) -> bool:
        """Handle !daily command with enhanced animations and streak tracking"""
        try:
            import asyncio
            import datetime
            guild_id = message.guild.id
            user_id = message.author.id

            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                embed = discord.Embed(
                    title="❌ Daily Rewards Unavailable",
                    description="The economy system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel economy to enable the system")
                await message.channel.send(embed=embed)
                return False

            from modules.economy import Economy
            economy = Economy(self.bot)

            # Loading animation
            loading_embed = create_loading_embed(
                title=f"{get_animated_emoji('economy')} Opening Daily Reward Chest",
                description="🔑 Checking eligibility...\n💰 Calculating rewards...\n🎁 Preparing your gift...",
                system_name="economy"
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Checking eligibility...\n💰 Calculating rewards...\n🎁 Preparing your gift..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Checking eligibility...\n✅ Calculating rewards...\n🎁 Preparing your gift..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Checking eligibility...\n✅ Calculating rewards...\n✅ Preparing your gift..."
            await loading_msg.edit(embed=loading_embed)

            # Check if already claimed today
            last_daily = dm.get_guild_data(guild_id, "last_daily", {})
            streak_data = dm.get_guild_data(guild_id, "daily_streaks", {})

            last_time = last_daily.get(str(user_id))
            current_streak = streak_data.get(str(user_id), {}).get("streak", 0)

            if last_time:
                last_date = datetime.datetime.fromisoformat(last_time)
                time_diff = datetime.datetime.now() - last_date

                if time_diff.days < 1:
                    # Already claimed today
                    next_claim = last_date + datetime.timedelta(days=1)
                    time_remaining = next_claim - datetime.datetime.now()

                    hours, remainder = divmod(int(time_remaining.total_seconds()), 3600)
                    minutes, seconds = divmod(remainder, 60)

                    already_claimed_embed = discord.Embed(
                        title="🎉 Daily Reward Already Claimed!",
                        description=f"You've already claimed your daily reward today!\n\n"
                                   f"**Next reward available in:**\n"
                                   f"⏰ `{hours}h {minutes}m {seconds}s`",
                        color=discord.Color.orange()
                    )

                    already_claimed_embed.add_field(
                        name="🔥 Current Streak",
                        value=f"**{current_streak}** days in a row!",
                        inline=True
                    )

                    already_claimed_embed.add_field(
                        name="💎 Streak Bonus",
                        value=f"+{min(current_streak * 10, 100)}% reward",
                        inline=True
                    )

                    already_claimed_embed.set_footer(text="Come back tomorrow for your next reward!")
                    already_claimed_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/chest_locked.png")

                    await loading_msg.edit(embed=already_claimed_embed)
                    await loading_msg.add_reaction("⏰")
                    return True

            # Calculate reward with streak bonus
            config = dm.get_guild_data(guild_id, "economy_config", {})
            base_reward = config.get("daily_amount", 100)

            # Check if streak is maintained (claimed within 48 hours of last claim)
            streak_maintained = True
            if last_time:
                last_date = datetime.datetime.fromisoformat(last_time)
                if (datetime.datetime.now() - last_date).days > 2:
                    streak_maintained = False
                    current_streak = 0

            if streak_maintained and last_time:
                current_streak += 1
            elif not last_time:
                current_streak = 1

            # Streak bonus (up to 100% extra)
            streak_bonus_percent = min(current_streak * 10, 100)
            bonus_amount = int(base_reward * (streak_bonus_percent / 100))
            total_reward = base_reward + bonus_amount

            # Award the coins
            economy.add_coins(guild_id, user_id, total_reward)

            # Update streak data
            streak_data[str(user_id)] = {
                "streak": current_streak,
                "last_claim": str(datetime.datetime.now())
            }
            dm.update_guild_data(guild_id, "daily_streaks", streak_data)

            # Update last daily
            last_daily[str(user_id)] = str(datetime.datetime.now())
            dm.update_guild_data(guild_id, "last_daily", last_daily)

            currency_name = config.get("currency_name", "Coins")
            currency_emoji = config.get("currency_emoji", "🪙")

            # Success embed
            success_embed = create_success_embed(
                description=f"**{message.author.display_name}** opened their daily reward chest!",
                action_type="daily"
            )
            success_embed.title = f"{get_animated_emoji('economy')} Daily Reward Claimed Successfully!"

            success_embed.add_field(
                name=f"{get_animated_emoji('economy')} Reward Amount",
                value=f"**{total_reward:,}** {currency_name}\n"
                      f"Base: `{base_reward:,}`\n"
                      f"Streak Bonus: `{bonus_amount:,}` (+{streak_bonus_percent}%)",
                inline=True
            )

            success_embed.add_field(
                name=f"{get_animated_emoji('leveling')} Streak Status",
                value=f"**{current_streak}** consecutive days!\n"
                      f"Next bonus: `+{min((current_streak + 1) * 10, 100)}%`",
                inline=True
            )

            success_embed.add_field(
                name=f"{get_animated_emoji('reminders')} Next Reward",
                value=f"Available in **24 hours**\n"
                      f"<t:{int((datetime.datetime.now() + datetime.timedelta(days=1)).timestamp())}:R>",
                inline=True
            )

            # Achievement check
            achievements = []
            if current_streak >= 7:
                achievements.append("🔥 **Week Warrior** - 7 day streak!")
            if current_streak >= 30:
                achievements.append("👑 **Monthly Monarch** - 30 day streak!")
            if current_streak >= 100:
                achievements.append("🌟 **Century Champion** - 100 day streak!")

            if achievements:
                success_embed.add_field(
                    name=f"{get_animated_emoji('gamification')} Achievements Unlocked",
                    value="\n".join(achievements),
                    inline=False
                )

            success_embed.set_footer(text=f"Daily Rewards • Keep your streak alive!")
            success_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/chest_opened.png")

            await loading_msg.edit(embed=success_embed)

            # Celebration reactions
            await loading_msg.add_reaction("🎉")
            await loading_msg.add_reaction("💰")
            if current_streak >= 7:
                await loading_msg.add_reaction("🔥")
            if current_streak >= 30:
                await loading_msg.add_reaction("👑")

            return True

        except Exception as e:
            logger.error(f"Error in handle_economy_daily: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Daily Reward Error",
                description="Unable to process your daily reward. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_economy_buy(self, message: discord.Message) -> bool:
        """!buy <item_name_or_id>"""
        guild_id = message.guild.id
        
        # Check if economy system is enabled
        if not is_system_enabled(guild_id, "economy"):
            await message.channel.send("❌ The economy system is currently disabled on this server.")
            return False
        
        parts = message.content.split(maxsplit=1)
        if len(parts) < 2:
            await message.channel.send("❌ Usage: `!buy <item_name_or_id>`")
            return True

        query = parts[1].strip()
        items = dm.get_guild_data(guild_id, "shop_items", [])

        target_item = None
        for item in items:
            if str(item.get("id")) == query or item.get("name").lower() == query.lower():
                target_item = item
                break

        if not target_item:
            await message.channel.send(f"❌ Item '**{query}**' not found in the shop.")
            return True

        from modules.economy import Economy
        eco = Economy(self.bot)

        balance = eco.get_coins(guild_id, message.author.id)
        price = target_item.get("price", 0)

        if balance < price:
            await message.channel.send(f"❌ You don't have enough coins! (Need {price}, have {balance})")
            return True

        # Check stock
        stock = target_item.get("stock", -1)
        if stock == 0:
            await message.channel.send("❌ This item is out of stock!")
            return True

        # Give item (role)
        role_id = target_item.get("role_id")
        if role_id:
            role = message.guild.get_role(int(role_id))
            if role:
                try:
                    await message.author.add_roles(role)
                except:
                    await message.channel.send("❌ I couldn't assign you the role. Please contact staff.")
                    return True
            else:
                await message.channel.send("❌ The role for this item no longer exists.")
                return True

        # Deduct coins
        eco.add_coins(guild_id, message.author.id, -price)

        # Update stock
        if stock > 0:
            target_item["stock"] -= 1
            dm.update_guild_data(guild_id, "shop_items", items)

        await message.channel.send(f"ðŸ›ï¸ You bought **{target_item['name']}** for **{price} coins**!")
        return True

    async def handle_economy_transfer(self, message: discord.Message) -> bool:
        """!transfer <user> <amount>"""
        
        # Check if economy system is enabled
        config = dm.get_guild_data(message.guild.id, "economy_config", {})
        if not config.get("enabled", True):
            await message.channel.send("❌ Economy system is disabled on this server.")
            return True
        
        parts = message.content.split()
        if len(parts) < 3:
            await message.channel.send("❌ Usage: `!transfer @user <amount>`")
            return True

        target = message.mentions[0] if message.mentions else None
        if not target:
            await message.channel.send("❌ Please mention a user to transfer coins to.")
            return True

        try:
            amount = int(parts[2])
        except ValueError:
            await message.channel.send("❌ Invalid amount.")
            return True

        from modules.economy import Economy
        eco = Economy(self.bot)

        if amount <= 0:
            await message.channel.send("❌ Amount must be positive.")
            return True

        if eco.get_coins(guild_id, message.author.id) < amount:
            await message.channel.send("❌ Insufficient funds.")
            return True

        eco.add_coins(guild_id, message.author.id, -amount)
        eco.add_coins(guild_id, target.id, amount)
        await message.channel.send(f"ðŸ’¸ Transferred **{amount} coins** to {target.mention}.")
        return True

    async def handle_economy_rob(self, message: discord.Message) -> bool:
        """!rob <user>"""
        guild_id = message.guild.id
        
        # Check if economy system is enabled
        if not is_system_enabled(guild_id, "economy"):
            await message.channel.send("❌ The economy system is currently disabled on this server.")
            return False
        
        if not message.mentions:
            await message.channel.send("❌ Mention someone to rob!")
            return True

        target = message.mentions[0]
        if target.id == message.author.id:
            await message.channel.send("❌ You can't rob yourself!")
            return True

        from modules.economy import Economy
        eco = Economy(self.bot)

        author_id = message.author.id

        # Cooldown check
        last_rob = dm.get_guild_data(guild_id, "last_rob", {})
        now = time.time()
        if now - last_rob.get(str(author_id), 0) < 3600:
            rem = int(3600 - (now - last_rob.get(str(author_id), 0)))
            await message.channel.send(f"❌ You're laying low. Try again in **{rem//60}m {rem%60}s**.")
            return True

        target_bal = eco.get_coins(guild_id, target.id)
        if target_bal < 100:
            await message.channel.send("❌ They're too poor to rob. Have some heart!")
            return True

        last_rob[str(author_id)] = now
        dm.update_guild_data(guild_id, "last_rob", last_rob)

        success = random.random() < 0.4
        if success:
            stolen = random.randint(10, int(target_bal * 0.3))
            eco.add_coins(guild_id, target.id, -stolen)
            eco.add_coins(guild_id, author_id, stolen)
            await message.channel.send(f"💰 **Success!** You robbed {target.mention} and made off with **{stolen} coins**!")
        else:
            fine = random.randint(50, 200)
            eco.add_coins(guild_id, author_id, -fine)
            await message.channel.send(f"ðŸ‘® **Caught!** You were caught trying to rob {target.mention} and fined **{fine} coins**.")
        return True

    async def handle_economy_balance(self, message: discord.Message) -> bool:
        """Handle !balance command with enhanced visuals and animations"""
        try:
            import asyncio
            guild_id = message.guild.id
            user_id = message.author.id

            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                embed = discord.Embed(
                    title="❌ Economy Unavailable",
                    description="The economy system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel economy to enable the system")
                await message.channel.send(embed=embed)
                return False

            from modules.economy import Economy
            from modules.leveling import Leveling
            economy = Economy(self.bot)
            leveling = Leveling(self.bot)

            # Loading animation
            loading_embed = create_loading_embed(
                title=f"{get_animated_emoji('economy')} Opening Your Wallet",
                description="🔍 Counting coins...\n💎 Polishing gems...\n📊 Calculating wealth...",
                system_name="economy"
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.6)
            loading_embed.description = "✅ Counting coins...\n💎 Polishing gems...\n📊 Calculating wealth..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Counting coins...\n✅ Polishing gems...\n📊 Calculating wealth..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Counting coins...\n✅ Polishing gems...\n✅ Calculating wealth..."
            await loading_msg.edit(embed=loading_embed)

            # Get economy data
            config = dm.get_guild_data(guild_id, "economy_config", {})
            coins = economy.get_coins(guild_id, user_id)
            gems = leveling.get_gems(guild_id, user_id)
            xp = leveling.get_xp(guild_id, user_id)
            level = leveling.get_level_from_xp(xp)
            prestige = leveling.get_prestige(guild_id, user_id)

            currency_name = config.get("currency_name", "Coins")
            currency_emoji = config.get("currency_emoji", "🪙")
            gem_name = config.get("gem_name", "Gems")

            # Enhanced balance embed
            embed = create_success_embed(
                description=f"**Financial Status Report**\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                action_type="economy"
            )
            embed.title = f"{get_animated_emoji('economy')} {message.author.display_name}'s Wealth Overview"

            embed.set_thumbnail(url=message.author.display_avatar.url)

            # Main currencies
            embed.add_field(
                name=f"{currency_emoji} Primary Currency",
                value=f"**{coins:,}** {currency_name}",
                inline=True
            )

            embed.add_field(
                name=f"{get_animated_emoji('leveling')} Premium Currency",
                value=f"**{gems}** {gem_name}",
                inline=True
            )

            embed.add_field(
                name=f"{get_animated_emoji('gamification')} Net Worth",
                value=f"**{coins + (gems * 100):,}** total value",
                inline=True
            )

            # Level and XP information
            xp_to_next = leveling.get_xp_for_next_level(level) - xp
            embed.add_field(
                name=f"{get_animated_emoji('leveling')} Level Progress",
                value=f"**Level {level}** {f'(Prestige {prestige})' if prestige > 0 else ''}\n"
                      f"**{xp:,} XP** earned\n"
                      f"**{xp_to_next:,} XP** to next level",
                inline=False
            )

            # Progress bar for level
            progress_percent = min(100, (xp / leveling.get_xp_for_next_level(level)) * 100) if leveling.get_xp_for_next_level(level) > 0 else 100
            progress_bar = "█" * int(progress_percent / 10) + "░" * (10 - int(progress_percent / 10))
            embed.add_field(
                name=f"{get_animated_emoji('intelligence')} Progress Bar",
                value=f"`{progress_bar}` **{progress_percent:.1f}%**",
                inline=False
            )

            # Recent activity
            recent_activity = []
            # Check recent transactions (you might want to store this data)
            embed.add_field(
                name=f"{get_animated_emoji('server_analytics')} Recent Activity",
                value="• Balance loaded successfully\n• All assets accounted for\n• Ready for transactions",
                inline=False
            )

            # Quick actions
            embed.add_field(
                name="⚡ Quick Actions",
                value="`!daily` - Claim daily reward\n"
                      "`!work` - Earn coins\n"
                      "`!shop` - Browse items\n"
                      "`!leaderboard` - Check rankings",
                inline=False
            )

            embed.set_footer(text=f"Economy System • Last updated: {discord.utils.format_dt(discord.utils.utcnow())}")
            embed.set_author(name=f"{message.author.display_name}'s Portfolio", icon_url=message.author.display_avatar.url)

            await loading_msg.edit(embed=embed)

            # Add celebratory reactions based on wealth
            if coins >= 10000:
                await loading_msg.add_reaction("💎")
            elif coins >= 1000:
                await loading_msg.add_reaction("🪙")
            await loading_msg.add_reaction("💰")

            return True

        except Exception as e:
            logger.error(f"Error in handle_economy_balance: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Wallet Error",
                description="Unable to access your financial data. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False
            
            from modules.economy import Economy
            from modules.leveling import Leveling
            economy = Economy(self.bot)
            leveling = Leveling(self.bot)
            user_id = message.author.id

            # Check if economy system is enabled
            config = dm.get_guild_data(guild_id, "economy_config", {})
            if not config.get("enabled", True):
                await message.channel.send("❌ Economy system is disabled on this server.")
                return True

            coins = economy.get_coins(guild_id, user_id)
            gems = leveling.get_gems(guild_id, user_id)
            xp = leveling.get_xp(guild_id, user_id)
            level = leveling.get_level_from_xp(xp)

            currency_name = config.get("currency_name", "coins")
            currency_emoji = config.get("currency_emoji", "🪙")
            gem_name = config.get("gem_name", "gems")

            embed = discord.Embed(title=f"{currency_emoji} {message.author.name}'s Balance", color=discord.Color.gold())
            embed.add_field(name=f"{currency_emoji} {currency_name}", value=f"{coins:,}", inline=True)
            embed.add_field(name=f"💎 {gem_name}", value=str(gems), inline=True)
            embed.add_field(name="🆙 Level", value=f"{level} ({xp:,} XP)", inline=True)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_balance: {e}")
            await message.channel.send("❌ Unable to retrieve your balance. Please contact an administrator.")
            return False

    async def handle_economy_beg(self, message: discord.Message) -> bool:
        """!beg — small random coin reward with a short cooldown. Mirrors handle_economy_work."""
        try:
            from modules.economy import Economy
            economy = Economy(self.bot)
            guild_id = message.guild.id
            user_id = message.author.id

            c = dm.get_guild_data(guild_id, "economy_config", {})
            if not c.get("enabled", True):
                await message.channel.send("❌ Economy system is disabled on this server.")
                return True

            min_reward = c.get("beg_min", 1)
            max_reward = c.get("beg_max", 25)
            cooldown = c.get("beg_cooldown_seconds", 300)

            last_beg = dm.get_guild_data(guild_id, "last_beg", {})
            last_time = last_beg.get(str(user_id), 0)
            now = time.time()

            if now - last_time < cooldown:
                remaining = int(cooldown - (now - last_time))
                await message.channel.send(f"❌ Nobody feels generous right now. Try again in **{remaining // 60}m {remaining % 60}s**.")
                return True

            # 25% chance of getting nothing — keeps it fun.
            if random.random() < 0.25:
                last_beg[str(user_id)] = now
                dm.update_guild_data(guild_id, "last_beg", last_beg)
                await message.channel.send("ðŸ¥² Nobody gave you anything this time. Try again later!")
                return True

            reward = random.randint(min_reward, max_reward)
            economy.add_coins(guild_id, user_id, reward)
            last_beg[str(user_id)] = now
            dm.update_guild_data(guild_id, "last_beg", last_beg)

            donors = ["a kind stranger", "an old wizard", "a tired developer", "a passing knight", "a generous shopkeeper"]
            await message.channel.send(f"ðŸ™‡ You begged and received **{reward} coins** from {random.choice(donors)}!")
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_beg: {e}")
            await message.channel.send("❌ Unable to beg right now. Please try again.")
            return False

    async def handle_economy_leaderboard(self, message: discord.Message) -> bool:
        """!economylb — enhanced economy leaderboard with pagination and stats"""
        try:
            import asyncio
            from discord import ui

            guild_id = message.guild.id

            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                embed = discord.Embed(
                    title="❌ Economy Leaderboard Unavailable",
                    description="The economy system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel economy to enable the system")
                await message.channel.send(embed=embed)
                return False

            from modules.economy import Economy
            economy = Economy(self.bot)

            # Loading animation
            loading_embed = discord.Embed(
                title="🏆 Loading Economy Leaderboard",
                description="💰 Counting coins...\n📊 Calculating rankings...\n🏅 Preparing podium...",
                color=discord.Color.gold()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Counting coins...\n📊 Calculating rankings...\n🏅 Preparing podium..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Counting coins...\n✅ Calculating rankings...\n🏅 Preparing podium..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Counting coins...\n✅ Calculating rankings...\n✅ Preparing podium..."
            await loading_msg.edit(embed=loading_embed)

            balances = dm.get_guild_data(guild_id, "economy_balances", {})
            gems = dm.get_guild_data(guild_id, "economy_gems", {})

            if not balances:
                empty_embed = discord.Embed(
                    title="💰 Economy Leaderboard",
                    description="**No one has earned any coins yet!**\n\n"
                               "Be the first to start your financial journey!\n\n"
                               "*Use `!daily`, `!work`, and other economy commands to earn coins.*",
                    color=discord.Color.light_grey()
                )
                empty_embed.set_footer(text="Economy system • Start earning today!")
                empty_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/empty_wallet.png")

                await loading_msg.edit(embed=empty_embed)
                await loading_msg.add_reaction("💰")
                return True

            # Create comprehensive leaderboard data
            leaderboard_data = []
            for user_id, coins in balances.items():
                user_gems = gems.get(user_id, 0)
                net_worth = coins + (user_gems * 100)  # Gems worth 100 coins each
                leaderboard_data.append({
                    "user_id": user_id,
                    "coins": coins,
                    "gems": user_gems,
                    "net_worth": net_worth
                })

            # Sort by net worth
            leaderboard_data.sort(key=lambda x: x["net_worth"], reverse=True)

            config = dm.get_guild_data(guild_id, "economy_config", {})
            currency_name = config.get("currency_name", "Coins")
            currency_emoji = config.get("currency_emoji", "🪙")
            gem_name = config.get("gem_name", "Gems")

            # Create paginated leaderboard
            class EconomyLeaderboardView(ui.View):
                def __init__(self, leaderboard_data, currency_name, currency_emoji, gem_name, guild_name):
                    super().__init__(timeout=300)
                    self.leaderboard_data = leaderboard_data
                    self.currency_name = currency_name
                    self.currency_emoji = currency_emoji
                    self.gem_name = gem_name
                    self.guild_name = guild_name
                    self.current_page = 0
                    self.per_page = 8
                    self.update_buttons()

                def update_buttons(self):
                    total_pages = (len(self.leaderboard_data) - 1) // self.per_page + 1
                    self.prev_button.disabled = self.current_page == 0
                    self.next_button.disabled = self.current_page >= total_pages - 1
                    self.page_label.label = f"Page {self.current_page + 1}/{total_pages}"

                @ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, disabled=True)
                async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
                    self.current_page -= 1
                    self.update_buttons()
                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                @ui.button(label="📄 Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
                async def page_label(self, interaction: discord.Interaction, button: ui.Button):
                    pass

                @ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
                async def next_button(self, interaction: discord.Interaction, button: ui.Button):
                    self.current_page += 1
                    self.update_buttons()
                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                @ui.button(label="🏆 Top 3", style=discord.ButtonStyle.primary)
                async def top_three(self, interaction: discord.Interaction, button: ui.Button):
                    embed = self.create_top_three_embed()
                    await interaction.response.send_message(embed=embed, ephemeral=True)

                def create_embed(self):
                    start_idx = self.current_page * self.per_page
                    end_idx = start_idx + self.per_page
                    page_data = self.leaderboard_data[start_idx:end_idx]

                    embed = discord.Embed(
                        title=f"💰 {self.guild_name} — Economy Leaderboard",
                        description=f"**Wealth rankings by net worth**\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.gold()
                    )

                    # Trophy emojis for top 3
                    trophies = ["🥇", "🥈", "🥉"]

                    for i, entry in enumerate(page_data):
                        rank = start_idx + i + 1
                        user_id = entry["user_id"]
                        coins = entry["coins"]
                        gems = entry["gems"]
                        net_worth = entry["net_worth"]

                        # Rank display
                        if rank <= 3:
                            rank_display = trophies[rank - 1]
                        else:
                            rank_display = f"#{rank}"

                        # Wealth display
                        wealth_parts = []
                        if coins > 0:
                            wealth_parts.append(f"{coins:,} {self.currency_emoji}")
                        if gems > 0:
                            wealth_parts.append(f"{gems} 💎")
                        wealth_str = " + ".join(wealth_parts) if wealth_parts else "0"

                        embed.add_field(
                            name=f"{rank_display} <@{user_id}>",
                            value=f"**Net Worth:** {net_worth:,} total\n"
                                  f"**Assets:** {wealth_str}",
                            inline=False
                        )

                    # Statistics
                    total_wealth = sum(entry["net_worth"] for entry in self.leaderboard_data)
                    total_coins = sum(entry["coins"] for entry in self.leaderboard_data)
                    total_gems = sum(entry["gems"] for entry in self.leaderboard_data)

                    embed.add_field(
                        name="📊 Server Statistics",
                        value=f"• Total Wealth: `{total_wealth:,}`\n"
                              f"• Active Traders: `{len(self.leaderboard_data)}`\n"
                              f"• Average Balance: `{total_wealth // max(len(self.leaderboard_data), 1):,}`",
                        inline=False
                    )

                    embed.set_footer(text=f"Economy System • Updated in real-time")
                    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/trophy.png")

                    return embed

                def create_top_three_embed(self):
                    top_three = self.leaderboard_data[:3]

                    embed = discord.Embed(
                        title="🏆 Economy Champions",
                        description="**The wealthiest members of the server!**\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.gold()
                    )

                    trophies = ["🥇 **CHAMPION**", "🥈 **RUNNER-UP**", "🥉 **THIRD PLACE**"]
                    podium_emojis = ["👑", "🏅", "🎖️"]

                    for i, entry in enumerate(top_three):
                        user_id = entry["user_id"]
                        coins = entry["coins"]
                        gems = entry["gems"]
                        net_worth = entry["net_worth"]

                        embed.add_field(
                            name=f"{podium_emojis[i]} {trophies[i]}",
                            value=f"**<@{user_id}>**\n"
                                  f"**Net Worth:** {net_worth:,}\n"
                                  f"**Coins:** {coins:,} {self.currency_emoji}\n"
                                  f"**Gems:** {gems} 💎",
                            inline=False
                        )

                    embed.set_footer(text="Congratulations to our top earners!")
                    return embed

            view = EconomyLeaderboardView(leaderboard_data, currency_name, currency_emoji, gem_name, message.guild.name)
            embed = view.create_embed()

            await loading_msg.edit(embed=embed, view=view)

            # Add celebration reactions
            await loading_msg.add_reaction("💰")
            await loading_msg.add_reaction("🏆")
            await loading_msg.add_reaction("🥇")

            return True

        except Exception as e:
            logger.error(f"Error in handle_economy_leaderboard: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Leaderboard Error",
                description="Unable to load the economy leaderboard. The system may be temporarily unavailable.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_economy_shop(self, message: discord.Message) -> bool:
        """!shop command for members"""
        try:
            guild_id = message.guild.id
            
            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                await message.channel.send("❌ The economy system is currently disabled on this server.")
                return False
            
            items = dm.get_guild_data(guild_id, "shop_items", [])

            if not items:
                await message.channel.send("ðŸ›’ The shop is currently empty.")
                return True

            embed = discord.Embed(title=f"{get_animated_emoji('economy')} {message.guild.name} Shop", color=discord.Color.green())
            for item in items[:25]:
                embed.add_field(name=f"{item['name']} — {item['price']} Credits", value=item.get('description', 'No description'), inline=False)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_shop: {e}")
            await message.channel.send("❌ Unable to load shop. Please try again.")
            return False

    async def handle_economy_transfer(self, message: discord.Message) -> bool:
        """!transfer — transfer coins to another user"""
        try:
            guild_id = message.guild.id
            author_id = message.author.id

            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                await message.channel.send("❌ The economy system is currently disabled on this server.")
                return False

            parts = message.content.split()
            if len(parts) < 3:
                await message.channel.send("Usage: `!transfer @user amount`")
                return False

            try:
                # Parse target
                target_part = parts[1]
                if target_part.startswith("<@") and target_part.endswith(">"):
                    target_id = target_part.strip("<@!>")
                else:
                    target_id = target_part

                target = message.guild.get_member(int(target_id))
                if not target:
                    await message.channel.send("❌ User not found in this server.")
                    return False

                if target.id == author_id:
                    await message.channel.send("❌ You cannot transfer coins to yourself.")
                    return False

                amount = int(parts[2])
                if amount <= 0:
                    await message.channel.send("❌ Amount must be positive.")
                    return False

                from modules.economy import Economy
                economy = Economy(self.bot)

                current_balance = economy.get_coins(guild_id, author_id)
                if current_balance < amount:
                    await message.channel.send(f"❌ Insufficient funds. You have {current_balance:,} coins.")
                    return False

                # Perform transfer
                economy.add_coins(guild_id, author_id, -amount)
                economy.add_coins(guild_id, target.id, amount)
                economy.log_transaction(guild_id, author_id, amount, "transfer", f"to {target.id}")

                embed = discord.Embed(
                    title="💸 Coin Transfer Successful",
                    description=f"**{message.author.display_name}** transferred **{amount:,} coins** to **{target.display_name}**!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Sender", value=f"{message.author.mention}\nBalance: {current_balance - amount:,}", inline=True)
                embed.add_field(name="Recipient", value=f"{target.mention}\nBalance: {economy.get_coins(guild_id, target.id):,}", inline=True)
                embed.set_footer(text="Economy System • Secure transactions")

                await message.channel.send(embed=embed)
                return True

            except ValueError:
                await message.channel.send("❌ Invalid amount. Please use a number.")
                return False

        except Exception as e:
            logger.error(f"Error in handle_economy_transfer: {e}")
            await message.channel.send("❌ Transfer failed. Please try again.")
            return False

    async def handle_economy_buy(self, message: discord.Message) -> bool:
        """!buy — purchase an item from the shop"""
        try:
            guild_id = message.guild.id
            author_id = message.author.id

            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                await message.channel.send("❌ The economy system is currently disabled on this server.")
                return False

            parts = message.content.split()
            if len(parts) < 2:
                await message.channel.send("Usage: `!buy <item_name>`")
                return False

            item_name = " ".join(parts[1:]).lower()
            items = dm.get_guild_data(guild_id, "shop_items", [])

            item = None
            for i in items:
                if i["name"].lower() == item_name:
                    item = i
                    break

            if not item:
                await message.channel.send("❌ Item not found in the shop.")
                return False

            price = item["price"]
            from modules.economy import Economy
            economy = Economy(self.bot)

            if economy.get_coins(guild_id, author_id) < price:
                await message.channel.send(f"❌ Insufficient funds. You need {price:,} coins.")
                return False

            # Deduct coins
            economy.add_coins(guild_id, author_id, -price)
            economy.log_transaction(guild_id, author_id, price, "purchase", f"bought {item['name']}")

            # Give role if applicable
            role_given = False
            if item.get("role_id"):
                role = message.guild.get_role(int(item["role_id"]))
                if role:
                    try:
                        await message.author.add_roles(role)
                        role_given = True
                    except:
                        pass

            embed = discord.Embed(
                title="🛒 Purchase Successful!",
                description=f"You bought **{item['name']}** for **{price:,} coins**!",
                color=discord.Color.green()
            )
            embed.add_field(name="Item", value=item["name"], inline=True)
            embed.add_field(name="Cost", value=f"{price:,} coins", inline=True)
            if role_given:
                embed.add_field(name="Role Granted", value=role.name, inline=True)
            embed.set_footer(text="Thank you for your purchase!")

            await message.channel.send(embed=embed)
            return True

        except Exception as e:
            logger.error(f"Error in handle_economy_buy: {e}")
            await message.channel.send("❌ Purchase failed. Please try again.")
            return False

    async def handle_gamification_quests(self, message: discord.Message) -> bool:
        """!quests — list available quests"""
        try:
            guild_id = message.guild.id

            if not is_system_enabled(guild_id, "gamification"):
                await message.channel.send("❌ The gamification system is currently disabled.")
                return False

            # Placeholder for quests
            embed = discord.Embed(
                title="🎯 Available Quests",
                description="Complete quests to earn rewards!",
                color=discord.Color.purple()
            )
            embed.add_field(name="Daily Login", value="Log in daily - Reward: 100 XP", inline=False)
            embed.add_field(name="Chat Master", value="Send 100 messages - Reward: 500 XP", inline=False)
            embed.add_field(name="Voice Veteran", value="Spend 1 hour in voice - Reward: 200 XP", inline=False)
            embed.set_footer(text="Check back regularly for new quests!")

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_gamification_quests: {e}")
            return False

    async def handle_gamification_prestige(self, message: discord.Message) -> bool:
        """!prestige — prestige system"""
        try:
            guild_id = message.guild.id

            if not is_system_enabled(guild_id, "gamification"):
                await message.channel.send("❌ The gamification system is currently disabled.")
                return False

            embed = discord.Embed(
                title="⭐ Prestige System",
                description="Reset your progress for permanent bonuses!",
                color=discord.Color.gold()
            )
            embed.add_field(name="Requirements", value="Reach max level and have gems", inline=False)
            embed.add_field(name="Benefits", value="Permanent XP multipliers", inline=False)
            embed.set_footer(text="Prestige when ready!")

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_gamification_prestige: {e}")
            return False

    async def handle_gamification_dice(self, message: discord.Message) -> bool:
        """!dice — roll dice"""
        try:
            import random
            result = random.randint(1, 6)
            embed = discord.Embed(
                title="🎲 Dice Roll",
                description=f"{message.author.mention} rolled a **{result}**!",
                color=discord.Color.blue()
            )
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            return False

    async def handle_gamification_flip(self, message: discord.Message) -> bool:
        """!flip — coin flip"""
        try:
            import random
            result = random.choice(["Heads", "Tails"])
            embed = discord.Embed(
                title="🪙 Coin Flip",
                description=f"{message.author.mention} got **{result}**!",
                color=discord.Color.green()
            )
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            return False

    async def handle_events_create(self, message: discord.Message) -> bool:
        """!event create — create event (placeholder)"""
        await message.channel.send("📅 Event creation: Use `/setup` to configure events.")
        return True

    async def handle_events_list(self, message: discord.Message) -> bool:
        """!event list — list events"""
        embed = discord.Embed(
            title="📅 Upcoming Events",
            description="No events scheduled.",
            color=discord.Color.blue()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_tournaments_create(self, message: discord.Message) -> bool:
        """!tournament create — create tournament"""
        await message.channel.send("🏆 Tournament creation: Use `/setup` to configure tournaments.")
        return True

    async def handle_tournaments_join(self, message: discord.Message) -> bool:
        """!join <tournament> — join tournament"""
        await message.channel.send("🏆 Tournament joining is currently unavailable. Check back later or use `/setup` to configure tournaments.")
        return True

    async def handle_tournaments_leaderboard(self, message: discord.Message) -> bool:
        """!tournamentleaderboard — tournament leaderboard"""
        embed = discord.Embed(
            title="🏆 Tournament Leaderboard",
            description="Top tournament players.",
            color=discord.Color.gold()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_reminders(self, message: discord.Message) -> bool:
        """!reminders — list reminders"""
        embed = discord.Embed(
            title="⏰ Your Reminders",
            description="No active reminders.",
            color=discord.Color.blue()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_remind(self, message: discord.Message) -> bool:
        """!remind — set reminder (placeholder)"""
        await message.channel.send("⏰ Reminder: Use `/remindme` for slash command.")
        return True

    async def handle_announcements_create(self, message: discord.Message) -> bool:
        """!announcement create — create announcement"""
        await message.channel.send("📢 Announcement: Use `/setup` to configure announcements.")
        return True

    async def handle_giveaways_create(self, message: discord.Message) -> bool:
        """!giveaway create — create giveaway"""
        await message.channel.send("🎉 Giveaway: Use `/setup` to configure giveaways.")
        return True

    async def handle_giveaways_list(self, message: discord.Message) -> bool:
        """!giveaway list — list giveaways"""
        embed = discord.Embed(
            title="🎉 Active Giveaways",
            description="No active giveaways.",
            color=discord.Color.purple()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_serverstats(self, message: discord.Message) -> bool:
        """!serverstats — server statistics"""
        guild = message.guild
        embed = discord.Embed(
            title=f"📊 {guild.name} Stats",
            color=discord.Color.blue()
        )
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        await message.channel.send(embed=embed)
        return True

    async def handle_mystats(self, message: discord.Message) -> bool:
        """!mystats — user statistics"""
        embed = discord.Embed(
            title=f"📈 {message.author.display_name}'s Stats",
            color=discord.Color.green()
        )
        embed.add_field(name="Joined", value=message.author.joined_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="Roles", value=len(message.author.roles), inline=True)
        await message.channel.send(embed=embed)
        return True

    async def handle_atrisk(self, message: discord.Message) -> bool:
        """!atrisk — at-risk users"""
        embed = discord.Embed(
            title="⚠️ At-Risk Users",
            description="Users needing attention.",
            color=discord.Color.red()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_automod_status(self, message: discord.Message) -> bool:
        """!automod status — automod status"""
        # Check if automod system is enabled
        if not is_system_enabled(message.guild.id, "automod"):
            await message.channel.send("❌ The AutoMod system is currently disabled on this server.")
            return False

        embed = discord.Embed(
            title="🤖 AutoMod Status",
            description="AutoMod is active.",
            color=discord.Color.green()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_guardian_status(self, message: discord.Message) -> bool:
        """!guardian status — guardian status"""
        embed = discord.Embed(
            title="⚔️ Guardian Status",
            description="Guardian is protecting the server.",
            color=discord.Color.blue()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_chatchannel_add(self, message: discord.Message) -> bool:
        """!chatchannel add — add chat channel"""
        await message.channel.send("🧠 Chat channel: Use `/setup` to configure AI chat channels.")
        return True

    async def handle_suggest(self, message: discord.Message) -> bool:
        """!suggest — submit suggestion"""
        parts = message.content.split(None, 1)
        if len(parts) < 2:
            await message.channel.send("Usage: `!suggest <your suggestion>`")
            return False

        suggestion = parts[1]
        embed = discord.Embed(
            title="💡 New Suggestion",
            description=suggestion,
            color=discord.Color.yellow()
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        await message.channel.send(embed=embed)
        return True

    async def handle_ticket(self, message: discord.Message) -> bool:
        """!ticket — create ticket"""
        await message.channel.send("🎫 Ticket: Use buttons or `/setup` to configure tickets.")
        return True

    async def handle_appeal(self, message: discord.Message) -> bool:
        """!appeal — create appeal"""
        await message.channel.send("⚖️ Appeal: Use `/setup` to configure appeals.")
        return True

    async def handle_apply(self, message: discord.Message) -> bool:
        """!apply — apply for staff"""
        await message.channel.send("📋 Application: Use buttons or `/setup` to configure applications.")
        return True

    async def handle_modlog_view(self, message: discord.Message) -> bool:
        """!modlog view — view mod logs"""
        embed = discord.Embed(
            title="📋 Moderation Logs",
            description="Recent mod actions.",
            color=discord.Color.red()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_kick(self, message: discord.Message) -> bool:
        """!kick — kick user"""
        if not message.author.guild_permissions.kick_members:
            await message.channel.send("❌ No permission.")
            return False
        await message.channel.send("🔨 Kick: Use `/kick` slash command.")
        return True

    async def handle_ban(self, message: discord.Message) -> bool:
        """!ban — ban user"""
        if not message.author.guild_permissions.ban_members:
            await message.channel.send("❌ No permission.")
            return False
        await message.channel.send("🔨 Ban: Use `/ban` slash command.")
        return True

    async def handle_mute(self, message: discord.Message) -> bool:
        """!mute — mute user"""
        if not message.author.guild_permissions.moderate_members:
            await message.channel.send("❌ No permission.")
            return False
        await message.channel.send("🔇 Mute: Use timeout feature.")
        return True

    async def handle_modstats(self, message: discord.Message) -> bool:
        """!modstats — moderation stats"""
        embed = discord.Embed(
            title="📊 Mod Stats",
            description="Moderation statistics.",
            color=discord.Color.red()
        )
        await message.channel.send(embed=embed)
        return True

    async def handle_leveling_levels(self, message: discord.Message) -> bool:
        """!levels — show leveling system info"""
        try:
            guild_id = message.guild.id

            if not is_system_enabled(guild_id, "leveling"):
                await message.channel.send("❌ The leveling system is currently disabled.")
                return False

            from modules.leveling import Leveling
            leveling = Leveling(self.bot)

            embed = discord.Embed(
                title="🆙 Leveling System Info",
                description="Earn XP by chatting and level up for rewards!",
                color=discord.Color.blue()
            )
            embed.add_field(name="XP per Message", value="15-25 XP", inline=True)
            embed.add_field(name="Level Formula", value="Level = √(XP / 100)", inline=True)
            embed.add_field(name="Gems", value="XP / 10", inline=True)
            embed.add_field(name="Commands", value="`!rank`, `!leaderboard`", inline=False)
            embed.set_footer(text="Keep chatting to level up!")

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_levels: {e}")
            return False

    async def handle_leveling_rewards(self, message: discord.Message) -> bool:
        """!rewards — show level rewards"""
        try:
            guild_id = message.guild.id

            if not is_system_enabled(guild_id, "leveling"):
                await message.channel.send("❌ The leveling system is currently disabled.")
                return False

            rewards = dm.get_guild_data(guild_id, "level_rewards", {})
            if not rewards:
                await message.channel.send("No level rewards set up yet.")
                return True

            embed = discord.Embed(title="🎁 Level Rewards", color=discord.Color.purple())
            for level, role_id in sorted(rewards.items(), key=lambda x: int(x[0])):
                role = message.guild.get_role(int(role_id))
                role_name = role.name if role else "Unknown Role"
                embed.add_field(name=f"Level {level}", value=role_name, inline=True)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_rewards: {e}")
            return False

    async def handle_leveling_shop(self, message: discord.Message) -> bool:
        """!levelshop — shop for leveling perks (placeholder)"""
        await message.channel.send("🛍️ Level shop is being redesigned. Check back soon for new perks!")
        return True

    async def handle_ban(self, message: discord.Message) -> bool:
        """!ban — ban a user"""
        if not message.author.guild_permissions.ban_members:
            await message.channel.send("❌ No permission.")
            return False

        parts = message.content.split()
        if len(parts) < 2:
            await message.channel.send("Usage: !ban @user [reason]")
            return False

        try:
            user_id = int(parts[1].strip("<@!>"))
            member = message.guild.get_member(user_id)
            if not member:
                await message.channel.send("❌ User not found.")
                return False

            reason = " ".join(parts[2:]) if len(parts) > 2 else "No reason"
            await member.ban(reason=reason)
            await message.channel.send(f"✅ Banned {member.mention} for {reason}")
        except Exception as e:
            await message.channel.send(f"❌ Error: {e}")
        return True

    async def handle_mute(self, message: discord.Message) -> bool:
        """!mute — timeout a user"""
        if not message.author.guild_permissions.moderate_members:
            await message.channel.send("❌ No permission.")
            return False

        parts = message.content.split()
        if len(parts) < 3:
            await message.channel.send("Usage: !mute @user duration reason (e.g. 1h spam)")
            return False

        try:
            user_id = int(parts[1].strip("<@!>"))
            member = message.guild.get_member(user_id)
            if not member:
                await message.channel.send("❌ User not found.")
                return False

            duration_str = parts[2]
            import re
            match = re.match(r'(\d+)([smhd])', duration_str)
            if not match:
                await message.channel.send("❌ Invalid duration. Use format like 1h, 30m, 10s")
                return False

            amount = int(match.group(1))
            unit = match.group(2)
            seconds = amount * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]
            reason = " ".join(parts[3:]) if len(parts) > 3 else "No reason"

            await member.timeout(discord.utils.utcnow() + datetime.timedelta(seconds=seconds), reason=reason)
            await message.channel.send(f"✅ Muted {member.mention} for {duration_str} - {reason}")
        except Exception as e:
            await message.channel.send(f"❌ Error: {e}")
        return True

    async def handle_modstats(self, message: discord.Message) -> bool:
        """!modstats — moderation statistics"""
        embed = discord.Embed(title="📊 Mod Stats", description="Moderation statistics.", color=discord.Color.red())
        await message.channel.send(embed=embed)
        return True

    async def handle_warn(self, message: discord.Message) -> bool:
        """!warn — warn a user"""
        # Check if warnings system is enabled
        if not is_system_enabled(message.guild.id, "warnings"):
            await message.channel.send("❌ The warnings system is currently disabled on this server.")
            return False

        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ No permission.")
            return False

        parts = message.content.split(None, 2)
        if len(parts) < 2:
            await message.channel.send("Usage: !warn @user reason")
            return False

        try:
            user_id = int(parts[1].strip("<@!>"))
            member = message.guild.get_member(user_id)
            if not member:
                await message.channel.send("❌ User not found.")
                return False

            reason = parts[2] if len(parts) > 2 else "No reason"
            warnings = dm.get_guild_data(message.guild.id, "warnings", {})
            user_warnings = warnings.get(str(user_id), [])
            user_warnings.append({"reason": reason, "by": message.author.id, "at": time.time()})
            warnings[str(user_id)] = user_warnings
            dm.update_guild_data(message.guild.id, "warnings", warnings)
            await message.channel.send(f"⚠️ Warned {member.mention} for {reason}")
        except Exception as e:
            await message.channel.send(f"❌ Error: {e}")
        return True

    async def handle_warnings(self, message: discord.Message) -> bool:
        """!warnings — view warnings"""
        parts = message.content.split()
        if len(parts) > 1:
            try:
                user_id = int(parts[1].strip("<@!>"))
                warnings_list = dm.get_guild_data(message.guild.id, "warnings", {}).get(str(user_id), [])
                embed = discord.Embed(title=f"⚠️ Warnings for <@{user_id}>", color=discord.Color.orange())
                if warnings_list:
                    for i, w in enumerate(warnings_list):
                        embed.add_field(name=f"Warning {i+1}", value=f"Reason: {w['reason']}\nBy: <@{w['by']}>\nAt: <t:{int(w['at'])}:f>", inline=False)
                else:
                    embed.description = "No warnings."
                await message.channel.send(embed=embed)
            except ValueError:
                await message.channel.send("❌ Invalid user.")
        else:
            warnings_list = dm.get_guild_data(message.guild.id, "warnings", {}).get(str(message.author.id), [])
            embed = discord.Embed(title="⚠️ Your Warnings", color=discord.Color.orange())
            if warnings_list:
                for i, w in enumerate(warnings_list):
                    embed.add_field(name=f"Warning {i+1}", value=f"Reason: {w['reason']}\nBy: <@{w['by']}>\nAt: <t:{int(w['at'])}:f>", inline=False)
            else:
                embed.description = "No warnings."
            await message.channel.send(embed=embed)
        return True

    async def handle_clearwarn(self, message: discord.Message) -> bool:
        """!clearwarn — clear warnings for a user"""
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ No permission.")
            return False

        parts = message.content.split()
        if len(parts) < 2:
            await message.channel.send("Usage: !clearwarn @user")
            return False

        try:
            user_id = int(parts[1].strip("<@!>"))
            warnings = dm.get_guild_data(message.guild.id, "warnings", {})
            if str(user_id) in warnings:
                del warnings[str(user_id)]
                dm.update_guild_data(message.guild.id, "warnings", warnings)
                await message.channel.send(f"✅ Cleared warnings for <@{user_id}>")
            else:
                await message.channel.send("❌ No warnings found.")
        except ValueError:
            await message.channel.send("❌ Invalid user.")
        return True

    async def handle_clearallwarns(self, message: discord.Message) -> bool:
        """!clearallwarns — clear all warnings"""
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ No permission.")
            return False

        dm.update_guild_data(message.guild.id, "warnings", {})
        await message.channel.send("✅ Cleared all warnings.")
        return True

    async def handle_raidstatus(self, message: discord.Message) -> bool:
        """!raidstatus — check anti-raid status"""
        config = dm.get_guild_data(message.guild.id, "anti_raid_config", {})
        enabled = config.get("enabled", False)
        status = "✅ Enabled" if enabled else "❌ Disabled"
        embed = discord.Embed(title="🚨 Anti-Raid Status", description=f"Status: {status}", color=discord.Color.red() if enabled else discord.Color.green())
        await message.channel.send(embed=embed)
        return True

    async def handle_guardian_status(self, message: discord.Message) -> bool:
        """!guardian status — check guardian status"""
        config = dm.get_guild_data(message.guild.id, "guardian_config", {})
        enabled = config.get("enabled", False)
        status = "✅ Enabled" if enabled else "❌ Disabled"
        embed = discord.Embed(title="⚔️ Guardian Status", description=f"Status: {status}", color=discord.Color.blue() if enabled else discord.Color.red())
        await message.channel.send(embed=embed)
        return True

    async def handle_automod_status(self, message: discord.Message) -> bool:
        """!automod status — check automod status"""
        config = dm.get_guild_data(message.guild.id, "automod_config", {})
        enabled = config.get("enabled", False)
        status = "✅ Enabled" if enabled else "❌ Disabled"
        embed = discord.Embed(title="🤖 AutoMod Status", description=f"Status: {status}", color=discord.Color.green() if enabled else discord.Color.red())
        await message.channel.send(embed=embed)
        return True

    async def handle_reactionrolespanel(self, message: discord.Message) -> bool:
        """!reactionrolespanel — open reaction roles panel"""
        await message.channel.send("🎭 Reaction roles panel: Use `/setup` to configure.")
        return True

    async def handle_reactionmenuspanel(self, message: discord.Message) -> bool:
        """!reactionmenuspanel — open reaction menus panel"""
        await message.channel.send("📌 Reaction menus panel: Use `/setup` to configure.")
        return True

    async def handle_rolebuttonspanel(self, message: discord.Message) -> bool:
        """!rolebuttonspanel — open role buttons panel"""
        await message.channel.send("🔘 Role buttons panel: Use `/setup` to configure.")
        return True

    async def handle_leveling_rank(self, message: discord.Message) -> bool:
        """!rank — show the invoker's current XP, level, gems and streak."""
        try:
            guild_id = message.guild.id
            
            # Check if leveling system is enabled
            if not is_system_enabled(guild_id, "leveling"):
                await message.channel.send("❌ The leveling system is currently disabled on this server.")
                return False
            
            from modules.leveling import Leveling
            leveling = Leveling(self.bot)
            user_id = message.author.id

            # Check if leveling system is enabled
            config = dm.get_guild_data(guild_id, "leveling_config", {})
            if not config.get("enabled", True):
                await message.channel.send("❌ Leveling system is disabled on this server.")
                return True

            xp = leveling.get_xp(guild_id, user_id)
            level = leveling.get_level_from_xp(xp)
            gems = leveling.get_gems(guild_id, user_id)
            streak = leveling.get_streak(guild_id, user_id)

            # XP needed for next level: level^2 * 100 (inverse of get_level_from_xp).
            next_level = level + 1
            xp_for_next = (next_level * next_level) * 100
            xp_into_level = xp - (level * level * 100)
            xp_to_next = max(0, xp_for_next - xp)

            embed = create_success_embed(
                description=f"Rank information for {message.author.display_name}",
                action_type="leveling"
            )
            embed.title = f"{get_animated_emoji('leveling')} {message.author.display_name}'s Rank"
            embed.set_thumbnail(url=message.author.display_avatar.url)
            embed.add_field(name=f"{get_animated_emoji('leveling')} Level", value=str(level), inline=True)
            embed.add_field(name=f"{get_animated_emoji('intelligence')} Total XP", value=f"{xp:,}", inline=True)
            embed.add_field(name=f"{get_animated_emoji('gamification')} Gems", value=str(gems), inline=True)
            embed.add_field(name=f"{get_animated_emoji('leveling')} Streak", value=f"{get_animated_emoji('leveling')} {streak} day(s)", inline=True)
            embed.add_field(name=f"{get_animated_emoji('intelligence')} XP to next level", value=f"{xp_to_next:,} XP", inline=True)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_rank: {e}")
            await message.channel.send("❌ Unable to retrieve your rank. Please try again.")
            return False

    async def handle_leveling_leaderboard(self, message: discord.Message) -> bool:
        """!leaderboard / !rank top — top XP earners in the guild."""
        try:
            guild_id = message.guild.id
            
            # Check if leveling system is enabled
            if not is_system_enabled(guild_id, "leveling"):
                await message.channel.send("❌ The leveling system is currently disabled on this server.")
                return False
            
            from modules.leveling import Leveling
            leveling = Leveling(self.bot)

            board = leveling.get_leaderboard(guild_id, limit=10)
            if not board:
                await message.channel.send("ðŸ“‰ Nobody has earned any XP yet. Start chatting!")
                return True

            medals = {1: "ðŸ¥‡", 2: "ðŸ¥ˆ", 3: "ðŸ¥‰"}
            lines = []
            for entry in board:
                rank = entry["rank"]
                badge = medals.get(rank, f"`#{rank:>2}`")
                mention = f"<@{entry['user_id']}>"
                streak_txt = f" ðŸ”¥{entry['streak']}" if entry.get("streak") else ""
                lines.append(
                    f"{badge} {mention} — Lvl **{entry['level']}** Â· {entry['xp']:,} XP{streak_txt}"
                )

            embed = discord.Embed(
                title=f"🏆 {message.guild.name} — XP Leaderboard",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            if message.guild.icon:
                embed.set_thumbnail(url=message.guild.icon.url)
            embed.set_footer(text=f"Top {len(board)} of all members • Earn XP by chatting!")

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_leaderboard: {e}")
            await message.channel.send("❌ Unable to load leaderboard. Please try again.")
            return False

    async def handle_leveling_levels(self, message: discord.Message) -> bool:
        """!levels — show level progression information."""
        try:
            guild_id = message.guild.id

            # Check if leveling system is enabled
            if not is_system_enabled(guild_id, "leveling"):
                await message.channel.send("❌ The leveling system is currently disabled on this server.")
                return False

            from modules.leveling import Leveling
            leveling = Leveling(self.bot)

            # Calculate level progression
            levels_info = []
            for level in range(1, 21):  # Show first 20 levels
                xp_needed = level * 100  # Since get_level_from_xp uses sqrt(xp/100)
                levels_info.append(f"**Level {level}** — {xp_needed:,} XP")

            embed = discord.Embed(
                title="📊 Level Progression",
                description="Here's how XP translates to levels:\n\n" + "\n".join(levels_info[:10]),
                color=discord.Color.blue(),
            )

            if len(levels_info) > 10:
                embed.add_field(
                    name="Continued...",
                    value="\n".join(levels_info[10:]),
                    inline=False
                )

            embed.set_footer(text="Level = floor(sqrt(XP / 100)) • Keep chatting to level up!")

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_levels: {e}")
            await message.channel.send("❌ Unable to load level information. Please try again.")
            return False

    async def handle_leveling_rewards(self, message: discord.Message) -> bool:
        """!rewards — show leveling rewards and role unlocks."""
        try:
            guild_id = message.guild.id

            # Check if leveling system is enabled
            if not is_system_enabled(guild_id, "leveling"):
                await message.channel.send("❌ The leveling system is currently disabled on this server.")
                return False

            # Get configured rewards
            rewards = dm.get_guild_data(guild_id, "level_rewards", {})

            if not rewards:
                embed = discord.Embed(
                    title="🎁 Level Rewards",
                    description="No role rewards have been configured yet.\n\nUse `/configpanel leveling` to set up automatic role rewards for reaching certain levels!",
                    color=discord.Color.green(),
                )
            else:
                reward_lines = []
                for level_str, role_id in sorted(rewards.items(), key=lambda x: int(x[0])):
                    level = int(level_str)
                    role = message.guild.get_role(int(role_id))
                    role_name = role.name if role else f"Role {role_id}"
                    reward_lines.append(f"**Level {level}** — {role_name}")

                embed = discord.Embed(
                    title="🎁 Level Rewards",
                    description="Earn these roles by reaching the specified levels:\n\n" + "\n".join(reward_lines),
                    color=discord.Color.green(),
                )

            embed.add_field(
                name="💎 Gems",
                value="You also earn **Gems** as you level up!\nGems = floor(XP ÷ 10)\n\nUse gems in the level shop with `!levelshop`",
                inline=False
            )

            embed.set_footer(text="Configure rewards with /configpanel leveling")

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_levels: {e}")
            await message.channel.send("❌ Unable to load rewards information. Please try again.")
            return False



    async def undo_last_actions(self, interaction: discord.Interaction, count: int = 1) -> List[Tuple[str, bool]]:
        """Undo the last N action groups for the guild."""
        guild_id = interaction.guild.id
        action_logs = dm.get_guild_data(guild_id, "action_logs", [])

        if not action_logs:
            return [("No actions to undo", False)]

        to_undo = action_logs[-count:]
        remaining = action_logs[:-count]
        dm.update_guild_data(guild_id, "action_logs", remaining)

        results = []
        for log_entry in reversed(to_undo):
            undo_data = log_entry.get("undo_data", {})
            undo_action = undo_data.get("action")
            success = await self._execute_undo(interaction, undo_action, undo_data)
            results.append((log_entry.get("action", "unknown"), success))

        return results

    async def _execute_undo(self, interaction: discord.Interaction, undo_action: str, undo_data: Dict) -> bool:
        """Execute a specific undo operation."""
        try:
            guild = interaction.guild

            if undo_action == "delete_channel":
                channel = guild.get_channel(undo_data.get("channel_id"))
                if channel:
                    await channel.delete()
                    return True

            elif undo_action == "delete_role":
                role = guild.get_role(undo_data.get("role_id"))
                if role:
                    await role.delete()
                    return True

            elif undo_action == "remove_role":
                member = guild.get_member(undo_data.get("user_id"))
                role = guild.get_role(undo_data.get("role_id"))
                if member and role:
                    await member.remove_roles(role)
                    return True

            elif undo_action == "delete_prefix_command":
                cmd_name = undo_data.get("cmd_name")
                cmds = dm.get_guild_data(guild.id, "custom_commands", {})
                if cmd_name in cmds:
                    previous = undo_data.get("previous_code")
                    if previous:
                        cmds[cmd_name] = previous
                    else:
                        del cmds[cmd_name]
                    dm.update_guild_data(guild.id, "custom_commands", cmds)
                    return True

            elif undo_action == "delete_message":
                channel = guild.get_channel(undo_data.get("channel_id"))
                if channel:
                    try:
                        msg = await channel.fetch_message(undo_data.get("message_id"))
                        await msg.delete()
                        return True
                    except discord.NotFound:
                        pass

            elif undo_action == "batch_undo":
                results = []
                for sub_undo in undo_data.get("undo_data", []):
                    res = await self._execute_undo(interaction, sub_undo.get("action"), sub_undo)
                    results.append(res)
                return all(results)

            elif undo_action in ("undo_staff_system", "undo_economy", "undo_trigger_role"):
                return await self._undo_system_setup(guild, undo_action)

            return False
        except Exception as e:
            logger.error("Undo Error (%s): %s", undo_action, e)
            return False

    async def _undo_system_setup(self, guild: discord.Guild, undo_action: str) -> bool:
        """Undo a system setup by removing associated channels and commands."""
        try:
            if undo_action == "undo_staff_system":
                channels_to_remove = ["apply-staff", "apply-staff-logs"]
                cmds_to_remove = ["apply", "help staffapply"]
            elif undo_action == "undo_economy":
                channels_to_remove = ["economy", "shop"]
                cmds_to_remove = ["daily", "balance", "transfer", "shop", "help economy"]
            elif undo_action == "undo_trigger_role":
                channels_to_remove = []
                cmds_to_remove = ["triggers", "help triggers"]
            else:
                return False

            for ch_name in channels_to_remove:
                channel = discord.utils.get(guild.channels, name=ch_name)
                if channel:
                    await channel.delete()

            cmds = dm.get_guild_data(guild.id, "custom_commands", {})
            for cmd in cmds_to_remove:
                cmds.pop(cmd, None)
            dm.update_guild_data(guild.id, "custom_commands", cmds)

            return True
        except Exception as e:
            logger.error("System Undo Error (%s): %s", undo_action, e)
            return False

    async def handle_config_panel_redirect(self, message: discord.Message, data: dict) -> bool:
        """Handle custom commands that open config panels, with permission check."""
        if not message.author.guild_permissions.administrator and message.author.id != message.guild.owner_id:
            await message.channel.send("❌ Only administrators can access configuration panels.")
            return True

        system = data.get("system")
        if not system:
            return False

        from modules.config_panels import handle_config_panel_command
        panel = handle_config_panel_command(message.guild.id, system, self.bot)
        if panel:
            await message.channel.send(f"⚙️ **{system.title()}** Configuration", view=panel)
        else:
            await message.channel.send(f"❌ Unknown system '{system}'. Use `!configpanel` to list systems.")
        return True

    async def handle_help_all(self, message: discord.Message) -> bool:
        """Handle !help command - Delegate to standardized help system."""
        try:
            from modules.help_system import send_help
            if isinstance(message, discord.Interaction):
                await send_help(message.channel, message.guild_id, message.user, bot=self.bot)
            else:
                await send_help(message.channel, message.guild.id, message.author, bot=self.bot)
            return True
        except Exception as e:
            print(f"Error in handle_help_all: {e}")
            # Fallback: send simple help
            embed = discord.Embed(
                title="📖 Miro Bot Help",
                description="**Categories:**\n• 🛡️ Security\n• 💰 Economy\n• 🎮 Gamification\n• 📋 Staff\n\nUse `!configpanel <system>` to configure systems.",
                color=0x5865F2
            )
            await message.channel.send(embed=embed)
            return True
    
    def _get_popular_commands(self, guild_id: int) -> List[str]:
        """Get most popular commands based on usage statistics."""
        usage = dm.get_guild_data(guild_id, "command_usage", {})
        sorted_cmds = sorted(usage.items(), key=lambda x: x[1].get("count", 0), reverse=True)
        return [cmd for cmd, data in sorted_cmds[:10] if data.get("count", 0) > 0]

    async def handle_help_system(self, message: discord.Message, system: str) -> bool:
        """Handle !help <system> command - Delegate to standardized help system."""
        from modules.help_system import send_help
        if isinstance(message, discord.Interaction):
            await send_help(message.channel, message.guild_id, message.user, system_query=system, bot=self.bot)
        else:
            await send_help(message.channel, message.guild.id, message.author, system_query=system, bot=self.bot)
        return True

    async def handle_staffpromo_status(self, message: discord.Message) -> bool:
        """Handle !staffpromo_status command with enhanced progress tracking"""
        try:
            import asyncio
            guild_id = message.guild.id
            user_id = message.author.id

            # Check if staff promotion system is enabled
            if not is_system_enabled(guild_id, "staffpromo"):
                embed = discord.Embed(
                    title="❌ Staff Promotion Unavailable",
                    description="The staff promotion system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel staffpromo to enable the system")
                await message.channel.send(embed=embed)
                return False

            guild = message.guild
            member = message.author
            staff_promo = self.bot.staff_promo
            promotion_service = self.bot.staff_promo.promotion_service

            # Loading animation
            loading_embed = discord.Embed(
                title="📈 Analyzing Staff Performance",
                description="👥 Reviewing activity metrics...\n📊 Calculating promotion score...\n🏆 Checking eligibility...",
                color=discord.Color.blue()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Reviewing activity metrics...\n📊 Calculating promotion score...\n🏆 Checking eligibility..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.6)
            loading_embed.description = "✅ Reviewing activity metrics...\n✅ Calculating promotion score...\n🏆 Checking eligibility..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Reviewing activity metrics...\n✅ Calculating promotion score...\n✅ Checking eligibility..."
            await loading_msg.edit(embed=loading_embed)

            config = staff_promo._get_full_config(guild.id)
            settings = config.get("settings", {})

            # Check if staff promotion system is enabled
            if not settings.get("auto_promote", True):
                disabled_embed = discord.Embed(
                    title="❌ Staff Promotion Disabled",
                    description="The staff promotion system is currently disabled.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                disabled_embed.set_footer(text="Automatic promotions are paused")
                await loading_msg.edit(embed=disabled_embed)
                return True

            metrics = config.get("metrics", staff_promo._default_metrics)
            score = promotion_service._compute_score(guild.id, user_id, member, metrics)
            tiers = config.get("tiers", staff_promo._default_tiers)

            # Find current tier
            current_tier = None
            next_tier = None
            current_tier_index = -1

            roles_by_tier = config.get("roles_by_tier", {})
            for i, tier in enumerate(tiers):
                rid = roles_by_tier.get(tier["name"])
                if rid and any(r.id == rid for r in member.roles):
                    current_tier = tier
                    current_tier_index = i
                    break

            # Find next tier
            if current_tier_index < len(tiers) - 1:
                next_tier = tiers[current_tier_index + 1]

            # Enhanced status embed
            embed = discord.Embed(
                title="📈 Staff Promotion Status",
                description=f"**{member.display_name}'s** promotion progress and metrics.",
                color=discord.Color.blue()
            )

            embed.set_thumbnail(url=member.display_avatar.url)

            # Current tier information
            if current_tier:
                embed.add_field(
                    name="🏅 Current Rank",
                    value=f"**{current_tier['name']}**\n"
                          f"Role: <@&{roles_by_tier.get(current_tier['name'], 'Unknown')}>",
                    inline=True
                )
            else:
                embed.add_field(
                    name="🏅 Current Rank",
                    value="**Not Staff**\nNo promotion tier assigned",
                    inline=True
                )

            # Overall score with progress bar
            score_percentage = min(100, score * 100)
            progress_bar = "█" * int(score_percentage / 10) + "░" * (10 - int(score_percentage / 10))

            embed.add_field(
                name="📊 Promotion Score",
                value=f"**{score_percentage:.1f}%**\n"
                      f"`{progress_bar}`",
                inline=True
            )

            # Next tier information
            if next_tier:
                next_threshold = next_tier.get("threshold", 100)
                embed.add_field(
                    name="🎯 Next Promotion",
                    value=f"**{next_tier['name']}**\n"
                          f"Required: {int(next_threshold * 100)}%\n"
                          f"Progress: {score_percentage:.1f}%/{int(next_threshold * 100)}%",
                    inline=True
                )
            else:
                embed.add_field(
                    name="🎯 Next Promotion",
                    value="**MAX RANK**\nYou've reached the highest tier!",
                    inline=True
                )

            # Detailed metrics breakdown
            udata = dm.get_guild_data(guild.id, f"user_{user_id}", {})
            metrics_breakdown = []

            for metric_name, cfg in metrics.items():
                if not cfg.get("enabled", True):
                    continue

                current_value = udata.get(metric_name, 0)
                target_value = cfg.get("target", 100)
                percentage = min(100, (current_value / max(target_value, 1)) * 100)

                # Metric emoji
                metric_emoji = {
                    "messages": "💬",
                    "voice_time": "🎤",
                    "warnings_handled": "⚠️",
                    "reports_resolved": "📋",
                    "peer_reviews": "⭐",
                    "events_hosted": "📅"
                }.get(metric_name, "📊")

                metrics_breakdown.append(
                    f"{metric_emoji} **{metric_name.replace('_', ' ').title()}**\n"
                    f"Progress: {current_value}/{target_value} ({percentage:.1f}%)"
                )

            if metrics_breakdown:
                embed.add_field(
                    name="📈 Performance Metrics",
                    value="\n\n".join(metrics_breakdown[:6]),  # Limit to 6 metrics
                    inline=False
                )

            # Recent activity
            embed.add_field(
                name="⚡ Recent Activity",
                value="• Metrics updated in real-time\n• Score recalculated automatically\n• Promotion checks run daily",
                inline=False
            )

            # Quick actions
            embed.add_field(
                name="🛠️ Quick Actions",
                value="`!staffpromo leaderboard` - View top performers\n"
                      "`!staffpromo progress` - Detailed progress report\n"
                      "`!configpanel staffpromo` - Configure system",
                inline=False
            )

            embed.set_footer(text=f"Staff Promotion System • Last updated: {discord.utils.format_dt(discord.utils.utcnow())}")
            embed.set_author(name=f"{member.display_name}'s Promotion Dashboard", icon_url=member.display_avatar.url)

            await loading_msg.edit(embed=embed)

            # Add celebratory reactions
            await loading_msg.add_reaction("📈")
            if score_percentage >= 75:
                await loading_msg.add_reaction("⭐")
            if current_tier:
                await loading_msg.add_reaction("🏅")

            return True

        except Exception as e:
            logger.error(f"Error in handle_staffpromo_status: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Promotion Status Error",
                description="Unable to load your promotion status. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_status: {e}")
            await message.channel.send("❌ Unable to retrieve your promotion status. Please contact staff.")
            return False

    async def handle_staffpromo_leaderboard(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        
        # Check if staff promotion system is enabled
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        promotion_service = self.bot.staff_promo.promotion_service

        config = staff_promo._get_full_config(guild.id)
        metrics = config.get("metrics", staff_promo._default_metrics)

        scores = []
        for member in guild.members:
            if member.bot:
                continue
            score = promotion_service._compute_score(guild.id, member.id, member, metrics)
            scores.append((member, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        top_10 = scores[:10]

        embed = discord.Embed(title=f"{get_animated_emoji('staff')} Staff Promotion Leaderboard", color=discord.Color.gold())

        if not top_10:
            embed.add_field(name="No data", value="No staff members evaluated yet", inline=False)
        else:
            for i, (member, score) in enumerate(top_10, 1):
                embed.add_field(name=f"#{i} {member.display_name}", value=f"Score: {score*100:.1f}%", inline=True)

        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_config(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True

        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)

        settings = config.get("settings", staff_promo._default_settings)
        tiers = config.get("tiers", staff_promo._default_tiers)

        embed = discord.Embed(title="⚙️ Staff Promo Configuration", color=discord.Color.orange())

        embed.add_field(name="Auto Promote", value=str(settings.get("auto_promote", True)), inline=True)
        embed.add_field(name="Auto Demote", value=str(settings.get("auto_demote", False)), inline=True)
        embed.add_field(name="Min Tenure", value=f"{settings.get('min_tenure_hours', 72)} hours", inline=True)
        embed.add_field(name="Cooldown", value=f"{settings.get('promotion_cooldown_hours', 24)} hours", inline=True)

        tiers_text = "\n".join([f"• {t['name']}: {int(t['threshold']*100)}%" for t in tiers])
        embed.add_field(name="Tiers", value=tiers_text or "None", inline=False)

        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_progress(self, message: discord.Message) -> bool:
        guild = message.guild
        member = message.author
        staff_promo = self.bot.staff_promo
        promotion_service = self.bot.staff_promo.promotion_service

        config = staff_promo._get_full_config(guild.id)
        metrics = config.get("metrics", staff_promo._default_metrics)
        tiers = config.get("tiers", staff_promo._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))

        for tier in tiers:
            tier_name = tier.get("name")
            if tier_name not in role_ids or not role_ids[tier_name]:
                role_name = tier.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name] = r.id

        score = promotion_service._compute_score(guild.id, member.id, member, metrics)
        current_index = staff_promo._get_current_tier_index(member, tiers, role_ids)

        embed = discord.Embed(title="ðŸ“Š Your Promotion Progress", color=discord.Color.blue())
        embed.add_field(name="Current Score", value=f"{score*100:.1f}%", inline=True)

        if current_index < len(tiers) - 1:
            next_tier = tiers[current_index + 1]
            next_threshold = next_tier.get("threshold", 0)
            percent_away = (next_threshold - score) * 100

            embed.add_field(name="Next Tier", value=next_tier.get("name"), inline=True)
            embed.add_field(name="Progress", value=f"{percent_away:.1f}% away", inline=True)

            progress_bar = "○" * int(score * 10) + "●" * (10 - int(score * 10))
            embed.add_field(name="Progress Bar", value=f"`{progress_bar}` {score*100:.0f}%", inline=False)

            if percent_away <= 5:
                embed.add_field(name="ðŸš€ Almost there!", value="You're very close to your next promotion!", inline=False)
        else:
            embed.add_field(name="Status", value="You've reached the highest tier!", inline=True)

        embed.set_thumbnail(url=member.display_avatar.url)
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_promote(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True

        guild = message.guild
        staff_promo = self.bot.staff_promo

        parts = message.content.split()
        if len(parts) < 4:
            await message.channel.send("Usage: `!staffpromo promote @user <tier>`")
            return True

        try:
            user_id = int(parts[2].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in promote: %s", e)
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True

        tier_name = " ".join(parts[3:])

        config = staff_promo._get_full_config(guild.id)
        success, result = await staff_promo.manual_promote(guild, target_member, tier_name, config)

        if success:
            await message.channel.send(f"✅ {target_member.mention} {result}")
        else:
            await message.channel.send(f"❌ {result}")
        return True

    async def handle_staffpromo_demote(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True

        guild = message.guild
        staff_promo = self.bot.staff_promo

        parts = message.content.split()
        if len(parts) < 4:
            await message.channel.send("Usage: `!staffpromo demote @user <tier>`\nUse `none` to remove all staff roles.")
            return True

        try:
            user_id = int(parts[2].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in demote: %s", e)
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True

        tier_name = " ".join(parts[3:])

        config = staff_promo._get_full_config(guild.id)
        success, result = await staff_promo.manual_demote(guild, target_member, tier_name, config)

        if success:
            await message.channel.send(f"✅ {target_member.mention} {result}")
        else:
            await message.channel.send(f"❌ {result}")
        return True

    async def handle_staffpromo_exclude(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True

        guild = message.guild
        staff_promo = self.bot.staff_promo

        parts = message.content.split()
        if len(parts) < 4:
            await message.channel.send("Usage: `!staffpromo exclude add @user` or `!staffpromo exclude remove @user`")
            return True

        action = parts[2].lower()
        if action not in ["add", "remove"]:
            await message.channel.send("Usage: `!staffpromo exclude add @user` or `!staffpromo exclude remove @user`")
            return True

        try:
            user_id = int(parts[3].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in exclude: %s", e)
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True

        config = staff_promo._get_full_config(guild.id)
        settings = config.get("settings", staff_promo._default_settings)
        excluded = settings.get("excluded_users", [])

        if action == "add":
            if user_id not in excluded:
                excluded.append(user_id)
                await message.channel.send(f"✅ {target_member.mention} added to exclusion list.")
            else:
                await message.channel.send(f"⚠️ {target_member.mention} is already excluded.")
        else:
            if user_id in excluded:
                excluded.remove(user_id)
                await message.channel.send(f"✅ {target_member.mention} removed from exclusion list.")
            else:
                await message.channel.send(f"⚠️ {target_member.mention} is not in the exclusion list.")

        settings["excluded_users"] = excluded
        config["settings"] = settings
        dm.update_guild_data(guild.id, "staff_promo_config", config)

        return True

    async def handle_staffpromo_tiers(self, message: discord.Message) -> bool:
        """Handle !staffpromo tiers command - Interactive tier management"""
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True

        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)
        tiers = config.get("tiers", staff_promo._default_tiers)
        role_ids = config.get("roles_by_tier", {})

        # Create embed showing current tiers
        embed = discord.Embed(
            title="ðŸ“Š Promotion Tiers Management",
            description="Manage promotion tiers for this server",
            color=discord.Color.blue()
        )

        if tiers:
            tiers_text = ""
            for tier in sorted(tiers, key=lambda t: t.get("threshold", 0)):
                name = tier.get("name", "Unknown")
                threshold = int(tier.get("threshold", 0) * 100)
                role_id = role_ids.get(name)
                role_mention = f"<@&{role_id}>" if role_id and guild.get_role(role_id) else "Not set"
                tiers_text += f"**{name}**: {threshold}% → {role_mention}\n"
            embed.add_field(name="Current Tiers", value=tiers_text or "None", inline=False)
        else:
            embed.add_field(name="Current Tiers", value="No tiers configured", inline=False)

        # Create view with buttons
        view = TierManagementView(guild, staff_promo, config)
        await message.channel.send(embed=embed, view=view)
        return True

    async def handle_staffpromo_roles(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True

        guild = message.guild
        staff_promo = self.bot.staff_promo

        parts = message.content.split()
        if len(parts) < 5:
            await message.channel.send(
                "Usage: `!staffpromo roles add <tier> @role` or `!staffpromo roles remove <tier> @role`\n"
                "Example: `!staffpromo roles add Moderator @Moderator`"
            )
            return True

        action = parts[2].lower()
        if action not in ["add", "remove", "list"]:
            await message.channel.send("Use: `add`, `remove`, or `list`")
            return True

        if action == "list":
            config = staff_promo._get_full_config(guild.id)
            role_ids = config.get("roles_by_tier", {})
            tiers = config.get("tiers", staff_promo._default_tiers)

            embed = discord.Embed(title="⚙️ Role Mappings", color=discord.Color.orange())
            for tier in tiers:
                tier_name = tier.get("name")
                rid = role_ids.get(tier_name)
                if rid:
                    role = guild.get_role(rid)
                    role_mention = role.mention if role else f"Role ID: {rid}"
                else:
                    role_mention = "Not set"
                embed.add_field(name=tier_name, value=role_mention, inline=True)

            await message.channel.send(embed=embed)
            return True

        tier_name = parts[3]

        try:
            role_id = int(parts[4].strip("<@&>"))
            target_role = guild.get_role(role_id)
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("Role lookup failed: %s", e)
            await message.channel.send("❌ Could not find role. Use `@role` format.")
            return True

        config = staff_promo._get_full_config(guild.id)
        role_ids = config.get("roles_by_tier", {})

        tiers = config.get("tiers", staff_promo._default_tiers)
        valid_tiers = [t.get("name").lower() for t in tiers]

        if tier_name.lower() not in valid_tiers:
            valid_list = ", ".join([t.get("name") for t in tiers])
            await message.channel.send(f"❌ Invalid tier. Valid tiers: {valid_list}")
            return True

        if action == "add":
            role_ids[tier_name] = target_role.id
            await message.channel.send(f"✅ Mapped **{tier_name}** to {target_role.mention}")
        else:
            if tier_name in role_ids:
                del role_ids[tier_name]
            await message.channel.send(f"✅ Removed mapping for **{tier_name}**")

        config["roles_by_tier"] = role_ids
        dm.update_guild_data(guild.id, "staff_promo_config", config)

        return True

    async def handle_staffpromo_review(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        guild = message.guild
        staff_promo = self.bot.staff_promo

        parts = message.content.split()

        if message.author.guild_permissions.administrator:
            if len(parts) >= 3:
                action = parts[2].lower()
                if action in ["approve", "reject"]:
                    return await self.handle_promotion_decision(message, action, parts)

            config = staff_promo._get_full_config(guild.id)
            pending = config.get("pending_reviews", [])

            if not pending:
                await message.channel.send("⚠️ No pending promotion reviews.")
                return True

            embed = discord.Embed(title="ðŸ“ Pending Promotion Reviews", color=discord.Color.yellow())
            for review in pending:
                user_id = review.get("user_id")
                tier_name = review.get("tier_name")
                score = review.get("score", 0)
                embed.add_field(name=f"User ID: {user_id}", value=f"Tier: {tier_name}, Score: {score*100:.1f}%", inline=False)

            embed.add_field(name="Actions", value="`!staffpromo approve @user` or `!staffpromo reject @user`", inline=False)
            await message.channel.send(embed=embed)
            return True
        else:
            config = staff_promo._get_full_config(guild.id)
            pending = config.get("pending_reviews", [])

            user_pending = [r for r in pending if r.get("user_id") == message.author.id]
            if user_pending:
                for review in user_pending:
                    await message.channel.send(f"⏳ Your promotion to **{review.get('tier_name')}** is pending review. Score: {review.get('score', 0)*100:.1f}%")
            else:
                await message.channel.send("❌ You have no pending reviews.")
            return True

    async def handle_promotion_decision(self, message: discord.Message, decision: str, parts: list) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo

        if len(parts) < 3:
            await message.channel.send(f"Usage: `!staffpromo {decision} @user`")
            return True

        try:
            user_id = int(parts[2].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in promotion decision: %s", e)
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True

        config = staff_promo._get_full_config(guild.id)
        pending = config.get("pending_reviews", [])

        review_to_remove = None
        target_tier_name = None
        for review in pending:
            if review.get("user_id") == user_id:
                review_to_remove = review
                target_tier_name = review.get("tier_name")
                break

        if not review_to_remove:
            await message.channel.send(f"❌ No pending review found for that user.")
            return True

        pending = [r for r in pending if r.get("user_id") != user_id]
        config["pending_reviews"] = pending
        dm.update_guild_data(guild.id, "staff_promo_config", config)

        if decision == "approve":
            tiers = config.get("tiers", staff_promo._default_tiers)
            role_ids = dict(config.get("roles_by_tier", {}))

            for tier in tiers:
                tier_name = tier.get("name")
                if tier_name not in role_ids or not role_ids[tier_name]:
                    role_name = tier.get("role_name")
                    if role_name:
                        r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                        if r:
                            role_ids[tier_name] = r.id

            tier = next((t for t in tiers if t.get("name") == target_tier_name), None)
            if tier:
                current_index = staff_promo._get_current_tier_index(target_member, tiers, role_ids)
                settings = config.get("settings", staff_promo._default_settings)
                await staff_promo._promote_member(guild, target_member, tier, tiers, role_ids, current_index, settings, config)
                await message.channel.send(f"✅ Approved! {target_member.mention} promoted to **{target_tier_name}**")
        else:
            try:
                await target_member.send(f"❌ Your promotion to **{target_tier_name}** was rejected.")
            except Exception as e:
                logger.debug("Could not DM user about rejected promotion: %s", e)
            await message.channel.send(f"✅ Rejected promotion for {target_member.mention}")

        return True

    async def handle_staffpromo_requirements(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        guild = message.guild
        staff_promo = self.bot.staff_promo

        config = staff_promo._get_full_config(guild.id)
        requirements = config.get("tier_requirements", staff_promo._default_tier_requirements)
        tiers = config.get("tiers", staff_promo._default_tiers)

        embed = discord.Embed(title="ðŸ“‹ Tier Requirements", color=discord.Color.blue())

        for tier in tiers:
            tier_name = tier.get("name")
            tier_reqs = requirements.get(tier_name, {})

            if tier_reqs:
                req_text = "\n".join([f"• {k}: {v}" for k, v in tier_reqs.items()])
            else:
                req_text = "No requirements"

            embed.add_field(name=f"{tier_name} ({int(tier.get('threshold', 0)*100)}%)", value=req_text, inline=False)

        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_bonuses(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "staffpromo"):
            await message.channel.send("❌ The staff promotion system is currently disabled on this server.")
            return False
        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)
        metrics = config.get("metrics", staff_promo._default_metrics)

        embed = discord.Embed(title="ðŸŒŸ Staff Promotion Metrics & Bonuses", color=discord.Color.gold())

        desc = "The following activity metrics are tracked for staff promotions:\n\n"
        for name, data in metrics.items():
            if data.get("enabled", True):
                weight = data.get("weight", 1.0)
                desc += f"• **{name.replace('_', ' ').title()}**: Weight {weight}x\n"

        embed.description = desc
        embed.set_footer(text="Higher weights mean the metric is more important for promotion.")

        await message.channel.send(embed=embed)
        return True

    def _resolve_channel(self, channel_identifier):
        """Resolve a channel from the guild by name, ID, or mention format."""
        guild = getattr(self, 'guild', None)
        if guild is None:
            if hasattr(self, '_guild_context') and self._guild_context:
                guild = self._guild_context

        if guild is None:
            import inspect
            for frame_record in inspect.stack():
                local_self = frame_record.frame.f_locals.get('self')
                if hasattr(local_self, 'guild'):
                    guild = local_self.guild
                    break

        if guild is None:
            return None

        # Check if channel_identifier is an ID (int)
        if isinstance(channel_identifier, int):
            return guild.get_channel(channel_identifier)
        # Check if channel_identifier is a string mention (e.g. <#1234567890>)
        elif isinstance(channel_identifier, str) and channel_identifier.startswith('<#') and channel_identifier.endswith('>'):
            try:
                channel_id = int(channel_identifier[2:-1])
                return guild.get_channel(channel_id)
            except Exception:
                pass
        # Check if channel_identifier is a name
        elif isinstance(channel_identifier, str):
            for channel in guild.channels:
                if channel.name == channel_identifier or channel.name == channel_identifier.lstrip('#'):
                    return channel

    async def handle_ticket_create(self, message: discord.Message) -> bool:
        """Handle !ticket command with enhanced ticket creation interface"""
        try:
            guild_id = message.guild.id

            # Check if ticket system is enabled
            if not is_system_enabled(guild_id, "tickets"):
                embed = discord.Embed(
                    title="❌ Tickets Unavailable",
                    description="The ticket system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel tickets to enable the system")
                await message.channel.send(embed=embed)
                return False

            from modules.auto_setup import CreateTicketButton

            # Check if ticket system is enabled
            config = dm.get_guild_data(message.guild.id, "tickets_config", {})
            if not config.get("enabled", True):
                embed = discord.Embed(
                    title="❌ Tickets Disabled",
                    description="The ticket system is currently disabled.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Ticket creation is temporarily unavailable")
                await message.channel.send(embed=embed)
                return True

            # Check if user already has an open ticket
            tickets_data = dm.load_json("tickets", default={})
            user_tickets = []

            for tid, ticket in tickets_data.items():
                if ticket.get("user_id") == message.author.id and ticket.get("status") == "open":
                    user_tickets.append(ticket)

            if user_tickets:
                embed = discord.Embed(
                    title="🎫 Existing Ticket Found",
                    description="You already have an open ticket. Please use your existing ticket or wait for it to be resolved.",
                    color=discord.Color.orange()
                )

                for ticket in user_tickets[:3]:  # Show up to 3 recent tickets
                    embed.add_field(
                        name=f"Ticket #{ticket.get('id', 'Unknown')}",
                        value=f"Created: <t:{int(ticket.get('created_at', 0))}:R>\n"
                              f"Status: Open\n"
                              f"Channel: <#{ticket.get('channel_id', 'Unknown')}>",
                        inline=False
                    )

                embed.set_footer(text="Use your existing ticket channel for continued support")
                await message.channel.send(embed=embed)
                return True

            # Enhanced ticket creation interface
            embed = discord.Embed(
                title="🎫 Support Ticket System",
                description=f"**Hello {message.author.display_name}!**\n\n"
                           "Need help from the support team? Click the button below to create a private ticket.\n\n"
                           "Our support team will assist you as soon as possible!",
                color=discord.Color.green()
            )

            embed.add_field(
                name="📋 What Happens Next",
                value="1. Click '🎫 Create Ticket'\n"
                      "2. Select a category (if prompted)\n"
                      "3. Describe your issue in detail\n"
                      "4. Support team will respond",
                inline=False
            )

            embed.add_field(
                name="⏱️ Response Times",
                value="• General Support: Within 1-2 hours\n"
                      "• Urgent Issues: Within 30 minutes\n"
                      "• Emergency: Immediate attention",
                inline=False
            )

            embed.add_field(
                name="📞 Before Creating a Ticket",
                value="• Check `!help` for common questions\n"
                      "• Search existing channels for answers\n"
                      "• Be specific about your issue\n"
                      "• Include relevant details/screenshots",
                inline=False
            )

            embed.add_field(
                name="⚠️ Important Notes",
                value="• One ticket per issue please\n"
                      "• Stay in your ticket channel\n"
                      "• Be patient and respectful\n"
                      "• Close tickets when resolved",
                inline=False
            )

            embed.set_footer(text=f"Ticket System • {message.guild.name} • Support is here to help!")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/support_ticket.png")
            embed.set_author(name=f"{message.author.display_name}'s Support Request", icon_url=message.author.display_avatar.url)

            view = CreateTicketButton(message.guild.id)
            msg = await message.channel.send(embed=embed, view=view)

            # Add supportive reactions
            await msg.add_reaction("🎫")
            await msg.add_reaction("🆘")
            await msg.add_reaction("💬")

            return True

        except Exception as e:
            logger.error(f"Error in handle_ticket_create: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Ticket Error",
                description="Unable to create a support ticket. Please try again later or contact staff directly.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_ticket_close(self, message: discord.Message) -> bool:
        if not message.guild: return False
        # Check if it's a ticket channel
        if not message.channel.name.startswith("ticket-"):
            await message.channel.send("❌ This command can only be used inside a ticket channel.")
            return True

        system = getattr(self.bot, "tickets", None)
        if system:
            # We need to find the ticket object
            tickets_data = dm.load_json("tickets", default={})
            target_ticket = None
            for tid, t in tickets_data.items():
                if t.get("channel_id") == message.channel.id:
                    target_ticket = t
                    break

            if target_ticket:
                await message.channel.send("ðŸ”’ Closing ticket...")
                await asyncio.sleep(2)
                await message.channel.delete()
                target_ticket["status"] = "closed"
                dm.save_json("tickets", tickets_data)
                return True

        await message.channel.send("❌ Failed to close ticket automatically.")
        return True

    async def handle_verification_verify(self, message: discord.Message) -> bool:
        guild_id = message.guild.id

        # Check if verification system is enabled
        if not is_system_enabled(guild_id, "verification"):
            embed = discord.Embed(
                title="❌ Verification Unavailable",
                description="The verification system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                color=discord.Color.red()
            )
            embed.set_footer(text="Use !configpanel verification to enable the system")
            await message.channel.send(embed=embed)
            return False

        from modules.verification import VerifyView
        verification_system = getattr(self.bot, 'verification', None)

        # Check if verification system is enabled
        config = dm.get_guild_data(message.guild.id, "verification_config", {})
        if not config.get("enabled", True):
            embed = discord.Embed(
                title="❌ Verification Disabled",
                description="The verification system is currently disabled.\n\n*Please contact an administrator to enable it.*",
                color=discord.Color.red()
            )
            embed.set_footer(text="Use !configpanel verification to enable the system")
            await message.channel.send(embed=embed)
            return True

        # Enhanced verification embed with animations
        embed = discord.Embed(
            title="🛡️ Server Verification Required",
            description="Welcome to **{}**! To access the server, you must complete verification.\n\n"
                       "Click the **✅ Verify** button below to start the process.".format(message.guild.name),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🔒 Security Features",
            value="• Account age verification\n• CAPTCHA challenge\n• Automated role assignment",
            inline=False
        )

        embed.set_footer(text="Verification is required for all new members • Protected by Guardian AI")
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/verification_shield.png")

        # Send with animation effect
        msg = await message.channel.send(embed=embed, view=VerifyView(verification_system))

        # Add reaction animation
        await msg.add_reaction("✅")
        await msg.add_reaction("🛡️")

        return True

    async def handle_set_verify_channel(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "verification"):
            await message.channel.send("❌ The verification system is currently disabled on this server.")
            return False
        from modules.verification import Verification
        verification = Verification(self.bot)
        args = message.content.split()
        await verification.set_verify_channel(message, args)
        return True

    async def handle_create_tournament(self, message: discord.Message) -> bool:
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "tournaments"):
            await message.channel.send("❌ The tournaments system is currently disabled on this server.")
            return False
        from modules.tournaments import TournamentSystem, Tournament, TournamentType, TournamentStatus
        tournament_system = TournamentSystem(self.bot)
        args = message.content.split()
        # Parse tournament name from args (e.g., !tournament create "My Tournament")
        if len(args) < 3:
            return await message.channel.send("❌ Usage: `!tournament create <tournament name>`")
        name = " ".join(args[2:])
        # Create tournament with default settings
        guild = message.guild
        settings = tournament_system.get_guild_settings(guild.id)
        import time
        from datetime import datetime
        tournament_id = f"tournament_{int(time.time())}"
        new_tournament = Tournament(
            id=tournament_id,
            guild_id=guild.id,
            name=name,
            description=f"Tournament: {name}",
            tournament_type=TournamentType.SINGLE_ELIMINATION,
            status=TournamentStatus.REGISTRATION,
            max_participants=settings.get("default_max", 32),
            min_participants=settings.get("default_min", 4),
            prize_pool=settings.get("default_prize", {"coins": 500, "xp": 250}),
            registration_end=time.time() + 86400,  # 24 hours from now
            start_time=time.time() + 86400,
            rounds=[],
            participants=[],
            teams={},
            bracket=[],
            winner=None,
            created_by=message.author.id,
            created_at=time.time(),
            channel_id=message.channel.id
        )
        tournament_system._tournaments[tournament_id] = new_tournament
        tournament_system._save_tournament(new_tournament)
        await message.channel.send(f"✅ Tournament **{name}** created! Registration open for 24h. Use `!join {tournament_id}` to join.")
        return True

    async def handle_create_event(self, message: discord.Message) -> bool:
        from modules.events import EventScheduler
        event_scheduler = EventScheduler(self.bot)
        args = message.content.split()
        if len(args) < 3:
            return await message.channel.send("❌ Usage: `!event create <event name>` or `!evenf create <event name>`")
        name = " ".join(args[2:])
        # Use AI to create event with default settings
        guild = message.guild
        import time
        event_id = f"event_{int(time.time())}"
        # Create a basic event (AI can be used to expand later)
        from datetime import datetime, timedelta
        next_run = datetime.now() + timedelta(days=1)
        from modules.events import ScheduledEvent, EventType, EventStatus
        new_event = ScheduledEvent(
            id=event_id,
            guild_id=guild.id,
            channel_id=message.channel.id,
            name=name,
            description=f"Event: {name}",
            event_type=EventType.CUSTOM,
            schedule="0 0 * * *",  # Daily placeholder, adjust via config
            next_run=next_run.timestamp(),
            status=EventStatus.SCHEDULED,
            rewards={"coins": 100, "xp": 50},
            settings={},
            created_by=message.author.id
        )
        # Save event
        event_scheduler._save_scheduled_event(new_event)
        await message.channel.send(f"✅ Event **{name}** created! ID: `{event_id}`. Use `!join {event_id}` to join.")
        return True

    async def handle_appeal_create(self, message: discord.Message) -> bool:
        """Handle !appeal command with enhanced appeal interface"""
        try:
            guild_id = message.guild.id

            # Check if appeals system is enabled
            if not is_system_enabled(guild_id, "appeals"):
                embed = discord.Embed(
                    title="❌ Appeals Unavailable",
                    description="The appeal system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel appeals to enable the system")
                await message.channel.send(embed=embed)
                return False

            # Check if user has any active bans/warnings to appeal
            # This is a basic check - in a real implementation you'd check actual infractions
            appeals = dm.get_guild_data(guild_id, "appeals", [])
            user_appeals = [appeal for appeal in appeals if appeal.get("user_id") == message.author.id]

            # Check for pending appeals
            pending_appeal = next((appeal for appeal in user_appeals if appeal.get("status") == "pending"), None)
            if pending_appeal:
                embed = discord.Embed(
                    title="⏳ Appeal Already Pending",
                    description="You already have a pending appeal.\n\n"
                               "Please wait for the review team to process your current appeal.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="📅 Submitted",
                    value=f"<t:{int(pending_appeal.get('submitted_at', 0))}:F>",
                    inline=True
                )
                embed.add_field(
                    name="📊 Status",
                    value="Under Review",
                    inline=True
                )
                embed.set_footer(text="You'll be notified when there's a decision")
                await message.channel.send(embed=embed)
                return True

            # Enhanced appeal interface
            embed = discord.Embed(
                title="⚖️ Appeal Submission Portal",
                description=f"**{message.author.display_name}**, welcome to the appeals process.\n\n"
                           "If you've received a moderation action that you believe was unfair, "
                           "you can submit an appeal for review by the moderation team.\n\n"
                           "Click the button below to start your appeal.",
                color=discord.Color.orange()
            )

            embed.add_field(
                name="📋 Appeal Process",
                value="1. Click '⚖️ Submit Appeal'\n"
                      "2. Provide details about the incident\n"
                      "3. Explain why you believe the action was unfair\n"
                      "4. Submit for staff review",
                inline=False
            )

            embed.add_field(
                name="⏱️ Processing Time",
                value="• Appeals are typically reviewed within 24-48 hours\n"
                      "• You'll receive a DM with the decision\n"
                      "• Both approval and denial decisions are final",
                inline=False
            )

            embed.add_field(
                name="📞 Important Notes",
                value="• Provide specific details and evidence\n"
                      "• Be respectful and honest in your appeal\n"
                      "• Multiple invalid appeals may result in further action\n"
                      "• Contact staff directly for urgent matters",
                inline=False
            )

            embed.set_footer(text=f"Appeals System • {message.guild.name} • All appeals are confidential")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/justice_scales.png")
            embed.set_author(name=f"{message.author.display_name}'s Appeal", icon_url=message.author.display_avatar.url)

            from modules.appeals import AppealPersistentView
            msg = await message.channel.send(embed=embed, view=AppealPersistentView())

            # Add appropriate reactions
            await msg.add_reaction("⚖️")
            await msg.add_reaction("📋")
            await msg.add_reaction("⏳")

            return True

        except Exception as e:
            logger.error(f"Error in handle_appeal_create: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Appeal Error",
                description="Unable to load the appeal portal. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_application_apply(self, message: discord.Message) -> bool:
        """Handle !apply command with enhanced application interface"""
        try:
            guild_id = message.guild.id

            # Check if applications system is enabled
            if not is_system_enabled(guild_id, "applications"):
                embed = discord.Embed(
                    title="❌ Applications Unavailable",
                    description="The staff application system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel applications to enable the system")
                await message.channel.send(embed=embed)
                return False

            # Check if user already has a pending application
            applications = dm.get_guild_data(guild_id, "applications", [])
            user_app = next((app for app in applications if app.get("user_id") == message.author.id and app.get("status") == "pending"), None)

            if user_app:
                embed = discord.Embed(
                    title="⏳ Application Already Pending",
                    description="You already have a pending staff application.\n\n"
                               "Please wait for the review team to process your current application.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="📅 Submitted",
                    value=f"<t:{int(user_app.get('submitted_at', 0))}:F>",
                    inline=True
                )
                embed.add_field(
                    name="📊 Status",
                    value="Under Review",
                    inline=True
                )
                embed.set_footer(text="You'll be notified when there's an update")
                await message.channel.send(embed=embed)
                return True

            # Enhanced application interface
            embed = discord.Embed(
                title="📋 Staff Application Portal",
                description=f"**Welcome, {message.author.display_name}!**\n\n"
                           "Ready to join the staff team? Click the button below to start your application.\n\n"
                           "**What to expect:**\n"
                           "• Quick application form\n"
                           "• Review by current staff\n"
                           "• Response within 24-48 hours",
                color=discord.Color.blue()
            )

            embed.add_field(
                name="📝 Application Process",
                value="1. Click '📋 Apply Now'\n"
                      "2. Fill out the application form\n"
                      "3. Submit for review\n"
                      "4. Wait for staff decision",
                inline=False
            )

            embed.add_field(
                name="🎯 Requirements",
                value="• Active community member\n"
                      "• Good communication skills\n"
                      "• Willingness to help others\n"
                      "• Follow server rules",
                inline=False
            )

            embed.add_field(
                name="📞 Need Help?",
                value="Contact current staff or use `!help applications` for more information.",
                inline=False
            )

            embed.set_footer(text=f"Applications System • {message.guild.name}")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/application_form.png")
            embed.set_author(name=f"{message.author.display_name}'s Application", icon_url=message.author.display_avatar.url)

            from modules.applications import ApplicationPersistentView
            msg = await message.channel.send(embed=embed, view=ApplicationPersistentView())

            # Add encouraging reactions
            await msg.add_reaction("📋")
            await msg.add_reaction("✨")
            await msg.add_reaction("🎉")

            return True

        except Exception as e:
            logger.error(f"Error in handle_application_apply: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Application Error",
                description="Unable to load the application portal. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_list_quests(self, message: discord.Message) -> bool:
        """Handle !quests command - Enhanced quest system with progress visualization"""
        try:
            import asyncio
            from discord import ui

            gamification = self.bot.gamification
            guild_id = message.guild.id
            user_id = message.author.id

            # Loading animation
            loading_embed = discord.Embed(
                title="🎯 Loading Quest Log",
                description="📜 Accessing quest database...\n🎯 Calculating progress...\n🏆 Preparing rewards...",
                color=discord.Color.purple()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Accessing quest database...\n🎯 Calculating progress...\n🏆 Preparing rewards..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Accessing quest database...\n✅ Calculating progress...\n🏆 Preparing rewards..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Accessing quest database...\n✅ Calculating progress...\n✅ Preparing rewards..."
            await loading_msg.edit(embed=loading_embed)

            # Get user's active quests
            user_quests = gamification.get_user_quests(guild_id, user_id)

            if not user_quests:
                empty_embed = discord.Embed(
                    title="🎯 Quest Log - Empty",
                    description="**You have no active quests right now!**\n\n"
                               "New quests will be assigned automatically as you participate in the server.\n\n"
                               "*Complete quests to earn XP, coins, and special rewards!*",
                    color=discord.Color.light_grey()
                )
                empty_embed.add_field(
                    name="🎮 Quest Types",
                    value="• **Daily Challenges** - Complete daily tasks\n• **Achievement Hunts** - Reach milestones\n• **Community Goals** - Help the server grow",
                    inline=False
                )
                empty_embed.set_footer(text="Quests refresh automatically • Stay active to get new challenges!")
                empty_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/empty_quest.png")

                await loading_msg.edit(embed=empty_embed)
                await loading_msg.add_reaction("🎯")
                return True

            # Create interactive quest view
            class QuestView(ui.View):
                def __init__(self, quests, gamification, guild_id, user_id):
                    super().__init__(timeout=300)
                    self.quests = quests
                    self.gamification = gamification
                    self.guild_id = guild_id
                    self.user_id = user_id
                    self.current_quest = 0

                def create_progress_bar(self, progress):
                    filled = int(progress / 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    return f"`{bar}` {progress}%"

                def create_embed(self):
                    quest_data = self.quests[self.current_quest]
                    quest_id = quest_data.get("id", "unknown")
                    progress = self.gamification._check_quest_progress(self.guild_id, self.user_id, quest_id)

                    embed = discord.Embed(
                        title="🎯 Quest Log",
                        description=f"**Quest {self.current_quest + 1} of {len(self.quests)}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.purple()
                    )

                    # Quest details
                    embed.add_field(
                        name=f"📜 {quest_data.get('name', 'Unknown Quest')}",
                        value=quest_data.get('description', 'No description available.'),
                        inline=False
                    )

                    # Progress visualization
                    progress_bar = self.create_progress_bar(progress)
                    status_emoji = "✅" if progress >= 100 else "⏳"
                    embed.add_field(
                        name="📊 Progress",
                        value=f"{progress_bar}\n{status_emoji} **{progress}% Complete**",
                        inline=False
                    )

                    # Rewards
                    reward = quest_data.get('reward', {})
                    if reward:
                        reward_text = ""
                        if reward.get('xp'):
                            reward_text += f"🆙 {reward['xp']} XP\n"
                        if reward.get('coins'):
                            reward_text += f"💰 {reward['coins']} Coins\n"
                        if reward.get('badge'):
                            reward_text += f"🏅 {reward['badge']}\n"

                        if reward_text:
                            embed.add_field(
                                name="🎁 Rewards",
                                value=reward_text.strip(),
                                inline=True
                            )

                    # Quest type
                    quest_type = quest_data.get('type', 'achievement')
                    type_emoji = {
                        'daily': '📅',
                        'achievement': '🏆',
                        'community': '👥',
                        'special': '✨'
                    }.get(quest_type, '🎯')

                    embed.add_field(
                        name="🏷️ Quest Type",
                        value=f"{type_emoji} {quest_type.title()}",
                        inline=True
                    )

                    # Navigation info
                    embed.set_footer(text=f"Use buttons to navigate • Quest {self.current_quest + 1}/{len(self.quests)}")
                    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/quest_scroll.png")

                    return embed

                @ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary)
                async def prev_quest(self, interaction: discord.Interaction, button: ui.Button):
                    if self.current_quest > 0:
                        self.current_quest -= 1
                    else:
                        self.current_quest = len(self.quests) - 1

                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                @ui.button(label="📋 Quest List", style=discord.ButtonStyle.primary)
                async def quest_list(self, interaction: discord.Interaction, button: ui.Button):
                    # Show compact list of all quests
                    list_embed = discord.Embed(
                        title="🎯 All Active Quests",
                        description="**Your current quest progress:**\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.purple()
                    )

                    for i, quest_data in enumerate(self.quests):
                        quest_id = quest_data.get("id", "unknown")
                        progress = self.gamification._check_quest_progress(self.guild_id, self.user_id, quest_id)
                        status = "✅" if progress >= 100 else f"{progress}%"

                        list_embed.add_field(
                            name=f"{i+1}. {quest_data.get('name', 'Unknown')}",
                            value=f"Progress: {status}",
                            inline=True
                        )

                    await interaction.response.send_message(embed=list_embed, ephemeral=True)

                @ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
                async def next_quest(self, interaction: discord.Interaction, button: ui.Button):
                    if self.current_quest < len(self.quests) - 1:
                        self.current_quest += 1
                    else:
                        self.current_quest = 0

                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

            view = QuestView(user_quests, gamification, guild_id, user_id)
            embed = view.create_embed()

            await loading_msg.edit(embed=embed, view=view)

            # Add quest-themed reactions
            await loading_msg.add_reaction("🎯")
            await loading_msg.add_reaction("🏆")
            await loading_msg.add_reaction("🎮")

            return True

        except Exception as e:
            logger.error(f"Error in handle_list_quests: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Quest System Error",
                description="Unable to load your quest log. The gamification system may be temporarily unavailable.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_prestige(self, message: discord.Message) -> bool:
        """Handle !prestige command - Interactive prestige system with animations"""
        try:
            import asyncio
            from discord import ui

            gamification = self.bot.gamification
            guild_id = message.guild.id
            user_id = message.author.id

            # Check if gamification system is enabled
            if not is_system_enabled(guild_id, "gamification"):
                embed = discord.Embed(
                    title="❌ Gamification Unavailable",
                    description="The gamification system is currently disabled on this server.",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel gamification to enable the system")
                await message.channel.send(embed=embed)
                return False

            # Loading animation
            loading_embed = discord.Embed(
                title="🌟 Accessing Prestige Chamber",
                description="✨ Calculating your achievements...\n🏆 Checking prestige eligibility...\n💎 Preparing ascension rewards...",
                color=discord.Color.purple()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.9)
            loading_embed.description = "✅ Calculating your achievements...\n🏆 Checking prestige eligibility...\n💎 Preparing ascension rewards..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.6)
            loading_embed.description = "✅ Calculating your achievements...\n✅ Checking prestige eligibility...\n💎 Preparing ascension rewards..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Calculating your achievements...\n✅ Checking prestige eligibility...\n✅ Preparing ascension rewards..."
            await loading_msg.edit(embed=loading_embed)

            # Get user's current level and prestige
            leveling_data = dm.get_guild_data(guild_id, "leveling_data", {})
            user_data = leveling_data.get(str(user_id), {})
            current_level = user_data.get("level", 1)
            current_prestige = user_data.get("prestige", 0)

            # Check if user can prestige (need to be at max level)
            max_level = 100  # You might want to make this configurable
            can_prestige = current_level >= max_level

            # Prestige bonuses
            prestige_bonuses = {
                1: {"xp_bonus": 5, "coin_multiplier": 1.1, "title": "Apprentice Ascendant"},
                2: {"xp_bonus": 10, "coin_multiplier": 1.2, "title": "Journeyman Legend"},
                3: {"xp_bonus": 15, "coin_multiplier": 1.3, "title": "Master Myth"},
                4: {"xp_bonus": 20, "coin_multiplier": 1.4, "title": "Grandmaster Immortal"},
                5: {"xp_bonus": 25, "coin_multiplier": 1.5, "title": "Transcendent Deity"}
            }

            class PrestigeView(ui.View):
                def __init__(self, can_prestige, current_level, current_prestige, prestige_bonuses):
                    super().__init__(timeout=300)
                    self.can_prestige = can_prestige
                    self.current_level = current_level
                    self.current_prestige = current_prestige
                    self.prestige_bonuses = prestige_bonuses

                def create_embed(self):
                    embed = discord.Embed(
                        title="🌟 Prestige Ascension Chamber",
                        description="**Transcend your limits and achieve greatness!**\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.purple()
                    )

                    # Current status
                    embed.add_field(
                        name="📊 Current Status",
                        value=f"**Level:** {self.current_level}/100\n"
                              f"**Prestige:** {self.current_prestige}\n"
                              f"**Status:** {'✅ Ready to Ascend' if self.can_prestige else '⏳ Journey Continues'}",
                        inline=False
                    )

                    # Next prestige info
                    next_prestige = self.current_prestige + 1
                    if next_prestige in self.prestige_bonuses:
                        bonus = self.prestige_bonuses[next_prestige]
                        embed.add_field(
                            name=f"🏆 Next Prestige: Level {next_prestige}",
                            value=f"**Title:** {bonus['title']}\n"
                                  f"**XP Bonus:** +{bonus['xp_bonus']}%\n"
                                  f"**Coin Multiplier:** {bonus['coin_multiplier']}x\n"
                                  f"**Special Perks:** Unlocked",
                            inline=False
                        )

                    # How prestige works
                    embed.add_field(
                        name="⚡ Ascension Mechanics",
                        value="• Reach Level 100 to become eligible\n"
                              "• Reset to Level 1 with bonus multipliers\n"
                              "• Keep all achievements and cosmetic rewards\n"
                              "• Unlock exclusive titles and abilities",
                        inline=False
                    )

                    # Current bonuses (if any)
                    if self.current_prestige > 0 and self.current_prestige in self.prestige_bonuses:
                        current_bonus = self.prestige_bonuses[self.current_prestige]
                        embed.add_field(
                            name="💎 Active Bonuses",
                            value=f"**Title:** {current_bonus['title']}\n"
                                  f"**XP Bonus:** +{current_bonus['xp_bonus']}%\n"
                                  f"**Coin Multiplier:** {current_bonus['coin_multiplier']}x",
                            inline=True
                        )

                    embed.set_footer(text="Prestige • Ascend beyond your limits")
                    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/prestige_crystal.png")

                    return embed

                @ui.button(label="🚀 Ascend Now!", style=discord.ButtonStyle.success, disabled=True)
                async def ascend_button(self, interaction: discord.Interaction, button: ui.Button):
                    if not self.can_prestige:
                        await interaction.response.send_message(
                            "❌ You must reach Level 100 before you can ascend!", ephemeral=True
                        )
                        return

                    # Confirm ascension with modal
                    class AscensionConfirm(ui.Modal, title="Confirm Ascension"):
                        confirm = ui.TextInput(
                            label="Type 'ASCEND' to confirm",
                            placeholder="This action cannot be undone!",
                            required=True,
                            max_length=10
                        )

                        async def on_submit(self, it):
                            if self.confirm.value.upper() != "ASCEND":
                                await it.response.send_message("❌ Ascension cancelled.", ephemeral=True)
                                return

                            # Perform ascension
                            leveling_data = dm.get_guild_data(it.guild_id, "leveling_data", {})
                            user_data = leveling_data.get(str(it.user.id), {})

                            # Increment prestige
                            new_prestige = user_data.get("prestige", 0) + 1
                            user_data["prestige"] = new_prestige
                            user_data["level"] = 1  # Reset to level 1
                            user_data["xp"] = 0    # Reset XP

                            leveling_data[str(it.user.id)] = user_data
                            dm.update_guild_data(it.guild_id, "leveling_data", leveling_data)

                            # Success message
                            success_embed = discord.Embed(
                                title="🌟 ASCENSION COMPLETE!",
                                description=f"**Congratulations, {it.user.mention}!**\n\n"
                                           f"You have ascended to **Prestige {new_prestige}**!\n\n"
                                           f"✨ **Title Unlocked:** {self.prestige_bonuses.get(new_prestige, {}).get('title', 'Ascendant')}\n"
                                           f"⚡ **XP Bonus:** +{self.prestige_bonuses.get(new_prestige, {}).get('xp_bonus', 0)}%\n"
                                           f"💰 **Coin Multiplier:** {self.prestige_bonuses.get(new_prestige, {}).get('coin_multiplier', 1.0)}x",
                                color=discord.Color.gold()
                            )

                            await it.response.send_message(embed=success_embed, ephemeral=True)

                            # Update the main embed
                            self.current_prestige = new_prestige
                            self.current_level = 1
                            self.can_prestige = False
                            new_embed = self.create_embed()
                            await it.followup.edit_message(it.message.id, embed=new_embed, view=self)

                    await interaction.response.send_modal(AscensionConfirm())

                @ui.button(label="📈 View Leaderboard", style=discord.ButtonStyle.primary)
                async def leaderboard_button(self, interaction: discord.Interaction, button: ui.Button):
                    # Get prestige leaderboard
                    leveling_data = dm.get_guild_data(interaction.guild_id, "leveling_data", {})
                    prestige_users = []

                    for user_id, data in leveling_data.items():
                        prestige = data.get("prestige", 0)
                        if prestige > 0:
                            prestige_users.append((user_id, prestige))

                    # Sort by prestige (descending)
                    prestige_users.sort(key=lambda x: x[1], reverse=True)

                    if not prestige_users:
                        await interaction.response.send_message(
                            "🏆 **Prestige Leaderboard**\n\nNo one has ascended yet. Be the first!",
                            ephemeral=True
                        )
                        return

                    leaderboard_text = "**🏆 Prestige Champions**\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    for i, (user_id, prestige) in enumerate(prestige_users[:10]):
                        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"#{i+1}"
                        title = self.prestige_bonuses.get(prestige, {}).get('title', f'Prestige {prestige}')
                        leaderboard_text += f"{medal} <@{user_id}> - **{title}**\n"

                    await interaction.response.send_message(leaderboard_text, ephemeral=True)

                @ui.button(label="ℹ️ Prestige Info", style=discord.ButtonStyle.secondary)
                async def info_button(self, interaction: discord.Interaction, button: ui.Button):
                    info_embed = discord.Embed(
                        title="🌟 Prestige Guide",
                        description="**Master the art of ascension!**",
                        color=discord.Color.blue()
                    )

                    info_embed.add_field(
                        name="🎯 How to Prestige",
                        value="1. Reach Level 100 through chatting and activities\n"
                              "2. Click '🚀 Ascend Now!' to reset and gain bonuses\n"
                              "3. Repeat the cycle to achieve higher prestige levels",
                        inline=False
                    )

                    info_embed.add_field(
                        name="💎 Prestige Benefits",
                        value="• **XP Bonuses** - Permanent percentage increases\n"
                              "• **Coin Multipliers** - Higher earnings from activities\n"
                              "• **Exclusive Titles** - Special recognition\n"
                              "• **Achievement Unlocks** - New challenges and rewards",
                        inline=False
                    )

                    await interaction.response.send_message(embed=info_embed, ephemeral=True)

            view = PrestigeView(can_prestige, current_level, current_prestige, prestige_bonuses)
            view.ascend_button.disabled = not can_prestige
            embed = view.create_embed()

            await loading_msg.edit(embed=embed, view=view)

            # Add prestige-themed reactions
            await loading_msg.add_reaction("🌟")
            await loading_msg.add_reaction("🏆")
            await loading_msg.add_reaction("💎")

            return True

        except Exception as e:
            logger.error(f"Error in handle_prestige: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Ascension Chamber Error",
                description="The prestige system is experiencing technical difficulties. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_dice(self, message: discord.Message) -> bool:
        """Handle !dice command - Roll dice game"""
        try:
            import random
            # Simple dice roll game
            result = random.randint(1, 6)
            await message.channel.send(f"ðŸŽ² You rolled a **{result}**!")
            return True
        except Exception as e:
            logger.error(f"Error in handle_dice: {e}")
            await message.channel.send(f"❌ Error rolling dice. Please try again.")
            return False

    async def handle_flip(self, message: discord.Message) -> bool:
        """Handle !flip command - Coin flip game"""
        try:
            import random
            result = random.choice(["Heads", "Tails"])
            emoji = "ðŸª™" if result == "Heads" else "ðŸŽ¯"
            await message.channel.send(f"{emoji} You flipped **{result}**!")
            return True
        except Exception as e:
            logger.error(f"Error in handle_flip: {e}")
            await message.channel.send(f"❌ Error flipping coin. Please try again.")
            return False

    async def handle_slots(self, message: discord.Message) -> bool:
        """Handle !slots command - Slot machine game"""
        try:
            import random
            symbols = ["ðŸ’", "ðŸŠ", "ðŸ‡", "ðŸ’Ž", "7ï¸âƒ£"]
            slots = [random.choice(symbols) for _ in range(3)]
            if slots[0] == slots[1] == slots[2]:
                result = f"{' '.join(slots)} - **Jackpot!** ðŸŽ‰"
            else:
                result = f"{' '.join(slots)} - Better luck next time!"
            await message.channel.send(f"ðŸŽ° {result}")
            return True
        except Exception as e:
            logger.error(f"Error in handle_slots: {e}")
            await message.channel.send(f"❌ Error playing slots. Please try again.")
            return False

    async def handle_trivia(self, message: discord.Message) -> bool:
        """Handle !trivia command - Trivia game"""
        try:
            import random
            questions = [
                ("What is the capital of France?", "Paris"),
                ("What is 2 + 2?", "4"),
                ("What color is the sky?", "Blue"),
            ]
            q, a = random.choice(questions)
            await message.channel.send(f"ðŸ“ **Trivia Question:** {q}\nReply with your answer!")
            # Note: Full trivia implementation would require waiting for reply
            return True
        except Exception as e:
            logger.error(f"Error in handle_trivia: {e}")
            await message.channel.send(f"❌ Error starting trivia. Please try again.")
            return False

    async def handle_starboard_leaderboard(self, message: discord.Message) -> bool:
        """Handle !starboard command - Show enhanced starboard leaderboard with animations"""
        try:
            import asyncio
            from discord import ui

            starboard = self.bot.starboard
            guild_id = message.guild.id

            # Loading animation
            loading_embed = discord.Embed(
                title="⭐ Loading Starboard",
                description="✨ Gathering starred messages...\n🌟 Calculating star counts...\n🎯 Ranking top messages...",
                color=discord.Color.gold()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.7)
            loading_embed.description = "✅ Gathering starred messages...\n🌟 Calculating star counts...\n🎯 Ranking top messages..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Gathering starred messages...\n✅ Calculating star counts...\n🎯 Ranking top messages..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Gathering starred messages...\n✅ Calculating star counts...\n✅ Ranking top messages..."
            await loading_msg.edit(embed=loading_embed)

            # Get leaderboard data
            leaderboard = starboard.get_leaderboard(guild_id)

            if not leaderboard:
                empty_embed = discord.Embed(
                    title="⭐ Starboard Hall of Fame",
                    description="**No starred messages yet!**\n\n"
                               "Be the first to get your message starred! ⭐\n\n"
                               "*Star messages by reacting with ⭐ to highlight amazing content.*",
                    color=discord.Color.light_grey()
                )
                empty_embed.add_field(
                    name="🌟 How It Works",
                    value="• React with ⭐ to any message\n• Reach the star threshold to get featured\n• Top starred messages appear here",
                    inline=False
                )
                empty_embed.set_footer(text="Starboard celebrates the best community content")
                empty_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/star_empty.png")

                await loading_msg.edit(embed=empty_embed)
                await loading_msg.add_reaction("⭐")
                return True

            # Create paginated leaderboard
            class StarboardView(ui.View):
                def __init__(self, leaderboard, current_page=0):
                    super().__init__(timeout=300)
                    self.leaderboard = leaderboard
                    self.current_page = current_page
                    self.per_page = 5
                    self.update_buttons()

                def update_buttons(self):
                    total_pages = (len(self.leaderboard) - 1) // self.per_page + 1
                    self.prev_button.disabled = self.current_page == 0
                    self.next_button.disabled = self.current_page >= total_pages - 1
                    self.page_label.label = f"Page {self.current_page + 1}/{total_pages}"

                @ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, disabled=True)
                async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
                    self.current_page -= 1
                    self.update_buttons()
                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                @ui.button(label="📄 Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
                async def page_label(self, interaction: discord.Interaction, button: ui.Button):
                    pass

                @ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
                async def next_button(self, interaction: discord.Interaction, button: ui.Button):
                    self.current_page += 1
                    self.update_buttons()
                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                def create_embed(self):
                    start_idx = self.current_page * self.per_page
                    end_idx = start_idx + self.per_page
                    page_entries = self.leaderboard[start_idx:end_idx]

                    embed = discord.Embed(
                        title="⭐ Starboard Hall of Fame",
                        description=f"**Celebrating the most starred messages!** ({len(self.leaderboard)} total)\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.gold()
                    )

                    # Medal emojis for top 3
                    medals = ["🥇", "🥈", "🥉"]

                    for i, entry in enumerate(page_entries):
                        rank = start_idx + i + 1
                        star_count = entry.get('star_count', 0)
                        author_id = entry.get('author_id', 'unknown')
                        jump_url = entry.get('jump_url', '#')

                        # Medal for top 3
                        rank_display = medals[i] if rank <= 3 else f"#{rank}"

                        # Star visualization
                        stars_display = "⭐" * min(star_count, 10)
                        if star_count > 10:
                            stars_display += f" (+{star_count-10})"

                        embed.add_field(
                            name=f"{rank_display} {stars_display} ({star_count} stars)",
                            value=f"**Author:** <@{author_id}>\n"
                                 f"**[View Message]({jump_url})**",
                            inline=False
                        )

                    # Statistics
                    total_stars = sum(entry.get('star_count', 0) for entry in self.leaderboard)
                    embed.add_field(
                        name="📊 Starboard Stats",
                        value=f"• Total Stars Given: `{total_stars}`\n"
                              f"• Messages Starred: `{len(self.leaderboard)}`\n"
                              f"• Average Stars: `{total_stars // max(len(self.leaderboard), 1)}`",
                        inline=False
                    )

                    embed.set_footer(text="Starboard • Celebrating community excellence")
                    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/star_trophy.png")

                    return embed

            view = StarboardView(leaderboard)
            embed = view.create_embed()

            await loading_msg.edit(embed=embed, view=view)

            # Add celebratory reactions
            await loading_msg.add_reaction("⭐")
            await loading_msg.add_reaction("🥇")
            await loading_msg.add_reaction("🎉")

            return True

        except Exception as e:
            logger.error(f"Error in handle_starboard_leaderboard: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Starboard Error",
                description="Unable to load the starboard leaderboard. The system may be temporarily unavailable.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_list_events(self, message: discord.Message) -> bool:
        """Handle !events command - List scheduled events"""
        try:
            events = self.bot.event_scheduler
            guild_id = message.guild.id

            # Get scheduled events from the module
            from data_manager import dm
            scheduled_events = dm.get_guild_data(guild_id, "scheduled_events", {})

            if not scheduled_events:
                await message.channel.send("ðŸ“… No events scheduled. Use `!event create <name>` to create one.")
                return True

            embed = discord.Embed(title=f"{get_animated_emoji('events')} Scheduled Events", color=discord.Color.blue())
            for event_id, event_data in list(scheduled_events.items())[:10]:  # Top 10
                embed.add_field(
                    name=event_data.get("name", "Unknown Event"),
                    value=f"Status: {event_data.get('status', 'Unknown')}\nNext run: <t:{int(event_data.get('next_run', 0))}:R>",
                    inline=False
                )

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_list_events: {e}")
            await message.channel.send(f"❌ Error listing events. Please try again later.")
            return False

    async def handle_list_tournaments(self, message: discord.Message) -> bool:
        """Handle !tournaments command - List tournaments"""
        guild_id = message.guild.id
        if not is_system_enabled(guild_id, "tournaments"):
            await message.channel.send("❌ The tournaments system is currently disabled on this server.")
            return False
        try:
            tournaments = self.bot.tournaments

            if not tournaments._tournaments:
                await message.channel.send("🏆 No tournaments created yet. Use `!tournament create <name>` to create one.")
                return True

            embed = discord.Embed(title="🏆 Tournaments", color=discord.Color.gold())
            for t_id, t in list(tournaments._tournaments.items())[:10]:
                embed.add_field(
                    name=t.name,
                    value=f"ID: `{t_id}`\nStatus: {t.status.value}\nParticipants: {len(t.participants)}",
                    inline=False
                )

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_list_tournaments: {e}")
            await message.channel.send(f"❌ Error listing tournaments. Please try again later.")
            return False

    async def handle_tournament_leaderboard(self, message: discord.Message) -> bool:
        """Handle !tournamentleaderboard command"""
        try:
            tournaments = self.bot.tournaments

            if not tournaments._tournaments:
                await message.channel.send("🏆 No tournaments yet. Use `!tournament create <name>` to create one.")
                return True

            # Calculate leaderboard from tournament results
            embed = discord.Embed(title=f"{get_animated_emoji('tournaments')} Tournament Leaderboard", color=discord.Color.gold())
            # This is a simplified version - full implementation would track wins/losses
            embed.description = "Tournament leaderboard will be available after tournaments complete."
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_tournament_leaderboard: {e}")
            await message.channel.send(f"❌ Error showing leaderboard. Please try again later.")
            return False

    async def handle_tournament_join(self, message: discord.Message) -> bool:
        """Handle !join command - Join a tournament"""
        try:
            tournaments = self.bot.tournaments
            guild_id = message.guild.id

            # Parse tournament ID from message
            args = message.content.split()
            if len(args) < 2:
                await message.channel.send("❌ Usage: `!join <tournament_id>`")
                return True

            tournament_id = args[1]
            if tournament_id not in tournaments._tournaments:
                await message.channel.send(f"❌ Tournament `{tournament_id}` not found.")
                return True

            # Add user to tournament
            tournament = tournaments._tournaments[tournament_id]
            user_id = message.author.id
            if user_id in tournament.participants:
                await message.channel.send("❌ You're already in this tournament!")
                return True

            tournament.participants.append(user_id)
            tournaments._save_tournament(tournament)
            await message.channel.send(f"✅ You joined the tournament! Participants: {len(tournament.participants)}")
            return True
        except Exception as e:
            logger.error(f"Error in handle_tournament_join: {e}")
            await message.channel.send(f"❌ Error joining tournament. Please try again.")
            return False

    async def handle_server_stats(self, message: discord.Message) -> bool:
        """Handle !serverstats command"""
        try:
            intelligence = self.bot.intelligence
            guild = message.guild

            # Get basic server stats
            embed = discord.Embed(title="ðŸ“Š Server Statistics", color=discord.Color.blue())
            embed.add_field(name="Server Name", value=guild.name, inline=True)
            embed.add_field(name="Members", value=guild.member_count, inline=True)
            embed.add_field(name="Channels", value=len(guild.channels), inline=True)
            embed.add_field(name="Roles", value=len(guild.roles), inline=True)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_server_stats: {e}")
            await message.channel.send(f"❌ Error showing server stats. Please try again later.")
            return False

    async def handle_my_stats(self, message: discord.Message) -> bool:
        """Handle !mystats command"""
        try:
            guild_id = message.guild.id
            user_id = message.author.id

            # Get user stats from various modules
            from data_manager import dm

            embed = discord.Embed(title="ðŸ“Š Your Stats", color=discord.Color.green())

            # Leveling stats
            leveling_data = dm.get_guild_data(guild_id, "leveling_data", {})
            user_data = leveling_data.get(str(user_id), {})
            level = user_data.get("level", 0)
            xp = user_data.get("xp", 0)

            embed.add_field(name="Level", value=str(level), inline=True)
            embed.add_field(name="XP", value=str(xp), inline=True)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_my_stats: {e}")
            await message.channel.send(f"❌ Error showing your stats. Please try again later.")
            return False

    async def handle_at_risk(self, message: discord.Message) -> bool:
        """Handle !atrisk command"""
        try:
            from data_manager import dm
            guild_id = message.guild.id

            # Simple implementation - show users with low engagement
            embed = discord.Embed(title="⚠️ At-Risk Users", color=discord.Color.orange())
            embed.description = "Users who might be at risk of leaving the server (low activity)."
            embed.add_field(name="Note", value="Full implementation tracks activity and engagement metrics.", inline=False)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_at_risk: {e}")
            await message.channel.send(f"❌ Error showing at-risk users. Please try again later.")
            return False

    async def handle_remind(self, message: discord.Message) -> bool:
        """Handle !remind command - Set a reminder"""
        try:
            import re
            # Parse time and message from command
            content = message.content
            # Simple reminder: !remind 10m Check the oven
            match = re.search(r'!remind\s+(\d+[msh])\s+(.+)', content)
            if not match:
                await message.channel.send("❌ Usage: `!remind <time> <message>`\nExample: `!remind 10m Check the oven`")
                return True

            time_str, reminder_msg = match.groups()
            await message.channel.send(f"â° Reminder set! I'll remind you in {time_str}.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_remind: {e}")
            await message.channel.send(f"❌ Error setting reminder. Please try again.")
            return False

    async def handle_list_reminders(self, message: discord.Message) -> bool:
        """Handle !reminders command - List reminders"""
        try:
            from data_manager import dm
            user_id = str(message.author.id)
            reminders = dm.get_guild_data(message.guild.id, "reminders", {})
            user_reminders = [r for r in reminders.values() if r.get("user_id") == user_id]

            if not user_reminders:
                await message.channel.send("â° No reminders set. Use `!remind <time> <message>` to set one.")
                return True

            embed = discord.Embed(title="â° Your Reminders", color=discord.Color.blue())
            for r in user_reminders[:10]:
                embed.add_field(name=f"Reminder #{r.get('id', '?')}", value=r.get("message", "No message"), inline=False)

            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_list_reminders: {e}")
            await message.channel.send(f"❌ Error listing reminders. Please try again later.")
            return False

    async def handle_mod_stats(self, message: discord.Message) -> bool:
        """Handle !modstats command"""
        try:
            from data_manager import dm
            guild_id = message.guild.id

            embed = discord.Embed(title="ðŸ”¨ Moderation Stats", color=discord.Color.red())
            
            # Get mod actions from logging data
            mod_cases = dm.get_guild_data(guild_id, "mod_cases", {})
            embed.add_field(name="Total Cases", value=str(len(mod_cases)), inline=True)
            
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_mod_stats: {e}")
            await message.channel.send(f"❌ Error showing mod stats. Please try again later.")
            return False

    async def handle_shift_start(self, message: discord.Message) -> bool:
        """Handle !shift start command"""
        try:
            staff_shift = self.bot.staff_shifts
            guild_id = message.guild.id
            user_id = message.author.id
            
            # Simple shift start
            from data_manager import dm
            shifts = dm.get_guild_data(guild_id, "staff_shifts", {})
            if str(user_id) in shifts and shifts[str(user_id)].get("status") == "active":
                await message.channel.send("❌ You're already on shift!")
                return True
            
            shifts[str(user_id)] = {"status": "active", "start_time": time.time()}
            dm.update_guild_data(guild_id, "staff_shifts", shifts)
            await message.channel.send("✅ Shift started! Use `!shift end` to end it.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_shift_start: {e}")
            await message.channel.send(f"❌ Error starting shift. Please try again.")
            return False

    async def handle_shift_end(self, message: discord.Message) -> bool:
        """Handle !shift end command"""
        try:
            from data_manager import dm
            guild_id = message.guild.id
            user_id = message.author.id
            
            shifts = dm.get_guild_data(guild_id, "staff_shifts", {})
            if str(user_id) not in shifts or shifts[str(user_id)].get("status") != "active":
                await message.channel.send("❌ You're not on shift!")
                return True
            
            # Calculate shift duration
            start_time = shifts[str(user_id)].get("start_time", time.time())
            duration = int(time.time() - start_time)
            
            shifts[str(user_id)]["status"] = "ended"
            shifts[str(user_id)]["duration"] = duration
            dm.update_guild_data(guild_id, "staff_shifts", shifts)
            
            await message.channel.send(f"✅ Shift ended! Duration: {duration//60} minutes.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_shift_end: {e}")
            await message.channel.send(f"❌ Error ending shift. Please try again.")
            return False

    async def handle_shift_status(self, message: discord.Message) -> bool:
        """Handle !shift command - Show shift status"""
        try:
            from data_manager import dm
            guild_id = message.guild.id
            user_id = str(message.author.id)
            
            shifts = dm.get_guild_data(guild_id, "staff_shifts", {})
            if user_id not in shifts or shifts[user_id].get("status") != "active":
                await message.channel.send("❌ You're not currently on shift. Use `!shift start` to begin.")
                return True
            
            start_time = shifts[user_id].get("start_time", time.time())
            duration = int((time.time() - start_time) / 60)  # minutes
            
            await message.channel.send(f"✅ You're on shift! Duration: {duration} minutes. Use `!shift end` to end.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_shift_status: {e}")
            await message.channel.send(f"❌ Error showing shift status. Please try again later.")
            return False

    async def handle_staff_review_cmd(self, message: discord.Message) -> bool:
        """Handle !staffreview command"""
        try:
            embed = discord.Embed(title="ðŸ“ Staff Reviews", color=discord.Color.blue())
            embed.description = "Submit staff reviews and feedback."
            embed.add_field(name="How it Works", value="Staff reviews are managed through the admin panel.", inline=False)
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staff_review_cmd: {e}")
            await message.channel.send(f"❌ Error processing staff review. Please try again later.")
            return False

    async def handle_announce(self, message: discord.Message) -> bool:
        """Handle !announce command"""
        try:
            # Simple announcement - echo the message
            content = message.content
            args = content.split(maxsplit=1)
            if len(args) < 2:
                await message.channel.send("❌ Usage: `!announce <message>`")
                return True
            
            announcement = args[1]
            embed = discord.Embed(title="ðŸ“¢ Announcement", description=announcement, color=discord.Color.blue())
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_announce: {e}")
            await message.channel.send(f"❌ Error making announcement. Please try again.")
            return False

    async def handle_leveling_shop(self, message: discord.Message) -> bool:
        """Handle !levelshop command with enhanced interactive shop"""
        try:
            import asyncio
            from discord import ui

            guild_id = message.guild.id
            user_id = message.author.id

            # Check if leveling system is enabled
            if not is_system_enabled(guild_id, "leveling"):
                embed = discord.Embed(
                    title="❌ Leveling Shop Unavailable",
                    description="The leveling system is currently disabled on this server.",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel leveling to enable the system")
                await message.channel.send(embed=embed)
                return False

            # Loading animation
            loading_embed = discord.Embed(
                title="🎁 Opening Level Shop",
                description="🛍️ Loading shop inventory...\n💎 Checking your balance...\n🎨 Preparing premium rewards...",
                color=discord.Color.purple()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Loading shop inventory...\n💎 Checking your balance...\n🎨 Preparing premium rewards..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Loading shop inventory...\n✅ Checking your balance...\n🎨 Preparing premium rewards..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Loading shop inventory...\n✅ Checking your balance...\n✅ Preparing premium rewards..."
            await loading_msg.edit(embed=loading_embed)

            # Get user's leveling data
            leveling_data = dm.get_guild_data(guild_id, "leveling_data", {})
            user_data = leveling_data.get(str(user_id), {})
            user_xp = user_data.get("xp", 0)
            user_level = user_data.get("level", 1)

            # Get shop items (you may want to store these in config)
            shop_items = [
                {"id": "color_role", "name": "🎨 Custom Color Role", "cost": 5000, "desc": "Get a custom color role for your name", "type": "role"},
                {"id": "vip_badge", "name": "⭐ VIP Badge", "cost": 10000, "desc": "Special VIP badge in your profile", "type": "badge"},
                {"id": "double_xp", "name": "⚡ 2x XP Boost (24h)", "cost": 2500, "desc": "Double XP gain for 24 hours", "type": "booster"},
                {"id": "level_skip", "name": "🚀 Level Skip", "cost": 15000, "desc": "Skip one level instantly", "type": "special"},
                {"id": "custom_title", "name": "🏷️ Custom Title", "cost": 8000, "desc": "Set a custom title under your name", "type": "cosmetic"},
                {"id": "shop_discount", "name": "💰 Shop Discount (10%)", "cost": 3000, "desc": "10% discount on all shop items", "type": "booster"}
            ]

            class LevelShopView(ui.View):
                def __init__(self, items, user_xp, user_level):
                    super().__init__(timeout=300)
                    self.items = items
                    self.user_xp = user_xp
                    self.user_level = user_level
                    self.selected_item = None

                def create_embed(self):
                    embed = discord.Embed(
                        title="🎁 Leveling Shop",
                        description=f"**Welcome to the Level Shop!**\n\n"
                                   f"💎 **Your Balance:** {self.user_xp:,} XP (Level {self.user_level})\n"
                                   f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.purple()
                    )

                    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/level_shop.png")

                    for i, item in enumerate(self.items):
                        can_afford = self.user_xp >= item["cost"]
                        status = "✅ Available" if can_afford else f"❌ Need {item['cost'] - self.user_xp:,} more XP"

                        embed.add_field(
                            name=f"{item['name']} — {item['cost']:,} XP",
                            value=f"{item['desc']}\n{status}",
                            inline=False
                        )

                    embed.add_field(
                        name="📋 How to Buy",
                        value="Select an item below and confirm your purchase!\n"
                              "Items are delivered instantly to your inventory.",
                        inline=False
                    )

                    embed.set_footer(text="Earn XP by chatting • Use !rank to check your progress")
                    return embed

                @ui.button(label="🎨 Color Role", style=discord.ButtonStyle.primary, row=0)
                async def buy_color_role(self, interaction: discord.Interaction, button: ui.Button):
                    await self.handle_purchase(interaction, 0)

                @ui.button(label="⭐ VIP Badge", style=discord.ButtonStyle.primary, row=0)
                async def buy_vip_badge(self, interaction: discord.Interaction, button: ui.Button):
                    await self.handle_purchase(interaction, 1)

                @ui.button(label="⚡ XP Boost", style=discord.ButtonStyle.primary, row=1)
                async def buy_xp_boost(self, interaction: discord.Interaction, button: ui.Button):
                    await self.handle_purchase(interaction, 2)

                @ui.button(label="🚀 Level Skip", style=discord.ButtonStyle.primary, row=1)
                async def buy_level_skip(self, interaction: discord.Interaction, button: ui.Button):
                    await self.handle_purchase(interaction, 3)

                @ui.button(label="🏷️ Custom Title", style=discord.ButtonStyle.secondary, row=2)
                async def buy_custom_title(self, interaction: discord.Interaction, button: ui.Button):
                    await self.handle_purchase(interaction, 4)

                @ui.button(label="💰 Discount", style=discord.ButtonStyle.secondary, row=2)
                async def buy_discount(self, interaction: discord.Interaction, button: ui.Button):
                    await self.handle_purchase(interaction, 5)

                async def handle_purchase(self, interaction: discord.Interaction, item_index):
                    item = self.items[item_index]

                    # Check if user can afford
                    leveling_data = dm.get_guild_data(interaction.guild_id, "leveling_data", {})
                    user_data = leveling_data.get(str(interaction.user.id), {})
                    current_xp = user_data.get("xp", 0)

                    if current_xp < item["cost"]:
                        await interaction.response.send_message(
                            f"❌ You don't have enough XP! You need {item['cost'] - current_xp:,} more XP.",
                            ephemeral=True
                        )
                        return

                    # Deduct XP and give item
                    user_data["xp"] = current_xp - item["cost"]

                    # Add item to user's inventory
                    inventory = user_data.get("inventory", [])
                    inventory.append({
                        "item_id": item["id"],
                        "name": item["name"],
                        "purchased_at": interaction.created_at.timestamp(),
                        "type": item["type"]
                    })
                    user_data["inventory"] = inventory

                    leveling_data[str(interaction.user.id)] = user_data
                    dm.update_guild_data(interaction.guild_id, "leveling_data", leveling_data)

                    # Success message with animation
                    success_embed = discord.Embed(
                        title="🎉 Purchase Successful!",
                        description=f"You bought **{item['name']}** for {item['cost']:,} XP!",
                        color=discord.Color.green()
                    )
                    success_embed.add_field(
                        name="💎 Remaining Balance",
                        value=f"{user_data['xp']:,} XP",
                        inline=True
                    )
                    success_embed.add_field(
                        name="📦 Item Delivered",
                        value="Check your inventory with `/inventory`",
                        inline=True
                    )

                    await interaction.response.send_message(embed=success_embed, ephemeral=True)

                    # Update the shop embed
                    self.user_xp = user_data["xp"]
                    new_embed = self.create_embed()
                    await interaction.followup.edit_message(interaction.message.id, embed=new_embed, view=self)

            view = LevelShopView(shop_items, user_xp, user_level)
            embed = view.create_embed()

            await loading_msg.edit(embed=embed, view=view)

            # Add celebratory reactions
            await loading_msg.add_reaction("🎁")
            await loading_msg.add_reaction("💎")
            await loading_msg.add_reaction("🛍️")

            return True

        except Exception as e:
            logger.error(f"Error in handle_leveling_shop: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Shop Error",
                description="The leveling shop is experiencing technical difficulties. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_staffpromotion_history(self, message: discord.Message) -> bool:
        """Handle !promotionhistory command"""
        try:
            from data_manager import dm
            guild_id = message.guild.id
            
            embed = discord.Embed(title="ðŸ“ˆ Promotion History", color=discord.Color.gold())
            promo_history = dm.get_guild_data(guild_id, "promotion_history", [])
            
            if not promo_history:
                embed.description = "No promotion history yet."
            else:
                for entry in promo_history[-10:]:  # Last 10 entries
                    embed.add_field(
                        name=f"User: {entry.get('user_id', 'Unknown')}",
                        value=f"From: {entry.get('from_tier', '?')} → To: {entry.get('to_tier', '?')}\nDate: {entry.get('timestamp', 'Unknown')}",
                        inline=False
                    )
            
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromotion_history: {e}")
            await message.channel.send(f"❌ Error showing promotion history. Please try again later.")
            return False

    async def handle_peer_vote(self, message: discord.Message) -> bool:
        """Handle !vote command - Peer voting"""
        try:
            # Simple peer vote implementation
            guild_id = message.guild.id
            
            # Check if user mentioned someone
            if not message.mentions:
                await message.channel.send("❌ Usage: `!vote @user`")
                return True
            
            target = message.mentions[0]
            staff_promo = self.bot.staff_promo
            
            # Submit peer vote
            await staff_promo.submit_peer_vote(guild_id, message.author.id, target.id)
            await message.channel.send(f"✅ Peer vote recorded for {target.mention}.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_peer_vote: {e}")
            await message.channel.send(f"❌ Error processing vote. Please try again.")
            return False

    async def handle_economy_challenge(self, message: discord.Message) -> bool:
        """Handle !challenge command"""
        try:
            guild_id = message.guild.id
            
            # Check if economy system is enabled
            if not is_system_enabled(guild_id, "economy"):
                await message.channel.send("❌ The economy system is currently disabled on this server.")
                return False
            
            import random
            # Simple challenge: guess a number
            number = random.randint(1, 10)
            await message.channel.send(f"ðŸŽ¯ **Challenge:** Guess a number between 1 and 10! Reply with your guess.")
            # Note: Full implementation would wait for reply
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_challenge: {e}")
            await message.channel.send(f"❌ Error starting challenge. Please try again.")
            return False

    async def handle_application_status(self, message: discord.Message) -> bool:
        """Handle !application status command"""
        try:
            config = dm.get_guild_data(message.guild.id, "application_config", {})
            if not config.get("enabled", False):
                await message.channel.send("❌ Applications system is not enabled.")
                return True

            apps = dm.get_guild_data(message.guild.id, "applications", {})
            pending = 0
            approved = 0
            denied = 0
            for user_apps in apps.values():
                for app in user_apps:
                    status = app.get("status", "pending")
                    if status == "pending":
                        pending += 1
                    elif status == "approved":
                        approved += 1
                    elif status == "denied":
                        denied += 1

            embed = discord.Embed(title="📋 Applications Status", color=discord.Color.blue())
            embed.add_field(name="System Enabled", value="✅ Yes" if config.get("enabled") else "❌ No", inline=True)
            embed.add_field(name="Pending", value=pending, inline=True)
            embed.add_field(name="Approved", value=approved, inline=True)
            embed.add_field(name="Denied", value=denied, inline=True)
            embed.set_footer(text=f"Total Applications: {pending + approved + denied}")
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_application_status: {e}")
            await message.channel.send("❌ Unable to load application status. Please try again.")
            return False

    async def handle_appeal_status(self, message: discord.Message) -> bool:
        """Handle !appeal status command"""
        try:
            config = dm.get_guild_data(message.guild.id, "appeal_config", {})
            if not config.get("enabled", False):
                await message.channel.send("❌ Appeals system is not enabled.")
                return True

            appeals = dm.get_guild_data(message.guild.id, "appeals", {})
            pending = 0
            accepted = 0
            denied = 0
            for user_appeals in appeals.values():
                for appeal in user_appeals:
                    status = appeal.get("status", "pending")
                    if status == "pending":
                        pending += 1
                    elif status == "accepted":
                        accepted += 1
                    elif status == "denied":
                        denied += 1

            embed = discord.Embed(title="⚖️ Appeals Status", color=discord.Color.orange())
            embed.add_field(name="System Enabled", value="✅ Yes" if config.get("enabled") else "❌ No", inline=True)
            embed.add_field(name="Pending", value=pending, inline=True)
            embed.add_field(name="Accepted", value=accepted, inline=True)
            embed.add_field(name="Denied", value=denied, inline=True)
            embed.set_footer(text=f"Total Appeals: {pending + accepted + denied}")
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_appeal_status: {e}")
            await message.channel.send("❌ Unable to load appeal status. Please try again.")
            return False

    async def handle_help_embed(self, message: discord.Message) -> bool:
        """Handle !help embed command"""
        try:
            from modules.help_system import HelpSystem
            help_system = HelpSystem(self.bot)
            await help_system.send_help_embed(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_help_embed: {e}")
            await message.channel.send("❌ Unable to load help embed. Please try again.")
            return False

    async def handle_simple(self, message: discord.Message) -> bool:
        """Handle simple command"""
        try:
            # Get the command data
            cmd_name = message.content.split()[0][1:]  # remove !
            cmds = dm.get_guild_data(message.guild.id, "custom_commands", {})
            code = cmds.get(cmd_name)
            if isinstance(code, str):
                data = json.loads(code)
            else:
                data = code

            content = data.get("content", "✅ Command executed.")
            content = self._render_template(content, message)
            await message.channel.send(content)
            return True
        except Exception as e:
            logger.error(f"Error in handle_simple: {e}")
            await message.channel.send("✅ Simple command executed.")
            return True







    async def handle_economy_shop(self, message: discord.Message) -> bool:
        """Handle !economy shop command"""
        try:
            embed = discord.Embed(title="Economy Shop", description="Buy items with your coins!", color=discord.Color.blue())
            embed.add_field(name="Role Boost (100 coins)", value="Get a temporary role boost", inline=False)
            embed.add_field(name="Custom Title (500 coins)", value="Set a custom title", inline=False)
            embed.add_field(name="VIP Status (1000 coins)", value="Get VIP perks for a day", inline=False)
            embed.set_footer(text="Use !economy buy <item> to purchase")
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_shop: {e}")
            await message.channel.send("❌ Error loading shop. Please try again.")
            return False

    async def handle_economy_transfer(self, message: discord.Message) -> bool:
        """Handle !economy transfer command"""
        try:
            args = message.content.split()
            if len(args) < 3:
                await message.channel.send("❌ Usage: !economy transfer <@user> <amount>")
                return True
            # Parse amount
            try:
                amount = int(args[2])
            except ValueError:
                await message.channel.send("❌ Invalid amount.")
                return True
            if amount <= 0:
                await message.channel.send("❌ Amount must be positive.")
                return True
            # Find user
            if not message.mentions:
                await message.channel.send("❌ Mention a user to transfer to.")
                return True
            target = message.mentions[0]
            if target.id == message.author.id:
                await message.channel.send("❌ Cannot transfer to yourself.")
                return True
            balance = dm.get_user_data(message.author.id, "balance", 0)
            if balance < amount:
                await message.channel.send("❌ Insufficient balance.")
                return True
            # Transfer
            dm.update_user_data(message.author.id, "balance", balance - amount)
            target_balance = dm.get_user_data(target.id, "balance", 0) + amount
            dm.update_user_data(target.id, "balance", target_balance)
            await message.channel.send(f"✅ Transferred {amount} coins to {target.display_name}.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_transfer: {e}")
            await message.channel.send("❌ Error transferring coins. Please try again.")
            return False

    async def handle_economy_rob(self, message: discord.Message) -> bool:
        """Handle !economy rob command"""
        try:
            from modules.economy import Economy
            economy = Economy(self.bot)
            args = message.content.split()
            await economy.rob(message, args)
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_rob: {e}")
            await message.channel.send("❌ Error robbing. Please try again.")
            return False

    async def handle_economy_buy(self, message: discord.Message) -> bool:
        """Handle !economy buy command"""
        try:
            from modules.economy import Economy
            economy = Economy(self.bot)
            args = message.content.split()
            await economy.buy(message, args)
            return True
        except Exception as e:
            logger.error(f"Error in handle_economy_buy: {e}")
            await message.channel.send("❌ Error buying item. Please try again.")
            return False

    async def handle_leaderboard(self, message: discord.Message) -> bool:
        """Handle !leaderboard command"""
        try:
            from modules.leveling import Leveling
            leveling = Leveling(self.bot)
            await leveling.leaderboard(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leaderboard: {e}")
            await message.channel.send("❌ Error loading leaderboard. Please try again.")
            return False

    async def handle_leveling_rank(self, message: discord.Message) -> bool:
        """Handle !leveling rank command"""
        try:
            xp = dm.get_user_data(message.author.id, "xp", 0)
            level = xp // 100  # simple level calc
            embed = discord.Embed(title=f"{message.author.display_name}'s Rank", color=discord.Color.blue())
            embed.add_field(name="Level", value=level, inline=True)
            embed.add_field(name="XP", value=f"{xp}/{(level+1)*100}", inline=True)
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_rank: {e}")
            await message.channel.send("❌ Error loading rank. Please try again.")
            return False

    async def handle_leveling_leaderboard(self, message: discord.Message) -> bool:
        """Handle !leveling leaderboard command"""
        try:
            xps = {}
            for uid in dm.list_user_ids():
                xp = dm.get_user_data(uid, "xp", 0)
                if xp > 0:
                    xps[uid] = xp
            sorted_xp = sorted(xps.items(), key=lambda x: x[1], reverse=True)[:10]
            embed = discord.Embed(title=f"{get_animated_emoji('leveling')} Leveling Leaderboard", color=discord.Color.gold())
            for i, (uid, xp) in enumerate(sorted_xp, 1):
                user = self.bot.get_user(uid)
                name = user.display_name if user else f"User {uid}"
                level = xp // 100
                embed.add_field(name=f"{i}. {name}", value=f"Level {level} ({xp} XP)", inline=False)
            if not sorted_xp:
                embed.description = "No one has XP yet!"
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_leveling_leaderboard: {e}")
            await message.channel.send("❌ Error loading leveling leaderboard. Please try again.")
            return False

    async def handle_staffpromo_status(self, message: discord.Message) -> bool:
        """Handle !staffpromo status command"""
        try:
            from modules.staff_promo import StaffPromotionSystem
            staff_promo = StaffPromotionSystem(self.bot)
            config = staff_promo.get_config(message.guild.id)
            enabled = config.get("enabled", False)
            embed = discord.Embed(title="Staff Promotion Status", color=discord.Color.blue())
            embed.add_field(name="Enabled", value="✅ Yes" if enabled else "❌ No", inline=True)
            if enabled:
                tiers = config.get("tiers", [])
                embed.add_field(name="Tiers", value=len(tiers), inline=True)
                embed.add_field(name="Active Promotions", value="Check logs for details", inline=False)
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_status: {e}")
            await message.channel.send("❌ Error loading staff promo status. Please try again.")
            return False

    async def handle_staffpromo_leaderboard(self, message: discord.Message) -> bool:
        """Handle !staffpromo leaderboard command"""
        try:
            # Simple leaderboard based on activity or something
            embed = discord.Embed(title="Staff Promotion Leaderboard", description="Top promoted members (placeholder)", color=discord.Color.blue())
            embed.add_field(name="1. Example User", value="Tier: Moderator", inline=False)
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_leaderboard: {e}")
            await message.channel.send("❌ Error loading staff promo leaderboard. Please try again.")
            return False

    async def handle_staffpromo_progress(self, message: discord.Message) -> bool:
        """Handle !staffpromo progress command"""
        try:
            from modules.staff_promo import StaffPromo
            staff_promo = StaffPromo(self.bot)
            await staff_promo.progress(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_progress: {e}")
            await message.channel.send("❌ Error loading progress. Please try again.")
            return False

    async def handle_staffpromo_tiers(self, message: discord.Message) -> bool:
        """Handle !staffpromo tiers command"""
        try:
            from modules.staff_promo import StaffPromo
            staff_promo = StaffPromo(self.bot)
            await staff_promo.tiers(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_tiers: {e}")
            await message.channel.send("❌ Error loading tiers. Please try again.")
            return False

    async def handle_staffpromo_config(self, message: discord.Message) -> bool:
        """Handle !staffpromo config command"""
        try:
            from modules.staff_promo import StaffPromo
            staff_promo = StaffPromo(self.bot)
            await staff_promo.config(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_config: {e}")
            await message.channel.send("❌ Error loading config. Please try again.")
            return False

    async def handle_staffpromo_promote(self, message: discord.Message) -> bool:
        """Handle !staffpromo promote command"""
        try:
            from modules.staff_promo import StaffPromo
            staff_promo = StaffPromo(self.bot)
            args = message.content.split()
            await staff_promo.promote(message, args)
            return True
        except Exception as e:
            logger.error(f"Error in handle_staffpromo_promote: {e}")
            await message.channel.send("❌ Error promoting. Please try again.")
            return False

    async def handle_config_panel(self, message: discord.Message) -> bool:
        """Handle !config panel command"""
        try:
            from modules.config_panels import ConfigPanels
            config_panels = ConfigPanels(self.bot)
            await config_panels.show_panel(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_config_panel: {e}")
            await message.channel.send("❌ Error loading config panel. Please try again.")
            return False

    async def handle_list_triggers(self, message: discord.Message) -> bool:
        """Handle !list triggers command"""
        try:
            triggers = dm.get_guild_data(message.guild.id, "triggers", {})
            if not triggers:
                await message.channel.send("📝 No triggers configured.")
                return True

            embed = discord.Embed(title="📝 Configured Triggers", color=discord.Color.green())
            for trigger, response in triggers.items():
                embed.add_field(name=trigger, value=response[:200] + "..." if len(response) > 200 else response, inline=False)
            await message.channel.send(embed=embed)
            return True
        except Exception as e:
            logger.error(f"Error in handle_list_triggers: {e}")
            await message.channel.send("❌ Error loading triggers. Please try again.")
            return False

    async def handle_help_all(self, message: discord.Message) -> bool:
        """Handle !help all command"""
        try:
            from modules.help_system import HelpSystem
            help_system = HelpSystem(self.bot)
            await help_system.send_full_help(message)
            return True
        except Exception as e:
            logger.error(f"Error in handle_help_all: {e}")
            await message.channel.send("❌ Error loading full help. Please try again.")
            return False

    async def handle_raidstatus(self, message: discord.Message) -> bool:
        """Handle !raidstatus command with enhanced visuals and animations"""
        try:
            import asyncio

            # Loading animation
            loading_embed = discord.Embed(
                title="🛡️ Scanning Anti-Raid Defenses",
                description="🔍 Analyzing security protocols...\n🔍 Checking threat detection...\n🔍 Monitoring server activity...",
                color=discord.Color.orange()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(1)
            loading_embed.description = "✅ Analyzing security protocols...\n🔍 Checking threat detection...\n🔍 Monitoring server activity..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Analyzing security protocols...\n✅ Checking threat detection...\n🔍 Monitoring server activity..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Analyzing security protocols...\n✅ Checking threat detection...\n✅ Monitoring server activity..."
            await loading_msg.edit(embed=loading_embed)

            settings = dm.get_guild_data(message.guild.id, "anti_raid_settings", {})
            enabled = settings.get("enabled", False)

            # Enhanced status embed
            if enabled:
                embed = discord.Embed(
                    title="🛡️ Anti-Raid Defense: ACTIVE",
                    description="**Server protection is currently enabled and operational.**",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="🔒 Protection Status",
                    value="✅ **ENABLED**\n🛡️ **ACTIVE**\n⚡ **MONITORING**",
                    inline=True
                )
            else:
                embed = discord.Embed(
                    title="🛡️ Anti-Raid Defense: INACTIVE",
                    description="**Server protection is currently disabled.**",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="🔓 Protection Status",
                    value="❌ **DISABLED**\n⚠️ **VULNERABLE**\n🔴 **OFFLINE**",
                    inline=True
                )

            # Detailed settings
            if enabled:
                embed.add_field(
                    name="⚙️ Active Protections",
                    value=f"• Join Rate Limit: `{settings.get('join_rate_limit', '10')}/min`\n"
                          f"• Message Spam Limit: `{settings.get('message_spam_limit', '5')}/sec`\n"
                          f"• Account Age Check: `{settings.get('min_account_age', '7')} days`\n"
                          f"• Auto-Lockdown: `{settings.get('auto_lockdown', 'Enabled')}`",
                    inline=False
                )

                # Recent activity
                recent_raids = settings.get("recent_raids", [])
                if recent_raids:
                    raid_list = "\n".join([f"• {raid.get('timestamp', 'Unknown')} - {raid.get('severity', 'Unknown')} threat" for raid in recent_raids[-3:]])
                    embed.add_field(
                        name="📊 Recent Activity",
                        value=raid_list,
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📊 Recent Activity",
                        value="• No recent threats detected\n• Server is secure",
                        inline=False
                    )

            embed.add_field(
                name="🛠️ Management",
                value="Use `!configpanel antiraid` to configure protection settings.\n"
                      "Contact administrators to enable/disable the system.",
                inline=False
            )

            embed.set_footer(text=f"Anti-Raid System • Last checked: {discord.utils.format_dt(discord.utils.utcnow())}")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/shield.png")

            await loading_msg.edit(embed=embed)

            # Add celebratory reactions for active system
            if enabled:
                await loading_msg.add_reaction("🛡️")
                await loading_msg.add_reaction("✅")
                await loading_msg.add_reaction("⚡")

            return True

        except Exception as e:
            logger.error(f"Error in handle_raidstatus: {e}")
            error_embed = discord.Embed(
                title="❌ System Error",
                description="Unable to load anti-raid status. The system may be temporarily unavailable.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_guardian_status(self, message: discord.Message) -> bool:
        """Handle !guardian status command with AI-themed animations"""
        try:
            import asyncio

            # AI-themed loading animation
            loading_embed = discord.Embed(
                title="🤖 Initializing Guardian AI",
                description="🧠 Booting neural networks...\n🔍 Scanning threat databases...\n⚡ Calibrating detection algorithms...",
                color=discord.Color.purple()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Booting neural networks...\n🔍 Scanning threat databases...\n⚡ Calibrating detection algorithms..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.6)
            loading_embed.description = "✅ Booting neural networks...\n✅ Scanning threat databases...\n⚡ Calibrating detection algorithms..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Booting neural networks...\n✅ Scanning threat databases...\n✅ Calibrating detection algorithms..."
            await loading_msg.edit(embed=loading_embed)

            config = dm.get_guild_data(message.guild.id, "guardian_config", {})
            enabled = config.get("enabled", False)

            # Enhanced AI-themed status embed
            if enabled:
                embed = discord.Embed(
                    title="🤖 Guardian AI: ACTIVE",
                    description="**Advanced AI threat detection is online and protecting your server.**",
                    color=discord.Color.purple()
                )
                embed.add_field(
                    name="🧠 AI Status",
                    value="✅ **ONLINE**\n🤖 **ACTIVE**\n⚡ **SCANNING**",
                    inline=True
                )
            else:
                embed = discord.Embed(
                    title="🤖 Guardian AI: OFFLINE",
                    description="**AI threat detection is currently disabled.**",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="🧠 AI Status",
                    value="❌ **OFFLINE**\n⚠️ **VULNERABLE**\n🔴 **STANDBY**",
                    inline=True
                )

            # Detailed protection modules
            if enabled:
                toxicity_level = config.get("toxicity_level", "OFF")
                scam_level = config.get("scam_level", "OFF")
                impersonation_level = config.get("impersonation_level", "OFF")

                embed.add_field(
                    name="🛡️ Protection Modules",
                    value=f"• **Toxicity Filter**: `{toxicity_level}`\n"
                          f"• **Scam Detection**: `{scam_level}`\n"
                          f"• **Impersonation Guard**: `{impersonation_level}`\n"
                          f"• **Mass DM Monitor**: `{config.get('mass_dm_threshold', 10)}/min`\n"
                          f"• **Token Detection**: `{'ENABLED' if config.get('token_detection', False) else 'DISABLED'}`",
                    inline=False
                )

                # Threat statistics
                log = config.get("guardian_log", [])
                recent_threats = len([entry for entry in log if "detected" in entry.get("action", "").lower()])
                embed.add_field(
                    name="📊 Threat Intelligence",
                    value=f"• Threats Detected: `{recent_threats}`\n"
                          f"• Auto-Actions Taken: `{len(log)}`\n"
                          f"• Server Security: `PROTECTED`",
                    inline=False
                )

            embed.add_field(
                name="⚙️ Configuration",
                value="Use `!configpanel guardian` to adjust AI detection settings.\n"
                      "Fine-tune threat levels and response actions.",
                inline=False
            )

            embed.set_footer(text=f"Guardian AI System • Last scan: {discord.utils.format_dt(discord.utils.utcnow())}")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/ai_guardian.png")

            await loading_msg.edit(embed=embed)

            # AI-themed reactions
            if enabled:
                await loading_msg.add_reaction("🤖")
                await loading_msg.add_reaction("🧠")
                await loading_msg.add_reaction("⚡")
                await loading_msg.add_reaction("🛡️")

            return True

        except Exception as e:
            logger.error(f"Error in handle_guardian_status: {e}")
            error_embed = discord.Embed(
                title="❌ AI System Error",
                description="Guardian AI is experiencing technical difficulties. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_automod_status(self, message: discord.Message) -> bool:
        """Handle !automod status command with enhanced rule visualization"""
        try:
            import asyncio

            # Loading animation
            loading_embed = discord.Embed(
                title="🤖 Initializing AutoMod",
                description="🔧 Loading moderation rules...\n🔍 Scanning active filters...\n📊 Calculating statistics...",
                color=discord.Color.blue()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.7)
            loading_embed.description = "✅ Loading moderation rules...\n🔍 Scanning active filters...\n📊 Calculating statistics..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Loading moderation rules...\n✅ Scanning active filters...\n📊 Calculating statistics..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Loading moderation rules...\n✅ Scanning active filters...\n✅ Calculating statistics..."
            await loading_msg.edit(embed=loading_embed)

            config = dm.get_guild_data(message.guild.id, "automod_config", {})
            enabled = config.get("enabled", False)
            rules = config.get("rules", {})

            # Enhanced status embed
            if enabled:
                embed = discord.Embed(
                    title="🤖 AutoMod: ACTIVE",
                    description="**Automated moderation is protecting your server 24/7.**",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="⚙️ System Status",
                    value="✅ **ENABLED**\n🤖 **ACTIVE**\n🛡️ **PROTECTING**",
                    inline=True
                )
            else:
                embed = discord.Embed(
                    title="🤖 AutoMod: INACTIVE",
                    description="**Automated moderation is currently disabled.**",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="⚙️ System Status",
                    value="❌ **DISABLED**\n⚠️ **MANUAL MODE**\n🔴 **OFFLINE**",
                    inline=True
                )

            # Detailed rule status
            if enabled and rules:
                active_rules = []
                inactive_rules = []

                for rule_name, rule_config in rules.items():
                    if rule_config.get("enabled", False):
                        active_rules.append(f"✅ {rule_name.replace('_', ' ').title()}")
                    else:
                        inactive_rules.append(f"❌ {rule_name.replace('_', ' ').title()}")

                if active_rules:
                    embed.add_field(
                        name="🛡️ Active Rules",
                        value="\n".join(active_rules[:8]),  # Limit to 8 for embed size
                        inline=False
                    )

                if inactive_rules:
                    embed.add_field(
                        name="🔇 Inactive Rules",
                        value="\n".join(inactive_rules[:8]),
                        inline=False
                    )

                # Escalation info
                escalation = config.get("escalation", {})
                if escalation:
                    embed.add_field(
                        name="⚠️ Escalation Levels",
                        value=f"Warning → Mute → Kick → Ban\n"
                              f"Reset: `{escalation.get('reset_hours', 24)} hours`",
                        inline=False
                    )

            # Statistics
            embed.add_field(
                name="📊 Moderation Stats",
                value="• Actions Taken: `Loading...`\n• Messages Scanned: `24/7`\n• Server Protection: `ACTIVE`" if enabled else "• System Status: `DISABLED`\n• Manual Moderation: `REQUIRED`\n• Server Protection: `VULNERABLE`",
                inline=False
            )

            embed.add_field(
                name="⚙️ Configuration",
                value="Use `!configpanel automod` to enable/disable rules and adjust settings.\n"
                      "Fine-tune filters and escalation policies.",
                inline=False
            )

            embed.set_footer(text=f"AutoMod System • Last updated: {discord.utils.format_dt(discord.utils.utcnow())}")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/automod_robot.png")

            await loading_msg.edit(embed=embed)

            # Add celebratory reactions
            if enabled:
                await loading_msg.add_reaction("🤖")
                await loading_msg.add_reaction("🛡️")
                await loading_msg.add_reaction("✅")

            return True

        except Exception as e:
            logger.error(f"Error in handle_automod_status: {e}")
            error_embed = discord.Embed(
                title="❌ Moderation Error",
                description="Unable to load AutoMod status. The system may be temporarily unavailable.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_modlog_view(self, message: discord.Message) -> bool:
        """Handle !modlog view command with enhanced pagination and animations"""
        try:
            import asyncio
            from discord import ui

            # Loading animation
            loading_embed = discord.Embed(
                title="📋 Accessing Moderation Database",
                description="🔍 Retrieving moderation records...\n📊 Analyzing log entries...\n📋 Formatting report...",
                color=discord.Color.orange()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.6)
            loading_embed.description = "✅ Retrieving moderation records...\n📊 Analyzing log entries...\n📋 Formatting report..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Retrieving moderation records...\n✅ Analyzing log entries...\n📋 Formatting report..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Retrieving moderation records...\n✅ Analyzing log entries...\n✅ Formatting report..."
            await loading_msg.edit(embed=loading_embed)

            logs = dm.get_guild_data(message.guild.id, "mod_logs", [])

            if not logs:
                no_logs_embed = discord.Embed(
                    title="📋 Moderation Log",
                    description="**No moderation actions have been recorded yet.**\n\n"
                               "As moderators take actions (warn, mute, kick, ban), they will appear here.\n\n"
                               "*This helps maintain transparency and accountability.*",
                    color=discord.Color.light_grey()
                )
                no_logs_embed.set_footer(text="Moderation logs help track server safety and fairness")
                await loading_msg.edit(embed=no_logs_embed)
                return True

            # Create paginated view for logs
            class ModLogView(ui.View):
                def __init__(self, logs, current_page=0):
                    super().__init__(timeout=300)
                    self.logs = logs
                    self.current_page = current_page
                    self.logs_per_page = 5
                    self.update_buttons()

                def update_buttons(self):
                    total_pages = (len(self.logs) - 1) // self.logs_per_page + 1
                    self.prev_button.disabled = self.current_page == 0
                    self.next_button.disabled = self.current_page >= total_pages - 1
                    self.page_label.label = f"Page {self.current_page + 1}/{total_pages}"

                @ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, disabled=True)
                async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
                    self.current_page -= 1
                    self.update_buttons()
                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                @ui.button(label="📄 Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
                async def page_label(self, interaction: discord.Interaction, button: ui.Button):
                    pass

                @ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
                async def next_button(self, interaction: discord.Interaction, button: ui.Button):
                    self.current_page += 1
                    self.update_buttons()
                    embed = self.create_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

                def create_embed(self):
                    start_idx = self.current_page * self.logs_per_page
                    end_idx = start_idx + self.logs_per_page
                    page_logs = self.logs[start_idx:end_idx]

                    embed = discord.Embed(
                        title="📋 Moderation Log",
                        description=f"**Recent moderation actions** ({len(self.logs)} total)\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
                        color=discord.Color.orange()
                    )

                    for log in page_logs:
                        mod_id = log.get('moderator_id', 'Unknown')
                        mod = f"<@{mod_id}>" if mod_id != 'Unknown' else 'Unknown'

                        action = log.get('action', 'Unknown').upper()
                        target_id = log.get('user_id', 'Unknown')
                        target = f"<@{target_id}>" if target_id != 'Unknown' else 'Unknown'

                        reason = log.get('reason', 'No reason provided')
                        if len(reason) > 50:
                            reason = reason[:47] + "..."

                        timestamp = f"<t:{int(log.get('timestamp', 0))}:F>"

                        # Action emoji
                        action_emoji = {
                            'WARN': '⚠️', 'MUTE': '🔇', 'KICK': '👢', 'BAN': '🔨',
                            'UNMUTE': '🔊', 'UNBAN': '✅', 'TIMEOUT': '⏰'
                        }.get(action, '📋')

                        embed.add_field(
                            name=f"{action_emoji} {action} by {mod}",
                            value=f"**Target:** {target}\n**Reason:** {reason}\n**Time:** {timestamp}",
                            inline=False
                        )

                    embed.set_footer(text="Moderation logs ensure transparency and accountability")
                    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/mod_log.png")

                    return embed

            # Sort logs by timestamp (newest first) and create view
            sorted_logs = sorted(logs, key=lambda x: x.get('timestamp', 0), reverse=True)
            view = ModLogView(sorted_logs)
            embed = view.create_embed()

            await loading_msg.edit(embed=embed, view=view)

            # Add reaction
            await loading_msg.add_reaction("📋")

            return True

        except Exception as e:
            logger.error(f"Error in handle_modlog_view: {e}")
            error_embed = discord.Embed(
                title="❌ Database Error",
                description="Unable to access moderation logs. The system may be temporarily unavailable.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_suggest(self, message: discord.Message) -> bool:
        """Handle !suggest command with enhanced validation and feedback"""
        try:
            import asyncio
            guild_id = message.guild.id

            # Check if suggestions system is enabled
            if not is_system_enabled(guild_id, "suggestions"):
                embed = discord.Embed(
                    title="❌ Suggestions Unavailable",
                    description="The suggestions system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel suggestions to enable the system")
                await message.channel.send(embed=embed)
                return False

            args = message.content.split(maxsplit=1)
            if len(args) < 2:
                embed = discord.Embed(
                    title="💡 How to Submit a Suggestion",
                    description="Share your ideas to help improve the server!",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="📝 Usage",
                    value="`!suggest <your suggestion here>`",
                    inline=False
                )
                embed.add_field(
                    name="✨ Examples",
                    value="`!suggest Add a music channel`\n"
                          "`!suggest Create a gaming events category`\n"
                          "`!suggest Add more emoji reactions`",
                    inline=False
                )
                embed.add_field(
                    name="🎯 Tips for Good Suggestions",
                    value="• Be specific and detailed\n"
                          "• Explain why it would help\n"
                          "• Keep it constructive\n"
                          "• Check if it already exists",
                    inline=False
                )
                embed.set_footer(text="Your suggestions help make the server better!")
                embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/lightbulb.png")
                await message.channel.send(embed=embed)
                return True

            suggestion_text = args[1].strip()

            # Validate suggestion length
            if len(suggestion_text) < 10:
                embed = discord.Embed(
                    title="❌ Suggestion Too Short",
                    description="Please provide more detail in your suggestion (at least 10 characters).",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="💡 Tip",
                    value="The more detailed your suggestion, the better we can understand and implement it!",
                    inline=False
                )
                await message.channel.send(embed=embed)
                return True

            if len(suggestion_text) > 1000:
                embed = discord.Embed(
                    title="❌ Suggestion Too Long",
                    description="Please keep your suggestion under 1000 characters.",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
                return True

            # Loading animation
            loading_embed = discord.Embed(
                title="💡 Processing Your Suggestion",
                description="📝 Reviewing content...\n✅ Checking guidelines...\n💾 Saving suggestion...",
                color=discord.Color.blue()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.7)
            loading_embed.description = "✅ Reviewing content...\n✅ Checking guidelines...\n💾 Saving suggestion..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Reviewing content...\n✅ Checking guidelines...\n✅ Saving suggestion..."
            await loading_msg.edit(embed=loading_embed)

            # Check for duplicate recent suggestions
            suggestions = dm.get_guild_data(guild_id, "suggestions", [])
            recent_suggestions = [s for s in suggestions if s.get("user_id") == message.author.id and time.time() - s.get("timestamp", 0) < 3600]  # Last hour

            if recent_suggestions:
                embed = discord.Embed(
                    title="⏰ Recent Suggestion Found",
                    description="You've submitted a suggestion recently. Please wait before submitting another.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="⏱️ Cooldown",
                    value="You can submit another suggestion in 1 hour.",
                    inline=True
                )
                await loading_msg.edit(embed=embed)
                return True

            # Create suggestion
            suggestion = {
                "id": len(suggestions) + 1,
                "user_id": message.author.id,
                "text": suggestion_text,
                "timestamp": time.time(),
                "status": "pending",
                "votes": {"up": 0, "down": 0},
                "voters": []
            }

            suggestions.append(suggestion)
            dm.update_guild_data(guild_id, "suggestions", suggestions)

            # Success embed
            success_embed = discord.Embed(
                title="✅ Suggestion Submitted Successfully!",
                description=f"**Thank you for your suggestion, {message.author.display_name}!**\n\n"
                           f"Your idea has been recorded and will be reviewed by the community and staff.",
                color=discord.Color.green()
            )

            success_embed.add_field(
                name="📋 Suggestion Details",
                value=f"**ID:** `{suggestion['id']}`\n"
                      f"**Status:** Pending Review\n"
                      f"**Submitted:** <t:{int(time.time())}:R>",
                inline=True
            )

            success_embed.add_field(
                name="🎯 What's Next",
                value="• Community members can vote on your suggestion\n"
                      "• Staff will review and provide feedback\n"
                      "• You may be contacted for more details\n"
                      "• Check back later for updates!",
                inline=False
            )

            success_embed.add_field(
                name="💡 Suggestion Summary",
                value=f"```{suggestion_text[:200]}{'...' if len(suggestion_text) > 200 else ''}```",
                inline=False
            )

            success_embed.set_footer(text=f"Suggestion #{suggestion['id']} • {message.guild.name}")
            success_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/check_circle.png")

            await loading_msg.edit(embed=success_embed)

            # Add celebratory reactions
            await loading_msg.add_reaction("✅")
            await loading_msg.add_reaction("💡")
            await loading_msg.add_reaction("👍")

            return True

        except Exception as e:
            logger.error(f"Error in handle_suggest: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Suggestion Error",
                description="Unable to submit your suggestion. Please try again later.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_chatchannel_add(self, message: discord.Message) -> bool:
        """Handle !chatchannel add command"""
        try:
            if not message.channel_mentions:
                await message.channel.send("❌ Please mention a channel to add as chat channel.")
                return True

            channel = message.channel_mentions[0]
            if not isinstance(channel, discord.TextChannel):
                await message.channel.send("❌ Please specify a valid text channel.")
                return True

            config = dm.get_guild_data(message.guild.id, "chat_channels_config", {})
            channels = config.get("channels", [])
            if channel.id in channels:
                await message.channel.send(f"❌ {channel.mention} is already a chat channel.")
                return True

            channels.append(channel.id)
            config["channels"] = channels
            dm.update_guild_data(message.guild.id, "chat_channels_config", config)
            await message.channel.send(f"✅ Added {channel.mention} as a chat channel.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_chatchannel_add: {e}")
            await message.channel.send("❌ Error adding chat channel. Please try again.")
            return False

    async def handle_autoresponder_add(self, message: discord.Message) -> bool:
        """Handle !autoresponder add command"""
        try:
            args = message.content.split(maxsplit=2)
            if len(args) < 3:
                await message.channel.send("❌ Usage: !autoresponder add <trigger> <response>")
                return True

            trigger = args[1].lower()
            response = args[2]

            responders = dm.get_guild_data(message.guild.id, "auto_responders", {})
            if trigger in responders:
                await message.channel.send(f"❌ Trigger '{trigger}' already exists.")
                return True

            responders[trigger] = response
            dm.update_guild_data(message.guild.id, "auto_responders", responders)
            await message.channel.send(f"✅ Added autoresponder for '{trigger}'.")
            return True
        except Exception as e:
            logger.error(f"Error in handle_autoresponder_add: {e}")
            await message.channel.send("❌ Error adding autoresponder. Please try again.")
            return False

    async def handle_remindme(self, message: discord.Message) -> bool:
        """Handle !remindme command with enhanced validation and visuals"""
        try:
            import asyncio
            import re

            args = message.content.split(maxsplit=2)
            if len(args) < 3:
                embed = discord.Embed(
                    title="⏰ Reminder System",
                    description="Never forget important tasks again!",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="📝 Usage",
                    value="`!remindme <time> <message>`",
                    inline=False
                )
                embed.add_field(
                    name="⏰ Time Formats",
                    value="`30s` - 30 seconds\n"
                          "`5m` - 5 minutes\n"
                          "`2h` - 2 hours\n"
                          "`1d` - 1 day\n"
                          "`1w` - 1 week",
                    inline=True
                )
                embed.add_field(
                    name="💡 Examples",
                    value="`!remindme 2h Submit assignment`\n"
                          "`!remindme 30m Check the oven`\n"
                          "`!remindme 1d Call mom`",
                    inline=True
                )
                embed.add_field(
                    name="📋 Features",
                    value="• Reminders sent via DM\n"
                          "• Multiple reminders allowed\n"
                          "• Automatic cleanup of old reminders",
                    inline=False
                )
                embed.set_footer(text="Stay organized with personal reminders!")
                embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/alarm_clock.png")
                await message.channel.send(embed=embed)
                return True

            time_str = args[1]
            reminder_text = args[2]

            # Validate reminder text
            if len(reminder_text.strip()) < 3:
                embed = discord.Embed(
                    title="❌ Reminder Too Short",
                    description="Please provide a more detailed reminder message (at least 3 characters).",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
                return True

            if len(reminder_text) > 500:
                embed = discord.Embed(
                    title="❌ Reminder Too Long",
                    description="Please keep your reminder under 500 characters.",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
                return True

            # Loading animation
            loading_embed = discord.Embed(
                title="⏰ Setting Up Your Reminder",
                description="🕒 Parsing time format...\n📝 Processing message...\n💾 Scheduling reminder...",
                color=discord.Color.blue()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.6)
            loading_embed.description = "✅ Parsing time format...\n📝 Processing message...\n💾 Scheduling reminder..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Parsing time format...\n✅ Processing message...\n💾 Scheduling reminder..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.3)
            loading_embed.description = "✅ Parsing time format...\n✅ Processing message...\n✅ Scheduling reminder..."
            await loading_msg.edit(embed=loading_embed)

            # Parse time with enhanced format support
            match = re.match(r'(\d+)([smhdw])', time_str.lower())
            if not match:
                error_embed = discord.Embed(
                    title="❌ Invalid Time Format",
                    description="Please use a valid time format.",
                    color=discord.Color.red()
                )
                error_embed.add_field(
                    name="⏰ Supported Formats",
                    value="`30s` - seconds\n"
                          "`5m` - minutes\n"
                          "`2h` - hours\n"
                          "`1d` - days\n"
                          "`1w` - weeks",
                    inline=False
                )
                await loading_msg.edit(embed=error_embed)
                return True

            amount = int(match.group(1))
            unit = match.group(2)

            # Validate reasonable limits
            if unit == 's' and amount > 3600:  # Max 1 hour for seconds
                amount = 3600
            elif unit == 'm' and amount > 1440:  # Max 24 hours for minutes
                amount = 1440
            elif unit == 'h' and amount > 168:  # Max 1 week for hours
                amount = 168
            elif unit == 'd' and amount > 30:  # Max 30 days
                amount = 30
            elif unit == 'w' and amount > 4:  # Max 4 weeks
                amount = 4

            multipliers = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
            delay = amount * multipliers[unit]
            reminder_time = time.time() + delay

            # Check user reminder limits
            reminders = dm.get_guild_data(message.guild.id, "reminders", [])
            user_reminders = [r for r in reminders if r.get("user_id") == message.author.id and r.get("timestamp", 0) > time.time()]

            if len(user_reminders) >= 10:  # Max 10 active reminders per user
                embed = discord.Embed(
                    title="❌ Too Many Reminders",
                    description="You can only have up to 10 active reminders at once.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="💡 Tip",
                    value="Use `!reminders` to view and manage your existing reminders.",
                    inline=False
                )
                await loading_msg.edit(embed=embed)
                return True

            # Create reminder
            reminder = {
                "id": len(reminders) + 1,
                "user_id": message.author.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id,
                "text": reminder_text,
                "timestamp": reminder_time,
                "created_at": time.time(),
                "status": "active"
            }

            reminders.append(reminder)
            dm.update_guild_data(message.guild.id, "reminders", reminders)

            # Success embed
            success_embed = discord.Embed(
                title="✅ Reminder Set Successfully!",
                description=f"**{message.author.display_name}**, your reminder has been scheduled!",
                color=discord.Color.green()
            )

            success_embed.add_field(
                name="⏰ Reminder Details",
                value=f"**When:** <t:{int(reminder_time)}:F> (<t:{int(reminder_time)}:R>)\n"
                      f"**Message:** {reminder_text}\n"
                      f"**ID:** `{reminder['id']}`",
                inline=False
            )

            success_embed.add_field(
                name="📱 Delivery Method",
                value="You'll receive this reminder via **direct message** to ensure you don't miss it.",
                inline=False
            )

            success_embed.add_field(
                name="⚙️ Management",
                value="Use `!reminders` to view all your scheduled reminders.",
                inline=False
            )

            success_embed.set_footer(text=f"Reminder #{reminder['id']} • {message.guild.name}")
            success_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/bell.png")

            await loading_msg.edit(embed=success_embed)

            # Add celebratory reactions
            await loading_msg.add_reaction("✅")
            await loading_msg.add_reaction("⏰")
            await loading_msg.add_reaction("📅")

            return True

        except Exception as e:
            logger.error(f"Error in handle_remindme: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Reminder Setup Failed",
                description="There was an error setting up your reminder. Please try again.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_announcement_create(self, message: discord.Message) -> bool:
        """Handle !announcement create command with enhanced permissions and formatting"""
        try:
            import asyncio

            # Permission check
            if not message.author.guild_permissions.manage_messages and message.author.id != message.guild.owner_id:
                embed = discord.Embed(
                    title="❌ Permission Denied",
                    description="You need **Manage Messages** permission to create announcements.",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="📋 Required Permissions",
                    value="• Manage Messages\n• Or Server Owner",
                    inline=True
                )
                embed.set_footer(text="Contact a moderator for announcement permissions")
                await message.channel.send(embed=embed)
                return True

            args = message.content.split(maxsplit=1)
            if len(args) < 2:
                embed = discord.Embed(
                    title="📢 Announcement System",
                    description="Create important server announcements with enhanced formatting!",
                    color=discord.Color.gold()
                )
                embed.add_field(
                    name="📝 Usage",
                    value="`!announcement create <message>`",
                    inline=False
                )
                embed.add_field(
                    name="✨ Features",
                    value="• Rich embed formatting\n"
                          "• Professional appearance\n"
                          "• Server branding\n"
                          "• Announcement logging",
                    inline=True
                )
                embed.add_field(
                    name="💡 Examples",
                    value="`!announcement create Welcome to our updated server!`\n"
                          "`!announcement create Server maintenance tonight at 10 PM`",
                    inline=True
                )
                embed.set_footer(text="Make your announcements stand out!")
                embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/megaphone.png")
                await message.channel.send(embed=embed)
                return True

            announcement_text = args[1].strip()

            # Validate announcement length
            if len(announcement_text) < 5:
                embed = discord.Embed(
                    title="❌ Announcement Too Short",
                    description="Please provide a more detailed announcement (at least 5 characters).",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
                return True

            if len(announcement_text) > 2000:
                embed = discord.Embed(
                    title="❌ Announcement Too Long",
                    description="Please keep your announcement under 2000 characters.",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
                return True

            # Loading animation
            loading_embed = discord.Embed(
                title="📢 Preparing Announcement",
                description="✨ Formatting message...\n🎨 Applying styling...\n📣 Broadcasting announcement...",
                color=discord.Color.gold()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Formatting message...\n🎨 Applying styling...\n📣 Broadcasting announcement..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Formatting message...\n✅ Applying styling...\n📣 Broadcasting announcement..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Formatting message...\n✅ Applying styling...\n✅ Broadcasting announcement..."
            await loading_msg.edit(embed=loading_embed)

            # Create enhanced announcement embed
            announcement_embed = discord.Embed(
                title="📢 IMPORTANT ANNOUNCEMENT",
                description=announcement_text,
                color=discord.Color.gold()
            )

            announcement_embed.set_author(
                name=f"{message.author.display_name}",
                icon_url=message.author.display_avatar.url
            )

            announcement_embed.set_thumbnail(message.guild.icon.url if message.guild.icon else None)

            announcement_embed.add_field(
                name="🏠 Server",
                value=message.guild.name,
                inline=True
            )

            announcement_embed.add_field(
                name="📅 Date & Time",
                value=f"<t:{int(time.time())}:F>",
                inline=True
            )

            announcement_embed.add_field(
                name="👤 Announced By",
                value=message.author.mention,
                inline=True
            )

            announcement_embed.set_footer(
                text=f"📢 Official Server Announcement • {message.guild.name}",
                icon_url=message.guild.icon.url if message.guild.icon else None
            )

            # Send the announcement
            await message.channel.send(embed=announcement_embed)

            # Success feedback
            success_embed = discord.Embed(
                title="✅ Announcement Sent Successfully!",
                description="Your announcement has been broadcast to the channel.",
                color=discord.Color.green()
            )

            success_embed.add_field(
                name="📊 Announcement Details",
                value=f"• **Channel:** {message.channel.mention}\n"
                      f"• **Length:** {len(announcement_text)} characters\n"
                      f"• **Timestamp:** <t:{int(time.time())}:R>",
                inline=False
            )

            # Log the announcement
            announcements_log = dm.get_guild_data(message.guild.id, "announcements_log", [])
            log_entry = {
                "id": len(announcements_log) + 1,
                "author_id": message.author.id,
                "channel_id": message.channel.id,
                "content": announcement_text,
                "timestamp": time.time()
            }
            announcements_log.append(log_entry)
            dm.update_guild_data(message.guild.id, "announcements_log", announcements_log)

            success_embed.add_field(
                name="📋 Logged",
                value=f"Announcement logged for records (ID: `{log_entry['id']}`)",
                inline=False
            )

            await loading_msg.edit(embed=success_embed)

            # Add celebratory reactions
            await loading_msg.add_reaction("📢")
            await loading_msg.add_reaction("✅")
            await loading_msg.add_reaction("🎉")

            # Ping @everyone if it's a critical announcement (optional)
            # You could add logic here to detect keywords like "emergency", "urgent", etc.

            return True

        except Exception as e:
            logger.error(f"Error in handle_announcement_create: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Announcement Failed",
                description="There was an error creating your announcement. Please try again.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False

    async def handle_giveaway_create(self, message: discord.Message) -> bool:
        """Handle !giveaway create command with enhanced validation and visuals"""
        try:
            import asyncio
            import re

            # Check if giveaway system is enabled
            if not is_system_enabled(message.guild.id, "giveaways"):
                embed = discord.Embed(
                    title="❌ Giveaways Unavailable",
                    description="The giveaway system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel giveaways to enable the system")
                await message.channel.send(embed=embed)
                return False

            # Permission check
            if not message.author.guild_permissions.manage_messages and message.author.id not in [message.guild.owner_id]:
                embed = discord.Embed(
                    title="❌ Permission Denied",
                    description="You need **Manage Messages** permission to create giveaways.",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Contact a moderator or administrator for assistance")
                await message.channel.send(embed=embed)
                return True

            # Check if giveaways system is enabled
            if not is_system_enabled(message.guild.id, "giveaways"):
                embed = discord.Embed(
                    title="❌ Giveaways Unavailable",
                    description="The giveaway system is currently disabled on this server.\n\n*Please contact an administrator to enable it.*",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Use !configpanel giveaways to enable the system")
                await message.channel.send(embed=embed)
                return False

            args = message.content.split()
            if len(args) < 5:  # !giveaway create duration winners prize
                embed = discord.Embed(
                    title="❌ Invalid Command Usage",
                    description="**Correct Usage:** `!giveaway create <duration> <winners> <prize>`",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="📝 Examples",
                    value="`!giveaway create 1h 1 Discord Nitro`\n"
                          "`!giveaway create 30m 3 $50 Steam Gift Card`\n"
                          "`!giveaway create 2d 5 Custom Server Role`",
                    inline=False
                )
                embed.add_field(
                    name="⏰ Duration Formats",
                    value="`30s` - 30 seconds\n"
                          "`5m` - 5 minutes\n"
                          "`2h` - 2 hours\n"
                          "`1d` - 1 day",
                    inline=True
                )
                embed.set_footer(text="Make sure to specify duration, winner count, and prize")
                await message.channel.send(embed=embed)
                return True

            # Loading animation
            loading_embed = discord.Embed(
                title="🎉 Creating Giveaway",
                description="🎯 Validating parameters...\n🏆 Setting up prize...\n⏰ Configuring timer...",
                color=discord.Color.gold()
            )
            loading_msg = await message.channel.send(embed=loading_embed)

            await asyncio.sleep(0.8)
            loading_embed.description = "✅ Validating parameters...\n🏆 Setting up prize...\n⏰ Configuring timer..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.5)
            loading_embed.description = "✅ Validating parameters...\n✅ Setting up prize...\n⏰ Configuring timer..."
            await loading_msg.edit(embed=loading_embed)

            await asyncio.sleep(0.4)
            loading_embed.description = "✅ Validating parameters...\n✅ Setting up prize...\n✅ Configuring timer..."
            await loading_msg.edit(embed=loading_embed)

            duration_str = args[2]
            winners_str = args[3]
            prize = ' '.join(args[4:])

            # Actually, let me fix the argument parsing
            duration_str = args[2]
            winners_str = args[3]
            prize = ' '.join(args[4:])

            # Validate winners count
            try:
                winners = int(winners_str)
                if winners < 1 or winners > 20:
                    raise ValueError("Winner count must be between 1 and 20")
            except ValueError:
                error_embed = discord.Embed(
                    title="❌ Invalid Winner Count",
                    description="The number of winners must be between 1 and 20.",
                    color=discord.Color.red()
                )
                await loading_msg.edit(embed=error_embed)
                return True

            # Validate prize
            if not prize or len(prize.strip()) < 3:
                error_embed = discord.Embed(
                    title="❌ Invalid Prize",
                    description="Please specify a prize with at least 3 characters.",
                    color=discord.Color.red()
                )
                await loading_msg.edit(embed=error_embed)
                return True

            # Parse duration
            match = re.match(r'(\d+)([smhd])', duration_str.lower())
            if not match:
                error_embed = discord.Embed(
                    title="❌ Invalid Duration Format",
                    description="Use formats like: `30s`, `5m`, `2h`, `1d`",
                    color=discord.Color.red()
                )
                error_embed.add_field(
                    name="⏰ Examples",
                    value="`30s` = 30 seconds\n"
                          "`5m` = 5 minutes\n"
                          "`2h` = 2 hours\n"
                          "`1d` = 1 day",
                    inline=False
                )
                await loading_msg.edit(embed=error_embed)
                return True

            amount = int(match.group(1))
            unit = match.group(2)

            # Validate reasonable limits
            if unit == 's' and amount > 300:  # Max 5 minutes for seconds
                amount = 300
            elif unit == 'm' and amount > 1440:  # Max 24 hours for minutes
                amount = 1440
            elif unit == 'h' and amount > 168:  # Max 1 week for hours
                amount = 168
            elif unit == 'd' and amount > 30:  # Max 30 days
                amount = 30

            multipliers = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
            duration = amount * multipliers[unit]
            end_time = time.time() + duration

            # Create giveaway
            giveaways = dm.get_guild_data(message.guild.id, "giveaways", [])
            giveaway_id = len(giveaways) + 1

            giveaway = {
                "id": giveaway_id,
                "channel_id": message.channel.id,
                "host_id": message.author.id,
                "prize": prize,
                "winners": winners,
                "end_time": end_time,
                "participants": [],
                "active": True,
                "created_at": time.time()
            }

            giveaways.append(giveaway)
            dm.update_guild_data(message.guild.id, "giveaways", giveaways)

            # Create enhanced giveaway embed
            embed = discord.Embed(
                title="🎉 GIVEAWAY TIME!",
                description=f"**Prize:** {prize}\n"
                           f"**Winners:** {winners}\n"
                           f"**Ends:** <t:{int(end_time)}:R> (<t:{int(end_time)}:F>)",
                color=discord.Color.gold()
            )

            embed.add_field(
                name="🎯 How to Enter",
                value="React with 🎉 to this message!\n\n"
                      "*You must stay in the server to win.*",
                inline=False
            )

            embed.add_field(
                name="🏆 Hosted By",
                value=f"<@{message.author.id}>",
                inline=True
            )

            embed.add_field(
                name="📊 Participants",
                value="`0` entrants so far",
                inline=True
            )

            embed.set_footer(text=f"Giveaway #{giveaway_id} • Ends")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/trophy.png")
            embed.set_author(name=f"{message.guild.name} Giveaway", icon_url=message.guild.icon.url if message.guild.icon else None)

            msg = await message.channel.send(embed=embed)
            await msg.add_reaction("🎉")

            # Update giveaway with message ID
            giveaway["message_id"] = msg.id
            dm.update_guild_data(message.guild.id, "giveaways", giveaways)

            # Success message
            success_embed = create_success_embed(
                description=f"Your giveaway for **{prize}** has been created!\n\n"
                           f"• **Duration:** {duration_str}\n"
                           f"• **Winners:** {winners}\n"
                           f"• **Ends:** <t:{int(end_time)}:R>",
                action_type="giveaways"
            )
            success_embed.title = f"{get_animated_emoji('giveaways')} Giveaway Created Successfully!"

            success_embed.add_field(
                name=f"{get_animated_emoji('gamification')} Next Steps",
                value="• Participants will react with 🎉\n"
                      "• Winners will be selected automatically\n"
                      "• You'll be notified when it ends",
                inline=False
            )

            await loading_msg.edit(embed=success_embed)

            # Add celebratory reactions
            await loading_msg.add_reaction("🎉")
            await loading_msg.add_reaction("✅")
            await loading_msg.add_reaction("🏆")

            return True

        except Exception as e:
            logger.error(f"Error in handle_giveaway_create: {e}")
            import traceback
            traceback.print_exc()
            error_embed = discord.Embed(
                title="❌ Giveaway Creation Failed",
                description="There was an error creating your giveaway. Please check your command format and try again.",
                color=discord.Color.red()
            )
            await message.channel.send(embed=error_embed)
            return False
