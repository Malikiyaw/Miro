import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Deque, Dict, List, Optional

from logger import logger

SOURCE_COMMAND = "command"
SOURCE_AI = "ai"
SOURCE_AUTOMOD = "automod"
SOURCE_SYSTEM = "system"
SOURCE_SCHEDULED = "scheduled"


@dataclass
class AuditEvent:
    """Unified audit record for any state-changing operation."""
    action: str
    actor_id: Optional[int] = None
    target: Optional[str] = None
    guild_id: Optional[int] = None
    source: str = SOURCE_SYSTEM
    success: bool = True
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEvent":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class AuditLog:
    """
    Central audit adapter. Persists events as JSONL (one file per day) under
    data/audit/ and keeps an in-memory ring buffer for fast recent queries.
    Existing logging systems keep working; this only unifies the record format.
    """

    def __init__(self, directory: str = "data/audit", memory_size: int = 1000):
        self.directory = directory
        self._memory: Deque[dict] = deque(maxlen=memory_size)
        self._lock = threading.Lock()
        os.makedirs(directory, exist_ok=True)

    def record(self, event: AuditEvent):
        """Store an audit event (memory + JSONL). Never raises."""
        entry = event.to_dict()
        with self._lock:
            self._memory.append(entry)
        try:
            day = time.strftime("%Y-%m-%d", time.gmtime(event.timestamp))
            path = os.path.join(self.directory, f"audit-{day}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Audit persistence failed: {e}")

    def record_action(self, action: str, actor_id=None, target=None, guild_id=None,
                      source: str = SOURCE_SYSTEM, success: bool = True, **metadata):
        self.record(AuditEvent(
            action=action, actor_id=actor_id, target=target, guild_id=guild_id,
            source=source, success=success, metadata=metadata,
        ))

    def get_recent(self, limit: int = 50, guild_id: int = None, source: str = None) -> List[dict]:
        """Most recent audit entries, newest first, optionally filtered."""
        with self._lock:
            entries = list(self._memory)
        entries.reverse()
        if guild_id is not None:
            entries = [e for e in entries if e.get("guild_id") == guild_id]
        if source is not None:
            entries = [e for e in entries if e.get("source") == source]
        return entries[:limit]
