# Stock Finder

A Telegram bot for the NSE. Send it a list of stocks and options (best between 09:30 and 10:00 IST). For each one it
replies with a verdict and the critical parameters behind it (value, target, points).

```
✅ APL Apollo Tubes (APLAPOLLO) ₹2,195.00: LONG TERM
  Basic Materials
📈 Long-term 86/100 (need 65) ✅
  ✓ Price vs 200-day avg: 2,165.3 vs 2,002.1 (+8.2%) (want above) +10/10
  ✓ 12-month momentum: +27% (want >+10%) +10/10
  ✓ ROE: 25% (want >15%) +10/10
  ✗ Volatility (1 yr): 44% (want <25%) 0/10

❌ United Spirits (UNITDSPR) ₹1,289.50: NOT RECOMMENDED
  Consumer Defensive
  Long-term 58/100 (need 65)
  ✗ Price vs 200-day avg: 1,227.4 vs 1,359.7 (-9.7%) (want above) 0/10
  ✗ Earnings growth (yoy): -8% (want >+10%) 0/8
```

A recommended strategy shows its top 4 strengths and its biggest shortfall; a strategy that missed shows its two
biggest shortfalls. Notes in brackets such as `(short term)` are ignored.

Marks: ✓ full points · ◐ partial · ✗ none or penalty · – no data (not scored).

## What you can send

Separate items with commas or new lines. Use any company name or NSE symbol; spelling mistakes are tolerated.

| Input | Meaning |
|---|---|
| `Tata Steel, hdfc bank, RELIANCE` | stocks by name or symbol |
| `TCS INFY WIPRO` | several exact symbols on one line |
| `NIFTY 25000 CE` | option, nearest expiry |
| `Bank Nifty 52000PE 27NOV` | option, specific expiry |
| `HDFC Bank 1800 CE` | stock option by company name |

Names are matched against NSE's official equity list (`EQUITY_L.csv`, downloaded once a day and cached in
`NSE_LIST_CACHE`). If that fails, the bot falls back to a Yahoo Finance search of NSE listings. If a name matches
several companies (e.g. `TATA`), the bot lists the closest ones for you to pick from.

Index options are supported for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY and NIFTYNXT50.

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

## Daily record and weekly report

Every request is saved to SQLite (`STOCK_FINDER_DB`): the time, the item, the verdict, the price, and the intraday
plan or option premium. If an item is sent more than once in a day, only the first (morning) call is judged.

- `/today` lists what has been recorded today.
- `/report` gives this week's hit/fail report. It is also sent automatically every **Saturday at 10:00 IST**, covering
  Monday to Saturday.

How each call is judged:

| Verdict | ✅ Hit | ❌ Fail |
|---|---|---|
| INTRADAY LONG/SHORT | target reached before the stop that day (or closed in profit) | stop first (or closed at a loss) |
| LONG TERM | beat NIFTY from that day's open to the latest close | lagged NIFTY |
| NOT RECOMMENDED | did not beat NIFTY (avoiding it was right) | beat NIFTY |
| Option BUY | premium now higher (intrinsic value if expired) | premium fell |
| Option AVOID | premium fell | premium rose |

Only 5-minute bars that start after the call count. A bar that touches both stop and target counts as the stop.
One week is an early read for long-term calls. ⏳ marks calls that can't be judged yet, for example while the
market is still open.

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
   .venv/bin/python -m stock_finder check "Tata Steel, NIFTY 25000 CE"
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
