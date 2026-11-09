"""Personal finance calculators: SIP / lumpsum / goals / retirement / EMI & prepayment / tax regimes.

Conventions: SIPs are invested at the START of each month (annuity due) and compound monthly at
(1 + annual)^(1/12) - 1, so a 12% annual return really is 12% a year.
Tax slabs are DATA (TAX_REGIMES below) so they can be updated each budget without touching the logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def monthly_rate(annual: float) -> float:
    return (1 + annual) ** (1 / 12) - 1


def lumpsum_fv(amount: float, annual: float, years: float) -> float:
    return amount * (1 + annual) ** years


def sip_schedule(monthly: float, annual: float, years: int, step_up: float = 0.0) -> pd.DataFrame:
    """Year-end invested vs value. step_up = yearly % increase in the SIP amount."""
    i, bal, inv, rows, amt = monthly_rate(annual), 0.0, 0.0, [], monthly
    for y in range(1, years + 1):
        for _ in range(12):
            bal = (bal + amt) * (1 + i)
            inv += amt
        rows.append({"year": y, "invested": inv, "value": bal, "gain": bal - inv, "sip": amt})
        amt *= 1 + step_up
    return pd.DataFrame(rows)


def sip_fv(monthly: float, annual: float, years: int, step_up: float = 0.0) -> float:
    return float(sip_schedule(monthly, annual, years, step_up)["value"].iloc[-1]) if years > 0 else 0.0


def required_sip(goal: float, annual: float, years: int, existing: float = 0.0) -> float:
    """Monthly SIP needed to reach `goal` (future rupees), net of what `existing` savings grow to."""
    n, i = years * 12, monthly_rate(annual)
    gap = goal - lumpsum_fv(existing, annual, years)
    if gap <= 0:
        return 0.0
    factor = ((1 + i) ** n - 1) / i * (1 + i) if i else n
    return gap / factor


def inflate(amount_today: float, inflation: float, years: float) -> float:
    return amount_today * (1 + inflation) ** years


def retirement_corpus(monthly_expense_today: float, years_to_retire: int, years_in_retirement: int,
                      inflation: float = 0.06, post_return: float = 0.08) -> dict:
    """Corpus needed at retirement to fund inflation-rising expenses (paid at the start of each year)."""
    annual_at_ret = monthly_expense_today * 12 * (1 + inflation) ** years_to_retire
    r, g, n = post_return, inflation, years_in_retirement
    if abs(r - g) < 1e-9:
        corpus = annual_at_ret * n
    else:
        corpus = annual_at_ret * (1 - ((1 + g) / (1 + r)) ** n) / (r - g) * (1 + r)
    return {"annual_expense_at_retirement": annual_at_ret, "corpus": corpus}


def emi(principal: float, annual_rate: float, years: float) -> float:
    """Standard reducing-balance EMI (bank convention: monthly rate = annual / 12)."""
    r, n = annual_rate / 12, int(round(years * 12))
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def amortise(principal: float, annual_rate: float, years: float, extra_monthly: float = 0.0,
             lump: dict | None = None) -> pd.DataFrame:
    """Month-by-month schedule. extra_monthly = regular prepayment; lump = {month: amount}."""
    r, pay = annual_rate / 12, emi(principal, annual_rate, years)
    bal, m, rows, lump = principal, 0, [], lump or {}
    while bal > 0.005 and m < 1200:
        m += 1
        interest = bal * r
        principal_paid = min(pay - interest + extra_monthly + lump.get(m, 0.0), bal)
        bal -= principal_paid
        rows.append({"month": m, "interest": interest, "principal": principal_paid, "balance": max(bal, 0.0)})
    return pd.DataFrame(rows)


def prepayment_benefit(principal, annual_rate, years, extra_monthly=0.0, lump=None) -> dict:
    base = amortise(principal, annual_rate, years)
    new = amortise(principal, annual_rate, years, extra_monthly, lump)
    return {"emi": emi(principal, annual_rate, years), "months_base": len(base), "months_new": len(new),
            "interest_base": base["interest"].sum(), "interest_new": new["interest"].sum(),
            "interest_saved": base["interest"].sum() - new["interest"].sum(), "months_saved": len(base) - len(new)}


def asset_allocation(age: int, risk: str = "moderate") -> dict:
    """Rule-of-thumb starting point: equity = 100 - age, tilted by risk appetite. Not advice."""
    tilt = {"conservative": -15, "moderate": 0, "aggressive": 15}[risk]
    eq = int(np.clip(100 - age + tilt, 20, 85))
    gold = 10
    return {"Equity": eq, "Debt": 100 - eq - gold, "Gold": gold}


def emergency_fund(monthly_expense: float, dependents: int = 0, stable_job: bool = True) -> float:
    months = 6 + (3 if not stable_job else 0) + min(dependents, 3)
    return monthly_expense * months


# ------------------------------------------------------------------ income tax (individual, resident, <60)
# Verify these against the current Finance Act before relying on them; update as budgets change.
TAX_REGIMES = {
    "new": {
        "label": "New regime (sec 115BAC) - FY 2025-26 slabs (Finance Act 2025)",
        "slabs": [(400000, 0.0), (800000, 0.05), (1200000, 0.10), (1600000, 0.15),
                  (2000000, 0.20), (2400000, 0.25), (float("inf"), 0.30)],
        "std_deduction": 75000, "rebate_limit": 1200000, "rebate_max": 60000,
        "surcharge": [(5000000, 0.10), (10000000, 0.15), (20000000, 0.25)],  # capped at 25% in new regime
    },
    "old": {
        "label": "Old regime - FY 2025-26",
        "slabs": [(250000, 0.0), (500000, 0.05), (1000000, 0.20), (float("inf"), 0.30)],
        "std_deduction": 50000, "rebate_limit": 500000, "rebate_max": 12500,
        "surcharge": [(5000000, 0.10), (10000000, 0.15), (20000000, 0.25), (50000000, 0.37)],
    },
}
CESS = 0.04


def slab_tax(income: float, slabs) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in slabs:
        if income > lower:
            tax += (min(income, upper) - lower) * rate
        lower = upper
    return tax


def income_tax(gross_salary: float, regime: str = "new", deductions: float = 0.0, other_income: float = 0.0) -> dict:
    """Tax on salary + other normal income. `deductions` (80C, 80D, HRA, home-loan interest...) count
    only in the old regime. Surcharge marginal relief is not applied (matters only just above 50L/1Cr)."""
    R = TAX_REGIMES[regime]
    taxable = max(gross_salary - R["std_deduction"], 0) + other_income
    if regime == "old":
        taxable = max(taxable - deductions, 0)
    base = slab_tax(taxable, R["slabs"])
    rebate = 0.0
    if taxable <= R["rebate_limit"]:
        rebate = min(base, R["rebate_max"])
    elif regime == "new":
        # marginal relief: tax cannot exceed the income above the rebate limit
        rebate = max(base - (taxable - R["rebate_limit"]), 0.0)
    after = base - rebate
    sr = 0.0
    for limit, rate in R["surcharge"]:
        if taxable > limit:
            sr = rate
    surcharge = after * sr
    cess = (after + surcharge) * CESS
    total = after + surcharge + cess
    return {"regime": regime, "taxable_income": taxable, "slab_tax": base, "rebate_87a": rebate,
            "surcharge": surcharge, "cess": cess, "total_tax": total,
            "effective_rate": total / (gross_salary + other_income) if gross_salary + other_income else 0.0}


def compare_regimes(gross_salary: float, deductions: float = 0.0, other_income: float = 0.0) -> dict:
    n = income_tax(gross_salary, "new", 0, other_income)
    o = income_tax(gross_salary, "old", deductions, other_income)
    better = "new" if n["total_tax"] <= o["total_tax"] else "old"
    return {"new": n, "old": o, "better": better, "saving": abs(n["total_tax"] - o["total_tax"])}
