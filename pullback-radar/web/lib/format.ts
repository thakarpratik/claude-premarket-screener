export const usd = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;

export const pct = (v: number | null | undefined, d = 1, sign = true) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${sign && v > 0 ? "+" : ""}${v.toFixed(d)}%`;

export const num = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: d });

export function cap(v: number | null | undefined) {
  if (!v) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  return `$${(v / 1e6).toFixed(0)}M`;
}

export function et(iso: string | null | undefined, withDate = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  return (
    d.toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: withDate ? "short" : undefined,
      day: withDate ? "numeric" : undefined,
      hour: "numeric",
      minute: "2-digit",
    }) + " ET"
  );
}

export const cls = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");
