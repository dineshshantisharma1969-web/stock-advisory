import numpy as np
import pytest

from core import personal as pf
from core import results


# ---------------------------------------------------------------- personal finance
def test_emi_known_value():
    # ₹10 lakh at 10% for 10 years: standard bank EMI ₹13,215.07
    assert pf.emi(1_000_000, 0.10, 10) == pytest.approx(13215.07, abs=0.01)


def test_amortisation_repays_principal():
    s = pf.amortise(1_000_000, 0.10, 10)
    assert len(s) == 120 and s["principal"].sum() == pytest.approx(1_000_000, abs=0.01)


def test_prepayment_saves_interest_and_time():
    r = pf.prepayment_benefit(5_000_000, 0.085, 20, extra_monthly=5000)
    assert r["interest_saved"] > 0 and r["months_saved"] > 0


def test_sip_matches_annuity_due_formula():
    i = pf.monthly_rate(0.12)
    n = 120
    expected = 10000 * ((1 + i) ** n - 1) / i * (1 + i)
    assert pf.sip_fv(10000, 0.12, 10) == pytest.approx(expected)


def test_required_sip_inverts_sip():
    fv = pf.sip_fv(15000, 0.11, 12)
    assert pf.required_sip(fv, 0.11, 12) == pytest.approx(15000)


def test_step_up_increases_value():
    assert pf.sip_fv(10000, 0.12, 10, 0.1) > pf.sip_fv(10000, 0.12, 10)


def test_retirement_corpus_zero_real_return():
    # if returns equal inflation, corpus = first-year expense x years
    r = pf.retirement_corpus(50000, 0, 20, inflation=0.06, post_return=0.06)
    assert r["corpus"] == pytest.approx(50000 * 12 * 20)


def test_new_regime_rebate_up_to_12_lakh():
    # salary 12.75L - 75k standard deduction = 12L taxable -> nil tax
    assert pf.income_tax(1_275_000, "new")["total_tax"] == 0


def test_new_regime_marginal_relief():
    # taxable 12.25L: slab tax 63,750 but marginal relief caps it at the 25,000 above 12L; +4% cess
    r = pf.income_tax(1_300_000, "new")
    assert r["slab_tax"] == pytest.approx(63750)
    assert r["total_tax"] == pytest.approx(26000)


def test_old_regime_hand_computed():
    # 10L salary - 50k std - 1.5L 80C = 8L taxable: 12,500 + 60,000 = 72,500; +4% cess = 75,400
    assert pf.income_tax(1_000_000, "old", deductions=150_000)["total_tax"] == pytest.approx(75400)


def test_new_regime_high_income():
    # 30L salary: taxable 29.25L; slabs: 20k+40k+60k+80k+1L + 5.25L*30% = 4,57,500; +cess = 4,75,800
    assert pf.income_tax(3_000_000, "new")["total_tax"] == pytest.approx(475800)


def test_allocation_sums_to_100():
    for age in (22, 45, 70):
        for risk in ("conservative", "moderate", "aggressive"):
            assert sum(pf.asset_allocation(age, risk).values()) == 100


# ---------------------------------------------------------------- quarterly results
def test_fy_quarter_labels():
    assert results.fy_quarter("2026-09-30") == "Q2 FY27"
    assert results.fy_quarter("2026-06-30") == "Q1 FY27"
    assert results.fy_quarter("2026-12-31") == "Q3 FY27"
    assert results.fy_quarter("2027-03-31") == "Q4 FY27"


def test_company_result_yoy_qoq():
    q = {"period_end": ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"],
         "revenue": [100.0, 105, 110, 115, 120], "ebit": [10.0, 11, 12, 13, 15],
         "net_income": [8.0, 8, 9, 10, 12], "eps": [1.0] * 5}
    r = results.company_result(q)
    assert r["quarter"] == "Q2 FY27"
    assert r["rev_yoy"] == pytest.approx(0.20) and r["rev_qoq"] == pytest.approx(120 / 115 - 1)
    assert r["np_yoy"] == pytest.approx(0.5)
    assert r["margin_chg_yoy"] == pytest.approx(0.125 - 0.10)
    assert results.grade(r) == "Strong"


def test_turnaround_grade():
    q = {"period_end": ["2025-09-30", "2026-09-30"], "revenue": [100.0, 110], "ebit": [-5.0, 8],
         "net_income": [-4.0, 6], "eps": [-1.0, 1]}
    assert results.grade(results.company_result(q)) == "Turnaround"


def test_missing_year_ago_quarter_is_insufficient():
    q = {"period_end": ["2026-06-30", "2026-09-30"], "revenue": [100.0, 110], "ebit": [10.0, 11],
         "net_income": [8.0, 9], "eps": [1.0, 1]}
    r = results.company_result(q)
    assert np.isnan(r["rev_yoy"]) and results.grade(r) == "Insufficient data"
