"""V8 public AgentRuntime entrypoint and hard backend completion wrapper.

The underlying runtime performs planning/execution/observation. This wrapper adds
V8 request classification and a final backend truth check so an actionable
request can never escape as COMPLETED without verified mutation receipts.
"""
from agent.runtime import AgentRuntime as _AgentRuntime
from agent.runtime import needs_confirmation, MAX_AGENT_STEPS, DANGEROUS_TOOLS
from agent.state import (
    AgentState, JobStatus, ErrorType, Receipt, ExecutionReceipt, Observation,
    AgentExecutionResult, FinalAIResponse, AgentJob, classify_error,
)
from agent.tools import find_duplicate_channels, validate_params
from agent.request_classifier import classify_request


class AgentRuntime(_AgentRuntime):
    async def run(self, *args, **kwargs):
        request = ""
        if len(args) >= 2:
            request = str(args[1] or "")
        elif "user_request" in kwargs:
            request = str(kwargs.get("user_request") or "")

        classification = classify_request(request)
        if classification.execution_required:
            self.state = AgentState.EXECUTION_REQUIRED

        final, result = await super().run(*args, **kwargs)
        result.execution_required = classification.execution_required
        result.request_class = classification.kind.value
        result.requested_count = classification.requested_count

        # V8 hard invariant: an actionable request is never COMPLETED unless
        # every effective mutation receipt is successful and verified.
        mutations = result.mutation_receipts
        fully_verified = bool(mutations) and all(
            r.success and r.verified for r in mutations
        )
        if classification.execution_required:
            if classification.requested_count > 0:
                fully_verified = fully_verified and len(
                    [r for r in mutations if r.success and r.verified]
                ) == classification.requested_count

            if not fully_verified:
                result.final_state = AgentState.FAILED
                if result.job:
                    result.job.status = AgentState.FAILED
                verified = sum(1 for r in mutations if r.success and r.verified)
                failed = [r for r in mutations if not r.success]
                unverified = [r for r in mutations if r.success and not r.verified]
                lines = ["⚠️ Operation not completed — backend verification did not pass."]
                expected = classification.requested_count or len(mutations)
                lines.append(
                    f"Verified: {verified}/{expected}"
                    + (f" | Failed: {len(failed)}" if failed else "")
                    + (f" | Unverified: {len(unverified)}" if unverified else "")
                )
                for receipt in failed[:5]:
                    lines.append(
                        f"❌ `{receipt.action}` [{receipt.error_type.value}] — {receipt.message[:140]}"
                    )
                if not mutations:
                    lines.append("❌ No mutation tool call was executed.")
                final = FinalAIResponse(
                    text="\n".join(lines),
                    state=AgentState.FAILED,
                    request_id=getattr(final, "request_id", ""),
                )

        return final, result


__all__ = [
    "AgentRuntime", "needs_confirmation", "MAX_AGENT_STEPS", "DANGEROUS_TOOLS",
    "AgentState", "JobStatus", "ErrorType", "Receipt", "ExecutionReceipt", "Observation",
    "AgentExecutionResult", "FinalAIResponse", "AgentJob", "classify_error",
    "find_duplicate_channels", "validate_params",
]
