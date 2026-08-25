"""Staff Management systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: staff_shifts.py, staff_reviews.py, staff_promo.py, promotion_service.py, applications.py, staff_system.py, staff_extras.py
"""



# ======================================================================
# From: modules/staff_shifts.py
# ======================================================================

import discord
from discord.ext import commands, tasks
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone

from data_manager import dm
from logger import logger


class StaffShiftSystem:
    def __init__(self, bot):
        self.bot = bot
        # self._shifts[guild_id][user_id] = { ... active shift data ... }
        self._shifts: Dict[int, Dict[int, dict]] = {}

    def start_tasks(self):
        """Start all background tasks. Call this after the event loop is running."""
        self._idle_monitor.start()

    def _load_active_shifts(self, guild_id: int):
        if guild_id not in self._shifts:
            self._shifts[guild_id] = dm.get_guild_data(guild_id, "active_staff_shifts", {})

    def _save_active_shifts(self, guild_id: int):
        dm.update_guild_data(guild_id, "active_staff_shifts", self._shifts.get(guild_id, {}))

    def _get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "staff_shifts_config", {
            "enabled": True,
            "on_duty_role_id": None,
            "idle_timeout_minutes": 30,
            "shift_channel_id": None,
            "notifications_enabled": True,
            "goals": {}, # user_id -> {"weekly_hours": X}
            "schedule": [] # list of {"user_id": X, "day": 0-6, "start": "HH:MM", "end": "HH:MM"}
        })

    def _save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "staff_shifts_config", config)

    def _get_history(self, guild_id: int) -> List[dict]:
        return dm.get_guild_data(guild_id, "staff_shifts_history", [])

    def _save_history(self, guild_id: int, history: List[dict]):
        dm.update_guild_data(guild_id, "staff_shifts_history", history[-1000:]) # Keep last 1000 shifts

    async def handle_shift_start(self, message, parts=None):
        """Handle !shift start command"""
        guild = message.guild
        if not guild: return
        
        self._load_active_shifts(guild.id)
        # Check if already on shift
        if message.author.id in self._shifts[guild.id]:
            await message.channel.send("❌ You are already on a shift!")
            return

        config = self._get_config(guild.id)
        
        # Assign on-duty role if configured
        role_id = config.get("on_duty_role_id")
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await message.author.add_roles(role, reason="Staff clocked in")
                except Exception as e:
                    logger.error(f"Failed to add on-duty role: {e}")

        # Initialize shift data
        if guild.id not in self._shifts:
            self._shifts[guild.id] = {}
        
        self._shifts[guild.id][message.author.id] = {
            "user_id": message.author.id,
            "username": str(message.author),
            "start_time": time.time(),
            "last_activity": time.time(),
            "messages": 0,
            "mod_actions": 0,
            "voice_minutes": 0,
            "tickets_resolved": 0,
            "notes": ""
        }
        self._save_active_shifts(guild.id)

        # Notification
        if config.get("notifications_enabled") and config.get("shift_channel_id"):
            channel = guild.get_channel(config.get("shift_channel_id"))
            if channel:
                await channel.send(f"🟢 **{message.author.display_name}** clocked in.")

        await message.channel.send(f"✅ Shift started! Good luck, {message.author.display_name}.")

    async def handle_shift_end(self, message, parts=None):
        """Handle !shift end or !endshift command"""
        guild = message.guild
        if not guild: return
        self._load_active_shifts(guild.id)
        if message.author.id not in self._shifts[guild.id]:
            await message.channel.send("❌ You don't have an active shift!")
            return

        # Extract notes if provided
        # Parts can be ['shift', 'end', 'note', ...] or ['endshift', 'note', ...]
        notes = ""
        if parts:
            if parts[0].lower() == "shift" and len(parts) > 2:
                notes = " ".join(parts[2:])
            elif parts[0].lower() == "endshift" and len(parts) > 1:
                notes = " ".join(parts[1:])

        await self._end_shift(guild, message.author.id, notes=notes)
        await message.channel.send(f"✅ Shift ended and recorded. Thanks for your work!")

    async def _end_shift(self, guild: discord.Guild, user_id: int, reason: str = "Clocked out", notes: str = ""):
        self._load_active_shifts(guild.id)
        if user_id not in self._shifts[guild.id]:
            return

        shift_data = self._shifts[guild.id].pop(user_id)
        self._save_active_shifts(guild.id)
        end_time = time.time()
        duration_seconds = end_time - shift_data["start_time"]
        duration_hours = duration_seconds / 3600

        shift_data.update({
            "end_time": end_time,
            "duration_hours": duration_hours,
            "end_reason": reason,
            "notes": notes or shift_data.get("notes", "")
        })

        # Remove on-duty role
        config = self._get_config(guild.id)
        role_id = config.get("on_duty_role_id")
        if role_id:
            role = guild.get_role(role_id)
            member = guild.get_member(user_id)
            if role and member:
                try:
                    await member.remove_roles(role, reason="Staff clocked out")
                except Exception as e:
                    logger.error(f"Failed to remove on-duty role: {e}")

        # Save to history
        history = self._get_history(guild.id)
        history.append(shift_data)
        self._save_history(guild.id, history)

        # Update user stats for promotion system
        udata = dm.get_guild_data(guild.id, f"user_{user_id}", {})
        udata["on_duty_hours"] = udata.get("on_duty_hours", 0) + duration_hours
        udata["on_duty_messages"] = udata.get("on_duty_messages", 0) + shift_data["messages"]
        dm.update_guild_data(guild.id, f"user_{user_id}", udata)

        # Notification
        if config.get("notifications_enabled") and config.get("shift_channel_id"):
            channel = guild.get_channel(config.get("shift_channel_id"))
            if channel:
                duration_str = f"{int(duration_hours)}h {int((duration_seconds % 3600) / 60)}m"
                await channel.send(f"🔴 **{shift_data['username']}** clocked out. Duration: {duration_str}. Reason: {reason}")

    async def handle_show_shifts(self, message, parts=None):
        """Handle !show shifts command"""
        guild = message.guild
        guild_id = guild.id
        
        self._load_active_shifts(guild_id)
        shifts = self._shifts.get(guild_id, {})
        
        if not shifts:
            await message.channel.send("No active shifts!")
            return
        
        embed = discord.Embed(
            title="📅 Active Shifts",
            color=discord.Color.blue()
        )
        
        for user_id, shift_data in shifts.items():
            member = guild.get_member(user_id)
            name = member.display_name if member else shift_data.get("username", "Unknown")
            duration = (time.time() - shift_data["start_time"]) / 3600
            embed.add_field(
                name=name,
                value=f"On duty for: {duration:.1f}h\nMessages: {shift_data['messages']}",
                inline=True
            )
        
        await message.channel.send(embed=embed)

    async def handle_myshifts(self, message, parts=None):
        """Handle !myshifts command"""
        guild = message.guild
        if not guild: return

        history = self._get_history(guild.id)
        user_shifts = [s for s in history if s.get("user_id") == message.author.id]

        if not user_shifts:
            await message.channel.send("You have no shift history recorded.")
            return

        total_hours = sum(s.get("duration_hours", 0) for s in user_shifts)
        recent = user_shifts[-5:]
        
        embed = discord.Embed(title=f"📊 Shift History: {message.author.display_name}", color=discord.Color.blue())
        embed.add_field(name="Total Hours", value=f"{total_hours:.1f}h", inline=True)
        embed.add_field(name="Total Shifts", value=str(len(user_shifts)), inline=True)

        history_text = ""
        for s in reversed(recent):
            date = datetime.fromtimestamp(s["start_time"]).strftime("%m/%d %H:%M")
            history_text += f"• {date}: {s.get('duration_hours', 0):.1f}h ({s.get('end_reason', 'N/A')})\n"

        embed.add_field(name="Recent Shifts", value=history_text or "None", inline=False)

        await message.channel.send(embed=embed)

    @tasks.loop(minutes=5)
    async def _idle_monitor(self):
        """Automatically clock out users who have been idle for too long."""
        for guild_id, guild_shifts in list(self._shifts.items()):
            guild = self.bot.get_guild(guild_id)
            if not guild: continue

            config = self._get_config(guild_id)
            idle_timeout_mins = config.get("idle_timeout_minutes", 30)
            if idle_timeout_mins <= 0: continue

            now = time.time()
            for user_id, shift_data in list(guild_shifts.items()):
                last_active = shift_data.get("last_activity", shift_data["start_time"])
                if (now - last_active) / 60 > idle_timeout_mins:
                    await self._end_shift(guild, user_id, reason="Idle timeout")

    @_idle_monitor.before_loop
    async def before_idle_monitor(self):
        await self.bot.wait_until_ready()

    async def track_message(self, message: discord.Message):
        """Track messages sent while on duty"""
        if not message.guild or message.author.bot: return
        gid, uid = message.guild.id, message.author.id
        self._load_active_shifts(gid)
        if uid in self._shifts[gid]:
            self._shifts[gid][uid]["messages"] += 1
            self._shifts[gid][uid]["last_activity"] = time.time()
            self._save_active_shifts(gid)

    async def track_moderation_action(self, guild_id: int, user_id: int):
        self._load_active_shifts(guild_id)
        if user_id in self._shifts[guild_id]:
            self._shifts[guild_id][user_id]["mod_actions"] += 1
            self._shifts[guild_id][user_id]["last_activity"] = time.time()
            self._save_active_shifts(guild_id)

    async def track_voice_minutes(self, guild_id: int, user_id: int, minutes: int):
        self._load_active_shifts(guild_id)
        if user_id in self._shifts[guild_id]:
            self._shifts[guild_id][user_id]["voice_minutes"] += minutes
            self._save_active_shifts(guild_id)

    async def track_ticket_resolved(self, guild_id: int, user_id: int):
        self._load_active_shifts(guild_id)
        if user_id in self._shifts[guild_id]:
            self._shifts[guild_id][user_id]["tickets_resolved"] += 1
            self._shifts[guild_id][user_id]["last_activity"] = time.time()
            self._save_active_shifts(guild_id)

    # --- Legacy / Merged Handlers from original code ---

    async def handle_task_assign(self, message, parts=None):
        """Handle !task assign command"""
        if not parts or len(parts) < 3:
            await message.channel.send("Usage: !task assign @user <task>")
            return
        
        guild = message.guild
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", "").replace("!", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        task_name = " ".join(parts[2:])
        target = guild.get_member(user_id)
        if not target:
            await message.channel.send("User not found!")
            return
        
        tasks_data = dm.get_guild_data(guild.id, "staff_tasks", {})
        if str(user_id) not in tasks_data: tasks_data[str(user_id)] = []
        
        tasks_data[str(user_id)].append({
            "task": task_name,
            "assigned_by": str(message.author),
            "assigned_at": time.time(),
            "completed": False
        })
        dm.update_guild_data(guild.id, "staff_tasks", tasks_data)
        await message.channel.send(f"✅ Task assigned to {target.display_name}")

    async def handle_task_complete(self, message, parts=None):
        """Handle !task complete command"""
        guild = message.guild
        user_id = str(message.author.id)
        tasks_data = dm.get_guild_data(guild.id, "staff_tasks", {})
        
        if user_id not in tasks_data or not tasks_data[user_id]:
            await message.channel.send("No tasks assigned to you!")
            return

        # Mark last pending task as complete
        found = False
        for task in reversed(tasks_data[user_id]):
            if not task["completed"]:
                task["completed"] = True
                task["completed_at"] = time.time()
                found = True
                await message.channel.send(f"✅ Task completed: {task['task']}")
                break

        if found:
            dm.update_guild_data(guild.id, "staff_tasks", tasks_data)
            await self.track_ticket_resolved(guild.id, message.author.id) # Counts as activity
        else:
            await message.channel.send("All your tasks are already completed!")

    async def handle_task_list(self, message, parts=None):
        """Handle !tasks command"""
        guild = message.guild
        tasks_data = dm.get_guild_data(guild.id, "staff_tasks", {})
        
        if not tasks_data:
            await message.channel.send("No active tasks!")
            return
        
        embed = discord.Embed(title="📋 Staff Tasks", color=discord.Color.blue())
        for uid, u_tasks in tasks_data.items():
            member = guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            pending = [t["task"] for t in u_tasks if not t["completed"]]
            if pending:
                embed.add_field(name=name, value="\n".join(pending[:5]), inline=False)
        
        if not embed.fields:
            await message.channel.send("No pending tasks!")
        else:
            await message.channel.send(embed=embed)

    async def handle_warn(self, message, parts=None):
        """Handle !warn command (staff warning)"""
        if not parts or len(parts) < 3:
            await message.channel.send("Usage: !warn @user <reason>")
            return
        
        guild = message.guild
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", "").replace("!", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        reason = " ".join(parts[2:])
        target = guild.get_member(user_id)
        if not target:
            await message.channel.send("User not found!")
            return
        
        warnings = dm.get_guild_data(guild.id, f"staff_warnings_{user_id}", [])
        warnings.append({
            "reason": reason,
            "by": str(message.author),
            "at": time.time()
        })
        dm.update_guild_data(guild.id, f"staff_warnings_{user_id}", warnings)
        await self.track_moderation_action(guild.id, message.author.id)
        await message.channel.send(f"⚠️ Staff warning issued to {target.display_name}")

    async def handle_warnings(self, message, parts=None):
        """Handle !warnings command (view staff warnings)"""
        guild = message.guild
        target = message.author
        if parts and len(parts) > 1:
            try:
                uid = int(parts[1].replace("<@", "").replace(">", "").replace("!", ""))
                target = guild.get_member(uid) or target
            except: pass

        warnings = dm.get_guild_data(guild.id, f"staff_warnings_{target.id}", [])
        if not warnings:
            await message.channel.send(f"{target.display_name} has no staff warnings.")
            return

        embed = discord.Embed(title=f"⚠️ Warnings: {target.display_name}", color=discord.Color.red())
        for w in warnings[-10:]:
            date = datetime.fromtimestamp(w["at"]).strftime("%Y-%m-%d")
            embed.add_field(name=f"{date} by {w['by']}", value=w['reason'], inline=False)
        await message.channel.send(embed=embed)

    async def handle_activity_logs(self, message, parts=None):
        await message.channel.send("Use `!shiftspanel` to view detailed activity logs.")

    async def handle_all_activity(self, message, parts=None):
        await message.channel.send("Use `!shiftspanel` to view all staff activity.")

    async def add_schedule_entry(self, guild_id: int, user_id: int, day: int, start: str, end: str):
        config = self._get_config(guild_id)
        config["schedule"].append({
            "user_id": user_id,
            "day": day, # 0=Mon, 6=Sun
            "start": start,
            "end": end
        })
        self._save_config(guild_id, config)

    async def remove_schedule_entry(self, guild_id: int, index: int):
        config = self._get_config(guild_id)
        if 0 <= index < len(config["schedule"]):
            config["schedule"].pop(index)
            self._save_config(guild_id, config)
            return True
        return False

    async def set_hour_goal(self, guild_id: int, user_id: int, weekly_hours: float):
        config = self._get_config(guild_id)
        config["goals"][str(user_id)] = {"weekly_hours": weekly_hours}
        self._save_config(guild_id, config)

    async def setup(self, interaction):
        """Initial setup via autosetup"""
        guild = interaction.guild
        config = self._get_config(guild.id)
        
        category = discord.utils.get(guild.categories, name="Staff Hub")
        if not category:
            category = await guild.create_category("Staff Hub")
        
        channel = discord.utils.get(guild.text_channels, name="shift-logs")
        if not channel:
            channel = await guild.create_text_channel("shift-logs", category=category)
            await channel.set_permissions(guild.default_role, read_messages=False)
        
        config["shift_channel_id"] = channel.id
        self._save_config(guild.id, config)
        
        return True

    # ---- Slash-command adapters ----

    async def start_shift(self, interaction):
        """Slash-command adapter: clock in."""
        await self.handle_shift_start(_SlashMessageShim(interaction))

    async def end_shift(self, interaction):
        """Slash-command adapter: clock out."""
        await self.handle_shift_end(_SlashMessageShim(interaction))

    async def start_break(self, interaction):
        """Slash-command adapter: start a break."""
        guild_id = interaction.guild.id
        self._load_active_shifts(guild_id)
        if interaction.user.id not in self._shifts[guild_id]:
            return await interaction.response.send_message("❌ You don't have an active shift!", ephemeral=True)
        if self._shifts[guild_id][interaction.user.id].get("on_break"):
            return await interaction.response.send_message("☕ You are already on break!", ephemeral=True)
        self._shifts[guild_id][interaction.user.id]["on_break"] = True
        self._shifts[guild_id][interaction.user.id]["break_start"] = time.time()
        self._save_active_shifts(guild_id)
        await interaction.response.send_message("☕ Break started. Your shift is paused.", ephemeral=True)

    async def end_break(self, interaction):
        """Slash-command adapter: end a break."""
        guild_id = interaction.guild.id
        self._load_active_shifts(guild_id)
        if interaction.user.id not in self._shifts[guild_id]:
            return await interaction.response.send_message("❌ You don't have an active shift!", ephemeral=True)
        shift = self._shifts[guild_id][interaction.user.id]
        if not shift.get("on_break"):
            return await interaction.response.send_message("☕ You are not on break!", ephemeral=True)
        break_minutes = int((time.time() - shift.get("break_start", time.time())) / 60)
        shift["on_break"] = False
        shift["break_start"] = None
        shift["total_break_minutes"] = shift.get("total_break_minutes", 0) + break_minutes
        self._save_active_shifts(guild_id)
        await interaction.response.send_message(f"✅ Break ended. Total break time: {shift['total_break_minutes']} minutes.", ephemeral=True)

    async def get_my_shifts(self, interaction):
        """Slash-command adapter: show shift history."""
        await self.handle_myshifts(_SlashMessageShim(interaction))


