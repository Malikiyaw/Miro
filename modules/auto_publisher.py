import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional

from data_manager import dm
from logger import logger


class AutoPublisher:
    def __init__(self, bot):
        self.bot = bot
        self._publish_channels: Dict[int, List[int]] = {}
        self._load_settings()

    def _load_settings(self):
        """Settings are now loaded per-guild in get_guild_settings."""
        # No global settings to load
        return

    def _save_settings(self, guild_id: int, settings: dict):
        dm.update_guild_data(guild_id, "auto_publisher_settings", settings)

    def start_bump_monitor(self):
        asyncio.create_task(self._bump_monitor_loop())

    async def _bump_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    settings = self.get_guild_settings(guild.id)
                    bump_channel_id = settings.get("bump_channel")
                    
                    if bump_channel_id:
                        channel = guild.get_channel(int(bump_channel_id))
                        if channel:
                            await self._check_bump_reminder(channel, settings)
            except Exception as e:
                logger.error(f"Bump monitor error: {e}")
            
            await asyncio.sleep(3600)

    async def _check_bump_reminder(self, channel: discord.TextChannel, settings: dict):
        messages = []
        
        try:
            async for message in channel.history(limit=10):
                messages.append(message)
        except Exception as e:
            logger.error(f"Failed to get channel history: {e}")
            return
        
        # Common bump bot IDs (DISBOARD, etc.)
        BUMP_BOT_IDS = [302050872383242240]

        for message in messages:
            if message.author.id in BUMP_BOT_IDS:
                if "bump" in message.content.lower() or "success" in message.content.lower():
                    last_bump = message.created_at.timestamp()
                    time_since = time.time() - last_bump
                    
                    if time_since > 7200:
                        embed = discord.Embed(
                            title="💡 Bump Reminder",
                            description="The server can be bumped! Use `/bump` to help the server grow.",
                            color=discord.Color.blue()
                        )
                        
                        try:
                            await channel.send(embed=embed)
                        except Exception as e:
                            logger.error(f"Failed to send bump reminder: {e}")
                    
                    break

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "auto_publisher_settings", {
            "enabled": True,
            "auto_publish": True,
            "publish_channels": [],
            "announcement_channel": None,
            "bump_channel": None,
            "bump_reminder": True
        })

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        guild_id = message.guild.id
        settings = self.get_guild_settings(guild_id)
        
        if not settings.get("auto_publish", True):
            return
        
        if not isinstance(message.channel, discord.Thread):
            return
        
        thread = message.channel
        
        if thread.parent_id in settings.get("publish_channels", []):
            try:
                if not thread.pinned:
                    await thread.publish()
            except Exception as e:
                logger.error(f"Failed to publish thread: {e}")

    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if before.pinned or after.pinned:
            return
        
        guild_id = before.guild.id
        settings = self.get_guild_settings(guild_id)
        
        if not settings.get("auto_publish", True):
            return
        
        if before.parent_id in settings.get("publish_channels", []):
            try:
                if not after.pinned:
                    await after.publish()
            except Exception as e:
                logger.error(f"Failed to publish thread on update: {e}")

    def add_publish_channel(self, guild_id: int, channel_id: int):
        settings = self.get_guild_settings(guild_id)
        if "publish_channels" not in settings:
            settings["publish_channels"] = []
        
        if channel_id not in settings["publish_channels"]:
            settings["publish_channels"].append(channel_id)
            self._save_settings(guild_id, settings)

    async def create_announcement(self, guild_id: int, channel_id: int, title: str, 
                                  content: str, mention_roles: List[int] = None) -> discord.Message:
        if mention_roles is None:
            mention_roles = []
            
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None
            
        channel = guild.get_channel(channel_id)
        
        if not channel:
            return None

        embed = discord.Embed(
            title=title,
            description=content,
            color=discord.Color.blue()
        )

        mentions = []
        if mention_roles:
            for role_id in mention_roles:
                role = guild.get_role(role_id)
                if role:
                    mentions.append(role.mention)

        message_content = ", ".join(mentions) if mentions else ""

        try:
            msg = await channel.send(content=message_content, embed=embed)
            await msg.publish()
            return msg
        except Exception as e:
            logger.error(f"Failed to create announcement: {e}")
            return None

    async def schedule_announcement(self, guild_id: int, channel_id: int, title: str,
                                    content: str, post_at: float, mention_roles: List[int] = None):
        if mention_roles is None:
            mention_roles = []
            
        scheduled = {
            "id": f"scheduled_{guild_id}_{int(time.time())}",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "title": title,
            "content": content,
            "post_at": post_at,
            "mention_roles": mention_roles or [],
            "created_at": time.time()
        }

        scheduled_announcements = dm.get_guild_data(guild_id, "scheduled_announcements", {})
        scheduled_announcements[scheduled["id"]] = scheduled
        dm.update_guild_data(guild_id, "scheduled_announcements", scheduled_announcements)

        asyncio.create_task(self._post_scheduled(scheduled))

        return scheduled

    async def _post_scheduled(self, scheduled: dict):
        wait_time = scheduled["post_at"] - time.time()
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        await self.create_announcement(
            scheduled["guild_id"],
            scheduled["channel_id"],
            scheduled["title"],
            scheduled["content"],
            scheduled.get("mention_roles", [])
        )
        
        scheduled_announcements = dm.get_guild_data(scheduled["guild_id"], "scheduled_announcements", {})
        if scheduled["id"] in scheduled_announcements:
            del scheduled_announcements[scheduled["id"]]
            dm.update_guild_data(scheduled["guild_id"], "scheduled_announcements", scheduled_announcements)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "auto_publisher_settings", settings)
        
        help_embed = discord.Embed(
            title="📢 Auto-Publisher",
            description="Auto-publish threads and announcement management.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Automatically publishes new threads in selected channels. Supports scheduled announcements and bump reminders.",
            inline=False
        )
        help_embed.add_field(
            name="!announce",
            value="Create an announcement (admin).",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["announce"] = json.dumps({
            "command_type": "create_announcement"
        })
        custom_cmds["help publisher"] = json.dumps({
            "command_type": "help_embed",
            "title": "📢 Auto-Publisher",
            "description": "Auto-publish threads.",
            "fields": [
                {"name": "!announce", "value": "Create announcement.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


async def setup(bot):
    await bot.add_cog(AutoPublisher(bot))