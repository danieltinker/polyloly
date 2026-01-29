"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.bot.errors import InvalidConfigError, MissingConfigError


@dataclass
class RiskConfig:
    """Risk management configuration."""

    # Per-order limits
    min_order_size: float = 5.0
    max_single_order: float = 100.0
    max_slippage_bps: float = 50.0

    # Per-market limits
    max_position_per_market: float = 1500.0
    max_open_orders_per_market: int = 5
    max_leg_imbalance_usdc: float = 100.0
    max_leg_imbalance_shares: float = 50.0

    # Circuit breaker
    max_consecutive_rejects: int = 3
    max_cancel_failures: int = 3
    max_order_latency_ms: float = 5000.0

    # Correlation
    max_exposure_per_correlation_group: float = 2000.0

    # Global
    max_total_exposure: float = 5000.0
    max_daily_loss: float = 200.0
    max_consecutive_global_errors: int = 5

    # Time-based
    weekend_trading_enabled: bool = False
    peak_hours_size_multiplier: float = 0.5

    # Recovery
    circuit_breaker_recovery_sec: float = 300.0


@dataclass
class PairArbConfig:
    """Pair arbitrage strategy configuration."""

    enabled: bool = True
    pair_cost_cap: float = 0.975
    safety_margin: float = 0.005
    fee_rate: float = 0.02
    step_usdc: float = 25.0
    max_total_cost: float = 1500.0
    max_leg_imbalance_usdc: float = 100.0
    prefer_balance: bool = True
    min_liquidity_usdc: float = 100.0


@dataclass
class TemporalArbConfig:
    """Temporal arbitrage strategy configuration."""

    enabled: bool = False
    min_confidence: float = 0.90
    min_edge_threshold: float = 0.05
    max_entry_price: float = 0.95
    kelly_fraction: float = 0.25
    max_single_stake: float = 50.0
    max_exposure: float = 200.0
    stale_threshold_ms: int = 30000


@dataclass
class TruthEngineConfig:
    """Truth engine configuration."""

    confirm_threshold: float = 0.90
    max_wait_ms: int = 10000
    required_sources_for_final: int = 2
    allowed_skew_ms: int = 2000
    tier_a_sources: list[str] = field(default_factory=lambda: ["grid", "official"])
    tier_b_sources: list[str] = field(default_factory=lambda: ["pandascore", "opendota"])
    tier_c_sources: list[str] = field(default_factory=lambda: ["liquipedia"])


@dataclass
class EventBusConfig:
    """Event bus configuration."""

    max_queue_size: int = 1000
    overflow_policy: str = "drop"  # drop, coalesce, block, halt
    handler_timeout_ms: float = 5000.0
    max_retry_attempts: int = 3
    retry_base_delay_ms: float = 100.0


@dataclass
class ExecutionConfig:
    """Execution layer configuration."""

    max_slippage_bps: float = 50.0
    order_timeout_ms: float = 30000.0
    cancel_timeout_ms: float = 10000.0
    max_retry_attempts: int = 3
    reconcile_interval_sec: float = 60.0
    paper_trading: bool = True


@dataclass
class PolymarketConfig:
    """Polymarket adapter configuration."""

    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    private_key: str = ""
    api_key: str = ""
    rate_limit_per_minute: int = 100


@dataclass
class EsportsConfig:
    """Esports adapters configuration."""

    opendota_enabled: bool = True
    opendota_api_key: str = ""
    opendota_poll_interval_ms: int = 5000

    pandascore_enabled: bool = False
    pandascore_api_key: str = ""

    grid_enabled: bool = False
    grid_api_key: str = ""

    liquipedia_enabled: bool = True


@dataclass
class Settings:
    """Main application settings."""

    # Bot info
    name: str = "polyloly"
    version: str = "0.1.0"
    log_level: str = "INFO"
    health_port: int = 8080

    # Component configs
    risk: RiskConfig = field(default_factory=RiskConfig)
    pair_arb: PairArbConfig = field(default_factory=PairArbConfig)
    temporal_arb: TemporalArbConfig = field(default_factory=TemporalArbConfig)
    truth_engine: TruthEngineConfig = field(default_factory=TruthEngineConfig)
    event_bus: EventBusConfig = field(default_factory=EventBusConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    esports: EsportsConfig = field(default_factory=EsportsConfig)

    # Alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""

    # Admin
    admin_token: str = ""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml_config(config_dir: Path, env: str = "dev") -> dict[str, Any]:
    """Load YAML configuration files."""
    config: dict[str, Any] = {}

    # Load base config
    base_path = config_dir / "base.yaml"
    if base_path.exists():
        with open(base_path) as f:
            config = yaml.safe_load(f) or {}

    # Load environment-specific config
    env_path = config_dir / f"{env}.yaml"
    if env_path.exists():
        with open(env_path) as f:
            env_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, env_config)

    return config


def _get_env(key: str, default: str = "") -> str:
    """Get environment variable with default."""
    return os.environ.get(key, default)


