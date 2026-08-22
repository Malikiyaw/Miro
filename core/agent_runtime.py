"""V7 compatibility entrypoint and hard completion wrapper.

The implementation remains in agent.runtime. This wrapper is the public runtime
used by the Discord AI chat path and adds a final backend truth check so an
internal COMPLETED state can never escape when mutations are unverified.
"""
from agent.runtime import AgentRuntime as _AgentRuntime
from agent.runtime import needs_confirmation, MAX_AGENT_STEPS, DANGEROUS_TOOLS
from agent.state import (
    AgentState, JobStatus, ErrorType, Receipt, Observation,
    AgentExecutionResult, FinalAIResponse, AgentJob, classify_error,
)
from agent.tools import find_duplicate_channels, validate_params


class AgentRuntime(_AgentRuntime):
    async def run(self, *args, **kwargs):
        final, result = await super().run(*args, **kwargs)

        # V7 hard invariant: an actionable request is never COMPLETED unless
        # every execution receipt is successful AND verified. This catches any
        # legacy/edge path inside the underlying runtime that might otherwise
        # return a completed state after a blocked or unverified operation.
        request = ""
        if len(args) >= 2:
            request = str(args[1] or "")
        elif "user_request" in kwargs:
            request = str(kwargs.get("user_request") or "")

        from core.action_meta import infer_intent
        _, operation = infer_intent(request)
        actionable = operation in ("delete", "create") or any(
            word in request.lower() for word in (
                "lock", "rename", "move", "ban", "kick", "assign", "remove", "timeout"
            )
        )

        if actionable:
            receipts = result.receipts
            fully_verified = bool(receipts) and all(
                r.success and r.verified for r in receipts
            )
            if not fully_verified:
                result.final_state = AgentState.FAILED
                if result.job:
                    result.job.status = AgentState.FAILED
                verified = sum(1 for r in receipts if r.success and r.verified)
                failed = [r for r in receipts if not r.success]
                unverified = [r for r in receipts if r.success and not r.verified]
                lines = ["⚠️ Operation not completed — backend verification did not pass."]
                lines.append(
                    f"Verified: {verified}/{len(receipts)}"
                    + (f" | Failed: {len(failed)}" if failed else "")
                    + (f" | Unverified: {len(unverified)}" if unverified else "")
                )
                for receipt in failed[:5]:
                    lines.append(
                        f"❌ `{receipt.action}` [{receipt.error_type.value}] — {receipt.message[:140]}"
                    )
                if not receipts:
                    lines.append("❌ No ActionHandler tool call was executed.")
                final = FinalAIResponse(
                    text="\n".join(lines),
                    state=AgentState.FAILED,
                    request_id=getattr(final, "request_id", ""),
                )

        return final, result


__all__ = [
    "AgentRuntime", "needs_confirmation", "MAX_AGENT_STEPS", "DANGEROUS_TOOLS",
    "AgentState", "JobStatus", "ErrorType", "Receipt", "Observation",
    "AgentExecutionResult", "FinalAIResponse", "AgentJob", "classify_error",
    "find_duplicate_channels", "validate_params",
]
