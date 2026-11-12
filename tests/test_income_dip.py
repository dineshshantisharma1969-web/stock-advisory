import io
from datetime import date, datetime

import pandas as pd
import pytest

from core import forward, income, snapshot

COLS = "TradDt,FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,ClsPric,SttlmPric,UndrlygPric,TtlTradgVol"


def chain(day, spot, expiry, prices, vol=10):
    """prices: {(type, strike): price}"""
    lines = [COLS] + [f"{day},IDO,NIFTY,{expiry},{k},{t},{p},{p},{spot},{vol}" for (t, k), p in prices.items()]
    lines.append(f"{day},STO,RELIANCE,{expiry},1300,CE,10,10,1290,5")       # must be ignored
    return income.parse_chain("\n".join(lines).encode())


def test_strikes_follow_the_rule():
    s = income.strikes(25000, 0.146, 6)        # expected move = 25000 x 0.146 x sqrt(6/365) = 468
    assert s["sell_put"] == 24650 and s["sell_call"] == 25350      # 0.75 moves, rounded to 50
    assert s["buy_put"] == 24200 and s["buy_call"] == 25800        # one more move out


def test_parse_keeps_only_nifty_options_and_uses_settlement_when_untraded():
    raw = "\n".join([COLS, "2026-11-18,IDO,NIFTY,2026-11-24,25000,CE,0,123.5,25010,0",
                     "2026-11-18,IDF,NIFTY,2026-11-24,,,25050,25050,25010,9"]).encode()
    ch = income.parse_chain(raw)
    assert len(ch) == 1 and ch["price"].iloc[0] == 123.5 and income.spot_of(ch) == 25010


PX = {("PE", 24650): 40, ("PE", 24200): 12, ("CE", 25350): 38, ("CE", 25800): 9}


def entry():
    return income.open_trade(chain("2026-11-18", 25000, "2026-11-24", PX), date(2026, 11, 24), 0.146)


def test_open_trade_credit_and_max_loss():
    t = entry()
    assert t["credit"] == pytest.approx(40 - 12 + 38 - 9 - 4)       # 57 gross, 4 points of costs
    assert t["max_loss"] == pytest.approx(450 - 53) and t["status"] == "open"


def test_mark_holds_inside_stops_on_close_beyond_sold_strike_and_settles_at_expiry():
    t = entry()
    calm = income.mark(t, chain("2026-11-19", 25100, "2026-11-24", {k: v / 2 for k, v in PX.items()}))
    assert "status" not in calm and calm["open_pnl_points"] == pytest.approx(53 - 28.5)
    hit = income.mark(t, chain("2026-11-20", 25360, "2026-11-24",
                               {("PE", 24650): 5, ("PE", 24200): 1, ("CE", 25350): 150, ("CE", 25800): 20}))
    assert hit["status"] == "stopped" and hit["pnl_points"] == pytest.approx(53 - (5 - 1 + 150 - 20) - 4)
    worthless = income.mark(t, chain("2026-11-24", 25200, "2026-11-24", PX))
    assert worthless["status"] == "expired" and worthless["pnl_points"] == pytest.approx(53)
    deep = income.mark(t, chain("2026-11-24", 23000, "2026-11-24", PX))
    assert deep["pnl_points"] == pytest.approx(-t["max_loss"]) and deep["r_multiple"] == -1     # loss is capped


class Client:
    def __init__(self):
        self.rows = []

    def select(self, table, params=None):
        return [] if table == "fo_lots" else [dict(r) for r in self.rows]

    def insert(self, table, rows, upsert_on=None):
        self.rows += [dict(r) for r in rows]

    def update(self, table, match, values):
        for r in self.rows:
            if r["expiry"] == match["expiry"]:
                r.update(values)


