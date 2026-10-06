const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v, d = 1) => v == null ? "—" : `${(v * 100).toFixed(d)}%`;
const sgn = (v, d = 2) => v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}`;
const money = v => v == null ? "—" : `$${v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(2)}`;
const big = v => v == null ? "—" : v >= 1e12 ? `$${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;
const cls = v => v == null ? "" : v >= 0 ? "up" : "down";
const daysUntil = iso => iso ? Math.round((new Date(iso) - new Date()) / 864e5) : null;
const ago = ts => { const m = Math.round((Date.now() / 1000 - ts) / 60); return m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`; };

let SNAP = null, FILTER = "all", POLL = null, MODE = "picks";
const TOP_N = 5;
const TOP_MIN_CAP = 2e9, TOP_MIN_PRICE = 10;
const passes = c => c.pump?.level === "low" && !c.data_warning && c.bull >= c.bear &&
  (c.market_cap || 0) >= TOP_MIN_CAP && (c.price || 0) >= TOP_MIN_PRICE;

// Online (Vercel) the page is read-only: everything comes from the bundle the PC uploads after each refresh.
const REMOTE = !!window.REMOTE;
let BUNDLE = null;
async function remoteApi(path) {
  if (!BUNDLE || path === "/api/snapshot") {
    const r = await fetch("/api/data", { cache: "no-store" });
    if (r.status === 401 || r.redirected) { location.href = "/login"; throw new Error("Signed out"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    BUNDLE = await r.json();
  }
  if (path === "/api/snapshot") return { snapshot: BUNDLE.snapshot, status: { ...BUNDLE.status, running: false, last_error: null } };
  if (path === "/api/performance") return BUNDLE.performance;
  const m = path.match(/^\/api\/stock\/(.+)$/);
  if (m) {
    const sym = decodeURIComponent(m[1]).toUpperCase();
    const card = BUNDLE.snapshot.cards.find(c => c.symbol === sym) || null;
    return BUNDLE.details[sym] || { card, chart: [], history: [], news: card?.news || [], implied_move: null, info: {}, all_drivers: [], partial: true };
  }
  throw new Error("Not available online. Use the app on your PC for this.");
}

async function api(path, opts) {
  if (REMOTE) return remoteApi(path);
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---------------------------------------------------------------- header / status
function renderStatus(status) {
  const p = $("#progress");
  if (status.running) {
    p.classList.remove("hidden");
    $("#progressText").textContent = status.step;
    $("#refreshBtn").disabled = true;
  } else {
    p.classList.add("hidden");
    $("#refreshBtn").disabled = false;
    if (status.last_error) { p.classList.remove("hidden"); $("#progressText").textContent = "Last refresh failed: " + status.last_error; }
  }
}

function renderHeader() {
  if (!SNAP) return;
  const s = SNAP.session, labels = { open: "Market open", pre: "Pre-market", after: "After hours", closed: "Market closed" };
  const el = $("#session"); el.textContent = labels[s] || s; el.className = "pill " + s;
  const gen = new Date(SNAP.generated_at), sameDay = gen.toDateString() === new Date().toDateString();
  const ageMin = (Date.now() - gen) / 6e4;
  $("#updated").textContent = "Updated " + (sameDay ? "" : gen.toLocaleDateString([], { month: "short", day: "numeric" }) + " ") +
    gen.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) +
    (REMOTE && SNAP.session === "open" && ageMin > 45 ? " · PC may be off" : "");
  const m = SNAP.model, bs = m.big_stats, ds = m.dir_stats;
  const dirEdge = ds.holdout_acc > ds.baseline_acc + 0.01;
  $("#modelBar").innerHTML = `
    <span>Big move = <b>≥${SNAP.big_move_pct}%</b> close-to-close next session</span>
    <span>Model trained on <b>${m.n_train.toLocaleString()}</b> stock-days through <b>${m.trained_through}</b></span>
    <span>Out-of-sample ranking skill (AUC) <b>${bs.holdout_auc.toFixed(2)}</b> <span class="small">(0.50 = guessing)</span></span>
    <span>Direction call: <b style="color:${dirEdge ? "var(--up)" : "var(--warn)"}">${dirEdge ? `${pct(ds.holdout_acc)} accurate in testing` : "no proven edge yet"}</b></span>`;
}

// ---------------------------------------------------------------- cards
function gauge(p) {
  const r = 32, c = 2 * Math.PI * r, col = p >= .5 ? "var(--accent-2)" : p >= .25 ? "var(--accent)" : "var(--faint)";
  return `<div class="gauge"><svg width="78" height="78" viewBox="0 0 78 78">
    <circle cx="39" cy="39" r="${r}" fill="none" stroke="var(--line-2)" stroke-width="7"/>
    <circle cx="39" cy="39" r="${r}" fill="none" stroke="${col}" stroke-width="7" stroke-linecap="round"
      stroke-dasharray="${c * p} ${c}"/></svg><div class="val">${Math.round(p * 100)}%</div></div>`;
}

function tags(c) {
  const t = [];
  if (c.mover === "day_gainers") t.push(`<span class="tag g">Top gainer</span>`);
  if (c.mover === "day_losers") t.push(`<span class="tag l">Top loser</span>`);
  if (c.mover === "most_actives") t.push(`<span class="tag a">Most active</span>`);
  if (c.mover === "small_cap_gainers") t.push(`<span class="tag g">Small-cap gainer</span>`);
  if (c.pump?.level === "high") t.push(`<span class="tag l">⚠ Pump risk high</span>`);
  if (c.pump?.level === "elevated") t.push(`<span class="tag e">Pump risk elevated</span>`);
  const d = daysUntil(c.next_earnings);
  if (d != null && d <= 10) t.push(`<span class="tag e">Earnings ${d <= 0 ? "today" : `in ${d}d`}</span>`);
  if (c.sector) t.push(`<span class="tag">${esc(c.sector)}</span>`);
  return t.join("");
}

function dirBlock(c) {
  const up = c.p_up;
  const lean = up >= .5 ? `Leans up ${pct(up, 0)}` : `Leans down ${pct(1 - up, 0)}`;
  return `<div class="dir ${c.dir_edge ? "" : "noedge"}">
    <div class="meter-lbl">${c.dir_edge ? `<b>${lean}</b>` : `Direction: <b>no proven edge</b> — tested at coin-flip accuracy`}</div>
    <div class="dirbar"><i style="width:${up * 100}%"></i></div></div>`;
}

function lockLine(k, c) {
  const since = c.price != null && k.price ? (c.price / k.price - 1) * 100 : null;
  return `<div class="lockline"><b>🔒 #${k.rank} locked</b> at ${money(k.price)} · chance then ${pct(k.p_big, 0)}${since != null ? ` · since lock <span class="${cls(since)}">${sgn(since)}%</span>` : ""}</div>`;
}

const fmtDay = iso => new Date(iso + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

// ---------------------------------------------------------------- trade plan (direction-free)
let RISK = 500;
try { RISK = +localStorage.getItem("risk") || 500; } catch {}
const VERDICT = {
  wait: () => "Wait for the 10:00 ET range", long: p => `Broke up ${p.range.since ? "at " + p.range.since : ""}: long setup`,
  short: p => `Broke down ${p.range.since ? "at " + p.range.since : ""}: short setup`, stand_aside: () => "Broke both ways: stand aside",
  skip: () => "Skip",
};

function planBlock(p, c, full) {
  if (!p) return "";
  const r = p.range || {}, lines = [];
  if (r.state === "set") {
    lines.push(`<div class="lv">Range 9:30–10:00: ${money(r.low)} – ${money(r.high)} · now ${money(r.last)}</div>`);
    lines.push(`<div class="lv">Long above ${money(r.high)}, stop ${money(r.low)} · Short below ${money(r.low)}, stop ${money(r.high)}</div>`);
  } else if (r.state === "forming") {
    lines.push(`<div class="lv">Range forming: ${money(r.low)} – ${money(r.high)} so far · now ${money(r.last)}</div>`);
  } else {
    lines.push(`<div class="pnote">Opens 9:30 ET. Let the first 30 minutes set the range, then trade the side it breaks, with the stop on the other side.</div>`);
  }
  const dist = p.stop_dist || p.atr_dollars;
  if (dist > 0) {
    const sh = Math.floor(RISK / dist);
    lines.push(`<div class="lv">Size: $${RISK.toLocaleString()} risk ÷ ${money(dist)} stop${r.state === "set" ? " (range width)" : " (1× normal day)"} = <b>${sh.toLocaleString()} shares</b> (~${money(sh * (c.price || 0))})</div>`);
  }
  (p.skip || []).forEach(s => lines.push(`<div class="skip">⚠ ${esc(s)}</div>`));
  (p.notes || []).forEach(s => lines.push(`<div class="pnote">• ${esc(s)}</div>`));
  const o = p.options;
  if (o) lines.push(`<div class="pnote">Options: ±${o.implied_pct.toFixed(1)}% priced by ${o.expiry} vs ~${o.expected_pct.toFixed(1)}% typical → <b>${o.verdict}</b>${full ? ` (${esc(o.label)})` : ""}</div>`);
  else if (full) lines.push(`<div class="pnote">Options: no options market for this stock.</div>`);
  return `<div class="plan v-${p.verdict}"><div class="plan-head"><span class="sec-t" style="margin:0">Trade plan</span><span class="verdict">${VERDICT[p.verdict](p)}</span></div>
    ${lines.join("")}<div class="untested">Rules not yet tested on past data. Paper trade them first.${p.as_of ? ` · as of ${p.as_of} ET` : ""}</div></div>`;
}

function card(c, rank, lock) {
  const w = c.why || {};
  const ref = w.sector_etf ? `${w.sector_etf} ${sgn(w.sector_ret)}%` : `S&P ${sgn(w.mkt_ret)}%`;
  const total = c.bull + c.bear + c.caution || 1;
  const n = c.news?.[0];
  return `<article class="card ${c.pump?.level === "high" ? "risk-high" : ""}" data-sym="${c.symbol}">
    <div class="card-head">
      <div><div class="sym">${rank ? `<span class="rank">#${rank}</span>` : ""}${c.symbol}</div><div class="name">${esc(c.name)}</div><div class="tags">${tags(c)}</div></div>
      ${REMOTE ? (c.watch ? `<span class="star on" title="On your watchlist">★</span>` : "") : `<button class="star ${c.watch ? "on" : ""}" data-star="${c.symbol}" title="Watchlist">${c.watch ? "★" : "☆"}</button>`}
    </div>
    <div class="price-row">
      <div><div class="price">${money(c.price)}</div><div class="prev">prev close ${money(c.prev_close)}${c.partial ? " · live" : ""}</div></div>
      <div class="chg ${cls(c.change_pct)}">${sgn(c.change)} (${sgn(c.change_pct)}%)</div>
    </div>
    ${lock ? lockLine(lock, c) : ""}
    ${lock ? planBlock(lock.plan, c) : ""}
    <div class="meter">${gauge(c.p_big)}
      <div><div class="meter-lbl">Chance of a <b>≥${SNAP.big_move_pct}% move</b> next session</div>
        <div class="meter-lbl"><b>${c.lift.toFixed(1)}×</b> the average stock's odds</div>${dirBlock(c)}</div>
    </div>
    ${c.data_warning ? `<div class="warnbox">⚠ ${esc(c.data_warning)}</div>` : ""}
    ${pumpBox(c)}
    <div class="why"><div class="sec-t">Why it moved today</div>
      <div class="kind">${esc(w.kind)}</div>
      <div class="facts">${sgn(w.ret)}% vs ${ref} · ${w.rel_volume?.toFixed(1)}× volume</div>
      ${n ? `<a class="headline" href="${esc(n.link)}" target="_blank" rel="noopener" data-stop>${esc(n.title)} <span class="pub">— ${esc(n.publisher)}, ${ago(n.time)}</span></a>` : ""}
    </div>
    ${c.drivers.length ? `<div><div class="sec-t">What's driving the odds</div><ul class="drivers">${c.drivers.map(d => `<li>${esc(d.text)}</li>`).join("")}</ul></div>` : ""}
    <div class="sig"><span><span class="up">${c.bull} positive</span> · <span class="down">${c.bear} negative</span> · <span style="color:var(--warn)">${c.caution} caution</span></span>
      <div class="sigbar"><i class="b" style="width:${c.bull / total * 100}%"></i><i class="r" style="width:${c.bear / total * 100}%"></i><i class="c" style="width:${c.caution / total * 100}%"></i></div></div>
  </article>`;
}

function pumpBox(c) {
  const p = c.pump;
  if (!p || p.level === "low") return "";
  return `<div class="pumpbox ${p.level}"><b>${p.level === "high" ? "⚠ Pump-and-dump warning signs" : "Some pump-and-dump warning signs"} (${p.score}/100)</b>
    <ul>${p.flags.slice(0, 3).map(f => `<li>${esc(f.label)}: ${esc(f.value)}</li>`).join("")}</ul></div>`;
}

function renderGrid() {
  if (!SNAP) { $("#grid").innerHTML = `<div class="empty">Loading first scan — downloading two years of data and training the model. This takes about a minute.</div>`; return; }
  const q = $("#search").value.trim().toLowerCase(), minP = +$("#minP").value / 100, sort = $("#sort").value;
  $("#count").textContent = "";
  if (MODE === "picks") {
    const P = SNAP.picks || {}, items = P.items || [];
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    $("#modeTitle").textContent = P.session ? `Picks for ${fmtDay(P.session)}` : "Locked picks";
    const note = $("#modeNote");
    note.className = "mode-note";
    if (!P.session) {
      note.textContent = "The first list locks after the next market close (4:10 PM ET). It's built from the completed trading day, then stays fixed for the next session.";
    } else {
      const at = new Date(P.locked_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      note.textContent = `Locked ${at} from the completed close. This list stays the same until the next close; only prices update.` +
        (P.session < today ? " This is the latest list available, because the PC wasn't running at the last close." : "");
    }
    $("#topCount").textContent = "";
    $("#grid").innerHTML = items.length ? items.map(k => {
      const c = SNAP.cards.find(x => x.symbol === k.symbol);
      return c ? card(c, k.rank, k) : `<article class="card"><div class="sym">#${k.rank} ${k.symbol}</div>${lockLine(k, {})}<div class="note">No live data for this stock right now.</div></article>`;
    }).join("") : (P.session ? `<div class="empty">No stock passed every check at the last close, so nothing was locked.</div>` : "");
    $("#grid").insertAdjacentHTML("beforeend", prevPicks());
    return;
  }
  if (MODE === "live") {
    const ok = SNAP.cards.filter(passes).sort((a, b) => b.p_big - a.p_big);
    $("#modeTitle").textContent = "Live view";
    const note = $("#modeNote");
    note.className = "mode-note warn";
    note.textContent = "Re-ranked at every refresh. During market hours it uses a half-finished trading day, so it changes often. Watch it here, but decide from the locked picks.";
    $("#topCount").textContent = `${ok.length} of ${SNAP.cards.length} stocks pass the checks right now. Showing the ${Math.min(TOP_N, ok.length)} most likely to move.`;
    $("#grid").innerHTML = ok.length ? ok.slice(0, TOP_N).map((c, i) => card(c, i + 1)).join("") : `<div class="empty">No stocks pass all the checks right now.</div>`;
    return;
  }
  $("#modeTitle").textContent = "All stocks";
  $("#modeNote").textContent = "";
  const hidePump = $("#hidePump").checked, nHigh = SNAP.cards.filter(c => c.pump?.level === "high").length;
  $("#pumpCount").textContent = nHigh ? `${nHigh} flagged` : "";
  let cards = SNAP.cards.filter(c => !(hidePump && c.pump?.level === "high" && !c.watch) &&
    (!q || c.symbol.toLowerCase().includes(q) || (c.name || "").toLowerCase().includes(q)) && c.p_big >= minP &&
    (FILTER === "all" || (FILTER === "movers" && c.mover) || (FILTER === "watch" && c.watch) ||
      (FILTER === "earnings" && (d => d != null && d <= 10)(daysUntil(c.next_earnings)))));
  const key = { p_big: c => c.p_big, abs_change: c => Math.abs(c.change_pct || 0), rel_volume: c => c.rel_volume || 0, signals: c => c.bull - c.bear }[sort];
  cards.sort((a, b) => key(b) - key(a));
  $("#count").textContent = `${cards.length} of ${SNAP.cards.length} stocks`;
  $("#grid").innerHTML = cards.length ? cards.map(card).join("") : `<div class="empty">No stocks match these filters.</div>`;
}

