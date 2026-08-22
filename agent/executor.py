"""Tool executor: the ONLY mutation gateway is ActionHandler.dispatch()."""
import asyncio
from typing import Any, Dict, Optional

from logger import logger
from .recovery import with_retry
from .state import ErrorType, Receipt, classify_error

TOOL_TIMEOUTS = {"query": 15.0, "default": 30.0}


def tool_timeout(name: str) -> float:
    from core.action_meta import get_meta
    if get_meta(name).get("operation") == "query":
        return TOOL_TIMEOUTS["query"]
    return TOOL_TIMEOUTS["default"]


class Executor:
    def __init__(self, bot):
        self.bot = bot

    # -- interaction contexts ---------------------------------------------

    @staticmethod
    def build_message_interaction(message):
        """Real speaker context for message-driven agent runs: the admin gate
        in dispatch() then applies to the actual human, not the bot."""
        class _MessageInteraction:
            guild = message.guild
            user = message.author
            channel = message.channel
            response = None
            followup = None
            _miro_ai_source = True

            def __init__(self):
                me = self

                class _Resp:
                    done = False

                    async def send_message(self, *a, **k):
                        me._Resp.done = True

                    async def defer(self, *a, **k):
                        me._Resp.done = True

                    async def edit_message(self, *a, **k):
                        pass

                    def is_done(self):
                        return me._Resp.done

                self.response = _Resp()

                class _Follow:
                    async def send(self, *a, **k):
                        pass

                self.followup = _Follow()
        return _MessageInteraction()

    @staticmethod
    def build_bot_identity_interaction(bot, guild_id: int):
        try:
            from actions import ScheduledTaskInteraction
            return ScheduledTaskInteraction(bot, guild_id)
        except Exception:
            resolved_guild = bot.get_guild(guild_id) if hasattr(bot, "get_guild") else None
            bot_user = getattr(bot, "user", None)

            class _BotIdentity:
                guild = resolved_guild
                user = bot_user
                channel = getattr(resolved_guild, "system_channel", None) if resolved_guild else None
                response = None
                followup = None

                def __init__(self):
                    me = self

                    class _Resp:
                        done = False

                        def is_done(self):
                            return False

                        async def send_message(self, *a, **k): pass

                        async def defer(self, *a, **k): pass

                    self.response = _Resp()

                    class _Follow:
                        async def send(self, *a, **k): pass

                    self.followup = _Follow()
            return _BotIdentity()

    # -- execution ----------------------------------------------------------

    async def execute(self, interaction, name: str, params: Dict[str, Any],
                      request_id: str = "", retries: int = 1,
                      timeout_override: Optional[float] = None) -> Receipt:
        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            receipt = Receipt(action=name, success=False, verified=False,
                              error_type=ErrorType.PROVIDER_ERROR,
                              message="ActionHandler unavailable", request_id=request_id)
            return receipt

        async def run():
            ctx = interaction
            try:
                if ctx is None:
                    ctx = self.build_bot_identity_interaction(
                        self.bot, params.get("guild_id") or 0)
                success, info = await handler.dispatch(ctx, name, params)
                return bool(success), info or {}
            except Exception as e:
                logger.error(f"Agent tool {name} raised: {e}")
                return False, {"error": str(e)[:200]}

        limit = timeout_override or tool_timeout(name)
        try:
            success, info = await asyncio.wait_for(with_retry(run, retries, 1.5),
                                                   timeout=limit)
        except asyncio.TimeoutError:
            success, info = False, {"error": f"tool timed out after {limit:.0f}s"}

        error_text = str(info.get("error", "")) if isinstance(info, dict) else ""
        target_id = ""
        for key in ("channel_id", "role_id", "user_id"):
            if isinstance(info, dict) and info.get(key):
                target_id = str(info[key])
                break
        if not target_id and isinstance(params, dict):
            for key in ("channel_id", "role_id", "user_id"):
                if params.get(key):
                    target_id = str(params[key])
                    break

        et = ErrorType.NONE if success else (
            classify_error(error_text) if error_text else ErrorType.UNKNOWN)
        return Receipt(action=name, target_id=target_id,
                       target_type=self.get(name)["object_type"],
                       success=bool(success),
                       verified=False,  # verifier upgrades this
                       error_type=et,
                       message=str((info or {}).get("message", error_text))[:200]
                       if isinstance(info, dict) else str(info)[:200],
                       request_id=request_id)

    # registry passthrough
    def get(self, name: str) -> Dict[str, Any]:
        from .tool_registry import tool_registry
        return tool_registry.get(name)
