import discord
from discord.ext import commands
import asyncio
import json
from typing import Dict, List, Optional

from data_manager import dm
from logger import logger


class ContentGenerator:
    def __init__(self, bot):
        self.bot = bot
        self._templates: Dict[int, dict] = {}

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "content_settings", {
            "enabled": True,
            "default_welcome_channel": None,
            "auto_topics": True,
            "auto_descriptions": True
        })

    async def generate_welcome_message(self, guild: discord.Guild, user: discord.Member) -> str:
        prompt = f"""Generate a welcoming message for a new member joining this Discord server.

SERVER: {guild.name}
MEMBER: {user.display_name}
MEMBER COUNT: {guild.member_count}

Respond with JSON only:
{{
    "welcome_message": "A warm, friendly welcome message (1-2 sentences)",
    "rules_hint": "Brief mention of where to find rules",
    "tip": "One useful tip for new members"
}}

Make it feel personal and welcoming, not generic."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild.id,
                user_id=user.id,
                user_input=prompt,
                system_prompt="You write welcoming messages for Discord servers. Be warm, friendly, and concise."
            )
            
            return result.get("welcome_message", f"Welcome {user.mention} to {guild.name}!")
            
        except Exception as e:
            logger.error(f"Failed to generate welcome message: {e}")
            return f"Welcome {user.mention} to {guild.name}!"

    async def generate_channel_topic(self, channel: discord.TextChannel, purpose: str = None) -> str:
        if purpose is None:
            purpose = f"channel named {channel.name}"
        
        prompt = f"""Generate a description/topic for a Discord channel.

CHANNEL: {channel.name}
PURPOSE: {purpose}
SERVER: {channel.guild.name}

Respond with JSON only:
{{
    "topic": "A brief 1-2 sentence topic description",
    "guidelines": ["1-2 usage guidelines"]
}}

Make it clear and helpful."""

        try:
            result = await self.bot.ai.chat(
                guild_id=channel.guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You write Discord channel descriptions. Be clear and helpful."
            )
            
            return result.get("topic", f"Discussion channel for {channel.name}")
            
        except Exception as e:
            logger.error(f"Failed to generate channel topic: {e}")
            return f"Discussion channel for {channel.name}"

    async def generate_rules_embed(self, guild: discord.Guild, raw_rules: str) -> discord.Embed:
        prompt = f"""Convert these raw rules into a formatted Discord embed.

SERVER: {guild.name}
RAW RULES: {raw_rules}

Respond with JSON only:
{{
    "title": "Rules",
    "description": "Brief intro (1 sentence)",
    "fields": [
        {{"name": "Rule name", "value": "Rule description"}}
    ],
    "footer": "Server name or custom footer"
}}

Format as clear, numbered rules."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You format Discord rules into attractive embeds. Be clear and concise."
            )
            
            embed = discord.Embed(
                title=result.get("title", "📋 Server Rules"),
                description=result.get("description", "Please follow these rules to keep our community great!"),
                color=discord.Color.blue()
            )
            
            for field in result.get("fields", [])[:10]:
                embed.add_field(
                    name=field.get("name", "Rule"),
                    value=field.get("value", ""),
                    inline=False
                )
            
            embed.set_footer(text=result.get("footer", guild.name))
            
            return embed
            
        except Exception as e:
            logger.error(f"Failed to generate rules embed: {e}")
            return discord.Embed(
                title="📋 Server Rules",
                description=raw_rules[:2000],
                color=discord.Color.blue()
            )

    async def generate_event_banner(self, guild: discord.Guild, event_name: str, 
                                   event_type: str, details: str) -> dict:
        prompt = f"""Generate an event description and banner content.

EVENT NAME: {event_name}
EVENT TYPE: {event_type}
DETAILS: {details}

Respond with JSON only:
{{
    "title": "Event title with emoji",
    "description": "2-3 sentence event description",
    "schedule": "When the event happens",
    "requirements": ["requirement 1", "requirement 2"],
    "rewards": ["reward 1", "reward 2"],
    "banner_text": "Short text for event banner (under 50 chars)"
}}

Make it exciting and clear."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You create exciting Discord event descriptions. Be fun and engaging."
            )
            
            return {
                "title": result.get("title", f"🎮 {event_name}"),
                "description": result.get("description", details),
                "schedule": result.get("schedule", "Scheduled soon"),
                "requirements": result.get("requirements", []),
                "rewards": result.get("rewards", []),
                "banner_text": result.get("banner_text", event_name[:50])
            }
            
        except Exception as e:
            logger.error(f"Failed to generate event banner: {e}")
            return {
                "title": f"🎮 {event_name}",
                "description": details,
                "schedule": "Scheduled soon",
                "requirements": [],
                "rewards": [],
                "banner_text": event_name[:50]
            }

    async def summarize_discussion(self, messages: List[discord.Message], max_length: int = 500) -> str:
        if not messages:
            return "No messages to summarize."
        
        message_texts = []
        for msg in messages[-20:]:
            message_texts.append(f"{msg.author.display_name}: {msg.content}")
        
        combined = "\n".join(message_texts)
        
        prompt = f"""Summarize this Discord discussion into a brief summary.

