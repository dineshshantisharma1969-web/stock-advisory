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

`python -m pytest` runs 94 tests that check the maths against independent values: Gordon-growth DCF,
reverse-DCF round trip, put-call parity, a known bank EMI, hand-computed income tax, no-look-ahead
backtest fills, tamper detection in the idea book, and the Supabase backend against a simulated API.

## Strategies (v2)

Besides the six classic setups, four strategies use market-wide context computed across the Nifty 500
(no look-ahead): **breadth regime** (`mkt_ok`: at least 50% of stocks above their 200 DMA) and
**relative strength** (`rs_pct`: percentile of the 6-month return skipping the latest month).
Momentum leaders, MACD trend + regime and RS breakout exit on a **3x ATR trailing stop** (no fixed target);
RSI pullback + regime keeps a 2x ATR stop and 2R target. All v2 strategies are long-only.
Grade **A** now also requires a positive expectancy in the in-sample period, so a strategy that only
looks good in the recent months is held at B.

## Clients, tracks and publishing rules

`Home.py` builds the menu. Clients see: Welcome, My calls, Risk profile, Track record, market pages,
Q2 results and Personal finance. The research desk (screener, stock analysis, scanner, backtest, client
profiles, QR code) appears only after admin login; those pages also refuse clients who open them directly.

- **Risk profile:** 8 questions score 0-32 and map the client to tracks: Long-term (everyone), Swing
  (moderate score and a horizon over a year) and F&O (hard gates: trading experience, emergency fund,
  tolerance for a 20%+ loss, score 24+). Clients may save their answers; admins see them under Client profiles.
- **Publishing rule:** swing and F&O calls can be published only for setups graded **A** out-of-sample
  in the latest backtest on live data; fundamental calls only for "Strong idea" stocks that pass the
  quality gate. Each call is tagged with the tracks it is for and stays in the tamper-evident record.

## Zerodha Kite Connect (primary data source)

1. Subscribe at developers.kite.trade (₹500/month per API key) and create an app.
2. Set its **Redirect URL** to this app's address (e.g. `https://<your-app>.streamlit.app/`).
3. Add to Streamlit Secrets: `[kite]` with `api_key = "..."` and `api_secret = "..."`.
4. Each morning, as admin, tap **Connect Zerodha (daily login)** in the sidebar and log in on Zerodha's
   page; you return to the app connected until 6:00 am IST next day. The api_secret stays on the server.

When connected, prices (5 years daily) and F&O lot sizes come from Kite; otherwise Yahoo, then NSE.
Use Kite data on the research-desk side; showing live exchange data to clients may need an NSE licence.

## Data sources

Prices (5 years of daily history) come from Yahoo Finance first. If Yahoo refuses most stocks (it throttles shared cloud servers),
the app falls back to NSE's official daily bhavcopy: it appends the missing trading days to the saved
history, or builds the history from bhavcopies if nothing is saved. Bhavcopy prices are not adjusted for
splits or bonuses; the next successful Yahoo refresh re-syncs the adjusted history. A refresh that fails
never overwrites good data, and fundamentals that fail for a company keep that company's previous figures.

## Persistent storage (Supabase)

With Supabase configured, the idea book, trade journal, FII/DII history and the data cache are stored in
Supabase instead of local files, so nothing is lost when Streamlit Cloud restarts. The `ideas` table is
append-only in the database itself (update/delete/truncate blocked by trigger, `prev_hash` unique), and all
tables have row-level security with no policies, so only the server-side secret key can reach them.
Configure with `[supabase] url / key` in `.streamlit/secrets.toml` or `SUPABASE_URL` / `SUPABASE_KEY` env vars.

## Automatic daily refresh

`.github/workflows/refresh.yml` runs the tests and `refresh.py` at 19:35 IST on weekdays and saves to
Supabase. Add `SUPABASE_URL` and `SUPABASE_KEY` as GitHub Actions secrets. Use **Run workflow** on the
Actions tab for an immediate refresh.

## Sharing with clients

Deploy (e.g. Streamlit Community Cloud), then create `.streamlit/secrets.toml` with
`admin_password = "..."` and `public_url = "https://..."`. Clients get a read-only view; publishing ideas,
refreshing data and the journal need the admin login. The Client Access page then shows a QR code.
