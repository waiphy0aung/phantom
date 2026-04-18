"""
Telegram notification service.

Every trade, every error, every daily summary — delivered to your phone.
No silent failures. If the bot does something, you know about it.
"""

import logging
from datetime import datetime, timezone

import requests

import config

logger = logging.getLogger(__name__)


def _send_message(text: str):
    """Send a message via Telegram Bot API."""
    if not config.TELEGRAM_ENABLED:
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        # Notification failure should never crash the bot
        logger.warning(f"Telegram notification error: {e}")


def notify_trade(symbol: str, side: str, amount: float, price: float, balance: float):
    """Notify on trade execution."""
    emoji = "\U0001f7e2" if side.upper() == "BUY" else "\U0001f534"
    mode = "PAPER" if not config.LIVE_MODE else "LIVE"
    text = (
        f"{emoji} <b>{side.upper()}</b> [{mode}]\n"
        f"Pair: <code>{symbol}</code>\n"
        f"Amount: <code>{amount:.6f}</code>\n"
        f"Price: <code>${price:,.2f}</code>\n"
        f"Balance: <code>${balance:,.2f}</code>\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    _send_message(text)


def notify_close(symbol: str, entry: float, exit_price: float, pnl: float, reason: str):
    """Notify on position close."""
    emoji = "\U0001f4b0" if pnl > 0 else "\U0001f4a5"
    text = (
        f"{emoji} <b>POSITION CLOSED</b>\n"
        f"Pair: <code>{symbol}</code>\n"
        f"Entry: <code>${entry:,.2f}</code>\n"
        f"Exit: <code>${exit_price:,.2f}</code>\n"
        f"PnL: <code>${pnl:,.2f}</code>\n"
        f"Reason: {reason}"
    )
    _send_message(text)


def notify_error(error: str):
    """Notify on critical error."""
    text = (
        f"\u26a0\ufe0f <b>BOT ERROR</b>\n"
        f"<code>{error[:500]}</code>\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    _send_message(text)


def notify_startup():
    """Notify that the bot has started."""
    mode = "PAPER" if not config.LIVE_MODE else "\u26a0\ufe0f LIVE"
    pairs = ", ".join(config.TRADING_PAIRS)
    text = (
        f"\U0001f680 <b>Trading Bot Started</b>\n"
        f"Mode: <b>{mode}</b>\n"
        f"Exchange: {config.EXCHANGE_ID}\n"
        f"Pairs: {pairs}\n"
        f"Timeframe: {config.TIMEFRAME}\n"
        f"Risk/trade: {config.MAX_RISK_PER_TRADE*100}%"
    )
    _send_message(text)


def notify_daily_summary(balance: float, positions: dict, trades_today: int, pnl_today: float):
    """Daily performance summary."""
    pos_text = "\n".join(
        f"  {s}: {p['side']} @ ${p['entry_price']:,.2f}"
        for s, p in positions.items()
    ) or "  None"

    text = (
        f"\U0001f4ca <b>Daily Summary</b>\n"
        f"Balance: <code>${balance:,.2f}</code>\n"
        f"Trades today: {trades_today}\n"
        f"PnL today: <code>${pnl_today:,.2f}</code>\n"
        f"Open positions:\n{pos_text}"
    )
    _send_message(text)