// ---------------------------------------------------------------- detail drawer
function priceChart(rows) {
  if (!rows.length) return "";
  const W = 700, H = 220, VH = 50, pad = 4;
  const cs = rows.map(r => r.c), lo = Math.min(...cs.concat(rows.map(r => r.s ?? Infinity))), hi = Math.max(...cs.concat(rows.map(r => r.s ?? -Infinity)));
  const x = i => pad + i * (W - 2 * pad) / (rows.length - 1), y = v => pad + (hi - v) * (H - 2 * pad) / (hi - lo || 1);
  const line = k => rows.map((r, i) => r[k] == null ? "" : `${i && rows[i - 1][k] != null ? "L" : "M"}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`).join("");
  const vmax = Math.max(...rows.map(r => r.v || 0));
  const bw = (W - 2 * pad) / rows.length;
  const vols = rows.map((r, i) => { const h = (r.v || 0) / vmax * VH, up = i && r.c >= rows[i - 1].c;
    return `<rect x="${x(i) - bw / 2}" y="${H + 8 + VH - h}" width="${Math.max(bw - 0.6, .6)}" height="${h}" fill="${up ? "var(--up)" : "var(--down)"}" opacity=".45"/>`; }).join("");
  const first = rows[0], last = rows.at(-1);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H + VH + 12}" role="img" aria-label="Price chart, one year">
    <path d="${line("s")}" fill="none" stroke="var(--warn)" stroke-width="1.3" opacity=".8"/>
    <path d="${line("c")}" fill="none" stroke="var(--accent)" stroke-width="1.8"/>${vols}
    <text x="${W - pad}" y="12" text-anchor="end" fill="var(--muted)" font-size="11" font-family="JetBrains Mono">${money(hi)}</text>
    <text x="${W - pad}" y="${H - 4}" text-anchor="end" fill="var(--muted)" font-size="11" font-family="JetBrains Mono">${money(lo)}</text>
  </svg><div class="legend"><span><i style="background:var(--accent)"></i>Close</span><span><i style="background:var(--warn)"></i>50-day avg</span><span>Volume below</span>
  <span style="margin-left:auto">${first.d} → ${last.d} · ${sgn((last.c / first.c - 1) * 100, 1)}%</span></div></div>`;
}

const FEAT_LABEL = {
  log_atr_pct: "Normal daily range (ATR)", rv20: "20-day volatility", vol_expansion: "Volatility expanding", rel_volume: "Relative volume",
  abs_ret: "Size of today's move", abs_gap: "Size of opening gap", range_vs_atr: "Today's range vs normal", squeeze: "Bollinger width (low = squeeze)",
  rsi_extreme: "RSI distance from 50", from_52w_high: "Distance from 52w high", from_52w_low: "Distance above 52w low", earnings_next: "Earnings before next session",
  earnings_today: "Earnings reaction today", mkt_abs_ret: "S&P 500 move size", vix: "VIX level", streak: "Recent big-move days",
  ret1: "Today's return", ret5: "5-day return", ret20: "20-day return", rsi_c: "RSI (centred)", dist_sma20: "Distance from 20-day avg", dist_sma50: "Distance from 50-day avg",
  gap: "Opening gap", close_loc: "Close position in day's range", mkt_ret: "S&P 500 return", rel_strength_20: "20-day strength vs S&P", rel_volume_signed: "Volume × direction",
};

function impactBars(items, key, maxAbs) {
  maxAbs = maxAbs || Math.max(...items.map(i => Math.abs(i[key])), 1e-9);
  return `<div class="bars">${items.map(i => { const v = i[key], w = Math.abs(v) / maxAbs * 50;
    return `<div class="row"><span>${FEAT_LABEL[i.feature] || i.feature}</span><div class="track"><span class="mid"></span>
      <i style="${v >= 0 ? `left:50%` : `right:50%`};width:${w}%;background:${v >= 0 ? "var(--accent)" : "var(--faint)"}"></i></div>
      <span class="num small" style="text-align:right">${sgn(v)}</span></div>`; }).join("")}</div>`;
}

async function openDetail(sym) {
  const dr = $("#drawer"), body = $("#drawerBody");
  history.replaceState(null, "", `#stock=${sym}`);
  dr.classList.remove("hidden");
  body.innerHTML = `<div class="empty"><span class="spinner"></span> Loading ${esc(sym)}…</div>`;
  let d;
  try { d = await api(`/api/stock/${encodeURIComponent(sym)}`); } catch (e) { body.innerHTML = `<div class="empty">Could not load ${esc(sym)}: ${esc(e.message)}</div>`; return; }
  const c = d.card, i = d.info || {}, im = d.implied_move;
  if (!c) { body.innerHTML = `<div class="d-head"><h2>${esc(sym)}</h2></div><div class="box note">Not scored yet. Add it to your watchlist and it will be included in the next refresh.</div>${priceChart(d.chart)}`; return; }
  if (d.partial) { body.innerHTML = `<div class="d-head"><h2>${c.symbol} <span class="chg ${cls(c.change_pct)}" style="font-size:16px">${money(c.price)} ${sgn(c.change_pct)}%</span></h2><div class="name">${esc(c.name)}</div></div>
    <div class="box note">The full detail view is published online for the top 30 stocks, the Top 5 candidates and your watchlist. Open this stock in the app on your PC for everything.</div>
    ${pumpSection(c)}<div class="box"><h3>Buy-side checklist — ${c.bull} positive · ${c.bear} negative · ${c.caution} caution</h3>
    ${c.checklist.map(k => `<div class="check"><span class="ic ${k.signal}">${{ bull: "↑", bear: "↓", caution: "!", neutral: "·" }[k.signal]}</span><span>${esc(k.label)}</span><span class="v">${esc(k.value)}</span></div>`).join("")}</div>`; return; }
  const hist = d.history.filter(h => h.actual_ret != null);
  const hits = hist.filter(h => (h.p_big >= .5) === !!h.big).length;
  body.innerHTML = `
    <div class="d-head"><h2>${c.symbol} <span class="chg ${cls(c.change_pct)}" style="font-size:16px">${money(c.price)} ${sgn(c.change_pct)}%</span></h2>
      <div class="name">${esc(c.name)} · ${esc(c.sector || "")} · ${big(c.market_cap)}</div><div class="tags" style="margin-top:8px">${tags(c)}</div></div>

    <div class="box"><h3>The numbers</h3><div class="kpis">
      <div class="kpi"><div class="k">Big-move chance (≥${SNAP.big_move_pct}%)</div><div class="v">${pct(c.p_big, 0)}</div><div class="n">${c.lift.toFixed(1)}× average stock</div></div>
      <div class="kpi"><div class="k">Options-implied move</div><div class="v">${im ? `±${im.pct.toFixed(1)}%` : "—"}</div><div class="n">${im ? `straddle to ${im.expiry}` : "no options data"}</div></div>
      <div class="kpi"><div class="k">Normal daily range</div><div class="v">${c.atr_pct?.toFixed(1)}%</div><div class="n">14-day ATR</div></div>
      <div class="kpi"><div class="k">Direction lean</div><div class="v ${c.dir_edge ? cls(c.p_up - .5) : ""}">${c.p_up >= .5 ? "Up" : "Down"} ${pct(Math.max(c.p_up, 1 - c.p_up), 0)}</div><div class="n">${c.dir_edge ? "model beat baseline in testing" : "no proven edge — treat as 50/50"}</div></div>
      <div class="kpi"><div class="k">Today</div><div class="v ${cls(c.change_pct)}">${sgn(c.change_pct)}%</div><div class="n">gap ${sgn(c.gap_pct)}% · ${c.rel_volume?.toFixed(1)}× vol</div></div>
      <div class="kpi"><div class="k">Range today</div><div class="v" style="font-size:15px">${money(c.day_low)}–${money(c.day_high)}</div><div class="n">prev close ${money(c.prev_close)}</div></div>
    </div>
    ${im ? `<p class="note" style="margin:10px 0 0">The options market is pricing a ±${im.pct.toFixed(1)}% move by ${im.expiry}. ${im.pct >= SNAP.big_move_pct ? "That agrees the stock could move big." : "That's below the big-move line, so traders expect a calmer stock."}</p>` : ""}
    </div>

    ${c.data_warning ? `<div class="box warnbox">⚠ ${esc(c.data_warning)}</div>` : ""}

    <div class="box"><h3>Price — last 12 months</h3>${priceChart(d.chart)}</div>

    <div class="box"><h3>Why it moved today</h3>
      <p class="note" style="margin:0 0 8px"><b style="color:var(--text)">${esc(c.why.kind)}.</b> The stock moved ${sgn(c.why.ret)}%, while ${c.why.sector_etf ? `its sector (${c.why.sector_etf}) moved ${sgn(c.why.sector_ret)}% and ` : ""}the S&P 500 moved ${sgn(c.why.mkt_ret)}%.
      That leaves ${sgn(c.why.excess)}% the market doesn't explain. Volume is ${c.why.rel_volume.toFixed(1)}× normal${c.why.rel_volume >= 2 ? ", so the move has heavy participation" : ""}.</p>
      ${d.news.length ? `<div class="sec-t" style="margin-top:10px">Headlines from the last 4 days (possible catalysts)</div>${d.news.map(n => `<a class="newsitem" href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}<small>${esc(n.publisher)} · ${ago(n.time)}</small></a>`).join("")}` : `<p class="note">No company-specific headlines in the last 4 days.</p>`}
    </div>

    ${d.plan ? `<div class="box" style="padding:0;border:0;background:none">${planBlock(d.plan, c, true)}</div>` : ""}

    ${pumpSection(c)}

    <div class="box"><h3>Buy-side checklist — ${c.bull} positive · ${c.bear} negative · ${c.caution} caution</h3>
      ${c.checklist.map(k => `<div class="check"><span class="ic ${k.signal}">${{ bull: "↑", bear: "↓", caution: "!", neutral: "·" }[k.signal]}</span><span>${esc(k.label)}</span><span class="v">${esc(k.value)}</span></div>`).join("")}
      <p class="note" style="margin:10px 0 0">Every line is a measured fact (trend, relative strength, analyst numbers, valuation, growth, short interest, event risk). The count shows how the evidence leans; it isn't a buy order.</p>
    </div>

    <div class="box"><h3>What's pushing the big-move odds (this stock, today)</h3>
      ${impactBars(d.all_drivers.slice(0, 12), "impact")}
      <p class="note" style="margin:8px 0 0">Bars to the right raise the chance of a big move; bars to the left lower it. Each one is the model's learned weight × how unusual this stock's reading is.</p>
    </div>

    <div class="box"><h3>This stock's prediction history</h3>
      ${hist.length ? `<p class="note" style="margin:0 0 8px">${hits}/${hist.length} graded calls correct at the 50% line.</p>` : `<p class="note">No graded calls yet. Each prediction is graded after the next session closes.</p>`}
      ${d.history.length ? `<table><tr><th>Date</th><th>Predicted chance</th><th>Next-day move</th><th>Result</th></tr>
      ${d.history.slice(0, 15).map(h => `<tr><td>${h.date}</td><td>${pct(h.p_big, 0)}</td><td class="${cls(h.actual_ret)}">${h.actual_ret == null ? "pending" : sgn(h.actual_ret) + "%"}</td>
        <td>${h.actual_ret == null ? "—" : h.big ? "Big move" : "Normal"}</td></tr>`).join("")}</table>` : ""}
    </div>

    <div class="box"><h3>Fundamentals snapshot</h3><div class="kpis">
      <div class="kpi"><div class="k">Trailing P/E</div><div class="v">${i.trailingPE?.toFixed(1) ?? "—"}</div></div>
      <div class="kpi"><div class="k">Forward P/E</div><div class="v">${i.forwardPE?.toFixed(1) ?? "—"}</div></div>
      <div class="kpi"><div class="k">Beta</div><div class="v">${i.beta?.toFixed(2) ?? "—"}</div></div>
      <div class="kpi"><div class="k">Profit margin</div><div class="v">${i.profitMargins != null ? pct(i.profitMargins) : "—"}</div></div>
      <div class="kpi"><div class="k">52-week range</div><div class="v" style="font-size:14px">${money(i.fiftyTwoWeekLow)}–${money(i.fiftyTwoWeekHigh)}</div></div>
      <div class="kpi"><div class="k">Next earnings</div><div class="v" style="font-size:14px">${c.next_earnings ? new Date(c.next_earnings).toLocaleDateString() : "—"}</div></div>
    </div></div>`;
}

