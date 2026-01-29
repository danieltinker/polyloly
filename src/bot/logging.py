"""Structured JSON logging for PolyLOL."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

import structlog

# Context variables for correlation IDs
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_market_id: ContextVar[str | None] = ContextVar("market_id", default=None)
_match_id: ContextVar[str | None] = ContextVar("match_id", default=None)
_order_id: ContextVar[str | None] = ContextVar("order_id", default=None)


def set_run_id(run_id: str) -> None:
    """Set the run ID for all subsequent log messages."""
    _run_id.set(run_id)


def set_context(
    market_id: str | None = None,
    match_id: str | None = None,
    order_id: str | None = None,
) -> None:
    """Set context for subsequent log messages."""
    if market_id is not None:
        _market_id.set(market_id)
    if match_id is not None:
        _match_id.set(match_id)
    if order_id is not None:
        _order_id.set(order_id)


def clear_context() -> None:
    """Clear all context variables."""
    _market_id.set(None)
    _match_id.set(None)
    _order_id.set(None)


def add_context_ids(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Processor to add context IDs to log events."""
    run_id = _run_id.get()
    market_id = _market_id.get()
    match_id = _match_id.get()
    order_id = _order_id.get()

    if run_id:
        event_dict["run_id"] = run_id
    if market_id:
        event_dict["market_id"] = market_id
    if match_id:
        event_dict["match_id"] = match_id
    if order_id:
        event_dict["order_id"] = order_id

    return event_dict


def add_timestamp(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Processor to add ISO8601 timestamp."""
    event_dict["ts"] = datetime.now(timezone.utc).isoformat()
    return event_dict


def rename_event_key(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Rename 'event' to 'message' for consistency."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def setup_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_file: str | None = None,
) -> None:
    """
    Configure structured logging for the application.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: If True, output JSON; if False, output human-readable
        log_file: Optional file path to write logs to
    """
    # Set up standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    # Configure processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_timestamp,
        add_context_ids,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # JSON output for production
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            rename_event_key,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Human-readable output for development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set up file handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level.upper()))
        
        # File handler always uses JSON
        file_formatter = logging.Formatter("%(message)s")
        file_handler.setFormatter(file_formatter)
        
        logging.getLogger().addHandler(file_handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


# Convenience function for component-specific loggers
def get_component_logger(component: str) -> structlog.stdlib.BoundLogger:
    """
    Get a logger bound to a specific component.
    
    Args:
        component: Component name (e.g., "truth_engine", "order_manager")
        
    Returns:
        Logger with component field pre-bound
    """
    return structlog.get_logger().bind(component=component)


# Pre-configured loggers for main components
class Loggers:
    """Pre-configured loggers for main components."""

    @staticmethod
    def truth_engine() -> structlog.stdlib.BoundLogger:
        return get_component_logger("truth_engine")

    @staticmethod
    def trading_engine() -> structlog.stdlib.BoundLogger:
        return get_component_logger("trading_engine")

    @staticmethod
    def order_manager() -> structlog.stdlib.BoundLogger:
        return get_component_logger("order_manager")

    @staticmethod
    def risk_manager() -> structlog.stdlib.BoundLogger:
        return get_component_logger("risk_manager")

    @staticmethod
    def pair_arb() -> structlog.stdlib.BoundLogger:
        return get_component_logger("pair_arb")

    @staticmethod
    def temporal_arb() -> structlog.stdlib.BoundLogger:
        return get_component_logger("temporal_arb")

    @staticmethod
    def event_bus() -> structlog.stdlib.BoundLogger:
        return get_component_logger("event_bus")

    @staticmethod
    def polymarket() -> structlog.stdlib.BoundLogger:
        return get_component_logger("polymarket")

    @staticmethod
    def esports() -> structlog.stdlib.BoundLogger:
        return get_component_logger("esports")
