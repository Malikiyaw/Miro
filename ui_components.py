import discord
import json
import asyncio
from typing import List, Dict, Any, Tuple, Optional
from data_manager import dm
from logger import logger

class EditCommandModal(discord.ui.Modal):
    def __init__(self, cmd_name: str, user_id: int, validate_func):
        super().__init__(title=f"Edit: !{cmd_name}")
        self.cmd_name = cmd_name
        self.user_id = user_id
        self.validate_func = validate_func

        self.code_input = discord.ui.TextInput(
            label="Command Code (JSON)",
            style=discord.TextStyle.paragraph,
            placeholder='{"command_type": "..."}',
            required=True
        )
        self.add_item(self.code_input)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)

        new_code = self.code_input.value

        try:
            data = json.loads(new_code)
        except json.JSONDecodeError:
            await interaction.response.send_message("❌ Invalid JSON! Please enter valid JSON.", ephemeral=True)
            return

        valid, error_msg = self.validate_func(data)
        if not valid:
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return

        custom_cmds = dm.get_guild_data(interaction.guild.id, "custom_commands", {})
        custom_cmds[self.cmd_name] = new_code
        dm.update_guild_data(interaction.guild.id, "custom_commands", custom_cmds)

        await interaction.response.edit_message(content=f"✅ Command `!{self.cmd_name}` updated!", view=None)

