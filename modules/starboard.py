import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

from data_manager import dm
from logger import logger


@dataclass
class StarredMessage:
    message_id: int
    channel_id: int
    guild_id: int
    star_count: int
    original_url: str
    created_at: float


class StarboardSystem:
    def __init__(self, bot):
        self.bot = bot
        self._starred_messages: Dict[int, StarredMessage] = {}
        self._starboard_channels: Dict[int, int] = {}
        self._reaction_roles: Dict[int, Dict[str, int]] = {}
        self._emoji_rewards: Dict[int, dict] = {}

    def _load_guild_data(self, guild_id: int):
        """Lazy load guild data to ensure multi-server isolation."""
        if guild_id not in self._starboard_channels:
            data = dm.get_guild_data(guild_id, "starboard_system_data", {})
            self._starboard_channels[guild_id] = data.get("channel_id")
            self._reaction_roles[guild_id] = data.get("reaction_roles", {})
            self._emoji_rewards[guild_id] = data.get("emoji_rewards", {})

            starred = data.get("starred_messages", {})
            for msg_id, msg_data in starred.items():
                self._starred_messages[int(msg_id)] = StarredMessage(
                    message_id=int(msg_id),
                    channel_id=msg_data["channel_id"],
                    guild_id=msg_data["guild_id"],
                    star_count=msg_data["star_count"],
                    original_url=msg_data["original_url"],
                    created_at=msg_data["created_at"]
                )

    def _save_guild_data(self, guild_id: int):
        """Save guild data immediately for immortality."""
        # Filter starred messages for this guild
        guild_starred = {
            str(msg_id): {
                "channel_id": msg.channel_id,
                "guild_id": msg.guild_id,
                "star_count": msg.star_count,
                "original_url": msg.original_url,
                "created_at": msg.created_at
            }
            for msg_id, msg in self._starred_messages.items()
            if msg.guild_id == guild_id
        }

        data = {
            "channel_id": self._starboard_channels.get(guild_id),
            "reaction_roles": self._reaction_roles.get(guild_id, {}),
            "emoji_rewards": self._emoji_rewards.get(guild_id, {}),
            "starred_messages": guild_starred
        }
        dm.update_guild_data(guild_id, "starboard_system_data", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "starboard_config", {
            "enabled": True,
            "emoji": "⭐",
            "threshold": 3,
            "auto_pin": True,
            "pin_threshold": 10,
            "reward_thresholds": {
                "5": {"coins": 10, "xp": 5},
                "10": {"coins": 25, "xp": 15},
                "25": {"coins": 50, "xp": 30}
            },
            "reactions_enabled": True
        })

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        self._load_guild_data(guild.id)
        
        member = payload.member or await guild.fetch_member(payload.user_id)
        if not member or member.bot: return

        if guild.id in self._reaction_roles:
            role_map = self._reaction_roles[guild.id]
            emoji_str = str(payload.emoji)
            
            if emoji_str in role_map:
                role_id = role_map[emoji_str]
                role = guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role)
                    except:
                        pass
        
        settings = self.get_guild_settings(guild.id)

        # Honor the master "star reactions" toggle from StarboardConfigView.
        # When admins disable reactions via the config panel, star clicks should be a no-op.
        if not settings.get("reactions_enabled", True):
            return

        star_emoji = settings.get("emoji", "⭐")

        if str(payload.emoji) != star_emoji:
            return
        
        channel = guild.get_channel(payload.channel_id)
        if not channel: return
        message = await channel.fetch_message(payload.message_id)
        
        reaction = discord.utils.get(message.reactions, emoji=payload.emoji.name)
        count = reaction.count if reaction else 0

        if count >= settings.get("threshold", 3):
            await self.add_to_starboard(message, count)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        self._load_guild_data(guild.id)
        
        member = await guild.fetch_member(payload.user_id)
        if not member or member.bot: return

        if guild.id in self._reaction_roles:
            role_map = self._reaction_roles[guild.id]
            emoji_str = str(payload.emoji)
            
            if emoji_str in role_map:
                role_id = role_map[emoji_str]
                role = guild.get_role(role_id)
                if role:
                    try:
                        await member.remove_roles(role)
                    except:
                        pass

    async def add_to_starboard(self, message: discord.Message, star_count: int):
        guild_id = message.guild.id
        self._load_guild_data(guild_id)
        
        if guild_id not in self._starboard_channels or not self._starboard_channels[guild_id]:
            return
        
        starboard_channel_id = self._starboard_channels[guild_id]
        starboard_channel = message.guild.get_channel(starboard_channel_id)
        
        if not starboard_channel:
            return
        
        embed = discord.Embed(
            description=message.content[:2000],
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=f"#{message.channel.name}", inline=True)
        embed.add_field(name="Stars", value=str(star_count), inline=True)
        
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        
        jump_url = f"https://discord.com/channels/{guild_id}/{message.channel.id}/{message.id}"
        embed.add_field(name="Original", value=f"[Jump]({jump_url})", inline=True)
        
        starred_msg = self._starred_messages.get(message.id)
        
        if starred_msg:
            try:
                starred_msg.star_count = star_count
                old_msg = await starboard_channel.fetch_message(starred_msg.original_url.split('/')[-1])
                await old_msg.edit(embed=embed)
            except:
                pass
        else:
            try:
                new_msg = await starboard_channel.send(embed=embed)
                
                self._starred_messages[message.id] = StarredMessage(
                    message_id=message.id,
                    channel_id=message.channel.id,
                    guild_id=guild_id,
                    star_count=star_count,
                    original_url=jump_url,
                    created_at=time.time()
                )
                
                self._save_guild_data(guild_id)
                
                await self._check_reward(guild_id, message.author, star_count)
                await self._check_auto_pin(message, star_count)
                
            except Exception as e:
                logger.error(f"Failed to add to starboard: {e}")

    async def _check_reward(self, guild_id: int, user: discord.Member, star_count: int):
        settings = self.get_guild_settings(guild_id)
        thresholds = settings.get("reward_thresholds", {})
        
        for threshold, rewards in thresholds.items():
            if star_count >= int(threshold):
                coins = rewards.get("coins", 0)
                xp = rewards.get("xp", 0)

                if coins and hasattr(self.bot, "economy"):
                    self.bot.economy.add_coins(guild_id, user.id, coins)
                if xp and hasattr(self.bot, "leveling"):
                    self.bot.leveling.add_xp(guild_id, user.id, xp)

                try:
                    await user.send(f"⭐ You earned {coins} coins and {xp} XP for your starred message!")
                except:
                    pass

    async def _check_auto_pin(self, message: discord.Message, star_count: int):
        settings = self.get_guild_settings(message.guild.id)
        
        if not settings.get("auto_pin", True):
            return
        
        pin_threshold = settings.get("pin_threshold", 10)
        
        if star_count >= pin_threshold:
            try:
                await message.pin()
            except:
                pass

    def set_starboard_channel(self, guild_id: int, channel_id: int):
        self._load_guild_data(guild_id)
        self._starboard_channels[guild_id] = channel_id
        self._save_guild_data(guild_id)

    def add_reaction_role(self, guild_id: int, emoji: str, role_id: int):
        self._load_guild_data(guild_id)
        if guild_id not in self._reaction_roles:
            self._reaction_roles[guild_id] = {}
        
        self._reaction_roles[guild_id][emoji] = role_id
        self._save_guild_data(guild_id)

    def get_leaderboard(self, guild_id: int) -> List[dict]:
        self._load_guild_data(guild_id)
        messages = [m for m in self._starred_messages.values() if m.guild_id == guild_id]
        messages.sort(key=lambda x: x.star_count, reverse=True)
        
        leaderboard = []
        for i, msg in enumerate(messages[:10]):
            leaderboard.append({
                "rank": i + 1,
                "message_id": msg.message_id,
                "channel_id": msg.channel_id,
                "star_count": msg.star_count
            })
        
        return leaderboard

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "starboard_config", settings)
        
        # Create documentation channel
        try:
            doc_channel = await guild.create_text_channel("starboard-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        # Post comprehensive documentation
        doc_embed = discord.Embed(
            title="⭐ Starboard & Reaction System Guide",
            description="Complete guide to starring messages and using reaction roles!",
            color=discord.Color.gold()
        )
        doc_embed.add_field(
            name="📖 How It Works",
            value="React to messages with ⭐ to add them to the starboard. When a message gets enough stars, it's posted to the starboard channel for everyone to see!",
            inline=False
        )
        doc_embed.add_field(
            name="🎮 Available Commands",
            value="**!starboard** - View top starred messages\n" +
                  "**!help starboard** - Show this guide",
            inline=False
        )
        doc_embed.add_field(
            name="💡 How to Use",
            value="1. Find a message you like\n" +
                  "2. React with ⭐ (star emoji)\n" +
                  "3. Once it gets 3+ stars, it goes to starboard\n" +
                  "4. Highly starred messages (10+) get pinned!\n" +
                  "5. Message authors earn coins/XP rewards",
            inline=False
        )
        doc_embed.add_field(
            name="🎁 Rewards",
            value="• 5 stars: 10 coins, 5 XP\n" +
                  "• 10 stars: 25 coins, 15 XP\n" +
                  "• 25 stars: 50 coins, 30 XP",
            inline=False
        )
        doc_embed.set_footer(text="Created by Miro AI • Use !help starboard for more info")
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** React to any message with ⭐ to star it!")
        
        help_embed = discord.Embed(
            title="⭐ Starboard & Reaction System",
            description="Star messages to add to starboard. Reaction roles and emoji rewards.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="React with ⭐ to star messages. When they reach the threshold, they're posted to the starboard. Reaction roles give roles on emoji react.",
            inline=False
        )
        help_embed.add_field(
            name="!starboard",
            value="View top starred messages.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["starboard"] = json.dumps({
            "command_type": "starboard_leaderboard"
        })
        custom_cmds["help starboard"] = json.dumps({
            "command_type": "help_embed",
            "title": "⭐ Starboard & Reaction System",
            "description": "Star messages and earn rewards.",
            "fields": [
                {"name": "!starboard", "value": "View top starred messages.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
