"""V8 tool executor: the ONLY mutation gateway is ActionHandler.dispatch()."""
import asyncio
import time
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

    def _permission_decision(self, interaction, name: str, params: Dict[str, Any]):
        """Run every agent tool through the central V2/V8 permission engine."""
        engine = getattr(self.bot, "permission_engine", None)
        if engine is None:
            return True, ""

        try:
            from core.permissions.context import RequestContext
            guild = getattr(interaction, "guild", None)
            user = getattr(interaction, "user", None)
            permissions = getattr(user, "guild_permissions", None)
            is_admin = bool(getattr(permissions, "administrator", False))
            is_owner = bool(guild is not None and getattr(guild, "owner_id", None) == getattr(user, "id", None))
            is_bot_identity = bool(getattr(interaction, "_miro_ai_source", False) is False and user is getattr(self.bot, "user", None))

            target_role_position = None
            target_role_id = params.get("role_id")
            if target_role_id and guild is not None:
                role = guild.get_role(int(target_role_id)) if str(target_role_id).isdigit() else None
                if role is not None:
                    target_role_position = role.position

            user_top = getattr(getattr(user, "top_role", None), "position", -1)
            bot_member = guild.me if guild is not None else None
            bot_top = getattr(getattr(bot_member, "top_role", None), "position", -1)

            ctx = RequestContext(
                guild_id=getattr(guild, "id", None),
                user_id=getattr(user, "id", None),
                action=name,
                source="ai" if getattr(interaction, "_miro_ai_source", False) else "command",
                is_admin=is_admin,
                is_owner=is_owner,
                is_bot_identity=is_bot_identity,
                user_top_role_position=user_top,
                target_role_position=target_role_position,
                bot_top_role_position=bot_top,
                metadata={"target_role_id": target_role_id},
            )
            decision = engine.evaluate(ctx)
            return bool(decision.allowed), str(getattr(decision, "reason", ""))
        except Exception as exc:
            logger.exception("Agent permission evaluation failed")
            return False, f"permission engine error: {exc}"

    async def execute(self, interaction, name: str, params: Dict[str, Any],
                      request_id: str = "", retries: int = 1,
                      timeout_override: Optional[float] = None,
                      job_id: str = "") -> Receipt:
        started = time.time()
        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            return Receipt(
                action=name, success=False, verified=False,
                error_type=ErrorType.PROVIDER_ERROR,
                message="ActionHandler unavailable", request_id=request_id,
                job_id=job_id, parameters=dict(params), started_at=started,
                finished_at=time.time(),
            )

        allowed, permission_reason = self._permission_decision(interaction, name, params)
        if not allowed:
            return Receipt(
                action=name, success=False, verified=False,
                error_type=ErrorType.MISSING_PERMISSION,
                message=permission_reason or "permission denied",
                request_id=request_id, job_id=job_id,
                parameters=dict(params), started_at=started,
                finished_at=time.time(),
            )

        async def run():
            ctx = interaction
            try:
                if ctx is None:
                    ctx = self.build_bot_identity_interaction(
                        self.bot, params.get("guild_id") or 0)
                # Single authoritative mutation gateway. No AI-facing path may
                # call Discord mutations directly.
                success, info = await handler.dispatch(ctx, name, params)
                return bool(success), info or {}
            except Exception as e:
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
            job_id=job_id,
            parameters=dict(params),
            started_at=started,
            finished_at=time.time(),
        )

    def get(self, name: str) -> Dict[str, Any]:
        from .tool_registry import tool_registry
        return tool_registry.get(name)
