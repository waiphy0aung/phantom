"""
PHANTOM Backtester — test the strategy against historical data.

Uses the exact same indicators, regime detection, and signal generation
as the live bot. No lookahead bias. Bar-by-bar simulation.

Usage:
    python backtest.py                          # default: 30 days
    python backtest.py --days 90                # 90 days
    python backtest.py --days 180 --pair BTC    # single pair, 6 months
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd

import config
from indicators import atr, chandelier_exit
from regime import detect_regime, Regime
from strategy import generate_signal, Signal

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("backtest")

# Fee per side (entry + exit = 2x this)
FEE_RATE = config.MAKER_FEE if config.USE_MAKER_FEES else config.TAKER_FEE


def calc_fees(amount: float, entry_price: float, exit_price: float) -> float:
    """Total round-trip fees for a trade."""
    return amount * entry_price * FEE_RATE + amount * exit_price * FEE_RATE


# =============================================================================
# Data fetching
# =============================================================================

def fetch_historical_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Fetch historical OHLCV from Bybit. Handles pagination."""
    exchange = ccxt.bybit({"enableRateLimit": True})
    all_candles = []

    # Calculate total candles needed
    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
    minutes = tf_minutes.get(timeframe, 15)
    total_candles = (days * 24 * 60) // minutes

    since = exchange.parse8601(
        (datetime.now(timezone.utc) - pd.Timedelta(days=days)).isoformat()
    )

    print(f"  Fetching {symbol} {timeframe} — {total_candles} candles ({days} days)...", end=" ", flush=True)

    while len(all_candles) < total_candles:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        all_candles.extend(batch)
        since = batch[-1][0] + 1  # next ms after last candle
        time.sleep(exchange.rateLimit / 1000)

    print(f"got {len(all_candles)} candles")

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df


