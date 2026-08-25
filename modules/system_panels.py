import re

import discord
from discord import ui
import time
from typing import Callable, Dict, List, Optional

from data_manager import dm
from logger import logger
from core.access_control import AccessLevel
from ui.system_panel import SystemPanelView
from ui.components import ConfirmView, build_status_embed, format_bool, truncate


# --------------------------------------------------------------------------- #
# Real module accessors (no fake state: everything goes through the modules)  #
# --------------------------------------------------------------------------- #

def _cfg(bot, guild_id: int, key: str, default=None):
    return dm.get_guild_data(guild_id, key, {} if default is None else default)


def _save(bot, guild_id: int, key: str, value):
    dm.update_guild_data(guild_id, key, value)


def _make_accessors(config_key: str):
    """Standard dm-backed read/write for a config key."""
    def read(bot, guild_id):
        value = _cfg(bot, guild_id, config_key)
        return value if isinstance(value, dict) else {}
    def write(bot, guild_id, config):
        _save(bot, guild_id, config_key, config)
    return read, write


def _module_accessors(module_attr: str, config_key: str,
                      getter: str, setter: str):
    """Read/write through the module's own accessor methods when present."""
    def read(bot, guild_id):
        module = getattr(bot, module_attr, None)
        if module is not None and hasattr(module, getter):
            try:
                value = getattr(module, getter)(guild_id)
                if isinstance(value, dict):
                    return value
            except Exception as e:
                logger.debug(f"{module_attr}.{getter} failed: {e}")
        return _cfg(bot, guild_id, config_key)
    def write(bot, guild_id, config):
        module = getattr(bot, module_attr, None)
        if module is not None and hasattr(module, setter):
            try:
                getattr(module, setter)(guild_id, config)
                return
            except Exception as e:
                logger.warning(f"{module_attr}.{setter} failed, using dm: {e}")
        _save(bot, guild_id, config_key, config)
    return read, write


# --------------------------------------------------------------------------- #
# The 10 merged systems (16 original systems preserved underneath)            #
# --------------------------------------------------------------------------- #

def _count_key(bot, guild_id, key):
    value = _cfg(bot, guild_id, key)
    return len(value) if isinstance(value, (list, dict)) else 0


def _metric_count(label, key):
    """(label, fn) tuple — fn(bot, guild_id) -> (label, count)."""
    def fn(bot, gid):
        return (label, _count_key(bot, gid, key))
    return (label, fn)


def _metric_liststatus(label, key, status):
    def fn(bot, gid):
        value = _cfg(bot, gid, key)
        if isinstance(value, list):
            return (label, sum(1 for v in value if isinstance(v, dict) and v.get(status) == status))
        return (label, 0)
    return (label, fn)


