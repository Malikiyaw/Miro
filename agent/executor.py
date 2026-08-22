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

    @staticmethod
    def build_message_interaction(message):
        """Preserve the real human speaker for ActionHandler permission checks."""
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
                    class _Resp:
                        done = False
                        def is_done(self): return False
                        async def send_message(self, *a, **k): pass
                        async def defer(self, *a, **k): pass
                    self.response = _Resp()
                    class _Follow:
                        async def send(self, *a, **k): pass
                    self.followup = _Follow()
            return _BotIdentity()

    async def execute(self, interaction, name: str, params: Dict[str, Any],
                      request_id: str = "", retries: int = 1,
                      timeout_override: Optional[float] = None) -> Receipt:
        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            return Receipt(action=name, success=False, verified=False,
                           error_type=ErrorType.PROVIDER_ERROR,
                           message="ActionHandler unavailable", request_id=request_id)

        async def run():
            ctx = interaction
            try:
                if ctx is None:
                    ctx = self.build_bot_identity_interaction(
                        self.bot, params.get("guild_id") or 0)
                # Single authoritative mutation gateway.
                success, info = await handler.dispatch(ctx, name, params)
                return bool(success), info or {}
            except Exception as e:
                # Never swallow an execution exception. Convert it to a receipt
                # so the agent can observe the exact failure and replan.
                logger.exception(f"Agent tool {name} raised")
                return False, {"error": str(e)[:500], "exception": type(e).__name__}

        limit = timeout_override or tool_timeout(name)
        try:
            success, info = await asyncio.wait_for(
                with_retry(run, retries, 1.5), timeout=limit
            )
        except asyncio.TimeoutError:
            success, info = False, {"error": f"tool timed out after {limit:.0f}s"}
        except Exception as e:
            logger.exception(f"Agent tool {name} execution wrapper failed")
            success, info = False, {"error": str(e)[:500], "exception": type(e).__name__}

        info = info if isinstance(info, dict) else {"result": str(info)}
        error_text = str(info.get("error", ""))

        target_id = ""
        for key in ("channel_id", "role_id", "user_id", "member_id"):
            if info.get(key):
                target_id = str(info[key])
                break
        if not target_id:
            for key in ("channel_id", "role_id", "user_id", "member_id"):
                if params.get(key):
                    target_id = str(params[key])
                    break

        # Keep structured discovery results in the observation stream so a
        # subsequent planning turn can use exact IDs rather than hallucinating.
        message = str(info.get("message") or "").strip()
        if not message:
            if isinstance(info.get("duplicates"), list):
                ids = [str(x.get("id")) for x in info["duplicates"] if isinstance(x, dict) and x.get("id")]
                message = f"duplicates={ids}"
            elif isinstance(info.get("matches"), list):
                ids = [str(x.get("id")) for x in info["matches"] if isinstance(x, dict) and x.get("id")]
                message = f"matches={ids}"
            elif error_text:
                message = error_text
            else:
                message = str(info.get("result") or "ActionHandler returned no message")

        et = ErrorType.NONE if success else (
            classify_error(error_text) if error_text else ErrorType.UNKNOWN
        )
        return Receipt(
            action=name,
            target_id=target_id,
            target_type=self.get(name)["object_type"],
            success=bool(success),
            verified=False,
            error_type=et,
            message=message[:500],
            request_id=request_id,
        )

    def get(self, name: str) -> Dict[str, Any]:
        from .tool_registry import tool_registry
        return tool_registry.get(name)
