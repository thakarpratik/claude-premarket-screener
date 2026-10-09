"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, post } from "@/lib/api";
import { cls, et } from "@/lib/format";

interface Status {
  mode: string;
  synthetic: boolean;
  ready: boolean;
  session: string;
  setup_messages: string[];
  delay_minutes: number | null;
  disclaimer: string;
  providers: Record<string, any>;
}

interface Ctx {
  status: Status | null;
  scanVersion: number;
  rescan: () => Promise<void>;
  scanning: boolean;
  lastScan: string | null;
  setLastScan: (s: string | null) => void;
}
const AppCtx = createContext<Ctx>({} as Ctx);
export const useApp = () => useContext(AppCtx);

const NAV = [
  ["/", "Market Overview"],
  ["/intraday", "Intraday"],
  ["/swing", "Swing"],
  ["/watchlist", "Watchlist"],
  ["/news", "News & Sentiment"],
  ["/alerts", "Alerts"],
  ["/journal", "Risk & Journal"],
  ["/backtest", "Backtests"],
  ["/flagged", "Flagged (Risk)"],
  ["/settings", "Settings"],
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [status, setStatus] = useState<Status | null>(null);
  const [me, setMe] = useState<{ email: string } | null>(null);
  const [unread, setUnread] = useState(0);
  const [scanVersion, setScanVersion] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    api("/auth/me").then(setMe).catch(() => router.push("/login"));
    api("/status").then(setStatus).catch(() => null);
  }, [router]);

  useEffect(() => {
    const load = () => api("/alerts?unread_only=true").then((r) => setUnread(r.unread)).catch(() => null);
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [scanVersion]);

  const rescan = useCallback(async () => {
    setScanning(true);
    try {
      const r = await post("/scan");
      if (r?.generated_at) setLastScan(r.generated_at);
      setScanVersion((v) => v + 1);
    } finally {
      setScanning(false);
    }
  }, []);

  const logout = async () => {
    await post("/auth/logout").catch(() => null);
    router.push("/login");
  };

  return (
    <AppCtx.Provider value={{ status, scanVersion, rescan, scanning, lastScan, setLastScan }}>
      {status?.synthetic && (
        <div className="bg-info/15 border-b border-info/40 px-4 py-1.5 text-center text-xs text-info">
          DEMO MODE — every price, headline, event and result shown is synthetic. Nothing here reflects the real market.
        </div>
      )}
      {status && !status.ready && (
        <div className="bg-down/15 border-b border-down/40 px-4 py-2 text-sm text-down">
          Scanner offline — setup required: {status.setup_messages.join(" ")}
        </div>
      )}
      <div className="flex min-h-screen">
        <aside className={cls("fixed inset-y-0 left-0 z-30 w-56 border-r border-line bg-panel transition-transform md:static md:translate-x-0", menu ? "translate-x-0" : "-translate-x-full")}>
          <div className="px-4 py-4">
            <div className="text-sm font-semibold tracking-wide">Pullback Radar</div>
            <div className="text-[11px] text-mute">U.S. equities · research tool</div>
          </div>
          <nav className="flex flex-col px-2">
            {NAV.map(([href, label]) => {
              const active = href === "/" ? path === "/" : path.startsWith(href);
              return (
                <Link key={href} href={href} onClick={() => setMenu(false)} className={cls("rounded-md px-3 py-1.5 text-sm", active ? "bg-panel2 text-ink" : "text-mute hover:text-ink")}>
                  {label}
                  {href === "/alerts" && unread > 0 && <span className="ml-2 rounded bg-info/20 px-1.5 text-[11px] text-info">{unread}</span>}
                </Link>
              );
            })}
          </nav>
          <div className="absolute bottom-0 w-full border-t border-line px-4 py-3 text-[11px] text-mute">
            {me?.email}
            <button onClick={logout} className="ml-2 text-info hover:underline">Sign out</button>
            <p className="mt-2 leading-snug">Not financial advice. No orders are ever placed.</p>
          </div>
        </aside>
        <main className="flex-1 min-w-0">
          <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b border-line bg-bg/95 px-4 py-2 backdrop-blur">
            <button className="btn md:hidden" onClick={() => setMenu(!menu)} aria-label="Menu">☰</button>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (q.trim()) router.push(`/research/${q.trim().toUpperCase()}`);
              }}
              className="flex-1 min-w-[140px] max-w-xs"
            >
              <input className="input" placeholder="Research a ticker…" value={q} onChange={(e) => setQ(e.target.value)} />
            </form>
            <div className="flex items-center gap-3 text-xs text-mute">
              <span>
                Session: <span className="text-ink capitalize">{status?.session ?? "—"}</span>
              </span>
              <span>
                Data: <span className={status?.synthetic ? "text-info" : "text-ink"}>{status ? (status.synthetic ? "synthetic" : status.delay_minutes ? `${status.delay_minutes}-min delayed` : "real-time") : "—"}</span>
              </span>
              {lastScan && <span>Scanned {et(lastScan, false)}</span>}
              <button className="btn-primary" onClick={rescan} disabled={scanning || (status ? !status.ready : true)}>
                {scanning ? "Scanning…" : "Scan now"}
              </button>
            </div>
          </header>
          <div className="p-4">{children}</div>
        </main>
      </div>
    </AppCtx.Provider>
  );
}
