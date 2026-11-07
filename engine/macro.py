"""Macro and country-risk gauges from market prices on Yahoo Finance, plus hand-entered figures.
Pressure flags use stated thresholds (judgment defaults). This is context for decisions, not a signal."""
import json
from datetime import date

import pandas as pd

TICKERS = {
    "usdinr": ("INR=X", "USD/INR", "price"),
    "brent": ("BZ=F", "Brent crude (USD)", "price"),
    "us10y": ("^TNX", "US 10-year yield (%)", "yield"),
    "dxy": ("DX-Y.NYB", "Dollar index", "price"),
    "gold": ("GC=F", "Gold (USD)", "price"),
    "spx": ("^GSPC", "S&P 500", "price"),
    "vix_india": ("^INDIAVIX", "India VIX", "level"),
}

RULES = [
    ("usdinr", "m1", 2.0, "rupee weakening fast (>2% in a month)"),
    ("brent", "m1", 10.0, "crude up more than 10% in a month"),
    ("us10y", "m1_abs", 0.30, "US 10-year yield up more than 30 bp in a month"),
    ("dxy", "m1", 2.0, "dollar index up more than 2% in a month"),
    ("gold", "m1", 8.0, "gold up more than 8% in a month (safety bid)"),
    ("spx", "m1", -5.0, "S&P 500 down more than 5% in a month"),
    ("vix_india", "pct", 80.0, "India VIX above its 80th percentile of the past year"),
]


def _chg(s, n):
    return None if len(s) <= n else round(float((s.iloc[-1] / s.iloc[-n - 1] - 1) * 100), 2)


def compute(closes, manual_path="config/manual_inputs.json", events_path="config/events.json"):
    """closes: DataFrame of macro tickers' closes (columns = Yahoo symbols)."""
    out, flags = {}, []
    for key, (sym, label, kind) in TICKERS.items():
        if sym not in closes.columns or closes[sym].dropna().empty:
            out[key] = dict(label=label, missing=True)
            continue
        s = closes[sym].dropna()
        d = dict(label=label, symbol=sym, last=round(float(s.iloc[-1]), 2), as_of=s.index[-1].strftime("%Y-%m-%d"),
                 d1=_chg(s, 1), m1=_chg(s, 21), m3=_chg(s, 63))
        if kind == "yield":
            d["m1_abs"] = None if len(s) <= 21 else round(float(s.iloc[-1] - s.iloc[-22]), 2)
        if key == "vix_india":
            y = s.iloc[-252:]
            d["pct"] = round(float((y < s.iloc[-1]).mean() * 100), 0)
            d["rank"] = round(float((s.iloc[-1] - y.min()) / (y.max() - y.min()) * 100), 0) if y.max() > y.min() else None
        out[key] = d
    for key, field, thr, text in RULES:
        v = out.get(key, {}).get(field)
        if v is None:
            continue
        hit = v <= thr if thr < 0 else v >= thr
        if hit:
            flags.append(text)
    backdrop = "calm" if len(flags) <= 1 else "watchful" if len(flags) <= 3 else "stressed"

    manual = {}
    try:
        raw = json.load(open(manual_path, encoding="utf-8"))
        for k, v in raw.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            age = None
            if v.get("as_of"):
                age = (date.today() - date.fromisoformat(v["as_of"])).days
            manual[k] = dict(v, age_days=age, stale=(age is None or age > 7))
    except FileNotFoundError:
        pass
    try:
        events = sorted(json.load(open(events_path, encoding="utf-8")), key=lambda e: e["date"], reverse=True)
    except FileNotFoundError:
        events = []
    return dict(gauges=out, flags=flags, backdrop=backdrop,
                backdrop_rule="0-1 flags calm, 2-3 watchful, 4 or more stressed", manual=manual, events=events)