def test_step_opens_on_first_session_after_expiry_catches_up_and_is_idempotent():
    days = {date(2026, 11, 17): chain("2026-11-17", 24900, "2026-11-17", {("PE", 24900): 1}),      # expiry day
            date(2026, 11, 18): chain("2026-11-18", 25000, "2026-11-24", PX),
            date(2026, 11, 19): chain("2026-11-19", 25100, "2026-11-24", PX),
            date(2026, 11, 24): chain("2026-11-24", 25200, "2026-11-24", PX)}
    c, vix = Client(), {date(2026, 11, 18): 0.146}
    kw = dict(fetch=days.get, vix_fn=lambda start: vix)
    assert income.step(c, today=date(2026, 11, 17), **kw)["opened"] == []         # nothing before the entry day
    out = income.step(c, today=date(2026, 11, 19), **kw)                          # button pressed a day late
    assert len(out["opened"]) == 1 and c.rows[0]["entry_date"] == "2026-11-18"    # still entered on the 18th
    assert c.rows[0]["last_checked"] == "2026-11-19"
    assert income.step(c, today=date(2026, 11, 19), **kw)["opened"] == [] and len(c.rows) == 1
    out = income.step(c, today=date(2026, 11, 25), **kw)
    assert [t["status"] for t in out["closed"]] == ["expired"] and c.rows[0]["pnl_rupees"] == 53 * 65


def test_step_waits_when_vix_is_missing():
    days = {date(2026, 11, 18): chain("2026-11-18", 25000, "2026-11-24", PX)}
    notes = []
    out = income.step(Client(), today=date(2026, 11, 18), fetch=days.get, vix_fn=lambda s: {}, log=notes.append)
    assert out["opened"] == [] and "VIX" in notes[0]


# ------------------------------------------------------------------ pullback book
def series(closes):
    idx = pd.bdate_range(end="2026-11-12", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1})


def test_dip_signal_needs_uptrend_and_sharp_dip():
    up = [100 + i * 0.2 for i in range(250)]
    prices = {"DIP": series(up + [148, 146, 144]),            # above the 200 DMA, three hard down days
              "CALM": series(up + [150.2, 150.4, 150.6]),     # no dip
              "DOWN": series([200 - i * 0.2 for i in range(250)] + [148, 146, 144]),   # dip in a downtrend
              "NOTFO": series(up + [148, 146, 144])}
    sig = forward.dip_signals(prices, ["DIP", "CALM", "DOWN"])
    assert list(sig["symbol"]) == ["DIP"]
    rows = forward.dip_plan(prices, [], ["DIP", "CALM", "DOWN"], now=datetime(2026, 11, 12, 20, 0, tzinfo=forward.IST))
    r = rows[0]
    assert r["book"] == forward.DIP_BOOK and r["exit_rule"] == "sma5" and r["trade_date"] == "2026-11-13"
    assert (r["entry"] - r["stop"]) * r["qty"] <= forward.CAPITAL * forward.RISK + 1


def test_sma5_exit_leaves_at_next_open_after_close_above_average():
    idx = pd.bdate_range("2026-11-02", periods=9)
    px = pd.DataFrame({"Open": [100, 99, 98, 97, 96, 95.5, 97, 99.5, 101], "High": [101, 100, 99, 98, 97, 97, 99, 101, 102],
                       "Low": [99, 98, 97, 96, 95, 95, 96.5, 98, 100], "Close": [99, 98, 97, 96, 95, 96.5, 98.5, 100, 101],
                       "Volume": 1}, index=idx)
    t = {"id": 1, "trade_date": "2026-11-09", "symbol": "X", "side": "long", "entry_type": "open", "entry": 95.0,
         "stop": 85.0, "target": 105.0, "qty": 10, "horizon_days": 10, "exit_rule": "sma5"}
    r = snapshot.evaluate_trade(t, px)
    # filled 9 Nov at 95.5; that day closes 96.5, below... 5-day avg is 96.5 -> not above; 10 Nov closes 98.5 > avg
    assert r["status"] == "exit signal" and r["fill"] == 95.5 and r["exit"] == 99.5 and r["exit_date"] == "2026-11-11"
    t2 = dict(t, exit_rule=None, target=96.9)
    assert snapshot.evaluate_trade(t2, px)["status"] == "target"         # old behaviour unchanged
