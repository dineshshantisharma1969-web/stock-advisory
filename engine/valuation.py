"""Automated fair value and BUY / HOLD / SELL model rating, following the india-equity-advisory skill
(references/valuation.md and SKILL.md Part 3). Every assumption is derived by a stated rule from the company's own
statements and its industry peers, and returned with the result so the screen can show it. These are mechanical
defaults, not analyst judgment: a careful report should revisit them stock by stock."""
import json
import math
from datetime import date
from pathlib import Path

import numpy as np

NAN = float("nan")
CR = 1e7
P = json.loads((Path(__file__).resolve().parents[1] / "config" / "valuation_params.json").read_text(encoding="utf-8"))
RF = P["gsec_10y"] - P["sovereign_default_spread"]
ERP, TAX = P["erp_india"], P["tax_rate"]
GT = P["terminal_growth"]
SW = P["scenario_weights"]
SCEN = ("bear", "base", "bull")
AS_OF = None  # set by the back-test to value a stock as of a past date; None = today


def ok(x):
    return x is not None and isinstance(x, (int, float)) and not math.isnan(x)


def v(t, row, i=0):
    if not t:
        return NAN
    r = t["rows"].get(row)
    return NAN if not r or i >= len(r) or r[i] is None else float(r[i])


def series(t, row):
    return [v(t, row, i) for i in range(len(t["years"]))] if t else []


def classify(info):
    sector, industry = info.get("sector") or "", info.get("industry") or ""
    if sector == "Financial Services":
        if industry.startswith("Insurance"):
            return "insurer"
        if industry.startswith("Banks") or industry in ("Credit Services", "Mortgage Finance", "Financial Conglomerates"):
            return "lender"
        return "capital_markets"
    return "cyclical" if sector in P["cyclical_sectors"] else "default"


# ---------------------------------------------------------------- peers
def peer_stats(funds):
    rows = {}
    for sym, f in funds.items():
        i = f.get("info") or {}
        ev = i.get("enterpriseToEbitda")
        pe = i.get("forwardPE")
        rows[sym] = dict(sector=i.get("sector"), industry=i.get("industry"),
                         ev=ev if ok(ev) and 2 <= ev <= 80 else None, pe=pe if ok(pe) and 3 <= pe <= 120 else None)
    return rows


def peer_pct(sym, table, key):
    me = table.get(sym, {})
    for basis in ("industry", "sector"):
        vals = [r[key] for s, r in table.items() if s != sym and r[basis] and r[basis] == me.get(basis) and r[key] is not None]
        if len(vals) >= 4:
            q = np.percentile(vals, [25, 50, 75])
            return dict(bear=float(q[0]), base=float(q[1]), bull=float(q[2]), n=len(vals), basis=f"{basis}: {me.get(basis)}")
    return None


# ---------------------------------------------------------------- DCF
def dcf(rev0, growth, margin, capex, da_pct, nwc, w, gT, mult, stub):
    r, pv, f, eb, df = rev0, 0.0, 0.0, 0.0, 1.0
    for i in range(10):
        rn = r * (1 + growth[i])
        eb = rn * margin[i]
        dep = rn * da_pct
        f = (eb - dep) * (1 - TAX) + dep - rn * capex[i] - (rn - r) * nwc
        df = 1 / (1 + w) ** (i + 1)
        pv += f * df
        r = rn
    tv_g = f * (1 + gT) / (w - gT)
    tv_m = eb * mult
    grow = (1 + w) ** stub
    return dict(ev_gordon=(pv + tv_g * df) * grow, ev_exit=(pv + tv_m * df) * grow, fcff10=f, ebitda10=eb,
                tv_share_gordon=tv_g * df / (pv + tv_g * df) if pv + tv_g * df > 0 else NAN,
                implied_g_exit=(tv_m * w - f) / (tv_m + f) if tv_m + f else NAN, implied_mult_gordon=tv_g / eb if eb else NAN)


def path(start, end, n_to=3):
    return [start + (end - start) * min(k + 1, n_to) / n_to for k in range(10)]


def growth_path(g1, gT):
    mid = (g1 + gT) / 2
    first = [g1 + (mid - g1) * k / 4 for k in range(5)]
    return first + [mid + (gT - mid) * (k + 1) / 5 for k in range(5)]