SYSTEM_GROUPS: Dict[str, dict] = {
    "member_management": {
        "emoji": "👤", "name": "Member Management",
        "subsystems": [
            {"key": "verification", "label": "Verification", "module_attr": "verification",
             "config_key": "verification_config",
             "settings": [
                 {"key": "verify_channel", "label": "Verify channel", "type": "channel"},
                 {"key": "verified_role", "label": "Verified role", "type": "role"},
                 {"key": "unverified_role", "label": "Unverified role", "type": "role"},
                 {"key": "min_account_age_days", "label": "Min account age (days)", "type": "int"},
             ],
             "toggles": [{"key": "kick_new_accounts", "label": "Kick new accounts"}],
             "metrics": [_metric_count("Verified users", "verified_users")]},
            {"key": "welcome", "label": "Welcome", "module_attr": "welcome_leave",
             "config_key": "welcome_leave_config",
             "settings": [
                 {"key": "welcome_channel", "label": "Welcome channel", "type": "channel"},
                 {"key": "welcome_message", "label": "Welcome message ({user}/{server})", "type": "str"},
                 {"key": "leave_channel", "label": "Leave channel (blank = same)", "type": "channel"},
                 {"key": "leave_message", "label": "Leave message", "type": "str"},
             ],
             "toggles": [{"key": "welcome_dm", "label": "DM welcome"}],
             "metrics": []},
            {"key": "leave", "label": "Leave messages", "module_attr": "welcome_leave",
             "config_key": "welcome_leave_config",
             "settings": [
                 {"key": "leave_channel", "label": "Leave channel", "type": "channel"},
                 {"key": "leave_message", "label": "Leave message", "type": "str"},
             ],
             "toggles": [],
             "metrics": []},
        ],
    },
    "progression": {
        "emoji": "💰", "name": "Progression",
        "subsystems": [
            {"key": "economy", "label": "Economy", "module_attr": "economy",
             "config_key": "economy_config",
             "settings": [
                 {"key": "coin_emoji", "label": "Currency emoji", "type": "str"},
                 {"key": "daily_amount", "label": "Daily reward amount", "type": "int"},
                 {"key": "daily_cooldown", "label": "Daily cooldown (seconds)", "type": "int"},
                 {"key": "work_cooldown", "label": "Work cooldown (seconds)", "type": "int"},
             ],
             "toggles": [],
             "metrics": [_metric_count("Members with balance", "economy_balances"),
                         _metric_count("Shop items", "shop_items")]},
            {"key": "leveling", "label": "Leveling", "module_attr": "leveling",
             "config_key": "leveling_config",
             "settings": [
                 {"key": "xp_per_message", "label": "XP per message", "type": "int"},
                 {"key": "message_cooldown", "label": "XP cooldown (seconds)", "type": "int"},
                 {"key": "announce_channel", "label": "Level-up channel", "type": "channel"},
             ],
             "toggles": [{"key": "announce_level_ups", "label": "Announce level-ups"}],
             "metrics": [_metric_count("Tracked users", "leveling_users")]},
            {"key": "shop", "label": "Shop", "module_attr": "shop",
             "config_key": "shop_items",
             "settings": [],
             "toggles": [],
             "supports_toggle": False,
             "metrics": [_metric_count("Shop items", "shop_items"),
                         _metric_count("Purchases", "purchases")]},
            {"key": "gamification", "label": "Gamification", "module_attr": "gamification",
             "config_key": "gamification_config",
             "settings": [],
             "toggles": [],
             "supports_toggle": False,
             "metrics": []},
            {"key": "tournaments", "label": "Tournaments", "module_attr": "tournaments",
             "config_key": "tournament_settings",
             "settings": [],
             "toggles": [],
             "metrics": []},
            {"key": "events", "label": "Event Scheduler", "module_attr": "event_scheduler",
             "config_key": "event_settings",
             "accessors": _module_accessors("event_scheduler", "event_settings",
                                            "get_guild_settings", "save_guild_settings"),
             "settings": [
                 {"key": "announcement_channel", "label": "Event channel", "type": "channel"},
             ],
             "toggles": [],
             "metrics": []},
        ],
    },
    "tickets": {
        "emoji": "🎫", "name": "Tickets",
        "subsystems": [
            {"key": "tickets", "label": "Tickets", "module_attr": "tickets",
             "config_key": "tickets_config",
             "settings": [
                 {"key": "ticket_category", "label": "Ticket category", "type": "channel"},
                 {"key": "log_channel", "label": "Log channel", "type": "channel"},
                 {"key": "delete_closed_tickets", "label": "Delete closed tickets (true/false)", "type": "str"},
             ],
             "roles": [{"key": "staff_roles", "label": "Staff roles"}],
             "toggles": [],
             "metrics": [_metric_count("Open tickets", "user_tickets"),
                         _metric_count("Closed tickets", "closed_tickets")]},
        ],
    },
    "suggestions": {
        "emoji": "💡", "name": "Suggestions",
        "subsystems": [
            {"key": "suggestions", "label": "Suggestions", "module_attr": "suggestions",
             "config_key": "suggestions_config",
             "settings": [
                 {"key": "suggestions_channel", "label": "Suggestions channel", "type": "channel"},
             ],
             "roles": [{"key": "staff_roles", "label": "Reviewer roles"}],
             "toggles": [],
             "metrics": [_metric_liststatus("Pending suggestions", "suggestions", "pending")]},
        ],
    },
    "giveaways": {
        "emoji": "🎁", "name": "Giveaways",
        "subsystems": [
            {"key": "giveaways", "label": "Giveaways", "module_attr": "giveaways",
             "config_key": "giveaways_config",
             "settings": [],
             "toggles": [],
             "metrics": [_metric_count("Ended giveaways", "ended_giveaways")]},
        ],
    },
    "communications": {
        "emoji": "📢", "name": "Communications",
        "subsystems": [
            {"key": "announcements", "label": "Announcements", "module_attr": "announcements",
             "config_key": "announcements_config",
             "settings": [
                 {"key": "announcement_channel", "label": "Announcement channel", "type": "channel"},
                 {"key": "approval_channel", "label": "Approval channel", "type": "channel"},
             ],
             "roles": [{"key": "staff_roles", "label": "Announcer roles"}],
             "toggles": [{"key": "require_approval", "label": "Require approval"}],
             "metrics": [_metric_count("Pending approvals", "pending_announcements")]},
            {"key": "reminders", "label": "Reminders", "module_attr": "reminders",
             "config_key": "reminders_config",
             "settings": [],
             "toggles": [],
             "metrics": [_metric_count("Scheduled reminders", "scheduled_reminders")]},
            {"key": "modmail", "label": "Modmail", "module_attr": "modmail",
             "config_key": "modmail_config",
             "settings": [
                 {"key": "category_id", "label": "Thread category", "type": "channel"},
                 {"key": "log_channel_id", "label": "Log channel", "type": "channel"},
             ],
             "toggles": [],
             "metrics": []},
            {"key": "auto_publisher", "label": "Auto-Publisher", "module_attr": "auto_publisher",
             "config_key": "auto_publisher_settings",
             "accessors": _module_accessors("auto_publisher", "auto_publisher_settings",
                                            "get_guild_settings", "save_settings"),
             "settings": [],
             "toggles": [{"key": "enabled", "label": "Auto-publish news posts"}],
             "metrics": []},
        ],
    },
    "anti_raid": {
        "emoji": "🛡️", "name": "Anti-Raid",
        "subsystems": [
            {"key": "anti_raid", "label": "Anti-Raid", "module_attr": "anti_raid",
             "config_key": "anti_raid_config",
             "accessors": _module_accessors("anti_raid", "anti_raid_config",
                                            "get_guild_settings", "save_settings"),
             "settings": [
                  {"key": "mass_join_threshold", "label": "Join threshold (accounts)", "type": "int", "min": 3, "max": 100},
                  {"key": "mass_join_window", "label": "Time window (seconds)", "type": "int", "min": 10, "max": 600},
                  {"key": "min_account_age_days", "label": "Min account age (days)", "type": "int", "min": 0, "max": 30},
                  {"key": "alert_channel_id", "label": "Alert channel", "type": "channel"},
              ],
              "selects": [{"key": "action", "label": "Raid response action",
                           "options": [("lockdown", "🔒 Lockdown"), ("kick", "👢 Kick"),
                                       ("ban", "🔨 Ban"), ("mute", "🔇 Mute")]}],
             "toggles": [{"key": "auto_lockdown", "label": "Auto lockdown"},
                         {"key": "age_filter_enabled", "label": "Account-age filter"}],
             "metrics": [_metric_count("Logged incidents", "raid_log")],
             "lockdown": True},
            {"key": "guardian", "label": "Guardian (AI threat detection)", "module_attr": "guardian",
             "config_key": "guardian_config",
             "settings": [
                 {"key": "alert_channel", "label": "Alert channel", "type": "channel"},
                 {"key": "log_channel", "label": "Log channel", "type": "channel"},
             ],
             "toggles": [{"key": "anti_nuke_enabled", "label": "Anti-nuke"},
                         {"key": "anti_scam_enabled", "label": "Anti-scam"}],
             "metrics": []},
        ],
    },
    "moderation": {
        "emoji": "🔨", "name": "Moderation",
        "subsystems": [
            {"key": "automod", "label": "Auto-Mod", "module_attr": "automod",
             "config_key": "automod_config",
             "accessors": _module_accessors("automod", "automod_config", "get_config", "save_config"),
             "settings": [
                 {"key": "log_channel_id", "label": "Mod-log channel", "type": "channel"},
             ],
             "toggles": [],
             "metrics": [_metric_count("Violation history", "automod_history")],
             "rule_toggles": True},
            {"key": "warnings", "label": "Warnings", "module_attr": "warnings",
             "config_key": "warning_config",
             "accessors": _module_accessors("warnings", "warning_config", "get_config", "save_config"),
             "settings": [
                 {"key": "expiry_days", "label": "Warning expiry (days)", "type": "int"},
             ],
             "toggles": [{"key": "dm_enabled", "label": "DM users on warn"}],
             "metrics": [_metric_count("Warning history", "warning_history")]},
            {"key": "moderation", "label": "Moderation", "module_attr": "moderation",
             "config_key": "moderation_config",
             "settings": [
                 {"key": "log_channel", "label": "Mod-log channel", "type": "channel"},
             ],
             "toggles": [],
             "roles": [{"key": "mod_roles", "label": "Moderator roles"}],
             "metrics": []},
            {"key": "appeals", "label": "Appeals", "module_attr": "appeals",
             "config_key": "appeals_config",
             "settings": [
                 {"key": "appeals_channel_id", "label": "Appeals channel", "type": "channel"},
                 {"key": "log_channel_id", "label": "Log channel", "type": "channel"},
                 {"key": "cooldown_days", "label": "Appeal cooldown (days)", "type": "int"},
             ],
             "toggles": [],
             "supports_toggle": False,
             "metrics": []},
            {"key": "event_logging", "label": "Event Logging", "module_attr": "logging_system",
             "config_key": "logging_config",
             "accessors": _module_accessors("logging_system", "logging_config",
                                            "get_config", "save_config"),
             "settings": [
                 {"key": "log_channel", "label": "Log channel", "type": "channel"},
             ],
             "toggles": [{"key": "enabled", "label": "Event logging"}],
             "metrics": []},
            {"key": "mod_log", "label": "Mod-Log", "module_attr": "mod_logging",
             "config_key": "mod_log_config",
             "settings": [
                 {"key": "channel_id", "label": "Mod-log channel", "type": "channel"},
             ],
             "toggles": [],
             "supports_toggle": False,
             "metrics": []},
        ],
    },
    "automation": {
        "emoji": "⚙️", "name": "Automation",
        "subsystems": [
            {"key": "auto_responder", "label": "Auto-Responder", "module_attr": "auto_responder",
             "config_key": "auto_responder_config",
             "settings": [],
             "toggles": [],
             "metrics": [_metric_count("Configured responses", "auto_responders")]},
            {"key": "reaction_roles", "label": "Reaction Roles", "module_attr": "reaction_roles",
             "config_key": "reaction_roles",
             "settings": [],
             "toggles": [],
             "supports_toggle": False,  # module stores mappings directly; no enabled flag
             "metrics": [_metric_count("Mapped messages", "reaction_roles")]},
            {"key": "starboard", "label": "Starboard", "module_attr": "starboard",
             "config_key": "starboard_config",
             "settings": [
                 {"key": "starboard_channel", "label": "Starboard channel", "type": "channel"},
                 {"key": "star_threshold", "label": "Stars required", "type": "int"},
             ],
             "toggles": [],
             "metrics": [_metric_count("Starred messages", "starred_messages")]},
            {"key": "reaction_menus", "label": "Reaction Menus", "module_attr": "reaction_menus",
             "config_key": "reaction_menus_config",
             "settings": [],
             "toggles": [],
             "metrics": [_metric_count("Configured menus", "reaction_menus")]},
            {"key": "role_buttons", "label": "Role Buttons", "module_attr": "role_buttons",
             "config_key": "role_buttons_config",
             "settings": [],
             "toggles": [],
             "metrics": [_metric_count("Button panels", "role_buttons")]},
            {"key": "trigger_roles", "label": "Trigger Roles", "module_attr": "trigger_roles",
             "config_key": "trigger_roles",
             "settings": [],
             "toggles": [],
             "supports_toggle": False,  # stores trigger mappings directly
             "metrics": [_metric_count("Keyword triggers", "trigger_roles")]},
        ],
    },
    "ai": {
        "emoji": "🤖", "name": "Miro AI",
        "subsystems": [
            {"key": "ai", "label": "AI Engine", "module_attr": "ai",
             "config_key": "ai_config",
             "settings": [
                 {"key": "model", "label": "Model (blank = provider default)", "type": "str"},
                 {"key": "fallback_models", "label": "Fallback models (comma-separated)", "type": "str"},
                 {"key": "max_tokens", "label": "Max tokens per response", "type": "int"},
                 {"key": "temperature", "label": "Temperature (0.0-2.0)", "type": "float"},
                 {"key": "timeout", "label": "Request timeout (seconds)", "type": "int"},
             ],
             "toggles": [{"key": "agent_enabled", "label": "Agent mode"},
                         {"key": "enabled", "label": "AI enabled"}],
             "metrics": []},
            {"key": "ai_chat", "label": "AI Chat Channels", "module_attr": "ai_chat",
             "config_key": "ai_chat_settings",
             "accessors": _module_accessors("ai_chat", "ai_chat_settings",
                                            "get_guild_settings", "save_settings"),
             "settings": [],
             "toggles": [{"key": "enabled", "label": "AI chat channels"}],
             "metrics": []},
            {"key": "community_health", "label": "Community Health", "module_attr": "community_health",
             "config_key": "community_health_config",
             "settings": [
                 {"key": "alert_channel", "label": "Alert channel", "type": "channel"},
             ],
             "toggles": [{"key": "enabled", "label": "Health monitoring"}],
             "metrics": []},
            {"key": "conflict_resolution", "label": "Conflict Resolution", "module_attr": "conflict_resolution",
             "config_key": "conflict_resolution_config",
             "settings": [
                 {"key": "log_channel", "label": "Log channel", "type": "channel"},
             ],
             "toggles": [{"key": "enabled", "label": "Conflict detection"}],
             "metrics": []},
            {"key": "content_generator", "label": "Content Generator", "module_attr": "content_generator",
             "config_key": "content_settings",
             "settings": [],
             "toggles": [],
             "supports_toggle": False,
             "metrics": []},
        ],
    },
    "staff_management": {
        "emoji": "👮", "name": "Staff Management",
        "subsystems": [
            {"key": "staff_shifts", "label": "Staff Shifts", "module_attr": "staff_shifts",
             "config_key": "staff_shifts_config",
             "accessors": _module_accessors("staff_shifts", "staff_shifts_config",
                                            "_get_config", "_save_config"),
             "settings": [
                 {"key": "shift_channel_id", "label": "Shift channel", "type": "channel"},
                 {"key": "idle_timeout_minutes", "label": "Idle timeout (minutes)", "type": "int"},
             ],
             "roles": [{"key": "on_duty_role_id", "label": "On-duty role"}],
             "toggles": [{"key": "notifications_enabled", "label": "Shift notifications"}],
             "metrics": [_metric_count("Shift history entries", "staff_shifts_history")]},
            {"key": "staff_reviews", "label": "Staff Reviews", "module_attr": "staff_reviews",
             "config_key": "staff_reviews_config",
             "accessors": _module_accessors("staff_reviews", "staff_reviews_config",
                                            "_get_config", "_save_config"),
             "settings": [
                 {"key": "review_channel_id", "label": "Review channel", "type": "channel"},
                 {"key": "cycle", "label": "Cycle (weekly/bi-weekly/monthly)", "type": "str"},
             ],
             "toggles": [{"key": "notifications_enabled", "label": "Review notifications"}],
             "metrics": [_metric_count("Completed reviews", "staff_reviews_history")]},
            {"key": "staff_promo", "label": "Staff Promotions", "module_attr": "staff_promo",
             "config_key": "staff_promo_config",
             "accessors": _module_accessors("staff_promo", "staff_promo_config",
                                            "get_config", "save_config"),
             "settings": [
                 {"key": "announcement_channel", "label": "Promotion channel", "type": "channel"},
             ],
             "toggles": [{"key": "enabled", "label": "Promotion tracking"}],
             "metrics": []},
            {"key": "applications", "label": "Applications", "module_attr": "applications",
             "config_key": "application_config",
             "settings": [
                 {"key": "channel_id", "label": "Applications channel", "type": "channel"},
             ],
             "roles": [{"key": "staff_roles", "label": "Reviewer roles"}],
             "toggles": [],
             "metrics": []},
        ],
    },
}

