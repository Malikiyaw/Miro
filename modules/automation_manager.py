"""Automation Manager: scale layer for agent-created automations & commands.

Powers the 1000x upgrade:
- update / pause / resume / run-now / bulk operations on automations
- interval schedules ("every 15 minutes") -> cron conversion
- per-guild quotas (100 automations, 100 prefix commands)
- run tracking (run_count, fail_count, last_run, last_error) with
  auto-pause after repeated failures
- enhanced prefix commands (aliases, per-command cooldown, permission)
"""
from typing import Any, Dict, List, Optional, Tuple

import discord

from data_manager import dm
from logger import logger

MAX_AUTOMATIONS_PER_GUILD = 100
MAX_COMMANDS_PER_GUILD = 100
MAX_BULK_ITEMS = 25
AUTO_PAUSE_AFTER_FAILURES = 10


# --------------------------------------------------------------------------- #
# Interval schedule conversion                                                #
# --------------------------------------------------------------------------- #

def interval_to_cron(schedule: Dict[str, Any]) -> Optional[str]:
    """Convert {"every_minutes": 15} / {"every_hours": 2} / {"daily_at": "09:00"}
    to a cron expression. Returns None if not an interval schedule."""
    if not isinstance(schedule, dict):
        return None
    every_minutes = schedule.get("every_minutes") or schedule.get("interval_minutes")
    every_hours = schedule.get("every_hours") or schedule.get("interval_hours")
    daily_at = schedule.get("daily_at") or schedule.get("time")
    weekly_day = schedule.get("weekly_on") or schedule.get("day_of_week")
    if every_minutes is not None:
        n = max(1, min(int(every_minutes), 1440))
        return f"*/{n} * * * *"
    if every_hours is not None:
        n = max(1, min(int(every_hours), 24))
        return f"0 */{n} * * *"
    if daily_at:
        try:
            hh, mm = str(daily_at).split(":")[:2]
            return f"{int(mm) % 60} {int(hh) % 24} * * *"
        except (ValueError, TypeError):
            return None
    if weekly_day is not None and daily_at is None:
        # weekly_on: 0=Sun..6=Sat, optional schedule["at"] = "HH:MM"
        at = str(schedule.get("at") or "12:00")
        try:
            hh, mm = at.split(":")[:2]
            days = str(weekly_day).lower()
            day_map = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
            day = day_map.get(days[:3], int(weekly_day) % 7 if str(weekly_day).isdigit() else 1)
            return f"{int(mm) % 60} {int(hh) % 24} * * {day}"
        except (ValueError, TypeError):
            return None
    return None


# --------------------------------------------------------------------------- #
# Quotas                                                                      #
# --------------------------------------------------------------------------- #

def check_quota(guild_id: int, kind: str, additional: int = 1) -> Tuple[bool, str]:
    if kind == "automation":
        current = len(dm.get_guild_data(guild_id, "automations", {}) or {})
        limit = MAX_AUTOMATIONS_PER_GUILD
    else:
        current = len(dm.get_guild_data(guild_id, "custom_commands", {}) or {})
        limit = MAX_COMMANDS_PER_GUILD
    if current + additional > limit:
        return False, f"Quota exceeded: {current}/{limit} {kind}s. Delete unused ones first."
    return True, ""


# --------------------------------------------------------------------------- #
# Automation CRUD helpers (shared with actions.py)                            #
# --------------------------------------------------------------------------- #

def get_automations(guild_id: int) -> dict:
    return dm.get_guild_data(guild_id, "automations", {}) or {}


def save_automations(guild_id: int, autos: dict):
    dm.update_guild_data(guild_id, "automations", autos)


def find_automation(guild_id: int, name: str) -> Tuple[Optional[str], Optional[dict]]:
    """Case-insensitive lookup. Returns (canonical_name, entry)."""
    autos = get_automations(guild_id)
    if name in autos:
        return name, autos[name]
    lowered = str(name).strip().lower()
    for key, entry in autos.items():
        if str(key).lower() == lowered:
            return key, entry
    return None, None


def record_run(guild_id: int, name: str, success: bool, error: str = "") -> dict:
    """Update run stats; auto-pause after repeated failures.
    Returns the updated entry (or {} if automation vanished)."""
    autos = get_automations(guild_id)
    entry = autos.get(name)
    if not isinstance(entry, dict):
        return {}
    entry["run_count"] = int(entry.get("run_count", 0)) + 1
    entry["last_run"] = __import__("time").time()
    if success:
        entry["fail_count"] = 0
        entry["last_error"] = ""
    else:
        entry["fail_count"] = int(entry.get("fail_count", 0)) + 1
        entry["last_error"] = str(error)[:300]
        if entry["fail_count"] >= AUTO_PAUSE_AFTER_FAILURES and not entry.get("paused"):
            entry["paused"] = True
            entry["paused_reason"] = f"auto-paused after {entry['fail_count']} consecutive failures"
            logger.warning(f"Automation '{name}' auto-paused in guild {guild_id}: {entry['paused_reason']}")
    save_automations(guild_id, autos)
    return entry


