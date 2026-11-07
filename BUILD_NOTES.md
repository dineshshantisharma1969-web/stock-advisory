# Stock advisory app: build notes

Rules for the build are in the skill
`C:\Users\Dinesh Sharma\.claude\skills\india-equity-advisory\` (read `SKILL.md` and `references\app-build.md`).

## Decisions
- Phone web app (installs to the home screen), developed on this PC, deployed to **Railway or Render**.
- Personal use only, so the cloud app needs a **password gate** (`APP_PASSWORD` env var).
- Data: **yfinance** for now (15 min delayed). Universe: **NIFTY 500** (`config/nifty500_symbols.txt`).
- Yahoo often throttles cloud servers: the PC computes a snapshot and can **upload it** to the cloud app.
- The user wants: RSI, MACD, advance-decline ratio, market breadth, and a macro layer for geopolitical and
  country factors (liquidity, rates, rupee, crude). All of it is context, not back-tested signals.
- Clear BUY / HOLD / SELL model rating per stock (rule in SKILL.md Part 3): second version.

## Done (7-Nov-2026)
- Whole first version written: `engine/` (indicators, breadth, macro), `jobs/refresh.py` (builds
  `data/snapshot.json`), `jobs/push_snapshot.py`, `server/app.py` (FastAPI, password gate, snapshot upload,
  refresh trigger), `web/` (Market, Stocks, Watch, Macro tabs, stock detail, installable), deploy files
  (`Procfile`, `render.yaml`, `runtime.txt`), `README.md`, `.claude/launch.json` (preview server "advisory-server").
- `jobs/refresh.py` tested end to end with yesterday's temporary yfinance copy: 486 of 500 stocks, 4.7 MB snapshot,
  data as of 2026-11-05 (Yahoo's 6-Nov stock bars were empty at run time; the job now trims trailing empty rows).

- Packages installed by the user (7-Nov, 08:10): fastapi 0.162, uvicorn 0.54, pandas 3.1.1, yfinance 1.8.0.
  pip install is refused for Claude by the permission classifier; the user runs it.
- Server started via `.claude/launch.json` (advisory-server, 127.0.0.1:8000) and checked in the browser at phone
  size: Market, Stocks, Watch, Macro tabs and the ICICIBANK stock page render; no console errors. Service worker
  changed to network-first so edits show immediately.

- Valuation engine and BUY / HOLD / SELL model rating built (7-Nov): `jobs/fundamentals.py` (statements for all
  500, about 2 minutes, run weekly), `engine/valuation.py`, `config/valuation_params.json`; ratings shown on the
  Market tab (counts), Stocks tab (badge, filter, margin-of-safety sort) and stock page (rating, buy-below /
  sell-above, scenario table, 12/36-month targets, reverse DCF, quality gate, warnings, assumptions).
  First run: 37 BUY, 198 HOLD, 213 SELL, 38 NOT RATED.
- Safeguards added after checking the first results: US-dollar statements converted (Infosys, HCL Tech);
  incomplete latest year columns dropped and growth measured over real elapsed time (Nestle India's year-end
  change); statements older than 18 months -> NOT RATED; minority interests above 25% of equity -> NOT RATED
  (holding structures such as BBTC); BUY needs two or more methods and methods within 2x of each other;
  lenders use two-stage residual income; insurers NOT RATED.

- Sliders on the stock page (7-Nov): `web/model.js` ports the engine to the phone; verified identical to the
  server for all 428 adjustable stocks at default positions (same rating, fair value within Rs 1).
- Point-in-time back-test of the ratings (7-Nov): `jobs/backtest_ratings.py` -> `data/backtest.json`, shown on the
  Market tab. 28 monthly dates Jul-2024 to Oct-2026, about 450-500 stocks each. 12-month average total return:
  BUY +8.1%, HOLD +7.5%, SELL +3.5%, all rated +5.0% (17 dates). The two non-overlapping years disagree:
  from Jul-2024 BUY -0.2% vs all +8.3%; from Jul-2025 BUY +10.5% vs all +4.3%. Verdict: mixed, not a proven edge.
  Biases: survivorship (today's members), trailing EPS for P/E, constant G-Sec and ERP.
- Live ratings log started (7-Nov): `data/ratings_log.jsonl`, one row per stock per data date, append-only,
  hash-chained; written by `jobs/refresh.py`. This, not the back-test, becomes the track record.

- Scheduled tasks (7-Nov): `StockAdvisory\DailyRefresh` weekdays 18:00 and `StockAdvisory\WeeklyFundamentals`
  Saturdays 10:00, via `jobs/run_daily.cmd` / `run_weekly.cmd`, log in `data/refresh.log`. Test run OK (exit 0).
  First attempt with `schtasks /TR` failed (0x80070002): the space in the user's path broke the command; the
  tasks are registered with `Register-ScheduledTask` and `cmd.exe /c "<path>"` instead.
- Live track record (7-Nov): `track_record()` in `jobs/refresh.py` measures every logged rating against the
  latest close; shown on the Market tab. First returns appear after the next trading day's refresh.
- Phone (7-Nov): app icon designed and rendered by `tools/make_icons.py` (192/512 any + maskable, 180 Apple,
  favicon); manifest and Apple meta tags added. Wi-Fi start script `jobs/run_server_wifi.cmd` (needs
  `config/app_password.txt`). Login cookie fixed: Secure only over HTTPS, otherwise phones on plain-HTTP
  Wi-Fi lost the session.

- Render deployment prepared (7-Nov): `render.yaml` (free plan, Singapore, ALLOW_REFRESH=0, APP_PASSWORD set in
  Render, APP_SECRET generated); server reads DATA_DIR; the cloud copy gets data only by upload, which
  `run_daily.cmd` / `run_weekly.cmd` now do after each refresh via `jobs/push_snapshot.py` (reads
  `config/cloud_url.txt` and `config/app_password.txt`, both git-ignored; skips if no URL). Back-test is embedded
  in the snapshot. Cloud mode simulated locally and passed. Git repo created, first commit 16cc4f5 (main).
  Waiting on the user: GitHub private repo + push, Render Blueprint deploy, then the URL for config/cloud_url.txt.
  Free plan caveat: sleeps when idle and loses its files on restart -> empty until the next upload.

## Monetisation (asked 7-Nov: 14-day trial plan) - NOT started
**Decision (Dinesh, 7-Nov-2026): stay personal for now; keep logging ratings to build the live track record,
revisit monetisation later.** The log only grows on days `jobs/refresh.py` runs (daily scheduling not set up).
Prerequisites when it is revisited (see skill `references/compliance-commercial.md`):
- Selling BUY/HOLD/SELL ratings to others, including free trial users, needs SEBI Research Analyst registration
  (or a tie-up with a registered entity) before the first outside user.
- Yahoo's terms bar commercial use: a licensed data source is needed before charging.
- Hypothetical back-test results must not be used as marketing performance; live results go through PaRRVA.

## Known limits of the ratings
- Bank inputs (EPS, book value) come from Yahoo summary fields, which mixed bases for HDFC Bank; private banks
  mostly rate SELL at the current cost of equity. Treat bank ratings with care.
- Promoter pledge, PSU status, holding-company discounts and analyst judgment are not captured.
- Exit multiples often imply growth above the risk-free rate (warned on each stock).

## Next, in order
1. Sliders on the stock page to change growth, margin and discount rate and see the value move.
2. F&O index view in the app (from the skill's `fno_index_view.py`).
3. Deploy to Render or Railway (user's accounts; set APP_PASSWORD); test `push_snapshot.py`.
4. Check whether TradingView's free embed shows NSE symbols; find a permitted FII/DII flows source.
