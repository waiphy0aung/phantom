You are an expert crypto trading bot developer using CCXT and Python.

Build a COMPLETE, self-contained, production-ready crypto auto-trading bot project from scratch.

Project structure:
- main.py (the main infinite loop that runs 24/7)
- strategy.py (all trading logic)
- config.py (API keys, pairs, risk settings — with placeholders)
- requirements.txt
- README.md with exact run instructions

Requirements:
- Exchange: Bybit or Binance (best for Singapore users)
- Pairs: BTC/USDT, ETH/USDT, SOL/USDT
- Timeframe: 15m
- Strategy: Classic but solid — RSI + EMA crossover + volume filter (or suggest a better simple one and explain why)
- Full risk management: max 1% risk per trade, stoploss -5%, trailing stop, take-profit 2-3%
- Paper trading / dry-run mode by default (switchable to live)
- Telegram notifications for every trade (optional but recommended)
- Auto-restart on errors
- Infinite loop with proper sleep, error handling, and logging
- Use only free/open-source libraries (ccxt, pandas, ta-lib, python-telegram-bot)
- Add clear comments and safety warnings

Use Claude Code's full agentic power: create all files, run tests if needed, and make sure everything works.

After generating the files, output a summary: "Bot is ready. Run with: python main.py"
