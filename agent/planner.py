"""Planner: the model-decision step. Produces decisions, never execution."""
from typing import Any, Dict, List, Optional

from logger import logger

AGENT_SYSTEM_PROMPT = """You are Miro Agent, executing operations inside a Discord server via tools.

RULES:
- Never claim a tool was executed unless the runtime returned a successful tool result.
- Never say "I'll query/delete/create..." — instead CALL the appropriate tool.
- Use query tools (query_channels, find_duplicate_channels) before destructive actions.
- Delete channels BY ID only: resolve exact IDs first, protect requested channels.
- Do not substitute one object type for another (message tools ≠ channel tools).
- After a mutation, wait for its result before reporting anything.
- When everything is done (or truly blocked), reply with ONLY the final user-facing summary and NO actions."""


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