AUTOMOD_RULE_LABELS = {
    "spam": "Spam", "mentions": "Mention spam", "caps": "Caps", "emojis": "Emoji spam",
    "links": "Links", "invites": "Invites", "banned_words": "Banned words",
    "zalgo": "Zalgo", "mass_ping": "Mass ping", "repeated_chars": "Repeated chars",
    "new_account": "New accounts", "attachments": "Attachments", "newlines": "Newline flood",
}


def get_group(group_key: str) -> Optional[dict]:
    return SYSTEM_GROUPS.get(group_key)


# --------------------------------------------------------------------------- #
# The unified panel                                                           #
# --------------------------------------------------------------------------- #

class GroupPanelView(SystemPanelView):
    """One consistent control panel for every merged system group."""

    TABS = ["overview", "test", "help", "history", "danger"]

    def __init__(self, bot, interaction: discord.Interaction, group_key: str):
        spec = SYSTEM_GROUPS[group_key]
        super().__init__(bot, interaction.guild, interaction.user.id,
                         required_level=AccessLevel.ADMIN, timeout=300)
        self.group_key = group_key
        self.spec = spec
        self.tab = "overview"
        self.sub = spec["subsystems"][0]["key"] if spec["subsystems"] else None
        self.build()

    # -- config helpers ------------------------------------------------------

    def _sub(self) -> dict:
        for s in self.spec["subsystems"]:
            if s["key"] == self.sub:
                return s
        return self.spec["subsystems"][0]

    def _read(self, sub: dict) -> dict:
        accessors = sub.get("accessors")
        if accessors:
            return accessors[0](self.bot, self.guild.id)
        return _make_accessors(sub["config_key"])[0](self.bot, self.guild.id)

    def _write(self, sub: dict, config: dict):
        accessors = sub.get("accessors")
        if accessors:
            accessors[1](self.bot, self.guild.id, config)
        else:
            _make_accessors(sub["config_key"])[1](self.bot, self.guild.id, config)

    # -- UI construction ------------------------------------------------------

    def build(self):
        self.clear_items()
        nav = ui.Select(placeholder="Panel section…", custom_id="miro:nav", row=0)
        nav.add_option(label="📊 Overview", value="overview",
                       description="Status and live metrics")
        for s in self.spec["subsystems"]:
            nav.add_option(label=f"{s['label']} settings", value=f"sub:{s['key']}")
        nav.add_option(label="🧪 Test", value="test", description="Run live diagnostics")
        nav.add_option(label="🕘 Recent changes", value="history", description="Audit trail for this system")
        nav.add_option(label="❓ Help", value="help", description="What these settings do")
        nav.add_option(label="☠️ Danger zone", value="danger", description="Reset configurations")
        nav.callback = self._nav_select
        self.add_item(nav)

        if self.tab == "sub":
            self._build_subsystem_actions(self._sub())
        elif self.tab == "overview":
            self._build_overview_actions()
        elif self.tab == "danger":
            self._build_danger_actions()
        elif self.tab == "test":
            self._build_test_actions()

    async def _nav_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        async def work():
            if value.startswith("sub:"):
                self.tab, self.sub = "sub", value[4:]
            else:
                self.tab = value
            self.build()
        await self.perform(interaction, f"nav:{value}", work, success="", refresh=True,
                           level=AccessLevel.EVERYONE)

    def _build_overview_actions(self):
        enable_all = ui.Button(label="Enable all", emoji="🟢",
                               style=discord.ButtonStyle.success, custom_id="miro:enable_all", row=4)
        enable_all.callback = lambda i: self.perform(
            i, "overview:enable_all", self._toggle_all_work(True),
            success="🟢 All subsystems enabled.")
        disable_all = ui.Button(label="Disable all", emoji="🔴",
                                style=discord.ButtonStyle.danger, custom_id="miro:disable_all", row=4)
        disable_all.callback = lambda i: self.perform(
            i, "overview:disable_all", self._toggle_all_work(False),
            success="🔴 All subsystems disabled.")
        self.add_item(enable_all)
        self.add_item(disable_all)

    def _toggle_all_work(self, enabled: bool):
        async def work():
            for sub in self.spec["subsystems"]:
                if sub.get("supports_toggle", True):
                    cfg = self._read(sub)
                    cfg["enabled"] = enabled
                    self._write(sub, cfg)
            return "ok"
        return work

    def _build_subsystem_actions(self, sub: dict):
        row = 1
        if sub.get("supports_toggle", True):
            cfg = self._read(sub)
            enabled = bool(cfg.get("enabled"))
            toggle = ui.Button(
                label="Disable" if enabled else "Enable",
                emoji="⛔" if enabled else "✅",
                style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
                custom_id="miro:sub_toggle", row=row)
            toggle.callback = lambda i: self.perform(
                i, f"{sub['key']}:toggle", self._toggle_work(sub, not enabled),
                success=f"{'🔴 Disabled' if enabled else '🟢 Enabled'} {sub['label']}.")
            self.add_item(toggle)
            row += 1

        if sub["settings"]:
            edit = ui.Button(label="Edit settings", emoji="✏️",
                             style=discord.ButtonStyle.primary, custom_id="miro:sub_edit", row=row)
            edit.callback = self._make_edit_callback(sub)
            self.add_item(edit)
            row += 1

        for tg in sub.get("toggles", []):
            if row > 3:
                break
            cfg = self._read(sub)
            on = bool(cfg.get(tg["key"]))
            btn = ui.Button(label=f"{tg['label']}: {'on' if on else 'off'}",
                            emoji="✅" if on else "⚪",
                            style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
                            custom_id=f"miro:tg:{tg['key']}", row=row)
            btn.callback = lambda i, tg=tg, new=not on: self.perform(
                i, f"{sub['key']}:toggle:{tg['key']}", self._flag_work(sub, tg["key"], new),
                success=f"{tg['label']}: {'🟢 on' if new else '⚪ off'}")
            self.add_item(btn)
            row += 1

        if sub.get("rule_toggles"):
            self._build_rule_toggles(sub, row)

        for sel in sub.get("selects", []):
            if row > 4:
                break
            cfg = self._read(sub)
            current = str(cfg.get(sel["key"], ""))
            select = ui.Select(
                placeholder=f"{sel['label']}"
                            f"{' (now: ' + current + ')' if current else ''}",
                custom_id=f"miro:sel:{sel['key']}", row=row)
            for value, label in sel["options"]:
                select.add_option(label=label, value=value,
                                  default=(value == current))
            select.callback = self._make_select_callback(sub, sel)
            self.add_item(select)
            row += 1

        if sub.get("lockdown"):
            self._build_lockdown_buttons(sub)

        if sub.get("roles"):
            for rspec in sub["roles"]:
                if row > 4:
                    break
                select = ui.RoleSelect(placeholder=f"Set {rspec['label']}…",
                                       min_values=0, max_values=5,
                                       custom_id=f"miro:role:{rspec['key']}", row=row)
                select.callback = self._make_role_callback(sub, rspec)
                self.add_item(select)
                row += 1

        if sub.get("channel_setting") is not None:
            pass  # channels are edited through the settings modal for reliability

    def _build_rule_toggles(self, sub: dict, row: int):
        cfg = self._read(sub)
        rules = cfg.get("rules", {})
        if row > 4:
            row = 4
        select = ui.Select(placeholder="Toggle an Auto-Mod rule…",
                           custom_id="miro:automod_rule", row=row)
        for rule_key, label in AUTOMOD_RULE_LABELS.items():
            rule_cfg = rules.get(rule_key, {})
            state = "🟢" if rule_cfg.get("enabled") else "⚪"
            select.add_option(label=f"{label} {state}", value=rule_key)
        select.callback = self._make_rule_callback(sub)
        self.add_item(select)

    def _make_rule_callback(self, sub: dict):
        async def callback(interaction: discord.Interaction):
            rule_key = interaction.data["values"][0]
            label = AUTOMOD_RULE_LABELS.get(rule_key, rule_key)
            current = bool(self._read(sub).get("rules", {}).get(rule_key, {}).get("enabled"))
            new_state = not current
            async def work():
                cfg = self._read(sub)
                rules = cfg.setdefault("rules", {})
                rule = rules.setdefault(rule_key, {})
                rule["enabled"] = new_state
                self._write(sub, cfg)
                return new_state
            await self.perform(
                interaction, f"automod:rule:{rule_key}", work,
                success=f"{label} rule {'🟢 enabled' if new_state else '⚪ disabled'}",
                refresh=True)
        return callback

    def _make_select_callback(self, sub: dict, sel: dict):
        async def callback(interaction: discord.Interaction):
            value = interaction.data["values"][0]
            async def work():
                cfg = self._read(sub)
                cfg[sel["key"]] = value
                self._write(sub, cfg)
                return value
            await self.perform(
                interaction, f"{sub['key']}:sel:{sel['key']}", work,
                success=f"✅ {sel['label']} set to **{value}**.", refresh=True)
        return callback

    def _build_lockdown_buttons(self, sub: dict):
        lock = ui.Button(label="🚨 LOCK SERVER", style=discord.ButtonStyle.danger,
                         custom_id="miro:lock", row=4)
        lock.callback = self._make_lock_callback(True)
        unlock = ui.Button(label="🔓 End lockdown", style=discord.ButtonStyle.success,
                           custom_id="miro:unlock", row=4)
        unlock.callback = self._make_lock_callback(False)
        self.add_item(lock)
        self.add_item(unlock)

    def _make_lock_callback(self, engage: bool):
        async def callback(interaction: discord.Interaction):
            action = "anti_raid:lock" if engage else "anti_raid:unlock"
            confirm_msg = ("🚨 This will put the server in LOCKDOWN (channel permissions "
                           "restricted server-wide). Are you absolutely sure?") if engage else \
                          ("End the active lockdown and restore channel permissions?")

            confirm_view = ConfirmView(self.author_id, confirm_msg, danger=engage, timeout=30)
            await interaction.response.send_message(confirm_msg, view=confirm_view, ephemeral=True)

            async def wait_and_run():
                await confirm_view.wait()
                if not confirm_view.confirmed:
                    try:
                        await interaction.followup.send("❎ Lockdown action cancelled.", ephemeral=True)
                    except Exception:
                        pass
                    return

                async def work():
                    module = getattr(self.bot, "anti_raid", None)
                    if module is None:
                        raise RuntimeError("Anti-raid module is not loaded")
                    if engage:
                        module._lockdown(self.guild)
                        return "locked"
                    module.lift_lockdown(self.guild)
                    return "unlocked"
                await self.perform(interaction, action, work,
                                   level=AccessLevel.OWNER,
                                   success="🚨 Server locked down." if engage else "🔓 Lockdown ended.",
                                   refresh=False)

            import asyncio as _aio
            _aio.create_task(wait_and_run())
        return callback

    def _make_edit_callback(self, sub: dict):
        async def callback(interaction: discord.Interaction):
            cfg = self._read(sub)
            modal = make_settings_modal(self, sub, cfg)
            await interaction.response.send_modal(modal)
        return callback

    def _make_role_callback(self, sub: dict, rspec: dict):
        async def callback(interaction: discord.Interaction):
            role_ids = [v for v in interaction.data["values"]]
            async def work():
                cfg = self._read(sub)
                cfg[rspec["key"]] = role_ids
                self._write(sub, cfg)
                return f"{len(role_ids)} roles"
            await self.perform(interaction, f"{sub['key']}:roles:{rspec['key']}", work,
                               success=f"✅ {rspec['label']} saved.")
        return callback

    def _flag_work(self, sub: dict, key: str, value: bool):
        async def work():
            cfg = self._read(sub)
            cfg[key] = value
            self._write(sub, cfg)
            return value
        return work

    def _toggle_work(self, sub: dict, enabled: bool):
        async def work():
            cfg = self._read(sub)
            cfg["enabled"] = enabled
            self._write(sub, cfg)
            return enabled
        return work

    def _build_danger_actions(self):
        backups = self._get_backups()
        row = 1
        for idx, sub in enumerate(self.spec["subsystems"]):
            btn = ui.Button(label=f"Reset {sub['label']}", emoji="♻️",
                            style=discord.ButtonStyle.danger,
                            custom_id=f"miro:reset:{sub['key']}", row=min(row, 4))
            btn.callback = self._make_reset_callback(sub)
            self.add_item(btn)
            if sub["config_key"] in backups and row <= 4:
                restore = ui.Button(label=f"Restore {sub['label']}", emoji="⏪",
                                    style=discord.ButtonStyle.success,
                                    custom_id=f"miro:restore:{sub['key']}",
                                    row=min(row, 4), disabled=False)
                restore.callback = self._make_restore_callback(sub)
                self.add_item(restore)
            row += 1

    # -- config backup store --------------------------------------------------

    def _get_backups(self) -> dict:
        return dm.get_guild_data(self.guild.id, "config_backups", {}) or {}

    def _save_backup(self, sub: dict, cfg: dict):
        try:
            backups = self._get_backups()
            backups[sub["config_key"]] = {"data": cfg, "at": time.time()}
            dm.update_guild_data(self.guild.id, "config_backups", backups)
        except Exception as e:
            logger.warning(f"Config backup failed: {e}")

    def _make_reset_callback(self, sub: dict):
        async def callback(interaction: discord.Interaction):
            confirm_view = ConfirmView(self.author_id,
                                       f"Reset **{sub['label']}** to defaults? Its configuration will be erased.\n"
                                       f"(A backup is kept — you can Restore it from this tab.)",
                                       danger=True)
            await interaction.response.send_message(
                f"♻️ Reset **{sub['label']}**? This erases its configuration for this server.",
                view=confirm_view, ephemeral=True)

            async def work():
                current = dm.get_guild_data(self.guild.id, sub["config_key"], {})
                if isinstance(current, dict) and current:
                    self._save_backup(sub, current)
                dm.delete_guild_data(self.guild.id, sub["config_key"])
                return "deleted"

            async def wait_and_run():
                await confirm_view.wait()
                if confirm_view.confirmed:
                    await self.perform(interaction, f"{sub['key']}:reset", work,
                                       success=f"♻️ {sub['label']} configuration reset (backup saved).")
                else:
                    try:
                        await interaction.followup.send("❎ Reset cancelled.", ephemeral=True)
                    except Exception:
                        pass

            import asyncio as _aio
            _aio.create_task(wait_and_run())
        return callback

    def _make_restore_callback(self, sub: dict):
        async def callback(interaction: discord.Interaction):
            backup = self._get_backups().get(sub["config_key"])
            if not backup or not isinstance(backup.get("data"), dict):
                return await interaction.response.send_message(
                    "❌ No backup found for this system.", ephemeral=True)

            async def work():
                self._write(sub, backup["data"])
                # remove the consumed backup
                backups = self._get_backups()
                backups.pop(sub["config_key"], None)
                dm.update_guild_data(self.guild.id, "config_backups", backups)
                return "restored"

            when = backup.get("at")
            when_txt = f"<t:{int(when)}:R>" if isinstance(when, (int, float)) else "earlier"
            await self.perform(
                interaction, f"{sub['key']}:restore", work,
                success=f"⏪ {sub['label']} configuration restored (backup from {when_txt}).",
                refresh=True)
        return callback

    def _build_test_actions(self):
        run = ui.Button(label="Run diagnostics", emoji="🧪",
                        style=discord.ButtonStyle.primary, custom_id="miro:test_run", row=4)
        run.callback = lambda i: self.perform(i, "panel:diagnostics", self._run_diagnostics_work(),
                                              success="", refresh=True, level=AccessLevel.EVERYONE)
        self.add_item(run)

    def _run_diagnostics_work(self):
        async def work():
            self._last_diagnostics = await run_diagnostics(self.bot, self.guild, self.spec)
            return "ok"
        return work

    # -- rendering -------------------------------------------------------------

    def refresh_state(self):
        pass  # all reads happen live in build_embed; nothing cached

    def build_embed(self) -> discord.Embed:
        title = f"{self.spec['emoji']} {self.spec['name']}"
        if self.tab == "overview" or self.tab == "sub":
            return self._build_status_embed(title)
        if self.tab == "test":
            return self._build_test_embed(title)
        if self.tab == "history":
            return self._build_history_embed(title)
        if self.tab == "help":
            return self._build_help_embed(title)
        return self._build_danger_embed(title)

    def _build_history_embed(self, title: str) -> discord.Embed:
        """Recent audit entries touching this system group."""
        entries = []
        audit_log = getattr(self.bot, "audit_log", None)
        if audit_log is not None:
            try:
                recent = audit_log.get_recent(limit=200, guild_id=self.guild.id)
            except TypeError:
                try:
                    recent = audit_log.get_recent(limit=200)
                except Exception:
                    recent = []
            except Exception:
                recent = []
            group_words = {w for w in re.split(r"[_:]", self.group_key) if len(w) > 3}
            sub_keys = {sub["key"] for sub in self.spec["subsystems"]}
            for e in recent:
                text = str(e.get("action", "") or "")
                blob = (text + " " + str(e.get("target", "") or "")).lower()
                if any(k.replace("_", "") in blob.replace("_", "") or k in blob
                       for k in sub_keys | group_words):
                    entries.append(e)
                if len(entries) >= 8:
                    break
        if not entries:
            return build_status_embed(
                f"{title} — Recent changes", self.guild,
                fields=[("Audit trail",
                         "No recorded changes for this system yet.\n"
                         "Every panel change is written to the audit log automatically.", False)])
        lines = []
        for e in entries:
            ts = e.get("timestamp")
            when = f"<t:{int(ts)}:R>" if isinstance(ts, (int, float)) and ts else "recently"
            actor = f"<@{e['actor_id']}>" if e.get("actor_id") else "unknown"
            lines.append(f"• `{e.get('action', '?')}` by {actor} · {when}")
        return build_status_embed(f"{title} — Recent changes", self.guild,
                                  fields=[("Audit trail (latest 8)",
                                           "\n".join(lines)[:4000], False)])

    def _runtime_status(self, sub: dict) -> str:
        """Real runtime liveness: is this subsystem's module loaded & running?"""
        module = getattr(self.bot, sub.get("module_attr", ""), None)
        if module is None:
            return "🔴 module not loaded"
        # monitor-style modules expose a start method and keep a task/loop flag
        for flag in ("_task", "_monitor_task", "_running", "_loop"):
            t = getattr(module, flag, None)
            if t is not None:
                if hasattr(t, "is_running"):
                    return "🟢 running" if t.is_running() else "🟠 stopped"
                if isinstance(t, bool):
                    return "🟢 active" if t else "🟠 inactive"
        if hasattr(module, "start_monitoring") or hasattr(module, "start_tasks") \
                or hasattr(module, "start_loops") or hasattr(module, "start_event_monitor"):
            return "🟢 loaded (event-driven)"
        return "🟢 loaded"

    def _build_status_embed(self, title: str) -> discord.Embed:
        fields = []
        if self.tab == "overview":
            for sub in self.spec["subsystems"]:
                cfg = self._read(sub)
                status = format_bool(bool(cfg.get("enabled"))) if sub.get("supports_toggle", True) else "⚙️ Active"
                metrics = " · ".join(f"{label}: **{mf(self.bot, self.guild.id)[1]}**"
                                     for label, mf in sub.get("metrics", []))
                value = f"{status} · {self._runtime_status(sub)}"
                if metrics:
                    value += f"\n{metrics}"
                fields.append((f"{sub['label']}", value, False))
            return build_status_embed(title, self.guild, fields=fields,
                                      footer="Pick a subsystem above to configure it · every button performs real changes")
        sub = self._sub()
        cfg = self._read(sub)
        fields = [("Status", format_bool(bool(cfg.get("enabled")))
                   if sub.get("supports_toggle", True) else "⚙️ Active", False)]
        for setting in sub["settings"]:
            value = cfg.get(setting["key"], "")
            pretty = f"<#{value}>" if setting["type"] == "channel" and str(value).isdigit() else \
                     (f"<@&{value}>" if setting["type"] == "role" and str(value).isdigit() else value)
            fields.append((setting["label"], str(pretty) or "*(not set)*", True))
        for tg in sub.get("toggles", []):
            fields.append((tg["label"], format_bool(bool(cfg.get(tg["key"]))), True))
        for label, mf in sub.get("metrics", []):
            fields.append((label, str(mf(self.bot, self.guild.id)[1]), True))
        return build_status_embed(f"{title} — {sub['label']}", self.guild, fields=fields,
                                  footer="Edit settings / toggles below · changes persist immediately")

    def _build_test_embed(self, title: str) -> discord.Embed:
        results = getattr(self, "_last_diagnostics", None)
        if not results:
            return build_status_embed(f"{title} — Diagnostics", self.guild,
                                      fields=[("Ready", "Press **Run diagnostics** to validate every "
                                               "configured channel, role, and module.", False)])
        lines = []
        for sub_key, sub_label, checks in results:
            lines.append(f"**{sub_label}**")
            lines.extend(f"{'✅' if ok else '❌'} {msg}" for ok, msg in checks)
        return build_status_embed(f"{title} — Diagnostics", self.guild,
                                  fields=[("Results", "\n".join(lines)[:4000] or "All good.", False)],
                                  color=0x57F287)

    def _build_help_embed(self, title: str) -> discord.Embed:
        lines = []
        for sub in self.spec["subsystems"]:
            lines.append(f"**{sub['label']}** — config key `{sub['config_key']}`")
            for s in sub["settings"]:
                lines.append(f"• {s['label']}")
            for t in sub.get("toggles", []):
                lines.append(f"• {t['label']} (toggle)")
        return build_status_embed(f"{title} — Help", self.guild,
                                  fields=[("What these settings do",
                                           "\n".join(lines)[:4000], False),
                                          ("How panels work",
                                           "Every button validates your permissions, performs the real "
                                           "backend change, persists it to the server's data, writes an "
                                           "audit entry, and refreshes this panel.", False)])

    def _build_danger_embed(self, title: str) -> discord.Embed:
        lines = [f"♻️ **Reset {s['label']}** — erases `{s['config_key']}` for this server."
                 for s in self.spec["subsystems"]]
        backups = self._get_backups()
        backup_keys = [s["config_key"] for s in self.spec["subsystems"] if s["config_key"] in backups]
        extra = ""
        if backup_keys:
            extra = ("\n\n⏪ **Backups available** for: " +
                     ", ".join(f"`{k}`" for k in backup_keys) +
                     " — use the Restore buttons below.")
        return build_status_embed(f"{title} — Danger zone", self.guild, status=False,
                                  fields=[("Destructive operations",
                                           "\n".join(lines) +
                                           "\n\nEach reset asks for confirmation and keeps a restorable backup." + extra,
                                           False)])