class _SlashMessageShim:
    """Adapts a discord.Interaction to the message-based shift handlers."""

    def __init__(self, interaction):
        self.guild = interaction.guild
        self.author = interaction.user
        self.channel = _SlashChannelShim(interaction)


class _SlashChannelShim:
    def __init__(self, interaction):
        self.interaction = interaction
        self.id = getattr(interaction.channel, "id", None)
        self._responded = False

    async def send(self, content=None, embed=None, **kwargs):
        if not self._responded:
            self._responded = True
            try:
                await self.interaction.response.send_message(content or "", embed=embed)
                return
            except Exception:
                pass
        try:
            await self.interaction.followup.send(content or "", embed=embed)
        except Exception:
            pass


def staff_shifts_extension_setup(bot):
    return StaffShiftSystem(bot)



# ======================================================================
# From: modules/staff_reviews.py
# ======================================================================

import discord
from discord.ext import commands, tasks
import asyncio
import json
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from data_manager import dm
from logger import logger


class StaffReviewSystem:
    def __init__(self, bot):
        self.bot = bot

    def start_tasks(self):
        self._review_monitor.start()

    def _get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "staff_reviews_config", {
            "enabled": True,
            "cycle": "monthly", # weekly, bi-weekly, monthly
            "start_day": 0, # 0=Mon
            "last_cycle_start": 0,
            "next_cycle_start": 0,
            "review_channel_id": None,
            "notifications_enabled": True,
            "criteria": [
                {"name": "Responsiveness", "weight": 1.0},
                {"name": "Helpfulness", "weight": 1.0},
                {"name": "Professionalism", "weight": 1.0},
                {"name": "Activity", "weight": 1.0},
                {"name": "Initiative", "weight": 1.0},
                {"name": "Rule Knowledge", "weight": 1.0}
            ],
            "thresholds": {
                "warning": 2.5,
                "promotion": 4.5
            },
            "weights": {
                "admin": 0.5,
                "peer": 0.3,
                "self": 0.2
            },
            "staff_roles": []
        })

    def _save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "staff_reviews_config", config)

    def _get_active_reviews(self, guild_id: int) -> dict:
        """Returns { user_id: { self: {}, peer: { voter_id: {} }, admin: {} } }"""
        return dm.get_guild_data(guild_id, "staff_active_reviews", {})

    def _save_active_reviews(self, guild_id: int, reviews: dict):
        dm.update_guild_data(guild_id, "staff_active_reviews", reviews)

    def _get_history(self, guild_id: int) -> List[dict]:
        return dm.get_guild_data(guild_id, "staff_reviews_history", [])

    def _save_history(self, guild_id: int, history: List[dict]):
        dm.update_guild_data(guild_id, "staff_reviews_history", history[-500:])

    @tasks.loop(hours=24)
    async def _review_monitor(self):
        """Monitor and trigger review cycles."""
        for guild in self.bot.guilds:
            config = self._get_config(guild.id)
            if not config.get("enabled"): continue

            now = time.time()
            if config.get("next_cycle_start", 0) > 0 and config.get("next_cycle_start", 0) <= now:
                await self.start_review_cycle(guild.id)

    @_review_monitor.before_loop
    async def before_review_monitor(self):
        await self.bot.wait_until_ready()

    async def start_review_cycle(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild: return

        # 1. Compile existing active reviews before clearing
        active = self._get_active_reviews(guild_id)
        if active:
            await self.compile_reviews(guild_id)

        config = self._get_config(guild_id)
        config["last_cycle_start"] = time.time()
        
        # Calculate next cycle start
        days = 30
        if config["cycle"] == "weekly": days = 7
        elif config["cycle"] == "bi-weekly": days = 14
        
        config["next_cycle_start"] = time.time() + (days * 86400)
        self._save_config(guild_id, config)

        # Clear active reviews and start fresh
        self._save_active_reviews(guild_id, {})
        
        # Notify staff
        staff_members = []
        for role_id in config.get("staff_roles", []):
            role = guild.get_role(role_id)
            if role:
                for m in role.members:
                    if not m.bot and m not in staff_members:
                        staff_members.append(m)
        
        if not staff_members:
            # Fallback to members with manage_messages
            staff_members = [m for m in guild.members if m.guild_permissions.manage_messages and not m.bot]

        if config.get("notifications_enabled", True):
            for member in staff_members:
                try:
                    embed = discord.Embed(
                        title="📝 Staff Review Cycle Started!",
                        description=f"A new review cycle has started in **{guild.name}**. Please complete your self-review and peer reviews.",
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="How to complete", value="Use `!review` in the server to open the review menu.")
                    await member.send(embed=embed)
                except:
                    pass

        if config.get("review_channel_id"):
            channel = guild.get_channel(config.get("review_channel_id"))
            if channel:
                await channel.send("🚀 **A new Staff Review cycle has begun.** Staff members have been notified via DM.")

    async def handle_review_command(self, message, parts=None):
        """Handle !review command - opens review selection menu"""
        guild = message.guild
        if not guild: return
        
        config = self._get_config(guild.id)
        # Check if staff
        is_staff = False
        if config.get("staff_roles"):
            is_staff = any(r.id in config["staff_roles"] for r in message.author.roles)
        else:
            is_staff = message.author.guild_permissions.manage_messages
            
        if not is_staff and not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is for staff members only.")
            return

        embed = discord.Embed(title="📝 Staff Review Menu", description="Select what kind of review you'd like to perform.", color=discord.Color.blue())
        view = ReviewSelectionView(self, guild.id, message.author.id)
        await message.channel.send(embed=embed, view=view, delete_after=60)

    async def handle_myreview(self, message, parts=None):
        """Handle !myreview command - shows user's own performance trend"""
        guild = message.guild
        if not guild: return
        
        history = self._get_history(guild.id)
        user_reviews = [r for r in history if r.get("user_id") == message.author.id]
        
        if not user_reviews:
            await message.channel.send("You have no completed reviews in history yet.")
            return

        recent = user_reviews[-6:]
        scores = [r["composite_score"] for r in recent]
        
        embed = discord.Embed(title=f"📈 Performance Trend: {message.author.display_name}", color=discord.Color.green())
        
        # Simple text graph
        graph = ""
        for score in scores:
            bar = "█" * int(score * 2)
            graph += f"`{score:.1f}` {bar}\n"
        
        embed.add_field(name="Last 6 Cycles", value=graph or "N/A")
        embed.add_field(name="Current Status", value="✅ Excellent" if scores[-1] >= 4.0 else "⚠️ Needs Improvement" if scores[-1] < 3.0 else "🟢 Good", inline=False)
        
        await message.channel.send(embed=embed)

    async def submit_self_review(self, guild_id: int, user_id: int, ratings: dict):
        active = self._get_active_reviews(guild_id)
        uid_str = str(user_id)
        if uid_str not in active: active[uid_str] = {"self": {}, "peer": {}, "admin": {}}
        active[uid_str]["self"] = ratings
        self._save_active_reviews(guild_id, active)

    async def submit_peer_review(self, guild_id: int, voter_id: int, target_id: int, ratings: dict):
        active = self._get_active_reviews(guild_id)
        target_str = str(target_id)
        if target_str not in active: active[target_str] = {"self": {}, "peer": {}, "admin": {}}
        active[target_str]["peer"][str(voter_id)] = ratings
        self._save_active_reviews(guild_id, active)

    async def submit_admin_review(self, guild_id: int, admin_id: int, target_id: int, ratings: dict):
        active = self._get_active_reviews(guild_id)
        target_str = str(target_id)
        if target_str not in active: active[target_str] = {"self": {}, "peer": {}, "admin": {}}
        active[target_str]["admin"] = ratings # Admin review is authoritative
        self._save_active_reviews(guild_id, active)

    async def compile_reviews(self, guild_id: int):
        """Compile all active reviews into history and generate report."""
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        
        config = self._get_config(guild_id)
        active = self._get_active_reviews(guild_id)
        history = self._get_history(guild_id)
        
        cycle_id = int(time.time())
        report_data = []

        for uid_str, data in active.items():
            user_id = int(uid_str)
            member = guild.get_member(user_id)
            if not member: continue
            
            # Calculate average peer score
            peer_scores = data.get("peer", {})
            avg_peer = {}
            if peer_scores:
                for criteria in config["criteria"]:
                    name = criteria["name"]
                    vals = [p[name] for p in peer_scores.values() if name in p]
                    if vals: avg_peer[name] = sum(vals) / len(vals)
            
            # Composite calculation with configurable weights
            weights = config.get("weights", {"admin": 0.5, "peer": 0.3, "self": 0.2})
            composite = 0
            weights_found = 0
            
            def get_avg_rating(ratings):
                if not ratings: return 0
                return sum(ratings.values()) / len(ratings)

            admin_score = get_avg_rating(data.get("admin"))
            peer_score = get_avg_rating(avg_peer)
            self_score = get_avg_rating(data.get("self"))

            scores = []
            if admin_score: scores.append(admin_score * weights.get("admin", 0.5)); weights_found += weights.get("admin", 0.5)
            if peer_score: scores.append(peer_score * weights.get("peer", 0.3)); weights_found += weights.get("peer", 0.3)
            if self_score: scores.append(self_score * weights.get("self", 0.2)); weights_found += weights.get("self", 0.2)

            final_score = sum(scores) / weights_found if weights_found > 0 else 0

            entry = {
                "user_id": user_id,
                "username": str(member),
                "cycle_id": cycle_id,
                "timestamp": time.time(),
                "self_ratings": data.get("self"),
                "peer_ratings_avg": avg_peer,
                "admin_ratings": data.get("admin"),
                "composite_score": final_score
            }
            history.append(entry)
            report_data.append(entry)

            # DM results to staff member
            if config.get("notifications_enabled"):
                try:
                    embed = discord.Embed(title="📊 Your Review Results", color=discord.Color.blue())
                    embed.add_field(name="Composite Score", value=f"{final_score:.2f} / 5.0")
                    status = "✅ Promotion Eligible" if final_score >= config["thresholds"]["promotion"] else "⚠️ Warning/Probation" if final_score <= config["thresholds"]["warning"] else "🟢 Satisfactory"
                    embed.add_field(name="Status", value=status)
                    await member.send(embed=embed)
                except: pass

        self._save_history(guild_id, history)
        self._save_active_reviews(guild_id, {}) # Clear active
        
        # Post report to channel
        if config.get("review_channel_id") and report_data:
            channel = guild.get_channel(config.get("review_channel_id"))
            if channel:
                report_data.sort(key=lambda x: x["composite_score"], reverse=True)
                desc = "\n".join([f"• **{x['username']}**: {x['composite_score']:.2f}" for x in report_data[:10]])
                embed = discord.Embed(title="📋 Staff Review Report", description=f"Cycle: {datetime.now().strftime('%Y-%m-%d')}\n\n{desc}", color=discord.Color.gold())
                await channel.send(embed=embed)

    async def setup(self, interaction):
        """Initial setup via autosetup"""
        guild = interaction.guild
        config = self._get_config(guild.id)
        
        category = discord.utils.get(guild.categories, name="Staff Hub")
        if not category:
            category = await guild.create_category("Staff Hub")
        
        channel = discord.utils.get(guild.text_channels, name="staff-reviews")
        if not channel:
            channel = await guild.create_text_channel("staff-reviews", category=category)
            await channel.set_permissions(guild.default_role, read_messages=False)
        
        config["review_channel_id"] = channel.id
        self._save_config(guild.id, config)
        
        return True


class ReviewSelectionView(discord.ui.View):
    def __init__(self, system, guild_id, user_id):
        super().__init__(timeout=60)
        self.system = system
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.button(label="Self Review", style=discord.ButtonStyle.primary, custom_id="rev_cfg_self_review")
    async def self_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        await interaction.response.send_modal(ReviewModal(self.system, self.guild_id, "self", interaction.user))

    @discord.ui.button(label="Peer Review", style=discord.ButtonStyle.secondary, custom_id="rev_cfg_peer_review")
    async def peer_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        
        class PeerSelect(discord.ui.UserSelect):
            def __init__(self, system, guild_id):
                super().__init__(placeholder="Select staff member to review...", min_values=1, max_values=1)
                self.system = system
                self.guild_id = guild_id
            async def callback(self, it):
                target = self.values[0]
                if target.id == it.user.id:
                    return await it.response.send_message("You cannot peer-review yourself! Use Self Review.", ephemeral=True)
                # merged: modules.staff_reviews is defined in this file
                await it.response.send_modal(ReviewModal(self.system, self.guild_id, "peer", target, it.user.id))
        
        v = discord.ui.View(); v.add_item(PeerSelect(self.system, self.guild_id))
        await interaction.response.send_message("Select a staff member to peer review:", view=v, ephemeral=True)


class ReviewModal(discord.ui.Modal):
    def __init__(self, system, guild_id, review_type, target_member, voter_id=None):
        super().__init__(title=f"{review_type.title()} Review: {target_member.display_name}")
        self.system = system
        self.guild_id = guild_id
        self.review_type = review_type
        self.target_id = target_member.id
        self.voter_id = voter_id or target_member.id
        
        config = system._get_config(guild_id)
        # Combine all criteria into one multi-line text input to bypass the 5-field limit
        self.criteria_names = [c["name"] for c in config["criteria"]]
        
        instruction = ", ".join(self.criteria_names)
        self.ratings_input = discord.ui.TextInput(
            label=f"Ratings for: {instruction}",
            placeholder="Format: 5, 4, 5, 3, 4, 5 (one per criteria)",
            style=discord.TextStyle.paragraph,
            default=", ".join(["5"] * len(self.criteria_names)),
            required=True
        )
        self.add_item(self.ratings_input)
        
        self.notes_input = discord.ui.TextInput(
            label="Additional Notes",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        ratings = {}
        try:
            scores = [int(s.strip()) for s in self.ratings_input.value.split(",")]
            if len(scores) < len(self.criteria_names):
                return await interaction.response.send_message(f"❌ Please provide {len(self.criteria_names)} ratings.", ephemeral=True)

            for i, name in enumerate(self.criteria_names):
                score = scores[i]
                if not 1 <= score <= 5: raise ValueError
                ratings[name] = score
        except:
            return await interaction.response.send_message("❌ Invalid ratings. Use comma-separated numbers 1-5.", ephemeral=True)
        
        if self.review_type == "self":
            await self.system.submit_self_review(self.guild_id, self.target_id, ratings)
        elif self.review_type == "peer":
            await self.system.submit_peer_review(self.guild_id, self.voter_id, self.target_id, ratings)
        elif self.review_type == "admin":
            await self.system.submit_admin_review(self.guild_id, self.voter_id, self.target_id, ratings)

        await interaction.response.send_message(f"✅ {self.review_type.title()} review submitted!", ephemeral=True)


def staff_reviews_extension_setup(bot):
    return StaffReviewSystem(bot)



# ======================================================================
# From: modules/staff_promo.py
# ======================================================================

# ======================================================================

import asyncio
from datetime import datetime
import json
import discord
from discord.ext import commands, tasks
from typing import Optional, List

import time
from data_manager import dm
from logger import logger
# merged: modules.promotion_service is defined in this file


class PromotionReviewView(discord.ui.View):
    def __init__(self, bot=None, guild_id=None, user_id=None, tier_name=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.tier_name = tier_name

        # Static custom_ids for persistence
        self.approve_btn.custom_id = "promo_approve_btn"
        self.deny_btn.custom_id = "promo_deny_btn"

    def _get_review_data(self, message_id: int):
        return dm.load_json(f"promo_review_{message_id}", default={"upvotes": [], "downvotes": [], "user_id": self.user_id, "tier_name": self.tier_name})

    def _save_review_data(self, message_id: int, data: dict):
        dm.save_json(f"promo_review_{message_id}", data)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅", custom_id="promo_approve_btn")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Only Senior Staff can vote.", ephemeral=True)

        data = self._get_review_data(interaction.message.id)
        if interaction.user.id not in data.get("upvotes", []):
            if "upvotes" not in data: data["upvotes"] = []
            data["upvotes"].append(interaction.user.id)
            if interaction.user.id in data.get("downvotes", []):
                data["downvotes"].remove(interaction.user.id)
            self._save_review_data(interaction.message.id, data)

        await self._update_message(interaction, data)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="promo_deny_btn")
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Only Senior Staff can vote.", ephemeral=True)

        data = self._get_review_data(interaction.message.id)
        if interaction.user.id not in data.get("downvotes", []):
            if "downvotes" not in data: data["downvotes"] = []
            data["downvotes"].append(interaction.user.id)
            if interaction.user.id in data.get("upvotes", []):
                data["upvotes"].remove(interaction.user.id)
            self._save_review_data(interaction.message.id, data)

        await self._update_message(interaction, data)

    async def _update_message(self, interaction, data):
        embed = interaction.message.embeds[0]

        user_id = data.get("user_id") or self.user_id
        tier_name = data.get("tier_name") or self.tier_name

        if not user_id:
            try: user_id = int(embed.footer.text.split("ID: ")[1])
            except: pass
        if not tier_name:
            try: tier_name = embed.description.split("**")[1]
            except: pass

        up_count = len(data.get("upvotes", []))
        down_count = len(data.get("downvotes", []))

        found = False
        for i, field in enumerate(embed.fields):
            if field.name == "Votes":
                embed.set_field_at(i, name="Votes", value=f"✅ {up_count} | ❌ {down_count}", inline=True)
                found = True
                break
        if not found:
            embed.add_field(name="Votes", value=f"✅ {up_count} | ❌ {down_count}", inline=True)

        if not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.message.edit(embed=embed, view=self)

        # Execution logic
        config = interaction.client.staff_promo._get_full_config(interaction.guild_id)
        req_votes = config.get("tier_requirements", {}).get(tier_name, {}).get("votes", 3)

        if up_count >= req_votes:
            # Execute promotion
            guild = interaction.guild
            member = guild.get_member(user_id)
            if member:
                if data.get("executed"): return
                data["executed"] = True
                self._save_review_data(interaction.message.id, data)

                success, msg = await interaction.client.staff_promo.manual_promote(guild, member, tier_name, config)
                try:
                    if success:
                        await interaction.followup.send(f"✅ Threshold met! {member.mention} promoted to **{tier_name}**.", ephemeral=False)
                    else:
                        await interaction.followup.send(f"❌ Promotion failed: {msg}", ephemeral=False)
                except: pass
                # Disable buttons
                for child in self.children: child.disabled = True
                await interaction.message.edit(view=self)


class StaffPromotionSystem:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.promotion_service = PromotionService()
        
        self._default_tiers = []  # Auto-detected from server roles
        
        self._default_trial_settings = {
            "enabled": True,
            "duration_days": 14,
            "evaluation_metrics": {
                "activity_score_min": 0.3,
                "ticket_resolution_min": 5,
                "voice_hours_min": 10
            },
            "auto_revert_on_fail": True
        }
        
        self._default_metrics = {
            "xp": {"weight": 0.20, "max": 5000, "enabled": True},
            "tenure_days": {"weight": 0.15, "max": 90, "enabled": True},
            "messages": {"weight": 0.15, "max": 1000, "enabled": True},
            "tickets_resolved": {"weight": 0.20, "max": 50, "enabled": True},
            "voice_minutes": {"weight": 0.10, "max": 3600, "enabled": True},
            "rep_received": {"weight": 0.08, "max": 100, "enabled": True},
            "rep_given": {"weight": 0.06, "max": 100, "enabled": True},
            "gamification_score": {"weight": 0.10, "max": 100, "enabled": True},
            "level": {"weight": 0.06, "max": 50, "enabled": True}
        }
        
        self._default_settings = {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        }
        
        self._default_tier_requirements = {}
        
        
        self._default_rewards = {
            "promotion_reward_coins": 500,
            "promotion_reward_title": True,
            "demotion_penalty_coins": 200,
        }
        
        self._last_promotion_time = {}
        self._last_demotion_time = {}
        self._last_notification_time = {}
    
    def _detect_server_roles(self, guild: discord.Guild) -> List[dict]:
        """Auto-detect existing staff roles in the server"""
        detected = []
        
        server_roles = guild.roles
        
        rank_keywords = {
            "owner": 1.0,
            "admin": 0.95,
            "head": 0.8,
            "senior": 0.6,
            "lead": 0.75,
            "mod": 0.4,
            "trial": 0.2,
            "helper": 0.15,
            "trainee": 0.1,
        }
        
        for role in sorted(server_roles, key=lambda x: x.position, reverse=True):
            role_name_lower = role.name.lower()
            
            if any(kw in role_name_lower for kw in rank_keywords):
                if not role.is_default():
                    detected.append({
                        "name": role.name,
                        "threshold": rank_keywords.get([k for k in rank_keywords if k in role_name_lower][0], 0.3),
                        "role_name": role.name
                    })
        
        if not detected:
            detected = [
                {"name": "Trial Moderator", "threshold": 0.2, "role_name": "Trial Moderator"},
                {"name": "Moderator", "threshold": 0.4, "role_name": "Moderator"},
                {"name": "Senior Moderator", "threshold": 0.6, "role_name": "Senior Moderator"},
                {"name": "Head Moderator", "threshold": 0.8, "role_name": "Head Moderator"},
            ]
        
        detected.sort(key=lambda x: x.get("threshold", 0))
        return detected

    def _get_full_config(self, guild_id: int) -> dict:
        guild = self.bot.get_guild(guild_id)
        
        cfg = dm.get_guild_data(guild_id, "staff_promo_config", {})
        
        if not cfg.get("tiers") or cfg.get("tiers") == []:
            if guild:
                cfg.setdefault("tiers", self._detect_server_roles(guild))
            else:
                cfg.setdefault("tiers", self._get_fallback_tiers())
        
        cfg.setdefault("metrics", self._default_metrics)
        cfg.setdefault("settings", self._default_settings)
        cfg.setdefault("rewards", self._default_rewards)
        cfg.setdefault("roles_by_tier", {})
        cfg.setdefault("tier_requirements", {})
        cfg.setdefault("pending_reviews", [])
        cfg.setdefault("trial_settings", self._default_trial_settings)
        cfg.setdefault("staff_applications", {})
        cfg.setdefault("application_tracking", {})
        
        return cfg
    
    def _get_fallback_tiers(self) -> List[dict]:
        """Fallback tiers if no server roles detected"""
        return [
            {"name": "Trial Staff", "threshold": 0.2, "role_name": "Trial Staff"},
            {"name": "Staff", "threshold": 0.4, "role_name": "Staff"},
            {"name": "Senior Staff", "threshold": 0.6, "role_name": "Senior Staff"},
            {"name": "Head Staff", "threshold": 0.8, "role_name": "Head Staff"},
            {"name": "Admin", "threshold": 0.95, "role_name": "Admin"},
        ]

    async def _promotion_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    await self._evaluate_guild(guild)
            except Exception as e:
                logger.error(f"Staff promo loop error: {e}")
            await asyncio.sleep(3600)

    async def _evaluate_guild(self, guild: discord.Guild):
        config = self._get_full_config(guild.id)
        settings = config.get("settings", self._default_settings)
        
        if not settings.get("auto_promote", True):
            return
        
        tiers = config.get("tiers", self._default_tiers)
        metrics = config.get("metrics", self._default_metrics)
        excluded = settings.get("excluded_users", [])
        
        role_ids = dict(config.get("roles_by_tier", {}))
        for tier in tiers:
            tier_name = tier.get("name")
            if tier_name not in role_ids or not role_ids[tier_name]:
                role_name = tier.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name] = r.id
        
        for member in guild.members:
            if member.bot or member.id in excluded:
                continue
            
            if not self.promotion_service._check_tenure(member, settings):
                continue
            
            await self._evaluate_member(guild, member, tiers, role_ids, metrics, settings, config)

    def _check_tenure(self, member: discord.Member, settings: dict) -> bool:
        min_hours = settings.get("min_tenure_hours", 72)
        if not member.joined_at:
            return False
        tenure_hours = (discord.utils.utcnow() - member.joined_at).total_seconds() / 3600
        return tenure_hours >= min_hours

    async def _check_trial_period(self, guild_id: int, member: discord.Member, config: dict) -> Optional[str]:
        """Check trial period status: 'active', 'complete', 'revert', 'extend', or None if not in trial"""
        trial_settings = config.get("trial_settings", self._default_trial_settings)
        if not trial_settings.get("enabled", True):
            return None
            
        # Check if member has trial moderator role
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        trial_role_id = None
        
        for tier in tiers:
            if tier.get("name") == "Trial Moderator":
                trial_role_id = role_ids.get(tier.get("name"))
                break
                
        if not trial_role_id:
            return None
            
        has_trial_role = any(r.id == trial_role_id for r in member.roles)
        if not has_trial_role:
            return None
            
        # Get trial start time from user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        trial_start = udata.get("trial_start_time")
        
        if not trial_start:
            # Set trial start time if not set
            udata["trial_start_time"] = discord.utils.utcnow().timestamp()
            dm.update_guild_data(guild_id, f"user_{member.id}", udata)
            return "active"
            
        trial_duration_days = trial_settings.get("duration_days", 14)
        trial_seconds = trial_duration_days * 24 * 3600
        elapsed_time = discord.utils.utcnow().timestamp() - trial_start
        
        if elapsed_time >= trial_seconds:
            # Trial period ended, evaluate performance
            evaluation_result = await self.promotion_service._evaluate_trial_performance(guild_id, member, trial_settings, config)
            if evaluation_result == "pass":
                return "complete"
            elif evaluation_result == "fail":
                return "revert"
            else:
                return "extend"  # Need more time
        else:
            return "active"

    async def _evaluate_trial_performance(self, guild_id: int, member: discord.Member, trial_settings: dict, config: dict) -> str:
        """Evaluate trial performance based on metrics"""
        metrics = trial_settings.get("evaluation_metrics", {
            "activity_score_min": 0.3,
            "ticket_resolution_min": 5,
            "voice_hours_min": 10
        })
        
        # Get user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        
        # Check activity score (from promotion system)
        current_score = self.promotion_service._compute_score(guild_id, member.id, member, config.get("metrics", self._default_metrics))
        activity_score_min = metrics.get("activity_score_min", 0.3)
        
        # Check ticket resolutions (would need ticket system integration)
        ticket_resolutions = udata.get("ticket_resolutions", 0)
        ticket_resolution_min = metrics.get("ticket_resolution_min", 5)
        
        # Check voice hours
        voice_hours = udata.get("voice_minutes", 0) / 60  # Convert to hours
        voice_hours_min = metrics.get("voice_hours_min", 10)
        
        # Evaluate criteria
        score_pass = current_score >= activity_score_min
        ticket_pass = ticket_resolutions >= ticket_resolution_min
        voice_pass = voice_hours >= voice_hours_min
        
        # Require at least 2 out of 3 criteria to pass
        passes = sum([score_pass, ticket_pass, voice_pass])
        
        if passes >= 2:
            return "pass"
        else:
            return "fail"

    async def put_on_probation(self, guild: discord.Guild, member: discord.Member, duration_days: int, reason: str):
        """Put a staff member on probation."""
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        udata["on_probation"] = True
        udata["probation_reason"] = reason
        udata["probation_start_timestamp"] = time.time()
        udata["probation_end_timestamp"] = time.time() + (duration_days * 24 * 3600)
        dm.update_guild_data(guild.id, f"user_{member.id}", udata)

        # Log
        logger.info(f"StaffPromo[{guild.id}] {member} put on probation for {duration_days} days: {reason}")

        try:
            await member.send(f"⚠️ You have been placed on probation for **{duration_days} days**.\nReason: {reason}\nYour promotion eligibility is paused during this period.")
        except: pass
        return True

    async def end_probation(self, guild: discord.Guild, member: discord.Member):
        """End a staff member's probation early."""
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        udata["on_probation"] = False
        dm.update_guild_data(guild.id, f"user_{member.id}", udata)

        try:
            await member.send(f"✅ Your probation has ended. You are now eligible for promotions again.")
        except: pass
        return True

    async def _handle_trial_revert(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, settings, config):
        """Handle automatic reversion from trial period"""
        # Remove trial moderator role
        trial_role_id = role_ids.get("Trial Moderator")
        if trial_role_id:
            trial_role = guild.get_role(trial_role_id)
            if trial_role and trial_role in member.roles:
                try:
                    await member.remove_roles(trial_role)
                except Exception as e:
                    logger.error(f"Failed to remove trial role: {e}")
        
        # Add back any previous roles if they existed
        # For simplicity, we'll just remove the trial role and let system evaluate normally
        
        # Reset trial data
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        if "trial_start_time" in udata:
            del udata["trial_start_time"]
            dm.update_guild_data(guild.id, f"user_{member.id}", udata)
        
        # Notify user
        try:
            await member.send("📋 Your trial period has ended and you did not meet the requirements for promotion. "
                            "Your Trial Moderator role has been removed. You can reapply after improving your activity.")
        except:
            pass
            
        logger.info(f"StaffPromo[{guild.id}] {member} reverted from trial period due to insufficient performance")

    async def _evaluate_member(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, metrics, settings, config):
        user_id = member.id
        cooldown_key = f"{guild.id}_{user_id}"
        cooldown_hours = settings.get("promotion_cooldown_hours", 24)
        
        if cooldown_key in self._last_promotion_time:
            last = self._last_promotion_time[cooldown_key]
            if (discord.utils.utcnow() - last).total_seconds() < cooldown_hours * 3600:
                return
        
        score = self.promotion_service._compute_score(guild.id, user_id, member, metrics)
        target_tier = None
        for tier in sorted(tiers, key=lambda t: t.get("threshold", 0)):
            if score >= tier.get("threshold", 0):
                target_tier = tier
        
        # Check for trial period completion/reversion
        trial_status = await self._check_trial_period(guild.id, member, config)
        if trial_status == "revert":
            await self._handle_trial_revert(guild, member, tiers, role_ids, settings, config)
            return
        elif trial_status == "extend":
            # Extend trial period, don't process promotion
            return
        elif trial_status == "complete":
            # Trial completed successfully, allow promotion to next tier
            pass
        
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        target_index = -1 if not target_tier else tiers.index(target_tier)
        
        if target_index > current_index:
            target_tier_name = target_tier.get("name")
            if not self.promotion_service._check_tier_requirements(guild.id, member, target_tier_name, config):
                return
            
            if settings.get("review_mode", False):
                await self.promotion_service._submit_promotion_review(guild, member, target_tier, score, config)
                return
            
            await self._promote_member(guild, member, target_tier, tiers, role_ids, current_index, settings, config)
            self._last_promotion_time[cooldown_key] = discord.utils.utcnow()
        elif settings.get("auto_demote", False) and target_index < current_index and current_index > 0:
            demotion_cooldown_key = f"{guild.id}_{user_id}_demote"
            demotion_cooldown_hours = settings.get("demotion_cooldown_hours", 168)
            if demotion_cooldown_key in self._last_demotion_time:
                last = self._last_demotion_time[demotion_cooldown_key]
                if (discord.utils.utcnow() - last).total_seconds() < demotion_cooldown_hours * 3600:
                    return
            
            buffer = settings.get("demotion_threshold_buffer", 0.1)
            if score < target_tier.get("threshold", 0) - buffer:
                await self._demote_member(guild, member, target_index, tiers, role_ids, current_index, settings, config)
                self._last_demotion_time[demotion_cooldown_key] = discord.utils.utcnow()
        
        if settings.get("notify_near_promotion", True):
            await self._check_progress_notification(guild, member, score, tiers, role_ids, settings)

    def _get_current_tier_index(self, member: discord.Member, tiers, role_ids) -> int:
        for idx, tier in enumerate(tiers):
            rid = role_ids.get(tier.get("name"))
            if rid and any(r.id == rid for r in member.roles):
                return idx
        return -1



    async def _promote_member(self, guild: discord.Guild, member: discord.Member, target_tier, tiers, role_ids, current_index, settings, config):
        new_role_id = role_ids.get(target_tier.get("name"))
        if new_role_id:
            try:
                role = guild.get_role(new_role_id)
                if role and role not in member.roles:
                    await member.add_roles(role)
            except Exception as e:
                logger.error(f"Failed to assign promotion role: {e}")
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            tier = tiers[idx]
            rid = role_ids.get(tier.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in member.roles:
                    try:
                        await member.remove_roles(rm)
                    except:
                        pass
        
        await self._apply_promotion_rewards(guild, member, target_tier.get("name"), config)
        await self._log_promotion(guild, member, target_tier.get("name"), settings)

    async def _demote_member(self, guild: discord.Guild, member: discord.Member, target_index, tiers, role_ids, current_index, settings, config):
        if target_index < 0:
            for idx in range(current_index + 1):
                if idx >= len(tiers):
                    continue
                tier = tiers[idx]
                rid = role_ids.get(tier.get("name"))
                if rid:
                    rm = guild.get_role(rid)
                    if rm and rm in member.roles:
                        try:
                            await member.remove_roles(rm)
                        except:
                            pass
            new_tier_name = "None"
        else:
            target_tier = tiers[target_index]
            new_role_id = role_ids.get(target_tier.get("name"))
            
            for idx in range(current_index + 1):
                if idx >= len(tiers):
                    continue
                tier = tiers[idx]
                rid = role_ids.get(tier.get("name"))
                if rid:
                    rm = guild.get_role(rid)
                    if rm and rm in member.roles:
                        try:
                            await member.remove_roles(rm)
                        except:
                            pass
            
            if new_role_id:
                try:
                    role = guild.get_role(new_role_id)
                    if role:
                        await member.add_roles(role)
                except:
                    pass
            
            new_tier_name = target_tier.get("name")
        
        await self._apply_demotion_penalty(guild, member, new_tier_name, config)
        await self._log_demotion(guild, member, new_tier_name, settings)

    async def _apply_promotion_rewards(self, guild: discord.Guild, member: discord.Member, new_tier: str, config: dict):
        rewards = config.get("rewards", self._default_rewards)
        
        coins = rewards.get("promotion_reward_coins", 0)
        if coins > 0:
            try:
                user_data = dm.get_guild_data(guild.id, f"user_{member.id}", {})
                user_data["coins"] = user_data.get("coins", 0) + coins
                dm.update_guild_data(guild.id, f"user_{member.id}", user_data)
            except Exception as e:
                logger.error(f"Failed to give promotion coins: {e}")
        
        if rewards.get("promotion_reward_title", True):
            try:
                title_data = dm.get_guild_data(guild.id, f"user_{member.id}_titles", {})
                title = f"Promoted {new_tier}"
                if title not in title_data.get("titles", []):
                    title_data.setdefault("titles", []).append(title)
                    dm.update_guild_data(guild.id, f"user_{member.id}_titles", title_data)
            except:
                pass

    async def _apply_demotion_penalty(self, guild: discord.Guild, member: discord.Member, new_tier: str, config: dict):
        rewards = config.get("rewards", self._default_rewards)
        
        coins = rewards.get("demotion_penalty_coins", 0)
        if coins > 0:
            try:
                user_data = dm.get_guild_data(guild.id, f"user_{member.id}", {})
                user_data["coins"] = max(0, user_data.get("coins", 0) - coins)
                dm.update_guild_data(guild.id, f"user_{member.id}", user_data)
            except Exception as e:
                logger.error(f"Failed to apply demotion penalty: {e}")

    async def _check_progress_notification(self, guild: discord.Guild, member: discord.Member, score: float, tiers, role_ids, settings):
        notif_key = f"{guild.id}_{member.id}_progress"
        if notif_key in self._last_notification_time:
            last = self._last_notification_time[notif_key]
            if (discord.utils.utcnow() - last).total_seconds() < 86400:
                return
        
        config = self._get_full_config(guild.id)
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        
        if current_index < len(tiers) - 1:
            next_tier = tiers[current_index + 1]
            threshold = next_tier.get("threshold", 0)
            threshold_val = settings.get("near_promotion_threshold", 0.05)
            
            if threshold - score <= threshold_val and threshold - score > 0:
                percent_away = (threshold - score) * 100
                try:
                    await member.send(f"🎯 You're **{percent_away:.1f}%** away from being promoted to **{next_tier.get('name')}**! Keep it up!")
                    self._last_notification_time[notif_key] = discord.utils.utcnow()
                except:
                    pass

    async def _log_promotion(self, guild: discord.Guild, member: discord.Member, new_tier: str, settings: dict):
        logger.info(f"StaffPromo[{guild.id}] {member} promoted to {new_tier}")
        
        # Save to history
        logs = dm.get_guild_data(guild.id, "promotion_logs", [])
        logs.append({
            "ts": time.time(),
            "user": str(member),
            "user_id": member.id,
            "to": new_tier,
            "reason": "Automatic criteria met"
        })
        dm.update_guild_data(guild.id, "promotion_logs", logs[-50:])

        log_ch_id = settings.get("log_channel")
        if log_ch_id:
            channel = guild.get_channel(int(log_ch_id))
            if channel:
                try:
                    embed = discord.Embed(
                        title="🎖️ Staff Promotion",
                        description=f"{member.mention} has been promoted to **{new_tier}**",
                        color=discord.Color.green()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                except:
                    pass
        
        announce_ch_id = settings.get("announce_channel")
        if announce_ch_id and settings.get("notify_on_promotion", True):
            channel = guild.get_channel(int(announce_ch_id))
            if channel:
                try:
                    await channel.send(f"🎉 Congratulations {member.mention}! Promoted to **{new_tier}**!")
                except:
                    pass

    async def _log_demotion(self, guild: discord.Guild, member: discord.Member, new_tier: str, settings: dict):
        logger.info(f"StaffPromo[{guild.id}] {member} demoted to {new_tier}")
        
        # Save to history
        logs = dm.get_guild_data(guild.id, "promotion_logs", [])
        logs.append({
            "ts": time.time(),
            "user": str(member),
            "user_id": member.id,
            "to": new_tier,
            "reason": "Criteria no longer met"
        })
        dm.update_guild_data(guild.id, "promotion_logs", logs[-50:])

        log_ch_id = settings.get("log_channel")
        if log_ch_id:
            channel = guild.get_channel(int(log_ch_id))
            if channel:
                try:
                    embed = discord.Embed(
                        title="📉 Staff Demotion",
                        description=f"{member.mention} has been demoted to **{new_tier}**",
                        color=discord.Color.red()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                except:
                    pass
        
        announce_ch_id = settings.get("announce_channel")
        if announce_ch_id and settings.get("notify_on_demotion", True):
            channel = guild.get_channel(int(announce_ch_id))
            if channel:
                try:
                    await channel.send(f"⚠️ {member.mention} has been demoted to **{new_tier}**.")
                except:
                    pass

    async def manual_promote(self, guild: discord.Guild, target_member: discord.Member, tier_name: str, config: dict):
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
        if not tier:
            return False, "Tier not found"
        
        tier_index = tiers.index(tier)
        
        for t in tiers:
            tier_name_key = t.get("name")
            if tier_name_key not in role_ids or not role_ids[tier_name_key]:
                role_name = t.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name_key] = r.id
        
        current_index = self._get_current_tier_index(target_member, tiers, role_ids)
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            t = tiers[idx]
            rid = role_ids.get(t.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in member.roles:
                    try:
                        await target_member.remove_roles(rm)
                    except:
                        pass
        
        new_role_id = role_ids.get(tier.get("name"))
        if new_role_id:
            try:
                role = guild.get_role(new_role_id)
                if role:
                    await target_member.add_roles(role)
            except Exception as e:
                return False, str(e)
        
        await self._apply_promotion_rewards(guild, target_member, tier.get("name"), config)
        
        cooldown_key = f"{guild.id}_{target_member.id}"
        self._last_promotion_time[cooldown_key] = discord.utils.utcnow()
        
        return True, f"Promoted to {tier.get('name')}"

    async def submit_peer_vote(self, guild_id, voter_id, target_id):
        """Submit a peer vote for a staff member."""
        votes = dm.get_guild_data(guild_id, f"peer_votes_{target_id}", [])
        if voter_id not in votes:
            votes.append(voter_id)
            dm.update_guild_data(guild_id, f"peer_votes_{target_id}", votes)
            return True
        return False

    async def manual_demote(self, guild: discord.Guild, target_member: discord.Member, tier_name: str, config: dict):
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        if tier_name.lower() == "none":
            tier_index = -1
        else:
            tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
            if not tier:
                return False, "Tier not found"
            tier_index = tiers.index(tier)
        
        for t in tiers:
            tier_name_key = t.get("name")
            if tier_name_key not in role_ids or not role_ids[tier_name_key]:
                role_name = t.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name_key] = r.id
        
        current_index = self._get_current_tier_index(target_member, tiers, role_ids)
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            t = tiers[idx]
            rid = role_ids.get(t.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in target_member.roles:
                    try:
                        await target_member.remove_roles(rm)
                    except:
                        pass
        
        if tier_index >= 0:
            target_tier = tiers[tier_index]
            new_role_id = role_ids.get(target_tier.get("name"))
            if new_role_id:
                try:
                    role = guild.get_role(new_role_id)
                    if role:
                        await target_member.add_roles(role)
                except:
                    pass
            new_tier_name = target_tier.get("name")
        else:
            new_tier_name = "None"
        
        await self._apply_demotion_penalty(guild, target_member, new_tier_name, config)
        
        demotion_cooldown_key = f"{guild.id}_{target_member.id}_demote"
        self._last_demotion_time[demotion_cooldown_key] = discord.utils.utcnow()
        
        return True, f"Demoted to {new_tier_name}"

    def get_config(self, guild_id: int) -> dict:
        return self._get_full_config(guild_id)

    async def setup(self, interaction: discord.Interaction, params: dict = None):
        guild = interaction.guild
        
        doc_name = "staff-promo-guide"
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            doc_channel = await guild.create_text_channel(doc_name, overwrites=overwrites)
        except:
            doc_channel = interaction.channel
        
        config = self._get_full_config(guild.id)
        tiers = config.get("tiers", self._default_tiers)
        metrics = config.get("metrics", self._default_metrics)
        settings = config.get("settings", self._default_settings)
        rewards = config.get("rewards", self._default_rewards)
        
        embed = discord.Embed(
            title="🧭 Staff Auto-Promotion System",
            description="Automatically promotes/demotes staff based on performance metrics",
            color=discord.Color.green()
        )
        
        tiers_text = "\n".join([f"• **{t['name']}**: {int(t['threshold']*100)}%" for t in tiers])
        embed.add_field(name="📊 Promotion Tiers", value=tiers_text or "No tiers configured", inline=False)
        
        embed.add_field(
            name="⚙️ Configuration",
            value=(
                f"• Auto-promote: `{settings.get('auto_promote', True)}`\n"
                f"• Auto-demote: `{settings.get('auto_demote', False)}`\n"
                f"• Min tenure: `{settings.get('min_tenure_hours', 72)}h`\n"
                f"• Promotion cooldown: `{settings.get('promotion_cooldown_hours', 24)}h`"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🎁 Promotion Rewards",
            value=(
                f"• Coins: `{rewards.get('promotion_reward_coins', 500)}`\n"
                f"• Title: `{rewards.get('promotion_reward_title', True)}`\n"
                f"• Demotion penalty: `{rewards.get('demotion_penalty_coins', 200)}` coins"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💬 Commands",
            value=(
                "• `!staffpromo status` - Check your score\n"
                "• `!staffpromo leaderboard` - Top staff\n"
                "• `!staffpromo progress` - Progress to next tier\n"
                "• `!staffpromo config` - View config (admin)\n"
                "• `!staffpromo promote @user <tier>` - Promote (admin)\n"
                "• `!staffpromo demote @user <tier>` - Demote (admin)\n"
                "• `!staffpromo exclude add/remove @user` - Exclude user\n"
                "• `!staffpromo roles add/remove <tier> @role` - Map roles"
            ),
            inline=False
        )
        
        await doc_channel.send(embed=embed)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["vote"] = json.dumps({"command_type": "peer_vote"})
        custom_cmds["staffpromo status"] = json.dumps({"command_type": "staffpromo_status"})
        custom_cmds["staffpromo leaderboard"] = json.dumps({"command_type": "staffpromo_leaderboard"})
        custom_cmds["staffpromo config"] = json.dumps({"command_type": "staff_promo_config"})
        custom_cmds["staffpromo progress"] = json.dumps({"command_type": "staffpromo_progress"})
        custom_cmds["staffpromo promote"] = json.dumps({"command_type": "staffpromo_promote"})
        custom_cmds["staffpromo demote"] = json.dumps({"command_type": "staffpromo_demote"})
        custom_cmds["staffpromo exclude"] = json.dumps({"command_type": "staffpromo_exclude"})
        custom_cmds["staffpromo roles"] = json.dumps({"command_type": "staffpromo_roles"})
        custom_cmds["staffpromo review"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo requirements"] = json.dumps({"command_type": "staffpromo_requirements"})
        custom_cmds["staffpromo bonuses"] = json.dumps({"command_type": "staffpromo_bonuses"})
        custom_cmds["staffpromo approve"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo reject"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo tiers"] = json.dumps({"command_type": "staffpromo_tiers"})
        custom_cmds["tiers"] = json.dumps({"command_type": "staffpromo_tiers"})
        
        custom_cmds["help staffpromo"] = json.dumps({
            "command_type": "help_embed",
            "title": "Staff Promotion System Help",
            "description": "Auto-promotes/demotes staff based on performance metrics.",
            "fields": [
                {"name": "!staffpromo status", "value": "Check your current promotion score.", "inline": False},
                {"name": "!staffpromo leaderboard", "value": "View top staff members by score.", "inline": False},
                {"name": "!staffpromo progress", "value": "See progress to next tier.", "inline": False},
                {"name": "!staffpromo requirements", "value": "View tier requirements.", "inline": False},
                {"name": "!staffpromo config", "value": "View configuration (admin).", "inline": False},
                {"name": "!staffpromo promote @user <tier>", "value": "Manually promote user (admin).", "inline": False},
                {"name": "!staffpromo demote @user <tier>", "value": "Manually demote user (admin).", "inline": False},
                {"name": "!staffpromo exclude add/remove @user", "value": "Exclude from auto-promotion (admin).", "inline": False},
                {"name": "!staffpromo roles add/remove <tier> @role", "value": "Map roles to tiers (admin).", "inline": False},
                {"name": "!staffpromo tiers", "value": "Manage promotion tiers interactively (admin).", "inline": False},
                {"name": "!tiers", "value": "Manage promotion tiers interactively (admin).", "inline": False},
                {"name": "!staffpromo review", "value": "View pending reviews (admin).", "inline": False},
                {"name": "!staffpromo approve @user", "value": "Approve promotion (admin).", "inline": False},
                {"name": "!staffpromo reject @user", "value": "Reject promotion (admin).", "inline": False},
                {"name": "!help staffpromo", "value": "Show this help embed.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("✅ Staff Promotion System set up!", ephemeral=True)


class StaffPromoTiersView(discord.ui.View):
    """Interactive hierarchy management for staff tiers"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        embed = discord.Embed(
            title="🏗️ Staff Promotion Tiers Management",
            description="Manage the staff hierarchy tiers below.",
            color=discord.Color.blue()
        )

        if tiers:
            for i, tier in enumerate(tiers):
                embed.add_field(
                    name=f"{i+1}. {tier['name']}",
                    value=f"Role: <@&{tier['role_id']}>\nRequirements: {tier.get('requirements', 'None')}",
                    inline=False
                )
        else:
            embed.add_field(name="No Tiers", value="Add tiers using the buttons below.", inline=False)

        return embed

    @discord.ui.button(label="Add Tier", style=discord.ButtonStyle.success, emoji="➕")
    async def add_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddTierModal(self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Tier", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        if not tiers:
            return await interaction.response.send_message("No tiers to edit.", ephemeral=True)

        select = TierSelect(tiers)
        await interaction.response.send_message("Select a tier to edit:", view=TierSelectView(select), ephemeral=True)

    @discord.ui.button(label="Remove Tier", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        if not tiers:
            return await interaction.response.send_message("No tiers to remove.", ephemeral=True)

        select = TierSelect(tiers, action="remove")
        await interaction.response.send_message("Select a tier to remove:", view=TierSelectView(select), ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class AddTierModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Add New Tier")
        self.guild_id = guild_id

        self.name_input = discord.ui.TextInput(label="Tier Name", placeholder="e.g. Junior Moderator")
        self.role_id_input = discord.ui.TextInput(label="Role ID", placeholder="123456789")
        self.requirements_input = discord.ui.TextInput(label="Requirements", style=discord.TextStyle.long, placeholder="Activity requirements, etc.", required=False)

        self.add_item(self.name_input)
        self.add_item(self.role_id_input)
        self.add_item(self.requirements_input)

    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        try:
            role_id_int = int(self.role_id_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid role ID.", ephemeral=True)

        new_tier = {
            "name": self.name_input.value,
            "role_id": role_id_int,
            "requirements": self.requirements_input.value or "None"
        }
        tiers.append(new_tier)
        config["tiers"] = tiers
        dm.update_guild_data(self.guild_id, "staff_promo_config", config)

        await interaction.response.send_message(f"✅ Added tier '{self.name_input.value}'!", ephemeral=True)


class TierSelect(discord.ui.Select):
    def __init__(self, tiers: list, action: str = "edit"):
        options = []
        for i, tier in enumerate(tiers):
            options.append(discord.SelectOption(
                label=tier['name'],
                value=str(i),
                description=f"Role: <@&{tier['role_id']}>"
            ))
        super().__init__(placeholder=f"Select tier to {action}", options=options[:25])
        self.tiers = tiers
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        tier = self.tiers[index]

        if self.action == "edit":
            modal = EditTierModal(self.view.guild_id, index, tier)
            await interaction.response.send_modal(modal)
        elif self.action == "remove":
            config = dm.get_guild_data(self.view.guild_id, "staff_promo_config", {})
            tiers = config.get("tiers", [])
            removed = tiers.pop(index)
            config["tiers"] = tiers
            dm.update_guild_data(self.view.guild_id, "staff_promo_config", config)
            await interaction.response.send_message(f"✅ Removed tier '{removed['name']}'!", ephemeral=True)


class TierSelectView(discord.ui.View):
    def __init__(self, select: TierSelect):
        super().__init__()
        self.add_item(select)


class EditTierModal(discord.ui.Modal):
    def __init__(self, guild_id: int, index: int, tier: dict):
        super().__init__(title=f"Edit Tier: {tier['name']}")
        self.guild_id = guild_id
        self.index = index
        self.tier = tier

        self.name_input = discord.ui.TextInput(label="Tier Name", default=tier['name'])
        self.role_id_input = discord.ui.TextInput(label="Role ID", default=str(tier['role_id']))
        self.requirements_input = discord.ui.TextInput(label="Requirements", style=discord.TextStyle.long, default=tier.get('requirements', 'None'))

        self.add_item(self.name_input)
        self.add_item(self.role_id_input)
        self.add_item(self.requirements_input)

    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        try:
            role_id_int = int(self.role_id_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid role ID.", ephemeral=True)

        tiers[self.index] = {
            "name": self.name_input.value,
            "role_id": role_id_int,
            "requirements": self.requirements_input.value
        }
        config["tiers"] = tiers
        dm.update_guild_data(self.guild_id, "staff_promo_config", config)

        await interaction.response.send_message(f"✅ Updated tier '{self.name_input.value}'!", ephemeral=True)


class StaffPromoRequirementsView(discord.ui.View):
    """Per-tier criteria editor"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        embed = discord.Embed(
            title="📋 Staff Promotion Requirements",
            description="Set criteria for each tier.",
            color=discord.Color.green()
        )

        if tiers:
            for tier in tiers:
                embed.add_field(
                    name=tier['name'],
                    value=f"Requirements: {tier.get('requirements', 'None')}\nRole: <@&{tier['role_id']}>",
                    inline=False
                )
        else:
            embed.add_field(name="No Tiers", value="Add tiers first using `!staffpromo tiers`.", inline=False)

        return embed

    @discord.ui.button(label="Set Requirements", style=discord.ButtonStyle.primary, emoji="✏️")
    async def set_requirements(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        if not tiers:
            return await interaction.response.send_message("No tiers to edit.", ephemeral=True)

        select = TierSelect(tiers, action="requirements")
        await interaction.response.send_message("Select a tier to set requirements:", view=TierSelectView(select), ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class StaffPromoStatusView(discord.ui.View):
    """Check staff promotion status"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        enabled = config.get("enabled", False)
        tiers = config.get("tiers", [])

        embed = discord.Embed(
            title="📊 Staff Promotion Status",
            color=discord.Color.blue() if enabled else discord.Color.red()
        )

        embed.add_field(name="System Status", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Total Tiers", value=str(len(tiers)), inline=True)
        embed.add_field(name="Active Promotions", value="Check individual user progress", inline=False)

        return embed


class StaffPromoLeaderboardView(discord.ui.View):
    """Staff leaderboard"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        # Placeholder for leaderboard logic
        embed = discord.Embed(
            title="🏆 Staff Leaderboard",
            description="Top performing staff members.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Coming Soon", value="Leaderboard feature under development.", inline=False)
        return embed


class StaffPromoProgressView(discord.ui.View):
    """Personal progress"""
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id

    def create_embed(self):
        embed = discord.Embed(
            title="📈 Your Promotion Progress",
            description="Track your journey through the staff ranks.",
            color=discord.Color.purple()
        )
        embed.add_field(name="Current Tier", value="Check with staff for details.", inline=False)
        return embed


class StaffPromoBonusesView(discord.ui.View):
    """Bonuses management"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        embed = discord.Embed(
            title="🎁 Promotion Bonuses",
            description="Manage bonus rewards for promotions.",
            color=discord.Color.yellow()
        )
        embed.add_field(name="Bonuses", value="Configure bonuses in the config panel.", inline=False)
        return embed


class StaffPromo(StaffPromotionSystem):
    """Message-based compatibility facade used by actions.py custom-command handlers."""

    async def progress(self, message):
        """!staffpromo progress — show your promotion progress."""
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        if not config.get("enabled", False):
            return await message.channel.send("❌ The staff promotion system is disabled on this server.")

        tiers = config.get("tiers", [])
        embed = discord.Embed(
            title="📈 Staff Promotion Progress",
            description=f"Your journey through the staff ranks in **{message.guild.name}**.",
            color=discord.Color.purple()
        )
        for i, tier in enumerate(tiers[:10]):
            name = tier.get("name", f"Tier {i + 1}")
            req = tier.get("requirements", {})
            embed.add_field(
                name=f"**{name}**",
                value=f"Activity: {req.get('activity', 0)}% • Tenure: {req.get('tenure_days', 0)}d",
                inline=False
            )
        embed.set_footer(text="Use !configpanel staffpromo to configure")
        await message.channel.send(embed=embed)

    async def tiers(self, message):
        """!staffpromo tiers — list all promotion tiers."""
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        tiers = config.get("tiers", [])
        if not tiers:
            return await message.channel.send("📋 No tiers configured yet.")

        embed = discord.Embed(title="🏷️ Staff Promotion Tiers", color=discord.Color.blue())
        for i, tier in enumerate(tiers, 1):
            name = tier.get("name", f"Tier {i}")
            role_id = tier.get("role_id")
            role = message.guild.get_role(int(role_id)) if role_id else None
            embed.add_field(
                name=f"{i}. {name}",
                value=f"Role: {role.mention if role else 'None'} • Min Score: {tier.get('min_score', 0)}",
                inline=False
            )
        await message.channel.send(embed=embed)

    async def config(self, message):
        """!staffpromo config — show the current configuration."""
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        embed = discord.Embed(title="⚙️ Staff Promotion Config", color=discord.Color.green())
        embed.add_field(name="Enabled", value="✅ Yes" if config.get("enabled", False) else "❌ No", inline=True)
        embed.add_field(name="Tiers", value=len(config.get("tiers", [])), inline=True)
        embed.add_field(name="Review Cycle (days)", value=config.get("review_cycle_days", 30), inline=True)
        await message.channel.send(embed=embed)

    async def promote(self, message, args=None):
        """!staffpromo promote @user <tier> — manually promote a member."""
        if not message.author.guild_permissions.administrator:
            return await message.channel.send("❌ Only administrators can promote members.")

        if not message.mentions:
            return await message.channel.send("❌ Usage: `!staffpromo promote @user <tier name>`")

        target = message.mentions[0]
        tier_name = " ".join((args or message.content.split())[3:]).strip('"')
        if not tier_name:
            return await message.channel.send("❌ Usage: `!staffpromo promote @user <tier name>`")

        config = self.get_config(message.guild.id)
        tiers = config.get("tiers", [])
        target_tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
        if not target_tier:
            return await message.channel.send(f"❌ Tier '{tier_name}' not found.")

        try:
            await self.manual_promote(message.guild, target, target_tier.get("name"), config)
            await message.channel.send(f"✅ **{target.display_name}** promoted to **{target_tier.get('name')}**!")
        except Exception as e:
            logger.error(f"Error in manual promotion: {e}")
            await message.channel.send("❌ Failed to promote member. Please try again.")



# ======================================================================

# From: modules/promotion_service.py
# ======================================================================

import asyncio
from datetime import datetime
import discord
from typing import Optional, List, Dict, Any
from data_manager import dm
from logger import logger


class PromotionService:
    """Service class encapsulating staff promotion logic"""
    
    def __init__(self):
        self._default_metrics = {
            "xp": {"weight": 0.15, "max": 5000, "enabled": True},
            "tenure_days": {"weight": 0.12, "max": 90, "enabled": True},
            "messages": {"weight": 0.12, "max": 1000, "enabled": True},
            "tickets_resolved": {"weight": 0.15, "max": 50, "enabled": True},
            "voice_minutes": {"weight": 0.10, "max": 3600, "enabled": True},
            "rep_received": {"weight": 0.08, "max": 100, "enabled": True},
            "rep_given": {"weight": 0.06, "max": 100, "enabled": True},
            "gamification_score": {"weight": 0.10, "max": 100, "enabled": True},
            "level": {"weight": 0.02, "max": 50, "enabled": True},
            "events_hosted": {"weight": 0.10, "max": 10, "enabled": True},
            "peer_votes": {"weight": 0.05, "max": 20, "enabled": True}
        }
        
        self._default_tier_requirements = {}
        
        self._last_promotion_time = {}
        self._last_demotion_time = {}
        self._last_notification_time = {}

    def _calculate_gamification_score(self, guild_id: int, user_id: int) -> int:
        """Calculate a gamification score based on quests and skills."""
        try:
            # Quest completion bonus
            udata = dm.get_guild_data(guild_id, f"user_{user_id}", {})
            quests = udata.get("quests_completed", 0)
            
            # Skill points
            skills = dm.get_guild_data(guild_id, f"skills_{user_id}", {})
            skill_score = sum(s.get("level", 1) for s in skills.values()) if isinstance(skills, dict) else 0
            
            total_score = (quests * 10) + (skill_score * 5)
            return min(100, total_score)
        except Exception as e:
            logger.error(f"Error calculating gamification score: {e}")
            return 0

    def _get_user_level(self, guild_id: int, user_id: int) -> int:
        """Get user level from leveling system or calculate from XP"""
        try:
            # Try to get from leveling system first
            level_data = dm.get_guild_data(guild_id, f"level_{user_id}", {})
            if level_data:
                return level_data.get("level", 1)
            
            # Fallback: calculate from XP
            user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
            xp = user_data.get("xp", 0)
            # Simple level calculation: every 1000 XP = 1 level
            level = max(1, xp // 1000)
            return min(50, level)  # Cap at 50
        except Exception as e:
            logger.error(f"Error getting user level: {e}")
            return 1

    def _compute_score(self, guild_id: int, user_id: int, member: discord.Member, metrics: dict, config: Optional[dict] = None) -> float:
        """Compute promotion score based on various metrics"""
        now = discord.utils.utcnow()
        joined = member.joined_at or now
        tenure_days = (now - joined).days
        
        udata = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        values = {
            "xp": udata.get("xp", 0),
            "tenure_days": tenure_days,
            "messages": udata.get("on_duty_messages", udata.get("total_messages", 0)),
            "tickets_resolved": dm.get_guild_data(guild_id, f"tickets_resolved_{user_id}", 0),
            "voice_minutes": udata.get("voice_minutes", 0),
            "rep_received": udata.get("rep_received", 0),
            "rep_given": udata.get("rep_given", 0),
            "gamification_score": self._calculate_gamification_score(guild_id, user_id),
            "level": self._get_user_level(guild_id, user_id),
            "events_hosted": dm.get_guild_data(guild_id, f"events_hosted_{user_id}", 0),
            "peer_votes": len(dm.get_guild_data(guild_id, f"peer_votes_{user_id}", []))
        }
        
        score = 0.0
        for metric_name, m_config in metrics.items():
            if not m_config.get("enabled", True):
                continue
            weight = m_config.get("weight", 0)
            max_val = m_config.get("max", 100)
            raw_val = values.get(metric_name, 0)
            normalized = max(0, min(1, raw_val / max_val)) if max_val > 0 else 0
            score += normalized * weight
        
        return min(1.0, score)

    async def _submit_promotion_review(self, guild: discord.Guild, member: discord.Member, target_tier, score: float, config: dict):
        """Submit a promotion for review"""
        pending = config.get("pending_reviews", [])
        tier_name = target_tier.get("name")
        
        for review in pending:
            if review.get("user_id") == member.id and review.get("tier_name") == tier_name:
                return
        
        review_data = {
            "user_id": member.id,
            "user_name": str(member),
            "tier_name": tier_name,
            "score": score,
            "timestamp": discord.utils.utcnow().isoformat(),
        }
        
        pending.append(review_data)
        config["pending_reviews"] = pending
        dm.update_guild_data(guild.id, "staff_promo_config", config)
        
        review_ch_id = config.get("settings", {}).get("review_channel")
        if review_ch_id:
            channel = guild.get_channel(int(review_ch_id))
            if channel:
                embed = discord.Embed(
                    title="📋 Promotion Review Request",
                    description=f"{member.mention} is eligible for promotion to **{tier_name}**",
                    color=discord.Color.yellow()
                )
                embed.add_field(name="Score", value=f"{score*100:.1f}%", inline=True)
                embed.add_field(name="Member", value=member.mention, inline=True)
                embed.set_footer(text=f"User ID: {member.id}")

                # merged: modules.staff_promo is defined in this file
                view = PromotionReviewView(guild_id=guild.id, user_id=member.id, tier_name=tier_name)
                msg = await channel.send(embed=embed, view=view)

                # Store review data for persistence
                dm.save_json(f"promo_review_{msg.id}", {
                    "upvotes": [],
                    "downvotes": [],
                    "user_id": member.id,
                    "tier_name": tier_name,
                    "executed": False
                })
        
        try:
            await member.send(f"📋 Your promotion to **{tier_name}** is pending review.")
        except:
            pass

    def _check_tenure(self, member: discord.Member, settings: dict) -> bool:
        """Check if member meets minimum tenure requirements"""
        min_hours = settings.get("min_tenure_hours", 72)
        if not member.joined_at:
            return False
        tenure_hours = (discord.utils.utcnow() - member.joined_at).total_seconds() / 3600
        return tenure_hours >= min_hours

    def _check_tier_requirements(self, guild_id: int, member: discord.Member, tier_name: str, config: dict) -> bool:
        """Check if member meets specific tier requirements"""
        requirements = config.get("tier_requirements", self._default_tier_requirements)
        tier_reqs = requirements.get(tier_name, {})
        
        # 0 warnings requirement
        warnings = dm.get_guild_data(guild_id, f"user_warnings_{member.id}", [])
        active_warnings = [w for w in warnings if w.get("active") and not w.get("pardoned")]
        if len(active_warnings) > 0:
            logger.info(f"StaffPromo[{guild_id}] {member} ineligible for {tier_name} due to active warnings.")
            return False

        # Probation check
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        if udata.get("on_probation"):
            probation_end = udata.get("probation_end_timestamp", 0)
            if time.time() < probation_end:
                logger.info(f"StaffPromo[{guild_id}] {member} ineligible for {tier_name} - on probation.")
                return False

        if not tier_reqs:
            return True
        
        joined_at = member.joined_at or discord.utils.utcnow()
        tenure_days = (discord.utils.utcnow() - joined_at).days
        
        missing = []
        for req_type, req_value in tier_reqs.items():
            if req_type == "messages":
                if udata.get("total_messages", 0) < req_value:
                    missing.append(f"messages: {udata.get('total_messages', 0)}/{req_value}")
            elif req_type == "tenure_days":
                if tenure_days < req_value:
                    missing.append(f"tenure: {tenure_days}/{req_value} days")
            elif req_type == "xp":
                if udata.get("xp", 0) < req_value:
                    missing.append(f"XP: {udata.get('xp', 0)}/{req_value}")
            elif req_type == "events":
                hosted = dm.get_guild_data(guild_id, f"events_hosted_{member.id}", 0)
                if hosted < req_value:
                    missing.append(f"events: {hosted}/{req_value}")
            elif req_type == "votes":
                votes = len(dm.get_guild_data(guild_id, f"peer_votes_{member.id}", []))
                if votes < req_value:
                    missing.append(f"votes: {votes}/{req_value}")
        
        if missing:
            logger.info(f"StaffPromo[{guild_id}] {member} missing requirements for {tier_name}: {', '.join(missing)}")
            return False
        
        return True

    async def _evaluate_trial_performance(self, guild_id: int, member: discord.Member, trial_settings: dict, config: dict) -> str:
        """Evaluate trial performance based on metrics"""
        metrics = trial_settings.get("evaluation_metrics", {
            "activity_score_min": 0.3,
            "ticket_resolution_min": 5,
            "voice_hours_min": 10
        })
        
        # Get user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        
        # Check activity score (from promotion system)
        current_score = self._compute_score(guild_id, member.id, member, config.get("metrics", self._default_metrics))
        activity_score_min = metrics.get("activity_score_min", 0.3)
        
        # Check ticket resolutions (would need ticket system integration)
        ticket_resolutions = udata.get("ticket_resolutions", 0)
        ticket_resolution_min = metrics.get("ticket_resolution_min", 5)
        
        # Check voice hours
        voice_hours = udata.get("voice_minutes", 0) / 60  # Convert to hours
        voice_hours_min = metrics.get("voice_hours_min", 10)
        
        # Evaluate criteria
        score_pass = current_score >= activity_score_min
        ticket_pass = ticket_resolutions >= ticket_resolution_min
        voice_pass = voice_hours >= voice_hours_min
        
        # Require at least 2 out of 3 criteria to pass
        passes = sum([score_pass, ticket_pass, voice_pass])
        
        if passes >= 2:
            return "pass"
        else:
            return "fail"



# ======================================================================
# From: modules/applications.py
# ======================================================================

import discord
from discord import ui, Interaction, TextStyle, Embed, ButtonStyle
import datetime
import time
import json
from typing import List, Dict, Optional, Any
from data_manager import dm
from logger import logger

class ApplicationSystem:
    """
    Complete staff application system with submission, review, and management.
    Features:
    - Application submission via modal
    - Staff review and approval process
    - Application status tracking
    - Automatic DM notifications
    """

    def __init__(self, bot):
        self.bot = bot

    def get_persistent_views(self):
        return [ApplicationPersistentView(), ApplicationReviewView()]

    async def create_application(self, interaction):
        """Slash-command adapter: open the application modal."""
        config = dm.get_guild_data(interaction.guild.id, "application_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Applications are currently disabled.", ephemeral=True)
        questions = config.get("questions") or [
            "Why do you want to join the staff team?",
            "What experience do you have?",
            "How active are you on this server?",
            "What would you improve?",
            "Anything else we should know?",
        ]
        await interaction.response.send_modal(ApplicationModal(questions))

    async def handle_application_submit(self, interaction, application_data):
        """Handle application submission."""
        config = dm.get_guild_data(interaction.guild.id, "application_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Applications are currently disabled.", ephemeral=True)

        # Store application
        applications = dm.get_guild_data(interaction.guild.id, "applications", [])
        applications.append({
            "id": int(time.time()),
            "user_id": interaction.user.id,
            "data": application_data,
            "status": "pending",
            "submitted_at": time.time()
        })
        dm.update_guild_data(interaction.guild.id, "applications", applications)

        await interaction.response.send_message("✅ Application submitted! You'll be notified of the decision.", ephemeral=True)

    async def review_application(self, interaction, application_id, action, reason=None):
        """Review an application (approve/deny)."""
        applications = dm.get_guild_data(interaction.guild.id, "applications", [])
        application = next((a for a in applications if a["id"] == application_id), None)

        if not application:
            return await interaction.response.send_message("❌ Application not found.", ephemeral=True)

        # Check permissions
        config = dm.get_guild_data(interaction.guild.id, "application_config", {})
        is_staff = (interaction.user.guild_permissions.administrator or
                   any(role.id == int(rid) for rid in config.get("staff_roles", []) for role in interaction.user.roles))

        if not is_staff:
            return await interaction.response.send_message("❌ Only staff can review applications.", ephemeral=True)

        # Update status
        application["status"] = "approved" if action == "approve" else "denied"
        application["reviewed_by"] = interaction.user.id
        application["reviewed_at"] = time.time()
        if reason:
            application["review_reason"] = reason

        # Update in storage
        for i, a in enumerate(applications):
            if a["id"] == application_id:
                applications[i] = application
                break
        dm.update_guild_data(interaction.guild.id, "applications", applications)

        # Notify applicant
        try:
            user = self.bot.get_user(application["user_id"])
            if user:
                embed = discord.Embed(
                    title="📋 Application Update",
                    description=f"Your application has been **{application['status']}**.",
                    color=discord.Color.green() if action == "approve" else discord.Color.red()
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                await user.send(embed=embed)
        except:
            pass

        await interaction.response.send_message(f"✅ Application {action}d!", ephemeral=True)

class ApplicationPersistentView(ui.View):
    """Persistent view for the public 'Apply Now' button."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Apply Now", style=ButtonStyle.primary, custom_id="app_apply_now")
    async def apply_now(self, interaction: Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "application_config", {})

        # Check if applications are closed
        if not config.get("applications_open", True):
            return await interaction.response.send_message("❌ Applications are currently closed.", ephemeral=True)

        # Enforce cooldown
        cooldown_days = config.get("cooldown_days", 30)
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(str(interaction.user.id), [])

        if user_apps:
            last_app = user_apps[-1]
            last_ts = last_app.get("timestamp", 0)
            elapsed = (time.time() - last_ts) / (24 * 3600)
            if elapsed < cooldown_days:
                remaining = cooldown_days - elapsed
                return await interaction.response.send_message(f"❌ You've applied recently. You can reapply in {remaining:.1f} days.", ephemeral=True)

        app_types = config.get("application_types", [])
        if app_types:
            view = ui.View(timeout=60)
            select = ui.Select(placeholder="Select Application Type", options=[
                discord.SelectOption(label=t, value=t) for t in app_types
            ])

            async def select_callback(it: Interaction):
                app_type = select.values[0]
                questions = config.get("questions", ["Why do you want to join?", "What experience do you have?"])
                await it.response.send_modal(ApplicationModal(questions, app_type))

            select.callback = select_callback
            view.add_item(select)
            return await interaction.response.send_message("Please select the type of application you'd like to submit:", view=view, ephemeral=True)

        questions = config.get("questions", ["Why do you want to join?", "What experience do you have?"])
        modal = ApplicationModal(questions)
        await interaction.response.send_modal(modal)

class ApplicationModal(ui.Modal):
    def __init__(self, questions: List[str], app_type: str = "General"):
        super().__init__(title=f"{app_type} Application")
        self.questions = questions
        self.app_type = app_type
        self.inputs = []
        for q in questions[:5]:
            i = ui.TextInput(label=q, style=TextStyle.paragraph, required=True, max_length=1000)
            self.add_item(i)
            self.inputs.append(i)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "application_config", {})

        # Save application
        answers = [i.value for i in self.inputs]
        app_data = {
            "id": f"{interaction.user.id}_{int(time.time())}",
            "user_id": interaction.user.id,
            "timestamp": time.time(),
            "status": "pending",
            "answers": answers,
            "questions": self.questions[:5],
            "type": self.app_type
        }

        apps = dm.get_guild_data(guild_id, "applications", {})
        if str(interaction.user.id) not in apps:
            apps[str(interaction.user.id)] = []
        apps[str(interaction.user.id)].append(app_data)
        dm.update_guild_data(guild_id, "applications", apps)

        # Send to log channel
        log_ch_id = config.get("log_channel_id")
        log_ch = interaction.guild.get_channel(log_ch_id)

        if log_ch:
            embed = Embed(title=f"📋 New {self.app_type} Application Received", color=discord.Color.blue())
            embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)

            account_age = (datetime.datetime.now(datetime.timezone.utc) - interaction.user.created_at).days
            join_date = interaction.user.joined_at.strftime("%Y-%m-%d") if interaction.user.joined_at else "Unknown"

            embed.add_field(name="User Info", value=f"Account Age: {account_age} days\nJoined: {join_date}\nSubmitted: <t:{int(time.time())}:R>")

            for q, a in zip(app_data["questions"], app_data["answers"]):
                embed.add_field(name=q, value=a[:1024], inline=False)

            embed.set_footer(text=f"App ID: {app_data['id']}")

            view = ApplicationReviewView()
            await log_ch.send(embed=embed, view=view)

            # Ping role if enabled
            if config.get("auto_ping_enabled") and config.get("staff_role_id"):
                await log_ch.send(f"<@&{config['staff_role_id']}> New application submitted!", delete_after=5)

        # DM applicant
        if config.get("applicant_dms_enabled", True):
            try:
                await interaction.user.send("Your application was received and is under review.")
            except:
                pass

        await interaction.response.send_message("✅ Your application has been submitted!", ephemeral=True)

class ApplicationReviewView(ui.View):
    """Staff-only review actions."""
    def __init__(self):
        super().__init__(timeout=None)

    def _get_app_info(self, embed: Embed):
        footer = embed.footer.text
        if footer and "App ID: " in footer:
            return footer.replace("App ID: ", "").split("_")
        return None, None

    async def _update_app_status(self, interaction: Interaction, status: str, reason: str = None):
        user_id_str, ts_str = self._get_app_info(interaction.message.embeds[0])
        if not user_id_str:
            return await interaction.response.send_message("❌ Could not find application data.", ephemeral=True)

        guild_id = interaction.guild_id
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(user_id_str, [])

        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == ts_str:
                app["status"] = status
                if reason: app["deny_reason"] = reason
                target_app = app
                break

        if target_app:
            dm.update_guild_data(guild_id, "applications", apps)
            # Log action
            log_entry = {
                "action": f"application_{status}",
                "user_id": int(user_id_str),
                "moderator_id": interaction.user.id,
                "timestamp": time.time(),
                "app_id": target_app["id"],
                "reason": reason
            }
            logs = dm.get_guild_data(guild_id, "action_logs", [])
            logs.append(log_entry)
            dm.update_guild_data(guild_id, "action_logs", logs[-100:])
            return target_app
        return None

    @ui.button(label="Accept", style=ButtonStyle.success, emoji="✅", custom_id="app_review_accept")
    async def accept(self, interaction: Interaction, button: ui.Button):
        app = await self._update_app_status(interaction, "accepted")
        if not app: return

        config = dm.get_guild_data(interaction.guild_id, "application_config", {})
        role_id = config.get("role_to_give_on_accept")
        role = interaction.guild.get_role(role_id)

        role_error = None
        applicant = interaction.guild.get_member(app["user_id"])
        if role and applicant:
            try:
                await applicant.add_roles(role)
            except:
                role_error = f"⚠️ Failed to add role <@&{role_id}> to {applicant.mention}."

        if config.get("applicant_dms_enabled", True) and applicant:
            msg = config.get("acceptance_dm", "Congratulations {user}! Your application was accepted for {role}.").format(
                user=applicant.name, role=role.name if role else "the role"
            )
            try: await applicant.send(msg)
            except: pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Decision", value=f"Accepted by {interaction.user.mention} at <t:{int(time.time())}:f>")
        await interaction.message.edit(embed=embed, view=None)

        resp_msg = f"✅ Application accepted for <@{app['user_id']}>."
        if role_error: resp_msg += f"\n{role_error}"
        await interaction.response.send_message(resp_msg, ephemeral=True)

    @ui.button(label="Deny", style=ButtonStyle.danger, emoji="❌", custom_id="app_review_deny")
    async def deny(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_app_info(interaction.message.embeds[0])
        await interaction.response.send_modal(DenyModal(user_id_str, ts_str))

    @ui.button(label="View Profile", style=ButtonStyle.secondary, emoji="🔍", custom_id="app_review_profile")
    async def view_profile(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_app_info(interaction.message.embeds[0])
        member = interaction.guild.get_member(int(user_id_str)) or await interaction.guild.fetch_member(int(user_id_str))

        if not member:
            return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

        embed = Embed(title=f"User Profile: {member}", color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Joined Discord", value=f"<t:{int(member.created_at.timestamp())}:R>")
        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>")

        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:20]) or "None", inline=False)

        # Add server history if available (e.g. from moderation logs)
        mod_logs = dm.get_guild_data(interaction.guild_id, "mod_logs", [])
        user_logs = [l for l in mod_logs if l.get("user_id") == member.id]
        embed.add_field(name="Mod History", value=f"Total Infractions: {len(user_logs)}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Put on Hold", style=ButtonStyle.secondary, emoji="🕐", custom_id="app_review_hold")
    async def hold(self, interaction: Interaction, button: ui.Button):
        app = await self._update_app_status(interaction, "on_hold")
        if not app: return

        config = dm.get_guild_data(interaction.guild_id, "application_config", {})
        applicant = interaction.guild.get_member(app["user_id"])

        if config.get("applicant_dms_enabled", True) and applicant:
            try: await applicant.send("Your application for the server is currently on hold/under further review.")
            except: pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.orange()
        embed.add_field(name="Status Update", value=f"Put on Hold by {interaction.user.mention}")
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("🕐 Application put on hold.", ephemeral=True)

    @ui.button(label="Previous Apps", style=ButtonStyle.secondary, emoji="📋", custom_id="app_review_prev")
    async def view_previous(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_app_info(interaction.message.embeds[0])
        apps = dm.get_guild_data(interaction.guild_id, "applications", {})
        user_apps = apps.get(user_id_str, [])

        if not user_apps:
            return await interaction.response.send_message("No previous applications found.", ephemeral=True)

        desc = ""
        for app in user_apps:
            status_emoji = {"accepted": "✅", "denied": "❌", "pending": "⏳", "on_hold": "🕐"}.get(app["status"], "❓")
            desc += f"{status_emoji} **{app['status'].title()}** - <t:{int(app['timestamp'])}:R> (ID: `{app['id']}`)\n"

        embed = Embed(title=f"Previous Applications: {user_id_str}", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Request Info", style=ButtonStyle.secondary, emoji="💬", custom_id="app_review_info")
    async def request_info(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_app_info(interaction.message.embeds[0])
        await interaction.response.send_modal(RequestInfoModal(user_id_str, ts_str))

class DenyModal(ui.Modal):
    def __init__(self, user_id_str, ts_str):
        super().__init__(title="Deny Application")
        self.user_id_str = user_id_str
        self.ts_str = ts_str
        self.reason = ui.TextInput(label="Reason for Denial", style=TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(self.user_id_str, [])

        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                app["status"] = "denied"
                app["deny_reason"] = self.reason.value
                target_app = app
                break

        if target_app:
            dm.update_guild_data(guild_id, "applications", apps)

            config = dm.get_guild_data(guild_id, "application_config", {})
            applicant = interaction.guild.get_member(target_app["user_id"])

            if config.get("applicant_dms_enabled", True) and applicant:
                msg = config.get("denial_dm", "Sorry {user}, your application was denied. Reason: {reason}").format(
                    user=applicant.name, reason=self.reason.value
                )
                try: await applicant.send(msg)
                except: pass

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.add_field(name="Decision", value=f"Denied by {interaction.user.mention} at <t:{int(time.time())}:f>\n**Reason:** {self.reason.value}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"❌ Application denied for <@{target_app['user_id']}>.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find application data.", ephemeral=True)

class RequestInfoModal(ui.Modal):
    def __init__(self, user_id_str, ts_str):
        super().__init__(title="Request More Info")
        self.user_id_str = user_id_str
        self.ts_str = ts_str
        self.question = ui.TextInput(label="Question to ask the applicant", style=TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.question)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(self.user_id_str, [])

        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                if "notes" not in app: app["notes"] = []
                app["notes"].append(f"Info requested: {self.question.value}")
                target_app = app
                break

        if target_app:
            dm.update_guild_data(guild_id, "applications", apps)
            applicant = interaction.guild.get_member(target_app["user_id"])

            if applicant:
                try: await applicant.send(f"Staff have requested more information regarding your application:\n\n> {self.question.value}")
                except: pass

            embed = interaction.message.embeds[0]
            embed.add_field(name="Information Requested", value=f"By {interaction.user.mention}: {self.question.value}", inline=False)
            await interaction.message.edit(embed=embed)
            
            # Log action
            log_entry = {
                "action": "application_info_requested",
                "user_id": int(self.user_id_str),
                "moderator_id": interaction.user.id,
                "timestamp": time.time(),
                "app_id": target_app["id"],
                "question": self.question.value
            }
            logs = dm.get_guild_data(guild_id, "action_logs", [])
            logs.append(log_entry)
            dm.update_guild_data(guild_id, "action_logs", logs[-100:])
            
            await interaction.response.send_message("✅ Information requested from applicant.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find application data.", ephemeral=True)



# ======================================================================
# From: modules/staff_system.py
# ======================================================================

import discord
from discord import ui
import datetime
import json
from data_manager import dm
from typing import Dict, Optional
from logger import logger

class StaffApplicationPersistentView(ui.View):
    """Persistent view for the 'Apply Now' button."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(label="Apply Now", style=discord.ButtonStyle.success, custom_id="staff_apply_now")
    async def apply_now(self, interaction: discord.Interaction, button: ui.Button):
        # 30-day cooldown check
        apps = dm.load_json("applications", default={})
        last_app = apps.get(str(interaction.user.id))
        if last_app and (datetime.datetime.now() - datetime.datetime.fromisoformat(last_app['timestamp'])).days < 30:
            return await interaction.response.send_message("[ERROR] You can only apply once every 30 days.", ephemeral=True)
        
        await interaction.response.send_modal(StaffApplicationModal(self.bot))

class StaffReviewPersistentView(ui.View):
    """Persistent view for staff logs (Approve/Deny)."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="staff_approve_app")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        embed = interaction.message.embeds[0]
        try:
            applicant_id = int(embed.fields[0].value)
        except (IndexError, ValueError, AttributeError) as e:
            logger.error("Failed to parse applicant ID: %s", e)
            return await interaction.followup.send("[ERROR] Could not identify applicant.", ephemeral=True)

        guild = interaction.guild
        applicant = guild.get_member(applicant_id)
        if applicant:
            try:
                await applicant.send(f"[AI] Your staff application for {guild.name} has been approved!")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.debug("Could not DM applicant: %s", e)
        
        await interaction.message.edit(content=f"[SUCCESS] Approved by {interaction.user.name}", view=None)
        
        # Store in applications.json
        apps = dm.load_json("applications", default={})
        apps[str(applicant_id)] = {"status": "approved", "timestamp": str(datetime.datetime.now())}
        dm.save_json("applications", apps)

    @ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="staff_deny_app")
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        embed = interaction.message.embeds[0]
        try:
            applicant_id = int(embed.fields[0].value)
        except (IndexError, ValueError, AttributeError) as e:
            logger.error("Failed to parse applicant ID: %s", e)
            return await interaction.followup.send("[ERROR] Could not identify applicant.", ephemeral=True)

        guild = interaction.guild
        applicant = guild.get_member(applicant_id)
        if applicant:
            try:
                await applicant.send(f"[AI] Your staff application for {guild.name} has been denied.")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.debug("Could not DM applicant: %s", e)
        
        await interaction.message.edit(content=f"[ERROR] Denied by {interaction.user.name}", view=None)
        
        apps = dm.load_json("applications", default={})
        apps[str(applicant_id)] = {"status": "denied", "timestamp": str(datetime.datetime.now())}
        dm.save_json("applications", apps)

class StaffApplicationModal(ui.Modal):
    def __init__(self, bot):
        super().__init__(title='Staff Application')
        self.bot = bot
        
    q1 = ui.TextInput(label='Why do you want to be staff?', style=discord.TextStyle.paragraph, min_length=20, max_length=1000)
    q2 = ui.TextInput(label='What experience do you have?', style=discord.TextStyle.paragraph, min_length=10, max_length=1000)
    q3 = ui.TextInput(label='Weekly Activity (hours)?', placeholder='e.g. 15-20 hours', min_length=1, max_length=50)
    q4 = ui.TextInput(label='What skills do you bring?', style=discord.TextStyle.paragraph, min_length=10, max_length=1000)
    q5 = ui.TextInput(label='Anything else?', required=False, style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        log_channel_id = dm.get_guild_data(guild_id, "staff_log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id)

        if not log_channel:
            return await interaction.followup.send("[ERROR] Staff logs channel not found. Please contact an admin.", ephemeral=True)

        embed = discord.Embed(title=f"New Staff Application: {interaction.user.name}", color=discord.Color.gold())
        embed.add_field(name="User ID", value=interaction.user.id)
        embed.add_field(name="1. Why?", value=self.q1.value, inline=False)
        embed.add_field(name="2. Experience", value=self.q2.value, inline=False)
        embed.add_field(name="3. Activity", value=self.q3.value, inline=False)
        embed.add_field(name="4. Skills", value=self.q4.value, inline=False)
        embed.add_field(name="5. Extra", value=self.q5.value or "N/A", inline=False)

        # Use the Persistent Review View
        view = StaffReviewPersistentView()
        await log_channel.send(embed=embed, view=view)
        await interaction.followup.send("[SUCCESS] Staff application submitted! We will DM you shortly.", ephemeral=True)

class StaffSystem:
    def __init__(self, bot):
        self.bot = bot

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # 1. Create Channels
        public_channel = await guild.create_text_channel("apply-staff")
        log_channel = await guild.create_text_channel("apply-staff-logs", overwrites={
            guild.default_role: discord.PermissionOverwrite(read_messages=False)
        })

        dm.update_guild_data(guild.id, "staff_log_channel_id", log_channel.id)

        # 2. Public Panel with Persistent View
        embed = discord.Embed(title="Join Our Staff Team", description="Click below to apply for a staff position!", color=discord.Color.green())
        view = StaffApplicationPersistentView(self.bot)
        await public_channel.send(embed=embed, view=view)

        # 3. Auto-Documentation
        help_embed = discord.Embed(title="Apply Staff System Help", description="Information about staff application system.", color=discord.Color.blue())
        help_embed.add_field(name="!apply status", value="Checks the status of your application.")
        help_embed.add_field(name="!help staffapply", value="Shows this help embed.")
        await public_channel.send(embed=help_embed)
        
        # 4. Register Custom Commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["apply"] = json.dumps({"command_type": "application_status"})
        custom_cmds["help staffapply"] = json.dumps({
            "command_type": "help_embed",
            "title": "Apply Staff System Help",
            "description": "Information about staff application system.",
            "fields": [
                {"name": "!apply status", "value": "Checks the status of your application.", "inline": False},
                {"name": "!help staffapply", "value": "Shows this help embed.", "inline": False}
            ]
        })
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        return True



# ======================================================================
# From: modules/staff_extras.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


class StaffExtras:
    def __init__(self, bot):
        self.bot = bot
        self._reviews: Dict[int, Dict[int, dict]] = {}  # guild_id -> {user_id: review_data}
        self._training_tasks: Dict[int, Dict[str, dict]] = {}  # guild_id -> {task_name: task_data}
        self._promotion_history: Dict[int, List[dict]] = {}  # guild_id -> [promotion_records]
        self._appeals: Dict[int, Dict[int, dict]] = {}  # guild_id -> {user_id: appeal_data}
        self._exit_interviews: Dict[int, List[dict]] = {}  # guild_id -> [exit_data]
        self._load_data()

    def _load_data(self):
        """Preload all guild data for staff extras on startup"""
        # Get all guild IDs from data manager (assuming dm has a method to list guilds)
        # If not, load per-guild on demand via _get_guild_data
        import os
        data_dir = dm.data_dir if hasattr(dm, "data_dir") else "./data"
        if not os.path.exists(data_dir):
            return
        for guild_folder in os.listdir(data_dir):
            if not guild_folder.isdigit():
                continue
            guild_id = int(guild_folder)
            self._get_guild_data(guild_id)

    def _save_guild_data(self, guild_id: int):
        """Save all staff extras data for a specific guild."""
        dm.update_guild_data(guild_id, "staff_reviews", self._reviews.get(guild_id, {}))
        dm.update_guild_data(guild_id, "training_tasks", self._training_tasks.get(guild_id, {}))
        dm.update_guild_data(guild_id, "promotion_history", self._promotion_history.get(guild_id, []))
        dm.update_guild_data(guild_id, "staff_appeals", self._appeals.get(guild_id, {}))
        dm.update_guild_data(guild_id, "exit_interviews", self._exit_interviews.get(guild_id, []))

    def _get_guild_data(self, guild_id: int):
        """Ensure guild data is loaded into memory."""
        if guild_id not in self._reviews:
            self._reviews[guild_id] = dm.get_guild_data(guild_id, "staff_reviews", {})
            self._training_tasks[guild_id] = dm.get_guild_data(guild_id, "training_tasks", {})
            self._promotion_history[guild_id] = dm.get_guild_data(guild_id, "promotion_history", [])
            self._appeals[guild_id] = dm.get_guild_data(guild_id, "staff_appeals", {})
            self._exit_interviews[guild_id] = dm.get_guild_data(guild_id, "exit_interviews", [])

    async def on_member_remove(self, member):
        """Handle exit interviews when staff leave"""
        guild = member.guild
        guild_id = guild.id
        
        config = dm.get_guild_data(guild_id, "staff_promo_config", {})
        is_staff = config.get("is_staff", False)
        
        if not is_staff:
            return
        
        staff_roles = config.get("staff_roles", [])
        member_roles = [r.id for r in member.roles]
        
        if any(r in member_roles for r in staff_roles):
            await self._do_exit_interview(member, guild)

    async def _do_exit_interview(self, member, guild):
        """Send exit interview DM"""
        guild_id = guild.id
        
        embed = discord.Embed(
            title="👋 Goodbye from Staff",
            description=f"You've left **{guild.name}** where you were on the staff team.",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Feedback",
            value="We'd love to hear your thoughts! What prompted your departure?",
            inline=False
        )
        embed.add_field(
            name="Options",
            value="React with:\n🟢 Happy to help\n🟡 Neutral\n🔴 Unhappy",
            inline=False
        )
        
        try:
            msg = await member.send(embed=embed)
            await msg.add_reaction("🟢")
            await msg.add_reaction("🟡")
            await msg.add_reaction("🔴")
            
            self._get_guild_data(guild_id)
            self._exit_interviews[guild_id].append({
                "user_id": member.id,
                "username": str(member),
                "timestamp": time.time(),
                "guild_id": guild_id,
                "reaction": None
            })
            self._save_guild_data(guild_id)
        except:
            pass

    async def on_reaction_add(self, reaction, user):
        """Handle exit interview responses"""
        if user.bot:
            return
        
        if not reaction.message.author.bot:
            return
        
        if "Goodbye from Staff" not in str(reaction.message.embeds):
            return
        
        guild = reaction.message.guild
        if not guild:
            return
        
        guild_id = guild.id
        emoji = str(reaction.emoji)
        
        for entry in self._exit_interviews.get(guild_id, []):
            if entry.get("user_id") == user.id and entry.get("reaction") is None:
                entry["reaction"] = emoji
                self._save_data()
                break

    async def get_staff_leaderboard(self, guild_id: int, limit: int = 10) -> List[dict]:
        """Get staff ranked by performance"""
        self._get_guild_data(guild_id)
        config = dm.get_guild_data(guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        
        staff_members = []
        guild = self.bot.get_guild(guild_id)
        
        staff_role_ids = config.get("staff_roles", [])
        
        for role_id in staff_role_ids:
            role = guild.get_role(role_id)
            if role:
                for member in role.members:
                    if not member.bot:
                        score = await self._calculate_member_score(guild_id, member)
                        staff_members.append({
                            "member": member,
                            "score": score,
                            "tier": self._get_tier_for_score(score, tiers)
                        })
        
        staff_members.sort(key=lambda x: x["score"], reverse=True)
        return staff_members[:limit]

    async def _calculate_member_score(self, guild_id: int, member) -> float:
        """Calculate member's promotion score"""
        try:
            config = dm.get_guild_data(guild_id, "staff_promo_config", {})
            metrics = config.get("metrics", {})
            
            xp = dm.get_guild_data(guild_id, f"xp_{member.id}", 0)
            messages = dm.get_guild_data(guild_id, f"messages_{member.id}", 0)
            
            score = (
                (xp / 5000 * 0.25) +
                (messages / 1000 * 0.20)
            )
            
            return min(1.0, score)
        except:
            return 0.0

    def _get_tier_for_score(self, score: float, tiers: List[dict]) -> str:
        """Get tier name for score"""
        current_tier = "Trial Staff"
        for tier in sorted(tiers, key=lambda x: x.get("threshold", 0), reverse=True):
            if score >= tier.get("threshold", 0):
                current_tier = tier.get("name", "Staff")
                break
        return current_tier

    async def record_promotion(self, guild_id: int, member: discord.Member, 
                               old_tier: str, new_tier: str, reason: str):
        """Record a promotion in history"""
        if guild_id not in self._promotion_history:
            self._promotion_history[guild_id] = []
        
        record = {
            "user_id": member.id,
            "username": str(member),
            "old_tier": old_tier,
            "new_tier": new_tier,
            "reason": reason,
            "timestamp": time.time(),
            "guild_id": guild_id
        }
        
        self._promotion_history[guild_id].append(record)
        self._promotion_history[guild_id] = self._promotion_history[guild_id][-100:]
        self._save_data()
        
        await self._log_promotion(member.guild, record)

    async def _log_promotion(self, guild: discord.Guild, record: dict):
        """Log promotion to channel"""
        config = dm.get_guild_data(guild.id, "staff_promo_config", {})
        log_channel_id = config.get("log_channel")
        
        if not log_channel_id:
            return
        
        channel = guild.get_channel(log_channel_id)
        if not channel:
            return
        
        is_promotion = record.get("old_tier") != record.get("new_tier")
        
        if is_promotion:
            embed = discord.Embed(
                title="📢 Staff Promotion" if "promotion" in record.get("reason", "").lower() 
                     else "📉 Staff Demotion",
                color=discord.Color.green() if is_promotion else discord.Color.red()
            )
        else:
            embed = discord.Embed(
                title="📋 Staff Change",
                color=discord.Color.blue()
            )
        
        member = guild.get_member(record["user_id"])
        embed.add_field(name="Member", value=member.mention if member else record["username"], inline=True)
        embed.add_field(name="From", value=record.get("old_tier", "N/A"), inline=True)
        embed.add_field(name="To", value=record.get("new_tier", "N/A"), inline=True)
        embed.add_field(name="Reason", value=record.get("reason", "Manual"), inline=False)
        embed.timestamp = datetime.fromtimestamp(record["timestamp"])
        
        await channel.send(embed=embed)

    async def create_training_task(self, guild_id: int, name: str, description: str, 
                              required_score: float, reward_boost: float):
        """Create a training task"""
        self._get_guild_data(guild_id)
        self._training_tasks[guild_id][name] = {
            "description": description,
            "required_score": required_score,
            "reward_boost": reward_boost,
            "created_at": time.time()
        }
        self._save_guild_data(guild_id)

    async def get_training_tasks(self, guild_id: int) -> List[dict]:
        """Get available training tasks"""
        self._get_guild_data(guild_id)
        tasks = self._training_tasks.get(guild_id, {})
        return [{"name": k, **v} for k, v in tasks.items()]

    async def complete_training(self, member: discord.Member, task_name: str) -> bool:
        """Mark training as completed"""
        guild_id = member.guild.id
        
        tasks = self._training_tasks.get(guild_id, {})
        if task_name not in tasks:
            return False
        
        task = tasks[task_name]
        
        dm.update_guild_data(guild_id, f"training_{member.id}_{task_name}", {
            "completed": True,
            "completed_at": time.time()
        })
        
        return True

    async def submit_appeal(self, guild_id: int, user_id: int, message: str):
        """Submit appeal for demotion"""
        if guild_id not in self._appeals:
            self._appeals[guild_id] = {}
        
        self._appeals[guild_id][user_id] = {
            "message": message,
            "timestamp": time.time(),
            "votes": [],
            "status": "pending"
        }
        self._save_data()

    async def vote_on_appeal(self, guild_id: int, user_id: int, voter_id: int, approve: bool):
        """Vote on appeal"""
        if guild_id not in self._appeals:
            return False
        
        appeal = self._appeals[guild_id].get(user_id)
        if not appeal or appeal.get("status") != "pending":
            return False
        
        appeal["votes"].append({
            "voter_id": voter_id,
            "approve": approve,
            "timestamp": time.time()
        })
        
        approve_votes = sum(1 for v in appeal["votes"] if v.get("approve"))
        total_votes = len(appeal["votes"])
        
        if total_votes >= 3:
            if approve_votes > total_votes / 2:
                appeal["status"] = "approved"
            else:
                appeal["status"] = "rejected"
            
            self._save_data()
        
        return True

    async def get_promotion_history(self, guild_id: int, limit: int = 20) -> List[dict]:
        """Get promotion history"""
        history = self._promotion_history.get(guild_id, [])
        return history[-limit:]

    async def request_peer_review(self, guild_id: int, reviewer_id: int, 
                                target_id: int, rating: int, comment: str):
        """Submit peer review"""
        if guild_id not in self._reviews:
            self._reviews[guild_id] = {}
        
        if target_id not in self._reviews[guild_id]:
            self._reviews[guild_id][target_id] = {"reviews": []}
        
        self._reviews[guild_id][target_id]["reviews"].append({
            "reviewer_id": reviewer_id,
            "rating": rating,
            "comment": comment,
            "timestamp": time.time()
        })
        
        self._reviews[guild_id][target_id]["reviews"] = \
            self._reviews[guild_id][target_id]["reviews"][-10:]
        
        self._save_data()

    async def get_peer_review_score(self, guild_id: int, user_id: int) -> float:
        """Get average peer review score"""
        reviews = self._reviews.get(guild_id, {}).get(user_id, {}).get("reviews", [])
        
        if not reviews:
            return 0.5
        
        avg_rating = sum(r["rating"] for r in reviews) / len(reviews)
        return avg_rating / 5.0


class StaffExtrasCommands:
    def __init__(self, bot):
        self.bot = bot
        self.extras = StaffExtras(bot)

    async def handle_staff_leaderboard(self, message):
        """Handle !staffleaderboard command"""
        guild = message.guild
        leaderboard = await self.extras.get_staff_leaderboard(guild.id)
        
        if not leaderboard:
            await message.channel.send("No staff to display!")
            return
        
        embed = discord.Embed(
            title="🏆 Staff Leaderboard",
            description="Top staff by performance",
            color=discord.Color.gold()
        )
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, entry in enumerate(leaderboard[:10]):
            member = entry["member"]
            score = entry["score"]
            tier = entry["tier"]
            
            embed.add_field(
                name=f"{medals[i]} {member.display_name}",
                value=f"Score: {score:.0%} | {tier}",
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_promotion_history(self, message):
        """Handle !promotionhistory command"""
        guild = message.guild
        history = await self.extras.get_promotion_history(guild.id)
        
        if not history:
            await message.channel.send("No promotion history yet!")
            return
        
        embed = discord.Embed(
            title="📜 Promotion History",
            color=discord.Color.blue()
        )
        
        for record in history[-10:]:
            member = guild.get_member(record["user_id"])
            name = member.display_name if member else record["username"]
            
            is_promo = "promotion" in record.get("reason", "").lower()
            emoji = "📈" if is_promo else "📉"
            
            date = datetime.fromtimestamp(record["timestamp"]).strftime("%m/%d")
            embed.add_field(
                name=f"{emoji} {name}",
                value=f"{record.get('old_tier')} → {record.get('new_tier')} | {date}",
                inline=True
            )
        
        await message.channel.send(embed=embed)

    async def handle_training_tasks(self, message):
        """Handle !trainingtasks command"""
        guild = message.guild
        tasks = await self.extras.get_training_tasks(guild.id)
        
        if not tasks:
            await message.channel.send("No training tasks available. Ask an admin to create some!")
            return
        
        embed = discord.Embed(
            title="📚 Training Tasks",
            description="Complete these for promotion boost!",
            color=discord.Color.green()
        )
        
        for task in tasks:
            embed.add_field(
                name=task["name"],
                value=f"{task['description']}\nReward: +{task['reward_boost']:.0%} boost",
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_appeal(self, message, parts):
        """Handle !appeal command"""
        if len(parts) < 2:
            await message.channel.send("Usage: !appeal <reason for appeal>")
            return
        
        guild = message.guild
        reason = " ".join(parts[1:])
        
        await self.extras.submit_appeal(guild.id, message.author.id, reason)
        
        await message.channel.send("✅ Appeal submitted! Staff will vote on it.")


def staff_extras_extension_setup(bot):
    return StaffExtras(bot)
