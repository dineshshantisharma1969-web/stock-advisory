"""F&O helpers: Black-Scholes pricing and greeks, multi-leg payoff, and mapping a directional view
to a defined-risk structure. Lot sizes change every few months, so they come from a CSV you
download from NSE (data/fo_lots.csv: columns symbol,lot_size) rather than being hard-coded."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, r, sigma, kind="CE") -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0) if kind == "CE" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "CE":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_greeks(S, K, T, r, sigma, kind="CE") -> dict:
    if T <= 0 or sigma <= 0:
        return {"delta": np.nan, "gamma": np.nan, "theta_day": np.nan, "vega_1pct": np.nan}
    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf = math.exp(-d1 ** 2 / 2) / math.sqrt(2 * math.pi)
    delta = _ncdf(d1) if kind == "CE" else _ncdf(d1) - 1
    theta = (-S * pdf * sigma / (2 * math.sqrt(T))
             - (r * K * math.exp(-r * T) * (_ncdf(d2) if kind == "CE" else -_ncdf(-d2)))) / 365
    return {"delta": delta, "gamma": pdf / (S * sigma * math.sqrt(T)), "theta_day": theta,
            "vega_1pct": S * pdf * math.sqrt(T) / 100}


def implied_vol(price, S, K, T, r, kind="CE") -> float:
    lo, hi = 0.01, 3.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, kind) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def payoff(legs: list[dict], spots: np.ndarray) -> np.ndarray:
    """legs: {type: CE|PE|FUT, side: +1 buy / -1 sell, strike, premium, qty} -> P&L at expiry."""
    total = np.zeros_like(spots, dtype=float)
    for L in legs:
        if L["type"] == "FUT":
            v = spots - L["premium"]  # premium = futures entry price
        elif L["type"] == "CE":
            v = np.maximum(spots - L["strike"], 0) - L["premium"]
        else:
            v = np.maximum(L["strike"] - spots, 0) - L["premium"]
        total += L["side"] * v * L["qty"]
    return total


def strike_step(spot: float) -> float:
    for lim, step in ((250, 2.5), (500, 5), (1000, 10), (2500, 20), (5000, 50), (10000, 100)):
        if spot < lim:
            return step
    return 100.0


def suggest_structure(side: str, spot: float, target: float, stop: float, iv: float, days: int = 25,
                      lot: int = 1, r: float = config.RISK_FREE) -> dict:
    """Translate a swing signal into a defined-risk option spread around the technical target."""
    step = strike_step(spot)
    atm = round(spot / step) * step
    T = days / 365
    tgt = round(target / step) * step
    if side == "long":
        k1, k2, kind, name = atm, max(tgt, atm + step), "CE", "Bull call spread"
    else:
        k1, k2, kind, name = atm, min(tgt, atm - step), "PE", "Bear put spread"
    p1, p2 = bs_price(spot, k1, T, r, iv, kind), bs_price(spot, k2, T, r, iv, kind)
    legs = [{"type": kind, "side": +1, "strike": k1, "premium": p1, "qty": lot},
            {"type": kind, "side": -1, "strike": k2, "premium": p2, "qty": lot}]
    debit = (p1 - p2) * lot
    width = abs(k2 - k1) * lot
    return {"name": name, "legs": legs, "max_loss": debit, "max_profit": width - debit,
            "breakeven": k1 + (p1 - p2) if kind == "CE" else k1 - (p1 - p2),
            "reward_risk": (width - debit) / debit if debit > 0 else np.nan,
            "note": "Premiums are Black-Scholes estimates from your IV input; check live option chain before trading."}


def load_lots() -> dict:
    p = config.DATA_DIR / "fo_lots.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return dict(zip(df["symbol"].str.upper(), df["lot_size"].astype(int)))


def historical_vol(close: pd.Series, n: int = 30) -> float:
    return float(np.log(close).diff().tail(n).std() * math.sqrt(252))
