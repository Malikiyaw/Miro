"""V8 request classification for the execution-first agent harness.

The classifier is deliberately deterministic. The LLM is not allowed to decide
whether a request requires execution; the runtime decides that before the model
can produce a user-facing answer.
"""
from dataclasses import dataclass
from enum import Enum
import re


class RequestClass(str, Enum):
    CHAT = "CHAT"
    QUERY = "QUERY"
    READ_ONLY = "READ_ONLY"
    MUTATION = "MUTATION"
    MULTI_STEP_MUTATION = "MULTI_STEP_MUTATION"


MUTATION_PATTERNS = (
    "delete", "remove", "create", "make", "add", "set up", "setup",
    "configure", "enable", "disable", "rename", "edit", "change", "move",
    "lock", "unlock", "ban", "kick", "timeout", "warn", "assign", "revoke",
    "clear", "purge", "wipe", "give", "take", "promote", "demote",
)
QUERY_PATTERNS = (
    "what ", "which ", "list ", "show ", "find ", "search ", "how many",
    "count ", "who ", "where ", "when ", "status", "is ", "are ",
    "do we have", "does the server have", "can you see",
)
MULTI_STEP_PATTERNS = (
    "duplicate", "duplicates", "all ", "every ", "each ", "multiple",
    "bulk", "batch", "set up", "setup", "configure", "and then", "then ",
    "system", "everything", "entire", "missing",
)


@dataclass(frozen=True)
class RequestClassification:
    kind: RequestClass
    execution_required: bool
    requested_count: int = 0


def classify_request(text: str) -> RequestClassification:
    """Classify a user request without asking the model.

    Numeric quantities are captured when present so completion can later compare
    requested vs verified mutations. A zero count means the runtime should infer
    the required work from discovered targets/plan state.
    """
    low = " ".join((text or "").lower().split())
    if not low:
        return RequestClassification(RequestClass.CHAT, False, 0)

    mutation = any(p in low for p in MUTATION_PATTERNS)
    query = any(p in low for p in QUERY_PATTERNS)

    # "find duplicate channels" is discovery, not a mutation.
    if mutation:
        multi = any(p in low for p in MULTI_STEP_PATTERNS)
        numbers = re.findall(r"\b(\d+)\b", low)
        requested = int(numbers[0]) if numbers else 0
        if requested > 1:
            multi = True
        if " and " in low:
            multi = True
        kind = RequestClass.MULTI_STEP_MUTATION if multi else RequestClass.MUTATION
        return RequestClassification(kind, True, requested)

    if query:
        return RequestClassification(RequestClass.READ_ONLY, False, 0)

    return RequestClassification(RequestClass.CHAT, False, 0)


__all__ = ["RequestClass", "RequestClassification", "classify_request"]