# ---------------------------------------------------------------- quality
def piotroski(fin, bs, cf):
    if not (fin and bs and cf) or len(bs["years"]) < 2:
        return None, [], 0
    ta = series(bs, "Total Assets")
    avg = lambda i: (ta[i] + ta[i + 1]) / 2 if i + 1 < len(ta) and ok(ta[i + 1]) else ta[i]
    ni, cfo = series(fin, "Net Income"), series(cf, "Operating Cash Flow")
    roa = [ni[i] / avg(i) if ok(ni[i]) and ok(ta[i]) else NAN for i in range(2)]
    ltd = series(bs, "Long Term Debt And Capital Lease Obligation")
    ltd = [x if ok(x) else 0.0 for x in ltd]
    ca, cl = series(bs, "Current Assets"), series(bs, "Current Liabilities")
    sh = series(bs, "Ordinary Shares Number")
    gp, rev = series(fin, "Gross Profit"), series(fin, "Total Revenue")
    tests = [
        ("ROA positive", roa[0] > 0 if ok(roa[0]) else None),
        ("operating cash flow positive", cfo[0] > 0 if ok(cfo[0]) else None),
        ("ROA improved", roa[0] > roa[1] if ok(roa[0]) and ok(roa[1]) else None),
        ("cash flow above profit", cfo[0] > ni[0] if ok(cfo[0]) and ok(ni[0]) else None),
        ("long-term debt ratio fell", ltd[0] / avg(0) <= ltd[1] / avg(1) if ok(ta[0]) and ok(ta[1]) else None),
        ("current ratio improved", ca[0] / cl[0] > ca[1] / cl[1] if all(ok(x) and x for x in (ca[0], cl[0], ca[1], cl[1])) else None),
        ("no new shares", sh[0] <= sh[1] * 1.001 if ok(sh[0]) and ok(sh[1]) else None),
        ("gross margin improved", gp[0] / rev[0] > gp[1] / rev[1] if all(ok(x) and x for x in (gp[0], rev[0], gp[1], rev[1])) else None),
        ("asset turnover improved", rev[0] / avg(0) > rev[1] / avg(1) if ok(rev[0]) and ok(rev[1]) and ok(ta[0]) and ok(ta[1]) else None),
    ]
    avail = [(n, r) for n, r in tests if r is not None]
    return sum(1 for _, r in avail if r), [n for n, r in avail if not r], len(avail)


def altman(fin, bs, mcap):
    ta, tl = v(bs, "Total Assets"), v(bs, "Total Liabilities Net Minority Interest")
    wc = v(bs, "Working Capital")
    if not ok(wc):
        wc = v(bs, "Current Assets") - v(bs, "Current Liabilities")
    reserves = v(bs, "Stockholders Equity") - (v(bs, "Capital Stock") if ok(v(bs, "Capital Stock")) else 0)
    ebit, rev = v(fin, "Operating Income"), v(fin, "Total Revenue")
    if not all(ok(x) for x in (ta, tl, wc, reserves, ebit, rev)) or ta <= 0 or tl <= 0:
        return None
    return 1.2 * wc / ta + 1.4 * reserves / ta + 3.3 * ebit / ta + 0.6 * mcap / tl + rev / ta


# ---------------------------------------------------------------- valuation paths
def _stub(fy_end):
    months = ((AS_OF or date.today()) - date.fromisoformat(fy_end)).days / 30.44
    return max(0.0, min(months, 18)) / 12, months


