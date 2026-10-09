"use client";
import { useEffect, useState } from "react";
import { api, del, patch, post, put } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";
import { useApp } from "@/components/Shell";

type S = Record<string, any>;

const NUM: [string, string, string, number?][] = [
  ["small_cap_min", "Small-cap minimum ($)", "Market cap"],
  ["mid_cap_min", "Mid-cap minimum ($)", "Market cap"],
  ["large_cap_min", "Large-cap minimum ($)", "Market cap"],
  ["min_price", "Minimum price ($)", "Price & liquidity"],
  ["max_price", "Maximum price ($, blank = none)", "Price & liquidity"],
  ["min_dollar_volume_intraday", "Min avg $ volume — intraday", "Price & liquidity"],
  ["min_dollar_volume_swing", "Min avg $ volume — swing", "Price & liquidity"],
  ["max_spread_pct", "Max bid-ask spread %", "Price & liquidity"],
  ["max_atr_pct", "Max volatility (ATR %, blank = none)", "Price & liquidity"],
  ["swing_hold_days_min", "Swing hold — min days", "Strategy"],
  ["swing_hold_days_max", "Swing hold — max days", "Strategy"],
  ["min_setup_score", "Minimum setup score", "Strategy"],
  ["bearish_regime_score_add", "Extra score needed in a bearish market", "Strategy"],
  ["account_equity", "Account equity ($)", "Risk"],
  ["risk_pct_per_trade", "Risk per trade (% of equity)", "Risk"],
  ["max_position_pct", "Max position (% of equity)", "Risk"],
  ["min_reward_risk", "Minimum reward : risk", "Risk"],
  ["slippage_per_share", "Slippage allowance ($/share)", "Risk"],
  ["commission_per_trade", "Commission per order ($)", "Risk"],
  ["daily_loss_limit_pct", "Daily loss limit (% of equity)", "Risk"],
  ["earnings_blackout_days_swing", "Earnings blackout — swing (trading days)", "Event risk"],
  ["earnings_blackout_days_intraday", "Earnings blackout — intraday (trading days)", "Event risk"],
];

