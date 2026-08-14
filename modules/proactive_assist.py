import discord
from discord.ext import tasks, commands
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from data_manager import dm
from logger import logger

class ProactiveAIAssist(commands.Cog):
    """
    Proactive AI Assistant that monitors server health and suggests actions to admins.
    Moves the bot from reactive to proactive community management.
    """

    def __init__(self, bot):
        self.bot = bot
        self.check_interval = 60 # minutes
        self._last_intervention = {} # guild_id -> timestamp
        self.proactive_loop.start()

    def cog_unload(self):
        self.proactive_loop.cancel()

    @tasks.loop(minutes=60)
    async def proactive_loop(self):
        """Main loop to analyze server state and offer proactive help."""
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            try:
                await self._analyze_and_assist(guild)
            except Exception as e:
                logger.error(f"Error in proactive assist for guild {guild.id}: {e}")

    async def _analyze_and_assist(self, guild: discord.Guild):
        guild_id = guild.id

        # Cooldown check: don't annoy admins too often
        last_time = self._last_intervention.get(guild_id, 0)
        if time.time() - last_time < 3600 * 6: # At most once every 6 hours
            return

        # 1. Gather Metrics
        intelligence = getattr(self.bot, 'intelligence', None)
        if not intelligence:
            return

        metrics = await intelligence.get_server_metrics(guild_id)
        at_risk = intelligence.get_at_risk_members(guild_id)
        trends = intelligence.get_topic_trends(guild_id)

        # 2. Check for "Red Flags" or opportunities
        # e.g. Rapid join rate, rising toxicity (if we had sentiment), declining engagement

        reasons = []
        if metrics.engagement_score < 40:
            reasons.append("Low engagement score")
        if len(at_risk) > 10:
            reasons.append(f"High number of at-risk members ({len(at_risk)})")

        # 3. Consult the AI
        if not reasons and metrics.messages_today < 10:
            # Maybe too quiet?
            reasons.append("Server is very quiet")

        if not reasons:
            return # Everything seems fine

        # Build prompt for AI
        prompt = f"""
        Analyze this Discord server's state and suggest ONE proactive action for the admins.

        SERVER: {guild.name}
        METRICS:
        - Engagement Score: {metrics.engagement_score:.1f}/100
        - Active Members: {metrics.active_members}/{metrics.total_members}
        - Messages today: {metrics.messages_today}
        - At-risk members: {len(at_risk)}

        CONCERNS IDENTIFIED: {', '.join(reasons)}

        TASK:
        Provide a concise, professional suggestion to the admins.
        It could be a moderation action (e.g., enable slowmode if spammy),
        an engagement action (e.g., host a giveaway or trivia),
        or a configuration change.

        Respond with JSON:
        {{
            "analysis": "Brief summary of what you noticed",
            "suggestion": "Clear actionable advice",
            "action_type": "moderation|engagement|config",
            "priority": "low|medium|high"
        }}
        """

        try:
            # Use AI to decide
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=0,
                user_input=prompt,
                system_prompt="You are a proactive server management AI consultant. Be helpful, professional, and data-driven."
            )

            if result and "suggestion" in result:
                await self._notify_admins(guild, result)
                self._last_intervention[guild_id] = time.time()

        except Exception as e:
            logger.debug(f"Proactive AI Assist consultation failed: {e}")

    async def _notify_admins(self, guild: discord.Guild, advice: dict):
        """Send a proactive suggestion to the server's log channel or system channel."""
        # Find a suitable channel
        log_channel_id = dm.get_guild_data(guild.id, "mod_log_config", {}).get("log_channel_id")
        channel = guild.get_channel(log_channel_id) if log_channel_id else guild.system_channel

        if not channel:
            # Fallback to first manageable text channel
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break

        if not channel:
            return

        priority_emoji = {"low": "🔵", "medium": "🟠", "high": "🔴"}.get(advice.get("priority", "low"), "⚪")

        embed = discord.Embed(
            title=f"{priority_emoji} Proactive AI Suggestion",
            description=advice["analysis"],
            color=discord.Color.blue() if advice.get("priority") != "high" else discord.Color.red()
        )

        embed.add_field(name="💡 Recommendation", value=advice["suggestion"], inline=False)
        embed.set_footer(text="Miro Proactive Assist • This is an automated insight based on server activity.")

        # Use a whisper/ephemeral-like message if possible, but since it's background,
        # we'll just send a regular message in a staff channel if we can find one.

        staff_channel = discord.utils.get(guild.text_channels, name="staff-chat") or \
                        discord.utils.get(guild.text_channels, name="mod-chat") or \
                        discord.utils.get(guild.text_channels, name="admin-chat")

        target = staff_channel or channel

        try:
            await target.send(embed=embed)
            logger.info(f"Sent proactive assist notification to {guild.name}")
        except:
            pass

async def setup(bot):
    await bot.add_cog(ProactiveAIAssist(bot))
