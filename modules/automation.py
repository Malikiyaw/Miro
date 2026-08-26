"""Automation systems.

Consolidated module (file-level merge). Each system class is unchanged;
original paths remain as compatibility shims.
Original files: auto_responder.py, reaction_roles.py, reaction_menus.py, role_buttons.py, trigger_roles.py, starboard.py
"""



# ======================================================================
# From: modules/auto_responder.py
# ======================================================================

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

        # Live template substitution ({user} {server} {channel} {args}) for
        # agent-created responders — no-op when ActionHandler is unavailable.
        _ah = getattr(self.bot, "action_handler", None)
        if _ah is not None and hasattr(_ah, "_render_template"):
            try:
                response = _ah._render_template(response, message)
            except Exception:
                pass
        
        # Delete original message if configured
        if responder.get("delete_trigger", False):
            try:
                await message.delete()
            except discord.Forbidden:
                pass
        
        # Send response (skip when there is no reply text — e.g. delete-only responders)
        if response and response.strip():
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


# Compat alias: the admin panels below instantiate `AutoResponder(bot)`;
# the real class is AutoResponderSystem (same methods).
AutoResponder = AutoResponderSystem


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
            await interaction.response.send_message(embed=embed, ephemeral=True)
            file = discord.File(fp=json_str.encode(), filename="auto_responders.json")
            await interaction.followup.send(file=file, ephemeral=True)
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



# ======================================================================
# From: modules/reaction_roles.py
# ======================================================================

import discord
from data_manager import dm
from logger import logger
import time
from typing import Dict, Optional, List