function pumpSection(c) {
  const p = c.pump, st = SNAP.spike_stats;
  if (!p) return "";
  const col = { high: "var(--down)", elevated: "var(--warn)", low: "var(--up)" }[p.level];
  return `<div class="box"><h3>Pump-and-dump check — <span style="color:${col}">${p.level} risk (${p.score}/100)</span></h3>
    ${p.flags.length ? p.flags.map(f => `<div class="check"><span class="ic ${p.level === "low" ? "neutral" : p.level === "high" ? "bear" : "caution"}">!</span><span>${esc(f.label)}</span><span class="v">${esc(f.value)}</span></div>`).join("")
      : `<p class="note" style="margin:0">None of the warning signs apply.</p>`}
    <p class="note" style="margin:10px 0 0">Checks follow the SEC's published pump-and-dump warning signs: tiny company, penny price, sudden price and volume spikes, the whole float trading in a day,
      a very small float, little real business, no analysts or institutions, recent reverse splits, big moves with no news, and earlier spike-and-crash patterns. Companies over $10B are too big to pump, so they're capped at low risk.</p>
    ${st ? `<p class="note" style="margin:8px 0 0"><b style="color:var(--text)">What happened after past spikes in our data</b> (+40% in 5 days on 3× volume, under $20; ${st.n} cases):
      median 20-day return afterwards ${sgn(st.median_20d, 1)}%, ${pct(st.pct_lost_any, 0)} were lower 20 days later, ${pct(st.pct_lost_30, 0)} lost 30% or more.
      ${st.n < 50 ? "The sample is still small. It grows every day the app runs." : ""}</p>` : ""}
  </div>`;
}

