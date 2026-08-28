import time
import asyncio
import discord
from typing import Any, Callable, Optional

from logger import logger
from core.access_control import AccessControl, access_control, AccessLevel

PANEL_EXPIRED_TEXT = "⌛ This panel has expired. Run `/autosetup` or `/system` again."


class PanelExpired(Exception):
    pass


class SystemPanelView(discord.ui.View):
    """
    Base class implementing the Miro button contract for EVERY panel:

        press → validate interaction → permissions → state/duplicate locks
              → real backend call → persistence (handler's job, via dm)
              → audit event → panel refresh → success/error feedback

    Subclasses declare discord.ui buttons/selects and delegate their work to
    self.perform(...). No fake buttons: every perform() reaches real modules.
    """

    def __init__(self, bot, guild: discord.Guild, author_id: int,
                 required_level: AccessLevel = AccessLevel.ADMIN,
                 system_config: Optional[dict] = None, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild = guild
        self.author_id = author_id
        self.required_level = required_level
        self.system_config = system_config or {}
        self.message: Optional[discord.Message] = None
        self._timed_out = False
        self._inflight: set[str] = set()

    # ------------------------------------------------------------------ #
    # lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def on_timeout(self):
        self._timed_out = True
        if self.message is not None:
            try:
                await self.message.edit(content=PANEL_EXPIRED_TEXT, embed=None, view=None)
            except Exception:
                pass

    def _audit(self, action: str, user_id: int, success: bool, detail: str = "", metadata: dict = None):
        audit = getattr(self.bot, "audit_log", None)
        if audit is None:
            return
        try:
            meta = {"detail": detail[:200]} if detail else {}
            if metadata:
                # truncate values to keep jsonl small
                for k, v in metadata.items():
                    try:
                        meta[k] = str(v)[:300] if not isinstance(v, (dict, list)) else (str(v)[:300])
                    except Exception:
                        pass
            audit.record_action(action, actor_id=user_id, target=self.guild.name[:80]
                                if self.guild else None, guild_id=self.guild.id if self.guild else None,
                                source="panel", success=success, **meta)
        except Exception as e:
            logger.debug(f"panel audit failed: {e}")

    def _bus_publish(self, event: str, **payload):
        bus = getattr(self.bot, "event_bus", None)
        if bus is None:
            return
        try:
            asyncio.create_task(bus.publish(event, guild_id=self.guild.id if self.guild else None, **payload))
        except Exception:
            pass

    async def _deny(self, interaction: discord.Interaction, reason: str, action: str):
        if interaction.response.is_done():
            await interaction.followup.send(f"🚫 {reason}", ephemeral=True)
        else:
            await interaction.response.send_message(f"🚫 {reason}", ephemeral=True)

    async def perform(
        self,
        interaction: discord.Interaction,
        action: str,
        work: Optional[Callable[[], Any]],
        *,
        level: Optional[AccessLevel] = None,
        success: str = "✅ Done.",
        refresh: bool = True,
        ephemeral_success: bool = True,
    ):
        """
        Execute `work()` through the full contract. `work` must be an awaitable
        factory performing the REAL module/backend change (including its own
        dm.update_guild_data persistence). Returns work()'s result value.
        """
        user_id = interaction.user.id

        # 1. interaction/expiry validation
        if self._timed_out:
            return await self._deny(interaction, PANEL_EXPIRED_TEXT, action)

        # 2. double-click protection: one execution per action at a time
        if action in self._inflight:
            return await self._deny(interaction, "⏳ Already working on that — one moment.", action)
        self._inflight.add(action)

        # 3. central permission check
        allowed, why = access_control.check(
            interaction,
            level if level is not None else self.required_level,
            self.system_config or None,
        )
        if not allowed:
            self._inflight.discard(action)
            self._audit(action, user_id, success=False, detail=f"denied: {why}")
            return await self._deny(interaction, why, action)

        # 4-6. real backend call (work persists via dm) + audit with before/after diff
        error_text = ""
        result = None
        try:
            result = await work() if work is not None else None
            meta = {}
            if isinstance(result, dict):
                for k in ("report","checks","missing","before","after","created","reused","failed","diagnostics"):
                    if k in result:
                        meta[k] = result[k]
                # for string results that embed report, also capture
                if not meta and result:
                    meta["result"] = str(result)[:300]
            elif result is not None:
                meta["result"] = str(result)[:300]
            self._audit(action, user_id, success=True, detail=str(result)[:150] if result else "", metadata=meta)
            self._bus_publish("command.executed", source="panel")
        except Exception as e:
            error_text = f"{type(e).__name__}: {e}"
            logger.error(f"Panel action '{action}' failed: {e}")
            self._audit(action, user_id, success=False, detail=error_text, metadata={"error": error_text[:300]})
        finally:
            self._inflight.discard(action)

        # 7. reload + update panel state
        if error_text:
            msg = f"❌ {error_text[:400]}"
        elif refresh:
            try:
                self.refresh_state()
                await self.render(interaction)
                msg = success
            except Exception as e:
                logger.error(f"Panel refresh failed after '{action}': {e}")
                msg = success + "\n⚠️ Panel refresh failed — reopen the panel to see fresh values."
        else:
            msg = success

        # 8. feedback (deferred interactions use followups)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=ephemeral_success)
            elif ephemeral_success:
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.response.defer()
        except Exception:
            pass
        return result

    # -- subclass hooks -----------------------------------------------------

    def refresh_state(self):
        """Reload whatever state this panel displays (called before render)."""

    def build_embed(self) -> discord.Embed:
        raise NotImplementedError

    async def render(self, interaction: discord.Interaction):
        """Redraw the panel message in place."""
        embed = self.build_embed()
        try:
            if interaction.response.is_done():
                await interaction.message.edit(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException as e:
            logger.debug(f"panel render skipped: {e}")
