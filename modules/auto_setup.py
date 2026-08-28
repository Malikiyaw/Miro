import discord
from discord import ui, app_commands
import asyncio
import json
import time
import traceback
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from data_manager import dm
from logger import logger

class SetupState(Enum):
    PENDING = "pending"
    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"

@dataclass
class ServerSetup:
    guild_id: int
    state: SetupState
    started_at: float
    completed_at: Optional[float]
    steps_completed: List[str]
    config: dict
    selected_systems: Optional[List[str]] = None

class SystemCategory:
    """
    The 10 consolidated setup groups. Each group installs its underlying
    systems (all original functionality preserved underneath).
    """
    MEMBER_MANAGEMENT = {"name": "👤 Member Management", "emoji": "👤", "group_key": "member_management",
                         "systems": ["verification", "welcome_leave"]}
    PROGRESSION = {"name": "💰 Progression", "emoji": "💰", "group_key": "progression",
                   "systems": ["economy", "leveling", "shop", "gamification", "tournaments", "events"]}
    TICKETS = {"name": "🎫 Tickets", "emoji": "🎫", "group_key": "tickets",
               "systems": ["tickets"]}
    SUGGESTIONS = {"name": "💡 Suggestions", "emoji": "💡", "group_key": "suggestions",
                   "systems": ["suggestions"]}
    GIVEAWAYS = {"name": "🎁 Giveaways", "emoji": "🎁", "group_key": "giveaways",
                 "systems": ["giveaways"]}
    COMMUNICATIONS = {"name": "📢 Communications", "emoji": "📢", "group_key": "communications",
                      "systems": ["announcements", "reminders", "modmail", "auto_publisher"]}
    ANTI_RAID = {"name": "🛡️ Anti-Raid", "emoji": "🛡️", "group_key": "anti_raid",
                 "systems": ["anti_raid", "guardian"]}
    MODERATION = {"name": "🔨 Moderation", "emoji": "🔨", "group_key": "moderation",
                  "systems": ["automod", "warnings", "moderation", "appeals", "logging", "mod_logging"]}
    AUTOMATION = {"name": "⚙️ Automation", "emoji": "⚙️", "group_key": "automation",
                  "systems": ["auto_responder", "reaction_roles", "reaction_menus",
                              "role_buttons", "trigger_roles", "starboard"]}
    STAFF_MANAGEMENT = {"name": "👮 Staff Management", "emoji": "👮", "group_key": "staff_management",
                        "systems": ["staff_shifts", "staff_reviews", "staff_promo", "applications"]}

    @classmethod
    def get_all_categories(cls):
        return [cls.MEMBER_MANAGEMENT, cls.PROGRESSION, cls.TICKETS, cls.SUGGESTIONS,
                cls.GIVEAWAYS, cls.COMMUNICATIONS, cls.ANTI_RAID, cls.MODERATION,
                cls.AUTOMATION, cls.STAFF_MANAGEMENT]

    @classmethod
    def find_by_key(cls, group_key: str):
        for category in cls.get_all_categories():
            if category["group_key"] == group_key:
                return category
        return None


# One-click curated bundles: preset name -> systems to install
SETUP_PRESETS = {
    "gaming": {
        "label": "🎮 Gaming Community",
        "systems": ["verification", "welcome_leave", "leveling", "economy", "shop",
                    "gamification", "tournaments", "giveaways", "starboard"],
    },
    "support": {
        "label": "💼 Support Server",
        "systems": ["verification", "tickets", "modmail", "announcements", "reminders",
                    "automod", "warnings", "moderation", "logging", "applications",
                    "staff_shifts"],
    },
    "community": {
        "label": "👥 Small Community",
        "systems": ["verification", "welcome_leave", "suggestions", "giveaways",
                    "auto_responder", "reaction_roles", "reminders", "announcements"],
    },
    "everything": {
        "label": "🏢 Everything",
        "systems": [s for cat in SystemCategory.get_all_categories() for s in cat["systems"]],
    },
}


class PreflightReport:
    """Result of pre-installation checks.

    Blocking failures (missing permissions) prevent installation.
    Warnings (e.g. low role position) never block — setup proceeds and
    the user is told what may need manual attention afterwards.
    """

    def __init__(self):
        self.ok = True
        self.has_warnings = False
        self.lines = []

    def add(self, passed: bool, text: str):
        """Blocking check: failure prevents installation."""
        if passed:
            self.lines.append("✅ " + text)
        else:
            self.ok = False
            self.lines.append("❌ " + text)

    def warn(self, text: str):
        """Non-blocking advisory."""
        self.has_warnings = True
        self.lines.append("⚠️ " + text)


class SetupProgress:
    """Single live embed showing per-system install status. No channel spam."""

    ICONS = {"pending": "⏳", "running": "🔧", "ok": "✅", "warn": "⚠️", "fail": "❌"}

    def __init__(self, interaction, systems: List[str], resume_from: int = 0):
        self.interaction = interaction
        self.systems = list(systems)
        self.status = {s: ("ok" if i < resume_from else "pending") for i, s in enumerate(systems)}
        self.started = time.time()
        self.message = None

    def _bar(self) -> str:
        done = sum(1 for v in self.status.values() if v in ("ok", "warn", "fail"))
        total = max(len(self.systems), 1)
        filled = int(20 * done / total)
        return "█" * filled + "░" * (20 - filled) + f" {done}/{total}"

    def build_embed(self) -> discord.Embed:
        lines = [f"{self.ICONS[self.status[s]]} {s.replace('_', ' ').title()}" for s in self.systems]
        elapsed = max(1, int(time.time() - self.started))
        embed = discord.Embed(
            title="⚙️ Installing Systems…",
            description="`" + self._bar() + "`",
            color=discord.Color.orange(),
        )
        half = (len(lines) + 1) // 2
        embed.add_field(name="Progress", value="\n".join(lines[:half]) or "—", inline=True)
        if lines[half:]:
            embed.add_field(name="‎", value="\n".join(lines[half:]), inline=True)
        embed.set_footer(text=f"Elapsed: {elapsed}s · installing safely with rate limiting")
        return embed

    async def start(self):
        # ACK IMMEDIATELY — component interactions must be acknowledged
        # within 3 seconds or Discord shows "didn't respond in time".
        # defer() acks without changing the message; after that @original
        # reliably resolves to the wizard/confirm message for later edits.
        try:
            if not self.interaction.response.is_done():
                await self.interaction.response.defer()
        except Exception:
            pass
        try:
            self.message = await self.interaction.original_response()
            await self.message.edit(embed=self.build_embed(), view=None)
            return
        except Exception:
            self.message = None
        try:
            await self.interaction.response.edit_message(embed=self.build_embed(), view=None)
            self.message = await self.interaction.original_response()
        except Exception:
            pass

    async def set_status(self, system: str, status: str):
        self.status[system] = status
        if self.message is not None:
            try:
                await self.message.edit(embed=self.build_embed())
            except Exception:
                pass

    async def finish(self, embed: discord.Embed, view=None):
        if self.message is not None:
            try:
                await self.message.edit(embed=embed, view=view)
                return
            except Exception:
                pass
        try:
            await self.interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            pass