# --------------------------------------------------------------------------- #
# Settings modal (real persistence on submit)                                  #
# --------------------------------------------------------------------------- #

def make_settings_modal(panel: GroupPanelView, sub: dict, cfg: dict):
    fields = sub["settings"][:5]

    class SettingsModal(ui.Modal):
        def __init__(self):
            super().__init__(title=f"{sub['label']} settings", timeout=180)
            self.inputs = {}
            for idx, setting in enumerate(fields):
                current = cfg.get(setting["key"], "")
                if setting["type"] == "channel" and str(current).isdigit():
                    current = str(current)
                ti = ui.TextInput(label=setting["label"][:45],
                                  style=discord.TextStyle.short,
                                  default=str(current)[:100] if current not in (None, "") else None,
                                  required=False,
                                  max_length=200)
                self.inputs[setting["key"]] = (setting, ti)
                self.add_item(ti)

        async def on_submit(self, interaction: discord.Interaction):
            async def work():
                new_cfg = panel._read(sub)
                for key, (setting, ti) in self.inputs.items():
                    raw = ti.value.strip()
                    if setting["type"] == "channel":
                        digits = "".join(ch for ch in raw if ch.isdigit())
                        if raw and not digits:
                            raise ValueError(f"{setting['label']}: enter a channel ID or name")
                        new_cfg[key] = digits
                    elif setting["type"] == "int":
                        val = int(float(raw)) if raw else 0
                        lo, hi = setting.get("min"), setting.get("max")
                        if lo is not None and val < lo:
                            raise ValueError(f"{setting['label']}: minimum is {lo}")
                        if hi is not None and val > hi:
                            raise ValueError(f"{setting['label']}: maximum is {hi}")
                        new_cfg[key] = val
                    elif setting["type"] == "float":
                        new_cfg[key] = float(raw) if raw else 0.0
                    else:
                        new_cfg[key] = raw
                panel._write(sub, new_cfg)
                return "saved"
            await panel.perform(interaction, f"{sub['key']}:edit", work,
                                success="✅ Settings saved and applied.", refresh=True)

        async def on_error(self, interaction: discord.Interaction, error: Exception):
            logger.error(f"Settings modal failed: {error}")
            try:
                await interaction.response.send_message(f"❌ Could not save: {error}", ephemeral=True)
            except Exception:
                pass

    return SettingsModal()


