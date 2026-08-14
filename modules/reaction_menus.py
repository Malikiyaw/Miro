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
