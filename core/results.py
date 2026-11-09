"""Quarterly results season: YoY / QoQ growth and margin change for each reported quarter.

Indian fiscal year runs April-March, so the quarter ending 30 Sep 2026 is Q2 FY27.
No consensus estimates are available from free sources, so a 'beat/miss' is judged against
the company's own trend (YoY growth and margin change), not against analyst forecasts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def fy_quarter(period_end) -> str:
    d = pd.Timestamp(period_end)
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month)
    if q is None:
        return d.strftime("%b %Y")
    fy = d.year + 1 if d.month >= 4 else d.year
    return f"Q{q} FY{fy % 100:02d}"


def _g(new, old):
    if old is None or new is None or np.isnan(old) or np.isnan(new) or old <= 0:
        return np.nan
    return new / old - 1


def company_result(q: dict, period_end: str | None = None) -> dict | None:
    """Metrics for one quarter (default: latest) vs same quarter last year and the previous quarter."""
    ends = q.get("period_end") or []
    if not ends:
        return None
    i = ends.index(period_end) if period_end in ends else (None if period_end else len(ends) - 1)
    if i is None:
        return None
    end = pd.Timestamp(ends[i])

    def find(months_back):
        target = (end - pd.DateOffset(months=months_back)) + pd.offsets.MonthEnd(0)
        for j, e in enumerate(ends):
            if pd.Timestamp(e).to_period("M") == target.to_period("M"):
                return j
        return None

    y, p = find(12), find(3)
    rev, ebit, ni = q["revenue"], q["ebit"], q["net_income"]
    get = lambda arr, j: arr[j] if j is not None and j < len(arr) else np.nan
    margin = lambda j: get(ebit, j) / get(rev, j) if get(rev, j) and not np.isnan(get(rev, j)) else np.nan
    m_now, m_ly = margin(i), margin(y)
    return {
        "period_end": ends[i], "quarter": fy_quarter(ends[i]),
        "revenue_cr": get(rev, i) / 1e7, "net_profit_cr": get(ni, i) / 1e7,
        "rev_yoy": _g(get(rev, i), get(rev, y)), "rev_qoq": _g(get(rev, i), get(rev, p)),
        "np_yoy": _g(get(ni, i), get(ni, y)), "np_qoq": _g(get(ni, i), get(ni, p)),
        "op_margin": m_now, "margin_chg_yoy": m_now - m_ly if not (np.isnan(m_now) or np.isnan(m_ly)) else np.nan,
        "loss_to_profit": get(ni, y) < 0 < get(ni, i) if y is not None else False,
    }


def grade(r: dict) -> str:
    if r.get("loss_to_profit"):
        return "Turnaround"
    rv, npy, mc = r["rev_yoy"], r["np_yoy"], r["margin_chg_yoy"]
    if any(np.isnan(v) for v in (rv, npy)):
        return "Insufficient data"
    if rv > 0.10 and npy > 0.15 and (np.isnan(mc) or mc >= 0):
        return "Strong"
    if rv > 0 and npy > 0:
        return "Good"
    if npy < -0.15 or (rv < 0 and npy < 0):
        return "Poor"
    return "Mixed"


def results_table(fundamentals: dict, universe: pd.DataFrame, quarter: str | None = None,
                  prices: dict | None = None) -> pd.DataFrame:
    """quarter: an FY label like 'Q2 FY27'; None = each company's latest quarter."""
    meta = universe.set_index("symbol")
    rows = []
    for s, f in fundamentals.items():
        q = f.get("quarters") or {}
        ends = q.get("period_end") or []
        if quarter:
            match = [e for e in ends if fy_quarter(e) == quarter]
            if not match:
                continue
            r = company_result(q, match[-1])
        else:
            r = company_result(q)
        if not r:
            continue
        r["grade"] = grade(r)
        r.update({"symbol": s, "name": meta["name"].get(s, s), "industry": meta["industry"].get(s, "")})
        if prices and s in prices and len(prices[s]) > 5:
            c = prices[s]["Close"]
            r["price_1w"] = c.iloc[-1] / c.iloc[-6] - 1
        rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    lead = ["symbol", "name", "industry", "quarter", "grade", "revenue_cr", "rev_yoy", "rev_qoq",
            "net_profit_cr", "np_yoy", "np_qoq", "op_margin", "margin_chg_yoy"]
    return df[lead + [c for c in df.columns if c not in lead]].sort_values("np_yoy", ascending=False)


def available_quarters(fundamentals: dict) -> list[str]:
    ends = {e for f in fundamentals.values() for e in (f.get("quarters") or {}).get("period_end", [])}
    return [fy_quarter(e) for e in sorted(ends, reverse=True) if pd.Timestamp(e).is_quarter_end]
