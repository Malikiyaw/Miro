"""V8 bridge for AI chat.

Mutation requests are intercepted before the legacy conversational path can
answer. The old _execute_actions hook is also disabled so there is no second AI
execution gateway hiding behind the chat system.
"""
from functools import wraps

from agent.harness import AgentHarness
from agent.request_classifier import classify_request

_INSTALLED = False


def install(AIChatSystem):
    global _INSTALLED
    if _INSTALLED:
        return

    original_chat = AIChatSystem._handle_ai_chat

    @wraps(original_chat)
    async def v8_handle_ai_chat(self, message, chat_channel):
        classification = classify_request(message.content)
        if not classification.execution_required:
            return await original_chat(self, message, chat_channel)

        harness = getattr(self.bot, "agent_harness", None)
        if harness is None:
            harness = AgentHarness(self.bot)
            self.bot.agent_harness = harness

        result = await harness.run_message(message, chat_channel=chat_channel)
        response = result.response
        if response is not None:
            return response
        return await message.channel.send(
            "⚠️ The agent could not produce a verified execution result.",
            suppress_embeds=True,
        )

    async def v8_legacy_execute_actions_disabled(self, message, result):
        """Hard-stop the pre-V8 direct action path.

        All state-changing AI work must enter AgentHarness -> AgentRuntime ->
        Executor -> ActionHandler. Keeping this hook inert prevents a future
        conversational code path from silently bypassing the harness.
        """
        raise RuntimeError("LEGACY_AI_EXECUTION_DISABLED: use AgentHarness")

    AIChatSystem._handle_ai_chat = v8_handle_ai_chat
    AIChatSystem._execute_actions = v8_legacy_execute_actions_disabled
    _INSTALLED = True


__all__ = ["install"]
