import pandas as pd
import pytest

from core import nse, snapshot


def bars(rows, start="2026-11-10"):
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1)


def trade(**kw):
    t = {"id": 1, "trade_date": "2026-11-10", "symbol": "ABC", "side": "long", "entry_type": "open", "entry": 100.0,
         "stop": 95.0, "target": 110.0, "qty": 100, "horizon_days": 10}
    t.update(kw)
    return t


def test_open_order_hits_target():
    px = bars([(101, 103, 100, 102), (102, 111, 101, 110)])
    r = snapshot.evaluate_trade(trade(), px)
    assert r["status"] == "target" and r["fill"] == 101 and r["exit"] == 110
    assert r["gross_pnl"] == pytest.approx(900) and r["costs"] == pytest.approx(0.001 * (101 + 110) * 100)


def test_limit_not_touched_is_not_filled():
    px = bars([(103, 105, 101, 104)])
    assert snapshot.evaluate_trade(trade(entry_type="limit", entry=100.0), px)["status"] == "not filled"


def test_limit_fill_and_same_bar_stop_first():
    px = bars([(102, 112, 94, 100)])         # touches limit, stop and target in one bar
    r = snapshot.evaluate_trade(trade(entry_type="limit"), px)
    assert r["fill"] == 100 and r["status"] == "stop" and r["net_pnl"] < 0


def test_gap_through_stop_fills_at_open():
    px = bars([(100, 101, 99, 100), (90, 92, 88, 91)])
    r = snapshot.evaluate_trade(trade(), px)
    assert r["status"] == "stop" and r["exit"] == 90


def test_short_and_open_position_marked_to_close():
    px = bars([(100, 101, 99, 98), (98, 99, 97, 97)])
    r = snapshot.evaluate_trade(trade(side="short", stop=105.0, target=90.0), px)
    assert r["status"] == "open" and r["exit"] == 97 and r["gross_pnl"] == pytest.approx(300)


def test_pending_before_session():
    px = bars([(100, 101, 99, 100)], start="2026-11-09")
    assert snapshot.evaluate_trade(trade(trade_date="2026-11-12"), px)["status"] == "pending"


def test_rule_checks():
    df = pd.DataFrame([trade(id=i, qty=100) for i in range(6)] + [trade(id=9, trade_date="2026-11-11", qty=9000)])
    out = snapshot.rule_checks(df)
    assert any("6 trades" in x for x in out) and any("#9" in x for x in out)


def test_parse_lot_sizes():
    text = ("UNDERLYING                                        ,SYMBOL    ,NOV-26    ,DEC-26    ,JAN-27\n"
            "NIFTY 50                                          ,NIFTY     ,75        ,75        ,75\n"
            "Reliance Industries Limited                       ,RELIANCE  ,500       ,500       ,500\n"
            "Some New Stock                                    ,NEWCO     ,          ,1200      ,1200\n")
    assert nse.parse_lot_sizes(text) == {"NIFTY": 75, "RELIANCE": 500, "NEWCO": 1200}


def test_snapshot_rows_are_json_safe():
    import json
    from core import data
    u = data.load_universe().head(3)
    px = data.DemoProvider(days=300).prices(list(u["symbol"]))
    rows = snapshot.snapshot_rows(px, u)
    json.dumps(rows, allow_nan=False)
    assert len(rows) == 3 and rows[0]["close"] > 0


def test_trailing_paper_trade_locks_in_profit():
    rows = [(100, 101, 99, 100)] * 20 + [(100 + 2 * i, 102 + 2 * i, 99 + 2 * i, 101 + 2 * i) for i in range(20)] \
           + [(130, 131, 110, 112)]
    px = bars(rows, start="2026-09-01")
    t = trade(trade_date="2026-09-29", entry=100.0, stop=94.0, target=999.0, trail_atr=3.0, horizon_days=60)
    r = snapshot.evaluate_trade(t, px)
    assert r["status"] == "trail stop" and r["exit"] > 110 and r["net_pnl"] > 0
