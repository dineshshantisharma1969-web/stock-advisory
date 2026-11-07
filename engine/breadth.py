"""Market breadth from the universe's daily closes, highs, lows and volumes (dates x stocks frames).
Labels use stated thresholds; none of this is a back-tested signal."""
import numpy as np
import pandas as pd


def _series(s, n, nd=2):
    t = s.iloc[-n:]
    return [None if pd.isna(v) else round(float(v), nd) for v in t]


def compute(closes, volumes, index_close, sessions=120):
    chg = closes.pct_change(fill_method=None)
    valid = chg.notna()
    adv, dec = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    unch = (valid & (chg == 0)).sum(axis=1)
    net = adv - dec
    ad_line = net.cumsum()
    mcclellan = net.ewm(span=19, adjust=False).mean() - net.ewm(span=39, adjust=False).mean()
    pct_above = {}
    for n in (20, 50, 200):
        ma = closes.rolling(n).mean()
        pct_above[n] = (closes > ma).sum(axis=1) / ma.notna().sum(axis=1).replace(0, np.nan) * 100
    new_hi = (closes >= closes.rolling(252, min_periods=252).max()).sum(axis=1)
    new_lo = (closes <= closes.rolling(252, min_periods=252).min()).sum(axis=1)
    upvol, dnvol = volumes.where(chg > 0).sum(axis=1), volumes.where(chg < 0).sum(axis=1)

    today = {k: int(v.iloc[-1]) for k, v in dict(advances=adv, declines=dec, unchanged=unch, new_52w_highs=new_hi, new_52w_lows=new_lo).items()}
    today["ad_ratio"] = round(float(adv.iloc[-1] / max(dec.iloc[-1], 1)), 2)
    today["up_down_volume_ratio"] = round(float(upvol.iloc[-1] / max(dnvol.iloc[-1], 1)), 2)
    today["mcclellan"] = round(float(mcclellan.iloc[-1]), 1)
    for n in (20, 50, 200):
        today[f"pct_above_{n}dma"] = round(float(pct_above[n].iloc[-1]), 1)

    p200, mcc = today["pct_above_200dma"], today["mcclellan"]
    if p200 >= 60 and mcc > 0:
        state = "broad advance"
    elif p200 <= 40 and mcc < 0:
        state = "broad decline"
    else:
        state = "mixed"
    idx_at_20d_high = bool(index_close.iloc[-1] >= index_close.iloc[-20:].max())
    narrowing = idx_at_20d_high and bool(pct_above[50].iloc[-1] < pct_above[50].iloc[-21])
    ad_slope_20 = float(ad_line.iloc[-1] - ad_line.iloc[-21])

    return dict(
        today=today, state=state, narrowing=narrowing, ad_line_change_20d=int(ad_slope_20),
        rules="broad advance: >=60% above 200-DMA and McClellan > 0; broad decline: <=40% and McClellan < 0; "
              "narrowing: index at a 20-day high while fewer stocks sit above their 50-DMA than 20 sessions ago",
        series=dict(dates=[d.strftime("%Y-%m-%d") for d in closes.index[-sessions:]],
                    ad_line=_series(ad_line, sessions, 0), mcclellan=_series(mcclellan, sessions, 1),
                    pct_above_50=_series(pct_above[50], sessions, 1), pct_above_200=_series(pct_above[200], sessions, 1),
                    net_advances=_series(net, sessions, 0), index=_series(index_close, sessions)),
        coverage=int(valid.iloc[-1].sum()),
    )
