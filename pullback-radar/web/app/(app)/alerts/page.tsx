"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, post } from "@/lib/api";
import { cls, et, usd } from "@/lib/format";
import { Empty, Loading } from "@/components/ui";
import { useApp } from "@/components/Shell";

export default function Alerts() {
  const { scanVersion } = useApp();
  const [data, setData] = useState<any>(null);
  const load = () => api("/alerts").then(setData);
  useEffect(() => {
    load();
  }, [scanVersion]);
  if (!data) return <Loading />;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Alerts</h1>
        <button className="btn" onClick={() => post("/alerts/read-all").then(load)}>Mark all read</button>
        <Link href="/settings#alerts" className="link text-sm">Configure</Link>
      </div>
      <p className="text-sm text-mute">In-app alerts for watchlist stocks. Each condition notifies once when it becomes true, not on every scan. Alerts describe market conditions only — no order is ever placed.</p>
      <div className="panel">
        {data.alerts.length === 0 ? <Empty>No alerts yet. Add stocks to the watchlist to start tracking them.</Empty> : (
          <ul className="divide-y divide-line">
            {data.alerts.map((a: any) => (
              <li key={a.id} className={cls("flex gap-3 px-4 py-3", !a.read && "bg-info/5")}>
                <div className="min-w-0 flex-1">
                  <div className="text-sm"><Link href={a.link} className="font-semibold hover:underline">{a.title}</Link> <span className="text-xs capitalize text-mute">{a.style}</span>{a.synthetic && <span className="ml-2 text-[11px] text-info">synthetic data</span>}</div>
                  <div className="text-xs text-mute">{a.message}</div>
                  <div className="text-[11px] text-mute">Price {usd(a.price)} at {et(a.price_time)} · alert created {et(a.created_at)}</div>
                </div>
                {!a.read && <button className="text-xs text-info hover:underline" onClick={() => post(`/alerts/${a.id}/read`).then(load)}>mark read</button>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
