"""V8 mathematical completion gate based on effective execution receipts."""
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
        latest = {}
        for receipt in result.receipts:
            if get_meta(receipt.action).get("operation") == "query":
                continue
            try:
                params = json.dumps(receipt.parameters or {}, sort_keys=True, default=str)
            except Exception:
                params = str(receipt.parameters)
            latest[(receipt.action, params)] = receipt
        return list(latest.values())

    @staticmethod
    def _units(receipt: Receipt) -> int:
        """One receipt can represent several verified targets for batch tools."""
        if receipt.action in {"bulk_delete_channels", "cleanup_duplicate_channels"}:
            ids = receipt.parameters.get("channel_ids") or receipt.parameters.get("channels") or []
            if isinstance(ids, list) and ids:
                return len(ids)
        return 1

    def evaluate(self, result: AgentExecutionResult, final_text: str, actionable: bool) -> GateVerdict:
        receipts = self._effective_mutations(result)
        successful = [r for r in receipts if r.success]
        failed = [r for r in receipts if not r.success]
        verified = [r for r in successful if r.verified]
        unverified = [r for r in successful if not r.verified]

        verified_units = sum(self._units(r) for r in verified)
        effective_units = sum(self._units(r) for r in receipts)
        failed_units = sum(self._units(r) for r in failed)

        actions_successful = bool(receipts) and not failed
        state_verified = bool(receipts) and not failed and not unverified
        if actionable and result.requested_count > 0:
            state_verified = state_verified and verified_units == result.requested_count
            actions_successful = actions_successful and verified_units == result.requested_count

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
            reason = "not every requested mutation target has a successful verified receipt"
            if claims_success and not explains_failure:
                reason = "blocked fabricated success claim: execution is not fully verified"
            return GateVerdict(False, actions_successful, False, False, reason)
        if actionable and failed:
            return GateVerdict(False, False, False, False,
                               f"{failed_units} mutation target(s) failed")

        return GateVerdict(goal_completed, actions_successful, state_verified,
                           (not actionable) or goal_completed)
