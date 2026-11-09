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
