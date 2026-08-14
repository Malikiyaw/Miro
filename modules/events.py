import discord
from discord.ext import commands
import asyncio
import json
import time
import random
import re
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
import croniter

from data_manager import dm
from logger import logger


class EventType(Enum):
    TRIVIA = "trivia"
    STORY_BUILD = "story_build"
    DEBATE = "debate"
    QUIZ = "quiz"
    GAME = "game"
    GIVEAWAY = "giveaway"
    CONTEST = "contest"
    POLL = "poll"
    CUSTOM = "custom"


class EventStatus(Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledEvent:
    id: str
    guild_id: int
    channel_id: int
    name: str
    description: str
    event_type: EventType
    schedule: str
    next_run: float
    status: EventStatus
    rewards: dict
    settings: dict
    created_by: int
    created_at: float
    participants: List[int]
    message_id: Optional[int]


@dataclass
class ActiveEvent:
    id: str
    event_type: EventType
    message_id: int
    channel_id: int
    guild_id: int
    data: dict
    started_at: float
    participants: List[int]
    scores: Dict[int, int]


class EventScheduler:
    def __init__(self, bot):
        self.bot = bot
        self._scheduled_events: Dict[str, ScheduledEvent] = {}
        self._active_events: Dict[str, ActiveEvent] = {}
        self._guild_settings: Dict[int, dict] = {}
        self._load_scheduled_events()

    def _load_scheduled_events(self):
        events_data = dm.load_json("scheduled_events", default={})
        
        for event_id, data in events_data.items():
            try:
                event = ScheduledEvent(
                    id=event_id,
                    guild_id=data["guild_id"],
                    channel_id=data["channel_id"],
                    name=data["name"],
                    description=data["description"],
                    event_type=EventType(data["event_type"]),
                    schedule=data["schedule"],
                    next_run=data["next_run"],
                    status=EventStatus(data["status"]),
                    rewards=data["rewards"],
                    settings=data["settings"],
                    created_by=data["created_by"],
                    created_at=data["created_at"],
                    participants=data.get("participants", []),
                    message_id=data.get("message_id")
                )
                self._scheduled_events[event_id] = event
            except Exception as e:
                logger.error(f"Failed to load scheduled event {event_id}: {e}")

    def _save_scheduled_event(self, event: ScheduledEvent):
        events_data = dm.load_json("scheduled_events", default={})
        events_data[event.id] = {
            "guild_id": event.guild_id,
            "channel_id": event.channel_id,
            "name": event.name,
            "description": event.description,
            "event_type": event.event_type.value,
            "schedule": event.schedule,
            "next_run": event.next_run,
            "status": event.status.value,
            "rewards": event.rewards,
            "settings": event.settings,
            "created_by": event.created_by,
            "created_at": event.created_at,
            "participants": event.participants,
            "message_id": event.message_id
        }
        dm.save_json("scheduled_events", events_data)

    def get_guild_settings(self, guild_id: int) -> dict:
        if guild_id in self._guild_settings:
            return self._guild_settings[guild_id]
        
        settings = dm.get_guild_data(guild_id, "event_settings", {
            "enabled": True,
            "auto_rewards": True,
            "default_rewards": {
                "coins": 100,
                "xp": 50
            },
            "optimal_hours": [18, 19, 20, 21],
            "min_participants": 3,
            "max_duration_minutes": 30
        })
        self._guild_settings[guild_id] = settings
        return settings

    def calculate_next_run(self, schedule: str) -> Optional[float]:
        try:
            cron = croniter.croniter(schedule)
            return cron.get_next()
        except:
            return None

    def start_event_monitor(self):
        asyncio.create_task(self._event_monitor_loop())

    async def _event_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                current_time = time.time()
                
                for event_id, event in list(self._scheduled_events.items()):
                    if event.status == EventStatus.SCHEDULED and event.next_run <= current_time:
                        await self._start_event(event)
                
                for event_id, event in list(self._active_events.items()):
                    duration = event.data.get("duration_minutes", 15)
                    if current_time - event.started_at >= duration * 60:
                        await self._end_event(event)
            
            except Exception as e:
                logger.error(f"Event monitor error: {e}")
            
            await asyncio.sleep(30)

    async def _start_event(self, event: ScheduledEvent):
        event.status = EventStatus.ACTIVE
        self._save_scheduled_event(event)
        
        channel = self.bot.get_channel(event.channel_id)
        if not channel:
            logger.error(f"Event channel not found: {event.channel_id}")
            return
        
        embed = discord.Embed(
            title=f"🎮 {event.name}",
            description=event.description,
            color=discord.Color.gold()
        )
        embed.add_field(name="Type", value=event.event_type.value.title(), inline=True)
        embed.add_field(name="Rewards", value=f"💰 {event.rewards.get('coins', 0)} coins, XP {event.rewards.get('xp', 0)}", inline=True)
        
        view = discord.ui.View()
        join_btn = discord.ui.Button(label="Join Event", style=discord.ButtonStyle.primary, custom_id=f"event_join_{event.id}")
        
        async def join_callback(interaction: discord.Interaction):
            if interaction.message.id != event.message_id:
                return
            
            if interaction.user.id in event.participants:
                await interaction.response.send_message("You already joined!", ephemeral=True)
                return
            
            event.participants.append(interaction.user.id)
            self._save_scheduled_event(event)
            
            await interaction.response.send_message(f"✅ Joined {event.name}!", ephemeral=True)
            self._update_event_message(event)
        
        join_btn.callback = join_callback
        view.add_item(join_btn)
        
        message = await channel.send(embed=embed, view=view)
        event.message_id = message.id
        
        active_event = ActiveEvent(
            id=event.id,
            event_type=event.event_type,
            message_id=message.id,
            channel_id=channel.id,
            guild_id=event.guild_id,
            data={
                "name": event.name,
                "description": event.description,
                "settings": event.settings,
                "duration_minutes": event.settings.get("duration", 15),
                "questions": [],
                "current_question": 0
            },
            started_at=time.time(),
            participants=event.participants,
            scores={}
        )
        self._active_events[event.id] = active_event
        
        if event.event_type == EventType.TRIVIA:
            await self._run_trivia_event(event, channel, active_event)
        elif event.event_type == EventType.STORY_BUILD:
            await self._run_story_event(event, channel, active_event)
        
        logger.info(f"Started event: {event.name} in {channel.name}")

    async def _run_trivia_event(self, event: ScheduledEvent, channel: discord.TextChannel, active_event: ActiveEvent):
        topics = active_event.data["settings"].get("topics", ["general"])
        
        topics_str = ", ".join(topics)
        trivia_prompt = f"""Generate 5 trivia questions about {topics_str}.
        
Respond with JSON only:
{{
    "questions": [
        {{
            "question": "question text",
            "options": ["option A", "option B", "option C", "option D"],
            "correct": 0
        }}
    ]
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=event.guild_id,
                user_id=event.created_by,
                user_input=trivia_prompt,
                system_prompt="You generate fun trivia questions. Return exactly 5 questions with 4 options each and indicate which option is correct (0-3)."
            )
            
            active_event.data["questions"] = result.get("questions", [])
        except Exception as e:
            logger.error(f"Failed to generate trivia: {e}")
            active_event.data["questions"] = self._get_default_trivia()

    def _get_default_trivia(self) -> List[dict]:
        return [
            {"question": "What is 2 + 2?", "options": ["3", "4", "5", "6"], "correct": 1},
            {"question": "What color is the sky?", "options": ["Red", "Blue", "Green", "Yellow"], "correct": 1},
            {"question": "What is the capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "correct": 2},
            {"question": "How many days in a year?", "options": ["365", "366", "364", "360"], "correct": 0},
            {"question": "What is H2O?", "options": ["Salt", "Water", "Gold", "Oxygen"], "correct": 1}
        ]

    async def _run_story_event(self, event: ScheduledEvent, channel: discord.TextChannel, active_event: ActiveEvent):
        story_prompt = active_event.data["settings"].get("story_prompt", "Start a creative story with the theme: adventure")
        
        try:
            result = await self.bot.ai.chat(
                guild_id=event.guild_id,
                user_id=event.created_by,
                user_input=story_prompt,
                system_prompt="Continue a collaborative story. Keep each contribution to 1-2 sentences. Build on previous contributions."
            )
            
            active_event.data["story"] = result.get("summary", "The story begins...")
            active_event.data["contributions"] = []
        except Exception as e:
            logger.error(f"Failed to start story: {e}")
            active_event.data["story"] = "The story begins..."
            active_event.data["contributions"] = []
    
    """Poll System"""
    POLL_OPTIONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    async def create_poll(self, channel: discord.TextChannel, question: str, options: List[str], 
                        duration_minutes: int = 60, multiple_choice: bool = False) -> discord.Message:
        """Create a poll message with reactions."""
        if len(options) > 10:
            options = options[:10]
        
        options_text = "\n".join(f"{self.POLL_OPTIONS[i]} {opt}" for i, opt in enumerate(options))
        
        embed = discord.Embed(title="📊 New Poll!", color=discord.Color.blurple())
        embed.add_field(name=question, value=options_text, inline=False)
        embed.set_footer(text=f"Duration: {duration_minutes} minutes | {'Multiple choice' if multiple_choice else 'Single choice'}")
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="End Poll", style=discord.ButtonStyle.danger, custom_id="end_poll"))
        
        msg = await channel.send(embed=embed)
        
        # Add reactions
        for i in range(len(options)):
            await msg.add_reaction(self.POLL_OPTIONS[i])
        
        # Add vote counts to message
        await msg.add_reaction("📊")
        
        return msg
    
    async def create_contest(self, channel: discord.TextChannel, title: str, description: str,
                           submission_deadline: int = 7) -> discord.Message:
        """Create a contest with submissions."""
        embed = discord.Embed(title=f"🏆 {title}", description=description, color=discord.Color.gold())
        embed.add_field(name="How to Enter", value="DM the bot your submission!", inline=False)
        embed.add_field(name="Deadline", value=f"{submission_deadline} days", inline=False)
        
        msg = await channel.send(embed=embed)
        
        return msg
    
    async def _end_event(self, active_event: ActiveEvent):
        event_id = active_event.id
        
        if event_id in self._scheduled_events:
            scheduled = self._scheduled_events[event_id]
            scheduled.status = EventStatus.COMPLETED
            scheduled.next_run = self.calculate_next_run(scheduled.schedule) or (time.time() + 86400)
            scheduled.status = EventStatus.SCHEDULED
            scheduled.participants = []
            self._save_scheduled_event(scheduled)
        
        channel = self.bot.get_channel(active_event.channel_id)
        if channel and active_event.participants:
            embed = discord.Embed(
                title=f"🏆 Event Ended: {active_event.data['name']}",
                description="Thanks for participating!",
                color=discord.Color.gold()
            )
            
            if active_event.scores:
                top_scores = sorted(active_event.scores.items(), key=lambda x: x[1], reverse=True)[:5]
                score_text = "\n".join([f"**{i+1}.** <@{uid}> - {score} pts" for i, (uid, score) in enumerate(top_scores)])
                embed.add_field(name="Leaderboard", value=score_text, inline=False)
                
                await self._distribute_rewards(active_event, top_scores)
            else:
                embed.add_field(name="Results", value="No scores recorded.", inline=False)
            
            await channel.send(embed=embed)
        
        del self._active_events[event_id]
        logger.info(f"Event ended: {active_event.data['name']}")

    async def _distribute_rewards(self, active_event: ActiveEvent, top_scores: List[tuple]):
        event_id = active_event.id
        if event_id in self._scheduled_events:
            scheduled = self._scheduled_events[event_id]
            rewards = scheduled.rewards
            
            for i, (user_id, score) in enumerate(top_scores):
                multiplier = 1.0 if i == 0 else 0.5 if i == 1 else 0.25
                
                coins = int(rewards.get("coins", 100) * multiplier)
                xp = int(rewards.get("xp", 50) * multiplier)
                
                user_data = dm.get_guild_data(active_event.guild_id, f"user_{user_id}", {})
                user_data["coins"] = user_data.get("coins", 0) + coins
                user_data["xp"] = user_data.get("xp", 0) + xp
                dm.update_guild_data(active_event.guild_id, f"user_{user_id}", user_data)

    def _update_event_message(self, event: ScheduledEvent):
        """Update the event embed to reflect current participant count."""
        if not event.message_id:
            return

        async def _update():
            try:
                channel = self.bot.get_channel(event.channel_id)
                if not channel:
                    return
                message = await channel.fetch_message(event.message_id)
                if not message:
                    return

                embed = message.embeds[0]
                # Update participant count field
                field_found = False
                for i, field in enumerate(embed.fields):
                    if field.name == "Participants":
                        embed.set_field_at(i, name="Participants", value=str(len(event.participants)), inline=True)
                        field_found = True
                        break

                if not field_found:
                    embed.add_field(name="Participants", value=str(len(event.participants)), inline=True)

                await message.edit(embed=embed)
            except Exception as e:
                logger.debug(f"Could not update event message: {e}")

        asyncio.create_task(_update())

    async def create_event(self, guild_id: int, channel_id: int, name: str, description: str,
                          event_type: EventType, schedule: str, created_by: int, 
                          rewards: dict = None, settings: dict = None) -> ScheduledEvent:
        event_id = f"event_{guild_id}_{int(time.time())}"
        
        next_run = self.calculate_next_run(schedule)
        
        event = ScheduledEvent(
            id=event_id,
            guild_id=guild_id,
            channel_id=channel_id,
            name=name,
            description=description,
            event_type=event_type,
            schedule=schedule,
            next_run=next_run or time.time(),
            status=EventStatus.SCHEDULED,
            rewards=rewards or {"coins": 100, "xp": 50},
            settings=settings or {},
            created_by=created_by,
            created_at=time.time(),
            participants=[],
            message_id=None
        )
        
        self._scheduled_events[event_id] = event
        self._save_scheduled_event(event)
        
        return event

    async def ai_create_event(self, guild_id: int, user_id: int, request: str) -> ScheduledEvent:
        settings = self.get_guild_settings(guild_id)
        
        prompt = f"""Create a scheduled event based on this request: "{request}"

Available event types: trivia, story_build, debate, quiz, game, giveaway, contest
Schedule format: cron expression (e.g., "0 19 * * *" for daily at 7pm)

Respond with JSON only:
{{
    "name": "Event name",
    "description": "What happens in this event",
    "event_type": "trivia/story_build/etc",
    "schedule": "0 19 * * *",
    "topics": ["topic1", "topic2"],
    "rewards": {{"coins": 100, "xp": 50}},
    "duration": 15
}}"""

        result = await self.bot.ai.chat(
            guild_id=guild_id,
            user_id=user_id,
            user_input=prompt,
            system_prompt="You create fun Discord events. Be creative and specific. Use standard cron format for schedules."
        )
        
        channel = self.bot.get_guild(guild_id).text_channels[0]
        
        return await self.create_event(
            guild_id=guild_id,
            channel_id=channel.id,
            name=result.get("name", "AI Event"),
            description=result.get("description", "Fun event!"),
            event_type=EventType(result.get("event_type", "trivia")),
            schedule=result.get("schedule", "0 19 * * *"),
            created_by=user_id,
            rewards=result.get("rewards", settings["default_rewards"]),
            settings=result
        )

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "event_settings", settings)
        
        help_embed = discord.Embed(
            title="📅 Smart Event Scheduler",
            description="AI-powered event creation and scheduling with automatic rewards.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Tell the AI what kind of event you want, and it will create and schedule it automatically. Events run on cron schedules with automatic participation rewards.",
            inline=False
        )
        help_embed.add_field(
            name="AI Event Creation",
            value="Use /bot to create events: 'Create a weekly trivia event about science on Sundays at 8pm'",
            inline=False
        )
        help_embed.add_field(
            name="!events",
            value="List all scheduled events.",
            inline=False
        )
        help_embed.add_field(
            name="!join <event>",
            value="Join an active event.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["events"] = json.dumps({
            "command_type": "list_events"
        })
        custom_cmds["evenf"] = json.dumps({
            "command_type": "list_events"
        })
        custom_cmds["evenf create"] = json.dumps({
            "command_type": "create_event"
        })
        custom_cmds["help events"] = json.dumps({
            "command_type": "help_embed",
            "title": "📅 Smart Event Scheduler",
            "description": "AI-powered event creation and scheduling.",
            "fields": [
                {"name": "How it works", "value": "Tell the AI what kind of event you want.", "inline": False},
                {"name": "!events", "value": "List all scheduled events.", "inline": False},
                {"name": "!join <event>", "value": "Join an active event.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
