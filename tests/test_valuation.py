import math

import numpy as np
import pandas as pd
import pytest

from core import config, data, valuation as v


def company(**kw):
    f = data.empty_fundamentals("TEST")
    f.update(dict(price=100.0, shares=1e8, market_cap=1e10, beta=1.0, total_debt=0.0, cash=0.0,
                  book_equity=5e9, revenue=[1e10, 1.1e10, 1.21e10], ebit=[1e9, 1.1e9, 1.21e9],
                  ebitda=[1.2e9, 1.3e9, 1.45e9], net_income=[7e8, 7.7e8, 8.47e8], eps=[7.0, 7.7, 8.47],
                  ocf=[9e8, 9e8, 9e8], capex=[-3e8, -3e8, -3e8], interest_expense=0.0, tax_rate=0.25))
    f.update(kw)
    return f


def test_cagr():
    assert v.cagr([100, 121]) == pytest.approx(0.21)  # one year
    assert v.cagr([100, 110, 121]) == pytest.approx(0.10)
    assert math.isnan(v.cagr([-5, 10]))      # sign flip -> undefined
    assert math.isnan(v.cagr([10]))


def test_dcf_matches_gordon_growth_when_growth_equals_terminal():
    # With constant growth g, EV must equal the closed-form FCF1 / (r - g).
    fcf0, g, r = 100.0, 0.05, 0.11
    ev = v.dcf_enterprise_value(fcf0, g, r, terminal=g, years=10)["ev"]
    assert ev == pytest.approx(fcf0 * (1 + g) / (r - g), rel=1e-9)


def test_dcf_rejects_discount_below_terminal():
    with pytest.raises(ValueError):
        v.dcf_enterprise_value(100, 0.1, 0.04, terminal=0.05)


def test_wacc_unlevered_is_cost_of_equity():
    f = company(beta=1.2)
    assert v.wacc(f) == pytest.approx(config.RISK_FREE + 1.2 * config.EQUITY_RISK_PREMIUM)


def test_wacc_with_debt_is_lower_than_cost_of_equity():
    f = company(total_debt=5e9, interest_expense=4e8)  # 8% pre-tax cost of debt
    ke = config.RISK_FREE + config.EQUITY_RISK_PREMIUM
    expected = (1e10 * ke + 5e9 * 0.08 * 0.75) / 1.5e10
    assert v.wacc(f) == pytest.approx(expected)


def test_base_fcf_is_average_ocf_plus_capex():
    assert v.base_fcf(company()) == pytest.approx(6e8)


def test_dcf_per_share_subtracts_net_debt():
    f0, f1 = company(), company(total_debt=1e9, cash=2e8)
    a = v.dcf_value_per_share(f0, growth=0.1, discount=0.12)["value"]
    b = v.dcf_value_per_share(f1, growth=0.1, discount=0.12)["value"]
    assert a - b == pytest.approx(8e8 / 1e8)  # net debt / shares


def test_reverse_dcf_round_trip():
    # Price the company at the DCF value for 14% growth; reverse DCF must recover 14%.
    f = company()
    d = v.dcf_value_per_share(f, growth=0.14, discount=0.12)
    f["market_cap"] = d["equity_value"]
    assert v.reverse_dcf_growth(f, discount=0.12) == pytest.approx(0.14, abs=1e-6)


def test_negative_fcf_gives_no_dcf():
    f = company(ocf=[1e8, 1e8, 1e8], capex=[-5e8, -5e8, -5e8])
    assert math.isnan(v.dcf_value_per_share(f)["value"])
    assert math.isnan(v.reverse_dcf_growth(f))


def test_multiples():
    m = v.analyse(company())
    assert m["pe"] == pytest.approx(100 / 8.47)
    assert m["eps_cagr"] == pytest.approx(0.10)
    assert m["peg"] == pytest.approx((100 / 8.47) / 10)
    assert m["ev_ebitda"] == pytest.approx(1e10 / 1.45e9)
    assert m["roe"] == pytest.approx(8.47e8 / 5e9)
    assert m["roce"] == pytest.approx(1.21e9 / 5e9)
    assert m["np_growth_yoy"] == pytest.approx(0.10)
    assert m["pb"] == pytest.approx(2.0)


def test_financials_skip_dcf_and_ev():
    m = v.analyse(company(), "Financial Services")
    assert m["is_financial"] and math.isnan(m["dcf_value"]) and math.isnan(m["ev_ebitda"])
    assert not math.isnan(m["pe"])


def test_quality_gate_blocks_value_traps():
    good = pd.Series({"pe": 12, "np_cagr": 0.1, "debt_equity": 0.5, "interest_cover": 8, "is_financial": False})
    assert v.quality_gate(good) == "pass"
    trap = good.copy()
    trap["np_cagr"], trap["debt_equity"] = -0.2, 3.0
    out = v.quality_gate(trap)
    assert "profit not growing" in out and "debt/equity" in out


def test_screen_runs_and_strong_ideas_all_pass_gate():
    u = data.load_universe().head(60)
    fu = data.DemoProvider(days=300).all_fundamentals(list(u["symbol"]))
    sc = v.screen(fu, u)
    assert len(sc) == 60 and sc["score"].between(0, 100).all()
    assert (sc.loc[sc["verdict"] == "Strong idea", "quality_gate"] == "pass").all()


def test_refresh_merge_keeps_old_data_for_failures():
    old = {"A": {"v": 1}, "B": {"v": 1}}
    new = {"A": {"v": 2}, "B": {"v": None}, "C": {"v": 3}}
    merged, n = data.merge_refresh(old, new, lambda f: f["v"] is not None)
    assert n == 2 and merged == {"A": {"v": 2}, "B": {"v": 1}, "C": {"v": 3}}


def test_usable_fundamentals():
    good = company()
    assert data.usable_fundamentals(good)
    assert not data.usable_fundamentals(company(price=np.nan))
    assert not data.usable_fundamentals(company(revenue=[], net_income=[]))
    assert not data.usable_fundamentals({**company(), "source": "error: 401"})