class CommandsListView(discord.ui.View):
    """Interactive view for listing and editing all custom commands"""

    def __init__(self, all_commands: dict, command_groups: dict, user_id: int, validate_func, parent: str = None, page: int = 0):
        super().__init__(timeout=180)
        self.all_commands = all_commands
        self.command_groups = command_groups
        self.user_id = user_id
        self.validate_func = validate_func
        self.parent = parent
        self.page = page
        self.commands_per_page = 10

        if parent is None:
            parent_commands = [p for p in command_groups.keys() if not command_groups[p]]
            parent_commands.extend([p for p in command_groups.keys() if command_groups[p]])
            start_idx = page * self.commands_per_page
            end_idx = start_idx + self.commands_per_page
            page_commands = parent_commands[start_idx:end_idx]

            for cmd in page_commands:
                btn = discord.ui.Button(label=f"!{cmd}", custom_id=f"view_{cmd}", style=discord.ButtonStyle.secondary)
                btn.callback = self.create_view_callback(cmd)
                self.add_item(btn)

            total = len(parent_commands)
            total_pages = (total + self.commands_per_page - 1) // self.commands_per_page

            if page > 0:
                prev_btn = discord.ui.Button(label="⬅️ Prev", custom_id=f"prev_{page}", style=discord.ButtonStyle.primary)
                prev_btn.callback = self.create_parent_prev_callback(page)
                self.add_item(prev_btn)

            if page < total_pages - 1:
                next_btn = discord.ui.Button(label="Next ➡️", custom_id=f"next_{page}", style=discord.ButtonStyle.primary)
                next_btn.callback = self.create_parent_next_callback(page)
                self.add_item(next_btn)

            back_btn = discord.ui.Button(label="✖️ Close", custom_id="close", style=discord.ButtonStyle.danger)
            back_btn.callback = self.close_callback
            self.add_item(back_btn)
        else:
            subcommands = command_groups.get(parent, [])
            if parent not in subcommands:
                subcommands = [parent] + subcommands

            start_idx = page * self.commands_per_page
            end_idx = start_idx + self.commands_per_page
            page_subs = subcommands[start_idx:end_idx]

            for sub in page_subs:
                is_parent = (sub == parent)
                btn = discord.ui.Button(
                    label=f"!{sub}" + (" (parent)" if is_parent else ""),
                    custom_id=f"edit_{sub}",
                    style=discord.ButtonStyle.success if is_parent else discord.ButtonStyle.secondary
                )
                btn.callback = self.create_edit_callback(sub)
                self.add_item(btn)

            total_pages = (len(subcommands) + self.commands_per_page - 1) // self.commands_per_page

            if page > 0:
                prev_btn = discord.ui.Button(label="⬅️ Prev", custom_id=f"prev_{page}", style=discord.ButtonStyle.primary)
                prev_btn.callback = self.create_sub_prev_callback(page)
                self.add_item(prev_btn)

            if page < total_pages - 1:
                next_btn = discord.ui.Button(label="Next ➡️", custom_id=f"next_{page}", style=discord.ButtonStyle.primary)
                next_btn.callback = self.create_sub_next_callback(page)
                self.add_item(next_btn)

            back_btn = discord.ui.Button(label="⬅️ Back", custom_id="back", style=discord.ButtonStyle.primary)
            back_btn.callback = self.back_callback
            self.add_item(back_btn)

    def create_view_callback(self, parent: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.validate_func, parent, 0)
            subcommands = self.command_groups.get(parent, [])
            if parent not in subcommands:
                subcommands = [parent] + subcommands
            total = len(subcommands)

            embed = discord.Embed(
                title=f"📋 Command: !{parent}",
                description=f"Subcommands: {total}\n\nClick to edit each command's code.",
                color=discord.Color.orange()
            )
            await interaction.response.edit_message(embed=embed, view=view)
        return callback

    def create_edit_callback(self, cmd_name: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)

            modal = EditCommandModal(cmd_name, self.user_id, self.validate_func)
            await interaction.response.send_modal(modal)
        return callback

    def create_parent_prev_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.validate_func, None, page - 1)
            await interaction.response.edit_message(view=view)
        return callback

    def create_parent_next_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.validate_func, None, page + 1)
            await interaction.response.edit_message(view=view)
        return callback

    def create_sub_prev_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.validate_func, self.parent, page - 1)
            await interaction.response.edit_message(view=view)
        return callback

    def create_sub_next_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.validate_func, self.parent, page + 1)
            await interaction.response.edit_message(view=view)
        return callback

    async def back_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.validate_func, None, 0)
        
        command_groups = self.command_groups
        command_list = []
        for parent in sorted(command_groups.keys()):
            subcount = len(command_groups[parent])
            if subcount > 0:
                command_list.append(f"**!{parent}** ({subcount} subcommands)")
            else:
                command_list.append(f"**!{parent}**")

        embed = discord.Embed(
            title="📋 All Custom Commands",
            description=f"Total: {len(self.all_commands)} commands\n\nClick a command to view/edit its subcommands.",
            color=discord.Color.blue()
        )
        embed.add_field(name="📋 Commands", value="\n".join(command_list[:25]), inline=False)

        await interaction.response.edit_message(embed=embed, view=view)

    async def close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await interaction.response.edit_message(content="✅ Commands closed.", view=None)

