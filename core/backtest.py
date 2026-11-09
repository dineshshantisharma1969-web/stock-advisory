"""Backtest the technical setups on historical data, so call quality is MEASURED, not assumed.

Rules that avoid the usual self-deception:
- No look-ahead: a signal on day t is entered at day t+1's OPEN.
- Stop and target are set from that actual entry price and the ATR known on day t.
- If a bar touches both stop and target, we assume the STOP hit first (conservative).
- Round-trip costs (brokerage + STT + slippage) are deducted from every trade.
- One open trade per symbol per setup at a time.
- Results are split in-sample / out-of-sample by date; trust the out-of-sample numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .technicals import SETUPS, add_indicators, levels


def backtest_symbol(sym: str, x: pd.DataFrame, setup: str, max_hold: int = 20, cost_pct: float = 0.002,
                    atr_mult=config.ATR_STOP_MULT, rr=config.REWARD_RISK, allow_short=True) -> list[dict]:
    sig = SETUPS[setup](x).values
    o, h, l, c, a = (x[k].values for k in ("Open", "High", "Low", "Close", "atr"))
    idx = x.index
    trades, i, n = [], 0, len(x)
    while i < n - 1:
        side = sig[i]
        if side == 0 or np.isnan(a[i]) or (side < 0 and not allow_short):
            i += 1
            continue
        e = i + 1
        lv = levels(o[e], a[i], "long" if side > 0 else "short", atr_mult, rr)
        exit_px, exit_i, reason = None, None, ""
        for j in range(e, min(e + max_hold, n)):
            hit_stop = l[j] <= lv["stop"] if side > 0 else h[j] >= lv["stop"]
            hit_tgt = h[j] >= lv["target"] if side > 0 else l[j] <= lv["target"]
            if hit_stop:
                # gap through the stop fills at the open, not the stop price
                exit_px = min(o[j], lv["stop"]) if side > 0 else max(o[j], lv["stop"])
                exit_px = exit_px if j > e else lv["stop"]
                exit_i, reason = j, "stop"
                break
            if hit_tgt:
                exit_px, exit_i, reason = lv["target"], j, "target"
                break
        if exit_px is None:
            exit_i = min(e + max_hold, n) - 1
            if exit_i == n - 1 and e + max_hold > n:   # still open at end of data: ignore
                break
            exit_px, reason = c[exit_i], "time"
        gross = (exit_px / lv["entry"] - 1) * side
        risk = abs(lv["entry"] - lv["stop"]) / lv["entry"]
        net = gross - cost_pct
        trades.append({"symbol": sym, "setup": setup, "side": "long" if side > 0 else "short",
                       "entry_date": idx[e], "exit_date": idx[exit_i], "entry": lv["entry"],
                       "exit": exit_px, "ret": net, "r": net / risk if risk else np.nan,
                       "bars": exit_i - e + 1, "reason": reason})
        i = exit_i + 1
    return trades


def run(prices: dict, setups: list[str] | None = None, oos_frac: float = 0.3, progress=None, **kw) -> pd.DataFrame:
    setups = setups or list(SETUPS)
    all_tr = []
    for k, (sym, df) in enumerate(prices.items()):
        if len(df) < 260:
            continue
        x = add_indicators(df)
        for s in setups:
            all_tr += backtest_symbol(sym, x, s, **kw)
        if progress:
            progress((k + 1) / len(prices))
    tr = pd.DataFrame(all_tr)
    if tr.empty:
        return tr
    cut = tr["entry_date"].quantile(1 - oos_frac)
    tr["sample"] = np.where(tr["entry_date"] >= cut, "out-of-sample", "in-sample")
    return tr


def stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"trades": 0}
    wins, losses = tr[tr["ret"] > 0], tr[tr["ret"] <= 0]
    curve = tr.sort_values("exit_date")["r"].cumsum()
    pf = wins["ret"].sum() / -losses["ret"].sum() if len(losses) and losses["ret"].sum() < 0 else np.inf
    return {
        "trades": len(tr), "win_rate": len(wins) / len(tr), "avg_ret": tr["ret"].mean(),
        "expectancy_r": tr["r"].mean(), "profit_factor": pf,
        "avg_win": wins["ret"].mean() if len(wins) else 0, "avg_loss": losses["ret"].mean() if len(losses) else 0,
        "max_dd_r": float((curve - curve.cummax()).min()), "avg_bars": tr["bars"].mean(),
    }


def summary(tr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (setup, sample), g in tr.groupby(["setup", "sample"]):
        rows.append({"setup": setup, "sample": sample, **stats(g)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    oos = df[df["sample"] == "out-of-sample"].set_index("setup")
    # "Publishable" only if it held up out of sample with enough trades.
    df["grade"] = df["setup"].map(lambda s: grade(oos.loc[s]) if s in oos.index else "insufficient data")
    return df.sort_values(["setup", "sample"])


def grade(row) -> str:
    if row["trades"] < 30:
        return "insufficient data"
    if row["expectancy_r"] > 0.2 and row["profit_factor"] > 1.4:
        return "A - publishable"
    if row["expectancy_r"] > 0.05 and row["profit_factor"] > 1.1:
        return "B - monitor"
    return "C - do not publish"
