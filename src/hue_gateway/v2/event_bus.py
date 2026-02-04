from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2EventItem:
    cursor: int
    event: dict[str, Any]


@dataclass(frozen=True)
class V2Subscription:
    queue: "asyncio.Queue[V2EventItem]"
    unsubscribe: callable


class V2EventBus:
    def __init__(self, *, replay_maxlen: int = 500) -> None:
        self._cursor = 0
        self._replay: deque[V2EventItem] = deque(maxlen=max(1, int(replay_maxlen)))
        self._subscribers: set[asyncio.Queue[V2EventItem]] = set()
        self._lock = asyncio.Lock()

    @property
    def cursor(self) -> int:
        return self._cursor

    async def subscribe(self) -> V2Subscription:
        queue: asyncio.Queue[V2EventItem] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.add(queue)

        async def _unsubscribe() -> None:
            async with self._lock:
                self._subscribers.discard(queue)

        return V2Subscription(queue=queue, unsubscribe=_unsubscribe)

    async def publish(self, event: dict[str, Any]) -> V2EventItem:
        async with self._lock:
            self._cursor += 1
            item = V2EventItem(cursor=self._cursor, event=event)
            self._replay.append(item)
            subscribers = list(self._subscribers)

        for q in subscribers:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass
        return item

    async def allocate_cursor(self) -> int:
        async with self._lock:
            self._cursor += 1
            return self._cursor

    async def replay_from(self, last_cursor: int) -> list[V2EventItem] | None:
        """
        Return events with cursor > last_cursor, if last_cursor is still in the replay buffer.
        If last_cursor is too old (not in buffer) but buffer is non-empty, return None.
        If buffer is empty, return [].
        """
        async with self._lock:
            items = list(self._replay)

        if not items:
            return None if last_cursor > 0 else []
        if last_cursor <= 0:
            return items

        cursors = {it.cursor for it in items}
        if last_cursor not in cursors:
            return None
        return [it for it in items if it.cursor > last_cursor]
