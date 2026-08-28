import asyncio
import time
from collections import Counter
from typing import Dict

from data_manager import dm
from logger import logger

from .event_bus import Event


class AnalyticsCollector:
    """
    Unified analytics: subscribes to the internal event bus and maintains a
    single shared per-guild metric stream (no redundant databases). Counters
    are flushed periodically into the existing guild data store.
    """

    TRACKED = (
        "message.created", "member.joined", "member.left",
        "moderation.action", "ticket.created", "ticket.closed",
        "automod.violation", "ai.request", "command.executed",
        "action.verified",   # only EXECUTED + VERIFIED agent actions count
        "action.unverified",
    )

    FLUSH_INTERVAL = 300  # 5 minutes

    def __init__(self, event_bus):
        self.bus = event_bus
        self._counters: Dict[int, Counter] = {}
        self._dirty = False

    def start(self):
        for name in self.TRACKED:
            self.bus.subscribe(name, self._on_event)
        self.bus.subscribe("analytics.*", self._on_event)

    def _on_event(self, event: Event):
        guild_id = event.payload.get("guild_id")
        if guild_id is None:
            return
        counter = self._counters.setdefault(int(guild_id), Counter())
        counter[f"{event.name}:{time.strftime('%Y-%m-%d', time.gmtime())}"] += 1
        self._dirty = True

    async def flush_loop(self):
        while True:
            await asyncio.sleep(self.FLUSH_INTERVAL)
            try:
                await self.flush()
            except Exception as e:
                logger.error(f"Analytics flush failed: {e}")

    async def flush(self):
        for guild_id, counter in list(self._counters.items()):
            if not counter:
                continue
            stored = dm.get_guild_data(guild_id, "analytics_stream", {})
            for key, value in counter.items():
                stored[key] = stored.get(key, 0) + value
            # keep the stream bounded: retain the most recent 2000 metric keys
            if len(stored) > 2000:
                stored = dict(sorted(stored.items(), key=lambda kv: kv[0])[-2000:])
            dm.update_guild_data(guild_id, "analytics_stream", stored)
            counter.clear()
        self._dirty = False

    def summary(self, guild_id: int, limit: int = 15) -> dict:
        stored = dm.get_guild_data(guild_id, "analytics_stream", {})
        return dict(sorted(stored.items(), key=lambda kv: kv[1], reverse=True)[:limit])
