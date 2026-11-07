"""F&O view for NIFTY 50 and BANK NIFTY: trend, volatility, expiries, futures fair value and sizing, model option
structures per market view, and the trade call. Calls come only from index rules that passed their back-test
(jobs/backtest_index.py); with none passed, the call is NO TRADE and each rule's signal is shown as a watch item.
Option premiums are MODEL values (Black-Scholes at a flat volatility), not market quotes: Yahoo has no NSE option chain."""
import calendar
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from engine import index_rules as ir

ROOT = Path(__file__).resolve().parents[1]
N = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))


def bs(S, K, T, r, q, sig, kind):
    d1 = (math.log(S / K) + (r - q + sig * sig / 2) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if kind == "C":
        return S * math.exp(-q * T) * N(d1) - K * math.exp(-r * T) * N(d2), math.exp(-q * T) * N(d1)
    return K * math.exp(-r * T) * N(-d2) - S * math.exp(-q * T) * N(-d1), -math.exp(-q * T) * N(-d1)


def _shift(d, holidays):
    while d.isoformat() in holidays or d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def expiries(today, holidays, weekly):
    """Next weekly (if offered) and the next two monthly expiries (last Tuesday), moved back over holidays."""
    out, d = {}, today + timedelta(days=1)
    if weekly:
        while d.weekday() != 1:
            d += timedelta(days=1)
        out["weekly"] = _shift(d, holidays)
    months, y, m = [], today.year, today.month
    while len(months) < 2:
        last = date(y, m, calendar.monthrange(y, m)[1])
        while last.weekday() != 1:
            last -= timedelta(days=1)
        e = _shift(last, holidays)
        if e > today:
            months.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    out["monthly"], out["next_monthly"] = months
    return out


def structures(S, iv, T, r, q, step, width, lot, capital, max_risk_pct, em):
    rnd = lambda x: int(round(x / step) * step)
    atm = rnd(S)

    def build(name, view, legs):
        net = sum(sg * bs(S, K, T, r, q, iv, k)[0] for sg, K, k in legs)
        delta = sum(sg * bs(S, K, T, r, q, iv, k)[1] for sg, K, k in legs)
        grid = np.arange(atm - 8 * width, atm + 8 * width + 1, step / 10)
        pay = sum(sg * np.maximum((grid - K) if k == "C" else (K - grid), 0) for sg, K, k in legs) - net
        be = [float(grid[i]) for i in range(1, len(grid)) if (pay[i - 1] < 0) != (pay[i] < 0)]
        max_loss, max_profit = float(-pay.min() * lot), float(pay.max() * lot)
        return dict(name=name, view=view,
                    legs=[dict(action="BUY" if sg > 0 else "SELL", strike=K, type="CE" if k == "C" else "PE",
                               premium=round(bs(S, K, T, r, q, iv, k)[0], 1)) for sg, K, k in legs],
                    net=round(net, 1), net_type="debit" if net > 0 else "credit", net_rs=round(abs(net) * lot),
                    max_profit_rs=round(max_profit), max_loss_rs=round(max_loss), breakevens=[round(b) for b in be],
                    reward_risk=round(max_profit / max_loss, 2) if max_loss else None, net_delta=round(delta, 2),
                    lots_within_limit=int(capital * max_risk_pct / 100 // max_loss) if max_loss else 0)

    sc, sp = rnd(S + em), rnd(S - em)
    wing = max(step, rnd(width * 2 / 3))
    return [
        build("Bull call spread", "bullish", [(1, atm, "C"), (-1, atm + width, "C")]),
        build("Bull put spread (credit)", "bullish", [(-1, atm, "P"), (1, atm - width, "P")]),
        build("Bear put spread", "bearish", [(1, atm, "P"), (-1, atm - width, "P")]),
        build("Bear call spread (credit)", "bearish", [(-1, atm, "C"), (1, atm + width, "C")]),
        build("Iron condor at about 1 SD", "neutral", [(-1, sc, "C"), (1, sc + wing, "C"), (-1, sp, "P"), (1, sp - wing, "P")]),
    ]


def build(px, vix, today_ts, backtest_path=ROOT / "data" / "backtest_index.json", params_path=ROOT / "config" / "fno_params.json"):
    P = json.loads(Path(params_path).read_text(encoding="utf-8"))
    bt = json.loads(Path(backtest_path).read_text(encoding="utf-8")) if Path(backtest_path).exists() else None
    r, q = P["rates"]["r"], P["rates"]["q"]
    today = today_ts.date()
    vix = vix.dropna()
    v252 = vix.iloc[-252:]
    vix_now = float(vix.iloc[-1])
    vol = dict(india_vix=round(vix_now, 2), low_52w=round(float(v252.min()), 2), high_52w=round(float(v252.max()), 2),
               rank=round(float((vix_now - v252.min()) / (v252.max() - v252.min()) * 100)),
               percentile=round(float((v252 < vix_now).mean() * 100)))
    vol["regime"] = "low" if vol["rank"] < 30 else "high" if vol["rank"] > 50 else "middle"
    vol["strategy_hint"] = {"low": "options are cheap: prefer debit spreads (buying)", "high": "options are rich: prefer credit spreads (selling, defined risk)",
                            "middle": "neither cheap nor rich: debit and credit spreads both reasonable"}[vol["regime"]]

    nifty_close = px["Close"]["^NSEI"].dropna()
    nifty_rv = float(np.log(nifty_close / nifty_close.shift()).iloc[-20:].std() * math.sqrt(252))
    out = dict(as_of=today.isoformat(), volatility=vol, indices={}, rules=[], capital_rs=P["capital_rs"],
               risk_per_trade_pct=P["risk_per_trade_pct"], holidays_note=P["holidays_note"],
               model_note="Option premiums are model values (Black-Scholes, flat volatility), not market quotes. Check live premiums, margin and the broker's contract list before acting.",
               risk_note="SEBI's studies found about 9 in 10 individual F&O traders lost money in FY25.")
    rule_status = {}
    if bt:
        for rule, res in bt["results"].items():
            rule_status[rule] = res["passed"]
            c = res["combined_oos"]
            out["rules"].append(dict(rule=rule, label=res["label"], passed=res["passed"], checks=res["checks"],
                                     oos=dict(trades=c["n"], win=round(c["win"] * 100), pf=round(c["pf"], 2), exp=round(c["exp"], 3), t=round(c["t"], 2)) if c else None))
        out["backtest_run_on"], out["backtest_split"] = bt["run_on"], bt["split"]

    for name, cfg in P["indices"].items():
        t = cfg["symbol"]
        df = pd.DataFrame({k: px[k][t] for k in ["Open", "High", "Low", "Close"]}).dropna()
        o = ir.prepare(df)
        x = o.iloc[-1]
        S = float(x.Close)
        rv = float(np.log(o["Close"] / o["Close"].shift()).iloc[-20:].std() * math.sqrt(252))
        iv = vix_now / 100 if t == "^NSEI" else vix_now / 100 * (rv / nifty_rv if nifty_rv else 1)
        exps = expiries(today, set(P["holidays"]), cfg["weekly"])
        opt_exp = exps["monthly"] if (exps["monthly"] - today).days >= 10 else exps["next_monthly"]
        T = (opt_exp - today).days / 365
        em = S * iv * math.sqrt(T)
        fut_T = (exps["monthly"] - today).days / 365
        fair = S * math.exp((r - q) * fut_T)
        lot = cfg["lot"]
        stop_pts = 2 * float(x.atr)
        risk_lot = stop_pts * lot
        signals = {}
        for rule in ir.RULES:
            cs = ir.current_state(o, rule)
            if cs:
                signals[rule] = {**cs, "lots": int(P["capital_rs"] * P["risk_per_trade_pct"] / 100 // (cs["risk_points"] * lot))}
        live = [(rule, s) for rule, s in signals.items() if rule_status.get(rule)]
        if live:
            rule, s = live[0]
            call = dict(action=f"{s['side']} FUTURES", rule=rule, **s,
                        reason=f"rule {rule} ({ir.RULES[rule]}) passed its back-test and signalled on the latest close")
        else:
            call = dict(action="NO TRADE", reason="no index rule has passed its back-test" if not any(rule_status.values())
                        else "no rule that passed its back-test is signalling today")
        out["indices"][name] = dict(
            close=round(S, 2), chg=round(float((S / o["Close"].iloc[-2] - 1) * 100), 2), sma50=round(float(x.sma50)), sma200=round(float(x.sma200)),
            vs_200dma=round((S / float(x.sma200) - 1) * 100, 1), rsi=round(float(x.rsi), 1), atr=round(float(x.atr)),
            atr_pct=round(float(x.atr) / S * 100, 2), regime="up" if x.up else "down" if x.down else "mixed",
            realised_vol_20d=round(rv * 100, 1), iv_used=round(iv * 100, 1),
            iv_note="India VIX" if t == "^NSEI" else "India VIX scaled by BANK NIFTY / NIFTY realised volatility (estimate)",
            expiries={k: v.isoformat() for k, v in exps.items()}, options_expiry=opt_exp.isoformat(), dte=(opt_exp - today).days,
            expected_move=round(em), range_1sd=[round(S - em), round(S + em)],
            futures=dict(expiry=exps["monthly"].isoformat(), fair_value=round(fair, 1), basis=round(fair - S, 1), lot=lot,
                         lot_as_of=cfg["lot_as_of"], notional_rs=round(fair * lot), stop_points_2atr=round(stop_pts),
                         risk_per_lot_rs=round(risk_lot), lots_at_risk_limit=int(P["capital_rs"] * P["risk_per_trade_pct"] / 100 // risk_lot),
                         capital_for_one_lot_rs=round(risk_lot / (P["risk_per_trade_pct"] / 100))),
            structures=structures(S, iv, T, r, q, cfg["strike_step"], cfg["spread_width"], lot, P["capital_rs"], P["max_risk_per_structure_pct"], em),
            signals=signals, call=call,
            series=dict(dates=[d.strftime("%Y-%m-%d") for d in o.index[-120:]], close=[round(float(v), 1) for v in o["Close"].iloc[-120:]],
                        sma50=[round(float(v), 1) for v in o["sma50"].iloc[-120:]], sma200=[round(float(v), 1) for v in o["sma200"].iloc[-120:]]),
        )
    return out
