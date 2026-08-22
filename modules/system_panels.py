import discord
from discord import ui
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
                 {"key": "mass_join_threshold", "label": "Join threshold (accounts)", "type": "int"},
                 {"key": "mass_join_window", "label": "Time window (seconds)", "type": "int"},
                 {"key": "min_account_age_days", "label": "Min account age (days)", "type": "int"},
                 {"key": "action", "label": "Action (lockdown/kick/ban/mute)", "type": "str"},
                 {"key": "alert_channel_id", "label": "Alert channel", "type": "channel"},
             ],
             "toggles": [{"key": "auto_lockdown", "label": "Auto lockdown"},
                         {"key": "age_filter_enabled", "label": "Account-age filter"}],
             "metrics": [_metric_count("Logged incidents", "raid_log")],
             "lockdown": True},
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

    TABS = ["overview", "test", "help", "danger"]

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
        for idx, sub in enumerate(self.spec["subsystems"]):
            row = min(1 + idx, 4)
            btn = ui.Button(label=f"Reset {sub['label']}", emoji="♻️",
                            style=discord.ButtonStyle.danger,
                            custom_id=f"miro:reset:{sub['key']}", row=row)
            btn.callback = self._make_reset_callback(sub)
            self.add_item(btn)

    def _make_reset_callback(self, sub: dict):
        async def callback(interaction: discord.Interaction):
            confirm_view = ConfirmView(self.author_id,
                                       f"Reset **{sub['label']}** to defaults? Its configuration will be erased.",
                                       danger=True)
            await interaction.response.send_message(
                f"♻️ Reset **{sub['label']}**? This erases its configuration for this server.",
                view=confirm_view, ephemeral=True)

            async def work():
                dm.delete_guild_data(self.guild.id, sub["config_key"])
                return "deleted"

            async def wait_and_run():
                await confirm_view.wait()
                if confirm_view.confirmed:
                    await self.perform(interaction, f"{sub['key']}:reset", work,
                                       success=f"♻️ {sub['label']} configuration reset to module defaults.")
                else:
                    try:
                        await interaction.followup.send("❎ Reset cancelled.", ephemeral=True)
                    except Exception:
                        pass

            import asyncio as _aio
            _aio.create_task(wait_and_run())
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
        if self.tab == "help":
            return self._build_help_embed(title)
        return self._build_danger_embed(title)

    def _build_status_embed(self, title: str) -> discord.Embed:
        fields = []
        if self.tab == "overview":
            for sub in self.spec["subsystems"]:
                cfg = self._read(sub)
                status = format_bool(bool(cfg.get("enabled"))) if sub.get("supports_toggle", True) else "⚙️ Active"
                metrics = " · ".join(f"{label}: **{mf(self.bot, self.guild.id)[1]}**"
                                     for label, mf in sub.get("metrics", []))
                fields.append((f"{sub['label']}", f"{status}" + (f"\n{metrics}" if metrics else ""), False))
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
        return build_status_embed(f"{title} — Danger zone", self.guild, status=False,
                                  fields=[("Destructive operations",
                                           "\n".join(lines) + "\n\nEach reset asks for confirmation first.",
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
                        new_cfg[key] = int(float(raw)) if raw else 0
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
    for sub in spec["subsystems"]:
        cfg = dm.get_guild_data(guild.id, sub["config_key"], {})
        if not isinstance(cfg, dict):
            cfg = {}
        checks = []
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
                    checks.append((channel is not None,
                                   f"{setting['label']}: {'#' + channel.name if channel else 'channel was deleted'}"))
            elif setting["type"] == "role":
                if not value:
                    checks.append((False, f"{setting['label']}: not set"))
                else:
                    role = guild.get_role(int(value)) if str(value).isdigit() else None
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
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)
        else:
            await interaction.response.send_message(embed=embed, view=view)
            view.message = await interaction.original_response()
    except Exception:
        pass