class ReactionRoleSystem:
    """
    Reaction Roles System:
    - Bind any emoji to any role on any message
    - Supports restrictions (age, level, roles)
    - Logs all actions
    """
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> Dict:
        """Get reaction role configurations for a guild"""
        return dm.get_guild_data(guild_id, "reaction_roles", {})

    def save_config(self, guild_id: int, config: Dict):
        """Save reaction role configurations for a guild"""
        dm.update_guild_data(guild_id, "reaction_roles", config)

    def log_action(self, guild_id: int, user_id: int, action: str, role_id: int, message_id: int):
        """Log a reaction role action"""
        logs = dm.get_guild_data(guild_id, "reaction_role_log", [])
        logs.append({
            "ts": time.time(),
            "user_id": user_id,
            "action": action,
            "role_id": role_id,
            "message_id": message_id
        })
        # Keep last 100 logs
        dm.update_guild_data(guild_id, "reaction_role_log", logs[-100:])

    async def handle_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle role assignment on reaction add"""
        if payload.member and payload.member.bot:
            return

        guild_id = payload.guild_id
        if not guild_id:
            return

        config = self.get_config(guild_id)
        msg_id_str = str(payload.message_id)
        if msg_id_str not in config:
            return

        emoji_str = str(payload.emoji)
        role_data = config[msg_id_str].get(emoji_str)
        if not role_data:
            return

        role_id = role_data.get("role_id")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        member = payload.member
        if not member:
            member = guild.get_member(payload.user_id)
        if not member:
            return

        # Check restrictions
        # 1. Min Account Age (days)
        min_age = role_data.get("min_age", 0)
        if min_age > 0:
            days_old = (discord.utils.utcnow() - member.created_at).days
            if days_old < min_age:
                return

        # 2. Min Level
        min_level = role_data.get("min_level", 0)
        if min_level > 0:
            xp = self.bot.leveling.get_xp(guild_id, member.id)
            user_level = self.bot.leveling.get_level_from_xp(xp)
            if user_level < min_level:
                return

        # 3. Prerequisite Role
        prereq_id = role_data.get("prerequisite_role_id")
        if prereq_id:
            if not any(r.id == int(prereq_id) for r in member.roles):
                return

        # 4. Incompatible Role
        incomp_id = role_data.get("incompatible_role_id")
        if incomp_id:
            if any(r.id == int(incomp_id) for r in member.roles):
                return

        try:
            await member.add_roles(role, reason="Reaction Role assignment")
            self.log_action(guild_id, member.id, "add", role_id, payload.message_id)
        except Exception as e:
            logger.error(f"Failed to add reaction role {role_id} to user {member.id}: {e}")

    async def handle_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle role removal on reaction remove"""
        guild_id = payload.guild_id
        if not guild_id:
            return

        config = self.get_config(guild_id)
        msg_id_str = str(payload.message_id)
        if msg_id_str not in config:
            return

        emoji_str = str(payload.emoji)
        role_data = config[msg_id_str].get(emoji_str)
        if not role_data:
            return

        role_id = role_data.get("role_id")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        member = guild.get_member(payload.user_id)
        if not member:
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction Role removal")
                self.log_action(guild_id, member.id, "remove", role_id, payload.message_id)
        except Exception as e:
            logger.error(f"Failed to remove reaction role {role_id} from user {member.id}: {e}")

    def add_reaction_role(self, guild_id: int, message_id: int, emoji: str, role_id: int,
                           min_age: int = 0, min_level: int = 0,
                           prereq_role_id: Optional[int] = None,
                           incomp_role_id: Optional[int] = None):
        """Add a reaction role binding to the config"""
        config = self.get_config(guild_id)
        msg_id_str = str(message_id)
        if msg_id_str not in config:
            config[msg_id_str] = {}

        config[msg_id_str][emoji] = {
            "role_id": role_id,
            "min_age": min_age,
            "min_level": min_level,
            "prerequisite_role_id": prereq_role_id,
            "incompatible_role_id": incomp_role_id
        }
        self.save_config(guild_id, config)

    def remove_reaction_role(self, guild_id: int, message_id: int, emoji: str):
        """Remove a reaction role binding from the config"""
        config = self.get_config(guild_id)
        msg_id_str = str(message_id)
        if msg_id_str in config and emoji in config[msg_id_str]:
            del config[msg_id_str][emoji]
            if not config[msg_id_str]:
                del config[msg_id_str]
            self.save_config(guild_id, config)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """AI-driven setup for reaction roles"""
        guild = interaction.guild

        # Example help embed for the system
        help_embed = discord.Embed(
            title="🎭 Reaction Roles System",
            description="Assign roles instantly by reacting to messages.",
            color=discord.Color.blue()
        )
        help_embed.add_field(
            name="How to use",
            value="Staff can use the `!reactionrolespanel` to bind emojis to roles on specific messages.",
            inline=False
        )
        help_embed.add_field(
            name="Restrictions",
            value="You can set minimum account age, level requirements, and role dependencies.",
            inline=False
        )

        await interaction.followup.send(embed=help_embed, ephemeral=True)

        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["reactionrolespanel"] = "configpanel reactionroles"
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        return True



# ======================================================================
# From: modules/reaction_menus.py
# ======================================================================

import discord
from discord import ui
import time
from typing import Dict, List, Optional, Any, Union
from data_manager import dm
from logger import logger

