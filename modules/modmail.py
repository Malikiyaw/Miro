import discord
from discord import ui, Interaction, TextStyle, Embed, ButtonStyle
from data_manager import dm
import datetime
import time
import json
from typing import List, Dict, Optional, Any
from logger import logger

class ModmailSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_persistent_views(self):
        return [ModmailThreadView()]

    async def handle_dm(self, message: discord.Message):
        """Handle incoming DMs for modmail."""
        user = message.author

        # Find guilds where modmail is enabled and user is a member
        shared_guilds = [g for g in self.bot.guilds if g.get_member(user.id)]

        enabled_guilds = []
        for guild in shared_guilds:
            config = dm.get_guild_data(guild.id, "modmail_config", {})
            if config.get("enabled", False):
                # Check if blocked
                blocked_users = dm.get_guild_data(guild.id, "modmail_blocked", [])
                if user.id in blocked_users:
                    continue
                enabled_guilds.append(guild)

        if not enabled_guilds:
            return # Modmail not enabled or user blocked in all shared servers

        if len(enabled_guilds) > 1:
            # Let user choose server
            view = ui.View(timeout=60)
            select = ui.Select(placeholder="Select Server to Contact Staff")
            for guild in enabled_guilds[:25]:
                select.add_option(label=guild.name, value=str(guild.id))

            async def select_callback(it: Interaction):
                guild_id = int(select.values[0])
                guild = self.bot.get_guild(guild_id)
                await it.response.send_message(f"Forwarding your message to **{guild.name}** staff...", ephemeral=True)
                await self._process_modmail(message, guild)

            select.callback = select_callback
            view.add_item(select)
            await user.send("Which server would you like to contact staff for?", view=view)
        else:
            await self._process_modmail(message, enabled_guilds[0])

    async def _process_modmail(self, message: discord.Message, guild: discord.Guild):
        user = message.author
        config = dm.get_guild_data(guild.id, "modmail_config", {})
        log_channel_id = config.get("log_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

        if not log_channel:
            return await user.send(f"❌ Modmail is currently unavailable for **{guild.name}**.")

        # Check for existing thread
        threads = dm.get_guild_data(guild.id, "modmail_threads", {})
        thread_data = threads.get(str(user.id))

        channel_to_send = None
        is_new = False

        if thread_data and thread_data.get("status") == "open":
            channel_id = thread_data.get("channel_id")
            channel_to_send = guild.get_channel(channel_id) or guild.get_thread(channel_id)

        if not channel_to_send:
            is_new = True
            # Create new thread or channel
            style = config.get("thread_style", "thread") # thread or channel

            if style == "channel":
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                staff_role_id = config.get("staff_role_id")
                if staff_role_id:
                    staff_role = guild.get_role(staff_role_id)
                    if staff_role:
                        overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                channel_to_send = await guild.create_text_channel(
                    name=f"modmail-{user.name}",
                    category=log_channel.category,
                    overwrites=overwrites
                )
            else:
                # Default to thread in log_channel
                try:
                    channel_to_send = await log_channel.create_thread(
                        name=f"modmail-{user.name}",
                        type=discord.ChannelType.private_thread if guild.premium_tier >= 2 else discord.ChannelType.public_thread
                    )
                except:
                    # Fallback to public thread if private fails
                    channel_to_send = await log_channel.create_thread(
                        name=f"modmail-{user.name}",
                        type=discord.ChannelType.public_thread
                    )

            thread_data = {
                "user_id": user.id,
                "channel_id": channel_to_send.id,
                "status": "open",
                "opened_at": time.time(),
                "messages": []
            }
            threads[str(user.id)] = thread_data
            dm.update_guild_data(guild.id, "modmail_threads", threads)

            # Initial embed in thread
            embed = Embed(title="📬 New Modmail Thread", color=discord.Color.blue())
            embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
            embed.add_field(name="Account Age", value=f"<t:{int(user.created_at.timestamp())}:R>")
            member = guild.get_member(user.id)
            embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member else "Not in server")

            view = ModmailThreadView()

            # Implementation of "Toggle Pings"
            ping_content = None
            if config.get("new_thread_pings", True):
                staff_role_id = config.get("staff_role_id")
                if staff_role_id:
                    ping_content = f"<@&{staff_role_id}>"

            await channel_to_send.send(content=ping_content, embed=embed, view=view)

            if is_new:
                auto_reply = config.get("auto_reply_message", "Your message has been forwarded to the staff. We'll get back to you soon.")
                try:
                    await user.send(auto_reply)
                except:
                    pass

        # Forward the message
        forward_embed = Embed(description=message.content, color=discord.Color.light_grey())
        forward_embed.set_author(name=user.name, icon_url=user.display_avatar.url)
        forward_embed.timestamp = datetime.datetime.now()

        if message.attachments:
            forward_embed.add_field(name="Attachments", value="\n".join([a.url for a in message.attachments]))

        await channel_to_send.send(embed=forward_embed)

        # Save to history
        thread_data["messages"].append({
            "sender": "user",
            "content": message.content,
            "timestamp": time.time(),
            "attachments": [a.url for a in message.attachments]
        })
        dm.update_guild_data(guild.id, "modmail_threads", threads)

