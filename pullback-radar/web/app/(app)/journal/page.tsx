"use client";
import { useEffect, useState } from "react";
import { api, del, patch, post } from "@/lib/api";
import { cls, et, num, usd } from "@/lib/format";
import { Empty, ErrorBox, Loading } from "@/components/ui";

export default function Journal() {
  const [data, setData] = useState<any>(null);
  const [loss, setLoss] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => {
    api("/trades").then(setData).catch((e) => setErr(e.message));
    api("/risk/daily-loss").then(setLoss).catch(() => null);
  };
  useEffect(load, []);
  if (!data) return <Loading />;
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Risk &amp; trade journal</h1>
      <ErrorBox error={err} />
      {loss && (["actual", "paper"] as const).map((m) => loss[m]?.reached && (
        <div key={m} className="rounded-md border border-down/50 bg-down/10 px-3 py-2 text-sm text-down">{m === "paper" ? "Paper: " : ""}{loss[m].message}</div>
      ))}
      <div className="grid gap-4 lg:grid-cols-3">
        <RiskCalculator />
        <div className="panel lg:col-span-2">
          <div className="panel-h">Performance (paper and actual are never combined)</div>
          <div className="grid gap-4 p-4 md:grid-cols-2">
            {(["actual", "paper"] as const).map((m) => <Stats key={m} mode={m} s={data.stats[m]} loss={loss?.[m]} />)}
          </div>
          <p className="px-4 pb-3 text-[11px] text-mute">{data.stats.note}</p>
        </div>
      </div>
      <TradeForm onSaved={load} />
      <div className="panel overflow-x-auto">
        <div className="panel-h">Trades</div>
        {data.trades.length === 0 ? <Empty>No trades recorded.</Empty> : (
          <table className="tbl min-w-[1000px]">
            <thead><tr><th>Mode</th><th>Stock</th><th>Strategy</th><th>Entry</th><th>Qty</th><th>Stop / T1</th><th>Exit</th><th>P&amp;L</th><th>R</th><th>Notes</th><th></th></tr></thead>
            <tbody>
              {data.trades.map((t: any) => (
                <tr key={t.id}>
                  <td><span className={cls("rounded px-1.5 text-xs", t.mode === "paper" ? "bg-info/15 text-info" : "bg-panel2")}>{t.mode}</span>{t.synthetic_data && <div className="text-[10px] text-info">synthetic prices</div>}</td>
                  <td className="font-semibold">{t.symbol}<div className="text-[11px] font-normal capitalize text-mute">{t.style}</div></td>
                  <td className="text-xs">{t.strategy ?? "—"}</td>
                  <td className="num">{usd(t.entry_price)}<div className="text-[11px] text-mute">{et(t.entry_time)}</div></td>
                  <td className="num">{num(t.quantity, 0)}</td>
                  <td className="num text-xs"><span className="text-down">{usd(t.stop)}</span> / <span className="text-up">{usd(t.target1)}</span></td>
                  <td className="num">{t.exit_price ? usd(t.exit_price) : <CloseBtn t={t} onDone={load} />}<div className="text-[11px] text-mute">{t.exit_reason ?? ""}</div></td>
                  <td className={cls("num", (t.pnl ?? 0) >= 0 ? "text-up" : "text-down")}>{t.pnl === null ? "open" : usd(t.pnl)}</td>
                  <td className="num">{t.r_multiple === null ? "—" : t.r_multiple.toFixed(2)}</td>
                  <td className="max-w-[260px] whitespace-pre-wrap text-xs text-mute">{t.rationale}{t.notes ? `\n${t.notes}` : ""}{t.screenshot && <a className="link block" href={t.screenshot} target="_blank">screenshot</a>}<Upload id={t.id} onDone={load} /></td>
                  <td><button className="text-xs text-down hover:underline" onClick={() => confirm("Delete this trade?") && del(`/trades/${t.id}`).then(load)}>delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Stats({ mode, s, loss }: { mode: string; s: any; loss: any }) {
  const o = s.overall;
  return (
    <div>
      <div className="mb-2 text-sm font-semibold capitalize">{mode} <span className="text-xs font-normal text-mute">({s.open_positions} open)</span></div>
      {o.trades === 0 ? <div className="text-xs text-mute">No closed {mode} trades.</div> : (
        <div className="grid grid-cols-3 gap-2 text-xs">
          <K k="Trades" v={o.trades} /><K k="Win rate" v={`${o.win_rate.toFixed(0)}%`} /><K k="Profit factor" v={o.profit_factor ? o.profit_factor.toFixed(2) : "—"} />
          <K k="Avg win" v={usd(o.avg_win)} /><K k="Avg loss" v={usd(o.avg_loss)} /><K k="Expectancy" v={usd(o.expectancy)} />
          <K k="Net P&L" v={usd(o.net_pnl)} /><K k="Max drawdown" v={usd(o.max_drawdown)} /><K k="Avg R" v={o.avg_r === null ? "—" : o.avg_r.toFixed(2)} />
        </div>
      )}
      {s.by_strategy?.length > 0 && (
        <table className="tbl mt-2 text-xs"><thead><tr><th>Strategy</th><th>n</th><th>Win%</th><th>Exp.</th></tr></thead><tbody>
          {s.by_strategy.map((b: any, i: number) => <tr key={i}><td>{b.style} · {b.strategy}</td><td>{b.trades}</td><td>{b.win_rate.toFixed(0)}</td><td>{usd(b.expectancy)}</td></tr>)}
        </tbody></table>
      )}
      {loss && <div className="mt-2 text-[11px] text-mute">Today: {usd(loss.realized_today)} realized · daily limit {usd(loss.limit)} · {usd(loss.remaining)} remaining</div>}
    </div>
  );
}

const K = ({ k, v }: { k: string; v: React.ReactNode }) => (
  <div><div className="text-[10px] uppercase text-mute">{k}</div><div className="num">{v}</div></div>
);

function RiskCalculator() {
  const [f, setF] = useState({ equity: 25000, risk_pct: 0.5, entry: 50, stop: 48.5, slippage_per_share: 0.02, commission_per_trade: 0, max_position_pct: 25, t1: 54, t2: 56, min_reward_risk: 2 });
  const [r, setR] = useState<any>(null);
  useEffect(() => {
    api("/settings").then(({ settings: s }) => setF((x) => ({ ...x, equity: s.account_equity, risk_pct: s.risk_pct_per_trade, slippage_per_share: s.slippage_per_share, commission_per_trade: s.commission_per_trade, max_position_pct: s.max_position_pct, min_reward_risk: s.min_reward_risk })));
  }, []);
  useEffect(() => {
    const t = setTimeout(() => {
      post("/risk/position-size", { ...f, targets: [f.t1, f.t2].filter(Boolean) }).then(setR).catch(() => null);
    }, 250);
    return () => clearTimeout(t);
  }, [f]);
  const fld = (k: keyof typeof f, label: string, step = "0.01") => (
    <div><label className="label">{label}</label><input className="input" type="number" step={step} value={f[k]} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} /></div>
  );
  return (
    <div className="panel">
      <div className="panel-h">Position-size calculator</div>
      <div className="grid grid-cols-2 gap-2 p-4">
        {fld("equity", "Account equity", "100")}{fld("risk_pct", "Risk % per trade", "0.1")}
        {fld("entry", "Entry")}{fld("stop", "Stop")}
        {fld("t1", "Target 1")}{fld("t2", "Target 2")}
        {fld("slippage_per_share", "Slippage / share")}{fld("commission_per_trade", "Commission / order")}
        {fld("max_position_pct", "Max position % of equity", "1")}{fld("min_reward_risk", "Min R:R", "0.5")}
      </div>
      <div className="border-t border-line p-4 text-sm">
        {!r ? null : !r.valid ? <div className="text-down">{r.errors.join("; ")}</div> : (
          <div className="space-y-1">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <K k="Risk / share (gross)" v={usd(r.risk_per_share, 4)} /><K k="With slippage" v={usd(r.risk_per_share_with_slippage, 4)} />
              <K k="Max shares" v={num(r.shares, 0)} /><K k="Position value" v={`${usd(r.position_value)} (${r.position_pct_of_equity}%)`} />
              <K k="Total planned risk" v={`${usd(r.total_planned_risk)} (${r.planned_risk_pct}%)`} /><K k="Limited by" v={r.limited_by} />
            </div>
            {r.targets.map((t: any) => <div key={t.label} className="text-xs">{t.label} {usd(t.price)}: R:R {t.reward_risk} · net profit {usd(t.net_profit)} ({t.net_reward_risk}R net)</div>)}
            {r.warnings.map((w: string, i: number) => <div key={i} className="text-xs text-warn">! {w}</div>)}
            <div className="text-[11px] text-mute">{r.notes[0]} Never add to a losing position or increase size to recover losses.</div>
          </div>
        )}
      </div>
    </div>
  );
}

function TradeForm({ onSaved }: { onSaved: () => void }) {
  const blank = { mode: "actual", symbol: "", style: "swing", strategy: "", entry_time: new Date().toISOString().slice(0, 16), entry_price: "", quantity: "", stop: "", target1: "", target2: "", exit_price: "", exit_time: "", fees: "0", rationale: "", notes: "" };
  const [f, setF] = useState<any>(blank);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const n = (v: string) => (v === "" ? null : Number(v));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await post("/trades", {
        ...f, entry_time: new Date(f.entry_time).toISOString(), entry_price: Number(f.entry_price), quantity: Number(f.quantity),
        stop: n(f.stop), target1: n(f.target1), target2: n(f.target2), exit_price: n(f.exit_price), fees: Number(f.fees || 0),
        exit_time: f.exit_time ? new Date(f.exit_time).toISOString() : null, strategy: f.strategy || null,
      });
      setF(blank);
      setOpen(false);
      onSaved();
    } catch (x: any) {
      setErr(x.message);
    }
  };
  const inp = (k: string, label: string, type = "text") => (
    <div><label className="label">{label}</label><input className="input" type={type} step="any" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
  );
  return (
    <div className="panel">
      <button className="panel-h w-full" onClick={() => setOpen(!open)}><span>Record a trade</span><span>{open ? "close" : "open"}</span></button>
      {open && (
        <form onSubmit={save} className="grid gap-2 p-4 sm:grid-cols-3 lg:grid-cols-6">
          <div><label className="label">Mode</label><select className="input" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="actual">actual</option><option value="paper">paper</option></select></div>
          {inp("symbol", "Symbol")}
          <div><label className="label">Style</label><select className="input" value={f.style} onChange={(e) => setF({ ...f, style: e.target.value })}><option>swing</option><option>intraday</option></select></div>
          {inp("strategy", "Strategy / setup")}{inp("entry_time", "Entry time", "datetime-local")}{inp("entry_price", "Entry price", "number")}
          {inp("quantity", "Quantity", "number")}{inp("stop", "Stop", "number")}{inp("target1", "Target 1", "number")}
          {inp("target2", "Target 2", "number")}{inp("exit_time", "Exit time", "datetime-local")}{inp("exit_price", "Exit price", "number")}
          {inp("fees", "Fees", "number")}
          <div className="sm:col-span-3"><label className="label">Rationale</label><input className="input" value={f.rationale} onChange={(e) => setF({ ...f, rationale: e.target.value })} /></div>
          <div className="sm:col-span-2"><label className="label">Notes</label><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
          <div className="flex items-end"><button className="btn-primary w-full justify-center">Save</button></div>
          <div className="sm:col-span-6"><ErrorBox error={err} /></div>
        </form>
      )}
    </div>
  );
}

function CloseBtn({ t, onDone }: { t: any; onDone: () => void }) {
  return (
    <button className="text-xs text-info hover:underline" onClick={() => {
      const v = prompt(`Exit price for ${t.symbol}?`);
      if (v && Number(v) > 0) patch(`/trades/${t.id}`, { exit_price: Number(v), exit_reason: "manual" }).then(onDone);
    }}>close…</button>
  );
}

function Upload({ id, onDone }: { id: number; onDone: () => void }) {
  return (
    <label className="block cursor-pointer text-info hover:underline">
      attach image
      <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const fd = new FormData();
        fd.append("file", file);
        await api(`/trades/${id}/screenshot`, { method: "POST", body: fd }).catch((x) => alert(x.message));
        onDone();
      }} />
    </label>
  );
}