// Past locked lists and what each stock did in the session it was picked for.
function picksRecord(limit) {
  const rows = SNAP?.picks_history || [];
  const by = {};
  rows.forEach(r => (by[r.session] ||= []).push(r));
  const sessions = Object.keys(by).sort().reverse().slice(0, limit);
  const graded = rows.filter(r => r.big != null), hits = graded.filter(r => r.big).length;
  return { by, sessions, graded, hits };
}

function picksTable(rec) {
  return `<table><tr><th>Session</th><th>Picks → next-session move</th><th>Moved ≥${SNAP.big_move_pct}%</th></tr>
    ${rec.sessions.map(s => { const g = rec.by[s].filter(r => r.big != null);
      return `<tr><td>${fmtDay(s)}</td><td style="white-space:normal">${rec.by[s].map(r => `${r.symbol} ${r.actual_ret == null ? `<span class="muted">pending</span>` : `<span class="${cls(r.actual_ret)}">${sgn(r.actual_ret, 1)}%</span>`}`).join(" · ")}</td>
      <td>${g.length ? `${g.filter(r => r.big).length}/${g.length}` : "—"}</td></tr>`; }).join("")}</table>`;
}

function prevPicks() {
  const rec = picksRecord(5);
  if (rec.sessions.length < 2 && !rec.graded.length) return "";
  return `<div class="box prevpicks" style="grid-column:1/-1"><h3>Recent locked picks — how they did</h3>${picksTable(rec)}
    <p class="note" style="margin:8px 0 0">${rec.graded.length ? `${rec.hits} of ${rec.graded.length} graded picks moved ${SNAP.big_move_pct}%+ (either direction).` : "Picks are graded after the session they were picked for closes."} Full record is under Track record &amp; learning.</p></div>`;
}