def value_operating(sym, f, price, beta, peers, kind, warn):
    fin, bs, cf, info = f["fin"], f["bs"], f["cf"], f["info"]
    rev = [x for x in series(fin, "Total Revenue") if ok(x)]
    opinc, da = series(fin, "Operating Income"), series(fin, "Reconciled Depreciation")
    ebitda_row = series(fin, "EBITDA")
    n = min(len(rev), 4)
    margins = []
    for i in range(n):
        e = opinc[i] + da[i] if ok(opinc[i]) and ok(da[i]) else ebitda_row[i] if i < len(ebitda_row) else NAN
        if ok(e) and rev[i] > 0:
            margins.append(e / rev[i])
    if not margins or margins[0] <= 0 or rev[0] <= 0:
        return None, "operating EBITDA is negative or missing: DCF and multiples do not apply"
    m_last, m_avg = margins[0], float(np.mean(margins[:3]))
    k = min(n - 1, 3)
    yrs = (date.fromisoformat(fin["years"][0]) - date.fromisoformat(fin["years"][k])).days / 365.25 if k >= 1 else 0
    g_hist = (rev[0] / rev[k]) ** (1 / yrs) - 1 if k >= 1 and rev[k] > 0 and yrs > 0.5 else 0.08
    if k >= 1 and abs(yrs - k) > 0.2:
        warn.append(f"statement years are irregular ({k} columns span {yrs:.1f} years, likely a change of year-end): growth measured over actual time")
    g_base = min(max(g_hist, 0.03), 0.20)
    da_pct = float(np.nanmean([da[i] / rev[i] for i in range(min(n, 3)) if ok(da[i])] or [0.04]))
    capex_s = series(cf, "Capital Expenditure")
    capex_pct = float(np.nanmean([-capex_s[i] / rev[i] for i in range(min(n, 3)) if i < len(capex_s) and ok(capex_s[i])] or [da_pct * 1.2]))
    capex_pct = min(max(capex_pct, da_pct), 0.25)
    capex_path = [capex_pct + (max(da_pct * 1.15, 0.01) - capex_pct) * (k_ + 1) / 10 for k_ in range(10)]
    chg_wc = series(cf, "Change In Working Capital")
    d_rev = sum(rev[i] - rev[i + 1] for i in range(min(n, 4) - 1))
    d_wc = sum(-chg_wc[i] for i in range(min(n, 4) - 1) if i < len(chg_wc) and ok(chg_wc[i]))
    nwc = min(max(d_wc / d_rev, 0.0), 0.25) if d_rev > 0 else 0.05

    shares = v(bs, "Ordinary Shares Number")
    if not ok(shares) or shares <= 0:
        shares = info.get("sharesOutstanding") or NAN
    mcap = price * shares
    debt = v(bs, "Total Debt")
    debt = debt if ok(debt) else 0.0
    cash = v(bs, "Cash Cash Equivalents And Short Term Investments")
    cash = cash if ok(cash) else 0.0
    mi = v(bs, "Minority Interest")
    mi = mi if ok(mi) else 0.0
    inv = sum(x for x in (v(bs, "Investmentin Financial Assets"), v(bs, "Long Term Equity Investment")) if ok(x))
    eq = v(bs, "Stockholders Equity")
    if ok(eq) and eq > 0 and mi / (eq + mi) > 0.25:
        return None, (f"minority interests are {mi / (eq + mi):.0%} of total equity (holding-company structure): "
                      "the book-value deduction misstates value; needs a sum-of-parts valuation")
    intexp = v(fin, "Interest Expense")
    debt1 = v(bs, "Total Debt", 1)
    avg_debt = (debt + debt1) / 2 if ok(debt1) else debt
    kd = min(max(intexp / avg_debt, 0.06), 0.14) if ok(intexp) and avg_debt > 0 else 0.09
    ke = RF + beta * ERP
    wd = debt / (debt + mcap) if mcap > 0 else 0
    wacc = (1 - wd) * ke + wd * kd * (1 - TAX)
    wacc = max(wacc, GT["bull"] + 0.03)
    stub, months = _stub(fin["years"][0])
    if months > 18:
        return None, f"latest complete annual statements on Yahoo are {months:.0f} months old: rating withheld until they update"

    ev_peer = peer_pct(sym, peers, "ev")
    cap_lo, cap_hi = P["exit_multiple_cap"]
    exit_m = {s: min(max(ev_peer[s], cap_lo), cap_hi) for s in SCEN} if ev_peer else dict(bear=10.0, base=12.0, bull=14.0)
    if not ev_peer:
        warn.append("fewer than 4 industry or sector peers with EV/EBITDA: exit multiples 10/12/14x used")

    scen = {
        "bear": dict(g1=max(g_base - 0.04, 0.02), m=min(m_last, m_avg) * 0.9, w=wacc + 0.005, gT=GT["bear"]),
        "base": dict(g1=g_base, m=m_avg, w=wacc, gT=GT["base"]),
        "bull": dict(g1=min(g_base + 0.03, 0.25), m=max(m_last, m_avg) * 1.05, w=wacc - 0.005, gT=GT["bull"]),
    }
    out, dcf_detail = {s: {} for s in SCEN}, {}
    for s in SCEN:
        c = scen[s]
        d = dcf(rev[0], growth_path(c["g1"], c["gT"]), path(m_last, c["m"]), capex_path, da_pct, nwc, c["w"], c["gT"], exit_m[s], stub)
        g_ps = (d["ev_gordon"] - debt + cash - mi + inv) / shares
        x_ps = (d["ev_exit"] - debt + cash - mi + inv) / shares
        dcf_detail[s] = d
        out[s]["dcf_gordon"], out[s]["dcf_exit"] = g_ps, x_ps
        if d["fcff10"] > 0 and g_ps > 0 and x_ps > 0:
            out[s]["dcf"] = (g_ps + x_ps) / 2
    if "dcf" not in out["base"]:
        warn.append("projected free cash flow is negative at year 10 (capex above cash generation): DCF dropped")
        for s in SCEN:
            out[s].pop("dcf", None)
    elif dcf_detail["base"]["implied_g_exit"] > RF:
        warn.append(f"base exit multiple {exit_m['base']:.1f}x implies {dcf_detail['base']['implied_g_exit']:.1%} growth forever, above the risk-free rate: the exit-multiple DCF leans optimistic")

    ebitda_ttm = info.get("ebitda") or (m_last * rev[0])
    debt_q = info.get("totalDebt") if ok(info.get("totalDebt")) else debt
    cash_q = info.get("totalCash") if ok(info.get("totalCash")) else cash
    if ev_peer and ebitda_ttm and ebitda_ttm > 0:
        for s in SCEN:
            out[s]["ev_ebitda"] = (ev_peer[s] * ebitda_ttm - debt_q + cash_q - mi) / shares
    pe_peer = peer_pct(sym, peers, "pe")
    teps, feps = info.get("trailingEps"), info.get("forwardEps")
    if pe_peer and ok(teps) and teps > 0:
        for s in SCEN:
            eps = feps if s != "bear" and ok(feps) and feps > 0 else teps
            out[s]["pe"] = pe_peer[s] * eps
    for s in SCEN:
        out[s] = {m: x for m, x in out[s].items() if ok(x)}

    def val_at(gr):
        return (dcf(rev[0], [gr] * 10, path(m_last, m_avg), capex_path, da_pct, nwc, wacc, GT["base"], 12, stub)["ev_gordon"]
                - debt + cash - mi + inv) / shares
    rev_dcf = None
    if val_at(0.6) < price:
        rev_dcf = dict(implied_growth=None, text="price needs more than 60% revenue growth a year at these margins")
    elif val_at(-0.05) > price:
        rev_dcf = dict(implied_growth=None, text="price is below the value at -5% revenue growth a year")
    else:
        lo, hi = -0.05, 0.6
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if val_at(mid) < price else (lo, mid)
        rev_dcf = dict(implied_growth=round(mid * 100, 1),
                       text=f"price needs {mid:.1%} revenue growth a year for 10 years at a {m_avg:.1%} EBITDA margin; "
                            f"history: {g_hist:.1%} a year over {yrs:.1f} years")

    pis, fails, avail = piotroski(fin, bs, cf)
    z = altman(fin, bs, mcap)
    nd_e = (debt - cash) / ebitda_ttm if ebitda_ttm and ebitda_ttm > 0 else NAN
    assumptions = dict(
        revenue_cagr_history=round(g_hist * 100, 1), years_of_history=round(yrs, 1),
        growth_year1={s: round(scen[s]["g1"] * 100, 1) for s in SCEN},
        ebitda_margin_last=round(m_last * 100, 1), ebitda_margin_avg=round(m_avg * 100, 1),
        ebitda_margin_target={s: round(scen[s]["m"] * 100, 1) for s in SCEN},
        capex_pct_start=round(capex_pct * 100, 1), da_pct=round(da_pct * 100, 1), nwc_pct_of_growth=round(nwc * 100, 1),
        tax=round(TAX * 100, 2), risk_free=round(RF * 100, 2), erp=round(ERP * 100, 2), beta=round(beta, 2),
        ke=round(ke * 100, 2), kd=round(kd * 100, 2), debt_weight=round(wd * 100, 1),
        wacc={s: round(scen[s]["w"] * 100, 2) for s in SCEN}, terminal_growth={s: round(scen[s]["gT"] * 100, 2) for s in SCEN},
        exit_multiple={s: round(exit_m[s], 1) for s in SCEN},
        peer_ev_ebitda=ev_peer and {**{s: round(ev_peer[s], 1) for s in SCEN}, "n": ev_peer["n"], "basis": ev_peer["basis"]},
        peer_forward_pe=pe_peer and {**{s: round(pe_peer[s], 1) for s in SCEN}, "n": pe_peer["n"], "basis": pe_peer["basis"]},
        stub_months=round(stub * 12, 1), statements_year_end=fin["years"][0],
        bridge_cr=dict(debt=round(debt / CR), cash=round(cash / CR), minority=round(mi / CR), investments=round(inv / CR)),
        tv_share_gordon_base=round(dcf_detail["base"]["tv_share_gordon"] * 100, 0) if ok(dcf_detail["base"]["tv_share_gordon"]) else None,
        implied_g_of_exit_base=round(dcf_detail["base"]["implied_g_exit"] * 100, 1) if ok(dcf_detail["base"]["implied_g_exit"]) else None,
    )
    quality = dict(piotroski=pis, piotroski_tests=avail, piotroski_failed=fails, altman_z=round(z, 2) if z is not None else None,
                   net_debt_to_ebitda=round(nd_e, 2) if ok(nd_e) else None, market_cap_cr=round(mcap / CR),
                   roe=round(v(fin, "Net Income") / v(bs, "Stockholders Equity") * 100, 1) if ok(v(bs, "Stockholders Equity")) and v(bs, "Stockholders Equity") > 0 else None)
    model = dict(type="dcf", rev0=rev[0], m_last=m_last, m_base=m_avg, g_base=g_base, capex_path=capex_path, da_pct=da_pct,
                 nwc=nwc, stub=stub, tax=TAX, debt=debt, cash=cash, mi=mi, inv=inv, shares=shares, wacc_base=wacc,
                 gT_base=GT["base"], rf=RF, exit_multiple=exit_m,
                 fixed={sc: {m: out[sc][m] for m in ("ev_ebitda", "pe") if m in out[sc]} for sc in SCEN})
    return dict(scenarios=out, assumptions=assumptions, quality=quality, reverse_dcf=rev_dcf, ke=ke, model=model), None


