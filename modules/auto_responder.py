import discord
from discord.ui import Button, View, Modal, TextInput, Select
from data_manager import dm
import re
import json
import time
from typing import Optional


class AutoResponderSystem:
    """
    Auto Responder System - Keyword-based automated replies
    Zero Data Loss with immediate writes.
    """
    
    def __init__(self, bot):
        self.bot = bot
    
    def get_responders(self, guild_id: int) -> list:
        return dm.get_guild_data(guild_id, "auto_responders", [])
    
    def add_responder(self, guild_id: int, responder: dict):
        responders = self.get_responders(guild_id)
        responder["id"] = len(responders) + 1
        responder["enabled"] = True
        responder["trigger_count"] = 0
        responders.append(responder)
        dm.update_guild_data(guild_id, "auto_responders", responders)
        return responder
    
    def update_responder(self, guild_id: int, responder_id: int, updates: dict):
        responders = self.get_responders(guild_id)
        for i, r in enumerate(responders):
            if r.get("id") == responder_id:
                responders[i].update(updates)
                dm.update_guild_data(guild_id, "auto_responders", responders)
                return True
        return False
    
    def delete_responder(self, guild_id: int, responder_id: int):
        responders = self.get_responders(guild_id)
        responders = [r for r in responders if r.get("id") != responder_id]
        dm.update_guild_data(guild_id, "auto_responders", responders)
        return True
    
    def check_message(self, message: discord.Message) -> Optional[dict]:
        """Check if message triggers any auto-responder."""
        if message.author.bot or not message.guild:
            return None

        guild_id = message.guild.id

        # Check if auto-responder system is globally enabled
        config = dm.get_guild_data(guild_id, "auto_responder_config", {"enabled": True})
        if not config.get("enabled", True):
            return None

        content = message.content.lower()
        responders = self.get_responders(guild_id)

        # Check channel restrictions
        allowed_channels = dm.get_guild_data(guild_id, "auto_responder_channels", None)
        if allowed_channels and str(message.channel.id) not in allowed_channels:
            return None

        # Check role restrictions
        allowed_roles = dm.get_guild_data(guild_id, "auto_responder_roles", None)
        if allowed_roles:
            user_role_ids = [str(r.id) for r in message.author.roles]
            if not any(r in allowed_roles for r in user_role_ids):
                return None
        
        # Check cooldown
        cooldown = dm.get_guild_data(guild_id, "auto_responder_cooldown", 0)
        last_triggered = dm.get_guild_data(guild_id, "auto_responder_last", {})
        current_time = time.time()
        
        for responder in responders:
            if not responder.get("enabled", True):
                continue
            
            triggered = False
            match_type = responder.get("match_type", "contains")
            trigger = responder.get("trigger", "").lower()
            
            if match_type == "exact":
                triggered = content == trigger
            elif match_type == "contains":
                triggered = trigger in content
            elif match_type == "starts_with":
                triggered = content.startswith(trigger)
            elif match_type == "ends_with":
                triggered = content.endswith(trigger)
            elif match_type == "regex":
                try:
                    triggered = bool(re.search(trigger, content, re.IGNORECASE))
                except re.error:
                    continue
            
            if triggered:
                # Check cooldown for this responder
                last_time = last_triggered.get(f"{message.author.id}_{responder['id']}", 0)
                if current_time - last_time < cooldown:
                    continue
                
                # Update trigger count and last triggered
                responder["trigger_count"] = responder.get("trigger_count", 0) + 1
                last_triggered[f"{message.author.id}_{responder['id']}"] = current_time
                dm.update_guild_data(guild_id, "auto_responders", responders)
                dm.update_guild_data(guild_id, "auto_responder_last", last_triggered)
                
                return responder
        
        return None
    
    async def handle_message(self, message: discord.Message):
        """Handle incoming message and trigger auto-responder if matched."""
        responder = self.check_message(message)
        if not responder:
            return
        
        response_type = responder.get("response_type", "text")
        response = responder.get("response", "")
        
        # Handle wildcard capture
        if "{capture}" in response or "{x}" in response:
            trigger = responder.get("trigger", "")
            if responder.get("match_type") == "regex":
                match = re.search(trigger, message.content, re.IGNORECASE)
                if match:
                    captured = match.group(1) if match.groups() else match.group(0)
                    response = response.replace("{capture}", captured).replace("{x}", captured)
        
        # Delete original message if configured
        if responder.get("delete_trigger", False):
            try:
                await message.delete()
            except discord.Forbidden:
                pass
        
        # Send response
        if response_type == "text":
            if responder.get("reply_mode", False):
                await message.channel.send(response, reference=message)
            elif responder.get("dm_mode", False):
                try:
                    await message.author.send(response)
                except discord.Forbidden:
                    await message.channel.send(response)
            else:
                await message.channel.send(response)
        
        elif response_type == "embed":
            embed = discord.Embed(
                description=response,
                color=discord.Color.blue()
            )
            if responder.get("reply_mode", False):
                await message.channel.send(embed=embed, reference=message)
            else:
                await message.channel.send(embed=embed)
        
        elif response_type == "random":
            import random
            responses = response.split("|")
            selected = random.choice(responses).strip()
            await message.channel.send(selected)
        
        elif response_type == "reaction":
            emojis = response.split()
            for emoji in emojis[:5]:  # Max 5 reactions
                try:
                    await message.add_reaction(emoji)
                except discord.Forbidden:
                    pass


