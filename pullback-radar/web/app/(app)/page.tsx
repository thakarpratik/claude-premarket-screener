"use client";
import Link from "next/link";
import { useScanData } from "@/lib/useScan";
import type { Card } from "@/lib/api";
import { cls, et, num, pct, usd } from "@/lib/format";
import { Empty, ErrorBox, Loading, RegimePill, RiskBadge, StatusBadge } from "@/components/ui";
import NewsList from "@/components/NewsList";

const PRIORITY: Record<string, number> = { Triggered: 0, "Approaching Entry": 1, Watch: 2 };

export default function Overview() {
  const { data, error, loading } = useScanData("/scan/latest");
  if (loading && !data) return <Loading label="Scanning the market…" />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const m = data.market;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="panel lg:col-span-2">
          <div className="panel-h">
            <span>1 · What is the market doing?</span>
            <RegimePill regime={m.regime.regime} />
          </div>
          <div className="space-y-3 p-4">
            <p className="text-sm">{m.regime.guidance}</p>
            <p className="text-xs text-mute">{m.conditions} Benchmarks as of {et(m.as_of)}.</p>
            <div className="overflow-x-auto">
              <table className="tbl">
                <thead><tr><th>Index</th><th>Last</th><th>1D</th><th>5D</th><th>20D</th><th>50-day</th><th>200-day</th><th>RSI</th></tr></thead>
                <tbody>
                  {m.indices.map((r: any) => (
                    <tr key={r.symbol}>
                      <td><span className="font-semibold">{r.symbol}</span> <span className="text-xs text-mute">{r.name}</span></td>
                      {r.unavailable ? <td colSpan={7} className="text-mute">unavailable</td> : (<>
                        <td className="num">{usd(r.close)}</td>
                        <Chg v={r.change_1d} /><Chg v={r.change_5d} /><Chg v={r.change_20d} />
                        <td className={r.above_50 ? "text-up" : "text-down"}>{r.above_50 ? "above" : "below"}{r.sma50_rising === false ? " (falling)" : ""}</td>
                        <td className={r.above_200 ? "text-up" : "text-down"}>{r.above_200 === null ? "—" : r.above_200 ? "above" : "below"}</td>
                        <td className="num">{num(r.rsi, 0)}</td>
                      </>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="grid gap-x-4 text-xs text-mute sm:grid-cols-2">
              {m.regime.reasons.map((r: string, i: number) => <li key={i}>• {r}</li>)}
            </ul>
          </div>
        </div>
        <div className="panel">
          <div className="panel-h">Conditions</div>
          <div className="space-y-3 p-4 text-sm">
            <div>
              <div className="label">Volatility</div>
              <div><span className="num">{num(m.volatility.spy_realized_20d, 1)}%</span> <span className="capitalize text-mute">({m.volatility.label ?? "n/a"})</span></div>
              <div className="text-[11px] text-mute">{m.volatility.source}</div>
            </div>
            <div>
              <div className="label">Breadth ({m.breadth.scope}, n={m.breadth.sample_size})</div>
              <div className="num">{m.breadth.advancers} adv / {m.breadth.decliners} dec · {num(m.breadth.pct_above_50dma, 0)}% above 50-day</div>
            </div>
            <div>
              <div className="label">Scheduled events</div>
              {m.economic_events.length ? m.economic_events.slice(0, 5).map((e: any, i: number) => (
                <div key={i} className="text-xs">{e.date} {e.time_of_day ?? ""} — {e.description} <span className="text-mute">({e.source})</span></div>
              )) : <div className="text-xs text-mute">None found in configured sources.</div>}
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {(["swing", "intraday"] as const).map((st) => <TopSetups key={st} style={st} block={data[st]} />)}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="panel">
          <div className="panel-h">Sector leadership (20-day)</div>
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>#</th><th>Sector</th><th>5D</th><th>20D</th><th>Trend</th></tr></thead>
              <tbody>
                {m.sectors.map((s: any) => (
                  <tr key={s.etf}>
                    <td className="num text-mute">{s.rank}</td>
                    <td>{s.sector} <span className="text-xs text-mute">{s.etf}</span>{s.leadership !== "neutral" && <span className={cls("ml-2 text-[11px]", s.leadership === "leading" ? "text-up" : "text-down")}>{s.leadership}</span>}</td>
                    <Chg v={s.change_5d} /><Chg v={s.change_20d} />
                    <td className={s.above_50 ? "text-up" : "text-down"}>{s.above_50 ? "above 50-day" : "below 50-day"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel">
          <div className="panel-h">Market news</div>
          <div className="px-4"><NewsList items={m.news} /></div>
        </div>
      </div>
      {data.errors?.length > 0 && (
        <div className="panel p-3 text-xs text-warn">
          {data.errors.length} data issue(s) during the scan:{" "}
          {data.errors.slice(0, 5).map((e: any) => `${e.symbol ?? "market"} ${e.stage}: ${e.error}`).join(" · ")}
        </div>
      )}
      <p className="text-[11px] text-mute">{data.probability_note}</p>
    </div>
  );
}

function Chg({ v }: { v: number | null }) {
  return <td className={cls("num", v === null ? "" : v >= 0 ? "text-up" : "text-down")}>{pct(v, 2)}</td>;
}

function TopSetups({ style, block }: { style: "swing" | "intraday"; block: any }) {
  if (!block) return null;
  const cards: Card[] = [...block.ranked].sort((a, b) => (PRIORITY[a.status] ?? 9) - (PRIORITY[b.status] ?? 9) || b.score.score - a.score.score).slice(0, 6);
  return (
    <div className="panel">
      <div className="panel-h">
        <span>2–7 · {style === "swing" ? "Swing" : "Intraday"} setups closest to actionable</span>
        <Link href={`/${style}`} className="link normal-case tracking-normal">All {block.ranked.length} →</Link>
      </div>
      {cards.length === 0 ? (
        <Empty>{block.message}</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead><tr><th>Stock</th><th>Status</th><th>Confirms above</th><th>Invalid below</th><th>Up / down</th><th>R:R</th><th>Risk</th></tr></thead>
            <tbody>
              {cards.map((c) => (
                <tr key={c.symbol}>
                  <td><Link className="font-semibold hover:underline" href={`/research/${c.symbol}?style=${style}`}>{c.symbol}</Link><div className="max-w-[160px] truncate text-[11px] text-mute">{c.setup_type}</div></td>
                  <td><StatusBadge status={c.status} /></td>
                  <td className="num">{usd(c.plan?.trigger_price)}</td>
                  <td className="num text-down">{usd(c.plan?.stop)}</td>
                  <td className="num whitespace-nowrap text-xs"><span className="text-up">{pct(c.plan?.gain_pct_t1)}</span> / <span className="text-down">{pct(c.plan?.loss_pct)}</span></td>
                  <td className={cls("num", (c.plan?.reward_risk ?? 0) < 2 ? "text-warn" : "")}>{c.plan?.reward_risk?.toFixed(2) ?? "—"}</td>
                  <td className="whitespace-nowrap">
                    <RiskBadge m={c.manipulation} />
                    {c.events?.next_earnings && <span className="ml-1 text-[11px] text-warn">ER</span>}
                    {c.news?.some((n) => n.impact === "high") && <span className="ml-1 text-[11px] text-info">news</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {block.flagged_count > 0 && <div className="border-t border-line px-4 py-2 text-xs text-mute">{block.flagged_count} stock(s) flagged for abnormal activity are kept out of this list — <Link href="/flagged" className="link">review separately</Link>.</div>}
    </div>
  );
}
