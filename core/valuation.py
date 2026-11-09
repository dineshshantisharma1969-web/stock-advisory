"""Fundamental valuation: DCF, reverse DCF, enterprise value, PE, PEG, growth and operating quality.

Conventions
- Annual series are oldest -> newest (see data.empty_fundamentals).
- Free cash flow (FCF) = operating cash flow + capital expenditure (capex is negative in statements).
  This is a pragmatic FCFF proxy; for leveraged companies it slightly understates FCFF.
- DCF is two-stage: growth fades linearly from the starting rate to terminal growth over N years.
- Banks/NBFCs/insurers (config.FINANCIAL_SECTORS): FCF and EV are not meaningful, so DCF/EV are
  skipped and they are judged on PE, P/B, ROE and profit growth.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def cagr(series: list[float]) -> float:
    """Compound annual growth between first and last value; NaN if not computable (e.g. sign flip)."""
    if len(series) < 2 or series[0] <= 0 or series[-1] <= 0:
        return np.nan
    return (series[-1] / series[0]) ** (1 / (len(series) - 1)) - 1


def wacc(f: dict, rf: float = config.RISK_FREE, erp: float = config.EQUITY_RISK_PREMIUM) -> float:
    beta = f.get("beta")
    beta = float(np.clip(beta, 0.5, 2.0)) if _ok(beta) else 1.0
    ke = rf + beta * erp
    debt = f.get("total_debt") if _ok(f.get("total_debt")) else 0.0
    mcap = f.get("market_cap")
    if not _ok(mcap) or mcap <= 0:
        return ke
    tax = f.get("tax_rate") if _ok(f.get("tax_rate")) else config.DEFAULT_TAX
    interest = f.get("interest_expense")
    kd = float(np.clip(interest / debt, 0.06, 0.14)) if debt > 0 and _ok(interest) else rf + 0.02
    v = mcap + debt
    return mcap / v * ke + debt / v * kd * (1 - tax)


def base_fcf(f: dict, years: int = 3) -> float:
    ocf, capex = f.get("ocf") or [], f.get("capex") or []
    n = min(len(ocf), len(capex), years)
    if n == 0:
        return np.nan
    fcf = [o + c for o, c in zip(ocf[-n:], capex[-n:])]
    return float(np.mean(fcf))  # averaging smooths lumpy capex years


def dcf_enterprise_value(fcf0: float, growth: float, discount: float,
                         terminal: float = config.TERMINAL_GROWTH, years: int = config.DCF_YEARS) -> dict:
    if discount <= terminal:
        raise ValueError("Discount rate must exceed terminal growth")
    flows, fcf = [], fcf0
    for t in range(1, years + 1):
        g = growth + (terminal - growth) * (t - 1) / max(years - 1, 1)  # linear fade
        fcf *= 1 + g
        flows.append(fcf / (1 + discount) ** t)
    tv = fcf * (1 + terminal) / (discount - terminal)
    pv_tv = tv / (1 + discount) ** years
    return {"pv_flows": float(sum(flows)), "pv_terminal": float(pv_tv), "ev": float(sum(flows) + pv_tv),
            "terminal_share": float(pv_tv / (sum(flows) + pv_tv)) if (sum(flows) + pv_tv) else np.nan}


def net_debt(f: dict) -> float:
    d = f.get("total_debt") if _ok(f.get("total_debt")) else 0.0
    c = f.get("cash") if _ok(f.get("cash")) else 0.0
    return d - c


def dcf_value_per_share(f: dict, growth: float | None = None, discount: float | None = None,
                        terminal: float = config.TERMINAL_GROWTH, years: int = config.DCF_YEARS) -> dict:
    fcf0 = base_fcf(f)
    shares = f.get("shares")
    if not _ok(fcf0) or fcf0 <= 0 or not _ok(shares) or shares <= 0:
        return {"value": np.nan, "reason": "Needs positive free cash flow and share count"}
    if growth is None:
        growth = default_growth(f)
    discount = discount if discount is not None else wacc(f)
    ev = dcf_enterprise_value(fcf0, growth, discount, terminal, years)
    equity = ev["ev"] - net_debt(f)
    return {"value": equity / shares, "fcf0": fcf0, "growth": growth, "discount": discount,
            "terminal": terminal, "equity_value": equity, **ev, "reason": ""}


def default_growth(f: dict) -> float:
    """Starting growth = blend of revenue and profit CAGR, capped to a sane band."""
    cands = [g for g in (cagr(f.get("revenue") or []), cagr(f.get("net_income") or [])) if _ok(g)]
    g = float(np.mean(cands)) if cands else 0.08
    return float(np.clip(g, 0.0, 0.25))


def reverse_dcf_growth(f: dict, discount: float | None = None, terminal: float = config.TERMINAL_GROWTH,
                       years: int = config.DCF_YEARS) -> float:
    """Starting FCF growth that the current market price implies (solved by bisection)."""
    fcf0 = base_fcf(f)
    mcap = f.get("market_cap")
    if not _ok(fcf0) or fcf0 <= 0 or not _ok(mcap):
        return np.nan
    target_ev = mcap + net_debt(f)
    discount = discount if discount is not None else wacc(f)
    lo, hi = -0.5, 1.5
    ev = lambda g: dcf_enterprise_value(fcf0, g, discount, terminal, years)["ev"]
    if not (ev(lo) <= target_ev <= ev(hi)):
        return np.nan
    for _ in range(80):
        mid = (lo + hi) / 2
        if ev(mid) < target_ev:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def analyse(f: dict, industry: str = "") -> dict:
    """All fundamental metrics for one company."""
    fin = industry in config.FINANCIAL_SECTORS
    price, shares, mcap = f.get("price"), f.get("shares"), f.get("market_cap")
    ni, eps, rev, ebit = (f.get(k) or [] for k in ("net_income", "eps", "revenue", "ebit"))
    ebitda = f.get("ebitda") or []
    eq, debt = f.get("book_equity"), f.get("total_debt")

    eps_ttm = eps[-1] if eps else (ni[-1] / shares if ni and _ok(shares) and shares else np.nan)
    pe = price / eps_ttm if _ok(price) and _ok(eps_ttm) and eps_ttm > 0 else np.nan
    eps_g = cagr(eps) if len(eps) >= 2 else cagr(ni)
    peg = pe / (eps_g * 100) if _ok(pe) and _ok(eps_g) and eps_g > 0 else np.nan
    ev = mcap + net_debt(f) if _ok(mcap) else np.nan
    margins = [e / r for e, r in zip(ebit, rev) if r] if rev and ebit else []
    cap_employed = (eq if _ok(eq) else 0) + (debt if _ok(debt) else 0) - (f.get("cash") if _ok(f.get("cash")) else 0)

    m = {
        "price": price, "market_cap_cr": mcap / 1e7 if _ok(mcap) else np.nan,
        "pe": pe, "eps_cagr": eps_g, "peg": peg,
        "np_growth_yoy": ni[-1] / ni[-2] - 1 if len(ni) >= 2 and ni[-2] > 0 else np.nan,
        "np_cagr": cagr(ni), "revenue_cagr": cagr(rev),
        "op_margin": margins[-1] if margins else np.nan,
        "op_margin_change": margins[-1] - margins[0] if len(margins) >= 2 else np.nan,
        "roe": ni[-1] / eq if ni and _ok(eq) and eq > 0 else np.nan,
        "roce": ebit[-1] / cap_employed if ebit and cap_employed > 0 else np.nan,
        "pb": mcap / eq if _ok(mcap) and _ok(eq) and eq > 0 else np.nan,
        "debt_equity": debt / eq if _ok(debt) and _ok(eq) and eq > 0 else np.nan,
        "interest_cover": ebit[-1] / f["interest_expense"] if ebit and _ok(f.get("interest_expense"))
                          and f["interest_expense"] > 0 else np.nan,
        "dividend_yield": f.get("dividend_yield", np.nan),
        "ev_cr": np.nan, "ev_ebitda": np.nan, "ev_sales": np.nan, "fcf_yield": np.nan,
        "wacc": np.nan, "dcf_value": np.nan, "dcf_upside": np.nan,
        "implied_growth": np.nan, "growth_gap": np.nan, "is_financial": fin,
    }
    if not fin:
        fcf = base_fcf(f)
        m.update({
            "ev_cr": ev / 1e7 if _ok(ev) else np.nan,
            "ev_ebitda": ev / ebitda[-1] if _ok(ev) and ebitda and ebitda[-1] > 0 else np.nan,
            "ev_sales": ev / rev[-1] if _ok(ev) and rev and rev[-1] > 0 else np.nan,
            "fcf_yield": fcf / mcap if _ok(fcf) and _ok(mcap) and mcap > 0 else np.nan,
            "wacc": wacc(f),
        })
        d = dcf_value_per_share(f)
        if _ok(d["value"]) and _ok(price) and price > 0:
            m["dcf_value"] = d["value"]
            m["dcf_upside"] = d["value"] / price - 1
        ig = reverse_dcf_growth(f)
        m["implied_growth"] = ig
        hist = default_growth(f)
        # positive gap = market expects LESS growth than the company has delivered (potentially cheap)
        m["growth_gap"] = hist - ig if _ok(ig) else np.nan
    return m


# ------------------------------------------------------------------ screen
SCORE_FACTORS = {
    # factor: (higher_is_better, weight)
    "dcf_upside": (True, 2.0), "growth_gap": (True, 1.5), "peg": (False, 1.5),
    "pe_vs_industry": (False, 1.0), "np_cagr": (True, 1.5), "roce": (True, 1.5),
    "roe": (True, 1.0), "op_margin_change": (True, 1.0), "debt_equity": (False, 1.0),
    "interest_cover": (True, 0.5), "fcf_yield": (True, 1.0), "ev_ebitda": (False, 1.0),
}


def screen(fundamentals: dict, universe: pd.DataFrame) -> pd.DataFrame:
    ind = dict(zip(universe["symbol"], universe["industry"]))
    names = dict(zip(universe["symbol"], universe["name"]))
    rows = []
    for s, f in fundamentals.items():
        try:
            r = analyse(f, ind.get(s, ""))
        except Exception:
            continue
        r.update({"symbol": s, "name": names.get(s, s), "industry": ind.get(s, "")})
        rows.append(r)
    df = pd.DataFrame(rows)
    # Yahoo occasionally returns numbers as text; a mixed column breaks on-screen tables (ArrowInvalid)
    text_cols = {"symbol", "name", "industry", "quarter", "period_end", "grade", "verdict", "quality_gate"}
    for col in df.columns:
        if col not in text_cols and df[col].dtype == object and col not in ("is_financial", "loss_to_profit"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if df.empty:
        return df
    med = df.groupby("industry")["pe"].transform("median")
    df["pe_vs_industry"] = df["pe"] / med - 1
    # percentile rank each factor (0-100), missing -> neutral 50, weighted average
    score, wsum = 0.0, 0.0
    for col, (hib, w) in SCORE_FACTORS.items():
        pr = df[col].rank(pct=True, ascending=hib) * 100
        score = score + pr.fillna(50) * w
        wsum += w
    df["score"] = (score / wsum).round(1)
    df["verdict"] = pd.cut(df["score"], [-1, 35, 50, 65, 101],
                           labels=["Avoid", "Neutral", "Watch", "Strong idea"]).astype(str)
    # Hard quality gate: a cheap-looking stock with shrinking profits or a stretched balance sheet
    # is a value trap, not an idea. Failing the gate caps the verdict at "Watch".
    df["quality_gate"] = df.apply(quality_gate, axis=1)
    df.loc[(df["verdict"] == "Strong idea") & (df["quality_gate"] != "pass"), "verdict"] = "Watch"
    lead = ["symbol", "name", "industry", "score", "verdict", "price", "dcf_value", "dcf_upside",
            "implied_growth", "pe", "pe_vs_industry", "peg", "ev_ebitda", "np_cagr", "roce", "roe"]
    return df[lead + [c for c in df.columns if c not in lead]].sort_values("score", ascending=False)


def quality_gate(r) -> str:
    fails = []
    if not (_ok(r.get("pe")) and r["pe"] > 0):
        fails.append("loss-making")
    if not (_ok(r.get("np_cagr")) and r["np_cagr"] > 0):
        fails.append("profit not growing")
    if not r.get("is_financial"):
        if _ok(r.get("debt_equity")) and r["debt_equity"] > 2:
            fails.append("debt/equity > 2")
        if _ok(r.get("interest_cover")) and r["interest_cover"] < 3:
            fails.append("interest cover < 3")
    elif _ok(r.get("roe")) and r["roe"] < 0.10:
        fails.append("ROE < 10%")
    return "pass" if not fails else "; ".join(fails)


def idea_reasons(r: pd.Series) -> list[str]:
    """Plain-English reasons behind a fundamental idea."""
    out = []
    if _ok(r.get("dcf_upside")) and r["dcf_upside"] > 0.15:
        out.append(f"DCF value {r['dcf_value']:,.0f} is {r['dcf_upside']:.0%} above price")
    if _ok(r.get("growth_gap")) and r["growth_gap"] > 0.03:
        out.append(f"Price implies only {r['implied_growth']:.1%} FCF growth vs history")
    if _ok(r.get("peg")) and 0 < r["peg"] < 1:
        out.append(f"PEG {r['peg']:.2f} (<1: growth not priced in)")
    if _ok(r.get("pe_vs_industry")) and r["pe_vs_industry"] < -0.2:
        out.append(f"PE {r['pe']:.1f} is {-r['pe_vs_industry']:.0%} below industry median")
    if _ok(r.get("np_cagr")) and r["np_cagr"] > 0.15:
        out.append(f"Net profit CAGR {r['np_cagr']:.0%}")
    if _ok(r.get("roce")) and r["roce"] > 0.2:
        out.append(f"ROCE {r['roce']:.0%}")
    if _ok(r.get("op_margin_change")) and r["op_margin_change"] > 0.02:
        out.append(f"Operating margin up {r['op_margin_change']*100:.1f} pts")
    if _ok(r.get("debt_equity")) and r["debt_equity"] > 1.5:
        out.append(f"Caution: debt/equity {r['debt_equity']:.1f}")
    return out
