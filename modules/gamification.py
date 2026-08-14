import discord
from discord.ext import commands
import asyncio
import json
import time
import random
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from data_manager import dm
from logger import logger
import os


class QuestType(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    PERSONAL = "personal"
    SOCIAL = "social"
    CHALLENGE = "challenge"


class QuestStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CLAIMED = "claimed"


@dataclass
class Quest:
    id: str
    guild_id: int
    user_id: int
    quest_type: QuestType
    title: str
    description: str
    requirements: dict
    rewards: dict
    expires_at: float
    status: QuestStatus
    progress: int
    created_at: float


@dataclass
class Skill:
    name: str
    level: int
    xp: int
    xp_to_next: int


class AdaptiveGamification:
    def __init__(self, bot):
        self.bot = bot
        self._active_quests: Dict[str, Quest] = {}
        self._user_skills: Dict[int, Dict[int, Dict[str, Skill]]] = {}
        self._seasonal_events: Dict[int, dict] = {}
        self._server_challenges: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        """Load quests and events from guild-specific files."""
        count = 0
        data_dir = "data"
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_id_str = filename[6:-5]
                        if not guild_id_str.isdigit(): continue
                        guild_id = int(guild_id_str)
                        guild_data = dm.load_json(filename[:-5], default={})

                        # Load quests
                        quests_data = guild_data.get("quests", {})
                        for quest_id, data in quests_data.items():
                            quest = Quest(
                                id=quest_id,
                                guild_id=guild_id,
                                user_id=data["user_id"],
                                quest_type=QuestType(data["quest_type"]),
                                title=data["title"],
                                description=data["description"],
                                requirements=data["requirements"],
                                rewards=data["rewards"],
                                expires_at=data["expires_at"],
                                status=QuestStatus(data["status"]),
                                progress=data["progress"],
                                created_at=data["created_at"]
                            )
                            if quest.status == QuestStatus.ACTIVE and quest.expires_at > time.time():
                                self._active_quests[quest_id] = quest
                                count += 1

                        # Load seasonal events
                        guild_seasonal = guild_data.get("seasonal_events", {})
                        if guild_seasonal:
                            self._seasonal_events[guild_id] = guild_seasonal

                    except Exception as e:
                        logger.error(f"Failed to load gamification data from {filename}: {e}")
        logger.info(f"Loaded {count} active quests from guild files.")

    def _save_quests(self, quest: Quest):
        """Save a single quest to its guild-specific file."""
        guild_id = quest.guild_id
        quests = dm.get_guild_data(guild_id, "quests", {})
        
        quests[quest.id] = {
            "user_id": quest.user_id,
            "quest_type": quest.quest_type.value,
            "title": quest.title,
            "description": quest.description,
            "requirements": quest.requirements,
            "rewards": quest.rewards,
            "expires_at": quest.expires_at,
            "status": quest.status.value,
            "progress": quest.progress,
            "created_at": quest.created_at
        }
        dm.update_guild_data(guild_id, "quests", quests)


    def start_quest_refresh(self):
        asyncio.create_task(self._quest_refresh_loop())

    async def _quest_refresh_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                await self._refresh_daily_quests()
                await self._refresh_server_challenges()
                await self._check_quest_progress()
                await self._update_ranking_titles()
            except Exception as e:
                logger.error(f"Quest refresh error: {e}")
            
            await asyncio.sleep(60)

    async def _refresh_server_challenges(self):
        for guild in self.bot.guilds:
            challenges = dm.get_guild_data(guild.id, "server_challenges", [])
            if not challenges:
                # Generate new set
                new_challenges = [
                    {"id": "daily_msg", "name": "Chat Fever", "desc": "Send 1000 messages collectively", "target": 1000, "progress": 0, "type": "daily", "reward": {"coins": 500}},
                    {"id": "weekly_voice", "name": "Talkative Community", "desc": "Spend 100 hours in voice collectively", "target": 6000, "progress": 0, "type": "weekly", "reward": {"coins": 2000}}
                ]
                dm.update_guild_data(guild.id, "server_challenges", new_challenges)

    async def _refresh_daily_quests(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                
                await self._generate_daily_quest(guild.id, member.id)

    async def _generate_daily_quest(self, guild_id: int, user_id: int):
        # Skip AI quest generation silently if no API key is configured for this guild
        keys = self.bot.ai._get_all_guild_keys(guild_id)
        if not keys:
            return

        existing_quest_count = sum(
            1 for q in self._active_quests.values()
            if q.guild_id == guild_id and q.user_id == user_id and q.quest_type == QuestType.DAILY and q.status == QuestStatus.ACTIVE
        )
        
        if existing_quest_count >= 3:
            return
        
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        interests = user_data.get("interests", ["general"])
        
        prompt = f"""Generate a daily quest for a Discord user.

User interests: {', '.join(interests)}

Respond with JSON only:
{{
    "title": "Quest title",
    "description": "What the player needs to do",
    "type": "daily",
    "requirements": {{"type": "messages", "count": 10}},
    "rewards": {{"coins": 50, "xp": 25}},
    "duration_hours": 24
}}

Make it fun and varied. Consider message sending, reactions, voice chat, command usage, etc."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=user_id,
                user_input=prompt,
                system_prompt="You create fun daily quests for Discord users. Keep them achievable (5-20 actions)."
            )
            
            if not result or "error" in result:
                logger.warning(f"AI failed to provide quest data: {result.get('error', 'Unknown error')}")
                return
            
            quest_id = f"quest_{guild_id}_{user_id}_{int(time.time())}"
            
            quest = Quest(
                id=quest_id,
                guild_id=guild_id,
                user_id=user_id,
                quest_type=QuestType.DAILY,
                title=result.get("title", "Daily Quest"),
                description=result.get("description", "Complete this quest!"),
                requirements=result.get("requirements", {"type": "messages", "count": 10}),
                rewards=result.get("rewards", {"coins": 50, "xp": 25}),
                expires_at=time.time() + (result.get("duration_hours", 24) * 3600),
                status=QuestStatus.ACTIVE,
                progress=0,
                created_at=time.time()
            )
            
            self._active_quests[quest_id] = quest
            self._save_quests(quest)
            
        except Exception as e:
            error_str = str(e)
            if "No API key" in error_str or "API key" in error_str or "RetryError" in error_str:
                return
            logger.warning(f"Failed to generate daily quest for user {user_id} in guild {guild_id}: {e}")

    async def _check_quest_progress(self):
        current_time = time.time()
        
        for quest_id, quest in list(self._active_quests.items()):
            if quest.status != QuestStatus.ACTIVE:
                continue
            
            if quest.expires_at < current_time:
                quest.status = QuestStatus.EXPIRED
                self._save_quests(quest)
                continue
            
            if quest.quest_type == QuestType.DAILY:
                user_data = dm.get_guild_data(quest.guild_id, f"user_{quest.user_id}", {})
                
                req_type = quest.requirements.get("type")
                req_count = quest.requirements.get("count", 10)
                
                if req_type == "messages":
                    current = user_data.get("messages_sent_today", 0)
                    quest.progress = min(current, req_count)
                elif req_type == "commands":
                    current = user_data.get("commands_used_today", 0)
                    quest.progress = min(current, req_count)
                elif req_type == "voice":
                    current = user_data.get("voice_minutes_today", 0)
                    quest.progress = min(current, req_count)
                
                if quest.progress >= req_count:
                    quest.status = QuestStatus.COMPLETED
                    await self._notify_quest_complete(quest)
                
                self._save_quests(quest)


    async def _update_ranking_titles(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot: continue

                xp = self.bot.leveling.get_xp(guild.id, member.id)
                level = self.bot.leveling.get_level_from_xp(xp)

                title_name = None
                if level >= 100: title_name = "Legend"
                elif level >= 50: title_name = "Elite"
                elif level >= 25: title_name = "Veteran"
                elif level >= 10: title_name = "Regular"
                else: title_name = "Newcomer"

                current_title = dm.get_guild_data(guild.id, f"ranking_title_{member.id}")
                if title_name != current_title:
                    dm.update_guild_data(guild.id, f"ranking_title_{member.id}", title_name)
                    # Optionally assign role
                    role = discord.utils.get(guild.roles, name=title_name)
                    if role:
                        try: await member.add_roles(role)
                        except Exception as e: logger.error(f"Failed to add role {title_name}: {e}")

    async def prestige(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        xp = self.bot.leveling.get_xp(guild_id, user_id)
        level = self.bot.leveling.get_level_from_xp(xp)

        prestige_config = dm.get_guild_data(guild_id, "gamification_config", {}).get("prestige_level", 100)

        if level < prestige_config:
            return await interaction.response.send_message(f"You must reach level {prestige_config} to prestige!", ephemeral=True)

        # Reset XP/Level
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        xp_data[str(user_id)] = 0
        dm.update_guild_data(guild_id, "leveling_xp", xp_data)

        # Increase Prestige Level
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        current_prestige = user_data.get("prestige", 0)
        user_data["prestige"] = current_prestige + 1
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)

        await interaction.response.send_message(f"🔱 **PRESTIGE!** You have reset to level 1 and reached Prestige **{current_prestige + 1}**!")

    async def mini_game_dice(self, interaction: discord.Interaction, bet: int):
        if bet <= 0: return await interaction.response.send_message("Bet must be positive!", ephemeral=True)
        coins = self.bot.economy.get_coins(interaction.guild.id, interaction.user.id)
        if coins < bet: return await interaction.response.send_message("Insufficient coins!", ephemeral=True)
        user_roll, bot_roll = random.randint(1, 6), random.randint(1, 6)
        if user_roll > bot_roll:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, bet)
            result = f"You rolled {user_roll}, I rolled {bot_roll}. **You win {bet} coins!**"
        elif bot_roll > user_roll:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, -bet)
            result = f"You rolled {user_roll}, I rolled {bot_roll}. **You lost {bet} coins.**"
        else: result = f"Both rolled {user_roll}. **It's a draw!**"
        await interaction.response.send_message(f"🎲 {result}")

    async def mini_game_flip(self, interaction: discord.Interaction, side: str, bet: int):
        if bet <= 0: return await interaction.response.send_message("Bet must be positive!", ephemeral=True)
        coins = self.bot.economy.get_coins(interaction.guild.id, interaction.user.id)
        if coins < bet: return await interaction.response.send_message("Insufficient coins!", ephemeral=True)
        result_side = random.choice(["heads", "tails"])
        if side.lower() == result_side:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, bet)
            res_text = f"It was **{result_side}**! **You win {bet} coins!**"
        else:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, -bet)
            res_text = f"It was **{result_side}**... **You lost {bet} coins.**"
        await interaction.response.send_message(f"🪙 {res_text}")

    async def mini_game_slots(self, interaction: discord.Interaction, bet: int):
        if bet <= 0: return await interaction.response.send_message("Bet must be positive!", ephemeral=True)
        coins = self.bot.economy.get_coins(interaction.guild.id, interaction.user.id)
        if coins < bet: return await interaction.response.send_message("Insufficient coins!", ephemeral=True)
        emojis = ["🍒", "🍋", "🍇", "🍊", "🍎", "💎", "7️⃣"]
        res = [random.choice(emojis) for _ in range(3)]
        slot_str = " | ".join(res)
        if res[0] == res[1] == res[2]:
            mult = 10 if res[0] == "7️⃣" else 5
            win = bet * mult
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, win)
            res_text = f"**JACKPOT!** {slot_str}\n**You win {win} coins!**"
        elif res[0] == res[1] or res[1] == res[2] or res[0] == res[2]:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, bet)
            res_text = f"**Match!** {slot_str}\n**You win {bet} coins!**"
        else:
            self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, -bet)
            res_text = f"{slot_str}\n**You lost {bet} coins.**"
        await interaction.response.send_message(f"🎰 {res_text}")

    async def mini_game_trivia(self, interaction: discord.Interaction):
        # Sample trivia questions
        questions = [
            {"q": "What is the capital of France?", "a": "Paris"},
            {"q": "Who wrote 'Romeo and Juliet'?", "a": "Shakespeare"},
            {"q": "What is the largest planet in our solar system?", "a": "Jupiter"}
        ]
        q_data = random.choice(questions)
        await interaction.response.send_message(f"❓ **Trivia:** {q_data['q']}\n(Reply with the answer in 15s)")
        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=15.0)
            if msg.content.lower() == q_data['a'].lower():
                self.bot.economy.add_coins(interaction.guild.id, interaction.user.id, 50)
                await interaction.channel.send(f"✅ Correct! **+50 coins**")
            else:
                await interaction.channel.send(f"❌ Wrong! The answer was **{q_data['a']}**.")
        except asyncio.TimeoutError:
            await interaction.channel.send(f"⏰ Time's up! The answer was **{q_data['a']}**.")

    async def _notify_quest_complete(self, quest: Quest):
        member = self.bot.get_guild(quest.guild_id).get_member(quest.user_id)
        if not member:
            return
        
        embed = discord.Embed(
            title="✅ Quest Completed!",
            description=f"**{quest.title}** - {quest.description}",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Rewards",
            value=f"💰 {quest.rewards.get('coins', 0)} coins, ✨ {quest.rewards.get('xp', 0)} XP",
            inline=False
        )
        
        try:
            await member.send(embed=embed)
        except:
            pass

    async def claim_quest_reward(self, guild_id: int, user_id: int, quest_id: str) -> bool:
        if quest_id not in self._active_quests:
            return False
        
        quest = self._active_quests[quest_id]
        
        if quest.user_id != user_id or quest.guild_id != guild_id:
            return False
        
        if quest.status != QuestStatus.COMPLETED:
            return False
        
        self.bot.economy.add_coins(guild_id, user_id, quest.rewards.get("coins", 0))
        self.bot.leveling.add_xp(guild_id, user_id, quest.rewards.get("xp", 0))

        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        user_data["quests_completed"] = user_data.get("quests_completed", 0) + 1
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)
        
        quest.status = QuestStatus.CLAIMED
        self._save_quests(quest)
        
        return True

    def get_user_quests(self, guild_id: int, user_id: int) -> List[dict]:
        user_quests = []
        for quest in self._active_quests.values():
            if quest.guild_id == guild_id and quest.user_id == user_id and quest.status in [QuestStatus.ACTIVE, QuestStatus.COMPLETED]:
                user_quests.append({
                    "id": quest.id, "title": quest.title, "description": quest.description,
                    "type": quest.quest_type.value, "progress": quest.progress,
                    "requirements": quest.requirements, "rewards": quest.rewards,
                    "status": quest.status.value, "expires_at": quest.expires_at
                })
        return user_quests

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["quests"] = json.dumps({"command_type": "list_quests"})
        custom_cmds["quest"] = json.dumps({"command_type": "list_quests"})
        custom_cmds["prestige"] = json.dumps({"command_type": "prestige"})
        custom_cmds["dice"] = json.dumps({"command_type": "dice"})
        custom_cmds["flip"] = json.dumps({"command_type": "flip"})
        custom_cmds["slots"] = json.dumps({"command_type": "slots"})
        custom_cmds["trivia"] = json.dumps({"command_type": "trivia"})

        custom_cmds["gamificationpanel"] = "configpanel gamification"

        custom_cmds["help gamification"] = json.dumps({
            "command_type": "help_embed",
            "title": "Gamification System Help",
            "description": "Earn rewards through games and challenges.",
            "fields": [
                {"name": "!quests", "value": "List available quests.", "inline": False},
                {"name": "!prestige", "value": "Prestige system.", "inline": False},
                {"name": "!dice", "value": "Dice game.", "inline": False},
                {"name": "!flip", "value": "Coin flip game.", "inline": False},
                {"name": "!slots", "value": "Slot machine game.", "inline": False},
                {"name": "!trivia", "value": "Trivia game.", "inline": False},
                {"name": "!help gamification", "value": "Show this help message.", "inline": False}
            ]
        })

        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("Gamification system set up! Try `!quests`, `!dice`, or `!flip <bet>`.", ephemeral=True)
        return True


Gamification = AdaptiveGamification
