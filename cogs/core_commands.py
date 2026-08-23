import os
import difflib
import discord
from discord import app_commands
from discord.ext import commands
import logging
import aiohttp
from data_manager import dm
from ai_providers import AIProviderRegistry
from typing import List, Optional
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CoreCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.providers = AIProviderRegistry()

    # ------------------------------------------------------------------
    # AI config helpers
    # ------------------------------------------------------------------

    PROVIDER_CHOICES = [
        app_commands.Choice(name="🌐 OpenRouter (Universal)", value="openrouter"),
        app_commands.Choice(name="🤖 OpenAI", value="openai"),
        app_commands.Choice(name="✨ Google Gemini", value="gemini"),
        app_commands.Choice(name="🧠 Anthropic Claude", value="anthropic"),
        app_commands.Choice(name="⚡ Groq (Ultra-Fast)", value="groq"),
        app_commands.Choice(name="🌬️ Mistral AI", value="mistral"),
        app_commands.Choice(name="🐋 DeepSeek", value="deepseek"),
        app_commands.Choice(name="🧭 Alibaba DashScope (Qwen)", value="dashscope"),
        app_commands.Choice(name="🧮 Cerebras", value="cerebras"),
        app_commands.Choice(name="🔺 SambaNova", value="sambanova"),
        app_commands.Choice(name="🤝 Together AI", value="together"),
    ]

    def _active_provider(self, guild_id: int) -> str:
        """The provider requests will actually use (mirrors AIClient resolution:
        /config provider choice > key-store marker > env default)."""
        stored = dm.get_guild_api_key(guild_id) or {}
        return (
            dm.get_guild_data(guild_id, "active_provider")
            or stored.get("provider")
            or (getattr(self.bot.ai, "default_provider", None) if self.bot.ai else None)
            or "openrouter"
        )

    def _active_model(self, guild_id: int) -> str:
        custom = dm.get_guild_data(guild_id, "custom_model", None)
        if custom:
            return custom
        env_model = os.getenv("AI_MODEL", "")
        return env_model or self.providers.default_model(self._active_provider(guild_id))

    def _provider_key(self, guild_id: int, provider: str) -> Optional[str]:
        """Key that would be used for a provider: guild-stored first, env fallback second."""
        entry = dm.get_guild_api_key(guild_id, provider=provider)
        if isinstance(entry, dict) and entry.get("api_key"):
            return entry["api_key"]
        if provider == getattr(self.bot.ai, "default_provider", None):
            key = getattr(self.bot.ai, "default_api_key", "")
            if key and len(key) > 10:
                return key
        return None

    async def _known_models(self, guild_id: int, provider: str) -> List[str]:
        """Live model list from the provider when possible, curated list otherwise."""
        key = self._provider_key(guild_id, provider)
        if key:
            live = await self.providers.list_models(provider, key)
            if live:
                return live
        return self.providers.curated_models(provider)

    @staticmethod
    def _admin_check(interaction: discord.Interaction) -> bool:
        return bool(interaction.user.guild_permissions.administrator or
                    interaction.user.id == interaction.guild.owner_id)

    # ------------------------------------------------------------------
    # /config provider
    # ------------------------------------------------------------------

    config = app_commands.Group(name="config", description="Configure bot settings")

    async def config_model_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for model selection: live models from the active
        provider when available, curated fallback list otherwise."""
        if not interaction.guild:
            return []
        guild_id = interaction.guild.id
        provider = self._active_provider(guild_id)
        try:
            available_models = await self._known_models(guild_id, provider)
        except Exception:
            available_models = self.providers.curated_models(provider)

        filtered = [m for m in available_models if current.lower() in m.lower()]
        if not filtered and current:
            filtered = available_models[:25]
        return [app_commands.Choice(name=m[:100], value=m) for m in filtered[:25]]

    @config.command(name="model", description="Set the AI model used for this server")
    @app_commands.describe(
        model="Model name (use autocomplete — fetched live from your provider)",
        force="Accept the model without validating it against the provider",
    )
    @app_commands.autocomplete(model=config_model_autocomplete)
    async def config_model(self, interaction: discord.Interaction, model: str, force: Optional[bool] = False):
        await interaction.response.defer(ephemeral=True)
        if not self._admin_check(interaction):
            await interaction.followup.send("❌ Only Administrators can change configuration.", ephemeral=True)
            return

        model = model.strip()
        provider = self._active_provider(interaction.guild.id)

        # Embedding/utility models categorically cannot generate responses
        if not AIProviderRegistry.is_chat_model(model):
            await interaction.followup.send(
                f"❌ `{model}` is an embedding/utility model — it can't generate chat or JSON "
                f"responses. Pick a chat model via the autocomplete (live models are filtered "
                f"automatically), e.g. `{self.providers.default_model(provider)}`.",
                ephemeral=True,
            )
            return

        if not force:
            known = await self._known_models(interaction.guild.id, provider)
            if known and model not in known:
                suggestions = difflib.get_close_matches(model, known, n=4, cutoff=0.25)
                msg = f"❌ `{model}` is not in {self.providers.display(provider)}'s model list."
                if suggestions:
                    msg += "\nDid you mean: " + ", ".join(f"`{s}`" for s in suggestions) + "?"
                msg += f"\nUse `force:True` to set it anyway (custom/finetuned models)."
                await interaction.followup.send(msg, ephemeral=True)
                return

        dm.update_guild_data(interaction.guild.id, "custom_model", model)
        embed = discord.Embed(
            title="✅ Model updated",
            description=f"**{self.providers.display(provider)}** will now use `{model}`.",
            color=discord.Color.green(),
        )
        key = self._provider_key(interaction.guild.id, provider)
        if not key:
            embed.set_footer(text=f"⚠️ No API key stored for {provider} — use /config key")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @config.command(name="provider", description="Set the active AI provider for this server")
    @app_commands.choices(provider=PROVIDER_CHOICES)
    @app_commands.describe(provider="Which AI provider to route requests through")
    async def config_provider(self, interaction: discord.Interaction, provider: str):
        await interaction.response.defer(ephemeral=True)
        if not self._admin_check(interaction):
            await interaction.followup.send("❌ Only Administrators can change configuration.", ephemeral=True)
            return
        if not self.providers.exists(provider):
            await interaction.followup.send(f"❌ Unknown provider `{provider}`.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        previous = self._active_provider(guild_id)
        dm.update_guild_data(guild_id, "active_provider", provider)

        # Keep the current model if it looks compatible with the new provider;
        # otherwise reset to that provider's default model.
        current_model = self._active_model(guild_id)
        curated = [m.lower() for m in self.providers.curated_models(provider)]

        def model_compatible(current: str, provider_models: List[str]) -> bool:
            if not provider_models:
                return True
            cur = current.lower()
            for pm in provider_models:
                tokens = [t for t in pm.replace("/", ".").replace("_", ".").replace("-", ".").split(".")
                          if len(t) >= 3 and not t.isdigit()]
                if any(tok in cur for tok in tokens):
                    return True
            return False

        if model_compatible(current_model, curated):
            model_note = f"Kept current model: `{current_model}`"
        else:
            default_model = self.providers.default_model(provider)
            dm.update_guild_data(guild_id, "custom_model", default_model)
            model_note = f"Model set to the {provider} default: `{default_model}` (previous `{current_model}` wasn't valid on {provider})"

        key = self._provider_key(guild_id, provider)
        embed = discord.Embed(
            title=f"{self.providers.display(provider)} is now active",
            color=discord.Color.green() if key else discord.Color.orange(),
        )
        embed.add_field(name="Routing", value=f"~~{previous}~~ → **{provider}**", inline=False)
        embed.add_field(name="Model", value=model_note, inline=False)
        if key:
            embed.add_field(name="API Key", value=f"✅ Using stored/env key {AIProviderRegistry.mask_key(key)}", inline=False)
        else:
            signup = self.providers.key_signup_url(provider)
            embed.add_field(
                name="⚠️ No API key for this provider",
                value=f"Set one with `/config key` ([get a key]({signup})). "
                      f"Requests will fall back to other configured keys until then.",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @config.command(name="key", description="Store your own API key for a provider (encrypted + auto-tested)")
    @app_commands.choices(provider=PROVIDER_CHOICES)
    @app_commands.describe(
        provider="Provider this key belongs to",
        api_key="Paste the secret API key — it is encrypted at rest and never shown again",
    )
    async def config_key(self, interaction: discord.Interaction, provider: str, api_key: str):
        await interaction.response.defer(ephemeral=True)
        if not self._admin_check(interaction):
            await interaction.followup.send("❌ Only Administrators can change configuration.", ephemeral=True)
            return
        if not self.providers.exists(provider):
            await interaction.followup.send(f"❌ Unknown provider `{provider}`.", ephemeral=True)
            return

        api_key = api_key.strip()
        valid, reason = self.providers.validate_key_format(provider, api_key)
        if not valid:
            signup = self.providers.key_signup_url(provider)
            await interaction.followup.send(
                f"❌ {reason}\nGet a valid key here: <{signup}>\n**Nothing was changed.**",
                ephemeral=True,
            )
            return

        # Test FIRST — an invalid key must never be stored or activated
        ok, detail, latency_ms = await self.providers.test_key(provider, api_key)
        masked = AIProviderRegistry.mask_key(api_key)
        if not ok:
            embed = discord.Embed(
                title=f"❌ API key test failed — {self.providers.display(provider)}",
                description=f"Provider: **{provider}**\nReason: *{detail}*\n\n**Nothing was changed.**",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="What to do",
                value=f"Double-check the key at <{self.providers.key_signup_url(provider)}> and try again.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Verified — now encrypt, store, and activate
        dm.set_guild_api_key(interaction.guild.id, api_key, provider)
        dm.update_guild_data(interaction.guild.id, "active_provider", provider)

        embed = discord.Embed(
            title=f"✅ Connected — {self.providers.display(provider)}",
            description=f"Key {masked} verified and saved encrypted ({detail}).",
            color=discord.Color.green(),
        )
        embed.add_field(name="Latency", value=f"{latency_ms:.0f} ms", inline=True)
        embed.add_field(name="Active model", value=f"`{self._active_model(interaction.guild.id)}`", inline=True)
        embed.set_footer(text="This key is now the primary AI backend for this server.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @config.command(name="clearkey", description="Remove a stored API key for this server")
    @app_commands.choices(provider=PROVIDER_CHOICES)
    @app_commands.describe(provider="Provider whose stored key should be removed")
    async def config_clearkey(self, interaction: discord.Interaction, provider: str):
        await interaction.response.defer(ephemeral=True)
        if not self._admin_check(interaction):
            await interaction.followup.send("❌ Only Administrators can change configuration.", ephemeral=True)
            return

        removed = dm.clear_guild_api_key(interaction.guild.id, provider)
        if removed:
            remaining = [
                p for p in (dm.get_guild_api_key(interaction.guild.id) or {}).get("providers", {})
                if self._provider_key(interaction.guild.id, p)
            ]
            note = f"Remaining keys: {', '.join(remaining)}" if remaining else "No other keys stored — the bot-wide env key will be used if present."
            await interaction.followup.send(f"🗑️ Removed the stored **{provider}** key.\n{note}", ephemeral=True)
        else:
            await interaction.followup.send(f"ℹ️ No stored key found for **{provider}** on this server.", ephemeral=True)

    @config.command(name="status", description="Show the full AI configuration for this server")
    async def config_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        provider = self._active_provider(guild_id)
        model = self._active_model(guild_id)

        env_key = getattr(self.bot.ai, "default_api_key", "") if self.bot.ai else ""
        embed = discord.Embed(title="⚙️ AI Configuration", color=discord.Color.blue())
        embed.add_field(name="Active provider", value=self.providers.display(provider), inline=True)
        embed.add_field(name="Model", value=f"`{model}`", inline=True)
        embed.add_field(name="Memory depth", value=str(dm.get_guild_data(guild_id, "memory_depth", 20)), inline=True)

        stored = dm.get_guild_api_key(guild_id) or {}
        providers_cfg = stored.get("providers", {}) if isinstance(stored, dict) else {}
        if providers_cfg:
            lines = []
            for pname, cfg in providers_cfg.items():
                key = (cfg or {}).get("api_key")
                marker = "🟢" if pname == provider else "⚪"
                lines.append(f"{marker} {self.providers.display(pname)} — {AIProviderRegistry.mask_key(key) if key else '*(empty)*'}")
            embed.add_field(name="Stored keys", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Stored keys", value="None — using bot-wide environment key", inline=False)

        env_state = f"✅ configured ({getattr(self.bot.ai, 'default_provider', '?')})" if env_key else "❌ not set"
        embed.add_field(name="Bot-wide env fallback", value=env_state, inline=False)
        embed.set_footer(text="/config provider · /config model · /config key · /config test · /config clearkey")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @config.command(name="test", description="Run full AI diagnostics for this server")
    async def config_test(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self._admin_check(interaction):
            await interaction.followup.send("❌ Only Administrators can run tests.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        provider = self._active_provider(guild_id)
        stored_model = self._active_model(guild_id)
        key = self._provider_key(guild_id, provider)
        registry = self.providers
        rows: list[tuple[str, bool, str]] = []

        def add(label: str, ok: bool, detail: str = ""):
            rows.append((label, ok, detail))

        # Test the model that will ACTUALLY run: cross-provider names are
        # coerced exactly like production requests do.
        try:
            model = await self.bot.ai._coerce_model_for_provider(guild_id, provider, key) \
                    or registry.default_model(provider)
        except Exception:
            model = stored_model
        def add(label: str, ok: bool, detail: str = ""):
            rows.append((label, ok, detail))

        # 1. Guild configuration
        from core.guild_ai_config import GuildAIConfig
        gcfg = GuildAIConfig.load(guild_id)
        add("Guild config", True,
            f"provider={gcfg.provider or 'auto'} · model={gcfg.model or 'default'} · "
            f"max_tokens={gcfg.max_tokens} · agent={'on' if gcfg.agent_enabled else 'off'}")

        # 2. API key
        if not key:
            add("API key", False, f"no key stored or env-configured for {provider}")
        else:
            ok, detail, latency = await registry.test_key(provider, key)
            add("API key", ok, f"{detail} ({latency:.0f} ms)" if latency else detail)

        # 3. Model availability
        known = None
        if key:
            known = await registry.list_models(provider, key)
        if known:
            stored_in = stored_model in known
            add("Model available", model in known,
                f"stored `{stored_model}`: "
                + ("in catalog" if stored_in else
                   f"NOT in catalog — runtime will use `{model}`")
                + (f" · effective `{model}` in catalog" if model in known else ""))
        else:
            add("Model available", True, "catalog unavailable — skipping strict check")

        # 4. Real completion through the canonical pipeline
        started = time.perf_counter()
        if not key:
            add("Text response", False, "skipped — no key")
        else:
            chat_url = registry.chat_base_url(provider)
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: MIRO ONLINE"}],
                "max_tokens": 20,
            }
            try:
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(chat_url, json=payload,
                                            headers=registry._auth_headers(provider, key)) as resp:
                        latency = (time.perf_counter() - started) * 1000
                        body = await resp.json(content_type=None)
                        if resp.status == 200:
                            from core.ai_response import normalize_provider_response, watchdog_check
                            norm = normalize_provider_response(body, provider=provider, model=model)
                            ok, why = watchdog_check(norm.text)
                            snippet = norm.text.strip()[:80] or why
                            add("Text response", norm.ok and ok,
                                f"{norm.status.value} · {latency:.0f} ms · “{snippet}”")
                        else:
                            err = ""
                            if isinstance(body, dict):
                                err = str((body.get("error") or {}).get("message", ""))[:120]
                            add("Text response", False, f"HTTP {resp.status}: {err or 'unknown'}")
            except Exception as e:
                add("Text response", False, f"connection failed: {str(e)[:120]}")

        # 5. Tool/agent capability (provider supports function calling?)
        tool_capable = provider in ("openai", "openrouter", "groq", "mistral",
                                    "deepseek", "together", "cerebras", "sambanova")
        agent_detail = "supported" if tool_capable else "not supported by this provider"
        if not gcfg.agent_enabled:
            agent_detail += " · agent disabled in guild ai_config"
        add("Tools / Agent", tool_capable and gcfg.agent_enabled, agent_detail)

        # Fallback chain visibility
        chain = getattr(self.bot.ai, "_get_all_guild_keys", lambda gid: [])(guild_id)
        fallback_names = [c["provider"] for c in chain][1:]
        add("Fallbacks", bool(fallback_names),
            ", ".join(fallback_names) if fallback_names else "none configured")

        # Render
        lines = []
        all_ok = True
        for label, ok, detail in rows:
            mark = "🟢" if ok else "🔴"
            if not ok:
                all_ok = False
            lines.append(f"{mark} **{label}** — {detail}" if detail else f"{mark} **{label}**")
        embed = discord.Embed(
            title="🤖 Miro AI Diagnostics",
            description="\n".join(lines)[:4000],
            color=discord.Color.green() if all_ok else discord.Color.orange(),
        )
        embed.add_field(name="Route", value=f"{registry.display(provider)} · `{model}`", inline=False)
        embed.set_footer(text=f"Guild: {interaction.guild.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

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