def value_lender(sym, f, price, beta, peers, warn):
    info = f["info"]
    eps, feps, bvps = info.get("trailingEps"), info.get("forwardEps"), info.get("bookValue")
    if not (ok(eps) and ok(bvps) and bvps > 0):
        return None, "EPS or book value missing"
    roe = eps / bvps
    ke = RF + beta * ERP
    g = P["bank_growth"]
    dps = info.get("dividendRate") or 0
    retention = min(max(1 - dps / eps, 0.5), 0.95) if eps > 0 else 0.75
    out = {s: {} for s in SCEN}
    roe_s = dict(bear=roe - 0.02, base=roe, bull=roe + 0.02)

    def residual_income(r):
        # two-stage: 10 years at ROE r with book growing at r x retention, then ROE fades halfway to Ke and grows at g
        bv, pv = bvps, 0.0
        for t in range(1, 11):
            pv += (r - ke) * bv / (1 + ke) ** t
            bv *= 1 + r * retention
        r_term = ke + (r - ke) / 2
        tv = (r_term - ke) * bv * (1 + g) / (ke - g)
        return max(bvps + pv + tv / (1 + ke) ** 10, 0.0)

    for s in SCEN:
        out[s]["pb_roe"] = residual_income(roe_s[s])
    pe_peer = peer_pct(sym, peers, "pe")
    if pe_peer and eps > 0:
        for s in SCEN:
            out[s]["pe"] = pe_peer[s] * (feps if s != "bear" and ok(feps) and feps > 0 else eps)
    warn.append("lender: book value and EPS come from Yahoo summary fields, which can mix accounting bases; "
                "asset quality (NPA), capital adequacy and margins are not checked")
    assumptions = dict(method="two-stage residual income: 10 years at the scenario ROE with book growing at ROE x retention, then ROE fades halfway to Ke and grows at the terminal rate",
                       roe=round(roe * 100, 1), roe_scenarios={s: round(roe_s[s] * 100, 1) for s in SCEN}, retention=round(retention * 100),
                       book_growth_stage1=round(roe * retention * 100, 1), growth=round(g * 100, 1),
                       ke=round(ke * 100, 2), beta=round(beta, 2), risk_free=round(RF * 100, 2), erp=round(ERP * 100, 2),
                       bvps=round(bvps, 2), market_implied_roe=round((g + price / bvps * (ke - g)) * 100, 1),
                       peer_forward_pe=pe_peer and {**{s: round(pe_peer[s], 1) for s in SCEN}, "n": pe_peer["n"], "basis": pe_peer["basis"]})
    quality = dict(roe=round(roe * 100, 1), piotroski=None, altman_z=None, net_debt_to_ebitda=None,
                   market_cap_cr=round(price * (info.get("sharesOutstanding") or 0) / CR))
    model = dict(type="ri", bvps=bvps, roe_base=roe, retention=retention, ke_base=ke, g=g, rf=RF,
                 fixed={sc: {m: out[sc][m] for m in ("pe",) if m in out[sc]} for sc in SCEN})
    return dict(scenarios=out, assumptions=assumptions, quality=quality, reverse_dcf=None, ke=ke, model=model), None


