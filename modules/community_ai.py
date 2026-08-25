"""Community Ai systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: ai_chat.py, intelligence.py, community_health.py, conflict_resolution.py, server_analytics.py, content_generator.py, embed_system.py
"""



# ======================================================================
# From: modules/ai_chat.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from data_manager import dm
from logger import logger
from history_manager import history_manager
from vector_memory import vector_memory


class ChannelMode(Enum):
    GENERAL = "general"
    HELP = "help"
    RPG = "rpg"
    COUNSELOR = "counselor"
    TRANSLATOR = "translator"
    CUSTOM = "custom"
    CODING = "coding"
    CREATIVE = "creative"
    GAMING = "gaming"


class AIProvider(Enum):
    DEFAULT = "default"
    CLAUDE = "claude"
    GPT4 = "gpt4"
    DEEPSEEK = "deepseek"
    LOCAL = "local"


@dataclass
class AIChatChannel:
    id: str
    guild_id: int
    channel_id: int
    mode: ChannelMode
    persona: str
    system_prompt: str
    memory_depth: int
    translate_languages: List[str]
    custom_settings: dict
    created_at: float
    created_by: int


class AIChatSystem:
    def __init__(self, bot):
        self.bot = bot
        self._chat_channels: Dict[str, AIChatChannel] = {}
        self._channel_sessions: Dict[int, dict] = {}
        self._load_channels()

    def _load_channels(self):
        data = dm.load_json("ai_chat_channels", default={})
        
        for channel_id, c_data in data.items():
            try:
                channel = AIChatChannel(
                    id=channel_id,
                    guild_id=c_data["guild_id"],
                    channel_id=c_data["channel_id"],
                    mode=ChannelMode(c_data["mode"]),
                    persona=c_data.get("persona", ""),
                    system_prompt=c_data.get("system_prompt", ""),
                    memory_depth=c_data.get("memory_depth", 50),
                    translate_languages=c_data.get("translate_languages", []),
                    custom_settings=c_data.get("custom_settings", {}),
                    created_at=c_data["created_at"],
                    created_by=c_data["created_by"]
                )
                self._chat_channels[channel_id] = channel
            except Exception as e:
                logger.error(f"Failed to load AI chat channel {channel_id}: {e}")

    def _save_channel(self, channel: AIChatChannel):
        data = dm.load_json("ai_chat_channels", default={})
        data[channel.id] = {
            "guild_id": channel.guild_id,
            "channel_id": channel.channel_id,
            "mode": channel.mode.value,
            "persona": channel.persona,
            "system_prompt": channel.system_prompt,
            "memory_depth": channel.memory_depth,
            "translate_languages": channel.translate_languages,
            "custom_settings": channel.custom_settings,
            "created_at": channel.created_at,
            "created_by": channel.created_by
        }
        dm.save_json("ai_chat_channels", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "ai_chat_settings", {
            "enabled": True,
            "default_memory_depth": 50,
            "max_channels": 5,
            "allowed_modes": ["general", "help", "rpg", "counselor", "translator"]
        })

    def _get_default_persona(self, mode: ChannelMode) -> tuple:
        personas = {
            ChannelMode.GENERAL: (
                "Friendly AI Assistant",
                "You are a friendly, helpful AI assistant in a Discord server. Be conversational, helpful, and engaging. Keep responses concise but informative."
            ),
            ChannelMode.HELP: (
                "Tech Support",
                "You are a technical support AI. Help users with their problems, ask clarifying questions, and provide step-by-step solutions. Be patient and thorough."
            ),
            ChannelMode.RPG: (
                "Fantasy Narrator",
                "You are a fantasy RPG AI narrator. Create immersive story experiences. Respond to player actions, describe scenes, and drive the narrative forward. Be creative and descriptive."
            ),
            ChannelMode.COUNSELOR: (
                "Supportive Counselor",
                "You are a supportive, empathetic counselor AI. Listen attentively, validate feelings, and provide gentle guidance. Never give medical advice. Be warm and understanding."
            ),
            ChannelMode.TRANSLATOR: (
                "Language Translator",
                "You are a multilingual translator. Translate messages between languages accurately. Detect the source language and respond in the requested language."
            ),
            ChannelMode.CUSTOM: (
                "Custom AI",
                "You are a helpful AI assistant. Be friendly and engage in conversation."
            )
        }

        persona_name, base_prompt = personas.get(mode, personas[ChannelMode.GENERAL])

        # Add Discord Automation AI framework instructions
        framework_instructions = """

IMPORTANT: You are integrated with a Discord Automation AI framework. All your responses MUST be valid JSON objects with exactly these keys:
- "reasoning": string under 500 characters explaining your thought process
- "summary": string under 200 characters for user-visible response
- "actions": array (max 5 actions) where each action is an object with "name" and "parameters"

Available actions include: send_message, send_embed, add_role, remove_role, assign_role, create_channel, delete_channel, create_role, delete_role, kick_user, ban_user, timeout_user, mute_user, unmute_user, set_nickname, send_dm, etc.

For assign_role: Perform pre-flight checks (bot's highest role > target role, not managed, has MANAGE_ROLES permission). If checks fail, fail silently and note in summary.

If more than 5 actions needed, execute first 5 and note in summary to continue.

Ensure no trailing commas, comments, or text outside JSON. Lines under 1500 characters to prevent crashes.
"""

        enhanced_prompt = base_prompt + framework_instructions

        return persona_name, enhanced_prompt

    async def create_chat_channel(self, guild_id: int, channel_id: int, mode: ChannelMode,
                                custom_persona: str = None, custom_prompt: str = None,
                                created_by: int = 0) -> AIChatChannel:
        channel_id_str = str(channel_id)
        
        if channel_id_str in self._chat_channels:
            return self._chat_channels[channel_id_str]
        
        persona, system_prompt = self._get_default_persona(mode)
        
        if custom_persona:
            persona = custom_persona
        if custom_prompt:
            system_prompt = custom_prompt
        
        settings = self.get_guild_settings(guild_id)
        
        chat_channel = AIChatChannel(
            id=channel_id_str,
            guild_id=guild_id,
            channel_id=channel_id,
            mode=mode,
            persona=persona,
            system_prompt=system_prompt,
            memory_depth=settings.get("default_memory_depth", 50),
            translate_languages=[],
            custom_settings={},
            created_at=time.time(),
            created_by=created_by
        )
        
        self._chat_channels[channel_id_str] = chat_channel
        self._save_channel(chat_channel)
        
        return chat_channel

    async def handle_message(self, message: discord.Message) -> Optional[discord.Message]:
        if message.author.bot:
            return None
        
        channel_id_str = str(message.channel.id)
        
        if channel_id_str not in self._chat_channels:
            return None
        
        chat_channel = self._chat_channels[channel_id_str]
        
        if chat_channel.mode == ChannelMode.TRANSLATOR:
            return await self._handle_translator_mode(message, chat_channel)
        
        return await self._handle_ai_chat(message, chat_channel)

    async def _handle_ai_chat(self, message: discord.Message, chat_channel: AIChatChannel) -> Optional[discord.Message]:
        user_input = message.content
        
        session_key = f"{message.guild.id}_{message.author.id}_{message.channel.id}"
        
        if session_key not in self._channel_sessions:
            self._channel_sessions[session_key] = {
                "messages": [],
                "started_at": time.time()
            }
        
        session = self._channel_sessions[session_key]

        # FIX: was missing `await` — coroutine was never resolved, history never used
        try:
            history = await history_manager.get_enhanced_context(
                message.guild.id,
                message.author.id,
                depth=chat_channel.memory_depth
            )
        except Exception:
            history = []
        
        if chat_channel.mode == ChannelMode.RPG:
            rpg_context = await self._get_rpg_context(message.guild.id)
            system_prompt = chat_channel.system_prompt + "\n\n" + rpg_context
        else:
            system_prompt = chat_channel.system_prompt
        
        try:
            # Check for AI provider override from channel settings
            provider = getattr(chat_channel, 'ai_provider', None)

            if provider and provider != AIProvider.DEFAULT:
                # Use different AI for this channel
                result = await self._chat_with_provider(
                    message.guild.id,
                    message.author.id,
                    user_input,
                    system_prompt,
                    provider
                )
            else:
                result = await self.bot.ai.chat(
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    user_input=user_input,
                    persist=True,  # keep channel conversations in per-user history
                    system_prompt=system_prompt
                )

            # Parse JSON response with strict validation
            if not isinstance(result, dict):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    return await message.channel.send("Invalid AI response format. Please try again.", suppress_embeds=True)

            # Validate response structure
            if not self._validate_framework_response(result):
                return await message.channel.send("AI response validation failed. Please try again.", suppress_embeds=True)

            # Extract components
            reasoning = result.get("reasoning", "")
            summary = result.get("summary", "I didn't quite catch that. Could you try again?")
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            # Native provider turns carry calls under `tool_calls`; legacy
            # JSON plans under `actions`. Both must execute.
            actions = result.get("tool_calls") or result.get("actions", [])

            # Execute via the Agent Runtime — the summary is regenerated from
            # actual execution results, never sent as-is after failures.
            if actions:
                try:
                    final, exec_result = await self._execute_actions(message, result)
                    if exec_result.failures:
                        # Plan item 15: never send the pre-execution summary
                        summary = (final.text if final and final.text
                                   else "⚠️ Some actions could not be completed:\n"
                                        + "\n".join(exec_result.failures[:5]))
                    elif final and final.text:
                        summary = final.text
                except Exception as e:
                    logger.error(f"Agent execution failed: {e}")
                    summary = "⚠️ I couldn't complete that operation. The error was logged."
            # Store conversation
            session["messages"].append({"role": "user", "content": user_input})
            session["messages"].append({"role": "assistant", "content": summary})

            if len(session["messages"]) > 50:
                session["messages"] = session["messages"][-50:]

            # FIX: was missing `await` — coroutine never executed, vector memory never written
            try:
                await vector_memory.store_conversation(
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    user_message=user_input,
                    bot_response=summary,
                    reasoning=reasoning,
                    walkthrough=result.get("walkthrough", ""),
                    importance_score=0.5
                )
            except Exception as ve:
                logger.debug(f"vector store failed: {ve}")

            if len(summary) > 2000:
                summary = summary[:1997] + "..."

            return await message.channel.send(summary, suppress_embeds=True)
            
        except Exception as e:
            logger.error(f"AI chat error: {e}")
            return await message.channel.send("Sorry, I encountered an error. Please try again.", suppress_embeds=True)

    async def _handle_translator_mode(self, message: discord.Message, chat_channel: AIChatChannel) -> Optional[discord.Message]:
        user_input = message.content
        
        prompt = f"""Translate this message. Detect the source language and translate to all configured languages.

AVAILABLE LANGUAGES: {', '.join(chat_channel.translate_languages)}

MESSAGE TO TRANSLATE: {user_input}

Respond with JSON only:
{{
    "detected_language": "language name",
    "translations": {{
        "language1": "translated text",
        "language2": "translated text"
    }}
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_input=prompt,
                system_prompt="You are a multilingual translator. Translate accurately and preserve meaning."
            )
            
            translations = result.get("translations", {})
            detected = result.get("detected_language", "Unknown")
            
            embed = discord.Embed(
                title="🌐 Translation",
                description=f"Detected: **{detected}**",
                color=discord.Color.blue()
            )
            
            for lang, text in translations.items():
                embed.add_field(name=lang.title(), value=text, inline=False)
            
            return await message.channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return await message.channel.send("Sorry, translation failed. Please try again.", suppress_embeds=True)

    async def _get_rpg_context(self, guild_id: int) -> str:
        rpg_data = dm.get_guild_data(guild_id, "rpg_data", {})

        if not rpg_data:
            return "This is a new adventure. The world is waiting to be explored."

        context = "RECENT ADVENTURE:\n"

        for key, value in rpg_data.items():
            if key.startswith("story_"):
                context += f"- {value[:200]}\n"

        return context

    def _validate_framework_response(self, response: dict) -> bool:
        """Validate the AI response conforms to Discord Automation AI framework constraints."""
        if not isinstance(response, dict):
            return False

        # Check required keys
        required_keys = {"reasoning", "summary", "actions"}
        if not all(key in response for key in required_keys):
            return False

        # Validate reasoning
        reasoning = response.get("reasoning", "")
        if not isinstance(reasoning, str) or len(reasoning) > 500:
            return False

        # Validate summary
        summary = response.get("summary", "")
        if not isinstance(summary, str) or len(summary) > 200:
            return False

        # Validate actions
        actions = response.get("actions", [])
        if not isinstance(actions, list) or len(actions) > 3:
            return False

        for action in actions:
            if not isinstance(action, dict):
                return False
            if "name" not in action or "parameters" not in action:
                return False
            if not isinstance(action["name"], str) or not isinstance(action["parameters"], dict):
                return False

        return True

    async def _execute_actions(self, message: discord.Message, result: dict):
        """Run the model's action plan through the Agent Runtime with the
        speaker's real permissions. Returns (FinalAIResponse, AgentExecutionResult)."""
        from core.agent_runtime import AgentRuntime
        from agent.executor import Executor

        actions = [a for a in (result.get("actions") or []) if isinstance(a, dict)]
        allow_dangerous = bool(getattr(message.author, "guild_permissions", None)
                               and message.author.guild_permissions.administrator)
        runtime = AgentRuntime(self.bot, message.guild, message.author,
                               allow_dangerous=allow_dangerous)
        # Speaker-context interaction: dispatch's admin gate applies to the
        # actual human who typed the message, never the bot identity.
        interaction = Executor.build_message_interaction(message)
        return await runtime.run(
            interaction,
            str(message.content)[:2000],
            "You are Miro Agent executing an operation requested in an AI channel.",
            initial_result={"summary": str(result.get("summary") or ""),
                            "actions": actions},
        )

    async def _legacy_execute_actions_unused(self):
        pass

    async def _chat_with_provider(self, guild_id: int, user_id: int, user_input: str,
                                system_prompt: str, provider: AIProvider) -> dict:
        """Multi-AI Provider System - Chat with a specific AI provider."""
        try:
            return await self.bot.ai.chat(guild_id, user_id, user_input, system_prompt, persist=True)
        except Exception as e:
            logger.error(f"AI provider error: {e}")
            return {"summary": "Sorry, AI service temporarily unavailable."}
    
    async def _handle_web_search(self, user_input: str, system_prompt: str,
                                 guild_id: int = 0, user_id: int = 0) -> str:
        """Web Search System - Search the web and include results in AI response."""
        try:
            search_results = await self.bot.ai.get_search_results(user_input)

            if not search_results or "disabled" in search_results.lower() or "error" in search_results.lower():
                return None

            enhanced_prompt = f"{system_prompt}\n\nWEB SEARCH RESULTS:\n{search_results}\n\nBased on these results, answer the user's question."

            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=user_id,
                user_input=user_input,
                system_prompt=enhanced_prompt
            )
            
            return result.get("summary")
        except Exception as e:
            logger.error(f"Web search error: {e}")
            return None
    
    """AI Command Execution"""
    async def _check_for_commands(self, message: discord.Message, response: str) -> Optional[str]:
        """Check if AI wants to execute a command."""
        if not response.startswith("!") and not response.startswith("/"):
            return None
        
        # Sanitize command
        cmd = response.strip().split()[0]
        allowed_cmds = {"ping", "server", "userinfo", "avatar", "botinfo"}
        
        # Check if allowed
        if cmd.lstrip("!/") in allowed_cmds:
            return response
        
        return None
    
    async def update_channel_settings(self, channel_id: str, **kwargs):
        if channel_id not in self._chat_channels:
            return
        
        chat_channel = self._chat_channels[channel_id]
        
        for key, value in kwargs.items():
            if hasattr(chat_channel, key):
                setattr(chat_channel, key, value)
        
        self._save_channel(chat_channel)

    def get_channel_info(self, channel_id: str) -> Optional[dict]:
        if channel_id not in self._chat_channels:
            return None
        
        chat_channel = self._chat_channels[channel_id]
        
        return {
            "id": chat_channel.id,
            "mode": chat_channel.mode.value,
            "persona": chat_channel.persona,
            "memory_depth": chat_channel.memory_depth,
            "translate_languages": chat_channel.translate_languages
        }

    def list_guild_channels(self, guild_id: int) -> List[AIChatChannel]:
        return [ch for ch in self._chat_channels.values() if ch.guild_id == guild_id]

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "ai_chat_settings", settings)
        
        help_embed = discord.Embed(
            title="💬 AI Chat Channels",
            description="Dedicated AI conversation channels with personas and channel-specific memories.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Create AI-powered text channels. Each channel can have its own persona (friendly, help, RPG, counselor, translator). The AI remembers conversations in that channel.",
            inline=False
        )
        help_embed.add_field(
            name="Channel Modes",
            value="• **general** - Friendly conversational AI\n• **help** - Technical support\n• **rpg** - Fantasy storytelling\n• **counselor** - Supportive listener\n• **translator** - Multi-language",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["help aichat"] = json.dumps({
            "command_type": "help_embed",
            "title": "💬 AI Chat Channels",
            "description": "Dedicated AI conversation channels.",
            "fields": [
                {"name": "How it works", "value": "Each channel has its own AI persona with channel-specific memory.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands



# ======================================================================
# From: modules/intelligence.py
# ======================================================================

import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict

from data_manager import dm
from logger import logger


@dataclass
class UserActivity:
    user_id: int
    messages_sent: int
    voice_time: int
    commands_used: int
    last_active: float
    joined_at: float
    interaction_scores: List[float]


@dataclass
class ServerMetrics:
    guild_id: int
    total_members: int
    active_members: int
    messages_today: int
    commands_today: int
    new_members_today: int
    left_members_today: int
    avg_response_time: float
    ai_interactions: int
    engagement_score: float


class ServerIntelligence:
    def __init__(self, bot):
        self.bot = bot
        self._activity_data: Dict[int, Dict[int, UserActivity]] = {}
        self._topic_trends: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        saved_data = dm.load_json("server_intelligence", default={})
        if not isinstance(saved_data, dict):
            return

        for guild_id_str, guild_data in saved_data.items():
            try:
                guild_id = int(guild_id_str)
                self._activity_data[guild_id] = {}

                users = guild_data.get("users", {}) if isinstance(guild_data, dict) else {}
                for user_id_str, user_data in users.items():
                    if not isinstance(user_data, dict):
                        continue  # skip corrupted/legacy entries
                    self._activity_data[guild_id][int(user_id_str)] = UserActivity(
                        user_id=int(user_id_str),
                        messages_sent=int(user_data.get("messages_sent", 0) or 0),
                        voice_time=float(user_data.get("voice_time", 0) or 0),
                        commands_used=int(user_data.get("commands_used", 0) or 0),
                        last_active=float(user_data.get("last_active", 0) or 0),
                        joined_at=user_data.get("joined_at", time.time()),
                        interaction_scores=user_data.get("interaction_scores", [])
                        if isinstance(user_data.get("interaction_scores"), list) else []
                    )
            except Exception as e:
                logger.error(f"Failed to load intelligence data for guild {guild_id_str}: {e}")

    def _save_data(self):
        data = {}
        
        for guild_id, users in self._activity_data.items():
            data[str(guild_id)] = {
                "users": {}
            }
            
            for user_id, activity in users.items():
                data[str(guild_id)]["users"][str(user_id)] = {
                    "messages_sent": activity.messages_sent,
                    "voice_time": activity.voice_time,
                    "commands_used": activity.commands_used,
                    "last_active": activity.last_active,
                    "joined_at": activity.joined_at,
                    "interaction_scores": activity.interaction_scores
                }
        
        dm.save_json("server_intelligence", data)

    def start_monitoring(self):
        asyncio.create_task(self._intelligence_monitor_loop())

    async def _intelligence_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                await self._analyze_server_health()
                await self._detect_topic_trends()
                await self._identify_at_risk_members()
            except Exception as e:
                logger.error(f"Intelligence monitor error: {e}")
            
            await asyncio.sleep(300)

    async def _analyze_server_health(self):
        for guild in self.bot.guilds:
            metrics = await self.get_server_metrics(guild.id)
            
            health_data = dm.get_guild_data(guild.id, "server_health", {})
            health_data["last_check"] = time.time()
            health_data["engagement_score"] = metrics.engagement_score
            health_data["active_members"] = metrics.active_members
            health_data["messages_today"] = metrics.messages_today
            
            dm.update_guild_data(guild.id, "server_health", health_data)

    async def _detect_topic_trends(self):
        for guild in self.bot.guilds:
            command_usage = dm.get_guild_data(guild.id, "command_usage", {})
            
            recent_commands = []
            for cmd, data in command_usage.items():
                if not isinstance(data, dict):
                    continue  # counters or legacy ints are not usable here
                last_used = data.get("last_used", 0)
                if time.time() - last_used < 86400:
                    recent_commands.append({"command": cmd, "uses": data.get("count", 0), "last_used": last_used})
            
            self._topic_trends[guild.id] = recent_commands

    async def _identify_at_risk_members(self):
        for guild in self.bot.guilds:
            if guild.id not in self._activity_data:
                continue
            
            at_risk = []
            cutoff = time.time() - (7 * 24 * 60 * 60)
            
            for user_id, activity in self._activity_data[guild.id].items():
                if activity.last_active < cutoff:
                    member = guild.get_member(user_id)
                    if member:
                        days_inactive = int((time.time() - activity.last_active) / 86400)
                        at_risk.append({"user_id": user_id, "days_inactive": days_inactive, "join_date": member.joined_at})
            
            risk_data = dm.get_guild_data(guild.id, "at_risk_members", {})
            if not isinstance(risk_data, dict):
                risk_data = {}
            risk_data["members"] = at_risk
            risk_data["last_updated"] = time.time()
            dm.update_guild_data(guild.id, "at_risk_members", risk_data)

    async def get_server_metrics(self, guild_id: int) -> ServerMetrics:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return ServerMetrics(guild_id, 0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        
        total_members = guild.member_count
        active_members = 0
        
        if guild_id in self._activity_data:
            cutoff = time.time() - (24 * 60 * 60)
            active_members = sum(1 for a in self._activity_data[guild_id].values() if a.last_active > cutoff)
        
        messages_today = 0
        commands_today = 0
        
        command_usage = dm.get_guild_data(guild_id, "command_usage", {})
        for cmd, data in command_usage.items():
            if not isinstance(data, dict):
                continue
            last_used = data.get("last_used", 0)
            if time.time() - last_used < 86400:
                commands_today += data.get("count", 0)

        health_data = dm.get_guild_data(guild_id, "server_health", {})
        messages_today = health_data.get("messages_today", 0) if isinstance(health_data, dict) else 0
        
        new_members = len([m for m in guild.members if m.joined_at and (discord.utils.utcnow() - m.joined_at).days == 0])
        
        engagement_score = self._calculate_engagement_score(total_members, active_members, messages_today, commands_today)
        
        return ServerMetrics(
            guild_id=guild_id,
            total_members=total_members,
            active_members=active_members,
            messages_today=messages_today,
            commands_today=commands_today,
            new_members_today=new_members,
            left_members_today=0,
            avg_response_time=0.0,
            ai_interactions=0,
            engagement_score=engagement_score
        )

    def _calculate_engagement_score(self, total_members: int, active_members: int, 
                                    messages: int, commands: int) -> float:
        if total_members == 0:
            return 0.0
        
        activity_rate = active_members / total_members
        message_rate = messages / max(active_members, 1)
        command_rate = commands / max(active_members, 1)
        
        score = (activity_rate * 40) + (min(message_rate / 10, 1) * 30) + (min(command_rate / 5, 1) * 30)
        
        return min(100.0, score)

    async def track_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        guild_id = message.guild.id
        user_id = message.author.id
        
        if guild_id not in self._activity_data:
            self._activity_data[guild_id] = {}
        
        if user_id not in self._activity_data[guild_id]:
            self._activity_data[guild_id][user_id] = UserActivity(
                user_id=user_id,
                messages_sent=0,
                voice_time=0,
                commands_used=0,
                last_active=time.time(),
                joined_at=message.author.joined_at.timestamp() if message.author.joined_at else time.time(),
                interaction_scores=[]
            )
        
        activity = self._activity_data[guild_id][user_id]
        activity.messages_sent += 1
        activity.last_active = time.time()
        
        if time.time() % 60 == 0:
            self._save_data()

    def get_topic_trends(self, guild_id: int) -> List[dict]:
        return self._topic_trends.get(guild_id, [])

    def get_at_risk_members(self, guild_id: int) -> List[dict]:
        risk_data = dm.get_guild_data(guild_id, "at_risk_members", {})
        return risk_data.get("members", [])

    async def generate_health_report(self, guild_id: int) -> discord.Embed:
        metrics = await self.get_server_metrics(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        embed = discord.Embed(
            title=f"📊 Server Intelligence Report: {guild.name}",
            color=discord.Color.blue()
        )
        
        health_emoji = "🟢" if metrics.engagement_score >= 70 else "🟡" if metrics.engagement_score >= 40 else "🔴"
        
        embed.add_field(
            name=f"{health_emoji} Engagement Score",
            value=f"**{metrics.engagement_score:.1f}**/100",
            inline=True
        )
        embed.add_field(
            name="👥 Members",
            value=f"{metrics.active_members}/{metrics.total_members} active",
            inline=True
        )
        embed.add_field(
            name="💬 Messages (24h)",
            value=str(metrics.messages_today),
            inline=True
        )
        embed.add_field(
            name="🤖 Commands (24h)",
            value=str(metrics.commands_today),
            inline=True
        )
        
        at_risk = self.get_at_risk_members(guild_id)
        if at_risk:
            at_risk_text = "\n".join([f"<@{m['user_id']}> - {m['days_inactive']}d inactive" for m in at_risk[:5]])
            embed.add_field(
                name="⚠️ At Risk Members",
                value=at_risk_text,
                inline=False
            )
        
        trends = self.get_topic_trends(guild_id)
        if trends:
            top_commands = "\n".join([f"`!{t['command']}` ({t['uses']} uses)" for t in sorted(trends, key=lambda x: x["uses"], reverse=True)[:5]])
            embed.add_field(
                name="📈 Trending Commands",
                value=top_commands,
                inline=False
            )
        
        embed.timestamp = datetime.now()
        
        return embed

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        help_embed = discord.Embed(
            title="🔍 Server Intelligence Dashboard",
            description="Real-time analytics and insights about your server.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Tracks member activity, engagement scores, topic trends, and identifies at-risk members for retention.",
            inline=False
        )
        help_embed.add_field(
            name="!serverstats",
            value="View complete server health report.",
            inline=False
        )
        help_embed.add_field(
            name="!mystats",
            value="View your personal activity stats.",
            inline=False
        )
        help_embed.add_field(
            name="!atrisk",
            value="List members at risk of leaving.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["serverstats"] = json.dumps({
            "command_type": "server_stats"
        })
        custom_cmds["mystats"] = json.dumps({
            "command_type": "my_stats"
        })
        custom_cmds["atrisk"] = json.dumps({
            "command_type": "at_risk"
        })
        custom_cmds["help intelligence"] = json.dumps({
            "command_type": "help_embed",
            "title": "🔍 Server Intelligence Dashboard",
            "description": "Real-time analytics and insights.",
            "fields": [
                {"name": "!serverstats", "value": "View complete server health report.", "inline": False},
                {"name": "!mystats", "value": "View your personal activity stats.", "inline": False},
                {"name": "!atrisk", "value": "List members at risk of leaving.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands



# ======================================================================
# From: modules/community_health.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


@dataclass
class Interaction:
    from_user: int
    to_user: int
    weight: float
    timestamp: float
    channel_id: int


@dataclass
class RelationshipCluster:
    name: str
    members: List[int]
    avg_interaction_strength: float
    topics: List[str]


@dataclass
class MemberHealth:
    user_id: int
    inbound_interactions: int
    outbound_interactions: int
    mutual_connections: List[int]
    isolation_score: float
    influence_score: float
    activity_trend: str
    days_inactive: int


class CommunityHealth:
    def __init__(self, bot):
        self.bot = bot
        self._interaction_graph: Dict[int, Dict[int, Dict[int, float]]] = {}
        self._last_analysis: Dict[int, float] = {}
        self._analysis_interval = 3600 * 24
        self._pending_reports: Dict[int, asyncio.Task] = {}
        self._guild_configs: Dict[int, dict] = {}
        self._member_cache: Dict[int, Dict[int, MemberHealth]] = {}

    def get_config(self, guild_id: int) -> dict:
        if guild_id in self._guild_configs:
            return self._guild_configs[guild_id]
        
        config = dm.get_guild_data(guild_id, "community_health_config", {
            "enabled": True,
            "analysis_interval_hours": 24,
            "health_reports_enabled": True,
            "isolation_alerts": True,
            "bridge_events_enabled": True,
            "mentorship_enabled": True,
            "min_interactions_for_analysis": 10,
            "isolation_threshold": 0.3,
            "report_channel": None,
            "excluded_roles": []
        })
        
        self._guild_configs[guild_id] = config
        return config

    def update_config(self, guild_id: int, key: str, value):
        config = self.get_config(guild_id)
        config[key] = value
        self._guild_configs[guild_id] = config
        dm.update_guild_data(guild_id, "community_health_config", config)

    def _ensure_guild(self, guild_id: int):
        if guild_id not in self._interaction_graph:
            self._interaction_graph[guild_id] = {}
        if guild_id not in self._member_cache:
            self._member_cache[guild_id] = {}

    async def analyze_interaction(self, message: discord.Message) -> bool:
        if message.author.bot:
            return False
        
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        
        if not config.get("enabled", True):
            return False
        
        if message.author.guild_permissions.administrator:
            for role in message.author.roles:
                if role.id in config.get("excluded_roles", []):
                    return False
        
        self._ensure_guild(guild_id)
        author_id = message.author.id
        
        if author_id not in self._interaction_graph[guild_id]:
            self._interaction_graph[guild_id][author_id] = {}
        
        mentions = self._extract_mentions(message.content)
        for mentioned_id in mentions:
            if mentioned_id != author_id and not self.bot.get_user(mentioned_id).bot:
                self._update_interaction(guild_id, author_id, mentioned_id, 0.3, message.channel.id)
        
        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg.author.id != author_id:
                    self._update_interaction(guild_id, author_id, ref_msg.author.id, 0.5, message.channel.id)
            except:
                pass
        
        await self._trigger_periodic_analysis(guild_id)
        
        return True

    def _extract_mentions(self, content: str) -> List[int]:
        import re
        mention_pattern = r'<@!?(\d+)>'
        mentions = re.findall(mention_pattern, content)
        return [int(m) for m in mentions]

    def _update_interaction(self, guild_id: int, from_user: int, to_user: int, weight: float, channel_id: int = 0):
        self._ensure_guild(guild_id)
        
        if from_user not in self._interaction_graph[guild_id]:
            self._interaction_graph[guild_id][from_user] = {}
        
        current_weight = self._interaction_graph[guild_id][from_user].get(to_user, 0)
        decay_factor = 0.95
        new_weight = min(1.0, (current_weight * decay_factor) + weight)
        
        self._interaction_graph[guild_id][from_user][to_user] = new_weight

    async def _trigger_periodic_analysis(self, guild_id: int):
        now = time.time()
        last = self._last_analysis.get(guild_id, 0)
        
        interval = self.get_config(guild_id).get("analysis_interval_hours", 24) * 3600
        
        if now - last >= interval:
            self._last_analysis[guild_id] = now
            try:
                await self.generate_health_report(guild_id)
            except Exception as e:
                logger.error(f"Error generating health report for guild {guild_id}: {e}")

    async def generate_health_report(self, guild_id: int) -> dict:
        config = self.get_config(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            return {"error": "Guild not found"}
        
        self._ensure_guild(guild_id)
        
        interaction_data = self._build_interaction_summary(guild_id)
        
        member_health = await self._analyze_member_health(guild_id, guild, interaction_data)
        
        clusters = await self._identify_clusters(guild_id, guild, interaction_data)
        
        health_score = self._calculate_health_score(interaction_data, member_health, clusters)
        
        insights = await self._generate_ai_insights(guild_id, guild, member_health, clusters, health_score)
        
        suggestions = await self._suggest_actions(guild_id, guild, member_health, clusters, health_score)
        
        report = {
            "timestamp": time.time(),
            "guild_id": guild_id,
            "health_score": health_score,
            "member_count": len(member_health),
            "active_members": sum(1 for m in member_health.values() if m.days_inactive < 7),
            "isolated_members": sum(1 for m in member_health.values() if m.isolation_score > config.get("isolation_threshold", 0.3)),
            "clusters": [{"name": c.name, "members": c.members, "strength": c.avg_interaction_strength} for c in clusters],
            "insights": insights,
            "suggestions": suggestions
        }
        
        if config.get("health_reports_enabled", True):
            await self._send_health_report(guild, report)
        
        self._save_health_history(guild_id, report)
        
        return report

    def _build_interaction_summary(self, guild_id: int) -> dict:
        graph = self._interaction_graph.get(guild_id, {})
        
        total_interactions = 0
        interaction_pairs = 0
        mutual_connections = 0
        
        for user_id, targets in graph.items():
            for target_id, weight in targets.items():
                if weight > 0.1:
                    total_interactions += 1
                    if target_id in graph and user_id in graph.get(target_id, {}):
                        if graph[target_id].get(user_id, 0) > 0.1:
                            mutual_connections += 1
                    interaction_pairs += 1
        
        return {
            "total_interactions": total_interactions,
            "interaction_pairs": interaction_pairs,
            "mutual_connections": mutual_connections,
            "unique_users": len(graph)
        }

    async def _analyze_member_health(self, guild_id: int, guild: discord.Guild, interaction_data: dict) -> Dict[int, MemberHealth]:
        graph = self._interaction_graph.get(guild_id, {})
        config = self.get_config(guild_id)
        
        member_health = {}
        
        for member in guild.members:
            if member.bot:
                continue
            
            user_id = member.id
            user_graph = graph.get(user_id, {})
            
            inbound = sum(1 for u in graph if user_id in graph.get(u, {}))
            outbound = len(user_graph)
            
            mutual = [t for t in user_graph if t in graph and user_id in graph.get(t, {})]
            
            avg_outbound = sum(user_graph.values()) / len(user_graph) if user_graph else 0
            isolation_score = max(0, 1 - (avg_outbound * 2))
            
            members_above = sum(1 for u in graph.values() for w in u.values() if w > user_graph.get(member.id, 0))
            influence_score = min(1.0, members_above / max(1, len(graph)))
            
            last_active = await self._get_last_active(user_id, guild)
            days_inactive = int((time.time() - last_active) / 86400) if last_active else 999
            
            activity_trend = "stable"
            if days_inactive < 3:
                activity_trend = "active"
            elif days_inactive > 14:
                activity_trend = "declining"
            
            member_health[user_id] = MemberHealth(
                user_id=user_id,
                inbound_interactions=inbound,
                outbound_interactions=outbound,
                mutual_connections=mutual,
                isolation_score=isolation_score,
                influence_score=influence_score,
                activity_trend=activity_trend,
                days_inactive=days_inactive
            )
        
        return member_health

    async def _get_last_active(self, user_id: int, guild: discord.Guild) -> float:
        try:
            history = dm.get_guild_data(guild.id, "user_activity_history", {})
            if str(user_id) in history:
                return history[str(user_id)].get("last_message", 0)
        except:
            pass
        return 0

    async def _identify_clusters(self, guild_id: int, guild: discord.Guild, interaction_data: dict) -> List[RelationshipCluster]:
        graph = self._interaction_graph.get(guild_id, {})
        
        clusters = []
        visited = set()
        
        def get_strong_connections(user_id: int) -> List[int]:
            user_graph = graph.get(user_id, {})
            return [t for t, w in user_graph.items() if w > 0.4]
        
        for user_id in graph:
            if user_id in visited:
                continue
            
            connected = {user_id}
            queue = [user_id]
            
            while queue:
                current = queue.pop(0)
                for conn in get_strong_connections(current):
                    if conn not in connected:
                        connected.add(conn)
                        queue.append(conn)
            
            if len(connected) >= 3:
                clusters.append(RelationshipCluster(
                    name=f"Group {len(clusters) + 1}",
                    members=list(connected),
                    avg_interaction_strength=0.5,
                    topics=[]
                ))
                visited.update(connected)
        
        return clusters[:5]

    def _calculate_health_score(self, interaction_data: dict, member_health: Dict[int, MemberHealth], clusters: List[RelationshipCluster]) -> float:
        if not member_health:
            return 0.0
        
        unique_users = interaction_data.get("unique_users", 1)
        total_members = len(member_health)
        
        participation_ratio = unique_users / max(1, total_members)
        
        mutual_ratio = interaction_data.get("mutual_connections", 0) / max(1, interaction_data.get("interaction_pairs", 1))
        
        avg_isolation = sum(m.isolation_score for m in member_health.values()) / len(member_health)
        isolation_penalty = 1 - avg_isolation
        
        cluster_bonus = min(0.2, len(clusters) * 0.05)
        
        health_score = (
            (participation_ratio * 0.3) +
            (mutual_ratio * 0.3) +
            (isolation_penalty * 0.3) +
            cluster_bonus
        )
        
        return min(1.0, health_score)

    async def _generate_ai_insights(self, guild_id: int, guild: discord.Guild, 
                                     member_health: Dict[int, MemberHealth], 
                                     clusters: List[RelationshipCluster],
                                     health_score: float) -> List[str]:
        isolated = [m for m in member_health.values() if m.isolation_score > 0.5]
        influencers = sorted(member_health.values(), key=lambda x: x.influence_score, reverse=True)[:5]
        
        insights = []
        
        insights.append(f"Community health score: {health_score:.1f}/10 ({'Good' if health_score > 0.6 else 'Needs attention'})")
        
        if isolated:
            insights.append(f"{len(isolated)} members showing high isolation (low cross-interaction)")
        
        if influencers:
            top_influencer = guild.get_member(influencers[0].user_id)
            if top_influencer:
                insights.append(f"Top community builder: {top_influencer.display_name} (influence score: {influencers[0].influence_score:.2f})")
        
        if clusters:
            insights.append(f"Found {len(clusters)} active group clusters with strong internal connections")
        
        return insights

    async def _suggest_actions(self, guild_id: int, guild: discord.Guild,
                               member_health: Dict[int, MemberHealth],
                               clusters: List[RelationshipCluster],
                               health_score: float) -> List[dict]:
        config = self.get_config(guild_id)
        suggestions = []
        
        isolated = [m for m in member_health.values() if m.isolation_score > config.get("isolation_threshold", 0.3)]
        
        if isolated and config.get("isolation_alerts", True):
            isolated_sample = isolated[:3]
            member_mentions = [f"<@{m.user_id}>" for m in isolated_sample]
            suggestions.append({
                "type": "isolation_outreach",
                "priority": "high",
                "description": f"Reach out to isolated members: {', '.join(member_mentions)}",
                "action": "Private message welcoming them and finding shared interests"
            })
        
        if len(clusters) >= 2 and config.get("bridge_events_enabled", True):
            cluster_names = [c.name for c in clusters[:2]]
            suggestions.append({
                "type": "bridge_event",
                "priority": "medium",
                "description": f"Host event to connect {cluster_names[0]} and {cluster_names[1]}",
                "action": "Create collaborative event requiring cross-group participation"
            })
        
        if len(isolated) > 5 and config.get("mentorship_enabled", True):
            suggestions.append({
                "type": "mentorship_program",
                "priority": "medium",
                "description": "Start mentorship program pairing newcomers with established members",
                "action": "Match isolated members with active community builders"
            })
        
        if health_score < 0.5:
            suggestions.append({
                "type": "engagement_boost",
                "priority": "high",
                "description": "Low community cohesion - consider engagement-focused activities",
                "action": "Daily icebreakers, team activities, or discussion prompts"
            })
        
        return suggestions

    async def _send_health_report(self, guild: discord.Guild, report: dict):
        report_channel_id = dm.get_guild_data(guild.id, "report_channel")
        
        if not report_channel_id:
            config = self.get_config(guild.id)
            report_channel_id = config.get("report_channel")
        
        if not report_channel_id:
            return
        
        channel = guild.get_channel(report_channel_id)
        if not channel:
            return
        
        health = report.get("health_score", 0)
        color = discord.Color.green() if health > 0.6 else discord.Color.orange() if health > 0.4 else discord.Color.red()
        
        embed = discord.Embed(
            title="📊 Community Health Report",
            description=f"Health Score: **{health:.1f}/10**",
            color=color
        )
        
        embed.add_field(
            name="Members",
            value=f"Total: {report.get('member_count', 0)} | Active: {report.get('active_members', 0)} | Isolated: {report.get('isolated_members', 0)}",
            inline=False
        )
        
        for insight in report.get("insights", [])[:3]:
            embed.add_field(name="Insight", value=insight, inline=False)
        
        for suggestion in report.get("suggestions", [])[:3]:
            embed.add_field(
                name=f"💡 {suggestion.get('type', '').replace('_', ' ').title()}",
                value=f"{suggestion.get('description', '')}\n*Action: {suggestion.get('action', '')}*",
                inline=False
            )
        
        embed.set_footer(text="Community Health Analysis • AI-Powered")
        embed.timestamp = discord.utils.utcnow()
        
        await channel.send(embed=embed)

    def _save_health_history(self, guild_id: int, report: dict):
        history = dm.load_json("community_health_history", default={})
        
        if str(guild_id) not in history:
            history[str(guild_id)] = []
        
        history[str(guild_id)].append({
            "timestamp": report.get("timestamp"),
            "health_score": report.get("health_score"),
            "member_count": report.get("member_count"),
            "isolated_members": report.get("isolated_members")
        })
        
        history[str(guild_id)] = history[str(guild_id)][-30:]
        dm.save_json("community_health_history", history)

    async def get_member_health(self, guild_id: int, user_id: int) -> Optional[MemberHealth]:
        self._ensure_guild(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            return None
        
        interaction_data = self._build_interaction_summary(guild_id)
        member_health = await self._analyze_member_health(guild_id, guild, interaction_data)
        
        return member_health.get(user_id)

    async def suggest_connections(self, guild_id: int, user_id: int, limit: int = 5) -> List[Tuple[int, float]]:
        self._ensure_guild(guild_id)
        graph = self._interaction_graph.get(guild_id, {})
        
        user_graph = graph.get(user_id, {})
        existing_connections = set(user_graph.keys())
        
        candidates = []
        
        for other_user_id, other_graph in graph.items():
            if other_user_id == user_id or other_user_id in existing_connections:
                continue
            
            other_to_user = other_graph.get(user_id, 0)
            if other_to_user > 0.1:
                continue
            
            mutual = len(set(user_graph.keys()) & set(other_graph.keys()))
            score = mutual + other_to_user + (0.1 * len(other_graph))
            candidates.append((other_user_id, score))
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:limit]

    async def get_community_stats(self, guild_id: int) -> dict:
        self._ensure_guild(guild_id)
        
        history = dm.load_json("community_health_history", default={})
        guild_history = history.get(str(guild_id), [])
        
        if not guild_history:
            return {"error": "No data available yet"}
        
        latest = guild_history[-1]
        
        if len(guild_history) >= 2:
            prev = guild_history[-2]
            score_change = latest.get("health_score", 0) - prev.get("health_score", 0)
            trend = "up" if score_change > 0.05 else "down" if score_change < -0.05 else "stable"
        else:
            score_change = 0
            trend = "stable"
        
        return {
            "current_health": latest.get("health_score", 0),
            "trend": trend,
            "score_change": score_change,
            "member_count": latest.get("member_count", 0),
            "isolated_members": latest.get("isolated_members", 0),
            "data_points": len(guild_history)
        }



# ======================================================================
# From: modules/conflict_resolution.py
# ======================================================================

import discord
import asyncio
import json
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from data_manager import dm
from logger import logger


class ConflictSeverity(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ConflictType(Enum):
    HEATED_DEBATE = "heated_debate"
    PERSONAL_ATTACKS = "personal_attacks"
    RULE_SKATING = "rule_skating"
    GANGING_UP = "ganging_up"
    MISUNDERSTANDING = "misunderstanding"
    REPEATED_ARGUMENT = "repeated_argument"


class InterventionStrategy(Enum):
    SUGGEST_BREAK = "suggest_break"
    REDIRECT_TOPIC = "redirect_topic"
    MEDIATE_PRIVATE = "mediate_private"
    REMIND_RULES = "remind_rules"
    SEPARATE_PARTICIPANTS = "separate_participants"
    NOTIFY_MODERATOR = "notify_moderator"
    SUMMARIZE_BOTH_SIDES = "summarize_both_sides"
    OFFER_MEDIATION = "offer_mediation"


@dataclass
class ConflictData:
    channel_id: int
    guild_id: int
    participants: List[int]
    conflict_type: Optional[ConflictType]
    severity: ConflictSeverity
    start_time: float
    messages_analyzed: int = 0
    interventions_attempted: int = 0
    resolved: bool = False


@dataclass
class InterventionRecord:
    timestamp: float
    guild_id: int
    channel_id: int
    conflict_type: Optional[str]
    participants: List[int]
    strategy: str
    immediate_result: str
    follow_up_needed: bool
    long_term_outcome: Optional[str] = None


class ConflictResolution:
    def __init__(self, bot):
        self.bot = bot
        self.active_conflicts: Dict[int, ConflictData] = {}
        self._guild_configs: Dict[int, dict] = {}
        self._tension_scores: Dict[int, List[float]] = {}
        self._message_history: Dict[int, List[dict]] = {}
        self._cooldowns: Dict[int, float] = {}
        self._cooldown_seconds = 30

    def get_config(self, guild_id: int) -> dict:
        if guild_id in self._guild_configs:
            return self._guild_configs[guild_id]
        
        config = dm.get_guild_data(guild_id, "conflict_resolution_config", {
            "enabled": True,
            "sensitivity": "medium",
            "auto_intervene": True,
            "notify_mods": True,
            "exempt_channels": [],
            "exempt_roles": [],
            "intervention_aggressiveness": "balanced",
            "min_participants": 2,
            "max_interventions_per_hour": 10
        })
        
        sensitivity_map = {"low": 0.8, "medium": 0.6, "high": 0.4, "critical": 0.2}
        config["_sensitivity_threshold"] = sensitivity_map.get(config.get("sensitivity", "medium"), 0.6)
        
        self._guild_configs[guild_id] = config
        return config

    def update_config(self, guild_id: int, key: str, value):
        config = self.get_config(guild_id)
        config[key] = value
        self._guild_configs[guild_id] = config
        dm.update_guild_data(guild_id, "conflict_resolution_config", config)

    async def analyze_message(self, message: discord.Message) -> bool:
        if message.author.bot:
            return False
        
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        
        if not config.get("enabled", True):
            return False
        
        channel_id = message.channel.id
        if channel_id in config.get("exempt_channels", []):
            return False
        
        user = message.author
        for role in user.roles:
            if role.id in config.get("exempt_roles", []):
                return False
        
        now = time.time()
        if channel_id in self._cooldowns:
            if now - self._cooldowns[channel_id] < self._cooldown_seconds:
                return False
        
        await self._add_to_history(channel_id, message)
        
        recent_messages = self._message_history.get(channel_id, [])
        if len(recent_messages) < 5:
            return False
        
        analysis_result = await self._analyze_tension(message.channel, recent_messages)
        
        if analysis_result["tension_score"] > config.get("_sensitivity_threshold", 0.6):
            if not await self.moderation_has_violation(message):
                await self._proactive_intervene(message, analysis_result)
                self._cooldowns[channel_id] = time.time()
                return True
        
        return False

    async def _add_to_history(self, channel_id: int, message: discord.Message):
        if channel_id not in self._message_history:
            self._message_history[channel_id] = []
        
        msg_data = {
            "author_id": message.author.id,
            "content": message.content,
            "timestamp": message.created_at.timestamp()
        }
        
        self._message_history[channel_id].append(msg_data)
        self._message_history[channel_id] = self._message_history[channel_id][-50:]

    async def _analyze_tension(self, channel: discord.TextChannel, messages: List[dict]) -> dict:
        prompt = f"""Analyze the last {len(messages)} messages in #{channel.name} for conflict indicators.

Recent messages:
{chr(10).join([f"User {m['author_id']}: {m['content'][:200]}" for m in messages[-10:]])}

Analyze for:
1. Tension score (0.0-1.0): How heated is the conversation?
2. Conflict type:heated_debate, personal_attacks, rule_skating, ganging_up, misunderstanding, repeated_argument, or none
3. Participants: Which user IDs are involved
4. Severity: low, medium, high, critical
5. Should intervene: boolean

Respond in JSON format:
{{"tension_score": 0.0-1.0, "conflict_type": "type or null", "participants": [user_ids], "severity": "level", "should_intervene": true/false, "reason": "brief explanation"}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=channel.guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You are a conflict analysis system. Analyze conversations for tension and potential conflicts. Be accurate and brief."
            )
            
            summary = result.get("summary", "")
            import re
            json_match = re.search(r'\{.*\}', summary, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "tension_score": float(data.get("tension_score", 0.5)),
                    "conflict_type": data.get("conflict_type"),
                    "participants": [int(p) for p in data.get("participants", [])],
                    "severity": data.get("severity", "medium"),
                    "should_intervene": data.get("should_intervene", False),
                    "reason": data.get("reason", "")
                }
        except Exception as e:
            logger.error(f"Error analyzing tension: {e}")
        
        return {
            "tension_score": 0.5,
            "conflict_type": None,
            "participants": [],
            "severity": "medium",
            "should_intervene": False,
            "reason": "Analysis failed"
        }

    async def moderation_has_violation(self, message: discord.Message) -> bool:
        content_lower = message.content.lower()
        violation_keywords = ["spam", "scam", "nazi", "explicit", "illegal"]
        return any(kw in content_lower for kw in violation_keywords)

    async def _proactive_intervene(self, message: discord.Message, analysis: dict):
        guild_id = message.guild.id
        channel = message.channel
        config = self.get_config(guild_id)
        
        participants = analysis.get("participants", [])
        if not participants:
            participants = [message.author.id]
        
        conflict_type = analysis.get("conflict_type")
        severity = analysis.get("severity", "medium")
        
        strategy = self._select_strategy(conflict_type, severity, participants, config)
        
        await self._execute_intervention(channel, participants, strategy, conflict_type, severity)
        
        await self._record_intervention(
            guild_id=guild_id,
            channel_id=channel.id,
            conflict_type=conflict_type,
            participants=participants,
            strategy=strategy
        )
        
        if channel.id not in self.active_conflicts:
            self.active_conflicts[channel.id] = ConflictData(
                channel_id=channel.id,
                guild_id=guild_id,
                participants=participants,
                conflict_type=conflict_type,
                severity=ConflictSeverity[severity.upper()],
                start_time=time.time()
            )
        else:
            self.active_conflicts[channel.id].interventions_attempted += 1

    def _select_strategy(self, conflict_type: Optional[str], severity: str, participants: List[int], config: dict) -> str:
        strategies_by_type = {
            "heated_debate": ["suggest_break", "redirect_topic", "summarize_both_sides"],
            "personal_attacks": ["mediate_private", "remind_rules", "notify_moderator"],
            "rule_skating": ["remind_rules", "offer_mediation"],
            "ganging_up": ["separate_participants", "notify_moderator"],
            "repeated_argument": ["suggest_break", "redirect_topic"],
            "misunderstanding": ["summarize_both_sides", "offer_mediation"]
        }
        
        available = strategies_by_type.get(conflict_type, ["suggest_break", "redirect_topic"])
        
        learned_strategies = self._get_learned_strategies(conflict_type)
        if learned_strategies:
            available = learned_strategies + available
        
        return available[0]

    def _get_learned_strategies(self, conflict_type: Optional[str]) -> List[str]:
        if not conflict_type:
            return []
        
        try:
            outcomes = dm.load_json("conflict_outcomes", default={})
            if not outcomes:
                return []
            
            strategy_success = {}
            for entry in outcomes:
                if entry.get("conflict_type") == conflict_type:
                    strategy = entry.get("strategy", "")
                    result = entry.get("long_term_outcome", "")
                    if strategy and result == "resolved":
                        strategy_success[strategy] = strategy_success.get(strategy, 0) + 1
            
            if strategy_success:
                sorted_strategies = sorted(strategy_success.items(), key=lambda x: x[1], reverse=True)
                return [s[0] for s in sorted_strategies[:2]]
        except:
            pass
        
        return []

    async def _execute_intervention(self, channel: discord.TextChannel, participants: List[int], 
                                     strategy: str, conflict_type: Optional[str], severity: str):
        member = channel.guild.me
        
        strategy_templates = {
            "suggest_break": f"I've noticed this discussion is getting a bit heated. Maybe consider taking a break or continuing in a dedicated channel?",
            "redirect_topic": f"Let's keep #{channel.name} on-topic. For debates, try #debate-channel!",
            "mediate_private": "I'd love to help mediate this - would you both like to move to DMs or a private thread?",
            "remind_rules": f"Remember to keep discussions respectful! Check {channel.guild.rules_channel.mention if channel.guild.rules_channel else '#rules'} for guidelines.",
            "separate_participants": "Let's give each side some space. Perhaps continue in separate threads?",
            "summarize_both_sides": "I'd like to summarize what I've heard - does this sound accurate?",
            "offer_mediation": "Would you like me to help find common ground?",
            "notify_moderator": ""
        }
        
        message = strategy_templates.get(strategy, "")
        
        if strategy == "notify_moderator":
            config = self.get_config(channel.guild.id)
            if config.get("notify_mods", True):
                log_channel_id = dm.get_guild_data(channel.guild.id, "log_channel")
                if log_channel_id:
                    log_channel = channel.guild.get_channel(log_channel_id)
                    if log_channel:
                        embed = discord.Embed(
                            title="⚠️ Potential Conflict Detected",
                            description=f"Tension detected in #{channel.name}",
                            color=discord.Color.orange()
                        )
                        embed.add_field(name="Participants", value=f"<@{'> <@'.join(map(str, participants))}>", inline=False)
                        if conflict_type:
                            embed.add_field(name="Type", value=conflict_type, inline=True)
                        embed.add_field(name="Severity", value=severity, inline=True)
                        await log_channel.send(embed=embed)
        elif message:
            await channel.send(f"💡 {message}", delete_after=30)

    async def _record_intervention(self, guild_id: int, channel_id: int, conflict_type: Optional[str],
                                    participants: List[int], strategy: str):
        record = {
            "timestamp": time.time(),
            "guild_id": guild_id,
            "channel_id": channel_id,
            "conflict_type": conflict_type,
            "participants": participants,
            "strategy": strategy,
            "immediate_result": "pending",
            "follow_up_needed": False
        }
        
        outcomes = dm.load_json("conflict_outcomes", default=[])
        if not isinstance(outcomes, list):
            outcomes = []
        outcomes.append(record)
        outcomes = outcomes[-200:]
        dm.save_json("conflict_outcomes", outcomes)
        
        await self._learn_from_outcome(record)

    async def _learn_from_outcome(self, record: dict):
        await asyncio.sleep(3600)
        
        channel_id = record.get("channel_id")
        if channel_id in self.active_conflicts:
            conflict = self.active_conflicts[channel_id]
            
            if conflict.resolved or conflict.interventions_attempted >= 3:
                final_outcome = "resolved" if conflict.resolved else "recurring"
                
                outcomes = dm.load_json("conflict_outcomes", default=[])
                if isinstance(outcomes, list):
                    for i, entry in enumerate(outcomes):
                        if entry.get("channel_id") == channel_id and entry.get("timestamp") == record.get("timestamp"):
                            outcomes[i]["long_term_outcome"] = final_outcome
                            break
                    dm.save_json("conflict_outcomes", outcomes)
                
                if final_outcome == "resolved":
                    await self._store_successful_pattern(record)
                
                del self.active_conflicts[channel_id]

    async def _store_successful_pattern(self, record: dict):
        from vector_memory import vector_memory
        
        vector_memory.store_conversation(
            guild_id=record["guild_id"],
            user_id=0,
            user_message=f"RESOLVED_CONFLICT: {record.get('conflict_type', 'unknown')}",
            bot_response=f"Used {record['strategy']} with success",
            reasoning="Successful conflict resolution pattern",
            walkthrough="Learned from intervention outcome",
            importance_score=0.85
        )

    async def check_resolution_status(self, channel_id: int) -> bool:
        conflict = self.active_conflicts.get(channel_id)
        if not conflict:
            return True
        
        messages = self._message_history.get(channel_id, [])
        if len(messages) < 3:
            return True
        
        recent_tension = sum([1 for m in messages[-5:] if any(
            kw in m.get("content", "").lower() 
            for kw in ["calm", "agree", "sorry", "understand", "thanks", "ok", "okay"]
        )])
        
        if recent_tension >= 3:
            conflict.resolved = True
            return True
        
        return False

    async def get_metrics(self, guild_id: int) -> dict:
        outcomes = dm.load_json("conflict_outcomes", default=[])
        if not isinstance(outcomes, list):
            outcomes = []
        
        guild_outcomes = [o for o in outcomes if o.get("guild_id") == guild_id]
        
        total = len(guild_outcomes)
        resolved = sum(1 for o in guild_outcomes if o.get("long_term_outcome") == "resolved")
        
        strategy_stats = {}
        for o in guild_outcomes:
            strat = o.get("strategy", "unknown")
            if strat not in strategy_stats:
                strategy_stats[strat] = {"total": 0, "resolved": 0}
            strategy_stats[strat]["total"] += 1
            if o.get("long_term_outcome") == "resolved":
                strategy_stats[strat]["resolved"] += 1
        
        return {
            "total_interventions": total,
            "resolved": resolved,
            "success_rate": resolved / total if total > 0 else 0,
            "strategy_stats": strategy_stats
        }



# ======================================================================
# From: modules/server_analytics.py
# ======================================================================

import os
import json
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from discord.ext import tasks

from logger import logger
from data_manager import dm


class ServerAnalytics:
    """
    Tracks server activity metrics and provides predictive analytics.
    Monitors message counts, unique chatters, XP gains, and predicts trends.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self._hourly_cache = {}  # guild_id -> {hour: metrics}
        self._analytics_task = None
        # Loop started manually in setup_hook
    
    def __del__(self):
        """Cleanup task on deletion"""
        if self._analytics_task and not self._analytics_task.cancelled():
            self._analytics_task.cancel()
    
    def start_monitoring_loop(self):
        if not self.hourly_analytics_loop.is_running():
            self.hourly_analytics_loop.start()

    @tasks.loop(hours=1)
    async def hourly_analytics_loop(self):
        """Run every hour to log current activity and prune old data"""
        try:
            await self.bot.wait_until_ready()
            await self._log_hourly_metrics()
            await self._prune_old_data()
            logger.info("Hourly server analytics logged successfully")
        except Exception as e:
            logger.error(f"Error in hourly analytics loop: {e}")
    
    @hourly_analytics_loop.before_loop
    async def before_hourly_analytics(self):
        """Wait for bot to be ready before starting"""
        await self.bot.wait_until_ready()
        # Wait until the top of the next hour
        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        delay = (next_hour - now).total_seconds()
        logger.info(f"Server analytics will start in {delay:.0f} seconds")
        await asyncio.sleep(delay)
    
    @staticmethod
    def _sanitize_hourly(data) -> Dict[str, Dict]:
        """
        Guarantee {hour_key: {metrics}} shape no matter what is stored on disk.
        Protects every consumer from legacy/corrupted entries ('int' object has
        no attribute 'get', string indices must be integers).
        """
        if not isinstance(data, dict):
            return {}
        hourly = data.get("hourly_data")
        if not isinstance(hourly, dict):
            return {}
        return {k: v for k, v in hourly.items() if isinstance(v, dict)}

    async def _log_hourly_metrics(self):
        """Collect and store metrics for all guilds"""
        for guild in self.bot.guilds:
            try:
                guild_id = guild.id
                hour_key = datetime.now().strftime("%Y-%m-%d-%H")

                # Get metrics from the last hour
                metrics = await self._collect_guild_metrics(guild)

                # Store in JSON file
                analytics_file = f"server_analytics_{guild_id}"
                analytics_data = dm.load_json(analytics_file, default=None)
                if not isinstance(analytics_data, dict):
                    analytics_data = {}
                analytics_data["hourly_data"] = self._sanitize_hourly(analytics_data)

                analytics_data["hourly_data"][hour_key] = {
                    "timestamp": time.time(),
                    "message_count": metrics["message_count"],
                    "unique_chatters": metrics["unique_chatters"],
                    "total_xp_gained": metrics["xp_gained"],
                    "voice_minutes": metrics.get("voice_minutes", 0),
                    "collected_at": datetime.now().isoformat()
                }
                
                # Keep only last 720 hours (30 days)
                sorted_hours = sorted(analytics_data["hourly_data"].keys(), reverse=True)
                if len(sorted_hours) > 720:
                    for old_hour in sorted_hours[720:]:
                        del analytics_data["hourly_data"][old_hour]
                
                dm.save_json(analytics_file, analytics_data)
                
            except Exception as e:
                logger.error(f"Failed to collect metrics for guild {guild.name}: {e}")
    
    async def _collect_guild_metrics(self, guild) -> Dict[str, int]:
        """Collect current metrics for a guild"""
        metrics = {
            "message_count": 0,
            "unique_chatters": 0,
            "xp_gained": 0,
            "voice_minutes": 0
        }
        
        try:
            # Get message count and unique chatters from leveling module
            if hasattr(self.bot, 'leveling'):
                hourly_stats = self.bot.leveling.get_hourly_stats(guild.id, 1)  # Last hour
                if hourly_stats:
                    # Take the most recent hour's data
                    latest_hour = sorted(hourly_stats.keys())[0] if hourly_stats else None
                    if latest_hour:
                        hour_data = hourly_stats[latest_hour]
                        if isinstance(hour_data, dict):
                            metrics["message_count"] = int(hour_data.get("message_count", 0) or 0)
                            metrics["unique_chatters"] = int(hour_data.get("unique_chatters", 0) or 0)
                            metrics["xp_gained"] = int(hour_data.get("xp_gained", 0) or 0)
            
            # Voice minutes metric removed - voice_system has been deleted
            metrics["voice_minutes"] = 0
            
            # Convert set to count for JSON serialization (already an int now)
                
        except Exception as e:
            logger.error(f"Error collecting metrics for guild {guild.id}: {e}")
        
        return metrics
    
    async def _prune_old_data(self):
        """Remove data older than 30 days"""
        cutoff_time = time.time() - (30 * 24 * 3600)  # 30 days ago
        
        for guild in self.bot.guilds:
            try:
                analytics_file = f"server_analytics_{guild.id}"
                analytics_data = dm.load_json(analytics_file, default=None)
                if not isinstance(analytics_data, dict):
                    continue

                if "hourly_data" not in analytics_data:
                    continue

                # Remove old entries (skip corrupted non-dict entries too)
                original_count = len(analytics_data["hourly_data"]) if isinstance(analytics_data["hourly_data"], dict) else 0
                cleaned = self._sanitize_hourly(analytics_data)
                analytics_data["hourly_data"] = {
                    hour: data for hour, data in cleaned.items()
                    if isinstance(data.get("timestamp", 0), (int, float)) and data.get("timestamp", 0) > cutoff_time
                }

                pruned_count = original_count - len(analytics_data["hourly_data"])
                if pruned_count > 0:
                    dm.save_json(analytics_file, analytics_data)
                    logger.debug(f"Pruned {pruned_count} old entries for guild {guild.id}")
                    
            except Exception as e:
                logger.error(f"Error pruning data for guild {guild.id}: {e}")
    
    def get_forecast(self, guild_id: int) -> Dict[str, Any]:
        """
        Generate a comprehensive forecast for a guild.
        
        Returns:
            dict with:
            - trend: "rising", "stable", or "declining"
            - trend_percentage: change percentage from previous day
            - predicted_peak: predicted peak activity time
            - xp_level_up_eta: estimated time until average user levels up
            - health_score: 0-100 score
            - current_activity: current activity level description
            - recommendations: list of suggestions
        """
        analytics_file = f"server_analytics_{guild_id}"
        analytics_data = dm.load_json(analytics_file, default=None)

        hourly_data = self._sanitize_hourly(analytics_data)

        if not hourly_data:
            return self._generate_empty_forecast()
        
        # Sort hours chronologically
        sorted_hours = sorted(hourly_data.keys())
        
        # Get last 24 hours of data
        last_24_hours = sorted_hours[-24:] if len(sorted_hours) >= 24 else sorted_hours
        last_6_hours = sorted_hours[-6:] if len(sorted_hours) >= 6 else sorted_hours
        
        # Get same period from previous day for comparison
        prev_day_hours = sorted_hours[-48:-24] if len(sorted_hours) >= 48 else []
        
        # Calculate metrics
        current_messages = sum(hourly_data[h].get("message_count", 0) for h in last_6_hours)
        current_chatters = sum(hourly_data[h].get("unique_chatters", 0) for h in last_6_hours)
        current_xp = sum(hourly_data[h].get("total_xp_gained", 0) for h in last_6_hours)
        
        prev_messages = sum(hourly_data[h].get("message_count", 0) for h in prev_day_hours[:6]) if prev_day_hours else current_messages
        prev_chatters = sum(hourly_data[h].get("unique_chatters", 0) for h in prev_day_hours[:6]) if prev_day_hours else current_chatters
        
        # Calculate trend
        if prev_messages == 0:
            trend_percentage = 0
        else:
            trend_percentage = ((current_messages - prev_messages) / prev_messages) * 100
        
        if trend_percentage > 10:
            trend = "rising"
        elif trend_percentage < -10:
            trend = "declining"
        else:
            trend = "stable"
        
        # Predict peak activity time
        predicted_peak = self._predict_peak_time(hourly_data, last_24_hours)
        
        # Calculate XP level-up ETA
        xp_eta = self._calculate_xp_eta(guild_id, current_xp, last_6_hours)
        
        # Calculate health score (0-100)
        health_score = self._calculate_health_score(
            current_messages=current_messages,
            current_chatters=current_chatters,
            trend_percentage=trend_percentage,
            hourly_data=hourly_data,
            last_24_hours=last_24_hours
        )
        
        # Generate current activity description
        current_activity = self._describe_current_activity(current_messages, current_chatters, trend)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(trend, health_score, current_chatters)
        
        return {
            "trend": trend,
            "trend_percentage": round(trend_percentage, 2),
            "predicted_peak": predicted_peak,
            "xp_level_up_eta": xp_eta,
            "health_score": health_score,
            "current_activity": current_activity,
            "recommendations": recommendations,
            "metrics": {
                "messages_last_6h": current_messages,
                "chatters_last_6h": current_chatters,
                "xp_last_6h": current_xp
            }
        }
    
    def _generate_empty_forecast(self) -> Dict[str, Any]:
        """Return a default forecast when no data is available"""
        return {
            "trend": "unknown",
            "trend_percentage": 0,
            "predicted_peak": "insufficient data",
            "xp_level_up_eta": "insufficient data",
            "health_score": 50,
            "current_activity": "No recent activity data available",
            "recommendations": ["Encourage more server participation to generate analytics"],
            "metrics": {
                "messages_last_6h": 0,
                "chatters_last_6h": 0,
                "xp_last_6h": 0
            }
        }
    
    def _predict_peak_time(self, hourly_data: Dict, last_24_hours: List[str]) -> str:
        """Predict the peak activity time for the next 24 hours based on historical patterns"""
        if len(last_24_hours) < 12:
            return "insufficient data"
        
        # Group by hour of day
        hour_activity = {}
        for hour_key in last_24_hours:
            try:
                dt = datetime.strptime(hour_key, "%Y-%m-%d-%H")
                hour_of_day = dt.hour
                messages = hourly_data[hour_key].get("message_count", 0)
                
                if hour_of_day not in hour_activity:
                    hour_activity[hour_of_day] = []
                hour_activity[hour_of_day].append(messages)
            except Exception:
                continue
        
        if not hour_activity:
            return "insufficient data"
        
        # Find hour with highest average activity
        best_hour = max(hour_activity.items(), key=lambda x: sum(x[1]) / len(x[1]))[0]
        
        # Format as readable time
        if best_hour == 0:
            return "12:00 AM"
        elif best_hour < 12:
            return f"{best_hour}:00 AM"
        elif best_hour == 12:
            return "12:00 PM"
        else:
            return f"{best_hour - 12}:00 PM"
    
    def _calculate_xp_eta(self, guild_id: int, current_xp: int, hours: List[str]) -> str:
        """Estimate time until average user levels up"""
        if current_xp == 0 or len(hours) == 0:
            return "insufficient data"
        
        # Get average XP needed per level from leveling module
        avg_xp_per_level = 1000  # Default assumption
        
        # Calculate hourly XP rate
        xp_per_hour = current_xp / len(hours)
        
        if xp_per_hour == 0:
            return "no XP gain detected"
        
        # Estimate hours until next level for average user
        hours_to_level = avg_xp_per_level / xp_per_hour
        
        if hours_to_level < 24:
            return f"{int(hours_to_level)} hours"
        elif hours_to_level < 168:  # 1 week
            return f"{int(hours_to_level / 24)} days"
        else:
            return f"{int(hours_to_level / 168)} weeks"
    
    def _calculate_health_score(self, current_messages: int, current_chatters: int, 
                               trend_percentage: float, hourly_data: Dict, 
                               last_24_hours: List[str]) -> int:
        """Calculate overall server health score (0-100)"""
        score = 50  # Base score
        
        # Message activity component (0-25 points)
        if current_messages > 100:
            score += 25
        elif current_messages > 50:
            score += 20
        elif current_messages > 20:
            score += 15
        elif current_messages > 5:
            score += 10
        elif current_messages > 0:
            score += 5
        
        # Unique chatters component (0-25 points)
        if current_chatters > 20:
            score += 25
        elif current_chatters > 10:
            score += 20
        elif current_chatters > 5:
            score += 15
        elif current_chatters > 2:
            score += 10
        elif current_chatters > 0:
            score += 5
        
        # Trend component (-25 to +25 points)
        trend_bonus = max(-25, min(25, trend_percentage))
        score += trend_bonus
        
        # Consistency component (0-10 points)
        if len(last_24_hours) >= 24:
            message_counts = [hourly_data[h].get("message_count", 0) for h in last_24_hours]
            avg_messages = sum(message_counts) / len(message_counts)
            variance = sum((x - avg_messages) ** 2 for x in message_counts) / len(message_counts)
            
            # Lower variance = more consistent = higher score
            if variance < 100:
                score += 10
            elif variance < 500:
                score += 7
            elif variance < 1000:
                score += 4
        
        return max(0, min(100, int(score)))
    
    def _describe_current_activity(self, messages: int, chatters: int, trend: str) -> str:
        """Generate a human-readable description of current activity"""
        if messages == 0:
            return "The server is currently quiet with no recent messages"
        
        activity_level = "very active" if messages > 100 else "moderately active" if messages > 30 else "somewhat active"
        chatter_level = "with many participants" if chatters > 15 else "with a few active members" if chatters > 5 else "with limited participation"
        
        trend_desc = ""
        if trend == "rising":
            trend_desc = "Activity is increasing compared to yesterday"
        elif trend == "declining":
            trend_desc = "Activity is slightly lower than usual"
        else:
            trend_desc = "Activity is stable"
        
        return f"The server is {activity_level} {chatter_level}. {trend_desc}"
    
    def _generate_recommendations(self, trend: str, health_score: int, chatters: int) -> List[str]:
        """Generate actionable recommendations based on analytics"""
        recommendations = []
        
        if trend == "declining":
            recommendations.append("Consider hosting an event to boost engagement")
            recommendations.append("Try starting a discussion topic in general chat")
        
        if health_score < 40:
            recommendations.append("Server activity is low - consider promoting the server")
            recommendations.append("Add interactive commands or games to encourage participation")
        
        if chatters < 5:
            recommendations.append("Only a few members are active - try tagging inactive members")
            recommendations.append("Create voice channels to encourage real-time interaction")
        
        if health_score > 80:
            recommendations.append("Great momentum! Consider adding new features to maintain interest")
            recommendations.append("Perfect time to launch new initiatives or events")
        
        if not recommendations:
            recommendations.append("Server health looks good - keep doing what you're doing!")
        
        return recommendations[:3]  # Return top 3 recommendations
    
    async def get_hourly_stats(self, guild_id: int, hours: int = 24) -> Dict[str, Any]:
        """Get raw hourly statistics for the past N hours"""
        analytics_file = f"server_analytics_{guild_id}"
        analytics_data = dm.load_json(analytics_file, default=None)

        hourly_data = self._sanitize_hourly(analytics_data)
        sorted_hours = sorted(hourly_data.keys(), reverse=True)[:hours]
        
        return {
            hour: hourly_data[hour] for hour in sorted_hours if hour in hourly_data
        }


# Global instance
analytics = None


def setup_analytics(bot):
    """Initialize the server analytics system"""
    global analytics
    analytics = ServerAnalytics(bot)
    return analytics


def get_analytics() -> Optional[ServerAnalytics]:
    """Get the analytics instance"""
    return analytics



# ======================================================================
# From: modules/content_generator.py
# ======================================================================

import discord
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



# ======================================================================
# From: modules/embed_system.py
# ======================================================================

import discord
from discord.ext import commands
from discord import ui
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from enum import Enum
import time
import asyncio

from data_manager import dm
from logger import logger


class ButtonType(Enum):
    VERIFY = "verify"
    APPLY_STAFF = "apply_staff"
    CREATE_TICKET = "create_ticket"
    CUSTOM = "custom"


@dataclass
class EmbedConfig:
    """Configuration for an embed with buttons"""
    title: str
    description: str
    color: discord.Color = discord.Color.blue()
    fields: Optional[List[Dict[str, Any]]] = None
    footer: Optional[str] = None
    thumbnail: Optional[str] = None
    image: Optional[str] = None
    buttons: Optional[List[ButtonType]] = None
    custom_buttons: Optional[List[Dict[str, Any]]] = None  # For extensible custom buttons

    def __post_init__(self):
        if self.fields is None:
            self.fields = []
        if self.buttons is None:
            self.buttons = []


class EmbedSystem:
    """Robust system for creating embeds with buttons and modals"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_views: Dict[str, ui.View] = {}  # Track active views by message ID

    def get_persistent_views(self) -> List[ui.View]:
        """Persistent views that survive restarts (guild resolved at click time)."""
        return [
            EmbedVerifyView(0),
            EmbedApplyStaffView(0),
            EmbedCreateTicketView(0),
        ]
        
    async def create_embed_with_buttons(
        self,
        channel: discord.TextChannel,
        config: EmbedConfig,
        guild_id: int,
        timeout: int = 300  # 5 minutes default timeout
    ) -> discord.Message:
        """
        Create and send an embed with buttons
        
        Args:
            channel: The channel to send the embed to
            config: Configuration for the embed
            guild_id: The guild ID for context
            timeout: Timeout in seconds for button interactions
            
        Returns:
            The sent message
        """
        try:
            embed = self._build_embed(config)
            view = self._build_view(config.buttons, config.custom_buttons, guild_id, timeout)
            
            message = await channel.send(embed=embed, view=view)
            
            # Track the view for potential cleanup
            if timeout > 0:
                view_id = f"{guild_id}_{message.id}"
                self._active_views[view_id] = view
                
                # Schedule cleanup
                self.bot.loop.create_task(self._cleanup_view_later(view_id, timeout))
            
            logger.info(f"Created embed with buttons in {channel.name} (guild: {guild_id})")
            return message
            
        except Exception as e:
            logger.error(f"Failed to create embed with buttons: {e}")
            raise
    
    def _build_embed(self, config: EmbedConfig) -> discord.Embed:
        """Build a Discord embed from configuration"""
        embed = discord.Embed(
            title=config.title,
            description=config.description,
            color=config.color
        )
        
        for field in config.fields or []:
            embed.add_field(
                name=field['name'],
                value=field['value'],
                inline=field.get('inline', False)
            )
        
        if config.footer:
            embed.set_footer(text=config.footer)
        
        if config.thumbnail:
            embed.set_thumbnail(url=config.thumbnail)
        
        if config.image:
            embed.set_image(url=config.image)
        
        embed.timestamp = discord.utils.utcnow()
        return embed
    
    def _build_view(
        self,
        buttons: Optional[List[ButtonType]],
        custom_buttons: Optional[List[Dict[str, Any]]],
        guild_id: int,
        timeout: int
    ) -> ui.View:
        """Build a view with the specified buttons"""
        view = ui.View(timeout=timeout)

        for button_type in buttons or []:
            if button_type == ButtonType.VERIFY:
                view.add_item(EmbedVerifyButton(guild_id))
            elif button_type == ButtonType.APPLY_STAFF:
                view.add_item(EmbedApplyStaffButton(guild_id))
            elif button_type == ButtonType.CREATE_TICKET:
                view.add_item(EmbedCreateTicketButton(guild_id))

        # Add custom buttons
        for custom_config in custom_buttons or []:
            button = EmbedCustomButton(
                label=custom_config['label'],
                style=custom_config.get('style', discord.ButtonStyle.secondary),
                custom_id=custom_config['custom_id'],
                callback=custom_config.get('callback'),
                guild_id=guild_id
            )
            view.add_item(button)

        return view
    
    async def _cleanup_view_later(self, view_id: str, delay: int):
        """Clean up a view after timeout"""
        await asyncio.sleep(delay)
        if view_id in self._active_views:
            view = self._active_views[view_id]
            if not view.is_finished():
                try:
                    view.stop()
                except Exception as e:
                    logger.debug(f"Error stopping view {view_id}: {e}")
            del self._active_views[view_id]

    async def create_example_embed(self, channel: discord.TextChannel, guild_id: int) -> discord.Message:
        """
        Create an example embed with Verify, Apply Staff, and Create Ticket buttons

        Args:
            channel: The channel to send the embed to
            guild_id: The guild ID

        Returns:
            The sent message
        """
        config = EmbedConfig(
            title="Server Actions",
            description="Welcome to our server! Use the buttons below to interact with our systems.",
            color=discord.Color.blue(),
            fields=[
                {
                    "name": "Verification",
                    "value": "Click 'Verify' to get access to the rest of the server.",
                    "inline": False
                },
                {
                    "name": "Staff Applications",
                    "value": "Interested in joining our staff team? Click 'Apply Staff' to submit your application.",
                    "inline": False
                },
                {
                    "name": "Support Tickets",
                    "value": "Need help? Click 'Create Ticket' to open a support ticket.",
                    "inline": False
                }
            ],
            footer="All interactions are logged for moderation purposes",
            buttons=[
                ButtonType.VERIFY,
                ButtonType.APPLY_STAFF,
                ButtonType.CREATE_TICKET
            ]
        )

        return await self.create_embed_with_buttons(channel, config, guild_id)


# Button Classes
class EmbedVerifyButton(ui.Button):
    """Verify button for embed system"""

    def __init__(self, guild_id: int):
        super().__init__(
            label="Verify",
            style=discord.ButtonStyle.success,
            custom_id="embed_verify_button_persistent"
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message("❌ Error: Guild not found.", ephemeral=True)
                return

            # Get verification role from guild data
            guild_id = self.guild_id or guild.id
            role_id = dm.get_guild_data(guild_id, "verify_role")
            role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")
            
            if not role:
                await interaction.response.send_message("❌ Verification role not found. Please contact staff.", ephemeral=True)
                return
            
            if role in interaction.user.roles:
                await interaction.response.send_message("✅ You are already verified!", ephemeral=True)
                return

            # Handle Unverified role removal if applicable
            unverified = discord.utils.get(guild.roles, name="Unverified")
            if unverified and unverified in interaction.user.roles:
                await interaction.user.remove_roles(unverified)

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ You're verified! Enjoy the server!", ephemeral=True)
            
            logger.info(f"User {interaction.user} verified in guild {guild.id}")
            
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to assign the Verified role.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in verify button callback: {e}")
            await interaction.response.send_message("❌ An error occurred during verification.", ephemeral=True)


class EmbedApplyStaffButton(ui.Button):
    """Apply for staff button that opens a modal"""

    def __init__(self, guild_id: int):
        super().__init__(
            label="Apply Staff",
            style=discord.ButtonStyle.primary,
            custom_id="embed_apply_staff_button_persistent"
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            modal = EmbedStaffApplicationModal(self.guild_id or interaction.guild_id)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error opening staff application modal: {e}")
            await interaction.response.send_message("❌ An error occurred opening the application form.", ephemeral=True)


class EmbedCreateTicketButton(ui.Button):
    """Create ticket button for embed system"""

    def __init__(self, guild_id: int):
        super().__init__(
            label="Create Ticket",
            style=discord.ButtonStyle.primary,
            custom_id="embed_create_ticket_button_persistent"
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message("❌ Error: Guild not found.", ephemeral=True)
                return
            
            # Find ticket channel
            guild_id = self.guild_id or guild.id
            ch_id = dm.get_guild_data(guild_id, 'tickets_channel') or dm.get_guild_data(guild_id, 'ticket_queue_channel')
            channel = guild.get_channel(ch_id) if ch_id else discord.utils.get(guild.text_channels, name="ticket-queue")
            
            if not channel:
                await interaction.response.send_message("❌ Ticket channel not found. Please contact staff.", ephemeral=True)
                return

            try:
                thread = await channel.create_thread(
                    name=f"ticket-{interaction.user.display_name}",
                    type=discord.ChannelType.private_thread if guild.premium_tier >= 2 else discord.ChannelType.public_thread,
                    inviter=interaction.user
                )
                await thread.send(f"🎫 **New Ticket**\n{interaction.user.mention} has opened a ticket. Staff will be with you shortly.")
                await interaction.response.send_message(f"✅ Ticket created! Go to {thread.mention}", ephemeral=True)
                
                logger.info(f"User {interaction.user} created ticket in guild {guild.id}")
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ I lack permissions to create threads.", ephemeral=True)
            except Exception as e:
                logger.error(f"Failed to create ticket thread: {e}")
                await interaction.response.send_message("❌ Failed to create ticket thread.", ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error in create ticket button callback: {e}")
            await interaction.response.send_message("❌ An error occurred creating the ticket.", ephemeral=True)


# View Classes for Persistent Buttons
class EmbedVerifyView(ui.View):
    """Persistent view containing the verify button"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(EmbedVerifyButton(guild_id))


class EmbedApplyStaffView(ui.View):
    """Persistent view containing the apply staff button"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(EmbedApplyStaffButton(guild_id))


class EmbedCreateTicketView(ui.View):
    """Persistent view containing the create ticket button"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(EmbedCreateTicketButton(guild_id))


class EmbedCustomButton(ui.Button):
    """Custom button for extensibility"""

    def __init__(self, label: str, style: discord.ButtonStyle, custom_id: str, callback: Optional[Callable], guild_id: int):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.custom_callback = callback
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        try:
            if self.custom_callback:
                await self.custom_callback(interaction, self.guild_id)
            else:
                await interaction.response.send_message("❌ This button is not configured.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in custom button callback: {e}")
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


# Modal Classes
class EmbedStaffApplicationModal(ui.Modal):
    """Modal for staff applications"""
    
    def __init__(self, guild_id: int):
        super().__init__(title="Staff Application", timeout=600)  # 10 minutes
        self.guild_id = guild_id
        
        self.reason_input = ui.TextInput(
            label="Why do you want to be staff?",
            style=discord.TextStyle.paragraph,
            placeholder="Tell us about yourself and why you'd be a good fit...",
            required=True,
            min_length=50,
            max_length=1000
        )
        
        self.experience_input = ui.TextInput(
            label="Experience",
            style=discord.TextStyle.paragraph,
            placeholder="Any previous moderation experience? (optional)",
            required=False,
            min_length=0,
            max_length=1000
        )
        
        self.add_item(self.reason_input)
        self.add_item(self.experience_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message("❌ Error: Guild not found.", ephemeral=True)
                return
            
            # Find applications/log channel
            apps_channel = None
            
            # Try applications channel first
            apps_channel_id = dm.get_guild_data(guild.id, "applications_channel")
            if apps_channel_id:
                apps_channel = guild.get_channel(apps_channel_id)
            
            # Fallback to log channel
            if not apps_channel:
                log_channel_id = dm.get_guild_data(guild.id, "log_channel")
                if log_channel_id:
                    apps_channel = guild.get_channel(log_channel_id)
            
            # Final fallback
            if not apps_channel:
                apps_channel = discord.utils.get(guild.text_channels, name="applications")
            
            if not apps_channel:
                await interaction.response.send_message("❌ Applications channel not found. Please contact staff.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="📝 New Staff Application",
                description=f"Application from {interaction.user.mention}",
                color=discord.Color.purple()
            )
            embed.add_field(name="Reason", value=self.reason_input.value or "Not provided", inline=False)
            embed.add_field(name="Experience", value=self.experience_input.value or "Not provided", inline=False)
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            
            await apps_channel.send(embed=embed)
            await interaction.response.send_message("✅ Your application has been submitted!", ephemeral=True)
            
            logger.info(f"User {interaction.user} submitted staff application in guild {guild.id}")
            
        except Exception as e:
            logger.error(f"Error in staff application modal submit: {e}")
            await interaction.response.send_message("❌ An error occurred while submitting your application.", ephemeral=True)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in staff application modal: {error}")
        await interaction.response.send_message("❌ An error occurred while submitting your application.", ephemeral=True)
    
    async def on_timeout(self):
        # Modal timed out, no action needed
        pass