def _get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean environment variable."""
    value = os.environ.get(key, "").lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return default


def _get_env_float(key: str, default: float) -> float:
    """Get float environment variable."""
    value = os.environ.get(key)
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    return default


def _get_env_int(key: str, default: int) -> int:
    """Get int environment variable."""
    value = os.environ.get(key)
    if value:
        try:
            return int(value)
        except ValueError:
            pass
    return default


def load_settings(
    config_dir: Path | str | None = None,
    env: str = "dev",
    dotenv_path: Path | str | None = None,
) -> Settings:
    """
    Load settings from YAML config and environment variables.
    
    Priority (highest to lowest):
    1. Environment variables
    2. Environment-specific YAML (e.g., dev.yaml)
    3. Base YAML (base.yaml)
    4. Defaults
    
    Args:
        config_dir: Path to config directory (default: ./config)
        env: Environment name (dev, prod)
        dotenv_path: Path to .env file (default: ./.env)
        
    Returns:
        Loaded Settings object
    """
    # Load .env file
    if dotenv_path:
        load_dotenv(dotenv_path)
    else:
        load_dotenv()

    # Determine config directory
    if config_dir is None:
        config_dir = Path("config")
    else:
        config_dir = Path(config_dir)

    # Load YAML config
    yaml_config = _load_yaml_config(config_dir, env)

    # Build settings with YAML values as base
    bot_config = yaml_config.get("bot", {})
    risk_config = yaml_config.get("risk", {})
    strategies_config = yaml_config.get("strategies", {})
    truth_config = yaml_config.get("truth_engine", {})
    bus_config = yaml_config.get("event_bus", {})
    exec_config = yaml_config.get("execution", {})
    adapters_config = yaml_config.get("adapters", {})

    # Build component configs
    risk = RiskConfig(
        max_daily_loss=_get_env_float("MAX_DAILY_LOSS", risk_config.get("max_daily_loss", 200.0)),
        max_position_per_market=_get_env_float(
            "MAX_POSITION_PER_MARKET",
            risk_config.get("max_position_per_market", 1500.0),
        ),
        max_total_exposure=_get_env_float(
            "MAX_TOTAL_EXPOSURE",
            risk_config.get("max_total_exposure", 5000.0),
        ),
        **{k: v for k, v in risk_config.items() if k not in [
            "max_daily_loss", "max_position_per_market", "max_total_exposure"
        ]},
    )

    pair_arb_config = strategies_config.get("pair_arb", {})
    pair_arb = PairArbConfig(**pair_arb_config) if pair_arb_config else PairArbConfig()

    temporal_arb_config = strategies_config.get("temporal_arb", {})
    temporal_arb = TemporalArbConfig(**temporal_arb_config) if temporal_arb_config else TemporalArbConfig()

    truth_engine = TruthEngineConfig(**truth_config) if truth_config else TruthEngineConfig()
    event_bus = EventBusConfig(**bus_config) if bus_config else EventBusConfig()

    execution = ExecutionConfig(
        paper_trading=_get_env_bool("PAPER_TRADING", exec_config.get("paper_trading", True)),
        **{k: v for k, v in exec_config.items() if k != "paper_trading"},
    )

    poly_config = adapters_config.get("polymarket", {})
    polymarket = PolymarketConfig(
        host=poly_config.get("host", "https://clob.polymarket.com"),
        chain_id=poly_config.get("chain_id", 137),
        private_key=_get_env("POLYMARKET_PRIVATE_KEY", ""),
        api_key=_get_env("POLYMARKET_API_KEY", ""),
        rate_limit_per_minute=poly_config.get("rate_limit_per_minute", 100),
    )

    esports_config = adapters_config.get("esports", {})
    esports = EsportsConfig(
        opendota_enabled=esports_config.get("opendota", {}).get("enabled", True),
        opendota_api_key=_get_env("OPENDOTA_API_KEY", ""),
        opendota_poll_interval_ms=esports_config.get("opendota", {}).get("poll_interval_ms", 5000),
        pandascore_enabled=esports_config.get("pandascore", {}).get("enabled", False),
        pandascore_api_key=_get_env("PANDASCORE_API_KEY", ""),
        grid_enabled=esports_config.get("grid", {}).get("enabled", False),
        grid_api_key=_get_env("GRID_API_KEY", ""),
        liquipedia_enabled=esports_config.get("liquipedia", {}).get("enabled", True),
    )

    return Settings(
        name=bot_config.get("name", "polyloly"),
        version=bot_config.get("version", "0.1.0"),
        log_level=_get_env("LOG_LEVEL", bot_config.get("log_level", "INFO")),
        health_port=_get_env_int("HEALTH_PORT", bot_config.get("health_port", 8080)),
        risk=risk,
        pair_arb=pair_arb,
        temporal_arb=temporal_arb,
        truth_engine=truth_engine,
        event_bus=event_bus,
        execution=execution,
        polymarket=polymarket,
        esports=esports,
        telegram_bot_token=_get_env("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_get_env("TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=_get_env("DISCORD_WEBHOOK_URL", ""),
        admin_token=_get_env("ADMIN_TOKEN", ""),
    )


def validate_settings(settings: Settings) -> list[str]:
    """
    Validate settings and return list of warnings/errors.
    
    Returns:
        List of validation messages (empty if all OK)
    """
    issues: list[str] = []

    # Check for required secrets in live mode
    if not settings.execution.paper_trading:
        if not settings.polymarket.private_key:
            issues.append("ERROR: POLYMARKET_PRIVATE_KEY required for live trading")

    # Warn about risk limits
    if settings.risk.max_daily_loss > 500:
        issues.append(f"WARNING: High daily loss limit: ${settings.risk.max_daily_loss}")

    if settings.risk.max_total_exposure > 10000:
        issues.append(f"WARNING: High total exposure limit: ${settings.risk.max_total_exposure}")

    # Check strategy consistency
    if settings.pair_arb.pair_cost_cap >= 1.0 - settings.pair_arb.fee_rate:
        issues.append(
            f"ERROR: pair_cost_cap ({settings.pair_arb.pair_cost_cap}) must be < "
            f"1 - fee_rate ({1.0 - settings.pair_arb.fee_rate})"
        )

    return issues


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Set the global settings instance (for testing)."""
    global _settings
    _settings = settings
