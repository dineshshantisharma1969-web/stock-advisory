"""Regression tests for the ten findings of the Nov 2026 code review."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import daily_bot as bot
from core import data, forward, income, live, snapshot
from tests.test_income_dip import PX, Client, chain, entry

IST = forward.IST


def bars(closes, start="2026-10-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1})


def trailing_trade(px, **kw):
    return dict({"id": 1, "symbol": "A", "trade_date": px.index[20].strftime("%Y-%m-%d"), "side": "long",
                 "entry_type": "open", "entry": 100.0, "stop": 94.0, "target": 118.0, "qty": 100,
                 "horizon_days": 60, "trail_atr": 3.0}, **kw)


# 1 + 2 ---------------------------------------------------------------------------------------------
def test_open_trailing_trade_reports_the_trailed_stop_and_r_uses_the_initial_risk():
    px = bars([100.0] * 21 + [100 + 2 * i for i in range(1, 15)])          # steady rise: the stop ratchets up
    r = snapshot.evaluate_trade(trailing_trade(px), px)
    assert r["status"] == "open" and r["stop_now"] > 110                    # far above the initial 94
    assert r["r_multiple"] == pytest.approx(r["net_pnl"] / (6.0 * 100))     # risk = entry - INITIAL stop


def test_closed_trailing_trade_r_is_not_inflated_by_the_trailed_stop():
    px = bars([100.0] * 21 + [100 + 2 * i for i in range(1, 15)] + [100.0])   # then a collapse through the trail
    r = snapshot.evaluate_trade(trailing_trade(px), px)
    assert r["status"] == "trail stop" and r["stop_now"] is None
    assert r["r_multiple"] == pytest.approx(r["net_pnl"] / 600.0)


def quotes(**rows):
    return pd.DataFrame.from_dict(rows, orient="index").assign(updated_at=pd.Timestamp.now(tz="UTC"))


def test_live_view_squares_off_at_the_trailed_stop():
    t = pd.DataFrame([{"symbol": "A", "side": "long", "fill": 100.0, "qty": 10, "status": "open", "stop": 94.0,
                       "stop_now": 118.0, "target": 130.0, "trail_atr": 3.0}])
    p = live.live_pnl(t, quotes(A={"ltp": 115.0})).iloc[0]
    assert p["live_status"] == "trail stop (squared off)" and p["live_exit"] == 118.0
    assert live.live_pnl(t, quotes(A={"ltp": 119.0})).iloc[0]["live_status"] == "open"


# 5 -------------------------------------------------------------------------------------------------
def test_todays_new_order_is_filled_at_the_open_and_marked_live():
    row = {"symbol": "A", "side": "long", "fill": None, "qty": 10, "status": "pending", "stop": 90.0,
           "target": 130.0, "entry_type": "open", "trade_date": "2026-11-16", "stop_now": None}
    t = pd.DataFrame([row, dict(row, symbol="B", trade_date="2026-11-17"), dict(row, symbol="C", entry_type="limit")])
    p = live.live_pnl(t, quotes(A={"ltp": 104.0, "open": 101.0}, B={"ltp": 50.0, "open": 50.0},
                                C={"ltp": 50.0, "open": 50.0}), today=date(2026, 11, 16))
    assert list(p["symbol"]) == ["A"]                                   # tomorrow's order and limit orders wait
    assert p.iloc[0]["live_fill"] == 101.0 and p.iloc[0]["live_status"] == "open"
    assert p.iloc[0]["live_pnl"] == pytest.approx((104 - 101) * 10 - 0.001 * 101 * 10)


# 3 -------------------------------------------------------------------------------------------------
def test_yahoo_unfinished_bar_is_dropped_during_market_hours_only():
    df = bars([1, 2, 3], start="2026-11-12")                            # Thu, Fri, Mon 16 Nov
    assert len(data.drop_unfinished(df, datetime(2026, 11, 16, 9, 40, tzinfo=IST))) == 2
    assert len(data.drop_unfinished(df, datetime(2026, 11, 16, 16, 0, tzinfo=IST))) == 3
    assert len(data.drop_unfinished(df, datetime(2026, 11, 17, 9, 5, tzinfo=IST))) == 3


# 4 -------------------------------------------------------------------------------------------------
def test_books_refuse_to_trade_on_stale_prices():
    px = {"A": bars([100.0] * 30, start="2026-10-01")}                  # last bar 11 Nov
    fresh = datetime(2026, 11, 12, 9, 5, tzinfo=IST)
    forward.check_fresh(px, fresh)                                      # next morning: fine
    forward.check_fresh(px, datetime(2026, 11, 13, 9, 5, tzinfo=IST))   # one holiday in between: fine
    with pytest.raises(forward.StalePrices):
        forward.check_fresh(px, datetime(2026, 11, 17, 9, 5, tzinfo=IST))
    assert forward.is_fresh(date(2026, 11, 13), date(2026, 11, 16))     # Friday -> Monday
    assert not forward.is_fresh(date(2026, 11, 11), date(2026, 11, 17))


def test_stale_signal_is_not_planned(monkeypatch):
    from core import technicals
    from tests.test_forward import sig_frame
    monkeypatch.setattr(technicals, "scan", lambda *a, **k: sig_frame([("A", 1000, 20, 0.95)]))   # signal 12 Nov
    assert len(forward.plan({}, [], now=datetime(2026, 11, 12, 20, 0, tzinfo=IST))) == 1
    assert forward.plan({}, [], now=datetime(2026, 11, 18, 9, 5, tzinfo=IST)) == []


# 9 -------------------------------------------------------------------------------------------------
def test_condor_settles_on_the_expiry_day_close_when_that_days_file_is_missing():
    t = entry()                                                         # expiry 24 Nov, sold 24650 / 25350
    late = chain("2026-11-25", 23000, "2026-12-01", PX)                 # next day: Nifty has collapsed
    assert income.mark(t, late) == {}                                   # cannot settle on the 25th's price
    done = income.mark(t, late, settle=(25200.0, date(2026, 11, 24)))
    assert done["status"] == "expired" and done["exit_date"] == "2026-11-24"
    assert done["exit_spot"] == 25200.0 and done["pnl_points"] == pytest.approx(53)


def test_condor_step_waits_then_uses_the_expiry_close():
    days = {date(2026, 11, 18): chain("2026-11-18", 25000, "2026-11-24", PX),
            date(2026, 11, 25): chain("2026-11-25", 23000, "2026-12-01", PX)}
    c = Client()
    kw = dict(fetch=days.get, vix_fn=lambda s: {date(2026, 11, 18): 0.146})
    income.step(c, today=date(2026, 11, 18), spot_fn=lambda d: None, **kw)
    out = income.step(c, today=date(2026, 11, 25), spot_fn=lambda d: None, **kw)
    row = next(r for r in c.rows if r["expiry"] == "2026-11-24")
    assert out["closed"] == [] and row["status"] == "open" and row["last_checked"] == "2026-11-18"
    out = income.step(c, today=date(2026, 11, 25), spot_fn=lambda d: (25200.0, d), **kw)
    row = next(r for r in c.rows if r["expiry"] == "2026-11-24")
    assert row["status"] == "expired" and row["pnl_points"] == pytest.approx(53)


# 7 -------------------------------------------------------------------------------------------------
def test_housekeeping_refreshes_weekly_parts_and_republishes(monkeypatch):
    from core import backtest
    saved, published = {}, []
    u = pd.DataFrame({"symbol": ["A", "B"]})
    monkeypatch.setattr(data, "load_cache", lambda name, max_age_hours=None: (saved.get(name), None))
    monkeypatch.setattr(data, "save_cache", lambda name, obj: saved.__setitem__(name, obj))
    monkeypatch.setattr(data.YahooProvider, "all_fundamentals",
                        lambda self, syms, progress=None, pause=0.3, prices=None: {s: {"symbol": s} for s in syms})
    monkeypatch.setattr(data, "usable_fundamentals", lambda f: True)
    monkeypatch.setattr(backtest, "run", lambda prices: pd.DataFrame())
    monkeypatch.setattr(bot, "saved_screen", lambda universe: "SCREEN")
    monkeypatch.setattr(snapshot, "publish", lambda c, p, un, screen, bt, log=print: published.append(screen))
    problems = []

    def step(name, fn):
        try:
            return fn()
        except Exception as e:
            problems.append(name)

    did = bot.housekeeping(None, {}, u, step)
    assert did == ["fundamentals", "backtest grades"] and not problems
    assert set(saved["fund_live"]) == {"A", "B"} and published == ["SCREEN"]
