"""
PHANTOM Live Trading Dashboard.

Real-time terminal UI showing everything you need at a glance:
- Account balance + daily PnL
- Market prices + regime per pair
- Open positions with unrealized PnL
- Recent trade history
- Performance stats (win rate, profit factor, drawdown)

Uses Rich library for clean, formatted terminal output.
"""

from __future__ import annotations


from datetime import datetime, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
from journal import TradeJournal
from regime import RegimeState, Regime


console = Console()


def _regime_color(regime: Regime) -> str:
    if regime == Regime.TRENDING:
        return "green"
    if regime == Regime.RANGING:
        return "yellow"
    return "dim"


def _pnl_color(pnl: float) -> str:
    if pnl > 0:
        return "green"
    if pnl < 0:
        return "red"
    return "white"


def build_header(balance: float, daily_pnl: float, mode: str) -> Panel:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pnl_color = _pnl_color(daily_pnl)
    pnl_sign = "+" if daily_pnl >= 0 else ""

    text = Text()
    text.append("PHANTOM", style="bold cyan")
    text.append("  Adaptive Regime Trading Engine\n", style="dim")
    text.append(f"  Balance: ", style="white")
    text.append(f"${balance:,.2f}", style="bold white")
    text.append(f"  |  Daily PnL: ", style="white")
    text.append(f"{pnl_sign}${daily_pnl:,.2f}", style=f"bold {pnl_color}")
    text.append(f"  |  Mode: ", style="white")
    mode_style = "bold red" if mode == "LIVE" else "bold green"
    text.append(mode, style=mode_style)
    text.append(f"  |  {now}", style="dim")

    return Panel(text, border_style="cyan")


