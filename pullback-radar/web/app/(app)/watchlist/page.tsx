"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, del, post } from "@/lib/api";
import { et, usd } from "@/lib/format";
import { Empty, ErrorBox, FreshnessTag, Loading, RiskBadge, StatusBadge } from "@/components/ui";
import { useApp } from "@/components/Shell";

export default function Watchlist() {
  const { scanVersion } = useApp();
  const [items, setItems] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sym, setSym] = useState("");
  const [style, setStyle] = useState("swing");
  const load = () => api("/watchlist").then((r) => setItems(r.items)).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, [scanVersion]);
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await post("/watchlist", { symbol: sym, style });
      setSym("");
      load();
    } catch (x: any) {
      setErr(x.message);
    }
  };
  if (!items) return <Loading />;
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Watchlist</h1>
      <p className="text-sm text-mute">Saved setups are re-checked on every scan. Alerts fire once when a stock enters its entry zone, triggers, breaks its stop, reaches a target, gets material news, shows unusual activity, rises in manipulation risk, or nears earnings. The plan saved when you added the stock is kept for comparison.</p>
      <form onSubmit={add} className="flex flex-wrap items-end gap-2">
        <div><label className="label">Symbol</label><input className="input w-32" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} required /></div>
        <div><label className="label">Style</label><select className="input" value={style} onChange={(e) => setStyle(e.target.value)}><option>swing</option><option>intraday</option></select></div>
        <button className="btn-primary">Add</button>
      </form>
      <ErrorBox error={err} />
      <div className="panel overflow-x-auto">
        {items.length === 0 ? <Empty>Nothing saved yet. Add stocks from a setup card or above.</Empty> : (
          <table className="tbl min-w-[900px]">
            <thead><tr><th>Stock</th><th>Style</th><th>Now</th><th>Price</th><th>Saved plan (zone · trigger · stop · T1)</th><th>Current plan</th><th>Risk</th><th></th></tr></thead>
            <tbody>
              {items.map((w) => {
                const c = w.current;
                const sp = w.plan_snapshot;
                return (
                  <tr key={w.id}>
                    <td><Link className="font-semibold hover:underline" href={`/research/${w.symbol}?style=${w.style}`}>{w.symbol}</Link><div className="text-[11px] text-mute">added {et(w.added_at)}</div></td>
                    <td className="capitalize">{w.style}</td>
                    <td>{c ? <StatusBadge status={c.status} /> : <span className="text-xs text-mute">{w.last_status ?? "not in latest scan"}</span>}
                      {c?.reasons_avoid?.[0] && <div className="max-w-[220px] text-[11px] text-down">{c.reasons_avoid[0]}</div>}</td>
                    <td className="num">{usd(c?.price ?? w.last_price)}<div><FreshnessTag f={c?.freshness} /></div></td>
                    <td className="num text-xs">{sp ? `${usd(sp.entry_zone_low)}–${usd(sp.entry_zone_high)} · ${usd(sp.trigger_price)} · ${usd(sp.stop)} · ${usd(sp.target1)}` : "—"}</td>
                    <td className="num text-xs">{c?.plan ? `${usd(c.plan.entry_zone_low)}–${usd(c.plan.entry_zone_high)} · ${usd(c.plan.trigger_price)} · ${usd(c.plan.stop)} · ${usd(c.plan.target1)}` : "—"}</td>
                    <td><RiskBadge m={c?.manipulation} /></td>
                    <td><button className="text-xs text-down hover:underline" onClick={() => del(`/watchlist/${w.id}`).then(load)}>remove</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
