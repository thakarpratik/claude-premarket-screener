"use client";
import { useMemo, useState } from "react";
import { useScanData } from "@/lib/useScan";
import type { Card, Style } from "@/lib/api";
import { et } from "@/lib/format";
import { Empty, ErrorBox, Loading, RegimePill } from "./ui";
import { SetupTable } from "./SetupCard";

const INTRO: Record<Style, string> = {
  intraday: "Opening-range retests, VWAP reclaims and orderly VWAP pullbacks in today's session. Levels use 1/5/15-minute structure; positions are planned to close by the end of the day.",
  swing: "Stocks pulling back within healthy daily and weekly uptrends toward meaningful support, for roughly 2–10 session holds.",
};

export default function Opportunities({ style }: { style: Style }) {
  const { data, error, loading } = useScanData(`/opportunities/${style}`);
  const [status, setStatus] = useState("all");
  const [capF, setCapF] = useState("all");
  const [minRR, setMinRR] = useState(0);
  const [showAvoid, setShowAvoid] = useState(false);
  const filt = useMemo(
    () => (cards: Card[]) =>
      cards.filter(
        (c) =>
          (status === "all" || c.status === status) &&
          (capF === "all" || c.cap_category === capF) &&
          ((c.plan?.reward_risk ?? 0) >= minRR || minRR === 0)
      ),
    [status, capF, minRR]
  );
  if (loading && !data) return <Loading label="Scanning…" />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  if (data.disabled) return <Empty>{data.message}</Empty>;
  const ranked = filt(data.ranked);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{style === "swing" ? "Swing opportunities" : "Intraday opportunities"}</h1>
        <RegimePill regime={data.regime?.regime} />
        <span className="text-xs text-mute">Scan {et(data.generated_at)} · session {data.session}</span>
      </div>
      <p className="max-w-3xl text-sm text-mute">{INTRO[style]} {data.regime?.guidance}</p>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <Sel label="Status" v={status} set={setStatus} opts={["all", "Triggered", "Approaching Entry", "Watch"]} />
        <Sel label="Market cap" v={capF} set={setCapF} opts={["all", "large", "mid", "small"]} />
        <div>
          <label className="label">Min R:R</label>
          <input className="input w-24" type="number" step="0.5" min={0} value={minRR} onChange={(e) => setMinRR(Number(e.target.value))} />
        </div>
      </div>
      <div className="panel">
        <div className="panel-h"><span>Ranked setups ({ranked.length})</span><span className="normal-case tracking-normal">click a row for the full card</span></div>
        {ranked.length ? <SetupTable cards={ranked} /> : <Empty>{data.message || "No setups match these filters."}</Empty>}
      </div>
      {data.watch_only.length > 0 && (
        <div className="panel">
          <div className="panel-h">Developing — below the ranking threshold ({data.watch_only.length})</div>
          <SetupTable cards={filt(data.watch_only)} showRank={false} />
        </div>
      )}
      <div className="panel">
        <button className="panel-h w-full" onClick={() => setShowAvoid(!showAvoid)}>
          <span>Avoid / invalidated ({data.avoid.length})</span><span>{showAvoid ? "hide" : "show"}</span>
        </button>
        {showAvoid && (data.avoid.length ? <SetupTable cards={data.avoid} showRank={false} /> : <Empty>None.</Empty>)}
      </div>
      <p className="text-[11px] text-mute">{data.probability_note}</p>
    </div>
  );
}

function Sel({ label, v, set, opts }: { label: string; v: string; set: (s: string) => void; opts: string[] }) {
  return (
    <div>
      <label className="label">{label}</label>
      <select className="input w-44" value={v} onChange={(e) => set(e.target.value)}>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}
