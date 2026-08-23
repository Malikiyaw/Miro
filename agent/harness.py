"""Miro V8 Agent Harness.

The runtime, not the LLM, owns execution. The harness classifies every request,
then routes execution-required work into the agent runtime. A model response
containing only prose can never be accepted as execution.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
import asyncio

from logger import logger
from agent.request_classifier import RequestClass, RequestClassification, classify_request


@dataclass
class HarnessResult:
    classification: RequestClassification
    response: Any = None
    execution_result: Any = None
    handled: bool = False


class AgentHarness:
    """Single entrypoint for Miro's agentic request lifecycle."""

    def __init__(self, bot, *, max_steps: Optional[int] = None):
        self.bot = bot
        self.max_steps = max_steps

    async def run(
        self,
        request: str,
        guild,
        user,
        *,
        interaction=None,
        initial_result: Optional[Dict[str, Any]] = None,
        system_prompt: str = "",
        on_progress=None,
    ) -> HarnessResult:
        classification = classify_request(request)

        if classification.kind == RequestClass.CHAT:
            response = await self.bot.ai.chat(
                guild_id=getattr(guild, "id", 0),
                user_id=getattr(user, "id", 0),
                user_input=request,
                system_prompt=system_prompt or "You are Miro, a helpful Discord assistant.",
                persist=True,
            )
            return HarnessResult(classification, response=response, handled=True)

        # READ_ONLY/QUERY and all mutations use the same agent runtime. The
        # runtime may finish a read-only request without a tool, but mutations
        # are execution_required and therefore cannot finish without receipts.
        from core.agent_runtime import AgentRuntime
        from agent.executor import Executor

        if interaction is None:
            interaction = self._build_interaction(guild, user)

        allow_dangerous = bool(
            getattr(getattr(user, "guild_permissions", None), "administrator", False)
        )
        if self.max_steps is None:
            runtime = AgentRuntime(
                self.bot, guild, user,
                allow_dangerous=allow_dangerous,
                on_progress=on_progress,
            )
        else:
            runtime = AgentRuntime(
                self.bot, guild, user,
                max_steps=self.max_steps,
                allow_dangerous=allow_dangerous,
                on_progress=on_progress,
            )

        # Critical V8 rule: if the first model response contains prose but no
        # tool calls, DO NOT pass that prose as an executable plan. Let the
        # runtime ask the planner again under the execution-required contract.
        safe_initial = None
        if isinstance(initial_result, dict):
            actions = [
                a for a in (initial_result.get("actions") or initial_result.get("tool_calls") or [])
                if isinstance(a, dict)
            ]
            if actions:
                safe_initial = {
                    "summary": str(initial_result.get("summary") or ""),
                    "actions": actions,
                }

        response, execution_result = await runtime.run(
            interaction,
            request[:2000],
            system_prompt or "You are Miro Agent. Execute the user's Discord request through verified tools.",
            initial_result=safe_initial,
        )
        return HarnessResult(
            classification,
            response=response,
            execution_result=execution_result,
            handled=True,
        )

    async def run_message(self, message, *, chat_channel=None) -> HarnessResult:
        """Run a Discord message through V8 and return a response-ready result."""
        progress_message = None

        async def progress(text: str):
            nonlocal progress_message
            try:
                if progress_message is None:
                    progress_message = await message.channel.send(text[:1900])
                else:
                    await progress_message.edit(content=text[:1900])
            except Exception as exc:
                logger.debug(f"V8 progress update failed: {exc}")

        result = await self.run(
            message.content,
            message.guild,
            message.author,
            interaction=self._message_interaction(message),
            system_prompt=getattr(chat_channel, "system_prompt", "") if chat_channel else "",
            on_progress=progress,
        )

        if progress_message is not None and result.response is not None:
            try:
                text = getattr(result.response, "text", None)
                if text:
                    await progress_message.edit(content=text[:2000])
            except Exception as exc:
                logger.debug(f"V8 final progress update failed: {exc}")

        return result

    @staticmethod
    def _message_interaction(message):
        from agent.executor import Executor
        return Executor.build_message_interaction(message)

    @staticmethod
    def _build_interaction(guild, user):
        class _Interaction:
            def __init__(self):
                self.guild = guild
                self.user = user
                self.channel = getattr(guild, "system_channel", None)
                self.response = self
                self.followup = self

            async def send_message(self, *args, **kwargs):
                return None

            async def send(self, *args, **kwargs):
                return None

            async def defer(self, *args, **kwargs):
                return None

            async def edit_message(self, *args, **kwargs):
                return None

        return _Interaction()


__all__ = ["AgentHarness", "HarnessResult"]