def value_pe_only(sym, f, price, beta, peers, warn):
    info = f["info"]
    eps, feps = info.get("trailingEps"), info.get("forwardEps")
    pe_peer = peer_pct(sym, peers, "pe")
    if not (pe_peer and ok(eps) and eps > 0):
        return None, "no positive EPS or too few peers for P/E"
    out = {s: {"pe": pe_peer[s] * (feps if s != "bear" and ok(feps) and feps > 0 else eps)} for s in SCEN}
    warn.append("capital-markets business (exchange, broker, asset manager or holding company): valued on peer P/E only")
    ke = RF + beta * ERP
    assumptions = dict(peer_forward_pe={**{s: round(pe_peer[s], 1) for s in SCEN}, "n": pe_peer["n"], "basis": pe_peer["basis"]},
                       eps_trailing=eps, eps_forward=feps, ke=round(ke * 100, 2), beta=round(beta, 2))
    quality = dict(piotroski=None, altman_z=None, net_debt_to_ebitda=None,
                   market_cap_cr=round(price * (info.get("sharesOutstanding") or 0) / CR))
    if f.get("fin") and f.get("bs") and f.get("cf"):
        quality["piotroski"], quality["piotroski_failed"], quality["piotroski_tests"] = piotroski(f["fin"], f["bs"], f["cf"])
    return dict(scenarios=out, assumptions=assumptions, quality=quality, reverse_dcf=None, ke=ke), None


