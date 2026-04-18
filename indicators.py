"""
Technical indicator library for PHANTOM.

All pure functions operating on pandas Series/DataFrames.
No side effects, no state, no exchange calls.
"""

import numpy as np
import pandas as pd


# =============================================================================
# MOVING AVERAGES
# =============================================================================

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average."""
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(window=period).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )


def hma(series: pd.Series, period: int) -> pd.Series:
    """
    Hull Moving Average — reduces lag while maintaining smoothness.
    HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
    """
    half_period = max(1, period // 2)
    sqrt_period = max(1, int(np.sqrt(period)))
    wma_half = wma(series, half_period)
    wma_full = wma(series, period)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_period)


# =============================================================================
# OSCILLATORS
# =============================================================================

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# =============================================================================
# VOLATILITY
# =============================================================================

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


# =============================================================================
# TREND INDICATORS
# =============================================================================

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """
    Average Directional Index.
    Returns DataFrame with columns: adx, plus_di, minus_di
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_vals = atr(high, low, close, period)

    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr_vals)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr_vals)

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx_vals = dx.ewm(span=period, adjust=False).mean()

    return pd.DataFrame({
        "adx": adx_vals,
        "plus_di": plus_di,
        "minus_di": minus_di,
    }, index=close.index)


def choppiness_index(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Choppiness Index — measures whether market is trending or ranging.
    High values (>61.8) = choppy. Low values (<38.2) = trending.
    """
    tr = true_range(high, low, close)
    atr_sum = tr.rolling(window=period).sum()
    high_max = high.rolling(window=period).max()
    low_min = low.rolling(window=period).min()

    price_range = high_max - low_min
    # Avoid division by zero
    price_range = price_range.replace(0, np.nan)

    ci = 100 * np.log10(atr_sum / price_range) / np.log10(period)
    return ci


def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               atr_period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Supertrend — ATR-based trend indicator.
    Returns DataFrame with columns: supertrend, direction (1=bullish, -1=bearish)
    """
    atr_vals = atr(high, low, close, atr_period)
    hl2 = (high + low) / 2

    upper_band = hl2 + multiplier * atr_vals
    lower_band = hl2 - multiplier * atr_vals

    st = pd.Series(np.nan, index=close.index)
    direction = pd.Series(0, index=close.index)

    # Initialize first bar from price vs bands
    if close.iloc[0] > upper_band.iloc[0]:
        st.iloc[0] = lower_band.iloc[0]
        direction.iloc[0] = 1
    else:
        st.iloc[0] = upper_band.iloc[0]
        direction.iloc[0] = -1

    for i in range(1, len(close)):
        # Adjust bands based on previous values
        if not (lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]):
            lower_band.iloc[i] = lower_band.iloc[i - 1]

        if not (upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]):
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        # Determine direction
        if direction.iloc[i - 1] == -1:  # was bearish
            if close.iloc[i] > upper_band.iloc[i]:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
        else:  # was bullish
            if close.iloc[i] < lower_band.iloc[i]:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            else:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1

    return pd.DataFrame({
        "supertrend": st,
        "direction": direction,
    }, index=close.index)


# =============================================================================
# BANDS / CHANNELS
# =============================================================================

def bollinger_bands(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    """Returns DataFrame with columns: upper, middle, lower, width"""
    middle = sma(close, period)
    std = close.rolling(window=period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    width = (upper - lower) / middle
    return pd.DataFrame({
        "bb_upper": upper,
        "bb_middle": middle,
        "bb_lower": lower,
        "bb_width": width,
    }, index=close.index)


def keltner_channels(high: pd.Series, low: pd.Series, close: pd.Series,
                     ema_period: int = 20, atr_period: int = 10,
                     multiplier: float = 1.5) -> pd.DataFrame:
    """Returns DataFrame with columns: upper, middle, lower"""
    middle = ema(close, ema_period)
    atr_vals = atr(high, low, close, atr_period)
    upper = middle + multiplier * atr_vals
    lower = middle - multiplier * atr_vals
    return pd.DataFrame({
        "kc_upper": upper,
        "kc_middle": middle,
        "kc_lower": lower,
    }, index=close.index)


def ttm_squeeze(high: pd.Series, low: pd.Series, close: pd.Series,
                bb_period: int = 20, bb_std: float = 2.0,
                kc_period: int = 20, kc_atr_period: int = 10,
                kc_mult: float = 1.5) -> pd.DataFrame:
    """
    TTM Squeeze — Bollinger Bands inside Keltner Channels.
    squeeze_on = True when BB is inside KC (volatility compressed).
    momentum = linear regression slope of close (direction of breakout).
    """
    bb = bollinger_bands(close, bb_period, bb_std)
    kc = keltner_channels(high, low, close, kc_period, kc_atr_period, kc_mult)

    squeeze_on = (bb["bb_lower"] > kc["kc_lower"]) & (bb["bb_upper"] < kc["kc_upper"])

    # Momentum: linear regression value over bb_period
    momentum = close.rolling(window=bb_period).apply(
        lambda x: np.polyfit(np.arange(len(x)), x, 1)[0], raw=True
    )

    return pd.DataFrame({
        "squeeze_on": squeeze_on,
        "squeeze_off": ~squeeze_on,
        "momentum": momentum,
    }, index=close.index)


# =============================================================================
# VWAP & Z-SCORE
# =============================================================================

def rolling_vwap(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, period: int = 96) -> pd.Series:
    """Rolling VWAP over a fixed lookback window."""
    typical_price = (high + low + close) / 3
    tp_vol = typical_price * volume
    return tp_vol.rolling(window=period).sum() / volume.rolling(window=period).sum()


def vwap_zscore(high: pd.Series, low: pd.Series, close: pd.Series,
                volume: pd.Series, period: int = 96) -> pd.Series:
    """Z-Score of price relative to rolling VWAP."""
    vwap = rolling_vwap(high, low, close, volume, period)
    deviation = close - vwap
    std = deviation.rolling(window=period).std()
    std = std.replace(0, np.nan)
    return deviation / std


# =============================================================================
# TRAILING STOPS
# =============================================================================

def chandelier_exit(high: pd.Series, low: pd.Series, close: pd.Series,
                    atr_period: int = 14, multiplier: float = 3.0,
                    lookback: int = 22) -> pd.DataFrame:
    """
    Chandelier Exit — trailing stop based on highest high minus ATR * multiplier.
    Returns long_stop and short_stop.
    """
    atr_vals = atr(high, low, close, atr_period)
    highest = high.rolling(window=lookback).max()
    lowest = low.rolling(window=lookback).min()

    long_stop = highest - multiplier * atr_vals
    short_stop = lowest + multiplier * atr_vals

    return pd.DataFrame({
        "long_stop": long_stop,
        "short_stop": short_stop,
    }, index=close.index)
