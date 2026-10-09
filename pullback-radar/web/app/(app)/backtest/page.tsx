"use client";
import { useEffect, useState } from "react";
import { api, post } from "@/lib/api";
import { cls, et, num } from "@/lib/format";
import { Empty, ErrorBox, Loading } from "@/components/ui";

export default function Backtests() {
  const [runs, setRuns] = useState<any[] | null>(null);
  const [sel, setSel] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ style: "swing", symbols: "", min_reward_risk: 2, max_hold_bars: 10, entry_window_bars: 3, slippage_per_share: 0.02, commission_per_order: 0, in_sample_pct: 70, days_intraday: 10, require_market_uptrend: false });
  const load = () => api("/backtests").then((r) => setRuns(r.runs));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (!runs?.some((r) => r.status === "running")) return;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [runs]);
  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await post("/backtests", { ...f, symbols: f.symbols.split(/[\s,]+/).filter(Boolean) });
      load();
    } catch (x: any) {
      setErr(x.message);
    }
  };
  const open = (id: number) => api(`/backtests/${id}`).then(setSel);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Historical backtests</h1>
      <p className="max-w-3xl text-sm text-mute">Replays the same scanner rules bar by bar with no look-ahead, conservative fills, slippage and commissions, and an in-sample / out-of-sample split. Use it to validate (or reject) the default weights and rules — results are simulated, not actual performance.</p>
      <form onSubmit={start} className="panel grid gap-2 p-4 sm:grid-cols-3 lg:grid-cols-6">
        <div><label className="label">Style</label><select className="input" value={f.style} onChange={(e) => setF({ ...f, style: e.target.value })}><option>swing</option><option>intraday</option></select></div>
        <div className="sm:col-span-2"><label className="label">Symbols (blank = latest scan&apos;s setups)</label><input className="input" value={f.symbols} onChange={(e) => setF({ ...f, symbols: e.target.value.toUpperCase() })} /></div>
        {([["min_reward_risk", "Min R:R"], ["max_hold_bars", "Max hold (sessions)"], ["entry_window_bars", "Entry window (bars)"], ["slippage_per_share", "Slippage / share"], ["commission_per_order", "Commission / order"], ["in_sample_pct", "In-sample %"], ["days_intraday", "Intraday sessions"]] as const).map(([k, l]) => (
          <div key={k}><label className="label">{l}</label><input className="input" type="number" step="any" value={(f as any)[k]} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} /></div>
        ))}
        <label className="flex items-end gap-2 text-sm"><input type="checkbox" checked={f.require_market_uptrend} onChange={(e) => setF({ ...f, require_market_uptrend: e.target.checked })} /> Only in S&amp;P uptrend</label>
        <div className="flex items-end"><button className="btn-primary w-full justify-center">Run backtest</button></div>
        <div className="sm:col-span-6"><ErrorBox error={err} /></div>
      </form>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="panel">
          <div className="panel-h">Runs</div>
          {!runs ? <Loading /> : runs.length === 0 ? <Empty>No backtests yet.</Empty> : (
            <ul className="divide-y divide-line">
              {runs.map((r) => (
                <li key={r.id}><button className="w-full px-4 py-2 text-left text-sm hover:bg-panel2" onClick={() => open(r.id)}>
                  #{r.id} <span className="capitalize">{r.style}</span> · {et(r.created_at)} {r.synthetic && <span className="text-[11px] text-info">synthetic</span>}
                  <div className="text-xs text-mute">{r.status === "running" ? "running…" : r.ok === false ? "failed" : `${r.overall?.trades ?? 0} trades · exp ${num(r.overall?.expectancy_r, 2)}R`}</div>
                </button></li>
              ))}
            </ul>
          )}
        </div>
        <div className="panel lg:col-span-2">
          <div className="panel-h">Result</div>
          {!sel ? <Empty>Select a run.</Empty> : <Result r={sel} />}
        </div>
      </div>
    </div>
  );
}

function Result({ r }: { r: any }) {
  const res = r.result;
  if (res.status === "running") return <Loading label="Running…" />;
  if (!res.ok) return <div className="p-4"><ErrorBox error={res.error || "Backtest failed"} /></div>;
  const rows: [string, any][] = [["All trades", res.overall], ...Object.entries(res.by_period || {}), ...Object.entries(res.by_regime || {}).map(([k, v]) => [`Regime: ${k}`, v] as [string, any])];
  return (
    <div className="space-y-3 p-4">
      {res.warnings.map((w: string, i: number) => <div key={i} className={cls("rounded border px-3 py-1.5 text-sm", w.startsWith("SYNTHETIC") ? "border-info/40 bg-info/10 text-info" : "border-warn/40 bg-warn/10 text-warn")}>{w}</div>)}
      <div className="text-xs text-mute">{res.symbols.join(", ")} · {res.start} → {res.end} · {res.signals} signals</div>
      <div className="overflow-x-auto">
        <table className="tbl">
          <thead><tr><th>Slice</th><th>Trades</th><th>Win %</th><th>Avg win</th><th>Avg loss</th><th>Exp. (R)</th><th>Exp. before costs</th><th>Exp. %</th><th>Profit factor</th><th>Max DD %</th></tr></thead>
          <tbody>
            {rows.map(([k, m]) => (
              <tr key={k}>
                <td className="capitalize">{k.replace(/_/g, " ")}</td>
                {m.trades ? (<>
                  <td className="num">{m.trades}</td><td className="num">{m.win_rate.toFixed(0)}</td>
                  <td className="num">{m.avg_win_r.toFixed(2)}R ({m.avg_win_pct.toFixed(1)}%)</td><td className="num">{m.avg_loss_r.toFixed(2)}R ({m.avg_loss_pct.toFixed(1)}%)</td>
                  <td className={cls("num", m.expectancy_r >= 0 ? "text-up" : "text-down")}>{m.expectancy_r.toFixed(3)}</td><td className="num">{m.expectancy_r_before_costs.toFixed(3)}</td>
                  <td className="num">{m.expectancy_pct.toFixed(2)}</td><td className="num">{m.profit_factor ? m.profit_factor.toFixed(2) : "—"}</td><td className="num">{m.max_drawdown_pct.toFixed(1)}</td>
                </>) : <td colSpan={9} className="text-mute">no trades</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="text-xs"><summary className="cursor-pointer text-mute">Safeguards</summary><ul className="mt-1 space-y-0.5">{res.safeguards.map((s: string, i: number) => <li key={i}>• {s}</li>)}</ul></details>
      <details className="text-xs"><summary className="cursor-pointer text-mute">Trades ({res.trades.length})</summary>
        <table className="tbl mt-1"><thead><tr><th>Symbol</th><th>Signal</th><th>Entry</th><th>Exit</th><th>Reason</th><th>R</th><th>Period</th></tr></thead><tbody>
          {res.trades.slice(0, 300).map((t: any, i: number) => <tr key={i}><td>{t.symbol}</td><td>{et(t.signal_time)}</td><td className="num">{t.entry.toFixed(2)}</td><td className="num">{t.exit.toFixed(2)}</td><td>{t.exit_reason}</td><td className={cls("num", t.r_multiple >= 0 ? "text-up" : "text-down")}>{t.r_multiple.toFixed(2)}</td><td>{t.period}</td></tr>)}
        </tbody></table>
      </details>
    </div>
  );
}
