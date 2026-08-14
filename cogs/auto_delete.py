import discord
from discord.ext import commands
from data_manager import dm
import logging

logger = logging.getLogger(__name__)

class AutoDeleteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="autodelete", invoke_without_command=True)
    async def autodelete(self, ctx):
        """Auto-delete messages containing specific words."""
        await ctx.send("Usage: !autodelete <add|remove|list> [word]")

    @autodelete.command(name="add")
    async def add_word(self, ctx, word: str):
        """Add a word to auto-delete list."""
        guild_id = ctx.guild.id
        words = dm.get_guild_data(guild_id, "auto_delete_words", [])
        if word.lower() in [w.lower() for w in words]:
            await ctx.send(f"Word '{word}' is already in the list.")
            return
        words.append(word.lower())
        dm.update_guild_data(guild_id, "auto_delete_words", words)
        await ctx.send(f"✅ Added '{word}' to auto-delete list.")

    @autodelete.command(name="remove")
    async def remove_word(self, ctx, word: str):
        """Remove a word from auto-delete list."""
        guild_id = ctx.guild.id
        words = dm.get_guild_data(guild_id, "auto_delete_words", [])
        filtered = [w for w in words if w.lower() != word.lower()]
        if len(filtered) == len(words):
            await ctx.send(f"Word '{word}' not found in the list.")
            return
        dm.update_guild_data(guild_id, "auto_delete_words", filtered)
        await ctx.send(f"✅ Removed '{word}' from auto-delete list.")

    @autodelete.command(name="list")
    async def list_words(self, ctx):
        """List all auto-delete words."""
        guild_id = ctx.guild.id
        words = dm.get_guild_data(guild_id, "auto_delete_words", [])
        if not words:
            await ctx.send("No auto-delete words configured.")
            return
        embed = discord.Embed(title="🗑️ Auto-Delete Words", color=discord.Color.red())
        embed.description = "\n".join([f"• {w}" for w in words])
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        guild_id = message.guild.id if message.guild else None
        if not guild_id:
            return
        words = dm.get_guild_data(guild_id, "auto_delete_words", [])
        if not words:
            return
        content_lower = message.content.lower()
        for word in words:
            if word.lower() in content_lower:
                try:
                    await message.delete()
                    logger.info(f"Auto-deleted message {message.id} in guild {guild_id} (contains '{word}')")
                except discord.Forbidden:
                    logger.warning(f"No permission to delete message {message.id} in guild {guild_id}")
                except Exception as e:
                    logger.error(f"Failed to delete message {message.id}: {e}")
                break

async def setup(bot):
    await bot.add_cog(AutoDeleteCog(bot))
