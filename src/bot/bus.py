"""Partitioned async event bus with backpressure and dead letter queue."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Type
from uuid import uuid4

from src.bot.errors import BackpressureError, HandlerTimeoutError
from src.bot.logging import get_component_logger
from src.bot.settings import EventBusConfig

logger = get_component_logger("event_bus")


@dataclass
class Event:
    """Base class for all events."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp_ms: int = field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    market_id: str | None = None  # For partitioning

    def partition_key(self) -> str:
        """Determine which queue this event goes to."""
        return self.market_id or "__global__"


@dataclass
class FailedEvent:
    """An event that failed processing after max retries."""

    event: Event
    handler_name: str
    error_message: str
    failed_at: datetime
    attempt_count: int
    partition_key: str


EventHandler = Callable[[Event], Awaitable[None]]


class PartitionedEventBus:
    """
    Async event bus with per-market partitioning.
    
    Features:
    - Per-market queues for ordering guarantees
    - Configurable backpressure handling
    - Dead letter queue for failed events
    - Retry with exponential backoff
    """

    def __init__(self, config: EventBusConfig):
        self._config = config
        self._market_queues: dict[str, asyncio.Queue[Event]] = {}
        self._global_queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=config.max_queue_size)
        self._handlers: dict[Type[Event], list[tuple[int, EventHandler]]] = defaultdict(list)
        self._dlq: asyncio.Queue[FailedEvent] = asyncio.Queue()
        self._running = False
        self._consumer_tasks: list[asyncio.Task[None]] = []

    def _get_or_create_queue(self, partition_key: str) -> asyncio.Queue[Event]:
        """Get or create a queue for a partition."""
        if partition_key == "__global__":
            return self._global_queue

        if partition_key not in self._market_queues:
            self._market_queues[partition_key] = asyncio.Queue(
                maxsize=self._config.max_queue_size
            )

        return self._market_queues[partition_key]

    async def publish(self, event: Event) -> bool:
        """
        Publish an event to the appropriate queue.
        
        Returns:
            True if accepted, False if dropped
        """
        partition_key = event.partition_key()
        queue = self._get_or_create_queue(partition_key)

        # Backpressure handling
        if queue.full():
            match self._config.overflow_policy:
                case "drop":
                    logger.warning(
                        "Event dropped due to backpressure",
                        event_type="event_dropped",
                        partition=partition_key,
                        event_class=type(event).__name__,
                    )
                    return False

                case "coalesce":
                    # Try to coalesce with existing event
                    coalesced = await self._try_coalesce(queue, event)
                    if coalesced:
                        return True
                    # Fall through to drop
                    logger.warning(
                        "Event dropped (could not coalesce)",
                        event_type="event_dropped",
                        partition=partition_key,
                        event_class=type(event).__name__,
                    )
                    return False

                case "block":
                    # Will block until space available
                    await queue.put(event)
                    return True

                case "halt":
                    raise BackpressureError(partition_key)

                case _:
                    # Default to drop
                    return False

        await queue.put(event)
        logger.debug(
            "Event published",
            event_type="event_published",
            partition=partition_key,
            event_class=type(event).__name__,
            event_id=event.event_id,
        )
        return True

    async def _try_coalesce(self, queue: asyncio.Queue[Event], new_event: Event) -> bool:
        """
        Try to coalesce new event with existing one of same type.
        Only works for certain event types (e.g., orderbook updates).
        """
        # For now, simple implementation that doesn't coalesce
        # A full implementation would drain the queue, replace, and re-add
        return False

    def subscribe(
        self,
        event_type: Type[Event],
        handler: EventHandler,
        priority: int = 0,
    ) -> None:
        """
        Register a handler for an event type.
        
        Higher priority handlers execute first.
        """
        self._handlers[event_type].append((priority, handler))
        # Sort by priority (descending)
        self._handlers[event_type].sort(key=lambda x: -x[0])

        logger.info(
            "Handler registered",
            event_type="handler_registered",
            event_class=event_type.__name__,
            handler=handler.__name__,
            priority=priority,
        )

    def unsubscribe(
        self,
        event_type: Type[Event],
        handler: EventHandler,
    ) -> None:
        """Remove a handler."""
        self._handlers[event_type] = [
            (p, h) for p, h in self._handlers[event_type] if h != handler
        ]

    async def start(self) -> None:
        """Start the event processing loops."""
        if self._running:
            return

        self._running = True

        # Start consumer for global queue
        task = asyncio.create_task(
            self._consume_queue("__global__", self._global_queue)
        )
        self._consumer_tasks.append(task)

        # Start consumers for existing market queues
        for partition_key, queue in self._market_queues.items():
            task = asyncio.create_task(self._consume_queue(partition_key, queue))
            self._consumer_tasks.append(task)

        logger.info("Event bus started", event_type="bus_started")

    async def stop(self) -> None:
        """Gracefully stop processing."""
        self._running = False

        # Cancel all consumer tasks
        for task in self._consumer_tasks:
            task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
        self._consumer_tasks.clear()

        logger.info("Event bus stopped", event_type="bus_stopped")

    async def _consume_queue(self, partition_key: str, queue: asyncio.Queue[Event]) -> None:
        """Process events from a single queue in order."""
        while self._running:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            await self._dispatch(event, partition_key)
            queue.task_done()

    async def _dispatch(self, event: Event, partition_key: str) -> None:
        """Dispatch event to all registered handlers with retry."""
        event_type = type(event)
        handlers = list(self._handlers.get(event_type, []))

        # Also get handlers for base classes
        for base in event_type.__mro__[1:]:
            if base in self._handlers:
                handlers.extend(self._handlers[base])

        # Deduplicate while preserving priority order
        seen: set[EventHandler] = set()
        unique_handlers: list[tuple[int, EventHandler]] = []
        for priority, handler in sorted(handlers, key=lambda x: -x[0]):
            if handler not in seen:
                seen.add(handler)
                unique_handlers.append((priority, handler))

        for _, handler in unique_handlers:
            success = await self._invoke_handler(event, handler, partition_key)
            if not success:
                # Handler exhausted retries, continue to next handler
                pass

    async def _invoke_handler(
        self,
        event: Event,
        handler: EventHandler,
        partition_key: str,
    ) -> bool:
        """
        Invoke handler with timeout and retry.
        
        Returns:
            True if successful, False if exhausted retries
        """
        for attempt in range(self._config.max_retry_attempts):
            try:
                await asyncio.wait_for(
                    handler(event),
                    timeout=self._config.handler_timeout_ms / 1000,
                )
                return True

            except asyncio.TimeoutError:
                logger.warning(
                    "Handler timeout",
                    event_type="handler_timeout",
                    handler=handler.__name__,
                    event_id=event.event_id,
                    attempt=attempt + 1,
                )

            except asyncio.CancelledError:
                raise

            except Exception as e:
                logger.error(
                    "Handler error",
                    event_type="handler_error",
                    handler=handler.__name__,
                    event_id=event.event_id,
                    attempt=attempt + 1,
                    error=str(e),
                    exc_info=True,
                )

            # Exponential backoff before retry
            if attempt < self._config.max_retry_attempts - 1:
                delay = self._config.retry_base_delay_ms * (2**attempt) / 1000
                await asyncio.sleep(delay)

        # Exhausted retries - send to DLQ
        await self._send_to_dlq(event, handler, partition_key)
        return False

    async def _send_to_dlq(
        self,
        event: Event,
        handler: EventHandler,
        partition_key: str,
    ) -> None:
        """Send failed event to dead letter queue."""
        failed = FailedEvent(
            event=event,
            handler_name=handler.__name__,
            error_message="Exhausted retries",
            failed_at=datetime.now(timezone.utc),
            attempt_count=self._config.max_retry_attempts,
            partition_key=partition_key,
        )

        await self._dlq.put(failed)

        logger.error(
            "Event sent to DLQ",
            event_type="event_to_dlq",
            event_id=event.event_id,
            event_class=type(event).__name__,
            handler=handler.__name__,
        )

    async def get_dlq_events(self) -> list[FailedEvent]:
        """Get all events from DLQ (for manual inspection)."""
        events: list[FailedEvent] = []
        while not self._dlq.empty():
            try:
                events.append(self._dlq.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def replay_dlq_event(self, failed: FailedEvent) -> bool:
        """Attempt to reprocess a DLQ event."""
        return await self.publish(failed.event)

    @property
    def queue_depths(self) -> dict[str, int]:
        """Get current queue depths for monitoring."""
        depths = {"__global__": self._global_queue.qsize()}
        for key, queue in self._market_queues.items():
            depths[key] = queue.qsize()
        return depths

    @property
    def dlq_size(self) -> int:
        """Get DLQ size for monitoring."""
        return self._dlq.qsize()


# =============================================================================
# Concrete Event Types
# =============================================================================


@dataclass
class ClockTick(Event):
    """Periodic heartbeat event."""

    tick_number: int = 0

    def partition_key(self) -> str:
        return "__global__"


@dataclass
class SystemHalt(Event):
    """Global trading halt signal."""

    reason: str = ""
    triggered_by: str = ""  # "kill_switch" | "manual" | "error"

    def partition_key(self) -> str:
        return "__global__"


# Global event bus instance
_bus: PartitionedEventBus | None = None


def get_event_bus() -> PartitionedEventBus:
    """Get the global event bus instance."""
    global _bus
    if _bus is None:
        from src.bot.settings import get_settings
        _bus = PartitionedEventBus(get_settings().event_bus)
    return _bus


def set_event_bus(bus: PartitionedEventBus) -> None:
    """Set the global event bus instance (for testing)."""
    global _bus
    _bus = bus
