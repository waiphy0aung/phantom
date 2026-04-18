"""
PHANTOM Monitor — read-only live view of the running bot.

Reads log file and CSV journals to display current state.
Safe to run alongside the systemd service — no exchange connection.

Usage: python monitor.py
"""

from __future__ import annotations

import os
import time
import re
from datetime import datetime, timezone

from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from journal import TradeJournal

import config

LOG_FILE = config.LOG_FILE
BALANCE_FILE = "balance_history.csv"

console = Console()


def _pnl_color(pnl: float) -> str:
    if pnl > 0:
        return "green"
    if pnl < 0:
        return "red"
    return "white"


def parse_regime_from_log(lines: list[str]) -> dict[str, dict]:
    """Extract latest regime info per symbol from log lines."""
    regimes = {}
    pattern = re.compile(
        r"(\S+/USDT:USDT) \| Regime\((\w+) \| ADX=([\d.]+) CHOP=([\d.]+) "
        r"ATR_r=([\d.]+) HTF=(BULL|BEAR) ADX_slope=([+-]?[\d.]+)\)"
    )
    for line in reversed(lines):
        match = pattern.search(line)
        if match:
            symbol = match.group(1)
            if symbol not in regimes:
                regimes[symbol] = {
                    "regime": match.group(2),
                    "adx": float(match.group(3)),
                    "chop": float(match.group(4)),
                    "atr_r": float(match.group(5)),
                    "htf": match.group(6),
                    "adx_slope": float(match.group(7)),
                }
            if len(regimes) >= 3:
                break
    return regimes


def parse_last_balance(lines: list[str]) -> tuple[float, float]:
    """Extract last known balance and daily PnL from log."""
    balance = 10000.0
    daily_pnl = 0.0

    # Check balance CSV if exists
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE) as f:
                last_line = ""
                for last_line in f:
                    pass
                if last_line and "," in last_line:
                    parts = last_line.strip().split(",")
                    if len(parts) >= 3:
                        balance = float(parts[1])
                        daily_pnl = float(parts[2])
        except Exception:
            pass

    return balance, daily_pnl


def parse_recent_signals(lines: list[str], n: int = 10) -> list[str]:
    """Extract recent signal/trade log lines."""
    signals = []
    keywords = ["BUY", "SELL", "CLOSE", "TREND", "MR ", "Squeeze", "HMA", "OPENED", "Closing"]
    for line in reversed(lines):
        if any(kw in line for kw in keywords):
            signals.append(line.strip())
            if len(signals) >= n:
                break
    signals.reverse()
    return signals