# ---------------------------------------------------------------- rating
def rate(sym, f, price, beta, peers):
    info = f.get("info") or {}
    kind = classify(info)
    warn = []
    base = dict(symbol=sym, kind=kind, sector=info.get("sector"), industry=info.get("industry"), name=info.get("longName"),
                price=round(price, 2), beta=round(beta, 2))
    if kind == "insurer":
        return dict(base, rating="NOT RATED", rating_note="insurer: needs embedded value (P/EV), which Yahoo does not supply")
    if kind in ("default", "cyclical") and not (f.get("fin") and f.get("bs") and f.get("cf")):
        return dict(base, rating="NOT RATED", rating_note="annual statements missing on Yahoo")
    try:
        if kind == "lender":
            res, why = value_lender(sym, f, price, beta, peers, warn)
        elif kind == "capital_markets":
            res, why = value_pe_only(sym, f, price, beta, peers, warn)
        else:
            res, why = value_operating(sym, f, price, beta, peers, kind, warn)
    except Exception as e:  # one malformed company must not stop the run
        return dict(base, rating="NOT RATED", rating_note=f"valuation error: {type(e).__name__}")
    if res is None:
        return dict(base, rating="NOT RATED", rating_note=why)

    weights = P["weights"]["lender" if kind == "lender" else "capital_markets" if kind == "capital_markets" else kind]
    blend = {}
    for s in SCEN:
        avail = {m: w for m, w in weights.items() if m in res["scenarios"][s]}
        tot = sum(avail.values())
        if not tot:
            return dict(base, rating="NOT RATED", rating_note="no valuation method applied", warnings=warn)
        blend[s] = sum(res["scenarios"][s][m] * w / tot for m, w in avail.items())
        res["scenarios"][s]["blend"] = blend[s]
    used = {m: w for m, w in weights.items() if m in res["scenarios"]["base"]}
    tot = sum(used.values())
    used = {m: round(w / tot * 100) for m, w in used.items()}
    fv = sum(SW[s] * blend[s] for s in SCEN)
    if fv <= 0:
        return dict(base, rating="NOT RATED", rating_note="fair value is not positive", warnings=warn)
    base_vals = [x for m, x in res["scenarios"]["base"].items() if m in weights]
    disagree = len(base_vals) > 1 and min(base_vals) > 0 and max(base_vals) / min(base_vals) > 2
    if disagree:
        warn.append(f"methods disagree by more than 2x in the base case ({min(base_vals):,.0f} to {max(base_vals):,.0f}): treat the blend with care")

    q = res["quality"]
    gate_fail = []
    if q.get("piotroski") is not None and q["piotroski"] < P["quality_gate"]["piotroski_min"]:
        gate_fail.append(f"Piotroski {q['piotroski']} below {P['quality_gate']['piotroski_min']}")
    if q.get("altman_z") is not None and q["altman_z"] < P["quality_gate"]["altman_min"]:
        gate_fail.append(f"Altman Z {q['altman_z']} below {P['quality_gate']['altman_min']}")
    if q.get("net_debt_to_ebitda") is not None and q["net_debt_to_ebitda"] > P["quality_gate"]["net_debt_to_ebitda_max"]:
        gate_fail.append(f"net debt {q['net_debt_to_ebitda']}x EBITDA")
    severe = []
    if q.get("piotroski") is not None and q["piotroski"] <= P["severe"]["piotroski_max"]:
        severe.append(f"Piotroski {q['piotroski']}")
    if q.get("altman_z") is not None and q["altman_z"] < P["severe"]["altman_below"]:
        severe.append(f"Altman Z {q['altman_z']} (distress zone)")

    rm = P["required_margin"]
    big = (q.get("market_cap_cr") or 0) >= P["large_cap_rs_crore"]
    if kind == "cyclical" or (q.get("piotroski") is not None and q["piotroski"] < 5):
        req = rm["cyclical_or_weak"]
    elif big and (q.get("piotroski") or 0) >= 7:
        req = rm["high_quality_large_cap"]
    else:
        req = rm["default"]
    buy_below, sell_above = fv * (1 - req), blend["bull"]

    if severe:
        rating, note = "SELL", "severe quality failure: " + ", ".join(severe)
    elif price > sell_above:
        rating, note = "SELL", f"price is above the optimistic-case value of Rs {sell_above:,.0f}"
    elif price <= buy_below and len(base_vals) < 2:
        rating, note = "HOLD", "cheap on the only method available; a BUY needs at least two valuation methods to agree"
    elif price <= buy_below and disagree:
        rating, note = "HOLD", "cheap on the blend, but the methods disagree by more than 2x; a BUY needs them closer"
    elif price <= buy_below and not gate_fail:
        rating, note = "BUY", f"price is at or below the buy-below level of Rs {buy_below:,.0f} ({req:.0%} margin of safety) and the quality gate passes"
    elif price <= buy_below:
        rating, note = "HOLD", "value-trap check: cheap, but the quality gate fails (" + "; ".join(gate_fail) + ")"
    elif price <= fv:
        rating, note = "HOLD", "add on dips: price is below fair value but above the buy-below level"
    else:
        rating, note = "HOLD", "do not add: price is above fair value but below the optimistic case"
    ke = res["ke"]
    dps = info.get("dividendRate") or 0
    t12 = {s: blend[s] * (1 + ke) - dps for s in SCEN}
    t36 = {s: blend[s] * (1 + ke) ** 3 - dps * ((1 + ke) ** 3 - 1) / ke for s in SCEN}
    rnd = lambda d: {k: round(x) for k, x in d.items()}
    return dict(
        base, rating=rating, rating_note=note, fair_value=round(fv), buy_below=round(buy_below), sell_above=round(sell_above),
        required_margin=round(req * 100), margin_of_safety=round((1 - price / fv) * 100, 1),
        downside_to_bear=round((blend["bear"] / price - 1) * 100, 1), upside_to_bull=round((blend["bull"] / price - 1) * 100, 1),
        scenarios={s: rnd(res["scenarios"][s]) for s in SCEN}, weights=used, scenario_weights=SW,
        targets=dict(m12={**rnd(t12), "weighted": round(sum(SW[s] * t12[s] for s in SCEN))},
                     m36={**rnd(t36), "weighted": round(sum(SW[s] * t36[s] for s in SCEN))}),
        assumptions=res["assumptions"], quality={**q, "gate_failures": gate_fail, "pledge": "not checked (not on Yahoo)"},
        model=res.get("model") and {**res["model"], "weights": {m: w for m, w in weights.items()}, "required_margin": req,
                                    "dps": dps, "severe": severe, "gate_failures": gate_fail},
        reverse_dcf=res["reverse_dcf"], warnings=warn,
        valid_until="next quarterly result or a 10% price move, whichever comes first",
    )


