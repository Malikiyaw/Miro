import discord
from data_manager import dm
import json
import asyncio
from typing import Dict, Set, Optional
from logger import logger

class TriggerRoles:
    """
    Presence-based trigger role system:
    - When a user types a trigger word, they get the role
    - Role is REMOVED when user goes offline
    - Role is RESTORED when user comes back online (if they previously triggered it)
    - Stores triggered state per user to track who should have the role
    """
    def __init__(self, bot):
        self.bot = bot
        self._presence_tasks: Dict[int, Set[int]] = {}  # guild_id -> set of user_ids being monitored

    def get_triggers(self, guild_id: int) -> dict:
        """Get all trigger words for a guild"""
        return dm.get_guild_data(guild_id, "trigger_roles", {})

    def get_triggered_users(self, guild_id: int) -> Set[int]:
        """Get set of user IDs who have triggered the role (should have it when online)"""
        triggered = dm.get_guild_data(guild_id, "triggered_users", {})
        return set(int(uid) for uid in triggered.get(str(guild_id), []))

    def add_triggered_user(self, guild_id: int, user_id: int):
        """Mark a user as having triggered the role"""
        triggered = dm.get_guild_data(guild_id, "triggered_users", {})
        guild_str = str(guild_id)
        if guild_str not in triggered:
            triggered[guild_str] = []
        if user_id not in triggered[guild_str]:
            triggered[guild_str].append(user_id)
            dm.update_guild_data(guild_id, "triggered_users", triggered)

    def remove_triggered_user(self, guild_id: int, user_id: int):
        """Remove a user from triggered list"""
        triggered = dm.get_guild_data(guild_id, "triggered_users", {})
        guild_str = str(guild_id)
        if guild_str in triggered and user_id in triggered[guild_str]:
            triggered[guild_str].remove(user_id)
            dm.update_guild_data(guild_id, "triggered_users", triggered)

    def add_trigger(self, guild_id: int, word: str, role_id: int):
        """Add a trigger word -> role mapping"""
        triggers = self.get_triggers(guild_id)
        triggers[word] = role_id
        dm.update_guild_data(guild_id, "trigger_roles", triggers)
        # Start presence monitoring for this guild if not already
        self._start_presence_monitoring(guild_id)

    def _start_presence_monitoring(self, guild_id: int):
        """Start monitoring presence changes for a guild"""
        if guild_id not in self._presence_tasks:
            self._presence_tasks[guild_id] = set()
            # Start the background presence check task
            asyncio.create_task(self._presence_monitor_loop(guild_id))

    def start_monitoring(self):
        """Start presence monitoring for all guilds that have trigger roles configured."""
        try:
            import os
            import glob
            for path in glob.glob(os.path.join("data", "guild_*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("trigger_roles"):
                        guild_id = int(os.path.basename(path).replace("guild_", "").replace(".json", ""))
                        self._start_presence_monitoring(guild_id)
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Trigger roles monitoring startup failed: {e}")

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """AI-driven setup for trigger role system"""
        guild = interaction.guild
        
        # Example: AI could pass params like:
        # {"word": "/Asclade", "role_name": "Picture Permissions", "role_color": "#FF0000"}
        
        trigger_word = params.get("word")
        role_name = params.get("role_name") 
        role_color = params.get("role_color", "#99AAB5")
        
        if not trigger_word or not role_name:
            await interaction.response.send_message("Missing required parameters: word and role_name", ephemeral=True)
            return False
            
        # 1. Create the role if it doesn't exist
        color = discord.Color(int(role_color.replace("#", ""), 16))
        role = discord.utils.get(guild.roles, name=role_name)
        
        if not role:
            role = await guild.create_role(name=role_name, color=color)
            
        # 2. Store the trigger word -> role ID mapping
        self.add_trigger(guild.id, trigger_word, role.id)
        
        # 3. Auto-documentation (MANDATORY for all systems)
        help_embed = discord.Embed(
            title="Trigger Roles System", 
            description="Assigns roles when users type specific trigger words. Role is removed when user goes offline.",
            color=discord.Color.blue()
        )
        help_embed.add_field(
            name="How it works", 
            value=f"When users type `{trigger_word}`, they automatically get the `{role_name}` role when online. Role is removed when they go offline.", 
            inline=False
        )
        help_embed.add_field(
            name="!triggers", 
            value="Lists all active trigger words and their assigned roles.", 
            inline=False
        )
        help_embed.add_field(
            name="!help triggerroles", 
            value="Shows this help message.", 
            inline=False
        )
        
        # Send help to the interaction channel (followup since we might have deferred)
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        # 4. Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["triggers"] = json.dumps({
            "command_type": "list_triggers"
        })
        custom_cmds["help triggerroles"] = json.dumps({
            "command_type": "help_embed",
            "title": "Trigger Roles System",
            "description": "Assigns roles when users type specific trigger words. Role is removed when user goes offline.",
            "fields": [
                {
                    "name": "How it works", 
                    "value": f"When users type `{trigger_word}`, they automatically get the `{role_name}` role when online. Role is removed when they go offline.", 
                    "inline": False
                },
                {
                    "name": "!triggers", 
                    "value": "Lists all active trigger words and their assigned roles.", 
                    "inline": False
                },
                {
                    "name": "!help triggerroles", 
                    "value": "Shows this help message.", 
                    "inline": False
                }
            ]
        })
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True

    async def _presence_monitor_loop(self, guild_id: int):
        """Background task to monitor presence changes and manage roles"""
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
            
        while not self.bot.is_closed():
            try:
                # Check if guild still exists
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    break
                    
                triggers = self.get_triggers(guild_id)
                if not triggers:
                    # No triggers, stop monitoring
                    if guild_id in self._presence_tasks:
                        del self._presence_tasks[guild_id]
                    break
                    
                triggered_users = self.get_triggered_users(guild_id)
                
                # Check each triggered user's presence
                for user_id in list(triggered_users):  # Copy to avoid modification during iteration
                    member = guild.get_member(user_id)
                    if not member:
                        # User left guild, remove from triggered list
                        self.remove_triggered_user(guild_id, user_id)
                        continue
                        
                    # Check if user should have the role based on any trigger
                    should_have_role = False
                    for word, role_id in triggers.items():
                        # We can't check message history easily, so we rely on explicit triggering
                        # For now, we'll check if they've ever triggered (stored in triggered_users)
                        # A more sophisticated system would check recent messages
                        if user_id in triggered_users:
                            should_have_role = True
                            break
                    
                    # Actually, let's simplify: if they're in triggered_users, they should have role when online
                    # But we need to know HOW they got in triggered_users - that's from handle_message
                    # So the logic is: if user is in triggered_users AND is online -> give role
                    #                           if user is in triggered_users AND is offline -> remove role
                    
                    # Get their roles for efficiency
                    member_roles = {r.id for r in member.roles}
                    
                    # Check each trigger role
                    for word, role_id in triggers.items():
                        role = guild.get_role(role_id)
                        if not role:
                            continue
                            
                        has_role = role.id in member_roles
                        is_online = member.status != discord.Status.offline
                        
                        # If they should have role (triggered) and are online -> give it
                        # If they should have role and are offline -> remove it
                        if user_id in triggered_users:
                            if is_online and not has_role:
                                await member.add_roles(role)
                                # Optional: log or debug
                            elif not is_online and has_role:
                                await member.remove_roles(role)
                                # Optional: log or debug
                                
            except Exception as e:
                logger.error("Error in presence monitor for guild %s: %s", guild_id, e)
                
            # Check every 30 seconds
            await asyncio.sleep(30)

    async def handle_message(self, message: discord.Message):
        """Handle trigger word detection in messages"""
        if message.author.bot or not message.guild:
            return
            
        guild_id = message.guild.id
        triggers = self.get_triggers(guild_id)
        if not triggers:
            return
            
        # Check if any trigger word is in the message
        triggered_role_id = None
        triggered_word = None
        
        for word, role_id in triggers.items():
            if word in message.content:
                triggered_role_id = role_id
                triggered_word = word
                break
                
        if triggered_role_id is not None:
            role = message.guild.get_role(triggered_role_id)
            if role:
                # Mark user as triggered (they should have role when online)
                self.add_triggered_user(guild_id, message.author.id)
                
                # If they're currently online, give them the role immediately
                if message.author.status != discord.Status.offline:
                    if role not in message.author.roles:
                        await message.author.add_roles(role)
                        await message.channel.send(f"✅ {message.author.mention}, you have been assigned the **{role.name}** role via trigger word '{triggered_word}'!")
                        
                # Start presence monitoring for this guild
                self._start_presence_monitoring(guild_id)
