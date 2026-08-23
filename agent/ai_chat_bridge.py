"""V8 bridge that makes AI-chat mutations execution-first without duplicating
Miro's existing chat-channel features.

Imported once from modules/__init__.py. It wraps only mutation requests; normal
chat, RPG, translator, help, and read-only behavior stays on the legacy AI chat
path.
"""
from functools import wraps

from agent.harness import AgentHarness
from agent.request_classifier import classify_request


_INSTALLED = False


def install(AIChatSystem):
    global _INSTALLED
    if _INSTALLED:
        return

    original = AIChatSystem._handle_ai_chat

    @wraps(original)
    async def v8_handle_ai_chat(self, message, chat_channel):
        classification = classify_request(message.content)
        if not classification.execution_required:
            return await original(self, message, chat_channel)

        harness = getattr(self.bot, "agent_harness", None)
        if harness is None:
            harness = AgentHarness(self.bot)
            self.bot.agent_harness = harness

        result = await harness.run_message(message, chat_channel=chat_channel)
        response = result.response
        text = getattr(response, "text", None) if response is not None else None
        if not text:
            text = "⚠️ The agent could not complete the requested operation."

        # run_message already renders progress into a single message. If it
        # could not create a progress message, send the final result normally.
        # This is intentionally based on the runtime result, never the model's
        # original summary.
        if result.execution_result is None:
            return await message.channel.send(text[:2000], suppress_embeds=True)

        return response

    AIChatSystem._handle_ai_chat = v8_handle_ai_chat
    _INSTALLED = True


__all__ = ["install"]
