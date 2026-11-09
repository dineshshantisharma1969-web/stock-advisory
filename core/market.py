"""Market breadth (computed from Nifty 500 prices), FII/DII flows and macro data."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import config


# ------------------------------------------------------------------ breadth
def close_panel(prices: dict) -> pd.DataFrame:
    return pd.DataFrame({s: df["Close"] for s, df in prices.items()}).sort_index()


def breadth(prices: dict) -> pd.DataFrame:
    c = close_panel(prices)
    hi = pd.DataFrame({s: df["High"] for s, df in prices.items()}).reindex(c.index)
    lo = pd.DataFrame({s: df["Low"] for s, df in prices.items()}).reindex(c.index)
    chg = c.pct_change()
    adv, dec = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    valid = c.notna().sum(axis=1)
    net = adv - dec
    out = pd.DataFrame({
        "advances": adv, "declines": dec, "unchanged": valid - adv - dec,
        "ad_ratio": adv / dec.replace(0, np.nan), "ad_line": net.cumsum(),
        "pct_above_50dma": (c > c.rolling(50).mean()).sum(axis=1) / valid * 100,
        "pct_above_200dma": (c > c.rolling(200).mean()).sum(axis=1) / valid * 100,
        "new_52w_highs": (hi >= hi.rolling(252, min_periods=120).max()).sum(axis=1),
        "new_52w_lows": (lo <= lo.rolling(252, min_periods=120).min()).sum(axis=1),
        # McClellan oscillator on ratio-adjusted net advances
        "mcclellan": (net / valid * 1000).ewm(span=19, adjust=False).mean()
                     - (net / valid * 1000).ewm(span=39, adjust=False).mean(),
        "equal_weight_index": (1 + chg.mean(axis=1).fillna(0)).cumprod() * 100,
    })
    return out.iloc[1:]


def breadth_regime(b: pd.DataFrame) -> tuple[str, str]:
    last = b.iloc[-1]
    p200, p50 = last["pct_above_200dma"], last["pct_above_50dma"]
    if p200 > 60 and p50 > 55:
        return "Risk-on", "Broad participation: most stocks above their 200 and 50 DMA. Favour longs."
    if p200 < 40 and p50 < 40:
        return "Risk-off", "Weak breadth: most stocks below key averages. Reduce size; favour shorts/hedges."
    return "Mixed", "Narrow or transitioning market. Be selective, keep stops tight."


def sector_performance(prices: dict, universe: pd.DataFrame, days: int = 21) -> pd.DataFrame:
    c = close_panel(prices)
    if len(c) <= days:
        return pd.DataFrame()
    ret = (c.iloc[-1] / c.iloc[-1 - days] - 1).rename("ret")
    df = universe.set_index("symbol").join(ret, how="inner")
    return df.groupby("industry")["ret"].agg(["mean", "count"]).sort_values("mean", ascending=False)


# ------------------------------------------------------------------ FII / DII
NSE_FIIDII = "https://www.nseindia.com/api/fiidiiTradeReact"
_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json",
        "Referer": "https://www.nseindia.com/reports/fii-dii"}


def fetch_fii_dii_today() -> pd.DataFrame:
    """NSE publishes only the latest day (provisional, cash market). We append it to a local history."""
    import requests
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=_HDR, timeout=15)  # sets the cookies NSE requires
    r = s.get(NSE_FIIDII, headers=_HDR, timeout=15)
    r.raise_for_status()
    rows = []
    for d in r.json():
        cat = "FII" if "FII" in d["category"].upper() else "DII"
        rows.append({"date": pd.to_datetime(d["date"]).normalize(), "category": cat,
                     "buy": float(d["buyValue"]), "sell": float(d["sellValue"]), "net": float(d["netValue"])})
    return pd.DataFrame(rows)


def load_fii_dii_history() -> pd.DataFrame:
    from . import cloud
    c = cloud.client()
    if c:
        df = pd.DataFrame(c.select("fii_dii", {"order": "date.asc"}), columns=["date", "category", "buy", "sell", "net"])
        df["date"] = pd.to_datetime(df["date"])
        return df
    if not config.FII_DII_CSV.exists():
        return pd.DataFrame(columns=["date", "category", "buy", "sell", "net"])
    df = pd.read_csv(config.FII_DII_CSV, parse_dates=["date"])
    return df


def merge_fii_dii(new: pd.DataFrame, save: bool = True) -> pd.DataFrame:
    from . import cloud
    c = cloud.client()
    if c and save and len(new):
        rows = new.assign(date=pd.to_datetime(new["date"]).dt.strftime("%Y-%m-%d"))[
            ["date", "category", "buy", "sell", "net"]].to_dict("records")
        c.insert("fii_dii", rows, upsert_on="date,category")
        return load_fii_dii_history()
    hist = pd.concat([load_fii_dii_history(), new], ignore_index=True)
    hist["date"] = pd.to_datetime(hist["date"])
    hist = hist.drop_duplicates(["date", "category"], keep="last").sort_values(["date", "category"])
    if save:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        hist.to_csv(config.FII_DII_CSV, index=False)
    return hist


def demo_fii_dii(days: int = 250) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    fii = np.cumsum(rng.normal(0, 600, days)) * 0.1 + rng.normal(-300, 2500, days)
    dii = -fii * 0.7 + rng.normal(800, 1500, days)
    rows = []
    for d, f, di in zip(dates, fii, dii):
        for cat, net in (("FII", f), ("DII", di)):
            buy = abs(rng.normal(12000, 2500))
            rows.append({"date": d, "category": cat, "buy": buy, "sell": buy - net, "net": net})
    return pd.DataFrame(rows)


def fii_dii_pivot(hist: pd.DataFrame) -> pd.DataFrame:
    p = hist.pivot_table(index="date", columns="category", values="net", aggfunc="sum").fillna(0)
    for c in ("FII", "DII"):
        if c not in p:
            p[c] = 0.0
        p[f"{c}_cum"] = p[c].cumsum()
        p[f"{c}_5d"] = p[c].rolling(5).sum()
    return p


# ------------------------------------------------------------------ macro
def macro_prices(demo: bool, period: str = "1y") -> pd.DataFrame:
    if demo:
        rng = np.random.default_rng(11)
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=250)
        start = {"Nifty 50": 24000, "Nifty Bank": 52000, "India VIX": 14, "USD/INR": 86, "Brent Crude": 70,
                 "Gold": 3300, "US 10Y Yield": 4.2, "Dollar Index": 100, "S&P 500": 6000}
        return pd.DataFrame({k: v * np.exp(np.cumsum(rng.normal(0, 0.01, 250))) for k, v in start.items()},
                            index=dates)
    import yfinance as yf
    raw = yf.download(list(config.MACRO_TICKERS.values()), period=period, auto_adjust=True, progress=False)["Close"]
    inv = {v: k for k, v in config.MACRO_TICKERS.items()}
    return raw.rename(columns=inv).ffill()


def world_bank(demo: bool) -> pd.DataFrame:
    if demo:
        yrs = list(range(2016, 2026))
        rng = np.random.default_rng(3)
        return pd.DataFrame({k: rng.normal(m, 1.0, len(yrs)) for k, m in
                             zip(config.WB_INDICATORS, (6.5, 5.0, -1.2))}, index=yrs)
    import requests
    out = {}
    for name, code in config.WB_INDICATORS.items():
        url = f"https://api.worldbank.org/v2/country/IN/indicator/{code}?format=json&per_page=20"
        js = requests.get(url, timeout=20).json()
        out[name] = {int(d["date"]): d["value"] for d in js[1] if d["value"] is not None}
    return pd.DataFrame(out).sort_index()


def macro_signals(m: pd.DataFrame) -> list[tuple[str, str]]:
    """Simple, explainable reads of the macro tape: (headline, implication)."""
    sig = []

    def chg(col, d=21):
        s = m[col].dropna()
        return s.iloc[-1] / s.iloc[-1 - d] - 1 if len(s) > d else np.nan

    if "India VIX" in m:
        v = m["India VIX"].dropna().iloc[-1]
        sig.append((f"India VIX {v:.1f}", "Elevated fear: option premiums rich, favour selling spreads" if v > 18
                    else "Calm: options cheap, favour buying directional spreads" if v < 12 else "Normal volatility"))
    if "USD/INR" in m and pd.notna(chg("USD/INR")):
        c = chg("USD/INR")
        sig.append((f"Rupee {'weakened' if c > 0 else 'strengthened'} {abs(c):.1%} in a month",
                    "Tailwind for IT/pharma exporters; FII outflow risk" if c > 0.01 else "Supports FII inflows"))
    if "Brent Crude" in m and pd.notna(chg("Brent Crude")):
        c = chg("Brent Crude")
        sig.append((f"Brent {c:+.1%} in a month", "Negative for OMCs, paints, aviation; inflation risk" if c > 0.08
                    else "Positive for OMCs, paints, aviation" if c < -0.08 else "Crude stable"))
    if "US 10Y Yield" in m and pd.notna(chg("US 10Y Yield")):
        c = chg("US 10Y Yield")
        sig.append((f"US 10Y yield {c:+.1%} in a month", "Rising US yields pressure EM flows" if c > 0.05
                    else "Easing US yields support EM flows" if c < -0.05 else "US yields steady"))
    return sig
