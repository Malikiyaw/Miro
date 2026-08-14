import discord
from discord import ui, Interaction, TextStyle, Embed, ButtonStyle
import datetime
import time
import json
from typing import List, Dict, Optional, Any
from data_manager import dm
from logger import logger

class ApplicationSystem:
    """
    Complete staff application system with submission, review, and management.
    Features:
    - Application submission via modal
    - Staff review and approval process
    - Application status tracking
    - Automatic DM notifications
    """

    def __init__(self, bot):
        self.bot = bot

    def get_persistent_views(self):
        return [ApplicationPersistentView(), ApplicationReviewView()]

    async def create_application(self, interaction):
        """Slash-command adapter: open the application modal."""
        config = dm.get_guild_data(interaction.guild.id, "application_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Applications are currently disabled.", ephemeral=True)
        questions = config.get("questions") or [
            "Why do you want to join the staff team?",
            "What experience do you have?",
            "How active are you on this server?",
            "What would you improve?",
            "Anything else we should know?",
        ]
        await interaction.response.send_modal(ApplicationModal(questions))

    async def handle_application_submit(self, interaction, application_data):
        """Handle application submission."""
        config = dm.get_guild_data(interaction.guild.id, "application_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Applications are currently disabled.", ephemeral=True)

        # Store application
        applications = dm.get_guild_data(interaction.guild.id, "applications", [])
        applications.append({
            "id": int(time.time()),
            "user_id": interaction.user.id,
            "data": application_data,
            "status": "pending",
            "submitted_at": time.time()
        })
        dm.update_guild_data(interaction.guild.id, "applications", applications)

        await interaction.response.send_message("✅ Application submitted! You'll be notified of the decision.", ephemeral=True)

    async def review_application(self, interaction, application_id, action, reason=None):
        """Review an application (approve/deny)."""
        applications = dm.get_guild_data(interaction.guild.id, "applications", [])
        application = next((a for a in applications if a["id"] == application_id), None)

        if not application:
            return await interaction.response.send_message("❌ Application not found.", ephemeral=True)

        # Check permissions
        config = dm.get_guild_data(interaction.guild.id, "application_config", {})
        is_staff = (interaction.user.guild_permissions.administrator or
                   any(role.id == int(rid) for rid in config.get("staff_roles", []) for role in interaction.user.roles))

        if not is_staff:
            return await interaction.response.send_message("❌ Only staff can review applications.", ephemeral=True)

        # Update status
        application["status"] = "approved" if action == "approve" else "denied"
        application["reviewed_by"] = interaction.user.id
        application["reviewed_at"] = time.time()
        if reason:
            application["review_reason"] = reason

        # Update in storage
        for i, a in enumerate(applications):
            if a["id"] == application_id:
                applications[i] = application
                break
        dm.update_guild_data(interaction.guild.id, "applications", applications)

        # Notify applicant
        try:
            user = self.bot.get_user(application["user_id"])
            if user:
                embed = discord.Embed(
                    title="📋 Application Update",
                    description=f"Your application has been **{application['status']}**.",
                    color=discord.Color.green() if action == "approve" else discord.Color.red()
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                await user.send(embed=embed)
        except:
            pass

        await interaction.response.send_message(f"✅ Application {action}d!", ephemeral=True)

class ApplicationPersistentView(ui.View):
    """Persistent view for the public 'Apply Now' button."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Apply Now", style=ButtonStyle.primary, custom_id="app_apply_now")
    async def apply_now(self, interaction: Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "application_config", {})

        # Check if applications are closed
        if not config.get("applications_open", True):
            return await interaction.response.send_message("❌ Applications are currently closed.", ephemeral=True)

        # Enforce cooldown
        cooldown_days = config.get("cooldown_days", 30)
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(str(interaction.user.id), [])

        if user_apps:
            last_app = user_apps[-1]
            last_ts = last_app.get("timestamp", 0)
            elapsed = (time.time() - last_ts) / (24 * 3600)
            if elapsed < cooldown_days:
                remaining = cooldown_days - elapsed
                return await interaction.response.send_message(f"❌ You've applied recently. You can reapply in {remaining:.1f} days.", ephemeral=True)

        app_types = config.get("application_types", [])
        if app_types:
            view = ui.View(timeout=60)
            select = ui.Select(placeholder="Select Application Type", options=[
                discord.SelectOption(label=t, value=t) for t in app_types
            ])

            async def select_callback(it: Interaction):
                app_type = select.values[0]
                questions = config.get("questions", ["Why do you want to join?", "What experience do you have?"])
                await it.response.send_modal(ApplicationModal(questions, app_type))

            select.callback = select_callback
            view.add_item(select)
            return await interaction.response.send_message("Please select the type of application you'd like to submit:", view=view, ephemeral=True)

        questions = config.get("questions", ["Why do you want to join?", "What experience do you have?"])
        modal = ApplicationModal(questions)
        await interaction.response.send_modal(modal)

class ApplicationModal(ui.Modal):
    def __init__(self, questions: List[str], app_type: str = "General"):
        super().__init__(title=f"{app_type} Application")
        self.questions = questions
        self.app_type = app_type
        self.inputs = []
        for q in questions[:5]:
            i = ui.TextInput(label=q, style=TextStyle.paragraph, required=True, max_length=1000)
            self.add_item(i)
            self.inputs.append(i)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        config = dm.get_guild_data(guild_id, "application_config", {})

        # Save application
        answers = [i.value for i in self.inputs]
        app_data = {
            "id": f"{interaction.user.id}_{int(time.time())}",
            "user_id": interaction.user.id,
            "timestamp": time.time(),
            "status": "pending",
            "answers": answers,
            "questions": self.questions[:5],
            "type": self.app_type
        }

        apps = dm.get_guild_data(guild_id, "applications", {})
        if str(interaction.user.id) not in apps:
            apps[str(interaction.user.id)] = []
        apps[str(interaction.user.id)].append(app_data)
        dm.update_guild_data(guild_id, "applications", apps)

        # Send to log channel
        log_ch_id = config.get("log_channel_id")
        log_ch = interaction.guild.get_channel(log_ch_id)

        if log_ch:
            embed = Embed(title=f"📋 New {self.app_type} Application Received", color=discord.Color.blue())
            embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)

            account_age = (datetime.datetime.now(datetime.timezone.utc) - interaction.user.created_at).days
            join_date = interaction.user.joined_at.strftime("%Y-%m-%d") if interaction.user.joined_at else "Unknown"

            embed.add_field(name="User Info", value=f"Account Age: {account_age} days\nJoined: {join_date}\nSubmitted: <t:{int(time.time())}:R>")

            for q, a in zip(app_data["questions"], app_data["answers"]):
                embed.add_field(name=q, value=a[:1024], inline=False)

            embed.set_footer(text=f"App ID: {app_data['id']}")

            view = ApplicationReviewView()
            await log_ch.send(embed=embed, view=view)

            # Ping role if enabled
            if config.get("auto_ping_enabled") and config.get("staff_role_id"):
                await log_ch.send(f"<@&{config['staff_role_id']}> New application submitted!", delete_after=5)

        # DM applicant
        if config.get("applicant_dms_enabled", True):
            try:
                await interaction.user.send("Your application was received and is under review.")
            except:
                pass

        await interaction.response.send_message("✅ Your application has been submitted!", ephemeral=True)

class ApplicationReviewView(ui.View):
    """Staff-only review actions."""
    def __init__(self):
        super().__init__(timeout=None)

    def _get_app_info(self, embed: Embed):
        footer = embed.footer.text
        if footer and "App ID: " in footer:
            return footer.replace("App ID: ", "").split("_")
        return None, None

    async def _update_app_status(self, interaction: Interaction, status: str, reason: str = None):
        user_id_str, ts_str = self._get_app_info(interaction.message.embeds[0])
        if not user_id_str:
            return await interaction.response.send_message("❌ Could not find application data.", ephemeral=True)

        guild_id = interaction.guild_id
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(user_id_str, [])

        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == ts_str:
                app["status"] = status
                if reason: app["deny_reason"] = reason
                target_app = app
                break

        if target_app:
            dm.update_guild_data(guild_id, "applications", apps)
            # Log action
            log_entry = {
                "action": f"application_{status}",
                "user_id": int(user_id_str),
                "moderator_id": interaction.user.id,
                "timestamp": time.time(),
                "app_id": target_app["id"],
                "reason": reason
            }
            logs = dm.get_guild_data(guild_id, "action_logs", [])
            logs.append(log_entry)
            dm.update_guild_data(guild_id, "action_logs", logs[-100:])
            return target_app
        return None

    @ui.button(label="Accept", style=ButtonStyle.success, emoji="✅", custom_id="app_review_accept")
    async def accept(self, interaction: Interaction, button: ui.Button):
        app = await self._update_app_status(interaction, "accepted")
        if not app: return

        config = dm.get_guild_data(interaction.guild_id, "application_config", {})
        role_id = config.get("role_to_give_on_accept")
        role = interaction.guild.get_role(role_id)

        role_error = None
        applicant = interaction.guild.get_member(app["user_id"])
        if role and applicant:
            try:
                await applicant.add_roles(role)
            except:
                role_error = f"⚠️ Failed to add role <@&{role_id}> to {applicant.mention}."

        if config.get("applicant_dms_enabled", True) and applicant:
            msg = config.get("acceptance_dm", "Congratulations {user}! Your application was accepted for {role}.").format(
                user=applicant.name, role=role.name if role else "the role"
            )
            try: await applicant.send(msg)
            except: pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Decision", value=f"Accepted by {interaction.user.mention} at <t:{int(time.time())}:f>")
        await interaction.message.edit(embed=embed, view=None)

        resp_msg = f"✅ Application accepted for <@{app['user_id']}>."
        if role_error: resp_msg += f"\n{role_error}"
        await interaction.response.send_message(resp_msg, ephemeral=True)

    @ui.button(label="Deny", style=ButtonStyle.danger, emoji="❌", custom_id="app_review_deny")
    async def deny(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_app_info(interaction.message.embeds[0])
        await interaction.response.send_modal(DenyModal(user_id_str, ts_str))

    @ui.button(label="View Profile", style=ButtonStyle.secondary, emoji="🔍", custom_id="app_review_profile")
    async def view_profile(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_app_info(interaction.message.embeds[0])
        member = interaction.guild.get_member(int(user_id_str)) or await interaction.guild.fetch_member(int(user_id_str))

        if not member:
            return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

        embed = Embed(title=f"User Profile: {member}", color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Joined Discord", value=f"<t:{int(member.created_at.timestamp())}:R>")
        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>")

        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:20]) or "None", inline=False)

        # Add server history if available (e.g. from moderation logs)
        mod_logs = dm.get_guild_data(interaction.guild_id, "mod_logs", [])
        user_logs = [l for l in mod_logs if l.get("user_id") == member.id]
        embed.add_field(name="Mod History", value=f"Total Infractions: {len(user_logs)}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Put on Hold", style=ButtonStyle.secondary, emoji="🕐", custom_id="app_review_hold")
    async def hold(self, interaction: Interaction, button: ui.Button):
        app = await self._update_app_status(interaction, "on_hold")
        if not app: return

        config = dm.get_guild_data(interaction.guild_id, "application_config", {})
        applicant = interaction.guild.get_member(app["user_id"])

        if config.get("applicant_dms_enabled", True) and applicant:
            try: await applicant.send("Your application for the server is currently on hold/under further review.")
            except: pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.orange()
        embed.add_field(name="Status Update", value=f"Put on Hold by {interaction.user.mention}")
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("🕐 Application put on hold.", ephemeral=True)

    @ui.button(label="Previous Apps", style=ButtonStyle.secondary, emoji="📋", custom_id="app_review_prev")
    async def view_previous(self, interaction: Interaction, button: ui.Button):
        user_id_str, _ = self._get_app_info(interaction.message.embeds[0])
        apps = dm.get_guild_data(interaction.guild_id, "applications", {})
        user_apps = apps.get(user_id_str, [])

        if not user_apps:
            return await interaction.response.send_message("No previous applications found.", ephemeral=True)

        desc = ""
        for app in user_apps:
            status_emoji = {"accepted": "✅", "denied": "❌", "pending": "⏳", "on_hold": "🕐"}.get(app["status"], "❓")
            desc += f"{status_emoji} **{app['status'].title()}** - <t:{int(app['timestamp'])}:R> (ID: `{app['id']}`)\n"

        embed = Embed(title=f"Previous Applications: {user_id_str}", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Request Info", style=ButtonStyle.secondary, emoji="💬", custom_id="app_review_info")
    async def request_info(self, interaction: Interaction, button: ui.Button):
        user_id_str, ts_str = self._get_app_info(interaction.message.embeds[0])
        await interaction.response.send_modal(RequestInfoModal(user_id_str, ts_str))

class DenyModal(ui.Modal):
    def __init__(self, user_id_str, ts_str):
        super().__init__(title="Deny Application")
        self.user_id_str = user_id_str
        self.ts_str = ts_str
        self.reason = ui.TextInput(label="Reason for Denial", style=TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(self.user_id_str, [])

        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                app["status"] = "denied"
                app["deny_reason"] = self.reason.value
                target_app = app
                break

        if target_app:
            dm.update_guild_data(guild_id, "applications", apps)

            config = dm.get_guild_data(guild_id, "application_config", {})
            applicant = interaction.guild.get_member(target_app["user_id"])

            if config.get("applicant_dms_enabled", True) and applicant:
                msg = config.get("denial_dm", "Sorry {user}, your application was denied. Reason: {reason}").format(
                    user=applicant.name, reason=self.reason.value
                )
                try: await applicant.send(msg)
                except: pass

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.add_field(name="Decision", value=f"Denied by {interaction.user.mention} at <t:{int(time.time())}:f>\n**Reason:** {self.reason.value}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"❌ Application denied for <@{target_app['user_id']}>.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find application data.", ephemeral=True)

class RequestInfoModal(ui.Modal):
    def __init__(self, user_id_str, ts_str):
        super().__init__(title="Request More Info")
        self.user_id_str = user_id_str
        self.ts_str = ts_str
        self.question = ui.TextInput(label="Question to ask the applicant", style=TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.question)

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        apps = dm.get_guild_data(guild_id, "applications", {})
        user_apps = apps.get(self.user_id_str, [])

        target_app = None
        for app in user_apps:
            if str(int(app["timestamp"])) == self.ts_str:
                if "notes" not in app: app["notes"] = []
                app["notes"].append(f"Info requested: {self.question.value}")
                target_app = app
                break

        if target_app:
            dm.update_guild_data(guild_id, "applications", apps)
            applicant = interaction.guild.get_member(target_app["user_id"])

            if applicant:
                try: await applicant.send(f"Staff have requested more information regarding your application:\n\n> {self.question.value}")
                except: pass

            embed = interaction.message.embeds[0]
            embed.add_field(name="Information Requested", value=f"By {interaction.user.mention}: {self.question.value}", inline=False)
            await interaction.message.edit(embed=embed)
            
            # Log action
            log_entry = {
                "action": "application_info_requested",
                "user_id": int(self.user_id_str),
                "moderator_id": interaction.user.id,
                "timestamp": time.time(),
                "app_id": target_app["id"],
                "question": self.question.value
            }
            logs = dm.get_guild_data(guild_id, "action_logs", [])
            logs.append(log_entry)
            dm.update_guild_data(guild_id, "action_logs", logs[-100:])
            
            await interaction.response.send_message("✅ Information requested from applicant.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find application data.", ephemeral=True)
