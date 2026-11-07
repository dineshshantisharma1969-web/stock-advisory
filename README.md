# Stock Advisory (personal, NIFTY 500)

Phone web app: market regime, breadth (advance-decline, % above moving averages, new highs/lows,
McClellan), macro and country-risk gauges, event notes, and per-stock technicals (RSI, MACD, moving
averages, ADX, ATR, relative strength) for the NIFTY 500. Rules and limits come from the skill in
`C:\Users\Dinesh Sharma\.claude\skills\india-equity-advisory`.

Data: Yahoo Finance through `yfinance`. Unofficial, NSE quotes 15 minutes delayed, **personal use only**.
Analysis for your own decisions, not investment advice.

## Run on this PC

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python.exe jobs\refresh.py
```

```bash
.venv\Scripts\python.exe -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` on the PC, or `http://<PC-IP>:8000` from a phone on the same Wi-Fi.
On the phone, use the browser menu "Add to Home screen" to install it.

Without `APP_PASSWORD` set, the app is open (fine on your own PC). Set it before exposing the app anywhere.

## Use it on your phone at home (same Wi-Fi)

1. Create `config\app_password.txt` with your chosen password on one line (kept out of Git).
2. Double-click `jobs\run_server_wifi.cmd`. It prints the address to open (like `http://192.168.1.5:8000`).
   Keep the window open. If Windows asks about network access for Python, allow **private networks only**.
3. On the phone, open that address, log in, then use the browser menu **Add to Home screen**. It then opens
   like an app, full screen, with its own icon.

It works only while the PC is on and the phone is on the same Wi-Fi. For use anywhere, deploy to the cloud (below).

## Deploy to Render or Railway

1. Put this folder in a Git repository (GitHub) and connect it to Render (uses `render.yaml`) or
   Railway (uses `Procfile` and `runtime.txt`).
2. Set the environment variable `APP_PASSWORD` to a strong password. Optional: `APP_SECRET` (random string),
   `REFRESH_ON_START=1` (the server tries to build its own snapshot on first start).
3. Yahoo often throttles or blocks cloud servers. If the cloud refresh fails, build the snapshot here
   and upload it:

```bash
.venv\Scripts\python.exe jobs\push_snapshot.py https://your-app.onrender.com YOUR_APP_PASSWORD
```

4. Free tiers sleep and have no permanent disk: after a restart the snapshot is gone until the next
   refresh or upload. A small paid plan or a persistent disk fixes that.

## Ratings (BUY / HOLD / SELL)

Weekly, or after results come out, refresh company financials (about 2 minutes for 500 companies):

```bash
.venv\Scripts\python.exe jobs\fundamentals.py
```

The daily `jobs\refresh.py` then values every stock and rates it. Rules and inputs are in
`config\valuation_params.json` (update `gsec_10y` when the 10-year yield moves). A model rating under stated
rules, the same for anyone; not advice fitted to your finances.

## Daily routine

Automatic (Windows Task Scheduler, set up 7-Nov-2026; runs only while you are logged in and the PC is on):
- `StockAdvisory\DailyRefresh`: weekdays 6:00 pm, `jobs\run_daily.cmd` (prices, ratings, ratings log, track record).
- `StockAdvisory\WeeklyFundamentals`: Saturdays 10:00 am, `jobs\run_weekly.cmd` (financials, then refresh).
- Output of both goes to `data\refresh.log`. Missed runs (PC off) are simply skipped.

By hand, when something happens: update `config\manual_inputs.json` (G-Sec yield, FII/DII flows) and
`config\events.json`.

To stop the schedule: `schtasks /Delete /TN "StockAdvisory\DailyRefresh" /F` (and the same for WeeklyFundamentals).

## Layout

```
engine/indicators.py   RSI, MACD, SMA, ATR, ADX, trend, swing-rule watch flags, chart series
engine/breadth.py      advances/declines, A/D line, % above DMAs, new highs/lows, McClellan
engine/macro.py        USD/INR, Brent, US 10Y, dollar index, gold, S&P 500, India VIX + manual inputs + events
jobs/refresh.py        builds data/snapshot.json
jobs/push_snapshot.py  uploads the snapshot to the cloud app
server/app.py          FastAPI: API + static web, password gate, snapshot upload, refresh trigger
web/                   phone-first interface (no build step), installable
config/                NIFTY 500 list, manual inputs, event notes
```

## Not yet built

Valuation engine and the buy / hold / sell model rating per stock; F&O view in the app; TradingView chart
embed (whether NSE symbols display in the free embed is unchecked); automated FII/DII source.
