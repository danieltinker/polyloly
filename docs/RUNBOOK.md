# PolyLOL Operational Runbook

> Procedures for common operational scenarios

---

## Table of Contents

1. [Startup Procedure](#1-startup-procedure)
2. [Shutdown Procedure](#2-shutdown-procedure)
3. [Incident Response](#3-incident-response)
4. [Common Issues](#4-common-issues)
5. [Monitoring](#5-monitoring)
6. [Recovery Procedures](#6-recovery-procedures)

---

## 1. Startup Procedure

### 1.1 Pre-Flight Checklist

```bash
# 1. Check system time sync
timedatectl status
# Ensure NTP is synchronized

# 2. Verify environment
cat .env | grep -v PASSWORD | grep -v KEY
# Check all required vars are set

# 3. Test connectivity
curl -s https://clob.polymarket.com/health | jq .
# Should return {"status":"ok"}

curl -s https://api.opendota.com/api/health | jq .
# Should return health status

# 4. Check USDC balance
python -m src.tools.healthcheck --check-balance
# Ensure sufficient funds
```

### 1.2 Start in Paper Mode (Default)

```bash
# Start with paper trading
PAPER_TRADING=true python -m src.bot.main

# Verify startup logs
tail -f logs/bot.jsonl | jq 'select(.event_type == "startup")'
```

### 1.3 Start in Live Mode

```bash
# WARNING: Real money at risk
# Double-check all settings first!

# 1. Verify config
python -m src.tools.healthcheck --full

# 2. Start with explicit live flag
PAPER_TRADING=false python -m src.bot.main

# 3. Monitor first 5 minutes closely
watch -n 5 'curl -s localhost:8080/metrics | grep polyloly_daily_pnl'
```

### 1.4 Startup Health Checks

The bot performs these checks automatically:

| Check | Description | Failure Action |
|-------|-------------|----------------|
| Config validation | All required fields present | Exit with error |
| NTP sync | Time within 1s of NTP | Warning (continue) |
| Polymarket WS | Connect to orderbook feed | Retry 3x, then exit |
| Polymarket REST | Fetch positions | Retry 3x, then exit |
| Esports feeds | Connect to enabled sources | Warning per source |
| USDC allowance | Sufficient approval | Warning (may fail orders) |

---

## 2. Shutdown Procedure

### 2.1 Graceful Shutdown

```bash
# Send SIGTERM
kill -15 $(pgrep -f "python -m src.bot.main")

# Or use Docker
docker-compose stop bot
```

**Graceful shutdown sequence:**
1. Stop accepting new signals (2s)
2. Cancel open orders (if configured)
3. Flush event log
4. Close WebSocket connections
5. Write state snapshot
6. Exit

### 2.2 Emergency Shutdown

```bash
# Immediate halt - use only in emergency
kill -9 $(pgrep -f "python -m src.bot.main")
```

**After emergency shutdown:**
1. Check for orphaned orders: `python -m src.tools.reconcile`
2. Review event log for last state
3. Cancel any remaining orders manually if needed

### 2.3 Planned Maintenance

```bash
# 1. Enable manual halt (prevents new orders)
curl -X POST localhost:8080/admin/halt

# 2. Wait for in-flight orders to settle (1-2 min)
sleep 120

# 3. Graceful shutdown
kill -15 $(pgrep -f "python -m src.bot.main")

# 4. Perform maintenance...

# 5. Restart
python -m src.bot.main
```

---

## 3. Incident Response

### 3.1 Severity Levels

| Level | Description | Response Time | Examples |
|-------|-------------|---------------|----------|
| **P0** | Trading halted, money at risk | Immediate | Kill switch, position mismatch |
| **P1** | Degraded, but safe | < 15 min | Single feed down, high latency |
| **P2** | Minor issue | < 1 hour | Warning logs, metric anomaly |
| **P3** | Improvement needed | Next business day | Performance optimization |

### 3.2 P0 Response Procedure

```
1. ASSESS (30 seconds)
   - Check alert details
   - Open Grafana dashboard
   - Review recent logs

2. MITIGATE (2 minutes)
   - If not already halted: curl -X POST localhost:8080/admin/halt
   - Verify no orders are pending
   - Take screenshot of current state

3. INVESTIGATE (10 minutes)
   - Identify root cause from logs
   - Check external service status (Polymarket, data feeds)
   - Document timeline

4. RESOLVE
   - Fix underlying issue
   - Test fix in paper mode if time permits
   - Reset kill switch: curl -X POST localhost:8080/admin/reset

5. POST-MORTEM
   - Document incident
   - Update runbook if needed
   - Create tickets for improvements
```

---

## 4. Common Issues

### 4.1 Kill Switch Triggered

**Symptoms:**
- All trading stopped
- Alert: "Kill switch activated: {reason}"
- Metric: `polyloly_kill_switch_active = 1`

**Diagnosis:**

```bash
# Check reason
grep "kill_switch_activated" logs/bot.jsonl | tail -5 | jq .

# Check daily P&L
curl -s localhost:8080/metrics | grep daily_pnl

# Check error counts
grep "error" logs/bot.jsonl | tail -20 | jq .
```

**Resolution by reason:**

| Reason | Action |
|--------|--------|
| `daily_loss_exceeded` | Wait until next day OR manually reset with review |
| `consecutive_errors` | Fix underlying error, then reset |
| `polymarket_disconnected` | Check Polymarket status, fix network |
| `esports_disconnected` | Check data feed status |
| `manual_halt` | Review why halt was requested |

**Reset procedure:**

```bash
# 1. Fix underlying issue
# 2. Verify system health
python -m src.tools.healthcheck --full

# 3. Reset (requires admin token)
curl -X POST localhost:8080/admin/reset \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 4. Monitor for 15 minutes
```

---

### 4.2 WebSocket Disconnection Loop

**Symptoms:**
- Reconnection counter incrementing rapidly
- No orderbook updates
- Metric: `polyloly_ws_reconnects_total` increasing

**Diagnosis:**

```bash
# Check Polymarket status
curl -s https://status.polymarket.com

# Check local network
curl -I https://clob.polymarket.com

# Check DNS
nslookup clob.polymarket.com

# Check recent reconnects
grep "ws_reconnect" logs/bot.jsonl | tail -20 | jq .
```

**Resolution:**

| Cause | Action |
|-------|--------|
| Polymarket down | Wait for recovery, system will auto-reconnect |
| Local network issue | Fix network, restart bot |
| Rate limited | Increase backoff delay in config |
| Auth expired | Regenerate credentials |

---

### 4.3 Position Mismatch

**Symptoms:**
- Alert: "Reconciliation mismatch detected"
- Metric: `polyloly_reconcile_mismatch_count > 0`

**Diagnosis:**

```bash
# Get mismatch details
grep "reconciliation_mismatch" logs/bot.jsonl | tail -5 | jq .

# Compare expected vs actual
python -m src.tools.reconcile --verbose
```

**Resolution:**

```bash
# 1. Halt the affected market
curl -X POST localhost:8080/admin/halt-market?market_id=<market_id>

# 2. Fetch actual positions from Polymarket
python -m src.tools.reconcile --fetch-actual

# 3. Determine correct state
# - Review recent fills in logs
# - Check Polymarket UI

# 4. Reset internal state to match actual
python -m src.tools.reconcile --reset-to-actual

# 5. Resume market
curl -X POST localhost:8080/admin/resume-market?market_id=<market_id>
```

---

### 4.4 High Order Latency

**Symptoms:**
- Orders taking > 5 seconds
- Metric: `polyloly_order_latency_seconds` p95 elevated

**Diagnosis:**

```bash
# Check latency distribution
curl -s localhost:8080/metrics | grep order_latency

# Check Polygon gas prices
curl -s https://gasstation-mainnet.matic.network/v2 | jq .fast

# Check recent order latencies
grep "order_placed" logs/bot.jsonl | tail -20 | jq '.latency_ms'
```

**Resolution:**

| Cause | Action |
|-------|--------|
| Chain congestion | Reduce order rate, increase gas |
| Polymarket load | Reduce order size, add delays |
| Local processing | Profile code, optimize |

---

### 4.5 Esports Feed Stale

**Symptoms:**
- Temporal strategy halted
- Alert: "Source stale: {source_id}"
- No truth updates for > 30s

**Diagnosis:**

```bash
# Check source health
grep "source_stale" logs/bot.jsonl | tail -5 | jq .

# Check external API
curl -s https://api.opendota.com/api/live | jq '.[0:3]'
```

**Resolution:**

```bash
# 1. Temporal strategy auto-halts (safe)

# 2. Check if API is actually down
# - OpenDota: https://status.opendota.com
# - GRID: Check dashboard

# 3. If API is up, restart the adapter
curl -X POST localhost:8080/admin/restart-adapter?adapter=opendota

# 4. Temporal strategy will auto-resume when fresh data arrives
```

---

## 5. Monitoring

### 5.1 Key Metrics to Watch

| Metric | Normal Range | Alert Threshold |
|--------|--------------|-----------------|
| `daily_pnl` | > -$50 | < -$100 |
| `order_latency_p95` | < 2s | > 5s |
| `ws_reconnects_total` (rate) | 0 | > 3/hour |
| `circuit_breaker_trips_total` | 0 | > 0 |
| `event_bus_depth` | < 100 | > 500 |
| `reconcile_mismatch_count` | 0 | > 0 |

### 5.2 Grafana Dashboard Panels

1. **P&L Overview** - Daily, weekly, monthly P&L
2. **Order Flow** - Orders placed, filled, rejected
3. **Latency** - Order latency histogram
4. **Positions** - Current exposure by market
5. **System Health** - WS status, error rates
6. **Strategy Performance** - Pair arb vs temporal arb

### 5.3 Log Queries

```bash
# Errors in last hour
grep '"level":"ERROR"' logs/bot.jsonl | tail -100 | jq .

# All orders for a market
grep '"market_id":"<market_id>"' logs/bot.jsonl | grep order | jq .

# P&L events
grep '"event_type":"pnl_'  logs/bot.jsonl | jq .

# Truth engine transitions
grep '"component":"truth_engine"' logs/bot.jsonl | jq '.event_type, .new_status'
```

---

## 6. Recovery Procedures

### 6.1 Recover from Crash

```bash
# 1. Check last state snapshot
cat data/state_snapshot.json | jq .

# 2. Check event log for last events
tail -100 logs/bot.jsonl | jq -s 'group_by(.market_id) | .[] | .[0]'

# 3. Reconcile positions
python -m src.tools.reconcile --verbose

# 4. If mismatches, resolve them first (see 4.3)

# 5. Restart
python -m src.bot.main
```

### 6.2 Recover from Data Corruption

```bash
# 1. Stop the bot
kill -15 $(pgrep -f "python -m src.bot.main")

# 2. Backup corrupted files
mv data/ data_corrupted_$(date +%Y%m%d)/

# 3. Create fresh data directory
mkdir data/

# 4. Fetch actual state from Polymarket
python -m src.tools.reconcile --rebuild-state

# 5. Restart
python -m src.bot.main
```

### 6.3 Disaster Recovery

If complete system loss:

```bash
# 1. Deploy fresh instance
git clone <repo> polyloly
cd polyloly

# 2. Restore config
cp /backup/.env .env
cp /backup/config/*.yaml config/

# 3. Start in paper mode first
PAPER_TRADING=true python -m src.bot.main

# 4. Verify system health
python -m src.tools.healthcheck --full

# 5. Reconcile with Polymarket
python -m src.tools.reconcile --rebuild-state

# 6. Switch to live when ready
# (After manual review of positions)
```

---

## Quick Reference

### Admin Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health/live` | GET | Liveness check |
| `/health/ready` | GET | Readiness check |
| `/metrics` | GET | Prometheus metrics |
| `/admin/halt` | POST | Global trading halt |
| `/admin/reset` | POST | Reset kill switch |
| `/admin/halt-market` | POST | Halt specific market |
| `/admin/resume-market` | POST | Resume specific market |

### Important Files

| File | Purpose |
|------|---------|
| `logs/bot.jsonl` | Structured event log |
| `data/state_snapshot.json` | Last known state |
| `data/positions.json` | Position cache |
| `config/*.yaml` | Configuration files |

### Emergency Contacts

| Role | Contact |
|------|---------|
| On-call engineer | (your contact) |
| Polymarket support | support@polymarket.com |
| Infra/hosting | (your provider) |

---

*Runbook Version: 1.0 | Last Updated: January 2025*
