"use client";
import { useEffect, useRef } from "react";
import type { Plan } from "@/lib/api";

type Bar = { time: string | number; open: number; high: number; low: number; close: number; volume: number; [k: string]: any };

const COLORS = { up: "#3fb68b", down: "#e5534b", grid: "#1b2130", text: "#8a94a7", sma20: "#d4a72c", sma50: "#539bf5", sma200: "#b083f0", vwap: "#d4a72c" };

/** Candles + volume + overlays, with the proposed entry zone, trigger, stop and targets drawn as price lines. */
export default function PriceChart({ bars, overlays, plan, height = 420, levels = [] }: { bars: Bar[]; overlays: string[]; plan: Plan | null; height?: number; levels?: { price: number; label: string; kind: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    let chart: any;
    let ro: ResizeObserver | null = null;
    let disposed = false;
    import("lightweight-charts").then(({ createChart, CrosshairMode, LineStyle }) => {
      if (disposed || !ref.current) return;
      chart = createChart(ref.current, {
        height,
        layout: { background: { color: "transparent" }, textColor: COLORS.text, fontSize: 11 },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "#232a38" },
        timeScale: { borderColor: "#232a38", timeVisible: typeof bars[0].time === "number" },
      });
      const candles = chart.addCandlestickSeries({ upColor: COLORS.up, downColor: COLORS.down, wickUpColor: COLORS.up, wickDownColor: COLORS.down, borderVisible: false });
      candles.setData(bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
      const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(bars.map((b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? "rgba(63,182,139,0.35)" : "rgba(229,83,75,0.35)" })));
      for (const key of overlays) {
        const s = chart.addLineSeries({ color: (COLORS as any)[key] || "#888", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        s.setData(bars.filter((b) => b[key] !== null && b[key] !== undefined).map((b) => ({ time: b.time, value: b[key] })));
      }
      if (plan) {
        const line = (price: number, color: string, title: string, style = LineStyle.Solid) =>
          candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
        line(plan.entry_zone_high, "#539bf5", "zone", LineStyle.Dotted);
        line(plan.entry_zone_low, "#539bf5", "zone", LineStyle.Dotted);
        line(plan.trigger_price, "#d8dee9", "trigger", LineStyle.Dashed);
        line(plan.stop, COLORS.down, "stop");
        line(plan.target1, COLORS.up, "T1");
        if (plan.target2) line(plan.target2, COLORS.up, "T2", LineStyle.Dashed);
      }
      for (const lv of levels.filter((l) => ["breakout", "opening_range", "resistance"].includes(l.kind)).slice(0, 4)) {
        candles.createPriceLine({ price: lv.price, color: "#4b5568", lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: false, title: lv.label });
      }
      chart.timeScale().fitContent();
      ro = new ResizeObserver(() => ref.current && chart.applyOptions({ width: ref.current.clientWidth }));
      ro.observe(ref.current);
    });
    return () => {
      disposed = true;
      ro?.disconnect();
      chart?.remove();
    };
  }, [bars, overlays, plan, height, levels]);
  return <div ref={ref} className="w-full" style={{ height }} />;
}

/** RSI and MACD panes for daily bars. */
export function OscillatorChart({ bars, kind, height = 130 }: { bars: Bar[]; kind: "rsi" | "macd"; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    let chart: any;
    let disposed = false;
    import("lightweight-charts").then(({ createChart, LineStyle }) => {
      if (disposed || !ref.current) return;
      chart = createChart(ref.current, {
        height,
        layout: { background: { color: "transparent" }, textColor: COLORS.text, fontSize: 10 },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderColor: "#232a38" },
        timeScale: { borderColor: "#232a38", visible: false },
      });
      if (kind === "rsi") {
        const s = chart.addLineSeries({ color: "#b083f0", lineWidth: 1, priceLineVisible: false });
        s.setData(bars.filter((b) => b.rsi != null).map((b) => ({ time: b.time, value: b.rsi })));
        s.createPriceLine({ price: 70, color: "#4b5568", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "70" });
        s.createPriceLine({ price: 30, color: "#4b5568", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "30" });
      } else {
        const h = chart.addHistogramSeries({ priceLineVisible: false });
        h.setData(bars.filter((b) => b.macd_hist != null).map((b) => ({ time: b.time, value: b.macd_hist, color: b.macd_hist >= 0 ? "rgba(63,182,139,0.6)" : "rgba(229,83,75,0.6)" })));
        const m = chart.addLineSeries({ color: "#539bf5", lineWidth: 1, priceLineVisible: false });
        m.setData(bars.filter((b) => b.macd != null).map((b) => ({ time: b.time, value: b.macd })));
        const sg = chart.addLineSeries({ color: "#d4a72c", lineWidth: 1, priceLineVisible: false });
        sg.setData(bars.filter((b) => b.macd_signal != null).map((b) => ({ time: b.time, value: b.macd_signal })));
      }
      chart.timeScale().fitContent();
    });
    return () => {
      disposed = true;
      chart?.remove();
    };
  }, [bars, kind, height]);
  return <div ref={ref} className="w-full" style={{ height }} />;
}
