/* Phone web app: renders the snapshot served by server/app.py. No build step. */
const $ = (s) => document.querySelector(s);
const view = $("#view");
let MARKET = null, STOCKS = null, tab = "market", sortKey = "rs_rank", query = "", ratingFilter = "ALL";
const RCLS = { BUY: "up", HOLD: "warn", SELL: "down" };
const ratingBadge = (r) => r ? `<span class="badge rating ${RCLS[r] || ""}">${r === "NOT RATED" ? "not rated" : r}</span>` : "";

const fmt = (n, d = 2) => n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (n, d = 1) => n == null ? "–" : (n > 0 ? "+" : "") + Number(n).toFixed(d) + "%";
const cls = (n) => n == null ? "" : n > 0 ? "up" : n < 0 ? "down" : "";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { $("#login").classList.remove("hidden"); throw new Error("login"); }
  if (!r.ok) { const t = await r.text(); throw new Error(t || r.statusText); }
  return r.json();
}

/* ---------- small SVG line chart ---------- */
function lineChart(series, opts = {}) {
  const w = 600, h = opts.h || 160, pad = 6;
  const all = series.flatMap((s) => s.v.filter((x) => x != null));
  if (!all.length) return "";
  let lo = Math.min(...all), hi = Math.max(...all);
  if (opts.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi === lo) hi = lo + 1;
  const n = Math.max(...series.map((s) => s.v.length));
  const x = (i) => pad + (i / (n - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - lo) / (hi - lo)) * (h - 2 * pad);
  const paths = series.map((s) => {
    let d = "", pen = false;
    s.v.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); pen = true; });
    return `<path d="${d}" fill="none" stroke="${s.c}" stroke-width="${s.w || 2}" stroke-dasharray="${s.dash || ""}"/>`;
  }).join("");
  const zero = opts.zero ? `<line x1="${pad}" x2="${w - pad}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>` : "";
  const bands = (opts.bands || []).map((b) => `<line x1="${pad}" x2="${w - pad}" y1="${y(b)}" y2="${y(b)}" stroke="var(--line)" stroke-dasharray="4 4"/>`).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}">${zero}${bands}${paths}
    <text x="${pad}" y="12" fill="var(--muted)" font-size="11">${fmt(hi, opts.d ?? 0)}</text>
    <text x="${pad}" y="${h - 2}" fill="var(--muted)" font-size="11">${fmt(lo, opts.d ?? 0)}</text></svg>`;
}

/* ---------- Market tab ---------- */
function renderMarket() {
  const m = MARKET.market, b = m.breadth, t = b.today, mc = m.macro;
  const idx = Object.entries(m.indices).map(([name, v]) => `
    <div class="kpi"><div class="l">${name}</div><div class="v">${fmt(v.close, 0)}</div>
    <div class="s ${cls(v.chg)}">${pct(v.chg, 2)} today · RSI ${v.rsi}</div>
    <div class="s">${pct(v.vs_200dma)} vs 200-DMA · 1m ${pct(v.ret_1m)} · 3m ${pct(v.ret_3m)}</div></div>`).join("");
  const stateCls = b.state === "broad advance" ? "up" : b.state === "broad decline" ? "down" : "warn";
  const backCls = mc.backdrop === "calm" ? "up" : mc.backdrop === "stressed" ? "down" : "warn";
  view.innerHTML = `
    <div class="card"><h3>Regime</h3>
      <div class="row"><span>NIFTY 500 vs 200-day average</span><span class="badge ${m.regime_up ? "up" : "down"}">${m.regime_up ? "above: risk-on filter passes" : "below: risk-on filter fails"}</span></div>
      <div class="row"><span>Breadth</span><span class="badge ${stateCls}">${b.state}${b.narrowing ? " · narrowing" : ""}</span></div>
      <div class="row"><span>Macro backdrop</span><span class="badge ${backCls}">${mc.backdrop} (${mc.flags.length} flag${mc.flags.length === 1 ? "" : "s"})</span></div>
    </div>
    ${MARKET.valuation_info && MARKET.valuation_info.counts ? `<div class="card"><h3>Model ratings (${Object.values(MARKET.valuation_info.counts).reduce((a, b) => a + b, 0)} stocks)</h3>
      <div class="grid">${["BUY", "HOLD", "SELL", "NOT RATED"].map((k) => `<div class="kpi rating-kpi" data-rating="${k}"><div class="l">${k === "NOT RATED" ? "Not rated" : k}</div><div class="v ${RCLS[k] || ""}">${MARKET.valuation_info.counts[k] ?? 0}</div></div>`).join("")}</div>
      <div class="small" style="margin-top:8px">${esc(MARKET.valuation_info.note || "")}. Tap a number to list those stocks.</div></div>` : ""}
    ${trackCard()}
    <div id="btcard"></div>
    <div class="card"><h3>Indices</h3><div class="grid">${idx}</div></div>
    <div class="card"><h3>NIFTY 50, 120 sessions</h3>${lineChart([
      { v: m.indices["NIFTY 50"].series.close, c: "var(--accent)" },
      { v: m.indices["NIFTY 50"].series.sma50, c: "var(--warn)", w: 1 },
      { v: m.indices["NIFTY 50"].series.sma200, c: "var(--muted)", w: 1, dash: "4 4" }])}
      <div class="small">blue close · amber 50-DMA · grey 200-DMA</div></div>
    <div class="card"><h3>Breadth today (${b.coverage} stocks)</h3>
      <div class="grid">
        <div class="kpi"><div class="l">Advances / declines</div><div class="v">${t.advances} / ${t.declines}</div><div class="s">A/D ratio ${t.ad_ratio} · unchanged ${t.unchanged}</div></div>
        <div class="kpi"><div class="l">Above 200-DMA</div><div class="v">${t.pct_above_200dma}%</div><div class="s">above 50-DMA ${t.pct_above_50dma}% · 20-DMA ${t.pct_above_20dma}%</div></div>
        <div class="kpi"><div class="l">New 52-week highs / lows</div><div class="v">${t.new_52w_highs} / ${t.new_52w_lows}</div><div class="s">closing basis</div></div>
        <div class="kpi"><div class="l">McClellan oscillator</div><div class="v ${cls(t.mcclellan)}">${fmt(t.mcclellan, 1)}</div><div class="s">up/down volume ${t.up_down_volume_ratio}</div></div>
      </div>
      <div class="small" style="margin-top:8px">A/D line change over 20 sessions: ${b.ad_line_change_20d > 0 ? "+" : ""}${b.ad_line_change_20d}. ${esc(b.rules)}</div>
    </div>
    <div class="card"><h3>Advance-decline line vs NIFTY 500</h3>${lineChart([{ v: b.series.ad_line, c: "var(--accent)" }])}
      ${lineChart([{ v: b.series.index, c: "var(--muted)" }], { h: 90 })}<div class="small">top: cumulative advances minus declines · bottom: NIFTY 500</div></div>
    <div class="card"><h3>% of stocks above 50 and 200-DMA</h3>${lineChart([{ v: b.series.pct_above_50, c: "var(--warn)" }, { v: b.series.pct_above_200, c: "var(--accent)" }], { bands: [40, 60] })}
      <div class="small">amber 50-DMA · blue 200-DMA · dashed 40 / 60</div></div>
    <div class="card"><h3>McClellan oscillator</h3>${lineChart([{ v: b.series.mcclellan, c: "var(--accent)" }], { zero: true, h: 110, d: 0 })}</div>
    ${limits()}`;
  view.querySelectorAll(".rating-kpi").forEach((el) => el.addEventListener("click", () => { ratingFilter = el.dataset.rating; sortKey = "mos"; tab = "stocks"; render(); }));
  renderBacktest();
}

function trackCard() {
  const t = MARKET.track_record;
  if (!t || !t.rows.length) return "";
  const live = t.rows.filter((r) => r.days > 0);
  const f = (v) => v == null ? "–" : `<span class="${cls(v)}">${pct(v * 100, 1)}</span>`;
  const latest = live.length ? live[0] : null;
  return `<div class="card"><h3>Live track record (ratings as issued)</h3>
    <div class="small" style="margin-bottom:8px">${t.rows.length} rating day${t.rows.length === 1 ? "" : "s"} logged, first ${t.rows[0].as_of}; prices to ${t.latest}. ${esc(t.note)}</div>
    ${live.length ? `<table class="vt"><tr><th>Rated on</th><th>Days</th><th>BUY</th><th>HOLD</th><th>SELL</th><th>All</th><th>NIFTY 500</th></tr>
      ${live.slice(0, 20).map((r) => `<tr><td>${r.as_of}</td><td>${r.days}</td><td>${f(r.groups.BUY.mean)}</td><td>${f(r.groups.HOLD.mean)}</td><td>${f(r.groups.SELL.mean)}</td><td>${f(r.all)}</td><td>${f(r.nifty500)}</td></tr>`).join("")}</table>
      <details style="margin-top:8px"><summary><b>BUY calls from ${latest.as_of}</b> <span class="small">(${latest.buys.length}; tap to open)</span></summary>
        ${latest.buys.map((b) => `<div class="row"><span>${b.symbol}</span><span>${fmt(b.price)} → ${fmt(b.latest)} ${f(b.ret)}</span></div>`).join("")}</details>
      <div class="small warn" style="margin-top:8px">Days to weeks of returns are noise; judge after several months and many rating days.</div>`
    : `<div class="small">No returns yet: the first ratings were logged on ${t.rows[0].as_of}, the latest prices are from the same day. Returns appear from the next trading day's refresh.</div>`}</div>`;
}

async function renderBacktest() {
  const el = document.getElementById("btcard");
  if (!el) return;
  let b;
  try { b = await api("/api/backtest"); } catch (e) { return; }
  const s = b.summary.h12;
  if (!s) return;
  const rows = b.rows.filter((r) => r.h12);
  const bar = (v) => `<span class="${cls(v)}">${pct(v * 100, 1)}</span>`;
  el.innerHTML = `<div class="card"><h3>Rating back-test (hypothetical)</h3>
    <div class="small" style="margin-bottom:8px">Average 12-month total return after each monthly rating date, ${rows[0].date} to ${rows[rows.length - 1].date} (${s.dates} dates), using only accounts published by each date.</div>
    <div class="grid">
      <div class="kpi"><div class="l">BUY</div><div class="v">${bar(s.buy)}</div></div>
      <div class="kpi"><div class="l">HOLD</div><div class="v">${bar(s.hold)}</div></div>
      <div class="kpi"><div class="l">SELL</div><div class="v">${bar(s.sell)}</div></div>
      <div class="kpi"><div class="l">All rated stocks</div><div class="v">${bar(s.universe)}</div><div class="s">NIFTY 500 index ${pct(s.nifty500 * 100, 1)} (no dividends)</div></div></div>
    <table class="vt" style="margin-top:10px"><tr><th>Rated on</th><th>BUY</th><th>SELL</th><th>All</th></tr>
      ${rows.map((r) => `<tr><td>${r.date}</td><td>${bar(r.h12.buy)}</td><td>${bar(r.h12.sell)}</td><td>${bar(r.h12.universe_mean)}</td></tr>`).join("")}</table>
    <div class="small warn" style="margin-top:8px">Weak evidence: the 12-month windows overlap, so this is about two independent years, and they disagree (BUY lagged from Jul-2024, led from Jul-2025). Today's NIFTY 500 members only (survivorship bias); trailing EPS used for P/E. Hypothetical results are not a track record.</div>
    <div class="small" style="margin-top:6px">Live record: ${b.live_log.days} day${b.live_log.days === 1 ? "" : "s"} of ratings logged${b.live_log.first ? " since " + b.live_log.first : ""}, append-only with a hash chain.</div></div>`;
}

/* ---------- Macro tab ---------- */
function renderMacro() {
  const mc = MARKET.market.macro, g = mc.gauges;
  const gauges = Object.values(g).filter((x) => !x.missing).map((x) => `
    <div class="kpi"><div class="l">${esc(x.label)}</div><div class="v">${fmt(x.last, x.last > 1000 ? 0 : 2)}</div>
    <div class="s">${x.m1_abs != null ? `1m <span class="${cls(x.m1_abs)}">${x.m1_abs > 0 ? "+" : ""}${x.m1_abs} pts</span> (${pct(x.m1)})`
      : `1d <span class="${cls(x.d1)}">${pct(x.d1)}</span> · 1m <span class="${cls(x.m1)}">${pct(x.m1)}</span> · 3m <span class="${cls(x.m3)}">${pct(x.m3)}</span>`}</div>
    ${x.pct != null ? `<div class="s">percentile ${x.pct}% · rank ${x.rank}%</div>` : ""}</div>`).join("");
  const manual = Object.entries(mc.manual).map(([k, v]) => `
    <div class="row"><span>${esc(k.replace(/_/g, " "))}<br><span class="small">${esc(v.source || "")}</span></span>
    <span style="text-align:right">${v.value == null ? '<span class="warn">not filled</span>' : fmt(v.value, 2) + " " + esc(v.unit || "")}<br>
    <span class="small ${v.stale ? "warn" : ""}">${v.as_of ? "as of " + v.as_of + (v.stale ? " · stale" : "") : ""}</span></span></div>`).join("");
  const events = mc.events.map((e) => `
    <div class="event"><div class="t">${esc(e.title)} <span class="badge ${e.risk === "high" ? "down" : e.risk === "medium" ? "warn" : ""}">${esc(e.risk)}</span></div>
    <div class="d">${e.date} · hit: ${esc((e.sectors_hit || []).join(", ") || "–")} · helped: ${esc((e.sectors_helped || []).join(", ") || "–")}</div>
    <div class="small">${esc(e.note)}<br>Source: ${esc(e.source)}</div></div>`).join("");
  view.innerHTML = `
    <div class="card"><h3>Backdrop: ${esc(mc.backdrop)}</h3>
      ${mc.flags.length ? mc.flags.map((f) => `<div class="flag warn">&#9888; ${esc(f)}</div>`).join("") : '<div class="small">No pressure flags today.</div>'}
      <div class="small" style="margin-top:8px">${esc(mc.backdrop_rule)}. Thresholds are stated judgment defaults, not back-tested.</div></div>
    <div class="card"><h3>Global and country gauges</h3><div class="grid">${gauges}</div></div>
    <div class="card"><h3>Hand-entered figures</h3>${manual}<div class="small" style="margin-top:8px">Edit config/manual_inputs.json. Flows and the G-Sec yield are not on Yahoo.</div></div>
    <div class="card"><h3>Event notes</h3>${events || '<div class="small">None recorded.</div>'}<div class="small" style="margin-top:8px">Edit config/events.json. Geopolitics has no numeric feed; these notes carry a stated risk level and the sectors affected.</div></div>
    ${limits()}`;
}

/* ---------- Stocks tab ---------- */
const SORTS = { mos: "Margin of safety", rs_rank: "Relative strength", chg: "Today %", ret_1m: "1 month", ret_6m: "6 months", rsi: "RSI", vol_ratio: "Volume", symbol: "A–Z" };
function renderStocks() {
  const list = STOCKS.stocks.filter((s) => (!query || s.symbol.includes(query)) && (ratingFilter === "ALL" || (s.rating || "NOT RATED") === ratingFilter))
    .sort((a, b) => sortKey === "symbol" ? a.symbol.localeCompare(b.symbol) : (b[sortKey] ?? -1e9) - (a[sortKey] ?? -1e9));
  view.innerHTML = `
    <input class="search" id="q" placeholder="Search NSE symbol" value="${esc(query)}" autocapitalize="characters">
    <div class="sortbar">${["ALL", "BUY", "HOLD", "SELL", "NOT RATED"].map((k) => `<button data-rf="${k}" class="${k === ratingFilter ? "active" : ""}">${k === "ALL" ? "All ratings" : k === "NOT RATED" ? "Not rated" : k}</button>`).join("")}</div>
    <div class="sortbar">${Object.entries(SORTS).map(([k, l]) => `<button data-sort="${k}" class="${k === sortKey ? "active" : ""}">${l}</button>`).join("")}</div>
    <div class="small" style="margin-bottom:6px">${list.length} of ${STOCKS.stocks.length} stocks · data as of ${STOCKS.data_as_of}</div>
    <div class="list">${list.slice(0, 150).map(item).join("")}</div>${limits()}`;
  $("#q").addEventListener("input", (e) => { query = e.target.value.toUpperCase(); renderStocks(); $("#q").focus(); $("#q").setSelectionRange(query.length, query.length); });
  view.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => { sortKey = b.dataset.sort; renderStocks(); }));
  view.querySelectorAll("[data-rf]").forEach((b) => b.addEventListener("click", () => { ratingFilter = b.dataset.rf; renderStocks(); }));
  view.querySelectorAll(".item").forEach((el) => el.addEventListener("click", () => openStock(el.dataset.sym)));
}
function item(s) {
  const trend = s.trend === "up" ? "up" : s.trend === "down" ? "down" : "warn";
  return `<div class="item" data-sym="${s.symbol}">
    <div><span class="sym">${s.symbol}</span> ${ratingBadge(s.rating)} <span class="badge ${trend}">${s.trend}</span>${s.triggers.length ? ` <span class="badge warn">watch: ${s.triggers.join(", ")}</span>` : ""}</div>
    <div class="px">${fmt(s.close)}<br><span class="${cls(s.chg)}" style="font-size:12px">${pct(s.chg, 2)}</span></div>
    <div class="sub">${s.fair_value ? `Fair value ${fmt(s.fair_value, 0)} · margin ${pct(s.mos, 0)} · ` : ""}RSI ${s.rsi} · MACD ${esc(s.macd_state)} · RS rank ${s.rs_rank ?? "–"} · 1m ${pct(s.ret_1m)} · 6m ${pct(s.ret_6m)} · vol ${s.vol_ratio}x</div></div>`;
}

/* ---------- Watch tab ---------- */
function renderWatch() {
  const hits = STOCKS.stocks.filter((s) => s.triggers.length);
  view.innerHTML = `
    <div class="card"><h3>Swing-rule triggers today</h3>
      <div class="small">Every one is <b>NO TRADE (watch)</b>: all three rules (breakout, pullback, mean reversion) failed their back-test on 458 NIFTY 500 stocks over 12 years, so no entry, stop or target is shown. Market filter ${MARKET.market.regime_up ? "passes" : "fails"}.</div></div>
    <div class="list">${hits.length ? hits.map(item).join("") : '<div class="small">No triggers on the latest bar.</div>'}</div>
    <div class="card" style="margin-top:12px"><h3>Strongest 6-month relative strength</h3>
      <div class="list">${STOCKS.stocks.filter((s) => s.rs_rank != null).sort((a, b) => b.rs_rank - a.rs_rank).slice(0, 15).map(item).join("")}</div></div>
    ${limits()}`;
  view.querySelectorAll(".item").forEach((el) => el.addEventListener("click", () => openStock(el.dataset.sym)));
}

/* ---------- Stock detail ---------- */
async function openStock(sym) {
  view.innerHTML = `<button class="back" id="back">&#8249; Back</button><div class="small">Loading ${sym}…</div>`;
  $("#back").addEventListener("click", render);
  const d = await api(`/api/stock/${sym}`), s = d.stock, c = d.chart;
  const trend = s.trend === "up" ? "up" : s.trend === "down" ? "down" : "warn";
  view.innerHTML = `
    <button class="back" id="back">&#8249; Back</button>
    <div class="card">
      <div class="row" style="border:0"><span style="font-size:20px;font-weight:700">${s.symbol}</span><span style="font-size:20px;font-weight:700">${fmt(s.close)} <span class="${cls(s.chg)}" style="font-size:14px">${pct(s.chg, 2)}</span></span></div>
      <div><span class="badge ${trend}">daily trend ${s.trend}</span> <span class="badge ${s.weekly_up ? "up" : "down"}">weekly ${s.weekly_up ? "up" : "down"}</span>
      ${s.triggers.map((t) => `<span class="badge warn">watch: ${t}</span>`).join(" ")}</div>
      <div class="small" style="margin-top:6px">Data as of ${d.data_as_of}, Yahoo Finance, 15 min delayed.</div>
    </div>
    ${valuationCards(d)}
    <div class="card"><h3>Price, 180 sessions</h3>${lineChart([{ v: c.close, c: "var(--accent)" }, { v: c.sma50, c: "var(--warn)", w: 1 }, { v: c.sma200, c: "var(--muted)", w: 1, dash: "4 4" }], { d: 0 })}
      <div class="small">blue close · amber 50-DMA · grey 200-DMA</div></div>
    <div class="card"><h3>RSI (14)</h3>${lineChart([{ v: c.rsi, c: "var(--accent)" }], { h: 100, bands: [30, 70] })}</div>
    <div class="card"><h3>MACD (12, 26, 9)</h3>${lineChart([{ v: c.macd, c: "var(--accent)" }, { v: c.macd_signal, c: "var(--warn)", w: 1 }], { h: 110, zero: true, d: 1 })}<div class="small">${esc(s.macd_state)}</div></div>
    <div class="card"><h3>Readings</h3>
      ${[["RSI (14)", s.rsi], ["MACD / signal / hist", `${fmt(s.macd)} / ${fmt(s.macd_signal)} / ${fmt(s.macd_hist)}`], ["SMA 20 / 50 / 200", `${fmt(s.sma20, 0)} / ${fmt(s.sma50, 0)} / ${fmt(s.sma200, 0)}`],
        ["ADX (14)", s.adx], ["ATR (14) % of price", pct(s.atr_pct, 2).replace("+", "")], ["Volume vs 20-day avg", s.vol_ratio + "x"],
        ["52-week range", `${fmt(s.lo52, 0)} – ${fmt(s.hi52, 0)} (at ${s.pos52}%)`], ["Returns 1m / 3m / 6m", `${pct(s.ret_1m)} / ${pct(s.ret_3m)} / ${pct(s.ret_6m)}`],
        ["Relative strength rank", s.rs_rank == null ? "–" : s.rs_rank + "th percentile"]]
        .map(([k, v]) => `<div class="row"><span class="muted">${k}</span><span>${v}</span></div>`).join("")}
    </div>
    ${limits()}`;
  $("#back").addEventListener("click", render);
  setupSliders(d.valuation, s.close);
}