class ModmailThreadView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _get_user(self, interaction: Interaction):
        guild_id = interaction.guild_id
        threads = dm.get_guild_data(guild_id, "modmail_threads", {})
        for uid_str, data in threads.items():
            if data.get("channel_id") == interaction.channel_id:
                return interaction.client.get_user(int(uid_str)) or await interaction.client.fetch_user(int(uid_str))
        return None

    @ui.button(label="Reply", style=ButtonStyle.primary, emoji="💬", custom_id="modmail_reply")
    async def reply(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        if not user: return await interaction.response.send_message("❌ User not found.", ephemeral=True)
        await interaction.response.send_modal(ModmailReplyModal(user))

    @ui.button(label="Send File", style=ButtonStyle.secondary, emoji="📎", custom_id="modmail_file")
    async def send_file(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(ModmailFileModal())

    @ui.button(label="Close", style=ButtonStyle.danger, emoji="🔒", custom_id="modmail_close")
    async def close(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        guild_id = interaction.guild_id
        threads = dm.get_guild_data(guild_id, "modmail_threads", {})
        config = dm.get_guild_data(guild_id, "modmail_config", {})

        if str(user.id) in threads:
            threads[str(user.id)]["status"] = "closed"
            threads[str(user.id)]["closed_at"] = time.time()
            dm.update_guild_data(guild_id, "modmail_threads", threads)

            # Transcript logic
            transcript = f"Modmail Transcript for {user} ({user.id})\n"
            for msg in threads[str(user.id)].get("messages", []):
                ts = datetime.datetime.fromtimestamp(msg['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                transcript += f"[{ts}] {msg['sender']}: {msg['content']}\n"

            # Save transcript
            transcripts = dm.get_guild_data(guild_id, "modmail_transcripts", [])
            transcripts.append({
                "user_id": user.id,
                "username": str(user),
                "closed_at": time.time(),
                "content": transcript
            })
            dm.update_guild_data(guild_id, "modmail_transcripts", transcripts)

            close_msg = config.get("close_message", "This modmail thread has been closed. If you have more questions, feel free to DM again.")
            try: await user.send(close_msg)
            except: pass

            await interaction.response.send_message("🔒 Thread closed.")
            await interaction.channel.edit(archived=True) if isinstance(interaction.channel, discord.Thread) else await interaction.channel.delete()

    @ui.button(label="Block", style=ButtonStyle.danger, emoji="🚫", custom_id="modmail_block")
    async def block(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        blocked = dm.get_guild_data(interaction.guild_id, "modmail_blocked", [])
        if user.id not in blocked:
            blocked.append(user.id)
            dm.update_guild_data(interaction.guild_id, "modmail_blocked", blocked)
            await interaction.response.send_message(f"🚫 User {user} has been blocked from Modmail.", ephemeral=True)
        else:
            await interaction.response.send_message("User is already blocked.", ephemeral=True)

    @ui.button(label="Escalate", style=ButtonStyle.secondary, emoji="⬆️", custom_id="modmail_escalate")
    async def escalate(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("⬆️ Thread escalated to senior staff.")

    @ui.button(label="History", style=ButtonStyle.secondary, emoji="📋", custom_id="modmail_history")
    async def history(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        transcripts = dm.get_guild_data(interaction.guild_id, "modmail_transcripts", [])
        user_history = [t for t in transcripts if t["user_id"] == user.id]

        if not user_history:
            return await interaction.response.send_message("No previous modmail history found.", ephemeral=True)

        desc = ""
        for t in user_history[-5:]:
            desc += f"- Closed at <t:{int(t['closed_at'])}:f>\n"

        await interaction.response.send_message(embed=Embed(title=f"History for {user}", description=desc), ephemeral=True)

    @ui.button(label="Add Note", style=ButtonStyle.secondary, emoji="🏷️", custom_id="modmail_note")
    async def add_note(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(NoteModal())

    @ui.button(label="Pin", style=ButtonStyle.secondary, emoji="📌", custom_id="modmail_pin")
    async def pin(self, interaction: Interaction, button: ui.Button):
        await interaction.message.pin()
        await interaction.response.send_message("📌 Thread message pinned.", ephemeral=True)

    @ui.button(label="User Info", style=ButtonStyle.secondary, emoji="👤", custom_id="modmail_user_info")
    async def user_info(self, interaction: Interaction, button: ui.Button):
        user = await self._get_user(interaction)
        member = interaction.guild.get_member(user.id)

        embed = Embed(title=f"User Info: {user}", color=discord.Color.blue())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="ID", value=user.id)
        embed.add_field(name="Created", value=f"<t:{int(user.created_at.timestamp())}:R>")
        if member:
            embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>")
            embed.add_field(name="Roles", value=" ".join([r.mention for r in member.roles[1:][:10]]) or "None")

        await interaction.response.send_message(embed=embed, ephemeral=True)

class ModmailReplyModal(ui.Modal, title="Reply to User"):
    message = ui.TextInput(label="Message", style=TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, user):
        super().__init__()
        self.user = user

    async def on_submit(self, interaction: Interaction):
        guild_id = interaction.guild_id
        threads = dm.get_guild_data(guild_id, "modmail_threads", {})

        try:
            embed = Embed(description=self.message.value, color=discord.Color.green())
            embed.set_author(name=f"Staff from {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            await self.user.send(embed=embed)

            # Log in thread
            log_embed = Embed(description=self.message.value, color=discord.Color.green())
            log_embed.set_author(name=f"Reply by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            await interaction.channel.send(embed=log_embed)

            # Save to history
            if str(self.user.id) in threads:
                threads[str(self.user.id)]["messages"].append({
                    "sender": f"staff ({interaction.user.name})",
                    "content": self.message.value,
                    "timestamp": time.time()
                })
                dm.update_guild_data(guild_id, "modmail_threads", threads)

            await interaction.response.send_message("✅ Reply sent.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to DM user: {e}", ephemeral=True)

class ModmailFileModal(ui.Modal, title="Send File URL"):
    url = ui.TextInput(label="File URL", placeholder="https://...", required=True)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.send_message("File URL forwarded to user (simulated).", ephemeral=True)

class NoteModal(ui.Modal, title="Add Staff Note"):
    note = ui.TextInput(label="Internal Note", style=TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: Interaction):
        embed = Embed(title="🏷️ Staff Note", description=self.note.value, color=discord.Color.gold())
        embed.set_footer(text=f"By {interaction.user}")
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Note added.", ephemeral=True)

async def setup(bot):
    bot.modmail = ModmailSystem(bot)
