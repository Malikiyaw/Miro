import asyncio
import fnmatch
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from logger import logger


@dataclass
class Event:
    """Standardized internal event (e.g. message.created, member.joined, ticket.closed)."""
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "system"


Handler = Callable[[Event], Any]


class EventBus:
    """
    Lightweight in-process pub/sub decoupling immediate reactive processing
    from feature systems. Handlers run isolated: one failure never affects
    other subscribers or the publisher.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Handler]] = {}
        self._lock = threading.Lock()

    def subscribe(self, pattern: str, handler: Handler):
        """Subscribe to events by exact name or glob pattern ('*' matches all)."""
        with self._lock:
            self._subscribers.setdefault(pattern, []).append(handler)

    def unsubscribe(self, pattern: str, handler: Handler):
        with self._lock:
            handlers = self._subscribers.get(pattern)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def _match(self, name: str) -> List[Handler]:
        matched: List[Handler] = []
        with self._lock:
            for pattern, handlers in list(self._subscribers.items()):
                if pattern == "*" or fnmatch.fnmatchcase(name, pattern):
                    matched.extend(list(handlers))
        return matched

    async def publish(self, event, source: str = "system", **payload):
        """Publish an event; every matching handler runs in its own task."""
        if not isinstance(event, Event):
            event = Event(name=event, payload=payload, source=source)
        for handler in self._match(event.name):
            asyncio.create_task(self._run(handler, event))

    async def publish_and_wait(self, event, source: str = "system", timeout: float = 10.0, **payload):
        """Publish an event and wait for all matching handlers to settle."""
        if not isinstance(event, Event):
            event = Event(name=event, payload=payload, source=source)
        results = await asyncio.gather(
            *(self._run(h, event) for h in self._match(event.name)),
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, Exception)]

    @staticmethod
    async def _run(handler: Handler, event: Event):
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await asyncio.wait_for(result, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(f"Event handler {getattr(handler, '__name__', handler)} timed out on {event.name}")
        except Exception as e:
            logger.error(f"Event handler {getattr(handler, '__name__', handler)} failed on {event.name}: {e}")
