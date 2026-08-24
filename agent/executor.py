"""V9 tool executor: ActionHandler is the ONLY mutation gateway.

This layer also performs deterministic dependency repair for tool calls that
can be safely resolved from live Discord state. The model is never trusted to
invent destructive IDs.
"""
import asyncio
import re
import time
from typing import Any, Dict, Optional

from logger import logger
from .recovery import with_retry
from .state import ErrorType, Receipt, classify_error

def tool_timeout(name: str) -> float:
    """Delegate to the central policy map (batch tools get long windows)."""
    from agent.policies import tool_timeout as policy_timeout
    return policy_timeout(name)


def _normalize_channel_name(value: str) -> str:
    value = str(value or "").lower().strip()
    value = re.sub(r"[-_\s]+", "-", value)
    value = re.sub(r"^[^a-z0-9]+", "", value)
    value = re.sub(r"-\d+$", "", value)
    return re.sub(r"\s+\d+$", "", value)


def _request_name_candidates(request: str):
    """Extract useful channel-name candidates without treating them as IDs."""
    text = str(request or "")
    candidates = []
    # quoted/backticked names are strongest
    for m in re.findall(r"[`\"']([^`\"']+)[`\"']", text):
        if m.strip(): candidates.append(m.strip())
    # Discord-style #channel names
    for m in re.findall(r"#([A-Za-z0-9_\-⚠️]+)", text):
        candidates.append(m)
    # A common natural-language form: "duplicate <name> channels"
    m = re.search(r"duplicate(?:\s+the)?\s+(.+?)\s+channels?\b", text, re.I)
    if m: candidates.append(m.group(1).strip(" `\"'"))
    return [_normalize_channel_name(x) for x in candidates if _normalize_channel_name(x)]