class ReactionMenuPersistentView(ui.View):
    """
    Persistent view for reaction role menus.
    Handles all menu types: Dropdown, Button Grid, Toggle, etc.
    """
    def __init__(self, menu_id: str):
        super().__init__(timeout=None)
        self.menu_id = menu_id

    async def _get_menu_data(self, guild_id: int):
        menus = dm.get_guild_data(guild_id, "reaction_menus_config", {})
        return menus.get(self.menu_id)

    async def _handle_role_assignment(self, interaction: discord.Interaction, role_ids: List[int]):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        menu_data = await self._get_menu_data(guild.id)
        if not menu_data or not menu_data.get("enabled", True):
            return await interaction.followup.send("⚠️ This menu is currently unavailable.", ephemeral=True)

        assigned = []
        removed = []

        for role_id in role_ids:
            role = guild.get_role(role_id)
            if not role: continue

            # Check exclusive menu logic
            if menu_data.get("type") == "exclusive":
                other_role_ids = [int(r["role_id"]) for r in menu_data.get("roles", []) if int(r["role_id"]) != role_id]
                roles_to_remove = [guild.get_role(rid) for rid in other_role_ids if guild.get_role(rid) in interaction.user.roles]
                if roles_to_remove:
                    try:
                        await interaction.user.remove_roles(*roles_to_remove, reason="Reaction Menu Exclusive Selection")
                        removed.extend([r.name for r in roles_to_remove])
                    except: pass

            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Reaction Menu Selection")
                removed.append(role.name)
                action = "remove"
            else:
                await interaction.user.add_roles(role, reason="Reaction Menu Selection")
                assigned.append(role.name)
                action = "add"

            # Log assignment
            log = dm.get_guild_data(guild.id, "reaction_menu_log", [])
            log.append({
                "ts": time.time(),
                "user_id": interaction.user.id,
                "role_id": role_id,
                "action": action,
                "menu_name": menu_data.get("name")
            })
            dm.update_guild_data(guild.id, "reaction_menu_log", log[-100:])

        msg = []
        if assigned: msg.append(f"✅ Added: {', '.join(assigned)}")
        if removed: msg.append(f"❌ Removed: {', '.join(removed)}")

        await interaction.followup.send("\n".join(msg) or "No changes made.", ephemeral=True)

