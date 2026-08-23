"""V9 planner: native tool calls plus runtime-only dependency context."""
from typing import Any, Dict, List, Optional

AGENT_SYSTEM_PROMPT = """You are Miro Agent, the reasoning/planning component inside an execution-first runtime.

The runtime is the agent. You are NOT the executor.

RESPONSE CONTRACT — return exactly ONE JSON object:
{
  "intent": "<short goal slug>",
  "tool_calls": [{"name": "...", "parameters": {...}}],
  "final_answer": null
}

EXECUTION-REQUIRED RULES:
- If the user request changes Discord state, the runtime has execution_required=true.
- A mutation turn MUST contain at least one tool_calls entry.
- Prose such as "I'll delete..." without a tool call is invalid.
- Never claim execution before the runtime returns a successful, verified observation.
- Never invent IDs. Resolve exact live IDs with discovery tools first.
- For duplicate channel deletion, identify exact channel IDs, preserve protected IDs,
  then delete and verify each target.
- After every tool observation, either request the next tool or return a verified final answer.

PLAYBOOK — duplicate-channel cleanup:
1. find_duplicate_channels with no name when the target is ambiguous.
2. Read the returned duplicates[] exact IDs.
3. Call bulk_delete_channels with channel_ids containing those exact IDs.
4. Never delete by name and never fabricate IDs.

RECOVERY:
- Transient failures may be retried by the executor.
- Permission failures are permanent unless observed state changes.
- Invalid parameters require a repaired tool call, not repetition.
"""

class Planner:
    def __init__(self, bot):
        self.bot = bot

    async def decide(self, guild_id: int, user_id: int, user_input: str,
                     extra_messages: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        result = await self.bot.ai.chat(
            guild_id=guild_id,
            user_id=user_id,
            user_input=user_input,
            system_prompt=AGENT_SYSTEM_PROMPT,
            extra_messages=extra_messages,
        )
        if not isinstance(result, dict):
            return {'summary': '', 'tool_calls': [], 'final_answer': None}

        calls = result.get('tool_calls') or result.get('actions') or []
        normalized = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get('function') if isinstance(call.get('function'), dict) else {}
            name = call.get('name') or fn.get('name')
            raw_args = call.get('parameters', call.get('arguments', fn.get('arguments', {})))
            if isinstance(raw_args, str):
                import json
                try:
                    raw_args = json.loads(raw_args or '{}')
                except Exception:
                    raw_args = {}
            args = dict(raw_args) if isinstance(raw_args, dict) else {}
            # Runtime-only context. Executor consumes and strips this before
            # ActionHandler.dispatch(); Discord never sees it.
            args.setdefault('_agent_request', user_input)
            normalized.append({'id': call.get('id', ''), 'name': str(name or ''), 'parameters': args})

        return {
            'intent': result.get('intent', ''),
            'tool_calls': normalized,
            'final_answer': result.get('final_answer') or (result.get('summary') if not normalized else None),
            'summary': result.get('summary', ''),
            '_ai_response': result.get('_ai_response'),
            'finish_reason': result.get('finish_reason'),
        }
