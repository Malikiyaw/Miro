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


def setup(bot):
    return StaffShiftSystem(bot)
