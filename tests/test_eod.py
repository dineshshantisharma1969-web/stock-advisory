"""End-of-day P&L and carried-forward calls.

Numbers below are hand-worked from the bars in `px()` and the same fill rules as
the idea book and the paper scorer.
"""
from datetime import date, datetime, timezone, timedelta

import pandas as pd
import pytest

from core import eod, ledger, snapshot

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 19, 16, 0, tzinfo=IST)  # Thursday, after the 15:30 close


def px(symbol_rows):
    """symbol -> OHLC frame. Each row is (open, high, low, close) from 2026-11-16."""
    out = {}
    for sym, rows in symbol_rows.items():
        idx = pd.bdate_range("2026-11-16", periods=len(rows))
        out[sym] = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1)
    return out


# Mon 16, Tue 17, Wed 18, Thu 19. Quiet bars: nothing trades through 91 or 131.
QUIET = [(100, 101, 99, 100), (101, 103, 100, 102), (102, 104, 101, 103), (103, 106, 102, 105)]


def idea(**kw):
    row = {
        "id": 1, "created_utc": "2026-11-16T04:00:00+00:00", "kind": "swing", "symbol": "ABC",
        "setup": "Momentum", "side": "long", "entry": 100.0, "stop": 90.0, "target": 130.0,
        "horizon_days": 20, "instrument": "Equity", "lot_size": 1, "rationale": "test", "tracks": "swing",
    }
    row.update(kw)
    return row


def paper(**kw):
    t = {
        "id": 1, "trade_date": "2026-11-17", "book": "fwd-momentum", "symbol": "ABC", "segment": "swing",
        "side": "long", "entry_type": "open", "entry": 100.0, "stop": 90.0, "target": 200.0, "qty": 10,
        "horizon_days": 20, "setup": "Momentum leaders", "grade": "forward test",
    }
    t.update(kw)
    return t


def summary(ideas=None, prices=None, paper_trades=None, journal=None, now=NOW, session=None, marks=None):
    return eod.end_of_day_summary(
        ideas=pd.DataFrame(ideas or []), prices=prices or {}, paper_trades=paper_trades or [],
        journal=journal if journal is not None else pd.DataFrame(), now=now, session=session, marks=marks,
    )


def test_closed_idea_today_books_realised_pnl_and_reason():
    # Fill at Tue open 101. Target = 101 + 20 = 121, first touched on Thu 19.
    bars = [(100, 101, 99, 100), (101, 103, 100, 102), (102, 104, 101, 103), (103, 125, 102, 122)]
    s = summary([idea(target=120.0)], px({"ABC": bars}))
    assert s.session == date(2026, 11, 19)
    assert len(s.closed_today) == 1 and s.carried_forward == []
    c = s.closed_today[0]
    assert c.symbol == "ABC" and c.book == "Idea book" and c.strategy == "Momentum"
    assert c.entry_date == "2026-11-17" and c.entry_price == pytest.approx(101)
    assert c.price == pytest.approx(121) and c.exit_reason == "target"
    assert c.pnl_inr == pytest.approx(20) and c.pnl_pct == pytest.approx(20 / 101)
    assert s.realised_pnl == pytest.approx(20)
    assert s.today_unrealised_mtm == pytest.approx(0)
    assert s.total_day_pnl == pytest.approx(20)


def test_open_idea_is_carried_with_levels_days_and_session_move():
    s = summary([idea()], px({"ABC": QUIET}))
    assert s.closed_today == [] and len(s.carried_forward) == 1
    c = s.carried_forward[0]
    # Fill 101, risk 10 -> stop 91, target 101 + 30 = 131. Latest close 105.
    assert c.entry_date == "2026-11-17" and c.entry_price == pytest.approx(101)
    assert c.price == pytest.approx(105)
    assert c.stop == pytest.approx(91) and c.target == pytest.approx(131)
    assert c.pnl_inr == pytest.approx(4) and c.pnl_pct == pytest.approx(4 / 101)
    assert c.days_held == 3  # Tue, Wed, Thu
    assert c.day_mtm_inr == pytest.approx(105 - 103)  # Wednesday close to Thursday close
    assert c.exit_reason is None and c.state == "open"
    assert s.unrealised_mtm == pytest.approx(4)
    assert s.today_unrealised_mtm == pytest.approx(2)
    assert s.total_day_pnl == pytest.approx(2)


def test_expired_idea_on_the_session_is_a_time_exit():
    s = summary([idea(horizon_days=3, stop=50, target=500)], px({"ABC": QUIET}))
    assert len(s.closed_today) == 1
    c = s.closed_today[0]
    assert c.exit_reason == "time" and c.price == pytest.approx(105)
    assert c.pnl_inr == pytest.approx(4)


def test_older_close_is_neither_today_nor_carried():
    # Stop is 0.5 under the published entry, so the fill-adjusted stop (100.5) is hit on the fill day (Tue 17).
    s = summary([idea(stop=99.5, target=200)], px({"ABC": QUIET}))
    assert s.closed_today == [] and s.carried_forward == []
    assert s.realised_pnl == 0 and s.total_day_pnl == 0