function valuationCards(d) {
  const v = d.valuation;
  if (!v) return `<div class="card"><h3>Model rating</h3><div class="small">No valuation: ${esc(d.valuation_note || "fundamentals not loaded")}</div></div>`;
  const head = `<div class="card"><h3>Model rating</h3>
    <div class="row" style="border:0"><span style="font-size:22px;font-weight:700" class="${RCLS[v.rating] || ""}">${v.rating === "NOT RATED" ? "Not rated" : v.rating}</span>
    ${v.fair_value ? `<span style="text-align:right">fair value <b>${fmt(v.fair_value, 0)}</b><br><span class="small">margin of safety ${pct(v.margin_of_safety, 0)}</span></span>` : ""}</div>
    <div style="font-size:14px">${esc(v.rating_note)}</div>
    ${v.fair_value ? `<div class="grid" style="margin-top:10px">
      <div class="kpi"><div class="l">BUY at or below</div><div class="v up">${fmt(v.buy_below, 0)}</div><div class="s">${v.required_margin}% margin required</div></div>
      <div class="kpi"><div class="l">SELL above</div><div class="v down">${fmt(v.sell_above, 0)}</div><div class="s">optimistic-case value</div></div>
      <div class="kpi"><div class="l">Downside to pessimistic</div><div class="v ${cls(v.downside_to_bear)}">${pct(v.downside_to_bear, 0)}</div></div>
      <div class="kpi"><div class="l">Upside to optimistic</div><div class="v ${cls(v.upside_to_bull)}">${pct(v.upside_to_bull, 0)}</div></div></div>
      <div class="small" style="margin-top:8px">Valid until the ${esc(v.valid_until)}. ${esc(d.valuation_method || "")}</div>` : `<div class="small" style="margin-top:6px">${esc(v.sector || "")} · ${esc(v.industry || "")}</div>`}</div>`;
  if (!v.fair_value) return head;
  const M = { dcf: "DCF (avg of two)", dcf_gordon: "· DCF perpetual growth", dcf_exit: "· DCF exit multiple", ev_ebitda: "Peer EV/EBITDA", pe: "Peer P/E", pb_roe: "Residual income (book, ROE)", blend: "Blended value" };
  const methods = Object.keys(M).filter((m) => v.scenarios.base[m] != null);
  const table = `<div class="card"><h3>Scenarios (weights ${Object.values(v.scenario_weights).map((w) => w * 100 + "%").join(" / ")})</h3>
    <table class="vt"><tr><th></th><th>Pessimistic</th><th>Base</th><th>Optimistic</th></tr>
    ${methods.map((m) => `<tr class="${m === "blend" ? "strong" : ""}"><td>${M[m]}${v.weights[m] != null ? ` <span class="small">${v.weights[m]}%</span>` : ""}</td>${["bear", "base", "bull"].map((s) => `<td>${fmt(v.scenarios[s][m], 0)}</td>`).join("")}</tr>`).join("")}
    <tr><td>12-month target</td>${["bear", "base", "bull"].map((s) => `<td>${fmt(v.targets.m12[s], 0)}</td>`).join("")}</tr>
    <tr><td>36-month target</td>${["bear", "base", "bull"].map((s) => `<td>${fmt(v.targets.m36[s], 0)}</td>`).join("")}</tr></table>
    <div class="small" style="margin-top:6px">Weighted targets: 12 months ${fmt(v.targets.m12.weighted, 0)} · 36 months ${fmt(v.targets.m36.weighted, 0)}. Targets assume the stock reaches fair value and earns its cost of equity; they are not forecasts.</div></div>`;
  const rd = v.reverse_dcf ? `<div class="card"><h3>What the price implies</h3><div style="font-size:14px">${esc(v.reverse_dcf.text)}</div></div>` : "";
  const q = v.quality;
  const qual = `<div class="card"><h3>Quality gate ${q.gate_failures && q.gate_failures.length ? '<span class="badge down">fails</span>' : '<span class="badge up">passes</span>'}</h3>
    ${[["Piotroski F-score", q.piotroski == null ? "n/a" : `${q.piotroski} / ${q.piotroski_tests}${q.piotroski_failed && q.piotroski_failed.length ? ` (failed: ${q.piotroski_failed.join(", ")})` : ""}`],
       ["Altman Z", q.altman_z ?? "n/a"], ["Net debt / EBITDA", q.net_debt_to_ebitda == null ? "n/a" : q.net_debt_to_ebitda + "x"],
       ["ROE", q.roe == null ? "n/a" : q.roe + "%"], ["Market cap", q.market_cap_cr ? fmt(q.market_cap_cr, 0) + " crore" : "n/a"], ["Promoter pledge", q.pledge]]
      .map(([k, val]) => `<div class="row"><span class="muted">${k}</span><span style="text-align:right">${esc(val)}</span></div>`).join("")}
    ${q.gate_failures && q.gate_failures.length ? `<div class="small warn">${esc(q.gate_failures.join("; "))}</div>` : ""}</div>`;
  const a = v.assumptions || {};
  const flat = (x) => x == null ? "–" : typeof x === "object" ? Object.entries(x).map(([k, y]) => `${k} ${typeof y === "number" ? y : esc(y)}`).join(" · ") : esc(x);
  const assum = `<div class="card"><details><summary><b>Assumptions used</b> <span class="small">(automated defaults, tap to open)</span></summary>
    ${Object.entries(a).map(([k, x]) => `<div class="row"><span class="muted">${esc(k.replace(/_/g, " "))}</span><span style="text-align:right;max-width:60%">${flat(x)}</span></div>`).join("")}</details></div>`;
  const warns = v.warnings && v.warnings.length ? `<div class="card"><h3>Check before relying on this</h3>${v.warnings.map((w) => `<div class="flag warn">&#9888; ${esc(w)}</div>`).join("")}</div>` : "";
  return head + sliderCard(v) + table + rd + qual + warns + assum;
}