// ---------------------------------------------------------------- performance
function lineChart(rows, keys, colors, fmt) {
  if (rows.length < 2) return `<p class="note">Needs at least two graded days. Check back after the next sessions close.</p>`;
  const W = 700, H = 180, p = 24;
  const vals = rows.flatMap(r => keys.map(k => r[k])).filter(v => v != null);
  const hi = Math.max(...vals, 0.01), x = i => p + i * (W - 2 * p) / (rows.length - 1), y = v => H - p - v / hi * (H - 2 * p);
  const paths = keys.map((k, j) => `<path d="${rows.map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r[k] ?? 0)}`).join("")}" fill="none" stroke="${colors[j]}" stroke-width="2"/>`).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}"><line x1="${p}" x2="${W - p}" y1="${H - p}" y2="${H - p}" stroke="var(--line-2)"/>
    <text x="${p}" y="12" fill="var(--muted)" font-size="11">${fmt(hi)}</text>${paths}
    <text x="${p}" y="${H - 6}" fill="var(--muted)" font-size="11">${rows[0].date}</text><text x="${W - p}" y="${H - 6}" text-anchor="end" fill="var(--muted)" font-size="11">${rows.at(-1).date}</text></svg></div>`;
}

async function renderPerf() {
  const el = $("#perf");
  el.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  const d = await api("/api/performance");
  const m = d.model;
  if (!m) { el.innerHTML = `<div class="empty">The model hasn't been trained yet.</div>`; return; }
  const bs = m.big_stats, ds = m.dir_stats, live = d.n > 0;
  el.innerHTML = `
    <div class="box"><h3>How it learns</h3><p class="note" style="margin:0">Every refresh logs a prediction for each stock. After the next session closes, each call is graded against what actually happened.
      The model then retrains on two years of history plus the new outcomes. It re-tests how heavily to weight recent days, and gives calls it got <i>badly</i> wrong
      (off by more than 60 points) ${3}× weight, so it corrects for them. Everything below is measured, not assumed.</p></div>

    <div class="box"><h3>Out-of-sample test — the ${20} most recent trading days, hidden from training</h3><div class="kpis">
      <div class="kpi"><div class="k">Ranking skill (AUC)</div><div class="v">${bs.holdout_auc.toFixed(3)}</div><div class="n">0.5 = random · 1.0 = perfect</div></div>
      <div class="kpi"><div class="k">Prediction error (log-loss)</div><div class="v">${bs.holdout_logloss.toFixed(3)}</div><div class="n">vs ${bs.baseline_logloss.toFixed(3)} guessing base rate (lower wins)</div></div>
      <div class="kpi"><div class="k">Base rate of big moves</div><div class="v">${pct(bs.base_rate)}</div><div class="n">of stock-days move ≥${d.big_move_pct}%</div></div>
      <div class="kpi"><div class="k">Direction accuracy</div><div class="v" style="color:${ds.holdout_acc > ds.baseline_acc + .01 ? "var(--up)" : "var(--warn)"}">${pct(ds.holdout_acc)}</div><div class="n">vs ${pct(ds.baseline_acc)} always picking the majority direction</div></div>
      <div class="kpi"><div class="k">Recency half-life chosen</div><div class="v">${bs.half_life ?? "∞"}</div><div class="n">days (re-picked at every retrain)</div></div>
      <div class="kpi"><div class="k">Badly-wrong calls upweighted</div><div class="v">${m.misses_upweighted}</div><div class="n">in the latest retrain</div></div>
    </div></div>

    <div class="box"><h3>Live track record — ${d.n.toLocaleString()} graded predictions</h3>
      ${live ? `<div class="kpis">
        <div class="kpi"><div class="k">Top-10 daily picks that moved big</div><div class="v">${pct(d.top10_hit, 0)}</div><div class="n">vs ${pct(d.actual_rate, 0)} for all stocks</div></div>
        <div class="kpi"><div class="k">Brier score</div><div class="v">${d.brier.toFixed(3)}</div><div class="n">vs ${d.brier_baseline.toFixed(3)} baseline (lower wins)</div></div>
        <div class="kpi"><div class="k">Live AUC</div><div class="v">${d.auc?.toFixed(3) ?? "—"}</div></div>
        <div class="kpi"><div class="k">Direction right on big moves</div><div class="v">${pct(d.dir_acc_on_big, 0)}</div><div class="n">${d.n_big} big moves graded</div></div>
      </div>
      <div class="sec-t" style="margin-top:14px">Daily: hit rate of top-10 picks (purple) vs all stocks (grey)</div>
      ${lineChart(d.daily, ["top10_hit", "actual_rate"], ["var(--accent-2)", "var(--faint)"], v => pct(v, 0))}
      <div class="sec-t" style="margin-top:14px">Calibration — when it says X%, does X% happen?</div>
      <table><tr><th>Predicted range</th><th>Avg predicted</th><th>Actually moved big</th><th>Calls</th></tr>
        ${d.calibration.map(b => `<tr><td>${b.range}</td><td>${pct(b.predicted, 0)}</td><td>${pct(b.actual, 0)}</td><td>${b.n}</td></tr>`).join("")}</table>`
      : `<p class="note">Predictions are graded after each next session closes. The first live results appear after the next market close.</p>`}
    </div>

    <div class="box"><h3>What the big-move model has learned (weights)</h3>${impactBars(d.weights, "w")}
      <p class="note" style="margin:8px 0 0">Positive = raises the chance of a big move. These are refit from the data at every retrain.</p></div>
    <div class="box"><h3>What the direction model has learned</h3>${impactBars(d.dir_weights, "w")}</div>

    ${d.misses?.length ? `<div class="box"><h3>Recent calls that were completely off (fed back with extra weight)</h3>
      <table><tr><th>Date</th><th>Stock</th><th>Predicted</th><th>Actual move</th></tr>
      ${d.misses.map(x => `<tr><td>${x.date}</td><td>${x.symbol}</td><td>${pct(x.p_big, 0)}</td><td class="${cls(x.actual_ret)}">${sgn(x.actual_ret)}%</td></tr>`).join("")}</table></div>` : ""}

    ${(() => { const rec = picksRecord(30); return rec.sessions.length ? `<div class="box"><h3>Locked picks record</h3>
      ${rec.graded.length ? `<p class="note" style="margin:0 0 8px">${rec.hits} of ${rec.graded.length} locked picks moved ${SNAP.big_move_pct}%+ in their session (${pct(rec.hits / rec.graded.length, 0)}), vs ${pct(d.actual_rate ?? m.big_stats.base_rate, 0)} for all stocks.</p>` : ""}
      ${picksTable(rec)}</div>` : ""; })()}

    <div class="box"><h3>Retraining log</h3><table><tr><th>Run</th><th>Data through</th><th>Rows</th><th>AUC</th><th>Half-life</th><th>Dir acc</th></tr>
      ${d.runs.map(r => `<tr><td>${r.run_at.slice(0, 16).replace("T", " ")}</td><td>${r.trained_through}</td><td>${r.n_train.toLocaleString()}</td><td>${r.holdout_auc?.toFixed(3)}</td><td>${r.half_life ?? "∞"}</td><td>${pct(r.dir_holdout_acc)}</td></tr>`).join("")}</table></div>`;
}

