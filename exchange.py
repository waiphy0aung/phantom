"""
Exchange abstraction layer for PHANTOM.

Wraps CCXT for all exchange operations. Handles:
- Multi-timeframe OHLCV (15m + 1H)
- Funding rate data
- Paper trading simulation
- Order execution (market orders)

Paper mode uses real market data with simulated fills.
"""

from __future__ import annotations


import logging
from datetime import datetime, timezone

import ccxt
import pandas as pd

import config

logger = logging.getLogger(__name__)


class PaperTrader:
    """Simulates order execution for paper trading mode."""

    def __init__(self):
        self.balance = 100.0  # match live account size for realistic paper testing
        self.positions: dict[str, dict] = {}
        self.order_id = 0
        self.trade_history: list[dict] = []

    def get_balance(self) -> float:
        return self.balance

    def get_position(self, symbol: str) -> dict | None:
        return self.positions.get(symbol)

    def get_all_positions(self) -> dict[str, dict]:
        return dict(self.positions)

    def execute_order(self, symbol: str, side: str, amount: float,
                      price: float, source: str = "trend") -> dict:
        self.order_id += 1
        order_id = f"paper-{self.order_id}"
        cost = amount * price
        now = datetime.now(timezone.utc).isoformat()

        if side == "buy":
            pos = self.positions.get(symbol)
            if pos and pos["side"] == "short":
                # Close existing short
                pnl = (pos["entry_price"] - price) * pos["amount"]
                self.balance += pos["amount"] * pos["entry_price"] + pnl
                self.trade_history.append({
                    "symbol": symbol,
                    "side": "short",
                    "entry": pos["entry_price"],
                    "exit": price,
                    "pnl": pnl,
                    "source": pos.get("source", "unknown"),
                    "bars_held": pos.get("bars_held", 0),
                    "closed_at": now,
                })
                del self.positions[symbol]
            else:
                # Open long
                if cost > self.balance:
                    raise ValueError(f"Insufficient balance: need {cost:.2f}, have {self.balance:.2f}")
                self.balance -= cost
                self.positions[symbol] = {
                    "symbol": symbol,
                    "side": "long",
                    "amount": amount,
                    "entry_price": price,
                    "opened_at": now,
                    "source": source,
                    "bars_held": 0,
                    "highest_price": price,
                }
        elif side == "sell":
            pos = self.positions.get(symbol)
            if pos and pos["side"] == "long":
                # Close existing long
                pnl = (price - pos["entry_price"]) * pos["amount"]
                self.balance += pos["amount"] * price
                self.trade_history.append({
                    "symbol": symbol,
                    "side": "long",
                    "entry": pos["entry_price"],
                    "exit": price,
                    "pnl": pnl,
                    "source": pos.get("source", "unknown"),
                    "bars_held": pos.get("bars_held", 0),
                    "closed_at": now,
                })
                del self.positions[symbol]
            else:
                # Open short — reserve margin (notional value as collateral)
                margin = cost  # 1x leverage = full notional as margin
                if margin > self.balance:
                    raise ValueError(f"Insufficient balance for short: need {margin:.2f}, have {self.balance:.2f}")
                self.balance -= margin
                self.positions[symbol] = {
                    "symbol": symbol,
                    "side": "short",
                    "amount": amount,
                    "entry_price": price,
                    "opened_at": now,
                    "source": source,
                    "bars_held": 0,
                    "lowest_price": price,
                }

        order = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "cost": cost,
            "status": "closed",
            "timestamp": now,
            "paper": True,
        }
        logger.info(
            f"[PAPER] {side.upper()} {amount:.6f} {symbol} @ {price:.2f} | "
            f"Balance: ${self.balance:.2f}"
        )
        return order

    def close_position(self, symbol: str, price: float) -> dict | None:
        """Close an existing position at given price."""
        pos = self.positions.get(symbol)
        if not pos:
            return None

        if pos["side"] == "long":
            pnl = (price - pos["entry_price"]) * pos["amount"]
            self.balance += pos["amount"] * price
        else:
            pnl = (pos["entry_price"] - price) * pos["amount"]
            self.balance += pos["amount"] * pos["entry_price"] + pnl

        now = datetime.now(timezone.utc).isoformat()
        self.trade_history.append({
            "symbol": symbol,
            "side": pos["side"],
            "entry": pos["entry_price"],
            "exit": price,
            "pnl": pnl,
            "source": pos.get("source", "unknown"),
            "bars_held": pos.get("bars_held", 0),
            "closed_at": now,
        })
        del self.positions[symbol]

        logger.info(
            f"[PAPER] CLOSE {symbol} {pos['side']} | "
            f"Entry={pos['entry_price']:.2f} Exit={price:.2f} "
            f"PnL=${pnl:.2f} | Balance: ${self.balance:.2f}"
        )
        return {"pnl": pnl, "side": pos["side"], "entry": pos["entry_price"]}

    def increment_bars(self):
        """Increment bars_held counter for all positions."""
        for pos in self.positions.values():
            pos["bars_held"] = pos.get("bars_held", 0) + 1

    def update_position_extremes(self, symbol: str, current_price: float):
        """Track highest/lowest price since entry for Chandelier Exit."""
        pos = self.positions.get(symbol)
        if not pos:
            return
        if pos["side"] == "long":
            pos["highest_price"] = max(pos.get("highest_price", current_price), current_price)
        else:
            pos["lowest_price"] = min(pos.get("lowest_price", current_price), current_price)


