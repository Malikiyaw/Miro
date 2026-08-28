import discord
from discord.ext import commands, tasks
import asyncio
import time
from datetime import datetime, timedelta
from data_manager import dm
from logger import logger

class ProactiveAssist(commands.Cog):
    """Proactive assistance system for Discord bot"""

    def __init__(self, bot):
        self.bot = bot
        self.check_inactive_users.start()

    def cog_unload(self):
        self.check_inactive_users.cancel()

    @tasks.loop(hours=24)
    async def check_inactive_users(self):
        """Check for inactive users and send welcome messages"""
        try:
            for guild in self.bot.guilds:
                # Skip if system not enabled
                config = dm.get_guild_data(guild.id, "proactive_config", {})
                if not config.get("enabled", False):
                    continue

                # Check member activity
                inactive_threshold = config.get("inactive_days", 7)
                cutoff_date = datetime.now() - timedelta(days=inactive_threshold)

                for member in guild.members:
                    if member.bot:
                        continue

                    # Check last message date (simplified)
                    last_active = dm.get_guild_data(guild.id, f"user_last_active_{member.id}", 0)
                    if last_active == 0 or datetime.fromtimestamp(last_active) < cutoff_date:
                        # Send proactive message
                        try:
                            embed = discord.Embed(
                                title="👋 Welcome back!",
                                description=f"We noticed you haven't been active in **{guild.name}** for a while. Is everything okay?",
                                color=discord.Color.blue()
                            )
                            embed.add_field(
                                name="Need help?",
                                value="• Check out our channels\n• Use !help for commands\n• Contact staff if needed",
                                inline=False
                            )
                            await member.send(embed=embed)
                            logger.info(f"Sent proactive message to {member} in {guild.name}")
                        except discord.Forbidden:
                            pass  # Can't DM user

        except Exception as e:
            logger.error(f"Error in proactive assistance: {e}")

    @commands.command(name="proactive")
    @commands.has_permissions(administrator=True)
    async def proactive_command(self, ctx, action: str = None):
        """Manage proactive assistance system"""
        if action == "enable":
            config = dm.get_guild_data(ctx.guild.id, "proactive_config", {})
            config["enabled"] = True
            dm.update_guild_data(ctx.guild.id, "proactive_config", config)
            await ctx.send("✅ Proactive assistance enabled")
        elif action == "disable":
            config = dm.get_guild_data(ctx.guild.id, "proactive_config", {})
            config["enabled"] = False
            dm.update_guild_data(ctx.guild.id, "proactive_config", config)
            await ctx.send("❌ Proactive assistance disabled")
        else:
            await ctx.send("Usage: !proactive enable/disable")

async def setup(bot):
    await bot.add_cog(ProactiveAssist(bot))