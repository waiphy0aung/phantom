"""
Trade journal — CSV persistence for all trades.

Survives restarts. Loads history on startup for performance calculations.
Every trade gets logged: entry, exit, PnL, regime, strategy source.
"""

from __future__ import annotations


import csv
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

JOURNAL_FILE = "trades.csv"
BALANCE_FILE = "balance_history.csv"

TRADE_FIELDS = [
    "timestamp", "symbol", "side", "source", "regime",
    "entry_price", "exit_price", "amount", "pnl", "pnl_pct",
    "bars_held", "exit_reason",
]

BALANCE_FIELDS = ["timestamp", "balance", "daily_pnl", "open_positions"]


@dataclass
class TradeRecord:
    timestamp: str = ""
    symbol: str = ""
    side: str = ""
    source: str = ""
    regime: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    amount: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    exit_reason: str = ""


class TradeJournal:
    """Persistent trade log with performance stats."""

    def __init__(self):
        self.trades: list[TradeRecord] = []
        self._load_history()

    def _load_history(self):
        if not os.path.exists(JOURNAL_FILE):
            return
        try:
            with open(JOURNAL_FILE, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rec = TradeRecord(
                        timestamp=row.get("timestamp", ""),
                        symbol=row.get("symbol", ""),
                        side=row.get("side", ""),
                        source=row.get("source", ""),
                        regime=row.get("regime", ""),
                        entry_price=float(row.get("entry_price", 0)),
                        exit_price=float(row.get("exit_price", 0)),
                        amount=float(row.get("amount", 0)),
                        pnl=float(row.get("pnl", 0)),
                        pnl_pct=float(row.get("pnl_pct", 0)),
                        bars_held=int(row.get("bars_held", 0)),
                        exit_reason=row.get("exit_reason", ""),
                    )
                    self.trades.append(rec)
        except Exception:
            pass

    def log_trade(self, symbol: str, side: str, source: str, regime: str,
                  entry_price: float, exit_price: float, amount: float,
                  pnl: float, bars_held: int, exit_reason: str):
        pnl_pct = (pnl / (entry_price * amount) * 100) if entry_price * amount > 0 else 0

        rec = TradeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol, side=side, source=source, regime=regime,
            entry_price=entry_price, exit_price=exit_price,
            amount=amount, pnl=pnl, pnl_pct=pnl_pct,
            bars_held=bars_held, exit_reason=exit_reason,
        )
        self.trades.append(rec)

        file_exists = os.path.exists(JOURNAL_FILE)
        with open(JOURNAL_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(rec))

    def log_balance(self, balance: float, daily_pnl: float, open_positions: int):
        file_exists = os.path.exists(BALANCE_FILE)
        with open(BALANCE_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=BALANCE_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "balance": f"{balance:.2f}",
                "daily_pnl": f"{daily_pnl:.2f}",
                "open_positions": open_positions,
            })

    # --- Performance Stats ---

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
        if not self.trades:
            return 0.0
        return self.wins / self.total_trades * 100

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def avg_win(self) -> float:
        winning = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(winning) / len(winning) if winning else 0.0

    @property
    def avg_loss(self) -> float:
        losing = [t.pnl for t in self.trades if t.pnl <= 0]
        return sum(losing) / len(losing) if losing else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def max_drawdown(self) -> float:
        if not self.trades:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self.trades:
            cumulative += t.pnl
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def avg_bars_held(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.bars_held for t in self.trades) / len(self.trades)

    def stats_by_source(self) -> dict[str, dict]:
        """Performance breakdown by strategy source (trend vs mean_reversion)."""
        result = {}
        for source in ["trend", "mean_reversion"]:
            trades = [t for t in self.trades if t.source == source]
            if not trades:
                continue
            wins = sum(1 for t in trades if t.pnl > 0)
            result[source] = {
                "trades": len(trades),
                "win_rate": wins / len(trades) * 100 if trades else 0,
                "total_pnl": sum(t.pnl for t in trades),
            }
        return result

    def stats_by_symbol(self) -> dict[str, dict]:
        """Performance breakdown by trading pair."""
        result = {}
        for symbol in set(t.symbol for t in self.trades):
            trades = [t for t in self.trades if t.symbol == symbol]
            wins = sum(1 for t in trades if t.pnl > 0)
            result[symbol] = {
                "trades": len(trades),
                "win_rate": wins / len(trades) * 100 if trades else 0,
                "total_pnl": sum(t.pnl for t in trades),
            }
        return result

    def recent_trades(self, n: int = 10) -> list[TradeRecord]:
        return self.trades[-n:]
