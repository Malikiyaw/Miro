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

    # Legacy per-system names -> unified group keys (backward compatibility)
    LEGACY_PANEL_ALIASES = {
        "verification": "member_management", "welcome": "member_management",
        "welcome_leave": "member_management",
        "economy": "progression", "leveling": "progression",
        "shop": "progression", "gamification": "progression",
        "tournaments": "progression", "events": "communications",
        "auto_mod": "moderation", "automod": "moderation", "warnings": "moderation",
        "logging": "moderation", "modlog": "moderation", "appeals": "moderation",
        "announcements": "communications", "reminders": "communications",
        "modmail": "communications", "auto_publisher": "communications",
        "starboard": "automation", "reaction_menus": "automation",
        "role_buttons": "automation", "trigger_roles": "automation",
        "staff_shifts": "staff_management", "staff_reviews": "staff_management",
        "staff_promo": "staff_management", "applications": "staff_management",
        "ai_chat": "ai",
    }

    @app_commands.command(name="configpanel", description="Open configuration panel for a system")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(system="The system to configure")
    @app_commands.choices(system=[
        app_commands.Choice(name="👤 Member Management (Verification/Welcome/Leave)", value="member_management"),
        app_commands.Choice(name="💰 Progression (Economy/Leveling/Shop/Games)", value="progression"),
        app_commands.Choice(name="🎫 Tickets", value="tickets"),
        app_commands.Choice(name="💡 Suggestions", value="suggestions"),
        app_commands.Choice(name="🎁 Giveaways", value="giveaways"),
        app_commands.Choice(name="📢 Communications (Announcements/Reminders/Modmail)", value="communications"),
        app_commands.Choice(name="🛡️ Anti-Raid (+ Guardian)", value="anti_raid"),
        app_commands.Choice(name="🔨 Moderation (Auto-Mod/Warnings/Appeals/Logs)", value="moderation"),
        app_commands.Choice(name="⚙️ Automation (Auto-Responder/Roles/Starboard)", value="automation"),
        app_commands.Choice(name="👮 Staff Management (Shifts/Reviews/Promotions/Applications)", value="staff_management"),
        app_commands.Choice(name="🤖 Miro AI (Engine/Chat/Health)", value="ai"),
    ])
    async def configpanel(self, interaction: discord.Interaction, system: str):
        """Open the unified configuration panel for a system group."""
        if not interaction.guild:
            return await interaction.response.send_message("This command only works in servers.", ephemeral=True)
        resolved = self.LEGACY_PANEL_ALIASES.get(system, system)
        # Panel construction reads server data — defer so Discord never times out
        await interaction.response.defer(ephemeral=True)
        from modules.system_panels import open_system_panel
        await open_system_panel(interaction, resolved)


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
                    "you could build, explain what you would set up for them. "
                    "You have full memory of this conversation. When the user replies with a "
                    "follow-up such as 'proceed', 'yes', 'do it', 'continue' or 'go ahead', treat it "
                    "as confirmation of the previously discussed request and act on it (or clearly "
                    "state the next step) instead of asking what they mean."
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
        # Native provider responses carry calls under `tool_calls`; legacy
        # JSON plans under `actions`. Both must execute.
        actions = result.get("tool_calls") or result.get("actions") or []

        # SAME-CHANNEL MEMORY: a short confirmation ("proceed", "yes", "do it")
        # after a pending agent plan must re-enter the agent path even when the
        # plain-chat model returned no tool calls. Previously the turn-1 plan
        # and the pause message were never persisted, so this check had nothing
        # to resolve and the user got a generic "could you clarify" reply.
        force_agent = False
        if not (isinstance(actions, list) and actions):
            try:
                from agent.request_classifier import classify_with_history
                from history_manager import history_manager as _hm
                _hist = await _hm.get_enhanced_context(interaction.guild.id, interaction.user.id, depth=10)
                _recent = [{"role": m.get("role", ""), "content": m.get("content", "")} for m in (_hist or [])]
                _cls = classify_with_history(text, _recent)
                if _cls.execution_required:
                    force_agent = True
                    # Expand the vague follow-up with the prior mutation intent
                    _last_mut = ""
                    for m in reversed(_recent):
                        _c = (m.get("content") or "")
                        if m.get("role") == "user" and any(
                                p in _c.lower() for p in ("create", "delete", "remove", "make", "add",
                                                          "automation", "channel", "role", "duplicate",
                                                          "setup", "configure", "ban", "kick", "lock")):
                            _last_mut = _c.strip()[:800]
                            if _last_mut.lower() != text.strip().lower():
                                break
                    if _last_mut:
                        text = f"{_last_mut} | follow-up: {text.strip()}"
                    logger.info(f"/bot follow-up upgraded to agent execution: {text[:120]}")
            except Exception as e:
                logger.debug(f"/bot history-aware follow-up check failed: {e}")

        if (isinstance(actions, list) and actions) or force_agent:
            from core.guild_ai_config import GuildAIConfig
            gcfg = GuildAIConfig.load(interaction.guild.id)
            try:
                interaction._miro_ai_source = True
            except Exception:
                pass

            if gcfg.agent_enabled:
                from core.agent_runtime import AgentRuntime, needs_confirmation
                allow_dangerous = bool(
                    interaction.user.guild_permissions.administrator)

                # Destructive plans need explicit human confirmation (V4 item 26)
                confirmed = True
                if allow_dangerous and needs_confirmation(actions):
                    from ui.components import ConfirmView
                    names = [str(a.get("name")) for a in actions if isinstance(a, dict)][:8]
                    confirm_view = ConfirmView(
                        interaction.user.id,
                        "🧹 This plan includes **destructive actions**:\n"
                        + ", ".join(f"`{n}`" for n in names)
                        + "\n\nProceed with execution?",
                        danger=True, timeout=30)
                    await interaction.followup.send(
                        "⚠️ Confirmation required before executing this plan.",
                        view=confirm_view, ephemeral=True)
                    await confirm_view.wait()
                    confirmed = confirm_view.confirmed
                    if not confirmed:
                        return await interaction.followup.send(
                            "❎ Cancelled — nothing was executed.", ephemeral=True)

                runtime = AgentRuntime(self.bot, interaction.guild, interaction.user,
                                       allow_dangerous=allow_dangerous,
                                       confirmed=confirmed)
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
                    initial_result={"summary": str(result.get("summary") or ""),
                                    "tool_calls": actions} if actions else None)
                reply = final.text or "Done."

                # PERSIST the agent final — including confirmation pauses like
                # "tell me to proceed" — so the next turn can resolve "proceed".
                try:
                    from history_manager import history_manager as _hm2
                    if reply.strip():
                        await _hm2.add_exchange(
                            interaction.guild.id, interaction.user.id,
                            text[:2000], reply[:4000])
                except Exception as _pe:
                    logger.debug(f"/bot agent final persist failed: {_pe}")

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
                # Agent mode off: direct one-pass dispatch (previous behavior).
                # Normalize native {function:{name,arguments}} entries first.
                try:
                    from core.agent_runtime import AgentRuntime as _AR
                    norm = _AR._normalize_actions(actions)
                except Exception:
                    norm = [a for a in actions if isinstance(a, dict)]
                try:
                    interaction._miro_ai_source = True
                except Exception:
                    pass
                reports = []
                for action in norm[:5]:
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
            # Diagnostic fallback: say WHAT happened (tool calls issued or not)
            actions_ran = len(result.get("actions") or [])
            diag = (f"the model issued {actions_ran} tool call(s) but produced no readable "
                    f"summary") if actions_ran else "no tool calls were issued"
            reply = (f"⚠️ The AI returned an unreadable response ({diag}). "
                     f"Please rephrase, or run `/config test` to check the AI provider.")

        # Discord hard-limits messages to 2000 chars; chunk long replies
        for i in range(0, len(reply), 2000):
            chunk = reply[i:i + 2000]
            if i == 0:
                await interaction.followup.send(chunk)
            else:
                await interaction.channel.send(chunk)

    # Automation manager (admin, ephemeral)
    @app_commands.command(name="automations", description="Manage AI-created automations (pause/resume/test/delete)")
    @app_commands.checks.has_permissions(administrator=True)
    async def automations(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("This command only works in servers.", ephemeral=True)
        from modules.automation_manager import AutomationManagerView
        view = AutomationManagerView(self.bot, interaction.user.id, interaction.guild.id)
        embed = view.build_embed()
        if not view._names():
            return await interaction.response.send_message(
                "ℹ️ No automations yet. Ask the AI: `/bot create an automation that posts daily stats at 9am`",
                ephemeral=True)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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