class HelpCategoryView(discord.ui.View):
    """Interactive view with category buttons for help system"""
    def __init__(self, handler, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.handler = handler
        self.guild_id = guild_id
        self.user_id = user_id
    
    @discord.ui.button(label="🛡️ Security", style=discord.ButtonStyle.primary, custom_id="help_sec")
    async def security_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await self._show_category(interaction, "Security & Verification", [
            ("verification", "🛡️", "Instant member onboarding and gates."),
            ("anti_raid", "⚔️", "Mass-join and spam detection logic."),
            ("guardian", "👁️", "AI-powered token and scam link detection."),
            ("automod", "🤖", "Keyword, caps, and mention filtering."),
            ("warnings", "⚠️", "Strike system with auto-escalation.")
        ], discord.Color.red())
    
    @discord.ui.button(label="📈 Engagement", style=discord.ButtonStyle.success, custom_id="help_eng")
    async def engagement_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await self._show_category(interaction, "Economy & Engagement", [
            ("economy", "💰", "Advanced currency and shop system."),
            ("leveling", "⬆️", "XP, roles, and multipliers for activity."),
            ("giveaways", "🎁", "Automated prize hosting and entry."),
            ("gamification", "🎮", "Prestige, quests, and daily challenges."),
            ("starboard", "⭐", "Hall of fame for best messages.")
        ], discord.Color.green())
    
    @discord.ui.button(label="⚖️ Staff", style=discord.ButtonStyle.secondary, custom_id="help_staff")
    async def staff_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await self._show_category(interaction, "Staff & Admin Hub", [
            ("staff_promo", "🌟", "Automated staff performance & hierarchy."),
            ("staff_shifts", "🕒", "On-duty tracking and idle monitor."),
            ("staff_reviews", "📝", "Peer and admin evaluation cycles."),
            ("applications", "📋", "Recruitment and interview management."),
            ("modmail", "📬", "Direct staff communication channel.")
        ], discord.Color.blue())
    
    @discord.ui.button(label="🤖 Automation", style=discord.ButtonStyle.primary, custom_id="help_auto", row=1)
    async def automation_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await self._show_category(interaction, "Smart Automation", [
            ("welcome", "👋", "Custom greet/leave messages and DMs."),
            ("tickets", "🎫", "Interactive support ticket system."),
            ("reminders", "⏰", "Personal and server-wide scheduled alerts."),
            ("auto_responder", "💬", "Automated replies for common questions."),
            ("chat_channels", "🧠", "AI-powered chat channels and personas.")
        ], discord.Color.teal())
    
    @discord.ui.button(label="🌐 Community", style=discord.ButtonStyle.secondary, custom_id="help_comm", row=1)
    async def community_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await self._show_category(interaction, "Community Tools", [
            ("reaction_roles", "🎭", "Bind roles to message reactions."),
            ("reaction_menus", "📋", "Organized role selection views."),
            ("role_buttons", "🔘", "Button-based role assignment."),
            ("suggestions", "💡", "Community feedback and voting."),
            ("events", "📅", "Server events and participant logging.")
        ], discord.Color.gold())

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.secondary, custom_id="help_search", row=1)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        
        class SearchModal(discord.ui.Modal, title="Search Miro Systems"):
            query = discord.ui.TextInput(label="System Name", placeholder="e.g. Economy, AutoMod...")
            def __init__(self, handler):
                super().__init__()
                self.handler = handler
            async def on_submit(self, it):
                await self.handler.handle_help_system(it, self.query.value)

        await interaction.response.send_modal(SearchModal(self.handler))

    @discord.ui.button(label="🏠 Home", style=discord.ButtonStyle.secondary, custom_id="help_home", row=1)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await self.handler.handle_help_all(interaction)

    async def _show_category(self, interaction: discord.Interaction, title: str, systems: List[tuple], color: discord.Color):
        guild_id = self.guild_id
        embed = discord.Embed(title=f"📋 Category: {title}", description="━━━━━━━━━━━━━━", color=color)
        
        for sys_id, emoji, desc in systems:
            status = "🔴"
            if dm.get_guild_data(guild_id, f"{sys_id}_config") or dm.get_guild_data(guild_id, f"{sys_id}_settings"):
                status = "🟢"

            embed.add_field(
                name=f"{status} {emoji} {sys_id.replace('_', ' ').title()}",
                value=f"{desc}\n`!configpanel {sys_id}` | `!help {sys_id}`",
                inline=False
            )
        
        embed.set_footer(text="🟢 = Installed | 🔴 = Not Setup")
        await interaction.response.edit_message(embed=embed, view=self)

class TierManagementView(discord.ui.View):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__(timeout=300)
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config

    @discord.ui.button(label="Add Tier", style=discord.ButtonStyle.success, emoji="➕", custom_id="tier_add")
    async def add_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTierModal(self.guild, self.staff_promo, self.config))

    @discord.ui.button(label="Edit Tier", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="tier_edit")
    async def edit_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditTierModal(self.guild, self.staff_promo, self.config))

    @discord.ui.button(label="Remove Tier", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="tier_remove")
    async def remove_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveTierModal(self.guild, self.staff_promo, self.config))

