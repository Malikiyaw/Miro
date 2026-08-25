import os
import re
from typing import Dict, List, Any
import io
import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import datetime as dt
import random
import signal
import json
import time
from dotenv import load_dotenv
from data_manager import dm
from logger import logger
from task_scheduler import task_scheduler
import traceback

# Import all system modules
from modules import (
    economy, leveling, verification, anti_raid, guardian, welcome_leave,
    tickets, suggestions, reminders, giveaways, announcements, auto_responder,
    reaction_roles, reaction_menus, role_buttons, moderation, logging_mod,
    mod_logging, warnings, staff_promo, staff_shifts, staff_reviews,
    starboard, ai_chat, applications, appeals, modmail, auto_setup,
    config_panels, intelligence, gamification, tournaments,
    shop, automod, trigger_roles, auto_announcer, auto_publisher,
    server_analytics, content_generator, embed_system, community_health,
    conflict_resolution, events, staff_extras
)

load_dotenv()

class MiroBot(commands.Bot):
    def __init__(self, proxy: str = None, proxy_auth=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        intents.guilds = True
        intents.reactions = True
        intents.message_content = True

        super().__init__(
            command_prefix=self.get_dynamic_prefix,
            intents=intents,
            help_command=None,
            proxy=proxy,
            proxy_auth=proxy_auth,
        )

        # Never leave a slash command unanswered ("didn't respond in time")
        self.tree.on_error = self._on_app_command_error

        # Initialize all systems
        self.economy = economy.EconomySystem(self)
        self.leveling = leveling.LevelingSystem(self)
        self.verification = verification.VerificationSystem(self)
        self.anti_raid = anti_raid.AntiRaidSystem(self)
        self.guardian = guardian.GuardianSystem(self)
        self.welcome_leave = welcome_leave.WelcomeLeaveSystem(self)
        self.tickets = tickets.TicketSystem(self)
        self.suggestions = suggestions.SuggestionSystem(self)
        self.reminders = reminders.ReminderSystem(self)
        self.giveaways = giveaways.GiveawaySystem(self)
        self.announcements = announcements.AnnouncementSystem(self)
        self.auto_responder = auto_responder.AutoResponderSystem(self)
        self.reaction_roles = reaction_roles.ReactionRoleSystem(self)
        self.reaction_menus = reaction_menus.ReactionMenuSystem(self)
        self.role_buttons = role_buttons.RoleButtonSystem(self)
        self.moderation = moderation.ModerationSystem(self)
        self.logging_system = logging_mod.LoggingSystem(self)
        self.mod_logging = mod_logging.ModLoggingSystem(self)
        self.warnings = warnings.WarningSystem(self)
        self.staff_promo = staff_promo.StaffPromotionSystem(self)
        self.staff_shifts = staff_shifts.StaffShiftSystem(self)
        self.staff_reviews = staff_reviews.StaffReviewSystem(self)
        self.starboard = starboard.StarboardSystem(self)
        self.ai_chat = ai_chat.AIChatSystem(self)
        self.gamification = gamification.AdaptiveGamification(self)
        self.tournaments = tournaments.TournamentSystem(self)
        self.applications = applications.ApplicationSystem(self)
        self.appeals = appeals.AppealSystem(self)
        self.modmail = modmail.ModmailSystem(self)
        self.auto_setup = auto_setup.AutoSetupSystem(self)
        self.intelligence = intelligence.ServerIntelligence(self)

        # Additional systems
        self.shop = shop.Shop(self)
        self.automod = automod.AutoModSystem(self)
        self.trigger_roles = trigger_roles.TriggerRoles(self)
        self.auto_announcer = auto_announcer.AutoAnnouncer(self)
        self.auto_publisher = auto_publisher.AutoPublisher(self)
        self.content_generator = content_generator.ContentGenerator(self)
        self.embed_system = embed_system.EmbedSystem(self)
        self.community_health = community_health.CommunityHealth(self)
        self.conflict_resolution = conflict_resolution.ConflictResolution(self)
        self.event_scheduler = events.EventScheduler(self)
        self.staff_extras = staff_extras.StaffExtras(self)
        self.analytics = server_analytics.setup_analytics(self)

        # Task scheduler for reminders, giveaways, etc. (shared singleton)
        self.task_scheduler = task_scheduler

        # AI client + server introspection (consumed by many modules)
        from ai_client import AIClient
        from server_query import ServerQueryEngine
        from actions import ActionHandler
        from vector_memory import vector_memory
        self.ai = AIClient(self, os.getenv("AI_API_KEY", ""))
        self.server_query = ServerQueryEngine(self)
        self.action_handler = ActionHandler(self)
        self.vector_memory = vector_memory

        # V9 native tool-calling: advertise real Miro tools to every provider
        # request so the model can CALL them instead of writing how-to guides.
        from agent.native_tools import install_on_bot
        install_on_bot(self)

        # Core architecture layer (V2 plan): event bus, audit, rate limits,
        # permissions, health watchdogs. Wraps existing systems — no duplicates.
        from core.event_bus import EventBus
        from core.audit import AuditLog
        from core.rate_limiter import RateLimiter
        from core.permissions import PermissionEngine
        from core.permissions.roles import RoleHierarchy
        from core.health import HealthMonitor
        from core.analytics_stream import AnalyticsCollector
        self.event_bus = EventBus()
        self.audit_log = AuditLog()
        self.rate_limiter = RateLimiter()
        self.permission_engine = PermissionEngine(RoleHierarchy())
        self.health = HealthMonitor()
        self.analytics_collector = AnalyticsCollector(self.event_bus)

        # State for immortal persistence
        self._background_tasks_started = False
        self._persistent_views_registered = False

    async def get_dynamic_prefix(self, bot, message):
        if not message.guild:
            return "!"
        return dm.get_guild_data(message.guild.id, "prefix", "!")

    async def setup_hook(self):
        """Initialize bot systems and restore immortal state."""
        logger.info("Starting Miro Bot setup...")

        # Load slash commands
        await self.load_extension('modules.slash_commands')

        # Load additional cogs (modules.proactive_assist is the AI advisor;
        # cogs.proactive_assist is a redundant duplicate loop, so it is not loaded)
        for cog in ("cogs.core_commands", "cogs.auto_delete", "modules.proactive_assist"):
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {e}")

        # Sync slash commands on startup (opt out with SYNC_COMMANDS=false)
        if os.getenv("SYNC_COMMANDS", "true").lower() not in ("false", "0", "no"):
            try:
                synced = await self.tree.sync()
                logger.info(f"Synced {len(synced)} slash commands")
            except Exception as e:
                logger.error(f"Failed to sync slash commands: {e}")

        # Register persistent views for immortal buttons
        await self._register_persistent_views()

        # Start background tasks
        await self._start_background_tasks()

        # Note: pending reminders/giveaways are restored per-guild by
        # giveaways.start_monitoring() / reminders.start_monitoring() above.

        logger.info("Miro Bot setup complete - all systems immortal!")

    async def _register_persistent_views(self):
        """Register all persistent views that survive bot restarts."""
        if self._persistent_views_registered:
            return

        # Register all system persistent views
        views_to_register = [
            # Auto setup buttons (CreateTicketButton/SuggestionButton stubs were
            # replaced by the real TicketPanelView / SuggestionPanelView)
            auto_setup.VerifyButton(),
            auto_setup.AcceptRulesButton(),
            auto_setup.ApplyStaffButton(),

            # System specific views
            self.verification.get_persistent_views(),
            self.tickets.get_persistent_views(),
            self.suggestions.get_persistent_views(),
            self.giveaways.get_persistent_views(),
            self.applications.get_persistent_views(),
            self.appeals.get_persistent_views(),
            self.modmail.get_persistent_views(),
            self.welcome_leave.get_persistent_views(),
        ]

        # Additional system views (reaction menus, role buttons, announcements)
        if hasattr(self, "reaction_menus") and self.reaction_menus:
            try:
                views_to_register.append(self.reaction_menus.get_persistent_views())
            except Exception as e:
                logger.error(f"Reaction menus persistent views failed: {e}")
        if hasattr(self, "role_buttons") and self.role_buttons:
            try:
                views_to_register.append(self.role_buttons.get_persistent_views())
            except Exception as e:
                logger.error(f"Role buttons persistent views failed: {e}")
        if hasattr(self, "announcements") and self.announcements:
            try:
                views_to_register.append(self.announcements.get_persistent_views())
            except Exception as e:
                logger.error(f"Announcements persistent views failed: {e}")
        if hasattr(self, "embed_system") and self.embed_system:
            try:
                views_to_register.append(self.embed_system.get_persistent_views())
            except Exception as e:
                logger.error(f"Embed system persistent views failed: {e}")

        # Flatten the list and register
        for view in views_to_register:
            if isinstance(view, list):
                for v in view:
                    if v is not None:
                        self.add_view(v)
            elif view is not None:
                self.add_view(view)

        self._persistent_views_registered = True
        logger.info("Persistent views registered")

    async def _start_background_tasks(self):
        """Start all background monitoring and automation tasks."""
        if self._background_tasks_started:
            return

        # Start the shared task scheduler (fires reminders, giveaways, AI tasks)
        await self.task_scheduler.start()

        # Rehydrate LIVE agent-created cron automations (survive restarts)
        try:
            self._restore_automations()
        except Exception as e:
            logger.error(f"Automation restore failed: {e}")

        # Start system monitors (sync methods - no await)
        self.anti_raid.start_monitoring()

        # Start task monitor methods (sync - no await needed)
        self.staff_reviews.start_tasks()
        self.staff_shifts.start_tasks()

        # Start async monitoring tasks
        asyncio.create_task(self._start_async_monitors())

        # Start cleanup tasks
        asyncio.create_task(self._cleanup_expired_sessions())
        asyncio.create_task(self._auto_backup_loop())

        # Core V2 layer: health watchdogs + unified analytics stream
        self._register_health_subsystems()
        self.health.start()
        self.analytics_collector.start()
        asyncio.create_task(self.analytics_collector.flush_loop())

        self._background_tasks_started = True
        logger.info("Background tasks started")

    def _register_health_subsystems(self):
        """Register watchdogs for the subsystems named in the architecture plan."""
        health = self.health

        health.register("gateway", lambda: self.is_ready())
        health.register("scheduler", lambda: getattr(self.task_scheduler, "_running", False))
        health.register("database", self._check_database)
        # Based on real request outcomes (AIClient.report_success/report_failure),
        # so it auto-recovers once API errors stop; never flags "not configured".
        health.register("ai_client", lambda: getattr(self.ai, "consecutive_failures", 0) < 5)
        health.register("event_bus", lambda: True)

    @staticmethod
    async def _check_database() -> bool:
        try:
            import os as _os
            return _os.access("data", _os.W_OK)
        except Exception:
            return False

    async def _start_async_monitors(self):
        """Start all async monitoring tasks."""
        # Each monitor is isolated so one failure can't kill the others
        async def safe(name, coro=None, func=None):
            try:
                if coro is not None:
                    await coro
                elif func is not None:
                    func()
            except Exception as e:
                logger.error(f"{name} monitor failed to start: {e}")

        # Guardian is a Cog - uses Discord event listeners, no explicit start needed
        await safe("giveaways", coro=self.giveaways.start_monitoring())
        await safe("reminders", coro=self.reminders.start_monitoring())
        await safe("announcements", coro=self.announcements.start_monitoring())
        await safe("intelligence", func=self.intelligence.start_monitoring)
        await safe("trigger_roles", func=self.trigger_roles.start_monitoring)
        if self.analytics:
            await safe("analytics", func=self.analytics.start_monitoring_loop)
        await safe("auto_announcer", func=self.auto_announcer.start_loops)
        await safe("auto_publisher", func=self.auto_publisher.start_bump_monitor)
        await safe("event_scheduler", func=self.event_scheduler.start_event_monitor)
        await safe("gamification", func=self.gamification.start_quest_refresh)
        logger.info("Async monitors started")

    async def _cleanup_expired_sessions(self):
        """Clean up expired AI sessions and temporary data."""
        while True:
            try:
                current_time = time.time()
                # Clean up expired sessions from various systems
                expired_sessions = []

                # Add cleanup logic for different session types as needed
                # For now, just sleep
                await asyncio.sleep(3600)  # Clean every hour
            except Exception as e:
                logger.error(f"Session cleanup error: {e}")
                await asyncio.sleep(3600)

    async def _auto_backup_loop(self):
        """Automatic data backup every 6 hours."""
        while True:
            try:
                dm.backup_data()
                logger.info("Automatic backup completed")
            except Exception as e:
                logger.error(f"Auto backup failed: {e}")
            await asyncio.sleep(21600)  # 6 hours

    async def on_ready(self):
        """Bot is ready and connected."""
        logger.info(f"Miro Bot ready as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")

        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()

        # One-time setup tasks
        if not hasattr(self, '_guild_setup_done'):
            self._guild_setup_done = True
            asyncio.create_task(self._setup_guild_data())

    async def _setup_guild_data(self):
        """Initialize data for guilds the bot is in."""
        for guild in self.guilds:
            # Ensure basic data structure exists
            dm.get_guild_data(guild.id, "initialized", True)

            # Set up any missing system data
            systems = [
                "economy", "leveling", "verification", "anti_raid", "guardian",
                "tickets", "suggestions", "reminders", "giveaways", "announcements",
                "auto_responder", "reaction_roles", "moderation", "warnings",
                "staff_shifts", "staff_reviews", "starboard", "ai_chat"
            ]

            for system in systems:
                config_key = f"{system}_config"
                data = dm.get_guild_data(guild.id, config_key)
                if not data:
                    dm.update_guild_data(guild.id, config_key, {"enabled": False})

        logger.info("Guild data initialization complete")

    def _setup_signal_handlers(self):
        """Set up graceful shutdown handlers."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))
            logger.info("Signal handlers set up")
        except Exception as e:
            logger.warning(f"Could not set up signal handlers: {e}")

    async def _graceful_shutdown(self):
        """Clean shutdown with data persistence."""
        logger.info("Starting graceful shutdown...")

        try:
            # Save all data
            dm.backup_data()

            # Stop background tasks
            await self.task_scheduler.stop()

            # Close bot connection
            await self.close()

            logger.info("Graceful shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            import sys
            sys.exit(0)

    async def on_message(self, message):
        """Handle incoming messages."""
        if message.author.bot:
            return

        # Handle DMs through modmail
        if isinstance(message.channel, discord.DMChannel):
            await self.modmail.handle_dm(message)
            return

        # LIVE custom prefix commands (agent-created via create_prefix_command).
        # Fast-path BEFORE the AI pipeline: a matched command executes instantly
        # and AI is skipped entirely. Zero restart needed.
        try:
            if await self._try_custom_prefix_command(message):
                return
        except Exception as e:
            logger.error(f"Custom prefix command fast-path error: {e}")

        # Process commands
        await self.process_commands(message)

        # Handle passive system triggers
        await self._handle_passive_systems(message)

    async def _try_custom_prefix_command(self, message) -> bool:
        """Execute a stored custom '!name' command synchronously.
        Returns True when the message was handled (caller must skip further processing)."""
        if getattr(message, "guild", None) is None:
            return False
        handler = getattr(self, "action_handler", None)
        if handler is None:
            return False
        content = message.content or ""
        if not content.strip():
            return False
        prefix = dm.get_guild_data(message.guild.id, "prefix", "!") or "!"
        if not content.startswith(prefix):
            return False
        rest = content[len(prefix):].strip()
        if not rest:
            return False
        cmds = dm.get_guild_data(message.guild.id, "custom_commands", {}) or {}
        if not cmds:
            return False
        lowered = rest.lower()
        # Longest-key match wins so "help economy" beats a plain "help" command
        hit_key = None
        hit_norm = None
        for key in cmds.keys():
            norm = str(key).lower().lstrip("!").strip()
            if not norm:
                continue
            if lowered == norm or lowered.startswith(norm + " "):
                if hit_norm is None or len(norm) > len(hit_norm):
                    hit_norm = norm
                    hit_key = key
        if hit_key is None:
            return False
        code = cmds.get(hit_key)
        if code is None:
            return False
        logger.info(f"LIVE custom command !{hit_key} triggered by {message.author} (guild {message.guild.id})")
        try:
            await handler.execute_custom_command(message, code, str(hit_key))
        except Exception as e:
            logger.error(f"Custom command !{hit_key} execution failed: {e}")
        # Count as handled regardless — the command path already responded,
        # falling through to AI would produce a confusing double reply.
        return True

    def _restore_automations(self):
        """Rehydrate LIVE agent-created cron automations from guild data so they
        survive restarts. Reminders/auto-responders restore themselves via their
        own monitors; only scheduled_task cron jobs need re-scheduling here."""
        try:
            from croniter import croniter
        except Exception:
            logger.warning("croniter unavailable — cannot restore cron automations")
            return
        from actions import ScheduledTaskInteraction
        handler = getattr(self, "action_handler", None)
        if handler is None:
            return
        now = time.time()
        restored = 0
        for guild in list(self.guilds):
            autos = dm.get_guild_data(guild.id, "automations", {}) or {}
            changed = False
            for name, entry in list(autos.items()):
                try:
                    if not isinstance(entry, dict) or entry.get("type") != "scheduled_task":
                        continue
                    cron = entry.get("cron")
                    sched_handler = entry.get("handler")
                    if not cron or not sched_handler:
                        continue
                    try:
                        nxt = float(entry.get("next_run") or 0.0)
                    except (TypeError, ValueError):
                        nxt = 0.0
                    if nxt <= now:
                        try:
                            nxt = croniter(cron, dt.datetime.now()).get_next(float)
                        except Exception as e:
                            logger.warning(f"Automation '{name}' invalid cron '{cron}' ({e}); skipping")
                            continue
                    params = entry.get("params") or {}
                    if not isinstance(params, dict):
                        params = {}
                    channel_id = entry.get("channel_id")
                    if channel_id is not None and "channel_id" not in params and "channel" not in params:
                        params = dict(params)
                        params["channel_id"] = channel_id

                    async def _run(_name=name, _cron=cron, _h=sched_handler, _p=dict(params), _gid=guild.id):
                        try:
                            mock = ScheduledTaskInteraction(self, _gid)
                            if mock.guild is None:
                                logger.warning(f"Automation '{_name}': guild {_gid} gone; dropped")
                                return
                            cid = (_p or {}).get("channel_id")
                            if cid:
                                try:
                                    ch = self.get_channel(int(str(cid)))
                                    if ch is not None:
                                        mock.channel = ch
                                except (TypeError, ValueError):
                                    pass
                            await handler.dispatch(mock, _h, dict(_p))
                            try:
                                n2 = croniter(_cron, dt.datetime.now()).get_next(float)
                                task_scheduler.schedule_task(n2, _run)
                                autos_now = dm.get_guild_data(_gid, "automations", {}) or {}
                                if _name in autos_now:
                                    autos_now[_name]["next_run"] = n2
                                    dm.update_guild_data(_gid, "automations", autos_now)
                            except Exception as e2:
                                logger.error(f"Automation '{_name}' reschedule failed: {e2}")
                        except Exception as e:
                            logger.error(f"Restored automation '{_name}' failed: {e}")

                    tid = task_scheduler.schedule_task(nxt, _run)
                    entry["task_id"] = tid
                    entry["next_run"] = nxt
                    changed = True
                    restored += 1
                except Exception as e:
                    logger.warning(f"Failed restoring automation '{name}': {e}")
            if changed:
                dm.update_guild_data(guild.id, "automations", autos)
        if restored:
            logger.info(f"Restored {restored} LIVE cron automation(s)")

    async def _handle_passive_systems(self, message):
        """Handle systems that react to messages passively."""
        async def safe(coro, name):
            try:
                await coro
            except Exception as e:
                logger.error(f"{name} passive error: {e}")

        # Leveling XP
        await safe(self.leveling.handle_message(message), "leveling")

        # Economy passive income
        await safe(self.economy.handle_message(message), "economy")

        # Auto responder
        await safe(self.auto_responder.handle_message(message), "auto_responder")

        # Anti-raid monitoring
        await safe(self.anti_raid.handle_message(message), "anti_raid")

        # Guardian uses Cog event listeners - no wrapper call needed

        # Staff shift tracking
        await safe(self.staff_shifts.handle_message(message), "staff_shifts")

        # AI chat channels
        await safe(self.ai_chat.handle_message(message), "ai_chat")

        # Trigger roles
        await safe(self.welcome_leave.handle_trigger_roles(message), "welcome_leave")
        await safe(self.trigger_roles.handle_message(message), "trigger_roles")

        # Auto-mod content filtering
        await safe(self.automod.handle_message(message), "automod")

        # Community health + conflict resolution (guarded — they call the AI)
        await safe(self.community_health.analyze_interaction(message), "community_health")
        await safe(self.conflict_resolution.analyze_message(message), "conflict_resolution")

        # Auto publisher (thread publishing / bump reminders)
        await safe(self.auto_publisher.on_message(message), "auto_publisher")

        # Internal event bus (analytics + future reactive systems)
        if getattr(message, "guild", None) is not None:
            await self.event_bus.publish(
                "message.created",
                guild_id=message.guild.id,
                user_id=message.author.id,
                channel_id=getattr(message.channel, "id", None),
            )

    async def on_member_join(self, member):
        """Handle member joins."""
        try:
            await self.verification.handle_member_join(member)
            await self.welcome_leave.handle_member_join(member)
            await self.anti_raid.handle_join(member)  # anti_raid uses handle_join
            asyncio.create_task(self.event_bus.publish("member.joined", guild_id=member.guild.id, user_id=member.id))
        except Exception as e:
            logger.error(f"Member join error: {e}")

    async def on_member_remove(self, member):
        """Handle member leaves."""
        try:
            await self.welcome_leave.handle_member_remove(member)
            await self.anti_raid.handle_member_remove(member)
            await self.staff_extras.on_member_remove(member)
            asyncio.create_task(self.event_bus.publish("member.left", guild_id=member.guild.id, user_id=member.id))
        except Exception as e:
            logger.error(f"Member remove error: {e}")

    async def on_reaction_add(self, reaction, user):
        """Handle reaction adds."""
        try:
            await self.starboard.handle_reaction_add(reaction, user)
            await self.staff_extras.on_reaction_add(reaction, user)
        except Exception as e:
            logger.error(f"Reaction add error: {e}")

    async def on_raw_reaction_add(self, payload):
        """Raw reaction events drive reaction-role assignments."""
        try:
            if payload.guild_id:
                await self.reaction_roles.handle_reaction_add(payload)
        except Exception as e:
            logger.error(f"Raw reaction add error: {e}")

    async def on_raw_reaction_remove(self, payload):
        try:
            if payload.guild_id:
                await self.reaction_roles.handle_reaction_remove(payload)
        except Exception as e:
            logger.error(f"Raw reaction remove error: {e}")

    async def on_guild_join(self, guild):
        """Handle joining a new guild."""
        logger.info(f"Joined new guild: {guild.name} ({guild.id})")
        try:
            await self.auto_setup.initialize_guild(guild)
        except Exception as e:
            logger.error(f"Guild join setup error: {e}")

    async def on_error(self, event, *args, **kwargs):
        """Global error handler."""
        logger.error(f"Event error in {event}: {traceback.format_exc()}")

    async def _on_app_command_error(self, interaction, error):
        """
        Slash-command safety net: Discord shows "The application did not
        respond" whenever a command errors without replying. This guarantees
        every failing command still answers the user.
        """
        try:
            import discord.app_commands as app_commands
            if isinstance(error, app_commands.CheckFailure):
                msg = "🚫 You don't have permission to use this command here."
            elif isinstance(error, app_commands.CommandOnCooldown):
                msg = f"⏳ Slow down — try again in {error.retry_after:.1f}s."
            else:
                logger.error(f"App command error in /{getattr(getattr(interaction, 'command', None), 'name', '?')}: "
                             f"{traceback.format_exc()}")
                msg = "❌ Something went wrong running that command. The error was logged."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            logger.error(f"Failed to report app command error: {traceback.format_exc()}")

# Create and run the bot
if __name__ == "__main__":
    import socket
    import sys

    def sanitize_error(exc: Exception) -> str:
        """Return a short, readable error message instead of raw HTML bodies."""
        text = getattr(exc, "text", "") or str(exc)
        # Cut HTML bodies (Cloudflare/Discord error pages) down to one line
        if "<!doctype html" in text.lower() or "<html" in text.lower():
            status = getattr(exc, "status", "?")
            return f"status {status}: HTML error page returned (likely Cloudflare rate limiting / blocked IP)"
        return text[:300]

    def start_health_server():
        """Bind Render's expected port so the Web Service is marked healthy while the bot retries."""
        port = int(os.getenv("PORT", "10000"))
        try:
            import threading
            from http.server import BaseHTTPRequestHandler, HTTPServer

            class _HealthHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"ok")

                def log_message(self, *args):
                    pass

            server = HTTPServer(("0.0.0.0", port), _HealthHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            logger.info(f"Health server listening on port {port}")
        except Exception as e:
            logger.warning(f"Could not start health server (port {port}): {e}")

    def make_bot():
        """Build a fresh MiroBot, optionally routed through a proxy (DISCORD_PROXY)."""
        proxy = os.getenv("DISCORD_PROXY", "").strip()
        proxy_auth = None
        if proxy:
            try:
                import aiohttp
                from urllib.parse import urlsplit
                parts = urlsplit(proxy)
                if parts.username:
                    proxy_auth = aiohttp.BasicAuth(parts.username, parts.password or "")
            except Exception as e:
                logger.warning(f"Invalid DISCORD_PROXY: {e}")
        bot = MiroBot(proxy=proxy or None, proxy_auth=proxy_auth)
        # Custom User-Agent: helps avoid naive Cloudflare bot fingerprinting
        try:
            bot.http.user_agent = "MiroBot/2.0 DiscordBot (https://github.com/Malikiyaw/Miro)"
        except Exception:
            pass
        return bot

    def run_with_retries():
        """Run the bot, retrying transient errors forever with capped exponential backoff.

        Key points:
        - A CRASH-AND-RESTART loop (Render restarting an exited process) causes rapid
          repeated logins from the same datacenter IP, which Discord/Cloudflare blocks
          with HTTP 429 / error 1015. Staying alive in-process and backing off breaks it.
        - discord.py's bot.run() closes the HTTP session on failure, so a reused bot
          instance dies with "Session is closed" — hence a FRESH MiroBot per attempt.
        - While blocked, a health HTTP server keeps Render from restarting the service,
          and we keep retrying (usually the block lifts within minutes to an hour).
        """
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            logger.error("DISCORD_TOKEN is not set. Add it to your environment (Render dashboard -> Environment).")
            sys.exit(1)

        start_health_server()

        attempt = 0
        while True:
            bot = make_bot()
            try:
                bot.run(token, reconnect=True)
                logger.info("Bot stopped cleanly.")
                return
            except discord.PrivilegedIntentsRequired:
                logger.error(
                    "Privileged intents are not enabled. Enable 'SERVER MEMBERS INTENT' and "
                    "'PRESENCE INTENT' at https://discord.com/developers/applications -> Bot -> "
                    "Privileged Gateway Intents, then restart."
                )
                sys.exit(1)
            except discord.LoginFailure as e:
                logger.error(f"Invalid Discord token. Double-check DISCORD_TOKEN. ({sanitize_error(e)})")
                sys.exit(1)
            except discord.HTTPException as e:
                status = getattr(e, "status", 0)
                if status == 429:
                    wait = min(30 * (2 ** min(attempt, 6)), 900) + random.uniform(0, 30)
                    logger.warning(
                        f"Discord is rate-limiting us (HTTP 429, likely Cloudflare blocking the datacenter IP). "
                        f"Retrying in {wait:.0f}s (attempt {attempt + 1}). {sanitize_error(e)}"
                    )
                    time.sleep(wait)
                    attempt += 1
                    continue
                if 500 <= status < 600:
                    wait = min(15 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 15)
                    logger.warning(f"Discord server error {status}. Retrying in {wait:.0f}s (attempt {attempt + 1})...")
                    time.sleep(wait)
                    attempt += 1
                    continue
                logger.error(f"Discord HTTP error {status}: {sanitize_error(e)}")
                sys.exit(1)
            except discord.ConnectionClosed as e:
                if e.code in (4000, 4006, 4009):
                    logger.error(f"Discord closed the connection (code {e.code}). Not retrying.")
                    sys.exit(1)
                wait = min(15 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 10)
                logger.warning(f"Discord connection closed (code {e.code}). Reconnecting in {wait:.0f}s...")
                time.sleep(wait)
                attempt += 1
            except discord.GatewayNotFound as e:
                logger.error(f"Discord gateway not found: {sanitize_error(e)}")
                sys.exit(1)
            except (socket.gaierror, TimeoutError, ConnectionError) as e:
                wait = min(10 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 10)
                logger.warning(f"Network error: {e}. Retrying in {wait:.0f}s...")
                time.sleep(wait)
                attempt += 1
            except Exception as e:
                # Unknown/transient failure: do NOT exit (a Render restart would restart the
                # login storm). Back off and try again with a fresh bot instance.
                logger.error(f"Unexpected error: {sanitize_error(e)}")
                logger.error(traceback.format_exc())
                wait = min(30 * (2 ** min(attempt, 6)), 900) + random.uniform(0, 30)
                logger.warning(f"Restarting in {wait:.0f}s (attempt {attempt + 1})...")
                time.sleep(wait)
                attempt += 1

    run_with_retries()