def build_market_table(
    prices: dict[str, float],
    regimes: dict[str, RegimeState],
    funding_rates: dict[str, float | None],
) -> Panel:
    table = Table(show_header=True, header_style="bold", expand=True, padding=(0, 1))
    table.add_column("Pair", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("Regime", justify="center")
    table.add_column("ADX", justify="right")
    table.add_column("Chop", justify="right")
    table.add_column("ATR Ratio", justify="right")
    table.add_column("1H Trend", justify="center")
    table.add_column("Funding", justify="right")

    for symbol in config.TRADING_PAIRS:
        short_name = symbol.split("/")[0]
        price = prices.get(symbol, 0)
        regime = regimes.get(symbol)
        funding = funding_rates.get(symbol)

        if regime:
            r_color = _regime_color(regime.regime)
            htf = "[green]BULL[/]" if regime.htf_direction == 1 else "[red]BEAR[/]"
            table.add_row(
                short_name,
                f"${price:,.2f}",
                f"[{r_color}]{regime.regime.value}[/]",
                f"{regime.adx_value:.1f}",
                f"{regime.chop_value:.1f}",
                f"{regime.atr_ratio:.2f}",
                htf,
                f"{funding:.4%}" if funding is not None else "—",
            )
        else:
            table.add_row(short_name, f"${price:,.2f}", "—", "—", "—", "—", "—", "—")

    return Panel(table, title="[bold]Market Overview[/]", border_style="blue")


def build_positions_table(
    positions: dict[str, dict],
    prices: dict[str, float],
) -> Panel:
    table = Table(show_header=True, header_style="bold", expand=True, padding=(0, 1))
    table.add_column("Pair", style="bold")
    table.add_column("Side", justify="center")
    table.add_column("Source", justify="center")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("uPnL", justify="right")
    table.add_column("uPnL %", justify="right")
    table.add_column("Bars", justify="right")

    if not positions:
        table.add_row("[dim]No open positions[/]", *[""] * 8)
    else:
        for symbol, pos in positions.items():
            short_name = symbol.split("/")[0]
            entry = pos.get("entry_price", 0)
            current = prices.get(symbol, entry)
            amount = pos.get("amount", 0)
            side = pos.get("side", "long")
            source = pos.get("source", "—")
            bars = pos.get("bars_held", 0)

            if side == "long":
                upnl = (current - entry) * amount
            else:
                upnl = (entry - current) * amount

            upnl_pct = (upnl / (entry * amount) * 100) if entry * amount > 0 else 0
            pnl_color = _pnl_color(upnl)

            side_style = "[green]LONG[/]" if side == "long" else "[red]SHORT[/]"

            table.add_row(
                short_name,
                side_style,
                source,
                f"${entry:,.2f}",
                f"${current:,.2f}",
                f"{amount:.6f}",
                f"[{pnl_color}]${upnl:,.2f}[/]",
                f"[{pnl_color}]{upnl_pct:+.2f}%[/]",
                str(bars),
            )

    return Panel(table, title="[bold]Open Positions[/]", border_style="magenta")


def build_trades_table(journal: TradeJournal) -> Panel:
    table = Table(show_header=True, header_style="bold", expand=True, padding=(0, 1))
    table.add_column("Time", style="dim")
    table.add_column("Pair", style="bold")
    table.add_column("Side")
    table.add_column("Source")
    table.add_column("Entry", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("PnL", justify="right")
    table.add_column("Reason")

    recent = journal.recent_trades(8)
    if not recent:
        table.add_row("[dim]No trades yet[/]", *[""] * 7)
    else:
        for t in reversed(recent):
            pnl_color = _pnl_color(t.pnl)
            ts = t.timestamp[11:19] if len(t.timestamp) > 19 else t.timestamp
            short_name = t.symbol.split("/")[0] if t.symbol else "—"
            table.add_row(
                ts,
                short_name,
                f"[green]LONG[/]" if t.side == "long" else f"[red]SHORT[/]",
                t.source[:5],
                f"${t.entry_price:,.2f}",
                f"${t.exit_price:,.2f}",
                f"[{pnl_color}]${t.pnl:,.2f}[/]",
                t.exit_reason[:30],
            )

    return Panel(table, title="[bold]Recent Trades[/]", border_style="yellow")


def build_stats_panel(journal: TradeJournal) -> Panel:
    if journal.total_trades == 0:
        text = Text("Waiting for first trade...", style="dim")
        return Panel(text, title="[bold]Performance[/]", border_style="green")

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
    lines.append(f"  Avg Hold:      {journal.avg_bars_held:.0f} bars")

    # Per-strategy breakdown
    by_source = journal.stats_by_source()
    if by_source:
        lines.append("")
        for src, stats in by_source.items():
            label = "Trend" if src == "trend" else "MeanRev"
            lines.append(
                f"  {label:8s}  {stats['trades']}T  "
                f"{stats['win_rate']:.0f}%WR  ${stats['total_pnl']:,.2f}"
            )

    # Per-symbol breakdown
    by_symbol = journal.stats_by_symbol()
    if by_symbol:
        lines.append("")
        for sym, stats in by_symbol.items():
            short = sym.split("/")[0]
            lines.append(
                f"  {short:5s}  {stats['trades']}T  "
                f"{stats['win_rate']:.0f}%WR  ${stats['total_pnl']:,.2f}"
            )

    text = Text("\n".join(lines))
    return Panel(text, title="[bold]Performance[/]", border_style="green")


class Dashboard:
    """Manages the live-updating terminal display."""

    def __init__(self, journal: TradeJournal):
        self.journal = journal
        self.prices: dict[str, float] = {}
        self.regimes: dict[str, RegimeState] = {}
        self.funding_rates: dict[str, float | None] = {}
        self.positions: dict[str, dict] = {}
        self.balance: float = 0.0
        self.daily_pnl: float = 0.0
        self.live: Live | None = None

    def start(self):
        self.live = Live(
            self._render(),
            console=console,
            refresh_per_second=1,
            screen=True,
        )
        self.live.start()

    def stop(self):
        if self.live:
            self.live.stop()

    def update(
        self,
        balance: float,
        daily_pnl: float,
        prices: dict[str, float],
        regimes: dict[str, RegimeState],
        positions: dict[str, dict],
        funding_rates: dict[str, float | None] | None = None,
    ):
        self.balance = balance
        self.daily_pnl = daily_pnl
        self.prices = prices
        self.regimes = regimes
        self.positions = positions
        if funding_rates:
            self.funding_rates = funding_rates

        if self.live:
            self.live.update(self._render())

    def _render(self) -> Layout:
        layout = Layout()

        mode = "LIVE" if config.LIVE_MODE else "PAPER"
        header = build_header(self.balance, self.daily_pnl, mode)
        market = build_market_table(self.prices, self.regimes, self.funding_rates)
        positions = build_positions_table(self.positions, self.prices)
        trades = build_trades_table(self.journal)
        stats = build_stats_panel(self.journal)

        layout.split_column(
            Layout(header, name="header", size=4),
            Layout(market, name="market", size=8),
            Layout(name="middle", size=12),
            Layout(name="bottom"),
        )

        layout["middle"].split_row(
            Layout(positions, name="positions"),
        )

        layout["bottom"].split_row(
            Layout(trades, name="trades", ratio=3),
            Layout(stats, name="stats", ratio=2),
        )

        return layout