class AddTierModal(discord.ui.Modal, title="Add Promotion Tier"):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__()
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config

    name = discord.ui.TextInput(label="Tier Name", placeholder="e.g., Senior Moderator", required=True)
    threshold = discord.ui.TextInput(label="Threshold (%)", placeholder="e.g., 75 for 75%", required=True)
    role = discord.ui.TextInput(label="Role Name (Optional)", placeholder="Exact role name", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            threshold_val = float(self.threshold.value) / 100
            if threshold_val < 0 or threshold_val > 1:
                await interaction.response.send_message("❌ Threshold must be between 0 and 100", ephemeral=True)
                return

            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            new_tier = {
                "name": self.name.value,
                "threshold": threshold_val,
                "role_name": self.role.value if self.role.value else None
            }
            tiers.append(new_tier)
            self.config["tiers"] = tiers

            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)

            await interaction.response.send_message(f"✅ Added tier **{self.name.value}** with threshold {self.threshold.value}%", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid threshold value", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error adding tier: {str(e)}", ephemeral=True)

class EditTierModal(discord.ui.Modal, title="Edit Promotion Tier"):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__()
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config

    tier_select = discord.ui.TextInput(
        label="Tier Name to Edit",
        placeholder="Enter exact tier name to edit",
        required=True
    )
    new_name = discord.ui.TextInput(label="New Tier Name (Optional)", placeholder="Leave blank to keep current", required=False)
    new_threshold = discord.ui.TextInput(label="New Threshold (%) (Optional)", placeholder="Leave blank to keep current", required=False)
    new_role = discord.ui.TextInput(label="New Role Name (Optional)", placeholder="Leave blank to keep current", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            tier_to_edit = None
            for tier in tiers:
                if tier.get("name", "").lower() == self.tier_select.value.lower():
                    tier_to_edit = tier
                    break

            if not tier_to_edit:
                await interaction.response.send_message(f"❌ Tier '{self.tier_select.value}' not found", ephemeral=True)
                return

            if self.new_name.value:
                tier_to_edit["name"] = self.new_name.value
            if self.new_threshold.value:
                threshold_val = float(self.new_threshold.value) / 100
                if threshold_val < 0 or threshold_val > 1:
                    await interaction.response.send_message("❌ Threshold must be between 0 and 100", ephemeral=True)
                    return
                tier_to_edit["threshold"] = threshold_val
            if self.new_role.value is not None:
                tier_to_edit["role_name"] = self.new_role.value if self.new_role.value else None

            self.config["tiers"] = tiers
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)

            await interaction.response.send_message(f"✅ Updated tier **{self.tier_select.value}**", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid threshold value", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error editing tier: {str(e)}", ephemeral=True)

class RemoveTierModal(discord.ui.Modal, title="Remove Promotion Tier"):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__()
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config

    tier_select = discord.ui.TextInput(
        label="Tier Name to Remove",
        placeholder="Enter exact tier name to remove",
        required=True
    )
    confirm = discord.ui.TextInput(
        label="Type 'CONFIRM' to delete",
        placeholder="This action cannot be undone",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if self.confirm.value != "CONFIRM":
                await interaction.response.send_message("❌ Confirmation failed. Type 'CONFIRM' to delete.", ephemeral=True)
                return

            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            tier_to_remove = None
            for i, tier in enumerate(tiers):
                if tier.get("name", "").lower() == self.tier_select.value.lower():
                    tier_to_remove = i
                    break

            if tier_to_remove is None:
                await interaction.response.send_message(f"❌ Tier '{self.tier_select.value}' not found", ephemeral=True)
                return

            removed_tier = tiers.pop(tier_to_remove)
            self.config["tiers"] = tiers
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)

            await interaction.response.send_message(f"✅ Removed tier **{removed_tier.get('name')}**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error removing tier: {str(e)}", ephemeral=True)
