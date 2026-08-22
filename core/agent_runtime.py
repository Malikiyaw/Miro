"""
Miro Agent Runtime — the controlled plan → tool → observe → replan loop.

Separates internal execution (AgentExecutionResult) from what users see
(FinalAIResponse), enforces a hard step limit, verifies destructive actions
against real Discord state, and feeds every observation back into the next
AI turn.
"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from logger import logger


class AgentState(str, Enum):
    PLANNING = "PLANNING"
    WAITING_FOR_TOOL = "WAITING_FOR_TOOL"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Tool policy tiers (plan item 19). Dangerous tools need explicit user intent;
# they are never inferred from tone.
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
}
DANGEROUS_TOOLS = {
    "delete_channel", "delete_role", "delete_messages", "ban_user", "kick_user",
    "softban_user", "timeout_user", "setup_moderation", "lock_server",
}

MAX_AGENT_STEPS = 8


@dataclass
class Observation:
    tool: str
    params: Dict[str, Any]
    success: bool
    verified: bool          # destructive actions must be confirmed against Discord state
    detail: str = ""
    timestamp: float = field(default_factory=time.time)

    def render(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        if self.success and not self.verified:
            status = "UNVERIFIED"
        return f"TOOL {self.tool} -> {status}: {self.detail[:300]}"


@dataclass
class AgentExecutionResult:
    """Internal record — NEVER posted to Discord directly."""
    actions: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    completed_steps: int = 0
    final_state: AgentState = AgentState.PLANNING
    hit_step_limit: bool = False

    def summary_line(self) -> str:
        ok = sum(1 for o in self.observations if o.success and o.verified)
        return f"{ok}/{len(self.observations)} verified"


@dataclass
class FinalAIResponse:
    """The ONLY thing allowed to reach Discord from an agent run."""
    text: str
    state: AgentState
    request_id: str = ""


class AgentRuntime:
    """
    Runs one agentic operation:

        REQUEST → UNDERSTAND → [PLAN → TOOL → OBSERVE]* → FINAL RESPONSE

    - max_steps hard limit prevents infinite loops
    - destructive tools are verified against live Discord state
    - observations are appended to the conversation so the model can replan
    """

    def __init__(self, bot, guild, user, *, max_steps: int = MAX_AGENT_STEPS,
                 allow_dangerous: bool = False):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.max_steps = max_steps
        self.allow_dangerous = allow_dangerous  # explicit user authorization only
        self.state = AgentState.PLANNING

    # ------------------------------------------------------------------ #
    async def run(self, interaction, user_request: str, system_prompt: str,
                  initial_result: Optional[dict] = None) -> tuple[FinalAIResponse, AgentExecutionResult]:
        result = AgentExecutionResult()
        messages: List[Dict[str, str]] = []

        # Seed with any plan the first AI turn already produced
        pending_actions: List[Dict[str, Any]] = []
        if initial_result:
            summary = str(initial_result.get("summary") or "").strip()
            if summary:
                messages.append({"role": "assistant", "content": summary})
            pending_actions = [a for a in (initial_result.get("actions") or [])
                               if isinstance(a, dict)]

        step = 0
        while True:
            # ---- PLAN / WAITING_FOR_TOOL --------------------------------
            if not pending_actions:
                self.state = AgentState.PLANNING
                try:
                    ai_result = await self.bot.ai.chat(
                        guild_id=self.guild.id,
                        user_id=getattr(self.user, "id", 0),
                        user_input=user_request if step == 0 else
                        "Continue the operation based on the observations above. "
                        "When everything is done, reply with the final result for the user "
                        "and NO actions.",
                        system_prompt=system_prompt,
                    )
                except Exception as e:
                    result.final_state = AgentState.FAILED
                    return FinalAIResponse(
                        text=f"⚠️ Agent planning failed: {str(e)[:200]}", state=result.final_state), result
                if not isinstance(ai_result, dict):
                    result.final_state = AgentState.FAILED
                    return FinalAIResponse(text="⚠️ Agent received an invalid response.", state=AgentState.FAILED), result
                summary = str(ai_result.get("summary") or "").strip()
                pending_actions = [a for a in (ai_result.get("actions") or [])
                                   if isinstance(a, dict)]
                if summary and not pending_actions:
                    # Model says it's done — this is the final answer
                    result.final_state = AgentState.COMPLETED
                    return FinalAIResponse(text=summary, state=AgentState.COMPLETED), result
                if summary:
                    messages.append({"role": "assistant", "content": summary})

            if not pending_actions:
                result.final_state = AgentState.FAILED
                return FinalAIResponse(text="⚠️ The agent could not determine any action to take.",
                                       state=AgentState.FAILED), result

            # ---- EXECUTE + OBSERVE --------------------------------------
            while pending_actions:
                step += 1
                if step > self.max_steps:
                    result.hit_step_limit = True
                    result.final_state = AgentState.CANCELLED
                    note = (f"⚠️ Miro stopped the operation after reaching its safety limit.\n"
                            f"Completed: {result.completed_steps}/{self.max_steps} actions.")
                    return FinalAIResponse(text=note, state=AgentState.CANCELLED), result

                action = pending_actions.pop(0)
                name = str(action.get("name") or "").strip()
                params = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
                result.actions.append({"name": name, "parameters": params})

                if name in DANGEROUS_TOOLS and not self.allow_dangerous:
                    obs = Observation(tool=name, params=params, success=False, verified=False,
                                      detail="refused: dangerous action requires explicit confirmation")
                    result.observations.append(obs)
                    result.failures.append(obs.render())
                    continue

                self.state = AgentState.EXECUTING
                success, info = await self._execute(interaction, name, params)
                verified = await self._verify(name, params, success) if success else False
                detail = ""
                if isinstance(info, dict):
                    detail = str(info.get("message") or info.get("error") or "")[:300]
                elif info:
                    detail = str(info)[:300]
                obs = Observation(tool=name, params=params, success=bool(success),
                                  verified=verified, detail=detail)
                result.observations.append(obs)
                result.completed_steps += 1 if success else 0
                if not success:
                    result.failures.append(obs.render())
                self.state = AgentState.OBSERVING

                # Feed the observation back so the model can replan
                messages.append({"role": "user",
                                 "content": f"OBSERVATION after `{name}`: {obs.render()}"})

            # All queued actions executed — ask the model to reassess
            self.state = AgentState.REPLANNING
            if step >= self.max_steps:
                result.hit_step_limit = True
                result.final_state = AgentState.CANCELLED
                return FinalAIResponse(
                    text=(f"⚠️ Miro stopped the operation after reaching its safety limit.\n"
                          f"Completed: {result.completed_steps}/{self.max_steps} actions."),
                    state=AgentState.CANCELLED), result

    # ------------------------------------------------------------------ #
    async def _execute(self, interaction, name: str, params: Dict[str, Any]):
        handler = getattr(self.bot, "action_handler", None)
        if handler is None:
            return False, {"error": "ActionHandler unavailable"}
        try:
            if interaction is None:
                # Scheduled / headless context: act as the bot identity.
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
                        response = self
                        followup = self
                        def is_done(self):
                            return False
                        async def send(self, *a, **k):
                            pass
                        async def send_message(self, *a, **k):
                            pass
                        async def defer(self, *a, **k):
                            pass
                    interaction = _BotIdentityInteraction()
            return await handler.dispatch(interaction, name, params)
        except Exception as e:
            logger.error(f"Agent tool {name} raised: {e}")
            return False, {"error": str(e)[:200]}

    async def _verify(self, name: str, params: Dict[str, Any], claimed_success: bool) -> bool:
        """Destructive actions are confirmed against REAL Discord state."""
        try:
            if name == "delete_channel":
                channel_id = params.get("channel_id")
                if channel_id and str(channel_id).isdigit():
                    gone = self.guild.get_channel(int(channel_id)) is None
                    return gone
                return True  # no id to check; accept handler verdict
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
        return True  # non-destructive: trust handler result


# ---------------------------------------------------------------------- #
# Deterministic duplicate-channel finder (plan item 18)                   #
# ---------------------------------------------------------------------- #

def find_duplicate_channels(guild, name: str, exclude_channel_id=None) -> Dict[str, Any]:
    """
    Deterministic matching on normalized names — the agent never invents
    duplicate-detection logic itself.
    """
    import re as _re

    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = _re.sub(r"[-_\s]+", "-", s)
        # Discord duplicate naming: emoji/decoration prefixes ("⚠️-name"),
        # clone counters ("name-2", "name 3"). Strip both before comparing.
        s = _re.sub(r"^[^a-z0-9]+", "", s)   # leading non-alphanumerics (emoji etc.)
        s = _re.sub(r"-\d+$", "", s)          # trailing counter: name-2
        s = _re.sub(r"\s+\d+$", "", s)        # trailing counter: name 2
        return s

    target = normalize(name)
    protected_id = str(exclude_channel_id) if exclude_channel_id else None
    matches = []
    for channel in guild.text_channels:
        if normalize(channel.name) == target:
            matches.append({
                "id": str(channel.id),
                "name": channel.name,
                "category_id": str(channel.category_id) if channel.category_id else "",
                "created_at": channel.created_at.isoformat() if channel.created_at else "",
            })
    kept = None
    remaining = []
    for m in matches:
        if m["id"] == protected_id:
            kept = m
        else:
            remaining.append(m)
    return {"matches": matches, "protected_id": protected_id or "",
            "kept": kept, "duplicates": remaining}
