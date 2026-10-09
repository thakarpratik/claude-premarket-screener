"use client";
import { useMemo, useState } from "react";
import { useScanData } from "@/lib/useScan";
import NewsList from "@/components/NewsList";
import { ErrorBox, Loading } from "@/components/ui";

export default function News() {
  const { data, error, loading } = useScanData("/news");
  const [ver, setVer] = useState("all");
  const [sent, setSent] = useState("all");
  const items = useMemo(() => (data?.company_news || []).filter((n: any) => (ver === "all" || n.verification === ver) && (sent === "all" || n.sentiment === sent)), [data, ver, sent]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const social = (data.company_news || []).filter((n: any) => n.social && n.social.mentions_avg && n.social.mentions_today / Math.max(1, n.social.mentions_avg) >= 5);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">News &amp; sentiment</h1>
      <p className="max-w-3xl text-sm text-mute">{data.sources_note} Sentiment comes from the provider when it supplies one, otherwise from documented keyword rules; optional AI interpretations are labelled separately and never change the source facts.</p>
      <div className="flex flex-wrap gap-3">
        <div><label className="label">Verification</label><select className="input" value={ver} onChange={(e) => setVer(e.target.value)}>{["all", "verified", "established", "opinion", "promotional", "unverified"].map((o) => <option key={o}>{o}</option>)}</select></div>
        <div><label className="label">Sentiment</label><select className="input" value={sent} onChange={(e) => setSent(e.target.value)}>{["all", "positive", "neutral", "negative"].map((o) => <option key={o}>{o}</option>)}</select></div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="panel lg:col-span-2"><div className="panel-h">Company news ({items.length})</div><div className="px-4"><NewsList items={items} showSymbol /></div></div>
        <div className="space-y-4">
          <div className="panel"><div className="panel-h">Market news</div><div className="px-4"><NewsList items={data.market_news} /></div></div>
          <div className="panel">
            <div className="panel-h">Unusual social activity</div>
            <div className="p-4 text-sm">
              {!data.social_available ? <span className="text-mute">No licensed social-sentiment source configured.</span> :
                social.length === 0 ? <span className="text-mute">No mention spikes among scanned stocks.</span> :
                  [...new Map(social.map((n: any) => [n.symbol, n])).values()].map((n: any) => (
                    <div key={n.symbol} className="text-warn">{n.symbol}: {n.social.mentions_today} mentions vs ~{Math.round(n.social.mentions_avg)} normal ({n.social.source}) — treat as unverified.</div>
                  ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
