"""
PHANTOM Strategy — Adaptive Regime Trading.

Two modes, selected by regime detector:

TRENDING: Keltner Channel squeeze breakout + HMA crossover confirmation
  - Only trade in direction of 1H Supertrend
  - Volume must confirm
  - Exit via Chandelier Exit

RANGING: VWAP Z-Score mean reversion + RSI(9) confirmation
  - Enter at |Z| > 2.0 with RSI at extremes
  - Exit when Z returns to 0 or time stop hit
  - Volume spike confirms exhaustion (selling/buying climax)

UNCERTAIN: No new entries. Manage existing positions only.
"""

import logging
from dataclasses import dataclass
from enum import Enum

import pandas as pd

import config
from indicators import (
    hma, rsi, atr,
    bollinger_bands, keltner_channels, ttm_squeeze,
    vwap_zscore, chandelier_exit,
)
from regime import Regime, RegimeState

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    """Full signal with context for the execution layer."""
    signal: Signal
    source: str           # "trend" or "mean_reversion"
    symbol: str
    price: float
    stop_loss: float
    take_profit: float | None
    confidence: float     # 0-1, used for sizing
    reason: str


def _trend_signal(df: pd.DataFrame, regime: RegimeState, symbol: str) -> TradeSignal | None:
    """
    Trend module: Keltner squeeze breakout + HMA crossover.
    Only fires when 1H Supertrend agrees with direction.
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # Keltner squeeze detection
    squeeze = ttm_squeeze(
        high, low, close,
        config.BB_PERIOD, config.BB_STD,
        config.KC_PERIOD, config.KC_ATR_PERIOD, config.KC_MULTIPLIER,
    )

    # HMA crossover
    hma_fast = hma(close, config.HMA_FAST)
    hma_slow = hma(close, config.HMA_SLOW)

    # Chandelier for stop-loss
    mult = config.CHANDELIER_MULTIPLIER.get(symbol, config.CHANDELIER_MULTIPLIER_DEFAULT)
    ce = chandelier_exit(high, low, close, config.CHANDELIER_ATR_PERIOD, mult)

    # Volume
    vol_avg = volume.rolling(20).mean()
    vol_ratio = volume / vol_avg

    current = df.index[-1]
    prev = df.index[-2]

    squeeze_was_on = squeeze["squeeze_on"].iloc[-2]
    squeeze_fired = squeeze["squeeze_on"].iloc[-2] and squeeze["squeeze_off"].iloc[-1]
    momentum = squeeze["momentum"].iloc[-1]
    momentum_prev = squeeze["momentum"].iloc[-2]

    hma_fast_now = hma_fast.iloc[-1]
    hma_slow_now = hma_slow.iloc[-1]
    hma_fast_prev = hma_fast.iloc[-2]
    hma_slow_prev = hma_slow.iloc[-2]

    bullish_hma = hma_fast_prev <= hma_slow_prev and hma_fast_now > hma_slow_now
    bearish_hma = hma_fast_prev >= hma_slow_prev and hma_fast_now < hma_slow_now

    vol_confirmed = vol_ratio.iloc[-1] >= config.TREND_VOLUME_MULTIPLIER
    price_now = close.iloc[-1]

    htf_bullish = regime.htf_direction == 1
    htf_bearish = regime.htf_direction == -1

    # --- BUY: squeeze fires bullish + HMA bullish cross + 1H bullish ---
    if htf_bullish and vol_confirmed:
        # Primary: squeeze breakout
        if squeeze_fired and momentum > 0 and momentum > momentum_prev:
            sl = ce["long_stop"].iloc[-1]
            reason = f"Squeeze breakout UP | Mom={momentum:.4f} | Vol={vol_ratio.iloc[-1]:.1f}x"
            logger.info(f">>> TREND BUY: {symbol} | {reason}")
            return TradeSignal(
                signal=Signal.BUY, source="trend", symbol=symbol,
                price=price_now, stop_loss=sl, take_profit=None,
                confidence=0.8, reason=reason,
            )
        # Secondary: HMA crossover in trending regime
        if bullish_hma:
            sl = ce["long_stop"].iloc[-1]
            reason = f"HMA crossover UP | HMA9={hma_fast_now:.2f} > HMA21={hma_slow_now:.2f}"
            logger.info(f">>> TREND BUY: {symbol} | {reason}")
            return TradeSignal(
                signal=Signal.BUY, source="trend", symbol=symbol,
                price=price_now, stop_loss=sl, take_profit=None,
                confidence=0.6, reason=reason,
            )

    # --- SELL: squeeze fires bearish + HMA bearish cross + 1H bearish ---
    if htf_bearish and vol_confirmed:
        if squeeze_fired and momentum < 0 and momentum < momentum_prev:
            sl = ce["short_stop"].iloc[-1]
            reason = f"Squeeze breakout DOWN | Mom={momentum:.4f} | Vol={vol_ratio.iloc[-1]:.1f}x"
            logger.info(f">>> TREND SELL: {symbol} | {reason}")
            return TradeSignal(
                signal=Signal.SELL, source="trend", symbol=symbol,
                price=price_now, stop_loss=sl, take_profit=None,
                confidence=0.8, reason=reason,
            )
        if bearish_hma:
            sl = ce["short_stop"].iloc[-1]
            reason = f"HMA crossover DOWN | HMA9={hma_fast_now:.2f} < HMA21={hma_slow_now:.2f}"
            logger.info(f">>> TREND SELL: {symbol} | {reason}")
            return TradeSignal(
                signal=Signal.SELL, source="trend", symbol=symbol,
                price=price_now, stop_loss=sl, take_profit=None,
                confidence=0.6, reason=reason,
            )

    return None


def _mean_reversion_signal(df: pd.DataFrame, regime: RegimeState, symbol: str) -> TradeSignal | None:
    """
    Mean reversion module: VWAP Z-Score + RSI(9) confirmation.
    Enters on overextension, exits on return to mean.
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    zscore = vwap_zscore(high, low, close, volume, config.VWAP_PERIOD)
    rsi_vals = rsi(close, config.MR_RSI_PERIOD)
    atr_vals = atr(high, low, close, config.CHANDELIER_ATR_PERIOD)

    vol_avg = volume.rolling(20).mean()
    vol_ratio = volume / vol_avg

    z_now = zscore.iloc[-1]
    rsi_now = rsi_vals.iloc[-1]
    vol_confirmed = vol_ratio.iloc[-1] >= config.MR_VOLUME_MULTIPLIER
    price_now = close.iloc[-1]
    atr_now = atr_vals.iloc[-1]

    if pd.isna(z_now) or pd.isna(rsi_now):
        return None

    # Stop-loss: 2x ATR from entry — wide enough to survive noise,
    # tight enough to limit damage if regime shifts under us
    mr_stop_atr_mult = 2.0

    # --- LONG: price significantly below VWAP + RSI oversold + volume climax ---
    if z_now < -config.ZSCORE_ENTRY and rsi_now < config.MR_RSI_LONG and vol_confirmed:
        sl = price_now - mr_stop_atr_mult * atr_now

        reason = f"MR LONG | Z={z_now:.2f} RSI={rsi_now:.1f} Vol={vol_ratio.iloc[-1]:.1f}x"
        logger.info(f">>> MR BUY: {symbol} | {reason}")
        return TradeSignal(
            signal=Signal.BUY, source="mean_reversion", symbol=symbol,
            price=price_now, stop_loss=sl, take_profit=None,
            confidence=min(0.9, abs(z_now) / 3.0),
            reason=reason,
        )

    # --- SHORT: price significantly above VWAP + RSI overbought + volume climax ---
    if z_now > config.ZSCORE_ENTRY and rsi_now > config.MR_RSI_SHORT and vol_confirmed:
        sl = price_now + mr_stop_atr_mult * atr_now

        reason = f"MR SHORT | Z={z_now:.2f} RSI={rsi_now:.1f} Vol={vol_ratio.iloc[-1]:.1f}x"
        logger.info(f">>> MR SELL: {symbol} | {reason}")
        return TradeSignal(
            signal=Signal.SELL, source="mean_reversion", symbol=symbol,
            price=price_now, stop_loss=sl, take_profit=None,
            confidence=min(0.9, abs(z_now) / 3.0),
            reason=reason,
        )

    return None


def generate_signal(df_15m: pd.DataFrame, df_1h: pd.DataFrame,
                    regime: RegimeState, symbol: str) -> TradeSignal | None:
    """
    Master signal generator. Routes to trend or mean reversion based on regime.
    Returns None if no actionable signal.
    """
    if len(df_15m) < config.CANDLE_LIMIT - 10:
        logger.warning(f"{symbol}: Insufficient 15m data ({len(df_15m)} bars)")
        return None

    if regime.regime == Regime.TRENDING:
        return _trend_signal(df_15m, regime, symbol)

    elif regime.regime == Regime.RANGING:
        return _mean_reversion_signal(df_15m, regime, symbol)

    else:
        # UNCERTAIN — no new entries
        logger.debug(f"{symbol}: Regime UNCERTAIN — sitting out")
        return None