export default function Settings() {
  const { rescan } = useApp();
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [rules, setRules] = useState<any>(null);
  const [custom, setCustom] = useState({ symbol: "", dir: "above", level: "" });
  const loadRules = () => api("/alert-rules").then(setRules);
  useEffect(() => {
    api("/settings").then((r) => setS(r.settings));
    loadRules();
  }, []);
  if (!s) return <Loading />;
  const save = async () => {
    setErr(null);
    setOk(null);
    try {
      const r = await put("/settings", s);
      setS(r.settings);
      setOk("Saved. Re-scanning with the new settings…");
      rescan();
    } catch (x: any) {
      setErr(x.message);
    }
  };
  const groups = [...new Set(NUM.map((n) => n[2]))];
  const toggle = (k: string, v: string) => setS({ ...s, [k]: s[k].includes(v) ? s[k].filter((x: string) => x !== v) : [...s[k], v] });
  return (
    <div className="max-w-5xl space-y-4">
      <h1 className="text-lg font-semibold">Settings</h1>
      <div className="grid gap-4 md:grid-cols-2">
        {groups.map((g) => (
          <div key={g} className="panel">
            <div className="panel-h">{g}</div>
            <div className="grid grid-cols-2 gap-2 p-4">
              {NUM.filter((n) => n[2] === g).map(([k, label]) => (
                <div key={k}>
                  <label className="label">{label}</label>
                  <input className="input" type="number" step="any" value={s[k] ?? ""} onChange={(e) => setS({ ...s, [k]: e.target.value === "" ? null : Number(e.target.value) })} />
                </div>
              ))}
              {g === "Market cap" && (
                <div className="col-span-2 flex gap-3 text-sm">{["small", "mid", "large"].map((c) => <label key={c} className="flex items-center gap-1"><input type="checkbox" checked={s.cap_categories.includes(c)} onChange={() => toggle("cap_categories", c)} /> {c}-cap</label>)}</div>
              )}
              {g === "Strategy" && (<>
                <div className="col-span-2 flex gap-3 text-sm">{["intraday", "swing"].map((c) => <label key={c} className="flex items-center gap-1"><input type="checkbox" checked={s.trading_styles.includes(c)} onChange={() => toggle("trading_styles", c)} /> {c}</label>)}</div>
                <div><label className="label">Opening range (minutes)</label><select className="input" value={s.opening_range_minutes} onChange={(e) => setS({ ...s, opening_range_minutes: Number(e.target.value) })}>{[5, 15, 30].map((m) => <option key={m}>{m}</option>)}</select></div>
                <div><label className="label">Sectors (comma list, blank = all)</label><input className="input" value={s.sectors.join(", ")} onChange={(e) => setS({ ...s, sectors: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></div>
              </>)}
              {g === "Risk" && (
                <div><label className="label">Below minimum R:R</label><select className="input" value={s.reward_risk_rule} onChange={(e) => setS({ ...s, reward_risk_rule: e.target.value })}><option value="warn">warn and penalise</option><option value="exclude">exclude</option></select></div>
              )}
              {g === "Event risk" && (
                <div className="col-span-2 space-y-1 text-sm">
                  <label className="flex items-center gap-2"><input type="checkbox" checked={s.exclude_before_earnings} onChange={(e) => setS({ ...s, exclude_before_earnings: e.target.checked })} /> Exclude setups inside the earnings blackout</label>
                  <label className="flex items-center gap-2"><input type="checkbox" checked={s.exclude_before_major_macro} onChange={(e) => setS({ ...s, exclude_before_major_macro: e.target.checked })} /> Exclude around major macro events (FOMC, CPI…)</label>
                  <label className="flex items-center gap-2"><input type="checkbox" checked={s.exclude_high_manipulation_risk} onChange={(e) => setS({ ...s, exclude_high_manipulation_risk: e.target.checked })} /> Exclude high manipulation-risk stocks (recommended)</label>
                </div>
              )}
            </div>
          </div>
        ))}
        <div className="panel">
          <div className="panel-h">Ranking weights (%)</div>
          <div className="grid grid-cols-2 gap-2 p-4">
            {Object.entries(s.weights).map(([k, v]) => (
              <div key={k}><label className="label">{k.replace(/_/g, " ")}</label><input className="input" type="number" step="1" value={v as number} onChange={(e) => setS({ ...s, weights: { ...s.weights, [k]: Number(e.target.value) } })} /></div>
            ))}
            <p className="col-span-2 text-[11px] text-mute">Weights are normalised to 100%. The defaults are a starting point, not proven optimal — validate changes with the backtester.</p>
          </div>
        </div>
        <div className="panel">
          <div className="panel-h">Notifications</div>
          <div className="space-y-1 p-4 text-sm">
            <label className="flex items-center gap-2"><input type="checkbox" checked={s.notify_in_app} onChange={(e) => setS({ ...s, notify_in_app: e.target.checked })} /> In-app alerts</label>
            <label className="flex items-center gap-2 text-mute"><input type="checkbox" disabled checked={false} readOnly /> Email / push (not configured on this server)</label>
          </div>
        </div>
      </div>
      <ErrorBox error={err} />
      {ok && <div className="text-sm text-up">{ok}</div>}
      <button className="btn-primary" onClick={save}>Save settings</button>

      <div id="alerts" className="panel">
        <div className="panel-h">Alert types (apply to every watchlist stock)</div>
        {!rules ? <Loading /> : (
          <div className="space-y-3 p-4">
            <div className="grid gap-1 sm:grid-cols-2">
              {rules.rules.filter((r: any) => !r.symbol).map((r: any) => (
                <label key={r.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={r.enabled} onChange={(e) => patch(`/alert-rules/${r.id}`, { enabled: e.target.checked }).then(loadRules)} />
                  {rules.kinds[r.kind]}
                </label>
              ))}
            </div>
            <div className="border-t border-line pt-3">
              <div className="mb-1 text-xs uppercase tracking-wider text-mute">Custom price alerts</div>
              {rules.rules.filter((r: any) => r.symbol).map((r: any) => (
                <div key={r.id} className="text-sm">{r.symbol} {r.params.above !== undefined ? `≥ ${r.params.above}` : `≤ ${r.params.below}`} <button className="ml-2 text-xs text-down hover:underline" onClick={() => del(`/alert-rules/${r.id}`).then(loadRules)}>remove</button></div>
              ))}
              <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); post("/alert-rules", { kind: "price_cross", symbol: custom.symbol, params: { [custom.dir]: Number(custom.level) } }).then(() => { setCustom({ symbol: "", dir: "above", level: "" }); loadRules(); }).catch((x) => setErr(x.message)); }}>
                <div><label className="label">Symbol (on watchlist)</label><input className="input w-28" value={custom.symbol} onChange={(e) => setCustom({ ...custom, symbol: e.target.value.toUpperCase() })} required /></div>
                <div><label className="label">Direction</label><select className="input" value={custom.dir} onChange={(e) => setCustom({ ...custom, dir: e.target.value })}><option value="above">crosses above</option><option value="below">crosses below</option></select></div>
                <div><label className="label">Level</label><input className="input w-28" type="number" step="any" value={custom.level} onChange={(e) => setCustom({ ...custom, level: e.target.value })} required /></div>
                <button className="btn">Add</button>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
