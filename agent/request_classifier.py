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


CONFIRMATION_PATTERNS = (
    "proceed", "confirm", "yes", "yep", "yeah", "do it", "go ahead",
    "execute", "run it", "delete them", "create it", "make it",
    "ok", "okay", "please proceed", "continue", "go on",
)

def is_confirmation_followup(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    # exact short confirmations or contains proceed/confirm
    if low in ("yes", "y", "yeah", "yep", "ok", "okay", "confirm", "proceed", "go"):
        return True
    return any(p in low for p in CONFIRMATION_PATTERNS)


def classify_with_history(text: str, recent_history: list = None) -> RequestClassification:
    """History-aware wrapper: if last turn was a pending mutation (discovery/
    clarification), a short confirmation like 'yes/proceed/do it' inherits it."""
    base = classify_request(text)
    if base.execution_required:
        return base
    if not recent_history:
        return base
    # Look at last assistant message for pending-action markers
    try:
        last_assistant = ""
        for m in reversed(recent_history):
            if m.get("role") == "assistant":
                last_assistant = (m.get("content") or "").lower()
                break
        if not last_assistant:
            return base
        pending_markers = (
            "no mutation was executed yet",
            "tell me to proceed",
            "discovery completed",
            "verified",
            "please clarify",
            "which one",
            "proceed with the deletion",
            "proceed with the creation",
            "awaiting confirmation",
        )
        is_pending = any(x in last_assistant for x in pending_markers) or "?" in last_assistant
        if is_pending and is_confirmation_followup(text):
            # Inherit execution_required from prior context
            # Try to infer mutation type from prior assistant/user history
            low_pending = last_assistant
            # If pending was about delete/create etc, treat as MUTATION
            if any(p in low_pending for p in MUTATION_PATTERNS):
                return RequestClassification(RequestClass.MUTATION, True, 0)
            # Generic confirmation of pending mutation
            return RequestClassification(RequestClass.MUTATION, True, 0)
        # Also: if prior user was MUTATION and current is short follow-up referencing it
        # e.g. user: "delete duplicate channels" -> assistant: "Discovery..." -> user: "do it again with same prompt"
        last_user = ""
        for m in reversed(recent_history):
            if m.get("role") == "user":
                last_user = (m.get("content") or "").lower()
                if last_user != " ".join((text or "").lower().split()):
                    break
        if last_user and any(p in last_user for p in MUTATION_PATTERNS):
            if is_confirmation_followup(text) or len(text.strip().split()) <= 4:
                # Short follow-up after a mutation likely refers to same intent
                # Only upgrade if current text is vague (no new mutation/query)
                if not any(p in " ".join(text.lower().split()) for p in QUERY_PATTERNS):
                    # Check recency: pending assistant asked clarification
                    if is_pending:
                        return RequestClassification(RequestClass.MUTATION, True, 0)
    except Exception:
        pass
    return base


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
        # Numbers DIRECTLY after an object word are TARGET IDs ("delete
        # channel 123"), not quantities. Only standalone counts may set
        # requested_count, otherwise completion math demands 123 verifications.
        id_like = set(re.findall(
            r"\b(?:channel|role|user|member|message|msg|id)\s+(\d+)\b", low))
        numbers = [n for n in re.findall(r"\b(\d+)\b", low) if n not in id_like]
        # Snowflake IDs (Discord object references/mentions) are not quantities
        numbers = [n for n in numbers if int(n) <= 999]
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
