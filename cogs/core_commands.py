import os
import discord
from discord import app_commands
from discord.ext import commands
import logging
from data_manager import dm
from typing import List
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class CoreCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Model lists for each provider - Real models only
    COMMON_MODELS = [
        "llama-3.3-70b-versatile", "llama-3.3-8b-instant",
        "llama-3.2-1b-preview", "llama-3.2-3b-preview", "llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview",
        "llama-3.1-8b-instant", "llama-guard-3-8b", "whisper-large-v3-turbo", "whisper-large-v3",
        "gemma2-9b-it", "gemma-7b-it", "mixtral-8x7b-32768",
        "qwen-2.5-coder-32b-instruct", "qwen-2.5-32b-instruct", "qwen-2.5-72b-instruct",
        "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest",
        "deepseek-v3", "deepseek-r1",
        "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash",
        "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo",
        "claude-3-5-sonnet-20240620", "claude-3-opus-20240229"
    ]
    
    MODEL_CHOICES = {
        "openrouter": ["openai/gpt-4o", "anthropic/claude-3-5-sonnet", "google/gemini-2.0-flash", "meta-llama/llama-3.3-70b"],
        "openai": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
        "gemini": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash"],
        "groq": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        "mistral": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest"],
        "deepseek": ["deepseek-chat", "deepseek-coder"],
        "anthropic": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
        "dashscope": ["qwen-turbo", "qwen-plus", "qwen-max"]
    }
    
    def get_default_model(self, provider: str) -> str:
        """Get the default model for a provider"""
        defaults = {
            "openrouter": "openai/gpt-4o",
            "openai": "gpt-4o",
            "gemini": "gemini-1.5-flash",
            "groq": "llama-3.3-70b-versatile",
            "mistral": "mistral-small-latest",
            "deepseek": "deepseek-chat",
            "anthropic": "claude-3-5-sonnet-20240620",
            "dashscope": "qwen-turbo"
        }
        return defaults.get(provider, "gpt-4o-mini")

    config = app_commands.Group(name="config", description="Configure bot settings")

    async def config_model_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for model selection based on active provider"""
        # Get the guild's active provider
        current_config = dm.get_guild_api_key(interaction.guild.id) if interaction.guild else None
        provider = current_config.get("provider", "openrouter") if current_config else "openrouter"

        # Get models for this provider
        available_models = self.MODEL_CHOICES.get(provider, self.COMMON_MODELS)

        # Filter models based on current input
        filtered_models = [m for m in available_models if current.lower() in m.lower()]

        # Return up to 25 choices
        return [app_commands.Choice(name=model, value=model) for model in filtered_models[:25]]

    @config.command(name="model", description="Set the AI model")
    @app_commands.describe(model="Model name (e.g. gpt-4, claude-3)")
    @app_commands.autocomplete(model=config_model_autocomplete)
    async def config_model(self, interaction: discord.Interaction, model: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return

        dm.update_guild_data(interaction.guild.id, "custom_model", model)
        await interaction.response.send_message(f"✅ AI model set to **{model}** for this server.", ephemeral=True)

    @config.command(name="provider", description="Set the active AI provider")
    @app_commands.choices(provider=[
        app_commands.Choice(name="OpenRouter (Universal)", value="openrouter"),
        app_commands.Choice(name="OpenAI", value="openai"),
        app_commands.Choice(name="Google Gemini", value="gemini"),
        app_commands.Choice(name="Groq (Ultra-Fast)", value="groq"),
        app_commands.Choice(name="Mistral AI", value="mistral"),
        app_commands.Choice(name="DeepSeek", value="deepseek"),
        app_commands.Choice(name="Anthropic", value="anthropic"),
        app_commands.Choice(name="Alibaba DashScope (Qwen)", value="dashscope")
    ])
    async def config_provider(self, interaction: discord.Interaction, provider: str):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("Only Administrators can change configuration.", ephemeral=True)
            return
        dm.update_guild_data(interaction.guild.id, "active_provider", provider)
        # Reset custom_model to provider's default
        default_model = self.get_default_model(provider)
        dm.update_guild_data(interaction.guild.id, "custom_model", default_model)
        await interaction.followup.send(f"✅ AI provider switched to **{provider}** and model reset to **{default_model}**.", ephemeral=True)

    @config.command(name="key", description="Set your own API key for a specific provider")
    @app_commands.choices(provider=[
        app_commands.Choice(name="OpenRouter", value="openrouter"),
        app_commands.Choice(name="OpenAI", value="openai"),
        app_commands.Choice(name="Gemini", value="gemini"),
        app_commands.Choice(name="Groq", value="groq"),
        app_commands.Choice(name="Mistral", value="mistral"),
        app_commands.Choice(name="DeepSeek", value="deepseek"),
        app_commands.Choice(name="Anthropic", value="anthropic"),
        app_commands.Choice(name="Alibaba DashScope (Qwen)", value="dashscope")
    ])
    @app_commands.describe(api_key="Your API key for this provider")
    async def config_key(self, interaction: discord.Interaction, provider: str, api_key: str):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("Only Administrators can change configuration.", ephemeral=True)
            return
        
        dm.set_guild_api_key(interaction.guild.id, api_key, provider)
        await interaction.followup.send(f"✅ API key for **{provider}** has been updated and encrypted.", ephemeral=True)

    @config.command(name="prefix", description="Set the server command prefix")
    @app_commands.describe(prefix="New prefix character (max 5 chars)")
    async def config_prefix(self, interaction: discord.Interaction, prefix: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
            
        if len(prefix) > 5:
            await interaction.response.send_message("❌ Prefix must be 5 characters or less.", ephemeral=True)
            return
            
        dm.update_guild_data(interaction.guild.id, "prefix", prefix)
        await interaction.response.send_message(f"✅ Server prefix set to **{prefix}**.", ephemeral=True)

    @config.command(name="sync", description="Force sync slash commands (Admin only)")
    async def config_sync(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can sync commands.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.tree.sync()
            await interaction.followup.send("✅ Slash commands synced successfully!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)

    @config.command(name="depth", description="Set memory depth")
    @app_commands.describe(depth="Number of messages to remember (5-100)")
    async def config_depth(self, interaction: discord.Interaction, depth: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
            
        if depth < 5 or depth > 100:
            await interaction.response.send_message("❌ Depth must be between 5 and 100.", ephemeral=True)
            return
            
        dm.update_guild_data(interaction.guild.id, "memory_depth", depth)
        await interaction.response.send_message(f"✅ Memory depth set to **{depth}**.", ephemeral=True)

    @app_commands.command(name="disable", description="Disable a bot feature or scheduled task")
    @app_commands.describe(feature="Feature or task to disable")
    async def disable_command(self, interaction: discord.Interaction, feature: str):
        """Disable a feature or scheduled task"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can disable features.", ephemeral=True)
            return

        # Check for scheduled tasks
        tasks = dm.load_json("ai_scheduled_tasks", default={})
        if feature in tasks and tasks[feature].get("guild_id") == interaction.guild.id:
            tasks[feature]["enabled"] = False
            dm.save_json("ai_scheduled_tasks", tasks)
            await interaction.response.send_message(f"✅ Disabled scheduled task: **{feature}**", ephemeral=True)
            return

        # Check for generic modules
        modules = ["leveling", "economy", "starboard", "anti_raid", "auto_publisher", "auto_announcer", "welcome"]
        if feature.lower() in modules:
            dm.update_guild_data(interaction.guild.id, f"{feature.lower()}_enabled", False)
            await interaction.response.send_message(f"✅ Disabled module: **{feature}**", ephemeral=True)
            # Update live status embed
            await self.bot.get_cog('AutoSetup').update_system_status_embed(interaction.guild.id)
            return

        await interaction.response.send_message(f"❌ Feature or task '**{feature}**' not found.", ephemeral=True)