MESSAGES:
{combined}

Respond with JSON only:
{{
    "summary": "A {max_length} character summary of the main points discussed",
    "key_points": ["point 1", "point 2", "point 3"],
    "conclusion": "What was decided or discussed"
}}

Keep it concise and informative."""

        try:
            result = await self.bot.ai.chat(
                guild_id=messages[0].guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You summarize Discord discussions. Be concise and capture the main points."
            )
            
            return result.get("summary", "Discussion summary unavailable.")
            
        except Exception as e:
            logger.error(f"Failed to summarize discussion: {e}")
            return "Discussion summary unavailable."

    async def generate_channel_description(self, guild: discord.Guild, channel_name: str,
                                         category: str = None) -> str:
        prompt = f"""Generate a description for a Discord channel.

CHANNEL NAME: {channel_name}
CATEGORY: {category or "general"}

Respond with JSON only:
{{
    "description": "What this channel is for (1-2 sentences)",
    "what_to_post": ["type of content 1", "type of content 2"],
    "tips": ["tip 1", "tip 2"]
}}

Make it helpful for new users."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You write Discord channel descriptions. Be helpful and clear."
            )
            
            return result.get("description", f"Discussion channel for {channel_name}")
            
        except Exception as e:
            logger.error(f"Failed to generate channel description: {e}")
            return f"Discussion channel for {channel_name}"

    async def generate_rule_responses(self, guild_id: int) -> Dict[str, str]:
        prompt = """Generate common rule reminders for a Discord server.

Respond with JSON only:
{
    "rules": {
        "spam": "Please don't spam messages.",
        "offtopic": "Please keep discussions on-topic.",
        "language": "Please keep language appropriate.",
        "advertising": "No advertising without permission.",
        "personal": "No personal attacks or harassment."
    }
}

Create 5 common rule reminders with trigger phrases and responses."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=0,
                user_input=prompt,
                system_prompt="You create rule reminder responses for Discord servers."
            )
            
            return result.get("rules", {})
            
        except Exception as e:
            logger.error(f"Failed to generate rule responses: {e}")
            return {}

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "content_settings", settings)
        
        help_embed = discord.Embed(
            title="📝 AI Content Generator",
            description="AI-generated content for your server - welcome messages, channel topics, rules, and more.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="When you create channels or set up systems, the AI can auto-generate descriptions, topics, and content.",
            inline=False
        )
        help_embed.add_field(
            name="Usage",
            value="Used automatically when creating:\n• Welcome messages\n• Channel topics\n• Rule embeds\n• Event descriptions\n• Discussion summaries",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["help content"] = json.dumps({
            "command_type": "help_embed",
            "title": "📝 AI Content Generator",
            "description": "AI-generated content for your server.",
            "fields": [
                {"name": "How it works", "value": "Used automatically when creating channels and systems.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
