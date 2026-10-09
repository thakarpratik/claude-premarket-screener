"use client";
import { cls } from "@/lib/format";
import type { Freshness as F, Manipulation } from "@/lib/api";

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    Triggered: "bg-up/15 text-up border-up/40",
    "Approaching Entry": "bg-info/15 text-info border-info/40",
    Watch: "bg-panel2 text-ink border-line",
    Invalidated: "bg-down/15 text-down border-down/40",
    Avoid: "bg-down/10 text-down/90 border-down/30",
  };
  return <span className={cls("inline-block rounded border px-2 py-0.5 text-xs whitespace-nowrap", map[status] || map.Watch)}>{status}</span>;
}

export function RegimePill({ regime }: { regime?: string }) {
  const c = regime === "bullish" ? "text-up border-up/40 bg-up/10" : regime === "bearish" ? "text-down border-down/40 bg-down/10" : "text-warn border-warn/40 bg-warn/10";
  return <span className={cls("rounded-full border px-2.5 py-0.5 text-xs capitalize", c)}>{regime || "unknown"} market</span>;
}

export function RiskBadge({ m }: { m?: Manipulation }) {
  if (!m || m.score === null || m.score === undefined) return <span className="text-xs text-mute">risk n/a</span>;
  const c = m.level === "high" ? "text-down border-down/40 bg-down/10" : m.level === "elevated" ? "text-warn border-warn/40 bg-warn/10" : "text-up/90 border-up/30 bg-up/5";
  return (
    <span title={m.label} className={cls("rounded border px-1.5 py-0.5 text-xs num whitespace-nowrap", c)}>
      P&amp;D {m.score}
    </span>
  );
}

export function FreshnessTag({ f }: { f?: F }) {
  if (!f) return null;
  const c =
    f.status === "real-time" ? "text-up" : f.status === "delayed" || f.status === "end-of-day" ? "text-warn" : f.status === "synthetic" ? "text-info" : "text-down";
  return (
    <span title={f.note} className={cls("text-xs", c)}>
      ● {f.status}
      {f.delay_minutes > 0 && f.status === "delayed" ? ` (${f.delay_minutes}m)` : ""}
    </span>
  );
}

export function ScoreBar({ value, max = 100 }: { value: number; max?: number }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  const c = value >= 70 ? "bg-up" : value >= 50 ? "bg-info" : value >= 35 ? "bg-warn" : "bg-down";
  return (
    <div className="h-1.5 w-full rounded bg-line">
      <div className={cls("h-1.5 rounded", c)} style={{ width: `${w}%` }} />
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-10 text-center text-mute text-sm">{children}</div>;
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-md border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{error}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="px-4 py-10 text-center text-mute text-sm animate-pulse">{label}</div>;
}

export function SentimentDot({ s }: { s: string }) {
  const c = s === "positive" ? "bg-up" : s === "negative" ? "bg-down" : "bg-mute";
  return <span className={cls("inline-block h-2 w-2 rounded-full", c)} title={s} />;
}
