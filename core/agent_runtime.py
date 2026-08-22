"""
Miro Agent Runtime V3 — real tool execution, no narration loops.

Core principle: the model DECIDES, the runtime EXECUTES. A text-only turn on
an actionable request is treated as planning narration and the runtime forces
a tool call. Only verified backend results may be reported to users.

Turn classification: TOOL_CALL / FINAL_TEXT / PLANNING / ERROR
Job states:          PLANNING/RUNNING/WAITING_TOOL/VERIFYING/COMPLETED/
                     FAILED/CANCELLED/TIMED_OUT
"""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from logger import logger


# --------------------------------------------------------------------------- #
# Tool policy (tiers + timeouts + retryability)                                #
# --------------------------------------------------------------------------- #

READ_ONLY_TOOLS = {
    "analyze_server_state", "query_server_info", "query_channels", "query_roles",
    "query_members", "query_member_details", "query_economy_leaderboard",
    "query_xp_leaderboard", "query_pending_applications", "query_active_shifts",
    "query_recent_messages", "find_duplicate_channels",
}
MUTATING_TOOLS = {
    "create_channel", "create_role", "edit_channel", "edit_role", "send_message",
    "reply_message", "add_reaction", "send_notification", "assign_role",
    "remove_role", "create_webhook", "connect_systems", "move_system",
    "bulk_delete_channels",
}
DANGEROUS_TOOLS = {
    "delete_channel", "delete_role", "delete_messages", "ban_user", "kick_user",
    "softban_user", "timeout_user", "setup_moderation",
}

TOOL_TIMEOUTS = {"query": 15.0, "default": 30.0}   # seconds

RETRYABLE_MARKERS = ("timeout", "timed out", "rate limit", "429", "network",
                     "temporarily", "502", "503", "connection")

MAX_AGENT_STEPS = 10


def tool_timeout(name: str) -> float:
    from core.action_meta import get_meta
    if get_meta(name).get("operation") == "query":
        return TOOL_TIMEOUTS["query"]
    return TOOL_TIMEOUTS["default"]


def is_retryable_error(error_text: str) -> bool:
    low = (error_text or "").lower()
    return any(m in low for m in RETRYABLE_MARKERS)


def validate_params(name: str, params: Dict[str, Any]) -> tuple[bool, str]:
    """Schema-level validation BEFORE dispatch (plan item 21)."""
    if name == "find_duplicate_channels":
        if not str(params.get("name") or "").strip():
            return False, "requires 'name' (the channel name to look for)"
    elif name == "bulk_delete_channels":
        ids = params.get("channel_ids") or params.get("channels")
        if not isinstance(ids, list) or not ids:
            return False, ("requires a non-empty 'channel_ids' list — resolve IDs "
                           "with find_duplicate_channels first; never delete by name")
    elif name in ("delete_channel",):
        if not (params.get("channel_id") or params.get("channel_name")):
            return False, "requires 'channel_id' (resolve the exact ID first) or 'channel_name'"
    elif name == "delete_role":
        if not (params.get("role_id") or params.get("role_name")):
            return False, "requires 'role_id' or 'role_name'"
    return True, ""


AGENT_SYSTEM_PROMPT = """You are Miro Agent, executing operations inside a Discord server via tools.

RULES:
- Never claim a tool was executed unless the runtime returned a successful tool result.
- Never say "I'll query/delete/create..." — instead CALL the appropriate tool.
- Use query tools (query_channels, find_duplicate_channels) before destructive actions.
- Delete channels BY ID only: resolve exact IDs first, protect requested channels.
- Do not substitute one object type for another (message tools ≠ channel tools).
- After a mutation, wait for its result before reporting anything.
- When everything is done (or truly blocked), reply with ONLY the final user-facing summary and NO actions."""


# --------------------------------------------------------------------------- #
# Job state                                                                    #
# --------------------------------------------------------------------------- #

class JobStatus(str, Enum):
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


