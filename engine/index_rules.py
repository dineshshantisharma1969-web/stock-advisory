"""Index futures rules for NIFTY 50 and BANK NIFTY, declared before any back-test was run (7-Nov-2026).
Signals use the daily close of day t; trades are entered at the open of day t+1. Results are in R (multiples
of the initial stop distance) after costs and futures carry, so they do not depend on capital or lot size.

F1 trend regime   long when close > 200-DMA and 50-DMA > 200-DMA; short when both are below. Initial stop
                  3 x ATR; trailing stop 3 x ATR from the best price since entry; exit when the regime ends.
F2 breakout       long on a close above the prior 20-day high, short on a close below the prior 20-day low.
                  Initial stop 2 x ATR; exit on a close beyond the prior 10-day low (long) / high (short).
F3 trend pullback in an up-regime (as F1) long when RSI(14) crosses back above 40; in a down-regime short when
                  RSI crosses back below 60. Stop 1.5 x ATR, target 3 x ATR, time exit after 15 sessions.

Costs: 0.05% of price per side (brokerage, exchange charges, STT on the sell side, slippage).
Carry: futures trade above spot by about (r - q); a long position loses and a short gains roughly
(r - q) = 5.3% a year relative to the index, charged per day held. Spot index data is used as the price path."""
import numpy as np
import pandas as pd

COST = 0.0005
CARRY = 0.053
RULES = {"F1": "trend regime", "F2": "20-day breakout", "F3": "trend pullback"}
GATE = dict(trades=40, expectancy=0.20, tstat=2.0, pf=1.3, maxdd=-0.20)  # out-of-sample, both indices together


def wilder(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def prepare(df):
    o = df.copy()
    c, h, l = o["Close"], o["High"], o["Low"]
    o["sma50"], o["sma200"] = c.rolling(50).mean(), c.rolling(200).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    o["atr"] = wilder(tr, 14)
    d = c.diff()
    o["rsi"] = 100 - 100 / (1 + wilder(d.clip(lower=0), 14) / wilder(-d.clip(upper=0), 14))
    o["hi20"], o["lo20"] = h.rolling(20).max().shift(), l.rolling(20).min().shift()
    o["hi10"], o["lo10"] = h.rolling(10).max().shift(), l.rolling(10).min().shift()
    o["up"] = (c > o["sma200"]) & (o["sma50"] > o["sma200"])
    o["down"] = (c < o["sma200"]) & (o["sma50"] < o["sma200"])
    return o.dropna(subset=["sma200", "atr", "rsi", "hi20"])


def signal(o, i, rule):
    """+1 long, -1 short, 0 none, from data up to and including bar i."""
    x, p = o.iloc[i], o.iloc[i - 1]
    if rule == "F1":
        return 1 if x.up and not p.up else -1 if x.down and not p.down else 0
    if rule == "F2":
        return 1 if x.Close > x.hi20 else -1 if x.Close < x.lo20 else 0
    if rule == "F3":
        if x.up and p.rsi < 40 <= x.rsi:
            return 1
        if x.down and p.rsi > 60 >= x.rsi:
            return -1
        return 0
    raise ValueError(rule)


def stop_mult(rule):
    return {"F1": 3.0, "F2": 2.0, "F3": 1.5}[rule]


def run(o, rule, cost=COST, carry=CARRY):
    O, H, L, C, A = (o[k].values for k in ("Open", "High", "Low", "Close", "atr"))
    up, down, lo10, hi10 = o["up"].values, o["down"].values, o["lo10"].values, o["hi10"].values
    idx, n, trades, i = o.index, len(o), [], 1
    while i < n - 1:
        side = signal(o, i, rule)
        if not side:
            i += 1
            continue
        j = i + 1
        entry, risk = O[j], stop_mult(rule) * A[i]
        stop = entry - side * risk
        target = entry + side * 2 * risk if rule == "F3" else None  # 3 ATR = 2 x the 1.5 ATR stop
        best, exit_px, k = entry, None, j
        while k < n:
            if side == 1 and L[k] <= stop:
                exit_px = min(O[k], stop) if k > j else stop; break
            if side == -1 and H[k] >= stop:
                exit_px = max(O[k], stop) if k > j else stop; break
            if target is not None and ((side == 1 and H[k] >= target) or (side == -1 and L[k] <= target)):
                exit_px = target; break
            if rule == "F1":
                best = max(best, H[k]) if side == 1 else min(best, L[k])
                stop = max(stop, best - 3 * A[k]) if side == 1 else min(stop, best + 3 * A[k])
                if (side == 1 and not up[k]) or (side == -1 and not down[k]):
                    if k + 1 < n:
                        exit_px, k = O[k + 1], k + 1
                    break
            if rule == "F2" and ((side == 1 and C[k] < lo10[k]) or (side == -1 and C[k] > hi10[k])):
                if k + 1 < n:
                    exit_px, k = O[k + 1], k + 1
                break
            if rule == "F3" and k - j >= 15:
                exit_px = C[k]; break
            k += 1
        if exit_px is None:
            break
        days = (idx[k] - idx[j]).days
        pnl = side * (exit_px - entry) - cost * (entry + exit_px) - side * carry * entry * days / 365
        trades.append(dict(entry_dt=idx[j], exit_dt=idx[k], side=side, entry=entry, exit=exit_px, R=pnl / risk, days=days))
        i = k + 1
    return trades


def stats(trades):
    R = np.array([t["R"] for t in trades])
    if len(R) < 2:
        return None
    w, lo = R[R > 0], R[R <= 0]
    eq = np.cumprod(1 + 0.01 * R)
    return dict(n=len(R), win=float((R > 0).mean()), avg_win=float(w.mean()) if len(w) else 0.0,
                avg_loss=float(lo.mean()) if len(lo) else 0.0,
                pf=float(w.sum() / -lo.sum()) if lo.sum() < 0 else float("inf"), exp=float(R.mean()),
                t=float(R.mean() / (R.std(ddof=1) / np.sqrt(len(R)))), total_1pct=float(eq[-1] - 1),
                maxdd_1pct=float((eq / np.maximum.accumulate(eq) - 1).min()), avg_days=float(np.mean([t["days"] for t in trades])),
                longs=int(sum(t["side"] == 1 for t in trades)), shorts=int(sum(t["side"] == -1 for t in trades)))


def gate(oos, oos_double):
    if not oos:
        return False, ["no trades"]
    checks = [(f"trades >= {GATE['trades']}", oos["n"] >= GATE["trades"]),
              (f"expectancy >= +{GATE['expectancy']}R", oos["exp"] >= GATE["expectancy"]),
              (f"t-stat > {GATE['tstat']}", oos["t"] > GATE["tstat"]),
              (f"profit factor >= {GATE['pf']}", oos["pf"] >= GATE["pf"]),
              ("max drawdown <= 20% at 1% risk", oos["maxdd_1pct"] >= GATE["maxdd"]),
              ("positive at double costs", bool(oos_double) and oos_double["exp"] > 0)]
    return all(ok for _, ok in checks), [f"{n} {'PASS' if ok else 'FAIL'}" for n, ok in checks]


def current_state(o, rule):
    """What the rule says on the latest bar: a new signal, or None."""
    s = signal(o, len(o) - 1, rule)
    x = o.iloc[-1]
    if not s:
        return None
    risk = stop_mult(rule) * x.atr
    return dict(side="BUY" if s == 1 else "SELL", ref_price=float(x.Close), stop=float(x.Close - s * risk),
                target=float(x.Close + s * 2 * risk) if rule == "F3" else None, risk_points=float(risk))