# --------------------------------------------------------------------------- #
# Live diagnostics (Test tab)                                                  #
# --------------------------------------------------------------------------- #

async def run_diagnostics(bot, guild: discord.Guild, spec: dict):
    results = []
    me = guild.me
    for sub in spec["subsystems"]:
        cfg = dm.get_guild_data(guild.id, sub["config_key"], {})
        if not isinstance(cfg, dict):
            cfg = {}
        checks = []
        module = getattr(bot, sub.get("module_attr", ""), None)
        checks.append((module is not None,
                       "module loaded" if module is not None else "module NOT loaded"))
        if sub.get("supports_toggle", True):
            checks.append((bool(cfg.get("enabled")), "enabled" if cfg.get("enabled")
                           else "disabled (enable it to activate the feature)"))
        for setting in sub["settings"]:
            value = cfg.get(setting["key"])
            if setting["type"] == "channel":
                if not value:
                    checks.append((False, f"{setting['label']}: not set"))
                else:
                    channel = guild.get_channel(int(value)) if str(value).isdigit() else None
                    ok = channel is not None
                    detail = f"{setting['label']}: {'#' + channel.name if channel else 'channel was deleted'}"
                    if ok and me is not None:
                        perms = channel.permissions_for(me)
                        if not perms.send_messages:
                            ok = False
                            detail += " — ⚠️ I cannot send messages there"
                    checks.append((ok, detail))
            elif setting["type"] == "role":
                if not value:
                    checks.append((False, f"{setting['label']}: not set"))
                else:
                    role_ids = value if isinstance(value, list) else [value]
                    for rid in (role_ids or [None]):
                        role = guild.get_role(int(rid)) if str(rid).isdigit() else None
                        checks.append((role is not None,
                                       f"{setting['label']}: {'@' + role.name if role else 'role was deleted'}"))
            else:
                checks.append((bool(value), f"{setting['label']}: {value or 'not set'}"))
        for label, mf in sub.get("metrics", []):
            _, count = mf(bot, guild.id)
            checks.append((True, f"{label}: {count}"))
        results.append((sub["key"], sub["label"], checks))
    return results


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #

async def open_system_panel(interaction: discord.Interaction, group_key: str):
    """Open (or re-open) the unified panel for a merged system group.
    Works whether or not the caller already deferred."""
    spec = SYSTEM_GROUPS.get(group_key)
    if spec is None:
        msg = f"❌ Unknown system `{group_key}`."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    try:
        view = GroupPanelView(interaction.client, interaction, group_key)
        embed = view.build_embed()
    except Exception as e:
        logger.error(f"Failed to build {group_key} panel: {e}")
        msg = f"❌ Could not build the {group_key} panel: {str(e)[:200]}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    try:
        if interaction.response.is_done():
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            view.message = await interaction.original_response()
    except Exception:
        pass
