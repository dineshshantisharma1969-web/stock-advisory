"""Readable daily snapshot in Supabase tables + paper-trading evaluation.

After each refresh the app writes, as plain database rows:
  market_snapshot  latest close + indicators + key fundamentals for every stock
  signals          setups fired in the last few sessions, with their backtest grade
  breadth_daily    advance/decline and % above moving averages, last 60 sessions
  backtest_grades  latest backtest summary per setup
  paper_results    outcome of every paper trade, computed from real prices
That lets an analyst (or Claude, through SQL) read the market and place paper trades in paper_trades,
while fills and P&L are always computed here from prices - never typed in by hand.

Paper fill rules (same spirit as the backtest):
  entry_type 'open'  -> filled at the open of the first session on/after trade_date
  entry_type 'limit' -> day order: filled only if that session trades through the limit
                        (long: low <= limit, fill = min(open, limit); short: high >= limit, fill = max(open, limit))
  exits: stop or target touched; if one bar touches both, the STOP counts; a gap through the stop
         fills at the open; otherwise closed at the close of the horizon_days-th session.
  costs: 0.1% of turnover per side.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import market, technicals

COST_PER_SIDE = 0.001
CAPITAL = 2_000_000


def _f(x):
    """JSON-safe float (NaN/inf -> None)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def snapshot_rows(prices: dict, universe: pd.DataFrame, screen: pd.DataFrame | None = None) -> list[dict]:
    meta = universe.set_index("symbol")
    sc = screen.set_index("symbol") if screen is not None and len(screen) else pd.DataFrame()
    rows = []
    for sym, df in prices.items():
        if len(df) < 30:
            continue
        x = technicals.add_indicators(df)
        last, c = x.iloc[-1], x["Close"]

        def chg(n):
            return _f(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None

        r = {"symbol": sym, "name": meta["name"].get(sym), "industry": meta["industry"].get(sym),
             "date": x.index[-1].strftime("%Y-%m-%d"),
             "open": _f(last["Open"]), "high": _f(last["High"]), "low": _f(last["Low"]), "close": _f(last["Close"]),
             "volume": _f(last["Volume"]), "chg_1d": chg(1), "chg_5d": chg(5), "chg_21d": chg(21),
             "dma20": _f(last["dma20"]), "dma50": _f(last["dma50"]), "dma200": _f(last["dma200"]),
             "rsi": _f(last["rsi"]), "macd": _f(last["macd"]), "macd_signal": _f(last["signal"]), "adx": _f(last["adx"]),
             "atr": _f(last["atr"]), "supertrend": int(last["supertrend"]) if pd.notna(last["supertrend"]) else None,
             "bb_up": _f(last["bb_up"]), "bb_lo": _f(last["bb_lo"]), "hi52": _f(last["hi52"]), "lo52": _f(last["lo52"]),
             "vol20": _f(last["vol20"])}
        if sym in sc.index:
            s = sc.loc[sym]
            r.update({"pe": _f(s.get("pe")), "roce": _f(s.get("roce")), "np_cagr": _f(s.get("np_cagr")),
                      "debt_equity": _f(s.get("debt_equity")), "fund_score": _f(s.get("score")),
                      "verdict": s.get("verdict")})
        rows.append(r)
    return rows


def signal_rows(prices: dict, grades: dict, days: int = 5) -> list[dict]:
    sig = technicals.scan(prices, lookback_days=days)
    if sig.empty:
        return []
    sig["grade"] = sig["setup"].map(grades).fillna("not tested")
    return [{"date": str(r.date), "symbol": r.symbol, "setup": r.setup, "side": r.side, "entry": _f(r.entry),
             "stop": _f(r.stop), "target": _f(r.target), "grade": r.grade, "rsi": _f(r.rsi), "adx": _f(r.adx)}
            for r in sig.itertuples()]


def breadth_rows(prices: dict, days: int = 60) -> list[dict]:
    b = market.breadth(prices)
    if b.empty:
        return []
    out = []
    for d, r in b.tail(days).iterrows():
        regime, _ = market.breadth_regime(b.loc[:d])
        out.append({"date": d.strftime("%Y-%m-%d"), "advances": int(r["advances"]), "declines": int(r["declines"]),
                    "pct_above_50dma": _f(r["pct_above_50dma"]), "pct_above_200dma": _f(r["pct_above_200dma"]),
                    "new_52w_highs": int(r["new_52w_highs"]), "new_52w_lows": int(r["new_52w_lows"]),
                    "mcclellan": _f(r["mcclellan"]), "regime": regime})
    return out


def grade_rows(summary: pd.DataFrame | None) -> list[dict]:
    if summary is None or len(summary) == 0:
        return []
    return [{"setup": r.setup, "sample": r.sample, "trades": int(r.trades), "win_rate": _f(r.win_rate),
             "expectancy_r": _f(r.expectancy_r), "profit_factor": _f(r.profit_factor), "max_dd_r": _f(r.max_dd_r),
             "grade": r.grade} for r in summary.itertuples()]


# ------------------------------------------------------------------ paper trading
def evaluate_trade(t: dict, px: pd.DataFrame | None) -> dict:
    res = {"trade_id": int(t["id"]), "status": "pending", "fill": None, "fill_date": None, "exit": None,
           "exit_date": None, "gross_pnl": None, "costs": None, "net_pnl": None, "r_multiple": None}
    if px is None or px.empty:
        res["status"] = "no data"
        return res
    bars = px[px.index >= pd.Timestamp(t["trade_date"])]
    if bars.empty:
        return res                                   # session not traded yet
    side = 1 if t["side"] == "long" else -1
    first = bars.iloc[0]
    if t["entry_type"] == "open":
        fill = float(first["Open"])
    else:
        lim = float(t["entry"])
        touched = first["Low"] <= lim if side > 0 else first["High"] >= lim
        if not touched:
            res["status"] = "not filled"
            return res
        fill = min(float(first["Open"]), lim) if side > 0 else max(float(first["Open"]), lim)
    stop, target, qty = float(t["stop"]), float(t["target"]), int(t["qty"])
    init_stop = stop
    horizon = int(t.get("horizon_days") or 10)
    trail = t.get("trail_atr")
    trail = float(trail) if trail is not None and not (isinstance(trail, float) and math.isnan(trail)) else None
    atr = technicals.atr(px).reindex(bars.index) if trail else None
    best = fill
    status, exit_px, exit_d = "open", float(bars["Close"].iloc[min(len(bars), horizon) - 1]), None
    for j, (d, bar) in enumerate(bars.head(horizon).iterrows()):
        hit_stop = bar["Low"] <= stop if side > 0 else bar["High"] >= stop
        hit_tgt = (not trail) and (bar["High"] >= target if side > 0 else bar["Low"] <= target)
        if hit_stop:
            gap = (bar["Open"] < stop) if side > 0 else (bar["Open"] > stop)
            status = "trail stop" if trail and stop != init_stop else "stop"
            exit_px, exit_d = float(bar["Open"]) if (gap and j > 0) else stop, d
            break
        if hit_tgt:
            status, exit_px, exit_d = "target", target, d
            break
        if trail and pd.notna(atr.iloc[j]):   # ratchet behind the best close, effective from the next bar
            if side > 0:
                best = max(best, float(bar["Close"]))
                stop = max(stop, best - trail * float(atr.iloc[j]))
            else:
                best = min(best, float(bar["Close"]))
                stop = min(stop, best + trail * float(atr.iloc[j]))
    else:
        if len(bars) >= horizon:
            status, exit_d = "expired", bars.index[horizon - 1]
        else:
            exit_px = float(bars["Close"].iloc[-1])       # open position marked to the latest close
    gross = (exit_px - fill) * qty * side
    costs = COST_PER_SIDE * (fill + (exit_px if status != "open" else 0)) * qty
    risk = abs(float(t["entry"]) - stop) * qty
    res.update({"status": status, "fill": fill, "fill_date": bars.index[0].strftime("%Y-%m-%d"), "exit": exit_px,
                "exit_date": exit_d.strftime("%Y-%m-%d") if exit_d is not None else None,
                "gross_pnl": gross, "costs": costs, "net_pnl": gross - costs,
                "r_multiple": (gross - costs) / risk if risk else None})
    return res


def evaluate_paper(trades: list[dict], prices: dict) -> list[dict]:
    return [evaluate_trade(t, prices.get(t["symbol"])) for t in trades]


def rule_checks(trades: pd.DataFrame, capital: float = CAPITAL, max_per_day: int = 5, max_risk: float = 0.02) -> list[str]:
    """Flag any breach of the game's rules (shown on the scoreboard)."""
    if trades.empty:
        return []
    out = []
    per_day = trades.groupby("trade_date").size()
    for d, n in per_day[per_day > max_per_day].items():
        out.append(f"{d}: {n} trades (limit {max_per_day})")
    risk = (trades["entry"] - trades["stop"]).abs() * trades["qty"]
    for i in trades.index[risk > max_risk * capital * 1.0001]:
        out.append(f"Trade #{trades.loc[i, 'id']} risks ₹{risk[i]:,.0f} (> {max_risk:.0%} of capital)")
    return out


# ------------------------------------------------------------------ publish
def publish(client, prices: dict, universe: pd.DataFrame, screen=None, backtest_summary=None, log=print) -> dict:
    """Write everything above to Supabase. Each part is independent: one failure doesn't stop the rest."""
    from . import nse
    done = {}
    grades = {}
    if backtest_summary is not None and len(backtest_summary):
        grades = backtest_summary.drop_duplicates("setup").set_index("setup")["grade"].to_dict()
    jobs = [
        ("market_snapshot", lambda: snapshot_rows(prices, universe, screen), "symbol"),
        ("signals", lambda: signal_rows(prices, grades), "date,symbol,setup"),
        ("breadth_daily", lambda: breadth_rows(prices), "date"),
        ("backtest_grades", lambda: grade_rows(backtest_summary), "setup,sample"),
    ]
    for table, make, key in jobs:
        try:
            rows = make()
            for i in range(0, len(rows), 500):
                client.insert(table, rows[i:i + 500], upsert_on=key)
            done[table] = len(rows)
        except Exception as e:
            log(f"{table} failed: {e}")
    try:
        trades = client.select("paper_trades", {"order": "id.asc"})
        res = evaluate_paper(trades, prices)
        if res:
            client.insert("paper_results", res, upsert_on="trade_id")
        done["paper_results"] = len(res)
    except Exception as e:
        log(f"paper_results failed: {e}")
    try:
        lots = nse.fetch_lot_sizes()
        if lots:
            client.insert("fo_lots", [{"symbol": k, "lot_size": v} for k, v in lots.items()], upsert_on="symbol")
        done["fo_lots"] = len(lots)
    except Exception as e:
        log(f"fo_lots skipped (NSE unreachable?): {e}")
    return done
