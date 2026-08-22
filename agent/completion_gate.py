"""
V7 completion gate.

The model never decides whether an operation is complete. Completion is a
backend fact: every requested mutation must have a successful, verified receipt.
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
        successful = [r for r in receipts if r.success]
        failed = [r for r in receipts if not r.success]
        unverified = [r for r in successful if not r.verified]

        # A server-changing request cannot complete without at least one receipt.
        # Every successful mutation must also be verified. "Any verified" is not
        # enough for multi-step work: 2/3 verified must remain incomplete.
        actions_successful = bool(receipts) and not failed
        state_verified = bool(receipts) and not unverified and all(
            r.success and r.verified for r in receipts
        )
        goal_completed = (not actionable) or (actions_successful and state_verified)

        low = (final_text or "").lower()
        explains_failure = any(w in low for w in (
            "cannot", "couldn't", "could not", "missing permission", "failed",
            "unable", "❌", "not completed", "not verified"
        ))
        claims_success = any(w in low for w in (
            "deleted", "removed", "created", "completed", "done", "success", "✅"
        ))

        if actionable and not receipts:
            return GateVerdict(
                goal_completed=False,
                actions_successful=False,
                state_verified=False,
                allowed=False,
                reason="actionable request completed without any tool execution receipt",
            )

        if actionable and not state_verified:
            reason = "not every requested mutation has a successful verified receipt"
            if claims_success and not explains_failure:
                reason = "blocked fabricated success claim: execution is not fully verified"
            return GateVerdict(goal_completed=False,
                               actions_successful=actions_successful,
                               state_verified=False,
                               allowed=False,
                               reason=reason)

        if actionable and failed and not acknowledges(failed, low):
            return GateVerdict(goal_completed=False,
                               actions_successful=False,
                               state_verified=False,
                               allowed=False,
                               reason="failures are not acknowledged in final response")

        return GateVerdict(goal_completed=goal_completed,
                           actions_successful=actions_successful,
                           state_verified=state_verified,
                           allowed=(not actionable) or goal_completed)


def acknowledges(failed_receipts, text_low: str) -> bool:
    if any(w in text_low for w in ("failed", "❌", "couldn't", "could not", "unable")):
        return True
    return all(r.action.lower() in text_low for r in failed_receipts)
