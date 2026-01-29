# Esports Algorithmic Trading Platform
## End-to-End Documentation & Cursor AI Setup Guide

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Data Sources & APIs](#data-sources--apis)
5. [Strategy Development](#strategy-development)
6. [Implementation Guide](#implementation-guide)
7. [Cursor AI Prompts](#cursor-ai-prompts)
8. [Project Structure](#project-structure)
9. [Deployment](#deployment)
10. [Risk Management](#risk-management)

---

## Overview

An esports algorithmic trading platform automates betting decisions on competitive gaming events (CS2, LoL, Dota 2, Valorant) using real-time data, statistical models, and execution algorithms.

### Key Differentiators from Traditional Sports

| Aspect | Traditional Sports | Esports |
|--------|-------------------|---------|
| Data Frequency | Minutes | Sub-second (300ms frames) |
| Match Duration | Hours | 30-60 minutes |
| In-game Events | Limited | 50+ unique data points |
| Market Volatility | Moderate | High (micro-betting) |
| Data Sources | Box scores | Live stream AI extraction |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        ESPORTS ALGOTRADER PLATFORM                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │ DATA LAYER   │    │ STRATEGY     │    │ EXECUTION    │              │
│  │              │    │ ENGINE       │    │ ENGINE       │              │
│  │ • PandaScore │───▶│              │───▶│              │              │
│  │ • Oddin.gg   │    │ • ML Models  │    │ • Order Mgmt │              │
│  │ • Custom     │    │ • Signals    │    │ • Risk Ctrl  │              │
│  │   Scrapers   │    │ • Backtester │    │ • Logging    │              │
│  └──────────────┘    └──────────────┘    └──────────────┘              │
│         │                   │                   │                       │
│         ▼                   ▼                   ▼                       │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      DATA STORE (PostgreSQL/Redis)                 │  │
│  │  • Historical odds    • Match data    • Positions    • P&L         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      MONITORING & ALERTS                          │  │
│  │  • Grafana dashboards    • Telegram/Discord bots    • Logging     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Data Ingestion Layer

**Purpose:** Collect real-time and historical esports data

```python
# data_ingestion/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

@dataclass
class Match:
    id: str
    game: str  # cs2, lol, dota2, valorant
    tournament: str
    team1: str
    team2: str
    start_time: datetime
    status: str  # upcoming, live, finished
    odds: dict
    
@dataclass
class LiveFrame:
    match_id: str
    timestamp: datetime
    team1_score: int
    team2_score: int
    game_state: dict  # game-specific data
    
class DataProvider(ABC):
    @abstractmethod
    async def get_matches(self, game: str) -> List[Match]:
        pass
    
    @abstractmethod
    async def subscribe_live(self, match_id: str, callback):
        pass
```

### 2. Strategy Engine

**Purpose:** Generate trading signals from data

```python
# strategies/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class SignalType(Enum):
    BUY_TEAM1 = "buy_team1"
    BUY_TEAM2 = "buy_team2"
    HOLD = "hold"

@dataclass
class Signal:
    match_id: str
    signal_type: SignalType
    confidence: float  # 0-1
    expected_value: float
    recommended_stake: float
    reasoning: str

class Strategy(ABC):
    @abstractmethod
    def analyze(self, match: Match, history: list) -> Signal:
        pass
    
    @abstractmethod
    def backtest(self, historical_data: list) -> dict:
        pass
```

### 3. Execution Engine

**Purpose:** Place and manage bets

```python
# execution/engine.py
from dataclasses import dataclass
from typing import Optional
import asyncio

@dataclass
class Order:
    id: str
    match_id: str
    side: str
    stake: float
    odds: float
    status: str
    placed_at: datetime
    filled_at: Optional[datetime] = None

class ExecutionEngine:
    def __init__(self, broker_client, risk_manager):
        self.broker = broker_client
        self.risk = risk_manager
        self.positions = {}
        
    async def execute(self, signal: Signal) -> Optional[Order]:
        # Risk checks
        if not self.risk.approve(signal):
            return None
            
        # Place order
        order = await self.broker.place_bet(
            match_id=signal.match_id,
            side=signal.signal_type.value,
            stake=signal.recommended_stake
        )
        
        return order
```

---

## Data Sources & APIs

### Primary: PandaScore API

**Coverage:** LoL, CS2, Dota 2, Valorant + 9 other titles
**Website:** https://developers.pandascore.co/

```python
# data_providers/pandascore.py
import aiohttp
from typing import List

class PandaScoreClient:
    BASE_URL = "https://api.pandascore.co"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}
        
    async def get_upcoming_matches(self, game: str) -> List[dict]:
        """Get upcoming matches for a game (lol, csgo, dota2, valorant)"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.BASE_URL}/{game}/matches/upcoming"
            async with session.get(url, headers=self.headers) as resp:
                return await resp.json()
                
    async def get_match_details(self, match_id: int) -> dict:
        """Get detailed match information"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.BASE_URL}/matches/{match_id}"
            async with session.get(url, headers=self.headers) as resp:
                return await resp.json()
                
    async def get_team_stats(self, team_id: int) -> dict:
        """Get team statistics"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.BASE_URL}/teams/{team_id}/stats"
            async with session.get(url, headers=self.headers) as resp:
                return await resp.json()

# Usage example
async def main():
    client = PandaScoreClient("YOUR_API_KEY")
    matches = await client.get_upcoming_matches("csgo")
    for match in matches[:5]:
        print(f"{match['opponents'][0]['opponent']['name']} vs "
              f"{match['opponents'][1]['opponent']['name']}")
```

**Data Structure (PandaScore):**
```
League → Series → Tournament → Match → Game
```

### Secondary: Odds APIs

**The Odds API** (https://the-odds-api.com)
- Multi-bookmaker odds comparison
- Arbitrage detection support

```python
# data_providers/odds_api.py
class OddsAPIClient:
    BASE_URL = "https://api.the-odds-api.com/v4"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    async def get_esports_odds(self) -> List[dict]:
        """Get odds from multiple bookmakers"""
        async with aiohttp.ClientSession() as session:
            params = {
                "apiKey": self.api_key,
                "sport": "esports",  # esports_lol, esports_csgo, etc.
                "regions": "us,eu,uk",
                "markets": "h2h",
                "oddsFormat": "decimal"
            }
            async with session.get(f"{self.BASE_URL}/sports/esports/odds", 
                                   params=params) as resp:
                return await resp.json()
```

---

## Strategy Development

### Strategy 1: Value Betting (Expected Value)

```python
# strategies/value_betting.py
from strategies.base import Strategy, Signal, SignalType

class ValueBettingStrategy(Strategy):
    """
    Place bets when our predicted probability exceeds 
    the implied odds probability by a threshold.
    
    EV = (probability * odds) - 1
    Bet when EV > min_edge (e.g., 5%)
    """
    
    def __init__(self, model, min_edge: float = 0.05):
        self.model = model  # ML model for probability prediction
        self.min_edge = min_edge
        
    def analyze(self, match: Match, history: list) -> Signal:
        # Get model's predicted probability for team1
        features = self._extract_features(match, history)
        prob_team1 = self.model.predict_proba(features)[0]
        prob_team2 = 1 - prob_team1
        
        # Calculate implied probabilities from odds
        odds_team1 = match.odds.get("team1", 2.0)
        odds_team2 = match.odds.get("team2", 2.0)
        implied_prob1 = 1 / odds_team1
        implied_prob2 = 1 / odds_team2
        
        # Calculate expected value
        ev_team1 = (prob_team1 * odds_team1) - 1
        ev_team2 = (prob_team2 * odds_team2) - 1
        
        # Generate signal
        if ev_team1 > self.min_edge and ev_team1 > ev_team2:
            return Signal(
                match_id=match.id,
                signal_type=SignalType.BUY_TEAM1,
                confidence=prob_team1,
                expected_value=ev_team1,
                recommended_stake=self._kelly_stake(prob_team1, odds_team1),
                reasoning=f"EV={ev_team1:.2%}, Model prob={prob_team1:.2%}"
            )
        elif ev_team2 > self.min_edge:
            return Signal(
                match_id=match.id,
                signal_type=SignalType.BUY_TEAM2,
                confidence=prob_team2,
                expected_value=ev_team2,
                recommended_stake=self._kelly_stake(prob_team2, odds_team2),
                reasoning=f"EV={ev_team2:.2%}, Model prob={prob_team2:.2%}"
            )
        
        return Signal(
            match_id=match.id,
            signal_type=SignalType.HOLD,
            confidence=0.0,
            expected_value=0.0,
            recommended_stake=0.0,
            reasoning="No positive EV opportunity"
        )
        
    def _kelly_stake(self, prob: float, odds: float, 
                     fraction: float = 0.25) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        where b = odds - 1, p = win prob, q = 1 - p
        Using fractional Kelly for safety
        """
        b = odds - 1
        q = 1 - prob
        kelly = (b * prob - q) / b
        return max(0, kelly * fraction)
```

### Strategy 2: Arbitrage Detection

```python
# strategies/arbitrage.py
class ArbitrageStrategy(Strategy):
    """
    Find guaranteed profit opportunities across bookmakers.
    Arbitrage exists when: (1/odds1) + (1/odds2) < 1
    """
    
    def find_arbitrage(self, odds_data: List[dict]) -> List[dict]:
        opportunities = []
        
        for match in odds_data:
            best_odds_team1 = max(
                b["odds"]["team1"] for b in match["bookmakers"]
            )
            best_odds_team2 = max(
                b["odds"]["team2"] for b in match["bookmakers"]
            )
            
            implied_total = (1/best_odds_team1) + (1/best_odds_team2)
            
            if implied_total < 1:
                profit_margin = (1 - implied_total) * 100
                opportunities.append({
                    "match": match["id"],
                    "profit_margin": profit_margin,
                    "bet1": {"odds": best_odds_team1, "bookmaker": "..."},
                    "bet2": {"odds": best_odds_team2, "bookmaker": "..."},
                    "stake_ratio": self._calculate_stakes(
                        best_odds_team1, best_odds_team2
                    )
                })
                
        return opportunities
        
    def _calculate_stakes(self, odds1: float, odds2: float, 
                          total_stake: float = 100) -> dict:
        """Calculate stake distribution for guaranteed profit"""
        stake1 = total_stake / (1 + (odds1 / odds2))
        stake2 = total_stake - stake1
        return {"team1": stake1, "team2": stake2}
```

### Strategy 3: Live In-Play Trading

```python
# strategies/live_trading.py
class LiveTradingStrategy(Strategy):
    """
    Trade on live odds movements during matches.
    Uses momentum and mean-reversion signals.
    """
    
    def __init__(self, lookback: int = 60):
        self.lookback = lookback  # seconds
        self.odds_history = []
        
    def on_frame(self, frame: LiveFrame, current_odds: dict) -> Signal:
        self.odds_history.append({
            "timestamp": frame.timestamp,
            "odds_team1": current_odds["team1"],
            "score": (frame.team1_score, frame.team2_score)
        })
        
        if len(self.odds_history) < self.lookback:
            return Signal(..., signal_type=SignalType.HOLD)
            
        # Calculate momentum (odds velocity)
        recent = self.odds_history[-self.lookback:]
        odds_change = recent[-1]["odds_team1"] - recent[0]["odds_team1"]
        
        # Mean reversion signal: if odds moved too fast, expect correction
        if odds_change > 0.3:  # odds dropped significantly (team1 doing well)
            # Could be overreaction - consider opposite bet
            return self._generate_reversal_signal(...)
        
        return Signal(..., signal_type=SignalType.HOLD)
```

---

## Implementation Guide

### Step 1: Environment Setup

```bash
# Create project
mkdir esports-algotrader && cd esports-algotrader
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install aiohttp asyncio pandas numpy scikit-learn 
pip install sqlalchemy asyncpg redis tensorflow
pip install python-telegram-bot discord.py
pip install pytest pytest-asyncio

# For ML models
pip install xgboost lightgbm catboost
```

### Step 2: Configuration

```python
# config.py
from dataclasses import dataclass
from typing import Optional
import os

@dataclass
class Config:
    # API Keys
    PANDASCORE_API_KEY: str = os.getenv("PANDASCORE_API_KEY", "")
    ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://...")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # Trading
    MAX_STAKE_PER_BET: float = 50.0
    MAX_DAILY_LOSS: float = 200.0
    MIN_EDGE: float = 0.05
    KELLY_FRACTION: float = 0.25
    
    # Monitoring
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")
```

### Step 3: Database Schema

```sql
-- migrations/001_initial.sql
CREATE TABLE matches (
    id VARCHAR(64) PRIMARY KEY,
    game VARCHAR(32) NOT NULL,
    tournament VARCHAR(128),
    team1 VARCHAR(128) NOT NULL,
    team2 VARCHAR(128) NOT NULL,
    start_time TIMESTAMP WITH TIME ZONE,
    status VARCHAR(32),
    result VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE odds_snapshots (
    id SERIAL PRIMARY KEY,
    match_id VARCHAR(64) REFERENCES matches(id),
    bookmaker VARCHAR(64),
    odds_team1 DECIMAL(10,4),
    odds_team2 DECIMAL(10,4),
    odds_draw DECIMAL(10,4),
    captured_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    match_id VARCHAR(64) REFERENCES matches(id),
    strategy VARCHAR(64) NOT NULL,
    signal_type VARCHAR(32) NOT NULL,
    confidence DECIMAL(5,4),
    expected_value DECIMAL(10,4),
    recommended_stake DECIMAL(10,2),
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    match_id VARCHAR(64) REFERENCES matches(id),
    signal_id INTEGER REFERENCES signals(id),
    side VARCHAR(32) NOT NULL,
    stake DECIMAL(10,2) NOT NULL,
    odds DECIMAL(10,4) NOT NULL,
    status VARCHAR(32) DEFAULT 'pending',
    pnl DECIMAL(10,2),
    placed_at TIMESTAMP DEFAULT NOW(),
    settled_at TIMESTAMP
);

CREATE INDEX idx_matches_game ON matches(game);
CREATE INDEX idx_matches_status ON matches(status);
CREATE INDEX idx_orders_status ON orders(status);
```

### Step 4: Main Application

```python
# main.py
import asyncio
from config import Config
from data_providers.pandascore import PandaScoreClient
from strategies.value_betting import ValueBettingStrategy
from execution.engine import ExecutionEngine
from monitoring.alerts import TelegramNotifier

async def main():
    config = Config()
    
    # Initialize components
    data_client = PandaScoreClient(config.PANDASCORE_API_KEY)
    strategy = ValueBettingStrategy(model=load_model(), min_edge=config.MIN_EDGE)
    execution = ExecutionEngine(broker_client=..., risk_manager=...)
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    
    while True:
        try:
            # Fetch upcoming matches
            for game in ["csgo", "lol", "dota2", "valorant"]:
                matches = await data_client.get_upcoming_matches(game)
                
                for match_data in matches:
                    match = Match.from_api(match_data)
                    history = await get_match_history(match)
                    
                    # Generate signal
                    signal = strategy.analyze(match, history)
                    
                    if signal.signal_type != SignalType.HOLD:
                        # Execute trade
                        order = await execution.execute(signal)
                        
                        if order:
                            await notifier.send(
                                f"🎮 New bet placed!\n"
                                f"Match: {match.team1} vs {match.team2}\n"
                                f"Side: {signal.signal_type.value}\n"
                                f"Stake: ${order.stake:.2f}\n"
                                f"EV: {signal.expected_value:.2%}"
                            )
                            
            # Wait before next scan
            await asyncio.sleep(60)
            
        except Exception as e:
            await notifier.send(f"⚠️ Error: {str(e)}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Cursor AI Prompts

### Project Initialization Prompt

```markdown
# Cursor Rules: Esports Algotrader

You are building an esports algorithmic trading platform in Python.

## Project Context
- Language: Python 3.11+
- Framework: asyncio for concurrency
- Database: PostgreSQL + Redis
- APIs: PandaScore (esports data), The Odds API (betting odds)
- ML: scikit-learn, XGBoost for predictions

## Code Style
- Use type hints everywhere
- Async/await for all I/O operations
- Dataclasses for data models
- Abstract base classes for extensibility
- 100% test coverage for core logic

## Architecture Patterns
- Clean Architecture: separate data/domain/presentation layers
- Repository pattern for database access
- Strategy pattern for trading algorithms
- Observer pattern for live data subscriptions

## File Structure
```
src/
├── data/           # Data providers and repositories
├── domain/         # Core business logic, entities
├── strategies/     # Trading strategy implementations
├── execution/      # Order management, risk control
├── ml/             # Machine learning models
├── monitoring/     # Alerts, dashboards
└── tests/          # Unit and integration tests
```

## Key Constraints
- Never commit API keys (use .env)
- Log all trades with full context
- Implement circuit breakers for losses
- Rate limit API calls appropriately
```

### Data Layer Prompt

```markdown
## Task: Create PandaScore Data Provider

Create an async Python client for the PandaScore API with these requirements:

1. **Endpoints to implement:**
   - GET /matches/upcoming
   - GET /matches/{id}
   - GET /teams/{id}/stats
   - WebSocket for live frames

2. **Features:**
   - Automatic rate limiting (1000 req/hour free tier)
   - Response caching with Redis (5 min TTL for upcoming, 24h for historical)
   - Retry with exponential backoff
   - Request logging for debugging

3. **Data Models (dataclasses):**
   - Match: id, game, tournament, teams, odds, status
   - Team: id, name, stats, recent_matches
   - LiveFrame: timestamp, score, game_state

4. **Error Handling:**
   - Custom exceptions: RateLimitError, APIError, AuthError
   - Graceful degradation when API unavailable

Generate the complete implementation with unit tests.
```

### Strategy Implementation Prompt

```markdown
## Task: Implement Value Betting Strategy

Create a value betting strategy with ML-powered probability predictions.

**Requirements:**

1. **Input:**
   - Match object with current odds
   - Historical match data for both teams

2. **Process:**
   - Extract features: team win rates, head-to-head, recent form, tournament tier
   - Predict win probability using trained model
   - Calculate Expected Value: EV = (model_prob * odds) - 1
   - Generate bet signal if EV > min_edge (configurable, default 5%)

3. **Output (Signal dataclass):**
   - match_id, signal_type, confidence, expected_value
   - recommended_stake (fractional Kelly criterion)
   - reasoning (human-readable explanation)

4. **Backtesting:**
   - Method to run strategy on historical data
   - Return metrics: ROI, win_rate, max_drawdown, sharpe_ratio

Include comprehensive tests with mock data.
```

### ML Model Prompt

```markdown
## Task: Build Win Probability Prediction Model

Create an ML pipeline for esports match outcome prediction.

**Data Features to Engineer:**
- Team-level: win_rate_last_20, avg_rounds_won, map_pool_strength
- Head-to-head: h2h_win_rate, avg_score_diff
- Tournament context: tier (S/A/B/C), stage (group/playoff)
- Form: win_streak, days_since_last_match

**Model Requirements:**
- Use XGBoost classifier
- Cross-validation with time-based splits (no future leakage)
- Calibrate probabilities with Platt scaling
- Feature importance analysis

**Pipeline:**
1. DataLoader: fetch from database, handle missing values
2. FeatureEngineer: transform raw data to features
3. Model: train, predict, save/load
4. Evaluator: accuracy, log_loss, calibration plot

Generate complete code with training script.
```

### Execution Engine Prompt

```markdown
## Task: Build Order Execution Engine

Create an execution engine with risk management.

**Components:**

1. **OrderManager:**
   - Submit, cancel, track orders
   - Support multiple broker integrations
   - Order state machine: pending → filled/rejected → settled

2. **RiskManager:**
   - Max stake per bet (configurable)
   - Max daily loss circuit breaker
   - Max exposure per match/game/tournament
   - Position sizing based on Kelly criterion

3. **PositionTracker:**
   - Real-time P&L calculation
   - Portfolio exposure by game/team
   - Settlement reconciliation

**Risk Rules (configurable):**
- max_stake_per_bet: $50
- max_daily_loss: $200
- max_exposure_per_game: $100
- kelly_fraction: 0.25

Include integration with Telegram for trade notifications.
```

### Testing Prompt

```markdown
## Task: Create Comprehensive Test Suite

Build tests for the esports algotrader platform.

**Test Categories:**

1. **Unit Tests:**
   - Strategy signal generation
   - Risk manager approval logic
   - Kelly criterion calculation
   - Feature engineering

2. **Integration Tests:**
   - PandaScore API client (with mocked responses)
   - Database operations
   - Full signal → execution flow

3. **Backtest Tests:**
   - Strategy performance on historical data
   - No look-ahead bias verification
   - Transaction cost impact

**Testing Tools:**
- pytest with pytest-asyncio
- unittest.mock for API mocking
- Factory pattern for test data
- Fixtures for database setup

Generate test files with 90%+ coverage targets.
```

### Monitoring Prompt

```markdown
## Task: Build Monitoring and Alerting System

Create a monitoring system for the trading platform.

**Components:**

1. **Metrics Collection:**
   - Trades per hour/day
   - Win rate (rolling 7/30 day)
   - P&L by strategy/game
   - API latency and error rates

2. **Alerts (Telegram/Discord):**
   - Trade executed (match, stake, odds, EV)
   - Circuit breaker triggered
   - API errors
   - Daily P&L summary

3. **Dashboard (optional Grafana):**
   - Real-time P&L chart
   - Exposure breakdown
   - Strategy performance comparison

**Alert Message Format:**
```
🎮 Trade Executed
Match: Team A vs Team B (CS2)
Side: Team A @ 2.15
Stake: $25.00
EV: +7.3%
```

Generate Telegram bot integration with async handlers.
```

---

## Project Structure

```
esports-algotrader/
├── .cursor/
│   └── rules/
│       └── esports-algotrader.mdc    # Cursor rules file
├── src/
│   ├── __init__.py
│   ├── main.py                       # Application entry point
│   ├── config.py                     # Configuration management
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── providers/
│   │   │   ├── pandascore.py         # PandaScore API client
│   │   │   ├── odds_api.py           # Odds API client
│   │   │   └── base.py               # Abstract provider
│   │   ├── repositories/
│   │   │   ├── matches.py            # Match data repository
│   │   │   ├── orders.py             # Order repository
│   │   │   └── base.py               # Base repository
│   │   └── models.py                 # Data models (dataclasses)
│   │
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── entities.py               # Core business entities
│   │   └── services.py               # Domain services
│   │
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py                   # Abstract strategy
│   │   ├── value_betting.py          # Value betting strategy
│   │   ├── arbitrage.py              # Arbitrage detection
│   │   └── live_trading.py           # Live in-play strategy
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── engine.py                 # Order execution engine
│   │   ├── risk_manager.py           # Risk management
│   │   └── position_tracker.py       # Position tracking
│   │
│   ├── ml/
│   │   ├── __init__.py
│   │   ├── features.py               # Feature engineering
│   │   ├── models.py                 # ML model definitions
│   │   ├── training.py               # Training pipeline
│   │   └── inference.py              # Prediction service
│   │
│   └── monitoring/
│       ├── __init__.py
│       ├── alerts.py                 # Telegram/Discord alerts
│       ├── metrics.py                # Metrics collection
│       └── dashboard.py              # Dashboard integration
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # Pytest fixtures
│   ├── unit/
│   │   ├── test_strategies.py
│   │   ├── test_risk_manager.py
│   │   └── test_features.py
│   └── integration/
│       ├── test_pandascore.py
│       └── test_execution.py
│
├── migrations/
│   └── 001_initial.sql               # Database schema
│
├── notebooks/
│   ├── 01_data_exploration.ipynb
│   ├── 02_feature_engineering.ipynb
│   └── 03_model_training.ipynb
│
├── scripts/
│   ├── backtest.py                   # Backtesting runner
│   ├── train_model.py                # Model training script
│   └── seed_data.py                  # Database seeding
│
├── .env.example                      # Environment template
├── .gitignore
├── docker-compose.yml                # Local development
├── Dockerfile
├── pyproject.toml                    # Dependencies
├── README.md
└── Makefile                          # Common commands
```

---

## Deployment

### Docker Setup

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY migrations/ migrations/

CMD ["python", "-m", "src.main"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  app:
    build: .
    env_file: .env
    depends_on:
      - db
      - redis
    restart: unless-stopped
    
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: algotrader
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

### Cloud Deployment (AWS)

```bash
# EC2 setup
sudo apt update && sudo apt install -y docker.io docker-compose
git clone your-repo
cd esports-algotrader
cp .env.example .env
# Edit .env with API keys
docker-compose up -d

# Systemd service for auto-restart
sudo tee /etc/systemd/system/algotrader.service << EOF
[Unit]
Description=Esports Algotrader
After=docker.service

[Service]
WorkingDirectory=/home/ubuntu/esports-algotrader
ExecStart=/usr/bin/docker-compose up
ExecStop=/usr/bin/docker-compose down
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable algotrader
sudo systemctl start algotrader
```

---

## Risk Management

### Circuit Breakers

```python
# execution/risk_manager.py
class RiskManager:
    def __init__(self, config: Config):
        self.max_stake = config.MAX_STAKE_PER_BET
        self.max_daily_loss = config.MAX_DAILY_LOSS
        self.daily_pnl = 0.0
        self.is_halted = False
        
    def approve(self, signal: Signal) -> bool:
        if self.is_halted:
            return False
            
        if signal.recommended_stake > self.max_stake:
            signal.recommended_stake = self.max_stake
            
        if self.daily_pnl < -self.max_daily_loss:
            self.is_halted = True
            return False
            
        return True
        
    def update_pnl(self, pnl: float):
        self.daily_pnl += pnl
        if self.daily_pnl < -self.max_daily_loss:
            self.is_halted = True
            
    def reset_daily(self):
        self.daily_pnl = 0.0
        self.is_halted = False
```

### Key Risk Metrics

| Metric | Description | Target |
|--------|-------------|--------|
| Max Drawdown | Largest peak-to-trough decline | < 20% |
| Sharpe Ratio | Risk-adjusted returns | > 1.5 |
| Win Rate | Percentage of winning bets | > 52% |
| ROI | Return on Investment | > 5% monthly |
| Kelly Fraction | Bet sizing multiplier | 0.25 (quarter Kelly) |

---

## Additional Resources

### APIs & Data Sources
- **PandaScore**: https://developers.pandascore.co/docs/introduction
- **The Odds API**: https://the-odds-api.com/
- **Oddin.gg** (Enterprise): https://oddin.gg/official-esports-data

### Learning Resources
- QuantConnect (algorithmic trading concepts): https://www.quantconnect.com/
- Betfair Exchange API (exchange-style betting): https://developer.betfair.com/

### Cursor AI Resources
- Cursor Rules Repository: https://github.com/instructa/ai-prompts
- Cursor Documentation: https://docs.cursor.com/context/rules-for-ai

---

## Quick Start Checklist

- [ ] Sign up for PandaScore API key (free tier: 1000 req/hour)
- [ ] Set up PostgreSQL and Redis
- [ ] Clone project and configure `.env`
- [ ] Run migrations: `python scripts/migrate.py`
- [ ] Train initial model: `python scripts/train_model.py`
- [ ] Start in paper trading mode: `PAPER_TRADING=true python -m src.main`
- [ ] Monitor via Telegram bot
- [ ] After validation, switch to live trading

---

*Document Version: 1.0 | Last Updated: January 2026*