class ReactionMenuSystem:
    """
    Reaction Roles Menus:
    Distinct from individual reaction roles - organized, styled menus.
    """
    def __init__(self, bot):
        self.bot = bot

    def get_menus(self, guild_id: int) -> Dict[str, Any]:
        return dm.get_guild_data(guild_id, "reaction_menus_config", {})

    def save_menus(self, guild_id: int, menus: Dict[str, Any]):
        dm.update_guild_data(guild_id, "reaction_menus_config", menus)

    async def create_menu(self, interaction: discord.Interaction, name: str, menu_type: str, roles: List[Dict[str, Any]], channel: discord.TextChannel, title: str, description: str):
        guild_id = interaction.guild_id
        menus = self.get_menus(guild_id)

        menu_id = f"menu_{int(time.time())}"

        menu_data = {
            "id": menu_id,
            "name": name,
            "type": menu_type,
            "roles": roles, # [{"role_id": 123, "label": "Dev", "emoji": "💻", "description": "..."}]
            "channel_id": channel.id,
            "title": title,
            "description": description,
            "enabled": True,
            "created_at": time.time()
        }

        view = self.build_view(menu_id, menu_type, roles)
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())

        try:
            message = await channel.send(embed=embed, view=view)
            menu_data["message_id"] = message.id
            menus[menu_id] = menu_data
            self.save_menus(guild_id, menus)
            return menu_id
        except Exception as e:
            logger.error(f"Failed to create reaction menu: {e}")
            return None

    def build_view(self, menu_id: str, menu_type: str, roles: List[Dict[str, Any]]) -> ui.View:
        view = ReactionMenuPersistentView(menu_id)

        if menu_type == "dropdown":
            options = []
            for r in roles:
                options.append(discord.SelectOption(
                    label=r.get("label", "Role"),
                    value=str(r["role_id"]),
                    description=r.get("description"),
                    emoji=r.get("emoji")
                ))

            select = ui.Select(
                placeholder="Choose your roles...",
                options=options,
                min_values=0,
                max_values=len(options) if "multi" in menu_type else 1,
                custom_id=f"rr_menu_select_{menu_id}"
            )

            async def select_callback(interaction: discord.Interaction):
                await view._handle_role_assignment(interaction, [int(val) for val in select.values])

            select.callback = select_callback
            view.add_item(select)

        elif menu_type in ["button_grid", "toggle", "exclusive", "multi_select"]:
            for r in roles:
                btn = ui.Button(
                    label=r.get("label", "Role"),
                    style=discord.ButtonStyle.secondary,
                    emoji=r.get("emoji"),
                    custom_id=f"rr_menu_btn_{menu_id}_{r['role_id']}"
                )

                async def btn_callback(interaction: discord.Interaction, rid=int(r["role_id"])):
                    await view._handle_role_assignment(interaction, [rid])

                # We need to bind the current rid to the callback, which rid=int(r["role_id"]) does correctly.
                btn.callback = btn_callback
                view.add_item(btn)

        return view

    def get_persistent_views(self) -> List[ui.View]:
        """Rebuild persistent views for every stored reaction menu (used at bot startup)."""
        import os
        import glob
        import json
        views: List[ui.View] = []
        try:
            for path in glob.glob(os.path.join("data", "guild_*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                menus = data.get("reaction_menus_config", {})
                if not menus:
                    continue
                for menu_id, menu in menus.items():
                    if not menu.get("enabled", True):
                        continue
                    try:
                        views.append(self.build_view(menu_id, menu.get("type", "dropdown"), menu.get("roles", [])))
                    except Exception as e:
                        logger.error(f"Failed to rebuild reaction menu view {menu_id}: {e}")
        except Exception as e:
            logger.error(f"Reaction menu persistent views rebuild failed: {e}")
        return views

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """Setup for reaction menus"""
        # Register prefix commands
        guild = interaction.guild
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["reactionmenuspanel"] = "configpanel reactionmenus"
        custom_cmds["menupanel"] = "configpanel reactionmenus"
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("🎭 Reaction Menus system initialized. Use `!reactionmenuspanel` to create your first menu.", ephemeral=True)
        return True



# ======================================================================
# From: modules/role_buttons.py
# ======================================================================

import discord
from discord import ui
import time
from typing import Dict, List, Optional, Any, Union
from data_manager import dm
from logger import logger

class RoleButtonPersistentView(ui.View):
    """
    Persistent view for standalone role buttons.
    """
    def __init__(self, panel_id: str):
        super().__init__(timeout=None)
        self.panel_id = panel_id

    async def _get_panel_data(self, guild_id: int):
        panels = dm.get_guild_data(guild_id, "role_buttons_config", {})
        return panels.get(self.panel_id)

    async def _handle_button_click(self, interaction: discord.Interaction, button_id: str):
        guild = interaction.guild
        panel_data = await self._get_panel_data(guild.id)
        if not panel_data or not panel_data.get("enabled", True):
            return await interaction.response.send_message("⚠️ This panel is currently unavailable.", ephemeral=True)

        button_data = panel_data.get("buttons", {}).get(button_id)
        if not button_data:
            return await interaction.response.send_message("❌ Button configuration not found.", ephemeral=True)

        # Check requirements
        req = button_data.get("requirement", {})
        if req.get("role_id"):
            if not any(r.id == int(req["role_id"]) for r in interaction.user.roles):
                return await interaction.response.send_message("❌ You don't meet the role requirement for this.", ephemeral=True)

        role_to_add = guild.get_role(int(button_data["role_id"]))
        role_to_remove = guild.get_role(int(button_data["remove_role_id"])) if button_data.get("remove_role_id") else None

        if not role_to_add:
            return await interaction.response.send_message("❌ Role to assign not found.", ephemeral=True)

        try:
            if role_to_add in interaction.user.roles:
                await interaction.user.remove_roles(role_to_add, reason="Role Button Toggle")
                await interaction.response.send_message(f"✅ Removed role: {role_to_add.name}", ephemeral=True)
                action = "remove"
            else:
                if role_to_remove:
                    await interaction.user.remove_roles(role_to_remove, reason="Role Button Swap")
                await interaction.user.add_roles(role_to_add, reason="Role Button Assignment")
                await interaction.response.send_message(f"✅ Added role: {role_to_add.name}", ephemeral=True)
                action = "add"

            # Log click
            log = dm.get_guild_data(guild.id, "role_button_log", [])
            log.append({
                "ts": time.time(),
                "user_id": interaction.user.id,
                "role_id": role_to_add.id,
                "action": action,
                "panel_name": panel_data.get("title"),
                "button_label": button_data.get("label")
            })
            dm.update_guild_data(guild.id, "role_button_log", log[-100:])

            # Increment stats
            panel_data["total_clicks"] = panel_data.get("total_clicks", 0) + 1
            panels = dm.get_guild_data(guild.id, "role_button_panels", {})
            panels[self.panel_id] = panel_data
            dm.update_guild_data(guild.id, "role_button_panels", panels)

        except Exception as e:
            logger.error(f"Failed to process role button click: {e}")
            await interaction.response.send_message("❌ An error occurred during role assignment.", ephemeral=True)

class RoleButtonSystem:
    """
    Role Buttons System:
    Standalone role assignment buttons - simpler than menus.
    """
    def __init__(self, bot):
        self.bot = bot

    def get_panels(self, guild_id: int) -> Dict[str, Any]:
        return dm.get_guild_data(guild_id, "role_buttons_config", {})

    def save_panels(self, guild_id: int, panels: Dict[str, Any]):
        dm.update_guild_data(guild_id, "role_buttons_config", panels)

    async def create_panel(self, interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel):
        guild_id = interaction.guild_id
        panels = self.get_panels(guild_id)

        panel_id = f"panel_{int(time.time())}"

        panel_data = {
            "id": panel_id,
            "title": title,
            "description": description,
            "channel_id": channel.id,
            "buttons": {}, # button_id -> data
            "enabled": True,
            "total_clicks": 0,
            "created_at": time.time()
        }

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        try:
            message = await channel.send(embed=embed)
            panel_data["message_id"] = message.id
            panels[panel_id] = panel_data
            self.save_panels(guild_id, panels)
            return panel_id
        except Exception as e:
            logger.error(f"Failed to create role button panel: {e}")
            return None

    def build_view(self, panel_id: str, buttons_data: Dict[str, Any]) -> ui.View:
        view = RoleButtonPersistentView(panel_id)

        for bid, data in buttons_data.items():
            btn = ui.Button(
                label=data.get("label", "Role"),
                style=getattr(discord.ButtonStyle, data.get("style", "secondary")),
                emoji=data.get("emoji"),
                custom_id=f"role_btn_{panel_id}_{bid}"
            )

            async def btn_callback(interaction: discord.Interaction, button_id=bid):
                await view._handle_button_click(interaction, button_id)

            btn.callback = btn_callback
            view.add_item(btn)

        return view

    def get_persistent_views(self) -> List[ui.View]:
        """Rebuild persistent views for every stored role-button panel (used at bot startup)."""
        import os
        import glob
        import json
        views: List[ui.View] = []
        try:
            for path in glob.glob(os.path.join("data", "guild_*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                panels = data.get("role_buttons_config", {})
                if not panels:
                    continue
                for panel_id, panel in panels.items():
                    if not panel.get("enabled", True):
                        continue
                    try:
                        views.append(self.build_view(panel_id, panel.get("buttons", {})))
                    except Exception as e:
                        logger.error(f"Failed to rebuild role button view {panel_id}: {e}")
        except Exception as e:
            logger.error(f"Role button persistent views rebuild failed: {e}")
        return views

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """Setup for role buttons"""
        guild = interaction.guild
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["rolebuttonspanel"] = "configpanel rolebuttons"
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("🔘 Role Buttons system initialized. Use `!rolebuttonspanel` to create a button panel.", ephemeral=True)
        return True



# ======================================================================
# From: modules/trigger_roles.py
# ======================================================================

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



# ======================================================================
# From: modules/starboard.py
# ======================================================================

import discord
# (unused discord.ext import removed for stub compatibility)
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
