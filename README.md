# Stock Finder

A Telegram bot for the NSE. Send it a list of stocks and options (best between 09:30 and 10:00 IST). For each one it
replies with a verdict and the few factors driving it.

```
✅ TCS ₹4,118.40: LONG TERM + INTRADAY LONG
  Long-term 78/100 · Intraday 72/100
  + price above 200-day average
  + ROE 46%
  + low debt (D/E 0.08)
  − 12-month momentum -4%
  intraday + volume 2.1x normal
  intraday + price above VWAP 4,108.20
  Plan LONG: entry 4,118.40 · stop 4,086.10 · target 4,182.90

❌ NIFTY 25000 CE 04 Nov @ ₹112.00: AVOID (40/100)
  spot 24,870.15 · IV 14.2% · PCR 0.68 · max pain 24900
  + 6 days to expiry
  − premium expensive (IV 14% vs realised 8%)
  − PCR 0.68: call writers capping upside
```

## What you can send

Separate items with commas or new lines. Plain stocks can also be separated by spaces.

| Input | Meaning |
|---|---|
| `RELIANCE, TCS INFY` | NSE stocks |
| `NIFTY 25000 CE` | option, nearest expiry |
| `BANKNIFTY 52000PE 27NOV` | option, specific expiry |

Index options are supported for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY and NIFTYNXT50. Stock options use the
stock symbol.

## How verdicts are decided

The rules are fixed, so the same data always gives the same answer. Each rule adds points and records a driver. A
verdict needs a score of at least 65.

**Long term** (0-100). If a fundamental field is missing it is left out of the score rather than counted as zero.
- Trend: price vs the 200-day average, the 50-day vs 200-day average, 12-month momentum (skipping the last month),
  and distance from the 52-week high.
- Fundamentals: ROE, debt/equity, earnings growth, revenue growth and P/E.
- Risk: volatility, the largest fall in the last year, and liquidity (average daily traded value above ₹10 cr).

**Intraday** (0-100), scored for both LONG and SHORT; the higher one wins:
- Today's 5-minute bars vs VWAP.
- Volume so far compared with prior sessions at the same time of day.
- RSI.
- The daily 20-day trend.
- The opening gap.
- How close the price is to the day's high or low.

The plan's stop is 0.5× the daily ATR and the target is 1× the daily ATR.

**Options** (0-100), from the option buyer's point of view:
- Underlying trend.
- Option IV vs the underlying's 20-day realised volatility.
- Days to expiry.
- Distance from spot.
- Contract volume.
- PCR.

Max pain is shown for reference.

Flags warn about results due within 7 days and about missing or early-session intraday data.

Every recommendation is logged to SQLite (`STOCK_FINDER_DB`), so its accuracy can be measured later.

## Data (free)

- Prices, intraday bars and fundamentals come from Yahoo Finance via `yfinance`. NSE quotes there can be delayed.
- Option chains come from NSE's public website API. NSE changes it from time to time; if it fails, options show
  `NO DATA` with the reason.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Install:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
   ```
3. Configure:
   ```bash
   cp .env.example .env
   ```
   Put the token in `TELEGRAM_BOT_TOKEN`.
4. Run the bot:
   ```bash
   set -a && . ./.env && set +a && .venv/bin/python -m stock_finder bot
   ```
5. Message the bot. It replies with your chat id. Add that id to `ALLOWED_CHAT_IDS` in `.env` and restart the bot.
6. Optional terminal check, without Telegram:
   ```bash
   .venv/bin/python -m stock_finder check RELIANCE, NIFTY 25000 CE
   ```
7. To run it permanently on a Linux server, use `deploy/stock-finder.service`.

## Tests

```bash
.venv/bin/pytest
```

```bash
.venv/bin/ruff check .
```

The tests use synthetic data, so they check the logic, not whether the signals make money. Use the recommendation
log to measure real accuracy before relying on the verdicts. Not investment advice.
