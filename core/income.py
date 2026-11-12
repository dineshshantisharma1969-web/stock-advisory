"""Forward test: a Nifty weekly iron condor, traded by fixed rules and scored on REAL option prices.

Why: in the Nov 2026 research (5 years of NSE F&O bhavcopy) this was the only option structure that made
money in every year - and only thinly (64% of weeks profitable, profit factor about 1.4, best of 32
variants tried, so partly luck). It is here to be proven or disproven on live weeks, not to be published.

The rules (frozen - do not tune them after seeing results):
  - Entry: at the close of the first session after a weekly Nifty expiry, for the next weekly expiry.
  - Strikes: expected move = Nifty close x India VIX x sqrt(days to expiry / 365).
    Sell a put and a call 0.75 expected moves from the close; buy a put and a call one further expected
    move out as protection. Strikes rounded to 50 points.
  - Exit: if Nifty CLOSES at or beyond either sold strike before expiry, close all four legs at that
    day's closing prices. Otherwise hold to expiry and settle on the Nifty close.
  - One lot. Costs: 1 point per leg each time a leg is traded.
  - Maximum loss is known on entry: strike gap minus the credit received.

Every price comes from NSE's daily F&O bhavcopy, so nothing is typed in by hand. Missed days are caught
up on the next run: the outcome depends only on the published files, never on when the button was pressed.
"""
from __future__ import annotations

import io
import math
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import nse

BOOK = "fwd-condor"
SETUP = "Nifty weekly iron condor (0.75 expected moves, hedged)"
Z_SHORT, Z_WIDE, STEP, SLIP, LOT = 0.75, 1.0, 50, 1.0, 65
TUESDAY_FROM = date(2025, 9, 1)     # NSE moved the Nifty weekly expiry from Thursday to Tuesday
GO_LIVE = date(2026, 11, 13)        # only cycles entered on/after this date are traded
MIN_DAYS, MAX_DAYS = 3, 9
URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def expiry_weekday(d: date) -> int:
    return 1 if d >= TUESDAY_FROM else 3


def parse_chain(raw: bytes) -> pd.DataFrame:
    """F&O bhavcopy bytes -> Nifty options: one row per (expiry, type, strike) with price and Nifty close."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df = df[(df["TckrSymb"].astype(str).str.strip() == "NIFTY") & (df["FinInstrmTp"].astype(str).str.strip() == "IDO")]
    close = pd.to_numeric(df["ClsPric"], errors="coerce")
    settle = pd.to_numeric(df["SttlmPric"], errors="coerce")
    vol = pd.to_numeric(df["TtlTradgVol"], errors="coerce").fillna(0)
    return pd.DataFrame({
        "date": pd.to_datetime(df["TradDt"]).dt.date, "expiry": pd.to_datetime(df["XpryDt"]).dt.date,
        "type": df["OptnTp"].astype(str).str.strip(), "strike": pd.to_numeric(df["StrkPric"], errors="coerce"),
        # an untraded strike has no real close: use the exchange's settlement price for it
        "price": np.where(vol > 0, close, settle), "spot": pd.to_numeric(df["UndrlygPric"], errors="coerce"),
    }).dropna(subset=["strike", "price"])


def fetch_chain(d: date, s=None) -> pd.DataFrame | None:
    """One day's Nifty option prices, or None on a holiday / not yet published (NSE answers 404)."""
    s = s or nse.session()
    r = s.get(URL.format(d=d), timeout=40)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    ch = parse_chain(r.content)
    return ch if len(ch) else None


def spot_of(chain: pd.DataFrame) -> float:
    return float(chain["spot"].dropna().iloc[0])


def quote(chain: pd.DataFrame, expiry: date, kind: str, strike: float) -> float | None:
    m = chain[(chain["expiry"] == expiry) & (chain["type"] == kind) & (chain["strike"] == strike)]
    return float(m["price"].iloc[0]) if len(m) else None


def _r(k: float) -> float:
    return round(k / STEP) * STEP


def strikes(spot: float, vix: float, days: int) -> dict:
    """vix as a fraction (0.14 = 14%)."""
    move = spot * vix * math.sqrt(days / 365)
    sp, sc = _r(spot - Z_SHORT * move), _r(spot + Z_SHORT * move)
    bp = min(_r(spot - (Z_SHORT + Z_WIDE) * move), sp - STEP)
    bc = max(_r(spot + (Z_SHORT + Z_WIDE) * move), sc + STEP)
    return {"sell_put": sp, "buy_put": bp, "sell_call": sc, "buy_call": bc, "expected_move": move}


LEGS = (("sell_put", "PE", -1), ("buy_put", "PE", +1), ("sell_call", "CE", -1), ("buy_call", "CE", +1))


def value(chain: pd.DataFrame, t: dict) -> float | None:
    """Points needed to buy the condor back at this day's closing prices."""
    v = 0.0
    for name, kind, side in LEGS:
        p = quote(chain, _d(t["expiry"]), kind, float(t[name]))
        if p is None:
            return None
        v -= side * p
    return v


def intrinsic(t: dict, spot: float) -> float:
    v = 0.0
    for name, kind, side in LEGS:
        k = float(t[name])
        v -= side * (max(k - spot, 0) if kind == "PE" else max(spot - k, 0))
    return v