async def safe_edit(interaction, **kwargs):
    """Edit the wizard message regardless of interaction state."""
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(**kwargs)
        else:
            await interaction.response.edit_message(**kwargs)
        return
    except Exception:
        pass
    try:
        msg = await interaction.original_response()
        await msg.edit(**kwargs)
    except Exception:
        pass


class AutoSetupSystem:
    """
    Complete auto-setup system that installs and configures all bot systems.
    Features:
    - Interactive system selection + one-click curated presets
    - Automatic channel/role creation (fully recorded for undo)
    - Pre-flight permission checks
    - Single live-progress embed (no channel spam)
    - Resume interrupted setups
    """

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Manifest (undo support)                                            #
    # ------------------------------------------------------------------ #

    def _manifest(self, guild_id: int) -> dict:
        manifests = dm.load_json("setup_manifests", default={})
        return manifests.get(str(guild_id), {})

    def _record(self, guild_id: int, system: str, kind: str, object_id: int):
        """Record a created channel/role in the setup manifest for undo."""
        try:
            manifests = dm.load_json("setup_manifests", default={})
            entry = manifests.setdefault(str(guild_id), {"systems": {}, "created_at": time.time()})
            sys_entry = entry["systems"].setdefault(system, {"channels": [], "roles": []})
            bucket = sys_entry.setdefault(kind + "s", [])
            if object_id not in bucket:
                bucket.append(object_id)
            dm.save_json("setup_manifests", manifests)
        except Exception as e:
            logger.warning(f"Manifest record failed: {e}")

    async def undo_setup(self, guild) -> Tuple[int, int]:
        """Delete only the channels/roles Miro created during setup.
        Returns (channels_removed, roles_removed)."""
        manifest = self._manifest(guild.id)
        removed_ch = removed_r = 0
        for _system, entry in manifest.get("systems", {}).items():
            for cid in entry.get("channels", []):
                ch = guild.get_channel(int(cid)) if str(cid).isdigit() else None
                if ch is not None:
                    try:
                        await ch.delete(reason="Miro auto-setup: undo")
                        removed_ch += 1
                    except Exception as e:
                        logger.warning(f"Undo: could not delete channel {cid}: {e}")
            for rid in entry.get("roles", []):
                role = guild.get_role(int(rid)) if str(rid).isdigit() else None
                if role is not None:
                    try:
                        await role.delete(reason="Miro auto-setup: undo")
                        removed_r += 1
                    except Exception as e:
                        logger.warning(f"Undo: could not delete role {rid}: {e}")
        manifests = dm.load_json("setup_manifests", default={})
        manifests.pop(str(guild.id), None)
        dm.save_json("setup_manifests", manifests)
        completed = dm.load_json("completed_setups", default={})
        completed.pop(str(guild.id), None)
        dm.save_json("completed_setups", completed)
        return removed_ch, removed_r

    # ------------------------------------------------------------------ #
    # Pre-flight checks                                                  #
    # ------------------------------------------------------------------ #

    def run_preflight(self, guild) -> PreflightReport:
        report = PreflightReport()
        me = guild.me
        perms = me.guild_permissions if me else None
        # Blocking checks — without these the installer cannot create anything
        report.add(bool(perms and perms.administrator), "Bot has Administrator permission")
        report.add(bool(perms and perms.manage_channels), "Bot can manage channels")
        report.add(bool(perms and perms.manage_roles), "Bot can manage roles")
        # Advisory only: creating roles/channels works from any position.
        # A low top-role just means Miro cannot ASSIGN/manage roles placed
        # above it, so created roles may need manual reordering later.
        top = me.top_role if me else None
        others = [r for r in guild.roles if not r.is_default() and r != top]
        higher = sum(1 for r in others if top is None or r.position > top.position)
        if top is None or higher:
            report.warn(
                f"Bot's highest role sits below {higher} other role(s) (position: {top.position if top else '?'}). "
                "Setup will continue — roles created during setup are placed just under the bot's role; "
                "drag them higher in Server Settings → Roles if members should outrank them.")
        return report

    # ------------------------------------------------------------------ #
    # Wizard entry                                                       #
    # ------------------------------------------------------------------ #

    async def start_setup(self, interaction):
        """Start the auto-setup process."""
        # defer-aware helper — slash wrapper now defers within 3s before calling us
        async def _send(**kw):
            try:
                if interaction.response.is_done():
                    return await interaction.followup.send(**kw)
                return await interaction.response.send_message(**kw)
            except Exception as e:
                try:
                    return await interaction.followup.send(**kw)
                except Exception:
                    logger.error(f"start_setup send failed: {e}")
                    raise
        if not interaction.user.guild_permissions.administrator:
            return await _send(content="❌ Only administrators can use auto-setup.", ephemeral=True)

        gid = str(interaction.guild.id)

        # Resume check: an interrupted setup exists?
        pending = dm.load_json("pending_setups", default={}).get(gid)
        completed = dm.load_json("completed_setups", default={}).get(gid)

        if pending and isinstance(pending.get("selected_systems"), list):
            done = pending.get("completed", [])
            remaining = [s for s in pending["selected_systems"] if s not in done]
            embed = discord.Embed(
                title="⏸️ Interrupted Setup Found",
                description=(
                    f"A previous setup was interrupted **{int(time.time() - pending.get('started_at', time.time()))}s ago**.\n\n"
                    f"**Completed:** {len(done)} system(s)\n"
                    f"**Remaining:** {len(remaining)} system(s)"
                    + ("\n".join(f"• {s}" for s in remaining[:10]) if remaining else "")
                ),
                color=discord.Color.gold(),
            )
            await _send(embed=embed, view=ResumeSetupView(self, interaction.guild.id, remaining), ephemeral=True)
            return

        if completed:
            manifest = self._manifest(interaction.guild.id)
            n_created = sum(len(v.get("channels", []) + v.get("roles", []))
                            for v in manifest.get("systems", {}).values())
            embed = discord.Embed(
                title="✅ This server has already been set up!",
                description=(
                    f"Installed by <@{completed.get('installed_by', '?')}>"
                    f" at <t:{int(completed.get('completed_at', 0))}:f>.\n"
                    f"Systems installed: **{len(completed.get('systems_installed', []))}**\n\n"
                    "You can re-run parts of it anytime, or undo everything below."
                ),
                color=discord.Color.green(),
            )
            view = AlreadySetupView(self) if n_created else None
            await _send(embed=embed, view=view, ephemeral=True)
            return

        # Fresh run: show pre-flight report up front
        report = self.run_preflight(interaction.guild)
        if not report.ok:
            color = discord.Color.red()
        elif report.has_warnings:
            color = discord.Color.gold()
        else:
            color = discord.Color.green()
        embed = discord.Embed(
            title="🤖 Miro Bot Auto-Setup",
            description="Welcome to the automated setup wizard!\n\n"
                        "**What will be created:**\n"
                        "• Roles and channels for each system\n"
                        "• Default configurations · fully undoable afterwards\n\n"
                        "**Pre-flight checks:**\n" + "\n".join(report.lines),
            color=color,
        )
        view = SetupStartView(self, preflight_ok=report.ok)
        await _send(embed=embed, view=view, ephemeral=True)

    async def begin_system_selection(self, interaction):
        """Show system category selection."""
        embed = discord.Embed(
            title="🎯 Select Systems to Install",
            description="Choose which systems you want to set up. You can select entire categories or individual systems.\n\n**Recommended for most servers:** Security, Moderation, and Automation categories.",
            color=discord.Color.blue()
        )

        view = CategorySelectView(self, interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def show_category_systems(self, interaction, category):
        """Show systems within a category for selection."""
        embed = discord.Embed(
            title=f"{category['emoji']} {category['name']}",
            description="Select the systems you want to install:",
            color=discord.Color.blue()
        )

        for system in category["systems"]:
            embed.add_field(
                name=f"🔧 {system.replace('_', ' ').title()}",
                value=self.get_system_description(system),
                inline=False
            )

        view = SystemSelectView(self, interaction.guild.id, category)
        await interaction.response.edit_message(embed=embed, view=view)

    def get_system_description(self, system: str) -> str:
        """Get description for a system."""
        descriptions = {
            "verification": "CAPTCHA verification to prevent bots",
            "anti_raid": "Automatic raid detection, lockdown, and emergency controls",
            "guardian": "Bot token detection and removal",
            "automod": "Automated message moderation (13 rule types)",
            "auto_mod": "Automated message moderation",
            "warnings": "User warning and punishment system",
            "economy": "Coins, shop, and gambling system",
            "leveling": "XP and role rewards system",
            "giveaways": "Automated giveaway management",
            "gamification": "Fun games and challenges",
            "starboard": "Popular message highlighting",
            "mod_logging": "Moderation action logging",
            "logging": "General server event logging",
            "modmail": "Private staff messaging",
            "suggestions": "Community suggestion voting",
            "staff_promo": "Staff promotion management",
            "staff_shifts": "Staff shift tracking",
            "staff_reviews": "Staff performance reviews",
            "applications": "Staff application system",
            "appeals": "Punishment appeal system",
            "welcome_leave": "Welcome/leave messages",
            "tickets": "Support ticket system",
            "reminders": "Scheduled reminders",
            "announcements": "Announcement management",
            "auto_responder": "Automated keyword responses",
            "reaction_roles": "Role assignment via reactions",
            "reaction_menus": "Interactive reaction menus",
            "role_buttons": "Role buttons for self-assignment",
            "ai_chat": "AI-powered chat channels"
        }
        return descriptions.get(system, "System functionality")

    async def start_installation(self, interaction, selected_systems):
        """Begin system installation (live progress embed, no channel spam)."""
        gid = str(interaction.guild.id)

        # Resume support: skip already-completed systems
        pending = dm.load_json("pending_setups", default={}).get(gid) or {}
        done_before = [s for s in pending.get("completed", []) if s in selected_systems]
        resume_from = len(done_before)
        to_install = [s for s in selected_systems if s not in done_before]

        # Pre-flight gate
        report = self.run_preflight(interaction.guild)
        if not report.ok:
            embed = discord.Embed(
                title="❌ Pre-flight Checks Failed",
                description="Cannot install safely:\n" + "\n".join(report.lines),
                color=discord.Color.red(),
            )
            await safe_edit(interaction, embed=embed, view=None)
            return

        progress = SetupProgress(interaction, selected_systems, resume_from=resume_from)
        try:
            await progress.start()

            # Persist pending state (with completed steps for resume)
            pending_setups = dm.load_json("pending_setups", default={})
            pending_setups[gid] = {
                "user_id": interaction.user.id,
                "selected_systems": list(selected_systems),
                "completed": list(done_before),
                "started_at": time.time(),
                "channel_id": interaction.channel.id,
            }
            dm.save_json("pending_setups", pending_setups)

            results = await self.install_systems(interaction.guild, to_install,
                                                 interaction.user, interaction.channel,
                                                 progress=progress)

            # Update pending bookkeeping
            pending_setups = dm.load_json("pending_setups", default={})
            ok_systems = done_before + [s for s, ok in results.items() if ok]
            failed_systems = [s for s, ok in results.items() if not ok]

            if not failed_systems:
                completed_setups = dm.load_json("completed_setups", default={})
                completed_setups[gid] = {
                    "completed_at": time.time(),
                    "systems_installed": sorted(set(ok_systems)),
                    "installed_by": interaction.user.id,
                }
                dm.save_json("completed_setups", completed_setups)
                pending_setups.pop(gid, None)
                dm.save_json("pending_setups", pending_setups)

            await self._send_final_report(progress, interaction, ok_systems, failed_systems)
        except Exception as e:
            logger.error(f"auto-setup installation crashed: {traceback.format_exc()}")
            embed = discord.Embed(
                title="❌ Setup Crashed",
                description=f"Installation stopped unexpectedly:\n`{str(e)[:300]}`\n\n"
                            "Systems already installed are kept. Use `/autosetup` again to "
                            "resume the remaining ones.",
                color=discord.Color.red(),
            )
            await safe_edit(interaction, embed=embed, view=None)

    async def _send_final_report(self, progress, interaction, ok_systems, failed_systems):
        """Post-install verification: diagnostics + configpanel jump buttons."""
        from modules.system_panels import SYSTEM_GROUPS, open_system_panel

        diag_lines = []
        groups_installed = []
        for group_key, spec in SYSTEM_GROUPS.items():
            group_sys = {sub["key"] for sub in spec["subsystems"]}
            hits = [s for s in ok_systems if s in group_sys or
                    (s == "welcome_leave" and "verification" in group_sys)]
            if hits:
                groups_installed.append(group_key)
        try:
            from data_manager import dm as _dm
            import discord as _d
            for gk in groups_installed[:4]:
                spec = SYSTEM_GROUPS[gk]
                checks_ok = 0
                checks_total = 0
                for sub in spec["subsystems"]:
                    cfg = _dm.get_guild_data(interaction.guild.id, sub["config_key"], {})
                    enabled = bool(cfg.get("enabled")) if isinstance(cfg, dict) else False
                    checks_total += 1
                    checks_ok += (1 if (enabled or not sub.get("supports_toggle", True)) else 0)
                icon = "✅" if checks_ok == checks_total else "⚠️"
                diag_lines.append(f"{icon} **{spec['name']}** — {checks_ok}/{checks_total} active")
        except Exception as e:
            logger.warning(f"Post-install diagnostics failed: {e}")
            diag_lines.append(f"⚠️ Diagnostics unavailable: {str(e)[:80]}")

        desc = f"Successfully installed **{len(ok_systems)}** system(s).\n"
        if failed_systems:
            desc += f"⚠️ Issues: {', '.join(failed_systems[:8])}\n"
        desc += "\n**Live verification:**\n" + "\n".join(diag_lines)
        embed = discord.Embed(
            title="✅ Setup Complete!" if not failed_systems else "⚠️ Setup Finished (partial)",
            description=desc,
            color=discord.Color.green() if not failed_systems else discord.Color.gold(),
        )
        view = SetupDoneView(groups_installed)
        await progress.finish(embed, view)

    async def install_systems(self, guild, systems, user, channel, progress=None) -> dict:
        """Install all selected systems. Returns {system_name: success_bool}."""
        results = {}
        for system in systems:
            if progress is not None:
                await progress.set_status(system, "running")

            try:
                if system == "verification":
                    success = await self.setup_verification_system(guild, user)
                elif system == "economy":
                    success = await self.setup_economy_system(guild, user)
                elif system == "leveling":
                    success = await self.setup_leveling_system(guild, user)
                elif system == "tickets":
                    success = await self.setup_tickets_system(guild, user)
                elif system == "welcome_leave":
                    success = await self.setup_welcome_system(guild, user)
                elif system == "applications":
                    success = await self.setup_applications(guild, user)
                elif system == "appeals":
                    success = await self.setup_appeals(guild, user)
                else:
                    success = await self.setup_generic_system(guild, system, user)

                results[system] = bool(success)
                if progress is not None:
                    await progress.set_status(system, "ok" if success else "warn")
                # persist resume state after each step
                try:
                    pends = dm.load_json("pending_setups", default={})
                    entry = pends.get(str(guild.id))
                    if entry is not None and success:
                        entry.setdefault("completed", []).append(system)
                        dm.save_json("pending_setups", pends)
                except Exception:
                    pass

                await asyncio.sleep(0.5)  # rate limiting

            except Exception as e:
                logger.error(f"Failed to install {system}: {e}")
                results[system] = False
                if progress is not None:
                    await progress.set_status(system, "fail")

        return results

    async def setup_verification_system(self, guild, user) -> bool:
        """Set up verification system."""
        try:
            # Create roles
            verified_role = await guild.create_role(name="Verified", color=discord.Color.green())
            unverified_role = await guild.create_role(name="Unverified", color=discord.Color.red())
            self._record(guild.id, "verification", "role", verified_role.id)
            self._record(guild.id, "verification", "role", unverified_role.id)

            # Create channel
            verify_channel = await guild.create_text_channel("verify")
            self._record(guild.id, "verification", "channel", verify_channel.id)

            # Set permissions
            await verify_channel.set_permissions(guild.default_role, read_messages=False)
            await verify_channel.set_permissions(unverified_role, read_messages=True, send_messages=True)

            # Configure system
            config = {
                "enabled": True,
                "verified_role": str(verified_role.id),
                "unverified_role": str(unverified_role.id),
                "verify_channel": str(verify_channel.id),
                "min_account_age_days": 1,
                "kick_new_accounts": False
            }
            dm.update_guild_data(guild.id, "verification_config", config)

            # Post the persistent Verify panel so members can actually click it
            try:
                from modules.member_management import VerificationView
                await self._post_panel(
                    guild, "verification", verify_channel,
                    "🔐 Verification Required",
                    "Click the **Verify Me** button below to verify yourself "
                    "and gain full access to the server.",
                    discord.Color.blue(),
                    VerificationView(self.bot.verification, guild.id))
            except Exception as e:
                logger.warning(f"Verify panel post failed: {e}")

            return True
        except Exception as e:
            logger.error(f"Verification setup failed: {e}")
            return False

    async def setup_economy_system(self, guild, user) -> bool:
        """Set up economy system."""
        try:
            # Create channels
            shop_channel = await guild.create_text_channel("shop")
            self._record(guild.id, "economy", "channel", shop_channel.id)

            # Configure system
            config = {
                "enabled": True,
                "earn_rates": {
                    "coins_per_message": 5,
                    "gem_chance": 0.01
                },
                "daily_amount": 100,
                "daily_cooldown": 86400,
                "currency_name": "Coins",
                "currency_emoji": "🪙",
                "gem_name": "Gems",
                "gem_emoji": "💎",
                "starting_balance": 50
            }
            dm.update_guild_data(guild.id, "economy_config", config)

            return True
        except Exception as e:
            logger.error(f"Economy setup failed: {e}")
            return False

    async def setup_leveling_system(self, guild, user) -> bool:
        """Set up leveling system."""
        try:
            # Create leaderboard channel
            lb_channel = await guild.create_text_channel("leaderboard")
            self._record(guild.id, "leveling", "channel", lb_channel.id)

            # Configure system
            config = {
                "enabled": True,
                "xp_per_message": 10,
                "message_cooldown": 60,
                "announce_level_ups": True,
                "announce_channel": str(lb_channel.id),
                "role_rewards": {}
            }
            dm.update_guild_data(guild.id, "leveling_config", config)

            return True
        except Exception as e:
            logger.error(f"Leveling setup failed: {e}")
            return False

    async def setup_tickets_system(self, guild, user) -> bool:
        """Set up ticket system."""
        try:
            # Create category and channels
            ticket_category = await guild.create_category("Support Tickets")
            ticket_queue = await guild.create_text_channel("ticket-queue", category=ticket_category)
            self._record(guild.id, "tickets", "channel", ticket_category.id)
            self._record(guild.id, "tickets", "channel", ticket_queue.id)

            # Create staff role
            staff_role = await guild.create_role(name="Support Staff", color=discord.Color.blue())
            self._record(guild.id, "tickets", "role", staff_role.id)

            # Configure system
            config = {
                "enabled": True,
                "ticket_category": str(ticket_category.id),
                "ticket_queue_channel": str(ticket_queue.id),
                "staff_roles": [str(staff_role.id)],
                "log_channel": str(ticket_queue.id)
            }
            dm.update_guild_data(guild.id, "tickets_config", config)

            # Post the persistent Create-Ticket panel into the queue channel
            try:
                from modules.tickets import TicketPanelView
                await self._post_panel(
                    guild, "tickets", ticket_queue,
                    "🎫 Support Tickets",
                    "Need help? Click **Create Ticket** below and a staff member "
                    "will assist you as soon as possible.",
                    discord.Color.blue(),
                    TicketPanelView(self.bot.tickets))
            except Exception as e:
                logger.warning(f"Ticket panel post failed: {e}")

            return True
        except Exception as e:
            logger.error(f"Tickets setup failed: {e}")
            return False

    async def setup_welcome_system(self, guild, user) -> bool:
        """Set up welcome system."""
        try:
            # Create welcome channel
            welcome_channel = await guild.create_text_channel("welcome")
            self._record(guild.id, "welcome_leave", "channel", welcome_channel.id)

            # Configure system
            config = {
                "enabled": True,
                "welcome_channel": str(welcome_channel.id),
                "welcome_message": "Welcome {user} to {server}!",
                "leave_message": "{user} has left the server.",
                "welcome_dm": "Welcome to {server}! Please check the rules and enjoy your stay.",
                "welcome_dm_buttons": True
            }
            dm.update_guild_data(guild.id, "welcome_leave_config", config)

            return True
        except Exception as e:
            logger.error(f"Welcome setup failed: {e}")
            return False

    # Systems whose runtime module reads a different key than f"{system}_config"
    CONFIG_KEY_OVERRIDES = {
        "automod": "automod_config",
        "auto_mod": "automod_config",
        "warnings": "warning_config",
        "reaction_roles": "reaction_roles",
        "trigger_roles": "trigger_roles",
        "mod_logging": "mod_log_config",
        "applications": "application_config",
        "events": "event_settings",
        "tournaments": "tournament_settings",
        "ai_chat": "ai_chat_settings",
        "content_generator": "content_settings",
        "auto_publisher": "auto_publisher_settings",
    }

    # Table-driven installers: channels/roles created + config written.
    # Any system not listed here falls back to plain {"enabled": True}.
    INSTALL_TABLE = {
        "guardian": {"config_key": "guardian_config"},
        "moderation": {"channels": ["mod-log"], "config_key": "moderation_config",
                       "extra": {"log_channel": "{channel:mod-log}"}},
        "automod": {"channels": ["mod-log"], "config_key": "automod_config",
                    "extra": {"log_channel_id": "{channel:mod-log}",
                              "rules": {}}},
        "starboard": {"channels": ["starboard"], "config_key": "starboard_config",
                      "extra": {"starboard_channel": "{channel:starboard}",
                                "star_threshold": 3}},
        "suggestions": {"channels": ["suggestions"], "config_key": "suggestions_config",
                        "extra": {"suggestions_channel": "{channel:suggestions}",
                                  "staff_roles": []},
                        "panel": True},
        "giveaways": {"channels": ["giveaways"], "config_key": "giveaways_config"},
        "announcements": {"channels": ["announcements"], "config_key": "announcements_config",
                          "extra": {"announcement_channel": "{channel:announcements}",
                                    "require_approval": False}},
        "reminders": {"config_key": "reminders_config"},
        "auto_responder": {"config_key": "auto_responder_config"},
        "reaction_menus": {"config_key": "reaction_menus_config"},
        "role_buttons": {"config_key": "role_buttons_config"},
        "trigger_roles": {"config_key": "trigger_roles"},
        "anti_raid": {"channels": ["anti-raid-alerts"], "config_key": "anti_raid_config",
                      "extra": {"alert_channel_id": "{channel:anti-raid-alerts}",
                                "mass_join_threshold": 5, "mass_join_window": 60,
                                "action": "kick"}},
        "events": {"config_key": "event_settings", "extra": {"enabled": True}},
        "tournaments": {"config_key": "tournament_settings"},
        "gamification": {"config_key": "gamification_config"},
        "modmail": {"config_key": "modmail_config"},
        "ai_chat": {"config_key": "ai_chat_settings"},
        "logging": {"channels": ["logs"], "config_key": "logging_config",
                    "extra": {"log_channel": "{channel:logs}", "enabled": True}},
        "mod_logging": {"channels": ["mod-log"], "config_key": "mod_log_config",
                        "extra": {"channel_id": "{channel:mod-log}", "enabled": True}},
        "staff_shifts": {"channels": ["staff-shifts"], "config_key": "staff_shifts_config",
                         "roles": ["Staff"],
                         "extra": {"shift_channel_id": "{channel:staff-shifts}",
                                   "notifications_enabled": True}},
        "staff_reviews": {"channels": ["staff-reviews"], "config_key": "staff_reviews_config",
                          "extra": {"review_channel_id": "{channel:staff-reviews}",
                                    "cycle": "monthly", "notifications_enabled": True}},
        "staff_promo": {"channels": ["staff-promotions"], "config_key": "staff_promo_config",
                        "extra": {"announcement_channel": "{channel:staff-promotions}",
                                  "enabled": True}},
        "welcome_dm": {"config_key": "welcome_leave_config"},
    }

    async def _get_or_create_channel(self, guild, name):
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            return existing
        return await guild.create_text_channel(name)

    async def _post_panel(self, guild, system, channel, title, description, color, view):
        """Post a user-facing panel (button) into a channel. Never fails the install."""
        try:
            embed = discord.Embed(title=title, description=description, color=color)
            await channel.send(embed=embed, view=view)
            return True
        except Exception as e:
            logger.warning(f"Panel post failed for {system} in #{channel.name}: {e}")
            return False

    async def _create_setup_channel(self, guild, name) -> Optional[discord.TextChannel]:
        """Create a text channel by name, reusing one if it already exists."""
        try:
            existing = discord.utils.get(guild.text_channels, name=name)
            if existing:
                return existing
            return await guild.create_text_channel(name)
        except Exception as e:
            logger.error(f"Failed to create channel '{name}': {e}")
            return None

    async def setup_verification(self, interaction, params=None) -> bool:
        return await self.setup_verification_system(interaction.guild, interaction.user)

    async def setup_economy(self, interaction, params=None) -> bool:
        return await self.setup_economy_system(interaction.guild, interaction.user)

    async def setup_leveling(self, interaction, params=None) -> bool:
        return await self.setup_leveling_system(interaction.guild, interaction.user)

    async def setup_tickets(self, interaction, params=None) -> bool:
        return await self.setup_tickets_system(interaction.guild, interaction.user)

    async def setup_welcome(self, interaction, params=None) -> bool:
        return await self.setup_welcome_system(interaction.guild, interaction.user)

    async def setup_applications(self, source, params=None) -> bool:
        """Set up the applications system with a staff-apply channel.

        Accepts an Interaction (actions.py path) or a Guild (autosetup path).
        """
        try:
            guild = getattr(source, "guild", source)
            # FakeGuild and real Guild have no .guild attr — fall back to source
            if not hasattr(guild, "create_text_channel"):
                guild = source
            channel = await self._create_setup_channel(guild, "applications")
            if not channel:
                return False
            self._record(guild.id, "applications", "channel", channel.id)
            config = {
                "enabled": True,
                "channel_id": channel.id,
                "staff_roles": [],
                "questions": [
                    "Why do you want to join the staff team?",
                    "What experience do you have?",
                    "How active are you on this server?",
                    "What would you improve?",
                    "Anything else we should know?",
                ],
            }
            dm.update_guild_data(guild.id, "application_config", config)
            dm.update_guild_data(guild.id, "applications_channel", channel.id)
            embed = discord.Embed(
                title="📋 Staff Applications",
                description="Interested in joining the staff team? Click the button below to apply!",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed, view=ApplyStaffButton(guild_id=guild.id))
            return True
        except Exception as e:
            logger.error(f"Applications setup failed: {e}")
            return False

    async def setup_appeals(self, source, params=None) -> bool:
        """Set up the appeals system with an appeals channel.

        Accepts an Interaction (actions.py path) or a Guild (autosetup path).
        """
        try:
            guild = getattr(source, "guild", source)
            if not hasattr(guild, "create_text_channel"):
                guild = source
            channel = await self._create_setup_channel(guild, "appeals")
            if not channel:
                return False
            self._record(guild.id, "appeals", "channel", channel.id)
            config = {
                "appeals_channel_id": channel.id,
                "log_channel_id": channel.id,
                "cooldown_days": 30,
                "reviewer_role_id": None,
                "questions": [
                    "Why were you banned?",
                    "Why should you be unbanned?",
                    "What will you do differently?",
                    "Any evidence to provide?",
                ],
            }
            dm.update_guild_data(guild.id, "appeals_config", config)
            embed = discord.Embed(
                title="⚖️ Moderation Appeals",
                description="If you have been banned or punished and wish to appeal, click the button below.",
                color=discord.Color.blue(),
            )
            try:
                from modules.security import AppealPersistentView
                view = AppealPersistentView()
            except Exception:
                view = ApplyStaffButton(guild_id=guild.id)
            await channel.send(embed=embed, view=view)
            return True
        except Exception as e:
            logger.error(f"Appeals setup failed: {e}")
            return False

    # Panels posted into the first created channel after install (user-facing buttons)
    PANEL_FACTORIES = {
        "suggestions": {
            "title": "💡 Suggestions",
            "description": "Have an idea to improve the server? "
                           "Click **Make a Suggestion** below — staff and members can vote on it.",
        },
    }

    async def _post_system_panel(self, guild, system, channels):
        spec = self.PANEL_FACTORIES.get(system)
        if not spec or not channels:
            return
        from importlib import import_module
        try:
            if system == "suggestions":
                view = import_module("modules.suggestions").SuggestionPanelView(self.bot.suggestions)
            else:
                return
            first_channel = guild.get_channel(int(list(channels.values())[0]))
            if first_channel is None:
                return
            await self._post_panel(
                guild, system, first_channel,
                spec["title"], spec["description"], discord.Color.blue(), view)
        except Exception as e:
            logger.warning(f"{system} panel post failed: {e}")

    async def _install_from_table(self, guild, system, spec) -> bool:
        """Generic real installer driven by INSTALL_TABLE entries."""
        channels = {}
        for name in spec.get("channels", []):
            ch = await self._get_or_create_channel(guild, name)
            if ch:
                channels[name] = str(ch.id)
                self._record(guild.id, system, "channel", ch.id)
        roles = {}
        for role_name in spec.get("roles", []):
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                try:
                    role = await guild.create_role(name=role_name, color=discord.Color.blue())
                    self._record(guild.id, system, "role", role.id)
                except Exception as e:
                    logger.warning(f"Could not create role '{role_name}': {e}")
            if role:
                roles[role_name] = str(role.id)

        config = {"enabled": True}
        for key, value in spec.get("extra", {}).items():
            if isinstance(value, str) and value.startswith("{channel:") and value.endswith("}"):
                cname = value[len("{channel:"):-1]
                value = channels.get(cname, "")
            elif isinstance(value, str) and value.startswith("{role:") and value.endswith("}"):
                rname = value[len("{role:"):-1]
                value = [roles[rname]] if rname in roles else []
            config[key] = value
        dm.update_guild_data(guild.id, spec["config_key"], config)

        if spec.get("panel"):
            await self._post_system_panel(guild, system, channels)
        return True

    async def setup_generic_system(self, guild, system, user) -> bool:
        """Real setup when a table entry exists; minimal config otherwise."""
        spec = self.INSTALL_TABLE.get(system)
        if spec:
            return await self._install_from_table(guild, system, spec)
        try:
            config = {"enabled": True}
            key = self.CONFIG_KEY_OVERRIDES.get(system, f"{system}_config")
            dm.update_guild_data(guild.id, key, config)
            return True
        except Exception as e:
            logger.error(f"Generic setup failed for {system}: {e}")
            return False

    async def initialize_guild(self, guild):
        """Initialize basic data for a new guild."""
        # Preserve custom prefix (don't clobber on re-join) – fixes restart wipe
        if not dm.get_guild_data(guild.id, "prefix"):
            dm.update_guild_data(guild.id, "prefix", "!")

        # Initialize basic configs
        systems = [
            "verification", "anti_raid", "guardian", "auto_mod", "warnings",
            "economy", "leveling", "giveaways", "gamification", "starboard",
            "mod_logging", "logging", "modmail", "suggestions",
            "staff_promo", "staff_shifts", "staff_reviews", "applications", "appeals",
            "welcome_leave", "tickets", "reminders", "announcements", "auto_responder",
            "reaction_roles", "reaction_menus", "role_buttons", "ai_chat"
        ]

        for system in systems:
            config_key = self.CONFIG_KEY_OVERRIDES.get(system, f"{system}_config")
            if not dm.get_guild_data(guild.id, config_key):
                dm.update_guild_data(guild.id, config_key, {"enabled": False})


class AutoSetup(AutoSetupSystem):
    """Interaction-based facade used by the AI action framework (actions.py).

    The real system uses (guild, user) signatures; the action framework calls
    these with (interaction, params), so this class adapts the call and adds
    the missing per-system setup methods.
    """

    async def setup_moderation(self, interaction, params=None) -> bool:
        """Set up moderation with a mod-log channel and moderator role."""
        try:
            guild = interaction.guild
            channel = await self._create_setup_channel(guild, "mod-log")
            if not channel:
                return False
            self._record(guild.id, "moderation", "channel", channel.id)
            self._record(guild.id, "moderation", "channel", channel.id)
            role = None
            try:
                role = await guild.create_role(name="Moderator", color=discord.Color.orange())
                self._record(guild.id, "moderation", "role", role.id)
            except Exception as e:
                logger.error(f"Failed to create Moderator role: {e}")
            config = {
                "enabled": True,
                "log_channel": str(channel.id),
                "mod_roles": [str(role.id)] if role else [],
            }
            dm.update_guild_data(guild.id, "moderation_config", config)
            dm.update_guild_data(guild.id, "mod_logging_config", {"enabled": True, "log_channel": str(channel.id)})
            return True
        except Exception as e:
            logger.error(f"Moderation setup failed: {e}")
            return False

    async def setup_logging(self, interaction, params=None) -> bool:
        """Set up the logging system with a logs channel."""
        try:
            guild = interaction.guild
            channel = await self._create_setup_channel(guild, "logs")
            if not channel:
                return False
            self._record(guild.id, "logging", "channel", channel.id)
            self._record(guild.id, "logging", "channel", channel.id)
            dm.update_guild_data(guild.id, "logging_config", {"enabled": True, "log_channel": str(channel.id)})
            return True
        except Exception as e:
            logger.error(f"Logging setup failed: {e}")
            return False

# UI Classes
class SetupStartView(discord.ui.View):
    def __init__(self, auto_setup, preflight_ok: bool = True):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup
        self.preflight_ok = preflight_ok
        # rows 0-1: curated presets, row 2: custom flows
        for i, key in enumerate(("gaming", "support")):
            self.add_item(PresetButton(auto_setup, key, SETUP_PRESETS[key], row=0))
        for i, key in enumerate(("community", "everything")):
            self.add_item(PresetButton(auto_setup, key, SETUP_PRESETS[key], row=1))

    @discord.ui.button(label="Custom Selection…", style=discord.ButtonStyle.primary, emoji="🎯", row=2)
    async def start_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.auto_setup.begin_system_selection(interaction)

    @discord.ui.button(label="Quick Setup (Recommended)", style=discord.ButtonStyle.success, emoji="⚡", row=2)
    async def quick_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.preflight_ok:
            report = self.auto_setup.run_preflight(interaction.guild)
            if not report.ok:
                return await interaction.response.send_message(
                    "❌ Pre-flight checks failed:\n" + "\n".join(report.lines),
                    ephemeral=True)
        recommended = ["verification", "welcome_leave", "tickets", "economy", "leveling",
                       "automod", "warnings", "moderation", "anti_raid", "auto_responder",
                       "announcements", "reminders"]
        await self.auto_setup.start_installation(interaction, recommended)


class PresetButton(discord.ui.Button):
    """One-click curated system bundle with confirmation."""

    PRESET_COLORS = {
        "gaming": discord.ButtonStyle.success,
        "support": discord.ButtonStyle.primary,
        "community": discord.ButtonStyle.secondary,
        "everything": discord.ButtonStyle.danger,
    }

    def __init__(self, auto_setup, key: str, spec: dict, row: int = 0):
        super().__init__(
            label=f"{spec['label']} ({len(spec['systems'])})",
            style=self.PRESET_COLORS.get(key, discord.ButtonStyle.secondary),
            row=row,
        )
        self.auto_setup = auto_setup
        self.key = key
        self.spec = spec

    async def callback(self, interaction: discord.Interaction):
        if not self.spec["systems"]:
            return await interaction.response.send_message("❌ Empty preset.", ephemeral=True)
        from ui.components import ConfirmView
        view = ConfirmView(
            interaction.user.id,
            f"Install **{self.spec['label']}** — {len(self.spec['systems'])} systems?\n"
            f"Channels/roles will be created (fully undoable afterwards).",
            timeout=30)
        await interaction.response.send_message(
            f"Confirm installation of **{self.spec['label']}**?", view=view, ephemeral=True)
        await view.wait()
        if not view.confirmed:
            try:
                await interaction.followup.send("❎ Installation cancelled.", ephemeral=True)
            except Exception:
                pass
            return
        await self.auto_setup.start_installation(interaction, list(self.spec["systems"]))


class ResumeSetupView(discord.ui.View):
    """Shown when an interrupted setup is detected."""

    def __init__(self, auto_setup, guild_id: int, remaining: List[str]):
        super().__init__(timeout=180)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.remaining = remaining

    @discord.ui.button(label="Resume Setup", style=discord.ButtonStyle.success, emoji="▶️")
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.remaining:
            pending_setups = dm.load_json("pending_setups", default={})
            pending_setups.pop(str(self.guild_id), None)
            dm.save_json("pending_setups", pending_setups)
            return await interaction.response.edit_message(
                content="✅ Nothing left to install — previous setup already finished.",
                embed=None, view=None)
        await self.auto_setup.start_installation(interaction, list(self.remaining))

    @discord.ui.button(label="Start Fresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def fresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups.pop(str(self.guild_id), None)
        dm.save_json("pending_setups", pending_setups)
        await self.auto_setup.begin_system_selection(interaction)

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups.pop(str(self.guild_id), None)
        dm.save_json("pending_setups", pending_setups)
        await interaction.response.edit_message(
            content="🗑️ Interrupted setup discarded.", embed=None, view=None)


class AlreadySetupView(discord.ui.View):
    """Shown on /autosetup when the server was already set up."""

    def __init__(self, auto_setup):
        super().__init__(timeout=120)
        self.auto_setup = auto_setup

    @discord.ui.button(label="Undo Entire Setup", style=discord.ButtonStyle.danger, emoji="↩️")
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        from ui.components import ConfirmView
        confirm = ConfirmView(
            interaction.user.id,
            "Delete every channel and role Miro created during auto-setup?\n"
            "**This cannot be undone.** System configurations are kept.",
            danger=True, timeout=30)
        await interaction.response.send_message("Confirm undo:", view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.confirmed:
            return
        try:
            await interaction.followup.send("↩️ Undoing setup…", ephemeral=True)
            ch, r = await self.auto_setup.undo_setup(interaction.guild)
            await interaction.followup.send(
                f"✅ Removed {ch} channel(s) and {r} role(s) created by auto-setup.",
                ephemeral=True)
        except Exception as e:
            logger.error(f"Undo failed: {e}")
            try:
                await interaction.followup.send(f"❌ Undo failed: {str(e)[:150]}", ephemeral=True)
            except Exception:
                pass


class SetupDoneView(discord.ui.View):
    """Completion screen: jump into config panels or undo."""

    def __init__(self, group_keys: List[str]):
        super().__init__(timeout=600)
        options = []
        for gk in group_keys[:25]:
            try:
                from modules.system_panels import SYSTEM_GROUPS
                spec = SYSTEM_GROUPS.get(gk, {})
                options.append(discord.SelectOption(
                    label=f"{spec.get('name', gk)}",
                    value=gk,
                    emoji=spec.get("emoji"),
                    description="Open its configuration panel",
                ))
            except Exception:
                continue
        if options:
            self.add_item(ConfigureSelect(options))


class ConfigureSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="⚙️ Configure an installed system group…", options=options)

    async def callback(self, interaction: discord.Interaction):
        from modules.system_panels import open_system_panel
        await open_system_panel(interaction, self.values[0])


class CategorySelectView(discord.ui.View):
    def __init__(self, auto_setup, guild_id: int):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup
        self.guild_id = guild_id

        # Add category buttons (5 per row max — 10 groups use rows 0-1)
        categories = SystemCategory.get_all_categories()
        for i, category in enumerate(categories):
            button = CategoryButton(category, auto_setup, row=i // 5)
            self.add_item(button)

class CategoryButton(discord.ui.Button):
    def __init__(self, category, auto_setup, row: int = 0):
        super().__init__(
            label=category["name"],
            emoji=category["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row
        )
        self.category = category
        self.auto_setup = auto_setup

    async def callback(self, interaction: discord.Interaction):
        await self.auto_setup.show_category_systems(interaction, self.category)

class SystemSelectView(discord.ui.View):
    def __init__(self, auto_setup, guild_id: int, category):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.category = category
        self.selected_systems = []

        # Add system buttons
        for i, system in enumerate(category["systems"]):
            button = SystemButton(system, self)
            self.add_item(button)

        # Add control buttons
        self.add_item(InstallGroupButton(self, row=4))
        self.add_item(InstallSelectedButton(self, row=4))
        self.add_item(BackButton(auto_setup, guild_id, row=4))

    def toggle_all(self):
        """Mark every system in this group as selected."""
        self.selected_systems = list(self.category["systems"])

class SystemButton(discord.ui.Button):
    def __init__(self, system, parent_view):
        super().__init__(
            label=system.replace("_", " ").title(),
            style=discord.ButtonStyle.secondary
        )
        self.system = system
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if self.system in self.parent_view.selected_systems:
            self.parent_view.selected_systems.remove(self.system)
            self.style = discord.ButtonStyle.secondary
        else:
            self.parent_view.selected_systems.append(self.system)
            self.style = discord.ButtonStyle.success

        await interaction.response.edit_message(view=self.parent_view)

class InstallGroupButton(discord.ui.Button):
    """One-click install of every system in the merged group."""
    def __init__(self, parent_view, row=4):
        super().__init__(
            label="Install Entire Group",
            emoji="📦",
            style=discord.ButtonStyle.primary,
            row=row
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.toggle_all()
        await self.parent_view.auto_setup.start_installation(
            interaction, self.parent_view.selected_systems)

class InstallSelectedButton(discord.ui.Button):
    def __init__(self, parent_view, row=0):
        super().__init__(
            label="Install Selected",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=row
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_systems:
            return await interaction.response.send_message("❌ Please select at least one system.", ephemeral=True)

        await self.parent_view.auto_setup.start_installation(interaction, self.parent_view.selected_systems)

class BackButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, row=0):
        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️",
            row=row
        )
        self.auto_setup = auto_setup
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎯 Select Systems to Install",
            description="Choose which systems you want to set up.",
            color=discord.Color.blue()
        )
        view = CategorySelectView(self.auto_setup, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


# Persistent View Classes for Auto-Setup Buttons
class VerifyButton(discord.ui.View):
    """Persistent view for verification button during auto-setup."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify Me", style=discord.ButtonStyle.success, custom_id="verify_button_persistent")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")

        if not role:
            return await interaction.response.send_message("❌ Verification role not found. Please contact staff.", ephemeral=True)

        if role in interaction.user.roles:
            return await interaction.response.send_message("✅ You are already verified!", ephemeral=True)

        try:
            # Handle Unverified role removal if using the modules/verification system
            unverified = discord.utils.get(guild.roles, name="Unverified")
            if unverified and unverified in interaction.user.roles:
                await interaction.user.remove_roles(unverified)

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ You're verified! Enjoy the server!", ephemeral=True)
            # Log action
            logger.info(f"User {interaction.user.id} verified in guild {guild.id}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to assign the Verified role. Check my role position!", ephemeral=True)


class AcceptRulesButton(discord.ui.View):
    """Persistent view for accept rules button during auto-setup."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="I Accept the Rules", style=discord.ButtonStyle.primary, custom_id="accept_rules_persistent")
    async def accept_rules_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")

        if role and role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Thanks for accepting! You now have full access.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("✅ Rules accepted (but I couldn't add your role).", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to create ticket thread.", ephemeral=True)


class CreateTicketButton(discord.ui.View):
    """Persistent view for create ticket button during auto-setup."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.secondary, custom_id="create_ticket_persistent")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would integrate with the ticket system
        await interaction.response.send_message("🎫 Ticket creation is handled through the ticket system.", ephemeral=True)


class SuggestionButton(discord.ui.View):
    """Persistent view for suggestion button during auto-setup."""
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Make Suggestion", style=discord.ButtonStyle.secondary, custom_id="suggestion_button_persistent")
    async def suggestion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would integrate with the suggestions system
        await interaction.response.send_message("💡 Suggestion creation is handled through the suggestions system.", ephemeral=True)


class ApplyStaffButton(discord.ui.View):
    """Persistent view for apply staff button during auto-setup."""
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Apply for Staff", style=discord.ButtonStyle.primary, custom_id="apply_staff_persistent")
    async def apply_staff_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would integrate with the applications system
        await interaction.response.send_message("👥 Staff applications are handled through the applications system.", ephemeral=True)