import numpy as np
import pandas as pd
import pytest

from core import backtest, market, technicals as t


def frame(close, spread=1.0, volume=1000):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=len(close))
    return pd.DataFrame({"Open": close, "High": close + spread, "Low": close - spread, "Close": close,
                         "Volume": volume}, index=idx)


def test_sma_ema():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert t.sma(s, 3).iloc[-1] == 4
    assert t.ema(pd.Series([7.0] * 10), 5).iloc[-1] == pytest.approx(7)


def test_rsi_extremes():
    assert t.rsi(pd.Series(np.arange(1, 50, dtype=float))).iloc[-1] == pytest.approx(100)
    assert t.rsi(pd.Series(np.arange(50, 1, -1, dtype=float))).iloc[-1] == pytest.approx(0)


def test_macd_flat_is_zero():
    m = t.macd(pd.Series([100.0] * 60))
    assert m.iloc[-1].abs().max() == pytest.approx(0)


def test_bollinger_constant_has_zero_width():
    b = t.bollinger(pd.Series([50.0] * 30))
    assert b["bb_width"].iloc[-1] == 0 and b["bb_up"].iloc[-1] == 50


def test_atr_constant_range():
    df = frame([100.0] * 40, spread=2.0)
    assert t.atr(df).iloc[-1] == pytest.approx(4.0)


def test_golden_cross_fires_once():
    # falls for 250 days then rises: the 50 DMA crosses above the 200 DMA exactly once
    close = np.r_[np.linspace(200, 100, 250), np.linspace(100, 260, 150)]
    x = t.add_indicators(frame(close))
    sig = t.rule_golden_cross(x)
    assert (sig == 1).sum() == 1


def test_levels_long_short():
    lv = t.levels(100, 5, "long", atr_mult=2, rr=2)
    assert (lv["stop"], lv["target"]) == (90, 120)
    lv = t.levels(100, 5, "short", atr_mult=2, rr=2)
    assert (lv["stop"], lv["target"]) == (110, 80)


@pytest.fixture
def one_signal(monkeypatch):
    """Inject a test rule that fires a long signal on bar 30 only."""
    def rule(x):
        s = pd.Series(0, index=x.index)
        s.iloc[30] = 1
        return s
    monkeypatch.setitem(t.SETUPS, "test", rule)
    monkeypatch.setitem(backtest.SETUPS, "test", rule)


def test_backtest_target_hit_uses_next_open_and_costs(one_signal):
    close = np.r_[[100.0] * 31, 101.0, 130.0, [130.0] * 10]
    df = frame(close, spread=0.5)
    df.iloc[31, df.columns.get_loc("Open")] = 101.0
    x = t.add_indicators(df)
    atr30 = x["atr"].iloc[30]
    tr = backtest.backtest_symbol("X", x, "test", cost_pct=0.002, atr_mult=2, rr=2)
    assert len(tr) == 1
    r = tr[0]
    assert r["entry"] == 101.0                      # next day's open, not the signal close
    assert r["reason"] == "target"
    assert r["exit"] == pytest.approx(101 + 4 * atr30)
    assert r["ret"] == pytest.approx((4 * atr30) / 101 - 0.002)


def test_backtest_same_bar_stop_and_target_counts_as_stop(one_signal):
    df = frame([100.0] * 45, spread=0.5)
    df.iloc[32, df.columns.get_loc("High")] = 200   # both levels touched on one bar
    df.iloc[32, df.columns.get_loc("Low")] = 1
    tr = backtest.backtest_symbol("X", t.add_indicators(df), "test")
    assert tr[0]["reason"] == "stop" and tr[0]["ret"] < 0


def test_backtest_grades():
    assert backtest.grade({"trades": 10, "expectancy_r": 1, "profit_factor": 3}) == "insufficient data"
    assert backtest.grade({"trades": 50, "expectancy_r": 0.3, "profit_factor": 1.6}).startswith("A")
    assert backtest.grade({"trades": 50, "expectancy_r": -0.1, "profit_factor": 0.8}).startswith("C")


def test_breadth_counts():
    up, down = frame(np.linspace(100, 130, 300)), frame(np.linspace(130, 100, 300))
    b = market.breadth({"UP": up, "DN": down})
    last = b.iloc[-1]
    assert last["advances"] == 1 and last["declines"] == 1
    assert last["pct_above_200dma"] == 50


def test_grade_needs_both_periods():
    good = {"trades": 50, "expectancy_r": 0.3, "profit_factor": 1.6}
    assert backtest.grade(good, {"expectancy_r": 0.1, "profit_factor": 1.2}).startswith("A")
    assert backtest.grade(good, {"expectancy_r": -0.1, "profit_factor": 0.9}).startswith("B")


def test_trailing_stop_lets_winner_run(monkeypatch):
    """Steady uptrend then a drop: the trailing stop exits near the top, far beyond a fixed 2R target."""
    def rule(x):
        s = pd.Series(0, index=x.index)
        s.iloc[30] = 1
        return s
    monkeypatch.setitem(t.SETUPS, "trail-test", rule)
    monkeypatch.setitem(backtest.SETUPS, "trail-test", rule)
    monkeypatch.setitem(t.EXITS, "trail-test", {"mode": "trail", "atr_mult": 3.0, "max_hold": 200})
    close = np.r_[[100.0] * 31, np.linspace(100, 200, 60), np.linspace(200, 150, 20)]
    x = t.add_indicators(frame(close, spread=0.5))
    tr = backtest.backtest_symbol("X", x, "trail-test")
    assert len(tr) == 1 and tr[0]["reason"] == "trail stop"
    assert tr[0]["exit"] > 180 and tr[0]["r"] > 5        # a fixed 2R target would have exited near 106


def test_market_features_no_lookahead():
    a = frame(np.linspace(100, 300, 300))
    b = frame(np.linspace(300, 100, 300))
    f = t.market_features({"A": a, "B": b})
    assert f["A"]["rs_pct"].iloc[-1] == 1.0 and f["B"]["rs_pct"].iloc[-1] == 0.5
    assert f["A"]["mkt_ok"].iloc[-1]                     # 1 of 2 above 200 DMA = 50% -> ok at threshold 50
    # changing the future must not change the past
    a2 = a.copy(); a2.iloc[-1, a2.columns.get_loc("Close")] = 1
    f2 = t.market_features({"A": a2, "B": b})
    assert f2["A"]["rs_pct"].iloc[-2] == f["A"]["rs_pct"].iloc[-2]


def test_v2_setups_run_on_demo_data():
    from core import data
    u = data.load_universe().head(40)
    px = data.DemoProvider(days=400).prices(list(u["symbol"]))
    tr = backtest.run(px, [s for s in t.SETUPS if "trailing" in s or "regime" in s])
    assert len(tr) > 0 and tr["setup"].nunique() >= 2
    sig = t.scan(px, lookback_days=60)
    assert {"exit"} <= set(sig.columns)
