"""Fetch annual statements and summary fields for every NIFTY 500 stock from Yahoo Finance into data/fundamentals.json.
Slow (several HTTP calls per company), so it runs separately from the daily price refresh: weekly, or after results season.
Resumable: companies fetched in the last 7 days are skipped unless --force is given. Saves progress every 25 companies.
Run: .venv\\Scripts\\python.exe jobs\\fundamentals.py [--force] [--only SYM1,SYM2]"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd
import yfinance as yf

from jobs.refresh import load_symbols

OUT = Path("data/fundamentals.json")
INFO_KEYS = ["longName", "sector", "industry", "currentPrice", "marketCap", "sharesOutstanding", "trailingEps", "forwardEps",
             "bookValue", "dividendRate", "totalDebt", "totalCash", "ebitda", "totalRevenue", "enterpriseValue",
             "enterpriseToEbitda", "trailingPE", "forwardPE", "priceToBook", "returnOnEquity", "heldPercentInsiders",
             "financialCurrency", "numberOfAnalystOpinions", "earningsGrowth", "revenueGrowth"]
ROWS = {
    "fin": ["Total Revenue", "Operating Income", "Reconciled Depreciation", "Pretax Income", "Tax Provision", "Net Income",
            "Interest Expense", "Gross Profit", "EBITDA"],
    "bs": ["Total Debt", "Cash Cash Equivalents And Short Term Investments", "Minority Interest", "Stockholders Equity",
           "Total Assets", "Total Liabilities Net Minority Interest", "Current Assets", "Current Liabilities",
           "Long Term Debt And Capital Lease Obligation", "Long Term Debt", "Ordinary Shares Number", "Share Issued",
           "Working Capital", "Retained Earnings", "Capital Stock", "Investmentin Financial Assets", "Long Term Equity Investment"],
    "cf": ["Capital Expenditure", "Operating Cash Flow", "Change In Working Capital", "Interest Paid Cff", "Cash Dividends Paid"],
}


def _table(df, rows):
    if df is None or df.empty:
        return None
    df = df.iloc[:, :4]
    return {"years": [c.strftime("%Y-%m-%d") for c in df.columns],
            "rows": {r: [None if pd.isna(v) else float(v) for v in df.loc[r]] for r in rows if r in df.index}}


def fetch(sym, tries=3):
    for attempt in range(tries):
        try:
            tk = yf.Ticker(sym + ".NS")
            info = tk.info or {}
            out = {"info": {k: info.get(k) for k in INFO_KEYS},
                   "fin": _table(tk.financials, ROWS["fin"]), "bs": _table(tk.balance_sheet, ROWS["bs"]),
                   "cf": _table(tk.cashflow, ROWS["cf"]), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if not out["info"].get("currentPrice") and not out["fin"]:
                raise ValueError("empty response")
            return sym, out, None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
            time.sleep(2 + 3 * attempt)
    return sym, None, err


def main():
    force = "--force" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1].upper().split(",")
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"companies": {}, "errors": {}}
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    symbols = only or load_symbols()
    todo = [s for s in symbols if force or only or s not in data["companies"]
            or datetime.fromisoformat(data["companies"][s]["fetched_at"]) < cutoff]
    print(f"{len(symbols)} symbols, {len(todo)} to fetch", flush=True)
    t0, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fetch, s) for s in todo]
        for f in as_completed(futs):
            sym, out, err = f.result()
            done += 1
            if out:
                data["companies"][sym] = out
                data["errors"].pop(sym, None)
            else:
                data["errors"][sym] = err
            if done % 25 == 0 or done == len(todo):
                data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                OUT.parent.mkdir(exist_ok=True)
                OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
                print(f"{done}/{len(todo)} in {time.time() - t0:.0f}s, errors so far {len(data['errors'])}", flush=True)
    print(f"done: {len(data['companies'])} companies stored, {len(data['errors'])} errors"
          + (f" ({', '.join(sorted(data['errors'])[:15])})" if data["errors"] else ""), flush=True)


if __name__ == "__main__":
    main()
