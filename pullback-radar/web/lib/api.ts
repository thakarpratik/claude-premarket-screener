// Thin client for the FastAPI backend (proxied at /api by next.config.ts).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "pullback-radar", ...(init.headers as any) };
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth")) {
    window.location.href = "/login";
    throw new ApiError(401, "Not signed in");
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const d = data?.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : JSON.stringify(d ?? res.statusText));
  }
  return data as T;
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T = any>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T = any>(path: string) => api<T>(path, { method: "DELETE" });

export type Style = "intraday" | "swing";

export interface Plan {
  entry_zone_low: number;
  entry_zone_high: number;
  trigger_price: number;
  trigger_text: string;
  entry_price: number;
  stop: number;
  stop_text: string;
  target1: number;
  target1_text: string;
  target2: number | null;
  target2_text: string | null;
  conditional_text: string;
  risk_per_share: number;
  reward_risk: number | null;
  reward_risk_t2: number | null;
  gain_pct_t1: number;
  gain_pct_t2: number | null;
  loss_pct: number;
}

export interface Signal {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
  group: string;
  weight: number;
}

export interface NewsItem {
  id: string;
  symbol?: string;
  headline: string;
  publisher: string;
  url: string;
  published_at: string;
  source: string;
  summary?: string | null;
  category: string;
  impact: "low" | "medium" | "high";
  sentiment: "positive" | "neutral" | "negative";
  sentiment_method: string;
  verification: string;
  verification_label: string;
  why_it_matters: string;
  priced_in: { status: string; note: string };
  ai?: { sentiment: string; impact: string; why_it_matters: string; opinion_or_promotional: boolean; model: string };
}

export interface Manipulation {
  score: number | null;
  level: "low" | "elevated" | "high" | "unknown";
  label: string;
  flags: { points: number; key: string; label: string; value: string }[];
  unknown: string[];
  disclaimer?: string;
}

export interface Freshness {
  status: string;
  as_of: string | null;
  age_minutes: number | null;
  delay_minutes: number;
  session: string;
  note: string;
}

export interface Card {
  symbol: string;
  name: string;
  exchange: string;
  sector: string | null;
  market_cap: number | null;
  cap_category: string | null;
  style: Style;
  status: string;
  setup_type: string | null;
  technical_state: string;
  qualifies: boolean;
  is_setup: boolean;
  rank?: number;
  price: number;
  price_time: string;
  freshness: Freshness;
  bar_as_of: string | null;
  session_note: string | null;
  plan: Plan | null;
  levels?: { price: number; label: string; kind: string }[];
  signals: Signal[];
  metrics: Record<string, any>;
  score: {
    score: number;
    base_score: number;
    components: Record<string, number>;
    weights: Record<string, number>;
    penalties: { label: string; points: number }[];
    exclusions: string[];
    threshold: number;
    gate_reason: string | null;
  };
  explanation: {
    why_qualifies: string[];
    entry_attractive: string[];
    confirmation: string[];
    failure_risks: string[];
    news_event_risks: string[];
    ranking_note: string | null;
  };
  manipulation: Manipulation;
  news: NewsItem[];
  events: { upcoming: any[]; blocking: string[]; notes: string[]; next_earnings: any | null };
  event_driven_decline: any | null;
  position_size: any | null;
  warnings: string[];
  reasons_avoid: string[];
  social: any | null;
  sources: Record<string, any>;
}
