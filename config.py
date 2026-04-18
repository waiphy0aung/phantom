"""
PHANTOM — Adaptive Regime Trading Engine
Configuration

WARNING: Never commit real API keys to version control.
Use environment variables or a .env file in production.
"""

from __future__ import annotations


import os

from dotenv import load_dotenv
load_dotenv()

# =============================================================================
# MODE
# =============================================================================
LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() == "true"

# =============================================================================
# EXCHANGE
# =============================================================================
EXCHANGE_ID = "bybit"
EXCHANGE_CONFIG = {
    "apiKey": os.getenv("EXCHANGE_API_KEY", "YOUR_API_KEY_HERE"),
    "secret": os.getenv("EXCHANGE_SECRET", "YOUR_SECRET_HERE"),
    "options": {
        "defaultType": "swap",
        "adjustForTimeDifference": True,
    },
}

# =============================================================================
# TRADING PAIRS & TIMEFRAMES
# =============================================================================
# $100 account — SOL only. BTC minimum order ($77) exceeds safe position size.
TRADING_PAIRS = ["SOL/USDT:USDT"]

# Per-asset strategy mode
ASSET_STRATEGY_MODE = {
    "SOL/USDT:USDT": ["trend"],                       # trend only — SOL's edge
}
TIMEFRAME = "15m"
HTF_TIMEFRAME = "1h"           # higher timeframe for trend direction
CANDLE_LIMIT = 200             # need more data for regime detection
HTF_CANDLE_LIMIT = 100

# =============================================================================
# REGIME DETECTION
# =============================================================================
ADX_PERIOD = 10                # faster ADX for crypto's quick regime shifts
CHOP_PERIOD = 14               # Choppiness Index period
ATR_FAST = 14                  # for ATR ratio
ATR_SLOW = 100

# Regime thresholds — per-asset because SOL transitions faster than BTC
REGIME_THRESHOLDS = {
    "BTC/USDT:USDT": {"trending_adx": 25, "choppy_adx": 18, "trending_chop": 45, "choppy_chop": 55},
    "ETH/USDT:USDT": {"trending_adx": 25, "choppy_adx": 18, "trending_chop": 45, "choppy_chop": 55},
    "SOL/USDT:USDT": {"trending_adx": 22, "choppy_adx": 15, "trending_chop": 48, "choppy_chop": 58},
}
REGIME_THRESHOLDS_DEFAULT = {"trending_adx": 25, "choppy_adx": 18, "trending_chop": 45, "choppy_chop": 55}
REGIME_ATR_EXPANSION = 1.2

# =============================================================================
# TREND MODULE (active in TRENDING regime)
# =============================================================================
# Keltner Channels + squeeze detection
KC_PERIOD = 20
KC_ATR_PERIOD = 10
KC_MULTIPLIER = 1.5

# Bollinger Bands for squeeze detection
BB_PERIOD = 20
BB_STD = 2.0

# Hull Moving Average crossover confirmation
HMA_FAST = 9
HMA_SLOW = 21

# Higher timeframe Supertrend for direction
SUPERTREND_ATR_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0

# Volume confirmation for trend entries
TREND_VOLUME_MULTIPLIER = 1.5

# =============================================================================
# MEAN REVERSION MODULE (active in RANGING regime)
# =============================================================================
# VWAP Z-Score
VWAP_PERIOD = 96               # 96 bars of 15m = 24 hours
ZSCORE_ENTRY = 1.5             # enter mean reversion at |Z| > 1.5 (was 2.0 — never fired)
ZSCORE_STOP = 3.5              # hard stop if Z goes beyond 3.5
ZSCORE_EXIT = 0.0              # exit when Z returns to 0

# RSI confirmation
MR_RSI_PERIOD = 9
MR_RSI_LONG = 30               # RSI below this confirms long (was 25 — too strict)
MR_RSI_SHORT = 70              # RSI above this confirms short (was 75 — too strict)

# Volume for mean reversion (selling/buying climax)
MR_VOLUME_MULTIPLIER = 1.5     # was 2.0 — climax volume is rare on 15m

# Time stop (bars)
MR_TIME_STOP_DEFAULT = 20      # BTC, ETH
MR_TIME_STOP_SOL = 30          # SOL reverts slower

# =============================================================================
# CRYPTO EDGE FILTERS
# =============================================================================
# Funding rate — skip trade if funding is against you beyond this
FUNDING_RATE_THRESHOLD = 0.0003  # 0.03% per 8h

# Taker buy/sell ratio
TAKER_RATIO_LONG_MIN = 0.9     # don't long if taker ratio below this
TAKER_RATIO_SHORT_MAX = 1.1    # don't short if taker ratio above this

# OI divergence — price rising + OI falling = no new longs
OI_DIVERGENCE_LOOKBACK = 12    # bars to check for divergence

# Time-of-day sizing (UTC hours)
FULL_SIZE_START = 8             # 08:00 UTC
FULL_SIZE_END = 16              # 16:00 UTC
REDUCED_SIZE_FACTOR = 0.5       # 50% size outside prime hours

# =============================================================================
# RISK MANAGEMENT
# =============================================================================
# At $100, 1% risk = $1 which sizes below SOL minimum (0.1 SOL = $8.80).
# Real risk per trade is ~3% at this balance. Acceptable for micro account.
MAX_RISK_PER_TRADE = 0.03       # 3% for $100 micro account — scale down to 1% at $500+
LEVERAGE = 1

# Chandelier Exit ATR multipliers (per-asset)
CHANDELIER_ATR_PERIOD = 14
CHANDELIER_MULTIPLIER = {
    "BTC/USDT:USDT": 3.0,
    "ETH/USDT:USDT": 3.5,
    "SOL/USDT:USDT": 4.0,
}
# Default for unknown pairs
CHANDELIER_MULTIPLIER_DEFAULT = 3.0

# Trading fees (for backtesting)
TAKER_FEE = 0.00055            # 0.055% Bybit taker fee per side
MAKER_FEE = 0.0002             # 0.02% Bybit maker fee per side
USE_MAKER_FEES = False         # assume taker (market orders) by default

# Minimum order sizes (exchange-enforced)
MIN_ORDER_SIZE = {
    "BTC/USDT:USDT": 0.001,
    "ETH/USDT:USDT": 0.01,
    "SOL/USDT:USDT": 0.1,
}
MIN_ORDER_SIZE_DEFAULT = 0.001

# Correlation filter — not needed with single pair
CORRELATION_FILTER_ENABLED = False

# Daily drawdown limit — wider for micro account (one bad trade = 3%)
DAILY_DRAWDOWN_LIMIT = 0.10     # 10% — $10 on a $100 account

# Max concurrent positions — one pair, one position
MAX_OPEN_POSITIONS = 1

# =============================================================================
# TELEGRAM
# =============================================================================
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# =============================================================================
# LOOP
# =============================================================================
LOOP_INTERVAL_SECONDS = 60
MAX_RETRIES_ON_ERROR = 5
RETRY_DELAY_SECONDS = 30

# =============================================================================
# LOGGING
# =============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "trade_bot.log"
