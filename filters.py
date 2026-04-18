"""
Crypto-specific edge filters for PHANTOM.

These filters use data unique to perpetual futures markets:
- Funding rate (crowd positioning)
- Open interest divergence (new money vs. liquidation)
- Taker buy/sell ratio (urgency)
- Time-of-day sizing (volume/liquidity cycles)
- Correlation filter (BTC+ETH overlap)

Each filter returns a pass/fail with a sizing multiplier.
"""

from __future__ import annotations


import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


class FilterResult:
    """Result of running all filters on a potential trade."""

    def __init__(self):
        self.passed = True
        self.size_multiplier = 1.0
        self.reasons: list[str] = []

    def fail(self, reason: str):
        self.passed = False
        self.reasons.append(f"BLOCKED: {reason}")

    def reduce_size(self, factor: float, reason: str):
        self.size_multiplier *= factor
        self.reasons.append(f"SIZE x{factor}: {reason}")

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"Filter({status} size={self.size_multiplier:.2f} | {'; '.join(self.reasons) or 'clean'})"


def check_funding_rate(funding_rate: float | None, side: str) -> tuple[bool, str]:
    """
    Skip trade if funding rate is extreme against our direction.
    Positive funding = longs pay shorts = crowded longs.
    """
    if funding_rate is None:
        return True, ""

    threshold = config.FUNDING_RATE_THRESHOLD

    if side == "buy" and funding_rate > threshold:
        return False, f"Funding {funding_rate:.4%} too high for long (crowded longs)"

    if side == "sell" and funding_rate < -threshold:
        return False, f"Funding {funding_rate:.4%} too negative for short (crowded shorts)"

    return True, ""


def check_oi_divergence(price_change_pct: float | None, oi_change_pct: float | None,
                        side: str) -> tuple[bool, str]:
    """
    Price rising + OI falling = no new longs (short covering rally).
    Price falling + OI falling = capitulation (potential long entry).
    """
    if price_change_pct is None or oi_change_pct is None:
        return True, ""

    # Price up but OI down — short covering, not genuine buying
    if side == "buy" and price_change_pct > 0.5 and oi_change_pct < -2.0:
        return False, f"OI divergence: price +{price_change_pct:.1f}% but OI {oi_change_pct:.1f}% (short covering)"

    # Price down but OI down — capitulation, might be ok for longs (contrarian)
    # Price down and OI up — new shorts, don't go long
    if side == "buy" and price_change_pct < -0.5 and oi_change_pct > 2.0:
        return False, f"OI confirms downtrend: price {price_change_pct:.1f}% OI +{oi_change_pct:.1f}% (new shorts)"

    return True, ""


def check_taker_ratio(taker_buy_sell_ratio: float | None, side: str) -> tuple[bool, str]:
    """
    Taker ratio = taker buy volume / taker sell volume.
    Don't long when sellers dominate. Don't short when buyers dominate.
    """
    if taker_buy_sell_ratio is None:
        return True, ""

    if side == "buy" and taker_buy_sell_ratio < config.TAKER_RATIO_LONG_MIN:
        return False, f"Taker ratio {taker_buy_sell_ratio:.2f} too low for long (sellers dominating)"

    if side == "sell" and taker_buy_sell_ratio > config.TAKER_RATIO_SHORT_MAX:
        return False, f"Taker ratio {taker_buy_sell_ratio:.2f} too high for short (buyers dominating)"

    return True, ""


def check_time_of_day() -> float:
    """
    Returns size multiplier based on current UTC hour.
    Full size during 08:00-16:00 UTC (peak volume).
    Reduced size outside peak hours.
    """
    hour = datetime.now(timezone.utc).hour

    if config.FULL_SIZE_START <= hour < config.FULL_SIZE_END:
        return 1.0
    return config.REDUCED_SIZE_FACTOR


def check_correlation(side: str, symbol: str, open_positions: dict[str, dict]) -> tuple[bool, str]:
    """
    Don't go same direction on BTC + ETH simultaneously.
    They're 0.7-0.85 correlated — that's not two bets, it's 1.5x one bet.
    """
    if not config.CORRELATION_FILTER_ENABLED:
        return True, ""

    correlated_pairs = {
        "BTC/USDT:USDT": "ETH/USDT:USDT",
        "ETH/USDT:USDT": "BTC/USDT:USDT",
    }

    correlated = correlated_pairs.get(symbol)
    if correlated and correlated in open_positions:
        existing_side = open_positions[correlated].get("side", "")
        if (side == "buy" and existing_side == "long") or (side == "sell" and existing_side == "short"):
            return False, f"Correlation block: already {existing_side} on {correlated}"

    return True, ""


def run_filters(
    side: str,
    symbol: str,
    open_positions: dict[str, dict],
    funding_rate: float | None = None,
    price_change_pct: float | None = None,
    oi_change_pct: float | None = None,
    taker_buy_sell_ratio: float | None = None,
) -> FilterResult:
    """
    Run all crypto edge filters on a potential trade.
    Returns FilterResult with pass/fail and size multiplier.
    """
    result = FilterResult()

    # Funding rate
    passed, reason = check_funding_rate(funding_rate, side)
    if not passed:
        result.fail(reason)

    # OI divergence
    passed, reason = check_oi_divergence(price_change_pct, oi_change_pct, side)
    if not passed:
        result.fail(reason)

    # Taker ratio
    passed, reason = check_taker_ratio(taker_buy_sell_ratio, side)
    if not passed:
        result.fail(reason)

    # Correlation
    passed, reason = check_correlation(side, symbol, open_positions)
    if not passed:
        result.fail(reason)

    # Time-of-day sizing (doesn't block, just reduces size)
    tod_mult = check_time_of_day()
    if tod_mult < 1.0:
        result.reduce_size(tod_mult, f"Off-peak hours (UTC {datetime.now(timezone.utc).hour}:00)")

    logger.info(f"{symbol} {side.upper()} filters: {result}")
    return result
