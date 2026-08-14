import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


class AutoAnnouncer:
    def __init__(self, bot):
        self.bot = bot
        self._schedules: Dict[int, List[dict]] = {}
        self._reminders: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        data = dm.load_json("announcer_reminders", default={})
        self._schedules = data.get("schedules", {})
        self._reminders = data.get("reminders", {})

    def _save_data(self):
        data = {
            "schedules": self._schedules,
            "reminders": self._reminders
        }
        dm.save_json("announcer_reminders", data)

    def start_loops(self):
        asyncio.create_task(self._announcement_loop())
        asyncio.create_task(self._reminder_loop())

    async def _announcement_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            now = datetime.now()
            for guild_id, schedules in list(self._schedules.items()):
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                
                for schedule in schedules:
                    if schedule.get("posted"):
                        continue
                    
                    post_time = schedule.get("post_time", 0)
                    if now.timestamp() >= post_time:
                        channel_id = schedule.get("channel_id")
                        message = schedule.get("message", "")
                        embed_data = schedule.get("embed", {})
                        
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            logger.warning(f"Announcement channel {channel_id} not found in guild {guild_id}")
                            schedule["posted"] = True  # Mark as posted to avoid retrying
                            self._save_data()
                            continue
                        # Check if bot can send messages to the channel
                        perms = channel.permissions_for(guild.me)
                        if not perms.send_messages:
                            logger.warning(f"Bot lacks send permissions in announcement channel {channel_id} for guild {guild_id}")
                            schedule["posted"] = True
                            self._save_data()
                            continue
                        if embed_data:
                            embed = discord.Embed(
                                title=embed_data.get("title", ""),
                                description=message,
                                color=int(embed_data.get("color", "349ke5f"), 16)
                            )
                            await channel.send(embed=embed)
                        else:
                            await channel.send(message)
                        
                        schedule["posted"] = True
                        self._save_data()
            
            await asyncio.sleep(60)

    async def _reminder_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            now = datetime.now()
            for guild_id, reminders in list(self._reminders.items()):
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                
                for reminder in reminders[:]:
                    if reminder.get("sent"):
                        continue
                    
                    due_time = reminder.get("due_time", 0)
                    if now.timestamp() >= due_time:
                        target_id = reminder.get("target_id")
                        message = reminder.get("message", "")
                        
                        target = guild.get_member(target_id)
                        if target:
                            try:
                                await target.send(f"⏰ Reminder: {message}")
                            except:
                                pass
                        
                        reminder["sent"] = True
                        self._reminders[guild_id] = [r for r in self._reminders[guild_id] if not r.get("sent")]
                        self._save_data()
            
            await asyncio.sleep(60)

    async def handle_announce_create(self, message, parts):
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 2:
            await message.channel.send("Usage: !announce <time> <message>\nExample: !announce 1h Server restart in 30 minutes!")
            return
        
        time_str = parts[1]
        delay_seconds = self._parse_time(time_str)
        
        if delay_seconds is None:
            await message.channel.send("Invalid time! Use: 30s, 5m, 1h, 1d")
            return
        
        remaining_parts = parts[2:] if len(parts) > 2 else [""]
        ann_message = " ".join(remaining_parts)
        
        post_time = datetime.now().timestamp() + delay_seconds
        
        if guild_id not in self._schedules:
            self._schedules[guild_id] = []
        
        self._schedules[guild_id].append({
            "message": ann_message,
            "post_time": post_time,
            "channel_id": message.channel.id,
            "posted": False,
            "created_by": str(message.author)
        })
        
        self._save_data()
        
        wait_min = delay_seconds / 60
        await message.channel.send(f"✅ Scheduled announcement in {wait_min:.0f} minutes!")

    async def handle_announce_list(self, message):
        guild = message.guild
        guild_id = guild.id
        
        schedules = self._schedules.get(guild_id, [])
        
        if not schedules:
            await message.channel.send("No scheduled announcements!")
            return
        
        pending = [s for s in schedules if not s.get("posted")]
        
        embed = discord.Embed(
            title="📅 Scheduled Announcements",
            color=discord.Color.blue()
        )
        
        for s in pending[:10]:
            time_left = s.get("post_time", 0) - datetime.now().timestamp()
            mins = max(0, time_left / 60)
            embed.add_field(
                name=f"⏰ {mins:.0f} min",
                value=s.get("message", "")[:50],
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_remind(self, message, parts):
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 2:
            await message.channel.send("Usage: !remind <time> <message>\nExample: !remind 30m Check the server!")
            return
        
        time_str = parts[1]
        delay_seconds = self._parse_time(time_str)
        
        if delay_seconds is None:
            await message.channel.send("Invalid time! Use: 30s, 5m, 1h, 1d")
            return
        
        reminder_text = " ".join(parts[2:]) if len(parts) > 2 else "Reminder"
        
        due_time = datetime.now().timestamp() + delay_seconds
        
        if guild_id not in self._reminders:
            self._reminders[guild_id] = []
        
        self._reminders[guild_id].append({
            "message": reminder_text,
            "due_time": due_time,
            "target_id": message.author.id,
            "sent": False
        })
        
        self._save_data()
        
        wait_min = delay_seconds / 60
        await message.channel.send(f"✅ Reminder set for {wait_min:.0f} minutes!")

    async def handle_remind_user(self, message, parts):
        guild = message.guild
        
        if len(parts) < 3:
            await message.channel.send("Usage: !remind @user <time> <message>")
            return
        
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        time_str = parts[2]
        delay_seconds = self._parse_time(time_str)
        
        if delay_seconds is None:
            await message.channel.send("Invalid time!")
            return
        
        reminder_text = " ".join(parts[3:]) if len(parts) > 3 else "Reminder"
        
        guild_id = guild.id
        due_time = datetime.now().timestamp() + delay_seconds
        
        if guild_id not in self._reminders:
            self._reminders[guild_id] = []
        
        self._reminders[guild_id].append({
            "message": reminder_text,
            "due_time": due_time,
            "target_id": user_id,
            "sent": False
        })
        
        self._save_data()
        
        target = guild.get_member(user_id)
        wait_min = delay_seconds / 60
        await message.channel.send(f"✅ Will remind {target.display_name} in {wait_min:.0f} minutes!")

    async def handle_reminders_list(self, message):
        guild = message.guild
        guild_id = guild.id
        
        user_id = message.author.id
        reminders = self._reminders.get(guild_id, [])
        user_reminders = [r for r in reminders if r.get("target_id") == user_id and not r.get("sent")]
        
        if not user_reminders:
            await message.channel.send("No active reminders!")
            return
        
        embed = discord.Embed(
            title="⏰ Your Reminders",
            color=discord.Color.blue()
        )
        
        for r in user_reminders[:10]:
            time_left = r.get("due_time", 0) - datetime.now().timestamp()
            mins = max(0, time_left / 60)
            embed.add_field(
                name=f"⏰ {mins:.0f} min",
                value=r.get("message", ""),
                inline=False
            )
        
        await message.channel.send(embed=embed)

    def _parse_time(self, time_str: str) -> Optional[float]:
        time_str = time_str.lower()
        
        multipliers = {
            "s": 1,
            "sec": 1,
            "m": 60,
            "min": 60,
            "h": 3600,
            "hour": 3600,
            "d": 86400,
            "day": 86400
        }
        
        for unit, mult in multipliers.items():
            if time_str.endswith(unit):
                try:
                    num = float(time_str[:-len(unit)])
                    return num * mult
                except:
                    return None
        
        return None


def setup(bot):
    return AutoAnnouncer(bot)