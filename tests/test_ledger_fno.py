import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from core import fno, ledger


def idea(**kw):
    d = {"created_utc": "2026-01-05T10:00:00+00:00", "kind": "swing", "symbol": "ABC", "setup": "t", "side": "long",
         "entry": 100, "stop": 90, "target": 120, "horizon_days": 10, "instrument": "Equity", "lot_size": None,
         "rationale": "test"}
    d.update(kw)
    return d


def test_chain_verifies_and_detects_tampering(tmp_path):
    db = tmp_path / "j.db"
    for i in range(3):
        ledger.publish_idea(idea(symbol=f"S{i}"), db)
    assert ledger.verify_chain(db)[0]
    # normal edits are blocked by triggers
    with pytest.raises(sqlite3.DatabaseError):
        with sqlite3.connect(db) as c:
            c.execute("UPDATE ideas SET target=999 WHERE id=2")
    # someone who drops the triggers and edits a past call is still caught
    with sqlite3.connect(db) as c:
        c.execute("DROP TRIGGER ideas_no_update")
        c.execute("UPDATE ideas SET target=999 WHERE id=2")
    ok, msg = ledger.verify_chain(db)
    assert not ok and "#2" in msg


def test_evaluate_idea_target_hit(tmp_path):
    db = tmp_path / "j.db"
    ledger.publish_idea(idea(), db)
    idx = pd.bdate_range("2026-01-05", periods=6)
    px = pd.DataFrame({"Open": [100, 101, 105, 110, 115, 121], "High": [100, 104, 108, 114, 122, 125],
                       "Low": [99, 100, 103, 108, 113, 119], "Close": [100, 103, 107, 113, 121, 124],
                       "Volume": 1}, index=idx)
    ev = ledger.evaluate_ideas(ledger.ideas(db), {"ABC": px})
    r = ev.iloc[0]
    # filled at next session's open (101); target = fill + 20 = 121, first reached on 2026-01-09
    assert r["fill"] == 101 and r["status"] == "target" and r["exit"] == 121
    assert r["r_multiple"] == pytest.approx(2.0)


def test_journal_pnl(tmp_path):
    db = tmp_path / "j.db"
    tid = ledger.add_trade({"segment": "Futures", "symbol": "ABC", "side": "short", "qty": 500, "entry_date": "2026-01-01",
                            "entry": 200, "charges": 300}, db)
    ledger.close_trade(tid, "2026-01-10", 190, 600, db)
    t = ledger.trades(path=db)
    assert t.iloc[0]["pnl"] == pytest.approx((200 - 190) * 500 - 600)


def test_put_call_parity():
    S, K, T, r, s = 1000, 1050, 0.25, 0.07, 0.25
    c, p = fno.bs_price(S, K, T, r, s, "CE"), fno.bs_price(S, K, T, r, s, "PE")
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-8)


def test_implied_vol_round_trip():
    price = fno.bs_price(500, 520, 30 / 365, 0.07, 0.31, "CE")
    assert fno.implied_vol(price, 500, 520, 30 / 365, 0.07, "CE") == pytest.approx(0.31, abs=1e-6)


def test_bull_call_spread_bounds():
    s = fno.suggest_structure("long", 1000, 1100, 950, 0.25, 25, lot=100)
    spots = np.linspace(500, 2000, 1000)
    pl = fno.payoff(s["legs"], spots)
    assert pl.min() == pytest.approx(-s["max_loss"]) and pl.max() == pytest.approx(s["max_profit"])