def open_trade(chain: pd.DataFrame, expiry: date, vix: float, lot: int = LOT) -> dict | None:
    """The condor entered at this day's close. vix as a fraction. None if a leg has no price."""
    d0, spot = chain["date"].iloc[0], spot_of(chain)
    days = (expiry - d0).days
    if not (MIN_DAYS <= days <= MAX_DAYS and vix > 0):
        return None
    t = {"expiry": expiry.isoformat(), "entry_date": d0.isoformat(), "spot": round(spot, 2), "vix": round(vix * 100, 2),
         **{k: v for k, v in strikes(spot, vix, days).items() if k != "expected_move"}}
    v = value(chain, t)
    if v is None:
        return None
    credit = v - 4 * SLIP
    width = max(t["sell_put"] - t["buy_put"], t["buy_call"] - t["sell_call"])
    if credit <= 0 or width - credit <= 0:
        return None
    t.update({"credit": round(credit, 2), "max_loss": round(width - credit, 2), "lot_size": int(lot), "status": "open",
              "last_checked": d0.isoformat(), "book": BOOK})
    return t


def mark(t: dict, chain: pd.DataFrame) -> dict:
    """Apply one later session to an open trade. Returns the fields to update (always last_checked)."""
    d, spot = chain["date"].iloc[0], spot_of(chain)
    exp, credit = _d(t["expiry"]), float(t["credit"])
    upd = {"last_checked": d.isoformat()}
    if d >= exp:
        pnl, status = credit - intrinsic(t, spot), "expired"
    elif spot <= float(t["sell_put"]) or spot >= float(t["sell_call"]):
        v = value(chain, t)
        if v is None:
            return upd
        pnl, status = credit - v - 4 * SLIP, "stopped"
    else:
        v = value(chain, t)
        if v is not None:
            upd["open_pnl_points"] = round(credit - v, 2)
        return upd
    pnl = max(pnl, -float(t["max_loss"]))
    upd.update({"status": status, "exit_date": d.isoformat(), "exit_spot": round(spot, 2), "pnl_points": round(pnl, 2),
                "pnl_rupees": round(pnl * int(t.get("lot_size") or LOT), 0),
                "r_multiple": round(pnl / float(t["max_loss"]), 4), "open_pnl_points": None})
    return upd


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def previous_expiry_day(expiry: date) -> date:
    """The scheduled weekly expiry before this one (an expiry moved by a holiday still follows that weekday)."""
    d = expiry - timedelta(days=1)
    while d.weekday() != expiry_weekday(d) or (expiry - d).days < 4:
        d -= timedelta(days=1)
    return d


def fetch_vix(start: date) -> dict:
    """India VIX closes by date (fraction) from Yahoo Finance."""
    import yfinance as yf
    h = yf.Ticker("^INDIAVIX").history(start=start - timedelta(days=7), auto_adjust=False)["Close"].dropna()
    return {ix.date(): float(v) / 100 for ix, v in h.items()}


def step(client, today: date | None = None, fetch=None, vix_fn=fetch_vix, log=lambda m: None) -> dict:
    """Bring the condor book up to date: score open trades day by day, then open any cycle that is due.
    Idempotent. `fetch(day) -> chain | None` and `vix_fn(start) -> {date: vix}` are injectable for tests."""
    today = today or date.today()
    if fetch is None:
        s = nse.session()
        fetch = lambda d: fetch_chain(d, s)     # noqa: E731
    cache: dict = {}

    def chain(d):
        if d not in cache:
            cache[d] = fetch(d)
        return cache[d]

    rows = client.select("condor_trades", {"order": "expiry.asc"})
    out = {"closed": [], "opened": [], "open": 0}
    for t in rows:
        if t["status"] != "open":
            continue
        for d in _weekdays(_d(t["last_checked"]) + timedelta(days=1), today):
            ch = chain(d)
            if ch is None:
                continue
            upd = mark(t, ch)
            t.update(upd)
            client.update("condor_trades", {"expiry": t["expiry"]}, upd)
            if t["status"] != "open":
                out["closed"].append(t)
                break
    # ---- entries: find the latest published session, then every cycle whose entry day is on/after GO_LIVE
    latest = next((d for d in sorted(_weekdays(today - timedelta(days=10), today), reverse=True) if chain(d) is not None), None)
    if latest is None:
        log("NSE F&O bhavcopy: no file found for the last 10 days")
        return out
    have = {str(t["expiry"])[:10] for t in rows}
    vix = None
    for exp in sorted(e for e in set(chain(latest)["expiry"]) if e >= latest)[:2]:
        if exp.isoformat() in have:
            continue
        prev = previous_expiry_day(exp)
        d0 = next((d for d in _weekdays(prev + timedelta(days=1), min(today, exp - timedelta(days=1)))
                   if chain(d) is not None), None)
        if d0 is None or d0 < GO_LIVE:
            continue
        if vix is None:
            vix = vix_fn(d0)
        if d0 not in vix:
            log(f"Condor for {exp}: India VIX for {d0} not available yet - will retry on the next run")
            continue
        t = open_trade(chain(d0), exp, vix[d0], _lot(client))
        if t is None:
            log(f"Condor for {exp}: skipped (no usable prices or credit on {d0})")
            continue
        for d in _weekdays(d0 + timedelta(days=1), today):      # catch up if the entry day was a while ago
            ch = chain(d)
            if ch is not None and t["status"] == "open":
                t.update(mark(t, ch))
        client.insert("condor_trades", [t], upsert_on="expiry")
        out["opened"].append(t)
    out["open"] = sum(1 for t in rows if t["status"] == "open") + sum(1 for t in out["opened"] if t["status"] == "open")
    return out


def _lot(client) -> int:
    try:
        r = client.select("fo_lots", {"symbol": "eq.NIFTY"})
        return int(r[0]["lot_size"]) if r else LOT
    except Exception:
        return LOT


def describe(t: dict) -> str:
    return (f"Sell {float(t['sell_put']):.0f} PE / buy {float(t['buy_put']):.0f} PE, "
            f"sell {float(t['sell_call']):.0f} CE / buy {float(t['buy_call']):.0f} CE")
