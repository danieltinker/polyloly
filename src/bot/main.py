"""
PolyLOL - Main Entry Point

Esports Algo-Trader for Polymarket
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, timezone
from uuid import uuid4

from src import __version__
from src.bot.bus import ClockTick, PartitionedEventBus, get_event_bus, set_event_bus
from src.bot.clock import Clock, get_clock
from src.bot.logging import Loggers, set_run_id, setup_logging
from src.bot.settings import Settings, get_settings, load_settings, validate_settings

logger = Loggers.truth_engine()  # Will be replaced with proper logger


class Application:
    """Main application orchestrator."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.run_id = str(uuid4())[:8]
        self.clock = Clock()
        self.bus: PartitionedEventBus | None = None
        self._shutdown_event = asyncio.Event()
        self._tick_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the application."""
        # Set up logging
        setup_logging(
            level=self.settings.log_level,
            json_output=True,
            log_file="logs/bot.jsonl",
        )
        set_run_id(self.run_id)

        logger.info(
            "Starting PolyLOL",
            event_type="startup",
            version=__version__,
            run_id=self.run_id,
            paper_trading=self.settings.execution.paper_trading,
        )

        # Validate settings
        issues = validate_settings(self.settings)
        for issue in issues:
            if issue.startswith("ERROR"):
                logger.error(issue, event_type="config_error")
                if not self.settings.execution.paper_trading:
                    raise SystemExit(1)
            else:
                logger.warning(issue, event_type="config_warning")

        # Initialize event bus
        self.bus = PartitionedEventBus(self.settings.event_bus)
        set_event_bus(self.bus)
        await self.bus.start()

        # Start clock tick loop
        self._tick_task = asyncio.create_task(self._tick_loop())

        logger.info(
            "PolyLOL started successfully",
            event_type="startup_complete",
            run_id=self.run_id,
        )

        # In a full implementation, we would:
        # 1. Start Polymarket WebSocket connections
        # 2. Start esports data adapters
        # 3. Load market mappings
        # 4. Reconcile positions
        # 5. Start strategies

        # For now, just wait for shutdown
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Stop the application gracefully."""
        logger.info("Shutdown initiated", event_type="shutdown_start")

        # Stop tick loop
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass

        # Stop event bus
        if self.bus:
            await self.bus.stop()

        logger.info(
            "Shutdown complete",
            event_type="shutdown_complete",
            run_id=self.run_id,
        )

    async def _tick_loop(self) -> None:
        """Periodic tick for time-based logic."""
        tick_number = 0
        tick_interval = 1.0  # 1 second

        while not self._shutdown_event.is_set():
            try:
                if self.bus:
                    await self.bus.publish(ClockTick(tick_number=tick_number))
                tick_number += 1
                await asyncio.sleep(tick_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Tick loop error",
                    event_type="tick_error",
                    error=str(e),
                )
                await asyncio.sleep(tick_interval)

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self._shutdown_event.set()


def handle_signal(app: Application, sig: signal.Signals) -> None:
    """Handle shutdown signals."""
    logger.info(f"Received signal {sig.name}", event_type="signal_received")
    app.request_shutdown()


async def async_main() -> int:
    """Async main entry point."""
    # Load settings
    settings = load_settings()

    # Create application
    app = Application(settings)

    # Set up signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda s=sig: handle_signal(app, s),
        )

    try:
        await app.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(
            "Fatal error",
            event_type="fatal_error",
            error=str(e),
            exc_info=True,
        )
        return 1
    finally:
        await app.stop()

    return 0


def main() -> None:
    """Main entry point."""
    # Ensure logs directory exists
    from pathlib import Path
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    # Run async main
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
