import discord
from discord import ui, Interaction, TextStyle, Embed, ButtonStyle
from data_manager import dm
import datetime
import time
import json
from typing import List, Dict, Optional, Any
from logger import logger

class BanAppealModal(ui.Modal, title="Submit Ban Appeal"):
    q1 = ui.TextInput(label="Why were you banned?", style=TextStyle.paragraph, required=True, max_length=1000)
    q2 = ui.TextInput(label="Why should you be unbanned?", style=TextStyle.paragraph, required=True, max_length=1000)
    q3 = ui.TextInput(label="What will you do differently?", style=TextStyle.paragraph, required=True, max_length=1000)
    q4 = ui.TextInput(label="Any evidence to provide?", style=TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "appeals_config", {})
        
        # Save appeal to guild_data
        appeal_id = f"{interaction.user.id}_{int(time.time())}"
        appeal_data = {
            "id": appeal_id,
            "user_id": interaction.user.id,
            "username": str(interaction.user),
            "timestamp": time.time(),
            "status": "pending",
            "answers": {
                "why_banned": self.q1.value,
                "why_unban": self.q2.value,
                "different": self.q3.value,
                "evidence": self.q4.value
            }
        }
        
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        if str(interaction.user.id) not in appeals:
            appeals[str(interaction.user.id)] = []
        appeals[str(interaction.user.id)].append(appeal_data)
        dm.update_guild_data(guild_id, "appeals", appeals)
        
        # Post to #appeals-log
        log_channel_id = config.get("log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None
        
        if log_channel:
            embed = Embed(title="⚖️ New Ban Appeal Received", color=discord.Color.orange())
            embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)

            embed.add_field(name="Why were you banned?", value=self.q1.value[:1024], inline=False)
            embed.add_field(name="Why should you be unbanned?", value=self.q2.value[:1024], inline=False)
            embed.add_field(name="What will you do differently?", value=self.q3.value[:1024], inline=False)
            embed.add_field(name="Evidence", value=self.q4.value[:1024] or "None provided", inline=False)

            history = appeals.get(str(interaction.user.id), [])
            embed.add_field(name="Appeal History", value=f"This is appeal #{len(history)} from this user.")

            embed.set_footer(text=f"Appeal ID: {appeal_id}")

            view = AppealReviewView()
            await log_channel.send(embed=embed, view=view)

            # Ping reviewer role
            reviewer_role_id = config.get("reviewer_role_id")
            if reviewer_role_id:
                await log_channel.send(f"<@&{reviewer_role_id}> New ban appeal submitted!", delete_after=5)
        
        # DM user
        try:
            await interaction.user.send("Your appeal has been received. You will be notified of the decision.")
        except:
            pass

        await interaction.response.send_message("✅ Your appeal has been submitted and staff have been notified.", ephemeral=True)

class AppealPersistentView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @ui.button(label="Submit Appeal", style=ButtonStyle.primary, custom_id="appeal_submit_button")
    async def submit_appeal(self, interaction: Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "appeals_config", {})
        
        # Check blacklist
        blacklist = dm.get_guild_data(guild_id, "appeals_blacklist", [])
        if interaction.user.id in blacklist:
            return await interaction.response.send_message("❌ You are blacklisted from submitting appeals.", ephemeral=True)

        # Enforce cooldown
        cooldown_days = config.get("cooldown_days", 30)
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_appeals = appeals.get(str(interaction.user.id), [])
        
        if user_appeals:
            last_appeal = user_appeals[-1]
            elapsed_days = (time.time() - last_appeal.get("timestamp", 0)) / (24 * 3600)
            if elapsed_days < cooldown_days:
                remaining = cooldown_days - elapsed_days
                return await interaction.response.send_message(f"❌ You must wait {remaining:.1f} more days before appealing again.", ephemeral=True)
        
        await interaction.response.send_modal(BanAppealModal())

