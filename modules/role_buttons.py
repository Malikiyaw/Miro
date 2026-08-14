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
