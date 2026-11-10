"""Forward-test paper book: trades ONE strategy mechanically, so its live record cannot be cherry-picked.

Book "fwd-momentum" trades "Momentum leaders (RS top 10%, trailing stop)":
  - Signals are taken from the latest completed session only (the strategy's own rules, incl. the
    breadth filter: no trades while fewer than 50% of Nifty 500 stocks are above their 200 DMA).
  - At most 3 new trades a day, strongest relative strength first; no second position in a held stock.
  - Risk 1% of the book (Rs 20,000 on Rs 20 lakh) per trade; stop 3 x ATR below the signal close,
    then a 3 x ATR trailing stop; exit after 60 sessions at the latest. Entry at the next session's open.
  - Total open exposure capped at 100% of capital (cash book, no leverage).
Trades are appended to paper_trades (append-only, unique per book/day/stock) and scored like any other.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from . import snapshot, technicals

BOOK = "fwd-momentum"
SETUP = "Momentum leaders (RS top 10%, trailing stop)"
CAPITAL = 2_000_000
RISK = 0.01
MAX_NEW = 3
MAX_EXPOSURE = 1.0
IST = timezone(timedelta(hours=5, minutes=30))


def next_session(after: date) -> date:
    d = after + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def order_date(signal_day: date, now: datetime | None = None) -> date:
    """The first session whose open is still in the future (exchange holidays are handled by the scorer,
    which fills at the first trading bar on/after this date)."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    d = next_session(signal_day)
    while datetime.combine(d, time(9, 15), IST) <= now:
        d = next_session(d)
    return d


def plan(prices: dict, existing: list[dict], now: datetime | None = None) -> list[dict]:
    """New trades for today, without writing anything (pure: easy to test)."""
    sig = technicals.scan(prices, [SETUP], lookback_days=1, allow_short=False)
    if sig.empty:
        return []
    ex = technicals.exit_rule(SETUP)
    res = snapshot.evaluate_paper([t for t in existing if t.get("book") == BOOK], prices)
    open_ids = {r["trade_id"] for r in res if r["status"] in ("open", "pending")}
    held = {t["symbol"] for t in existing if t.get("book") == BOOK and t["id"] in open_ids}
    exposure = sum(float(t["entry"]) * int(t["qty"]) for t in existing if t.get("book") == BOOK and t["id"] in open_ids)
    sig = sig[~sig["symbol"].isin(held)].sort_values("rs_pct", ascending=False)
    out = []
    for r in sig.itertuples():
        if len(out) >= MAX_NEW:
            break
        risk_per_share = ex["atr_mult"] * float(r.atr)
        if not (risk_per_share > 0 and math.isfinite(risk_per_share)):
            continue
        qty = int(CAPITAL * RISK // risk_per_share)
        value = qty * float(r.entry)
        if qty <= 0 or exposure + value > MAX_EXPOSURE * CAPITAL:
            continue
        exposure += value
        out.append({
            "trade_date": order_date(r.date, now).isoformat(), "book": BOOK, "symbol": r.symbol, "segment": "swing",
            "side": "long", "entry_type": "open", "entry": round(float(r.entry), 2),
            "stop": round(float(r.entry) - risk_per_share, 2), "target": round(float(r.entry) + 3 * risk_per_share, 2),
            "qty": qty, "horizon_days": int(ex["max_hold"]), "trail_atr": float(ex["atr_mult"]),
            "setup": SETUP, "grade": "forward test",
            "rationale": (f"Signal {r.date}: relative strength top {100 - 100 * float(r.rs_pct):.0f}% of Nifty 500, "
                          f"above 50 DMA, market breadth healthy. Risk Rs {qty * risk_per_share:,.0f} (1%). "
                          f"Exit: 3x ATR trailing stop, max 60 sessions."),
        })
    return out


def run(client, prices: dict, now: datetime | None = None) -> list[dict]:
    """Plan and write today's forward-test trades. Safe to run twice: duplicates are skipped."""
    existing = client.select("paper_trades", {"order": "id.asc"})
    rows = plan(prices, existing, now)
    done = {(t["book"], str(t["trade_date"]), t["symbol"]) for t in existing}
    rows = [r for r in rows if (r["book"], r["trade_date"], r["symbol"]) not in done]
    if rows:
        client.insert("paper_trades", rows)
    return rows
