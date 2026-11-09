"""Technical indicators and rule-based trade setups (pure pandas, no TA-Lib needed).

Every setup returns entry / stop / target so it can be logged, tracked and backtested.
Stops are ATR-based (config.ATR_STOP_MULT) and targets are a fixed reward:risk multiple.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


# ------------------------------------------------------------------ indicators
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def rsi(close: pd.Series, n=14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def bollinger(close: pd.Series, n=20, k=2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_up": up, "bb_lo": lo,
                         "bb_width": (up - lo) / mid, "bb_pct": (close - lo) / (up - lo)})


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n=14) -> pd.Series:
    up, dn = df["High"].diff(), -df["Low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def stochastic(df: pd.DataFrame, n=14, d=3) -> pd.DataFrame:
    lo, hi = df["Low"].rolling(n).min(), df["High"].rolling(n).max()
    k = 100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)
    return pd.DataFrame({"stoch_k": k, "stoch_d": k.rolling(d).mean()})


def supertrend(df: pd.DataFrame, n=10, mult=3.0) -> pd.Series:
    """+1 uptrend / -1 downtrend."""
    a = atr(df, n)
    hl2 = (df["High"] + df["Low"]) / 2
    upper, lower = (hl2 + mult * a).values, (hl2 - mult * a).values
    close = df["Close"].values
    trend = np.ones(len(df))
    fu, fl = upper.copy(), lower.copy()
    for i in range(1, len(df)):
        fu[i] = upper[i] if upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1] else fu[i - 1]
        fl[i] = lower[i] if lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1] else fl[i - 1]
        if close[i] > fu[i - 1]:
            trend[i] = 1
        elif close[i] < fl[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return pd.Series(trend, index=df.index)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["Close"]
    for n in (20, 50, 200):
        out[f"dma{n}"] = sma(c, n)
    out["ema21"] = ema(c, 21)
    out = out.join(macd(c)).join(bollinger(c)).join(stochastic(out))
    out["rsi"] = rsi(c)
    out["atr"] = atr(out)
    out["adx"] = adx(out)
    out["supertrend"] = supertrend(out)
    out["vol20"] = out["Volume"].rolling(20).mean()
    out["hi52"] = out["High"].rolling(252, min_periods=120).max()
    out["lo52"] = out["Low"].rolling(252, min_periods=120).min()
    return out


# ------------------------------------------------------------------ setups
# Each rule maps an indicator frame to a Series: +1 long signal, -1 short signal, 0 nothing.
# Vectorised so the same code drives today's scan AND the historical backtest (no drift between them).
def _cross_up(a, b):
    return (a.shift() <= b.shift()) & (a > b)


def _cross_dn(a, b):
    return (a.shift() >= b.shift()) & (a < b)


def _sig(long_mask, short_mask=None):
    out = pd.Series(0, index=long_mask.index, dtype=int)
    out[long_mask.fillna(False)] = 1
    if short_mask is not None:
        out[short_mask.fillna(False)] = -1
    return out


def rule_golden_cross(x):
    return _sig(_cross_up(x["dma50"], x["dma200"]), _cross_dn(x["dma50"], x["dma200"]))


def rule_macd_trend(x):
    """MACD crossover only in the direction of the 200-DMA trend."""
    return _sig(_cross_up(x["macd"], x["signal"]) & (x["Close"] > x["dma200"]),
                _cross_dn(x["macd"], x["signal"]) & (x["Close"] < x["dma200"]))


def rule_bb_squeeze_breakout(x):
    """Band width in the lowest 20% of the last 120 days, then a close outside the band on 1.5x volume."""
    squeeze = x["bb_width"].shift() <= x["bb_width"].shift().rolling(120, min_periods=60).quantile(0.2)
    vol = x["Volume"] > 1.5 * x["vol20"]
    return _sig(squeeze & vol & (x["Close"] > x["bb_up"]), squeeze & vol & (x["Close"] < x["bb_lo"]))


def rule_rsi_pullback(x):
    """In an uptrend (above 200 DMA) RSI recovers back above 40; mirror for downtrend."""
    up = (x["Close"] > x["dma200"]) & (x["rsi"].shift() < 40) & (x["rsi"] >= 40)
    dn = (x["Close"] < x["dma200"]) & (x["rsi"].shift() > 60) & (x["rsi"] <= 60)
    return _sig(up, dn)


def rule_52w_breakout(x):
    return _sig((x["Close"] > x["hi52"].shift()) & (x["Volume"] > 2 * x["vol20"]) & (x["adx"] > 20))


def rule_supertrend_flip(x):
    st = x["supertrend"]
    return _sig((st.shift() < 0) & (st > 0) & (x["adx"] > 20), (st.shift() > 0) & (st < 0) & (x["adx"] > 20))


SETUPS = {
    "Golden/Death cross (50/200 DMA)": rule_golden_cross,
    "MACD crossover with 200-DMA trend": rule_macd_trend,
    "Bollinger squeeze breakout": rule_bb_squeeze_breakout,
    "RSI pullback in trend": rule_rsi_pullback,
    "52-week high breakout on volume": rule_52w_breakout,
    "Supertrend flip (ADX>20)": rule_supertrend_flip,
}


def levels(entry: float, atr_value: float, side: str, atr_mult=config.ATR_STOP_MULT, rr=config.REWARD_RISK) -> dict:
    risk = atr_mult * atr_value
    if side == "long":
        return {"entry": entry, "stop": entry - risk, "target": entry + rr * risk}
    return {"entry": entry, "stop": entry + risk, "target": entry - rr * risk}


def scan(prices: dict, setups: list[str] | None = None, lookback_days: int = 1,
         allow_short: bool = True) -> pd.DataFrame:
    """Signals fired in the last `lookback_days` bars across the universe."""
    setups = setups or list(SETUPS)
    rows = []
    for sym, df in prices.items():
        if len(df) < 210:
            continue
        x = add_indicators(df)
        tail = x.iloc[-lookback_days:]
        for name in setups:
            sig = SETUPS[name](x).iloc[-lookback_days:]
            for dt, v in sig[sig != 0].items():
                side = "long" if v > 0 else "short"
                if side == "short" and not allow_short:
                    continue
                bar = tail.loc[dt]
                lv = levels(float(bar["Close"]), float(bar["atr"]), side)
                rows.append({"date": dt.date(), "symbol": sym, "setup": name, "side": side, **lv,
                             "rsi": bar["rsi"], "adx": bar["adx"],
                             "risk_pct": abs(lv["entry"] - lv["stop"]) / lv["entry"]})
    return pd.DataFrame(rows)
