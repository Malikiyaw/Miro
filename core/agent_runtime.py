"""V8 public AgentRuntime entrypoint and backend completion wrapper."""
from agent.runtime import AgentRuntime as _AgentRuntime
from agent.runtime import needs_confirmation, MAX_AGENT_STEPS, DANGEROUS_TOOLS
from agent.state import (
    AgentState, JobStatus, ErrorType, Receipt, ExecutionReceipt, Observation,
    AgentExecutionResult, FinalAIResponse, AgentJob, classify_error,
)
from agent.tools import find_duplicate_channels, validate_params
from agent.request_classifier import classify_request
from agent.completion_gate import CompletionGate


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

        if classification.execution_required:
            gate = CompletionGate()
            verdict = gate.evaluate(result, getattr(final, "text", ""), True)
            if not verdict.allowed:
                result.final_state = AgentState.FAILED
                if result.job:
                    result.job.status = AgentState.FAILED
                mutations = gate._effective_mutations(result)
                verified_units = sum(gate._units(r) for r in mutations if r.success and r.verified)
                failed = [r for r in mutations if not r.success]
                unverified = [r for r in mutations if r.success and not r.verified]
                expected = classification.requested_count or sum(gate._units(r) for r in mutations)
                lines = ["⚠️ Operation not completed — backend verification did not pass."]
                lines.append(
                    f"Verified: {verified_units}/{expected}"
                    + (f" | Failed: {len(failed)}" if failed else "")
                    + (f" | Unverified: {len(unverified)}" if unverified else "")
                )
                for receipt in failed[:5]:
                    lines.append(f"❌ `{receipt.action}` [{receipt.error_type.value}] — {receipt.message[:140]}")
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
