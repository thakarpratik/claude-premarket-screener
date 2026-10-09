"use client";
import type { NewsItem } from "@/lib/api";
import { cls, et } from "@/lib/format";
import { SentimentDot } from "./ui";

const VER: Record<string, string> = {
  verified: "text-up border-up/30",
  established: "text-info border-info/30",
  opinion: "text-warn border-warn/30",
  promotional: "text-down border-down/30",
  unverified: "text-mute border-line",
};

export default function NewsList({ items, showSymbol = false }: { items: NewsItem[]; showSymbol?: boolean }) {
  if (!items.length) return <div className="text-xs text-mute">No news in the lookback window from configured sources.</div>;
  return (
    <ul className="divide-y divide-line/60">
      {items.map((n) => (
        <li key={n.id} className="py-2 text-sm">
          <div className="flex items-start gap-2">
            <SentimentDot s={n.sentiment} />
            <div className="min-w-0 flex-1">
              <a href={n.url} target="_blank" rel="noopener noreferrer nofollow" className="link">
                {showSymbol && n.symbol ? <span className="mr-1 font-semibold text-ink">{n.symbol}</span> : null}
                {n.headline}
              </a>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-mute">
                <span>{n.publisher}</span>
                <span>{et(n.published_at)}</span>
                <span className={cls("rounded border px-1", VER[n.verification])}>{n.verification_label}</span>
                <span className="capitalize">{n.sentiment} · {n.impact} impact</span>
                <span>({n.sentiment_method})</span>
                <span>{n.category.replace(/_/g, " ")}</span>
              </div>
              <div className="mt-0.5 text-xs text-mute">Why it matters: {n.why_it_matters}</div>
              {n.priced_in && n.priced_in.status !== "not_measurable" && <div className="text-xs text-mute">Priced in? {n.priced_in.note}</div>}
              {n.ai && (
                <div className="mt-0.5 text-xs text-info/90">
                  AI interpretation ({n.ai.model}): {n.ai.sentiment}, {n.ai.impact} impact — {n.ai.why_it_matters}
                  {n.ai.opinion_or_promotional ? " (flagged as opinion/promotional)" : ""}
                </div>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
