"""
Observer: feeds execution + verification results back into the agent reasoning stream.
Progress is generated from runtime state; structured observations include the exact
resolved parameters so the planner can continue from reality instead of guessing.
"""
from typing import Dict, List
from agent.state import Observation

class Observer:
    def __init__(self): self.history: List[str] = []

    def record(self, obs: Observation) -> str:
        marker = "✅" if obs.success and obs.verified else ("⚠️" if obs.success else "❌")
        line = f"{marker} `{obs.tool}`"
        if not obs.success and obs.detail: line += f" — {obs.detail[:120]}"
        self.history.append(line)
        return line

    def board(self, current: str = "") -> str:
        lines=["🤖 Miro Agent","━━━━━━━━━━━━━━━━"]+self.history[-6:]
        if current: lines.append(current)
        return "\n".join(lines)[:1900]

    def observation_message(self, obs: Observation) -> Dict[str, str]:
        import json
        receipt=obs.receipt
        structured={
            "tool_call_id": getattr(receipt,"request_id","") if receipt else "",
            "tool": obs.tool,
            "success": bool(obs.success),
            "verified": bool(obs.verified),
            "error_type": receipt.error_type.value if receipt else "",
            "target_id": getattr(receipt,"target_id","") if receipt else "",
            "parameters": getattr(receipt,"parameters",{}) if receipt else obs.params,
            "result": {"message": obs.detail[:500]},
        }
        return {"role":"user","content":
            f"OBSERVATION after `{obs.tool}`: {obs.render()}\n"
            f"{json.dumps(structured, ensure_ascii=False, default=str)}\n"
            "Use these exact observed values for the next step. Do not invent IDs. "
            "If the goal is fully met, reply with the final summary and NO actions."}

    def rejection_message(self,name: str,reason: str,suggested=None)->Dict[str,str]:
        sug=f" Use one of: {', '.join(suggested)}." if suggested else ""
        return {"role":"user","content":f"REJECTED ACTION `{name}`: {reason}{sug} Replan."}

    def nudge_message(self)->Dict[str,str]:
        return {"role":"user","content":"INVALID_AGENT_TURN: text-only response on an actionable request. This is not execution. Call the appropriate tool NOW."}

    def loop_message(self)->Dict[str,str]:
        return {"role":"user","content":"LOOP_DETECTED: repeated identical tool call without progress. Change approach or finish with a summary of what IS done."}
