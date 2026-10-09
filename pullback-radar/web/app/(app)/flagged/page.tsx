"use client";
import Link from "next/link";
import { useScanData } from "@/lib/useScan";
import { cap, pct, usd } from "@/lib/format";
import { Empty, ErrorBox, Loading, RiskBadge } from "@/components/ui";

export default function Flagged() {
  const { data, error, loading } = useScanData("/flagged");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Flagged for abnormal activity</h1>
      <p className="max-w-3xl text-sm text-mute">{data.note} Scores describe suspicious patterns (SEC/FINRA pump-and-dump warning signs) — they are not findings or accusations of manipulation. High-risk names are excluded from recommendations by default.</p>
      <div className="panel">
        {data.flagged.length === 0 ? <Empty>No stocks with elevated or high abnormal-activity risk in the latest scan.</Empty> : (
          <ul className="divide-y divide-line">
            {data.flagged.map((f: any) => (
              <li key={f.symbol} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <Link href={`/research/${f.symbol}`} className="font-semibold hover:underline">{f.symbol}</Link>
                  <span className="text-sm text-mute">{f.name}</span>
                  <RiskBadge m={f.manipulation} />
                  <span className="text-xs">{f.manipulation.label}</span>
                  <span className="num text-xs text-mute">{usd(f.price)} · {cap(f.market_cap)} · day {pct(f.metrics?.day_change_pct)}</span>
                </div>
                <ul className="mt-1 grid gap-x-6 text-xs md:grid-cols-2">
                  {f.manipulation.flags.map((x: any, i: number) => <li key={i}>+{x.points} {x.label}: <span className="text-mute">{x.value}</span></li>)}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
