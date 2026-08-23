"""
Observer: feeds execution + verification results back into the agent
reasoning stream, and renders the live progress board from REAL runtime
state (never model narration).
"""
from typing import Dict, List

from agent.state import AgentExecutionResult, Observation, Receipt


class Observer:
    def __init__(self):
        self.history: List[str] = []

    def record(self, obs: Observation) -> str:
        if obs.success and obs.verified:
            marker = "✅"
        elif obs.success:
            marker = "⚠️"
        else:
            marker = "❌"
        line = f"{marker} `{obs.tool}`"
        if not obs.success and obs.detail:
            line += f" — {obs.detail[:80]}"
        self.history.append(line)
        return line

    def board(self, current: str = "") -> str:
        lines = ["🤖 Miro Agent", "━━━━━━━━━━━━━━━━"]
        lines.extend(self.history[-6:])
        if current:
            lines.append(current)
        return "\n".join(lines)[:1900]

    def observation_message(self, obs: Observation) -> Dict[str, str]:
        """The reasoning-stream entry for one executed+verified tool call.
        Carries the structured result per the V7 protocol so the model can
        reason over exact outcomes (tool_call_id/success/verified/result)."""
        import json as _json
        structured = ""
        if obs.receipt is not None:
            structured = "\n" + _json.dumps({
                "tool_call_id": obs.receipt.request_id,
                "tool": obs.tool,
                "success": obs.success,
                "verified": obs.verified,
                "error_type": obs.receipt.error_type.value,
                "result": {"message": obs.detail[:400]},
            }, ensure_ascii=False)
        return {"role": "user", "content":
                f"OBSERVATION after `{obs.tool}`: {obs.render()}{structured}\n"
                f"If the goal is fully met, reply with the final summary and NO actions."}

    def rejection_message(self, name: str, reason: str, suggested=None) -> Dict[str, str]:
        sug = f" Use one of: {', '.join(suggested)}." if suggested else ""
        return {"role": "user", "content":
                f"REJECTED ACTION `{name}`: {reason}{sug} Replan."}

    def nudge_message(self) -> Dict[str, str]:
        return {"role": "user", "content":
                "INVALID_AGENT_TURN: text-only response on an actionable request. "
                "You described an action but called NO tool. That is not execution. "
                "Call the appropriate tool NOW."}

    def loop_message(self) -> Dict[str, str]:
        return {"role": "user", "content":
                "LOOP_DETECTED: repeated identical tool call without progress. "
                "Change approach or finish with a summary of what IS done."}