def read_log_tail(path: str, n: int = 500) -> list[str]:
    """Read last N lines of log file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            lines = f.readlines()
            return lines[-n:]
    except Exception:
        return []


def build_display(journal: TradeJournal) -> Layout:
    layout = Layout()

    log_lines = read_log_tail(LOG_FILE)
    regimes = parse_regime_from_log(log_lines)
    balance, daily_pnl = parse_last_balance(log_lines)
    signals = parse_recent_signals(log_lines)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pnl_color = _pnl_color(daily_pnl)
    pnl_sign = "+" if daily_pnl >= 0 else ""

    # Header
    header_text = Text()
    header_text.append("PHANTOM", style="bold cyan")
    header_text.append("  Monitor (read-only)\n", style="dim")
    header_text.append(f"  Balance: ", style="white")
    header_text.append(f"${balance:,.2f}", style="bold white")
    header_text.append(f"  |  Daily PnL: ", style="white")
    header_text.append(f"{pnl_sign}${daily_pnl:,.2f}", style=f"bold {pnl_color}")
    header_text.append(f"  |  {now}", style="dim")
    header = Panel(header_text, border_style="cyan")

    # Market table
    market = Table(show_header=True, header_style="bold", expand=True, padding=(0, 1))
    market.add_column("Pair", style="bold")
    market.add_column("Regime", justify="center")
    market.add_column("ADX", justify="right")
    market.add_column("Chop", justify="right")
    market.add_column("ATR Ratio", justify="right")
    market.add_column("1H Trend", justify="center")
    market.add_column("ADX Slope", justify="right")

    for symbol in config.TRADING_PAIRS:
        short_name = symbol.split("/")[0]
        r = regimes.get(symbol)
        if r:
            regime_val = r["regime"]
            color = "green" if regime_val == "TRENDING" else "yellow" if regime_val == "RANGING" else "dim"
            htf = "[green]BULL[/]" if r["htf"] == "BULL" else "[red]BEAR[/]"
            market.add_row(
                short_name,
                f"[{color}]{regime_val}[/]",
                f"{r['adx']:.1f}",
                f"{r['chop']:.1f}",
                f"{r['atr_r']:.2f}",
                htf,
                f"{r['adx_slope']:+.2f}",
            )
        else:
            market.add_row(short_name, "[dim]waiting...[/]", *["—"] * 5)

    market_panel = Panel(market, title="[bold]Market[/]", border_style="blue")

    # Signals
    signal_text = Text()
    if signals:
        for s in signals[-8:]:
            # Trim timestamp prefix for cleaner display
            short = s[22:] if len(s) > 22 else s
            signal_text.append(short + "\n", style="white")
    else:
        signal_text.append("Waiting for signals...", style="dim")
    signals_panel = Panel(signal_text, title="[bold]Activity[/]", border_style="yellow")

    # Performance
    if journal.total_trades == 0:
        stats_text = Text("No trades yet — bot is scanning...", style="dim")
    else:
        pf = journal.profit_factor
        pf_str = f"{pf:.2f}" if pf != float("inf") else "INF"
        lines = []
        lines.append(f"  Trades:        {journal.total_trades}  ({journal.wins}W / {journal.losses}L)")
        lines.append(f"  Win Rate:      {journal.win_rate:.1f}%")
        lines.append(f"  Total PnL:     ${journal.total_pnl:,.2f}")
        lines.append(f"  Profit Factor: {pf_str}")
        lines.append(f"  Avg Win:       ${journal.avg_win:,.2f}")
        lines.append(f"  Avg Loss:      ${journal.avg_loss:,.2f}")
        lines.append(f"  Max Drawdown:  ${journal.max_drawdown:,.2f}")

        by_source = journal.stats_by_source()
        if by_source:
            lines.append("")
            for src, stats in by_source.items():
                label = "Trend" if src == "trend" else "MeanRev"
                lines.append(f"  {label:8s}  {stats['trades']}T  {stats['win_rate']:.0f}%WR  ${stats['total_pnl']:,.2f}")

        stats_text = Text("\n".join(lines))

    stats_panel = Panel(stats_text, title="[bold]Performance[/]", border_style="green")

    # Recent trades
    trades_table = Table(show_header=True, header_style="bold", expand=True, padding=(0, 1))
    trades_table.add_column("Time", style="dim")
    trades_table.add_column("Pair", style="bold")
    trades_table.add_column("Side")
    trades_table.add_column("Source")
    trades_table.add_column("Entry", justify="right")
    trades_table.add_column("Exit", justify="right")
    trades_table.add_column("PnL", justify="right")

    recent = journal.recent_trades(6)
    if not recent:
        trades_table.add_row("[dim]No trades yet[/]", *[""] * 6)
    else:
        for t in reversed(recent):
            pc = _pnl_color(t.pnl)
            ts = t.timestamp[11:19] if len(t.timestamp) > 19 else t.timestamp
            sn = t.symbol.split("/")[0] if t.symbol else "—"
            side_style = "[green]LONG[/]" if t.side == "long" else "[red]SHORT[/]"
            trades_table.add_row(
                ts, sn, side_style, t.source[:5],
                f"${t.entry_price:,.2f}", f"${t.exit_price:,.2f}",
                f"[{pc}]${t.pnl:,.2f}[/]",
            )

    trades_panel = Panel(trades_table, title="[bold]Recent Trades[/]", border_style="magenta")

    layout.split_column(
        Layout(header, name="header", size=4),
        Layout(market_panel, name="market", size=8),
        Layout(name="middle", size=12),
        Layout(name="bottom"),
    )
    layout["middle"].split_row(
        Layout(signals_panel, name="signals"),
    )
    layout["bottom"].split_row(
        Layout(trades_panel, name="trades", ratio=3),
        Layout(stats_panel, name="stats", ratio=2),
    )

    return layout


def main():
    journal = TradeJournal()

    with Live(build_display(journal), console=console, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                # Reload journal to pick up new trades
                journal = TradeJournal()
                live.update(build_display(journal))
                time.sleep(5)
            except KeyboardInterrupt:
                break

    print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
