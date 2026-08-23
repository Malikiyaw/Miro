"""V8 completion gate.

The model never decides whether a mutation is complete. Completion is a
backend fact derived from effective execution receipts and live verification.
"""
from dataclasses import dataclass
import json
from typing import List

from agent.state import AgentExecutionResult, Receipt
from core.action_meta import get_meta


@dataclass
class GateVerdict:
    goal_completed: bool
    actions_successful: bool
    state_verified: bool
    allowed: bool
    reason: str = ""


class CompletionGate:
    @staticmethod
    def _effective_mutations(result: AgentExecutionResult) -> List[Receipt]:
        """Keep the latest receipt for each attempted mutation.

        This lets a successful recovery replace an earlier transient/semantic
        failure without allowing an unrelated failed target to disappear.
        """
        latest = {}
        for receipt in result.receipts:
            if get_meta(receipt.action).get("operation") == "query":
                continue
            try:
                params = json.dumps(receipt.parameters or {}, sort_keys=True, default=str)
            except Exception:
                params = str(receipt.parameters)
            key = (receipt.action, params)
            latest[key] = receipt
        return list(latest.values())

    def evaluate(self, result: AgentExecutionResult, final_text: str,
                 actionable: bool) -> GateVerdict:
        receipts = self._effective_mutations(result)
        successful = [r for r in receipts if r.success]
        failed = [r for r in receipts if not r.success]
        verified = [r for r in successful if r.verified]
        unverified = [r for r in successful if not r.verified]

        actions_successful = bool(receipts) and not failed
        state_verified = bool(receipts) and not failed and not unverified

        if actionable and result.requested_count > 0:
            state_verified = state_verified and len(verified) == result.requested_count
            actions_successful = actions_successful and len(verified) == result.requested_count

        goal_completed = (not actionable) or (actions_successful and state_verified)

        low = (final_text or "").lower()
        explains_failure = any(w in low for w in (
            "cannot", "couldn't", "could not", "missing permission", "failed",
            "unable", "❌", "not completed", "not verified", "partial",
        ))
        claims_success = any(w in low for w in (
            "deleted", "removed", "created", "completed", "done", "success", "✅"
        ))

        if actionable and not receipts:
            return GateVerdict(False, False, False, False,
                               "actionable request completed without any mutation execution receipt")

        if actionable and not state_verified:
            reason = "not every requested mutation has a successful verified receipt"
            if claims_success and not explains_failure:
                reason = "blocked fabricated success claim: execution is not fully verified"
            return GateVerdict(False, actions_successful, False, False, reason)

        if actionable and failed:
            return GateVerdict(False, False, False, False,
                               "one or more effective mutation attempts failed")

        return GateVerdict(goal_completed, actions_successful, state_verified,
                           (not actionable) or goal_completed)
