"""Point-in-time back-test of the BUY / HOLD / SELL model rating on the NIFTY 500.

At each month-start rating date D the engine sees only:
  - annual statements whose year ended at least 92 days before D (Indian listed companies publish annual
    results within 60 days of year-end, so this is conservative),
  - prices up to D (beta from the 156 weeks before D),
  - peer multiples rebuilt from those same statements and prices (Yahoo's own multiples are today's, so unused).
It then rates every stock and measures the total return (Yahoo adjusted close, dividends included) over the
next 6 and 12 months. Results go to data/backtest.json and are printed.

Known biases, stated in the output:
  - survivorship: the universe is today's NIFTY 500, so companies that fell out of the index are missing;
  - no forward EPS history: the P/E method uses trailing (latest annual) EPS at every date;
  - share counts are put in today's terms by detecting splits / bonuses as jumps in the share count;
  - the 10-year G-Sec yield and ERP are held at today's values.
Run: .venv\\Scripts\\python.exe jobs\\backtest_ratings.py   (needs data/fundamentals.json; downloads 5 years of prices)"""
import json
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf

from engine import valuation
from jobs.refresh import load_symbols

FIRST, LAST = date(2024, 7, 1), date(2026, 10, 1)
AVAIL_DAYS = 92
PRICES = Path("data/prices_5y.pkl")


def month_starts(a, b):
    d, out = a, []
    while d <= b:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def load_prices(symbols):
    if PRICES.exists() and (date.today() - date.fromtimestamp(PRICES.stat().st_mtime)).days < 1:
        return pd.read_pickle(PRICES)
    print("downloading 5 years of prices ...", flush=True)
    px = yf.download([s + ".NS" for s in symbols] + ["^CRSLDX", "INR=X"], period="5y", interval="1d",
                     auto_adjust=False, progress=False, threads=True)
    px.to_pickle(PRICES)
    return px


def adjusted_shares(sh, shares_today):
    """Put a share-count series (latest first) in today's terms by chaining split / bonus jumps (>1.4x or <0.7x)."""
    out, cum = [], 1.0
    s0 = next((x for x in sh if x), None)
    if s0 and shares_today:
        r = shares_today / s0
        if r > 1.4 or r < 0.7:
            cum = r
    prev = None
    for x in sh:
        if x is None:
            out.append(None)
            continue
        if prev is not None:
            r = prev / x
            if r > 1.4 or r < 0.7:
                cum *= r
        out.append(x * cum)
        prev = x
    return out


def point_in_time(f, d, price, fx):
    """Fundamentals as they could have been known on date d, with a synthesised summary block."""
    info = f.get("info") or {}
    cur = info.get("financialCurrency")
    mult = fx if cur == "USD" and fx else 1.0
    g = {"info": {k: info.get(k) for k in ("sector", "industry", "longName")}, "fetched_at": None}
    for t in ("fin", "bs", "cf"):
        tab = f.get(t)
        if not tab:
            g[t] = None
            continue
        keep = [i for i, y in enumerate(tab["years"]) if date.fromisoformat(y) + timedelta(days=AVAIL_DAYS) <= d]
        if not keep:
            g[t] = None
            continue
        g[t] = {"years": [tab["years"][i] for i in keep],
                "rows": {r: [None if (i >= len(vals) or vals[i] is None) else
                             (vals[i] if r in ("Ordinary Shares Number", "Share Issued") else vals[i] * mult) for i in keep]
                         for r, vals in tab["rows"].items()}}
    fin, bs, cf = g["fin"], g["bs"], g["cf"]
    if not fin or not bs:
        return None
    sh_raw = bs["rows"].get("Ordinary Shares Number") or bs["rows"].get("Share Issued")
    if not sh_raw:
        return None
    full_sh = (f["bs"]["rows"].get("Ordinary Shares Number") or [None])
    today_sh = info.get("sharesOutstanding") or next((x for x in full_sh if x), None)
    # chain from the full (today's) series so splits after d are captured, then take the columns known at d
    full_adj = adjusted_shares(full_sh, today_sh)
    yrs_full = f["bs"]["years"]
    adj = [full_adj[yrs_full.index(y)] if y in yrs_full and yrs_full.index(y) < len(full_adj) else None for y in bs["years"]]
    if not adj or not adj[0]:
        return None
    bs["rows"]["Ordinary Shares Number"] = adj
    v = lambda t, r: (t["rows"].get(r) or [None])[0] if t else None
    shares = adj[0]
    ni, eq = v(fin, "Net Income"), v(bs, "Stockholders Equity")
    op, da, eb_row = v(fin, "Operating Income"), v(fin, "Reconciled Depreciation"), v(fin, "EBITDA")
    ebitda = op + da if op is not None and da is not None else eb_row
    debt, cash, mi = v(bs, "Total Debt") or 0.0, v(bs, "Cash Cash Equivalents And Short Term Investments") or 0.0, v(bs, "Minority Interest") or 0.0
    divs = v(cf, "Cash Dividends Paid")
    eps = ni / shares if ni is not None else None
    mcap = price * shares
    ev = mcap + debt - cash + mi
    g["info"].update(
        currentPrice=price, sharesOutstanding=shares, marketCap=mcap, financialCurrency="INR",
        trailingEps=eps, forwardEps=None, bookValue=eq / shares if eq else None,
        dividendRate=-divs / shares if divs else 0.0, totalDebt=debt, totalCash=cash, ebitda=ebitda,
        enterpriseToEbitda=ev / ebitda if ebitda and ebitda > 0 else None,
        trailingPE=price / eps if eps and eps > 0 else None, forwardPE=price / eps if eps and eps > 0 else None,
    )
    return g


