from datetime import date, datetime

import pandas as pd
import pytest

from core import forward, technicals


def sig_frame(rows):
    return pd.DataFrame([{"date": date(2026, 11, 12), "symbol": s, "setup": forward.SETUP, "side": "long",
                          "entry": e, "stop": e - 30, "target": e + 90, "exit": "trailing", "rsi": 60, "adx": 25,
                          "atr": a, "rs_pct": rs, "risk_pct": 0.05} for s, e, a, rs in rows])


@pytest.fixture
def signals(monkeypatch):
    def set_(rows):
        monkeypatch.setattr(technicals, "scan", lambda prices, setups, lookback_days, allow_short: sig_frame(rows))
    return set_


def test_order_date_skips_weekend_and_started_sessions():
    ist = forward.IST
    # Friday's signal, planned Friday evening -> Monday
    assert forward.order_date(date(2026, 11, 13), datetime(2026, 11, 13, 19, 0, tzinfo=ist)) == date(2026, 11, 16)
    # planned Monday at 10:00, after the open -> Tuesday (never back-dated)
    assert forward.order_date(date(2026, 11, 13), datetime(2026, 11, 16, 10, 0, tzinfo=ist)) == date(2026, 11, 17)
    # planned Monday 08:30, before the open -> Monday
    assert forward.order_date(date(2026, 11, 13), datetime(2026, 11, 16, 8, 30, tzinfo=ist)) == date(2026, 11, 16)


def test_plan_takes_top3_by_strength_with_1pct_risk(signals):
    signals([("A", 1000, 20, 0.91), ("B", 500, 10, 0.99), ("C", 200, 5, 0.95), ("D", 100, 2, 0.93)])
    rows = forward.plan({}, [], now=datetime(2026, 11, 12, 20, 0, tzinfo=forward.IST))
    assert [r["symbol"] for r in rows] == ["B", "C", "D"]          # strongest first, max 3
    for r in rows:
        risk = (r["entry"] - r["stop"]) * r["qty"]
        assert risk <= forward.CAPITAL * forward.RISK + 1 and risk > forward.CAPITAL * forward.RISK * 0.9
        assert r["trail_atr"] == 3.0 and r["horizon_days"] == 60 and r["trade_date"] == "2026-11-13"


def test_plan_skips_held_stock_and_caps_exposure(signals, monkeypatch):
    signals([("A", 1000, 20, 0.99), ("B", 100, 0.2, 0.98)])
    existing = [{"id": 1, "book": forward.BOOK, "symbol": "A", "trade_date": "2026-11-10", "side": "long",
                 "entry_type": "open", "entry": 1000.0, "stop": 940.0, "target": 1180.0, "qty": 300,
                 "horizon_days": 60, "trail_atr": 3.0}]
    monkeypatch.setattr(forward.snapshot, "evaluate_paper",
                        lambda trades, prices: [{"trade_id": 1, "status": "open"}])
    rows = forward.plan({}, existing, now=datetime(2026, 11, 12, 20, 0, tzinfo=forward.IST))
    # A already held (Rs 3 lakh open); B's 1% risk with a tiny ATR needs Rs 33 lakh of stock -> over the cap
    assert rows == []


def test_run_is_idempotent(signals):
    signals([("A", 1000, 20, 0.99)])

    class C:
        def __init__(self):
            self.rows = []

        def select(self, table, params=None):
            return [dict(r, id=i + 1) for i, r in enumerate(self.rows)]

        def insert(self, table, rows, upsert_on=None):
            self.rows += rows
            return rows

    c = C()
    now = datetime(2026, 11, 12, 20, 0, tzinfo=forward.IST)
    assert len(forward.run(c, {}, now)) == 1
    assert forward.run(c, {}, now) == [] and len(c.rows) == 1      # second run adds nothing


def test_no_signal_no_trades(signals):
    signals([])
    assert forward.plan({}, []) == []
