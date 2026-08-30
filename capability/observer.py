"""DiscordObserver, Verifier contracts, error classification, recovery."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ErrorClass, FailureKind, ToolResult


class VerificationOutcome(str, Enum):
    VERIFIED = "verified"
    NOT_FOUND = "not_found"
    MISMATCH = "mismatch"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class Verifier:
    """Per-tool verifier declaration.

    `check` is a callable that takes the action output and a snapshot
    and returns a VerificationOutcome. The runtime polls bounded times.
    """

    name: str
    check: Callable[[Dict[str, Any], Dict[str, Any]], VerificationOutcome]
    max_attempts: int = 3
    interval_seconds: float = 0.25
    applies_to: str = ""


def bounded_poll(
    check: Callable[[], VerificationOutcome],
    *,
    max_attempts: int = 3,
    interval_seconds: float = 0.25,
) -> VerificationOutcome:
    """Poll until the verifier reports VERIFIED, MISMATCH, or attempts are exhausted."""
    for attempt in range(max_attempts):
        outcome = check()
        if outcome in (VerificationOutcome.VERIFIED, VerificationOutcome.MISMATCH,
                       VerificationOutcome.NOT_FOUND):
            return outcome
        if attempt < max_attempts - 1:
            time.sleep(interval_seconds)
    return VerificationOutcome.TIMEOUT


def classify_error(message: str) -> Tuple[ErrorClass, FailureKind]:
    """Normalize a Discord error string into (ErrorClass, FailureKind)."""
    if not message:
        return ErrorClass.UNKNOWN, FailureKind.PERMANENT
    m = message.lower()
    if "missing permissions" in m or "403" in m:
        return ErrorClass.PERMISSION_DENIED, FailureKind.PERMANENT
    if "hierarchy" in m or "50013" in m:
        return ErrorClass.HIERARCHY_ERROR, FailureKind.PERMANENT
    if "not found" in m or "404" in m or "10003" in m or "10004" in m or "10008" in m or "10013" in m:
        return ErrorClass.NOT_FOUND, FailureKind.REPLAN_REQUIRED
    if "already" in m or "conflict" in m or "30007" in m or "30005" in m:
        return ErrorClass.ALREADY_EXISTS, FailureKind.PERMANENT
    if "rate" in m or "429" in m or "global ratelimit" in m:
        return ErrorClass.RATE_LIMITED, FailureKind.TRANSIENT
    if "timeout" in m or "timed out" in m:
        return ErrorClass.TIMEOUT, FailureKind.TRANSIENT
    if "invalid" in m or "400" in m:
        return ErrorClass.INVALID_PARAMS, FailureKind.PERMANENT
    if "network" in m or "connection" in m or "reset" in m:
        return ErrorClass.NETWORK_ERROR, FailureKind.TRANSIENT
    return ErrorClass.UNKNOWN, FailureKind.PERMANENT


class RecoveryAction(str, Enum):
    RETRY = "retry"
    RE_RESOLVE = "re_resolve"
    SKIP = "skip"
    STOP = "stop"
    USER_INPUT = "user_input"


class RecoveryEngine:
    """Decides what to do after a failure based on its classification."""

    def decide(self, message: str) -> RecoveryAction:
        cls, kind = classify_error(message)
        if kind == FailureKind.TRANSIENT:
            return RecoveryAction.RETRY
        if cls == ErrorClass.NOT_FOUND:
            return RecoveryAction.RE_RESOLVE
        if cls == ErrorClass.PERMISSION_DENIED or cls == ErrorClass.HIERARCHY_ERROR:
            return RecoveryAction.STOP
        if cls == ErrorClass.INVALID_PARAMS:
            return RecoveryAction.USER_INPUT
        return RecoveryAction.SKIP


class DiscordObserver:
    """In-memory observer.

    Production plugs a Discord adapter into the same interface. The
    observer maintains a per-guild snapshot of channels, roles, members,
    messages, and perms so the verifier can re-read state.
    """

    def __init__(self) -> None:
        self._channels: Dict[str, Dict[str, Any]] = {}  # channel_id → info
        self._roles: Dict[str, Dict[str, Any]] = {}
        self._members: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (guild, member) → info
        self._messages: Dict[str, Dict[str, Any]] = {}
        self._perms: Dict[Tuple[str, str, str], int] = {}  # (guild, channel, member) → bits
        self._server: Dict[str, Dict[str, Any]] = {}

    # -- ingest --
    def upsert_channel(self, info: Mapping[str, Any]) -> None:
        cid = str(info["id"])
        self._channels[cid] = dict(info)

    def delete_channel(self, channel_id: str) -> None:
        self._channels.pop(str(channel_id), None)

    def upsert_role(self, info: Mapping[str, Any]) -> None:
        rid = str(info["id"])
        self._roles[rid] = dict(info)

    def delete_role(self, role_id: str) -> None:
        self._roles.pop(str(role_id), None)

    def upsert_member(self, guild_id: str, info: Mapping[str, Any]) -> None:
        mid = str(info["id"])
        self._members[(str(guild_id), mid)] = dict(info)

    def remove_member(self, guild_id: str, member_id: str) -> None:
        self._members.pop((str(guild_id), str(member_id)), None)

    def upsert_message(self, info: Mapping[str, Any]) -> None:
        mid = str(info["id"])
        self._messages[mid] = dict(info)

    def delete_message(self, message_id: str) -> None:
        self._messages.pop(str(message_id), None)

    def set_perms(self, guild_id: str, channel_id: str, member_id: str, value: int) -> None:
        self._perms[(str(guild_id), str(channel_id), str(member_id))] = int(value) & 0xFFFFFFFF

    def upsert_server(self, info: Mapping[str, Any]) -> None:
        sid = str(info["id"])
        self._server[sid] = dict(info)

    # -- observe --
    def observe_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        return self._channels.get(str(channel_id))

    def observe_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        return self._roles.get(str(role_id))

    def observe_member(self, guild_id: str, member_id: str) -> Optional[Dict[str, Any]]:
        return self._members.get((str(guild_id), str(member_id)))

    def observe_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        return self._messages.get(str(message_id))

    def observe_permission(self, guild_id: str, channel_id: str, member_id: str) -> Optional[int]:
        return self._perms.get((str(guild_id), str(channel_id), str(member_id)))

    def observe_server(self, guild_id: str) -> Optional[Dict[str, Any]]:
        return self._server.get(str(guild_id))

    # -- built-in verifiers --
    def verify_channel_deleted(self, params: Mapping[str, Any], _) -> VerificationOutcome:
        return VerificationOutcome.NOT_FOUND if not self.observe_channel(str(params["channel_id"])) \
            else VerificationOutcome.MISMATCH

    def verify_channel_exists(self, params: Mapping[str, Any], _) -> VerificationOutcome:
        ch = self.observe_channel(str(params["channel_id"]))
        if not ch:
            return VerificationOutcome.NOT_FOUND
        for key, val in params.items():
            if key in ("channel_id",):
                continue
            if ch.get(key) != val:
                return VerificationOutcome.MISMATCH
        return VerificationOutcome.VERIFIED

    def verify_role_on_member(self, params: Mapping[str, Any], _) -> VerificationOutcome:
        m = self.observe_member(str(params["guild_id"]), str(params["member_id"]))
        if not m:
            return VerificationOutcome.NOT_FOUND
        roles = set(m.get("roles", []))
        return VerificationOutcome.VERIFIED if str(params["role_id"]) in roles \
            else VerificationOutcome.MISMATCH

    def verify_message_exists(self, params: Mapping[str, Any], _) -> VerificationOutcome:
        return VerificationOutcome.VERIFIED if self.observe_message(str(params["message_id"])) \
            else VerificationOutcome.NOT_FOUND

    def verify_message_absent(self, params: Mapping[str, Any], _) -> VerificationOutcome:
        return VerificationOutcome.VERIFIED if not self.observe_message(str(params["message_id"])) \
            else VerificationOutcome.MISMATCH
