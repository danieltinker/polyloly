# PolyLOL - Polymarket Esports Algo-Trader

An algorithmic trading bot for Polymarket esports prediction markets.

## Overview

PolyLOL implements two primary strategies:

1. **Binary Pair Arbitrage** - Buy YES+NO pairs when combined cost < 0.98 (after fees) for guaranteed profit at resolution
2. **Temporal Arbitrage** - Exploit latency advantage by acting on esports match outcomes faster than the market

## Features

- Event-driven architecture with partitioned queues
- Dual state machines (Truth Engine + Trading Engine)
- Multi-source esports data with tiered confirmation
- Comprehensive risk management with circuit breakers
- Paper trading mode for safe testing

## Project Structure

```
polyloly/
├── config/
│   └── base.yaml           # Configuration
├── src/
│   ├── bot/
│   │   ├── main.py         # Entry point
│   │   ├── bus.py          # Event bus
│   │   ├── settings.py     # Config loading
│   │   ├── logging.py      # Structured logging
│   │   ├── clock.py        # Time utilities
│   │   └── errors.py       # Custom exceptions
│   └── domain/
│       ├── types.py        # Core dataclasses
│       └── engines/
│           ├── truth_engine.py    # Match state machine
│           └── trading_engine.py  # Trading state machine
├── tests/                  # Test suite
├── specs/                  # Design specifications
├── docs/                   # Runbook & ADRs
├── pyproject.toml          # Dependencies
└── .env.example            # Environment template
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- pip or uv package manager

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/polyloly.git
cd polyloly

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### 3. Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API keys
# IMPORTANT: Keep paper_trading=true until you're confident!
```

### 4. Run Tests

```bash
pytest tests/ -v
```

### 5. Run the Bot (Paper Mode)

```bash
# Create required directories
mkdir -p logs data

# Run
python -m src.bot.main
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PAPER_TRADING` | Yes | Set to `true` for paper trading |
| `POLYMARKET_PRIVATE_KEY` | For live | Your wallet private key |
| `OPENDOTA_API_KEY` | No | OpenDota API key (optional) |
| `MAX_DAILY_LOSS` | No | Override daily loss limit |

### Risk Limits (config/base.yaml)

```yaml
risk:
  max_daily_loss: 200.0        # USD
  max_position_per_market: 1500.0
  max_total_exposure: 5000.0
  max_slippage_bps: 50
```

## Architecture

### State Machines

**Truth Engine States:**
```
PRE_MATCH → LIVE ⇄ PAUSED → PENDING_CONFIRM → FINAL
```

**Trading Engine States:**
```
IDLE → BUILDING_PAIR → LOCKED_PAIR → FINALIZING → RESOLVED
         ↓                              ↑
    TEMPORAL_ACTIVE ───────────────────┘
         ↓
       HALT (circuit breaker)
```

### Event Flow

```
Esports APIs → MatchEvent → Truth Engine → TruthDelta/TruthFinal
                                                 ↓
Polymarket WS → OrderBookUpdate ←── Trading Engine ──→ OrderIntent
                                                 ↓
                                          Order Manager
                                                 ↓
                                        Polymarket CLOB
```

## Development

### Code Quality

```bash
# Format code
ruff format .

# Lint
ruff check .

# Type check
mypy src/
```

### Running Specific Tests

```bash
# Test truth engine
pytest tests/test_truth_engine.py -v

# Test pair math
pytest tests/test_pair_math.py -v
```

## Risk Warning

**⚠️ TRADING INVOLVES SUBSTANTIAL RISK OF LOSS**

- Always start with paper trading
- Never risk more than you can afford to lose
- The bot is provided as-is with no guarantees
- Past performance does not indicate future results

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests and linting
4. Submit a pull request

## Support

For issues and questions, please open a GitHub issue.