def resample_to_1h(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Resample 15m data to 1H for higher timeframe analysis."""
    return df_15m.resample("1h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


# =============================================================================
# Backtest position tracking
# =============================================================================

@dataclass
class Position:
    symbol: str
    side: str
    amount: float
    entry_price: float
    source: str
    entry_bar: int
    highest_price: float = 0.0
    lowest_price: float = float("inf")

    def __post_init__(self):
        if self.side == "long":
            self.highest_price = self.entry_price
        else:
            self.lowest_price = self.entry_price


@dataclass
class Trade:
    symbol: str
    side: str
    source: str
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    pnl_pct: float
    bars_held: int
    regime: str
    exit_reason: str


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    start_balance: float = 10000.0
    total_fees: float = 0.0

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_trades * 100) if self.trades else 0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_return_pct(self) -> float:
        return (self.total_pnl / self.start_balance * 100) if self.start_balance else 0

    @property
    def avg_win(self) -> float:
        w = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(w) / len(w) if w else 0

    @property
    def avg_loss(self) -> float:
        l = [t.pnl for t in self.trades if t.pnl < 0]
        return sum(l) / len(l) if l else 0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0
        return gross_win / gross_loss

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0
        peak = self.equity_curve[0]
        max_dd = 0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        if len(self.equity_curve) < 2:
            return 0
        returns = pd.Series(self.equity_curve).pct_change().dropna()
        if returns.std() == 0:
            return 0
        # Annualize from 15m bars: ~35,040 bars/year
        return float(returns.mean() / returns.std() * np.sqrt(35040))

    @property
    def avg_bars_held(self) -> float:
        return sum(t.bars_held for t in self.trades) / len(self.trades) if self.trades else 0

    def by_source(self) -> dict[str, dict]:
        result = {}
        for source in set(t.source for t in self.trades):
            trades = [t for t in self.trades if t.source == source]
            wins = sum(1 for t in trades if t.pnl > 0)
            result[source] = {
                "trades": len(trades),
                "win_rate": wins / len(trades) * 100,
                "pnl": sum(t.pnl for t in trades),
                "avg_pnl": sum(t.pnl for t in trades) / len(trades),
            }
        return result

    def by_symbol(self) -> dict[str, dict]:
        result = {}
        for sym in set(t.symbol for t in self.trades):
            trades = [t for t in self.trades if t.symbol == sym]
            wins = sum(1 for t in trades if t.pnl > 0)
            result[sym] = {
                "trades": len(trades),
                "win_rate": wins / len(trades) * 100,
                "pnl": sum(t.pnl for t in trades),
            }
        return result

    def by_regime(self) -> dict[str, dict]:
        result = {}
        for regime in set(t.regime for t in self.trades):
            trades = [t for t in self.trades if t.regime == regime]
            wins = sum(1 for t in trades if t.pnl > 0)
            result[regime] = {
                "trades": len(trades),
                "win_rate": wins / len(trades) * 100,
                "pnl": sum(t.pnl for t in trades),
            }
        return result


# =============================================================================
# Core backtester
# =============================================================================

def run_backtest(
    symbols: list[str],
    days: int = 30,
    start_balance: float = 10000.0,
) -> BacktestResult:
    """Run bar-by-bar backtest across all symbols."""

    # Fetch data
    print("\n=== Fetching Historical Data ===\n")
    data_15m = {}
    data_1h = {}
    for symbol in symbols:
        df = fetch_historical_ohlcv(symbol, "15m", days)
        data_15m[symbol] = df
        data_1h[symbol] = resample_to_1h(df)

    # Find common date range
    start = max(df.index[0] for df in data_15m.values())
    end = min(df.index[-1] for df in data_15m.values())

    # Need warmup period for indicators
    warmup = config.CANDLE_LIMIT
    all_indices = data_15m[symbols[0]].loc[start:end].index
    if len(all_indices) <= warmup:
        print("Not enough data after warmup period.")
        return BacktestResult()

    tradeable_indices = all_indices[warmup:]
    total_bars = len(tradeable_indices)

    print(f"\n=== Running Backtest ===\n")
    print(f"  Period:     {start.date()} to {end.date()}")
    print(f"  Bars:       {total_bars} (after {warmup} warmup)")
    print(f"  Symbols:    {', '.join(s.split('/')[0] for s in symbols)}")
    print(f"  Balance:    ${start_balance:,.2f}")
    print()

    # State
    balance = start_balance
    positions: dict[str, Position] = {}
    result = BacktestResult(start_balance=start_balance)
    result.equity_curve.append(balance)

    for bar_idx, timestamp in enumerate(tradeable_indices):
        # Build lookback windows for this bar
        for symbol in symbols:
            df_full = data_15m[symbol]
            bar_loc = df_full.index.get_loc(timestamp)

            # Slice up to current bar (no lookahead)
            df_15m_window = df_full.iloc[max(0, bar_loc - config.CANDLE_LIMIT + 1):bar_loc + 1]
            df_1h_window = data_1h[symbol].loc[:timestamp].iloc[-config.HTF_CANDLE_LIMIT:]

            current_price = float(df_15m_window["close"].iloc[-1])

            # --- Manage existing positions ---
            if symbol in positions:
                pos = positions[symbol]
                bars_held = bar_idx - pos.entry_bar

                # Update price extremes
                if pos.side == "long":
                    pos.highest_price = max(pos.highest_price, current_price)
                else:
                    pos.lowest_price = min(pos.lowest_price, current_price)

                # Check Chandelier Exit
                mult = config.CHANDELIER_MULTIPLIER.get(symbol, config.CHANDELIER_MULTIPLIER_DEFAULT)
                atr_vals = atr(df_15m_window["high"], df_15m_window["low"],
                              df_15m_window["close"], config.CHANDELIER_ATR_PERIOD)
                current_atr = float(atr_vals.iloc[-1])
                should_close = False
                reason = ""

                if pos.side == "long":
                    stop = pos.highest_price - mult * current_atr
                    if current_price <= stop:
                        should_close = True
                        reason = f"Chandelier: {current_price:.2f} <= {stop:.2f}"
                else:
                    stop = pos.lowest_price + mult * current_atr
                    if current_price >= stop:
                        should_close = True
                        reason = f"Chandelier: {current_price:.2f} >= {stop:.2f}"

                # MR Z-score exit
                if not should_close and pos.source == "mean_reversion":
                    from indicators import vwap_zscore
                    if len(df_15m_window) >= config.VWAP_PERIOD:
                        zscore = vwap_zscore(
                            df_15m_window["high"], df_15m_window["low"],
                            df_15m_window["close"], df_15m_window["volume"],
                            config.VWAP_PERIOD,
                        )
                        z_now = zscore.iloc[-1]
                        if not pd.isna(z_now):
                            if pos.side == "long" and z_now >= config.ZSCORE_EXIT:
                                should_close = True
                                reason = f"MR target: Z={z_now:.2f}"
                            if pos.side == "short" and z_now <= config.ZSCORE_EXIT:
                                should_close = True
                                reason = f"MR target: Z={z_now:.2f}"

                # Time stop for MR
                time_limit = config.MR_TIME_STOP_SOL if "SOL" in symbol else config.MR_TIME_STOP_DEFAULT
                if not should_close and pos.source == "mean_reversion" and bars_held >= time_limit:
                    should_close = True
                    reason = f"Time stop: {bars_held} bars"

                if should_close:
                    if pos.side == "long":
                        raw_pnl = (current_price - pos.entry_price) * pos.amount
                    else:
                        raw_pnl = (pos.entry_price - current_price) * pos.amount

                    fees = calc_fees(pos.amount, pos.entry_price, current_price)
                    pnl = raw_pnl - fees
                    result.total_fees += fees

                    balance += pos.amount * current_price if pos.side == "long" else (
                        pos.amount * pos.entry_price + raw_pnl
                    )
                    balance -= fees

                    pnl_pct = pnl / (pos.entry_price * pos.amount) * 100

                    result.trades.append(Trade(
                        symbol=symbol, side=pos.side, source=pos.source,
                        entry_price=pos.entry_price, exit_price=current_price,
                        amount=pos.amount, pnl=pnl, pnl_pct=pnl_pct,
                        bars_held=bars_held, regime=pos.source,
                        exit_reason=reason,
                    ))
                    del positions[symbol]

            # --- Check for new entries ---
            if symbol not in positions and len(positions) < config.MAX_OPEN_POSITIONS:
                if len(df_15m_window) < config.CANDLE_LIMIT - 10:
                    continue

                regime = detect_regime(df_15m_window, df_1h_window, symbol)
                trade_signal = generate_signal(df_15m_window, df_1h_window, regime, symbol)

                if trade_signal and trade_signal.signal != Signal.HOLD:
                    side = "buy" if trade_signal.signal == Signal.BUY else "sell"

                    # Position sizing
                    risk_amount = balance * config.MAX_RISK_PER_TRADE * trade_signal.confidence
                    price_risk = abs(current_price - trade_signal.stop_loss)
                    if price_risk <= 0:
                        continue

                    size = risk_amount / price_risk
                    min_size = config.MIN_ORDER_SIZE.get(symbol, config.MIN_ORDER_SIZE_DEFAULT)
                    max_size = (balance * 0.95) / current_price
                    size = min(size, max_size)

                    if size < min_size:
                        continue

                    # Deduct cost + entry fee
                    cost = size * current_price
                    if cost > balance:
                        continue

                    balance -= cost
                    pos_side = "long" if side == "buy" else "short"

                    positions[symbol] = Position(
                        symbol=symbol, side=pos_side, amount=size,
                        entry_price=current_price, source=trade_signal.source,
                        entry_bar=bar_idx,
                    )

        # Track equity (balance + unrealized PnL)
        equity = balance
        for sym, pos in positions.items():
            price = float(data_15m[sym].loc[:timestamp]["close"].iloc[-1])
            if pos.side == "long":
                equity += pos.amount * price
            else:
                equity += pos.amount * pos.entry_price + (pos.entry_price - price) * pos.amount
        result.equity_curve.append(equity)

        # Progress
        if bar_idx % 500 == 0 and bar_idx > 0:
            pct = bar_idx / total_bars * 100
            print(f"  [{pct:5.1f}%] Bar {bar_idx}/{total_bars} | "
                  f"Balance: ${balance:,.2f} | Equity: ${equity:,.2f} | "
                  f"Trades: {len(result.trades)} | Open: {len(positions)}")

    # Close any remaining positions at last price
    for symbol, pos in list(positions.items()):
        last_price = float(data_15m[symbol]["close"].iloc[-1])
        if pos.side == "long":
            raw_pnl = (last_price - pos.entry_price) * pos.amount
        else:
            raw_pnl = (pos.entry_price - last_price) * pos.amount

        fees = calc_fees(pos.amount, pos.entry_price, last_price)
        pnl = raw_pnl - fees
        result.total_fees += fees

        balance += pos.amount * last_price if pos.side == "long" else (
            pos.amount * pos.entry_price + raw_pnl
        )
        balance -= fees
        pnl_pct = pnl / (pos.entry_price * pos.amount) * 100
        bars_held = total_bars - pos.entry_bar

        result.trades.append(Trade(
            symbol=symbol, side=pos.side, source=pos.source,
            entry_price=pos.entry_price, exit_price=last_price,
            amount=pos.amount, pnl=pnl, pnl_pct=pnl_pct,
            bars_held=bars_held, regime=pos.source,
            exit_reason="End of backtest",
        ))

    return result


# =============================================================================
# Report
# =============================================================================

def print_report(result: BacktestResult, days: int):
    """Print comprehensive backtest results."""
    print("\n" + "=" * 60)
    print("  PHANTOM BACKTEST REPORT")
    print("=" * 60)

    if result.total_trades == 0:
        print("\n  No trades executed.\n")
        return

    pf = result.profit_factor
    pf_str = f"{pf:.2f}" if pf != float("inf") else "INF"

    final_equity = result.equity_curve[-1] if result.equity_curve else result.start_balance
    monthly_return = result.total_return_pct / (days / 30) if days > 0 else 0

    print(f"""
  Period:           {days} days
  Starting Balance: ${result.start_balance:,.2f}
  Final Equity:     ${final_equity:,.2f}
  Total Return:     {result.total_return_pct:+.2f}%
  Monthly Return:   {monthly_return:+.2f}%

  Total Trades:     {result.total_trades}
  Wins:             {result.wins}
  Losses:           {result.losses}
  Win Rate:         {result.win_rate:.1f}%

  Avg Win:          ${result.avg_win:,.2f}
  Avg Loss:         ${result.avg_loss:,.2f}
  Profit Factor:    {pf_str}
  Sharpe Ratio:     {result.sharpe_ratio:.2f}
  Max Drawdown:     {result.max_drawdown:.2f}%
  Avg Hold Time:    {result.avg_bars_held:.0f} bars ({result.avg_bars_held * 15 / 60:.1f}h)
  Total Fees:       ${result.total_fees:,.2f}
  Fee Rate:         {FEE_RATE*100:.3f}% per side
""")

    # By strategy source
    by_source = result.by_source()
    if by_source:
        print("  --- By Strategy ---")
        for src, stats in by_source.items():
            label = "Trend" if src == "trend" else "MeanRev" if src == "mean_reversion" else src
            print(f"  {label:10s}  {stats['trades']:3d} trades  "
                  f"{stats['win_rate']:5.1f}% WR  ${stats['pnl']:>10,.2f}  "
                  f"avg ${stats['avg_pnl']:>8,.2f}")
        print()

    # By symbol
    by_symbol = result.by_symbol()
    if by_symbol:
        print("  --- By Pair ---")
        for sym, stats in by_symbol.items():
            short = sym.split("/")[0]
            print(f"  {short:5s}  {stats['trades']:3d} trades  "
                  f"{stats['win_rate']:5.1f}% WR  ${stats['pnl']:>10,.2f}")
        print()

    # Top 5 wins and losses
    sorted_trades = sorted(result.trades, key=lambda t: t.pnl)
    if len(sorted_trades) >= 3:
        print("  --- Worst Trades ---")
        for t in sorted_trades[:3]:
            short = t.symbol.split("/")[0]
            print(f"  {short} {t.side:5s} ${t.pnl:>10,.2f} ({t.pnl_pct:+.1f}%) "
                  f"held {t.bars_held} bars — {t.exit_reason}")

        print("\n  --- Best Trades ---")
        for t in sorted_trades[-3:]:
            short = t.symbol.split("/")[0]
            print(f"  {short} {t.side:5s} ${t.pnl:>10,.2f} ({t.pnl_pct:+.1f}%) "
                  f"held {t.bars_held} bars — {t.exit_reason}")
        print()

    print("=" * 60)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="PHANTOM Backtester")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--pair", type=str, default=None, help="Single pair: BTC, ETH, or SOL")
    parser.add_argument("--balance", type=float, default=10000, help="Starting balance (default: 10000)")
    args = parser.parse_args()

    if args.pair:
        pair_map = {
            "BTC": "BTC/USDT:USDT",
            "ETH": "ETH/USDT:USDT",
            "SOL": "SOL/USDT:USDT",
        }
        symbol = pair_map.get(args.pair.upper())
        if not symbol:
            print(f"Unknown pair: {args.pair}. Use BTC, ETH, or SOL.")
            sys.exit(1)
        symbols = [symbol]
    else:
        symbols = config.TRADING_PAIRS

    result = run_backtest(symbols, days=args.days, start_balance=args.balance)
    print_report(result, args.days)


if __name__ == "__main__":
    main()
