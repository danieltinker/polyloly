"""Clock utilities for monotonic time and wall clock."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Timestamp:
    """A point in time with both wall clock and monotonic time."""

    wall_time: datetime
    monotonic_ns: int

    @classmethod
    def now(cls) -> Timestamp:
        """Get current timestamp."""
        return cls(
            wall_time=datetime.now(timezone.utc),
            monotonic_ns=time.monotonic_ns(),
        )

    @property
    def wall_time_ms(self) -> int:
        """Wall time as milliseconds since epoch."""
        return int(self.wall_time.timestamp() * 1000)

    @property
    def monotonic_ms(self) -> float:
        """Monotonic time in milliseconds."""
        return self.monotonic_ns / 1_000_000

    def elapsed_since(self, other: Timestamp) -> float:
        """Elapsed time in seconds since another timestamp (monotonic)."""
        return (self.monotonic_ns - other.monotonic_ns) / 1_000_000_000

    def elapsed_ms_since(self, other: Timestamp) -> float:
        """Elapsed time in milliseconds since another timestamp (monotonic)."""
        return (self.monotonic_ns - other.monotonic_ns) / 1_000_000


class Clock:
    """
    Clock utility providing consistent time access.
    
    Uses monotonic time for duration calculations and wall clock for timestamps.
    Can be mocked for testing by subclassing.
    """

    def __init__(self) -> None:
        self._start = Timestamp.now()

    def now(self) -> Timestamp:
        """Get current timestamp."""
        return Timestamp.now()

    def now_ms(self) -> int:
        """Get current wall time as milliseconds since epoch."""
        return int(time.time() * 1000)

    def now_utc(self) -> datetime:
        """Get current UTC datetime."""
        return datetime.now(timezone.utc)

    def monotonic_ns(self) -> int:
        """Get monotonic nanoseconds (for duration calculations)."""
        return time.monotonic_ns()

    def monotonic_ms(self) -> float:
        """Get monotonic milliseconds."""
        return time.monotonic_ns() / 1_000_000

    def elapsed_since_start(self) -> float:
        """Seconds elapsed since clock was created."""
        return self.now().elapsed_since(self._start)

    def elapsed_since_start_ms(self) -> float:
        """Milliseconds elapsed since clock was created."""
        return self.now().elapsed_ms_since(self._start)


class MockClock(Clock):
    """
    Mock clock for testing.
    
    Allows manual control of time for deterministic tests.
    """

    def __init__(self, initial_time: datetime | None = None) -> None:
        self._wall_time = initial_time or datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self._monotonic_ns = 0
        self._start = Timestamp(
            wall_time=self._wall_time,
            monotonic_ns=self._monotonic_ns,
        )

    def now(self) -> Timestamp:
        """Get current (mocked) timestamp."""
        return Timestamp(
            wall_time=self._wall_time,
            monotonic_ns=self._monotonic_ns,
        )

    def now_ms(self) -> int:
        """Get current (mocked) wall time as milliseconds."""
        return int(self._wall_time.timestamp() * 1000)

    def now_utc(self) -> datetime:
        """Get current (mocked) UTC datetime."""
        return self._wall_time

    def monotonic_ns(self) -> int:
        """Get (mocked) monotonic nanoseconds."""
        return self._monotonic_ns

    def monotonic_ms(self) -> float:
        """Get (mocked) monotonic milliseconds."""
        return self._monotonic_ns / 1_000_000

    def advance(self, seconds: float) -> None:
        """Advance time by the given number of seconds."""
        from datetime import timedelta

        self._wall_time += timedelta(seconds=seconds)
        self._monotonic_ns += int(seconds * 1_000_000_000)

    def advance_ms(self, milliseconds: float) -> None:
        """Advance time by the given number of milliseconds."""
        self.advance(milliseconds / 1000)

    def set_time(self, wall_time: datetime) -> None:
        """Set the wall clock to a specific time."""
        diff = (wall_time - self._wall_time).total_seconds()
        self._wall_time = wall_time
        self._monotonic_ns += int(diff * 1_000_000_000)


# Global clock instance (can be replaced with MockClock in tests)
_clock: Clock = Clock()


def get_clock() -> Clock:
    """Get the global clock instance."""
    return _clock


def set_clock(clock: Clock) -> None:
    """Set the global clock instance (for testing)."""
    global _clock
    _clock = clock


def now() -> Timestamp:
    """Get current timestamp using global clock."""
    return _clock.now()


def now_ms() -> int:
    """Get current time in milliseconds using global clock."""
    return _clock.now_ms()


def now_utc() -> datetime:
    """Get current UTC datetime using global clock."""
    return _clock.now_utc()