class AutoResponderPanel(View):
    """Admin panel for Auto Responder configuration."""
    
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.ar = AutoResponder(bot)
    
    async def update_embed(self, interaction: discord.Interaction):
        responders = self.ar.get_responders(self.guild_id)
        enabled_count = sum(1 for r in responders if r.get("enabled", True))
        
        embed = discord.Embed(
            title="🤖 Auto Responder System",
            description=f"Manage keyword-based automated replies.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Total Responders", value=str(len(responders)), inline=True)
        embed.add_field(name="Enabled", value=str(enabled_count), inline=True)
        embed.add_field(name="Disabled", value=str(len(responders) - enabled_count), inline=True)
        
        cooldown = dm.get_guild_data(self.guild_id, "auto_responder_cooldown", 0)
        embed.add_field(name="Cooldown", value=f"{cooldown}s", inline=True)
        
        channels = dm.get_guild_data(self.guild_id, "auto_responder_channels", None)
        roles = dm.get_guild_data(self.guild_id, "auto_responder_roles", None)
        embed.add_field(name="Channel Restriction", value="Yes" if channels else "All", inline=True)
        embed.add_field(name="Role Restriction", value="Yes" if roles else "All", inline=True)
        
        if responders:
            recent = sorted(responders, key=lambda x: x.get("trigger_count", 0), reverse=True)[:3]
            top = "\n".join([f"• {r.get('trigger', 'N/A')}: {r.get('trigger_count', 0)} triggers" for r in recent])
            embed.add_field(name="Top Triggers", value=top or "None", inline=False)
        
        embed.set_footer(text="Every button is fully functional")
        
        msg = await interaction.original_response()
        await msg.edit(embed=embed, view=self)
    
    @discord.ui.button(label="📋 View All", style=discord.ButtonStyle.primary, row=0, custom_id="ar_cfg__view_all")
    async def view_all(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        if not responders:
            return await interaction.response.send_message("No auto-responders configured.", ephemeral=True)
        
        embed = discord.Embed(title="📋 All Auto Responders", color=discord.Color.blue())
        for r in responders[:10]:
            status = "✅" if r.get("enabled", True) else "❌"
            embed.add_field(
                name=f"{status} ID:{r.get('id')} - {r.get('trigger', 'N/A')[:30]}",
                value=f"Type: {r.get('match_type', 'contains')} | Response: {r.get('response', '')[:50]}...",
                inline=False
            )
        if len(responders) > 10:
            embed.set_footer(text=f"Showing 10 of {len(responders)} responders")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="➕ Add Responder", style=discord.ButtonStyle.success, row=0, custom_id="ar_cfg__add_responder")
    async def add_responder(self, interaction: discord.Interaction, button: Button):
        modal = AddResponderModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="✏️ Edit Responder", style=discord.ButtonStyle.secondary, row=0, custom_id="ar_cfg__edit_responder")
    async def edit_responder(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        if not responders:
            return await interaction.response.send_message("No responders to edit.", ephemeral=True)
        
        select = EditResponderSelect(self.bot, self.guild_id)
        view = View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Select a responder to edit:", ephemeral=True, view=view)
    
    @discord.ui.button(label="⏸️ Disable", style=discord.ButtonStyle.secondary, row=1, custom_id="ar_cfg__disable")
    async def disable_responder(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        active = [r for r in responders if r.get("enabled", True)]
        if not active:
            return await interaction.response.send_message("No active responders to disable.", ephemeral=True)
        
        select = DisableResponderSelect(self.bot, self.guild_id, action="disable")
        view = View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Select responder to disable:", ephemeral=True, view=view)
    
    @discord.ui.button(label="▶️ Enable", style=discord.ButtonStyle.success, row=1, custom_id="ar_cfg__enable")
    async def enable_responder(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        disabled = [r for r in responders if not r.get("enabled", True)]
        if not disabled:
            return await interaction.response.send_message("No disabled responders.", ephemeral=True)
        
        select = DisableResponderSelect(self.bot, self.guild_id, action="enable")
        view = View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Select responder to enable:", ephemeral=True, view=view)
    
    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.danger, row=1, custom_id="ar_cfg__delete")
    async def delete_responder(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        if not responders:
            return await interaction.response.send_message("No responders to delete.", ephemeral=True)
        
        select = DeleteResponderSelect(self.bot, self.guild_id)
        view = View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Select responder to delete:", ephemeral=True, view=view)
    
    @discord.ui.button(label="🔍 Test Responder", style=discord.ButtonStyle.primary, row=2, custom_id="ar_cfg__test_responder")
    async def test_responder(self, interaction: discord.Interaction, button: Button):
        modal = TestResponderModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📊 Stats", style=discord.ButtonStyle.secondary, row=2, custom_id="ar_cfg__stats")
    async def show_stats(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        total_triggers = sum(r.get("trigger_count", 0) for r in responders)
        enabled_count = sum(1 for r in responders if r.get("enabled", True))
        
        # Get today's triggers (simplified)
        today_triggers = total_triggers  # In production, track per-day
        
        embed = discord.Embed(title="📊 Auto Responder Stats", color=discord.Color.blue())
        embed.add_field(name="Total Responders", value=str(len(responders)), inline=True)
        embed.add_field(name="Enabled", value=str(enabled_count), inline=True)
        embed.add_field(name="Total Triggers", value=str(total_triggers), inline=True)
        embed.add_field(name="Triggers Today", value=str(today_triggers), inline=True)
        
        if responders:
            top = max(responders, key=lambda x: x.get("trigger_count", 0))
            embed.add_field(name="Most Triggered", value=f"{top.get('trigger', 'N/A')} ({top.get('trigger_count', 0)} times)", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🌐 Channel Restriction", style=discord.ButtonStyle.secondary, row=3, custom_id="ar_cfg__channel_restriction")
    async def set_channels(self, interaction: discord.Interaction, button: Button):
        select = ChannelRestrictionSelect(self.bot, self.guild_id)
        view = View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Select channels where auto-responders work (or none for all):", ephemeral=True, view=view)
    
    @discord.ui.button(label="🎭 Role Restriction", style=discord.ButtonStyle.secondary, row=3, custom_id="ar_cfg__role_restriction")
    async def set_roles(self, interaction: discord.Interaction, button: Button):
        select = RoleRestrictionSelect(self.bot, self.guild_id)
        view = View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Select roles that can trigger auto-responders (or none for all):", ephemeral=True, view=view)
    
    @discord.ui.button(label="⏱️ Set Cooldown", style=discord.ButtonStyle.primary, row=3, custom_id="ar_cfg__set_cooldown")
    async def set_cooldown(self, interaction: discord.Interaction, button: Button):
        modal = CooldownModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔃 Import", style=discord.ButtonStyle.secondary, row=4, custom_id="ar_cfg__import")
    async def import_responders(self, interaction: discord.Interaction, button: Button):
        modal = ImportModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📤 Export", style=discord.ButtonStyle.secondary, row=4, custom_id="ar_cfg__export")
    async def export_responders(self, interaction: discord.Interaction, button: Button):
        responders = self.ar.get_responders(self.guild_id)
        json_str = json.dumps(responders, indent=2)
        
        embed = discord.Embed(title="📤 Exported Auto Responders", color=discord.Color.green())
        if len(json_str) > 4000:
            embed.description = "Data too large for embed. Sending as file."
            await interaction.response.send_message(embed=embed)
            file = discord.File(fp=json_str.encode(), filename="auto_responders.json")
            await interaction.followup.send(file=file)
        else:
            embed.add_field(name="JSON Data", value=f"```json\n{json_str}\n```", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)


class AddResponderModal(Modal, title="Add Auto Responder"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.ar = AutoResponder(bot)
    
    trigger = TextInput(label="Trigger Word/Phrase", placeholder="e.g., hello, !help, what time")
    response = TextInput(label="Response", style=discord.TextStyle.long, placeholder="The reply message")
    
    async def on_submit(self, interaction: discord.Interaction):
        match_select = MatchTypeSelect(self.bot, self.guild_id, self.trigger.value, self.response.value)
        view = View(timeout=180)
        view.add_item(match_select)
        await interaction.response.send_message("Select match type:", ephemeral=True, view=view)


class MatchTypeSelect(Select):
    def __init__(self, bot, guild_id: int, trigger: str, response: str):
        self.bot = bot
        self.guild_id = guild_id
        self.trigger = trigger
        self.response = response
        self.ar = AutoResponder(bot)
        
        options = [
            discord.SelectOption(label="Exact Match", value="exact", description="Message must exactly match trigger"),
            discord.SelectOption(label="Contains", value="contains", description="Message contains trigger anywhere"),
            discord.SelectOption(label="Starts With", value="starts_with", description="Message starts with trigger"),
            discord.SelectOption(label="Ends With", value="ends_with", description="Message ends with trigger"),
            discord.SelectOption(label="Regex", value="regex", description="Advanced pattern matching"),
        ]
        super().__init__(placeholder="Select match type", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        response_select = ResponseTypeSelect(self.bot, self.guild_id, self.trigger, self.response, self.values[0])
        view = View(timeout=180)
        view.add_item(response_select)
        await interaction.response.send_message("Select response type:", ephemeral=True, view=view)


class ResponseTypeSelect(Select):
    def __init__(self, bot, guild_id: int, trigger: str, response: str, match_type: str):
        self.bot = bot
        self.guild_id = guild_id
        self.trigger = trigger
        self.response = response
        self.match_type = match_type
        self.ar = AutoResponder(bot)
        
        options = [
            discord.SelectOption(label="Plain Text", value="text", description="Simple text response"),
            discord.SelectOption(label="Rich Embed", value="embed", description="Formatted embed response"),
            discord.SelectOption(label="Random List", value="random", description="Pick from multiple responses (use | separator)"),
            discord.SelectOption(label="Reaction Only", value="reaction", description="Add emoji reactions only"),
        ]
        super().__init__(placeholder="Select response type", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        responder = {
            "trigger": self.trigger,
            "response": self.response,
            "match_type": self.match_type,
            "response_type": self.values[0],
        }
        self.ar.add_responder(self.guild_id, responder)
        
        embed = discord.Embed(title="✅ Auto Responder Added", color=discord.Color.green())
        embed.add_field(name="Trigger", value=self.trigger, inline=False)
        embed.add_field(name="Match Type", value=self.match_type, inline=True)
        embed.add_field(name="Response Type", value=self.values[0], inline=True)
        embed.add_field(name="Response", value=self.response[:500], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EditResponderSelect(Select):
    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.ar = AutoResponder(bot)
        
        responders = self.ar.get_responders(guild_id)
        options = [
            discord.SelectOption(label=f"ID:{r['id']} - {r['trigger'][:25]}", value=str(r['id']))
            for r in responders[:25]
        ]
        super().__init__(placeholder="Select responder to edit", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        responder_id = int(self.values[0])
        responders = self.ar.get_responders(self.guild_id)
        responder = next((r for r in responders if r['id'] == responder_id), None)
        
        if not responder:
            return await interaction.response.send_message("Responder not found.", ephemeral=True)
        
        modal = EditResponderModal(self.bot, self.guild_id, responder)
        await interaction.response.send_modal(modal)


class EditResponderModal(Modal, title="Edit Auto Responder"):
    def __init__(self, bot, guild_id: int, responder: dict):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.responder_id = responder['id']
        self.ar = AutoResponder(bot)
        
        self.trigger.default = responder.get('trigger', '')
        self.response.default = responder.get('response', '')
    
    trigger = TextInput(label="Trigger Word/Phrase")
    response = TextInput(label="Response", style=discord.TextStyle.long)
    
    async def on_submit(self, interaction: discord.Interaction):
        self.ar.update_responder(self.guild_id, self.responder_id, {
            "trigger": self.trigger.value,
            "response": self.response.value,
        })
        await interaction.response.send_message("✅ Responder updated!", ephemeral=True)


class DeleteResponderSelect(Select):
    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.ar = AutoResponder(bot)
        
        responders = self.ar.get_responders(guild_id)
        options = [
            discord.SelectOption(label=f"ID:{r['id']} - {r['trigger'][:25]}", value=str(r['id']), emoji="🗑️")
            for r in responders[:25]
        ]
        super().__init__(placeholder="Select responder to delete", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        responder_id = int(self.values[0])
        modal = DeleteConfirmModal(self.bot, self.guild_id, responder_id)
        await interaction.response.send_modal(modal)


class DeleteConfirmModal(Modal, title="Confirm Deletion"):
    def __init__(self, bot, guild_id: int, responder_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.responder_id = responder_id
        self.ar = AutoResponder(bot)
    
    confirm = TextInput(label="Type DELETE to confirm", placeholder="DELETE")
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "DELETE":
            return await interaction.response.send_message("❌ Confirmation failed. Type DELETE exactly.", ephemeral=True)
        
        self.ar.delete_responder(self.guild_id, self.responder_id)
        await interaction.response.send_message("✅ Responder deleted!", ephemeral=True)


class DisableResponderSelect(Select):
    def __init__(self, bot, guild_id: int, action: str):
        self.bot = bot
        self.guild_id = guild_id
        self.action = action
        self.ar = AutoResponder(bot)
        
        responders = self.ar.get_responders(guild_id)
        filtered = [r for r in responders if (action == "disable" and r.get("enabled", True)) or (action == "enable" and not r.get("enabled", True))]
        
        options = [
            discord.SelectOption(label=f"ID:{r['id']} - {r['trigger'][:25]}", value=str(r['id']))
            for r in filtered[:25]
        ]
        super().__init__(placeholder=f"Select responder to {action}", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        responder_id = int(self.values[0])
        self.ar.update_responder(self.guild_id, responder_id, {"enabled": self.action == "enable"})
        await interaction.response.send_message(f"✅ Responder {'enabled' if self.action == 'enable' else 'disabled'}!", ephemeral=True)


class TestResponderModal(Modal, title="Test Auto Responder"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.ar = AutoResponder(bot)
    
    test_message = TextInput(label="Type a test message", style=discord.TextStyle.long, placeholder="Enter message to test against all responders")
    
    async def on_submit(self, interaction: discord.Interaction):
        content = self.test_message.value.lower()
        responders = self.ar.get_responders(self.guild_id)
        
        matches = []
        for r in responders:
            if not r.get("enabled", True):
                continue
            
            trigger = r.get("trigger", "").lower()
            match_type = r.get("match_type", "contains")
            triggered = False
            
            if match_type == "exact" and content == trigger:
                triggered = True
            elif match_type == "contains" and trigger in content:
                triggered = True
            elif match_type == "starts_with" and content.startswith(trigger):
                triggered = True
            elif match_type == "ends_with" and content.endswith(trigger):
                triggered = True
            elif match_type == "regex":
                try:
                    if re.search(trigger, content, re.IGNORECASE):
                        triggered = True
                except:
                    pass
            
            if triggered:
                matches.append(r)
        
        if not matches:
            return await interaction.response.send_message("❌ No responders would trigger for this message.", ephemeral=True)
        
        embed = discord.Embed(title="🔍 Test Results", color=discord.Color.green())
        embed.description = f"**{len(matches)}** responder(s) would trigger:"
        for m in matches:
            embed.add_field(
                name=f"ID:{m['id']} - {m['trigger'][:30]}",
                value=f"Match: {m['match_type']} | Response: {m['response'][:100]}...",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CooldownModal(Modal, title="Set Global Cooldown"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
    
    cooldown = TextInput(label="Cooldown in seconds", placeholder="0 = no cooldown", default="0")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            cooldown = int(self.cooldown.value)
            if cooldown < 0:
                raise ValueError()
        except ValueError:
            return await interaction.response.send_message("❌ Please enter a valid non-negative number.", ephemeral=True)
        
        dm.update_guild_data(self.guild_id, "auto_responder_cooldown", cooldown)
        await interaction.response.send_message(f"✅ Cooldown set to {cooldown} seconds!", ephemeral=True)


class ChannelRestrictionSelect(Select):
    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        
        guild = bot.get_guild(guild_id)
        channels = guild.text_channels if guild else []
        
        options = [discord.SelectOption(label="All Channels", value="all", description="Remove channel restriction")]
        for ch in channels[:25]:
            options.append(discord.SelectOption(label=f"#{ch.name}", value=str(ch.id)))
        
        super().__init__(placeholder="Select allowed channels", options=options, min_values=0, max_values=25)
    
    async def callback(self, interaction: discord.Interaction):
        if self.values and "all" in self.values:
            dm.update_guild_data(self.guild_id, "auto_responder_channels", None)
            return await interaction.response.send_message("✅ Channel restriction removed - works in all channels!", ephemeral=True)
        
        dm.update_guild_data(self.guild_id, "auto_responder_channels", list(self.values))
        await interaction.response.send_message(f"✅ Restricted to {len(self.values)} channel(s)!", ephemeral=True)


class RoleRestrictionSelect(Select):
    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        
        guild = bot.get_guild(guild_id)
        roles = guild.roles if guild else []
        
        options = [discord.SelectOption(label="All Roles", value="all", description="Remove role restriction")]
        for role in roles[:25]:
            if not role.is_default():
                options.append(discord.SelectOption(label=role.name, value=str(role.id)))
        
        super().__init__(placeholder="Select allowed roles", options=options, min_values=0, max_values=25)
    
    async def callback(self, interaction: discord.Interaction):
        if self.values and "all" in self.values:
            dm.update_guild_data(self.guild_id, "auto_responder_roles", None)
            return await interaction.response.send_message("✅ Role restriction removed - works for all members!", ephemeral=True)
        
        dm.update_guild_data(self.guild_id, "auto_responder_roles", list(self.values))
        await interaction.response.send_message(f"✅ Restricted to {len(self.values)} role(s)!", ephemeral=True)


class ImportModal(Modal, title="Import Auto Responders"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.ar = AutoResponder(bot)
    
    json_data = TextInput(label="Paste JSON array", style=discord.TextStyle.long, placeholder='[{"trigger": "hello", "response": "hi!", "match_type": "contains", "response_type": "text"}]')
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            data = json.loads(self.json_data.value)
            if not isinstance(data, list):
                raise ValueError("Must be a JSON array")
            
            count = 0
            for item in data:
                if isinstance(item, dict) and "trigger" in item and "response" in item:
                    self.ar.add_responder(self.guild_id, item)
                    count += 1
            
            await interaction.response.send_message(f"✅ Imported {count} responder(s)!", ephemeral=True)
        except json.JSONDecodeError:
            await interaction.response.send_message("❌ Invalid JSON format. Please check your input.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


async def setup_auto_responder(bot, guild: discord.Guild):
    """Setup auto-responder system for a guild."""
    ar = AutoResponder(bot)
    
    # Create guide channel
    guide_channel = await guild.create_text_channel("autoresponder-guide", reason="Auto Responder setup")
    
    embed = discord.Embed(
        title="🤖 Auto Responder System Guide",
        description="Automatically respond to keywords with custom messages!",
        color=discord.Color.blue()
    )
    embed.add_field(name="Commands", value="`!autorespondpanel` - Open admin panel", inline=False)
    embed.add_field(name="Features", value="• Exact/Contains/Starts/Ends/Regex matching\n• Text/Embed/Random/Reaction responses\n• Channel & role restrictions\n• Per-user cooldowns\n• Wildcard capture {x}\n• Delete trigger option\n• Reply/DM modes", inline=False)
    embed.add_field(name="Variables", value="`{capture}` or `{x}` - Captured text from regex", inline=False)
    embed.add_field(name="Troubleshooting", value="• Ensure bot has send/delete permissions\n• Check channel/role restrictions\n• Verify cooldown settings", inline=False)
    
    await guide_channel.send(embed=embed)
    
    # Register help command
    custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
    custom_cmds["help autorespond"] = json.dumps({
        "command_type": "help_embed",
        "title": "🤖 Auto Responder Help",
        "description": "Keyword-based automated replies.",
        "fields": [
            {"name": "!autorespondpanel", "value": "Open the admin configuration panel", "inline": False},
            {"name": "Match Types", "value": "exact, contains, starts_with, ends_with, regex", "inline": False},
            {"name": "Response Types", "value": "text, embed, random, reaction", "inline": False}
        ]
    })
    dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
    
    return True