def close_on(series, d):
    s = series.loc[:pd.Timestamp(d)].dropna()
    return (float(s.iloc[-1]), s.index[-1]) if len(s) else (None, None)


def main():
    symbols = load_symbols()
    funds = json.loads(Path("data/fundamentals.json").read_text(encoding="utf-8"))["companies"]
    px = load_prices(symbols)
    close, adj = px["Close"], px["Adj Close"]
    idx = close["^CRSLDX"].dropna()
    fxs = close["INR=X"].dropna()
    last_day = adj.index[adj[[s + ".NS" for s in symbols if s + ".NS" in adj]].notna().mean(axis=1) >= 0.5][-1]
    weekly = adj.resample("W-FRI").last().pct_change(fill_method=None)
    lo_b, hi_b = valuation.P["beta_clip"]

    results = []
    for d in month_starts(FIRST, LAST):
        ts = pd.Timestamp(d)
        fx, _ = close_on(fxs, d)
        pit, prices, betas = {}, {}, {}
        wk = weekly.loc[:ts].iloc[-156:]
        for sym in symbols:
            t = sym + ".NS"
            if t not in close or sym not in funds:
                continue
            p, when = close_on(close[t], d)
            if p is None or (ts - when).days > 7:
                continue
            g = point_in_time(funds[sym], d, p, fx)
            if not g:
                continue
            pit[sym], prices[sym] = g, p
            x = wk[[t, "^CRSLDX"]].dropna()
            if len(x) >= 52:
                c = np.cov(x[t], x["^CRSLDX"])
                betas[sym] = (min(max(0.67 * c[0, 1] / c[1, 1] + 0.33, lo_b), hi_b), float(np.corrcoef(x[t], x["^CRSLDX"])[0, 1] ** 2))
        valuation.AS_OF = d
        rated = valuation.value_all(pit, prices, betas, usdinr=None)
        valuation.AS_OF = None

        row = {"date": d.isoformat(), "n": len(rated), "counts": {}}
        i0, _ = close_on(idx, d)
        for h in (6, 12):
            end = date(d.year + (d.month + h - 1) // 12, (d.month + h - 1) % 12 + 1, 1)
            if pd.Timestamp(end) > last_day + pd.Timedelta(days=3):
                continue
            rets = {}
            for sym, r in rated.items():
                t = sym + ".NS"
                a0, w0 = close_on(adj[t], d)
                a1, w1 = close_on(adj[t], end)
                if a0 and a1 and w1 > w0:
                    rets[sym] = a1 / a0 - 1
            i1, _ = close_on(idx, end)
            groups = {}
            for k in ("BUY", "HOLD", "SELL", "NOT RATED"):
                v = [rets[s] for s, r in rated.items() if r["rating"] == k and s in rets]
                groups[k] = dict(n=len(v), mean=float(np.mean(v)) if v else None, median=float(np.median(v)) if v else None,
                                 beat_universe=None)
            allv = list(rets.values())
            uni = float(np.mean(allv)) if allv else None
            for k, gk in groups.items():
                if gk["n"]:
                    v = [rets[s] for s, r in rated.items() if r["rating"] == k and s in rets]
                    gk["beat_universe"] = float(np.mean([x > uni for x in v]))
            mos = [(r.get("margin_of_safety"), rets[s]) for s, r in rated.items() if s in rets and r.get("margin_of_safety") is not None]
            spearman = float(pd.Series([m for m, _ in mos]).rank().corr(pd.Series([x for _, x in mos]).rank())) if len(mos) > 10 else None
            row[f"h{h}"] = dict(end=end.isoformat(), universe_mean=uni, nifty500=(i1 / i0 - 1) if i0 and i1 else None,
                                groups=groups, spearman_mos_vs_return=spearman, n_returns=len(rets))
        row["counts"] = {k: sum(1 for r in rated.values() if r["rating"] == k) for k in ("BUY", "HOLD", "SELL", "NOT RATED")}
        results.append(row)
        h12 = row.get("h12")
        f = lambda x: "  n/a " if x is None else f"{x * 100:+6.1f}%"
        if h12:
            gg = h12["groups"]
            print(f"{d}  rated {row['n']:>3} B/H/S {row['counts']['BUY']:>3}/{row['counts']['HOLD']:>3}/{row['counts']['SELL']:>3}  12m: BUY {f(gg['BUY']['mean'])} HOLD {f(gg['HOLD']['mean'])} SELL {f(gg['SELL']['mean'])}  all {f(h12['universe_mean'])}  NIFTY500 {f(h12['nifty500'])}  rank corr {h12['spearman_mos_vs_return']:+.2f}", flush=True)
        else:
            print(f"{d}  rated {row['n']:>3} B/H/S {row['counts']['BUY']:>3}/{row['counts']['HOLD']:>3}/{row['counts']['SELL']:>3}  (12-month return not yet available)", flush=True)

    summary = {}
    for h in (6, 12):
        rows = [r[f"h{h}"] for r in results if f"h{h}" in r]
        if not rows:
            continue
        def avg(k):
            v = [r["groups"][k]["mean"] for r in rows if r["groups"][k]["mean"] is not None]
            return float(np.mean(v)) if v else None
        spread = [r["groups"]["BUY"]["mean"] - r["groups"]["SELL"]["mean"] for r in rows
                  if r["groups"]["BUY"]["mean"] is not None and r["groups"]["SELL"]["mean"] is not None]
        summary[f"h{h}"] = dict(
            dates=len(rows), first=rows[0]["end"], buy=avg("BUY"), hold=avg("HOLD"), sell=avg("SELL"),
            universe=float(np.mean([r["universe_mean"] for r in rows])), nifty500=float(np.mean([r["nifty500"] for r in rows if r["nifty500"] is not None])),
            buy_minus_sell=float(np.mean(spread)) if spread else None,
            buy_beat_sell_share=float(np.mean([s > 0 for s in spread])) if spread else None,
            buy_beat_universe=float(np.mean([r["groups"]["BUY"]["mean"] > r["universe_mean"] for r in rows if r["groups"]["BUY"]["mean"] is not None])),
            spearman=float(np.mean([r["spearman_mos_vs_return"] for r in rows if r["spearman_mos_vs_return"] is not None])),
        )
    out = dict(run_on=date.today().isoformat(), first_date=FIRST.isoformat(), last_date=LAST.isoformat(), price_data_to=str(last_day.date()),
               method=__doc__, results=results, summary=summary)
    Path("data/backtest.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nSUMMARY (averages across rating dates; overlapping windows, so dates are not independent)")
    for h, s in summary.items():
        print(f"{h}: {s['dates']} dates | BUY {s['buy']:+.1%} HOLD {s['hold']:+.1%} SELL {s['sell']:+.1%} | all stocks {s['universe']:+.1%} | NIFTY 500 index {s['nifty500']:+.1%}"
              f" | BUY-SELL {s['buy_minus_sell']:+.1%}, BUY beat SELL on {s['buy_beat_sell_share']:.0%} of dates, BUY beat all stocks on {s['buy_beat_universe']:.0%} | rank corr {s['spearman']:+.2f}")


if __name__ == "__main__":
    main()
