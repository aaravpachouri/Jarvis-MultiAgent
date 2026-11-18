"""Low-overhead in-process event bus for task and agent messages.

Subscribers consume their own queues, so a slow dashboard or telemetry reader
cannot block the agent that publishes an event. Queue overflow drops the oldest
telemetry/event item and records the drop count rather than blocking execution.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Iterable

from .contracts import AgentMessage, TaskEvent

BusItem = AgentMessage | TaskEvent


@dataclass(slots=True)
class EventSubscription:
    subscription_id: str
    queue: asyncio.Queue[BusItem]
    event_types: frozenset[str] = frozenset()
    dropped_count: int = 0
    closed: bool = False

    async def get(self, timeout: float | None = None) -> BusItem:
        if self.closed and self.queue.empty():
            raise RuntimeError("Subscription is closed.")
        if timeout is None:
            return await self.queue.get()
        return await asyncio.wait_for(self.queue.get(), timeout=timeout)

    def get_nowait(self) -> BusItem:
        return self.queue.get_nowait()


class EventBus:
    """Non-blocking publish/subscribe for local agents and live UI consumers."""

    def __init__(self, *, default_queue_size: int = 256) -> None:
        if default_queue_size < 1:
            raise ValueError("default_queue_size must be at least 1.")
        self.default_queue_size = default_queue_size
        self._subscriptions: dict[str, EventSubscription] = {}
        self._sequence = 0

    def subscribe(
        self,
        event_types: Iterable[str] | None = None,
        *,
        queue_size: int | None = None,
    ) -> EventSubscription:
        self._sequence += 1
        size = queue_size if queue_size is not None else self.default_queue_size
        if size < 1:
            raise ValueError("queue_size must be at least 1.")
        allowed = frozenset(value.strip() for value in (event_types or ()) if value.strip())
        subscription = EventSubscription(
            subscription_id=f"sub-{self._sequence}",
            queue=asyncio.Queue(maxsize=size),
            event_types=allowed,
        )
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def unsubscribe(self, subscription: EventSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.subscription_id
        existing = self._subscriptions.pop(key, None)
        if existing is not None:
            existing.closed = True

    def publish(self, item: BusItem) -> int:
        """Queue an event for matching subscribers without awaiting consumers.

        Returns the number of subscriptions that received the event.
        """
        event_type = item.event_type if isinstance(item, TaskEvent) else item.message_type
        delivered = 0
        for subscription in tuple(self._subscriptions.values()):
            if subscription.closed:
                continue
            if subscription.event_types and event_type not in subscription.event_types:
                continue
            if subscription.queue.full():
                try:
                    subscription.queue.get_nowait()
                    subscription.dropped_count += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                subscription.queue.put_nowait(item)
                delivered += 1
            except asyncio.QueueFull:
                subscription.dropped_count += 1
        return delivered

    @property
    def subscriber_count(self) -> int:
        return sum(1 for subscription in self._subscriptions.values() if not subscription.closed)
