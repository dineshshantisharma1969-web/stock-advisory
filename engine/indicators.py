"""Per-stock technical readings. Formulas follow TradingView's defaults (Wilder smoothing for RSI, ATR, ADX)
so the numbers match a TradingView chart with the same settings."""
import numpy as np
import pandas as pd


def wilder(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def compute(df):
    """df: Open, High, Low, Close, Volume (daily). Returns df with indicator columns added."""
    o = df.copy()
    c, h, l, v = o["Close"], o["High"], o["Low"], o["Volume"]
    for n in (20, 50, 200):
        o[f"sma{n}"] = c.rolling(n).mean()
    d = c.diff()
    o["rsi"] = 100 - 100 / (1 + wilder(d.clip(lower=0), 14) / wilder(-d.clip(upper=0), 14))
    o["macd"] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    o["macd_signal"] = o["macd"].ewm(span=9, adjust=False).mean()
    o["macd_hist"] = o["macd"] - o["macd_signal"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    o["atr"] = wilder(tr, 14)
    um, dm = h.diff(), -l.diff()
    pdi = 100 * wilder(pd.Series(np.where((um > dm) & (um > 0), um, 0.0), index=o.index), 14) / o["atr"]
    mdi = 100 * wilder(pd.Series(np.where((dm > um) & (dm > 0), dm, 0.0), index=o.index), 14) / o["atr"]
    o["adx"] = wilder(100 * (pdi - mdi).abs() / (pdi + mdi), 14)
    o["bb_lo"] = o["sma20"] - 2 * c.rolling(20).std(ddof=0)
    o["vol_ratio"] = v / v.rolling(20).mean()
    o["don_hi"] = h.rolling(20).max().shift()
    wk = c.resample("W-FRI").last()
    o["wk_up"] = (wk > wk.rolling(30).mean()).reindex(o.index, method="ffill").fillna(False)
    return o


def _r(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def snapshot(o, market_ok):
    """Latest-bar readings for one stock from compute() output. Triggers are the swing rules in the skill;
    all three failed their back-test, so they are watch flags, never trade calls."""
    x, p = o.iloc[-1], o.iloc[-2]
    c = o["Close"]
    hi52, lo52 = o["High"].iloc[-252:].max(), o["Low"].iloc[-252:].min()
    up = x.Close > x.sma200 and x.sma50 > x.sma200
    down = x.Close < x.sma200 and x.sma50 < x.sma200
    if x.macd_hist > 0 and p.macd_hist <= 0:
        macd_state = "bullish cross today"
    elif x.macd_hist < 0 and p.macd_hist >= 0:
        macd_state = "bearish cross today"
    else:
        macd_state = "above signal" if x.macd_hist > 0 else "below signal"
    ret = lambda n: _r((x.Close / c.iloc[-n - 1] - 1) * 100, 1) if len(c) > n else None
    liquid = (c * o["Volume"]).iloc[-20:].median() >= 5e7
    trig = []
    if up and bool(x.wk_up) and market_ok and liquid and x.Close > x.don_hi and x.vol_ratio >= 1.5:
        trig.append("breakout")
    if up and market_ok and liquid and p.rsi < 40 <= x.rsi and x.Close > x.sma50:
        trig.append("pullback")
    if x.Close > x.sma200 and market_ok and liquid and p.Close < p.bb_lo and x.Close > x.bb_lo:
        trig.append("mean reversion")
    return dict(
        close=_r(x.Close), chg=_r((x.Close / p.Close - 1) * 100), rsi=_r(x.rsi, 1),
        macd=_r(x.macd), macd_signal=_r(x.macd_signal), macd_hist=_r(x.macd_hist), macd_state=macd_state,
        sma20=_r(x.sma20), sma50=_r(x.sma50), sma200=_r(x.sma200),
        trend="up" if up else "down" if down else "mixed", weekly_up=bool(x.wk_up),
        adx=_r(x.adx, 1), atr_pct=_r(x.atr / x.Close * 100), vol_ratio=_r(x.vol_ratio),
        hi52=_r(hi52), lo52=_r(lo52), pos52=_r((x.Close - lo52) / (hi52 - lo52) * 100, 0) if hi52 > lo52 else None,
        ret_1m=ret(21), ret_3m=ret(63), ret_6m=ret(126), triggers=trig,
    )


def chart(o, bars=180):
    t = o.iloc[-bars:]
    col = lambda name, nd=2: [None if pd.isna(v) else round(float(v), nd) for v in t[name]]
    return dict(dates=[d.strftime("%Y-%m-%d") for d in t.index], close=col("Close"), sma50=col("sma50"),
                sma200=col("sma200"), rsi=col("rsi", 1), macd=col("macd"), macd_signal=col("macd_signal"))
