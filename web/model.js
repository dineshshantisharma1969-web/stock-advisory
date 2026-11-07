/* Live re-valuation on the phone. A line-for-line port of engine/valuation.py (dcf, growth_path, path,
   residual income, blend and the rating rule) so the sliders reproduce the server's numbers at their defaults. */
const VM = (() => {
  const SCEN = ["bear", "base", "bull"], SW = { bear: 0.25, base: 0.5, bull: 0.25 };

  function growthPath(g1, gT) {
    const mid = (g1 + gT) / 2, out = [];
    for (let k = 0; k < 5; k++) out.push(g1 + (mid - g1) * k / 4);
    for (let k = 0; k < 5; k++) out.push(mid + (gT - mid) * (k + 1) / 5);
    return out;
  }
  const path = (start, end, nTo = 3) => Array.from({ length: 10 }, (_, k) => start + (end - start) * Math.min(k + 1, nTo) / nTo);

  function dcf(m, growth, margin, w, gT, mult) {
    let r = m.rev0, pv = 0, f = 0, eb = 0, df = 1;
    for (let i = 0; i < 10; i++) {
      const rn = r * (1 + growth[i]);
      eb = rn * margin[i];
      const dep = rn * m.da_pct;
      f = (eb - dep) * (1 - m.tax) + dep - rn * m.capex_path[i] - (rn - r) * m.nwc;
      df = 1 / Math.pow(1 + w, i + 1);
      pv += f * df;
      r = rn;
    }
    const grow = Math.pow(1 + w, m.stub);
    const toShare = (ev) => (ev - m.debt + m.cash - m.mi + m.inv) / m.shares;
    return { gordon: toShare((pv + f * (1 + gT) / (w - gT) * df) * grow), exit: toShare((pv + eb * mult * df) * grow), fcff10: f };
  }

  // inputs: {g, margin, wacc, gT} for type "dcf"; {roe, ke} for type "ri" (base-case values; other cases follow the engine's offsets)
  function scenarios(m, x) {
    const out = { bear: {}, base: {}, bull: {} };
    if (m.type === "dcf") {
      const c = {
        bear: { g1: Math.max(x.g - 0.04, 0.02), m: Math.min(m.m_last, x.margin) * 0.9, w: x.wacc + 0.005, gT: x.gT - 0.01 },
        base: { g1: x.g, m: x.margin, w: x.wacc, gT: x.gT },
        bull: { g1: Math.min(x.g + 0.03, 0.25), m: Math.max(m.m_last, x.margin) * 1.05, w: x.wacc - 0.005, gT: Math.min(x.gT + 0.0025, m.rf) },
      };
      for (const s of SCEN) {
        if (c[s].w - c[s].gT < 0.005) { out[s].invalid = true; continue; }
        const d = dcf(m, growthPath(c[s].g1, c[s].gT), path(m.m_last, c[s].m), c[s].w, c[s].gT, m.exit_multiple[s]);
        out[s].dcf_gordon = d.gordon; out[s].dcf_exit = d.exit;
        if (d.fcff10 > 0 && d.gordon > 0 && d.exit > 0) out[s].dcf = (d.gordon + d.exit) / 2;
      }
      if (out.base.dcf == null) for (const s of SCEN) delete out[s].dcf;
    } else {
      const roeS = { bear: x.roe - 0.02, base: x.roe, bull: x.roe + 0.02 };
      for (const s of SCEN) {
        let bv = m.bvps, pv = 0;
        for (let t = 1; t <= 10; t++) { pv += (roeS[s] - x.ke) * bv / Math.pow(1 + x.ke, t); bv *= 1 + roeS[s] * m.retention; }
        const rTerm = x.ke + (roeS[s] - x.ke) / 2;
        const tv = x.ke - m.g > 0.001 ? (rTerm - x.ke) * bv * (1 + m.g) / (x.ke - m.g) : NaN;
        const val = m.bvps + pv + tv / Math.pow(1 + x.ke, 10);
        if (isFinite(val)) out[s].pb_roe = Math.max(val, 0);
      }
    }
    for (const s of SCEN) Object.assign(out[s], m.fixed[s] || {});
    return out;
  }

  function rate(m, price, x) {
    const sc = scenarios(m, x), W = m.weights, blend = {};
    for (const s of SCEN) {
      const avail = Object.keys(W).filter((k) => sc[s][k] != null && isFinite(sc[s][k]));
      const tot = avail.reduce((a, k) => a + W[k], 0);
      if (!tot) return { error: "no valuation method applies with these inputs" };
      blend[s] = avail.reduce((a, k) => a + sc[s][k] * W[k] / tot, 0);
      sc[s].blend = blend[s];
    }
    const fv = SCEN.reduce((a, s) => a + SW[s] * blend[s], 0);
    if (!(fv > 0)) return { error: "fair value is not positive with these inputs", scenarios: sc };
    const baseVals = Object.keys(W).map((k) => sc.base[k]).filter((v) => v != null && isFinite(v));
    const disagree = baseVals.length > 1 && Math.min(...baseVals) > 0 && Math.max(...baseVals) / Math.min(...baseVals) > 2;
    const buyBelow = fv * (1 - m.required_margin), sellAbove = blend.bull;
    let rating, note;
    if (m.severe && m.severe.length) [rating, note] = ["SELL", "severe quality failure: " + m.severe.join(", ")];
    else if (price > sellAbove) [rating, note] = ["SELL", "price is above the optimistic-case value"];
    else if (price <= buyBelow && baseVals.length < 2) [rating, note] = ["HOLD", "cheap on the only method available"];
    else if (price <= buyBelow && disagree) [rating, note] = ["HOLD", "cheap, but the methods disagree by more than 2x"];
    else if (price <= buyBelow && !(m.gate_failures && m.gate_failures.length)) [rating, note] = ["BUY", "at or below the buy-below level and the quality gate passes"];
    else if (price <= buyBelow) [rating, note] = ["HOLD", "value-trap check: cheap, but the quality gate fails"];
    else if (price <= fv) [rating, note] = ["HOLD", "add on dips"];
    else [rating, note] = ["HOLD", "do not add"];
    return { rating, note, fv, buyBelow, sellAbove, mos: (1 - price / fv) * 100, scenarios: sc, disagree };
  }

  function defaults(m) {
    return m.type === "dcf" ? { g: m.g_base, margin: m.m_base, wacc: m.wacc_base, gT: m.gT_base } : { roe: m.roe_base, ke: m.ke_base };
  }
  return { rate, defaults, scenarios };
})();