const SLIDERS = {
  dcf: [["g", "Revenue growth, year 1", 0, 0.30, 0.005], ["margin", "EBITDA margin from year 3", 0.01, 0.60, 0.005],
        ["wacc", "Discount rate (WACC)", 0.06, 0.18, 0.001], ["gT", "Growth after year 10", 0.02, null, 0.0005]],
  ri: [["roe", "Return on equity (ROE)", 0, 0.30, 0.0025], ["ke", "Cost of equity", 0.08, 0.18, 0.001]],
};
function sliderCard(v) {
  const m = v.model;
  if (!m) return `<div class="card"><h3>Try your own assumptions</h3><div class="small">Not available for this stock: it is valued on peer P/E only, so there is no model to adjust.</div></div>`;
  const x = VM.defaults(m);
  const rows = SLIDERS[m.type].map(([k, label, lo, hi, step]) => {
    const max = hi == null ? m.rf - 0.0005 : hi;
    return `<div class="sl"><label for="sl-${k}">${label}</label><input type="range" id="sl-${k}" data-k="${k}" min="${lo}" max="${max}" step="${step}" value="${x[k]}">
      <span class="slv" id="slv-${k}"></span><span class="sld small">default ${(x[k] * 100).toFixed(k === "gT" ? 2 : 1)}%</span></div>`;
  }).join("");
  return `<div class="card" id="slcard"><h3>Try your own assumptions</h3>
    <div class="small" style="margin-bottom:8px">Moves the base case; the pessimistic and optimistic cases shift with it by the engine's fixed offsets. Peer-multiple values stay fixed. Nothing here changes the saved rating.</div>
    ${rows}
    <div id="slout"></div>
    <button class="ghost" id="slreset">Reset to defaults</button></div>`;
}
function setupSliders(v, price) {
  const m = v && v.model, card = document.getElementById("slcard");
  if (!m || !card) return;
  const inputs = card.querySelectorAll('input[type="range"]');
  const update = () => {
    const x = {};
    inputs.forEach((i) => { x[i.dataset.k] = +i.value; document.getElementById("slv-" + i.dataset.k).textContent = (+i.value * 100).toFixed(i.dataset.k === "gT" ? 2 : 1) + "%"; });
    const r = VM.rate(m, price, x);
    const out = document.getElementById("slout");
    if (r.error) { out.innerHTML = `<div class="flag warn">${esc(r.error)}</div>`; return; }
    const changed = r.rating !== v.rating;
    out.innerHTML = `<div class="row" style="border:0;margin-top:6px"><span style="font-size:20px;font-weight:700" class="${RCLS[r.rating] || ""}">${r.rating}</span>
      <span style="text-align:right">fair value <b>${fmt(r.fv, 0)}</b><br><span class="small">margin of safety ${pct(r.mos, 0)}</span></span></div>
      <div class="small">${esc(r.note)}${changed ? ` · <b>changed from ${v.rating}</b>` : ""}</div>
      <div class="grid" style="margin-top:8px">
        <div class="kpi"><div class="l">BUY at or below</div><div class="v up">${fmt(r.buyBelow, 0)}</div></div>
        <div class="kpi"><div class="l">SELL above</div><div class="v down">${fmt(r.sellAbove, 0)}</div></div></div>
      <div class="small" style="margin-top:6px">Blended value: pessimistic ${fmt(r.scenarios.bear.blend, 0)} · base ${fmt(r.scenarios.base.blend, 0)} · optimistic ${fmt(r.scenarios.bull.blend, 0)}${r.disagree ? " · methods disagree by more than 2x" : ""}</div>`;
  };
  inputs.forEach((i) => i.addEventListener("input", update));
  document.getElementById("slreset").addEventListener("click", () => { const x = VM.defaults(m); inputs.forEach((i) => { i.value = x[i.dataset.k]; }); update(); });
  update();
}

