"use client";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, post } from "@/lib/api";
import type { Card, Style } from "@/lib/api";
import { cap, cls, et } from "@/lib/format";
import { ErrorBox, Loading, RegimePill, StatusBadge } from "@/components/ui";
import PriceChart, { OscillatorChart } from "@/components/PriceChart";
import { SetupDetail } from "@/components/SetupCard";
import { useApp } from "@/components/Shell";

const DAILY_OVERLAYS = ["sma20", "sma50", "sma200"];
const INTRA_OVERLAYS = ["vwap"];

export default function Research() {
  const { symbol } = useParams<{ symbol: string }>();
  const sp = useSearchParams();
  const { scanVersion } = useApp();
  const [style, setStyle] = useState<Style>((sp.get("style") as Style) || "swing");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    api(`/stocks/${encodeURIComponent(symbol)}`).then(setData).catch((e) => setErr(e.message));
  }, [symbol, scanVersion]);
  const card: Card | undefined = data?.cards?.[style];
  const bars = useMemo(() => (style === "swing" ? data?.chart?.daily : data?.chart?.intraday) || [], [data, style]);
  if (err) return <ErrorBox error={err} />;
  if (!data) return <Loading label={`Analysing ${symbol}…`} />;
  const info = data.info;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{info.symbol}</h1>
        <span className="text-mute">{info.name}</span>
        <span className="text-xs text-mute">{info.exchange} · {info.security_type} · {info.sector ?? "sector n/a"} · {cap(info.market_cap)}</span>
        {card && <StatusBadge status={card.status} />}
        <RegimePill regime={data.regime?.regime} />
        <div className="ml-auto flex gap-1">
          {(["swing", "intraday"] as Style[]).map((s) => (
            <button key={s} className={cls("btn capitalize", style === s && "border-info text-info")} onClick={() => setStyle(s)}>{s}</button>
          ))}
          <button className="btn" onClick={() => post("/watchlist", { symbol: info.symbol, style }).then(() => setMsg("Added to watchlist.")).catch((e) => setMsg(e.message))}>
            {data.watchlist?.includes(style) ? "✓ Watching" : "+ Watchlist"}
          </button>
        </div>
      </div>
      {msg && <div className="text-xs text-info">{msg}</div>}
      {data.universe_reasons?.length > 0 && (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          Outside the scan universe: {data.universe_reasons.join("; ")}. Shown for research only.
        </div>
      )}
      <div className="panel">
        <div className="panel-h">
          <span>{style === "swing" ? "Daily · SMA 20 / 50 / 200" : `5-minute · VWAP · session ${data.chart?.intraday_session ?? ""}`}</span>
          <span className="normal-case tracking-normal">
            {style === "swing" && data.chart?.daily_partial_last_bar ? "Last bar is still forming · " : ""}
            Proposed levels: dotted = entry zone, dashed = trigger, red = stop, green = targets. Times ET.
          </span>
        </div>
        <div className="p-2">
          {bars.length ? <PriceChart bars={bars} overlays={style === "swing" ? DAILY_OVERLAYS : INTRA_OVERLAYS} plan={card?.plan ?? null} levels={card?.levels} /> : <div className="p-6 text-sm text-mute">No {style} bars available for this session.</div>}
        </div>
        {style === "swing" && bars.length > 0 && (
          <div className="grid gap-2 border-t border-line p-2 md:grid-cols-2">
            <div><div className="px-2 text-[11px] text-mute">RSI (14)</div><OscillatorChart bars={bars} kind="rsi" /></div>
            <div><div className="px-2 text-[11px] text-mute">MACD (12, 26, 9)</div><OscillatorChart bars={bars} kind="macd" /></div>
          </div>
        )}
      </div>
      {card ? (
        <div className="panel">
          <div className="panel-h"><span>{style} analysis · bar as of {et(card.bar_as_of)}</span></div>
          <SetupDetail card={card} />
          <div className="border-t border-line p-4">
            <div className="mb-2 text-xs uppercase tracking-wider text-mute">All signals</div>
            <div className="grid gap-1 text-xs md:grid-cols-2">
              {card.signals.map((s, i) => (
                <div key={i} className={cls(s.group === "breakdown" ? "text-down" : s.passed ? "text-up" : "text-mute")}>
                  {s.group === "breakdown" ? "▼" : s.passed ? "✓" : "·"} {s.label} — <span className="text-mute">{s.detail}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="panel p-4 text-sm text-mute">{style} analysis unavailable — check data errors below.</div>
      )}
      {data.errors?.length > 0 && <div className="text-xs text-warn">Data issues: {data.errors.map((e: any) => `${e.stage}: ${e.error}`).join(" · ")}</div>}
    </div>
  );
}