// ---------------------------------------------------------------- wiring
async function load() {
  let r;
  try { r = await api("/api/snapshot"); }
  catch (e) {
    if (!SNAP) $("#grid").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    clearTimeout(POLL); POLL = setTimeout(load, 60000); return;
  }
  renderStatus(r.status);
  const fresh = r.snapshot && (!SNAP || r.snapshot.generated_at !== SNAP.generated_at);
  if (fresh) { SNAP = r.snapshot; renderHeader(); renderGrid(); }
  if (!SNAP) renderGrid();
  clearTimeout(POLL);
  POLL = setTimeout(load, REMOTE ? 300000 : r.status.running ? 2500 : 60000);
}

if (REMOTE) {
  $("#addForm").classList.add("hidden");
  const b = $("#refreshBtn");
  b.textContent = "Reload"; b.title = "Fetch the latest upload from your PC";
  b.insertAdjacentHTML("afterend", `<a class="btn ghost" href="/logout">Log out</a>`);
}
$("#refreshBtn").onclick = REMOTE ? () => load() : async () => { await api("/api/refresh", { method: "POST" }); load(); };
$("#modes").onclick = e => {
  const b = e.target.closest(".chip"); if (!b) return;
  MODE = b.dataset.m;
  document.querySelectorAll("#modes .chip").forEach(x => x.classList.toggle("active", x === b));
  $("#allControls").classList.toggle("hidden", MODE !== "all");
  $("#riskBox").classList.toggle("hidden", MODE !== "picks");
  $("#topHead .criteria").classList.toggle("hidden", MODE === "all");
  renderGrid();
};
$("#risk").value = RISK;
$("#risk").oninput = e => {
  RISK = Math.max(+e.target.value || 0, 0);
  try { localStorage.setItem("risk", RISK); } catch {}
  renderGrid();
};
$("#search").oninput = renderGrid;
$("#sort").onchange = renderGrid;
$("#hidePump").onchange = renderGrid;
$("#minP").oninput = e => { $("#minPv").textContent = e.target.value + "%"; renderGrid(); };
$("#filters").onclick = e => {
  const b = e.target.closest(".chip"); if (!b) return;
  FILTER = b.dataset.f; document.querySelectorAll("#filters .chip").forEach(x => x.classList.toggle("active", x === b)); renderGrid();
};
$("#addForm").onsubmit = async e => {
  e.preventDefault();
  const s = $("#addSym").value.trim().toUpperCase(); if (!s) return;
  await api(`/api/watchlist/${encodeURIComponent(s)}`, { method: "POST" });
  $("#addSym").value = "";
  await api("/api/refresh", { method: "POST" }); load();
};
$("#grid").onclick = async e => {
  if (e.target.closest("[data-stop]")) return;
  const st = e.target.closest("[data-star]");
  if (st) {
    const s = st.dataset.star, c = SNAP.cards.find(x => x.symbol === s);
    await api(`/api/watchlist/${s}`, { method: c.watch ? "DELETE" : "POST" });
    c.watch = !c.watch; renderGrid(); return;
  }
  const cd = e.target.closest(".card"); if (cd) openDetail(cd.dataset.sym);
};
const closeDrawer = () => { $("#drawer").classList.add("hidden"); history.replaceState(null, "", location.pathname); };
document.querySelectorAll("[data-close]").forEach(x => x.onclick = closeDrawer);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  $("#screener").classList.toggle("hidden", t.dataset.tab !== "screener");
  $("#perf").classList.toggle("hidden", t.dataset.tab !== "perf");
  if (t.dataset.tab === "perf") renderPerf();
});
load().then(() => {
  const h = location.hash;
  if (h.startsWith("#stock=")) openDetail(decodeURIComponent(h.slice(7)));
  else if (h === "#perf") $('.tab[data-tab="perf"]').click();
});
