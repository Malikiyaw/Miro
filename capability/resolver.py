"""Parameter Resolution Engine — sections 6, 7, 35, 36 of MIRO V11.

Never let the LLM manufacture IDs. The resolver turns a user-friendly
target (channel name, member mention, role label) into a Discord id
with a confidence score and provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class ResolutionSource(str, Enum):
    DISCORD_LOOKUP = "discord_lookup"
    CONFIG = "config"
    TOOL_RESULT = "tool_result"
    USER_SUPPLIED = "user_supplied"


@dataclass
class IdProvenance:
    """How a resolved id was obtained. Destructive actions prefer live lookup."""
    id: str
    source: ResolutionSource
    confidence: float
    label: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def is_trusted(self) -> bool:
        return self.source in (ResolutionSource.DISCORD_LOOKUP, ResolutionSource.CONFIG) \
            and self.confidence >= 0.8


@dataclass
class ResolutionResult:
    resolved: bool
    id: Optional[str] = None
    confidence: float = 0.0
    candidates: List[IdProvenance] = field(default_factory=list)
    reason: str = ""

    def is_confident(self, threshold: float = 0.85) -> bool:
        return self.resolved and self.confidence >= threshold


class Resolver:
    """Name → id resolver for channels, members, roles.

    The resolver is intentionally simple in this build. It is a registry
    of object-lists so the test suite can drive it deterministically; the
    real Discord adapter plugs into the same surface.
    """

    CONFIDENCE_THRESHOLD = 0.85

    def __init__(self, *, threshold: float = CONFIDENCE_THRESHOLD) -> None:
        self._channels: Dict[str, Tuple[str, str]] = {}  # name → (id, guild_id)
        self._members: Dict[str, Tuple[str, str]] = {}  # name → (id, guild_id)
        self._roles: Dict[str, Tuple[str, str]] = {}  # name → (id, guild_id)
        self._guild_to_channels: Dict[str, Dict[str, str]] = {}
        self._guild_to_members: Dict[str, Dict[str, str]] = {}
        self._guild_to_roles: Dict[str, Dict[str, str]] = {}
        self.threshold = threshold

    # -- ingest --
    def add_channel(self, guild_id: str, channel_id: str, name: str) -> None:
        self._channels[self._key(name, guild_id)] = (str(channel_id), str(guild_id))
        self._guild_to_channels.setdefault(str(guild_id), {})[name] = str(channel_id)

    def add_member(self, guild_id: str, member_id: str, name: str) -> None:
        self._members[self._key(name, guild_id)] = (str(member_id), str(guild_id))
        self._guild_to_members.setdefault(str(guild_id), {})[name] = str(member_id)

    def add_role(self, guild_id: str, role_id: str, name: str) -> None:
        self._roles[self._key(name, guild_id)] = (str(role_id), str(guild_id))
        self._guild_to_roles.setdefault(str(guild_id), {})[name] = str(role_id)

    # -- resolve --
    def resolve_channel(self, query: str, guild_id: Optional[str] = None) -> ResolutionResult:
        return self._resolve(query, guild_id, "channel",
                             self._channels, self._guild_to_channels)

    def resolve_member(self, query: str, guild_id: Optional[str] = None) -> ResolutionResult:
        return self._resolve(query, guild_id, "member",
                             self._members, self._guild_to_members)

    def resolve_role(self, query: str, guild_id: Optional[str] = None) -> ResolutionResult:
        return self._resolve(query, guild_id, "role",
                             self._roles, self._guild_to_roles)

    # -- helpers --
    def _key(self, name: str, guild_id: Optional[str]) -> str:
        return f"{guild_id or '*'}::{name.lower()}"

    def _resolve(
        self,
        query: str,
        guild_id: Optional[str],
        kind: str,
        global_index: Mapping[str, Tuple[str, str]],
        guild_index: Mapping[str, Mapping[str, str]],
    ) -> ResolutionResult:
        if query is None:
            return ResolutionResult(resolved=False, reason=f"{kind} query is None")
        q = str(query).strip()
        if not q:
            return ResolutionResult(resolved=False, reason=f"{kind} query is empty")
        # 1. Direct id: digits only → use as-is, mark USER_SUPPLIED.
        if q.isdigit():
            return ResolutionResult(
                resolved=True,
                id=q,
                confidence=0.95,
                candidates=[IdProvenance(id=q, source=ResolutionSource.USER_SUPPLIED,
                                         confidence=0.95, label=q)],
                reason="user-supplied id",
            )
        # 2. Case-insensitive scan. "foo" must match "foo" only, not "foobar".
        cands: List[IdProvenance] = []
        ql = q.lower()
        for (k, (cid, gid)) in global_index.items():
            if not k.startswith(f"{guild_id or '*'}::"):
                continue
            label = k.split("::", 1)[1]
            if label == ql:
                cands.append(IdProvenance(id=cid, source=ResolutionSource.DISCORD_LOOKUP,
                                          confidence=1.0, label=label))
            elif ql in label:
                cands.append(IdProvenance(id=cid, source=ResolutionSource.DISCORD_LOOKUP,
                                          confidence=0.7, label=label))
        if not cands:
            return ResolutionResult(resolved=False, reason=f"no {kind} matches {q!r}")
        # If there's exactly one candidate at all, return it.
        if len(cands) == 1:
            c = cands[0]
            return ResolutionResult(resolved=True, id=c.id, confidence=c.confidence,
                                    candidates=cands, reason="unique match")
        # Multiple candidates: any case with more than one candidate is ambiguous
        # — the user may have intended the partial match. The runtime will ask
        # for clarification instead of guessing on a destructive target.
        cands.sort(key=lambda c: -c.confidence)
        return ResolutionResult(resolved=False, reason=f"{len(cands)} ambiguous {kind} matches",
                                candidates=cands)
