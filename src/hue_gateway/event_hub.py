from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Subscription:
    queue: "asyncio.Queue[dict[str, Any]]"
    unsubscribe: callable


class EventHub:
    def __init__(self, *, max_queue_size: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> Subscription:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(queue)

        async def _unsubscribe() -> None:
            async with self._lock:
                self._subscribers.discard(queue)

        return Subscription(queue=queue, unsubscribe=_unsubscribe)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

