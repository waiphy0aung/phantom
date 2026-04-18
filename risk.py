"""
Risk management for PHANTOM — ATR-based, regime-aware.

Rules:
- 1% max risk per trade, adjusted by confidence and time-of-day
- ATR-based stops via Chandelier Exit (not fixed %)
- Per-asset ATR multipliers (BTC=3x, ETH=3.5x, SOL=4x)
- Daily drawdown limit: 3% → stop all trading
- Time stop: 20 bars default, 30 for SOL
- Correlation-aware: no BTC+ETH same direction

The strategy finds trades. Risk management keeps you alive to trade tomorrow.
"""

from __future__ import annotations


import logging
from datetime import datetime, timezone

import pandas as pd

import config
from indicators import atr, vwap_zscore

logger = logging.getLogger(__name__)


class DailyDrawdownTracker:
    """Tracks daily PnL and enforces drawdown limit."""

    def __init__(self, starting_balance: float):
        self.starting_balance = starting_balance
        self.daily_start_balance = starting_balance
        self.current_date = datetime.now(timezone.utc).date()
        self.daily_pnl = 0.0
        self.is_stopped = False

    def update(self, current_balance: float):
        today = datetime.now(timezone.utc).date()
        if today != self.current_date:
            # New day — reset
            self.current_date = today
            self.daily_start_balance = current_balance
            self.daily_pnl = 0.0
            self.is_stopped = False
            logger.info(f"New trading day. Starting balance: ${current_balance:,.2f}")

        self.daily_pnl = current_balance - self.daily_start_balance
        drawdown_pct = abs(self.daily_pnl) / self.daily_start_balance if self.daily_pnl < 0 else 0

        if drawdown_pct >= config.DAILY_DRAWDOWN_LIMIT:
            if not self.is_stopped:
                logger.warning(
                    f"DAILY DRAWDOWN LIMIT HIT: {drawdown_pct:.1%} "
                    f"(${self.daily_pnl:,.2f}). Stopping all trading."
                )
            self.is_stopped = True

    @property
    def can_trade(self) -> bool:
        return not self.is_stopped


def calculate_position_size(
    balance: float,
    entry_price: float,
    stop_price: float,
    symbol: str = "",
    confidence: float = 1.0,
    size_multiplier: float = 1.0,
) -> float:
    """
    Calculate position size based on risk.

    size = (balance * risk% * confidence * tod_multiplier) / |entry - stop|
    Returns 0 if below exchange minimum order size.
    """
    if entry_price <= 0 or stop_price <= 0:
        return 0.0

    risk_amount = balance * config.MAX_RISK_PER_TRADE * confidence * size_multiplier
    price_risk = abs(entry_price - stop_price)

    if price_risk == 0:
        return 0.0

    size = risk_amount / price_risk

    # Cap at what we can afford (keep 5% buffer)
    max_affordable = (balance * 0.95) / entry_price
    size = min(size, max_affordable)

    # Check minimum order size
    min_size = config.MIN_ORDER_SIZE.get(symbol, config.MIN_ORDER_SIZE_DEFAULT)
    if size < min_size:
        logger.warning(
            f"Position size {size:.6f} below minimum {min_size} for {symbol} — skipping"
        )
        return 0.0

    logger.info(
        f"Position sizing: balance=${balance:.2f} risk=${risk_amount:.2f} "
        f"entry={entry_price:.2f} stop={stop_price:.2f} "
        f"conf={confidence:.1f} mult={size_multiplier:.1f} → size={size:.6f}"
    )
    return size


def get_chandelier_stop(df: pd.DataFrame, symbol: str, position: dict) -> float:
    """
    Chandelier stop anchored to the position's extreme price since entry.
    For longs: highest_price - ATR * multiplier
    For shorts: lowest_price + ATR * multiplier
    Uses the position's tracked high/low, NOT a rolling window.
    """
    mult = config.CHANDELIER_MULTIPLIER.get(symbol, config.CHANDELIER_MULTIPLIER_DEFAULT)
    atr_vals = atr(df["high"], df["low"], df["close"], config.CHANDELIER_ATR_PERIOD)
    current_atr = float(atr_vals.iloc[-1])

    side = position.get("side", "long")
    if side == "long":
        highest = position.get("highest_price", position["entry_price"])
        return highest - mult * current_atr
    else:
        lowest = position.get("lowest_price", position["entry_price"])
        return lowest + mult * current_atr


def should_close_position(
    position: dict,
    current_price: float,
    df_15m: pd.DataFrame,
    symbol: str,
    bars_held: int,
) -> tuple[bool, str]:
    """
    Check if position should be closed.

    Checks in order:
    1. Chandelier Exit (ATR-based trailing stop)
    2. Mean reversion target (Z-score returns to 0)
    3. Time stop
    """
    entry = position["entry_price"]
    side = position.get("side", "long")
    source = position.get("source", "trend")

    # --- Chandelier Exit (anchored to position high/low, not rolling) ---
    stop = get_chandelier_stop(df_15m, symbol, position)

    if side == "long" and current_price <= stop:
        return True, f"Chandelier Exit: price {current_price:.2f} <= stop {stop:.2f}"
    if side == "short" and current_price >= stop:
        return True, f"Chandelier Exit: price {current_price:.2f} >= stop {stop:.2f}"

    # --- Mean reversion exit: Z-score returns to target ---
    # RSI exit was removed — it fires long before Z=0, cutting profit in half.
    # Let Z-score drive MR exits. Chandelier handles the downside.
    if source == "mean_reversion" and len(df_15m) >= config.VWAP_PERIOD:
        zscore = vwap_zscore(
            df_15m["high"], df_15m["low"], df_15m["close"],
            df_15m["volume"], config.VWAP_PERIOD,
        )
        z_now = zscore.iloc[-1]
        if not pd.isna(z_now):
            if side == "long" and z_now >= config.ZSCORE_EXIT:
                return True, f"MR target hit: Z={z_now:.2f} >= {config.ZSCORE_EXIT}"
            if side == "short" and z_now <= config.ZSCORE_EXIT:
                return True, f"MR target hit: Z={z_now:.2f} <= {config.ZSCORE_EXIT}"

    # --- Time stop ---
    time_limit = config.MR_TIME_STOP_SOL if "SOL" in symbol else config.MR_TIME_STOP_DEFAULT
    if source == "mean_reversion" and bars_held >= time_limit:
        return True, f"Time stop: {bars_held} bars >= {time_limit} limit"

    return False, ""