class Exchange:
    """Unified exchange interface — paper or live, same API."""

    def __init__(self):
        self.live_mode = config.LIVE_MODE
        self.paper = PaperTrader()

        exchange_class = getattr(ccxt, config.EXCHANGE_ID)

        if not self.live_mode:
            self.client = exchange_class({"enableRateLimit": True})
            logger.info("Exchange initialized in PAPER TRADING mode")
        else:
            self.client = exchange_class({
                **config.EXCHANGE_CONFIG,
                "enableRateLimit": True,
            })
            logger.warning("!!! EXCHANGE INITIALIZED IN LIVE MODE — REAL MONEY !!!")

        if config.LEVERAGE > 1 and self.live_mode:
            self._set_leverage()

    def _set_leverage(self):
        for symbol in config.TRADING_PAIRS:
            try:
                self.client.set_leverage(config.LEVERAGE, symbol)
                logger.info(f"Leverage set to {config.LEVERAGE}x for {symbol}")
            except Exception as e:
                logger.warning(f"Could not set leverage for {symbol}: {e}")

    def fetch_ohlcv(self, symbol: str, timeframe: str | None = None,
                    limit: int | None = None) -> pd.DataFrame:
        """Fetch OHLCV candles as DataFrame."""
        tf = timeframe or config.TIMEFRAME
        lim = limit or config.CANDLE_LIMIT
        raw = self.client.fetch_ohlcv(symbol, timeframe=tf, limit=lim)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df

    def fetch_ohlcv_15m(self, symbol: str) -> pd.DataFrame:
        return self.fetch_ohlcv(symbol, config.TIMEFRAME, config.CANDLE_LIMIT)

    def fetch_ohlcv_1h(self, symbol: str) -> pd.DataFrame:
        return self.fetch_ohlcv(symbol, config.HTF_TIMEFRAME, config.HTF_CANDLE_LIMIT)

    def get_ticker_price(self, symbol: str) -> float:
        ticker = self.client.fetch_ticker(symbol)
        return float(ticker["last"])

    def get_balance(self) -> float:
        if not self.live_mode:
            return self.paper.get_balance()
        balance = self.client.fetch_balance()
        return float(balance.get("USDT", {}).get("free", 0))

    def get_position(self, symbol: str) -> dict | None:
        if not self.live_mode:
            return self.paper.get_position(symbol)
        try:
            positions = self.client.fetch_positions([symbol])
            for pos in positions:
                if float(pos.get("contracts", 0)) > 0:
                    return {
                        "symbol": symbol,
                        "side": pos["side"],
                        "amount": float(pos["contracts"]),
                        "entry_price": float(pos["entryPrice"]),
                    }
        except Exception as e:
            logger.error(f"Error fetching position for {symbol}: {e}")
        return None

    def get_all_positions(self) -> dict[str, dict]:
        if not self.live_mode:
            return self.paper.get_all_positions()
        result = {}
        for symbol in config.TRADING_PAIRS:
            pos = self.get_position(symbol)
            if pos:
                result[symbol] = pos
        return result

    def place_order(self, symbol: str, side: str, amount: float,
                    price: float | None = None, source: str = "trend") -> dict:
        if not self.live_mode:
            if price is None:
                price = self.get_ticker_price(symbol)
            return self.paper.execute_order(symbol, side, amount, price, source)
        try:
            order = self.client.create_market_order(symbol, side, amount)
            logger.info(f"[LIVE] {side.upper()} {amount:.6f} {symbol} | Order ID: {order['id']}")
            return order
        except Exception as e:
            logger.error(f"Order failed: {side} {amount} {symbol} — {e}")
            raise

    def close_position(self, symbol: str, price: float | None = None) -> dict | None:
        """Close an existing position."""
        if not self.live_mode:
            if price is None:
                price = self.get_ticker_price(symbol)
            return self.paper.close_position(symbol, price)
        pos = self.get_position(symbol)
        if not pos:
            return None
        close_side = "sell" if pos["side"] == "long" else "buy"
        self.place_order(symbol, close_side, pos["amount"])
        return pos

    def fetch_funding_rate(self, symbol: str) -> float | None:
        """Get current funding rate for a perpetual contract."""
        try:
            funding = self.client.fetch_funding_rate(symbol)
            return float(funding.get("fundingRate", 0))
        except Exception as e:
            logger.debug(f"Could not fetch funding rate for {symbol}: {e}")
            return None

    def fetch_open_interest(self, symbol: str) -> float | None:
        """Get current open interest."""
        try:
            oi = self.client.fetch_open_interest(symbol)
            return float(oi.get("openInterestAmount", 0))
        except Exception as e:
            logger.debug(f"Could not fetch OI for {symbol}: {e}")
            return None

    def fetch_price_change_pct(self, df_15m: pd.DataFrame, lookback: int = 12) -> float | None:
        """Price change % over lookback bars."""
        if len(df_15m) < lookback + 1:
            return None
        old_price = df_15m["close"].iloc[-(lookback + 1)]
        new_price = df_15m["close"].iloc[-1]
        if old_price == 0:
            return None
        return (new_price - old_price) / old_price * 100

    def fetch_oi_change_pct(self, symbol: str) -> float | None:
        """
        OI change approximation. Fetches current OI and compares to
        cached previous value. Returns None on first call.
        """
        current_oi = self.fetch_open_interest(symbol)
        if current_oi is None:
            return None

        cache_key = f"_oi_prev_{symbol}"
        prev_oi = getattr(self, cache_key, None)
        setattr(self, cache_key, current_oi)

        if prev_oi is None or prev_oi == 0:
            return None
        return (current_oi - prev_oi) / prev_oi * 100

    def fetch_taker_buy_sell_ratio(self, symbol: str) -> float | None:
        """
        Taker buy/sell volume ratio from recent trades.
        Approximated from the last 15m OHLCV bar's buy/sell volume.
        Bybit doesn't expose taker ratio directly via CCXT — we estimate
        from the relationship between close position in the bar and volume.
        """
        try:
            # Use recent trades to estimate taker direction
            trades = self.client.fetch_trades(symbol, limit=200)
            if not trades:
                return None
            buy_vol = sum(t["amount"] for t in trades if t.get("side") == "buy")
            sell_vol = sum(t["amount"] for t in trades if t.get("side") == "sell")
            if sell_vol == 0:
                return None
            return buy_vol / sell_vol
        except Exception as e:
            logger.debug(f"Could not fetch taker ratio for {symbol}: {e}")
            return None

    def increment_position_bars(self):
        """Increment bars_held for paper positions."""
        if not self.live_mode:
            self.paper.increment_bars()
