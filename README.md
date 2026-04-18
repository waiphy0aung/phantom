# PHANTOM — Adaptive Regime Trading Engine

An automated crypto trading bot that detects market regimes and switches between trend-following and mean-reversion strategies. Built for Bybit perpetual futures.

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

Paper mode by default. No API keys needed — uses public market data with simulated fills.

## Strategy: How It Works

PHANTOM doesn't use one strategy. It detects the market's current **regime** and adapts:

### Regime Detection (every cycle)
- **ADX(10)** — trend strength
- **Choppiness Index(14)** — range detection
- **ATR ratio** — volatility expansion/compression
- **1H Supertrend** — higher-timeframe directional bias

| Regime | Condition | Action |
|--------|-----------|--------|
| TRENDING | ADX > 25 AND Chop < 45 | Trend-following entries |
| RANGING | ADX < 18 OR Chop > 55 | Mean-reversion entries |
| UNCERTAIN | Everything else | No new trades |

### Trend Module (TRENDING regime)
- **Entry:** Keltner Channel squeeze breakout + HMA 9/21 crossover
- **Direction:** Must align with 1H Supertrend
- **Exit:** Chandelier Exit (ATR-based trailing stop)
- **Volume:** Entry bar must exceed 1.5x average

### Mean Reversion Module (RANGING regime)
- **Entry:** VWAP Z-Score > 2.0 + RSI(9) at extremes + volume climax
- **Exit:** Z-Score returns to 0, RSI crosses 50, or time stop
- **Time stop:** 20 bars (BTC/ETH), 30 bars (SOL)

### Crypto Edge Filters (always active)
- **Funding rate** — skip if > 0.03% against trade direction
- **Taker buy/sell ratio** — don't fight dominant flow
- **Correlation filter** — no BTC + ETH same direction simultaneously
- **Time-of-day sizing** — 50% size outside 08:00-16:00 UTC

## Risk Management

| Setting | Value |
|---------|-------|
| Max risk per trade | 1% of account |
| Stop-loss | ATR-based (Chandelier Exit) |
| BTC ATR multiplier | 3.0x |
| ETH ATR multiplier | 3.5x |
| SOL ATR multiplier | 4.0x |
| Max concurrent positions | 2 |
| Daily drawdown limit | 3% → stop trading |

## Files

```
config.py       — All configuration and parameters
indicators.py   — Technical indicator library (EMA, HMA, RSI, ATR, ADX, BB, KC, Supertrend, VWAP)
regime.py       — Market regime detection (TRENDING/RANGING/UNCERTAIN)
strategy.py     — Dual-mode signal generation (trend + mean reversion)
filters.py      — Crypto-specific edge filters (funding, OI, taker ratio)
risk.py         — Position sizing, Chandelier Exit, daily drawdown
exchange.py     — CCXT wrapper + paper trading simulator
notifier.py     — Telegram notifications
main.py         — Main 24/7 trading loop
```

## Environment Variables

```bash
# Trading mode
LIVE_MODE=true              # default: false (paper)

# Exchange API (only needed for live)
EXCHANGE_API_KEY=xxx
EXCHANGE_SECRET=xxx

# Telegram (optional)
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# Debug
LOG_LEVEL=DEBUG             # default: INFO
```

## Warnings

- **Paper trade for weeks, not days.** Measure win rate, drawdown, and Sharpe before going live.
- **Start with 1x leverage.** The bot is profitable at 1x. Leverage multiplies stupidity.
- **This is not financial advice.** You are responsible for your own trades.
- **The bot will stop trading after 3% daily drawdown.** This is by design. Don't override it.
