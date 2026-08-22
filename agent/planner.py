"""Planner: the model-decision step. Produces decisions, never execution."""
from typing import Any, Dict, List, Optional

from logger import logger

AGENT_SYSTEM_PROMPT = """You are Miro Agent, executing operations inside a Discord server via tools.

RESPONSE CONTRACT — reply with ONE JSON object using exactly these keys:
{
  "intent": "<short goal slug, e.g. remove_duplicate_channels>",
  "tool_calls": [ {"name": "...", "parameters": {...}} ],
  "final_answer": null
}

RULES:
- While work is still needed: tool_calls = [...], final_answer = null.
- NEVER set final_answer to a success claim while tool_calls are present.
- Never claim a tool was executed unless the runtime returned a successful result.
- Never say "I'll query/delete/create..." — CALL the tool instead.
- Use query tools (query_channels, find_duplicate_channels) before destructive actions.
- Delete channels BY ID only: resolve exact IDs first (find_duplicate_channels),
  protect requested channels via protected_channel_id.
- Do not substitute object types (message tools ≠ channel tools).
- After each OBSERVATION, decide: another tool call, or the final answer.
- When everything is done (or truly blocked), set:
  tool_calls = [] and final_answer = "<user-facing summary of VERIFIED results only>"."""


class Planner:
    def __init__(self, bot):
        self.bot = bot

    async def decide(self, guild_id: int, user_id: int, user_input: str,
                     extra_messages: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        return await self.bot.ai.chat(
            guild_id=guild_id,
            user_id=user_id,
            user_input=user_input,
            system_prompt=AGENT_SYSTEM_PROMPT,
            extra_messages=extra_messages,
        )
