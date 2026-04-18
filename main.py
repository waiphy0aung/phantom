"""
PHANTOM — Adaptive Regime Trading Engine
Main loop: runs 24/7, detects regimes, routes signals, manages risk.
Live terminal dashboard with real-time market data.

WARNING: This trades real money in LIVE mode. Paper trade first.
"""

import logging
import sys
import time
import traceback
from datetime import datetime, timezone

import config
from exchange import Exchange
from regime import detect_regime, RegimeState
from strategy import generate_signal, Signal
from risk import (
    calculate_position_size,
    should_close_position,
    DailyDrawdownTracker,
)
from filters import run_filters
from notifier import (
    notify_trade,
    notify_close,
    notify_error,
    notify_startup,
)
from journal import TradeJournal
from dashboard import Dashboard


def setup_logging():
    """Log to file only — dashboard owns the terminal."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s"
    handlers = [
        logging.FileHandler(config.LOG_FILE),
    ]
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=fmt,
        handlers=handlers,
    )


logger = logging.getLogger("main")


class CycleState:
    """Shared state collected during a cycle for the dashboard."""

    def __init__(self):
        self.prices: dict[str, float] = {}
        self.regimes: dict[str, RegimeState] = {}
        self.funding_rates: dict[str, float | None] = {}
        self.oi_changes: dict[str, float | None] = {}
        self.taker_ratios: dict[str, float | None] = {}
        self.ohlcv_15m: dict[str, object] = {}  # cached 15m data per symbol


def run_cycle(
    exchange: Exchange,
    drawdown: DailyDrawdownTracker,
    journal: TradeJournal,
    state: CycleState,
):
    """One full trading cycle."""
    balance = exchange.get_balance()
    drawdown.update(balance)

    # Collect market data for dashboard and filters
    for symbol in config.TRADING_PAIRS:
        try:
            state.prices[symbol] = exchange.get_ticker_price(symbol)
            state.funding_rates[symbol] = exchange.fetch_funding_rate(symbol)
            state.oi_changes[symbol] = exchange.fetch_oi_change_pct(symbol)
            state.taker_ratios[symbol] = exchange.fetch_taker_buy_sell_ratio(symbol)
        except Exception as e:
            logger.error(f"Error fetching market data for {symbol}: {e}")

    if not drawdown.can_trade:
        logger.warning("Daily drawdown limit hit — managing positions only")
        _manage_positions(exchange, journal, state)
        return

    # --- Phase 1: Manage existing positions ---
    _manage_positions(exchange, journal, state)

    # --- Phase 2: Scan for new entries ---
    positions = exchange.get_all_positions()
    balance = exchange.get_balance()

    if len(positions) >= config.MAX_OPEN_POSITIONS:
        return

    for symbol in config.TRADING_PAIRS:
        if symbol in positions:
            continue

        try:
            df_15m = exchange.fetch_ohlcv_15m(symbol)
            df_1h = exchange.fetch_ohlcv_1h(symbol)
            state.ohlcv_15m[symbol] = df_15m

            regime = detect_regime(df_15m, df_1h, symbol)
            state.regimes[symbol] = regime

            trade_signal = generate_signal(df_15m, df_1h, regime, symbol)
            if trade_signal is None or trade_signal.signal == Signal.HOLD:
                continue

            side = "buy" if trade_signal.signal == Signal.BUY else "sell"

            # Compute price change for OI divergence filter
            df_for_price_change = state.ohlcv_15m.get(symbol, df_15m)
            price_change = exchange.fetch_price_change_pct(df_for_price_change)

            filter_result = run_filters(
                side=side,
                symbol=symbol,
                open_positions=positions,
                funding_rate=state.funding_rates.get(symbol),
                price_change_pct=price_change,
                oi_change_pct=state.oi_changes.get(symbol),
                taker_buy_sell_ratio=state.taker_ratios.get(symbol),
            )

            if not filter_result.passed:
                logger.info(f"{symbol}: Signal {side.upper()} blocked by filters")
                continue

            size = calculate_position_size(
                balance=balance,
                entry_price=trade_signal.price,
                stop_price=trade_signal.stop_loss,
                symbol=symbol,
                confidence=trade_signal.confidence,
                size_multiplier=filter_result.size_multiplier,
            )

            if size <= 0:
                continue

            price = exchange.get_ticker_price(symbol)
            exchange.place_order(symbol, side, size, price, trade_signal.source)

            notify_trade(symbol, side, size, price, exchange.get_balance())
            logger.info(
                f"OPENED {side.upper()}: {symbol} size={size:.6f} @ {price:.2f} "
                f"SL={trade_signal.stop_loss:.2f} | {trade_signal.reason} "
                f"[{regime.regime.value}]"
            )

            balance = exchange.get_balance()
            positions = exchange.get_all_positions()

            if len(positions) >= config.MAX_OPEN_POSITIONS:
                break

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")

    exchange.increment_position_bars()

    # Log balance snapshot
    journal.log_balance(
        exchange.get_balance(),
        drawdown.daily_pnl,
        len(exchange.get_all_positions()),
    )


def _manage_positions(exchange: Exchange, journal: TradeJournal, state: CycleState):
    """Check all open positions for exit conditions."""
    positions = exchange.get_all_positions()

    for symbol, position in list(positions.items()):
        try:
            price = state.prices.get(symbol) or exchange.get_ticker_price(symbol)

            # Track highest/lowest since entry for Chandelier anchoring
            if not exchange.live_mode:
                exchange.paper.update_position_extremes(symbol, price)

            df_15m = exchange.fetch_ohlcv_15m(symbol)
            bars_held = position.get("bars_held", 0)

            should_close, reason = should_close_position(
                position, price, df_15m, symbol, bars_held,
            )

            if should_close:
                logger.info(f"Closing {symbol}: {reason}")
                result = exchange.close_position(symbol, price)

                if result:
                    pnl = 0.0
                    if isinstance(result, dict) and "pnl" in result:
                        pnl = result["pnl"]
                    else:
                        entry = position.get("entry_price", price)
                        amt = position.get("amount", 0)
                        if position.get("side") == "long":
                            pnl = (price - entry) * amt
                        else:
                            pnl = (entry - price) * amt

                    # Log to journal
                    journal.log_trade(
                        symbol=symbol,
                        side=position.get("side", "long"),
                        source=position.get("source", "unknown"),
                        regime=str(state.regimes.get(symbol, "—")),
                        entry_price=position["entry_price"],
                        exit_price=price,
                        amount=position.get("amount", 0),
                        pnl=pnl,
                        bars_held=bars_held,
                        exit_reason=reason,
                    )

                    notify_close(symbol, position["entry_price"], price, pnl, reason)

        except Exception as e:
            logger.error(f"Error managing {symbol}: {e}")


def main():
    setup_logging()

    logger.info("PHANTOM starting")

    if config.LIVE_MODE:
        logger.warning("!!! LIVE MODE — REAL MONEY AT RISK !!!")
        print("\n  !!! LIVE MODE — REAL MONEY AT RISK !!!")
        print("  Starting in 10 seconds... Ctrl+C to abort.\n")
        time.sleep(10)

    exchange = Exchange()
    drawdown = DailyDrawdownTracker(exchange.get_balance())
    journal = TradeJournal()
    dash = Dashboard(journal)
    notify_startup()

    consecutive_errors = 0

    # Initial dashboard update
    dash.update(
        balance=exchange.get_balance(),
        daily_pnl=0.0,
        prices={},
        regimes={},
        positions={},
    )
    dash.start()

    try:
        while True:
            try:
                cycle_start = time.time()
                state = CycleState()

                run_cycle(exchange, drawdown, journal, state)

                # Update dashboard
                dash.update(
                    balance=exchange.get_balance(),
                    daily_pnl=drawdown.daily_pnl,
                    prices=state.prices,
                    regimes=state.regimes,
                    positions=exchange.get_all_positions(),
                    funding_rates=state.funding_rates,
                )

                consecutive_errors = 0
                elapsed = time.time() - cycle_start
                sleep_time = max(0, config.LOOP_INTERVAL_SECONDS - elapsed)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                raise

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Cycle error #{consecutive_errors}: {e}\n{traceback.format_exc()}")
                notify_error(str(e))

                if consecutive_errors >= config.MAX_RETRIES_ON_ERROR:
                    logger.critical(f"Hit {config.MAX_RETRIES_ON_ERROR} errors — stopping")
                    notify_error(f"Bot stopped after {config.MAX_RETRIES_ON_ERROR} consecutive errors")
                    break

                delay = config.RETRY_DELAY_SECONDS * consecutive_errors
                time.sleep(delay)

    except KeyboardInterrupt:
        pass
    finally:
        dash.stop()
        logger.info("PHANTOM stopped.")
        print("\n  PHANTOM stopped. Check trade_bot.log for details.\n")


if __name__ == "__main__":
    main()
