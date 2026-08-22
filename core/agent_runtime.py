"""Compat shim — the implementation lives in the agent/ package."""
from agent.runtime import (AgentRuntime, needs_confirmation,
                           MAX_AGENT_STEPS, DANGEROUS_TOOLS)
from agent.state import (AgentState, JobStatus, ErrorType, Receipt,
                         Observation, AgentExecutionResult, FinalAIResponse,
                         AgentJob, classify_error)
from agent.tools import find_duplicate_channels, validate_params

__all__ = ["AgentRuntime", "needs_confirmation", "MAX_AGENT_STEPS",
           "DANGEROUS_TOOLS", "AgentState", "JobStatus", "ErrorType",
           "Receipt", "Observation", "AgentExecutionResult",
           "FinalAIResponse", "AgentJob", "classify_error",
           "find_duplicate_channels", "validate_params"]
