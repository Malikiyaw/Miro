"""SecretRedactor and ToolSecurityPolicy — sections 58-60."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# Common secret-shaped patterns.
_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("discord_bot_token", re.compile(r"[MN][A-Za-z\d]{23,}\.[A-Za-z\d_-]{6,}\.[A-Za-z\d_-]{27,}")),
    ("discord_webhook_token", re.compile(r"(?P<url>https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+)")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")),
    ("api_key", re.compile(r"(?i)(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{16,})['\"]?")),
    ("auth_header", re.compile(r"(?i)(authorization|x-api-key|x-auth-token)\s*:\s*[^\s,;]+")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
]


@dataclass
class SecretRedactor:
    """Replaces secret-shaped substrings with `[REDACTED:<kind>]`.

    Operates on dicts/lists/strings recursively. Stable on bytes/None.
    """

    enabled: bool = True
    _redactions: int = 0

    def redact(self, value: Any) -> Any:
        if not self.enabled:
            return value
        if isinstance(value, str):
            return self._redact_str(value)
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.redact(v) for v in value)
        return value

    def _redact_str(self, s: str) -> str:
        # Standalone opaque tokens (>= 16 chars, alphanumeric+underscore+hyphen).
        if re.match(r"^[A-Za-z0-9_\-]{16,}$", s):
            return f"[REDACTED:token] {s[:4]}…"
        out = s
        for kind, pat in _PATTERNS:
            new = pat.sub(f"[REDACTED:{kind}]", out)
            if new != out:
                self._redactions += out.count("[REDACTED") if "[REDACTED" in out else \
                    (out != new)
            out = new
        return out

    def count(self) -> int:
        return self._redactions


class ToolSecurityPolicy:
    """Static policy enforced on the tool layer regardless of caller.

    The LLM cannot:
      - bypass permissions
      - bypass confirmation
      - manufacture ids
      - disable audit
      - suppress verification
      - call discord.py directly
      - modify tool definitions
      - modify system policies
    """

    def __init__(self) -> None:
        self.audit_enabled: bool = True
        self.verification_required: bool = True
        self.idempotency_enforced: bool = True
        self.allow_bypass_permissions: bool = False
        self.allow_bypass_confirmation: bool = False
        self.allow_audit_disable: bool = False
        self.allow_verification_suppress: bool = False
        self.allow_definition_mutation: bool = False
        self.allow_policy_mutation: bool = False
        self.allow_direct_discord: bool = False

    def assert_can_call(self, *, permissions_ok: bool, confirmation_ok: bool) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if not permissions_ok and not self.allow_bypass_permissions:
            reasons.append("permission_denied")
        if not confirmation_ok and not self.allow_bypass_confirmation:
            reasons.append("confirmation_required")
        return (len(reasons) == 0, reasons)

    def assert_audit(self) -> bool:
        return self.audit_enabled and not self.allow_audit_disable

    def assert_verification(self) -> bool:
        return self.verification_required and not self.allow_verification_suppress
