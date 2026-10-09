"use client";
import Link from "next/link";
import { useState } from "react";
import type { Card } from "@/lib/api";
import { post } from "@/lib/api";
import { cap, cls, et, num, pct, usd } from "@/lib/format";
import { FreshnessTag, RiskBadge, ScoreBar, StatusBadge } from "./ui";
import NewsList from "./NewsList";

const COMP_LABELS: Record<string, string> = {
  technical: "Technical setup",
  trend_rs: "Trend & rel. strength",
  entry: "Entry quality",
  volume_liquidity: "Volume & liquidity",
  news: "News & catalysts",
  market_alignment: "Market & sector",
  sentiment: "Sentiment quality",
};

export function SetupTable({ cards, showRank = true }: { cards: Card[]; showRank?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="overflow-x-auto">
      <table className="tbl min-w-[980px]">
        <thead>
          <tr>
            {showRank && <th>#</th>}
            <th>Stock</th>
            <th>Status</th>
            <th>Price</th>
            <th>Entry zone</th>
            <th>Trigger</th>
            <th>Stop</th>
            <th>Targets</th>
            <th>R:R</th>
            <th>Up / down</th>
            <th>Score</th>
            <th>Risk</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((c) => {
            const k = `${c.symbol}-${c.style}`;
            const p = c.plan;
            return (
              <FragmentRow key={k} open={open === k}>
                <tr className="cursor-pointer hover:bg-panel2" onClick={() => setOpen(open === k ? null : k)}>
                  {showRank && <td className="num text-mute">{c.rank ?? "–"}</td>}
                  <td>
                    <div className="font-semibold">{c.symbol}</div>
                    <div className="max-w-[200px] truncate text-xs text-mute">{c.setup_type || c.technical_state.replace(/_/g, " ")}</div>
                  </td>
                  <td><StatusBadge status={c.status} /></td>
                  <td className="num">
                    {usd(c.price)}
                    <div className="text-[11px] text-mute">{et(c.price_time, false)} <FreshnessTag f={c.freshness} /></div>
                  </td>
                  <td className="num">{p ? `${num(p.entry_zone_low)}–${num(p.entry_zone_high)}` : "—"}</td>
                  <td className="num">{p ? usd(p.trigger_price) : "—"}</td>
                  <td className="num text-down">{p ? usd(p.stop) : "—"}</td>
                  <td className="num text-up">{p ? `${num(p.target1)}${p.target2 ? ` / ${num(p.target2)}` : ""}` : "—"}</td>
                  <td className={cls("num", p && (p.reward_risk ?? 0) < 2 ? "text-warn" : "")}>{p?.reward_risk ? `${p.reward_risk.toFixed(2)}` : "—"}</td>
                  <td className="num whitespace-nowrap text-xs">
                    {p ? (
                      <>
                        <span className="text-up">{pct(p.gain_pct_t1)}</span> / <span className="text-down">{pct(p.loss_pct)}</span>
                      </>
                    ) : "—"}
                  </td>
                  <td className="w-24">
                    <div className="num text-xs">{c.score.score.toFixed(0)}</div>
                    <ScoreBar value={c.score.score} />
                  </td>
                  <td>
                    <RiskBadge m={c.manipulation} />
                    {c.events?.next_earnings && <div className="mt-1 text-[11px] text-warn">ER {c.events.next_earnings.date}</div>}
                  </td>
                </tr>
                {open === k && (
                  <tr>
                    <td colSpan={showRank ? 12 : 11} className="bg-bg/60 p-0">
                      <SetupDetail card={c} />
                    </td>
                  </tr>
                )}
              </FragmentRow>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FragmentRow({ children }: { children: React.ReactNode; open: boolean }) {
  return <>{children}</>;
}

export function SetupDetail({ card: c, compact = false }: { card: Card; compact?: boolean }) {
  const [msg, setMsg] = useState<string | null>(null);
  const p = c.plan;
  const ps = c.position_size;
  const watch = async () => {
    try {
      await post("/watchlist", { symbol: c.symbol, style: c.style });
      setMsg("Added to watchlist — alerts will track the entry zone, trigger, stop and targets.");
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  const paper = async () => {
    try {
      const r = await post("/paper/from-setup", { symbol: c.symbol, style: c.style });
      setMsg(`Paper trade recorded: ${r.quantity} shares at ${usd(r.entry_price)} (${et(r.entry_time)}). No real order was sent.`);
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  const ind = c.metrics || {};
  return (
    <div className="grid gap-4 p-4 lg:grid-cols-3">
      <div className="space-y-3 lg:col-span-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-base font-semibold">{c.symbol}</span>
          <span className="text-mute">{c.name}</span>
          <span className="text-xs text-mute">· {c.exchange} · {c.sector ?? "sector n/a"} · {cap(c.market_cap)} {c.cap_category ? `(${c.cap_category}-cap)` : ""}</span>
          <span className="rounded border border-line px-1.5 text-xs capitalize">{c.style}</span>
          <StatusBadge status={c.status} />
        </div>
        <div className="text-xs text-mute">
          Price {usd(c.price)} at {et(c.price_time)} · <FreshnessTag f={c.freshness} /> — {c.freshness?.note}
          {c.session_note && <div className="text-warn">{c.session_note}</div>}
        </div>
        {p ? (
          <div className="rounded-md border border-line bg-panel2 p-3 text-sm">
            <div className="mb-1 text-xs uppercase tracking-wider text-mute">Conditional plan</div>
            <p>{p.conditional_text}</p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <Kv k="Entry zone" v={`${usd(p.entry_zone_low)} – ${usd(p.entry_zone_high)}`} />
              <Kv k="Confirmation" v={usd(p.trigger_price)} />
              <Kv k="Stop / invalidation" v={usd(p.stop)} cls="text-down" />
              <Kv k="Risk / share" v={usd(p.risk_per_share)} />
              <Kv k="Target 1" v={`${usd(p.target1)} (${pct(p.gain_pct_t1)})`} cls="text-up" />
              <Kv k="Target 2" v={p.target2 ? `${usd(p.target2)} (${pct(p.gain_pct_t2)})` : "—"} cls="text-up" />
              <Kv k="Reward : risk" v={p.reward_risk ? `${p.reward_risk.toFixed(2)} : 1 (T2 ${p.reward_risk_t2?.toFixed(2) ?? "—"})` : "invalid"} />
              <Kv k="Loss to stop" v={pct(p.loss_pct)} cls="text-down" />
            </div>
            <ul className="mt-2 space-y-0.5 text-xs text-mute">
              <li>Trigger: {p.trigger_text}</li>
              <li>Stop: {p.stop_text}</li>
              <li>T1: {p.target1_text}{p.target2_text ? ` · T2: ${p.target2_text}` : ""}</li>
            </ul>
          </div>
        ) : (
          <div className="rounded-md border border-line bg-panel2 p-3 text-sm text-mute">No valid trade plan.</div>
        )}
        {(c.warnings.length > 0 || c.reasons_avoid.length > 0) && (
          <div className="space-y-1 text-xs">
            {c.reasons_avoid.map((r, i) => <div key={`a${i}`} className="text-down">✕ {r}</div>)}
            {c.warnings.map((w, i) => <div key={`w${i}`} className="text-warn">! {w}</div>)}
          </div>
        )}
        {!compact && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Section title="1 · Why it qualifies" items={c.explanation.why_qualifies} />
            <Section title="2 · What makes the entry attractive" items={c.explanation.entry_attractive} />
            <Section title="3 · What confirms the trade" items={c.explanation.confirmation} />
            <Section title="4 · What could make it fail" items={c.explanation.failure_risks} />
            <Section title="5 · News & event risks" items={c.explanation.news_event_risks} />
            <Section title="6 · Ranking" items={[c.explanation.ranking_note || (c.qualifies ? "Qualifies — its rank against other candidates is shown in the scan list." : "Not ranked: does not meet the ranking requirements.")]} />
          </div>
        )}
        {!compact && c.news?.length > 0 && (
          <div>
            <div className="mb-1 text-xs uppercase tracking-wider text-mute">News (source-linked)</div>
            <NewsList items={c.news.slice(0, 5)} />
          </div>
        )}
      </div>
      <div className="space-y-3">
        <div className="rounded-md border border-line p-3">
          <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wider text-mute">
            <span>Setup-quality score</span>
            <span className="num text-ink">{c.score.score.toFixed(1)} / 100</span>
          </div>
          {Object.entries(c.score.components).map(([k, v]) => (
            <div key={k} className="mb-1.5">
              <div className="flex justify-between text-xs"><span className="text-mute">{COMP_LABELS[k]} <span className="text-[10px]">({c.score.weights[k]}%)</span></span><span className="num">{v.toFixed(0)}</span></div>
              <ScoreBar value={v} />
            </div>
          ))}
          {c.score.penalties.map((p, i) => <div key={i} className="text-xs text-warn">{p.label}: {p.points}</div>)}
          <p className="mt-2 text-[11px] text-mute">A rules-based quality rating, not a probability of profit. Needs ≥ {c.score.threshold} and technical ≥ 50 to rank.</p>
        </div>
        <div className="rounded-md border border-line p-3 text-xs">
          <div className="mb-1 uppercase tracking-wider text-mute">Indicators</div>
          <div className="grid grid-cols-2 gap-1">
            {ind.rsi !== undefined && <Kv k="RSI(14)" v={num(ind.rsi, 1)} />}
            {ind.atr_pct !== undefined && <Kv k="ATR %" v={pct(ind.atr_pct, 2, false)} />}
            {ind.rel_volume !== undefined && <Kv k="Rel. volume" v={`${num(ind.rel_volume, 2)}x`} />}
            {ind.relative_volume_est !== undefined && <Kv k="Rel. volume (est.)" v={`${num(ind.relative_volume_est, 2)}x`} />}
            {ind.pullback_depth_pct !== undefined && <Kv k="Pullback depth" v={pct(ind.pullback_depth_pct, 1, false)} />}
            {ind.rs_market_63 !== undefined && <Kv k="RS vs S&P (3m)" v={`${num(ind.rs_market_63, 1)} pts`} />}
            {ind.rs_sector_63 !== undefined && <Kv k="RS vs sector" v={`${num(ind.rs_sector_63, 1)} pts`} />}
            {ind.sma20 !== undefined && <Kv k="SMA 20/50" v={`${num(ind.sma20)} / ${num(ind.sma50)}`} />}
            {ind.vwap !== undefined && <Kv k="VWAP" v={usd(ind.vwap)} />}
            {ind.opening_range_high !== undefined && <Kv k="Opening range" v={`${num(ind.opening_range_low)}–${num(ind.opening_range_high)}`} />}
            {ind.dollar_volume_20d !== undefined && <Kv k="Avg $ volume" v={`$${num((ind.dollar_volume_20d || 0) / 1e6, 1)}M`} />}
            {ind.gap_pct !== undefined && <Kv k="Gap" v={pct(ind.gap_pct)} />}
          </div>
        </div>
        <div className="rounded-md border border-line p-3 text-xs">
          <div className="mb-1 flex justify-between uppercase tracking-wider text-mute"><span>Manipulation risk</span><RiskBadge m={c.manipulation} /></div>
          <div className="text-mute">{c.manipulation.label}</div>
          {c.manipulation.flags.slice(0, 4).map((f, i) => <div key={i}>• {f.label}: <span className="text-mute">{f.value}</span></div>)}
          {c.manipulation.unknown?.length > 0 && <div className="mt-1 text-mute">Not checked: {c.manipulation.unknown.join(", ")}</div>}
        </div>
        {ps?.valid && (
          <div className="rounded-md border border-line p-3 text-xs">
            <div className="mb-1 uppercase tracking-wider text-mute">Position size (your risk settings)</div>
            <div className="grid grid-cols-2 gap-1">
              <Kv k="Shares" v={num(ps.shares, 0)} />
              <Kv k="Position" v={usd(ps.position_value, 0)} />
              <Kv k="Planned risk" v={`${usd(ps.total_planned_risk)} (${ps.planned_risk_pct}%)`} />
              <Kv k="Limited by" v={ps.limited_by} />
            </div>
            {ps.warnings.map((w: string, i: number) => <div key={i} className="mt-1 text-warn">! {w}</div>)}
            <div className="mt-1 text-mute">{ps.notes[0]}</div>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button className="btn" onClick={watch}>+ Watchlist</button>
          <button className="btn" onClick={paper} disabled={c.status !== "Triggered"} title={c.status !== "Triggered" ? "Paper entries require the confirmation trigger" : ""}>Paper trade</button>
          <Link className="btn" href={`/research/${c.symbol}?style=${c.style}`}>Research →</Link>
        </div>
        {msg && <div className="text-xs text-info">{msg}</div>}
        <div className="text-[11px] text-mute">Sources: prices {c.sources?.prices}; reference {c.sources?.reference}; news {(c.sources?.news || []).join(", ") || "none"}.</div>
      </div>
    </div>
  );
}

function Kv({ k, v, cls: c }: { k: string; v: React.ReactNode; cls?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-mute">{k}</div>
      <div className={cls("num", c)}>{v}</div>
    </div>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-line p-3">
      <div className="mb-1 text-xs uppercase tracking-wider text-mute">{title}</div>
      <ul className="space-y-1 text-xs">
        {items.map((x, i) => <li key={i}>{x}</li>)}
      </ul>
    </div>
  );
}
