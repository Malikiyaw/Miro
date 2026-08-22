"""
CompletionGate (V6 items 8-9): AI summaries are untrusted. A response is
delivered only when goal_completed, actions_successful and state_verified
all hold — otherwise the factual receipt report is delivered instead.

Invariants:
    PLANNED  ≠ EXECUTED
    EXECUTED ≠ VERIFIED
    VERIFIED =  COMPLETED
"""
from dataclasses import dataclass
from typing import List

from agent.state import AgentExecutionResult, Receipt


@dataclass
class GateVerdict:
    goal_completed: bool
    actions_successful: bool
    state_verified: bool
    allowed: bool
    reason: str = ""


class CompletionGate:
    def evaluate(self, result: AgentExecutionResult, final_text: str,
                 actionable: bool) -> GateVerdict:
        receipts: List[Receipt] = result.receipts
        actions_successful = bool(receipts) and all(r.success for r in receipts)
        state_verified = bool(receipts) and any(r.success and r.verified for r in receipts)
        goal_completed = (not actionable) or (actions_successful and state_verified)

        # A blocking/failure explanation from the model is an honest finish.
        low = (final_text or "").lower()
        explains_failure = any(w in low for w in
                               ("cannot", "couldn't", "could not", "missing permission",
                                "failed to", "lacks", "❌"))
        if explains_failure:
            goal_completed = False

        # Fabricated success detection: claims completion but receipts disagree
        claims_success = any(w in low for w in
                             ("deleted", "removed", "created", "completed",
                              "done", "✅"))
        if actionable and claims_success and not state_verified and not explains_failure:
            return GateVerdict(False, actions_successful, state_verified, False,
                               "completion claim without verified execution")

        failed_receipts = [r for r in receipts if not r.success]
        if actionable and failed_receipts:
            if not acknowledges(failed_receipts, low):
                return GateVerdict(goal_completed=False,
                                   actions_successful=actions_successful,
                                   state_verified=state_verified,
                                   allowed=False,
                                   reason="failures not acknowledged in final response")

        allowed = (not actionable) or (goal_completed and state_verified) or \
            (explains_failure and not state_verified)
        return GateVerdict(goal_completed, actions_successful, state_verified, allowed)


def acknowledges(failed_receipts, text_low: str) -> bool:
    """True when the final text mentions every failed tool or a generic failure."""
    if any(w in text_low for w in ("failed", "❌", "couldn't", "could not")):
        return True
    return all(r.action.lower() in text_low for r in failed_receipts)