async def setup(bot):
    await bot.add_cog(CoreCommands(bot))

    # Add connect_systems slash command
    @bot.tree.command(name="connect_systems", description="Create a connection between two systems (e.g., when member joins, send welcome message)")
    @app_commands.describe(
        source_system="The system that triggers the connection (e.g., verification, leveling)",
        trigger_event="The event that triggers the connection (e.g., member_join, level_up)",
        target_system="The system that performs the action (e.g., welcome, economy)",
        action="The action to perform (e.g., send_message, give_points)"
    )
    async def connect_systems_command(interaction: discord.Interaction, source_system: str, trigger_event: str, target_system: str, action: str):
        """Slash command for creating system connections"""
        # Check if user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You need Administrator permission to use this command.", ephemeral=True)
            return
            
        # Defer response as this might take a moment
        await interaction.response.defer(ephemeral=True)
        
        # Get available systems for validation
        available_systems = [
            "verification", "leveling", "economy", "welcome", "tickets", "appeals", 
            "staff_promo", "staff_shift", "staff_reviews", "anti_raid", "automod", 
            "warnings", "modmail", "auto_responder", "reminders", "giveaways",
            "events", "tournaments", "chat_channels", "starboard", "reaction_roles",
            "reaction_menus", "role_buttons", "logging", "mod_logging", "community_health",
            "conflict_resolution", "server_analytics", "intelligence", "gamification",
            "content_generator", "tournaments", "auto_setup", "guardian", "staff_extras"
        ]
        
        # Validate systems
        if source_system not in available_systems:
            await interaction.followup.send(f"❌ Invalid source system. Available systems: {', '.join(available_systems[:10])}...", ephemeral=True)
            return
            
        if target_system not in available_systems:
            await interaction.followup.send(f"❌ Invalid target system. Available systems: {', '.join(available_systems[:10])}...", ephemeral=True)
            return
        
        # Common trigger events by system
        trigger_events_map = {
            "verification": ["member_join", "verification_complete", "verification_failed"],
            "leveling": ["level_up", "xp_gain", "daily_xp_bonus"],
            "economy": ["daily_claimed", "work_completed", "crime_completed", "shop_purchase"],
            "welcome": ["member_join", "member_leave"],
            "tickets": ["ticket_created", "ticket_closed", "ticket_claimed"],
            "appeals": ["appeal_submitted", "appeal_approved", "appeal_denied"],
            "staff_promo": ["promotion_earned", "demotion_issued"],
            "staff_shift": ["shift_started", "shift_ended"],
            "staff_reviews": ["review_submitted", "review_completed"],
            "anti_raid": ["raid_detected", "mass_join_detected"],
            "automod": ["rule_triggered", "message_flagged"],
            "warnings": ["warning_issued", "warning_cleared"],
            "modmail": ["modmail_received", "modmail_closed"],
            "auto_responder": ["keyword_matched"],
            "reminders": ["reminder_triggered"],
            "giveaways": ["giveaway_ended", "giveaway_won"],
            "events": ["event_started", "event_ended", "event_joined"],
            "tournaments": ["tournament_started", "tournament_ended", "tournament_joined"],
            "chat_channels": ["message_received", "ai_response_generated"],
            "starboard": ["star_received"],
            "reaction_roles": ["role_assigned_via_reaction"],
            "reaction_menus": ["role_assigned_via_menu"],
            "role_buttons": ["role_assigned_via_button"],
            "logging": ["log_entry_created"],
            "mod_logging": ["mod_action_logged"],
            "community_health": ["health_report_generated"],
            "conflict_resolution": ["conflict_resolved", "mediation_completed"],
            "server_analytics": ["analytics_updated"],
            "intelligence": ["intelligence_generated"],
            "gamification": ["quest_completed", "daily_challenge_claimed"],
            "content_generator": ["content_generated"],
            "tournaments": ["tournament_started", "tournament_ended"],
            "auto_setup": ["setup_completed"],
            "guardian": ["threat_detected", "link_scanned"],
            "staff_extras": ["compliment_sent", "report_submitted"]
        }
        
        # Validate trigger event
        valid_triggers = trigger_events_map.get(source_system, [])
        if valid_triggers and trigger_event not in valid_triggers:
            await interaction.followup.send(f"❌ Invalid trigger event for {source_system}. Valid events: {', '.join(valid_triggers)}", ephemeral=True)
            return
            
        # Common actions by target system
        actions_map = {
            "verification": ["start_verification", "send_verification_dm"],
            "leveling": ["give_xp", "send_level_up_message"],
            "economy": ["give_points", "remove_points", "open_shop"],
            "welcome": ["send_welcome_message", "assign_welcome_role"],
            "tickets": ["create_ticket", "close_ticket", "notify_staff"],
            "appeals": ["create_appeal", "notify_appeal_team"],
            "staff_promo": ["promote_user", "demote_user", "notify_staff_promo"],
            "staff_shift": ["start_shift_tracking", "end_shift_tracking"],
            "staff_reviews": ["request_staff_review", "notify_review_team"],
            "anti_raid": ["trigger_lockdown", "notify_mods"],
            "automod": ["flag_message", "apply_automod_punishment"],
            "warnings": ["issue_warning", "clear_warnings"],
            "modmail": ["create_modmail_thread", "notify_modmail_team"],
            "auto_responder": ["send_auto_response"],
            "reminders": ["send_reminder"],
            "giveaways": ["create_giveaway", "end_giveaway", "pick_winner"],
            "events": ["create_event", "end_event", "notify_event_attendees"],
            "tournaments": ["create_tournament", "end_tournament", "notify_participants"],
            "chat_channels": ["send_ai_message", "start_ai_chat"],
            "starboard": ["add_to_starboard"],
            "reaction_roles": ["assign_reaction_role"],
            "reaction_menus": ["assign_menu_role"],
            "role_buttons": ["assign_button_role"],
            "logging": ["create_log_entry"],
            "mod_logging": ["log_mod_action"],
            "community_health": ["generate_health_report"],
            "conflict_resolution": ["initiate_conflict_resolution"],
            "server_analytics": ["generate_analytics_report"],
            "intelligence": ["generate_intelligence_report"],
            "gamification": ["create_quest", "start_daily_challenge"],
            "content_generator": ["generate_content"],
            "tournaments": ["create_tournament"],
            "auto_setup": ["run_auto_setup"],
            "guardian": ["scan_for_threats", "block_malicious_link"],
            "staff_extras": ["send_compliment", "process_user_report"]
        }
        
        # Validate action
        valid_actions = actions_map.get(target_system, [])
        if valid_actions and action not in valid_actions:
            await interaction.followup.send(f"❌ Invalid action for {target_system}. Valid actions: {', '.join(valid_actions)}", ephemeral=True)
            return
        
        # Execute the connect_systems action
        from actions import ActionHandler
        action_handler = ActionHandler(self.bot)
        
        params = {
            "source_system": source_system,
            "trigger_event": trigger_event,
            "target_system": target_system,
            "action": action,
            "parameters": {}
        }
        
        success, result = await action_handler.dispatch(interaction, "connect_systems", params)
        
        if success:
            embed = discord.Embed(
                title="✅ System Connection Created",
                description=f"**{source_system}** → **{target_system}**",
                color=discord.Color.green()
            )
            embed.add_field(name="Trigger Event", value=f"`{trigger_event}`", inline=True)
            embed.add_field(name="Action", value=f"`{action}`", inline=True)
            embed.set_footer(text="Use /configpanel to manage your connections")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            error_msg = result.get("error", "Unknown error") if result else "Unknown error"
            await interaction.followup.send(f"❌ Failed to create connection: {error_msg}", ephemeral=True)


