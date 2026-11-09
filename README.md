# Nifty 500 Research Desk

Streamlit app for fundamental and technical research on the Nifty 500.

**Run:** `pip install -r requirements.txt` then `streamlit run Home.py`. Click **Refresh live data** in the
sidebar (Yahoo Finance, needs internet) or switch on **Demo data** to explore offline. Demo data is synthetic.

| Page | What it does |
|---|---|
| Home | Market regime, breadth, top ideas, today's signals, track-record snapshot |
| Fundamental Ideas | Nifty 500 scored on DCF, reverse DCF, PE vs industry, PEG, EV/EBITDA, profit growth, ROCE/ROE, margins, leverage; quality gate |
| Stock Analysis | Candles + DMA/Bollinger/MACD/RSI; interactive DCF, reverse DCF, sensitivity grid; financials |
| Trading Ideas | MACD, DMA cross, Bollinger squeeze, RSI pullback, 52w breakout, Supertrend setups with ATR stop/target; position sizing; futures and option-spread structuring |
| Backtest | No look-ahead, costs, out-of-sample split, A/B/C grading of each setup |
| Market Breadth | A/D, % above 50/200 DMA, 52w highs/lows, McClellan, sector rotation |
| FII / DII | NSE daily provisional cash flows, stored to build history |
| Macro | VIX, USD/INR, crude, gold, US yields, global indices; World Bank India data |
| Track Record | Append-only, hash-chained idea book auto-tracked to P&L; manual F&O/swing journal |
| Q2 Results | Quarterly revenue/profit YoY & QoQ, margin change and grade for each company (Q2 FY27 by default) |
| Personal Finance | SIP/step-up, goal planner, retirement corpus, EMI & prepayment, new vs old tax regime, allocation |
| Client Access | QR code for the deployed app link, plus a pre-sharing checklist |

Universe: `data/nifty500.csv` (unofficial mirror of NSE's list, dated 2026-05-15 - replace with the
current file from niftyindices.com). F&O lot sizes: save NSE's list as `data/fo_lots.csv` (symbol,lot_size).

Not investment advice. Publishing stock recommendations to the public in India requires SEBI Research
Analyst registration.

## Daily data refresh

`python refresh.py` downloads prices and the day's FII/DII figures, refreshes fundamentals and quarterly
results when they are more than 7 days old, and re-grades the setups with the backtester. Schedule it for
about 7:30 pm IST on weekdays (cron or Windows Task Scheduler; see the top of `refresh.py`). It exits with a
non-zero code if a step fails.

## Tests

`python -m pytest` runs 46 tests that check the maths against independent values: Gordon-growth DCF,
reverse-DCF round trip, put-call parity, a known bank EMI, hand-computed income tax, no-look-ahead
backtest fills, and tamper detection in the idea book.

## Sharing with clients

Deploy (e.g. Streamlit Community Cloud), then create `.streamlit/secrets.toml` with
`admin_password = "..."` and `public_url = "https://..."`. Clients get a read-only view; publishing ideas,
refreshing data and the journal need the admin login. The Client Access page then shows a QR code.
