"""Custom exceptions for PolyLOL."""

from __future__ import annotations


class PolyLOLError(Exception):
    """Base exception for all PolyLOL errors."""

    pass


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigError(PolyLOLError):
    """Configuration-related error."""

    pass


class MissingConfigError(ConfigError):
    """Required configuration is missing."""

    def __init__(self, key: str):
        self.key = key
        super().__init__(f"Missing required configuration: {key}")


class InvalidConfigError(ConfigError):
    """Configuration value is invalid."""

    def __init__(self, key: str, value: object, reason: str):
        self.key = key
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid configuration for {key}={value}: {reason}")


# =============================================================================
# Trading Errors
# =============================================================================


class TradingError(PolyLOLError):
    """Trading-related error."""

    pass


class OrderRejectedError(TradingError):
    """Order was rejected by the exchange."""

    def __init__(self, reason: str, order_id: str | None = None):
        self.reason = reason
        self.order_id = order_id
        super().__init__(f"Order rejected: {reason}")


class OrderTimeoutError(TradingError):
    """Order timed out waiting for confirmation."""

    def __init__(self, order_id: str, timeout_ms: float):
        self.order_id = order_id
        self.timeout_ms = timeout_ms
        super().__init__(f"Order {order_id} timed out after {timeout_ms}ms")


class InsufficientLiquidityError(TradingError):
    """Not enough liquidity to fill the order."""

    def __init__(self, market_id: str, required: float, available: float):
        self.market_id = market_id
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient liquidity in {market_id}: need {required}, have {available}"
        )


class SlippageExceededError(TradingError):
    """Slippage exceeds allowed threshold."""

    def __init__(self, expected_bps: float, max_bps: float):
        self.expected_bps = expected_bps
        self.max_bps = max_bps
        super().__init__(
            f"Slippage {expected_bps:.1f}bps exceeds max {max_bps:.1f}bps"
        )


# =============================================================================
# Risk Errors
# =============================================================================


class RiskError(PolyLOLError):
    """Risk management error."""

    pass


class RiskLimitExceededError(RiskError):
    """A risk limit was exceeded."""

    def __init__(self, limit_name: str, current: float, max_allowed: float):
        self.limit_name = limit_name
        self.current = current
        self.max_allowed = max_allowed
        super().__init__(
            f"Risk limit exceeded: {limit_name} is {current}, max is {max_allowed}"
        )


class KillSwitchActiveError(RiskError):
    """Trading halted due to kill switch."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Kill switch active: {reason}")


class CircuitBreakerOpenError(RiskError):
    """Circuit breaker is open for this market."""

    def __init__(self, market_id: str, reason: str):
        self.market_id = market_id
        self.reason = reason
        super().__init__(f"Circuit breaker open for {market_id}: {reason}")


# =============================================================================
# Adapter Errors
# =============================================================================


class AdapterError(PolyLOLError):
    """External adapter error."""

    pass


class ConnectionError(AdapterError):
    """Failed to connect to external service."""

    def __init__(self, service: str, reason: str):
        self.service = service
        self.reason = reason
        super().__init__(f"Failed to connect to {service}: {reason}")


class RateLimitError(AdapterError):
    """Rate limit exceeded for external service."""

    def __init__(self, service: str, retry_after_sec: float | None = None):
        self.service = service
        self.retry_after_sec = retry_after_sec
        msg = f"Rate limit exceeded for {service}"
        if retry_after_sec:
            msg += f", retry after {retry_after_sec}s"
        super().__init__(msg)


class ApiError(AdapterError):
    """API returned an error response."""

    def __init__(self, service: str, status_code: int, message: str):
        self.service = service
        self.status_code = status_code
        self.message = message
        super().__init__(f"{service} API error {status_code}: {message}")


# =============================================================================
# Event Bus Errors
# =============================================================================


class EventBusError(PolyLOLError):
    """Event bus error."""

    pass


class BackpressureError(EventBusError):
    """Event bus queue is full and backpressure policy is 'halt'."""

    def __init__(self, partition: str):
        self.partition = partition
        super().__init__(f"Event bus backpressure on partition: {partition}")


class HandlerTimeoutError(EventBusError):
    """Event handler timed out."""

    def __init__(self, handler_name: str, timeout_ms: float):
        self.handler_name = handler_name
        self.timeout_ms = timeout_ms
        super().__init__(f"Handler {handler_name} timed out after {timeout_ms}ms")


# =============================================================================
# State Machine Errors
# =============================================================================


class StateMachineError(PolyLOLError):
    """State machine error."""

    pass


class InvalidTransitionError(StateMachineError):
    """Invalid state transition attempted."""

    def __init__(self, current_state: str, event: str, allowed_states: list[str]):
        self.current_state = current_state
        self.event = event
        self.allowed_states = allowed_states
        super().__init__(
            f"Invalid transition: cannot process {event} in state {current_state}. "
            f"Allowed from: {allowed_states}"
        )


class DuplicateEventError(StateMachineError):
    """Duplicate event detected."""

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"Duplicate event: {event_id}")