class AppealReviewView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_appeal_info(self, embed: Embed):
        footer = embed.footer.text
        if footer and "Appeal ID: " in footer:
            return footer.replace("Appeal ID: ", "").split("_")
        return None, None

    async def _update_appeal_status(self, interaction: Interaction, status: str, staff_note: str = None):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        if not user_id_str:
            return None

        guild_id = interaction.guild_id
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_appeals = appeals.get(user_id_str, [])
        
        target_app = None
        for app in user_appeals:
            if str(int(app["timestamp"])) == ts_str:
                app["status"] = status
                if staff_note:
                    if "staff_notes" not in app: app["staff_notes"] = []
                    app["staff_notes"].append(staff_note)
                target_app = app
                break
        
        if target_app:
            dm.update_guild_data(guild_id, "appeals", appeals)
            return target_app
        return None

    @ui.button(label="Approve", style=ButtonStyle.success, emoji="✅", custom_id="appeal_review_approve")
    async def approve(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        await interaction.response.send_modal(ApproveModal(user_id_str, ts_str))

    @ui.button(label="Deny", style=ButtonStyle.danger, emoji="❌", custom_id="appeal_review_deny")
    async def deny(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        await interaction.response.send_modal(DenyModal(user_id_str, ts_str))

    @ui.button(label="Escalate", style=ButtonStyle.secondary, emoji="⏸️", custom_id="appeal_review_escalate")
    async def escalate(self, interaction: Interaction, button: ui.Button):
        config = dm.get_guild_data(interaction.guild_id, "appeals_config", {})
        reviewer_role_id = config.get("reviewer_role_id")
        
        embed = interaction.message.embeds[0]
        embed.title = "⚖️ [ESCALATED] Ban Appeal"
        embed.color = discord.Color.dark_red()
        
        await interaction.message.edit(embed=embed)
        
        msg = "⏸️ Appeal escalated to senior staff."
        if reviewer_role_id:
            msg += f" <@&{reviewer_role_id}>"
        
        await interaction.response.send_message(msg, ephemeral=False)

    @ui.button(label="Check Ban Reason", style=ButtonStyle.secondary, emoji="🔍", custom_id="appeal_review_ban_reason")
    async def check_ban_reason(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_appeal_info(interaction.message.embeds[0])
        try:
            ban_entry = await interaction.guild.fetch_ban(discord.Object(id=int(user_id_str)))
            await interaction.response.send_message(f"🔍 **Original Ban Reason:** {ban_entry.reason or 'No reason provided.'}", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ User is not currently banned.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error fetching ban reason: {e}", ephemeral=True)

    @ui.button(label="View History", style=ButtonStyle.secondary, emoji="📋", custom_id="appeal_review_history")
    async def view_history(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_appeal_info(interaction.message.embeds[0])
        appeals = dm.get_guild_data(interaction.guild_id, "appeals", {})
        user_appeals = appeals.get(user_id_str, [])
        
        if not user_appeals:
            return await interaction.response.send_message("No previous appeals found.", ephemeral=True)

        desc = ""
        for app in user_appeals:
            status_emoji = {"accepted": "✅", "denied": "❌", "pending": "⏳", "on_hold": "🕐"}.get(app["status"], "❓")
            desc += f"{status_emoji} **{app['status'].title()}** - <t:{int(app['timestamp'])}:R> (ID: `{app['id']}`)\n"

        embed = Embed(title=f"Appeal History: {user_id_str}", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Request Info", style=ButtonStyle.secondary, emoji="💬", custom_id="appeal_review_info")
    async def request_info(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_appeal_info(interaction.message.embeds[0])
        await interaction.response.send_modal(RequestInfoModal(user_id_str, ts_str))

    @ui.button(label="Put on Hold", style=ButtonStyle.secondary, emoji="🕐", custom_id="appeal_review_hold")
    async def hold(self, interaction: Interaction, button: ui.Button):
        app = await self._update_appeal_status(interaction, "on_hold")
        if not app: return
        
        config = dm.get_guild_data(interaction.guild_id, "appeals_config", {})
        user = interaction.guild.get_member(int(app["user_id"]))
        if user:
            try: await user.send("Your appeal needs more time and has been put on hold.")
            except: pass
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.gold()
        embed.add_field(name="Status", value=f"🕐 Put on hold by {interaction.user.mention}", inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("🕐 Appeal put on hold.", ephemeral=True)

    @ui.button(label="Blacklist", style=ButtonStyle.danger, emoji="🚫", custom_id="appeal_review_blacklist")
    async def blacklist(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_appeal_info(interaction.message.embeds[0])
        blacklist = dm.get_guild_data(interaction.guild_id, "appeals_blacklist", [])
        if int(user_id_str) not in blacklist:
            blacklist.append(int(user_id_str))
            dm.update_guild_data(interaction.guild_id, "appeals_blacklist", blacklist)
            await interaction.response.send_message(f"🚫 User <@{user_id_str}> has been blacklisted from appeals.", ephemeral=True)
        else:
            await interaction.response.send_message("User is already blacklisted.", ephemeral=True)

class ApproveModal(ui.Modal, title="Approve Appeal"):
    note = ui.TextInput(label="Optional note to user", style=TextStyle.paragraph, required=False, max_length=500)
    
    def __init__(self, user_id_str, ts_str):
        super().__init__()
        self.user_id_str = user_id_str
        self.ts_str = ts_str

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_apps = appeals.get(self.user_id_str, [])
        
        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                app["status"] = "accepted"
                target_app = app
                break
        
        if target_app:
            dm.update_guild_data(guild_id, "appeals", appeals)
            
            # Unban user
            try:
                await interaction.guild.unban(discord.Object(id=int(self.user_id_str)), reason=f"Appeal accepted by {interaction.user}")
            except Exception as e:
                logger.error(f"Failed to unban user {self.user_id_str}: {e}")

            # DM user
            config = dm.get_guild_data(guild_id, "appeals_config", {})
            user = await interaction.client.fetch_user(int(self.user_id_str))
            if user:
                invite = ""
                # Try to generate an invite if configured
                try:
                    channels = interaction.guild.text_channels
                    if channels:
                        inv = await channels[0].create_invite(max_uses=1, unique=True)
                        invite = inv.url
                except: pass

                msg = config.get("approval_dm", "Your appeal has been accepted! You have been unbanned. {invite}").format(
                    user=user.name, invite=invite
                )
                try: await user.send(msg)
                except: pass

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.add_field(name="Decision", value=f"✅ Approved by {interaction.user.mention}\nNote: {self.note.value or 'None'}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"✅ Approved appeal and unbanned <@{self.user_id_str}>.", ephemeral=True)

class DenyModal(ui.Modal, title="Deny Appeal"):
    reason = ui.TextInput(label="Reason for Denial", style=TextStyle.paragraph, required=True, max_length=1000)
    
    def __init__(self, user_id_str, ts_str):
        super().__init__()
        self.user_id_str = user_id_str
        self.ts_str = ts_str

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        appeals = dm.get_guild_data(guild_id, "appeals", {})
        user_apps = appeals.get(self.user_id_str, [])
        
        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                app["status"] = "denied"
                app["deny_reason"] = self.reason.value
                target_app = app
                break
        
        if target_app:
            dm.update_guild_data(guild_id, "appeals", appeals)

            # DM user
            config = dm.get_guild_data(guild_id, "appeals_config", {})
            user = await interaction.client.fetch_user(int(self.user_id_str))
            if user:
                cooldown_days = config.get("cooldown_days", 30)
                next_date = (datetime.datetime.now() + datetime.timedelta(days=cooldown_days)).strftime("%Y-%m-%d")

                msg = config.get("denial_dm", "Your appeal was denied. Reason: {reason}\nYou can appeal again after {next_date}.").format(
                    user=user.name, reason=self.reason.value, next_date=next_date
                )
                try: await user.send(msg)
                except: pass

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.add_field(name="Decision", value=f"❌ Denied by {interaction.user.mention}\nReason: {self.reason.value}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"❌ Denied appeal for <@{self.user_id_str}>.", ephemeral=True)

class RequestInfoModal(ui.Modal, title="Request More Info"):
    question = ui.TextInput(label="Question", style=TextStyle.paragraph, required=True, max_length=1000)

    def __init__(self, user_id_str, ts_str):
        super().__init__()
        self.user_id_str = user_id_str
        self.ts_str = ts_str

    async def on_submit(self, interaction: Interaction):
        user = await interaction.client.fetch_user(int(self.user_id_str))
        if user:
            try: await user.send(f"Staff have requested more information regarding your appeal:\n\n> {self.question.value}")
            except: pass
        
        embed = interaction.message.embeds[0]
        embed.add_field(name="Info Requested", value=f"By {interaction.user.mention}: {self.question.value}", inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Information requested.", ephemeral=True)

class AppealSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_persistent_views(self):
        return [AppealPersistentView(), AppealReviewView()]

    async def create_appeal(self, interaction):
        """Slash-command adapter: open the ban appeal modal."""
        await interaction.response.send_modal(BanAppealModal())

    async def setup(self, interaction: Interaction):
        """Standard setup for appeals system."""
        guild = interaction.guild
        
        # Create category
        category = await guild.create_category("Appeals")
        
        # Create #appeals
        appeals_ch = await guild.create_text_channel("appeals", category=category)
        
        # Create #appeals-log (private)
        log_ch = await guild.create_text_channel("appeals-log", category=category)
        await log_ch.set_permissions(guild.default_role, read_messages=False)
        
        # Initial config
        config = {
            "appeals_channel_id": appeals_ch.id,
            "log_channel_id": log_ch.id,
            "cooldown_days": 30,
            "reviewer_role_id": None,
            "questions": [
                "Why were you banned?",
                "Why should you be unbanned?",
                "What will you do differently?",
                "Any evidence to provide?"
            ]
        }
        dm.update_guild_data(guild.id, "appeals_config", config)
        
        # Post panel to #appeals
        embed = Embed(title="⚖️ Moderation Appeals", description="If you have been banned or punished and wish to appeal, click the button below.", color=discord.Color.blue())
        await appeals_ch.send(embed=embed, view=AppealPersistentView())
        
        return True

async def setup(bot):
    # This function is kept for compatibility but the actual setup is handled in bot.py
    # to ensure proper initialization order
    return True
