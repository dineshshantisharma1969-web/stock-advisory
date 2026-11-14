from datetime import date, datetime

import pandas as pd
import pytest

import daily_bot as bot
from core import live


def test_last_session_morning_uses_previous_weekday():
    ist = bot.IST
    assert bot.last_session(datetime(2026, 11, 16, 8, 30, tzinfo=ist)) == date(2026, 11, 13)   # Monday -> Friday
    assert bot.last_session(datetime(2026, 11, 17, 8, 30, tzinfo=ist)) == date(2026, 11, 16)
    assert bot.last_session(datetime(2026, 11, 17, 20, 0, tzinfo=ist)) == date(2026, 11, 17)   # evening -> today


def test_book_lines_totals_open_and_booked():
    trades = [{"id": 1, "symbol": "GLAND"}, {"id": 2, "symbol": "WELSPUNLIV"}, {"id": 3, "symbol": "OLD"}]
    res = [{"trade_id": 1, "status": "open", "net_pnl": 3802.0}, {"trade_id": 2, "status": "open", "net_pnl": -3082.0},
           {"trade_id": 3, "status": "stop", "net_pnl": -20000.0}]
    out = bot.book_lines("Momentum leaders", trades, res, [{"symbol": "TCS"}])
    assert out[1] == "Total P&L: *−₹19,280* (open +₹720, booked −₹20,000)"
    assert "• GLAND: +₹3,802" in out and "New today: TCS (at 9:15 open)" in out


def q(**prices):
    now = pd.Timestamp.now(tz="UTC")
    return pd.DataFrame({"ltp": prices, "updated_at": now})


BASE = {"side": "long", "fill": 100.0, "qty": 10, "status": "open", "stop": 90.0, "target": 120.0}


def test_live_square_off_at_stop_and_target():
    t = pd.DataFrame([dict(BASE, symbol="A"), dict(BASE, symbol="B"), dict(BASE, symbol="C"),
                      dict(BASE, symbol="D", trail_atr=3.0), dict(BASE, symbol="E", exit_rule="sma5")])
    p = live.live_pnl(t, q(A=89.0, B=121.0, C=105.0, D=125.0, E=125.0)).set_index("symbol")
    assert p.loc["A", "live_status"] == "stop (squared off)" and p.loc["A", "live_exit"] == 90.0
    assert p.loc["A", "live_pnl"] == pytest.approx((90 - 100) * 10 - 0.001 * 100 * 10 - 0.001 * 90 * 10)
    assert p.loc["B", "live_status"] == "target (squared off)" and p.loc["B", "live_exit"] == 120.0
    assert p.loc["C", "live_status"] == "open" and p.loc["C", "live_pnl"] == pytest.approx(50 - 1)
    assert p.loc["D", "live_status"] == "open"          # trailing-stop trades have no fixed target
    assert p.loc["E", "live_status"] == "open"          # pullback trades exit on their own rule


class FakeDB:
    def __init__(self):
        self.tables = {"paper_trades": [], "condor_trades": [], "fo_lots": [{"symbol": "A", "lot_size": 100}],
                       "bot_runs": [], "app_errors": []}

    def select(self, table, params=None):
        return [dict(r) for r in self.tables.get(table, [])]

    def insert(self, table, rows, upsert_on=None):
        for r in rows:
            self.tables.setdefault(table, []).append(dict(r, id=len(self.tables.get(table, [])) + 1))
        return rows

    def update(self, table, match, values):
        return []


def test_bot_runs_end_to_end_with_fakes(monkeypatch, tmp_path):
    from core import backtest, cloud, config, data, forward, income, snapshot
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(bot, "HERE", tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    db = FakeDB()
    monkeypatch.setattr(cloud, "client", lambda: db)
    idx = pd.bdate_range(end="2026-11-13", periods=300)
    px = {"A": pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1}, index=idx)}
    monkeypatch.setattr(data, "load_universe", lambda: pd.DataFrame({"symbol": ["A"], "name": ["A"], "industry": ["x"]}))
    monkeypatch.setattr(data, "refresh_prices", lambda syms, log=print, today=None: (px, "Prices updated (test)"))
    monkeypatch.setattr(data, "save_cache", lambda name, obj: None)
    monkeypatch.setattr(data, "load_cache", lambda name, max_age_hours=None: (None, None))
    monkeypatch.setattr(backtest, "run", lambda prices: pd.DataFrame())
    calls = []
    monkeypatch.setattr(snapshot, "publish", lambda *a, **k: calls.append("publish") or {"paper_results": 0})
    monkeypatch.setattr(forward, "run", lambda c, p: [{"symbol": "A"}])
    monkeypatch.setattr(forward, "run_dip", lambda c, p: [])
    monkeypatch.setattr(income, "step", lambda c, log=None: {"opened": [], "closed": [], "open": 0})
    assert bot.main([]) == 0
    assert calls == ["publish"]
    run = db.tables["bot_runs"][0]
    assert run["ok"] is True and "New today: A (at 9:15 open)" in run["summary"]