def health_line(entry: dict) -> str:
    """One-line health summary for embeds."""
    if entry.get("paused"):
        return f"⏸️ paused — {entry.get('paused_reason', 'manual')}"
    fails = int(entry.get("fail_count", 0))
    runs = int(entry.get("run_count", 0))
    if fails:
        return f"🟠 {runs} runs, {fails} recent failures"
    if runs:
        return f"🟢 {runs} runs OK"
    return "⚪ never run yet"


# --------------------------------------------------------------------------- #
# Prefix commands: enhanced creation + bulk                                   #
# --------------------------------------------------------------------------- #

def normalize_command_entry(name: str, code, aliases=None, cooldown_seconds=None,
                            permission=None, description=None) -> Tuple[Optional[str], Optional[str]]:
    """Return (stored_json, error). code is the raw stored JSON string/dict."""
    import json as _json
    if isinstance(code, dict):
        data = code
        stored = _json.dumps(data)
    else:
        try:
            data = _json.loads(str(code))
            if not isinstance(data, dict):
                data = {"command_type": "simple", "content": str(code)}
            stored = _json.dumps(data)
        except Exception:
            stored = _json.dumps({"command_type": "simple", "content": str(code)})
    if aliases:
        data["aliases"] = [str(a).lstrip("!").strip().lower()[:32] for a in aliases if str(a).strip()][:5]
        stored = _json.dumps(data)
    if cooldown_seconds is not None:
        try:
            data["cooldown"] = max(0, int(cooldown_seconds))
            stored = _json.dumps(data)
        except (ValueError, TypeError):
            pass
    if permission in ("everyone", "mod", "moderator", "admin", "administrator"):
        data["permission"] = "admin" if permission in ("admin", "administrator") else "mod"
        stored = _json.dumps(data)
    if description:
        data["description"] = str(description)[:100]
        stored = _json.dumps(data)
    return stored, None


def check_command_permission(message: discord.Message, data: dict) -> bool:
    """Gate for stored 'permission' field on custom commands."""
    required = (data or {}).get("permission")
    if not required:
        return True
    perms = message.author.guild_permissions
    if required == "admin":
        return perms.administrator
    if required == "mod":
        return perms.administrator or perms.manage_messages or perms.manage_guild
    return True


# --------------------------------------------------------------------------- #
# /automations management UI                                                  #
# --------------------------------------------------------------------------- #