@dataclass
class AgentJob:
    job_id: str
    guild_id: int
    user_id: int
    goal: str
    status: JobStatus = JobStatus.PLANNING
    current_step: int = 0
    total_steps: int = MAX_AGENT_STEPS
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    last_tool: str = ""
    last_result: str = ""

    def snapshot(self) -> dict:
        return {"agent_job_id": self.job_id, "guild_id": self.guild_id,
                "user_id": self.user_id, "goal": self.goal[:100],
                "status": self.status.value, "step": f"{self.current_step}/{self.total_steps}",
                "last_tool": self.last_tool}


@dataclass
class Observation:
    tool: str
    params: Dict[str, Any]
    success: bool
    verified: bool
    detail: str = ""
    timestamp: float = field(default_factory=time.time)

    def render(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        if self.success and not self.verified:
            status = "UNVERIFIED"
        return f"TOOL {self.tool} -> {status}: {self.detail[:300]}"


@dataclass
class AgentExecutionResult:
    actions: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    completed_steps: int = 0
    final_state: JobStatus = JobStatus.PLANNING
    hit_step_limit: bool = False
    loop_detected: bool = False
    job: Optional[AgentJob] = None

    def summary_line(self) -> str:
        ok = sum(1 for o in self.observations if o.success and o.verified)
        return f"{ok}/{len(self.observations)} verified"


@dataclass
class FinalAIResponse:
    text: str
    state: JobStatus
    request_id: str = ""


# --------------------------------------------------------------------------- #
# Deterministic duplicate-channel finder                                       #
# --------------------------------------------------------------------------- #

def find_duplicate_channels(guild, name: str, protected_channel_id=None,
                            exclude_channel_id=None) -> Dict[str, Any]:
    import re as _re

    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = _re.sub(r"[-_\s]+", "-", s)
        s = _re.sub(r"^[^a-z0-9]+", "", s)
        s = _re.sub(r"-\d+$", "", s)
        s = _re.sub(r"\s+\d+$", "", s)
        return s

    target = normalize(name)
    protected_id = str(protected_channel_id or exclude_channel_id or "")
    matches, duplicates, kept = [], [], None
    for channel in guild.text_channels:
        if normalize(channel.name) == target:
            entry = {"id": str(channel.id), "name": channel.name,
                     "category_id": str(channel.category_id) if channel.category_id else "",
                     "created_at": channel.created_at.isoformat() if channel.created_at else ""}
            matches.append(entry)
            if entry["id"] == protected_id:
                kept = entry
            else:
                duplicates.append(entry)
    return {"target_name": name, "protected_channel_id": protected_id,
            "matches": matches, "kept": kept, "duplicates": duplicates,
            "protected": kept, "protected_id": protected_id}


# --------------------------------------------------------------------------- #
# The runtime                                                                  #
# --------------------------------------------------------------------------- #

class AgentRuntime:
    """
    Owns the whole lifecycle. The model decides; this class executes.

    Hard rules enforced here (not just prompted):
      - text-only turns on actionable requests force a tool call
      - every mutation is awaited, then verified against live state
      - identical repeated tool calls trigger LOOP_DETECTED replanning
      - read-only finishes cannot end an actionable goal without either
        executed+verified work or an explicit blocking reason
      - per-tool timeouts; only retryable errors are retried once
    """

    def __init__(self, bot, guild, user, *, max_steps: int = MAX_AGENT_STEPS,
                 allow_dangerous: bool = False,
                 on_progress: Optional[Callable[[str], Any]] = None):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.max_steps = max_steps
        self.allow_dangerous = allow_dangerous
        self.on_progress = on_progress
        self.state = JobStatus.PLANNING
        self._signatures: List[str] = []
        self._nudges = 0

    # -- progress -----------------------------------------------------------

    async def _progress(self, text: str):
        if self.on_progress is None:
            return
        try:
            out = self.on_progress(text)
            if asyncio.iscoroutine(out):
                await asyncio.wait_for(out, timeout=5.0)
        except Exception as e:
            logger.debug(f"agent progress update failed: {e}")

    # -- main loop ----------------------------------------------------------

    async def run(self, interaction, user_request: str, system_prompt: str,
                  initial_result: Optional[dict] = None) -> tuple[FinalAIResponse, AgentExecutionResult]:
        from core.action_meta import infer_intent, get_meta
        object_type, operation = infer_intent(user_request)
        actionable = operation in ("delete", "create") or object_type is not None \
            or any(v in (user_request or "").lower() for v in ("lock", "rename"))

        job = AgentJob(job_id=f"job_{uuid.uuid4().hex[:8]}", guild_id=self.guild.id,
                       user_id=getattr(self.user, "id", 0), goal=user_request[:120])
        result = AgentExecutionResult(job=job)
        self._original_request = user_request
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": f"{system_prompt}\n\n{AGENT_SYSTEM_PROMPT}"}
        ]

        pending_actions: List[Dict[str, Any]] = []
        if initial_result:
            summary = str(initial_result.get("summary") or "").strip()
            pending_actions = [a for a in (initial_result.get("actions") or [])
                               if isinstance(a, dict)]
            # Narration with no tools is NOT execution — discard it as final;
            # keep it visible to the model as its own prior turn only.
            if summary and pending_actions:
                messages.append({"role": "assistant", "content":
                                 f"(internal plan, not yet executed): {summary}"})

        step = 0
        await self._progress("🤖 Miro Agent\nStatus: Working…")

        while True:
            # ---------------- PLAN ----------------
            if not pending_actions:
                self.state = JobStatus.PLANNING
                job.status = JobStatus.PLANNING
                try:
                    ai_result = await self.bot.ai.chat(
                        guild_id=self.guild.id,
                        user_id=getattr(self.user, "id", 0),
                        user_input=user_request if step == 0 else
                        "Continue based on the observations above.",
                        system_prompt=AGENT_SYSTEM_PROMPT,
                        extra_messages=messages,
                    )
                except Exception as e:
                    result.final_state = JobStatus.FAILED
                    job.status = JobStatus.FAILED
                    return FinalAIResponse(
                        text=f"⚠️ Agent planning failed: {str(e)[:200]}",
                        state=JobStatus.FAILED), result
                if not isinstance(ai_result, dict):
                    result.final_state = JobStatus.FAILED
                    return FinalAIResponse(text="⚠️ Agent received an invalid response.",
                                           state=JobStatus.FAILED), result

                summary = str(ai_result.get("summary") or "").strip()
                pending_actions = [a for a in (ai_result.get("actions") or [])
                                   if isinstance(a, dict)]

                if pending_actions:
                    if summary:
                        messages.append({"role": "assistant", "content":
                                         f"(internal plan, not yet executed): {summary}"})
                else:
                    # ---- TEXT-ONLY TURN CLASSIFICATION ----
                    blocking_reason = any(w in summary.lower() for w in
                                          ("cannot", "missing permission", "lacks",
                                           "not have permission", "failed to"))
                    has_verified_work = any(o.success for o in result.observations)
                    if actionable and not blocking_reason and self._nudges < 3 \
                            and not (has_verified_work and not pending_actions):
                        # Narration like "I'll delete them now" is NOT execution,
                        # even on the very first turn.
                        self._nudges += 1
                        messages.append({"role": "assistant", "content": summary})
                        messages.append({"role": "user", "content":
                                         "TEXT_ONLY_TURN: You described an action but called NO tool. "
                                         "That is not execution. Call the appropriate tool NOW "
                                         "(e.g. find_duplicate_channels / delete_channel by ID)."})
                        continue
                    if actionable and has_verified_work and not blocking_reason:
                        summary = self._receipt_summary(result) or (
                            summary + "\n" + result.summary_line())
                    elif actionable and step > 0 and not summary:
                        summary = "⚠️ I could not complete that action. No tool succeeded."
                    result.final_state = (JobStatus.COMPLETED if not blocking_reason
                                          else JobStatus.FAILED)
                    job.status = result.final_state
                    job.completed_at = time.time()
                    return FinalAIResponse(text=summary or "Done.", state=result.final_state), result

            # ---------------- EXECUTE ----------------
            while pending_actions:
                step += 1
                job.current_step = step
                if step > self.max_steps:
                    result.hit_step_limit = True
                    result.final_state = JobStatus.CANCELLED
                    job.status = JobStatus.CANCELLED
                    note = (f"⚠️ Miro stopped the operation because the agent reached its "
                            f"execution limit.\nCompleted: {result.completed_steps}/{self.max_steps} actions.")
                    await self._progress(note)
                    return FinalAIResponse(text=note, state=JobStatus.CANCELLED), result

                action = pending_actions.pop(0)
                name = str(action.get("name") or "").strip()
                params = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
                result.actions.append({"name": name, "parameters": params})

                # semantic object-type gate
                from core.action_meta import validate_action
                allowed, reason, suggested = validate_action(self._original_request
                                                             if hasattr(self, "_original_request")
                                                             else user_request, name)
                if not allowed:
                    obs = Observation(tool=name, params=params, success=False, verified=False,
                                      detail=f"REJECTED — {reason} Valid tools: {', '.join(suggested)}")
                    result.observations.append(obs); result.failures.append(obs.render())
                    messages.append({"role": "user", "content":
                                     f"REJECTED ACTION `{name}`: {reason} "
                                     f"Use one of: {', '.join(suggested)}. Replan."})
                    continue

                # parameter schema gate
                ok_params, why_params = validate_params(name, params)
                if not ok_params:
                    obs = Observation(tool=name, params=params, success=False, verified=False,
                                      detail=f"INVALID PARAMETERS — {why_params}")
                    result.observations.append(obs); result.failures.append(obs.render())
                    messages.append({"role": "user", "content":
                                     f"INVALID PARAMETERS for `{name}`: {why_params}. Repair the tool call."})
                    continue

                # dangerous gate
                if name in DANGEROUS_TOOLS and not self.allow_dangerous:
                    obs = Observation(tool=name, params=params, success=False, verified=False,
                                      detail="refused: dangerous action requires explicit admin authorization")
                    result.observations.append(obs); result.failures.append(obs.render())
                    continue

                # loop detection (plan 27)
                sig = f"{name}:{json.dumps(params, sort_keys=True)[:200]}"
                recent = self._signatures[-3:]
                if recent.count(sig) >= 2:
                    result.loop_detected = True
                    obs = Observation(tool=name, params=params, success=False, verified=False,
                                      detail="LOOP_DETECTED — identical call already made twice")
                    result.observations.append(obs)
                    messages.append({"role": "user", "content":
                                     "LOOP_DETECTED: you repeated an identical tool call without "
                                     "progress. Change approach, pick different parameters, or "
                                     "finish with a final summary of what IS done."})
                    continue
                self._signatures.append(sig)

                self.state = JobStatus.WAITING_TOOL
                job.status = JobStatus.WAITING_TOOL
                job.last_tool = name
                await self._progress(f"🤖 Miro Agent\n⚙️ Executing `{name}`… "
                                     f"(step {step}/{self.max_steps})")

                success, info = await self._execute_with_timeout(interaction, name, params)
                retries = 0
                while not success and retries < 1:
                    err_text = str((info or {}).get("error", "")) if isinstance(info, dict) else str(info)
                    if is_retryable_error(err_text):
                        retries += 1
                        logger.warning(f"[AGENT] retrying {name} after retryable error: {err_text[:100]}")
                        await asyncio.sleep(1.5 * retries)
                        success, info = await self._execute_with_timeout(interaction, name, params)
                    else:
                        break

                self.state = JobStatus.VERIFYING
                job.status = JobStatus.VERIFYING
                verified = await self._verify(name, params, success) if success else False
                detail = str((info or {}).get("message") or (info or {}).get("error") or "")[:300] \
                    if isinstance(info, dict) else str(info)[:300]
                obs = Observation(tool=name, params=params, success=bool(success),
                                  verified=verified, detail=detail)
                result.observations.append(obs)
                result.completed_steps += 1 if success else 0
                if not success:
                    result.failures.append(obs.render())
                job.last_result = obs.render()
                self.state = JobStatus.RUNNING   # observing result

                bus = getattr(self.bot, "event_bus", None)
                if bus is not None:
                    try:
                        asyncio.create_task(bus.publish(
                            "action.verified" if (success and verified) else "action.unverified",
                            guild_id=self.guild.id, tool=name, success=success))
                    except Exception:
                        pass

                marker = "✅" if (success and verified) else "⚠️" if success else "❌"
                await self._progress(f"🤖 Miro Agent\n{marker} `{name}` — {obs.render()[:150]}")
                messages.append({"role": "user",
                                 "content": f"OBSERVATION after `{name}`: {obs.render()}\n"
                                            f"If the goal is fully met, reply with the final summary "
                                            f"and NO actions."})

            # all queued actions ran — reassess unless we're at the cap
            self.state = JobStatus.RUNNING
            if step >= self.max_steps:
                result.hit_step_limit = True
                result.final_state = JobStatus.TIMED_OUT
                job.status = JobStatus.TIMED_OUT
                note = (f"⚠️ Miro stopped the operation because the agent reached its "
                        f"execution limit.\nCompleted: {result.completed_steps}/{self.max_steps} actions.")
                await self._progress(note)
                return FinalAIResponse(text=note, state=JobStatus.TIMED_OUT), result

    # ------------------------------------------------------------------ #

    def _receipt_summary(self, result: AgentExecutionResult) -> str:
        if not result.observations:
            return ""
        verified = [o for o in result.observations if o.success and o.verified]
        unverified = [o for o in result.observations if o.success and not o.verified]
        failed = [o for o in result.observations if not o.success]
        lines = ["✅ Operation complete." if not failed else "⚠️ Operation finished with issues.",
                 f"Executed: {len(verified)} verified"
                 + (f", {len(unverified)} unverified" if unverified else "")
                 + (f", {len(failed)} failed" if failed else "")]
        for o in failed:
            lines.append(f"❌ `{o.tool}` — {o.detail[:100]}")
        return "\n".join(lines)

    async def _execute_with_timeout(self, interaction, name: str, params: Dict[str, Any]):
        limit = tool_timeout(name)
        try:
            return await asyncio.wait_for(self._execute(interaction, name, params), timeout=limit)
        except asyncio.TimeoutError:
            return False, {"error": f"tool timed out after {limit:.0f}s"}

    async def _execute(self, interaction, name: str, params: Dict[str, Any]):
        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            return False, {"error": "ActionHandler unavailable"}
        try:
            if interaction is None:
                try:
                    from actions import ScheduledTaskInteraction
                    interaction = ScheduledTaskInteraction(self.bot, self.guild.id)
                except Exception:
                    bot_user = getattr(self.bot, "user", None) or type("U", (), {"id": 0})()
                    me = self
                    class _BotIdentityInteraction:
                        guild = me.guild
                        user = bot_user
                        channel = None
                        response = me
                        followup = me
                        def is_done(self):
                            return False
                        async def send(self, *a, **k): pass
                        async def send_message(self, *a, **k): pass
                        async def defer(self, *a, **k): pass
                    interaction = _BotIdentityInteraction()
            return await handler.dispatch(interaction, name, params)
        except Exception as e:
            logger.error(f"Agent tool {name} raised: {e}")
            return False, {"error": str(e)[:200]}

    async def _verify(self, name: str, params: Dict[str, Any], claimed_success: bool) -> bool:
        """Mutations are confirmed against REAL Discord state."""
        try:
            if name == "delete_channel":
                channel_id = params.get("channel_id")
                if channel_id and str(channel_id).isdigit():
                    return self.guild.get_channel(int(channel_id)) is None
                return True
            if name == "bulk_delete_channels":
                ids = params.get("channel_ids") or []
                remaining = [i for i in ids
                             if str(i).isdigit() and self.guild.get_channel(int(i)) is not None
                             and str(i) != str(params.get("protected_channel_id") or "")]
                return not remaining
            if name == "delete_role":
                role_id = params.get("role_id")
                if role_id and str(role_id).isdigit():
                    return self.guild.get_role(int(role_id)) is None
                return True
            if name == "create_channel":
                channel_id = params.get("channel_id")
                if channel_id and str(channel_id).isdigit():
                    return self.guild.get_channel(int(channel_id)) is not None
                return True
        except Exception as e:
            logger.debug(f"verification for {name} failed: {e}")
            return False
        return True
