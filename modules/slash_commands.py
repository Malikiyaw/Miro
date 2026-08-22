import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from data_manager import dm
from logger import logger
from modules import (
    economy, leveling, verification, tickets, suggestions,
    giveaways, reminders, welcome_leave, auto_setup, config_panels
)
from modules.guardian import GuardianSystem

class SlashCommands(commands.Cog):
    """Slash commands for the bot."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="autosetup", description="Set up bot systems for your server")
    @app_commands.checks.has_permissions(administrator=True)
    async def autosetup(self, interaction: discord.Interaction):
        """Start the auto-setup process."""
        await self.bot.auto_setup.start_setup(interaction)

    @app_commands.command(name="configpanel", description="Open configuration panel for a system")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(system="The system to configure")
    @app_commands.choices(system=[
        app_commands.Choice(name="Verification", value="verification"),
        app_commands.Choice(name="Economy", value="economy"),
        app_commands.Choice(name="Leveling", value="leveling"),
        app_commands.Choice(name="Tickets", value="tickets"),
        app_commands.Choice(name="Suggestions", value="suggestions"),
        app_commands.Choice(name="Giveaways", value="giveaways"),
        app_commands.Choice(name="Welcome/Leave", value="welcome_leave"),
        app_commands.Choice(name="Reminders", value="reminders"),
        app_commands.Choice(name="Anti-Raid", value="anti_raid"),
        app_commands.Choice(name="Auto-Mod", value="auto_mod"),
        app_commands.Choice(name="Warnings", value="warnings"),
        app_commands.Choice(name="Announcements", value="announcements"),
        app_commands.Choice(name="Auto-Responder", value="auto_responder"),
        app_commands.Choice(name="Reaction Roles", value="reaction_roles"),
        app_commands.Choice(name="Staff Shifts", value="staff_shifts"),
        app_commands.Choice(name="Staff Reviews", value="staff_reviews"),
        app_commands.Choice(name="Starboard", value="starboard"),
        app_commands.Choice(name="AI Chat", value="ai_chat"),
        app_commands.Choice(name="Modmail", value="modmail"),
        app_commands.Choice(name="Logging", value="logging")
    ])
    async def configpanel(self, interaction: discord.Interaction, system: str):
        """Open configuration panel for a system."""
        if not interaction.guild:
            return await interaction.response.send_message("This command only works in servers.", ephemeral=True)
        panel = config_panels.get_config_panel(interaction.guild.id, system, self.bot)
        if not panel:
            return await interaction.response.send_message(f"❌ System '{system}' not found.", ephemeral=True)

        embed = discord.Embed(
            title=f"⚙️ {system.replace('_', ' ').title()} Configuration",
            description="Use the buttons below to configure this system.",
            color=discord.Color.blue()
        )

        emoji, description = config_panels.get_system_info(system)
        embed.add_field(name=f"{emoji} System", value=description, inline=False)

        # Panels imported from feature modules (verification, economy, ...)
        # don't share ConfigPanelView's accessor — fall back to their real
        # storage key instead of crashing.
        config = None
        try:
            config = panel.get_config()
        except AttributeError:
            key_overrides = {
                "verification": "verification_config", "economy": "economy_config",
                "leveling": "leveling_config", "tickets": "tickets_config",
                "suggestions": "suggestions_config", "giveaways": "giveaways_config",
            }
            key = key_overrides.get(system, f"{system}_config")
            config = dm.get_guild_data(interaction.guild.id, key, {})
        if isinstance(config, dict) and config:
            settings = "\n".join(f"**{k}:** `{str(v)[:50]}`" for k, v in list(config.items())[:8])
            embed.add_field(name="Current Settings", value=settings or "_No settings_", inline=False)

        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)

    # AI chat: /bot <text>
    @app_commands.command(name="bot", description="Chat with Miro's AI - ask anything or request features")
    @app_commands.describe(text="Your message to Miro's AI (question, idea, or feature request)")
    async def miro_chat(self, interaction: discord.Interaction, text: str):
        """Send any text to the server's AI and reply with its response."""
        if not interaction.guild:
            return await interaction.response.send_message("This command only works in servers.", ephemeral=True)

        # Rate limit per user on the AI tier (admins exempt from emergency stop)
        limiter = getattr(self.bot, "rate_limiter", None)
        if limiter is not None:
            allowed, retry_after = limiter.check("ai", interaction.user.id,
                                                 exempt=interaction.user.guild_permissions.administrator)
            if not allowed:
                return await interaction.response.send_message(
                    f"⏳ You're sending requests too quickly. Try again in {retry_after:.0f}s.",
                    ephemeral=True,
                )

        await interaction.response.defer(thinking=True)

        bus = getattr(self.bot, "event_bus", None)
        if bus is not None:
            await bus.publish("ai.request", guild_id=interaction.guild.id, user_id=interaction.user.id,
                              source="/bot")

        try:
            result = await self.bot.ai.chat(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                user_input=text[:2000],
                persist=True,  # remember this exchange so follow-ups like "proceed" keep context
                system_prompt=(
                    "You are Miro, a helpful and proactive Discord server assistant. "
                    "Answer the user's question directly and concisely. If they describe a feature "
                    "you could build, explain what you would set up for them."
                ),
            )
        except asyncio.TimeoutError:
            logger.warning(f"/bot AI request timed out for user {interaction.user.id}")
            return await interaction.followup.send(
                "⏱️ The AI took too long to answer (large requests like server changes can be slow). "
                "Please try again — it usually works on the second attempt.",
            )
        except Exception as e:
            name = type(e).__name__
            if "Retry" in name or "429" in str(e):
                msg = ("⏳ My AI providers are rate-limited right now (too many requests). "
                       "Give it a minute and try again.")
            elif "Timeout" in name or "timeout" in str(e).lower():
                msg = ("⏱️ The AI took too long to answer. Please try again — "
                       "it usually works on the second attempt.")
            elif "empty response" in str(e).lower():
                msg = ("🤖 The AI model kept returning blank answers. This usually clears up on "
                       "retry — if it persists, an admin can run `/config test` or pick another "
                       "model via `/config model`.")
            else:
                logger.error(f"/bot AI request failed for user {interaction.user.id}: {e}")
                msg = ("⚠️ I couldn't complete that request. Please try again in a minute — "
                       "or ask an admin to check `/config status` / `/config test`.")
            return await interaction.followup.send(msg)

        if not isinstance(result, dict):
            result = {"summary": str(result)}

        error = result.get("error")
        if error:
            return await interaction.followup.send(f"⚠️ AI error: {str(error)[:500]}")

        # Pull the actual answer from whichever field the model used
        reply = ""
        for key in ("summary", "response", "content", "message", "reply", "answer", "text"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                reply = value.strip()
                break

        # Agentic execution: the model's plan runs through the agent runtime
        # (tool → observe → replan → final answer). Internal planning text
        # never reaches Discord — only the final response + a report.
        actions = result.get("actions") or []
        if isinstance(actions, list) and actions:
            from core.guild_ai_config import GuildAIConfig
            gcfg = GuildAIConfig.load(interaction.guild.id)
            try:
                interaction._miro_ai_source = True
            except Exception:
                pass

            if gcfg.agent_enabled:
                from core.agent_runtime import AgentRuntime
                allow_dangerous = bool(
                    interaction.user.guild_permissions.administrator)
                runtime = AgentRuntime(self.bot, interaction.guild, interaction.user,
                                       allow_dangerous=allow_dangerous)
                # One persistent progress message — never repeated AI narration
                progress_msg = None

                async def on_progress(text: str):
                    nonlocal progress_msg
                    try:
                        if progress_msg is None:
                            progress_msg = await interaction.followup.send(text[:1900])
                        else:
                            await progress_msg.edit(content=text[:1900])
                    except Exception:
                        pass
                runtime.on_progress = on_progress

                final, exec_result = await runtime.run(
                    interaction, text[:2000],
                    ("You are Miro, a helpful and proactive Discord server assistant "
                     "executing an operation for a trusted administrator."),
                    initial_result=result)
                reply = final.text or "Done."
                # Final answer goes out as a fresh message; the progress
                # message above stays as the live execution log.
                if exec_result.observations:
                    lines = []
                    for obs in exec_result.observations:
                        if obs.success and obs.verified:
                            mark = "✅"
                        elif obs.success:
                            mark = "⚠️"
                        else:
                            mark = "❌"
                        line = f"{mark} `{obs.tool}`"
                        if not obs.success and obs.detail:
                            line += f" — {obs.detail[:80]}"
                        lines.append(line)
                    reply += "\n\n**Actions:**\n" + "\n".join(lines)
            else:
                # Agent mode off: direct one-pass dispatch (previous behavior)
                try:
                    interaction._miro_ai_source = True
                except Exception:
                    pass
                reports = []
                for action in actions[:5]:
                    if not isinstance(action, dict):
                        continue
                    name = str(action.get("name") or "").strip()
                    params = action.get("parameters")
                    if not name:
                        continue
                    try:
                        success, info = await self.bot.action_handler.dispatch(
                            interaction, name, params if isinstance(params, dict) else {})
                        detail = ""
                        if isinstance(info, dict) and info.get("error"):
                            detail = f": {str(info['error'])[:80]}"
                        reports.append(f"{'✅' if success else '❌'} `{name}`{detail}")
                    except Exception as e:
                        logger.error(f"/bot action {name} failed: {e}")
                        reports.append(f"❌ `{name}`: {str(e)[:80]}")
                if reports:
                    reply = (reply or "Done!") + "\n\n**Actions executed:**\n" + "\n".join(reports)

        if not reply:
            reply = "*(The AI didn't produce a readable answer — please try rephrasing your message.)*"

        # Discord hard-limits messages to 2000 chars; chunk long replies
        for i in range(0, len(reply), 2000):
            chunk = reply[i:i + 2000]
            if i == 0:
                await interaction.followup.send(chunk)
            else:
                await interaction.channel.send(chunk)

    # Unified system panels: /system <group>
    @app_commands.command(name="system", description="Open the unified control panel for a system")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(system="Which system panel to open")
    @app_commands.choices(system=[
        app_commands.Choice(name="👤 Member Management (Verification/Welcome/Leave)", value="member_management"),
        app_commands.Choice(name="💰 Progression (Economy/Leveling)", value="progression"),
        app_commands.Choice(name="🎫 Tickets", value="tickets"),
        app_commands.Choice(name="💡 Suggestions", value="suggestions"),
        app_commands.Choice(name="🎁 Giveaways", value="giveaways"),
        app_commands.Choice(name="📢 Communications (Announcements/Reminders)", value="communications"),
        app_commands.Choice(name="🛡️ Anti-Raid", value="anti_raid"),
        app_commands.Choice(name="🔨 Moderation (Auto-Mod/Warnings)", value="moderation"),
        app_commands.Choice(name="⚙️ Automation (Auto-Responder/Reaction Roles)", value="automation"),
        app_commands.Choice(name="👮 Staff Management (Shifts/Reviews)", value="staff_management"),
        app_commands.Choice(name="🤖 Miro AI (Provider/Model/Agent)", value="ai"),
    ])
    async def system_panel(self, interaction: discord.Interaction, system: str):
        if not interaction.guild:
            return await interaction.response.send_message("This command only works in servers.", ephemeral=True)
        # Panel construction reads server data — defer so Discord never times out
        await interaction.response.defer()
        from modules.system_panels import open_system_panel
        await open_system_panel(interaction, system)

    # Economy commands
    @app_commands.command(name="balance", description="Check your coin balance")
    async def balance(self, interaction: discord.Interaction):
        await self.bot.economy.balance(interaction)

    @app_commands.command(name="daily", description="Claim your daily coins")
    async def daily(self, interaction: discord.Interaction):
        await self.bot.economy.daily(interaction)

    @app_commands.command(name="work", description="Work for coins")
    async def work(self, interaction: discord.Interaction):
        await self.bot.economy.work(interaction)

    @app_commands.command(name="transfer", description="Transfer coins to another user")
    @app_commands.describe(user="User to transfer to", amount="Amount of coins")
    async def transfer(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        await self.bot.economy.transfer(interaction, user, amount)

    @app_commands.command(name="shop", description="Browse the server shop")
    async def shop(self, interaction: discord.Interaction):
        await self.bot.economy.shop(interaction)

    @app_commands.command(name="buy", description="Buy an item from the shop")
    @app_commands.describe(item="Name of the item to buy")
    async def buy(self, interaction: discord.Interaction, item: str):
        await self.bot.economy.buy(interaction, item)

    @app_commands.command(name="leaderboard", description="View economy leaderboard")
    async def leaderboard(self, interaction: discord.Interaction):
        await self.bot.economy.leaderboard(interaction)

    @app_commands.command(name="challenge", description="View daily challenge")
    async def challenge(self, interaction: discord.Interaction):
        await self.bot.economy.challenge(interaction)

    # Leveling commands
    @app_commands.command(name="rank", description="Check your leveling rank")
    async def rank(self, interaction: discord.Interaction):
        await self.bot.leveling.rank(interaction)

    @app_commands.command(name="lvlleaderboard", description="View leveling leaderboard")
    async def lvlleaderboard(self, interaction: discord.Interaction):
        await self.bot.leveling.leaderboard(interaction)

    @app_commands.command(name="rewards", description="View level rewards")
    async def rewards(self, interaction: discord.Interaction):
        await self.bot.leveling.rewards(interaction)

    # Ticket commands
    @app_commands.command(name="ticket", description="Create a new support ticket")
    async def ticket(self, interaction: discord.Interaction):
        await self.bot.tickets.create_ticket(interaction)

    # Suggestion commands
    @app_commands.command(name="suggest", description="Create a new suggestion")
    async def suggest(self, interaction: discord.Interaction):
        await self.bot.suggestions.create_suggestion(interaction)

    # Giveaway commands
    @app_commands.command(name="giveaway", description="Create a new giveaway")
    @app_commands.describe(prize="What to give away", duration="Duration in seconds", winners="Number of winners")
    @app_commands.checks.has_permissions(administrator=True)
    async def giveaway(self, interaction: discord.Interaction, prize: str, duration: int, winners: int = 1):
        await self.bot.giveaways.create_giveaway(interaction, prize, duration, winners)

    # Reminder commands
    @app_commands.command(name="remind", description="Set a reminder")
    @app_commands.describe(message="Reminder message", time="Time in seconds")
    async def remind(self, interaction: discord.Interaction, message: str, time: int):
        await self.bot.reminders.create_reminder(interaction, message, time, False)

    @app_commands.command(name="reminders", description="List your reminders")
    async def reminders(self, interaction: discord.Interaction):
        await self.bot.reminders.list_reminders(interaction)

    # Warning commands
    @app_commands.command(name="warn", description="Warn a user")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(user="User to warn", reason="Warning reason", severity="Warning severity")
    @app_commands.choices(severity=[
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="High", value="high")
    ])
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str, severity: str = "medium"):
        await self.bot.warnings.warn_user(interaction, user, reason, severity)

    @app_commands.command(name="warnings", description="View user warnings")
    @app_commands.describe(user="User to check (optional)")
    async def warnings(self, interaction: discord.Interaction, user: discord.Member = None):
        await self.bot.warnings.get_user_warnings(interaction, user or interaction.user)

    # Staff shift commands
    @app_commands.command(name="shift", description="Manage staff shifts")
    @app_commands.describe(action="Shift action")
    @app_commands.choices(action=[
        app_commands.Choice(name="Start", value="start"),
        app_commands.Choice(name="End", value="end"),
        app_commands.Choice(name="Break Start", value="break_start"),
        app_commands.Choice(name="Break End", value="break_end")
    ])
    async def shift(self, interaction: discord.Interaction, action: str):
        if action == "start":
            await self.bot.staff_shifts.start_shift(interaction)
        elif action == "end":
            await self.bot.staff_shifts.end_shift(interaction)
        elif action == "break_start":
            await self.bot.staff_shifts.start_break(interaction)
        elif action == "break_end":
            await self.bot.staff_shifts.end_break(interaction)

    @app_commands.command(name="myshifts", description="View your shift history")
    async def myshifts(self, interaction: discord.Interaction):
        await self.bot.staff_shifts.get_my_shifts(interaction)

    # Application commands
    @app_commands.command(name="apply", description="Apply for staff position")
    async def apply(self, interaction: discord.Interaction):
        await self.bot.applications.create_application(interaction)

    # Appeal commands
    @app_commands.command(name="appeal", description="Appeal a warning")
    async def appeal(self, interaction: discord.Interaction):
        await self.bot.appeals.create_appeal(interaction)

async def setup(bot):
    await bot.add_cog(SlashCommands(bot))
    # Reuse the instance created in MiroBot.__init__ so its listeners and
    # bot.guardian references point at the same object.
    await bot.add_cog(getattr(bot, "guardian", None) or GuardianSystem(bot))