class AutomationManagerView(discord.ui.View):
    """Ephemeral admin panel: paginated automation list with per-item controls."""

    def __init__(self, bot, user_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id
        self.page = 0
        self.selected: Optional[str] = None
        self.refresh()

    def _names(self) -> List[str]:
        return sorted(get_automations(self.guild_id).keys())

    def refresh(self):
        self.clear_items()
        names = self._names()
        if not names:
            return
        per_page = 10
        start = self.page * per_page
        page_names = names[start:start + per_page]
        select = discord.ui.Select(placeholder="Select an automation…",
                                   custom_id="miro:auto_pick")
        for n in page_names:
            entry = get_automations(self.guild_id).get(n, {})
            badge = "⏸️" if entry.get("paused") else "🟢"
            select.add_option(label=f"{badge} {n[:80]}", value=n)
        select.callback = self._pick
        self.add_item(select)
        if self.page > 0:
            prev = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary,
                                     custom_id="miro:auto_prev")
            prev.callback = self._prev
            self.add_item(prev)
        if start + per_page < len(names):
            nxt = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary,
                                    custom_id="miro:auto_next")
            nxt.callback = self._next
            self.add_item(nxt)
        if self.selected:
            entry = get_automations(self.guild_id).get(self.selected, {})
            if entry.get("paused"):
                b = discord.ui.Button(label="Resume", emoji="▶️",
                                      style=discord.ButtonStyle.success,
                                      custom_id="miro:auto_resume")
                b.callback = self._resume
                self.add_item(b)
            else:
                b = discord.ui.Button(label="Pause", emoji="⏸️",
                                      style=discord.ButtonStyle.secondary,
                                      custom_id="miro:auto_pause")
                b.callback = self._pause
                self.add_item(b)
            test = discord.ui.Button(label="Run now", emoji="⚡",
                                     style=discord.ButtonStyle.primary,
                                     custom_id="miro:auto_run")
            test.callback = self._run_now
            self.add_item(test)
            dele = discord.ui.Button(label="Delete", emoji="🗑️",
                                     style=discord.ButtonStyle.danger,
                                     custom_id="miro:auto_delete")
            dele.callback = self._delete
            self.add_item(dele)

    def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id and not (
                interaction.user.guild_permissions and interaction.user.guild_permissions.administrator):
            return False
        return True

    async def _pick(self, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)
        self.selected = interaction.data["values"][0]
        self.refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.selected = None
        self.refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page += 1
        self.selected = None
        self.refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _pause(self, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)
        autos = get_automations(self.guild_id)
        if self.selected in autos:
            autos[self.selected]["paused"] = True
            autos[self.selected]["paused_reason"] = "paused by admin"
            save_automations(self.guild_id, autos)
        self.refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _resume(self, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)
        autos = get_automations(self.guild_id)
        if self.selected in autos:
            entry = autos[self.selected]
            entry["paused"] = False
            entry["paused_reason"] = ""
            entry["fail_count"] = 0
            save_automations(self.guild_id, autos)
            self._reschedule(entry)
        self.refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def _reschedule(self, entry: dict):
        """Re-schedule a resumed cron automation via the ActionHandler."""
        try:
            handler = getattr(self.bot, "action_handler", None)
            if handler is None or entry.get("type") != "scheduled_task":
                return
            from task_scheduler import task_scheduler
            from croniter import croniter
            import datetime as _dt
            nxt = croniter(entry["cron"], _dt.datetime.now()).get_next(float)
            tid = handler._schedule_cron_job(self.guild_id, self.selected, entry["cron"],
                                             entry.get("handler", "send_message"),
                                             entry.get("params") or {},
                                             entry.get("channel_id"))
            if tid:
                entry["task_id"] = tid
                entry["next_run"] = nxt
                autos = get_automations(self.guild_id)
                autos[self.selected] = entry
                save_automations(self.guild_id, autos)
        except Exception as e:
            logger.warning(f"Resume reschedule failed for {self.selected}: {e}")

    async def _run_now(self, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        entry = get_automations(self.guild_id).get(self.selected)
        if not entry:
            return await interaction.followup.send("❌ Automation not found.", ephemeral=True)
        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            return await interaction.followup.send("❌ Action handler unavailable.", ephemeral=True)
        from actions import ScheduledTaskInteraction
        mock = ScheduledTaskInteraction(self.bot, self.guild_id)
        cid = (entry.get("params") or {}).get("channel_id") or entry.get("channel_id")
        if cid:
            ch = self.bot.get_channel(int(str(cid)))
            if ch is not None:
                mock.channel = ch
        try:
            ok, info = await handler.dispatch(mock, entry.get("handler", "send_message"),
                                              dict(entry.get("params") or {}))
            record_run(self.guild_id, self.selected, bool(ok), str(info.get("error", "")) if isinstance(info, dict) else "")
            await interaction.followup.send(
                f"⚡ Test run {'✅ succeeded' if ok else '❌ failed'}: "
                f"{str((info or {}).get('error', 'executed'))[:200]}",
                ephemeral=True)
        except Exception as e:
            record_run(self.guild_id, self.selected, False, str(e))
            await interaction.followup.send(f"❌ Test run failed: {str(e)[:200]}", ephemeral=True)
        self.refresh()
        try:
            await interaction.edit_original_response(embed=self.build_embed(), view=self)
        except Exception:
            pass

    async def _delete(self, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)
        autos = get_automations(self.guild_id)
        entry = autos.pop(self.selected, None)
        if entry:
            try:
                from task_scheduler import task_scheduler
                tid = entry.get("task_id")
                if tid:
                    task_scheduler.cancel_task(tid)
            except Exception:
                pass
            save_automations(self.guild_id, autos)
        self.selected = None
        self.refresh()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def build_embed(self) -> discord.Embed:
        autos = get_automations(self.guild_id)
        total = len(autos)
        paused = sum(1 for e in autos.values() if isinstance(e, dict) and e.get("paused"))
        embed = discord.Embed(
            title="⚙️ Automation Manager",
            description=f"**{total}** automation(s) · {paused} paused · "
                        f"quota {total}/{MAX_AUTOMATIONS_PER_GUILD}",
            color=discord.Color.blue())
        if self.selected:
            entry = autos.get(self.selected, {})
            cron = entry.get("cron", "—")
            embed.add_field(
                name=f"⚙️ {self.selected}",
                value=(f"Type: `{entry.get('type', '?')}` · Cron: `{cron}`\n"
                       f"Handler: `{entry.get('handler', '?')}` · {health_line(entry)}\n"
                       f"Last run: <t:{int(entry['last_run'])}:R>" if entry.get("last_run") else "Last run: never"),
                inline=False)
        else:
            names = self._names()
            per_page = 10
            start = self.page * per_page
            lines = []
            for n in names[start:start + per_page]:
                e = autos.get(n, {})
                lines.append(f"{('⏸️' if e.get('paused') else '🟢')} **{n}** — "
                             f"`{e.get('type', '?')}` · {health_line(e)}")
            embed.add_field(name="Automations",
                            value="\n".join(lines) or "None yet — ask the AI to create some!",
                            inline=False)
        embed.set_footer(text="Select an automation to manage it · changes are live")
        return embed