def test_fundamental_holding_is_carried_forward():
    s = summary([idea(kind="fundamental", setup="Quality compounder", lot_size=None)], px({"ABC": QUIET}))
    assert len(s.carried_forward) == 1 and s.closed_today == []
    c = s.carried_forward[0]
    assert c.book == "Idea book" and c.strategy == "Quality compounder"
    assert c.entry_date == "2026-11-16" and c.entry_price == pytest.approx(100)  # same-day open
    assert c.price == pytest.approx(105) and c.pnl_inr == pytest.approx(5)
    assert c.days_held == 4


def test_forward_book_open_and_stopped_today():
    prices = px({
        "OPEN": QUIET,
        "STOP": [(100, 101, 99, 100), (101, 103, 100, 102), (102, 104, 101, 103), (103, 104, 90, 92)],
        "DIP": QUIET,
    })
    trades = [
        paper(id=1, symbol="OPEN"),
        paper(id=2, symbol="STOP", stop=95.0),
        paper(id=3, symbol="DIP", book="fwd-dip", setup="Pullback"),
    ]
    s = summary(prices=prices, paper_trades=trades)
    by_sym = {c.symbol: c for c in s.closed_today + s.carried_forward}
    assert set(by_sym) == {"OPEN", "STOP"}  # the dip book is not this summary
    stopped = by_sym["STOP"]
    assert stopped.state == "closed" and stopped.exit_reason == "stop" and stopped.book == "fwd-momentum"
    assert stopped.price == pytest.approx(95)
    gross, costs = (95 - 101) * 10, 0.001 * (101 + 95) * 10
    assert stopped.pnl_inr == pytest.approx(gross - costs)
    assert stopped.pnl_pct == pytest.approx((gross - costs) / (101 * 10))
    held = by_sym["OPEN"]
    assert held.state == "open" and held.strategy == "Momentum leaders"
    ev = snapshot.evaluate_trade(paper(id=1, symbol="OPEN"), prices["OPEN"])
    assert held.pnl_inr == pytest.approx(ev["net_pnl"])
    assert held.stop == pytest.approx(ev["stop_now"])
    assert held.day_mtm_inr == pytest.approx((105 - 103) * 10)
    assert held.days_held == 3
    assert s.realised_pnl == pytest.approx(stopped.pnl_inr)
    assert s.today_unrealised_mtm == pytest.approx(held.day_mtm_inr)
    assert s.total_day_pnl == pytest.approx(s.realised_pnl + s.today_unrealised_mtm)
    assert s.unrealised_mtm == pytest.approx(held.pnl_inr)


def test_pending_order_is_not_a_carried_call():
    s = summary(prices=px({"ABC": QUIET}), paper_trades=[paper(trade_date="2026-11-20")])
    assert s.closed_today == [] and s.carried_forward == []


def test_journal_manual_close_and_open_mark(tmp_path):
    db = tmp_path / "j.db"
    prices = px({"ABC": QUIET})
    ledger.add_trade({"segment": "Swing (cash)", "symbol": "ABC", "side": "long", "qty": 5,
                      "entry_date": "2026-11-17", "entry": 200, "charges": 15, "notes": "open"}, db)
    closed = ledger.add_trade({"segment": "Futures", "symbol": "XYZ", "side": "short", "qty": 4,
                               "entry_date": "2026-11-10", "entry": 50, "charges": 8, "notes": "booked"}, db)
    ledger.close_trade(closed, "2026-11-19", 40, 8, db)
    # An older manual close must not count as today.
    older = ledger.add_trade({"segment": "Swing (cash)", "symbol": "OLD", "side": "long", "qty": 1,
                              "entry_date": "2026-11-10", "entry": 10, "charges": 0}, db)
    ledger.close_trade(older, "2026-11-12", 12, 0, db)
    journal = ledger.trades(prices, db)
    s = summary(prices=prices, journal=journal)
    assert {c.symbol for c in s.closed_today} == {"XYZ"}
    booked = s.closed_today[0]
    assert booked.book == "Journal" and booked.exit_reason == "manual" and booked.strategy == "Futures"
    assert booked.pnl_inr == pytest.approx((50 - 40) * 4 - 8) and booked.pnl_pct == pytest.approx(32 / (50 * 4))
    assert len(s.carried_forward) == 1
    held = s.carried_forward[0]
    assert held.symbol == "ABC" and held.price == pytest.approx(105)
    assert held.pnl_inr == pytest.approx((105 - 200) * 5 - 15)
    assert held.pnl_pct == pytest.approx(held.pnl_inr / (200 * 5))
    assert held.stop is None and held.target is None and held.days_held == 3
    # Entered before today, so today's move is the price change only (charges stay in the from-entry P&L).
    assert held.day_mtm_inr == pytest.approx((105 - 103) * 5)
    assert s.realised_pnl == pytest.approx(booked.pnl_inr)
    assert s.total_day_pnl == pytest.approx(booked.pnl_inr + held.day_mtm_inr)


