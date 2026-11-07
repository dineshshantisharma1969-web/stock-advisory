"""Build data/snapshot.json: index readings, breadth, macro gauges, per-stock technicals and chart series.
Source: Yahoo Finance via yfinance (unofficial, NSE quotes 15 minutes delayed, personal use only).
Run: .venv\\Scripts\\python.exe jobs\\refresh.py          (about 2-4 minutes for the NIFTY 500)"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf

from engine import breadth, fno, indicators, macro, valuation

INDICES = {"^NSEI": "NIFTY 50", "^CRSLDX": "NIFTY 500", "^NSEBANK": "BANK NIFTY"}


def load_symbols(path="config/nifty500_symbols.txt"):
    raw = " ".join(l for l in Path(path).read_text(encoding="utf-8").splitlines() if not l.startswith("#"))
    return sorted({s.strip() for s in raw.replace(",", " ").split() if s.strip()})


def compute_betas(px, tickers, index="^CRSLDX", weeks=156):
    """Blume-adjusted beta of weekly returns against the NIFTY 500 over the last 3 years, clipped to the stated range."""
    lo, hi = valuation.P["beta_clip"]
    wk = px["Adj Close"][tickers + [index]].resample("W-FRI").last().pct_change(fill_method=None).iloc[-weeks:]
    out = {}
    for t in tickers:
        x = wk[[t, index]].dropna()
        if len(x) < 52:
            continue
        c = np.cov(x[t], x[index])
        raw = c[0, 1] / c[1, 1]
        r2 = float(np.corrcoef(x[t], x[index])[0, 1] ** 2)
        out[t[:-3]] = (min(max(0.67 * raw + 0.33, lo), hi), r2)
    return out


def log_ratings(vals, as_of, path=Path("data/ratings_log.jsonl")):
    """Append one row per rated stock for this data date, once. Each row carries the hash of the previous row, so a
    later edit or deletion is detectable: this is the live, forward track record (unlike the back-test)."""
    path.parent.mkdir(exist_ok=True)
    last_hash, logged_dates = "genesis", set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                last_hash = rec["hash"]
                logged_dates.add(rec["as_of"])
    if as_of in logged_dates:
        return 0
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for sym in sorted(vals):
            r = vals[sym]
            rec = dict(as_of=as_of, symbol=sym, rating=r["rating"], price=r["price"], fair_value=r.get("fair_value"),
                       buy_below=r.get("buy_below"), sell_above=r.get("sell_above"), note=r.get("rating_note"),
                       engine="valuation.py + valuation_params.json", prev=last_hash)
            rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
            last_hash = rec["hash"]
            f.write(json.dumps(rec) + "\n")
            n += 1
    return n


def track_record(latest_close, idx_close, latest_date, path=Path("data/ratings_log.jsonl")):
    """Measure every logged rating against actual prices since it was issued (price return, dividends excluded)."""
    if not path.exists():
        return None
    by_date = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            by_date.setdefault(rec["as_of"], []).append(rec)
    idx = idx_close.dropna()
    rows = []
    for d, recs in sorted(by_date.items()):
        rets = {r["symbol"]: latest_close[r["symbol"]] / r["price"] - 1
                for r in recs if r.get("price") and latest_close.get(r["symbol"])}
        allv = list(rets.values())
        uni = float(np.mean(allv)) if allv else None
        i0 = idx.loc[:pd.Timestamp(d)]
        groups = {}
        for k in ("BUY", "HOLD", "SELL"):
            v = [rets[r["symbol"]] for r in recs if r["rating"] == k and r["symbol"] in rets]
            groups[k] = dict(n=len(v), mean=float(np.mean(v)) if v else None, median=float(np.median(v)) if v else None,
                             up=float(np.mean([x > 0 for x in v])) if v else None,
                             beat_all=float(np.mean([x > uni for x in v])) if v and uni is not None else None)
        buys = sorted(({"symbol": r["symbol"], "price": r["price"], "latest": latest_close.get(r["symbol"]),
                        "ret": rets.get(r["symbol"])} for r in recs if r["rating"] == "BUY"),
                      key=lambda x: -(x["ret"] if x["ret"] is not None else -9))
        rows.append(dict(as_of=d, days=(latest_date - pd.Timestamp(d)).days, n=len(rets), all=uni,
                         nifty500=float(idx.iloc[-1] / i0.iloc[-1] - 1) if len(i0) else None, groups=groups, buys=buys))
    return dict(latest=latest_date.strftime("%Y-%m-%d"), rows=rows,
                note="Price return from the logged price to the latest close; dividends excluded. Ratings as issued, never revised.")


def index_snapshot(df):
    o = indicators.compute(df)
    x = o.iloc[-1]
    c = o["Close"]
    return dict(close=round(float(x.Close), 2), chg=round(float((x.Close / o["Close"].iloc[-2] - 1) * 100), 2),
                rsi=round(float(x.rsi), 1), sma50=round(float(x.sma50), 0), sma200=round(float(x.sma200), 0),
                vs_200dma=round(float((x.Close / x.sma200 - 1) * 100), 1), adx=round(float(x.adx), 1),
                ret_1m=round(float((x.Close / c.iloc[-22] - 1) * 100), 1), ret_3m=round(float((x.Close / c.iloc[-64] - 1) * 100), 1),
                hi52=round(float(o["High"].iloc[-252:].max()), 0), lo52=round(float(o["Low"].iloc[-252:].min()), 0),
                series=indicators.chart(o, 120))


def main():
    t0 = time.time()
    symbols = load_symbols()
    stock_tickers = [s + ".NS" for s in symbols]
    print(f"downloading {len(stock_tickers)} stocks + {len(INDICES)} indices (3 years) ...")
    px = yf.download(stock_tickers + list(INDICES), period="3y", interval="1d", auto_adjust=False, progress=False, threads=True)
    print(f"downloading macro series ...")
    mpx = yf.download([t[0] for t in macro.TICKERS.values()], period="2y", interval="1d", auto_adjust=False, progress=False, threads=True)

    # Yahoo sometimes returns a trailing row that is empty for most NSE stocks (while indices and currencies have
    # values). Cut every frame at the last date on which at least half the stocks have a close, so breadth, index
    # and stock readings all describe the same session.
    coverage = px["Close"][stock_tickers].notna().mean(axis=1)
    last_good = coverage[coverage >= 0.5].index[-1]
    dropped = [d.strftime("%Y-%m-%d") for d in px.index[px.index > last_good]]
    px = px.loc[:last_good]
    if dropped:
        print(f"dropped trailing rows with too few stock closes: {dropped}")

    def frame(t):
        return pd.DataFrame({k: px[k][t] for k in ["Open", "High", "Low", "Close", "Volume"]}).dropna()

    n500 = frame("^CRSLDX")
    market_ok = bool(n500["Close"].iloc[-1] > n500["Close"].rolling(200).mean().iloc[-1])
    indices = {name: index_snapshot(frame(t)) for t, name in INDICES.items()}

    stocks, charts, skipped = [], {}, []
    for sym, t in zip(symbols, stock_tickers):
        try:
            df = frame(t)
            df = df[df["Volume"] > 0]
            if len(df) < 260:
                skipped.append(sym)
                continue
            o = indicators.compute(df)
            snap = indicators.snapshot(o, market_ok)
            snap["symbol"] = sym
            stocks.append(snap)
            charts[sym] = indicators.chart(o)
        except Exception as e:  # one bad ticker must not stop the run
            skipped.append(f"{sym} ({type(e).__name__})")
    ret6 = pd.Series({s["symbol"]: s["ret_6m"] for s in stocks}).dropna()
    rs_rank = ret6.rank(pct=True) * 100
    for s in stocks:
        s["rs_rank"] = round(float(rs_rank.get(s["symbol"], np.nan)), 0) if s["symbol"] in rs_rank else None

    kept = [s + ".NS" for s in symbols if s in charts]
    vals, fund_note = {}, "data/fundamentals.json not found: run jobs/fundamentals.py for ratings"
    fpath = Path("data/fundamentals.json")
    if fpath.exists():
        fdata = json.loads(fpath.read_text(encoding="utf-8"))
        fx = mpx["Close"]["INR=X"].dropna()
        vals = valuation.value_all(fdata["companies"], {s["symbol"]: s["close"] for s in stocks}, compute_betas(px, kept),
                                   usdinr=float(fx.iloc[-1]) if len(fx) else None)
        fund_note = f"statements fetched {fdata.get('updated_at', '?')[:10]} for {len(fdata['companies'])} companies"
        for s in stocks:
            r = vals.get(s["symbol"])
            s["rating"] = r["rating"] if r else "NOT RATED"
            s["fair_value"] = r.get("fair_value") if r else None
            s["mos"] = r.get("margin_of_safety") if r else None
    counts = {k: sum(1 for s in stocks if s.get("rating") == k) for k in ("BUY", "HOLD", "SELL", "NOT RATED")}
    logged = log_ratings(vals, px.index[-1].strftime("%Y-%m-%d")) if vals else 0
    if logged:
        print(f"ratings log: appended {logged} rows for {px.index[-1].date()}")

    try:
        fno_block = fno.build(px, mpx["Close"]["^INDIAVIX"], px.index[-1])
    except Exception as e:  # the F&O view must never stop the daily refresh
        fno_block = {"error": f"{type(e).__name__}: {e}"}
        print("F&O view failed:", fno_block["error"])

    br = breadth.compute(px["Close"][kept], px["Volume"][kept], n500["Close"])
    mc = macro.compute(mpx["Close"])

    as_of = px.index[-1].strftime("%Y-%m-%d")
    snapshot = dict(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        data_as_of=as_of,
        source="Yahoo Finance via yfinance; NSE quotes delayed 15 minutes; unofficial, personal use only",
        universe=dict(name="NIFTY 500 (list dated 30-Jun-2026)", requested=len(symbols), covered=len(stocks), skipped=skipped,
                      dropped_trailing_rows=dropped),
        market=dict(regime_up=market_ok, regime_rule="NIFTY 500 above its 200-day average", indices=indices, breadth=br, macro=mc),
        stocks=sorted(stocks, key=lambda s: s["symbol"]),
        valuations=vals,
        backtest=json.loads(Path("data/backtest.json").read_text(encoding="utf-8")) if Path("data/backtest.json").exists() else None,
        fno=fno_block,
        track_record=track_record({s["symbol"]: s["close"] for s in stocks}, n500["Close"], px.index[-1]),
        valuation_info=dict(note=fund_note, counts=counts, params=valuation.P,
                            method="Automated defaults from each company's statements and industry peers (rules in "
                                   "config/valuation_params.json and engine/valuation.py). A model rating under stated rules, "
                                   "the same for anyone; not advice fitted to your finances."),
        charts=charts,
        limits="Analysis for the user's own decisions. Not investment advice; not from a SEBI-registered adviser. "
               "Swing-rule triggers are watch flags: all three rules failed their back-test.",
    )
    Path("data").mkdir(exist_ok=True)
    with open("data/snapshot.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, separators=(",", ":"))
    size = os.path.getsize("data/snapshot.json") / 1e6
    print(f"done in {time.time() - t0:.0f}s: {len(stocks)} stocks, {len(skipped)} skipped, data as of {as_of}, "
          f"regime up={market_ok}, breadth={br['state']}, macro backdrop={mc['backdrop']}, ratings {counts}, snapshot {size:.1f} MB")
    if skipped:
        print("skipped:", ", ".join(skipped))


if __name__ == "__main__":
    main()