KEY_ROW = {"fin": "Total Revenue", "bs": "Total Assets", "cf": "Operating Cash Flow"}
INFO_MONEY = ("ebitda", "totalDebt", "totalCash", "totalRevenue")


def clean(f, usdinr):
    """Drop year columns whose key row is empty (partial latest years, e.g. Nestle India) and convert statements
    reported in US dollars (companies whose Yahoo data comes from a US 20-F filing, e.g. Infosys) to rupees."""
    g = {"info": dict(f.get("info") or {}), "notes": []}
    cur = g["info"].get("financialCurrency")
    fx = usdinr if cur == "USD" and usdinr else 1.0
    if cur == "USD":
        if not usdinr:
            g["notes"].append("statements in USD and no USD/INR rate available")
        else:
            g["notes"].append(f"statements reported in USD (US filing); converted at USD/INR {usdinr:.2f}")
            for k in INFO_MONEY:
                if ok(g["info"].get(k)):
                    g["info"][k] = g["info"][k] * fx
    elif cur and cur != "INR":
        g["notes"].append(f"statements reported in {cur}: not converted, values unreliable")
    for t, key in KEY_ROW.items():
        tab = f.get(t)
        if not tab or key not in tab["rows"]:
            g[t] = None
            continue
        keep = [i for i, x in enumerate(tab["rows"][key]) if x is not None]
        if not keep:
            g[t] = None
            continue
        if keep[0] > 0:
            g["notes"].append(f"{t}: latest column {tab['years'][0]} incomplete on Yahoo, skipped")
        g[t] = {"years": [tab["years"][i] for i in keep],
                "rows": {r: [None if vals[i] is None else (vals[i] * fx if r != "Ordinary Shares Number" and r != "Share Issued" else vals[i])
                             for i in keep if i < len(vals)] for r, vals in tab["rows"].items()}}
    years = [g[t]["years"][0] for t in ("fin", "bs", "cf") if g.get(t)]
    if len(set(years)) > 1:
        g["notes"].append(f"latest year differs across statements ({', '.join(sorted(set(years)))})")
    return g


def value_all(funds, prices, betas, usdinr=None):
    """funds: {sym: fundamentals}; prices: {sym: latest close}; betas: {sym: (beta_adj, r2)}; usdinr: latest rate."""
    funds = {s: clean(f, usdinr) for s, f in funds.items()}
    peers = peer_stats(funds)
    out = {}
    for sym, f in funds.items():
        if sym not in prices:
            continue
        b, r2 = betas.get(sym, (1.0, None))
        r = rate(sym, f, prices[sym], b, peers)
        if f["notes"]:
            r.setdefault("warnings", []).extend(f["notes"])
        if r2 is not None and r2 < 0.1:
            r.setdefault("warnings", []).append(f"beta has a weak fit to the NIFTY 500 (R-squared {r2:.2f})")
        out[sym] = r
    return out