def _resolve_bulk_delete_ids(guild, params: Dict[str, Any], request: str):
    """Resolve exact duplicate IDs from live Discord state.

    Safety rule: this function NEVER deletes by name. It only converts a
    missing bulk-delete dependency into concrete IDs obtained from the live
    guild object. Ambiguous groups fail closed.
    """
    ids = params.get("channel_ids") or params.get("channels")
    if isinstance(ids, list) and ids:
        return [str(x) for x in ids if str(x).isdigit()]
    if guild is None:
        raise ValueError("cannot resolve duplicate channels without a live guild")

    from agent.tools import find_all_duplicate_groups
    scan = find_all_duplicate_groups(guild)
    groups = scan.get("groups") or []
    if not groups:
        raise ValueError("no duplicate channel groups were found in live Discord state")

    candidates = _request_name_candidates(request)
    selected = []
    if candidates:
        for group in groups:
            base = _normalize_channel_name(group.get("base_name", ""))
            if any(base == candidate or base in candidate or candidate in base for candidate in candidates):
                selected.append(group)
    if not selected:
        # If the live scan has exactly one duplicate group, it is safe to use
        # it because the request explicitly asked for duplicate channels.
        if len(groups) == 1:
            selected = groups
        else:
            raise ValueError("duplicate target is ambiguous; resolve the exact group with find_duplicate_channels first")
    if len(selected) != 1:
        raise ValueError("multiple duplicate channel groups match the request; refusing ambiguous deletion")

    group = selected[0]
    protected = str(params.get("protected_channel_id") or group.get("protected_channel_id") or group.get("original", {}).get("id") or "")
    duplicates = group.get("duplicates") or []
    resolved = [str(item.get("id")) for item in duplicates if isinstance(item, dict) and str(item.get("id", "")).isdigit() and str(item.get("id")) != protected]
    if not resolved:
        raise ValueError("duplicate group resolved, but it contains no deletable duplicate IDs")
    return resolved


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
                    async def send_message(self, *a, **k): me._Resp.done = True
                    async def defer(self, *a, **k): me._Resp.done = True
                    async def edit_message(self, *a, **k): pass
                    def is_done(self): return me._Resp.done
                self.response = _Resp()
                class _Follow:
                    async def send(self, *a, **k): pass
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
                guild = resolved_guild; user = bot_user
                channel = getattr(resolved_guild, "system_channel", None) if resolved_guild else None
                response = None; followup = None
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
        engine = getattr(self.bot, "permission_engine", None)
        if engine is None: return True, ""
        try:
            from core.permissions.context import RequestContext
            guild = getattr(interaction, "guild", None); user = getattr(interaction, "user", None)
            permissions = getattr(user, "guild_permissions", None)
            is_admin = bool(getattr(permissions, "administrator", False))
            is_owner = bool(guild is not None and getattr(guild, "owner_id", None) == getattr(user, "id", None))
            is_bot_identity = bool(getattr(interaction, "_miro_ai_source", False) is False and user is getattr(self.bot, "user", None))
            target_role_position = None; target_role_id = params.get("role_id")
            if target_role_id and guild is not None:
                role = guild.get_role(int(target_role_id)) if str(target_role_id).isdigit() else None
                if role is not None: target_role_position = role.position
            user_top = getattr(getattr(user, "top_role", None), "position", -1)
            bot_member = guild.me if guild is not None else None
            bot_top = getattr(getattr(bot_member, "top_role", None), "position", -1)
            ctx = RequestContext(guild_id=getattr(guild, "id", None), user_id=getattr(user, "id", None), action=name,
                source="ai" if getattr(interaction, "_miro_ai_source", False) else "command", is_admin=is_admin,
                is_owner=is_owner, is_bot_identity=is_bot_identity, user_top_role_position=user_top,
                target_role_position=target_role_position, bot_top_role_position=bot_top,
                metadata={"target_role_id": target_role_id})
            decision = engine.evaluate(ctx)
            return bool(decision.allowed), str(getattr(decision, "reason", ""))
        except Exception as exc:
            logger.exception("Agent permission evaluation failed")
            return False, f"permission engine error: {exc}"

    async def execute(self, interaction, name: str, params: Dict[str, Any], request_id: str = "", retries: int = 1,
                      timeout_override: Optional[float] = None, job_id: str = "") -> Receipt:
        started = time.time()
        # Native planner context is internal-only and MUST NOT reach Discord.
        request_text = str(params.pop("_agent_request", "") or "")

        # Repair the exact failure seen in production: bulk_delete_channels
        # arrived without channel_ids even though duplicate discovery was also
        # requested. Resolve exact live IDs here before validation/dispatch.
        if name == "bulk_delete_channels" and not (params.get("channel_ids") or params.get("channels")):
            try:
                guild = getattr(interaction, "guild", None)
                ids = _resolve_bulk_delete_ids(guild, params, request_text)
                params["channel_ids"] = ids
                logger.info(f"[AGENT REPAIR] bulk_delete_channels resolved {len(ids)} exact live channel IDs")
            except Exception as exc:
                message = f"unable to safely resolve bulk_delete_channels dependencies: {str(exc)[:300]}"
                logger.warning(f"[AGENT REPAIR] {message}")
                return Receipt(action=name, success=False, verified=False, error_type=ErrorType.INVALID_PARAMS,
                    message=message, request_id=request_id, job_id=job_id, parameters=dict(params),
                    started_at=started, finished_at=time.time())

        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            return Receipt(action=name, success=False, verified=False, error_type=ErrorType.PROVIDER_ERROR,
                message="ActionHandler unavailable", request_id=request_id, job_id=job_id, parameters=dict(params),
                started_at=started, finished_at=time.time())

        allowed, permission_reason = self._permission_decision(interaction, name, params)
        if not allowed:
            return Receipt(action=name, success=False, verified=False, error_type=ErrorType.MISSING_PERMISSION,
                message=permission_reason or "permission denied", request_id=request_id, job_id=job_id,
                parameters=dict(params), started_at=started, finished_at=time.time())

        async def run():
            ctx = interaction
            try:
                if ctx is None: ctx = self.build_bot_identity_interaction(self.bot, params.get("guild_id") or 0)
                success, info = await handler.dispatch(ctx, name, params)
                return bool(success), info or {}
            except Exception as e:
                logger.exception(f"Agent tool {name} raised")
                return False, {"error": str(e)[:500], "exception": type(e).__name__}

        limit = timeout_override or tool_timeout(name)
        try:
            success, info = await asyncio.wait_for(with_retry(run, retries, 1.5), timeout=limit)
        except asyncio.TimeoutError:
            success, info = False, {"error": f"tool timed out after {limit:.0f}s"}
        except Exception as e:
            logger.exception(f"Agent tool {name} execution wrapper failed")
            success, info = False, {"error": str(e)[:500], "exception": type(e).__name__}

        info = info if isinstance(info, dict) else {"result": str(info)}
        error_text = str(info.get("error", "")); target_id = ""
        for key in ("channel_id", "role_id", "user_id", "member_id"):
            if info.get(key): target_id = str(info[key]); break
        if not target_id:
            for key in ("channel_id", "role_id", "user_id", "member_id"):
                if params.get(key): target_id = str(params[key]); break
        message = str(info.get("message") or "").strip()
        if not message:
            if isinstance(info.get("duplicates"), list):
                ids = [str(x.get("id")) for x in info["duplicates"] if isinstance(x, dict) and x.get("id")]
                message = f"duplicates={ids}"
            elif isinstance(info.get("matches"), list):
                ids = [str(x.get("id")) for x in info["matches"] if isinstance(x, dict) and x.get("id")]
                message = f"matches={ids}"
            elif error_text: message = error_text
            else: message = str(info.get("result") or "ActionHandler returned no message")
        et = ErrorType.NONE if success else (classify_error(error_text) if error_text else ErrorType.UNKNOWN)
        try:
            tool_meta = self.get(name)
            target_type = (tool_meta.get("metadata") or {}).get("object_type", tool_meta.get("object_type", ""))
        except Exception: target_type = ""
        return Receipt(action=name, target_id=target_id, target_type=target_type, success=bool(success), verified=False,
            error_type=et, message=message[:500], request_id=request_id, job_id=job_id, parameters=dict(params),
            started_at=started, finished_at=time.time())

    def get(self, name: str) -> Dict[str, Any]:
        from .tool_registry import tool_registry
        return tool_registry.get(name)
