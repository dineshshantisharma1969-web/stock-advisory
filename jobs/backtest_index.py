"""Back-test the pre-declared index futures rules (engine/index_rules.py) on NIFTY 50 and BANK NIFTY.
In-sample: entries before 2018. Out-of-sample: 2018 onward, judged on both indices together.
Writes data/backtest_index.json. Run: .venv\\Scripts\\python.exe jobs\\backtest_index.py"""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd
import yfinance as yf

from engine import index_rules as ir

SPLIT = pd.Timestamp("2018-01-01")
INDICES = {"^NSEI": "NIFTY 50", "^NSEBANK": "BANK NIFTY"}


def main():
    px = yf.download(list(INDICES), period="max", interval="1d", auto_adjust=False, progress=False)
    data = {}
    for t, name in INDICES.items():
        df = pd.DataFrame({k: px[k][t] for k in ["Open", "High", "Low", "Close"]}).dropna()
        df = df[(df["High"] >= df["Low"]) & (df["Open"] > 0)]
        data[name] = ir.prepare(df)
        print(f"{name}: {df.index[0].date()} to {df.index[-1].date()}, {len(df)} bars")
    out = {"run_on": date.today().isoformat(), "split": str(SPLIT.date()), "rules": ir.RULES, "gate": ir.GATE,
           "doc": ir.__doc__, "results": {}}
    for rule, label in ir.RULES.items():
        res, all_is, all_oos, all_oos2 = {}, [], [], []
        for name, o in data.items():
            tr = ir.run(o, rule)
            tr2 = ir.run(o, rule, cost=2 * ir.COST)
            is_, oos = [t for t in tr if t["entry_dt"] < SPLIT], [t for t in tr if t["entry_dt"] >= SPLIT]
            all_is += is_; all_oos += oos; all_oos2 += [t for t in tr2 if t["entry_dt"] >= SPLIT]
            bh = o["Close"]
            seg = bh[bh.index >= SPLIT]
            res[name] = dict(in_sample=ir.stats(is_), out_of_sample=ir.stats(oos),
                             buy_hold_oos=float(seg.iloc[-1] / seg.iloc[0] - 1), current=ir.current_state(o, rule))
        s_is, s_oos, s_oos2 = ir.stats(all_is), ir.stats(sorted(all_oos, key=lambda t: t["exit_dt"])), ir.stats(all_oos2)
        passed, checks = ir.gate(s_oos, s_oos2)
        out["results"][rule] = dict(label=label, per_index=res, combined_in=s_is, combined_oos=s_oos,
                                    combined_oos_double_cost=s_oos2, passed=passed, checks=checks)
        f = lambda s: "n/a" if not s else (f"trades {s['n']:>3} (L{s['longs']}/S{s['shorts']}) win {s['win']:.0%} PF {s['pf']:.2f} "
                                          f"exp {s['exp']:+.3f}R t {s['t']:+.2f} | 1% risk: total {s['total_1pct']:+.1%} maxDD {s['maxdd_1pct']:.1%} | hold {s['avg_days']:.0f}d")
        print(f"\n=== {rule} {label} ===")
        print(f"  in-sample      {f(s_is)}")
        print(f"  OUT-OF-SAMPLE  {f(s_oos)}")
        print(f"  double costs   exp {s_oos2['exp']:+.3f}R" if s_oos2 else "  double costs n/a")
        for name, r in res.items():
            print(f"    {name:<10} OOS {f(r['out_of_sample'])} | buy&hold since 2018 {r['buy_hold_oos']:+.0%} | today: {r['current'] or 'no signal'}")
        print("  gate: " + "; ".join(checks) + f"  ->  {'PASSED' if passed else 'REJECTED'}")
    Path("data/backtest_index.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