/* ---------- F&O tab ---------- */
let FNO = null, fnoIndex = "NIFTY 50";
async function renderFno() {
  if (!FNO) {
    view.innerHTML = '<div class="small">Loading F&amp;O view…</div>';
    try { FNO = await api("/api/fno"); } catch (e) { if (e.message !== "login") view.innerHTML = `<div class="card">F&amp;O view not available: ${esc(e.message)}</div>`; return; }
  }
  const F = FNO, v = F.volatility, i = F.indices[fnoIndex], fut = i.futures;
  const callCls = i.call.action.startsWith("BUY") ? "up" : i.call.action.startsWith("SELL") ? "down" : "warn";
  const watch = Object.entries(i.signals);
  const ruleName = Object.fromEntries((F.rules || []).map((r) => [r.rule, r]));
  const hint = { low: ["bullish", "bearish"], high: ["bullish", "bearish", "neutral"], middle: ["bullish", "bearish", "neutral"] }[v.regime];
  view.innerHTML = `
    <div class="sortbar">${Object.keys(F.indices).map((n) => `<button data-ix="${n}" class="${n === fnoIndex ? "active" : ""}">${n}</button>`).join("")}</div>
    <div class="card"><h3>Trade call · ${esc(fnoIndex)}</h3>
      <div style="font-size:24px;font-weight:700" class="${callCls}">${esc(i.call.action)}</div>
      <div style="font-size:14px">${esc(i.call.reason)}.</div>
      ${i.call.stop ? `<div class="grid" style="margin-top:8px"><div class="kpi"><div class="l">Reference entry</div><div class="v">${fmt(i.call.ref_price, 0)}</div></div>
        <div class="kpi"><div class="l">Stop</div><div class="v down">${fmt(i.call.stop, 0)}</div></div><div class="kpi"><div class="l">Lots at your risk limit</div><div class="v">${i.call.lots}</div></div></div>` : ""}
      ${watch.length ? `<div style="margin-top:10px"><b>Watch only</b> <span class="small">(rules that failed their back-test, shown for information)</span>
        ${watch.map(([r, s]) => `<div class="row"><span>${r} ${esc(ruleName[r] ? ruleName[r].label : "")}: <b class="${s.side === "BUY" ? "up" : "down"}">${s.side}</b></span>
          <span style="text-align:right">ref ${fmt(s.ref_price, 0)} · stop ${fmt(s.stop, 0)}<br><span class="small">${s.lots ? s.lots + " lot(s) at 1% risk" : "0 lots: risk per lot exceeds 1% of capital"}</span></span></div>`).join("")}</div>` : '<div class="small" style="margin-top:8px">No rule is signalling on the latest close.</div>'}
    </div>
    <div class="card"><h3>${esc(fnoIndex)} · ${i.call.action === "NO TRADE" ? "context" : "market"}</h3>
      <div class="grid">
        <div class="kpi"><div class="l">Close</div><div class="v">${fmt(i.close, 0)}</div><div class="s ${cls(i.chg)}">${pct(i.chg, 2)} · regime ${i.regime}</div></div>
        <div class="kpi"><div class="l">RSI (14)</div><div class="v">${i.rsi}</div><div class="s">${pct(i.vs_200dma)} vs 200-DMA</div></div>
        <div class="kpi"><div class="l">ATR (14)</div><div class="v">${fmt(i.atr, 0)}</div><div class="s">${i.atr_pct}% of price · 20d vol ${i.realised_vol_20d}%</div></div>
        <div class="kpi"><div class="l">Expected move to ${i.options_expiry}</div><div class="v">±${fmt(i.expected_move, 0)}</div><div class="s">1 SD range ${fmt(i.range_1sd[0], 0)} – ${fmt(i.range_1sd[1], 0)}</div></div></div>
      ${lineChart([{ v: i.series.close, c: "var(--accent)" }, { v: i.series.sma50, c: "var(--warn)", w: 1 }, { v: i.series.sma200, c: "var(--muted)", w: 1, dash: "4 4" }])}
      <div class="small">blue close · amber 50-DMA · grey 200-DMA · expiries: ${i.expiries.weekly ? "weekly " + i.expiries.weekly + " · " : ""}monthly ${i.expiries.monthly} · next ${i.expiries.next_monthly}</div></div>
    <div class="card"><h3>Volatility</h3>
      <div class="grid"><div class="kpi"><div class="l">India VIX</div><div class="v">${v.india_vix}</div><div class="s">52w ${v.low_52w} – ${v.high_52w}</div></div>
        <div class="kpi"><div class="l">IV rank / percentile</div><div class="v">${v.rank}% / ${v.percentile}%</div><div class="s">${v.regime}</div></div></div>
      <div class="small" style="margin-top:6px">${esc(v.strategy_hint)}. Volatility used for ${esc(fnoIndex)}: ${i.iv_used}% (${esc(i.iv_note)}).</div></div>
    <div class="card"><h3>Futures (${fut.expiry} expiry)</h3>
      ${[["Fair value", fmt(fut.fair_value, 1) + ` (basis +${fmt(fut.basis, 1)})`], ["Lot size", `${fut.lot} <span class="small">(${esc(fut.lot_as_of)})</span>`], ["Contract value per lot", "₹" + fmt(fut.notional_rs, 0)],
         ["Stop of 2 x ATR", fmt(fut.stop_points_2atr, 0) + " points = ₹" + fmt(fut.risk_per_lot_rs, 0) + " per lot"], [`Lots at ${F.risk_per_trade_pct}% risk of ₹${fmt(F.capital_rs, 0)}`, fut.lots_at_risk_limit],
         ["Capital needed for 1 lot at that risk", "₹" + fmt(fut.capital_for_one_lot_rs, 0)]].map(([k, x]) => `<div class="row"><span class="muted">${k}</span><span style="text-align:right">${x}</span></div>`).join("")}
      ${fut.lots_at_risk_limit === 0 ? '<div class="small warn" style="margin-top:6px">One futures lot risks more than your per-trade limit: use defined-risk option spreads instead, or accept a larger risk knowingly.</div>' : ""}</div>
    <div class="card"><h3>Option structures by view (${i.options_expiry}, ${i.dte} days)</h3>
      <div class="small" style="margin-bottom:6px">${esc(F.model_note)}</div>
      ${i.structures.map((s) => `<div class="event"><div class="t">${esc(s.name)} <span class="badge ${s.view === "bullish" ? "up" : s.view === "bearish" ? "down" : ""}">${s.view}</span>${hint.includes(s.view) && ((v.regime === "low" && s.net_type === "debit") || (v.regime === "high" && s.net_type === "credit") || v.regime === "middle") ? ' <span class="badge">fits volatility</span>' : ""}</div>
        <div class="d">${s.legs.map((l) => `${l.action} ${fmt(l.strike, 0)} ${l.type} @ ${l.premium}`).join(" · ")}</div>
        <div class="small">Net ${s.net_type} ₹${fmt(s.net_rs, 0)} per lot · max profit ₹${fmt(s.max_profit_rs, 0)} · max loss ₹${fmt(s.max_loss_rs, 0)} · breakeven ${s.breakevens.map((b) => fmt(b, 0)).join(" / ")} · lots within ${2}% limit: ${s.lots_within_limit}</div></div>`).join("")}
      <div class="small" style="margin-top:6px">These are templates for a view you already hold, not a recommendation of the view.</div></div>
    <div class="card"><h3>Index rules: back-test ${F.backtest_split ? "(judged from " + F.backtest_split + ")" : ""}</h3>
      <table class="vt"><tr><th>Rule</th><th>Trades</th><th>Win</th><th>PF</th><th>Per trade</th><th>Verdict</th></tr>
      ${(F.rules || []).map((r) => `<tr><td>${r.rule} ${esc(r.label)}</td><td>${r.oos ? r.oos.trades : "–"}</td><td>${r.oos ? r.oos.win + "%" : "–"}</td><td>${r.oos ? r.oos.pf : "–"}</td><td>${r.oos ? (r.oos.exp > 0 ? "+" : "") + r.oos.exp + "R" : "–"}</td><td class="${r.passed ? "up" : "down"}">${r.passed ? "passed" : "rejected"}</td></tr>`).join("")}</table>
      <div class="small" style="margin-top:6px">NIFTY 50 and BANK NIFTY since 2007, after costs and futures carry. A rule needs 40+ trades, +0.20R per trade, t-stat above 2, profit factor 1.3+, drawdown within 20% and a profit at double costs before it may issue BUY / SELL calls.</div></div>
    <div class="limits warn">${esc(F.risk_note)} ${esc(MARKET ? MARKET.limits : "")}</div>`;
  view.querySelectorAll("[data-ix]").forEach((b) => b.addEventListener("click", () => { fnoIndex = b.dataset.ix; renderFno(); }));
}

function limits() { return `<div class="limits">${esc(MARKET ? MARKET.limits : "")}</div>`; }

/* ---------- shell ---------- */
async function load() {
  [MARKET, STOCKS] = await Promise.all([api("/api/market"), api("/api/stocks")]);
  $("#asof").textContent = "· " + MARKET.data_as_of;
  render();
}
function render() {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  if (!MARKET) { view.innerHTML = '<div class="card">No data yet. Run jobs/refresh.py, then reload.</div>'; return; }
  ({ market: renderMarket, stocks: renderStocks, watch: renderWatch, fno: renderFno, macro: renderMacro })[tab]();
  window.scrollTo(0, 0);
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; render(); }));
$("#refreshBtn").addEventListener("click", async () => {
  const r = await api("/api/refresh", { method: "POST" }); alert(r.message);
});
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: $("#pw").value }) });
  if (r.ok) { $("#login").classList.add("hidden"); load(); } else { $("#loginErr").textContent = "Wrong password"; }
});
load().catch((e) => { if (e.message !== "login") view.innerHTML = `<div class="card">${esc(e.message)}</div>`; });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
