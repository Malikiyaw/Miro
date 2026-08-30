"""ServerSnapshotter, RollbackRegistry — sections 37-38 of MIRO V11."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .observer import DiscordObserver


@dataclass
class ServerSnapshot:
    guild_id: str
    taken_at: float = field(default_factory=time.time)
    channels: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    roles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    members: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    perms: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    server: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "taken_at": self.taken_at,
            "channels": dict(self.channels),
            "roles": dict(self.roles),
            "members": {f"{g}::{m}": info for (g, m), info in self.members.items()},
            "perms": {f"{g}|{c}|{m}": v for (g, c, m), v in self.perms.items()},
            "server": dict(self.server),
        }


class ServerSnapshotter:
    """Capture a guild snapshot from the observer."""

    def __init__(self, observer: DiscordObserver) -> None:
        self._observer = observer
        self._last: Dict[str, ServerSnapshot] = {}

    def take(self, guild_id: str) -> ServerSnapshot:
        snap = ServerSnapshot(guild_id=str(guild_id))
        for cid, info in list(self._observer._channels.items()):  # noqa: SLF001 (test seam)
            if str(info.get("guild_id", "")) == str(guild_id):
                snap.channels[cid] = info
        for rid, info in list(self._observer._roles.items()):
            snap.roles[rid] = info
        for (gid, mid), info in list(self._observer._members.items()):
            if str(gid) == str(guild_id):
                snap.members[(gid, mid)] = info
        for (gid, cid, mid), v in list(self._observer._perms.items()):
            if str(gid) == str(guild_id):
                snap.perms[(gid, cid, mid)] = v
        server = self._observer.observe_server(str(guild_id))
        if server:
            snap.server = dict(server)
        self._last[str(guild_id)] = snap
        return snap

    def last(self, guild_id: str) -> Optional[ServerSnapshot]:
        return self._last.get(str(guild_id))


@dataclass
class RollbackPlan:
    tool: str
    target_id: str
    before: Dict[str, Any]
    after: Dict[str, Any]
    reversible: bool
    reason: str = ""


class RollbackRegistry:
    """Records per-tool pre-state and provides explicit rollback plans.

    Discord-side rollback depends on the action being declared
    `supports_rollback=True`. Otherwise, the registry records the fact
    that no rollback is possible — the receipt will mark it as such.
    """

    def __init__(self) -> None:
        self._history: List[RollbackPlan] = []

    def record(self, plan: RollbackPlan) -> None:
        self._history.append(plan)

    def pending(self, target_id: Optional[str] = None) -> List[RollbackPlan]:
        if target_id is None:
            return list(self._history)
        return [p for p in self._history if p.target_id == str(target_id)]

    def clear(self) -> None:
        self._history.clear()