def test_short_idea_pnl_sign():
    # Fill 101, stop for a short is fill + risk. Risk = 10, so stop 111. Target = 101 - 30 = 71.
    # Thu low 70 hits the target.
    bars = [(100, 101, 99, 100), (101, 103, 100, 102), (102, 104, 101, 103), (103, 104, 70, 72)]
    s = summary([idea(side="short", entry=100, stop=110, target=70)], px({"ABC": bars}))
    assert len(s.closed_today) == 1
    c = s.closed_today[0]
    assert c.exit_reason == "target" and c.price == pytest.approx(71)
    assert c.pnl_inr == pytest.approx((101 - 71) * 1)


def test_empty_sources_are_zero_and_format_says_so():
    s = summary(now=NOW)
    assert s.session is None and s.closed_today == [] and s.carried_forward == []
    assert s.realised_pnl == 0 and s.unrealised_mtm == 0
    assert s.today_unrealised_mtm == 0 and s.total_day_pnl == 0
    assert s.as_of == NOW
    text = eod.format_eod(s)
    assert "15:30" not in text  # no session yet: the clock time is the as-of, not a fabricated close
    assert "19 Nov 2026, 16:00 IST" in text
    assert "No calls closed today." in text
    assert "No open calls carried forward." in text


def test_as_of_is_the_cash_close_in_ist_once_the_session_exists():
    s = summary(prices=px({"ABC": QUIET}), now=NOW)
    assert s.session == date(2026, 11, 19)
    assert s.as_of == datetime(2026, 11, 19, 15, 30, tzinfo=IST)
    assert "19 Nov 2026, 15:30 IST" in eod.format_eod(s)
    early = summary(prices=px({"ABC": QUIET}), now=datetime(2026, 11, 19, 14, 0, tzinfo=IST))
    assert early.as_of == datetime(2026, 11, 19, 14, 0, tzinfo=IST)


def test_live_mark_squares_off_a_stop_and_advances_the_session():
    # Bars end Wednesday. At 15:45 Thursday a live price is through the stop, so Thursday is the session.
    bars = QUIET[:3]
    prices = px({"ABC": bars})
    now = datetime(2026, 11, 19, 15, 45, tzinfo=IST)
    s = summary([idea()], prices, now=now, marks={"ABC": 80.0})
    assert s.session == date(2026, 11, 19)
    assert len(s.closed_today) == 1 and s.carried_forward == []
    c = s.closed_today[0]
    assert c.exit_reason == "stop" and c.price == pytest.approx(91)  # fill 101, risk 10
    assert c.pnl_inr == pytest.approx(91 - 101)
    assert s.realised_pnl == pytest.approx(-10)
    assert s.today_unrealised_mtm == 0


def test_live_mark_reprices_an_open_call_from_the_previous_close():
    bars = QUIET[:3]  # last official close is Wednesday 103
    now = datetime(2026, 11, 19, 15, 45, tzinfo=IST)
    s = summary([idea()], px({"ABC": bars}), now=now, marks={"ABC": 110.0})
    assert s.session == date(2026, 11, 19) and s.closed_today == []
    c = s.carried_forward[0]
    assert c.price == pytest.approx(110)
    assert c.pnl_inr == pytest.approx(110 - 101)
    assert c.day_mtm_inr == pytest.approx(110 - 103)
    assert s.total_day_pnl == pytest.approx(7)


def test_marks_do_not_invent_a_session_before_the_close():
    now = datetime(2026, 11, 19, 14, 0, tzinfo=IST)
    s = summary([idea()], px({"ABC": QUIET[:3]}), now=now, marks={"ABC": 110.0})
    assert s.session == date(2026, 11, 18)  # still the last bar; the cash close has not happened


def test_load_summary_skips_private_books_for_clients(monkeypatch):
    from core import cloud

    seen = []

    class DB:
        def select(self, table, params=None):
            seen.append(table)
            return []

    monkeypatch.setattr(cloud, "client", lambda: DB())

    def boom(*a, **k):
        raise AssertionError("clients must not read the journal")

    monkeypatch.setattr(ledger, "trades", boom)
    monkeypatch.setattr(ledger, "ideas", lambda path=None: pd.DataFrame())
    s = eod.load_summary({}, admin=False, now=NOW)
    assert seen == []
    assert s.closed_today == [] and s.carried_forward == []


def test_frames_round_trip_display_columns():
    s = summary([idea()], px({"ABC": QUIET}))
    closed = eod.closed_frame(s)
    carried = eod.carried_frame(s)
    assert list(closed.columns) == ["symbol", "book", "strategy", "side", "entry_date", "entry_price",
                                    "exit_price", "exit_reason", "pnl_inr", "pnl_pct"]
    assert closed.empty
    assert list(carried.columns) == ["symbol", "book", "strategy", "side", "entry_date", "entry_price",
                                     "close", "pnl_inr", "pnl_pct", "stop", "target", "days_held"]
    assert carried.iloc[0]["symbol"] == "ABC" and carried.iloc[0]["days_held"] == 3
